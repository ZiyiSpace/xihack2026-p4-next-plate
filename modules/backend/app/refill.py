"""规则补菜引擎：确定性规则生成/合并任务；Agent 只做解释，不做计算。

触发与合并规则（PRD「现场流程与补菜任务」）：
- 余量低 或 预计耗尽时间 <= 准备时间+缓冲 => 补菜（refill）
- 余量低但无取用（滞留）                  => 巡检（check），不自动补
- 滞留超时且余量仍高                      => 撤盘建议（pull，防报废）
- 未完成任务的量计入待补充量（在制数量），避免重复派单
- 同菜同动作的未完成任务 => 更新而非新建
- 菜品被标记 dispatch_suspended（识别/称重异常）=> 暂停自动派单
- 闭店前 N 分钟 => 小批量（批量减半，低优先级）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from . import config, db
from .analytics import serving_metrics
from .interpreter import ServingState

CLOSING_TIME = "21:30"   # 闭店时间（演示配置，可移到菜品/门店配置）
PRE_CLOSE_MIN = 60       # 闭店前 N 分钟进入小批量模式
ETA_SAFETY_MIN = 5       # 预计耗尽触发线的安全缓冲


def _now() -> datetime:
    return datetime.now().astimezone()


def _pre_close(now: datetime) -> bool:
    try:
        hh, mm = map(int, CLOSING_TIME.split(":"))
        closing = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError:
        return False
    return timedelta(0) <= closing - now <= timedelta(minutes=PRE_CLOSE_MIN)


def evaluate_serving(state: ServingState, now: Optional[datetime] = None) -> list[dict]:
    """对单个 open serving 评估规则，返回需要新建/更新的任务。"""
    now = now or state.last_obs_at or _now()
    dish = db.get_dish(state.dish_id) or {}
    if dish.get("dispatch_suspended"):
        return []

    m = serving_metrics(state, now)
    ratio = m["remaining_ratio"]
    velocity = m["velocity_g_per_min"]
    eta = m["eta_empty_min"]
    stagnation = m["stagnation_min"]
    tasks: list[dict] = []

    prep = float(dish.get("prep_time_min") or 3)
    unit_mass = dish.get("unit_mass_g")
    countable = bool(dish.get("countable"))
    batch = dish.get("batch_size")

    # ---- 补菜判断 ----
    low_ratio = ratio is not None and ratio <= config.REFILL_RATIO_THRESHOLD
    eta_urgent = eta is not None and eta <= prep + ETA_SAFETY_MIN
    low_demand = (velocity is not None and velocity <= config.LOW_DEMAND_VELOCITY) or velocity is None

    if (low_ratio or eta_urgent) and not low_demand:
        # 在制/待处理量计入待补充量，避免重复派单
        pending_g = sum((t.get("quantity_g") or 0) for t in db.get_tasks()
                        if t["dish_id"] == state.dish_id and t["action"] == "refill"
                        and t["status"] in ("pending", "making"))
        target = state.baseline_g or dish.get("std_portion_g") or 300.0
        deficit = max(0.0, target - (state.trusted_net_g or 0.0) - pending_g)
        if deficit > config.NOISE_BOUND_G:
            qty_g = min(deficit, float(batch) if batch else deficit)
            priority = "high" if (eta is not None and eta <= prep) or (ratio or 1) <= 0.15 else "medium"
            reason = (f"余量{ratio:.0%}，速度{velocity}g/min，预计{eta}分钟耗尽"
                      if eta is not None else f"余量{ratio:.0%}")
            if pending_g > 0:
                reason += f"；在制{pending_g:.0f}g已计入"
            task = _task("refill", state, dish, qty_g, countable, unit_mass,
                         priority, reason, now, prep)
            tasks.append(task)

    # ---- 巡检：余量低但没人取 ----
    if low_ratio and low_demand and stagnation is not None and stagnation >= config.STAGNATION_WARN_MIN:
        tasks.append(_task(
            "check", state, dish, None, False, None, "low",
            f"余量低但{stagnation:.0f}分钟无取用：建议巡检（位置/卖相/新鲜度），不自动补菜",
            now, 0))

    # ---- 撤盘：滞留超保鲜窗口且余量仍高 ----
    # 已经到更换期限的盘子不再补一条撤盘建议：换下上新本身就是处置方式，
    # 两条任务说的是同一盘菜，只留可执行的那条。
    replace_overdue = m.get("replace_overdue_min")
    age = m.get("age_min")
    if m["waste_risk"] == "high" and ratio is not None and ratio >= 0.5 \
            and not (replace_overdue is not None and replace_overdue >= 0):
        tasks.append(_task(
            "pull", state, dish, None, False, None, "medium",
            f"滞留{stagnation:.0f}分钟且余量{ratio:.0%}：报废风险，建议撤盘或换位置促销",
            now, 0))

    # ---- 更换：这盘菜上转盘太久了，与余量多少无关 ----
    # 卖得慢的菜可能一直不缺货，但摆久了卖相和口感都会掉，要换下上新批次。
    if replace_overdue is not None and replace_overdue >= 0 and age is not None:
        tasks.append(_task(
            "replace", state, dish, None, False, None,
            "high" if replace_overdue >= config.REPLACE_AFTER_MIN / 2 else "medium",
            f"这盘已上转盘{age:.0f}分钟，超过更换期限"
            f"{m['replace_after_min']:.0f}分钟，余量{ratio:.0%}：建议换下并上新批次",
            now, 0))

    return tasks


def _task(action: str, state: ServingState, dish: dict, qty_g: Optional[float],
          countable: bool, unit_mass, priority: str, reason: str,
          now: datetime, prep_min: float) -> dict:
    need_by = (now + timedelta(minutes=prep_min)).isoformat(timespec="seconds") if action == "refill" else None
    qty_count = None
    unit = "g"
    if countable and unit_mass and qty_g:
        qty_count = int(round(qty_g / float(unit_mass)))
        unit = dish.get("unit_name") or "个"
        reason += f"；折合{qty_count}{unit}"
    if _pre_close(now):
        if qty_g:
            qty_g = qty_g / 2
            qty_count = int(round(qty_g / float(unit_mass))) if unit_mass and countable else None
        priority = "low"
        reason += "；闭店前小批量规则"
    return {
        "task_id": uuid.uuid4().hex[:12],
        "action": action,
        "dish_id": state.dish_id,
        "serving_id": state.serving_id,
        "plate_id": state.plate_id,
        "quantity_g": round(qty_g, 1) if qty_g else None,
        "quantity_count": qty_count,
        "unit": unit,
        "priority": priority,
        "need_by": need_by,
        "status": "pending",
        "reason": reason,
        "evidence": list(state.evidence_ids[-3:]),
        "simulated": state.simulated,
        "created_at": now.isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
        "closed_at": None,
        "close_note": None,
    }


def commit_tasks(new_tasks: list[dict]) -> tuple[list[dict], list[dict]]:
    """落库 + 合并：同(菜品,动作)存在未完成任务 => 更新数量/优先级/依据，不重复生成。"""
    created, updated = [], []
    quiet_after = timedelta(minutes=config.TASK_QUIET_MIN)
    for t in new_tasks:
        existing = None
        for o in db.get_tasks():
            if (o["dish_id"] == t["dish_id"] and o["action"] == t["action"]
                    and o["status"] in ("pending", "making", "postponed")
                    and (o.get("serving_id") == t.get("serving_id") or t["action"] in ("check", "pull"))):
                existing = o
                break
        if existing:
            done_at = existing.get("closed_at")
            if done_at and _parse_iso(done_at) and now_minus(_parse_iso(done_at)) < quiet_after:
                continue
            merged = dict(existing)
            merged.update({
                "quantity_g": t["quantity_g"] or existing.get("quantity_g"),
                "quantity_count": t["quantity_count"] or existing.get("quantity_count"),
                "priority": max(t["priority"], existing["priority"], key=lambda p: ["low", "medium", "high"].index(p)),
                "need_by": t.get("need_by") or existing.get("need_by"),
                "reason": t["reason"],
                "evidence": list(dict.fromkeys((existing.get("evidence") or []) + t["evidence"]))[:6],
                "updated_at": t["updated_at"],
                "status": "pending" if existing["status"] == "postponed" else existing["status"],
            })
            db.insert_task(merged)
            updated.append(merged)
        else:
            db.insert_task(t)
            created.append(t)
    return created, updated


def _parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def now_minus(dt: datetime) -> timedelta:
    return datetime.now().astimezone() - dt


def auto_close_refill_tasks(plate_id: str, recorded_added_g: float,
                            observed_delta_g: Optional[float]) -> list[dict]:
    """补菜记录到达时核对并闭环任务：完成反馈与称重核对不一致 => 提示人工检查。"""
    closed = []
    for t in db.get_tasks():
        if t["action"] != "refill" or t["status"] not in ("pending", "making", "postponed"):
            continue
        if t.get("plate_id") and t["plate_id"] != plate_id:
            continue
        mismatch = ""
        if observed_delta_g is not None and t.get("quantity_g"):
            diff = abs(observed_delta_g - float(t["quantity_g"]))
            if diff > max(config.NOISE_BOUND_G * 3, 0.25 * float(t["quantity_g"])):
                mismatch = f"称重核对不一致（任务{t['quantity_g']}g vs 实测{observed_delta_g:.0f}g），请人工检查"
        t.update({
            "status": "done",
            "closed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "close_note": f"补菜记录 {recorded_added_g}g 闭环" + (f"；{mismatch}" if mismatch else ""),
        })
        db.insert_task(t)
        closed.append(t)
    return closed
