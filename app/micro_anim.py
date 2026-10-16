"""Micro-interactions from the CDS motion guide (core/animation.py tokens).

Everything is cheap by construction:
* no ttk.Style changes per frame (those re-render every ttk widget, ~65 ms);
* a handful of shared plain ``tk.Frame`` bars created once and moved with
  ``place(in_=widget)`` -- one ``configure(bg=...)``/``place`` per frame;
* all tweens run on the shared ``Animator`` timer, which stops when idle;
* with "启用过渡动画" off every tween jumps to its end state.

Items:
  combobox value change  -> 120 ms accent sweep under the field (snap), then
                            120 ms fade back (ease-out)
  combobox list open     -> 200 ms expand of the popdown (height 35%->100%,
                            alpha 0->1 where supported), ease-out
  toggles (check/radio,  -> 60 ms accent bar grow on state change (snap)
  sidebar mode buttons)
  theme switch           -> 450 ms cross-fade of a snapshot of the old theme
                            (Windows atomic cover; instant elsewhere)
  button hover           -> one shared 2 px accent underline, 60 ms fade
                            in / out (ease-out), like the sidebar preview bar
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from core import animation as anim

HOVER_CLASSES = ("TButton",)
TOGGLE_CLASSES = ("TCheckbutton", "TRadiobutton")
BAR_H = 2

stats = {"hover": 0, "combo": 0, "popdown": 0, "toggle": 0, "frames": 0, "frame_ms": 0.0, "frame_max": 0.0}


def _timed(step: Callable[[float], None]) -> Callable[[float], None]:
    def run(p: float) -> None:
        t = time.perf_counter()
        step(p)
        dt = (time.perf_counter() - t) * 1000
        stats["frames"] += 1
        stats["frame_ms"] += dt
        stats["frame_max"] = max(stats["frame_max"], dt)
    return run


class MicroAnimations:
    def __init__(self, root: tk.Misc, animator: anim.Animator, colors: Callable[[], dict],
                 excluded: Callable[[tk.Misc], bool]) -> None:
        self.root = root
        self.animator = animator
        self._colors = colors
        self._excluded = excluded
        self._hover_bar = self._bar()
        self._combo_bar = self._bar()
        self._toggle_bar = self._bar()
        self._hover_widget: tk.Misc | None = None
        self._hover_color: str | None = None
        for cls in HOVER_CLASSES:
            root.bind_class(cls, "<Enter>", self._on_enter, add="+")
            root.bind_class(cls, "<Leave>", self._on_leave, add="+")
            root.bind_class(cls, "<ButtonPress-1>", self._on_press, add="+")
            root.bind_class(cls, "<ButtonRelease-1>", self._on_btn_release, add="+")
        root.bind_class("TCombobox", "<<ComboboxSelected>>", self._on_combo_selected, add="+")
        root.bind_class("ComboboxPopdown", "<Map>", self._on_popdown_map, add="+")
        root.bind_class("ComboboxPopdown", "<Unmap>", self._on_popdown_unmap, add="+")
        for cls in TOGGLE_CLASSES:
            root.bind_class(cls, "<<Invoke>>", self._on_toggle, add="+")
            root.bind_class(cls, "<ButtonRelease-1>", self._on_toggle_release, add="+")
        # add="+" is essential: other code (scroll areas) also binds
        # <MouseWheel> on "all"; a plain bind_all would replace theirs.
        # Locked by tests/test_animation.py (test_mousewheel_bind_all_is_additive).
        root.bind_all("<MouseWheel>", self._hide_all, add="+")
        # Destroyed widgets must not stay referenced (hover target, press
        # style) -- stale refs raise TclError or animate a dead bar target.
        for cls in set(HOVER_CLASSES) | set(TOGGLE_CLASSES) | {"TCombobox"}:
            root.bind_class(cls, "<Destroy>", self._on_widget_destroy, add="+")

    # -- helpers ----------------------------------------------------------
    def _bar(self) -> tk.Frame:
        return tk.Frame(self.root, bd=0, highlightthickness=0, height=BAR_H, width=1)

    def _c(self, key: str, fallback: str) -> str:
        try:
            return str(self._colors().get(key, fallback))
        except Exception:
            return fallback

    def _widget(self, event: tk.Event) -> tk.Misc | None:
        w = event.widget
        if isinstance(w, str):
            try:
                w = self.root.nametowidget(w)
            except (KeyError, tk.TclError):
                return None
        return w

    def _on_widget_destroy(self, event: tk.Event) -> None:
        w = self._widget(event)
        if w is None:
            return
        if w is self._hover_widget:
            self.animator.cancel("btn-hover")
            self._hover_widget = None
            self._hover_color = None
        pressed = getattr(self, "_press_style", None)
        if pressed and pressed[0] is w:
            self._press_style = None

    def _rest(self, w: tk.Misc) -> str:
        """Background behind the widget -- the bar fades from/to this.  Walk
        up the parents: a tk widget's own bg, else its ttk style's
        background; fall back to the panel colour."""
        style = None
        try:
            parent = w.master
            while parent is not None:
                try:
                    bg = str(parent.cget("background"))
                    if bg:
                        return bg
                except tk.TclError:
                    pass
                try:
                    name = str(parent.cget("style")) or str(parent.winfo_class())
                    if style is None:
                        style = ttk.Style(self.root)
                    bg = str(style.lookup(name, "background"))
                    if bg:
                        return bg
                except tk.TclError:
                    pass
                parent = parent.master
        except (AttributeError, tk.TclError):
            pass
        return self._c("panel", "#FFFFFF")

    def _hide_all(self, _e=None) -> None:
        for key, bar in (("btn-hover", self._hover_bar), ("combo-sweep", self._combo_bar), ("toggle", self._toggle_bar)):
            self.animator.cancel(key)
            bar.place_forget()
        self._hover_widget = None
        self._hover_color = None

    # -- button hover -------------------------------------------------------
    def _on_enter(self, event: tk.Event) -> None:
        w = self._widget(event)
        if w is None or self._excluded(w):
            return
        hw = self._hover_widget
        if hw is not None and hw is not w:
            try:
                alive = bool(hw.winfo_exists())
            except tk.TclError:
                alive = False
            if not alive:
                self._hover_widget = None
                self._hover_color = None
        try:
            if w.instate(["disabled"]):  # type: ignore[attr-defined]
                return
        except (AttributeError, tk.TclError):
            pass
        stats["hover"] += 1
        bar = self._hover_bar
        rest, accent = self._rest(w), self._c("primary", "#2563EB")
        start = self._hover_color if self._hover_widget is w and self._hover_color else rest
        self._hover_widget = w
        try:
            bar.configure(bg=start)
            bar.place(in_=w, relx=0.12, rely=1.0, y=-BAR_H - 1, relwidth=0.76, height=BAR_H)
            bar.lift()
        except tk.TclError:
            return

        def step(p: float) -> None:
            self._hover_color = anim.lerp_color(start, accent, p)
            bar.configure(bg=self._hover_color)

        self.animator.animate("btn-hover", anim.DUR_FAST, _timed(step), ease=anim.EASE_OUT)

    def _on_leave(self, event: tk.Event) -> None:
        w = self._widget(event)
        if w is None or w is not self._hover_widget:
            return
        bar = self._hover_bar
        start = self._hover_color or self._c("primary", "#2563EB")
        rest = self._rest(w)

        def step(p: float) -> None:
            self._hover_color = anim.lerp_color(start, rest, p)
            bar.configure(bg=self._hover_color)

        def done() -> None:
            if self._hover_widget is w:
                bar.place_forget()
                self._hover_widget = None
                self._hover_color = None

        self.animator.animate("btn-hover", anim.DUR_FAST, _timed(step), ease=anim.EASE_OUT, done=done)

    # -- segmented toggle buttons (Secondary <-> Selected TButton styles) ---
    def _on_press(self, event: tk.Event) -> None:
        w = self._widget(event)
        try:
            self._press_style = (w, str(w.cget("style"))) if w is not None else None
        except tk.TclError:
            self._press_style = None
        self._on_leave(event)

    def _on_btn_release(self, event: tk.Event) -> None:
        w = self._widget(event)
        pressed = getattr(self, "_press_style", None)
        if w is None or not pressed or pressed[0] is not w:
            return
        before = pressed[1]

        def check() -> None:
            try:
                after = str(w.cget("style"))
            except tk.TclError:
                return
            if after != before and after.endswith("Selected.TButton") and after not in ("SidebarSelected.TButton", "SidebarParentSelected.TButton"):
                self.toggle_pulse(w)

        self.root.after_idle(check)

    # -- combobox value change ------------------------------------------------
    def _on_combo_selected(self, event: tk.Event) -> None:
        w = self._widget(event)
        if w is None:
            return
        stats["combo"] += 1
        bar = self._combo_bar
        accent, rest = self._c("primary", "#2563EB"), self._rest(w)
        try:
            bar.configure(bg=accent)
            bar.place(in_=w, relx=0, rely=1.0, y=-BAR_H, relwidth=0.001, height=BAR_H)
            bar.lift()
        except tk.TclError:
            return

        def sweep(p: float) -> None:
            bar.place_configure(relwidth=max(0.001, p))

        def fade(p: float) -> None:
            bar.configure(bg=anim.lerp_color(accent, rest, p))

        def fade_done() -> None:
            bar.place_forget()

        def swept() -> None:
            self.animator.animate("combo-sweep", anim.DUR_SNAP, _timed(fade), ease=anim.EASE_OUT, done=fade_done)

        if not self.animator.enabled:
            bar.place_forget()
            return
        self.animator.animate("combo-sweep", anim.DUR_SNAP, _timed(sweep), ease=anim.EASE_SNAP, done=swept)

    # -- combobox list open --------------------------------------------------
    @staticmethod
    def _fix_popdown_view(call, path: str) -> None:
        """ttk's Post runs `see current` BEFORE it sets the listbox -height,
        so on the first open the view is computed for the default 10-line,
        not-yet-laid-out listbox: it shows only the lower items, then the
        upper ones pop in.  Set the final top index directly (no geometry
        needed): everything from 0 when the list fits, else keep the
        selection visible the way `see` would at the final height."""
        lb = f"{path}.f.l"
        try:
            n = int(call(lb, "size"))
            lines = max(1, int(call(lb, "cget", "-height")))
            sel = call(lb, "curselection")
            cur = int(sel[0]) if isinstance(sel, tuple) and sel else int(sel or 0) if str(sel).strip() else 0
            top = 0 if n <= lines or cur < lines else min(cur, n - lines)
            call(lb, "yview", top)
        except (tk.TclError, ValueError, TypeError, IndexError):
            pass

    def _on_popdown_unmap(self, event: tk.Event) -> None:
        """Closed mid-expand: hand the frame back to grid so the next Post's
        `winfo reqheight` (popdown size) is correct."""
        path = str(event.widget)
        call = self.root.tk.call
        try:
            if call("winfo", "toplevel", path) != path:
                return
            self.animator.cancel(("popdown", path))
            try:
                call("wm", "attributes", path, "-alpha", 1.0)  # never stay translucent
            except tk.TclError:
                pass
            frame = f"{path}.f"
            if str(call("winfo", "manager", frame)) == "place":
                call("place", "forget", frame)
                call("grid", frame)
        except tk.TclError:
            pass

    def _on_popdown_map(self, event: tk.Event) -> None:
        path = str(event.widget)
        call = self.root.tk.call
        try:
            if call("winfo", "toplevel", path) != path:
                return
        except tk.TclError:
            return
        self._fix_popdown_view(call, path)
        if not self.animator.enabled:
            return
        try:
            geo = str(call("wm", "geometry", path))
            size, _, pos = geo.partition("+")
            wd, ht = (int(v) for v in size.split("x"))
            x, y = pos.split("+")[:2]
        except (tk.TclError, ValueError):
            return
        if ht < 8:
            return
        stats["popdown"] += 1
        alpha = True
        try:
            call("wm", "attributes", path, "-alpha", 0.0)
        except tk.TclError:
            alpha = False

        # Keep the list content at its final size, anchored at the top: only
        # the toplevel's height grows (window clipping), so items never
        # reflow or scroll mid-animation.
        frame = f"{path}.f"
        pinned = False
        try:
            call("grid", "remove", frame)
            call("place", frame, "-x", 0, "-y", 0, "-relwidth", 1.0, "-height", ht)
            pinned = True
        except tk.TclError:
            pass

        def unpin() -> None:
            if not pinned:
                return
            try:
                call("place", "forget", frame)
                call("grid", frame)
            except tk.TclError:
                pass

        def step(p: float) -> None:
            try:
                h = max(1, round(ht * (0.35 + 0.65 * p)))
                call("wm", "geometry", path, f"{wd}x{h}+{x}+{y}")
                if alpha:
                    call("wm", "attributes", path, "-alpha", min(1.0, 0.2 + 0.8 * p))
            except tk.TclError:
                pass

        self.animator.animate(("popdown", path), anim.DUR_BASE, _timed(step), ease=anim.EASE_OUT, done=unpin)

    # -- toggles ------------------------------------------------------------
    def _on_toggle_release(self, event: tk.Event) -> None:
        w = self._widget(event)
        if w is None:
            return
        try:
            if not w.instate(["!disabled"]):  # type: ignore[attr-defined]
                return
            inside = 0 <= event.x < w.winfo_width() and 0 <= event.y < w.winfo_height()
        except (AttributeError, tk.TclError):
            return
        if inside:
            self.root.after_idle(lambda: self.toggle_pulse(w))

    def _on_toggle(self, event: tk.Event) -> None:
        w = self._widget(event)
        if w is not None:
            self.toggle_pulse(w)

    def toggle_pulse(self, w: tk.Misc) -> None:
        """60 ms state transition: an accent bar grows under the toggled
        control (snap), then fades in the next 60 ms."""
        if not self.animator.enabled:
            return
        stats["toggle"] += 1
        bar = self._toggle_bar
        accent, rest = self._c("primary", "#2563EB"), self._rest(w)
        try:
            bar.configure(bg=accent)
            bar.place(in_=w, relx=0, rely=1.0, y=-BAR_H, relwidth=0.001, height=BAR_H)
            bar.lift()
        except tk.TclError:
            return

        def grow(p: float) -> None:
            bar.place_configure(relwidth=max(0.001, p))

        def fade(p: float) -> None:
            bar.configure(bg=anim.lerp_color(accent, rest, p))

        def grown() -> None:
            self.animator.animate("toggle", anim.DUR_FAST, _timed(fade), ease=anim.EASE_OUT,
                                  done=bar.place_forget)

        self.animator.animate("toggle", anim.DUR_FAST, _timed(grow), ease=anim.EASE_SNAP, done=grown)
