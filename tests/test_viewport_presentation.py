import ctypes as c
from ctypes import wintypes as w
import sys
import time
import unittest
from unittest.mock import patch

from tests.test_motion import pump
from tests.validate_responsive_layout import mocked_application
from app import transitions as native


@unittest.skipUnless(sys.platform == 'win32', 'Windows native presentation')
class ViewportPresentationTests(unittest.TestCase):
    def setUp(self):
        self.foreground = patch.object(native.SnapshotTransition, '_owner_is_foreground', return_value=True)
        self.foreground.start()
        self.addCleanup(self.foreground.stop)
        self.context = mocked_application()
        self.app = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.app.set_animations(False)
        self.root = self.app.root
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(str(args))
        self.root.geometry('1400x850+20+20')
        self.app.navigation.show('awp1221')
        pump(self.root, .4)
        self.presenter = self.app.viewport_presentation
        button = patch.object(self.presenter, '_button_down', return_value=True)
        button.start()
        self.addCleanup(button.stop)
        self.page = self.app.navigation.pages['awp1221']
        self.addCleanup(lambda: self.assertEqual(self.errors, []))

    def wait_ready(self):
        deadline = time.perf_counter()+3
        while self.app.transitions.active and time.perf_counter() < deadline:
            pump(self.root, .03)
        self.assertFalse(self.app.transitions.active)

    def test_resize_keeps_page_and_bitmap_and_crops_to_owner(self):
        surface = self.app.transitions
        self.assertTrue(self.presenter.begin_resize())
        bitmap = surface._bitmap
        size = surface._bitmap_size
        self.assertFalse(self.app.navigation.content_shell.winfo_ismapped())
        for geometry in ('1100x700', '1550x920', '1200x800'):
            self.root.geometry(geometry)
            pump(self.root, .08)
            self.assertTrue(surface.active)
            self.assertEqual(surface._bitmap, bitmap)
            self.assertEqual(surface._bitmap_size, size)
            with native._physical_pixels():
                owner, overlay = native._client_rect(self.root), native._client_rect(surface._overlay)
            self.assertGreaterEqual(overlay[0], owner[0])
            self.assertGreaterEqual(overlay[1], owner[1])
            self.assertLessEqual(overlay[0]+overlay[2], owner[0]+owner[2])
            self.assertLessEqual(overlay[1]+overlay[3], owner[1]+owner[3])
        self.presenter.end_resize()
        self.wait_ready()
        self.assertTrue(self.app.navigation.content_shell.winfo_ismapped())
        self.assertTrue(self.app.navigation._page_layout_ready())
        self.assertIs(self.app.navigation.pages['awp1221'], self.page)
        self.assertEqual(surface._bitmap, 0)
        self.assertIsNone(surface._resize_offset)

    def test_resize_abort_and_animation_disable_restore_content(self):
        self.assertTrue(self.presenter.begin_resize())
        self.app.set_animations(False)
        pump(self.root, .1)
        self.assertFalse(self.presenter.resizing)
        self.assertTrue(self.app.navigation.content_shell.winfo_ismapped())
        self.assertFalse(self.app.transitions.active)

    def test_scroll_presents_whole_frame_without_fade_and_passes_input(self):
        area = self.page.mode_scroll
        before = area.canvas.yview()
        area.canvas.yview_moveto(.5)
        self.assertTrue(self.app.transitions.active)
        self.assertIs(self.presenter._scroll_canvas, area.canvas)
        api = c.WinDLL('user32')
        api.GetWindowLongW.argtypes = [w.HWND, c.c_int]
        self.assertTrue(api.GetWindowLongW(self.app.transitions._frame, -20) & 0x20)
        self.assertIs(self.presenter.pointer_target(self.app.transitions._overlay), area.canvas)
        event = type('Wheel', (), dict(x_root=50, y_root=100, widget=self.app.transitions._overlay))()
        with patch.object(area, 'winfo_containing', return_value=self.app.transitions._overlay):
            self.assertIs(area._pointer_widget(event), area.canvas)
        area.canvas.yview_scroll(-32, 'units')
        self.wait_ready()
        self.assertNotEqual(area.canvas.yview(), before)
        self.assertIsNone(self.presenter._scroll_canvas)
        self.assertFalse(self.app.animator.is_running(self.app.transitions._key))

    def test_only_size_changes_with_resize_input_start_freeze(self):
        with patch.object(self.presenter, '_native_resize_input', return_value=True):
            self.root.geometry('+50+50')
            pump(self.root, .03)
            self.assertFalse(self.presenter.resizing)
            self.root.geometry('1200x800')
            pump(self.root, .08)
            self.assertTrue(self.presenter.resizing)
            with patch.object(self.presenter, '_button_down', return_value=False):
                pump(self.root, .08)
                self.wait_ready()
            self.assertFalse(self.presenter.resizing)

    def test_destroying_scroll_canvas_releases_mask(self):
        area = self.page.mode_scroll
        area.canvas.yview_moveto(.4)
        self.assertTrue(self.app.transitions.active)
        area.canvas.yview_scroll(5, 'units')
        self.assertTrue(self.presenter._scroll_queue)
        area.canvas.destroy()
        pump(self.root, .05)
        self.assertFalse(self.app.transitions.active)
        self.assertIsNone(self.presenter._scroll_canvas)
        self.assertEqual(self.presenter._scroll_queue, [])

    def test_repeated_scroll_keeps_original_frame_deadline(self):
        area = self.page.mode_scroll
        area.canvas.yview_moveto(.4)
        job = self.app.transitions._job
        token = self.app.transitions._token
        for _ in range(8):
            area.canvas.yview_scroll(1, 'units')
        self.assertEqual(self.app.transitions._job, job)
        self.assertEqual(self.app.transitions._token, token)
        self.wait_ready()

    def test_queued_scroll_queries_preserve_order_and_coordinates(self):
        canvas = self.page.mode_scroll.canvas
        def commands():
            canvas.yview_moveto(.2)
            canvas.yview_scroll(32, 'units')
            canvas.yview_moveto(.4)
            canvas.yview_scroll(-16, 'units')
        with patch.object(self.presenter, 'scroll', side_effect=lambda _canvas, operation: operation()):
            commands()
            expected_view, expected_y = canvas.yview(), canvas.canvasy(0)
        commands()
        self.assertTrue(self.presenter._scroll_queue)
        self.assertEqual(canvas.canvasy(0), expected_y)
        self.assertEqual(canvas.yview(), expected_view)
        self.assertEqual(self.presenter._scroll_queue, [])
        self.wait_ready()

    def test_close_discards_queued_scroll_and_cancels_its_job(self):
        canvas = self.page.mode_scroll.canvas
        canvas.yview_moveto(.3)
        canvas.yview_scroll(12, 'units')
        self.assertTrue(self.presenter._scroll_queue)
        self.presenter.close()
        pump(self.root, .08)
        self.assertEqual(self.presenter._scroll_queue, [])
        self.assertIsNone(self.presenter._scroll_job)
        self.assertFalse(self.app.transitions.active)

    def test_withdraw_returns_live_page_without_a_mask(self):
        self.assertTrue(self.presenter.begin_resize())
        self.root.withdraw()
        pump(self.root, .03)
        self.assertFalse(self.presenter.resizing)
        self.assertFalse(self.app.transitions.active)
        self.root.deiconify()
        pump(self.root, .2)
        self.assertTrue(self.app.navigation.content_shell.winfo_ismapped())
        self.assertIs(self.app.navigation.pages['awp1221'], self.page)

    def test_focus_loss_aborts_resize_and_restores_controls(self):
        self.assertTrue(self.presenter.begin_resize())
        with patch.object(self.app.transitions, '_owner_is_foreground', return_value=False):
            self.app.transitions._owner_focus_changed()
        pump(self.root, .1)
        self.assertFalse(self.presenter.resizing)
        self.assertFalse(self.app.transitions.active)
        self.assertTrue(self.app.navigation.content_shell.winfo_ismapped())

    def test_close_cancels_resize_check_and_root_bindings(self):
        self.assertTrue(self.presenter.begin_resize())
        self.presenter.close()
        pump(self.root, .08)
        self.assertIsNone(self.presenter._resize_check)
        self.assertIsNone(self.presenter._prime_job)
        self.assertFalse(self.root.bind_class(self.presenter._tag, '<Configure>'))
        self.assertFalse(self.app.transitions.active)
        self.assertTrue(self.app.navigation.content_shell.winfo_ismapped())
