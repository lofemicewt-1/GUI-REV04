from __future__ import annotations

import unittest
from types import SimpleNamespace

import tkinter as tk
from tkinter import ttk

from app.application import PiezoApplication
from pages.base_page import VerticalScrollArea


def _overflow(stage: tk.Misc, canvas: tk.Canvas) -> int:
    canvas.update_idletasks()
    right_edge = canvas.winfo_rootx() + canvas.winfo_width()
    kids = [widget for widget in stage.winfo_children() if widget.winfo_ismapped()]
    if not kids:
        return 0
    farthest = max(widget.winfo_rootx() + widget.winfo_width() for widget in kids)
    return farthest - right_edge


class VerticalScrollAreaWidthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.ctx = SimpleNamespace(colors={"bg": "#ffffff"})

    def test_propagate_width_keeps_wide_form_visible(self) -> None:
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)
        self.root.geometry("900x400")
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)

        area = VerticalScrollArea(body, self.ctx, propagate_width=True)
        area.grid(row=0, column=0, sticky="nsew")
        form = ttk.Frame(area.content)
        form.grid(row=0, column=0, sticky="ew")
        for column, minsize in enumerate((126, 180, 126, 180)):
            form.columnconfigure(column, minsize=minsize)
            ttk.Entry(form, width=16).grid(row=0, column=column, sticky="ew")

        fat = ttk.Frame(body, width=700, height=200)
        fat.grid(row=0, column=1, sticky="nsew")
        fat.grid_propagate(False)

        self.root.update_idletasks()
        self.root.update()
        self.assertLessEqual(_overflow(form, area.canvas), 1)


class FullModeAdcLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = PiezoApplication()
        self.addCleanup(self.app.root.destroy)
        self.app.root.update_idletasks()
        self.app.root.update()

    def test_awp1221_full_mode_adc_stage_is_not_clipped(self) -> None:
        page = self.app.navigation.pages["awp1221"]
        self.app.navigation.show("awp1221")
        page.show_mode("full_mode")
        self.app.root.update_idletasks()
        self.app.root.update()
        mode = page.mode_pages["full_mode"]
        overflow = _overflow(mode._stage_frames[0], mode._vertical_scroll_areas[0].canvas)
        self.assertLessEqual(overflow, 1, f"AWP1221 ADC clipped by {overflow}px")

    def test_awp1018_full_mode_adc_stage_is_not_clipped(self) -> None:
        page = self.app.navigation.pages["awp1018"]
        self.app.navigation.show("awp1018")
        page.show_mode("full_mode", update_chip=False)
        self.app.root.update_idletasks()
        self.app.root.update()
        mode = page.mode_pages["full_mode"]
        overflow = _overflow(mode._stage_frames["Stage0"], mode._vertical_scroll_areas[0].canvas)
        self.assertLessEqual(overflow, 1, f"AWP1018 ADC clipped by {overflow}px")


if __name__ == "__main__":
    unittest.main()
