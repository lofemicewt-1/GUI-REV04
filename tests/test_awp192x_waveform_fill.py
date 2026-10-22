"""AWP192x previews use the available width after resize and maximize."""

import unittest
import faulthandler

from tests.validate_responsive_layout import _flush, mocked_application


class WaveformFillTests(unittest.TestCase):
    def assert_fills(self, container, panel, canvas, *, address_plot=False):
        self.assertLessEqual(
            abs(container.winfo_width() - panel.winfo_x() - panel.winfo_width()), 2,
            "Waveform panel leaves unused space on the right",
        )
        self.assertGreater(canvas.winfo_width(), panel.winfo_width() - 40)
        curves = canvas.find_withtag("wave_curve") or [
            item for item in canvas.find_all()
            if canvas.type(item) == "line" and len(canvas.coords(item)) > 4
        ]
        self.assertTrue(curves, "Resized preview must still contain its waveform")
        coords = [x for item in curves for x in canvas.coords(item)[::2]]
        # The standalone generator uses SRAM addresses for its x axis, so
        # a 256-point slice intentionally occupies part of the 1024 addresses.
        if not address_plot:
            self.assertGreater(max(coords) - min(coords), canvas.winfo_width() * .85)

    def test_button_and_generator_fill_after_resize_maximize_and_restore(self):
        faulthandler.dump_traceback_later(180, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        with mocked_application() as app:
            app.set_animations(False)
            errors = []
            app.root.report_callback_exception = lambda *args: errors.append(str(args))
            app.root.geometry("1920x1080+0+0")
            app.root.maxsize(5000, 3000)
            app.navigation.show("awp1921_1923")
            host = app.navigation.pages["awp1921_1923"]
            host.show_mode("button_mode")
            _flush(app.root, 1)
            wave = host.mode_pages["button_mode"].waveform_page
            # Wide windows must fill beyond the former 960/1200-pixel cap.
            for size in ("3000x1400", "1024x768", "1920x1080"):
                app.root.geometry(size)
                for source in ("sinc", "rom", "ram"):
                    with self.subTest(size=size, source=source):
                        wave.select_source(source)
                        _flush(app.root, 1)
                        self.assert_fills(wave, wave.plot_panel, wave.canvas)
            app.root.state("zoomed")
            _flush(app.root, 1)
            self.assert_fills(wave, wave.plot_panel, wave.canvas)
            app.root.state("normal")
            _flush(app.root, 1)
            self.assert_fills(wave, wave.plot_panel, wave.canvas)

            host.show_mode("full_mode")
            _flush(app.root, 1)
            full = host.mode_pages["full_mode"]
            full.open_waveform_generator()
            _flush(app.root, 1)
            generator = full.waveform_page
            window = full._waveform_window
            window.maxsize(5000, 3000)
            for size in ("3000x1400", "1120x720"):
                window.geometry(size)
                _flush(app.root, 1)
                self.assert_fills(generator._body_layout_frame, generator.canvas.master, generator.canvas, address_plot=True)
            window.state("zoomed")
            _flush(app.root, 1)
            self.assert_fills(generator._body_layout_frame, generator.canvas.master, generator.canvas, address_plot=True)
            window.state("normal")
            _flush(app.root, 1)
            self.assert_fills(generator._body_layout_frame, generator.canvas.master, generator.canvas, address_plot=True)
            full._close_waveform_window()
            self.assertFalse(errors, errors)


if __name__ == "__main__":
    unittest.main()
