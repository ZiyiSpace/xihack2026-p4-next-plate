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
              retries: int = 3, missing_endpoint: bool = False) -> dict:
        """带指数退避的重试；**只重试可能自己好的错误**。

        可重试：transport 异常、5xx（503 = model loading）、408/425/429，
        以及 404（**仅当**该端点应当存在时 —— AutoDL 代理在上游容器重启期间也回 404）。

        不重试：其余 4xx（401/403/404/413/422 …）。这些是确定性错误，重试只是白等。
        `missing_endpoint=True` 用于「服务器上可能没有这个端点」的调用（例如视觉服务器
        换版本时移除了某个接口），这类 404 立即失败，不再空等三次。

        注意：不能靠 `raise_for_status()` 来「不重试」—— 它抛的 `HTTPStatusError`
        是 `httpx.HTTPError` 的子类，会被下面的 `except` 抓住照样重试。必须显式跳出循环。
        """
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
            except httpx.HTTPError as e:
                last_err = e
            else:
                if r.status_code == 200:
                    return r.json()
                retryable = (r.status_code >= 500
                             or r.status_code in (408, 425, 429)
                             or (r.status_code == 404 and not missing_endpoint))
                if not retryable:
                    raise JevUnavailable(f"HTTP {r.status_code}: {r.text[:200]}") from None
                last_err = JevUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
            if attempt + 1 < retries:
                time.sleep(1.5 * (2 ** attempt))
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
                 repeat: int = 1, include_none: bool = False) -> Optional[dict]:
        """菜品识别。返回 {dish, confidence, margin, abstained, ranking}；置信度阈值由调用方判。

        `include_none` 追加一个「以上都不是」选项。服务方文档提醒它「容易误伤真的菜」，
        本队实测确认过（见 `vision.identify_dish`），只在称重判定为空盘时才开。
        """
        data = {"modality": "image", "repeat": str(repeat)}
        if include_none:
            data["include_none"] = "true"
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
        # 视觉服务器当前不提供 /v1/portion，404 直接失败，不要重试三次空等 25 秒
        return self._post("/v1/portion", files=files, data=data, missing_endpoint=True)

    # ---- 路线 A：量 ----

    def measure(self, empty_b64: str, before_b64: str, after_b64: str) -> Optional[dict]:
        """背景差分测余量比例（精确版）。前提：固定机位 + 空盘参照。"""
        files = [
            ("empty", ("empty.png", base64.b64decode(empty_b64), "image/png")),
            ("before", ("before.png", base64.b64decode(before_b64), "image/png")),
            ("after", ("after.png", base64.b64decode(after_b64), "image/png")),
        ]
        # 同上：该端点当前不存在；且本方法在仓库里没有任何调用者（死代码）
        return self._post("/v1/measure", files=files, missing_endpoint=True)

    def register_menu(self, name: str, items: list[str]) -> Optional[dict]:
        return self._post("/v1/menu", json_body={"name": name, "items": items})
