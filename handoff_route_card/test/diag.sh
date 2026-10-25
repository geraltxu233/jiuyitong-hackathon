#!/usr/bin/env bash
# 部署自检：在服务器上执行，把整段输出贴回来。
# 用法： sudo bash diag.sh
# 只做检查和一次明确标注的写测试，不改配置、不重启服务。
set -u

PORT="${PORT:-8010}"
DBPATH="${DB_PATH:-/opt/route-card/data/route_card.sqlite3}"
OUT=0
say()  { printf '\n=== %s ===\n' "$1"; }
ok()   { printf '[OK]   %s\n' "$1"; }
bad()  { printf '[BAD]  %s\n' "$1"; OUT=1; }
warn() { printf '[WARN] %s\n' "$1"; }

# 只打本机回环。用函数而不是字符串变量：--noproxy '*' 里的 * 放进变量会被
# bash 当通配符展开成当前目录的一堆文件名。
curl_local() { curl -sS --noproxy '*' --max-time 20 "$@"; }

say "0. 我到底在哪台机器上"
echo "hostname : $(hostname)"
echo "kernel   : $(uname -sr)"
echo "时间     : $(date '+%F %T %Z')"
echo "IPv4     : $(hostname -I 2>/dev/null || echo '(取不到)')"
if [ "$(id -u)" != "0" ]; then
  warn "不是 root，部分检查会失败；建议 sudo bash diag.sh"
fi

say "1. 服务进程与端口"
# ss 只是旁证：没权限、没装 ss、或跑在容器里都可能看不到，不能据此判死。
# 真正的判据是第 2 节的直连健康检查。
SS_OUT=$(ss -lntp 2>/dev/null | grep ":$PORT")
if [ -n "$SS_OUT" ]; then
  echo "$SS_OUT"
  ok "ss 看到有进程监听 $PORT"
else
  warn "ss 看不到 $PORT（可能没权限 / 没装 ss / 在容器里）；以第 2 节直连结果为准"
fi
if systemctl is-active --quiet route-card 2>/dev/null; then
  ok "systemd 单元 route-card 处于 active"
elif command -v systemctl >/dev/null 2>&1; then
  warn "route-card 不是 active：$(systemctl is-active route-card 2>&1)"
  systemctl status route-card --no-pager -n 20 2>&1 | tail -20
else
  warn "没有 systemctl，可能没按 systemd 方式部署"
fi

say "2. 本机直连服务（不经过 nginx）"
H=$(curl_local "http://127.0.0.1:$PORT/health" 2>&1)
if printf '%s' "$H" | grep -q '"code":0'; then
  ok "http://127.0.0.1:$PORT/health 正常（服务确实活着，且 nginx 的上游可达）"
  echo "       $H"
else
  bad "直连失败：$H"
  echo "       这一项失败才是真的没起来。上面的 ss 只是旁证。"
fi
if printf '%s' "$H" | grep -q '"key":"loaded"'; then
  ok "腾讯 Key 已加载"
else
  warn "腾讯 Key 未加载（查 /opt/route-card/.env）"
fi

say "3. 服务端整条链路写读测试（会真查一次腾讯）"
SID="diag_$(date +%s)"
SEED=$(curl_local -X POST "http://127.0.0.1:$PORT/api/query-transit" \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"city\":\"上海\",\"origin_text\":\"西岸国际会展中心\",\"destination_text\":\"复旦大学附属中山医院\",\"travel_mode\":\"transit\"}" 2>&1)
if printf '%s' "$SEED" | grep -q '"is_error":false'; then
  ok "写入成功（会话 $SID）"
else
  bad "写入失败：$(printf '%s' "$SEED" | head -c 400)"
fi
CARD=$(curl_local "http://127.0.0.1:$PORT/api/route-card?session_id=$SID" 2>&1)
if printf '%s' "$CARD" | grep -q '"code":0'; then
  ok "回读成功，服务端链路完整"
  echo "       $(printf '%s' "$CARD" | head -c 400)"
else
  bad "回读失败：$(printf '%s' "$CARD" | head -c 300)"
fi

say "4. Agent 有没有真的调过工具（raw_log）"
RAW=$(curl_local "http://127.0.0.1:$PORT/api/admin/raw?n=5" 2>&1)
if printf '%s' "$RAW" | grep -q '"items":\[\]'; then
  warn "raw_log 是空的 —— 平台从未请求过 /mcp。问题在 Connector 挂载，不在板子"
elif printf '%s' "$RAW" | grep -q '"code":0'; then
  ok "有入站请求，原始记录如下（重点看 headers 里的 session 字段名）"
  printf '%s' "$RAW" | head -c 4000
  echo
else
  bad "读 raw_log 失败：$(printf '%s' "$RAW" | head -c 300)"
fi

say "5. 库里有什么：板子上该选哪个会话"
CARDS=$(curl_local "http://127.0.0.1:$PORT/api/admin/cards?n=10" 2>&1)
if printf '%s' "$CARDS" | grep -q '"counts"'; then
  ok "统计：$(printf '%s' "$CARDS" | sed -n 's/.*"counts":\([^}]*}\).*/\1/p')"
  echo "  最近的卡（板子上要选右边这个 session_id 对应的会话）："
  printf '%s' "$CARDS" | python3 -c '
import json, sys
d = json.load(sys.stdin)
items = d.get("items") or []
if not items:
    print("    一张卡都没有 —— 说明还没人成功查过路")
for it in items:
    print("    session=%s  card_id=%s  code=%s  %s  %s" % (
        it.get("session_id"), it.get("card_id"), it.get("code"),
        it.get("title"), it.get("built_at")))
' 2>/dev/null || printf '%s\n' "$CARDS"
else
  warn "读不到 /api/admin/cards（服务版本较旧，或用 sqlite 兜底看）"
  if command -v python3 >/dev/null; then
    python3 - "$DBPATH" 2>&1 <<'PY' || true
import glob, sqlite3, sys
from pathlib import Path
def open_ro(path):
    try:
        return sqlite3.connect(Path(path).resolve().as_uri() + "?immutable=0&mode=ro", uri=True)
    except Exception:
        return sqlite3.connect(path)
for path in dict.fromkeys([sys.argv[1]] + glob.glob("/opt/route-card/**/*.sqlite3", recursive=True)):
    try:
        db = open_ro(path)
        for row in db.execute("SELECT session_id, card_id, code, title, built_at FROM cards ORDER BY built_at DESC LIMIT 10"):
            print("    session=%s card_id=%s code=%s %s %s" % row)
        db.close()
    except Exception as exc:
        print("    %s -> 打不开：%s" % (path, exc))
PY
  fi
fi

say "6. nginx 里的 location 是否落在对的 server 块"
NG=$(nginx -T 2>/dev/null | grep -n "route-card\|server_name\|allow 127.0.0.1" | head -40)
if [ -n "$NG" ]; then
  echo "$NG"
else
  warn "读不到 nginx 配置（需要 root）"
fi

say "7. 最近日志"
tail -n 25 /var/log/route-card.log 2>/dev/null || warn "读不到 /var/log/route-card.log"

say "结论"
if [ "$OUT" -eq 0 ]; then
  echo "服务端本地链路全部正常。板子如果还不行，问题在『选了哪个会话』或『平台有没有调工具』。"
else
  echo "上面标 [BAD] 的项就是断点，按顺序修。"
fi
