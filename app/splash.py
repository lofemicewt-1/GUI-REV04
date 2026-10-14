"""Borderless startup splash with animated dots.

Kept free of app/page imports so main.py can show it before loading the rest.
PyInstaller: resolve assets via ``sys._MEIPASS`` when frozen.
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path
from typing import Any

_DOT_FRAMES = ("。", "。。", "。。。")
_DOT_MS = 380


def _asset(name: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parents[1]
    return base / "assets" / name


class StartupSplash:
    """Small centered window: logo + “正在加载” + cycling dots."""

    def __init__(self, *, theme: str = "light") -> None:
        dark = theme == "dark"
        bg = "#111827" if dark else "#F5F7FA"
        fg = "#F9FAFB" if dark else "#111827"
        muted = "#9CA3AF" if dark else "#6B7280"

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.configure(bg=bg)
        except tk.TclError:
            pass

        shell = tk.Frame(self.root, bg=bg, padx=28, pady=22)
        shell.pack(fill="both", expand=True)

        self._logo_img: tk.PhotoImage | None = None
        logo_path = _asset("logo_dark.png" if dark else "logo_light.png")
        if logo_path.exists():
            try:
                img = tk.PhotoImage(file=str(logo_path), master=self.root)
                factor = max(1, -(-img.width() // 220))  # keep the logo <= 220 px wide
                if factor > 1:
                    img = img.subsample(factor, factor)
                self._logo_img = img
                tk.Label(shell, image=img, bg=bg).pack(pady=(0, 12))
            except tk.TclError:
                self._logo_img = None

        row = tk.Frame(shell, bg=bg)
        row.pack()
        self._label = tk.Label(
            row,
            text="正在加载",
            font=("Microsoft YaHei UI", 14),
            fg=fg,
            bg=bg,
        )
        self._label.pack(side="left")
        self._dots = tk.Label(
            row,
            text=_DOT_FRAMES[0],
            font=("Microsoft YaHei UI", 14),
            fg=muted,
            bg=bg,
            width=3,
            anchor="w",
        )
        self._dots.pack(side="left")


        self._dot_i = 0
        self._last_dot = 0.0
        self._closed = False

        self.root.update_idletasks()
        w = max(self.root.winfo_reqwidth(), 280)
        h = max(self.root.winfo_reqheight(), 120)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.deiconify()
        self.root.lift()
        self.pump()

    def pump(self) -> None:
        """Advance dots if due and process splash events (call between build steps)."""
        if self._closed:
            return
        now = time.perf_counter()
        if now - self._last_dot >= _DOT_MS / 1000.0:
            self._dot_i = (self._dot_i + 1) % len(_DOT_FRAMES)
            try:
                self._dots.configure(text=_DOT_FRAMES[self._dot_i])
            except tk.TclError:
                pass
            self._last_dot = now
        try:
            self.root.update_idletasks()
            self.root.update()
        except tk.TclError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass
