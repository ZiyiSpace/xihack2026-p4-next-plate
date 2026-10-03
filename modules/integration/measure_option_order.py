#!/usr/bin/env python3
"""选项顺序敏感性实测：同一批图跑两遍，只把候选清单倒过来。

要回答的问题
------------
v1.0 回放里 60 帧的认菜只有 32 帧对，而且错得很有方向：**错 28 次里有 10 次倒向「小馒头」**，
其中 7 次是「鱼丸」整段被认成小馒头（置信度 0.978–0.998）。而 `vision.menu_names()`
是 `ORDER BY dish_id`，小馒头（D01）**永远是第一个选项**。

所以有两种解释，必须分开：
  A. **菜驱动**：模型确实分不开鱼丸和小馒头（两者都是白色一口大小），换个位置照样错。
  B. **位置驱动**：模型有位置偏置，往清单第一项倒；倒过来它就改倒向新的第一项。

做法
----
同一批图、同一份候选清单，只改顺序跑两遍（正序 / 倒序），然后看每个样本的答案
**跟着菜走还是跟着位置走**。两种解释给出的数字完全不同：
  A => 倒序后「小馒头」占比大致不变，逐样本答案基本一致。
  B => 倒序后「腌鸡肉」（新的第一项）占比顶上，「小馒头」掉下去。

为什么不用 `/v1/ask` 问开放式的「这是什么菜」
---------------------------------------------
服务端没有开放生成口子：`/v1/identify` 和 `/v1/ask` 底下都走 `/v1/decide`，
而 `/v1/ask` 的 `options` 也是 2–256 个候选（见《使用说明》/v1/ask 参数表）。
所以「不给候选、让它自由作答」在当前服务器上做不到；能调的只有候选清单的
**规模**和**顺序**。本脚本测顺序，规模见 `measure_identify_options.py`。

跑法
----
    python modules/integration/measure_option_order.py --dataset D:/develop/hotpot_v1_0
    python modules/integration/measure_option_order.py --dataset ... --limit 12   # 先探

**用后端自己的 `app.jev.JevClient`**（不是 curl），和本模块其余脚本一致：
重试、超时、离线降级都走真实路径。密钥读 `modules/backend/.env`。

退出码：0 = 跑完；2 = 数据集读不到或密钥缺失。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "modules" / "backend"
sys.path.insert(0, str(BACKEND))

import app.config as config  # noqa: E402  （import 时会把 modules/backend/.env 读进环境）
from app.jev import JevClient, JevUnavailable  # noqa: E402


def load_dataset(dataset: Path) -> tuple[list[dict], list[str]]:
    """返回 (样本, 菜品名清单)。菜品清单用数据集自己的目录顺序（D01..D06）。"""
    catalog = json.loads((dataset / "dish_catalog.json").read_text(encoding="utf-8"))
    name_of = {d["dish_id"]: d["name"] for d in catalog}
    order = [name_of[k] for k in sorted(name_of)]

    samples = []
    for line in (dataset / "observations.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        image = dataset / rec["image_path"] if rec.get("image_path") else None
        if not image or not image.exists():
            continue
        samples.append({"sid": rec["sample_id"], "truth": name_of.get(rec.get("dish_id")),
                        "image": image})
    return samples, order


def run_arm(client: JevClient, samples: list[dict], options: list[str],
            label: str) -> list[dict]:
    """跑一遍候选清单，返回每个样本的答案与其在清单里的位置。"""
    out = []
    for i, s in enumerate(samples, 1):
        b64 = base64.b64encode(s["image"].read_bytes()).decode()
        try:
            r = client.identify(b64, options) or {}
        except JevUnavailable as e:
            print(f"  [{label}] {s['sid']} 调用失败：{e}")
            r = {}
        dish = r.get("dish")
        out.append({
            "sid": s["sid"], "truth": s["truth"], "dish": dish,
            "confidence": r.get("confidence"),
            "index": options.index(dish) if dish in options else None,
            "correct": dish == s["truth"],
        })
        if i % 10 == 0:
            print(f"  [{label}] {i}/{len(samples)}")
    return out


def summarize(arm: list[dict], options: list[str], label: str) -> None:
    n = len(arm)
    right = sum(a["correct"] for a in arm)
    print(f"\n--- {label}（清单：{' / '.join(options)}）---")
    print(f"  正确 {right}/{n}")
    picks = Counter(a["dish"] for a in arm)
    print("  答案分布：" + "  ".join(f"{k}×{v}" for k, v in picks.most_common()))
    first = options[0]
    print(f"  落在第一项「{first}」上：{picks.get(first, 0)}/{n}"
          f"（其中答对 {sum(1 for a in arm if a['correct'] and a['dish'] == first)}）")


def compare(a: list[dict], b: list[dict], order_a: list[str], order_b: list[str]) -> None:
    """逐样本对比两臂：答案是跟着菜走，还是跟着位置走。"""
    by_b = {x["sid"]: x for x in b}
    same_dish = same_index = both = 0
    print("\n=== 逐样本：菜驱动 还是 位置驱动 ===")
    for x in a:
        y = by_b.get(x["sid"])
        if not y:
            continue
        both += 1
        if x["dish"] == y["dish"]:
            same_dish += 1
        if x["index"] is not None and x["index"] == y["index"]:
            same_index += 1
    print(f"  两臂答案相同（同菜名）：{same_dish}/{both}")
    print(f"  两臂位置相同（同一个序号）：{same_index}/{both}")
    print("\n  判读：若「位置相同」明显高于「菜名相同」，说明模型主要在挑位置而不是挑菜。")

    print("\n  两臂答案不同的样本：")
    for x in a:
        y = by_b.get(x["sid"])
        if y and x["dish"] != y["dish"]:
            print(f"    {x['sid']:<6} 真值={str(x['truth']):<8} "
                  f"正序→{str(x['dish']):<8}(#{x['index']})  "
                  f"倒序→{str(y['dish']):<8}(#{y['index']})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="候选清单顺序敏感性实测")
    parser.add_argument("--dataset", required=True, help="数据集目录")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个样本（0=全部）")
    parser.add_argument("--json", default="", help="把原始结果落盘成 JSON")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    dataset = Path(args.dataset)
    if not dataset.is_dir():
        raise SystemExit(f"数据集目录不存在：{dataset}")
    if not config.JEV_API_KEY:
        raise SystemExit("JEV_API_KEY 为空（modules/backend/.env 里配了吗？）")

    samples, order = load_dataset(dataset)
    if args.limit:
        samples = samples[:args.limit]
    print(f"数据集 {dataset}：{len(samples)} 张带图样本，菜品 {len(order)} 道")
    print(f"视觉服务器：{config.JEV_BASE_URL}")

    client = JevClient()
    reversed_order = list(reversed(order))
    print("\n[正序]")
    arm_a = run_arm(client, samples, order, "正序")
    print("[倒序]")
    arm_b = run_arm(client, samples, reversed_order, "倒序")

    summarize(arm_a, order, "正序")
    summarize(arm_b, reversed_order, "倒序")
    compare(arm_a, arm_b, order, reversed_order)

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"正序": arm_a, "倒序": arm_b, "正序清单": order, "倒序清单": reversed_order},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n原始结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
