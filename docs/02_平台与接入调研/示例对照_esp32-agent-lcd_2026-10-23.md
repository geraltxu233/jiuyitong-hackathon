# 示例对照：you06/esp32-agent-lcd

> 调研日期：2026-10-23 | https://github.com/you06/esp32-agent-lcd
> 用途：作为"ESP32 接 Agent"的最小可行参照，对照 TiDB Link 方案的取舍

---

## 1. 仓库快照

| 项 | 事实 |
| --- | --- |
| 规模 | 11 个文件，19 KB，单次提交（2026-08-26 创建即推送） |
| 语言 | TypeScript（桥接层）+ C++（固件） |
| 硬件 | **M5Stack CoreS3**（ESP32-S3，320×240 LCD，带触摸/按键/背光） |
| 依赖 | `serialport@12`、`tsx`、`typescript`；固件用 PlatformIO + M5GFX 0.2.28 + M5Unified 0.2.21（锁定 GitHub 版本，绕开 Library Registry） |
| License | **无**（法务上不能直接拷代码进产品，只能参考） |
| Star | 0 |

---

## 2. 它做了什么（一句话）

**把 ESP32 变成一台"哑终端"：设备不联网、不调 API、不持有任何密钥，只通过 USB 串口收发 JSONL；所有智能与网络都在 PC 上的一个 TypeScript 桥接进程里。**

```
M5Stack CoreS3 ──USB CDC / JSONL──▶ TypeScript Bridge ──REST+NDJSON──▶ Agent Stack
      ▲                                      │
      └──────── 最终结论一行 JSON ────────────┘
```

人怎么用：PC 终端敲一句 prompt → 桥接层建 Session、发 Turn → 消费流式响应 → 只把**最终结论**一行发给设备 → 屏幕大字显示。设备上的 A 键也能触发（发一条预设 prompt）。

---

## 3. 三部分代码拆解

### 3.1 固件 `src/main.cpp`（13.9 KB）——最值得看的部分

| 模块 | 做法 |
| --- | --- |
| 网络 | **完全没有**。WiFi 库都没引，`Serial` 就是全部传输层 |
| JSON | **手写极简提取器**，只支持取 string / bool 两个字段（`jsonStringForKey` / `jsonBoolForKey`），连 ArduinoJson 都没用；含完整转义处理与 `\uXXXX` → UTF-8 编码 |
| 排版 | `textPixelWidth()` 按 UTF-8 首字节判断字符字节数，ASCII 记 8px、CJK 记 16px；`drawWrappedText()` 逐字符试探换行，最多 9 行 |
| 字体 | `fonts::efontCN_16`（M5GFX 内置中文点阵） |
| 界面 | 6 种状态：Boot / Waiting / Ready / Running / Conclusion / ERROR / Terminal，顶栏 26px 高亮条 + 底部 "USB serial" 角标 |
| 触发 | A 键手动；另可选 60 秒自动刷新（`SERIAL_AUTO_REFRESH_MS`，下限钳到 5 秒） |
| 握手 | 开机每 1.5 秒发 `{"type":"hello","device":"m5stack-cores3"}`，直到桥接层回 `ready` |
| 串口 | `cfg.serial_baudrate = 0` 让 M5Unified 不接管 Serial，自己 `Serial.begin` 避免冲突 |

### 3.2 桥接层 `agent-stack-client.ts`（12.5 KB）——Agent API 封装

四步调用链，硬编码在 `run()` 里：

1. `GET /api/console/projects` → 选 `status == "active"` 的第一个
2. `GET /api/agents` → 选第一个
3. `POST /api/sessions`（body `{agentId}`，header `x-agent9-project-id`）→ **只建一次，缓存在实例里**
4. `POST /api/sessions/{id}/turns`（`Accept: application/x-ndjson`，body `{input:{type:"text",text}}`）→ 消费 NDJSON 流

NDJSON 消费器 `consumeNdjsonStream()` 设计得很好，可独立单测（有对应的 `agent-stack-client.test.ts`，用假 reader 灌 chunk）：

| 事件 | 处理 |
| --- | --- |
| 空行 | 当作心跳，跳过 |
| `turn_started` | 取 `turnId` |
| `progress` / `assistant_draft` | **丢弃**（只为让屏幕不闪） |
| `assistant_message` | 存 `payload.text` 作为最终结论 |
| `turn_error` | 记错误，继续读到终态 |
| `turn_finished` | 校验 `status ∈ {succeeded, failed, interrupted}` |

终态校验很严：流结束但没有 `turn_finished` → 抛 `missing_terminal_event`；成功但没有 `assistant_message` → 抛 `missing_assistant_message`。

### 3.3 桥接层 `serial-bridge.ts`（7.8 KB）——串口与队列

| 机制 | 细节 |
| --- | --- |
| 全局单队列 | 终端输入、A 键、60 秒自动刷新**共用一个 `runQueue`**，同一时刻只跑一个 Turn |
| 幂等/对齐 | 每条请求带 `requestId`，回来的 `result` 必须匹配 `gActiveRequestId`，否则丢弃 |
| 重连 | 串口 close 后 1.5 秒重连，重连成功立刻补发 `ready` |
| 防污染 | **非 `{` 开头的行直接丢弃**——因为 ESP32 复位日志会混在同一条 USB CDC 流里 |
| 限流 | prompt 1–4000 字符；输入缓冲超 64 KB 直接清空并报错 |
| 无 .env 自动加载 | 故意的，密钥只从环境变量进内存 |

### 3.4 入口 `server.ts`（2.9 KB）

PC 终端 REPL（`readline`），`You >` 提示符，`/exit` 退出。终端的 Turn 结果用 `display` 消息同时打到屏幕和 stdout。

---

## 4. 串口协议（JSONL，一行为一条消息）

| 方向 | 消息 |
| --- | --- |
| 设备→桥 | `{"type":"hello","device":"m5stack-cores3"}` |
| 设备→桥 | `{"type":"run","requestId":"1","prompt":"..."}` |
| 桥→设备 | `{"type":"ready","defaultPromptAvailable":true}` |
| 桥→设备 | `{"type":"result","requestId":"1","status":"succeeded","conclusion":"..."}` |
| 桥→设备 | `{"type":"result","requestId":"1","status":"failed","error":{"code","message"}}` |
| 桥→设备 | `{"type":"display","status":"succeeded","conclusion":"..."}`（终端触发的回显） |
| 桥→设备 | `{"type":"error","error":{"code","message"}}`（协议级错误） |

设计要点：**只有一条结果消息**，不把流式事件透传给设备。设备端的协议状态机因此只有 4 个分支。

---

## 5. 它处理得漂亮的 7 个细节（可复用）

1. **hello/ready 双向重试**——解决"设备和桥谁先启动"的时序问题，比固定 `delay(3000)` 可靠得多。
2. **丢弃非 `{` 开头的行**——ESP32 复位时会把 boot log 吐到同一个 CDC 口，不清洗就会误判成协议错误。换成 WiFi TCP 也一样有用（TLS 握手失败的噪声、AT 回显）。
3. **requestId 匹配**——异步结果与请求对齐，天然丢弃过期响应。
4. **只取 `assistant_message`，丢弃 draft**——屏幕不闪烁、不闪回。对墨水屏更是刚需。
5. **全局单队列**——避免两个 Turn 并发把 Session 上下文搅乱（这个平台一个 Session 只能串行）。
6. **手写 50 行 JSON 提取器**——只取两个字段，比引 ArduinoJson 省 30 KB flash，且行为可预期。
7. **NDJSON 消费器与 HTTP 解耦**（传入 reader 而非 response）——所以能单测。这个结构换成 SSE 解析器照样成立。

---

## 6. 它没做的事（能力边界）

| 缺什么 | 影响 |
| --- | --- |
| 无 WiFi / 无 TLS | 必须插 USB 线到一台跑 Node 的电脑。产品形态上是"开发板 + 笔记本"，不是独立设备 |
| 无音频（麦克风/喇叭/TTS） | 纯文本往返，没有语音交互 |
| 无工具调用 / 无 Connector | Agent 只能回文字，**不能控制硬件**（连屏幕都是桥接层直接写的，不经过 Agent 决策） |
| 不流式上屏 | 只显示最终结论，中间的 draft 全丢 |
| 单设备 | 一个串口、一个 Session、一个 Agent |
| 串口无鉴权 | 拿到线的人就能触发 Turn（本地开发可接受，产品形态不可接受） |
| Turn 失败不重试 | 失败就报错，靠人工再按一次 |

---

## 7. 关键：它用的是**旧 Agent Stack API**，不是 TiDB Link

这是最容易踩的坑。它和 `tidblink-dev-guide` 的 `/api/v1` 契约**不是一套东西**：

| 维度 | 这个仓库（Agent Stack） | TiDB Link（新指南） |
| --- | --- | --- |
| 密钥 | `ag9_uak_...` | `ti_org_...` / `ti_user_...` |
| 概念 | Project → Agent → Session → Turn | Organization → Agent Template → Agent → Session → Turn |
| 入口 | `/api/console/projects`、`/api/agents`、`/api/sessions` | `/api/v1/org/**`、`/api/v1/user/**` |
| 发消息与看结果 | **一次 POST 即返回 NDJSON 流**（请求与流绑定） | **发送与观察分离**：先 `GET …/events?stream=true`（SSE 长连接订阅 Session），再 `POST …/events` 提交 |
| 事件名 | `turn_started` / `assistant_draft` / `assistant_message` / `turn_finished` | `agent.session.turn.started` / `.output_text.snapshot` / `.item.added` / `.completed` |
| Turn 强杀 | 关流即结束 | **关流不取消 Turn**，要显式发 `InputCancelEvent` |
| 断点续传 | 无 | SSE 用 `id` 作 `cursor` 重连 |

**结论：`agent-stack-client.ts` 的 4 个 fetch 调用不能照抄，整个 run() 结构要重写。** 可照抄的是：错误封装（`AgentStackError`）、超时（AbortController 贯穿到流消费结束）、心跳空行处理、终态强校验、reader 抽象。

---

## 8. 搬到你的微雪 ESP32-S3-ePaper-3.97 上必须改的地方

| 项 | 这个仓库的做法 | 你的板子上会出什么事 |
| --- | --- | --- |
| 刷新 | `renderScreen()` 每次 `fillScreen(kBlack)` 全屏重绘 | **墨水屏整屏刷新 1.5–2 秒 + 黑闪**。必须改成：只在内容变化时刷、区分全刷/局刷、等待态不上屏 |
| 状态提示 | 反复刷 "Running / Waiting for Agent Stack..." | 墨水屏上是灾难。改用一个 LED 或屏角静态小标，等待期间保持上一条结论不动 |
| 字体 | `efontCN_16`（16px 点阵） | 800×480 上要 48–64px 大字，需自带 GB2312 子集字库或预渲染位图 |
| 换行算法 | 8px/16px 硬编码 | 逻辑可复用，宽度参数要按实际字号重算 |
| 传输 | USB CDC | 产品叙事要求脱线，必须换 WiFi + TLS（这就接回 TiDB Link 的 Realtime WS 或你的 HTTP 接口） |
| 结论策略 | 只显示 `assistant_message`，丢 draft | **保持不变，这是对的** |

---

## 9. 结论：这个示例在你的方案里该扮演什么角色

**不要照搬它的架构。** 它是"PC 外挂大脑"，与"高光时刻全在硬件本体、不依赖手机/电脑"的产品叙事直接冲突。

**要照搬它的固件侧 300 行。** 具体是：
- `jsonStringForKey` + `parseJsonStringAt` + `appendUtf8`（约 90 行，零依赖 JSON 提取）
- `utf8CharLength` + `textPixelWidth` + `drawWrappedText`（约 60 行，中文按字宽换行）
- hello/ready 握手与重试、非 `{` 行过滤、requestId 对齐、单队列串行
- 6 状态界面的信息层级（顶栏 + 状态色 + 正文 + 角标）

**要照抄它的后端启发：** "只把最终结论透传给设备"这个收窄，能让设备端状态机缩到 4 个分支。你做 TiDB Link 接入时，Connector 工具回给设备的内容也应该这样收窄（回结构化 JSON，不回原始事件流）。

**它的最大价值是验证了一条判断：** 嵌入式屏幕上，"等一个终态结论"比"渲染流式增量"简单一个数量级，而且体验上不掉分。
