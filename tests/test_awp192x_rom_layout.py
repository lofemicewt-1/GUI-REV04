"""ROM selection must collapse the unused SRAM parameter area."""
import faulthandler
import unittest

from tests.validate_responsive_layout import _flush, mocked_application


class RomLayoutTests(unittest.TestCase):
    def test_rom_hint_follows_source_tabs_and_fields_return(self):
        faulthandler.dump_traceback_later(60, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        with mocked_application() as app:
            app.set_animations(False)
            errors = []
            app.root.report_callback_exception = lambda *args: errors.append(str(args))
            app.root.geometry("1920x1080+0+0")
            app.navigation.show("awp1921_1923")
            host = app.navigation.pages["awp1921_1923"]
            host.show_mode("button_mode")
            _flush(app.root)
            page = host.mode_pages["button_mode"]
            wave = page.waveform_page
            for source in ("ram", "sinc", "ram"):
                wave.source_buttons[source].invoke()
                _flush(app.root)
                self.assertTrue(wave.fields.winfo_ismapped())
                self.assertTrue(any(cell.winfo_ismapped() for cell in wave.cells.values()))
                wave.source_buttons["rom"].invoke()
                _flush(app.root)
                tabs = wave.source_buttons["rom"].master
                gap = wave.source_hint.winfo_rooty() - (tabs.winfo_rooty() + tabs.winfo_height())
                self.assertLessEqual(gap, 24, f"ROM hint is separated from its tabs by {gap}px of empty fields")
                self.assertFalse(wave.fields.winfo_ismapped())
                self.assertEqual(page.play_waveform_var.get(), "0x1F")
                self.assertEqual(wave.effective_points(), 1024)
                self.assertTrue(wave.canvas.find_withtag("wave_curve"))
            self.assertFalse(errors, errors)


if __name__ == "__main__":
    unittest.main()
