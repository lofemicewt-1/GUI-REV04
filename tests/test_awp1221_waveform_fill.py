"""AWP1221 Button previews fill their column after maximize and restore."""

import faulthandler
import unittest

from tests.validate_responsive_layout import _flush, mocked_application


class Awp1221WaveformFillTests(unittest.TestCase):
    def assert_fills(self, page):
        shell = page._waveform_selector_shell
        panel = page._waveform_selector_preview
        canvas = page.rom_canvas
        self.assertLessEqual(
            abs(shell.winfo_width() - panel.winfo_x() - panel.winfo_width()), 2,
            "Waveform preview must fill the available column width",
        )
        self.assertGreaterEqual(canvas.winfo_width(), panel.winfo_width() - 2)
        self.assertTrue(canvas.winfo_viewable())
        self.assertGreaterEqual(canvas.winfo_height(), 500)
        for label in page._selector_widgets["awp1221_rom_preview_captions"]:
            self.assertFalse(label.winfo_ismapped(), "Preview explanations belong inside the canvas")
        if page._sinc_selected():
            self.assertFalse(canvas.find_withtag("sinc_segment_card"))
        else:
            details = canvas.find_withtag("rom_preview_details")
            self.assertEqual(len(details), 1)
            text = canvas.itemcget(details[0], "text")
            self.assertIn(page.rom_metric_var.get(), text)
            self.assertIn(page.rom_frequency_var.get(), text)
            self.assertEqual(canvas.itemcget(details[0], "fill"), page.colors["muted"])
            summary = canvas.find_withtag("wave_duration")[0]
            self.assertLess(canvas.bbox(details[0])[3], canvas.bbox(summary)[1])
        self.assertLessEqual(panel.winfo_y() + panel.winfo_height(),
                             page._button_waveform_scroll.canvas.winfo_height())
        bounds = canvas.bbox("all")
        self.assertIsNotNone(bounds)
        self.assertGreaterEqual(bounds[0], 0)
        self.assertGreaterEqual(bounds[1], 0)
        self.assertLessEqual(bounds[2], canvas.winfo_width())
        self.assertLessEqual(bounds[3], canvas.winfo_height())
        curves = [canvas.coords(item) for item in canvas.find_all()
                  if canvas.type(item) == "line" and len(canvas.coords(item)) > 4]
        self.assertTrue(curves)
        positions = [x for curve in curves for x in curve[::2]]
        self.assertGreater(max(positions) - min(positions), canvas.winfo_width() - 80)

    def test_button_preview_fills_wide_maximized_and_restored_windows(self):
        faulthandler.dump_traceback_later(120, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        with mocked_application() as app:
            app.set_animations(False)
            app.root.maxsize(5000, 3000)
            app.root.geometry("3000x1400+0+0")
            errors = []
            app.root.report_callback_exception = lambda *args: errors.append(str(args))
            app.navigation.show("awp1221")
            host = app.navigation.pages["awp1221"]
            host.show_mode("button_mode")
            page = host.mode_pages["button_mode"]
            _flush(app.root, 1)
            for size in ("3000x1400", "1280x720", "1920x1080"):
                app.root.geometry(size)
                for tab in ("sinc", "rom"):
                    with self.subTest(size=size, tab=tab):
                        page._select_button_wave_tab(tab)
                        _flush(app.root, 1)
                        self.assert_fills(page)
            for state in ("zoomed", "normal"):
                app.root.state(state)
                for tab in ("sinc", "rom"):
                    with self.subTest(state=state, tab=tab):
                        page._select_button_wave_tab(tab)
                        _flush(app.root, 1)
                        self.assert_fills(page)
            self.assertFalse(errors, errors)


if __name__ == "__main__":
    unittest.main()
