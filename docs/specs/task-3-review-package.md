BASE: 49a20c0
HEAD: cd12818

## Commits

cd12818 Overlay AWP1221 realtime thresholds and press vibration bands.

## Stat

 pages/awp1221_page.py                  | 142 ++++++++++++++++++++++++++++++++-
 tests/test_awp1221_realtime_overlay.py |  86 ++++++++++++++++++++
 2 files changed, 226 insertions(+), 2 deletions(-)

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
diff --git a/tests/test_awp1221_realtime_overlay.py b/tests/test_awp1221_realtime_overlay.py
index 0f3534b..ca09454 100644
--- a/tests/test_awp1221_realtime_overlay.py
+++ b/tests/test_awp1221_realtime_overlay.py
@@ -1,17 +1,19 @@
 from __future__ import annotations
 
 import time
 import tkinter as tk
 import unittest
 from collections import deque
+from types import SimpleNamespace
 
+from pages.awp1221_page import AWP1221ModePage
 from pages.awp1221_realtime_sram import (
     ADCVoltageWindow,
     VIBRATION_BAND_COLOR,
     awp1221_adc_counts_to_mv,
     awp1221_plot_ceiling,
     awp1221_threshold_adc_counts,
     awp1221_vibration_band_seconds,
     awp1221_visible_band,
 )
 
@@ -94,12 +96,96 @@ class Awp1221RealtimeWindowTests(unittest.TestCase):
         self.window.set_threshold_counts(6400, 1600)
         self.window.add_vibration_band(1.0)
         self.window.vibration_bands = deque([(time.monotonic() - 0.5, 1.0)], maxlen=64)
         self.window.redraw()
         fills = [self.window.canvas.itemcget(item, "fill") for item in self.window.canvas.find_all()]
         self.assertIn("#2563EB", fills)
         self.assertIn("#9333EA", fills)
         self.assertIn(VIBRATION_BAND_COLOR, fills)
 
 
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
 if __name__ == "__main__":
     unittest.main()
