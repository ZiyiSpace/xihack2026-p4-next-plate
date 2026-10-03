# 数据集模板 —— 把你的图片和称重样例放这里

这就是采集端读的东西。**复制整个目录**改成你自己的，然后：

```bash
python capture.py run --dataset 你的目录 --speed 6
```

不用改代码，也不用动 `modules/hotpot_dataset_v0_1`（那是数据集负责人的目录）。

## 目录长这样

```
你的目录/
├── observations.jsonl      ← 必填：一条 = 一次「转盘经过观察站」
├── images/                 ← 图放这里，observations.jsonl 用相对路径引用
│   ├── TEMPLATE001.png
│   └── ...
├── dish_catalog.json       ← 可选：用了后端不认识的菜品才需要
└── operation_log.jsonl     ← 可选：员工上盘/补菜记录，不填会自动补上盘记录
```

只有 `observations.jsonl` 是必填的。本模板**故意不放** `operation_log.jsonl`，
就是为了验证「只有图片 + 称重」也能跑通。

## observations.jsonl：一行一次过站

JSON Lines —— **一行一个 JSON 对象**，不要逗号，不要外层数组。

```json
{"sample_id":"TEMPLATE001","plate_id":"P001","dish_id":"D01","timestamp":"2026-10-03T18:00:00+08:00","station_id":"A","lap_index":1,"image_path":"images/TEMPLATE001.png","tare_g":180,"gross_weight_g":380,"net_weight_g":200}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `sample_id` | ✅ | 这次观测的唯一编号。**也是图片在后端的存储名**，重复跑靠它去重，别重号 |
| `plate_id` | ✅ | 物理盘号。同一盘的多条记录就是它的轨迹 |
| `timestamp` | ✅ | **必须带时区**（`+08:00`），否则后端算不了时效 |
| `station_id` | ✅ | 观察站，`A`/`B`/`C` 随便编，同站同盘 20 秒内会被合并成一次经过 |
| `dish_id` | 建议 | 这盘装的是什么菜，要在后端菜品表里存在（见下） |
| `net_weight_g` | 二选一 | 净重。和 `gross_weight_g`+`tare_g` 给一个就行 |
| `gross_weight_g` / `tare_g` | 二选一 | 毛重和皮重，后端自己减 |
| `image_path` | 可选 | 相对本目录的图片路径。**不填就是没有图**，事件照收，只是没有抓拍和视觉校验 |
| `lap_index` | 可选 | 第几圈，不影响判读 |

其余字段（`image_width`、`sequence_id`、`weight_source` 之类）采集端不读，留着不碍事。

### 最少要写什么

只要你能给出「一张图 + 一个净重 + 一个时间戳 + 一个盘号 + 一个站点」，就能跑：

```json
{"sample_id":"my-001","plate_id":"P001","station_id":"A","timestamp":"2026-10-03T18:00:00+08:00","net_weight_g":200,"image_path":"images/my-001.png"}
```

## images/

把图原样丢进来就行，文件名和 `image_path` 对上即可。**放多大的图都行**——
采集端上传的是原始字节，后端按内容判类型（PNG / JPEG / WebP 都认）。

本目录下的 `TEMPLATE00*.png` 是占位图（灰色方块，跟菜没关系）。它们只是为了让模板
开箱就能跑；**演示前务必换成真实照片**，否则视觉模型认不出东西。

占位图特意用 `TEMPLATE` 前缀，**不要**改回 `T001.png` 这种像真样本的名字：
存储名就是文件名，而 `T001`–`T010` 已经是 v1.0 数据集里豆腐片（D04）的样本编号。
名字撞上、内容不同，后端会按设计拒绝覆盖并返回 409，然后整个回放会中断。

## 判读规则（决定你要造什么样的数据）

后端不是简单地「重量变了就记账」，它按上一次可信读数解释这次变化：

| 重量变化 | 后端判成 |
|---|---|
| 首帧 | `baseline` 首次上盘基准 |
| 少 4 克以内 | `no_change`（`NOISE_BOUND_G` 噪声界） |
| 明显变少 | `removal` 取用 |
| 变多 + 有补菜记录 | `refill_confirmed` 补菜已确认 |
| 变多 + **没有**补菜记录 | `unexplained_increase` 未解释突增 → 标记并**暂停该菜自动派单** |

最后一条最容易踩：想演示「补菜」，要么在 `operation_log.jsonl` 里写一条 `refill`
记录，要么直接接受它被判成异常。**凭空变重一定是异常**，这是设计意图。

## dish_catalog.json：用新菜品才需要

后端只认识它菜品表里的菜。用后端已有的（`D01` 小馒头、`D05` 腌牛肉等 6 种）
不用管；要加新菜，在本目录放一份：

```json
[{"dish_id": "D07", "name": "毛肚", "challenge": "形状不规则、易遮挡", "status": "本批已生成"}]
```

采集端在 `run` 之前会把它**补进**后端（已存在的菜品不会覆盖——
小馒头「25 克/件、按件计数」这类现场调过的规则不能被数据集冲掉）。

## operation_log.jsonl：可选

员工上盘 / 补菜 / 撤盘的记录，能显著提高判读准确度：

```json
{"event_id":"E001","timestamp":"2026-10-03T17:59:45+08:00","plate_id":"P001","event_type":"initial_load","recorded_net_g":200}
{"event_id":"E002","timestamp":"2026-10-03T18:02:00+08:00","plate_id":"P001","event_type":"refill","recorded_added_g":120}
```

`event_type` 取 `initial_load`（上盘）/ `refill`（补菜）/ `pull`（撤盘）/ `rebind`（换菜）。

**不放这个文件也能跑**：采集端会按每盘的首次观测自动补一条 `initial_load`
（时间放在首次观测前 15 秒，上盘量取首次观测的净重）。因为后端要求
「盘先有开放绑定」才收站点事件，缺了它会逐条报 `盘 P001 无开放绑定`。

上盘时间要**早于**该盘首次观测，补菜时间要落在重量变多的那两次观测之间。
