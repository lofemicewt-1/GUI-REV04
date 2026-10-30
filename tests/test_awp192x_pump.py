"""Pump frequency labels must fit, and low frequencies must reach the write path."""
import faulthandler
import unittest
from tkinter import font as tkfont
from unittest.mock import patch

from pages.awp192x_modes import PumpModePage, build_frequency_options
from tests.validate_responsive_layout import _flush, mocked_application


class PumpFrequencyTests(unittest.TestCase):
    def test_rough_range_adds_supported_low_frequency_dividers(self):
        self.assertEqual(build_frequency_options()[-3:],
                         [("83 Hz", 83, 11), ("77 Hz", 77, 12), ("71 Hz", 71, 13)])

    def test_precise_range_reaches_70_hz(self):
        options = PumpModePage.build_precise_frequency_options()
        self.assertEqual(min(option[1] for option in options), 70)
        self.assertEqual(max(option[1] for option in options), 1000)
        self.assertEqual(len(options), len({option[1] for option in options}))
        for _label, frequency, sample_rate, depth in options:
            self.assertEqual(frequency, round(1000 * 1024 / int(depth) / (sample_rate + 1)))

    def test_precise_70_hz_prefers_widest_supported_data_depth(self):
        options = PumpModePage.build_precise_frequency_options()
        label, frequency, reg_value, data_depth = next(item for item in options if item[1] == 70)
        self.assertEqual((frequency, reg_value, data_depth), (70, 56, "256"))
        self.assertIn("Datadeep(256)", label)
        self.assertIn("Dac Sample Rate(56)", label)

    def test_frequency_text_fits_and_low_frequency_writes(self):
        faulthandler.dump_traceback_later(60, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        with mocked_application() as app:
            app.set_animations(False)
            app.root.geometry("1280x900+0+0")
            app.navigation.show("awp1921_1923")
            host = app.navigation.pages["awp1921_1923"]
            host.show_mode("pump_mode")
            _flush(app.root)
            page = host.mode_pages["pump_mode"]
            page._checks["precise_frequency"].invoke()
            _flush(app.root)
            combo = page.freq_combo
            errors = []
            app.root.report_callback_exception = lambda *args: errors.append(str(args))
            for theme in ("light", "dark"):
                app.set_theme(theme)
                for language in ("zh", "en"):
                    app.set_language(language)
                    for width, scaling in ((1024, 1.0), (1280, 1.5), (1920, 2.0)):
                        with self.subTest(theme=theme, language=language, width=width, scaling=scaling):
                            app.root.tk.call("tk", "scaling", scaling)
                            app.root.geometry(f"{width}x900+0+0")
                            _flush(app.root)
                            font = tkfont.Font(root=app.root, font=combo.cget("font"))
                            needed = max(font.measure(label) for label in combo.cget("values"))
                            self.assertGreaterEqual(combo.winfo_width() - 28, needed,
                                                    f"frequency field {combo.winfo_width()} px, text {needed} px")
                            check = page._checks["precise_frequency"]
                            self.assertGreaterEqual(check.winfo_rooty(), combo.winfo_rooty() + combo.winfo_height())
                            self.assertLessEqual(combo.winfo_x() + combo.winfo_width(), combo.master.winfo_width())
                            arrow_x = combo.winfo_width() - 8
                            self.assertIn("arrow", combo.identify(arrow_x, combo.winfo_height() // 2))
            option = next(option for option in page.precise_freq_options if option[1] == 75)
            with patch.object(page, "write_reg", return_value=True) as write:
                page.freq_var.set(option[0])
            self.assertEqual([call.args[0] for call in write.call_args_list], [0x15, 0x14])
            self.assertEqual(write.call_args_list[0].args[1], option[2])
            reg14 = write.call_args_list[1].args[1]
            depth_code = next(code for depth, code, _ in page.PUMP_DATA_DEPTH_OPTIONS if depth == option[3])
            self.assertEqual((reg14 >> 7) & 7, depth_code)
            page._checks["precise_frequency"].invoke()
            self.assertEqual(page.freq_var.get(), "77 Hz")
            page._checks["precise_frequency"].invoke()
            self.assertEqual(page._current_frequency_option()[1], 77)
            self.assertFalse(errors, errors)


if __name__ == "__main__":
    unittest.main()
