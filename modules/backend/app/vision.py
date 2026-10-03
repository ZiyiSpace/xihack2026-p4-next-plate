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

# 服务方 /v1/identify 在 include_none=True 时追加的选项文本（对齐《使用说明》）
ABSTAIN_OPTION = "以上都不是"

# 交叉验证问句与档位。档位要平行（都由「还剩多少」这一档决定），
# 「看不清」必须留着：没有它，模型只能在「有菜/没菜」里硬选一个。
REMAINING_QUESTION = "这是一张餐厅转盘上菜盘的照片。只看照片，这个盘子上的食物大概还剩多少？"
REMAINING_OPTIONS = ["盘上还有不少菜", "只剩一点", "基本空了", "看不清"]
REMAINING_STATE = "侧拍的转盘菜盘照片，画面里可能有手、夹子或相邻的盘子。"


def _client() -> JevClient:
    return _SHARED[0]


_SHARED: list[JevClient] = []


def set_client(c: JevClient) -> None:
    _SHARED.clear()
    _SHARED.append(c)


def menu_names() -> list[str]:
    return [d["name"] for d in db.get_dishes()]


def identify_dish(image_b64: str, plate_has_food: bool = True) -> Optional[dict]:
    """纯识别：图片 -> 菜品，不预设绑定。

    和 `check_binding` 的区别是「我不知道这是什么菜」vs「验证是不是我以为的那道菜」。
    用于人工上传样例时的首次识别。离线/失败返回 `{"skipped": True}`，调用方据此回退。

    候选清单是**库里的全部菜品**，不是某一盘绑定的那道菜 —— 模型的任务是在整份菜单里
    做选择，不是给预设答案盖章。

    `plate_has_food=False`（称重判定盘上没东西）时给清单追加「以上都不是」。
    本队 v0.2 数据集实测（`modules/integration/measure_identify_options.py`）：
    空盘不给这个选项，模型会用 0.71–0.99 的置信度编一个菜名出来（6 张空盘只对 1 张），
    给了之后 6 张对 5 张。但有菜时开它会误伤真菜（14 张里 14 对 -> 10 对），
    所以只在称重说空盘时才开。
    """
    menu = {d["name"]: d["dish_id"] for d in db.get_dishes()}
    if not menu:
        return None
    try:
        r = _client().identify(image_b64, list(menu), include_none=not plate_has_food)
    except JevUnavailable as e:
        return {"skipped": True, "note": str(e)[:120]}
    if not r:
        return {"skipped": True}
    name = r.get("dish")
    conf = float(r.get("confidence") or 0)
    # 服务端把「以上都不是」当成一个候选返回，也可能自己标 abstained，两种都算弃权
    abstained = bool(r.get("abstained")) or name == ABSTAIN_OPTION
    return {
        "dish_id": None if abstained else menu.get(name),
        "recognized": name,
        "confidence": round(conf, 3),
        "margin": r.get("margin"),
        "abstained": abstained,
        "ranking": (r.get("ranking") or [])[:3],
        "below_floor": abstained or conf < config.IDENTIFY_CONFIDENCE_FLOOR,
        "source": "model",
    }


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


def check_remaining(image_b64: str) -> Optional[dict]:
    """视觉交叉验证：画面里这盘还剩多少？离线/失败返回 None（调用方按原规则记账）。

    只用来发现「秤说快空了、画面里却还有一盘」这种自相矛盾 —— 手/夹子挡在盘上时
    称重会掉读，而那一跳在重量序列上和真实取用长得一模一样，脱离画面分不出来。
    v1.0 数据集里 F003/C003 就是这种样本：秤读到 22/30 克，画面里还是满满一盘。
    """
    try:
        r = _client().ask(REMAINING_QUESTION, REMAINING_OPTIONS, image_b64=image_b64,
                          state=REMAINING_STATE)
    except JevUnavailable as e:
        return {"skipped": True, "note": str(e)[:120]}
    if not r:
        return {"skipped": True}
    verdict = r.get("prediction")
    conf = float(r.get("confidence") or 0)
    return {
        "verdict": verdict,
        "confidence": round(conf, 3),
        # 只认「还有不少菜」这一档：其余档位（只剩一点/基本空了/看不清）都不推翻称重，
        # 「看不清」更不该被当成「秤错了」的证据。
        "disagrees": verdict == REMAINING_OPTIONS[0] and conf >= config.CROSSCHECK_CONFIDENCE_FLOOR,
        "source": "model",
    }


def read_image_b64(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except OSError:
        return None
