#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给板子选中的会话直接推一张路线卡，然后回读校验。

用途只有两个：
  1. 演示前刷新卡片时间（页脚会变成当前时刻，不会显示昨天的 21:20）。
  2. 平台还没打通时兜底：换目的地也能立刻出图。

走的是公网 `/mcp`，也就是平台将来要走的那条路，不是后台后门。

用法：
    python tools/push_card.py --session sess_6e85d3713661432c8e59b4005f4a6e93 \
        --from 西岸国际会展中心 --to 复旦大学附属中山医院

    # 只改终点，起点用默认
    python tools/push_card.py --to 华山医院

    # 看现在这张卡是什么（不写）
    python tools/push_card.py --peek
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://keleshiyizhimao.com"
DEFAULT_SESSION = "sess_6e85d3713661432c8e59b4005f4a6e93"
DEFAULT_FROM = "西岸国际会展中心"
DEFAULT_TO = "复旦大学附属中山医院"
DEFAULT_CITY = "上海"


def opener():
    """显式禁用代理：本机 http_proxy 会把 127.0.0.1 和公网请求都接走。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post_mcp(base, payload, timeout=30):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base + "/mcp", data=body,
        headers={"Content-Type": "application/json", "User-Agent": "carecard-push/1.0"})
    with opener().open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_card(base, session_id, timeout=15):
    url = base + "/api/route-card?" + urllib.parse.urlencode({"session_id": session_id})
    request = urllib.request.Request(url, headers={"User-Agent": "carecard-push/1.0"})
    with opener().open(request, timeout=timeout) as response:
        raw = response.read()
    return raw, json.loads(raw.decode("utf-8"))


def show_card(raw, card):
    print()
    print("板子会显示成：")
    print("  标题  " + (card.get("title") or "(无)"))
    for line in card.get("lines") or []:
        print("        " + line)
    print("  页脚  " + (card.get("source") or "(无)"))
    print("  字节  %d（空卡是 44，五行卡约 250）" % len(raw))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--from", dest="origin", default=DEFAULT_FROM)
    parser.add_argument("--to", dest="dest", default=DEFAULT_TO)
    parser.add_argument("--city", default=DEFAULT_CITY)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--peek", action="store_true", help="只看不写")
    args = parser.parse_args()

    if args.peek:
        raw, card = get_card(args.base, args.session)
        if card.get("code") == 0:
            show_card(raw, card)
        else:
            print("这个会话还没有卡：%s" % card.get("message"))
        return 0

    print("会话 %s" % args.session)
    print("路线 %s → %s（%s）" % (args.origin, args.dest, args.city))
    print("查询中，约 3 秒……")

    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "query_transit", "arguments": {
            "city": args.city, "origin_text": args.origin,
            "destination_text": args.dest, "travel_mode": "transit",
            "session_id": args.session}},
    }
    try:
        result = post_mcp(args.base, payload)
    except Exception as exc:                                  # noqa: BLE001
        print("调用失败：%s" % exc)
        print("先确认服务器在跑：curl -sS %s/health" % args.base)
        return 1

    if "error" in result:
        print("平台返回错误：%s" % json.dumps(result["error"], ensure_ascii=False))
        return 1

    content = (result.get("result") or {}).get("content") or []
    spoken = content[0].get("text") if content else ""
    is_error = (result.get("result") or {}).get("isError")
    print()
    print(("查询失败：" if is_error else "查询成功：") + (spoken or "(无文案)"))
    if is_error:
        return 1

    print()
    print("回读校验……")
    raw, card = get_card(args.base, args.session)
    if card.get("code") != 0:
        print("回读失败：%s（卡没落到这个会话，检查服务日志）" % card.get("message"))
        return 1
    show_card(raw, card)
    print()
    print("现在去板子上按：长按中键 → 中键（第 1 项 去医院）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
