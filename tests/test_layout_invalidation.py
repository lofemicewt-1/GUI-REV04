from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.test_motion import pump
from tests.validate_responsive_layout import mocked_application


class LayoutInvalidationTests(unittest.TestCase):
    def test_warm_navigation_keeps_cached_widgets_and_waits_for_active_mode_layout(self):
        with mocked_application() as app:
            app.set_animations(False)
            root, nav = app.root, app.navigation
            nav.show("awp1921_1923")
            pump(root, .3)
            host = dict.__getitem__(nav.pages, "awp1921_1923")
            button_page = dict.__getitem__(host.mode_pages, "button_mode")
            self.assertTrue(nav._page_layout_ready())

            button_page._button_adc_diagram_job = "pending-diagram-job"
            self.assertFalse(nav._page_layout_ready())
            button_page._button_adc_diagram_job = None
            self.assertTrue(nav._page_layout_ready())

            nav.show("awp1018")
            pump(root, .3)
            nav.show("awp1921_1923")
            pump(root, .3)
            self.assertIs(dict.__getitem__(nav.pages, "awp1921_1923"), host)
            self.assertIs(dict.__getitem__(host.mode_pages, "button_mode"), button_page)

    def test_192x_preview_redraws_coalesce_and_cancel(self):
        with mocked_application() as app:
            app.set_animations(False)
            root, nav = app.root, app.navigation
            nav.show("awp1921_1923")
            pump(root, .3)
            host = dict.__getitem__(nav.pages, "awp1921_1923")
            button_page = dict.__getitem__(host.mode_pages, "button_mode")
            waveform_page = button_page.waveform_page

            with patch.object(waveform_page, "_redraw_active_plot", wraps=waveform_page._redraw_active_plot) as preview:
                for _ in range(12):
                    waveform_page._queue_plot_redraw()
                pump(root, .1)
            self.assertEqual(preview.call_count, 1)

            errors = []
            root.report_callback_exception = lambda kind, value, tb: errors.append(repr(value))
            waveform_page._queue_plot_redraw()
            pending = waveform_page._preview_job
            self.assertIsNotNone(pending)
            button_page.destroy()
            root.update()
            self.assertNotIn(pending, root.tk.splitlist(root.tk.call("after", "info")))
            self.assertEqual(errors, [])

    def test_warm_chip_switch_does_not_resize_an_unchanged_canvas_window(self):
        with mocked_application() as app:
            app.set_animations(False)
            nav = app.navigation
            app.root.geometry("1700x980+0+0")
            nav.show("awp1221")
            pump(app.root, .3)
            nav.show("awp1018")
            pump(app.root, .3)
            before = tuple(nav.content_canvas.itemcget(nav.content_window, key) for key in ("width", "height"))
            with patch.object(nav.content_canvas, "itemconfigure", wraps=nav.content_canvas.itemconfigure) as resize:
                nav.show("awp1221")
                pump(app.root, .3)
            after = tuple(nav.content_canvas.itemcget(nav.content_window, key) for key in ("width", "height"))
            self.assertEqual(before, after)
            self.assertFalse(resize.call_args_list, "switching equally sized pages invalidated the entire canvas window")

    def test_wrapping_footer_stabilizes_after_large_window_restore(self):
        from app.transitions import _native_paint_pending
        import time
        with mocked_application() as app:
            app.set_animations(False)
            root, nav = app.root, app.navigation
            root.geometry("1700x980+0+0")
            nav.show("awp1221")
            root.after(1800, root.quit)
            root.mainloop()
            root.state("zoomed")
            root.after(2200, root.quit)
            root.mainloop()
            root.state("normal")
            root.geometry("1200x800")
            events = []
            since = time.perf_counter()
            footer = nav.pages["awp1221"].footer_scroll
            footer.content.bind("<Configure>", lambda e: events.append(time.perf_counter()-since), add="+")
            root.after(2800, root.quit)
            root.mainloop()
            self.assertFalse([t for t in events if t > 2.0], events)
            self.assertFalse(_native_paint_pending(nav.content_canvas))
            self.assertTrue(nav._page_layout_ready())
            self.assertLessEqual(footer.winfo_rooty()-root.winfo_rooty()+footer.winfo_height(), root.winfo_height())

    def test_deferred_visible_scroll_layout_blocks_page_readiness(self):
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show("awp1221")
            pump(app.root, .3)
            area = app.navigation.pages["awp1221"].mode_scroll
            area._queue_scroll_layout()
            self.assertFalse(app.navigation._page_layout_ready())
            pump(app.root, .1)
            self.assertTrue(app.navigation._page_layout_ready())
            self.assertNotIn(area, app.root._piezo_pending_scroll_layouts)

    def test_destroy_releases_queued_layout_and_registry_entry(self):
        from pages.base_page import VerticalScrollArea
        with mocked_application() as app:
            area = VerticalScrollArea(app.root, app.ctx)
            area._queue_scroll_layout()
            job = area._layout_job
            self.assertIn(area, app.root._piezo_pending_scroll_layouts)
            area.destroy()
            self.assertNotIn(area, app.root._piezo_pending_scroll_layouts)
            self.assertNotIn(job, app.root.tk.splitlist(app.root.tk.call("after", "info")))

    def test_unchanged_scroll_layout_does_not_invalidate_its_canvas(self):
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show("awp1221")
            pump(app.root, .3)
            area = app.navigation.pages["awp1221"].mode_scroll
            area._sync_scroll_region()
            pump(app.root, .1)
            with patch.object(area.canvas, "configure", wraps=area.canvas.configure) as redraw:
                area._sync_scroll_region()
                area._sync_scroll_region()
            writes = [call for call in redraw.call_args_list if "scrollregion" in call.kwargs]
            self.assertFalse(writes, "unchanged layout repeatedly invalidated the scroll canvas")


if __name__ == "__main__":
    unittest.main()
