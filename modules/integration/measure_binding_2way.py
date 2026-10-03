#!/usr/bin/env python3
"""2 项校验形态实测：把「6 道菜里选一个」换成「是不是这道菜」。

要回答的问题
------------
`check_binding` 现在在**已经知道绑定是哪道菜**的前提下，仍然把整份菜单（6 道）发给模型，
让它从 6 个里挑。而 `measure_option_order.py` 已实测：这个问法下 33% 的答案会因清单顺序
翻转，且第一项有加成。第七节又实测 2 项（两道真菜、无弃权项）→ 有菜图 14/14。

所以候选改法是 2 项校验。但**光测「能不能确认正确绑定」是不够的** —— 一个永远答「是」的
校验器敏感度满分、特异度为零，毫无用处。三条臂一起测才有意义：

  control  绑定=真值，选项 [真值, 诱饵]（**无弃权项**）-> 期望选真值。这是第七节
                                                        那个 14/14 的复现，也是
                                                        「弃权项偷走了多少」的基准。
  confirm  绑定=真值，选项 [真值, 以上都不是]         -> 期望选真值（**敏感度**）
  reject   绑定=另一道菜，选项 [绑定菜, 以上都不是]    -> 期望选「以上都不是」（**特异度**）

`reject` 臂里模型若选了那道**错的绑定菜**，就是最危险的情况：**假确认** ——
绑定错了却被告知「与绑定一致」。这个数单独报。

臂的顺序是特意的：control 最有信息量，先跑；服务器中途挂掉时先牺牲的是收益最小的那条。

调用失败绝不当作一种答案
------------------------
第一版把失败的调用（`dish` 为 None）和「选了别的」混在一起统计，于是服务器整个挂掉时
control 臂输出了「选绑定菜 0/60、选别的 60/60」，看起来像一个真结果 —— 这是最坏的一种
测量脚本缺陷。现在：调用失败单独计数，**连续失败超过阈值直接中止并退出码 3**，
不再继续产出看起来像数据的数字。

弃权项只能自己写进 `options`，不能用 `include_none`
---------------------------------------------------
服务端在追加「以上都不是」**之前**先校验 `options` 长度，只发 1 道菜会直接 422：

    {"detail":"candidates must contain between 2 and 256 entries"}

所以要凑成 2 项校验，只能把 `"以上都不是"` 当成普通候选写进列表。

跑法
----
    python modules/integration/measure_binding_2way.py --dataset D:/develop/hotpot_v1_0
    python modules/integration/measure_binding_2way.py --dataset ... --limit 4   # 先探
    python modules/integration/measure_binding_2way.py --dataset ... --max-fail 5

**用后端自己的 `app.jev.JevClient`**，密钥读 `modules/backend/.env`。

退出码：0 = 跑完；2 = 数据集读不到或密钥缺失；3 = 连续调用失败，已中止（结果不可用）。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "modules" / "backend"
sys.path.insert(0, str(BACKEND))

import app.config as config  # noqa: E402
from app.jev import JevClient, JevUnavailable  # noqa: E402

ABSTAIN = "以上都不是"
# control 先跑：它信息量最大，服务器中途挂掉时先牺牲收益最小的臂
ARMS = ("control", "confirm", "reject")


class RunAborted(Exception):
    """连续调用失败达到阈值，余下结果不可信。"""


def load_samples(dataset: Path) -> tuple[list[dict], list[str]]:
    catalog = json.loads((dataset / "dish_catalog.json").read_text(encoding="utf-8"))
    name_of = {d["dish_id"]: d["name"] for d in catalog}
    order = [name_of[k] for k in sorted(name_of)]

    out = []
    for line in (dataset / "observations.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        image = dataset / rec["image_path"] if rec.get("image_path") else None
        if not image or not image.exists():
            continue
        truth = name_of.get(rec.get("dish_id"))
        if truth not in order:
            continue
        # 诱饵取目录顺序里的下一道菜：确定性、可复现
        decoy = order[(order.index(truth) + 1) % len(order)]
        out.append({"sid": rec["sample_id"], "truth": truth, "decoy": decoy, "image": image})
    return out, order


def arm_options(sample: dict, arm: str) -> tuple[str, list[str]]:
    """返回 (这一臂声明的绑定菜, 发给模型的候选清单)。"""
    if arm == "reject":
        return sample["decoy"], [sample["decoy"], ABSTAIN]
    if arm == "confirm":
        return sample["truth"], [sample["truth"], ABSTAIN]
    return sample["truth"], [sample["truth"], sample["decoy"]]  # control 无弃权项


def run_arm(client: JevClient, samples: list[dict], arm: str, max_fail: int) -> list[dict]:
    """跑一条臂。返回 None 表示调用失败（不是「选了别的」）。"""
    out: list[dict] = []
    consecutive = 0
    for i, s in enumerate(samples, 1):
        bound, options = arm_options(s, arm)
        b64 = base64.b64encode(s["image"].read_bytes()).decode()
        try:
            r = client.identify(b64, options)
        except JevUnavailable as e:
            r = None
            print(f"  [{arm}] {s['sid']} 第 {consecutive + 1} 次连续失败：{str(e)[:80]}")
        if r is None:
            consecutive += 1
            if consecutive >= max_fail:
                raise RunAborted(
                    f"{arm} 臂连续 {consecutive} 次调用失败（最后一条 {s['sid']}），已中止。"
                    f"视觉服务器可能已经下线，结果不可用。")
            out.append({"sid": s["sid"], "truth": s["truth"], "bound": bound,
                        "decoy": s["decoy"], "dish": None, "confidence": None,
                        "picked_bound": False, "picked_abstain": False, "correct": False})
            continue
        consecutive = 0
        dish = r.get("dish")
        out.append({
            "sid": s["sid"], "truth": s["truth"], "bound": bound, "decoy": s["decoy"],
            "dish": dish, "confidence": r.get("confidence"),
            "picked_bound": dish == bound,
            "picked_abstain": dish == ABSTAIN,
            "correct": (dish == bound) if arm in ("confirm", "control")
                       else (dish == ABSTAIN),
        })
        if i % 10 == 0:
            print(f"  [{arm}] {i}/{len(samples)}")
    return out


def summarize(arm: str, rows: list[dict]) -> None:
    """**只在有效调用上算比例**；失败单独报，绝不折进答案分布。"""
    ok = [r for r in rows if r["dish"]]
    failed = len(rows) - len(ok)
    print(f"\n--- {arm}（有效 {len(ok)}/{len(rows)}，失败 {failed}）---")
    if not ok:
        print("  无有效数据，不能得出任何结论")
        return
    picked_bound = sum(r["picked_bound"] for r in ok)
    picked_abstain = sum(r["picked_abstain"] for r in ok)
    other = len(ok) - picked_bound - picked_abstain
    print(f"  选「绑定菜」    {picked_bound}/{len(ok)}")
    print(f"  选「以上都不是」 {picked_abstain}/{len(ok)}")
    print(f"  选别的          {other}/{len(ok)}")
    if arm == "confirm":
        print(f"  => 敏感度（正确绑定被判一致）：{picked_bound}/{len(ok)}"
              f"   误弃权 {picked_abstain}/{len(ok)}")
    elif arm == "reject":
        print(f"  => 特异度（错误绑定被拒）：{picked_abstain}/{len(ok)}")
        print(f"  => **假确认**（绑定错了却说一致）：{picked_bound}/{len(ok)}")
    else:
        print(f"  => 无弃权项时的正确率：{picked_bound}/{len(ok)}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="2 项校验形态实测")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-fail", type=int, default=3,
                    help="连续失败达到这个数就中止（默认 3）")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    dataset = Path(args.dataset)
    if not dataset.is_dir():
        raise SystemExit(f"数据集目录不存在：{dataset}")
    if not config.JEV_API_KEY:
        raise SystemExit("JEV_API_KEY 为空（modules/backend/.env 里配了吗？）")

    samples, order = load_samples(dataset)
    if args.limit:
        samples = samples[:args.limit]
    client = JevClient()

    # 起飞前先确认服务器在。不在就直接退，不要跑出 60 条“答案”。
    health = client.health()
    print(f"数据集 {dataset}：{len(samples)} 张样本，菜品 {len(order)} 道")
    print(f"服务器 {config.JEV_BASE_URL}   健康：{health}")
    if not health or health.get("status") != "ok":
        print("视觉服务器不健康，先把它起起来再跑。", file=sys.stderr)
        return 3

    results: dict[str, list[dict]] = {}
    try:
        for arm in ARMS:
            print(f"\n[{arm}]")
            results[arm] = run_arm(client, samples, arm, args.max_fail)
    except RunAborted as e:
        print(f"\n中止：{e}", file=sys.stderr)
        if args.json:  # 半途数据照样落盘，但带了中止标记
            Path(args.json).write_text(json.dumps(
                {"aborted": str(e), **results}, ensure_ascii=False, indent=2), encoding="utf-8")
        return 3

    for arm in ARMS:
        summarize(arm, results[arm])

    print("\n=== reject 臂里被假确认的样本 ===")
    hits = [r for r in results["reject"] if r["picked_bound"]]
    for r in hits:
        print(f"  {r['sid']:<6} 真值={r['truth']:<8} 绑定={r['bound']:<8} "
              f"模型仍说={r['dish']:<8} conf={r['confidence']}")
    if not hits:
        print("  无")

    print("\n=== 逐菜：敏感度 / 特异度 / 对照 ===")
    for name in order:
        cf = [r for r in results["confirm"] if r["truth"] == name and r["dish"]]
        rj = [r for r in results["reject"] if r["truth"] == name and r["dish"]]
        ct = [r for r in results["control"] if r["truth"] == name and r["dish"]]
        if not cf and not ct:
            continue
        print(f"  {name:<8} 确认 {sum(r['picked_bound'] for r in cf)}/{len(cf)}   "
              f"拒绝 {sum(r['picked_abstain'] for r in rj)}/{len(rj)}   "
              f"对照 {sum(r['picked_bound'] for r in ct)}/{len(ct)}")

    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n原始结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
