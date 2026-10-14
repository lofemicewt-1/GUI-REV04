# 按钮按压样式实施结果

BUTTON_STYLE_READY_FOR_ACCEPTANCE_V2
BUTTON_STYLE_READY_FOR_ACCEPTANCE

执行代理：GPT-5.6 Luna Max（按任务分配）

本轮只在共享主题文件 `styles/theme.py` 补充按钮按压验收所需的状态绘制修正，保留并行视觉排版任务已有的按钮尺寸和页面排版改动。普通、`Selected.TButton`、`BgSelected.TButton`、`SidebarSelected.TButton` 的按压图像现在保留深色外圈，并在上/左内缘加入内阴影；pressed 图像不再使用向外投影。自定义图像布局恢复 `Button.border` 外层，使边框连续，不出现九宫格底边断续纹理。

选中态继续使用按压图像并保持，取消选中仍由原有页面逻辑恢复普通样式。禁用状态在自定义 image element、普通 foreground/relief 映射及回退映射中优先于 pressed/active，因此 disabled+active/pressed 不会恢复蓝色按压态。普通命令回调、禁用行为和页面逻辑未改动。

自定义 image 布局将 `relief` 保持为 `flat`，因为 ttk 的 `sunken` relief 在该 image element 上只影响边缘绘制并会产生重复纹理；实际凹陷感由深色外圈和上/左内阴影提供。图像创建失败时，原生回退样式仍使用 `sunken` pressed relief 和相同的禁用优先级。

修改文件：

- `styles/theme.py`

离线视觉证据：

- `outputs/BUTTON_PRESSED_STYLE_EVIDENCE.png`：由本机真实 Tk 窗口捕获的 760×250 状态对比，包含普通、按压、选中、禁用四个 `ttk.Button`。

已执行验证：

- `py -m compileall -q styles app pages`：通过。
- `git diff --check`：通过。
- Tk 按钮状态验证：`Secondary.TButton` 命令回调执行一次；禁用后再次 invoke 不执行。
- disabled 优先级验证：`Selected.TButton` 在 disabled+pressed+active 组合下返回面板底色、低对比度文字和 `flat`；普通按钮返回面板底色、禁用文字和 `flat`。
- 布局验证：共享按钮样式布局根元素为 `Button.border`；按压图像像素显示深色左/上内缘，且无 pressed 外投影。
- 真实 Tk 状态图：四种状态均成功渲染并保存到证据 PNG，按压和选中按钮显示连续深色外圈。

限制：验证使用本机 Tk 离线实例和窗口截图，没有连接串口、访问硬件，也没有进行人工真实鼠标按住时长的验收。工作区存在其他并行任务和既有未提交修改，本轮未覆盖或清理它们。
