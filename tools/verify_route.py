#!/usr/bin/env python3
"""H9 逐字核对：上屏的每一行，是否都能在腾讯原始数据里找到出处。

为什么需要这个：后端把腾讯的 steps 拍平成 9 行短句，展平逻辑一旦错位
（比如把"下车站"写成"上车站"、站数差一站），屏上看起来完全正常，
但老人会坐错站。这类错误只有把两边并排比才看得出来。

用法：
    python tools/verify_route.py            # 查一次并核对
    python tools/verify_route.py --dump     # 额外打印原始 steps 全字段
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import route_server as rs  # noqa: E402


def short(value):
    """腾讯的 geton/getoff 是 dict，取其中的 title。"""
    if isinstance(value, dict):
        return str(value.get("title") or "")
    return str(value or "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", action="store_true", help="打印原始 steps 全字段")
    args = parser.parse_args()

    key = rs.load_key()
    if not key:
        print("错误：没有腾讯 Key（backend/tencent_key.txt 或环境变量 TENCENT_KEY）")
        return 1

    data = rs.tencent_get("/ws/direction/v1/transit/",
                          {"from": rs.ORIGIN, "to": rs.DEST, "policy": rs.POLICY}, key)
    if data.get("status") != 0:
        print(f"腾讯返回 status={data.get('status')} {data.get('message')}")
        return 1
    routes = (data.get("result") or {}).get("routes") or []
    if not routes:
        print("腾讯没有返回方案")
        return 1

    print(f"起点 {rs.ORIGIN_NAME}  {rs.ORIGIN}")
    print(f"终点 {rs.DEST_NAME}  {rs.DEST}")
    print(f"候选 {len(routes)} 条：")
    for i, route in enumerate(routes):
        walk, transfers, minutes = rs._route_stats(route)
        night = "  含夜宵线(已排除)" if rs._has_night_line(route) else ""
        print(f"  [{i}] 步行{walk}m 换乘{transfers}次 {minutes}分{night}")

    chosen = rs.pick_route(routes)
    walk, transfers, minutes = rs._route_stats(chosen)
    print(f"\n选中：步行{walk}m 换乘{transfers}次 {minutes}分")

    # ── 从原始 steps 里抽出"必须能在屏上看到"的事实 ──────────────────────────
    facts = []   # (描述, 期望出现在某行的片段)
    steps = chosen.get("steps") or []
    for index, step in enumerate(steps):
        if step.get("mode") == "WALKING":
            meters = int(step.get("distance") or 0)
            if index == 0:
                facts.append((f"首段步行 {meters}m", f"步行{rs._round10(meters)}米"))
            elif index == len(steps) - 1:
                facts.append((f"末段步行 {meters}m", f"步行{rs._round10(meters)}米"))
            elif meters > 50:
                facts.append((f"站间步行 {meters}m", f"{rs._round10(meters)}米"))
            continue
        for line in (step.get("lines") or [])[:1]:
            title = rs._short(line.get("title"))
            geton = rs._short(short(line.get("geton")))
            getoff = rs._short(short(line.get("getoff")))
            size = int(line.get("station_count") or 0)
            facts.append((f"线路 {title}", title))
            if geton:
                facts.append((f"上车点 {geton}", geton))
            if getoff:
                facts.append((f"下车站 {getoff}", getoff))
                facts.append((f"站数 {size}", f"{size}站"))

    lines = rs.build_lines(chosen)
    print(f"\n上屏 {len(lines)} 行（每页 5 行 → {-(-len(lines) // 5)} 页）：")
    for i, line in enumerate(lines, 1):
        print(f"  {i}. [{rs._line_width(line)}px] {line}")

    print("\n逐项核对（上屏文本里必须找得到）：")
    missing = 0
    for description, needle in facts:
        hit = any(needle in line for line in lines)
        print(f"  {'✓' if hit else '✗'} {description}")
        if not hit:
            missing += 1

    # 反向检查：方向行来自 destination，逐段确认各自都有对应的一句
    print("\n方向行（腾讯 destination → 屏上文案）：")
    for step in steps:
        if step.get("mode") != "TRANSIT":
            continue
        for line in (step.get("lines") or [])[:1]:
            dest = rs._text(line.get("destination"))
            if not dest:
                continue
            target = f"开往{dest}方向"
            mark = "✓" if target in lines else "✗ 没有对应行"
            print(f"  {mark} {dest!r} → {target!r}")

    if args.dump:
        print("\n原始 steps：")
        for index, step in enumerate(steps):
            print(f"  [{index}] {step.get('mode')} {step.get('distance')}m")
            for line in (step.get("lines") or [])[:1]:
                print(f"        title={line.get('title')!r} {line.get('station_count')}站 "
                      f"geton={short(line.get('geton'))!r} getoff={short(line.get('getoff'))!r} "
                      f"dest={rs._text(line.get('destination'))!r}")

    print()
    if missing:
        print(f"结论：不通过，{missing} 项事实在屏上找不到出处")
        return 1
    print("结论：通过（每一条原始事实都能在屏上找到出处；措辞是否通顺仍需人读一遍）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
