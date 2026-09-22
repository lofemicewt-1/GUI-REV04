from __future__ import annotations

import unittest

from pages.awp1221_realtime_sram import (
    awp1221_adc_counts_to_mv,
    awp1221_plot_ceiling,
    awp1221_threshold_adc_counts,
    awp1221_vibration_band_seconds,
    awp1221_visible_band,
)


class Awp1221RealtimeHelperTests(unittest.TestCase):
    def test_precision_off_keeps_12_bit_code(self) -> None:
        self.assertEqual(awp1221_threshold_adc_counts(400, False), 400)

    def test_precision_on_shifts_four_bits(self) -> None:
        self.assertEqual(awp1221_threshold_adc_counts(400, True), 6400)

    def test_invalid_code_is_masked_to_12_bits(self) -> None:
        self.assertEqual(awp1221_threshold_adc_counts(0x1400, False), 0x400)

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

    def test_visible_band_clips_to_window(self) -> None:
        self.assertEqual(awp1221_visible_band(8.0, 4.0, 10.0, 5.0), (8.0, 10.0))

    def test_visible_band_none_when_outside_span(self) -> None:
        self.assertIsNone(awp1221_visible_band(0.0, 1.0, 10.0, 5.0))


if __name__ == "__main__":
    unittest.main()
