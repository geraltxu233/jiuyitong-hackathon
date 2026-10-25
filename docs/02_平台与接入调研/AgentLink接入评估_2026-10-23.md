# Agent_link 接入评估

> 2026-10-23 | https://github.com/Deotaland/Agent_link  
> 目标硬件：Waveshare ESP32-S3-ePaper-3.97（N16R8）  
> 结论用途：判断"Agent_link + App + ESP32"能否直接成为黑客松主线

---

## 一、一句话结论

**能用，而且是目前最完整的一条路——因为仓库里已经有这块板子的官方板级支持和一个专用 app，把实时语音全栈都写完了。** 但有三件事必须先澄清，否则会在现场卡住：

1. **它不需要 App。** WiFi profile 直连平台，App 只在 BLE 路线需要，且 App 未公开。这反而解决了之前"手机依赖"的冲突。
2. **它是 ESP-IDF 项目，不是 Arduino 项目。** 之前准备的 Arduino IDE 环境对这条路完全用不上；且这块板明确不支持 IDF 6.1。
3. **它默认连的是 Deotaland 自己的平台（`bot.deotaland.cn`），不是赛事的 Agent Stack。** 这是最大的分歧点，见第五节。

另外必须说清楚：**代码齐备 ≠ 真机验证过。** 板级 README 末尾明确写着设备验收全部未完成，包括语音、配网、激活、按键极性、屏幕刷新。这是当前最重要的风险。

---

## 二、它是什么

仓库描述："Open-source SDK connecting physical devices to AI agents through BLE, Wi-Fi, and extensible hardware interfaces." MIT 许可。

把前面几份材料的血缘补齐了：

| 材料 | 角色 |
| --- | --- |
| `tidblink-dev-guide` | 上层 API 契约文档（`/api/v1`、SSE、Connector、Realtime） |
| `esp32-agent-lcd` | 早期示例（M5Stack LCD + USB 串口桥，旧 API） |
| **`Agent_link`** | **真正的设备端 SDK + 量产级 app**，含 BLE / WiFi / Muse 三条通道 |
| ROROLEE / WOWNNY / FoloToy AI Passport / M5Stack StopWatch | 这个 SDK 的商用产品线（board 目录里都在） |

关键连接点：`REALTIME.md` 里写 WebSocket 子协议是 **`tidb-link.realtime.v1`**。也就是说，Agent_link 的 WiFi 实时语音，用的就是之前那份 `tidblink-dev-guide` 定义的协议。**之前读的文档不是白读，它是这套 SDK 的协议底座。**

这意味着：之前判断"Realtime WS + Opus + 流控 + 60 秒 token 两天成功率不到 40%"的那个最难部分，**SDK 已经做完了**，不用自己写。

---

## 三、你这块板子的支持状况

`firmware/boards/waveshare-epaper-397/` 是完整板级移植，不是通用适配。

| 项 | 内容 |
| --- | --- |
| Profile | `waveshare-epaper-397-wifi`（WiFi 通道） |
| 固件型号 | `WS-EPAPER-397` |
| 状态 | `supported` |
| 芯片/存储 | ESP32-S3-WROOM-1-N16R8，16 MB flash，8 MB **Octal PSRAM** |
| 屏幕 | 800×480 单色墨水屏，SPI3 |
| 音频 | ES8311（I2S）+ NS4150B 功放（GPIO39 高有效），16 kHz |
| 电源 | TG28 PMIC（AXP2101 兼容寄存器，0x34），先校验 chip ID 再写电源寄存器 |
| 按键 | 三板键 上 4 / 中 5 / 下 6（低有效），BOOT 0 |
| 字库 | Noto Sans SC 全集 **6763 个 GB2312 汉字**，TinyTTF，28 px，32 字形缓存，放在 `anim_pack` 分区 0x412000 |
| IDF | **5.5.x（以 5.5.4 验证）；6.1 不支持**——共享 ES8311 驱动未适配 |
| 应用 | `firmware/apps/waveshare-epaper-397-wifi/`（ui / audio / controls / main 齐全） |

引脚与之前工作区 `ARDUINO环境配置` 那份表逐项对得上（I2C 41/42、I2S 13/14/47/21/48、PA 39、EPD 11/12/10/9/46/3）。说明这份移植是照官方 BSP 做的，不是拍脑袋抄的。

**已内建的屏幕刷新策略**（正好解决我之前担心的墨水屏黑闪问题）：

- 整屏单色快刷之间至少间隔 2 秒
- 每 16 次成功刷新做一次全刷
- BUSY 20 秒超时，失败退避 30 秒
- 空闲时面板休眠
- 只有实际画出来的状态行变化才触发刷新（隐藏的倒计时更新不刷屏）
- 流式文本等稳定后才上屏

---

## 四、原样能跑出什么

烧完这个 profile，不改一行代码，设备具备：

| 能力 | 说明 |
| --- | --- |
| 配网 | 开热点 `WavePaper397-XXXX`，连上后 http://192.168.4.1 输 2.4 GHz WiFi |
| 激活 | 屏幕显示 6 位激活码，到 `bot.deotaland.cn` 输入并绑定 Agent |
| 设备认证 | 绑定后拿到 `auth_key` 存 NVS，是唯一持久凭据；每 60 秒刷新 |
| 会话管理 | 列出 / 创建 / 选择 / 删除该 Agent 的对话，游标分页，选择存 NVS |
| 实时语音 | WSS 子协议 `tidb-link.realtime.v1`，S3 走单声道 Opus（16k 入 / 24k 出 / 20 ms），协商失败回落 PCM16 |
| 屏幕 | 状态行 + 激活码 + 会话名 + **最多 1 KB 回复文本**，中英常见字，超长裁剪 |
| 按键 | 中键点击 = 开始/结束通话；长按 1 秒 = 菜单；上/下 = 音量或菜单选择；BOOT = 返回/挂断 |
| OTA | 带 SHA256 校验的镜像写入 |

**这意味着"AI 大脑 + 语音 + 屏幕显示 + 配网 + 激活 + OTA"这一整层不用自己写。**

---

## 五、三个必须先解决的问题

### 问题 1（最严重）：平台地址指向 Deotaland，不是赛事 Agent Stack

`firmware/boards/common/platform_config.h`：

```c
#define DEOTALAND_PLATFORM_BASE_URL "https://bot.deotaland.cn"
```

这个 URL 编译进固件。而赛事相关的是 `ventured-agent-stack.pingcap.cn`。两者是不同的服务。

`REALTIME.md` 提到三条 grant 来源，其中两条可以用来接别的平台：

- **Authenticated MQTT 可下发短期 grant**
- **`CONFIG_AGENT_LINK_REALTIME_DIRECT`**：固定 session + 私有 User API Key 的开发路径，同样走同一套 worker

所以接赛事平台有两条路：

| 路线 | 做法 | 前提 | 风险 |
| --- | --- | --- | --- |
| **A：用 Deotaland 平台** | 原样烧录，`bot.deotaland.cn` 上建导诊 Agent | 赛事允许用这个平台 | 可能不符合赛题要求的 Agent 平台 |
| **B：接赛事 Agent Stack** | 改 BASE_URL + 走 `REALTIME_DIRECT`（固定 session + User API Key）或 MQTT 下发 grant | 赛事平台侧要注册对应 product、要有 session 和 User Key | 需要改代码 + 平台侧配合 |

**`REALTIME_DIRECT` 是这条 SDK 接任意 TiDB Link 部署的正规入口。** 但它用的是固定 session 和私有 User API Key，属于开发路径，不是生产激活流程。

**必须先问主办方一句话：赛题是否强制要求使用指定平台的 Agent？** 答案决定走 A 还是 B。

### 问题 2：product ID 文档自相矛盾

同一块板，三处写法不一致：

| 位置 | 值 |
| --- | --- |
| `profiles/products.json` | `esp32-s3-epaper-397` → **id 5** |
| `boards/waveshare-epaper-397/README.md` | product ID **5** |
| `boards/waveshare-epaper-397/config.json` 的 `notes` | "Platform product ID **20**" |

构建工具读的是 `products.json`，所以实际生效是 **5**；`config.json` 里的 20 是过期残留。这不会导致构建失败，但如果你按 20 去平台后台找产品，会找错。**建议向上游提 issue 修掉。**

### 问题 3：设备验收状态是 pending

板级 README 的 Acceptance 段落原文：device acceptance remains pending，未验证清单包括——

TG28 电源轨、整屏/快刷与方向、按键极性、ES8311 麦克风与喇叭电平、正确/错误 WiFi 配网、激活、平台绑定与重绑、会话分页/创建/恢复、`session.ready`、真实双向语音、断连/结束/重试、重启持久化。

并明确写着：**"A successful build does not verify these behaviors."**

另外 `REALTIME.md` 的验证记录（2026-10-17）里有一条很关键：

> Conversation creation failed with business code **1002 (`agent_template_unavailable`)**

意思是平台侧必须给这个 product 配好 Agent 模板，否则**创建会话直接失败**。这是平台配置问题，不是固件问题——必须找平台方确认模板已就绪。

---

## 六、立即可执行的步骤

### 1. 装 ESP-IDF 5.5.4（不要 6.x）

Windows 用官方 ESP-IDF Tools Installer 的 5.5.x 离线包最稳（国内网络下在线安装容易卡在拉 Python 包）。装完确认 `idf.py --version` 输出 5.5.4。

之前那份 Arduino IDE 3.2.0 的配置**留着做单点验证可以，但这条路用不上**。两套工具链不要混在一个工程里。

### 2. 克隆并构建

```bash
git clone https://github.com/Deotaland/Agent_link.git
cd Agent_link/firmware
python tools/build_firmware.py --list
python tools/build_firmware.py waveshare-epaper-397-wifi --jobs 2
```

首次构建需要联网拉 `dependencies.lock` 里的托管组件（lvgl、TinyTTF 等），之后可离线。

产物在 `firmware/build/`：`agent_link.bin`（OTA 用）、`merged.bin`（整包）、`agent_link.elf`（崩溃解析）、`flasher_args.json`。

### 3. 烧录：用分段烧录，不要整包

```bash
python -m serial.tools.list_ports -v     # 找 COM 口
idf.py -p COMx flash monitor
```

**必须用 `idf.py flash`（分段），不要用 `merged.bin` 整包烧。** 整包会覆盖 NVS，把 WiFi 凭据、配对信息、设备身份一起冲掉。只有全新板或要彻底重置时才用整包。

进不了下载模式：按住 BOOT 再插 USB（原生 USB D+/D- 是 GPIO20/19）。

### 4. 首次上电看重启日志

启动日志里核对三个字段：`board`、`model`、`transport`。应当是：

```
board = waveshare-epaper-397
model = WS-EPAPER-397
transport = WIFI
```

对不上说明烧错了 profile，立刻停。

### 5. 配网 + 激活

1. 手机/电脑连热点 `WavePaper397-XXXX`
2. 打开 `http://192.168.4.1`，填 **2.4 GHz** WiFi（5 GHz 连不上）
3. 屏幕出现 6 位激活码
4. 到 `bot.deotaland.cn` 输入激活码，绑定一个**支持语音的 Agent**
5. 中键点击开始通话；长按 1 秒开菜单会话列表

---

## 七、Go / No-Go 判据

按顺序过，任何一步不过就停在原地排查，不要跳步。

| 关卡 | 证据 | 不过怎么办 |
| --- | --- | --- |
| G1 构建 | `build_firmware.py` 成功产出 `merged.bin` + `flasher_args.json` | 检查 IDF 版本是否 5.5.x、组件是否拉全 |
| G2 启动 | 串口 115200 打出 `board/model/transport` 三项正确 | 烧错 profile 或板级不匹配 |
| G3 屏幕 | 显示激活码且不花屏、方向正确 | TG28 电源轨或 EPD 驱动问题，报文给上游 |
| G4 激活 | 平台侧绑定成功，设备日志连续 60 秒刷新认证 | 平台 product 未注册或激活码过期 |
| G5 会话 | 能拉到会话列表（哪怕为空） | **1002 `agent_template_unavailable` → 平台没配 Agent 模板** |
| G6 语音 | `session.ready` 后能听到回复、麦克风有电平 | 查 ES8311 电平、PA GPIO39、Opus 协商回落 |
| G7 稳定 | 连续 3 次通话成功；重启后仍能免配网直接通话 | NVS 持久化或 auth_key 问题 |

**G5 是最容易卡住的一关，而且不在你手里。** 建议今天就去平台后台确认这个 product 有没有可用 Agent 模板，别等烧完才发现。

---

## 八、对导诊场景的适配判断

| 维度 | 现状 | 对导诊的影响 |
| --- | --- | --- |
| 交互模式 | **持续通话**：中键点击开始，再点结束。不是按住说话 | 对老人反而友好（不用一直按着）；但演示节奏要设计"点一下→说话→听完→再点一下" |
| 回复呈现 | 纯文本 dump，最多 1 KB，超长裁剪；800×480 / 28 px 约 28 字 × 17 行 | "建议您挂心内科，门诊楼 3F 305 诊室，三楼东侧"完全放得下，**首版不需要改固件** |
| 字体 | GB2312 全集 6763 字，生僻字显示 `?` | 科室名、楼层、提示语都在常用字范围，够用 |
| 回声 | **半双工**：播放时麦克风静音，无 AEC，不支持语音打断 | 一问一答场景可接受；但要接受"不能插话" |
| 结构 | 回复是自由文本，不是结构化字段 | 想让屏上排成"科室 / 楼层 / 诊室"三行大字，得改 `epaper_wifi_ui.cc`——**建议首版不做** |
| 工具调用 | 未见 Agent 侧工具/MCP 配置入口（在平台侧，不在固件） | 科室表规模小（10 个以内），直接写进 Agent 指令即可，不需要工具 |

**结论：首版零固件改动**。只要在平台上建好 Agent 指令，把"症状→科室→楼层诊室→门诊时间"的话术限死，配合墨水屏的文本渲染就够演示。

**唯一值得改固件的地方**：把回复从自由文本变成三段固定版式（科室大字 + 楼层诊室 + 提示语）。这是 Day2 有余力才做的事，改动集中在 `epaper_wifi_ui.cc`。

---

## 九、对前面几份文档的修正

1. **"L2 Realtime 两天成功率不到 40%"** —— 这个判断作废。SDK 已实现，不需要自己写。
2. **"ROROLEE 协议未知，是最大证据缺口"** —— 收窄了。协议就是 设备认证（`/api/device-gateway/auth`）+ MQTT 命令 + WSS（`tidb-link.realtime.v1`）。剩下的未知只有"赛事用哪个平台"。
3. **"手机 App 是运行依赖，与产品叙事冲突"** —— 不成立。WiFi 路线不需要 App，只有配网时借一次热点。
4. **"必须自己做 Connector/MQTT 才能让 Agent 控硬件"** —— 对本项目不必要。回复文本直接上屏就能构成闭环。
5. **Arduino 环境那份文档** —— 对这条路无用，但仍是板子单点验证（PSRAM/I2C/引脚）的有效工具，保留。

---

## 十、要问主办方的三句话

1. **赛题是否强制使用指定平台（如 `tidb-agent-stack.pingcap.cn`）？还是允许用 Deotaland 平台？** —— 决定走路线 A 还是 B，这是唯一的分叉点。
2. **`esp32-s3-epaper-397`（product id 5）在这个平台上有没有已就绪的、支持语音的 Agent 模板？** —— 决定 G5 能不能过。
3. **如果必须接赛事平台，能否提供 product 注册 + 固定 session + User API Key，以走 `REALTIME_DIRECT`？** —— 这是 SDK 接外部部署的正规入口。

---

## 十一、官方《快速上手指南》的补充

对照 `bot.deotaland.cn/#/developer/docs?doc=quick-start` 全文，本评估的结论基本成立，另有五处增量。

### 11.1 被官方文档确认的四点

| 本评估的判断 | 官方原文 |
| --- | --- |
| 本板 profile 与热点名 | 表中列 `waveshare-epaper-397-wifi`；热点 `WavePaper397-XXXX` |
| 必须 IDF 5.5.x | "Waveshare ePaper 3.97 受共享音频驱动限制，目前需要 5.5.x" |
| 系统 COM 口不等于板子 | "系统自带 COM 口不等于 ESP32"（与实测 COM3/4/6 为主板口一致） |
| 烧录方式 | "分区布局兼容时，`idf.py flash` 使用分段镜像写入，保留 NVS"；合并镜像会覆盖 WiFi、设备身份和平台绑定凭据 |

### 11.2 产品 ID 冲突有了官方解释

第 7 节原文：**"其他 WiFi profile 仍保留上一个平台的产品 ID，在当前域名使用前需要重新核对。"**

这就是 `products.json` 写 5、`config.json` 的 notes 写 20 的根因——不是笔误，是平台迁移没做完。**烧录前必须重新核对，不能默认 5。**

### 11.3 有官方工具可以现场定论产品 ID

`GET /api/products/all` + `skills/agent-link-port/scripts/find_product.py`，用账号令牌 `DEOTALAND_TOKEN` 查询（注意：这是开发者账号令牌，**不是**设备认证密钥）：

```powershell
$platformToken = Read-Host "Platform account token" -AsSecureString
$env:DEOTALAND_TOKEN = [System.Net.NetworkCredential]::new("", $platformToken).Password
python skills/agent-link-port/scripts/find_product.py "Waveshare ESP32-S3-ePaper-3.97" --chip esp32-s3 --json
Remove-Item Env:DEOTALAND_TOKEN
```

名称或 product_key 唯一精确匹配时返回 `selected_id`，可直接用。**这一步应在上板前完成。**

### 11.4 仓库自带四个官方 AI 工作流

`skills/` 下有 `agent-link-flash`、`agent-link-build`、`agent-link-port`、`agent-link-debug`，可用 `tools/install_skills.py --dest <目录>` 装进任意助手（默认写 `~/.claude/skills`）：

```sh
python tools/install_skills.py --project --check
python tools/install_skills.py --project
```

这意味着"AI 识别板型 → 选 profile → 构建 → 检查串口 → 说明写入影响"这套流程有官方脚本兜底，不必凭记忆拼命令。

### 11.5 两处需要修正的说法

1. **构建命令的工作目录**：`firmware/README.md` 是在 `firmware/` 下跑 `python tools/build_firmware.py`，快速指南是在仓库根跑 `python firmware/tools/build_firmware.py`。两者都可用，**以根目录写法为准**，避免路径歧义。
2. **"创建会话"是首次通话最脆弱的一步**：指南说首次通话"没有会话时创建一个"，而 `REALTIME.md` 的实测记录里出现过 `code 1002 (agent_template_unavailable)`。两者合起来说明——平台没配 Agent 模板时，**新设备第一次通话会直接失败**。这是上板后的第一个观察点，不是语音质量问题。

### 11.6 仍未解决

指南全文只提 `bot.deotaland.cn`，**未提赛事平台**。第 7 节反而明确"产品 ID 属于具体的平台部署……切换平台后，需要确认产品注册信息、重新编译"。即换平台是被支持的，但不是改 URL 就行——产品必须在目标平台注册过。第十节那三个问题依然要问。
