"""数据契约：站点事件 / 状态快照 / 补菜任务（对齐团队计划书 v1.0 三份契约）。

设计约定：
- 克数与件数分开存，不混算（计划书"必须统一的计算规则"）。
- 所有可能来自模拟/回放的数据带 simulated 标记（PRD 要求模拟数据显著标记）。
- 缺失重量用 None，不用 0。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- 站点事件（契约 1）


class StationEventIn(BaseModel):
    """采集端上报的一次有效经过事件（连续视频帧应由采集端或后端合并后上报）。"""

    event_id: Optional[str] = Field(
        None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
        description="采集端唯一 id；不传则后端生成并用于去重。"
                    "该值同时是抓拍图片的存储名，含其他字符会被拒（422）")
    plate_id: str = Field(..., description="物理盘号（外部绑定，不依赖图中标记）")
    station_id: str = Field(..., description="观察站点 A/B/C...")
    observed_at: datetime = Field(..., description="采集时间（带时区）")
    lap_index: Optional[int] = Field(None, description="圈次；没有则由后端按站点序推算")
    net_weight_g: Optional[float] = Field(None, description="净重克；与 gross 二选一")
    gross_weight_g: Optional[float] = Field(None, description="毛重克（配合 tare_g 换算净重）")
    tare_g: Optional[float] = Field(None, description="皮重克")
    item_count: Optional[int] = Field(None, description="易计数菜品的件数（称重/视觉给出）")
    dish_id: Optional[str] = Field(None, description="采集端认为的菜品（用于校验绑定）")
    image_b64: Optional[str] = Field(None, description="图片 base64（可选，供视觉校验）")
    image_ref: Optional[str] = Field(None, description="图片引用路径/URL（可选）")
    visual_level: Optional[str] = Field(None, description="采集端视觉余量档位：满/中/少/空")
    quality: str = Field("normal", description="normal|blur|occluded|low_res")
    source: str = Field("station", description="station|replay|manual")
    simulated: bool = Field(False, description="是否模拟/回放数据")


class OperationIn(BaseModel):
    """人工/后厨操作记录：上盘、补菜、撤盘、报损、换菜绑定。"""

    op_id: Optional[str] = None
    timestamp: datetime
    plate_id: Optional[str] = None
    op_type: str = Field(
        ..., description="initial_load|refill|pull|rebind|discard_note"
    )
    dish_id: Optional[str] = Field(None, description="initial_load/rebind 时必填")
    recorded_net_g: Optional[float] = Field(None, description="initial_load 记录的净重")
    recorded_added_g: Optional[float] = Field(None, description="refill 记录的补充克数")
    tare_g: Optional[float] = None
    disposal_reason: Optional[str] = Field(
        None, description="撤盘处置：丢弃|喂狗|可复用（报损统计口径）"
    )
    operator: str = "staff"
    note: Optional[str] = None
    source: str = "manual"
    simulated: bool = False


# ---------------------------------------------------------------- 菜品配置


class DishConfigIn(BaseModel):
    dish_id: str
    name: str
    countable: bool = Field(False, description="是否易计数（按件展示）")
    unit_name: Optional[str] = Field(None, description="计数单位：个/片/块")
    unit_mass_g: Optional[float] = Field(None, description="单件标准克重（如小馒头 25g）")
    std_portion_g: Optional[float] = Field(None, description="标准装盘净重克")
    prep_time_min: float = Field(3.0, description="后厨准备时间（分钟），补菜提前量")
    batch_size: Optional[float] = Field(None, description="制作批量（件数或克数）")
    freshness_min: Optional[float] = Field(None, description="保鲜窗口分钟；空则用全局默认")
    cost_per_10g: Optional[float] = Field(None, description="模拟成本：每 10 克食材成本（元）")
    note: Optional[str] = None


# ---------------------------------------------------------------- 状态快照（契约 2）


class ServingSnapshot(BaseModel):
    plate_id: str
    serving_id: str
    dish_id: str
    dish_name: str
    status: str = Field("open", description="open|closed")
    trusted_net_g: Optional[float] = Field(None, description="最近一次可信净重")
    baseline_g: Optional[float] = None
    remaining_ratio: Optional[float] = None
    remaining_count: Optional[int] = Field(None, description="易计数菜品折算件数")
    consumed_g: float = 0
    refilled_g: float = 0
    last_station: Optional[str] = None
    last_observed_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    velocity_g_per_min: Optional[float] = Field(None, description="近期取用速度")
    eta_empty_min: Optional[float] = Field(None, description="按近期速度预计耗尽分钟数")
    stagnation_min: Optional[float] = Field(None, description="距上次取用的分钟数")
    waste_risk: str = Field("none", description="none|watch|high")
    anomaly: Optional[str] = Field(None, description="未解释突增等异常说明")
    binding_check: Optional[dict] = Field(None, description="视觉菜品校验结果")
    simulated: bool = False


# ---------------------------------------------------------------- 补菜任务（契约 3）


class TaskOut(BaseModel):
    task_id: str
    action: str = Field(..., description="refill|check|pull")
    dish_id: str
    dish_name: Optional[str] = None
    serving_id: Optional[str] = None
    plate_id: Optional[str] = None
    quantity_g: Optional[float] = None
    quantity_count: Optional[int] = None
    unit: str = "g"
    priority: str = Field("medium", description="high|medium|low")
    need_by: Optional[datetime] = None
    status: str = Field("pending", description="pending|making|done|cancelled|postponed")
    reason: Optional[str] = None
    evidence_event_ids: list[str] = Field(default_factory=list)
    simulated: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    close_note: Optional[str] = None


class TaskUpdateIn(BaseModel):
    status: Optional[str] = Field(None, description="making|done|cancelled|postponed")
    quantity_g: Optional[float] = None
    quantity_count: Optional[int] = None
    postpone_min: Optional[float] = None
    note: Optional[str] = Field(None, description="修改/取消/延后原因（PRD 要求记录原因）")


# ---------------------------------------------------------------- 经营分析


class CoversIn(BaseModel):
    """模拟收银输入：用餐人数（用于每客/每百客口径）。"""

    timestamp: datetime
    party_size: int = Field(..., ge=1)
    note: Optional[str] = None
    simulated: bool = True


class DishSummary(BaseModel):
    dish_id: str
    name: str
    open_plates: int = 0
    remaining_g: float = 0
    remaining_count: Optional[int] = None
    consumed_g: float = 0
    refilled_g: float = 0
    pending_refill_g: float = Field(0, description="未完成补菜任务的在途量")
    velocity_g_per_min: Optional[float] = None
    eta_empty_min: Optional[float] = None
    dispatch_suspended: bool = False
    stagnated_plates: int = 0
    simulated: bool = False


class InterpretationResult(BaseModel):
    classification: str = Field(
        ...,
        description="baseline|no_change|removal|refill_confirmed|unexplained_increase|"
        "anomaly_resolved|late_refill|merged_pass|out_of_order|no_weight"
        "（前七个由 interpreter 产出，merged_pass/out_of_order/no_weight 由 core 的"
        "合帧、乱序隔离、无称重分支产出）",
    )
    delta_g: Optional[float] = None
    detail: Optional[str] = None
    recorded_added_g: Optional[float] = None
    needs_remeasure: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
