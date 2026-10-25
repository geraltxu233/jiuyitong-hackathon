#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路线卡服务：给墨水屏拉卡，同时给 Agent 提供 query_transit 工具。

一块服务干两件事：
  1. GET  /api/route-card?session_id=...   板子按键时来拉"这个会话最新的一张卡"
  2. POST /mcp                             Agent 调 query_transit，查路并把卡写进同一个会话
两件事共用一张 cards 表，所以"模型说的"和"屏幕显示的"必然是同一次查询。

只依赖标准库，不装任何包。部署目标：Linux + Python 3.8+。

几条硬约束（来自 firmware/apps/waveshare-epaper-397-wifi/route_card.h）：
  - 一次 GET 必须直返 200，不能 301/302；板端 disable_auto_redirect=true
  - 响应体 ≤ 2048 字节；板端缓冲区只有这么大，超了直接截断成坏 JSON
  - 正文目标 5 行，硬上限 9 行（一屏 9 行，超过要按键翻页，老人不会按）
  - 每行 ≤ 696 px（汉字 56 px），超宽上屏会被画省略号
  - 不用长连接；每个响应都带 Content-Length 并 Connection: close
"""

import argparse
import hmac
import json
import os
import re
import sqlite3
import sys
import threading
import time
import traceback
import unicodedata
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------- 常量与口径

APP_NAME = "carecard-route-card"
APP_VERSION = "1.1.0"

# 板端大字页可用宽度：800 - 20(左) - 20(右) - 64(省略号留位)。与 tools/check_route_card.py 同口径。
LINE_BUDGET_PX = 696
CJK_PX, ASCII_PX = 56, 24
SOURCE_BUDGET_PX = 440      # 页脚 28 px 一行，约 15 个汉字
CARD_LINES = 5              # 目标行数：一屏出完
MAX_LINES = 9               # 硬上限：板端一页只有 9 个逻辑行槽
BODY_LIMIT = 2000           # 板端缓冲 2048，留 48 字节余量给截断判断

TENCENT_TIMEOUT = 3.0       # 板子最多等 8 秒，腾讯侧 3 秒硬超时后回缓存
WALK_BUCKET_M = 200         # 步行按 200 米分档：差不到一档就别为它多花 20 分钟

POLICIES = ("RECOMMEND", "LEAST_TRANSFER")
DROP_ORDER = ("walk_mid", "board", "off_last")

# 错误码：0 成功；其余一律给一句能直接上屏的中文。
CODE_OK = 0
CODE_BAD_PARAM = 2
CODE_NO_ROUTE = 3
CODE_NO_ENDPOINT = 4
CODE_NO_PLACE = 5
CODE_UPSTREAM = 6

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "route_card.sqlite3")


# ---------------------------------------------------------------- 腾讯地图

def load_key():
    key = os.environ.get("TENCENT_KEY", "").strip()
    if key:
        return key
    for name in ("tencent_key.txt", os.path.join("..", "backend", "tencent_key.txt"),
                 os.path.join("..", "..", "backend", "tencent_key.txt")):
        path = os.path.join(HERE, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip()
    return ""


def tencent_get(path, params, key):
    params = dict(params)
    params["key"] = key
    url = "https://apis.map.qq.com" + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": APP_NAME + "/" + APP_VERSION})
    with urllib.request.urlopen(request, timeout=TENCENT_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _text(value):
    """腾讯字段有的是字符串、有的是 {title: ...}，统一取成字符串。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("title") or "").strip()
    return ""


def _short(name):
    """去掉括号补充说明：人民广场(福州路) → 人民广场。行宽只有 12 字。"""
    return re.sub(r"[（(].*?[)）]", "", _text(name)).strip()


def _round10(distance):
    return max(10, int(round(distance / 10.0)) * 10)


def resolve_place(key, city, keyword):
    """把地名解析成坐标。返回 (name, lat, lng, address)；查不到抛 LookupError。

    模型不许自己编坐标，所以这里一定走腾讯的地点搜索，并且限定在城市范围内。
    """
    data = tencent_get("/ws/place/v1/search", {
        "keyword": keyword,
        "boundary": f"region({city},0)",
        "page_size": 3,
    }, key)
    if data.get("status") != 0:
        raise RuntimeError(f"地点搜索失败 status={data.get('status')} {data.get('message')}")
    items = data.get("data") or []
    if not items:
        raise LookupError(keyword)
    best = items[0]
    location = best.get("location") or {}
    lat, lng = location.get("lat"), location.get("lng")
    if lat is None or lng is None:
        raise LookupError(keyword)
    return _text(best.get("title")) or keyword, lat, lng, _text(best.get("address"))


def _route_stats(route):
    steps = route.get("steps") or []
    walk = sum(int(s.get("distance") or 0) for s in steps if s.get("mode") == "WALKING")
    transits = sum(1 for s in steps if s.get("mode") == "TRANSIT")
    return walk, max(0, transits - 1), int(route.get("duration") or 0)


def _has_night_line(route):
    """夜宵线白天不开，老人也不会等，直接排除。"""
    for step in route.get("steps") or []:
        if step.get("mode") != "TRANSIT":
            continue
        for line in step.get("lines") or []:
            if "夜" in _short(line.get("title")):
                return True
    return False


def pick_route(routes):
    """适老排序：步行分档 > 少换乘 > 总耗时。

    不按米数硬排——实测会出现"为省 57 米多花 20 分钟"的方案，对老人没意义。
    """
    usable = [route for route in routes if not _has_night_line(route)]

    def rank(route):
        walk, transfers, duration = _route_stats(route)
        return (walk // WALK_BUCKET_M, transfers, duration)

    return sorted(usable or routes, key=rank)[0]


def _route_signature(route):
    titles = []
    for step in route.get("steps") or []:
        if step.get("mode") == "TRANSIT":
            lines = step.get("lines") or []
            if lines:
                titles.append(_short(lines[0].get("title")))
    walk, transfers, duration = _route_stats(route)
    return (duration, walk, transfers, tuple(titles))


def collect_routes(key, origin, dest):
    """两个策略都查一遍再合并，代价是多一次 HTTP（合计仍 <1s）。"""
    merged, errors = [], []
    for policy in POLICIES:
        try:
            data = tencent_get("/ws/direction/v1/transit/", {
                "from": origin, "to": dest, "policy": policy,
            }, key)
            if data.get("status") != 0:
                raise RuntimeError(f"status={data.get('status')} {data.get('message')}")
            merged.extend((data.get("result") or {}).get("routes") or [])
        except Exception as exc:                      # noqa: BLE001
            errors.append(f"{policy}: {exc}")
    if not merged:
        raise RuntimeError("; ".join(errors) or "腾讯没有返回可用方案")

    seen, unique = set(), []
    for route in merged:
        signature = _route_signature(route)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(route)
    return unique


def _line_width(text):
    return sum(CJK_PX if unicodedata.east_asian_width(c) in ("W", "F") else ASCII_PX for c in text)


def _clean(text):
    """只清洗，不硬截断：截断交给固件画省略号，老人能看出这行没写完。"""
    text = text.replace("\r", " ").replace("\n", " ").strip()
    width = _line_width(text)
    if width > LINE_BUDGET_PX:
        log(f"[warn] 行超宽 {width}px > {LINE_BUDGET_PX}px，上屏会被截断: {text}")
    return text


def build_lines(route):
    """把腾讯的 steps 拍平成 ≤5 行，顺序即"现在该做什么"。"""
    steps = route.get("steps") or []
    total = len(steps)
    items, skip_board = [], False
    for index, step in enumerate(steps):
        mode = step.get("mode")
        if mode == "WALKING":
            distance = int(step.get("distance") or 0)
            if index == 0:
                items.append(("walk_first", f"步行{_round10(distance)}米到车站"))
            elif index == total - 1:
                items.append(("walk_last", f"下车步行{_round10(distance)}米到院"))
            elif distance > 50:
                items.append(("walk_mid", f"到站步行{_round10(distance)}米换乘"))
            continue
        if mode != "TRANSIT":
            continue
        lines = step.get("lines") or []
        if not lines:
            continue
        line = lines[0]
        title, geton, getoff = _short(line.get("title")), _short(line.get("geton")), _short(line.get("getoff"))
        count = int(line.get("station_count") or 0)
        nxt = steps[index + 1] if index + 1 < total else None
        same_next = (nxt is not None and nxt.get("mode") == "WALKING"
                     and index + 2 < total and steps[index + 2].get("mode") == "TRANSIT"
                     and int(nxt.get("distance") or 0) <= 50)
        has_next_transit = any(s.get("mode") == "TRANSIT" for s in steps[index + 1:])
        if geton and not skip_board:
            items.append(("board", f"在{geton}上车"))
        skip_board = same_next
        items.append(("ride", f"乘{title}坐{count}站"))
        if getoff:
            if has_next_transit:
                items.append(("off_transfer", f"到{getoff}换乘"))
            else:
                items.append(("off_last", f"到{getoff}下车"))

    def drop_one():
        for kind in DROP_ORDER:
            for position, (item_kind, _) in enumerate(items):
                if item_kind == kind:
                    del items[position]
                    return True
        return False

    before = len(items)
    while len(items) > CARD_LINES and drop_one():
        pass
    texts = [_clean(text) for _, text in items]
    if len(texts) > CARD_LINES:            # 换乘段特别多时兜底：保头保尾
        texts = texts[:CARD_LINES - 1] + [texts[-1]]
    if before > len(texts):
        log(f"[compact] {before} 行压到 {len(texts)} 行，一屏出完")
    return texts[:MAX_LINES]


def source_text(duration, transfers, when):
    bits = [f"{duration}分"]
    if transfers:
        bits.append(f"换乘{transfers}次")
    return f"腾讯地图 {' '.join(bits)} {when}"


# ---------------------------------------------------------------- 存储

class Store:
    """一个 sqlite 文件存三张表：会话、卡、调用记录。

    cards 每个 session 只有一行（永远是最新的一张），板子只拉最新，
    所以不需要历史版本；要追溯就看 calls 表。
    """

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self):
        with self.lock, self.db:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id  TEXT PRIMARY KEY,
                device_id   TEXT,
                note        TEXT,
                created_at  TEXT NOT NULL,
                last_seen   TEXT
            );
            CREATE TABLE IF NOT EXISTS cards (
                session_id  TEXT PRIMARY KEY,
                card_id     INTEGER NOT NULL,
                code        INTEGER NOT NULL DEFAULT 0,
                title       TEXT NOT NULL DEFAULT '',
                lines       TEXT NOT NULL DEFAULT '[]',
                source      TEXT NOT NULL DEFAULT '',
                message     TEXT NOT NULL DEFAULT '',
                city        TEXT,
                origin_text TEXT,
                dest_text   TEXT,
                travel_mode TEXT,
                built_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS calls (
                call_id     TEXT PRIMARY KEY,
                session_id  TEXT,
                tool        TEXT,
                request     TEXT,
                result      TEXT,
                created_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS raw_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                at          TEXT NOT NULL,
                path        TEXT,
                headers     TEXT,
                body        TEXT
            );
            """)

    def touch_session(self, session_id, device_id=None, note=None):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO sessions(session_id, device_id, note, created_at, last_seen) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET last_seen=excluded.last_seen, "
                "device_id=COALESCE(excluded.device_id, sessions.device_id)",
                (session_id, device_id, note, now, now))

    def has_session(self, session_id):
        with self.lock:
            row = self.db.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        return row is not None

    def save_card(self, session_id, card):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT card_id FROM cards WHERE session_id=?", (session_id,)).fetchone()
            card_id = (row["card_id"] + 1) if row else 1
            self.db.execute(
                "INSERT INTO cards(session_id, card_id, code, title, lines, source, message, "
                "city, origin_text, dest_text, travel_mode, built_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET card_id=excluded.card_id, code=excluded.code, "
                "title=excluded.title, lines=excluded.lines, source=excluded.source, "
                "message=excluded.message, city=excluded.city, origin_text=excluded.origin_text, "
                "dest_text=excluded.dest_text, travel_mode=excluded.travel_mode, built_at=excluded.built_at",
                (session_id, card_id, card["code"], card.get("title", ""),
                 json.dumps(card.get("lines", []), ensure_ascii=False),
                 card.get("source", ""), card.get("message", ""),
                 card.get("city"), card.get("origin_text"), card.get("dest_text"),
                 card.get("travel_mode"), now))
        return card_id

    def get_card(self, session_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM cards WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return None
        card = dict(row)
        card["lines"] = json.loads(card["lines"] or "[]")
        return card

    def get_call(self, call_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM calls WHERE call_id=?", (call_id,)).fetchone()
        return dict(row) if row else None

    def save_call(self, call_id, session_id, tool, request, result):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO calls(call_id, session_id, tool, request, result, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (call_id, session_id, tool, json.dumps(request, ensure_ascii=False),
                 json.dumps(result, ensure_ascii=False), time.strftime("%Y-%m-%dT%H:%M:%S")))

    def log_raw(self, path, headers, body):
        with self.lock, self.db:
            self.db.execute("INSERT INTO raw_log(at, path, headers, body) VALUES(?,?,?,?)",
                            (time.strftime("%Y-%m-%dT%H:%M:%S"), path,
                             json.dumps(headers, ensure_ascii=False), body))

    def recent_raw(self, limit=20):
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM raw_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def recent_cards(self, limit=20):
        """最近的卡。板子上该选哪个会话，看这里最长的那一列就是。"""
        with self.lock:
            rows = self.db.execute(
                "SELECT session_id, card_id, code, title, source, built_at, "
                "origin_text, dest_text FROM cards ORDER BY built_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(row) for row in rows]

    def counts(self):
        with self.lock:
            def one(sql):
                return self.db.execute(sql).fetchone()[0]
            return {"sessions": one("SELECT COUNT(*) FROM sessions"),
                    "cards": one("SELECT COUNT(*) FROM cards"),
                    "calls": one("SELECT COUNT(*) FROM calls"),
                    "raw_log": one("SELECT COUNT(*) FROM raw_log")}


# ---------------------------------------------------------------- 业务

def build_card(key, session_id, city, origin_text, dest_text, travel_mode="transit"):
    """查一次路，返回 (给屏幕的四字段卡, 给模型口述的一句话)。"""
    if travel_mode != "transit":
        return ({"code": CODE_BAD_PARAM, "message": "只支持公交路线"}, None)

    try:
        origin_name, origin_lat, origin_lng, _ = resolve_place(key, city, origin_text)
    except LookupError:
        return ({"code": CODE_NO_PLACE, "message": "找不到这个起点"}, None)
    except Exception as exc:                          # noqa: BLE001
        log(f"[error] 起点解析失败: {exc}")
        return ({"code": CODE_UPSTREAM, "message": "地图服务忙，稍后再试"}, None)

    try:
        dest_name, dest_lat, dest_lng, dest_address = resolve_place(key, city, dest_text)
    except LookupError:
        return ({"code": CODE_NO_PLACE, "message": "找不到这个终点"}, None)
    except Exception as exc:                          # noqa: BLE001
        log(f"[error] 终点解析失败: {exc}")
        return ({"code": CODE_UPSTREAM, "message": "地图服务忙，稍后再试"}, None)

    log(f"[{time.strftime('%H:%M:%S')}] 解析: {origin_name}({origin_lat},{origin_lng}) "
        f"→ {dest_name}({dest_lat},{dest_lng})")

    try:
        routes = collect_routes(key, f"{origin_lat},{origin_lng}", f"{dest_lat},{dest_lng}")
        route = pick_route(routes)
    except Exception as exc:                          # noqa: BLE001
        log(f"[error] 公交规划失败: {exc}")
        return ({"code": CODE_UPSTREAM, "message": "没查到公交路线"}, None)

    lines = build_lines(route)
    if not lines:
        return ({"code": CODE_NO_ROUTE, "message": "没查到公交路线"}, None)

    walk, transfers, duration = _route_stats(route)
    when = time.strftime("%H:%M")
    card = {
        "code": CODE_OK,
        "title": f"去{dest_name}",
        "lines": lines,
        "source": source_text(duration, transfers, when),
        "city": city, "origin_text": origin_text, "dest_text": dest_text,
        "travel_mode": travel_mode,
    }
    spoken = (f"从{origin_name}到{dest_name}，全程约{duration}分钟，"
              f"步行{walk}米，换乘{transfers}次。"
              f"路线：{'；'.join(lines)}。")
    return (card, spoken)


def normalize_card(card):
    """把卡压成板子能吃的形状：只留四字段（+错误时的 message），≤2048 字节、≤9 行。"""
    if card.get("code", CODE_OK) != CODE_OK:
        payload = {"code": card.get("code", CODE_NO_ROUTE),
                   "message": card.get("message") or "没查到路线"}
        return payload

    payload = {
        "code": CODE_OK,
        "title": card.get("title") or "去医院",
        "lines": list(card.get("lines") or [])[:MAX_LINES],
        "source": card.get("source") or "",
        # 板子用它判断是否是新卡；旧固件不认识这两个字段会忽略，保持兼容。
        "card_id": int(card.get("card_id") or 0),
        "built_at": card.get("built_at") or "",
    }
    for _ in range(MAX_LINES):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) <= BODY_LIMIT:
            return payload
        if not payload["lines"]:
            break
        payload["lines"] = payload["lines"][:-1]
        payload["source"] = payload["source"][:30]
        log(f"[warn] 响应 {len(body)} 字节超限，已丢末行重算")

    return {"code": CODE_NO_ROUTE, "message": "路线太长放不下"}


# ---------------------------------------------------------------- HTTP

LOGGERS_LOCK = threading.Lock()


def log(message):
    with LOGGERS_LOCK:
        print(message, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "CareCardRouteCard/1.0"
    protocol_version = "HTTP/1.1"
    store = None
    key = ""
    connector_key = ""
    card_base = ""

    # 板端要的是"一次读完就关"，所以每个响应都显式给长度并 close。
    def _send(self, payload, status=200, content_type="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else \
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        return len(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        peer = self.client_address[0]
        started = time.time()

        if parsed.path == "/health":
            size = self._send({"code": 0, "name": APP_NAME, "version": APP_VERSION,
                               "key": "loaded" if self.key else "missing",
                               "sessions": "ok", "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
        elif parsed.path == "/api/route-card":
            size = self._route_card(query)
        elif parsed.path == "/api/admin/card":
            session_id = (query.get("session_id") or [""])[0]
            card = self.store.get_card(session_id) if session_id else None
            size = self._send({"code": 0 if card else CODE_NO_ROUTE,
                               "card": card, "raw_card_id": card["card_id"] if card else None})
        elif parsed.path == "/api/admin/raw":
            limit = int((query.get("n") or ["20"])[0])
            size = self._send({"code": 0, "items": self.store.recent_raw(max(1, min(limit, 100)))})
        elif parsed.path == "/api/admin/cards":
            limit = int((query.get("n") or ["20"])[0])
            size = self._send({"code": 0, "counts": self.store.counts(),
                               "items": self.store.recent_cards(max(1, min(limit, 100)))})
        elif parsed.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            size = 0
        else:
            size = self._send({"code": CODE_NO_ENDPOINT, "message": "接口不存在"}, 404)

        elapsed = (time.time() - started) * 1000
        log(f"[{time.strftime('%H:%M:%S')}] {peer} GET {parsed.path}"
            f"{'?' + parsed.query if parsed.query else ''} -> {size}B {elapsed:.0f}ms")

    def _route_card(self, query):
        session_id = (query.get("session_id") or [""])[0].strip()
        if not session_id:
            return self._send({"code": CODE_BAD_PARAM, "message": "缺少会话编号"})
        if len(session_id) > 128:
            return self._send({"code": CODE_BAD_PARAM, "message": "会话编号太长"})

        # 会话不存在和"还没查过路线"返回同一句话，避免被拿来枚举会话。
        card = self.store.get_card(session_id)
        if not card:
            log(f"[info] 会话 {session_id[:12]}... 没有卡")
            return self._send({"code": CODE_NO_ROUTE, "message": "还没有查到路线"})

        if card["code"] != CODE_OK:
            # 上次查询失败：原样回错误，让屏幕显示原因而不是假装有路线。
            return self._send({"code": card["code"], "message": card["message"] or "没查到路线"})

        return self._send(normalize_card(card))

    def _mcp_authorized(self):
        """配置 connectorKey 后，所有 MCP 请求（包括 discovery）都先过鉴权。"""
        if not self.connector_key:
            return True
        expected = "Bearer " + self.connector_key
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, expected)

    def _raw_headers(self):
        """排障保留字段结构，但绝不把 Bearer、Cookie 等凭据落进 SQLite。"""
        sensitive = {"authorization", "proxy-authorization", "cookie", "x-api-key"}
        return {key: ("[REDACTED]" if key.lower() in sensitive else value)
                for key, value in self.headers.items()}

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        text = raw.decode("utf-8", "replace")

        if parsed.path in ("/mcp", "/mcp/"):
            if not self._mcp_authorized():
                return self._send({"jsonrpc": "2.0", "id": None,
                                   "error": {"code": -32001, "message": "Unauthorized"}}, 401)
            # 留档用于核对平台真实 Call Context；敏感头已脱敏。
            self.store.log_raw(parsed.path, self._raw_headers(), text[:4000])
            try:
                message = json.loads(text) if text else {}
            except ValueError:
                return self._send({"jsonrpc": "2.0", "id": None,
                                   "error": {"code": -32700, "message": "Parse error"}}, 200)
            return self._mcp(message)

        if parsed.path == "/api/admin/session":
            try:
                body = json.loads(text) if text else {}
            except ValueError:
                return self._send({"code": CODE_BAD_PARAM, "message": "请求体不是 JSON"}, 400)
            session_id = str(body.get("session_id") or "").strip()
            if not session_id:
                return self._send({"code": CODE_BAD_PARAM, "message": "缺少 session_id"}, 400)
            self.store.touch_session(session_id, body.get("device_id"), body.get("note"))
            return self._send({"code": 0, "session_id": session_id})

        if parsed.path == "/api/query-transit":
            # REST 兜底：不接 Agent 也能先用 curl 验证腾讯和排版是否正常。
            try:
                body = json.loads(text) if text else {}
            except ValueError:
                return self._send({"code": CODE_BAD_PARAM, "message": "请求体不是 JSON"}, 400)
            return self._query_transit(body, call_id=body.get("call_id") or "")

        return self._send({"code": CODE_NO_ENDPOINT, "message": "接口不存在"}, 404)

    # -- MCP ------------------------------------------------------------
    def _mcp(self, message):
        method = message.get("method") or ""
        msg_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            return self._send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": params.get("protocolVersion") or "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": APP_NAME, "version": APP_VERSION},
            }})

        if method in ("notifications/initialized", "notifications/cancelled"):
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return 0

        if method == "ping":
            return self._send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        if method == "tools/list":
            return self._send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [{
                "name": "query_transit",
                "description": ("查询城市内两地的公交路线。只在起终点都明确后调用；"
                                "地点不完整时先向用户追问，不要猜。结果会同时生成墨水屏路线卡。"),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "城市名，例如 上海"},
                        "origin_text": {"type": "string", "description": "起点中文地名"},
                        "destination_text": {"type": "string", "description": "终点中文地名"},
                        "travel_mode": {"type": "string", "enum": ["transit"],
                                        "description": "固定 transit（公交）"},
                    },
                    "required": ["city", "origin_text", "destination_text"],
                },
            }]}})

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name != "query_transit":
                return self._send({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": f"未知工具 {name}"}], "isError": True}})
            call_id, session_id = self._identities(params, arguments)
            card, spoken, is_error = self._run_query(arguments, call_id, session_id)
            text = spoken if spoken else (card.get("message") or "没查到路线")
            if not session_id:
                text = "服务端没拿到会话编号，路线卡没有生成。" + text
            return self._send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "content": [{"type": "text", "text": text}], "isError": is_error}})

        return self._send({"jsonrpc": "2.0", "id": msg_id,
                           "error": {"code": -32601, "message": f"Method not found: {method}"}})

    def _identities(self, params, arguments):
        """正式环境只信平台签发的 Call Context，不信模型填写的身份参数。"""
        meta = params.get("_meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        context = meta.get("tidb-link/context") or {}
        if not isinstance(context, dict):
            context = {}

        # 无论是否采用，都先移除模型可见的身份字段，避免它们下游再生效。
        argument_session = arguments.pop("session_id", None) or arguments.pop("sessionId", None)
        argument_call = arguments.pop("call_id", None) or arguments.pop("callId", None)
        session_id = context.get("sessionId") or ""
        call_id = context.get("callId") or ""

        if not self.connector_key:
            # 仅保留给未配置鉴权的本地/演示模式；正式部署不得依赖这条回退。
            session_id = (session_id or argument_session
                          or meta.get("sessionId") or meta.get("session_id")
                          or self.headers.get("Mcp-Session-Id") or self.headers.get("X-Session-Id") or "")
            call_id = (call_id or argument_call or meta.get("callId") or meta.get("call_id")
                       or self.headers.get("X-Call-Id") or "")
        return str(call_id or ""), str(session_id or "")

    def _run_query(self, arguments, call_id, session_id=""):
        """执行一次查询并落卡；callId 命中则直接返回上次结果，不重复查腾讯。"""
        if session_id:
            arguments["session_id"] = session_id
        if call_id:
            previous = self.store.get_call(call_id)
            if previous:
                result = json.loads(previous["result"] or "{}")
                log(f"[idem] callId {call_id[:16]} 命中，复用结果")
                return result.get("card", {}), result.get("spoken"), result.get("is_error", False)

        card, spoken, is_error = self._do_query(arguments)
        if call_id:
            self.store.save_call(call_id, session_id or card.get("session_id", ""), "query_transit",
                                 arguments, {"card": card, "spoken": spoken, "is_error": is_error})
        return card, spoken, is_error

    def _do_query(self, arguments):
        city = str(arguments.get("city") or "").strip()
        origin_text = str(arguments.get("origin_text") or "").strip()
        dest_text = str(arguments.get("destination_text") or "").strip()
        travel_mode = str(arguments.get("travel_mode") or "transit").strip()
        session_id = str(arguments.get("session_id") or "").strip()

        log(f"[tool] query_transit city={city!r} origin={origin_text!r} "
            f"dest={dest_text!r} mode={travel_mode!r} session={session_id[:12]!r}")

        if not city or not origin_text or not dest_text:
            card = {"code": CODE_BAD_PARAM, "message": "还缺地点"}
            return card, "需要先确认城市、起点和终点三样，问我一句再查。", True

        if not self.key:
            card = {"code": CODE_UPSTREAM, "message": "服务没配地图"}
            return card, "服务端没有配置地图 Key，请联系管理员。", True

        card, spoken = build_card(self.key, session_id, city, origin_text, dest_text, travel_mode)
        is_error = card.get("code") != CODE_OK

        if session_id:
            if not self.store.has_session(session_id):
                self.store.touch_session(session_id, note="auto from tool call")
            card["session_id"] = session_id
            card_id = self.store.save_card(session_id, card)
            log(f"[card] session={session_id[:12]} card_id={card_id} code={card['code']} "
                f"lines={len(card.get('lines') or [])}")
        else:
            log("[warn] 没有 sessionId，路线卡未落库")

        if is_error:
            return card, f"这次没查到：{card.get('message')}。", True
        return card, spoken, False

    def _query_transit(self, body, call_id):
        session_id = str(body.get("session_id") or "").strip()
        card, spoken, is_error = self._run_query(dict(body), call_id, session_id)
        return self._send({"code": card.get("code", CODE_OK), "card": card,
                           "spoken": spoken, "is_error": is_error})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8010")))
    parser.add_argument("--db", default=os.environ.get("DB_PATH", DEFAULT_DB))
    args = parser.parse_args()

    Handler.key = load_key()
    Handler.connector_key = os.environ.get("CONNECTOR_KEY", "").strip()
    Handler.store = Store(args.db)
    Handler.card_base = os.environ.get("CARD_BASE", "")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    log(f"{APP_NAME} {APP_VERSION} 已启动，监听 {args.host}:{args.port}")
    log(f"  数据库: {os.path.abspath(args.db)}")
    log(f"  地图 Key: {'已加载' if Handler.key else '未加载（TENCENT_KEY 环境变量）'}")
    log(f"  Connector 鉴权: {'已启用' if Handler.connector_key else '未启用（仅限本地/演示，正式部署请配置 CONNECTOR_KEY）'}")
    log("  板端:   GET  /api/route-card?session_id=...")
    log("  Agent:  POST /mcp（JSON-RPC, tool=query_transit）")
    log("  调试:   POST /api/query-transit | GET /api/admin/card | GET /api/admin/raw")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("\n已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
