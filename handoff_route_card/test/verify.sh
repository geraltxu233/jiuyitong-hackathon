#!/usr/bin/env bash
# 部署后一键验收。用法：
#   ./verify.sh https://keleshiyizhimao.com
#   ./verify.sh http://127.0.0.1:8010        # 只在服务器本机自测
# 只读，不改配置；会真实查一次腾讯（约 1-2 秒）。
set -u

BASE="${1:-http://127.0.0.1:8010}"
SESSION="verify_$(date +%s)"
PASS=0
FAIL=0

# 板端 disable_auto_redirect=true：-L 0 是刻意不加的参数，任何 3xx 都必须算失败。
# --noproxy：这台机器若有全局代理，回环请求会被绕出去。
CURL=(curl -sS --noproxy '*' --max-time 25)
AUTH=()
if [ -n "${CONNECTOR_KEY:-}" ]; then
  AUTH=(-H "Authorization: Bearer ${CONNECTOR_KEY}")
fi

ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m %s\n' "$1"; }
head1(){ printf '\n\033[1m%s\033[0m\n' "$1"; }
body() { "${CURL[@]}" "$1"; }

head1 "0) 连通性"
HEALTH=$(body "$BASE/health" 2>/dev/null || true)
if printf '%s' "$HEALTH" | grep -q '"code":0'; then
  ok "GET /health -> $HEALTH"
else
  bad "服务没起来：$HEALTH"
  printf '\n结果：%d 通过 / %d 失败（后面的不必测）\n' "$PASS" "$FAIL"
  exit 1
fi

head1 "1) 直返 200（板端不接受任何跳转）"
HC=$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/health")
if [ "$HC" = "200" ]; then
  ok "HTTP $HC，没有 301/302，证书链对板端有效"
else
  bad "HTTP $HC；板端只认 200，3xx 会让屏幕直接显示查询失败"
fi

head1 "2) 未知会话不泄露、不伪造"
EMPTY=$(body "$BASE/api/route-card?session_id=$SESSION")
if printf '%s' "$EMPTY" | grep -q '"code":3'; then
  ok "返回 code=3：$EMPTY"
else
  bad "未知会话应先返回 code=3，实际：$EMPTY"
fi

head1 "3) 真实查一次路（官方 Call Context + Bearer 鉴权）"
CALL="verify_call_$(date +%s)"
if [ -n "${CONNECTOR_KEY:-}" ]; then
  UNAUTH=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/mcp" \
    -H 'Content-Type: application/json' -d '{}')
  if [ "$UNAUTH" = "401" ]; then
    ok "缺少 Bearer 时返回 401，未授权请求不能调用工具"
  else
    bad "缺少 Bearer 应返回 401，实际 HTTP $UNAUTH"
  fi
fi
RESP=$("${CURL[@]}" -X POST "$BASE/mcp" \
  -H 'Content-Type: application/json' "${AUTH[@]}" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"query_transit\",\"arguments\":{\"city\":\"上海\",\"origin_text\":\"西岸国际会展中心\",\"destination_text\":\"复旦大学附属中山医院\",\"travel_mode\":\"transit\"},\"_meta\":{\"tidb-link/context\":{\"callId\":\"$CALL\",\"sessionId\":\"$SESSION\"}}}}")
if printf '%s' "$RESP" | grep -q '"isError":false'; then
  ok "tools/call 成功，会话 $SESSION 已生成路线卡"
  printf '      模型会这样说：%s\n' "$(printf '%s' "$RESP" | sed -n 's/.*"text":"\([^"]*\)".*/\1/p' | head -c 260)"
else
  bad "tools/call 失败：$(printf '%s' "$RESP" | head -c 400)"
fi

head1 "4) 板端拉同一张卡（四字段 + 体积 + 行宽）"
CARD=$(body "$BASE/api/route-card?session_id=$SESSION")
CB=$("${CURL[@]}" "$BASE/api/route-card?session_id=$SESSION" | wc -c | tr -d ' ')
printf '%s' "$CARD" | python3 -c '
import json, sys, unicodedata
raw = sys.stdin.read()
try:
    card = json.loads(raw)
except ValueError:
    print("  \033[31mFAIL\033[0m 返回的不是合法 JSON：%s" % raw[:200]); sys.exit(1)

fail = 0
if card.get("code") != 0:
    print("  \033[31mFAIL\033[0m code=%s message=%s" % (card.get("code"), card.get("message"))); sys.exit(1)
print("  \033[32mPASS\033[0m code=0")

extra = set(card) - {"code", "title", "lines", "source"}
if extra:
    print("  \033[31mFAIL\033[0m 多了调试字段，不该回给板子：%s" % ", ".join(sorted(extra)))
else:
    print("  \033[32mPASS\033[0m 只回四字段 code/title/lines/source")

lines = card.get("lines") or []
if 0 < len(lines) <= 9:
    print("  \033[32mPASS\033[0m %d 行，一屏出得完（上限 9）" % len(lines))
else:
    print("  \033[31mFAIL\033[0m %d 行，超过一屏 9 行" % len(lines))

def width(text, small=False):
    cjk, ascii_ = (28, 12) if small else (56, 24)
    return sum(cjk if unicodedata.east_asian_width(c) in ("W", "F") else ascii_ for c in text)

wide = 0
print("      title: %s" % card.get("title", ""))
for line in lines:
    w = width(line)
    wide += w > 696
    print("      %4dpx | %s%s" % (w, line, "   <-- 超宽会被画省略号" if w > 696 else ""))
print("      source: %s" % card.get("source", ""))
if wide:
    print("  \033[31mFAIL\033[0m %d 行超过 696 px" % wide)
else:
    print("  \033[32mPASS\033[0m 所有行 ≤ 696 px")

if width(card.get("source", ""), small=True) <= 440:
    print("  \033[32mPASS\033[0m source ≤ 440 px（页脚 28 px 字号）")
else:
    print("  \033[31mFAIL\033[0m source 超过 440 px，页脚会被截断")
' || FAIL=$((FAIL+1))

if [ "$CB" -le 2048 ]; then
  ok "响应体 $CB 字节 ≤ 2048（板端缓冲上限）"
else
  bad "响应体 $CB 字节超过 2048，板端会截断成坏 JSON"
fi

head1 "5) 幂等：同一个 callId 重放不重复查腾讯"
# 判据不看耗时（受网络与代理影响），看卡的版本号：幂等命中时 card_id 不变，
# 一旦重查腾讯就会写新卡、card_id 自增。
CID_BEFORE=$("${CURL[@]}" "$BASE/api/admin/card?session_id=$SESSION" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("raw_card_id"))' 2>/dev/null || echo "?")
T0=$(date +%s%N)
"${CURL[@]}" -o /dev/null -X POST "$BASE/mcp" \
  -H 'Content-Type: application/json' "${AUTH[@]}" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"query_transit\",\"arguments\":{\"city\":\"上海\",\"origin_text\":\"西岸国际会展中心\",\"destination_text\":\"复旦大学附属中山医院\"},\"_meta\":{\"tidb-link/context\":{\"callId\":\"$CALL\",\"sessionId\":\"$SESSION\"}}}}"
T1=$(date +%s%N)
CID_AFTER=$("${CURL[@]}" "$BASE/api/admin/card?session_id=$SESSION" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("raw_card_id"))' 2>/dev/null || echo "?")
MS=$(( (T1 - T0) / 1000000 ))
if [ "$CID_BEFORE" = "$CID_AFTER" ]; then
  ok "重放后 card_id 仍是 $CID_AFTER，没有生成新卡（${MS}ms）"
else
  bad "card_id 从 $CID_BEFORE 变成 $CID_AFTER，说明又查了一次腾讯；检查日志里有没有 [idem] 命中"
fi

head1 "6) 地点不全不瞎猜"
NOPE=$("${CURL[@]}" -X POST "$BASE/mcp" -H 'Content-Type: application/json' "${AUTH[@]}" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"query_transit","arguments":{"city":"上海","origin_text":""}}}')
if printf '%s' "$NOPE" | grep -q '"isError":true'; then
  ok "缺起点时 isError=true，Agent 会先追问再查"
else
  bad "缺起点时应当报错：$(printf '%s' "$NOPE" | head -c 300)"
fi

printf '\n\033[1m结果：%d 通过 / %d 失败\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
