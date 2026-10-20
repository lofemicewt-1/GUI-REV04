"""Opt-in shared step progress and advanced-tools chrome.

Chip pages mount these widgets explicitly.  Merely inheriting ``BasePage`` or
registering module headings never creates visible chrome.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
import tkinter as tk
from tkinter import ttk
from typing import Callable, Hashable, Literal, Mapping, Sequence, Any

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
        self._desired_expanded = self.store.get_trapezoid_expanded(state_key, default=False)
        self._owning_area = self._nearest_scroll_area(canvas)
        self._old_scroll_extra: int | None = None
        self._reserved_clearance: int | None = None

        self.floating_group.columnconfigure(0, weight=1)
        self.floating_group.rowconfigure(0, weight=1)
        self.progress_slot = ttk.Frame(floating_group, style="Panel.TFrame")
        self.progress_slot.grid(row=0, column=0, sticky="ew")
        self.progress_slot.columnconfigure(0, weight=1)

        self.button = tk.Canvas(
            floating_group,
            width=300,
            height=32,
            highlightthickness=0,
            borderwidth=0,
            takefocus=True,
            cursor="hand2",
        )
        self.button.grid(row=1, column=0, sticky="n", pady=(0, 0))
        self.button.bind("<Configure>", self._draw_button, add="+")
        self.button.bind("<Button-1>", self._toggle_from_event, add="+")
        self.button.bind("<Return>", self._toggle_from_event, add="+")
        self.button.bind("<space>", self._toggle_from_event, add="+")
        self.button.bind("<FocusIn>", lambda _event: self._draw_button(), add="+")
        self.button.bind("<FocusOut>", lambda _event: self._draw_button(), add="+")
        self._tooltip = SimpleTooltip(self.button, self._accessibility_text)

        # Controls are constructed once, even while collapsed.  The inner
        # frame is a canvas window so unusually tall advanced panels remain
        # reachable when the viewport constrains their height.
        self._panel_outer = ttk.Frame(floating_group, style="Panel.TFrame", padding=6)
        self._panel_outer.grid_propagate(False)
        self._panel_outer.columnconfigure(0, weight=1)
        self._panel_outer.rowconfigure(0, weight=1)
        self._panel_canvas = tk.Canvas(self._panel_outer, highlightthickness=0, borderwidth=0)
        self._panel_canvas.grid(row=0, column=0, sticky="nsew")
        self._panel_scrollbar = ttk.Scrollbar(
            self._panel_outer, orient="vertical", command=self._panel_canvas.yview,
        )
        self._panel_canvas.configure(yscrollcommand=self._panel_scrollbar.set)
        self.panel = ttk.Frame(self._panel_canvas, style="Panel.TFrame", padding=4)
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
            inset = min(14, max(4, width // 12))
            fill = colors.get("panel", "#FFFFFF")
            outline = colors.get("border", "#AAB2BF")
            if self.button.focus_get() is self.button:
                outline = colors.get("accent", "#2563EB")
            self.button.create_polygon(
                inset, 1, width - inset, 1, width - 1, height - 1, 1, height - 1,
                fill=fill, outline=outline, width=2,
            )
            self.button.create_text(
                width // 2, height // 2,
                text=self.host.tr("chip_tools_label"),
                fill=colors.get("text", "#111827"),
                font=("TkDefaultFont", int(getattr(self.host.winfo_toplevel(), "_piezo_body_font_size", 9)), "bold"),
            )
        except tk.TclError:
            pass

    def _toggle_from_event(self, _event: tk.Event | None = None) -> str:
        self.set_expanded(not self.expanded)
        return "break"

    def _on_viewport_configure(self, _event: tk.Event | None = None) -> None:
        try:
            width, height = int(self.canvas.winfo_width()), int(self.canvas.winfo_height())
        except (tk.TclError, ValueError):
            return
        if width <= 1 or height <= 1:
            return
        group_height = max(1, int(self.floating_group.winfo_reqheight()))
        forced = width < 560 or height < max(300, group_height + 110)
        if forced != self._forced_collapsed:
            self._forced_collapsed = forced
            self._apply_expanded_state()
        self._queue_layout()

    def _reserve_scroll_clearance(self, *, extra_height: int = 0) -> None:
        area = self._owning_area
        if area is None:
            return
        if self._old_scroll_extra is None:
            self._old_scroll_extra = int(area.scroll_region_extra)
        clearance = max(44, int(self.floating_group.winfo_reqheight()) + int(extra_height) + 12)
        self._reserved_clearance = max(self._old_scroll_extra, clearance)
        area.scroll_region_extra = self._reserved_clearance
        area._queue_scroll_layout()

    def set_expanded(self, value: bool, *, persist: bool = True) -> None:
        value = bool(value)
        if persist:
            self._desired_expanded = value
            self.store.set_trapezoid_expanded(self.state_key, value)
        self._apply_expanded_state(requested=value if not persist else None)

    def _apply_expanded_state(self, requested: bool | None = None) -> None:
        desired = self._desired_expanded if requested is None else bool(requested)
        actual = desired and not self._forced_collapsed
        self._expanded = actual
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
            # Placed as a canvas child, this group is pinned to the viewport;
            # it is outside the scrolling content window.
            natural_width = max(1, int(self.floating_group.winfo_reqwidth()))
            group_width = min(natural_width, max(1, width - 16))
            self.floating_group.place(
                relx=0.5,
                rely=1.0,
                anchor="s",
                y=-10,
                width=group_width,
            )
            self.button.configure(width=min(300, max(80, width - 20)))
            for bar in getattr(self.host, "_step_progress_bars", ()):
                cell_width = max(72, (group_width - 104) // max(1, len(bar.steps)))
                for cell in bar._cells.values():
                    cell.configure(wraplength=cell_width)
            group_height = max(1, int(self.floating_group.winfo_reqheight()))
            if self.expanded:
                panel_width = min(max(280, int(self.panel.winfo_reqwidth()) + 20), max(240, width - 24))
                max_panel_height = max(80, height - group_height - 24)
                panel_height = min(max_panel_height, max(100, int(self.panel.winfo_reqheight()) + 18))
                # The panel opens from the top edge of the combined floating
                # chrome, so it stays above the progress cells while leaving
                # the trapezoid attached below it.
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
        self._queue_layout()

    def refresh_theme(self) -> None:
        self._draw_button()
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

        super().__init__(parent, style="Panel.TFrame", padding=(6, 4))
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
        self._collapsed = False
        self._metadata = self._module_targets(page)
        self._cells: dict[str, tk.Button] = {}
        self._tooltips: dict[str, Any] = {}

        self.columnconfigure(0, weight=1)
        self._cells_frame = ttk.Frame(self, style="Panel.TFrame")
        self._cells_frame.grid(row=0, column=0, sticky="ew")
        self._cells_frame.columnconfigure(0, weight=1)
        for index, step in enumerate(self.steps):
            self._cells_frame.columnconfigure(index, weight=1, uniform=f"stepcells_{id(self)}")
            button = tk.Button(
                self._cells_frame,
                text="",
                command=lambda key=step.key: self.scroll_to_step(key),
                takefocus=True,
                relief="flat",
                borderwidth=1,
                padx=8,
                pady=4,
                width=15,
                wraplength=190,
                justify="center",
                fg="#FFFFFF",
                activeforeground="#FFFFFF",
            )
            button.grid(row=0, column=index, sticky="ew", padx=2)
            self._cells[step.key] = button
            self._tooltips[step.key] = SimpleTooltip(button, lambda key=step.key: self.tooltip_text(key))

        right = ttk.Frame(self, style="Panel.TFrame")
        right.grid(row=0, column=1, sticky="e", padx=(4, 0))
        self.badge = ttk.Button(right, text="", takefocus=True, command=self.expand)
        self.badge.grid(row=0, column=0, sticky="e")
        self.collapse_button = ttk.Button(right, text="⌄", width=2, takefocus=True, command=self.collapse)
        self.collapse_button.grid(row=0, column=1, sticky="e", padx=(2, 0))
        SimpleTooltip(self.badge, lambda: self.page.tr("step_expand" if self._collapsed else "step_count", **self._count_args()))
        SimpleTooltip(self.collapse_button, lambda: self.page.tr("step_collapse"))
        self._render()
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
                self._clear_all_for_context()
            self._last_context = current
            return current
        if self._last_context is not None and current != self._last_context:
            self._clear_all_for_context()
        self._last_context = current
        return current

    def _clear_all_for_context(self) -> None:
        self._epoch += 1
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
        data.state = "running"
        data.detail = ""
        data.note_key = None
        self._render()
        return EventToken(
            context=current,
            activation_epoch=self._epoch,
            step_key=step_key,
            param_version=data.param_version,
            operation_id=data.operation_id,
            part_fingerprints=tuple(sorted(data.fingerprints.items(), key=lambda item: item[0])),
        )

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

    def invalidate_from(self, step_key: str) -> None:
        index = self._index[step_key]
        for step in self.steps[index:]:
            data = self._data[step.key]
            data.param_version += 1
            data.operation_id += 1
            data.state = "idle"
            data.detail = ""
            data.note_key = "step_changed"
            data.receipts.clear()
        self._render()

    def reset_all(self) -> None:
        self._epoch += 1
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
            count_args = self._count_args()
            for key, data in self._data.items():
                button = self._cells[key]
                metadata = self._metadata.get(data.spec.ordinal, {})
                title_key = metadata.get("title_key")
                title = self.page.tr(title_key) if title_key else key
                button.configure(
                    text=f"{data.spec.ordinal}\n{title}",
                    bg=_STATE_COLORS[data.state],
                    activebackground=_STATE_COLORS[data.state],
                    disabledforeground="#FFFFFF",
                )
            count_text = self.page.tr("step_count", **count_args)
            self.badge.configure(text=count_text if self._collapsed else count_text)
            self.collapse_button.configure(text="⌄")
            if self._collapsed:
                self._cells_frame.grid_remove()
                self.badge.configure(text=count_text, command=self.expand)
                self.collapse_button.grid_remove()
            else:
                self._cells_frame.grid()
                self.badge.configure(text=count_text, command=self.expand)
                self.collapse_button.grid()
            self.host.trapezoid._reserve_scroll_clearance()
            self.host.trapezoid._on_viewport_configure()
            self.host.trapezoid._queue_layout()
        except (tk.TclError, AttributeError):
            pass

    def collapse(self) -> None:
        self._collapsed = True
        self._render()

    def expand(self) -> None:
        self._collapsed = False
        self._render()

    def scroll_to_step(self, step_key: str) -> None:
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
        try:
            size = int(getattr(self.page.winfo_toplevel(), "_piezo_body_font_size", 9))
            for button in self._cells.values():
                button.configure(font=("TkDefaultFont", size))
        except tk.TclError:
            pass
        self._render()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            self._cancel_context_check()

    def destroy(self) -> None:
        self._cancel_context_check()
        try:
            self.host._forget_step_progress(self)
        except (AttributeError, tk.TclError):
            pass
        super().destroy()

