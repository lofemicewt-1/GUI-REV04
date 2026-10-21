from __future__ import annotations

from types import SimpleNamespace
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
import unittest

from pages.base_page import BasePage
from styles.theme import apply_theme
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test


def _context(language: str = "en", theme: str = "light") -> SimpleNamespace:
    return SimpleNamespace(
        colors={},
        state=SimpleNamespace(language=language, theme=theme),
    )


class PageTitleLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        prepare_tk_test(self)
        self.root = tk.Tk()
        self.root.withdraw()
        self.ctx = _context()
        self.ctx.colors = apply_theme(self.root, "light", 10, 1.0)
        self.page = BasePage(self.root, self.ctx)
        self.page.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        attach_tk_cleanup(self, self.root, self.page)

    def _settle(self) -> None:
        self.root.update_idletasks()
        self.root.update()

    def test_theme_spacing_scales_from_body_line_height(self) -> None:
        small = apply_theme(self.root, "light", 8, 0.85)
        small_title_gap = self.root._piezo_title_after_gap
        small_module_gap = self.root._piezo_module_gap
        small_chip_title = self.root._piezo_chip_title_font_size
        small_line_height = int(tkfont.Font(
            root=self.root,
            family="Microsoft YaHei UI",
            size=self.root._piezo_body_font_size,
        ).metrics("linespace"))

        large = apply_theme(self.root, "dark", 18, 1.25)

        self.assertGreater(self.root._piezo_title_after_gap, small_title_gap)
        self.assertGreater(self.root._piezo_module_gap, small_module_gap)
        self.assertGreater(self.root._piezo_chip_title_font_size, small_chip_title)
        self.assertGreater(
            self.root._piezo_chip_title_font_size,
            self.root._piezo_prominent_title_font_size,
        )
        self.assertEqual(small_title_gap, max(1, int(round(small_line_height * 0.3))))
        self.assertEqual(small_module_gap, max(1, int(round(small_line_height * 0.45))))
        self.assertEqual(small["bg"], "#FFFFFF")
        self.assertNotEqual(large["bg"], small["bg"])

    def test_mode_header_uses_title_height_and_can_shrink(self) -> None:
        self.ctx.colors = apply_theme(self.root, "light", 18, 1.25)
        self.root._piezo_top_chrome_height = 240  # legacy sidebar metric must not size page titles
        header = self.page.make_title(self.page, "button_mode", show_logo=False)
        header.grid(row=0, column=0, sticky="ew")
        self._settle()

        large_height = int(header.grid_rowconfigure(0)["minsize"])
        large_title_gap = int(header.grid_info()["pady"][1])
        self.assertEqual(large_height, self.page._mode_title_height(header))

        self.ctx.colors = apply_theme(self.root, "light", 8, 0.85)
        self.page.refresh_theme()
        self._settle()

        small_height = int(header.grid_rowconfigure(0)["minsize"])
        small_title_gap = int(header.grid_info()["pady"][1])
        self.assertLess(small_height, large_height)
        self.assertLess(small_title_gap, large_title_gap)
        self.assertEqual(small_height, self.page._mode_title_height(header))

    def test_chip_title_and_wordmark_do_not_use_sidebar_logo_height(self) -> None:
        self.root._piezo_top_chrome_height = 300
        header = self.page.make_title(self.page, "awp1221")
        header.grid(row=0, column=0, sticky="ew")
        self._settle()

        chip_title = self.page._chip_title_labels[-1]
        self.assertEqual(int(chip_title.cget("font")[1]), self.root._piezo_chip_title_font_size)
        self.assertNotIn("_chrome_sizer", header.__dict__)
        self.assertLess(int(header.winfo_reqheight()), 300)

    def test_two_by_five_preset_toolbar_moves_below_title_when_narrow(self) -> None:
        self.root.geometry("540x600")
        self.root.deiconify()
        header = self.page.make_title(self.page, "button_mode", show_logo=False)
        header.grid(row=0, column=0, sticky="ew")
        preset = self.page.make_preset_frame(header)
        for row in range(2):
            for column in range(5):
                ttk.Button(
                    preset,
                    style="Bg.TButton",
                    text=f"Act {row}-{column}",
                ).grid(
                    row=row,
                    column=column,
                    sticky="ew",
                    padx=(0 if column == 0 else 6, 0),
                    pady=(0, 8) if row else (0, 0),
                )
        self.page._sync_mode_header(header)
        self._settle()

        area = header._preset_area
        frame = header._preset_frame
        placements = {
            (int(child.grid_info()["row"]), int(child.grid_info()["column"]))
            for child in frame.grid_slaves()
        }
        self.assertEqual(len(placements), 10)
        self.assertEqual({row for row, _column in placements}, {0, 1})
        self.assertEqual({column for _row, column in placements}, set(range(5)))
        self.assertEqual(int(area.grid_info()["row"]), 1)
        self.assertEqual(int(area.grid_info()["column"]), 0)
        self.assertTrue(area._horizontal_scrollbar_visible)
        self.assertFalse(area._scrollbar_visible)
        self.assertEqual(
            int(header.grid_rowconfigure(1)["minsize"]),
            int(area.winfo_reqheight()),
        )

    def test_preset_toolbar_uses_visible_viewport_with_wide_content(self) -> None:
        self.root.geometry("540x600")
        self.root.deiconify()
        self.page.rowconfigure(0, weight=1)
        self.page.columnconfigure(0, weight=1)
        viewport, content = self.page.make_vertical_scroll_area(
            self.page,
            min_content_width=1320,
        )
        viewport.grid(row=0, column=0, sticky="nsew")
        header = self.page.make_title(content, "button_mode", show_logo=False)
        header.grid(row=0, column=0, sticky="ew")
        preset = self.page.make_preset_frame(header)
        for row in range(2):
            for column in range(5):
                ttk.Button(
                    preset,
                    style="Bg.TButton",
                    text=f"Act {row}-{column}",
                ).grid(row=row, column=column, sticky="ew")

        self._settle()
        self.assertGreater(int(header.winfo_width()), int(viewport.canvas.winfo_width()))
        self.assertEqual(int(header._preset_area.grid_info()["row"]), 1)

        self.root.geometry("1700x600")
        self._settle()

        self.assertEqual(int(header._preset_area.grid_info()["row"]), 0)

    def test_preset_toolbar_stacks_at_padded_scroll_content_boundary(self) -> None:
        self.root.geometry("1600x600")
        self.root.deiconify()
        self.page.rowconfigure(0, weight=1)
        self.page.columnconfigure(0, weight=1)
        viewport, content = self.page.make_vertical_scroll_area(
            self.page,
            horizontal=True,
            clamp_width=True,
        )
        viewport.grid(row=0, column=0, sticky="nsew")
        content.configure(padding=(8, 0, 8, 0))
        header = self.page.make_title(content, "button_mode", show_logo=False)
        header.grid(row=0, column=0, sticky="ew")
        preset = self.page.make_preset_frame(header)
        for column in range(5):
            ttk.Button(
                preset,
                style="Bg.TButton",
                text=f"Preset {column}",
            ).grid(row=0, column=column, sticky="ew")

        self._settle()
        required_width = (
            int(header._mode_title_width)
            + int(header._preset_frame.winfo_reqwidth())
            + 24
        )
        target_canvas_width = required_width + 8
        for _ in range(3):
            canvas_width = int(viewport.canvas.winfo_width())
            if canvas_width == target_canvas_width:
                break
            root_width = int(self.root.winfo_width())
            self.root.geometry(
                f"{root_width + target_canvas_width - canvas_width}x600"
            )
            self._settle()

        canvas_width = int(viewport.canvas.winfo_width())
        self.assertGreaterEqual(canvas_width, required_width)
        self.assertLess(canvas_width - 16, required_width)
        self.assertEqual(int(header._preset_area.grid_info()["row"]), 1)

        content.configure(padding=(8, 0, 8))
        self.page._layout_mode_header(header)
        self.assertEqual(int(header._preset_area.grid_info()["row"]), 1)


if __name__ == "__main__":
    unittest.main()
