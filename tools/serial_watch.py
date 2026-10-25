#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""串口旁听：抓墨水屏板子的运行日志，关键行打屏，全文落文件。

为什么要有这个脚本：屏幕上只有一句结论，看不出是"没联网"还是"联网了但没卡"。
串口日志里的 route / https 两行能把这件事定死，是唯一的一手证据。

用法（用 ESP-IDF 的 python，它自带 pyserial）：
    D:/ESP_IDF/espressif/python_env/idf5.5_py3.11_env/Scripts/python.exe \
        tools/serial_watch.py --port COM6 --seconds 900

要点：
  - 打开串口时把 DTR/RTS 都拉低，避免旁听动作本身把板子复位。
  - Windows 的 USB 串口偶尔报 ClearCommError / PermissionError 13。此时不能退出，
    必须关掉失效句柄、重新打开，否则会丢掉复测尾段日志。
  - 每行前面加本机时间戳，方便和服务器的访问日志对时辰。
  - 关键行立刻打屏；其余行只落文件，不刷屏。
"""

import argparse
import sys
import time
from pathlib import Path

import serial

# 这些词一旦出现，说明到了我们关心的那一层
KEYWORDS = (
    "route:",
    "https:",
    "card state",
    "all endpoints failed",
    "request started",
    "route  ",          # 任务名
    "voice",
    "realtime",
    "uplink",
    "session.ready",
    "key:",
    "WSS",
    "esp_err",
    "ASSERT",
    "abort",
)

# 这些是噪音，落文件也不要（WiFi 扫描、电量轮询特别吵）
NOISE = ("battery", "adc", "charg", "wifi: scan", "esp_wifi", "phy")

MAX_LINE_BUFFER = 64 * 1024


def open_port(name):
    # 必须先以 DTR/RTS=LOW 的状态打开，再按 RTS→DTR 的顺序拉高。
    # 直接 Serial(name) 打开后再把 DTR/RTS 拉低，会在 USB Serial/JTAG 板上
    # 产生一次复位脉冲，日志里表现为 USB_UART_CHIP_RESET；旁听动作本身
    # 不应该重启被测设备。这个顺序与 ESP-IDF monitor 的 no-reset 打开方式一致。
    port = serial.Serial()
    port.port = name
    port.baudrate = 115200
    port.timeout = 0.05
    port.setRTS(False)
    port.setDTR(False)
    port.open()
    port.setRTS(True)
    port.setDTR(True)
    return port


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM6")
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--log", default="")
    parser.add_argument("--all", action="store_true", help="打印所有行，不只关键行")
    args = parser.parse_args()

    default_log = Path(__file__).resolve().parent.parent / "_refs" / "serial_watch.log"
    log_path = Path(args.log) if args.log else default_log
    log_path.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + args.seconds
    hits = 0
    reconnects = 0
    port = None
    buffered = b""

    print(f"串口旁听目标 {args.port}，抓 {args.seconds} 秒，日志写到 {log_path}", flush=True)
    print("现在可以开始操作板子；语音与路线事件会在这里实时显示。", flush=True)
    print("-" * 60, flush=True)

    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        while time.monotonic() < deadline:
            if port is None:
                try:
                    port = open_port(args.port)
                    if reconnects:
                        reconnects += 1
                        stamp = time.strftime("%H:%M:%S")
                        print(f"[{stamp}] 串口已重连 {args.port}", flush=True)
                        log.write(f"[{stamp}] --- serial reconnected {args.port} ---\n")
                        log.flush()
                except Exception as exc:                      # noqa: BLE001
                    print(f"打不开串口 {args.port}: {exc}；1 秒后重试", flush=True)
                    print("确认板子插着、端口号正确、没有被 idf.py monitor 占着。", flush=True)
                    time.sleep(1)
                    continue

            try:
                waiting = port.in_waiting
                raw = port.read(waiting if waiting > 0 else 1)
            except Exception as exc:                          # noqa: BLE001
                reconnects += 1
                stamp = time.strftime("%H:%M:%S")
                print(f"[{stamp}] 串口中断: {exc}；正在重连", flush=True)
                log.write(f"[{stamp}] --- serial interrupted: {exc}; reconnecting ---\n")
                log.flush()
                try:
                    port.close()
                except Exception:                             # noqa: BLE001
                    pass
                port = None
                buffered = b""
                time.sleep(1)
                continue

            if not raw:
                continue

            buffered += raw
            if len(buffered) > MAX_LINE_BUFFER:
                # 没有换行的异常输出不能无限涨内存，保留尾部即可。
                buffered = buffered[-4096:]

            while b"\n" in buffered:
                line_bytes, buffered = buffered.split(b"\n", 1)
                line = line_bytes.decode("utf-8", "replace").rstrip("\r")
                if not line.strip():
                    continue
                stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
                record = f"[{stamp}] {line}"
                lowered = line.lower()
                if not any(word in lowered for word in NOISE):
                    log.write(record + "\n")
                    log.flush()
                if args.all or any(word in line for word in KEYWORDS):
                    print(record, flush=True)
                    hits += 1

    if port is not None:
        try:
            port.close()
        except Exception:                                     # noqa: BLE001
            pass
    print("-" * 60, flush=True)
    print(f"抓取结束，关键行 {hits} 条，重连 {reconnects} 次，全文见 {log_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
