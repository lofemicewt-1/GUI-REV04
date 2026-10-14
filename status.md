# GUI-REV04 审查与修复状态

更新：2026-10-14（Asia/Shanghai）　分支：`audit-fixes`（仅本地，未推送）
固件参照：`outputs/Without_GUI_Single/Without_GUI_Single.ino`（`outputs/` 被 .gitignore 忽略，未入库）

## 一、已完成的 GUI 修复

| # | 文件 | 内容 | 原因 |
|---|---|---|---|
| 1 | `core/serial_manager.py` | 所有串口超时/等待改用 `time.monotonic()` | 系统时间跳变会导致卡死或提前超时 |
| 2 | `app/navigation.py` | 根窗口 Map/Unmap 改绑到仅根窗口持有的 bindtag | 原绑定对每个子控件都触发，切页时数百次无效回调 |
| 3 | `pages/base_page.py` | 标题 Logo 字号计算加缓存（含 tk scaling） | 每次 `<Configure>` 都新建约 60 个 Font，约 100 ms |
| 4 | `app/application.py` | `_refresh_widget_fonts` 按字体描述缓存 `font.actual()` | 主题切换由约 5 s 降到约 0.3 s |
| 5 | `config/languages.py` | 删除被覆盖的重复文案键 `awp1018_diff_*_hint` | 死代码，显示不变 |
| 6 | `core/serial_manager.py` | CFGEND / CFGDEL / CFGAPPLY 应答等待放宽到 3 s | EEPROM 写满 256 字节约 0.85 s，超过默认 0.8 s，会误报失败且迟到应答污染下一条命令 |
| 7 | `core/base_tool.py`、`core/serial_manager.py` | 解析 `^ERR <CMD> <REASON> [code]~`，日志追加 `ERROR <CMD> failed: <REASON> code=N (含义)`，错误信息为"原始帧 [解释]" | 便于定位失败点；**成功/失败语义不变** |
| 8 | `core/base_tool.py`、`pages/awp1922_page.py` | 地址 NACK 重试按错误码判断：带码时仅 `I2C 2` 重试；不带码的 `ERR WREGW I2C`（现固件）保持原重试；`WREGW STOP` 等不重试 | 向前兼容将来带码固件，避免对数据口重复写 |
| 9 | `pages/awp1922_page.py` | AWP1922 0x00 写入新逻辑（见第二节） | 用户确认的需求 |
| 10 | `tests/test_audit_awp1922_err.py` | AWP1922 逻辑（含关断状态、RO 位屏蔽）、ERR 解析/重试/上下文、AWP1221 0x50–0x57 回归测试 | — |
| 12 | `app/application.py`、`core/serial_manager.py`、`core/chip_status_cache.py` | 删除全部 `STAT?` 使用及 FLAG 轮询死代码（`_start_flag_poll`/`_stop_flag_poll` 等 20 个方法、`_D2ScanRequest`、`read_status_snapshot`、`read_board_flag_for_poll`、`build_stat_*`、`d2_snapshot`/`snapshot` 结果分支、`not d2_scan` 分支、相关常量与状态字段，约 660 行） | 均不可达；保留 CAP FLAG3 + EVSTAT 路径与旧固件"需要 FLAG3 EVSTAT 固件"提示；`FLAG?` 手动接口 `read_board_flag` 保留 |
| 11 | `core/base_tool.py` | 寄存器读写失败信息包含：命令、dev/reg/width/count、尝试次数、完整原始应答、原因、错误码及含义；同时写入串口日志 | 便于定位；"未连接/忙"等传输层错误文本保持不变（调用方按文本比较） |

性能（1400×900，Xvfb）：首次进入芯片页 150–200 ms → 30–130 ms；之后切页 15–50 ms；主题切换 ~5 s → ~0.3 s；语言切换 ~250–380 ms（未改）。

## 二、已确认的决定

- **AWP1922 寄存器表（数据手册已确认）**：0x00 bit7 BIAS_EN（0=关断）、bit6 PLAY、bit2–0 OTP/OVP/UVLO 屏蔽，默认 0x80；0x01 只读，bit6 ON_PLAY、bit2–0 OTP/OVP/UVLO；0x05 bit6–4 I2C_ADDR（只读）、bit3–0 FREQ_CONTROL；0x06 bit4–0 VOUT_CONTROL。
- **AWP1922 0x00 写入规则**：
  - 写 0x00 时若 BIAS_EN=0，GUI 强制置 1（含手动寄存器写）；
  - **例外**：用户点击 Shutdown 后，该地址记为"用户关断"，此后对该地址的 0x00 写入（含手动写、Play/Stop）都不再强制使能，直到点击 Wake 清除；状态按芯片地址分别记录，Shutdown 写失败则不记；
  - 仅在"被强制置 1"且 PLAY=1 时，读回芯片 0x05/0x06 并回写，只写可写位：0x05 取 `& 0x0F`（去掉只读 I2C_ADDR 位），0x06 取 `& 0x1F`；读失败的寄存器跳过并记日志；
  - Wake：读回 0x00，仅把 EN（bit7）置 1、其余位保持；若 EN 已为 1 则不写；任何情况下都不回写 0x05/0x06；同时清除该地址的"用户关断"标记（读回失败时用缓存值）；
  - 已删除旧的"写 0x40 后读 0x01 清状态"逻辑。常量在 `pages/awp1922_page.py` 顶部。
- **AWP1221 地址 0x50–0x57**：GUI 各表（探测、状态监控、预设签名、页面地址）已全部覆盖，并有回归测试。
- **板上按键**：活动槽为 Type-4 时任意按键都应用活动槽——行为确认无需修改。
- **固件写寄存器前停止 192x runtime**：保持现状（固件无剩余 Flash 做额外检查）。GUI 向某 192x 地址写任何寄存器都会使该地址的检测/播放 runtime 停止，且不通知 GUI。
- **ERR 不统一为失败**：保持 `send_command` 原语义，只增强显示。
- **按需刷新语言**：不做（首次切到 AWP1921/1018/1221 达 105–140 ms，可感知）。
- **字体与窗口缩放**：保持现状——窗口缩放不立即更新字体，主题/字号切换时按当前缩放重设。
- **"缩放与字号未变时跳过字体重设"优化**：不做。该次重设同时会把之后动态创建（未经过重设）的控件字体统一，无法保证无视觉变化；且该步骤现仅约 90 ms。

## 三、协议要点（GUI ↔ 固件 REV04）

- 帧：`^…~`，固件命令缓冲 36 字节（最长有效帧 35 字符，WREGW 16 位 5 个值恰好占满）。
- RREGW 上限：8 位 32 个 / 16 位 16 个；WREGW 上限：8 位 10 个 / 16 位 5 个。GUI 与固件一致。
- RREGW 失败 `^ERR RREGW I2C <code>~`，code：1 过长、2 地址 NACK、3 数据 NACK、4 其他、5 超时、6 读不足、7 长度非法。WREGW 失败目前**不带码**（`^ERR WREGW I2C~`，也覆盖"停止 192x runtime 失败"）。
- CFG：记录 128/256 字节，CRC16-CCITT-FALSE（init 0xFFFF）；Type-4 必须 256 字节；EEPROM 槽地址 256 + slot×256（共 3 槽，至 1024 字节）。
- 就绪：`^READY REV04~`；状态：`CAP?`→`^CAP FLAG3~`，`SESSION/TGT/TGTCLR/MON/FLAG?/INTACK`，D2 事件推送 `^EVSTAT FLAG3 …~`。
- `STAT?` 固件已删除（回 `^ERR UNKNOWN CMD~`）；GUI 已移除全部调用，状态只来自 EVSTAT 推送（2026-10-14）。旧固件（无 CAP FLAG3）仅显示"需要 FLAG3 EVSTAT 固件"提示，不轮询。
- `docs/specs/02-SETTLED.md` 列出的 PING/PONG、GPIO、GPIOU、FLAG 写、GPIOCFG 脉冲在固件中不存在，GUI 也未使用；文档待更新。

## 四、待处理事项

1. ~~STAT? 路径移除~~：**已完成**（见第一节 #12）。
2. **后台线程改队列：已在本地分支 `thread-queue` 实现（未合入 audit-fixes，未推送）。** 新增 `core/ui_dispatch.py`：`UiDispatcher`（FIFO `SimpleQueue`，主线程每 16 ms 取队列；每轮全部取完，上限 2000 条/12 ms，剩余 1 ms 后继续）+ `post_to_ui(widget, fn, *args)`（工作线程只入队、不调 Tk；关窗后丢弃；无应用时回退 `after(0)`）。26 处 `after(0)`（含 2 个 `_schedule_ui` 帮助函数、realtime SRAM 两处变量回写、串口断开回调）全部改走 `post_to_ui`；`append_serial_log` 改为按页面批量合并（一次插入多行，清空用代号丢弃旧批次）。回调异常走 `report_callback_exception`，控件已销毁的 TclError 静默。压测：关窗 30 轮 0 报错；日志洪泛吞吐 ~700→13 000 行/s（旧实现工作线程被 UI 反压阻塞），心跳 p99 ≤ 46 ms，切页中位数 ~13 ms 无退化。
3. **测试**：`tests/validate_type4_avr.js` 依赖 `outputs/type4_20260930/`（测试入口 .ino、hex、符号、用例、avr8js），仓库中缺失，暂无法运行。4 个测试仅能在 Windows 上通过（windll、Windows 路径、截图）。
4. 文档 `02-SETTLED.md` 需与固件现状同步（见第三节）。

## 五、固件修改建议及状态

| # | 内容 | 状态 |
|---|---|---|
| 1 | `setup()` 中 `wdt_enable` 前清空串口接收缓冲（启动期间收到的字节原本会在 READY 后被执行） | **已在本地副本修改** |
| 2 | WREGW 失败带 Wire 错误码、区分 `STOP` | **否决**（Flash 不足）；GUI 已向前兼容 |
| 3 | `applyAwp1221Payload` 先读 0x05/0x15，读成功后再写 0x1F=000C、0x02=0010 | **已在本地副本修改**（见下方风险） |
| 4 | 删除未使用的 `flagStatusInterruptSequence()`（原子读取 `interruptSequence` 的辅助函数，固件与 GUI 均无调用） | **已在本地副本删除**（编译器原已剔除，Flash 不变） |

固件第 3 项说明：未"挪到最后"——函数末尾写 `0x02=0x0001`（启动），若把 `0x02=0x0010` 放到其后会把刚启动的芯片又停掉，且描述符会在芯片运行时写入。现顺序为：读 0x05 → 读 0x15 →（任一失败直接返回，芯片未被改动）→ 写 0x1F → 写 0x02=0010 → 写 0x05 → 其余不变。风险：若 `0x02=0x0010` 会改变 0x05/0x15 的内容，则现在保存的是停止前的值，需对照芯片手册确认。

固件编译（arduino-cli，arduino:avr:nano，avr core 1.8.8）：修改后 Flash 30,414 / 30,720（99%），RAM 1,212 / 2,048。补丁：`/workspace/firmware-audit.patch`。

## Windows 最大化卡死 / 切页先旧尺寸后重排（2026-10-14）
- “Write timeout” 弹窗唯一来源是首页“连接”按钮 → `SerialManager.connect()`；原实现在 Tk 主线程同步打开串口并握手。COM4 为蓝牙标准串行（非通讯板），打开时 Windows 会尝试连接配对设备、阻塞数秒，随后握手写入超时 → 主窗口在此期间无法重绘/缩放，表现为“最大化卡住数秒后弹 Write timeout”。缩放/Map/Configure 路径不会写串口，也不会打开端口；该问题 master 即存在，非 audit-fixes / thread-queue 引入。
- 修复：连接改在后台线程执行，主线程 25 ms 轮询结果；连接期间“连接”按钮禁用、重复点击忽略；失败仍弹原错误框，成功路径不变。
- 切页：隐藏页面保留上次显示时的几何尺寸，`navigation.show()` 原先在 `on_show()` 之后才在 idle 中同步尺寸，Windows 上可先按旧尺寸绘制再重排。修复：`grid()` 后立即 `update_idletasks()` + 同步内容窗口，再调用 `on_show()`。同样为 master 既有行为。
- Linux 实测（2560×1440，1.5× 缩放）：最大化重排 master 126–151 ms，audit-fixes 68–103 ms，无多秒卡顿。
- 蓝牙串口：枚举时按描述/hwid/厂商含 “Bluetooth”/“蓝牙”/BTHENUM/BTHMODEM，或设备名 rfcomm* 识别为蓝牙口。`SerialManager.connect()` 直接拒绝（不打开），首页连接弹出明确提示；Windows COM 兜底扫描跳过已知蓝牙口；默认选中第一个非蓝牙口。
- 连接可取消：连接中按钮变为“取消连接”，点击立即恢复界面并丢弃迟到结果；后台线程返回后若已打开则自动关闭端口；再次连接会等前一次后台清理完成后再打开（在后台线程中等待，不阻塞界面）。握手阶段无法中途打断，只能等其超时后关闭。
