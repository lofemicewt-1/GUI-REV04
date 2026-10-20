from __future__ import annotations

from types import SimpleNamespace
import gc
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
import unittest

from app.application import PiezoApplication
from pages.base_page import BasePage
from styles.theme import apply_theme


class ModuleTitleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.page = None

        def _cleanup() -> None:
            page = getattr(self, "page", None)
            if page is not None:
                try:
                    page.destroy()
                except tk.TclError:
                    pass
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            self.page = None
            self.root = None
            gc.collect()

        self.addCleanup(_cleanup)

    def test_theme_exposes_bold_module_and_section_titles(self) -> None:
        style = ttk.Style(self.root)
        sizes: list[int] = []
        for theme in ("light", "dark"):
            colors = apply_theme(self.root, theme, 10, 1.0)
            body_size = self.root._piezo_body_font_size
            title_size = self.root._piezo_module_title_font_size
            sizes.append(title_size)

            self.assertGreaterEqual(title_size, body_size + 1)
            for style_name, expected_size in (
                ("ModuleTitle.TLabel", title_size),
                ("SectionTitle.TLabel", body_size),
            ):
                font = tkfont.Font(root=self.root, font=style.lookup(style_name, "font"))
                actual = font.actual()
                self.assertEqual(int(actual["size"]), expected_size)
                self.assertEqual(actual["weight"], "bold")
                self.assertEqual(style.lookup(style_name, "foreground"), colors["text"])

        self.assertEqual(sizes[0], sizes[1])

    def test_font_refresh_preserves_module_title_size(self) -> None:
        apply_theme(self.root, "light", 10, 1.0)
        label = ttk.Label(self.root, text="Waveform settings", style="ModuleTitle.TLabel")
        label.pack()

        app = PiezoApplication.__new__(PiezoApplication)
        app.root = self.root
        app.state = SimpleNamespace(font_size=10)
        app._refresh_widget_fonts()

        font = tkfont.Font(root=self.root, font=label.cget("font"))
        self.assertEqual(int(font.actual()["size"]), self.root._piezo_module_title_font_size)
        self.assertEqual(font.actual()["weight"], "bold")

    def test_module_section_has_module_title_and_content_body(self) -> None:
        colors = apply_theme(self.root, "dark", 10, 1.0)
        context = SimpleNamespace(
            colors=colors,
            state=SimpleNamespace(language="en", theme="dark"),
        )
        self.page = BasePage(self.root, context)
        page = self.page
        page.pack(fill="both", expand=True)

        section, body = page.make_module_section(
            page,
            "awp1221_button_wave_settings",
            row=0,
            column=0,
            sticky="ew",
        )
        self.root.update_idletasks()

        title = section._module_title_label
        self.assertEqual(section.cget("style"), "Module.TFrame")
        self.assertEqual(title.cget("style"), "ModuleTitle.TLabel")
        self.assertEqual(title.cget("text"), "Waveform Settings")
        self.assertIs(body.master, section)
        self.assertEqual(body.cget("style"), "Panel.TFrame")
        self.assertGreaterEqual(section._module_help_slot.winfo_reqwidth(), 26)

