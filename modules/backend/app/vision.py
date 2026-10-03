"""视觉模块适配层：调用 Jev 服务器，输出结构化结果并标注来源。

规则（来自服务端实测结论，详见《使用说明》）：
- 认菜：confidence < 0.6 => 当作没认出来；清单越短越准（6 道菜内）
- 测量：优先 /v1/measure（需固定机位+空盘参照）；无参照时用 /v1/portion 粗估，
  必须给 subject、count_max 贴近实际、不问百分比
- 模型不可用 => 返回 None 并标注 skipped，业务回退纯规则（计划书要求）
"""
from __future__ import annotations

import base64
from typing import Optional

from . import config, db
from .jev import JevClient, JevUnavailable


def _client() -> JevClient:
    return _SHARED[0]


_SHARED: list[JevClient] = []


def set_client(c: JevClient) -> None:
    _SHARED.clear()
    _SHARED.append(c)


def menu_names() -> list[str]:
    return [d["name"] for d in db.get_dishes()]


def check_binding(image_b64: str, expected_dish_id: str) -> Optional[dict]:
    """菜品绑定校验：识别结果 vs 绑定表。离线/失败返回 None。"""
    names = menu_names()
    if not names:
        return None
    dish = db.get_dish(expected_dish_id) or {}
    try:
        r = _client().identify(image_b64, names)
    except JevUnavailable as e:
        return {"skipped": True, "note": str(e)[:120]}
    if not r:
        return {"skipped": True}
    conf = float(r.get("confidence") or 0)
    recognized = r.get("dish")
    ok = conf >= config.IDENTIFY_CONFIDENCE_FLOOR and recognized == dish.get("name")
    return {
        "recognized": recognized,
        "confidence": round(conf, 3),
        "expected": dish.get("name"),
        "ok": ok,
        "below_floor": conf < config.IDENTIFY_CONFIDENCE_FLOOR,
        "source": "model",
    }


def portion_estimate(before_b64: str, after_b64: str, dish_id: str) -> Optional[dict]:
    """两图对比粗估"少了几个"（仅易计数菜品）。失败返回 None。"""
    dish = db.get_dish(dish_id) or {}
    if not dish.get("countable"):
        return None
    try:
        r = _client().portion(
            before_b64, after_b64,
            subject=dish["name"],
            count_max=int(dish.get("batch_size") or 6),
            unit=dish.get("unit_name") or "个",
        )
    except JevUnavailable:
        return None
    if not r:
        return None
    return {
        "count": r.get("count"),
        "answer": r.get("answer"),
        "confidence": r.get("confidence"),
        "note": "模型档位粗估，仅作交叉验证；不用于记账",
        "source": "model",
    }


def measure_reduction(empty_b64: str, before_b64: str, after_b64: str) -> Optional[dict]:
    """背景差分精确测量（固定机位 + 空盘参照）。"""
    try:
        r = _client().measure(empty_b64, before_b64, after_b64)
    except JevUnavailable:
        return None
    if not r:
        return None
    return {
        "remaining_ratio": r.get("remaining_ratio"),
        "reduced_ratio": r.get("reduced_ratio"),
        "source": "measure",
        "note": "背景差分，误差约1-2个百分点；残渣会高估剩余",
    }


def read_image_b64(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except OSError:
        return None
