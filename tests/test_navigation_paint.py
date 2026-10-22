from __future__ import annotations

import unittest
import time
from unittest.mock import patch

from tests.test_motion import pump
from tests.validate_responsive_layout import mocked_application


class NavigationPaintTests(unittest.TestCase):
    def setUp(self):
        # Exercise full native presentation even on background CI desktops.
        # The production 300 ms fallback is covered separately.
        foreground = patch("app.transitions.SnapshotTransition._owner_is_foreground", return_value=True)
        foreground.start()
        self.addCleanup(foreground.stop)
        budget = patch("app.transitions.COVER_PAINT_WAIT_S", 3.0)
        budget.start()
        self.addCleanup(budget.stop)

    def test_cold_chip_switch_reveals_only_after_page_and_footer_are_mapped(self):
        with mocked_application() as app:
            app.root.geometry("1700x980+0+0")
            pump(app.root, 0.1)
            frames = []
            errors = []
            app.root.report_callback_exception = lambda k, v, tb: errors.append(f"{k.__name__}: {v}")
            original = app.animator.animate

            def measured(key, duration, step, **kwargs):
                if isinstance(key, tuple) and key[0] == "snapshot":
                    paint = step
                    def step(p):
                        if p == 0:
                            page = dict.get(app.navigation.pages, "awp1221")
                            footer = getattr(page, "_footer_panel", None)
                            frames.append((bool(page and page.winfo_ismapped()),
                                           bool(footer and footer.winfo_ismapped()),
                                           0 if page is None else page.winfo_height()))
                        paint(p)
                return original(key, duration, step, **kwargs)

            app.animator.animate = measured
            with patch.object(app.root, "update_idletasks", side_effect=AssertionError("nested redraw drain")):
                app.navigation.show("awp1221")
                deadline = time.perf_counter()+11
                while not frames and app.transitions.active and time.perf_counter() < deadline:
                    pump(app.root, 0.05)
            self.assertTrue(frames, "native page fade was not exercised")
            self.assertFalse(errors, errors)
            self.assertTrue(all(mapped and footer and height > 1 for mapped, footer, height in frames), frames)
            self.assertEqual([page.page_name for page in app.navigation.pages.built_values()],
                             ["home", "awp1221"], "navigation built an unrelated chip page")
            page = dict.get(app.navigation.pages, "awp1221")
            self.assertIs(page._footer_panel.master, page.footer_scroll.content)
            self.assertEqual(list(dict.keys(page.mode_pages)), ["button_mode"])
            button = page.mode_pages["button_mode"]
            button.rom_point_time_var.set("3")
            page.show_mode("full_mode")
            full = page.mode_pages["full_mode"]
            self.assertIs(button._shared_state_owner, full._shared_state_owner)
            page.show_mode("button_mode")
            self.assertIs(page.mode_pages["button_mode"], button)
            self.assertEqual(button.rom_point_time_var.get(), "3")

    def test_awp1018_builds_only_selected_mode_and_can_open_late_full_mode(self):
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show("awp1018")
            page = dict.get(app.navigation.pages, "awp1018")
            self.assertEqual(list(dict.keys(page.mode_pages)), ["awp1018_normal_mode"])
            self.assertEqual(list(page.mode_pages), ["awp1018_normal_mode", "awp1018_diff_mode", "full_mode"])
            page.show_mode("full_mode", update_chip=False)
            self.assertEqual(list(dict.keys(page.mode_pages)), ["awp1018_normal_mode", "full_mode"])

    def test_small_window_keeps_awp1018_body_and_advanced_tools_visible(self):
        with mocked_application() as app:
            app.set_animations(False)
            app.root.geometry("480x320+0+0")
            app.apply_settings("zh", "dark", 11)
            app.navigation.show("awp1018")
            pump(app.root, 1)
            page = app.navigation.pages["awp1018"]
            self.assertTrue(app.navigation._page_layout_ready())
            self.assertTrue(page.mode_scroll.winfo_ismapped())
            self.assertGreater(page.mode_scroll.winfo_height(), 30)
            self.assertTrue(page.identity_bar.winfo_ismapped())
            # Chrome belongs to the reserved band outside the scrolling canvas.
            self.assertIs(page._floating_group.master, page.mode_scroll)
            self.assertTrue(page._floating_group.winfo_ismapped())
            page.mode_scroll.canvas.yview_moveto(1)
            pump(app.root, .1)
            self.assertAlmostEqual(page.mode_scroll.canvas.yview()[1], 1)


if __name__ == "__main__":
    unittest.main()
