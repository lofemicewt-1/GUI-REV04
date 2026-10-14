BASE: 1a3e71b
HEAD: cd12818

## Commits

cd12818 Overlay AWP1221 realtime thresholds and press vibration bands.
49a20c0 Restore AWP1221 visible-band zero-width reject.
26dc258 Draw AWP1221 realtime threshold lines and vibration bands.
4a5cd5a Add AWP1221 realtime overlay conversion helpers.

## Stat

 pages/awp1221_page.py                  | 142 +++++++++++++++++++++++-
 pages/awp1221_realtime_sram.py         | 141 ++++++++++++++++++++++--
 tests/test_awp1221_realtime_overlay.py | 191 +++++++++++++++++++++++++++++++++
 3 files changed, 463 insertions(+), 11 deletions(-)

## Diff
```diff

diff --git a/pages/awp1221_page.py b/pages/awp1221_page.py
index 3b6e99f..71a4ba1 100644
--- a/pages/awp1221_page.py
+++ b/pages/awp1221_page.py
@@ -30,20 +30,22 @@ from core.base_tool import (
     GPIO_MODE_DETECT,
     GPIO_MODE_HIGH,
     GPIO_MODE_LOW,
     run_async_write,
 )
 from pages.base_page import BasePage, SimpleTooltip
 from pages.awp1221_realtime_sram import (
     AWP1221RealtimeMixin,
     AWP1221SramEditor,
     AWP1221_ADC_LIVE_REGISTER,
+    awp1221_threshold_adc_counts,
+    awp1221_vibration_band_seconds,
     decode_awp1221_adc_value,
 )
 from pages.gpio_controls import (
     NullGpioControlPanel,
     active_serial_identity,
 )
 
 _GPIO_CATEGORY_BY_LABEL = {label: key for key, label in GPIO_CATEGORY_LABELS.items()}
 _GPIO_FUNCTION_BY_LABEL = {label: code for code, label in GPIO_FUNCTION_LABELS.items()}
 
@@ -976,21 +978,23 @@ class AWP1221ModePage(AWP1221RealtimeMixin, BasePage):
         }
         self._sinc_active_group = AWP1221_SINC_SERIAL_GROUPS[0][0]
         self._sinc_verified_key: tuple[Any, ...] | None = None
         self.sinc_group_hint_var = tk.StringVar(value="")
         self.auto_serial_var = (
             auto_serial_var if auto_serial_var is not None else tk.BooleanVar(value=True)
         )
         self.rom_metric_var = tk.StringVar(value="")
         self.rom_frequency_var = tk.StringVar(value="")
         self.board_slot_var = tk.StringVar(value="0")
+        self._visible_stage_index = 0
         self._build_ui()
+        self._bind_realtime_overlay_traces()
         self._auto_serial_trace = self.auto_serial_var.trace_add(
             "write",
             lambda *_: self._sync_auto_serial_page_state(),
         )
         self._adc_trigger_log_listener = self._handle_adc_trigger_log
         self.ctx.serial.add_log_listener(self._adc_trigger_log_listener)
 
     def _build_ui(self) -> None:
         if self.mode_key == "button_mode":
             self._build_button_mode_ui()
@@ -2910,20 +2914,22 @@ class AWP1221ModePage(AWP1221RealtimeMixin, BasePage):
         self.rom_frequency_var.set("")
 
     def _show_stage(self, stage_index: int) -> None:
         if stage_index not in self._stage_frames:
             return
         for frame in self._stage_frames.values():
             frame.grid_remove()
         self._stage_frames[stage_index].grid()
         for index, button in self._stage_buttons.items():
             button.configure(style="Selected.TButton" if index == stage_index else "Secondary.TButton")
+        self._visible_stage_index = stage_index
+        self._push_realtime_thresholds()
 
     def _register_text(self, widget: tk.Widget, key: str) -> None:
         self._translated_widgets.append((widget, key))
 
     def _add_help_button(self, parent: tk.Misc, row: int, column: int, text_key: str) -> None:
         label = ttk.Label(
             parent,
             text=self.tr("awp1018_help_symbol"),
             style="Muted.TLabel",
             cursor="question_arrow",
@@ -3450,20 +3456,148 @@ class AWP1221ModePage(AWP1221RealtimeMixin, BasePage):
         self._buttons["adc_read_on_trigger"] = trigger_button
 
     def read_adc_value(self) -> None:
         if self._ensure_connected():
             self._request_adc_value_read(show_error=True)
 
     def toggle_adc_trigger_read(self) -> None:
         self._adc_trigger_read_active = not self._adc_trigger_read_active
         self._refresh_adc_read_buttons()
 
+    def toggle_adc_realtime(self) -> None:
+        super().toggle_adc_realtime()
+        if self._adc_realtime_active:
+            self._push_realtime_thresholds()
+
+    def _parse_realtime_code(self, text: object) -> int | None:
+        try:
+            return int(str(text).strip(), 0)
+        except (TypeError, ValueError):
+            return None
+
+    def _realtime_threshold_counts(self) -> tuple[int | None, int | None]:
+        if self.mode_key == "button_mode":
+            upper = lower = None
+            for variables in getattr(self, "_button_stage_vars", ()):
+                code = self._parse_realtime_code(variables["threshold"].get())
+                if code is None:
+                    continue
+                counts = awp1221_threshold_adc_counts(
+                    code,
+                    bool(variables["use_high_12_bits"].get()),
+                )
+                mode = str(variables["event_mode"].get())
+                if mode == "press" and upper is None:
+                    upper = counts
+                elif mode == "release" and lower is None:
+                    lower = counts
+            return upper, lower
+        stages = getattr(self, "_stage_vars", ())
+        if not stages:
+            return None, None
+        index = min(max(getattr(self, "_visible_stage_index", 0), 0), len(stages) - 1)
+        variables = stages[index]
+        precision = bool(variables["use_high_12_bits"].get())
+        press = self._parse_realtime_code(variables["press_threshold"]["threshold"].get())
+        release = self._parse_realtime_code(variables["release_threshold"]["threshold"].get())
+        return (
+            awp1221_threshold_adc_counts(press, precision) if press is not None else None,
+            awp1221_threshold_adc_counts(release, precision) if release is not None else None,
+        )
+
+    def _realtime_press_repeat(self) -> int:
+        if self.mode_key == "button_mode":
+            for variables in getattr(self, "_button_stage_vars", ()):
+                if str(variables["event_mode"].get()) != "press":
+                    continue
+                value = self._parse_realtime_code(variables["repeat"].get())
+                return value if value is not None else 0
+            return 0
+        stages = getattr(self, "_stage_vars", ())
+        if not stages:
+            return 0
+        index = min(max(getattr(self, "_visible_stage_index", 0), 0), len(stages) - 1)
+        value = self._parse_realtime_code(stages[index]["wave"]["press_repeat"].get())
+        return value if value is not None else 0
+
+    def _realtime_waveform_period_s(self) -> float:
+        if self._sinc_selected():
+            try:
+                return float(self._build_current_sinc_plan().duration_us) / 1_000_000.0
+            except Exception:
+                return 0.0
+        try:
+            depth = int(self.rom_depth_var.get().strip(), 0)
+            point_time = int(self.rom_point_time_var.get().strip(), 0)
+            half_cycle_code = self._parse_binary_option(self.rom_half_cycle_var.get())
+            if depth <= 0 or point_time <= 0:
+                return 0.0
+            effective = awp1221_waveform_effective_depth_points(
+                self.rom_waveform_var.get(),
+                depth,
+                half_cycle_code,
+            )
+            return effective * point_time / 1_000_000.0
+        except Exception:
+            return 0.0
+
+    def _realtime_vibration_duration_s(self) -> float:
+        try:
+            debounce_ms = float(str(self.debounce_time_var.get()).strip())
+        except (TypeError, ValueError):
+            debounce_ms = 0.0
+        return awp1221_vibration_band_seconds(
+            self._realtime_waveform_period_s(),
+            self._realtime_press_repeat(),
+            debounce_ms,
+        )
+
+    def _push_realtime_thresholds(self) -> None:
+        window = getattr(self, "_adc_voltage_window", None)
+        if window is None or not window.winfo_exists():
+            return
+        upper, lower = self._realtime_threshold_counts()
+        window.set_threshold_counts(upper, lower)
+
+    def _bind_realtime_overlay_traces(self) -> None:
+        variables: list[tk.Variable] = [self.debounce_time_var]
+        for stage in getattr(self, "_button_stage_vars", ()):
+            variables.extend(
+                (
+                    stage["threshold"],
+                    stage["event_mode"],
+                    stage["use_high_12_bits"],
+                    stage["repeat"],
+                )
+            )
+        for stage in getattr(self, "_stage_vars", ()):
+            variables.extend(
+                (
+                    stage["press_threshold"]["threshold"],
+                    stage["release_threshold"]["threshold"],
+                    stage["use_high_12_bits"],
+                    stage["wave"]["press_repeat"],
+                )
+            )
+        for variable in (
+            self.rom_waveform_var,
+            self.rom_depth_var,
+            self.rom_point_time_var,
+            self.rom_half_cycle_var,
+            *self._sinc_time_variables(),
+            getattr(self, "sinc_depth_var", None),
+        ):
+            if variable is not None:
+                variables.append(variable)
+        for variable in variables:
+            variable.trace_add("write", lambda *_: self._push_realtime_thresholds())
+
     def _request_adc_value_read(
         self,
         *,
         show_error: bool,
         device_addr: int | None = None,
         on_complete: Callable[[], None] | None = None,
         register: int = AWP1221_ADC_VALUE_REGISTER,
     ) -> bool:
         if self._adc_read_in_flight:
             return False
@@ -3555,28 +3689,32 @@ class AWP1221ModePage(AWP1221RealtimeMixin, BasePage):
         detection_active = (
             bool(shared_state.detection_active)
             if shared_state is not None
             else bool(getattr(self, "_adc_detection_active", False))
         )
         detection_device_addr = (
             shared_state.detection_device_addr
             if shared_state is not None
             else getattr(self, "_adc_detection_device_addr", None)
         )
-        if not self._adc_trigger_read_active or not detection_active:
-            return
         upper = line.upper()
         marker = "MCU_TEXT"
         marker_index = upper.find(marker)
         if marker_index < 0:
             return
         payload = upper[marker_index + len(marker):].strip()
+        if payload == "P" and self._adc_realtime_active:
+            window = self._adc_voltage_window
+            if window is not None and window.winfo_exists():
+                window.add_vibration_band(self._realtime_vibration_duration_s())
+        if not self._adc_trigger_read_active or not detection_active:
+            return
         if payload in {"P", "R"}:
             self._request_adc_value_read(
                 show_error=False,
                 device_addr=detection_device_addr,
             )
 
     def on_hide(self) -> None:
         self._adc_lifecycle_generation += 1
         self._stop_adc_realtime()
         self._adc_trigger_read_active = False
diff --git a/pages/awp1221_realtime_sram.py b/pages/awp1221_realtime_sram.py
index d743a10..6a2a742 100644
--- a/pages/awp1221_realtime_sram.py
+++ b/pages/awp1221_realtime_sram.py
@@ -26,20 +26,71 @@ AWP1221_ADC_LOAD_REGISTER = 0x1F
 AWP1221_REALTIME_POLL_MS = 10
 AWP1221_REALTIME_POLL_OPTIONS = ("10", "20", "50", "100", "300")
 
 
 def decode_awp1221_adc_value(raw: int) -> int:
     """Decode register 0x13 as a signed 16-bit ADC sample."""
     word = int(raw) & 0xFFFF
     return word if word < 0x8000 else word - 0x10000
 
 
+THRESHOLD_UPPER_COLOR = "#2563EB"
+THRESHOLD_LOWER_COLOR = "#9333EA"
+VIBRATION_BAND_COLOR = "#86EFAC"
+
+
+def awp1221_threshold_adc_counts(code: int, use_high_12_bits: bool) -> int:
+    value = int(code) & 0xFFF
+    return value << 4 if use_high_12_bits else value
+
+
+def awp1221_adc_counts_to_mv(
+    counts: float,
+    sample_us: float,
+    load_ua: float,
+    capacitance_uf: float,
+) -> float:
+    return float(counts) * float(sample_us) * float(load_ua) / float(capacitance_uf) / 1000.0
+
+
+def awp1221_plot_ceiling(*magnitudes: float) -> float:
+    peak = max((abs(float(value)) for value in magnitudes), default=0.0)
+    return max(1.0, peak * 1.15)
+
+
+def awp1221_vibration_band_seconds(
+    waveform_period_s: float,
+    event_repeat: int,
+    debounce_ms: float,
+) -> float:
+    period = max(0.0, float(waveform_period_s))
+    debounce = max(0.0, float(debounce_ms)) / 1000.0
+    return max(int(event_repeat), 1) * period + debounce
+
+
+def awp1221_visible_band(
+    start: float,
+    duration_s: float,
+    now: float,
+    span: float,
+) -> tuple[float, float] | None:
+    if span <= 0 or duration_s <= 0:
+        return None
+    left = now - span
+    right = now
+    band_left = max(float(start), left)
+    band_right = min(float(start) + float(duration_s), right)
+    if band_right <= band_left:
+        return None
+    return band_left, band_right
+
+
 def _schedule_ui(widget: tk.Misc, callback: Callable[..., None], *args: Any) -> None:
     try:
         widget.after(0, callback, *args)
     except tk.TclError:
         pass
 
 
 def _run_ui_background_task(
     widget: tk.Misc,
     task: Callable[[], Any],
@@ -65,20 +116,23 @@ class ADCVoltageWindow(tk.Toplevel):
         self.minsize(620, 380)
         self.protocol("WM_DELETE_WINDOW", on_close)
         self.sample_time = tk.StringVar(value="4 us")
         self.load = tk.StringVar(value="40 uA")
         self.capacitance = tk.StringVar(value="2")
         self.time_scale = tk.StringVar(value="10")
         self.poll_interval = tk.StringVar(value=str(AWP1221_REALTIME_POLL_MS))
         self.voltage = tk.StringVar(value="鈥?mV")
         self.status = tk.StringVar(value="姝ｅ湪璇诲彇...")
         self.points: deque[tuple[float, float]] = deque(maxlen=2000)
+        self.upper_counts: int | None = None
+        self.lower_counts: int | None = None
+        self.vibration_bands: deque[tuple[float, float]] = deque(maxlen=64)
 
         controls = ttk.Frame(self, padding=12)
         controls.pack(fill="x")
         for column in range(5):
             controls.columnconfigure(column, weight=1)
         for column, (label, variable, options) in enumerate((
             ("ADC_Sample_Tim", self.sample_time, ("4 us", "8 us", "16 us", "32 us")),
             ("ADC_LOAD", self.load, ("40 uA", "160 uA")),
         )):
             ttk.Label(controls, text=label).grid(row=0, column=column, sticky="w")
@@ -107,62 +161,123 @@ class ADCVoltageWindow(tk.Toplevel):
         self.canvas.pack(fill="both", expand=True, padx=12, pady=12)
         self.canvas.bind("<Configure>", lambda _event: self.redraw())
 
     def poll_interval_ms(self) -> int:
         try:
             value = int(str(self.poll_interval.get()).strip())
         except ValueError:
             return AWP1221_REALTIME_POLL_MS
         return value if value > 0 else AWP1221_REALTIME_POLL_MS
 
+    def set_threshold_counts(self, upper: int | None, lower: int | None) -> None:
+        self.upper_counts = upper
+        self.lower_counts = lower
+        self.redraw()
+
+    def add_vibration_band(self, duration_s: float) -> None:
+        width = float(duration_s)
+        if width <= 0:
+            return
+        self.vibration_bands.append((time.monotonic(), width))
+        self.redraw()
+
+    def _conversion_params(self) -> tuple[float, float, float] | None:
+        try:
+            capacitance = float(self.capacitance.get())
+            sample_us = float(self.sample_time.get().split()[0])
+            load_ua = float(self.load.get().split()[0])
+            if capacitance <= 0:
+                raise ValueError
+        except ValueError:
+            return None
+        return sample_us, load_ua, capacitance
+
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
+        params = self._conversion_params()
         try:
-            capacitance = float(self.capacitance.get())
             span = float(self.time_scale.get())
-            if capacitance <= 0 or span <= 0:
+            if span <= 0 or params is None:
                 raise ValueError
-            duration = float(self.sample_time.get().split()[0])
-            current = float(self.load.get().split()[0])
+            duration, current, capacitance = params
         except ValueError:
             self.voltage.set("璇疯緭鍏ユ湁鏁堢殑 C 鍜屾椂闂磋寖鍥?)
             return
-        values = [(stamp, signed * duration * current / capacitance / 1000.0) for stamp, signed in self.points]
+        values = [
+            (stamp, awp1221_adc_counts_to_mv(signed, duration, current, capacitance))
+            for stamp, signed in self.points
+        ]
         self.voltage.set(f"{values[-1][1]:.3f} mV" if values else "鈥?mV")
         now = time.monotonic()
         visible = [(stamp, value) for stamp, value in values if stamp >= now - span]
-        magnitude = max((abs(value) for _, value in visible), default=0.0)
-        ceiling = max(1.0, magnitude * 1.15)
+        upper_mv = (
+            awp1221_adc_counts_to_mv(self.upper_counts, duration, current, capacitance)
+            if self.upper_counts is not None
+            else None
+        )
+        lower_mv = (
+            awp1221_adc_counts_to_mv(self.lower_counts, duration, current, capacitance)
+            if self.lower_counts is not None
+            else None
+        )
+        ceiling = awp1221_plot_ceiling(
+            *(value for _, value in visible),
+            *(item for item in (upper_mv, lower_mv) if item is not None),
+        )
+        def y_at(value: float) -> float:
+            return (top + bottom) / 2 - value / ceiling * (bottom - top) / 2
+        def x_at(stamp: float) -> float:
+            return left + (stamp - now + span) / span * (right - left)
+        for start, band_duration in self.vibration_bands:
+            visible_band = awp1221_visible_band(start, band_duration, now, span)
+            if visible_band is None:
+                continue
+            canvas.create_rectangle(
+                x_at(visible_band[0]),
+                top,
+                x_at(visible_band[1]),
+                bottom,
+                fill=VIBRATION_BAND_COLOR,
+                outline="",
+            )
         for index in range(6):
             y = bottom - (bottom - top) * index / 5
             canvas.create_line(left, y, right, y, fill="#e4e8ed")
             canvas.create_text(left - 8, y, text=f"{ceiling * (2 * index / 5 - 1):.1f}", anchor="e", fill="#596575")
             x = left + (right - left) * index / 5
             canvas.create_line(x, top, x, bottom, fill="#e4e8ed")
             canvas.create_text(x, bottom + 15, text=f"{-span + span * index / 5:.0f}", fill="#596575")
         canvas.create_text(12, top, text="mV", anchor="w")
         canvas.create_text(right, bottom + 32, text="Time (s)", anchor="e")
+        for mv, color, label in (
+            (upper_mv, THRESHOLD_UPPER_COLOR, "涓婇槇鍊?),
+            (lower_mv, THRESHOLD_LOWER_COLOR, "涓嬮槇鍊?),
+        ):
+            if mv is None:
+                continue
+            y = y_at(mv)
+            canvas.create_line(left, y, right, y, fill=color, dash=(6, 4), width=2)
+            canvas.create_text(right - 4, y - 8, text=f"{label} {mv:.3f} mV", anchor="e", fill=color)
         coords: list[float] = []
         for stamp, value in visible:
-            coords.extend((left + (stamp - now + span) / span * (right - left),
-                           (top + bottom) / 2 - value / ceiling * (bottom - top) / 2))
+            coords.extend((x_at(stamp), y_at(value)))
         if len(coords) >= 4:
             canvas.create_line(*coords, fill="#2f80ed", width=2)
 
 
 class AWP1221RealtimeMixin:
     """Reusable realtime-display lifecycle for an AWP1221 Tk page.
 
     The host must provide ``ctx``, ``device_addr()``, ``_ensure_connected()``,
     ``_read_register_value()``, ``_ensure_reg_1f_bit3()``, ``_request_adc_value_read()`` and
     ``_refresh_adc_read_buttons()``.
@@ -252,12 +367,20 @@ class AWP1221RealtimeMixin:
         self._adc_realtime_job = None
         self._refresh_adc_read_buttons()
 
 
 __all__ = [
     "ADCVoltageWindow",
     "AWP1221RealtimeMixin",
     "AWP1221SramEditor",
     "AWP1221_ADC_LIVE_REGISTER",
     "AWP1221_REALTIME_POLL_OPTIONS",
+    "THRESHOLD_LOWER_COLOR",
+    "THRESHOLD_UPPER_COLOR",
+    "VIBRATION_BAND_COLOR",
+    "awp1221_adc_counts_to_mv",
+    "awp1221_plot_ceiling",
+    "awp1221_threshold_adc_counts",
+    "awp1221_vibration_band_seconds",
+    "awp1221_visible_band",
     "decode_awp1221_adc_value",
 ]
diff --git a/tests/test_awp1221_realtime_overlay.py b/tests/test_awp1221_realtime_overlay.py
new file mode 100644
index 0000000..ca09454
--- /dev/null
+++ b/tests/test_awp1221_realtime_overlay.py
@@ -0,0 +1,191 @@
+from __future__ import annotations
+
+import time
+import tkinter as tk
+import unittest
+from collections import deque
+from types import SimpleNamespace
+
+from pages.awp1221_page import AWP1221ModePage
+from pages.awp1221_realtime_sram import (
+    ADCVoltageWindow,
+    VIBRATION_BAND_COLOR,
+    awp1221_adc_counts_to_mv,
+    awp1221_plot_ceiling,
+    awp1221_threshold_adc_counts,
+    awp1221_vibration_band_seconds,
+    awp1221_visible_band,
+)
+
+
+class Awp1221RealtimeHelperTests(unittest.TestCase):
+    def test_precision_off_keeps_12_bit_code(self) -> None:
+        self.assertEqual(awp1221_threshold_adc_counts(400, False), 400)
+
+    def test_precision_on_shifts_four_bits(self) -> None:
+        self.assertEqual(awp1221_threshold_adc_counts(400, True), 6400)
+
+    def test_invalid_code_is_masked_to_12_bits(self) -> None:
+        self.assertEqual(awp1221_threshold_adc_counts(0x1400, False), 0x400)
+
+    def test_mv_conversion_matches_live_curve_formula(self) -> None:
+        self.assertAlmostEqual(awp1221_adc_counts_to_mv(400, 4.0, 40.0, 2.0), 32.0)
+
+    def test_ceiling_includes_thresholds_when_signal_is_small(self) -> None:
+        self.assertAlmostEqual(awp1221_plot_ceiling(0.0, 32.0, 8.0), 32.0 * 1.15)
+
+    def test_ceiling_floor_is_one(self) -> None:
+        self.assertEqual(awp1221_plot_ceiling(), 1.0)
+
+    def test_band_width_uses_repeat_and_debounce(self) -> None:
+        self.assertAlmostEqual(awp1221_vibration_band_seconds(0.010, 2, 100), 0.120)
+
+    def test_repeat_zero_plays_once(self) -> None:
+        self.assertAlmostEqual(awp1221_vibration_band_seconds(0.010, 0, 0), 0.010)
+
+    def test_negative_period_and_debounce_clamp_to_zero(self) -> None:
+        self.assertEqual(awp1221_vibration_band_seconds(-1.0, 1, -50), 0.0)
+
+    def test_visible_band_clips_to_window(self) -> None:
+        self.assertEqual(awp1221_visible_band(8.0, 4.0, 10.0, 5.0), (8.0, 10.0))
+
+    def test_visible_band_none_when_outside_span(self) -> None:
+        self.assertIsNone(awp1221_visible_band(0.0, 1.0, 10.0, 5.0))
+
+
+class Awp1221RealtimeWindowTests(unittest.TestCase):
+    def setUp(self) -> None:
+        self.root = tk.Tk()
+        self.root.withdraw()
+        self.window = ADCVoltageWindow(self.root, lambda: None)
+        self.window.sample_time.set("4 us")
+        self.window.load.set("40 uA")
+        self.window.capacitance.set("2")
+        self.window.time_scale.set("10")
+
+    def tearDown(self) -> None:
+        try:
+            self.window.destroy()
+        except tk.TclError:
+            pass
+        self.root.destroy()
+
+    def test_set_threshold_counts_stores_values(self) -> None:
+        self.window.set_threshold_counts(6400, 1600)
+        self.assertEqual(self.window.upper_counts, 6400)
+        self.assertEqual(self.window.lower_counts, 1600)
+
+    def test_add_vibration_band_ignores_non_positive_duration(self) -> None:
+        self.window.add_vibration_band(0)
+        self.window.add_vibration_band(-1)
+        self.assertEqual(len(self.window.vibration_bands), 0)
+
+    def test_add_vibration_band_records_start_and_duration(self) -> None:
+        before = time.monotonic()
+        self.window.add_vibration_band(0.12)
+        after = time.monotonic()
+        self.assertEqual(len(self.window.vibration_bands), 1)
+        start, duration = self.window.vibration_bands[0]
+        self.assertTrue(before <= start <= after)
+        self.assertAlmostEqual(duration, 0.12)
+
+    def test_redraw_creates_threshold_and_band_items(self) -> None:
+        self.window.update_idletasks()
+        self.window.canvas.config(width=400, height=300)
+        self.window.update_idletasks()
+        self.window.set_threshold_counts(6400, 1600)
+        self.window.add_vibration_band(1.0)
+        self.window.vibration_bands = deque([(time.monotonic() - 0.5, 1.0)], maxlen=64)
+        self.window.redraw()
+        fills = [self.window.canvas.itemcget(item, "fill") for item in self.window.canvas.find_all()]
+        self.assertIn("#2563EB", fills)
+        self.assertIn("#9333EA", fills)
+        self.assertIn(VIBRATION_BAND_COLOR, fills)
+
+
+def _var(value: object) -> SimpleNamespace:
+    return SimpleNamespace(get=lambda: value)
+
+
+class Awp1221RealtimePageLogicTests(unittest.TestCase):
+    def test_button_mode_uses_first_press_and_release_stages(self) -> None:
+        page = object.__new__(AWP1221ModePage)
+        page.mode_key = "button_mode"
+        page._button_stage_vars = [
+            {
+                "event_mode": _var("press"),
+                "threshold": _var("400"),
+                "use_high_12_bits": _var(True),
+                "repeat": _var("2"),
+            },
+            {
+                "event_mode": _var("release"),
+                "threshold": _var("100"),
+                "use_high_12_bits": _var(True),
+                "repeat": _var("0"),
+            },
+        ]
+        self.assertEqual(page._realtime_threshold_counts(), (6400, 1600))
+        self.assertEqual(page._realtime_press_repeat(), 2)
+
+    def test_full_mode_uses_visible_stage_press_release(self) -> None:
+        page = object.__new__(AWP1221ModePage)
+        page.mode_key = "full_mode"
+        page._visible_stage_index = 1
+        page._stage_vars = [
+            {},
+            {
+                "press_threshold": {"threshold": _var("300")},
+                "release_threshold": {"threshold": _var("80")},
+                "use_high_12_bits": _var(False),
+                "wave": {"press_repeat": _var("3")},
+            },
+        ]
+        self.assertEqual(page._realtime_threshold_counts(), (300, 80))
+        self.assertEqual(page._realtime_press_repeat(), 3)
+
+    def test_invalid_threshold_omits_that_line(self) -> None:
+        page = object.__new__(AWP1221ModePage)
+        page.mode_key = "button_mode"
+        page._button_stage_vars = [
+            {
+                "event_mode": _var("press"),
+                "threshold": _var("abc"),
+                "use_high_12_bits": _var(False),
+                "repeat": _var("0"),
+            },
+            {
+                "event_mode": _var("release"),
+                "threshold": _var("100"),
+                "use_high_12_bits": _var(False),
+                "repeat": _var("0"),
+            },
+        ]
+        self.assertEqual(page._realtime_threshold_counts(), (None, 100))
+
+    def test_vibration_duration_combines_period_repeat_debounce(self) -> None:
+        page = object.__new__(AWP1221ModePage)
+        page._realtime_waveform_period_s = lambda: 0.01
+        page._realtime_press_repeat = lambda: 2
+        page.debounce_time_var = _var("100")
+        self.assertAlmostEqual(page._realtime_vibration_duration_s(), 0.12)
+
+    def test_process_p_starts_band_without_trigger_read(self) -> None:
+        bands: list[float] = []
+        page = object.__new__(AWP1221ModePage)
+        page._adc_realtime_active = True
+        page._adc_trigger_read_active = False
+        page._adc_detection_active = True
+        page._shared_state_for = lambda: None
+        page._adc_voltage_window = SimpleNamespace(
+            winfo_exists=lambda: True,
+            add_vibration_band=lambda duration: bands.append(duration),
+        )
+        page._realtime_vibration_duration_s = lambda: 0.12
+        page._request_adc_value_read = lambda **kwargs: None
+        AWP1221ModePage._process_adc_trigger_log(page, "MCU_TEXT P")
+        self.assertEqual(bands, [0.12])
+
+
+if __name__ == "__main__":
+    unittest.main()
