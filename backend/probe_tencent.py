#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯位置服务探针：验证 Key 权限，并打印公交规划的真实字段结构。

不改任何业务代码，只回答三个问题：
  1. 这个 Key 有没有 WebService API 权限（地点搜索 + 公交路线规划）
  2. 医院的 POI ID 与正门坐标是什么
  3. routes[0].steps[] 的真实字段长什么样（区别于文档里的伪代码假设）
"""

import json
import os
import sys
import urllib.parse
import urllib.request

# Key 从 backend/tencent_key.txt 或环境变量读，不写进源码
def load_key():
    key = os.environ.get("TENCENT_KEY", "").strip()
    if key:
        return key
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tencent_key.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    return ""

KEY = load_key()
TIMEOUT = 8


def get(path, params):
    params = dict(params)
    params["key"] = KEY
    url = "https://apis.map.qq.com" + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "carecard-probe/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8")), url


def show(title, obj, depth=0, max_depth=3):
    pad = "  " * depth
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                print(f"{pad}{k}: {type(v).__name__}")
                if depth < max_depth:
                    show(title, v, depth + 1, max_depth)
            else:
                text = str(v)
                print(f"{pad}{k} = {text[:60]}")
    elif isinstance(obj, list):
        if not obj:
            print(f"{pad}(空数组)")
            return
        print(f"{pad}[0] of {len(obj)}:")
        show(title, obj[0], depth + 1, max_depth)


def main():
    print("=" * 70)
    print("1) 地点搜索：复旦大学附属中山医院")
    print("=" * 70)
    try:
        data, url = get("/ws/place/v1/search", {
            "keyword": "复旦大学附属中山医院",
            "boundary": "region(上海,0)",
            "page_size": 3,
        })
    except Exception as exc:  # noqa: BLE001
        print("请求失败:", exc)
        return 1
    print("status =", data.get("status"), "| message =", data.get("message"))
    if data.get("status") != 0:
        print("\nKey 可能缺少「WebServiceAPI」权限或未分配额度。")
        print("原始响应:", json.dumps(data, ensure_ascii=False)[:400])
        return 1

    poi = None
    for item in data.get("data", []):
        print(f"  - {item.get('title')} | id={item.get('id')} | "
              f"{item.get('location', {}).get('lat')},{item.get('location', {}).get('lng')}")
        print(f"    地址: {item.get('address')} | 类型: {item.get('category')}")
        if poi is None:
            poi = item
    if not poi:
        print("没有搜到结果")
        return 1

    lat = poi["location"]["lat"]
    lng = poi["location"]["lng"]
    print(f"\n选用 POI: {poi.get('title')}  {lat},{lng}")

    print()
    print("=" * 70)
    print("2) 公交路线规划：从人民广场到该医院")
    print("=" * 70)
    try:
        data, url = get("/ws/direction/v1/transit/", {
            "from": "31.230378,121.473701",
            "to": f"{lat},{lng}",
            "policy": "LEAST_TRANSFER",
        })
    except Exception as exc:  # noqa: BLE001
        print("请求失败:", exc)
        return 1
    print("status =", data.get("status"), "| message =", data.get("message"))
    if data.get("status") != 0:
        print("原始响应:", json.dumps(data, ensure_ascii=False)[:400])
        return 1

    routes = data.get("result", {}).get("routes", [])
    print(f"方案数 = {len(routes)}")
    if not routes:
        print("没有可用方案")
        return 1

    route = routes[0]
    print("\n-- route[0] 顶层字段 --")
    for k, v in route.items():
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")
        elif isinstance(v, dict):
            print(f"  {k}: dict -> {list(v)[:8]}")
        else:
            print(f"  {k} = {v}")

    steps = route.get("steps", [])
    print(f"\n-- steps 共 {len(steps)} 段 --")
    for i, step in enumerate(steps):
        keys = list(step.keys())
        print(f"  [{i}] 字段: {keys}")
        for k in ("type", "mode", "distance", "duration", "instruction"):
            if k in step:
                print(f"       {k} = {step[k]}")
        lines = step.get("lines")
        if lines:
            line = lines[0]
            print(f"       lines[0] 字段: {list(line.keys())}")
            for k in ("title", "station_count", "vehicle", "distance", "duration"):
                if k in line:
                    print(f"         {k} = {line[k]}")
            for k in ("geton", "getoff"):
                if k in line:
                    print(f"         {k} = {line[k]}")

    print()
    print("=" * 70)
    print("3) 原始响应体积")
    print("=" * 70)
    body = json.dumps(data, ensure_ascii=False)
    print(f"公交响应 JSON 长度 = {len(body)} 字符（约 {len(body.encode('utf-8'))} 字节）")
    print("完整 routes[0] 前 200 字:", body[:200].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
