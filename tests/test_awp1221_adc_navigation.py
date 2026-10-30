from __future__ import annotations

import time
import unittest

from pages.base_page import VerticalScrollArea
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
from tests.validate_responsive_layout import mocked_application


class Awp1221AdcNavigationTests(unittest.TestCase):
    def pump(self, ms: int = 100) -> None:
        self.root.after(ms, self.root.quit)
        self.root.mainloop()

    def wait_for(self, predicate) -> None:
        deadline = time.perf_counter() + 8
        while not predicate() and time.perf_counter() < deadline:
            self.pump(25)
        self.assertTrue(predicate(), "page did not finish mapping")

    def settle(self, app, page, canvas, heading) -> None:
        previous = None
        stable = 0
        deadline = time.perf_counter() + 8
        while stable < 5 and time.perf_counter() < deadline:
            self.pump()
            canvas.yview()  # Flush queued native scroll operations before measuring.
            geometry = (canvas.winfo_width(), canvas.winfo_height(), canvas.cget("scrollregion"),
                        heading.winfo_rooty(), heading.winfo_height(), page.winfo_height(),
                        page._button_adc_split_signature)
            ready = (app.navigation._pending_page is None and app.navigation._pending_mode is None
                     and not app.transitions.active and not app.viewport_presentation.resizing)
            stable = stable + 1 if ready and geometry == previous else 0
            previous = geometry
        self.assertGreaterEqual(stable, 5, "page geometry did not settle")

    def test_adc_step_click_reveals_parameter_heading(self) -> None:
        prepare_tk_test(self)
        fixture = mocked_application()
        app = fixture.__enter__()
        self.root = app.root
        attach_tk_cleanup(self, self.root)
        self.addCleanup(fixture.__exit__, None, None, None)
        self.root.geometry("1700x980")
        self.root.deiconify()
        app.navigation.show("awp1221")
        self.pump(500)
        host = app.navigation.pages["awp1221"]
        host.show_mode("button_mode")
        deadline = time.perf_counter() + 8
        while "button_mode" not in host.mode_pages and time.perf_counter() < deadline:
            self.pump(25)
        self.assertIn("button_mode", host.mode_pages)
        page = host.mode_pages["button_mode"]
        host.show_step_chrome(page)
        self.wait_for(lambda: page.winfo_ismapped() and page.step_progress.canvas.winfo_ismapped())
        self.pump(300)

        section = page._button_adc_panels[1]
        heading = section._module_title_label
        area = section.master
        while not isinstance(area, VerticalScrollArea):
            area = area.master
        canvas = area.canvas
        bar = page.step_progress
        for width, language, theme in (
            (1700, "zh", "light"),
            (1280, "zh", "light"),
            (1280, "en", "dark"),
            (1700, "en", "dark"),
        ):
            with self.subTest(width=width, language=language, theme=theme):
                self.root.geometry(f"{width}x980")
                app.apply_settings(language, theme, app.state.font_size)
                self.settle(app, page, canvas, heading)
                self.assertTrue(page.winfo_ismapped())
                self.assertTrue(bar.canvas.winfo_ismapped())
                for start in (0.0, 1.0):
                    canvas.yview_moveto(start)
                    self.settle(app, page, canvas, heading)
                    before_x = canvas.xview()
                    x1, y1, x2, y2 = bar._hit_rects["adc"]
                    x, y = int((x1 + x2) / 2), int((y1 + y2) / 2)
                    bar.canvas.event_generate("<ButtonPress-1>", x=x, y=y)
                    bar.canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
                    self.settle(app, page, canvas, heading)
                    offset = heading.winfo_rooty() - canvas.winfo_rooty()
                    self.assertGreaterEqual(offset, 0, "ADC parameter heading scrolled above viewport")
                    self.assertLessEqual(offset, 50, "ADC step did not align the parameter heading near the top")
                    self.assertLessEqual(offset + heading.winfo_height(), canvas.winfo_height())
                    self.assertEqual(canvas.xview(), before_x)
                    self.assertEqual(bar.active_step_key, "adc")


if __name__ == "__main__":
    unittest.main()
