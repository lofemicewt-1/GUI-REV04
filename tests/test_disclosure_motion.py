"""Exercise real log and advanced drawer lifecycles without hardware."""
import time
import unittest
from unittest.mock import patch

from tests.test_awp1221_adc_stage_motion import AdcStageMotionFixture


class DisclosureMotionTests(AdcStageMotionFixture, unittest.TestCase):
    def wait_log(self):
        motion = self.page._serial_log_motion
        deadline = time.perf_counter()+3
        stable = 0
        def poll():
            nonlocal stable
            stable = stable+1 if not motion.busy else 0
            if stable >= 2 or time.perf_counter() >= deadline:
                self.root.quit()
            else:
                self.root.after(16, poll)
        self.root.after(16, poll)
        self.root.mainloop()
        self.assertFalse(motion.busy)
        self.assertFalse(self.errors, self.errors)

    def test_log_toggle_exposes_intermediate_heights(self):
        body = self.page._communication_log_panel
        self.assertFalse(body.winfo_manager())
        heights = []
        binding = body.bind("<Configure>", lambda _e: heights.append(body.winfo_height()), add="+")
        try:
            self.page._communication_log_toggle.invoke()
            deadline = time.perf_counter()+3
            stable = 0
            def poll():
                nonlocal stable
                ready = (not self.page._serial_log_motion.busy and body.winfo_ismapped()
                         and body.winfo_height() == self.page._serial_log_content.winfo_reqheight())
                stable = stable+1 if ready else 0
                if stable >= 2 or time.perf_counter() >= deadline:
                    self.root.quit()
                else:
                    self.root.after(16, poll)
            self.root.after(16, poll)
            self.root.mainloop()
            self.assertFalse(self.page._serial_log_motion.busy)
        finally:
            body.unbind("<Configure>", binding)
        final = body.winfo_height()
        self.assertTrue(any(1 < height < final-1 for height in heights), (final, heights))
        self.assertFalse(self.errors, self.errors)

    def test_log_quick_redirect_disable_mode_hide_and_destroy(self):
        toggle = self.page._communication_log_toggle
        motion = self.page._serial_log_motion
        for _ in range(3):
            toggle.invoke()
            self.pump(15)
        self.wait_log()
        self.assertTrue(motion.expanded)
        self.assertEqual(self.page._serial_log_body.winfo_height(), self.page._serial_log_content.winfo_reqheight())
        self.assertEqual(int(self.page._serial_log_text.cget("height")), 8)
        toggle.invoke()
        self.app.set_animations(False)
        self.wait_log()
        self.assertFalse(self.page._serial_log_body.winfo_manager())
        self.app.set_animations(True)
        toggle.invoke()
        self.host.show_mode("full_mode")
        self.pump(300)
        self.assertFalse(motion.busy)
        self.assertIsNone(getattr(self.page, "_comm_log_scroll_job", None))
        self.host.show_mode("button_mode")
        self.wait_log()
        toggle.invoke()
        self.page.destroy()
        self.pump(100)
        self.assertFalse(motion.busy)
        self.assertFalse(self.errors, self.errors)

    def test_log_does_not_request_a_scroll_cover_during_reveal(self):
        motion = self.page._serial_log_motion
        calls = []
        original = self.app.viewport_presentation.scroll
        def scroll(canvas, operation):
            if canvas is self.host.mode_scroll.canvas:
                calls.append(motion.busy)
            return original(canvas, operation)
        with patch.object(self.app.viewport_presentation, "scroll", side_effect=scroll):
            self.page._communication_log_toggle.invoke()
            self.wait_log()
            self.pump(100)
        self.assertFalse(any(calls), calls)

    def test_advanced_drawer_never_clips_progress_or_header_text(self):
        trap = self.host.trapezoid
        trap.set_expanded(False, persist=False)
        self.pump(400)
        clips = []
        samples = []
        canvases = (self.page.step_progress.canvas, trap.button)

        def sample(_event=None):
            samples.append(trap._reveal_height)
            for canvas in canvases:
                if not canvas.winfo_viewable():
                    continue
                for item in canvas.find_all():
                    if canvas.type(item) != "text" or canvas.itemcget(item, "state") == "hidden":
                        continue
                    box = canvas.bbox(item)
                    if not box:
                        continue
                    left, top, right, bottom = box
                    left += canvas.winfo_rootx()
                    right += canvas.winfo_rootx()
                    top += canvas.winfo_rooty()
                    bottom += canvas.winfo_rooty()
                    ancestor = canvas
                    while ancestor is not trap.floating_group.master:
                        x, y = ancestor.winfo_rootx(), ancestor.winfo_rooty()
                        width, height = ancestor.winfo_width(), ancestor.winfo_height()
                        if left < x or top < y or right > x+width or bottom > y+height:
                            clips.append((str(canvas), canvas.itemcget(item, "text"),
                                          str(ancestor), (left-x, top-y, right-x, bottom-y), (width, height)))
                        ancestor = ancestor.master

        bindings = [(widget, widget.bind("<Configure>", sample, add="+"))
                    for widget in (*canvases, trap.progress_slot, trap.floating_group)]
        try:
            for expanded in (True, False):
                trap.set_expanded(expanded, persist=False)
                deadline = time.perf_counter()+.7
                while time.perf_counter() < deadline:
                    self.pump(10)
                    sample()
        finally:
            for widget, binding in bindings:
                widget.unbind("<Configure>", binding)
        self.assertTrue(any(height > 0 for height in samples))
        self.assertFalse(clips, clips[:8])
        self.assertFalse(self.errors, self.errors)
