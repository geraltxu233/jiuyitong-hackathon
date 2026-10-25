# 接口契约（板端 + Agent 两套入口）

服务只做两件事：给 Agent 提供 `query_transit` 工具，给墨水屏提供"这个会话最新的一张卡"。
两者共用一张 `cards` 表，所以**模型说的和屏幕显示的必然是同一次查询**。

---

## 一、板端入口

```http
GET /api/route-card?session_id=<平台会话ID>
Accept: application/json
```

无认证头。`session_id` 本身就是凭证：服务端只认已存在的会话，查不到一律当"没有"。

### 成功（HTTP 200，实测 251 字节）

```json
{
  "code": 0,
  "title": "去复旦大学附属中山医院(东院区)",
  "lines": [
    "步行300米到车站",
    "乘浦西滨江1路坐6站",
    "到打浦路斜土路换乘",
    "乘733路坐3站",
    "下车步行250米到院"
  ],
  "source": "腾讯地图 60分 换乘1次 19:33"
}
```

### 失败（HTTP 仍是 200，靠 code 区分）

```json
{"code": 3, "message": "还没有查到路线"}
```

**只回这四个字段（失败时是 `code` + `message`）**，不回 `session_id`、`card_id`、原始腾讯 JSON。
调试信息去 `/api/admin/card` 看。

### 硬约束

| 项 | 值 | 来源 |
| --- | --- | --- |
| 状态码 | 必须直返 200，不得 301/302 | 板端 `disable_auto_redirect=true` |
| 响应体 | ≤ 2048 字节 | 板端 `kBodyCap = 2048` |
| lines 行数 | 目标 5 行，硬上限 9 行 | 一屏 9 个逻辑行槽，超过要翻页 |
| 每行宽度 | ≤ 696 px（汉字 56 px，半角 24 px） | 800 - 40 边距 - 64 省略号留位 |
| source 宽度 | ≤ 440 px（页脚 28 px 字号） | 页脚实测 |
| 传输 | 带 `Content-Length`，`Connection: close`，不分块不压缩 | 板端一次 GET 读完 |
| 中文文案 | `message` 一句话、能直接上屏、≤ 12 汉字 | 老人要看得懂 |

### 错误码

| code | 含义 | 屏幕上的 message |
| --- | --- | --- |
| 0 | 成功 | — |
| 2 | 参数不对 | 缺少会话编号 / 只支持公交路线 |
| 3 | 没有路线 / 会话不存在 / 上次查询失败 | 还没有查到路线 |
| 5 | 地点解析不到 | 找不到这个起点 / 找不到这个终点 |
| 6 | 腾讯超时或报错 | 地图服务忙，稍后再试 |

> 会话不存在与"还没查过路线"返回**同一句话**，避免被拿来枚举会话。区别只写在服务端日志里。

---

## 二、Agent 入口（Connector）

```http
POST /mcp
Content-Type: application/json
Authorization: Bearer <connectorKey>
```

JSON-RPC 2.0，支持 `initialize` / `tools/list` / `tools/call` / `ping`。

### 工具定义

```json
{
  "name": "query_transit",
  "inputSchema": {
    "type": "object",
    "properties": {
      "city":             {"type": "string", "description": "城市名，例如 上海"},
      "origin_text":      {"type": "string", "description": "起点中文地名"},
      "destination_text": {"type": "string", "description": "终点中文地名"},
      "travel_mode":      {"type": "string", "enum": ["transit"]}
    },
    "required": ["city", "origin_text", "destination_text"]
  }
}
```

### 调用

```json
{
  "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": {
    "name": "query_transit",
    "arguments": {
      "city": "上海",
      "origin_text": "西岸国际会展中心",
      "destination_text": "复旦大学附属中山医院",
      "travel_mode": "transit"
    },
    "_meta": {
      "tidb-link/context": {
        "callId": "op_example_001",
        "sessionId": "sess_example_001"
      }
    }
  }
}
```

### 返回（给模型口述的长文本）

```json
{
  "jsonrpc": "2.0", "id": 1,
  "result": {
    "content": [{"type": "text", "text": "从西岸国际会展中心(上海)到复旦大学附属中山医院(东院区)，全程约60分钟，步行924米，换乘1次。路线：……"}],
    "isError": false
  }
}
```

同一次调用会**顺手把四字段卡写进这个会话**，板子按键就能拉到。

### Connector 鉴权与 sessionId / callId

平台每次请求（包括 catalog discovery）都会发送：

```http
Authorization: Bearer <connectorKey>
```

服务端配置 `CONNECTOR_KEY` 后，所有 `/mcp` 请求都先校验该头，失败返回 HTTP 401。
`raw_log` 中鉴权头、Cookie 等敏感头统一记录为 `[REDACTED]`。

官方 Call Context 的准确位置已经由 TiDB Link 文档确认：

```json
{
  "params": {
    "_meta": {
      "tidb-link/context": {
        "callId": "op_...",
        "sessionId": "sess_..."
      }
    }
  }
}
```

| 值 | 正式模式取值 | 用途 |
| --- | --- | --- |
| sessionId | `params._meta["tidb-link/context"].sessionId` | 绑定当前 Agent Session 的路线卡 |
| callId | `params._meta["tidb-link/context"].callId` | 工具调用幂等去重 |

配置 `CONNECTOR_KEY` 的正式模式只信上述平台上下文，`arguments.session_id`、旧 headers 等模型可填字段不会用于写卡。
未配置时保留旧字段回退，仅供本地/演示兼容。

每次已鉴权 POST 的脱敏 headers 与 body 会存进 `raw_log`。第一次真实平台调用之后，在服务器本机执行：

```bash
curl -sS 'http://127.0.0.1:8010/api/admin/raw?n=5'
```

核对实际上下文与板子选择的 Session 是否一致。

### 幂等

`callId` 相同 → 直接返回上次结果，不重复查腾讯，不覆盖已有卡。
实测重放 3ms、`card_id` 不变。

---

## 三、会话从哪来

`session_id` 必须先在 `sessions` 表里存在。两条路：

1. **正式**：Agent 调用工具时带 sessionId，服务端发现没见过会自动建一条（`note='auto from tool call'`）。
2. **手工造（测试用）**：

```bash
curl -sS -X POST http://127.0.0.1:8010/api/admin/session \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"sess_test_001","device_id":"44b176b71900"}'
```

---

## 四、调试接口（仅本机）

| 接口 | 用途 |
| --- | --- |
| `GET /health` | 存活 + Key 是否加载 |
| `GET /api/admin/card?session_id=` | 看这张卡的完整落库内容（含 card_id、解析出的 POI 文本） |
| `GET /api/admin/raw?n=20` | 看最近 N 次 `/mcp` 的 headers 和 body |
| `POST /api/query-transit` | 不接 Agent 也能用 curl 直接验腾讯与排版 |
