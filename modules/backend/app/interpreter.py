"""确定性事件解释状态机：把"称重读数序列 + 后厨操作记录"解释成业务事件。

规则全部可解释、可复现（计划书要求：固定输入得到可复现的状态）：

    首次读数                       -> baseline（建立基线）
    |Δ| <= 噪声界(默认4g)           -> no_change
    Δ < -噪声界                     -> removal（取用；不等于顾客吃完，不等于浪费）
    Δ > +噪声界 且窗口内有补菜记录    -> refill_confirmed
    Δ > +噪声界 且无补菜记录         -> unexplained_increase（异常：可信净重不更新，要求复测）
      下一次回到异常前可信值附近      -> anomaly_resolved（确认上次为传感器尖峰）
      下一次仍在高位且此时出现补菜记录 -> late_refill（晚到的补菜确认）

设计依据 hotpot_dataset_v0_1 的场景设定与 expected_behavior：
B003 +180g 无记录 => 标记未解释突增并要求复测；
B004 回到 149g    => 支持上一条读数异常，-182g 不得记为取用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import config


@dataclass
class ServingState:
    """一次上菜批次（serving）的滚动状态。"""

    serving_id: str
    plate_id: str
    dish_id: str
    trusted_net_g: Optional[float] = None      # 最近一次可信净重
    baseline_g: Optional[float] = None         # 初始装菜量
    tare_g: Optional[float] = None
    last_obs_at: Optional[datetime] = None
    last_station: Optional[str] = None
    last_lap: Optional[int] = None
    last_removal_at: Optional[datetime] = None  # 最近一次确认取用
    consumed_g: float = 0.0
    refilled_g: float = 0.0
    anomaly: Optional[dict] = None             # 未决异常 {observed_at, spike_g}
    simulated: bool = False
    evidence_ids: list[str] = field(default_factory=list)


def interpret(
    state: ServingState,
    net_g: float,
    observed_at: datetime,
    station_id: str,
    refill_ops: list[dict],
    event_id: str = "",
) -> tuple[str, dict]:
    """输入一次净重读数，返回 (classification, 详情)。调用方据此更新 state。

    refill_ops: 上次观测以来、该盘的补菜记录（timestamp 在 (last_obs_at, observed_at]）。
    """
    noise = config.NOISE_BOUND_G
    detail: dict = {"net_g": net_g, "station": station_id, "event_id": event_id}

    if state.trusted_net_g is None:
        state.baseline_g = net_g
        state.trusted_net_g = net_g
        state.last_obs_at = observed_at
        state.last_station = station_id
        return "baseline", detail

    delta = net_g - state.trusted_net_g
    detail["delta_g"] = round(delta, 1)

    # ---- 异常未决期间的特殊处理 ----
    if state.anomaly is not None:
        if abs(delta) <= noise:
            # 回到异常前的可信值 => 上次确为尖峰
            state.anomaly = None
            state.trusted_net_g = net_g
            state.last_obs_at = observed_at
            state.last_station = station_id
            return "anomaly_resolved", {**detail,
                                        "note": "读数回落至可信值附近，确认此前为传感器尖峰"}
        if delta > noise and refill_ops:
            added = sum(float(o.get("recorded_added_g") or 0) for o in refill_ops)
            state.anomaly = None
            state.trusted_net_g = net_g
            state.refilled_g += delta
            state.last_obs_at = observed_at
            state.last_station = station_id
            return "late_refill", {**detail, "recorded_added_g": added,
                                   "note": "高位维持且补菜记录到位，事后确认补菜"}
        # 仍未解释：保持异常，不更新可信值
        state.anomaly = {"observed_at": observed_at.isoformat(), "spike_g": round(delta, 1)}
        state.last_obs_at = observed_at
        state.last_station = station_id
        return "unexplained_increase", {**detail, "needs_remeasure": True,
                                        "note": "连续两次未解释增重，可信净重保持旧值，需人工复秤"}

    # ---- 正常路径 ----
    if abs(delta) <= noise:
        state.trusted_net_g = net_g
        state.last_obs_at = observed_at
        state.last_station = station_id
        return "no_change", detail

    if delta < 0:
        taken = -delta
        state.trusted_net_g = net_g
        state.consumed_g += taken
        state.last_removal_at = observed_at
        state.last_obs_at = observed_at
        state.last_station = station_id
        return "removal", {**detail, "taken_g": round(taken, 1),
                           "note": "取用（盘内量减少；不推断顾客是否吃完）"}

    # delta > noise
    if refill_ops:
        added = sum(float(o.get("recorded_added_g") or 0) for o in refill_ops)
        state.trusted_net_g = net_g
        state.refilled_g += delta
        state.last_obs_at = observed_at
        state.last_station = station_id
        return "refill_confirmed", {**detail, "recorded_added_g": added,
                                    "note": "增重与补菜记录匹配"}

    state.anomaly = {"observed_at": observed_at.isoformat(), "spike_g": round(delta, 1)}
    state.last_obs_at = observed_at
    state.last_station = station_id
    return "unexplained_increase", {**detail, "needs_remeasure": True,
                                    "note": "增重无补菜记录：标记未解释突增，要求复测；不更新可信净重"}


def same_pass(prev_at: Optional[datetime], prev_station: Optional[str],
              at: datetime, station: str) -> bool:
    """同盘同站、时间窗内的重复读数 => 视为同一次经过（合并，不重复计数）。"""
    if prev_at is None or prev_station != station:
        return False
    return (at - prev_at).total_seconds() <= config.PASS_MERGE_WINDOW_S
