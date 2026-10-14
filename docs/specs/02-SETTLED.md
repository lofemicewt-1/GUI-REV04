# 已拍板

## 第 1 轮

| 项 | 决定 |
|---|---|
| 兼容 | **B**：GUI REV04 实际用到的指令保持；其余可清 |
| 硬件 | **A**：Arduino Nano ATmega328P |
| 源码 | **B**：多文件 `.ino` + `.h/.cpp`，Arduino 能编 |
| 行为 | **C**：每条指令单独决定克隆还是修 |
| 流程 | **A**：指令 → 192x runtime → 1018/EEPROM → GPIO/ISR → boot/loop |

## GUI 实际用到的线协议（事实，不必再问）

必须保持：`PING`/`PONG REV04 READY|BOOTING`、`^READY REV04~`、BOOTING 锁、`CAP?`、`SESSION`、`TGT`、`TGTCLR`、`MON`、`FLAG?`、`FLAG`、`INTACK`、`I2CSPD?`、`I2CSPD`、全部 `CFG*`、`RREGW`/`^RDATAW`、`WREGW`/`^OK WREGW`、`GPIOCFG`（含脉冲）、`GPIO`、`GPIOR`、`GPIOU`、`^EVSTAT FLAG3`、`^GPIOEV`。

GUI 不解析、可清候选：`debug_flag`、sidecar `E …`/`I …`、`INT I2C`、`I Detection watchdog`、`I Release timeout R`、`EEPROM_PRESET_*` 散行。

## 第 2 轮（部分）

| 项 | 决定 |
|---|---|
| RX 缓冲 | **A**：保持 36 |
| 注释 | **A**：中文；每条指令/函数都写。AVR 注释在编译期丢掉，**不占 Flash/SRAM**；占空间的是 `Serial.print` 字符串，应用 `F()` |
| 错误帧 | **C**：统一 `^ERR <CMD> <REASON>~`，并改 GUI 解析 |
| `debug_flag` | **A**：删除 |
| 握手 | **无 PING**。boot 静默；完成后只发 `^READY REV04~`。GUI 等到该帧即连上（超时 30s）。删 PING/PONG/BOOTING/`debug_flag` |
| 回绕旗子 | **SESSION 的 OK 与 SAME 都清 `interruptRolloverResync`** |
| SESSION 失败 | **A**：仍 `^ERR SESSION FORMAT~` |
| CAP? | 回包改为 `^CAP FLAG3~`，GUI 认 FLAG3 即启用 EVSTAT |
| FLAG? | **B**：`^FLAG INTF=<0\|1> SEQ=<8hex>~`，无软件位；改 GUI |
| INTACK | **B**：成功仍 `^OK INTACK <seq>~`；STALE 改为 `^ERR INTACK STALE~` 不带 SEQ；改 GUI |
| STAT? | **B**：删除；只靠 EVSTAT；改 GUI（**已完成 2026-10-14**：固件已删除，GUI 已移除 STAT? 与 FLAG 轮询死代码） |
| EVSTAT | **A**：帧与 INTACK-hold 规则克隆 |
| TGT/TGTCLR/MON | **B**：成功帧与规则克隆，但删除 `ANY`；槽号必须 0–2 |
| I2CSPD | **A**：只允许 100000/400000 |
| RREGW | 删除 32 位，只留 8/16；count 与回包格式其余克隆；改 GUI |
| WREGW 停 runtime | **要停** |
| RREGW I2C 错误 | **A**：保留 `^ERR RREGW I2C <Wire码>~` |
| WREGW 位宽 | **A**：与 RREGW 一样只留 8/16，删 32 位；改 GUI |
| GPIOCFG 脉冲 | **删除**；GUI 1221 `start_pulse` 一并删 |
| GPIO 命令 | **Q2-A**：只留 `GPIOCFG`（HIGH/LOW/HIZ/DETECT/PULLUP，DETECT 仅 D2–D5）+ `GPIOR`（只读不改模式）。脚：D2–D13、A1–A3。删 `GPIO`/`GPIOU` |
| GPIOEV | **A**：帧克隆 |
| ADC | 新命令 `ADCR A1\|A2\|A3` → `^ADCR A1 <0-1023>~`；仅 A1–A3 |
| 读频 | 仅 D8 / Timer1 ICP。`FREQ?` 回周期微秒，GUI 换算 Hz。帧 `^FREQ D8 <us>~` |
| CFG 族 | 成功帧克隆；错误 `^ERR <具体命令> <REASON>~`（如 `CFGREAD SLOT`） |
| payload type 4 | **不做** |
| EEPROM_PRESET_* 散行 | **A**：删除 |
| 物理预设键 | **A**：按下立即 apply，不写活动槽；失败亮 D7 |
| 192x 生命周期 | **A**：无串口 start/stop；仅预设路径创建；WREGW 先 stop |
| 192x 状态机 | **A**：克隆（3s 松开超时；以后是否可配另谈） |
| 检测看门狗 | **A**：3s 复位+重放波形；删除散行日志 |
| sidecar E/I | **A**：删除 |
| 192x RAM 槽数 | **最多 2**；EEPROM 预设仍 3；FLAG 监视仍 3 |
| 192x 波形生成 | **A**：PROGMEM 256 点 Q15 cosine LUT，去掉 float/libm；配方线格式不变 |
| 1018 apply | **B**：行为 1:1，细节不另改 |
| 上电复位 | 192x / 1018 / **1221** 均为 probe 成功后 `0x01=0x5283` |
| 1018 runtime | **无**。apply 是一次性写寄存器，loop 里没有状态机，不做 |
| 1221 runtime / type4 | **不做**。CFG 仍 UNSUPPORTED；1221 只用 FLAG 监视 |
| 上电时序 | **A**：500ms + 最多 10×100ms；boot 静默 |
| WDT | **A**：1s，loop 与波形写入喂狗 |
| 文件拆分 | **A**：`Without_GUI.ino` + protocol/i2c/gpio/preset/awp192x/flag_status/adc_freq |
| boot 未就绪命令 | **A**：静默丢，无回包 |
| 错误 CMD 名 | **A**：用具体命令字 |
| D2 rescan | **A**：nINT 仍低则最多再扫 2 次；EVSTAT 仅 scan0 |
