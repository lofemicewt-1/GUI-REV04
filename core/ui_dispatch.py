"""Thread-safe hand-off of callbacks from worker threads to the Tk main thread.

Worker threads must not call Tk (``widget.after`` included): once the main loop
has stopped this raises ``RuntimeError: main thread is not in main loop`` and
under load each ``after(0)`` is a separate Tcl event.  Workers instead put
``(fn, args)`` into a FIFO queue; the main thread drains it on a short timer.

* ``post`` never touches Tk, so it is safe from any thread and after shutdown
  (items posted after ``close`` are dropped).
* FIFO order is preserved for everything posted through the dispatcher.
* Each tick drains all pending items, bounded by an item cap and a time budget
  so a log flood cannot freeze the UI; leftovers continue on the next tick.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from typing import Any, Callable

DEFAULT_INTERVAL_MS = 16
BUSY_INTERVAL_MS = 1
MAX_ITEMS_PER_TICK = 2000
TIME_BUDGET_S = 0.012

_active_lock = threading.Lock()
_active: "UiDispatcher | None" = None


class UiDispatcher:
    def __init__(
        self,
        root: tk.Misc,
        *,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        max_items: int = MAX_ITEMS_PER_TICK,
        time_budget_s: float = TIME_BUDGET_S,
    ) -> None:
        self.root = root
        self.interval_ms = max(1, int(interval_ms))
        self.max_items = max(1, int(max_items))
        self.time_budget_s = float(time_budget_s)
        self._queue: "queue.SimpleQueue[tuple[Callable[..., Any], tuple[Any, ...]]]" = queue.SimpleQueue()
        self._closed = threading.Event()
        self._after_id: str | None = None
        self.processed = 0
        self.dropped = 0

    # -- any thread ---------------------------------------------------------
    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def post(self, fn: Callable[..., Any], *args: Any) -> bool:
        if self._closed.is_set():
            self.dropped += 1
            return False
        self._queue.put((fn, args))
        return True

    def pending(self) -> int:
        return self._queue.qsize()

    # -- main thread only ---------------------------------------------------
    def start(self) -> "UiDispatcher":
        global _active
        with _active_lock:
            _active = self
        self._schedule(self.interval_ms)
        return self

    def close(self) -> None:
        global _active
        self._closed.set()
        with _active_lock:
            if _active is self:
                _active = None
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except (tk.TclError, RuntimeError):
                pass
            self._after_id = None
        # Drop anything still queued; widgets are going away.
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def drain(self, *, max_items: int | None = None) -> int:
        """Run pending callbacks now (main thread).  Returns the number run."""
        limit = self.max_items if max_items is None else max(1, int(max_items))
        deadline = time.perf_counter() + self.time_budget_s
        count = 0
        while count < limit and not self._closed.is_set():
            try:
                fn, args = self._queue.get_nowait()
            except queue.Empty:
                break
            count += 1
            try:
                fn(*args)
            except tk.TclError:
                # Target widget destroyed between post and run: same outcome as
                # the old ``after(0)`` raising TclError in the worker.
                pass
            except Exception as exc:  # report like a normal Tk callback
                try:
                    self.root.report_callback_exception(type(exc), exc, exc.__traceback__)
                except Exception:
                    pass
            if count % 64 == 0 and time.perf_counter() >= deadline:
                break
        self.processed += count
        return count

    def _schedule(self, delay_ms: int) -> None:
        if self._closed.is_set():
            return
        try:
            self._after_id = self.root.after(delay_ms, self._tick)
        except (tk.TclError, RuntimeError):
            self._after_id = None

    def _tick(self) -> None:
        self._after_id = None
        if self._closed.is_set():
            return
        self.drain()
        self._schedule(BUSY_INTERVAL_MS if not self._queue.empty() else self.interval_ms)


def active_dispatcher() -> UiDispatcher | None:
    return _active


def post_to_ui(widget: Any, fn: Callable[..., Any], *args: Any) -> None:
    """Run ``fn(*args)`` on the Tk main thread.  Safe from any thread.

    Uses the application's dispatcher when one is running; otherwise (light
    test fixtures without an application) falls back to ``widget.after(0)``.
    """
    dispatcher = _active
    if dispatcher is not None and not dispatcher.closed:
        dispatcher.post(fn, *args)
        return
    after = getattr(widget, "after", None)
    if not callable(after):
        fn(*args)
        return
    try:
        after(0, fn, *args)
    except (tk.TclError, RuntimeError):
        pass
