"""抓拍图片存储：接收采集端上传的图，并按名提供同源 URL 读取。

真实部署里这一层由相机网关或对象存储承担；此处用本地目录实现同一个接缝，
让「拍照 → 上传 → 落存储 → 事件只引用 URL」在无硬件时也能完整走通。

对外约定：
- 存储 key 是单层名字，不含路径分隔符；写入后内容不可变，同 key 不同内容返回 409。
- 采集端上传字节时，事件里的 `image_ref` 是同源相对 URL（如 `/api/images/B001.png`），
  便于前端直接当 <img src> 用。历史数据里可能是宿主绝对路径或数据集相对路径，
  读取方按文件名回落到本存储（见 `adapter._image_url`）。
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import config

router = APIRouter()

# 单层名字：首字符必须是字母或数字，其后只允许字母数字与 _ . -，杜绝 ../ 越权。
# 前导点也被拒绝，所以下面 write 用的隐藏临时文件不会出现在列目录里。
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_EXT_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))
MAX_IMAGE_BYTES = 16 * 1024 * 1024

# store() 在同步端点里被线程池并发调用，查重与落盘要作为一个动作完成
_STORE_LOCK = threading.Lock()

# ---------------------------------------------------------------- 存储


class ImageStoreError(ValueError):
    """存储拒绝写入：名字非法。"""


class ImageConflictError(ImageStoreError):
    """同名但内容不同——覆盖会让已发出的引用指向另一张图，所以拒绝。"""


def url_for(name: str) -> str:
    """存储 key 对应的读取 URL。"""
    return f"/api/images/{name}"


def store(name: str, data: bytes) -> str:
    """把一张抓拍写入存储，返回其读取 URL。

    同名同内容幂等复用；同名不同内容抛 `ImageConflictError`，不静默覆盖。
    """
    if not _NAME_RE.match(name or ""):
        raise ImageStoreError(f"非法的图片名：{name!r}（只允许字母数字与 _.-，不含路径分隔符）")
    os.makedirs(config.IMAGE_DIR, exist_ok=True)
    path = os.path.join(config.IMAGE_DIR, name)
    with _STORE_LOCK:
        if os.path.exists(path):
            with open(path, "rb") as f:
                if hashlib.sha256(f.read()).digest() == hashlib.sha256(data).digest():
                    return url_for(name)
            raise ImageConflictError(f"存储中已有同名但内容不同的图片：{name}")
        # 临时名随机且以前导点开头：并发写不共用同一个临时文件，_NAME_RE 也让它
        # 对列目录和读取不可见，避免半张图被当成成品读走。
        tmp = os.path.join(config.IMAGE_DIR, f".{uuid.uuid4().hex}.part")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError:
            _discard(tmp)
            raise
    return url_for(name)


def resolve(name: str) -> Optional[str]:
    """存储 key 对应的可读路径；名字非法或文件不存在返回 None。"""
    if not _NAME_RE.match(name or ""):
        return None
    path = os.path.join(config.IMAGE_DIR, name)
    return path if os.path.isfile(path) else None


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass  # 临时文件本来就没建起来，没什么可清的


# ---------------------------------------------------------------- HTTP


@router.post("/api/images")
async def upload_image(file: UploadFile = File(...), name: Optional[str] = Form(None)):
    """上传一张抓拍。不传 name 时按内容哈希命名，同图天然去重。"""
    if file.size and file.size > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB 上限")
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB 上限")
    key = name or _content_name(file.filename, data)
    return {"image_ref": store(key, data), "name": key, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


@router.get("/api/images")
def list_images():
    """列出存储中的图片（演示时用来确认「采集到的图确实存下来了」）。"""
    if not os.path.isdir(config.IMAGE_DIR):
        return {"count": 0, "images": []}
    rows = []
    for name in sorted(os.listdir(config.IMAGE_DIR)):
        path = resolve(name)
        if path:
            rows.append({"name": name, "bytes": os.path.getsize(path),
                         "image_ref": url_for(name)})
    return {"count": len(rows), "images": rows}


@router.get("/api/images/{name}")
def read_image(name: str):
    """按存储 key 读取图片；名字非法与不存在都返回 404，不泄露目录结构。"""
    path = resolve(name)
    if not path:
        raise HTTPException(404, "存储中没有这张图片")
    return FileResponse(path, media_type=_media_type(path, name))


def _media_type(path: str, name: str) -> str:
    """先看文件头再退回后缀。

    `/api/events/station/upload` 不带文件名，落盘时统一叫 `.png`；直接传 JPEG
    就会得到一个字节是 JPEG、Content-Type 是 image/png 的响应，浏览器渲染不了。
    """
    with open(path, "rb") as f:
        head = f.read(16)
    for magic, media in _MAGIC:
        if head.startswith(magic):
            return media
    if head[8:12] == b"WEBP":
        return "image/webp"
    return _EXT_MEDIA.get(os.path.splitext(name)[1].lower(), "application/octet-stream")


def _content_name(filename: Optional[str], data: bytes) -> str:
    """按内容哈希生成存储 key，后缀沿用上传文件名（不认识就用 .png）。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in _EXT_MEDIA:
        ext = ".png"
    return hashlib.sha256(data).hexdigest()[:16] + ext
