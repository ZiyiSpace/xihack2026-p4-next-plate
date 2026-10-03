"""确定性事件解释状态机：把"称重读数序列 + 后厨操作记录"解释成业务事件。

规则全部可解释、可复现（计划书要求：固定输入得到可复现的状态）：

    首次读数                       -> baseline（建立基线）
    |Δ| <= 噪声界(默认4g)           -> no_change
    Δ < -噪声界 且画质可用           -> removal（取用；不等于顾客吃完，不等于浪费）
    Δ < -噪声界 且变化量不足采信      -> unexplained_decrease（不记账；下一次确认或撤销）
      「不足采信」有两个来源：采集端报告本帧遮挡，或视觉交叉验证与称重矛盾
    Δ > +噪声界 且窗口内有补菜记录    -> refill_confirmed
    Δ > +噪声界 且无补菜记录         -> unexplained_increase（异常：可信净重不更新，要求复测）
      下一次回到异常前可信值附近      -> anomaly_resolved（确认上次为传感器尖峰）
      下一次仍在低位                 -> removal（复测确认那一下是真的被取走了）
      下一次仍在高位且此时出现补菜记录 -> late_refill（晚到的补菜确认）

设计依据 hotpot_dataset_v0_1 的场景设定与 expected_behavior：
B003 +180g 无记录 => 标记未解释突增并要求复测；
B004 回到 149g    => 支持上一条读数异常，-182g 不得记为取用。

向上和向下都要防尖峰，理由不对称但结论一样：手或夹子挡在盘上时称重读数不可信，
而那一下「少了一百克」在重量序列上和真实取用长得一模一样，脱离画面分不出来。
v1.0 数据集把这类样本标成 hand_occlusion，期望行为是「保留疑点并复测」。
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
    quality: str = "normal",
    unreliable_gain: str = "",
) -> tuple[str, dict]:
    """输入一次净重读数，返回 (classification, 详情)。调用方据此更新 state。

    refill_ops:      上次观测以来、该盘的补菜记录（timestamp 在 (last_obs_at, observed_at]）。
    quality:         采集端对**这一帧**的画质判断（normal|blur|occluded|low_res），原样留痕。
    unreliable_gain: 非空表示这一帧的**变化量**不足采信，值是原因（进 note）。
                     由调用方给出：采集端报告遮挡，或视觉交叉验证与称重矛盾。
                     解释器只认「信不信这一跳」，不关心是谁说的 —— 这样加新的
                     不可信来源时不用改规则。
    """
    noise = config.NOISE_BOUND_G
    detail: dict = {"net_g": net_g, "station": station_id, "event_id": event_id}
    if quality != "normal":
        detail["frame_quality"] = quality
    # 「画质不足采信」这一步推导属于规则引擎，不属于调用方：
    # 调用方只补它自己知道的原因（比如视觉交叉验证），不用重复画质那套判断。
    if quality == "occluded" and not unreliable_gain:
        unreliable_gain = "采集端报告本帧画面被遮挡"

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
        if delta < -noise:
            # 复查仍是低位 => 那一跳不是传感器抖动，是真的少了，补记账（只是晚了一次观测）
            taken = -delta
            state.anomaly = None
            state.trusted_net_g = net_g
            state.consumed_g += taken
            state.last_removal_at = observed_at
            state.last_obs_at = observed_at
            state.last_station = station_id
            return "removal", {**detail, "taken_g": round(taken, 1),
                               "note": "复测仍为低位，确认上一条被遮挡/异常的读数背后确实少了一盘菜"}
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
        if unreliable_gain:
            # 这一帧的变化量不足采信：先挂起，等下一次读数确认或撤销。
            # 不更新可信净重，所以下一次若回到原值，上面那条 anomaly_resolved 分支会接住；
            # 若下一次仍在低位，则确认那一下是真的被取走了（见异常未决分支）。
            state.anomaly = {"observed_at": observed_at.isoformat(), "spike_g": round(delta, 1)}
            state.last_obs_at = observed_at
            state.last_station = station_id
            return "unexplained_decrease", {
                **detail, "needs_remeasure": True,
                "note": f"{unreliable_gain}，这一帧的下降暂不记账；等下一次读数确认或撤销"}
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


def worth_visual_check(state: ServingState, net_g: float) -> bool:
    """这一帧值不值得花一次视觉调用核对。

    只在「秤说这盘快空了，同时还在明显下降」时为真 —— 那正是遮挡/失真读数会伪装成
    「顾客把菜取光了」的场合。每帧都问会把视觉服务器变成热路径上的串行瓶颈，
    而且绝大多数帧的称重本来就没什么好核的。
    """
    if state.trusted_net_g is None or not state.baseline_g:
        return False
    if net_g - state.trusted_net_g > -config.NOISE_BOUND_G:
        return False
    return net_g / max(state.baseline_g, 1e-6) <= config.NEAR_EMPTY_RATIO
