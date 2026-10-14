# AWP1221 最大化布局修复结果

状态：`AWP1221_LAYOUT_READY`

本阶段按最新范围只修复 AWP1221 Button Mode / Full Mode 的最大化、模式切换和恢复小窗口布局。Type-4 板载预设错误回调、侧栏冷启动/隐藏重显示和固件/寄存器协议均未修改。

## 根因

AWP1221 页面固定页脚后，外层 `VerticalScrollArea` 的 Canvas 视口会随窗口增高，但嵌入的模式页面只保持自然请求高度（Button 约 705 px，Full 约 754 px），所以最大化窗口下控件仍挤在上方，下方留下大片空白。Full Mode 的左右列还使用左列 `weight=0`、右列 `weight=1`，左侧 ADC 四列表单被限制在约 392 px，触发内部横向滚动。

## 修改

- `pages/base_page.py` 为 `VerticalScrollArea` 增加可选 `propagate_height`。启用时，短内容填充视口并保留 16 px 底部滚动区间；内容高于视口时仍按自然高度滚动。
- `pages/awp1221_page.py` 仅对 AWP1221 外层模式滚动区启用 `propagate_height=True`。
- 模式切换后在 Tk 几何请求稳定后重新同步滚动区，保证 Button ↔ Full 切换不会沿用上一模式的高度。
- Full Mode 改为左 ADC 面板伸展、右波形/日志面板保持自然宽度；Button Mode 的等宽双面板保持不变。

## 实测证据

真实 Tk 离线 mock 专项命令：

```text
py -B tests/validate_awp1221_maximize.py
status=AWP1221_QA_READY
observations=4 errors=0
```

该专项实际调用 `root.state('zoomed')` 和 `normal` 恢复，覆盖 Button/Full 最大化及 Full/Button 恢复小窗口；桥接、串口和传输均 mock，没有 I2C、寄存器写入、固件编译/烧录或 EXE 打包。

- 最大化根窗口：2240×1329；Button/Full mode 内容高度均为 1099 px，视口 1115 px，填充率 98.6%，垂直滚动条隐藏。
- 恢复窗口：1000×700；Full 请求高度 754 px、Button 请求高度 705 px，垂直/横向滚动条显示，滚动终点均可达。
- 真实截图：
  - [`Button 最大化`](C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\awp1221-button_mode-zoomed.png)
  - [`Full 最大化`](C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\awp1221-full_mode-zoomed.png)
  - [`Full 恢复小窗`](C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\awp1221-full_mode-restored.png)
  - [`Button 恢复小窗`](C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\awp1221-button_mode-restored.png)
  - JSON 几何证据：[`awp1221_error_maximize_validation.json`](C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\awp1221_error_maximize_validation.json)

## 其他检查

- `py -B -m unittest tests.test_full_mode_adc_layout.FullModeAdcLayoutTests.test_awp1221_full_mode_adc_stage_is_not_clipped`：通过。
- `py -B -m compileall -q pages/base_page.py pages/awp1221_page.py`：通过。
- `git diff --check -- pages/base_page.py pages/awp1221_page.py`：通过；仅有现有 LF/CRLF 提示。
- `tests.test_full_mode_adc_layout` 全文件仍有一个与本改动无关的既有通用 `VerticalScrollAreaWidthTests.test_propagate_width_keeps_wide_form_visible` 失败（`234 px`）；AWP1221 Full Mode 用例已由失败变为通过。

## 边界

本结果只声明离线 mock 下的 Tk 布局和截图证据。Type-4 板载预设仍按当前实现提示 `CFG_UNSUPPORTED`，待布局验收后另行处理。

AWP1221_FIX_READY
