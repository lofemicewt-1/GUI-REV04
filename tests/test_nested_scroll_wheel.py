"""Wheel input must leave a nested viewport that cannot scroll further."""

import faulthandler
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import traceback
import time
import unittest
from unittest.mock import patch

from pages.base_page import VerticalScrollArea
from styles.theme import apply_theme
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
from tests.validate_responsive_layout import mocked_application


def pump(root, milliseconds=100):
    root.after(milliseconds, root.quit)
    root.mainloop()


def wheel(target, delta):
    target.event_generate(
        "<MouseWheel>", delta=delta, x=10, y=10,
        rootx=target.winfo_rootx() + 10,
        rooty=target.winfo_rooty() + 10, when="now",
    )


class NestedScrollWheelTests(unittest.TestCase):
    def setUp(self):
        prepare_tk_test(self)
        self.root = tk.Tk()
        attach_tk_cleanup(self, self.root)
        self.root.geometry("640x480+0+0")
        ctx = SimpleNamespace(colors=apply_theme(self.root, "light", 10),
                              state=SimpleNamespace(ui_scale=1.0))
        self.outer = VerticalScrollArea(self.root, ctx)
        self.outer.pack(fill="both", expand=True)
        self.inner = VerticalScrollArea(self.outer.content, ctx, scroll_region_extra=0)
        self.inner.grid(row=0, column=0, sticky="ew")
        self.inner.canvas.configure(height=180)
        self.body = ttk.Frame(self.inner.content, width=800, height=180)
        self.body.grid(row=0, column=0)
        ttk.Frame(self.outer.content, height=1200).grid(row=1, column=0)
        deadline = time.perf_counter()+2
        stable = 0
        def settled():
            nonlocal stable
            ready = (self.inner.winfo_height() == self.inner.winfo_reqheight()
                     and self.inner.canvas.winfo_height() == self.inner.canvas.winfo_reqheight()
                     and self.inner._layout_job is None and self.outer._layout_job is None)
            stable = stable+1 if ready else 0
            if stable >= 3 or time.perf_counter() >= deadline:
                self.root.quit()
            else:
                self.root.after(16, settled)
        self.root.after(16, settled)
        self.root.mainloop()
        self.assertGreaterEqual(stable, 3, 'nested scrollbar allocation did not settle')

    def test_horizontal_only_viewport_passes_vertical_wheel_to_parent_once(self):
        self.assertEqual(self.inner.canvas.yview(), (0.0, 1.0))
        self.assertLess(self.inner.canvas.xview()[1], 1)
        before = self.outer.canvas.canvasy(0)
        with patch.object(self.outer.canvas, "yview_scroll",
                          wraps=self.outer.canvas.yview_scroll) as outer_scroll, \
             patch.object(self.inner.canvas, "yview_scroll",
                          wraps=self.inner.canvas.yview_scroll) as inner_scroll:
            wheel(self.inner.canvas, -120)
            pump(self.root)
        self.assertGreater(self.outer.canvas.canvasy(0), before)
        outer_scroll.assert_called_once()
        inner_scroll.assert_not_called()

    def test_scrollable_child_keeps_wheel_until_its_directional_boundary(self):
        self.body.configure(height=600)
        pump(self.root)
        outer_before = self.outer.canvas.yview()
        wheel(self.inner.canvas, -120)
        pump(self.root)
        self.assertGreater(self.inner.canvas.yview()[0], 0)
        self.assertEqual(self.outer.canvas.yview(), outer_before)

        self.inner.canvas.yview_moveto(1)
        pump(self.root)
        wheel(self.inner.canvas, -120)
        pump(self.root)
        self.assertGreater(self.outer.canvas.yview()[0], outer_before[0])
        self.assertEqual(self.inner.canvas.yview()[1], 1)

        # Upward input still belongs to the child while it has room above.
        outer_before = self.outer.canvas.yview()
        wheel(self.inner.canvas, 120)
        pump(self.root)
        self.assertLess(self.inner.canvas.yview()[1], 1)
        self.assertEqual(self.outer.canvas.yview(), outer_before)
        self.inner.canvas.yview_moveto(0)
        pump(self.root)
        wheel(self.inner.canvas, 120)
        pump(self.root)
        self.assertLess(self.outer.canvas.yview()[0], outer_before[0])


class Awp1221WaveformWheelTests(unittest.TestCase):
    def test_posted_waveform_selector_scrolls_without_callback_errors(self):
        prepare_tk_test(self)
        context = mocked_application()
        app = context.__enter__()
        attach_tk_cleanup(self, app.root, app)
        self.addCleanup(context.__exit__, None, None, None)
        root = app.root
        root.geometry("1280x720+0+0")
        app.set_animations(False)
        errors = []
        root.report_callback_exception = lambda *args: errors.append(
            "".join(traceback.format_exception(*args)))
        app.navigation.show("awp1221")
        host = app.navigation.pages["awp1221"]
        host.show_mode("button_mode")
        page = host.mode_pages["button_mode"]
        page._select_button_wave_tab("sinc")
        page.step_progress.scroll_to_step("waveform")
        deadline = time.perf_counter()+10
        ready = False
        def mapped():
            nonlocal ready
            ready = (app.navigation._page_layout_ready() and app._resize_after is None
                     and any(isinstance(widget, ttk.Combobox) and widget.winfo_viewable()
                             and len(widget.cget("values")) > 2
                             for row in page._selector_widgets.values() for widget in row))
            if ready or time.perf_counter() >= deadline:
                root.quit()
            else:
                root.after(20, mapped)
        root.after(20, mapped)
        root.mainloop()
        self.assertTrue(ready, "waveform selector was not mapped before posting")
        combo = next(widget for row in page._selector_widgets.values() for widget in row
                     if isinstance(widget, ttk.Combobox) and widget.winfo_viewable()
                     and len(widget.cget("values")) > 2)
        combo.configure(height=2)
        popup = str(root.tk.call("ttk::combobox::PopdownWindow", str(combo)))
        root.tk.call("ttk::combobox::Post", str(combo))
        self.addCleanup(lambda: root.tk.call("ttk::combobox::Unpost", str(combo)))
        listbox = popup + ".f.l"
        deadline = time.perf_counter()+2
        posted = False
        def list_ready():
            nonlocal posted
            first, last = map(float, root.tk.call(listbox, 'yview'))
            posted = (bool(int(root.tk.call('winfo', 'ismapped', listbox)))
                      and last-first < 1
                      and app._resize_after is None and app.navigation._page_layout_ready())
            if posted or time.perf_counter() >= deadline:
                root.quit()
            else:
                root.after(20, list_ready)
        root.after(20, list_ready)
        root.mainloop()
        self.assertTrue(posted, ('native listbox did not map with a scrollable range',
            root.winfo_geometry(), app.navigation._page_layout_ready(), app._resize_after,
            str(combo), combo.cget('values'), root.tk.call(listbox, 'yview'),
            root.tk.call(listbox, 'cget', '-height'), root.tk.call('winfo', 'ismapped', listbox),
            root.tk.call('winfo', 'height', listbox)))
        root.tk.call("focus", "-force", listbox)
        root.tk.call(listbox, "yview", "moveto", 0)
        before = tuple(map(float, root.tk.call(listbox, "yview")))
        page_before = host.mode_scroll.canvas.yview()
        sidebar_before = app.navigation.sidebar_canvas.yview()
        x = int(root.tk.call("winfo", "rootx", listbox)) + 10
        y = int(root.tk.call("winfo", "rooty", listbox)) + 10
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>"):
            root.tk.call("event", "generate", listbox, sequence,
                         "-delta", -120, "-rootx", x, "-rooty", y)
        # Both chrome redraw paths run while focus belongs to Tcl's listbox.
        root.after(0, page.step_progress._draw_strip)
        root.after(0, page.step_progress._hit_tooltip)
        root.after(0, host.trapezoid._draw_button)
        pump(root, 200)
        after = tuple(map(float, root.tk.call(listbox, "yview")))
        self.assertGreater(after[0], before[0])
        self.assertEqual(host.mode_scroll.canvas.yview(), page_before)
        self.assertEqual(app.navigation.sidebar_canvas.yview(), sidebar_before)
        self.assertEqual(errors, [], "\n".join(errors))

    def test_wheel_over_waveform_moves_mode_viewport(self):
        prepare_tk_test(self)
        faulthandler.dump_traceback_later(30, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        context = mocked_application()
        app = context.__enter__()
        attach_tk_cleanup(self, app.root, app)
        self.addCleanup(context.__exit__, None, None, None)
        app.set_animations(False)
        root = app.root
        root.geometry("1280x720+0+0")
        errors = []
        root.report_callback_exception = lambda *args: errors.append(str(args))
        app.navigation.show("awp1221")
        host = app.navigation.pages["awp1221"]
        host.show_mode("button_mode")
        page = host.mode_pages["button_mode"]
        for _ in range(4):
            pump(root)
        for tab in ("sinc", "rom", "sram"):
            with self.subTest(tab=tab):
                page._select_button_wave_tab(tab)
                host.mode_scroll.canvas.yview_moveto(0)
                for _ in range(20):
                    pump(root)
                    if (page._button_waveform_scroll.canvas.winfo_width() > 1
                            and (tab == "sram" or page.rom_canvas.winfo_viewable())
                            and app.navigation._page_layout_ready()):
                        break
                else:
                    self.fail("Waveform settings did not become ready for wheel input")
                area = page._button_waveform_scroll
                area.canvas.yview_moveto(1)
                pump(root)
                targets = [area.canvas]
                if tab != "sram":
                    targets.append(page.rom_canvas)
                for target in targets:
                    before = host.mode_scroll.canvas.canvasy(0)
                    wheel(target, -120)
                    pump(root, 200)
                    self.assertGreater(host.mode_scroll.canvas.canvasy(0), before,
                                       "Wheel over waveform settings did not scroll the page")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
