# AWP1221 Type-4 实现与验收

最新变更：Type-4 不保存 debounce，每次应用均关闭 Stage0/1 四个防抖使能位。当前交付见 [AWP1221_TYPE4_DEBOUNCE_OFF.md](AWP1221_TYPE4_DEBOUNCE_OFF.md)；下文的“保持 bit15”是历史行为，已被此次要求替代。

后续更新：新保存范围已改为仅 SIN/SINC，旧 Type-4 记录兼容。当前 GUI/固件、空间测量和验证见 [AWP1221_TYPE4_SIN_SINC_ONLY.md](AWP1221_TYPE4_SIN_SINC_ONLY.md)。下文记录首次交付状态。

日期：2026-09-30。依据 `AWP1221_TYPE4_DEFINITION_PLAN.md` 的 61 项确认。用户最后指定当前模型直接执行，不使用子智能体；本次未创建子智能体。

## 交付与使用

- 源固件：`outputs/Without_GUI/Without_GUI.ino` 及同目录 `.cpp/.h`。
- GUI：`outputs/type4_20260930/gui/AnalogwinPiezoGUI.exe`。
- Nano 旧 bootloader 编译输出：`outputs/type4_20260930/firmware/Without_GUI.ino.hex`；同时提供包含 bootloader 的编译输出，但常规 IDE 上传使用 sketch HEX。本次没有上传任何 HEX。
- 原始固件与本次涉及的 Python 文件备份：`outputs/type4_20260930/baseline`；SHA-256 在同级 `baseline_sha256.json` 与 `languages_baseline_sha256.json`。
- 最终交付文件 SHA-256：`outputs/type4_20260930/delivery_sha256.json`。

需要配套使用本次新固件和新 GUI，旧 EXE 的 Type-4 占位按钮不会因仅修改 `.ino` 而改变。固件首次启动迁移 EEPROM，保留活动槽选择；不要求回刷旧 128 字节步长固件。

Button Mode 的四个动作：保存只保存当前 GUI 输入及实际引用的波形；设为活动只改变活动槽；应用槽位加载波形、配置 Stage0/1 并开始检测；读取并应用先校验读回记录，再应用芯片，并回填保存的参数。通信级 `CFGREAD` 保持只读，上传读回校验不会意外应用芯片。应用按钮与读取按钮会检查所存类型和当前目标地址。

活动槽为 Type-4 时，板载预设按键重载该活动槽；其它类型保持原有按键到槽位的映射。上电跳过 Type-4 的芯片复位及自动应用，其它类型保留原流程。每次 Type-4 应用在驱动入口直接写一次 `0x1F=0x000C`；此值不进入载荷，保存、设为活动及校验读取都不写该寄存器。

GUI 以所引用的 Slice/Serial 编辑参数构造配方，无需读取芯片寄存器。ROM 编辑器新增各 Slice 的输入缓存，可保存尚未写芯片的修改。尚未在 GUI 定义的普通 Slice 会明确报缺少配方；原始 RAM 样点没有可重建的 ROM/SINC 配方，不能当作 Type-4 保存。此限制不影响 Full Mode 的 RAM 操作。

## 格式

物理 EEPROM 槽固定 256 字节。记录头仍为 `<HBBHBB`（8 字节）：magic `0xA192`、记录版本 2、类型、芯片签名、器件地址、载荷长度。类型 4 的签名为 `0x1221`，地址 `0x50..0x57`。记录 CRC16-CCITT-FALSE 紧随载荷，其余填充 `0xFF`。CFG 上传/读取另外校验完整线上记录的传输 CRC。

Type-4 载荷版本 1；21 字节公共头如下，所有多字节字段为小端。

| 偏移 | 长度 | 内容 |
| --- | --- | --- |
| 0 | 1 | 载荷版本 1 |
| 1 | 1 | 实际使用的不同配方数（两阶段最多引用两条） |
| 2 | 1 | ADC Zero Time 码，0..7 |
| 3、4 | 各 1 | Press、Release Return Bit Time 码，0..7 |
| 5、9 | 各 4 | 共用 SINC 最大/最小电压，IEEE754 float32；无 SINC 时为零 |
| 13、15 | 各 2 | Stage0/1 原始 12 位阈值 |
| 17、18 | 各 1 | Stage0/1 配方索引 |
| 19、20 | 各 1 | Stage0/1 事件重复次数码，0..7 |

每条配方 14 字节，顺序为 `kind, coarse, fine, offset, time1:u16, time2:u16, time3:u16, time4:u16, repeat:u16`。

- `kind=0..3`：SINE、Gauss、Fixed-slope Ramp、DC；保存 VPK、Micro Bias、Offset、Point Time、内部重复次数。只用 time1，其余时间字段为零。
- `kind=4`：当前三段 SINC，保存 time1..3，time4 为零，repeat 固定 1；幅度码及偏置由共享电压生成。
- `kind=5`：保留工作区现有 Serial3/4 的完整 SIN＋三段 SINC，保存四个时间及首段 SIN 重复次数。它不是新的用户相位选择，仍沿用既有固定连续三段构造。
- 波形共享按配方内容去重；不同阶段可引用同一条。禁止未引用配方和重复定义。
- 载荷为 35 或 49 字节，含记录头及 CRC 后为 45 或 59 字节，再填充到 256 字节；保存的是参数而非原始样点。

固件在应用时自动分配：单条 ROM 位于 Slice0 起；三段 SINC 使用 Serial1/2（18..20、21..23）；SIN＋SINC 使用 Serial3/4（24..27、28..31）。原 SRAM 目标位置不存入配方，页面回填使用实际分配结果。

## 寄存器处理

- Stage0 固定按下、Stage1 固定释放；两阶段 OVV 时间固定 500 us（码 3）；跳转固定 0→1、1→0；阈值精度固定 1。
- 控制寄存器按掩码 `0xFF33` 合并；阈值寄存器保留 debounce bit15 和比较方向 bit12；保存的阈值写到 Stage0 按下、Stage1 释放项。两个事件项使用对应阶段相同的波形与事件重复次数，沿用现有 Button Mode 编码。
- Zero Time 合并 `0x05[15:13]`，并将运行时 `0x05[11:10]` 固定为 `00`（4 us）；Return Bit Time 合并 `0x06[7:2]`。描述符访问临时切换 SRAM 模式，完成/写失败后恢复 SRAM 地址及播放访问模式。
- ADC Sample Update 不新增 EEPROM 字段；Type-4 apply 固定运行时使用 4 us。ZPS、ZPS Detect、Sleep Sensitivity、Auto Serial 不由 Type-4 改写；全局 Debounce Time 寄存器 `0x12` 不写；阶段防抖使能位按当前 Type-4 约定关闭。MCU 物理按键的既有 35ms 去抖保持原状，与芯片阶段防抖不同。
- ROM 深度固定 512、半周期 00、相位 0、无限循环 0；SINC 深度固定 512，按现有三段生成器重建。AVR 直接解析 float32 的正数编码向内量化，避免引入浮点运算库导致 Flash 超限。

## EEPROM 扩容与兼容

新槽基址为 256、512、768，总范围 256..1023；活动槽仍在地址 0。上传暂存为 256 字节 RAM 数据及 32 字节接收位图，不再写 EEPROM 上传区。

首次迁移按旧 Slot2→Slot1→Slot0 的次序：旧地址为 512、384、256，新地址为 768、512、256。先提交目标，再推进 EEPROM 地址 250..252 的布局标记/进度；在对应步骤提交前，旧源仍保留。最后一步才填充 Slot0 的后半区，防止覆盖尚未迁移的旧 Slot1。断电重启可重复当前步骤；只迁移格式、CRC 和载荷有效的旧 Type-3/Type-5，空/无效旧槽对应新空槽，活动指针不改。

所有类型物理槽均为 256 字节。旧 Type-3/Type-5 保持 128 字节线上记录，`CFGBEGIN` 接受 128/256，旧类型 `CFGREAD` 仍输出 128，Type-4 输出 256。旧 GUI 可以继续读写旧类型。新的记录在 EEPROM 中一律填充到 256 字节。拒绝缺少上传片段、错误 CRC、非法载荷、槽号截断回绕。

## 验收证据与边界

- Python/Tk：最终相关回归共 156 项通过（含 11 项新增 Type-4 测试）。新增覆盖所有 ROM 类型、共享/不同 SINC、Serial3/4、GUI 未应用输入、回填、原始 `0x800` 阈值、按钮实际回调、256 字节传输、CRC 及不能重建的 RAM 输入。测试同时禁止真实串口/I2C；完整输出在 `python_tests.txt`，销毁 Tk 测试窗口时存在既有 ThemeChanged 异步提示，测试断言和进程退出均通过。
- AVR 模拟：`tests/prepare_type4_avr.py` 从产品源码生成测试入口；`tests/validate_type4_avr.js` 执行编译后的 AVR 指令。结果在 `outputs/type4_20260930/avr_validation_result.json`：8 组波形与 Python 描述符逐字相同；137 个实际变更字节的断电点均恢复；8 种旧槽占用组合、旧类型读写/应用、RAM 暂存、错误处理、Type-4 上电跳过/按键重载、`0x1F` 次数、描述符写失败恢复均通过。
- 模拟最低栈距测试固件静态数据末端 594 字节；模拟外设替换了 Wire/UART/EEPROM/时间函数，此数不是包含真实外设调用栈的硬件峰值。
- 产品 Nano 编译：基线 Flash 27,072、静态 RAM 939；本次 Flash 30,200 / 30,720（余 520）、静态 RAM 1,212 / 2,048（余 836）。按原 Nano 旧 bootloader 目标编译通过。详细输出在 `firmware_compile.txt`。
- EXE 构建和基本启动检查通过，证据分别在 `exe_build.log`、`exe_launch_check.txt`；新 EXE 中确认包含两个新增 Type-4 模块。基本启动只证明窗口显示、响应和正常退出。沙箱首轮检查无进程操作权限，已仅清理该次测试进程后改用正常用户权限验证。
- 新 EXE 的 500ms 缩放压力检查在 small（640×480 外窗口请求）步骤重复出现响应超时，记录为 `exe_resize_check.txt`。用本次 Type-4 相关文件的开始时备份重建基线 EXE 后，同一检查也在 small 步骤失败，证据为 `exe_baseline_resize_check.txt`；该问题在没有本次 Type-4 改动的基线中存在。本次未改动既有布局响应逻辑，保留此未通过项；基本启动通过不等于缩放验收通过。
- 未进行真实串口板卡验证、EEPROM 实机断电试验或烧录，尚不能声称硬件验收完成。模拟断电模型在单字节写完成后中断，不模拟 EEPROM 单字节写入中的电压异常/损坏。

## 重跑

1. `py -m unittest discover -s tests -p 'test_awp1221*.py'`；`py -m unittest tests.test_awp192x_button_detect`。
2. 使用安装的 Arduino CLI，对 `outputs/Without_GUI` 以 `arduino:avr:nano:cpu=atmega328old` 编译。
3. `py tests/prepare_type4_avr.py`；以 `arduino:avr:uno` 编译 `outputs/type4_20260930/Type4Validation`，build path 为 `outputs/type4_20260930/build_validation`（测试入口与产品 HEX 分离）。
4. 用 `avr-nm -C` 将测试 ELF 的符号表输出到 `outputs/type4_20260930/avr_symbols.txt`。在 `outputs/type4_20260930/avr_validation` 安装 `avr8js` 后运行 `node tests/validate_type4_avr.js`。
5. PyInstaller 使用既有 `AnalogwinPiezoGUI.spec`，输出到上述任务目录；用现有 `build_support/check_windows_launch.py` 检查本次新 EXE。

本次未提交 Git；工作区原有界面、布局、SINC Serial3/4 及其它修改保持。执行期间另一个会话更新了 SINC 深度提示和预览端点，本次保持这些修改。`baseline_difference.diff` 是开始时备份到当前的全部差异，`concurrent_sinc_changes.diff` 单独记录上述并发变化；`task_changes.diff` 过滤它们后记录已有文件的本次 Type-4 差异（新增文件另见源码）。不把整个脏工作区 diff 作为 Type-4 改动。

交付 SHA-256：GUI `13D41C29647609E4184381C2D67AE5E03FC03EAFF48B88BFA8A92C130ED56930`；产品 sketch HEX `3E273D669573F6E6392EEFA7AD5293FD1AE73F66591639000CF78599490A515C`。完整清单见 `delivery_sha256.json`。
