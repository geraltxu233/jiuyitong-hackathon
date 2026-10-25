#!/usr/bin/env python3
"""路线卡 JSON 上板前自检。

板端只做两件事：把后端给的文字原样排上去，排不下就截断加省略号。
截断会丢语义（屏上只剩 `雨雪天班次可…`），所以行长必须在后端就控住。

用法：
    python tools/check_route_card.py route.json
    python tools/check_route_card.py            # 读内置示例，验证脚本本身

判定依据：
    title / lines 走大字页（scale=2）：汉字 56 px，数字与半角英文按 24 px 保守估算
    source 走小字行（scale=1）：汉字 28 px，数字与半角英文按 12 px
    两种版式的可用宽度都按「800 - 20(左边距) - 20(右边距) - 省略号留位」计算
"""
import json
import sys
import unicodedata

BUDGET_BIG = 696    # scale=2：800 - 40 - 64
BUDGET_SMALL = 724  # scale=1：800 - 40 - 36
CJK_BIG, ASCII_BIG = 56, 24
CJK_SMALL, ASCII_SMALL = 28, 12
MAX_LINES = 12
ROWS_PER_PAGE = 5

SAMPLE = {
    "code": 0,
    "title": "去市第一人民医院（东院）",
    "lines": [
        "出小区东门向东走200米",
        "乘58路，开往人民广场方向",
        "坐6站，中心广场站下车",
        "换乘地铁2号线（B口进站）",
        "坐3站，市一医院站下车",
        "从2号口出，向西步行150米",
        "末班车23:00，雨雪天停运",
    ],
    "source": "样例数据 · 非真实公交",
}


def width(text, big=True):
    """按板端字体估算渲染宽度（保守）。big=True 对应大字页，False 对应小字行。"""
    cjk = CJK_BIG if big else CJK_SMALL
    ascii_px = ASCII_BIG if big else ASCII_SMALL
    total = 0
    for ch in text:
        wide = unicodedata.east_asian_width(ch) in ("W", "F")
        total += cjk if wide else ascii_px
    return total


def check(card):
    errors, warnings = [], []

    if not isinstance(card.get("code"), int):
        errors.append("code 必须是整数")
    elif card["code"] != 0:
        if not card.get("message"):
            warnings.append("code 非 0 但没有 message，屏上只能显示通用失败文案")
        return errors, warnings, []

    title = card.get("title", "")
    if not title:
        errors.append("title 为空，屏上标题区会空着")
    elif width(title) > BUDGET_BIG:
        errors.append(f"title 超宽 {width(title)}px > {BUDGET_BIG}px：{title}")

    lines = card.get("lines") or []
    if not lines:
        errors.append("lines 为空，屏上没有正文")
    if len(lines) > MAX_LINES:
        errors.append(f"lines 有 {len(lines)} 行，超过上限 {MAX_LINES}")

    rows = []
    for i, line in enumerate(lines, 1):
        px = width(line)
        ok = px <= BUDGET_BIG
        rows.append((i, px, ok, line))
        if not ok:
            errors.append(f"第 {i} 行超宽 {px}px > {BUDGET_BIG}px（上屏会被截断）：{line}")

    source = card.get("source", "")
    if not source:
        errors.append("source 为空：屏上看不出数据来源与查询时间，无法判断这条路线能不能信")
    elif width(source, big=False) > BUDGET_SMALL:
        errors.append(f"source 超宽 {width(source, big=False)}px > {BUDGET_SMALL}px（小字行会截断）：{source}")

    pages = max(1, (len(lines) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    if pages > 1:
        warnings.append(f"{len(lines)} 行会分成 {pages} 页，老人需要按翻页键；建议压到 {ROWS_PER_PAGE} 行以内")

    return errors, warnings, rows


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as handle:
            card = json.load(handle)
        name = sys.argv[1]
    else:
        card = SAMPLE
        name = "内置示例"

    errors, warnings, rows = check(card)

    print(f"文件：{name}")
    print(f"预算：正文 {BUDGET_BIG}px/行（汉字 {CJK_BIG}px），source {BUDGET_SMALL}px（汉字 {CJK_SMALL}px）\n")
    if rows:
        print(f"{'行':>2}  {'宽度':>5}  {'占预算':>6}  内容")
        for i, px, ok, line in rows:
            print(f"{i:>2}  {px:>4}px  {px * 100 // BUDGET_BIG:>5}%  {line}")
        print()

    for warning in warnings:
        print(f"警告：{warning}")
    for error in errors:
        print(f"错误：{error}")

    if errors:
        print(f"\n结论：不通过（{len(errors)} 处错误）")
        return 1
    print("\n结论：通过" + ("（有警告）" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
