"""Signed Gauss samples, explicit bias, half-cycle slicing and preview axes."""
from types import SimpleNamespace
import unittest

from core.base_tool import map_voltage_to_dac
from core.waveform_engine import WaveformEngine
from pages.awp1221_page import (
    AWP1221ModePage,
    awp1221_waveform_effective_depth_points,
    awp1221_waveform_offset_voltage,
    awp1221_waveform_peak_to_peak_voltage,
    awp1221_waveform_preview_value,
    awp1221_waveform_preview_voltage,
    encode_awp1221_waveform_descriptor,
)


class GaussDacTests(unittest.TestCase):
    def setUp(self):
        self.engine = WaveformEngine(voltage_range=100)

    def test_signed_peaks_and_zero_tails_use_the_sine_dac_mapping(self):
        for points in (127, 128, 256, 1020):
            for peak in (-25, 25):
                for positive_zero in (False, True):
                    with self.subTest(points=points, peak=peak, positive_zero=positive_zero):
                        result = self.engine.generate("gauss", points, gauss_peak=peak,
                                                      gauss_start_positive_zero=positive_zero)
                        self.assertEqual((result.values[0], result.values[-1]), (0x800, 0x800))
                        self.assertEqual(result.voltages[points // 2], peak)
                        sine_peak = self.engine.generate("sine", 1, start_phase_deg=90 if peak > 0 else 270,
                                                         amplitude=abs(peak)).values[0]
                        self.assertEqual(result.values[points // 2], sine_peak)

    def test_amplitude_is_used_when_no_gauss_peak_override_is_given(self):
        for amplitude in (0, 25, -25, 100, 150):
            with self.subTest(amplitude=amplitude):
                result = self.engine.generate("gauss", 161, amplitude=amplitude)
                self.assertEqual(result.voltages[80], min(abs(amplitude), 100))
        self.assertEqual(self.engine.generate("gauss", 161).voltages[80], 100)

    def test_explicit_offset_shifts_every_sample_before_dac_mapping(self):
        for peak in (-25, 25):
            original = self.engine.generate("gauss", 161, gauss_peak=peak)
            for bias in (-20, 20):
                shifted = self.engine.generate("gauss", 161, gauss_peak=peak, offset=bias)
                for before, after, code in zip(original.voltages, shifted.voltages, shifted.values):
                    self.assertAlmostEqual(after, before + bias)
                    self.assertEqual(code, self.engine.map_voltage_to_dac(before + bias))
                self.assertEqual(shifted.values[0], self.engine.map_voltage_to_dac(bias))

    def test_offset_then_clamp_at_both_dac_rails(self):
        for peak, bias, rail in ((80, 40, 0xFFF), (-80, -40, 0x000)):
            result = self.engine.generate("gauss", 161, gauss_peak=peak, offset=bias)
            self.assertEqual(result.values[80], rail)
            self.assertEqual(result.values[0], self.engine.map_voltage_to_dac(bias))
            self.assertTrue(all(0 <= value <= 0xFFF for value in result.values))


class GaussRomPreviewTests(unittest.TestCase):
    def voltage(self, position, half=0, phase=0, offset=0, coarse=8, fine=2):
        return awp1221_waveform_preview_voltage("Gauss", position, half, phase, coarse, fine, offset)

    def test_full_and_half_cycles_have_exact_mid_code_tails_and_signed_peaks(self):
        amplitude = awp1221_waveform_peak_to_peak_voltage(8, 2) / 2
        for half in range(4):
            tails = (0, 1) if half in (0, 3) else ((0,) if half == 1 else (1,))
            peak_at = 0.5 if half in (0, 3) else (1 if half == 1 else 0)
            for phase, sign in ((0, 1), (1, -1)):
                with self.subTest(half=half, phase=phase):
                    for tail in tails:
                        self.assertEqual(self.voltage(tail, half, phase), 0)
                        self.assertEqual(map_voltage_to_dac(self.voltage(tail, half, phase), 100), 0x800)
                    self.assertEqual(self.voltage(peak_at, half, phase), sign * amplitude)
                    self.assertEqual(map_voltage_to_dac(self.voltage(peak_at, half, phase), 100),
                                     map_voltage_to_dac(sign * amplitude, 100))

    def test_half_cycles_are_the_two_halves_of_the_same_full_gauss(self):
        for phase in (0, 1):
            for index in range(81):
                position = index / 80
                self.assertAlmostEqual(self.voltage(position, 1, phase), self.voltage(position / 2, 0, phase))
                self.assertAlmostEqual(self.voltage(position, 2, phase), self.voltage((1 + position) / 2, 0, phase))
                self.assertEqual(self.voltage(position, 3, phase), self.voltage(position, 0, phase))

    def test_offset_shifts_all_samples_for_both_polarities_and_all_half_codes(self):
        for half in range(4):
            for phase in (0, 1):
                for offset in (0x10, 0x70):
                    bias = awp1221_waveform_offset_voltage(offset)
                    for index in range(161):
                        position = index / 160
                        self.assertAlmostEqual(self.voltage(position, half, phase, offset),
                                               self.voltage(position, half, phase) + bias)

    def test_preview_clamps_after_applying_bias(self):
        for half in range(4):
            peak_at = 0.5 if half in (0, 3) else (1 if half == 1 else 0)
            for phase, offset, expected in ((0, 0x40, 100), (1, 0x3F, -100)):
                with self.subTest(half=half, phase=phase):
                    self.assertEqual(self.voltage(peak_at, half, phase, offset, 31, 3), expected)

    def test_half_cycle_depths_and_wire_words_keep_the_existing_contract(self):
        for depth in (256, 512):
            for half in range(4):
                self.assertEqual(awp1221_waveform_effective_depth_points("Gauss", depth, half),
                                 128 if half in (1, 2) else 256)
                for phase in (0, 1):
                    actual = encode_awp1221_waveform_descriptor(
                        source_code=2, micro_bias_code=2, waveform_code=1,
                        depth_code=0 if depth == 512 else 1, half_cycle_code=half,
                        phase_code=phase, point_time=7, repeat_count=9, infinite_loop=False)
                    expected_first = 0x888 | (0x20 if depth == 256 else 0) | (half << 1) | phase
                    self.assertEqual(actual, (expected_first, 6, 8))

    def test_sine_preview_and_amplitude_conventions_are_unchanged(self):
        amplitude = awp1221_waveform_peak_to_peak_voltage(8, 2) / 2
        self.assertEqual(awp1221_waveform_preview_value("SINE", 0, 0, 0), -1)
        self.assertEqual(awp1221_waveform_preview_voltage("SINE", 0, 0, 0, 8, 2, 0x70),
                         12.5 - amplitude)

    def test_gui_draws_zero_volts_at_the_vertical_middle(self):
        # Exercise the real drawing method without a window/display dependency.
        class Canvas:
            def __init__(self):
                self.lines, self.texts = [], []

            def winfo_width(self):
                return 820

            def winfo_height(self):
                return 520

            def delete(self, *_args):
                pass

            def create_rectangle(self, *_args, **_kwargs):
                pass

            def create_line(self, *coords, **options):
                self.lines.append((coords, options))

            def create_text(self, *coords, **options):
                self.texts.append((coords, options))

        value = lambda text: SimpleNamespace(get=lambda: text, set=lambda _: None)
        canvas = Canvas()
        page = SimpleNamespace(
            rom_canvas=canvas, ctx=SimpleNamespace(colors={}), mode_key="full_mode",
            _button_rom_preview_signature=lambda: ("gauss",), _sinc_selected=lambda: False,
            rom_waveform_var=value("Gauss"), rom_depth_var=value("256"), rom_point_time_var=value("1"),
            rom_phase_var=value("1"), rom_half_cycle_var=value("00"), rom_vpk_var=value("8"),
            rom_micro_bias_var=value("2"), rom_offset_var=value("0"), rom_metric_var=value(""),
            rom_frequency_var=value(""), _parse_binary_option=lambda text: int(text),
            tr=lambda key, **kwargs: key,
        )
        AWP1221ModePage._draw_button_rom_preview(page)
        middle = 24 + (520 - 24 - 72) / 2
        zero_labels = [(coords, options) for coords, options in canvas.texts if options.get("text") == "0 V"]
        self.assertEqual(len(zero_labels), 1)
        self.assertEqual(zero_labels[0][0][1], middle)
        curve = next(coords for coords, _ in canvas.lines if len(coords) > 4)
        self.assertEqual(curve[1], middle)
        self.assertEqual(curve[-1], middle)
        self.assertGreater(max(curve[1::2]), middle)


if __name__ == "__main__":
    unittest.main()
