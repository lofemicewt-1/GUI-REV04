"""Real widget events must preserve selection and show unselected hover."""
import unittest
from tests.test_awp1221_adc_stage_motion import AdcStageMotionFixture


class AdcSelectionStateTests(AdcStageMotionFixture, unittest.TestCase):
    def test_diagram_hover_keeps_the_selected_card(self):
        legend = self.page.adc_legend
        self.page._button_stage_buttons[0].invoke()
        self.settle()
        boxes = legend.canvas.find_withtag("stage_box")
        selected, other = boxes
        selected_fill = legend.canvas.itemcget(selected, "fill")
        normal = legend.canvas.itemcget(other, "fill")
        x0, y0, x1, y1 = legend.canvas.coords(other)
        legend.canvas.event_generate("<Motion>", x=(x0+x1)//2, y=(y0+y1)//2)
        self.pump()
        self.assertNotEqual(legend.canvas.itemcget(other, "fill"), normal)
        self.assertEqual(legend.canvas.itemcget(selected, "fill"), selected_fill)
        legend.canvas.event_generate("<Motion>", x=2, y=2)
        self.pump()
        self.assertEqual(legend.canvas.itemcget(other, "fill"), normal)

    def test_rule_row_selection_survives_hover_exit(self):
        legend = self.page.adc_legend
        cell = legend._rule_rows[2][0]
        cell.event_generate("<Enter>")
        cell.event_generate("<Button-1>")
        cell.event_generate("<Leave>")
        self.settle()
        self.assertEqual(self.page._visible_button_stage_index, 1)
        self.assertEqual(legend._rule_borders[2].cget("highlightbackground"), self.page.colors["primary"])
        self.assertNotEqual(legend._rule_rows[2][0].cget("background"),
                            legend._rule_rows[1][0].cget("background"))

    def test_rule_hover_is_neutral_and_only_selection_has_a_checkmark(self):
        legend = self.page.adc_legend
        for language, theme in (("zh", "light"), ("en", "dark")):
            self.app.apply_settings(language, theme, self.app.state.font_size)
            self.page._button_stage_buttons[0].invoke()
            self.settle()
            selected = legend._rule_rows[1][0]
            other = legend._rule_rows[2][0]
            other.event_generate("<Enter>")
            self.pump()
            self.assertTrue(selected.cget("text").startswith("✓ "))
            self.assertFalse(other.cget("text").startswith("✓ "))
            self.assertEqual(legend._rule_borders[1].cget("highlightbackground"), self.page.colors["primary"])
            self.assertEqual(legend._rule_borders[2].cget("highlightbackground"), self.page.colors["border"])
            other.event_generate("<Button-1>")
            other.event_generate("<Leave>")
            self.assert_selected(1)
            self.assertTrue(other.cget("text").startswith("✓ "))
            self.assertFalse(selected.cget("text").startswith("✓ "))

    def test_unselected_buttons_hover_without_a_white_focus_frame(self):
        for motion in (self.page._button_stage_motion, self.page._button_wave_selection_motion):
            self.settle(motion)
            other = next(button for button in motion._buttons if button is not motion._selected)
            index = motion._buttons.index(other)
            base = motion._bases[index]
            normal = motion.canvas.itemcget(base, "fill")
            blue = motion.canvas.itemcget(motion._fill, "fill")
            x, y, width, height = motion._rect_for(other)
            motion.canvas.event_generate("<Motion>", x=x+width//2, y=y+height//2)
            self.pump()
            self.assertNotEqual(motion.canvas.itemcget(base, "fill"), normal)
            self.assertEqual(motion.canvas.itemcget(motion._fill, "fill"), blue)
            self.assert_no_dashed_frame(motion)
            motion.canvas.event_generate("<Leave>")
            self.pump()
            self.assertEqual(motion.canvas.itemcget(base, "fill"), normal)

    def test_source_has_no_separate_underline(self):
        self.assertFalse(hasattr(self.page, "_button_wave_indicator"))

    def test_card_row_and_button_selection_stay_in_sync(self):
        legend = self.page.adc_legend
        before = [self.page._button_stage_config(index) for index in range(2)]
        for index, via in ((1, "row"), (0, "card"), (1, "button")):
            if via == "row":
                legend._rule_rows[index+1][0].event_generate("<Button-1>")
            elif via == "card":
                box = legend.canvas.find_withtag("stage_box")[index]
                x0, y0, x1, y1 = legend.canvas.coords(box)
                legend.canvas.event_generate("<Motion>", x=(x0+x1)//2, y=(y0+y1)//2)
                legend.canvas.event_generate("<Button-1>", x=(x0+x1)//2, y=(y0+y1)//2)
            else:
                self.page._button_stage_buttons[index].invoke()
            self.assert_selected(index)
            self.assertEqual(legend._preview_stage, f"Stage{index}")
            self.assertEqual(legend._rule_rows[index+1][0].cget("foreground"), self.page.colors["primary"])
            box = legend.canvas.find_withtag("stage_box")[index]
            self.assertEqual(legend.canvas.itemcget(box, "outline"), self.page.colors["primary"])
            self.assertEqual(float(legend.canvas.itemcget(box, "width")), 3)
        self.assertEqual([self.page._button_stage_config(index) for index in range(2)], before)

    def test_hover_respects_theme_disable_and_dispose(self):
        self.root.focus_force()
        for language, theme in (("en", "dark"), ("zh", "light")):
            self.app.apply_settings(language, theme, self.app.state.font_size)
            for motion in (self.page._button_stage_motion, self.page._button_wave_selection_motion):
                self.settle(motion)
                button = next(button for button in motion._buttons if button is not motion._selected)
                item = motion._bases[motion._buttons.index(button)]
                x, y, width, height = motion._rect_for(button)
                motion.canvas.event_generate("<Motion>", x=x+width//2, y=y+height//2)
                self.settle(motion)
                self.assertEqual(motion.canvas.itemcget(item, "fill"), motion._hover_bg)
                self.assertEqual(motion.canvas.itemcget(item, "outline"), motion._blue)
                self.assert_no_dashed_frame(motion)
                selected = motion._selected
                button.state(("disabled",))
                motion.canvas.event_generate("<Motion>", x=x+width//2, y=y+height//2)
                motion.canvas.event_generate("<ButtonPress-1>", x=x+width//2, y=y+height//2)
                motion.canvas.event_generate("<ButtonRelease-1>", x=x+width//2, y=y+height//2)
                self.settle(motion)
                self.assertIs(motion._selected, selected)
                self.assertEqual(motion.canvas.itemcget(item, "fill"), motion._background)
                button.state(("!disabled",))
        motions = (self.page._button_stage_motion, self.page._button_wave_selection_motion)
        self.page._button_stage_buttons[1].invoke()
        self.page._button_wave_tabs["sram"].invoke()
        self.page.destroy()
        self.pump(100)
        self.assertTrue(all(not motion.busy for motion in motions))
        self.assertFalse(self.errors, self.errors)


