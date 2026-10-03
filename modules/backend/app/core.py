"""服务编排层：HTTP API 与数据集回放共用同一条代码路径。

职责（对应计划书「后端与确定性计算」模块边界）：
- 接收存储、身份绑定、事件去重、跨站点关联、异常标记、换菜开启新批次
- 状态汇总、规则补菜、任务合并与闭环
- 视觉校验为增量：模型不可用时全部回退纯规则
"""
from __future__ import annotations

import base64
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from . import analytics, config, db, refill, vision
from .interpreter import ServingState, interpret, same_pass
from .jev import JevClient
from .schemas import (CoversIn, DishConfigIn, OperationIn, StationEventIn,
                      TaskUpdateIn)


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.now().astimezone().tzinfo)


class HotpotService:
    def __init__(self) -> None:
        self.jev = JevClient()
        vision.set_client(self.jev)
        self.states: dict[str, ServingState] = {}   # serving_id -> state
        self._anomaly_counts: dict[str, int] = {}   # dish_id -> 未决异常盘数
        self._replay_lock = threading.Lock()
        db.init()
        self._seed_dishes()
        self._rebuild()

    # ------------------------------------------------ 启动恢复

    def _seed_dishes(self) -> None:
        if db.get_dishes():
            return
        catalog_path = os.path.join(config.DATASET_DIR, "dish_catalog.json")
        if os.path.exists(catalog_path):
            import json
            with open(catalog_path) as f:
                for d in json.load(f):
                    db.upsert_dish({
                        "dish_id": d["dish_id"], "name": d["name"],
                        "countable": d["dish_id"] == "D01",
                        "unit_name": "个" if d["dish_id"] == "D01" else None,
                        "unit_mass_g": 25.0 if d["dish_id"] == "D01" else None,
                        "std_portion_g": 200.0 if d["dish_id"] == "D01" else 300.0,
                        "prep_time_min": 3.0, "batch_size": None,
                        "freshness_min": None, "cost_per_10g": 0.05, "note": d.get("challenge"),
                    })

    def _rebuild(self) -> None:
        """从 DB 恢复内存状态（幂等：servings 表为权威，事件只重建滚动字段）。"""
        self.states.clear()
        self._anomaly_counts.clear()
        for s in db.get_servings(status="open"):
            self.states[s["serving_id"]] = ServingState(
                serving_id=s["serving_id"], plate_id=s["plate_id"],
                dish_id=s["dish_id"], trusted_net_g=s.get("trusted_net_g"),
                baseline_g=s.get("baseline_g"), tare_g=s.get("tare_g"),
                simulated=bool(s.get("simulated")))
        for e in db.get_events(limit=100000):
            st = self.states.get(e.get("serving_id"))
            if st is None:
                continue
            st.evidence_ids.append(e["event_id"])
            interp = e.get("interpretation") or {}
            cls = interp.get("classification")
            if cls == "merged_pass" or e.get("net_weight_g") is None:
                continue
            ts = _parse(e["observed_at"])
            st.last_obs_at = max(st.last_obs_at or ts, ts)
            st.last_station = e["station_id"]
            if cls == "removal":
                st.consumed_g += interp.get("taken_g") or 0.0
                st.last_removal_at = ts
            elif cls in ("refill_confirmed", "late_refill"):
                st.refilled_g += abs(interp.get("delta_g") or 0.0)
            elif cls == "unexplained_increase":
                st.anomaly = {"observed_at": ts.isoformat(),
                              "spike_g": interp.get("delta_g")}
                self._anomaly_counts[st.dish_id] = self._anomaly_counts.get(st.dish_id, 0) + 1
            elif cls == "anomaly_resolved" and st.anomaly is not None:
                st.anomaly = None
                self._anomaly_counts[st.dish_id] = max(0, self._anomaly_counts.get(st.dish_id, 0) - 1)

    def _open_serving(self, plate_id: str, dish_id: str, ts: str,
                      baseline_g: Optional[float], tare_g: Optional[float],
                      simulated: bool) -> ServingState:
        existing = db.get_servings(plate_id=plate_id, status="open")
        for s in existing:
            self._close_serving(plate_id, ts, "换菜自动关闭")
        n = len(db.get_servings(plate_id=plate_id)) + 1
        serving_id = f"{plate_id}_s{n:02d}"
        st = ServingState(serving_id=serving_id, plate_id=plate_id, dish_id=dish_id,
                          tare_g=tare_g, simulated=simulated)
        self.states[serving_id] = st
        self._persist_serving(st)
        return st

    def _close_serving(self, plate_id: str, ts: str, reason: str) -> Optional[ServingState]:
        for s in db.get_servings(plate_id=plate_id, status="open"):
            st = self.states.get(s["serving_id"])
            db.insert_serving({**s, "status": "closed", "closed_at": ts, "close_reason": reason})
            if st:
                st.anomaly = None
            self.states.pop(s["serving_id"], None)
            return st
        return None

    def _state_for_event(self, e: dict) -> Optional[ServingState]:
        if e.get("serving_id"):
            return self.states.get(e["serving_id"])
        opens = db.get_servings(plate_id=e["plate_id"], status="open")
        if opens:
            return self.states.get(opens[-1]["serving_id"])
        return None

    def _persist_serving(self, st: ServingState) -> None:
        row = db.query_one("SELECT * FROM servings WHERE serving_id=?", (st.serving_id,)) or {}
        opened_at = row.get("opened_at")
        if opened_at is None and st.last_obs_at is not None:
            opened_at = st.last_obs_at.isoformat()
        db.insert_serving({
            "serving_id": st.serving_id, "plate_id": st.plate_id, "dish_id": st.dish_id,
            "status": row.get("status") or "open",
            "opened_at": opened_at,
            "closed_at": row.get("closed_at"), "close_reason": row.get("close_reason"),
            "tare_g": st.tare_g, "baseline_g": st.baseline_g,
            "trusted_net_g": st.trusted_net_g, "simulated": st.simulated,
        })

    # ------------------------------------------------ 站点事件（契约 1）

    def ingest_station_event(self, ev: StationEventIn, image_bytes: Optional[bytes] = None) -> dict:
        ts = ev.observed_at.astimezone()
        net = ev.net_weight_g
        if net is None and ev.gross_weight_g is not None:
            tare = ev.tare_g if ev.tare_g is not None else 180.0
            net = ev.gross_weight_g - tare
        event_id = ev.event_id or uuid.uuid4().hex[:12]

        # 幂等：同 event_id 重复上报直接忽略（不重复扣量/计数）
        if ev.event_id and db.query_one("SELECT event_id FROM station_events WHERE event_id=?",
                                        (ev.event_id,)):
            return {"event_id": event_id, "duplicate": True,
                    "note": "重复上报已忽略"}

        st = self._state_for_event({"plate_id": ev.plate_id, "serving_id": None})
        if st is None:
            return {"error": f"盘 {ev.plate_id} 无开放绑定；请先 POST /api/bindings 上盘绑定",
                    "event_id": event_id}

        # 乱序隔离：迟到旧事件留痕但不参与解释与任务触发
        # （完成标准：任务完成后不会因旧事件再次触发；验收场景：乱序不直接计为取用）
        if st.last_obs_at is not None and ts < st.last_obs_at:
            db.insert_event({
                "event_id": event_id, "serving_id": st.serving_id, "plate_id": ev.plate_id,
                "station_id": ev.station_id, "observed_at": ts.isoformat(),
                "lap_index": ev.lap_index, "net_weight_g": net,
                "gross_weight_g": ev.gross_weight_g, "tare_g": ev.tare_g,
                "item_count": ev.item_count, "dish_id_claim": ev.dish_id,
                "image_ref": ev.image_ref, "visual_level": ev.visual_level,
                "quality": ev.quality, "source": ev.source, "simulated": ev.simulated,
                "interpretation": {"classification": "out_of_order",
                                   "note": f"事件时间早于已处理观测 {st.last_obs_at.isoformat()}；"
                                           "留痕待复核，不更新状态、不触发任务"},
                "created_at": db.now_iso(),
            })
            return {"event_id": event_id, "classification": "out_of_order",
                    "note": "迟到旧事件已隔离：留痕待复核，不更新状态、不触发任务"}

        # 去重/合并：同盘同站时间窗内且重量无真实变化 => 同一次经过，留痕但状态不变
        if (net is not None and same_pass(st.last_obs_at, st.last_station, ts, ev.station_id)
                and st.trusted_net_g is not None
                and abs(net - st.trusted_net_g) <= config.NOISE_BOUND_G):
            db.insert_event({
                "event_id": event_id, "serving_id": st.serving_id, "plate_id": ev.plate_id,
                "station_id": ev.station_id, "observed_at": ts.isoformat(),
                "lap_index": ev.lap_index, "net_weight_g": net,
                "gross_weight_g": ev.gross_weight_g, "tare_g": ev.tare_g,
                "item_count": ev.item_count, "dish_id_claim": ev.dish_id,
                "image_ref": ev.image_ref, "visual_level": ev.visual_level,
                "quality": ev.quality, "source": ev.source, "simulated": ev.simulated,
                "interpretation": {"classification": "merged_pass",
                                   "note": "同一次经过的重复帧已合并，不重复计数"},
                "created_at": db.now_iso(),
            })
            return {"event_id": event_id, "merged": True,
                    "classification": "merged_pass",
                    "note": "同一次经过的重复帧已合并，不重复计数"}

        # 视觉绑定校验（可选增量；离线自动跳过）
        binding_check = None
        if image_bytes is not None and ev.dish_id is not None:
            binding_check = vision.check_binding(
                base64.b64encode(image_bytes).decode(), st.dish_id)

        refill_ops = [o for o in db.get_ops(plate_id=ev.plate_id,
                                            after=st.last_obs_at.isoformat() if st.last_obs_at else None,
                                            before=ts.isoformat())
                      if o["op_type"] == "refill"]

        image_ref = ev.image_ref
        if image_bytes is not None:
            os.makedirs(config.IMAGE_DIR, exist_ok=True)
            image_ref = os.path.join(config.IMAGE_DIR, f"{event_id}.png")
            with open(image_ref, "wb") as f:
                f.write(image_bytes)

        classification, detail = (interpret(st, net, ts, ev.station_id, refill_ops, event_id)
                                  if net is not None else ("no_weight", {"note": "本次无称重，仅记录"}))
        interpretation = {"classification": classification, **detail}
        if binding_check is not None:
            interpretation["binding_check"] = binding_check

        db.insert_event({
            "event_id": event_id, "serving_id": st.serving_id, "plate_id": ev.plate_id,
            "station_id": ev.station_id, "observed_at": ts.isoformat(),
            "lap_index": ev.lap_index, "net_weight_g": net,
            "gross_weight_g": ev.gross_weight_g, "tare_g": ev.tare_g,
            "item_count": ev.item_count, "dish_id_claim": ev.dish_id,
            "image_ref": image_ref, "visual_level": ev.visual_level,
            "quality": ev.quality, "source": ev.source, "simulated": ev.simulated,
            "interpretation": interpretation, "created_at": db.now_iso(),
        })
        st.evidence_ids.append(event_id)

        # 异常 -> 暂停该菜品自动派单（PRD）；恢复 -> 解除
        if classification == "unexplained_increase":
            self._suspend_dish(st.dish_id, f"盘{st.plate_id} 未解释突增 {detail.get('delta_g')}g")
        elif classification in ("anomaly_resolved", "refill_confirmed", "late_refill"):
            self._anomaly_resolved(st)

        self._persist_serving(st)

        if classification != "unexplained_increase":
            created, updated = refill.commit_tasks(refill.evaluate_serving(st, ts))
        else:
            created, updated = [], []

        return {
            "event_id": event_id,
            "interpretation": interpretation,
            "snapshot": self.serving_snapshot(st),
            "new_tasks": {"created": created, "updated": updated},
        }

    def _suspend_dish(self, dish_id: str, why: str) -> None:
        self._anomaly_counts[dish_id] = self._anomaly_counts.get(dish_id, 0) + 1
        if self._anomaly_counts[dish_id] >= 1:
            db.set_dish_flag(dish_id, "dispatch_suspended", True)
            db.set_meta(f"suspend_reason:{dish_id}", why)

    def _anomaly_resolved(self, st: ServingState) -> None:
        cnt = self._anomaly_counts.get(st.dish_id, 0) - 1
        self._anomaly_counts[st.dish_id] = max(0, cnt)
        if cnt <= 0:
            db.set_dish_flag(st.dish_id, "dispatch_suspended", False)

    # ------------------------------------------------ 操作记录

    def ingest_operation(self, op: OperationIn) -> dict:
        ts = op.timestamp.astimezone()
        op_id = op.op_id or uuid.uuid4().hex[:12]
        st = None
        if op.op_type in ("initial_load", "rebind") and op.plate_id:
            if not op.dish_id:
                return {"op_id": op_id, "error": "initial_load/rebind 需要 dish_id（盘-菜绑定）"}
            st = self._open_serving(op.plate_id, op.dish_id, ts.isoformat(),
                                    op.recorded_net_g, op.tare_g, op.simulated)
        elif op.op_type == "pull" and op.plate_id:
            st = self._close_serving(op.plate_id, ts.isoformat(),
                                     op.disposal_reason or "未标记")
        elif op.plate_id:
            opens = db.get_servings(plate_id=op.plate_id, status="open")
            st = self.states.get(opens[-1]["serving_id"]) if opens else None

        db.insert_op({
            "op_id": op_id, "timestamp": ts.isoformat(), "plate_id": op.plate_id,
            "op_type": op.op_type, "dish_id": op.dish_id,
            "recorded_net_g": op.recorded_net_g, "recorded_added_g": op.recorded_added_g,
            "tare_g": op.tare_g, "disposal_reason": op.disposal_reason,
            "operator": op.operator, "note": op.note, "source": op.source,
            "simulated": op.simulated,
        })

        closed_tasks: list[dict] = []
        if op.op_type == "refill" and op.plate_id:
            # 增量以补菜后的下一次称重为准；此处按记录闭环，不一致会在下一次称重时暴露
            closed_tasks = refill.auto_close_refill_tasks(
                op.plate_id, float(op.recorded_added_g or 0), None)

        result: dict[str, Any] = {"op_id": op_id, "op_type": op.op_type,
                                  "closed_tasks": closed_tasks}
        if st is not None:
            result["snapshot"] = self.serving_snapshot(st)
        return result

    # ------------------------------------------------ 快照与汇总（契约 2）

    def serving_snapshot(self, st: ServingState) -> dict:
        dish = db.get_dish(st.dish_id) or {}
        m = analytics.serving_metrics(st)
        return {
            "plate_id": st.plate_id, "serving_id": st.serving_id,
            "dish_id": st.dish_id, "dish_name": dish.get("name", st.dish_id),
            "status": "open" if st.anomaly is None else "open_anomaly",
            "trusted_net_g": st.trusted_net_g, "baseline_g": st.baseline_g,
            "remaining_ratio": m["remaining_ratio"],
            "remaining_count": analytics.remaining_count(st),
            "consumed_g": round(st.consumed_g, 1), "refilled_g": round(st.refilled_g, 1),
            "last_station": st.last_station,
            "last_observed_at": st.last_obs_at.isoformat() if st.last_obs_at else None,
            "opened_at": None, **m, "anomaly": st.anomaly, "simulated": st.simulated,
        }

    def plates(self) -> list[dict]:
        snaps = []
        for sid in sorted(self.states):
            snaps.append(self.serving_snapshot(self.states[sid]))
        for s in snaps:
            row = db.query_one("SELECT opened_at FROM servings WHERE serving_id=?", (s["serving_id"],))
            s["opened_at"] = row["opened_at"] if row else None
        return snaps

    def dish_summary(self) -> list[dict]:
        return analytics.dish_summaries(list(self.states.values()))

    # ------------------------------------------------ 任务（契约 3）

    def tasks(self, status: Optional[str] = None) -> list[dict]:
        out = []
        dishes = {d["dish_id"]: d for d in db.get_dishes()}
        for t in db.get_tasks(status=status):
            t["dish_name"] = dishes.get(t["dish_id"], {}).get("name")
            t["simulated"] = bool(t.get("simulated"))
            out.append(t)
        return out

    def update_task(self, task_id: str, upd: TaskUpdateIn) -> Optional[dict]:
        t = next((x for x in db.get_tasks() if x["task_id"] == task_id), None)
        if t is None:
            return None
        now = db.now_iso()
        # 数量/单位检查（任务单要求）：克数、件数只接受非负数，不能同时改写成互相矛盾的口径
        if upd.quantity_g is not None and upd.quantity_g < 0:
            raise ValueError("quantity_g 不能为负")
        if upd.quantity_count is not None and upd.quantity_count < 0:
            raise ValueError("quantity_count 不能为负")
        if upd.postpone_min is not None and upd.postpone_min < 0:
            raise ValueError("postpone_min 不能为负")
        if upd.status:
            if upd.status not in ("making", "done", "cancelled", "postponed", "pending"):
                raise ValueError(f"非法状态 {upd.status}")
            t["status"] = upd.status
            if upd.status in ("done", "cancelled"):
                t["closed_at"] = now
        if upd.status == "postponed" and upd.postpone_min:
            t["need_by"] = (datetime.now().astimezone()
                            + timedelta(minutes=upd.postpone_min)
                            ).isoformat(timespec="seconds")
        if upd.quantity_g is not None:
            t["quantity_g"] = upd.quantity_g
        if upd.quantity_count is not None:
            t["quantity_count"] = upd.quantity_count
            # 件数与克数分开保存；单位在任务生成时按菜品是否易计数决定，修改数量不改口径
        if upd.note:
            t["close_note"] = ((t.get("close_note") or "") + f" | {upd.note}").strip(" |")
        t["updated_at"] = now
        db.insert_task(t)
        return t

    def resume_dispatch(self, dish_id: str) -> dict:
        db.set_dish_flag(dish_id, "dispatch_suspended", False)
        self._anomaly_counts[dish_id] = 0
        return {"dish_id": dish_id, "dispatch_suspended": False}

    # ------------------------------------------------ 经营分析

    def summary(self) -> dict:
        return analytics.business_summary(list(self.states.values()))

    def waste(self) -> dict:
        return analytics.waste_ledger()

    def add_covers(self, c: CoversIn) -> dict:
        db.insert_covers(c.timestamp.astimezone().isoformat(), c.party_size,
                         c.note, c.simulated)
        return {"ok": True, "total_covers": self._total_covers()}

    def _total_covers(self) -> int:
        rows = db.query("SELECT SUM(party_size) n FROM covers")
        return int(rows[0]["n"] or 0) if rows else 0

    # ------------------------------------------------ 健康与配置

    def upsert_dish_config(self, d: DishConfigIn) -> dict:
        db.upsert_dish({**d.model_dump(), "countable": int(d.countable)})
        return db.get_dish(d.dish_id)

    def health(self) -> dict:
        jev = self.jev.health()
        return {
            "status": "ok",
            "mode": "offline_rules" if self.jev.offline else "online_with_model",
            "open_servings": len(self.states),
            "jev": jev,
        }
