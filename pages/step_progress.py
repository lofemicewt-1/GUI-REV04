"""Opt-in shared step progress and advanced-tools chrome.

Chip pages mount these widgets explicitly.  Merely inheriting ``BasePage`` or
registering module headings never creates visible chrome.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable, Hashable, Literal, Mapping, Sequence, Any

from app.window_layout import is_compact_window
from app.ui_state_store import UiStateStore
from core.ui_dispatch import post_to_ui


StepState = Literal["idle", "running", "done", "error"]


@dataclass(frozen=True)
class StepSpec:
    key: str
    ordinal: int
    completion_key: str
    part_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("step key must not be empty")
        if int(self.ordinal) < 1:
            raise ValueError("step ordinal must be positive")
        if len(set(self.part_keys)) != len(self.part_keys):
            raise ValueError("part keys must be unique")


@dataclass(frozen=True)
class ProgressContext:
    port_session: tuple | None
    chip_addr: int
    mode: str


@dataclass(frozen=True)
class EventToken:
    context: ProgressContext
    activation_epoch: int
    step_key: str
    param_version: int
    operation_id: int
    part_fingerprints: tuple[tuple[str, Hashable], ...] = ()

    def fingerprint_map(self) -> dict[str, Hashable]:
        return dict(self.part_fingerprints)


_EXCLUDED_ROLES = {
    "reference", "verify", "feedback", "advanced", "troubleshoot", "shortcut",
}
_STATE_COLORS = {
    "idle": "#6B7280",
    "running": "#D99A00",
    "done": "#2563EB",
    "error": "#DC2626",
}


def _ui_scale(widget: Any) -> float:
    try:
        state = getattr(getattr(widget, "ctx", None), "state", None)
        return max(0.5, float(getattr(state, "ui_scale", 1.0) or 1.0))
    except (TypeError, ValueError):
        return 1.0


def _slim_px(value: float, scale: float) -> int:
    return max(1, int(round(value * scale)))


def _step_font(widget: Any, *, bold: bool = False) -> tkfont.Font:
    families = tkfont.families(widget)
    family = ("Microsoft YaHei UI" if "Microsoft YaHei UI" in families else
              tkfont.nametofont("TkDefaultFont", root=widget).actual("family"))
    return tkfont.Font(root=widget, family=family, size=-round(18 * _ui_scale(widget)),
                       weight="bold" if bold else "normal")


def _rounded(canvas: tk.Canvas, box: tuple[float, float, float, float], radius: float, **kw: Any) -> int:
    """One rounded Canvas item, so stable layouts can update it in place."""
    x1, y1, x2, y2 = box
    r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    return canvas.create_polygon(
        x1+r, y1, x1+r, y1, x2-r, y1, x2-r, y1, x2, y1, x2, y1+r,
        x2, y1+r, x2, y2-r, x2, y2-r, x2, y2, x2-r, y2, x2-r, y2,
        x1+r, y2, x1+r, y2, x1, y2, x1, y2-r, x1, y2-r,
        x1, y1+r, x1, y1+r, x1, y1, smooth=True, splinesteps=24, **kw,
    )


TRAPEZOID_BASE_HEIGHT = 24
# Compact strip: hug step text, never stretch edge-to-edge.
STRIP_MAX_WIDTH = 1100
STRIP_MAX_FRACTION = 0.7
STRIP_NARROW_FRACTION = 0.92
ADV_BUTTON_MAX_WIDTH = 330
ADV_BUTTON_PAD = 45


class Trapezoid:
    """Floating, persistent advanced-tools panel owned by a chip host."""

    def __init__(
        self,
        host: Any,
        canvas: tk.Misc,
        floating_group: ttk.Frame,
        *,
        build_state_actions: Callable[[ttk.Frame], Any],
        build_register_bar: Callable[[Any], Any],
        state_key: str,
        build_extra: Callable[[ttk.Frame], Any] | None = None,
        store: UiStateStore | None = None,
    ) -> None:
        from pages.base_page import SimpleTooltip, WrappingGroupBar

        self.host = host
        self.canvas = canvas
        self.floating_group = floating_group
        self.state_key = state_key
        self.store = store or UiStateStore()
        self._layout_job: str | None = None
        self._viewport_bind_id: str | None = None
        self._forced_collapsed = False
        self._responsive_collapsed = False
        self._responsive_override: bool | None = None
        self._last_layout_signature: tuple[Any, ...] | None = None
        self._desired_expanded = self.store.get_trapezoid_expanded(state_key, default=False)
        self._owning_area = self._nearest_scroll_area(canvas)
        self._old_scroll_extra: int | None = None
        self._reserved_clearance: int | None = None
        self._old_bottom_inset = getattr(self._owning_area, "bottom_inset", 0)
        self._hover = False
        self._pressed = False
        self._button_font = _step_font(host)
        self._button_label_font = _step_font(host)

        self.floating_group.columnconfigure(0, weight=1)
        self.floating_group.rowconfigure(0, weight=1)
        self.progress_slot = ttk.Frame(floating_group, style="App.TFrame")
        self.progress_slot.grid(row=0, column=0, sticky="ew")
        self.progress_slot.columnconfigure(0, weight=1)

        self.button = tk.Canvas(
            floating_group,
            width=450,
            height=_slim_px(TRAPEZOID_BASE_HEIGHT, _ui_scale(host)),
            highlightthickness=0,
            borderwidth=0,
            takefocus=True,
            cursor="hand2",
        )
        self.button.grid(row=1, column=0, sticky="n", pady=(0, 0))
        self.button.bind("<Configure>", self._draw_button, add="+")
        self.button.bind("<ButtonPress-1>", self._press_button, add="+")
        self.button.bind("<ButtonRelease-1>", self._release_button, add="+")
        self.button.bind("<Return>", self._toggle_from_event, add="+")
        self.button.bind("<space>", self._toggle_from_event, add="+")
        self.button.bind("<FocusIn>", lambda _event: self._draw_button(), add="+")
        self.button.bind("<FocusOut>", lambda _event: self._draw_button(), add="+")
        self._tooltip = SimpleTooltip(self.button, self._accessibility_text)
        self.button.bind("<Enter>", lambda _e: self._button_hover(True), add="+")
        self.button.bind("<Leave>", lambda _e: self._button_hover(False), add="+")

        # Controls are constructed once, even while collapsed.  The inner
        # frame is a canvas window so unusually tall advanced panels remain
        # reachable when the viewport constrains their height.
        self._panel_outer = ttk.Frame(floating_group, style="Panel.TFrame", padding=4)
        self._panel_outer.grid_propagate(False)
        self._panel_outer.columnconfigure(0, weight=1)
        self._panel_outer.rowconfigure(0, weight=1)
        self._panel_canvas = tk.Canvas(self._panel_outer, highlightthickness=0, borderwidth=0)
        self._panel_canvas.grid(row=0, column=0, sticky="nsew")
        self._panel_scrollbar = ttk.Scrollbar(
            self._panel_outer, orient="vertical", command=self._panel_canvas.yview,
        )
        self._panel_canvas.configure(yscrollcommand=self._panel_scrollbar.set)
        self.panel = ttk.Frame(self._panel_canvas, style="Panel.TFrame", padding=2)
        self.panel.columnconfigure(0, weight=1)
        self._panel_window = self._panel_canvas.create_window(
            (0, 0), window=self.panel, anchor="nw",
        )
        self.panel.bind("<Configure>", self._queue_panel_layout, add="+")
        self._panel_canvas.bind("<Configure>", self._queue_panel_layout, add="+")

        actions = ttk.Frame(self.panel, style="Panel.TFrame")
        actions.grid(row=0, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        self.actions = actions
        self.registers = WrappingGroupBar(self.panel)
        self.registers.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        build_state_actions(actions)
        build_register_bar(self.registers)
        if build_extra is not None:
            build_extra(self.panel)

        self._on_viewport_configure()
        self._apply_expanded_state()
        self._reserve_scroll_clearance()

    @staticmethod
    def _nearest_scroll_area(widget: tk.Misc) -> Any | None:
        from pages.base_page import VerticalScrollArea

        current: Any = widget
        while current is not None:
            if isinstance(current, VerticalScrollArea):
                return current
            current = getattr(current, "master", None)
        return None

    @property
    def expanded(self) -> bool:
        return bool(getattr(self, "_expanded", False))

    def _accessibility_text(self) -> str:
        return self.host.tr("chip_tools_collapse" if self.expanded else "chip_tools_expand")

    def _draw_button(self, event: tk.Event | None = None) -> None:
        try:
            is_configure = event is not None and getattr(event, "type", None) == tk.EventType.Configure
            width = max(1, int(event.width) if is_configure else int(self.button.winfo_width()))
            height = max(1, int(event.height) if is_configure else int(self.button.winfo_height()))
            colors = getattr(self.host, "colors", {})
            self.button.configure(bg=colors.get("bg", "#F7F8FA"))
            self.button.delete("all")
            fill, outline, text = "#F1F5F9", "#CBD5E1", "#475569"
            if self.expanded:
                fill, outline, text = "#DBEAFE", "#93B4F5", "#1D4ED8"
            if self._hover:
                fill = "#E2E8F0"
            if self._pressed:
                fill = "#CBD5E1"
            if self.button.focus_get() is self.button:
                outline = "#93C5FD"
            scale = _ui_scale(self.host)
            stroke = 1.5 * scale
            inset = stroke / 2
            _rounded(self.button, (inset, inset, width - inset, height - inset), height / 2,
                     fill=fill, outline=outline, width=stroke)
            label = self.host.tr("chip_tools_label")
            icon_space = _slim_px(18, scale)
            # Keep the full translated label even when the width cap applies.
            size = round(18 * scale)
            self._button_label_font.configure(size=-size)
            room = max(1, width - _slim_px(ADV_BUTTON_PAD, scale))
            while size > 1 and self._button_label_font.measure(label) > room:
                size -= 1
                self._button_label_font.configure(size=-size)
            label_width = self._button_label_font.measure(label)
            x = (width - label_width - icon_space) / 2
            self.button.create_text(x, height / 2, anchor="w", text=label,
                                    fill=text, font=self._button_label_font, tags="label")
            cx, cy = x + label_width + _slim_px(12, scale), height / 2
            dy = (-1 if self.expanded else 1) * 2.25 * scale
            self.button.create_line(cx-4.5*scale, cy-dy, cx, cy+dy, cx+4.5*scale, cy-dy,
                                    fill=text, width=stroke)
        except tk.TclError:
            pass

    def _button_hover(self, value: bool) -> None:
        self._hover = value
        if not value:
            self._pressed = False
        self._draw_button()

    def _press_button(self, _event: tk.Event) -> None:
        self._pressed = True
        self.button.focus_set()
        self._draw_button()

    def _release_button(self, event: tk.Event) -> None:
        pressed = self._pressed
        self._pressed = False
        if pressed and 0 <= event.x < self.button.winfo_width() and 0 <= event.y < self.button.winfo_height():
            self._toggle_from_event()
        self._draw_button()

    def _toggle_from_event(self, _event: tk.Event | None = None) -> str:
        self.set_expanded(not self.expanded)
        return "break"

    def _on_viewport_configure(self, _event: tk.Event | None = None) -> None:
        try:
            width, height = int(self.canvas.winfo_width()), int(self.canvas.winfo_height())
            root = self.host.winfo_toplevel()
            root_width, root_height = int(root.winfo_width()), int(root.winfo_height())
        except (tk.TclError, ValueError):
            return
        if width <= 1 or height <= 1:
            return
        responsive_changed = False
        if root_width > 1 and root_height > 1:
            responsive = is_compact_window(root_width, root_height)
            if responsive != self._responsive_collapsed:
                self._responsive_collapsed = responsive
                self._responsive_override = None
                responsive_changed = True
        group_height = max(1, int(self.floating_group.winfo_reqheight()))
        forced = width < 560 or height < max(300, group_height + 110)
        forced_changed = forced != self._forced_collapsed
        if forced_changed:
            self._forced_collapsed = forced
            self._apply_expanded_state()
        elif responsive_changed:
            self._apply_expanded_state()
        self._queue_layout()

    def _reserve_scroll_clearance(self, *, extra_height: int = 0) -> None:
        area = self._owning_area
        if area is None:
            return
        if self._old_scroll_extra is None:
            self._old_scroll_extra = int(area.scroll_region_extra)
        try:
            measured = max(int(self.floating_group.winfo_reqheight()), int(self.floating_group.winfo_height()))
        except tk.TclError:
            measured = 1
        scale = _ui_scale(self.host)
        band = max(_slim_px(63, scale), measured + _slim_px(6, scale))
        area.set_bottom_inset(max(self._old_bottom_inset, band))
        clearance = max(_slim_px(63, scale), measured + int(extra_height) + _slim_px(24, scale))
        self._reserved_clearance = max(self._old_scroll_extra, clearance)
        area.scroll_region_extra = self._reserved_clearance
        area._queue_scroll_layout()

    def set_expanded(self, value: bool, *, persist: bool = True) -> None:
        value = bool(value)
        if persist:
            self._desired_expanded = value
            self.store.set_trapezoid_expanded(self.state_key, value)
        if self._responsive_collapsed and not self._forced_collapsed:
            # Small-window auto-collapse is a temporary presentation default.
            # Explicit user input can still expand it during this compact interval.
            self._responsive_override = value
        self._apply_expanded_state(requested=value if not persist else None)

    def _apply_expanded_state(self, requested: bool | None = None) -> None:
        desired = self._desired_expanded if requested is None else bool(requested)
        if self._responsive_collapsed:
            desired = self._responsive_override if self._responsive_override is not None else False
        actual = desired and not self._forced_collapsed
        self._expanded = actual
        self._last_layout_signature = None
        try:
            self._draw_button()
            if actual:
                self._panel_outer.place(in_=self.floating_group, relx=0.5, anchor="s")
                self._queue_layout()
            else:
                self._panel_outer.place_forget()
        except tk.TclError:
            pass

    def _queue_layout(self, _event: tk.Event | None = None) -> None:
        if self._layout_job is None:
            try:
                self._layout_job = self.host.after_idle(self._layout)
            except tk.TclError:
                self._layout_job = None

    def _layout(self) -> None:
        self._layout_job = None
        try:
            width = max(1, int(self.canvas.winfo_width()))
            height = max(1, int(self.canvas.winfo_height()))
            # Pinned in the owning area's bottom band, outside its viewport.
            natural_width = max(1, int(self.floating_group.winfo_reqwidth()))
            group_height = max(1, int(self.floating_group.winfo_reqheight()))
            panel_width_request = max(1, int(self.panel.winfo_reqwidth()))
            panel_height_request = max(1, int(self.panel.winfo_reqheight()))
            state = getattr(getattr(self.host, "ctx", None), "state", None)
            bars = [bar for bar in getattr(self.host, "_step_progress_bars", ())
                    if bar is getattr(getattr(self.host, "_active_step_page", None), "step_progress", None)]
            content_hint = max((bar.preferred_width() for bar in bars), default=0)
            signature = (
                width, height, natural_width, group_height, content_hint,
                panel_width_request, panel_height_request, bool(self.expanded),
                float(getattr(state, "ui_scale", 1.0) or 1.0),
                self.host.tr("chip_tools_label"), tuple(id(bar) for bar in bars),
            )
            if signature == self._last_layout_signature:
                return
            self._last_layout_signature = signature
            scale = float(getattr(state, "ui_scale", 1.0) or 1.0)
            self._button_font.configure(size=-round(18 * scale))
            label_w = self._button_font.measure(self.host.tr("chip_tools_label"))
            adv_need = min(_slim_px(ADV_BUTTON_MAX_WIDTH, scale), label_w + _slim_px(ADV_BUTTON_PAD, scale))
            fraction = (STRIP_NARROW_FRACTION if width < STRIP_MAX_WIDTH * scale
                        else STRIP_MAX_FRACTION)
            cap = max(1, min(round(width * fraction),
                             _slim_px(STRIP_MAX_WIDTH, scale), width - _slim_px(24, scale)))
            group_width = min(max(content_hint, adv_need), cap)
            self.floating_group.place(relx=0.5, rely=1.0, anchor="s", y=-_slim_px(6, scale), width=group_width)
            self.button.configure(width=min(adv_need, group_width),
                                  height=_slim_px(TRAPEZOID_BASE_HEIGHT, scale))
            # The strip Canvas already includes its three shadow pixels.
            gap = _slim_px(6, scale) - _slim_px(3, scale) if bars else 0
            self.button.grid_configure(pady=(gap, 0))
            for bar in bars:
                bar.apply_width(group_width)
            self._reserve_scroll_clearance()
            if self.expanded:
                panel_width = min(max(280, int(self.panel.winfo_reqwidth()) + 20), max(240, width - 24))
                max_panel_height = max(80, height - group_height - 24)
                panel_height = min(max_panel_height, max(100, int(self.panel.winfo_reqheight()) + 18))
                # The panel opens from the top edge of the combined floating
                # chrome, so it stays above the progress cells while leaving
                # the advanced pill attached below it.
                panel_y = -6
                self._panel_outer.place(
                    in_=self.floating_group,
                    relx=0.5,
                    y=panel_y,
                    anchor="s",
                    width=panel_width,
                    height=panel_height,
                )
                if int(self.panel.winfo_reqheight()) + 18 > panel_height:
                    self._panel_scrollbar.grid(row=0, column=1, sticky="ns")
                    content_width = max(1, panel_width - 42)
                else:
                    self._panel_scrollbar.grid_remove()
                    content_width = max(1, panel_width - 12)
                self._panel_outer.columnconfigure(1, weight=0)
                self._panel_canvas.configure(bg=getattr(self.host, "colors", {}).get("panel", "#FFFFFF"))
                self._panel_canvas.itemconfigure(self._panel_window, width=content_width)
                self._panel_canvas.configure(scrollregion=self._panel_canvas.bbox("all"))
                self.registers.reflow(max(1, panel_width - 32))
            else:
                self._panel_outer.place_forget()
        except tk.TclError:
            pass

    def _queue_panel_layout(self, _event: tk.Event | None = None) -> None:
        if self._layout_job is None:
            try:
                self._layout_job = self.host.after_idle(self._layout)
            except tk.TclError:
                self._layout_job = None

    def refresh_language(self) -> None:
        self._draw_button()
        self._last_layout_signature = None
        self._queue_layout()

    def refresh_theme(self) -> None:
        self._button_font.configure(size=-round(18 * _ui_scale(self.host)))
        self._draw_button()
        self._last_layout_signature = None
        self._queue_layout()

    def refresh_layout(self) -> None:
        self._last_layout_signature = None
        self._draw_button()
        self._on_viewport_configure()
        self._queue_layout()

    def destroy(self) -> None:
        if self._layout_job is not None:
            try:
                self.host.after_cancel(self._layout_job)
            except tk.TclError:
                pass
            self._layout_job = None
        area = self._owning_area
        if area is not None and self._old_scroll_extra is not None:
            if self._reserved_clearance is None or int(area.scroll_region_extra) == self._reserved_clearance:
                area.scroll_region_extra = self._old_scroll_extra
                area._queue_scroll_layout()
        if area is not None:
            area.set_bottom_inset(self._old_bottom_inset)
        if self._viewport_bind_id is not None:
            try:
                self.canvas.unbind("<Configure>", self._viewport_bind_id)
            except tk.TclError:
                pass
            self._viewport_bind_id = None
        try:
            self.floating_group.destroy()
        except tk.TclError:
            pass


@dataclass
class _StepData:
    spec: StepSpec
    state: StepState = "idle"
    detail: str = ""
    note_key: str | None = None
    fingerprints: dict[str, Hashable] = field(default_factory=dict)
    receipts: dict[str, Hashable] = field(default_factory=dict)
    param_version: int = 0
    operation_id: int = 0


class StepProgressBar(ttk.Frame):
    """UI-thread-owned progress state machine with immutable worker tokens."""

    def __init__(
        self,
        page: Any,
        host: Any,
        parent: ttk.Frame,
        steps: Sequence[StepSpec],
        context: Callable[[], ProgressContext],
    ) -> None:
        from pages.base_page import SimpleTooltip

        super().__init__(parent, style="App.TFrame")
        self.page = page
        self.host = host
        self.context = context
        self.steps = tuple(steps)
        if not self.steps:
            raise ValueError("at least one step is required")
        if len({step.key for step in self.steps}) != len(self.steps):
            raise ValueError("step keys must be unique")
        if len({step.ordinal for step in self.steps}) != len(self.steps):
            raise ValueError("step ordinals must be unique")
        if tuple(step.ordinal for step in self.steps) != tuple(sorted(step.ordinal for step in self.steps)):
            raise ValueError("steps must be ordered by ordinal")

        self._data = {step.key: _StepData(step) for step in self.steps}
        self._index = {step.key: index for index, step in enumerate(self.steps)}
        self._epoch = 0
        self._active = False
        self._poll_job: str | None = None
        self._last_context: ProgressContext | None = None
        self._issued_tokens: dict[str, EventToken] = {}
        self._status_unsubscribe = None
        self._collapsed = False
        self._metadata = self._module_targets(page)
        self._titles: dict[str, str] = {}
        self._display_titles: dict[str, str] = {}
        self._hit_rects: dict[str, tuple[float, float, float, float]] = {}
        self._items: dict[str, dict[str, int]] = {}
        self._strip_width: int | None = None
        self._drawing_signature: tuple[Any, ...] | None = None
        self._hover_key: str | None = None
        self._pressed_key: str | None = None
        self._focus_key = self.steps[0].key
        self.active_step_key: str | None = None  # Most recently navigated step.
        self._font = _step_font(page)
        self._bold_font = _step_font(page, bold=True)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                takefocus=True, cursor="hand2")
        self.canvas.grid(row=0, column=0)
        self._tooltip = SimpleTooltip(self.canvas, self._hit_tooltip)
        self.canvas.bind("<Motion>", self._motion, add="+")
        self.canvas.bind("<Leave>", self._leave, add="+")
        self.canvas.bind("<ButtonPress-1>", self._press, add="+")
        self.canvas.bind("<ButtonRelease-1>", self._release, add="+")
        self.canvas.bind("<FocusIn>", self._focus, add="+")
        self.canvas.bind("<FocusOut>", self._blur, add="+")
        for sequence in ("<Left>", "<Right>", "<Home>", "<End>", "<Return>", "<space>"):
            self.canvas.bind(sequence, self._key, add="+")
        self._render()
        bus = getattr(getattr(page, "ctx", None), "status_bus", None)
        if bus is not None:
            self._status_unsubscribe = bus.subscribe(self._on_status_update)
        self.bind("<Destroy>", self._on_destroy, add="+")

    @staticmethod
    def _module_targets(page: Any) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for item in getattr(page, "_module_step_metadata", ()):
            role = item.get("role")
            if role in _EXCLUDED_ROLES:
                continue
            ordinal = item.get("ordinal")
            if ordinal is None:
                continue
            # Only ordinary primary headings create cells, and the first one
            # registered for an ordinal owns its navigation target.
            if ordinal not in result:
                result[int(ordinal)] = item
        return result

    def _current_context(self) -> ProgressContext | None:
        try:
            value = self.context()
        except Exception:
            return None
        if not isinstance(value, ProgressContext):
            return None
        return value

    def _recheck_context(self) -> ProgressContext | None:
        current = self._current_context()
        if current is None or current.port_session is None:
            if self._last_context is not None:
                bus = getattr(getattr(self.page, "ctx", None), "status_bus", None)
                if bus is not None:
                    bus.invalidate_context(self._last_context)
                self._clear_all_for_context()
            self._last_context = current
            return current
        if self._last_context is not None and current != self._last_context:
            bus = getattr(getattr(self.page, "ctx", None), "status_bus", None)
            if bus is not None:
                bus.invalidate_context(self._last_context)
            self._clear_all_for_context()
        self._last_context = current
        return current

    def _clear_all_for_context(self) -> None:
        self._epoch += 1
        self._invalidate_issued_tokens()
        for data in self._data.values():
            data.param_version += 1
            data.operation_id += 1
            data.state = "idle"
            data.detail = ""
            data.note_key = None
            data.receipts.clear()
        self._render()

    def update_params(self, step_key: str, fingerprints: Mapping[str, Hashable]) -> None:
        data = self._data[step_key]
        new = dict(fingerprints)
        if data.fingerprints == new:
            return
        changed = {key for key in set(data.fingerprints) | set(new) if data.fingerprints.get(key) != new.get(key)}
        index = self._index[step_key]
        old_receipts = dict(data.receipts)
        keep_parts = set(data.spec.part_keys) & old_receipts.keys()
        can_retain = bool(data.spec.part_keys) and changed.issubset(set(data.spec.part_keys))

        for key in self.steps[index:]:
            current = self._data[key.key]
            self._invalidate_step_token(key.key)
            current.param_version += 1
            current.operation_id += 1
            current.detail = ""
            current.note_key = "step_changed"
            current.state = "idle"
            current.receipts.clear()
        data.fingerprints = new
        if can_retain:
            data.receipts.update({part: old_receipts[part] for part in keep_parts if old_receipts[part] == new.get(part)})
            if len(data.receipts) == len(data.spec.part_keys):
                data.state = "done"
        self._render()

    def begin(self, step_key: str) -> EventToken:
        data = self._data[step_key]
        current = self._recheck_context()
        if current is None:
            current = ProgressContext(None, 0, "")
        data.operation_id += 1
        self._invalidate_step_token(step_key)
        data.state = "running"
        data.detail = ""
        data.note_key = None
        self._render()
        token = EventToken(
            context=current,
            activation_epoch=self._epoch,
            step_key=step_key,
            param_version=data.param_version,
            operation_id=data.operation_id,
            part_fingerprints=tuple(sorted(data.fingerprints.items(), key=lambda item: item[0])),
        )
        self._issued_tokens[step_key] = token
        return token

    def mark(self, step_key: str, state: StepState, token: EventToken, *, detail: str = "") -> None:
        post_to_ui(self, self._deliver_mark, step_key, state, token, str(detail))

    def mark_part(self, step_key: str, part_key: str, state: StepState, token: EventToken) -> None:
        post_to_ui(self, self._deliver_part, step_key, part_key, state, token)

    def note(self, step_key: str, text_key: str, token: EventToken) -> None:
        post_to_ui(self, self._deliver_note, step_key, text_key, token)

    def _token_is_current(self, step_key: str, token: EventToken) -> bool:
        current = self._recheck_context()
        if not self._active or current is None or current.port_session is None:
            return False
        try:
            if not int(self.winfo_exists()) or not int(self.page.winfo_exists()):
                return False
        except (tk.TclError, AttributeError):
            return False
        data = self._data.get(step_key)
        return bool(
            data is not None
            and token.step_key == step_key
            and token.context == current
            and token.activation_epoch == self._epoch
            and token.param_version == data.param_version
            and token.operation_id == data.operation_id
        )

    def _deliver_mark(self, step_key: str, state: StepState, token: EventToken, detail: str) -> None:
        if state not in {"idle", "running", "done", "error"} or not self._token_is_current(step_key, token):
            return
        data = self._data[step_key]
        if state == "done" and data.spec.part_keys and not all(part in data.receipts for part in data.spec.part_keys):
            return
        data.state = state
        data.detail = detail
        self._render()

    def _deliver_part(self, step_key: str, part_key: str, state: StepState, token: EventToken) -> None:
        if not self._token_is_current(step_key, token):
            return
        data = self._data[step_key]
        if part_key not in data.spec.part_keys or state not in {"idle", "running", "done", "error"}:
            return
        captured = token.fingerprint_map()
        if state == "done":
            if part_key not in captured or captured[part_key] != data.fingerprints.get(part_key):
                return
            data.receipts[part_key] = captured[part_key]
            data.state = "done" if all(part in data.receipts for part in data.spec.part_keys) else "running"
            data.detail = ""
        elif state == "error":
            data.receipts.pop(part_key, None)
            data.state = "error"
        elif state == "running":
            data.state = "running"
        else:
            data.state = "done" if all(part in data.receipts for part in data.spec.part_keys) else "idle"
        self._render()

    def _deliver_note(self, step_key: str, text_key: str, token: EventToken) -> None:
        if not self._token_is_current(step_key, token):
            return
        self._data[step_key].note_key = str(text_key)
        self._render()

    def _on_status_update(self, update: Any) -> None:
        token = getattr(update, "token", None)
        progress_token = getattr(token, "progress_token", None)
        if not isinstance(progress_token, EventToken):
            return
        step_key = progress_token.step_key
        state = getattr(update, "state", "")
        if state == "running":
            self._deliver_mark(step_key, "running", progress_token, "")
        elif state == "receipt":
            part_state = getattr(update, "part_state", None)
            mapped = {"done": "done", "error": "error", "running": "running", "idle": "idle"}.get(part_state)
            if mapped is not None and getattr(update, "part_key", None):
                self._deliver_part(step_key, update.part_key, mapped, progress_token)
        elif state == "success":
            self._deliver_mark(step_key, "done", progress_token, "")
        elif state == "failure":
            self._deliver_mark(step_key, "error", progress_token, str(getattr(update, "error", "")))

    def _invalidate_step_token(self, step_key: str) -> None:
        token = self._issued_tokens.pop(step_key, None)
        bus = getattr(getattr(self.page, "ctx", None), "status_bus", None)
        if token is not None and bus is not None:
            bus.invalidate_progress(token)

    def _invalidate_issued_tokens(self) -> None:
        for key in tuple(self._issued_tokens):
            self._invalidate_step_token(key)

    def invalidate_from(self, step_key: str) -> None:
        index = self._index[step_key]
        for step in self.steps[index:]:
            data = self._data[step.key]
            self._invalidate_step_token(step.key)
            data.param_version += 1
            data.operation_id += 1
            data.state = "idle"
            data.detail = ""
            data.note_key = "step_changed"
            data.receipts.clear()
        self._render()

    def reset_all(self) -> None:
        self._epoch += 1
        self._invalidate_issued_tokens()
        for data in self._data.values():
            data.param_version += 1
            data.operation_id += 1
            data.state = "idle"
            data.detail = ""
            data.note_key = None
            data.receipts.clear()
        self._last_context = self._current_context()
        self._render()

    def activate(self) -> None:
        current = self._current_context()
        if self._last_context is not None and current != self._last_context:
            self._clear_all_for_context()
        self._last_context = current
        self._active = True
        self._schedule_context_check()

    def deactivate(self) -> None:
        self._active = False
        self._epoch += 1
        self._invalidate_issued_tokens()
        for data in self._data.values():
            data.operation_id += 1
            if data.state == "running":
                data.state = "done" if data.spec.part_keys and all(p in data.receipts for p in data.spec.part_keys) else "idle"
        self._cancel_context_check()
        self._render()

    def _schedule_context_check(self) -> None:
        if not self._active or self._poll_job is not None:
            return
        try:
            self._poll_job = self.after(500, self._poll_context)
        except tk.TclError:
            self._poll_job = None

    def _poll_context(self) -> None:
        self._poll_job = None
        if self._active:
            self._recheck_context()
            self._schedule_context_check()

    def _cancel_context_check(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None

    def accepts(self, token: EventToken) -> bool:
        return self._token_is_current(token.step_key, token)

    def snapshot(self) -> Mapping[str, Mapping[str, Any]]:
        result = {}
        for key, data in self._data.items():
            result[key] = {
                "state": data.state,
                "detail": data.detail,
                "note_key": data.note_key,
                "fingerprints": dict(data.fingerprints),
                "parts": dict(data.receipts),
                "param_version": data.param_version,
                "operation_id": data.operation_id,
            }
        return MappingProxyType(result)

    def _count_args(self) -> dict[str, int]:
        return {
            "done": sum(data.state == "done" for data in self._data.values()),
            "total": len(self.steps),
        }

    def tooltip_text(self, step_key: str) -> str:
        data = self._data[step_key]
        self._metadata = self._module_targets(self.page)
        metadata = self._metadata.get(data.spec.ordinal, {})
        pieces: list[str] = []
        help_key = metadata.get("help_key")
        if help_key:
            pieces.append(self.page.tr(help_key))
        condition = self.page.tr(data.spec.completion_key)
        pieces.append(self.page.tr("step_completion_condition", condition=condition))
        pieces.append(self.page.tr(f"step_state_{data.state}"))
        if data.note_key:
            pieces.append(self.page.tr(data.note_key))
        if data.detail:
            pieces.append(data.detail)
        return "\n\n".join(piece for piece in pieces if piece)

    def _render(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self._metadata = self._module_targets(self.page)
        for key, data in self._data.items():
            title_key = self._metadata.get(data.spec.ordinal, {}).get("title_key")
            title = self.page.tr(title_key) if title_key else key
            self._titles[key] = f"{data.spec.ordinal} · {title}"
        self._draw_strip()
        self.host.trapezoid._reserve_scroll_clearance()
        self.host.trapezoid._on_viewport_configure()
        self.host.trapezoid._queue_layout()

    def _measure(self, text: str) -> int:
        return max(self._font.measure(text), self._bold_font.measure(text))

    def _geometry(self) -> tuple[int, int, int, int, int, int]:
        scale = _ui_scale(self.page)
        px = lambda value: _slim_px(value, scale)
        return (px(10.5), 2*px(6) + px(15) + px(6), 2*px(4.5) + px(9), px(42), px(9), px(21))

    def preferred_width(self) -> int:
        pad, cell_fixed, connector, badge, gap, collapse = self._geometry()
        if self._collapsed:
            return 2 * pad + badge
        return (2 * pad + sum(cell_fixed + self._measure(self._titles.get(step.key, step.key))
                              for step in self.steps)
                + (len(self.steps) - 1) * connector + gap + badge + collapse)

    def apply_width(self, width: int) -> None:
        """Fit cells to content, shortening the longest titles first."""
        self._strip_width = max(1, int(width))
        self._draw_strip()

    def _elide(self, full: str, room: int, *, compact: bool = False) -> str:
        if self._measure(full) <= room:
            return full
        if self._measure("…") > room:
            return ""
        lo, hi = 0, len(full)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._measure(full[:mid] + "…") <= room:
                lo = mid
            else:
                hi = mid - 1
        if compact:
            ordinal, separator, title = full.partition(" · ")
            if title:
                # Spend the ellipsis pixels on a recognizable title prefix.
                prefix = (title[:4] if "\u3400" <= title[0] <= "\u9fff"
                          else title.split()[0][:6])
                for length in range(len(prefix), min(3, len(prefix)) - 1, -1):
                    short = ordinal + separator + prefix[:length]
                    if len(short) > lo and self._measure(short) <= room:
                        return short
        return full[:lo] + "…"

    def _draw_strip(self) -> None:
        scale = _ui_scale(self.page)
        px = lambda value: _slim_px(value, scale)
        # Enlarge the original rounded 18/20/22 px tiers to 27/30/33 px.
        # Grow further only when glyphs would clip.
        line = max(self._font.metrics("linespace"), self._bold_font.metrics("linespace"))
        designed = round(1.5 * px(18))
        height = designed if line <= designed else line + px(6)
        width = self._strip_width or self.preferred_width()
        compact = self.host.trapezoid.canvas.winfo_width() < STRIP_MAX_WIDTH * scale
        colors = getattr(self.page, "colors", {}) or {}
        background = colors.get("bg", "#FFFFFF")
        dark = getattr(getattr(self.page.ctx, "state", None), "theme", "light") == "dark"
        signature = (width, height, scale, compact, self._collapsed, tuple(self._titles.items()),
                     str(self._font.actual()), background, dark)
        if signature == self._drawing_signature:
            self._paint()
            return
        self._drawing_signature = signature
        c = self.canvas
        c.configure(width=width, height=height + px(3), bg=background)
        c.delete("all")
        self._items.clear()
        self._hit_rects.clear()
        self._display_titles.clear()
        stroke = 1.5 * scale
        inset = stroke / 2
        for offset, color in ((px(3), "#172033" if dark else "#EEF1F5"),
                              (px(1.5), "#1B2535" if dark else "#DCE2EA"), (0, "#202B3C")):
            _rounded(c, (inset, inset + offset, width - inset, height - inset + offset), height / 2,
                     fill=color, outline="#3A4658" if offset == 0 else color, width=stroke,
                     tags="strip" if offset == 0 else "shadow")
        pad, cell_fixed, connector, badge, gap, collapse = self._geometry()
        x, cy = ((width - badge) / 2 if self._collapsed else pad), height / 2
        if not self._collapsed:
            measured = [self._measure(self._titles[step.key]) for step in self.steps]
            room = max(0, width - 2*pad - cell_fixed*len(self.steps)
                       - connector*(len(self.steps)-1) - gap - badge - collapse)
            lo, hi = 0, max(measured)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if sum(min(value, mid) for value in measured) <= room:
                    lo = mid
                else:
                    hi = mid - 1
            for index, step in enumerate(self.steps):
                key = step.key
                text_room = min(measured[index], lo)
                text = self._elide(self._titles[key], text_room, compact=compact)
                self._display_titles[key] = text
                cell_width = cell_fixed + text_room
                box = (x, stroke, x + cell_width, height - stroke)
                self._hit_rects[key] = box
                items = self._items[key] = {}
                items["background"] = _rounded(c, box, (height-2*stroke)/2,
                                                fill="#202B3C", outline="", width=stroke)
                nx, r = x + px(6) + px(7.5), px(15)/2
                node = (nx-r, cy-r, nx+r, cy+r)
                items["node"] = c.create_oval(*node, width=stroke)
                items["inner"] = c.create_oval(nx-r+px(3), cy-r+px(3), nx+r-px(3), cy+r-px(3), width=stroke)
                items["dot"] = c.create_oval(nx-stroke, cy-stroke, nx+stroke, cy+stroke, outline="")
                items["check"] = c.create_line(nx-r*.55, cy, nx-r*.1, cy+r*.45,
                                                nx+r*.6, cy-r*.5, fill="white", width=stroke)
                items["bang"] = c.create_line(nx, cy-r*.55, nx, cy+r*.05, fill="white", width=stroke)
                dot_r = .9 * scale
                items["bang_dot"] = c.create_oval(nx-dot_r, cy+r*.4-dot_r, nx+dot_r, cy+r*.4+dot_r,
                                                    fill="white", outline="")
                items["text"] = c.create_text(x + px(6) + px(15) + px(6), cy, anchor="w", text=text,
                                               fill="#F1F5F9", font=self._font)
                x += cell_width
                if index < len(self.steps)-1:
                    items["connector"] = c.create_line(x+px(4.5), cy, x+px(4.5)+px(9), cy, width=stroke)
                    x += connector
            x += gap
        self._badge_box = (x, (height-px(21))/2, x+badge, (height+px(21))/2)
        self._badge_item = _rounded(c, self._badge_box, px(10.5), fill="#354258", outline="", width=stroke)
        self.badge_text_item = c.create_text(x+badge/2, cy, fill="#E2E8F0", font=self._font)
        self._hit_rects["__badge"] = self._badge_box
        if not self._collapsed:
            x += badge
            self._hit_rects["__collapse"] = (x, stroke, x+collapse, height-stroke)
            self._collapse_outline = _rounded(c, self._hit_rects["__collapse"], px(10.5),
                                               fill="#202B3C", outline="", width=stroke)
            c.create_line(x+collapse/2-px(4.5), cy-px(1.5), x+collapse/2, cy+px(3),
                          x+collapse/2+px(4.5), cy-px(1.5), fill="#CBD5E1", width=stroke)
        if self._focus_key not in self._hit_rects:
            self._focus_key = next(iter(self._hit_rects))
        if self._hover_key not in self._hit_rects:
            self._hover_key = None
        self._paint()

    def _paint(self) -> None:
        c = self.canvas
        focused = c.focus_get() is c
        for key, items in self._items.items():
            state = self._data[key].state
            active = key == self.active_step_key
            fill = "#354258" if active or key == self._hover_key else "#202B3C"
            if key == self._pressed_key:
                fill = "#46556B"
            outline = "#93C5FD" if focused and key == self._focus_key else "#94A3B8" if active else ""
            c.itemconfigure(items["background"], fill=fill, outline=outline)
            c.itemconfigure(items["text"], font=self._bold_font if active or state == "running" else self._font)
            c.itemconfigure(items["node"], fill=_STATE_COLORS[state] if state in {"done", "error"} else fill,
                            outline=_STATE_COLORS[state])
            c.itemconfigure(items["inner"], outline="#CBD5E1" if state == "idle" else "#D99A00",
                            state="normal" if state in {"idle", "running"} else "hidden")
            c.itemconfigure(items["dot"], fill="#D99A00", state="normal" if state == "running" else "hidden")
            for name, visible in (("check", state == "done"), ("bang", state == "error"),
                                  ("bang_dot", state == "error")):
                c.itemconfigure(items[name], state="normal" if visible else "hidden")
            if "connector" in items:
                c.itemconfigure(items["connector"], fill="#93B4F5" if state == "done" else "#526075")
        c.itemconfigure(self.badge_text_item, text=self.page.tr("step_count", **self._count_args()))
        c.itemconfigure(self._badge_item, outline="#93C5FD" if focused and self._focus_key == "__badge" else "")
        if not self._collapsed:
            c.itemconfigure(self._collapse_outline,
                            outline="#93C5FD" if focused and self._focus_key == "__collapse" else "",
                            fill="#46556B" if self._pressed_key == "__collapse" else "#354258" if self._hover_key == "__collapse" else "#202B3C")

    def _hit(self, x: float, y: float) -> str | None:
        return next((key for key, (x1, y1, x2, y2) in self._hit_rects.items()
                     if x1 <= x <= x2 and y1 <= y <= y2), None)

    def _hit_tooltip(self) -> str:
        key = self._hover_key or (self._focus_key if self.canvas.focus_get() is self.canvas else None)
        if key in self._data:
            return self._titles[key] + "\n\n" + self.tooltip_text(key)
        if key == "__collapse":
            return self.page.tr("step_collapse")
        return self.page.tr("step_expand") if key == "__badge" else ""

    def _motion(self, event: tk.Event) -> None:
        key = self._hit(event.x, event.y)
        if key != self._hover_key:
            self._hover_key = key
            self._tooltip._show(event)
            self._paint()

    def _leave(self, _event: tk.Event) -> None:
        self._hover_key = None
        self._pressed_key = None
        self._paint()

    def _press(self, event: tk.Event) -> None:
        self._pressed_key = self._hit(event.x, event.y)
        if self._pressed_key is not None:
            self._focus_key = self._pressed_key
        self.canvas.focus_set()
        self._tooltip._hide()
        self._paint()

    def _release(self, event: tk.Event) -> None:
        key = self._hit(event.x, event.y)
        pressed, self._pressed_key = self._pressed_key, None
        if key is not None and key == pressed:
            self._invoke(key)
        self._paint()

    def _invoke(self, key: str) -> None:
        if key == "__badge":
            self.expand()
        elif key == "__collapse":
            self.collapse()
        else:
            self.scroll_to_step(key)

    def _focus(self, event: tk.Event) -> None:
        if self._focus_key not in self._hit_rects:
            self._focus_key = next(iter(self._hit_rects))
        self._paint()
        self._tooltip._show(event)

    def _blur(self, _event: tk.Event) -> None:
        self._tooltip._hide()
        self._paint()

    def _key(self, event: tk.Event) -> str:
        keys = list(self._hit_rects)
        index = keys.index(self._focus_key) if self._focus_key in keys else 0
        if event.keysym in {"Return", "space"}:
            self._invoke(keys[index])
        else:
            index = (0 if event.keysym == "Home" else len(keys)-1 if event.keysym == "End"
                     else (index + (1 if event.keysym == "Right" else -1)) % len(keys))
            self._focus_key = keys[index]
            self._tooltip._show(event)
        self._paint()
        return "break"

    def collapse(self) -> None:
        self._collapsed = True
        self._render()

    def expand(self) -> None:
        self._collapsed = False
        self._render()

    def scroll_to_step(self, step_key: str) -> None:
        self.active_step_key = step_key
        self._paint()
        self._metadata = self._module_targets(self.page)
        metadata = self._metadata.get(self._data[step_key].spec.ordinal)
        target = metadata.get("target") if metadata else None
        if target is None:
            return
        from pages.base_page import VerticalScrollArea

        areas = []
        current: Any = target
        while current is not None:
            if isinstance(current, VerticalScrollArea):
                areas.append(current)
            current = getattr(current, "master", None)
        focus: tk.Misc = target
        for area in areas:
            try:
                area.update_idletasks()
                canvas = area.canvas
                y = canvas.canvasy(focus.winfo_rooty() - canvas.winfo_rooty())
                region = canvas.cget("scrollregion")
                total_height = float(canvas.tk.splitlist(region)[3]) if region else 0.0
                if total_height > 0:
                    margin = min(24.0, max(0.0, y))
                    canvas.yview_moveto(max(0.0, (y - margin) / total_height))
                # If the target lives in a nested scroll area, reveal that
                # viewport in its parent while preserving every xview.
                focus = area
            except (tk.TclError, TypeError, ValueError, IndexError):
                continue

    def refresh_language(self) -> None:
        self._render()

    def refresh_theme(self) -> None:
        size = -round(18 * _ui_scale(self.page))
        self._font.configure(size=size)
        self._bold_font.configure(size=size)
        self._drawing_signature = None
        self._render()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            self._cancel_context_check()
            if self._status_unsubscribe is not None:
                self._status_unsubscribe()
                self._status_unsubscribe = None

    def destroy(self) -> None:
        self._cancel_context_check()
        if self._status_unsubscribe is not None:
            self._status_unsubscribe()
            self._status_unsubscribe = None
        self._invalidate_issued_tokens()
        try:
            self.host._forget_step_progress(self)
        except (AttributeError, tk.TclError):
            pass
        super().destroy()

