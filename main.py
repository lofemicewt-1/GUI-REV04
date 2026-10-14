from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path


def crash_log_path(
    *,
    frozen: bool | None = None,
    executable: str | None = None,
    script: str | None = None,
) -> Path:
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        return Path(executable or sys.executable).resolve().with_name(
            "AnalogwinPiezoGUI.error.log"
        )
    return Path(script or __file__).resolve().with_name("AnalogwinPiezoGUI.error.log")


def _report_startup_failure(exc: BaseException) -> None:
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    paths = [crash_log_path()]
    if os.environ.get("LOCALAPPDATA"):
        paths.append(Path(os.environ["LOCALAPPDATA"]) / "AnalogwinPiezoGUI" / "AnalogwinPiezoGUI.error.log")
    paths.append(Path(tempfile.gettempdir()) / "AnalogwinPiezoGUI" / "AnalogwinPiezoGUI.error.log")
    location = "(could not write log file)"
    for path in paths:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            location = str(path)
            break
        except OSError:
            continue
    message = f"程序启动失败 / Startup failed.\n\n{location}\n\n{text[-2000:]}"
    # A missing/broken Tk runtime must not also break the error dialog.
    if sys.platform == "win32":
        try:
            import ctypes

            dialog = ctypes.windll.user32.MessageBoxW
            dialog.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
            dialog.restype = ctypes.c_int
            if dialog(None, message, "AnalogwinPiezoGUI", 0x10 | 0x10000):
                return
        except Exception:
            pass
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("AnalogwinPiezoGUI", message)
        root.destroy()
    except Exception:
        if sys.stderr is not None:
            print(message, file=sys.stderr)


def main() -> None:
    splash = None
    if os.environ.get("PIEZO_SPLASH", "0") == "1":
        try:
            from app.splash import StartupSplash

            splash = StartupSplash()
        except Exception:
            splash = None

    def progress(label: str, index: int, total: int) -> None:
        if splash is not None:
            splash.pump()

    if splash is not None:
        splash.pump()
    from app.application import PiezoApplication

    if splash is not None:
        splash.pump()
    app = PiezoApplication(on_startup_progress=progress, start_withdrawn=splash is not None)
    if splash is not None:
        splash.close()
        app.reveal()
    app.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _report_startup_failure(exc)
        raise SystemExit(1)
