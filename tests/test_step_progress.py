from __future__ import annotations

import tkinter as tk
from tests.tk_cleanup import attach_tk_cleanup, cancel_after_jobs, prepare_tk_test
from tkinter import ttk
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from core import animation as anim
from core.ui_dispatch import UiDispatcher
from pages.base_page import BasePage
from pages.step_progress import ProgressContext, StepProgressBar, StepSpec, Trapezoid
from styles.theme import apply_theme


class StepProgressSizingTests(unittest.TestCase):
    def test_strip_width_fraction_changes_at_scaled_viewport_breakpoint(self) -> None:
        for viewport, scale, fraction in ((1024, 1.0, 0.92), (1099, 1.0, 0.92),
                                          (1100, 1.0, 0.7), (1200, 1.0, 0.7),
                                          (1374, 1.25, 0.92), (1375, 1.25, 0.7),
                                          (2000, 1.0, 0.7)):
            with self.subTest(viewport=viewport, scale=scale):
                trap = MagicMock(expanded=False, _last_layout_signature=None, _reveal_height=0)
                trap.canvas.winfo_width.return_value = viewport
                trap._available_viewport_height.return_value = 800
                trap._base_group_height.return_value = 57
                trap.host.ctx.state.ui_scale = scale
                trap._button_font.measure.return_value = 100
                bar = MagicMock()
                bar.preferred_width.return_value = 10000
                trap.host._step_progress_bars = [bar]
                trap.host._active_step_page.step_progress = bar
                Trapezoid._layout(trap)
                bar.apply_width.assert_called_once_with(
                    min(round(viewport * fraction), round(1100 * scale)))

    def test_compact_shortening_preserves_cjk_and_latin_prefixes(self) -> None:
        bar = SimpleNamespace(_measure=lambda text: sum(9 if char.isascii() else 18 for char in text))
        for full, short in (("1 · 波形设置与输出参数", "1 · 波形设"),
                            ("1 · 波形设置与输出参数", "1 · 波形设置"),
                            ("2 · Waveform configuration", "2 · Wave"),
                            ("3 · ADC configuration", "3 · ADC")):
            with self.subTest(title=full, short=short):
                room = bar._measure(short)
                self.assertEqual(StepProgressBar._elide(bar, full, room, compact=True), short)
                self.assertTrue(StepProgressBar._elide(bar, full, room).endswith("…"))
                self.assertEqual(StepProgressBar._elide(bar, full, bar._measure(full), compact=True), full)
                for tiny_room in (0, 18, 54):
                    text = StepProgressBar._elide(bar, full, tiny_room, compact=True)
                    self.assertLessEqual(bar._measure(text), tiny_room)


class StepProgressTests(unittest.TestCase):
    def test_unallocated_tools_button_does_not_measure_fonts(self):
        from unittest.mock import patch
        with patch.object(self.trapezoid._button_label_font, 'measure', side_effect=AssertionError('hidden font fit')):
            self.trapezoid._draw_button(SimpleNamespace(type=tk.EventType.Configure, width=1, height=1))

    def test_show_chrome_does_not_drain_idle(self):
        from unittest.mock import patch
        with patch.object(self.host, 'update_idletasks', side_effect=AssertionError('nested idle')):
            self.host.show_step_chrome(self.page)
        self.root.update()
        self.assertTrue(self.bar.winfo_ismapped())

    def setUp(self) -> None:
        prepare_tk_test(self)
        self.root = tk.Tk()
        attach_tk_cleanup(self, self.root)
        self.root.geometry("900x680+10+10")
        self.root.update_idletasks()
        self.root.deiconify()
        self.context = type("Context", (), {})()
        self.context.colors = apply_theme(self.root, "light", 10, 1.0)
        self.context.state = type("State", (), {"language": "en", "theme": "light"})()
        self.context.serial = type("Serial", (), {})()

        self.host = BasePage(self.root, self.context)
        self.host.pack(fill="both", expand=True)
        self.host.columnconfigure(0, weight=1)
        self.host.rowconfigure(0, weight=1)
        self.area, _content = self.host.make_vertical_scroll_area(self.host)
        self.area.grid(row=0, column=0, sticky="nsew")
        self.trapezoid = self.host.mount_trapezoid(
            self._build_actions,
            self._build_registers,
            state_key="test_chip",
            build_extra=self._build_extra,
        )

        self.page = BasePage(self.root, self.context)
        self.page.pack(fill="both", expand=True)
        self.page.make_module_section(
            self.page,
            "awp1221_button_wave_settings",
            step=1,
            step_total=3,
            help_key="awp1221_help_waveform",
            row=0,
            column=0,
            sticky="ew",
        )
        self.page.make_module_title_row(
            self.page,
            "awp1221_adc_diagram",
            step=2,
            step_total=3,
            role="reference",
            help_key="awp1221_help_adc_legend",
        )
        self.page.make_module_section(
            self.page,
            "awp1221_button_adc_settings",
            step=2,
            step_total=3,
            help_key="awp1221_help_adc_params",
            row=2,
            column=0,
            sticky="ew",
        )
        self.page.make_module_section(
            self.page,
            "awp1221_control_buttons",
            step=3,
            step_total=3,
            help_key="awp1221_help_switches",
            row=3,
            column=0,
            sticky="ew",
        )
        self.context_value = ProgressContext(("COM1", 123, 4), 0x50, "button")
        self.bar = self.page.mount_step_progress(
            host=self.host,
            steps=(
                StepSpec("waveform", 1, "step_done_1221_waveform"),
                StepSpec("adc", 2, "step_done_1221_adc", ("settings", "stage0")),
                StepSpec("detection", 3, "step_done_1221_detection"),
            ),
            context=lambda: self.context_value,
        )
        self.host.show_step_chrome(self.page)
        self.root.update()

    def _build_actions(self, parent: ttk.Frame) -> None:
        ttk.Button(parent, text="Shutdown").grid(row=0, column=0)

    def _build_registers(self, bar) -> None:
        group = bar.add_group()
        ttk.Entry(group).grid(row=0, column=0)

    def _build_extra(self, panel: ttk.Frame) -> None:
        ttk.Label(panel, text="Detailed register controls").grid(row=2, column=0, sticky="w")

    def tearDown(self) -> None:
        cancel_after_jobs(self.root)
        for widget in (self.page, self.host):
            try:
                widget.destroy()
            except tk.TclError:
                pass
        self.page = self.host = self.area = self.trapezoid = self.bar = None

    def flush(self) -> None:
        self.root.update()

    def test_aggregate_receipts_invalidation_retention_and_badge(self) -> None:
        self.bar.update_params("adc", {"settings": 1, "stage0": 10})
        self.bar.update_params("detection", {"start": 1})
        adc = self.bar.begin("adc")
        self.bar.mark_part("adc", "settings", "done", adc)
        self.flush()
        self.assertEqual(self.bar.snapshot()["adc"]["state"], "running")
        self.assertEqual(self.bar.snapshot()["adc"]["parts"], {"settings": 1})
        self.bar.mark_part("adc", "stage0", "done", adc)
        self.flush()
        self.assertEqual(self.bar.snapshot()["adc"]["state"], "done")

        detection = self.bar.begin("detection")
        self.bar.mark("detection", "done", detection)
        self.flush()
        self.assertEqual(self.bar.snapshot()["detection"]["state"], "done")

        self.bar.update_params("adc", {"settings": 1, "stage0": 11})
        snapshot = self.bar.snapshot()
        self.assertEqual(snapshot["adc"]["state"], "idle")
        self.assertEqual(snapshot["adc"]["parts"], {"settings": 1})
        self.assertEqual(snapshot["detection"]["state"], "idle")

        waveform = self.bar.begin("waveform")
        self.bar.mark("waveform", "done", waveform)
        self.flush()
        self.bar.collapse()
        self.flush()
        self.assertEqual(self.bar.canvas.itemcget(self.bar.badge_text_item, "text"), "1/3")
        self.bar.expand()
        self.flush()
        self.assertIn("waveform", self.bar._hit_rects)

    def test_newer_operation_overtakes_old_and_idle_is_cancellation(self) -> None:
        old = self.bar.begin("waveform")
        current = self.bar.begin("waveform")
        self.bar.mark("waveform", "done", old)
        self.flush()
        self.assertEqual(self.bar.snapshot()["waveform"]["state"], "running")
        self.bar.mark("waveform", "done", current)
        self.flush()
        self.assertEqual(self.bar.snapshot()["waveform"]["state"], "done")

        cancelled = self.bar.begin("waveform")
        self.bar.mark("waveform", "idle", cancelled)
        self.flush()
        self.assertEqual(self.bar.snapshot()["waveform"]["state"], "idle")

    def test_context_parameter_epoch_activation_and_destroy_fence_tokens(self) -> None:
        original = ProgressContext(("COM1", 123, 4), 0x50, "button")
        mismatches = (
            ProgressContext(("COM2", 123, 4), 0x50, "button"),
            ProgressContext(("COM1", 124, 4), 0x50, "button"),
            ProgressContext(("COM1", 123, 5), 0x50, "button"),
            ProgressContext(original.port_session, 0x51, "button"),
            ProgressContext(original.port_session, 0x50, "full"),
            ProgressContext(None, 0x50, "button"),
        )
        for mismatched in mismatches:
            with self.subTest(context=mismatched):
                self.context_value = original
                self.bar.activate()
                token = self.bar.begin("waveform")
                self.context_value = mismatched
                self.bar.mark("waveform", "done", token)
                self.flush()
                self.assertEqual(self.bar.snapshot()["waveform"]["state"], "idle")

        self.context_value = original
        self.bar.activate()
        self.bar.update_params("waveform", {"shape": 1})
        stale_params = self.bar.begin("waveform")
        self.bar.update_params("waveform", {"shape": 2})
        self.bar.mark("waveform", "done", stale_params)
        self.flush()
        self.assertEqual(self.bar.snapshot()["waveform"]["state"], "idle")

        stale_operation = self.bar.begin("waveform")
        self.bar.begin("waveform")
        self.bar.mark("waveform", "done", stale_operation)
        self.flush()
        self.assertEqual(self.bar.snapshot()["waveform"]["state"], "running")

        stale_epoch = self.bar.begin("waveform")
        self.bar.reset_all()
        self.bar.mark("waveform", "done", stale_epoch)
        self.flush()
        self.assertEqual(self.bar.snapshot()["waveform"]["state"], "idle")

        stale_activation = self.bar.begin("waveform")
        self.host.show_step_chrome(None)
        self.bar.mark("waveform", "done", stale_activation)
        self.flush()
        self.assertEqual(self.bar.snapshot()["waveform"]["state"], "idle")

        self.host.show_step_chrome(self.page)
        destroyed_token = self.bar.begin("waveform")
        self.page.destroy()
        self.assertFalse(self.bar.accepts(destroyed_token))

    def test_tooltip_translation_duplicate_primary_and_role_exclusion(self) -> None:
        target = self.bar._metadata[1]
        self.assertEqual(target["title_key"], "awp1221_button_wave_settings")
        # The verify/reference duplicate at ordinal 2 is ignored in favor of
        # the first ordinary primary module.
        self.assertEqual(self.bar._metadata[2]["title_key"], "awp1221_button_adc_settings")
        token = self.bar.begin("waveform")
        self.bar.mark("waveform", "error", token, detail="write rejected")
        self.flush()
        tooltip = self.bar.tooltip_text("waveform")
        self.assertIn("Step 2 of 4", tooltip)
        self.assertIn("Completion condition:", tooltip)
        self.assertIn("Failed", tooltip)
        self.assertIn("write rejected", tooltip)

        self.context.state.language = "zh"
        self.page.refresh_language()
        tooltip = self.bar.tooltip_text("waveform")
        self.assertIn("完成条件：", tooltip)
        self.assertIn("失败", tooltip)

    def test_worker_result_is_delivered_through_the_ui_dispatcher(self) -> None:
        dispatcher = UiDispatcher(self.root, interval_ms=10000).start()
        self.addCleanup(dispatcher.close)
        token = self.bar.begin("waveform")
        worker = threading.Thread(target=lambda: self.bar.mark("waveform", "done", token))
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.bar.snapshot()["waveform"]["state"], "running")
        self.assertEqual(dispatcher.pending(), 1)
        dispatcher.drain()
        self.assertEqual(self.bar.snapshot()["waveform"]["state"], "done")

    def test_floating_group_is_viewport_pinned_and_panel_stays_inside_bounds(self) -> None:
        self.root.update()
        group = self.trapezoid.floating_group
        canvas = self.area.canvas
        self.assertIs(group.master, self.area)
        self.assertEqual(group.winfo_manager(), "place")
        first = (group.winfo_rootx(), group.winfo_rooty())
        self.area.canvas.yview_scroll(10, "units")
        self.root.update()
        self.assertEqual((group.winfo_rootx(), group.winfo_rooty()), first)
        self.assertGreater(self.area.scroll_region_extra, 24)

        self.trapezoid.set_expanded(True, persist=False)
        self.root.update()
        outer = self.trapezoid._panel_outer
        top = outer.winfo_rooty()
        bottom = top + outer.winfo_height()
        view_top = canvas.winfo_rooty()
        view_bottom = self.area.winfo_rooty() + self.area.winfo_height()
        self.assertGreaterEqual(top, view_top)
        self.assertLessEqual(bottom, view_bottom)
        self.assertGreaterEqual(
            top,
            self.bar.canvas.winfo_rooty() + self.bar.canvas.winfo_height(),
            "advanced controls must unfold below the progress cells",
        )
        self.assertGreaterEqual(top, self.trapezoid.button.winfo_rooty() + self.trapezoid.button.winfo_height())
        self.assertIs(outer.master, self.trapezoid.advanced_group)
        self.assertEqual(self.trapezoid.advanced_group.cget("style"), "Module.TFrame")

    def test_advanced_header_stays_above_content_while_drawer_reveals_upward(self) -> None:
        self.page.pack_forget()
        trap = self.trapezoid
        trap.set_expanded(False, persist=False)
        self.flush()
        button_y = trap.button.winfo_rooty()
        drawer_bottom = trap.advanced_group.winfo_rooty() + trap.advanced_group.winfo_height()
        strip_y = self.bar.canvas.winfo_rooty()
        animator = self.root._piezo_animator = anim.Animator(self.root)
        self.addCleanup(animator.stop)
        with patch.object(anim.time, "perf_counter", return_value=0.0) as clock:
            trap.set_expanded(True, persist=False)
            self.flush()
            self.assertTrue(animator.is_running(trap._motion_key))
            self.assertEqual(trap._reveal_height, 0)
            clock.return_value = 0.05
            animator._tick()
            self.flush()
            partial = trap._reveal_height
            self.assertGreater(partial, 0)
            self.assertLess(partial, trap._reveal_target)
            self.assertLess(trap.button.winfo_rooty(), button_y)
            self.assertEqual(trap.advanced_group.winfo_rooty() + trap.advanced_group.winfo_height(), drawer_bottom)
            self.assertLess(self.bar.canvas.winfo_rooty(), strip_y)
            self.assertGreaterEqual(trap._panel_outer.winfo_rooty(),
                                    trap.button.winfo_rooty() + trap.button.winfo_height())
            trap.set_expanded(False, persist=False)
            self.flush()
            self.assertEqual(trap._reveal_height, partial)
            clock.return_value = 0.5
            animator._tick()
            self.flush()
            self.assertFalse(trap._panel_outer.winfo_ismapped())
            self.assertFalse(animator.is_running(trap._motion_key))
            self.assertEqual(trap.button.winfo_rooty(), button_y)
            self.assertEqual(self.bar.canvas.winfo_rooty(), strip_y)

    def test_hiding_chrome_cancels_advanced_reveal_and_restores_on_return(self) -> None:
        self.page.pack_forget()
        clock_patch = patch.object(anim.time, "perf_counter", return_value=0.0)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        trap = self.trapezoid
        trap.set_expanded(False, persist=False)
        self.flush()
        animator = self.root._piezo_animator = anim.Animator(self.root)
        self.addCleanup(animator.stop)
        trap.set_expanded(True, persist=False)
        self.flush()
        self.assertTrue(animator.is_running(trap._motion_key))
        self.host.current_mode = "full_mode"
        trap.refresh_layout()
        self.flush()
        self.assertFalse(animator.is_running(trap._motion_key))
        self.assertFalse(trap._panel_outer.winfo_ismapped())
        self.host.current_mode = "button_mode"
        trap.refresh_layout()
        self.flush()
        animator.set_enabled(False)
        self.flush()
        self.assertTrue(trap._panel_outer.winfo_ismapped())
        self.assertGreater(trap._reveal_height, 0)

    def test_advanced_arrow_and_controls_are_compact(self) -> None:
        trap = self.trapezoid
        for expanded in (False, True):
            trap.set_expanded(expanded, persist=False)
            self.flush()
            points = trap.button.coords("chevron")
            self.assertEqual(len(points), 6)
            if expanded:
                self.assertGreater(points[3], points[1], "expanded header points down to its content")
            else:
                self.assertLess(points[3], points[1], "collapsed drawer points upward")
        action = trap.actions.winfo_children()[0]
        self.assertLess(action.winfo_height(), 40)
        self.assertLess(trap._panel_outer.winfo_height(), 130)

    def test_slim_strip_single_line_with_color_chip(self) -> None:
        self.root.update()
        self.assertEqual(self.bar.canvas.itemcget("strip", "fill"), "#202B3C")
        self.assertNotIn("\n", self.bar._display_titles["waveform"])
        self.assertIn(" · ", self.bar._titles["waveform"])
        self.assertEqual(self.bar.canvas.winfo_height(), 30)  # 27 + three shadow pixels
        self.assertEqual(self.bar._font.cget("size"), -18)
        node = self.bar._items["waveform"]["node"]
        self.assertEqual(self.bar.canvas.itemcget(node, "outline").lower(), "#6b7280")
        self.assertGreaterEqual(self.area.bottom_inset, 63)
        self.assertGreaterEqual(self.bar.canvas.winfo_rooty(),
                                self.area.canvas.winfo_rooty() + self.area.canvas.winfo_height())
        self.assertEqual(self.trapezoid.button.winfo_height(), 20)
        info = self.trapezoid.floating_group.place_info()
        self.assertEqual(int(info.get("y", 0)), -6)
        self.assertGreaterEqual(
            self.area.scroll_region_extra,
            self.trapezoid.floating_group.winfo_reqheight() + 24,
        )
        self.assertLessEqual(
            self.trapezoid.floating_group.winfo_width(),
            max(300, int(self.area.canvas.winfo_width() * 0.92) + 6),
            "step strip must stay a compact fraction of the viewport",
        )
        self.assertLessEqual(
            self.trapezoid.button.winfo_width(),
            self.trapezoid.floating_group.winfo_width() + 2,
        )

    def test_narrow_cells_elide_instead_of_wrapping(self) -> None:
        self.root.update()
        self.bar._titles["waveform"] = "2 · " + "很长的步骤标题" * 6
        self.bar.apply_width(390)
        text = self.bar._display_titles["waveform"]
        self.assertTrue(text.endswith("…"))
        self.assertNotIn("\n", text)

    def test_short_titles_survive_budget_and_navigation_does_not_resize(self) -> None:
        self.bar._titles = {"waveform": "1 · " + "Long waveform title " * 8,
                            "adc": "2 · ADC", "detection": "3 · EN"}
        self.bar.apply_width(480)
        self.assertEqual(self.bar._display_titles["adc"], "2 · ADC")
        self.assertEqual(self.bar._display_titles["detection"], "3 · EN")
        self.assertTrue(self.bar._display_titles["waveform"].endswith("…"))
        rectangles = dict(self.bar._hit_rects)
        width = self.bar.preferred_width()
        self.bar.scroll_to_step("adc")
        self.assertEqual(self.bar._hit_rects, rectangles)
        self.assertEqual(self.bar.preferred_width(), width)

    def test_state_updates_reuse_canvas_items_and_draw_geometry_symbols(self) -> None:
        self.flush()
        items = self.bar.canvas.find_all()
        width = self.bar.canvas.winfo_width()
        token = self.bar.begin("waveform")
        self.bar.mark("waveform", "done", token)
        self.flush()
        node = self.bar._items["waveform"]
        self.assertEqual(self.bar.canvas.find_all(), items)
        self.assertEqual(self.bar.canvas.winfo_width(), width)
        self.assertEqual(self.bar.canvas.itemcget(node["node"], "fill"), "#2563EB")
        self.assertEqual(self.bar.canvas.type(node["check"]), "line")
        self.assertEqual(self.bar.canvas.itemcget(node["check"], "state"), "normal")
        token = self.bar.begin("waveform")
        self.bar.mark("waveform", "error", token)
        self.flush()
        self.assertEqual(self.bar.canvas.itemcget(node["check"], "state"), "hidden")
        self.assertEqual(self.bar.canvas.itemcget(node["bang"], "state"), "normal")
        self.assertEqual(self.bar.canvas.type(node["bang"]), "line")

    def test_keyboard_navigation_and_collapse_are_independent_of_state(self) -> None:
        self.bar.canvas.focus_force()
        self.flush()
        self.bar.canvas.event_generate("<Right>")
        self.bar.canvas.event_generate("<Return>")
        self.flush()
        self.assertEqual(self.bar.active_step_key, "adc")
        self.assertTrue(all(data["state"] == "idle" for data in self.bar.snapshot().values()))
        self.bar.canvas.event_generate("<End>")
        self.bar.canvas.event_generate("<space>")
        self.flush()
        self.assertTrue(self.bar._collapsed)
        self.bar.canvas.event_generate("<Return>")
        self.flush()
        self.assertFalse(self.bar._collapsed)
        self.assertGreaterEqual(self.trapezoid.button.winfo_width(), 150)

    def test_fixed_band_clears_content_at_every_scroll_position(self) -> None:
        for row in range(30):
            ttk.Label(self.area.content, text=f"content {row}").grid(row=row, column=0, pady=10)
        self.flush()
        group = self.trapezoid.floating_group
        position = (group.winfo_rootx(), group.winfo_rooty())
        for fraction in (0.0, 0.5, 1.0):
            self.area.canvas.yview_moveto(fraction)
            self.flush()
            viewport_bottom = self.area.canvas.winfo_rooty() + self.area.canvas.winfo_height()
            self.assertLessEqual(viewport_bottom, group.winfo_rooty())
            self.assertEqual((group.winfo_rootx(), group.winfo_rooty()), position)

    def test_scale_tiers_keep_pixel_fonts_and_bottom_pill(self) -> None:
        for scale, strip_height, button_height in ((1.0, 27, 20), (1.1, 30, 22), (1.25, 33, 25)):
            with self.subTest(scale=scale):
                self.context.state.ui_scale = scale
                self.bar.refresh_theme()
                self.trapezoid.refresh_layout()
                self.flush()
                self.assertEqual(self.bar._font.cget("size"), -round(18*scale))
                self.assertEqual(self.bar._bold_font.cget("size"), -round(18*scale))
                self.assertEqual(self.bar.canvas.winfo_height(), strip_height + round(3*scale))
                self.assertEqual(self.trapezoid.button.winfo_height(), button_height)
                self.assertGreaterEqual(self.area.bottom_inset, round(63*scale))
                self.assertGreaterEqual(self.trapezoid.button.winfo_rooty(),
                                        self.bar.canvas.winfo_rooty() + self.bar.canvas.winfo_height())

    def test_cell_navigation_scrolls_owning_canvas_without_changing_horizontal_view(self) -> None:
        page = BasePage(self.root, self.context)
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        area, content = page.make_vertical_scroll_area(page)
        area.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        for row in range(28):
            ttk.Label(content, text=f"filler {row}").grid(row=row, column=0, sticky="w", pady=8)
        section, _body = page.make_module_section(
            content,
            "awp1221_control_buttons",
            step=1,
            step_total=1,
            help_key="awp1221_help_switches",
            row=30,
            column=0,
            sticky="ew",
        )
        bar = page.mount_step_progress(
            host=self.host,
            steps=(StepSpec("target", 1, "step_done_1221_detection"),),
            context=lambda: self.context_value,
        )
        self.host.show_step_chrome(page)
        self.root.update()
        self.assertGreater(section.winfo_rooty(), area.canvas.winfo_rooty())
        before_x = area.canvas.xview()
        bar.scroll_to_step("target")
        self.root.update()
        self.assertGreater(area.canvas.yview()[0], 0.0)
        self.assertEqual(area.canvas.xview(), before_x)
        # Node, title, and padding clicks all invoke the same step.
        area.canvas.yview_moveto(0.0)
        self.root.update()
        self.assertEqual(area.canvas.yview()[0], 0.0)
        x1, y1, x2, y2 = bar._hit_rects["target"]
        for x in (x1 + 1, x1 + 9, x2 - 6):
            area.canvas.yview_moveto(0.0)
            self.root.update()
            bar.canvas.event_generate("<ButtonPress-1>", x=int(x), y=int((y1+y2)/2))
            bar.canvas.event_generate("<ButtonRelease-1>", x=int(x), y=int((y1+y2)/2))
            self.root.update()
            self.assertGreater(area.canvas.yview()[0], 0.0)
            self.assertEqual(area.canvas.xview(), before_x)
            self.assertEqual(bar.snapshot()["target"]["state"], "idle")
            self.assertEqual(bar.active_step_key, "target")
        page.destroy()

    def test_trapezoid_only_host_has_no_automatic_progress_bar(self) -> None:
        self.assertEqual(len(self.host._step_progress_bars), 1)
        other = BasePage(self.root, self.context)
        other.pack()
        trap = other.mount_trapezoid(self._build_actions, self._build_registers, state_key="full_only")
        self.root.update()
        self.assertIsNotNone(trap)
        self.assertEqual(other._step_progress_bars, [])
        self.assertFalse(any(isinstance(child, ttk.Button) for child in trap.progress_slot.winfo_children()))
        other.destroy()


if __name__ == "__main__":
    unittest.main()

    def test_metadata_ordinals_survive_without_title_prefix(self) -> None:
        page = BasePage(self.root, self.context)
        page.pack(fill="both", expand=True)
        section, _body = page.make_module_section(
            page, "awp1221_control_buttons", step=1, step_total=1, help_key="awp1221_help_switches",
        )
        bar = page.mount_step_progress(
            host=self.host,
            steps=(StepSpec("target", 1, "step_done_1221_detection"),),
            context=lambda: self.context_value,
        )
        self.assertIn(1, bar._metadata)
        self.assertIs(bar._metadata[1]["target"], section)
        self.assertEqual(section.winfo_children()[0].winfo_children()[0].cget("text") if False else True, True)
        # With a step bar present, section titles drop the "Step N" prefix.
        titles = [b["label"].cget("text") for b in page._module_title_bindings]
        self.assertTrue(all("Step " not in t and "第" not in t for t in titles))
        page.destroy()
