#!/usr/bin/env python3
"""契约自检：用后端真实的 JevClient 打真实运行的 Jev 服务器。

做什么
------
1. **静态扫描** `modules/backend/app/*.py`，把后端实际用到的每个 `/v1/...` 端点找出来；
2. 拉服务器的 `/openapi.json`，对照这些端点哪些**还在**、哪些**已经没了**；
3. 用后端自己的 `app.jev.JevClient` **实打实调用**一遍，确认在线、密钥有效、返回可用。

为什么不用 curl 而是用后端自己的客户端
--------------------------------------
`jev.py` 有自己的重试、超时、离线降级逻辑。用真实的客户端测，测的才是真实的联调路径；
用 curl 测只能证明端口通。

**前提**：本脚本靠 `modules/backend/` 的目录结构 import 后端代码，所以它和后端是硬耦合的 ——
后端 `app/` 换位置或改包名，这里要跟着改。这是刻意换来的：宁可硬耦合到真实客户端，也不用
一份会漂移的复刻实现。

跑法
----
    # 密钥从环境变量读，绝对不要写进仓库
    JEV_API_KEY=jev_xxxx python modules/integration/check_jev_contract.py

    # 换地址 / 换图
    JEV_API_KEY=... python modules/integration/check_jev_contract.py --url https://host:8443
    JEV_API_KEY=... python modules/integration/check_jev_contract.py --image path/to/dish.png

退出码：0 = 后端用到的端点全部可用；1 = 有缺口；2 = 没给密钥。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "modules" / "backend"
DATASET = REPO / "modules" / "hotpot_dataset_v0_1"

# 后端 app/*.py 里出现的端点字符串，例如 _post("/v1/identify", ...)
ENDPOINT_RE = re.compile(r'["\'](/v1/[a-z_/]+)["\']')


# ---------------------------------------------------------------- 静态分析

def scan_backend_calls() -> dict[str, list[str]]:
    """Endpoint -> [file:line, ...] for every /v1/... the backend app calls."""
    found: dict[str, list[str]] = {}
    for path in sorted((BACKEND / "app").glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for endpoint in ENDPOINT_RE.findall(line):
                found.setdefault(endpoint, []).append(f"{path.name}:{lineno}")
    return found


def server_routes(base_url: str) -> set[str] | None:
    """Paths the running server advertises, or None when it cannot be read."""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/openapi.json", timeout=20) as fh:
            return set(json.load(fh).get("paths", {}))
    except Exception:
        return None


def _functions(path: Path):
    """Yield (name, first_line_number, body_text) for each def, indented or not.

    Class methods (JevClient.identify and friends) are indented, so the pattern
    must allow leading whitespace; the body runs until the next def at any depth.
    """
    current: str | None = None
    start = 0
    buffer: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.match(r"^\s*def (\w+)\(", line)
        if match:
            if current:
                yield current, start, "\n".join(buffer)
            current, start, buffer = match.group(1), lineno, [line]
        elif current is not None:
            buffer.append(line)
    if current:
        yield current, start, "\n".join(buffer)


def impact_chain(endpoint: str) -> list[str]:
    """Endpoint -> jev.py method -> vision.py wrapper -> callers, as text lines."""
    chain: list[str] = []
    jev_path = BACKEND / "app" / "jev.py"
    vision_path = BACKEND / "app" / "vision.py"

    methods = [(name, line) for name, line, body in _functions(jev_path) if endpoint in body]
    for method, line in methods:
        chain.append(f"jev.JevClient.{method}()          jev.py:{line}")
    if not methods:
        return chain

    wrappers: list[tuple[str, int]] = []
    for name, line, body in _functions(vision_path):
        if any(f"_client().{method}(" in body for method, _ in methods):
            wrappers.append((name, line))
            chain.append(f"  <- vision.{name}()            vision.py:{line}")

    for path in sorted((BACKEND / "app").glob("*.py")):
        if path.name in ("jev.py", "vision.py"):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name, _ in wrappers:
                if re.search(rf"(?<![\w.])vision\.{name}\(", line):
                    chain.append(f"       <- {path.name}:{lineno}")
    return chain


# ---------------------------------------------------------------- 渲染

def brief(value: object, limit: int = 120) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 1] + "…"


def report_static(calls: dict[str, list[str]], routes: set[str] | None) -> list[str]:
    """Print endpoint vs server table; return the endpoints the server lacks."""
    print("── 1. 后端用到的端点 vs 服务器实际提供 ──")
    print(f"{'端点':<18} {'服务器':<8} 后端调用点")
    print("-" * 78)
    missing: list[str] = []
    for endpoint in sorted(calls):
        present = "有" if (routes is None or endpoint in routes) else "缺失"
        if present == "缺失":
            missing.append(endpoint)
        print(f"{endpoint:<18} {present:<8} {', '.join(calls[endpoint])}")
    if routes is None:
        print("（无法读取 /openapi.json，跳过存在性判断）")
    print()
    return missing


def report_calls(client, image_b64: str, dish_names: list[str]) -> tuple[list[tuple], object]:
    """Actually call every backend method; return (rows, identify result)."""
    print("── 2. 用后端真实的 JevClient 实际调用 ──")
    rows: list[tuple[str, str, bool, float, str]] = []

    def call(label: str, required: str, fn) -> object:
        started = time.time()
        try:
            out = fn()
        except Exception as error:  # 契约不符时可能是 JevUnavailable，也可能是别的
            rows.append((label, required, False,
                         (time.time() - started) * 1000,
                         f"{type(error).__name__}: {str(error)[:100]}"))
            return None
        rows.append((label, required, out is not None,
                     (time.time() - started) * 1000, brief(out)))
        return out

    call("GET /health", "必需", client.health)
    call("POST /v1/menu", "必需", lambda: client.register_menu("集成自检（可删）", dish_names))
    identified = call("POST /v1/identify", "必需", lambda: client.identify(image_b64, dish_names))
    call("POST /v1/ask", "必需", lambda: client.ask("这盘菜还在供应吗？", ["在", "不在"]))
    call("POST /v1/portion", "后端在用", lambda: client.portion(image_b64, image_b64, "小馒头"))
    call("POST /v1/measure", "后端定义未调用",
         lambda: client.measure(image_b64, image_b64, image_b64))

    for label, required, ok, ms, detail in rows:
        print(f"  [{'OK ' if ok else 'FAIL'}] {label:<20} {required:<16} {ms:7.0f} ms  {detail}")
    print()
    return rows, identified


def report_recognition(identified: object, floor: float) -> None:
    """Print the identification result and whether it clears the confidence floor."""
    print("── 3. 认菜结果 ──")
    if not identified:
        print("  认菜失败，后端 check_binding() 会返回 skipped")
        print()
        return
    print(f"  识别为 : {identified.get('dish')}  置信度 {identified.get('confidence')}")
    print(f"  领先   : {identified.get('margin')}    abstained={identified.get('abstained')}")
    for row in (identified.get("ranking") or [])[:5]:
        print(f"    {row['option']:<10} {row['probability']:.4f}")
    confidence = float(identified.get("confidence") or 0)
    verdict = "可用" if confidence >= floor else f"低于下限 {floor}，后端会当作没认出来"
    print(f"  判定   : {verdict}")
    print()


def report_conclusion(health: object, missing: list[str], calls: dict[str, list[str]],
                      rows: list[tuple]) -> int:
    """Print the verdict and return the process exit code."""
    print("── 4. 结论 ──")
    if health:
        print(f"  服务器在线: {health.get('status')}  loaded={health.get('loaded')}  "
              f"显存 {health.get('vram_used_gib')}/{health.get('vram_total_gib')} GiB")
    if missing:
        print(f"  ⚠ 后端在用但服务器已移除的端点: {', '.join(missing)}")
        for endpoint in missing:
            print(f"    {endpoint}")
            for link in impact_chain(endpoint):
                print(f"      {link}")
    failed_required = [r for r in rows if r[1] == "必需" and not r[2]]
    if failed_required:
        print(f"  ✗ 必需端点失败 {len(failed_required)} 个")
        return 1
    if missing:
        print("  必需端点全部可用；上面列出的缺口只影响标注的那几处调用。")
        return 1
    print("  ✓ 后端用到的端点全部可用。")
    return 0


# ---------------------------------------------------------------- 编排

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="后端 ↔ Jev 服务器契约自检")
    parser.add_argument("--key", default=os.environ.get("JEV_API_KEY", ""),
                        help="API 密钥；默认读环境变量 JEV_API_KEY")
    parser.add_argument("--url", default=os.environ.get("JEV_BASE_URL", ""),
                        help="覆盖服务器地址；默认用后端 config.py 里的默认值")
    parser.add_argument("--image", default=str(DATASET / "images" / "B001.png"),
                        help="用于认菜自检的照片")
    return parser.parse_args()


def load_backend(args: argparse.Namespace):
    """Point sys.path at modules/backend and import its real client + config."""
    os.environ["JEV_API_KEY"] = args.key
    if args.url:
        os.environ["JEV_BASE_URL"] = args.url
    sys.path.insert(0, str(BACKEND))
    from app import config                      # noqa: PLC0415 —— 必须在改完环境变量之后
    from app.jev import JevClient               # noqa: PLC0415
    return config, JevClient


def main() -> int:
    # Windows 控制台默认 GBK，中文和箭头符号会直接抛 UnicodeEncodeError
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    if not args.key:
        print("✗ 没有密钥。请设置环境变量 JEV_API_KEY 后再跑（密钥不要提交到仓库）。")
        print("  例：JEV_API_KEY=jev_xxx python modules/integration/check_jev_contract.py")
        return 2

    config, JevClient = load_backend(args)
    calls = scan_backend_calls()
    routes = server_routes(config.JEV_BASE_URL)

    print("=" * 78)
    print("后端 ↔ Jev 服务器 契约自检")
    print("=" * 78)
    print(f"服务器   : {config.JEV_BASE_URL}")
    print(f"密钥     : {args.key[:8]}…（已隐藏）")
    print(f"自检照片 : {args.image}")
    print(f"后端并发 : {config.JEV_MAX_CONCURRENCY}   "
          f"认菜置信度下限: {config.IDENTIFY_CONFIDENCE_FLOOR}")
    print()

    missing = report_static(calls, routes)

    client = JevClient()
    if client.offline:
        print("✗ 客户端处于离线模式（JEV_API_KEY 为空）")
        return 1

    dish_names = [d["name"] for d in
                  json.loads((DATASET / "dish_catalog.json").read_text(encoding="utf-8"))]
    image_b64 = base64.b64encode(Path(args.image).read_bytes()).decode()
    rows, identified = report_calls(client, image_b64, dish_names)

    report_recognition(identified, config.IDENTIFY_CONFIDENCE_FLOOR)
    return report_conclusion(client.health(), missing, calls, rows)


if __name__ == "__main__":
    sys.exit(main())
