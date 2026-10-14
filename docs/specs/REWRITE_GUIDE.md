# Without_GUI 重编指南

源：`GUI REV03/outputs/Without_GUI/Without_GUI.ino`（3328 行）  
输出：`GUI REV04/outputs/Without_GUI/`  
硬件：Arduino Nano，ATmega328P 或同排针 328PB  
协议字：`REV04`  
未确认本文件前不写固件。

注释：中文，每条指令/函数都写。注释不占 Flash；`Serial.print` 用 `F()`。

---

## 1. 源码拆分

全部在本目录，Arduino 能直接编：

| 文件 | 职责 |
|---|---|
| `Without_GUI.ino` | `setup` / `loop` / WDT |
| `protocol.h/.cpp` | 帧、解析、命令分发、错误帧 |
| `i2c.h/.cpp` | Wire 读写、`RREGW`/`WREGW`、I2CSPD |
| `gpio.h/.cpp` | GPIOCFG / GPIOR / GPIOEV / ISR |
| `adc_freq.h/.cpp` | `ADCR`、`FREQ?` |
| `flag_status.h/.cpp` | SESSION/TGT/MON/CAP/EVSTAT/FLAG?/INTACK |
| `preset.h/.cpp` | EEPROM CFG、按键、上电 apply |
| `awp192x.h/.cpp` | 192x payload、波形、runtime |

RAM：192x runtime **最多 2**；EEPROM 预设 **3**；FLAG 监视 **3**。

---

## 2. 帧与握手

- 命令帧：`^` … `~`，缓冲 **36** 字节。超长：`^ERR <已识别CMD或CMD> TOO_LONG~`（收包中尚无命令名则 `^ERR CMD TOO_LONG~`）。
- **删除** `PING` / `PONG` / `BOOTING` / `debug_flag`。
- boot：不解析命令、无回包。preset 做完后只发 **`^READY REV04~`**。
- GUI 连接（避免丢掉唯一的 READY）：
  1. 打开串口。Arduino 通常 DTR 复位，MCU 会重新 `setup` 再发一次 READY。
  2. **不要**先盲等 2s 再 `reset_input_buffer()`。开串口后立即组帧，最多等 30s 听 `^READY REV04~`。
  3. 若适配器不拉 DTR、板子早已 READY：30s 内收不到 READY。此时发一次 `CAP?`；已就绪会回 `^CAP FLAG3~`，视为连上。boot 未完成则静默无回包，可再等 READY。
  4. 固件 **不重发** READY。重连只靠 DTR 复位后的那一帧，或已就绪后的 `CAP?`。
- 错误一律 **`^ERR <CMD> <REASON>~`**，CMD 用具体命令字。可选第三段仅 `RREGW` 的 Wire 数字：`^ERR RREGW I2C 7~`。
- 未知命令：`^ERR UNKNOWN CMD~`（或 `^ERR <token> UNKNOWN~`，实现时固定一种并改 GUI）。推荐 **`^ERR UNKNOWN CMD~`**。

---

## 3. 指令表

### 删除

`PING`，`FLAG` 写入，`STAT?`，`GPIO`，`GPIOU`，GPIOCFG 脉冲参数，`debug_flag`，sidecar `E`/`I`，`INT I2C`，`I Detection watchdog`，`I Release timeout`，`EEPROM_PRESET_*`。

### 保留（成功帧，除非下表改过）

#### `CAP?`

- 入：无参数
- 出： **`^CAP FLAG3~`**
- GUI：认到 `FLAG3` 即启用 EVSTAT，不再匹配整句广告

#### `SESSION <8hex≠0>`

- 新 id：清全部 FLAG cache，设 session，**清 `interruptRolloverResync`**，`^SESSION OK <8hex>~`
- 同 id：不清 cache，**仍清回绕旗子**，`^SESSION SAME <8hex>~`
- 失败：`^ERR SESSION FORMAT~`
- 不碰 192x runtime

#### `TGT <0-2> 192|1221 <aa> <gen4≠0>`

- **删除 ANY**。槽必须 0–2
- 地址：192 → 44–46；1221 → 50–57
- generation 单调；同参同 gen → `SAME`；否则 `OK`
- 失败：`FORMAT` / `UNSUPPORTED` / `SLOT` / `ADDR` / `GEN_ZERO` / `FULL` / `DUP` / `GEN_OLD` / `GEN_SAME`
- 出：`^TGT OK|SAME <slot> <type> <aa> <gen>~`

#### `TGTCLR <slot> <gen4>`

- 出：`^TGTCLR OK <slot> <gen>~`
- 失败：`FORMAT` / `SLOT` / `GEN_FORMAT` / `UNCONFIGURED` / `GEN_STALE`

#### `MON <slot> ON|OFF`

- OFF 清该槽采样，登记保留
- 出：`^MON OK <slot> <type> ON|OFF>~`
- 失败：`FORMAT` / `SLOT` / `UNCONFIGURED` / `STATE`

#### `FLAG?`

- 出：**`^FLAG INTF=<0|1> SEQ=<8hex>~`**（无软件位）
- 无 pending：INTF=0，SEQ=00000000
- 有 pending：INTF=1，SEQ=当前序号（≠0）

#### `INTACK <8hex≠0>`

- 成功：`^OK INTACK <seq>~`，清 pending，放行 EVSTAT hold
- `^ERR INTACK NONE|RESYNC|FORMAT|STALE~`（**STALE 不带 SEQ**）

#### `I2CSPD?` / `I2CSPD 100000|400000`

- 默认 400kHz，超时 25ms
- 出：`^I2CSPD <hz>~`
- 非法：`^ERR I2CSPD DENY~`

#### `RREGW <aa> <rr> 8|16 <count>`

- **删除 32 位**
- 总字节 ≤32；8bit count≤32，16bit count≤16（与现 GUI 上限一致，但宽度无 32）
- 出：`^RDATAW aa rr bits count <hex>~`
- 失败：`FORMAT` / `COUNT` / `I2C <n>`

#### `WREGW <aa> <rr> 8|16 <hexblob>`

- **删除 32 位**；count 8bit≤10，16bit≤5
- 写之前对该地址 **`stopAwp192xRuntime`**（失败整条 `I2C`）
- 出：`^OK WREGW aa rr bits count~`

#### `GPIOCFG <pin> HIGH|LOW|DETECT|HIZ|PULLUP`

- 脚：D2–D13、A1–A3。禁：D0/D1/D7/A0/A4/A5/A6/A7
- DETECT **仅 D2–D5**，否则 `UNSUPPORTED`
- **无脉冲参数**
- 出：`^GPIOCFG D<n> <MODE>~`（A 脚回 `A1` 形式，实现与 GUI 对齐：数字脚 `D5`，模拟 `A1`）
- 配模式时取消该脚旧中断；不破坏其它脚

#### `GPIOR <pin>`

- 同上脚集合
- **只读，不改模式、不拆 DETECT**
- 出：`^GPIOR <原token> <0|1>~`

#### `ADCR A1|A2|A3`

- 10bit，AVCC
- 出：`^ADCR A1 <0-1023>~`
- 其它脚：`^ERR ADCR PIN~`

#### `FREQ?`

- 仅 D8 + Timer1 ICP1。测**相邻同向沿的一个周期**（µs），GUI 换算 Hz。
- 开始：保存 D8 当前 GPIO 模式，改为输入；结束：**无论成败都恢复**原模式。
- 等待上限 **100ms**（无第二沿 → `^ERR FREQ RANGE~`）。Timer1 溢出或周期折算后为 0 同样 RANGE。
- 量程：约 10Hz～几十 kHz（100ms 超时决定下限；ICP 捕捉决定上限）。回整数微秒。
- 等待期间必须 `wdt_reset()`，并继续 `serviceAwp192xRuntimeTimers` / 已挂起的 D2 邮箱（可短轮询，禁止空转 100ms 不理 runtime）。
- 出：`^FREQ D8 <period_us>~`。占用 Timer1，D9/D10 PWM 不可用。

#### CFG 族（成功帧克隆）

| 命令 | 成功 |
|---|---|
| `CFG?` | `^CFG ACTIVE=<n\|NONE> SLOTS=<hex>~` |
| `CFGREAD <slot>` | `^CFGREAD <slot> <256 hex> CRC=<4hex>~` |
| `CFGBEGIN <slot> 128 <crc>` | `^CFGBEGIN OK~` |
| `CFGDATA <off> <hex≤11B>` | `^CFGDATA OK~` |
| `CFGEND <slot>` | `^CFGEND OK CRC=<4hex>~` |
| `CFGUSE <slot>` | `^CFGUSE OK <n>~`（只写活动槽） |
| `CFGAPPLY <slot>` | `^CFGAPPLY OK <n>~` |
| `CFGDEL <slot>` | `^CFGDEL OK <n>~` |

失败示例：`^ERR CFGREAD SLOT~`、`^ERR CFGAPPLY DEVICE_MISSING~`、`^ERR CFGEND CRC~`。REASON 用原后缀（`SLOT`/`FORMAT`/`UNSUPPORTED`/`EMPTY`/`DEVICE_MISSING`/`SIGNATURE_MISMATCH`/`APPLY_WRITE`/`VERIFY_MISMATCH`）。未知 CFG：`^ERR CFG UNKNOWN~`。

槽 0–2，128 字节，CRC16-CCITT-FALSE init 0xFFFF。type4 仍 UNSUPPORTED。

---

## 4. 主动推送

### `^READY REV04~`

boot 完成一次。

### `^EVSTAT FLAG3 <session8> <irq8> <3 records>~`

- 仅 D2 **scan0** 且至少一路 MON ON
- 记录格式克隆（`U<slot>` / `D<slot>,type,addr,gen,flags,status,sample,irq,last,age,err,src`）
- INTACK 前 hold D2_SCAN 缓存；同序号 rescan 不推、不覆盖
- nINT 仍低：最多再扫 **2** 次供 192x 状态机，不推第二帧 EVSTAT

### `^GPIOEV GPIO<index> D<pin> SEQ=<8hex> LEVEL=0 SRC=INT0|PCINT2 CHIP=BOARD~`

- DETECT 下降沿。D2=INT0 且 index=0；D3–D5=PCINT2
- 与 EVSTAT 可同时发生（D2）
- GUI 只记日志

---

## 5. FLAG 监视（与 runtime 独立）

- 3 槽 RAM cache，结构 22 字节逻辑克隆
- 192 读 0x02；1221 读 0x03
- MON OFF 清 sample；SESSION 换 id 清全部
- 不启动/停止 192x 检测

---

## 6. GPIO / ADC / 读频 限制

| 能力 | 允许脚 |
|---|---|
| 数字 GPIOCFG | D2–D13，A1–A3 |
| DETECT | D2–D5 |
| ADC | **仅 A1 A2 A3** |
| 读频 | **仅 D8 / ICP1** |
| 禁 | D0 D1 D7 A0 A4 A5 A6 A7 |

D7 仍是预设失败灯，非公共 GPIO。

---

## 7. EEPROM 与按键

- 活动槽：EEPROM[0]，`0xFF`=无
- V2 槽：基址 256，每槽 128；暂存 640
- 写槽：先擦 magic 再写 body 最后写回 magic
- 物理键 A0/A7/A6：模拟 `<512` 为按下，去抖 35ms；按下 `activatePreset(0/1/2)`；**不写活动槽**；失败 D7 亮，成功灭
- 上电：500ms → 按类型复位已存目标 → 活动槽最多 10 次 apply、间隔 100ms
- 复位：**192x / 1018 / 1221 均为 probe 成功后写 `0x01=0x5283`**（与三页 GUI 复位按钮相同）。按地址去重，同一地址只复位一次。
- 1221 无合法 CFG payload。`validateV2PresetAt` 对 type4 返回 **UNSUPPORTED**，`readLogicalPreset` 只接受 **VALID**。上电复位循环与原代码一样：**跳过 INVALID/UNSUPPORTED 槽**，既不 `0x5283` 也不 apply。不要写「合法 1221 头只复位不 apply」——原路径里不存在这种槽。

---

## 8. AWP192x runtime

- 仅 CFG apply / 上电活动槽 / 物理键创建
- RAM **2 槽**，地址仍可为 0x44/0x45/0x46，同时最多两颗。
- 满槽：该地址**已在**两槽之一 → 覆盖更新（先 reset 再写）。新地址且两槽都 `valid` → **拒绝**，`CFGAPPLY`/`按键`/`boot apply` 失败 `APPLY_WRITE`（或 `^ERR CFGAPPLY APPLY_WRITE~`），**已有两路不动**。
- apply 后 DETECT；无串口 start/stop
- WREGW 先 stop：`0x06=0002` 与 `0008`，都成功才清槽
- 相位：WAIT_PRESS ↔ WAIT_RELEASE；rearm 等 ON_PLAY 清再 ADC_Go 再等 ADC_BUSY
- 松开超时 **3s**（以后可配另谈，本轮写死）
- ADC_BUSY 看门狗（克隆原 `serviceAwp192xDetectionRearm` / `recoverAwp192xDetectionRearm`）：
  - **仅** `REARM_WAIT_ADC_BUSY` 且已经 `ADC_Go`（`detection_rearm_go_issued=1`）后累加 3s。等 ON_PLAY 结束的阶段**不计时、不复位**。
  - 超时：先把 `detection_rearm_go_issued` 清 0，再校验 EEPROM payload+CRC，失败则**不复位芯片**。通过才 `0x01=0x5283`、重放波形、再 `switchCfg`/`ADC_Go`，然后 `go_issued=1`、elapsed 清零，继续等 ADC_BUSY。
  - 重放失败：elapsed 清零，留在 ADC_BUSY 且 `go_issued=0`，再过 3s 重试 recover。不得把旧的 ADC_BUSY=1 当成新一次 ADC_Go 成功。
  - 无串口日志。
- 波形展开（减额 **A**：整数 LUT，配方线格式不变）：
  - EEPROM 仍只存 11 字节配方 × 1–8 条（kind=1 三段缓动「SINC」、kind=2 正弦）。**不**把 1018/1221 的 Gauss ROM 引进 192x。
  - MCU **禁止**对 192x 路径使用 `float`/`cos()`/`math.h`。
  - PROGMEM 表：`int16_t cos_q15[256]`，索引 `0..255` 对应 `0..2π`（不含 2π），值域 `cos` 的 Q15（32767 ≈ 1.0）。
  - 缓动 `(1-cos(π t))/2`：`t = index/(count-1)`（count≤1 则直接终点）；`θ = π t` 映射到表的半圈（index `0..128` 线性）；`eased = (32767 - cos_q15[i]) / 2`，再与起终点 mV 做 32 位整数插值，四舍五入规则与现 `±0.5f` 相同（正数 +0.5，负数 -0.5）。
  - 正弦：`cos((phase_deg-90)*π/180)*amp + offset` 改为把相位折到 `[0,360)` 再查表；`phase_deg` 仍按原配方（points2 为起始角、phase_span 为跨度）。
  - `mapMillivoltsToDac` 保持现有整数缩放（±95000mV，零点 0x0800/0x07FF）。
  - 写 SRAM 路径不变：0x16 索引 + 0x00 逐点 `write_single`，循环内 `wdt_reset()`。boot / CFGAPPLY / 看门狗重放都走这张表。
  - GUI 配方编码不变，不必改 Python 生成器。
- **无** sidecar 串口

D2 处理（与原 `handleInterrupt` 一致，指南旧句不准确）：
- 先读 **已 configured 且 MON ON** 的 FLAG 目标（192→0x02，1221→0x03）。仅 scan0 写入 EVSTAT 缓存。
- 再遍历每个 **DETECT runtime**：该地址若已在上面读过则**复用**；否则自己读 0x02。
- **MON OFF / 未登记 的 runtime 仍必须读 0x02 并跑按/放状态机。** 「只补读未在目标表里的」是错的。
- 按 bit11 / bit9 切相位。不打印 `E`/`I`。

---

## 9. AWP1018

- 无 runtime
- CFGAPPLY：现 `applyAwp1018AdcPreset` 行为 1:1（描述符 SRAM、寄存器表、最后 `0x02=0001`）
- 上电复位：`0x01=0x5283`（与 GUI 按钮相同）

---

## 10. AWP1221

- 无 runtime，CFG type4 = UNSUPPORTED
- 复位：`0x01=0x5283`（与 GUI 相同）
- 只用 TGT/MON/EVSTAT 读 0x03

---

## 11. setup / loop

`setup`：清 WDRF → 关 WDT → Serial 115200（无 debug_flag）→ D2 上拉 → Wire 400k+25ms timeout → 灭 D7 → 上电 preset（静默 delay，不 `serialEvent`）→ 按键初值 → D2 DETECT 中断 → WDT 1s → **`^READY REV04~`**。

`loop`：喂狗 → 服务 D2（handleInterrupt + 有条件 EVSTAT + rescan≤2）→ GPIOEV →（无脉冲）→ 192x rearm/release 定时（超时无打印）→ `serialEvent` → 扫按键。

I2C：`write_single` / `read_single_safe` 克隆。CRC 克隆。

---

## 12. GUI 必须改（同步）

1. 连接：等 `^READY REV04~` 30s；删 PING/BOOTING/`debug_flag` 处理
2. `CAP?` 认 `^CAP FLAG3~`
3. 删 `STAT?` 路径；只吃 EVSTAT
4. `FLAG?` 新语法；删 `FLAG 0|1`
5. `INTACK STALE` 无 SEQ
6. TGT 不发 ANY
7. RREGW/WREGW 去掉 32 位
8. 删 GPIO 脉冲、`GPIO`/`GPIOU`；GPIOCFG 扩脚 + PULLUP；GPIOR 不改模式
9. 错误解析：`ERR <CMD> <REASON>`
10. 1221 脉冲 UI 删除
11. 新：`ADCR`、`FREQ?`（周期 µs → Hz）
12. 不再把 `PONG` 当身份

---

## 13. 以后再谈（本轮不做）

- 3s 松开超时改由 GUI/payload 配置
- I2C 块写 0x00（未验证芯片是否连写自增）
- 1018 apply 内部重构（行为已 1:1）
- 1221/1018 runtime
