"""Exercise the reusable module without constructing chip pages or the app."""
import tkinter as tk
from tkinter import ttk
import unittest

from core.animation import Animator
from core.ui_motion import DropdownReveal, UnderlineMotion
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test


class UiMotionTests(unittest.TestCase):
    def setUp(self):
        prepare_tk_test(self)
        self.root = tk.Tk()
        attach_tk_cleanup(self, self.root)
        self.root.geometry("480x240")
        self.errors = []
        self.root.report_callback_exception = lambda k, v, tb: self.errors.append(str(v))
        self.animator = Animator(self.root)
        body = ttk.Frame(self.root, padding=12)
        body.pack(fill="both", expand=True)
        header = ttk.Frame(body)
        header.pack(fill="x", padx=9)
        self.buttons = []
        for index in range(3):
            header.columnconfigure(index, weight=1, uniform="tabs")
            button = ttk.Button(header, text=f"Tab {index}")
            button.grid(row=0, column=index, sticky="ew", padx=3)
            self.buttons.append(button)
        self.track = ttk.Frame(body, height=3)
        self.track.pack(fill="x", pady=4)
        self.indicator = tk.Frame(self.track, bg="#2563EB", bd=0)
        self.motion = UnderlineMotion(self.indicator, self.animator, owner=body)
        self.dropdown = DropdownReveal(self.root, self.animator)
        self.combo = ttk.Combobox(body, values=[str(i) for i in range(20)], state="readonly", height=5)
        self.combo.pack()
        self.root.update()
        self.motion.select(self.buttons[0], animate=False)
        self.motion.finish()
        self.root.update_idletasks()

    def tearDown(self):
        self.motion.close()
        self.dropdown.close()
        self.animator.stop()
        self.assertFalse(self.errors, self.errors)

    def pump(self, ms=300):
        self.root.after(ms, self.root.quit)
        self.root.mainloop()

    def assert_at(self, button):
        self.assertEqual(self.indicator.winfo_x(), button.winfo_rootx()-self.track.winfo_rootx())
        self.assertEqual(self.indicator.winfo_width(), button.winfo_width())

    def test_redirects_to_latest_widget_and_uses_track_coordinates(self):
        samples = []
        self.indicator.bind("<Configure>", lambda event: samples.append(event.x), add="+")
        start = self.indicator.winfo_x()
        self.motion.select(self.buttons[2])
        self.root.after(35, lambda: self.motion.select(self.buttons[1]))
        self.pump(400)
        self.assert_at(self.buttons[1])
        end = self.indicator.winfo_x()
        self.assertTrue(any(start < x < end for x in samples), samples)
        self.assertFalse(self.motion.busy)

    def test_finish_commits_queued_selection_when_disabled(self):
        self.motion.select(self.buttons[2])
        self.assertTrue(self.motion.busy)
        self.animator.set_enabled(False)
        self.motion.finish()
        self.root.update_idletasks()
        self.assert_at(self.buttons[2])
        self.assertFalse(self.motion.busy)

    def test_redirect_during_pending_geometry_starts_at_visible_position(self):
        self.motion.select(self.buttons[2])
        self.root.update_idletasks()
        self.animator._tweens[self.motion._key].step(.3)
        visible = (float(self.indicator.winfo_x()), float(self.indicator.winfo_width()))
        self.assertNotEqual(self.motion._rect, visible)
        self.motion.select(self.buttons[1])
        self.motion._place(True)  # Configure repair before the preceding place reaches Tk.
        self.assertEqual(self.motion._rect, visible)
        self.pump(350)
        self.assert_at(self.buttons[1])

    def test_sustained_busy_frames_have_a_bounded_retry_budget(self):
        self.motion.select(self.buttons[2])
        self.root.update_idletasks()
        start = self.indicator.winfo_x()
        end = self.buttons[2].winfo_rootx()-self.track.winfo_rootx()
        for index in range(4):
            tween = self.animator._tweens[self.motion._key]
            tween.start -= 1
            self.animator._cancel_timer()
            self.animator._tick()
            self.root.update_idletasks()
            if index < 3:
                self.assertLess(start, self.indicator.winfo_x())
                self.assertLess(self.indicator.winfo_x(), end)
                start = self.indicator.winfo_x()
            self.assertEqual(self.motion.busy, index < 3)
        self.assert_at(self.buttons[2])

    def test_late_frame_wait_can_be_finished_hidden_or_destroyed(self):
        for action in ("finish", "hide", "close", "disable"):
            with self.subTest(action=action):
                indicator = tk.Frame(self.track, bg="#2563EB", bd=0)
                motion = UnderlineMotion(indicator, self.animator)
                motion.select(self.buttons[0], animate=False)
                motion.finish()
                self.root.update_idletasks()
                motion.select(self.buttons[2])
                self.root.update_idletasks()
                tween = self.animator._tweens[motion._key]
                tween.start -= 1
                self.animator._cancel_timer()
                self.animator._tick()
                stale = tween.step
                if action == "disable":
                    self.animator.set_enabled(False)
                else:
                    getattr(motion, action)()
                self.root.update_idletasks()
                stale(.6)
                self.pump(30)
                self.assertFalse(motion.busy)
                self.assertNotIn(motion._key, self.animator._tweens)
                if action in ("finish", "disable"):
                    self.assertEqual(indicator.winfo_x(), self.buttons[2].winfo_rootx()-self.track.winfo_rootx())
                else:
                    self.assertFalse(indicator.winfo_ismapped())
                motion.close()
                indicator.destroy()
                self.animator.set_enabled(True)

    def test_redirect_during_late_frame_wait_uses_actual_geometry(self):
        start = self.indicator.winfo_x()
        self.motion.select(self.buttons[2])
        self.root.update_idletasks()
        tween = self.animator._tweens[self.motion._key]
        tween.start -= 1
        self.animator._cancel_timer()
        self.animator._tick()
        self.assertTrue(self.motion._waiting_frame)
        self.assertEqual(self.indicator.winfo_x(), start)
        self.motion.select(self.buttons[1])
        self.assertEqual(self.motion._rect[0], start)
        self.pump(350)
        self.assert_at(self.buttons[1])
        self.assertFalse(self.motion.busy)

    def test_hidden_track_cancels_and_remap_restores_latest_selection(self):
        self.motion.select(self.buttons[2])
        self.track.pack_forget()
        self.pump(40)
        self.assertFalse(self.motion.busy)
        self.track.pack(fill="x", pady=4)
        self.pump()
        self.assert_at(self.buttons[2])

    def test_destroying_selected_widget_cancels_queued_work(self):
        self.motion.select(self.buttons[2])
        self.buttons[2].destroy()
        self.pump(40)
        self.assertFalse(self.motion.busy)
        self.assertFalse(self.indicator.winfo_ismapped())
        self.motion.select(self.buttons[0])
        self.pump()
        self.assert_at(self.buttons[0])

    def test_page_owner_remap_restores_a_manually_hidden_indicator(self):
        owner = self.track.master
        self.motion.hide()
        owner.pack_forget()
        self.pump(40)
        owner.pack(fill="both", expand=True)
        self.pump()
        self.assertTrue(self.indicator.winfo_viewable())
        self.assert_at(self.buttons[0])

    def test_close_rejects_stale_frame_callbacks(self):
        self.motion.select(self.buttons[2])
        self.root.update_idletasks()
        stale = self.animator._tweens[self.motion._key].step
        self.motion.close()
        stale(.5)
        self.motion.select(self.buttons[1])
        self.pump(40)
        self.assertFalse(self.motion.busy)
        self.assertFalse(self.indicator.winfo_ismapped())

    def test_dropdown_close_preserves_foreign_class_binding(self):
        foreign = []
        self.root.bind_class("ComboboxPopdown", "<Map>", lambda _e: foreign.append(True), add="+")
        call = self.root.tk.call
        call("ttk::combobox::Post", str(self.combo))
        self.pump(30)
        self.assertTrue(self.dropdown.active)
        self.dropdown.close()
        self.assertFalse(self.dropdown.active)
        self.assertFalse(self.animator.is_running(("popdown", str(self.combo)+".popdown")))
        call("ttk::combobox::Unpost", str(self.combo))
        count = len(foreign)
        call("ttk::combobox::Post", str(self.combo))
        self.pump(40)
        self.assertGreater(len(foreign), count)
        self.assertFalse(self.dropdown.active)
        call("ttk::combobox::Unpost", str(self.combo))


if __name__ == "__main__":
    unittest.main()
