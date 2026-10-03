# 全员状态表

> **怎么用**：每位成员的 AI 上传完工作后，在这里**自己加一行**（用群昵称，别用分工文件里的代号——对不上号）。改自己那行，不动别人。列不够可以加，别删别人的行。
>
> 已有一行是 hz 的，格式照抄。

| 成员（群昵称） | 槽位 | 一句话产出 | 状态 | 证据 | 最近更新 |
|---|---|---|---|---|---|
| hz | modules/backend | 感知与补菜后端：事件解释状态机、三份接口契约、规则补菜、经营分析、数据集回放（对评 8/8、五场景 9/9） | 可运行 | evidence/hz-回放对评报告.json；modules/backend/scripts/scenario_tests.py | 2026-10-03 |
| （示例行，用完删）某某 | modules/dataset | 做了 XX 菜品的 AI 生成图片与质量标注 X 张 | 半成品 | evidence/某某/… | 10-03 |

| liiiyiiixiii（群昵称待确认） | modules/frontend；docs；evidence/liiiyiiixiii | 厨房/管理工作台：余量、补菜到口倒计时、任务操作、报损、经营分析、模型配置，独立原型含 API 与 D1 | 原型可运行；团队后端待联调 | evidence/liiiyiiixiii/验证记录.md（25/25、类型检查、构建通过）；界面截图 | 2026-10-03 |
| chujieHong | modules/model；modules/integration | Jev-Omni 视觉服务器部署与接口封装：菜品识别接口（8 道清单 8/8、40 道 75%）、HTTP 封装、网页版、线上契约快照；另做后端↔服务器可复跑联调脚本（认菜校验 8/8、回放对评 8/8） | 可运行；已与 backend 联调通过 | modules/integration/验证记录.md；modules/model/openapi.json；modules/integration/evidence/ | 2026-10-03 |

## 问题与待办

- [x] 队友 GitHub 权限（四个邀请已发，Dingyiiiii 已接受）
- [x] 工作台接入团队后端：**已由后端适配层完成（app/adapter.py，工作台代码零改动）**——页面经后端同源反代，/api/* 直达规则引擎，浏览器实测渲染真实数据。页面侧仅需视觉/交互微调（liiiyiiixiii 确认即可）
- [ ] 真实采集数据联调（有设备就往 /api/events/station/upload 发）
- [ ] 补菜规则阈值与逐月者规则草案核对
- [ ] 演示动线排练：工作台实操 + 数据面板收尾（具体今晚定）
- [x] **`jev.py` 的重试白名单从来没生效（已修，动了 `modules/backend/app/jev.py`）** —— `raise_for_status()` 在 `try` 里抛出，而它抛的 `HTTPStatusError` 是 `httpx.HTTPError` 的子类，**立刻被同一个 `except` 抓住继续重试**。所以文档里写的「401/422/413 不重试」从未成立：错误密钥、404、422 都会重试三次。已把状态码判断移出 `try`，只重试 transport 异常、5xx、408/425/429，以及该端点应当存在时的 404。实测：`/v1/portion` 24.9s→**8.4s**（3 次请求→**1 次**，服务器日志计数确认）、`/v1/measure` 30.5s→10.1s、错误密钥 401 ≥10.5s→**4.7s**；在线回放 **50.5s → 37.3s**
- [ ] **backend：`/v1/portion` 调用链仍在，但视觉服务器已移除该端点** —— 重试浪费已修（见上条），但「不该调这个端点」这个结构问题还在：`jev.py:115` → `vision.portion_estimate()` → `replay.py:135`。业务结果不受影响（仅少一行 `交叉验证(模型)` 日志）。是否删掉这条链由 backend 取舍 —— 详见 `modules/integration/验证记录.md` 第四节
- [ ] **backend：Windows 上起不来** —— `core.py:49` 等处的 `open()` 没指定 `encoding="utf-8"`，Windows 默认 GBK 读 UTF-8 中文会崩。临时用 `PYTHONUTF8=1`，正解是加 `encoding="utf-8"`
- [ ] **backend：`/v1/measure` 是死代码** —— `vision.measure_reduction()` 全仓无调用者
- [ ] `.gitignore` 没忽略 `.venv`，在仓库里建虚拟环境会变成未跟踪文件
- [x] **工作台页面经反代后「有 HTML 无样式」（已修，动了 `modules/backend/app/main.py`）** —— 根因：`_proxy_frontend` 用 httpx 默认的 `Accept: */*` 去请求 Vite，Vite 便把 `.css` 当成 JS 模块返回 `text/javascript`，浏览器 MIME 检查拒绝套用样式；同时**查询串被丢掉**，Vite 的 `?v=<hash>` 模块版本跟着错位。已改为透传 `accept`/`user-agent` 与查询串，并转发重定向的 `Location`。验证：**48 个模块经代理与直连逐字节一致**（修复前 30/48 不一致），`/app/globals.css` 恢复 `text/css`（220,827 字节）
