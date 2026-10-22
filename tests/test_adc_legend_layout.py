"""Real Tk regressions for compact ADC legends and module help anchors."""
import tkinter as tk
from tkinter import font as tkfont
from types import SimpleNamespace
import unittest

from config.languages import t
from pages.adc_legend import AdcLegend
from pages.base_page import BasePage
from styles.theme import apply_theme
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test


class CompactLegendLayoutTests(unittest.TestCase):
    def setUp(self):
        prepare_tk_test(self)
        self.root = tk.Tk()
        attach_tk_cleanup(self, self.root)
        self.root.tk.call("tk", "scaling", 1.5)
        self.colors = apply_theme(self.root, "light", 10)

    def flush(self):
        for _ in range(4):
            self.root.update_idletasks()
            self.root.update()

    def test_compact_legend_text_fits_canvas_cards_and_table(self):
        legend = AdcLegend(self.root, lambda key, **kw: t("zh", key, **kw), self.colors)
        legend.pack(fill="x")
        legend.set_rules([
            dict(id="Stage0", condition="press", threshold=300, adc_precision="高 12 位",
                 waveform="serial1 slice18-20", target="Stage1"),
            dict(id="Stage1", condition="release", threshold=100, adc_precision="低 12 位",
                 waveform="serial1 slice18-20", target="Stage0"),
        ], {"Stage0": []}, preview_stage="Stage0")
        for width in (510, 440, 600):
            with self.subTest(width=width):
                self.root.geometry(f"{width}x1000")
                self.flush()
                canvas = legend.canvas
                for index, item in enumerate(canvas.find_withtag("stage_wait")):
                    precision = "高 12 位" if index == 0 else "低 12 位"
                    wait = canvas.itemcget(item, "text")
                    self.assertEqual(wait.split("\n")[1], f"ADC · {precision}")
                    self.assertNotIn("ADC", wait.split("\n")[0])
                    self.assertEqual(legend._rule_rows[index + 1][2].cget("text"), wait)
                    self.assertNotIn("\n\n", wait)
                for item in canvas.find_withtag("transition_label"):
                    self.assertNotIn("\n\n", canvas.itemcget(item, "text"))
                for item in canvas.find_all():
                    if canvas.type(item) != "text":
                        continue
                    font = tkfont.Font(root=self.root, font=canvas.itemcget(item, "font"))
                    self.assertLessEqual(font.actual("size"), round(self.root._piezo_body_font_size * 1.15))
                    box = canvas.bbox(item)
                    self.assertGreaterEqual(box[0], 0, canvas.itemcget(item, "text"))
                    self.assertGreaterEqual(box[1], 0, canvas.itemcget(item, "text"))
                    self.assertLessEqual(box[2], canvas.winfo_width())
                    self.assertLessEqual(box[3], canvas.winfo_height(), canvas.itemcget(item, "text"))
                card_top = min(canvas.coords(i)[1] for i in canvas.find_withtag("stage_box"))
                for item in canvas.find_withtag("transition_label"):
                    box = canvas.bbox(item)
                    if box[1] < card_top:
                        self.assertLess(box[3], card_top)
                for cell in legend._table.winfo_children():
                    column = int(cell.grid_info()["column"])
                    _, _, allocated, _ = legend._table.grid_bbox(column, int(cell.grid_info()["row"]))
                    self.assertLessEqual(int(cell.cget("wraplength")), allocated - 8)
                for row in legend._rule_rows.values():
                    target = row[3]
                    font = tkfont.Font(root=self.root, font=target.cget("font"))
                    self.assertLessEqual(font.measure(target.cget("text")), target.winfo_width() - 16)

    def test_module_question_button_fits_slot_at_font_scales(self):
        context = SimpleNamespace(colors=self.colors, state=SimpleNamespace(language="zh", theme="light"))
        page = BasePage(self.root, context)
        page.pack(fill="both", expand=True)
        title, _ = page.make_module_title_row(page, "awp1221_adc_diagram", help_key="awp1221_help_adc_legend")
        slot = title._module_help_slot
        button = title._module_help_button
        self.root.geometry("600x400")
        for size in (10, 14, 18):
            with self.subTest(font_size=size):
                apply_theme(self.root, "light", size)
                self.flush()
                self.assertGreaterEqual(slot.winfo_width(), button.winfo_reqwidth())
                self.assertGreaterEqual(slot.winfo_height(), button.winfo_reqheight())


if __name__ == "__main__":
    unittest.main()
