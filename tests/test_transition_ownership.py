from __future__ import annotations

import sys
import tkinter as tk
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
import unittest
from unittest.mock import patch

from app import transitions as tr
from core.animation import Animator
from tests.test_motion import pump


@unittest.skipUnless(sys.platform == "win32", "Windows native ownership")
class TransitionOwnershipTests(unittest.TestCase):
    def setUp(self):
        prepare_tk_test(self)
        self.root = tk.Tk()
        attach_tk_cleanup(self, self.root)
        self.root.geometry("480x320+20+20")
        self.root.update()
        self.animator = Animator(self.root)
        self.transition = tr.SnapshotTransition(self.root, self.animator)
        self.errors = []
        self.root.report_callback_exception = lambda k, v, tb: self.errors.append(str(v))

    def tearDown(self):
        self.transition.close()
        self.animator.stop()
        self.assertFalse(self.errors, self.errors)
        self.transition = self.animator = None

    def test_background_owner_never_creates_or_shows_cover(self):
        with patch.object(self.transition, "_owner_is_foreground", return_value=False):
            self.assertFalse(self.transition.cover(self.root))
        self.assertFalse(self.transition.active)
        self.assertEqual(self.transition._frame, 0)
        self.assertEqual(self.transition._bitmap, 0)

    def test_focus_loss_releases_cover_and_pending_reveal(self):
        with patch.object(self.transition, "_owner_is_foreground", return_value=True):
            self.assertTrue(self.transition.cover(self.root))
            self.transition.reveal(120, ready=lambda: False)
        with patch.object(self.transition, "_owner_is_foreground", return_value=False):
            self.transition._owner_focus_changed()
            pump(self.root, .05)
        self.assertFalse(self.transition.active)
        self.assertEqual(self.transition._bitmap, 0)
        self.assertIsNone(self.transition._job)

    def test_resize_cannot_raise_cover_above_foreign_foreground_window(self):
        with patch.object(self.transition, "_owner_is_foreground", return_value=True):
            self.assertTrue(self.transition.cover(self.root))
        with patch.object(self.transition, "_owner_is_foreground", return_value=False), patch.object(tr, "_move_window", wraps=tr._move_window) as move:
            self.transition._owner_changed()
            pump(self.root, .05)
        self.assertFalse(self.transition.active)
        self.assertFalse([call for call in move.call_args_list if call.kwargs.get("show")])

    def test_focus_loss_during_fade_cancels_animation_and_releases_bitmap(self):
        with patch.object(self.transition, "_owner_is_foreground", return_value=True):
            self.assertTrue(self.transition.cover(self.root))
            self.transition.reveal(200, ready=lambda: True)
            pump(self.root, .1)
            self.assertTrue(self.transition.active)
        with patch.object(self.transition, "_owner_is_foreground", return_value=False):
            pump(self.root, .05)
        self.assertFalse(self.transition.active)
        self.assertFalse(self.animator.is_running(self.transition._key))
        self.assertEqual(self.transition._bitmap, 0)

    def test_current_owner_chain_includes_owned_dialogs(self):
        with patch.object(tr._user32, "GetForegroundWindow", return_value=222), patch.object(tr._user32, "GetAncestor", return_value=111), patch.object(tr, "_frame_hwnd", return_value=111):
            self.assertTrue(self.transition._owner_is_foreground())
        with patch.object(tr._user32, "GetForegroundWindow", return_value=222), patch.object(tr._user32, "GetAncestor", return_value=333), patch.object(tr, "_frame_hwnd", return_value=111):
            self.assertFalse(self.transition._owner_is_foreground())

    def test_oversized_area_cover_stays_inside_owner_at_every_resize(self):
        canvas = tk.Canvas(self.root, width=900, height=700)
        canvas.place(x=-20, y=-10, width=900, height=700)
        self.root.update()
        try:
            with patch.object(self.transition, "_owner_is_foreground", return_value=True):
                self.assertTrue(self.transition.cover(canvas))
                for size in ("640x480", "320x240", "800x600"):
                    self.root.geometry(size)
                    pump(self.root, .03)
                    if not self.transition.active:
                        self.transition.cover(canvas)
                    with tr._physical_pixels():
                        rect = tr._bounded_client_rect(canvas, self.root)
                        owner = tr._client_rect(self.root)
                    self.assertIsNotNone(rect)
                    self.assertGreaterEqual(rect[0], owner[0])
                    self.assertGreaterEqual(rect[1], owner[1])
                    self.assertLessEqual(rect[0]+rect[2], owner[0]+owner[2])
                    self.assertLessEqual(rect[1]+rect[3], owner[1]+owner[3])
                    import ctypes
                    from ctypes import wintypes
                    actual = wintypes.RECT()
                    self.assertTrue(tr._user32.GetWindowRect(wintypes.HWND(self.transition._frame), ctypes.byref(actual)))
                    self.assertGreaterEqual(actual.left, owner[0])
                    self.assertGreaterEqual(actual.top, owner[1])
                    self.assertLessEqual(actual.right, owner[0]+owner[2])
                    self.assertLessEqual(actual.bottom, owner[1]+owner[3])
        finally:
            canvas.destroy()

    def test_unready_page_fallback_remains_bounded_and_releases_input_cover(self):
        with patch.object(self.transition, "_owner_is_foreground", return_value=True):
            self.assertTrue(self.transition.cover(self.root))
            self.transition.reveal(120, ready=lambda: False)
            pump(self.root, tr.COVER_PAINT_WAIT_S+.12)
        self.assertFalse(self.transition.active)
        self.assertEqual(self.transition._bitmap, 0)
        self.assertIsNone(self.transition._job)

    def test_resize_cancels_old_resolution_cover_and_releases_bitmap(self):
        with patch.object(self.transition, '_owner_is_foreground', return_value=True):
            self.assertTrue(self.transition.cover(self.root))
            self.root.geometry('650x430')
            pump(self.root, .06)
        self.assertFalse(self.transition.active)
        self.assertEqual(self.transition._bitmap, 0)
        self.assertIsNone(self.transition._job)

    def test_primed_cover_does_not_drain_page_idle_work(self):
        ran = []
        with patch.object(self.transition, '_owner_is_foreground', return_value=True):
            self.transition.prime()
        job = self.root.after_idle(lambda: ran.append(True))
        try:
            with patch.object(self.transition, '_owner_is_foreground', return_value=True):
                self.assertTrue(self.transition.cover(self.root))
            self.assertEqual(ran, [], 'first cover ran nested idle work')
        finally:
            self.root.after_cancel(job)

    def test_unready_navigation_calls_cancel_once_without_activating(self):
        activated, canceled = [], []
        with patch.object(self.transition, '_owner_is_foreground', return_value=True):
            self.transition.change(self.root, 120, lambda: None, ready=lambda: False,
                wait_s=.05, on_ready=lambda: activated.append(True), on_cancel=lambda: canceled.append(True))
            pump(self.root, .15)
            self.transition.finish()
        self.assertEqual(activated, [])
        self.assertEqual(canceled, [True])
        self.assertFalse(self.transition.active)

    def test_failed_activation_cannot_leave_an_input_cover(self):
        def fail():
            raise ValueError('activation failed')
        with patch.object(self.transition, '_owner_is_foreground', return_value=True), patch('app.transitions._native_paint_pending', return_value=False):
            self.transition.change(self.root, 60, lambda: None, ready=lambda: True,
                                   wait_s=.5, on_ready=fail)
            pump(self.root, .2)
        self.assertFalse(self.transition.active)
        self.assertEqual(self.transition._bitmap, 0)
        self.assertEqual(self.errors, ['activation failed'])
        self.errors.clear()

    def test_ready_navigation_activates_once_and_does_not_rollback_after_fade(self):
        activated, canceled = [], []
        with patch.object(self.transition, '_owner_is_foreground', return_value=True), patch('app.transitions._native_paint_pending', return_value=False):
            self.transition.change(self.root, 60, lambda: None, ready=lambda: True,
                wait_s=.5, on_ready=lambda: activated.append(True), on_cancel=lambda: canceled.append(True))
            pump(self.root, .3)
        self.assertEqual(activated, [True])
        self.assertEqual(canceled, [])


if __name__ == "__main__":
    unittest.main()
