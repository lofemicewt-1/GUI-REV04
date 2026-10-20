import tkinter as tk
from tkinter import ttk
import unittest
import gc
import weakref
from unittest.mock import patch

from pages.base_page import SimpleTooltip, hide_scroll_chrome, relay_vertical_scroll


class ScrollChromeTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('400x240+20+20')
        self.errors = []
        self.root.report_callback_exception = lambda kind, value, tb: self.errors.append(value)
        self.label = ttk.Label(self.root, text='target')
        self.label.pack()
        self.scrollbar = ttk.Scrollbar(self.root)
        self.scrollbar.pack()
        self.root.update()
        self.tooltip = SimpleTooltip(self.label, 'tooltip text')

    def tearDown(self):
        self.tooltip._hide()
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def test_scroll_during_creation_cancels_pending_tooltip(self):
        self.root.after_idle(lambda: relay_vertical_scroll(self.scrollbar, None, '0.1', '0.6'))
        self.tooltip._show(None)
        self.root.update()
        self.assertIsNone(self.tooltip.tip)

    def test_creation_does_not_run_nested_idle_callbacks(self):
        ran = []
        self.root.after_idle(lambda: ran.append(True))
        self.tooltip._show(None)
        self.assertEqual(ran, [])
        self.root.update()

    def test_hide_and_destroy_cancel_pending_position(self):
        self.tooltip._show(None)
        self.tooltip._hide()
        self.label.destroy()
        self.root.update()
        self.assertIsNone(self.tooltip.tip)
        self.assertNotIn(self.tooltip, SimpleTooltip._open)

    def test_visible_tip_is_positioned_and_scroll_hides_it(self):
        self.tooltip._show(None)
        self.root.update()
        tip = self.tooltip.tip
        self.assertIsNotNone(tip)
        self.assertTrue(tip.winfo_ismapped())
        self.assertGreater(tip.winfo_width(), 1)
        hide_scroll_chrome()
        self.root.update()
        self.assertIsNone(self.tooltip.tip)

    def test_unchanged_scroll_position_does_not_clear_hover(self):
        with patch('pages.base_page.hide_scroll_chrome') as hide:
            relay_vertical_scroll(self.scrollbar, ('0.0', '0.5'), '0.0', '0.5')
            hide.assert_not_called()


class TitleFontLifetimeTests(unittest.TestCase):
    def test_root_destroy_releases_cached_title_font(self):
        from pages.base_page import _title_font
        root = tk.Tk()
        font = _title_font(root, ('Microsoft YaHei UI', 23, 'bold'))
        reference = weakref.ref(font)
        del font
        root.destroy()
        gc.collect()
        self.assertIsNone(reference(), 'title cache retained a destroyed interpreter')


if __name__ == '__main__':
    unittest.main()
