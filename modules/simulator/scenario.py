"""场景装载：把数据集与客流画像展开成采集端要上报的事件流。

本模块只读文件和算时间，不发请求，方便单独核对「时间轴展开对不对」。

两条流的窗口长度不一样，这不是错误而是事实：
- 站点事件来自数据集，窗口是那 8 张图的真实间隔（约 3 分半）；
- 客流画像覆盖整个晚市（约 4 小时）。
因此这里只负责把每条流**线性铺到指定时长**上，由调用方决定这段演示跑多久，
两条流会同时结束。
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Optional


@dataclass(frozen=True)
class Step:
    """采集端在某一时刻要上报的一件事。

    `payload` 已经是后端该端点的请求体：数据集字段到上报契约的映射属于场景知识，
    放在这里而不是发送端，发送端就只剩「按时间发出去」。
    `image` 是要随事件上传的图片绝对路径，没有图时为 None。
    """

    at: datetime
    kind: str                     # station | operation | covers
    payload: dict
    label: str
    image: Optional[str] = None


@dataclass(frozen=True)
class Stream:
    """一条按时间升序的采集流。"""

    name: str
    steps: list[Step]

    @property
    def start(self) -> datetime:
        return self.steps[0].at

    @property
    def end(self) -> datetime:
        return self.steps[-1].at

    @property
    def span_s(self) -> float:
        return (self.end - self.start).total_seconds()

    def offsets(self, duration_s: float) -> list[tuple[float, Step]]:
        """把这条流线性铺到 `duration_s` 秒上，返回 (秒偏移, 事件)。

        首事件在 0 秒、末事件在 duration_s 秒；流内时间间隔的比例保持不变。
        """
        span = self.span_s
        return [((s.at - self.start).total_seconds() / span * duration_s, s) if span else (0.0, s)
                for s in self.steps]


# ---------------------------------------------------------------- 数据集

def load_station_stream(dataset_dir: str) -> Stream:
    """读 observations.jsonl（+ 可选的 operation_log.jsonl），合成一条站点采集流。

    只要你能提供「一张图 + 一个净重读数 + 一个时间戳」，就能造出自己的数据集：
    `operation_log.jsonl` 是可选的，缺了会自动按每盘首次观测补上盘记录
    （后端要求盘先有开放绑定才收站点事件）。

    answer_key.jsonl 刻意不读：它是评分集，不能作为采集端输入。
    """
    obs = _read_jsonl(os.path.join(dataset_dir, "observations.jsonl"))
    ops_path = os.path.join(dataset_dir, "operation_log.jsonl")
    ops = _read_jsonl(ops_path) if os.path.exists(ops_path) else []
    # 盘-菜绑定由外部提供（数据集 README：图中没有可解码的盘号标记），
    # 上盘/补菜记录本身不带 dish_id，按观测里的绑定补上。
    plate_dish = {o["plate_id"]: o.get("dish_id") for o in obs}
    if not ops:
        ops = _synthesize_initial_load(obs)

    steps: list[Step] = []
    for rec in obs:
        image = os.path.join(dataset_dir, rec["image_path"]) if rec.get("image_path") else None
        steps.append(Step(
            at=_parse(rec["timestamp"]), kind="station",
            payload={
                "event_id": rec["sample_id"],
                "plate_id": rec["plate_id"],
                "station_id": rec["station_id"],
                "observed_at": rec["timestamp"],
                "lap_index": rec.get("lap_index"),
                "net_weight_g": rec.get("net_weight_g"),
                "gross_weight_g": rec.get("gross_weight_g"),
                "tare_g": rec.get("tare_g"),
                "dish_id": rec.get("dish_id"),
                # 采集端对帧画质的判断（normal|blur|occluded|low_res）。数据集可以在
                # observations.jsonl 里用 frame_quality 声明；没声明就按 normal 上报 ——
                # 真实部署里这一项要由采集端自己从像素算（模糊/遮挡检测），不是抄标注。
                "quality": rec.get("frame_quality") or "normal",
                "source": "capture",
                "simulated": True,
            },
            image=image,
            label=(f"站点 {rec['station_id']} 盘 {rec['plate_id']} "
                   f"净重 {rec.get('net_weight_g')}g 图 {rec['sample_id']}"),
        ))
    for rec in ops:
        plate_id = rec.get("plate_id")
        synthesized = rec.get("source") == "capture_synthesized"
        steps.append(Step(
            at=_parse(rec["timestamp"]), kind="operation",
            payload={
                "op_id": rec["event_id"],
                "timestamp": rec["timestamp"],
                "plate_id": plate_id,
                "op_type": rec["event_type"],
                "dish_id": plate_dish.get(plate_id),
                "recorded_net_g": rec.get("recorded_net_g"),
                "recorded_added_g": rec.get("recorded_added_g"),
                "source": rec.get("source") or "capture",
                "simulated": True,
            },
            label=f"操作 {rec['event_type']} 盘 {plate_id} 绑定 {plate_dish.get(plate_id)}",
        ))

    steps.sort(key=lambda s: s.at)
    if not steps:
        raise FileNotFoundError(f"{dataset_dir} 下没有可用的观测或操作记录")
    return Stream("dataset", steps)


def load_catalog(dataset_dir: str) -> list[dict]:
    """数据集自带的菜品目录；没有这个文件就返回空列表。

    后端只会为它认识的菜品记账，带新菜品的数据集需要先把目录推过去。
    """
    path = os.path.join(dataset_dir, "dish_catalog.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _synthesize_initial_load(obs: list[dict]) -> list[dict]:
    """没有操作记录时，按每盘首次观测补一条上盘记录。

    后端要求「盘先有开放绑定」才收站点事件，缺了这条，一个只带图片和称重的
    数据集会被逐条拒收（`盘 P001 无开放绑定`）。上盘量取首次观测的净重，
    时间放在首次观测前 15 秒。
    """
    first: dict[str, dict] = {}
    for o in obs:
        first.setdefault(o["plate_id"], o)
    out = []
    for plate_id, o in first.items():
        at = _parse(o["timestamp"]) - timedelta(seconds=15)
        out.append({
            "event_id": f"AUTO-{plate_id}",
            "timestamp": at.isoformat(timespec="seconds"),
            "plate_id": plate_id,
            "event_type": "initial_load",
            "recorded_net_g": o.get("net_weight_g"),
            "source": "capture_synthesized",
        })
    return out


def image_files(stream: Stream) -> dict[str, str]:
    """采集流里出现的图片：存储名 -> 本地文件绝对路径。

    存储名沿用数据集文件名（如 B001.png），这样存储里的 key 和数据集能对上，
    出错时一眼看得出是哪张图。
    """
    out: dict[str, str] = {}
    for s in stream.steps:
        if s.image:
            out[os.path.basename(s.image)] = s.image
    return out


def shift(stream: Stream, delta: timedelta) -> Stream:
    """整条流平移 `delta`，事件时间和载荷里的时间戳一起挪。

    数据集的时间戳是录制当天，直接回放会被后端按**真实当前时间**算出一段
    「几小时没有取用」，把每一盘都判成滞留需复核。采集端模拟的是「刚刚拍到」，
    所以默认把整条时间轴挪到现在——这既是场景的本意，也让后端的时效判断成立。
    """
    moved = []
    for s in stream.steps:
        payload = dict(s.payload)
        for key in ("observed_at", "timestamp"):
            if key in payload:
                at = datetime.fromisoformat(payload[key]) + delta
                payload[key] = at.isoformat(timespec="seconds")
        moved.append(replace(s, at=s.at + delta, payload=payload))
    return Stream(stream.name, moved)


# ---------------------------------------------------------------- 客流画像

def load_crowd_stream(profile_path: str) -> Stream:
    """按客流画像展开到店事件。

    同一份画像 + 同一个 seed 必然得到同一条曲线，演示可复现。
    只产出「到店人数」：后端与工作台目前只有这一个客流落点，
    离店/翻台没有接口承接，不在这里伪造。
    """
    with open(profile_path, encoding="utf-8") as f:
        profile = json.load(f)
    rng = random.Random(profile["seed"])
    day = profile["date"]
    offset = profile.get("timezone_offset", "+08:00")
    sizes = [int(k) for k in profile["party_size_weights"]]
    weights = [float(profile["party_size_weights"][k]) for k in profile["party_size_weights"]]

    steps: list[Step] = []
    for hour, count in profile["arrivals_per_hour"].items():
        base = datetime.fromisoformat(f"{day}T{int(hour):02d}:00:00{offset}")
        for i in range(int(count)):
            # 每小时 count 桌，在小时内均匀落点并加一点抖动
            at = base + timedelta(minutes=(i + rng.random()) / int(count) * 60)
            party = rng.choices(sizes, weights=weights)[0]
            steps.append(Step(
                at=at, kind="covers",
                payload={
                    "timestamp": at.isoformat(timespec="seconds"),
                    "party_size": party,
                    "note": profile.get("scenario"),
                    "simulated": True,
                },
                label=f"到店 {party} 人",
            ))

    steps.sort(key=lambda s: s.at)
    if not steps:
        raise ValueError(f"{profile_path} 展开后没有任何到店事件")
    return Stream("crowd", steps)


# ---------------------------------------------------------------- 帮助

def _read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)
