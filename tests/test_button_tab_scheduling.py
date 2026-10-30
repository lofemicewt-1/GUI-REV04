from __future__ import annotations

import tkinter as tk
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import animation as anim
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
from tests.validate_responsive_layout import mocked_application


class ButtonTabSchedulingTests(unittest.TestCase):
    def setUp(self):
        prepare_tk_test(self)
        fixture = mocked_application()
        self.app = fixture.__enter__()
        self.root = self.app.root
        attach_tk_cleanup(self, self.root)
        self.addCleanup(fixture.__exit__, None, None, None)
        self.root.geometry("1700x980")
        self.app.navigation.set_developer_visibility_override(True)
        self.root.deiconify()
        self.pump()

    def pump(self, ms=100):
        self.root.after(ms, self.root.quit)
        self.root.mainloop()

    def wait_for(self, predicate):
        deadline = time.perf_counter() + 2.0
        while not predicate() and time.perf_counter() < deadline:
            self.pump(25)
        self.assertTrue(predicate(), "widget did not finish its Map/Configure work")

    def page(self, key):
        self.app.set_animations(False)
        self.app.navigation.show(key)
        self.pump(250)
        host = self.app.navigation.pages[key]
        self.wait_for(lambda: host.winfo_viewable() and not self.app.transitions.active)
        host.show_mode("full_mode")
        self.wait_for(lambda: host.mode_pages["full_mode"].winfo_viewable())
        host.show_mode("button_mode")
        self.wait_for(lambda: host.mode_pages["button_mode"].winfo_viewable())
        self.pump()
        self.app.set_animations(True)
        if key == "awp1221":
            self.wait_for(lambda: host.mode_pages["button_mode"]._button_wave_selection_motion._indicator.winfo_viewable())
        return host, host.mode_pages["button_mode"]

    def test_1221_tab_does_not_apply_hidden_full_view(self):
        host, button = self.page("awp1221")
        full = host.mode_pages["full_mode"]
        with patch.object(full, "_apply_settings_model_to_view", wraps=full._apply_settings_model_to_view) as apply:
            button._button_wave_tabs["sinc"].invoke()
            self.pump()
            self.assertEqual(apply.call_count, 0)
        host.show_mode("full_mode")
        self.pump()
        self.assertEqual(full.rom_waveform_var.get(), "SINC")

    def test_detail_configure_queued_before_destroy_is_safe(self):
        host, _ = self.page("awp1921_1923")
        canvas = host.detail_fields_canvas
        self.assertIsNotNone(canvas)
        canvas.destroy()
        host._sync_detail_fields_canvas_width(SimpleNamespace(width=400))
        host._sync_detail_fields_scrollregion()

    def test_1923_hidden_preview_waits_for_map(self):
        host, button = self.page("awp1921_1923")
        wave = button.waveform_page
        host.show_mode("full_mode")
        self.pump()
        with patch("pages.awp192x_button_view.draw_timed_waveform") as draw:
            wave.voltage_amplitude_var.set("17")
            wave._button_plot_drawn = None
            wave._queue_plot_redraw()
            wave.waveform_type_var.set("serial")
            self.pump()
            self.assertEqual(draw.call_count, 0)
        host.show_mode("button_mode")
        self.wait_for(lambda: wave._button_plot_drawn is not None)

    def test_1923_source_batch_keeps_sine_timing_and_manual_output(self):
        _, button = self.page("awp1921_1923")
        wave = button.waveform_page
        wave.source_buttons["sinc"].invoke()
        button.duration_var.set("37")
        manual = (button.max_voltage_var.get(), button.duration_var.get())
        wave.source_buttons["ram"].invoke()
        plan = wave.get_sine_plan()
        self.assertEqual(int(button.duration_var.get()), plan.point_us)
        wave.source_buttons["sinc"].invoke()
        self.assertEqual((button.max_voltage_var.get(), button.duration_var.get()), manual)

    def test_tools_hover_reuses_fitted_font_and_stable_canvas(self):
        host, _ = self.page("awp1221")
        trap = host.trapezoid
        # Explicitly show the chrome so this checks real font fitting.
        host.show_step_chrome(host.mode_pages["button_mode"])
        self.wait_for(trap.button.winfo_ismapped)
        trap._draw_button()
        with patch.object(trap._button_label_font, "configure", wraps=trap._button_label_font.configure) as configure:
            trap._button_hover(True)
            trap._button_hover(False)
            self.assertEqual(configure.call_count, 0)
        items = trap.button.find_all()
        trap._draw_button()
        self.assertEqual(trap.button.find_all(), items)

    def test_progress_stable_draw_uses_cached_metrics(self):
        _, button = self.page("awp1221")
        bar = button.step_progress
        bar._draw_strip()
        with patch.object(bar._font, "metrics", wraps=bar._font.metrics) as metrics, \
             patch.object(bar._font, "actual", wraps=bar._font.actual) as actual, \
             patch.object(bar.canvas, "itemconfigure", wraps=bar.canvas.itemconfigure) as paint, \
             patch.object(bar.canvas, "coords", wraps=bar.canvas.coords) as coords, \
             patch.object(bar.canvas, "move", wraps=bar.canvas.move) as move:
            bar._draw_strip()
            self.assertEqual(metrics.call_count, 0)
            self.assertEqual(actual.call_count, 0)
            self.assertEqual(paint.call_count, 0)
            self.assertEqual(coords.call_count, 0)
            self.assertEqual(move.call_count, 0)

    def assert_background_at_tab(self, page, key):
        button = page._button_wave_tabs[key]
        self.wait_for(lambda: page._button_wave_selection_motion._indicator.winfo_x() == button.winfo_x()
                      and page._button_wave_selection_motion._indicator.winfo_width() == button.winfo_width())
        self.assertEqual(page._button_wave_selection_motion._indicator.winfo_x(), button.winfo_x())
        self.assertEqual(page._button_wave_selection_motion._indicator.winfo_width(), button.winfo_width())

    def test_1221_tab_background_uses_snap_and_redirects_from_visible_position(self):
        _, page = self.page("awp1221")
        page._button_wave_tabs["rom"].invoke()
        self.pump(250)
        animator = anim.animator_for(page)
        animator.set_enabled(True)
        start = page._button_wave_selection_motion._motion._rect
        with patch.object(animator, "animate", wraps=animator.animate) as animate:
            page._button_wave_tabs["sinc"].invoke()
            self.wait_for(lambda: animator.is_running(page._button_wave_selection_motion._motion._key))
            calls = [call for call in animate.call_args_list if call.args[0] == page._button_wave_selection_motion._motion._key]
        self.assertTrue(calls)
        self.assertTrue(all(call.args[1] == anim.DUR_SNAP for call in calls))
        self.assertTrue(all(call.kwargs["ease"] is anim.EASE_SNAP for call in calls))
        self.assertNotEqual(page._button_wave_selection_motion._motion._rect, page._button_wave_selection_motion._motion._target_rect)
        animator._tweens[page._button_wave_selection_motion._motion._key].step(0.5)
        middle = page._button_wave_selection_motion._motion._rect
        self.assertNotEqual(middle, start)
        page._button_wave_tabs["sram"].invoke()
        self.assertEqual(page._button_wave_selection_motion._motion._rect, middle)
        self.wait_for(lambda: page._button_wave_selection_motion._motion._job is None)
        animator.cancel(page._button_wave_selection_motion._motion._key, finish=True)
        self.pump(250)
        self.wait_for(lambda: page._button_wave_selection_motion._indicator.winfo_x() == page._button_wave_tabs["sram"].winfo_x())
        self.assert_background_at_tab(page, "sram")
        self.assertEqual(page.rom_source_var.get(), "RAM")

    def test_1221_tab_background_repeat_disable_hide_and_destroy(self):
        host, page = self.page("awp1221")
        animator = anim.animator_for(page)
        animator.set_enabled(False)
        page._button_wave_tabs["sinc"].invoke()
        self.pump()
        self.assert_background_at_tab(page, "sinc")
        animator.set_enabled(True)
        page._button_wave_tabs["rom"].invoke()
        self.wait_for(lambda: animator.is_running(page._button_wave_selection_motion._motion._key))
        tween = animator._tweens[page._button_wave_selection_motion._motion._key]
        page._button_wave_tabs["rom"].invoke()
        self.assertIs(animator._tweens[page._button_wave_selection_motion._motion._key], tween)
        animator.set_enabled(False)
        self.pump()
        self.assert_background_at_tab(page, "rom")
        animator.set_enabled(True)
        page._button_wave_tabs["sinc"].invoke()
        host.show_mode("full_mode")
        self.pump()
        self.assertFalse(animator.is_running(page._button_wave_selection_motion._motion._key))
        host.show_mode("button_mode")
        self.pump(250)
        self.assert_background_at_tab(page, "sinc")
        page._button_wave_tabs["rom"].invoke()
        page.destroy()
        self.assertFalse(animator.is_running(page._button_wave_selection_motion._motion._key))

    def test_1221_source_changes_render_intermediate_background_positions(self):
        _, page = self.page("awp1221")
        self.wait_for(lambda: not self.app.transitions.active)
        for key in ("sinc", "rom", "sram", "sinc", "sram", "rom"):
            with self.subTest(source=key):
                start = page._button_wave_selection_motion._indicator.winfo_x()
                samples = []
                binding = page._button_wave_selection_motion._indicator.bind(
                    "<Configure>", lambda event: samples.append(event.x), add="+")
                try:
                    page._button_wave_tabs[key].invoke()
                    self.wait_for(lambda: page._button_wave_selection_motion._motion._job is None
                                  and not self.app.animator.is_running(page._button_wave_selection_motion._motion._key)
                                  and not page._has_pending_layout_work()
                                  and page._button_wave_selection_motion._indicator.winfo_x() == page._button_wave_tabs[key].winfo_x()
                                  and page._button_wave_selection_motion._indicator.winfo_width() == page._button_wave_tabs[key].winfo_width())
                    self.pump(50)  # Deliver the final native Configure event.
                finally:
                    page._button_wave_selection_motion._indicator.unbind("<Configure>", binding)
                end = page._button_wave_selection_motion._indicator.winfo_x()
                self.assertTrue(any(min(start, end) < x < max(start, end) for x in samples),
                                f"{key} jumped from {start} to {end}: {samples}")
                self.assert_background_at_tab(page, key)

    def test_1221_busy_first_frame_does_not_skip_visible_motion(self):
        _, page = self.page("awp1221")
        animator = self.app.animator
        start = page._button_wave_selection_motion._motion._rect
        page._button_wave_tabs["sinc"].invoke()
        self.root.update_idletasks()
        tween = animator._tweens[page._button_wave_selection_motion._motion._key]
        tween.start -= 1  # Reproduce a first timer dispatched after its entire budget.
        animator._cancel_timer()
        animator._tick()
        self.assertTrue(animator.is_running(page._button_wave_selection_motion._motion._key))
        self.assertEqual(page._button_wave_selection_motion._motion._rect, start)
        self.pump(300)
        self.wait_for(lambda: not page._button_wave_selection_motion._motion.busy)
        self.assert_background_at_tab(page, "sinc")

    def test_1221_disabling_motion_finishes_a_queued_background(self):
        _, page = self.page("awp1221")
        page._button_wave_tabs["sram"].invoke()
        self.assertIsNotNone(page._button_wave_selection_motion._motion._job)
        self.app.set_animations(False)
        self.wait_for(lambda: page._button_wave_selection_motion._motion._job is None)
        self.pump(250)
        self.assertFalse(self.app.animator.is_running(page._button_wave_selection_motion._motion._key))
        self.assert_background_at_tab(page, "sram")

    def test_1221_tab_background_tracks_resize_language_and_theme(self):
        _, page = self.page("awp1221")
        for width, language, theme in ((1024, "en", "dark"), (1700, "zh", "light")):
            with self.subTest(width=width, language=language, theme=theme):
                self.root.geometry(f"{width}x800")
                self.app.set_language(language)
                self.app.set_theme(theme)
                page._button_wave_tabs["rom"].invoke()
                self.pump(250)
                self.assert_background_at_tab(page, "rom")
                track = page._button_wave_selection_motion._indicator.master
                self.assertGreaterEqual(page._button_wave_selection_motion._indicator.winfo_x(), 0)
                self.assertLessEqual(page._button_wave_selection_motion._indicator.winfo_x()
                                     + page._button_wave_selection_motion._indicator.winfo_width(), track.winfo_width())

    def test_1221_sram_visibility_does_not_drain_idle_or_restyle(self):
        _, button = self.page("awp1221")
        with patch.object(tk.Misc, "update_idletasks", side_effect=AssertionError("nested idle drain")):
            button._button_wave_tabs["sram"].invoke()
            self.pump()
        editor = button.sram_editor
        with patch.object(editor, "refresh_theme") as theme, patch.object(editor, "refresh_language") as language:
            button._sync_sram_editor_visibility()
            self.assertEqual(theme.call_count, 0)
            self.assertEqual(language.call_count, 0)
        button._button_wave_tabs["rom"].invoke()
        self.pump()
        self.assertIsNone(getattr(button, "_sram_geometry_job", None))

    def test_hidden_adc_table_does_not_measure_or_poll(self):
        host, button = self.page("awp1221")
        legend = button.adc_legend
        host.show_mode("full_mode")
        self.pump()
        with patch("pages.adc_legend.tkfont.Font", side_effect=AssertionError("hidden font measure")), \
             patch.object(legend, "after", wraps=legend.after) as after:
            legend._rule_text_widths.clear()
            legend._rule_fonts.clear()
            legend._fit_rule_columns()
            legend._render_diagram()
            self.assertEqual(after.call_count, 0)
        host.show_mode("button_mode")
        self.wait_for(lambda: bool(legend._rule_text_widths))
        self.assertTrue(legend._table.winfo_ismapped())


if __name__ == "__main__":
    unittest.main()
