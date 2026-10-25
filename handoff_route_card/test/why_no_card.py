#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回答一个问题：板子拿着哪个会话号来要卡，库里的卡存在哪个会话号下。

屏幕显示"还没有查到路线"时，网络这层已经不用怀疑了——那句话是服务端发出去的。
剩下只有三种可能，这个脚本一次性把它们分开：

  1. 服务端日志里根本没有 GET /api/route-card   → 请求没到这台机器
  2. 有请求，但 session_id 是空的              → 板子没选会话，走了局域网演示地址
  3. 有请求、session_id 也有值，但库里没这个号  → 卡写在别的会话下，或压根没写过

用法（在服务器上，和 route_card_service.py 同一台）：
    python3 why_no_card.py
    python3 why_no_card.py --db /opt/route-card/data/route_card.sqlite3
    python3 why_no_card.py --board-session <从板子上抄下来的会话号>

只读，不写库、不改配置、不重启服务。
"""

import argparse
import glob
import http.client
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

DEFAULT_DB_CANDIDATES = (
    "/opt/route-card/data/route_card.sqlite3",
    "/opt/route-card/route_card.sqlite3",
    "/opt/route-card/app/route_card.sqlite3",
)

LOG_CANDIDATES = (
    "/var/log/route-card.log",
    "/opt/route-card/route-card.log",
    "/opt/route-card/app/route-card.log",
)

NGINX_LOG_CANDIDATES = (
    "/var/log/nginx/access.log",
    "/var/log/nginx/keleshiyizhimao.access.log",
)

GET_RE = re.compile(r"GET\s+/api/route-card\?session_id=([^\s&]*)")
PEER_RE = re.compile(r"\]\s+(\S+)\s+GET\s+/api/route-card")


def section(title):
    print()
    print("=" * 62)
    print(title)
    print("=" * 62)


def read_lines(path, limit=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except Exception as exc:                                  # noqa: BLE001
        return None, str(exc)
    return (lines[-limit:] if limit else lines), None


def service_log_lines(limit=400, override=""):
    """优先读文件；读不到就走 journalctl；都不行返回空。"""
    for path in ([override] if override else []) + list(LOG_CANDIDATES):
        if os.path.exists(path):
            lines, err = read_lines(path, limit)
            if lines is not None:
                return lines, path
    try:
        out = subprocess.run(
            ["journalctl", "-u", "route-card", "-n", str(limit), "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=20, check=False)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.splitlines(), "journalctl -u route-card"
    except Exception:                                         # noqa: BLE001
        pass
    return [], "(没有找到服务日志)"


def nginx_lines(limit=4000):
    for path in NGINX_LOG_CANDIDATES:
        if os.path.exists(path):
            lines, _ = read_lines(path, limit)
            if lines:
                return lines, path
    return [], "(没有找到 nginx access 日志)"


def open_db(path):
    if not path or not os.path.exists(path):
        return None
    try:
        return sqlite3.connect("file:%s?mode=ro" % os.path.abspath(path), uri=True)
    except Exception:                                         # noqa: BLE001
        try:
            return sqlite3.connect(path)
        except Exception:                                     # noqa: BLE001
            return None


def find_db(explicit):
    if explicit:
        return explicit
    for path in DEFAULT_DB_CANDIDATES:
        if os.path.exists(path):
            return path
    hits = glob.glob("/opt/**/*.sqlite3", recursive=True) + glob.glob("/srv/**/*.sqlite3", recursive=True)
    return hits[0] if hits else ""


def table_counts(db):
    out = {}
    for table in ("sessions", "cards", "calls", "raw_log"):
        try:
            out[table] = db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        except Exception:                                     # noqa: BLE001
            out[table] = -1
    return out


def cards_list(db, limit=15):
    try:
        return db.execute(
            "SELECT session_id, card_id, code, title, message, built_at "
            "FROM cards ORDER BY built_at DESC LIMIT ?", (limit,)).fetchall()
    except Exception:                                         # noqa: BLE001
        return []


def sessions_list(db, limit=15):
    try:
        return db.execute(
            "SELECT session_id, note, created_at, last_seen FROM sessions "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    except Exception:                                         # noqa: BLE001
        return []


def call_sessions(db, limit=15):
    try:
        return db.execute(
            "SELECT call_id, session_id, created_at FROM calls "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    except Exception:                                         # noqa: BLE001
        return []


def probe_health(port):
    """直连回环，绕开系统代理。

    这台机器上 http_proxy 很常见（我们踩过一次：请求被代理接走后回 502，
    看起来像服务挂了，其实是代理把 127.0.0.1 也拦了）。所以用 http.client，
    它从不读代理环境变量。
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", "/health")
        resp = conn.getresponse()
        return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:                                  # noqa: BLE001
        return {"error": str(exc)}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="")
    parser.add_argument("--log", default="", help="服务日志路径（默认自动找）")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--board-session", default="", help="板子选中的会话号（可选，已知就填）")
    args = parser.parse_args()

    print("路线卡排查：板子要的会话 vs 库里存的会话")
    print("机器时间 %s" % time.strftime("%Y-%m-%d %H:%M:%S %Z"))

    # ---------------------------------------------------------------- 1
    section("一、服务是不是活着")
    health = probe_health(args.port)
    if health.get("code") == 0:
        print("服务在跑：%s" % json.dumps(health, ensure_ascii=False))
        if health.get("key") != "loaded":
            print("注意：腾讯 Key 没加载，写卡一定失败（查 .env 里的 TENCENT_KEY）")
    else:
        print("直连 127.0.0.1:%d 失败：%s" % (args.port, health.get("error")))
        print("服务都没起来，先解决这个，后面不用看。")

    # ---------------------------------------------------------------- 2
    section("二、板子到底有没有来要卡（服务端访问日志）")
    log_lines, log_source = service_log_lines(override=args.log)
    print("日志来源：%s" % log_source)
    board_hits = []
    for line in log_lines:
        match = GET_RE.search(line)
        if match:
            peer = PEER_RE.search(line)
            board_hits.append((match.group(1), peer.group(1) if peer else "?", line.strip()))
    if not board_hits:
        print("没有任何 GET /api/route-card 记录。")
        print("说明请求没打到这台服务：查固件里的域名、nginx 的 location、以及安全组。")
    else:
        print("最近 %d 次板端取卡（新→旧）：" % min(len(board_hits), 10))
        for sid, peer, raw in reversed(board_hits[-10:]):
            print("  会话号=%-34s 源地址=%-12s %s" % (sid or "(空)", peer, raw[:90]))

    # ---------------------------------------------------------------- 3
    section("三、nginx 那边看到了什么（含板子真实公网 IP）")
    ng_lines, ng_source = nginx_lines()
    print("日志来源：%s" % ng_source)
    ng_hits = [line for line in ng_lines if "/api/route-card" in line]
    if not ng_hits:
        print("nginx 日志里没有 /api/route-card。若第二节也没记录，问题在 nginx 之前。")
    else:
        for line in ng_hits[-6:]:
            print("  " + line[:200])

    # ---------------------------------------------------------------- 4
    section("四、库里有什么")
    db_path = find_db(args.db)
    print("数据库：%s" % (db_path or "(没找到)"))
    db = open_db(db_path)
    cards, session_rows, calls = [], [], []
    if db is None:
        print("打不开数据库，跳过本节。可以用 --db 指定路径。")
    else:
        counts = table_counts(db)
        print("行数：sessions=%s cards=%s calls=%s raw_log=%s" % (
            counts["sessions"], counts["cards"], counts["calls"], counts["raw_log"]))
        cards = cards_list(db)
        session_rows = sessions_list(db)
        calls = call_sessions(db)
        if not cards:
            print("cards 表是空的 —— 从来没有人成功写过卡。")
        for row in cards:
            print("  会话=%-34s card=%s code=%s %s %s" % (row[0], row[1], row[2], row[3], row[5]))
        if calls:
            print("最近的工具调用（calls 表，说明平台确实调过 query_transit）：")
            for row in calls[:6]:
                print("  call=%s 会话=%s %s" % (row[0][:16], row[1] or "(空)", row[2]))
        else:
            print("calls 表是空的 —— 平台没有成功调用过 query_transit（或没带 callId）。")
        if counts["raw_log"] == 0:
            print("raw_log 是空的 —— 平台从未请求过 /mcp，Connector 这一环还没通。")

    # ---------------------------------------------------------------- 5
    section("五、结论")
    board_sessions = list(dict.fromkeys(sid for sid, _, _ in board_hits if sid))
    card_sessions = [row[0] for row in cards]
    given = args.board_session.strip()

    if not board_hits:
        if health.get("code") != 0:
            verdict = (
                "服务本身没起来，板子当然拿不到卡。\n"
                "  下一步：systemctl status route-card，先让 /health 能直连返回 code 0。"
            )
        else:
            verdict = (
                "板子的请求没有到达这台服务（服务是活的，日志里没有 GET）。\n"
                "  下一步：在板端串口日志里找 https: end 这一行，看 err 与 status。\n"
                "  如果 err 不是 ESP_OK，是 TLS 或域名问题；如果是 404，是 nginx location 问题。\n"
                "  如果 status=200 但日志没记录，说明请求打到了另一台机器（域名解析到了别处）。"
            )
    elif not board_sessions:
        verdict = (
            "板子来要卡了，但没带会话号（session_id 为空）。\n"
            "  这说明按键时板子没有选中任何会话，走了局域网演示地址。\n"
            "  下一步：先在板子上重新选一次会话，再按中键查路线。"
        )
    elif given and given in card_sessions:
        verdict = "板子要的会话 %s 库里有卡，屏幕却报没有路线，属于固件或缓存问题。" % given
    elif given:
        verdict = (
            "板子要的会话 %s 库里没有卡（库里只有 %s）。\n"
            "  下一步：用第六节那条命令，把卡直接写到板子的会话号上，再按一次中键。" %
            (given, "、".join(card_sessions[:5]) or "空"))
    else:
        # 板子可能按过多次、中途还换过会话，所以只看最新一次请求的那个会话号。
        target = board_sessions[-1]
        target_card = next((row for row in cards if row[0] == target), None)
        if target_card is None:
            verdict = (
                "对不上。板子最新一次要的会话是 %s，库里的卡在 %s。\n"
                "  说明平台写卡时的会话号和板子取卡时的会话号不是一个东西。\n"
                "  下一步：用第六节那条命令把卡写到 %s 上，屏幕立刻就能出图。" %
                (target, "、".join(card_sessions[:3]) or "(空)", target))
        elif target_card[2] == 0:
            verdict = (
                "会话 %s 已经有正常卡（code=0，title=%s），这张卡是好的。\n"
                "  屏幕还报没有路线，只可能是那次按键发生在这张卡写入之前。\n"
                "  下一步：回板子上再按一次中键重试。若仍失败，看串口 bytes 是不是 44：\n"
                "  44 说明板子读到的还是空卡响应，可以排除网络问题。" % (target, target_card[3]))
        else:
            verdict = (
                "会话 %s 有卡，但那张卡本身是失败态（code=%s，message=%s）。\n"
                "  屏幕显示的是上次查询的失败原因，不是网络问题。\n"
                "  下一步：重新调一次 query_transit，或直接用第六节那条命令覆盖成正常卡。" %
                (target, target_card[2], target_card[4] or "(空)"))
    print(verdict)
    print()

    # ---------------------------------------------------------------- 6
    section("六、把卡直接写到板子的会话号上（最快让屏幕出真卡）")
    target = given or (board_sessions[-1] if board_sessions else "<把上面抄下来的会话号填这里>")
    print("在服务器上执行这一条，把 <会话号> 换成 %s：" % target)
    print()
    print('curl -sS -X POST http://127.0.0.1:%d/api/query-transit \\' % args.port)
    print('  -H "Content-Type: application/json" \\')
    print('  -d \'{"session_id":"%s","city":"上海","origin_text":"西岸国际会展中心",'
          '"destination_text":"复旦大学附属中山医院","travel_mode":"transit"}\'' % target)
    print()
    print("然后回板子上：长按中键 → 中键（第 1 项 去医院）。")
    print("屏幕出 5 行路线，就证明板子到服务端到屏幕整条链路完全可用，")
    print("剩下的问题只在『平台调工具时用的会话号』和『板子选的会话』是否同一个。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
