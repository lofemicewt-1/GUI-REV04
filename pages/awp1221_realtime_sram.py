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

from core.awp1221_gpio import (
    AWP1221_GPIO_REGISTER,
    GpioChannelBits,
    pack_gpio_channel,
    unpack_gpio_register,
)
from core.base_tool import GPIO_MODE_DETECT, parse_gpio_read_response, run_async_write

from pages.awp1221_sram_editor import AWP1221SramEditor


AWP1221_ADC_SETTINGS_REGISTER = 0x05
AWP1221_ADC_LIVE_REGISTER = 0x13
AWP1221_ADC_LOAD_REGISTER = 0x1F
AWP1221_ADC_LOAD_BIT = 1 << 2
AWP1221_ADC_SAMPLE_TIME_OPTIONS = ("4 us", "8 us", "16 us", "32 us")
AWP1221_ADC_SAMPLE_TIME_MASK = 0x0C00
AWP1221_REALTIME_POLL_MS = 10
AWP1221_REALTIME_POLL_OPTIONS = ("10", "20", "50", "100", "300")
AWP1221_VIBRATION_GPIO_FUNCTION = 0x01
AWP1221_VIBRATION_MCU_PIN = "D2"


def awp1221_adc_load_label(register_1f: int) -> str:
    return "160 uA" if int(register_1f) & AWP1221_ADC_LOAD_BIT else "40 uA"


def awp1221_merge_adc_load(current: int, load: str) -> int:
    value = int(current) & 0xFFFF
    if str(load) == "160 uA":
        return value | AWP1221_ADC_LOAD_BIT
    return value & ~AWP1221_ADC_LOAD_BIT


def awp1221_merge_sample_time(current: int, sample_time: str) -> int:
    code = AWP1221_ADC_SAMPLE_TIME_OPTIONS.index(str(sample_time))
    value = int(current) & 0xFFFF
    return (value & ~AWP1221_ADC_SAMPLE_TIME_MASK) | (code << 10)


def decode_awp1221_adc_value(raw: int) -> int:
    """Decode register 0x13 as a signed 16-bit ADC sample."""
    word = int(raw) & 0xFFFF
    return word if word < 0x8000 else word - 0x10000


THRESHOLD_UPPER_COLOR = "#2563EB"
THRESHOLD_LOWER_COLOR = "#9333EA"
VIBRATION_BAND_COLOR = "#86EFAC"
VIBRATION_BANDS_VISIBLE = False


def awp1221_threshold_adc_counts(code: int, use_high_12_bits: bool) -> int:
    value = int(code) & 0xFFF
    if value & 0x800:
        value -= 0x1000
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


def awp1221_vibration_falling_edge(previous: int | None, current: int) -> bool:
    return previous != 0 and int(current) == 0


def awp1221_zero_time_seconds(label: str) -> float:
    text = str(label).strip().lower().split()
    if len(text) < 2:
        return 0.0
    try:
        value = float(text[0])
    except ValueError:
        return 0.0
    unit = text[1]
    if unit == "ms":
        return max(0.0, value) / 1000.0
    if unit == "us":
        return max(0.0, value) / 1_000_000.0
    if unit == "s":
        return max(0.0, value)
    return 0.0


def awp1221_vibration_band_seconds(
    waveform_period_s: float,
    event_repeat: int,
    debounce_ms: float,
    *,
    debounce_enabled: bool = True,
    zero_time_s: float = 0.0,
) -> float:
    period = max(0.0, float(waveform_period_s))
    zero = max(0.0, float(zero_time_s))
    debounce = max(0.0, float(debounce_ms)) / 1000.0 if debounce_enabled else 0.0
    return max(int(event_repeat), 1) * period + zero + debounce


def awp1221_visible_band(
    start: float,
    duration_s: float,
    now: float,
    span: float,
    min_width_s: float = 0.0,
) -> tuple[float, float] | None:
    if span <= 0:
        return None
    duration = max(0.0, float(duration_s), float(min_width_s))
    if duration <= 0:
        return None
    window_left = now - span
    window_right = now
    band_left = float(start)
    band_right = float(start) + duration
    if band_right < window_left or band_left > window_right:
        return None
    draw_left = max(band_left, window_left)
    draw_right = min(band_right, window_right)
    if draw_right <= draw_left:
        draw_right = window_right
        draw_left = max(window_left, window_right - duration)
    if draw_right <= draw_left:
        return None
    return draw_left, draw_right


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

    def __init__(
        self,
        parent: tk.Widget,
        on_close: Callable[[], None],
        on_settings_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("AWP1221 实时电压")
        self.geometry("900x560")
        self.minsize(620, 380)
        self.protocol("WM_DELETE_WINDOW", on_close)
        self.on_settings_change = on_settings_change
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
            ("ADC_SAMPLE_TIME", self.sample_time, AWP1221_ADC_SAMPLE_TIME_OPTIONS),
            ("ADC_LOAD", self.load, ("40 uA", "160 uA")),
        )):
            ttk.Label(controls, text=label).grid(row=0, column=column, sticky="w")
            combo = ttk.Combobox(controls, textvariable=variable, values=options, state="readonly", width=15)
            combo.grid(row=1, column=column, sticky="ew", padx=(0, 12))
            combo.bind("<<ComboboxSelected>>", lambda _event: self._on_conversion_or_settings())
        ttk.Label(controls, text="陶瓷电容等效容值 (uF)").grid(row=0, column=2, sticky="w")
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
        self.canvas = tk.Canvas(self, background="#FFFFFF", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=12, pady=12)
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self._theme_colors: dict[str, str] = {}

    def _on_conversion_or_settings(self) -> None:
        self.redraw()
        callback = self.on_settings_change
        if callback is not None:
            callback()

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

    def refresh_theme(self, colors: dict[str, str] | None = None) -> None:
        if colors:
            self._theme_colors = dict(colors)
        theme = getattr(self, "_theme_colors", {})
        bg = theme.get("bg", "#F5F7FA")
        canvas_bg = theme.get("canvas_bg", bg)
        try:
            self.configure(bg=bg)
        except tk.TclError:
            pass
        self.canvas.configure(background=canvas_bg, highlightbackground=theme.get("border", canvas_bg))
        self.redraw()

    def redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 100 or height < 100:
            return
        left, right, top, bottom = 82, width - 20, 28, height - 45
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
        if VIBRATION_BANDS_VISIBLE:
            min_width_s = 8.0 / max(right - left, 1.0) * span
            for start, band_duration in self.vibration_bands:
                visible_band = awp1221_visible_band(
                    start,
                    band_duration,
                    now,
                    span,
                    min_width_s=min_width_s,
                )
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
        theme = getattr(self, "_theme_colors", {})
        grid = theme.get("border", "#e4e8ed")
        axis = theme.get("muted", "#596575")
        label = theme.get("text", "#111827")
        curve = theme.get("primary", "#2f80ed")
        for index in range(6):
            y = bottom - (bottom - top) * index / 5
            canvas.create_line(left, y, right, y, fill=grid)
            canvas.create_text(left - 8, y, text=f"{ceiling * (2 * index / 5 - 1):.1f}", anchor="e", fill=axis)
            x = left + (right - left) * index / 5
            canvas.create_line(x, top, x, bottom, fill=grid)
            canvas.create_text(x, bottom + 15, text=f"{-span + span * index / 5:.0f}", fill=axis)
        canvas.create_text(left - 8, 10, text="mV", anchor="e", fill=label)
        canvas.create_text(right, bottom + 32, text="Time (s)", anchor="e", fill=label)
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
            canvas.create_line(*coords, fill=curve, width=2)


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
        self._vibration_gpio_level: int | None = None

    def toggle_adc_realtime(self) -> None:
        if self._adc_realtime_active:
            self._stop_adc_realtime()
            return
        if not self._ensure_connected():
            return
        self._adc_voltage_window = ADCVoltageWindow(
            self,
            self._stop_adc_realtime,
            on_settings_change=self._apply_adc_voltage_settings,
        )
        colors = getattr(getattr(self, "ctx", None), "colors", None)
        if isinstance(colors, dict):
            self._adc_voltage_window.refresh_theme(colors)
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
            try:
                self._prepare_vibration_gpio(address)
            except Exception:
                pass
            settings = self._read_register_value(AWP1221_ADC_SETTINGS_REGISTER, address)
            return settings, load

        def apply_settings(values: tuple[int, int]) -> None:
            window = self._adc_voltage_window
            if self._adc_realtime_active and window is target_window and window is not None and window.winfo_exists():
                window.sample_time.set(AWP1221_ADC_SAMPLE_TIME_OPTIONS[(values[0] >> 10) & 3])
                load = awp1221_adc_load_label(values[1])
                window.load.set(load)
                page_load = getattr(self, "adc_ldo_var", None)
                if page_load is not None:
                    page_load.set(load)
                window.redraw()
                self._poll_adc_realtime()

        def settings_error(error: Exception) -> None:
            self._show_adc_voltage_error(error)
            if self._adc_realtime_active and self._adc_voltage_window is target_window:
                self._poll_adc_realtime()

        _run_ui_background_task(self, read_settings, on_done=apply_settings, on_error=settings_error)

    def _write_adc_voltage_settings(self, address: int) -> None:
        window = self._adc_voltage_window
        if window is None:
            return
        sample = window.sample_time.get()
        load = window.load.get()
        current_05 = self._read_register_value(AWP1221_ADC_SETTINGS_REGISTER, address)
        current_1f = self._read_register_value(AWP1221_ADC_LOAD_REGISTER, address)
        self._write_register_value(
            AWP1221_ADC_SETTINGS_REGISTER,
            awp1221_merge_sample_time(current_05, sample),
            address,
        )
        self._write_register_value(
            AWP1221_ADC_LOAD_REGISTER,
            awp1221_merge_adc_load(current_1f, load),
            address,
        )
        setter = getattr(self, "adc_sample_update_var", None)
        if setter is not None:
            after = getattr(self, "after", None)
            if callable(after):
                after(0, setter.set, sample)
            else:
                setter.set(sample)
        load_setter = getattr(self, "adc_ldo_var", None)
        if load_setter is not None:
            after = getattr(self, "after", None)
            if callable(after):
                after(0, load_setter.set, load)
            else:
                load_setter.set(load)

    def _apply_adc_voltage_settings(self) -> None:
        window = self._adc_voltage_window
        if window is None or not window.winfo_exists():
            return
        try:
            address = self.device_addr()
        except Exception as error:
            self._show_adc_voltage_error(error)
            return

        target_window = window

        def apply_settings(_result: Any) -> None:
            current = self._adc_voltage_window
            if current is target_window and current is not None and current.winfo_exists():
                current.redraw()

        _run_ui_background_task(
            self,
            lambda: self._write_adc_voltage_settings(address),
            on_done=apply_settings,
            on_error=self._show_adc_voltage_error,
        )

    def _show_adc_voltage_error(self, error: Exception) -> None:
        window = self._adc_voltage_window
        if window is not None and window.winfo_exists():
            window.status.set(f"读取失败: {error}")

    def _prepare_vibration_gpio(self, address: int) -> None:
        current = self._read_register_value(AWP1221_GPIO_REGISTER, address)
        gpio0, _gpio1, _reserved = unpack_gpio_register(current)
        updated = pack_gpio_channel(
            current,
            0,
            GpioChannelBits(AWP1221_VIBRATION_GPIO_FUNCTION, gpio0.open_drain),
        )
        if updated != current:
            self._write_register_value(AWP1221_GPIO_REGISTER, updated, address)
        response = self.ctx.serial.configure_gpio(AWP1221_VIBRATION_MCU_PIN, GPIO_MODE_DETECT)
        if not getattr(response, "ok", False):
            raise ValueError(getattr(response, "error", None) or "GPIOCFG D2 DETECT failed")
        invalidate = getattr(getattr(self, "host", None), "_invalidate_gpio_settings", None)
        if callable(invalidate):
            invalidate()

    def _read_vibration_gpio_level(self) -> int:
        response = self.ctx.serial.read_gpio(AWP1221_VIBRATION_MCU_PIN)
        if not getattr(response, "ok", False):
            raise ValueError(getattr(response, "error", None) or "GPIOR D2 failed")
        return parse_gpio_read_response(getattr(response, "data", "")).level

    def _note_vibration_gpio_level(self, level: int | None) -> None:
        previous = self._vibration_gpio_level
        if level is None:
            return
        if (
            awp1221_vibration_falling_edge(previous, level)
            and self._adc_realtime_active
        ):
            window = self._adc_voltage_window
            if window is not None and window.winfo_exists():
                getter = getattr(self, "_realtime_vibration_duration_s", None)
                duration = getter() if callable(getter) else 0.0
                window.add_vibration_band(duration if duration > 0 else 0.001)
        self._vibration_gpio_level = int(level)

    def _poll_adc_realtime(self) -> None:
        self._adc_realtime_job = None
        if not self._adc_realtime_active:
            return
        if not self.ctx.serial.is_connected:
            self._stop_adc_realtime()
            return
        if getattr(self, "_adc_read_in_flight", False):
            self._schedule_next_adc_poll()
            return
        try:
            address = self.device_addr()
        except Exception:
            self._schedule_next_adc_poll()
            return
        generation = getattr(self, "_adc_lifecycle_generation", 0)
        self._adc_read_in_flight = True

        def work() -> tuple[int | None, int]:
            with self.ctx.serial.suppress_logging():
                level: int | None
                try:
                    level = self._read_vibration_gpio_level()
                except Exception:
                    level = None
                value = self._read_register_value(AWP1221_ADC_LIVE_REGISTER, address)
                return level, value

        def on_done(result: tuple[int | None, int]) -> None:
            level, value = result
            self._note_vibration_gpio_level(level)
            self._finish_adc_value_read(
                value=value,
                error=None,
                show_error=False,
                generation=generation,
                on_complete=self._schedule_next_adc_poll,
                register=AWP1221_ADC_LIVE_REGISTER,
            )
            window = self._adc_voltage_window
            if window is not None and window.winfo_exists():
                if level is None:
                    marker = "D2=?"
                elif int(level) == 0:
                    marker = "On Play"
                else:
                    marker = "D2=1"
                window.status.set(f"{marker} · ADC 0x13: 0x{value & 0xFFFF:04X}")

        def on_error(error: Exception) -> None:
            self._finish_adc_value_read(
                value=None,
                error=error,
                show_error=False,
                generation=generation,
                on_complete=self._schedule_next_adc_poll,
                register=AWP1221_ADC_LIVE_REGISTER,
            )

        _run_ui_background_task(self, work, on_done=on_done, on_error=on_error)

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
        self._vibration_gpio_level = None
        self._refresh_adc_read_buttons()


__all__ = [
    "ADCVoltageWindow",
    "AWP1221RealtimeMixin",
    "AWP1221SramEditor",
    "AWP1221_ADC_LIVE_REGISTER",
    "AWP1221_ADC_LOAD_BIT",
    "AWP1221_ADC_LOAD_REGISTER",
    "AWP1221_ADC_SAMPLE_TIME_MASK",
    "AWP1221_ADC_SAMPLE_TIME_OPTIONS",
    "AWP1221_ADC_SETTINGS_REGISTER",
    "AWP1221_REALTIME_POLL_OPTIONS",
    "AWP1221_VIBRATION_GPIO_FUNCTION",
    "THRESHOLD_LOWER_COLOR",
    "THRESHOLD_UPPER_COLOR",
    "VIBRATION_BAND_COLOR",
    "awp1221_adc_counts_to_mv",
    "awp1221_adc_load_label",
    "awp1221_merge_adc_load",
    "awp1221_merge_sample_time",
    "awp1221_plot_ceiling",
    "awp1221_threshold_adc_counts",
    "awp1221_vibration_falling_edge",
    "awp1221_vibration_band_seconds",
    "awp1221_zero_time_seconds",
    "awp1221_visible_band",
    "decode_awp1221_adc_value",
]
