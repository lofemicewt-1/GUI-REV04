# AWP1018 / AWP1221 Full Mode 布局专项验证

本报告只覆盖后续计划定义的真实 Tk 布局边界：AWP1018 Normal、Diff、Full，AWP1221 Button、Full；每个场景均执行真实 `root.state('zoomed')`，随后恢复到 `1000x700`，并保存顶层窗口实际截图。脚本通过离线 harness mock 桥接、串口和传输入口，不读写 I²C、不写寄存器、不刷固件。

- 检查状态：`FULL_MODE_QA_READY`。
- 场景：5 个模式，每个模式最大化和恢复各 1 张，共 10 张实际截图。
- JSON 证据：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\full_mode_layout_validation.json`。
- `propagate_width` 共享回归探针与页面内部 ADC/多列区域均纳入几何断言。
- 本专项没有写入产品源文件；实现者的并行产品改动不在本报告中归因。

## propagate_width 探针

- overflow=`0px`，area=`680px`，canvas=`680px`，form=`680px`，form request=`680px`，横向滚动条=隐藏。
- 横向视图：`(0.0, 1.0) -> (0.0, 1.0)`。

## 实际截图和布局证据

- `awp1018/awp1018_normal_mode/zoomed`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\full-followup-awp1018-awp1018_normal_mode-zoomed.png`；state=`zoomed`；根窗口 2240×1329；mode canvas 1970×1115；active 填充率 107.9%。
  - ADC stage `stage0` mapped=`True`，stage {'x': 262, 'y': 306, 'width': 937, 'height': 244}，request width=`716`，当前横向溢出=`0px`；body columns=`[{'column': 0, 'mapped': True, 'rect': {'x': 248, 'y': 209, 'width': 965, 'height': 1097}, 'req_width': 744, 'req_height': 1097}, {'column': 1, 'mapped': True, 'rect': {'x': 1225, 'y': 209, 'width': 977, 'height': 1097}, 'req_width': 796, 'req_height': 858}]`。
- `awp1018/awp1018_normal_mode/restored`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\full-followup-awp1018-awp1018_normal_mode-restored.png`；state=`normal`；根窗口 1000×700；mode canvas 1565×458；active 填充率 262.7%。
  - ADC stage `stage0` mapped=`True`，stage {'x': 290, 'y': 334, 'width': 937, 'height': 244}，request width=`716`，当前横向溢出=`0px`；body columns=`[{'column': 0, 'mapped': True, 'rect': {'x': 276, 'y': 237, 'width': 965, 'height': 1097}, 'req_width': 744, 'req_height': 1097}, {'column': 1, 'mapped': True, 'rect': {'x': 1253, 'y': 237, 'width': 977, 'height': 1097}, 'req_width': 796, 'req_height': 858}]`。
  - endpoint 0: region=`(0.0, 0.0, 1984.0, 1219.0)`，Y `(0.0, 0.3757178014766202) -> (0.6242821985233798, 1.0)`，X `(0.0, 0.7888104838709677) -> (0.21118951612903225, 1.0)`，可达=`True/True`。
- `awp1018/awp1018_diff_mode/zoomed`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\full-followup-awp1018-awp1018_diff_mode-zoomed.png`；state=`zoomed`；根窗口 2240×1329；mode canvas 1970×1115；active 填充率 112.7%。
  - ADC stage `stage0` mapped=`True`，stage {'x': 262, 'y': 306, 'width': 937, 'height': 213}，request width=`716`，当前横向溢出=`0px`；body columns=`[{'column': 0, 'mapped': True, 'rect': {'x': 248, 'y': 209, 'width': 965, 'height': 1151}, 'req_width': 744, 'req_height': 1151}, {'column': 1, 'mapped': True, 'rect': {'x': 1225, 'y': 209, 'width': 977, 'height': 1151}, 'req_width': 796, 'req_height': 982}]`。
- `awp1018/awp1018_diff_mode/restored`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\full-followup-awp1018-awp1018_diff_mode-restored.png`；state=`normal`；根窗口 1000×700；mode canvas 1565×458；active 填充率 274.5%。
  - ADC stage `stage0` mapped=`True`，stage {'x': 290, 'y': 334, 'width': 937, 'height': 213}，request width=`716`，当前横向溢出=`0px`；body columns=`[{'column': 0, 'mapped': True, 'rect': {'x': 276, 'y': 237, 'width': 965, 'height': 1151}, 'req_width': 744, 'req_height': 1151}, {'column': 1, 'mapped': True, 'rect': {'x': 1253, 'y': 237, 'width': 977, 'height': 1151}, 'req_width': 796, 'req_height': 982}]`。
  - endpoint 0: region=`(0.0, 0.0, 1984.0, 1273.0)`，Y `(0.5978004713275726, 0.9575805184603299) -> (0.6402199528672428, 1.0)`，X `(0.21118951612903225, 1.0) -> (0.21118951612903225, 1.0)`，可达=`True/True`。
- `awp1018/full_mode/zoomed`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\full-followup-awp1018-full_mode-zoomed.png`；state=`zoomed`；根窗口 2240×1329；mode canvas 1984×1115；active 填充率 98.6%。
  - ADC stage `stage0` mapped=`True`，stage {'x': 262, 'y': 252, 'width': 816, 'height': 244}，request width=`716`，当前横向溢出=`0px`；body columns=`[{'column': 0, 'mapped': True, 'rect': {'x': 248, 'y': 155, 'width': 850, 'height': 1047}, 'req_width': 758, 'req_height': 278}, {'column': 1, 'mapped': True, 'rect': {'x': 1110, 'y': 155, 'width': 563, 'height': 1047}, 'req_width': 378, 'req_height': 278}, {'column': 2, 'mapped': True, 'rect': {'x': 1685, 'y': 155, 'width': 531, 'height': 1047}, 'req_width': 392, 'req_height': 597}]`。
  - nested scroll 0: canvas 836×1033，content request 744×1097，hscroll=显示，propagate_width=`True`。
  - nested scroll 1: canvas 563×1033，content request 318×180，hscroll=显示，propagate_width=`False`。
  - nested scroll 2: canvas 517×534，content request 560×815，hscroll=显示，propagate_width=`False`。
- `awp1018/full_mode/restored`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\full-followup-awp1018-full_mode-restored.png`；state=`normal`；根窗口 1000×700；mode canvas 1565×458；active 填充率 141.7%。
  - ADC stage `stage0` mapped=`True`，stage {'x': 290, 'y': 280, 'width': 816, 'height': 244}，request width=`716`，当前横向溢出=`0px`；body columns=`[{'column': 0, 'mapped': True, 'rect': {'x': 276, 'y': 183, 'width': 850, 'height': 597}, 'req_width': 758, 'req_height': 278}, {'column': 1, 'mapped': True, 'rect': {'x': 1138, 'y': 183, 'width': 563, 'height': 597}, 'req_width': 378, 'req_height': 278}, {'column': 2, 'mapped': True, 'rect': {'x': 1713, 'y': 183, 'width': 531, 'height': 597}, 'req_width': 392, 'req_height': 597}]`。
  - nested scroll 0: canvas 836×583，content request 744×1097，hscroll=显示，propagate_width=`True`。
  - nested scroll 1: canvas 563×583，content request 318×180，hscroll=显示，propagate_width=`False`。
  - nested scroll 2: canvas 517×264，content request 560×815，hscroll=显示，propagate_width=`False`。
  - endpoint 0: region=`(0.0, 0.0, 1984.0, 1273.0)`，Y `(0.6402199528672428, 1.0) -> (0.6402199528672428, 1.0)`，X `(0.21118951612903225, 1.0) -> (0.21118951612903225, 1.0)`，可达=`True/True`。
- `awp1221/button_mode/zoomed`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\full-followup-awp1221-button_mode-zoomed.png`；state=`zoomed`；根窗口 2240×1329；mode canvas 1984×1115；active 填充率 98.6%。
  - ADC stage `button_stage0` mapped=`True`，stage {'x': 262, 'y': 383, 'width': 930, 'height': 205}，request width=`658`，当前横向溢出=`0px`；body columns=`[{'column': 0, 'mapped': True, 'rect': {'x': 248, 'y': 209, 'width': 972, 'height': 993}, 'req_width': 392, 'req_height': 264}, {'column': 1, 'mapped': True, 'rect': {'x': 1232, 'y': 209, 'width': 984, 'height': 993}, 'req_width': 674, 'req_height': 599}]`。
  - nested scroll 0: canvas 958×993，content request 734×1270，hscroll=隐藏，propagate_width=`False`。
  - nested scroll 1: canvas 461×513，content request 474×686，hscroll=显示，propagate_width=`False`。
- `awp1221/button_mode/restored`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\full-followup-awp1221-button_mode-restored.png`；state=`normal`；根窗口 1000×700；mode canvas 1543×458；active 填充率 153.9%。
  - ADC stage `button_stage0` mapped=`True`，stage {'x': 290, 'y': 411, 'width': 930, 'height': 205}，request width=`658`，当前横向溢出=`0px`；body columns=`[{'column': 0, 'mapped': True, 'rect': {'x': 276, 'y': 237, 'width': 972, 'height': 599}, 'req_width': 392, 'req_height': 264}, {'column': 1, 'mapped': True, 'rect': {'x': 1260, 'y': 237, 'width': 984, 'height': 599}, 'req_width': 674, 'req_height': 599}]`。
  - nested scroll 0: canvas 958×599，content request 734×1270，hscroll=隐藏，propagate_width=`False`。
  - nested scroll 1: canvas 461×277，content request 474×686，hscroll=显示，propagate_width=`False`。
  - endpoint 0: region=`(0.0, 0.0, 1984.0, 721.0)`，Y `(0.0, 0.6352288488210819) -> (0.3647711511789182, 1.0)`，X `(0.0, 0.7777217741935484) -> (0.22227822580645162, 1.0)`，可达=`True/True`。
  - endpoint 1: region=`(0.0, 0.0, 958.0, 1286.0)`，Y `(0.0, 0.46578538102643857) -> (0.5342146189735615, 1.0)`，X `(0.0, 1.0) -> (0.0, 1.0)`，可达=`True/True`。
  - endpoint 2: region=`(0.0, 0.0, 474.0, 702.0)`，Y `(0.0, 0.3945868945868946) -> (0.6054131054131054, 1.0)`，X `(0.0, 0.9725738396624473) -> (0.027426160337552744, 1.0)`，可达=`True/True`。
- `awp1221/full_mode/zoomed`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\full-followup-awp1221-full_mode-zoomed.png`；state=`zoomed`；根窗口 2240×1329；mode canvas 1984×1115；active 填充率 98.6%。
  - ADC stage `stage0` mapped=`True`，stage {'x': 262, 'y': 332, 'width': 1231, 'height': 561}，request width=`800`，当前横向溢出=`0px`；body columns=`[{'column': 0, 'mapped': True, 'rect': {'x': 248, 'y': 158, 'width': 1273, 'height': 1044}, 'req_width': 842, 'req_height': 264}, {'column': 1, 'mapped': True, 'rect': {'x': 1533, 'y': 158, 'width': 683, 'height': 1044}, 'req_width': 683, 'req_height': 699}]`。
  - nested scroll 0: canvas 1259×1044，content request 828×1626，hscroll=隐藏，propagate_width=`True`。
  - nested scroll 1: canvas 285×573，content request 329×641，hscroll=显示，propagate_width=`False`。
- `awp1221/full_mode/restored`：`C:\Users\wasd1\Desktop\GUI REV04\responsive_layout_validation_artifacts\full-followup-awp1221-full_mode-restored.png`；state=`normal`；根窗口 1000×700；mode canvas 1543×458；active 填充率 164.6%。
  - ADC stage `stage0` mapped=`True`，stage {'x': 290, 'y': 360, 'width': 1231, 'height': 561}，request width=`800`，当前横向溢出=`0px`；body columns=`[{'column': 0, 'mapped': True, 'rect': {'x': 276, 'y': 186, 'width': 1273, 'height': 699}, 'req_width': 842, 'req_height': 264}, {'column': 1, 'mapped': True, 'rect': {'x': 1561, 'y': 186, 'width': 683, 'height': 699}, 'req_width': 683, 'req_height': 699}]`。
  - nested scroll 0: canvas 1259×699，content request 828×1626，hscroll=隐藏，propagate_width=`True`。
  - nested scroll 1: canvas 285×366，content request 329×641，hscroll=显示，propagate_width=`False`。
  - endpoint 0: region=`(0.0, 0.0, 1984.0, 770.0)`，Y `(0.34155844155844156, 0.9363636363636364) -> (0.4051948051948052, 1.0)`，X `(0.22227822580645162, 1.0) -> (0.22227822580645162, 1.0)`，可达=`True/True`。
  - endpoint 1: region=`(0.0, 0.0, 1259.0, 1642.0)`，Y `(0.0, 0.42570036540803896) -> (0.574299634591961, 1.0)`，X `(0.0, 1.0) -> (0.0, 1.0)`，可达=`True/True`。
  - endpoint 2: region=`(0.0, 0.0, 329.0, 657.0)`，Y `(0.0, 0.5570776255707762) -> (0.4429223744292237, 1.0)`，X `(0.0, 0.8662613981762918) -> (0.1337386018237082, 1.0)`，可达=`True/True`。

## 问题

- 未发现本阶段定义的最大化、恢复、ADC 表单压缩、内部多列不可达或 propagate_width 问题。

## 结论

FULL_MODE_QA_READY
