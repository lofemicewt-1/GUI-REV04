"""AWP1221 realtime ADC display and SRAM feature entry point.

The GUI page only supplies register I/O and common page services.  Realtime
window/lifecycle code lives here, while the SRAM editor is re-exported from
this module so callers have one stable integration point for both features.
"""
from __future__ import annotations

import time
from collections import deque
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from core.base_tool import run_async_write

try:
    from pages.awp1221_sram_editor import AWP1221SramEditor
except ImportError:
    AWP1221SramEditor = None


AWP1221_ADC_SETTINGS_REGISTER = 0x05
AWP1221_ADC_LIVE_REGISTER = 0x13
AWP1221_ADC_LOAD_REGISTER = 0x1F
AWP1221_REALTIME_POLL_MS = 10
AWP1221_REALTIME_POLL_OPTIONS = ("10", "20", "50", "100", "300")


def decode_awp1221_adc_value(raw: int) -> int:
    """Decode register 0x13 as a signed 16-bit ADC sample."""
    word = int(raw) & 0xFFFF
    return word if word < 0x8000 else word - 0x10000


THRESHOLD_UPPER_COLOR = "#2563EB"
THRESHOLD_LOWER_COLOR = "#9333EA"
VIBRATION_BAND_COLOR = "#86EFAC"


def awp1221_threshold_adc_counts(code: int, use_high_12_bits: bool) -> int:
    value = int(code) & 0xFFF
    return value << 4 if use_high_12_bits else value


def awp1221_adc_counts_to_mv(
    counts: float,
    sample_us: float,
    load_ua: float,
    capacitance_uf: float,
) -> float:
    return float(counts) * float(sample_us) * float(load_ua) / float(capacitance_uf) / 1000.0


def awp1221_plot_ceiling(*magnitudes: float) -> float:
    peak = max((abs(float(value)) for value in magnitudes), default=0.0)
    return max(1.0, peak * 1.15)


def awp1221_vibration_band_seconds(
    waveform_period_s: float,
    event_repeat: int,
    debounce_ms: float,
) -> float:
    period = max(0.0, float(waveform_period_s))
    debounce = max(0.0, float(debounce_ms)) / 1000.0
    return max(int(event_repeat), 1) * period + debounce


def awp1221_visible_band(
    start: float,
    duration_s: float,
    now: float,
    span: float,
) -> tuple[float, float] | None:
    if span <= 0 or duration_s <= 0:
        return None
    left = now - span
    right = now
    band_left = max(float(start), left)
    band_right = min(float(start) + float(duration_s), right)
    if band_right <= band_left:
        return None
    return band_left, band_right


def _schedule_ui(widget: tk.Misc, callback: Callable[..., None], *args: Any) -> None:
    try:
        widget.after(0, callback, *args)
    except tk.TclError:
        pass


def _run_ui_background_task(
    widget: tk.Misc,
    task: Callable[[], Any],
    *,
    on_done: Callable[[Any], None],
    on_error: Callable[[Exception], None],
) -> None:
    """Run serial work off Tk's UI thread and marshal callbacks back to Tk."""
    run_async_write(
        task,
        on_done=lambda result: _schedule_ui(widget, on_done, result),
        on_error=lambda error: _schedule_ui(widget, on_error, error),
    )


class ADCVoltageWindow(tk.Toplevel):
    """Live voltage plot; selectors describe conversion and do not write the chip."""

    def __init__(self, parent: tk.Widget, on_close: Callable[[], None]) -> None:
        super().__init__(parent)
        self.title("AWP1221 实时电压")
        self.geometry("900x560")
        self.minsize(620, 380)
        self.protocol("WM_DELETE_WINDOW", on_close)
        self.sample_time = tk.StringVar(value="4 us")
        self.load = tk.StringVar(value="40 uA")
        self.capacitance = tk.StringVar(value="2")
        self.time_scale = tk.StringVar(value="10")
        self.poll_interval = tk.StringVar(value=str(AWP1221_REALTIME_POLL_MS))
        self.voltage = tk.StringVar(value="— mV")
        self.status = tk.StringVar(value="正在读取...")
        self.points: deque[tuple[float, float]] = deque(maxlen=2000)
        self.upper_counts: int | None = None
        self.lower_counts: int | None = None
        self.vibration_bands: deque[tuple[float, float]] = deque(maxlen=64)

        controls = ttk.Frame(self, padding=12)
        controls.pack(fill="x")
        for column in range(5):
            controls.columnconfigure(column, weight=1)
        for column, (label, variable, options) in enumerate((
            ("ADC_Sample_Tim", self.sample_time, ("4 us", "8 us", "16 us", "32 us")),
            ("ADC_LOAD", self.load, ("40 uA", "160 uA")),
        )):
            ttk.Label(controls, text=label).grid(row=0, column=column, sticky="w")
            combo = ttk.Combobox(controls, textvariable=variable, values=options, state="readonly", width=15)
            combo.grid(row=1, column=column, sticky="ew", padx=(0, 12))
            combo.bind("<<ComboboxSelected>>", lambda _event: self.redraw())
        ttk.Label(controls, text="C (uF)").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.capacitance, width=10).grid(row=1, column=2, sticky="ew", padx=(0, 12))
        ttk.Label(controls, text="Time scale (s)").grid(row=0, column=3, sticky="w")
        ttk.Combobox(controls, textvariable=self.time_scale, values=("5", "10", "30", "60"), width=10).grid(
            row=1, column=3, sticky="ew", padx=(0, 12)
        )
        ttk.Label(controls, text="Poll (ms)").grid(row=0, column=4, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.poll_interval,
            values=AWP1221_REALTIME_POLL_OPTIONS,
            state="readonly",
            width=10,
        ).grid(row=1, column=4, sticky="ew")
        self.capacitance.trace_add("write", lambda *_: self.redraw())
        self.time_scale.trace_add("write", lambda *_: self.redraw())
        ttk.Label(self, textvariable=self.voltage, font=("Arial", 20, "bold")).pack(anchor="w", padx=14)
        ttk.Label(self, textvariable=self.status).pack(anchor="w", padx=14)
        self.canvas = tk.Canvas(self, background="white", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=12, pady=12)
        self.canvas.bind("<Configure>", lambda _event: self.redraw())

    def poll_interval_ms(self) -> int:
        try:
            value = int(str(self.poll_interval.get()).strip())
        except ValueError:
            return AWP1221_REALTIME_POLL_MS
        return value if value > 0 else AWP1221_REALTIME_POLL_MS

    def set_threshold_counts(self, upper: int | None, lower: int | None) -> None:
        self.upper_counts = upper
        self.lower_counts = lower
        self.redraw()

    def add_vibration_band(self, duration_s: float) -> None:
        width = float(duration_s)
        if width <= 0:
            return
        self.vibration_bands.append((time.monotonic(), width))
        self.redraw()

    def _conversion_params(self) -> tuple[float, float, float] | None:
        try:
            capacitance = float(self.capacitance.get())
            sample_us = float(self.sample_time.get().split()[0])
            load_ua = float(self.load.get().split()[0])
            if capacitance <= 0:
                raise ValueError
        except ValueError:
            return None
        return sample_us, load_ua, capacitance

    def add_sample(self, raw: int) -> None:
        signed = decode_awp1221_adc_value(raw)
        self.points.append((time.monotonic(), signed))
        self.status.set(f"ADC 0x13: 0x{raw & 0xFFFF:04X} ({signed})")
        self.redraw()

    def redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 100 or height < 100:
            return
        left, right, top, bottom = 65, width - 20, 20, height - 45
        params = self._conversion_params()
        try:
            span = float(self.time_scale.get())
            if span <= 0 or params is None:
                raise ValueError
            duration, current, capacitance = params
        except ValueError:
            self.voltage.set("请输入有效的 C 和时间范围")
            return
        values = [
            (stamp, awp1221_adc_counts_to_mv(signed, duration, current, capacitance))
            for stamp, signed in self.points
        ]
        self.voltage.set(f"{values[-1][1]:.3f} mV" if values else "— mV")
        now = time.monotonic()
        visible = [(stamp, value) for stamp, value in values if stamp >= now - span]
        upper_mv = (
            awp1221_adc_counts_to_mv(self.upper_counts, duration, current, capacitance)
            if self.upper_counts is not None
            else None
        )
        lower_mv = (
            awp1221_adc_counts_to_mv(self.lower_counts, duration, current, capacitance)
            if self.lower_counts is not None
            else None
        )
        ceiling = awp1221_plot_ceiling(
            *(value for _, value in visible),
            *(item for item in (upper_mv, lower_mv) if item is not None),
        )
        def y_at(value: float) -> float:
            return (top + bottom) / 2 - value / ceiling * (bottom - top) / 2
        def x_at(stamp: float) -> float:
            return left + (stamp - now + span) / span * (right - left)
        for start, band_duration in self.vibration_bands:
            visible_band = awp1221_visible_band(start, band_duration, now, span)
            if visible_band is None:
                continue
            canvas.create_rectangle(
                x_at(visible_band[0]),
                top,
                x_at(visible_band[1]),
                bottom,
                fill=VIBRATION_BAND_COLOR,
                outline="",
            )
        for index in range(6):
            y = bottom - (bottom - top) * index / 5
            canvas.create_line(left, y, right, y, fill="#e4e8ed")
            canvas.create_text(left - 8, y, text=f"{ceiling * (2 * index / 5 - 1):.1f}", anchor="e", fill="#596575")
            x = left + (right - left) * index / 5
            canvas.create_line(x, top, x, bottom, fill="#e4e8ed")
            canvas.create_text(x, bottom + 15, text=f"{-span + span * index / 5:.0f}", fill="#596575")
        canvas.create_text(12, top, text="mV", anchor="w")
        canvas.create_text(right, bottom + 32, text="Time (s)", anchor="e")
        for mv, color, label in (
            (upper_mv, THRESHOLD_UPPER_COLOR, "上阈值"),
            (lower_mv, THRESHOLD_LOWER_COLOR, "下阈值"),
        ):
            if mv is None:
                continue
            y = y_at(mv)
            canvas.create_line(left, y, right, y, fill=color, dash=(6, 4), width=2)
            canvas.create_text(right - 4, y - 8, text=f"{label} {mv:.3f} mV", anchor="e", fill=color)
        coords: list[float] = []
        for stamp, value in visible:
            coords.extend((x_at(stamp), y_at(value)))
        if len(coords) >= 4:
            canvas.create_line(*coords, fill="#2f80ed", width=2)


class AWP1221RealtimeMixin:
    """Reusable realtime-display lifecycle for an AWP1221 Tk page.

    The host must provide ``ctx``, ``device_addr()``, ``_ensure_connected()``,
    ``_read_register_value()``, ``_ensure_reg_1f_bit3()``, ``_request_adc_value_read()`` and
    ``_refresh_adc_read_buttons()``.
    """

    def _init_adc_realtime(self) -> None:
        self._adc_realtime_active = False
        self._adc_realtime_job: str | None = None
        self._adc_voltage_window: ADCVoltageWindow | None = None

    def toggle_adc_realtime(self) -> None:
        if self._adc_realtime_active:
            self._stop_adc_realtime()
            return
        if not self._ensure_connected():
            return
        self._adc_voltage_window = ADCVoltageWindow(self, self._stop_adc_realtime)
        self._adc_voltage_window.transient(self.winfo_toplevel())
        self._adc_realtime_active = True
        self._refresh_adc_read_buttons()
        self._load_adc_voltage_settings()

    def _load_adc_voltage_settings(self) -> None:
        try:
            address = self.device_addr()
        except Exception:
            return
        target_window = self._adc_voltage_window

        def read_settings() -> tuple[int, int]:
            load = self._ensure_reg_1f_bit3(address)
            settings = self._read_register_value(AWP1221_ADC_SETTINGS_REGISTER, address)
            return settings, load

        def apply_settings(values: tuple[int, int]) -> None:
            window = self._adc_voltage_window
            if self._adc_realtime_active and window is target_window and window is not None and window.winfo_exists():
                window.sample_time.set(("4 us", "8 us", "16 us", "32 us")[(values[0] >> 10) & 3])
                window.load.set("160 uA" if values[1] & (1 << 11) else "40 uA")
                window.redraw()
                self._poll_adc_realtime()

        def settings_error(error: Exception) -> None:
            self._show_adc_voltage_error(error)

        _run_ui_background_task(self, read_settings, on_done=apply_settings, on_error=settings_error)

    def _show_adc_voltage_error(self, error: Exception) -> None:
        window = self._adc_voltage_window
        if window is not None and window.winfo_exists():
            window.status.set(f"读取失败: {error}")

    def _poll_adc_realtime(self) -> None:
        self._adc_realtime_job = None
        if not self._adc_realtime_active:
            return
        if not self.ctx.serial.is_connected:
            self._stop_adc_realtime()
            return
        if not self._request_adc_value_read(
            show_error=False,
            on_complete=self._schedule_next_adc_poll,
            register=AWP1221_ADC_LIVE_REGISTER,
        ):
            self._schedule_next_adc_poll()

    def _schedule_next_adc_poll(self) -> None:
        if not self._adc_realtime_active:
            return
        delay = AWP1221_REALTIME_POLL_MS
        window = self._adc_voltage_window
        if window is not None and window.winfo_exists():
            delay = window.poll_interval_ms()
        self._adc_realtime_job = self.after(delay, self._poll_adc_realtime)

    def _stop_adc_realtime(self) -> None:
        self._adc_realtime_active = False
        window = self._adc_voltage_window
        self._adc_voltage_window = None
        if window is not None and window.winfo_exists():
            window.destroy()
        if self._adc_realtime_job is not None:
            try:
                self.after_cancel(self._adc_realtime_job)
            except Exception:
                pass
        self._adc_realtime_job = None
        self._refresh_adc_read_buttons()


__all__ = [
    "ADCVoltageWindow",
    "AWP1221RealtimeMixin",
    "AWP1221SramEditor",
    "AWP1221_ADC_LIVE_REGISTER",
    "AWP1221_REALTIME_POLL_OPTIONS",
    "THRESHOLD_LOWER_COLOR",
    "THRESHOLD_UPPER_COLOR",
    "VIBRATION_BAND_COLOR",
    "awp1221_adc_counts_to_mv",
    "awp1221_plot_ceiling",
    "awp1221_threshold_adc_counts",
    "awp1221_vibration_band_seconds",
    "awp1221_visible_band",
    "decode_awp1221_adc_value",
]
