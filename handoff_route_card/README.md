# 路线卡服务 · 部署交接

给 `<服务器公网IP>`（`keleshiyizhimao.com`）加一个公网 HTTPS 路径，
让墨水屏能把"刚查出来的公交路线"显示出来。

**只新增，不改现有配置。** 现在这台机器上跑着 CloudMood，本包的 nginx 改动是
新增两个 `location`，不动 `server` 块的其他内容，也不碰 CloudMood 的任何文件。

---

## 这个服务干什么

一块小服务，两个入口，共用一张表：

```
用户说话 → Agent 调 query_transit → 查腾讯公交 → 写一张卡进这个会话
                                                        ↓
板子按键 → GET /api/route-card?session_id=... → 读到同一张卡 → 上屏
```

共用一张表是为了保证"模型嘴里说的"和"屏幕显示的"是同一次查询，不会出现
说 A 路线、屏上出 B 路线的情况。

- 纯标准库，**不装任何包**，Python 3.8+ 即可
- 数据落 sqlite，单文件，备份就是复制一个文件
- 地图 Key 只在服务器的 `.env` 里，不进固件、不进仓库

---

## 部署（约 5 分钟）

### 1. 放文件

把整个文件夹原样传到 `/opt/route-card`（保持目录结构，systemd 与脚本里的路径都按这个写死了）：

```bash
# 在本地机器上执行
scp -r handoff_route_card root@<服务器公网IP>:/opt/route-card

# 或者在服务器上执行
sudo mkdir -p /opt/route-card/data
sudo chown -R www-data:www-data /opt/route-card/data
sudo cp app/.env.example /opt/route-card/.env
sudo nano /opt/route-card/.env          # 填 TENCENT_KEY
sudo chmod 600 /opt/route-card/.env
```

> `.env` 里填 `TENCENT_KEY` 和平台注册返回的 `CONNECTOR_KEY`。`PORT` 和 `DB_PATH` 保持默认即可。
> `CONNECTOR_KEY` 只存服务器，不能写进固件、仓库或日志。配置后 `/mcp` 的所有请求都必须带 `Authorization: Bearer <connectorKey>`。

### 2. 先手工跑起来看有没有报错

```bash
cd /opt/route-card/app
sudo -u www-data env $(grep -v '^#' /opt/route-card/.env | xargs) \
  python3 route_card_service.py --host 127.0.0.1 --port 8010 --db /opt/route-card/data/route_card.sqlite3
```

另开一个终端：

```bash
curl -sS http://127.0.0.1:8010/health
```

看到 `"code":0` 且 `"key":"loaded"` 就是好了。有 `"key":"missing"` 说明 `.env` 没读进去。
按 Ctrl+C 停掉，继续下一步。

### 3. 装 systemd

```bash
sudo cp systemd/route-card.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now route-card
sudo systemctl status route-card --no-pager
sudo tail -n 20 /var/log/route-card.log
```

### 4. 加 nginx 路由

打开 `keleshiyizhimao.com` 那个 server 块（通常是 `/etc/nginx/conf.d/*.conf`
或 `/etc/nginx/sites-enabled/*`），把 `nginx/route-card.conf` 里的四段 `location`
贴进去，`server` 块其他内容一个字都别动。

```bash
sudo nginx -t          # 必须先过这一步
sudo systemctl reload nginx
```

`nginx -t` 报错就不要 reload —— 报错时 nginx 仍在用旧配置，CloudMood 没受影响。

### 5. 验收

```bash
cd /opt/route-card/test
set -a; source /opt/route-card/.env; set +a
bash verify.sh https://keleshiyizhimao.com
```

全部 PASS 才算部署成功。它会真实查一次腾讯（约 1-2 秒）；配置 `CONNECTOR_KEY` 时还会额外验证未授权请求返回 401。

---

## 部署完要回传给我的三个值

板端固件要用这三样东西才能接上：

| 项 | 期望值 | 怎么确认 |
| --- | --- | --- |
| 完整地址 | `https://keleshiyizhimao.com/api/route-card?session_id=%s` | 看第 5 步 PASS |
| 认证方式 | 无额外 token，`session_id` 本身即凭证 | 同上 |
| 示例响应 | `test/sample_success.json` 的效果，但要是**你机器上真跑出来的** | 第 5 步第 4 节会打印 |

**必须是 `keleshiyizhimao.com`，不能用 `feileme.` 子域，也不能用 IP。** 两个原因：

1. `feileme.` 子域该 server 块整站 301 跳到 apex，板端 `disable_auto_redirect=true`，收到 301 直接报"查询失败"。
2. 那张证书的 CN 是 `feileme.keleshiyizhimao.com`，用 IP 访问会主机名不匹配，证书校验挂。

---

## 平台挂载前还必须做的事

### 1. 部署 1.1.0 并配置 connectorKey

2026-10-24 21:50 本地验收的 1.1.0 已补齐两项官方协议要求：

- 从 `params._meta["tidb-link/context"]` 读取平台签发的 `sessionId` 与 `callId`。
- 配置 `CONNECTOR_KEY` 后，`initialize` / `tools/list` / `tools/call` 都校验 Bearer；鉴权头写入 `raw_log` 前会脱敏。

正式模式下只信 Call Context，模型在 `arguments` 里填的 `session_id` 不会用于写卡。
未配置 `CONNECTOR_KEY` 时保留旧参数兼容，仅供本地/演示，不能当正式安全配置。

### 2. 让平台真正调到这个 Connector

服务侧已经按 MCP 实现了 `initialize` / `tools/list` / `tools/call`，但**还没在真实平台验证过**。
上游文档写的是 `streamable_http`，我们返回的是 `application/json` 而不是 SSE 流。
如果平台只认 SSE，需要改返回格式——这是已知风险，没有实测样本。

Console 的 Template editor → **MCP & Connectors** 中选择同组织、已激活的 Connector 并保存。
这一步没有 Customer API 替代。挂载后必须**新建 Session**（模板配置在 Session 创建时冻结，旧 Session 不会继承）。

### 3. 第一次真实调用后核对原始上下文

```bash
curl -sS 'http://127.0.0.1:8010/api/admin/raw?n=5'
```

确认请求里有 `params._meta["tidb-link/context"].sessionId`，且与板子选择的 Session 一致。
`Authorization` 应显示为 `[REDACTED]`，不能把真实 connectorKey 贴进聊天或文档。

---

## 回滚

```bash
sudo systemctl disable --now route-card
sudo rm /etc/systemd/system/route-card.service
# 然后从 nginx 配置里删掉那四段 location
sudo nginx -t && sudo systemctl reload nginx
```

nginx 配置改之前建议先备份：

```bash
sudo cp /etc/nginx/conf.d/<你的配置文件> /root/nginx-backup-$(date +%F-%H%M).conf
```

---

## 目录说明

```
app/route_card_service.py   服务本体，单文件，零依赖
app/.env.example            配置模板（复制成 /opt/route-card/.env）
nginx/route-card.conf       要贴进 server 块的 location 段
systemd/route-card.service  开机自启
test/verify.sh              一键验收，七项检查
test/why_no_card.py         板子屏幕报没有路线时先跑这个，自动定位断点
test/sample_success.json    成功响应的样子
test/sample_fail.json       失败响应的样子
API.md                      完整契约：两套入口、错误码、硬约束、调试接口
```

## 排障速查

| 现象 | 先看 |
| --- | --- |
| 板子屏幕显示「还没有查到路线」 | 这不是网络问题，是服务端正常应答。先跑 `python3 test/why_no_card.py`，它会直接说出板子要的会话和库里卡的会话差在哪 |
| 想看板子到底发了什么 | `grep 'GET /api/route-card' /var/log/route-card.log | tail -5`，日志里有板子的真实 `session_id` 和返回字节数（空卡固定 44B） |
| `verify.sh` 第 0 项就失败 | `systemctl status route-card`，看 8010 有没有起来 |
| 第 1 项 HTTP 不是 200 | 是不是贴进了 `feileme.` 那个 server 块；或者 `location` 少了 `=` |
| 第 4 项 code=3 | 工具的 sessionId 没取到，去 `/api/admin/raw` 看真实字段名 |
| 第 4 项 code=6 | 腾讯 Key 没配或没 WebServiceAPI 权限，看 `/var/log/route-card.log` |
| 第 5 项 card_id 变了 | 幂等没生效，日志里应该没有 `[idem]` 行 |
| 板子上显示"网络不好，请再试" | 传输层失败，看板端串口 `https: end err=`；`WS_ROUTE_SESSION_ENDPOINT` 是否填了域名而不是 IP |
| 板子上显示"接口不存在" | HTTP 404，nginx 的 location 没生效（少了 `=` 或贴错 server 块） |
