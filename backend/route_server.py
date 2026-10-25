#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""老人导诊卡 —— 导航后端（S5：接真实公交数据）

契约来源：firmware/apps/waveshare-epaper-397-wifi/route_card.h
  GET /api/transit?from=demo&to=hospital
  {"code":0,"title":"...","lines":["每行<=12汉字", ...],"source":"来源 + 查询时间"}
  code 非 0 时用 message 给一句可直接显示的中文原因。

三条硬约束：
  1. 板端一次 GET 最多等 8 秒 → 腾讯侧 3 秒硬超时，超时立刻给缓存
  2. 每行按像素宽度控制在 696px 内（56px 汉字 ≈ 12 字），**整卡压到 5 行一屏出完**；
     压缩优先丢"开往方向"、换乘步行提示、上车点，换乘站名与首末步行必须保留
  3. 数据来源必须如实：真实/缓存/演示三种写法不许混
"""

import argparse
import json
import os
import re
import socket
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# 板端大字页的可用宽度：800 - 20(左边距) - 20(右边距) - 64(省略号留位)。
# 汉字 56 px，数字与半角英文按 24 px 保守估算。与 tools/check_route_card.py 同一套口径。
LINE_BUDGET_PX = 696
CJK_PX, ASCII_PX = 56, 24
MAX_LINES = 12
CARD_LINES = 5   # 一屏能出完的行数，超过就要按键翻页
TENCENT_TIMEOUT = 3.0

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "cache_route.json")

# 预设起点 → 一家医院。首版不做定位、不做多起点（决策文档已锁定）。
# 起点取"行人主入口"而不是会展中心几何中心：老人是从门口开始走的。
ORIGIN_NAME = "西岸国际会展中心"
ORIGIN = "31.162751,121.462919"
DEST_NAME = "复旦大学附属中山医院"
DEST_POI = "6577562127214739761"
DEST = "31.197019,121.456540"  # 东院区，与科室表"心内科→16号楼（东院区）"一致

# 适老排序：少走路 > 少换乘 > 总耗时。LEAST_TRANSFER 会给出步行 1.7 km 的方案，
# LEAST_WALK 这个取值腾讯直接报 348 参数错误，所以用 RECOMMEND 再自己挑。
POLICY = "RECOMMEND"

SAMPLE_TITLE = "去" + DEST_NAME
SAMPLE_LINES = [
    "出小区东门向东走200米",
    "到中山路站乘58路",
    "开往火车站方向",
    "坐6站到市第一医院站",
    "下车向西走200米到正门",
]

INDEX_HTML = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>导航后端 · 自检</title>
<style>body{{font:16px/1.6 -apple-system,sans-serif;margin:0;padding:20px;background:#111;color:#eee}}
h1{{font-size:20px;margin:0 0 12px}}a{{color:#6cf}}pre{{background:#1c1c1c;padding:12px;border-radius:8px;overflow:auto;font-size:13px}}
.ok{{color:#3c6}}.k{{color:#888}}</style>
<h1>导航后端已连通</h1>
<p class="ok">{status}</p>
<p>真实路线：<a href="/api/transit?from=demo&amp;to=hospital">/api/transit?from=demo&amp;to=hospital</a></p>
<p>离线样例：<a href="/api/transit?from=demo&amp;to=demo">/api/transit?from=demo&amp;to=demo</a></p>
<pre>{body}</pre>
<p class="k">页面本身不参与板端链路，仅供人工自检。Key：{key_state}</p>
"""


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


def _line_width(text):
    """按板端字体估算这一行在屏上占多少像素。"""
    return sum(CJK_PX if unicodedata.east_asian_width(c) in ("W", "F") else ASCII_PX for c in text)


def _clean(text):
    """只做清洗，不截断。

    截断交给固件：它在排不下时会画省略号，老人能看出"这行没写完"。
    服务端硬切（原 text[:12]）会把句子砍成读得通但内容残缺的样子，
    而且因为长度刚好够，固件的省略号永远不会触发——问题看不出来。
    超宽只打一条 warning，提醒把这行写短。
    """
    text = text.replace("\r", " ").replace("\n", " ").strip()
    width = _line_width(text)
    if width > LINE_BUDGET_PX:
        print(f"[warn] 行超宽 {width}px > {LINE_BUDGET_PX}px，上屏会被截断: {text}", flush=True)
    return text


def _now(fmt="%Y-%m-%d %H:%M"):
    return time.strftime(fmt, time.localtime())


def load_key():
    key = os.environ.get("TENCENT_KEY", "").strip()
    if key:
        return key
    path = os.path.join(HERE, "tencent_key.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    return ""


def tencent_get(path, params, key):
    params = dict(params)
    params["key"] = key
    url = "https://apis.map.qq.com" + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "carecard-backend/1.0"})
    with urllib.request.urlopen(req, timeout=TENCENT_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def walk_line(distance, index, total):
    meters = max(10, int(round(distance / 10.0)) * 10)
    if index == 0:
        return _clean(f"步行{meters}米到车站")
    if index == total - 1:
        return _clean(f"下车步行{meters}米到院")
    return _clean(f"站内步行{meters}米换乘")


# 一屏出不完就要按键翻页，老人不该为看一眼路线按键。所以先按"信息优先"排，
# 超出一屏时按下面的顺序丢：先丢换乘步行提示，再丢上车点，最后丢末段下车站名。
# 换乘站名永远不丢——那是全程唯一的迷路点。
DROP_ORDER = ("walk_mid", "board", "off_last")


def build_lines(route):
    """把腾讯的 steps 拍平成 ≤5 行，顺序即"现在该做什么"。"""
    steps = route.get("steps") or []
    total = len(steps)
    items = []          # [(kind, text)]，kind 决定超行时先丢谁
    skip_board = False
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
        title = _short(line.get("title"))
        geton = _short(line.get("geton"))
        getoff = _short(line.get("getoff"))
        count = int(line.get("station_count") or 0)
        nxt = steps[index + 1] if index + 1 < total else None
        same_next = (nxt is not None and nxt.get("mode") == "WALKING"
                     and index + 2 < total and steps[index + 2].get("mode") == "TRANSIT"
                     and int(nxt.get("distance") or 0) <= 50)
        # 后面只要还有一段公交，这一站的"下车"就是换乘点，标成 off_transfer 永不压缩——
        # 丢了它老人会在中间某站不知道该下车还是继续坐。
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
    if len(texts) > CARD_LINES:   # 换乘段特别多时兜底：保头保尾
        texts = texts[:CARD_LINES - 1] + [texts[-1]]
    if before > len(texts):
        print(f"[compact] {before} 行压到 {len(texts)} 行，一屏出完", flush=True)
    return texts[:MAX_LINES]


def _round10(distance):
    return max(10, int(round(distance / 10.0)) * 10)


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


WALK_BUCKET_M = 200  # 步行按 200 米分档：差不到一档就别为它多花 20 分钟


def pick_route(routes):
    """适老排序：步行分档 > 少换乘 > 总耗时。

    步行不按米数硬排——实测会出现"为省 57 米多花 20 分钟"的方案，
    对老人没意义。200 米分档后同档内比换乘与耗时。
    """
    usable = [route for route in routes if not _has_night_line(route)]

    def rank(route):
        walk, transfers, duration = _route_stats(route)
        return (walk // WALK_BUCKET_M, transfers, duration)

    return sorted(usable or routes, key=rank)[0]


def _fetch_routes(key, policy):
    data = tencent_get("/ws/direction/v1/transit/", {
        "from": ORIGIN,
        "to": DEST,
        "policy": policy,
    }, key)
    if data.get("status") != 0:
        raise RuntimeError(f"腾讯 status={data.get('status')} {data.get('message')}")
    return (data.get("result") or {}).get("routes") or []


def _route_signature(route):
    titles = []
    for step in route.get("steps") or []:
        if step.get("mode") == "TRANSIT":
            lines = step.get("lines") or []
            if lines:
                titles.append(_short(lines[0].get("title")))
    walk, transfers, duration = _route_stats(route)
    return (duration, walk, transfers, tuple(titles))


def collect_routes(key):
    """两个策略都查一遍再合并。

    单用 RECOMMEND 会漏掉 LEAST_TRANSFER 里的好方案（实测人民广场那条
    从 45 分掉到 65 分），单用 LEAST_TRANSFER 又会给出步行 1.7 公里的方案。
    合并后统一按适老口径排序，代价是多一次 HTTP（合计仍 <1s）。
    """
    merged, errors = [], []
    for policy in (POLICY, "LEAST_TRANSFER"):
        try:
            merged.extend(_fetch_routes(key, policy))
        except Exception as exc:  # noqa: BLE001
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
    if errors:
        print(f"[warn] 部分策略失败，已用其余结果: {'; '.join(errors)}", flush=True)
    return unique


def query_route(key):
    """返回 (lines, duration, transfers)；失败抛异常。"""
    route = pick_route(collect_routes(key))
    lines = build_lines(route)
    if not lines:
        raise RuntimeError("路线解析为空")
    walk, transfers, duration = _route_stats(route)
    print(f"[{_now('%H:%M:%S')}] 选中方案: 步行{walk}米 换乘{transfers}次 {duration}分", flush=True)
    return lines, duration, transfers


def save_cache(lines, duration, transfers):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as handle:
            json.dump({"lines": lines, "duration": duration, "transfers": transfers,
                       "at": _now()}, handle, ensure_ascii=False)
    except OSError as exc:
        print(f"[cache] 写入失败: {exc}", flush=True)


def load_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("lines") and data.get("at"):
            return data
    except (OSError, ValueError):
        pass
    return None


def source_text(duration, transfers, when):
    bits = [f"{duration}分"]
    if transfers:
        bits.append(f"换乘{transfers}次")
    return f"腾讯地图 {' '.join(bits)} {when}"


class Handler(BaseHTTPRequestHandler):
    server_version = "CareCardRoute/0.2"
    protocol_version = "HTTP/1.1"
    key = ""

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        return len(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        return len(body)

    def parse_request(self):
        ok = super().parse_request()
        if not ok:
            raw = getattr(self, "raw_requestline", b"") or b""
            hint = "空连接" if not raw else ("疑似 TLS/代理" if raw[:1] == b"\x16" else raw[:24].hex(" "))
            print(f"[{_now('%H:%M:%S')}] {self.client_address[0]} 非 HTTP 请求: {hint}", flush=True)
        return ok

    def do_GET(self):
        started = time.time()
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        peer = self.client_address[0]
        size = 0

        if parsed.path == "/health":
            size = self._send_json({"code": 0, "msg": "ok", "time": _now(),
                                    "key": "loaded" if self.key else "missing"})
        elif parsed.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
        elif parsed.path == "/":
            sample = json.dumps({"code": 0, "title": SAMPLE_TITLE, "lines": SAMPLE_LINES,
                                 "source": "页面自检"}, ensure_ascii=False, indent=2)
            size = self._send_html(INDEX_HTML.format(
                status="Key 已加载，可查真实公交" if self.key else "Key 未加载，只能给样例",
                body=sample.replace("&", "&amp;").replace("<", "&lt;"),
                key_state="已加载" if self.key else "缺失"))
        elif parsed.path == "/api/transit":
            size = self._route(parse_qs(parsed.query))
        else:
            size = self._send_json({"code": 4, "message": "接口不存在"}, 404)

        ms = (time.time() - started) * 1000
        qs = "&".join(f"{k}={v[0]}" for k, v in query.items())
        print(f"[{_now('%H:%M:%S')}] {peer} GET {parsed.path}"
              f"{'?' + qs if qs else ''} -> {size}B {ms:.0f}ms", flush=True)

    def _route(self, query):
        origin = (query.get("from") or [""])[0]
        target = (query.get("to") or [""])[0]
        if not origin or not target:
            return self._send_json({"code": 2, "message": "缺少起点或终点"})
        if target == "demo":
            return self._send_json({"code": 0, "title": SAMPLE_TITLE,
                                    "lines": SAMPLE_LINES, "source": "演示数据 " + _now("%H:%M")})
        if target != "hospital":
            return self._send_json({"code": 5, "message": "没有查到去这里的车"})
        if not self.key:
            return self._send_json({"code": 3, "message": "后端没有配置 Key"})

        try:
            lines, duration, transfers = query_route(self.key)
        except Exception as exc:  # noqa: BLE001
            print(f"[{_now('%H:%M:%S')}] 腾讯查询失败: {exc}", flush=True)
            cache = load_cache()
            if cache:
                return self._send_json({"code": 0, "title": "去" + DEST_NAME,
                                        "lines": cache["lines"],
                                        "source": "缓存路线 " + cache["at"][5:]})
            return self._send_json({"code": 3, "message": "网络不好，请再试"})

        save_cache(lines, duration, transfers)
        print(f"[{_now('%H:%M:%S')}] 路线 {len(lines)} 行 {duration}分 换乘{transfers}次", flush=True)
        return self._send_json({"code": 0, "title": "去" + DEST_NAME, "lines": lines,
                                "source": source_text(duration, transfers, _now("%H:%M"))})


def _local_ips():
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    Handler.key = load_key()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"导航后端已启动，监听 {args.host}:{args.port}", flush=True)
    print(f"  Key: {'已加载' if Handler.key else '未加载（放 backend/tencent_key.txt 或设 TENCENT_KEY）'}", flush=True)
    print(f"  起点: {ORIGIN_NAME} {ORIGIN}   终点: {DEST_NAME} {DEST} (POI {DEST_POI})", flush=True)
    for ip in _local_ips():
        print(f"  板子可用地址: http://{ip}:{args.port}/api/transit?from=demo&to=hospital", flush=True)
    print(f"  自检页:       http://127.0.0.1:{args.port}/", flush=True)
    print("按 Ctrl+C 停止", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
