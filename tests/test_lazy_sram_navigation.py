from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.validate_responsive_layout import mocked_application
from tests.test_motion import pump
from pages.awp1221_sram_editor import AWP1221SramEditor


class LazySramNavigationTests(unittest.TestCase):
    def test_default_rom_and_broadcasts_do_not_construct_hidden_ram_editor(self):
        with mocked_application() as app:
            app.set_animations(False)
            with patch("pages.awp1221_page.AWP1221SramEditor", wraps=AWP1221SramEditor) as factory:
                app.navigation.show("awp1221")
                page = app.navigation.pages["awp1221"].mode_pages["button_mode"]
                pump(app.root, .2)
                self.assertEqual(factory.call_count, 0, "default ROM cold visit constructed hidden SRAM controls")
                app.apply_settings("en", "dark", 12)
                app.root.geometry("1200x800")
                page._select_button_wave_tab("sinc")
                page._select_button_wave_tab("rom")
                pump(app.root, .2)
                self.assertEqual(factory.call_count, 0, "theme/language/resize or non-RAM tabs eagerly built SRAM")
                page._select_button_wave_tab("sram")
                pump(app.root, .2)
                self.assertEqual(factory.call_count, 1)
                editor = page.sram_editor
                self.assertTrue(editor.winfo_ismapped())
                self.assertEqual(editor.language, "en")
                self.assertEqual(editor.colors, app.colors)
                editor.sram_values_var.set("0, 50, -20, 0")
                editor._apply_sram_values()
                saved = (editor.sram_values_var.get(), tuple(editor._sram_controls), editor._sram_result)
                page._select_button_wave_tab("rom")
                app.apply_settings("zh", "light", 10)
                page._select_button_wave_tab("sram")
                pump(app.root, .2)
                self.assertIs(page.sram_editor, editor)
                self.assertEqual(factory.call_count, 1)
                self.assertEqual((editor.sram_values_var.get(), tuple(editor._sram_controls), editor._sram_result), saved)
                self.assertEqual(editor.language, "zh")

    def test_explicit_editor_access_is_compatible_and_stays_hidden_in_rom(self):
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show("awp1221")
            page = app.navigation.pages["awp1221"].mode_pages["button_mode"]
            editor = page.sram_editor
            self.assertIsInstance(editor, AWP1221SramEditor)
            self.assertEqual(editor.grid_info(), {})
            self.assertIs(page.sram_editor, editor)


if __name__ == "__main__":
    unittest.main()
