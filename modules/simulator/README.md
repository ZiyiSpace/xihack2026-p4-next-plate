# 虚拟采集端

没有相机、没有称重台时，用它把预生成的数据集当成「刚刚采集到的数据」推给后端。
它站在**设备那一侧**：只通过 HTTP 与后端真实接口交互，不 import 后端任何代码。

```bash
cd modules/simulator
python capture.py seed     # 把数据集图片放进后端图片存储
python capture.py run      # 按时间轴推送观测、操作与客流
python capture.py check    # 回读后端，确认数据真的进去了
```

只依赖 Python 3.10+ 标准库，不需要装任何包，也不需要本仓库的虚拟环境。

## 换成你自己的图片和称重数据

复制 [`dataset_template/`](dataset_template/README.md) 整个目录，把图丢进它的 `images/`，
改 `observations.jsonl`，然后：

```bash
python capture.py run --dataset 你的目录 --speed 6
```

不用改代码，也不用动 `modules/hotpot_dataset_v0_1`（那是数据集负责人的目录）。

只有 `observations.jsonl` 是必填的，一行一次过站：

```json
{"sample_id":"my-001","plate_id":"P001","station_id":"A","timestamp":"2026-10-03T18:00:00+08:00","net_weight_g":200,"image_path":"images/my-001.png"}
```

`operation_log.jsonl`（员工上盘/补菜记录）和 `dish_catalog.json`（新菜品目录）都是可选的：
前者缺了会按每盘首次观测自动补一条上盘记录，后者只管后端还不认识的菜。
字段逐个解释见 [`dataset_template/README.md`](dataset_template/README.md)。

模板里那几张 `T00*.png` 是**灰色占位图，跟菜没关系**，只为让模板开箱能跑。
用它们跑的时候视觉校验会返回「低于置信下限，不作判定」——这不是 bug，
是模型对着一张灰方块诚实地不猜。换成真照片就好了。

## run 的参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--backend` | `http://127.0.0.1:8000` | 后端根地址，也可用 `CAPTURE_BACKEND` |
| `--dataset` | `../hotpot_dataset_v0_1` | 数据集目录 |
| `--crowd` | `data/crowd_evening.json` | 客流画像 |
| `--speed` | `30` | 观测流回放倍速；`--speed 6` 约 37 秒，演示更好看 |
| `--shift` | `now` | `now` 把时间轴平移到当前时刻；`none` 保留数据集原始时间戳 |
| `--no-covers` | 关 | 不发客流事件 |
| `--no-images` | 关 | 只发图片引用不发字节，跳过视觉校验（快很多） |
| `--keep` | 关 | 不复位后端数据（默认会先复位，否则客流会累加） |
| `--verbose` | 关 | 逐条打印客流事件（默认每 25 桌一行） |
| `--report` | `evidence/capture_report.json` | 运行报告写入路径 |

## 为什么默认要平移时间轴

数据集的时间戳是录制当天。直接发进去，后端会拿**真实当前时间**和事件时间作差，
算出「几小时没有取用」，于是每一盘都显示滞留需复核。
采集端模拟的是「刚刚拍到」，所以默认把整条时间轴挪到现在，后端的时效判断才成立。
需要和数据集逐字对齐核对时用 `--shift none`。

## 为什么倍速只作用于观测流

观测流窗口 220 秒（8 张图），客流画像覆盖 4 小时晚市——两条流本来就不同长。
`--speed` 定义的是**整段演示跑多久**：观测流照倍速压到该时长，客流曲线线性铺满同一时长，
两条流同时结束。真实耗时会更长一些，因为每条带图的观测都要等视觉服务器推理。

## 客流画像

`data/crowd_evening.json` 描述晚市到达率、桌均人数分布和随机种子，
由 `scenario.py` 用固定 seed 确定性展开成到店事件——同一份画像必然得到同一条曲线，
演示可复现。它是**纯合成画像**，不是实测客流。

只产出「到店人数」：后端与工作台目前只有这一个客流落点。画像里的 `dwell_minutes`
是场景假设，没有对应的接口，所以不往外发（详见 [`模块说明.md`](模块说明.md) 的「没做完的」）。

## 退出码

`run` / `check`：`0` 成功，`1` 有事件发送失败或链路核对不通过，便于接进 CI 或演示脚本。
