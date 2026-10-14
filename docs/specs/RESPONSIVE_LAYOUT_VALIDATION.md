# Responsive Layout Validation

本报告由 `tests/validate_responsive_layout.py` 生成。验证只构造真实 Tk 页面并 mock 桥接、串口枚举和传输入口；没有启动桥接服务、打开串口、读取 I²C、编译或烧录固件。

- 矩阵记录：146 条（窗口 7 种；代表场景 DPI 1.0, 1.25, 1.5, 2.0）。
- 错误：0；警告：0。
- JSON 证据：[responsive_layout_matrix.json](C:/Users/wasd1/Desktop/GUI REV04/responsive_layout_validation_artifacts/responsive_layout_matrix.json)。
- 已完成尺寸矩阵基线：[responsive_layout_matrix_baseline.json](C:/Users/wasd1/Desktop/GUI REV04/responsive_layout_validation_artifacts/responsive_layout_matrix_baseline.json)；本轮只复用其记录，不复用旧问题列表。
- 当前产品布局代码 fingerprint：`c1db6bfd90edeeb34c9915ff79569ec2cd01da81412e836d72192d10c3814482`。
- DPI 说明：矩阵中的 `scale` 是 Tk logical scaling（`tk scaling`），用于逻辑字号/尺寸回归；本次离线验证没有修改或声称覆盖 Windows 系统显示 DPI。

## 覆盖清单

- top_level_pages: ["home", "awp1921_1923", "awp1922", "awp1018", "awp1221", "settings"]
- mode_pages: {"awp1921_1923": ["button_mode", "pump_mode", "full_mode"], "awp1018": ["awp1018_normal_mode", "awp1018_diff_mode", "full_mode"], "awp1221": ["button_mode", "full_mode"]}
- notebooks: ["AWP1018 normal/diff ROM waveform selector", "AWP1221 waveform/ROM tools"]
- custom_windows: ["ContinuousRegisterReadDialog", "AWP1018 ROM selector", "AWP192x waveform generator", "AWP192x ADC detect"]
- floating_overlays: ["SimpleTooltip", "AWP192x preset tooltip"]

## 动态交互覆盖

- Notebook 实测 `select()` 标签数：8。每个当前模式中的实际 Notebook tab 均被选中、更新并检查映射状态。
- Tooltip 实测数量：128。逐个发送 Enter/Leave，检查窗口创建、屏幕边界与销毁。
- 侧栏 footer：2 个场景；在滚动终点检查“使用说明”和版本文本可达。
- 嵌套滚轮：4 个事件场景；普通滚轮与 Shift+滚轮要求最近的单一滚动拥有者响应一次。
- 动态补测场景：640×480、Tk scaling 1.0 与 2.0，中文浅色设置；Notebook/内部几何/Tooltip/滚轮均为当前运行实测。
- 静态清单与上述动态实测分开记录，避免把源码存在性当作交互覆盖。

## 问题与证据

- 未发现布局错误或警告。

## 代表性截图

- `small-awp192x-full`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\small-awp192x-full.bmp`（已保存）
- `small-awp1018-diff`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\small-awp1018-diff.bmp`（已保存）
- `small-awp1221-button`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\small-awp1221-button.bmp`（已保存）

## 结论

LAYOUT_VALIDATION_READY_V2
