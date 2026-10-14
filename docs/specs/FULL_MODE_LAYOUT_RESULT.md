# AWP1018 / AWP1221 Full Mode 布局跟进结果

状态：`FULL_MODE_LAYOUT_READY`

本阶段只处理 Full Mode 的窗口布局、内容可达性和模式切换重算。没有修改 Type-4 预设路由、寄存器/协议、侧栏启动逻辑或硬件访问。

## 根因

- AWP1018 与前序 AWP1221 一样，固定页脚页面的外层 `VerticalScrollArea` 只跟随 Canvas 视口尺寸，嵌入模式页仍停留在自然请求高度。最大化后短的 Full Mode 因此挤在上方，下面留下空白。
- `VerticalScrollArea(propagate_width=True)` 在默认水平滚动模式下被提前返回，宽表单的自然请求宽度不能传递给外层 Canvas。通用布局用例因此出现 234 px 的实际溢出。
- AWP1018 Full Mode 左 ADC 列原来是 `weight=0`、最小 760 px，外层有剩余空间时仍保持固定宽度，内层滚动区会在普通桌面宽度下变窄。

## 产品修改

- `pages/base_page.py`
  - 为 `VerticalScrollArea` 和 `BasePage.make_vertical_scroll_area` 增加可选 `propagate_height`。
  - 启用时将短内容窗口填充到当前视口，同时继续按自然高度保留超高内容的纵向滚动。
  - `propagate_width=True` 现在也适用于水平滚动区，宽内容的 Canvas 请求宽度可以参与父级网格布局。
- `pages/awp1018_page.py`
  - 外层模式滚动区启用 `propagate_height=True`。
  - Normal/Diff/Full 切换后在 Tk 空闲几何计算完成时重算外层滚动区；Normal/Diff 保留滚动范围，短 Full 在最大化时重新填充视口。
  - Full Mode 左 ADC 列保留 760 px 安全最小宽度并使用 `weight=2`，与中、右列共同分配剩余宽度。
- `pages/awp1221_page.py`
  - 保留前序已验收的高度传播、模式切换重算和 Full 左 ADC 列伸展修复；本阶段没有改变其协议或 Type-4 行为。

## 回归证据

```text
py -B -m unittest tests.test_full_mode_adc_layout -v
Ran 3 tests ... OK

py -B tests/validate_awp1221_maximize.py
status=AWP1221_QA_READY
observations=4 errors=0

py -B -m compileall -q pages/base_page.py pages/awp1018_page.py pages/awp1221_page.py
exit=0

git diff --check -- pages/base_page.py pages/awp1018_page.py pages/awp1221_page.py
exit=0
```

专项几何检查使用 mock 串口/桥接的真实 Tk 页面，没有连接串口、读写 I2C、编译或烧录硬件：

- AWP1018 最大化模式切换：Normal 实际高度 1203 px、纵向滚动可见；Diff 实际高度 1257 px、纵向滚动可见；Full 实际高度 1099 px，视口 1115 px，纵向滚动隐藏。
- AWP1018 恢复到 1000×700 后，Full 实际模式页为 1592×649，外层 Canvas 为 1565×458，横向和纵向滚动均显示；Normal 请求高度 1203 px，滚动区域为 `0 0 1608 1219`，底部内容仍可滚动到达。
- AWP1018 Full 在 1366×768 稳定布局中，三列宽度为 761/384/397 px；左 ADC 内层 Canvas 与内容均为 747 px，未产生内部横向滚动截断。
- AWP1221 专项仍覆盖 Button/Full 最大化和恢复小窗，4 个场景 0 个错误；详细报告和截图见 `AWP1221_ERROR_MAXIMIZE_VALIDATION.md` 与 `responsive_layout_validation_artifacts/`。

独立 QA 仍需补充本阶段 AWP1018 Normal/Diff/Full、AWP1221 Button/Full 的最终截图和专项报告；该实现结果不代替 `FULL_MODE_LAYOUT_VALIDATION.md`。

FULL_MODE_LAYOUT_READY
