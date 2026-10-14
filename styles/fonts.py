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


_FAMILIES_CACHE: dict[str, frozenset[str]] = {}


def font_families(root: Any) -> frozenset[str]:
    """``tkfont.families`` enumerates every installed font (slow on Windows);
    the set does not change while the app runs, so read it once per root."""
    key = str(getattr(root, "_w", root))
    families = _FAMILIES_CACHE.get(key)
    if families is None:
        families = _FAMILIES_CACHE[key] = frozenset(tkfont.families(root))
    return families


def sidebar_brand_font_spec(root: Any) -> tuple[str, str]:
    register_bundled_fonts()
    families = font_families(root)
    if "JetBrains Mono ExtraBold" in families:
        return ("JetBrains Mono ExtraBold", "normal")
    if "JetBrains Mono" in families:
        return ("JetBrains Mono", "bold")
    for name in ("Consolas", "Cascadia Mono", "Courier New"):
        if name in families:
            return (name, "bold")
    return ("Courier", "bold")


def page_title_weight(root: Any) -> str:
    """Weight for large page titles.

    Light text on a dark background reads heavier (irradiation), so dark mode
    uses the next lighter weight.  Microsoft YaHei UI has no 500/600 axis;
    Regular is the nearest step below Bold.  Light mode is unchanged.
    """
    try:
        theme = getattr(root.winfo_toplevel(), "_piezo_theme", "light")
    except Exception:
        theme = getattr(root, "_piezo_theme", "light")
    return "normal" if theme == "dark" else "bold"


_DIGIT_FAMILIES = ("Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Liberation Mono", "Courier New")
_digits_cache: dict[tuple[str, int, float], tuple[str, int]] = {}


def digits_font(root: Any, body_family: str, body_size: int) -> tuple[str, int]:
    """Monospace (tabular-digit) font for numeric readouts.

    Sized so its line height never exceeds the body font's: entries and
    labels keep their current height, digits just stop shifting sideways.
    """
    try:
        scaling = float(root.tk.call("tk", "scaling"))
    except Exception:
        scaling = 0.0
    key = (body_family, int(body_size), round(scaling, 4))
    cached = _digits_cache.get(key)
    if cached is not None:
        return cached
    families = {name.lower(): name for name in font_families(root)}
    family = next((families[f.lower()] for f in _DIGIT_FAMILIES if f.lower() in families), "TkFixedFont")
    if family == "TkFixedFont":
        result = (family, int(body_size))
    else:
        limit = tkfont.Font(root=root, family=body_family, size=int(body_size)).metrics("linespace")
        size = int(body_size)
        probe = tkfont.Font(root=root, family=family, size=size)
        while size > 6 and probe.metrics("linespace") > limit:
            size -= 1
            probe.configure(size=size)
        while probe.metrics("linespace") <= limit and size < int(body_size) + 4:
            probe.configure(size=size + 1)
            if probe.metrics("linespace") > limit:
                break
            size += 1
        result = (family, size)
    _digits_cache[key] = result
    return result
