"""Real-window regressions for waveform preview canvases that must draw on show."""

import os
from pathlib import Path
import unittest

from tests.validate_responsive_layout import _flush, mocked_application


class WaveformPreviewVisibilityTests(unittest.TestCase):
    def open_app(self):
        context = mocked_application()
        app = context.__enter__()

        def cleanup():
            try:
                context.__exit__(None, None, None)
            except Exception:
                try:
                    app.root.destroy()
                except Exception:
                    pass

        self.addCleanup(cleanup)
        app.set_animations(False)
        app.root.geometry("1366x768+0+0")
        return app

    def assert_preview(self, app, canvas):
        _flush(app.root, 5)
        self.assertTrue(canvas.find_all())
        self.assertGreater(canvas.winfo_width(), 1)
        self.assertGreater(canvas.winfo_height(), 10)
        self.assertGreater(canvas.master.winfo_height(), 10)
        self.assertTrue(canvas.winfo_ismapped())
        self.assertTrue(canvas.winfo_viewable())
        return [(canvas.type(item), canvas.coords(item),
                 canvas.itemcget(item, "text") if canvas.type(item) == "text" else "")
                for item in canvas.find_all()]

    def capture(self, app, canvas, family, label):
        directory = os.environ.get("PREVIEW_SCREENSHOT_DIR")
        if not directory:
            return
        from PIL import ImageGrab
        from pages.base_page import VerticalScrollArea

        focus = canvas.master
        current = focus
        while current is not None:
            if isinstance(current, VerticalScrollArea):
                _flush(app.root)
                y = current.canvas.canvasy(focus.winfo_rooty() - current.canvas.winfo_rooty())
                region = current.canvas.tk.splitlist(current.canvas.cget("scrollregion"))
                height = float(region[3]) if len(region) == 4 and float(region[3]) else 1.0
                current.canvas.yview_moveto(max(0, (y - 50) / height))
                focus = current
            current = current.master
        _flush(app.root, 5)
        root = app.root
        path = Path(directory) / f"preview_fix_{family}_{label}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                             root.winfo_rootx() + root.winfo_width(),
                             root.winfo_rooty() + root.winfo_height()),
                       xdisplay=os.environ.get("DISPLAY")).save(path)

    def _check_page(self, route, mode, family, *, select=None, show_kwargs=None):
        app = self.open_app()
        app.navigation.show(route)
        host = app.navigation.pages[route]
        host.show_mode(mode, **(show_kwargs or {}))
        _flush(app.root, 8)
        page = host.mode_pages[mode]
        wave = getattr(page, "waveform_page", page)
        if select is not None:
            select(page, wave)
            _flush(app.root, 5)
        canvas = wave.canvas if family == "1921" else wave.rom_canvas
        before = self.assert_preview(app, canvas)
        variable = (wave.voltage_amplitude_var if family == "1921"
                    else wave.rom_waveform_var)
        original = variable.get()
        if family == "1921":
            value = "30" if original != "30" else "20"
        else:
            value = "Gauss" if original != "Gauss" else (
                "SIN" if family == "1018" else "SINE")
        variable.set(value)
        self.assertNotEqual(before, self.assert_preview(app, canvas))
        variable.set(original)
        self.assert_preview(app, canvas)
        app.root.geometry("1280x720+0+0")
        self.assert_preview(app, canvas)
        app.root.geometry("1366x768+0+0")
        self.assert_preview(app, canvas)
        self.capture(app, canvas, family, "button")

    def test_1018_normal_selector_draws(self):
        self._check_page("awp1018", "awp1018_normal_mode", "1018",
                         show_kwargs={"update_chip": False})

    def test_1221_button_selector_draws(self):
        def select(page, _wave):
            page._select_button_wave_tab("rom")
        self._check_page("awp1221", "button_mode", "1221", select=select)

    def test_192x_button_ram_draws(self):
        def select(_page, wave):
            wave.select_source("ram")
        self._check_page("awp1921_1923", "button_mode", "1921", select=select)


if __name__ == "__main__":
    unittest.main()
