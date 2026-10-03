# 红考拉补菜与经营分析工作台

西安红考拉旋转自助小火锅的单店首版，包含运行总览、后厨任务、撤盘报损、经营分析和规则配置。界面为中文，时区为 Asia/Shanghai。

## 运行

当前只在本地运行和更新，不使用 Sites 发布。访问 `http://127.0.0.1:5173/`。

要求 Node.js 24，依赖用 npm 锁定。已有本地数据库和 `.env`，直接运行：

```sh
npm run dev
```

数据保存在项目的 `.wrangler/state`，密钥加密参数保存在忽略提交的 `.env`；重启服务不会清空配置。保留 `MODEL_KEY_ENCRYPTION_KEY`，避免无法解密已保存的 API Key。

首次从干净目录安装时执行 `npm ci`、复制 `.env.example` 为 `.env`、生成 32 随机字节的 base64 填入 `MODEL_KEY_ENCRYPTION_KEY`，然后执行 `npm run build`。用本地 Wrangler 依次应用 `drizzle/*.sql`（已应用的迁移不要重复执行）：

```sh
node --import ./scripts/sites-env.mjs node_modules/wrangler/bin/wrangler.js d1 execute DB --local --persist-to .wrangler/state --config dist/server/wrangler.json --file drizzle/0000_smooth_midnight.sql
node --import ./scripts/sites-env.mjs node_modules/wrangler/bin/wrangler.js d1 execute DB --local --persist-to .wrangler/state --config dist/server/wrangler.json --file drizzle/0001_wandering_jackpot.sql
```

`npm run db:generate` 仅在 schema 改动时生成新迁移。`npm run start` 可运行构建后的本地 Worker。

## 多端入口

- `/`：工作端选择页。
- `/kitchen`：厨房端，首页优先显示待补菜品、菜品补充总量及最近一盘到达补餐口的倒计时，按预计到口先后排列。任务汇总和历史记录保留在管理端。真实倒计时依赖设备提交 `refillArrivalAt`；未接入或预测失效显示待接入 / 待更新。模拟模式以明确标注的 3 分钟转盘周期演示，随模拟推进更新时间。
- `/admin`：管理端，默认进入菜品总览，包含全部后厨操作、经营分析、报表和设置。

两端复用业务组件，通过独立页面入口与导航展示不同内容，共用 `/api/*` 后端及同一 D1 数据库。选中相同数据来源时，操作会在另一端的下次刷新（每 5 秒）后反映；模拟与真实数据仍各自隔离。

当前为前端岗位视图拆分，尚未实现账号登录和服务端角色权限；入口不构成访问权限限制，现有状态接口仍返回完整门店数据。正式按账号隔离信息时需补充后端身份认证、角色授权和按角色裁剪接口数据。

## 功能与口径

- 模拟模式从 2026-10-03 18:42 开始；按钮推进一分钟，任务执行产生模拟上盘记录。所有示例订单、成本和历史均为模拟。
- 真实与模拟分别保存到 D1，刷新或跨会话读取同一来源的持久记录；业务数据不依赖浏览器存储。
- 真实模式默认关闭自动派单，菜品默认参数未经现场确认。逐项配置并确认后才会用于自动派单。
- 每盘标识代表一次上盘生命周期。跨圈复用同一标识，撤盘后再次上盘必须使用新标识。
- 净重变化用于估算取用和同盘补充；同盘增加是推定补充。小于等于 3g 的噪声不累计到新基准。计数与重量方向矛盾、低置信度、超容量或过期读数暂停该菜品自动派单。
- 净重与件数均支持。盘子自重、读数与影像同步、稳定采样由上游采集设备处理。
- 补菜依据近 10 分钟取用速率、批量、目标余量、准备和安全时间；未结束任务不重复派发。闭店前 30 分钟最多补一个批量，不足准备时间不派单。取消后 5 分钟冷却。
- 真实任务完成须先观测到足量补充，不允许通过点击伪造库存。任务可修改数量、延后或取消。
- 撤盘保留不算报损；丢弃及其他不再供餐的处置计入食材损失。餐后剩余需要另采样，不能从转盘识别得到。
- 取用不是实际吃掉的量；按人头收费的菜品不直接贡献销售收入。排名为取用排名。
- 收银与采购自动导入尚未实现；真实订单、采购成本、反馈和会员关联留待后续接入。默认只提供时段画像，不推断个人年龄或身份。
- 食材消耗成本是已记录取用及报损的重量乘当时已确认成本。缺少人数或成本时每客指标和毛利不计算；不包含人工、租金、能源、调料及其他成本。

## DeepSeek API Key 配置

服务端支持 OpenAI 兼容的 Chat Completions API，默认 `https://api.deepseek.com/chat/completions`，模型 `deepseek-flash`。官方说明：[DeepSeek V4.1 Flash](https://api-docs.deepseek.com/news/news260910/) 与 [API](https://api-docs.deepseek.com/api/create-chat-completion/)。

在“门店设置 → 数据接入”粘贴 API Key，点击“验证并保存”，即可启用分析助手；也可测试连接、更换或移除。密钥加密存入本地数据库，接口只返回末四位遮罩，不写入 Git 或浏览器存储。验证失败不会覆盖旧配置。

`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL` 可在本地 `.env` 调整；`DEEPSEEK_API_KEY` 保留为首次配置前的环境变量兼容入口。

尚未配置密钥时只运行规则分析，页面明确标记。已配置时调用 DeepSeek；模型失败或超时显示错误，不冒充成功。模型只读统计和给出建议，自动补菜由确定性规则执行。真实 DeepSeek 调用尚未联调。

## 验证

- `node --experimental-strip-types --test tests/engine.test.ts`
- `node node_modules/typescript/bin/tsc --noEmit`
- `node tests/api-smoke.mjs`（需要本地运行的 5173 服务；修改仅发生在模拟数据，验证后可保留或清理测试库）
- `npm run build`

已涵盖跨圈去重、幂等、乱序拒绝、重量噪声、异常恢复、闭店批量、重复任务、真实完成核对、报损与保留、成本缺失和模拟数据质量。支持浏览器 WebMCP 读取状态与推进模拟，使用同一业务接口；当前环境未完成浏览器实测。

## 首版边界

本项目为试运行原型，尚未连接真实门店设备、真实收银或采购，也未验证实际降低报损和提高利润的效果。规则需现场确认，不能直接按默认值用于后厨生产。

数据库采用按来源隔离的单行状态快照与版本比较更新，业务事件、任务和参数一并原子保存，适合首版单店低吞吐试运行。单份快照超过 1.8MB 时拒绝新写入，不静默丢历史。高频设备接入前应迁移到按事件存储的表、增加历史归档与设备在线监测；实时看板中的过期读数只描述可信余量，不代表确定缺菜。
