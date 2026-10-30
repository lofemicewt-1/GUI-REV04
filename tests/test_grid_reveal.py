"""Grid disclosure must wait for real geometry and finish without stale work."""
import time
import tkinter as tk
from tkinter import ttk
import unittest

from core.animation import Animator
from core.ui_motion import GridReveal
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test


class GridRevealTests(unittest.TestCase):
    def setUp(self):
        prepare_tk_test(self)
        self.root = tk.Tk()
        attach_tk_cleanup(self, self.root)
        self.root.geometry("480x360")
        self.errors = []
        self.root.report_callback_exception = lambda k, v, tb: self.errors.append(str(v))
        self.animator = Animator(self.root)
        self.owner = ttk.Frame(self.root)
        self.owner.pack(fill="both", expand=True)
        self.owner.columnconfigure(0, weight=1)
        self.clip = ttk.Frame(self.owner)
        self.clip.grid(row=0, column=0, sticky="ew")
        self.clip.columnconfigure(0, weight=1)
        self.content = ttk.Frame(self.clip)
        self.content.grid(row=0, column=0, sticky="new")
        ttk.Label(self.content, text="Unchanged content", padding=60).grid()
        self.completed = []
        self.motion = GridReveal(self.clip, self.content, self.animator, owner=self.owner,
                                 on_settled=lambda: self.completed.append(self.motion.expanded))
        self.addCleanup(self.animator.stop)
        self.addCleanup(self.motion.close)
        self.root.update()

    def drive(self):
        deadline = time.perf_counter()+3
        stable = 0
        def poll():
            nonlocal stable
            stable = stable+1 if not self.motion.busy else 0
            if stable >= 2 or time.perf_counter() > deadline:
                self.root.quit()
            else:
                self.root.after(16, poll)
        self.root.after(16, poll)
        self.root.mainloop()
        self.assertFalse(self.motion.busy)
        self.assertFalse(self.errors, self.errors)

    def test_open_close_have_visible_middle_heights_and_single_completion(self):
        samples = []
        self.clip.bind("<Configure>", lambda e: samples.append(e.height), add="+")
        self.motion.set_expanded(True)
        self.drive()
        full = self.content.winfo_reqheight()
        self.assertTrue(any(1 < height < full-1 for height in samples), samples)
        self.assertEqual(self.clip.winfo_height(), full)
        samples.clear()
        self.motion.set_expanded(False)
        self.drive()
        self.assertTrue(any(1 < height < full-1 for height in samples), samples)
        self.assertFalse(self.clip.winfo_manager())
        self.assertEqual(self.completed, [True, False])

    def test_collapse_from_the_one_pixel_preparation_cannot_wait_forever(self):
        self.motion.set_expanded(True)
        self.root.update_idletasks()
        self.motion.set_expanded(False)
        self.root.update_idletasks()
        tween = self.animator._tweens[self.motion._key]
        tween.start -= 1
        self.animator._cancel_timer()
        self.animator._tick()
        self.drive()
        self.assertFalse(self.clip.winfo_manager())

    def test_busy_frame_disable_redirect_and_owner_hide_cancel_waits(self):
        self.motion.set_expanded(True)
        self.root.update_idletasks()
        tween = self.animator._tweens[self.motion._key]
        tween.start -= 1
        self.animator._cancel_timer()
        self.animator._tick()
        self.assertTrue(self.motion._waiting_frame)
        self.animator.set_enabled(False)
        self.drive()
        self.assertEqual(self.clip.winfo_height(), self.content.winfo_reqheight())
        for value in (False, True, False, True):
            self.motion.set_expanded(value)
        self.drive()
        self.animator.set_enabled(True)
        self.motion.set_expanded(False)
        self.root.update_idletasks()
        self.owner.pack_forget()
        self.root.update()
        self.assertFalse(self.motion.busy)
        self.assertFalse(self.clip.winfo_manager())

    def test_destroy_rejects_queued_or_stale_animation_steps(self):
        self.motion.set_expanded(True)
        self.root.update_idletasks()
        stale = self.animator._tweens[self.motion._key].step
        self.owner.destroy()
        stale(.5)
        self.motion.set_expanded(False)
        self.drive()
        self.motion.close()


if __name__ == "__main__":
    unittest.main()
