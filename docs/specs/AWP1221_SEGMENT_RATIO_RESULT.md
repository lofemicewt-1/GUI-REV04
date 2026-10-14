# AWP1221 分段比例实施结果

日期：2026-09-28  
状态：软件实现已交付验收，硬件未验证。

本会话模型是 grok-4.7，不能派发指南要求的 5.6 Luna / Max，也没有创建子代理。用户直接要求实现指南，因此由本会话完成代码、测试和证据，而不是替换成未授权模型后假装由执行代理完成。

## 更改文件

- `core/awp1221_sram.py`
- `pages/awp1221_sram_editor.py`
- `tests/test_awp1221_sram.py`
- `tests/test_awp1221_sram_ui.py`
- `docs/specs/awp1221_segment_ratio_evidence/geometry.json`
- `docs/specs/awp1221_segment_ratio_evidence/segment_ratio_default.bmp`
- `docs/specs/awp1221_segment_ratio_evidence/segment_ratio_minimum.bmp`
- `docs/specs/awp1221_segment_ratio_evidence/segment_ratio_zoom.bmp`

未修改 SRAM 编码、描述符、SAMPLE_TIME 寄存器语义、VPK/OFFSET、ROM/SINC 或固件。未连接真实串口。未提交 git。

## 验证

```powershell
py -3 -m unittest discover -s tests -p 'test_awp1221_sram*.py' -v
```

退出码：0。46 tests，OK。

```powershell
git -c core.safecrlf=false diff --check
```

退出码：0。该命令不覆盖未跟踪文件。已额外检查本次未跟踪源文件和本文，无行尾空白或制表符。

离线截图与几何记录：

- `docs/specs/awp1221_segment_ratio_evidence/segment_ratio_default.bmp`
- `docs/specs/awp1221_segment_ratio_evidence/segment_ratio_minimum.bmp`
- `docs/specs/awp1221_segment_ratio_evidence/segment_ratio_zoom.bmp`
- `docs/specs/awp1221_segment_ratio_evidence/geometry.json`

## 审核返工

合法比例但无法压缩时不再设置 `_sram_input_invalid`。幅值仍可拖动；写入保持禁用，直到重新生成成功。非法比例文本仍然禁止拖动和写入。

窄窗口改为上下排列，并限制表单高度，避免波形区被挤没。横轴和纵轴标签重叠时丢掉后画的标签。比例行、起始点数和地址标签改为纵向排列，避免在 285px 栏里被裁切。

补充验证：Button/Full 页面、6 段和 7 段比例行、逐项滚进可视区域、横轴标签不重叠。新截图在 `docs/specs/awp1221_segment_ratio_evidence/review/`，未覆盖原先三张底图。

复跑：

```powershell
py -3 -m unittest discover -s tests -p 'test_awp1221_sram*.py' -v
```

退出码：0。48 tests，OK。

## 未解决

- 真实 SRAM 写入和示波器时序未运行，需要另行授权。软件测试和截图不能代替硬件波形。
- 销毁已显示的离线 Tk 窗口时，控制台可能打印一次非失败的 `ttk::ThemeChanged` / `application has been destroyed`。对应用例仍为 ok，未发现功能失败。

## 交付标志

以下标志只表示交付验收，不表示验收已通过：

```text
AWP1221_SEGMENT_RATIO_CORE_READY
AWP1221_SEGMENT_RATIO_UI_READY
AWP1221_SEGMENT_RATIO_READY_FOR_REVIEW
```
