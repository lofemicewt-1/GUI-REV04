# AWP1221 错误与最大化布局专项验证

本报告只覆盖当前阶段的 AWP1221 Button Mode / Full Mode 窗口布局。验证构造真实 Tk 页面，调用真实 `root.state('zoomed')`、恢复 `normal` 尺寸，并对每个状态保存实际顶层窗口截图；桥接、串口和传输入口全部 mock，没有写寄存器、读 I²C、刷固件或打包 EXE。

- 检查状态：`AWP1221_QA_READY`。
- 实测场景：4 个（Button/Full 最大化各 1，Full/Button 恢复小窗口各 1）。
- JSON 证据：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\awp1221_error_maximize_validation.json`。
- 本阶段明确暂停：Type-4 预设按钮回调回归、侧栏冷启动/隐藏重显示和旧全矩阵；它们不计入本报告结论。

## 实际截图

- `button_mode/zoomed`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\awp1221-button_mode-zoomed.png`（已保存，已目视检查）
- `full_mode/zoomed`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\awp1221-full_mode-zoomed.png`（已保存，已目视检查）
- `full_mode/restored`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\awp1221-full_mode-restored.png`（已保存，已目视检查）
- `button_mode/restored`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\awp1221-button_mode-restored.png`（已保存，已目视检查）

## 几何与可达性

- `button_mode/zoomed`：Tk state=`zoomed`，根窗口 2240×1329，mode scroll 区 1984×1115，内容请求高度 705，vscroll=隐藏, active mode 填充率 98.6%。
- `full_mode/zoomed`：Tk state=`zoomed`，根窗口 2240×1329，mode scroll 区 1984×1115，内容请求高度 754，vscroll=隐藏, active mode 填充率 98.6%。
- `full_mode/restored`：Tk state=`normal`，根窗口 1000×700，mode scroll 区 1557×472，内容请求高度 754，vscroll=显示。
  - 恢复窗口滚动终点：`(0.0, 0.5948051948051948) -> (0.4051948051948052, 1.0)`，可达=`True`。
  - 恢复窗口横向终点：`(0.0, 0.7777217741935484) -> (0.22227822580645162, 1.0)`，可达=`True`。
- `button_mode/restored`：Tk state=`normal`，根窗口 1000×700，mode scroll 区 1557×472，内容请求高度 705，vscroll=显示。
  - 恢复窗口滚动终点：`(0.3647711511789182, 1.0) -> (0.3647711511789182, 1.0)`，可达=`True`。
  - 恢复窗口横向终点：`(0.22227822580645162, 1.0) -> (0.22227822580645162, 1.0)`，可达=`True`。

## 问题

- 未发现本阶段定义的最大化、恢复、内容占高或滚动终点问题。

## 目视结论

AWP1221_QA_READY
