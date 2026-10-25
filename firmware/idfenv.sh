#!/usr/bin/env bash
# 手工加载 ESP-IDF 5.5.5 环境（Git Bash 下 export.sh 的路径转换会失败）
# 用法： source firmware/idfenv.sh   然后 idfbuild / idfflash
export IDF_PATH="D:/ESP_IDF/Espressif/frameworks/esp-idf-v5.5.5"
export IDF_TOOLS_PATH="D:/ESP_IDF/Espressif"
export ESP_ROM_ELF_DIR="D:/ESP_IDF/Espressif/tools/esp-rom-elfs/20241011/"
export IDF_PYTHON_ENV_PATH="D:/ESP_IDF/Espressif/python_env/idf5.5_py3.11_env"
export IDF_CCACHE_ENABLE=1
export ESP_IDF_VERSION=5.5

T="/d/ESP_IDF/Espressif/tools"
export PATH="$T/xtensa-esp-elf/esp-14.2.0_20260121/xtensa-esp-elf/bin:$T/riscv32-esp-elf/esp-14.2.0_20260121/riscv32-esp-elf/bin:$T/esp-clang/esp-19.1.2_20250312/esp-clang/bin:$T/cmake/3.30.2/bin:$T/ninja/1.12.1:$T/idf-exe/1.0.3:$T/ccache/4.12.1/ccache-4.12.1-windows-x86_64:/d/ESP_IDF/Espressif/python_env/idf5.5_py3.11_env/Scripts:/d/ESP_IDF/Espressif/frameworks/esp-idf-v5.5.5/tools:$PATH"

IDF_PY="D:/ESP_IDF/Espressif/python_env/idf5.5_py3.11_env/Scripts/python.exe"
IDF_MAIN="D:/ESP_IDF/Espressif/frameworks/esp-idf-v5.5.5/tools/idf.py"

idfbuild() { MSYS2_ARG_CONV_EXCL="*" "$IDF_PY" "$IDF_MAIN" -C "D:/Agent_link/firmware" "$@"; }
