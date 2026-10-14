# Responsive layout validation feedback

阶段性静态/离线 Tk 反馈，供 `layout_impl` 在最终验证前处理：

- 在 640x480 下，主窗口当前已经能够缩小，页面和模式的外层/内层滚动条均可到达内容边界；已生成 44 条页面/模式记录和 3 张 BMP 截图，当前这组检查无错误。
- 初次检查发现 `fit_toplevel_to_screen()` 会把 AWP1018 ROM selector 与 AWP192x waveform generator 缩到约 608x384；实现者随后已在当前工作树给弹窗内容增加嵌套 `VerticalScrollArea`。最终矩阵仍需确认滚动条端点和主题刷新均通过。
- 验证构造始终 mock `LocalI2cBridge.start/close`、串口枚举、连接、发送、读写和断开；未打开串口、未启动桥接、未做 I2C/硬件 I/O。

## 当前补测（layout implementation fingerprint `98dff8232ab7d55488f03920341cdeab2d1d91d29b6af47a36ae3892b262c698`）

补测脚本复用已完成的 146 条尺寸矩阵记录，但将其保存为独立的 `responsive_layout_matrix_baseline.json`，不复用旧问题列表；本轮在当前代码上单独运行 640×480、Tk scaling 1.0/2.0 的动态批次。

已确认通过：

- 6 个顶层页面、全部已注册模式；
- 实际 Notebook tab 全部调用 `Notebook.select()`：8 次选择记录，选中 tab 均映射；
- Tooltip 实测 128 次 Enter/Leave，窗口创建、屏幕边界和销毁均通过；
- 侧栏滚动到底后“使用说明”和版本文本均可达（1.0/2.0 两个场景）；
- 嵌套普通滚轮与 Shift+滚轮在 1.0 场景各由最近的单一滚动拥有者响应一次；2.0 场景无水平溢出时明确记录跳过；
- 本轮未进行桥接、串口、I²C、硬件或 Windows 系统 DPI 修改；`scale` 仅是 Tk logical scaling。

当前阻塞项（8 条，均为当前代码在 Tk scaling=2.0、640×480、AWP1018 diff mode 的固定容器内部裁切证据）：

- `pages/awp1018_page.py:1714` 的 `_build_diff_channel_status` 为每个通道创建固定 `tk.Frame(height=54)` 并关闭 geometry propagation。当前布局中四组通道标题/数值 Label 的请求尺寸为 `[71,47]` / `[26,48]`，实际分配为 `[71,26]` / `[26,26]`；父框实际高度为 26。文字会被固定父容器垂直裁切。
- 受影响控件是 `frame9`、`frame9.frame2`、`frame9.frame3`、`frame9.frame4` 下的标题和值 Label；证据完整记录在 `responsive_layout_matrix.json` 的 `widget-request-clipping:awp1018:awp1018_diff_mode` 条目中，并带有 `scale=2.0`。
- 已查看当前版本截图：`responsive_layout_validation_artifacts/scale2-awp1018-diff.bmp`、`responsive_layout_validation_artifacts/scale2-awp1221-full.bmp`；640×480 下大字号内容需要滚动，AWP1018 diff 的固定通道状态区仍由上述几何证据阻塞。

AWP1221 在隐藏 Notebook tab 过滤修正后未再报误；旧动态运行的重复错误已从本轮计数中排除，当前报告只包含本次运行的 8 条问题。

这份反馈只记录验证发现；产品修改由 `layout_impl` 负责。
