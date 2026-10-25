/*
 * 00_board_check.ino
 * ESP32-S3-ePaper-3.97 板卡自检
 *
 * 用途：验证 Arduino IDE 的 Tools 参数是否配对，以及板子主要外设是否在线。
 *      不依赖任何第三方库，刷完看串口即可。
 *
 * 期望输出（全部 PASS）：
 *   Chip        : ESP32-S3  rev 0.x, 2 core(s), 240 MHz
 *   Flash       : 16.00 MB            -> Flash Size 选了 16MB
 *   PSRAM       : 8.00 MB 可用        -> PSRAM 选了 OPI PSRAM
 *   I2C         : 0x18 ES8311 / 0x51 PCF85063 / 0x70 SHTC3 / 0x6B QMI8658
 *
 * 若串口完全无输出 -> 检查 USB CDC On Boot = Enabled（否则 Serial 走 UART0）
 * 若 PSRAM = 0     -> 检查 PSRAM = OPI PSRAM
 * 若 Flash = 4MB   -> 检查 Flash Size = 16MB (128Mb)
 */

#include <Arduino.h>
#include <Wire.h>
#include <esp_chip_info.h>
#include <esp_flash.h>
#include <esp_system.h>

// ---- 板级引脚（来自微雪官方例程 DEV_Config.h / 01_Audio_Test.ino）----
#define I2C_SDA_PIN 41
#define I2C_SCL_PIN 42

static const uint32_t EXPECT_FLASH_BYTES = 16UL * 1024UL * 1024UL;
static const uint32_t EXPECT_PSRAM_BYTES = 8UL * 1024UL * 1024UL;

struct i2c_expect_t {
    uint8_t     addr;
    const char *name;
};

// 仅列官方例程里出现过的器件；TG28 电源管理芯片地址未在例程中体现，不做判定
static const i2c_expect_t I2C_EXPECT[] = {
    {0x18, "ES8311 (audio codec)"},
    {0x51, "PCF85063 (RTC)"},
    {0x70, "SHTC3 (temp/humi)"},
    {0x6B, "QMI8658 (IMU)"},
};

static void print_chip_info()
{
    esp_chip_info_t info;
    esp_chip_info(&info);

    Serial.printf("Chip        : ESP32-S3  rev %d.%d, %d core(s), %lu MHz\n",
                  info.revision / 100, info.revision % 100,
                  info.cores, (unsigned long)getCpuFrequencyMhz());
    Serial.printf("Arduino core: %s\n", ESP.getSdkVersion());

    // 注意：这是编译进固件的核心版本，与你 Board Manager 里装的一致
    Serial.printf("Sketch build: %s %s, IDF %s\n", __DATE__, __TIME__, esp_get_idf_version());
}

static bool check_flash()
{
    uint32_t size = 0;
    esp_err_t err = esp_flash_get_size(NULL, &size);
    if (err != ESP_OK) {
        Serial.printf("Flash       : read failed (0x%X)\n", err);
        return false;
    }
    bool ok = (size >= EXPECT_FLASH_BYTES);
    Serial.printf("Flash       : %.2f MB  [%s]\n", size / 1048576.0f,
                  ok ? "PASS" : "FAIL, expect 16MB");
    return ok;
}

static bool check_psram()
{
    size_t total = ESP.getPsramSize();
    if (total == 0) {
        Serial.println("PSRAM       : 0 MB  [FAIL, set Tools > PSRAM = OPI PSRAM]");
        return false;
    }
    bool ok = (total >= EXPECT_PSRAM_BYTES);
    Serial.printf("PSRAM       : %.2f MB total, %.2f MB free  [%s]\n",
                  total / 1048576.0f, ESP.getFreePsram() / 1048576.0f,
                  ok ? "PASS" : "FAIL, expect 8MB OPI");
    if (total > 0) {
        // 实测 malloc 能否真正落在 PSRAM 上（有些错误配置下 size 报数但分配失败）
        void *p = heap_caps_malloc(1024 * 1024, MALLOC_CAP_SPIRAM);
        Serial.printf("PSRAM 1MB alloc: %s\n", p ? "OK" : "FAILED");
        if (p) {
            free(p);
        }
    }
    return ok;
}

static int scan_i2c()
{
    Serial.println("I2C scan    : (SDA=41, SCL=42, 400kHz)");
    int found = 0;
    for (uint8_t addr = 1; addr < 127; addr++) {
        Wire.beginTransmission(addr);
        if (Wire.endTransmission() == 0) {
            const char *name = "unknown device";
            for (size_t i = 0; i < sizeof(I2C_EXPECT) / sizeof(I2C_EXPECT[0]); i++) {
                if (I2C_EXPECT[i].addr == addr) {
                    name = I2C_EXPECT[i].name;
                    break;
                }
            }
            Serial.printf("              0x%02X  %s\n", addr, name);
            found++;
        }
        delay(2);   // 给总线留恢复时间，避免个别器件 clock stretching 把扫描带死
    }
    Serial.printf("              total %d device(s)\n", found);
    return found;
}

static bool check_i2c_expected()
{
    bool all_ok = true;
    for (size_t i = 0; i < sizeof(I2C_EXPECT) / sizeof(I2C_EXPECT[0]); i++) {
        Wire.beginTransmission(I2C_EXPECT[i].addr);
        bool ok = (Wire.endTransmission() == 0);
        if (!ok) {
            Serial.printf("              MISSING 0x%02X %s\n",
                          I2C_EXPECT[i].addr, I2C_EXPECT[i].name);
            all_ok = false;
        }
    }
    return all_ok;
}

void setup()
{
    Serial.begin(115200);
    delay(1500);            // 等 USB CDC 枚举完成，否则前面几行会丢

    Serial.println();
    Serial.println("==== ESP32-S3-ePaper-3.97 board check ====");

    print_chip_info();

    bool flash_ok = check_flash();
    bool psram_ok = check_psram();

    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, 400000);
    Wire.setTimeOut(50);
    scan_i2c();
    bool i2c_ok = check_i2c_expected();

    Serial.println("------------------------------------------");
    bool all = flash_ok && psram_ok && i2c_ok;
    Serial.printf("RESULT      : %s\n", all ? "ALL PASS - 环境配置正确" : "HAS FAILURE - 见上方 FAIL/MISSING 项");
    Serial.printf("Free heap   : %lu bytes\n", (unsigned long)ESP.getFreeHeap());
    Serial.println("==========================================");
}

void loop()
{
    delay(1000);
}
