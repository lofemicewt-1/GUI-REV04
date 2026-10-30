"""ROM target fitting and its real Tk editor/write boundary (offline)."""
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from pages.awp1221_page import (
    awp1221_solve_rom,
    awp1221_waveform_effective_depth_points,
    awp1221_waveform_offset_voltage,
    awp1221_waveform_peak_to_peak_voltage,
    awp1221_waveform_preview_voltage,
    encode_awp1221_waveform_descriptor,
)
from tests.validate_responsive_layout import _capture_window_bmp, _flush, mocked_application


class RomSolverTests(unittest.TestCase):
    def test_exact_sine_target_and_depth_tie_prefers_more_points(self):
        solution = awp1221_solve_rom(1_000_000 / 5120, 62.5, -37.5)
        self.assertEqual((solution.depth, solution.point_time), (512, 10))
        self.assertEqual((solution.coarse, solution.fine), (15, 3))
        self.assertEqual(awp1221_waveform_offset_voltage(solution.offset), 12.5)
        self.assertEqual((solution.upper, solution.lower), (62.5, -37.5))

    def test_quantized_frequency_is_the_nearest_hardware_candidate(self):
        for target in (8, 80, 200, 333, 3000):
            with self.subTest(target=target):
                solution = awp1221_solve_rom(target, 60, -20)
                best = min(abs(1_000_000 / (depth * time) - target)
                           for depth in (512, 256) for time in range(1, 257))
                self.assertAlmostEqual(abs(solution.frequency - target), best)
                self.assertLessEqual(abs(solution.upper - 60), 0.78125)
                self.assertLessEqual(abs(solution.lower + 20), 0.78125)

    def test_actual_extrema_match_preview_for_all_supported_shapes(self):
        for waveform in ("SINE", "Gauss", "Fixed-slope Ramp"):
            for half in range(4):
                for phase in range(2):
                    low, high = (-20, 30) if waveform == "SINE" and (half in (0, 3) or phase == 0) else (
                        (0, 40) if (waveform == "SINE" or phase == 0) else (-40, 0))
                    with self.subTest(waveform=waveform, half=half, phase=phase):
                        solution = awp1221_solve_rom(200, high, low, waveform, half, phase)
                        values = [awp1221_waveform_preview_voltage(waveform, p / 160,
                            half, phase, solution.coarse, solution.fine, solution.offset)
                            for p in range(161)]
                        self.assertAlmostEqual(max(values), solution.upper)
                        self.assertAlmostEqual(min(values), solution.lower)
                        points = awp1221_waveform_effective_depth_points(waveform, solution.depth, half)
                        self.assertEqual(solution.frequency, 1_000_000 / (points * solution.point_time))

    def test_rails_offset_limits_and_smallest_amplitude(self):
        for high, low in ((100, -100), (100, 0), (0, -98.4375), (0.78125, -0.78125)):
            solution = awp1221_solve_rom(200, high, low)
            self.assertEqual((solution.upper, solution.lower), (high, low))

    def test_invalid_or_unreachable_inputs_are_rejected(self):
        for args in ((0, 20, -20), (-1, 20, -20), (200, -20, 20),
                     (200, 101, 0), (200, 0, -101), (200, 0, 0),
                     (200, 80, 60), (200, 0.1, 0), (1, 20, -20),
                     (5000, 20, -20), (math.nan, 20, -20), (200, math.inf, 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                awp1221_solve_rom(*args)
        with self.assertRaisesRegex(ValueError, "dc_error"):
            awp1221_solve_rom(200, 20, -20, "DC")


class RomInputUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = mocked_application()
        cls.app = cls.context.__enter__()
        cls.app.set_animations(False)
        cls.root = cls.app.root
        cls.errors = []
        cls.root.report_callback_exception = lambda *args: cls.errors.append(str(args))
        cls.app.navigation.show("awp1221")
        _flush(cls.root, 5)
        cls.host = cls.app.navigation.pages["awp1221"]

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def setUp(self):
        self.host.show_mode("button_mode")
        self.page = self.host.mode_pages["button_mode"]
        self.page._select_button_wave_tab("rom")
        self.page.rom_manual_var.set(False)
        self.page.rom_waveform_var.set("SINE")
        self.page.rom_half_cycle_var.set(self.page.HALF_CYCLE_OPTIONS[0])
        self.page.rom_phase_var.set(self.page.PHASE_OPTIONS[0])
        _flush(self.root, 3)
        self.errors.clear()

    def targets(self, frequency="200", upper="60", lower="-20", flush=True):
        self.page.rom_target_frequency_var.set(frequency)
        self.page.rom_target_upper_var.set(upper)
        self.page.rom_target_lower_var.set(lower)
        if flush:
            _flush(self.root, 3)

    def raw(self):
        return tuple(v.get() for v in (self.page.rom_depth_var, self.page.rom_point_time_var,
            self.page.rom_vpk_var, self.page.rom_micro_bias_var, self.page.rom_offset_var))

    def test_targets_set_raw_parameters_and_show_actual_values(self):
        self.targets()
        solution = awp1221_solve_rom(200, 60, -20)
        self.assertEqual(self.raw(), (str(solution.depth), str(solution.point_time),
            self.page.VPK_OPTIONS[solution.coarse], self.page.MICRO_BIAS_OPTIONS[solution.fine],
            self.page.OFFSET_OPTIONS[solution.offset]))
        self.assertIn("195.312", self.page.rom_target_result_var.get())
        self.assertFalse(self.page._selector_widgets["awp1221_rom_amplitude"][0].winfo_manager())
        self.assertTrue(self.page._selector_widgets["awp1221_rom_target_upper"][0].winfo_manager())
        self.assertEqual(self.errors, [])

    def test_write_before_idle_uses_new_targets_and_original_descriptor_encoding(self):
        self.targets(flush=False)
        solution = awp1221_solve_rom(200, 60, -20)
        with patch.object(self.page, "_ensure_connected", return_value=True), patch(
            "pages.awp1221_page._run_ui_background_task", side_effect=lambda _page, task, **_kw: task()
        ), patch.object(self.page, "_write_waveform_selection") as writer:
            self.assertTrue(self.page.apply_waveform_selection())
        expected = encode_awp1221_waveform_descriptor(source_code=2, micro_bias_code=solution.fine,
            waveform_code=0, depth_code=0 if solution.depth == 512 else 1,
            half_cycle_code=0, phase_code=0, point_time=solution.point_time,
            repeat_count=int(self.page.rom_repeat_var.get()),
            infinite_loop=bool(self.page.rom_infinite_loop_var.get()))
        self.assertEqual(writer.call_args.args[3:], (expected, solution.offset, solution.coarse))
        self.page._set_waveform_write_busy(False)

    def test_invalid_target_blocks_write_and_export_without_using_previous_values(self):
        self.targets()
        previous = self.raw()
        self.targets(upper="oops")
        self.assertEqual(self.raw(), previous)
        with patch("pages.awp1221_page.messagebox.showerror"), patch.object(
            self.page, "_ensure_connected") as connected:
            self.assertFalse(self.page.apply_waveform_selection())
            connected.assert_not_called()
        with self.assertRaises(ValueError):
            self.page._build_current_register_script_operations()

    def test_manual_parameters_and_sinc_are_preserved(self):
        self.targets()
        self.page.rom_manual_var.set(True)
        self.page._on_rom_manual_changed()
        self.page.rom_point_time_var.set("7")
        self.page.rom_depth_var.set("256")
        _flush(self.root, 3)
        previous = self.raw()
        self.page.rom_manual_var.set(False)
        self.page._on_rom_manual_changed()
        self.assertTrue(self.page._solve_rom_targets())
        self.assertEqual(self.raw(), previous)
        sinc = self.page._sinc_group_snapshot()
        self.page._select_button_wave_tab("sinc")
        _flush(self.root, 3)
        self.assertEqual(self.page._sinc_group_snapshot(), sinc)
        self.assertFalse(self.page._selector_widgets["awp1221_rom_target_upper"][0].winfo_manager())
        self.assertFalse(self.page._selector_widgets["awp1221_rom_manual"][0].winfo_manager())
        self.page._select_button_wave_tab("rom")
        _flush(self.root, 3)
        self.assertEqual(self.raw(), previous)
        self.assertEqual(self.errors, [])

    def test_layout_and_mode_restoration_in_both_languages(self):
        out = Path(__file__).resolve().parents[1] / "outputs/awp1221_rom_input_20261023"
        out.mkdir(parents=True, exist_ok=True)
        for mode in ("button_mode", "full_mode"):
            self.host.show_mode(mode)
            self.page = self.host.mode_pages[mode]
            self.page.rom_source_var.set("ROM")
            self.page.rom_waveform_var.set("SINE")
            for language, theme, size, font, scale in (
                ("zh", "light", "1700x980", 10, 1.5),
                ("en", "dark", "1280x720", 14, 2.0),
                ("zh", "light", "640x480", 10, 1.5),
            ):
                with self.subTest(mode=mode, language=language, size=size):
                    self.root.tk.call("tk", "scaling", scale)
                    self.app.state.language, self.app.state.theme = language, theme
                    self.app.state.font_size = font
                    self.app._apply_visual_settings()
                    self.host.refresh_language()
                    self.root.geometry(size)
                    _flush(self.root, 3)
                    self.targets()
                    widgets = [w for key in ("awp1221_rom_target_frequency", "awp1221_rom_target_upper",
                        "awp1221_rom_target_lower", "awp1221_rom_manual", "awp1221_rom_target_result")
                        for w in self.page._selector_widgets[key]]
                    for widget in widgets:
                        self.assertTrue(widget.winfo_manager())
                        self.assertGreaterEqual(widget.winfo_x(), 0)
                        self.assertGreaterEqual(widget.winfo_y(), 0)
                        self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), widget.master.winfo_width())
                        self.assertLessEqual(widget.winfo_y() + widget.winfo_height(), widget.master.winfo_height())
                    for i, first in enumerate(widgets):
                        for second in widgets[i + 1:]:
                            overlap_x = min(first.winfo_x() + first.winfo_width(), second.winfo_x() + second.winfo_width()) - max(first.winfo_x(), second.winfo_x())
                            overlap_y = min(first.winfo_y() + first.winfo_height(), second.winfo_y() + second.winfo_height()) - max(first.winfo_y(), second.winfo_y())
                            self.assertFalse(overlap_x > 0 and overlap_y > 0, (str(first), str(second)))
                    self.assertEqual(self.errors, [])
                    if mode == "button_mode" and size == "1700x980":
                        self.host.mode_scroll.canvas.yview_moveto(0)
                        _flush(self.root, 2)
                        self.assertIsNone(_capture_window_bmp(self.root, out / "rom-input.bmp"))
        self.app.state.language, self.app.state.theme, self.app.state.font_size = "zh", "light", 10
        self.app._apply_visual_settings()


if __name__ == "__main__":
    unittest.main()
