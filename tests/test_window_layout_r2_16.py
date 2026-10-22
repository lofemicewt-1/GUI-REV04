from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.window_layout import (
    clamped_waveform_width,
    is_compact_window,
    scaled_content_max_width,
    scaled_waveform_max_width,
    ui_scale_for_width,
)
from pages.waveform_layout import WaveformWidthClamp


class _GeometryWidget:
    def __init__(self, width: int = 0) -> None:
        self.width = width
        self.height = 0
        self.propagation = None

    def bind(self, _sequence, _callback, add=None):
        self.add = add
        return "binding"

    def unbind(self, _sequence, _binding):
        return None

    def grid_propagate(self, enabled):
        self.propagation = enabled

    def winfo_width(self):
        return self.width

    def cget(self, key):
        if key not in ("width", "height"):
            raise KeyError(key)
        return getattr(self, key)

    def winfo_pixels(self, value):
        return int(value)

    def configure(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class WindowLayoutTierTests(unittest.TestCase):
    def test_waveform_clamp_sizes_real_ttk_panel_before_drawing(self) -> None:
        import tkinter as tk
        from tkinter import ttk
        from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test

        prepare_tk_test(self)
        root = tk.Tk()
        attach_tk_cleanup(self, root)
        root.geometry("900x520")
        container = ttk.Frame(root)
        container.pack(fill="both", expand=True)
        panel = ttk.Frame(container)
        panel.grid(row=0, column=0)
        canvas = tk.Canvas(panel, width=280, height=280)
        canvas.grid(sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        owner = SimpleNamespace(ctx=SimpleNamespace(state=SimpleNamespace(ui_scale=1)))
        clamp = WaveformWidthClamp(
            owner, container, panel, reserved_width=lambda: 200,
            requested_height=lambda: 320,
        )
        self.addCleanup(clamp.close)
        root.update()
        self.assertEqual(panel.winfo_height(), 320)
        self.assertEqual(panel.winfo_width(), 700)
        self.assertGreater(canvas.winfo_width(), 50)
        self.assertGreater(canvas.winfo_height(), 50)
        root.geometry("800x520")
        root.update()
        self.assertEqual(panel.winfo_width(), 600)

    def test_frozen_width_breakpoints_and_width_caps(self) -> None:
        self.assertEqual(ui_scale_for_width(1599), 1.0)
        self.assertEqual(ui_scale_for_width(1600), 1.1)
        self.assertEqual(ui_scale_for_width(2199), 1.1)
        self.assertEqual(ui_scale_for_width(2200), 1.25)
        self.assertTrue(is_compact_window(1399, 900))
        self.assertTrue(is_compact_window(1600, 799))
        self.assertFalse(is_compact_window(1400, 800))
        self.assertEqual(scaled_content_max_width(1.25, 3000), 2250)
        self.assertEqual(scaled_content_max_width(1.1, 1200), 1200)
        self.assertEqual(scaled_waveform_max_width(1.25), 1200)

    def test_waveform_column_clamps_to_cap_and_free_width(self) -> None:
        self.assertEqual(clamped_waveform_width(1.25, 2200, 700), 1200)
        self.assertEqual(clamped_waveform_width(1.10, 1500, 400), 1056)
        self.assertEqual(clamped_waveform_width(1.10, 900, 400), 500)
        self.assertEqual(clamped_waveform_width(1.00, 300, 500), 1)

    def test_waveform_width_manager_sets_fixed_frame_width(self) -> None:
        owner = SimpleNamespace(ctx=SimpleNamespace(state=SimpleNamespace(ui_scale=1.25)))
        container = _GeometryWidget(width=1800)
        panel = _GeometryWidget()
        clamp = WaveformWidthClamp(owner, container, panel, reserved_width=lambda: 600)
        self.assertIs(panel.propagation, False)
        self.assertEqual(panel.width, 1200)

        container.width = 900
        clamp.refresh()
        self.assertEqual(panel.width, 300)
        clamp.close()

    def test_waveform_height_survives_width_clamping_and_hidden_first_show(self) -> None:
        owner = SimpleNamespace(ctx=SimpleNamespace(state=SimpleNamespace(ui_scale=1)))
        container = _GeometryWidget(width=1)
        panel = _GeometryWidget()
        requested_height = 480
        clamp = WaveformWidthClamp(
            owner, container, panel, requested_height=lambda: requested_height,
        )
        self.assertEqual(panel.height, 480)
        container.width = 1200
        clamp.refresh()
        self.assertEqual(panel.height, 480)
        self.assertEqual(panel.width, 960)
        requested_height = 550
        container.width = 800
        clamp.refresh()
        self.assertEqual((panel.width, panel.height), (800, 550))
        clamp.close()


class WindowResizeLayoutTests(unittest.TestCase):
    def test_resize_font_walk_refreshes_canvas_titles_in_hidden_page_tree(self) -> None:
        from pages.base_page import BasePage, CanvasTitle
        from tests.validate_responsive_layout import mocked_application

        with mocked_application() as app:
            home = dict.get(app.navigation.pages, "home")
            hidden_page = BasePage(home, app.ctx)
            hidden_page.grid()
            title = CanvasTitle(hidden_page, text="Resize tier title")
            title.grid()
            hidden_page.grid_remove()
            app.root.update_idletasks()
            previous_font = title._font
            previous_item_font = title.itemcget(title._item, "font")

            app._apply_visual_settings(scale=1.25, refresh_navigation_theme=False)
            scale_tier_font = title._font
            app.state.font_size = 16
            app._apply_visual_settings(scale=1.25, refresh_navigation_theme=False)

            self.assertNotEqual(scale_tier_font, previous_font)
            self.assertNotEqual(title._font, scale_tier_font)
            self.assertNotEqual(title.itemcget(title._item, "font"), previous_item_font)
            self.assertEqual(title._font[1], app.root._piezo_title_font_size)

    def test_resize_reuses_built_page_without_activation(self) -> None:
        from tests.test_motion import pump
        from tests.validate_responsive_layout import mocked_application

        with mocked_application() as app:
            app.set_animations(False)
            root, nav = app.root, app.navigation
            home = dict.get(nav.pages, "home")
            self.assertIsNotNone(home)
            page_ids = {name: id(page) for name, page in zip(nav.pages.keys(), nav.pages.built_values())}
            activation = patch.object(home, "on_show", wraps=home.on_show)
            with activation as on_show:
                for width, height in ((1920, 1080), (2000, 1080), (1500, 900)):
                    root.geometry(f"{width}x{height}+0+0")
                    pump(root, 0.09)
                self.assertEqual(on_show.call_count, 0)

            self.assertEqual(app.state.ui_scale, 1.0)
            self.assertIs(dict.get(nav.pages, "home"), home)
            self.assertEqual(
                {name: id(page) for name, page in zip(nav.pages.keys(), nav.pages.built_values())},
                page_ids,
            )
            self.assertFalse(nav._hbar_visible)

            root.geometry("2560x1440+0+0")
            pump(root, 0.12)
            self.assertEqual(app.state.ui_scale, 1.25)
            canvas_width = int(nav.content_canvas.winfo_width())
            item_width = int(float(nav.content_canvas.itemcget(nav.content_window, "width")))
            item_x = int(float(nav.content_canvas.coords(nav.content_window)[0]))
            self.assertLessEqual(item_width, 2250)
            self.assertEqual(item_x, max(0, (canvas_width - item_width) // 2))


if __name__ == "__main__":
    unittest.main()
