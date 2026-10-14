BASE: 4a5cd5a
HEAD: 26dc258

## Commits

26dc258 Draw AWP1221 realtime threshold lines and vibration bands.

## Stat

 pages/awp1221_realtime_sram.py         | 84 ++++++++++++++++++++++++++++++----
 tests/test_awp1221_realtime_overlay.py | 53 +++++++++++++++++++++
 2 files changed, 127 insertions(+), 10 deletions(-)

## Diff
```diff

diff --git a/pages/awp1221_realtime_sram.py b/pages/awp1221_realtime_sram.py
index 5af84e6..b168e2e 100644
--- a/pages/awp1221_realtime_sram.py
+++ b/pages/awp1221_realtime_sram.py
@@ -72,21 +72,21 @@ def awp1221_visible_band(
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
-    if band_right <= band_left:
+    if band_right < band_left:
         return None
     return band_left, band_right
 
 
 def _schedule_ui(widget: tk.Misc, callback: Callable[..., None], *args: Any) -> None:
     try:
         widget.after(0, callback, *args)
     except tk.TclError:
         pass
 
@@ -116,20 +116,23 @@ class ADCVoltageWindow(tk.Toplevel):
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
@@ -158,62 +161,123 @@ class ADCVoltageWindow(tk.Toplevel):
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
diff --git a/tests/test_awp1221_realtime_overlay.py b/tests/test_awp1221_realtime_overlay.py
index 317b8c2..e7d6473 100644
--- a/tests/test_awp1221_realtime_overlay.py
+++ b/tests/test_awp1221_realtime_overlay.py
@@ -1,15 +1,19 @@
 from __future__ import annotations
 
+import time
+import tkinter as tk
 import unittest
 
 from pages.awp1221_realtime_sram import (
+    ADCVoltageWindow,
+    VIBRATION_BAND_COLOR,
     awp1221_adc_counts_to_mv,
     awp1221_plot_ceiling,
     awp1221_threshold_adc_counts,
     awp1221_vibration_band_seconds,
     awp1221_visible_band,
 )
 
 
 class Awp1221RealtimeHelperTests(unittest.TestCase):
     def test_precision_off_keeps_12_bit_code(self) -> None:
@@ -39,12 +43,61 @@ class Awp1221RealtimeHelperTests(unittest.TestCase):
     def test_negative_period_and_debounce_clamp_to_zero(self) -> None:
         self.assertEqual(awp1221_vibration_band_seconds(-1.0, 1, -50), 0.0)
 
     def test_visible_band_clips_to_window(self) -> None:
         self.assertEqual(awp1221_visible_band(8.0, 4.0, 10.0, 5.0), (8.0, 10.0))
 
     def test_visible_band_none_when_outside_span(self) -> None:
         self.assertIsNone(awp1221_visible_band(0.0, 1.0, 10.0, 5.0))
 
 
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
+        self.window.redraw()
+        fills = [self.window.canvas.itemcget(item, "fill") for item in self.window.canvas.find_all()]
+        self.assertIn("#2563EB", fills)
+        self.assertIn("#9333EA", fills)
+        self.assertIn(VIBRATION_BAND_COLOR, fills)
+
+
 if __name__ == "__main__":
     unittest.main()
