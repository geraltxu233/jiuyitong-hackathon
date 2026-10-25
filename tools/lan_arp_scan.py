#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""局域网扫活 + ARP 表查询：UDP 探活触发 ARP，再从 arp -a 里找目标 MAC。
只发一个 UDP 包，不依赖 ICMP，不依赖管理权限。"""

import re
import socket
import subprocess
import sys
import time

PREFIX = sys.argv[1] if len(sys.argv) > 1 else "172.16.0"
TARGET_OUI = (sys.argv[2] if len(sys.argv) > 2 else "44-b1-76").lower()


def sweep():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for i in range(1, 255):
        try:
            s.sendto(b"\x00", (f"{PREFIX}.{i}", 9))
        except OSError:
            pass
    s.close()


def arp_table():
    out = subprocess.run(["arp", "-a"], capture_output=True).stdout
    try:
        text = out.decode("gbk")
    except UnicodeDecodeError:
        text = out.decode("utf-8", "ignore")
    rows = []
    for line in text.splitlines():
        m = re.search(r"^\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{17})\s+(\S+)", line)
        if m:
            rows.append((m.group(1), m.group(2).lower(), m.group(3)))
    return rows


if __name__ == "__main__":
    print(f"扫描 {PREFIX}.0/24 ...", flush=True)
    sweep()
    time.sleep(1.5)
    rows = arp_table()
    print(f"ARP 表共 {len(rows)} 条：")
    for ip, mac, kind in sorted(rows, key=lambda r: tuple(int(x) for x in r[0].split("."))):
        print(f"  {ip:<16} {mac}  {kind}")
    hit = [r for r in rows if r[1].startswith(TARGET_OUI)]
    print()
    if hit:
        for ip, mac, _ in hit:
            print(f"命中板子: {ip}  {mac}")
    else:
        print(f"未找到 MAC 前缀 {TARGET_OUI} 的设备 → 板子不在本网段")
