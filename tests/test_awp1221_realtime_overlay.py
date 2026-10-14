from __future__ import annotations

import time
import tkinter as tk
import unittest
from collections import deque
from types import SimpleNamespace

from pages.awp1221_page import AWP1221ModePage
from pages.awp1221_realtime_sram import (
    ADCVoltageWindow,
    AWP1221RealtimeMixin,
    VIBRATION_BAND_COLOR,
    awp1221_adc_counts_to_mv,
    awp1221_plot_ceiling,
    awp1221_threshold_adc_counts,
    awp1221_vibration_band_seconds,
    awp1221_vibration_falling_edge,
    awp1221_visible_band,
    awp1221_zero_time_seconds,
)


class Awp1221RealtimeHelperTests(unittest.TestCase):
    def test_precision_off_keeps_12_bit_code(self) -> None:
        self.assertEqual(awp1221_threshold_adc_counts(400, False), 400)

    def test_precision_on_shifts_four_bits(self) -> None:
        self.assertEqual(awp1221_threshold_adc_counts(400, True), 6400)

    def test_invalid_code_is_masked_to_12_bits(self) -> None:
        self.assertEqual(awp1221_threshold_adc_counts(0x1400, False), 0x400)

    def test_negative_code_sign_extends(self) -> None:
        self.assertEqual(awp1221_threshold_adc_counts(0xFFF, False), -1)

    def test_negative_code_precision_shifts_signed(self) -> None:
        self.assertEqual(awp1221_threshold_adc_counts(0xFFF, True), -16)

    def test_mv_conversion_matches_live_curve_formula(self) -> None:
        self.assertAlmostEqual(awp1221_adc_counts_to_mv(400, 4.0, 40.0, 2.0), 32.0)

    def test_ceiling_includes_thresholds_when_signal_is_small(self) -> None:
        self.assertAlmostEqual(awp1221_plot_ceiling(0.0, 32.0, 8.0), 32.0 * 1.15)

    def test_ceiling_floor_is_one(self) -> None:
        self.assertEqual(awp1221_plot_ceiling(), 1.0)

    def test_band_width_uses_repeat_and_debounce(self) -> None:
        self.assertAlmostEqual(awp1221_vibration_band_seconds(0.010, 2, 100), 0.120)

    def test_repeat_zero_plays_once(self) -> None:
        self.assertAlmostEqual(awp1221_vibration_band_seconds(0.010, 0, 0), 0.010)

    def test_negative_period_and_debounce_clamp_to_zero(self) -> None:
        self.assertEqual(awp1221_vibration_band_seconds(-1.0, 1, -50), 0.0)

    def test_debounce_skipped_when_disabled(self) -> None:
        self.assertAlmostEqual(
            awp1221_vibration_band_seconds(0.010, 1, 100, debounce_enabled=False, zero_time_s=0.032),
            0.042,
        )

    def test_zero_time_label_to_seconds(self) -> None:
        self.assertAlmostEqual(awp1221_zero_time_seconds("32 ms"), 0.032)
        self.assertAlmostEqual(awp1221_zero_time_seconds("1 ms"), 0.001)

    def test_visible_band_clips_to_window(self) -> None:
        self.assertEqual(awp1221_visible_band(8.0, 4.0, 10.0, 5.0), (8.0, 10.0))

    def test_visible_band_none_when_outside_span(self) -> None:
        self.assertIsNone(awp1221_visible_band(0.0, 1.0, 10.0, 5.0))

    def test_visible_band_just_started_paints_into_the_past(self) -> None:
        left, right = awp1221_visible_band(10.0, 0.032, 10.0, 10.0, min_width_s=0.1)
        self.assertAlmostEqual(left, 9.9)
        self.assertAlmostEqual(right, 10.0)

    def test_vibration_falling_edge_only_high_to_low(self) -> None:
        self.assertTrue(awp1221_vibration_falling_edge(1, 0))
        self.assertTrue(awp1221_vibration_falling_edge(None, 0))
        self.assertFalse(awp1221_vibration_falling_edge(0, 0))
        self.assertFalse(awp1221_vibration_falling_edge(1, 1))
        self.assertFalse(awp1221_vibration_falling_edge(0, 1))


class Awp1221RealtimeWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = ADCVoltageWindow(self.root, lambda: None)
        self.window.sample_time.set("4 us")
        self.window.load.set("40 uA")
        self.window.capacitance.set("2")
        self.window.time_scale.set("10")

    def tearDown(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        self.root.destroy()

    def test_set_threshold_counts_stores_values(self) -> None:
        self.window.set_threshold_counts(6400, 1600)
        self.assertEqual(self.window.upper_counts, 6400)
        self.assertEqual(self.window.lower_counts, 1600)

    def test_add_vibration_band_ignores_non_positive_duration(self) -> None:
        self.window.add_vibration_band(0)
        self.window.add_vibration_band(-1)
        self.assertEqual(len(self.window.vibration_bands), 0)

    def test_add_vibration_band_records_start_and_duration(self) -> None:
        before = time.monotonic()
        self.window.add_vibration_band(0.12)
        after = time.monotonic()
        self.assertEqual(len(self.window.vibration_bands), 1)
        start, duration = self.window.vibration_bands[0]
        self.assertTrue(before <= start <= after)
        self.assertAlmostEqual(duration, 0.12)

    def test_redraw_creates_threshold_items_but_hides_band_items(self) -> None:
        self.window.update_idletasks()
        self.window.canvas.config(width=400, height=300)
        self.window.update_idletasks()
        self.window.set_threshold_counts(6400, 1600)
        self.window.add_vibration_band(1.0)
        self.window.vibration_bands = deque([(time.monotonic() - 0.5, 1.0)], maxlen=64)
        self.window.redraw()
        fills = [self.window.canvas.itemcget(item, "fill") for item in self.window.canvas.find_all()]
        self.assertIn("#2563EB", fills)
        self.assertIn("#9333EA", fills)
        self.assertNotIn(VIBRATION_BAND_COLOR, fills)
        self.assertEqual(len(self.window.vibration_bands), 1)


def _var(value: object) -> SimpleNamespace:
    return SimpleNamespace(get=lambda: value)


class Awp1221RealtimePageLogicTests(unittest.TestCase):
    def test_button_mode_uses_first_press_and_release_stages(self) -> None:
        page = object.__new__(AWP1221ModePage)
        page.mode_key = "button_mode"
        page._button_stage_vars = [
            {
                "event_mode": _var("press"),
                "threshold": _var("400"),
                "use_high_12_bits": _var(True),
                "repeat": _var("2"),
            },
            {
                "event_mode": _var("release"),
                "threshold": _var("100"),
                "use_high_12_bits": _var(True),
                "repeat": _var("0"),
            },
        ]
        self.assertEqual(page._realtime_threshold_counts(), (6400, 1600))
        self.assertEqual(page._realtime_press_repeat(), 2)

    def test_full_mode_uses_visible_stage_press_release(self) -> None:
        page = object.__new__(AWP1221ModePage)
        page.mode_key = "full_mode"
        page._visible_stage_index = 1
        page._stage_vars = [
            {},
            {
                "press_threshold": {"threshold": _var("300")},
                "release_threshold": {"threshold": _var("80")},
                "use_high_12_bits": _var(False),
                "wave": {"press_repeat": _var("3")},
            },
        ]
        self.assertEqual(page._realtime_threshold_counts(), (300, 80))
        self.assertEqual(page._realtime_press_repeat(), 3)

    def test_invalid_threshold_omits_that_line(self) -> None:
        page = object.__new__(AWP1221ModePage)
        page.mode_key = "button_mode"
        page._button_stage_vars = [
            {
                "event_mode": _var("press"),
                "threshold": _var("abc"),
                "use_high_12_bits": _var(False),
                "repeat": _var("0"),
            },
            {
                "event_mode": _var("release"),
                "threshold": _var("100"),
                "use_high_12_bits": _var(False),
                "repeat": _var("0"),
            },
        ]
        self.assertEqual(page._realtime_threshold_counts(), (None, 100))

    def test_out_of_range_threshold_omits_that_line(self) -> None:
        page = object.__new__(AWP1221ModePage)
        page.mode_key = "button_mode"
        page._button_stage_vars = [
            {
                "event_mode": _var("press"),
                "threshold": _var("4095"),
                "use_high_12_bits": _var(False),
                "repeat": _var("0"),
            },
            {
                "event_mode": _var("release"),
                "threshold": _var("-1"),
                "use_high_12_bits": _var(False),
                "repeat": _var("0"),
            },
        ]
        self.assertEqual(page._realtime_threshold_counts(), (None, -1))

    def test_vibration_duration_combines_period_repeat_debounce(self) -> None:
        page = object.__new__(AWP1221ModePage)
        page.mode_key = "button_mode"
        page._button_stage_vars = [
            {"event_mode": _var("press"), "debounce": _var(True)},
        ]
        page._realtime_waveform_period_s = lambda: 0.01
        page._realtime_press_repeat = lambda: 2
        page.debounce_time_var = _var("100")
        page.zero_time_var = _var("32 ms")
        self.assertAlmostEqual(page._realtime_vibration_duration_s(), 0.152)

    def test_vibration_duration_skips_debounce_when_unchecked(self) -> None:
        page = object.__new__(AWP1221ModePage)
        page.mode_key = "button_mode"
        page._button_stage_vars = [
            {"event_mode": _var("press"), "debounce": _var(False)},
        ]
        page._realtime_waveform_period_s = lambda: 0.01
        page._realtime_press_repeat = lambda: 1
        page.debounce_time_var = _var("100")
        page.zero_time_var = _var("32 ms")
        self.assertAlmostEqual(page._realtime_vibration_duration_s(), 0.042)

    def test_process_p_does_not_start_band_without_d2_low(self) -> None:
        bands: list[float] = []
        page = object.__new__(AWP1221ModePage)
        page._adc_realtime_active = True
        page._adc_trigger_read_active = False
        page._adc_detection_active = True
        page._shared_state_for = lambda: None
        page._adc_voltage_window = SimpleNamespace(
            winfo_exists=lambda: True,
            add_vibration_band=lambda duration: bands.append(duration),
        )
        page._realtime_vibration_duration_s = lambda: 0.12
        page._request_adc_value_read = lambda **kwargs: None
        AWP1221ModePage._process_adc_trigger_log(page, "MCU_TEXT P")
        self.assertEqual(bands, [])

    def test_d2_falling_edge_starts_one_band(self) -> None:
        bands: list[float] = []
        page = object.__new__(AWP1221RealtimeMixin)
        page._vibration_gpio_level = 1
        page._adc_realtime_active = True
        page._adc_voltage_window = SimpleNamespace(
            winfo_exists=lambda: True,
            add_vibration_band=lambda duration: bands.append(duration),
        )
        page._realtime_vibration_duration_s = lambda: 0.12
        page._note_vibration_gpio_level(0)
        page._note_vibration_gpio_level(0)
        page._note_vibration_gpio_level(1)
        page._note_vibration_gpio_level(0)
        self.assertEqual(bands, [0.12, 0.12])


if __name__ == "__main__":
    unittest.main()
