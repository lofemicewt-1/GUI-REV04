"""Mode selection survives paint timeouts; explicit cancellation restores it."""
import sys
import time
import unittest
from unittest.mock import patch

from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
from tests.validate_responsive_layout import mocked_application


@unittest.skipUnless(sys.platform == "win32", "Windows native mode transitions")
class ModeNavigationCompletionTests(unittest.TestCase):
    def setUp(self):
        prepare_tk_test(self)
        fixture = mocked_application()
        self.app = fixture.__enter__()
        self.root = self.app.root
        attach_tk_cleanup(self, self.root)
        self.addCleanup(fixture.__exit__, None, None, None)
        self.root.geometry("1200x800")
        self.nav = self.app.navigation
        self.nav.set_developer_visibility_override(True)
        self.app.set_animations(False)
        self.nav.show("awp1221")
        self.host = self.nav.mode_host
        self.host.show_mode("full_mode")
        self.host.show_mode("button_mode")
        self.pump(500)
        self.root.deiconify()
        self.pump(200)
        self.app.set_animations(True)
        self.errors = []
        self.root.report_callback_exception = lambda kind, value, trace: self.errors.append(str(value))
        self.foreground = patch.object(self.app.transitions, "_owner_is_foreground", return_value=True)
        self.foreground.start()
        self.addCleanup(self.foreground.stop)

    def pump(self, ms):
        self.root.after(ms, self.root.quit)
        self.root.mainloop()

    def assert_selection(self, key):
        deadline = time.perf_counter() + 3.5
        while (self.nav._transition_pending or self.app.transitions.active) and time.perf_counter() < deadline:
            self.pump(25)
        self.assertEqual(self.host.current_mode, key)
        self.assertTrue(self.host.mode_pages[key].winfo_viewable())
        self.assertEqual([name for name, button in self.nav.mode_buttons.items()
                          if button.cget("style") == "SidebarModeSelected.TButton"], [key])
        target_y = round(self.nav.motion._rect(self.nav.mode_buttons[key])[1])
        self.assertEqual(self.nav.motion.indicator.winfo_y(), target_y)
        self.assertFalse(self.nav._transition_pending)
        self.assertFalse(self.app.transitions.active)
        self.assertIsNone(self.nav._pending_mode)
        self.assertFalse(self.errors, self.errors)

    def test_paint_timeout_keeps_the_selected_mode(self):
        reveal = self.app.transitions.reveal
        def short_reveal(*args, **kwargs):
            kwargs["wait_s"] = .05
            return reveal(*args, **kwargs)
        with patch.object(self.nav, "_page_layout_ready", return_value=False), \
             patch.object(self.app.transitions, "reveal", side_effect=short_reveal):
            self.nav.mode_buttons["full_mode"].invoke()
            self.assertTrue(self.app.transitions.active)
            self.pump(700)
        self.assert_selection("full_mode")

    def test_explicit_cancel_restores_page_background_and_indicator(self):
        with patch.object(self.nav, "_page_layout_ready", return_value=False):
            self.nav.mode_buttons["full_mode"].invoke()
            self.assertTrue(self.app.transitions.active)
            self.pump(40)
            self.nav._cancel_preparation()
            self.pump(500)
        self.assert_selection("button_mode")

    def test_latest_mode_click_supersedes_an_unfinished_transition(self):
        with patch.object(self.nav, "_page_layout_ready", return_value=False):
            self.nav.mode_buttons["full_mode"].invoke()
            self.assertTrue(self.app.transitions.active)
            self.pump(40)
            self.nav.mode_buttons["button_mode"].invoke()
            self.pump(500)
        self.assert_selection("button_mode")

    def test_completed_transition_keeps_page_and_sidebar_in_sync(self):
        with patch.object(self.nav, "_page_layout_ready", return_value=True), \
             patch("app.transitions._native_paint_pending", return_value=False):
            self.nav.mode_buttons["full_mode"].invoke()
            self.pump(900)
        self.assert_selection("full_mode")


if __name__ == "__main__":
    unittest.main()
