"""虚拟采集端：没有硬件时，把预生成的数据集当成「刚拍到的数据」推给后端。

它模拟的是相机/称重网关这一侧，只通过 HTTP 与后端真实接口交互，不 import 后端
任何代码——这样它既能指向本机，也能指向别人的部署，换成真硬件时直接整体替换。

    python capture.py seed     把数据集图片放进后端图片存储（幂等）
    python capture.py run      按时间轴把站点事件、操作记录、客流事件发往后端
    python capture.py check    回读后端，逐项确认数据真的进去了

后端地址默认 http://127.0.0.1:8000，可用 --backend 或环境变量 CAPTURE_BACKEND 覆盖。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scenario  # noqa: E402  —— 放在 sys.path 调整之后
from client import BackendClient, BackendError  # noqa: E402

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BACKEND = os.environ.get("CAPTURE_BACKEND", "http://127.0.0.1:8000")
DEFAULT_DATASET = os.path.join(os.path.dirname(MODULE_DIR), "hotpot_dataset_v0_1")
DEFAULT_CROWD = os.path.join(MODULE_DIR, "data", "crowd_evening.json")
EVIDENCE_DIR = os.path.join(MODULE_DIR, "evidence")

# 同一时刻多条事件的上报顺序：先开档/补菜，再报观测，最后记客流
_KIND_ORDER = {"operation": 0, "station": 1, "covers": 2}


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (BackendError, OSError, ValueError) as e:
        print(f"[采集端] 失败：{e}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capture.py", description="虚拟采集端：把数据集当成实时数据推给后端")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--backend", default=DEFAULT_BACKEND, help="后端根地址")
        p.add_argument("--dataset", default=DEFAULT_DATASET, help="数据集目录")
        p.add_argument("--timeout", type=float, default=180.0, help="单请求超时秒数")

    p_seed = sub.add_parser("seed", help="把数据集图片放进后端图片存储")
    common(p_seed)
    p_seed.set_defaults(handler=cmd_seed)

    p_run = sub.add_parser("run", help="按时间轴把数据推给后端")
    common(p_run)
    p_run.add_argument("--crowd", default=DEFAULT_CROWD, help="客流画像文件")
    p_run.add_argument("--speed", type=float, default=30.0,
                       help="站点事件回放倍速（默认 30，即数据集 3 分半压到约 7 秒）")
    p_run.add_argument("--shift", choices=("now", "none"), default="now",
                       help="把整条时间轴挪到现在（默认）还是保持数据集原始时间戳")
    p_run.add_argument("--no-covers", action="store_true", help="不发客流事件")
    p_run.add_argument("--no-images", action="store_true",
                       help="只发图片引用、不发图片字节（跳过视觉校验）")
    p_run.add_argument("--keep", action="store_true", help="不清空既有数据（默认先复位）")
    p_run.add_argument("--verbose", action="store_true", help="逐条打印客流事件（默认每 25 桌一行）")
    p_run.add_argument("--report", default=os.path.join(EVIDENCE_DIR, "capture_report.json"),
                       help="运行报告写入路径")
    p_run.set_defaults(handler=cmd_run)

    p_check = sub.add_parser("check", help="回读后端确认数据进去了")
    common(p_check)
    p_check.set_defaults(handler=cmd_check)
    return parser


# ---------------------------------------------------------------- seed


def cmd_seed(args: argparse.Namespace) -> int:
    client = BackendClient(args.backend, args.timeout)
    stream = scenario.load_station_stream(args.dataset)
    before = {i["name"] for i in client.list_images()["images"]}
    refs = _seed_images(client, stream)
    names = set(refs)
    print(f"[采集端] 后端 {args.backend} 图片存储共 {client.list_images()['count']} 张；"
          f"本批 {len(names)} 张中新增 {len(names - before)} 张、复用 {len(names & before)} 张")
    for name, ref in sorted(refs.items()):
        print(f"  {name:12s} -> {ref}")
    return 0


def _seed_images(client: BackendClient, stream: scenario.Stream) -> dict[str, str]:
    """把采集流用到的图片放进后端存储，返回 存储名 -> image_ref。

    存储名沿用数据集文件名，内容相同时后端幂等复用，重复跑不会堆垃圾。
    """
    refs: dict[str, str] = {}
    for name, path in sorted(scenario.image_files(stream).items()):
        with open(path, "rb") as f:
            refs[name] = client.upload_image(name, f.read())["image_ref"]
    return refs


# ---------------------------------------------------------------- run


def cmd_run(args: argparse.Namespace) -> int:
    client = BackendClient(args.backend, args.timeout)
    health = client.health()
    print(f"[采集端] 后端 {args.backend} 在线，模式 {health.get('mode')}，"
          f"视觉服务器 {health.get('jev', {}).get('status')}")

    if not args.keep:
        client.reset()
        print("[采集端] 已复位后端业务数据（菜品配置保留）")

    stream = scenario.load_station_stream(args.dataset)
    refs = _seed_images(client, stream)
    print(f"[采集端] {len(refs)} 张图已放进存储 {args.backend}/api/images")

    streams = [stream]
    if not args.no_covers:
        streams.append(scenario.load_crowd_stream(args.crowd))
    if args.shift == "now":
        delta = datetime.now().astimezone().replace(microsecond=0) - stream.start
        streams = [scenario.shift(s, delta) for s in streams]
        stream = streams[0]
        print(f"[采集端] 时间轴整体平移到现在（+{delta}），"
              f"否则后端会按真实当前时间把每一盘都算成滞留")
    duration = stream.span_s / args.speed
    timeline = _merge([(off, s) for st in streams for off, s in st.offsets(duration)])
    counts = {k: len([1 for _, s in timeline if s.kind == k]) for k in ("operation", "station", "covers")}
    print(f"[采集端] 共 {len(timeline)} 条事件"
          f"（观测 {counts['station']} / 操作 {counts['operation']} / 客流 {counts['covers']}），"
          f"站点流窗口 {stream.span_s:.0f}s 按 ×{args.speed:g} 压到 {duration:.1f}s，"
          f"客流曲线同步铺满同一时长")
    if duration < 15:
        print(f"[采集端] 提示：本次会话约 {duration:.0f} 秒，工作台 5 秒轮询可能只看到最后一帧；"
              f"演示建议 --speed 6（约 {stream.span_s / 6:.0f} 秒）")

    result = _play(client, timeline, refs, with_image=not args.no_images, verbose=args.verbose)
    _write_report(args, result)
    return 0 if not result["failed"] else 1


def _play(client: BackendClient, timeline: list[tuple[float, scenario.Step]],
          refs: dict[str, str], with_image: bool, verbose: bool) -> dict:
    """按偏移睡眠后逐条上报；单条失败只记不中断，避免一条脏数据毁掉整场演示。"""
    started = time.monotonic()
    sent = {"station": 0, "operation": 0, "covers": 0}
    failed: list[dict] = []
    classifications: dict[str, int] = {}
    tasks_created = 0

    for offset, step in timeline:
        _sleep_until(started, offset)
        payload = dict(step.payload)
        try:
            if step.kind == "station":
                payload = _with_image(payload, step, refs, with_image)
                r = client.station_event(payload)
                sent["station"] += 1
                cls = (r.get("interpretation") or {}).get("classification") or r.get("classification")
                classifications[cls] = classifications.get(cls, 0) + 1
                tasks_created += len((r.get("new_tasks") or {}).get("created") or [])
                note = (r.get("interpretation") or {}).get("note") or r.get("note") or ""
                _line(offset, f"观测 {payload['event_id']} {step.label} => {cls} {note}")
            elif step.kind == "operation":
                r = client.operation(payload)
                sent["operation"] += 1
                _line(offset, f"操作 {payload['op_id']} {step.label} => {'ok' if 'error' not in r else r['error']}")
            else:
                client.covers(payload)
                sent["covers"] += 1
                if verbose:
                    _line(offset, f"客流 {step.label} @ {payload['timestamp'][11:16]}")
                elif sent["covers"] % 25 == 0:
                    _line(offset, f"客流 … 已发 {sent['covers']} 桌")
        except BackendError as e:
            failed.append({"at": offset, "kind": step.kind, "label": step.label,
                           "status": e.status, "error": str(e)[:300]})
            _line(offset, f"!! {step.label} 失败 HTTP {e.status}")

    elapsed = time.monotonic() - started
    state = client.state()
    return {
        "backend": client.base_url,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_s": round(elapsed, 1),
        "sent": sent, "failed": failed, "classifications": classifications,
        "refill_tasks_created": tasks_created,
        "images_in_store": client.list_images()["count"],
        "readback": _readback(state),
    }


def _with_image(payload: dict, step: scenario.Step, refs: dict[str, str],
                with_image: bool) -> dict:
    """给观测挂上图片引用；`with_image` 时再带上字节，让后端跑视觉绑定校验。

    引用始终带上：后端的重复帧/乱序分支只落库不落盘，没有引用这些帧在
    工作台上就是一张没有图的记录。
    """
    if not step.image:
        return payload
    name = os.path.basename(step.image)
    if name in refs:
        payload["image_ref"] = refs[name]
    if with_image:
        with open(step.image, "rb") as f:
            payload["image_b64"] = base64.b64encode(f.read()).decode()
    return payload


def _readback(state: dict) -> dict:
    """从工作台视角回读一遍，作为报告里的「确实生效了」证据。"""
    return {
        "captures": len(state.get("captures") or []),
        "guests": (state.get("metrics") or {}).get("guests"),
        "taken_g": (state.get("metrics") or {}).get("takenG"),
        "dishes": [{"id": d["id"], "name": d["name"], "takeG": d["takeG"],
                    "weightG": d["weightG"], "status": d["status"]}
                   for d in state.get("dishes") or []],
        "tasks": [{"id": t["id"], "dishId": t["dishId"], "quantity": t["quantity"],
                   "status": t["status"], "reason": t["reason"]}
                  for t in state.get("tasks") or []],
    }


# ---------------------------------------------------------------- check


def cmd_check(args: argparse.Namespace) -> int:
    client = BackendClient(args.backend, args.timeout)
    health = client.health()
    images = client.list_images()
    events = client.list_events(limit=1000)
    state = client.state()
    summary = client.analytics_summary()

    with_image = [e for e in events if e.get("image_ref")]
    broken = []
    for e in with_image:
        name = os.path.basename(str(e["image_ref"]).replace("\\", "/"))
        status, media = client.probe_image(name)
        # 206 是 Range 命中，同样是取到了图
        if status not in (200, 206) or not media.startswith("image/"):
            broken.append({"event_id": e["event_id"], "name": name,
                           "status": status, "content_type": media})

    print(f"[核对] 后端          {args.backend}（{health.get('mode')}）")
    print(f"[核对] 图片存储      {images['count']} 张，取不回 {len(broken)} 张")
    print(f"[核对] 站点事件      {len(events)} 条，其中带图 {len(with_image)} 条")
    print(f"[核对] 工作台抓拍    {len(state.get('captures') or [])} 条")
    print(f"[核对] 到店人数      {(state.get('metrics') or {}).get('guests')}"
          f"（分析接口口径 {summary.get('covers')}）")
    print(f"[核对] 菜品          {len(state.get('dishes') or [])} 项，"
          f"补菜任务 {len(state.get('tasks') or [])} 个")
    for d in state.get("dishes") or []:
        print(f"       {d['name']:<8} 余量 {d['weightG']}g  取用 {d['takeG']}g  状态 {d['status']}")
    for name in broken:
        print(f"       !! 取图失败 {name}")
    ok = not broken and len(events) > 0 and images["count"] > 0
    print(f"[核对] 结论：{'链路通' if ok else '链路有问题'}")
    return 0 if ok else 1


# ---------------------------------------------------------------- 帮助


def _merge(items: list[tuple[float, scenario.Step]]) -> list[tuple[float, scenario.Step]]:
    """按偏移排序；同一时刻先操作、再观测、最后客流。"""
    return sorted(items, key=lambda x: (x[0], _KIND_ORDER.get(x[1].kind, 9)))


def _sleep_until(started: float, offset: float) -> None:
    remaining = offset - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)


def _line(offset: float, text: str) -> None:
    print(f"  +{offset:6.1f}s  {text}", flush=True)


def _write_report(args: argparse.Namespace, result: dict) -> None:
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset": args.dataset, "crowd": None if args.no_covers else args.crowd,
        "speed": args.speed, "with_images": not args.no_images, **result,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[采集端] 完成：上报 {report['sent']}，失败 {len(result['failed'])} 条，"
          f"耗时 {result['elapsed_s']}s，生成补菜任务 {result['refill_tasks_created']} 个")
    print(f"[采集端] 回读工作台：抓拍 {report['readback']['captures']} 张，"
          f"到店 {report['readback']['guests']} 人，取用 {report['readback']['taken_g']}g")
    print(f"[采集端] 报告已写入 {args.report}")


if __name__ == "__main__":
    raise SystemExit(main())
