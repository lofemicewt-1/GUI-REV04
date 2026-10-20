from __future__ import annotations

from pathlib import Path
import re
import tkinter as tk
import threading
import webbrowser
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Any

from config.app_info import CHIP_WEBSITE_URLS
from config.languages import t
from core.ui_dispatch import post_to_ui
from core import animation as anim
from styles.fonts import sidebar_brand_font_spec, page_title_weight
from styles.theme import get_colors
from app.viewport_presentation import ViewportCanvas


def fit_toplevel_to_screen(
    window: tk.Toplevel,
    *,
    preferred_width: int,
    preferred_height: int,
    minimum_width: int = 360,
    minimum_height: int = 240,
) -> tuple[int, int]:
    """Size and place a child window inside conservative screen bounds.

    Fixed dialog minimums larger than the display leave controls outside the
    screen before a user can reach them.  The caller is expected to place
    content in a scroll area when it has a larger natural request size.  The
    bounds reserve space for window chrome/taskbar margins and are also capped
    by the owning application's client size; this helper does not query the
    native Win32 work-area rectangle.
    """

    try:
        window.update_idletasks()
        screen_width = max(320, int(window.winfo_screenwidth()))
        screen_height = max(240, int(window.winfo_screenheight()))
    except tk.TclError:
        screen_width, screen_height = 1280, 720

    available_width = max(320, screen_width - 32)
    available_height = max(240, screen_height - 96)
    # Keep auxiliary windows inside the active application window as well.
    # The owning page can be wider than the root because it lives in a
    # horizontal canvas, so use the real top-level client bounds here.
    try:
        owner = window.master.winfo_toplevel()
        owner_width = int(owner.winfo_width())
        owner_height = int(owner.winfo_height())
        if owner_width > 1:
            available_width = min(available_width, max(320, owner_width - 32))
        if owner_height > 1:
            available_height = min(available_height, max(240, owner_height - 96))
    except (tk.TclError, AttributeError, TypeError, ValueError):
        pass
    width = min(max(1, int(preferred_width)), available_width)
    height = min(max(1, int(preferred_height)), available_height)
    minimum_width = min(max(1, int(minimum_width)), width)
    minimum_height = min(max(1, int(minimum_height)), height)
    window.minsize(minimum_width, minimum_height)
    window.geometry(f"{width}x{height}")

    try:
        window.update_idletasks()
        parent = window.master.winfo_toplevel()
        parent_x = int(parent.winfo_rootx())
        parent_y = int(parent.winfo_rooty())
        parent_width = int(parent.winfo_width())
        parent_height = int(parent.winfo_height())
        x = parent_x + max(0, (parent_width - width) // 2)
        y = parent_y + max(0, (parent_height - height) // 2)
        x = max(16, min(x, screen_width - width - 16))
        y = max(32, min(y, screen_height - height - 48))
        window.geometry(f"{width}x{height}+{x}+{y}")
    except (tk.TclError, AttributeError, TypeError, ValueError):
        pass
    return width, height

def build_serial_log_export_content(history: list[str], visible_text: str) -> str:
    """Return the complete serial log export text.

    Prefer the serial manager history, but merge in visible Text content so
    response frames such as ``MCU ^Done~`` are not lost if a page appended them
    directly to the log widget.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for source_line in [*history, *visible_text.splitlines()]:
        line = source_line.rstrip()
        if not line or line in seen:
            continue
        lines.append(line)
        seen.add(line)
    return "\n".join(lines)

CANVAS_SCROLL_PX = 32
_SCROLL_HIDE_SEQUENCES = (
    "<MouseWheel>",
    "<Shift-MouseWheel>",
    "<Button-4>",
    "<Button-5>",
    "<Shift-Button-4>",
    "<Shift-Button-5>",
)
_hiding_scroll_chrome = False


def scaled_scroll_px(scale: float) -> int:
    try:
        value = float(scale)
    except (TypeError, ValueError):
        value = 1.0
    if value <= 0:
        value = 1.0
    return max(1, int(round(CANVAS_SCROLL_PX * value)))


class SimpleTooltip:
    _open: list[SimpleTooltip] = []

    def __init__(self, widget: tk.Widget, text_getter: Any) -> None:
        self.widget = widget
        self.text_getter = text_getter
        self.tip: tk.Toplevel | None = None
        self._position_job: str | None = None
        self._generation = 0
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)
        widget.bind("<Unmap>", self._hide, add="+")
        widget.bind("<Destroy>", self._on_widget_destroy, add="+")
        self._ensure_scroll_hook()

    def _ensure_scroll_hook(self) -> None:
        try:
            root = self.widget._root()
        except tk.TclError:
            return
        if getattr(root, "_piezo_tooltip_scroll_hook", False):
            return
        root._piezo_tooltip_scroll_hook = True
        for sequence in _SCROLL_HIDE_SEQUENCES:
            root.bind_all(sequence, SimpleTooltip.hide_all, add="+")

    @classmethod
    def hide_all(cls, _event: tk.Event | None = None) -> None:
        for tip in list(cls._open):
            tip._hide()

    def _on_widget_destroy(self, event: tk.Event) -> None:
        if getattr(event, "widget", None) is not self.widget:
            return
        self._hide()

    def _show(self, _event: tk.Event) -> None:
        self._hide()
        text = self.text_getter() if callable(self.text_getter) else str(self.text_getter)
        if not text:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.withdraw()
        if self not in SimpleTooltip._open:
            SimpleTooltip._open.append(self)
        self.tip.wm_overrideredirect(True)
        label = tk.Label(
            self.tip,
            text=text,
            justify="left",
            bg="#111827",
            fg="#F9FAFB",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
            wraplength=320,
        )
        label.pack()
        tip = self.tip
        generation = self._generation
        self._position_job = self.widget.after_idle(
            lambda: self._position(tip, generation, x, y)
        )

    def _position(self, tip: tk.Toplevel, generation: int, x: int, y: int) -> None:
        if generation != self._generation or self.tip is not tip:
            return
        self._position_job = None
        try:
            if not tip.winfo_exists() or not self.widget.winfo_ismapped():
                self._hide()
                return
            screen_width = tip.winfo_screenwidth()
            screen_height = tip.winfo_screenheight()
            tip_width = tip.winfo_reqwidth()
            tip_height = tip.winfo_reqheight()
            x = min(max(8, x), max(8, screen_width - tip_width - 8))
            if y + tip_height + 8 > screen_height:
                y = max(8, self.widget.winfo_rooty() - tip_height - 8)
            y = min(max(8, y), max(8, screen_height - tip_height - 8))
            tip.wm_geometry(f"+{x}+{y}")
            animator = anim.animator_for(self.widget)
            if animator is not None and animator.enabled:
                tip.attributes('-alpha', 0)
            tip.deiconify()
            if animator is not None and animator.enabled:
                animator.animate(("tooltip", id(self)), anim.GRAPHEME_MS,
                                 lambda p: tip.attributes("-alpha", p), ease=anim.EASE_GRAPHEME)
        except tk.TclError:
            self._hide()

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._generation += 1
        if self._position_job is not None:
            try:
                self.widget.after_cancel(self._position_job)
            except tk.TclError:
                pass
            self._position_job = None
        try:
            SimpleTooltip._open.remove(self)
        except ValueError:
            pass
        try:
            animator = anim.animator_for(self.widget)
        except tk.TclError:
            animator = None
        if animator is not None:
            animator.cancel(("tooltip", id(self)))
        tip = self.tip
        self.tip = None
        if tip is not None:
            try:
                tip.destroy()
            except tk.TclError:
                pass


def hide_scroll_chrome() -> None:
    global _hiding_scroll_chrome
    if _hiding_scroll_chrome:
        return
    _hiding_scroll_chrome = True
    try:
        SimpleTooltip.hide_all()
        try:
            from app.micro_anim import MicroAnimations
        except ImportError:
            return
        try:
            MicroAnimations.hide_open_bars()
        except tk.TclError:
            pass
    finally:
        _hiding_scroll_chrome = False


def relay_vertical_scroll(
    scrollbar: ttk.Scrollbar,
    previous: tuple[str, str] | None,
    first: str,
    last: str,
) -> tuple[str, str]:
    scrollbar.set(first, last)
    key = (str(first), str(last))
    if key != previous:
        hide_scroll_chrome()
    return key


class VerticalScrollArea(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        ctx: Any,
        *,
        horizontal: bool = True,
        min_content_width: int = 0,
        propagate_width: bool = False,
        propagate_height: bool = False,
        clamp_width: bool = False,
        reserve_vertical_gutter: bool = False,
        scroll_region_extra: int = 16,
    ) -> None:
        super().__init__(parent, style="App.TFrame")
        self.ctx = ctx
        self.horizontal = horizontal
        self.min_content_width = max(0, min_content_width)
        self.propagate_width = bool(propagate_width)
        self.propagate_height = bool(propagate_height)
        self.clamp_width = bool(clamp_width)
        self.reserve_vertical_gutter = bool(reserve_vertical_gutter)
        self.scroll_region_extra = max(0, int(scroll_region_extra))
        self._scrollbar_visible = False
        self._horizontal_scrollbar_visible = False
        self._layout_job: str | None = None
        self._yview_key: tuple[str, str] | None = None
        self._xview_key: tuple[str, str] | None = None

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.canvas = ViewportCanvas(
            self,
            highlightthickness=0,
            borderwidth=0,
            yscrollincrement=1,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        if self.reserve_vertical_gutter:
            # Wrapping footers also resize their viewport to the content height.
            # Keep the content width fixed when the scrollbar appears/disappears,
            # otherwise delayed wrapping and height fitting can oscillate forever.
            self.columnconfigure(1, minsize=self.scrollbar.winfo_reqwidth())
        self.horizontal_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(
            yscrollcommand=self._on_yview,
            xscrollcommand=self._on_xview,
        )

        self.content = ttk.Frame(self.canvas, style="App.TFrame")
        self.content.columnconfigure(0, weight=1)
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.content.bind("<Configure>", self._queue_scroll_layout)
        self.canvas.bind("<Configure>", self._queue_scroll_layout)
        self.bind("<Destroy>", self._cancel_scroll_layout, add="+")
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.canvas.bind_all("<Shift-MouseWheel>", self._on_shift_mousewheel, add="+")
        self.refresh_theme()

    def _pointer_widget(self, event: tk.Event) -> tk.Misc:
        try:
            widget = self.winfo_containing(int(event.x_root), int(event.y_root))
            if widget is not None:
                presentation = getattr(self._root(), '_piezo_viewport_presentation', None)
                if presentation is not None:
                    widget = presentation.pointer_target(widget)
                return widget
        except (tk.TclError, AttributeError, TypeError, ValueError):
            pass
        return event.widget

    def _natural_content_size(self) -> tuple[int, int]:
        return (
            max(1, self.min_content_width, self.content.winfo_reqwidth()),
            max(1, self.content.winfo_reqheight()),
        )

    def _window_item_span(self, option: str) -> int:
        try:
            return int(float(self.canvas.itemcget(self.window_id, option)))
        except (tk.TclError, TypeError, ValueError):
            return 0

    def _queue_scroll_layout(self, _event: tk.Event | None = None) -> None:
        # One content resize also configures its canvas and scrollbars. Read
        # their final dimensions once instead of repainting at every boundary.
        if self._layout_job is None:
            owner = self._root()
            pending = getattr(owner, "_piezo_pending_scroll_layouts", None)
            if pending is None:
                pending = owner._piezo_pending_scroll_layouts = set()
            pending.add(self)
            self._layout_job = self.after(0, self._run_scroll_layout)

    def _run_scroll_layout(self) -> None:
        self._layout_job = None
        self._sync_scroll_region()

    def _cancel_scroll_layout(self, event: tk.Event | None = None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
        getattr(self._root(), "_piezo_pending_scroll_layouts", set()).discard(self)

    def _sync_scroll_region(self, _event: tk.Event | None = None) -> None:
        self._cancel_scroll_layout()
        try:
            natural_width, natural_height = self._natural_content_size()
            canvas_width = max(1, self.canvas.winfo_width())
            canvas_height = max(1, self.canvas.winfo_height())
            if self.horizontal and not (self.clamp_width and canvas_width > 1):
                width = max(natural_width, canvas_width)
            else:
                width = canvas_width
            if self._window_item_span("width") != width:
                self.canvas.itemconfigure(self.window_id, width=width)
            height = natural_height + self.scroll_region_extra
            if self.propagate_height:
                requested_height = max(natural_height, canvas_height - self.scroll_region_extra)
                if self._window_item_span("height") != requested_height:
                    self.canvas.itemconfigure(self.window_id, height=requested_height)
                height = max(height, requested_height)
                if natural_height + self.scroll_region_extra <= canvas_height:
                    height = min(height, canvas_height)
            region = (0, 0, width, height)
            if tuple(map(float, self.canvas.tk.splitlist(self.canvas.cget("scrollregion")))) != region:
                self.canvas.configure(scrollregion=region)
            needs_scrollbar = height > canvas_height
            needs_horizontal_scrollbar = self.horizontal and not self.clamp_width and width > canvas_width
            if needs_scrollbar and not self._scrollbar_visible:
                self.scrollbar.grid(row=0, column=1, sticky="ns")
                self._scrollbar_visible = True
            elif not needs_scrollbar and self._scrollbar_visible:
                self.scrollbar.grid_remove()
                self._scrollbar_visible = False
            if needs_horizontal_scrollbar and not self._horizontal_scrollbar_visible:
                self.horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
                self._horizontal_scrollbar_visible = True
            elif not needs_horizontal_scrollbar and self._horizontal_scrollbar_visible:
                self.horizontal_scrollbar.grid_remove()
                self._horizontal_scrollbar_visible = False
            self._propagate_canvas_width()
        except tk.TclError:
            pass

    def _requested_content_width(self) -> int:
        return max(1, self.min_content_width, self.content.winfo_reqwidth())

    def _propagate_canvas_width(self) -> None:
        if not self.propagate_width:
            return
        req = self._requested_content_width()
        if self.canvas.winfo_reqwidth() != req:
            self.canvas.configure(width=req)

    def _sync_content_width(self, event: tk.Event) -> None:
        try:
            if self.horizontal and not (self.clamp_width and event.width > 1):
                width = max(1, event.width, self.min_content_width, self.content.winfo_reqwidth())
            else:
                width = max(1, event.width)
            if self._window_item_span("width") != width:
                self.canvas.itemconfigure(self.window_id, width=width)
            self._sync_scroll_region()
        except tk.TclError:
            pass

    def _y_increment(self) -> int:
        state = getattr(self.ctx, "state", None)
        return scaled_scroll_px(getattr(state, "ui_scale", 1.0) if state is not None else 1.0)

    def _on_yview(self, first: str, last: str) -> None:
        self._yview_key = relay_vertical_scroll(self.scrollbar, self._yview_key, first, last)

    def _on_xview(self, first: str, last: str) -> None:
        self._xview_key = relay_vertical_scroll(self.horizontal_scrollbar, self._xview_key, first, last)

    def _on_mousewheel(self, event: tk.Event) -> None:
        try:
            if not self.winfo_ismapped() or self._nearest_scroll_area(self._pointer_widget(event)) is not self:
                return
            delta = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(delta * self._y_increment(), "units")
        except tk.TclError:
            pass

    def _on_shift_mousewheel(self, event: tk.Event) -> None:
        try:
            if not self.horizontal or not self.winfo_ismapped() or self._nearest_scroll_area(self._pointer_widget(event)) is not self:
                return
            delta = -1 if event.delta > 0 else 1
            self.canvas.xview_scroll(delta, "units")
        except tk.TclError:
            pass

    def _contains_widget(self, widget: tk.Misc) -> bool:
        return self._nearest_scroll_area(widget) is self

    @staticmethod
    def _nearest_scroll_area(widget: tk.Misc) -> VerticalScrollArea | None:
        current: tk.Misc | None = widget
        while current is not None:
            if isinstance(current, VerticalScrollArea):
                return current
            current = getattr(current, "master", None)
        return None

    def refresh_theme(self) -> None:
        self.canvas.configure(bg=self.ctx.colors["bg"], yscrollincrement=1)


class WrappingGroupBar(ttk.Frame):
    def __init__(self, parent: tk.Misc, *, style: str = "Panel.TFrame") -> None:
        super().__init__(parent, style=style)
        self._groups: list[ttk.Frame] = []
        self._hidden: set[int] = set()
        self._signature: tuple[Any, ...] | None = None
        self._gap = 8
        self._in_reflow = False
        self.bind("<Configure>", self._on_configure, add="+")

    def add_group(self) -> ttk.Frame:
        group = ttk.Frame(self, style="Panel.TFrame")
        self._groups.append(group)
        group.grid(row=0, column=len(self._groups) - 1, sticky="w", padx=(0, self._gap))
        return group

    def set_group_visible(self, group: ttk.Frame, visible: bool) -> None:
        if visible:
            self._hidden.discard(id(group))
        else:
            self._hidden.add(id(group))
            group.grid_remove()
        self._signature = None
        width = self.winfo_width()
        if width > 1:
            self.reflow(width)

    def visible_groups(self) -> list[ttk.Frame]:
        return [group for group in self._groups if id(group) not in self._hidden]

    def natural_width(self) -> int:
        groups = self.visible_groups()
        if not groups:
            return 1
        total = 0
        for group in groups:
            total += max(1, int(group.winfo_reqwidth())) + self._gap
        return max(1, total)

    def _rows_for(self, available: int) -> list[list[ttk.Frame]]:
        rows: list[list[ttk.Frame]] = []
        current: list[ttk.Frame] = []
        used = 0
        limit = max(1, available)
        for group in self.visible_groups():
            width = max(1, int(group.winfo_reqwidth()))
            if current and used + self._gap + width > limit:
                rows.append(current)
                current = [group]
                used = width
            else:
                if current:
                    used += self._gap
                current.append(group)
                used += width
        if current:
            rows.append(current)
        return rows

    def reflow(self, available: int) -> None:
        if available <= 1 or self._in_reflow:
            return
        rows = self._rows_for(available)
        signature = tuple(tuple(id(group) for group in row) for row in rows)
        if signature == self._signature:
            return
        self._in_reflow = True
        self._signature = signature
        try:
            for group in self._groups:
                group.grid_remove()
            for row_index, row in enumerate(rows):
                for column, group in enumerate(row):
                    group.grid(
                        row=row_index,
                        column=column,
                        sticky="w",
                        padx=(0, self._gap),
                        pady=(0, 4),
                    )
        finally:
            self._in_reflow = False

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        self.reflow(int(event.width))


class FooterSplit(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.columnconfigure(0, weight=1)
        self.registers = WrappingGroupBar(self)
        self.identity = WrappingGroupBar(self)
        self.registers.grid(row=0, column=0, sticky="ew")
        self.identity.grid(row=0, column=1, sticky="e")
        self._signature: tuple[Any, ...] | None = None
        self._in_reflow = False
        self.bind("<Configure>", self._on_configure, add="+")

    def reflow(self, available: int) -> None:
        if available <= 1 or self._in_reflow:
            return
        gap = 16
        reg_w = self.registers.natural_width()
        id_w = self.identity.natural_width()
        side_by_side = reg_w + gap + id_w <= available
        signature = (available // 8, side_by_side)
        if signature == self._signature:
            return
        self._in_reflow = True
        self._signature = signature
        try:
            if side_by_side:
                self.columnconfigure(1, weight=1)
                self.registers.grid(row=0, column=0, columnspan=1, sticky="w", pady=0)
                self.identity.grid(row=0, column=1, columnspan=1, sticky="e", padx=(gap, 0), pady=0)
                self.registers.reflow(max(1, reg_w))
                self.identity.reflow(max(1, id_w))
            else:
                self.columnconfigure(1, weight=0)
                self.registers.grid(row=0, column=0, columnspan=2, sticky="ew", padx=0, pady=0)
                self.identity.grid(row=1, column=0, columnspan=2, sticky="ew", padx=0, pady=(4, 0))
                self.registers.reflow(available)
                self.identity.reflow(available)
        finally:
            self._in_reflow = False

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        self.reflow(int(event.width))


_CHIP_TITLE_KEYS = {"awp1921_1923", "awp1921", "awp1923", "awp1922", "awp1018", "awp1221", "awp1744_awp1766"}


def _font_probe(root: tk.Misc, family: str, weight: str) -> tkfont.Font:
    owner = root._root()
    probes = getattr(owner, "_motion_font_probes", None)
    if probes is None:
        probes = owner._motion_font_probes = {}
    key = (family, weight)
    probe = probes.get(key)
    if probe is None:
        probe = probes[key] = tkfont.Font(root=owner, family=family, size=12, weight=weight)
    return probe


def _font_linespace(root: tk.Misc, family: str, weight: str, size: int) -> int:
    owner = root._root()
    scaling = str(root.tk.call("tk", "scaling"))
    cache = getattr(owner, "_motion_font_linespace_cache", None)
    if cache is None:
        cache = owner._motion_font_linespace_cache = {}
    key = (family, weight, scaling, int(size))
    hit = cache.get(key)
    if hit is None:
        probe = _font_probe(root, family, weight)
        probe.configure(size=int(size))
        hit = cache[key] = int(probe.metrics("linespace"))
    return hit


def _font_size_for_pixel_height(root: tk.Misc, family: str, weight: str, target_px: int) -> int:
    owner = root._root()
    cache = getattr(owner, "_motion_font_height_cache", None)
    if cache is None:
        cache = owner._motion_font_height_cache = {}
    key = (family, weight, int(target_px), str(root.tk.call("tk", "scaling")))
    if key not in cache:
        cache[key] = _font_size_for_pixel_height_uncached(root, family, weight, target_px)
    return cache[key]


def _wordmark_width(root: tk.Misc, family: str, size: int, weight: str) -> int:
    owner = root._root()
    cache = getattr(owner, "_motion_wordmark_width_cache", None)
    if cache is None:
        cache = owner._motion_wordmark_width_cache = {}
    key = (family, size, weight, str(root.tk.call("tk", "scaling")))
    hit = cache.get(key)
    if hit is None:
        probe = _font_probe(root, family, weight)
        probe.configure(size=int(size))
        hit = cache[key] = int(probe.measure("PIEZO CONTROL"))
    return hit


def _fit_font_size(root: tk.Misc, family: str, weight: str, target: int) -> int | None:
    ref = _font_linespace(root, family, weight, 12)
    if ref > target:
        return 12
    est = max(12, min(72, int(target * 12 / ref)))
    steps = 0
    if _font_linespace(root, family, weight, est) <= target:
        while est < 72 and _font_linespace(root, family, weight, est + 1) <= target:
            est += 1
            steps += 1
            if steps > 8:
                return None
        return est
    while est > 12 and _font_linespace(root, family, weight, est) > target:
        est -= 1
        steps += 1
        if steps > 8:
            return None
    return est


def _font_size_for_pixel_height_uncached(root: tk.Misc, family: str, weight: str, target_px: int) -> int:
    target = max(12, int(target_px))
    fitted = _fit_font_size(root, family, weight, target)
    if fitted is not None:
        return fitted
    chosen = 12
    for size in range(12, 73):
        if _font_linespace(root, family, weight, size) <= target:
            chosen = size
            continue
        break
    return chosen


def _prominent_title(title_key: str) -> bool:
    if title_key in {"awp1921_1923", "awp1921", "awp1923", "awp1922", "awp1018", "awp1221", "awp1744_awp1766"}:
        return True
    return title_key.endswith("_mode")


_TITLE_TEXT_PAD = 2
_TITLE_FONT_SEQ = 0


def _title_cache(widget: tk.Misc) -> dict[str, Any]:
    owner = widget._root()
    cache = getattr(owner, '_piezo_title_cache', None)
    if cache is None:
        cache = {'fonts': {}, 'metrics': {}, 'widths': {}, 'realized': set()}
        owner._piezo_title_cache = cache
        def clear(event: tk.Event) -> None:
            if event.widget is owner:
                for entries in cache.values():
                    entries.clear()
        owner.bind('<Destroy>', clear, add='+')
    return cache


def _title_scaling(widget: tk.Misc) -> str:
    try:
        return str(widget.tk.call("tk", "scaling"))
    except tk.TclError:
        return ""


def _title_font_attrs(font: tuple[Any, ...]) -> tuple[Any, ...]:
    family = "TkDefaultFont"
    size = 0
    weight = "normal"
    slant = "roman"
    underline = 0
    overstrike = 0
    if font:
        family = str(font[0])
        index = 1
        if len(font) > 1:
            try:
                size = int(font[1])
                index = 2
            except (TypeError, ValueError):
                size = 0
        for part in font[index:]:
            token = str(part).lower()
            if token in {"bold", "normal"}:
                weight = token
            elif token in {"italic", "roman"}:
                slant = token
            elif token == "underline":
                underline = 1
            elif token == "overstrike":
                overstrike = 1
    return (family, size, weight, slant, underline, overstrike)


def _title_font(widget: tk.Misc, font: tuple[Any, ...]) -> tkfont.Font:
    global _TITLE_FONT_SEQ
    attrs = _title_font_attrs(font)
    key = attrs
    fonts = _title_cache(widget)['fonts']
    cached = fonts.get(key)
    if cached is not None:
        return cached
    family, size, weight, slant, underline, overstrike = attrs
    _TITLE_FONT_SEQ += 1
    cached = tkfont.Font(
        root=widget,
        name=f"piezoCanvasTitle{_TITLE_FONT_SEQ}",
        family=family,
        size=int(size),
        weight=str(weight),
        slant=str(slant),
        underline=bool(underline),
        overstrike=bool(overstrike),
    )
    fonts[key] = cached
    return cached


def _title_metrics(widget: tk.Misc, font: tuple[Any, ...]) -> tuple[int, int]:
    key = (_title_font_attrs(font), _title_scaling(widget))
    metrics = _title_cache(widget)['metrics']
    hit = metrics.get(key)
    if hit is None:
        info = _title_font(widget, font).metrics()
        hit = (int(info["linespace"]), int(info["ascent"]))
        metrics[key] = hit
    return hit


def _title_text_width(widget: tk.Misc, font: tuple[Any, ...], text: str) -> int:
    key = (_title_font_attrs(font), _title_scaling(widget), text)
    widths = _title_cache(widget)['widths']
    hit = widths.get(key)
    if hit is None:
        hit = int(_title_font(widget, font).measure(text))
        widths[key] = hit
    return hit


def _title_font_holder(widget: tk.Misc) -> tk.Label:
    owner = widget._root()
    holder = getattr(owner, "_piezo_title_font_holder", None)
    if holder is None:
        holder = tk.Label(owner)
        owner._piezo_title_font_holder = holder
    return holder


def _ensure_title_font(widget: tk.Misc, font: tuple[Any, ...]) -> None:
    attrs = _title_font_attrs(font)
    key = attrs
    realized = _title_cache(widget)['realized']
    if key in realized:
        return
    probe = _title_font(widget, font)
    try:
        _title_font_holder(widget).configure(font=probe.name)
    except tk.TclError:
        return
    _title_metrics(widget, font)
    realized.add(key)


def _warm_title_fonts(widget: tk.Misc) -> None:
    try:
        root = widget.winfo_toplevel()
    except tk.TclError:
        return
    weight = page_title_weight(root)
    title_size = int(getattr(root, "_piezo_title_font_size", 18) or 18)
    prominent = int(getattr(root, "_piezo_prominent_title_font_size", title_size + 1) or (title_size + 1))
    chip = int(getattr(root, "_piezo_chip_title_font_size", 0) or (prominent + 6))
    for size in {int(title_size), int(prominent), int(chip)}:
        _ensure_title_font(widget, ("Microsoft YaHei UI", size, weight))
        _ensure_title_font(widget, ("Microsoft YaHei UI", size, weight, "underline"))


def _colors_of(widget: tk.Misc | None) -> dict[str, str]:
    current = widget
    while current is not None:
        colors = getattr(getattr(current, "ctx", None), "colors", None)
        if isinstance(colors, dict) and colors.get("bg") and colors.get("text"):
            return colors
        current = getattr(current, "master", None)
    try:
        root = widget.winfo_toplevel() if widget is not None else None
        theme = str(getattr(root, "_piezo_theme", "light"))
    except (tk.TclError, AttributeError):
        theme = "light"
    return get_colors(theme)


class CanvasTitle(tk.Canvas):
    """Page or mode title drawn with create_text so it paints on map."""

    def __init__(self, parent: tk.Misc, *, style: str = "Title.TLabel", text: str = "", **kwargs: Any) -> None:
        self._style_name = style
        self._text = ""
        self._underline = False
        self._font: tuple[Any, ...] = ("Microsoft YaHei UI", 18, "bold")
        self._item: int | None = None
        self._fit_key: tuple[Any, ...] | None = None
        self._fit_job: str | None = None
        self._item_font: str | None = None
        kwargs.pop("highlightthickness", None)
        kwargs.pop("bd", None)
        kwargs.pop("borderwidth", None)
        background = kwargs.pop("background", None) or kwargs.pop("bg", None) or _colors_of(parent).get("bg", "#F5F7FA")
        super().__init__(
            parent,
            highlightthickness=0,
            bd=0,
            background=background,
            width=_TITLE_TEXT_PAD * 2,
            height=_TITLE_TEXT_PAD * 2,
            takefocus=0,
            **kwargs,
        )
        _warm_title_fonts(self)
        self._font = self._role_font()
        self._underline = any(str(part).lower() == "underline" for part in self._font)
        self._text = str(text) if text else ""
        self._item = self.create_text(
            _TITLE_TEXT_PAD,
            _TITLE_TEXT_PAD,
            text="",
            anchor="nw",
            fill=self._foreground(),
            tags=("title",),
        )
        self._register_title()
        self._queue_fit()

    def _register_title(self) -> None:
        widget: tk.Misc | None = self.master
        while widget is not None:
            titles = getattr(widget, "_canvas_titles", None)
            if isinstance(titles, list):
                titles.append(self)
                return
            widget = getattr(widget, "master", None)

    def _foreground(self) -> str:
        return _colors_of(self).get("text", "#111827")

    def _background(self) -> str:
        return _colors_of(self).get("bg", "#F5F7FA")

    def _role_font(self) -> tuple[Any, ...]:
        root = self.winfo_toplevel()
        weight = page_title_weight(root)
        title_size = int(getattr(root, "_piezo_title_font_size", 18) or 18)
        prominent = int(getattr(root, "_piezo_prominent_title_font_size", title_size + 1) or (title_size + 1))
        if self._style_name == "ChipTitle.TLabel":
            size = int(getattr(root, "_piezo_chip_title_font_size", 0) or 0) or prominent + 6
        elif self._style_name == "Prominent.TLabel":
            size = prominent
        else:
            size = title_size
        font: tuple[Any, ...] = ("Microsoft YaHei UI", int(size), weight)
        if self._underline:
            return (*font, "underline")
        return font

    def _queue_fit(self) -> None:
        if self._fit_job is not None:
            return
        try:
            self._fit_job = self.after_idle(self._idle_fit)
        except tk.TclError:
            self._fit()

    def _idle_fit(self) -> None:
        self._fit_job = None
        self._fit()

    def _cancel_queued_fit(self) -> None:
        job = self._fit_job
        if job is None:
            return
        self._fit_job = None
        try:
            self.after_cancel(job)
        except tk.TclError:
            pass

    def _apply_font(self, font: tuple[Any, ...]) -> None:
        font = tuple(font)
        self._underline = any(str(part).lower() == "underline" for part in font)
        changed = font != self._font or self._fit_key is None
        self._font = font
        if self._item is None:
            return
        if changed:
            self._cancel_queued_fit()
            self._fit()
            return
        self._repaint()

    def _repaint(self) -> None:
        if self._item is None:
            return
        background = self._background()
        try:
            current_bg = str(self.cget("background"))
        except tk.TclError:
            return
        if current_bg.lower() != background.lower():
            super().configure(background=background)
        self.itemconfigure(self._item, fill=self._foreground())

    def _fit(self) -> None:
        if self._item is None:
            return
        self._cancel_queued_fit()
        probe = _title_font(self, self._font)
        if self._item_font != probe.name:
            self.itemconfigure(self._item, font=probe.name)
            self._item_font = probe.name
        linespace, _ascent = _title_metrics(self, self._font)
        width = max(_TITLE_TEXT_PAD * 2, _title_text_width(self, self._font, self._text) + _TITLE_TEXT_PAD * 2)
        height = max(_TITLE_TEXT_PAD * 2, linespace + _TITLE_TEXT_PAD * 2)
        foreground = self._foreground()
        background = self._background()
        key = (self._font, self._text, foreground, background.lower(), width, height)
        if key == self._fit_key:
            return
        self._fit_key = key
        try:
            current_w = int(float(self.cget("width")))
            current_h = int(float(self.cget("height")))
            current_bg = str(self.cget("background"))
        except (tk.TclError, TypeError, ValueError):
            current_w = current_h = -1
            current_bg = ""
        if current_w != width or current_h != height or current_bg.lower() != background.lower():
            super().configure(width=width, height=height, background=background)
        self.itemconfigure(self._item, text=self._text, fill=foreground, font=probe.name)
        self.coords(self._item, _TITLE_TEXT_PAD, _TITLE_TEXT_PAD)

    def refresh(self) -> None:
        try:
            self._apply_font(self._role_font())
        except tk.TclError:
            return

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        if cnf is None and not kwargs:
            return super().configure()
        if isinstance(cnf, str) and not kwargs:
            if cnf in {"text", "font", "style"}:
                return self.cget(cnf)
            return super().configure(cnf)
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
        text = kwargs.pop("text", None)
        font = kwargs.pop("font", None)
        foreground = kwargs.pop("foreground", kwargs.pop("fg", None))
        changed = False
        if font is not None:
            new_font = self._font_tuple(font)
            self._underline = any(str(part).lower() == "underline" for part in new_font)
            if new_font != self._font or self._fit_key is None:
                self._font = new_font
                changed = True
        if text is not None:
            new_text = str(text)
            if new_text != self._text or self._fit_key is None:
                self._text = new_text
                changed = True
        if changed or self._fit_key is None:
            self._cancel_queued_fit()
            self._fit()
        if foreground is not None and self._item is not None:
            self.itemconfigure(self._item, fill=foreground)
        if kwargs:
            super().configure(**kwargs)
        return None

    config = configure

    def cget(self, key: str) -> Any:
        if key == "style":
            return self._style_name
        if key == "text":
            return self._text
        if key == "font":
            return self._font
        if key in {"foreground", "fg"}:
            return self._foreground()
        return super().cget(key)

    def __getitem__(self, key: str) -> Any:
        return self.cget(key)

    def destroy(self) -> None:
        self._cancel_queued_fit()
        widget: tk.Misc | None = getattr(self, "master", None)
        while widget is not None:
            titles = getattr(widget, "_canvas_titles", None)
            if isinstance(titles, list):
                try:
                    titles.remove(self)
                except ValueError:
                    pass
                break
            widget = getattr(widget, "master", None)
        super().destroy()

    def _font_tuple(self, font: Any) -> tuple[Any, ...]:
        if isinstance(font, (tuple, list)):
            return tuple(font)
        try:
            return tuple(self.tk.splitlist(str(font)))
        except tk.TclError:
            return tuple(str(font).split())


class BasePage(ttk.Frame):
    """
    \u6240\u6709\u9875\u9762\u7684\u57fa\u7c7b\u3002

    \u5b50\u9875\u9762\u5efa\u8bae\u5b9e\u73b0\uff1a
        build_ui()
        refresh_language()
        refresh_theme()
        on_show()
    """

    page_name: str = "base"
    pin_window_footer = False

    def __init__(self, parent: tk.Misc, ctx: Any) -> None:
        super().__init__(parent, style="App.TFrame")
        self.ctx = ctx
        self._text_widgets: list[tuple[Any, str]] = []
        self._logo_widgets: list[ttk.Label] = []
        self._chip_title_labels: list[CanvasTitle] = []
        self._canvas_titles: list[CanvasTitle] = []
        self._logo_images: list[tk.PhotoImage] = []
        self._chrome_headers: list[ttk.Frame] = []
        self._mode_headers: list[ttk.Frame] = []
        self._title_spacing_widgets: list[tk.Misc] = []
        self._module_spacing_widgets: list[tuple[tk.Misc, bool, bool]] = []
        self._header_layout_job: str | None = None
        self._serial_log_text: tk.Text | None = None
        self._serial_log_listener: Any = None
        self._serial_log_clear_listener: Any = None
        self._vertical_scroll_areas: list[VerticalScrollArea] = []
        self._serial_selectors: list[dict[str, Any]] = []

    @property
    def language(self) -> str:
        return self.ctx.state.language

    @property
    def colors(self) -> dict[str, str]:
        return self.ctx.colors

    def tr(self, key: str, **kwargs: Any) -> str:
        return t(self.language, key, **kwargs)

    def bind_text(self, widget: Any, key: str) -> None:
        """
        \u6ce8\u518c\u9700\u8981\u968f\u8bed\u8a00\u5207\u6362\u66f4\u65b0 text \u7684\u63a7\u4ef6\u3002
        """
        self._text_widgets.append((widget, key))
        try:
            widget.configure(text=self.tr(key))
        except Exception:
            pass

    def refresh_language(self) -> None:
        for widget, key in self._text_widgets:
            try:
                widget.configure(text=self.tr(key))
            except Exception:
                pass
        self._refresh_serial_selectors()
        self._sync_mode_headers()
        self._queue_header_layout()

    def _sync_mode_button_styles(self) -> None:
        current = getattr(self, "current_mode", None)
        mode_pages = getattr(self, "mode_pages", {})
        for key, btn in getattr(self, "mode_buttons", {}).items():
            if key not in mode_pages:
                continue
            try:
                btn.configure(
                    style="SidebarModeSelected.TButton" if key == current else "SidebarMode.TButton"
                )
            except tk.TclError:
                pass

    def refresh_theme(self) -> None:
        """
        子类里有 Canvas / Text 这种非 ttk 控件时，覆盖这个方法更新颜色。
        """
        self._refresh_header_logos()
        self._refresh_canvas_titles()
        self._sync_header_chrome()
        self._sync_mode_headers()
        self._queue_header_layout()
        self._refresh_serial_log_theme()
        for area in self._vertical_scroll_areas:
            area.refresh_theme()

    def _refresh_canvas_titles(self) -> None:
        live: list[CanvasTitle] = []
        for title in self._canvas_titles:
            try:
                if not int(title.winfo_exists()):
                    continue
                title.refresh()
            except tk.TclError:
                continue
            live.append(title)
        self._canvas_titles = live

    def on_show(self) -> None:
        self._refresh_serial_selectors()

    def make_serial_selector(
        self,
        parent: tk.Misc,
        *,
        page_name: str | None = None,
        row: int = 0,
        column: int = 0,
        padx: tuple[int, int] = (0, 8),
    ) -> int:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=column, sticky="w", padx=padx)
        self.bind_text(label, "communication_port")

        variable = tk.StringVar()
        combo = ttk.Combobox(parent, textvariable=variable, values=[], state="readonly", width=22)
        combo.grid(row=row, column=column + 1, sticky="w", padx=(0, 18))

        control: dict[str, Any] = {
            "page_name": page_name or self.page_name,
            "variable": variable,
            "combo": combo,
            "options": {},
        }
        combo.bind("<<ComboboxSelected>>", lambda _event, item=control: self._on_serial_selector_changed(item))
        self._serial_selectors.append(control)
        self._refresh_serial_selector(control)
        return column + 2

    def _serial_options(self) -> list[tuple[str, str]]:
        provider = getattr(self.ctx.serial, "connected_port_options", None)
        if callable(provider):
            return list(provider())
        selected = getattr(self.ctx.state, "selected_port", None)
        return [(selected, selected)] if selected else []

    def _refresh_serial_selectors(self) -> None:
        for control in self._serial_selectors:
            self._refresh_serial_selector(control)

    def _refresh_serial_selector(self, control: dict[str, Any]) -> None:
        options = self._serial_options()
        option_map = {label: port for label, port in options}
        control["options"] = option_map
        combo = control["combo"]
        variable = control["variable"]
        combo.configure(values=list(option_map))

        selected_port = None
        selector = getattr(self.ctx.serial, "selected_port_for_page", None)
        if callable(selector):
            selected_port = selector(control["page_name"])
        else:
            selected_port = getattr(self.ctx.state, "selected_port", None)

        selected_label = next((label for label, port in options if port == selected_port), "")
        if variable.get() != selected_label:
            variable.set(selected_label)

    def _on_serial_selector_changed(self, control: dict[str, Any]) -> None:
        label = control["variable"].get()
        port = control.get("options", {}).get(label)
        binder = getattr(self.ctx.serial, "set_page_port", None)
        if callable(binder):
            binder(control["page_name"], port)

    def enable_pinned_footer_layout(self) -> None:
        self.pin_window_footer = True
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

    def make_merged_footer(self, parent: tk.Misc | None = None) -> tuple[ttk.Frame, ttk.Frame, WrappingGroupBar, WrappingGroupBar]:
        footer = ttk.Frame(self if parent is None else parent, style="Panel.TFrame", padding=(12, 10))
        footer.columnconfigure(0, weight=1)
        actions = ttk.Frame(footer, style="Panel.TFrame")
        actions.grid(row=0, column=0, sticky="ew")
        split = FooterSplit(footer)
        split.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        try:
            self.after_idle(lambda: split.reflow(max(1, split.winfo_width())))
        except tk.TclError:
            pass
        return footer, actions, split.registers, split.identity

    def make_vertical_scroll_area(
        self,
        parent: tk.Misc,
        *,
        horizontal: bool = True,
        min_content_width: int = 0,
        propagate_width: bool = False,
        propagate_height: bool = False,
        clamp_width: bool = False,
        reserve_vertical_gutter: bool = False,
        scroll_region_extra: int = 16,
    ) -> tuple[VerticalScrollArea, ttk.Frame]:
        area = VerticalScrollArea(
            parent,
            self.ctx,
            horizontal=horizontal,
            min_content_width=min_content_width,
            propagate_width=propagate_width,
            propagate_height=propagate_height,
            clamp_width=clamp_width,
            reserve_vertical_gutter=reserve_vertical_gutter,
            scroll_region_extra=scroll_region_extra,
        )
        self._vertical_scroll_areas.append(area)
        return area, area.content

    def make_title(
        self,
        parent: tk.Misc,
        title_key: str,
        subtitle: str | None = None,
        *,
        show_logo: bool = True,
    ) -> ttk.Frame:
        _warm_title_fonts(self)
        header = ttk.Frame(parent, style="App.TFrame")
        self._title_spacing_widgets.append(header)
        header.columnconfigure(0, weight=1)

        title_style = "ChipTitle.TLabel" if title_key in _CHIP_TITLE_KEYS else (
            "Prominent.TLabel" if _prominent_title(title_key) else "Title.TLabel"
        )
        if title_key == "awp1921_1923":
            title_group = ttk.Frame(header, style="App.TFrame")
            title_group.grid(row=0, column=0, sticky="nw", padx=(8, 0))
            self._make_linked_title_part(title_group, "AWP1921", "awp1921", 0, title_style)
            slash = CanvasTitle(title_group, text="/", style=title_style)
            slash.grid(row=0, column=1, sticky="w")
            slash.configure(font=("Microsoft YaHei UI", self._chip_title_size(), page_title_weight(self)))
            self._chip_title_labels.append(slash)
            self._make_linked_title_part(title_group, "AWP1923", "awp1923", 2, title_style)
        else:
            title = CanvasTitle(header, style=title_style)
            title.grid(row=0, column=0, sticky="nw", padx=(8, 0) if show_logo else 0)
            self.bind_text(title, title_key)
            self._configure_title_link(title, title_key)
            if title_style == "ChipTitle.TLabel":
                self._chip_title_labels.append(title)

        if subtitle:
            sub = ttk.Label(header, style="Subtitle.TLabel")
            sub.grid(row=1, column=0, sticky="w", padx=(8, 0) if show_logo else 0, pady=(3, 0))
            if subtitle.startswith("@"):
                self.bind_text(sub, subtitle[1:])
            else:
                sub.configure(text=subtitle)

        if show_logo:
            logo = self._make_header_logo(header)
            logo.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
            header.bind("<Configure>", self._queue_header_layout, add="+")
            self._chrome_headers.append(header)
            self._sync_header_chrome_widget(header)
        else:
            self._mode_headers.append(header)
            header.bind("<Configure>", self._queue_header_layout, add="+")
            self._sync_mode_header(header)

        return header

    def make_preset_frame(self, header: ttk.Frame) -> ttk.Frame:
        """Keep the five action columns aligned and reachable on small windows."""
        area, content = self.make_vertical_scroll_area(header, scroll_region_extra=0)
        area.grid(row=0, column=1, sticky="new", padx=(24, 0))
        frame = ttk.Frame(content, style="App.TFrame")
        frame.grid(row=0, column=0, sticky="nw")
        header._preset_area = area
        header._preset_frame = frame
        area.bind("<Configure>", self._queue_header_layout, add="+")
        frame.bind("<Configure>", self._queue_header_layout, add="+")
        viewport = VerticalScrollArea._nearest_scroll_area(header)
        if viewport is not None:
            viewport.canvas.bind("<Configure>", self._queue_header_layout, add="+")
        self._sync_mode_header(header)
        return frame

    def _sync_mode_headers(self) -> None:
        for header in self._mode_headers:
            self._sync_mode_header(header)
        self._queue_header_layout()

    def _sync_mode_header(self, header: ttk.Frame) -> None:
        # Unmapped probes follow ttk font/theme changes without visible controls.
        root = self._root()
        probes = getattr(root, "_piezo_header_probes", None)
        if probes is None or len(probes) != 3:
            probes = (ttk.Button(root, style="Bg.TButton"),
                      ttk.Combobox(root, width=4),
                      ttk.Label(root, style="App.TLabel"))
            root._piezo_header_probes = probes
        button, combo, slot_label = probes
        combo.configure(font=("Microsoft YaHei UI", int(getattr(root, "_piezo_body_font_size", 10))))
        size = int(getattr(root, "_piezo_prominent_title_font_size", 19))
        font = ("Microsoft YaHei UI", size, page_title_weight(self))
        title_keys = ("button_mode", "full_mode", "awp1018_normal_mode", "awp1018_diff_mode")
        title_width = max(_title_text_width(self, font, t(lang, key))
                          for lang in ("zh", "en") for key in title_keys) + 4
        header.columnconfigure(0, weight=0, minsize=title_width)
        header.columnconfigure(1, weight=1)
        header._mode_title_width = title_width
        area = getattr(header, "_preset_area", None)
        if area is not None:
            keys = ("button_mode_preset_1", "button_mode_import_user_preset",
                    "button_mode_export_user_preset", "save_to_board", "set_active",
                    "apply_slot", "read_slot", "awp1221_type4_active",
                    "awp1221_type4_apply", "awp1221_type4_read",
                    "awp1221_preset_1_reserved", "awp1221_import_reserved",
                    "awp1221_export_reserved")
            widths = []
            for key in keys:
                button.configure(text=self.tr(key))
                widths.append(button.winfo_reqwidth())
            body_font = ("Microsoft YaHei UI", int(getattr(root, "_piezo_body_font_size", 10)))
            slot_label.configure(text=self.tr("board_slot"), font=body_font)
            slot_width = slot_label.winfo_reqwidth() + combo.winfo_reqwidth() + 6
            width = max(118, slot_width, *widths) + 6
            for column in range(5):
                header._preset_frame.columnconfigure(column, weight=1,
                    uniform="page_preset_columns", minsize=width)
            module_gap = self._module_spacing()
            for child in header._preset_frame.grid_slaves(row=1):
                if self._grid_pady(child) != (module_gap, 0):
                    child.grid_configure(pady=(module_gap, 0))
            content_height = max(1, int(header._preset_frame.winfo_reqheight()))
            if int(area.canvas.cget("height")) != content_height:
                area.canvas.configure(height=content_height)
        self._queue_header_layout()

    def _queue_header_layout(self, _event: tk.Event | None = None) -> None:
        if self._header_layout_job is not None:
            return
        try:
            self._header_layout_job = self.after_idle(self._run_header_layout)
        except tk.TclError:
            self._header_layout_job = None

    def _run_header_layout(self) -> None:
        self._header_layout_job = None
        self._sync_title_spacing_widgets()
        self._sync_module_spacing_widgets()
        for header in tuple(self._chrome_headers):
            try:
                for label in self._logo_widgets:
                    if label.master is header:
                        self._configure_header_logo(label)
            except tk.TclError:
                continue
        for header in tuple(self._mode_headers):
            try:
                self._layout_mode_header(header)
            except tk.TclError:
                continue

    def _sync_title_spacing_widgets(self) -> None:
        desired = self._title_grid_padding()
        live: list[tk.Misc] = []
        for widget in self._title_spacing_widgets:
            try:
                if widget.winfo_manager() != "grid":
                    live.append(widget)
                    continue
                current = widget.grid_info().get("pady", 0)
                if isinstance(current, (tuple, list)):
                    current_padding = tuple(int(value) for value in current)
                else:
                    current_padding = (int(current), int(current))
                if current_padding != desired:
                    widget.grid_configure(pady=desired)
            except (tk.TclError, TypeError, ValueError):
                continue
            live.append(widget)
        self._title_spacing_widgets = live

    @staticmethod
    def _grid_pady(widget: tk.Misc) -> tuple[int, int]:
        try:
            current = widget.grid_info().get("pady", 0)
        except tk.TclError:
            return (0, 0)
        if isinstance(current, (tuple, list)):
            values = tuple(int(value) for value in current)
            if len(values) == 1:
                return (values[0], values[0])
            return (values[0], values[1])
        value = int(current)
        return (value, value)

    def _register_module_spacing_widget(
        self,
        widget: tk.Misc,
        *,
        before: bool = False,
        after: bool = True,
    ) -> None:
        self._module_spacing_widgets.append((widget, bool(before), bool(after)))
        self._queue_header_layout()

    def _sync_module_spacing_widgets(self) -> None:
        gap = self._module_spacing()
        live: list[tuple[tk.Misc, bool, bool]] = []
        for widget, before, after in self._module_spacing_widgets:
            try:
                if widget.winfo_manager() != "grid":
                    live.append((widget, before, after))
                    continue
                desired = (gap if before else 0, gap if after else 0)
                if self._grid_pady(widget) != desired:
                    widget.grid_configure(pady=desired)
            except (tk.TclError, TypeError, ValueError):
                continue
            live.append((widget, before, after))
        self._module_spacing_widgets = live

    def _register_title_spacing_widget(self, widget: tk.Misc) -> None:
        self._title_spacing_widgets.append(widget)
        self._queue_header_layout()

    @staticmethod
    def _vertical_grid_padding(widget: tk.Misc) -> int:
        try:
            padding = widget.grid_info().get("pady", 0)
        except tk.TclError:
            return 0
        if isinstance(padding, (tuple, list)):
            return sum(int(value) for value in padding)
        return 2 * int(padding)

    def _mode_title_height(self, header: ttk.Frame) -> int:
        row_heights: dict[int, int] = {}
        preset_area = getattr(header, "_preset_area", None)
        for child in header.grid_slaves():
            try:
                if child is preset_area:
                    continue
                info = child.grid_info()
                row = int(info.get("row", 0))
                column = int(info.get("column", 0))
                if column != 0 or row not in (0, 1):
                    continue
                height = int(child.winfo_reqheight()) + self._vertical_grid_padding(child)
            except (tk.TclError, TypeError, ValueError):
                continue
            row_heights[row] = max(row_heights.get(row, 0), height)
        return max(1, sum(row_heights.values()))

    def _layout_mode_header(self, header: ttk.Frame) -> None:
        title_height = self._mode_title_height(header)
        area = getattr(header, "_preset_area", None)
        if area is None:
            header.rowconfigure(0, weight=0, minsize=title_height)
            header.rowconfigure(1, weight=0, minsize=0)
            return

        title_width = max(1, int(getattr(header, "_mode_title_width", 0)))
        frame = getattr(header, "_preset_frame", None)
        action_width = max(1, int(frame.winfo_reqwidth())) if frame is not None else 1
        viewport = VerticalScrollArea._nearest_scroll_area(header)
        available_width = 0
        width_known = False
        try:
            if viewport is not None:
                canvas_width = int(viewport.canvas.winfo_width())
                if canvas_width > 1:
                    horizontal_padding = 0
                    ancestor = header.master
                    while ancestor is not None:
                        try:
                            raw_padding = ancestor.cget("padding")
                            padding = (
                                raw_padding
                                if isinstance(raw_padding, (tuple, list))
                                else ancestor.tk.splitlist(raw_padding)
                            )
                            if len(padding) in (1, 2):
                                horizontal_sides = (padding[0], padding[0])
                            elif len(padding) >= 3:
                                horizontal_sides = (padding[0], padding[2])
                            else:
                                horizontal_sides = ()
                            horizontal_padding += sum(
                                ancestor.winfo_pixels(side)
                                for side in horizontal_sides
                            )
                        except (tk.TclError, TypeError, ValueError):
                            pass
                        if ancestor is viewport.content:
                            break
                        ancestor = getattr(ancestor, "master", None)
                    available_width = max(0, canvas_width - horizontal_padding)
                    width_known = True
        except tk.TclError:
            pass
        if not width_known:
            try:
                available_width = int(header.master.winfo_width())
            except tk.TclError:
                available_width = 0

        side_by_side = available_width >= title_width + action_width + 24
        if side_by_side:
            area.grid_configure(row=0, column=1, columnspan=1, sticky="new", padx=(24, 0))
        else:
            area.grid_configure(row=1, column=0, columnspan=2, sticky="new", padx=0)

        content_height = max(1, int(frame.winfo_reqheight())) if frame is not None else 1
        if int(area.canvas.cget("height")) != content_height:
            area.canvas.configure(height=content_height)
        action_height = content_height
        if area._horizontal_scrollbar_visible:
            action_height += int(area.horizontal_scrollbar.winfo_reqheight())
        header.rowconfigure(0, weight=0, minsize=max(title_height, action_height) if side_by_side else title_height)
        header.rowconfigure(1, weight=0, minsize=0 if side_by_side else action_height)

    def _sync_header_chrome(self) -> None:
        for header in self._chrome_headers:
            try:
                self._sync_header_chrome_widget(header)
            except tk.TclError:
                pass

    def _sync_header_chrome_widget(self, header: ttk.Frame) -> None:
        for label in self._logo_widgets:
            if label.master is header:
                self._configure_header_logo(label)
        self._queue_header_layout()

    def _configure_title_link(self, title: CanvasTitle, title_key: str) -> None:
        url = CHIP_WEBSITE_URLS.get(title_key, "").strip()
        if not url:
            return

        style_name = str(title.cget("style"))
        if style_name == "ChipTitle.TLabel":
            size = self._chip_title_size()
        else:
            size_name = "_piezo_prominent_title_font_size" if style_name == "Prominent.TLabel" else "_piezo_title_font_size"
            size = int(getattr(self.winfo_toplevel(), size_name, 18))
        title.configure(cursor="hand2", font=("Microsoft YaHei UI", size, page_title_weight(self), "underline"))
        title.bind("<Button-1>", lambda _event, link=url: webbrowser.open(link))

    def _set_button_selected(self, key: str, selected: bool) -> None:
        buttons = getattr(self, "_buttons", {})
        button = buttons.get(key) if isinstance(buttons, dict) else None
        if button is not None:
            button.configure(style="Selected.TButton" if selected else "Secondary.TButton")

    def _make_linked_title_part(
        self,
        parent: tk.Misc,
        text: str,
        title_key: str,
        column: int,
        style: str = "Title.TLabel",
    ) -> None:
        title = CanvasTitle(parent, text=text, style=style)
        title.grid(row=0, column=column, sticky="w")
        self._configure_title_link(title, title_key)
        if style == "ChipTitle.TLabel":
            self._chip_title_labels.append(title)

    def _make_header_logo(self, parent: tk.Misc) -> ttk.Label:
        label = ttk.Label(parent, style="Wordmark.TLabel")
        self._logo_widgets.append(label)
        self._configure_header_logo(label)
        return label

    def _chip_title_size(self) -> int:
        root = self.winfo_toplevel()
        size = int(getattr(root, "_piezo_chip_title_font_size", 0))
        if not size:
            size = int(getattr(root, "_piezo_prominent_title_font_size", 19)) + 6
        _warm_title_fonts(self)
        return size

    def _refresh_chip_titles(self) -> None:
        size = self._chip_title_size()
        for title in self._chip_title_labels:
            try:
                current = str(title.cget("font"))
                underlined = "underline" in current
                font: tuple[Any, ...] = ("Microsoft YaHei UI", size, page_title_weight(self), "underline") if underlined else ("Microsoft YaHei UI", size, page_title_weight(self))
                title.configure(font=font)
            except tk.TclError:
                continue
        self._queue_header_layout()

    def _refresh_header_logos(self) -> None:
        self._refresh_chip_titles()
        for label in self._logo_widgets:
            try:
                self._configure_header_logo(label)
            except Exception:
                pass

    def _wordmark_slot(self, header: ttk.Frame, label: ttk.Label) -> int:
        try:
            width = int(header.winfo_width())
        except tk.TclError:
            return 0
        if width <= 1:
            return 0
        title_width = 0
        for child in header.winfo_children():
            if child is label:
                continue
            try:
                title_width = max(title_width, int(child.winfo_reqwidth()))
            except tk.TclError:
                continue
        return max(0, width - title_width - 24)

    def _configure_header_logo(self, label: ttk.Label) -> None:
        root = self.winfo_toplevel()
        chip_size = self._chip_title_size()
        title_height = _font_linespace(root, "Microsoft YaHei UI", page_title_weight(root), chip_size)
        theme = str(getattr(self.ctx.state, "theme", "light"))
        header = label.master
        family, weight = sidebar_brand_font_spec(root)
        preferred = _font_size_for_pixel_height(root, family, weight, title_height)
        available = self._wordmark_slot(header, label) if isinstance(header, tk.Misc) else 0
        size = preferred
        own_row = False
        if available > 0:
            root = self.winfo_toplevel()
            if size > 16 and _wordmark_width(root, family, size, weight) > available:
                lo, hi = 16, size - 1
                size = 16
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if _wordmark_width(root, family, mid, weight) <= available:
                        size = mid
                        lo = mid + 1
                    else:
                        hi = mid - 1
            own_row = _wordmark_width(root, family, size, weight) > available
        text_color = self.ctx.colors.get("text", "#111827")
        key = ("wordmark", title_height, theme, text_color, size, own_row)
        if getattr(label, "_logo_key", None) == key:
            return
        font: tuple[Any, ...] = (family, size) if weight == "normal" else (family, size, weight)
        label.configure(image="", text="PIEZO CONTROL", font=font, foreground=text_color)
        if own_row:
            label.grid(row=2, column=0, columnspan=2, rowspan=1, sticky="e", padx=(16, 0), pady=(4, 0))
        else:
            label.grid(row=0, column=1, columnspan=1, rowspan=2, sticky="e", padx=(16, 0), pady=0)
        setattr(label, "_logo_key", key)

    def _title_after_spacing(self) -> int:
        root = self.winfo_toplevel()
        value = int(getattr(root, "_piezo_title_after_gap", 0))
        if value > 0:
            return value
        body_size = int(getattr(root, "_piezo_body_font_size", 10))
        return max(1, int(round(body_size * 1.7 * 0.3)))

    def _module_spacing(self) -> int:
        root = self.winfo_toplevel()
        value = int(getattr(root, "_piezo_module_gap", 0))
        if value > 0:
            return value
        body_size = int(getattr(root, "_piezo_body_font_size", 10))
        return max(1, int(round(body_size * 1.7 * 0.45)))

    def _title_grid_padding(self) -> tuple[int, int]:
        return (0, self._title_after_spacing())

    def _header_logo_image(self, path: Path, max_h: int) -> tk.PhotoImage:
        root = self.winfo_toplevel()
        cache = getattr(root, "_piezo_logo_cache", None)
        if cache is None:
            cache = {}
            setattr(root, "_piezo_logo_cache", cache)
        key = (str(path), max_h)
        image = cache.get(key)
        if image is None:
            image = tk.PhotoImage(file=str(path), master=root)
            if image.height() > max_h:
                factor = max(1, (image.height() + max_h - 1) // max_h)
                image = image.subsample(factor, factor)
            cache[key] = image
        return image

    def make_serial_log(
        self,
        parent: tk.Misc,
        *,
        height: int = 7,
        width: int | None = None,
        subscribe: bool = True,
    ) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        toolbar = WrappingGroupBar(frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        title_group = toolbar.add_group()
        title = ttk.Label(title_group, style="SectionTitle.TLabel")
        title.grid(row=0, column=0, sticky="w")
        self.bind_text(title, "serial_log")

        clear_group = toolbar.add_group()
        clear_btn = ttk.Button(clear_group, style="Secondary.TButton", command=self.clear_serial_log)
        clear_btn.grid(row=0, column=0, sticky="w")
        self.bind_text(clear_btn, "clear_serial_log")

        export_group = toolbar.add_group()
        export_btn = ttk.Button(export_group, style="Secondary.TButton", command=self.export_serial_log)
        export_btn.grid(row=0, column=0, sticky="w")
        self.bind_text(export_btn, "export_serial_log")

        text_width = 8 if width is None else width
        text = tk.Text(
            frame,
            height=height,
            width=text_width,
            wrap="none",
            relief="flat",
            highlightthickness=1,
            font=("Consolas", int(getattr(self.winfo_toplevel(), "_piezo_mono_font_size", 9))),
        )
        text.grid(row=1, column=0, sticky="nsew")
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        xscroll.grid(row=2, column=0, sticky="ew")
        text.configure(xscrollcommand=xscroll.set, state="disabled")
        self._serial_log_text = text
        self._refresh_serial_log_theme()

        if subscribe:
            self._serial_log_listener = self.append_serial_log
            self.ctx.serial.add_log_listener(self._serial_log_listener)
            self._serial_log_clear_listener = self.clear_serial_log_view
            self.ctx.serial.add_log_clear_listener(self._serial_log_clear_listener)
            for line in self.ctx.serial.log_history[-10_000:]:
                self.append_serial_log(line)

        return frame

    def export_serial_log(self) -> None:
        text = self._serial_log_text
        if text is None:
            return

        history = self.ctx.serial.log_history
        visible_content = text.get("1.0", "end-1c")
        content = build_serial_log_export_content(history, visible_content)

        path = filedialog.asksaveasfilename(
            title=self.tr("export_serial_log"),
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            Path(path).write_text(content, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
    SERIAL_LOG_MAX_LINES = 10_000

    def _serial_log_pending_state(self) -> tuple[threading.Lock, list[Any]]:
        state = self.__dict__.get("_serial_log_pending")
        if state is None:
            state = self.__dict__.setdefault("_serial_log_pending", (threading.Lock(), [[], False, 0]))
        return state[0], state[1]

    def append_serial_log(self, line: str) -> None:
        lock, box = self._serial_log_pending_state()
        if threading.current_thread() is not threading.main_thread():
            # Coalesce: one UI callback inserts every line that arrived before
            # it runs, so a log flood costs one Text insert per tick per page.
            with lock:
                box[0].append(line)
                if len(box[0]) > self.SERIAL_LOG_MAX_LINES:
                    del box[0][: len(box[0]) - self.SERIAL_LOG_MAX_LINES]
                if box[1]:
                    return
                box[1] = True
                generation = box[2]
            try:
                post_to_ui(self, self._flush_serial_log_pending, None, generation)
            except Exception:
                pass
            return
        self._flush_serial_log_pending([line])

    def _flush_serial_log_pending(self, extra: list[str] | None = None, generation: int | None = None) -> None:
        lock, box = self._serial_log_pending_state()
        with lock:
            if generation is not None and generation != box[2]:
                return  # superseded by a clear; the newer batch has its own flush
            lines, box[0], box[1] = box[0], [], False
        if extra:
            lines.extend(extra)
        if not lines:
            return
        text = self._serial_log_text
        if text is None:
            return
        try:
            text.configure(state="normal")
            text.insert("end", "\n".join(lines) + "\n")
            count = int(text.index("end-1c").split(".")[0])
            if count > self.SERIAL_LOG_MAX_LINES:
                text.delete("1.0", f"{count - self.SERIAL_LOG_MAX_LINES}.0")
            text.see("end")
            text.configure(state="disabled")
        except Exception:
            pass

    def clear_serial_log(self) -> None:
        self.ctx.serial.clear_log()
        self.clear_serial_log_view()

    def clear_serial_log_view(self) -> None:
        lock, box = self._serial_log_pending_state()
        with lock:
            # Lines queued before the clear are discarded; lines arriving after
            # it start a new batch that is posted after the clear callback.
            box[0], box[1] = [], False
            box[2] += 1
        if threading.current_thread() is not threading.main_thread():
            try:
                post_to_ui(self, self._clear_serial_log_widget)
            except Exception:
                pass
            return
        self._clear_serial_log_widget()

    def _clear_serial_log_widget(self) -> None:
        text = self._serial_log_text
        if text is None:
            return
        try:
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.configure(state="disabled")
        except Exception:
            pass

    def destroy(self) -> None:
        if self._header_layout_job is not None:
            try:
                self.after_cancel(self._header_layout_job)
            except tk.TclError:
                pass
            self._header_layout_job = None
        if self._serial_log_listener is not None:
            self.ctx.serial.remove_log_listener(self._serial_log_listener)
            self._serial_log_listener = None
        if self._serial_log_clear_listener is not None:
            self.ctx.serial.remove_log_clear_listener(self._serial_log_clear_listener)
            self._serial_log_clear_listener = None
        super().destroy()

    def _refresh_serial_log_theme(self) -> None:
        if self._serial_log_text is None:
            return
        self._serial_log_text.configure(
            bg=self.colors["panel_alt"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            highlightbackground=self.colors["border"],
            font=("Consolas", int(getattr(self.winfo_toplevel(), "_piezo_mono_font_size", 9))),
        )

    def _logo_path_for_theme(self) -> Path | None:
        assets_dir = Path(__file__).resolve().parents[1] / "assets"
        themed_name = "logo_dark.png" if self.ctx.state.theme == "dark" else "logo_light.png"
        for name in (themed_name, "logo.png"):
            path = assets_dir / name
            if path.exists():
                return path
        return None
