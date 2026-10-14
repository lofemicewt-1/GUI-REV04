# Type-4 仅保存 SIN / SINC

后续更新：Stage0/1 debounce 现为应用 Type-4 时固定关闭；最新 GUI/固件和哈希见 [AWP1221_TYPE4_DEBOUNCE_OFF.md](AWP1221_TYPE4_DEBOUNCE_OFF.md)。下文保留本次保存范围调整的交付快照。

日期：2026-09-30。

## 本次确认与实施计划

用户要求新保存的 Type-4 仅包含 SIN、SINC，其它波形不保存，并评估是否节省空间。进一步确认：**保持现有 Type-4 记录兼容**。

1. 保留现有载荷版本 1、21 字节头、14 字节配方、256 字节物理槽及线上记录。
2. 新保存允许独立 SIN（kind 0）、三段 SINC（kind 4）、Serial3/4 的 SIN＋SINC（kind 5）。引用 Gauss、Fixed-slope Ramp、DC 或原始 RAM 时，拒绝整个保存，说明对应波形；不生成缺少阶段引用的记录。
3. 旧 Type-4 仍能读取、应用和回填，包括已保存的 kind 1..3；直接 CFG 上传同样执行新保存限制。Type-3/5 不变。
4. 同步 GUI、模块固件与单文件固件，进行格式兼容、旧记录回读/应用、新上传拒绝、SIN/SINC 波形和迁移测试，再比较 Nano Flash/RAM 及 EEPROM 字节数。
5. 使用当前模型直接实施，不创建子智能体。基线备份：`outputs/type4_sin_sinc_20260930/baseline`。

## 空间预期

保持格式兼容时，SIN/SINC 单配方载荷仍为 35 字节，两配方为 49 字节；物理槽和传输记录均为 256 字节。既有 Gauss/Ramp/DC 通过同一 ROM 分支的种类位重建，没有独立实现函数可删除；兼容旧记录时不能移除此分支。最终以编译数据为准，不预先声称节省 Flash。

## 验收结果

已完成。新保存支持 kind 0、4、5；GUI 在发送前给出对应 slice 与被排除波形的提示，固件在 RAM 暂存校验时拒绝 kind 1..3，因此旧 GUI 或直接 CFG 上传也不能绕过。旧 EEPROM 中的 kind 1..3 仍可读取、应用、回填；回填后如需再次保存，须改为 SIN/SINC。排除任一阶段波形时拒绝整条记录，原槽不被覆盖。

### 实测空间

| 项目 | 更新前 | 更新后 | 差异 |
| --- | ---: | ---: | ---: |
| Nano Flash | 30,200 B | 30,220 B | +20 B |
| Nano 静态 RAM | 1,212 B | 1,212 B | 0 |
| 单配方 Type-4 载荷 | 35 B | 35 B | 0 |
| 两配方 Type-4 载荷 | 49 B | 49 B | 0 |
| 每个物理 EEPROM 槽 / Type-4 传输记录 | 256 B | 256 B | 0 |

**此兼容方案没有节省空间。** 新增 20 字节是 MCU 端的新保存筛选逻辑；旧 Gauss/Ramp/DC 的重建功能共用 ROM 分支，保持旧记录兼容时仍需保留。原始样点从来不保存，限制允许的波形名称也不会缩短现有定长配方。

模块版与单文件版均为 30,220 / 30,720 B Flash、1,212 / 2,048 B 静态 RAM。目标保持 `arduino:avr:nano:cpu=atmega328old`。

### 交付

- 当前单文件固件：`outputs/Without_GUI_Single/Without_GUI_Single.ino`。
- 当前模块固件：`outputs/Without_GUI/Without_GUI.ino` 及同目录模块。
- 新 GUI：`outputs/type4_sin_sinc_20260930/gui/AnalogwinPiezoGUI.exe`；旧 EXE 没有新的保存前提示。
- 单文件产品 HEX：`outputs/type4_sin_sinc_20260930/firmware_single/Without_GUI_Single.ino.hex`。
- 模块产品 HEX：`outputs/type4_sin_sinc_20260930/firmware_modular/Without_GUI.ino.hex`。
- 文件哈希清单：`outputs/type4_sin_sinc_20260930/delivery_sha256.json`。

单文件 `.ino` SHA-256：`BD053D2254214D9DE149801C92D0F3113CE32DA03498E2A6457AA3DAAFDD181D`。

单文件 sketch HEX SHA-256：`0DF30DFF6611A87B2DBAD7EAD7459C48115D071332BA07D52DCB2BD91E0C97B9`。

GUI SHA-256：`22F5E08A17F848A42DED273836F221D24EA336A2197CC9A9EB51CF3700494B62`。

### 验证与边界

- 159 项 AWP 相关回归通过，其中 14 项 Type-4 检查；涵盖独立 SIN、Serial1/2 SINC、Serial3/4 SIN＋SINC、旧 ROM 记录、阶段引用、GUI 未应用输入和保存限制。证据：`python_tests.txt`、`type4_tests.txt`。销毁测试 Tk 窗口时有既有 ThemeChanged 异步提示，断言和退出码均通过。
- 8 组允许波形的载荷与更新前编码器逐字一致；旧 Gauss/Ramp/DC 的载荷和描述符与基线一致。17 个模块文件全部纳入单文件，除本地 include 外，内容逐字一致。证据：`source_validation.json`。
- 模块版、单文件版分别重新编译并运行 AVR 指令模拟：8 组波形（含 Serial3/4）与当前 Python 描述符相符；旧 kind 1..3 读取/应用通过，新上传拒绝且 EEPROM 无变化；137 个迁移变更字节断电点、8 种旧槽占用组合、128/256 字节传输、CRC/非法输入、活动槽/板载按键及 I2C 失败恢复通过。证据：`avr_validation_result.json`、`single_validation/avr_validation_result.json`。
- Nano 模块版和单文件产品编译均通过。证据：`firmware_modular_compile.txt`、`firmware_single_compile.txt`。
- 新 GUI 打包、嵌入新保存校验及基本启动检查通过：窗口显示、响应、干净关闭，在无关工作目录及失效 Python/Tcl 环境变量条件下运行。证据：`exe_build.log`、`exe_embedded_check.txt`、`exe_launch_check.txt`。本次没有重复既有缩放压力测试，基本启动不等于完整 UI 或实板验收。
- 没有连接真实串口、写入真实 EEPROM 或烧录。AVR 模拟替换 I2C、EEPROM、UART 和时间外设，断电按字节写完成后切断，不代表实机电压异常试验。

上述证据文件均位于 `outputs/type4_sin_sinc_20260930`。旧交付、开始时备份和工作区其它修改保留；未提交 Git。
