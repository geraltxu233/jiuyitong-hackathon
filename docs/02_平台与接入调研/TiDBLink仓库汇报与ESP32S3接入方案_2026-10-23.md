# TiDB Link 开发工具包：仓库汇报 + ESP32-S3 接入 AI 大脑方案

> 调研日期：2026-10-23 | 仓库：https://github.com/mem9-ai/tidblink-dev-guide
> 对照文件：INTERFACE_联调接口协定_2026-10-22.md、任务拆分_老人导诊卡套_2026-10-22.md

---

## 0. 一句话结论

这个仓库**不是一个可以直接烧进固件的 SDK**，而是一套"给 AI 编程助手读的接入说明书 + OpenAPI 契约"。它定义的架构里，**ESP32-S3 永远不能持有平台密钥、也不能直连平台 REST**；它能走的路只有两条：①直连 Realtime WebSocket 传音频（凭一次性 60 秒 token）；②作为工具被 Agent 间接调用（经由你自己写的设备服务器 / Connector）。把 TiDB Agent 当"大脑"可行，但**大脑到手、手脚要自己长**——工作量的大头不在板子上，在你那台设备服务器上。

---

## 1. 仓库里到底有什么

### 1.1 仓库性质

| 项 | 判断 |
| --- | --- |
| 类型 | 文档 + Skill 包（Markdown / YAML / SVG），**零可执行代码、零 SDK** |
| 目标读者 | 第一读者是 AI 编程助手（`SKILL.md` 是给 Agent 的入口指令），第二读者才是人 |
| 唯一代码内容 | `protocols/connectors.md` 里两段 TypeScript 骨架（streamable_http / tunnel 各一），是集成骨架不是可运行工程 |
| 创建 / 最后推送 | 2026-08-27 / 2026-10-21 |
| 提交数 | 4 次（初始 → 按新 API 重写 → 修 Connector 阻塞问题 → 补中文版） |
| Star / Fork / License | 0 / 1 / **无 license**（`/license` 返回 404） |

### 1.2 文件清单与用途

| 路径 | 大小 | 用途 | 对你的价值 |
| --- | --- | --- | --- |
| `SKILL.md` | 7.8 KB | AI 助手入口：文档路由、凭据表、范围边界、"哪些能力不在这份指南里" | **最高**。全是踩坑结论，比 OpenAPI 有用 |
| `README.zh-CN.md` | 5.1 KB | 架构图 + 6 阶段总表 + 安全模型 | 高，10 分钟能建立全局观 |
| `references/zh-CN/architecture.md` | 4.6 KB | 谁能持有哪把密钥、模板→会话的冻结规则、5 条架构推论 | **最高**。决定了板子能做什么不能做什么 |
| `references/zh-CN/development-path.md` | 19.9 KB | 阶段 0–5，共 23 步，每步含 curl 与"完成标准" | **最高**。照抄的施工图 |
| `references/zh-CN/protocols/connectors.md` | 20.2 KB | Connector（你的设备服务器）：MCP 协议、调用上下文、错误码、tunnel 多副本限制 | **最高**。硬件控制唯一合法通道 |
| `references/zh-CN/protocols/realtime-voice.md` | 30.2 KB | `tidb-link.realtime.v1` 语音协议：音频格式、二进制帧、流控、打断、重连 | **最高**。板子要直接实现这个 |
| `references/zh-CN/reliability.md` | 7.3 KB | 幂等键、ETag、SSE 断点续传、重试判定、Turn 失败码 | 中，后端侧用 |
| `references/zh-CN/upstream.md` | 2.4 KB | 契约固定在上游 `a32727a`，含 3 条已知边界核查 | 高，说明"文档说的 ≠ 你部署的版本" |
| `references/openapi.yaml` | 905 KB | 完整 `/api/v1` 契约（英文） | 中，查字段时用 |
| `references/api-reference.html` | 1.6 MB | 同一契约的 ReDoc 可视化（英文） | 低，人肉翻查用 |
| `references/zh-CN/diagrams/*.svg` | 12–17 KB | 架构图、流程图（中英各一套） | 低 |

### 1.3 仓库真正在教的东西

TiDB Link 的平台能力被拆成 6 个阶段、23 步：

| 阶段 | 你要做的事 | 用到哪把钥匙 |
| --- | --- | --- |
| 0 准备 | 组织所有者在 Console 签发组织密钥；运维确认 Sandbox / Drive9 / 模型已配好 | Console 手动 |
| 1 定义智能体 | 建 Skill（可选）+ 建 Agent Template，拿到 `templateId` 与 `ETag` | 组织密钥 `ti_org_…` |
| 2 配置用户 | 建 End User + 签发 User API Key | 组织密钥 |
| 3 接入设备 | 写 MCP 设备服务器 → 注册为 Connector → 激活 → **Console 手动挂到模板** | 组织密钥 + Console |
| 4 文本对话 | 建 Agent → 建 Session → 订阅 SSE → 发消息 → 渲染事件 | 用户密钥 `ti_user_…` |
| 5 语音对话 | 后端开 Realtime → 拿 token → 客户端连 WS → 传音频 | 用户密钥 + 一次性 token |

**注意阶段 3 的最后一步只能人工在 Console 点**，OpenAPI 明确拒绝 `content.tools`（返回 400）。这是整个方案里最硬的一个"人为卡点"。

### 1.4 文档自己承认的空白（很重要）

> "腾讯云验证中完成了文本和语音，但因缺少 Console 权限，**未完成设备工具调用**。"

也就是说：**"Agent 通过 Connector 控制硬件"这条链路，官方文档里没有端到端验证记录。** 你如果把它作为演示主线，就是在替官方做首次验证。

另外 `SKILL.md` 明确划出范围外能力：平台 Hardware（Hardware Models / 库存 / Registration / Binding / `/api/v1/hardware/connect`）、Client Tools、MCP Server 注册、Scheduler、Drive、Billing——这些不在指南覆盖内，不要试图从这些路子接设备。

---

## 2. 架构骨架：三条硬规则

```
     ┌──────────────┐   ① 音频（TLS/WS，只带一次性 token）   ┌────────────────┐
     │  ESP32-S3    │ ────────────────────────────────────▶ │                │
     │  墨水屏/麦克风 │ ◀──────────────────────────────────── │   TiDB Link    │
     └──────┬───────┘        媒体面：设备直连               │    平台        │
            │                                              │                │
            │ ② 设备协议（MQTT / WS / 裸 TCP，你自己定）      └───────┬────────┘
            │                                                      │ ③ MCP
            ▼                                                      │   tools/call
     ┌──────────────────┐   ④ REST 代理（持组织/用户密钥）           │
     │  你的设备服务器   │ ◀──────────────────────────────────────────┘
     │  = 应用后端      │ ──▶ 平台 /api/v1/**（建 Agent、建 Session、开 Realtime 换 token）
     │  = MCP Connector │
     └──────────────────┘
```

| 规则 | 内容 | 对板子的直接后果 |
| --- | --- | --- |
| R1 密钥不上设备 | 组织密钥只给后端；用户密钥只给后端；设备最多拿 60 秒一次性的 `connectToken` | 固件里**不允许**出现 `ti_org_` / `ti_user_`；烧进去就是设计错误 |
| R2 设备不直连 REST | `/api/v1/org/**` 和 `/api/v1/user/**` 全部由你的后端代理 | 板子要发音频，得先向你后端要 token；后端不在线 = 语音不可用 |
| R3 控制必须间接 | v1 已移除 `device.command` / `device.command_result`，硬件动作只能由 Agent 调 Connector 工具触发 | 屏幕刷新是**异步的**（Full Agent Turn 秒级），不是"说完立刻变" |

R3 是最容易被忽略、也最容易翻车的认知差。他们的演讲话术里如果说"AI 直接控制硬件"，技术上要诚实表述为"AI 决定内容 → 你的服务器下发 → 硬件执行"。

---

## 3. ESP32-S3 侧两条链路怎么拆

### 3.1 媒体面：设备 ⇄ Realtime WebSocket（直连）

这是唯一允许板子直连平台的通道。握手流程：

1. 设备向你的后端 `POST /api/device/realtime-token`（设备级鉴权，不要用用户密钥）
2. 后端用用户密钥 `POST /api/v1/user/sessions/{sessionId}/realtime` → 返回 `websocketUrl` + `connectToken` + 到期时间
3. 后端把这三个字段原样转给设备
4. 设备连 `wss://...`，子协议 `tidb-link.realtime.v1`，**10 秒内**发首帧：
   ```json
   {"type":"session.connect","connect_token":"<token>","endpointing":{"mode":"client"},"capabilities":["output_flow_control_v1"]}
   ```
5. 等 `session.ready`，按它返回的 `audio.input` / `audio.output` 实际值配采集与播放
6. 传音频 → 收 `transcript.final`（文本）和音频块

**音频选型（关键决策，按你的板子算过账）：**

| 方向 | 建议格式 | 带宽 | 为什么 |
| --- | --- | --- | --- |
| 上行（麦克风→模型） | `pcm16` 16000 Hz 单声道，20 ms = 640 B/帧 | 32 KB/s | 省掉 S3 上 Opus 编码器的 CPU 与调参时间；32 KB/s 对 WiFi 无压力 |
| 下行（模型→喇叭） | `opus` 24000 Hz，`packetDurationMs: 20`，`bitrate: 16000` | 每包 40 B，20 包/秒 = **2 KB/s** | 相比 24 kHz PCM16 的 48 KB/s，内存与带宽降 24 倍；S3 解码 24 kHz 单声道可行 |

下行必须配 `capabilities: ["output_flow_control_v1"]`（**只对 opus 输出生效**）。没有流控时，模型生成速度远快于播放速度，长回复会把缓冲区打爆（协议上限：1500 包 / 256 KiB / 8 个响应，超了直接取消该次生成）。

注意：`capabilities` 是较新字段，**旧版服务端会直接 `invalid_event` 断连**。上线前先确认服务端版本，别在演示前 1 小时才发现。

**二进制帧格式（自己拼，没有库）：**

| 字节 | 上行 | 下行 |
| --- | --- | --- |
| 0 | `0x01` | `0x02` |
| 1..4 | uint32 帧计数器，**大端** | uint32 `chunk_index`，大端 |
| 5.. | 音频载荷 | 音频载荷 |

注意坑：帧头是大端，但 PCM16 样本是小端。两个字节序不一样。

### 3.2 控制面：Agent ⇄ Connector ⇄ 设备

设备服务器要做两件事，写在同一个进程里最省事：

1. **MCP 服务器**：暴露工具（如 `show_department_card`、`show_transit_route`、`play_tts`），注册成 Connector
2. **REST 代理**：持有组织密钥/用户密钥，替设备与板子调用 `/api/v1/**`

每次 `tools/call` 携带调用上下文，在 `params._meta["tidb-link/context"]`：

```json
{"callId":"op_01JAB…","sessionId":"sess_…","organizationId":"org_…","deadline":"2026-…Z","attempt":1}
```

| 字段 | 怎么用 |
| --- | --- |
| `sessionId` | **唯一可信的身份来源**。用它查你自己的表 `sessionId → 应用用户 → 设备ID`，再决定推给哪块板子。绝不能从工具参数取身份，也不能在 schema 里放身份字段 |
| `callId` | 幂等键。屏幕刷新天然幂等，但 `play_tts` 这类有声副作用要按它去重 |
| `deadline` | 平台等待上限 300 秒，过了就停手 |
| `attempt` | **恒为 1**，平台不重试。`timeout` / `result_unknown` 时必须自己按 `callId` 核对副作用是否已发生 |

工具结果限制：只接受 `text` 和 `resource_link`，**图片/音频/二进制会被拒**。所以别想着让工具直接回传墨水屏图片，回 JSON 文本让设备自己渲染。

命名的坑：模型看到的工具名是 `mcp_<server>__<tool>`，小写化、非字母数字压成 `_`。

### 3.3 两条链路怎么合成一个闭环（这才是"大脑"的样子）

```
老人说："我胸口闷，心慌"
   ↓ 音频帧（媒体面，设备直连 Realtime WS）
Realtime Model 听懂 → 判断这需要查院内数据 → 委派 Full Agent 执行 Turn
   ↓ start_task（语音侧会说"我帮您查一下"）
Full Agent 用 Session 冻结的工具目录 → 调用 mcp_device__show_department_card
   ↓ MCP tools/call（带 sessionId）
你的设备服务器：sessionId → 设备 → MQTT 下发 {"lines":["心内科","门诊楼 3F 305","三楼东侧"]}
   ↓
墨水屏整屏刷新（大字），同时语音播报
```

一个 Session 同时承载两条链路，`sessionId` 是把它们缝在一起的唯一线头。

---

## 4. 关键账目（用你的板子算过）

板子：微雪 ESP32-S3-ePaper-3.97，S3R8 = 240 MHz 双核 + **8 MB PSRAM** + 512 KB SRAM，ES8311 音频，800×480 墨水屏。

| 项目 | 估算 | 结论 |
| --- | --- | --- |
| TLS 连接常驻内存 | 每条 ~40–60 KB（mbedTLS 上下文 + 16 KB 收发 buffer） | 两条连接（Realtime WS + 到你自己服务器的 MQTT）≈ 120 KB，放 PSRAM 够 |
| PCM16 上行 32 KB/s + Opus 下行 2 KB/s | 峰值 ~300 kbps | WiFi 无压力，但**要关 modem sleep**，否则抖动导致 20 ms 帧堆积 |
| Opus 解码 CPU | 24 kHz 单声道 ≈ 10–20% 单核 | 可行，用 esp-adf / esp-libopus 现成解码器 |
| I2S DMA buffer | 必须在**内部 RAM**，PSRAM 不能给 DMA | 这是 S3 上最常见的坑，留 8–16 KB 内部 RAM |
| 首响时延（按键→第一声） | 取 token 150–400 ms + 建 WS/ready 300–800 ms + 模型首包 500–900 ms | **1.5–2.5 s**，明显优于现有"录完上传→ASR→LLM→TTS"的 3–5 s |
| 控制面时延（说话→屏幕变） | Full Agent Turn，秒级 | 语音先说"帮您查一下"，屏幕随后到；不要承诺"立刻" |

**顺手优化（很值）：** 按键按下瞬间并行做两件事——开始本地录音进 ring buffer，同时请求 token + 建 WS。等 `session.ready` 后用 client endpointing 先发 `activity.start` 再把 buffer 灌进去。这样 0.5–1 s 的握手时间被藏在用户说话的过程里，主观时延接近 1 s。

---

## 5. 落地分级：两天怎么走

我的判断是：**Realtime 全链路在 2 天内一次做成的概率不到 40%**（新引入 TLS + WS + 二进制帧 + Opus + 流控 + 断线重连 6 个失败点）。所以必须分级，每级都可独立演示。

| 级别 | 做什么 | 板子改动 | 后端改动 | 风险 | 建议时点 |
| --- | --- | --- | --- | --- | --- |
| **L0 保底** | 现有 I1：录整段音频 → `POST /api/triage` → JSON → 屏 + TTS | 无（已有） | 无（已有） | 低 | 必须先绿 |
| **L1 大脑换芯** | TiDB Link 只当**文本大脑**：后端拿 agent + session + SSE，把"症状→科室+院内位置"的推理交给它；板子仍是发音频收 JSON | **零改动** | 新增：建 Agent/Session、SSE 订阅、Turn 事件解析 | 低 | Day1 晚必须绿 |
| **L2 高光** | 设备直连 Realtime WS 做语音对话 + Connector 反控墨水屏 | 大：WS/TLS/Opus/流控 | 大：token 代理 + MCP Connector + downlink | **高** | Day2 上午冲，14:00 不绿就砍回 L1 |

**为什么 L1 一定要先做：** 它只用文本 Turn，能在 2 小时内暴露"运维没配好 Sandbox / Drive9 / 模型"这类致命问题（返回 `503 service_configuration_unavailable` 或 `409 model_provider_unconfigured`）。如果环境本身不通，L2 做得再漂亮也是零。**先花 2 小时验证环境，比什么都重要。**

**L2 的砍点建议定在 Day2 14:00。** 判据不是"代码写完了"，而是"从按键说话到屏上出科室名，连续 3 次成功"。做不到就回 L1，演示话术改成"AI 大脑在云端，硬件是它的感官与显示"——依然是完整故事，只是少了语音直连的炫技。

---

## 6. 已知的坑（按严重度排序）

| # | 坑 | 严重度 | 应对 |
| --- | --- | --- | --- |
| 1 | **Connector 挂模板只能 Console 手动点**，API 提交 `content.tools` 一律 400 | 致命 | **赛前就要确认**：能不能登进 Console、是否有同组织模板编辑权限。MSP 开通的组织**不自动包含** Console 成员 |
| 2 | 阶段 3 官方**从未端到端验证**（因缺 Console 权限） | 致命 | Day0 就打一个 hello-world Connector 通一遍，别留到 Day2 |
| 3 | Sandbox / Drive9 / 模型未配 → 首条消息 503 | 高 | 让运维提前确认；L1 提前验证 |
| 4 | Session **冻结**模板内容，改模板/加 Connector 后必须**新建 Session** | 高 | 演示前不临时改模板；改了就重建 Session，并同步更新 `device → sessionId` 映射 |
| 5 | `tunnel` 传输**只对本副本可见**，文档实测 10 次激活 3 次失败 | 高 | 用 `streamable_http`。把设备服务器用 cloudflared / ngrok 暴露成公网 HTTPS（这是把 MCP 端点公网化，**不是**用 TiDB 的 tunnel 传输） |
| 6 | `capabilities` 旧服务端不认，直接 `invalid_event` 断连 | 中 | 先探服务端版本，或先不带 capabilities 跑通再加 |
| 7 | 平台**不重试** Connector 调用；`timeout`/`result_unknown` 结果未知 | 中 | 按 `callId` 去重与核对；墨水屏渲染幂等，`play_tts` 要防重 |
| 8 | 音频格式在连接生命周期内**固定**，改格式要结束重开 | 中 | 演示前定死 16k PCM16 上行 / 24k Opus 下行，别当天改 |
| 9 | token 60 秒过期 + 只能 `session.connect` 一次 | 中 | 每次连接都重新要；断线重连走 `POST …/realtime/connect-tokens` + `resume_after_event_id` |
| 10 | 无 license | 中 | 只当文档参考，不要 fork 后商用分发 |
| 11 | 组织模板 API 读取时**隐藏** Console 挂的 Connector 引用 | 中 | 别用 GET 结果做"是否已挂载"的判据，去 Console 看 |
| 12 | 工具结果拒绝图片/音频/二进制 | 低 | 只回 JSON 文本，设备端自己渲染 |

---

## 7. 对现有 INTERFACE 协定的增量（L2 才需要）

现有 `INTERFACE_联调接口协定_2026-10-22.md` 是 L0/L1 的基础，**L2 需要出 v2**，新增三块：

```text
I6. 设备取语音 token（设备 ↔ 你的后端）
POST https://<cloud-host>/api/device/realtime-token
  { "device_id": "esp32s3_001" }
  → { "websocketUrl": "...", "connectToken": "...", "connectTokenExpiresAt": "...", "sessionId": "sess_..." }
  说明：后端用用户密钥调 POST /api/v1/user/sessions/{id}/realtime，只转发这三个授权字段；
        token 60 秒有效、只能连一次，设备端不得缓存复用。

I7. 设备绑定（设备 ↔ 你的后端，开机一次）
POST https://<cloud-host>/api/device/bind
  { "device_id": "esp32s3_001" }
  → { "sessionId": "sess_..." }
  说明：后端记录 sessionId → 应用用户 → device_id，这张表是 Connector 授权的唯一依据。

I8. 下行动作（你的后端 → 设备，MQTT/WS）
{ "type": "display_card", "lines": ["心内科", "门诊楼 3F 305", "三楼东侧"], "speak": "建议您挂心内科" }
  说明：由 Connector 工具 show_department_card 触发；callId 作为幂等键。
```

同时 I1 在 L2 下退化为"降级路径"（Realtime 连不上时回落到录整段音频上传），不是删除。

---

## 8. 需要人去确认的事（不是代码能解决的）

| # | 待确认 | 找谁 | 阻塞什么 |
| --- | --- | --- | --- |
| 1 | `TIDB_LINK_BASE_URL` 是多少 | 主办方 / 运维 | 一切 |
| 2 | 组织 API 密钥能不能签发 | 组织所有者 / 主办方 | 阶段 1、2 |
| 3 | Console 账号能否登录且能编辑同组织模板 | 主办方 | **阶段 3 全部** |
| 4 | Sandbox / Drive9 / 至少一个模型是否已配 | 运维 | 首条消息（503 风险） |
| 5 | 部署是单副本还是多副本 | 运维 | 传输方式选型 |
| 6 | 部署版本是否支持 `capabilities` / `output_flow_control_v1` | 运维 | L2 内存方案 |
| 7 | 是否支持 `zh-CN` Realtime 语音与所需 endpointing 模式 | 运维 | 阶段 5 |

第 1–4 项如果赛前拿不到明确答复，**L2 直接放弃，全押 L0/L1**。这不是保守，是删掉不确定性。
