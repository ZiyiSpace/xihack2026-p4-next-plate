"""经营分析：取用速度 / 预计耗尽 / 滞留与报废风险 / 菜品排名 / 站点偏好 / 报损台账。

口径说明（对齐 PRD）：
- 按人头收费：取用量不直接等于收入，排名以"取用量 / 每百位顾客取用量"表达；
  付费项目接入订单后再统计真实销量。
- 转盘取用量 != 顾客实际吃掉的量；"取用"只表示盘内量减少。
- 模拟收银/成本输入显著标记 simulated，报告聚合时透传该标记。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from . import config, db
from .interpreter import ServingState

VELOCITY_WINDOW_MIN = 10  # 取用速度的计算窗口


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timedelta(hours=8))
    except ValueError:
        return None


def serving_metrics(state: ServingState, now: Optional[datetime] = None):
    """单个 serving 的派生指标。"""
    last_obs = state.last_obs_at
    now = now or last_obs or datetime.now().astimezone()
    events = [e for e in db.get_events(plate_id=state.plate_id)
              if e.get("serving_id") == state.serving_id]
    removals = [e for e in events
                if (e.get("interpretation") or {}).get("classification") == "removal"]

    # 近窗口取用速度
    win_start = now - timedelta(minutes=VELOCITY_WINDOW_MIN)
    recent = [_parse(e["observed_at"]) for e in removals if _parse(e["observed_at"]) and _parse(e["observed_at"]) > win_start]
    velocity = None
    if state.consumed_g > 0 and removals:
        window_min = max(
            1.0,
            min(VELOCITY_WINDOW_MIN,
                (now - (_parse(events[0]["observed_at"]) or now)).total_seconds() / 60),
        )
        recent_g = sum((e["interpretation"].get("taken_g") or 0) for e in removals
                       if _parse(e["observed_at"]) and _parse(e["observed_at"]) > win_start)
        velocity = round(recent_g / window_min, 2) if recent_g > 0 else 0.0
    _ = recent

    eta_empty = None
    if state.trusted_net_g and velocity and velocity > 0:
        eta_empty = round(state.trusted_net_g / velocity, 1)

    stagnation = None
    if state.last_removal_at:
        stagnation = round((now - state.last_removal_at).total_seconds() / 60, 1)
    elif state.baseline_g and state.last_obs_at:
        stagnation = round((now - state.last_obs_at).total_seconds() / 60, 1)

    remaining_ratio = None
    if state.trusted_net_g is not None and state.baseline_g:
        ratio = state.trusted_net_g / max(state.baseline_g, 1e-6)
        remaining_ratio = round(min(max(ratio, 0.0), 1.5), 3)

    # 报废风险：余量高 + 长时间无取用 / 超保鲜窗口
    dish = db.get_dish(state.dish_id) or {}
    freshness = dish.get("freshness_min") or config.FRESHNESS_WINDOW_MIN
    opened = _parse(db.query_one(
        "SELECT opened_at FROM servings WHERE serving_id=?", (state.serving_id,))["opened_at"]) \
        if db.query_one("SELECT opened_at FROM servings WHERE serving_id=?", (state.serving_id,)) else None
    age_min = round((now - opened).total_seconds() / 60, 1) if opened else None

    risk = "none"
    if remaining_ratio is not None and stagnation is not None:
        if (remaining_ratio >= 0.5 and stagnation >= freshness) or (age_min and age_min >= freshness * 1.5):
            risk = "high"
        elif remaining_ratio >= 0.5 and stagnation >= config.STAGNATION_WARN_MIN:
            risk = "watch"

    return {
        "velocity_g_per_min": velocity,
        "eta_empty_min": eta_empty,
        "stagnation_min": stagnation,
        "remaining_ratio": remaining_ratio,
        "age_min": age_min,
        "waste_risk": risk,
    }


def remaining_count(state: ServingState) -> Optional[int]:
    dish = db.get_dish(state.dish_id) or {}
    if not dish.get("countable") or not dish.get("unit_mass_g") or state.trusted_net_g is None:
        return None
    return int(round(state.trusted_net_g / float(dish["unit_mass_g"])))


def dish_summaries(states: list[ServingState], now: Optional[datetime] = None) -> list[dict]:
    """同类多盘汇总（不重复计数：在转余量只算 open serving；历史取用跨批次累计）。"""
    now = now or datetime.now().astimezone()
    dishes = {d["dish_id"]: d for d in db.get_dishes()}
    out: dict[str, dict] = {}

    # 历史口径：已关闭批次的累计取用（撤盘后不丢历史）
    closed_consumed: dict[str, float] = {}
    open_sids = {st.serving_id for st in states}
    for row in db.query(
            """SELECT s.dish_id dish_id, s.serving_id serving_id,
                      COALESCE(SUM(json_extract(e.interpretation,'$.taken_g')),0) consumed
               FROM station_events e JOIN servings s ON s.serving_id = e.serving_id
               WHERE json_extract(e.interpretation,'$.classification') = 'removal'
               GROUP BY s.dish_id, s.serving_id"""):
        if row["serving_id"] in open_sids:
            continue
        closed_consumed[row["dish_id"]] = closed_consumed.get(row["dish_id"], 0.0) + row["consumed"]

    for st in states:
        agg = out.setdefault(st.dish_id, {
            "dish_id": st.dish_id, "name": dishes.get(st.dish_id, {}).get("name", st.dish_id),
            "open_plates": 0, "remaining_g": 0.0, "consumed_g": 0.0, "refilled_g": 0.0,
            "remaining_count": 0, "countable": bool(dishes.get(st.dish_id, {}).get("countable")),
            "velocities": [], "etas": [], "stagnated_plates": 0,
            "pending_refill_g": 0.0, "dispatch_suspended": bool(dishes.get(st.dish_id, {}).get("dispatch_suspended")),
            "simulated": st.simulated,
        })
        agg["open_plates"] += 1
        agg["remaining_g"] += st.trusted_net_g or 0.0
        agg["consumed_g"] += st.consumed_g
        agg["refilled_g"] += st.refilled_g
        if agg["countable"]:
            agg["remaining_count"] += remaining_count(st) or 0
        m = serving_metrics(st, now)
        if m["velocity_g_per_min"] is not None:
            agg["velocities"].append(m["velocity_g_per_min"])
        if m["eta_empty_min"] is not None:
            agg["etas"].append(m["eta_empty_min"])
        if m["waste_risk"] in ("watch", "high"):
            agg["stagnated_plates"] += 1

    tasks = db.get_tasks()

    # 已关闭批次的历史取用并入（含无 open serving 的菜品）
    for dish_id in set(closed_consumed):
        if dish_id not in out:
            d = dishes.get(dish_id, {})
            out[dish_id] = {
                "dish_id": dish_id, "name": d.get("name", dish_id),
                "open_plates": 0, "remaining_g": 0.0, "consumed_g": 0.0, "refilled_g": 0.0,
                "remaining_count": 0, "countable": bool(d.get("countable")),
                "velocities": [], "etas": [], "stagnated_plates": 0,
                "pending_refill_g": 0.0, "dispatch_suspended": bool(d.get("dispatch_suspended")),
                "simulated": True,
            }
        out[dish_id]["consumed_g"] += closed_consumed.get(dish_id, 0.0)

    # 在途量只能累加一次，且必须在上面补全菜品之后：
    # 放在前面会让在售菜品被加两遍（待补充数量翻倍），挪到最后则同时覆盖
    # 「已撤盘但仍有未完成补菜任务」的菜品。
    for t in tasks:
        if t["status"] in ("pending", "making") and t["action"] == "refill":
            if t["dish_id"] in out:
                out[t["dish_id"]]["pending_refill_g"] += t.get("quantity_g") or 0.0

    result = []
    for agg in out.values():
        vels = agg.pop("velocities")
        etas = agg.pop("etas")
        agg["velocity_g_per_min"] = round(sum(vels) / len(vels), 2) if vels else None
        agg["eta_empty_min"] = round(min(etas), 1) if etas else None
        agg["remaining_g"] = round(agg["remaining_g"], 1)
        agg["consumed_g"] = round(agg["consumed_g"], 1)
        agg["refilled_g"] = round(agg["refilled_g"], 1)
        if not agg["countable"]:
            agg["remaining_count"] = None
        result.append(agg)
    return sorted(result, key=lambda d: -d["consumed_g"])


def station_preference() -> list[dict]:
    """取用偏好的空间分布：哪个站点观测到哪道菜被取最多（客群偏好线索）。"""
    rows = db.query(
        """SELECT s.dish_id, e.station_id, COUNT(*) n,
                  SUM(json_extract(e.interpretation,'$.taken_g')) taken_g
           FROM station_events e JOIN servings s ON s.serving_id = e.serving_id
           WHERE json_extract(e.interpretation,'$.classification') = 'removal'
           GROUP BY s.dish_id, e.station_id ORDER BY s.dish_id, taken_g DESC"""
    )
    by_dish: dict[str, list] = {}
    for r in rows:
        by_dish.setdefault(r["dish_id"], []).append({
            "station": r["station_id"], "times": r["n"],
            "taken_g": round(r["taken_g"] or 0, 1),
        })
    return [{"dish_id": d, "stations": v} for d, v in sorted(by_dish.items())]


def hourly_consumption() -> list[dict]:
    """时段取用趋势（小时桶）。"""
    rows = db.query(
        """SELECT substr(observed_at,1,13) hour,
                  SUM(json_extract(interpretation,'$.taken_g')) taken_g,
                  COUNT(*) times
           FROM station_events
           WHERE json_extract(interpretation,'$.classification') = 'removal'
           GROUP BY hour ORDER BY hour"""
    )
    return [{"hour": r["hour"].replace("T", " "), "taken_g": round(r["taken_g"] or 0, 1),
             "times": r["times"]} for r in rows]


def waste_ledger() -> dict:
    """报损台账：撤盘时按处置原因统计剩余量。只有闭环 serving 才计入。"""
    ops = db.get_ops(limit=2000)
    pulls = {o["plate_id"]: o for o in ops if o["op_type"] == "pull"}
    rows = db.query("SELECT * FROM servings WHERE status='closed'")
    ledger, totals = [], {"丢弃": 0.0, "喂狗": 0.0, "可复用": 0.0}
    for s in rows:
        op = pulls.get(s["plate_id"])
        reason = (op or {}).get("disposal_reason") or "未标记"
        remaining = s.get("trusted_net_g") or 0.0
        ledger.append({
            "serving_id": s["serving_id"], "plate_id": s["plate_id"],
            "dish_id": s["dish_id"], "remaining_g": round(remaining, 1),
            "disposal_reason": reason,
            "closed_at": s.get("closed_at"),
        })
        if reason in totals:
            totals[reason] += remaining
        else:
            totals[reason] = totals.get(reason, 0.0) + remaining
    return {
        "entries": ledger,
        "totals_g": {k: round(v, 1) for k, v in totals.items() if v > 0},
        "note": "报损=撤盘时剩余净重×处置原因；可复用不计入报废",
    }


def business_summary(states: list[ServingState]) -> dict:
    """经营总览（给看板头部）。"""
    dishes = dish_summaries(states)
    total_consumed = sum(d["consumed_g"] for d in dishes)
    total_remaining = sum(d["remaining_g"] for d in dishes)
    covers_rows = db.query("SELECT SUM(party_size) n FROM covers")
    covers = (covers_rows[0]["n"] if covers_rows and covers_rows[0]["n"] else 0) or 0
    sim_rows = db.query("SELECT COUNT(*) n FROM covers WHERE simulated=1")
    any_sim = bool(sim_rows and sim_rows[0]["n"])

    # 模拟成本（仅当菜品配置了 cost_per_10g）
    cost = 0.0
    dish_cfg = {d["dish_id"]: d for d in db.get_dishes()}
    for d in dishes:
        c = (dish_cfg.get(d["dish_id"]) or {}).get("cost_per_10g")
        if c:
            cost += d["consumed_g"] / 10.0 * float(c)

    ranking = [{"dish_id": d["dish_id"], "name": d["name"], "consumed_g": d["consumed_g"],
                "per_100_covers_g": round(d["consumed_g"] / covers * 100, 1) if covers else None}
               for d in dishes]

    return {
        "total_consumed_g": round(total_consumed, 1),
        "total_remaining_g": round(total_remaining, 1),
        "open_plates": sum(d["open_plates"] for d in dishes),
        "covers": covers,
        "per_cover_consumed_g": round(total_consumed / covers, 1) if covers else None,
        "simulated_finance": {
            "food_cost_yuan": round(cost, 2) if cost else None,
            "simulated": any_sim or cost > 0,
            "note": "成本为模拟口径（菜品 cost_per_10g 配置 × 取用量），不代表真实财务",
        },
        "dish_ranking": ranking,
        "station_preference": station_preference(),
        "hourly": hourly_consumption(),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
