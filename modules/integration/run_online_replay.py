#!/usr/bin/env python3
"""端到端联调演练：起后端 → 在线模式回放数据集 → 报对评结果与耗时。

和 `check_jev_contract.py` 的区别
--------------------------------
- `check_jev_contract.py`：**单端点**能不能通（HTTP 层面）。
- 本脚本：**整条业务链路**跑不跑得通（后端 → jev.py → Jev 服务器 → 事件解释 → 补菜任务）。

跑法
----
    JEV_API_KEY=jev_xxxx python modules/integration/run_online_replay.py
    JEV_API_KEY=... python modules/integration/run_online_replay.py --mode offline   # 对照

退出码：0 = 回放完成且对评满分；1 = 有失败；2 = 没给密钥（online 模式）。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "modules" / "backend"


# ---------------------------------------------------------------- HTTP 小工具

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http(url: str, method: str = "GET", timeout: float = 30.0):
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------- 起后端

def child_env(args: argparse.Namespace) -> dict:
    """Environment for the backend subprocess."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # 后端的 open() 没有指定 encoding，Windows 默认 GBK，读 UTF-8 的
    # dish_catalog.json 会 UnicodeDecodeError。开启 UTF-8 模式绕过，
    # 不去改别人的代码；正确修法是给那些 open() 加 encoding="utf-8"。
    env["PYTHONUTF8"] = "1"
    if args.url:
        env["JEV_BASE_URL"] = args.url
    if args.key:
        env["JEV_API_KEY"] = args.key
    return env


def start_backend(env: dict, port: int) -> subprocess.Popen:
    """Launch uvicorn on `port`; the caller owns terminate()."""
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=str(BACKEND), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )


def wait_health(base: str, process: subprocess.Popen, limit_s: float = 90.0) -> bool:
    deadline = time.time() + limit_s
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            http(base + "/api/health", timeout=5)
            return True
        except Exception:
            time.sleep(0.7)
    return False


# ---------------------------------------------------------------- 跑回放

def run_replay(base: str, mode: str, limit_s: float) -> tuple[dict, float]:
    """POST the replay, poll until it stops, return (status, elapsed seconds)."""
    http(base + f"/api/replay?mode={mode}&reset=true", method="POST")
    started = time.time()
    interactive = sys.stdout.isatty()
    status: dict = {}
    while time.time() - started < limit_s:
        status = http(base + "/api/replay/status")
        if not status.get("running"):
            break
        # 只在交互终端刷进度；重定向到文件时刷 \r 会留下一堆垃圾
        if interactive:
            print(f"\r  回放中… {status.get('progress')}/{status.get('total')}",
                  end="", flush=True)
        time.sleep(0.5)
    if interactive:
        print()
    return status, time.time() - started


# ---------------------------------------------------------------- 渲染

def report_replay(status: dict, elapsed: float) -> None:
    print(f"回放耗时 : {elapsed:.1f}s")
    report = status.get("report") or {}
    print(f"对评     : {report.get('score')}/{report.get('total')}")
    print()
    print("── 回放日志 ──")
    for line in status.get("log") or []:
        print(f"  {line}")
    print()


def report_vision_lines(status: dict) -> None:
    print("── 与视觉服务器有关的行 ──")
    lines = [l for l in (status.get("log") or [])
             if "校验" in l or "交叉验证" in l or "视觉" in l or "Jev" in l]
    if lines:
        for line in lines:
            print(f"  {line}")
    else:
        print("  （无：本模式没走到视觉调用，或都被降级跳过了）")
    print()


def report_binding_checks(base: str) -> None:
    """Print what the backend actually got back from Jev for each event."""
    print("── 视觉绑定校验（认菜）实际结果 ──")
    try:
        events = http(base + "/api/events?limit=50", timeout=30)
    except Exception as error:  # noqa: BLE001 —— 读不到不影响回放结论
        print(f"  （读取 /api/events 失败: {error}）")
        print()
        return
    checked = 0
    for event in events if isinstance(events, list) else []:
        check = (event.get("interpretation") or {}).get("binding_check")
        if not check:
            continue
        checked += 1
        event_id = event.get("event_id")
        if check.get("skipped"):
            print(f"  {event_id:<10} 跳过   {str(check.get('note', ''))[:60]}")
        else:
            mark = "✓" if check.get("ok") else "✗"
            floor = "  (置信度低于下限)" if check.get("below_floor") else ""
            print(f"  {event_id:<10} {mark}  认出「{check.get('recognized')}」"
                  f"  期望「{check.get('expected')}」  conf={check.get('confidence')}{floor}")
    if not checked:
        print("  （没有任何事件带 binding_check：后端没走到认菜，或全部跳过）")
    print()


def summarize(report: dict) -> int:
    total, score = report.get("total") or 0, report.get("score") or 0
    if total and score == total:
        print(f"✓ 对评满分 {score}/{total}")
        return 0
    print(f"✗ 对评 {score}/{total}，未满分")
    return 1


# ---------------------------------------------------------------- 编排

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="后端 ↔ Jev 端到端回放演练")
    parser.add_argument("--key", default=os.environ.get("JEV_API_KEY", ""))
    parser.add_argument("--url", default=os.environ.get("JEV_BASE_URL", ""))
    parser.add_argument("--mode", default="online", choices=("online", "offline"))
    parser.add_argument("--timeout", type=float, default=420.0)
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    if args.mode == "online" and not args.key:
        print("✗ online 模式需要 JEV_API_KEY（密钥不要提交到仓库）")
        print("  例：JEV_API_KEY=jev_xxx python modules/integration/run_online_replay.py")
        return 2

    env = child_env(args)
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    print("=" * 78)
    print("后端 ↔ Jev 端到端回放演练")
    print("=" * 78)
    print(f"模式     : {args.mode}")
    print(f"后端     : {base}")
    print(f"视觉服务器: {env.get('JEV_BASE_URL', '（用 config.py 默认值）')}")
    print(f"密钥     : {'已设置 ' + args.key[:8] + '…' if args.key else '未设置（离线规则模式）'}")
    print()

    process = start_backend(env, port)
    started = time.time()
    try:
        if not wait_health(base, process):
            print("✗ 后端启动失败，输出如下：")
            print(process.stdout.read() if process.stdout else "")
            return 1
        print(f"后端就绪，用时 {time.time() - started:.1f}s")
        print()

        status, elapsed = run_replay(base, args.mode, args.timeout)
        report_replay(status, elapsed)

        if status.get("error"):
            print(f"✗ 回放报错: {status['error']}")
            return 1

        report_vision_lines(status)
        report_binding_checks(base)
        return summarize(status.get("report") or {})
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    sys.exit(main())
