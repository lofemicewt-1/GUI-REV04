# Without_GUI.ino 清点（REV03 源，3328 行）

本文只记录现有行为，不改协议。重写前每一项都要过一遍。

## 1. 平台与常量

| 项 | 值 |
|---|---|
| MCU | Arduino Nano / ATmega328P（INT0、PCINT2、WDT、EEPROM） |
| 协议修订 | `FIRMWARE_PROTOCOL_REVISION = "REV04"` |
| 串口 | 115200，帧 `^` 开始 `~` 结束，缓冲 36 字节 |
| I2C | 默认 400kHz，超时 25ms，`setWireTimeout(..., true)` |
| WDT | setup 先关，boot preset 后再开 1s；波形写入循环内 `wdt_reset()` |
| nINT | D2 / INT0，FALLING，默认 DETECT |
| GPIO 公共脚 | D2–D5（GPIOCFG）；PCINT2 管 D3–D5 |
| 失败灯 | D7 |
| 物理预设键 | A0 / A7 / A6，模拟读 `<512` 为按下，去抖 35ms |
| 预设槽 | 3；V2 magic `0xA192`；槽大小 128；槽基址 EEPROM 256 |
| 上传暂存 | EEPROM 640 |
| 活动槽 | EEPROM 地址 0，`0xFF` = 无 |
| 192x runtime | 最多 3；地址 0x44/0x45/0x46 |
| FLAG cache | 3 槽；协议字 `FLAG3` |
| CFGDATA 块 | 最多 11 字节（Nano SRAM） |

签名表（PROGMEM）：

- 192x：`0x192A/B/C` → 0x44/45/46，ID 寄存器 0x1E 期望 0x5081，driver 192x，payload mask `0x05`
- 1018：`0x1018–0x101F` → 0x48–0x4F，ID 寄存器 0x2B 期望 0x1018，driver 1018，mask `0x12`
- 1221：签名都是 `0x1221`，地址 0x50–0x57，ID 寄存器 0x16 期望 0x1221，driver 1221，mask `0x00`

payload type：3=192x button runtime，4=1221 runtime（定义了但 apply 走 UNSUPPORTED），5=1018 ADC stage4。

## 2. 串口指令（`processCommand`）

帧内不含 `^~`。BOOTING 期间除 `PING` 外全部 `^ERR BOOTING~`。

### 2.1 系统

| 命令 | 语法 | 成功 | 失败 |
|---|---|---|---|
| PING | `PING` | `^PONG REV04 READY~` 或 `BOOTING`；清 `interruptRolloverResync` | FORMAT |
| I2CSPD? | `I2CSPD?` | `^I2CSPD <hz>~` | FORMAT |
| I2CSPD | `I2CSPD 100000\|400000` | 设 clock 并回显 | `I2CSPD_DENY` |

### 2.2 FLAG / 中断邮箱

| 命令 | 语法 | 成功 | 失败 |
|---|---|---|---|
| FLAG? | `FLAG?` | `^FLAG <0\|1> INTF=<0\|1> SEQ=<8hex>~`；无 pending 则 SEQ=0 | FORMAT |
| FLAG | `FLAG 0\|1` | 改 `BOARD_STATE_FLAG`，回 `^FLAG n~` | FORMAT |
| INTACK | `INTACK <8hex seq>` seq≠0 | `^OK INTACK <seq>~`，清 `interruptNotificationPending` | FORMAT / NONE / RESYNC / `STALE SEQ=<current>` |

### 2.3 FLAG3 状态缓存（与 192x runtime 独立）

| 命令 | 语法 | 成功 | 失败 |
|---|---|---|---|
| CAP? | `CAP?` | `^CAP FLAG3 SLOTS=3 SESSION=EXPLICIT 192=44-46:D2_SCAN 1221=50-57:D2_SCAN MON=EXPLICIT TGTCLR=GEN STAT=FLAG2 PUSH=EVSTAT~` | FORMAT |
| SESSION | `SESSION <8hex>` ≠0 | 相同则 `^SESSION SAME`；否则清全部 cache 并设 session | FORMAT |
| TGT | `TGT <slot\|ANY> <192\|1221> <2hex addr> <4hex gen>` gen≠0 | `^TGT OK` / `SAME` | FORMAT / UNSUPPORTED / SLOT / ADDR / GEN_ZERO / FULL / DUP / GEN_OLD / GEN_SAME |
| TGTCLR | `TGTCLR <slot> <4hex gen>` | `^TGTCLR OK` | FORMAT / SLOT / GEN_FORMAT / UNCONFIGURED / GEN_STALE |
| MON | `MON <slot> ON\|OFF` | `^MON OK <slot> <type> ON\|OFF~`；OFF 清 sample | FORMAT / SLOT / UNCONFIGURED / STATE |
| STAT? | `STAT?` | `^STAT <session8> <irq8> <3 records>~` | FORMAT |

记录格式：未配置 `U<slot>`；已配置 `D<slot>,<type>,<addr2>,<gen4>,<flags2>,<status4>,<sample8>,<irq8>,<last_good8>,<age8>,<err2>,<src2>`。age≥1000ms 标 STALE。

主动推送：`^EVSTAT FLAG3 <session> <irq> <3 records>~`，仅 scan==0 且至少一路 MON ON。ISR 不写串口。

地址约束：192 → 0x44–0x46；1221 → 0x50–0x57。1221 读寄存器 0x03，192 读 0x02。

cache flags：CONFIGURED 0x01，VALID 0x02，ERROR 0x04，STALE 0x08，NO_SAMPLE 0x10，SEQ_WRAP 0x20，MONITOR_ENABLED 0x40。

source：NONE 0，IRQ_02 1，REARM_02 2，1221_STATE 3，D2_SCAN 4。error：I2C_READ=1。

D2_SCAN 记录在 INTACK 前被 hold，同 seq 的 rescan 不得覆盖 scan0 事件。

### 2.4 CFG 预设

槽 0–2。上传必须整槽 128 字节。CFGDATA 每块 ≤11 字节 hex。CRC16-CCITT-FALSE（init 0xFFFF，poly 0x1021）。

| 命令 | 语法 | 成功 | 失败 |
|---|---|---|---|
| CFG? | `CFG?` | `^CFG ACTIVE=<n\|NONE> SLOTS=<hex mask>~` | — |
| CFGREAD | `CFGREAD <slot>` | `^CFGREAD <slot> <256 hex chars> CRC=<4hex>~`（整 128 字节 + 传输 CRC） | CFG_SLOT / CFG_UNSUPPORTED / CFG_EMPTY |
| CFGBEGIN | `CFGBEGIN <slot> 128 <crc hex>` | `^CFGBEGIN OK~`，清 staging 位图 | CFGBEGIN_FORMAT |
| CFGDATA | `CFGDATA <offset> <hex>` | `^CFGDATA OK~`，写 EEPROM 640+offset | CFGDATA_FORMAT |
| CFGEND | `CFGEND <slot>` | 位图满、CRC 匹配、staging 校验 V2 后原子写入槽（先擦 magic）`^CFGEND OK CRC=~` | CFGEND_FORMAT / CFG_CRC / CFG_UNSUPPORTED / CFG_FORMAT |
| CFGUSE | `CFGUSE <slot>` | 只写活动槽字节，不 apply。`^CFGUSE OK n~` | CFG_SLOT / CFG_UNSUPPORTED |
| CFGAPPLY | `CFGAPPLY <slot>` | probe ID + driver apply。192x 会 start DETECT。`^CFGAPPLY OK n~` | CFG_SLOT / UNSUPPORTED / DEVICE_MISSING / SIGNATURE_MISMATCH / CFG_FORMAT / CFG_APPLY_WRITE / CFG_VERIFY_MISMATCH（枚举有，当前 apply 路径未返回） |
| CFGDEL | `CFGDEL <slot>` | 槽填 0xFF；若是活动槽则活动槽改 0xFF。`^CFGDEL OK n~` | CFG_SLOT |

未知 CFG* → `CFG_UNKNOWN`。

V2 header 8B：magic u16，version u8=2，payload_type u8，chip_signature u16，device_addr u8，payload_length u8。随后 payload，再 2B CRC（覆盖 header+payload）。

写槽策略：先把目标 magic 写成 0xFFFF，拷 body，最后写回 magic。

### 2.5 寄存器桥

| 命令 | 语法 | 成功 | 失败 |
|---|---|---|---|
| RREGW | `RREGW <aa> <rr> <8\|16\|32> <count>` | `^RDATAW aa rr bits count <hexblob>~` | FORMAT / COUNT（总字节≤32）/ I2C + Wire 错误码 |
| WREGW | `WREGW <aa> <rr> <8\|16\|32> <hexblob>` | 先 `stopAwp192xRuntime(addr)`（失败则 I2C），再写。`^OK WREGW aa rr bits count~` | FORMAT / HEX / COUNT（8bit≤10，16bit≤5，32bit≤2）/ I2C |

地址 7bit，寄存器 8bit。读用 repeated start。写会拆掉该地址上的私有 192x runtime。

### 2.6 GPIO

GPIOCFG **只允许 D2–D5**。GPIO / GPIOR / GPIOU 走 `isAllowedGpioPin`：禁止 0/1/D2/D7/A4/A5/A0/A6/A7；允许 D3–D13 与 A0–A3 中未被禁止者（即 A1–A3；A0 被键占用所以禁止）。

| 命令 | 语法 | 成功 | 失败 |
|---|---|---|---|
| GPIOCFG | `GPIOCFG <pin> HIGH\|LOW\|DETECT\|HIZ` | `^GPIOCFG D<n> <MODE>~` | FORMAT / UNSUPPORTED |
| GPIOCFG 脉冲 | `GPIOCFG <pin> HIGH <count 1–255> <high_ms≥1> <gap_ms>` | 先配成 HIGH，再 loop 翻转 | FORMAT / UNSUPPORTED |
| GPIO | `GPIO <pin\|D#\|A#> 0\|1` 值按 **hex** 解析 | `^GPIO <原token> <0\|1>~` | GPIO_PIN_FORMAT / GPIO_VALUE / GPIO_PIN_DENY / GPIO_APPLY |
| GPIOR | `GPIOR <pin>` | 关该脚中断、设 INPUT、读。`^GPIOR token val~` | GPIOR_PIN_FORMAT / GPIO_PIN_DENY |
| GPIOU | `GPIOU <pin>` | 关中断、INPUT_PULLUP。`^GPIOU token~` | GPIOU_PIN_FORMAT / GPIO_PIN_DENY |

GPIOCFG 配任何模式会取消正在进行的脉冲（`gpioPulsePin=0`）。DETECT 失败若脚不支持中断。HIZ 大小写敏感（只认 `HIZ`）。

事件：`^GPIOEV GPIO<index> D<pin> SEQ=<8hex> LEVEL=<0\|1> SRC=INT0\|PCINT2 CHIP=BOARD~`。D2 同时推 GPIOEV 与 FLAG 邮箱。PCINT2 只记 **下降沿**。

## 3. 非命令输出

| 输出 | 时机 |
|---|---|
| `debug_flag` | setup 开头 |
| `^READY REV04~` | boot 完成、WDT 已开、READY 位置位 |
| `EEPROM_PRESET_OK/MISSING/SIGNATURE_MISMATCH/FAIL/EEPROM_PRESET_RESET_FAIL` | 上电活动槽 |
| `^EVSTAT FLAG3 ...~` | D2 scan0 且有 MON |
| `^GPIOEV ...~` | 公共 DETECT 脚下降沿 |
| `E <addr> <runtime_i> <irq8> <scan> <status\|----> <P\|R\|N\|E>` | 192x detect sidecar |
| `I <statusHEX> <isStage0> <isStage1> <action>` | 同上 |
| `INT I2C` | runtime 读 0x02 失败 |
| `I Detection watchdog <addr> R\|F` | rearm ADC_BUSY 超时 3s 后恢复成败 |
| `I Release timeout R` | WAIT_RELEASE 3s 未松开，强制回 WAIT_PRESS |

## 4. 上电 / loop

`setup`：清 MCUSR.WDRF → 关 WDT → Serial → D2 INPUT_PULLUP → Wire 400k + timeout → 关失败灯 → `applyBootPresetIfActive`（先 500ms 可服务串口，reset 所有已存预设目标 `0x01=0x5283`，再对活动槽最多 10 次 apply，间隔 100ms）→ 初始化按键状态 → `attachFixedInterrupt`（D2 DETECT）→ WDT 1s → READY → `^READY REV04~`。

boot 等待期间 `serialEvent()` 可跑，但除 PING 外命令回 BOOTING。

`loop`：WDT 喂狗 → 若 runtimeInterruptPending：handleInterrupt(seq, scan)；scan0 可 EVSTAT；若 D2 仍为 LOW 且 DETECT 且 rescan<2，则再挂 pending（同 seq）；否则清 rescan → serviceGpioEvents → GPIO 脉冲翻转 → 192x rearm/release 定时器 → serialEvent → scanPresetButtons。

物理键：稳定按下边沿 `activatePreset(slot)`，失败点亮 D7，成功灭灯。不写活动槽。

## 5. AWP192x 私有 runtime

来源：仅 EEPROM CFG apply / boot / 物理键。串口无 START/STOP 命令。WREGW 对该地址会 stop+清槽。

相位：WAIT_PRESS=0，WAIT_RELEASE=1，REARM_WAIT_PLAY_IDLE=2，REARM_WAIT_ADC_BUSY=3。

模式：STOP=0，DETECT=1，PLAY=2。apply 后 start DETECT。

status 0x02：bit0 ADC_BUSY，bit14 ON_PLAY。press 看 0x0800，release 看 0x0200。

`switchCfg`：写 0x0B/0x0F/0x11 后 `0x06=0x0001` ADC_Go。WAIT_RELEASE 时 event=`(wait_press & 0x0300)|0x0009`，press 清 bit15，release 置 bit15。

rearm：先等 ON_PLAY 清（无超时），再 ADC_Go，再等 ADC_BUSY；ADC 阶段 3s watchdog，超时则 reset `0x01=0x5283` + 重放 EEPROM 波形 + 再 ADC_Go。

release 超时硬编码 3s。

stop：`0x06=0x0002` 停检测 + `0x06=0x0008` 停播放。任一失败则保留 runtime 槽。

payload v2 118B：version=2，flags 仅允许 SERIAL_CONFIG_VALID=0x01，waveform 1–8。寄存器映射见 `writeAwp192xButtonPayloadV2FromEeprom`。波形在 MCU 上展开 sinc/sine → DAC 码，经 0x16 索引 + 0x00 SRAM 流式写入。电压范围 ±95000mV，DAC 零点 0x0800/0x07FF。

## 6. AWP1018 ADC preset

schema v2。control：bit7=0 含 stage2/3（22 寄存器），=1 仅 stage01（14 寄存器）。descriptor 1–8，每条 9 字节（slice + 4×u16）。寄存器 0x30 必须 0x2100。写 SRAM 描述符前把 0x05 的低 2 位置 1，写完恢复 0x05/0x28，再按表写寄存器，最后 `0x02=0x0001`。

## 7. 函数清单（全部需过）

### 串口/解析

`print_hex4` `print_hex_width` `print_hex8` `print_command_err` `noTrailingToken` `parseExactHexU32` `parse_hex_u32` `parse_dec_u32` `print_err` `isHexBlob` `decodeCfgDataHex` `processCommand` `handleCfgCommand` `serialEvent`

### I2C

`write_single` `read_single_safe` `registerWidthBytes` `readRegisterBlob` `writeRegisterBlob`

### 192x runtime

`stopAwp192xActions` `resetAwp192xChip` `awp192xRuntimeAddress` `setAwp192xRuntimeAddress` `findAwp192xRuntime` `reserveAwp192xRuntime` `clearAwp192xRuntimeSlot` `stopAwp192xRuntime` `switchCfg` `queueAwp192xDetectionRearm` `rearmPollDue` `serviceAwp192xDetectionRearm` `recoverAwp192xDetectionRearm` `printAwp192xRuntimeSidecar` `processAwp192xRuntimeStatus` `handleInterrupt` `serviceAwp192xRuntimeTimers` `readAwp192xPayloadWord` `readAwp192xPayloadDword` `readAwp192xWaveformRecipeV2` `signExtendAwp192x` `decodeAwp192xWaveformRecipeV2` `validateAwp192xButtonPayloadV2FromEeprom` `totalAwp192xWaveformV2Points` `smoothSegmentMv` `mapMillivoltsToDac` `awp192xWaveformV2PointMv` `readAwp192xRuntimeBase` `writeAwp192xButtonPayloadV2FromEeprom` `applyAwp192xButtonPayloadV2FromEeprom` `startAwp192xRuntime`

### FLAG cache

`isFlagStatusAddressForType` `parseFlagStatusType` `printFlagStatusType` `flagStatusInterruptSequence` `findFlagStatusSlotByAddress` `findFlagStatusEmptySlot` `findFlagStatusCache` `clearFlagStatusSample` `configureFlagStatusCache` `clearFlagStatusCache` `clearAllFlagStatusCaches` `hasEnabledFlagStatusMonitorTarget` `recordFlagStatusAttempt` `flagStatusSourceCarriesIrqSequence` `flagStatusEventCacheHeld` `recordFlagStatusSuccess` `recordFlagStatusFailure` `recordFlagStatusSuccessForAddress` `recordFlagStatusFailureForAddress` `printFlagStatusRecord` `printFlagStatusSnapshot` `printFlagStatusEventSnapshot` `printFlagStatusCapabilities` `handleFlagStatusTargetCommand` `handleFlagStatusClearCommand` `handleFlagStatusSessionCommand` `handleFlagStatusMonitorCommand`

### GPIO / ISR

`gpioPinIndex` `gpioPinSupportsDetect` `clearGpioEventMailbox` `disableGpioInterrupt` `enableGpioInterrupt` `configureGpioMode` `writeSharedDigitalOutput` `setPresetFailureLed` `isAllowedGpioPin` `parse_pin_token` `parseGpioModeToken` `printGpioModeToken` `recordGpioEventFromIsr` `blink0` `ISR(PCINT2_vect)` `attachFixedInterrupt` `serviceGpioEvents`

### EEPROM / preset / 1018

`crc16CcittFalseUpdate` `crc16FromEepromRange` `resetCfgUpload` `cfgUploadStagingAddress` `v2PresetSlotAddress` `loadSignatureDefinition` `findSignatureDefinition` `isAwp1018Signature` `isAwp192xSignature` `readAwp1018PayloadWord` `readAwp1018AdcPayloadLayout` `validateAwp1018AdcPayloadFromEeprom` `applyAwp1018AdcPreset` `presetTarget` `validateV2PresetAt` `readV2StoredPreset` `readV2StoredPresetStatus` `readLogicalPreset` `printHex2` `printLogicalPreset` `writeV2StoredPresetFromStaging` `readPresetEnvelope` `validatePresetEnvelope` `probePresetTarget` `applyPresetWithDriver` `applyLogicalPreset` `activatePreset` `delayWithBootSerialService` `applyLogicalPresetWithDelay` `isPresetButtonPressed` `handlePresetButtonApplyFailure` `applyPresetButtonSlot` `initPresetButtons` `scanPresetButtons` `resetConfiguredTargetsForBoot` `applyBootPresetIfActive` `printCfgSummary` `isValidPresetSlot`

### 入口

`setup` `loop`

辅助结构：`findD2StatusRead` `appendD2StatusRead`

## 8. 已知现状（提问时要过，不是现在改）

1. payload type 4（1221 runtime）只定义，validate 返回 UNSUPPORTED。
2. `PRESET_APPLY_VERIFY_MISMATCH` 有错误字符串，apply 路径未产生该结果。
3. GPIO 值用 hex 解析；GPIOCFG 的 HIZ 大小写敏感。
4. GPIOCFG 管 D2–D5；GPIO/GPIOR/GPIOU 禁止 D2。
5. GPIOR 会 detach 中断并把脚改成 INPUT，可能拆掉 DETECT。
6. 1221 FLAG 读 0x03，但 CAP 文案写 `1221=50-57:D2_SCAN`。
7. 串口无 192x runtime 生命周期命令；WREGW 会停 runtime。
8. 物理按键 apply 成功不更新 EEPROM 活动槽。
9. boot reset 对所有已存槽目标写 `0x01=0x5283`（含 1018/1221 地址，只要 probe 过）。
10. sidecar / watchdog / EEPROM_PRESET_* 不是 `^...~` 帧。
