"""样例数据接入：把「图片 + 称重」从管理端传进来，一键走通识别与入账。

这是给「手上还没有相机和称重台」准备的产品面。人先把样例传上来，点一次运行，
后端照**正常采集路径**走一遍（视觉识别 → 站点事件 → 解释 → 补菜），
不另开旁路——所以样例进来之后，工作台的余量、台账、补菜任务、时段分析全都自动带上它。

登记信息存在 `meta` 表的一行 JSON 里：刷新页面、重启后端都还在。
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from . import db, images, vision
from .core import HotpotService
from .schemas import OperationIn, StationEventIn

router = APIRouter()

_META_KEY = "samples"
# 上传时没给时间就按顺序每条隔一分钟，同一盘的多次经过天然有先后
_SECONDS_BETWEEN = 60
# 上盘记录要早于首次观测，后端才不会把首帧判成异常
_LOAD_LEAD_S = 15
_EDITABLE = ("plate_id", "station_id", "dish_id", "observed_at")


def attach(service: HotpotService) -> None:
    @router.get("/api/samples")
    def api_list():
        return _view(_load())

    @router.post("/api/samples")
    async def api_create(files: list[UploadFile] = File(...), items: str = Form("[]")):
        return await _create(files, items)

    @router.patch("/api/samples/{sample_id}")
    async def api_update(sample_id: str, patch: dict):
        return _update(sample_id, patch)

    @router.delete("/api/samples/{sample_id}")
    def api_delete(sample_id: str):
        return _delete(sample_id)

    @router.delete("/api/samples")
    def api_clear():
        _save([])
        return _view([])

    @router.post("/api/samples/{sample_id}/identify")
    def api_identify(sample_id: str):
        return _identify(sample_id)

    @router.post("/api/samples/ingest")
    def api_ingest():
        return _ingest(service)


# ---------------------------------------------------------------- 登记信息

def _load() -> list[dict]:
    raw = db.get_meta(_META_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _save(samples: list[dict]) -> None:
    db.set_meta(_META_KEY, samples)


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
            "pending": len([s for s in samples if s.get("status") not in ("done",)]),
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
    samples = _load()
    base = datetime.now().astimezone().replace(microsecond=0)
    created = []
    for i, f in enumerate(files):
        data = await f.read()
        if not data:
            raise HTTPException(400, f"{f.filename} 是空文件")
        item = items[i]
        sample_id = "S-" + uuid.uuid4().hex[:8]
        original = os.path.basename((f.filename or "").replace("\\", "/"))
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
    samples.extend(created)
    _save(samples)
    return {"created": len(created), **_view(samples)}


def _update(sample_id: str, patch: dict) -> dict:
    samples = _load()
    s = _find(samples, sample_id)
    if "net_weight_g" in patch:
        s["net_weight_g"] = _weight(patch["net_weight_g"])
    for key in _EDITABLE:
        if key in patch:
            value = str(patch[key] or "").strip()
            s[key] = (value or None) if key in ("dish_id", "observed_at") else value
    if "dish_id" in patch or "net_weight_g" in patch:
        # 改过输入就当没跑过，重新走一遍；已入账的事件按 event_id 幂等，不会翻倍
        s["status"] = "pending"
        s["result"] = None
    _save(samples)
    return {"sample": s, **_view(samples)}


def _delete(sample_id: str) -> dict:
    samples = _load()
    _find(samples, sample_id)
    _save([s for s in samples if s["sample_id"] != sample_id])
    return _view(_load())


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
        except images.ImageStoreError:
            continue
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
    """识别这条样例的菜品，结果写回登记信息。"""
    samples = _load()
    s = _find(samples, sample_id)
    if s.get("dish_id"):
        return {"sample": s, "skipped": True, "note": "已手动指定菜品，跳过识别"}
    image_b64 = _read_b64(s["image_ref"])
    if not image_b64:
        raise HTTPException(404, "这条样例的图片不在存储里了，请重新上传")
    recognized = vision.identify_dish(image_b64)
    s["recognized"] = recognized
    s["status"] = "identified" if _effective_dish(s) else "need_dish"
    _save(samples)
    return {"sample": s, "recognized": recognized, **_view(samples)}


def _ingest(service: HotpotService) -> dict:
    """把样例按盘分组入账：先开一盘，再按时间顺序报每一次经过。"""
    samples = _load()
    ready = [s for s in samples if _effective_dish(s)]
    blocked = [{"sample_id": s["sample_id"], "image_name": s["image_name"],
                "reason": "还没有可用菜品：识别不可信，或还没点运行识别"}
               for s in samples if not _effective_dish(s)]

    _assign_plates(ready)
    groups: dict[str, list[dict]] = {}
    for s in ready:
        groups.setdefault(s["plate_id"], []).append(s)

    results = []
    for plate_id, group in groups.items():
        group.sort(key=lambda x: x["observed_at"])
        first = group[0]
        # 后端要求盘先有开放绑定才收站点事件；上盘量取该盘首次观测的净重
        service.ingest_operation(OperationIn(
            op_id=f"SAMPLE-LOAD-{plate_id}",
            timestamp=_shift_iso(first["observed_at"], -_LOAD_LEAD_S),
            plate_id=plate_id, op_type="initial_load",
            dish_id=_effective_dish(first), recorded_net_g=first.get("net_weight_g"),
            source="sample", simulated=True))
        for s in group:
            results.append(_ingest_one(service, s))

    _save(samples)
    return {"ingested": len(results), "plates": sorted(groups),
            "blocked": blocked, "results": results, **_view(_load())}


def _assign_plates(ready: list[dict]) -> None:
    """没填盘号的按菜品分盘。

    同一道菜的多张图共用一个循环盘，后端才可能算出「取用」和补菜；
    一张图给一个盘只会得到一堆「首次上盘基准」，分析口径里什么都没有。
    """
    used = {row["plate_id"] for row in db.query("SELECT DISTINCT plate_id FROM servings")}
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
    s["status"] = "failed" if r.get("error") else "done"
    s["result"] = {
        "error": r.get("error"),
        "classification": interp.get("classification"),
        "note": interp.get("note"),
        "taken_g": interp.get("taken_g"),
        "delta_g": interp.get("delta_g"),
        "binding_check": interp.get("binding_check"),
        "trusted_net_g": (r.get("snapshot") or {}).get("trusted_net_g"),
        "new_tasks": [t.get("task_id") for t in ((r.get("new_tasks") or {}).get("created") or [])],
    }
    return {"sample_id": s["sample_id"], "image_name": s["image_name"],
            "plate_id": s["plate_id"], "dish_id": _effective_dish(s),
            "net_weight_g": s.get("net_weight_g"), **s["result"]}


# ---------------------------------------------------------------- 帮助

def _read_bytes(image_ref: str) -> Optional[bytes]:
    path = images.resolve(os.path.basename(image_ref.replace("\\", "/")))
    if not path:
        return None
    with open(path, "rb") as f:
        return f.read()


def _read_b64(image_ref: str) -> Optional[str]:
    data = _read_bytes(image_ref)
    return base64.b64encode(data).decode() if data else None


def _shift_iso(iso: str, seconds: float) -> str:
    return (datetime.fromisoformat(iso) + timedelta(seconds=seconds)).isoformat(timespec="seconds")
