"""前端工作台适配层：在团队后端上实现工作台页面期待的接口方言。

设计（联调方案 Plan B）：
- 工作台代码零改动：页面 fetch 的相对路径 /api/state、/api/actions 等
  由本模块实现，数据全部来自 HotpotService（验证过的规则引擎）。
- 页面自带的演示模式/D1/规则引擎不在该链路上，不参与记账。
- 字段映射依据 modules/frontend/lib/engine.ts 的 Summary 结构与
  app/api/*/route.ts 的请求/响应形状（PR #1）。
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import config, db, images, refill
from .core import HotpotService
from .schemas import (DishConfigIn, OperationIn, StationEventIn,
                      TaskUpdateIn)

router = APIRouter()

# 菜品分类（工作台 UI 的展示分组，后端目录无此字段，静态补齐）
_CATEGORY = {
    "D01": "水果点心", "D02": "水果点心", "D03": "荤菜",
    "D04": "豆制品", "D05": "荤菜", "D06": "荤菜",
}

_STATUS_MAP_TO = {"pending": "pending", "making": "preparing",
                  "done": "completed", "cancelled": "cancelled",
                  "postponed": "pending"}
_DISPOSAL_TO = {"discard": "丢弃", "other_loss": "其他处置", "retain": "可复用"}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _unit_mass(d: dict) -> Optional[float]:
    return float(d["unit_mass_g"]) if d.get("unit_mass_g") else None


def _to_unit(g: Optional[float], d: dict) -> Optional[float]:
    """克 -> 工作台计量单位（件数菜按件）。"""
    if g is None:
        return None
    m = _unit_mass(d)
    return round(g / m, 1) if (d.get("countable") and m) else round(g, 1)


def _image_url(ref: Optional[str]) -> Optional[str]:
    """事件里的图片引用 -> 浏览器可直接取的 URL。

    采集端走图片存储时存的已经是 `/api/images/<name>`，原样返回；历史数据可能
    是宿主绝对路径或数据集相对路径，统一按文件名回落到存储；取不到时由存储返回 404。
    """
    if not ref:
        return None
    if ref.startswith("/api/images/"):
        return ref
    return images.url_for(os.path.basename(ref.replace("\\", "/")))


def _captures(dishes_cfg: dict, limit: int = 12) -> list[dict]:
    """工作台「最近抓拍」：带图的站点事件，最新在前。

    判读文字与视觉校验直接取自事件的 interpretation，不做二次加工，
    保证卡片和后端记账看到的是同一条证据。
    """
    rows = db.query(
        "SELECT * FROM station_events WHERE image_ref IS NOT NULL AND image_ref <> '' "
        "ORDER BY observed_at DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        interp = json.loads(r["interpretation"]) if r["interpretation"] else {}
        dish_id = _dish_of_serving(r.get("serving_id"))
        out.append({
            "eventId": r["event_id"], "at": r["observed_at"],
            "plateId": r["plate_id"], "stationId": r["station_id"],
            "dishId": dish_id,
            "dishName": (dishes_cfg.get(dish_id) or {}).get("name"),
            "claimedDishId": r.get("dish_id_claim"),
            "netWeightG": r.get("net_weight_g"),
            "quality": r.get("quality"),
            "imageUrl": _image_url(r.get("image_ref")),
            "classification": interp.get("classification"),
            "note": interp.get("note"),
            "bindingCheck": interp.get("binding_check"),
            "simulated": bool(r.get("simulated")),
        })
    return out


def build_view(service: HotpotService, source: str = "live") -> dict:
    """把 HotpotService 的状态组装成工作台 Summary 形状。"""
    dishes_cfg = {d["dish_id"]: d for d in db.get_dishes()}
    dishes_view = service.dish_summary()
    plates = service.plates()
    tasks = service.tasks()
    waste = service.waste()

    # ---- 台账（ledger，取/补/报损/开档/撤下，最新在前，200 条）----
    ledger: list[dict] = []
    for e in db.get_events(limit=1000):
        interp = e.get("interpretation") or {}
        cls = interp.get("classification")
        if cls == "removal":
            ledger.append({"kind": "take", "weightG": interp.get("taken_g") or 0.0})
        elif cls in ("refill_confirmed", "late_refill"):
            ledger.append({"kind": "refill", "weightG": abs(interp.get("delta_g") or 0.0)})
        elif cls == "baseline":
            ledger.append({"kind": "opening", "weightG": e.get("net_weight_g") or 0.0})
        else:
            continue
        ledger[-1].update({"id": e["event_id"], "at": e["observed_at"],
                           "dishId": _dish_of_serving(e.get("serving_id")),
                           "plateId": e["plate_id"],
                           "quantity": _ledger_qty(ledger[-1]["weightG"], e.get("serving_id"), dishes_cfg)})
    for o in db.get_ops(limit=1000):
        if o["op_type"] == "pull":
            reason = o.get("disposal_reason") or "未标记"
            kind = "waste" if reason in ("丢弃", "其他处置", "喂狗") else "withdraw"
            st = _serving_before_close(o["plate_id"], o["timestamp"])
            if st is None:
                continue
            ledger.append({"id": o["op_id"], "at": o["timestamp"], "kind": kind,
                           "dishId": st["dish_id"], "plateId": o["plate_id"],
                           "weightG": st.get("trusted_net_g") or 0.0,
                           "quantity": _ledger_qty(st.get("trusted_net_g"), None, dishes_cfg, st["dish_id"]),
                           "reason": reason})
        elif o["op_type"] == "initial_load" and o.get("recorded_net_g"):
            ledger.append({"id": o["op_id"], "at": o["timestamp"], "kind": "opening",
                           "dishId": o["dish_id"], "plateId": o["plate_id"],
                           "weightG": float(o["recorded_net_g"]),
                           "quantity": _ledger_qty(o["recorded_net_g"], None, dishes_cfg, o["dish_id"])})
    ledger.sort(key=lambda x: x["at"], reverse=True)
    ledger = ledger[:200]

    # ---- 菜品（Summary.dishes）----
    waste_by_dish: dict[str, float] = {}
    for entry in waste.get("entries", []):
        waste_by_dish[entry["dish_id"]] = waste_by_dish.get(entry["dish_id"], 0.0) + entry["remaining_g"]
    refill_qty: dict[str, float] = {}
    refill_cnt: dict[str, int] = {}
    for l in ledger:
        if l["kind"] == "refill":
            refill_qty[l["dishId"]] = refill_qty.get(l["dishId"], 0.0) + l["weightG"]
            refill_cnt[l["dishId"]] = refill_cnt.get(l["dishId"], 0) + 1

    dishes = []
    for dv in dishes_view:
        cfg = dishes_cfg.get(dv["dish_id"], {})
        lead = float(cfg.get("prep_time_min") or 3)
        safety = 5.0
        unit = "件" if cfg.get("countable") else "g"
        capacity = _to_unit(cfg.get("std_portion_g") or 300, cfg) or 300
        batch = _to_unit(cfg.get("batch_size") or cfg.get("std_portion_g") or capacity, cfg) or capacity
        target = round((capacity or 300) * 3, 1)
        cost_kg = (float(cfg["cost_per_10g"]) * 100) if cfg.get("cost_per_10g") else None
        stock = dv["remaining_count"] if dv["countable"] else dv["remaining_g"]
        rate = _to_unit(dv.get("velocity_g_per_min"), cfg)
        minutes_left = dv.get("eta_empty_min")
        incoming = _to_unit(dv.get("pending_refill_g") or 0.0, cfg)
        if dv.get("dispatch_suspended") or dv.get("stagnated_plates"):
            status = "anomaly"
        elif minutes_left is not None and minutes_left <= lead + safety:
            status = "urgent"
        elif stock is not None and target and stock < target * 0.4:
            status = "watch"
        else:
            status = "okay"
        waste_g = round(waste_by_dish.get(dv["dish_id"], 0.0), 1)
        dishes.append({
            "id": dv["dish_id"], "name": dv["name"],
            "category": _CATEGORY.get(dv["dish_id"], "菜品"), "unit": unit,
            "capacity": capacity, "batch": batch, "target": target,
            "leadMinutes": lead, "safetyMinutes": safety,
            "costPerKg": cost_kg, "confirmed": True,
            "stock": stock, "weightG": dv["remaining_g"], "plates": dv["open_plates"],
            "rate": rate, "minutesLeft": minutes_left, "incoming": incoming,
            "status": status,
            "takeG": dv["consumed_g"],
            "takeQuantity": _to_unit(dv["consumed_g"], cfg),
            "wasteG": waste_g,
            "wasteCost": round(waste_g / 1000 * cost_kg, 2) if cost_kg else 0.0,
            "refillCount": refill_cnt.get(dv["dish_id"], 0),
            "refillQuantity": round(refill_qty.get(dv["dish_id"], 0.0), 1),
            "costReady": cost_kg is not None,
            "expense": round((dv["consumed_g"] + waste_g) / 1000 * cost_kg, 2) if cost_kg else 0.0,
        })

    # ---- 任务（Summary.tasks）----
    tasks_view = []
    for t in tasks:
        cfg = dishes_cfg.get(t["dish_id"], {})
        qty = t.get("quantity_count") if cfg.get("countable") else t.get("quantity_g")
        tasks_view.append({
            "id": t["task_id"], "dishId": t["dish_id"], "quantity": qty,
            "createdAt": t.get("created_at") or _now_iso(),
            "dueAt": t.get("need_by") or t.get("created_at") or _now_iso(),
            "status": _STATUS_MAP_TO.get(t["status"], "pending"),
            "reason": t.get("reason") or "", "actual": 0, "refillBaseline": 0,
            "updatedAt": t.get("updated_at") or _now_iso(),
        })

    # ---- 订单（模拟收银人数；营收未接入，保持 null 不冒充）----
    orders = [{"id": str(r["id"]), "at": r["timestamp"], "guests": r["party_size"],
               "revenue": None, "groupSize": None}
              for r in db.query("SELECT * FROM covers ORDER BY timestamp")]

    # ---- 时段（11:00-22:00 十二格，与工作台一致）----
    take_by_hour = _hourly_take()
    guests_by_hour: dict[str, int] = {}
    for o in orders:
        hh = o["at"][11:13] + ":00"
        guests_by_hour[hh] = guests_by_hour.get(hh, 0) + o["guests"]
    hourly = [{"hour": f"{h:02d}:00",
               "guests": guests_by_hour.get(f"{h:02d}:00", 0),
               "takeG": round(take_by_hour.get(f"{h:02d}:00", 0.0), 1)}
              for h in range(11, 23)]

    # ---- 汇总指标 ----
    guests = sum(o["guests"] for o in orders)
    taken_g = round(sum(d["takeG"] for d in dishes), 1)
    wasted_g = round(sum(d["wasteG"] for d in dishes), 1)
    cost_ready = bool(dishes) and all(d["costReady"] for d in dishes)
    food_expense = round(sum(d["expense"] for d in dishes), 2)
    waste_cost = round(sum(d["wasteCost"] for d in dishes), 2)
    metrics = {
        "guests": guests, "revenue": None, "takenG": taken_g, "wastedG": wasted_g,
        "wasteCost": waste_cost, "foodExpense": food_expense,
        "perGuestCost": round(food_expense / guests, 2) if (guests and cost_ready) else None,
        "perGuestWaste": round(waste_cost / guests, 2) if (guests and cost_ready) else None,
        "foodMargin": None,
        "pending": len([t for t in tasks_view if t["status"] in ("pending", "preparing")]),
        "anomaly": len([d for d in dishes if d["status"] == "anomaly"]),
        "costReady": cost_ready,
    }

    settings = db.get_meta("adapter_settings") or {}
    if isinstance(settings, str):
        settings = {}
    return {
        "source": source, "clock": _now_iso(),
        "dishes": dishes, "tasks": tasks_view, "ledger": ledger, "orders": orders,
        "settings": {"autoEnabled": True,
                     "closeTime": settings.get("closeTime", refill.CLOSING_TIME),
                     "staleMinutes": int(config.STAGNATION_WARN_MIN)},
        "revision": len(db.get_events(limit=100000)) + len(db.get_ops(limit=100000)),
        "metrics": metrics, "hourly": hourly,
        "integrations": {"deepseekConfigured": False, "ingestConfigured": True,
                         "model": None},
        "captures": _captures(dishes_cfg),
        "plates": [{"id": p["plate_id"], "dishId": p["dish_id"],
                    "quantity": p.get("remaining_count") if p.get("remaining_count") is not None else p["trusted_net_g"],
                    "weightG": p["trusted_net_g"], "seenAt": p.get("last_observed_at"),
                    "quality": 1.0, "removed": False}
                   for p in plates],
    }


# ------------------------------------------------ 帮助函数

def _dish_of_serving(serving_id: Optional[str]) -> Optional[str]:
    if not serving_id:
        return None
    row = db.query_one("SELECT dish_id FROM servings WHERE serving_id=?", (serving_id,))
    return row["dish_id"] if row else None


def _serving_before_close(plate_id: str, closed_at: str) -> Optional[dict]:
    rows = db.query(
        "SELECT * FROM servings WHERE plate_id=? AND closed_at<=? ORDER BY closed_at DESC LIMIT 1",
        (plate_id, closed_at))
    return rows[0] if rows else None


def _ledger_qty(weight_g: Optional[float], serving_id: Optional[str],
                dishes_cfg: dict, dish_id: Optional[str] = None) -> Optional[float]:
    did = dish_id or _dish_of_serving(serving_id)
    if did is None or weight_g is None:
        return None
    return _to_unit(weight_g, dishes_cfg.get(did, {}))


_HOURLY_CACHE: dict = {}


def _hourly_take() -> dict:
    rows = db.query(
        """SELECT substr(observed_at,12,2) hh, SUM(json_extract(interpretation,'$.taken_g')) g
           FROM station_events
           WHERE json_extract(interpretation,'$.classification')='removal' GROUP BY hh""")
    return {f"{r['hh']}:00": round(r["g"] or 0, 1) for r in rows}


# ------------------------------------------------ 路由（工作台方言）

def attach(service: HotpotService) -> None:
    @router.get("/api/state")
    def api_state(source: str = "live"):
        return build_view(service, source)

    @router.post("/api/actions")
    async def api_actions(request: Request):
        body = await request.json()
        action = body.get("action")
        if action == "task":
            upd = TaskUpdateIn(
                status={"pending": "pending", "preparing": "making",
                        "completed": "done", "cancelled": "cancelled"}.get(body.get("status")),
                quantity_g=body.get("quantity"), note=body.get("note"),
                postpone_min=body.get("delayMinutes"))
            t = service.update_task(body.get("id", ""), upd)
            if t is None:
                return JSONResponse({"error": "任务不存在"}, status_code=404)
        elif action == "waste":
            service.ingest_operation(OperationIn(
                timestamp=_now_iso(), plate_id=body.get("plateId"), op_type="pull",
                disposal_reason=_DISPOSAL_TO.get(body.get("disposition"), "未标记"),
                note=body.get("reason"), source="adapter"))
        elif action == "simulate":
            pass  # 真实链路由采集/回放驱动，无需模拟推进
        elif action == "settings":
            db.set_meta("adapter_settings", {
                "closeTime": body.get("closeTime") or refill.CLOSING_TIME})
        elif action == "dish":
            cfg = db.get_dish(body.get("id") or "")
            if cfg is None:
                return JSONResponse({"error": "菜品不存在"}, status_code=404)
            cost = body.get("costPerKg")
            service.upsert_dish_config(DishConfigIn(
                dish_id=cfg["dish_id"], name=cfg["name"],
                countable=bool(cfg.get("countable")),
                unit_name=cfg.get("unit_name"), unit_mass_g=cfg.get("unit_mass_g"),
                std_portion_g=body.get("target") or cfg.get("std_portion_g"),
                prep_time_min=body.get("leadMinutes") or cfg.get("prep_time_min") or 3,
                batch_size=body.get("batch") or cfg.get("batch_size"),
                freshness_min=cfg.get("freshness_min"),
                cost_per_10g=(cost / 100) if cost else cfg.get("cost_per_10g")))
        else:
            return JSONResponse({"error": f"未知操作 {action}"}, status_code=400)
        return {"success": True, "revision": build_view(service)["revision"],
                "duplicate": False}

    @router.post("/api/analysis")
    async def api_analysis(request: Request):
        body = await request.json()
        question = (body.get("question") or "").strip()
        view = build_view(service, body.get("source", "live"))
        return {"provider": "rules", "model": None,
                "answer": _rule_analysis(view, question),
                "source": body.get("source", "live"), "generatedAt": _now_iso()}

    @router.post("/api/ingest")
    async def api_ingest(request: Request):
        body = await request.json()
        results = []
        for e in body.get("events", []):
            try:
                results.append({"eventId": e.get("eventId"), "status": _apply_observation(service, e)})
            except Exception as ex:  # noqa: BLE001 —— 单条失败不阻断批次
                results.append({"eventId": e.get("eventId"), "status": f"error: {ex}"})
        return {"success": True, "results": results,
                "revision": build_view(service)["revision"]}

    @router.get("/api/export")
    def api_export(source: str = "live"):
        view = build_view(service, source)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["数据来源", "日期", "菜品", "补充次数", "补充分量", "计量单位",
                    "取用重量g", "报损重量g", "报损成本元"])
        label = "模拟" if source == "demo" else "联调虚拟数据" if source == "test" else "真实"
        for d in view["dishes"]:
            w.writerow([label, view["clock"], d["name"], d["refillCount"],
                        d["refillQuantity"], d["unit"], d["takeG"], d["wasteG"],
                        f"{d['wasteCost']:.2f}"])
        csv_text = "\ufeff" + buf.getvalue().replace("\r", "")
        return Response(content=csv_text, media_type="text/csv;charset=utf-8",
                        headers={"Content-Disposition":
                                 f'attachment; filename="koala-{source}-report.csv"'})

    @router.get("/api/model-config")
    def api_model_config_get():
        return {"configured": False, "storageReady": False, "model": None}

    @router.post("/api/model-config")
    def api_model_config_post():
        return JSONResponse(
            {"error": "团队后端链路使用规则分析，未接入 DeepSeek 配置"},
            status_code=400)

    @router.delete("/api/model-config")
    def api_model_config_delete():
        return {"configured": False, "storageReady": False, "model": None}


def _apply_observation(service: HotpotService, e: dict) -> str:
    kind = e.get("kind")
    ts = e.get("timestamp") or _now_iso()
    if kind == "remove":
        service.ingest_operation(OperationIn(
            timestamp=ts, plate_id=e.get("plateId"), op_type="pull",
            disposal_reason=_DISPOSAL_TO.get(e.get("disposition") or "", "未标记"),
            note=e.get("reason"), source="adapter"))
        return "applied"
    if kind == "add":
        service.ingest_operation(OperationIn(
            timestamp=ts, plate_id=e.get("plateId"), op_type="initial_load",
            dish_id=e.get("dishId"), recorded_net_g=e.get("netWeightG"),
            source="adapter"))
    # observe（以及 add 之后）都按站点事件入账：解释规则在后端
    r = service.ingest_station_event(StationEventIn(
        event_id=e.get("eventId"), plate_id=e.get("plateId"), station_id="S1",
        observed_at=ts, net_weight_g=e.get("netWeightG"),
        item_count=e.get("count"), dish_id=e.get("dishId"), source="adapter"))
    if isinstance(r, dict) and r.get("error"):
        return f"error: {r['error']}"
    if isinstance(r, dict) and r.get("duplicate"):
        return "duplicate"
    return "applied"


def _rule_analysis(view: dict, question: str) -> str:
    """规则分析（口径对齐工作台 ruleAnalysis；数据来自后端）。"""
    m = view["metrics"]
    if not view["ledger"] and not view["orders"]:
        return "当前暂无经营数据，暂不能推断需求高峰、顾客喜好或经营收益。"
    ranked = sorted(view["dishes"], key=lambda d: -(d["takeG"] or 0))
    waste = sorted(view["dishes"], key=lambda d: -(d["wasteCost"] or 0))
    peak = max(view["hourly"], key=lambda h: h["guests"]) if view["hourly"] else None
    pending = [t for t in view["tasks"] if t["status"] in ("pending", "preparing")]
    names = {d["id"]: d["name"] for d in view["dishes"]}
    units = {d["id"]: d["unit"] for d in view["dishes"]}
    if not m.get("costReady") and any(k in question for k in ("成本", "利润")):
        return "真实成本尚未确认或配方成本缺失，暂不计算成本与毛利。"
    prefix = ("以下基于模拟数据，是规则分析演示。" if view["source"] == "demo"
              else "以下基于联调虚拟数据，不代表真实经营。" if view["source"] == "test"
              else "以下基于已接入记录，是规则分析。")
    if any(k in question for k in ("补", "缺", "库存")):
        detail = "；".join(
            f"{names.get(t['dishId'], t['dishId'])}：建议 {t['quantity']}{units.get(t['dishId'], 'g')}"
            f"，依据为{t['reason']}" for t in pending[:3]) or "当前无未结束任务"
        body = f"当前有 {len(pending)} 个未结束补菜任务。{detail}。请结合实际后厨准备时间执行。"
    elif any(k in question for k in ("浪费", "报损", "成本", "利润")):
        top = "、".join(f"{d['name']} ¥{d['wasteCost']:.2f}" for d in waste[:3]) or "无"
        body = (f"当日撤盘报损 {(m['wastedG'] / 1000):.2f} kg，估算报损成本 ¥{m['wasteCost']:.2f}。"
                f"报损成本较高的菜品为 {top}。建议试验小批量补充，并观察缺菜和满意度是否变化。")
    elif any(k in question for k in ("画像", "客群", "高峰", "人数")):
        peak_txt = f"{peak['hour']} 时段，共 {peak['guests']} 人" if peak else "无记录"
        body = (f"到店人数高峰为 {peak_txt}。"
                f"{'今日已记录 %d 位顾客。' % m['guests'] if m['guests'] else '尚无收银人数记录。'}"
                "只能描述时段群体的取用结构，不能据此判断个人喜好。")
    else:
        top = "、".join(f"{d['name']} {(d['takeG'] / 1000):.2f}kg" for d in ranked[:3]) or "无"
        body = (f"当前取用重量排名前列为 {top}。今日取用 {(m['takenG'] / 1000):.2f}kg，"
                f"撤盘报损 {(m['wastedG'] / 1000):.2f}kg。"
                f"建议先关注未结束的 {len(pending)} 个补菜任务，再核查报损高的菜品。")
    return (f"{prefix}\n\n{body}\n\n取用量不等于吃掉的量或真实喜好；食材毛利不等于净利润，"
            "未扣人工、租金、能源和其他成本。建议效果需要现场试验验证。")
