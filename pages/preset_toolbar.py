"""Shared two-row preset and board-slot toolbar.

Chip pages supply callbacks, capabilities, translation keys, and tooltip keys;
this module owns only the common presentation and safe callback dispatch.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Collection, Mapping, Sequence
from tkinter import ttk
from typing import Any

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
    """Render the shared five-column, two-row preset toolbar.

    ``callbacks`` and ``capabilities`` use the keys in :data:`PRESET_ACTIONS`.
    An unsupported action is disabled by default; list it in
    ``hidden_capabilities`` to hide it instead. A missing callback is always
    safe and leaves its button disabled. The slot selector uses the separate
    ``slot_selection`` capability and callback.

    The caller should invoke :meth:`refresh_language` when the active language
    changes. No translated strings are cached in the component.
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
        **frame_options: Any,
    ) -> None:
        frame_options.setdefault("style", "App.TFrame")
        super().__init__(parent, **frame_options)
        self._translate = translate
        self.callbacks = dict(callbacks or {})
        self.capabilities = dict(capabilities or {})
        self.label_keys = {**DEFAULT_LABEL_KEYS, **dict(label_keys or {})}
        self.tooltip_keys = dict(tooltip_keys or {})
        self.hidden_capabilities = frozenset(hidden_capabilities)
        self._on_slot_selected_callback = on_slot_selected
        self.buttons: dict[str, ttk.Button] = {}
        self._tooltips: list[SimpleTooltip] = []

        for column in range(5):
            self.columnconfigure(column, weight=1, uniform="preset_toolbar_columns")

        first_row = PRESET_ACTIONS[:5]
        for column, action in enumerate(first_row):
            self._make_action_button(action, row=0, column=column)

        self.slot_values = tuple(slot_values)
        self._slot_lookup = {str(value): value for value in self.slot_values}
        initial_slot = self._choose_initial_slot(selected_slot)
        self.slot_var = tk.StringVar(self, value="" if initial_slot is None else str(initial_slot))
        self.slot_frame = ttk.Frame(self, style="App.TFrame")
        self.slot_frame.grid(row=1, column=0, sticky="ew", padx=2, pady=(4, 0))
        self.slot_frame.columnconfigure(1, weight=1)
        self.slot_label = ttk.Label(self.slot_frame, style="App.TLabel")
        self.slot_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.slot_combo = ttk.Combobox(
            self.slot_frame,
            textvariable=self.slot_var,
            values=tuple(self._slot_lookup),
            state="readonly",
            width=4,
        )
        self.slot_combo.grid(row=0, column=1, sticky="ew")
        self.slot_combo.bind("<<ComboboxSelected>>", self._on_slot_selected)
        slot_tooltip_key = self.tooltip_keys.get("slot_selection")
        if slot_tooltip_key:
            self._tooltips.append(
                SimpleTooltip(self.slot_combo, lambda key=slot_tooltip_key: self._translate(key))
            )

        second_row = PRESET_ACTIONS[5:]
        for column, action in enumerate(second_row, start=1):
            self._make_action_button(action, row=1, column=column)

        self._apply_slot_capability()
        self.refresh_language()

    @property
    def selected_slot(self) -> int | str | None:
        """Return the selected semantic slot value, not its display variable."""
        return self._slot_lookup.get(self.slot_var.get())

    def refresh_language(self) -> None:
        """Refresh all visible labels from their translation keys."""
        for action, button in self.buttons.items():
            self._set_text(button, self.label_keys[action])
        self._set_text(self.slot_label, self.label_keys["slot_selection"])

    def _make_action_button(self, action: str, *, row: int, column: int) -> None:
        button = ttk.Button(
            self,
            style="Bg.TButton",
            command=lambda name=action: self._invoke(name),
        )
        button.grid(row=row, column=column, sticky="ew", padx=2, pady=(0, 0) if row == 0 else (4, 0))
        self.buttons[action] = button
        supported = self.capabilities.get(action, True)
        if not supported or not callable(self.callbacks.get(action)):
            button.configure(state="disabled")
        if not supported and action in self.hidden_capabilities:
            button.grid_remove()
        tooltip_key = self.tooltip_keys.get(action)
        if tooltip_key:
            self._tooltips.append(SimpleTooltip(button, lambda key=tooltip_key: self._translate(key)))

    def _apply_slot_capability(self) -> None:
        supported = self.capabilities.get("slot_selection", True)
        if not supported or not self.slot_values:
            self.slot_combo.configure(state="disabled")
        if not supported and "slot_selection" in self.hidden_capabilities:
            self.slot_frame.grid_remove()

    def _choose_initial_slot(self, selected_slot: int | str | None) -> int | str | None:
        if selected_slot is not None:
            for value in self.slot_values:
                if value == selected_slot or str(value) == str(selected_slot):
                    return value
        return self.slot_values[0] if self.slot_values else None

    def _invoke(self, action: str) -> Any:
        callback = self.callbacks.get(action)
        if self.capabilities.get(action, True) and callable(callback):
            return callback()
        return None

    def _on_slot_selected(self, _event: tk.Event | None = None) -> None:
        callback = self._on_slot_selected_callback
        value = self.selected_slot
        if (
            self.capabilities.get("slot_selection", True)
            and callable(callback)
            and value is not None
        ):
            callback(value)

    def _set_text(self, widget: tk.Widget, key: str) -> None:
        try:
            widget.configure(text=self._translate(key))
        except tk.TclError:
            pass


__all__ = ["PresetToolbar", "PRESET_ACTIONS", "DEFAULT_LABEL_KEYS"]
