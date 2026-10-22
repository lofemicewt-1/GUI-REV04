"""SINC preview reports actual segment lengths and cumulative node times."""
from itertools import accumulate
from types import SimpleNamespace
import tkinter as tk
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
import unittest

from config.languages import t
from core.awp1221_sinc import awp1221_sinc_parse_params, build_awp1221_sinc_plan
from pages.awp1221_page import AWP1221ModePage
from styles.theme import get_colors


class SincTimingTests(unittest.TestCase):
    def test_rom_axis_keeps_endpoints_without_overlapping_in_narrow_preview(self):
        self.root.geometry('360x240')
        self.root.update()
        page = SimpleNamespace(rom_canvas=self.canvas, colors=get_colors('light'),
                               tr=lambda key, **kwargs: t('zh', key, **kwargs))
        AWP1221ModePage._draw_button_time_axis(page, 512, 25, 290, 150, total=False)
        boxes = [self.canvas.bbox(item) for item in self.canvas.find_withtag('time_axis')]
        for i, first in enumerate(boxes):
            for second in boxes[i+1:]:
                self.assertFalse(first[0] < second[2] and first[2] > second[0]
                                 and first[1] < second[3] and first[3] > second[1])

    def setUp(self):
        prepare_tk_test(self)
        self.root = tk.Tk()
        self.canvas = tk.Canvas(self.root, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        attach_tk_cleanup(self, self.root, self.canvas)
    def draw(self, serial, times, depth, repeat, width, language, theme, height=520):
        self.root.geometry(f"{width}x{height}")
        self.root.update()
        params = awp1221_sinc_parse_params("80", "12", *(str(v) for v in times[:3]), str(depth),
                    str(times[3]) if len(times) == 4 else None, str(repeat))
        plan = build_awp1221_sinc_plan(params, serial=serial)
        value = lambda: SimpleNamespace(set=lambda text: None)
        colors = get_colors(theme)
        page = SimpleNamespace(mode_key="button_mode", rom_canvas=self.canvas, colors=colors,
            ctx=SimpleNamespace(colors=colors), rom_metric_var=value(), rom_frequency_var=value(),
            _build_current_sinc_plan=lambda: plan, _sinc_reason_message=lambda reason: reason,
            tr=lambda key, **kwargs: t(language, key, **kwargs))
        page._annotate_button_preview = lambda *args, **kwargs: AWP1221ModePage._annotate_button_preview(page, *args, **kwargs)
        page._draw_button_time_axis = lambda *args, **kwargs: AWP1221ModePage._draw_button_time_axis(page, *args, **kwargs)
        AWP1221ModePage._draw_sinc_preview(page)
        self.root.update()

    def test_lengths_nodes_and_labels_fit_for_three_and_four_segments(self):
        cases = (
            ("serial1", (10, 10, 10), 512, 1, (2.56, 2.56, 2.56)),
            ("serial2", (10, 4, 7), 256, 1, (1.28, .512, .896)),
            ("serial3", (4, 7, 8, 9), 512, 2, (4.096, 1.792, 2.048, 2.304)),
            ("serial4", (5, 9, 11, 13), 256, 3, (3.84, 1.152, 1.408, 1.664)),
            ("serial4", (4, 1, 1, 1), 512, 256, (524.288, .256, .256, .256)),
        )
        for serial, times, depth, repeat, durations in cases:
            for width, language, theme in ((820, "zh", "light"), (360, "en", "dark")):
                with self.subTest(serial=serial, repeat=repeat, width=width):
                    self.draw(serial, times, depth, repeat, width, language, theme, height=400)
                    self.assertFalse(self.canvas.find_withtag("sinc_segment_card"))
                    labels = self.canvas.find_withtag("sinc_segment_duration")
                    self.assertEqual(len(labels), len(durations))
                    boxes = []
                    for item, duration, end in zip(labels, durations, accumulate(durations)):
                        text = self.canvas.itemcget(item, "text")
                        self.assertIn(f": {duration:g} ms" if language == "en" else f"：{duration:g} ms", text)
                        self.assertIn(f"{end:g} ms", text)
                        box = self.canvas.bbox(item)
                        self.assertGreaterEqual(box[0], 0)
                        self.assertLessEqual(box[2], self.canvas.winfo_width())
                        self.assertLessEqual(box[3], self.canvas.winfo_height())
                        self.assertEqual(self.canvas.itemcget(item, "fill"), get_colors(theme)["muted"])
                        boxes.append(box)
                    for index, box in enumerate(boxes):
                        for other in boxes[index + 1:]:
                            self.assertFalse(box[0] < other[2] and box[2] > other[0] and
                                             box[1] < other[3] and box[3] > other[1])
                    curves = self.canvas.find_withtag("wave_curve")
                    self.assertGreater(min(box[1] for box in boxes),
                                       max(self.canvas.bbox(item)[3] for item in curves) + 30)
                    if len(durations) == 4:
                        self.assertIn(f"×{repeat}", self.canvas.itemcget(labels[0], "text"))
                    summary = self.canvas.find_withtag("wave_duration")[0]
                    self.assertIn(f"{sum(durations):g} ms", self.canvas.itemcget(summary, "text"))
                    self.assertLessEqual(self.canvas.bbox(summary)[3], self.canvas.winfo_height())
                    node_boxes = [self.canvas.bbox(item) for item in self.canvas.find_withtag("time_axis")]
                    for index, box in enumerate(node_boxes):
                        for other in node_boxes[index + 1:]:
                            self.assertFalse(box[0] < other[2] and box[2] > other[0] and
                                             box[1] < other[3] and box[3] > other[1])


if __name__ == "__main__":
    unittest.main()
