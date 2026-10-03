"""样例数据接入：把「图片 + 称重」从管理端传进来，一键走通识别与入账。

这是给「手上还没有相机和称重台」准备的产品面。人先把样例传上来，点一次运行，
后端照**正常采集路径**走一遍（视觉识别 → 站点事件 → 解释 → 补菜），
不另开旁路——所以样例进来之后，工作台的余量、台账、补菜任务、时段分析全都自动带上它。

登记表存在 `data/samples.json`：
- 不放进通用的 `meta` 键值表，因为那里会被 `db.reset_all()` 整表清空，
  而「复位业务数据」不该顺手删掉用户上传的样例（改完输入想重算时正需要它们还在）。
- 读-改-写整体加锁。`db` 的锁只保护单条 SQL，跨不过「取快照 → 整表覆盖」，
  并发上传/编辑会互相整批覆盖。
"""
from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Callable, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from . import config, db, images, vision
from .core import HotpotService
from .schemas import OperationIn, StationEventIn

router = APIRouter()

# 与 hotpot.db 同目录；该目录已被 .gitignore 忽略
_STORE = os.path.join(os.path.dirname(config.DB_PATH), "samples.json")
_LOCK = threading.RLock()
# 上传时没给时间就按顺序每条隔一分钟，同一盘的多次经过天然有先后
_SECONDS_BETWEEN = 60
# 上盘记录要早于首次观测，后端才不会把首帧判成异常
_LOAD_LEAD_S = 15


class SamplePatchIn(BaseModel):
    """样例的可改字段。

    这些值会被当成采集读数直接入账，所以在入口就校验——之前收的是裸 dict，
    一个 `observed_at: "garbage"` 能一路存进来，直到入账时才炸成 500。
    """

    net_weight_g: Optional[float] = Field(None, ge=0, le=5000, description="称重读数，克")
    plate_id: Optional[str] = Field(None, max_length=32)
    station_id: Optional[str] = Field(None, max_length=8)
    dish_id: Optional[str] = Field(None, max_length=32, description="留空表示交给模型识别")
    observed_at: Optional[datetime] = None

    @field_validator("net_weight_g", mode="before")
    @classmethod
    def _blank_weight_means_none(cls, value):
        """清空输入框发的是空串，语义是「没有读数」，不是 0。"""
        return None if value == "" else value

    @field_validator("observed_at")
    @classmethod
    def _needs_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at 必须带时区偏移，例如 2026-10-03T18:00:00+08:00")
        return value


def attach(service: HotpotService) -> None:
    @router.get("/api/samples")
    def api_list():
        return _view(_load())

    @router.post("/api/samples")
    async def api_create(files: list[UploadFile] = File(...), items: str = Form("[]")):
        return await _create(files, items)

    @router.patch("/api/samples/{sample_id}")
    async def api_update(sample_id: str, patch: SamplePatchIn):
        return _update(sample_id, patch)

    @router.delete("/api/samples/{sample_id}")
    def api_delete(sample_id: str):
        return _delete(sample_id)

    @router.delete("/api/samples")
    def api_clear():
        with _LOCK:
            _save([])
            return _view([])

    @router.post("/api/samples/{sample_id}/identify")
    def api_identify(sample_id: str):
        return _identify(sample_id)

    @router.post("/api/samples/ingest")
    def api_ingest():
        with _LOCK:
            return _ingest(service)


# ---------------------------------------------------------------- 登记表

def _load() -> list[dict]:
    try:
        with open(_STORE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    # 只认结构完整的条目，脏数据不该让后面每一次请求都 500
    return [s for s in data if isinstance(s, dict) and s.get("sample_id")]


def _save(samples: list[dict]) -> None:
    os.makedirs(os.path.dirname(_STORE), exist_ok=True)
    tmp = _STORE + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _STORE)


def _mutate(change: Callable[[list[dict]], None]) -> dict:
    """读-改-写整体加锁，返回最新视图。"""
    with _LOCK:
        samples = _load()
        change(samples)
        _save(samples)
        return _view(samples)


def _find(samples: list[dict], sample_id: str) -> dict:
    for s in samples:
        if s["sample_id"] == sample_id:
            return s
    raise HTTPException(404, f"没有这条样例：{sample_id}")


def _view(samples: list[dict]) -> dict:
    return {
        "samples": samples,
        "counts": {
            "total": len(samples),
            "pending": len([s for s in samples if s.get("status") != "done"]),
            "done": len([s for s in samples if s.get("status") == "done"]),
            "no_weight": len([s for s in samples if s.get("net_weight_g") is None]),
        },
    }


def summary() -> dict:
    """样例清单，供工作台视图直接带出（前端不用再单独拉一次）。"""
    return _view(_load())


def _effective_dish(s: dict) -> Optional[str]:
    """入账时用哪道菜：人工指定的优先，否则用识别结果（置信不足不算数）。"""
    if s.get("dish_id"):
        return s["dish_id"]
    r = s.get("recognized") or {}
    return r.get("dish_id") if (r.get("dish_id") and not r.get("below_floor")) else None


# ---------------------------------------------------------------- 上传与编辑

async def _create(files: list[UploadFile], items_json: str) -> dict:
    items = _parse_items(items_json, len(files))
    base = datetime.now().astimezone().replace(microsecond=0)
    # 先把文件读出来（唯一需要 await 的一步），加锁期间不做 IO 等待
    payloads = [(f.filename, await f.read()) for f in files]
    created = []
    for i, (filename, data) in enumerate(payloads):
        if not data:
            raise HTTPException(400, f"{filename} 是空文件")
        item = items[i]
        sample_id = "S-" + uuid.uuid4().hex[:8]
        original = images.key_of(filename or "")
        created.append({
            "sample_id": sample_id,
            "image_name": original or f"{sample_id}.png",
            "image_ref": _store_image(original, data, sample_id),
            "bytes": len(data),
            "plate_id": str(item.get("plate_id") or "").strip(),
            "station_id": str(item.get("station_id") or "A").strip() or "A",
            "net_weight_g": _weight(item.get("net_weight_g")),
            "dish_id": item.get("dish_id") or None,
            # 没给时间就按选择顺序排开，用户不必手填
            "observed_at": item.get("observed_at")
                            or (base + timedelta(seconds=_SECONDS_BETWEEN * i)).isoformat(timespec="seconds"),
            "status": "pending",
            "recognized": None,
            "result": None,
            "created_at": base.isoformat(timespec="seconds"),
        })
    view = _mutate(lambda samples: samples.extend(created))
    return {"created": len(created), **view}


def _update(sample_id: str, patch: SamplePatchIn) -> dict:
    # exclude_unset 才能区分「没传这个字段」和「传了 null」
    changes = patch.model_dump(exclude_unset=True)
    if changes.get("dish_id") and not db.get_dish(changes["dish_id"]):
        raise HTTPException(422, f"没有这道菜：{changes['dish_id']}")
    found: dict = {}

    def change(samples: list[dict]) -> None:
        s = _find(samples, sample_id)
        if "net_weight_g" in changes:
            s["net_weight_g"] = changes["net_weight_g"]
        for key in ("plate_id", "station_id"):
            if key in changes:
                s[key] = (changes[key] or "").strip()
        if "dish_id" in changes:
            s["dish_id"] = (changes["dish_id"] or "").strip() or None
        if "observed_at" in changes:
            s["observed_at"] = changes["observed_at"].isoformat(timespec="seconds")
        if {"dish_id", "net_weight_g"} & set(changes):
            # 改过输入就当没跑过；但事件按编号幂等，重复入账不会翻倍，
            # 想让改动生效要走「复位并重跑」——这句话在结果里如实说明。
            s["status"] = "pending"
            s["result"] = None
        found.update(s)

    view = _mutate(change)
    return {"sample": found, **view}


def _delete(sample_id: str) -> dict:
    def change(samples: list[dict]) -> None:
        _find(samples, sample_id)
        samples[:] = [s for s in samples if s["sample_id"] != sample_id]

    return _mutate(change)


def _store_image(filename: str, data: bytes, sample_id: str) -> str:
    """把上传的图放进图片存储，返回 image_ref。

    优先沿用上传时的文件名（用户能对上号）；名字不合法或同名不同内容时，
    回落到按样例编号命名，绝不覆盖存储里已有的图。
    """
    for name in (filename, f"{sample_id}.png"):
        if not name:
            continue
        try:
            return images.store(name, data)
        except images.ImageConflictError:
            continue  # 同名不同图：换一个 key，不覆盖
        except images.ImageStoreError:
            continue  # 名字不合法：同样换 key
    raise HTTPException(400, "图片无法写入存储")


def _parse_items(raw: str, count: int) -> list[dict]:
    try:
        parsed = json.loads(raw or "[]")
    except ValueError as e:
        raise HTTPException(422, f"items 不是合法 JSON：{e}") from e
    if not isinstance(parsed, list):
        raise HTTPException(422, "items 必须是数组")
    return [parsed[i] if i < len(parsed) and isinstance(parsed[i], dict) else {}
            for i in range(count)]


def _weight(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        grams = float(value)
    except (TypeError, ValueError):
        return None
    return grams if grams >= 0 else None


# ---------------------------------------------------------------- 识别与入账

def _identify(sample_id: str) -> dict:
    """识别这条样例的菜品，结果写回登记表。

    推理是慢的网络调用，放在锁外做；写回时如果这条已经被删掉就静默跳过。
    """
    s = _find(_load(), sample_id)
    if s.get("dish_id"):
        return {"sample": s, "skipped": True, "note": "已手动指定菜品，跳过识别"}
    image_b64 = _read_b64(s["image_ref"])
    if not image_b64:
        raise HTTPException(404, "这条样例的图片不在存储里了，请重新上传")
    # 称重说盘上没东西时，让模型可以选「以上都不是」；没称重就按有菜处理，
    # 免得误伤真的菜（实测：有菜时开这个选项准度会掉）。
    weight = s.get("net_weight_g")
    has_food = weight is None or float(weight) > config.NOISE_BOUND_G
    recognized = vision.identify_dish(image_b64, plate_has_food=has_food)
    updated: dict = {}

    def change(samples: list[dict]) -> None:
        for item in samples:
            if item["sample_id"] == sample_id:
                item["recognized"] = recognized
                item["status"] = "identified" if _effective_dish(item) else "need_dish"
                updated.update(item)
                return

    view = _mutate(change)
    return {"sample": updated, "recognized": recognized, **view}


def _ingest(service: HotpotService) -> dict:
    """把样例按盘分组入账：先开一盘，再按时间顺序报每一次经过。

    单条失败只记在这条上，不中断整批。否则前面已入账的事件留在库里，
    登记状态却因为走不到 `_save` 还停在「待运行」，两边对不上。
    """
    samples = _load()
    ready = [s for s in samples if _effective_dish(s)]
    blocked = [{"sample_id": s["sample_id"], "image_name": s["image_name"],
                "reason": "还没有可用菜品：识别不可信，或还没点运行识别"}
               for s in samples if not _effective_dish(s)]

    _assign_plates(ready)
    groups: dict[str, list[dict]] = {}
    for s in ready:
        groups.setdefault(s["plate_id"], []).append(s)

    results, failed = [], []
    for plate_id, group in groups.items():
        group.sort(key=lambda x: x["observed_at"])
        try:
            _open_plate(service, plate_id, group[0])
        except Exception as e:  # noqa: BLE001 —— 开盘点失败要让这一组每条都留下原因
            failed.extend(_mark_failed(group, f"开盘点失败：{e}"))
            continue
        for s in group:
            try:
                results.append(_ingest_one(service, s))
            except Exception as e:  # noqa: BLE001 —— 一条脏数据不该毁掉整批
                failed.extend(_mark_failed([s], str(e)))
    _save(samples)
    return {"ingested": len(results), "plates": sorted(groups),
            "blocked": blocked, "failed": failed,
            "results": results, **_view(samples)}


def _open_plate(service: HotpotService, plate_id: str, first: dict) -> None:
    """开盘：后端要求盘先有开放绑定才收站点事件，上盘量取该盘首次观测的净重。"""
    service.ingest_operation(OperationIn(
        op_id=f"SAMPLE-LOAD-{plate_id}",
        timestamp=_shift_iso(first["observed_at"], -_LOAD_LEAD_S),
        plate_id=plate_id, op_type="initial_load",
        dish_id=_effective_dish(first), recorded_net_g=first.get("net_weight_g"),
        source="sample", simulated=True))


def _assign_plates(ready: list[dict]) -> None:
    """没填盘号的按菜品分盘。

    同一道菜的多张图共用一个循环盘，后端才可能算出「取用」和补菜；
    一张图给一个盘只会得到一堆「首次上盘基准」，分析口径里什么都没有。

    手填的盘号也要占位，否则「有人填了 P001 + 另一道菜留空」时会把 P001 再发出去，
    两道菜混进同一个 serving。
    """
    used = {row["plate_id"] for row in db.query("SELECT DISTINCT plate_id FROM servings")}
    used |= {s["plate_id"] for s in ready if s.get("plate_id")}
    auto: dict[str, str] = {}
    number = 1
    for s in ready:
        if s.get("plate_id"):
            continue
        dish = _effective_dish(s)
        if dish not in auto:
            while f"P{number:03d}" in used:
                number += 1
            auto[dish] = f"P{number:03d}"
            used.add(auto[dish])
            number += 1
        s["plate_id"] = auto[dish]


def _mark_failed(group: list[dict], reason: str) -> list[dict]:
    out = []
    for s in group:
        s["status"] = "failed"
        s["result"] = _blank_result(reason)
        out.append({"sample_id": s["sample_id"], "image_name": s["image_name"],
                    "reason": reason[:200]})
    return out


_RESULT_FIELDS = ("error", "classification", "note", "taken_g", "delta_g",
                  "binding_check", "trusted_net_g", "new_tasks")


def _blank_result(error: Optional[str] = None) -> dict:
    """一条样例的入账结果骨架；成功与失败共用同一套字段，前端只认这一种。"""
    return {**{k: None for k in _RESULT_FIELDS}, "error": error, "new_tasks": []}


def _ingest_one(service: HotpotService, s: dict) -> dict:
    dish_from_manual = bool(s.get("dish_id"))
    image_bytes = _read_bytes(s["image_ref"]) if dish_from_manual else None
    # 菜品是模型识别出来的就不再传一遍图：那是同一次请求，会白跑一次推理。
    # 人工指定的则带上图，让后端做绑定校验，「这盘到底是不是这道菜」才有人证。
    r = service.ingest_station_event(StationEventIn(
        event_id=s["sample_id"], plate_id=s["plate_id"], station_id=s["station_id"],
        observed_at=s["observed_at"], net_weight_g=s.get("net_weight_g"),
        dish_id=_effective_dish(s), image_ref=s["image_ref"],
        source="sample", simulated=True), image_bytes)

    interp = r.get("interpretation") or {}
    duplicate = bool(r.get("duplicate") or r.get("merged"))
    if duplicate:
        # 事件按编号幂等，改完输入再点运行会被后端忽略。如实说出来，
        # 别显示成「已入账」让人以为改动生效了。
        s["status"] = "done"
        s["result"] = _blank_result(
            "这条样例已按编号入账过，本次上报被幂等忽略；改动要用「复位并重跑」才会生效")
    else:
        s["status"] = "failed" if r.get("error") else "done"
        result = _blank_result(r.get("error"))
        result.update({
            "classification": interp.get("classification"),
            "note": interp.get("note"),
            "taken_g": interp.get("taken_g"),
            "delta_g": interp.get("delta_g"),
            "binding_check": interp.get("binding_check"),
            "trusted_net_g": (r.get("snapshot") or {}).get("trusted_net_g"),
            "new_tasks": [t.get("task_id") for t in ((r.get("new_tasks") or {}).get("created") or [])],
        })
        s["result"] = result

    return {"sample_id": s["sample_id"], "image_name": s["image_name"],
            "plate_id": s["plate_id"], "dish_id": _effective_dish(s),
            "net_weight_g": s.get("net_weight_g"), **s["result"]}


# ---------------------------------------------------------------- 帮助

def _read_bytes(image_ref: str) -> Optional[bytes]:
    path = images.resolve(images.key_of(image_ref))
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None  # 查到了又被删掉，当作没有


def _read_b64(image_ref: str) -> Optional[str]:
    data = _read_bytes(image_ref)
    return base64.b64encode(data).decode() if data else None


def _shift_iso(iso: str, seconds: float) -> str:
    return (datetime.fromisoformat(iso) + timedelta(seconds=seconds)).isoformat(timespec="seconds")
