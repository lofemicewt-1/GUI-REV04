"""Present complete scroll frames and avoid rebuilding live resize frames."""
from __future__ import annotations

import sys
import tkinter as tk
from typing import Callable, Any


class ViewportPresentation:
    def __init__(self, root: tk.Misc, navigation: Any, transition: Any) -> None:
        self.root, self.navigation, self.transition = root, navigation, transition
        self.resizing = False
        self._scroll_canvas: tk.Canvas | None = None
        self._closed = False
        self._resize_check = None
        self._prime_job = None
        self._scroll_job = None
        self._scroll_queue: list[tuple[tk.Canvas, Callable[[], Any]]] = []
        self._last_size = None
        self._tag = f'ViewportPresentation{id(self)}'
        root._piezo_viewport_presentation = self
        root.bindtags((self._tag, *root.bindtags()))
        root.bind_class(self._tag, '<Map>', self._on_map)
        root.bind_class(self._tag, '<Configure>', self._on_configure)
        root.bind_class(self._tag, '<FocusIn>', self._queue_prime)
        root.bind_class(self._tag, '<Escape>', lambda _event: self.end_resize())
        root.bind_class(self._tag, '<Destroy>', self._on_destroy)

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self._last_size = (self.root.winfo_width(), self.root.winfo_height())
            self._queue_prime()

    def _queue_prime(self, _event=None) -> None:
        if not self._closed and self._prime_job is None and self.transition._overlay is None:
            self._prime_job = self.root.after(50, self._prime)

    def _prime(self) -> None:
        self._prime_job = None
        if not self._closed:
            self.transition.prime(force=True)

    def _button_down(self) -> bool:
        if sys.platform != 'win32':
            return False
        import ctypes as c
        from ctypes import wintypes as w
        api = c.WinDLL('user32', use_last_error=True)
        api.GetAsyncKeyState.argtypes, api.GetAsyncKeyState.restype = [c.c_int], c.c_short
        return bool(api.GetAsyncKeyState(1) & 0x8000)

    def _native_resize_input(self) -> bool:
        if not self._button_down() or self.root.state() != 'normal':
            return False
        import ctypes as c
        from ctypes import wintypes as w
        from app.transitions import _frame_hwnd, _physical_pixels
        api = c.WinDLL('user32', use_last_error=True)
        api.GetCursorPos.argtypes = [c.POINTER(w.POINT)]
        api.GetWindowRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
        pointer, rect = w.POINT(), w.RECT()
        with _physical_pixels():
            hwnd = _frame_hwnd(self.root)
            if not api.GetCursorPos(c.byref(pointer)) or not api.GetWindowRect(hwnd, c.byref(rect)):
                return False
            dpi = api.GetDpiForWindow(w.HWND(hwnd)) if hasattr(api, 'GetDpiForWindow') else 96
        margin = max(8, round(12*(dpi or 96)/96))
        if not (rect.left-margin <= pointer.x <= rect.right+margin and rect.top-margin <= pointer.y <= rect.bottom+margin):
            return False
        return (min(abs(pointer.x-rect.left), abs(pointer.x-rect.right)) <= margin
                or min(abs(pointer.y-rect.top), abs(pointer.y-rect.bottom)) <= margin)

    def _on_configure(self, event: tk.Event) -> None:
        size = (event.width, event.height)
        if self._closed or event.widget is not self.root or size == self._last_size:
            return
        previous, self._last_size = self._last_size, size
        if previous is not None and not self.resizing and self._native_resize_input():
            self.begin_resize()

    def _check_resize_end(self) -> None:
        self._resize_check = None
        if not self.resizing or self._closed:
            return
        if not self._button_down():
            self.end_resize()
        else:
            self._resize_check = self.root.after(32, self._check_resize_end)

    def begin_resize(self) -> bool:
        if self._closed or self.resizing or not self.root.winfo_viewable():
            return False
        from pages.base_page import hide_scroll_chrome
        hide_scroll_chrome()
        self.navigation.cancel_pending_navigation()
        self.transition.finish()
        if not self.transition.cover(self.navigation.content_shell, force=True):
            return False
        self.transition.follow_owner_resize()
        self.transition._abort = self._restore_content
        self.resizing = True
        self.navigation.content_shell.grid_remove()
        self._resize_check = self.root.after(32, self._check_resize_end)
        return True

    def _restore_content(self) -> None:
        if not self.resizing:
            return
        self.resizing = False
        if self._resize_check is not None:
            self.root.after_cancel(self._resize_check)
            self._resize_check = None
        self.transition.stop_following_resize()
        if not self._closed:
            self.navigation.content_shell.grid()
            self.navigation._sync_content_window()

    def end_resize(self) -> None:
        if not self.resizing:
            return
        self._restore_content()
        self.transition.reveal(0, self.navigation._page_layout_ready, wait_s=2)

    def scroll(self, canvas: tk.Canvas, operation: Callable[[], Any]) -> Any:
        if self._closed or self.resizing or sys.platform != 'win32' or not canvas.winfo_ismapped():
            return operation()
        if self.navigation._pending_page is not None or self.navigation._pending_mode is not None:
            return operation()
        if self.transition.active:
            # Keep the already scheduled frame boundary. Restarting it on
            # every wheel event would hide all movement until scrolling stops.
            if self._scroll_canvas is canvas:
                self._scroll_queue.append((canvas, operation))
                return ''
            return operation()
        self.flush_scroll(canvas)
        # The native window stays mapped behind an opaque, owner-clipped frame.
        # Reveal atomically after its pending paint, with no cross-fade/row grow.
        if not self.transition.cover(canvas, force=True, sync_present=False, pass_input=True):
            return operation()
        self._scroll_canvas = canvas
        self.transition._abort = self._clear_scroll
        try:
            result = operation()
            self.transition.reveal(0, lambda: bool(canvas.winfo_ismapped()) and self.navigation._page_layout_ready(),
                                   wait_s=1.0, on_ready=self._clear_scroll,
                                   quiet_ms=0, stable_frames=1)
            return result
        except BaseException:
            self.transition.finish()
            raise

    def _clear_scroll(self) -> None:
        self._scroll_canvas = None
        if self._scroll_queue and self._scroll_job is None and not self._closed:
            self._scroll_job = self.root.after(0, self._run_scroll_queue)

    def flush_scroll(self, canvas: tk.Canvas) -> None:
        """Queries observe all earlier commands, including a queued frame."""
        if not self._scroll_queue:
            return
        selected = [operation for target, operation in self._scroll_queue if target is canvas]
        self._scroll_queue = [(target, operation) for target, operation in self._scroll_queue if target is not canvas]
        if canvas.winfo_exists():
            for operation in selected:
                operation()

    def _run_scroll_queue(self) -> None:
        self._scroll_job = None
        queued, self._scroll_queue = self._scroll_queue, []
        if self._closed:
            return
        # Native windows move once after this batch, while intervening wheel
        # input accumulates for the next complete frame. Preserve command order.
        for canvas in dict.fromkeys(target for target, _operation in queued):
            if canvas.winfo_exists():
                operations = [operation for target, operation in queued if target is canvas]
                self.scroll(canvas, lambda ops=operations: [operation() for operation in ops])

    def pointer_target(self, widget: tk.Misc) -> tk.Misc:
        if self._scroll_canvas is not None and widget is self.transition._overlay:
            return self._scroll_canvas
        return widget

    def canvas_destroyed(self, canvas: tk.Canvas) -> None:
        self._scroll_queue = [(target, operation) for target, operation in self._scroll_queue if target is not canvas]
        if self._scroll_canvas is canvas:
            self.transition.finish()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._scroll_queue.clear()
        if self._scroll_job is not None:
            self.root.after_cancel(self._scroll_job)
            self._scroll_job = None
        if self._prime_job is not None:
            self.root.after_cancel(self._prime_job)
            self._prime_job = None
        if self._resize_check is not None:
            self.root.after_cancel(self._resize_check)
            self._resize_check = None
        if self.resizing or self._scroll_canvas is not None:
            self.transition.finish()
        self._closed = True
        for sequence in ('<Map>', '<Configure>', '<FocusIn>', '<Escape>', '<Destroy>'):
            self.root.unbind_class(self._tag, sequence)


class ViewportCanvas(tk.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bind('<Destroy>', self._on_destroy, add='+')

    def _on_destroy(self, event):
        if event.widget is self:
            presentation = getattr(self._root(), '_piezo_viewport_presentation', None)
            if presentation is not None:
                presentation.canvas_destroyed(self)

    def _view(self, axis: str, args):
        operation = lambda: self.tk.call(self._w, axis, *args)
        presentation = getattr(self._root(), '_piezo_viewport_presentation', None)
        if args and presentation is not None:
            return presentation.scroll(self, operation)
        if presentation is not None:
            presentation.flush_scroll(self)
        return operation() if args else self._getdoubles(operation())

    def canvasx(self, screenx, gridspacing=None):
        self._flush_scroll()
        return super().canvasx(screenx, gridspacing)

    def canvasy(self, screeny, gridspacing=None):
        self._flush_scroll()
        return super().canvasy(screeny, gridspacing)

    def _flush_scroll(self):
        presentation = getattr(self._root(), '_piezo_viewport_presentation', None)
        if presentation is not None:
            presentation.flush_scroll(self)

    def yview(self, *args):
        return self._view('yview', args)

    def xview(self, *args):
        return self._view('xview', args)

    def yview_moveto(self, fraction):
        return self.yview('moveto', fraction)

    def xview_moveto(self, fraction):
        return self.xview('moveto', fraction)

    def yview_scroll(self, number, what):
        return self.yview('scroll', number, what)

    def xview_scroll(self, number, what):
        return self.xview('scroll', number, what)
