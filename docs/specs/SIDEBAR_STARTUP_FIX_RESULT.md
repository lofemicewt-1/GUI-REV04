# 首次启动侧栏空白修复结果

状态：`SIDEBAR_FIX_READY`

本次只处理首次启动时左侧导航栏空白的启动时序问题。桥接服务、串口枚举和硬件传输均使用 mock；没有打开串口、读取 I²C、编译或烧录固件。

## 复现与证据

使用真实 `PiezoApplication` 冷启动，未在验证脚本中调用 `geometry`、`update`、缩放、切页或应用视觉设置；仅在启动后的固定回调读取首帧状态并关闭窗口。修复前的结构输出为：

```text
COLD_START {
  root: (1700, 980, True),
  container: (1, 1, False),
  shell: (224, 1, False),
  canvas: (210, 1, False, "0 0 210 476"),
  sidebar: (189, 476, True),
  buttons: all created but not reachable through the unmapped canvas
}
```

同一冷启动流程在一次手动窗口缩放等价的 Tk 几何刷新后，外层立即变为 `1700x980`。这与用户描述的“缩放一下才能显示”一致。

在最终修复前的实际 Windows GDI 首帧截图中，左侧栏为空白；截图已转换为便于查看的 PNG：

- [修复前首帧](C:/Users/wasd1/Desktop/GUI%20REV04/responsive_layout_validation_artifacts/sidebar_cold_start_before_fix.png)
- 原始 BMP：[sidebar_cold_start_before_fix.bmp](C:/Users/wasd1/Desktop/GUI%20REV04/responsive_layout_validation_artifacts/sidebar_cold_start_before_fix.bmp)

## 根因

根因由冷启动事件序列验证：第一次 idle 几何同步发生在顶层窗口 Map 之前，root grid 给导航容器暂时分配 `1x1`；顶层窗口随后 Map 时，内嵌侧栏 Canvas 已处理过早期 Configure，但没有完成自己的 Map。此时内部按钮对象已创建，外层也可能已有尺寸，但 Canvas 仍是未映射状态。用户缩放会再次触发 grid/Configure，因此侧栏出现。

这是 Canvas Map 时序问题，不是品牌文本、按钮文案、串口状态或主题设置问题。

## 修改

在 [`app/navigation.py`](C:/Users/wasd1/Desktop/GUI%20REV04/app/navigation.py) 的 `Navigation` 初始化路径中增加一次性的顶层 `<Map>` 同步：

1. 首次 root Map 后刷新 Tk idle geometry；
2. 幂等地重新声明已有 `sidebar_canvas.grid()` 槽位，促使内嵌 Canvas 完成 Map；
3. 再刷新一次 idle geometry，并同步内容 Canvas 和侧栏 scrollregion。

该路径没有轮询、没有定时重试、没有修改主窗口尺寸，也没有依赖用户缩放。

## 修复后冷启动

仍使用同一 mock 冷启动命令，首帧状态为：

```text
FIRST_FRAME_AFTER_FIX {
  root: (1700, 980, True),
  container: (1700, 980, True),
  sidebar_shell: (224, 980, True),
  sidebar_canvas: (224, 980, True),
  sidebar: (224, 490, True),
  buttons: six buttons, all mapped
}
```

修复后的实际首帧截图已确认品牌和导航按钮绘制：

- [修复后首帧](C:/Users/wasd1/Desktop/GUI%20REV04/responsive_layout_validation_artifacts/sidebar_cold_start_after_fix_1000ms.png)
- 300ms 对照：[sidebar_cold_start_after_fix.png](C:/Users/wasd1/Desktop/GUI%20REV04/responsive_layout_validation_artifacts/sidebar_cold_start_after_fix.png)

另外验证了窗口先隐藏、再首次显示的生命周期：root、container、sidebar Canvas 和六个导航按钮均保持映射。

## 检查

- `py -m compileall -q app`：通过。
- `git diff --check -- app/navigation.py`：通过（仅显示现有 LF/CRLF 提示）。
- 冷启动 mock 回归：修复前红、修复后绿。
- 未执行完整尺寸矩阵；由独立 `sidebar_startup_qa` 任务继续验证小窗口滚动、切页和后续缩放。

## 限制

截图使用离线 mock 和 Windows GDI，不代表真实串口或硬件连接状态；本结果只声明首次导航栏映射和绘制修复，不声明硬件功能验收。

SIDEBAR_FIX_READY
