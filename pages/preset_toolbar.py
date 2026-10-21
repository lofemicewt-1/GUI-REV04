"""Shared two-box software-preset and board-slot toolbar."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Collection, Mapping, Sequence
from tkinter import ttk
from typing import Any

from config.lang_r2_b import install as install_r2b_language
from pages.base_page import SimpleTooltip


PRESET_ACTIONS: tuple[str, ...] = (
    "software_preset_1",
    "software_preset_2",
    "software_preset_3",
    "import_page_settings",
    "export_page_settings",
    "save_slot",
    "set_active_slot",
    "apply_slot",
    "read_and_apply",
)
SOFTWARE_ACTIONS = PRESET_ACTIONS[:5]
BOARD_ACTIONS = PRESET_ACTIONS[5:]

DEFAULT_LABEL_KEYS: dict[str, str] = {
    "software_preset_1": "button_mode_preset_1",
    "software_preset_2": "button_mode_preset_2",
    "software_preset_3": "button_mode_preset_3",
    "import_page_settings": "button_mode_import_user_preset",
    "export_page_settings": "button_mode_export_user_preset",
    "slot_selection": "board_slot",
    "save_slot": "save_to_board",
    "set_active_slot": "preset_toolbar_set_active_slot",
    "apply_slot": "preset_toolbar_apply_slot",
    "read_and_apply": "preset_toolbar_read_and_apply",
}


class PresetToolbar(ttk.Frame):
    """Render two responsive groups with inline titles and natural-size controls.

    Unsupported actions are disabled by default. Add an unsupported action to
    hidden_capabilities to hide its legacy button reference. Actions listed in
    auxiliary_actions live in the board-title menu but keep the same callback,
    capability, and button-state checks as their legacy references.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        translate: Callable[[str], str],
        callbacks: Mapping[str, Callable[[], Any] | None] | None = None,
        capabilities: Mapping[str, bool] | None = None,
        label_keys: Mapping[str, str] | None = None,
        tooltip_keys: Mapping[str, str | None] | None = None,
        slot_values: Sequence[int | str] = (0, 1, 2, 3),
        selected_slot: int | str | None = None,
        on_slot_selected: Callable[[int | str], Any] | None = None,
        hidden_capabilities: Collection[str] = (),
        auxiliary_actions: Collection[str] = (),
        **frame_options: Any,
    ) -> None:
        install_r2b_language()
        frame_options.setdefault("style", "App.TFrame")
        super().__init__(parent, **frame_options)
        self._translate = translate
        self.callbacks = dict(callbacks or {})
        self.capabilities = dict(capabilities or {})
        self.label_keys = {**DEFAULT_LABEL_KEYS, **dict(label_keys or {})}
        self.tooltip_keys = dict(tooltip_keys or {})
        self.hidden_capabilities = frozenset(hidden_capabilities)
        self.auxiliary_actions = tuple(dict.fromkeys(auxiliary_actions))
        unknown_actions = set(self.auxiliary_actions) - set(PRESET_ACTIONS)
        if unknown_actions:
            raise ValueError(f"Unknown auxiliary preset actions: {sorted(unknown_actions)}")
        self._on_slot_selected_callback = on_slot_selected
        self.buttons: dict[str, ttk.Button] = {}
        self._tooltips: list[SimpleTooltip] = []
        self._layout_job: str | None = None
        self._layout_running = False
        self._layout_pending = False
        self._destroyed = False
        self._language_dirty = True
        self._measurements_dirty = True
        self._measurement_signature: tuple[Any, ...] | None = None
        self._measured_widths: dict[str, int] = {}
        self._last_layout_signature: tuple[Any, ...] | None = None
        self._last_column_signature: tuple[Any, ...] | None = None
        self._last_orientation: str | None = None
        self._layout_run_count = 0
        self._grid_update_count = 0
        self._bound_widgets: list[tuple[tk.Misc, str, str | None]] = []
        self._parent_for_header: tk.Misc | None = None
        self._group_rows: dict[ttk.Frame, list[ttk.Frame]] = {}
        self._control_layouts: dict[ttk.Frame, tuple[Any, ...]] = {}

        self.software_frame, self._software_title, self._software_title_label, self._software_help = (
            self._make_group("r2b_toolbar_software_title", "r2b_toolbar_software_help")
        )
        self.board_frame, self._board_title, self._board_title_label, self._board_help = (
            self._make_group(
                "r2b_toolbar_board_title",
                "r2b_toolbar_board_help",
                board=bool(self.auxiliary_actions),
            )
        )

        self.slot_values = tuple(slot_values)
        self._slot_lookup = {str(value): value for value in self.slot_values}
        initial_slot = self._choose_initial_slot(selected_slot)
        self.slot_var = tk.StringVar(self, value="" if initial_slot is None else str(initial_slot))
        self.slot_frame = ttk.Frame(self.board_frame, style="App.TFrame")
        self.slot_label = ttk.Label(
            self.slot_frame,
            style="App.TLabel",
            text=self._translate(self.label_keys["slot_selection"]),
        )
        self.slot_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.slot_combo = ttk.Combobox(
            self.slot_frame,
            textvariable=self.slot_var,
            values=tuple(self._slot_lookup),
            state="readonly",
            width=4,
        )
        self.slot_combo.grid(row=0, column=1, sticky="w")
        self.slot_combo.bind("<<ComboboxSelected>>", self._on_slot_selected)
        slot_tooltip_key = self.tooltip_keys.get("slot_selection")
        if slot_tooltip_key:
            self._tooltips.append(
                SimpleTooltip(self.slot_combo, lambda key=slot_tooltip_key: self._translate(key))
            )

        for action in PRESET_ACTIONS:
            target = self.software_frame if action in SOFTWARE_ACTIONS else self.board_frame
            self._make_action_button(action, target)

        self._apply_slot_capability()
        self._software_controls = [
            self.buttons[action]
            for action in SOFTWARE_ACTIONS
            if self._action_is_visible(action)
        ]
        self._board_controls: list[tk.Widget] = []
        if self._slot_is_visible():
            self._board_controls.append(self.slot_frame)
        self._board_controls.extend(
            self.buttons[action]
            for action in BOARD_ACTIONS
            if self._action_is_visible(action)
        )
        self._place_group_controls(self.software_frame, self._software_title, self._software_controls)
        self._place_group_controls(self.board_frame, self._board_title, self._board_controls)
        self._create_auxiliary_menu()
        self._bind_layout_signals()
        self.refresh_language()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def mount_header(self) -> None:
        """Mount below the title in a full-width header container."""
        if self.winfo_manager() == "grid":
            self._queue_layout(invalidate_measurements=True)
            return
        parent = self.master
        self._parent_for_header = parent
        parent.columnconfigure(0, weight=1)
        self.grid(row=1, column=0, sticky="ew")
        self._bind_parent_layout(parent)
        self._queue_layout(invalidate_measurements=True)

    @property
    def selected_slot(self) -> int | str | None:
        """Return the selected semantic slot value, not its display variable."""
        return self._slot_lookup.get(self.slot_var.get())

    def refresh_language(self) -> None:
        """Queue translated labels and one coalesced layout refresh."""
        self._language_dirty = True
        self._queue_layout(invalidate_measurements=True)

    def refresh_theme(self) -> None:
        """Queue a measured layout refresh after theme or font changes."""
        self._queue_layout(invalidate_measurements=True)

    def reflow(self, available_width: int) -> str:
        """Stack groups when needed, wrapping controls within each group."""
        if self._destroyed:
            return self._last_orientation or "stacked"
        if self._layout_running:
            self._queue_layout()
            return self._last_orientation or "stacked"
        self._layout_running = True
        try:
            return self._reflow(available_width)
        finally:
            self._finish_layout()

    def _reflow(self, available_width: int) -> str:
        self.update_idletasks()
        self._measure_group_widths()
        width = max(0, int(available_width))

        software_min = self._measured_widths.get("software", 1)
        board_min = self._measured_widths.get("board", 1)
        side_by_side = width >= software_min + board_min + 8
        orientation = "side_by_side" if side_by_side else "stacked"
        if side_by_side:
            placements = ((self.software_frame, 0, 0), (self.board_frame, 0, 1))
            column_signature = (orientation, software_min, board_min)
        else:
            placements = ((self.software_frame, 0, 0), (self.board_frame, 1, 0))
            column_signature = (orientation,)

        layout_signature = (
            orientation,
            tuple((id(widget), row, column) for widget, row, column in placements),
        )
        if layout_signature != self._last_layout_signature:
            for widget, row, column in placements:
                padx = ((0, 4) if column == 0 else (4, 0)) if side_by_side else (0, 0)
                current = widget.grid_info()
                if (
                    widget.winfo_manager() != "grid"
                    or int(current.get("row", -1)) != row
                    or int(current.get("column", -1)) != column
                    or current.get("sticky") != "ew"
                    or self._normalise_padding(current.get("padx", 0)) != padx
                ):
                    widget.grid(row=row, column=column, sticky="ew", padx=padx)
                    self._grid_update_count += 1
            self._last_layout_signature = layout_signature

        if column_signature != self._last_column_signature:
            if side_by_side:
                self.columnconfigure(0, weight=1, minsize=software_min)
                self.columnconfigure(1, weight=1, minsize=board_min)
            else:
                self.columnconfigure(0, weight=1, minsize=0)
                self.columnconfigure(1, weight=0, minsize=0)
            self.rowconfigure(0, weight=0, minsize=0)
            self.rowconfigure(1, weight=0, minsize=0)
            self._last_column_signature = column_signature
        elif side_by_side:
            self.columnconfigure(0, minsize=software_min)
            self.columnconfigure(1, minsize=board_min)

        self._last_orientation = orientation
        spare = max(0, width - software_min - board_min - 8)
        software_width = software_min + spare // 2 if side_by_side else width
        board_width = board_min + spare - spare // 2 if side_by_side else width
        self._place_group_controls(
            self.software_frame, self._software_title, self._software_controls, software_width
        )
        self._place_group_controls(
            self.board_frame, self._board_title, self._board_controls, board_width
        )
        return orientation

    def destroy(self) -> None:
        self._cancel_layout_job()
        self._unbind_layout_signals()
        super().destroy()

    def _make_group(
        self,
        title_key: str,
        help_key: str,
        *,
        board: bool = False,
    ) -> tuple[ttk.Frame, ttk.Frame, ttk.Label, ttk.Label]:
        group = ttk.Frame(
            self, style="App.TFrame", padding=(6, 2), borderwidth=1, relief="solid"
        )
        group.columnconfigure(0, weight=1)
        title = ttk.Frame(group, style="App.TFrame")
        title_label = ttk.Label(title, style="App.TLabel", text=self._translate(title_key))
        title_label.grid(row=0, column=0, sticky="w")
        help_label = ttk.Label(title, style="App.TLabel", text="?")
        help_label.grid(row=0, column=1, sticky="w", padx=(4, 2))
        self._tooltips.append(SimpleTooltip(help_label, lambda key=help_key: self._translate(key)))
        if board:
            self._board_title_menu_button = ttk.Menubutton(
                title,
                text="⋯",
                style="Toolbutton",
            )
            self._board_title_menu_button.grid(row=0, column=2, sticky="w", padx=(2, 0))
            self._tooltips.append(
                SimpleTooltip(
                    self._board_title_menu_button,
                    lambda: self._translate("r2b_toolbar_aux_menu_help"),
                )
            )
        return group, title, title_label, help_label

    def _make_action_button(self, action: str, parent: ttk.Frame) -> None:
        button = ttk.Button(
            parent,
            style="Bg.TButton",
            text=self._translate(self.label_keys[action]),
            width=0,
            padding=(6, 3),
            command=lambda name=action: self._invoke(name),
        )
        self.buttons[action] = button
        supported = self.capabilities.get(action, True)
        if not supported or not callable(self.callbacks.get(action)):
            button.configure(state="disabled")
        if self._action_is_visible(action):
            tooltip_key = self.tooltip_keys.get(action)
            if tooltip_key:
                self._tooltips.append(
                    SimpleTooltip(button, lambda key=tooltip_key: self._translate(key))
                )

    def _place_group_controls(
        self,
        group: ttk.Frame,
        title: ttk.Frame,
        controls: Sequence[tk.Widget],
        available_width: int | None = None,
    ) -> None:
        # Each line has its own grid so a long control on another line cannot
        # widen the columns here. Widgets retain their original group parent.
        inner_width = max(1, available_width - 14) if available_width is not None else None
        lines: list[list[tk.Widget]] = [[title]]
        used = title.winfo_reqwidth() + 4
        for index, widget in enumerate(controls):
            required = widget.winfo_reqwidth() + 4
            # Keep the title and first control together, even at tiny widths.
            if index > 0 and inner_width is not None and used + required > inner_width:
                lines.append([])
                used = 0
            lines[-1].append(widget)
            used += required
        signature = tuple(tuple(str(widget) for widget in line) for line in lines)
        if self._control_layouts.get(group) == signature:
            return
        rows = self._group_rows.setdefault(group, [])
        while len(rows) < len(lines):
            rows.append(ttk.Frame(group, style="App.TFrame"))
        for row, line in enumerate(lines):
            row_frame = rows[row]
            row_frame.grid(row=row, column=0, sticky="w")
            for column, widget in enumerate(line):
                widget.grid(in_=row_frame, row=0, column=column, sticky="w", padx=2, pady=2)
                # Row frames are younger siblings of the controls; keep the
                # controls above their geometry container in Tk's stacking order.
                widget.lift()
        for row_frame in rows[len(lines):]:
            row_frame.grid_remove()
        self._control_layouts[group] = signature

    def _create_auxiliary_menu(self) -> None:
        self._auxiliary_menu_indexes: dict[str, int] = {}
        if not self.auxiliary_actions:
            self._auxiliary_menu: tk.Menu | None = None
            return
        self._auxiliary_menu = tk.Menu(
            self._board_title_menu_button,
            tearoff=False,
            postcommand=self._refresh_auxiliary_menu_states,
        )
        self._board_title_menu_button.configure(menu=self._auxiliary_menu)
        for index, action in enumerate(self.auxiliary_actions):
            self._auxiliary_menu_indexes[action] = index
            self._auxiliary_menu.add_command(
                label=self._translate(self.label_keys[action]),
                command=lambda name=action: self._invoke(name),
            )
        self._refresh_auxiliary_menu_states()

    def _apply_slot_capability(self) -> None:
        supported = self.capabilities.get("slot_selection", True)
        if not supported or not self.slot_values:
            self.slot_combo.configure(state="disabled")
        self._slot_visible = not (
            not supported and "slot_selection" in self.hidden_capabilities
        )

    def _action_is_visible(self, action: str) -> bool:
        if action in self.auxiliary_actions:
            return False
        return not (
            not self.capabilities.get(action, True)
            and action in self.hidden_capabilities
        )

    def _slot_is_visible(self) -> bool:
        return bool(getattr(self, "_slot_visible", True))

    def _action_is_enabled(self, action: str) -> bool:
        button = self.buttons.get(action)
        try:
            button_enabled = button is not None and str(button.cget("state")) != "disabled"
        except tk.TclError:
            button_enabled = False
        return (
            bool(self.capabilities.get(action, True))
            and callable(self.callbacks.get(action))
            and button_enabled
        )

    def _invoke(self, action: str) -> Any:
        if not self._action_is_enabled(action):
            return None
        callback = self.callbacks.get(action)
        return callback() if callable(callback) else None

    def _refresh_auxiliary_menu_states(self) -> None:
        menu = getattr(self, "_auxiliary_menu", None)
        if menu is None:
            return
        for action, index in self._auxiliary_menu_indexes.items():
            try:
                enabled = self._action_is_enabled(action)
                menu.entryconfigure(index, state="normal" if enabled else "disabled")
            except tk.TclError:
                return

    def _on_slot_selected(self, _event: tk.Event | None = None) -> None:
        callback = self._on_slot_selected_callback
        value = self.selected_slot
        if (
            self.capabilities.get("slot_selection", True)
            and str(self.slot_combo.cget("state")) != "disabled"
            and callable(callback)
            and value is not None
        ):
            callback(value)

    def _choose_initial_slot(self, selected_slot: int | str | None) -> int | str | None:
        if selected_slot is not None:
            for value in self.slot_values:
                if value == selected_slot or str(value) == str(selected_slot):
                    return value
        return self.slot_values[0] if self.slot_values else None

    def _bind_layout_signals(self) -> None:
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Map>", self._on_map, add="+")
        self.bind("<<ThemeChanged>>", self._on_theme_changed, add="+")
        self._bind_parent_layout(self.master)
        try:
            toplevel = self.winfo_toplevel()
            funcid = toplevel.bind("<<ThemeChanged>>", self._on_theme_changed, add="+")
            self._bound_widgets.append((toplevel, "<<ThemeChanged>>", funcid))
        except tk.TclError:
            pass

    def _bind_parent_layout(self, parent: tk.Misc) -> None:
        if any(widget is parent and sequence == "<Configure>" for widget, sequence, _ in self._bound_widgets):
            return
        try:
            configure_id = parent.bind("<Configure>", self._on_parent_configure, add="+")
            map_id = parent.bind("<Map>", self._on_map, add="+")
            theme_id = parent.bind("<<ThemeChanged>>", self._on_theme_changed, add="+")
            self._bound_widgets.extend(
                (
                    (parent, "<Configure>", configure_id),
                    (parent, "<Map>", map_id),
                    (parent, "<<ThemeChanged>>", theme_id),
                )
            )
        except tk.TclError:
            pass

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is self:
            self._queue_layout()

    def _on_parent_configure(self, event: tk.Event) -> None:
        if event.widget is self.master or event.widget is self._parent_for_header:
            self._queue_layout()

    def _on_map(self, _event: tk.Event | None = None) -> None:
        self._queue_layout(invalidate_measurements=True)

    def _on_theme_changed(self, _event: tk.Event | None = None) -> None:
        self.refresh_theme()

    def _queue_layout(self, *, invalidate_measurements: bool = False) -> None:
        if self._destroyed:
            return
        if invalidate_measurements:
            self._measurements_dirty = True
        if self._layout_running:
            # update_idletasks() drains layout signals too. Do not let a new
            # idle layout run until the current measurement/reflow has finished.
            self._layout_pending = True
            return
        if self._layout_job is not None:
            return
        try:
            self._layout_job = self.after_idle(self._run_layout)
        except tk.TclError:
            self._layout_job = None

    def _run_layout(self) -> None:
        self._layout_job = None
        if self._destroyed:
            return
        if self._layout_running:
            self._layout_pending = True
            return
        self._layout_running = True
        try:
            self._layout_run_count += 1
            if self._language_dirty:
                self._refresh_texts()
                self._language_dirty = False
                self._measurements_dirty = True
            try:
                width = int(self.winfo_width())
                if width <= 1:
                    width = int(self.master.winfo_width())
            except tk.TclError:
                width = 0
            self._reflow(width)
        finally:
            self._finish_layout()

    def _finish_layout(self) -> None:
        self._layout_running = False
        if self._layout_pending:
            self._layout_pending = False
            self._queue_layout()

    def _refresh_texts(self) -> None:
        for action, button in self.buttons.items():
            try:
                button.configure(text=self._translate(self.label_keys[action]))
            except tk.TclError:
                pass
        try:
            self.slot_label.configure(text=self._translate(self.label_keys["slot_selection"]))
            self._software_title_label.configure(text=self._translate("r2b_toolbar_software_title"))
            self._board_title_label.configure(text=self._translate("r2b_toolbar_board_title"))
            if self._auxiliary_menu is not None:
                for action, index in self._auxiliary_menu_indexes.items():
                    self._auxiliary_menu.entryconfigure(
                        index,
                        label=self._translate(self.label_keys[action]),
                    )
        except tk.TclError:
            pass
        self._refresh_auxiliary_menu_states()

    def _measure_group_widths(self) -> None:
        signature = self._current_measurement_signature()
        if not self._measurements_dirty and signature == self._measurement_signature:
            return
        self.update_idletasks()
        self._measured_widths = {
            # Measure the unwrapped controls, independent of the current rows.
            "software": 14 + sum(
                widget.winfo_reqwidth() + 4
                for widget in [self._software_title, *self._software_controls]
            ),
            "board": 14 + sum(
                widget.winfo_reqwidth() + 4
                for widget in [self._board_title, *self._board_controls]
            ),
        }
        self._measurement_signature = signature
        self._measurements_dirty = False

    def _current_measurement_signature(self) -> tuple[Any, ...]:
        style = ttk.Style(self)
        widgets: list[tk.Widget] = [
            self.software_frame,
            self.board_frame,
            self._software_title_label,
            self._board_title_label,
            self.slot_label,
            self.slot_combo,
            *self.buttons.values(),
        ]
        widget_signature: list[tuple[Any, ...]] = []
        for widget in widgets:
            try:
                widget_style = str(widget.cget("style"))
                text = str(widget.cget("text")) if "text" in widget.keys() else ""
                font = style.lookup(widget_style or widget.winfo_class(), "font")
                padding = style.lookup(widget_style or widget.winfo_class(), "padding")
                widget_signature.append((widget_style, text, str(font), str(padding)))
            except tk.TclError:
                widget_signature.append(("destroyed",))
        root = self.winfo_toplevel()
        return (
            style.theme_use(),
            getattr(root, "_piezo_body_font_size", None),
            tuple(widget_signature),
            tuple(self._visible_control_keys()),
        )

    def _visible_control_keys(self) -> tuple[str, ...]:
        visible = [f"software:{action}" for action in SOFTWARE_ACTIONS if self._action_is_visible(action)]
        visible.extend(f"board:{action}" for action in BOARD_ACTIONS if self._action_is_visible(action))
        if self._slot_is_visible():
            visible.append("board:slot_selection")
        return tuple(visible)

    @staticmethod
    def _normalise_padding(value: Any) -> tuple[int, ...]:
        if isinstance(value, (tuple, list)):
            return tuple(int(part) for part in value)
        try:
            return tuple(int(part) for part in str(value).split())
        except (TypeError, ValueError):
            try:
                return (int(value),)
            except (TypeError, ValueError):
                return (0,)

    def _cancel_layout_job(self) -> None:
        job, self._layout_job = self._layout_job, None
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass

    def _unbind_layout_signals(self) -> None:
        for widget, sequence, funcid in self._bound_widgets:
            if funcid is None:
                continue
            try:
                widget.unbind(sequence, funcid)
            except tk.TclError:
                pass
        self._bound_widgets.clear()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        self._destroyed = True
        self._cancel_layout_job()
        self._unbind_layout_signals()


__all__ = ["PresetToolbar", "PRESET_ACTIONS", "DEFAULT_LABEL_KEYS"]
