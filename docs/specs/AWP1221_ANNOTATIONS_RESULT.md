# AWP1221 注解与显示调整结果

日期：2026-09-29。按用户本轮“直接你来实行”要求直接实现，未分派子智能体。

## 本轮改动

- ADC OVV Time（含 Full Mode 的 press/release OVV）、Zero Time、按压退 bit、松手退 bit 增加现有样式的 `?` 悬停说明；Debounce 原有问号改用 AWP1221 专属说明，保留输入单位 ms。内容按用户给出的含义，中英文均有对应文本。
- ADC 原始十六进制和十进制读数保留，下方新增 bit15→bit0 的两行位视图（位号/值）。尚未读取时值显示 `—`。按当前可见 Stage 的 ADC 精确度高亮恰好 12 位：高 12 位为 bit15–4，低 12 位为 bit11–0；Stage 切换和精确度变化立即更新。深色高亮背景更亮、浅色高亮背景更深。位视图紧凑等宽，避免随原表单宽度伸出可视区。
- GPIO 分类下拉移除 DEBUG_OUTPUT；输出架构由勾选框改为 `Push-Pull` / `OpenDrian` 下拉，默认 OpenDrian（按用户指定拼写）。两个模式共享同一组变量，读回值和默认值相区分；保留 GPIO 编码与保留位。若芯片现有配置为调试功能，以原始代码加 readback 标识保留读回，不再提供调试分类供选择，不擅自替换硬件配置。
- SINC 下新增“振感提供的周期长度”。按 `(Slice 0x12 单点时长 + Slice 0x13 单点时长) × 256` 显示 μs，例如 8、10 对应 4608 μs。与整组完整播放时长分开，不受第三段时长或波深选择影响；输入无效或当前串联组不含 0x12/0x13 时显示 `—`，非 SINC 隐藏。

## 文件与并行工作边界

本轮应用修改集中在 `pages/awp1221_page.py` 和 `config/languages.py`，新增 `tests/test_awp1221_annotations.py`。

工作区本来已有大量未提交修改；执行中同文件又收到外部底栏和主题刷新修改。已保留这些内容，将本轮位视图刷新接入最终唯一主题刷新方法，静态确认两个 AWP1221 页面类没有重名方法。本轮不将其他来源的底栏/主题改动归为自身成果。

`awp1221_annotations_evidence/*.before.txt` 为本轮修改前快照；对应 `.patch` 是快照到当前文件的差异，包含执行期间到达的外部改动，不能直接视为本轮独立补丁。

## 验证

- 最终 `py -3 -m unittest discover -s tests -p 'test_awp1221*.py' -v`：122 项通过，包含新增 6 项离线集成测试。日志：`awp1221_annotations_evidence/tests-final.txt`。
- 新增验证覆盖两个模式、两个 Stage、高低 12 位及示例读数；GPIO 默认值、下拉操作、读回同步、调试读回保留、编码及保留位；问号及悬停窗口；SINC 动态周期、非 SINC 隐藏、无效值；浅→深→浅切换。
- 真实 Tk 离线截图验证 1920×1080 下新位视图 16 位完整显示，保存高/低 12 位和 GPIO 截图。早期位视图拉伸越界的失败已修复，旧日志保留作排查记录，以 tests-final.txt 为准。
- 针对修改文件的 compileall、Git 差异空白检查通过。现有 Tk 测试销毁窗口时可能打印 ThemeChanged 清理提示，最终无测试失败。
- 验证器禁用本地 I2C 桥启动并拦截串口连接、发送及寄存器访问；截图使用模拟值，不是芯片测量结果。未进行硬件验收、EXE 打包、Git 提交或合并。

## 审核截图

- [深色：高 12 位与 SINC 周期](awp1221_annotations_evidence/dark-adc-period.png)
- [浅色：低 12 位与 SINC 周期](awp1221_annotations_evidence/light-adc-low12.png)
- [深色：GPIO 下拉](awp1221_annotations_evidence/dark-gpio.png)

用户审核前保持当前工作区状态，不合并到主分支。
