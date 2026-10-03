#!/usr/bin/env python3
"""洪楚杰任务单第 4 条要求的场景测试：重复 / 乱序 / 缺失 / 补菜增重 / 换菜。

前置：后端已启动且已回放过数据集（P001 小馒头 149g / P002 腌牛肉 332g）。
用法：../.venv/bin/python scripts/scenario_tests.py [base_url]
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
PASS, FAIL = [], []


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        return {"_http": e.code, "_body": e.read().decode()[:200]}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}  {detail}")


def plates():
    return {p["plate_id"]: p for p in call("GET", "/api/plates")}


print("== 场景2 乱序：迟到旧事件 ==")
p2_before = plates()["P002"]
r = call("POST", "/api/events/station", {
    "event_id": "t-late", "plate_id": "P002", "station_id": "A",
    "observed_at": "2026-10-03T17:01:45+08:00",   # 早于已处理的 17:03:30
    "net_weight_g": 150})                          # 若被错误解释会记 182g 取用
p2_after = plates()["P002"]
cls = r.get("classification")
check("迟到旧事件被隔离", cls == "out_of_order", f"classification={cls}")
check("状态未被污染", p2_after["consumed_g"] == p2_before["consumed_g"]
      and p2_after["trusted_net_g"] == p2_before["trusted_net_g"],
      f"trusted {p2_before['trusted_net_g']}->{p2_after['trusted_net_g']}, "
      f"consumed {p2_before['consumed_g']}->{p2_after['consumed_g']}")

print("== 场景2b 完成标准：任务完成后旧事件不再次触发 ==")
ts_before = len([t for t in call("GET", "/api/tasks") if t["status"] == "pending"])
r = call("POST", "/api/events/station", {
    "event_id": "t-late2", "plate_id": "P002", "station_id": "C",
    "observed_at": "2026-10-03T17:02:20+08:00", "net_weight_g": 100})
ts_after = len([t for t in call("GET", "/api/tasks") if t["status"] == "pending"])
check("旧事件不触发新任务", r.get("classification") == "out_of_order" and ts_after == ts_before,
      f"pending {ts_before}->{ts_after}")

print("== 场景3 缺失：无称重事件 ==")
p1_before = plates()["P001"]
r = call("POST", "/api/events/station", {
    "event_id": "t-noweight", "plate_id": "P001", "station_id": "B",
    "observed_at": "2026-10-03T17:06:00+08:00"})   # 无 net/gross
p1_after = plates()["P001"]
cls = r.get("interpretation", {}).get("classification")
check("无称重只留痕", cls == "no_weight", f"classification={cls}")
check("状态不变（缺称不等于空盘）",
      p1_after["trusted_net_g"] == p1_before["trusted_net_g"])

print("== 场景4 补菜增重（有记录 / 无记录） ==")
r = call("POST", "/api/events/operation", {
    "timestamp": "2026-10-03T17:06:30+08:00", "plate_id": "P001",
    "op_type": "refill", "recorded_added_g": 100})
r = call("POST", "/api/events/station", {
    "event_id": "t-refill", "plate_id": "P001", "station_id": "C",
    "observed_at": "2026-10-03T17:07:00+08:00", "net_weight_g": 249})
cls = r.get("interpretation", {}).get("classification")
check("有记录 => refill_confirmed", cls == "refill_confirmed",
      f"classification={cls}, trusted={r.get('snapshot',{}).get('trusted_net_g')}")
r = call("POST", "/api/events/station", {
    "event_id": "t-spike", "plate_id": "P001", "station_id": "A",
    "observed_at": "2026-10-03T17:08:00+08:00", "net_weight_g": 420})
cls = r.get("interpretation", {}).get("classification")
check("无记录 => unexplained_increase（可信净重不更新）",
      cls == "unexplained_increase"
      and r["snapshot"]["trusted_net_g"] == 249,
      f"classification={cls}, trusted={r['snapshot']['trusted_net_g']}")

print("== 场景5 换菜：新批次不串历史 ==")
r = call("POST", "/api/events/operation", {
    "timestamp": "2026-10-03T17:09:00+08:00", "plate_id": "P001",
    "op_type": "rebind", "dish_id": "D02", "recorded_net_g": 220})
snap = r.get("snapshot", {})
check("换菜开启新 serving", snap.get("serving_id", "").startswith("P001_s")
      and snap.get("serving_id") != "P001_s01" and snap.get("dish_name") == "南瓜饼",
      f"{snap.get('serving_id')} / {snap.get('dish_name')}")
hist = call("GET", "/api/plates/P001/history")
sids = {e["serving_id"] for e in hist["events"]}
check("旧事件仍属旧批次", sids == {"P001_s01"}, f"serving 分布={sids}")

print("== 任务更新校验 ==")
tasks = call("GET", "/api/tasks?status=pending")
if tasks:
    tid = tasks[0]["task_id"]
    r = call("POST", f"/api/tasks/{tid}", {"quantity_g": -5})
    check("负数量被拒绝", r.get("_http") == 422, f"http={r.get('_http')}")
    r = call("POST", f"/api/tasks/{tid}", {"status": "hacked"})
    check("非法状态被拒绝", r.get("_http") == 422, f"http={r.get('_http')}")

print(f"\n结果：{len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
