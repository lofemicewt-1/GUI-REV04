# 跨电脑启动修复与迁移说明

日期：2026-09-20。直接执行，未使用子智能体；没有连接串口、写寄存器或修改固件。

## 本次交付

- 新版单文件程序：`dist/portable/AnalogwinPiezoGUI.exe`。
- 迁移压缩包：`dist/AnalogwinPiezoGUI-portable.zip`，包含新版 EXE 和本说明。
- 源码启动：项目根目录 `run_gui.bat` 已修复。
- 打包脚本：`build_exe.bat` 已修复。默认仍输出到 `dist/AnalogwinPiezoGUI.exe`。

**本次使用 portable 子目录，是因为打包时旧 `dist/AnalogwinPiezoGUI.exe` 正在运行，Windows 拒绝覆盖。迁移时请使用 portable 目录内的新版，或本次交付的 ZIP。原位置的旧 EXE 没有被替换。**

新版 EXE SHA-256：

```text
BA08B57F77A243E6F657976D76D19FE827FA5610C45503087C7B330882468BA3
```

## 原因与改动

实际复现的卡死原因在 `styles/fonts.py`：加载应用私有字体后，调用 `SendMessageW(HWND_BROADCAST, WM_FONTCHANGE, ...)`，同步等待其他程序的窗口处理消息。如果其中一个窗口不处理消息，本程序会在主窗口初始化阶段一直等待，既没有进入事件循环，也没有抛出异常，因此没有错误弹窗。

复现方法：创建一个真实但停止处理消息的测试窗口，再在独立进程中启动完整 GUI。修复前 15 秒超时，8 秒时采集到的主线程堆栈指向 `register_bundled_fonts` 中的 `SendMessageW`。删除该广播后同一测试通过，保留私有字体加载和现有字体回退。

依据：[微软 SendMessageW 文档](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagew)说明同步调用等待目标处理完成；[AddFontResourceExW 文档](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-addfontresourceexw)说明 `FR_PRIVATE` 字体仅供调用进程使用。

同时修复启动失败的可见性和启动器检查：

- Windows 启动异常直接使用原生错误弹窗，避免 Tk 本身损坏时连错误提示也无法显示。
- 日志先写程序旁边；没有写权限时依次尝试 `%LOCALAPPDATA%\AnalogwinPiezoGUI`、系统临时目录的 `AnalogwinPiezoGUI` 子目录。
- 丢弃失效的 `PYTHON_EXE` 设置，筛选 Python 3.10+ 且能导入 tkinter 的解释器。
- 依赖检查显示当前步骤，校验实际需要的 pyserial API；自动安装 pyserial 增加网络超时、重试限制及整体时间上限。
- 两个 BAT 使用 `pushd` 进入项目目录，支持网络共享路径映射，并在失败时保留控制台提示。
- 打包允许通过 `GUI_DIST_DIR` 指定输出目录；EXE 占用导致构建失败时提示关闭旧程序。

## 换电脑如何运行

### 推荐：使用 EXE

1. 把 `dist/AnalogwinPiezoGUI-portable.zip` 复制到目标电脑。
2. 完整解压，然后双击 `AnalogwinPiezoGUI.exe`。
3. EXE 自带 Python、Tcl/Tk、pyserial、图片和字体，不需要另装 Python。

此产物是 Windows x64 程序，本次在当前 Windows 主机验证。其他操作系统或不同 CPU 架构没有验收。

### 使用 run_gui.bat

需要复制整个源码项目，不能只复制 BAT；至少保留 `main.py`、`run_gui.bat`、`requirements.txt` 和 `app/assets/build_support/config/core/pages/styles` 文件夹。

目标电脑需要 Python 3.10+，安装时包含 Tcl/Tk 与 Python 启动器。双击 `run_gui.bat` 后会显示解释器和依赖检查过程；缺少 pyserial 时会尝试联网安装。离线机器应事先安装 pyserial，或者直接使用 EXE。

### 重新生成 EXE

关闭旧 EXE 后运行 `build_exe.bat`，成功输出为 `dist/AnalogwinPiezoGUI.exe`。也可以在命令提示符中指定输出目录：

```bat
set "GUI_DIST_DIR=dist\portable"
build_exe.bat
```

启动失败时，原生弹窗会给出异常及实际日志路径。底层 EXE 解压或加载失败发生在 Python 入口之前，不由 `main.py` 错误处理覆盖。

## 验证记录与边界

- `py -3 -m unittest discover -s tests -v`：10 项通过。
- 字体卡死回归：真实不响应窗口存在时，完整 GUI 能进入事件循环并关闭。
- Tk 不可导入且无 stderr 时仍调用原生弹窗；程序目录写日志失败时写到用户目录：通过。
- 新版 EXE 重新打包成功，Python 3.11.4 / PyInstaller 6.20.0 / Windows x64。
- 单独复制 EXE 到中文及空格目录，从不同工作目录启动：主窗口可见、响应 Windows 消息、关闭后进程正常退出。
- 上述 EXE 检查将 PATH 限制为 System32，并把 PYTHONHOME、PYTHONPATH、TCL_LIBRARY、TK_LIBRARY 指向不存在路径：通过。
- 打包 EXE 在另一个窗口不响应时同样通过启动、响应和关闭检查。
- 将源码运行所需文件复制到中文及空格目录，通过 `run_gui.bat` 从不同工作目录启动：通过。
- EXE 的初次隔离环境检查受进程访问权限限制；随后在普通 Windows 权限环境重测通过，已清理本次测试进程。
- `git diff --check` 通过。保留原有未提交改动。

**尚未在用户所指的另一台实体电脑上实测；以上是本机真实 GUI、迁移目录和环境隔离检查，不等于目标电脑或硬件验收。**

可重复的启动检查工具：`build_support/check_windows_launch.py`。它只识别本次启动进程及其子进程的窗口，不关闭用户已运行的其他 GUI。
