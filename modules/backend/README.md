# 旋转小火锅 · 感知与补菜后端（雏形）

XiHack2026 企业赛道四 —— 红考拉旋转小火锅「下一盘 / 多站点感知与智能补菜」的后端。
一句话：**采集端把"盘子经过时的图片+称重"发给它，它告诉你这盘菜发生了什么、还剩多少、要不要补、哪个菜受欢迎、浪费在哪。**

## 快速启动

```bash
cd backend
cp .env.example .env        # 填 JEV_API_KEY；留空 = 离线规则模式（不调模型也能完整跑）
../.venv/bin/uvicorn app.main:app --port 8000
# 看板  http://127.0.0.1:8000/
# 文档  http://127.0.0.1:8000/docs
```

依赖：`pip install -r requirements.txt`（fastapi / uvicorn / httpx / python-multipart）。

**一分钟演示**：打开看板 → 点「回放数据集（离线规则）」→ 看 8 条观测按时间线灌入、事件解释、补菜任务自动生成 → 点「在线模型」再看视觉校验结果。

## 架构（对应计划书的模块边界）

```
采集端(岳浩宇) ──站点事件──▶ ┌─────────────────────────────────────────┐
后厨/人工   ──操作记录──▶ │  core.py  HotpotService（唯一编排路径）      │
                            │   ├ interpreter.py  确定性事件解释状态机    │
                            │   ├ analytics.py   速度/ETA/滞留/偏好/报损 │
                            │   ├ refill.py      规则补菜 + 任务合并     │
                            │   ├ vision.py ──▶ jev.py ──▶ Jev 视觉服务器│
                            │   └ db.py          SQLite（data/hotpot.db）│
                            └─────────────────────────────────────────┘
前端(力一雄) ◀──快照/汇总/任务/分析──┘          replay.py：数据集回放+对评
```

- **量归 A、语义归 B**（服务器实测结论）：菜量以称重规则为准；模型只做认菜校验与交叉验证，失败自动跳过，**模型挂了业务照跑**。
- HTTP API 与数据集回放走同一条代码路径（`HotpotService`），回放即集成测试。

## 三份数据契约（对齐计划书）

### 1. 站点事件 `POST /api/events/station`（或 `/upload` 传图）

```json
{
  "event_id": "cam1-20261003-0001",     // 可选；重复上报自动忽略（幂等）
  "plate_id": "P001", "station_id": "A",
  "observed_at": "2026-10-03T17:00:00+08:00",
  "net_weight_g": 201,                   // 或 gross_weight_g + tare_g
  "dish_id": "D01",                      // 采集端认为的菜品（用于视觉校验）
  "image_b64": "...",                    // 可选；有则在线模式自动做认菜校验
  "quality": "normal"                    // normal|blur|occluded|low_res
}
```

返回：`{interpretation: {classification, delta_g, ...}, snapshot, new_tasks}`。
分类枚举：`baseline / no_change / removal / refill_confirmed / unexplained_increase / anomaly_resolved / late_refill / merged_pass`。

**解释规则**（全部确定性、可复现）：
- \|Δ\| ≤ 4g（噪声界）→ `no_change`；同盘同站 20s 内重复帧 → `merged_pass`
- 下降 → `removal`（取用；不推断顾客是否吃完）
- 上升且窗口内有补菜记录 → `refill_confirmed`；无记录 → `unexplained_increase`（**可信净重不更新，要求复秤**，且暂停该菜品自动派单）
- 异常后回到可信值 → `anomaly_resolved`（确认传感器尖峰，期间下降不记取用）

### 2. 状态快照 `GET /api/plates`

每盘：可信净重 / 余量比 / 剩余件数（易计数菜）/ 已取用 / 已补菜 / 取用速度 / **预计耗尽分钟** / 滞留分钟 / 报废风险 / 异常。`GET /api/plates/{id}/history` 看完整事件流。

### 3. 补菜任务 `GET /api/tasks`，`POST /api/tasks/{id}`

```json
{"status": "making"}                     // pending|making|done|cancelled|postponed
{"status": "cancelled", "note": "人工取消"}  // 修改/延后/取消必须带原因（PRD）
{"status": "postponed", "postpone_min": 10}
```

- 动作：`refill`（补）/ `check`（余量低但没人取 → 巡检，不自动补）/ `pull`（滞留超保鲜窗 → 撤盘防报废）
- **在制数量防重复派单**：未完成任务的量计入待补充量；同菜同动作的未完成任务更新而非新建
- 后厨补菜记录（`op_type=refill`）到达时自动闭环匹配任务；与称重核对不一致会标注"请人工检查"
- 闭店前 60 分钟自动降为半批量 + 低优先级（`refill.py` 顶部可配）

### 操作记录 `POST /api/events/operation`

上盘绑定 / 补菜 / 撤盘 / 换菜（换菜自动关闭旧 serving 开新 serving，历史不串盘）：

```json
{"timestamp": "...", "plate_id": "P001", "op_type": "rebind", "dish_id": "D02", "recorded_net_g": 220}
{"timestamp": "...", "plate_id": "P001", "op_type": "pull", "disposal_reason": "丢弃"}
```

处置原因（丢弃/喂狗/可复用）进**报损台账** `GET /api/analytics/waste`。

### 经营分析

- `GET /api/analytics/summary`：菜品取用排名、每客/每百客取用、站点偏好（哪段位置被取最多）、时段趋势、模拟食材成本（**显著标记 simulated**）
- `POST /api/analytics/covers`：模拟收银输入用餐人数（`{"timestamp": "...", "party_size": 3}`）
- `PUT /api/dishes/{dish_id}`：菜品配置（标准份量/准备时间/制作批量/保鲜窗/成本/是否计数）

## 与视觉服务器的分工（实测结论）

| 用途 | 接口 | 说明 |
|---|---|---|
| 认菜（绑定校验） | `/v1/identify` | 6 道菜清单实测 **6/6 正确**（conf 0.83–0.99）；< 0.6 当作没认出 |
| 菜量交叉验证 | `/v1/portion` | 合成模糊图上不可靠（曾答 0/2），**只做旁证不记账** |
| 精确测余量 | `/v1/measure` | 需固定机位 + 空盘参照，误差 1–2 个百分点；接入实拍后启用 |

## 前端工作台适配层（`app/adapter.py`）

工作台（`modules/frontend`）页面期待的接口方言在本后端上原生实现，**工作台代码零改动**即可跑在验证过的规则引擎上：

| 工作台调用 | 适配实现 |
|---|---|
| `GET /api/state?source=` | 组装 Summary 视图（菜品/盘子/任务/台账/时段/指标，数据全部来自规则引擎） |
| `POST /api/actions` | task（`preparing`↔`making`、`completed`↔`done`、delayMinutes↔延后）/ waste（撤盘进报损台账）/ settings / dish（菜品配置）/ simulate（真实链路无操作） |
| `POST /api/analysis` | 规则分析（口径对齐工作台 ruleAnalysis；未接 DeepSeek，永远走规则） |
| `POST /api/ingest` | 观测批量上报（observe/add/remove → 站点事件+操作记录，走完整解释状态机） |
| `GET /api/export` | CSV 导出（同工作台列） |
| `GET/POST/DELETE /api/model-config` | 桩：未配置（使页面走规则分析） |

部署：后端同时静态托管工作台构建产物（同源，页面相对路径 `/api/*` 直达适配层）。营收字段保持 null（收银未接入，不冒充模拟值）。

## 错误响应（给前端的约定）

HTTP 层：`404` 资源不存在（任务 id 等）、`422` 参数非法（负数量、非法状态、时间格式）、`409` 冲突（online 模式未配密钥）、`5xx` 服务端异常。

业务层软错误（HTTP 200 + `error` 字段，前端应提示并引导操作）：

| 场景 | 响应 |
|---|---|
| 盘子未绑定就上报 | `{"error": "盘 P00X 无开放绑定；请先 POST /api/bindings 上盘绑定"}` |
| 绑定/换菜缺菜品 | `{"error": "initial_load/rebind 需要 dish_id（盘-菜绑定）"}` |

不改变状态但需要前端知晓的信息（HTTP 200）：

| 场景 | 响应字段 |
|---|---|
| 同 event_id 重复上报 | `"duplicate": true` |
| 同一次经过的重复帧 | `"classification": "merged_pass"` |
| 迟到旧事件（乱序隔离） | `"classification": "out_of_order"` |
| 本次无称重 | `"classification": "no_weight"` |
| 未解释突增（要求复秤） | `"needs_remeasure": true` |

## 测试记录（2026-10-03，`scripts/scenario_tests.py` 可复跑）

| 场景（任务单第 4 条） | 结果 |
|---|---|
| 重复：同 event_id 连报两次 | ✅ 只记一次，不重复扣量 |
| 乱序：迟到旧事件 | ✅ 隔离留痕（out_of_order），状态与任务均不受影响 |
| 缺失：无称重事件 | ✅ 留痕（no_weight），缺称不等于空盘 |
| 补菜增重：有记录 / 无记录 | ✅ refill_confirmed / unexplained_increase（可信净重不更新） |
| 换菜：重新绑定 | ✅ 新 serving，历史事件不串批次 |
| 完成标准：任务完成后旧事件不再触发 | ✅（乱序隔离保证） |
| 数据集回放对 answer_key | ✅ 8/8（离线与在线模式） |
| 任务数量/状态校验 | ✅ 负数量、非法状态返回 422；延后需带原因 |

## 已验证（2026-10-03）

- 数据集回放**离线/在线均 8/8** 事件分类与场景设定一致（含 B003 +180g 传感器尖峰 → 标记不复秤、B004 恢复 → 确认尖峰不记取用）
- 快照数字与场景真值一致（小馒头 201→149 取 50g=2 个；腌牛肉 300−90+120≈332g）
- 认菜校验 6/6；补菜任务生成/闭环；撤盘报损台账；换菜不串盘；服务重启状态完整恢复
- 已知限制：速度/ETA 在 3 分钟的演示数据上偏大（数据时间压缩）；`/v1/portion` 在合成图上不可靠；回放状态（进度/日志）重启后清空（报告文件在 `data/replay_report.json`）
