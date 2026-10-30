"""The ADC segmented selector must draw intermediate blue-background frames."""
import time
import unittest

from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
from tests.validate_responsive_layout import mocked_application


class AdcStageMotionFixture:
    def setUp(self):
        prepare_tk_test(self)
        fixture = mocked_application()
        self.app = fixture.__enter__()
        self.root = self.app.root
        self.errors = []
        self.root.report_callback_exception = lambda k, v, tb: self.errors.append(str(v))
        attach_tk_cleanup(self, self.root)
        self.addCleanup(fixture.__exit__, None, None, None)
        self.root.geometry("1700x980")
        self.root.deiconify()
        self.app.set_animations(False)
        self.app.navigation.show("awp1221")
        self.host = self.app.navigation.mode_host
        self.host.show_mode("button_mode")
        self.page = self.host.mode_pages["button_mode"]
        self.pump(500)
        self.page.step_progress.scroll_to_step("adc")
        deadline = time.perf_counter() + 10
        ready = False
        previous = None
        unchanged_since = time.perf_counter()
        def mapped():
            nonlocal ready, previous, unchanged_since
            motion = self.page._button_stage_motion
            geometry = (self.root.winfo_geometry(), motion.canvas.winfo_geometry(),
                        tuple(motion._rect_for(button) for button in motion._buttons),
                        self.host.mode_scroll.canvas.winfo_geometry())
            if geometry != previous:
                previous, unchanged_since = geometry, time.perf_counter()
            ready = (self.page._button_stage_motion.canvas.winfo_viewable()
                     and not self.app.transitions.active and self.app.navigation._page_layout_ready()
                     and self.app._resize_after is None
                     and (self.root.winfo_width(), self.root.winfo_height()) == (1700, 980)
                     and time.perf_counter()-unchanged_since >= .3
                     and all(cell.winfo_ismapped() for row in self.page.adc_legend._rule_rows.values()
                             for cell in row))
            if ready or time.perf_counter() >= deadline:
                self.root.quit()
            else:
                self.root.after(20, mapped)
        self.root.after(20, mapped)
        self.root.mainloop()
        self.assertTrue(ready, 'ADC page was not mapped and layout-ready before interaction')
        self.assertTrue(all(cell.winfo_ismapped() for row in self.page.adc_legend._rule_rows.values()
                            for cell in row), "ADC rule rows are not ready for pointer input")
        self.app.set_animations(True)

    def settle(self, motion=None):
        if motion is None:
            motion = self.page._button_stage_motion
        deadline = time.perf_counter() + 3
        stable, previous = 0, None
        def poll():
            nonlocal stable, previous
            box = motion.canvas.bbox("selection")
            geometry = (box, motion.canvas.winfo_width(),
                        tuple(motion._rect_for(b) for b in motion._buttons))
            aligned = True
            if motion._selected is not None and motion.canvas.winfo_viewable():
                x, _y, width, _height = motion._rect_for(motion._selected)
                aligned = box is not None and abs((box[0]+box[2])/2-(x+width/2)) <= 1
            stable = stable + 1 if not motion.busy and aligned and geometry == previous else 0
            previous = geometry
            if stable >= 3 or time.perf_counter() >= deadline:
                self.root.quit()
            else:
                self.root.after(16, poll)
        self.root.after(16, poll)
        self.root.mainloop()
        self.assertGreaterEqual(stable, 3, "ADC selector geometry did not settle")
        self.assertFalse(motion.busy, (motion._draw_job, motion._motion._job,
                         motion._motion.animator.is_running(motion._motion._key),
                         motion._motion._rect, motion._motion._target_rect))
        if motion.canvas.winfo_viewable():
            self.assertEqual(motion.canvas.itemcget(motion._fill, "state"), "normal")
            self.assertGreater(motion.canvas.bbox("selection")[2]-motion.canvas.bbox("selection")[0], 1)
        self.assertFalse(self.errors, self.errors)

    def visual_layout(self, width):
        """Finish a real WM/font-tier change before timing selector motion."""
        deadline = time.perf_counter()+10
        stable = 0
        def poll():
            nonlocal stable
            ready = ((self.root.winfo_width(), self.root.winfo_height()) == (width, 980)
                     and self.app._resize_after is None and self.app.navigation._page_layout_ready()
                     and not self.app.transitions.active)
            stable = stable+1 if ready else 0
            if stable >= 3 or time.perf_counter() >= deadline:
                self.root.quit()
            else:
                self.root.after(20, poll)
        self.root.after(20, poll)
        self.root.mainloop()
        self.assertGreaterEqual(stable, 3, 'theme/WM resize did not finish before selector interaction')

    def assert_selected(self, index):
        motion = self.page._button_stage_motion
        self.settle()
        bbox = motion.canvas.bbox("selection")
        button = self.page._button_stage_buttons[index]
        target_x = button.winfo_rootx()-motion.canvas.winfo_rootx()
        self.assertEqual(self.page._visible_button_stage_index, index)
        self.assertAlmostEqual((bbox[0]+bbox[2])/2, target_x+button.winfo_width()/2, delta=1)
        self.assertEqual(self.page._visible_button_stage_index, index)
        self.assertEqual(self.page._button_stage_buttons[index].cget("style"), "Selected.TButton")

    def pump(self, ms=25):
        self.root.after(ms, self.root.quit)
        self.root.mainloop()

    def assert_no_dashed_frame(self, motion):
        for item in motion.canvas.find_all():
            if motion.canvas.type(item) in {"polygon", "rectangle", "line"}:
                self.assertFalse(motion.canvas.itemcget(item, "dash"))


class AdcStageMotionTests(AdcStageMotionFixture, unittest.TestCase):
    def test_stage_click_draws_intermediate_selected_background(self):
        motion = getattr(self.page, "_button_stage_motion", None)
        self.assertIsNotNone(motion, "ADC Stage0/Stage1 still switch button styles without a sliding background")
        self.page._button_stage_buttons[0].invoke()
        motion.finish()
        self.pump(100)
        self.assertTrue(motion.canvas.winfo_viewable())
        self.assertTrue(motion._indicator.winfo_viewable())
        start = motion.canvas.bbox("selection")[0]
        samples = []
        binding = motion._indicator.bind("<Configure>",
            lambda _event: samples.append(motion.canvas.bbox("selection")[0]), add="+")
        try:
            self.page._button_stage_buttons[1].invoke()
            deadline = time.perf_counter() + 3
            def poll():
                if not motion.busy or time.perf_counter() >= deadline:
                    self.root.quit()
                else:
                    self.root.after(16, poll)
            self.root.after(16, poll)
            self.root.mainloop()
            self.settle()
        finally:
            motion._indicator.unbind("<Configure>", binding)
        end = motion.canvas.bbox("selection")[0]
        self.assertTrue(any(start < x < end for x in samples),
                        (start, end, samples, motion._motion._rect, motion._motion._target_rect))
        self.assertEqual(self.page._visible_button_stage_index, 1)
        self.assertTrue(self.page._button_stage_frames[1].winfo_viewable())
        self.assertFalse(self.page._button_stage_frames[0].winfo_ismapped())
        self.settle()
        self.assertFalse(self.errors, self.errors)

    def test_pointer_and_legend_select_the_same_stage_without_changing_parameters(self):
        motion = self.page._button_stage_motion
        before = [self.page._button_stage_config(i) for i in range(2)]
        button = self.page._button_stage_buttons[1]
        x = button.winfo_rootx()-motion.canvas.winfo_rootx()+button.winfo_width()//2
        y = button.winfo_height()//2
        motion.canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        motion.canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.assert_selected(1)
        self.page.adc_legend.activate_stage("Stage0")
        self.assert_selected(0)
        self.assertEqual([self.page._button_stage_config(i) for i in range(2)], before)
        self.assertEqual(button.winfo_class(), "TButton")
        self.assertIn("TButton", button.bindtags())
        self.assertEqual(str(motion.canvas.cget("takefocus")), "0")

    def test_all_button_outlines_stay_inside_the_drawing_surface(self):
        motion = self.page._button_stage_motion
        for index in (0, 1):
            self.page._button_stage_buttons[index].invoke()
            self.assert_selected(index)
            for item in (*motion._bases, motion._fill):
                coordinates = motion.canvas.coords(item)
                xs, ys = coordinates[::2], coordinates[1::2]
                self.assertGreaterEqual(min(xs), .5)
                self.assertGreaterEqual(min(ys), .5)
                self.assertLessEqual(max(xs), motion.canvas.winfo_width()-.5)
                self.assertLessEqual(max(ys), motion.canvas.winfo_height()-.5)

    def test_pointer_focus_does_not_draw_an_inner_dashed_frame(self):
        motion = self.page._button_stage_motion
        self.root.focus_force()
        self.pump()
        for index in (1, 0):
            button = self.page._button_stage_buttons[index]
            x, y, width, height = motion._rect_for(button)
            motion.canvas.event_generate("<ButtonPress-1>", x=x+width//2, y=y+height//2)
            motion.canvas.event_generate("<ButtonRelease-1>", x=x+width//2, y=y+height//2)
            self.assert_selected(index)
            self.assertIs(self.root.focus_get(), button)
            self.assert_no_dashed_frame(motion)

    def test_keyboard_traversal_retains_focus_and_native_space_activation(self):
        motion = self.page._button_stage_motion
        first, second = motion._buttons
        self.root.focus_force()
        first.focus_set()
        self.settle()
        first.event_generate("<KeyPress-Tab>")
        self.settle()
        self.assertIs(self.root.focus_get(), second)
        self.assertEqual(motion.canvas.itemcget(motion._bases[1], "outline"), motion._blue)
        self.assertEqual(float(motion.canvas.itemcget(motion._bases[1], "width")), 2)
        self.assert_no_dashed_frame(motion)
        second.event_generate("<KeyPress-space>")
        second.event_generate("<KeyRelease-space>")
        self.assert_selected(1)

    def test_waveform_sources_have_the_same_sliding_background(self):
        motion = getattr(self.page, "_button_wave_selection_motion", None)
        self.assertIsNotNone(motion, "ROM/SINC/SRAM still have no sliding button background")
        self.page.step_progress.scroll_to_step("waveform")
        self.page._button_wave_tabs["rom"].invoke()
        self.settle(motion)
        self.assertTrue(motion.canvas.winfo_viewable())
        for key in ("sinc", "sram", "rom"):
            with self.subTest(source=key):
                start = motion.canvas.bbox("selection")[0]
                samples = []
                binding = motion._indicator.bind("<Configure>",
                    lambda _e: samples.append(motion.canvas.bbox("selection")[0]), add="+")
                try:
                    self.page._button_wave_tabs[key].invoke()
                    self.settle(motion)
                finally:
                    motion._indicator.unbind("<Configure>", binding)
                end = motion.canvas.bbox("selection")[0]
                self.assertTrue(any(min(start, end) < x < max(start, end) for x in samples),
                                (key, start, end, samples))
                button = self.page._button_wave_tabs[key]
                self.assertAlmostEqual((motion.canvas.bbox("selection")[0]
                                        + motion.canvas.bbox("selection")[2])/2,
                                       button.winfo_x()+button.winfo_width()/2, delta=1)
                self.assertFalse(hasattr(self.page, "_button_wave_indicator"))
                self.assertEqual(motion._parent.master.winfo_height(), motion._parent.winfo_height())

    def test_waveform_pointer_redirect_theme_and_hide_keep_the_frame(self):
        motion = self.page._button_wave_selection_motion
        self.page.step_progress.scroll_to_step("waveform")
        self.root.focus_force()
        for key in ("rom", "sinc", "sram", "rom"):
            button = self.page._button_wave_tabs[key]
            x, y, width, height = motion._rect_for(button)
            motion.canvas.event_generate("<ButtonPress-1>", x=x+width//2, y=y+height//2)
            motion.canvas.event_generate("<ButtonRelease-1>", x=x+width//2, y=y+height//2)
            self.pump(15)
        self.settle(motion)
        self.assertEqual(self.page._button_wave_tab_selection, "rom")
        self.assert_no_dashed_frame(motion)
        for width, language, theme in ((1280, "en", "dark"), (1700, "zh", "light")):
            self.root.geometry(f"{width}x980")
            self.visual_layout(width)
            self.app.apply_settings(language, theme, self.app.state.font_size)
            self.visual_layout(width)
            self.page.step_progress.scroll_to_step("waveform")
            self.settle(motion)
            for button, label, border in zip(motion._buttons, motion._labels, motion._bases):
                self.assertEqual(motion.canvas.itemcget(label, "text"), button.cget("text"))
                coordinates = motion.canvas.coords(border)
                self.assertGreaterEqual(min(coordinates[::2]), .5)
                self.assertGreaterEqual(min(coordinates[1::2]), .5)
                self.assertLessEqual(max(coordinates[::2]), motion.canvas.winfo_width()-.5)
                self.assertLessEqual(max(coordinates[1::2]), motion.canvas.winfo_height()-.5)
        self.page._button_wave_tabs["sram"].invoke()
        self.app.set_animations(False)
        self.settle(motion)
        self.host.show_mode("full_mode")
        self.pump(100)
        self.assertFalse(motion.busy)
        self.assertFalse(motion.canvas.winfo_ismapped())
        self.host.show_mode("button_mode")
        self.page.step_progress.scroll_to_step("waveform")
        self.settle(motion)
        self.assertEqual(self.page._button_wave_tab_selection, "sram")

    def test_quick_redirect_disable_and_mode_remap(self):
        motion = self.page._button_stage_motion
        for index in (1, 0, 1, 0, 1):
            self.page._button_stage_buttons[index].invoke()
            self.pump(15)
        self.assert_selected(1)
        self.page._button_stage_buttons[0].invoke()
        self.app.set_animations(False)
        motion.finish()
        self.assert_selected(0)
        self.host.show_mode("full_mode")
        self.pump(100)
        self.assertFalse(motion.busy)
        self.assertFalse(motion.canvas.winfo_ismapped())
        self.host.show_mode("button_mode")
        self.page.step_progress.scroll_to_step("adc")
        self.pump(400)
        self.assert_selected(0)

    def test_theme_language_and_resize_keep_text_and_selection_aligned(self):
        motion = self.page._button_stage_motion
        for width, language, theme in ((1280, "en", "dark"), (1700, "zh", "light")):
            self.root.geometry(f"{width}x980")
            self.visual_layout(width)
            self.app.apply_settings(language, theme, self.app.state.font_size)
            self.visual_layout(width)
            self.page.step_progress.scroll_to_step("adc")
            self.pump(250)
            self.page._button_stage_buttons[1].invoke()
            self.assert_selected(1)
            for button, text_item in zip(motion._buttons, motion._labels):
                self.assertEqual(motion.canvas.itemcget(text_item, "text"), button.cget("text"))


if __name__ == "__main__":
    unittest.main()
