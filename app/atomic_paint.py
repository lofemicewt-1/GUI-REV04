"""Atomic page switches on Windows (PIEZO_ATOMIC_PAINT=0 disables).

Tk on Windows draws every widget into its own native child window, one by
one, during idle callbacks -- outside WM_PAINT -- so a freshly mapped page
shows up in visible stages.  WS_EX_COMPOSITED / WM_SETREDRAW do not help:
Tk's idle drawing uses GetDC directly and bypasses both.

What does work with DWM (always on since Windows 8): a separate top-level
window has its own redirection surface, and the window *under* it keeps
painting into its own surface even while covered.  So before the switch we
grab a screenshot of the content area, show it in a borderless topmost
overlay, let the new page map, lay out and paint completely underneath,
and only then remove (or fade out) the overlay.  The user sees the old page
and then the finished new page -- no half-painted frames.
"""
from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from typing import Callable

# On by default on Windows, but only when the process is DPI aware
# (app/dpi.py): without that, Windows virtualises coordinates on scaled
# displays and the screen grab / overlay geometry disagree with what is on
# screen (ghosting, misplaced cover).  PIEZO_ATOMIC_PAINT=0 disables,
# PIEZO_ATOMIC_PAINT=1 forces it on.
def enabled() -> bool:
    if sys.platform != "win32":
        return False
    flag = os.environ.get("PIEZO_ATOMIC_PAINT", "")
    if flag == "0":
        return False
    if flag == "1":
        return True
    from app.dpi import dpi_aware

    return dpi_aware()


def _client_rect(widget: tk.Misc) -> tuple[int, int, int, int] | None:
    """Exact on-screen client rect (x, y, w, h) of a widget's native window,
    in physical px (GetClientRect + ClientToScreen)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = wintypes.HWND(int(widget.winfo_id()))
        rc = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
            return None
        pt = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return None
        return pt.x, pt.y, rc.right - rc.left, rc.bottom - rc.top
    except Exception:
        return None


def _place_topmost(toplevel: tk.Misc, x: int, y: int, w: int, h: int) -> None:
    """Put the overlay's frame window exactly at a physical-px rect, topmost,
    without activating it."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetParent.restype = wintypes.HWND
        frame = user32.GetParent(wintypes.HWND(int(toplevel.winfo_id())))
        if frame:
            # HWND_TOPMOST, SWP_NOACTIVATE | SWP_NOOWNERZORDER
            user32.SetWindowPos(frame, wintypes.HWND(-1), x, y, w, h, 0x0010 | 0x0200)
    except Exception:
        pass

def _blit_bitmap(hwnd: int, bmp: int, w: int, h: int) -> None:
    """Paint a GDI bitmap into a window right now (no waiting for WM_PAINT /
    the Tk event loop, which is about to be busy building the new page)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        user32.GetDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        dc = user32.GetDC(wintypes.HWND(hwnd))
        mem = gdi32.CreateCompatibleDC(wintypes.HDC(dc))
        old = gdi32.SelectObject(wintypes.HDC(mem), wintypes.HGDIOBJ(bmp))
        gdi32.BitBlt(wintypes.HDC(dc), 0, 0, w, h, wintypes.HDC(mem), 0, 0, 0x00CC0020)
        gdi32.SelectObject(wintypes.HDC(mem), wintypes.HGDIOBJ(old))
        gdi32.DeleteDC(wintypes.HDC(mem))
        user32.ReleaseDC(wintypes.HWND(hwnd), wintypes.HDC(dc))
        gdi32.GdiFlush()
        # Peek (no remove, no dispatch): lets Wine push the window surface to
        # the screen now; a no-op for Tk's event handling on Windows.
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
    except Exception:
        pass


def _delete_bitmap(bmp: int) -> None:
    try:
        import ctypes

        ctypes.windll.gdi32.DeleteObject(ctypes.c_void_p(bmp))
    except Exception:
        pass


def _grab_bitmap(x: int, y: int, w: int, h: int) -> int | None:
    """Screen rect -> HBITMAP (one BitBlt, no pixel conversion).  The caller
    owns the bitmap (_delete_bitmap)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        user32.GetDC.restype = wintypes.HDC
        screen = user32.GetDC(None)
        if not screen:
            return None
        mem = gdi32.CreateCompatibleDC(wintypes.HDC(screen))
        bmp = gdi32.CreateCompatibleBitmap(wintypes.HDC(screen), w, h)
        old = gdi32.SelectObject(wintypes.HDC(mem), wintypes.HGDIOBJ(bmp))
        ok = gdi32.BitBlt(wintypes.HDC(mem), 0, 0, w, h, wintypes.HDC(screen), x, y, 0x00CC0020)  # SRCCOPY
        gdi32.SelectObject(wintypes.HDC(mem), wintypes.HGDIOBJ(old))
        gdi32.DeleteDC(wintypes.HDC(mem))
        user32.ReleaseDC(None, wintypes.HDC(screen))
        if not ok:
            gdi32.DeleteObject(wintypes.HGDIOBJ(bmp))
            return None
        return int(bmp)
    except Exception:
        return None


def _frame_hwnd(widget: tk.Misc) -> int:
    """Top-level (frame) HWND of a Tk widget."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetAncestor.restype = wintypes.HWND
    return int(user32.GetAncestor(wintypes.HWND(int(widget.winfo_id())), 2) or 0)  # GA_ROOT


def _move_window(hwnd: int, x: int, y: int, w: int, h: int, show: bool) -> None:
    import ctypes
    from ctypes import wintypes

    # HWND_TOPMOST; SWP_NOACTIVATE|SWP_NOOWNERZORDER|SWP_NOREDRAW (+SHOWWINDOW)
    flags = 0x0010 | 0x0200 | 0x0008 | (0x0040 if show else 0)
    ctypes.windll.user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-1), x, y, w, h, flags)


# RedrawWindow flags
_RDW_INVALIDATE, _RDW_ERASE, _RDW_ALLCHILDREN, _RDW_UPDATENOW = 0x1, 0x4, 0x80, 0x100
_INVALIDATE = os.environ.get("PIEZO_ATOMIC_INVALIDATE", "0") == "1"


def _flush_native_paint(hwnd: int, invalidate: bool) -> None:
    """Send every pending WM_PAINT of the window tree now (synchronously)."""
    try:
        import ctypes
        from ctypes import wintypes

        flags = _RDW_ALLCHILDREN | _RDW_UPDATENOW
        if invalidate:
            flags |= _RDW_INVALIDATE | _RDW_ERASE
        ctypes.windll.user32.RedrawWindow(wintypes.HWND(hwnd), None, None, flags)
    except Exception:
        pass


def _any_update_pending(hwnd: int) -> bool:
    """True when the window or any descendant still has an update region."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = [False]
        PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(child, _lp):
            if user32.IsWindowVisible(child) and user32.GetUpdateRect(child, None, False):
                found[0] = True
                return False
            return True

        if user32.GetUpdateRect(wintypes.HWND(hwnd), None, False):
            return True
        user32.EnumChildWindows(wintypes.HWND(hwnd), PROC(cb), 0)
        return found[0]
    except Exception:
        return False


def _dwm_flush() -> None:
    try:
        import ctypes

        ctypes.windll.dwmapi.DwmFlush()
    except Exception:
        pass


# Cover is dropped at the latest this long after the click.
COVER_CAP_S = float(os.environ.get("PIEZO_ATOMIC_CAP_MS", "700")) / 1000.0
_PARK = -30000


class AtomicSwitch:
    """Snapshot cover for one content area (a widget, e.g. the root).

    The overlay is a borderless topmost toplevel created once, sized to the
    whole virtual screen and parked off-screen; it has no Tk background, so
    nothing but our BitBlt ever paints it.  cover() = one screen BitBlt into
    a bitmap + SetWindowPos + one BitBlt into the overlay (no PhotoImage, no
    Tk geometry/idle pass).  reveal() keeps the cover until Windows has no
    pending paint left anywhere in the main window, then removes it in one
    step."""

    def __init__(self, root: tk.Misc, area: tk.Misc, debug: Callable[[str], None] | None = None) -> None:
        self.root = root
        self.area = area
        self._debug = debug or (lambda _m: None)
        self._overlay: tk.Toplevel | None = None
        self._ov_frame = 0
        self._ov_client = 0
        self._bmp = 0
        self._active = False
        self._token = 0

    @property
    def active(self) -> bool:
        return self._active

    def _ensure_overlay(self) -> bool:
        if self._overlay is not None:
            return True
        try:
            import ctypes

            sm = ctypes.windll.user32.GetSystemMetrics
            vw, vh = max(1, sm(78)), max(1, sm(79))  # SM_CXVIRTUALSCREEN / CY
            ov = tk.Toplevel(self.root, bd=0, highlightthickness=0, background="")
            ov.withdraw()
            ov.overrideredirect(True)
            ov.geometry(f"{vw}x{vh}+{_PARK}+{_PARK}")
            ov.deiconify()
            ov.update_idletasks()
            ov.attributes("-topmost", True)
            self._ov_client = int(ov.winfo_id())
            self._ov_frame = _frame_hwnd(ov)
        except Exception:
            return False
        self._overlay = ov
        return bool(self._ov_frame)

    def cover(self) -> bool:
        """Freeze what the user sees: show a screenshot of the area on top."""
        if not enabled():
            return False
        t0 = time.perf_counter()
        try:
            if not self.root.winfo_viewable() or str(self.root.state()) == "iconic":
                return False
        except tk.TclError:
            return False
        rect = _client_rect(self.area)
        if rect is None:
            return False
        x, y, w, h = rect
        if w <= 1 or h <= 1 or not self._ensure_overlay():
            return False
        t1 = time.perf_counter()
        bmp = _grab_bitmap(x, y, w, h)
        if bmp is None:
            return False
        t2 = time.perf_counter()
        if self._bmp:
            _delete_bitmap(self._bmp)
        self._bmp = bmp
        try:
            _move_window(self._ov_frame, x, y, w, h, show=True)
            _blit_bitmap(self._ov_client, bmp, w, h)
        except Exception:
            self.park()
            return False
        self._active = True
        self._token += 1
        t3 = time.perf_counter()
        self._debug(
            f"atomic: cover {w}x{h}+{x}+{y} in {(t3 - t0) * 1000:.1f} ms "
            f"(setup {(t1 - t0) * 1000:.1f}, grab {(t2 - t1) * 1000:.1f}, show {(t3 - t2) * 1000:.1f})"
        )
        return True

    def reveal(self, fade_ms: int = 0, animator=None, started: float | None = None) -> None:
        """After the new page is laid out: make Windows finish painting every
        child window under the cover, then drop the cover in one step."""
        if not self._active:
            return
        token = self._token
        t_reveal = time.perf_counter()
        limit = (started if started is not None else t_reveal) + COVER_CAP_S

        def finish_paint() -> None:
            if token != self._token or not self._active:
                return
            t_idle = time.perf_counter()
            try:
                main = _frame_hwnd(self.root)
            except Exception:
                main = 0
            rounds = events = 0
            invalidate = _INVALIDATE
            while main and time.perf_counter() < limit:
                rounds += 1
                # Pending WM_PAINTs -> Tk Expose events now, then let Tk
                # handle them and run the resulting idle redraws.
                _flush_native_paint(main, invalidate)
                invalidate = False
                n = 0
                try:
                    while n < 2000 and self.root.tk.dooneevent(_WINDOW_EVENTS | _DONT_WAIT):
                        n += 1
                    self.root.update_idletasks()
                except tk.TclError:
                    break
                if token != self._token:
                    return  # a newer switch took over (event re-entered show)
                events += n
                if n == 0 and not _any_update_pending(main):
                    break
            _dwm_flush()  # our GDI output is in the next composed frame
            now = time.perf_counter()
            if started is not None:
                self._debug(
                    f"atomic: page painted {(now - started) * 1000:.1f} ms after click "
                    f"(first idle {(t_idle - started) * 1000:.1f}, flush {(now - t_idle) * 1000:.1f} ms, "
                    f"{rounds} rounds, {events} events{', CAP' if now >= limit else ''})"
                )
            self.park()

        try:
            self.root.after_idle(finish_paint)
            self.root.after(1500, lambda: token == self._token and self._active and self.park())  # safety
        except tk.TclError:
            self.park()

    def park(self) -> None:
        self._active = False
        if self._ov_frame:
            try:
                _move_window(self._ov_frame, _PARK, _PARK, 1, 1, show=False)
            except Exception:
                pass
        if self._bmp:
            _delete_bitmap(self._bmp)
            self._bmp = 0


import _tkinter  # noqa: E402

_WINDOW_EVENTS = getattr(_tkinter, "WINDOW_EVENTS", 4)
_DONT_WAIT = getattr(_tkinter, "DONT_WAIT", 2)
