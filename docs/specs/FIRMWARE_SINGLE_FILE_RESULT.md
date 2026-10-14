# 当前固件单文件整合

最新变更：单文件已同步 Type-4 Stage0/1 防抖默认关闭。当前文件哈希和验证见 [AWP1221_TYPE4_DEBOUNCE_OFF.md](AWP1221_TYPE4_DEBOUNCE_OFF.md)。

后续更新：同一路径的单文件固件已同步“仅新保存 SIN/SINC”的规则。当前哈希和编译资源见 [AWP1221_TYPE4_SIN_SINC_ONLY.md](AWP1221_TYPE4_SIN_SINC_ONLY.md)；下文是首次整合记录。

日期：2026-09-30。

## 计划

1. 将 `outputs/Without_GUI` 当前主程序、8 个实现模块及全部项目头文件整合为一份可独立编译的 `.ino`。
2. 只移除已整合的项目内 include，保留 Arduino、EEPROM、Wire 和 AVR 标准依赖；不修改寄存器、协议、Type-4、Serial3/4、EEPROM 迁移或运行逻辑。
3. 将新文件保存到 `outputs/Without_GUI_Single/Without_GUI_Single.ino`，原模块化源文件保持原样。
4. 使用现有 Nano 旧 bootloader 目标编译，核对资源占用；检查合并覆盖与源文件哈希，并验证编译后的 Type-4 流程。

## 结果

单文件已生成：`outputs/Without_GUI_Single/Without_GUI_Single.ino`，126,434 字节、3,464 行。该目录只有这一份 `.ino`，不依赖原项目的 `.cpp/.h` 文件，仍使用 Arduino 平台自带的 EEPROM、Wire 和 AVR 标准库。

Arduino IDE 打开该文件后，沿用原目标：Arduino Nano / ATmega328P (Old Bootloader)，串口波特率 115200。当前 Type-4、新版 Serial3/4、其它芯片运行逻辑、板载按键和 EEPROM 迁移代码均已包含。

- 原始内容核验通过：17 个源文件的常量、结构体、声明、实现及主程序全部纳入；除已解决的本地 include 外，各段内容与原文件逐字一致，原文件 SHA-256 未变。证据：`outputs/firmware_single_20260930/source_manifest.json`、`source_validation.json`。
- Nano 产品编译通过：Flash 30,200 / 30,720 字节；静态 RAM 1,212 / 2,048 字节，与本次整合前的模块版相同。证据：`outputs/firmware_single_20260930/compile.txt`。
- 将单文件中的 setup/loop 换为现有测试入口后，重新编译并执行 AVR 指令模拟：8 组 Type-4 配方（含 Serial3/4）与当前 Python 描述符逐字相同；137 个 EEPROM 变更字节断电点恢复、8 种旧槽占用组合、128/256 字节 CFG 传输、CRC/非法输入、上电跳过/板载键重载、每次应用一次 `0x1F` 写入及 I2C 失败恢复均通过。证据：`outputs/firmware_single_20260930/avr_validation_result.json`。
- 测试入口和产品 HEX 分开；产品编译输出位于 `outputs/firmware_single_20260930/firmware`。
- 尚未烧录或实板验证；AVR 模拟替换了 I2C、EEPROM、UART 和时间外设，不等于实机断电验收。

交付 `.ino` SHA-256：`8059B1F6A49E682D2EE489D8F55FF015593A6FC58E0DE76B352B5445C153B520`。

产品 sketch HEX SHA-256：`C0763EC50045A9F9999766A6F707A6205252B69AA3D0994522073F2037FDEEC7`。

原模块版、GUI 及其它工作区改动均保持原样。本次未创建子智能体。
