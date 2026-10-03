"""数据集回放引擎：模拟"采集端 → 后端"的真实数据流，用于演示与管道自检。

- 按时间戳顺序合并 observations + operation_log 灌入 HotpotService（与 HTTP API 同一路径）
- offline 模式：纯称重规则，不调模型（模型挂了业务仍可跑的验证）
- online 模式：额外调用视觉服务器做绑定校验（每个 serving 首帧）与
  取用事件的份数交叉验证（仅易计数菜品，best-effort）
- 结束后与 answer_key 对评事件分类，报告写入 data/replay_report.json
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from datetime import datetime
from typing import Optional

from . import config, db, vision
from .core import HotpotService
from .jev import JevUnavailable
from .schemas import OperationIn, StationEventIn

# 我们的分类 -> answer_key.event_since_previous_observation 的可比映射
_MAP = {
    "baseline": "initial_load",
    "removal": ("removal", "removal_and_spread"),
    "refill_confirmed": "refill",
    "no_change": "no_change",
    "anomaly_resolved": "no_change",      # B004: 场景无变化 + 传感器异常恢复
    "unexplained_increase": "no_change",  # B003: 场景无变化 + 正向突跳 => 应标记而非记账
}


class ReplayRunner:
    def __init__(self, service: HotpotService) -> None:
        self.service = service
        self.status: dict = {"running": False, "mode": None, "progress": 0,
                             "total": 0, "log": [], "report": None, "error": None}
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------ 对外

    def start(self, mode: str = "offline", reset: bool = True) -> dict:
        if self.status["running"]:
            return {"error": "回放已在进行中"}
        if not os.path.isdir(config.DATASET_DIR):
            return {"error": f"数据集目录不存在: {config.DATASET_DIR}"}
        if reset:
            db.reset_all()
            self.service.__init__()  # 重建内存状态（含菜品种子）
        self.status = {"running": True, "mode": mode, "progress": 0, "total": 0,
                       "log": [], "report": None, "error": None}
        self._thread = threading.Thread(target=self._run, args=(mode,), daemon=True)
        self._thread.start()
        return {"started": True, "mode": mode}

    # ------------------------------------------------ 内部

    def _log(self, msg: str) -> None:
        self.status["log"].append(msg)
        self.status["log"] = self.status["log"][-50:]
        print(f"[replay] {msg}", flush=True)

    def _run(self, mode: str) -> None:
        try:
            self._run_inner(mode)
        except Exception as e:  # noqa: BLE001 —— 回放失败要暴露给前端
            self.status["error"] = f"{e.__class__.__name__}: {e}"
            self._log(traceback.format_exc(limit=3))
        finally:
            self.status["running"] = False

    def _run_inner(self, mode: str) -> None:
        ds = config.DATASET_DIR
        obs = [json.loads(l) for l in open(os.path.join(ds, "observations.jsonl")) if l.strip()]
        ops = [json.loads(l) for l in open(os.path.join(ds, "operation_log.jsonl")) if l.strip()]
        # 盘-菜绑定由外部提供（数据集 README：盘号由外部绑定，图无可解码标记）
        plate_dish = {o["plate_id"]: o["dish_id"] for o in obs}
        timeline = ([(o["timestamp"], "obs", o) for o in obs]
                    + [(o["timestamp"], "op", o) for o in ops])
        timeline.sort(key=lambda x: x[0])
        self.status["total"] = len(timeline)

        # online 模式先注册菜单（清单短 => 更准）
        if mode == "online" and not self.service.jev.offline:
            try:
                self.service.jev.register_menu("旋转小火锅", vision.menu_names())
                self._log("菜单已注册到视觉服务器")
            except JevUnavailable as e:
                self._log(f"菜单注册失败（继续按内联清单识别）: {e}")

        results: dict[str, dict] = {}
        first_image_done: set[str] = set()
        prev_image: dict[str, str] = {}   # plate -> 上次图片 b64（取用交叉验证用）

        for ts, kind, item in timeline:
            if kind == "op":
                dish_id = item.get("dish_id") or plate_dish.get(item.get("plate_id"))
                self.service.ingest_operation(OperationIn(
                    op_id=item["event_id"], timestamp=item["timestamp"],
                    plate_id=item.get("plate_id"), op_type=item["event_type"],
                    dish_id=dish_id,
                    recorded_net_g=item.get("recorded_net_g"),
                    recorded_added_g=item.get("recorded_added_g"),
                    source="replay", simulated=True))
                self._log(f"op    {item['event_id']} {item['event_type']}"
                          + (f" 绑定 {item.get('plate_id')}→{dish_id}"
                             if item["event_type"] in ("initial_load", "rebind") else ""))
            else:
                img_b64 = vision.read_image_b64(os.path.join(ds, item["image_path"]))
                # online：serving 首帧做绑定校验；取用事件做份数交叉验证
                image_bytes = None
                if mode == "online" and img_b64:
                    image_bytes = _b64_bytes(img_b64)
                r = self.service.ingest_station_event(StationEventIn(
                    event_id=item["sample_id"], plate_id=item["plate_id"],
                    station_id=item["station_id"], observed_at=item["timestamp"],
                    lap_index=item.get("lap_index"), net_weight_g=item.get("net_weight_g"),
                    gross_weight_g=item.get("gross_weight_g"), tare_g=item.get("tare_g"),
                    dish_id=item.get("dish_id"), image_b64=img_b64 if mode == "online" else None,
                    image_ref=item["image_path"], source="replay", simulated=True),
                    image_bytes=image_bytes)
                cls = (r.get("interpretation") or {}).get("classification") if isinstance(r, dict) else None
                results[item["sample_id"]] = r
                if isinstance(r, dict) and r.get("error"):
                    self._log(f"event {item['sample_id']} => 错误: {r['error']}")
                else:
                    self._log(f"event {item['sample_id']} @{item['station_id']} "
                              f"net={item.get('net_weight_g')}g => {cls}")

                if (mode == "online" and img_b64 and cls == "removal"
                        and item["plate_id"] in prev_image):
                    dish = db.get_dish(item["dish_id"]) or {}
                    if dish.get("countable"):
                        est = vision.portion_estimate(prev_image[item["plate_id"]], img_b64,
                                                      item["dish_id"])
                        if est:
                            self._log(f"  交叉验证(模型): 少约 {est['count']} "
                                      f"{dish.get('unit_name') or '个'} (conf={est['confidence']})")
                if img_b64:
                    prev_image[item["plate_id"]] = img_b64
                first_image_done.add(item["plate_id"])

            self.status["progress"] += 1

        report = self._evaluate(results)
        self.status["report"] = report
        out = os.path.join(os.path.dirname(config.DB_PATH), "replay_report.json")
        with open(out, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        self._log(f"回放完成：事件分类 {report['score']}/{report['total']} 与场景设定一致；"
                  f"报告已写入 {out}")

    def _evaluate(self, results: dict) -> dict:
        path = os.path.join(config.DATASET_DIR, "answer_key.jsonl")
        if not os.path.exists(path):
            return {"note": "无 answer_key，跳过对评", "total": 0, "score": 0, "rows": []}
        key = {json.loads(l)["sample_id"]: json.loads(l)
               for l in open(path) if l.strip()}
        rows, score = [], 0
        for sid, ans in key.items():
            r = results.get(sid, {})
            cls = (r.get("interpretation") or {}).get("classification") if isinstance(r, dict) else None
            expected = ans["event_since_previous_observation"]
            ok = expected == _MAP.get(cls) or (
                isinstance(_MAP.get(cls), tuple) and expected in _MAP[cls])
            score += int(ok)
            rows.append({
                "sample_id": sid, "ours": cls, "expected": expected,
                "sensor_condition": ans.get("sensor_condition"),
                "expected_behavior": ans.get("expected_behavior"),
                "match": ok,
                "detail": (r.get("interpretation") or {}).get("note") if isinstance(r, dict) else None,
            })
        return {
            "total": len(rows), "score": score,
            "mode": self.status["mode"],
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "rows": rows,
            "snapshots": self.service.plates(),
            "dish_summary": self.service.dish_summary(),
            "tasks": self.service.tasks(),
            "waste": self.service.waste(),
            "summary": self.service.summary(),
        }


def _b64_bytes(b64: str) -> bytes:
    import base64
    return base64.b64decode(b64)
