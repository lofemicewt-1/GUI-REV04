"""Tk geometry helpers for waveform preview columns."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Any

from app.window_layout import clamped_waveform_width


class WaveformWidthClamp:
    """Keep a gridded plot panel within the scaled cap and available width."""

    def __init__(
        self,
        owner: Any,
        container: tk.Misc,
        panel: tk.Misc,
        *,
        reserved_width: Callable[[], int] | None = None,
        requested_height: Callable[[], int] | None = None,
    ) -> None:
        self.owner = owner
        self.container = container
        self.panel = panel
        self.reserved_width = reserved_width or (lambda: 0)
        self.requested_height = requested_height
        self._binding = container.bind("<Configure>", self._on_configure, add="+")
        panel.grid_propagate(False)
        self.refresh()

    def _on_configure(self, _event: tk.Event | None = None) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            # Disabling grid propagation freezes both axes, even though this
            # helper only clamps width. Keep the content's height request alive.
            if self.requested_height is not None:
                height = max(1, int(self.requested_height()))
                if int(float(self.panel.cget("height"))) != height:
                    self.panel.configure(height=height)
            available = int(self.container.winfo_width())
            if available <= 1:
                return
            state = getattr(getattr(self.owner, "ctx", None), "state", None)
            scale = float(getattr(state, "ui_scale", 1.0) or 1.0)
            reserved = max(0, int(self.reserved_width()))
            width = clamped_waveform_width(scale, available, reserved)
            if int(float(self.panel.cget("width"))) != width:
                self.panel.configure(width=width)
        except (tk.TclError, TypeError, ValueError):
            return

    def close(self) -> None:
        try:
            if self._binding:
                self.container.unbind("<Configure>", self._binding)
        except tk.TclError:
            pass
        self._binding = None
