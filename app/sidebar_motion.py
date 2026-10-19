"""Sidebar accents independent of ttk styles and the page geometry manager."""
from __future__ import annotations

import tkinter as tk
from typing import Any

from core import animation as anim


class SidebarMotion:
    def __init__(self, navigation: Any) -> None:
        self.nav = navigation
        self.animator = anim.animator_for(navigation.root)
        self.indicator = tk.Frame(navigation.sidebar, bd=0, highlightthickness=0)
        self.hover = tk.Frame(navigation.sidebar, bd=0, highlightthickness=0)
        self._y: float | None = None
        self._hover_widget = None
        self._hover_color = None
        self._job = None
        self._animate_pending = False
        navigation.sidebar.bind("<Configure>", lambda _e: self.refresh(), add="+")

    def bind(self, widget: tk.Misc) -> None:
        widget.bind("<Enter>", lambda _e: self._hover(widget, True), add="+")
        widget.bind("<Leave>", lambda _e: self._hover(widget, False), add="+")

    def _rect(self, widget: tk.Misc) -> tuple[int, float, int, int]:
        nav = self.nav
        width = nav._scaled(3)
        height = max(6, round(widget.winfo_height()*0.5))
        x = widget.winfo_rootx()-nav.sidebar.winfo_rootx()-width-nav._scaled(3)
        y = widget.winfo_rooty()-nav.sidebar.winfo_rooty()+(widget.winfo_height()-height)/2
        return max(0, x), y, width, height

    def _hover(self, widget: tk.Misc, entering: bool) -> None:
        if self.animator is None or not self.animator.enabled:
            return
        if not entering and widget is not self._hover_widget:
            return
        rest = self.nav.ctx.colors["panel_alt"]
        target = anim.lerp_color(rest, self.nav.ctx.colors["primary"], 0.45) if entering else rest
        start = self._hover_color if self._hover_widget is widget else rest
        if entering:
            x, y, width, height = self._rect(widget)
            self.hover.place(x=x, y=round(y), width=width, height=height, bordermode="outside")
            self._hover_widget = widget
        def step(p: float) -> None:
            self._hover_color = anim.lerp_color(start or rest, target, p)
            self.hover.configure(bg=self._hover_color)
        def done() -> None:
            if not entering:
                self.hover.place_forget()
                self._hover_widget = self._hover_color = None
        self.animator.animate("nav-hover", anim.DUR_FAST, step, done=done)

    def refresh(self, *, animate: bool = False) -> None:
        self._animate_pending = self._animate_pending or animate
        if self._job is None:
            self._job = self.nav.root.after(0, self._place)

    def _place(self) -> None:
        self._job = None
        animate, self._animate_pending = self._animate_pending, False
        nav = self.nav
        pending = getattr(nav, "_pending_page", None)
        widget = nav.buttons.get(pending) if pending else nav.mode_buttons.get(getattr(nav.mode_host, "current_mode", ""))
        if widget is None and not pending:
            widget = nav.buttons.get(nav.ctx.state.current_page)
        if widget is None or not widget.winfo_ismapped():
            self.indicator.place_forget()
            self._y = None
            return
        x, target, width, height = self._rect(widget)
        start = self._y
        self.indicator.configure(bg=nav.ctx.colors["primary"])
        def step(p: float) -> None:
            # The accordion can move its target while the indicator is in flight.
            x_now, target_now, width_now, height_now = self._rect(widget)
            self._y = target_now if start is None else start+(target_now-start)*p
            self.indicator.place(x=x_now, y=round(self._y), width=width_now, height=height_now,
                                 bordermode="outside")
            self.indicator.lift()
        if animate and start is not None and self.animator is not None:
            self.animator.animate("nav-indicator", anim.DUR_SNAP, step, ease=anim.EASE_SNAP,
                                  done=self.refresh)
        elif self.animator is None or not self.animator.is_running("nav-indicator"):
            step(1.0)

    def reset(self) -> None:
        if self.animator is not None:
            self.animator.cancel("nav-hover")
            self.animator.cancel("nav-indicator", finish=True)
        self.hover.place_forget()
        self._hover_widget = self._hover_color = None
        self.refresh()

    def close(self) -> None:
        if self._job is not None:
            self.nav.root.after_cancel(self._job)
            self._job = None
