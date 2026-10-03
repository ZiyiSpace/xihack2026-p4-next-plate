"""后端 HTTP 客户端：虚拟采集端与后端之间唯一的耦合面。

只用标准库，因为这条采集链路要能脱离本仓库的 Python 环境单独跑起来
（真实部署时这一层会换成相机网关上的程序，依赖越少越好替换）。

所有方法只做「发请求 + 解 JSON + 把非 2xx 变成异常」，不含任何业务判断。
"""
from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from typing import Any, Optional


class BackendError(RuntimeError):
    """后端返回非 2xx；保留状态码与响应体，便于定位契约不一致。"""

    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        super().__init__(f"{method} {path} -> HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class BackendClient:
    def __init__(self, base_url: str, timeout: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------ 契约方法

    def health(self) -> dict:
        return self._request("GET", "/api/health")

    def list_images(self) -> dict:
        return self._request("GET", "/api/images")

    def list_events(self, limit: int = 200) -> list:
        return self._request("GET", f"/api/events?limit={int(limit)}")

    def state(self) -> dict:
        return self._request("GET", "/api/state")

    def analytics_summary(self) -> dict:
        return self._request("GET", "/api/analytics/summary")

    def reset(self) -> dict:
        return self._request("POST", "/api/maintenance/reset")

    def probe_image(self, name: str) -> tuple[int, str]:
        """确认这张图在存储里取得回，返回 (状态码, content-type)。

        只要开头一小段：验证的是「取得到、类型对」，没必要把整张 1.8 MB 拉一遍。
        存储不支持 Range 时会退化成 200 全量响应，这里读满 16 字节就断开。
        """
        req = urllib.request.Request(f"{self.base_url}/api/images/{name}",
                                     headers={"Range": "bytes=0-15"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp.read(16)
                return resp.status, resp.headers.get("content-type", "")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except urllib.error.URLError:
            return 0, ""

    def upload_image(self, name: str, content: bytes, filename: Optional[str] = None) -> dict:
        """把一张抓拍放进后端图片存储，返回 {image_ref, name, bytes, sha256}。"""
        boundary = "----capture" + uuid.uuid4().hex
        body = b"".join([
            _form_field(boundary, "name", name),
            _form_file(boundary, "file", filename or name, content),
            f"--{boundary}--\r\n".encode(),
        ])
        return self._request("POST", "/api/images", body,
                             {"Content-Type": f"multipart/form-data; boundary={boundary}"})

    def station_event(self, payload: dict) -> dict:
        return self._json("POST", "/api/events/station", payload)

    def operation(self, payload: dict) -> dict:
        return self._json("POST", "/api/events/operation", payload)

    def covers(self, payload: dict) -> dict:
        return self._json("POST", "/api/analytics/covers", payload)

    # ------------------------------------------------ 传输

    def _json(self, method: str, path: str, payload: dict) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self._request(method, path, body, {"Content-Type": "application/json"})

    def _request(self, method: str, path: str, body: Optional[bytes] = None,
                 headers: Optional[dict] = None) -> Any:
        req = urllib.request.Request(self.base_url + path, data=body, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise BackendError(method, path, e.code,
                               e.read().decode("utf-8", "replace")) from e
        except urllib.error.URLError as e:
            raise BackendError(method, path, 0, f"连接失败：{e.reason}") from e
        return json.loads(raw.decode("utf-8")) if raw else None


def _form_field(boundary: str, name: str, value: str) -> bytes:
    return (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n").encode("utf-8")


def _form_file(boundary: str, name: str, filename: str, content: bytes) -> bytes:
    media = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    head = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {media}\r\n\r\n")
    return head.encode("utf-8") + content + b"\r\n"
