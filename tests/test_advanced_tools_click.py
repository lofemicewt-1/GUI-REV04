"""Offline click and visibility regressions for the floating advanced tools."""
from __future__ import annotations

from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.ui_state_store import UiStateStore
from pages.base_page import BasePage
from styles.theme import apply_theme
from tests.test_motion import pump
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
from tests.validate_responsive_layout import mocked_application, _widget_descendants


def click_advanced(trap, root):
    button = trap.button
    x, y = button.winfo_width() // 2, button.winfo_height() // 2
    button.event_generate("<ButtonPress-1>", x=x, y=y)
    button.event_generate("<ButtonRelease-1>", x=x, y=y)
    pump(root, 0.08)


def exposed_controls(root, parent):
    """Mapped alone is insufficient: clipped children are also 'mapped'."""
    visible = []
    for widget in _widget_descendants(parent):
        if not isinstance(widget, (ttk.Button, ttk.Entry, ttk.Combobox)):
            continue
        if not widget.winfo_ismapped() or widget.winfo_width() <= 1:
            continue
        x = widget.winfo_rootx() + widget.winfo_width() // 2
        y = widget.winfo_rooty() + widget.winfo_height() // 2
        if root.winfo_containing(x, y) is widget:
            visible.append(widget)
    return visible


class AdvancedToolsChipClickTests(unittest.TestCase):
    def test_button_modes_click_open_visible_controls_close_and_full_inline(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "pages.step_progress.UiStateStore",
            side_effect=lambda: UiStateStore(Path(directory) / "ui_state.json"),
        ), mocked_application() as app:
            app.set_animations(False)
            app.root.geometry("1366x768+0+0")
            for route, mode in (
                ("awp1221", "button_mode"),
                ("awp1018", "awp1018_normal_mode"),
                ("awp1921_1923", "button_mode"),
            ):
                with self.subTest(chip=route):
                    app.navigation.show(route)
                    pump(app.root, 0.08)
                    host = app.navigation.pages[route]
                    host.show_mode(mode)
                    pump(app.root, 0.08)
                    trap = host.trapezoid
                    trap.set_expanded(False)
                    pump(app.root, 0.04)
                    self.assertTrue(trap.button.winfo_ismapped())
                    click_advanced(trap, app.root)
                    self.assertTrue(trap.expanded)
                    self.assertTrue(trap._panel_outer.winfo_ismapped())
                    self.assertGreater(trap._panel_outer.winfo_height(), 20)
                    self.assertTrue(exposed_controls(app.root, trap.actions))
                    self.assertTrue(exposed_controls(app.root, trap.registers))

                    click_advanced(trap, app.root)
                    self.assertFalse(trap.expanded)
                    self.assertFalse(trap._panel_outer.winfo_ismapped())

                    # Switch while open: Full Mode has an inline footer and
                    # must not leave a floating popup or dead pill visible.
                    click_advanced(trap, app.root)
                    host.show_mode("full_mode")
                    pump(app.root, 0.08)
                    self.assertTrue(host._full_mode_footer.active)
                    self.assertFalse(trap.button.winfo_ismapped())
                    self.assertFalse(trap._panel_outer.winfo_ismapped())
                    host.show_mode(mode)
                    pump(app.root, 0.08)
                    self.assertTrue(trap.expanded)
                    self.assertTrue(exposed_controls(app.root, trap.registers))
                    trap.set_expanded(False)


class AdvancedToolsViewportTests(unittest.TestCase):
    def setUp(self):
        prepare_tk_test(self)
        self.root = tk.Tk()
        attach_tk_cleanup(self, self.root)
        self.root.geometry("520x480+0+0")
        ctx = SimpleNamespace(
            colors=apply_theme(self.root, "light", 10, 1.0),
            state=SimpleNamespace(language="en", theme="light"),
            serial=SimpleNamespace(),
        )
        self.host = BasePage(self.root, ctx)
        self.host.pack(fill="both", expand=True)
        area, _ = self.host.make_vertical_scroll_area(self.host)
        area.pack(fill="both", expand=True)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch("pages.step_progress.UiStateStore", return_value=UiStateStore(
            Path(directory.name) / "ui_state.json"
        )):
            self.trap = self.host.mount_trapezoid(
                lambda frame: ttk.Button(frame, text="Shutdown").pack(),
                lambda bar: ttk.Entry(bar.add_group()).pack(),
                state_key="click_regression",
            )
        pump(self.root, 0.05)

    def test_explicit_click_survives_forced_and_responsive_transitions(self):
        trap = self.trap
        self.assertTrue(trap._forced_collapsed)
        self.assertFalse(trap.expanded)
        click_advanced(trap, self.root)
        self.assertTrue(trap.expanded)
        self.assertTrue(exposed_controls(self.root, trap.actions))
        self.assertTrue(exposed_controls(self.root, trap.registers))
        for geometry in ("1600x900", "520x480"):
            self.root.geometry(geometry + "+0+0")
            pump(self.root, 0.05)
            self.assertTrue(trap.expanded)
        click_advanced(trap, self.root)
        self.assertFalse(trap.expanded)

    def test_only_unusable_viewport_temporarily_hides_explicit_expansion(self):
        trap = self.trap
        click_advanced(trap, self.root)
        self.assertTrue(trap.expanded)
        with patch.object(trap.canvas, "winfo_width", return_value=220):
            trap._on_viewport_configure()
            self.assertFalse(trap.expanded)
            self.assertTrue(trap._desired_expanded)
        trap._on_viewport_configure()
        pump(self.root, 0.05)
        self.assertTrue(trap.expanded)
        self.assertTrue(exposed_controls(self.root, trap.registers))


if __name__ == "__main__":
    unittest.main()
