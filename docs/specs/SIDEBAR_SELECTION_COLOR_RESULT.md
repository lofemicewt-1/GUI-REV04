# 芯片与模式选中颜色统一结果

日期：2026-09-29
状态：实现与离线验证完成；未提交、未合并。等待审核。

仅离线视觉验证，无硬件操作。`LocalI2cBridge.start` 与串口枚举被替换；`connect` / `send_command` / 寄存器读写未被真实打开。验证调用：start=1，枚举=24，被拦截的串口 I/O=0。

## 变更

可移植应用补丁只有 `styles/theme.py`。模式选中图直接复用芯片选中图，因此填充、边框、内阴影和圆角父色一致。

- 浅色选中：`#1D4ED8`，边框 `#1E40AF`
- 深色选中：`#3B82F6`，边框 `#1E3A8A`
- 未选中模式仍为 `#2871F3` / `#2871FA`
- 禁用优先：模式禁用填充回到未选中蓝，不显示选中色
- 回退样式路径原本就使用 `selected_bg` / `selected_edge`，未改

1221、1018、1921/1923 共用 `SidebarSelected` / `SidebarModeSelected`，一次修改同时生效。按钮文字、样式名和页面访问尺寸与改前一致。

当前分支仍是 `awp1221-realtime-threshold-bands`。工作区有大量无关未提交修改，因此没有新建 `codex/` 分支，也没有暂存或提交。

## 核对

改前模式选中中心：浅色 `#252DCE`，深色 `#4128DA`，与芯片选中不同。
改后浅色/深色选中图像素完全相同。截图填充采样同为浅色 `(29,78,216)`、深色 `(59,130,246)`。light → dark → light 后仍一致。

selected / active / pressed 保持芯片蓝。芯片禁用为父色 `#F9FAFB`；模式禁用为未选中蓝 `#2871F3`。

证据：`docs/specs/sidebar_selection_color_evidence/before` 与 `after`。
审核图：

- `after/light-awp1221-button_mode.png`
- `after/light-awp1018-awp1018_normal_mode.png`
- `after/light-awp1921_1923-button_mode.png`
- `after/dark-awp1221-button_mode.png`

`py -3 -m unittest tests.test_sidebar_selection_color -v` 通过。
`py -3 tests/validate_sidebar_selection_color.py --label after --expect-match` 无失败。

## 后续调整

深色选中填充改为 Logo 蓝 `#2871FA`，文字 `#05070D`；未选中模式填充 `#173B70`，白字。边框 `#173B70`，内阴影保留。截图字形对比度均不低于 4.5:1。浅色选中未改。
