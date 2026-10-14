# Type-4 采样时间修正独立验收清单

状态：TYPE4_SAMPLE_FIX_REVIEW_PASS

本文件是 `docs/specs/TYPE4_SAMPLE_TIME_FIX_PLAN_20260930.md` 的只读验收准备。实施者返回 `TYPE4_SAMPLE_FIX_READY_FOR_REVIEW` 前，本轮不判定源码、单文件固件或 HEX 已完成，也不打开串口、不连接硬件、不烧录。

## 验收对象与边界

实施完成后按以下顺序检查：

1. `outputs/Without_GUI/awp1221.cpp` 的 `applyAwp1221Payload()`。
2. `outputs/Without_GUI_Single/Without_GUI_Single.ino` 中合并后的同名 Type-4 路径。
3. EEPROM Type-4 载荷定义和旧记录读取路径：`core/awp1221_preset.py`、`outputs/Without_GUI/awp1221.cpp` 的 `validateAwp1221Payload()`，以及 `outputs/Without_GUI/preset.cpp` 的类型分派。
4. 交付目录 `outputs/type4_sample_4us_fix_20260930/` 中的源清单、SHA-256、AVR 验证结果、模块固件和单文件固件。

只把源码、编译、AVR 模拟器和文件一致性作为软件证据；没有实板寄存器回读或按压力复测时，不写成硬件已修复。

## 0x05 有效位与期望值

Type-4 apply 读取的初始 `saved05` 必须作为保留字段基准。描述符写入期间低两位暂时切换为 SRAM 写入模式 `0x0003`，描述符完成后恢复为播放模式 `0x0002`；这段既有时序必须仍在修正后存在。

| 字段 | 掩码/来源 | 修正后的要求 |
|---|---|---|
| Zero Time | `0xE000`，记录 payload byte 2 左移 13 位 | 按旧记录中的 `zero_time` 合并，`((post05 >> 13) & 7) == payload[2]`。|
| ADC Sample Update | `0x0C00`，bits 11:10 | 固定为 `00`，即 4 us；无论初始值是 4/8/16/32 us，`post05 & 0x0C00 == 0`。|
| SRAM mode | `0x0003` | 保持既有最终值 `0x0002`；不得把临时 `0x0003` 留在最终寄存器。|
| 其它 0x05 字段 | `~0xEC03`，等价有效保留掩码 `0x13FC` | 与 apply 前 `saved05` 相比保持不变。修正不得整字写 0x05，也不得清除 bit12、bits9:2 等无关位。|

因此，对于任意 `saved05`，最终值的可验证关系为：

```text
expected05 = (saved05 & ~0xEC03) | (payload[2] << 13) | 0x0002
```

其中 `payload[2]` 只允许 0..7。验收必须分别以初始 sample code `0, 1, 2, 3`（分别代表 4/8/16/32 us）运行，不能只测默认 `0x442E` 或只测旧现场的 8 us。

## 其它 Type-4 寄存器不得被采样修正破坏

- `0x1F`：继续按现有 Type-4 apply 约定整字写 `0x000C`，即 ADC_LDO bit2 与 command-gate bit3 均为 1；本次修正不得改变该行为。
- `0x06`：返回时间字段仍用掩码 `0x00FC` 合并 payload byte 3/4；bits7:2 更新，掩码外字段保持 apply 前值。
- `0x12`：Type-4 记录不携带全局 debounce time；apply 不得新增 0x12 写入，既有全局值必须保持。
- `0x08`/`0x0D`：Stage0/Stage1 control 仍用既有 `0xFF33` 掩码和 500 us OVV/事件位。
- `0x09`、`0x0A`、`0x0E`、`0x0F`：阈值、跳转位、比较方向和 debounce 清除逻辑保持既有 Type-4 契约；四个寄存器的 bit15 仍清零，bit12 比较方向仍保留。
- `0x0B`、`0x10`、`0x0C`、`0x11`：本次修正不应引入新的写入或改变波形/斜率路径。
- 最终 `0x02` 仍写 `0x0001` 启动检测；描述符写入失败时不能启动检测，且仍需恢复 `0x15` 与 `0x05` SRAM 模式。

## EEPROM 格式与旧 Type-4 兼容

采样时间不得进入 EEPROM 载荷：

- `TYPE4_VERSION` 仍为 1；Python `_HEADER` 和 `_RECIPE` 布局不变。
- 固件 `TYPE4_HEADER_SIZE` 仍为 21，`TYPE4_RECIPE_SIZE` 仍为 14；count 1/2 的 payload 长度和 V2 槽位 256 字节契约不变。
- `zero_time`、press/release return、阈值、wave refs、repeat 和 waveform recipes 的偏移不变；旧槽位无需重新保存即可按新规则 apply。
- `validateAwp1221Payload()` 的旧格式校验和 Type-4 kind 兼容不得被采样修正改变。
- 既有旧 Type-4 kind 1/2/3（Gauss、Fixed-slope Ramp、DC）记录仍可读取和 apply；新 CFG 保存继续按既有规则拒绝这些 legacy kind，且拒绝不能改写原 EEPROM。

## Type-3/Type-5 边界

这里的 Type-3/Type-5 指 V2 payload type：

- Type 3 `PAYLOAD_TYPE_AWP192X_BUTTON_RUNTIME` 仍走 `applyAwp192xButtonPayloadV2FromEeprom()`。
- Type 5 `PAYLOAD_TYPE_AWP1018_ADC_STAGE4` 仍走 `applyAwp1018AdcPreset()`。
- `applyPresetWithDriver()` 的类型分派、signature 检查、启动顺序和 Type-3/Type-5 寄存器写入不得因 Type-4 的 `0x0C00` 修正而改变。
- 变更 diff 应只包含 Type-4 apply 逻辑、其同步单文件内容、验证脚本/报告和交付产物所需元数据；若 Type-3/Type-5 源码有实质变化，列为阻塞问题并要求解释。

## 模块与单文件同步

实施者交付后必须做内容级检查，而非只比较文件时间：

1. 从模块源 `outputs/Without_GUI/awp1221.cpp` 提取 `applyAwp1221Payload()`（包含相关注释和 `mergeReg` 调用）。
2. 在 `outputs/Without_GUI_Single/Without_GUI_Single.ino` 的 `awp1221.cpp` 分段提取同一函数。
3. 对比函数逻辑、掩码、常量、写入顺序和错误恢复；单文件必须包含 `0xEC00`（或等价的 `0x0C00` + `0xE000`）修正且不能保留旧的仅 `0xE000` 合并。
4. 若交付目录有 `source_manifest.json`，核对 manifest 中每个源文件 SHA-256 与工作区源一致，并确认 merged SHA-256 与实际单文件一致；manifest 不能只声明成功而缺少可重算的路径和 hash。
5. 编译输入必须来自当前 `outputs/Without_GUI` 和当前 `outputs/Without_GUI_Single`，不能只复用修正前的 build 目录或旧 HEX。

## 软件验证与产物来源

待实施者标志返回后，独立复核以下证据：

- `tests/prepare_type4_avr.py` 是否以当前模块源生成验证工程和 `avr_cases.json`，并覆盖 4/8/16/32 us 初始值。
- `tests/validate_type4_avr.js` 是否在编译后的 AVR 指令上验证 `0x05` 期望式、四种初始 sample、重复 apply、Stage debounce/方向、0x12/0x15 恢复和旧 Type-4 kind 1/2/3。
- Type-3 与 Type-5 至少保留 envelope/CFG 路径边界证据；若验证只针对 Type-4，报告中必须明确 Type-3/Type-5 未执行，而不能写“全类型通过”。
- 模块固件和单文件固件均为 `arduino:avr:nano:cpu=atmega328old` 目标，并分别提供 `.hex`、`.elf`、必要的 `.eep`/bootloader 产物、编译输出和 Flash/RAM 统计。
- 交付目录的 SHA-256 清单覆盖实际可烧录文件；报告注明未进行串口写入和实板烧录。

## 独立验收结论规则

- 满足上述源码、兼容性、同步、验证和产物证据后，返回 `TYPE4_SAMPLE_FIX_REVIEW_PASS`。
- 任一字段掩码、旧格式、Type-3/5 边界、单文件同步、编译来源或 hash 证据不满足，列出精确路径/行号并返回具体问题，不返回通过标志。
- 即使软件验收通过，也只表示源码/编译/模拟器边界通过；实板效果需另行获得烧录授权并复测。

## 当前状态

独立只读验收已完成。实施者已返回 `TYPE4_SAMPLE_FIX_READY_FOR_REVIEW`，本次检查通过；结论仅覆盖源码、构建产物、manifest/hash 和 AVR 模拟器证据，不包含串口、烧录或实板按压力复测。

## 2026-09-30 独立验收记录

- 最小掩码：`outputs/Without_GUI/awp1221.cpp:130` 和单文件 `outputs/Without_GUI_Single/Without_GUI_Single.ino:672` 均为 `0xEC00`。该值等于 `0xE000 | 0x0C00`；与现有 SRAM/播放位恢复 `~0x0003` 合并后，保留掩码为 `~0xEC03 = 0x13FC`。逐一代入初始 `0x05[11:10] = 00/01/10/11`，结果均清为 `00`、低两位恢复为 `10`，Zero Time 和其余 `0x13FC` 位保持不变。
- 单文件同步：按函数内容提取比较，模块 `applyAwp1221Payload()` 与单文件对应分段完全一致；交付构建副本 `build_modular/sketch/awp1221.cpp:131`、`build_single/sketch/Without_GUI_Single.ino.cpp:842` 也保留同一 `0xEC00` 调用。单文件未发现 AVR 测试入口。
- Hash/来源：逐项重算 `source_manifest.json` 的 17 个源文件和 merged 单文件 hash，均与当前工作区一致；逐项重算 `delivery_sha256.json` 的 8 个交付文件，均一致。两个 Nano 构建 `build.options.json` 的 FQBN 均为 `arduino:avr:nano:cpu=atmega328old`，sketchLocation 分别指向当前模块目录和当前单文件目录；交付 ELF 的 `avr-size` 均为 Flash 30220、RAM 1212。
- 32-case 证据：现有 `tests/validate_type4_avr.js` 对四种初始采样值 `0x0000/0x0400/0x0800/0x0C00` 与 8 个波形 case 做 32 个编译 AVR 指令级 apply 用例，逐案检查 `0x05/0x06/0x12/0x15`、描述符、Stage 位和启动顺序；交付 `avr_validation_result.json` 与 runner 结果一致。本轮未重复完整验证。
- 兼容与边界：`core/awp1221_preset.py` 的 Type-4 version/length/recipe 定义未增加 sample 字段；旧 kind 1/2/3 读取/迁移证据仍在 fixture 中。`outputs/Without_GUI/preset.cpp` 的 Type-3/4/5 分派及 `awp192x.cpp`、`preset.cpp` 与修正前基线无差异；Type-3/Type-5 仍走各自路径。

验收标志：`TYPE4_SAMPLE_FIX_REVIEW_PASS`
