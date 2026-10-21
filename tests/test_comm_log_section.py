from __future__ import annotations

from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import font as tkfont, ttk
import unittest
from unittest.mock import patch

from config.languages import t
from tests.validate_responsive_layout import _flush, _widget_descendants, mocked_application


class CommunicationLogTranslationTests(unittest.TestCase):
    def test_shared_and_legacy_titles_have_one_consistent_name(self):
        for language, expected in (("zh", "通讯日志"), ("en", "Communication Log")):
            with self.subTest(language=language):
                self.assertEqual(t(language, "serial_log"), expected)
                self.assertEqual(t(language, "awp1221_communication_log"), expected)


class CommunicationLogSectionTests(unittest.TestCase):
    CASES = (
        ("awp1221", "button_mode"),
        ("awp1018", "awp1018_normal_mode"), ("awp1018", "awp1018_diff_mode"),
        ("awp1921_1923", "button_mode"),
        ("awp1921_1923", "pump_mode"),
        ("awp1922", None),
    )

    @classmethod
    def setUpClass(cls):
        cls.context = mocked_application()
        cls.app = cls.context.__enter__()
        cls.root = cls.app.root
        cls.app.set_animations(False)
        cls.app.set_language("zh")
        cls.root.geometry("1024x608+0+0")

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def setUp(self):
        self.app.set_language("zh")
        self.root.geometry("1024x608+0+0")

    def page(self, chip, mode=None):
        self.app.navigation.show(chip)
        host = self.app.navigation.pages[chip]
        if mode:
            host.show_mode(mode)
        _flush(self.root, 5)
        return host.mode_pages[mode] if mode else host

    def test_single_unwrapped_header_and_both_scrollbars_on_all_chip_modes(self):
        for chip, mode in self.CASES:
            with self.subTest(chip=chip, mode=mode):
                page = self.page(chip, mode)
                if not page._serial_log_body.winfo_manager():
                    page._serial_log_toggle.invoke()
                _flush(self.root, 5)
                self.assertEqual(self.root.winfo_width(), 1024)
                titles = [w for w in _widget_descendants(page)
                          if isinstance(w, (ttk.Label, ttk.Button))
                          and str(w.cget("text")) in ("通讯日志", "通信日志", "通讯日志 ▾")]
                self.assertEqual(titles, [page._serial_log_title])
                title = titles[0]
                self.assertEqual(str(title.cget("text")), "通讯日志")
                self.assertEqual(int(title.cget("wraplength")), 0)
                self.assertGreaterEqual(title.winfo_width(), title.winfo_reqwidth())
                self.assertEqual(title.winfo_height(), title.winfo_reqheight())
                header = page._serial_log_header
                self.assertIs(title.master, header)
                self.assertIs(page._serial_log_toggle.master, header)
                self.assertEqual(str(page._serial_log_toggle.cget("text")), "▾")
                buttons = [w for w in header.winfo_children() if isinstance(w, ttk.Button)]
                self.assertEqual([str(w.cget("text")) for w in buttons], ["?", "▾", "清除", "导出"])
                self.assertEqual(len({w.grid_info()["row"] for w in header.winfo_children()}), 1)
                self.assertLess(title.winfo_rootx() + title.winfo_width(), buttons[0].winfo_rootx())
                self.assertEqual(buttons[-1].winfo_x() + buttons[-1].winfo_width(), header.winfo_width())
                text = page._serial_log_text
                self.assertEqual(str(text.cget("wrap")), "none")
                self.assertTrue(tkfont.Font(root=self.root, font=text.cget("font")).metrics("fixed"))
                scrollbars = [w for w in text.master.winfo_children() if isinstance(w, ttk.Scrollbar)]
                self.assertEqual({str(w.cget("orient")) for w in scrollbars}, {"horizontal", "vertical"})
                self.assertTrue(all(w.winfo_ismapped() for w in scrollbars))

    def test_expanded_log_stays_eight_lines_when_window_shrinks_and_grows(self):
        for chip, mode in self.CASES:
            page = self.page(chip, mode)
            if not page._serial_log_body.winfo_manager():
                page._serial_log_toggle.invoke()
            page.clear_serial_log_view()
            for index in range(12):
                page.append_serial_log(f"local UI demo line {index + 1}")
            for width, height in ((1366, 768), (1024, 608), (1366, 768)):
                with self.subTest(chip=chip, mode=mode, size=(width, height)):
                    self.root.geometry(f"{width}x{height}+0+0")
                    self.app._apply_visual_settings()
                    _flush(self.root, 5)
                    self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (width, height))
                    text = page._serial_log_text
                    self.assertTrue(page._serial_log_body.winfo_ismapped())
                    self.assertEqual(int(text.cget("height")), 8)
                    # Check the allocated height too: grid must not stretch or
                    # squeeze an eight-line Text to match the viewport.
                    self.assertEqual(text.winfo_height(), text.winfo_reqheight())
                    self.assertLess(text.yview()[1] - text.yview()[0], 1)
                    text.yview_moveto(0)
                    self.assertEqual(text.index("@0,0"), "1.0")
                    text.yview_moveto(1)
                    self.assertGreater(text.yview()[0], 0)

    def test_full_mode_keeps_expanded_baseline_log(self):
        for chip, height in (("awp1221", 7), ("awp1018", 14), ("awp1921_1923", 18)):
            with self.subTest(chip=chip):
                page = self.page(chip, "full_mode")
                text = page._serial_log_text
                self.assertTrue(text.winfo_ismapped())
                self.assertEqual(int(text.cget("height")), height)
                self.assertEqual(page._serial_log_frame.cget("style"), "Panel.TFrame")
                self.assertIsNone(page._serial_log_toggle)
                self.assertEqual(page._serial_log_title.cget("style"), "SectionTitle.TLabel")
                scrollbars = [w for w in text.master.winfo_children() if isinstance(w, ttk.Scrollbar)]
                self.assertEqual([str(w.cget("orient")) for w in scrollbars], ["horizontal"])
                page.clear_serial_log_view()
                page.append_serial_log("full mode local log")
                self.assertIn("full mode local log", text.get("1.0", "end"))

    def test_collapse_preserves_header_and_content_and_updates_chevron(self):
        for chip, mode in (("awp1221", "button_mode"), ("awp1018", "awp1018_normal_mode"),
                           ("awp1921_1923", "button_mode")):
            with self.subTest(chip=chip):
                page = self.page(chip, mode)
                if not page._serial_log_body.winfo_manager():
                    page._serial_log_toggle.invoke()
                page.clear_serial_log_view()
                page.append_serial_log("local UI demo " + "0123456789 " * 80)
                _flush(self.root)
                page._serial_log_toggle.invoke()
                _flush(self.root)
                self.assertFalse(page._serial_log_body.winfo_manager())
                self.assertTrue(page._serial_log_header.winfo_ismapped())
                self.assertEqual(str(page._serial_log_toggle.cget("text")), "▸")
                page._serial_log_toggle.invoke()
                _flush(self.root)
                self.assertTrue(page._serial_log_body.winfo_ismapped())
                text = page._serial_log_text
                text.xview_moveto(0)
                self.assertLess(text.xview()[1], 1)
                text.xview_moveto(1)
                self.assertGreater(text.xview()[0], 0)
                self.assertIn("local UI demo", text.get("1.0", "end"))

    def test_translation_and_visual_refresh_keep_title_and_fixed_font(self):
        page = self.page("awp1221", "button_mode")
        for language, expected in (("en", "Communication Log"), ("zh", "通讯日志")):
            self.app.set_language(language)
            self.app._apply_visual_settings()
            _flush(self.root)
            self.assertEqual(str(page._serial_log_title.cget("text")), expected)
            self.assertEqual(int(page._serial_log_title.cget("wraplength")), 0)
            self.assertGreaterEqual(page._serial_log_title.winfo_width(), page._serial_log_title.winfo_reqwidth())
            self.assertTrue(tkfont.Font(root=self.root, font=page._serial_log_text.cget("font")).metrics("fixed"))

    def test_header_clear_and_export_use_existing_actions(self):
        page = self.page("awp1221", "button_mode")
        page.clear_serial_log_view()
        page.append_serial_log("local export demo")
        buttons = {str(w.cget("text")): w for w in page._serial_log_header.winfo_children()
                   if isinstance(w, ttk.Button)}
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "log.txt"
            with patch("pages.base_page.filedialog.asksaveasfilename", return_value=str(destination)):
                buttons["导出"].invoke()
            self.assertIn("local export demo", destination.read_text(encoding="utf-8"))
        with patch.object(page.ctx.serial, "clear_log") as clear:
            buttons["清除"].invoke()
        clear.assert_called_once_with()
        self.assertEqual(page._serial_log_text.get("1.0", "end-1c"), "")


if __name__ == "__main__":
    unittest.main()
