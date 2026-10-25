# ESP32-S3-ePaper-3.97 Arduino 环境配置清单

> 适用板卡：微雪 ESP32-S3-ePaper-3.97（ESP32-S3-WROOM-1-**N16R8**：16MB Flash + 8MB OPI PSRAM）
> 目标：Type-C 直连电脑 → Arduino IDE 能识别端口 → 编译上传 → 串口有输出、墨水屏能刷图
> 依据：微雪官方文档 docs.waveshare.net/ESP32-S3-ePaper-3.97/Arduino 及官方仓库 waveshareteam/ESP32-S3-ePaper-3.97

---

## 一、三件事，按顺序做

| 步骤 | 动作 | 说明 |
|------|------|------|
| 1 | 装 Arduino IDE 2.x | 官网 arduino.cc 下 Windows 安装包即可 |
| 2 | 装 ESP32 开发板核心 **3.2.0** | 首选项 → 附加开发板管理器地址填下面那条 URL → 开发板管理器搜 esp32 → 选 3.2.0 安装 |
| 3 | 选板 + 设 Tools 参数 | 见第二节表格，**一项都不能错** |

附加开发板管理器地址（官方源）：

```
https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

**版本必须 3.x（官方文档标注 3.2.0）**：官方音频例程用的是 `ESP_I2S.h`（Arduino-ESP32 3.x 才有的新 I2S API），装成 2.0.x 会直接编译不过。若 3.2.0 拉不下来（国内网络），去微雪文档"资料"页下 `Arduino-ESP32 离线组件包`，解压到
`C:\Users\<你>\AppData\Local\Arduino15\packages\` 下再重开 IDE。

---

## 二、Tools 菜单逐项配置（官方截图口径）

| 菜单项 | 必须设为 | 为什么 |
|--------|----------|--------|
| 开发板 Board | **ESP32S3 Dev Module** | 官方没有专板 entry，通用 S3 模块即正确选择 |
| 端口 Port | 你的 **COMx** | 原生 USB，插上就出，无需装驱动 |
| **USB CDC On Boot** | **Enabled** | 不开这项 `Serial.print` 走 UART0 而不是 USB，表现为"串口一片空白" |
| CPU Frequency | 240MHz (WiFi) | 麦克风录音 + 图片刷新都要算力 |
| Flash Mode | **QIO 80MHz** | N16R8 的 Flash 是 Quad，不是 OPI |
| **Flash Size** | **16MB (128Mb)** | 填小了分区表选不出 16M 方案 |
| **PSRAM** | **OPI PSRAM** | R8 的 8MB PSRAM 是 Octal 接线；设 Disabled 则 `ESP.getPsramSize()` 返回 0 |
| **Partition Scheme** | **16M Flash (3MB APP/9.9MB FATFS)** | 3MB APP 够塞音频/字库；9.9MB FATFS 可当 U 盘存 bmp。**若要 OTA 必须换带 OTA 的分区方案** |
| Upload Speed | 921600 | 掉线就降到 460800 |
| Upload Mode | UART0 / Hardware CDC | 与 USB CDC On Boot 配套 |
| USB Mode | Hardware CDC and JTAG | 保 JTAG 调试口 |
| USB DFU On Boot / USB Firmware MSC On Boot / Erase All Flash | Disabled | 默认即可，别乱开 |
| Core Debug Level | None | 出问题再改 Verbose |
| Arduino Runs On / Events Run On | Core 1 / Core 1 | 默认，别动 |

其余（JTAG Adapter、Zigbee Mode 等）保持默认。

---

## 三、上传与首次验证

1. Type-C 插电脑 → 设备管理器"端口(COM 和 LPT)"下应出现 **USB 串行设备(COMx)**（Win10/11 自带 usbser 驱动，不用装 CP210x/CH340）。
2. 打开 `firmware/00_board_check/00_board_check.ino`，按上表选好板子和端口，点上传。
3. 打开串口监视器，波特率 **115200**，看是否全部 PASS（Flash=16MB、PSRAM≈8MB、I2C 扫到 4 个器件）。
4. 再跑官方 `02_E-Paper_Example`（仓库 `Arduino/examples/`），屏幕能刷图即环境全通。

**上传失败 / 端口不出现**：长按 **BOOT** 键不松 → 重新插拔 USB（或按侧边 PWR 重新上电）→ 进入下载模式再上传。这是微雪 FAQ 给的标准解法。

**注意**：原生 USB 板子上传后端口会重新枚举（COM 号可能变），串口监视器断开重连属正常现象。

---

## 四、引脚表（来自官方例程源码，别照抄别家板子）

**墨水屏（SPI，控制器 SSD1677，800×480，1bpp；4 灰度 2bpp）**

| 信号 | GPIO |
|------|------|
| SCK | 11 |
| MOSI | 12 |
| CS | 10 |
| DC | 9 |
| RST | 46 |
| BUSY | 3 |

**I2C 总线（400kHz）**：SDA = 41，SCL = 42
挂载器件：ES8311 0x18、PCF85063 0x51、SHTC3 0x70、QMI8658 0x6B

**音频 ES8311 / NS4150B（I2S）**

| 信号 | GPIO |
|------|------|
| MCLK | 13 |
| BCLK | 14 |
| LRCK(WS) | 47 |
| DOUT(喇叭) | 48 |
| DIN(麦克风) | 21 |
| 功放使能 PA_CTRL | 39 |

**TF 卡（SD_MMC 4bit）**：CLK 16、CMD 17、D0 15、D1 7、D2 8、D3 18

> 遗留疑点（上板前用原理图核对）：官方 06_QMI8658A 例程把 `SENSOR_IRQ` 也写成 39，与音频例程的 `PA_CTRL` 撞号。两者不可能都是 39，我倾向于 IMU 中断脚在例程里是抄来的占位值——不用中断就无影响，要用中断必须查 `ESP32-S3-ePaper-3.97-Schematic.pdf`。

---

## 五、对本项目的三条具体建议

| 事项 | 结论 |
|------|------|
| 墨水屏驱动库 | **直接用官方 `EPD_3in97.cpp` + `GUI_Paint.cpp`**（拷进 sketch 目录即可，无外部库依赖）。GxEPD2 对这颗 800×480 屏的支持不如官方驱动稳，别折腾 |
| 传感器库 | `SensorLib` v0.3.1（SHTC3 / QMI8658 用），库管理器可搜到 |
| 显存预算 | 800×480 单色整屏 = 48000 字节，512KB SRAM 放得下；4 灰度 = 96000 字节，建议开 PSRAM 双缓冲。整屏刷新慢（秒级），导诊结果页用"整屏刷一次 + 停留"，别做动画 |

---

## 六、异常速查

| 现象 | 原因 | 处理 |
|------|------|------|
| 端口列表里没有 COM | 未进下载模式 / 线是充电线 | 长按 BOOT 重插；换数据线 |
| 上传报 `Failed to connect` | 同上 | 同上，并把 Upload Speed 降到 460800 |
| 上传成功但串口无输出 | USB CDC On Boot 没开 | 改 Enabled 重编重传 |
| `ESP.getPsramSize()` = 0 | PSRAM 没选 OPI | 改 OPI PSRAM 重编重传 |
| 编译 `ESP_I2S.h: No such file` | 核心是 2.x | 升级到 3.2.0 |
| 首次编译 3-5 分钟 | 正常，全量编译 BSP | 等 |
| 墨水屏花屏/全黑 | 引脚宏与板子不符 / 没做 Init 就 Display | 核对第四节引脚，先 `EPD_3IN97_Init()` 再刷 |
