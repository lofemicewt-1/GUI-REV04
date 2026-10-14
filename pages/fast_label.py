"""Plain tk labels for the two static ttk label styles.

On Windows every ttk widget paints through the themed element engine; a
plain ``tk.Label`` paints noticeably faster (about 40% less paint time per
label measured under Wine).  ``FastLabel`` takes the same ``style=`` name as
the ttk label it replaces and pulls background, foreground and font from
that ttk style, so theme changes still apply (see ``refresh_all``).
"""

from __future__ import annotations

import tkinter as tk
import weakref
from tkinter import ttk
from typing import Any

FAST_STYLES = frozenset({"Panel.TLabel", "Muted.TLabel"})
_TTK_ONLY = frozenset({"padding", "state", "class_", "class"})
_LIVE: "weakref.WeakSet[FastLabel]" = weakref.WeakSet()


_PAD_CACHE: dict[tuple[str, str], tuple[int, int]] = {}


def _style_padding(master: tk.Misc | None, style: str) -> tuple[int, int]:
    """Border+padding the ttk layout puts around the text, measured once per theme.

    Measured rather than hard-coded so the result matches whichever native
    theme (vista, clam, ...) is active on the user's machine.
    """
    st = ttk.Style(master)
    key = (str(st.theme_use()), style)
    if key not in _PAD_CACHE:
        probe_t = ttk.Label(master, text="Xy", style=style)
        font = st.lookup(style, "font") or "TkDefaultFont"
        probe_k = tk.Label(master, text="Xy", font=font, borderwidth=0, padx=0, pady=0, highlightthickness=0)
        dw = probe_t.winfo_reqwidth() - probe_k.winfo_reqwidth()
        dh = probe_t.winfo_reqheight() - probe_k.winfo_reqheight()
        probe_t.destroy()
        probe_k.destroy()
        _PAD_CACHE[key] = (max(0, dw // 2), max(0, dh // 2))
    return _PAD_CACHE[key]


class FastLabel(tk.Label):
    def __init__(self, master: tk.Misc | None = None, *, style: str = "Panel.TLabel", **kw: Any) -> None:
        self._piezo_style = style
        self._piezo_user_fg = "foreground" in kw or "fg" in kw
        self._piezo_user_bg = "background" in kw or "bg" in kw
        colors = self._style_colors(master, style)
        kw.setdefault("background", colors[0])
        kw.setdefault("foreground", colors[1])
        if "font" not in kw and colors[2]:
            kw["font"] = colors[2]
        kw.setdefault("borderwidth", 0)
        pad_x, pad_y = _style_padding(master, style)
        kw.setdefault("padx", pad_x)
        kw.setdefault("pady", pad_y)
        # ttk.Label defaults: text anchored west, multi-line text left-justified.
        kw.setdefault("anchor", str(ttk.Style(master).lookup(style, "anchor") or "w"))
        kw.setdefault("justify", "left")
        kw.setdefault("highlightthickness", 0)
        super().__init__(master, **kw)
        _LIVE.add(self)

    @staticmethod
    def _style_colors(master: tk.Misc | None, style: str) -> tuple[str, str, str]:
        st = ttk.Style(master)
        return (
            str(st.lookup(style, "background") or st.lookup(".", "background")),
            str(st.lookup(style, "foreground") or st.lookup(".", "foreground")),
            str(st.lookup(style, "font") or ""),
        )

    def refresh_style_colors(self) -> None:
        bg, fg, _font = self._style_colors(self, self._piezo_style)
        kw: dict[str, Any] = {}
        if not self._piezo_user_bg:
            kw["background"] = bg
        if not self._piezo_user_fg:
            kw["foreground"] = fg
        if kw:
            tk.Label.configure(self, **kw)

    def configure(self, cnf: Any = None, **kw: Any) -> Any:  # type: ignore[override]
        if isinstance(cnf, dict):
            kw = {**cnf, **kw}
            cnf = None
        if cnf is not None:
            if cnf == "style":
                return ("style", "style", "Style", "", self._piezo_style)
            return tk.Label.configure(self, cnf)
        style = kw.pop("style", None)
        if style is not None:
            self._piezo_style = str(style)
        if "foreground" in kw or "fg" in kw:
            self._piezo_user_fg = True
        if "background" in kw or "bg" in kw:
            self._piezo_user_bg = True
        result = tk.Label.configure(self, **kw) if kw else None
        if style is not None:
            self.refresh_style_colors()
        return result

    config = configure

    def cget(self, key: str) -> Any:  # type: ignore[override]
        if key == "style":
            return self._piezo_style
        return tk.Label.cget(self, key)

    __getitem__ = cget


def make_label(master: tk.Misc, **kw: Any) -> tk.Misc:
    """FastLabel for the static styles; ttk.Label for everything else."""
    style = kw.get("style")
    if style in FAST_STYLES and not (_TTK_ONLY & kw.keys()) and "image" not in kw:
        return FastLabel(master, **kw)
    return ttk.Label(master, **kw)


def refresh_all() -> None:
    for label in list(_LIVE):
        try:
            label.refresh_style_colors()
        except tk.TclError:
            pass


def is_label(widget: Any) -> bool:
    return isinstance(widget, (ttk.Label, FastLabel))
