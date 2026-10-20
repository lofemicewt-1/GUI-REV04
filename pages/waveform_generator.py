from __future__ import annotations

import json
import sys
import tempfile
import threading
import tkinter as tk
from contextlib import nullcontext
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.waveform_engine import WaveformEngine
from core.waveform_storage import SliceInfo, WaveformStorage
from core.base_tool import WREGW_MAX_VALUES
from core.awp192x_button_runtime import (
    AWP192X_GENERATOR_SINC,
    AWP192X_GENERATOR_SINC_VERSION,
    Awp192xSineWaveformRecipe,
    Awp192xWaveformRecipe,
    generate_awp192x_waveform_points,
)
from pages.base_page import BasePage
from pages.waveform_preview import PreviewSegment, compact_preview, draw_omissions
from pages.waveform_layout import WaveformWidthClamp


ARDUINO_I2C_TRANSACTION_BYTES = 31
WAVEFORM_I2C_MAX_POINTS_PER_WRITE = (ARDUINO_I2C_TRANSACTION_BYTES - 1) // 2
WAVEFORM_SERIAL_MAX_POINTS_PER_WRITE = WREGW_MAX_VALUES[16]
WAVEFORM_MAX_POINTS_PER_WRITE = min(
    WAVEFORM_I2C_MAX_POINTS_PER_WRITE,
    WAVEFORM_SERIAL_MAX_POINTS_PER_WRITE,
)
WAVEFORM_WRITE_POINT_OPTIONS = tuple(range(1, WAVEFORM_MAX_POINTS_PER_WRITE + 1))
WAVEFORM_DEFAULT_POINTS_PER_WRITE = 3


def parse_waveform_values_text(text: str) -> list[int]:
    tokens = str(text).replace(",", " ").split()
    if not tokens:
        raise ValueError("waveform file is empty")
    values = [int(token, 16) for token in tokens]
    if len(values) > 1024:
        raise ValueError("imported waveform supports up to 1024 points")
    if any(value < 0 or value > 0x0FFF for value in values):
        raise ValueError("all waveform values must be 0x000-0xFFF")
    return values


def format_waveform_values_text(values: list[int]) -> str:
    normalized = [int(value) for value in values]
    if not normalized:
        raise ValueError("waveform has no points to export")
    if len(normalized) > 1024:
        raise ValueError("waveform export supports up to 1024 points")
    if any(value < 0 or value > 0x0FFF for value in normalized):
        raise ValueError("all waveform values must be 0x000-0xFFF")
    return "".join(f"{value:04X}\n" for value in normalized)


def compare_waveform_values(
    *,
    expected_values: list[int],
    actual_values: list[int],
    start_addr: int,
) -> list[tuple[int, int, int, int]]:
    """Return (zero-based offset, SRAM address, expected, actual) mismatches."""
    if len(expected_values) != len(actual_values):
        raise ValueError("expected and actual waveform lengths must match")

    return [
        (offset, int(start_addr) + offset, int(expected), int(actual))
        for offset, (expected, actual) in enumerate(zip(expected_values, actual_values))
        if int(expected) != int(actual)
    ]


def append_zero_fill_tail(
    *,
    values: list[int],
    voltages: list[float],
    start_addr: int,
    sram_end: int,
) -> tuple[list[int], list[float], int]:
    current_end = int(start_addr) + len(values)
    target_end = max(current_end, int(sram_end))
    pad_count = target_end - current_end
    if pad_count <= 0:
        return list(values), list(voltages), current_end
    return (
        list(values) + [0x0800] * pad_count,
        list(voltages) + [0.0] * pad_count,
        target_end,
    )


class WaveformGeneratorPage(BasePage):
    page_name = "waveform_generator"
    SRAM_DISPLAY_POINTS = 1024
    SRAM_INDEX_TABLE_END = 0x003C
    SRAM_READ_BATCH_POINTS = 16

    def __init__(
        self,
        parent: tk.Misc,
        ctx: Any,
        *,
        embedded: bool = False,
        storage: WaveformStorage | None = None,
        positive_only: bool = False,
        slice_readout_width: int | None = None,
    ) -> None:
        super().__init__(parent, ctx)
        self.embedded = embedded
        self.positive_only = positive_only
        self.slice_readout_width = slice_readout_width
        self.engine = WaveformEngine(voltage_range=95.0)
        self.storage = storage if storage is not None else WaveformStorage()

        self.waveform_type_var = tk.StringVar(value="sine")
        self.slice_var = tk.IntVar(value=0)
        self.points_var = tk.StringVar(value="256")
        self.start_phase_var = tk.StringVar(value="270" if positive_only else "0")
        self.end_phase_var = tk.StringVar(value="630" if positive_only else "360")
        self.offset_var = tk.StringVar(value="0")
        self.voltage_amplitude_var = tk.StringVar(value="95")
        self.sinc_positive_voltage_var = tk.StringVar(value="95")
        self.sinc_negative_voltage_var = tk.StringVar(value="-95")
        self.sinc_points1_var = tk.StringVar(value="85")
        self.sinc_points2_var = tk.StringVar(value="86")
        self.sinc_points3_var = tk.StringVar(value="85")
        self.start_addr_var = tk.StringVar(value="0x0002")
        self.end_addr_var = tk.StringVar(value="0x0100")
        self.serial_slice_vars = [tk.StringVar(value="slice0")] + [
            tk.StringVar(value="0x1F") for _index in range(7)
        ]
        self.serial_repeat_vars = [tk.StringVar(value="1") for _index in range(8)]
        self.metrics_revision_var = tk.IntVar(value=0)
        self.fill_zero_tail_var = tk.BooleanVar(value=False)
        self.write_points_per_batch_var = tk.StringVar(value=str(WAVEFORM_DEFAULT_POINTS_PER_WRITE))

        self._last_values: list[int] = []
        self._last_voltages: list[float] = []
        self._imported_values: list[int] | None = None
        self._preview_imported_waveform = False
        self._plot_mode = "current"
        self._chip_voltages: list[float | None] = []
        self._chip_values_cache: list[int | None] | None = None
        self._waveform_write_busy = False
        self._waveform_write_observer: tuple[Any, Any] | None = None

        self._labels: dict[str, ttk.Label] = {}
        self._field_widgets: dict[str, list[tk.Widget]] = {}
        self._buttons: dict[str, ttk.Button] = {}

        self._build_ui()
        self._update_end_addr()
        self._preview_waveform()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        if not self.embedded:
            header = self.make_title(self, "waveform_generator")
            header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())

        body = ttk.Frame(self, style="App.TFrame")
        self._body_layout_frame = body
        body.grid(row=1 if not self.embedded else 0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=0)
        body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)
        ttk.Frame(body, style="App.TFrame").grid(row=0, column=2, sticky="nsew")

        controls = ttk.Frame(body, style="Panel.TFrame", padding=14)
        controls.grid(row=0, column=0, sticky="nsw", padx=(0, 12))

        plot_panel = ttk.Frame(body, style="Panel.TFrame", padding=10)
        plot_panel.grid(row=0, column=1, sticky="nsew")
        plot_panel.rowconfigure(1, weight=1)
        plot_panel.columnconfigure(0, weight=1)

        row = 0
        self._add_labeled_combobox(
            controls,
            row,
            "waveform_type",
            self.waveform_type_var,
            values=["sine", "square", "triangle", "dc", "sinc", "gauss", "imported", "serial"],
        )
        row += 1

        self._add_labeled_spinbox(controls, row, "slice_index", self.slice_var, 0, 29)
        row += 1

        self.serial_editor = self._build_serial_editor(controls)
        self.serial_editor.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(2, 4))
        self.serial_editor.grid_remove()

        self._add_labeled_entry(controls, row, "points", self.points_var)
        row += 1

        self._add_labeled_entry(controls, row, "start_phase", self.start_phase_var, unit="\u00b0")
        row += 1

        self._add_labeled_entry(controls, row, "end_phase", self.end_phase_var, unit="\u00b0")
        row += 1

        self._add_labeled_entry(controls, row, "offset", self.offset_var, unit="V")
        row += 1

        self._add_labeled_entry(controls, row, "voltage_amplitude", self.voltage_amplitude_var, unit="V")
        row += 1

        self._add_labeled_entry(controls, row, "sinc_positive_voltage", self.sinc_positive_voltage_var, unit="V")
        row += 1

        self._add_labeled_entry(controls, row, "sinc_negative_voltage", self.sinc_negative_voltage_var, unit="V")
        row += 1

        self._add_labeled_entry(controls, row, "sinc_points1", self.sinc_points1_var)
        row += 1

        self._add_labeled_entry(controls, row, "sinc_points2", self.sinc_points2_var)
        row += 1

        self._add_labeled_entry(controls, row, "sinc_points3", self.sinc_points3_var)
        row += 1

        self._add_labeled_entry(controls, row, "start_addr", self.start_addr_var)
        row += 1

        self._add_labeled_entry(controls, row, "end_addr", self.end_addr_var, readonly=True)
        row += 1

        self._add_labeled_combobox(
            controls,
            row,
            "write_points_per_batch",
            self.write_points_per_batch_var,
            values=[str(value) for value in WAVEFORM_WRITE_POINT_OPTIONS],
        )
        row += 1

        fill_check = ttk.Checkbutton(controls, variable=self.fill_zero_tail_var)
        fill_check.grid(row=row, column=0, columnspan=3, sticky="w", pady=5)
        self.bind_text(fill_check, "fill_zero_tail")
        self._field_widgets["fill_zero_tail"] = [fill_check]
        row += 1

        btn_frame = ttk.Frame(controls, style="Panel.TFrame")
        btn_frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        btn_frame.columnconfigure(2, weight=1)

        write_btn = ttk.Button(btn_frame, style="Primary.TButton", command=self.write_waveform)
        write_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["write_waveform"] = write_btn

        import_btn = ttk.Button(btn_frame, style="Secondary.TButton", command=self.import_waveform)
        import_btn.grid(row=0, column=1, sticky="ew", padx=6)
        self._buttons["import_waveform"] = import_btn

        export_btn = ttk.Button(btn_frame, style="Secondary.TButton", command=self.export_waveform)
        export_btn.grid(row=0, column=2, sticky="ew", padx=(6, 0))
        self._buttons["export_waveform"] = export_btn

        row += 1

        import_write_btn = ttk.Button(controls, style="Primary.TButton", command=self.write_imported_waveform)
        import_write_btn.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._buttons["write_imported_waveform"] = import_write_btn

        row += 1

        verify_btn = ttk.Button(
            controls,
            style="Secondary.TButton",
            command=self.verify_written_waveform,
        )
        verify_btn.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._buttons["verify_written_waveform"] = verify_btn
        self.verify_waveform_button = verify_btn

        row += 1

        self.canvas = tk.Canvas(
            plot_panel,
            bg=self.colors["canvas_bg"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
        )
        plot_toolbar = ttk.Frame(plot_panel, style="Panel.TFrame")
        plot_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.plot_toolbar = plot_toolbar
        plot_toolbar.columnconfigure(0, weight=1)
        plot_toolbar.columnconfigure(1, weight=1)

        show_current_btn = ttk.Button(plot_toolbar, style="Secondary.TButton", command=self.show_current_waveform)
        show_current_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["show_current_waveform"] = show_current_btn

        read_btn = ttk.Button(plot_toolbar, style="Secondary.TButton", command=self.show_chip_waveform)
        read_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._buttons["show_chip_waveform"] = read_btn

        self.canvas.grid(row=1, column=0, sticky="nsew")

        readout_options: dict[str, Any] = {
            "height": 6,
            "bg": self.colors["panel"],
            "fg": self.colors["text"],
            "insertbackground": self.colors["text"],
            "relief": "flat",
            "highlightthickness": 1,
            "highlightbackground": self.colors["border"],
            "wrap": "none",
        }
        if self.slice_readout_width is not None:
            readout_options["width"] = self.slice_readout_width
        self.read_slices_text = tk.Text(plot_panel, **readout_options)
        self.read_slices_text.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.read_slices_scrollbar = ttk.Scrollbar(
            plot_panel,
            orient="vertical",
            command=self.read_slices_text.yview,
        )
        self.read_slices_scrollbar.grid(row=2, column=1, sticky="ns", pady=(10, 0))
        self.read_slices_text.configure(yscrollcommand=self.read_slices_scrollbar.set)
        self.read_slices_xscrollbar: ttk.Scrollbar | None = None
        if self.slice_readout_width is not None:
            self.read_slices_xscrollbar = ttk.Scrollbar(
                plot_panel,
                orient="horizontal",
                command=self.read_slices_text.xview,
            )
            self.read_slices_xscrollbar.grid(row=3, column=0, sticky="ew")
            self.read_slices_text.configure(xscrollcommand=self.read_slices_xscrollbar.set)

        for var in [
            self.waveform_type_var,
            self.points_var,
            self.start_phase_var,
            self.end_phase_var,
            self.offset_var,
            self.voltage_amplitude_var,
            self.sinc_positive_voltage_var,
            self.sinc_negative_voltage_var,
            self.sinc_points1_var,
            self.sinc_points2_var,
            self.sinc_points3_var,
            self.start_addr_var,
            self.fill_zero_tail_var,
        ]:
            var.trace_add("write", lambda *_: self._on_param_changed())
        for var in [*self.serial_slice_vars, *self.serial_repeat_vars]:
            var.trace_add("write", lambda *_: self._on_serial_param_changed())

        self._plot_redraw_job: str | None = None
        self._plot_drawn_size: tuple[int, int] | None = None
        self.canvas.bind("<Configure>", self._queue_plot_redraw)
        self.bind("<Destroy>", self._cancel_plot_redraw, add="+")
        self._waveform_width_clamp = WaveformWidthClamp(
            self,
            body,
            plot_panel,
            reserved_width=lambda: int(controls.winfo_reqwidth()) + 12,
        )

        self.refresh_language()
        self._update_waveform_specific_fields()

    def refresh_layout(self) -> None:
        super().refresh_layout()
        clamp = getattr(self, "_waveform_width_clamp", None)
        if clamp is not None:
            clamp.refresh()

    def _add_labeled_entry(
        self,
        parent: tk.Misc,
        row: int,
        key: str,
        variable: tk.StringVar,
        *,
        readonly: bool = False,
        unit: str = "",
    ) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=5, padx=(0, 8))
        self._labels[key] = label

        entry = ttk.Entry(parent, textvariable=variable, width=12)
        if readonly:
            entry.configure(state="readonly")
        entry.grid(row=row, column=1, sticky="ew", pady=5)

        widgets: list[tk.Widget] = [label, entry]
        if unit:
            unit_label = ttk.Label(parent, text=unit, style="Muted.TLabel")
            unit_label.grid(row=row, column=2, sticky="w", padx=(6, 0), pady=5)
            widgets.append(unit_label)

        self._field_widgets[key] = widgets

    def _add_labeled_spinbox(
        self,
        parent: tk.Misc,
        row: int,
        key: str,
        variable: tk.IntVar,
        from_: int,
        to: int,
    ) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=5, padx=(0, 8))
        self._labels[key] = label

        spin = ttk.Spinbox(parent, from_=from_, to=to, textvariable=variable, width=12)
        spin.grid(row=row, column=1, sticky="ew", pady=5)
        self._field_widgets[key] = [label, spin]

    def _add_labeled_combobox(
        self,
        parent: tk.Misc,
        row: int,
        key: str,
        variable: tk.StringVar,
        values: list[str],
    ) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=5, padx=(0, 8))
        self._labels[key] = label

        combo = ttk.Combobox(parent, textvariable=variable, values=values, width=12, state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=5)
        self._field_widgets[key] = [label, combo]

    def refresh_language(self) -> None:
        super().refresh_language()
        for key, label in self._labels.items():
            label.configure(text=self.tr(key))

        for key, btn in self._buttons.items():
            btn.configure(text=self.tr(key))
        self._refresh_write_button_text()

    def refresh_theme(self) -> None:
        super().refresh_theme()
        if hasattr(self, "canvas"):
            self.canvas.configure(
                bg=self.colors["canvas_bg"],
                highlightbackground=self.colors["border"],
            )
        if hasattr(self, "read_slices_text"):
            self.read_slices_text.configure(
                bg=self.colors["panel"],
                fg=self.colors["text"],
                insertbackground=self.colors["text"],
                highlightbackground=self.colors["border"],
            )
        self._preview_waveform()

    def _on_param_changed(self) -> None:
        self._update_waveform_specific_fields()
        self._update_end_addr()
        self._notify_metrics_changed()
        if self._plot_mode == "current" and self.waveform_type_var.get() != "serial":
            self._preview_waveform()

    def _on_serial_param_changed(self) -> None:
        self._notify_metrics_changed()
        if self.waveform_type_var.get() == "serial":
            self._plot_mode = "current"
            self._preview_serial_waveform()

    def _notify_metrics_changed(self) -> None:
        self.metrics_revision_var.set(self.metrics_revision_var.get() + 1)

    def _update_waveform_specific_fields(self) -> None:
        waveform_type = self.waveform_type_var.get()

        all_param_keys = [
            "points",
            "start_phase",
            "end_phase",
            "offset",
            "voltage_amplitude",
            "sinc_positive_voltage",
            "sinc_negative_voltage",
            "sinc_points1",
            "sinc_points2",
            "sinc_points3",
            "start_addr",
            "end_addr",
            "write_points_per_batch",
            "fill_zero_tail",
        ]

        if waveform_type == "serial":
            visible = set()
        elif waveform_type == "dc":
            visible = {
                "points",
                "offset",
                "start_addr",
                "end_addr",
                "write_points_per_batch",
                "fill_zero_tail",
            }
        elif waveform_type == "sinc":
            visible = {
                "sinc_positive_voltage",
                "sinc_negative_voltage",
                "sinc_points1",
                "sinc_points2",
                "sinc_points3",
                "start_addr",
                "end_addr",
                "write_points_per_batch",
                "fill_zero_tail",
            }
        elif waveform_type == "imported":
            visible = {
                "points",
                "start_addr",
                "end_addr",
                "write_points_per_batch",
                "fill_zero_tail",
            }
        else:
            visible = {
                "points",
                "start_phase",
                "end_phase",
                "offset",
                "voltage_amplitude",
                "start_addr",
                "end_addr",
                "write_points_per_batch",
                "fill_zero_tail",
            }

        for key in all_param_keys:
            for widget in self._field_widgets.get(key, []):
                if key in visible:
                    widget.grid()
                else:
                    widget.grid_remove()

        for widget in self._field_widgets.get("slice_index", []):
            if waveform_type == "serial":
                widget.grid_remove()
            else:
                widget.grid()

        if hasattr(self, "serial_editor"):
            if waveform_type == "serial":
                self.read_slices_text.grid_remove()
                self.read_slices_scrollbar.grid_remove()
                if self.read_slices_xscrollbar is not None:
                    self.read_slices_xscrollbar.grid_remove()
                self.serial_editor.grid()
                self.verify_waveform_button.grid_remove()
                self._plot_mode = "current"
                self._preview_serial_waveform()
            else:
                self.serial_editor.grid_remove()
                self.read_slices_text.grid()
                self.read_slices_scrollbar.grid()
                if self.read_slices_xscrollbar is not None:
                    self.read_slices_xscrollbar.grid()
                self.verify_waveform_button.grid()
        self._refresh_write_button_text()

    def _refresh_write_button_text(self) -> None:
        button = self._buttons.get("write_waveform")
        if button is None:
            return
        key = "write_serial_config" if self.waveform_type_var.get() == "serial" else "write_waveform"
        button.configure(text=self.tr(key))

    def _build_serial_editor(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)

        ttk.Label(frame, text="Serial", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        slice_header = ttk.Label(frame, style="Panel.TLabel")
        slice_header.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.bind_text(slice_header, "serial_slice")
        repeat_header = ttk.Label(frame, style="Panel.TLabel")
        repeat_header.grid(row=0, column=2, sticky="w")
        self.bind_text(repeat_header, "serial_repeat")

        slice_options = [f"slice{index}" for index in range(30)] + ["0x1E", "0x1F"]
        repeat_options = [str(count) for count in range(1, 9)]
        for index, (slice_var, repeat_var) in enumerate(zip(self.serial_slice_vars, self.serial_repeat_vars)):
            ttk.Label(frame, text=f"serial{index}", style="Panel.TLabel").grid(
                row=index + 1,
                column=0,
                sticky="w",
                pady=4,
                padx=(0, 8),
            )
            ttk.Combobox(
                frame,
                textvariable=slice_var,
                values=slice_options,
                state="readonly",
                width=11,
            ).grid(row=index + 1, column=1, sticky="ew", pady=4, padx=(0, 8))
            ttk.Combobox(
                frame,
                textvariable=repeat_var,
                values=repeat_options,
                state="readonly",
                width=6,
            ).grid(row=index + 1, column=2, sticky="ew", pady=4)
        return frame

    def _parse_serial_slice_code(self, text: str) -> int:
        value = text.strip().lower()
        if value.startswith("slice"):
            code = int(value.replace("slice", "", 1), 10)
        else:
            code = int(value, 0)
        if 0 <= code <= 0x1F:
            return code
        raise ValueError("serial slice must be slice0-slice29, 0x1E, or 0x1F")

    def build_serial_register_values(self) -> list[tuple[int, int]]:
        codes = [self._parse_serial_slice_code(var.get()) for var in self.serial_slice_vars]
        repeats = [int(var.get(), 0) for var in self.serial_repeat_vars]
        if any(not 1 <= repeat <= 8 for repeat in repeats):
            raise ValueError("serial repeat count must be 1-8")

        values: list[tuple[int, int]] = []
        for pair_index, reg in enumerate(range(0x17, 0x1B)):
            first = pair_index * 2
            low = ((codes[first] & 0x1F) << 3) | ((repeats[first] - 1) & 0x07)
            high = ((codes[first + 1] & 0x1F) << 11) | (((repeats[first + 1] - 1) & 0x07) << 8)
            values.append((reg, low | high))
        return values

    def effective_points(self) -> int:
        if self.waveform_type_var.get() != "serial":
            if self._zero_tail_fill_enabled():
                start_addr = self._parse_int_auto(self.start_addr_var.get())
                return max(0, self.storage.SRAM_MAX + 1 - start_addr)
            return int(self.points_var.get())

        total = 0
        slices = self.storage.slices
        for slice_var, repeat_var in zip(self.serial_slice_vars, self.serial_repeat_vars):
            code = self._parse_serial_slice_code(slice_var.get())
            if code >= 0x1E:
                break
            item = slices.get(code)
            if item is not None:
                total += item.points * int(repeat_var.get(), 0)
        return total

    def _parse_int_auto(self, text: str) -> int:
        return int(text.strip(), 0)

    def _read_params(self) -> tuple[str, int, int, float, float, float, float, float, float, int, int, int, int, int]:
        waveform_type = self.waveform_type_var.get()
        slice_index = int(self.slice_var.get())
        points = int(self.points_var.get())
        start_phase = float(self.start_phase_var.get())
        end_phase = float(self.end_phase_var.get())
        offset = float(self.offset_var.get())
        amplitude = abs(float(self.voltage_amplitude_var.get()))

        sinc_positive = float(self.sinc_positive_voltage_var.get())
        sinc_negative = float(self.sinc_negative_voltage_var.get())
        sinc_points1 = int(self.sinc_points1_var.get())
        sinc_points2 = int(self.sinc_points2_var.get())
        sinc_points3 = int(self.sinc_points3_var.get())

        if waveform_type == "sinc":
            points = sinc_points1 + sinc_points2 + sinc_points3

        start_addr = self._parse_int_auto(self.start_addr_var.get())
        end_addr = self.storage.make_end_addr(start_addr, points)
        self.storage.validate_range(start_addr, end_addr)
        return (
            waveform_type,
            slice_index,
            points,
            start_phase,
            end_phase,
            offset,
            amplitude,
            sinc_positive,
            sinc_negative,
            sinc_points1,
            sinc_points2,
            sinc_points3,
            start_addr,
            end_addr,
        )

    def _power_on_recipe_from_params(
        self,
        *,
        waveform_type: str,
        slice_index: int,
        start_addr: int,
        points: int,
        start_phase: float,
        end_phase: float,
        offset: float,
        amplitude: float,
        sinc_positive: float,
        sinc_negative: float,
        sinc_points1: int,
        sinc_points2: int,
        sinc_points3: int,
    ) -> Awp192xWaveformRecipe | Awp192xSineWaveformRecipe | None:
        if self.positive_only or self._zero_tail_fill_enabled():
            return None
        if waveform_type == "sinc":
            return Awp192xWaveformRecipe(
                generator_type=AWP192X_GENERATOR_SINC,
                generator_version=AWP192X_GENERATOR_SINC_VERSION,
                slice_index=slice_index,
                start_addr=start_addr,
                points1=sinc_points1,
                points2=sinc_points2,
                points3=sinc_points3,
                positive_mv=round(sinc_positive * 1000),
                negative_mv=round(sinc_negative * 1000),
            )
        if waveform_type != "sine":
            return None
        if not float(start_phase).is_integer() or not float(end_phase).is_integer():
            return None
        amplitude_mv = round(min(abs(amplitude), self.engine.voltage_range) * 1000)
        offset_mv = round(offset * 1000)
        if (
            abs(amplitude * 1000 - round(amplitude * 1000)) > 1e-6
            or abs(offset * 1000 - offset_mv) > 1e-6
        ):
            return None
        start_degree = int(start_phase)
        phase_span = int(end_phase) - start_degree
        normalized_start = start_degree % 360
        return Awp192xSineWaveformRecipe(
            slice_index=slice_index,
            start_addr=start_addr,
            points=points,
            start_phase_deg=normalized_start,
            end_phase_deg=normalized_start + phase_span,
            amplitude_mv=amplitude_mv,
            offset_mv=offset_mv,
        )

    def _power_on_recipe_for_values(
        self,
        *,
        waveform_type: str,
        slice_index: int,
        start_addr: int,
        values: list[int],
    ) -> Awp192xWaveformRecipe | Awp192xSineWaveformRecipe | None:
        try:
            params = self._read_params()
            recipe = self._power_on_recipe_from_params(
                waveform_type=waveform_type,
                slice_index=slice_index,
                start_addr=start_addr,
                points=params[2],
                start_phase=params[3],
                end_phase=params[4],
                offset=params[5],
                amplitude=params[6],
                sinc_positive=params[7],
                sinc_negative=params[8],
                sinc_points1=params[9],
                sinc_points2=params[10],
                sinc_points3=params[11],
            )
            if recipe is None or list(generate_awp192x_waveform_points(recipe)) != list(values):
                return None
            return recipe
        except (TypeError, ValueError):
            return None

    def _update_end_addr(self) -> None:
        try:
            if self.waveform_type_var.get() == "sinc":
                points = int(self.sinc_points1_var.get()) + int(self.sinc_points2_var.get()) + int(self.sinc_points3_var.get())
                if self.points_var.get() != str(points):
                    self.points_var.set(str(points))
            else:
                points = int(self.points_var.get())
            start_addr = self._parse_int_auto(self.start_addr_var.get())
            end_addr = self.storage.make_end_addr(start_addr, points)
            if self._zero_tail_fill_enabled():
                end_addr = self.storage.SRAM_MAX
            self.end_addr_var.set(f"0x{end_addr:04X}")
        except Exception:
            pass

    def _generate_current(self, *, use_imported: bool = True) -> tuple[list[int], list[float], int, int, int, str]:
        (
            waveform_type,
            slice_index,
            points,
            start_phase,
            end_phase,
            offset,
            amplitude,
            sinc_positive,
            sinc_negative,
            sinc_points1,
            sinc_points2,
            sinc_points3,
            start_addr,
            end_addr,
        ) = self._read_params()

        if use_imported and self._imported_values is not None:
            values = self._select_imported_values(start_addr, end_addr, points)
            if len(values) != points:
                points = len(values)
                end_addr = self.storage.make_end_addr(start_addr, points)
                self.points_var.set(str(points))
                self.end_addr_var.set(f"0x{end_addr:04X}")
            voltages = [self.engine.map_dac_to_voltage(v) for v in values]
            values, voltages, end_addr = self._maybe_append_zero_tail_fill(values, voltages, start_addr, end_addr)
            return values, voltages, slice_index, start_addr, end_addr, "imported"

        power_on_recipe = self._power_on_recipe_from_params(
            waveform_type=waveform_type,
            slice_index=slice_index,
            start_addr=start_addr,
            points=points,
            start_phase=start_phase,
            end_phase=end_phase,
            offset=offset,
            amplitude=amplitude,
            sinc_positive=sinc_positive,
            sinc_negative=sinc_negative,
            sinc_points1=sinc_points1,
            sinc_points2=sinc_points2,
            sinc_points3=sinc_points3,
        )
        if isinstance(power_on_recipe, Awp192xSineWaveformRecipe):
            values = list(generate_awp192x_waveform_points(power_on_recipe))
            voltages = [self.engine.map_dac_to_voltage(value) for value in values]
            return values, voltages, slice_index, start_addr, end_addr, waveform_type

        result = self.engine.generate(
            waveform_type=waveform_type,
            points=points,
            start_phase_deg=start_phase,
            end_phase_deg=end_phase,
            offset=0.0 if self.positive_only else offset,
            amplitude=amplitude,
            sinc_vmax=sinc_positive,
            sinc_vmin=sinc_negative,
            sinc_points1=sinc_points1,
            sinc_points2=sinc_points2,
            sinc_points3=sinc_points3,
            gauss_peak=amplitude,
        )
        if self.positive_only:
            voltages = self._make_positive_only_voltages(result.voltages, waveform_type, amplitude, offset)
            values = [self.engine.map_voltage_to_dac(voltage) for voltage in voltages]
            values, voltages, end_addr = self._maybe_append_zero_tail_fill(values, voltages, start_addr, end_addr)
            return values, voltages, slice_index, start_addr, end_addr, waveform_type
        values, voltages, end_addr = self._maybe_append_zero_tail_fill(result.values, result.voltages, start_addr, end_addr)
        return values, voltages, slice_index, start_addr, end_addr, waveform_type

    def _zero_tail_fill_enabled(self) -> bool:
        variable = getattr(self, "fill_zero_tail_var", None)
        return bool(variable is not None and variable.get())

    def _maybe_append_zero_tail_fill(
        self,
        values: list[int],
        voltages: list[float],
        start_addr: int,
        end_addr: int,
    ) -> tuple[list[int], list[float], int]:
        if not self._zero_tail_fill_enabled():
            return values, voltages, end_addr
        values, voltages, end_addr = append_zero_fill_tail(
            values=values,
            voltages=voltages,
            start_addr=start_addr,
            sram_end=self.storage.SRAM_MAX + 1,
        )
        self.end_addr_var.set(f"0x{min(end_addr, self.storage.SRAM_MAX):04X}")
        return values, voltages, end_addr

    def _make_positive_only_voltages(
        self,
        voltages: list[float],
        waveform_type: str,
        amplitude: float,
        offset: float,
    ) -> list[float]:
        if waveform_type in {"sine", "square", "triangle"}:
            shifted = [(voltage + amplitude) / 2.0 + offset for voltage in voltages]
        else:
            shifted = [voltage + offset for voltage in voltages]
        return [max(0.0, min(self.engine.voltage_range, voltage)) for voltage in shifted]

    def _select_imported_values(self, start_addr: int, end_addr: int, points: int) -> list[int]:
        if self._imported_values is None:
            return []

        if end_addr <= len(self._imported_values):
            values = self._imported_values[start_addr:end_addr]
        else:
            values = self._imported_values[:points]

        if not values:
            raise ValueError("imported waveform has no data in the selected address range")
        return values

    def _preview_waveform(self) -> None:
        if not hasattr(self, "canvas"):
            return
        if self.waveform_type_var.get() == "serial":
            self._preview_serial_waveform()
            return

        try:
            values, voltages, _slice_index, start_addr, _end_addr, waveform_type = self._generate_current(
                use_imported=self._preview_imported_waveform
            )
            self._last_values = values
            self._last_voltages = voltages
        except Exception:
            values = self._last_values
            voltages = self._last_voltages
            start_addr = self._safe_start_addr()
            waveform_type = "imported" if self._preview_imported_waveform else self.waveform_type_var.get()

        self._plot_mode = "current"
        skip_index_table = waveform_type == "imported" and self._imported_values is not None and len(self._imported_values) >= self.SRAM_DISPLAY_POINTS
        self._draw_plot(self._make_address_voltages(voltages, start_addr, values=values, skip_index_table=skip_index_table))

    def _cancel_plot_redraw(self, event: tk.Event | None = None) -> None:
        if event is not None and event.widget is not self:
            return
        job = self._plot_redraw_job
        self._plot_redraw_job = None
        if job is None:
            return
        try:
            self.after_cancel(job)
        except tk.TclError:
            pass

    def _queue_plot_redraw(self, _event: tk.Event | None = None) -> None:
        if self._plot_redraw_job is not None:
            try:
                self.after_cancel(self._plot_redraw_job)
            except tk.TclError:
                pass
            self._plot_redraw_job = None
        try:
            self._plot_redraw_job = self.after(16, self._redraw_active_plot)
        except tk.TclError:
            self._plot_redraw_job = None

    def _redraw_active_plot(self) -> None:
        self._plot_redraw_job = None
        try:
            if not self.canvas.winfo_exists():
                return
            size = (int(self.canvas.winfo_width()), int(self.canvas.winfo_height()))
        except tk.TclError:
            return
        if size[0] <= 1 or size[1] <= 1 or size == self._plot_drawn_size:
            return
        if self.waveform_type_var.get() == "serial":
            self._draw_serial_plot(self._serial_preview_segments())
            return
        samples = getattr(self, "_last_plot_samples", None)
        if samples:
            self._draw_plot(samples)
            return
        self._preview_waveform()

    def show_current_waveform(self) -> None:
        self._plot_mode = "current"
        self._preview_waveform()

    def _preview_serial_waveform(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self._draw_serial_plot(self._serial_preview_segments())

    def _serial_preview_segments(self) -> list[PreviewSegment]:
        segments: list[PreviewSegment] = []
        cycles: dict[int, list[float]] = {}
        for serial_index, (slice_var, repeat_var) in enumerate(
            zip(self.serial_slice_vars, self.serial_repeat_vars)
        ):
            try:
                slice_code = self._parse_serial_slice_code(slice_var.get())
                repeat_count = int(repeat_var.get(), 0)
            except ValueError:
                continue
            if slice_code >= 0x1E:
                break

            if slice_code not in cycles:
                cycles[slice_code] = self._serial_slice_voltages(slice_code)
            voltages = cycles[slice_code]
            if not voltages:
                continue
            segments.append(PreviewSegment(f"serial{serial_index}: slice{slice_code} x{repeat_count}",
                                           voltages, max(1, min(8, repeat_count))))
        return segments

    def _serial_slice_voltages(self, slice_index: int) -> list[float]:
        item = self.storage.slices.get(slice_index)
        if item is not None and item.values:
            return [self.engine.map_dac_to_voltage(value) for value in item.values]

        if self._chip_values_cache is None:
            return []
        for index, start_addr, end_addr in getattr(self, "_analyzed_ranges", []):
            if index != slice_index:
                continue
            values = self._chip_values_cache[start_addr:end_addr]
            return [
                self.engine.map_dac_to_voltage(value)
                for value in values
                if value is not None
            ]
        return []

    def _draw_serial_plot(self, segments) -> None:
        canvas = self.canvas
        canvas.delete("all")

        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        pad_l, pad_r, pad_t, pad_b = 54, 24, 42, 40
        plot_w = max(1, width - pad_l - pad_r)
        plot_h = max(1, height - pad_t - pad_b)
        y_zero = pad_t + plot_h / 2
        colors = self.colors

        canvas.create_rectangle(pad_l, pad_t, pad_l + plot_w, pad_t + plot_h, outline=colors["border"])
        canvas.create_line(
            pad_l,
            y_zero,
            pad_l + plot_w,
            y_zero,
            fill=colors["border"],
            dash=(4, 3),
        )
        canvas.create_text(8, pad_t, anchor="nw", text="+95V", fill=colors["muted"])
        canvas.create_text(8, y_zero, anchor="w", text="0V", fill=colors["muted"])
        canvas.create_text(8, pad_t + plot_h, anchor="sw", text="-95V", fill=colors["muted"])
        canvas.create_text(
            pad_l,
            12,
            anchor="nw",
            text=self.tr("serial_preview_title"),
            fill=colors["text"],
        )

        layout = compact_preview(segments)
        total_points = layout.total
        if total_points <= 0:
            canvas.create_text(
                pad_l + plot_w / 2,
                y_zero,
                text=self.tr("serial_preview_empty"),
                fill=colors["muted"],
            )
            self._plot_drawn_size = (width, height)
            return

        for part in layout.parts:
            if part.omitted:
                continue
            if part.start:
                boundary_x = pad_l + plot_w * part.start / layout.display_total
                canvas.create_line(
                    boundary_x,
                    pad_t,
                    boundary_x,
                    pad_t + plot_h,
                    fill=colors["border"],
                    dash=(2, 3),
                )
            line_points: list[float] = []
            for index, voltage in enumerate(part.samples):
                position = part.start + (part.end - part.start) * index / max(1, len(part.samples) - 1)
                x = pad_l + plot_w * position / layout.display_total
                normalized = max(-1.0, min(1.0, voltage / self.engine.voltage_range))
                y = y_zero - normalized * (plot_h / 2)
                line_points.extend((x, y))
            if len(line_points) >= 4:
                canvas.create_line(*line_points, fill=colors["primary"], width=2, smooth=False, tags="wave_curve")
            label_x = pad_l + plot_w * (part.start + part.end) / (2 * layout.display_total)
            canvas.create_text(
                label_x,
                pad_t + plot_h + 18,
                text=part.label,
                fill=colors["muted"],
                anchor="center",
            )

        draw_omissions(canvas, layout, pad_l, plot_w, y_zero, colors)
        self._plot_drawn_size = (width, height)

    def _safe_start_addr(self) -> int:
        try:
            return self._parse_int_auto(self.start_addr_var.get())
        except Exception:
            return 0

    def _make_address_voltages(
        self,
        voltages: list[float],
        start_addr: int,
        *,
        values: list[int] | None = None,
        skip_index_table: bool = False,
    ) -> list[float | None]:
        samples: list[float | None] = [None] * self.SRAM_DISPLAY_POINTS
        for offset, voltage in enumerate(voltages):
            addr = start_addr + offset
            if 0 <= addr < self.SRAM_DISPLAY_POINTS:
                if skip_index_table and addr < self.SRAM_INDEX_TABLE_END:
                    value = values[offset] if values is not None and offset < len(values) else 0
                    if 0x0000 <= value <= self.storage.SRAM_MAX:
                        continue
                samples[addr] = voltage
        return samples

    def _draw_plot(self, voltages: list[float | None]) -> None:
        self._last_plot_samples = voltages
        canvas = self.canvas
        canvas.delete("all")

        w = max(1, canvas.winfo_width())
        h = max(1, canvas.winfo_height())

        pad_l, pad_r, pad_t, pad_b = 54, 160, 24, 40
        plot_w = max(1, w - pad_l - pad_r)
        plot_h = max(1, h - pad_t - pad_b)

        colors = self.colors

        # \u5750\u6807\u8f74
        canvas.create_rectangle(pad_l, pad_t, pad_l + plot_w, pad_t + plot_h, outline=colors["border"])
        y_zero = pad_t + plot_h / 2
        canvas.create_line(pad_l, y_zero, pad_l + plot_w, y_zero, fill=colors["border"], dash=(4, 3))

        canvas.create_text(8, pad_t, anchor="nw", text="+95V", fill=colors["muted"])
        canvas.create_text(8, y_zero, anchor="w", text="0V", fill=colors["muted"])
        canvas.create_text(8, pad_t + plot_h, anchor="sw", text="-95V", fill=colors["muted"])

        # \u53f3\u4fa7 SRAM \u8f74
        axis_x = pad_l + plot_w + 54
        canvas.create_line(axis_x, pad_t, axis_x, pad_t + plot_h, fill=colors["border"], width=2)
        canvas.create_text(axis_x + 16, pad_t, anchor="w", text="0x0000", fill=colors["muted"])
        canvas.create_text(axis_x + 16, pad_t + plot_h, anchor="w", text="0x03FF", fill=colors["muted"])

        self._draw_allocated_ranges(canvas, axis_x, pad_t, plot_h)

        if voltages:
            pts = []
            last_addr = self.SRAM_DISPLAY_POINTS - 1
            for i, v in enumerate(voltages[: self.SRAM_DISPLAY_POINTS]):
                if v is None:
                    if len(pts) >= 4:
                        canvas.create_line(*pts, fill=colors["primary"], width=2, smooth=False)
                    pts = []
                    continue
                x = pad_l + plot_w * i / last_addr
                normalized = max(-1.0, min(1.0, v / self.engine.voltage_range))
                y = y_zero - normalized * (plot_h / 2)
                pts.extend([x, y])
            if len(pts) >= 4:
                canvas.create_line(*pts, fill=colors["primary"], width=2, smooth=False)
            elif len(pts) == 2:
                x, y = pts
                canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=colors["primary"], outline="")
        self._plot_drawn_size = (w, h)

    def _draw_allocated_ranges(self, canvas: tk.Canvas, axis_x: float, top: float, height: float) -> None:
        colors = self.colors
        occupied_ranges: list[tuple[int | None, int, int, str]] = []

        for item in self.storage.slices.values():
            occupied_ranges.append((item.index, item.start_addr, item.end_addr, colors["primary"]))

        existing = {(start, end) for _idx, start, end, _color in occupied_ranges}
        for idx, start, end in getattr(self, "_analyzed_ranges", []):
            if (start, end) not in existing:
                occupied_ranges.append((idx, start, end, colors["danger"]))

        for _idx, start_addr, end_addr, outline in occupied_ranges:
            y1 = top + (start_addr / (self.storage.SRAM_MAX + 1)) * height
            y2 = top + (end_addr / (self.storage.SRAM_MAX + 1)) * height
            canvas.create_rectangle(
                axis_x - 10,
                y1,
                axis_x + 10,
                y2,
                fill="#BFDBFE",
                outline=outline,
            )
            canvas.create_text(
                axis_x + 18,
                (y1 + y2) / 2,
                anchor="w",
                text=f"0x{start_addr:04X}\n0x{max(start_addr, end_addr - 1):04X}",
                fill=colors["muted"],
            )

    def _find_waveform_conflicts(self, start_addr: int, end_addr: int) -> list[str]:
        conflicts: list[str] = []

        for item in self.storage.slices.values():
            if self._has_blocking_overlap(start_addr, end_addr, item.start_addr, item.end_addr):
                conflicts.append(
                    f"slice{item.index}: 0x{item.start_addr:04X}-0x{max(item.start_addr, item.end_addr - 1):04X}"
                )

        local_ranges = {(item.start_addr, item.end_addr) for item in self.storage.slices.values()}
        for idx, existing_start, existing_end in getattr(self, "_analyzed_ranges", []):
            if (existing_start, existing_end) in local_ranges:
                continue
            if self._has_blocking_overlap(start_addr, end_addr, existing_start, existing_end):
                conflicts.append(
                    f"chip slice{idx}: 0x{existing_start:04X}-0x{max(existing_start, existing_end - 1):04X}"
                )

        return conflicts

    def _has_blocking_overlap(self, start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
        overlap_points = min(end_a, end_b) - max(start_a, start_b)
        return overlap_points > 1

    def write_waveform(self) -> None:
        if self.waveform_type_var.get() == "serial":
            self.write_serial_config()
            return
        if self.waveform_type_var.get() == "imported":
            self.write_imported_waveform()
            return
        self._preview_imported_waveform = False
        self._write_current_waveform(use_imported=False)

    def write_serial_config(self) -> None:
        try:
            register_values = self.build_serial_register_values()
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return

        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return

        self._set_waveform_write_busy(True)
        dev = self.ctx.state.awp192x_device_addr

        def worker() -> None:
            error = ""
            for reg, value in register_values:
                resp = self.ctx.serial.write_register_value(dev, reg, value, bits=16)
                if not resp.ok:
                    error = resp.error
                    break
            try:
                self.after(0, finish, error)
            except tk.TclError:
                pass

        def finish(error: str) -> None:
            self._set_waveform_write_busy(False)
            if error:
                messagebox.showerror(self.tr("error"), error)
            else:
                messagebox.showinfo(self.tr("info"), self.tr("write_success"))

        threading.Thread(target=worker, daemon=True).start()

    def write_imported_waveform(self) -> None:
        if self._imported_values is None:
            messagebox.showwarning(self.tr("error"), self.tr("import_waveform_first"))
            return
        self._preview_imported_waveform = True
        self._write_current_waveform(use_imported=True)

    def set_write_observer(self, token_factory: Any, on_result: Any) -> None:
        """Observe connected write outcomes without changing waveform I/O.

        ``token_factory()`` runs on the UI thread before connected work starts.
        ``on_result`` is called as ``(token, outcome, device_addr, slice)`` on
        completion.  The token is opaque to this component and is carried
        independently from the hardware write data.
        """
        self._waveform_write_observer = (token_factory, on_result) if callable(token_factory) and callable(on_result) else None

    def _capture_write_observation(self, device_addr: int, slice_index: int) -> tuple[Any, Any] | None:
        observer = self._waveform_write_observer
        if observer is None:
            return None
        token_factory, on_result = observer
        try:
            return on_result, token_factory()
        except Exception:
            # Instrumentation must never prevent a pre-existing hardware write.
            return None

    @staticmethod
    def _report_write_observation(
        observation: tuple[Any, Any] | None,
        outcome: str,
        device_addr: int,
        slice_index: int,
    ) -> None:
        if observation is None:
            return
        callback, token = observation
        try:
            callback(token, outcome, int(device_addr), int(slice_index))
        except Exception:
            # An observer is informational and cannot alter UI/storage results.
            pass

    def _write_current_waveform(self, *, use_imported: bool) -> None:
        if self._waveform_write_busy:
            return

        try:
            values, voltages, slice_index, start_addr, end_addr, waveform_type = self._generate_current(use_imported=use_imported)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return

        power_on_recipe = self._power_on_recipe_for_values(
            waveform_type=waveform_type,
            slice_index=slice_index,
            start_addr=start_addr,
            values=values,
        )
        write_data = (
            values,
            voltages,
            slice_index,
            start_addr,
            end_addr,
            waveform_type,
            power_on_recipe,
        )

        if self.ctx.serial.is_connected:
            self._set_waveform_write_busy(True)
            dev = self.ctx.state.awp192x_device_addr
            observation = self._capture_write_observation(dev, slice_index)
            threading.Thread(
                target=self._prepare_waveform_write_worker,
                args=(write_data, dev, observation),
                daemon=True,
            ).start()
            return

        conflicts = self._find_waveform_conflicts(start_addr, end_addr)

        if conflicts:
            conflict_detail = "\n".join(conflicts)
            ok = messagebox.askyesno(
                self.tr("confirm_overwrite_title"),
                f"{self.tr('confirm_overwrite_message')}\n\n{conflict_detail}",
            )
            if not ok:
                return

        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            # \u672a\u8fde\u63a5\u65f6\u4ecd\u7136\u66f4\u65b0\u672c\u5730\u6620\u5c04\uff0c\u65b9\u4fbf\u79bb\u7ebf\u8c03\u8bd5\u754c\u9762\uff1b\u5982\u679c\u4e0d\u9700\u8981\u53ef\u5220\u9664\u4e0b\u9762\u51e0\u884c
            self.storage.add_or_replace(
                slice_index,
                start_addr,
                end_addr,
                len(values),
                waveform_type,
                values,
                power_on_recipe=power_on_recipe,
            )
            self._analyzed_ranges = [
                (idx, existing_start, existing_end)
                for idx, existing_start, existing_end in getattr(self, "_analyzed_ranges", [])
                if idx != slice_index
            ]
            self._analyzed_ranges.append((slice_index, start_addr, end_addr))
            self._update_chip_cache_after_write(slice_index, start_addr, end_addr, values)
            self._sync_read_slices_text()
            self._notify_metrics_changed()
            self._preview_waveform()
            return

    def _prepare_waveform_write_worker(
        self,
        write_data: tuple[
            list[int],
            list[float],
            int,
            int,
            int,
            str,
            Awp192xWaveformRecipe | Awp192xSineWaveformRecipe | None,
        ],
        dev: int,
        observation: tuple[Any, Any] | None = None,
    ) -> None:
        analyzed: list[tuple[int, int, int]] | None = None
        try:
            if self.ctx.serial.is_connected:
                lock = getattr(self.ctx.serial, "_lock", None)
                if lock is None:
                    analyzed = self._scan_existing_waveform_ranges(dev)
                else:
                    with lock:
                        analyzed = self._scan_existing_waveform_ranges(dev)
        except Exception:
            analyzed = None

        try:
            self.after(0, self._handle_waveform_write_ready, write_data, dev, analyzed, observation)
        except tk.TclError:
            pass

    def _handle_waveform_write_ready(
        self,
        write_data: tuple[
            list[int],
            list[float],
            int,
            int,
            int,
            str,
            Awp192xWaveformRecipe | Awp192xSineWaveformRecipe | None,
        ],
        dev: int,
        analyzed: list[tuple[int, int, int]] | None,
        observation: tuple[Any, Any] | None = None,
    ) -> None:
        values, voltages, slice_index, start_addr, end_addr, waveform_type, _recipe = write_data

        if analyzed is not None:
            self._analyzed_ranges = analyzed

        conflicts = self._find_waveform_conflicts(start_addr, end_addr)
        if conflicts:
            conflict_detail = "\n".join(conflicts)
            ok = messagebox.askyesno(
                self.tr("confirm_overwrite_title"),
                f"{self.tr('confirm_overwrite_message')}\n\n{conflict_detail}",
            )
            if not ok:
                self._set_waveform_write_busy(False)
                self._report_write_observation(observation, "cancelled", dev, slice_index)
                return

        if not self.ctx.serial.is_connected:
            self._set_waveform_write_busy(False)
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            self._report_write_observation(observation, "cancelled", dev, slice_index)
            return

        threading.Thread(
            target=self._write_waveform_worker,
            args=(write_data, dev, observation),
            daemon=True,
        ).start()

    def _write_waveform_worker(
        self,
        write_data: tuple[
            list[int],
            list[float],
            int,
            int,
            int,
            str,
            Awp192xWaveformRecipe | Awp192xSineWaveformRecipe | None,
        ],
        dev: int,
        observation: tuple[Any, Any] | None = None,
    ) -> None:
        values, _voltages, slice_index, start_addr, end_addr, _waveform_type, _recipe = write_data
        error = self._write_waveform_to_device(dev, values, slice_index, start_addr, end_addr)
        try:
            self.after(0, self._finish_waveform_write, write_data, dev, error, observation)
        except tk.TclError:
            pass

    def _write_waveform_to_device(
        self,
        dev: int,
        values: list[int],
        slice_index: int,
        start_addr: int,
        end_addr: int,
    ) -> str | None:
        if not self.ctx.serial.is_connected:
            return "Serial port is not connected"

        lock = getattr(self.ctx.serial, "_lock", None)
        if lock is None:
            return self._write_waveform_to_device_locked(dev, values, slice_index, start_addr, end_addr)

        with lock:
            return self._write_waveform_to_device_locked(dev, values, slice_index, start_addr, end_addr)

    def _write_waveform_to_device_locked(
        self,
        dev: int,
        values: list[int],
        slice_index: int,
        start_addr: int,
        end_addr: int,
    ) -> str | None:
        points_per_write = self._selected_write_points_per_batch()
        # \u5199\u5165\u6d41\u7a0b\uff1a
        # 1. \u8bbe\u7f6e waveform SRAM \u6a21\u5f0f\uff1a0x14 = 0x0003
        # 2. \u8bbe\u7f6e\u8d77\u59cb\u5730\u5740\u6307\u9488\uff1a0x16 = start_addr
        # 3. \u8fde\u7eed\u5411 0x00 \u5199\u5165\u6ce2\u5f62\u70b9\uff0c\u671f\u95f4\u4e0d\u91cd\u590d\u8bbe\u7f6e 0x16
        # 4. \u5199\u5b8c\u540e\u6062\u590d\u6700\u5927\u8f93\u51fa\u7535\u538b\u5bc4\u5b58\u5668 0x14 \u5bf9\u5e94\u503c
        resp = self.ctx.serial.write_register_value(dev, 0x14, 0x0003, bits=16)
        if not resp.ok:
            return resp.error

        # \u66f4\u65b0 SRAM slice index \u8868\uff1a
        # sliceN \u4f7f\u7528\u4e24\u4e2a index entry\uff1a
        #   2*N     -> start address
        #   2*N + 1 -> end address
        index_start_addr = int(slice_index) * 2
        index_end_addr = index_start_addr + 1

        resp = self.ctx.serial.write_register_value(dev, 0x16, index_start_addr, bits=16)
        if not resp.ok:
            return resp.error
        resp = self.ctx.serial.write_register_value(dev, 0x00, start_addr, bits=16)
        if not resp.ok:
            return resp.error

        resp = self.ctx.serial.write_register_value(dev, 0x16, index_end_addr, bits=16)
        if not resp.ok:
            return resp.error
        resp = self.ctx.serial.write_register_value(
            dev,
            0x00,
            min(end_addr, self.storage.SRAM_MAX),
            bits=16,
        )
        if not resp.ok:
            return resp.error

        resp = self.ctx.serial.write_register_value(dev, 0x16, start_addr, bits=16)
        if not resp.ok:
            return resp.error

        if points_per_write == 1:
            for value in values:
                resp = self.ctx.serial.write_register_value(dev, 0x00, value, bits=16)
                if not resp.ok:
                    return resp.error
        else:
            for offset in range(0, len(values), points_per_write):
                resp = self.ctx.serial.write_register_values(
                    dev,
                    0x00,
                    values[offset : offset + points_per_write],
                    bits=16,
                )
                if not resp.ok:
                    return resp.error

        resp = self.ctx.serial.write_register_value(
            dev,
            0x14,
            self.ctx.state.current_max_output_reg_value,
            bits=16,
        )
        if not resp.ok:
            return resp.error

        return None

    def _selected_write_points_per_batch(self) -> int:
        variable = getattr(self, "write_points_per_batch_var", None)
        try:
            value = int(variable.get()) if variable is not None else WAVEFORM_DEFAULT_POINTS_PER_WRITE
        except (TypeError, ValueError, tk.TclError):
            return WAVEFORM_DEFAULT_POINTS_PER_WRITE
        return value if value in WAVEFORM_WRITE_POINT_OPTIONS else WAVEFORM_DEFAULT_POINTS_PER_WRITE

    def _finish_waveform_write(
        self,
        write_data: tuple[
            list[int],
            list[float],
            int,
            int,
            int,
            str,
            Awp192xWaveformRecipe | Awp192xSineWaveformRecipe | None,
        ],
        dev: int,
        error: str | None,
        observation: tuple[Any, Any] | None = None,
    ) -> None:
        self._set_waveform_write_busy(False)
        values, voltages, slice_index, start_addr, end_addr, waveform_type, power_on_recipe = write_data

        if error:
            outcome = "cancelled" if not self.ctx.serial.is_connected else "error"
            self._report_write_observation(observation, outcome, dev, slice_index)
            messagebox.showerror(self.tr("error"), error)
            return

        self.storage.add_or_replace(
            slice_index,
            start_addr,
            end_addr,
            len(values),
            waveform_type,
            values,
            power_on_recipe=power_on_recipe,
        )
        self.storage.record_successful_write(slice_index)
        self._analyzed_ranges = [
            (idx, existing_start, existing_end)
            for idx, existing_start, existing_end in getattr(self, "_analyzed_ranges", [])
            if idx != slice_index
        ]
        self._analyzed_ranges.append((slice_index, start_addr, end_addr))
        self._update_chip_cache_after_write(slice_index, start_addr, end_addr, values)
        self._sync_read_slices_text()
        self._notify_metrics_changed()
        self._preview_waveform()
        self._report_write_observation(observation, "done", dev, slice_index)
        messagebox.showinfo(self.tr("info"), self.tr("write_success"))

    def _set_waveform_write_busy(self, busy: bool) -> None:
        self._waveform_write_busy = busy
        state = "disabled" if busy else "normal"
        for key in (
            "write_waveform",
            "import_waveform",
            "export_waveform",
            "write_imported_waveform",
            "verify_written_waveform",
            "show_chip_waveform",
        ):
            button = self._buttons.get(key)
            if button is not None:
                try:
                    button.configure(state=state)
                except tk.TclError:
                    pass

    def verify_written_waveform(self) -> None:
        if self._waveform_write_busy:
            return

        try:
            slice_index = int(self.slice_var.get())
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return

        snapshot = self.storage.get_successful_write(slice_index)
        if snapshot is None or not snapshot.values:
            messagebox.showwarning(
                self.tr("waveform_verify_title"),
                self.tr("waveform_verify_no_snapshot").format(slice=slice_index),
            )
            return

        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return

        self._set_waveform_write_busy(True)
        self._replace_read_slices_text(
            self.tr("waveform_verify_reading").format(
                slice=snapshot.index,
                points=len(snapshot.values),
            )
        )
        dev = self.ctx.state.awp192x_device_addr
        threading.Thread(
            target=self._verify_written_waveform_worker,
            args=(dev, snapshot),
            daemon=True,
        ).start()

    def _verify_written_waveform_worker(self, dev: int, snapshot: SliceInfo) -> None:
        actual_values, error = self._read_waveform_range_from_sram(
            dev,
            snapshot.start_addr,
            len(snapshot.values),
        )
        try:
            self.after(
                0,
                self._finish_waveform_verification,
                snapshot,
                actual_values,
                error,
            )
        except tk.TclError:
            pass

    def _finish_waveform_verification(
        self,
        snapshot: SliceInfo,
        actual_values: list[int],
        error: str | None,
    ) -> None:
        self._set_waveform_write_busy(False)
        if error:
            error_text = self.tr("waveform_verify_read_failed").format(error=error)
            self._replace_read_slices_text(error_text)
            messagebox.showerror(
                self.tr("waveform_verify_title"),
                error_text,
            )
            return

        mismatches = compare_waveform_values(
            expected_values=snapshot.values,
            actual_values=actual_values,
            start_addr=snapshot.start_addr,
        )
        self._replace_read_slices_text(
            self._format_waveform_verification_report(snapshot, mismatches)
        )

        if mismatches:
            messagebox.showwarning(
                self.tr("waveform_verify_title"),
                self.tr("waveform_verify_mismatch").format(
                    slice=snapshot.index,
                    mismatches=len(mismatches),
                    points=len(snapshot.values),
                ),
            )
        else:
            messagebox.showinfo(
                self.tr("waveform_verify_title"),
                self.tr("waveform_verify_match").format(
                    slice=snapshot.index,
                    points=len(snapshot.values),
                ),
            )

    def _format_waveform_verification_report(
        self,
        snapshot: SliceInfo,
        mismatches: list[tuple[int, int, int, int]],
    ) -> str:
        points = len(snapshot.values)
        end_addr = snapshot.start_addr + points - 1
        lines = [
            self.tr("waveform_verify_report_title").format(slice=snapshot.index),
            self.tr("waveform_verify_report_range").format(
                start=snapshot.start_addr,
                end=end_addr,
                points=points,
            ),
        ]
        if not mismatches:
            lines.append(self.tr("waveform_verify_report_match").format(points=points))
            return "\n".join(lines)

        lines.append(
            self.tr("waveform_verify_report_mismatch").format(
                mismatches=len(mismatches),
                points=points,
            )
        )
        lines.append("")
        lines.extend(
            self.tr("waveform_verify_report_item").format(
                point=offset + 1,
                offset=offset,
                address=address,
                expected=expected,
                actual=actual,
            )
            for offset, address, expected, actual in mismatches
        )
        return "\n".join(lines)

    def _read_waveform_range_from_sram(
        self,
        dev: int,
        start_addr: int,
        points: int,
    ) -> tuple[list[int], str | None]:
        if not self.ctx.serial.is_connected:
            return [], "Serial port is not connected"
        if points <= 0:
            return [], "Waveform has no points to verify"
        if start_addr < 0 or start_addr + points > self.SRAM_DISPLAY_POINTS:
            return [], "Waveform verification range is outside SRAM 0x0000-0x03FF"

        try:
            transaction_factory = getattr(self.ctx.serial, "snapshot_transaction", None)
            transaction = transaction_factory() if callable(transaction_factory) else nullcontext()
            with transaction:
                return self._read_waveform_range_from_sram_locked(dev, start_addr, points)
        except Exception as exc:
            return [], str(exc)

    def _read_waveform_range_from_sram_locked(
        self,
        dev: int,
        start_addr: int,
        points: int,
    ) -> tuple[list[int], str | None]:
        def read_register(reg: int) -> tuple[int | None, str | None]:
            response = self.ctx.serial.read_register_value(dev, reg, bits=16)
            if not response.ok:
                return None, response.error or f"Read register 0x{reg:02X} failed"
            value = self.ctx.serial.extract_rdataw_value(
                response.data,
                device_addr=dev,
                reg_addr=reg,
                bits=16,
            )
            if value is None:
                return None, f"Invalid register response for 0x{reg:02X}"
            return value, None

        original_mode, error = read_register(0x14)
        if error is not None or original_mode is None:
            return [], error
        original_pointer, error = read_register(0x16)
        if error is not None or original_pointer is None:
            return [], error

        response = self.ctx.serial.write_register_value(dev, 0x14, 0x0003, bits=16)
        if not response.ok:
            return [], response.error or "Failed to enter waveform SRAM access mode"

        actual_values: list[int] = []
        primary_error: str | None = None
        restore_errors: list[str] = []
        try:
            batch_limit = self.SRAM_READ_BATCH_POINTS
            offset = 0
            response = self.ctx.serial.write_register_value(dev, 0x16, start_addr, bits=16)
            if not response.ok:
                primary_error = (
                    f"Set SRAM address 0x{start_addr:04X} failed: "
                    f"{response.error or 'unknown error'}"
                )
            else:
                while offset < points:
                    address = start_addr + offset
                    batch_points = min(batch_limit, points - offset)
                    response = self.ctx.serial.read_register_values(
                        dev,
                        0x00,
                        bits=16,
                        count=batch_points,
                    )
                    if not response.ok:
                        end_address = address + batch_points - 1
                        primary_error = (
                            f"Read SRAM range 0x{address:04X}-0x{end_address:04X} failed: "
                            f"{response.error or 'unknown error'}"
                        )
                        break

                    batch_values = self.ctx.serial.extract_rdataw_values(
                        response.data,
                        device_addr=dev,
                        reg_addr=0x00,
                        bits=16,
                        count=batch_points,
                    )

                    if batch_values is None or len(batch_values) != batch_points:
                        end_address = address + batch_points - 1
                        primary_error = (
                            f"Invalid SRAM data response for range "
                            f"0x{address:04X}-0x{end_address:04X}"
                        )
                        break
                    actual_values.extend(int(value) & 0xFFFF for value in batch_values)
                    offset += batch_points
        except Exception as exc:
            primary_error = str(exc)
        finally:
            try:
                response = self.ctx.serial.write_register_value(
                    dev,
                    0x16,
                    original_pointer,
                    bits=16,
                )
                if not response.ok:
                    restore_errors.append(
                        f"restore 0x16 failed: {response.error or 'unknown error'}"
                    )
            except Exception as exc:
                restore_errors.append(f"restore 0x16 failed: {exc}")

            try:
                response = self.ctx.serial.write_register_value(
                    dev,
                    0x14,
                    original_mode,
                    bits=16,
                )
                if not response.ok:
                    restore_errors.append(
                        f"restore 0x14 failed: {response.error or 'unknown error'}"
                    )
            except Exception as exc:
                restore_errors.append(f"restore 0x14 failed: {exc}")

        errors = [item for item in (primary_error, *restore_errors) if item]
        if errors:
            return [], "; ".join(errors)
        return actual_values, None

    def _replace_read_slices_text(self, text: str) -> None:
        self.read_slices_text.configure(state="normal")
        self.read_slices_text.delete("1.0", "end")
        self.read_slices_text.insert("1.0", text)
        self.read_slices_text.see("1.0")

    def _sync_read_slices_text(self) -> None:
        self._replace_read_slices_text(self.storage.describe())

    def _waveform_values_for_export(self) -> list[int]:
        if getattr(self, "_plot_mode", "current") == "chip":
            selected_slice = int(self.slice_var.get())
            chip_values = getattr(self, "_chip_values_cache", None)
            for slice_index, start_addr, end_addr in getattr(self, "_analyzed_ranges", []):
                if slice_index != selected_slice:
                    continue
                if chip_values is None:
                    break
                values = chip_values[start_addr:end_addr]
                if len(values) != end_addr - start_addr or any(value is None for value in values):
                    raise ValueError("selected chip waveform contains unreadable points")
                return [int(value) for value in values if value is not None]
            raise ValueError(f"chip slice{selected_slice} has no readable waveform")

        waveform_type = self.waveform_type_var.get()
        if waveform_type == "serial":
            raise ValueError("serial configuration is not a single waveform")
        use_imported = waveform_type == "imported" or getattr(
            self,
            "_preview_imported_waveform",
            False,
        )
        values, _voltages, _slice_index, _start_addr, _end_addr, _waveform_type = (
            self._generate_current(use_imported=use_imported)
        )
        return list(values)

    def _serial_slice_values_for_export(
        self,
        slice_index: int,
    ) -> tuple[int, int, list[int]] | None:
        item = self.storage.slices.get(slice_index)
        if item is not None and item.values:
            values = [int(value) for value in item.values]
            if len(values) == item.end_addr - item.start_addr:
                return item.start_addr, item.end_addr, values

        chip_values = getattr(self, "_chip_values_cache", None)
        if chip_values is None:
            return None
        for index, start_addr, end_addr in getattr(self, "_analyzed_ranges", []):
            if index != slice_index:
                continue
            values = chip_values[start_addr:end_addr]
            if len(values) != end_addr - start_addr or any(value is None for value in values):
                return None
            return start_addr, end_addr, [int(value) for value in values if value is not None]
        return None

    def _serial_waveform_export_text(self) -> str:
        lines = ["# AWP192x Serial Waveform Export v1"]
        exported_count = 0
        for serial_index, (slice_var, repeat_var) in enumerate(
            zip(self.serial_slice_vars, self.serial_repeat_vars)
        ):
            slice_index = self._parse_serial_slice_code(slice_var.get())
            repeat_count = int(repeat_var.get(), 0)
            if not 1 <= repeat_count <= 8:
                raise ValueError(f"serial{serial_index} repeat count must be 1-8")
            if slice_index >= 0x1E:
                break

            exported = self._serial_slice_values_for_export(slice_index)
            if exported is None:
                raise ValueError(
                    f"serial{serial_index} slice{slice_index} has no readable waveform data"
                )
            start_addr, end_addr, values = exported
            value_lines = format_waveform_values_text(values).splitlines()
            if exported_count:
                lines.append("")
            lines.append(
                f"# BEGIN serial{serial_index} slice{slice_index} "
                f"repeat={repeat_count} start=0x{start_addr:04X} "
                f"end=0x{end_addr:04X} points={len(values)}"
            )
            lines.extend(value_lines)
            lines.append(f"# END serial{serial_index}")
            exported_count += 1

        if not exported_count:
            raise ValueError("serial configuration has no exportable slices")
        return "\n".join(lines) + "\n"

    def export_waveform(self) -> None:
        try:
            waveform_type_var = getattr(self, "waveform_type_var", None)
            if waveform_type_var is not None and waveform_type_var.get() == "serial":
                content = self._serial_waveform_export_text()
                initialfile = "awp192x_serial_waveforms.txt"
            else:
                values = self._waveform_values_for_export()
                content = format_waveform_values_text(values)
                slice_index = int(self.slice_var.get())
                initialfile = f"awp192x_slice{slice_index}_waveform.txt"
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return

        path = filedialog.asksaveasfilename(
            title=self.tr("export_waveform"),
            defaultextension=".txt",
            initialfile=initialfile,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            Path(path).write_text(content, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        messagebox.showinfo(self.tr("info"), self.tr("waveform_export_success"))

    def import_waveform(self) -> None:
        """
        \u4ec5\u652f\u6301 .txt\u3002
        \u6587\u4ef6\u5185\u5bb9\u53ef\u4ee5\u662f\u4e00\u884c\u4e00\u4e2a 4 \u4f4d hex\uff0c\u4e5f\u53ef\u4ee5\u6df7\u5408\u7a7a\u683c\u3001\u9017\u53f7\u3001\u6362\u884c\u3002
        """
        path = filedialog.askopenfilename(
            title=self.tr("import_waveform"),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            values = parse_waveform_values_text(
                Path(path).read_text(encoding="utf-8-sig")
            )
            self._imported_values = values
            self._preview_imported_waveform = True
            if hasattr(self, "fill_zero_tail_var"):
                self.fill_zero_tail_var.set(False)
            self.points_var.set(str(len(values)))
            self.waveform_type_var.set("imported")
            selected, voltages, *_ = self._generate_current(use_imported=True)
            self._last_values = selected
            self._last_voltages = voltages
            self._preview_waveform()
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def analyze_existing_waveform(self) -> None:
        self.show_chip_waveform()

    def show_chip_waveform(self) -> None:
        """
        \u8bfb\u53d6/\u5206\u6790\u5df2\u6709\u6ce2\u5f62 slice index \u8868\u3002

        \u5199\u5165\u6ce2\u5f62\u65f6 GUI \u4f1a\u5728 SRAM index \u533a\u5199\u5165\uff1a
            2*N     -> sliceN start address
            2*N + 1 -> sliceN end address

        \u56e0\u6b64\u8fd9\u91cc\u6309 pair \u8bfb\u53d6 0x0000-0x003B\uff0c\u5e76\u53ea\u663e\u793a\u6709\u6548\u7684 start/end\u3002
        """
        if self._waveform_write_busy:
            return

        dev = self.ctx.state.awp192x_device_addr
        cached = self._load_chip_cache(dev)
        if cached is not None and not self.ctx.serial.is_connected:
            values, analyzed = cached
            self._chip_values_cache = values
            self._analyzed_ranges = analyzed
            self._chip_voltages = self._chip_values_to_voltages(values, analyzed)
            self._plot_mode = "chip"
            self._render_analyzed_ranges_text(analyzed)
            self._draw_plot(self._chip_voltages)
            return

        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return

        self._set_waveform_write_busy(True)
        self._replace_read_slices_text(self.tr("show_chip_waveform_reading"))
        threading.Thread(
            target=self._show_chip_waveform_worker,
            args=(dev,),
            daemon=True,
        ).start()

    def _show_chip_waveform_worker(self, dev: int) -> None:
        chip_values, error = self._read_waveform_range_from_sram(
            dev,
            0,
            self.SRAM_DISPLAY_POINTS,
        )
        analyzed = self._analyze_existing_waveform_ranges(chip_values) if not error else []
        try:
            self.after(
                0,
                self._finish_show_chip_waveform,
                dev,
                chip_values,
                analyzed,
                error,
            )
        except tk.TclError:
            pass

    def _finish_show_chip_waveform(
        self,
        dev: int,
        chip_values: list[int],
        analyzed: list[tuple[int, int, int]],
        error: str | None,
    ) -> None:
        self._set_waveform_write_busy(False)
        if error:
            error_text = self.tr("show_chip_waveform_failed").format(error=error)
            self._replace_read_slices_text(error_text)
            messagebox.showerror(self.tr("error"), error_text)
            return

        self._analyzed_ranges = analyzed
        self._chip_values_cache = chip_values
        self._chip_voltages = self._chip_values_to_voltages(chip_values, analyzed)
        self._plot_mode = "chip"
        self._save_chip_cache(dev, chip_values, analyzed)
        self._render_analyzed_ranges_text(analyzed)
        self._draw_plot(self._chip_voltages)

    @staticmethod
    def _analyze_existing_waveform_ranges(
        values: list[int | None],
    ) -> list[tuple[int, int, int]]:
        analyzed: list[tuple[int, int, int]] = []
        for slice_index in range(30):
            index_start_addr = slice_index * 2
            index_end_addr = index_start_addr + 1
            if any(
                start <= index_start_addr < end or start <= index_end_addr < end
                for _idx, start, end in analyzed
            ):
                break
            if index_end_addr >= len(values):
                break
            start_value = values[index_start_addr]
            end_value = values[index_end_addr]
            if start_value is None or end_value is None:
                if analyzed:
                    break
                continue
            if not (0x0000 <= start_value <= 0x03FF and 0x0000 <= end_value <= 0x0400):
                if analyzed:
                    break
                continue
            if start_value == 0x0000 and end_value == 0x0000:
                if analyzed:
                    break
                continue
            if end_value <= start_value:
                if analyzed:
                    break
                continue
            analyzed.append((slice_index, start_value, end_value))
        return analyzed

    def _render_analyzed_ranges_text(self, analyzed: list[tuple[int, int, int]]) -> None:
        self.read_slices_text.configure(state="normal")
        self.read_slices_text.delete("1.0", "end")

        if analyzed:
            lines = [
                f"slice{idx}: start 0x{start:04X}, end 0x{end:04X}"
                for idx, start, end in analyzed
            ]
            self.read_slices_text.insert("1.0", "\n".join(lines))
        else:
            self.read_slices_text.insert(
                "1.0",
                "No valid waveform slice index found in SRAM index area 0x0000-0x003B.",
            )

        self.read_slices_text.configure(state="normal")

    def _read_chip_waveform_values(self, dev: int) -> list[int | None] | None:
        values, error = self._read_waveform_range_from_sram(
            dev,
            0,
            self.SRAM_DISPLAY_POINTS,
        )
        return None if error else values

    def _chip_values_to_voltages(
        self,
        values: list[int | None],
        analyzed: list[tuple[int, int, int]] | None = None,
    ) -> list[float | None]:
        valid_addresses: set[int] | None = None
        used_index_addresses: set[int] = set()
        ranges = analyzed if analyzed is not None else getattr(self, "_analyzed_ranges", [])
        if analyzed is not None or ranges:
            valid_addresses = set()
            for idx, start, end in ranges:
                used_index_addresses.add(idx * 2)
                used_index_addresses.add(idx * 2 + 1)
                clipped_start = max(0, start)
                clipped_end = min(self.SRAM_DISPLAY_POINTS, end)
                if clipped_start < clipped_end:
                    valid_addresses.update(range(clipped_start, clipped_end))

        voltages: list[float | None] = []
        for addr, value in enumerate(values[: self.SRAM_DISPLAY_POINTS]):
            if value is None:
                voltages.append(None)
                continue
            if valid_addresses is not None and addr not in valid_addresses:
                voltages.append(None)
                continue
            if valid_addresses is None and addr in used_index_addresses:
                voltages.append(None)
                continue
            if 0x0000 <= value <= self.engine.DAC_MAX:
                voltages.append(self.engine.map_dac_to_voltage(value))
            else:
                voltages.append(None)
        if len(voltages) < self.SRAM_DISPLAY_POINTS:
            voltages.extend([None] * (self.SRAM_DISPLAY_POINTS - len(voltages)))
        return voltages

    def _chip_cache_path(self, dev: int) -> Path:
        return Path(tempfile.gettempdir()) / f"piezo_gui_awp192x_{dev:02X}_sram_cache.json"

    def _load_chip_cache(self, dev: int) -> tuple[list[int | None], list[tuple[int, int, int]]] | None:
        path = self._chip_cache_path(dev)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            values = data.get("values", [])
            ranges = data.get("ranges", [])
            if not isinstance(values, list) or len(values) != self.SRAM_DISPLAY_POINTS:
                return None
            cleaned_values = [
                int(value) if isinstance(value, int) and 0 <= value <= 0xFFFF else None
                for value in values
            ]
            cleaned_ranges: list[tuple[int, int, int]] = []
            for item in ranges:
                if not isinstance(item, (list, tuple)) or len(item) != 3:
                    continue
                idx, start, end = (int(item[0]), int(item[1]), int(item[2]))
                if 0 <= idx < self.storage.MAX_SLICES and 0 <= start <= 0x03FF and start < end <= 0x0400:
                    cleaned_ranges.append((idx, start, end))
            return cleaned_values, cleaned_ranges
        except Exception:
            return None

    def _save_chip_cache(self, dev: int, values: list[int | None], ranges: list[tuple[int, int, int]]) -> None:
        clipped_values = list(values[: self.SRAM_DISPLAY_POINTS])
        if len(clipped_values) < self.SRAM_DISPLAY_POINTS:
            clipped_values.extend([None] * (self.SRAM_DISPLAY_POINTS - len(clipped_values)))
        data = {
            "device_addr": dev & 0xFF,
            "values": clipped_values,
            "ranges": [[idx, start, end] for idx, start, end in ranges],
        }
        try:
            self._chip_cache_path(dev).write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _update_chip_cache_after_write(self, slice_index: int, start_addr: int, end_addr: int, values: list[int]) -> None:
        dev = self.ctx.state.awp192x_device_addr
        loaded = self._load_chip_cache(dev)
        cache_values = loaded[0] if loaded is not None else self._chip_values_cache
        if cache_values is None:
            cache_values = [None] * self.SRAM_DISPLAY_POINTS
        else:
            cache_values = list(cache_values[: self.SRAM_DISPLAY_POINTS])
            if len(cache_values) < self.SRAM_DISPLAY_POINTS:
                cache_values.extend([None] * (self.SRAM_DISPLAY_POINTS - len(cache_values)))

        index_start_addr = int(slice_index) * 2
        index_end_addr = index_start_addr + 1
        if 0 <= index_start_addr < self.SRAM_DISPLAY_POINTS:
            cache_values[index_start_addr] = start_addr
        if 0 <= index_end_addr < self.SRAM_DISPLAY_POINTS:
            cache_values[index_end_addr] = end_addr

        for offset, value in enumerate(values):
            addr = start_addr + offset
            if 0 <= addr < self.SRAM_DISPLAY_POINTS:
                cache_values[addr] = value & 0xFFFF

        ranges = [
            (idx, existing_start, existing_end)
            for idx, existing_start, existing_end in getattr(self, "_analyzed_ranges", [])
            if idx != slice_index
        ]
        ranges.append((slice_index, start_addr, end_addr))
        self._analyzed_ranges = ranges
        self._chip_values_cache = cache_values
        self._chip_voltages = self._chip_values_to_voltages(cache_values, ranges)
        self._save_chip_cache(dev, cache_values, ranges)

    def _scan_existing_waveform_ranges(self, dev: int, show_errors: bool = False) -> list[tuple[int, int, int]] | None:
        resp = self.ctx.serial.write_register_value(dev, 0x14, 0x0003, bits=16)
        if not resp.ok:
            if show_errors:
                messagebox.showerror(self.tr("error"), resp.error)
            return None

        analyzed: list[tuple[int, int, int]] = []

        for slice_index in range(30):
            index_start_addr = slice_index * 2
            index_end_addr = index_start_addr + 1

            if any(
                start <= index_start_addr < end or start <= index_end_addr < end
                for _idx, start, end in analyzed
            ):
                break

            start_value = self._read_sram_word(dev, index_start_addr)
            end_value = self._read_sram_word(dev, index_end_addr)

            if start_value is None or end_value is None:
                if analyzed:
                    break
                continue

            if not (0x0000 <= start_value <= 0x03FF and 0x0000 <= end_value <= 0x0400):
                if analyzed:
                    break
                continue

            # \u672a\u4f7f\u7528\u9879\u901a\u5e38\u662f 0x0000/0x0000\uff0c\u8fc7\u6ee4\u6389\u3002
            if start_value == 0x0000 and end_value == 0x0000:
                if analyzed:
                    break
                continue

            # end \u5fc5\u987b\u5927\u4e8e start\uff1b\u5426\u5219\u4e0d\u662f\u5408\u6cd5 slice\u3002
            if end_value <= start_value:
                if analyzed:
                    break
                continue

            analyzed.append((slice_index, start_value, end_value))

        return analyzed

    def _read_sram_word(self, dev: int, sram_addr: int) -> int | None:
        resp = self.ctx.serial.write_register_value(dev, 0x16, sram_addr, bits=16)
        if not resp.ok:
            return None

        resp = self.ctx.serial.read_register_value(dev, 0x00, bits=16)
        if not resp.ok:
            return None

        return self.ctx.serial.extract_rdataw_value(
            resp.data,
            device_addr=dev,
            reg_addr=0x00,
            bits=16,
        )
