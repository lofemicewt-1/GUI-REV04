# Type-4 槽位采样时间修正结果

日期：2026-09-30  
交付标志：`TYPE4_SAMPLE_FIX_READY_FOR_REVIEW`

## 修正内容

用户要求把 Type-4 槽位应用改为与 GUI 4 us 的较轻状态一致。本次只修改 Type-4 固件 apply 路径：

- [outputs/Without_GUI/awp1221.cpp](../../outputs/Without_GUI/awp1221.cpp) 的 `applyAwp1221Payload()` 将 `0x05` 的合并掩码从 `0xE000` 改为 `0xEC00`。
- `0xEC00 = 0xE000 | 0x0C00`：继续从 Type-4 记录合并 Zero Time `0x05[15:13]`，同时清除运行时采样位 `0x05[11:10]` 为 `00`（4 us），其余 `0x05` 位和 SRAM/描述符访问模式保持原逻辑。
- `0x1F=0x000C`、Stage0/1 四个 debounce bit15 清除、阈值、比较方向 bit12、500 us OVV、波形引用、重复次数、Return Bit Time 和最后 `0x02=0x0001` 均保持原契约。
- [outputs/Without_GUI_Single/Without_GUI_Single.ino](../../outputs/Without_GUI_Single/Without_GUI_Single.ino) 已由既有 `outputs/type4_debounce_off_20260930/merge_firmware.py` 从当前模块源重新合并；单文件中包含同一 `0xEC00` 修正，没有带入 AVR 测试入口。
- EEPROM Type-4 载荷字节数和格式未改变，不新增采样字段；已有槽位记录无需重新保存。Type-3/Type-5 路径未修改。

定义文档第 38 项和诊断报告已同步：采样间隔不保存到 EEPROM，但 Type-4 apply 的运行时值固定为 4 us。

## 交付物与 SHA-256

完整清单见 [delivery_sha256.json](../../outputs/type4_sample_4us_fix_20260930/delivery_sha256.json)，源文件清单见 [source_manifest.json](../../outputs/type4_sample_4us_fix_20260930/source_manifest.json)。

| 文件 | SHA-256 |
|---|---|
| `outputs/Without_GUI/awp1221.cpp` | `687E772615713320151E9B6182564EFF109ACFA98802B9E999DA7B0FE5C6E7F4` |
| `outputs/Without_GUI_Single/Without_GUI_Single.ino` | `BEF2AFC1C6B0A672633655852127D6F221A7783BF922B43959FD2D78E49C5A0D` |
| `outputs/type4_sample_4us_fix_20260930/firmware_modular/Without_GUI.ino.hex` | `013AE4756C3521CE43567A3A3414284E985211D29051DBC3BB6012D04151C98F` |
| `outputs/type4_sample_4us_fix_20260930/firmware_single/Without_GUI_Single.ino.hex` | `1468F30B9ACBF4AB38C01791CC0378A294E5153788ADE6570C99D6FF9E26FAA8` |

验证 fixture 的 HEX 和测试 runner 哈希也保存在 `delivery_sha256.json`。

## 编译与验证

- 模块 Nano old bootloader：`arduino-cli compile --fqbn arduino:avr:nano:cpu=atmega328old outputs/Without_GUI`，通过；Flash **30220/30720 bytes（98%）**，RAM **1212/2048 bytes（59%）**。
- 单文件 Nano old bootloader：同一目标编译通过；Flash **30220/30720 bytes（98%）**，RAM **1212/2048 bytes（59%）**。
- Type-4 AVR fixture 编译通过；Node `avr8js` 结果见 [avr_validation_result.json](../../outputs/type4_sample_4us_fix_20260930/avr_validation_result.json)。
- 覆盖初始采样位 4/8/16/32 us 的 32 个 Type-4 apply 用例：最终均为 4 us，同时保留 Zero/Return、其他 `0x05` 有效位、Stage 阈值/方向、波形描述符和启动顺序。
- 同一 AVR harness 继续通过四个 Stage debounce 位清除、重复 CFGAPPLY、EEPROM 迁移 137 个断电点、旧 Gauss/Ramp/DC Type-4 兼容读取、Type-3/Type-5 记录迁移、CFG 上传/CRC/槽号校验、物理按键重载和 I2C 失败恢复。

验证结果是编译 AVR 指令和 mock EEPROM/I2C/UART/时间环境的证据；没有打开 COM14，也没有烧录。

## 独立只读验收

验收标志：`TYPE4_SAMPLE_FIX_REVIEW_PASS`

我按独立清单复核了最终源码和交付目录，而非仅采信实施者描述：

- `outputs/Without_GUI/awp1221.cpp:130`、`outputs/Without_GUI_Single/Without_GUI_Single.ino:672` 以及两个 Nano 构建副本均使用 `0xEC00` 合并 `0x05`。`0xEC00` 只占 Zero Time 与采样字段，结合既有低两位播放模式恢复后，保留掩码为 `0x13FC`；四种初始采样编码逐一代入均得到 4 us，Zero Time、其余有效位和播放模式保持。
- 模块函数与单文件函数内容级比较一致，单文件没有 AVR 测试入口。Type-4 EEPROM version/length/recipe 格式未变；Type-3/Type-5 分派源码保持原路径，旧 Type-4 kind 1/2/3 证据仍由现有 AVR fixture 覆盖。
- 重新计算 `source_manifest.json` 的 17 个源文件和 merged 单文件 hash、`delivery_sha256.json` 的 8 个交付文件 hash，均与实际文件一致。两个 `build.options.json` 都指向 `arduino:avr:nano:cpu=atmega328old` 和当前源目录；交付 ELF 的 `avr-size` 均为 Flash 30220、RAM 1212。
- 现有 runner 对 4/8/16/32 us 初始采样 × 8 waves 的 32 个 AVR cases 检查寄存器掩码、描述符、Stage 和启动顺序；本轮因证据与当前 hash/源码一致而未重复完整测试。

上述结论是离线源码、编译、文件一致性和 AVR 模拟器边界结论。没有 COM14 访问、串口写入、烧录或实板按压力复测，因此不把它表述为硬件根因或实板效果确认。

## 硬件边界

本次没有重新连接 COM14、没有向芯片发送写入、没有烧录固件。此前的 COM14 快照和用户 GUI 日志证明了 8 us 与 4 us 的实际复现差异；本次交付只证明源码、单文件合并、编译和离线模拟已采用 4 us 策略。实板修复需在另行授权烧录后，通过寄存器读回和按压力复现验收。

## GPT6 最终验收

最终验收通过：已接收实施与独立检查完成标志，并核对模块及单文件的 0xEC00 修正、32 个初始采样验证用例结果、定义文档第38项，以及单文件源码和两份产品 HEX 的实际 SHA-256 与清单一致。软件修正与交付已完成；实板烧录及烧录后效果验证尚未执行。烧录后 GUI 采样时间应选择 4 us 进行一致性复测。
