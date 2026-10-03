# Jev-Omni 多模态决策接口 · 部署说明

在 AutoDL 单卡 RTX 4090（24GB）上部署 [`akhilaaa3/Jev-Omni`](https://huggingface.co/akhilaaa3/Jev-Omni)
（Gemma 4 12B IT 合并权重，11.96B 参数）。
文本塔用 bitsandbytes **8bit 量化**，视觉/音频投影层保持 **BF16**，全部资产放在数据盘 `/root/autodl-tmp`。

> 这个模型不是聊天模型，而是**多模态决策分类器**：给它「情境 + 一个问题 + 若干选项」，
> 它做一次前向就返回每个选项的概率，不生成任何文字。

---

## 一、部署结果（实测）

| 指标 | 实测值 |
|---|---|
| 常驻显存 | **12.8 GiB** / 23.64 GiB |
| 四模态全跑后峰值 | **15.08 GiB** / 23.64 GiB |
| 模型载入耗时 | **33 秒** |
| 文本推理 | 250–1050 ms |
| 图片推理 | 276–352 ms |
| 音频推理 | 259 ms |
| 视频推理（16 帧） | 1084 ms |
| 并发 | 4 路并发全部成功，1 秒内完成，串行排队、无 OOM |
| 外网端到端 | 文本 **0.35 s** / 图片上传 **0.47 s**（含网络往返） |

| 组件 | 版本 |
|---|---|
| torch / torchvision | 2.14.1+cu126 / 0.29.1+cu126 |
| transformers | 5.17.0（模型作者指定） |
| bitsandbytes | 0.50.2 |
| accelerate / huggingface_hub | 1.15.0 / 1.33.0 |
| fastapi / uvicorn | 0.142.2 / 0.54.0 |
| GPU / 驱动 | RTX 4090 24GB / 550.107.02 |

---

## 二、目录结构

| 项目 | 路径 |
|---|---|
| 模型权重 | `/root/autodl-tmp/models/Jev-Omni`（`model.safetensors` = 23,919,549,408 字节） |
| Python 环境 | `/root/autodl-tmp/venv`（独立 venv，未改动镜像自带的 conda base） |
| 服务代码 | `/root/autodl-tmp/jev-app/jev_api.py`（接口）、`ui_html.py`（网页版）、`menus.json`（候选菜名清单） |
| 启动脚本 | `/root/autodl-tmp/jev-app/start.sh` |
| 日志 | `/root/autodl-tmp/logs/api.log` |
| API 密钥 | `/root/autodl-tmp/jev-api-key.txt` |
| 监听地址 | `0.0.0.0:6006`（AutoDL 已把 6006 映射到公网） |

---

## 三、启动、停止与自动恢复

服务已经配置为**无人值守**，正常情况下你不需要做任何事。

**自动恢复机制（已安装并验证）**

| 能力 | 机制 | 验证结果 |
|---|---|---|
| 崩溃自恢复 | 常驻看门狗每 30 秒检查一次；进程消失立即重启，活着但不响应则连续 3 次后重启 | 实测 `kill` 掉服务后 **20 秒**拉回进程、**40 秒**恢复可用，接口调用正常 |
| 开机自启 | 平台的用户启动脚本 `/etc/autodl.sh`（由 `/init/bin/customer.cmd.sh` 在容器启动时调用）拉起看门狗 | 按平台方式执行该脚本，看门狗自动恢复 |
| 兜底 | `/root/.bashrc` 里还有一行带防重判断的自启（防止 `/etc/autodl.sh` 被平台更新覆盖） | 已安装 |

看门狗自身也是 `setsid` 脱离终端运行的，SSH 断开不影响。

**手动操作（一般不必要）**

```bash
# 查看状态
bash /root/autodl-tmp/jev-app/start_watchdog.sh   # 已在跑会直接提示，不会重复启动
curl -s http://127.0.0.1:6006/health

# 日志
tail -f /root/autodl-tmp/logs/watchdog.log   # 看门狗：重启记录
tail -f /root/autodl-tmp/logs/api.log        # 服务本身：载入、报错

# 重启服务（看门狗会自动接管）
pkill -f "uvicorn jev_api:app"

# 完全停止（连看门狗一起停，否则它会把服务再拉起来）
pkill -f "jev-app/watchdog.sh"; pkill -f "uvicorn jev_api:app"
```

**注意**：看门狗在跑的时候，单独 `pkill uvicorn` 是停不掉服务的（这是设计如此）。要彻底停必须两个都杀。

**如果要重新打开自启**（例如重置过系统之后）：

```bash
bash /root/autodl-tmp/jev-app/fix_boot_hook.sh
```

---

## 四、公网地址与密钥

**公网地址（已实测可用）：**

```
https://u1201184-80c0-dda3c400.westb.seetacloud.com:8443
```

这是 AutoDL「自定义服务」把实例内 **6006** 端口映射出来的地址。若以后端口或实例变化，
可在控制台重新获取：容器实例 → 你的实例 → **自定义服务** → 复制 **6006** 对应的地址。

密钥：

```bash
cat /root/autodl-tmp/jev-api-key.txt
```

请求需携带密钥，任选一种：

```
Authorization: Bearer <key>
X-API-Key: <key>
```

（要临时关闭鉴权，用 `JEV_API_KEY= bash start.sh` 启动即可。）

---

## 五、把服务交给别人使用

**要给的 4 样东西：**

| # | 内容 | 说明 |
|---|---|---|
| 1 | 公网地址 `https://u1201184-80c0-dda3c400.westb.seetacloud.com:8443` | 固定不变，除非重建实例 |
| 2 | API 密钥 | 等同账号凭证，建议**单独发**，别和文档放在一起 |
| 3 | `使用说明（转发给对方）.md` | 对方照着自己就能调通 |
| 4 | 输入限制 | 已写在该文档里 |

**绝对不要给的：**

- ❌ SSH 登录信息（`ssh -p 15805 root@connect.westb.seetacloud.com` + 密码）—— 那是整台机器的 root 权限，对方能删模型、装挖矿程序、看你的数据。给 API 就够了。
- ❌ AutoDL 账号密码
- ❌ 服务器上的任何路径、脚本、密钥文件

**成本风险：**

- AutoDL **按小时计费**，实例开着就在烧钱，与是否有人调用无关。不用时记得关机。
- 目前**没有速率限制和配额统计**：拿到密钥的人可以随意高频调用。如果将来要对外开放给多人，建议加多密钥、速率限制与用量统计。
- AutoDL **连续关机 30 天会释放实例，数据全部清空**。届时用 `/root/autodl-tmp/jev-app/` 里的脚本可以整套重装。

---

## 六、接口与实测表现

服务只有一条路线：**Jev-Omni 模型**，用于菜品识别和通用提问。

| 用途 | 接口 | 显存 |
|---|---|---|
| **菜品识别** | `/v1/identify` | 13 GiB |
| 通用提问 | `/v1/ask`、`/v1/decide*` | 13 GiB |

菜品识别实测：8 道清单 **8/8**，12 道 7/8，40 道 6/8。

### `GET /health`

```json
{"status":"ok","loaded":true,"quantization":"8bit",
 "backbone":"model.language_model","vram_used_gib":12.8,"vram_total_gib":23.64}
```

### `POST /v1/decide` — JSON

| 字段 | 类型 | 说明 |
|---|---|---|
| `state` | string | 情境描述（必填） |
| `question` | string | 一个问题（必填） |
| `options` | string[] | 2–256 个候选答案（必填） |
| `modality` | string | `text` / `image` / `audio` / `video`，默认 `text` |
| `media_base64` | string | base64 媒体，支持 `data:image/png;base64,...` |
| `media_url` | string | 或给一个 HTTP(S) 地址，服务器代为下载 |
| `video_frames` | int | 视频抽帧数，默认 16 |

返回：

```json
{"prediction":"否","prediction_index":1,"confidence":0.999301,
 "probabilities":{"是":0.000699,"否":0.999301},
 "modality":"text","latency_ms":1142.7}
```

### `POST /v1/decide/upload` — 表单上传

`multipart/form-data`：`state`、`question`、`options`（每行一个）、`modality`、`file`。

### `POST /v1/menu` — 保存候选清单

菜品识别要反复用同一份菜名清单，存一次即可，之后只传清单名字。

```bash
curl -s -X POST "$URL/v1/menu" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"本店菜单","items":["红烧肉","小笼包","宫保鸡丁","麻婆豆腐"]}'
```

`GET /v1/menu` 列出全部清单，`DELETE /v1/menu/{name}` 删除。

### `POST /v1/identify` — 菜品识别（照片 + 候选清单 → 菜名）

这就是「把可能的菜品种类告诉它」的用法，表单上传：

| 字段 | 说明 |
|---|---|
| `file` | 菜品照片（必填） |
| `menu` | `/v1/menu` 存好的清单名 |
| `options` | 或内联候选菜名，每行一个（与 `menu` 二选一） |
| `include_none` | 追加「以上都不是」选项，用于照片里的菜可能不在清单里时 |
| `repeat` | 同一组选项轮换顺序跑 N 次取平均（1–8），更稳但更慢 |
| `top_k` | 返回前几名，默认 10 |
| `question` / `state` | 一般不用改 |

返回：

```json
{"dish":"麻婆豆腐","confidence":0.997,"margin":0.996,"abstained":false,
 "ranking":[{"option":"麻婆豆腐","probability":0.997},
            {"option":"宫保鸡丁","probability":0.001}],
 "order_variation":0.0,"passes":1,"latency_ms":274}
```

`confidence` 是选中那一道的概率，`margin` 是领先第二名的差距，**低置信度就当没认出来**。

### `POST /v1/ask` — 其它用途的通用入口

模型只会「从给定选项里挑一个」，不会自由生成文字，所以任何新用途都是**一句话问题 + 候选答案**：

```bash
curl -s -X POST "$URL/v1/ask" -H "Authorization: Bearer $KEY" \
  -F "question=这盘菜吃完了吗？" -F $'options=吃完了\n还剩一半\n几乎没动' \
  -F "file=@/path/to/plate.jpg" -F "modality=image"
```

`options` 留空默认 `是/否`；`modality` 可为 `text` / `image` / `audio` / `video`。

### `GET /ui` — 网页版

浏览器打开 `<公网地址>/ui`，填一次密钥（存在本机浏览器），拖照片进去就能看结果，不用写代码。

### `GET /docs`

FastAPI 自动生成的交互式文档，可以直接在浏览器里点选测试。

### 客户端：只有一份

**后端自带的那份就是唯一的客户端**：`modules/backend/app/jev.py` 的 `JevClient`。
它覆盖 `/v1/identify`、`/v1/ask`、`/v1/menu`、`/health`，带指数退避重试、单卡串行信号量，
以及 `JEV_API_KEY` 为空时的离线降级（模型挂了业务照跑）。

外部调用方不方便引后端代码时，用 curl 或任意 HTTP 库即可 ——
接口定义见本目录 [`openapi.json`](openapi.json)（从线上服务器导出），
示例见 [`使用说明（转发给对方）.md`](使用说明（转发给对方）.md)。

> 这里**刻意不放第二份客户端**。同一套 API 两份实现，就会有两套重试语义和两个各自漂移的
> 契约；接口一变就得记住改两处。联调自检（`modules/integration/check_jev_contract.py`）
> 用的也是后端那份真实客户端，测的就是线上路径。

---

### 菜品识别准确率（9 张真实菜品照片实测）

用维基百科的真实菜品照片测，不是合成图：8 张目标菜 + 1 张咖啡（不在清单里的干扰项）。

| 候选清单规模 | 正确率 | 备注 |
|---|---|---|
| **8 道菜** | **8/8 = 100%** | 置信度 0.91–0.998 |
| 12 道（加了饺子/馄饨等相似菜） | 7/8 = 87.5% | 饺子→馄饨，置信度 0.80，**自信地错** |
| **40 道菜**（真实菜单规模） | **6/8 = 75%** | 糖醋里脊→宫保鸡丁、饺子→馄饨 |
| 40 道 + 「以上都不是」 | 7/8 = 87.5% | 加弃权项反而救回一道 |

**置信度是可靠的过滤器**（40 道菜那组）：置信度 ≥ 0.78 时 5/5 全对；< 0.40 时只有 1/3 对。

实战建议：

1. **清单越短越准**：8 道 100%，40 道 75%。店里菜多就先分档（肉类 / 蔬菜 / 主食 / 点心）再在档内选。
2. **置信度低于 0.6 就当没认出来**，转人工或缩小清单重试。
3. `include_none` 是双刃剑：咖啡那张被正确拒掉（0.988），但**一张真的宫保鸡丁照片也被误拒**（0.973）。清单确定时别开。
4. 错误集中在**长得像的菜**（饺子/馄饨、糖醋里脊/宫保鸡丁）。这是视觉相似度问题，加长清单救不回来。
5. `repeat=3` 轮换选项顺序取平均，排名更稳，但会把置信度整体压低（实测同一张图 0.951 → 0.692），阈值要相应下调。

---

## 七、调用示例

**文本**

```bash
KEY=$(cat /root/autodl-tmp/jev-api-key.txt)
URL=https://u1201184-80c0-dda3c400.westb.seetacloud.com:8443

curl -s -X POST "$URL/v1/decide" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"state":"会议 10 点开始，现在是 9 点。","question":"会议开始了吗？","options":["是","否"]}'
```

**图片上传**

```bash
curl -s -X POST "$URL/v1/decide/upload" \
  -H "Authorization: Bearer $KEY" \
  -F "state=一张简单背景上有一个居中图形。" \
  -F "question=这个图形是什么颜色？" \
  -F $'options=红色\n蓝色\n绿色' \
  -F "modality=image" \
  -F "file=@/path/to/picture.png"
```

**Python**

```python
import requests

BASE = "https://u1201184-80c0-dda3c400.westb.seetacloud.com:8443"
KEY = open("/root/autodl-tmp/jev-api-key.txt").read().strip()
headers = {"Authorization": f"Bearer {KEY}"}

# 纯文本
r = requests.post(f"{BASE}/v1/decide", headers=headers, json={
    "state": "客户投诉被重复扣款，客服已退款 29 美元，客户回复「收到，都解决了，谢谢」。",
    "question": "问题是否真的得到了解决？",
    "options": ["是", "否"],
}, timeout=120)
print(r.json())

# 视频（服务器按 16 帧采样）
with open("clip.mp4", "rb") as fh:
    r = requests.post(
        f"{BASE}/v1/decide/upload",
        headers=headers,
        data={"state": "一段短视频。", "question": "画面中有运动吗？",
              "options": "有\n没有", "modality": "video"},
        files={"file": ("clip.mp4", fh, "video/mp4")},
        timeout=300,
    )
print(r.json())
```

---

## 八、实现要点与踩过的坑

这些不是常规配置问题，换机器重装时会再遇到，故记录在此。

1. **权重下载会卡死（Xet 存储）**
   该 checkpoint 存放在 HuggingFace 的 Xet 存储上，`hf-mirror.com` 只会 302 转发到
   `cas-bridge.xethub.hf.co`，而 `huggingface_hub` 2.x 默认走 Xet 协议（`cas-server`），
   在境内实例上连不通 —— 表现为下载在 **40 MiB 处永久停滞**。
   解决：绕过 huggingface_hub，用 **aria2c 16 连接**直连 Xet CDN。

2. **单线程慢 8 倍**
   `hf-mirror` 单流仅 381 KB/s（下 24GB 要 18 小时）；AutoDL 学术加速代理 1.5 MB/s（4.4 小时）。
   aria2c 16 连接实测 **12 MB/s**，约 30 分钟完成。

3. **`tie_word_embeddings` 会破坏 int8 量化**
   配置里 `tie_word_embeddings: true`。若让 `lm_head` 被量化成 int8，随后的权重绑定会用
   BF16 的 `embed_tokens` 覆盖它，int8 的 `CB` 属性随之丢失，**每次前向都报**
   `AttributeError: 'Parameter' object has no attribute 'CB'`。
   解决：把 `lm_head` 一并排除出量化（权重本就共享，不额外占显存）。

4. **torch 被 torchvision 悄悄升级**
   `torchvision` 的元数据精确依赖某个 torch 版本，不 pin 就会把 torch 顶到最新。
   两个必须一起 pin（本机最终为 torch 2.14.1 + torchvision 0.29.1，均为 cu126）。
   驱动 550 原生是 CUDA 12.4，靠 CUDA 12.x 次版本兼容跑 cu126，已验证可用。

5. **只量化文本塔**
   整个 checkpoint 22.277 GiB 中 `model.language_model` 占 22.179 GiB，
   视觉/音频只是 11 个嵌入投影张量（合计 0.098 GiB）。量化它们省不到 100 MB，
   却会损伤图像/音频质量，因此排除。

---

## 九、限制

- 单卡串行推理：服务内部用锁保证同一时刻只有一个前向。要更高吞吐请排队调用，不要并发压测。
- 音频最长 30 秒；视频固定抽 16 帧；选项 2–256（官方建议 ≤20 才保证质量）。
- 上传文件上限 200 MB。
- 音频管道已验证可用，但模型对**合成正弦波**判为「不是稳定音调」。真实语音/音乐建议实测确认。

---

## 十、完整重装步骤

```bash
# 1) 建环境（约 10 分钟）
bash /root/autodl-tmp/jev-app/setup_env.sh

# 2) 下权重（约 30 分钟，16 连接）
bash /root/autodl-tmp/jev-app/fetch_model.sh

# 3) 校验 + 四模态冒烟测试
bash /root/autodl-tmp/jev-app/verify_model.sh
bash /root/autodl-tmp/jev-app/run_all.sh

# 4) 起服务
bash /root/autodl-tmp/jev-app/start.sh

# 5) 接口自测
bash /root/autodl-tmp/jev-app/api_test.sh
```

---

## 十一、故障排查

| 现象 | 处理 |
|---|---|
| `/health` 一直是 `loading` | 载入约 40 秒，看 `logs/api.log` |
| 启动报显存不足 | `nvidia-smi` 看是否有残留进程；或改 `JEV_QUANTIZATION=4bit` 重启 |
| 音频报 ffmpeg 错误 | `source venv/bin/activate && ffmpeg -version` |
| 公网打不开 | 确认用的是控制台「自定义服务」里 **6006** 的地址；再看 `pgrep -af uvicorn` 与 `logs/watchdog.log` |
| 反复重启 | 看 `logs/watchdog.log` 的重启记录和 `logs/api.log` 的报错；模型载入失败通常是显存被别的进程占了 |
| 实例重启后失联 | 已配置开机自启；若仍失联，手动 `bash /root/autodl-tmp/jev-app/start_watchdog.sh`，并重跑 `fix_boot_hook.sh` |
| 重置系统后全丢 | 按第十节重装（数据盘若保留则只需重装环境） |
