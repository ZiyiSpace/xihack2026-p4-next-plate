# 全员状态表

> **怎么用**：每位成员的 AI 上传完工作后，在这里**自己加一行**（用群昵称，别用分工文件里的代号——对不上号）。改自己那行，不动别人。列不够可以加，别删别人的行。
>
> 已有一行是 hz 的，格式照抄。

| 成员（群昵称） | 槽位 | 一句话产出 | 状态 | 证据 | 最近更新 |
|---|---|---|---|---|---|
| hz | modules/backend | 感知与补菜后端：事件解释状态机、三份接口契约、规则补菜、经营分析、数据集回放（对评 8/8、五场景 9/9） | 可运行 | evidence/hz-回放对评报告.json；modules/backend/scripts/scenario_tests.py | 2026-10-03 |
| （示例行，用完删）某某 | modules/dataset | 做了 XX 菜品的 AI 生成图片与质量标注 X 张 | 半成品 | evidence/某某/… | 10-03 |

| liiiyiiixiii（群昵称待确认） | modules/frontend；docs；evidence/liiiyiiixiii | 厨房/管理工作台：余量、补菜到口倒计时、任务操作、报损、经营分析、模型配置，独立原型含 API 与 D1 | 原型可运行；团队后端待联调 | evidence/liiiyiiixiii/验证记录.md（25/25、类型检查、构建通过）；界面截图 | 2026-10-03 |

## 问题与待办

- [x] 队友 GitHub 权限（四个邀请已发，Dingyiiiii 已接受）
- [ ] **liiiyiiixiii：工作台改接 FastAPI 契约（映射表与契约速查见 docs/联调对接说明.md；后端跑起来看 /docs 交互式文档）——今晚 20:30 冻结前**
- [ ] 真实采集数据联调（有设备就往 /api/events/station/upload 发）
- [ ] 补菜规则阈值与逐月者规则草案核对
- [ ] 演示动线排练：工作台实操 + 数据面板收尾（具体今晚定）
