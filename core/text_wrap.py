"""Avoid a single orphan character on the last line of wrapped labels.

Tk wraps a label at its ``wraplength``: it breaks after the last space that
fits, otherwise (CJK runs have no spaces) between characters.  ``tk_wrap``
reproduces that so we can see the last line; when it would hold a single
character, a newline is inserted before the previous character so the last
line gets two.  The original text is kept and re-wrapped whenever text, font
or width changes, so a stale manual break never survives a resize.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any, Callable

_GUARD_ATTR = "_piezo_orphan_guard"


def _fit(text: str, measure: Callable[[str], int], width: int) -> int:
    """Largest n with measure(text[:n]) <= width (at least 1)."""
    lo, hi = 1, len(text)
    if measure(text) <= width:
        return len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(text[:mid]) <= width:
            lo = mid
        else:
            hi = mid - 1
    return max(1, lo)


def tk_wrap(text: str, measure: Callable[[str], int], width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        rest = para
        if not rest:
            lines.append("")
            continue
        while rest:
            n = _fit(rest, measure, width)
            if n >= len(rest):
                lines.append(rest)
                break
            cut = rest.rfind(" ", 0, n + 1)
            if cut > 0:
                lines.append(rest[:cut])
                rest = rest[cut + 1:].lstrip(" ")
            else:
                lines.append(rest[:n])
                rest = rest[n:]
    return lines


def orphan_safe(text: str, measure: Callable[[str], int], width: int) -> str:
    if width <= 0 or not text:
        return text
    out: list[str] = []
    for para in text.split("\n"):
        lines = tk_wrap(para, measure, width) if para else [""]
        if len(lines) >= 2 and len(lines[-1].strip()) == 1 and len(para) >= 2 and para[-2] not in " \n":
            para = para[:-2] + "\n" + para[-2:]
        out.append(para)
    return "\n".join(out)


def _pixels(widget: tk.Misc, value: Any) -> int:
    try:
        return int(round(widget.winfo_fpixels(value)))
    except (tk.TclError, ValueError):
        return 0


_FONT_CACHE: dict[tuple[str, str, str], Callable[[str], int]] = {}


def _measurer(label: tk.Misc) -> Callable[[str], int]:
    spec = str(label.cget("font") or "")
    if not spec:
        style = str(label.cget("style") or "TLabel")
        spec = str(label.tk.call("ttk::style", "lookup", style, "-font") or "TkDefaultFont")
    try:
        scaling = str(label.tk.call("tk", "scaling"))
    except tk.TclError:
        scaling = ""
    key = (str(label.tk), spec, scaling)
    measure = _FONT_CACHE.get(key)
    if measure is None:
        font = tkfont.Font(root=label, font=spec)
        glyphs: dict[str, int] = {}

        def measure(text: str, _font: tkfont.Font = font, _g: dict[str, int] = glyphs) -> int:
            # Sum of cached per-character advances: each glyph is measured
            # once per font (labels have no kerning-sensitive Latin runs at
            # wrap points that matter), so wrapping a page costs ~no Tk calls.
            total = 0
            for ch in text:
                w = _g.get(ch)
                if w is None:
                    w = _g[ch] = _font.measure(ch)
                total += w
            return total

        _FONT_CACHE[key] = measure
    return measure


class OrphanGuard:
    def __init__(self, label: tk.Misc) -> None:
        self.label = label
        self.source = str(label.cget("text"))
        self.applied: str | None = None
        self._key: tuple[Any, ...] | None = None
        self._configure = label.configure
        label.configure = self.configure  # type: ignore[method-assign]
        label.config = self.configure  # type: ignore[method-assign]
        label.bind("<Configure>", lambda _e: self.apply(), add="+")
        self.apply()

    def configure(self, cnf: Any = None, **kw: Any) -> Any:
        if isinstance(cnf, dict):
            kw = {**cnf, **kw}
            cnf = None
        if cnf is not None:  # query form: label.configure("text")
            return self._configure(cnf)
        if "text" in kw:
            self.source = str(kw.pop("text"))
            kw["text"] = self.source
        result = self._configure(**kw) if kw else self._configure()
        if kw and any(k in kw for k in ("text", "font", "wraplength", "style")):
            self.apply()
        return result

    def apply(self) -> None:
        label = self.label
        try:
            width = _pixels(label, label.cget("wraplength"))
            measure = _measurer(label)
            key = (self.source, width, id(measure))
            if key == self._key:
                return
            text = orphan_safe(self.source, measure, width)
            self._key = key
            if text != str(label.cget("text")):
                self._configure(text=text)
            self.applied = text
        except tk.TclError:
            pass


def widget_wraps(widget: tk.Misc) -> bool:
    try:
        return isinstance(widget, ttk.Label) and _pixels(widget, widget.cget("wraplength")) > 0
    except tk.TclError:
        return False


def warm_glyphs(label: tk.Misc, deadline: float) -> bool:
    """Measure the label's not-yet-cached glyphs until ``deadline``
    (perf_counter).  True when every glyph is cached."""
    import time

    measure = _measurer(label)
    for ch in set(str(label.cget("text"))):
        measure(ch)
        if time.perf_counter() >= deadline:
            return False
    return True


def guard_orphans(root: tk.Misc, *, styles: tuple[str, ...] = ("Muted.TLabel", "Panel.TLabel", "TLabel")) -> int:
    """Attach guards to wrapped, static-text ttk labels below ``root``."""
    count = 0
    stack = [root]
    while stack:
        widget = stack.pop()
        stack.extend(widget.winfo_children())
        if not isinstance(widget, ttk.Label) or getattr(widget, _GUARD_ATTR, None) is not None:
            continue
        try:
            if str(widget.cget("textvariable")):
                continue
            if _pixels(widget, widget.cget("wraplength")) <= 0:
                continue
            style = str(widget.cget("style") or "TLabel")
        except tk.TclError:
            continue
        if style not in styles:
            continue
        setattr(widget, _GUARD_ATTR, OrphanGuard(widget))
        count += 1
    return count
