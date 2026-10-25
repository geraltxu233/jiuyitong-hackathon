# Agent Stack 能力全览 · 与 Deotaland 逐项对照

> 2026-10-23 | 来源：`mem9-ai/tidblink-dev-guide`（提交 `d051412`，2026-10-21，与上次读取一致）
> 对照对象：Deotaland 平台 OpenAPI（60 paths，见 `平台OpenAPI盘点_2026-10-23.md`）

---

## 一、一句话

**Agent Stack 强在「可编程的智能体层」，Deotaland 强在「设备与固件层」。** 两者共用同一套实时语音协议（`tidb-link.realtime.v1`），说明底层同源，但暴露的接口面完全不同。

---

## 二、Agent Stack 的对象模型

四层嵌套，理解这个就理解它全部能力：

```text
Organization（组织）
  └─ Agent Template（模板，组织内共享：指令 / Sandbox / Skill / 语音语言 / Connector 引用）
       └─ Agent（一个终端用户 与 一个模板 的绑定）
            └─ Session（会话，创建时冻结模板内容，文本与语音共用）
                 └─ Turn（一次执行，事件持久化并通过 SSE 推送）
```

关键规则（来自 `architecture.md`）：

| 规则 | 后果 |
| --- | --- |
| 凭据决定权限范围 | 组织密钥只做开通，不能以用户身份操作 |
| **Session 冻结模板** | 改模板/加 Connector 后，必须新建 Session 才生效 |
| 发送与观察分离 | POST 只返回回执，结果只能从 SSE 或 Turn 历史读 |
| 文本与语音共用一个 Session | 语音不创建 Session，委派的工作变成普通 Turn |
| 设备由你管理 | 平台不认识你的设备，设备服务器负责映射与去重 |

---

## 三、能力清单（按"能做什么"列）

### 3.1 定义智能体

| 能力 | 细节 |
| --- | --- |
| Agent Template | 指令、Sandbox Profile、Skill 引用、Realtime 语言、Connector 引用；存盘即生效但只影响**之后**创建的 Session |
| **Skill** | ZIP 包（根目录 `SKILL.md`）→ 上传 → 异步校验 → 发布 → 轮询拿版本号；模板按 `{skillId, version}` 引用 |
| Sandbox 选择 | 从 `agent-template-reference-catalog` 选 `sandboxes[].id`，如 `standard-v1` |

### 3.2 配置用户

| 能力 | 细节 |
| --- | --- |
| End User | 组织内对象，用 `externalId` 映射你的应用用户 |
| User API Key | `ti_user_…`，只返回一次，可列出、可轮换 |
| 模型目录 | `GET /agent-models` 返回 `effectiveAllowedModels`，必须显式选模型 |

### 3.3 接入设备（Connector = 你写的 MCP 服务器）

| 能力 | 细节 |
| --- | --- |
| 注册 | `POST /org/connectors`，`connectorKey` 只返回一次 |
| 两种传输 | `streamable_http`（公网 HTTPS，推荐）／ `tunnel`（主动连 `/api/v1/connectors/ws`，但只对本副本可见） |
| 激活 | 发现并冻结工具目录（上限 200 个工具，单 schema ≤64 KiB） |
| **调用上下文** | 每次 `tools/call` 带 `callId / sessionId / organizationId / deadline / attempt` |
| 授权规则 | 身份只能来自你自己的 `sessionId → 用户 → 设备` 表，**不能从工具参数取** |
| 凭据轮换 | 停用期间 `rotate`，用 `expectedCurrentVersion` 乐观锁 |

**这是 Agent Stack 最有价值的一块**：它让 Agent 能调用**你自己写的任意服务**，而你那个服务可以调腾讯地图、查数据库、发 MQTT。

### 3.4 文本对话

| 能力 | 细节 |
| --- | --- |
| Session + SSE | 先订阅 `GET …/events?stream=true`，再 `POST …/events` 提交 |
| 事件类型 | `turn.started` / `output_text.snapshot`（可替换草稿）/ `progress` / `item.added`（含 `operation_step` 工具调用）/ `completed` |
| 断点续传 | 把最后一帧 `id` 作为 `cursor` 重连 |
| 取消 | 关流**不**取消 Turn，要显式发 `InputCancelEvent` |

### 3.5 语音对话

| 能力 | 细节 |
| --- | --- |
| 开启 | 后端 `POST …/realtime`，拿 `websocketUrl` + 60 秒一次性 `connectToken` |
| 格式 | 输入 PCM16 8k/16k 或 Opus 16k；输出 PCM16 8k/16k/24k 或 Opus 24k |
| 流控 | `output_flow_control_v1`（仅 Opus 输出），设备按播放进度给额度，防缓冲打爆 |
| 打断 | `playback.stopped` + `reason: user_interrupt`，或 `input_audio.clear` |
| 重连 | `POST …/realtime/connect-tokens` + `resume_after_event_id` |

### 3.6 工程严谨性（容易被忽略但很值钱）

| 能力 | 细节 |
| --- | --- |
| 幂等 | `Idempotency-Key` 头 + `clientEventId`，同 key 同体重放返回原结果，同 key 异体返回 409 |
| 并发控制 | 模板 `PUT` 要 `If-Match: <ETag>`，过期 412 |
| 分页 | `hasMore` + `nextCursor`，游标不透明且有作用域 |
| 重试 | 平台**不重试** Connector 调用；`timeout`/`result_unknown` 要自己按 `callId` 核对 |

### 3.7 明确不在指南范围内的

平台 Hardware（Hardware Models / 库存 / Registration / Binding / `/hardware/connect`）、Client Tools、MCP Server 注册、Scheduler、Drive 文件、Billing。

**注意**：契约里有这些，但指南说"不要用平台 Hardware 接设备，走 Connector"。

---

## 四、和 Deotaland 逐项对照

| 能力 | Agent Stack | Deotaland | 谁强 |
| --- | --- | --- | --- |
| 智能体定义 | Template（组织共享）+ 实例指令 | agent-type + `prompt`（10 万字） | 持平 |
| 规则可版本化 | **Skill（ZIP + 版本号 + 回滚）** | 只有一段 prompt | **Agent Stack** |
| 工具/函数调用 | **Connector（MCP，可调你的服务）** | 无 | **Agent Stack** |
| 文本对话 | Session + SSE 细粒度事件 | `execute(stream)` + conversations/messages | Agent Stack 更细 |
| **独立 ASR / TTS** | 无独立端点 | **`execute` 支持 asr / tts / vlm** | **Deotaland** |
| 实时语音 | WSS `tidb-link.realtime.v1` | **同一协议**（SDK 硬编） | 相同 |
| 文件 | Drive9（指南划在范围外） | **drive 直传，硬件可直用** | **Deotaland** |
| 设备管理 | 平台 Hardware（指南不建议） | **devices / bind-agent / OTA / MQTT 调试 token** | **Deotaland** |
| 幂等与并发控制 | **完整（key / ETag / cursor）** | 未见 | **Agent Stack** |
| 多租户模型 | Organization + End User + User Key | enterprise + virtual-users | 各自适配 |

---

## 五、所以它能补你什么

### 能补的，只有两样，但都很关键

**1. 规则可版本化（Skill）**

现在你的导诊规则是一段 prompt，改了没法回滚、没法 diff、没法给别人看"上一版怎么写的"。Skill 是 ZIP + 版本号，改坏了能退回去。对"不能瞎说"的医疗场景，这个价值不小。

**2. 工具调用（Connector）—— 这是需求二的另一个解**

这是之前没想到的：**Connector 让你自己的 MCP 服务器变成 Agent 的工具**。于是：

```text
老人说"心脏不舒服，怎么去医院"
   ↓
Agent 判断科室（Skill 里的规则）
   ↓
Agent 调工具 plan_transit(from, to)     ← Connector
   ↓
你的 MCP 服务器 → 腾讯网络定位 + 公交规划 API
   ↓
返回 3 行文本 → Agent 说出来 + 上屏
```

**这条路不需要改一行固件。** 之前判断"只能设备直连腾讯"，是因为 Deotaland 没有工具层；Agent Stack 有。

### 补不了的

设备管理、OTA、MQTT 调试 token、ASR/TTS 单点调用——这些 Deotaland 有而 Agent Stack 没有（或划在范围外）。切过去会丢掉你现在已经验证通过的那一整套。

---

## 六、两条路的真实代价对比

针对"想做腾讯导航"这一个具体需求：

| | 留在 Deotaland | 切到 Agent Stack |
| --- | --- | --- |
| 设备侧改动 | **大**：加 WiFi 扫描 + 非平台 HTTPS + JSON 解析 + 上屏 | **零** |
| 平台侧改动 | 无 | 大：改 URL、注册 product、重新激活绑定 |
| 后端改动 | 无 | 中：写一个 MCP 服务器（Node/Python） |
| 关键卡点 | WiFi 扫描与语音通话争资源；TLS 内存不够要错开 | **Connector 挂模板必须 Console 手动点**，且官方从未端到端验证过阶段 3 |
| 已通链路风险 | 不动，安全 | **要重走配网→激活→绑定，可能全部打回** |

**我的判断**：如果演示只剩一天，**两个都不建议做**，先把导诊话术做扎实。如果还有两天，选**留在 Deotaland + 设备直连**，因为它不赌未验证的平台能力。

Agent Stack 的工具能力确实更优雅，但它的阶段 3 是这份指南里唯一"官方承认没验证过"的部分。

---

## 七、如果要用 Agent Stack，最小路径

按依赖顺序，每一步都有卡点：

| 步 | 动作 | 卡点 |
| --- | --- | --- |
| 1 | 确认部署可用（`GET /api/v1/org`）+ 有 Console 模板编辑权限 | **不在你手里** |
| 2 | 建 Agent Template，`realtime: {"language":"zh-CN"}` | 要组织 API 密钥 |
| 3 | 写 MCP 服务器（腾讯定位 + 公交两个工具） | 你写，工作量中 |
| 4 | 注册 Connector → 激活 → Console 手动挂到模板 | **必须人工，且官方未验证** |
| 5 | 改设备 `platform_config.h` → 重建 → 重烧 → 重新配网激活 | 已通链路要重来 |
| 6 | 新建 Session（模板冻结，旧 Session 看不到新 Connector） | 容易忘 |

---

## 八、结论

- **Agent Stack 的能力清单里，对你真正有增量的只有 Skill 和 Connector 两项。**
- Connector 能解决"Agent 调腾讯 API"，代价是全平台迁移 + 一个未验证的挂载步骤。
- Deotaland 缺的是"工具层"，但它把你现在最需要的设备层和语音层都做完了。
- **两天内不要为了一个功能做全平台迁移。** 先把话术和演示脚本做稳，导航功能当作加分项，用设备直连的方式做，失败就砍。

---

## 九、常见误解澄清：是三层，不是两层

容易理解成"agent_link 有现成机器人 / agent_stack 要自己建"，因果其实是倒的。

```text
① Agent_link        固件 SDK。提供连接、音频通道、屏幕、按键。
                    本身没有大脑，是个"空壳 + 声带 + 嘴 + 屏"。
                          ↓ 编译进固件的 URL 指向谁，就连谁
② Deotaland 控制台   现成的租户层。已经替你建好 agent（就医通），
                    你只填名字/人设/参数、扫码绑设备，就能对话。
                    ← 这才是"开箱即用"的来源
                          ↓ 它的能力通道是
③ Agent Stack       底层平台 API。给的是原材料：模板、Agent、Session、Turn、SSE。
                    控制台替你做的每一步，在这里都要自己写代码。
```

| | 谁提供 | 你得到什么 |
| --- | --- | --- |
| Agent Link | 固件 | 设备会听、会说、会显示；但不知道跟谁说话 |
| Deotaland 控制台 | 预置 agent + 配置 UI | 一个能直接对话的机器人（就医通） |
| Agent Stack | API | 能自己建、自己控、自己加工具的智能体，但没有 UI |

**关键证据**：控制台上 `LLM = agent_stack / live-chat`、`REALTIME_VOICE = agent_stack / live-real`。

也就是说 **你现在这个「就医通」的模型和语音通道，本来就是 agent_stack**。你并不是"没有 agent_stack"，而是"已经在用它的模型能力，只是没拿到它的 API 控制权"。

这也解释了为什么控制台没有 Skill / 工具入口——Deotaland 只把 agent_stack 的一小部分做成了 UI（模型通道选择 + prompt）。

**打个比方**：Agent Stack 像云厂商的 API，Deotaland 控制台像预装了模板的 SaaS 面板。同一个后端，两个入口。

所以"要不要重建 agent"的准确答案是：

- **要重建的是"管理方式"，不是"内容"**：人设（prompt）可以原样搬到 Template 的 `instructions`；科室表、边界、输出格式全部复用。
- **固件不用重建**：板子已经会连 WSS、会 Opus、会显示，切过去只需改 URL + product 注册 + 重新激活。
- **要自己做的是会话管理**：控制台上是点菜单选会话，Agent Stack 上要自己建 Session、自己订阅 SSE。本质一样，只是没人给你 UI。
