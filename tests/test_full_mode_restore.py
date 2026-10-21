"""Mode-switch regressions that do not require a display or transport."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from config.languages import t
from pages.base_page import BasePage
from pages.full_mode_layout import FullModeFooter, is_full_mode
from pages.step_progress import Trapezoid


class FullModeRestoreTests(TestCase):
    def test_full_mode_labels_omit_steps_with_or_without_a_step_bar(self):
        label = Mock()
        page = SimpleNamespace(
            page_name="full_mode", step_progress=object(),
            tr=lambda key, **kw: t("zh", key, **kw),
            _module_title_bindings=[],
        )
        page._refresh_module_title = lambda binding: BasePage._refresh_module_title(page, binding)
        with patch("pages.base_page.ttk.Label", return_value=label):
            BasePage.make_full_mode_label(page, Mock(), "awp1221_adc", step=3)
        binding = page._module_title_bindings[0]
        for language in ("zh", "en"):
            page.tr = lambda key, **kw: t(language, key, **kw)
            for step_bar in (object(), None):
                with self.subTest(language=language, step_bar=step_bar is not None):
                    page.step_progress = step_bar
                    BasePage._refresh_module_title(page, binding)
                    self.assertEqual(label.configure.call_args.kwargs["text"], t(language, "awp1221_adc"))
        self.assertTrue(is_full_mode(SimpleNamespace(mode_key="full_mode")))

    def test_first_full_entry_preserves_original_scroll_reserve(self):
        area = SimpleNamespace(scroll_region_extra=37, set_bottom_inset=Mock(),
                               _queue_scroll_layout=Mock())
        trapezoid = SimpleNamespace(
            _owning_area=area, _old_scroll_extra=None, _old_bottom_inset=9,
            host=SimpleNamespace(current_mode="full_mode"),
        )
        Trapezoid._reserve_scroll_clearance(trapezoid)
        self.assertEqual(area.scroll_region_extra, 37)
        area.set_bottom_inset.assert_called_once_with(9)

    def test_footer_round_trips_restore_button_widgets_and_scroll_options(self):
        area = SimpleNamespace(horizontal=False, clamp_width=True, min_content_width=11,
                               propagate_width=True, propagate_height=True,
                               _queue_scroll_layout=Mock())
        button_footer, full_footer = Mock(), Mock()
        button_controls, full_controls = {}, {}
        host = SimpleNamespace(
            page_name="awp1018", mode_scroll=area, footer_scroll=button_footer,
            _buttons=button_controls, trapezoid=Mock(),
        )
        layout = FullModeFooter(host)
        layout.area = full_footer
        layout.full_refs = dict(footer_scroll=full_footer, _buttons=full_controls)
        layout.button_refs = dict(footer_scroll=button_footer, _buttons=button_controls)
        layout.fit = Mock()
        for _ in range(3):
            layout.select(True)
            self.assertIs(host._buttons, full_controls)
            self.assertTrue(area.horizontal)
            self.assertFalse(area.clamp_width)
            self.assertEqual(area.min_content_width, 1320)
            layout.select(False)
            self.assertIs(host._buttons, button_controls)
            self.assertIs(host.footer_scroll, button_footer)
            self.assertEqual((area.horizontal, area.clamp_width, area.min_content_width,
                              area.propagate_width, area.propagate_height),
                             (False, True, 11, True, True))
        self.assertEqual(button_footer.grid.call_count, 3)

    def test_192x_attribute_controls_get_translated_on_first_full_entry(self):
        names = ("addr_label", "reg_title_label", "reg_addr_label", "write_value_label",
                 "read_value_label", "rr_btn", "rw_btn", "detail_mode_check",
                 "detail_register_label")
        host = SimpleNamespace(page_name="awp1921_1923", bind_text=Mock(),
                               **{name: object() for name in names})
        layout = FullModeFooter(host)
        layout._translate_controls()
        translated = {call.args[0] for call in host.bind_text.call_args_list}
        self.assertEqual(translated, {getattr(host, name) for name in names})
