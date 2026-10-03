# 接入说明

## 本地 D1

在项目根目录先运行 `npm run build` 生成 `dist/server/wrangler.json`，首次迁移：

```sh
node --import ./scripts/sites-env.mjs ./node_modules/wrangler/bin/wrangler.js d1 execute DB --local --config dist/server/wrangler.json --persist-to .wrangler/state --file drizzle/0000_smooth_midnight.sql
```

生产迁移由 Sites 发布流程负责，运行时不创建表。迁移一旦应用后不修改原文件。

## 视觉与称重事件

服务端配置 `INGEST_API_KEY` 后，使用 `POST /api/ingest` 和 `Authorization: Bearer <token>`。示例（时间须替换为真实采样时间）：

```json
{
  "events": [{
    "eventId": "camera-01-seq-1001",
    "timestamp": "2026-10-03T18:42:00+08:00",
    "plateId": "plate-session-1001",
    "dishId": "beef",
    "kind": "add",
    "netWeightG": 300,
    "confidence": 0.98
  }]
}
```

- `eventId` 必须稳定且唯一，重试使用原 id。每次最多 100 条，按盘子采样时间递增发送，整个批次原子提交；其中任一无效事件会拒绝整批。
- `plateId` 是一次上盘生命周期标识；循环同盘保留该 id。重新上盘或换菜需新的生命周期 id。
- `dishId` 目前固定：beef、shrimp、potato、tofu、mushroom、lettuce、meatball、kelp、lotus、watermelon、bun、noodle。可在代码及后续菜品维护接口扩展。
- `kind` 为 `add`（新补充上盘）、`observe`（跨圈观测/同盘补充）、`remove`（撤盘）。新盘以 observe 首次出现时只记初始库存，不冒充已确认补充。
- `netWeightG` 为去皮后实际净重，0–2500g；净重超过菜品配置的标准单盘容量时标记异常，单位为件的容量按件判定。正常制作批量可拆成多盘。
- 鲜虾、牛肉丸、西瓜、小馒头还必须提供整数 `count`。计数与重量变化方向须一致。
- `refillArrivalAt`（可选）：该盘下一次到达补餐口的预计时间，ISO 8601，不能早于本次 `timestamp`。由采集端结合盘子位置、距补餐口距离与转盘速度计算；停机或无法预测时省略。每次观测都应更新，省略即清除旧预测。厨房端按同菜品最近一盘显示倒计时，已撤盘、异常、过期观测和超过到口时间 10 秒的预测不再使用。到口时间与任务 `dueAt`（补菜截止时间）是独立概念。
- `confidence` 为视觉与称重匹配可信度，低于 0.85 标记异常。该阈值需用实测数据校准。
- 撤盘需 `disposition`（discard 丢弃 / other_loss 其他不再供餐处置 / retain 保留）及 `reason`。撤盘前最后的取用差额会独立计入取用。
- 旧于同盘最后观测的事件被拒绝；超过服务端当前时间 5 分钟的事件被拒绝。异常读数不覆盖最后可信质量基准。
- 真实数据默认无订单、无报损历史、规则待确认；不能把模拟记录当成真实记录导入。

返回 `{success, results: [{eventId, status}], revision}`。重复事件返回 duplicate，无副作用；异常事件返回 anomaly，盘子待复核。401 为认证失败，503 为未配置或数据服务不可用，400 为输入或业务条件不满足。重试需保留事件 id。

## 其他接口

- `GET /api/state?source=demo|live`：当前可信库存、任务、当日汇总和最近 200 条流转记录。5 秒轮询，真实模式使用服务端当前时间检查过期。
- `POST /api/actions`：业务操作，source 与 UUID actionId 必填。支持 simulate、task、waste（仅模拟）、settings、dish。模拟推进在真实来源被拒绝；重复 actionId 不重复执行。数量和单位校验在服务端执行。
- `POST /api/analysis`：`{source, question}`，输出 provider、model、answer。未配置模型用规则分析，已配置模型服务失败返回错误，可能附带明确标记的规则替代结果。
- `GET /api/export?source=demo|live`：UTF-8 BOM CSV 菜品汇总，含数据来源标记。

页面读写依赖 Site 的私有访问策略。采集接口另要求设备令牌。现阶段没有 POS / 会员 / 成本采购的自动导入接口，真实群体画像与收益评估需后续开发。


## 页面配置 DeepSeek 密钥

门店设置 → 数据接入 → DeepSeek：粘贴 API Key 后点击“验证并保存”。服务端使用官方 `/models` 验证凭证及当前模型；验证失败不会覆盖旧密钥。测试不验证余额、实际生成质量或保证后续分析成功。

密钥使用 AES-256-GCM 加密后存入独立 `model_credentials` 表，不写入 demo/live 状态、导出或浏览器存储。页面只得到末四位遮罩。模拟和真实模式共用门店模型配置。保存后无需重新部署即可用于分析。移除记录使用停用标记，防止旧环境变量回退意外重新启用。

服务端 `MODEL_KEY_ENCRYPTION_KEY` 为 32 随机字节的 base64，作为 Sites secret 配置，必须保持稳定；轮换需先重新加密已存凭证。环境变量 `DEEPSEEK_API_KEY` 只在从未设置页面配置时兼容使用。当前 Site 通过平台 owner-private 权限保护这些共享配置接口，变更访问范围时需添加门店管理员授权检查。
