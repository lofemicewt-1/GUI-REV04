from __future__ import annotations

import math
import sys
import time
import tkinter as tk
from types import SimpleNamespace
from tkinter import ttk
import unittest
from unittest.mock import patch

from core import animation as anim
from core.lazy_pages import LazyPages
from core.ui_dispatch import UiDispatcher, post_to_ui


class ClockRoot:
    def __init__(self):
        self.jobs = {}
        self.next_id = 0

    def after(self, ms, callback):
        self.next_id += 1
        self.jobs[self.next_id] = callback
        return self.next_id

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def tick(self):
        jobs, self.jobs = self.jobs, {}
        for callback in jobs.values():
            callback()


class MotionClockTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.patcher = patch.object(anim.time, "perf_counter", lambda: self.clock)
        self.patcher.start()
        self.root = ClockRoot()
        self.a = anim.Animator(self.root)

    def tearDown(self):
        self.a.stop()
        self.patcher.stop()

    def test_busy_frame_catches_up_and_leaves_no_timer(self):
        seen, done = [], []
        self.a.animate("hover", anim.DUR_FAST, seen.append, done=lambda: done.append(True))
        self.clock = 0.3
        self.root.tick()
        self.assertEqual(seen, [0.0, 1.0])
        self.assertEqual(done, [True])
        self.assertFalse(self.root.jobs)

    def test_one_timer_and_replacement_never_calls_old_completion(self):
        old, current, done = [], [], []
        self.a.animate("k", 200, old.append, done=lambda: done.append("old"))
        self.a.animate("k", 120, current.append, done=lambda: done.append("new"))
        self.a.animate("other", 200, lambda p: None)
        self.assertEqual(len(self.root.jobs), 1)
        self.clock = 1
        self.root.tick()
        self.assertEqual(old, [0.0])
        self.assertEqual(current[-1], 1)
        self.assertEqual(done, ["new"])

    def test_disable_finishes_chained_jobs_synchronously(self):
        seen = []
        def chained():
            self.a.animate("k", 200, lambda p: seen.append(("second", p)))
        self.a.animate("k", 200, lambda p: seen.append(("first", p)), done=chained)
        self.a.set_enabled(False)
        self.assertEqual(seen[-2:], [("first", 1.0), ("second", 1.0)])
        self.assertFalse(self.a._tweens)
        self.assertFalse(self.root.jobs)

    def test_first_step_can_cancel_itself(self):
        self.a.animate("k", 200, lambda p: self.a.cancel("k"))
        self.assertFalse(self.root.jobs)

    def test_destroyed_target_does_not_stop_other_jobs(self):
        done, seen = [], []
        def gone(p):
            raise tk.TclError("destroyed")
        self.a.animate("gone", 200, gone, done=lambda: done.append(True))
        self.a.animate("live", 200, seen.append)
        self.clock = 1
        self.root.tick()
        self.assertEqual(done, [True])
        self.assertEqual(seen[-1], 1)

    def test_stop_prevents_late_rescheduling(self):
        self.a.stop()
        self.a.animate("late", 60, lambda p: self.fail("late callback"))
        self.assertFalse(self.root.jobs)


class MotionMathTests(unittest.TestCase):
    def test_reference_durations_and_easing_endpoints(self):
        self.assertEqual((anim.DUR_FAST, anim.DUR_SNAP, anim.DUR_BASE, anim.DUR_SHEET, anim.DUR_SLOW),
                         (60, 120, 200, 300, 450))
        for easing in (anim.EASE_OUT, anim.EASE_SNAP, anim.EASE_GRAPHEME,
                       anim.SPRING_BUTTON, anim.SPRING_CARD, anim.SPRING_FEEDBACK, anim.SPRING_DRAG):
            self.assertAlmostEqual(easing(0), 0)
            self.assertAlmostEqual(easing(1), 1)
            self.assertTrue(all(math.isfinite(easing(i/100)) for i in range(101)))
        peak_button = max(anim.SPRING_BUTTON(i/100) for i in range(101))
        peak_feedback = max(anim.SPRING_FEEDBACK(i/100) for i in range(101))
        self.assertGreater(peak_feedback, peak_button)
        self.assertLess(peak_button, 1.1)
        self.assertEqual(anim.lerp_color("#000", "#fff", 0.5), "#808080")


class LazyPageTests(unittest.TestCase):
    def test_registry_broadcasts_do_not_construct_pages(self):
        calls = []
        pages = LazyPages(lambda name, page: calls.append((name, page)))
        pages.register("one", lambda: object())
        pages.register("two", lambda: object())
        self.assertEqual(pages.keys(), ["one", "two"])
        self.assertEqual(pages.built_values(), [])
        page = pages["one"]
        self.assertIs(pages.get("one"), page)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(pages.values()), 2)
        self.assertEqual(len(calls), 2)

    def test_failed_construction_remains_retryable(self):
        calls = []
        def factory():
            calls.append(True)
            if len(calls) == 1:
                raise ValueError("failed")
            return object()
        pages = LazyPages(lambda *_: None)
        pages.register("one", factory)
        with self.assertRaises(ValueError):
            pages["one"]
        self.assertIn("one", pages)
        self.assertIsNotNone(pages["one"])


def pump(root, seconds):
    end = time.perf_counter()+seconds
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.002)


class TkMotionTests(unittest.TestCase):
    def setUp(self):
        from app.micro_anim import MicroAnimations
        # These tests exercise native surfaces rather than desktop activation.
        foreground = patch("app.transitions.SnapshotTransition._owner_is_foreground", return_value=True)
        foreground.start()
        self.addCleanup(foreground.stop)
        from styles.theme import get_colors
        self.root = tk.Tk()
        self.root.geometry("480x320+20+20")
        self.errors = []
        self.root.report_callback_exception = lambda k, v, tb: self.errors.append(str(v))
        self.animator = anim.Animator(self.root)
        self.micro = MicroAnimations(self.root, self.animator, lambda: get_colors("light"), lambda w: False)
        self.dispatcher = UiDispatcher(self.root).start()

    def tearDown(self):
        self.micro.close()
        self.animator.stop()
        self.dispatcher.close()
        self.root.update_idletasks()
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def test_popdown_opens_at_full_height_without_grow(self):
        combo = ttk.Combobox(self.root, values=[f"v{i}" for i in range(20)], state="readonly", height=5)
        combo.pack()
        combo.current(0)
        self.root.update()
        call = self.root.tk.call
        path = str(combo)+".popdown"
        lb = path+".f.l"
        heights = []

        def on_configure(event):
            if str(event.widget) == path:
                heights.append(int(event.height))

        self.root.bind_class("ComboboxPopdown", "<Configure>", on_configure, add="+")

        def assert_full(expect_top):
            # Deliver native Configure messages before checking the first one;
            # an idle-only drain does not dispatch Windows events.
            self.root.update()
            full_h = int(call("winfo", "reqheight", path))
            shown = int(call("winfo", "height", path))
            lb_h = int(call("winfo", "reqheight", lb))
            self.assertGreater(full_h, 8)
            self.assertGreaterEqual(full_h, lb_h)
            self.assertLessEqual(full_h - lb_h, 8)
            self.assertEqual(shown, full_h)
            opacity = float(call("wm", "attributes", path, "-alpha"))
            self.assertGreaterEqual(opacity, 0)
            self.assertLessEqual(opacity, 1)
            self.assertEqual(str(call("winfo", "manager", path+".f")), "grid")
            n = int(call(lb, "size"))
            top = round(float(call(lb, "yview")[0]) * n)
            self.assertEqual(top, expect_top)
            self.assertTrue(call(lb, "bbox", expect_top))
            return full_h

        call("ttk::combobox::Post", str(combo))
        full = assert_full(0)
        self.assertTrue(heights)
        self.assertEqual(heights[0], full)
        self.assertEqual(set(heights), {full})
        pump(self.root, 0.25)
        self.assertEqual(int(call("winfo", "height", path)), full)
        self.assertEqual(set(heights), {full})
        self.assertFalse(self.animator.is_running(("popdown", path)))
        self.assertEqual(float(call("wm", "attributes", path, "-alpha")), 1)
        self.animator.set_enabled(False)
        self.micro.reset()
        self.assertEqual(int(call("winfo", "height", path)), full)
        self.assertEqual(float(call("wm", "attributes", path, "-alpha")), 1)
        self.assertEqual(str(call("winfo", "manager", path+".f")), "grid")
        call("ttk::combobox::Unpost", str(combo))
        self.root.update()
        self.animator.set_enabled(True)

        before = list(heights)
        call("ttk::combobox::Post", str(combo))
        self.assertEqual(assert_full(0), full)
        self.assertTrue(all(height == full for height in heights[len(before):]))
        call("ttk::combobox::Unpost", str(combo))
        self.root.update()

        combo.current(17)
        self.root.update()
        call("ttk::combobox::Post", str(combo))
        n = int(call(lb, "size"))
        lines = int(call(lb, "cget", "-height"))
        self.assertEqual(assert_full(min(17, n - lines)), full)
        self.assertTrue(call(lb, "bbox", 17))
        self.assertEqual(set(heights), {full})
        call("ttk::combobox::Unpost", str(combo))

    def test_popdown_fades_as_one_surface_and_cancels_on_unpost(self):
        combo = ttk.Combobox(self.root, values=[f"v{i}" for i in range(20)], state="readonly", height=5)
        combo.pack()
        combo.current(0)
        self.root.update()
        call = self.root.tk.call
        path = str(combo)+".popdown"
        key = ("popdown", path)
        alpha = lambda: float(call("wm", "attributes", path, "-alpha"))
        for _ in range(2):
            call("ttk::combobox::Post", str(combo))
            self.root.update()
            self.assertTrue(self.animator.is_running(key), "dropdown entrance animation is missing")
            self.assertLess(alpha(), 1)
            full = int(call("winfo", "reqheight", path))
            self.assertEqual(int(call("winfo", "height", path)), full)
            pump(self.root, .05)
            self.assertGreater(alpha(), 0)
            self.assertLess(alpha(), 1)
            self.assertEqual(int(call("winfo", "height", path)), full)
            call("ttk::combobox::Unpost", str(combo))
            self.root.update()
            self.assertFalse(self.animator.is_running(key))
            self.assertEqual(alpha(), 1)
        call("ttk::combobox::Post", str(combo))
        pump(self.root, .26)
        self.assertFalse(self.animator.is_running(key))
        self.assertEqual(alpha(), 1)
        call("ttk::combobox::Unpost", str(combo))
        self.animator.set_enabled(False)
        self.micro.reset()
        call("ttk::combobox::Post", str(combo))
        self.root.update()
        self.assertFalse(self.animator.is_running(key))
        self.assertEqual(alpha(), 1)
        call("ttk::combobox::Unpost", str(combo))

    def test_popdown_reset_and_destroy_release_animation(self):
        combo = ttk.Combobox(self.root, values=['one', 'two'], state="readonly")
        combo.pack()
        self.root.update()
        call = self.root.tk.call
        path = str(combo)+".popdown"
        key = ("popdown", path)
        call("ttk::combobox::Post", str(combo))
        pump(self.root, .03)
        self.assertTrue(self.animator.is_running(key))
        self.micro.reset()
        self.assertFalse(self.animator.is_running(key))
        self.assertEqual(float(call("wm", "attributes", path, "-alpha")), 1)
        self.assertEqual(self.micro._popdowns, set())
        call("ttk::combobox::Unpost", str(combo))
        call("ttk::combobox::Post", str(combo))
        pump(self.root, .03)
        self.assertTrue(self.animator.is_running(key))
        combo.destroy()
        pump(self.root, .05)
        self.assertFalse(self.animator.is_running(key))
        self.assertEqual(self.micro._popdowns, set())

    def test_popdown_withdrawn_before_map_delivery_does_not_animate_hidden_window(self):
        combo = ttk.Combobox(self.root, values=['one', 'two'], state="readonly")
        combo.pack()
        self.root.update()
        call = self.root.tk.call
        path = str(combo)+".popdown"
        call("ttk::combobox::Post", str(combo))
        call("ttk::combobox::Unpost", str(combo))
        self.root.update()
        self.assertFalse(int(call("winfo", "ismapped", path)))
        self.assertFalse(self.animator.is_running(("popdown", path)))
        self.assertEqual(float(call("wm", "attributes", path, "-alpha")), 1)

    def test_worker_callback_is_on_main_thread_and_late_post_is_dropped(self):
        import threading
        seen = []
        worker = threading.Thread(target=lambda: post_to_ui(self.root, lambda: seen.append(threading.current_thread())))
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        pump(self.root, 0.06)
        self.assertEqual(seen, [threading.main_thread()])
        self.dispatcher.close()
        post_to_ui(self.root, lambda: self.fail("late callback"))
        self.assertEqual(self.dispatcher.pending(), 0)

    def test_dispatch_time_budget_is_checked_after_every_callback(self):
        for _ in range(5):
            self.dispatcher.post(time.sleep, 0.02)
        self.assertEqual(self.dispatcher.drain(), 1)
        self.assertEqual(self.dispatcher.pending(), 4)

    def test_large_panel_finishes_when_motion_is_disabled(self):
        window = tk.Toplevel(self.root)
        self.root.update()
        self.assertTrue(self.animator.is_running(("panel", str(window))))
        self.animator.set_enabled(False)
        self.micro.reset()
        self.assertEqual(float(window.attributes("-alpha")), 1)
        self.assertFalse(self.micro._panels)
        window.destroy()

    @unittest.skipUnless(sys.platform == "win32", "Windows native transition")
    def test_native_cover_works_releases_bitmap_and_preserves_ctypes_signatures(self):
        import ctypes
        from app.transitions import SnapshotTransition
        before = ctypes.windll.gdi32.CreateCompatibleBitmap.restype
        self.root.update()
        transition = SnapshotTransition(self.root, self.animator)
        try:
            self.root.configure(background="#123456")
            self.root.update()
            self.assertTrue(transition.cover(self.root))
            self.assertTrue(transition._bitmap)
            from ctypes import wintypes
            from app.transitions import _user32, _gdi32
            _user32.GetDC.restype = wintypes.HDC
            _gdi32.GetPixel.restype = wintypes.DWORD
            dc = _user32.GetDC(wintypes.HWND(transition._client))
            try:
                self.assertEqual(_gdi32.GetPixel(wintypes.HDC(dc), 30, 30), 0x563412)
            finally:
                _user32.ReleaseDC(wintypes.HWND(transition._client), wintypes.HDC(dc))
            from app.transitions import _client_rect, _physical_pixels
            _user32.WindowFromPoint.argtypes = [wintypes.POINT]
            _user32.WindowFromPoint.restype = wintypes.HWND
            _user32.GetAncestor.restype = wintypes.HWND
            with _physical_pixels():
                x, y, _w, _h = _client_rect(self.root)
                hit = _user32.WindowFromPoint(wintypes.POINT(x+30, y+30))
                self.assertEqual(_user32.GetAncestor(hit, 2), transition._frame,
                                 "old-page cover must not send clicks to unseen new controls")
            transition.reveal(anim.DUR_SNAP)
            pump(self.root, 0.25)
            self.assertFalse(transition.active)
            self.assertEqual(transition._bitmap, 0)
            self.assertIs(ctypes.windll.gdi32.CreateCompatibleBitmap.restype, before)
            transition.cover(self.root)
            transition.close()
            self.assertEqual(transition._frame, 0)
        finally:
            transition.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows native transition")
    def test_live_log_exposes_do_not_hold_the_page_cover_until_timeout(self):
        from app.transitions import SnapshotTransition
        text = tk.Text(self.root, width=30, height=3)
        text.pack()
        pump(self.root, .05)
        transition = SnapshotTransition(self.root, self.animator)
        job = [None]
        def update_log():
            text.insert("end", "RX status update\n")
            text.see("end")
            text.event_generate("<Expose>")
            job[0] = self.root.after(10, update_log)
        try:
            self.assertTrue(transition.cover(self.root))
            update_log()
            transition.reveal(anim.DUR_SNAP, ready=lambda: bool(text.winfo_ismapped()))
            pump(self.root, .45)
            self.assertFalse(transition.active, "live log paints kept an already laid-out page hidden")
        finally:
            if job[0] is not None:
                self.root.after_cancel(job[0])
            transition.close()
            text.destroy()

    def test_direct_root_destroy_cancels_worker_and_animation_timers(self):
        from app.transitions import SnapshotTransition
        root = tk.Tk()
        a = anim.Animator(root)
        d = UiDispatcher(root).start()
        transition = SnapshotTransition(root, a)
        root.update_idletasks()
        a.animate("k", 200, lambda p: None)
        root.after_idle(lambda: None)
        root.destroy()
        self.assertTrue(d.closed)
        self.assertTrue(a._stopped)
        self.assertEqual(root.tk.call("after", "info"), "")


if __name__ == "__main__":
    unittest.main()
