# 全员状态表

> **怎么用**：每位成员的 AI 上传完工作后，在这里**自己加一行**（用群昵称，别用分工文件里的代号——对不上号）。改自己那行，不动别人。列不够可以加，别删别人的行。
>
> 已有一行是 hz 的，格式照抄。

| 成员（群昵称） | 槽位 | 一句话产出 | 状态 | 证据 | 最近更新 |
|---|---|---|---|---|---|
| hz | modules/backend | 感知与补菜后端：事件解释状态机、三份接口契约、规则补菜、经营分析、数据集回放（对评 8/8、五场景 9/9） | 可运行 | evidence/hz-回放对评报告.json；modules/backend/scripts/scenario_tests.py | 2026-10-03 |
| （示例行，用完删）某某 | modules/dataset | 做了 XX 菜品的 AI 生成图片与质量标注 X 张 | 半成品 | evidence/某某/… | 10-03 |

| liiiyiiixiii（群昵称待确认） | modules/frontend；docs；evidence/liiiyiiixiii | 厨房/管理工作台：余量、补菜到口倒计时、任务操作、报损、经营分析、模型配置，独立原型含 API 与 D1 | 原型可运行；团队后端待联调 | evidence/liiiyiiixiii/验证记录.md（25/25、类型检查、构建通过）；界面截图 | 2026-10-03 |
| chujieHong | modules/model；modules/integration；modules/simulator | Jev-Omni 视觉服务器部署与接口封装：菜品识别接口（8 道清单 8/8、40 道 75%）、HTTP 封装、网页版、线上契约快照；后端↔服务器可复跑联调脚本（认菜 8/8、回放对评 8/8）；**虚拟采集端**（无硬件时扮演相机与称重网关：图片进存储、事件走真实 HTTP，129 条 0 失败、视觉校验 8/8） | 可运行；已与 backend 联调通过 | modules/integration/验证记录.md；modules/simulator/验证记录.md；modules/integration/evidence/工作台渲染核对.txt；modules/integration/evidence/样例数据接入-界面验收.txt；modules/model/openapi.json | 2026-10-03 |

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
- [x] **无硬件时的数据源：虚拟采集端（新增 `modules/simulator`）** —— 只走 HTTP 调后端真实接口，不 import 后端代码，换真硬件时整体替换。三条流：数据集观测（带图上传）、操作记录、客流画像。默认把时间轴平移到当前时刻，否则后端按真实当前时间会算出「几小时没有取用」，每盘都判成滞留需复核。实测 129 条事件 0 失败、视觉校验 8/8、11 条事件 41.8 秒
- [x] **抓拍图片的存储与出口（新增 `modules/backend/app/images.py`，并动了 `core.py`/`main.py`/`adapter.py`/`schemas.py`）** —— 之前图片只写盘、没有任何 HTTP 出口，前端也拿不到。现在 `POST /api/images` 上传、`GET /api/images/{name}` 读取、`GET /api/images` 列目录；事件的 `image_ref` 从**宿主绝对路径**改成同源 URL `/api/images/<name>`；工作台新增「采集抓拍」卡片（最近一张图 + 称重 + 判读 + 模型识别结果）。同时修掉三处隐患：`event_id` 无格式约束却被当文件名（非法值会 500，现由 schema 拒绝为 422）、`.part` 临时文件会出现在列目录里、并发写共用同一临时文件
- [x] **工作台抓拍卡片在真浏览器里渲染过（新增 `modules/integration/check_workbench_render.mjs`）** —— `tsc` 通过、`/api/state` 有 `captures`，都只能说明「代码能编译、数据能取到」，说明不了页面上长什么样。这个脚本真开浏览器打开工作台，读 DOM 并检查 `naturalWidth`——只有图**真的解码成功**才大于 0，光比对 `<img src>` 看不出裂图。实测 `/admin` 与 `/kitchen` 都通过（图片 1536×1024），反例（入口页 `/`）退出 1 并指出「卡片没渲染出来」
- [ ] **演示前确认打开的是 `/kitchen` 或 `/admin`** —— `http://127.0.0.1:8000/` 是「选择工作台」入口页，**没有**抓拍卡片
- [x] **管理端「样例数据接入」：门店设置 → 数据接入，选图、填净重、点运行（新增 `modules/backend/app/samples.py`、`modules/frontend/components/workspace/sample-intake.tsx`）** —— 没有相机和秤时，人先传样例；点运行依次走「视觉接口识别菜品 → 站点事件 → 解释 → 补菜」，**不另开旁路**，所以余量、台账、补菜任务、时段分析自动带上它。盘号留空则按识别出的菜品自动分盘（同一道菜多张图共用一个循环盘，才可能算出取用；一张图一个盘只会得到一堆「首次上盘基准」）。识别不可信时**拒绝入账**并提示手动指定菜品，不瞎记账。界面验收：选 2 张真图、填 201/151 克、点运行 → 判定「取用 50 克·已生成补菜任务」，总览显示小馒头余量 151g/需补充
- [x] **整体 review（高内聚低耦合）已做，详见 `modules/integration/代码审查记录.md`** —— 依赖图无环，`core` 不 import 上层。实证出 6 个真 bug 并修掉，其中一个是既有的：**`analytics.dish_summaries` 把在途量加了两遍**（两段逐字相同的累加），工作台「待补充」常年翻倍（实测任务 50g/2 个 → 显示 4 个），后厨会照错数备料。另修：样例 PATCH 不校验（`observed_at:"garbage"` 存进去、入账时 500）、批次中途出错导致数据库与登记状态不一致、自动分盘与手填盘号撞车、改完输入重跑显示「已入账」但结果空、样例登记表借用 `meta` 表会被业务复位删掉、读-改-写无锁。并把 3 份时间格式化 / 2 份判读说法 / 2 份克数格式化 / 2 份图片 key 推导收成各 1 份
- [ ] **review 里标注「未修、需拍板」的 11 项** —— 最重要的是 `adapter.build_view`（167 行 / 43 分支 / 8 件事）、`core.ingest_station_event`（111 行 / 6 件事）、`vision._SHARED` 全局单例；另有 `images.py` 存储与路由同文件、两个 CDP 脚本约 60 行重复、判读分类取值仍有 7 处事实来源。都在冻结后做
- [ ] **人流分析的「离店 / 翻台」没有接口落点** —— `POST /api/analytics/covers` 只收「到店人数」，工作台也只有到店人数与每百客口径。客流画像里的就餐时长目前只是场景说明，没有变成任何调用。要做翻台率需要后端新增入座/离店事件，并在工作台给它一个位置
- [x] **工作台页面经反代后「有 HTML 无样式」（已修，动了 `modules/backend/app/main.py`）** —— 根因：`_proxy_frontend` 用 httpx 默认的 `Accept: */*` 去请求 Vite，Vite 便把 `.css` 当成 JS 模块返回 `text/javascript`，浏览器 MIME 检查拒绝套用样式；同时**查询串被丢掉**，Vite 的 `?v=<hash>` 模块版本跟着错位。已改为透传 `accept`/`user-agent` 与查询串，并转发重定向的 `Location`。验证：**48 个模块经代理与直连逐字节一致**（修复前 30/48 不一致），`/app/globals.css` 恢复 `text/css`（220,827 字节）
