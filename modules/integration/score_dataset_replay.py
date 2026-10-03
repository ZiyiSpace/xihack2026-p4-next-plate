#!/usr/bin/env python3
"""数据集回放对评：把后端已落库的判读与数据集自己的 answer_key 比一遍。

和 `run_online_replay.py` 的区别
--------------------------------
- `run_online_replay.py`：**驱动**一次回放（自己起后端、看链路通不通、报耗时）。
- 本脚本：**事后对账**，只读数据库。它不关心回放怎么跑的，只问「后端记的账和
  数据集自己声明的答案差在哪」。所以可以先跑回放、随时重跑对评，不必重放数据集。

真值来源
--------
`answer_key.jsonl` 的 `expected_event`（缺失时退回 `event_since_previous_observation`）。
**不从后端输出反推**——反推的话任何输出都能自圆其说，对评就没有意义了。

两档判定
--------
`label_ok`    标签等价：判读名落在该期望事件的等价集合里。
`account_ok`  记账等价：在标签等价之外，再承认数据集画得比我们细的那一档
              （见 `ACCOUNTING_EQUIV`）。两档分开报，不合并成一个笼统的「准确率」。

跑法
----
    python modules/integration/score_dataset_replay.py --dataset D:/develop/hotpot_v1_0
    python modules/integration/score_dataset_replay.py --dataset ... --json out.json

退出码：0 = 无记账级不一致；1 = 有记账级不一致；2 = 数据集或数据库读不到。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_DB = REPO / "modules" / "backend" / "data" / "hotpot.db"

# 期望事件 -> 我们认得的判读。
#
# no_change 收了四个标签：这四条路径的**记账结果完全一致**（可信净重不动、不记取用），
# 区别只在「这一帧有没有值得说的异常」。数据集只声明「账上不该有变化」，没给异常留字段，
# 所以 `unexplained_increase` / `unexplained_decrease` 在这一档里算等价。
# 两者必须同时在场：它们是对称的挂起路径，只收一个就是漏。
LABEL_EQUIV = {
    "initial_load": {"baseline"},
    "removal": {"removal"},
    "no_change": {"no_change", "anomaly_resolved",
                  "unexplained_increase", "unexplained_decrease"},
    "refill": {"refill_confirmed", "late_refill"},
}

# 记账等价：数据集区分「取用」与「取用并把菜在盘里摊开」，我们只按净重减少记一笔取用。
# 取用量（-Δg）两边完全相同，差别是数据集多标了一个我们没建模的动作，账上无差。
ACCOUNTING_EQUIV = {
    "removal_and_spread": {"removal"},
}


def load_keys(dataset: Path) -> dict[str, dict]:
    """读 answer_key.jsonl，按 sample_id 索引。"""
    path = dataset / "answer_key.jsonl"
    if not path.exists():
        raise SystemExit(f"数据集里没有 answer_key.jsonl：{path}")
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["sample_id"]] = rec
    return out


def load_events(db_path: Path) -> list[dict]:
    """读后端落库的站点事件（只需样本 id、净重、判读）。"""
    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}（回放跑过了吗？）")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT event_id, net_weight_g, interpretation FROM station_events "
            "ORDER BY observed_at").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def pair_events(events: list[dict], keys: dict[str, dict]) -> list[dict]:
    """把落库事件和 answer_key 配对；数据集里没有的事件（如补菜操作）跳过。"""
    pairs = []
    for row in events:
        key = keys.get(row["event_id"])
        if not key:
            continue
        interp = json.loads(row["interpretation"]) if row["interpretation"] else {}
        got = interp.get("classification")
        exp = key.get("expected_event") or key.get("event_since_previous_observation")
        pairs.append({
            "sid": row["event_id"], "exp": exp, "got": got,
            "sensor": key.get("sensor_condition"), "food": key.get("food_state"),
            "net": row["net_weight_g"],
            "label_ok": got in LABEL_EQUIV.get(exp, set()),
            "account_ok": (got in LABEL_EQUIV.get(exp, set())
                           or got in ACCOUNTING_EQUIV.get(exp, set())),
        })
    return pairs


def tally(pairs: list[dict], field: str, key: str) -> None:
    """按某个维度分组打印通过数。"""
    values = sorted({p[key] for p in pairs}, key=lambda x: str(x))
    for value in values:
        group = [p for p in pairs if p[key] == value]
        print(f"  {str(value):<18} {sum(p[field] for p in group)}/{len(group)}")


def render(pairs: list[dict], keys: dict[str, dict], db_path: Path) -> dict:
    """打印对评报告并返回同样内容的字典，供 --json 落盘。"""
    report = {
        "数据库": str(db_path),
        "数据集样本": len(keys),
        "可比对": len(pairs),
        "标签等价": sum(p["label_ok"] for p in pairs),
        "记账等价": sum(p["account_ok"] for p in pairs),
    }
    print(f"可比对 {len(pairs)} / {len(keys)} 条（其余样本 id 不在库里）")
    print()

    print("=== 按期望事件（标签等价）===")
    for exp in sorted({p["exp"] for p in pairs}, key=lambda x: str(x)):
        group = [p for p in pairs if p["exp"] == exp]
        print(f"  {str(exp):<18} {sum(p['label_ok'] for p in group)}/{len(group)}")
    print()

    print("=== 按称重条件 ===")
    tally(pairs, "label_ok", "sensor")
    print()

    print("=== 按食物状态 ===")
    tally(pairs, "label_ok", "food")
    print()

    print("=== 判读分布（我们） ===")
    print(" ", dict(Counter(p["got"] for p in pairs)))
    print("=== 判读分布（数据集期望） ===")
    print(" ", dict(Counter(p["exp"] for p in pairs)))
    print()

    label_bad = [p for p in pairs if not p["label_ok"]]
    account_bad = [p for p in pairs if not p["account_ok"]]
    report["标签不一致"] = [p["sid"] for p in label_bad]
    report["记账不一致"] = [p["sid"] for p in account_bad]

    print(f"=== 标签不一致 {len(label_bad)} 条 ===")
    for p in label_bad:
        print(f"  {p['sid']:<6} 期望={str(p['exp']):<20} 得到={str(p['got']):<22} "
              f"称重={str(p['sensor']):<18} 食物={str(p['food']):<12} 净重={p['net']}")
    if account_bad:
        print()
        print(f"=== 记账不一致 {len(account_bad)} 条（真正影响账目的）===")
        for p in account_bad:
            print(f"  {p['sid']:<6} 期望={str(p['exp']):<20} 得到={str(p['got']):<22} "
                  f"净重={p['net']}")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="数据集回放对评（只读数据库）")
    parser.add_argument("--dataset", required=True, help="数据集目录（内含 answer_key.jsonl）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="后端 SQLite 路径")
    parser.add_argument("--json", default="", help="把报告另存为 JSON")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    dataset = Path(args.dataset)
    if not dataset.is_dir():
        raise SystemExit(f"数据集目录不存在：{dataset}")
    db_path = Path(args.db)

    keys = load_keys(dataset)
    pairs = pair_events(load_events(db_path), keys)
    if not pairs:
        raise SystemExit("没有可比对的事件：数据库里没有 answer_key 认得的样本 id")
    report = render(pairs, keys, db_path)

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入 {args.json}")

    if report["记账不一致"]:
        print(f"\n结论：{len(report['记账不一致'])} 条记账级不一致，需要修。")
        return 1
    print(f"\n结论：记账级 0 条不一致"
          f"（标签等价 {report['标签等价']}/{report['可比对']}）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
