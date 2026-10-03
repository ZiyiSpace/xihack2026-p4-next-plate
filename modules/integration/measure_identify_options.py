#!/usr/bin/env python3
"""候选清单规模对认菜准确率的影响 —— 用本队自己的数据集实测。

为什么要有这个脚本
------------------
服务方的《使用说明》给了它自己测的曲线：8 道菜 100%、12 道 87.5%、40 道 75%，
并明确写「候选清单越短越准」「超过 20 个质量明显下降」。但那是维基百科照片测的，
不是我们的转盘俯拍图。

本脚本用 `hotpot_dataset_v0_2` 的 20 张图，按三种候选清单配置各跑一遍，
把「给模型多少选项」变成一个我们自己的数字，而不是靠感觉。

三种配置
--------
- `short`  2 项：只给这批数据里真正出现过的两道菜（小馒头、腌牛肉）
- `full`   6 项：给完整菜品目录（含本批没出现的南瓜饼、鱼丸、豆腐片、腌鸡肉）
- `none`   6 项 + `include_none`（多一个「以上都不是」选项）

判定口径
--------
真值来自 `image_manifest.json` 的 `prompt_key` 前缀（`bun_*`=小馒头、`beef_*`=腌牛肉），
这是团队出图时写死的，不是模型输出推出来的。

`prompt_key` 里的 `*_empty` 图是**空盘**（盘上没有任何菜）。对空盘问「这是哪道菜」
本身就是个畸形问题，所以分开统计：
- 有菜图：答对 = 识别出的菜 == 真值，且置信度 >= 下限
- 空盘图：答对 = 拒答（低于下限、abstained，或选了「以上都不是」）
  空盘上还能高分报出菜名，属于**编造**，不算对。

跑法
----
    python modules/integration/measure_identify_options.py
    python modules/integration/measure_identify_options.py --repeat 2   # 更稳但更慢

密钥从 `modules/backend/.env` 读，不写进仓库。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "modules" / "backend"
DEFAULT_DATASET = Path(r"D:\360安全浏览器下载\hotpot_dataset_v0_2")

ABSTAIN = "以上都不是"
FLOOR = 0.6


def load_env() -> tuple[str, str]:
    """从 modules/backend/.env 读地址与密钥，环境变量优先。"""
    base = os.environ.get("JEV_BASE_URL", "")
    key = os.environ.get("JEV_API_KEY", "")
    env_file = BACKEND / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name, value = name.strip(), value.strip()
            if name == "JEV_BASE_URL" and not base:
                base = value
            elif name == "JEV_API_KEY" and not key:
                key = value
    return base.rstrip("/"), key


def truth_of(prompt_key: str) -> tuple[str, bool]:
    """prompt_key -> (真值菜名, 是否空盘图)。"""
    empty = prompt_key.endswith("_empty")
    if prompt_key.startswith("bun_"):
        return "小馒头", empty
    if prompt_key.startswith("beef_"):
        return "腌牛肉", empty
    raise SystemExit(f"未知的 prompt_key：{prompt_key}")


def call_identify(client, base: str, key: str, image: Path, options: list[str],
                  include_none: bool, repeat: int) -> dict:
    """打 /v1/identify；返回服务端原始 JSON（失败时带 error 字段）。"""
    data = {"modality": "image", "repeat": str(repeat)}
    if include_none:
        data["include_none"] = "true"
    data["options"] = "\n".join(options)
    files = {"file": (image.name, image.read_bytes(), "image/png")}
    try:
        response = client.post("/v1/identify", files=files, data=data)
    except Exception as error:  # 网络/超时都记成一次失败，不中断整轮
        return {"error": f"{type(error).__name__}: {error}"}
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}: {response.text[:160]}"}
    return response.json()


def verdict(result: dict, floor: float) -> str:
    """把一次识别收敛成「菜名」或「拒答」。"""
    if result.get("error"):
        return "调用失败"
    if result.get("abstained"):
        return "拒答"
    name = result.get("dish")
    if name == ABSTAIN:
        return "拒答"
    if name is None or float(result.get("confidence") or 0) < floor:
        return "拒答"
    return str(name)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="候选清单规模 × 认菜准确率")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--repeat", type=int, default=1, help="服务端轮换选项顺序重复次数")
    parser.add_argument("--floor", type=float, default=FLOOR)
    parser.add_argument("--out", default="", help="结果 JSON 落盘路径")
    args = parser.parse_args()

    try:
        import httpx
    except ImportError:
        print("✗ 需要 httpx（backend 依赖里有）：pip install httpx")
        return 2

    base, key = load_env()
    if not key:
        print("✗ 没读到 JEV_API_KEY（modules/backend/.env）")
        return 2

    dataset = Path(args.dataset)
    manifest = json.loads((dataset / "image_manifest.json").read_text(encoding="utf-8"))
    catalog = [d["name"] for d in
               json.loads((dataset / "dish_catalog.json").read_text(encoding="utf-8"))]
    # 完整目录用后端库里那份（6 道菜），本批只用两道
    full_catalog = ["小馒头", "南瓜饼", "鱼丸", "豆腐片", "腌牛肉", "腌鸡肉"]

    configs = [
        ("short", catalog, False, f"2 项：只给本批出现的 {'/'.join(catalog)}"),
        ("full", full_catalog, False, f"{len(full_catalog)} 项：完整菜品目录"),
        ("none", full_catalog, True, f"{len(full_catalog)} 项 + 「{ABSTAIN}」"),
    ]

    print("=" * 74)
    print("候选清单规模 × 认菜准确率（本队 v0.2 数据集，20 张）")
    print("=" * 74)
    print(f"服务器     : {base}")
    print(f"置信度下限 : {args.floor}")
    print(f"repeat     : {args.repeat}")
    print()

    rows: list[dict] = []
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"},
                      timeout=120) as client:
        health = client.get("/health", timeout=15)
        print(f"服务器状态 : {health.text[:120]}\n")
        for tag, options, include_none, label in configs:
            print(f"── 配置 {tag}：{label} ──")
            for entry in manifest:
                sample, prompt_key = entry["sample_id"], entry["prompt_key"]
                truth, is_empty = truth_of(prompt_key)
                started = time.time()
                result = call_identify(client, base, key, dataset / "images" / f"{sample}.png",
                                       options, include_none, args.repeat)
                elapsed = time.time() - started
                answer = verdict(result, args.floor)
                ok = (answer == "拒答") if is_empty else (answer == truth)
                rows.append({
                    "config": tag, "sample_id": sample, "prompt_key": prompt_key,
                    "truth": truth, "empty": is_empty, "answer": answer,
                    "confidence": result.get("confidence"), "margin": result.get("margin"),
                    "abstained": result.get("abstained"),
                    "ranking": (result.get("ranking") or [])[:3],
                    "error": result.get("error"), "ok": ok, "seconds": round(elapsed, 2),
                })
                mark = "✓" if ok else "✗"
                detail = result.get("error") or (
                    f"{answer} conf={result.get('confidence')} margin={result.get('margin')}")
                print(f"  {mark} {sample} {prompt_key:<18} 真值 {truth:<4}"
                      f"{'（空盘）' if is_empty else '      '} -> {detail}   {elapsed:.1f}s")
            print()

    print("=" * 74)
    print("汇总")
    print("=" * 74)
    print(f"{'配置':<7} {'选项数':<7} {'有菜图':<10} {'空盘图':<10} {'合计':<10}")
    print("-" * 74)
    sizes = {"short": 2, "full": len(full_catalog), "none": len(full_catalog)}
    for tag, _options, _none, _label in configs:
        group = [r for r in rows if r["config"] == tag]
        food = [r for r in group if not r["empty"]]
        empty = [r for r in group if r["empty"]]
        f_ok, e_ok = sum(r["ok"] for r in food), sum(r["ok"] for r in empty)
        print(f"{tag:<7} {sizes[tag]:<7} {f_ok}/{len(food):<8} {e_ok}/{len(empty):<8} "
              f"{f_ok + e_ok}/{len(group):<8}")

    print()
    print("── 空盘图（盘上无菜）上模型的作答明细 ──")
    for r in rows:
        if r["empty"]:
            top = r["ranking"][0] if r["ranking"] else {}
            print(f"  [{r['config']:<5}] {r['sample_id']} {r['prompt_key']:<18} "
                  f"-> {r['answer']:<6} conf={r['confidence']} "
                  f"首选={top.get('option')} {top.get('probability')}")

    failures = [r for r in rows if r["error"]]
    if failures:
        print(f"\n⚠ 调用失败 {len(failures)} 次（结果已排除，不影响上面的分母口径说明）")

    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"\n明细已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
