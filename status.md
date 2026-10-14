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
| 10 | `tests/test_audit_awp1922_err.py` | AWP1922 逻辑、ERR 解析/重试、AWP1221 0x50–0x57 回归测试 | — |

性能（1400×900，Xvfb）：首次进入芯片页 150–200 ms → 30–130 ms；之后切页 15–50 ms；主题切换 ~5 s → ~0.3 s；语言切换 ~250–380 ms（未改）。

## 二、已确认的决定

- **AWP1922 控制寄存器 0x00**（8 位）：使能位 `0x80`（bit7，即 1 起算的 bit8），播放位 `0x40`（bit6）。写 0x00 时若使能位为 0，GUI 强制置 1；仅在"被强制置 1"且播放位为 1 时，读取芯片当前 0x05（频率）/0x06（幅度）并原样回写。Shutdown 按钮不强制；**手动寄存器写 0x00 也会被强制使能**。删除了旧的"写 0x40 后读 0x01 清状态"逻辑。常量位于 `pages/awp1922_page.py` 顶部。
- **AWP1221 地址 0x50–0x57**：GUI 各表（探测、状态监控、预设签名、页面地址）已全部覆盖，并有回归测试。
- **板上按键**：活动槽为 Type-4 时任意按键都应用活动槽——行为确认无需修改。
- **固件写寄存器前停止 192x runtime**：保持现状（固件无剩余 Flash 做额外检查）。GUI 向某 192x 地址写任何寄存器都会使该地址的检测/播放 runtime 停止，且不通知 GUI。
- **ERR 不统一为失败**：保持 `send_command` 原语义，只增强显示。
- **按需刷新语言**：不做（首次切到 AWP1921/1018/1221 达 105–140 ms，可感知）。
- **主题切换不重设字体**：不做。窗口缩放不会重设字体，主题/字号切换时才按当前缩放重设；直接去掉会使缩放后字体停留在旧尺寸。

## 三、协议要点（GUI ↔ 固件 REV04）

- 帧：`^…~`，固件命令缓冲 36 字节（最长有效帧 35 字符，WREGW 16 位 5 个值恰好占满）。
- RREGW 上限：8 位 32 个 / 16 位 16 个；WREGW 上限：8 位 10 个 / 16 位 5 个。GUI 与固件一致。
- RREGW 失败 `^ERR RREGW I2C <code>~`，code：1 过长、2 地址 NACK、3 数据 NACK、4 其他、5 超时、6 读不足、7 长度非法。WREGW 失败目前**不带码**（`^ERR WREGW I2C~`，也覆盖"停止 192x runtime 失败"）。
- CFG：记录 128/256 字节，CRC16-CCITT-FALSE（init 0xFFFF）；Type-4 必须 256 字节；EEPROM 槽地址 256 + slot×256（共 3 槽，至 1024 字节）。
- 就绪：`^READY REV04~`；状态：`CAP?`→`^CAP FLAG3~`，`SESSION/TGT/TGTCLR/MON/FLAG?/INTACK`，D2 事件推送 `^EVSTAT FLAG3 …~`。
- `STAT?` 固件已删除（回 `^ERR UNKNOWN CMD~`）；GUI 中相关路径均不可达（见第四节）。
- `docs/specs/02-SETTLED.md` 列出的 PING/PONG、GPIO、GPIOU、FLAG 写、GPIOCFG 脉冲在固件中不存在，GUI 也未使用；文档待更新。

## 四、待处理事项

1. **STAT? 路径移除**（评估完成，未删除）：三处调用均不可达——
   - `_configure_status_target_worker` 中 `not capabilities.d2_scan` 分支：`parse_cap_frame` 恒置 `d2_scan=True`，CAP 失败时 capabilities 为 None，均不会进入；
   - `_read_d2_status_snapshot_worker`：仅由 FLAG 轮询触发，而 `_start_flag_poll` 无任何调用方；
   - `_read_status_snapshot_worker`：经 `_poll_status_snapshot`，而 `_status_poll_start` 是空函数。
   旧固件（无 CAP FLAG3）当前已只显示"需要 FLAG3 EVSTAT 固件"提示、不轮询，删除后行为不变。可连同 FLAG 轮询死代码一并删除，待确认。
2. **后台线程改队列**：29 处线程、约 16 处线程内 `after(0)`；未发现线程内直接操作控件。预计 1–1.5 天，待排期。
3. **测试**：`tests/validate_type4_avr.js` 依赖 `outputs/type4_20260930/`（测试入口 .ino、hex、符号、用例、avr8js），仓库中缺失，暂无法运行。4 个测试仅能在 Windows 上通过（windll、Windows 路径、截图）。
4. 文档 `02-SETTLED.md` 需与固件现状同步（见第三节）。

## 五、固件修改建议及状态

| # | 内容 | 状态 |
|---|---|---|
| 1 | `setup()` 中 `wdt_enable` 前清空串口接收缓冲（启动期间收到的字节原本会在 READY 后被执行） | **已在本地副本修改** |
| 2 | WREGW 失败带 Wire 错误码、区分 `STOP` | **否决**（Flash 不足）；GUI 已向前兼容 |
| 3 | `applyAwp1221Payload` 先读 0x05/0x15，读成功后再写 0x1F=000C、0x02=0010 | **已在本地副本修改**（见下方风险） |
| 4 | `flagStatusInterruptSequence()` 未使用 | **保留不删**；仅为原子读取 `interruptSequence` 的辅助函数，固件与 GUI 中均无调用 |

固件第 3 项说明：未"挪到最后"——函数末尾写 `0x02=0x0001`（启动），若把 `0x02=0x0010` 放到其后会把刚启动的芯片又停掉，且描述符会在芯片运行时写入。现顺序为：读 0x05 → 读 0x15 →（任一失败直接返回，芯片未被改动）→ 写 0x1F → 写 0x02=0010 → 写 0x05 → 其余不变。风险：若 `0x02=0x0010` 会改变 0x05/0x15 的内容，则现在保存的是停止前的值，需对照芯片手册确认。

固件编译（arduino-cli，arduino:avr:nano，avr core 1.8.8）：修改后 Flash 30,414 / 30,720（99%），RAM 1,212 / 2,048。
