"""Small Tk animation toolkit: cubic-bezier easing, colour interpolation and
one shared ~60 fps ticker.

Tk has no compositor, so everything here is property tweening (colours,
``place`` coordinates) driven from the main thread.  All tweens share one
``after`` timer; a tween keyed like an existing one replaces it (no stacking
when the user hovers quickly).  With animations disabled every tween jumps
straight to its final value, so the end state is identical either way.
"""

from __future__ import annotations

import time
import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Hashable

FRAME_MS = 16

# Motion tokens (reference: Claude Desktop "CDS" tokens from the note).
DUR_FAST = 60     # micro-interaction: hover / icon colour
DUR_SNAP = 120    # switch: slider / tab
DUR_BASE = 200    # basic transition: collapse / expand
DUR_SHEET = 300   # large panel: drawer / sidebar
DUR_SLOW = 450    # background / ambient colour
GRAPHEME_MS = 90  # per-character reveal
GRAPHEME_STAGGER_MS = 30

HOVER_MS = DUR_FAST
NAV_SLIDE_MS = DUR_SNAP
FADE_MS = DUR_SNAP


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


@dataclass
class _Tween:
    start: float | None  # set on the first painted frame
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
        self.frame_times: list[float] = []  # perf_counter of each tick (for diagnostics)
        self.record = False

    def animate(
        self,
        key: Hashable,
        duration_ms: float,
        step: Callable[[float], None],
        *,
        ease: Callable[[float], float] = EASE_OUT,
        done: Callable[[], None] | None = None,
    ) -> None:
        """Run ``step(eased_progress)`` each frame; ``step(1.0)`` is always the last call."""
        self._tweens.pop(key, None)
        if not self.enabled or duration_ms <= 0:
            self._finish(step, done)
            return
        # The clock starts on the first tick, not now: the caller is usually
        # in the middle of heavier work (e.g. a page switch) and starting the
        # clock here would skip the first part of the motion.
        self._tweens[key] = _Tween(None, duration_ms / 1000.0, ease, step, done)
        try:
            step(ease(0.0))
        except tk.TclError:
            self._tweens.pop(key, None)
            return
        self._ensure_ticking()

    def cancel(self, key: Hashable, *, finish: bool = False) -> None:
        tween = self._tweens.pop(key, None)
        if tween is not None and finish:
            self._finish(tween.step, tween.done)

    def is_running(self, key: Hashable) -> bool:
        return key in self._tweens

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            for key in list(self._tweens):
                self.cancel(key, finish=True)

    def stop(self) -> None:
        self._tweens.clear()
        if self._after is not None:
            try:
                self.root.after_cancel(self._after)
            except (tk.TclError, RuntimeError):
                pass
            self._after = None

    @staticmethod
    def _finish(step: Callable[[float], None], done: Callable[[], None] | None) -> None:
        try:
            step(1.0)
            if done is not None:
                done()
        except tk.TclError:
            pass

    def _ensure_ticking(self) -> None:
        if self._after is None:
            try:
                self._after = self.root.after(FRAME_MS, self._tick)
            except (tk.TclError, RuntimeError):
                self._after = None

    def _tick(self) -> None:
        self._after = None
        now = time.perf_counter()
        if self.record:
            self.frame_times.append(now)
        for key, tween in list(self._tweens.items()):
            if self._tweens.get(key) is not tween:
                continue
            if tween.start is None:
                tween.start = now - FRAME_MS / 1000.0
            p = (now - tween.start) / tween.duration
            if p >= 1.0:
                self._tweens.pop(key, None)
                self._finish(tween.step, tween.done)
                continue
            try:
                tween.step(tween.ease(p))
            except tk.TclError:
                self._tweens.pop(key, None)
        if self._tweens:
            self._ensure_ticking()


def animator_for(widget: tk.Misc) -> Animator | None:
    try:
        root = widget._root()  # type: ignore[attr-defined]
    except Exception:
        return None
    return getattr(root, "_piezo_animator", None)
