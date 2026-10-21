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
