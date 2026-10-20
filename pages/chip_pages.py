from __future__ import annotations

from collections.abc import Iterator

import json
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from core import register_base
from core.lazy_pages import LazyPages
from pages import awp192x_registers
from pages.awp1221_page import AWP1221Page
from pages.awp192x_modes import ButtonModePage, FullModePage, PumpModePage
from pages.base_page import BasePage, SimpleTooltip


AWP1744_AWP1766_VOLTAGE_TABLE = [
    4.422,
    4.863,
    5.293,
    5.734,
    6.164,
    6.615,
    7.035,
    7.486,
    7.926,
    8.376,
    8.787,
    9.237,
    9.668,
    10.118,
    10.529,
    10.989,
    11.389,
    11.840,
    12.260,
    12.701,
    13.141,
    13.582,
    14.012,
    14.452,
    14.893,
    15.343,
    15.754,
    16.204,
    16.635,
    17.095,
    17.506,
    17.956,
]


def _hex_u8(value: int) -> str:
    return f"0x{int(value) & 0xFF:02X}"


def _hex_u16(value: int) -> str:
    return f"0x{int(value) & 0xFFFF:04X}"


def build_awp1744_awp1766_pump_user_preset_payload(
    *,
    device_addr: int,
    amp_code: int,
    pump_enabled: bool | None,
) -> dict[str, Any]:
    return {
        "format": "AWP1744/AWP1766_USER_PRESET",
        "version": 1,
        "chip_address": _hex_u8(device_addr),
        "mode": "awp1744_awp1766_pump_mode",
        "settings": {
            "amp_code": int(amp_code) & 0x1F,
            "pump_enabled": None if pump_enabled is None else bool(pump_enabled),
        },
    }


def validate_awp1744_awp1766_pump_user_preset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("invalid AWP1744/AWP1766 user preset")
    if payload.get("format") not in {"AWP1744/AWP1766_USER_PRESET", "AWP1744_USER_PRESET"} or int(payload.get("version", 0)) != 1:
        raise ValueError("invalid AWP1744/AWP1766 user preset")
    if payload.get("mode") not in {"awp1744_awp1766_pump_mode", "awp1744_pump_mode"}:
        raise ValueError("unsupported AWP1744/AWP1766 preset mode")
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("invalid AWP1744/AWP1766 preset settings")
    amp_code = int(settings.get("amp_code", 0)) & 0x1F
    pump_value = settings.get("pump_enabled")
    pump_enabled = None if pump_value is None else bool(pump_value)
    return build_awp1744_awp1766_pump_user_preset_payload(
        device_addr=int(str(payload.get("chip_address", "0x74")), 0),
        amp_code=amp_code,
        pump_enabled=pump_enabled,
    )


def build_awp1221_user_preset_payload(
    *,
    device_addr: int,
    reg_addr: int,
    write_value: int,
) -> dict[str, Any]:
    return {
        "format": "AWP1221_USER_PRESET",
        "version": 1,
        "chip_address": _hex_u8(device_addr),
        "mode": "awp1221",
        "settings": {
            "reg_addr": int(reg_addr) & 0xFF,
            "write_value": int(write_value) & 0xFFFF,
        },
    }


def validate_awp1221_user_preset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("invalid AWP1221 user preset")
    if payload.get("format") != "AWP1221_USER_PRESET" or int(payload.get("version", 0)) != 1:
        raise ValueError("invalid AWP1221 user preset")
    settings = payload.get("settings")
    if payload.get("mode") != "awp1221" or not isinstance(settings, dict):
        raise ValueError("invalid AWP1221 preset settings")
    return build_awp1221_user_preset_payload(
        device_addr=int(str(payload.get("chip_address", "0x40")), 0),
        reg_addr=int(settings.get("reg_addr", 0)),
        write_value=int(settings.get("write_value", 0)),
    )


class AWP1921AWP1923Page(BasePage):
    """
    AWP1921/AWP1923 \u82af\u7247\u9875\u3002

    \u5de6\u4fa7\u5bfc\u822a\u53ea\u663e\u793a\u82af\u7247\u578b\u53f7\u3002
    \u70b9\u51fb AWP1921/AWP1923 \u540e\uff0c\u5728\u8be5\u9875\u9762\u5185\u90e8\u663e\u793a Mode \u9009\u62e9\uff1a
        - Button Mode
        - Pump Mode
        - Full Mode \u9884\u7559
    """

    page_name = "awp1921_1923"
    DETAIL_REGISTER_OPTIONS = awp192x_registers.awp192x_detail_register_options()
    DETAIL_BIT_COLUMN_MIN_WIDTH = 96
    DETAIL_GRID_MIN_WIDTH = DETAIL_BIT_COLUMN_MIN_WIDTH * 16
    DETAIL_FIELD_LABEL_LINES = 3
    DETAIL_FIELD_LABEL_ROW_MIN_HEIGHT = 52
    DETAIL_FIELD_CONTROL_ROW_MIN_HEIGHT = 34
    DETAIL_FIELDS_CANVAS_HEIGHT = 112
    DETAIL_RESERVED_BG_LIGHT = "#E5E7EB"
    DETAIL_RESERVED_BG_DARK = "#374151"
    DETAIL_RESERVED_FG_LIGHT = "#4B5563"
    DETAIL_RESERVED_FG_DARK = "#D1D5DB"
    STATE_COMMANDS = (
        ("full_cmd_reset", 0x5283, "Secondary.TButton"),
        ("full_cmd_sleep", 0x5350, "Secondary.TButton"),
        ("full_cmd_shutdown", 0x5337, "Danger.TButton"),
        ("full_cmd_hbridge_clear", 0x5F76, "Secondary.TButton"),
    )

    def __init__(self, parent: tk.Misc, ctx: Any, *, defer_build: bool = False) -> None:
        super().__init__(parent, ctx)
        self.addr_var = tk.StringVar(value="Pull-down 0x44")
        self.reg_addr_var = tk.StringVar(value="0x00")
        self.write_value_var = tk.StringVar(value="0x0000")
        self.read_value_var = tk.StringVar(value="")
        self.detail_mode_var = tk.BooleanVar(value=False)
        self.detail_register_var = tk.StringVar(value=self.DETAIL_REGISTER_OPTIONS[0])
        self.detail_note_var = tk.StringVar(value="")
        self.current_mode = "button_mode"
        self.mode_buttons: dict[str, ttk.Button] = {}
        self.state_buttons: dict[str, ttk.Button] = {}
        self.mode_pages = LazyPages(self._install_mode_page)
        self._detail_field_vars: dict[str, tk.StringVar] = {}
        self._detail_field_choice_values: dict[str, dict[str, int]] = {}
        self._detail_tooltips: list[SimpleTooltip] = []
        self._detail_last_view = "write"
        self._plain_reg_addr = self.reg_addr_var.get()
        self._plain_write_value = self.write_value_var.get()
        self._detail_value_drafts: dict[int, str] = {}
        self._detail_loaded_address: int | None = None
        self._detail_session = False
        self.detail_fields_canvas: tk.Canvas | None = None
        self.detail_fields_frame: tk.Frame | None = None
        self._detail_fields_window_id: int | None = None
        self._preparing = defer_build
        self._build_steps = self._initialize_steps()
        if not defer_build:
            for _ in self._build_steps:
                pass

    def _initialize_steps(self) -> Iterator[None]:
        yield from self._build_ui_steps()
        self._preparing = False

    def _build_ui(self) -> None:
        for _ in self._build_ui_steps():
            pass

    def _build_ui_steps(self) -> Iterator[None]:
        yield
        self.enable_pinned_footer_layout()
        # The navigation owns this pinned viewport; child requests must not resize it.
        self.grid_propagate(False)

        header = self.make_title(self, "awp1921_1923")
        header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())

        self.mode_scroll, self.mode_container = self.make_vertical_scroll_area(
            self, horizontal=False, propagate_width=True, clamp_width=True,
        )
        self.mode_scroll.grid(row=1, column=0, sticky="nsew")
        yield

        self.footer_scroll, footer_content = self.make_vertical_scroll_area(self, horizontal=False)
        self.footer_scroll.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        footer_content.columnconfigure(0, weight=1)
        footer = ttk.Frame(footer_content, style="Panel.TFrame", padding=(12, 8))
        footer.grid(row=0, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self._footer_fit_job = None
        yield
        footer.bind("<Configure>", self._queue_footer_fit)
        self.bind("<Configure>", self._queue_footer_fit, add="+")
        self._footer = footer
        from pages.base_page import WrappingGroupBar

        self.identity_bar = WrappingGroupBar(footer)
        self.identity_bar.grid(row=0, column=0, sticky="ew")
        yield from self._build_identity_bar_steps(self.identity_bar)
        yield
        self.trapezoid = self.mount_trapezoid(
            self._build_state_actions,
            self._build_register_bar,
            state_key="awp1921_1923",
            build_extra=self._build_detail_host,
        )
        self.reg_panel = self.trapezoid.registers
        # Trapezoid mounts advanced tools synchronously; keep prepare cancelable
        # for the page-preparation suite (needs >40 host+mode steps).
        for _ in range(12):
            yield
        # Keep mode headers away from the inner canvas clip edge on compact
        # windows while retaining horizontal scrolling for wide panels.
        self.mode_container.configure(padding=(8, 0, 8, 0))
        self.mode_container.rowconfigure(0, weight=1)
        self.mode_container.columnconfigure(0, weight=1)

        for name, page_type in (("button_mode", ButtonModePage), ("pump_mode", PumpModePage), ("full_mode", FullModePage)):
            self.mode_pages.register(name, lambda page_type=page_type, defer_build=False: page_type(self.mode_container, self.ctx, defer_build=defer_build), staged=True)

        self.refresh_language()
        yield
        if not self._preparing:
            self._on_addr_changed()
        yield from self.mode_pages.prepare("button_mode")
        self.show_mode("button_mode", activate=not self._preparing)
        self._prepared_activation_pending = self._preparing

    def _queue_footer_fit(self, _event=None):
        if self._footer_fit_job is None:
            self._footer_fit_job = self.after_idle(self._fit_footer_height)

    def _fit_footer_height(self):
        self._footer_fit_job = None
        try:
            if self.winfo_height() > 1:
                desired = min(self._footer.winfo_reqheight() + 16, max(48, int(self.winfo_height() * .2)))
                if self.footer_scroll.canvas.winfo_reqheight() != desired:
                    self.footer_scroll.canvas.configure(height=desired)
        except tk.TclError:
            pass

    def _install_mode_page(self, _name: str, page: ttk.Frame) -> None:
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        refresh = getattr(getattr(self.ctx, "app", None), "_refresh_widget_fonts", None)
        if callable(refresh):
            refresh(page)

    def _build_state_actions(self, parent: tk.Misc) -> None:
        for _ in self._build_state_actions_steps(parent):
            pass

    def _build_state_actions_steps(self, parent: tk.Misc) -> Iterator[None]:
        yield
        for column, (key, value, style) in enumerate(self.STATE_COMMANDS):
            parent.columnconfigure(column, weight=1, uniform="chip_state_actions")
            button = ttk.Button(
                parent,
                style=style,
                command=lambda command_value=value: self.write_chip_state(command_value),
            )
            button.grid(row=0, column=column, sticky="ew", padx=(0, 8))
            self.state_buttons[key] = button

    def _build_identity_bar(self, parent: tk.Misc) -> None:
        for _ in self._build_identity_bar_steps(parent):
            pass

    def _build_identity_bar_steps(self, parent: tk.Misc) -> Iterator[None]:
        yield
        serial_group = parent.add_group()
        self.make_serial_selector(serial_group, page_name=self.page_name, row=0, column=0)
        addr_group = parent.add_group()
        self.addr_label = ttk.Label(addr_group, style="Panel.TLabel")
        self.addr_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.addr_combo = ttk.Combobox(
            addr_group,
            textvariable=self.addr_var,
            values=["Pull-down 0x44", "Pull-up 0x45", "Floating 0x46"],
            state="readonly",
            width=18,
        )
        yield
        self.addr_combo.grid(row=0, column=1, sticky="w")
        self.addr_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_addr_changed())

    def _build_register_bar(self, parent: tk.Misc) -> None:
        for _ in self._build_register_bar_steps(parent):
            pass

    def _build_register_bar_steps(self, parent: tk.Misc) -> Iterator[None]:
        yield
        title_group = parent.add_group()
        self.reg_title_label = ttk.Label(title_group, style="Panel.TLabel")
        self.reg_title_label.grid(row=0, column=0, sticky="w")

        addr_group = parent.add_group()
        self.reg_addr_label = ttk.Label(addr_group, style="Panel.TLabel")
        self.reg_addr_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        yield
        ttk.Entry(addr_group, textvariable=self.reg_addr_var, width=8).grid(row=0, column=1, sticky="w")

        write_group = parent.add_group()
        self.write_value_label = ttk.Label(write_group, style="Panel.TLabel")
        self.write_value_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.write_value_entry = ttk.Entry(write_group, textvariable=self.write_value_var, width=10)
        self.write_value_entry.grid(row=0, column=1, sticky="w")
        yield
        self.write_value_entry.bind("<Return>", lambda _event: self._commit_detail_shared_value(), add="+")
        self.write_value_entry.bind("<FocusOut>", lambda _event: self._commit_detail_shared_value(), add="+")

        read_group = parent.add_group()
        self.read_value_label = ttk.Label(read_group, style="Panel.TLabel")
        self.read_value_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(read_group, textvariable=self.read_value_var, width=12, state="readonly").grid(row=0, column=1, sticky="w")
        yield

        button_group = parent.add_group()
        self.rr_btn = ttk.Button(button_group, style="Secondary.TButton", command=self.read_register)
        self.rr_btn.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.rw_btn = ttk.Button(button_group, style="Primary.TButton", command=self.write_register)
        self.rw_btn.grid(row=0, column=1, sticky="w")

        self._detail_entry_group = parent.add_group()
        yield
        self.detail_mode_check = ttk.Checkbutton(
            self._detail_entry_group,
            variable=self.detail_mode_var,
            command=self._on_detail_mode_changed,
        )
        self.detail_mode_check.grid(row=0, column=0, sticky="w")

    def _build_detail_host(self, footer: ttk.Frame) -> None:
        for _ in self._build_detail_host_steps(footer):
            pass

    def _build_detail_host_steps(self, footer: ttk.Frame) -> Iterator[None]:
        yield
        self._detail_host = ttk.Frame(footer, style="Panel.TFrame")
        self._detail_host.columnconfigure(0, weight=1)
        self._detail_host.rowconfigure(0, weight=1)
        self._detail_scroll, detail_inner = self.make_vertical_scroll_area(self._detail_host, horizontal=False)
        self._detail_scroll.grid(row=0, column=0, sticky="nsew")
        self.detail_frame = ttk.Frame(detail_inner, style="Panel.TFrame")
        yield
        self.detail_frame.grid(row=0, column=0, sticky="ew")
        self.detail_frame.columnconfigure(1, weight=1)
        self.detail_frame.columnconfigure(3, weight=1)

        self.detail_register_label = ttk.Label(self.detail_frame, style="Panel.TLabel")
        self.detail_register_label.grid(row=0, column=0, sticky="w", padx=(0, 6))

        self.detail_register_combo = ttk.Combobox(
            self.detail_frame,
            textvariable=self.detail_register_var,
            values=self.DETAIL_REGISTER_OPTIONS,
            state="readonly",
            width=34,
        )
        yield
        self.detail_register_combo.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self.detail_register_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_detail_register_selected())

        self.detail_note_label = ttk.Label(
            self.detail_frame,
            textvariable=self.detail_note_var,
            style="Muted.TLabel",
            wraplength=520,
        )
        self.detail_note_label.grid(row=0, column=2, columnspan=2, sticky="ew")

        detail_fields_shell = ttk.Frame(self.detail_frame, style="Panel.TFrame")
        detail_fields_shell.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        yield
        detail_fields_shell.columnconfigure(0, weight=1)

        self.detail_fields_canvas = tk.Canvas(
            detail_fields_shell,
            height=self.DETAIL_FIELDS_CANVAS_HEIGHT,
            bg=self.colors["panel"],
            highlightthickness=0,
            bd=0,
        )
        self.detail_fields_canvas.grid(row=0, column=0, sticky="ew")

        detail_fields_scrollbar = ttk.Scrollbar(
            detail_fields_shell,
            orient="horizontal",
            command=self.detail_fields_canvas.xview,
        )
        detail_fields_scrollbar.grid(row=1, column=0, sticky="ew")
        self.detail_fields_canvas.configure(xscrollcommand=detail_fields_scrollbar.set)
        yield

        self.detail_fields_frame = tk.Frame(self.detail_fields_canvas, bg=self.colors["panel"])
        self._detail_fields_window_id = self.detail_fields_canvas.create_window(
            (0, 0),
            window=self.detail_fields_frame,
            anchor="nw",
        )
        self.detail_fields_frame.bind("<Configure>", self._sync_detail_fields_scrollregion)
        self.detail_fields_canvas.bind("<Configure>", self._sync_detail_fields_canvas_width)
        detail_inner.columnconfigure(0, weight=1)

    def _parse_int(self, text: str) -> int:
        return int(text.strip(), 0)

    def _sync_detail_fields_scrollregion(self, _event: tk.Event | None = None) -> None:
        canvas = self.detail_fields_canvas
        if canvas is None:
            return
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _sync_detail_fields_canvas_width(self, event: tk.Event) -> None:
        canvas = self.detail_fields_canvas
        if canvas is None or self._detail_fields_window_id is None:
            return
        canvas.itemconfigure(
            self._detail_fields_window_id,
            width=max(event.width, self.DETAIL_GRID_MIN_WIDTH),
        )
        self._sync_detail_fields_scrollregion()

    def _current_detail_definition(self) -> register_base.RegisterDefinition | None:
        try:
            address = awp192x_registers.parse_awp192x_detail_register_option(self.detail_register_var.get())
        except Exception:
            return None
        return awp192x_registers.get_awp192x_detail_register_definition(address)

    def detail_mode_active(self) -> bool:
        return self.current_mode == "full_mode" and bool(self.detail_mode_var.get())

    def _active_register_address(self) -> int:
        definition = self._current_detail_definition() if self.detail_mode_active() else None
        if definition is not None:
            return definition.address
        return self._parse_int(self.reg_addr_var.get())

    def _store_detail_draft(self) -> None:
        address = self._detail_loaded_address
        if address is None:
            return
        self._detail_value_drafts[int(address)] = self.write_value_var.get()

    def _apply_detail_host_height(self) -> None:
        try:
            top_height = int(self.winfo_toplevel().winfo_height())
        except tk.TclError:
            top_height = 0
        cap = 220
        if top_height > 1:
            cap = max(96, min(220, top_height // 3))
        content = max(96, int(self.detail_frame.winfo_reqheight()))
        self._detail_host.grid_propagate(False)
        self._detail_host.configure(height=min(cap, content))

    def _show_detail_editor(self) -> None:
        self._detail_host.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._apply_detail_host_height()
        self.write_value_label.configure(text=self.tr("detail_register_value"))

    def _hide_detail_editor(self) -> None:
        self._detail_host.grid_remove()
        self.detail_note_var.set("")
        self.write_value_label.configure(text=self.tr("write_value"))
        self._set_register_button_states(can_read=True, can_write=True)

    def _sync_detail_mode_state(self) -> None:
        full = self.current_mode == "full_mode"
        self.reg_panel.set_group_visible(self._detail_entry_group, full)
        active = self.detail_mode_active()
        if active and not self._detail_session:
            self._plain_reg_addr = self.reg_addr_var.get()
            self._plain_write_value = self.write_value_var.get()
            self._detail_session = True
            self._show_detail_editor()
            self._on_detail_register_selected()
            return
        if not active and self._detail_session:
            self._store_detail_draft()
            self._detail_session = False
            self.reg_addr_var.set(self._plain_reg_addr)
            self.write_value_var.set(self._plain_write_value)
            self._hide_detail_editor()
            return
        if active:
            self._show_detail_editor()
        else:
            self._hide_detail_editor()

    def _on_detail_mode_changed(self) -> None:
        if self.current_mode != "full_mode":
            return
        self._sync_detail_mode_state()

    def _on_detail_register_selected(self) -> None:
        if not self.detail_mode_active():
            return
        definition = self._current_detail_definition()
        if definition is None:
            return

        previous = self._detail_loaded_address
        if previous is not None and previous != definition.address:
            self._detail_value_drafts[int(previous)] = self.write_value_var.get()
        self.reg_addr_var.set(f"0x{definition.address:02X}")
        draft = self._detail_value_drafts.get(definition.address)
        if draft is None:
            current_value = definition.default_value & definition.value_mask
            draft = register_base.format_register_value(definition, current_value)
        self._detail_loaded_address = definition.address
        self.write_value_var.set(draft)
        self._sync_detail_note(definition)
        self._set_register_button_states(can_read=definition.can_read, can_write=definition.can_write)
        self._refresh_detail_fields(definition=definition, view="write")

    def _sync_detail_note(self, definition: register_base.RegisterDefinition) -> None:
        parts: list[str] = []
        if not definition.can_write:
            parts.append(self.tr("detail_read_only"))
        if not definition.can_read:
            parts.append(self.tr("detail_write_only"))
        self.detail_note_var.set(" ".join(parts))

    def _set_register_button_states(self, *, can_read: bool, can_write: bool) -> None:
        self.rr_btn.state(["!disabled"] if can_read else ["disabled"])
        self.rw_btn.state(["!disabled"] if can_write else ["disabled"])

    def _commit_detail_shared_value(self) -> None:
        if not self.detail_mode_active():
            return
        definition = self._current_detail_definition()
        if definition is None:
            return
        try:
            value = self._parse_int(self.write_value_var.get()) & definition.value_mask
        except Exception:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            self._refresh_detail_fields(definition=definition, view=self._detail_last_view)
            return
        self.write_value_var.set(register_base.format_register_value(definition, value))
        self._refresh_detail_fields(definition=definition, view=self._detail_last_view)

    def _refresh_detail_fields(
        self,
        *,
        definition: register_base.RegisterDefinition | None = None,
        view: str = "write",
    ) -> None:
        if not self.detail_mode_active():
            return

        definition = definition or self._current_detail_definition()
        if definition is None:
            return

        self._detail_last_view = view
        if self.detail_fields_frame is None:
            return
        for widget in self.detail_fields_frame.winfo_children():
            widget.destroy()
        self._detail_tooltips.clear()
        self._detail_field_vars = {}
        self._detail_field_choice_values = {}

        try:
            value = self._parse_int(self.write_value_var.get()) & definition.value_mask
        except Exception:
            value = 0

        for column in range(definition.width):
            self.detail_fields_frame.columnconfigure(
                column,
                weight=1,
                uniform="detail_bits",
                minsize=self.DETAIL_BIT_COLUMN_MIN_WIDTH,
            )
        self.detail_fields_frame.rowconfigure(0, weight=1)

        placements = register_base.build_field_layout(definition, value, view=view)
        for placement in placements:
            block = placement.block
            if block.is_reserved:
                field_bg, label_fg = self._detail_reserved_colors()
            else:
                field_bg = self.colors["panel"]
                label_fg = self.colors["text"]
            field = tk.Frame(
                self.detail_fields_frame,
                bg=field_bg,
                highlightbackground=self.colors["border"],
                highlightcolor=self.colors["border"],
                highlightthickness=1,
                bd=0,
            )
            field.grid(
                row=0,
                column=placement.column,
                columnspan=placement.columnspan,
                sticky="nsew",
                padx=0,
                pady=0,
            )
            field.columnconfigure(0, weight=1)
            field.rowconfigure(0, minsize=self.DETAIL_FIELD_LABEL_ROW_MIN_HEIGHT, weight=0)
            field.rowconfigure(1, minsize=self.DETAIL_FIELD_CONTROL_ROW_MIN_HEIGHT, weight=0)

            bit_range = str(block.high_bit) if block.high_bit == block.low_bit else f"{block.high_bit}:{block.low_bit}"
            label = tk.Label(
                field,
                text=f"{bit_range}\n{block.label}",
                bg=field_bg,
                fg=label_fg,
                justify="center",
                width=1,
                height=self.DETAIL_FIELD_LABEL_LINES,
                anchor="center",
                wraplength=max(72, placement.columnspan * self.DETAIL_BIT_COLUMN_MIN_WIDTH - 10),
            )
            label.grid(**self._detail_label_grid_options(block))

            tooltip = self._detail_tooltip_text(block)
            self._detail_tooltips.append(SimpleTooltip(label, tooltip))

            if block.is_reserved:
                continue

            if self._detail_field_uses_value_label(definition, block):
                value = tk.Label(
                    field,
                    text=self._detail_field_value_text(block),
                    bg=field_bg,
                    fg=label_fg,
                    justify="center",
                    anchor="center",
                    width=1,
                )
                value.grid(row=1, column=0, sticky="nsew", padx=3, pady=(0, 3))
                self._detail_tooltips.append(SimpleTooltip(value, tooltip))
                continue

            var = tk.StringVar()
            self._detail_field_vars[block.key] = var
            self._detail_field_choice_values[block.key] = {}

            if self._detail_field_uses_text_entry(definition, block):
                var.set(self._detail_field_value_text(block))
                entry = ttk.Entry(field, textvariable=var, width=1, justify="center")
                entry.grid(row=1, column=0, sticky="ew", padx=3, pady=(0, 3))
                entry.bind("<Return>", lambda _event, key=block.key: self._apply_detail_field_value(key), add="+")
                entry.bind("<FocusOut>", lambda _event, key=block.key: self._apply_detail_field_value(key), add="+")
                self._detail_tooltips.append(SimpleTooltip(entry, tooltip))
                continue

            choices = register_base.build_field_value_choices(block)
            choice_values = {choice.label: choice.value for choice in choices}
            value_label = next((choice.label for choice in choices if choice.value == block.value), self._detail_field_value_text(block))
            var.set(value_label)
            self._detail_field_choice_values[block.key] = choice_values

            entry = ttk.Combobox(
                field,
                textvariable=var,
                values=[choice.label for choice in choices],
                state="readonly",
                width=1,
                justify="center",
            )
            entry.grid(row=1, column=0, sticky="ew", padx=3, pady=(0, 3))
            entry.bind("<<ComboboxSelected>>", lambda _event, key=block.key: self._apply_detail_field_value(key), add="+")
            entry.bind("<Button-1>", self._open_detail_combobox, add="+")
            self._detail_tooltips.append(SimpleTooltip(entry, tooltip))

    def _detail_reserved_colors(self) -> tuple[str, str]:
        if self.colors.get("canvas_bg") == "#111827":
            return self.DETAIL_RESERVED_BG_DARK, self.DETAIL_RESERVED_FG_DARK
        return self.DETAIL_RESERVED_BG_LIGHT, self.DETAIL_RESERVED_FG_LIGHT

    @staticmethod
    def _detail_label_grid_options(block: register_base.RegisterFieldBlock) -> dict[str, Any]:
        options: dict[str, Any] = {
            "row": 0,
            "column": 0,
            "sticky": "nsew",
            "padx": 2,
            "pady": (3, 2),
        }
        if block.is_reserved:
            options["rowspan"] = 2
        return options

    @staticmethod
    def _detail_field_uses_value_label(
        definition: register_base.RegisterDefinition,
        block: register_base.RegisterFieldBlock,
    ) -> bool:
        return not block.is_reserved and not definition.can_write

    @staticmethod
    def _detail_field_uses_text_entry(
        definition: register_base.RegisterDefinition,
        block: register_base.RegisterFieldBlock,
    ) -> bool:
        return definition.can_write and not block.is_reserved and not block.choices and block.span > 1

    @staticmethod
    def _detail_field_value_text(block: register_base.RegisterFieldBlock) -> str:
        if block.is_reserved:
            return "Reserved"
        if block.span == 1:
            return str(block.value)
        return f"0x{block.value:X}"

    @staticmethod
    def _open_detail_combobox(event: tk.Event) -> str | None:
        widget = event.widget
        try:
            if "disabled" in widget.state():
                return None
            widget.focus_set()
            widget.tk.call("ttk::combobox::Post", widget)
            return "break"
        except tk.TclError:
            return None

    def _detail_tooltip_text(self, block: register_base.RegisterFieldBlock) -> str:
        bit_range = f"bit {block.high_bit}" if block.high_bit == block.low_bit else f"bits {block.high_bit}:{block.low_bit}"
        if block.tooltip:
            return f"{bit_range}\n{block.tooltip}"
        return bit_range

    def _apply_detail_field_value(self, field_key: str) -> None:
        if not self.detail_mode_active():
            return
        definition = self._current_detail_definition()
        if definition is None or not definition.can_write:
            return
        bit_field = next((field for field in definition.fields if field.key == field_key), None)
        if bit_field is None or bit_field.is_reserved:
            return

        variable = self._detail_field_vars.get(field_key)
        if variable is None:
            return

        try:
            current_value = self._parse_int(self.write_value_var.get()) & definition.value_mask
            text = variable.get()
            field_value = self._detail_field_choice_values.get(field_key, {}).get(text)
            if field_value is None:
                field_value = register_base.parse_field_choice_value(text)
            next_value = register_base.update_register_field(definition, current_value, field_key, field_value)
        except Exception:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            self._refresh_detail_fields(definition=definition, view="write")
            return

        self.write_value_var.set(register_base.format_register_value(definition, next_value))
        self._refresh_detail_fields(definition=definition, view="write")

    def read_register(self) -> None:
        if not self.ctx.serial.is_connected:
            from tkinter import messagebox
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return
        definition = self._current_detail_definition() if self.detail_mode_active() else None
        if definition is not None and not definition.can_read:
            return
        begin = getattr(self, "begin_status", None)
        status_token = begin("register_read") if callable(begin) else None
        try:
            reg = self._active_register_address()
            device_addr = self.ctx.state.awp192x_device_addr
            resp = self.ctx.serial.read_register_value(device_addr, reg, bits=16)
            if resp.ok:
                value = self.ctx.serial.extract_rdataw_value(
                    resp.data,
                    device_addr=device_addr,
                    reg_addr=reg,
                    bits=16,
                )
                if value is None:
                    self.read_value_var.set(resp.data)
                elif definition is not None:
                    formatted = register_base.format_register_value(definition, value)
                    self.read_value_var.set(formatted)
                    self.write_value_var.set(formatted)
                    self._refresh_detail_fields(definition=definition, view="read")
                else:
                    self.read_value_var.set(f"0x{value & 0xFFFF:04X}")
                finish = getattr(self, "finish_status", None)
                if callable(finish):
                    finish(status_token, success=True)
            else:
                finish = getattr(self, "finish_status", None)
                if callable(finish):
                    finish(status_token, success=False, error=resp.error)
                from tkinter import messagebox
                messagebox.showerror(self.tr("error"), resp.error)
        except Exception as exc:
            finish = getattr(self, "finish_status", None)
            if callable(finish):
                finish(status_token, success=False, error=str(exc))
            from tkinter import messagebox
            messagebox.showerror(self.tr("error"), str(exc))

    def write_register(self) -> None:
        if not self.ctx.serial.is_connected:
            from tkinter import messagebox
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return
        definition = self._current_detail_definition() if self.detail_mode_active() else None
        if definition is not None and not definition.can_write:
            return
        begin = getattr(self, "begin_status", None)
        status_token = begin("register_write") if callable(begin) else None
        try:
            reg = self._active_register_address()
            value = self._parse_int(self.write_value_var.get())
            if definition is not None:
                value &= definition.value_mask
                self.write_value_var.set(register_base.format_register_value(definition, value))
            resp = self.ctx.serial.write_register_value(
                self.ctx.state.awp192x_device_addr,
                reg,
                value,
                bits=16,
            )
            if not resp.ok:
                finish = getattr(self, "finish_status", None)
                if callable(finish):
                    finish(status_token, success=False, error=resp.error)
                from tkinter import messagebox
                messagebox.showerror(self.tr("error"), resp.error)
            else:
                finish = getattr(self, "finish_status", None)
                if callable(finish):
                    finish(status_token, success=True)
                self._reset_step_progress()
                if definition is not None:
                    self._refresh_detail_fields(definition=definition, view="write")
        except Exception as exc:
            finish = getattr(self, "finish_status", None)
            if callable(finish):
                finish(status_token, success=False, error=str(exc))
            from tkinter import messagebox
            messagebox.showerror(self.tr("error"), str(exc))

    def write_chip_state(self, value: int) -> None:
        from core.ui_dispatch import post_to_ui

        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return

        device_addr = self.ctx.state.awp192x_device_addr
        progress_context = self._step_progress_context()

        def finish(ok: bool, error: str) -> None:
            if not ok:
                messagebox.showerror(self.tr("error"), error)
                return
            if progress_context == self._step_progress_context():
                self._reset_step_progress()

        def worker() -> None:
            response = self.ctx.serial.write_register_value(
                device_addr,
                0x01,
                value & 0xFFFF,
                bits=16,
            )
            post_to_ui(self, finish, response.ok, response.error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_addr_changed(self) -> None:
        previous_addr = getattr(self.ctx.state, "awp192x_device_addr", None)
        selected = self.addr_var.get()
        if "0x45" in selected:
            addr = 0x45
        elif "0x46" in selected:
            addr = 0x46
        else:
            addr = 0x44

        self.ctx.state.awp192x_device_addr = addr
        if previous_addr is not None and int(previous_addr) != addr:
            self._reset_step_progress()

    def _step_progress_context(self) -> Any:
        from core.awp192x_settings import connection_key_for_context
        from pages.step_progress import ProgressContext

        serial = self.ctx.serial
        try:
            session = connection_key_for_context(self.ctx) if bool(getattr(serial, "is_connected", False)) else None
        except Exception:
            session = None
        try:
            address = int(self.ctx.state.awp192x_device_addr)
        except (AttributeError, TypeError, ValueError):
            address = 0
        return ProgressContext(session, address, str(self.current_mode))

    def on_show(self) -> None:
        super().on_show()
        if getattr(self, "_prepared_activation_pending", False):
            self._prepared_activation_pending = False
            self._on_addr_changed()
        page = dict.get(self.mode_pages, self.current_mode)
        self.show_step_chrome(page if getattr(page, "step_progress", None) is not None else None)
        if page is not None and hasattr(page, "on_show"):
            page.on_show()

    def on_hide(self) -> None:
        page = dict.get(self.mode_pages, self.current_mode)
        if page is not None and hasattr(page, "on_hide"):
            page.on_hide()
        self._reset_step_progress()

    def _reset_step_progress(self) -> None:
        for bar in list(self._step_progress_bars):
            bar.reset_all()
        for page in dict.values(self.mode_pages):
            clear_receipts = getattr(page, "_clear_button_waveform_slice_receipts", None)
            if callable(clear_receipts):
                clear_receipts()

    def show_mode(self, mode: str, *, activate: bool = True) -> None:
        if mode not in self.mode_pages:
            return
        previous_mode = self.current_mode
        if mode != previous_mode:
            previous_page = dict.get(self.mode_pages, previous_mode)
            if previous_page is not None and hasattr(previous_page, "on_hide"):
                previous_page.on_hide()
        if mode != previous_mode or (activate and mode == "full_mode"):
            # Mode changes and activation of Full invalidate proofs across
            # every mode, including transitions where no bar is active.
            self._reset_step_progress()
        self.current_mode = mode
        self._sync_mode_button_styles()
        for page in dict.values(self.mode_pages):
            page.grid_remove()
        page = self.mode_pages[mode]
        page.grid()
        if activate:
            self.show_step_chrome(page if getattr(page, "step_progress", None) is not None else None)
        else:
            self.show_step_chrome(None)
        if activate and hasattr(page, "on_show"):
            page.on_show()
        self._sync_detail_mode_state()

    def refresh_language(self) -> None:
        super().refresh_language()
        self.addr_label.configure(text=self.tr("current_chip_addr"))
        if hasattr(self, "reg_title_label"):
            self.reg_title_label.configure(text=self.tr("read_write_registers"))
            self.reg_addr_label.configure(text=self.tr("reg_addr"))
            self.write_value_label.configure(
                text=self.tr("detail_register_value") if self.detail_mode_active() else self.tr("write_value")
            )
            self.read_value_label.configure(text=self.tr("read_value"))
            self.rr_btn.configure(text=self.tr("rr"))
            self.rw_btn.configure(text=self.tr("rw"))
            self.detail_mode_check.configure(text=self.tr("detail_mode"))
            self.detail_register_label.configure(text=self.tr("detail_register"))
            definition = self._current_detail_definition()
            if definition is not None:
                self._sync_detail_note(definition)
        for key, btn in self.mode_buttons.items():
            btn.configure(text=self.tr(key))
        for key, btn in self.state_buttons.items():
            btn.configure(text=self.tr(key))
        for page in dict.values(self.mode_pages):
            if hasattr(page, "refresh_language"):
                page.refresh_language()

    def refresh_theme(self) -> None:
        super().refresh_theme()
        if self.detail_fields_canvas is not None:
            self.detail_fields_canvas.configure(bg=self.colors["panel"])
        if self.detail_fields_frame is not None:
            self.detail_fields_frame.configure(bg=self.colors["panel"])
        if self.detail_mode_active():
            self._refresh_detail_fields(view=self._detail_last_view)
            self._apply_detail_host_height()
        for page in dict.values(self.mode_pages):
            if hasattr(page, "refresh_theme"):
                page.refresh_theme()


class AWP1744AWP1766Page(BasePage):
    page_name = "awp1744_awp1766"

    ADDR_OPTIONS = [
        ("Pulldown 0x74", 0x74),
        ("Pullup 0x75", 0x75),
        ("Floating 0x76", 0x76),
    ]

    def __init__(self, parent: tk.Misc, ctx: Any) -> None:
        super().__init__(parent, ctx)
        self.addr_var = tk.StringVar(value=self.ADDR_OPTIONS[0][0])
        self.reg_addr_var = tk.StringVar(value="0x01")
        self.write_value_var = tk.StringVar(value="0x0000")
        self.read_value_var = tk.StringVar(value="")
        self.current_mode = "awp1744_awp1766_pump_mode"
        self.mode_buttons: dict[str, ttk.Button] = {}
        self.mode_pages: dict[str, ttk.Frame] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        self.enable_pinned_footer_layout()

        header = self.make_title(self, "awp1744_awp1766")
        header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())

        self.mode_scroll, self.mode_container = self.make_vertical_scroll_area(self)
        self.mode_scroll.grid(row=1, column=0, sticky="nsew")
        self.mode_container.configure(padding=(8, 0, 8, 0))

        footer, actions, self.reg_panel, identity = self.make_merged_footer()
        footer.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        import_btn = ttk.Button(actions, style="Secondary.TButton", command=self.import_user_preset)
        import_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.mode_buttons["awp1744_awp1766_import_user_preset"] = import_btn
        export_btn = ttk.Button(actions, style="Secondary.TButton", command=self.export_user_preset)
        export_btn.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.mode_buttons["awp1744_awp1766_export_user_preset"] = export_btn
        actions.columnconfigure(0, weight=1, uniform="chip_state_actions")
        actions.columnconfigure(1, weight=1, uniform="chip_state_actions")
        self._build_register_bar(self.reg_panel)
        serial_group = identity.add_group()
        self.make_serial_selector(serial_group, page_name=self.page_name, row=0, column=0)
        addr_group = identity.add_group()
        self.addr_label = ttk.Label(addr_group, style="Panel.TLabel")
        self.addr_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.addr_combo = ttk.Combobox(
            addr_group,
            textvariable=self.addr_var,
            values=[label for label, _addr in self.ADDR_OPTIONS],
            state="readonly",
            width=18,
        )
        self.addr_combo.grid(row=0, column=1, sticky="w")
        self.addr_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_addr_changed())
        self.mode_container.rowconfigure(0, weight=1)
        self.mode_container.columnconfigure(0, weight=1)

        self.mode_pages["awp1744_awp1766_pump_mode"] = AWP1744AWP1766PumpModePage(self.mode_container, self.ctx)
        self.mode_pages["awp1744_awp1766_fan_mode"] = ReservedModePage(
            self.mode_container,
            self.ctx,
            "awp1744_awp1766_fan_mode",
        )

        for page in self.mode_pages.values():
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()

        self.refresh_language()
        self._on_addr_changed()
        self.show_mode(self.current_mode)

    def _build_register_bar(self, parent: tk.Misc) -> None:
        title_group = parent.add_group()
        self.reg_title_label = ttk.Label(title_group, style="Panel.TLabel")
        self.reg_title_label.grid(row=0, column=0, sticky="w")

        addr_group = parent.add_group()
        self.reg_addr_label = ttk.Label(addr_group, style="Panel.TLabel")
        self.reg_addr_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(addr_group, textvariable=self.reg_addr_var, width=8).grid(row=0, column=1, sticky="w")

        write_group = parent.add_group()
        self.write_value_label = ttk.Label(write_group, style="Panel.TLabel")
        self.write_value_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(write_group, textvariable=self.write_value_var, width=10).grid(row=0, column=1, sticky="w")

        read_group = parent.add_group()
        self.read_value_label = ttk.Label(read_group, style="Panel.TLabel")
        self.read_value_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(read_group, textvariable=self.read_value_var, width=12, state="readonly").grid(row=0, column=1, sticky="w")

        button_group = parent.add_group()
        self.rr_btn = ttk.Button(button_group, style="Secondary.TButton", command=self.read_register)
        self.rr_btn.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.rw_btn = ttk.Button(button_group, style="Primary.TButton", command=self.write_register)
        self.rw_btn.grid(row=0, column=1, sticky="w")

    def _parse_int(self, text: str) -> int:
        return int(text.strip(), 0)

    def _device_addr(self) -> int:
        selected = self.addr_var.get()
        for label, addr in self.ADDR_OPTIONS:
            if label == selected:
                return addr
        return 0x74

    def _on_addr_changed(self) -> None:
        addr = self._device_addr()
        self.ctx.state.extra["awp1744_awp1766_device_addr"] = addr

    def _set_addr_from_value(self, addr: int) -> None:
        value = int(addr) & 0xFF
        for label, option_addr in self.ADDR_OPTIONS:
            if option_addr == value:
                self.addr_var.set(label)
                self._on_addr_changed()
                return
        self.ctx.state.extra["awp1744_awp1766_device_addr"] = value

    def _ensure_connected(self) -> bool:
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return False
        return True

    def read_register(self) -> None:
        if not self._ensure_connected():
            return
        try:
            reg = self._parse_int(self.reg_addr_var.get())
            address = self._device_addr()
            resp = self.ctx.serial.read_register_value(address, reg, bits=16)
            if resp.ok:
                value = self.ctx.serial.extract_rdataw_value(
                    resp.data,
                    device_addr=address,
                    reg_addr=reg,
                    bits=16,
                )
                self.read_value_var.set(resp.data if value is None else f"0x{value & 0xFFFF:04X}")
            else:
                messagebox.showerror(self.tr("error"), resp.error)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def write_register(self) -> None:
        if not self._ensure_connected():
            return
        try:
            reg = self._parse_int(self.reg_addr_var.get())
            value = self._parse_int(self.write_value_var.get())
            resp = self.ctx.serial.write_register_value(self._device_addr(), reg, value, bits=16)
            if not resp.ok:
                messagebox.showerror(self.tr("error"), resp.error)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def export_user_preset(self) -> None:
        page = self.mode_pages.get(self.current_mode)
        if page is None or not hasattr(page, "export_user_preset_payload"):
            messagebox.showerror(self.tr("error"), self.tr("preset_current_mode_unavailable"))
            return
        try:
            payload = page.export_user_preset_payload()
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return

        path = filedialog.asksaveasfilename(
            title=self.tr("awp1744_awp1766_export_user_preset"),
            defaultextension=".json",
            initialfile="awp1744_awp1766_user_preset.json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        messagebox.showinfo(self.tr("info"), self.tr("user_preset_export_success"))

    def import_user_preset(self) -> None:
        path = filedialog.askopenfilename(
            title=self.tr("awp1744_awp1766_import_user_preset"),
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            payload = validate_awp1744_awp1766_pump_user_preset_payload(payload)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return
        if not self._ensure_connected():
            return
        self._set_addr_from_value(int(str(payload["chip_address"]), 0))
        self.show_mode(payload["mode"])
        page = self.mode_pages.get(payload["mode"])
        if page is None or not hasattr(page, "import_user_preset_payload"):
            messagebox.showerror(self.tr("error"), self.tr("preset_current_mode_unavailable"))
            return
        if not page.import_user_preset_payload(payload):
            return
        messagebox.showinfo(self.tr("info"), self.tr("user_preset_import_success"))

    def on_show(self) -> None:
        self._on_addr_changed()
        page = self.mode_pages.get(self.current_mode)
        if page is not None and hasattr(page, "on_show"):
            page.on_show()

    def show_mode(self, mode: str) -> None:
        if mode not in self.mode_pages:
            return
        old_page = self.mode_pages.get(self.current_mode)
        if old_page is not None and old_page is not self.mode_pages[mode] and hasattr(old_page, "stop_frequency_read"):
            old_page.stop_frequency_read()
        self.current_mode = mode
        self._sync_mode_button_styles()
        for page in self.mode_pages.values():
            page.grid_remove()
        page = self.mode_pages[mode]
        page.grid()
        if hasattr(page, "on_show"):
            page.on_show()

    def refresh_language(self) -> None:
        super().refresh_language()
        self.addr_label.configure(text=self.tr("current_chip_addr"))
        self.reg_title_label.configure(text=self.tr("read_write_registers"))
        self.reg_addr_label.configure(text=self.tr("reg_addr"))
        self.write_value_label.configure(text=self.tr("write_value"))
        self.read_value_label.configure(text=self.tr("read_value"))
        self.rr_btn.configure(text=self.tr("rr"))
        self.rw_btn.configure(text=self.tr("rw"))
        for key, btn in self.mode_buttons.items():
            btn.configure(text=self.tr(key))
        for page in self.mode_pages.values():
            if hasattr(page, "refresh_language"):
                page.refresh_language()

    def refresh_theme(self) -> None:
        super().refresh_theme()
        for page in self.mode_pages.values():
            if hasattr(page, "refresh_theme"):
                page.refresh_theme()


class AWP1744AWP1766PumpModePage(BasePage):
    page_name = "awp1744_awp1766_pump_mode"

    def __init__(self, parent: tk.Misc, ctx: Any) -> None:
        super().__init__(parent, ctx)
        self.pump_state_var = tk.StringVar(value="")
        self.amp_var = tk.StringVar(value=self._amp_labels()[-1])
        self.actual_voltage_var = tk.StringVar(value="")
        self.frequency_var = tk.StringVar(value="-- Hz")
        self.freq_status_var = tk.StringVar(value="")
        self._labels: dict[str, ttk.Label] = {}
        self._buttons: dict[str, ttk.Button] = {}
        self._pump_enabled: bool | None = None
        self._freq_polling = False
        self._freq_poll_job: str | None = None
        self._freq_request_running = False
        self._build_ui()
        self._update_actual_voltage()
        self.ctx.serial.add_log_listener(self._handle_frequency_log)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = self.make_title(self, "awp1744_awp1766_pump_mode", show_logo=False)
        header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())

        panel = ttk.Frame(self, style="Panel.TFrame", padding=18)
        panel.grid(row=1, column=0, sticky="nsew")
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(3, weight=1)
        panel.columnconfigure(4, weight=1)
        panel.rowconfigure(6, weight=1)

        self.make_module_title_row(
            panel,
            "pump_output_settings",
            step=1,
            step_total=3,
            help_key="awp1744_help_output",
            row=0,
            columnspan=5,
        )

        state_label = ttk.Label(panel, style="Panel.TLabel")
        state_label.grid(row=3, column=0, sticky="w", padx=(0, 10), pady=8)
        self._labels["awp1744_awp1766_pump_switch"] = state_label

        switch_frame = ttk.Frame(panel, style="Panel.TFrame")
        switch_frame.grid(row=3, column=1, columnspan=3, sticky="w", pady=8)

        open_btn = ttk.Button(switch_frame, style="Secondary.TButton", command=lambda: self.set_pump(True))
        open_btn.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._buttons["awp1744_awp1766_pump_open"] = open_btn

        close_btn = ttk.Button(switch_frame, style="Secondary.TButton", command=lambda: self.set_pump(False))
        close_btn.grid(row=0, column=1, sticky="w")
        self._buttons["awp1744_awp1766_pump_close"] = close_btn

        gpio2_label = ttk.Label(switch_frame, style="Muted.TLabel")
        gpio2_label.grid(row=0, column=2, sticky="w", padx=(18, 0))
        self._labels["awp1744_awp1766_use_gpio2"] = gpio2_label

        amp_label = ttk.Label(panel, style="Panel.TLabel")
        amp_label.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=8)
        self._labels["amplitude"] = amp_label

        amp_combo = ttk.Combobox(panel, textvariable=self.amp_var, values=self._amp_labels(), state="readonly")
        amp_combo.grid(row=1, column=1, sticky="ew", pady=8)
        amp_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_amp_changed())

        actual_label = ttk.Label(panel, style="Panel.TLabel")
        actual_label.grid(row=1, column=2, sticky="w", padx=(18, 10), pady=8)
        self._labels["awp1744_awp1766_actual_voltage"] = actual_label

        ttk.Label(panel, textvariable=self.actual_voltage_var, style="Panel.TLabel").grid(
            row=1,
            column=3,
            sticky="w",
            pady=8,
        )

        self.make_module_title_row(
            panel,
            "awp1744_pump_switch_title",
            step=2,
            step_total=3,
            help_key="awp1744_help_pump",
            row=2,
            columnspan=5,
            pady=(8, 0),
        )

        self.make_module_title_row(
            panel,
            "awp1744_frequency_reading_title",
            step=3,
            step_total=3,
            help_key="awp1744_help_frequency",
            row=4,
            columnspan=5,
            pady=(8, 0),
        )

        freq_label = ttk.Label(panel, style="Panel.TLabel")
        freq_label.grid(row=5, column=0, sticky="w", padx=(0, 10), pady=8)
        self._labels["chip_frequency"] = freq_label

        ttk.Label(panel, textvariable=self.frequency_var, style="Panel.TLabel").grid(
            row=5,
            column=1,
            sticky="w",
            pady=8,
        )

        gpio3_label = ttk.Label(panel, style="Muted.TLabel")
        gpio3_label.grid(row=5, column=2, sticky="w", padx=(18, 10), pady=8)
        self._labels["awp1744_awp1766_use_gpio3"] = gpio3_label

        freq_btn = ttk.Button(panel, style="Primary.TButton", command=self.start_frequency_read)
        freq_btn.grid(row=5, column=3, sticky="w", padx=(18, 10), pady=8)
        self._buttons["awp1744_awp1766_read_frequency"] = freq_btn

        ttk.Label(panel, textvariable=self.freq_status_var, style="Muted.TLabel").grid(
            row=5,
            column=4,
            sticky="w",
            pady=8,
        )

        log_frame = self.make_serial_log(panel, height=12, subscribe=True)
        log_frame.grid(row=6, column=0, columnspan=5, sticky="nsew", pady=(18, 0))
        self._register_module_spacing_widget(log_frame, before=True, after=False)

        self.refresh_language()
        self._refresh_pump_buttons()

    def _amp_labels(self) -> list[str]:
        return [f"0x{code:02X}  +/-{voltage:.3f} V" for code, voltage in enumerate(AWP1744_AWP1766_VOLTAGE_TABLE)]

    def _selected_amp_code(self) -> int:
        label = self.amp_var.get().strip()
        match = re.search(r"0x([0-9A-Fa-f]{1,2})", label)
        if match:
            return int(match.group(1), 16) & 0x1F
        return 0x1F

    def _amp_label_for_code(self, code: int) -> str:
        code = max(0, min(len(AWP1744_AWP1766_VOLTAGE_TABLE) - 1, int(code)))
        return self._amp_labels()[code]

    def _device_addr(self) -> int:
        return int(self.ctx.state.extra.get("awp1744_awp1766_device_addr", 0x74)) & 0xFF

    def _ensure_connected(self, *, show_error: bool = True) -> bool:
        if not self.ctx.serial.is_connected:
            if show_error:
                messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return False
        return True

    def _read_config_reg(self, show_error: bool = True) -> int | None:
        if not self._ensure_connected(show_error=show_error):
            return None
        address = self._device_addr()
        resp = self.ctx.serial.read_register_value(address, 0x01, bits=16)
        if not resp.ok:
            if show_error:
                messagebox.showerror(self.tr("error"), resp.error)
            return None
        value = self.ctx.serial.extract_rdataw_value(
            resp.data,
            device_addr=address,
            reg_addr=0x01,
            bits=16,
        )
        if value is None:
            if show_error:
                messagebox.showerror(self.tr("error"), resp.data or self.tr("invalid_input"))
            return None
        return value & 0xFFFF

    def _write_config_reg(self, value: int, show_error: bool = True) -> bool:
        if not self._ensure_connected(show_error=show_error):
            return False
        resp = self.ctx.serial.write_register_value(
            self._device_addr(),
            0x01,
            value & 0xFFFF,
            bits=16,
        )
        if not resp.ok:
            if show_error:
                messagebox.showerror(self.tr("error"), resp.error)
            return False
        return True

    def _update_config_bits(self, mask: int, bits: int, show_error: bool = True) -> bool:
        current = self._read_config_reg(show_error=show_error)
        if current is None:
            return False
        next_value = (current & ~mask) | (bits & mask)
        return self._write_config_reg(next_value, show_error=show_error)

    def set_pump(self, enabled: bool) -> None:
        if not self._ensure_connected():
            return
        mode = "HIGH" if enabled else "LOW"
        resp = self.ctx.serial.configure_gpio("A2", mode)
        if not resp.ok:
            messagebox.showerror(self.tr("error"), resp.error)
            return
        self._pump_enabled = enabled
        self.pump_state_var.set(self.tr("awp1744_awp1766_pump_open" if enabled else "awp1744_awp1766_pump_close"))
        self._refresh_pump_buttons()

    def _refresh_pump_buttons(self) -> None:
        open_btn = self._buttons.get("awp1744_awp1766_pump_open")
        close_btn = self._buttons.get("awp1744_awp1766_pump_close")
        if open_btn is not None:
            open_btn.configure(style="Selected.TButton" if self._pump_enabled is True else "Secondary.TButton")
        if close_btn is not None:
            close_btn.configure(style="Selected.TButton" if self._pump_enabled is False else "Secondary.TButton")

    def _update_actual_voltage(self) -> None:
        code = self._selected_amp_code()
        voltage = AWP1744_AWP1766_VOLTAGE_TABLE[code]
        self.actual_voltage_var.set(f"+/-{voltage:.3f} V")

    def _on_amp_changed(self) -> None:
        self._update_actual_voltage()
        code = self._selected_amp_code()
        self._update_config_bits(0x001F, code, show_error=True)

    def export_user_preset_payload(self) -> dict[str, Any]:
        return build_awp1744_awp1766_pump_user_preset_payload(
            device_addr=self._device_addr(),
            amp_code=self._selected_amp_code(),
            pump_enabled=self._pump_enabled,
        )

    def import_user_preset_payload(self, payload: dict[str, Any]) -> bool:
        payload = validate_awp1744_awp1766_pump_user_preset_payload(payload)
        settings = payload["settings"]
        self.amp_var.set(self._amp_label_for_code(settings["amp_code"]))
        self._update_actual_voltage()
        if not self._update_config_bits(0x001F, settings["amp_code"], show_error=True):
            return False
        if settings["pump_enabled"] is not None:
            self.set_pump(settings["pump_enabled"])
        return True

    def start_frequency_read(self) -> None:
        if not self._ensure_connected():
            return
        if not self._update_config_bits(0x0020, 0x0020, show_error=True):
            return
        if self._freq_polling:
            self.freq_status_var.set(self.tr("awp1744_awp1766_frequency_polling"))
            return
        self._freq_polling = True
        self.freq_status_var.set(self.tr("awp1744_awp1766_frequency_polling"))
        self._poll_frequency_once()

    def stop_frequency_read(self) -> None:
        self._freq_polling = False
        if self._freq_poll_job is not None:
            try:
                self.after_cancel(self._freq_poll_job)
            except Exception:
                pass
            self._freq_poll_job = None

    def _poll_frequency_once(self) -> None:
        if not self._freq_polling:
            return
        if not self.ctx.serial.is_connected:
            self._freq_polling = False
            self.freq_status_var.set(self.tr("not_connected"))
            return
        if not self._freq_request_running:
            self._freq_request_running = True
            thread = threading.Thread(target=self._frequency_request_worker, daemon=True)
            thread.start()
        self._freq_poll_job = self.after(1000, self._poll_frequency_once)

    def _frequency_request_worker(self) -> None:
        reader = getattr(self.ctx.serial, "read_frequency", None)
        resp = reader() if callable(reader) else self.ctx.serial.send_command("FREQ?", expect_response=True)
        try:
            self.after(0, self._finish_frequency_request, resp.ok, resp.data, resp.error)
        except Exception:
            pass

    def _finish_frequency_request(self, ok: bool, data: str, error: str) -> None:
        self._freq_request_running = False
        if not ok:
            self.freq_status_var.set(error)
            return
        if not self._set_frequency_from_text(data):
            self.freq_status_var.set(data)

    def _handle_frequency_log(self, line: str) -> None:
        parsed = self._parse_frequency_text(line)
        if parsed is None:
            return
        period_text, hz_text = parsed
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, self._set_frequency_reading, period_text, hz_text)
            except Exception:
                pass
            return
        self._set_frequency_reading(period_text, hz_text)

    def _set_frequency_from_text(self, text: str) -> bool:
        parsed = self._parse_frequency_text(text)
        if parsed is None:
            return False
        self._set_frequency_reading(*parsed)
        return True

    def _parse_frequency_text(self, text: str) -> tuple[str, str] | None:
        match = re.search(r"\^?FREQ\s+D8\s+([1-9][0-9]*)\s*~?", text, re.IGNORECASE)
        if not match:
            return None
        period_us = int(match.group(1))
        hz_text = f"{(1_000_000.0 / period_us):.2f}"
        return str(period_us), hz_text

    def _set_frequency_reading(self, period_text: str, hz_text: str) -> None:
        self.frequency_var.set(f"{hz_text} Hz")
        self.freq_status_var.set(f"{period_text} us")

    def refresh_language(self) -> None:
        super().refresh_language()
        for key, label in self._labels.items():
            label.configure(text=self.tr(key))
        for key, btn in self._buttons.items():
            btn.configure(text=self.tr(key))
        if self._pump_enabled is not None:
            self.pump_state_var.set(self.tr("awp1744_awp1766_pump_open" if self._pump_enabled else "awp1744_awp1766_pump_close"))


class ReservedModePage(BasePage):
    page_name = "reserved_mode"

    def __init__(self, parent: tk.Misc, ctx: Any, title_key: str) -> None:
        super().__init__(parent, ctx)
        self.title_key = title_key
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        header = self.make_title(self, self.title_key, show_logo=False)
        header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())
        self.title_label = header.grid_slaves(row=0, column=0)[0]
        panel = ttk.Frame(self, style="Panel.TFrame", padding=18)
        panel.grid(row=1, column=0, sticky="nsew")
        self.hint_label = ttk.Label(panel, style="Muted.TLabel", wraplength=620)
        self.hint_label.grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.refresh_language()

    def refresh_language(self) -> None:
        self.title_label.configure(text=self.tr(self.title_key))
        self.hint_label.configure(text=self.tr("placeholder_hint"))


class PlaceholderChipPage(BasePage):
    def __init__(self, parent: tk.Misc, ctx: Any, title_key: str) -> None:
        super().__init__(parent, ctx)
        self.title_key = title_key
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.header = self.make_title(self, self.title_key)
        self.header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())

        panel = ttk.Frame(self, style="Panel.TFrame", padding=18)
        panel.grid(row=1, column=0, sticky="nsew")
        self.title_label = ttk.Label(panel, style="SectionTitle.TLabel")
        self.title_label.grid(row=0, column=0, sticky="w")

        self.refresh_language()

    def refresh_language(self) -> None:
        super().refresh_language()
        self.title_label.configure(text=self.tr("placeholder_title"))
