# 切到 Agent Stack：具体步骤

> 2026-10-23 | 依据：`Agent_link` 源码（Kconfig + cloud_realtime.cc + transport_wifi.cpp）+ `tidblink-dev-guide` 阶段 0–5
> **状态：以下步骤由源码推导，未在真机端到端验证**

---

## 一、先纠正一个我之前的说法

我之前说"切平台 = 改 `platform_config.h` 的 URL + 注册 product + 重新配网激活"。**这个说法不准确，而且照做会失败。**

原因：Agent_link 的 WiFi 上线依赖 **device-gateway**（认领激活码 → 拿 `auth_key` → 换 MQTT token → 心跳）。证据链：

- `cloud_session.cc:425` 发布 `AGENT_CLOUD_ONLINE`，注释是 "Authenticated: heartbeat running, MQTT up or coming up"
- `transport_wifi.cpp:605` 状态机要求 `online` 才能进 `AGENT_STATE_CONNECTED`
- Agent Stack 的 OpenAPI 里**没有** `/api/device-gateway/**`

所以把 URL 改成 Agent Stack 后，设备**永远到不了 CONNECTED 状态**，语音请求会直接返回 `ESP_ERR_INVALID_STATE`。

---

## 二、SDK 里其实内置了正规通道

`firmware/components/agent_link/Kconfig:77`：

```
config AGENT_LINK_REALTIME_DIRECT
    bool "Request voice grants directly from Agent Stack"
    depends on AGENT_LINK_TRANSPORT_WIFI
    default n
    help
        Use a locally configured User API Key and session for device-side testing.
        Otherwise request short-lived grants from the application backend over MQTT.
```

配套三个字符串配置：`REALTIME_BASE_URL`、`REALTIME_SESSION_ID`、`REALTIME_USER_API_KEY`。

`cloud_realtime.cc:246` 显示它请求的正是：

```
{BASE_URL}/api/v1/user/sessions/{sessionId}/realtime
```

**这就是 TiDB Link 阶段 5 步骤 19 那个端点。**

而且 `transport_wifi.cpp:848` 会自动分流：

```cpp
const bool direct = cloud_realtime_direct_enabled();
if (s_link != AGENT_STATE_CONNECTED || (!direct && !cloud_mqtt_started())) return ESP_ERR_INVALID_STATE;
...
const esp_err_t r = direct ? cloud_realtime_request_direct() : cloud_json_request_realtime();
```

**结论：app 代码不用改，只改 Kconfig。**

---

## 三、正确的切法：只换语音来源，设备照旧上线

```
设备 ──device-gateway(不变)──▶ Deotaland    ← 保持已绑定的 auth_key，照常上线
  └──WSS + grant(新增)──────▶ Agent Stack  ← 语音从这里来
```

于是：

| 原以为要做的 | 实际要不要 |
| --- | --- |
| 改 `platform_config.h` | **不要**（改了反而上不了线） |
| 重新配网 | 不要 |
| 重新激活 / 输六位码 | 不要 |
| 平台注册 product | 不要 |
| Console 模板编辑权限 | **不需要**（不挂 Connector） |
| 改 app 代码 | 不要 |

这是目前代价最小的方案。

---

## 四、具体步骤

### 阶段 A：Agent Stack 侧建三样（约 30 分钟）

需要组织 API 密钥 `ti_org_…`（Console 由组织所有者签发）。

```bash
BASE="https://<agent-stack-host>"
ORG="ti_org_..."
```

**A1 确认部署可用**

```bash
curl --fail-with-body "$BASE/api/v1/org" -H "Authorization: Bearer $ORG"
```

**A2 取 Sandbox 目录**

```bash
curl --fail-with-body "$BASE/api/v1/org/agent-template-reference-catalog" \
  -H "Authorization: Bearer $ORG"
```

从 `sandboxes[].id` 选一个，例如 `standard-v1`。

**A3 建模板**（`instructions` 直接放你的就医通人设；`realtime` 必须非 null 才有语音）

```bash
curl --fail-with-body -X POST "$BASE/api/v1/org/agent-templates" \
  -H "Authorization: Bearer $ORG" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: triage-template-1' \
  -d '{"displayName":"就医通","content":{
        "schemaVersion":"agent-template-content@7",
        "realtime":{"language":"zh-CN"},
        "identity":{"agentName":"就医通","description":"导诊助手"},
        "sandbox":{"profileId":"<A2 拿到的 id>"},
        "instructions":"<你的就医通人设全文>",
        "skills":[]}}'
```

记下响应里的 `template.id` 和 `template.etag`。

**A4 建终端用户 + 签发用户密钥**

```bash
curl --fail-with-body -X POST "$BASE/api/v1/org/users" \
  -H "Authorization: Bearer $ORG" -H 'Content-Type: application/json' \
  -d '{"displayName":"导诊卡套","externalId":"esp32s3-001"}'

curl --fail-with-body -X POST "$BASE/api/v1/org/users/<userId>/api-keys" \
  -H "Authorization: Bearer $ORG" -H 'Content-Type: application/json' \
  -d '{"name":"device-001"}'
```

`token`（`ti_user_…`）**只返回一次**，立刻存好。

**A5 用用户密钥建 Agent 和 Session**

```bash
U="ti_user_..."

curl --fail-with-body "$BASE/api/v1/agent-models" -H "Authorization: Bearer $U"
# 记下 effectiveAllowedModels 里的一个 model 名

curl --fail-with-body -X POST "$BASE/api/v1/user/agents" \
  -H "Authorization: Bearer $U" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: agent-001' \
  -d '{"agentTemplateId":"<templateId>"}'

curl --fail-with-body -X POST "$BASE/api/v1/user/sessions" \
  -H "Authorization: Bearer $U" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: session-001' \
  -d '{"agentId":"<agentId>","model":"<model>"}'
```

**记下 `session.sessionId`** —— 下一步要烧进固件。

**A6 确认语音模型可用**

```bash
curl --fail-with-body "$BASE/api/v1/realtime-models" -H "Authorization: Bearer $U"
```

要求：`defaultModel` 在 `models` 里且 `available: true`，支持 `zh-CN` 和所需 endpointing 模式。

### 阶段 B：固件改四行配置

编辑 `firmware/sdkconfig.private`（被 gitignore，不进仓库）：

```ini
CONFIG_AGENT_LINK_REALTIME_DIRECT=y
CONFIG_AGENT_LINK_REALTIME_BASE_URL="https://<agent-stack-host>"
CONFIG_AGENT_LINK_REALTIME_SESSION_ID="<sessionId>"
CONFIG_AGENT_LINK_REALTIME_USER_API_KEY="ti_user_..."
```

然后重建 + 分段烧录：

```bash
python firmware/tools/build_firmware.py waveshare-epaper-397-wifi --jobs 2
cd firmware && idf.py -p COM6 flash monitor
```

**注意**：`DirectConfigValid()` 会校验 base URL 必须以 `http://` 或 `https://` 开头、sessionId 只含 `[A-Za-z0-9_-]`。不满足会静默返回 `ESP_ERR_INVALID_ARG`。

### 阶段 C：验证

| 关卡 | 期望日志 | 不符时 |
| --- | --- | --- |
| C1 上线 | 仍出现 `[link] Online (phase=5)` | 说明 device-gateway 断了，检查 Deotaland 绑定 |
| C2 请求语音 | 出现 `Requesting voice access`（direct 模式提示语，不是 `Waiting for voice gateway`） | 说明 direct 没生效，查 Kconfig 是否真编进去 |
| C3 建连 | `session.connect sent over WSS` → `voice session ready` | TLS 或 token 问题 |
| C4 音频 | **上行是 PCM16 不是 Opus** | 见下节代价 |

---

## 五、四个必须接受的代价

| # | 代价 | 依据 |
| --- | --- | --- |
| 1 | **上行变成 PCM16，32 KB/s** | Kconfig 原文："Direct test sessions retain PCM16 input and request Opus replies." 比 Opus 的 6 KB/s 大 5 倍多，WiFi 差的场地会先崩 |
| 2 | **只有一个固定会话** | `sessionId` 编译时写死。会话菜单里选别的没用，菜单功能基本作废 |
| 3 | **User API Key 编进固件** | 固件不能公开分发；泄露要轮换。Kconfig 注释明确要求"Firmware built with this key must remain private" |
| 4 | **官方定位是"设备侧测试"** | 原文 "for device-side testing"，不是生产路径 |

---

## 六、如果还要 Connector（让 Agent 调腾讯）

2026-10-23 赛前路线复核更正：不能断言使用 Connector 必须迁移全部设备管理，更不能把“换 URL + 注册 product + 重新激活”当成已验证步骤。

一种可能架构是：保留 Deotaland 的 device-gateway，用 REALTIME_DIRECT 连接已挂载 Connector 的 Agent Stack 会话。仍需分别验证：

1. 目标部署的账号权限、MCP服务器接入和模板挂载是否可用。
2. 创建新Session后，实时语音是否实际调用该工具；文本Turn具备工具能力不自动证明语音也具备。
3. 工具如何得到设备起点，以及结果如何回到屏幕；更换语音来源不会自动完成定位或设备控制。
4. 直连语音与原有设备管理能否在本板稳定并存。

此前关于“Connector必须全量迁移”的断言撤回。上述架构尚未端到端验证，不给出半天或一到两天的工期保证。

---

## 七、当前建议

四行Kconfig只解决语音授权来源，不能视作完整平台迁移或导航接入。

结合两人团队和10月25日10:00提交截止，建议保留当前语音链路，新增独立导航后端和板端文字路线卡。详见 `赛前技术路线决策与排期_2026-10-23.md`。
