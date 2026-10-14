# AWP1221 serial3 / serial4 SIN + SINC 实施结果

日期：2026-09-30

## 实施内容

- `core/awp1221_sinc.py` 将 serial3 固定为 Slice `0x18..0x1B`、serial4 固定为 `0x1C..0x1F`，每组计划写入这四个 Slice 的全部16个字。首Slice使用ROM SINE、全周期编码、phase 0（ROM −90°起点），幅值与偏置复用首个SINC段的量化值；`word2` 写入 `(repeat_count - 1)`。后三Slice沿用 serial1 的三段SINC描述符和量化数据，单段重复一次。
- serial3/4四个单点时长按物理Slice顺序解释为 `SIN、SINC1、SINC2、SINC3`。首段SIN重复次数默认为1，接受1..256的整数；总时长为 `N*t0*repeat + N/2*(t1+t2+t3)`，SIN频率仍为 `1e6/(N*t0)`。serial1/2维持原三时长、三Slice行为。
- AWP1221页面在 serial3/4显示首段重复次数输入，并按各段实际持续时间绘制预览。SIN绘图采样最多2048点，防止高重复次数令界面变慢。四个时长和重复次数随 serial3/serial4 各自保存，参数变化继续清除旧写入资格；断连和写失败仍会阻断播放。
- 软件预设结构包含 `sin_repeat`；读取 serial3/4预设时保留四个时长和重复数，旧预设缺少 `sin_repeat` 时使用1。serial1/2预设继续读三个时长，重复数固定为1。页面的“导入/导出页面设置”按钮在本次基线中仍为占位状态，本次没有扩展新的文件导入/导出流程。
- 中英文界面增加重复数、SIN频率和四时长不足提示。

## 验证记录

核心与页面回归，以及适用的现有AWP1221命令门控、ADC设置、实时覆盖、SRAM与页面用例，运行结果为 **109 tests passed**：

```powershell
py -m unittest tests.test_awp1221_serial34_sinc tests.test_awp1221_cmd_gate tests.test_awp1221_realtime_settings tests.test_awp1221_realtime_overlay tests.test_awp1221_sram tests.test_awp1221_sram_ui
```

源文件语法检查通过：

```powershell
py -m compileall -q core/awp1221_sinc.py pages/awp1221_page.py config/languages.py tests/test_awp1221_serial34_sinc.py tests/validate_awp1221_serial34_preview.py
```

差异空白检查通过：

```powershell
git diff --check
```

新增回归覆盖 serial3/4、512/256点深度、16个写入地址、SIN descriptor及−90°起始编码、SIN量化峰值、后三段与serial1描述符一致、四时长页面映射、repeat 1/2/256及word2编码、非法0/257/非整数、频率/总时长、资格key失效、断连/写失败互锁、旧预设缺字段兼容与四时长载入。`py -m pytest` 曾尝试运行，但当前环境没有安装pytest；未安装依赖，改用仓库现有 `unittest` 测试。

离线Tk页面使用现有 `mocked_application` 夹具构建，串口和I²C传输入口均设为禁止调用。运行命令：

```powershell
py tests/validate_awp1221_serial34_preview.py
```

serial3示例时长为 `4/7/8/9 us`、重复2次，总时长10240 us、SIN频率488.28125 Hz；serial4示例为 `5/9/11/13 us`、重复1次，总时长11008 us、SIN频率390.625 Hz。两组分别生成 `0x60..0x6F` 与 `0x70..0x7F` 共16字地址。256次重复的离线预览总时长530432 us，频率仍为488.28125 Hz，Canvas折线坐标4420个（2210个采样点），验证绘图采样有界。切换serial3/4后分别恢复2次与1次。

截图和机器可读结果保存在 [`awp1221_serial34_evidence`](awp1221_serial34_evidence) 下：

- serial3：[`serial3_preview.bmp`](awp1221_serial34_evidence/serial3_preview.bmp)、[`serial3_four_times.bmp`](awp1221_serial34_evidence/serial3_four_times.bmp)、[`serial3_repeat_control.bmp`](awp1221_serial34_evidence/serial3_repeat_control.bmp)、[`serial3_repeat256_preview.bmp`](awp1221_serial34_evidence/serial3_repeat256_preview.bmp)
- serial4：[`serial4_preview.bmp`](awp1221_serial34_evidence/serial4_preview.bmp)、[`serial4_four_times.bmp`](awp1221_serial34_evidence/serial4_four_times.bmp)、[`serial4_repeat_control.bmp`](awp1221_serial34_evidence/serial4_repeat_control.bmp)
- 数据：[`offline_preview_result.json`](awp1221_serial34_evidence/offline_preview_result.json)

## 基线归属和边界

开始时 `core/awp1221_sinc.py` 干净；`pages/awp1221_page.py` 和 `config/languages.py` 已有大量未提交修改，仓库中也有其他无关脏文件及未跟踪文件。开始时记录的core与页面SHA-256及相关文件状态见 [`baseline.json`](awp1221_serial34_evidence/baseline.json)。本次没有重置、清理或暂存其他改动；页面和语言文件的最终差异包含用户原有改动，需结合该基线审核本任务增量。

本次没有修改固件、连接真实串口/芯片、执行硬件写入、刷板、打包EXE或运行真实板卡验收。离线界面和模拟测试不能证明ROM采样边界或实际板上输出。

## 复核修正记录（2026-09-30）

复核发现预览绘图曾对三个半周期 SINC 也执行相位取模，导致各段终点错误回到起点；serial1/2 的每段预览时长还使用占位值1，横轴没有按真实时长分布。本轮只修复这两项预览契约，并更新深度提示与离线证据：相位取模现在只用于可重复的完整 SIN；半周期 SINC 保留位置1端点。serial1/2 每段横轴宽度按 `(深度/2) × 对应单点时长` 计算，三段总时间覆盖完整绘图区。serial3/4 提示明确显示首段 SIN 为 N 点、后三段各 N/2 点，serial1/2 继续显示原有每段点数提示。

新增三项页面绘图回归，直接读取 Canvas 实际折线坐标：验证完整 SIN 重复1、2、256次后均结束于0V；四段物理连接依次为0V、实际峰值、实际谷值、0V，且连接端点连续；serial1/2 示例时长4/8/16的段边界按1/7、3/7、1覆盖横轴，最后一点到达绘图区右边界。深度提示也有回归检查。适用 unittest 集合现为 **112 tests passed**（原109项加本轮3项）。

离线 Tk 检查重新运行成功。重复次数截图脚本逐步滚动，并断言控件整体位于视口内；记录的视口坐标为 y=374..607，重复输入控件为 y=554..589。Full Mode 离线检查选择了“波形选择器”Notebook 页，serial4四时长与重复2次配置可见，并记录共享SIN/SINC Canvas预览。最新截图包括：

- Button Mode重复控件：[`serial3_repeat_control.bmp`](awp1221_serial34_evidence/serial3_repeat_control.bmp)、[`serial4_repeat_control.bmp`](awp1221_serial34_evidence/serial4_repeat_control.bmp)
- Full Mode共享预览：[`full_mode_serial4_preview.bmp`](awp1221_serial34_evidence/full_mode_serial4_preview.bmp)
- 几何断言、四时长、重复次数、地址和时长记录：[`offline_preview_result.json`](awp1221_serial34_evidence/offline_preview_result.json)

本轮完整测试命令：

```powershell
py -m unittest tests.test_awp1221_serial34_sinc tests.test_awp1221_cmd_gate tests.test_awp1221_realtime_settings tests.test_awp1221_realtime_overlay tests.test_awp1221_sram tests.test_awp1221_sram_ui
```

结果：`Ran 112 tests ... OK`。离线 Button/Full Mode 命令仍为 `py tests/validate_awp1221_serial34_preview.py`。源码语法检查与 `git diff --check` 已在本轮末尾重新执行；硬件验收边界与前述记录相同。

## 主智能体最终验收

2026-09-30，执行模型为用户指定的 `gpt-6-luna / high`。主智能体在收到交付标志后独立复核源码、描述符、实际下拉标签路径及离线截图，并重新运行适用回归：`Ran 112 tests in 14.787s ... OK`。`compileall` 与 `git -c core.safecrlf=false diff --check` 均退出0。

验收结论：源码与离线软件验收通过。serial3/4 首段完整 ROM SIN 从 −90° 开始，量化范围为0V至与SINC相同的实际最大电压，独立单点时长与1..256次重复已接入；后三段SINC和对应四Slice写入计划正确。预览半周期终点、时长比例、完整下拉标签的分段深度提示均已修正。实际完整下拉项的提示测试覆盖512/256两种深度。

部分Tk测试在窗口销毁时输出 `ttk::ThemeChanged` 提示，测试退出0；该提示不作为板卡或启动结果。本次未运行真实板卡验收，未重新打包EXE。

最终源码 SHA-256：

- `core/awp1221_sinc.py`：`20E16A781F75C08853368D24A4C1659F41F1C17F066F32C25126BCE57C3C6E33`
- `pages/awp1221_page.py`：`A78F8502E1E9CE4CC2DBE8E57C368A1ED93B9B835439C8D3FF155ED9B6970F09`
- `config/languages.py`：`9655B4936F0703F8FFDBB9F02ABD4D54AE36E9528A1BD8683FF47A259D5697C0`

`AWP1221_SERIAL34_SIN_SINC_SOFTWARE_ACCEPTED`

## 真实下拉选项复核修正

最终复核确认串口下拉框变量保存完整显示标签（例如 `serial3 (0x18-0x1B, 4 slices)`），而非内部短名。本轮将深度提示改为通过 `awp1221_sinc_serial_group` 解析短名，并在不合法值时回退原有提示；单测覆盖真实 serial1/3/4 下拉标签及512/256点深度，离线脚本也改用真实下拉选项。`py -m unittest tests.test_awp1221_serial34_sinc` 结果为 **13 tests passed**；离线 Button/Full Mode 脚本已复跑成功，截图与JSON几何记录已更新。此前完整适用集合112项通过；本轮只对提示解析增加路径覆盖，未重跑整套集合。
