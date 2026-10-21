from __future__ import annotations

from types import SimpleNamespace
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
import unittest

from app.application import PiezoApplication
from pages.base_page import BasePage, hide_scroll_chrome
from styles.theme import apply_theme
from tests.tk_cleanup import attach_tk_cleanup, cancel_after_jobs, prepare_tk_test


class ModuleTitleTests(unittest.TestCase):
    def setUp(self) -> None:
        prepare_tk_test(self)
        self.root = tk.Tk()
        attach_tk_cleanup(self, self.root)
        self.root.withdraw()
        self.page = None
        self.addCleanup(self._destroy_page)

    def _destroy_page(self) -> None:
        cancel_after_jobs(self.root)
        page = self.page
        if page is not None:
            try:
                page.destroy()
            except tk.TclError:
                pass
        self.page = None

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

    def test_step_and_role_title_refreshes_after_language_change(self) -> None:
        colors = apply_theme(self.root, "light", 10, 1.0)
        context = SimpleNamespace(
            colors=colors,
            state=SimpleNamespace(language="en", theme="light"),
        )
        self.page = BasePage(self.root, context)
        self.page.pack(fill="both", expand=True)
        _row, title = self.page.make_module_title_row(
            self.page,
            "awp1221_adc_diagram",
            step=3,
            step_total=4,
            role="verify",
        )
        self.assertEqual(title.cget("text"), "Step 3 verification · ADC Diagram")

        context.state.language = "zh"
        self.page.refresh_language()
        self.assertEqual(title.cget("text"), "第3步验证 · ADC 图例")

    def test_step_metadata_comes_from_both_title_entry_points_without_duplicates(self) -> None:
        colors = apply_theme(self.root, "light", 10, 1.0)
        context = SimpleNamespace(
            colors=colors,
            state=SimpleNamespace(language="en", theme="light"),
        )
        self.page = BasePage(self.root, context)
        self.page.pack(fill="both", expand=True)

        direct, _label = self.page.make_module_title_row(
            self.page,
            "awp1221_chip_en_title",
            step=1,
            step_total=4,
            help_key="awp1221_help_chip_en",
        )
        section, _body = self.page.make_module_section(
            self.page,
            "awp1221_button_wave_settings",
            row=1,
            column=0,
            step=2,
            step_total=4,
            help_key="awp1221_help_waveform",
        )
        self.page.make_module_title_row(
            self.page,
            "awp1221_adc_diagram",
            step=1,
            step_total=4,
            role="reference",
        )

        self.assertEqual(len(self.page._module_step_metadata), 3)
        self.assertIs(self.page._module_step_metadata[0]["target"], direct)
        self.assertIs(self.page._module_step_metadata[1]["target"], section)
        self.assertEqual(self.page._module_step_metadata[1]["ordinal"], 2)

    def test_module_help_hover_pin_escape_and_destroy(self) -> None:
        colors = apply_theme(self.root, "light", 10, 1.0)
        context = SimpleNamespace(
            colors=colors,
            state=SimpleNamespace(language="en", theme="light"),
        )
        self.page = BasePage(self.root, context)
        self.page.pack(fill="both", expand=True)
        self.root.geometry("500x300+20+20")
        self.root.deiconify()
        section, _body = self.page.make_module_section(
            self.page,
            "awp1221_chip_en_title",
            row=0,
            column=0,
            sticky="ew",
            step=1,
            step_total=4,
            help_key="awp1221_help_chip_en",
        )
        self.root.update()

        controller = section._module_title_row._module_help_controller
        button = section._module_title_row._module_help_button
        self.assertEqual(button.cget("text"), "?")
        self.assertTrue(button.cget("takefocus"))

        controller.tooltip._show(None)
        self.root.update()
        self.assertIsNotNone(controller.tooltip.tip)
        tooltip_label = controller.tooltip.tip.winfo_children()[0]
        context_help = self.page.tr("awp1221_help_chip_en")
        self.assertEqual(tooltip_label.cget("text"), context_help)

        button.invoke()
        self.root.update()
        self.assertIs(controller, controller.__class__._active)
        self.assertIsNotNone(controller.window)
        self.assertEqual(controller.window._module_help_body.cget("text"), context_help)
        button.event_generate("<Leave>")
        self.root.update()
        self.assertIsNotNone(controller.window)

        # Headless Tk often leaves focus unset after Leave; force focus so the
        # button/window Escape binding can dismiss the pinned panel.
        button.focus_force()
        self.root.update()
        button.event_generate("<KeyPress-Escape>")
        self.root.update()
        self.assertIsNone(controller.__class__._active)
        self.assertIsNone(controller.window)

        button.invoke()
        self.root.update()
        hide_scroll_chrome()
        self.root.update()
        self.assertIsNone(controller.__class__._active)
        self.assertIsNone(controller.window)

        button.invoke()
        self.root.update()
        button.destroy()
        self.root.update()
        self.assertIsNone(controller.__class__._active)
        self.assertIsNone(controller.window)

