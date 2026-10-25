# Agent Stack 能力清单与对本项目的意义

> 2026-10-23 | 依据：`mem9-ai/tidblink-dev-guide`（新 `/api/v1`）、旧契约 200 条路径、`you06/esp32-agent-lcd` 示例
> 当前设备状态：已通过 Agent_link 接入 **Deotaland 平台**（WiFi + MQTT 上线，实时语音未验证）

---

## 一、先分清三层，别混

| 层 | 是什么 | 你现在的状态 |
| --- | --- | --- |
| 设备 SDK | `Deotaland/Agent_link` 固件 | **已在跑**，板子在线 |
| 平台 | 决定"AI 大脑"是谁、能配什么 | 目前是 `bot.deotaland.cn` |
| 文档仓库 | `tidblink-dev-guide` + `esp32-agent-lcd` | **不是平台，是说明书和示例** |

`Agent Stack` 是平台侧的东西（服务地址 `ventured-agent-stack.pingcap.cn`），和 Deotaland 是两个独立部署。两个 GitHub 仓库本身不提供服务，只是告诉你**怎么调这个平台**。

所以"Agent Stack 能做什么" = "如果你把大脑换成它，你能多用出什么"。

---

## 二、能力清单

`●` = 新指南正式覆盖；`○` = 契约里有但新指南明确划在范围外

| 能力 | 具体能做什么 | 对导诊项目 |
| --- | --- | --- |
| **文本对话** ● | 建 Agent → 建 Session → 发 Turn → 消费流式事件；支持历史、改名、切换 Agent | 基础，但设备端已由 SDK 封装 |
| **音频 Turn** ● | `POST /turns/audio` 上传 wav/mp3，服务端 ASR → 回复 | **最贴合**：说一句症状 → 一个结论，无需长连 |
| **实时语音** ● | WSS `tidb-link.realtime.v1` 双向音频、打断、重连 | Agent_link 已在客户端实现，切换平台即可复用 |
| **Skill** ● | 上传 ZIP 包（含 `SKILL.md`）→ 版本化发布 → 挂到 Agent | **高价值**：把"科室映射规则"写成可版本化的 Skill，而不是塞 prompt |
| **工具 / MCP** ●/○ | 旧契约：MCP Server 注册/激活/凭据轮换；Custom Tool 挂载；Managed Tool 安装 | 中：科室表小，暂不需要；若要查实时号源才有用 |
| **文件** ● | Session 文件、User File、校验和、分片上传、断点续传 | 中：可放医院科室表、门诊时间表 |
| **Artifact** ○ | 搜索、版本、预览、下载、发布 | 低：演示用不上 |
| **Scheduler** ○ | 定时跑 prompt，结果写新 Session | 低：卡套不需要定时任务 |
| **Billing / 治理** ○ | 用量、花费上限、服务用户、API Key 管理 | 低：但要知道有花费上限这回事 |
| **会话事件** ● | SSE 订阅、`cursor` 断点续传、`clientEventId` 幂等 | 中：后端稳定性用得上 |

---

## 三、三个真正值得用它的理由

### 1. 音频 Turn 比实时通话更适合导诊

实时通话是"一直连着、边听边说"，适合闲聊。导诊是"说一句 → 出一个结论"，天然是一次请求一次响应：

- 失败可以整段重试，不用处理半句话
- 可以先把音频存本地，网络差时延后上传
- 服务端返回结构化文本，直接上屏

代价：**要确认部署里 `ASR_OPENAI_API_KEY` 配了**，否则该接口直接返回 `501 audio_transcription_disabled`。

### 2. Skill 让判断规则可审计

现在设备绑定的 Agent 收到 1413 字节配置，行为由平台侧决定。若把"症状 → 科室"的规则做成 Skill：

- 规则有版本号，改了能回滚
- 同一段话术可复现，不会今天一个说法明天一个说法
- 评审时能直接看规则文件，而不是猜 prompt

对"适老导诊"这种**不能瞎说**的场景，这比调 prompt 靠谱。

### 3. 文件能力承载静态知识

医院科室表、楼层索引、门诊时间，做成文件挂在 Session/User File 上，Agent 读文件而不是靠记忆。10 个科室的小表用 Skill 的附件也够。

---

## 四、切过去的代价

| 路线 | 要做什么 | 前提 |
| --- | --- | --- |
| A 留在 Deotaland | 什么都不用改 | 赛题允许 |
| B 切 Agent Stack（正常激活） | 改 `platform_config.h` 的 URL + 在目标平台注册 product + 重编 + 重新激活绑定 | 平台侧给 product ID |
| B' 切 Agent Stack（`REALTIME_DIRECT`） | 固定 session + 私有 User API Key 编译进 `sdkconfig.private` | 平台给 session 和 User Key |

**注意**：改 URL 不会迁移产品 ID 或设备里已存的凭据。切过去之后设备要重新配网、重新激活、重新绑定。

---

## 五、现在不建议切平台

理由是按依赖顺序排的：

1. **实时语音还没验证过**（打通记录里 WSS 仍标"未验证"）。设备侧能力没证明之前，换平台等于把两个未知叠在一起。
2. **旧/新 API 代际未定**。赛事部署到底是 `/api/**` 还是 `/api/v1/**`，没实测过。
3. **平台 product 注册不在你手里**。切过去第一步就可能卡住。

**建议顺序**：先把 Deotaland 链路的语音跑通（建会话 → `session.ready` → 说话有回复）→ 再问主办方平台归属 → 再决定是否切。

---

## 附录：COM6 实检结果（2026-10-23 18:2x）

| 检查项 | 结果 |
| --- | --- |
| 端口存在 | COM6 ← `\Device\USBSER000` |
| 设备身份 | `VID_303A&PID_1001&MI_00`，友好名"USB 串行设备"——**Espressif 原生 USB Serial/JTAG，确认是这块板** |
| 第二接口 | `MI_02` → WinUsb Device（JTAG 调试口） |
| 端口可打开 | 是，115200 8N1，DTR/RTS 置位 |
| 空闲输出 | **8 秒 0 字节**——固件空闲不打日志，不是故障 |
| 附带修正 | COM3/COM4 是蓝牙调制解调器（`BthModem0/1`），不是主板串口；另有一个 CH343 在 COM5，当前未激活 |

要看启动日志必须复位设备，复位会挂断正在进行的通话。
