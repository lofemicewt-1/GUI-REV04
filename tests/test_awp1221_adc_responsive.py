"""Real-Tk ADC layout checks across window widths and resize directions."""
import unittest

from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
from tests.validate_responsive_layout import mocked_application, _flush


class AdcResponsiveLayoutTests(unittest.TestCase):
    def test_parameter_rows_and_panel_relationship_survive_resize_round_trip(self):
        prepare_tk_test(self)
        context = mocked_application()
        app = context.__enter__()
        attach_tk_cleanup(self, app.root, app)
        self.addCleanup(context.__exit__, None, None, None)
        app.set_animations(False)
        errors = []
        app.root.report_callback_exception = lambda *args: errors.append(str(args))
        app.navigation.show("awp1221")
        host = app.navigation.pages["awp1221"]
        host.show_mode("button_mode")
        page = host.mode_pages["button_mode"]
        root = app.root

        def settle():
            _flush(root, 3)
            root.after(100, root.quit)
            root.mainloop()
            _flush(root, 3)

        for language, font_size in (("zh", 10), ("en", 14)):
            app.state.language, app.state.font_size = language, font_size
            app._apply_visual_settings()
            host.refresh_language()
            seen = {}
            for width in (1920, 1700, 1400, 1100, 640, 1400, 1700, 1920):
                with self.subTest(language=language, font_size=font_size, width=width):
                    root.geometry(f"{width}x980")
                    settle()
                    left, right = page._button_adc_panels
                    side_by_side = page._button_adc_split_signature
                    snapshot = (side_by_side, page._button_stage_columns, page._button_adc_columns,
                                left.winfo_height(), right.winfo_height())
                    if width in seen:
                        self.assertEqual(snapshot, seen[width])
                    seen[width] = snapshot
                    self.assertEqual(right.winfo_height(), right.winfo_reqheight())
                    if side_by_side:
                        self.assertEqual(page._button_stage_columns, 3)
                        self.assertEqual(page._button_adc_columns, 3)
                        self.assertEqual(left.winfo_y(), right.winfo_y())
                        self.assertLessEqual(abs(left.winfo_height() - right.winfo_height()),
                                             10 * root._piezo_body_font_size)
                        for layout in page._button_stage_layouts:
                            row = layout["event"][1].grid_info()["row"]
                            self.assertTrue(all(check.grid_info()["row"] == row
                                                for check in layout["checks"]))
                    for item in page.adc_legend.canvas.find_all():
                        canvas = page.adc_legend.canvas
                        if canvas.type(item) == "text":
                            x0, y0, x1, y1 = canvas.bbox(item)
                            self.assertGreaterEqual(x0, 0)
                            self.assertGreaterEqual(y0, 0)
                            self.assertLessEqual(x1, canvas.winfo_width())
                            self.assertLessEqual(y1, canvas.winfo_height())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
