from __future__ import annotations
from unittest import mock

import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from pages.adc_legend import AdcLegend
from pages.awp1018_page import AWP1018NormalModePage
from pages.awp1221_page import AWP1221ModePage
from pages.awp192x_button_view import ButtonModeView
from styles.theme import get_colors
from tests.tk_cleanup import attach_tk_cleanup, cancel_after_jobs, prepare_tk_test
from tests.validate_responsive_layout import mocked_application


class AdcLegendClickTests(unittest.TestCase):
    @staticmethod
    def translate(key, **values):
        from config.languages import t

        return t("en", key, **values)

    def make_root(self):
        try:
            prepare_tk_test(self)
            root = tk.Tk()
            attach_tk_cleanup(self, root)
            self.addCleanup(cancel_after_jobs, root)
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.geometry("1000x700")
        return root

    def make_legend(self, root, callback=None):
        legend = AdcLegend(
            root,
            self.translate,
            get_colors("light"),
            stage_count=2,
            on_stage_activate=callback,
        )
        legend.pack(fill="x", expand=True)
        legend.set_rules(
            [
                {"id": "Stage0", "condition": "press", "threshold": "1", "unit": "V", "target": "Stage1"},
                {"id": "Stage1", "condition": "release", "threshold": "0", "unit": "V", "target": "Stage0"},
            ],
            {"Stage0": ["Channel 1"]},
        )
        root.update()
        return legend

    def make_app(self):
        try:
            prepare_tk_test(self)
            app_context = mocked_application()
            app = app_context.__enter__()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        attach_tk_cleanup(self, app.root, app)
        self.addCleanup(cancel_after_jobs, app.root)
        self.addCleanup(app_context.__exit__, None, None, None)
        app.set_animations(False)
        return app

    def test_activate_stage_dispatches_normalized_ids_and_disposed_is_noop(self):
        root = self.make_root()
        activated = []
        legend = self.make_legend(root, activated.append)

        legend.activate_stage(1)
        legend.activate_stage(" 1 ")
        legend.activate_stage("Stage0")
        self.assertEqual(activated, ["Stage1", "Stage1", "Stage0"])

        legend.dispose()
        legend.activate_stage(1)
        self.assertEqual(activated, ["Stage1", "Stage1", "Stage0"])

    def test_canvas_stage_card_click_dispatches_its_stage(self):
        root = self.make_root()
        activated = []
        legend = self.make_legend(root, activated.append)
        item = legend.canvas.find_withtag("stage_box")[1]
        x0, y0, x1, y1 = legend.canvas.bbox(item)
        x, y = (x0 + x1) // 2, (y0 + y1) // 2

        legend.canvas.event_generate("<Motion>", x=x, y=y)
        legend.canvas.event_generate("<Button-1>", x=x, y=y)
        root.update()

        self.assertEqual(activated, ["Stage1"])

    def test_whole_rule_table_row_clicks_and_highlights_only_that_row(self):
        root = self.make_root()
        activated = []
        legend = self.make_legend(root, activated.append)
        row = legend._rule_rows[1]
        other = legend._rule_rows[2]
        background = row[0].cget("background")
        for cell in row:
            cell.event_generate("<Enter>", x=2, y=2)
            root.update_idletasks()
            root.update()
            self.assertTrue(all(str(item.cget("cursor")) == "hand2" for item in row))
            self.assertTrue(all(item.cget("background") != background for item in row))
            # Exclusive hover: only the active row stays tinted.
            self.assertTrue(all(item.cget("background") == background for item in other))
            cell.event_generate("<Button-1>", x=2, y=2)
            root.update_idletasks()
            root.update()
            cell.event_generate("<Leave>", x=2, y=2)
            root.update_idletasks()
            root.update()
            self.assertTrue(all(str(item.cget("cursor")) == "" for item in row))
            self.assertTrue(all(item.cget("background") == background for item in row))
        self.assertEqual(activated, ["Stage0"] * 4)

    def assert_adc_target_is_legend(self, page, key):
        # Resolve through the same metadata path used by scroll_to_step.
        progress = page.step_progress
        metadata = progress._module_targets(page)
        ordinal = progress._data[key].spec.ordinal
        self.assertIs(metadata[ordinal]["target"], page.adc_legend.canvas)

    def test_canvas_stage_hover_uses_hand_cursor(self):
        root = self.make_root()
        legend = self.make_legend(root, lambda _stage: None)
        item = legend.canvas.find_withtag("stage_box")[0]
        x0, y0, x1, y1 = legend.canvas.bbox(item)

        legend.canvas.event_generate("<Motion>", x=(x0 + x1) // 2, y=(y0 + y1) // 2)
        root.update()

        self.assertEqual(legend.canvas.cget("cursor"), "hand2")
        legend.canvas.event_generate("<Motion>", x=1, y=1)
        root.update()
        self.assertEqual(legend.canvas.cget("cursor"), "")

    def test_no_callback_and_callback_setter_are_safe(self):
        root = self.make_root()
        legend = self.make_legend(root)
        legend.activate_stage(0)

        activated = []
        legend.set_stage_activate(activated.append)
        legend.activate_stage(0)
        self.assertEqual(activated, ["Stage0"])

    def test_awp1221_legend_callback_selects_built_button_stage(self):
        page = object.__new__(AWP1221ModePage)
        page._button_stage_frames = {0: object(), 1: object()}
        page._button_stage_vars = [{}, {}]
        page._show_button_stage = Mock()
        page.step_progress = SimpleNamespace(scroll_to_step=Mock())

        page._activate_button_stage_from_legend("Stage1")
        page._activate_button_stage_from_legend("Stage2")

        page._show_button_stage.assert_called_once_with(1)
        page.step_progress.scroll_to_step.assert_not_called()

    def test_awp1018_legend_callback_selects_built_stage(self):
        page = object.__new__(AWP1018NormalModePage)
        page._stage_frames = {"Stage0": object(), "Stage1": object()}
        page._show_stage = Mock()
        page.step_progress = SimpleNamespace(scroll_to_step=Mock())

        page._activate_stage_from_legend("Stage1")
        page._activate_stage_from_legend("Stage4")

        page._show_stage.assert_called_once_with("Stage1")
        page.step_progress.scroll_to_step.assert_not_called()

    def test_awp192x_legend_callback_activates_press_or_release_settings(self):
        page = object.__new__(ButtonModeView)
        page._button_adc_stage_fields = {"Stage0": Mock(), "Stage1": Mock()}
        page.step_progress = SimpleNamespace(scroll_to_step=Mock())

        page._activate_button_adc_stage("Stage1")
        self.assertEqual(page._active_adc_stage_id, "Stage1")
        page._button_adc_stage_fields["Stage1"].focus_set.assert_called_once_with()
        page.step_progress.scroll_to_step.assert_not_called()

        page._activate_button_adc_stage("Stage2")
        self.assertEqual(page._active_adc_stage_id, "Stage1")

    def test_page_legend_text_fits_cards_canvas_and_rule_cells(self):
        import os
        from pathlib import Path
        from tkinter import font as tkfont
        from pages.base_page import VerticalScrollArea
        from tests.validate_responsive_layout import _flush

        app = self.make_app()
        app.apply_settings("zh", "light", app.state.font_size)
        for route, mode in (
            ("awp1221", "button_mode"),
            ("awp1018", "awp1018_normal_mode"),
            ("awp1921_1923", "button_mode"),
        ):
            app.navigation.show(route)
            host = app.navigation.pages[route]
            host.show_mode(mode, **({"update_chip": False} if route == "awp1018" else {}))
            page = host.mode_pages[mode]
            legend = page.adc_legend
            for width in (1024, 1366, 1920):
                with self.subTest(route=route, width=width):
                    app.root.geometry(f"{width}x768+0+0")
                    _flush(app.root, 5)
                    canvas = legend.canvas
                    for box in canvas.find_withtag("stage_box"):
                        stage = next(tag for tag in canvas.gettags(box) if tag.startswith("Stage"))
                        x0, y0, x1, y1 = canvas.coords(box)
                        for item in canvas.find_withtag(stage):
                            if canvas.type(item) != "text":
                                continue
                            left, top, right, bottom = canvas.bbox(item)
                            self.assertGreaterEqual(left, x0 + 4)
                            self.assertGreaterEqual(top, y0 + 4)
                            self.assertLessEqual(right, x1 - 4)
                            self.assertLessEqual(bottom, y1 - 4)
                    for item in canvas.find_all():
                        if canvas.type(item) == "text":
                            left, top, right, bottom = canvas.bbox(item)
                            self.assertGreaterEqual(left, 4)
                            self.assertGreaterEqual(top, 4)
                            self.assertLessEqual(right, canvas.winfo_width() - 4)
                            self.assertLessEqual(bottom, canvas.winfo_height() - 4)
                    for row in legend._rule_rows.values():
                        for cell in row:
                            wrap = int(cell.cget("wraplength"))
                            font = tkfont.Font(root=app.root, font=cell.cget("font"))
                            # Tk wraps at words/characters. The configured wrap
                            # and any individual glyph must fit the actual cell.
                            self.assertLessEqual(wrap + 16, cell.winfo_width())
                            self.assertLessEqual(max(map(font.measure, cell.cget("text"))), wrap)
                            self.assertLessEqual(cell.winfo_x() + cell.winfo_width(), legend._table.winfo_width())

                    directory = os.environ.get("PREVIEW_SCREENSHOT_DIR")
                    if directory:
                        from PIL import ImageGrab

                        # Bring the entire legend into view for visual review;
                        # activation itself must continue to leave scroll alone.
                        focus = legend
                        current = focus.master
                        while current is not None:
                            if isinstance(current, VerticalScrollArea):
                                y = current.canvas.canvasy(focus.winfo_rooty() - current.canvas.winfo_rooty())
                                region = current.canvas.tk.splitlist(current.canvas.cget("scrollregion"))
                                height = float(region[3]) if len(region) == 4 else 1.0
                                current.canvas.yview_moveto(max(0, (y - 8) / max(1, height)))
                                focus = current
                            current = current.master
                        _flush(app.root, 5)
                        root = app.root
                        path = Path(directory) / f"after_adc_legend_clipfix_{route}_{width}x768.png"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                                             root.winfo_rootx() + root.winfo_width(),
                                             root.winfo_rooty() + root.winfo_height()),
                                       xdisplay=os.environ.get("DISPLAY")).save(path)

    def test_awp1221_page_legend_switches_visible_stage(self):
        app = self.make_app()
        app.navigation.show("awp1221")
        host = app.navigation.pages["awp1221"]
        host.show_mode("button_mode")
        page = host.mode_pages["button_mode"]
        app.root.update()

        self.assert_adc_target_is_legend(page, "adc")
        self.assertTrue(callable(page.adc_legend._on_stage_activate))
        page.adc_legend.activate_stage("Stage1")
        app.root.update_idletasks()
        self.assertEqual(page._visible_button_stage_index, 1)
        self.assertEqual(page._button_stage_frames[1].winfo_manager(), "grid")

    def test_awp1018_page_legend_switches_active_stage(self):
        app = self.make_app()
        app.navigation.show("awp1018")
        host = app.navigation.pages["awp1018"]
        page = host.mode_pages["awp1018_normal_mode"]
        app.root.update()

        self.assert_adc_target_is_legend(page, "adc")
        self.assertTrue(callable(page.adc_legend._on_stage_activate))
        page.adc_legend.activate_stage("Stage1")
        app.root.update_idletasks()
        self.assertEqual(page.active_stage.get(), "Stage1")
        self.assertEqual(page._stage_frames["Stage1"].winfo_manager(), "grid")

    def test_awp192x_page_legend_activates_stage_settings(self):
        app = self.make_app()
        app.navigation.show("awp1921_1923")
        host = app.navigation.pages["awp1921_1923"]
        host.show_mode("button_mode")
        page = host.mode_pages["button_mode"]
        app.root.update()

        self.assert_adc_target_is_legend(page, "config")
        self.assertTrue(callable(page.adc_legend._on_stage_activate))
        field = page._button_adc_stage_fields["Stage1"]
        with mock.patch.object(field, "focus_set") as focus_set:
            page.adc_legend.activate_stage("Stage1")
            app.root.update_idletasks()
        self.assertEqual(page._active_adc_stage_id, "Stage1")
        focus_set.assert_called()


if __name__ == "__main__":
    unittest.main()
