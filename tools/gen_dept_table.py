#!/usr/bin/env python3
"""把医院科室表编译成板端可直接查的 C 表。

为什么生成而不是手写：科室数据有 5 个字段、要按屏幕宽度拆句，
手写必然数错格子；生成器能顺手做宽度校验，超宽直接报错，不会烧进去才发现。

用法：
    python tools/gen_dept_table.py                       # 默认锁中山医院
    python tools/gen_dept_table.py --hospital 中山
"""
import argparse
import json
import os
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "四家医院_科室映射总表.json")

# 真正的固件工程不在工作区里，而在 Agent_link 仓库；工作区 firmware/ 只放环境脚本与回滚基线。
CANDIDATES = [
    os.environ.get("WS397_APP", ""),
    "D:/Agent_link/firmware/apps/waveshare-epaper-397-wifi",
    os.path.join(ROOT, "firmware", "apps", "waveshare-epaper-397-wifi"),
]
APP = next((p for p in CANDIDATES if p and os.path.isdir(p)), CANDIDATES[1])
HEADER = os.path.join(APP, "dept_table.h")
BODY = os.path.join(APP, "dept_table.cc")

# 与 tools/check_route_card.py 保持一致：大字页 696 px / 汉字 56 px，小字行 724 px / 汉字 28 px
BUDGET_PX = 696
BUDGET_SMALL = 724
CJK_PX, ASCII_PX = 56, 24
CJK_SMALL, ASCII_SMALL = 28, 12
MAX_ROWS = 5  # 与 PublishRoute 的 5 行排版一致


def width(text, small=False):
    cjk = CJK_SMALL if small else CJK_PX
    ascii_px = ASCII_SMALL if small else ASCII_PX
    return sum(cjk if unicodedata.east_asian_width(c) in ("W", "F") else ascii_px for c in text)


def split_hint(hint):
    """把提示按标点拆成独立短句；括号里的补充说明单独成句。

    屏幕只有 5 行，楼栋/楼层/诊区优先，提示只放得下的部分才放。绝不做
    字符级截断——宁可不显示补充说明，也不能把“去哪个楼”截成错的方向。
    """
    text = hint.replace(",", "，").replace("；", "，").replace(";", "，")
    text = text.replace("（", "，").replace("）", "，")
    return [p.strip() for p in text.split("，") if p.strip()]


def compact_room(room):
    """诊区字段里常带括号补充；括号内容放到提示行，不塞进“诊区”主行。"""
    return room.replace("（", "，").split("，")[0].strip()


def c_str(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hospital", default="中山", help="医院名子串；--all 时忽略")
    parser.add_argument("--all", action="store_true", help="生成四家医院全部科室")
    args = parser.parse_args()

    with open(SOURCE, encoding="utf-8") as handle:
        data = json.load(handle)
    if args.all:
        hospitals = data["hospitals"]
    else:
        hospitals = [h for h in data["hospitals"] if args.hospital in h["hospital"]]
        if len(hospitals) != 1:
            print(f"错误：'{args.hospital}' 命中 {len(hospitals)} 家医院，请写得更具体")
            return 1

    def hospital_key(name):
        # 和语音、路线卡标题里的院区简称保持一致；不靠模型输出自由文本。
        for needle, key in (("中山", "中山"), ("第六", "市六"), ("市六", "市六"),
                            ("仁济", "仁济"), ("肿瘤", "肿瘤")):
            if needle in name:
                return key
        raise ValueError(f"没有医院简称映射：{name}")

    errors = []
    entries = []
    for hospital in hospitals:
        key = hospital_key(hospital["hospital"])
        for dep in hospital["departments"]:
            # 楼栋、楼层、诊区必须显示；提示是补充信息，放得下才放。
            rows = [dep["building"], f"楼层 {dep['floor']}", f"诊区 {compact_room(dep['room'])}"]
            for part in split_hint(dep["hint"]):
                if len(rows) >= MAX_ROWS:
                    break
                if width(part) <= BUDGET_PX:
                    rows.append(part)
            for row in rows:
                if width(row) > BUDGET_PX:
                    errors.append(f"{key}/{dep['display_name']}：行超宽 {width(row)}px > {BUDGET_PX}px：{row}")
            if width(dep["hours"], small=True) > BUDGET_SMALL:  # 门诊时间是 scale=1 小字行
                errors.append(f"{key}/{dep['display_name']}：门诊时间超宽（小字行 {BUDGET_SMALL}px）：{dep['hours']}")
            entries.append((key, hospital["hospital"], dep["display_name"], rows, dep["hours"]))

    if errors:
        for error in errors:
            print("错误：" + error)
        print("\n未生成，先修数据。")
        return 1

    with open(HEADER, "w", encoding="utf-8") as out:
        out.write(
            "// 由 tools/gen_dept_table.py 生成，不要手改。\n"
            f"// 来源：四家医院_科室映射总表.json（{len(entries)} 个科室）\n"
            "#pragma once\n\n"
            "struct DeptEntry {\n"
            "    const char* hospital_key;   // 中山 / 市六 / 仁济 / 肿瘤\n"
            "    const char* hospital_name;  // 路线卡标题匹配用全名\n"
            "    const char* name;           // 归一化口语科室名，与语音建议一致\n"
            "    const char* lines[5];       // 大字页 5 行：楼栋 / 楼层 / 诊区 / 提示\n"
            "    const char* hours;          // 门诊时间，走小字行\n"
            "};\n\n"
            "extern const DeptEntry kDepartments[];\n"
            "extern const int kDepartmentCount;\n"
        )

    with open(BODY, "w", encoding="utf-8") as out:
        out.write('// 由 tools/gen_dept_table.py 生成，不要手改。\n#include "dept_table.h"\n\n')
        out.write("const DeptEntry kDepartments[] = {\n")
        for key, hospital_name, name, rows, hours in entries:
            padded = list(rows) + [None] * (MAX_ROWS - len(rows))
            cells = ", ".join(c_str(r) if r else "nullptr" for r in padded)
            widest = max(width(r) for r in rows)
            out.write(f"    {{{c_str(key)}, {c_str(hospital_name)}, {c_str(name)}, {{{cells}}}, "
                      f"{c_str(hours)}}},  // 最宽 {widest}px\n")
        out.write("};\n")
        out.write("const int kDepartmentCount = sizeof(kDepartments) / sizeof(kDepartments[0]);\n")

    print(f"已生成 {len(entries)} 个科室：{HEADER}")
    print(f"{'医院':<4} {'科室':<6} {'行数':>2}  各行宽度")
    for key, _, name, rows, _ in entries:
        print(f"{key:<4} {name:<6} {len(rows):>4}  " + " / ".join(f"{width(r)}px" for r in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
