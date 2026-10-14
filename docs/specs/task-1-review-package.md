BASE: 1a3e71b
HEAD: 4a5cd5a

## Commits

4a5cd5a Add AWP1221 realtime overlay conversion helpers.

## Stat

 pages/awp1221_realtime_sram.py         | 59 ++++++++++++++++++++++++++++++++++
 tests/test_awp1221_realtime_overlay.py | 50 ++++++++++++++++++++++++++++
 2 files changed, 109 insertions(+)

## Diff
```diff

diff --git a/pages/awp1221_realtime_sram.py b/pages/awp1221_realtime_sram.py
index d743a10..5af84e6 100644
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
@@ -252,12 +303,20 @@ class AWP1221RealtimeMixin:
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
index 0000000..317b8c2
--- /dev/null
+++ b/tests/test_awp1221_realtime_overlay.py
@@ -0,0 +1,50 @@
+from __future__ import annotations
+
+import unittest
+
+from pages.awp1221_realtime_sram import (
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
+if __name__ == "__main__":
+    unittest.main()
```n
