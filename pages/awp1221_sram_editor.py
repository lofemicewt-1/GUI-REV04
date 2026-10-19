"""AWP1221 SRAM waveform editor, ported from the September 20 selector."""
from __future__ import annotations

import math
import re
import threading
import tkinter as tk
from contextlib import nullcontext
from queue import Empty, Queue

from tkinter import messagebox, ttk

from pages.base_page import BasePage

from core.awp1221_sram import (
    SegmentRatioCapacityError,
    ZeroIntervalCandidate,
    allocate_segment_intervals,
    allocate_words,
    generate_smooth_points,
    generate_waveform,
    write_waveform,
)

_SRAM_OCCUPANCY: dict[tuple[tuple[str, int], int], tuple[int, int]] = {}
_RATIO_BASIS = 10000


class _SramInputError(ValueError):
    def __init__(self, zh: str, en: str) -> None:
        super().__init__(en)
        self.zh = zh
        self.en = en


def _equal_display_basis(segment_count: int) -> tuple[int, ...]:
    if segment_count < 1:
        return ()
    base, extra = divmod(_RATIO_BASIS, segment_count)
    return tuple(base + (1 if index < extra else 0) for index in range(segment_count))


class AWP1221SramEditor(BasePage):
    MIN_CONTROL_POINT = -36.0

    def __init__(self, parent, ctx, owner):
        super().__init__(parent, ctx)
        self.owner = owner
        self.ROM_AMPLITUDE_OPTIONS = owner.VPK_OPTIONS
        self.ROM_OFFSET_OPTIONS = owner.OFFSET_OPTIONS
        self.rom_amplitude_var = tk.StringVar(value=self.ROM_AMPLITUDE_OPTIONS[0])
        self.rom_offset_var = tk.StringVar(value=self.ROM_OFFSET_OPTIONS[0])
        self.rom_waveform_var = tk.StringVar(value="SRAM")
        self.rom_metric_var = tk.StringVar()
        self.rom_frequency_var = tk.StringVar()
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        scroll, controls = self.make_vertical_scroll_area(self)
        self._sram_scroll = scroll
        self._sram_narrow = False
        self._sram_reflowing = False
        scroll.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        scroll.horizontal = False
        scroll.canvas.configure(width=335)
        controls.columnconfigure(0, weight=1)
        plot = self._sram_plot = ttk.Frame(self, style="Panel.TFrame")
        plot.grid(row=0, column=1, sticky="nsew")
        plot.columnconfigure(0, weight=1)
        plot.rowconfigure(2, weight=1)
        self._sram_metric_label = ttk.Label(plot, textvariable=self.rom_metric_var, style="Muted.TLabel", wraplength=560)
        self._sram_metric_label.grid(row=0, column=0, sticky="w")
        self._sram_frequency_label = ttk.Label(plot, textvariable=self.rom_frequency_var, style="Muted.TLabel", wraplength=560)
        self._sram_frequency_label.grid(row=1, column=0, sticky="w")
        self.rom_canvas = tk.Canvas(plot, width=320, height=280, highlightthickness=0, background=self.colors["panel"])
        self.rom_canvas.grid(row=2, column=0, sticky="nsew")
        self._init_sram_editor(controls)
        self._sram_frame.grid()
        self.rom_canvas.bind("<Configure>", self._schedule_sram_redraw)
        plot.bind("<Configure>", self._schedule_sram_plot_wrap, add="+")
        self.bind("<Configure>", self._schedule_sram_layout, add="+")

    def _schedule_sram_redraw(self, _event=None) -> None:
        if getattr(self, "_sram_redraw_job", None) is not None:
            return
        def run() -> None:
            self._sram_redraw_job = None
            try:
                if not self.winfo_ismapped():
                    return
                if self._sram_input_invalid or self._sram_result is not None or self._sram_preview_points:
                    self._paint_sram_preview()
                else:
                    self._draw_sram_waveform()
            except tk.TclError:
                pass
        try:
            self._sram_redraw_job = self.after_idle(run)
        except tk.TclError:
            self._sram_redraw_job = None

    def _schedule_sram_layout(self, _event=None) -> None:
        if getattr(self, "_sram_layout_job", None) is not None:
            return
        def run() -> None:
            self._sram_layout_job = None
            try:
                self._sync_sram_editor_layout()
            except tk.TclError:
                pass
        try:
            self._sram_layout_job = self.after_idle(run)
        except tk.TclError:
            self._sram_layout_job = None

    def _schedule_sram_plot_wrap(self, _event=None) -> None:
        if getattr(self, "_sram_wrap_job", None) is not None:
            return
        def run() -> None:
            self._sram_wrap_job = None
            try:
                self._sync_sram_plot_wraplength()
            except tk.TclError:
                pass
        try:
            self._sram_wrap_job = self.after_idle(run)
        except tk.TclError:
            self._sram_wrap_job = None

    def _sync_sram_plot_wraplength(self, _event=None) -> None:
        if not hasattr(self, "_sram_plot"):
            return
        plot_wrap = max(320, self._sram_plot.winfo_width() - 8)
        if plot_wrap == getattr(self, "_sram_plot_wrap", None):
            return
        self._sram_plot_wrap = plot_wrap
        self._sram_metric_label.configure(wraplength=plot_wrap)
        self._sram_frequency_label.configure(wraplength=plot_wrap)

    def _device_addr(self):
        return self.owner.device_addr()

    def _ensure_connected(self):
        return self.owner._ensure_connected()

    def refresh_language(self):
        super().refresh_language()
        self._refresh_sram_language()

    def refresh_theme(self):
        super().refresh_theme()
        colors = self.colors
        self.rom_canvas.configure(
            background=colors.get("canvas_bg", colors["panel"]),
            highlightbackground=colors.get("border", colors["panel"]),
        )
        self._sram_paint_key = None
        if self._sram_result is not None or self._sram_preview_points or self._sram_input_invalid:
            self._paint_sram_preview()
        else:
            self._draw_sram_waveform()

    def _sync_sram_editor_layout(self, _event=None) -> None:
        if self._sram_reflowing or not hasattr(self, "_sram_plot"):
            return
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            return
        narrow = width < 680
        self._sram_reflowing = True
        try:
            if narrow != self._sram_narrow:
                self._sram_narrow = narrow
                if narrow:
                    self.rowconfigure(0, weight=0)
                    self.rowconfigure(1, weight=1, minsize=100)
                    self.columnconfigure(1, weight=1)
                    self._sram_scroll.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=0)
                    self._sram_plot.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
                else:
                    self.rowconfigure(1, weight=0, minsize=0)
                    self.rowconfigure(0, weight=1)
                    self.columnconfigure(1, weight=1)
                    self._sram_scroll.grid(row=0, column=0, columnspan=1, sticky="nsew", padx=(0, 12))
                    self._sram_plot.grid(row=0, column=1, columnspan=1, sticky="nsew", pady=0)
                    self._sram_scroll.canvas.configure(height=200)
            if narrow:
                form_height = max(110, min(220, height // 2))
                if int(float(self._sram_scroll.canvas.cget("height"))) != form_height:
                    self._sram_scroll.canvas.configure(height=form_height)
            self._sync_sram_plot_wraplength()
        finally:
            self._sram_reflowing = False

    def _init_sram_editor(self, controls: tk.Misc) -> None:
        self.sram_wave_var = tk.StringVar(value="0")
        self.sram_sample_var = tk.StringVar(value="1")
        self.sram_values_var = tk.StringVar(value="0, 100, -36, 0")
        self.sram_points_var = tk.StringVar(value="auto")
        self.sram_start_var = tk.StringVar(value="auto")
        self.sram_status_var = tk.StringVar()
        self._sram_controls = [0.0, 100.0, -36.0, 0.0]
        self._sram_drag_index = None
        self._sram_result = None
        self._sram_saved = _SRAM_OCCUPANCY
        self._sram_busy = False
        self._sram_poll_after = None
        self._sram_ratio_mode = "equal"
        self._sram_explicit_weights = None
        self._sram_force_equal = False
        self._sram_applying = False
        self._sram_rebuilding_ratios = False
        self._sram_input_invalid = False
        self._sram_error_text = None
        self._sram_status_note = None
        self._sram_point_request = None
        self._sram_start_request = "auto"
        self._sram_preview_points = ()
        self._sram_preview_intervals = ()
        self._sram_preview_controls = list(self._sram_controls)
        self._sram_ratio_rows = []
        self._sram_ratio_lock_widgets = []
        frame = self._sram_frame = ttk.Frame(controls, style="Panel.TFrame")
        frame.grid(row=1, column=0, columnspan=4, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        for row, (label, variable, values) in enumerate((
            ("WAVE NUMBER", self.sram_wave_var, [str(i) for i in range(31)]),
            ("VPK", self.rom_amplitude_var, self.ROM_AMPLITUDE_OPTIONS),
            ("OFFSET", self.rom_offset_var, self.ROM_OFFSET_OPTIONS),
            ("SAMPLE_TIME (us)", self.sram_sample_var, [str(i) for i in range(1, 257)]),
        )):
            ttk.Label(frame, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=7)
            combo = ttk.Combobox(frame, textvariable=variable, values=values, width=16, state="readonly")
            combo.grid(row=row, column=1, sticky="ew", pady=7)
            combo.bind("<<ComboboxSelected>>", lambda _e: self._draw_sram_waveform())
        self._sram_help = ttk.Label(frame, style="Muted.TLabel", wraplength=260)
        self._sram_help.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 8))
        self._sram_values_entry = ttk.Entry(frame, textvariable=self.sram_values_var, width=25)
        self._sram_values_entry.grid(row=5, column=0, columnspan=2, sticky="ew")
        self._sram_values_entry.bind("<Return>", lambda _event: self._apply_sram_values())
        self._sram_values_entry.bind("<FocusOut>", lambda _event: self._apply_sram_values())
        self._sram_update_btn = ttk.Button(frame, command=self._apply_sram_values)
        self._sram_update_btn.grid(row=6, column=0, columnspan=2, sticky="ew", pady=8)
        ratio = self._sram_ratio_frame = ttk.Frame(frame, style="Panel.TFrame")
        ratio.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ratio.columnconfigure(0, weight=1)
        self._sram_ratio_title = ttk.Label(ratio, style="Panel.TLabel", wraplength=180)
        self._sram_ratio_title.grid(row=0, column=0, sticky="w")
        self._sram_equal_btn = ttk.Button(ratio, command=self._equalize_sram_ratios)
        self._sram_equal_btn.grid(row=0, column=1, sticky="e")
        self._sram_ratio_rows_frame = ttk.Frame(ratio, style="Panel.TFrame")
        self._sram_ratio_rows_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self._sram_ratio_rows_frame.columnconfigure(0, weight=1)
        self._sram_ratio_total = ttk.Label(ratio, style="Muted.TLabel")
        self._sram_ratio_total.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._sram_points_label = ttk.Label(frame, style="Panel.TLabel", wraplength=250)
        self._sram_points_label.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self._sram_points_entry = ttk.Entry(frame, textvariable=self.sram_points_var, width=12)
        self._sram_points_entry.grid(row=9, column=0, columnspan=2, sticky="ew")
        self._sram_points_entry.bind("<Return>", lambda _event: self._apply_sram_values())
        self._sram_points_entry.bind("<FocusOut>", lambda _event: self._apply_sram_values())
        self._sram_points_hint = ttk.Label(frame, style="Muted.TLabel", wraplength=250)
        self._sram_points_hint.grid(row=10, column=0, columnspan=2, sticky="ew")
        self._sram_start_label = ttk.Label(frame, style="Panel.TLabel", wraplength=250)
        self._sram_start_label.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self._sram_start_entry = ttk.Entry(frame, textvariable=self.sram_start_var, width=12)
        self._sram_start_entry.grid(row=12, column=0, columnspan=2, sticky="ew")
        self._sram_start_entry.bind("<Return>", lambda _event: self._apply_sram_values())
        self._sram_start_entry.bind("<FocusOut>", lambda _event: self._apply_sram_values())
        self._sram_write_btn = ttk.Button(frame, style="Primary.TButton", command=self._start_sram_write)
        self._sram_write_btn.grid(row=13, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(frame, textvariable=self.sram_status_var, style="Muted.TLabel", wraplength=250).grid(
            row=14, column=0, columnspan=2, sticky="ew", pady=8)
        self._sram_progress = ttk.Progressbar(frame, maximum=100)
        self._sram_progress.grid(row=15, column=0, columnspan=2, sticky="ew", pady=4)
        self._sram_progress.grid_remove()
        frame.grid_remove()
        if getattr(self.owner, "mode_key", None) == "button_mode":
            self._sram_auto_serial = ttk.Checkbutton(
                controls,
                variable=self.owner.auto_serial_var,
                command=self.owner._on_auto_serial_changed,
            )
            self._sram_auto_serial.grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.rom_canvas.bind("<ButtonPress-1>", self._sram_press, add="+")
        self.rom_canvas.bind("<B1-Motion>", self._sram_drag, add="+")
        self.rom_canvas.bind("<ButtonRelease-1>", self._sram_release, add="+")
        self.bind("<Destroy>", self._sram_destroy, add="+")
        self._rebuild_ratio_rows()
        self._refresh_sram_language()

    def _sram_text(self, zh: str, en: str) -> str:
        return zh if self.language == "zh" else en

    def _refresh_sram_language(self) -> None:
        if not hasattr(self, "_sram_frame"):
            return
        self._sram_help.configure(text=self._sram_text(
            "控制点相对 Vpk，最低为 -36%。\n拖动曲线或圆点调整；起点和终点设为 0 可回到 VIN。",
            "Control points are relative to Vpk (minimum -36%).\nDrag the curve or handles. Set both ends to 0 to return to VIN."))
        self._sram_update_btn.configure(text=self._sram_text("更新控制点", "Update shape"))
        self._sram_write_btn.configure(text=self._sram_text("写入 SRAM", "Write SRAM"))
        self._sram_points_label.configure(text=self._sram_text("起始点数（auto=自动）", "Start points (auto or number)"))
        self._sram_points_hint.configure(text=self._sram_text(
            "为满足压缩要求，实际点数可能增加",
            "Actual point count may increase to satisfy compression"))
        self._sram_start_label.configure(text=self._sram_text("数据起始地址", "Data start address"))
        auto_serial = getattr(self, "_sram_auto_serial", None)
        translator = getattr(self.owner, "tr", None)
        if auto_serial is not None and callable(translator):
            auto_serial.configure(text=translator("awp1221_auto_serial"))
        if hasattr(self, "_sram_plot"):
            plot_wrap = max(560, self._sram_plot.winfo_width() - 8)
            self._sram_metric_label.configure(wraplength=plot_wrap)
            self._sram_frequency_label.configure(wraplength=plot_wrap)
        self._refresh_ratio_labels()
        self._sram_paint_key = None
        if self.rom_waveform_var.get() == "SRAM":
            result = getattr(self, "_sram_result", None)
            if result is not None:
                self._publish_sram_result(result)
            else:
                self._draw_sram_waveform()

    def _ratio_row_label(self, index: int, count: int) -> str:
        span = f"P{index}→P{index + 1}"
        if count == 2:
            name = self._sram_text("左段" if index == 0 else "右段", "Left" if index == 0 else "Right")
        else:
            name = self._sram_text(f"第 {index + 1} 段", f"Segment {index + 1}")
        auto = self._sram_text("（自动补足）", " (auto)") if count > 1 and index == count - 1 else ""
        return f"{name} {span}{auto}"

    def _refresh_ratio_labels(self) -> None:
        if not hasattr(self, "_sram_ratio_title"):
            return
        count = len(self._sram_ratio_rows)
        self._sram_ratio_title.configure(text=self._sram_text("各段占比（%）", "Segment ratios (%)"))
        self._sram_equal_btn.configure(text=self._sram_text("均分", "Equal split"))
        for row in self._sram_ratio_rows:
            row["label"].configure(text=self._ratio_row_label(row["index"], count))
            row["percent"].configure(text="%")
        self._refresh_ratio_total_text()
        self._update_ratio_actuals()

    def _refresh_ratio_total_text(self) -> None:
        try:
            if len(self._sram_ratio_rows) <= 1:
                total = _RATIO_BASIS
            else:
                editable = [self._parse_ratio_basis(row["variable"].get()) for row in self._sram_ratio_rows[:-1]]
                last_text = self._sram_ratio_rows[-1]["variable"].get().strip()
                last = self._parse_ratio_basis(last_text) if last_text else 0
                total = sum(editable) + last
        except _SramInputError:
            self._sram_ratio_total.configure(text=self._sram_text("合计：—", "Total: —"))
            return
        if total == _RATIO_BASIS:
            text = self._sram_text("合计：100.00 %", "Total: 100.00 %")
        else:
            text = self._sram_text(f"合计：{total / 100:.2f} %", f"Total: {total / 100:.2f} %")
        self._sram_ratio_total.configure(text=text)

    def _rebuild_ratio_rows(self) -> None:
        self._sram_rebuilding_ratios = True
        try:
            for child in self._sram_ratio_rows_frame.winfo_children():
                child.destroy()
            self._sram_ratio_rows = []
            self._sram_ratio_lock_widgets = [self._sram_equal_btn]
            count = max(1, len(self._sram_controls) - 1)
            basis = _equal_display_basis(count)
            for index in range(count):
                row_frame = ttk.Frame(self._sram_ratio_rows_frame, style="Panel.TFrame")
                row_frame.grid(row=index, column=0, sticky="ew", pady=2)
                row_frame.columnconfigure(2, weight=1)
                label = ttk.Label(row_frame, style="Panel.TLabel", wraplength=250)
                label.grid(row=0, column=0, columnspan=3, sticky="ew")
                variable = tk.StringVar(value=self._format_basis(basis[index]))
                state = "readonly" if count == 1 or index == count - 1 else "normal"
                entry = ttk.Entry(row_frame, textvariable=variable, width=8, state=state)
                entry.grid(row=1, column=0, sticky="w")
                percent = ttk.Label(row_frame, text="%", style="Panel.TLabel")
                percent.grid(row=1, column=1, sticky="w", padx=(4, 8))
                actual_var = tk.StringVar()
                actual = ttk.Label(row_frame, textvariable=actual_var, style="Muted.TLabel", wraplength=250)
                actual.grid(row=2, column=0, columnspan=3, sticky="ew")
                if state == "normal":
                    entry.bind("<Return>", lambda _event: self._apply_sram_values())
                    entry.bind("<FocusOut>", lambda _event: self._apply_sram_values())
                    entry.bind("<KeyRelease>", lambda _event: self._quiet_refresh_auto())
                self._sram_ratio_lock_widgets.append(entry)
                self._sram_ratio_rows.append({
                    "index": index,
                    "label": label,
                    "variable": variable,
                    "entry": entry,
                    "percent": percent,
                    "actual_var": actual_var,
                    "actual": actual,
                })
            self._refresh_ratio_labels()
        finally:
            self._sram_rebuilding_ratios = False

    def _fill_equal_display(self) -> None:
        basis = _equal_display_basis(len(self._sram_ratio_rows))
        for row, value in zip(self._sram_ratio_rows, basis):
            row["variable"].set(self._format_basis(value))
        self._refresh_ratio_total_text()

    def _fill_explicit_display(self, weights: tuple[int, ...]) -> None:
        for row, basis in zip(self._sram_ratio_rows, weights):
            row["variable"].set(self._format_basis(basis))
        self._refresh_ratio_total_text()

    @staticmethod
    def _format_basis(basis: int) -> str:
        return f"{basis / 100:.2f}"

    def _parse_ratio_basis(self, text: str) -> int:
        raw = text.strip()
        if raw == "":
            raise _SramInputError("分段比例不能为空", "Segment ratio cannot be empty")
        if re.fullmatch(r"[+-]?(?:nan|inf|infinity)", raw, flags=re.IGNORECASE):
            raise _SramInputError("分段比例必须是有限数字", "Segment ratio must be a finite number")
        if raw[0] in "+-":
            raise _SramInputError("每一段比例必须大于 0%", "Each segment ratio must be greater than 0%")
        if re.fullmatch(r"\d+\.\d{3,}", raw):
            raise _SramInputError("分段比例最多保留两位小数", "Segment ratios allow at most two decimal places")
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", raw):
            raise _SramInputError("分段比例必须是数字", "Segment ratio must be a number")
        if "." in raw:
            whole, frac = raw.split(".")
            frac = (frac + "00")[:2]
        else:
            whole, frac = raw, "00"
        basis = int(whole) * 100 + int(frac)
        if basis <= 0:
            raise _SramInputError("每一段比例必须大于 0%", "Each segment ratio must be greater than 0%")
        if basis > _RATIO_BASIS:
            raise _SramInputError(
                "前面各段之和必须小于 100%，以便最后一段大于 0%",
                "Earlier segments must sum to less than 100% so the last segment stays above 0%")
        return basis

    def _quiet_refresh_auto(self) -> None:
        if self._sram_rebuilding_ratios or self._sram_applying or len(self._sram_ratio_rows) < 2:
            return
        try:
            editable = [self._parse_ratio_basis(row["variable"].get()) for row in self._sram_ratio_rows[:-1]]
        except _SramInputError:
            self._sram_ratio_rows[-1]["variable"].set("")
            self._refresh_ratio_total_text()
            return
        last = _RATIO_BASIS - sum(editable)
        self._sram_ratio_rows[-1]["variable"].set("" if last <= 0 else self._format_basis(last))
        self._refresh_ratio_total_text()

    def _weights_from_inputs(self) -> tuple[int, ...] | None:
        count = len(self._sram_ratio_rows)
        if count <= 1:
            return None
        editable = [self._parse_ratio_basis(row["variable"].get()) for row in self._sram_ratio_rows[:-1]]
        equal = _equal_display_basis(count)
        if self._sram_ratio_mode == "equal" and editable == list(equal[:-1]):
            return None
        if sum(editable) >= _RATIO_BASIS:
            raise _SramInputError(
                "前面各段之和必须小于 100%，以便最后一段大于 0%",
                "Earlier segments must sum to less than 100% so the last segment stays above 0%")
        return tuple(editable) + (_RATIO_BASIS - sum(editable),)

    def _parse_control_values(self) -> list[float]:
        try:
            values = [float(part.strip()) for part in self.sram_values_var.get().replace("，", ",").split(",")]
        except ValueError:
            raise _SramInputError("控制点必须是数字", "Control points must be numbers") from None
        if any(not math.isfinite(value) for value in values):
            raise _SramInputError("控制点必须是有限数字", "Control points must be finite numbers")
        if not 2 <= len(values) <= 32:
            raise _SramInputError("请输入 2～32 个控制点", "Enter 2..32 control points")
        if any(value < self.MIN_CONTROL_POINT for value in values):
            raise _SramInputError("控制点最低为 -36%", "Control points must be at least -36%")
        return values

    def _parse_point_request(self):
        requested = self.sram_points_var.get().strip().lower()
        if requested in ("", "auto", "自动"):
            return None
        try:
            return int(requested)
        except ValueError:
            raise _SramInputError("起始点数必须是 auto 或整数", "Start points must be auto or an integer") from None

    def _point_count_for(self, values, request) -> int:
        if request is None:
            minimum = max(6, len(values))
            return 2 + 4 * ((minimum - 2 + 3) // 4)
        return request

    def _parse_start_request(self) -> str:
        return self.sram_start_var.get().strip().lower()

    def _apply_sram_values(self) -> None:
        if self._sram_busy or self._sram_rebuilding_ratios or self._sram_applying:
            return
        self._sram_applying = True
        try:
            self._apply_sram_values_inner()
        finally:
            self._sram_applying = False

    def _apply_sram_values_inner(self) -> None:
        try:
            values = self._parse_control_values()
            point_request = self._parse_point_request()
            start_request = self._parse_start_request()
            count_changed = len(values) != len(self._sram_controls)
            weights = None if count_changed or self._sram_force_equal else self._weights_from_inputs()
        except _SramInputError as exc:
            self._reject_sram_apply(exc.zh, exc.en)
            return
        try:
            result = self._generate_sram_shape(values, weights, point_request)
        except (ValueError, ZeroIntervalCandidate) as exc:
            self._accept_unencoded(values, weights, count_changed, point_request, start_request, exc)
            return
        try:
            self._sram_start_address(len(result.words), start_request)
        except ValueError as exc:
            self._reject_sram_apply(str(exc), str(exc))
            return
        self._commit_sram_apply(values, weights, count_changed, point_request, start_request, result)

    def _store_applied_inputs(self, values, weights, count_changed, point_request, start_request) -> None:
        self._sram_controls = list(values)
        self._sram_point_request = point_request
        self._sram_start_request = start_request
        if count_changed or self._sram_force_equal:
            self._sram_ratio_mode = "equal"
            self._sram_explicit_weights = None
            self._sram_status_note = (
                ("控制点数量已变化，分段比例已恢复均分",
                 "Control-point count changed; segment ratios reset to equal split")
                if count_changed else None
            )
            if count_changed:
                self._rebuild_ratio_rows()
            else:
                self._fill_equal_display()
            return
        self._sram_status_note = None
        if weights is None:
            self._sram_ratio_mode = "equal"
            self._sram_explicit_weights = None
            self._fill_equal_display()
        else:
            self._sram_ratio_mode = "explicit"
            self._sram_explicit_weights = tuple(weights)
            self._fill_explicit_display(self._sram_explicit_weights)

    def _commit_sram_apply(self, values, weights, count_changed, point_request, start_request, result) -> None:
        self._sram_input_invalid = False
        self._sram_error_text = None
        self._store_applied_inputs(values, weights, count_changed, point_request, start_request)
        self._publish_sram_result(result)

    def _accept_unencoded(self, values, weights, count_changed, point_request, start_request, exc) -> None:
        preview_ok = self._try_edit_preview(values, weights)
        zh, en = self._exception_pair(exc)
        if not preview_ok:
            zh = "当前输入未生效\n" + zh
            en = "Current input is not applied\n" + en
        self._sram_input_invalid = False
        self._sram_error_text = (zh, en)
        self._store_applied_inputs(values, weights, count_changed, point_request, start_request)
        self._sram_result = None
        self._sram_write_btn.state(["disabled"])
        message = self._sram_text(zh, en)
        self.sram_status_var.set(message)
        self.rom_metric_var.set("SRAM")
        self.rom_frequency_var.set(message)
        self._paint_sram_preview()

    def _reject_sram_apply(self, zh: str, en: str) -> None:
        self._sram_input_invalid = True
        self._sram_error_text = (zh, en)
        self._sram_result = None
        self._sram_write_btn.state(["disabled"])
        self.sram_status_var.set(self._sram_text(zh, en))
        self.rom_metric_var.set("SRAM")
        self.rom_frequency_var.set(self._sram_text(zh, en))
        self._paint_sram_preview()

    def _exception_pair(self, exc: Exception) -> tuple[str, str]:
        if isinstance(exc, SegmentRatioCapacityError):
            return str(exc), exc.english
        if isinstance(exc, _SramInputError):
            return exc.zh, exc.en
        text = str(exc)
        return text, text

    def _equalize_sram_ratios(self) -> None:
        if self._sram_busy or self._sram_applying:
            return
        snapshot = [row["variable"].get() for row in self._sram_ratio_rows]
        mode = self._sram_ratio_mode
        weights = self._sram_explicit_weights
        self._sram_force_equal = True
        try:
            self._fill_equal_display()
            self._apply_sram_values()
        finally:
            self._sram_force_equal = False
        if self._sram_input_invalid:
            self._sram_ratio_mode = mode
            self._sram_explicit_weights = weights
            for row, text in zip(self._sram_ratio_rows, snapshot):
                row["variable"].set(text)
            self._refresh_ratio_total_text()

    def _committed_weights(self):
        if self._sram_ratio_mode != "explicit":
            return None
        return self._sram_explicit_weights

    def _generate_sram_shape(self, values, segment_weights=None, point_request=None):
        count = self._point_count_for(values, point_request)
        return generate_waveform(values, total_points=count, centered=True, segment_weights=segment_weights)

    def _try_edit_preview(self, values, weights) -> bool:
        try:
            points = generate_smooth_points(values, 1018, centered=True, segment_weights=weights)
            resolved = weights if weights is not None else (1,) * (len(values) - 1)
            intervals = allocate_segment_intervals(1018, resolved)
        except (ValueError, ZeroIntervalCandidate, ZeroDivisionError):
            return False
        self._store_preview(values, points, intervals)
        return True

    def _store_preview(self, controls, points, intervals) -> None:
        self._sram_preview_controls = list(controls)
        self._sram_preview_points = tuple(points)
        self._sram_preview_intervals = tuple(intervals)

    def _sram_device_key(self):
        serial = self.ctx.serial
        port = serial.selected_port_for_page() if hasattr(serial, "selected_port_for_page") else self.ctx.state.selected_port
        return (str(port), self._device_addr())

    def _sram_start_address(self, word_count, requested=None):
        if requested is None:
            requested = self._sram_start_request
        occupied = self._sram_occupied()
        if requested in ("", "auto", "自动"):
            old_range = self._sram_saved.get((self._sram_device_key(), int(self.sram_wave_var.get())))
            if old_range is not None:
                start = old_range[0]
                end = start + word_count - 1
                if end <= 0x1FF and all(end < lo or start > hi for lo, hi in occupied):
                    return start
            return allocate_words(word_count, occupied)
        start = int(requested, 0)
        end = start + word_count - 1
        if not 0x100 <= start <= end <= 0x1FF:
            raise ValueError("SRAM address must stay within 0x100..0x1FF")
        if any(not (end < lo or start > hi) for lo, hi in occupied):
            raise ValueError("SRAM address overlaps another waveform written in this session")
        return start

    def _sram_geometry(self):
        canvas_w = max(1, self.rom_canvas.winfo_width())
        canvas_h = max(1, self.rom_canvas.winfo_height())
        left = 52 if canvas_w >= 140 else 8
        top = 22
        bottom = 46 if canvas_h >= 120 else 20
        return left, top, max(1, canvas_w - left - 12), max(1, canvas_h - top - bottom)

    @staticmethod
    def _boxes_overlap(first, second, pad: int = 1) -> bool:
        return first[0] < second[2] + pad and second[0] < first[2] + pad and first[1] < second[3] + pad and second[1] < first[3] + pad

    def _sram_control_xs(self, left, width, point_count, intervals, control_count) -> list[float]:
        if control_count <= 1:
            return [float(left)]
        span = max(1, point_count - 1)
        if intervals and len(intervals) == control_count - 1 and sum(intervals) == span:
            xs = [float(left)]
            acc = 0
            for interval in intervals:
                acc += interval
                xs.append(left + acc / span * width)
            return xs
        return [left + index / (control_count - 1) * width for index in range(control_count)]

    def _sram_press(self, event) -> None:
        if self.rom_waveform_var.get() != "SRAM" or self._sram_busy:
            return
        left, top, width, height = self._sram_geometry()
        if not left <= event.x <= left + width or not top <= event.y <= top + height:
            return
        controls = self._sram_preview_controls or self._sram_controls
        xs = self._sram_control_xs(left, width, len(self._sram_preview_points), self._sram_preview_intervals, len(controls))
        if not xs:
            return
        self._sram_drag_index = min(
            range(len(xs)),
            key=lambda index: (round(abs(xs[index] - event.x), 6), index),
        )
        self._sram_drag(event)

    def _sram_drag(self, event) -> None:
        if self.rom_waveform_var.get() != "SRAM" or self._sram_drag_index is None or self._sram_busy:
            return
        if self._sram_input_invalid:
            return
        if not 0 <= self._sram_drag_index < len(self._sram_controls):
            return
        _left, top, _width, height = self._sram_geometry()
        value = max(self.MIN_CONTROL_POINT, min(100.0, (0.5 - (event.y - top) / height) * 200))
        self._sram_controls[self._sram_drag_index] = round(value, 3)
        self.sram_values_var.set(", ".join(f"{v:g}" for v in self._sram_controls))
        self._draw_sram_waveform()

    def _sram_release(self, _event) -> None:
        self._sram_drag_index = None
        if self.rom_waveform_var.get() == "SRAM" and not self._sram_input_invalid:
            self._draw_sram_waveform()

    def _sram_occupied(self):
        current = int(self.sram_wave_var.get())
        key = self._sram_device_key()
        return [(item[0], item[1]) for (device, number), item in self._sram_saved.items()
                if device == key and number != current]

    def _playback_caption(self, point_count: int, sample: int) -> str:
        return self._sram_text(
            f"点间隔 {sample} us · 播放时长 {point_count * sample} us（按 N×SAMPLE_TIME）· 横轴跨度 {(point_count - 1) * sample} us · VIN = 0%",
            f"Sample {sample} us · Playback {point_count * sample} us (N×SAMPLE_TIME) · Axis span {(point_count - 1) * sample} us · VIN = 0%")

    def _sample_time(self) -> int:
        try:
            return int(self.sram_sample_var.get())
        except ValueError:
            return 1

    def _draw_sram_waveform(self) -> None:
        if not hasattr(self, "_sram_frame") or self._sram_busy:
            return
        if self._sram_input_invalid:
            self._sram_result = None
            self._sram_write_btn.state(["disabled"])
            self._paint_sram_preview()
            if self._sram_error_text:
                message = self._sram_text(*self._sram_error_text)
                self.sram_status_var.set(message)
                self.rom_frequency_var.set(message)
            self.rom_metric_var.set("SRAM")
            return
        try:
            result = self._generate_sram_shape(self._sram_controls, self._committed_weights(), self._sram_point_request)
            self._sram_start_address(len(result.words), self._sram_start_request)
        except (ValueError, ZeroIntervalCandidate) as exc:
            self._sram_result = None
            self._sram_write_btn.state(["disabled"])
            if not self._try_edit_preview(self._sram_controls, self._committed_weights()):
                message = self._sram_text("当前输入未生效", "Current input is not applied") + "\n" + str(exc)
            else:
                zh, en = self._exception_pair(exc)
                message = self._sram_text(zh, en)
            self.sram_status_var.set(message)
            self.rom_metric_var.set("SRAM")
            self.rom_frequency_var.set(message)
            self._paint_sram_preview()
            return
        self._publish_sram_result(result)

    def _publish_sram_result(self, result) -> None:
        self._sram_result = result
        self._store_preview(self._sram_controls, result.points, result.segment_intervals)
        start = self._sram_start_address(len(result.words), self._sram_start_request)
        address_hint = f"0x{start:03X}–0x{start + len(result.words) - 1:03X}"
        sample = self._sample_time()
        self.rom_metric_var.set(
            f"SRAM  /  WAVE {self.sram_wave_var.get()}  /  {len(result.points)} points  /  {len(result.words)} words")
        self.rom_frequency_var.set(self._playback_caption(len(result.points), sample))
        ready = self._sram_text("可写入", "Ready to write") + (
            f"\n{address_hint}\nVAL2: {min(result.differences, default=0)} … {max(result.differences, default=0)}"
            + self._sram_text(
                "\n单次播放 · Auto Mirror 关闭\nSAMPLE_TIME 为芯片全局设置",
                "\nSingle playback · Auto Mirror off\nSAMPLE_TIME is global to the chip"))
        if self._sram_status_note:
            ready = self._sram_text(*self._sram_status_note) + "\n" + ready
        self.sram_status_var.set(ready)
        self._sram_write_btn.state(["!disabled"])
        self._paint_sram_preview()

    def _update_ratio_actuals(self) -> None:
        intervals = self._sram_preview_intervals
        points = self._sram_preview_points
        if not intervals or len(points) < 2 or len(intervals) != len(self._sram_ratio_rows):
            for row in self._sram_ratio_rows:
                row["actual_var"].set("")
            return
        span = len(points) - 1
        for row, interval in zip(self._sram_ratio_rows, intervals):
            percent = 100.0 * interval / span
            row["actual_var"].set(self._sram_text(
                f"{interval} 间隔 · {percent:.2f}%",
                f"{interval} intervals · {percent:.2f}%"))

    def _sram_paint_signature(self):
        canvas = self.rom_canvas
        try:
            size = (canvas.winfo_width(), canvas.winfo_height())
        except tk.TclError:
            return None
        colors = self.colors
        return (
            size,
            self._sram_preview_points,
            tuple(self._sram_preview_controls or ()),
            self._sram_preview_intervals,
            self._sample_time(),
            colors.get("primary"),
            colors.get("border"),
            colors.get("panel"),
            colors.get("muted"),
            colors.get("canvas_bg"),
            getattr(self, "language", None),
            bool(self._sram_input_invalid),
        )

    def _paint_sram_preview(self) -> None:
        canvas = self.rom_canvas
        signature = self._sram_paint_signature()
        if signature is not None and signature == getattr(self, "_sram_paint_key", None) and canvas.find_withtag("sram-curve"):
            return
        canvas.delete("all")
        left, top, width, height = self._sram_geometry()
        colors = self.colors
        points = self._sram_preview_points
        controls = self._sram_preview_controls or self._sram_controls
        intervals = self._sram_preview_intervals
        sample = self._sample_time()
        plot_points = points if len(points) >= 2 else (0, 0)
        for i in range(5):
            y = top + height * i / 4
            canvas.create_line(left, y, left + width, y, fill=colors["border"], dash=(2, 4))
            if 100 - i * 50 == 0:
                canvas.create_line(left, y, left + width, y, fill=colors["primary"], dash=(4, 4))
        placed = []
        if left >= 36:
            for i in (2, 0, 4, 1, 3):
                level = 100 - i * 50
                self._place_axis_text(
                    canvas, left - 6, top + height * i / 4,
                    "VIN 0%" if level == 0 else f"{level:+d}%",
                    "e", colors["muted"], placed)
        self._paint_time_axis(canvas, left, top, width, height, len(plot_points), sample, colors, placed)
        canvas.create_text(left, 12, text=self._sram_text("相对 Vpk 的波形", "Waveform relative to Vpk"), anchor="w", fill=colors["muted"])
        if len(points) >= 2:
            coords = []
            for index, value in enumerate(points):
                coords.extend((left + index / (len(points) - 1) * width, top + height * (1 - value / 3600)))
            canvas.create_line(*coords, fill=colors["primary"], width=2, tags="sram-curve")
        xs = self._sram_control_xs(left, width, len(points), intervals, len(controls))
        for x, value in zip(xs, controls):
            y = top + height * (1 - (value + 100) / 200)
            canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill=colors["panel"], outline=colors["primary"], width=2)
        self._update_ratio_actuals()
        self._sram_paint_key = signature

    def _place_axis_text(self, canvas, x, y, text, anchor, fill, placed) -> None:
        item = canvas.create_text(x, y, text=text, anchor=anchor, fill=fill, tags="sram-axis")
        box = canvas.bbox(item)
        if not box or any(self._boxes_overlap(box, other) for other in placed):
            canvas.delete(item)
            return
        placed.append(box)

    def _paint_time_axis(self, canvas, left, top, width, height, point_count, sample, colors, placed) -> None:
        span = max(0, point_count - 1) * sample
        gap = 56
        count = 2 if width < gap * 2 else min(5, max(2, int(width // gap) + 1))
        y = top + height + 12
        for index in range(count):
            frac = index / (count - 1)
            anchor = "w" if index == 0 else "e" if index == count - 1 else "center"
            x = left if anchor == "w" else left + width if anchor == "e" else left + width * frac
            self._place_axis_text(canvas, x, y, f"{span * frac:g}", anchor, colors["muted"], placed)
        if width >= 160 and self.rom_canvas.winfo_height() - y >= 18:
            self._place_axis_text(canvas, left + width, y + 14, "time / us", "e", colors["muted"], placed)

    def _sram_lock_widgets(self) -> None:
        widgets = []

        def walk(parent) -> None:
            for child in parent.winfo_children():
                if isinstance(child, (ttk.Button, ttk.Entry, ttk.Combobox, ttk.Spinbox)):
                    widgets.append(child)
                walk(child)

        walk(self._sram_frame)
        for widget in self._sram_ratio_lock_widgets:
            if widget not in widgets:
                widgets.append(widget)
        self._sram_locked_widgets = []
        for widget in widgets:
            try:
                state = str(widget.cget("state"))
            except tk.TclError:
                continue
            self._sram_locked_widgets.append((widget, state))
            widget.configure(state="disabled")

    def _sram_unlock_widgets(self) -> None:
        for widget, state in getattr(self, "_sram_locked_widgets", ()):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def _start_sram_write(self) -> None:
        """Snapshot all Tk values, then keep serial I/O off the UI thread."""
        if self._sram_busy:
            return
        self._apply_sram_values()
        if self._sram_result is None or self._sram_input_invalid or "disabled" in self._sram_write_btn.state():
            return
        if not self._ensure_connected():
            return
        serial = self.ctx.serial
        manager = serial._current_manager() if hasattr(serial, "_current_manager") else serial
        if manager is None:
            return
        address = self._device_addr()
        wave = int(self.sram_wave_var.get())
        result = self._sram_result
        try:
            start_address = self._sram_start_address(len(result.words), self._sram_start_request)
            occupied = tuple(self._sram_occupied())
        except ValueError as exc:
            self._reject_sram_apply(str(exc), str(exc))
            return
        sample_time = int(self.sram_sample_var.get())
        vpk = self.ROM_AMPLITUDE_OPTIONS.index(self.rom_amplitude_var.get())
        offset = self.ROM_OFFSET_OPTIONS.index(self.rom_offset_var.get())
        port = serial.selected_port_for_page() if hasattr(serial, "selected_port_for_page") else self.ctx.state.selected_port
        key = (str(port), address)
        previous_range = self._sram_saved.get((key, wave))
        shared_state = self.owner._shared_state_for(address)
        self.owner._invalidate_sinc_verified()
        messages = Queue()
        self._sram_busy = True
        self._sram_lock_widgets()
        self._sram_progress.configure(value=0)
        self._sram_progress.grid()
        self.sram_status_var.set(self._sram_text("正在写入 SRAM…", "Writing SRAM…"))

        def worker():
            def read(reg):
                response = manager.read_register_value(address, reg, bits=16)
                if not response.ok:
                    raise IOError(getattr(response, "error", "") or "Serial read failed")
                return manager.extract_rdataw_value(response.data, device_addr=address, reg_addr=reg, bits=16)

            def write(reg, value):
                response = manager.write_register_value(address, reg, value, bits=16)
                if not response.ok:
                    raise IOError(getattr(response, "error", "") or "Serial write failed")
                return True

            try:
                with manager.snapshot_transaction() if hasattr(manager, "snapshot_transaction") else nullcontext():
                    if shared_state is not None and shared_state.active:
                        if shared_state.detection_active:
                            write(0x02, 0x0002)
                            shared_state.detection_active = False
                            shared_state.detection_device_addr = None
                        write(0x02, 0x0008)
                        shared_state.waveform_play_active = False
                    start, descriptor = write_waveform(
                        read, write, wave, result, sample_time, vpk, offset,
                        progress=lambda percent: messages.put(("progress", percent)),
                        occupied=occupied, start_address=start_address,
                        previous_range=previous_range)
                messages.put(("done", (start, descriptor)))
            except Exception as exc:
                messages.put(("error", str(exc)))

        def poll():
            self._sram_poll_after = None
            try:
                while True:
                    kind, value = messages.get_nowait()
                    if kind == "progress":
                        self._sram_progress.configure(value=value)
                        self.sram_status_var.set(self._sram_text("正在写入 SRAM", "Writing SRAM") + f" · {value}%")
                        continue
                    self._sram_busy = False
                    self._sram_unlock_widgets()
                    self._sram_progress.grid_remove()
                    if kind == "error":
                        self.sram_status_var.set(self._sram_text("写入失败：", "Write failed: ") + value)
                        messagebox.showerror(self.tr("error"), value)
                    else:
                        start, descriptor = value
                        self._sram_saved[(key, wave)] = (start, start + len(result.words) - 1)
                        if address == self._device_addr():
                            self.owner.auto_serial_var.set(False)
                            self.owner.rom_slice_var.set(f"slice{wave}")
                        self.sram_status_var.set(self._sram_text("写入完成", "Write complete") +
                            f" · {port} / 0x{address:02X} / WAVE {wave}\n0x{start:03X}–0x{start + len(result.words)-1:03X}")
                    return
            except Empty:
                self._sram_poll_after = self.after(40, poll)

        threading.Thread(target=worker, daemon=True).start()
        self._sram_poll_after = self.after(40, poll)

    def _sram_destroy(self, event) -> None:
        if event.widget is not self:
            return
        for name in ("_sram_poll_after", "_sram_redraw_job", "_sram_layout_job", "_sram_wrap_job"):
            job = getattr(self, name, None)
            if not job:
                continue
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
            setattr(self, name, None)
            self._sram_poll_after = None
