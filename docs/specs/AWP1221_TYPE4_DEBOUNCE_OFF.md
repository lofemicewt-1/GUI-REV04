# Type-4 Stage0/1 debounce 默认关闭

日期：2026-09-30。

## 确认和范围

Type-4 载荷不保存阶段 debounce 使能或全局 Debounce Time。之前的驱动保持阈值寄存器 bit15 原值，因此芯片上已启用 debounce 时，应用 Type-4 不会关闭。

用户要求默认全部关闭，进一步确认只关闭 Type-4 使用的 Stage0/1 的按下、释放 debounce。Type-4 仅定义这两个阶段；此前确认选项中提及 Stage2/3 是助手错误，不形成新增阶段需求。

## 实施计划

1. 每次应用 Type-4，清除 `0x09/0x0A/0x0E/0x0F` 的 debounce bit15，同时保留比较方向 bit12 和未配置的阈值位。
2. Type-4 读取回填把 Stage0/1 debounce 勾选关闭；Button Mode 初始值和软件预设默认值关闭，手动配置仍可按用户操作开启。
3. Type-4 格式、EEPROM 记录、全局 `0x12` 及 MCU 物理按键 35ms 去抖不变；其它芯片不变。
4. 同步模块、单文件固件和 GUI；验证开启/关闭初态、重复应用、失败退出、旧记录兼容和 SIN/SINC 重建，按 Nano 旧 bootloader 编译并记录资源。
5. 当前模型直接执行，不创建子智能体。基线备份：`outputs/type4_debounce_off_20260930/baseline`。

## 验收结果

已完成。固件将阈值寄存器更新掩码从 `0x6000` 扩至 `0xE000`，写入值的 bit15 固定为 0，因此原本开启或关闭的四个开关在每次成功应用 Type-4 后都关闭；阈值比较方向 bit12 保留。没有新增寄存器写入次数或 EEPROM 字段。保存或设为活动槽不会应用芯片；应用槽位、读取并应用及板载按键加载走同一关闭逻辑。

GUI 初始化、全部 Button Mode 软件预设及 Type-4 回填的 debounce 状态已关闭。手动直接配置芯片的开关仍能使用；其状态不被 Type-4 保存，下一次应用 Type-4 会重新关闭。全局 `0x12` 不写，物理按键 35ms 去抖不变。

## 资源与格式

| 项目 | 变更前 | 变更后 |
| --- | ---: | ---: |
| Nano Flash | 30,220 B | 30,220 B |
| Nano 静态 RAM | 1,212 B | 1,212 B |
| Type-4 单 / 双配方载荷 | 35 / 49 B | 35 / 49 B |
| 物理槽 / Type-4 传输记录 | 256 B | 256 B |

目标仍为 `arduino:avr:nano:cpu=atmega328old`；模块和单文件版本均通过。载荷版本 1 保持不变，新保存仍仅允许 SIN / SINC（含 Serial3/4）；原 Type-4 记录继续兼容。

## 当前交付

- 单文件固件：`outputs/Without_GUI_Single/Without_GUI_Single.ino`。
- 模块固件：`outputs/Without_GUI/Without_GUI.ino` 及同目录 `.cpp/.h`。
- GUI：`outputs/type4_debounce_off_20260930/gui/AnalogwinPiezoGUI.exe`。
- 单文件 sketch HEX：`outputs/type4_debounce_off_20260930/firmware_single/Without_GUI_Single.ino.hex`。
- 模块 sketch HEX：`outputs/type4_debounce_off_20260930/firmware_modular/Without_GUI.ino.hex`。
- 完整 SHA-256：`outputs/type4_debounce_off_20260930/delivery_sha256.json`。

`.ino` SHA-256：`0BDFB1BD6B7DE38D1A3CD38B1EB7331DA5879DD836EC351747C34AF0C1DB6702`。

单文件 HEX SHA-256：`FB67356C979CBD793C8FA3BB03383ED838D1F62E4C8A9C298C85EF8F59B1112F`。

GUI SHA-256：`0ECF4670245531919B95EF681AA05BDB6E4F4C0A2C30762F5D7C6AF0DDD8FE51`。

## 验证证据

所有证据位于 `outputs/type4_debounce_off_20260930`。

- 162 项 AWP 回归通过，含 17 项 Type-4 检查：页面初始化、软件预设加载、读取回填关闭；改变 GUI debounce 状态不改变保存的载荷；SIN/SINC 与旧记录兼容。证据：`python_tests.txt`、`type4_tests.txt`。Tk 测试窗口销毁时有既有 ThemeChanged 异步提示，断言和退出码通过。
- 模块版和单文件版 AVR 指令模拟通过：初始开/关两种状态及重新开启后重复应用均关闭四个 bit15，同时保留 bit12 和 `0x12`；8 组波形含 Serial3/4 逐字匹配；旧类型读取/应用、新保存筛选、137 个迁移字节断电点、8 种旧槽占用、CRC/128/256 字节传输、上电跳过/板键重载及 I2C 失败退出仍通过。证据：`avr_validation_result.json`、`single_validation/avr_validation_result.json`。
- 17 个源文件全部纳入单文件；除已解决的本地 include 外内容逐字一致。8 组允许波形与变更前编码逐字一致，3 组旧 ROM 记录的描述符兼容。证据：`source_validation.json`、`source_manifest.json`。
- Nano 产品编译通过，分别见 `firmware_modular_compile.txt`、`firmware_single_compile.txt`。
- 新 EXE 打包和基本启动通过；嵌入的两个页面模块及语言模块与当前源代码的编译内容一致，已确认不存在旧默认值。证据：`exe_build.log`、`exe_embedded_check.txt`、`exe_launch_check.txt`。
- 未进行真实串口、EEPROM 写入或固件烧录；AVR 的 I2C/EEPROM/UART/时间为模拟外设。基本启动不等于实板或完整布局验收，本次不重复既有窗口缩放压力测试。

基线和旧交付保留；本次未创建子智能体或提交 Git。
