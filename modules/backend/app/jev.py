"""Jev 视觉服务器客户端（同步，限并发，带退避重试）。

对应服务器两条路线：
- 路线 A（量）：/v1/measure 背景差分 —— 精确、免显卡，需固定机位 + 空盘参照
- 路线 B（语义）：/v1/identify 认菜、/v1/ask 判断题、/v1/portion 两图比份数量

单卡串行推理：全局信号量限制并发（官方建议 2-3）。
JEV_API_KEY 为空时进入离线模式：所有调用直接返回 None，业务走纯规则。
"""
from __future__ import annotations

import base64
import threading
import time
from typing import Optional

import httpx

from . import config


class JevUnavailable(Exception):
    pass


class JevClient:
    def __init__(self) -> None:
        self._sem = threading.Semaphore(max(1, config.JEV_MAX_CONCURRENCY))
        self._client: Optional[httpx.Client] = None
        self._lock = threading.Lock()
        self.offline = not config.JEV_API_KEY

    # ---- 基础 ----

    def _conn(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(
                    base_url=config.JEV_BASE_URL,
                    headers={"Authorization": f"Bearer {config.JEV_API_KEY}"},
                    timeout=config.JEV_TIMEOUT_S,
                )
            return self._client

    def _post(self, path: str, *, json_body=None, files=None, data=None,
              retries: int = 3) -> dict:
        """带指数退避的重试；401/422/413 不重试（配置错误重试无意义）。"""
        if self.offline:
            raise JevUnavailable("离线模式（JEV_API_KEY 为空），不调用模型")
        last_err: Exception = JevUnavailable("未重试")
        for attempt in range(retries):
            try:
                with self._sem:
                    if files or data:
                        r = self._conn().post(path, files=files, data=data)
                    else:
                        r = self._conn().post(path, json=json_body)
                if r.status_code in (401, 422, 413):
                    r.raise_for_status()
                if r.status_code >= 500:
                    # 503 = model loading，等一拍再试
                    last_err = JevUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
                else:
                    r.raise_for_status()
                    return r.json()
            except (httpx.HTTPError, JevUnavailable) as e:
                last_err = e
            time.sleep(1.5 * (2**attempt))
        raise JevUnavailable(f"Jev 服务重试耗尽: {last_err}")

    def health(self) -> Optional[dict]:
        if self.offline:
            return None
        try:
            r = self._conn().get("/health", timeout=10)
            return r.json() if r.status_code == 200 else {"status": f"http_{r.status_code}"}
        except httpx.HTTPError as e:
            return {"status": f"unreachable: {e.__class__.__name__}"}

    # ---- 路线 B：语义 ----

    def identify(self, image_b64: str, options: list[str], menu_name: Optional[str] = None,
                 repeat: int = 1) -> Optional[dict]:
        """菜品识别。返回 {dish, confidence, ...}；confidence < 阈值由调用方判无效。"""
        data = {"modality": "image", "repeat": str(repeat)}
        if menu_name:
            data["menu"] = menu_name
        else:
            data["options"] = "\n".join(options)
        files = {"file": ("plate.png", base64.b64decode(image_b64), "image/png")}
        return self._post("/v1/identify", files=files, data=data)

    def ask(self, question: str, options: list[str], image_b64: Optional[str] = None,
            state: str = "") -> Optional[dict]:
        data = {"question": question, "options": "\n".join(options), "state": state}
        if image_b64:
            data["modality"] = "image"
            files = {"file": ("plate.png", base64.b64decode(image_b64), "image/png")}
            return self._post("/v1/ask", files=files, data=data)
        return self._post("/v1/ask", data=data)

    def portion(self, before_b64: str, after_b64: str, subject: str,
                count_max: int = 6, unit: str = "个",
                empty_b64: Optional[str] = None) -> Optional[dict]:
        """两图对比少了几个（粗版）。必须给 subject，count_max 贴近实际范围。"""
        files = [
            ("before", ("before.png", base64.b64decode(before_b64), "image/png")),
            ("after", ("after.png", base64.b64decode(after_b64), "image/png")),
        ]
        data = {"subject": subject, "count_max": str(count_max), "unit": unit}
        if empty_b64:
            files.append(("empty", ("empty.png", base64.b64decode(empty_b64), "image/png")))
            data["modality"] = "image"
        return self._post("/v1/portion", files=files, data=data)

    # ---- 路线 A：量 ----

    def measure(self, empty_b64: str, before_b64: str, after_b64: str) -> Optional[dict]:
        """背景差分测余量比例（精确版）。前提：固定机位 + 空盘参照。"""
        files = [
            ("empty", ("empty.png", base64.b64decode(empty_b64), "image/png")),
            ("before", ("before.png", base64.b64decode(before_b64), "image/png")),
            ("after", ("after.png", base64.b64decode(after_b64), "image/png")),
        ]
        return self._post("/v1/measure", files=files)

    def register_menu(self, name: str, items: list[str]) -> Optional[dict]:
        return self._post("/v1/menu", json_body={"name": name, "items": items})
