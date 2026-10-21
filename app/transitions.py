"""Snapshot cross-fades adapted from the grok prototype.

Windows-only GDI presentation; no Pillow dependency, global DPI change, nested
Tk event pump, or topmost input-blocking screen cover. Other platforms simply
apply the change immediately. The owner-scoped cover captures pointer input
inside its rectangle until the new controls are visible; the sidebar stays usable.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import sys
import time
import tkinter as tk
from typing import Callable

from core import animation as anim
from core.ui_dispatch import cancel_pending_ui_jobs

_PARK = -30000
COVER_PAINT_WAIT_S = 0.3
# Private DLL wrappers keep our pointer-sized signatures isolated from other
# modules that use ctypes.windll (including existing screenshot utilities).
if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)


@contextmanager
def _physical_pixels():
    # Only the native geometry / GDI calls use physical screen coordinates.
    # Restore the thread before calling any Tk code (preserves current DPI/layout).
    setter = getattr(_user32, "SetThreadDpiAwarenessContext", None)
    old = None
    if setter is not None:
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        old = setter(ctypes.c_void_p(-3))
    try:
        yield
    finally:
        if old:
            setter(old)


def _client_rect(widget: tk.Misc) -> tuple[int, int, int, int] | None:
    """Exact on-screen client rect (x, y, w, h) of a widget's native window,
    in physical px (GetClientRect + ClientToScreen)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = _user32
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

def _bounded_client_rect(area: tk.Misc, owner: tk.Misc) -> tuple[int, int, int, int] | None:
    """Never let an owned cover extend beyond its owner's current client."""
    source, bounds = _client_rect(area), _client_rect(owner)
    if source is None or bounds is None:
        return None
    x, y = max(source[0], bounds[0]), max(source[1], bounds[1])
    right = min(source[0]+source[2], bounds[0]+bounds[2])
    bottom = min(source[1]+source[3], bounds[1]+bounds[3])
    if right <= x or bottom <= y:
        return None
    return x, y, right-x, bottom-y


def _blit_bitmap(hwnd: int, bmp: int, w: int, h: int,
                 source_size: tuple[int, int] | None = None) -> None:
    """Paint a GDI bitmap into a window right now (no waiting for WM_PAINT /
    the Tk event loop, which is about to be busy building the new page)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32, gdi32 = _user32, _gdi32
        user32.GetDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        dc = user32.GetDC(wintypes.HWND(hwnd))
        mem = gdi32.CreateCompatibleDC(wintypes.HDC(dc))
        old = gdi32.SelectObject(wintypes.HDC(mem), wintypes.HGDIOBJ(bmp))
        if source_size is None or source_size == (w, h):
            gdi32.BitBlt(wintypes.HDC(dc), 0, 0, w, h, wintypes.HDC(mem), 0, 0, 0x00CC0020)
        else:
            gdi32.StretchBlt(wintypes.HDC(dc), 0, 0, w, h, wintypes.HDC(mem),
                             0, 0, *source_size, 0x00CC0020)
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

        _gdi32.DeleteObject(ctypes.c_void_p(bmp))
    except Exception:
        pass

def _grab_bitmap(hwnd: int, w: int, h: int, *, source_x: int = 0, source_y: int = 0) -> int | None:
    """Capture only this application's window DC; release every GDI handle."""
    from ctypes import wintypes
    user32, gdi32 = _user32, _gdi32
    user32.GetWindowDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    dc = mem = bmp = old = 0
    success = False
    try:
        dc = user32.GetWindowDC(wintypes.HWND(hwnd))
        if not dc:
            return None
        mem = gdi32.CreateCompatibleDC(wintypes.HDC(dc))
        bmp = gdi32.CreateCompatibleBitmap(wintypes.HDC(dc), w, h)
        if not mem or not bmp:
            return None
        old = gdi32.SelectObject(wintypes.HDC(mem), wintypes.HGDIOBJ(bmp))
        success = bool(gdi32.BitBlt(wintypes.HDC(mem), 0, 0, w, h,
                                  wintypes.HDC(dc), source_x, source_y, 0x00CC0020))
        return int(bmp) if success else None
    finally:
        if old and mem:
            gdi32.SelectObject(wintypes.HDC(mem), wintypes.HGDIOBJ(old))
        if mem:
            gdi32.DeleteDC(wintypes.HDC(mem))
        if dc:
            user32.ReleaseDC(wintypes.HWND(hwnd), wintypes.HDC(dc))
        if bmp and not success:
            gdi32.DeleteObject(wintypes.HGDIOBJ(bmp))

def _frame_hwnd(widget: tk.Misc) -> int:
    """Top-level (frame) HWND of a Tk widget."""
    import ctypes
    from ctypes import wintypes

    user32 = _user32
    user32.GetAncestor.restype = wintypes.HWND
    return int(user32.GetAncestor(wintypes.HWND(int(widget.winfo_id())), 2) or 0)

def _move_window(hwnd: int, x: int, y: int, w: int, h: int, show: bool) -> None:
    import ctypes
    from ctypes import wintypes

    # HWND_TOP; SWP_NOACTIVATE|SWP_NOOWNERZORDER|SWP_NOREDRAW (+SHOWWINDOW)
    flags = 0x0010 | 0x0200 | 0x0008 | (0x0040 if show else 0)
    _user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(0), x, y, w, h, flags)

def _cloak(hwnd: int, on: bool) -> bool:
    """DWMWA_CLOAK: window stays mapped and keeps rendering into its DWM
    surface, but is not composed on screen."""
    try:
        import ctypes
        from ctypes import wintypes

        val = wintypes.BOOL(1 if on else 0)
        return _dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), 13, ctypes.byref(val), ctypes.sizeof(val)) == 0
    except Exception:
        return False

def _dwm_flush() -> None:
    try:
        import ctypes

        _dwmapi.DwmFlush()
    except Exception:
        pass

def _make_layered(hwnd: int) -> bool:
    """Give the overlay WS_EX_LAYERED once, at creation (alpha 255), so the
    fade only changes the alpha -- toggling the style later forces a fresh
    surface and shows a stale frame."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = _user32
        user32.GetWindowLongW.restype = ctypes.c_long
        ex = user32.GetWindowLongW(wintypes.HWND(hwnd), -20)
        user32.SetWindowLongW(wintypes.HWND(hwnd), -20,
                             (ex | 0x00080000 | 0x08000000 | 0x00000080) & ~0x00000020)
        return bool(_set_alpha(hwnd, 255))
    except Exception:
        return False

def _set_alpha(hwnd: int, alpha: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        return bool(_user32.SetLayeredWindowAttributes(
            wintypes.HWND(hwnd), 0, max(0, min(255, int(alpha))), 0x2))  # LWA_ALPHA
    except Exception:
        return False


def _native_paint_pending(area: tk.Misc) -> bool:
    if sys.platform != "win32":
        return False
    from ctypes import wintypes
    main_id = int(area.winfo_id())
    _user32.GetParent.restype = wintypes.HWND
    clips = {}
    def client_bounds(hwnd):
        if hwnd not in clips:
            rc, pt = wintypes.RECT(), wintypes.POINT()
            native = wintypes.HWND(hwnd)
            if not _user32.GetClientRect(native, ctypes.byref(rc)) or not _user32.ClientToScreen(native, ctypes.byref(pt)):
                return None
            clips[hwnd] = (pt.x, pt.y, pt.x+rc.right, pt.y+rc.bottom)
        return clips[hwnd]
    def visibly_dirty(hwnd):
        rc, pt = wintypes.RECT(), wintypes.POINT()
        native = wintypes.HWND(hwnd)
        if not _user32.GetUpdateRect(native, ctypes.byref(rc), False):
            return False
        pt.x, pt.y = rc.left, rc.top
        _user32.ClientToScreen(native, ctypes.byref(pt))
        left, top, right, bottom = pt.x, pt.y, pt.x+rc.right-rc.left, pt.y+rc.bottom-rc.top
        parent = hwnd
        while parent:
            bounds = client_bounds(parent)
            if bounds is None:
                return False
            left, top = max(left, bounds[0]), max(top, bounds[1])
            right, bottom = min(right, bounds[2]), min(bottom, bounds[3])
            if left >= right or top >= bottom:
                return False
            if int(parent) == main_id:
                return True
            parent = _user32.GetParent(wintypes.HWND(parent))
        return False
    if visibly_dirty(main_id):
        return True
    found = False
    visitor = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def check(child, _data):
        nonlocal found
        # Scrolled-out controls can retain update regions indefinitely. Only
        # pixels inside every ancestor's client clip can delay the reveal.
        if _user32.IsWindowVisible(child) and visibly_dirty(child):
            found = True
            return False
        return True
    _user32.EnumChildWindows(wintypes.HWND(main_id), visitor(check), 0)
    return found


def _request_native_paint(area: tk.Misc) -> None:
    if sys.platform == "win32":
        from ctypes import wintypes
        # Send pending WM_PAINTs; Tk processes the resulting Expose events in
        # its normal main loop. No recursive update()/input dispatch here.
        _user32.RedrawWindow(wintypes.HWND(int(area.winfo_id())), None, None, 0x180)


class SnapshotTransition:
    def __init__(self, root: tk.Misc, animator: anim.Animator) -> None:
        self.root = root
        self.animator = animator
        self._overlay: tk.Toplevel | None = None
        self._frame = self._client = self._bitmap = 0
        self._job: str | None = None
        self._token = 0
        self.active = False
        self._capable = False
        self._area: tk.Misc | None = None
        self._bitmap_size = (1, 1)
        self._duration_ms = anim.DUR_SNAP
        self._ready: Callable[[], bool] | None = None
        self._paint_activity = time.perf_counter()
        self._resize_offset: tuple[int, int] | None = None
        # Live logs/ADC updates may expose pixels continuously. Wait for
        # geometry to settle and visible native paint to finish, rather than
        # requiring the entire application to stop producing new content.
        self._paint_bindings = [("<Configure>", root.bind("<Configure>", self._paint_changed, add="+"))]
        self._key = ("snapshot", id(self))
        # An owner-only bind tag avoids callbacks for every descendant event.
        self._tag = f"MotionOwner{id(self)}"
        root.bindtags((self._tag, *root.bindtags()))
        for sequence in ("<Configure>", "<Unmap>"):
            root.bind_class(self._tag, sequence, self._owner_changed)
        root.bind_class(self._tag, "<Destroy>", self._owner_destroyed)
        self._focus_binding = root.bind("<FocusOut>", self._owner_focus_changed, add="+")

    def _paint_changed(self, event: tk.Event) -> None:
        if not self.active or self._area is None:
            return
        path, area = str(event.widget), str(self._area)
        overlay = str(self._overlay) if self._overlay is not None else ""
        if overlay and (path == overlay or path.startswith(overlay+".")):
            return
        if area == "." or path == area or path.startswith(area+"."):
            self._paint_activity = time.perf_counter()

    def _owner_destroyed(self, _event=None) -> None:
        # Also cover test harnesses / Tcl callers that destroy Tk directly.
        self.animator.stop()
        dispatcher = getattr(self.root, "_piezo_ui_dispatcher", None)
        if dispatcher is not None:
            dispatcher.close()
        self._frame = self._client = 0
        self.finish()
        cancel_pending_ui_jobs(self.root)

    def _owner_is_foreground(self) -> bool:
        if sys.platform != "win32":
            return True
        from ctypes import wintypes
        _user32.GetForegroundWindow.restype = wintypes.HWND
        foreground = _user32.GetForegroundWindow()
        # Windows can report no foreground window during activation changes
        # or on an isolated validation desktop. Do not guess an external app.
        if not foreground:
            return True
        _user32.GetAncestor.restype = wintypes.HWND
        owner = _frame_hwnd(self.root)
        return int(_user32.GetAncestor(wintypes.HWND(foreground), 3) or foreground) == owner

    def _owner_focus_changed(self, _event=None) -> None:
        if self.active and not self._owner_is_foreground():
            self.finish()

    def _owner_changed(self, _event=None) -> None:
        if self.active:
            if self._resize_offset is not None and _event is not None and _event.type == tk.EventType.Configure:
                with _physical_pixels():
                    owner = _client_rect(self.root)
                    if owner is not None:
                        dx, dy = self._resize_offset
                        width = min(self._bitmap_size[0], owner[2]-dx)
                        height = min(self._bitmap_size[1], owner[3]-dy)
                        if min(width, height) > 0:
                            _move_window(self._frame, owner[0]+dx, owner[1]+dy, width, height, True)
                            _blit_bitmap(self._client, self._bitmap, width, height)
                            self._cover_owner_rect = owner
                            return
            if _event is not None and _event.type == tk.EventType.Configure:
                with _physical_pixels():
                    if _client_rect(self.root) == getattr(self, '_cover_owner_rect', None):
                        return
            # The bitmap describes the previous client area. Moving/resizing,
            # maximizing or hiding the owner invalidates that presentation.
            self.finish()

    def follow_owner_resize(self) -> None:
        """Crop this one frozen frame to the owner's live client; never stretch."""
        if self.active and self._area is not None:
            with _physical_pixels():
                rect = _bounded_client_rect(self._area, self.root)
                owner = _client_rect(self.root)
            if rect is not None and owner is not None:
                self._resize_offset = (rect[0]-owner[0], rect[1]-owner[1])

    def stop_following_resize(self) -> None:
        self._resize_offset = None

    def prime(self, *, force: bool = False) -> None:
        """Realize the native surface before page construction queues redraws."""
        if sys.platform == 'win32' and (force or self.animator.enabled) and self._owner_is_foreground():
            self._ensure_overlay()

    def _ensure_overlay(self) -> bool:
        if self._overlay is not None:
            return self._capable
        overlay = tk.Toplevel(self.root, bd=0, highlightthickness=0, background="", cursor="watch")
        overlay.bind("<ButtonPress>", lambda _event: "break")
        overlay.bind("<ButtonRelease>", lambda _event: "break")
        overlay.withdraw()
        self._overlay = overlay
        overlay.overrideredirect(True)
        overlay.transient(self.root)
        overlay.geometry(f"1x1{_PARK}{_PARK}")
        overlay.deiconify()
        # Windows must realize its wrapper once. Prime this surface before
        # preparing a page; subsequent covers do not enter the idle loop.
        overlay.update_idletasks()
        self._client = int(overlay.winfo_id())
        self._frame = _frame_hwnd(overlay)
        self._capable = bool(self._frame and _make_layered(self._frame))
        return self._capable

    def cover(self, area: tk.Misc, *, force: bool = False, sync_present: bool = True,
              pass_input: bool = False) -> bool:
        if not self._owner_is_foreground():
            self.finish()
            return False
        if self.active and self._area is area:
            # Rapid navigation keeps the already visible, complete old page.
            self._token += 1
            self.animator.cancel(self._key)
            if self._job is not None:
                self.root.after_cancel(self._job)
                self._job = None
            _set_alpha(self._frame, 255)
            self._paint_activity = time.perf_counter()
            return True
        self.finish()
        if sys.platform != "win32" or (not force and not self.animator.enabled) or not self.root.winfo_viewable():
            return False
        try:
            with _physical_pixels():
                rect = _bounded_client_rect(area, self.root)
                source = _client_rect(area)
                if rect is None or source is None or min(rect[2:]) <= 1:
                    return False
                self._bitmap = _grab_bitmap(int(area.winfo_id()), *rect[2:],
                                            source_x=rect[0]-source[0], source_y=rect[1]-source[1]) or 0
                self._bitmap_size = rect[2:]
            if not self._bitmap or not self._ensure_overlay():
                self.finish()
                return False
            self._overlay.configure(cursor='' if pass_input else 'watch')
            from ctypes import wintypes
            ex = _user32.GetWindowLongW(wintypes.HWND(self._frame), -20)
            flags = (ex | 0x20) if pass_input else (ex & ~0x20)
            _user32.SetWindowLongW(wintypes.HWND(self._frame), -20, flags)
            with _physical_pixels():
                _cloak(self._frame, True)
                _set_alpha(self._frame, 255)
                _move_window(self._frame, *rect, show=True)
                _blit_bitmap(self._client, self._bitmap, *rect[2:])
                if sync_present:
                    _dwm_flush()
                _cloak(self._frame, False)
                if sync_present:
                    _dwm_flush()
            self.active = True
            with _physical_pixels():
                self._cover_owner_rect = _client_rect(self.root)
            self._area = area
            self._paint_activity = time.perf_counter()
            return True
        except (tk.TclError, OSError, AttributeError):
            self.finish()
            return False

    def reveal(self, duration_ms: int, ready: Callable[[], bool] | None = None, *,
               wait_s: float | None = None, on_ready: Callable[[], None] | None = None,
               quiet_ms: int | None = None, stable_frames: int = 2) -> None:
        if not self.active:
            return
        token = self._token
        self._duration_ms = duration_ms
        self._ready = ready
        started = time.perf_counter()
        wait_budget = COVER_PAINT_WAIT_S if wait_s is None else wait_s
        quiet_frames = 0

        def painted() -> None:
            nonlocal quiet_frames
            self._job = None
            if token != self._token or not self.active:
                return
            if not self._owner_is_foreground():
                self.finish()
                return
            layout_ready = ready is None or ready()
            now = time.perf_counter()
            quiet = now-self._paint_activity >= (2*anim.FRAME_MS if quiet_ms is None else quiet_ms)/1000
            pending = _native_paint_pending(self._area or self.root)
            quiet_frames = quiet_frames + 1 if layout_ready and quiet and not pending else 0
            if quiet_frames < stable_frames:
                if layout_ready and quiet and pending:
                    _request_native_paint(self._area or self.root)
                # Mapping, Expose and idle drawing run in the ordinary loop.
                # A nested update_idletasks() repeatedly redraws the whole
                # hierarchy and blocks input, especially on cross-chip visits.
                # A busy page can delay even the first check past the budget.
                # Let settled layout accumulate stable frames, but bound churn.
                elapsed = now-started
                if (not self.root.winfo_viewable()
                        or elapsed > wait_budget + 1.0
                        or (elapsed > wait_budget and not (layout_ready and quiet))):
                    self.finish()
                    return
                self._job = self.root.after(anim.FRAME_MS, painted)
                return
            _dwm_flush()
            try:
                if on_ready is not None:
                    on_ready()
            except BaseException:
                self.finish()
                raise
            self._abort = None
            def step(p: float) -> None:
                if token == self._token and self.active:
                    if not self._owner_is_foreground():
                        self.finish()
                        return
                    _set_alpha(self._frame, round(255*(1-p)))
            self.animator.animate(self._key, duration_ms, step, done=self.finish)

        # after_idle can precede Map/Expose of newly gridded native windows.
        self._job = self.root.after(anim.FRAME_MS, painted)

    def change(self, area: tk.Misc, duration_ms: int, action: Callable[[], None],
               *, ready: Callable[[], bool] | None = None, wait_s: float | None = None,
               on_ready: Callable[[], None] | None = None,
               on_cancel: Callable[[], None] | None = None) -> None:
        covered = self.cover(area)
        self._abort = on_cancel if covered else None
        try:
            action()
        except BaseException:
            self.finish()
            raise
        if covered:
            self.reveal(duration_ms, ready, wait_s=wait_s, on_ready=on_ready)
        elif on_ready is not None:
            on_ready()

    def finish(self) -> None:
        self._resize_offset = None
        abort_error = None
        abort, self._abort = getattr(self, '_abort', None), None
        if abort is not None:
            try:
                abort()
            except tk.TclError:
                pass
            except BaseException as error:
                abort_error = error
        self._token += 1
        self.animator.cancel(self._key)
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        self.active = False
        if self._frame:
            _cloak(self._frame, False)
            with _physical_pixels():
                _move_window(self._frame, _PARK, _PARK, 1, 1, show=False)
        if self._bitmap:
            _delete_bitmap(self._bitmap)
            self._bitmap = 0
        if abort_error is not None:
            raise abort_error

    def close(self) -> None:
        self.finish()
        for sequence, funcid in self._paint_bindings:
            self.root.unbind(sequence, funcid)
        self._paint_bindings.clear()
        if self._focus_binding is not None:
            self.root.unbind("<FocusOut>", self._focus_binding)
            self._focus_binding = None
        for sequence in ("<Configure>", "<Unmap>", "<Destroy>"):
            self.root.unbind_class(self._tag, sequence)
        if self._overlay is not None:
            self._overlay.destroy()
            self._overlay = None
            self._frame = self._client = 0
