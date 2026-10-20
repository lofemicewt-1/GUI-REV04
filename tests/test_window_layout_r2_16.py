from __future__ import annotations

import unittest
from unittest.mock import patch

from app.window_layout import (
    is_compact_window,
    scaled_content_max_width,
    scaled_waveform_max_width,
    ui_scale_for_width,
)


class WindowLayoutTierTests(unittest.TestCase):
    def test_frozen_width_breakpoints_and_width_caps(self) -> None:
        self.assertEqual(ui_scale_for_width(1599), 1.0)
        self.assertEqual(ui_scale_for_width(1600), 1.1)
        self.assertEqual(ui_scale_for_width(2199), 1.1)
        self.assertEqual(ui_scale_for_width(2200), 1.25)
        self.assertTrue(is_compact_window(1399, 900))
        self.assertTrue(is_compact_window(1600, 799))
        self.assertFalse(is_compact_window(1400, 800))
        self.assertEqual(scaled_content_max_width(1.25, 3000), 2250)
        self.assertEqual(scaled_content_max_width(1.1, 1200), 1200)
        self.assertEqual(scaled_waveform_max_width(1.25), 1200)


class WindowResizeLayoutTests(unittest.TestCase):
    def test_resize_reuses_built_page_without_activation(self) -> None:
        from tests.test_motion import pump
        from tests.validate_responsive_layout import mocked_application

        with mocked_application() as app:
            app.set_animations(False)
            root, nav = app.root, app.navigation
            home = dict.get(nav.pages, "home")
            self.assertIsNotNone(home)
            page_ids = {name: id(page) for name, page in zip(nav.pages.keys(), nav.pages.built_values())}
            activation = patch.object(home, "on_show", wraps=home.on_show)
            with activation as on_show:
                for width, height in ((1920, 1080), (2000, 1080), (1500, 900)):
                    root.geometry(f"{width}x{height}+0+0")
                    pump(root, 0.09)
                self.assertEqual(on_show.call_count, 0)

            self.assertEqual(app.state.ui_scale, 1.0)
            self.assertIs(dict.get(nav.pages, "home"), home)
            self.assertEqual(
                {name: id(page) for name, page in zip(nav.pages.keys(), nav.pages.built_values())},
                page_ids,
            )
            self.assertFalse(nav._hbar_visible)

            root.geometry("2560x1440+0+0")
            pump(root, 0.12)
            self.assertEqual(app.state.ui_scale, 1.25)
            canvas_width = int(nav.content_canvas.winfo_width())
            item_width = int(float(nav.content_canvas.itemcget(nav.content_window, "width")))
            item_x = int(float(nav.content_canvas.coords(nav.content_window)[0]))
            self.assertLessEqual(item_width, 2250)
            self.assertEqual(item_x, max(0, (canvas_width - item_width) // 2))


if __name__ == "__main__":
    unittest.main()
