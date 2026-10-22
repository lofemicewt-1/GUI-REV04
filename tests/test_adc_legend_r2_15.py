"""Offline rendering and adapter tests for Round-2 item 15."""
from __future__ import annotations

import tkinter as tk
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tkinter import font as tkfont
from pages import adc_legend as legend_module

from pages.adc_legend import AdcLegend
from pages.awp1018_page import AWP1018NormalModePage
from pages.awp1221_page import AWP1221ModePage
from pages.awp192x_modes import ButtonModePage
from styles.theme import get_colors


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class ConnectorFallbackTests(unittest.TestCase):
    def test_raster_failures_disable_pillow_for_all_legends(self):
        for operation in ("new", "Draw", "resize", "PhotoImage", "create_image"):
            for error in (ImportError, OSError, tk.TclError):
                with self.subTest(operation=operation, error=error):
                    image, draw, image_tk = Mock(), Mock(), Mock()
                    canvas = Mock()
                    operations = {
                        "new": image.new,
                        "Draw": draw.Draw,
                        "resize": image.new.return_value.resize,
                        "PhotoImage": image_tk.PhotoImage,
                        "create_image": canvas.create_image,
                    }
                    failing = operations[operation]
                    failing.side_effect = error("blocked raster backend")
                    with patch.multiple(legend_module, Image=image, ImageDraw=draw, ImageTk=image_tk):
                        legends = [SimpleNamespace(
                            canvas=canvas, colors={}, _ui_font=lambda: ("Arial", 10),
                            _connector_cache=None, _connector_photo=None,
                        ) for _ in range(2)]
                        for legend in legends:
                            for width in (100, 120):
                                AdcLegend._render_connectors(legend, width, 100, [([(0, 0), (1, 1)], "red")])
                                self.assertIsNone(legend._connector_photo)
                                self.assertIsNone(legend._connector_cache)
                        self.assertIsNone(legend_module.ImageTk)
                        failing.assert_called_once()
                        canvas.itemconfigure.assert_called_once_with("transition", state="normal")


class AdcLegendRenderingTests(unittest.TestCase):
    def test_rule_value_updates_reuse_cells_and_cached_measurements(self):
        root = self.make_root()
        legend = AdcLegend(root, self.translate, get_colors('light'))
        legend.pack(fill='x')
        stages = [dict(id='Stage0', condition='press', threshold=100, waveform='slice0', target='Stage1')]
        legend.set_rules(stages, {'Stage0': []})
        root.update()
        cells = tuple(legend._table.winfo_children())
        stages[0]['waveform'] = 'slice2'
        legend.set_rules(stages, {'Stage0': []})
        root.update()
        self.assertEqual(tuple(legend._table.winfo_children()), cells)
        self.assertEqual(legend._rule_rows[1][1].cget('text'), 'slice2')
        with patch.object(tkfont.Font, 'measure', side_effect=AssertionError('remeasured stable text')):
            legend._fit_rule_columns()

    def make_root(self):
        try:
            prepare_tk_test(self)
            root = tk.Tk()
            attach_tk_cleanup(self, root)
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.geometry("1100x720")
        return root

    @staticmethod
    def translate(key, **values):
        from config.languages import t

        return t("en", key, **values)

    def test_four_stage_rendering_routes_starts_and_unused_self_loop(self):
        root = self.make_root()
        legend = AdcLegend(root, self.translate, get_colors("light"), stage_count=4)
        legend.pack(fill="x", expand=True)
        legend.set_rules(
            [
                dict(id="Stage0", condition="press", comparator=">=", threshold="0x345", unit="ADC",
                     waveform="Normal vibration", target="Stage0"),
                dict(id="Stage1", condition="release", comparator="<=", threshold="32", unit="ADC",
                     waveform="slice18", target="Stage1", unused=True),
                dict(id="Stage2", condition="release", comparator="<=", threshold="-1", unit="V",
                     waveform="ROM sine", target="Stage0"),
                dict(id="Stage3", condition="press", comparator=">=", threshold="8", unit="ADC",
                     waveform="Strong vibration", target="ExternalStage"),
            ],
            {"Stage0": ["Group 1", "Group 2"], "Stage2": ["Group 3"]},
            preview_stage="Stage2",
        )
        root.update()

        self.assertEqual(len(legend.canvas.find_withtag("stage_box")), 4)
        # The active self-loop and backward jump are visible. The unused
        # self-target and an out-of-row target do not invent an arrow.
        self.assertEqual(len(legend.canvas.find_withtag("transition")), 2)
        self.assertIn("Group 1", legend.canvas.itemcget(legend.canvas.find_withtag("stage_marker")[0], "text"))
        unused_text = legend.canvas.itemcget(legend.canvas.find_withtag("stage_wait")[1], "text")
        self.assertIn("Unused", unused_text)
        self.assertIn("ExternalStage", " ".join(
            child.cget("text") for child in legend._table.winfo_children()
        ))
        self.assertNotIn("示意", legend._live_tip.cget("text"))
        self.assertNotIn("cannot determine", legend._live_tip.cget("text").lower())
        self.assertNotIn("illustrat", legend._live_tip.cget("text").lower())

        # A forward skip is rendered on a lower route just like a back jump.
        legend.set_rules(
            [
                dict(id=f"Stage{i}", condition="press", threshold=i, unit="ADC",
                     waveform="slice0", target={0: "Stage3", 1: "Stage1", 2: "Stage0", 3: "Stage3"}[i],
                     unused=(i == 1))
                for i in range(4)
            ],
            {"Stage0": ["Group 1"]},
        )
        root.update()
        self.assertEqual(len(legend.canvas.find_withtag("transition")), 3)

    def test_connector_image_cache_and_native_fallback(self):
        root = self.make_root()
        legend = AdcLegend(root, self.translate, get_colors("light"))
        legend.pack(fill="x")
        legend.set_rules([
            dict(id="Stage0", condition="press", threshold=1, target="Stage1"),
            dict(id="Stage1", condition="release", threshold=0, target="Stage0"),
        ], {})
        root.update()
        if legend_module.ImageTk is not None:
            self.assertEqual(len(legend.canvas.find_withtag("connector_image")), 1)
            photo = legend._connector_photo
            legend._render_diagram()
            self.assertIs(legend._connector_photo, photo)
            image_item = legend.canvas.find_withtag("connector_image")[0]
            self.assertEqual(legend.canvas.find_all()[0], image_item)
            self.assertTrue(all(legend.canvas.itemcget(item, "state") == "hidden"
                                for item in legend.canvas.find_withtag("transition")))
        with patch.object(legend_module, "ImageTk", None):
            legend._diagram_signature = None
            legend._render_locked_width = None
            legend._connector_cache = None
            legend._render_diagram()
            self.assertFalse(legend.canvas.find_withtag("connector_image"))
            for item in legend.canvas.find_withtag("transition"):
                self.assertNotEqual(legend.canvas.itemcget(item, "state"), "hidden")
                self.assertEqual(legend.canvas.itemcget(item, "smooth"), "true")
                self.assertGreaterEqual(int(legend.canvas.itemcget(item, "splinesteps")), 36)

        if legend_module.ImageTk is not None:
            legend._diagram_signature = None
            legend._render_locked_width = None
            legend._connector_cache = None
            legend._render_diagram()
            self.assertEqual(len(legend.canvas.find_withtag("connector_image")), 1)
            self.assertIsNotNone(legend._connector_photo)

    def test_blocked_imagetk_refresh_keeps_canvas_connectors_visible(self):
        root = self.make_root()
        backend = Mock()
        backend.PhotoImage.side_effect = ImportError("App Control blocked _imagingtk")
        with patch.object(legend_module, "ImageTk", backend), patch.object(root, "report_callback_exception") as errors:
            if legend_module.Image is None or legend_module.ImageDraw is None:
                self.skipTest("Pillow unavailable")
            legend = AdcLegend(root, self.translate, get_colors("light"))
            legend.pack(fill="x")
            for threshold in (1, 2, 3):
                legend.set_rules([
                    dict(id="Stage0", condition="press", threshold=threshold, target="Stage1"),
                    dict(id="Stage1", condition="release", threshold=0, target="Stage0"),
                ], {})
                root.update()
                lines = legend.canvas.find_withtag("transition")
                self.assertEqual(len(lines), 2)
                self.assertFalse(legend.canvas.find_withtag("connector_image"))
                for item in lines:
                    self.assertNotEqual(legend.canvas.itemcget(item, "state"), "hidden")
                    self.assertEqual(legend.canvas.itemcget(item, "smooth"), "true")
            backend.PhotoImage.assert_called_once()
            errors.assert_not_called()
            self.assertIsNone(legend_module.ImageTk)

    def test_measured_rule_columns_fit_at_requested_window_widths(self):
        from config.languages import t

        root = self.make_root()
        legend = AdcLegend(root, lambda key, **kw: t("zh", key, **kw), get_colors("light"))
        legend.pack(fill="x")
        legend.set_rules([
            dict(id="Stage0", condition="press", threshold=-128, unit="ADC · 高 / 12 位",
                 waveform="正常振动", target="Stage1"),
            dict(id="Stage1", condition="release", threshold=128, unit="ADC · 低 / 12 位",
                 waveform="强振动", target="Stage0"),
        ], {"Stage0": ["通道 1"]}, preview_stage="Stage0")
        for width in (1024, 1366, 1920):
            root.geometry(f"{width}x720")
            root.update()
            for cell in legend._table.winfo_children():
                column = int(cell.grid_info()["column"])
                font = tkfont.Font(root=root, font=cell.cget("font"))
                self.assertLessEqual(font.measure(cell.cget("text")) + 16,
                                     legend._column_widths[column])
                self.assertLessEqual(cell.winfo_x() + cell.winfo_width(), legend._table.winfo_width())

    def test_invalid_conditions_and_thresholds_render_as_unknown(self):
        root = self.make_root()
        legend = AdcLegend(root, self.translate, get_colors("dark"), stage_count=2)
        legend.pack(fill="x", expand=True)
        legend.set_rules(
            [
                dict(id="Stage0", condition="press", threshold="nan", unit="V", target="Stage1"),
                dict(id="Stage1", condition="release", threshold="", unit="V", target="Stage0"),
            ],
            {"Stage0": True},
        )
        root.update()
        self.assertEqual(
            [legend.canvas.itemcget(item, "text") for item in legend.canvas.find_withtag("stage_wait")],
            ["Condition unknown", "Condition unknown"],
        )
        self.assertEqual(len(legend.canvas.find_withtag("transition")), 2)
        self.assertTrue(all("Condition unknown" in legend.canvas.itemcget(item, "text")
                            for item in legend.canvas.find_withtag("transition_label")))


class RuleBindingTests(unittest.TestCase):
    def test_1221_binds_precision_wave_threshold_and_target(self):
        page = object.__new__(AWP1221ModePage)
        page.mode_key = "button_mode"
        page.tr = lambda key, **kwargs: key
        page.adc_legend = Mock()
        page._visible_button_stage_index = 1
        page._button_stage_vars = [
            {"event_mode": Value("press"), "threshold": Value("-128"),
             "use_high_12_bits": Value(True), "jump_target": Value("Stage1"),
             "wave": Value("serial1")},
            {"event_mode": Value("release"), "threshold": Value("not-a-number"),
             "use_high_12_bits": Value(False), "jump_target": Value("Stage0"),
             "wave": Value("slice18")},
        ]
        AWP1221ModePage._refresh_button_adc_legend(page)
        stages, starts = page.adc_legend.set_rules.call_args.args
        preview = page.adc_legend.set_rules.call_args.kwargs["preview_stage"]
        self.assertEqual(stages[0]["threshold"], -128)
        self.assertEqual(stages[0]["unit"], "")
        self.assertEqual(stages[0]["adc_precision"], "adc_legend_precision_high")
        self.assertEqual(stages[0]["waveform"], "adc_legend_wave_normal")
        self.assertEqual(stages[0]["target"], "Stage1")
        self.assertEqual(stages[1]["condition"], "unknown")
        self.assertEqual(stages[1]["unit"], "")
        self.assertEqual(stages[1]["adc_precision"], "adc_legend_precision_low")
        self.assertEqual(starts, {"Stage0": []})
        self.assertEqual(preview, "Stage1")

    def test_192x_binding_keeps_voltage_and_restored_waveform_identifier(self):
        page = object.__new__(ButtonModePage)
        page.page_name = "button_mode"
        page.tr = lambda key, **kwargs: key
        page.adc_legend = Mock()
        page.press_threshold_var = Value("3.125")
        page.release_threshold_var = Value("inf")
        page.press_waveform_var = Value("slice0")
        page.release_waveform_var = Value("slice18")
        ButtonModePage._refresh_button_adc_legend_rules(page)
        stages, starts = page.adc_legend.set_rules.call_args.args[:2]
        self.assertEqual(stages[0]["threshold"], "3.125")
        self.assertEqual(stages[0]["unit"], "V")
        self.assertEqual(stages[0]["waveform"], "adc_legend_wave_custom")
        self.assertEqual(stages[1]["condition"], "unknown")
        self.assertEqual(stages[1]["waveform"], "slice18")
        self.assertEqual(starts, {"Stage0": []})

    def test_1018_uses_condition_threshold_target_reachability_and_all_starts(self):
        page = object.__new__(AWP1018NormalModePage)
        page.tr = lambda key, **kwargs: key.format(**kwargs) if kwargs else key
        page.adc_legend = Mock()
        page.active_stage = Value("Stage2")
        page.channel_vars = [Value(True), Value(True), Value(False), Value(True)]
        page.channel_start_stage_vars = [Value("Stage0"), Value("Stage0"), Value("Stage1"), Value("Stage2")]
        page.stage_vars = {
            "Stage0": {"jump_condition": Value("press"), "jump_target": Value("Stage0"),
                       "high": Value("0x345"), "low": Value("100"), "waveform": Value("serial3")},
            "Stage1": {"jump_condition": Value("release"), "jump_target": Value("Stage1"),
                       "high": Value("200"), "low": Value("201"), "waveform": Value("restored-wave")},
            "Stage2": {"jump_condition": Value("release"), "jump_target": Value("Stage3"),
                       "high": Value("300"), "low": Value("301"), "waveform": Value("0x1F")},
            "Stage3": {"jump_condition": Value("press"), "jump_target": Value("ExternalStage"),
                       "high": Value("bad"), "low": Value("400"), "waveform": Value("slice0")},
        }
        AWP1018NormalModePage._refresh_adc_legend_rules(page)
        stages, starts = page.adc_legend.set_rules.call_args.args
        preview = page.adc_legend.set_rules.call_args.kwargs["preview_stage"]
        self.assertEqual(stages[0]["threshold"], 0x345)
        self.assertEqual(stages[0]["unit"], "ADC")
        self.assertFalse(stages[0]["unused"], "reachable self-loop stays configured")
        self.assertTrue(stages[1]["unused"], "unreachable self-loop is marked unused")
        self.assertEqual(stages[1]["waveform"], "restored-wave")
        self.assertEqual(stages[2]["threshold"], 301)
        self.assertEqual(stages[2]["waveform"], "adc_legend_wave_rom_sine")
        self.assertEqual(stages[3]["condition"], "unknown")
        self.assertEqual(stages[3]["target"], "ExternalStage")
        self.assertEqual(starts["Stage0"], ["adc_legend_start_group".format(group="awp1018_channel_label")]*2)
        self.assertEqual(starts["Stage2"], ["adc_legend_start_group".format(group="awp1018_channel_label")])
        self.assertEqual(preview, "Stage2")


class FakeEventAdapterTests(unittest.TestCase):
    def test_192x_event_pulse_restores_only_its_generation(self):
        page = object.__new__(ButtonModePage)
        page.STATUS_PRESS_COLOR = ButtonModePage.STATUS_PRESS_COLOR
        page.STATUS_TIMEOUT_COLOR = ButtonModePage.STATUS_TIMEOUT_COLOR
        page._sense_live_state = "idle"
        page._sense_live_generation = 0
        page._sense_status_active = True
        page._sense_timeout_mode = False
        page._sense_blink_job = None
        page.adc_legend = Mock()
        page.after_calls = []
        page.after = lambda delay, callback, *args: page.after_calls.append((delay, callback, args)) or "job"
        page.after_cancel = Mock()
        page._blink_sense_status(page.STATUS_PRESS_COLOR)
        delay, restore, args = page.after_calls[-1]
        self.assertEqual(delay, 180)
        self.assertEqual(page._sense_live_state, "press")
        ButtonModePage._set_sense_status_color(page, page.STATUS_TIMEOUT_COLOR)
        restore(*args)
        self.assertEqual(page._sense_live_state, "error")

    def test_192x_mode_hide_invalidates_late_event_callback(self):
        page = object.__new__(ButtonModePage)
        page.STATUS_PRESS_COLOR = ButtonModePage.STATUS_PRESS_COLOR
        page._sense_live_state = "idle"
        page._sense_live_generation = 0
        page._sense_status_active = True
        page._sense_timeout_mode = False
        page._sense_blink_job = None
        page.adc_legend = Mock()
        page.after_calls = []
        page.after = lambda delay, callback, *args: page.after_calls.append((delay, callback, args)) or "job"
        page.after_cancel = Mock()
        ButtonModePage._refresh_sense_legend = ButtonModePage._refresh_sense_legend.__get__(page)
        ButtonModePage._set_sense_status_color = ButtonModePage._set_sense_status_color.__get__(page)
        ButtonModePage._blink_sense_status = ButtonModePage._blink_sense_status.__get__(page)
        page._blink_sense_status(page.STATUS_PRESS_COLOR)
        ButtonModePage.on_hide(page)
        self.assertEqual(page._sense_live_state, "detecting")
        page.after_cancel.assert_called_once_with("job")
        _delay, restore, args = page.after_calls[-1]
        restore(*args)
        self.assertEqual(page._sense_live_state, "detecting")

    def test_1018_shared_pr_has_no_channel_and_adc_samples_do_not_imply_detection(self):
        page = SimpleNamespace(
            diff_mode=True,
            STATUS_PRESS_COLOR=AWP1018NormalModePage.STATUS_PRESS_COLOR,
            STATUS_TIMEOUT_COLOR=AWP1018NormalModePage.STATUS_TIMEOUT_COLOR,
            STATUS_READY_COLOR=AWP1018NormalModePage.STATUS_READY_COLOR,
            STATUS_IDLE_COLOR=AWP1018NormalModePage.STATUS_IDLE_COLOR,
            colors={"panel": "#FFFFFF", "border": "#D1D5DB", "text": "#111827"},
            tr=lambda key, **kwargs: key.format(**kwargs) if kwargs else key,
            adc_legend=Mock(),
            _diff_detect_active=True,
            _diff_channel_live_errors=set(),
            _diff_global_live_error=False,
            _diff_status_frames={},
            _diff_status_blink_job=None,
            _diff_legend_generation=0,
            after_calls=[],
            after_cancel=Mock(),
        )
        page.after = lambda delay, callback, *args: page.after_calls.append((delay, callback, args)) or "job"
        page._set_adc_legend_live = AWP1018NormalModePage._set_adc_legend_live.__get__(page)
        page._refresh_diff_legend_live = AWP1018NormalModePage._refresh_diff_legend_live.__get__(page)
        page._set_diff_channel_color = AWP1018NormalModePage._set_diff_channel_color.__get__(page)
        page._blink_diff_channel_status = AWP1018NormalModePage._blink_diff_channel_status.__get__(page)
        page._restore_diff_ready_after_blink = AWP1018NormalModePage._restore_diff_ready_after_blink.__get__(page)

        AWP1018NormalModePage._blink_diff_channel_status(page, page.STATUS_PRESS_COLOR)
        delay, restore, args = page.after_calls[-1]
        self.assertEqual(delay, 180)
        state, detail = page.adc_legend.set_live.call_args.args[:2]
        self.assertEqual(state, "press")
        self.assertEqual(detail, "adc_legend_live_shared_event")

        AWP1018NormalModePage._set_diff_channel_color(page, 2, page.STATUS_TIMEOUT_COLOR)
        restore(*args)
        latest = page.adc_legend.set_live.call_args
        self.assertEqual(latest.args[0], "error")
        self.assertEqual(set(latest.kwargs["channel_states"]), {"awp1018_channel_label"})

        page._diff_detect_active = False
        page._diff_channel_live_errors.clear()
        page.adc_legend.set_live.reset_mock()
        AWP1018NormalModePage._set_diff_channel_color(page, 0, page.STATUS_READY_COLOR)
        self.assertEqual(page.adc_legend.set_live.call_args.args[0], "idle")


if __name__ == "__main__":
    unittest.main()
