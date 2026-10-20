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
  combobox list open     -> 200 ms whole-list fade at full height (no grow);
                            first-open view keeps the selection visible
  toggles (check/radio)  -> 60 ms accent pulse on state change
  auxiliary panel        -> 300 ms opacity entrance (ordinary Toplevel only)
  theme switch           -> 450 ms cross-fade of a snapshot of the old theme
                            (Windows atomic cover; instant elsewhere)
  button hover           -> one shared 2 px accent underline, 60 ms fade
                            in / out (ease-out), like the sidebar preview bar
"""

from __future__ import annotations

import time
import tkinter as tk
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
    _live: list[MicroAnimations] = []

    def __init__(self, root: tk.Misc, animator: anim.Animator, colors: Callable[[], dict],
                 excluded: Callable[[tk.Misc], bool]) -> None:
        self.root = root
        self.animator = animator
        self._colors = colors
        self._excluded = excluded
        self._hover_bar = self._bar()
        self._combo_bar = self._bar()
        self._toggle_bar = self._bar()
        self._bindings = []
        self._panels: dict[str, tk.Misc] = {}
        self._popdowns: set[str] = set()
        self._closed = False
        self._hover_widget: tk.Misc | None = None
        self._hover_color: str | None = None
        for cls in HOVER_CLASSES:
            self._bind(cls, "<Enter>", self._on_enter, add="+")
            self._bind(cls, "<Leave>", self._on_leave, add="+")
            self._bind(cls, "<ButtonPress-1>", self._on_press, add="+")
            self._bind(cls, "<ButtonRelease-1>", self._on_btn_release, add="+")
        self._bind("TCombobox", "<<ComboboxSelected>>", self._on_combo_selected, add="+")
        self._bind("ComboboxPopdown", "<Map>", self._on_popdown_map, add="+")
        self._bind("ComboboxPopdown", "<Unmap>", self._on_popdown_unmap, add="+")
        self._bind("ComboboxPopdown", "<Destroy>", self._on_popdown_unmap, add="+")
        self._bind("Toplevel", "<Map>", self._on_panel_map, add="+")
        self._bind("Toplevel", "<Unmap>", self._on_panel_unmap, add="+")
        self._bind("Toplevel", "<Destroy>", self._on_panel_unmap, add="+")
        for cls in TOGGLE_CLASSES:
            self._bind(cls, "<<Invoke>>", self._on_toggle, add="+")
            self._bind(cls, "<ButtonRelease-1>", self._on_toggle_release, add="+")
        for sequence in (
            "<MouseWheel>",
            "<Shift-MouseWheel>",
            "<Button-4>",
            "<Button-5>",
            "<Shift-Button-4>",
            "<Shift-Button-5>",
        ):
            self._bind("all", sequence, self._hide_all, add="+")
        MicroAnimations._live.append(self)

    def _bind(self, tag, sequence, callback, **kwargs):
        funcid = self.root.bind_class(tag, sequence, callback, **kwargs)
        self._bindings.append((tag, sequence, funcid))

    def reset(self) -> None:
        self._hide_all()
        for path in list(self._panels):
            self.animator.cancel(("panel", path), finish=True)
        for path in list(self._popdowns):
            self.animator.cancel(("popdown", path), finish=True)

    def _on_panel_map(self, event: tk.Event) -> None:
        window = self._widget(event)
        if window is None or not self.animator.enabled or window.overrideredirect():
            return
        path = str(window)
        self._panels[path] = window
        self.animator.animate(("panel", path), anim.DUR_SHEET,
                              lambda p: window.attributes("-alpha", p),
                              done=lambda: self._panels.pop(path, None))

    def _on_panel_unmap(self, event: tk.Event) -> None:
        self.animator.cancel(("panel", str(event.widget)), finish=True)

    def close(self) -> None:
        self._closed = True
        try:
            MicroAnimations._live.remove(self)
        except ValueError:
            pass
        self.reset()
        for tag, sequence, funcid in self._bindings:
            script = str(self.root.tk.call("bind", tag, sequence))
            remaining = "\n".join(line for line in script.splitlines() if f"[{funcid} " not in line)
            self.root.tk.call("bind", tag, sequence, remaining)
            self.root.deletecommand(funcid)
        self._bindings.clear()
        for bar in (self._hover_bar, self._combo_bar, self._toggle_bar):
            bar.destroy()

    @classmethod
    def hide_open_bars(cls) -> None:
        for inst in list(cls._live):
            try:
                inst._hide_all()
            except tk.TclError:
                pass

    # -- helpers ----------------------------------------------------------
    def _bar(self) -> tk.Frame:
        return tk.Frame(self.root, bd=0, highlightthickness=0, height=BAR_H, width=1)

    def _c(self, key: str, fallback: str) -> str:
        try:
            return str(self._colors().get(key, fallback))
        except Exception:
            return fallback

    def _widget(self, event: tk.Event) -> tk.Misc | None:
        if self._closed:
            return None
        w = event.widget
        if isinstance(w, str):
            try:
                w = self.root.nametowidget(w)
            except (KeyError, tk.TclError):
                return None
        return w

    def _rest(self, w: tk.Misc) -> str:
        # background behind the widget (its parent's bg if it is a tk widget,
        # else the panel colour) -- the bar fades from/to this.
        parent = getattr(w, "master", None)
        try:
            from tkinter import ttk
            if isinstance(parent, ttk.Widget):
                style = str(parent.cget("style")) or parent.winfo_class()
                color = ttk.Style(self.root).lookup(style, "background")
            else:
                color = parent.cget("background")
            if str(color).startswith("#"):
                return str(color)
        except (AttributeError, tk.TclError):
            pass
        return self._c("panel", "#FFFFFF")

    def _hide_all(self, _e=None) -> None:
        for key, bar in (("btn-hover", self._hover_bar), ("combo-sweep", self._combo_bar), ("toggle", self._toggle_bar)):
            self.animator.cancel(key)
            try:
                bar.place_forget()
            except tk.TclError:
                pass
        self._hover_widget = None
        self._hover_color = None

    # -- button hover -------------------------------------------------------
    def _on_enter(self, event: tk.Event) -> None:
        w = self._widget(event)
        if w is None or self._excluded(w) or not self.animator.enabled:
            return
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
            if self._closed or self._excluded(w) or w.instate(["disabled"]):
                return
            if w.winfo_ismapped() and 0 <= event.x < w.winfo_width() and 0 <= event.y < w.winfo_height():
                self.toggle_pulse(w, button=True)

        self.root.after_idle(check)

    # -- combobox value change ------------------------------------------------
    def _on_combo_selected(self, event: tk.Event) -> None:
        w = self._widget(event)
        if w is None or not self.animator.enabled:
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
            bar.place_configure(relwidth=max(0.001, min(1.0, p)))

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

    def _on_popdown_map(self, event: tk.Event) -> None:
        if self._closed:
            return
        path = str(event.widget)
        call = self.root.tk.call
        try:
            if call("winfo", "toplevel", path) != path or not int(call("winfo", "ismapped", path)):
                return
        except tk.TclError:
            return
        self._fix_popdown_view(call, path)
        if not self.animator.enabled:
            return
        try:
            # Keep native placement, dimensions, selection and input handling.
            # Fade the full list instead of clipping rows during expansion.
            call("wm", "attributes", path, "-alpha", 1)
        except tk.TclError:
            return
        self._popdowns.add(path)
        stats["popdown"] += 1
        self.animator.animate(("popdown", path), anim.DUR_BASE,
                              _timed(lambda p: call("wm", "attributes", path, "-alpha", p)),
                              ease=anim.EASE_OUT,
                              done=lambda: self._popdowns.discard(path))

    def _on_popdown_unmap(self, event: tk.Event) -> None:
        path = str(event.widget)
        if path in self._popdowns:
            self.animator.cancel(("popdown", path), finish=True)
            self._popdowns.discard(path)

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

    def toggle_pulse(self, w: tk.Misc, *, button: bool = False) -> None:
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
            bar.place_configure(relwidth=max(0.001, min(1.0, p)))

        def fade(p: float) -> None:
            bar.configure(bg=anim.lerp_color(accent, rest, p))

        def grown() -> None:
            self.animator.animate("toggle", anim.DUR_FAST, _timed(fade), ease=anim.EASE_OUT,
                                  done=bar.place_forget)

        self.animator.animate("toggle", anim.DUR_SNAP if button else anim.DUR_FAST, _timed(grow),
                              ease=anim.SPRING_BUTTON if button else anim.SPRING_FEEDBACK, done=grown)
