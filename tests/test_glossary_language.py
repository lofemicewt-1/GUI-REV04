from __future__ import annotations

import tkinter as tk
import unittest
from tkinter import ttk

from config.languages import LANGUAGES, t
from pages.base_page import attach_glossary_tooltips


GLOSSARY_KEYS = (
    "glossary_section_title",
    "glossary_intro",
    "glossary_en",
    "glossary_adc",
    "glossary_sinc",
    "glossary_sine_sin",
    "glossary_rom",
    "glossary_sram",
    "glossary_vpk",
    "glossary_vpp",
    "glossary_ovv_adc",
    "glossary_debounce",
    "glossary_slice",
    "glossary_serial",
    "glossary_stage",
    "glossary_out_polarity",
    "glossary_nint",
    "glossary_eeprom",
    "glossary_type4",
    "glossary_gpio",
    "glossary_auto_serial",
)


class GlossaryLanguageTests(unittest.TestCase):
    def test_glossary_keys_exist_in_chinese_and_english(self) -> None:
        for language in ("zh", "en"):
            with self.subTest(language=language):
                self.assertTrue(set(GLOSSARY_KEYS).issubset(LANGUAGES[language]))
                self.assertNotIn("glossary_ovv_status", LANGUAGES[language])
                for key in GLOSSARY_KEYS:
                    self.assertTrue(LANGUAGES[language][key], key)

    def test_matching_labels_receive_live_multiterm_tooltips(self) -> None:
        root = tk.Tk()
        self.addCleanup(root.destroy)
        label = ttk.Label(root, text="ADC OVV Time")
        label.pack()
        tips = []

        attach_glossary_tooltips(root, lambda key: t("en", key), tips)
        tooltip = label._piezo_glossary_tooltip
        help_text = tooltip.text_getter()
        self.assertIn("ADC:", help_text)
        self.assertIn("OVV (ADC):", help_text)

        label.configure(text="OVV status bit")
        self.assertEqual(tooltip.text_getter(), "")
        self.assertEqual(len(tips), 1)


if __name__ == "__main__":
    unittest.main()
