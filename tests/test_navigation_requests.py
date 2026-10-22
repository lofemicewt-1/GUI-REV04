from __future__ import annotations

import time
import sys
import unittest
from tkinter import ttk
from unittest.mock import patch
from tests.validate_responsive_layout import mocked_application


class NavigationRequestTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "native sidebar paint")
    def test_selection_is_painted_before_an_expensive_factory_starts(self):
        from tests.test_motion import pump
        from tests.validate_navigation_paint import _selection_pixel_ready
        with mocked_application() as app:
            app.set_animations(False)
            app.apply_settings("zh", "light", 10)
            root, nav = app.root, app.navigation
            root.geometry("1200x800+0+0")
            seen = []
            def factory():
                seen.append(_selection_pixel_ready(nav.buttons["paint_target"], app.colors["primary_hover"]))
                time.sleep(.08)
                return ttk.Frame(nav.content, style="App.TFrame")
            nav.register_page("paint_target", "home", factory)
            pump(root, .2)
            nav.buttons["paint_target"].invoke()
            root.after(450, root.quit)
            root.mainloop()
            self.assertEqual(seen, [True], "cold page construction started before the selected button was painted")

    def test_sidebar_clicks_paint_selection_before_building_only_latest_target(self):
        with mocked_application() as app:
            app.set_animations(False)
            root, nav = app.root, app.navigation
            root.update()
            events, errors = [], []
            root.report_callback_exception = lambda k,v,tb: errors.append(str(v))
            def factory(name):
                events.append("build:"+name)
                time.sleep(.15)  # Representative expensive page constructor.
                return ttk.Frame(nav.content, style="App.TFrame")
            for name in ("skipped", "target"):
                nav.register_page(name, "home", lambda n=name: factory(n))
            nav.buttons["skipped"].invoke()
            nav.buttons["target"].invoke()
            self.assertEqual(events, [], "button handler synchronously built chip pages")
            self.assertEqual(nav.buttons["target"].cget("style"), "SidebarSelected.TButton")
            root.after(0, lambda: events.append("paint-opportunity"))
            root.after(450, root.quit)
            root.mainloop()
            self.assertEqual(events, ["paint-opportunity", "build:target"])
            self.assertEqual(app.state.current_page, "target")
            self.assertFalse(errors, errors)

    def test_navigation_is_not_starved_by_a_continuous_timer_stream(self):
        with mocked_application() as app:
            app.set_animations(False)
            root, nav = app.root, app.navigation
            root.update()
            busy, seen = [True], []
            def factory():
                seen.append(busy[0])
                return ttk.Frame(nav.content)
            def timer_stream():
                if busy[0]:
                    root.after(0, timer_stream)
            nav.register_page("busy_target", "home", factory)
            nav.buttons["busy_target"].invoke()
            root.after(0, timer_stream)
            root.after(180, lambda: busy.__setitem__(0, False))
            root.after(450, root.quit)
            root.mainloop()
            self.assertEqual(seen, [True], "navigation waited for the timer stream to stop")
            self.assertEqual(app.state.current_page, "busy_target")
            self.assertIsNone(nav._pending_navigation)
            self.assertIsNone(nav._pending_navigation_fallback)

    def test_clicking_current_page_cancels_pending_expensive_navigation(self):
        with mocked_application() as app:
            app.set_animations(False)
            root, nav = app.root, app.navigation
            root.update()
            built=[]
            nav.register_page("skipped", "home", lambda: built.append(True) or ttk.Frame(nav.content))
            nav.buttons["skipped"].invoke()
            nav.buttons["home"].invoke()
            root.after(180, root.quit)
            root.mainloop()
            self.assertFalse(built)
            self.assertEqual(app.state.current_page, "home")

    def test_awp192x_does_not_build_unselected_modes(self):
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show("awp1921_1923")
            page = app.navigation.pages["awp1921_1923"]
            self.assertEqual(list(dict.keys(page.mode_pages)), ["button_mode"])
            page.show_mode("full_mode")
            self.assertEqual(list(dict.keys(page.mode_pages)), ["button_mode", "full_mode"])

    def test_mode_click_supersedes_pending_chip_request(self):
        with mocked_application() as app:
            app.set_animations(False)
            root, nav = app.root, app.navigation
            nav.show("awp1221")
            root.update()
            nav.buttons["awp1922"].invoke()
            nav._on_mode_clicked("full_mode")
            deadline = time.perf_counter()+4
            while (nav._preparation is not None or nav._pending_navigation is not None) and time.perf_counter() < deadline:
                root.update()
                time.sleep(.001)
            self.assertEqual(app.state.current_page, "awp1221")
            self.assertEqual(nav.pages["awp1221"].current_mode, "full_mode")
            self.assertFalse(dict.__contains__(nav.pages, "awp1922"))

    def test_failed_page_build_restores_selection_and_keeps_current_page(self):
        with mocked_application() as app:
            app.set_animations(False)
            root, nav = app.root, app.navigation
            root.update()
            errors = []
            root.report_callback_exception = lambda k,v,tb: errors.append(str(v))
            def fail():
                raise ValueError("page build failed")
            nav.register_page("bad", "home", fail)
            nav.buttons["bad"].invoke()
            root.after(180, root.quit)
            root.mainloop()
            self.assertEqual(errors, ["page build failed"])
            self.assertEqual(app.state.current_page, "home")
            self.assertEqual(nav.buttons["home"].cget("style"), "SidebarSelected.TButton")
            self.assertFalse(app.transitions.active)

    def test_sinc_preview_reuses_plan_and_invalidates_when_input_changes(self):
        from pages import awp1221_page as module
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show("awp1221")
            view = app.navigation.pages["awp1221"].mode_pages["button_mode"]
            view._sinc_plan_cache = None
            with patch.object(module, "build_awp1221_sinc_plan", wraps=module.build_awp1221_sinc_plan) as build:
                first = view._build_current_sinc_plan()
                self.assertEqual(view._build_current_sinc_plan(), first)
                self.assertEqual(build.call_count, 1)
                view.sinc_time1_var.set("11")
                updated = view._build_current_sinc_plan()
                self.assertEqual(updated.params.times_us[0], 11)
                self.assertNotEqual(updated.params, first.params)
                self.assertEqual(view._build_current_sinc_plan(), updated)
                self.assertEqual(build.call_count, 2)


if __name__ == "__main__":
    unittest.main()
