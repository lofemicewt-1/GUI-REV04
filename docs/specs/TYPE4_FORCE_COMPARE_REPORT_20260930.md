# AWP1221 Type-4 槽位应用与 GUI 开始检测差异诊断

日期：2026-09-30  
执行边界：只读源码核对、离线差异模拟、受控 COM14 只读快照；不修改产品源码、不烧录固件、不发送 `WREGW`、启动、停止、复位、`CFGAPPLY` 或 EEPROM 写入命令。

## 当前结论边界

源码和离线同输入比较已经证明两个入口不是同一组寄存器写入序列。离线合成寄存器镜像中，GUI 开始检测与 Type-4 应用的最终差异为 `0x05`、`0x06`、`0x0A`、`0x0E`、`0x12`、`0x1F`；完整数据在 [path_diff.json](../../outputs/type4_force_compare_20260930/path_diff.json)。这些值是用于区分代码路径的合成结果，不是当前 COM14 芯片快照。

“槽位按压力更大”目前仍是用户的主观现象，尚未得到力值或同一机械条件下的 ADC 曲线。因而本报告只把寄存器差异列为可证伪因素，不把任何单项差异宣称为唯一物理原因。

用户随后提供了 GUI 在同一器件上的“较轻”复现日志摘录。该摘录与 `slot_retry_snapshot.json` 的实板槽位快照形成了一组实际配置对比：真实不同的是 `0x05` 的采样更新时间 `8 us -> 4 us`（XOR `0x0400`）和 `0x12` 的全局 Debounce Time `0 -> 100`（写入值 `0x0064`）。阈值、负载、Zero、Return、ZPS/ZPS Detect、Sleep、Stage 位和波形引用在提供的日志中保持一致。完整对比数据见 [actual_hardware_compare.json](../../outputs/type4_force_compare_20260930/actual_hardware_compare.json)。

## 已核对的源码行为

GUI `start_detection()` 在 [pages/awp1221_page.py](../../pages/awp1221_page.py) 的 `_write_start_detection` 路径中按顺序执行：

1. 对 `0x1F` 做 bit3 RMW 门控；bit3 已置位时不再写。
2. Button Mode 逐阶段读取相关寄存器，并写 `0x08/09/0A/0C` 与 `0x0D/0E/0F/11`。
3. 读取并写 `0x05/0x06/0x12/0x1F`，其中 `0x05/0x06` 按 GUI 采样、Zero、Return、ZPS、Sleep 等当前控件编码，`0x12` 按当前全局 Debounce Time 编码，`0x1F` 按 ADC LDO 选择更新 bit2。
4. 最后写 `0x02=0x0001` 开始检测。

Type-4 的 `_capture_type4_model()` 只编码 Zero Time、Press/Release Return Bit Time、阈值、波形配方引用和阶段重复次数。`_restore_type4_model()` 回填后固定 Stage0 按下、Stage1 释放、500 us、互跳、12 位精度和 `debounce=False`。采样更新、ZPS、ZPS Detect、Sleep Sensitivity、Auto Serial、ADC LDO、全局 Debounce Time 等字段不在 Type-4 载荷内。

当前固件 [outputs/Without_GUI/awp1221.cpp](../../outputs/Without_GUI/awp1221.cpp) 的 `applyAwp1221Payload()` 则：

1. 整字写 `0x1F=0x000C`，再写 `0x02=0x0010` 停止。
2. 临时切换 `0x05` SRAM/描述符访问模式，重建保存的波形描述符，完成后恢复 `0x15` 与 `0x05` 访问模式。
3. 当前修正版合并载荷中的 `0x05[15:13]` Zero Time，同时清除运行时 `0x05[11:10]` 为 `00`（4 us）；`0x06[7:2]` 合并 Return Bit Time。`0x05` 其他位和 SRAM 模式访问行为保留。
4. 逐阶段写固定 500 us、固定 Stage0 按下/Stage1 释放、固定互跳、波形引用和重复次数，并清除 `0x09/0x0A/0x0E/0x0F` bit15；比较方向 bit12 保留，未激活项的精度 bit14 被置为 1。
5. 最后写 `0x02=0x0001` 开始检测。

Type-4 固件不写 `0x12`，也不根据 GUI 当前 ADC LDO 或高位控件重写未保存 ADC 字段；`0x1F=0x000C` 会同时固定 bit2 和 bit3，并清除该寄存器其余原值。当前修正版额外固定 Type-4 运行时采样为 4 us，但不新增 EEPROM 字段，旧 Type-4 记录仍按原格式读取。`CFGREAD`、保存、设为活动与硬件应用是不同动作；硬件应用才进入上述驱动。

## 合成同输入差异（修正前基线）

差异脚本使用真实 GUI 编码/写入方法和固定初始镜像（`0x05=0xEFBD`、`0x06=0xDFFF`、四个阈值项初始 `0xB59D`、`0x12=0xBEA5`、`0x1F=0x0700`），两阶段均使用 Type-4 回填模型的 500 us、互跳、12 位精度、Stage0 阈值 `0xFFF`、Stage1 阈值 `0x800`。下表记录采样修正前的离线合成结果；`path_diff.json` 不是修正后固件的实板结果，真实本轮差异应以实板对比表和修正报告为准。

| 寄存器 | GUI 开始检测 | 槽位应用 | 差异含义 |
|---|---:|---:|---|
| `0x05` | `0xEBBD` | `0xEFBE` | GUI 按当前采样/ZPS/Auto Serial/Zero 写；槽位只合并 Zero，并保留未保存位 |
| `0x06` | `0xCFD7` | `0xDFD7` | GUI 按当前 Return/Sleep 写；槽位只合并 Return，保留 Sleep 等未保存位 |
| `0x0A` | `0x359D` | `0x759D` | Stage0 非激活释放项：GUI 保留原 bit14；槽位强制 bit14=1，同时均清 bit15 |
| `0x0E` | `0x159D` | `0x559D` | Stage1 非激活按下项：同上 |
| `0x12` | `0xBC64` | `0xBEA5` | GUI 写当前全局 Debounce Time；槽位保持原值 |
| `0x1F` | `0x070C` | `0x000C` | GUI RMW 保留高位并按 ADC LDO 设置 bit2；槽位整字固定 000C |

这组历史合成差异用于定位原始代码路径，不能覆盖修正后 Type-4 的 `0x05[11:10]=00` 策略，也不能单凭软件镜像判断机械按压力。

## 真实串口采集记录

脚本 [read_snapshot.py](../../outputs/type4_force_compare_20260930/read_snapshot.py) 使用 `PreserveLinesSerial`，只修改 COMMTIMEOUTS，不主动调用 `SetCommState` 或 DTR/RTS 控制。打开 COM14 后先持有连接，检测 READY/异步帧；只有人工恢复槽位并在 stdin 输入 `SLOT_RESTORED` 后，才发送以下只读命令：

`CAP?`、`CFG?`、`CFGREAD 0`、`CFGREAD 1`、`CFGREAD 2`，以及对发现的 Type-4 器件地址读取 `0x05`、`0x06`、`0x08..0x12`、`0x15`、`0x16`、`0x1F` 的 `RREGW ... 16 1`。

此前无效记录 [slot_snapshot.json](../../outputs/type4_force_compare_20260930/slot_snapshot.json) 在收到 `READY REV04` 后至少准备/尝试了只读 `CAP?`，但没有得到有效响应，因而没有硬件快照。第二次持有连接在人工输入 `SLOT_RESTORED` 后于 `ClearCommError` 处收到 Windows `PermissionError(13, 拒绝访问)`，输出为 `cap:null`、空槽位和空器件寄存器，仍没有有效硬件快照。若再次出现 READY 或句柄失效，只能记录为端口/设备重连事实，不能把重启前状态与重启后读数直接比较。

随后在现有授权下重新保持 COM14 连接，用户在新的稳定连接上再次按板载槽位按钮恢复“较费力”状态，脚本才接收 `SLOT_RESTORED`。本次使用 `slot_retry` 标签，PTY `session_id=92237`，读取完成且 `error:null`；未发送启动、停止、复位、`CFGAPPLY`、EEPROM 写入或烧录命令。有效结果见 [slot_retry_snapshot.json](../../outputs/type4_force_compare_20260930/slot_retry_snapshot.json)。

## 实板 Type-4 槽位快照

本节是 COM14 实板读取结果，不是合成差异脚本的输出。快照返回 `CAP FLAG3`、`CFG ACTIVE=0 SLOTS=1`；活动槽位索引为 0，保存记录数为 1，器件地址为 `0x50`。槽位 0 的 Type-4 载荷为：`zero_time=5`、`press_return=4`、`release_return=4`、`peak=85.0`、`trough=12.0`、阈值 `[350,250]`、波形引用 `[0,0]`、事件重复 `[0,0]`；波形为 `kind=5`、`coarse=0`、`fine=0`、`offset=0`、`times=[8,8,8,60]`、`repeat=1`。

对器件地址 `0x50` 的白名单寄存器读取为：

| 寄存器 | 实板值 | 按字段解码的诊断要点 |
|---|---:|---|
| `0x05` | `0xA42E` | sample update `8 us`、Zero `32 ms`、ZPS=true、ZPS Detect=true |
| `0x06` | `0x0290` | Press/Release Return 均 `800 us`、Sleep=false |
| `0x08` | `0x3311` | Stage0 按下项 |
| `0x09` | `0x615E` | 低 12 位阈值 `350`，bit15=0 |
| `0x0A` | `0x7800` | bit15=0 |
| `0x0B` | `0xC03F` | Stage0 配置项 |
| `0x0C` | `0x1818` | Stage1 释放项 |
| `0x0D` | `0x3322` | Stage1 配置项 |
| `0x0E` | `0x47FF` | bit15=0 |
| `0x0F` | `0x50FA` | 低 12 位阈值 `250`，bit15=0 |
| `0x10` | `0xC03F` | Stage1 配置项 |
| `0x11` | `0x1818` | Stage1 配置项 |
| `0x12` | `0x0000` | 全局 Debounce Time 当前为 0 |
| `0x15` | `0x01FE` | 实板只读状态 |
| `0x16` | `0x1221` | 实板只读状态 |
| `0x1F` | `0x000C` | ADC LDO/load `160 uA`，同时保留 gate 位 |

实板值显示：当前槽位保存的两个阈值与 `0x09/0x0F` 低 12 位一致；四个 Stage 项 `0x09/0x0A/0x0E/0x0F` 的 bit15 均为 0；全局 `0x12` 为 0，`0x1F` 为 `0x000C`。这确认了本次“较费力”状态下的槽位载荷和芯片寄存器内容，但它仍然只是槽位路径的一次状态快照，不能单独证明机械按压力的唯一原因。

## 实板槽位与 GUI 开始检测对比

下表只使用实板槽位快照和用户提供的 GUI 日志摘录。日志不是完整原始串口日志；其中 `RDATAW` 是 GUI 写入前的寄存器读回，`MCU OK WREGW` 是通讯板确认收到并执行写入命令的 ACK。提供的摘录没有在这些写入之后再次 `RDATAW` 读回，因此 GUI 列的写入值表示“软件请求且 MCU ACK”，不能等同于已经由第二次读回确认的最终芯片寄存器值。

| 寄存器/字段 | 槽位快照 | GUI 写入前读回 | GUI 写入值 / ACK | 实际差异 |
|---|---:|---:|---:|---|
| `0x05` sample update | `0xA42E` / 8 us | `0xA42E` / 8 us | `0xA02E` / ACK | 8 us -> 4 us，XOR `0x0400` |
| `0x06` Return/Sleep | `0x0290` | `0x0290` | `0x0290` / ACK | 相同；Press/Release 各 800 us，Sleep=false |
| `0x09` threshold | `0x615E` / low12=350 | `0x615E` / low12=350 | `0x615E` / ACK | 相同，bit15=0 |
| `0x0F` threshold | `0x50FA` / low12=250 | `0x50FA` / low12=250 | `0x50FA` / ACK | 相同，bit15=0 |
| `0x08/0A/0C/0D/0E/11` Stage | 与槽位相同 | 与槽位相同 | 与槽位相同 / ACK | Stage 位和波形引用相同 |
| `0x12` global debounce | `0x0000` / 0 | `0x0000` / 0 | `0x0064` / ACK | 0 -> 100 |
| `0x1F` ADC LDO/load | `0x000C` / 160 uA | `0x000C` / 160 uA | `0x000C` / ACK | 相同；不能用合成 `0x070C` vs `0x000C` 推测本次真实负载差异 |

GUI 日志还记录了最后的 `WREGW 50 02 16 0001` 及 MCU ACK，表示 GUI 随后启动检测；这条启动写入来自用户提供的日志，不是本次诊断执行者发送的命令。随后用户按单变量方案只把 GUI 采样更新时间从 `4 us` 改为 `8 us`，保持阈值 `350/250`、ADC LDO/load `160 uA` 和四个 debounce 使能位为 0，反馈为“变得与槽位一样费力”。这支持采样时间解释本次复现的按压力差异，但属于用户感受对照，不是标定力值测量，也不能直接推广到其他硬件。

若产品策略是让 Type-4 槽位应用也表现为 GUI `4 us` 的较轻状态，最小后续方案是在 Type-4 apply 中把 `0x05[11:10]` 固定为 `00`，同时保留 `0x05` 的其他位；这不需要新增 EEPROM 字节并保持记录兼容。本报告只记录该建议，本轮未修改产品源码。

## 验证证据

- `py -m unittest tests.test_awp1221_type4 -v`：17 项通过。
- `node tests/validate_type4_avr.js outputs/type4_debounce_off_20260930`：9 组编译 AVR 检查通过，包括 8 组波形/寄存器掩码、重复应用清除四个 debounce bit15、方向 bit12 与全局 `0x12` 保留、CFG/迁移/物理按键与 I2C 失败路径。
- `py outputs/type4_force_compare_20260930/compare_paths.py`：真实 GUI writer 方法的离线路径 trace 通过，生成 `path_diff.json`。
- AVR 模拟边界：I2C、EEPROM、UART、时间由 harness 模拟，没有打开 COM14，也没有烧录。

## 文件变更与待验证因果

诊断阶段新增/修改了 `read_snapshot.py`、`compare_paths.py`、`path_diff.json`、`slot_retry_status.json`、`slot_retry_snapshot.json`、`gui_light_log_excerpt_20260930.txt`、`actual_hardware_compare.json` 及本报告；后续采样修正另见 [TYPE4_SAMPLE_TIME_FIX_RESULT_20260930.md](TYPE4_SAMPLE_TIME_FIX_RESULT_20260930.md)，产品固件修改和交付物清单以该修正报告为准，工作区既有脏改动保留。

待验证：

1. GUI 日志中的写入值已有 MCU ACK，但尚缺写入后的 `RDATAW` 读回；因此 GUI 列目前是“软件请求/ACK”证据，不是完整的最终寄存器快照。
2. 本轮 A/B 已完成：仅把 GUI 采样更新从 `4 us` 改为 `8 us` 后，用户反馈按压力变得与槽位一样；这是本次复现的用户感受证据，不是标定力值。
3. 该 A/B 结果支持采样时间解释本次差异，但不能直接推广到其他硬件或替代力值/ADC 标定。
4. 软件写入 ACK 与写入后寄存器读回仍是两种不同证据；提供的 GUI 摘录只有写入前 `RDATAW` 和 MCU ACK，没有写入后的二次 `RDATAW`。

当前状态：源码、离线差异证据、实板槽位快照和本次单变量 A/B 均已完成。诊断已定位并实施 Type-4 运行时 4 us 采样修正；修正交付、编译和未烧录边界见 [TYPE4_SAMPLE_TIME_FIX_RESULT_20260930.md](TYPE4_SAMPLE_TIME_FIX_RESULT_20260930.md)。
