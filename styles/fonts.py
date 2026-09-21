from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from tkinter import font as tkfont
from typing import Any

_FR_PRIVATE = 0x10
_registered = False
_family_names = ("JetBrains Mono ExtraBold", "JetBrains Mono")


def _font_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "fonts"


def register_bundled_fonts() -> None:
    global _registered
    if _registered or sys.platform != "win32":
        return
    directory = _font_dir()
    if not directory.is_dir():
        _registered = True
        return
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi32.AddFontResourceExW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    gdi32.AddFontResourceExW.restype = ctypes.c_int
    for path in directory.glob("*.ttf"):
        gdi32.AddFontResourceExW(str(path), _FR_PRIVATE, None)
    # FR_PRIVATE fonts belong only to this process. Broadcasting WM_FONTCHANGE
    # synchronously waits for every other desktop app and can hang before our
    # mainloop starts. Tk enumerates these fonts locally below.
    _registered = True


def sidebar_brand_font_spec(root: Any) -> tuple[str, str]:
    register_bundled_fonts()
    families = set(tkfont.families(root))
    if "JetBrains Mono ExtraBold" in families:
        return ("JetBrains Mono ExtraBold", "normal")
    if "JetBrains Mono" in families:
        return ("JetBrains Mono", "bold")
    for name in ("Consolas", "Cascadia Mono", "Courier New"):
        if name in families:
            return (name, "bold")
    return ("Courier", "bold")
