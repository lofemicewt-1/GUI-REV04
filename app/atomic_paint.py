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

# Longest the cover may stay up, from the click.
QUIET_LIMIT_S = 0.30
BUDGET_MS = 480
FADE_MIN_MS = 80
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


def _grab_ppm(x: int, y: int, w: int, h: int) -> tuple[bytes, int] | None:
    """Screen rect -> (binary PPM bytes, HBITMAP) via GDI (no PIL dependency).
    The caller owns the bitmap (_delete_bitmap)."""
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

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        bih = BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bih.biWidth, bih.biHeight, bih.biPlanes, bih.biBitCount = w, -h, 1, 32  # top-down
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(wintypes.HDC(mem), wintypes.HBITMAP(bmp), 0, h, buf, ctypes.byref(bih), 0) if ok else 0
        gdi32.SelectObject(wintypes.HDC(mem), wintypes.HGDIOBJ(old))
        gdi32.DeleteDC(wintypes.HDC(mem))
        user32.ReleaseDC(None, wintypes.HDC(screen))
        if not got:
            gdi32.DeleteObject(wintypes.HGDIOBJ(bmp))
            return None
        bgra = buf.raw
        rgb = bytearray(w * h * 3)
        rgb[0::3] = bgra[2::4]
        rgb[1::3] = bgra[1::4]
        rgb[2::3] = bgra[0::4]
        return b"P6 %d %d 255\n" % (w, h) + bytes(rgb), int(bmp)
    except Exception:
        return None


class AtomicSwitch:
    """Snapshot cover for one content area (a widget, e.g. the content canvas)."""

    def __init__(self, root: tk.Misc, area: tk.Misc, debug: Callable[[str], None] | None = None) -> None:
        self.root = root
        self.area = area
        self._debug = debug or (lambda _m: None)
        self._overlay: tk.Toplevel | None = None
        self._label: tk.Label | None = None
        self._image: tk.PhotoImage | None = None
        self._active = False
        self._token = 0

    @property
    def active(self) -> bool:
        return self._active

    def _ensure_overlay(self) -> bool:
        if self._overlay is not None:
            return True
        try:
            ov = tk.Toplevel(self.root, bd=0, highlightthickness=0)
            ov.withdraw()
            ov.overrideredirect(True)
            lbl = tk.Label(ov, bd=0, highlightthickness=0, padx=0, pady=0)
            lbl.pack(fill="both", expand=True)
            ov.attributes("-alpha", 1.0)
            ov.geometry("1x1+-4000+-4000")
            ov.deiconify()
            ov.update_idletasks()
            ov.attributes("-topmost", True)  # set once; never toggled (a toggle races wm geometry)
            # stays mapped, parked off-screen: moving never activates it
        except tk.TclError:
            return False
        self._overlay, self._label = ov, lbl
        return True

    def cover(self) -> bool:
        """Freeze what the user sees: show a screenshot of the area on top."""
        if not enabled():
            return False
        t0 = time.perf_counter()
        try:
            if not self.root.winfo_viewable() or str(self.root.state()) == "iconic":
                return False
            x, y = self.area.winfo_rootx(), self.area.winfo_rooty()
            w, h = self.area.winfo_width(), self.area.winfo_height()
        except tk.TclError:
            return False
        rect = _client_rect(self.area)
        if rect is not None:
            x, y, w, h = rect
        if w <= 1 or h <= 1 or not self._ensure_overlay():
            return False
        grabbed = _grab_ppm(x, y, w, h)
        if grabbed is None:
            return False
        data, bmp = grabbed
        try:
            img = tk.PhotoImage(master=self.root, data=data, format="ppm")
            assert self._overlay is not None and self._label is not None
            self._label.configure(image=img)
            self._image = img
            self._overlay.attributes("-alpha", 1.0)
            self._overlay.geometry(f"{w}x{h}")
            self._overlay.update_idletasks()  # paint the snapshot while still parked
            self._overlay.geometry(f"{w}x{h}+{x}+{y}")
            self._overlay.update_idletasks()  # wm geometry is applied at idle
            _place_topmost(self._overlay, x, y, w, h)
            # Paint it synchronously now that it is on screen: Tk would only
            # draw it on Expose, after the (blocking) page switch.
            _blit_bitmap(self._label.winfo_id(), bmp, w, h)
        except (tk.TclError, AssertionError):
            _delete_bitmap(bmp)
            return False
        _delete_bitmap(bmp)
        self._active = True
        self._token += 1
        self._debug(f"atomic: cover {w}x{h}+{x}+{y} in {(time.perf_counter() - t0) * 1000:.1f} ms")
        return True

    def reveal(self, fade_ms: int = 0, animator=None, started: float | None = None) -> None:
        """After the new page is laid out: let Tk finish painting it under the
        cover, then drop the cover (instantly, or cross-fade over fade_ms)."""
        if not self._active:
            return
        token = self._token

        t_reveal = time.perf_counter()
        quiet = [0]

        def finish_paint(expected: float | None = None) -> None:
            # Drop the cover only once the event loop has gone quiet: two
            # consecutive 16 ms ticks that fire on time mean Tk has no more
            # redraws / Configure follow-ups queued for the new page.
            if token != self._token:
                return
            now = time.perf_counter()
            if expected is not None and now - expected < 0.008:
                quiet[0] += 1
            else:
                quiet[0] = 0
            limit = (started if started is not None else t_reveal) + QUIET_LIMIT_S
            if quiet[0] >= 1 or now > limit:
                drop()
                return
            try:
                self.root.update_idletasks()
                self.root.after(16, finish_paint, time.perf_counter() + 0.016)
            except tk.TclError:
                self.park()

        def drop() -> None:
            if token != self._token:
                return
            if started is not None:
                self._debug(f"atomic: page painted {(time.perf_counter() - started) * 1000:.1f} ms after click")
            # Never cross-fade: blending two different pages ghosts. Opaque
            # until removal, then removed in one step.
            fade = 0
            if fade > 0 and started is not None:
                # Keep click -> settled near the 500 ms target.
                fade = int(min(fade, max(FADE_MIN_MS, BUDGET_MS - (time.perf_counter() - started) * 1000)))
            if fade > 0 and animator is not None and self._overlay is not None:
                ov = self._overlay

                def step(p: float) -> None:
                    ov.attributes("-alpha", max(0.0, 1.0 - p))

                from core import animation as anim
                animator.animate("page-transition", fade, step, ease=anim.EASE_OUT, done=self.park)
            else:
                self.park()

        try:
            self.root.after_idle(finish_paint)
            self.root.after(1500, lambda: token == self._token and self._active and self.park())  # safety
        except tk.TclError:
            self.park()

    def park(self) -> None:
        self._active = False
        ov = self._overlay
        if ov is None:
            return
        try:
            ov.geometry("+-4000+-4000")
            ov.attributes("-alpha", 1.0)
            if self._label is not None:
                self._label.configure(image="")
        except tk.TclError:
            pass
        self._image = None
