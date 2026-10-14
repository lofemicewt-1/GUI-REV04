from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Full, Queue
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
from typing import Any

from app.navigation import Navigation
from app.state import AppState
from config.languages import t
from core.base_tool import (
    GPIO_ADC_PINS,
    GPIO_DETECT_PINS,
    GPIO_MODE_DETECT,
    GPIO_MODES,
    GPIO_PUBLIC_PINS,
    SerialResponse,
    is_serial_contention_error,
    parse_adc_read_response,
    parse_freq_response,
    parse_gpio_read_response,
)
from core import chip_status_cache
from core.chip_discovery import ChipDiscovery, ChipProbe, discover_first
from core.continuous_register_read import (
    ContinuousReadOutcome,
    ContinuousReadResult,
    ContinuousReadSettings,
    parse_continuous_read_settings,
    run_continuous_register_reads,
)
from core.local_i2c_bridge import LocalI2cBridge
from core.serial_manager import SerialManager, SerialRouter
from pages.base_page import BasePage, VerticalScrollArea, fit_toplevel_to_screen
from pages.awp1018_page import AWP1018Page
from pages.awp1221_page import AWP1221Page
from pages.awp1922_page import AWP1922Page
from pages.chip_pages import AWP1921AWP1923Page
from styles.theme import apply_theme


@dataclass
class ApplicationContext:
    state: AppState
    serial: SerialManager | SerialRouter
    colors: dict[str, str]
    app: Any


@dataclass
class _StatusOperation:
    """One serial status transaction and its private bounded result channel."""

    kind: str
    port: str
    session: int
    token: int
    results: Queue[tuple[Any, ...]] = field(default_factory=lambda: Queue(maxsize=1))
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)


class _StatusOperationCancelled(Exception):
    """Raised inside a worker when its pure-Python cancellation token is set."""


@dataclass
class _ContinuousReadOperation:
    """One fixed-session register read run and its bounded result channel."""

    token: int
    port: str
    transport: Any
    serial_handle: Any
    settings: ContinuousReadSettings
    dialog: Any
    results: Queue[ContinuousReadResult] = field(default_factory=lambda: Queue(maxsize=256))
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    outcome: ContinuousReadOutcome | None = None
    thread: threading.Thread | None = None
    dialog_closed: bool = False
    cancel_reason: str = ""


class HomePage(BasePage):
    page_name = "home"
    BAUDRATES = (9600, 57600, 115200, 230400, 460800, 921600)
    I2C_SPEEDS = (100000, 400000)
    REGISTER_WIDTHS = (8, 16)
    PORT_LIST_POLL_INTERVAL_MS = 30000
    STATUS_RESULT_CHECK_INTERVAL_MS = 25
    PUSH_EVENT_POLL_INTERVAL_MS = 25
    CONTINUOUS_READ_RESULT_CHECK_INTERVAL_MS = 25
    CONTINUOUS_READ_RESULT_QUEUE_SIZE = 256
    CONTINUOUS_READ_MAX_DISPLAY_ROWS = 500
    PUSH_EVENT_DEFER_TIMEOUT_S = 5.0
    STATUS_MAX_TARGETS = chip_status_cache.SLOT_COUNT
    STATUS_TYPE_VALUES = (chip_status_cache.CHIP_TYPE_192, chip_status_cache.CHIP_TYPE_1221, "1018")
    STATUS_ADDRESS_VALUES = {
        chip_status_cache.CHIP_TYPE_192: tuple(range(0x44, 0x47)),
        chip_status_cache.CHIP_TYPE_1221: tuple(range(0x50, 0x58)),
        "1018": tuple(range(0x48, 0x50)),
    }

    def __init__(self, parent: tk.Misc, ctx: ApplicationContext) -> None:
        super().__init__(parent, ctx)
        self.port_names: dict[str, str] = {}
        self._status_after_id: str | None = None
        self._status_after_port: str | None = None
        self._status_generation = 0
        self._status_inflight = False
        self._status_display_port: str | None = None
        # Kept only for direct unit fixtures that call a worker method without
        # starting an operation.  Live work uses each operation's private
        # maxsize=1 queue.
        self._status_results: Queue[tuple[Any, ...]] = Queue(maxsize=1)
        self._status_operation: _StatusOperation | None = None
        self._status_session_by_port: dict[str, int] = {}
        self._status_next_generation_by_port: dict[str, int] = {}
        self._status_state_by_port: dict[str, chip_status_cache.SnapshotState] = {}
        self._status_targets_by_port: dict[str, dict[int, chip_status_cache.TargetIdentity]] = {}
        self._status_capabilities_by_port: dict[str, chip_status_cache.CapabilityReply] = {}
        self._status_legacy_by_port: dict[str, bool] = {}
        self._status_errors_by_port: dict[str, str] = {}
        self._status_pending: dict[int, tuple[str, int, chip_status_cache.TargetIdentity, bool]] = {}
        self._status_row_vars: list[dict[str, Any]] = []
        # FLAG3 push events are delivered by SerialRouter into a FIFO.  Tk
        # drains that local queue; no callback below performs serial I/O.
        self._push_event_after_id: str | None = None
        self._push_event_pending_by_port: dict[str, list[tuple[Any, chip_status_cache.Snapshot]]] = {}
        self._push_event_pending_since_by_port: dict[str, float] = {}
        self._push_cap_attempted_by_port: set[str] = set()
        self._status_last_acked_irq_by_port: dict[str, int] = {}
        self._continuous_read_dialog: ContinuousRegisterReadDialog | None = None
        self._continuous_read_operation: _ContinuousReadOperation | None = None
        self._continuous_read_after_id: str | None = None
        self._continuous_read_next_token = 0
        # Status updates are event-owned (CAP FLAG3 + EVSTAT push); there is
        # no STAT?/FLAG polling path.
        self._serial_disconnect_error_by_port: dict[str, str] = {}
        self._port_list_poll_after_id: str | None = None
        self._port_scan_results: Queue[tuple[Any, str]] | None = None
        self._port_scan_after_id: str | None = None
        self._port_scan_closed = False
        self._serial_connection_listener = self._on_serial_connection_lost
        add_connection_listener = getattr(self.ctx.serial, "add_connection_listener", None)
        if callable(add_connection_listener):
            add_connection_listener(self._serial_connection_listener)
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, style="App.TFrame")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(0, weight=1)
        self._header_band = top

        header = self.make_title(top, "home", "@home_subtitle")
        header.grid(row=0, column=0, sticky="ew")
        self._page_header = header

        panel = ttk.Frame(self, style="Panel.TFrame", padding=18)
        panel.grid(row=1, column=0, sticky="nsew")
        self.main_panel = panel
        panel.columnconfigure(0, weight=1)

        main_title = ttk.Label(panel, style="SectionTitle.TLabel")
        main_title.grid(row=0, column=0, sticky="w", pady=(0, 12))
        self.bind_text(main_title, "connect_board_title")

        self.status_label = ttk.Label(panel, style="Panel.TLabel")
        self.status_label.grid(row=1, column=0, sticky="w", pady=(0, 12))

        self.port_hint_label = ttk.Label(panel, style="Muted.TLabel")
        self.port_hint_label.grid(row=2, column=0, sticky="w", pady=(0, 10))

        toolbar = ttk.Frame(panel, style="Panel.TFrame")
        toolbar.grid(row=3, column=0, sticky="ew")
        toolbar.columnconfigure(7, weight=1)

        self.port_label = ttk.Label(toolbar, style="Panel.TLabel")
        self.port_label.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(toolbar, textvariable=self.port_var, values=[], width=22)
        self.port_combo.grid(row=0, column=1, sticky="w")
        self.port_combo.bind("<<ComboboxSelected>>", self._on_status_port_changed)

        self.baud_label = ttk.Label(toolbar, style="Panel.TLabel")
        self.baud_label.grid(row=0, column=2, sticky="w", padx=(14, 8))

        self.baud_var = tk.StringVar(value=str(self.ctx.state.baudrate))
        self.baud_combo = ttk.Combobox(
            toolbar,
            textvariable=self.baud_var,
            values=[str(value) for value in self.BAUDRATES],
            state="readonly",
            width=10,
        )
        self.baud_combo.grid(row=0, column=3, sticky="w")

        self.refresh_btn = ttk.Button(toolbar, style="Quiet.TButton", command=self.refresh_ports)
        self.refresh_btn.grid(row=0, column=4, padx=(8, 0))

        self.connect_btn = ttk.Button(toolbar, style="Primary.TButton", command=self.toggle_connection)
        self.connect_btn.grid(row=0, column=5, padx=(8, 0))

        self.current_port_name_label = ttk.Label(toolbar, style="Panel.TLabel")
        self.current_port_name_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))

        self.current_port_name_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.current_port_name_var, style="Muted.TLabel").grid(
            row=1, column=1, columnspan=6, sticky="w", pady=(10, 0)
        )

        self.i2c_label = ttk.Label(toolbar, style="Panel.TLabel")
        self.i2c_label.grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(10, 0))

        self.i2c_speed_var = tk.StringVar(value="400000")
        self.i2c_speed_combo = ttk.Combobox(
            toolbar,
            textvariable=self.i2c_speed_var,
            values=[str(value) for value in self.I2C_SPEEDS],
            state="readonly",
            width=10,
        )
        self.i2c_speed_combo.grid(row=2, column=1, sticky="w", pady=(10, 0))

        self.i2c_btn = ttk.Button(toolbar, style="Secondary.TButton", command=self.apply_i2c_speed)
        self.i2c_btn.grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(10, 0))

        rr_panel = ttk.Frame(panel, style="Panel.TFrame")
        rr_panel.grid(row=4, column=0, sticky="ew", pady=(18, 0))
        rr_panel.columnconfigure(7, weight=1)

        self.device_addr_label = ttk.Label(rr_panel, style="Panel.TLabel")
        self.device_addr_label.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.device_addr_var = tk.StringVar(value="0x44")
        ttk.Entry(rr_panel, textvariable=self.device_addr_var, width=8).grid(row=0, column=1, sticky="w", padx=(0, 16))

        self.reg_addr_label = ttk.Label(rr_panel, style="Panel.TLabel")
        self.reg_addr_label.grid(row=0, column=2, sticky="w", padx=(0, 8))

        self.reg_addr_var = tk.StringVar(value="0x00")
        ttk.Entry(rr_panel, textvariable=self.reg_addr_var, width=8).grid(row=0, column=3, sticky="w", padx=(0, 16))

        self.write_value_label = ttk.Label(rr_panel, style="Panel.TLabel")
        self.write_value_label.grid(row=0, column=4, sticky="w", padx=(0, 8))

        self.write_value_var = tk.StringVar(value="0x0000")
        ttk.Entry(rr_panel, textvariable=self.write_value_var, width=10).grid(row=0, column=5, sticky="w", padx=(0, 16))

        self.register_width_label = ttk.Label(rr_panel, style="Panel.TLabel")
        self.register_width_label.grid(row=0, column=6, sticky="w", padx=(0, 8))

        self.register_width_var = tk.StringVar(value="16-bit")
        self.register_width_combo = ttk.Combobox(
            rr_panel,
            textvariable=self.register_width_var,
            values=[f"{bits}-bit" for bits in self.REGISTER_WIDTHS],
            state="readonly",
            width=8,
        )
        self.register_width_combo.grid(row=0, column=7, sticky="w")

        self.read_value_label = ttk.Label(rr_panel, style="Panel.TLabel")
        self.read_value_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))

        self.read_value_var = tk.StringVar(value="")
        ttk.Entry(rr_panel, textvariable=self.read_value_var, width=14).grid(row=1, column=1, sticky="w", padx=(0, 16), pady=(10, 0))

        read_actions = ttk.Frame(rr_panel, style="Panel.TFrame")
        read_actions.grid(row=1, column=2, columnspan=6, sticky="w", pady=(10, 0))
        self.rr_btn = ttk.Button(read_actions, style="Secondary.TButton", command=self.read_register)
        self.rr_btn.grid(row=0, column=0, padx=(0, 8))

        self.rw_btn = ttk.Button(read_actions, style="Primary.TButton", command=self.write_register)
        self.rw_btn.grid(row=0, column=1, padx=(0, 8))

        self.continuous_read_btn = ttk.Button(
            read_actions,
            style="Secondary.TButton",
            command=self.open_continuous_read_dialog,
        )
        self.continuous_read_btn.grid(row=0, column=2)

        board_panel = ttk.Frame(panel, style="Panel.TFrame")
        self.board_tools_panel = board_panel
        self.board_tools_title = ttk.Label(board_panel, style="SectionTitle.TLabel")
        self.board_tools_title.grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 6))
        self.gpio_pin_label = ttk.Label(board_panel, style="Panel.TLabel")
        self.gpio_pin_label.grid(row=1, column=0, sticky="w", padx=(0, 8))
        self.gpio_pin_var = tk.StringVar(value="D2")
        self.gpio_pin_combo = ttk.Combobox(
            board_panel,
            textvariable=self.gpio_pin_var,
            values=list(GPIO_PUBLIC_PINS),
            state="readonly",
            width=6,
        )
        self.gpio_pin_combo.grid(row=1, column=1, sticky="w", padx=(0, 16))
        self.gpio_pin_combo.bind("<<ComboboxSelected>>", self._on_board_gpio_pin_selected)
        self.gpio_mode_label = ttk.Label(board_panel, style="Panel.TLabel")
        self.gpio_mode_label.grid(row=1, column=2, sticky="w", padx=(0, 8))
        self.gpio_mode_var = tk.StringVar(value="HIGH")
        self.gpio_mode_combo = ttk.Combobox(
            board_panel,
            textvariable=self.gpio_mode_var,
            values=list(GPIO_MODES),
            state="readonly",
            width=10,
        )
        self.gpio_mode_combo.grid(row=1, column=3, sticky="w", padx=(0, 8))
        self.gpio_cfg_btn = ttk.Button(
            board_panel,
            style="Secondary.TButton",
            command=self.apply_board_gpio_mode,
        )
        self.gpio_cfg_btn.grid(row=1, column=4, sticky="w", padx=(0, 8))
        self.gpio_read_btn = ttk.Button(
            board_panel,
            style="Secondary.TButton",
            command=self.read_board_gpio,
        )
        self.gpio_read_btn.grid(row=1, column=5, sticky="w", padx=(0, 8))
        self.gpio_value_var = tk.StringVar(value="")
        ttk.Entry(board_panel, textvariable=self.gpio_value_var, width=12, state="readonly").grid(
            row=1, column=6, sticky="w"
        )

        self.adc_pin_label = ttk.Label(board_panel, style="Panel.TLabel")
        self.adc_pin_label.grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        self.adc_pin_var = tk.StringVar(value="A1")
        self.adc_pin_combo = ttk.Combobox(
            board_panel,
            textvariable=self.adc_pin_var,
            values=list(GPIO_ADC_PINS),
            state="readonly",
            width=6,
        )
        self.adc_pin_combo.grid(row=2, column=1, sticky="w", padx=(0, 16), pady=(10, 0))
        self.adc_read_btn = ttk.Button(
            board_panel,
            style="Secondary.TButton",
            command=self.read_board_adc,
        )
        self.adc_read_btn.grid(row=2, column=2, sticky="w", padx=(0, 8), pady=(10, 0))
        self.adc_value_var = tk.StringVar(value="")
        ttk.Entry(board_panel, textvariable=self.adc_value_var, width=12, state="readonly").grid(
            row=2, column=3, sticky="w", pady=(10, 0)
        )
        self.freq_btn = ttk.Button(
            board_panel,
            style="Secondary.TButton",
            command=self.read_board_frequency,
        )
        self.freq_btn.grid(row=2, column=4, sticky="w", padx=(0, 8), pady=(10, 0))
        self.freq_value_var = tk.StringVar(value="")
        ttk.Entry(board_panel, textvariable=self.freq_value_var, width=16, state="readonly").grid(
            row=2, column=5, columnspan=2, sticky="w", pady=(10, 0)
        )

        self._build_status_panel(panel)
        self.refresh_ports()
        self.refresh_language()
        self._schedule_port_list_poll()

    def _build_status_panel(self, parent: tk.Misc) -> None:
        """Build the compact explicit status-cache target table on Home."""

        panel = ttk.Frame(parent, style="Panel.TFrame")
        self.status_cache_panel = panel
        panel.columnconfigure(5, weight=1)

        self.status_cache_title = ttk.Label(panel, style="SectionTitle.TLabel")
        self.status_cache_title.grid(row=0, column=0, columnspan=7, sticky="w", pady=(0, 6))
        self.bind_text(self.status_cache_title, "status_cache_title")
        self.status_cache_hint = ttk.Label(panel, style="Muted.TLabel")
        self.status_cache_hint.grid(row=1, column=0, columnspan=7, sticky="w", pady=(0, 8))
        self.bind_text(self.status_cache_hint, "status_cache_hint")

        headers = ("status_cache_slot", "status_cache_type", "status_cache_address", "status_cache_enabled", "status_cache_value")
        for column, key in enumerate(headers):
            label = ttk.Label(panel, style="Muted.TLabel")
            label.grid(row=2, column=column, sticky="w", padx=(0, 8))
            self.bind_text(label, key)

        for slot in range(self.STATUS_MAX_TARGETS):
            row = 3 + slot
            slot_label = ttk.Label(panel, text=f"{slot}", style="Panel.TLabel", width=4)
            slot_label.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            type_var = tk.StringVar(value=chip_status_cache.CHIP_TYPE_192)
            type_combo = ttk.Combobox(
                panel,
                textvariable=type_var,
                values=list(self.STATUS_TYPE_VALUES),
                state="readonly",
                width=8,
            )
            type_combo.grid(row=row, column=1, sticky="w", padx=(0, 8), pady=2)
            default_address = self.STATUS_ADDRESS_VALUES[chip_status_cache.CHIP_TYPE_192][slot]
            address_var = tk.StringVar(value=f"0x{default_address:02X}")
            address_combo = ttk.Combobox(
                panel,
                textvariable=address_var,
                values=[f"0x{value:02X}" for value in self.STATUS_ADDRESS_VALUES[chip_status_cache.CHIP_TYPE_192]],
                state="readonly",
                width=8,
            )
            address_combo.grid(row=row, column=2, sticky="w", padx=(0, 8), pady=2)
            enabled_var = tk.BooleanVar(value=False)
            enabled_check = ttk.Checkbutton(
                panel,
                variable=enabled_var,
                command=lambda target_slot=slot: self._toggle_status_target(target_slot),
            )
            enabled_check.grid(row=row, column=3, sticky="w", padx=(0, 8), pady=2)
            remove_btn = ttk.Button(
                panel,
                style="Secondary.TButton",
                command=lambda target_slot=slot: self._remove_status_target(target_slot),
            )
            remove_btn.grid(row=row, column=4, sticky="w", padx=(0, 8), pady=2)
            value_var = tk.StringVar(value="--")
            value_label = ttk.Label(panel, textvariable=value_var, style="Muted.TLabel")
            value_label.grid(row=row, column=5, columnspan=2, sticky="w", pady=2)
            type_combo.bind(
                "<<ComboboxSelected>>",
                lambda _event, target_slot=slot: self._status_type_changed(target_slot),
            )
            address_combo.bind(
                "<<ComboboxSelected>>",
                lambda _event, target_slot=slot: self._status_address_changed(target_slot),
            )
            self._status_row_vars.append(
                {
                    "type": type_var,
                    "type_combo": type_combo,
                    "address": address_var,
                    "address_combo": address_combo,
                    "committed_type": chip_status_cache.CHIP_TYPE_192,
                    "committed_address": f"0x{default_address:02X}",
                    "enabled": enabled_var,
                    "enabled_check": enabled_check,
                    "remove": remove_btn,
                    "value": value_var,
                }
            )

    def _status_row(self, slot: int) -> dict[str, Any] | None:
        rows = getattr(self, "_status_row_vars", [])
        if 0 <= slot < len(rows):
            return rows[slot]
        return None

    def _status_type_changed(self, slot: int) -> None:
        row = self._status_row(slot)
        if row is None:
            return
        active = getattr(self, "_status_operation", None)
        if active is not None:
            self._restore_status_row_identity(slot, row)
            self._set_status_row_text(slot, "status command busy")
            return
        chip_type = str(row["type"].get()).strip()
        values = self.STATUS_ADDRESS_VALUES.get(chip_type, ())
        row["address_combo"].configure(values=[f"0x{value:02X}" for value in values])
        if values and row["address"].get() not in {f"0x{value:02X}" for value in values}:
            row["address"].set(f"0x{values[0]:02X}")
        if bool(row["enabled"].get()):
            row["enabled"].set(False)
            self._remove_status_target(slot)
        else:
            row["committed_type"] = chip_type
            row["committed_address"] = row["address"].get()
        self._refresh_status_row(slot)

    def _status_address_changed(self, slot: int) -> None:
        row = self._status_row(slot)
        if row is None:
            return
        active = getattr(self, "_status_operation", None)
        if active is not None:
            self._restore_status_row_identity(slot, row)
            self._set_status_row_text(slot, "status command busy")
            return
        if bool(row["enabled"].get()):
            row["enabled"].set(False)
            self._remove_status_target(slot)
        else:
            row["committed_address"] = row["address"].get()
        self._refresh_status_row(slot)

    def _restore_status_row_identity(self, slot: int, row: dict[str, Any]) -> None:
        """Rollback a target edit while any status serial transaction is active."""

        chip_type = str(row.get("committed_type", row["type"].get())).strip()
        address = str(row.get("committed_address", row["address"].get())).strip()
        row["type"].set(chip_type)
        values = self.STATUS_ADDRESS_VALUES.get(chip_type, ())
        row["address_combo"].configure(values=[f"0x{value:02X}" for value in values])
        if address:
            row["address"].set(address)

    def _on_status_port_changed(self, _event: tk.Event | None = None) -> None:
        """Select one cached port without mixing its event identity."""

        current = self.port_var.get().strip()
        operation = getattr(self, "_continuous_read_operation", None)
        if operation is not None and operation.port != current:
            self._cancel_continuous_read("connection_changed", port=operation.port)
        self._stop_status_monitor()
        self._status_display_port = None
        if current and self._is_selected_port_connected(current):
            if current not in self._status_session_by_port:
                self._status_refresh_session(current)
            else:
                self._status_display_port = current
                self._refresh_status_rows()
        else:
            self._refresh_status_rows()
        self._update_current_port_name()

    def _stop_status_monitor(self) -> None:
        """Invalidate status workers and cancel the next Tk callback."""

        self._status_generation = getattr(self, "_status_generation", 0) + 1
        operation = getattr(self, "_status_operation", None)
        if operation is not None:
            operation.cancel_event.set()
        after_id = getattr(self, "_status_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except (tk.TclError, AttributeError):
                pass
        self._status_after_id = None
        self._status_after_port = None
        if operation is None or operation.done_event.is_set():
            self._status_operation = None
            self._status_inflight = False
        else:
            # Keep the transaction occupied until the worker exits.  Setting
            # this false here would let a replacement command interleave with
            # a still-running SESSION/TGT/MON sequence.
            self._status_inflight = True
            self._status_after_port = operation.port
            self._status_after_id = self.after(
                self.STATUS_RESULT_CHECK_INTERVAL_MS,
                self._reap_cancelled_status_operation,
                operation,
            )
        self._status_drop_results()

    def _reap_cancelled_status_operation(self, operation: _StatusOperation) -> None:
        """Release the serial-operation gate after a cancelled worker exits."""

        if self._status_operation is not operation:
            return
        if not operation.done_event.is_set():
            self._status_after_port = operation.port
            self._status_after_id = self.after(
                self.STATUS_RESULT_CHECK_INTERVAL_MS,
                self._reap_cancelled_status_operation,
                operation,
            )
            return
        self._status_operation = None
        self._status_inflight = False
        self._status_after_id = None
        self._status_after_port = None

    def _status_refresh_session(self, port: str) -> int:
        """Create a new board session boundary and drop old status records."""

        self._status_session_by_port = getattr(self, "_status_session_by_port", {})
        self._status_next_generation_by_port = getattr(self, "_status_next_generation_by_port", {})
        self._status_state_by_port = getattr(self, "_status_state_by_port", {})
        self._status_targets_by_port = getattr(self, "_status_targets_by_port", {})
        self._status_capabilities_by_port = getattr(self, "_status_capabilities_by_port", {})
        self._status_legacy_by_port = getattr(self, "_status_legacy_by_port", {})
        self._status_errors_by_port = getattr(self, "_status_errors_by_port", {})
        self._status_pending = getattr(self, "_status_pending", {})
        session = (time.monotonic_ns() ^ id(self) ^ sum(map(ord, str(port)))) & chip_status_cache.MAX_U32
        if session == 0:
            session = 1
        self._stop_status_monitor()
        self._status_session_by_port[port] = session
        self._status_next_generation_by_port[port] = 0
        self._status_state_by_port[port] = chip_status_cache.SnapshotState(session_id=session)
        self._status_targets_by_port[port] = {}
        self._status_legacy_by_port.pop(port, None)
        self._status_errors_by_port.pop(port, None)
        self._push_event_pending_by_port = getattr(self, "_push_event_pending_by_port", {})
        self._push_event_pending_by_port.pop(port, None)
        self._push_event_pending_since_by_port = getattr(self, "_push_event_pending_since_by_port", {})
        self._push_event_pending_since_by_port.pop(port, None)
        self._status_pending.clear()
        self._status_drop_results()
        self._status_display_port = port
        self.ctx.state.extra["status_session"] = f"{session:08X}"
        self._refresh_status_rows()
        return session

    def _status_invalidate_port(self, port: str, *, stop_monitor: bool = True) -> None:
        self._status_session_by_port = getattr(self, "_status_session_by_port", {})
        self._status_next_generation_by_port = getattr(self, "_status_next_generation_by_port", {})
        self._status_state_by_port = getattr(self, "_status_state_by_port", {})
        self._status_targets_by_port = getattr(self, "_status_targets_by_port", {})
        self._status_capabilities_by_port = getattr(self, "_status_capabilities_by_port", {})
        self._status_legacy_by_port = getattr(self, "_status_legacy_by_port", {})
        self._status_errors_by_port = getattr(self, "_status_errors_by_port", {})
        self._status_pending = getattr(self, "_status_pending", {})
        if stop_monitor:
            self._stop_status_monitor()
        self._status_session_by_port.pop(port, None)
        self._status_next_generation_by_port.pop(port, None)
        self._status_state_by_port.pop(port, None)
        self._status_targets_by_port.pop(port, None)
        self._status_capabilities_by_port.pop(port, None)
        self._push_cap_attempted_by_port = getattr(self, "_push_cap_attempted_by_port", set())
        self._push_cap_attempted_by_port.discard(port)
        self._push_event_pending_by_port = getattr(self, "_push_event_pending_by_port", {})
        self._push_event_pending_by_port.pop(port, None)
        self._push_event_pending_since_by_port = getattr(self, "_push_event_pending_since_by_port", {})
        self._push_event_pending_since_by_port.pop(port, None)
        self._status_legacy_by_port.pop(port, None)
        self._status_errors_by_port.pop(port, None)
        self._status_pending = {
            slot: binding
            for slot, binding in self._status_pending.items()
            if not isinstance(binding, tuple) or not binding or binding[0] != port
        }
        self._status_drop_results() if stop_monitor else self._status_drop_results(port)
        display_port = getattr(self, "_status_display_port", None)
        if self.ctx.state.extra.get("status_session") and (
            display_port == port or (display_port is None and stop_monitor)
        ):
            self.ctx.state.extra.pop("status_session", None)
        if display_port == port:
            self._status_display_port = None
        self._refresh_status_rows()

    def _status_drop_results(self, port: str | None = None) -> None:
        queue = getattr(self, "_status_results", None)
        if queue is None:
            return
        retained: list[tuple[Any, ...]] = []
        while True:
            try:
                candidate = queue.get_nowait()
            except Empty:
                break
            if port is None:
                continue
            candidate_port = candidate[1] if isinstance(candidate, tuple) and len(candidate) > 1 else None
            if candidate_port != port:
                retained.append(candidate)
        for candidate in retained:
            try:
                queue.put_nowait(candidate)
            except Full:
                break

    def _status_next_generation(self, port: str) -> int:
        current = self._status_next_generation_by_port.get(port, 0)
        if current >= 0xFFFF:
            raise ValueError("status target generation exhausted; reconnect the board")
        current += 1
        self._status_next_generation_by_port[port] = current
        return current

    def _status_target_from_row(self, port: str, slot: int) -> chip_status_cache.TargetIdentity:
        row = self._status_row(slot)
        if row is None:
            raise ValueError(f"status slot {slot} is unavailable")
        chip_type = str(row["type"].get()).strip()
        if chip_type == "1018":
            raise ValueError("AWP1018 status monitoring is unsupported until its state contract is confirmed")
        if chip_type not in chip_status_cache.SUPPORTED_CHIP_TYPES:
            raise ValueError(f"unsupported status chip type: {chip_type}")
        address_text = str(row["address"].get()).strip()
        try:
            address = int(address_text, 0)
        except ValueError as exc:
            raise ValueError(f"invalid status address: {address_text}") from exc
        generation = self._status_next_generation(port)
        target = chip_status_cache.TargetIdentity(slot, chip_type, address, generation)
        target.validate()
        for other_slot, other in self._status_targets_by_port.get(port, {}).items():
            if other_slot != slot and other.address == target.address:
                raise ValueError(f"status address 0x{target.address:02X} is already used by slot {other_slot}")
        return target

    @staticmethod
    def _status_check_cancel(operation: _StatusOperation) -> None:
        """Check a worker-owned token without consulting Tk or page state."""

        if operation.cancel_event.is_set():
            raise _StatusOperationCancelled

    @staticmethod
    def _status_call_with_retry(
        serial: Any,
        method_name: str,
        *args: Any,
        cancel_event: threading.Event | None = None,
        **kwargs: Any,
    ) -> SerialResponse:
        method = getattr(serial, method_name, None)
        if not callable(method):
            return SerialResponse(False, error="status cache unsupported by this firmware")
        response = SerialResponse(False, error="status command did not return a response")
        for attempt in range(3):
            if cancel_event is not None and cancel_event.is_set():
                raise _StatusOperationCancelled
            try:
                response = method(*args, **kwargs)
            except Exception as exc:
                return SerialResponse(False, error=str(exc))
            if response.ok or not is_serial_contention_error(response.error):
                return response
            if attempt < 2:
                delay = 0.02 * (attempt + 1)
                if cancel_event is not None:
                    if cancel_event.wait(delay):
                        raise _StatusOperationCancelled
                else:
                    time.sleep(delay)
        return response

    @staticmethod
    def _status_parse(response: SerialResponse, parser: Any) -> Any:
        if not response.ok:
            raise chip_status_cache.FrameError(response.error or response.data or "status command failed")
        return parser(chip_status_cache.frame_body(response.data))

    def _status_queue_result(
        self,
        result: tuple[Any, ...],
        operation: _StatusOperation | None = None,
    ) -> None:
        queue = operation.results if operation is not None else getattr(self, "_status_results", None)
        if queue is None:
            return
        try:
            queue.put_nowait(result)
        except Full:
            try:
                queue.get_nowait()
            except Empty:
                pass
            try:
                queue.put_nowait(result)
            except Full:
                pass

    def _status_finish_operation(self, operation: _StatusOperation | None = None) -> None:
        """Release the serial-operation gate only for the matching operation."""

        if operation is None:
            self._status_inflight = False
            return
        if getattr(self, "_status_operation", None) is operation:
            self._status_operation = None
            self._status_inflight = False

    def _status_start_result_check(
        self,
        port: str,
        session: int,
        token: int,
        operation: _StatusOperation | None = None,
    ) -> None:
        callback_args: tuple[Any, ...] = (port, session, token)
        if operation is not None:
            callback_args += (operation,)
        self._status_after_port = port
        self._status_after_id = self.after(
            self.STATUS_RESULT_CHECK_INTERVAL_MS,
            self._consume_status_results,
            *callback_args,
        )

    def _status_is_current(
        self,
        port: str,
        session: int,
        token: int,
        operation: _StatusOperation | None = None,
    ) -> bool:
        selected_port = getattr(getattr(self, "port_var", None), "get", lambda: "")().strip()
        return (
            token == getattr(self, "_status_generation", 0)
            and self._status_session_by_port.get(port) == session
            and selected_port == port
            and self._is_selected_port_connected(port)
            and (operation is None or self._status_operation is operation)
        )

    def _toggle_status_target(self, slot: int) -> None:
        row = self._status_row(slot)
        if row is None:
            return
        enabled = bool(row["enabled"].get())
        if enabled:
            port = self.port_var.get().strip()
            if not port or not self._is_selected_port_connected(port):
                row["enabled"].set(False)
                self._set_status_row_text(slot, "-- (not connected)")
                return
            try:
                target = self._status_target_from_row(port, slot)
            except (TypeError, ValueError) as exc:
                row["enabled"].set(False)
                self._set_status_row_text(slot, str(exc))
                return
            session = self._status_session_by_port.get(port)
            if session is None:
                session = self._status_refresh_session(port)
            self._start_status_operation(port, session, slot, target, True)
            return

        port = self.port_var.get().strip()
        target = self._status_targets_by_port.get(port, {}).get(slot)
        if target is None:
            self._set_status_row_text(slot, "--")
            return
        session = self._status_session_by_port.get(port)
        if session is None:
            self._set_status_row_text(slot, "-- (session expired)")
            return
        self._start_status_operation(port, session, slot, target, False)

    def _remove_status_target(self, slot: int) -> None:
        """Disable and remove one explicit target after a user action."""

        row = self._status_row(slot)
        if row is not None:
            row["enabled"].set(False)
        port = getattr(getattr(self, "port_var", None), "get", lambda: "")().strip()
        target = self._status_targets_by_port.get(port, {}).get(slot)
        session = self._status_session_by_port.get(port)
        if target is None or session is None:
            self._set_status_row_text(slot, "--")
            return
        self._start_status_operation(port, session, slot, target, False)

    def _start_status_operation(
        self,
        port: str,
        session: int,
        slot: int,
        target: chip_status_cache.TargetIdentity,
        enabled: bool,
    ) -> None:
        active = getattr(self, "_status_operation", None)
        if active is not None:
            row = self._status_row(slot)
            if row is not None:
                row["enabled"].set(not enabled)
            self._set_status_row_text(slot, "status command busy")
            return
        if self._status_inflight:
            row = self._status_row(slot)
            if row is not None:
                row["enabled"].set(not enabled)
            self._set_status_row_text(slot, "status command busy")
            return
        chip_status_cache.StatusTargetBinding(port, session, target).validate()
        self._status_pending[slot] = (port, session, target, enabled)
        self._status_inflight = True
        token = self._status_generation
        operation = _StatusOperation("configure" if enabled else "clear", port, session, token)
        self._status_operation = operation
        target_worker = self._configure_status_target_worker if enabled else self._clear_status_target_worker
        threading.Thread(
            target=target_worker,
            args=(port, session, token, slot, target, operation),
            daemon=True,
        ).start()
        self._status_start_result_check(port, session, token, operation)
        self._set_status_row_text(slot, "pending")

    def _configure_status_target_worker(
        self,
        port: str,
        session: int,
        token: int,
        slot: int,
        target: chip_status_cache.TargetIdentity,
        operation: _StatusOperation | None = None,
    ) -> None:
        supplied_operation = operation
        operation = operation or _StatusOperation("configure", port, session, token)
        response: SerialResponse | None = None
        monitor_response: SerialResponse | None = None
        snapshot: chip_status_cache.Snapshot | None = None
        error = ""
        fallback = False
        cancelled = False
        try:
            serial = self._serial_for_port(port)
            if serial is None:
                raise RuntimeError("Selected serial port is not connected")
            capabilities = self._status_capabilities_by_port.get(port)
            if capabilities is None:
                self._status_check_cancel(operation)
                response = self._status_call_with_retry(
                    serial,
                    "read_status_capabilities",
                    cancel_event=operation.cancel_event,
                )
                if not response.ok:
                    fallback = "UNKNOWN_CMD" in (response.error or response.data).upper() or "unsupported" in (response.error or "").lower()
                    error = response.error or response.data or "status capability query failed"
                else:
                    capabilities = self._status_parse(response, chip_status_cache.parse_cap_frame)
                    if capabilities.slots < self.STATUS_MAX_TARGETS:
                        raise ValueError(f"firmware exposes only {capabilities.slots} status slots")
                    self._status_capabilities_by_port[port] = capabilities
            if not error and capabilities is not None:
                if capabilities.session_explicit:
                    self._status_check_cancel(operation)
                    response = self._status_call_with_retry(
                        serial,
                        "set_status_session",
                        session,
                        cancel_event=operation.cancel_event,
                    )
                    if not response.ok:
                        error = response.error or response.data or "status session failed"
                    else:
                        session_reply = self._status_parse(response, chip_status_cache.parse_session_frame)
                        if session_reply.session_id != session:
                            raise ValueError("status session acknowledgement mismatch")
                if not error:
                    self._status_check_cancel(operation)
                    response = self._status_call_with_retry(
                        serial,
                        "configure_status_target",
                        target.slot,
                        target.chip_type,
                        target.address,
                        target.generation,
                        cancel_event=operation.cancel_event,
                    )
                    if not response.ok:
                        error = response.error or response.data or "status target registration failed"
                if not error:
                    self._status_check_cancel(operation)
                    monitor_response = self._status_call_with_retry(
                        serial,
                        "set_status_monitor",
                        target.slot,
                        True,
                        chip_type=target.chip_type,
                        cancel_event=operation.cancel_event,
                    )
                    if not monitor_response.ok:
                        error = monitor_response.error or monitor_response.data or "status monitor enable failed"
        except _StatusOperationCancelled:
            cancelled = True
        except Exception as exc:
            error = str(exc)
        self._status_queue_result(
            (
                "configure",
                port,
                session,
                token,
                slot,
                target,
                response,
                monitor_response,
                snapshot,
                error,
                fallback,
                cancelled,
            ),
            supplied_operation,
        )
        operation.done_event.set()

    def _clear_status_target_worker(
        self,
        port: str,
        session: int,
        token: int,
        slot: int,
        target: chip_status_cache.TargetIdentity,
        operation: _StatusOperation | None = None,
    ) -> None:
        supplied_operation = operation
        operation = operation or _StatusOperation("clear", port, session, token)
        monitor_response: SerialResponse | None = None
        response: SerialResponse | None = None
        error = ""
        cancelled = False
        try:
            serial = self._serial_for_port(port)
            if serial is None:
                raise RuntimeError("Selected serial port is not connected")
            self._status_check_cancel(operation)
            monitor_response = self._status_call_with_retry(
                serial,
                "set_status_monitor",
                slot,
                False,
                chip_type=target.chip_type,
                cancel_event=operation.cancel_event,
            )
            if monitor_response.ok:
                self._status_check_cancel(operation)
                response = self._status_call_with_retry(
                    serial,
                    "clear_status_target",
                    slot,
                    target.generation,
                    cancel_event=operation.cancel_event,
                )
            if monitor_response is not None and not monitor_response.ok:
                error = monitor_response.error or monitor_response.data or "status monitor disable failed"
            elif response is not None and not response.ok:
                error = response.error or response.data or "status target removal failed"
        except _StatusOperationCancelled:
            cancelled = True
        except Exception as exc:
            error = str(exc)
        self._status_queue_result(
            ("clear", port, session, token, slot, target, monitor_response, response, error, cancelled),
            supplied_operation,
        )
        operation.done_event.set()

    def _consume_status_results(
        self,
        port: str,
        session: int,
        token: int,
        operation: _StatusOperation | None = None,
    ) -> None:
        self._status_after_id = None
        self._status_after_port = None
        active = operation or getattr(self, "_status_operation", None)
        if active is not None:
            if not self._status_is_current(port, session, token, active):
                # A stale callback must never drain a replacement operation's
                # queue.  Live operations have private queues; direct fixtures
                # retain the legacy queue path below.
                return
            result_queue = active.results
        else:
            if not self._status_is_current(port, session, token):
                return
            result_queue = self._status_results

        result: tuple[Any, ...] | None = None
        try:
            candidate = result_queue.get_nowait()
        except Empty:
            candidate = None
        if candidate is not None:
            if active is not None or (
                len(candidate) > 3
                and candidate[1] == port
                and candidate[2] == session
                and candidate[3] == token
            ):
                result = candidate
            else:
                # Keep an unrelated direct-fixture result available for its
                # matching callback rather than silently dropping it.
                try:
                    result_queue.put_nowait(candidate)
                except Full:
                    pass

        if result is None:
            if active is not None:
                if active.done_event.is_set():
                    self._status_finish_operation(active)
                else:
                    self._status_start_result_check(port, session, token, active)
            elif self._status_inflight:
                self._status_start_result_check(port, session, token)
            return

        kind = result[0]
        if kind == "configure":
            if len(result) >= 12:
                (
                    _kind,
                    _port,
                    _session,
                    _token,
                    slot,
                    target,
                    response,
                    monitor_response,
                    snapshot,
                    error,
                    fallback,
                    cancelled,
                ) = result[:12]
            else:
                (
                    _kind,
                    _port,
                    _session,
                    _token,
                    slot,
                    target,
                    response,
                    monitor_response,
                    snapshot,
                    error,
                    fallback,
                ) = result
                cancelled = False
            self._status_finish_operation(active)
            self._status_pending.pop(slot, None)
            if cancelled:
                return
            if fallback:
                self._status_legacy_by_port[port] = True
            monitor_failed = monitor_response is not None and not monitor_response.ok
            if error and (monitor_response is None or monitor_failed):
                row = self._status_row(slot)
                if row is not None:
                    row["enabled"].set(False)
                self._set_status_error(port, f"slot {slot}: {error}")
            else:
                state = self._status_state_by_port.setdefault(
                    port,
                    chip_status_cache.SnapshotState(session_id=session),
                )
                try:
                    state.configure_target(
                        target.slot,
                        target.address,
                        target.generation,
                        chip_type=target.chip_type,
                    )
                    if monitor_response is not None:
                        reply = self._status_parse(monitor_response, chip_status_cache.parse_mon_frame)
                        request = state.begin_monitor_request(slot, True)
                        state.apply_monitor_reply(reply, request)
                    self._status_targets_by_port.setdefault(port, {})[slot] = target
                    row = self._status_row(slot)
                    if row is not None:
                        row["committed_type"] = target.chip_type
                        row["committed_address"] = f"0x{target.address:02X}"
                    if snapshot is not None:
                        self._apply_status_snapshot(port, snapshot)
                    elif error:
                        # A successful MON with a failed first STAT still
                        # leaves the target registered so the poll can recover.
                        self._set_status_error(port, f"slot {slot}: {error}")
                except Exception as exc:
                    row = self._status_row(slot)
                    if row is not None:
                        row["enabled"].set(False)
                    self._set_status_error(port, f"slot {slot}: {exc}")
                else:
                    # _apply_status_snapshot owns clearing a previous error on
                    # a valid snapshot.  Preserve a STAT failure here.
                    if snapshot is None and not error:
                        self._status_errors_by_port.pop(port, None)
                    self._refresh_status_row(slot)
            return

        if kind == "clear":
            if len(result) >= 10:
                (
                    _kind,
                    _port,
                    _session,
                    _token,
                    slot,
                    target,
                    monitor_response,
                    response,
                    error,
                    cancelled,
                ) = result[:10]
            elif len(result) >= 9:
                _kind, _port, _session, _token, slot, target, response, error, cancelled = result[:9]
                monitor_response = None
            else:
                _kind, _port, _session, _token, slot, target, response, error = result
                monitor_response = None
                cancelled = False
            self._status_finish_operation(active)
            self._status_pending.pop(slot, None)
            if cancelled:
                return
            monitor_error = ""
            if monitor_response is not None and monitor_response.ok:
                state = self._status_state_by_port.get(port)
                if state is not None:
                    try:
                        reply = self._status_parse(monitor_response, chip_status_cache.parse_mon_frame)
                        request = state.begin_monitor_request(slot, False)
                        if not state.apply_monitor_reply(reply, request):
                            monitor_error = "status monitor disable acknowledgement was stale"
                    except Exception as exc:
                        monitor_error = str(exc)
                else:
                    monitor_error = "status monitor disable acknowledgement has no target state"
            if monitor_error:
                error = error or monitor_error
            if error:
                row = self._status_row(slot)
                if row is not None:
                    self._restore_status_row_identity(slot, row)
                    row["enabled"].set(True)
                self._set_status_error(port, f"slot {slot}: {error}")
            else:
                self._status_targets_by_port.get(port, {}).pop(slot, None)
                state = self._status_state_by_port.get(port)
                if state is not None:
                    state.remove_target(slot)
                row = self._status_row(slot)
                if row is not None:
                    row["committed_type"] = str(row["type"].get()).strip()
                    row["committed_address"] = str(row["address"].get()).strip()
                self._set_status_row_text(slot, "--")

    def _apply_status_snapshot(self, port: str, snapshot: chip_status_cache.Snapshot) -> bool:
        state = self._status_state_by_port.get(port)
        if state is None:
            return False
        try:
            state.apply_snapshot(snapshot)
        except chip_status_cache.StateError as exc:
            self._set_status_error(port, str(exc))
            return False
        self._status_errors_by_port.pop(port, None)
        self._refresh_status_rows()
        return True

    def _set_status_error(self, port: str, error: str) -> None:
        self._status_errors_by_port = getattr(self, "_status_errors_by_port", {})
        detail = f"{port}: {str(error).strip()}"
        self._status_errors_by_port[port] = detail
        self.ctx.state.extra["status_error"] = detail
        self._refresh_status_rows()

    def _set_status_row_text(self, slot: int, text: str) -> None:
        row = self._status_row(slot)
        if row is not None:
            row["value"].set(str(text))

    def _format_status_record(self, port: str, slot: int) -> str:
        target = self._status_targets_by_port.get(port, {}).get(slot)
        state = self._status_state_by_port.get(port)
        error = self._status_errors_by_port.get(port, "")
        if target is None or state is None:
            return error or "--"
        record = state.record(slot)
        monitor = state.monitor_enabled(slot)
        monitor_text = "MON?" if monitor is None else ("MON ON" if monitor else "MON OFF")
        d2_capable = bool(
            getattr(self._status_capabilities_by_port.get(port), "d2_scan", False)
        )
        if record is None:
            sample_text = (
                self.tr("status_cache_d2_waiting")
                if d2_capable and monitor is True
                else "no-sample"
            )
            text = f"{target.chip_type}@0x{target.address:02X} gen={target.generation:04X} {monitor_text} {sample_text}"
            return f"{text} error={error}" if error else text
        decoded = record.decoded_status
        labels = ",".join(f"b{bit}:{label}" for bit, label in (decoded.active_bits if decoded else ())) or "-"
        if decoded is not None and decoded.unknown_mask:
            labels += f" unknown=0x{decoded.unknown_mask:04X}"
        validity = "VALID" if record.valid else "NO_SAMPLE"
        if record.failed:
            validity += f" ERROR={record.error:02X}"
            if record.valid and d2_capable and record.source == chip_status_cache.SOURCE_D2_SCAN:
                validity += f" {self.tr('status_cache_history_raw')}"
        if record.flags & chip_status_cache.FLAG_STALE:
            validity += " STALE"
        if (
            d2_capable
            and monitor is True
            and not record.valid
            and not record.failed
            and record.source == chip_status_cache.SOURCE_NONE
        ):
            validity += f" {self.tr('status_cache_d2_waiting')}"
        if record.valid:
            age = f"age={record.age_ms}ms"
            if d2_capable and record.source == chip_status_cache.SOURCE_D2_SCAN:
                age += f" ({self.tr('status_cache_d2_snapshot_age')})"
        else:
            age = "age=--"
        text = (
            f"{target.chip_type}@0x{target.address:02X} gen={target.generation:04X} "
            f"{monitor_text} {validity} raw=0x{record.status:04X} {age} "
            f"src={record.source:02X} irq={record.irq_seq:08X} bits={labels}"
        )
        return f"{text} error={error}" if error else text

    def _refresh_status_row(self, slot: int) -> None:
        row = self._status_row(slot)
        if row is None:
            return
        chip_type = str(row["type"].get()).strip()
        unsupported = chip_type == "1018"
        try:
            row["enabled_check"].configure(
                state="disabled" if unsupported else "normal",
                text=self.tr("status_cache_enabled"),
            )
            row["remove"].configure(text=self.tr("status_cache_remove"))
        except Exception:
            pass
        port = getattr(getattr(self, "port_var", None), "get", lambda: "")().strip()
        if unsupported:
            self._set_status_row_text(slot, self.tr("status_cache_1018_unsupported"))
        else:
            self._set_status_row_text(slot, self._format_status_record(port, slot))

    def _refresh_status_rows(self) -> None:
        for slot in range(self.STATUS_MAX_TARGETS):
            self._refresh_status_row(slot)

    def _load_logo(self) -> None:
        """
        Home \u6807\u9898\u53f3\u4fa7 logo\u3002

        \u4f18\u5148\u8bfb\u53d6\uff1a
            assets/logo.png
            assets/icons/logo.png

        \u5982\u679c\u6682\u65f6\u6ca1\u6709\u56fe\u7247\u6587\u4ef6\uff0c\u4f1a\u663e\u793a\u6587\u5b57 LOGO \u5360\u4f4d\uff0c\u7a0b\u5e8f\u4e0d\u4f1a\u62a5\u9519\u3002
        """
        candidates = [
            Path("assets/logo.png"),
            Path("assets/icons/logo.png"),
            Path(__file__).resolve().parents[1] / "assets" / "logo.png",
            Path(__file__).resolve().parents[1] / "assets" / "icons" / "logo.png",
        ]

        for path in candidates:
            if path.exists():
                try:
                    img = tk.PhotoImage(file=str(path))
                    # \u8ba9 logo \u9ad8\u5ea6\u63a5\u8fd1\u6807\u9898\u533a\u57df\uff1b\u5982\u679c\u56fe\u7247\u8f83\u5927\uff0c\u505a\u7b80\u5355\u7f29\u5c0f
                    if img.height() > 72:
                        factor = max(1, img.height() // 72)
                        img = img.subsample(factor, factor)
                    self.logo_image = img
                    self.logo_label.configure(image=self.logo_image, text="")
                    return
                except Exception:
                    continue

        self.logo_label.configure(text="PIEZO\nGUI", justify="center")

    def refresh_ports(self, *, probe_missing: bool = True) -> None:
        if self._port_scan_closed or self._port_scan_results is not None:
            return
        # Windows port enumeration (including the COM fallback) can block in
        # a driver. Never make navigation/translation wait for that work.
        results: Queue[tuple[Any, str]] = Queue(maxsize=1)
        self._port_scan_results = results

        def scan() -> None:
            try:
                try:
                    info = self.ctx.serial.list_port_info(probe_missing=probe_missing)
                except TypeError:
                    info = self.ctx.serial.list_port_info()
            except Exception as exc:
                results.put((None, str(exc)))
            else:
                results.put((info, ""))

        threading.Thread(target=scan, daemon=True, name="serial-port-scan").start()
        self._port_scan_after_id = self.after(25, self._finish_port_scan)

    def _finish_port_scan(self) -> None:
        self._port_scan_after_id = None
        results = self._port_scan_results
        if self._port_scan_closed or results is None:
            return
        try:
            port_info, error = results.get_nowait()
        except Empty:
            self._port_scan_after_id = self.after(25, self._finish_port_scan)
            return
        self._port_scan_results = None
        if port_info is None:
            self.port_hint_label.configure(text=error)
            return
        self._apply_port_info(port_info)

    def _apply_port_info(self, port_info: Any) -> None:
        self.port_names = {info.device: info.name for info in port_info}
        ports = [info.device for info in port_info]
        current = self.port_var.get().strip()
        is_port_connected = self._is_selected_port_connected(current)
        if is_port_connected and current and current not in ports:
            ports = [current, *ports]
            self.port_names.setdefault(current, self.ctx.state.extra.get("selected_port_name", current))
        current_values = tuple(str(value) for value in self.port_combo.cget("values"))
        if current_values != tuple(ports):
            self.port_combo.configure(values=ports)

        if ports and (not current or (current not in ports and not is_port_connected)):
            self.port_var.set(ports[0])

        operation = getattr(self, "_continuous_read_operation", None)
        selected_now = self.port_var.get().strip()
        if operation is not None and operation.port != selected_now:
            self._cancel_continuous_read("connection_changed", port=operation.port)

        if not self.ctx.serial.is_available:
            hint = self.tr("serial_unavailable")
        elif not ports:
            hint = self.tr("no_ports_found")
        else:
            hint = ""
        if self.port_hint_label.cget("text") != hint:
            self.port_hint_label.configure(text=hint)

        self._update_current_port_name()

    def _stop_port_list_poll(self) -> None:
        after_id = getattr(self, "_port_list_poll_after_id", None)
        if after_id is None:
            return
        try:
            self.after_cancel(after_id)
        except (tk.TclError, AttributeError):
            pass
        self._port_list_poll_after_id = None

    def _schedule_port_list_poll(self) -> None:
        self._stop_port_list_poll()
        try:
            self._port_list_poll_after_id = self.after(
                self.PORT_LIST_POLL_INTERVAL_MS,
                self._poll_port_list,
            )
        except tk.TclError:
            self._port_list_poll_after_id = None

    def _poll_port_list(self) -> None:
        self._stop_port_list_poll()
        try:
            if not self.winfo_exists():
                return
            self.refresh_ports(probe_missing=False)
        except tk.TclError:
            return
        self._schedule_port_list_poll()

    def on_show(self) -> None:
        super().on_show()
        self.refresh_ports()

    def _update_current_port_name(self) -> None:
        port = self.port_var.get().strip()
        name = self.port_names.get(port, port)
        if port and name and name != port:
            self.current_port_name_var.set(f"{name} ({port})")
        else:
            self.current_port_name_var.set(name)

    def _is_selected_port_connected(self, port: str | None = None) -> bool:
        selected = (port if port is not None else self.port_var.get()).strip()
        checker = getattr(self.ctx.serial, "is_port_connected", None)
        if callable(checker):
            return checker(selected)
        return self.ctx.serial.is_connected

    def _on_serial_connection_lost(self, port: str, error: str) -> None:
        """Marshal a Router transport-loss event onto Tk's main thread."""

        if threading.current_thread() is threading.main_thread():
            self._handle_serial_connection_lost(port, error)
            return
        try:
            self.after(0, self._handle_serial_connection_lost, port, error)
        except Exception:
            pass

    def _handle_serial_connection_lost(self, port: str, error: str) -> None:
        """Stop Home workers and expose one reconnectable disconnected state."""

        lost_port = str(port or "").strip()
        if not lost_port:
            return

        # Router identity is the session fence.  A late callback from an old
        # manager must not clear a newly connected handle on the same port.
        manager_for_port = getattr(self.ctx.serial, "manager_for_port", None)
        if callable(manager_for_port) and manager_for_port(lost_port) is not None:
            return

        self._cancel_continuous_read("connection_changed", port=lost_port)

        home_port = getattr(getattr(self, "port_var", None), "get", lambda: "")().strip()
        home_is_lost = home_port == lost_port or (
            not home_port and getattr(self.ctx.state, "selected_port", None) == lost_port
        )
        operation = getattr(self, "_status_operation", None)
        operation_port = getattr(operation, "port", None)
        status_after_port = getattr(self, "_status_after_port", None)
        status_belongs_to_lost = (
            operation_port == lost_port
            or status_after_port == lost_port
        )
        if (
            not status_belongs_to_lost
            and home_is_lost
            and operation_port is None
            and status_after_port is None
            and getattr(self, "_status_display_port", None) in (None, lost_port)
            and (
                lost_port in getattr(self, "_status_session_by_port", {})
                or bool(getattr(self, "_status_inflight", False))
            )
        ):
            status_belongs_to_lost = True

        self._status_invalidate_port(lost_port, stop_monitor=status_belongs_to_lost)
        connected_ports = getattr(self.ctx.serial, "connected_ports", None)
        self.ctx.state.serial_connected = bool(connected_ports()) if callable(connected_ports) else False
        if getattr(self.ctx.state, "selected_port", None) == lost_port:
            self.ctx.state.selected_port = None

        detail = str(error).strip() or "serial transport disconnected"
        errors = getattr(self, "_serial_disconnect_error_by_port", {})
        first_error = errors.setdefault(lost_port, detail)
        if home_is_lost:
            self.ctx.state.extra["connection_detail"] = f"{lost_port}: disconnected: {first_error}"
            self.ctx.state.extra.pop("selected_port_name", None)

        try:
            self.refresh_ports()
        except Exception:
            pass
        if hasattr(self.ctx.app, "navigation"):
            try:
                self.ctx.app.navigation.refresh_language()
            except Exception:
                pass
        self.refresh_language()

    def toggle_connection(self) -> None:
        port = self.port_var.get().strip()
        self._cancel_continuous_read("connection_changed")
        if self._is_selected_port_connected(port):
            self._status_invalidate_port(port)
            self.ctx.serial.disconnect(port)
            self.ctx.state.serial_connected = False
            self.ctx.state.serial_connected = bool(getattr(self.ctx.serial, "connected_ports", lambda: [])())
            self.ctx.state.extra.pop("connection_detail", None)
            self.ctx.state.extra.pop("selected_port_name", None)
            if hasattr(self.ctx.app, "navigation"):
                self.ctx.app.navigation.refresh_language()
            self.refresh_language()
            return

        if not port:
            messagebox.showwarning(self.tr("error"), self.tr("no_ports_found"))
            return

        try:
            baudrate = int(self.baud_var.get())
        except Exception:
            baudrate = self.ctx.state.baudrate
        self.ctx.state.baudrate = baudrate

        resp = self.ctx.serial.connect(port, baudrate)
        if resp.ok:
            self._serial_disconnect_error_by_port.pop(port, None)
            self.ctx.state.serial_connected = True
            self.ctx.state.selected_port = port
            self.ctx.state.extra["selected_port_name"] = self.port_names.get(port, port)
            self.ctx.state.extra["connection_detail"] = f"{port}: {resp.data}"
            self._finish_connection_when_ready(port)
        else:
            messagebox.showerror(self.tr("error"), resp.error)

        self.refresh_language()

    def _finish_connection_when_ready(self, port: str) -> None:
        if not self._is_selected_port_connected(port):
            return

        ready_checker = getattr(self.ctx.serial, "is_port_ready", None)
        if callable(ready_checker):
            ready = bool(ready_checker(port))
        else:
            ready = bool(getattr(self.ctx.serial, "board_ready", True))

        if not ready:
            self.ctx.state.extra["connection_detail"] = f"{port}: waiting READY REV04"
            self.after(50, self._finish_connection_when_ready, port)
            return

        serial = self._serial_for_port(port)
        identity = str(getattr(serial, "board_identity", "")).strip()
        if identity:
            self.ctx.state.extra["connection_detail"] = f"{port}: {identity}"
        self._status_refresh_session(port)
        self._detect_awp192x_after_connect(port, serial)
        # Leave the explicit old-firmware prompt visible after the existing
        # read-only chip discovery has updated the connection detail.
        self._probe_status_capability_once(port, serial)
        if hasattr(self.ctx.app, "navigation"):
            self.ctx.app.navigation.refresh_language()
        self.refresh_language()

    def _probe_status_capability_once(self, port: str, serial: Any | None) -> None:
        """Read CAP exactly once per connected port generation."""

        # The application uses SerialRouter for real connections.  A bare
        # SerialManager is retained as a lightweight/manual compatibility
        # surface and has no Home-local event FIFO to consume.
        if not callable(getattr(self.ctx.serial, "drain_events", None)):
            return
        self._push_cap_attempted_by_port = getattr(self, "_push_cap_attempted_by_port", set())
        if port in self._push_cap_attempted_by_port:
            return
        self._push_cap_attempted_by_port.add(port)
        reader = getattr(serial, "read_status_capabilities", None)
        if not callable(reader):
            return
        try:
            response = reader()
        except Exception as exc:
            self._record_status_capability_notice(port, str(exc))
            return
        if response is None or not response.ok:
            self._record_status_capability_notice(
                port,
                getattr(response, "error", "")
                or getattr(response, "data", "")
                or "CAP? failed",
            )
            return
        try:
            capabilities = self._status_parse(response, chip_status_cache.parse_cap_frame)
        except (TypeError, ValueError) as exc:
            self._record_status_capability_notice(port, str(exc))
            return
        self._status_capabilities_by_port[port] = capabilities
        if getattr(capabilities, "push_event", False):
            self._start_push_event_poll()
        else:
            self._record_status_capability_notice(port, "FLAG3 EVSTAT push is unavailable; upgrade firmware")

    def _record_status_capability_notice(self, port: str, detail: str) -> None:
        """Expose an old/failed CAP probe without starting a fallback poll."""

        try:
            message = self.tr("status_cache_push_required")
        except Exception:
            # Lightweight Home fixtures may omit AppState.language; keep the
            # capability boundary visible without making readiness fail.
            message = "FLAG3 EVSTAT push firmware is required; older firmware is not polled automatically"
        if detail:
            message = f"{message}: {str(detail).strip()}"
        if getattr(self, "port_var", None) is not None:
            current = self.port_var.get().strip()
        else:
            current = getattr(self.ctx.state, "selected_port", None)
        if current == port:
            self.ctx.state.extra["connection_detail"] = f"{port}: {message}"
        self._refresh_status_rows()

    def _start_push_event_poll(self) -> None:
        """Schedule only the local Router FIFO drain; it never reads serial."""

        if not callable(getattr(self.ctx.serial, "drain_events", None)):
            return
        if getattr(self, "_push_event_after_id", None) is None:
            self._push_event_after_id = self.after(
                self.PUSH_EVENT_POLL_INTERVAL_MS,
                self._poll_push_events,
            )

    def _poll_push_events(self) -> None:
        self._push_event_after_id = None
        self._push_event_pending_by_port = getattr(self, "_push_event_pending_by_port", {})
        self._push_event_pending_since_by_port = getattr(self, "_push_event_pending_since_by_port", {})
        drain_events = getattr(self.ctx.serial, "drain_events", None)
        if not callable(drain_events):
            return
        try:
            events = drain_events()
        except Exception as exc:
            self._set_status_error(
                getattr(self, "_status_display_port", None) or "serial",
                f"EVSTAT queue failed: {exc}",
            )
            return
        for event in events:
            if not isinstance(event, tuple) or len(event) != 3:
                continue
            port, manager, snapshot = event
            key = str(port)
            self._push_event_pending_by_port.setdefault(key, []).append((manager, snapshot))
            self._push_event_pending_since_by_port.setdefault(key, time.monotonic())
        for port in list(getattr(self, "_push_event_pending_by_port", {})):
            self._consume_push_events_for_port(port)
        if (
            events
            or getattr(self, "_push_event_pending_by_port", {})
            or any(
                getattr(cap, "push_event", False)
                for cap in getattr(self, "_status_capabilities_by_port", {}).values()
            )
        ):
            self._start_push_event_poll()

    def _consume_push_events_for_port(self, port: str) -> None:
        self._push_event_pending_by_port = getattr(self, "_push_event_pending_by_port", {})
        self._push_event_pending_since_by_port = getattr(self, "_push_event_pending_since_by_port", {})
        pending = getattr(self, "_push_event_pending_by_port", {}).get(port)
        if not pending:
            return
        manager_for_port = getattr(self.ctx.serial, "manager_for_port", None)
        current_manager = manager_for_port(port) if callable(manager_for_port) else self._serial_for_port(port)
        retained: list[tuple[Any, chip_status_cache.Snapshot]] = []
        for manager, snapshot in pending:
            if current_manager is None:
                self._record_status_event_drop(port, snapshot, "port disconnected")
                continue
            if manager is not current_manager:
                # The Router has already fenced a superseded handle.  Do not
                # let a late event repopulate a new port/session cache.
                continue
            if not self._push_event_is_current(port, snapshot):
                if self._push_event_should_wait(port, snapshot):
                    retained.append((manager, snapshot))
                else:
                    self._record_status_event_drop(port, snapshot, "stale or unbound identity")
                continue
            if self._apply_status_snapshot(port, snapshot):
                self._acknowledge_evstat(port, snapshot.irq_seq)
        if retained:
            self._push_event_pending_by_port[port] = retained
        else:
            self._push_event_pending_by_port.pop(port, None)
            self._push_event_pending_since_by_port = getattr(self, "_push_event_pending_since_by_port", {})
            self._push_event_pending_since_by_port.pop(port, None)

    def _acknowledge_evstat(self, port: str, sequence: int) -> None:
        if not sequence:
            return
        last = getattr(self, "_status_last_acked_irq_by_port", {}).get(port)
        if last == sequence:
            return
        self._status_last_acked_irq_by_port[port] = sequence
        threading.Thread(
            target=self._acknowledge_evstat_worker,
            args=(port, sequence),
            daemon=True,
        ).start()

    def _acknowledge_evstat_worker(self, port: str, sequence: int) -> None:
        try:
            serial = self._serial_for_port(port)
            if serial is None:
                return
            response = serial.acknowledge_interrupt(sequence)
            if response is not None and not response.ok:
                logger = getattr(serial, "emit_log", None)
                if callable(logger):
                    logger(
                        f"ERROR INTACK {sequence:08X}: {response.error or response.data}"
                    )
        except Exception as exc:
            serial = self._serial_for_port(port)
            logger = getattr(serial, "emit_log", None) if serial is not None else None
            if callable(logger):
                logger(f"ERROR INTACK {sequence:08X}: {exc}")

    def _push_event_is_current(self, port: str, snapshot: chip_status_cache.Snapshot) -> bool:
        capabilities = getattr(self, "_status_capabilities_by_port", {}).get(port)
        session = getattr(self, "_status_session_by_port", {}).get(port)
        targets = getattr(self, "_status_targets_by_port", {}).get(port, {})
        state = getattr(self, "_status_state_by_port", {}).get(port)
        if capabilities is None or not getattr(capabilities, "push_event", False):
            return False
        if session is None or snapshot.session_id != session:
            return False
        if not targets:
            return False
        # A target identity can remain locally registered while its explicit
        # MON transaction is pending or has already been acknowledged OFF.
        # Such a frame is stale bookkeeping, not an active Home observation.
        if state is None or not any(
            state.monitor_enabled(slot) is True for slot in targets
        ):
            return False
        for record in snapshot.records:
            expected = targets.get(record.slot)
            if (expected is None) != (record.identity is None):
                return False
            if expected is not None and record.identity != expected:
                return False
        return True

    def _push_event_should_wait(self, port: str, snapshot: chip_status_cache.Snapshot) -> bool:
        """Retain an adjacent MON event until its target commit is visible."""

        if port not in getattr(self, "_status_session_by_port", {}):
            pending_since = getattr(self, "_push_event_pending_since_by_port", {}).get(port)
            if pending_since is None:
                return False
            return time.monotonic() - pending_since < self.PUSH_EVENT_DEFER_TIMEOUT_S
        if snapshot.session_id != self._status_session_by_port.get(port):
            return False
        capabilities = getattr(self, "_status_capabilities_by_port", {}).get(port)
        if capabilities is None:
            return True
        targets = getattr(self, "_status_targets_by_port", {}).get(port, {})
        if not targets:
            return getattr(self, "_status_operation", None) is not None or bool(
                getattr(self, "_status_pending", {})
            )
        for record in snapshot.records:
            expected = targets.get(record.slot)
            if expected is not None and record.identity != expected:
                return False
            if expected is None and record.identity is not None:
                # A target may be committed a few Tk ticks after MON ACK.
                if getattr(self, "_status_operation", None) is not None:
                    return True
                return False
        # Re-enabling an already registered target leaves its identity in the
        # local table while the explicit MON ON acknowledgement is in flight.
        # Keep a matching adjacent event until that acknowledgement commits;
        # a MON OFF/TGTCLR operation never sets this enable-pending condition.
        pending_enable = any(
            isinstance(binding, tuple)
            and len(binding) >= 4
            and binding[0] == port
            and binding[1] == self._status_session_by_port.get(port)
            and bool(binding[3])
            for binding in getattr(self, "_status_pending", {}).values()
        )
        operation = getattr(self, "_status_operation", None)
        configuring = (
            operation is not None
            and getattr(operation, "port", None) == port
            and getattr(operation, "session", None) == self._status_session_by_port.get(port)
            and getattr(operation, "kind", "") == "configure"
        )
        return pending_enable or configuring

    def _record_status_event_drop(self, port: str, snapshot: chip_status_cache.Snapshot, reason: str) -> None:
        """Drop a stale push frame without turning normal fencing into an error.

        Old sessions, superseded managers, removed targets and events received
        while no MON target is active are expected during reconnect and target
        changes.  They must not update the status cache or broadcast a shared
        error to every Home row.  Actual failures for a current binding still
        flow through ``_apply_status_snapshot`` and the operation result paths.
        """

        del port, snapshot, reason

    def destroy(self) -> None:
        self._port_scan_closed = True
        if self._port_scan_after_id is not None:
            try:
                self.after_cancel(self._port_scan_after_id)
            except tk.TclError:
                pass
            self._port_scan_after_id = None
        self._port_scan_results = None
        self._cancel_continuous_read("page_destroyed")
        self._stop_port_list_poll()
        after_id = getattr(self, "_continuous_read_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except (tk.TclError, AttributeError):
                pass
            self._continuous_read_after_id = None
        after_id = getattr(self, "_push_event_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except (tk.TclError, AttributeError):
                pass
            self._push_event_after_id = None
        listener = getattr(self, "_serial_connection_listener", None)
        remove_connection_listener = getattr(self.ctx.serial, "remove_connection_listener", None)
        if listener is not None and callable(remove_connection_listener):
            remove_connection_listener(listener)
            self._serial_connection_listener = None
        super().destroy()

    def _board_gpio_modes_for_pin(self, pin: str) -> tuple[str, ...]:
        if pin in GPIO_DETECT_PINS:
            return GPIO_MODES
        return tuple(mode for mode in GPIO_MODES if mode != GPIO_MODE_DETECT)

    def _on_board_gpio_pin_selected(self, _event: object | None = None) -> None:
        pin = str(self.gpio_pin_var.get()).strip().upper()
        modes = self._board_gpio_modes_for_pin(pin)
        self.gpio_mode_combo.configure(values=list(modes))
        if self.gpio_mode_var.get() not in modes:
            self.gpio_mode_var.set(modes[0] if modes else "HIGH")

    def apply_board_gpio_mode(self) -> None:
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return
        pin = str(self.gpio_pin_var.get()).strip().upper()
        mode = str(self.gpio_mode_var.get()).strip().upper()
        resp = self.ctx.serial.configure_gpio(pin, mode)
        if resp.ok:
            self.gpio_value_var.set(f"{pin} {mode}")
            self.ctx.state.extra["connection_detail"] = f"{self.ctx.state.selected_port}: {resp.data}"
        else:
            messagebox.showerror(self.tr("error"), resp.error)
        self.refresh_language()

    def read_board_gpio(self) -> None:
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return
        pin = str(self.gpio_pin_var.get()).strip().upper()
        resp = self.ctx.serial.read_gpio(pin)
        if not resp.ok:
            messagebox.showerror(self.tr("error"), resp.error)
            return
        try:
            ack = parse_gpio_read_response(resp.data)
        except ValueError as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        self.gpio_value_var.set(str(ack.level))
        self.refresh_language()

    def read_board_adc(self) -> None:
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return
        pin = str(self.adc_pin_var.get()).strip().upper()
        resp = self.ctx.serial.read_adc(pin)
        if not resp.ok:
            messagebox.showerror(self.tr("error"), resp.error)
            return
        try:
            ack = parse_adc_read_response(resp.data)
        except ValueError as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        self.adc_value_var.set(str(ack.value))
        self.refresh_language()

    def read_board_frequency(self) -> None:
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return
        resp = self.ctx.serial.read_frequency()
        if not resp.ok:
            messagebox.showerror(self.tr("error"), resp.error)
            return
        try:
            ack = parse_freq_response(resp.data)
        except ValueError as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        self.freq_value_var.set(f"{ack.frequency_hz:.2f} Hz")
        self.refresh_language()

    def apply_i2c_speed(self) -> None:
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return
        try:
            speed = int(self.i2c_speed_var.get())
        except Exception:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return
        resp = self.ctx.serial.set_i2c_speed(speed)
        if resp.ok:
            self.ctx.state.extra["i2c_speed"] = speed
            self.ctx.state.extra["connection_detail"] = f"{self.ctx.state.selected_port}: {resp.data}"
        else:
            messagebox.showerror(self.tr("error"), resp.error)
        self.refresh_language()

    def _serial_for_port(self, port: str) -> Any | None:
        """Bind background Home work to its connected board, not the active page."""
        resolver = getattr(self.ctx.serial, "manager_for_port", None)
        if callable(resolver):
            return resolver(port)
        return self.ctx.serial

    def _discover_supported_chip(self, serial: Any | None = None) -> tuple[ChipDiscovery | None, str]:
        """Return a read-only supported-chip probe result without touching Tk state."""
        last_error = ""

        def read_id(probe: ChipProbe, addr: int) -> int | None:
            nonlocal last_error
            value, error = self._read_detect_id(
                addr,
                probe.id_register,
                attempts=probe.attempts,
                serial=serial,
            )
            if value is not None:
                last_error = f"0x{addr:02X}: 0x{value & 0xFFFF:04X}"
            elif error and "ERR RREGW I2C" not in error.upper():
                last_error = error
            return value

        return discover_first(read_id), last_error

    def _apply_supported_chip_discovery(
        self,
        found: ChipDiscovery | None,
        last_error: str,
        *,
        port: str | None = None,
    ) -> None:
        if found is not None:
            addr = found.address
            self.device_addr_var.set(f"0x{addr:02X}")
            if found.probe.name == "AWP1018":
                self.ctx.state.awp192x_device_addr = addr
                self.ctx.state.extra["awp1018_device_addr"] = addr
                self.ctx.state.extra["connection_detail"] = f"AWP1018 0x{addr:02X}"
                return
            if found.probe.name == "AWP192X":
                self.ctx.state.awp192x_device_addr = addr
                label = {0x44: "Pull-down", 0x45: "Pull-up", 0x46: "Floating"}[addr]
                self.ctx.state.extra["connection_detail"] = f"AWP192X {label} 0x{addr:02X}"
                return
            self.ctx.state.extra["connection_detail"] = f"AWP1221 0x{addr:02X}"
            return

        detail = f": {last_error}" if last_error else ""
        board = port or self.ctx.state.selected_port or self.port_var.get().strip()
        self.ctx.state.extra["connection_detail"] = f"{board}: board online; supported chip not detected{detail}"

    def _detect_awp192x_after_connect(self, port: str, serial: Any | None) -> None:
        """Apply the initial read-only REV04 supported-chip discovery to the GUI."""
        found, last_error = self._discover_supported_chip(serial)
        self._apply_supported_chip_discovery(found, last_error, port=port)

    def _read_detect_id(
        self,
        addr: int,
        reg: int,
        *,
        attempts: int,
        serial: Any | None = None,
    ) -> tuple[int | None, str]:
        last_error = ""
        transport = serial or self.ctx.serial
        for attempt in range(max(1, attempts)):
            resp = transport.read_register_value(addr, reg, bits=16)
            if resp.ok:
                value = transport.extract_rdataw_value(
                    resp.data,
                    device_addr=addr,
                    reg_addr=reg,
                    bits=16,
                )
                if value is not None:
                    return value, ""
                last_error = resp.data or "empty response"
            else:
                last_error = resp.error

            if attempt + 1 < attempts:
                time.sleep(0.05)

        return None, last_error

    def _parse_hex(self, text: str) -> int:
        return int(text.strip(), 0)

    def _update_device_addr(self) -> int:
        addr = self._parse_hex(self.device_addr_var.get())
        self.ctx.state.awp192x_device_addr = addr
        return addr

    def _selected_register_width(self) -> int:
        return int(self.register_width_var.get().split("-", 1)[0])

    def read_register(self) -> None:
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return
        try:
            dev = self._update_device_addr()
            reg = self._parse_hex(self.reg_addr_var.get())
            bits = self._selected_register_width()
            resp = self.ctx.serial.read_register_value(dev, reg, bits=bits)
            if resp.ok:
                value = self.ctx.serial.extract_rdataw_value(
                    resp.data,
                    device_addr=dev,
                    reg_addr=reg,
                    bits=bits,
                )
                mask = (1 << bits) - 1
                digits = bits // 4
                self.read_value_var.set(resp.data if value is None else f"0x{value & mask:0{digits}X}")
            else:
                messagebox.showerror(self.tr("error"), resp.error)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def open_continuous_read_dialog(self) -> None:
        dialog = getattr(self, "_continuous_read_dialog", None)
        if dialog is not None and not getattr(dialog, "_closed", True):
            try:
                if dialog.winfo_exists():
                    dialog.lift()
                    return
            except tk.TclError:
                pass

        dialog = ContinuousRegisterReadDialog(self)
        self._continuous_read_dialog = dialog
        operation = getattr(self, "_continuous_read_operation", None)
        if operation is not None:
            dialog.wait_for_previous()

    def _start_continuous_read(
        self,
        dialog: ContinuousRegisterReadDialog,
        settings: ContinuousReadSettings,
    ) -> bool:
        if getattr(self, "_continuous_read_operation", None) is not None:
            dialog.wait_for_previous()
            return False

        port = self.port_var.get().strip()
        if not port or not self._is_selected_port_connected(port):
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"), parent=dialog)
            return False
        transport = self._serial_for_port(port)
        if transport is None or not bool(getattr(transport, "is_connected", False)):
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"), parent=dialog)
            return False
        transport_port = str(getattr(transport, "port", port) or port).strip()
        if transport_port != port:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"), parent=dialog)
            return False

        self._continuous_read_next_token = getattr(self, "_continuous_read_next_token", 0) + 1
        operation = _ContinuousReadOperation(
            token=self._continuous_read_next_token,
            port=port,
            transport=transport,
            serial_handle=getattr(transport, "_serial", None),
            settings=settings,
            dialog=dialog,
        )
        self._continuous_read_operation = operation
        dialog.begin(operation.token, settings)
        operation.thread = threading.Thread(
            target=self._run_continuous_read_worker,
            args=(operation,),
            name=f"continuous-register-read-{port}",
            daemon=True,
        )
        try:
            operation.thread.start()
        except Exception as exc:
            operation.outcome = ContinuousReadOutcome(
                attempts=0,
                terminal_error=str(exc).strip() or "Could not start register read worker",
                stop_reason="worker_start_failed",
            )
            operation.done_event.set()
        self._schedule_continuous_read_result_check(operation)
        return True

    def _run_continuous_read_worker(self, operation: _ContinuousReadOperation) -> None:
        def emit_result(result: ContinuousReadResult) -> bool:
            while not operation.dialog_closed:
                try:
                    operation.results.put(result, timeout=0.05)
                    return True
                except Full:
                    continue
            return False

        try:
            operation.outcome = run_continuous_register_reads(
                operation.settings,
                operation.transport,
                operation.cancel_event,
                emit_result,
                is_connection_current=lambda: self._continuous_read_connection_is_current(operation),
            )
        except Exception as exc:
            operation.outcome = ContinuousReadOutcome(
                attempts=0,
                terminal_error=str(exc).strip() or exc.__class__.__name__,
                stop_reason="worker_error",
            )
        finally:
            operation.done_event.set()

    def _continuous_read_connection_is_current(self, operation: _ContinuousReadOperation) -> bool:
        transport = operation.transport
        if self._serial_for_port(operation.port) is not transport:
            return False
        checker = getattr(self.ctx.serial, "is_port_connected", None)
        if callable(checker):
            if not checker(operation.port):
                return False
        elif not bool(getattr(transport, "is_connected", False)):
            return False

        current_port = str(getattr(transport, "port", operation.port) or operation.port).strip()
        if current_port != operation.port:
            return False
        if operation.serial_handle is not None:
            if getattr(transport, "_serial", None) is not operation.serial_handle:
                return False
        return True

    def _schedule_continuous_read_result_check(self, operation: _ContinuousReadOperation) -> None:
        if getattr(self, "_continuous_read_operation", None) is not operation:
            return
        if getattr(self, "_continuous_read_after_id", None) is not None:
            return
        try:
            self._continuous_read_after_id = self.after(
                self.CONTINUOUS_READ_RESULT_CHECK_INTERVAL_MS,
                self._poll_continuous_read_results,
                operation,
            )
        except tk.TclError:
            self._cancel_continuous_read("page_destroyed")

    def _poll_continuous_read_results(self, operation: _ContinuousReadOperation) -> None:
        if getattr(self, "_continuous_read_operation", None) is not operation:
            return
        self._continuous_read_after_id = None

        while True:
            if getattr(self, "_continuous_read_operation", None) is not operation:
                return
            try:
                result = operation.results.get_nowait()
            except Empty:
                break
            dialog = getattr(self, "_continuous_read_dialog", None)
            if (
                not operation.dialog_closed
                and dialog is operation.dialog
                and not getattr(dialog, "_closed", True)
                and getattr(dialog, "operation_token", None) == operation.token
            ):
                dialog.append_result(result)

        if getattr(self, "_continuous_read_operation", None) is not operation:
            return
        if operation.done_event.is_set() and operation.results.empty():
            outcome = operation.outcome or ContinuousReadOutcome(
                attempts=0,
                terminal_error="Register read worker stopped unexpectedly",
                stop_reason="worker_error",
            )
            dialog = getattr(self, "_continuous_read_dialog", None)
            if (
                not operation.dialog_closed
                and dialog is operation.dialog
                and not getattr(dialog, "_closed", True)
                and getattr(dialog, "operation_token", None) == operation.token
            ):
                dialog.finish(outcome, cancellation_reason=operation.cancel_reason)

            if getattr(self, "_continuous_read_operation", None) is not operation:
                return
            self._continuous_read_operation = None
            current_dialog = getattr(self, "_continuous_read_dialog", None)
            if current_dialog is not None and not getattr(current_dialog, "_closed", True):
                current_dialog.worker_released()
            return

        self._schedule_continuous_read_result_check(operation)

    def _cancel_continuous_read(self, reason: str, *, port: str | None = None) -> None:
        operation = getattr(self, "_continuous_read_operation", None)
        if operation is None or (port is not None and operation.port != port):
            return
        operation.cancel_reason = reason
        if reason in {"window_closed", "page_destroyed", "application_closing"}:
            operation.dialog_closed = True
        operation.cancel_event.set()

        dialog = getattr(self, "_continuous_read_dialog", None)
        if (
            dialog is not None
            and not getattr(dialog, "_closed", True)
            and getattr(dialog, "operation_token", None) == operation.token
        ):
            dialog.mark_stopping(reason)

    def cancel_continuous_read(self, reason: str = "application_closing") -> None:
        """Stop background reads before the application disconnects its ports."""

        self._cancel_continuous_read(reason)

    def _continuous_read_dialog_closed(self, dialog: ContinuousRegisterReadDialog) -> None:
        if getattr(self, "_continuous_read_dialog", None) is dialog:
            self._continuous_read_dialog = None
        self._cancel_continuous_read("window_closed")

    def write_register(self) -> None:
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return
        try:
            dev = self._update_device_addr()
            reg = self._parse_hex(self.reg_addr_var.get())
            value = self._parse_hex(self.write_value_var.get())
            bits = self._selected_register_width()
            resp = self.ctx.serial.write_register_value(dev, reg, value, bits=bits)
            if not resp.ok:
                messagebox.showerror(self.tr("error"), resp.error)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def refresh_language(self) -> None:
        super().refresh_language()
        lang = self.language
        status = t(lang, "connected") if self.ctx.serial.is_connected else t(lang, "disconnected")
        detail = self.ctx.state.extra.get("connection_detail", "")
        suffix = f" | {detail}" if detail else ""
        self.status_label.configure(text=f"{t(lang, 'status')}: {status}{suffix}")
        self.port_label.configure(text=t(lang, "serial_port"))
        self.baud_label.configure(text=t(lang, "baudrate"))
        self.i2c_label.configure(text=t(lang, "i2c_speed"))
        self.i2c_btn.configure(text=t(lang, "apply_i2c_speed"))
        self.current_port_name_label.configure(text=f"{t(lang, 'current_serial_name')}:")
        self.refresh_btn.configure(text=t(lang, "refresh_ports"))
        self.connect_btn.configure(text=t(lang, "disconnect") if self._is_selected_port_connected() else t(lang, "connect"))

        self.device_addr_label.configure(text=t(lang, "device_addr"))
        self.reg_addr_label.configure(text=t(lang, "reg_addr"))
        self.write_value_label.configure(text=t(lang, "write_value"))
        self.register_width_label.configure(text=t(lang, "register_data_width"))
        self.read_value_label.configure(text=t(lang, "read_value"))
        self.rr_btn.configure(text=t(lang, "rr"))
        self.rw_btn.configure(text=t(lang, "rw"))
        continuous_read_btn = getattr(self, "continuous_read_btn", None)
        if continuous_read_btn is not None:
            continuous_read_btn.configure(text=t(lang, "continuous_read_open"))
        if getattr(self, "board_tools_title", None) is not None:
            self.board_tools_title.configure(text=t(lang, "board_tools_title"))
            self.gpio_pin_label.configure(text=t(lang, "board_gpio_pin"))
            self.gpio_mode_label.configure(text=t(lang, "board_gpio_mode"))
            self.gpio_cfg_btn.configure(text=t(lang, "board_gpio_apply"))
            self.gpio_read_btn.configure(text=t(lang, "board_gpio_read"))
            self.adc_pin_label.configure(text=t(lang, "board_adc_pin"))
            self.adc_read_btn.configure(text=t(lang, "board_adc_read"))
            self.freq_btn.configure(text=t(lang, "board_freq_read"))
            self._on_board_gpio_pin_selected()

        self.refresh_ports()
        self._refresh_status_rows()
        dialog = getattr(self, "_continuous_read_dialog", None)
        if dialog is not None and not getattr(dialog, "_closed", True):
            try:
                dialog.refresh_language()
            except tk.TclError:
                pass


class ContinuousRegisterReadDialog(tk.Toplevel):
    """Non-modal Home dialog for bounded, sequential register reads."""

    def __init__(self, home: HomePage) -> None:
        super().__init__(home)
        self.home = home
        self._closed = False
        self.operation_token: int | None = None
        self._waiting_for_previous = False
        self._form_enabled = True
        self._settings: ContinuousReadSettings | None = None
        self._attempts = 0
        self._success_count = 0
        self._mismatch_count = 0
        self._failure_count = 0
        self._displayed_results: list[ContinuousReadResult] = []
        self._mismatch_policy_keys = ("continue", "stop", "error_continue")
        self._failure_policy_keys = ("continue", "stop")
        self._status_key = "continuous_read_ready"
        self._status_error = ""
        self._labels: list[tuple[ttk.Label, str]] = []

        self.device_addr_var = tk.StringVar(self, value=home.device_addr_var.get())
        self.reg_addr_var = tk.StringVar(self, value=home.reg_addr_var.get())
        self.width_var = tk.StringVar(self, value=home.register_width_var.get())
        self.expected_var = tk.StringVar(self, value="")
        self.mismatch_policy_code_var = tk.StringVar(self, value="continue")
        self.failure_policy_code_var = tk.StringVar(self, value="continue")
        self.bit_match_var = tk.BooleanVar(self, value=False)
        self.bit_index_var = tk.StringVar(self, value="0")
        self.expected_bit_var = tk.StringVar(self, value="1")
        self.count_var = tk.StringVar(self, value="10")
        self.infinite_var = tk.BooleanVar(self, value=False)
        self.interval_var = tk.StringVar(self, value="100")
        self.counts_var = tk.StringVar(self, value="")
        self.status_var = tk.StringVar(self, value="")

        self.transient(home.winfo_toplevel())
        fit_toplevel_to_screen(
            self,
            preferred_width=780,
            preferred_height=700,
            minimum_width=460,
            minimum_height=320,
        )
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        self.refresh_language()

    def _add_label(self, parent: tk.Misc, row: int, column: int, key: str) -> ttk.Label:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=column, sticky="w", padx=(0, 8), pady=5)
        self._labels.append((label, key))
        return label

    def _build_ui(self) -> None:
        scroll_area = VerticalScrollArea(
            self,
            self.home.ctx,
            horizontal=True,
            min_content_width=680,
        )
        scroll_area.grid(row=0, column=0, sticky="nsew")
        self._scroll_area = scroll_area
        body = ttk.Frame(scroll_area.content, style="Panel.TFrame", padding=14)
        body.grid(row=0, column=0, sticky="nsew")
        scroll_area.content.columnconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(3, weight=1)
        body.rowconfigure(13, weight=1)

        self._add_label(body, 0, 0, "continuous_read_device_addr")
        self.device_addr_entry = ttk.Entry(body, textvariable=self.device_addr_var, width=13)
        self.device_addr_entry.grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=5)
        self._add_label(body, 0, 2, "continuous_read_reg_addr")
        self.reg_addr_entry = ttk.Entry(body, textvariable=self.reg_addr_var, width=13)
        self.reg_addr_entry.grid(row=0, column=3, sticky="ew", pady=5)

        self._add_label(body, 1, 0, "continuous_read_width")
        self.width_combo = ttk.Combobox(
            body,
            textvariable=self.width_var,
            values=[f"{bits}-bit" for bits in HomePage.REGISTER_WIDTHS],
            state="readonly",
            width=10,
        )
        self.width_combo.grid(row=1, column=1, sticky="w", padx=(0, 16), pady=5)
        self._add_label(body, 1, 2, "continuous_read_expected")
        self.expected_entry = ttk.Entry(body, textvariable=self.expected_var, width=13)
        self.expected_entry.grid(row=1, column=3, sticky="ew", pady=5)

        self.expected_hint = ttk.Label(body, style="Muted.TLabel", wraplength=540)
        self.expected_hint.grid(row=2, column=1, columnspan=3, sticky="w", pady=(0, 5))

        self._add_label(body, 3, 0, "continuous_read_mismatch_policy")
        self.mismatch_policy_combo = ttk.Combobox(body, state="readonly", width=30)
        self.mismatch_policy_combo.grid(row=3, column=1, columnspan=3, sticky="w", pady=5)
        self.mismatch_policy_combo.bind("<<ComboboxSelected>>", self._update_mismatch_policy_selection)

        self._add_label(body, 4, 0, "continuous_read_failure_policy")
        self.failure_policy_combo = ttk.Combobox(body, state="readonly", width=30)
        self.failure_policy_combo.grid(row=4, column=1, columnspan=3, sticky="w", pady=5)
        self.failure_policy_combo.bind("<<ComboboxSelected>>", self._update_failure_policy_selection)

        self.bit_match_check = ttk.Checkbutton(
            body,
            variable=self.bit_match_var,
            command=self._update_bit_state,
        )
        self.bit_match_check.grid(row=5, column=0, columnspan=4, sticky="w", pady=5)
        self._add_label(body, 6, 0, "continuous_read_bit_index")
        self.bit_index_entry = ttk.Entry(body, textvariable=self.bit_index_var, width=13)
        self.bit_index_entry.grid(row=6, column=1, sticky="w", padx=(0, 16), pady=5)
        self._add_label(body, 6, 2, "continuous_read_expected_bit")
        self.expected_bit_combo = ttk.Combobox(
            body,
            textvariable=self.expected_bit_var,
            values=("0", "1"),
            state="readonly",
            width=10,
        )
        self.expected_bit_combo.grid(row=6, column=3, sticky="w", pady=5)
        self.bit_hint = ttk.Label(body, style="Muted.TLabel", wraplength=540)
        self.bit_hint.grid(row=7, column=1, columnspan=3, sticky="w", pady=(0, 5))

        self._add_label(body, 8, 0, "continuous_read_count")
        self.count_entry = ttk.Entry(body, textvariable=self.count_var, width=13)
        self.count_entry.grid(row=8, column=1, sticky="w", padx=(0, 16), pady=5)
        self.infinite_check = ttk.Checkbutton(
            body,
            variable=self.infinite_var,
            command=self._update_count_state,
        )
        self.infinite_check.grid(row=8, column=2, columnspan=2, sticky="w", pady=5)

        self._add_label(body, 9, 0, "continuous_read_interval")
        self.interval_entry = ttk.Entry(body, textvariable=self.interval_var, width=13)
        self.interval_entry.grid(row=9, column=1, sticky="w", pady=5)

        controls = ttk.Frame(body, style="Panel.TFrame")
        controls.grid(row=10, column=0, columnspan=4, sticky="w", pady=(8, 4))
        self.start_button = ttk.Button(
            controls,
            style="Primary.TButton",
            command=self.start_read,
        )
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button = ttk.Button(
            controls,
            style="Secondary.TButton",
            command=self.stop_read,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=1, padx=(0, 8))
        self.close_button = ttk.Button(
            controls,
            style="Secondary.TButton",
            command=self.close,
        )
        self.close_button.grid(row=0, column=2)

        self.counts_label = ttk.Label(body, textvariable=self.counts_var, style="Panel.TLabel")
        self.counts_label.grid(row=11, column=0, columnspan=4, sticky="w", pady=(6, 2))
        self.status_label = ttk.Label(body, textvariable=self.status_var, style="Muted.TLabel")
        self.status_label.grid(row=12, column=0, columnspan=4, sticky="w", pady=(0, 6))

        results_frame = ttk.Frame(body, style="Panel.TFrame")
        results_frame.grid(row=13, column=0, columnspan=4, sticky="nsew")
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)
        self.results_tree = ttk.Treeview(
            results_frame,
            columns=("sequence", "value", "check"),
            show="headings",
            height=12,
        )
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        self.results_scrollbar = ttk.Scrollbar(
            results_frame,
            orient="vertical",
            command=self.results_tree.yview,
        )
        self.results_scrollbar.grid(row=0, column=1, sticky="ns")
        self.results_tree.configure(yscrollcommand=self.results_scrollbar.set)
        self.results_tree.column("sequence", width=75, minwidth=65, stretch=False, anchor="center")
        self.results_tree.column("value", width=145, minwidth=110, stretch=False, anchor="center")
        self.results_tree.column("check", width=380, minwidth=220, stretch=True, anchor="w")
        self.results_tree.tag_configure("mismatch", foreground="#B91C1C")
        self.results_tree.tag_configure("error", foreground="#B91C1C")
        self.results_tree.tag_configure("match", foreground="#047857")

        self._set_form_enabled(True)
        self.stop_button.configure(state="disabled")
        self._refresh_counts()
        self._refresh_status()

    def refresh_language(self) -> None:
        language = self.home.ctx.state.language
        translate = lambda key: t(language, key)
        self.title(translate("continuous_read_title"))
        for label, key in self._labels:
            label.configure(text=translate(key))
        self.expected_hint.configure(text=translate("continuous_read_expected_hint"))
        self.bit_hint.configure(text=translate("continuous_read_bit_hint"))
        self.bit_match_check.configure(text=translate("continuous_read_bit_match_enable"))
        self.infinite_check.configure(text=translate("continuous_read_infinite"))
        self.start_button.configure(text=translate("continuous_read_start"))
        self.stop_button.configure(text=translate("continuous_read_stop"))
        self.close_button.configure(text=translate("continuous_read_close"))
        policy_code = self.mismatch_policy_code_var.get()
        if policy_code not in self._mismatch_policy_keys:
            policy_code = "continue"
            self.mismatch_policy_code_var.set(policy_code)
        self.mismatch_policy_combo.configure(
            values=tuple(
                translate(f"continuous_read_policy_{key}")
                for key in self._mismatch_policy_keys
            )
        )
        self.mismatch_policy_combo.current(self._mismatch_policy_keys.index(policy_code))
        failure_policy_code = self.failure_policy_code_var.get()
        if failure_policy_code not in self._failure_policy_keys:
            failure_policy_code = "continue"
            self.failure_policy_code_var.set(failure_policy_code)
        self.failure_policy_combo.configure(
            values=tuple(
                translate(f"continuous_read_failure_{key}")
                for key in self._failure_policy_keys
            )
        )
        self.failure_policy_combo.current(self._failure_policy_keys.index(failure_policy_code))
        self.results_tree.heading("sequence", text=translate("continuous_read_sequence"))
        self.results_tree.heading("value", text=translate("continuous_read_value"))
        self.results_tree.heading("check", text=translate("continuous_read_check"))
        self._refresh_counts()
        self._refresh_status()
        self._render_results()

    def _set_form_enabled(self, enabled: bool) -> None:
        self._form_enabled = bool(enabled)
        state = "normal" if self._form_enabled else "disabled"
        widgets = [
            self.device_addr_entry,
            self.reg_addr_entry,
            self.expected_entry,
            self.interval_entry,
            self.infinite_check,
        ]
        bit_match_check = getattr(self, "bit_match_check", None)
        if bit_match_check is not None:
            widgets.append(bit_match_check)
        for widget in widgets:
            widget.configure(state=state)
        self.width_combo.configure(state="readonly" if self._form_enabled else "disabled")
        mismatch_policy_combo = getattr(self, "mismatch_policy_combo", None)
        if mismatch_policy_combo is not None:
            mismatch_policy_combo.configure(state="readonly" if self._form_enabled else "disabled")
        failure_policy_combo = getattr(self, "failure_policy_combo", None)
        if failure_policy_combo is not None:
            failure_policy_combo.configure(state="readonly" if self._form_enabled else "disabled")
        self.start_button.configure(state="normal" if self._form_enabled else "disabled")
        self._update_count_state()
        update_bit_state = getattr(self, "_update_bit_state", None)
        if callable(update_bit_state):
            update_bit_state()

    def _update_mismatch_policy_selection(self, _event: Any = None) -> None:
        index = self.mismatch_policy_combo.current()
        if 0 <= index < len(self._mismatch_policy_keys):
            self.mismatch_policy_code_var.set(self._mismatch_policy_keys[index])

    def _update_failure_policy_selection(self, _event: Any = None) -> None:
        index = self.failure_policy_combo.current()
        if 0 <= index < len(self._failure_policy_keys):
            self.failure_policy_code_var.set(self._failure_policy_keys[index])

    def _update_count_state(self) -> None:
        enabled = self._form_enabled and not self.infinite_var.get()
        self.count_entry.configure(state="normal" if enabled else "disabled")

    def _update_bit_state(self) -> None:
        bit_index_entry = getattr(self, "bit_index_entry", None)
        expected_bit_combo = getattr(self, "expected_bit_combo", None)
        if bit_index_entry is None or expected_bit_combo is None:
            return
        enabled = self._form_enabled and bool(self.bit_match_var.get())
        bit_index_entry.configure(state="normal" if enabled else "disabled")
        expected_bit_combo.configure(state="readonly" if enabled else "disabled")

    def start_read(self) -> None:
        if not self.infinite_var.get() and not self.count_var.get().strip():
            messagebox.showerror(
                self.home.tr("error"),
                self.home.tr("continuous_read_invalid_settings"),
                parent=self,
            )
            return
        repeat_text = "" if self.infinite_var.get() else self.count_var.get()
        mismatch_policy_var = getattr(self, "mismatch_policy_code_var", None)
        mismatch_policy = mismatch_policy_var.get() if mismatch_policy_var is not None else "continue"
        failure_policy_var = getattr(self, "failure_policy_code_var", None)
        failure_policy = failure_policy_var.get() if failure_policy_var is not None else "continue"
        bit_match_var = getattr(self, "bit_match_var", None)
        bit_match_enabled = bool(bit_match_var.get()) if bit_match_var is not None else False
        bit_index_var = getattr(self, "bit_index_var", None)
        expected_bit_var = getattr(self, "expected_bit_var", None)
        try:
            settings = parse_continuous_read_settings(
                self.device_addr_var.get(),
                self.reg_addr_var.get(),
                self.width_var.get(),
                self.expected_var.get(),
                repeat_text,
                self.interval_var.get(),
                mismatch_policy=mismatch_policy,
                bit_match_enabled=bit_match_enabled,
                bit_index=bit_index_var.get() if bit_index_var is not None else None,
                expected_bit=expected_bit_var.get() if expected_bit_var is not None else None,
                failure_policy=failure_policy,
            )
        except ValueError:
            messagebox.showerror(
                self.home.tr("error"),
                self.home.tr("continuous_read_invalid_settings"),
                parent=self,
            )
            return
        self.home._start_continuous_read(self, settings)

    def begin(self, token: int, settings: ContinuousReadSettings) -> None:
        self.operation_token = token
        self._settings = settings
        self._attempts = 0
        self._success_count = 0
        self._mismatch_count = 0
        self._failure_count = 0
        self._displayed_results.clear()
        self._render_results()
        self._refresh_counts()
        self._waiting_for_previous = False
        self._set_form_enabled(False)
        self.stop_button.configure(state="normal")
        self._set_status("continuous_read_running")

    def wait_for_previous(self) -> None:
        self._waiting_for_previous = True
        self.operation_token = None
        self._set_form_enabled(False)
        self.stop_button.configure(state="disabled")
        self._set_status("continuous_read_waiting")

    def worker_released(self) -> None:
        if not self._waiting_for_previous:
            return
        self._waiting_for_previous = False
        self._set_form_enabled(True)
        self.stop_button.configure(state="disabled")
        self._set_status("continuous_read_ready")

    def mark_stopping(self, _reason: str = "") -> None:
        if self.operation_token is not None:
            self.stop_button.configure(state="disabled")
            self._set_status("continuous_read_stopping")

    def stop_read(self) -> None:
        operation = getattr(self.home, "_continuous_read_operation", None)
        if operation is None or self.operation_token != operation.token:
            return
        self.mark_stopping("user_stopped")
        self.home._cancel_continuous_read("user_stopped", port=operation.port)

    def append_result(self, result: ContinuousReadResult) -> None:
        if self._closed:
            return
        self._attempts += 1
        if result.error:
            self._failure_count += 1
        elif result.matches is False:
            self._mismatch_count += 1
        else:
            self._success_count += 1
        self._displayed_results.append(result)
        trimmed = False
        if len(self._displayed_results) > HomePage.CONTINUOUS_READ_MAX_DISPLAY_ROWS:
            del self._displayed_results[0]
            trimmed = True
        self._refresh_counts()
        if trimmed:
            children = self.results_tree.get_children()
            if children:
                self.results_tree.delete(children[-1])
        self._insert_result_row(result, index=0)
        self.results_tree.yview_moveto(0.0)
        show_result_alerts = getattr(self, "_show_result_alerts", None)
        if callable(show_result_alerts):
            show_result_alerts(result)

    def _render_results(self) -> None:
        if not hasattr(self, "results_tree"):
            return
        self.results_tree.delete(*self.results_tree.get_children())
        for result in reversed(self._displayed_results):
            self._insert_result_row(result)
        self.results_tree.yview_moveto(0.0)

    def _insert_result_row(self, result: ContinuousReadResult, *, index: int | str = "end") -> None:
        translate = lambda key: t(self.home.ctx.state.language, key)
        digits = (self._settings.bits // 4) if self._settings is not None else 4
        if result.value is None:
            value_text = "—"
        else:
            value_text = f"0x{result.value:0{digits}X}"
        if result.error:
            check_text = f"{translate('continuous_read_result_error')}: {result.error}"
            tag = "error"
        elif result.matches is True:
            check_text = translate("continuous_read_result_pass")
            tag = "match"
        elif result.matches is False:
            check_text = translate("continuous_read_result_mismatch")
            tag = "mismatch"
        else:
            check_text = translate("continuous_read_result_unchecked")
            tag = ""
        if (
            result.bit_value is not None
            and self._settings is not None
            and self._settings.bit_index is not None
        ):
            bit_status = translate(
                "continuous_read_bit_match"
                if result.bit_matches is True
                else "continuous_read_bit_mismatch"
            )
            bit_text = translate("continuous_read_bit_result").format(
                index=self._settings.bit_index,
                value=result.bit_value,
                status=bit_status,
            )
            check_text = f"{check_text} | {bit_text}"
            if result.bit_matches is False:
                tag = "mismatch"
            elif result.bit_matches is True and not tag:
                tag = "match"
        self.results_tree.insert(
            "",
            index,
            values=(result.sequence, value_text, check_text),
            tags=(tag,) if tag else (),
        )

    def _refresh_counts(self) -> None:
        if not hasattr(self, "counts_var"):
            return
        self.counts_var.set(
            t(self.home.ctx.state.language, "continuous_read_counts").format(
                attempts=self._attempts,
                success=self._success_count,
                mismatch=self._mismatch_count,
                failure=self._failure_count,
            )
        )

    def _set_status(self, key: str, error: str = "") -> None:
        self._status_key = key
        self._status_error = error
        self._refresh_status()

    def _refresh_status(self) -> None:
        if not hasattr(self, "status_var"):
            return
        text = t(self.home.ctx.state.language, self._status_key)
        if self._status_key == "continuous_read_failed":
            text = text.format(error=self._status_error)
        self.status_var.set(text)

    def _notification_is_current(self, token: int | None) -> bool:
        if self._closed or token is None or self.operation_token != token:
            return False
        if getattr(self.home, "_continuous_read_dialog", None) is not self:
            return False
        operation = getattr(self.home, "_continuous_read_operation", None)
        if (
            operation is None
            or getattr(operation, "token", None) != token
            or getattr(operation, "dialog", None) is not self
            or bool(getattr(operation, "dialog_closed", False))
        ):
            return False
        cancel_event = getattr(operation, "cancel_event", None)
        if cancel_event is not None and cancel_event.is_set():
            return False
        connection_is_current = getattr(self.home, "_continuous_read_connection_is_current", None)
        if callable(connection_is_current):
            try:
                if not connection_is_current(operation):
                    return False
            except Exception:
                return False
        return True

    def _show_result_alerts(self, result: ContinuousReadResult) -> None:
        token = self.operation_token
        settings = self._settings
        if token is None or settings is None or result.value is None:
            return
        translate = lambda key: t(self.home.ctx.state.language, key)
        digits = settings.bits // 4
        device = f"0x{settings.device_addr:02X}"
        register = f"0x{settings.reg_addr:02X}"
        actual = f"0x{result.value:0{digits}X}"
        if result.mismatch_alert and settings.expected_value is not None:
            if not self._notification_is_current(token):
                return
            expected = f"0x{settings.expected_value:0{digits}X}"
            messagebox.showerror(
                translate("continuous_read_mismatch_alert_title"),
                translate("continuous_read_mismatch_alert").format(
                    device=device,
                    register=register,
                    expected=expected,
                    actual=actual,
                ),
                parent=self,
            )
        if (
            result.bit_match_alert
            and result.bit_value is not None
            and settings.bit_index is not None
            and settings.expected_bit is not None
        ):
            if not self._notification_is_current(token):
                return
            messagebox.showinfo(
                translate("continuous_read_bit_match_alert_title"),
                translate("continuous_read_bit_match_alert").format(
                    device=device,
                    register=register,
                    index=settings.bit_index,
                    expected=settings.expected_bit,
                    actual=result.bit_value,
                    value=actual,
                ),
                parent=self,
            )

    def finish(
        self,
        outcome: ContinuousReadOutcome,
        *,
        cancellation_reason: str = "",
    ) -> None:
        self.operation_token = None
        self._set_form_enabled(True)
        self.stop_button.configure(state="disabled")
        if outcome.terminal_error:
            self._set_status("continuous_read_failed", outcome.terminal_error)
        elif cancellation_reason == "connection_changed" or outcome.stop_reason == "connection_changed":
            self._set_status("continuous_read_connection_changed")
        elif outcome.stop_reason == "expected_mismatch":
            self._set_status("continuous_read_stopped_mismatch")
        elif outcome.stop_reason == "completed":
            self._set_status("continuous_read_completed")
        else:
            self._set_status("continuous_read_stopped")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.home._continuous_read_dialog_closed(self)
        try:
            self.destroy()
        except tk.TclError:
            pass


class SettingsPage(BasePage):
    page_name = "settings"

    def __init__(self, parent: tk.Misc, ctx: ApplicationContext) -> None:
        super().__init__(parent, ctx)
        self._language_choices = {"中文": "zh", "English": "en"}
        self._language_labels = {"zh": "中文", "en": "English"}
        self.language_var = tk.StringVar(value=self._language_labels.get(self.ctx.state.language, "English"))
        self._theme_choices = {
            "light（浅色）": "light",
            "dark（深色）": "dark",
            "Auto（自动）": "auto",
        }
        self._theme_labels = {code: label for label, code in self._theme_choices.items()}
        preference = str(self.ctx.state.extra.get("theme_preference", self.ctx.state.theme))
        self.theme_var = tk.StringVar(value=self._theme_labels.get(preference, "light（浅色）"))
        self.font_size_var = tk.IntVar(value=self.ctx.state.font_size)
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)

        header = self.make_title(self, "settings")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        panel = ttk.Frame(self, style="Panel.TFrame", padding=18)
        panel.grid(row=1, column=0, sticky="nsew")
        panel.columnconfigure(1, weight=1)

        self.lang_label = ttk.Label(panel, style="Panel.TLabel")
        self.lang_label.grid(row=0, column=0, sticky="w", padx=(0, 10), pady=8)
        ttk.Combobox(panel, textvariable=self.language_var, values=["中文", "English"], state="readonly").grid(
            row=0, column=1, sticky="w", pady=8
        )

        self.theme_label = ttk.Label(panel, style="Panel.TLabel")
        self.theme_label.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=8)
        ttk.Combobox(
            panel,
            textvariable=self.theme_var,
            values=list(self._theme_choices),
            state="readonly",
            width=16,
        ).grid(
            row=1, column=1, sticky="w", pady=8
        )

        self.font_size_label = ttk.Label(panel, style="Panel.TLabel")
        self.font_size_label.grid(row=2, column=0, sticky="w", padx=(0, 10), pady=8)
        ttk.Combobox(
            panel,
            textvariable=self.font_size_var,
            values=[8, 9, 10, 11, 12, 13, 14, 15, 16, 18],
            state="readonly",
            width=8,
        ).grid(row=2, column=1, sticky="w", pady=8)

        self.apply_btn = ttk.Button(panel, style="Primary.TButton", command=self.apply_settings)
        self.apply_btn.grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 0))

        self.refresh_language()

    def apply_settings(self) -> None:
        language = self._language_choices.get(self.language_var.get(), "en")
        theme = self._theme_choices.get(self.theme_var.get(), "light")
        self.ctx.app.apply_settings(language, theme, self.font_size_var.get())

    def refresh_language(self) -> None:
        super().refresh_language()
        active = self._language_labels.get(self.ctx.state.language, "English")
        if self._language_choices.get(self.language_var.get()) != self.ctx.state.language:
            self.language_var.set(active)
        preference = str(self.ctx.state.extra.get("theme_preference", self.ctx.state.theme))
        theme_label = self._theme_labels.get(preference, "light（浅色）")
        if self._theme_choices.get(self.theme_var.get()) != preference:
            self.theme_var.set(theme_label)
        self.lang_label.configure(text="Language")
        self.theme_label.configure(text="Theme")
        self.font_size_label.configure(text="Font Size 字体大小")
        self.apply_btn.configure(text="Apply")


def _resolve_theme(theme: str) -> str:
    if theme != "auto":
        return theme if theme in {"light", "dark"} else "light"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(value) else "dark"
    except Exception:
        return "light"


class PiezoApplication:
    def __init__(self) -> None:
        self.root = tk.Tk()
        # Let scroll containers handle the wheel without changing a hovered combobox value.
        self.root.bind_class("TCombobox", "<MouseWheel>", lambda _event: None)
        self.root.title("Piezo Control GUI")
        self._apply_window_icon()
        # Start inside conservative screen bounds.  A large fixed
        # minimum made the application impossible to shrink on 1366px and
        # smaller displays, even though the page and navigation canvases can
        # expose their full content with scrollbars.
        screen_width = max(320, int(self.root.winfo_screenwidth()))
        screen_height = max(240, int(self.root.winfo_screenheight()))
        initial_width = min(1700, max(480, screen_width - 80))
        initial_height = min(980, max(360, screen_height - 120))
        self.root.geometry(f"{initial_width}x{initial_height}")
        self.root.minsize(480, 320)
        self._base_width = 1700
        self._base_height = 980
        self._resize_after: str | None = None
        self._last_visual_scale = 1.0
        self._last_root_size: tuple[int, int] = (0, 0)

        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.state = AppState()
        self.serial = SerialRouter(self.state, timeout=0.9)
        self.i2c_bridge = LocalI2cBridge(self.serial._current_manager)
        self.i2c_bridge.start()
        self.colors = apply_theme(self.root, self.state.theme, self.state.font_size, self.state.ui_scale)

        self.ctx = ApplicationContext(
            state=self.state,
            serial=self.serial,
            colors=self.colors,
            app=self,
        )

        self.navigation = Navigation(self.root, self.ctx)

        self._register_pages()
        self._refresh_widget_fonts()
        self.navigation.show("home")
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _apply_window_icon(self) -> None:
        icon_path = Path(__file__).resolve().parents[1] / "assets" / "app_icon.ico"
        if not icon_path.exists():
            return
        try:
            self.root.iconbitmap(default=str(icon_path))
        except Exception:
            pass

    def _register_pages(self) -> None:
        self.navigation.add_page("home", "home", HomePage(self.navigation.content, self.ctx))
        self.navigation.add_page("awp1921_1923", "awp1921_1923", AWP1921AWP1923Page(self.navigation.content, self.ctx))
        self.navigation.add_page("awp1922", "awp1922", AWP1922Page(self.navigation.content, self.ctx))
        self.navigation.add_page("awp1018", "awp1018", AWP1018Page(self.navigation.content, self.ctx))
        self.navigation.add_page("awp1221", "awp1221", AWP1221Page(self.navigation.content, self.ctx))
        self.navigation.add_page(
            "settings",
            "settings",
            SettingsPage(self.navigation.content, self.ctx),
            sidebar_button=False,
        )

    def set_language(self, language: str) -> None:
        if language not in {"zh", "en"}:
            return
        if language == self.state.language:
            return
        self.state.language = language
        self.root.title(t(language, "app_title"))
        self.navigation.refresh_language()

    def set_theme(self, theme: str) -> None:
        if theme not in {"light", "dark"}:
            return
        if theme == self.state.theme:
            return
        self.state.theme = theme
        self._apply_visual_settings()

    def set_font_size(self, font_size: int) -> None:
        try:
            size = int(font_size)
        except Exception:
            return
        next_font_size = max(8, min(18, size))
        if next_font_size == self.state.font_size:
            return
        self.state.font_size = next_font_size
        self._apply_visual_settings()

    def apply_settings(self, language: str, theme: str, font_size: int) -> None:
        language_changed = language in {"zh", "en"} and language != self.state.language
        preference = theme if theme in {"light", "dark", "auto"} else "light"
        resolved = _resolve_theme(preference)
        theme_changed = resolved != self.state.theme or self.state.extra.get("theme_preference") != preference
        try:
            next_font_size = max(8, min(18, int(font_size)))
        except Exception:
            next_font_size = self.state.font_size
        font_changed = next_font_size != self.state.font_size

        if language_changed:
            self.state.language = language
            self.root.title(t(language, "app_title"))
            self.navigation.refresh_language()

        if theme_changed or font_changed:
            if theme_changed:
                self.state.theme = resolved
                self.state.extra["theme_preference"] = preference
            if font_changed:
                self.state.font_size = next_font_size
            self._apply_visual_settings()

    def _window_scale(self) -> float:
        width = max(1, self.root.winfo_width())
        height = max(1, self.root.winfo_height())
        if width <= 1 or height <= 1:
            return 1.0
        ratio = min(width / self._base_width, height / self._base_height)
        return max(0.85, min(1.25, ratio))

    def _apply_visual_settings(self, *, refresh_widget_fonts: bool = True, scale: float | None = None) -> None:
        if scale is None:
            scale = self._window_scale()
        self.state.ui_scale = scale
        self._last_visual_scale = scale
        self._last_applied_font_size = self.state.font_size
        self.colors = apply_theme(self.root, self.state.theme, self.state.font_size, scale)
        self.ctx.colors = self.colors
        self.navigation.refresh_theme()
        if refresh_widget_fonts:
            self._refresh_widget_fonts()

    def _on_root_configure(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        size = (event.width, event.height)
        if size == self._last_root_size:
            return
        self._last_root_size = size
        if self._resize_after is not None:
            try:
                self.root.after_cancel(self._resize_after)
            except tk.TclError:
                pass
        self._resize_after = self.root.after(16, self._refresh_after_resize)

    def _refresh_after_resize(self) -> None:
        self._resize_after = None
        nav = getattr(self, "navigation", None)
        if nav is not None:
            nav._sync_content_window()
            if getattr(self.state, "current_page", "") == "home":
                nav._align_home_panel()

    def _refresh_widget_fonts(self) -> None:
        body_size = int(getattr(self.root, "_piezo_body_font_size", self.state.font_size))
        title_size = int(getattr(self.root, "_piezo_title_font_size", self.state.font_size + 8))
        small_size = max(7, body_size - 1)
        mono_size = int(getattr(self.root, "_piezo_mono_font_size", 9))

        # Resolving font.actual() goes through fontconfig on Linux (~4 ms per
        # call) and this walk visits every widget, so cache by font spec for
        # the duration of this pass.  Identical specs resolve identically.
        flags_cache: dict[str, tuple[bool, bool]] = {}

        def current_font_flags(widget: tk.Misc) -> tuple[bool, bool]:
            try:
                spec = widget.cget("font")
            except Exception:
                return False, False
            key = str(spec)
            cached = flags_cache.get(key)
            if cached is not None:
                return cached
            try:
                font = tkfont.Font(root=self.root, font=spec)
                actual = font.actual()
                result = (actual.get("weight") == "bold", bool(actual.get("underline")))
            except Exception:
                result = (False, False)
            flags_cache[key] = result
            return result

        def apply(widget: tk.Misc) -> None:
            try:
                style_name = widget.cget("style") if isinstance(widget, ttk.Widget) else ""
            except Exception:
                style_name = ""

            bold, underline = current_font_flags(widget)
            font: tuple[Any, ...] | None = None

            if isinstance(widget, tk.Text):
                font = ("Consolas", mono_size)
            elif isinstance(widget, ttk.Label):
                if style_name in {"Wordmark.TLabel", "ChipTitle.TLabel"}:
                    font = None
                elif style_name == "Prominent.TLabel":
                    prominent_size = int(getattr(self.root, "_piezo_prominent_title_font_size", title_size + 1))
                    font = ("Microsoft YaHei UI", prominent_size, "bold", *(() if not underline else ("underline",)))
                elif style_name in {"Title.TLabel", "PanelTitle.TLabel"}:
                    font = ("Microsoft YaHei UI", title_size, "bold", *(() if not underline else ("underline",)))
                elif style_name == "SectionTitle.TLabel":
                    font = ("Microsoft YaHei UI", body_size, "bold", *(() if not underline else ("underline",)))
                elif style_name == "SidebarTitle.TLabel":
                    sidebar_title_size = int(getattr(self.root, "_piezo_sidebar_title_font_size", title_size))
                    font = ("Microsoft YaHei UI", sidebar_title_size, "bold", *(() if not underline else ("underline",)))
                elif style_name == "SidebarVersion.TLabel":
                    font = ("Microsoft YaHei UI", small_size)
                else:
                    font = ("Microsoft YaHei UI", body_size, *(("bold",) if bold else ()), *(("underline",) if underline else ()))
            elif isinstance(widget, (tk.Label, tk.Button, tk.Entry, tk.Spinbox, tk.Checkbutton, tk.Radiobutton)):
                font = ("Microsoft YaHei UI", body_size, *(("bold",) if bold else ()), *(("underline",) if underline else ()))
            elif isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Spinbox, ttk.Checkbutton, ttk.Radiobutton)):
                font = ("Microsoft YaHei UI", body_size, *(("bold",) if bold else ()), *(("underline",) if underline else ()))

            if font is not None:
                try:
                    widget.configure(font=font)
                except tk.TclError:
                    pass

            if isinstance(widget, ttk.Combobox) and font is not None:
                popdown = f"{widget._w}.popdown"
                try:
                    if self.root.tk.call("winfo", "exists", popdown):
                        listbox = f"{popdown}.f.l"
                        if self.root.tk.call("winfo", "exists", listbox):
                            self.root.tk.call(listbox, "configure", "-font", font)
                except tk.TclError:
                    pass

            for child in widget.winfo_children():
                apply(child)

        apply(self.root)

    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        if self._resize_after is not None:
            try:
                self.root.after_cancel(self._resize_after)
            except tk.TclError:
                pass
            self._resize_after = None
        home_page = getattr(getattr(self, "navigation", None), "pages", {}).get("home")
        cancel_continuous_read = getattr(home_page, "cancel_continuous_read", None)
        if callable(cancel_continuous_read):
            cancel_continuous_read("application_closing")
        self._clear_waveform_cache_files()
        try:
            self.i2c_bridge.close()
        except Exception:
            pass
        try:
            self.serial.clear_log()
            if hasattr(self.serial, "disconnect_all"):
                self.serial.disconnect_all()
            elif self.serial.is_connected:
                self.serial.disconnect()
        except Exception:
            pass
        self.root.destroy()

    def _clear_waveform_cache_files(self) -> None:
        temp_dir = Path(tempfile.gettempdir())
        for path in temp_dir.glob("piezo_gui_awp192x_*_sram_cache.json"):
            try:
                path.unlink()
            except Exception:
                pass
