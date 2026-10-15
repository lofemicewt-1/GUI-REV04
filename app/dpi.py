"""Per-monitor DPI awareness on Windows (PIEZO_DPI_AWARE=0 disables).

Without it Windows bitmap-stretches the whole Tk window on scaled displays
(125 %/150 %...): text is blurry and every coordinate Tk reports is in
virtualised "logical" pixels, which do not match screen grabs.  Must run
before the first Tk root is created.
"""
from __future__ import annotations

import os
import sys

_MODE: str | None = None


def enable_dpi_awareness() -> str:
    """Idempotent.  Returns the mode that ended up active ("" if none)."""
    global _MODE
    if _MODE is not None:
        return _MODE
    _MODE = ""
    if sys.platform != "win32" or os.environ.get("PIEZO_DPI_AWARE", "1") == "0":
        return _MODE
    try:
        import ctypes

        user32 = ctypes.windll.user32
    except Exception:
        return _MODE
    # 1) Windows 10 1703+: per-monitor v2 (DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4)
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_void_p]
        if fn(ctypes.c_void_p(-4)):
            _MODE = "per-monitor-v2"
            return _MODE
    except Exception:
        pass
    # 2) Windows 8.1+: shcore PROCESS_PER_MONITOR_DPI_AWARE
    try:
        hr = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        if hr == 0:
            _MODE = "per-monitor"
            return _MODE
        if hr & 0xFFFFFFFF == 0x80070005:  # E_ACCESSDENIED: already set (manifest)
            _MODE = "preset"
            return _MODE
    except Exception:
        pass
    # 3) Vista+: system aware
    try:
        if user32.SetProcessDPIAware():
            _MODE = "system"
    except Exception:
        pass
    return _MODE


def dpi_aware() -> bool:
    return bool(_MODE)


def dpi_factor(root) -> float:
    """Screen DPI / 96 as Tk sees it (1.0 when not DPI aware)."""
    try:
        f = float(root.winfo_fpixels("1i")) / 96.0
    except Exception:
        return 1.0
    if not dpi_aware() and sys.platform == "win32":
        return 1.0
    return max(1.0, min(4.0, f))
