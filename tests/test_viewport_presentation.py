import ctypes as c
from ctypes import wintypes as w
import sys
import time
import tkinter as tk
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

    def test_scroll_cover_is_composed_before_live_content_moves(self):
        canvas = self.page.mode_scroll.canvas
        observed = []
        observer = canvas._view_observer
        with patch.object(native, "_dwm_flush", wraps=native._dwm_flush) as composed:
            def moved(axis):
                observed.append((self.app.transitions.active, composed.call_count))
                observer(axis)
            with patch.object(canvas, "_view_observer", side_effect=moved):
                canvas.yview_moveto(.4)
            self.assertTrue(observed)
            self.assertTrue(observed[0][0])
            self.assertGreaterEqual(observed[0][1], 2,
                                    "live controls moved before the opaque cover reached the compositor")
        self.wait_ready()

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

    def layout_tween(self):
        area = self.page.mode_scroll
        owner = tk.Frame(area, width=20, height=20)
        owner.place(x=10, y=10)
        pump(self.root, .05)
        self.app.set_animations(True)
        key = ('layout-test', id(owner))
        self.app.animator.animate(key, 2000, lambda _p: None)
        return area, owner, key

    def test_layout_frame_covers_before_mutation_and_resumes_remaining_time(self):
        area, owner, key = self.layout_tween()
        observed = []
        with (patch.object(native, '_dwm_flush', wraps=native._dwm_flush) as flush,
              patch.object(native, '_grab_bitmap', wraps=native._grab_bitmap) as capture):
            def change():
                observed.append((self.app.transitions.active, flush.call_count))
                owner.configure(height=35)
            self.presenter.present_layout(area, owner, key, change)
        self.assertEqual(capture.call_args.args[0], 0)
        self.assertTrue(capture.call_args.kwargs['include_layered'])
        with native._physical_pixels():
            rect = native._bounded_client_rect(area, self.root)
        self.assertEqual(capture.call_args.args[1:], rect[2:])
        self.assertEqual(capture.call_args.kwargs['source_x'], rect[0])
        self.assertEqual(capture.call_args.kwargs['source_y'], rect[1])
        tween = self.app.animator._tweens[key]
        self.assertEqual(observed[0][0], True)
        self.assertGreaterEqual(observed[0][1], 2)
        self.assertIs(self.app.transitions._area, area)
        self.assertIsNotNone(tween.suspended_at)
        started = tween.start
        self.wait_ready()
        self.assertIsNone(tween.suspended_at)
        self.assertGreater(tween.start, started)
        self.assertTrue(self.app.animator.is_running(key))
        self.assertIsNone(self.presenter._layout_job)
        self.assertFalse(self.presenter._layout_keys)

    def test_layout_requests_wait_for_foreign_cover_and_keep_latest(self):
        area, owner, key = self.layout_tween()
        self.assertTrue(self.app.transitions.cover(self.app.navigation.content_shell, force=True))
        seen = []
        self.presenter.present_layout(area, owner, key, lambda: seen.append('obsolete'))
        self.presenter.present_layout(area, owner, key, lambda: seen.append('latest'))
        pump(self.root, .04)
        self.assertEqual(seen, [])
        self.assertEqual(len(self.presenter._layout_queue), 1)
        self.app.transitions.finish()
        pump(self.root, .1)
        self.wait_ready()
        self.assertEqual(seen, ['latest'])
        self.assertIsNone(self.app.animator._tweens[key].suspended_at)
        self.assertFalse(self.presenter._layout_queue)

    def test_layout_invalidates_after_configure_quiet_instead_of_before_move(self):
        area, owner, key = self.layout_tween()
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=35))
        self.app.transitions._paint_activity = time.perf_counter()+.05
        intervals = []
        original = native._invalidate_native_area
        def invalidate(area):
            intervals.append(time.perf_counter()-self.app.transitions._paint_activity)
            original(area)
        with patch.object(native, '_invalidate_native_area', side_effect=invalidate):
            self.wait_ready()
        self.assertTrue(intervals)
        self.assertGreaterEqual(min(intervals), .016)

    def test_expose_delays_only_owned_layout_frames_and_binding_is_removed(self):
        area, owner, key = self.layout_tween()
        event = type('Expose', (), {'widget': owner})()
        self.app.transitions._paint_activity = 0
        self.presenter._layout_exposed(event)
        self.assertEqual(self.app.transitions._paint_activity, 0)
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=35))
        self.app.transitions._paint_activity = 0
        self.presenter._layout_exposed(event)
        self.assertGreater(self.app.transitions._paint_activity, 0)
        self.wait_ready()
        self.presenter.close()
        self.assertIsNone(self.presenter._layout_expose_binding)

    def test_redirect_repaints_latest_geometry_without_recapturing_partial_frame(self):
        area, owner, key = self.layout_tween()
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=35))
        pump(self.root, .02)
        token, bitmap = self.app.transitions._token, self.app.transitions._bitmap
        with (patch.object(native, '_grab_bitmap', wraps=native._grab_bitmap) as capture,
              patch.object(native, '_invalidate_native_area', wraps=native._invalidate_native_area) as redraw):
            self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=45))
            self.assertGreater(self.app.transitions._token, token)
            self.assertEqual(self.app.transitions._bitmap, bitmap)
            self.wait_ready()
        capture.assert_not_called()
        self.assertGreaterEqual(redraw.call_count, 2, 'new geometry reused an obsolete paint barrier')
        self.assertEqual(owner.winfo_reqheight(), 45)
        self.assertIsNone(self.app.animator._tweens[key].suspended_at)

    def test_live_text_exposes_do_not_restart_layout_quiet_time(self):
        area, owner, key = self.layout_tween()
        log = tk.Text(area.content, height=2)
        log.place(x=0, y=0, width=100, height=40)
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=35))
        self.app.transitions._paint_activity = 0
        self.presenter._layout_exposed(type('Expose', (), {'widget': log})())
        self.assertEqual(self.app.transitions._paint_activity, 0,
                         'continuous fixed-size log content must not exhaust the layout paint budget')
        self.presenter._layout_exposed(type('Expose', (), {'widget': owner})())
        self.assertGreater(self.app.transitions._paint_activity, 0)
        self.wait_ready()

    def test_redirect_gives_latest_geometry_a_fresh_paint_budget(self):
        area, owner, key = self.layout_tween()
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=35))
        bitmap = self.app.transitions._bitmap
        self.presenter._layout_deadline = time.perf_counter()-.01
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=45))
        self.assertGreater(self.presenter._layout_deadline-time.perf_counter(), 1,
                           'a redirect must not reveal new geometry using an expired old-frame budget')
        self.assertEqual(self.app.transitions._bitmap, bitmap)
        self.wait_ready()

    def test_late_scroll_region_reflow_is_covered_before_automatic_endpoint_move(self):
        area, owner, key = self.layout_tween()
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=35))
        self.wait_ready()
        self.app.animator.cancel(key)
        observed = []
        original = area.canvas.configure
        def configure(*a, **kw):
            if 'scrollregion' in kw:
                observed.append(self.app.transitions.active)
            return original(*a, **kw)
        area.scroll_region_extra += 71
        with patch.object(area.canvas, 'configure', side_effect=configure):
            area._sync_scroll_region()
        self.assertEqual(observed, [True], 'late layout moved native children outside the frame cover')
        self.wait_ready()

    def test_layout_hides_hover_surface_and_does_not_reopen_it_during_motion(self):
        from pages.base_page import SimpleTooltip
        area, owner, key = self.layout_tween()
        tip = SimpleTooltip(owner, 'ADC help')
        tip._show(None)
        self.assertIsNotNone(tip.tip)
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=35))
        self.assertIsNone(tip.tip)
        tip._show(None)
        self.assertIsNone(tip.tip)
        self.wait_ready()
        tip._show(None)
        self.assertIsNotNone(tip.tip)
        tip._hide()

    def test_finished_disclosure_does_not_keep_covering_unrelated_reflows(self):
        area, owner, key = self.layout_tween()
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=35))
        self.wait_ready()
        self.app.animator.cancel(key)
        self.presenter._layout_scopes[area][1] = 0
        observed = []
        self.presenter.layout_geometry(area, lambda: observed.append(self.app.transitions.active))
        self.assertEqual(observed, [False])
        self.assertNotIn(area, self.presenter._layout_scopes)

    def test_destroy_discards_deferred_layout_and_does_not_abort_foreign_cover(self):
        area, owner, key = self.layout_tween()
        self.assertTrue(self.app.transitions.cover(self.app.navigation.content_shell, force=True))
        self.presenter.present_layout(area, owner, key, lambda: self.fail('destroyed callback'))
        owner.destroy()
        pump(self.root, .04)
        self.assertTrue(self.app.transitions.active)
        self.assertFalse(self.presenter._layout_queue)
        self.assertIsNone(self.app.animator._tweens[key].suspended_at)
        self.assertIsNone(self.presenter._layout_job)
        self.assertNotIn(owner, self.presenter._layout_owner_bindings)
        self.app.transitions.finish()

    def test_hide_and_focus_loss_release_layout_masks_and_timers(self):
        area, owner, key = self.layout_tween()
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=35))
        owner.place_forget()
        pump(self.root, .04)
        self.assertFalse(self.app.transitions.active)
        self.assertIsNone(self.app.animator._tweens[key].suspended_at)
        owner.place(x=10, y=10)
        pump(self.root, .04)
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=40))
        with patch.object(self.app.transitions, '_owner_is_foreground', return_value=False):
            self.app.transitions._owner_focus_changed()
        self.assertFalse(self.app.transitions.active)
        self.assertIsNone(self.app.animator._tweens[key].suspended_at)
        self.assertIsNone(self.presenter._layout_job)

    def test_disable_and_close_release_layout_and_queued_requests(self):
        area, owner, key = self.layout_tween()
        self.presenter.present_layout(area, owner, key, lambda: owner.configure(height=35))
        self.app.set_animations(False)
        self.wait_ready()
        self.assertFalse(self.app.animator.is_running(key))
        self.assertFalse(self.presenter._layout_keys)
        self.app.set_animations(True)
        self.app.animator.animate(key, 2000, lambda _p: None)
        self.assertTrue(self.app.transitions.cover(self.app.navigation.content_shell, force=True))
        self.presenter.present_layout(area, owner, key, lambda: self.fail('closed callback'))
        self.presenter.close()
        pump(self.root, .04)
        self.assertFalse(self.presenter._layout_queue)
        self.assertIsNone(self.presenter._layout_job)
        self.assertIsNone(self.app.animator._tweens[key].suspended_at)
