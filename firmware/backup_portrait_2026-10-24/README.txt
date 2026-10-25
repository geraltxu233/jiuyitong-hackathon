应用分区备份（竖屏定稿 / 接入 agent_stack 之前）
=================================================

文件
  agent_link_portrait_1620352.bin   1,620,352 字节 (0x18B980)
  sha256 7f33a337ce9e5bf0aaf0ec5a6931916a60d29489abe2add1a4ba86e3b67ed9ff
  app_src/                          该版本全部 10 个源文件（.cc/.h）
  board_config/config.h             该版本的板级配置（三档字库 28/35/40 px）

对应源码
  D:/Agent_link 仓库，标签 pre-agent-stack-2026-10-24
  提交信息：竖屏定稿：三档字库 28/35/40 px、界面全中文、修退出通话后回复滞留

烧录（只写应用分区，不动字库、不动 NVS，WiFi 与设备绑定都保留）
  cd D:/Agent_link/firmware/build
  "D:/ESP_IDF/espressif/python_env/idf5.5_py3.11_env/Scripts/python.exe" -m esptool ^
    --chip esp32s3 -p COM6 -b 460800 --before default_reset --after hard_reset ^
    write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m ^
    0x10000 <本目录>/agent_link_portrait_1620352.bin

  实测 17 秒写完。烧完自动重启，约 4 秒出首页。

什么时候用这个
  新固件能启动、只是功能或界面改坏了 —— 用这个，够快。

什么时候不能用这个
  分区表被改过、NVS 被擦过、字库分区被动过 —— 那些不在应用分区里，
  git 回退和这个 bin 都救不回来，必须用 ../backup_fullflash_2026-10-24/ 的整片备份。
