"""Shared Tk motion clock. Durations follow the supplied motion reference.

Only UI presentation is animated. All jobs use one idle-stopping timer and
wall-clock deadlines; replacing a key never invokes the superseded callback.
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Hashable

FRAME_MS = 16
DUR_FAST = 60
DUR_SNAP = 120
DUR_BASE = 200
DUR_SHEET = 300
DUR_SLOW = 450
GRAPHEME_MS = 90
GRAPHEME_STAGGER_MS = 30

def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """CSS-style ``cubic-bezier(x1, y1, x2, y2)`` easing function (t -> eased)."""

    def sample(a1: float, a2: float, t: float) -> float:
        return ((1 - 3 * a2 + 3 * a1) * t + (3 * a2 - 6 * a1)) * t * t + 3 * a1 * t

    def slope(a1: float, a2: float, t: float) -> float:
        return 3 * (1 - 3 * a2 + 3 * a1) * t * t + 2 * (3 * a2 - 6 * a1) * t + 3 * a1

    def solve_t(x: float) -> float:
        t = x
        for _ in range(8):  # Newton-Raphson
            err = sample(x1, x2, t) - x
            d = slope(x1, x2, t)
            if abs(err) < 1e-5:
                return t
            if abs(d) < 1e-6:
                break
            t -= err / d
        lo, hi = 0.0, 1.0  # bisection fallback
        t = x
        for _ in range(30):
            v = sample(x1, x2, t)
            if abs(v - x) < 1e-5:
                break
            if v < x:
                lo = t
            else:
                hi = t
            t = (lo + hi) / 2
        return t

    def ease(p: float) -> float:
        if p <= 0:
            return 0.0
        if p >= 1:
            return 1.0
        return sample(y1, y2, solve_t(p))

    return ease


EASE_OUT = cubic_bezier(0.165, 0.84, 0.44, 1.0)      # --cds-ease-out: smooth decel
EASE_SNAP = cubic_bezier(0.32, 0.72, 0.0, 1.0)       # --cds-ease-snap: "bite" into place
EASE_OVERSHOOT = cubic_bezier(0.34, 1.3, 0.64, 1.0)  # --cds-ease-overshoot: ~3 % overshoot
EASE_GRAPHEME = cubic_bezier(0.215, 0.61, 0.355, 1.0)  # text reveal
EASE_IN_OUT = cubic_bezier(0.4, 0.0, 0.2, 1.0)       # standard (loading dots)
EASE_SETTLE = EASE_OVERSHOOT


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def lerp_color(a: str, b: str, p: float) -> str:
    try:
        ra, ga, ba = _hex_to_rgb(a)
        rb, gb, bb = _hex_to_rgb(b)
    except (ValueError, IndexError):
        return b if p >= 0.5 else a
    p = max(0.0, min(1.0, p))
    return "#%02X%02X%02X" % (
        round(ra + (rb - ra) * p),
        round(ga + (gb - ga) * p),
        round(ba + (bb - ba) * p),
    )


@dataclass(frozen=True)
class Spring:
    stiffness: float
    damping: float
    mass: float

    def response(self, seconds: float) -> float:
        """Analytic unit-step response: stable even for the stiff drag preset."""
        w = math.sqrt(self.stiffness / self.mass)
        a = self.damping / (2 * self.mass)
        if abs(a - w) < 1e-8:
            return 1 - math.exp(-a * seconds) * (1 + a * seconds)
        if a < w:
            wd = math.sqrt(w*w - a*a)
            return 1 - math.exp(-a * seconds) * (math.cos(wd*seconds) + a/wd*math.sin(wd*seconds))
        # Avoid cosh/sinh overflow for the overdamped 1200/80/0.1 preset.
        d = math.sqrt(a*a - w*w)
        r1, r2 = -w*w/(a+d), -a-d
        return 1 - (r2*math.exp(r1*seconds) - r1*math.exp(r2*seconds))/(r2-r1)

    def easing(self) -> Callable[[float], float]:
        # Fit the physical response into a motion token's allotted duration.
        # Both displacement and velocity must settle before truncation.
        end = 0.016
        while end < 5:
            if all(abs(1-self.response(end+i*0.016)) < 0.001 for i in range(20)):
                break
            end += 0.016
        scale = self.response(end)
        return lambda p: self.response(max(0.0, min(1.0, p))*end)/scale


SPRING_BUTTON = Spring(600, 30, 1).easing()
SPRING_CARD = Spring(320, 28, 1).easing()
SPRING_FEEDBACK = Spring(380, 18, 1).easing()
SPRING_DRAG = Spring(1200, 80, 0.1).easing()


@dataclass
class _Tween:
    start: float
    duration: float
    ease: Callable[[float], float]
    step: Callable[[float], None]
    done: Callable[[], None] | None


class Animator:
    def __init__(self, root: tk.Misc, *, enabled: bool = True) -> None:
        self.root = root
        self.enabled = enabled
        self._tweens: dict[Hashable, _Tween] = {}
        self._after: str | None = None
        self._stopped = False

    def animate(self, key: Hashable, duration_ms: float, step: Callable[[float], None],
                *, ease: Callable[[float], float] = EASE_OUT,
                done: Callable[[], None] | None = None) -> None:
        self.cancel(key)
        if self._stopped:
            return
        if not self.enabled or duration_ms <= 0:
            self._finish(step, done)
            return
        tween = _Tween(time.perf_counter(), duration_ms/1000, ease, step, done)
        self._tweens[key] = tween
        try:
            step(ease(0.0))
        except tk.TclError:
            if self._tweens.get(key) is tween:
                self._tweens.pop(key)
            if done is not None:
                self._call(done)
        self._ensure_ticking()

    def cancel(self, key: Hashable, *, finish: bool = False) -> None:
        tween = self._tweens.pop(key, None)
        if not self._tweens:
            self._cancel_timer()
        if tween is not None and finish:
            self._finish(tween.step, tween.done)

    def is_running(self, key: Hashable) -> bool:
        return key in self._tweens

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            for key, tween in list(self._tweens.items()):
                if self._tweens.get(key) is tween:
                    self.cancel(key, finish=True)

    def stop(self) -> None:
        self._stopped = True
        self._tweens.clear()
        self._cancel_timer()

    def _cancel_timer(self) -> None:
        if self._after is not None:
            try:
                self.root.after_cancel(self._after)
            except (tk.TclError, RuntimeError):
                pass
            self._after = None

    @staticmethod
    def _call(callback: Callable[[], None]) -> None:
        try:
            callback()
        except tk.TclError:
            pass

    def _finish(self, step: Callable[[float], None], done: Callable[[], None] | None) -> None:
        try:
            step(1.0)
        except tk.TclError:
            pass
        finally:
            if done is not None:
                self._call(done)

    def _ensure_ticking(self) -> None:
        if self._after is None and self._tweens and not self._stopped:
            try:
                self._after = self.root.after(FRAME_MS, self._tick)
            except (tk.TclError, RuntimeError):
                self.stop()

    def _tick(self) -> None:
        self._after = None
        now = time.perf_counter()
        try:
            for key, tween in list(self._tweens.items()):
                if self._tweens.get(key) is not tween:
                    continue
                p = max(0.0, min(1.0, (now-tween.start)/tween.duration))
                if p >= 1:
                    self._tweens.pop(key)
                    self._finish(tween.step, tween.done)
                    continue
                try:
                    tween.step(tween.ease(p))
                except tk.TclError:
                    if self._tweens.get(key) is tween:
                        self._tweens.pop(key)
                    if tween.done is not None:
                        self._call(tween.done)
        finally:
            self._ensure_ticking()


def animator_for(widget: tk.Misc) -> Animator | None:
    try:
        return getattr(widget._root(), "_piezo_animator", None)
    except (AttributeError, tk.TclError):
        return None
