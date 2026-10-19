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

class SimpleTooltip:
    def __init__(self, widget: tk.Widget, text_getter: Any) -> None:
        self.widget = widget
        self.text_getter = text_getter
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event: tk.Event) -> None:
        self._hide()
        text = self.text_getter() if callable(self.text_getter) else str(self.text_getter)
        if not text:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + 4
        self.tip = tk.Toplevel(self.widget)
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
        try:
            self.tip.update_idletasks()
            screen_width = self.tip.winfo_screenwidth()
            screen_height = self.tip.winfo_screenheight()
            tip_width = self.tip.winfo_reqwidth()
            tip_height = self.tip.winfo_reqheight()
            x = min(max(8, x), max(8, screen_width - tip_width - 8))
            if y + tip_height + 8 > screen_height:
                y = max(8, self.widget.winfo_rooty() - tip_height - 8)
            y = min(max(8, y), max(8, screen_height - tip_height - 8))
        except tk.TclError:
            pass
        self.tip.wm_geometry(f"+{x}+{y}")
        animator = anim.animator_for(self.widget)
        if animator is not None and animator.enabled:
            tip = self.tip
            animator.animate(("tooltip", id(self)), anim.GRAPHEME_MS,
                             lambda p: tip.attributes("-alpha", p), ease=anim.EASE_GRAPHEME)

    def _hide(self, _event: tk.Event | None = None) -> None:
        animator = anim.animator_for(self.widget)
        if animator is not None:
            animator.cancel(("tooltip", id(self)))
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


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
    ) -> None:
        super().__init__(parent, style="App.TFrame")
        self.ctx = ctx
        self.horizontal = horizontal
        self.min_content_width = max(0, min_content_width)
        self.propagate_width = bool(propagate_width)
        self.propagate_height = bool(propagate_height)
        self.clamp_width = bool(clamp_width)
        self._scrollbar_visible = False
        self._horizontal_scrollbar_visible = False
        self._layout_job: str | None = None

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.horizontal_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(
            yscrollcommand=self.scrollbar.set,
            xscrollcommand=self.horizontal_scrollbar.set,
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
            self._layout_job = self.after(anim.FRAME_MS, self._run_scroll_layout)

    def _run_scroll_layout(self) -> None:
        self._layout_job = None
        self._sync_scroll_region()

    def _cancel_scroll_layout(self, event: tk.Event | None = None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None

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
            height = natural_height + 16
            if self.propagate_height:
                requested_height = max(natural_height, canvas_height - 16)
                if self._window_item_span("height") != requested_height:
                    self.canvas.itemconfigure(self.window_id, height=requested_height)
                height = max(height, requested_height)
                if natural_height + 16 <= canvas_height:
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

    def _on_mousewheel(self, event: tk.Event) -> None:
        try:
            if not self.winfo_ismapped() or self._nearest_scroll_area(self._pointer_widget(event)) is not self:
                return
            delta = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(delta, "units")
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
        self.canvas.configure(bg=self.ctx.colors["bg"])


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


_CHIP_TITLE_KEYS = {"awp1921_1923", "awp1921", "awp1923", "awp1922", "awp1018", "awp1221"}


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
        self._chip_title_labels: list[ttk.Label] = []
        self._logo_images: list[tk.PhotoImage] = []
        self._chrome_headers: list[ttk.Frame] = []
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
        \u5b50\u7c7b\u91cc\u6709 Canvas / Text \u8fd9\u79cd\u975e ttk \u63a7\u4ef6\u65f6\uff0c\u8986\u76d6\u8fd9\u4e2a\u65b9\u6cd5\u66f4\u65b0\u989c\u8272\u3002
        """
        self._refresh_header_logos()
        self._sync_header_chrome()
        self._refresh_serial_log_theme()
        for area in self._vertical_scroll_areas:
            area.refresh_theme()

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
    ) -> tuple[VerticalScrollArea, ttk.Frame]:
        area = VerticalScrollArea(
            parent,
            self.ctx,
            horizontal=horizontal,
            min_content_width=min_content_width,
            propagate_width=propagate_width,
            propagate_height=propagate_height,
            clamp_width=clamp_width,
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
        header = ttk.Frame(parent, style="App.TFrame")
        header.columnconfigure(0, weight=1)

        title_style = "ChipTitle.TLabel" if title_key in _CHIP_TITLE_KEYS else (
            "Prominent.TLabel" if _prominent_title(title_key) else "Title.TLabel"
        )
        if title_key == "awp1921_1923":
            title_group = ttk.Frame(header, style="App.TFrame")
            title_group.grid(row=0, column=0, sticky="w")
            self._make_linked_title_part(title_group, "AWP1921", "awp1921", 0, title_style)
            slash = ttk.Label(title_group, text="/", style=title_style)
            slash.grid(row=0, column=1, sticky="w")
            slash.configure(font=("Microsoft YaHei UI", self._chip_title_size(), page_title_weight(self)))
            self._chip_title_labels.append(slash)
            self._make_linked_title_part(title_group, "AWP1923", "awp1923", 2, title_style)
        else:
            title = ttk.Label(header, style=title_style)
            title.grid(row=0, column=0, sticky="w")
            self.bind_text(title, title_key)
            self._configure_title_link(title, title_key)
            if title_style == "ChipTitle.TLabel":
                self._chip_title_labels.append(title)

        if subtitle:
            sub = ttk.Label(header, style="Subtitle.TLabel")
            sub.grid(row=1, column=0, sticky="w", pady=(3, 0))
            if subtitle.startswith("@"):
                self.bind_text(sub, subtitle[1:])
            else:
                sub.configure(text=subtitle)

        if show_logo:
            logo = self._make_header_logo(header)
            logo.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
            header.bind("<Configure>", lambda _event, label=logo: self._configure_header_logo(label), add="+")
            self._chrome_headers.append(header)
            self._sync_header_chrome_widget(header)

        return header

    def _sync_header_chrome(self) -> None:
        for header in self._chrome_headers:
            try:
                self._sync_header_chrome_widget(header)
            except tk.TclError:
                pass

    def _sync_header_chrome_widget(self, header: ttk.Frame) -> None:
        target = int(getattr(self.winfo_toplevel(), "_piezo_top_chrome_height", 0)) or 42
        bg = self.ctx.colors["bg"]
        sizer = getattr(header, "_chrome_sizer", None)
        if sizer is None:
            sizer = tk.Frame(header, width=1, highlightthickness=0, bd=0, bg=bg)
            sizer.grid(row=0, column=2, rowspan=3)
            header._chrome_sizer = sizer
        try:
            same = int(sizer.cget("height")) == target and str(sizer.cget("bg")) == bg
        except (tk.TclError, ValueError):
            same = False
        if same:
            return
        sizer.configure(height=target, bg=bg)

    def _configure_title_link(self, title: ttk.Label, title_key: str) -> None:
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
        title = ttk.Label(parent, text=text, style=style)
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
        height = int(getattr(self.winfo_toplevel(), "_piezo_top_chrome_height", 0)) or 42
        size = _font_size_for_pixel_height(self.winfo_toplevel(), "Microsoft YaHei UI", "bold", height)
        setattr(self.winfo_toplevel(), "_piezo_chip_title_font_size", size)
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
        sizer = getattr(header, "_chrome_sizer", None)
        for child in header.winfo_children():
            if child is label or child is sizer:
                continue
            try:
                title_width = max(title_width, int(child.winfo_reqwidth()))
            except tk.TclError:
                continue
        return max(0, width - title_width - 24)

    def _configure_header_logo(self, label: ttk.Label) -> None:
        height = int(getattr(self.winfo_toplevel(), "_piezo_top_chrome_height", 0)) or 42
        theme = str(getattr(self.ctx.state, "theme", "light"))
        header = label.master
        family, weight = sidebar_brand_font_spec(self.winfo_toplevel())
        preferred = _font_size_for_pixel_height(self.winfo_toplevel(), family, weight, height)
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
        key = ("wordmark", height, theme, text_color, size, own_row)
        if getattr(label, "_logo_key", None) == key:
            return
        font: tuple[Any, ...] = (family, size) if weight == "normal" else (family, size, weight)
        label.configure(image="", text="PIEZO CONTROL", font=font, foreground=text_color)
        if own_row:
            label.grid(row=2, column=0, columnspan=2, rowspan=1, sticky="e", padx=(16, 0), pady=(4, 0))
        else:
            label.grid(row=0, column=1, columnspan=1, rowspan=2, sticky="e", padx=(16, 0), pady=0)
        setattr(label, "_logo_key", key)

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
