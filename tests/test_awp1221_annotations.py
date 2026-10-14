"""Offline integration checks for AWP1221 hints, ADC bits and GPIO controls."""
from pathlib import Path
from tkinter import ttk
import unittest

from core.awp1221_gpio import GpioChannelBits, pack_gpio_register
from pages.awp1221_page import AWP1221_SINC_SERIAL_OPTIONS
from tests.validate_responsive_layout import mocked_application, _flush, _capture_window_bmp


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class Awp1221AnnotationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = mocked_application()
        cls.app = cls.context.__enter__()
        cls.host = cls.app.navigation.pages["awp1221"]
        cls.root = cls.app.root
        cls.root.geometry("1920x1080")
        cls.app.navigation.show("awp1221")
        _flush(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def setUp(self):
        self.page = self.host.mode_pages["button_mode"]

    def test_01_gpio_default_and_dropdown_encoding(self):
        self.assertEqual([v.get() for v in self.host.gpio_drive_mode_vars], ["OpenDrian"] * 2)
        self.assertTrue(all(v.get() for v in self.host.gpio_od_vars))
        combos = [w for w in descendants(self.page) if isinstance(w, ttk.Combobox)
                  and tuple(w.cget("values")) == ("Push-Pull", "OpenDrian")]
        self.assertEqual(combos, [])
        for index in range(2):
            self.host.gpio_od_vars[index].set(False)
            self.assertFalse(self.host.gpio_od_vars[index].get())
        self.host.gpio_function_vars[0].set("Floating")
        self.host.gpio_function_vars[1].set("Floating")
        a, b = self.host._gpio_pending_from_vars()
        self.assertEqual(pack_gpio_register(0xC0C0, a, b), 0xCECE)
        for index in range(2):
            self.host.gpio_od_vars[index].set(True)
        a, b = self.host._gpio_pending_from_vars()
        self.assertEqual(pack_gpio_register(0xC0C0, a, b), 0xCFCF)
        for mode in self.host.mode_pages.values():
            for w in descendants(mode):
                if isinstance(w, ttk.Combobox):
                    self.assertNotIn("DEBUG_OUTPUT", tuple(w.cget("values")))

    def test_02_gpio_readback_syncs_both_pages_without_rewriting_bits(self):
        self.host._apply_pending_to_vars(GpioChannelBits(0x08, False), GpioChannelBits(0x07, True))
        self.assertEqual([v.get() for v in self.host.gpio_drive_mode_vars], ["Push-Pull", "OpenDrian"])
        self.assertEqual(self.host._gpio_pending_from_vars(), (GpioChannelBits(0x08, False), GpioChannelBits(0x07, True)))
        self.host._apply_pending_to_vars(GpioChannelBits(0x0C, True), GpioChannelBits(0x07, True))
        self.assertEqual(self.host.gpio_category_vars[0].get(), "0x0C (readback)")
        self.assertEqual(self.host._gpio_function_labels(0), ())
        self.assertEqual(self.host._gpio_pending_from_vars()[0], GpioChannelBits(0x0C, True))
        self.host._apply_pending_to_vars(GpioChannelBits(0x07, True), GpioChannelBits(0x07, True))

    def test_03_bits_follow_stage_precision_and_sample(self):
        for key, mode in self.host.mode_pages.items():
            stages = mode._button_stage_vars if key == "button_mode" else mode._stage_vars
            select = mode._show_button_stage if key == "button_mode" else mode._show_stage
            for stage in (0, 1):
                select(stage)
                for high in (False, True):
                    stages[stage]["use_high_12_bits"].set(high)
                    mode._finish_adc_value_read(value=0xA5C3, error=None, show_error=False,
                                               generation=getattr(mode, "_adc_lifecycle_generation", 0), on_complete=None)
                    expected = set(range(4, 16) if high else range(12))
                    selected = {bit for bit, w in zip(range(15, -1, -1), mode._adc_bit_labels)
                                if w.cget("background") != mode.colors["panel"]}
                    self.assertEqual(selected, expected)
                    self.assertEqual("".join(w.cget("text").split("\n")[1] for w in mode._adc_bit_labels), "1010010111000011")
                select(1 - stage)
                self.assertEqual(len(mode._adc_bit_labels), 16)

    def test_04_sinc_period_is_first_pair_fixed_256_and_updates(self):
        p = self.page
        p.sinc_serial_var.set(AWP1221_SINC_SERIAL_OPTIONS[0])
        p.rom_waveform_var.set("SINC")
        p.sinc_time1_var.set("8")
        p.sinc_time2_var.set("10")
        self.assertIn("4608 μs", p.sinc_haptic_period_var.get())
        p.sinc_time3_var.set("32")
        self.assertIn("4608 μs", p.sinc_haptic_period_var.get())
        p.sinc_depth_var.set("256")
        self.assertIn("4608 μs", p.sinc_haptic_period_var.get())
        p.sinc_time1_var.set("")
        self.assertIn("—", p.sinc_haptic_period_var.get())
        p.sinc_time1_var.set("12")
        self.assertIn("5632 μs", p.sinc_haptic_period_var.get())
        label = p._selector_widgets["awp1221_sinc_haptic_period"][0]
        p.rom_waveform_var.set("SINE")
        self.assertEqual(label.winfo_manager(), "")
        p.rom_waveform_var.set("SINC")
        self.assertEqual(label.winfo_manager(), "grid")
        p.sinc_serial_var.set(AWP1221_SINC_SERIAL_OPTIONS[1])
        self.assertIn("—", p.sinc_haptic_period_var.get())
        p.sinc_serial_var.set(AWP1221_SINC_SERIAL_OPTIONS[0])

    def test_05_help_uses_existing_question_tooltip(self):
        self.app.state.language = "zh"
        self.host.refresh_language()
        expected = ["代表连续超过阈值多少时间触发ADC事件。",
                    "ADC在此段时间内，不计数，但仍进行ADC检测，输入单位为ms。",
                    "波形播放完成后播放所选时间的陶瓷片两端0V。",
                    "配置press事件触发之后的退bit时间周期，每经过1个周期退1个码字。",
                    "配置release事件触发之后的退bit时间周期，每经过1个周期退1个码字。"]
        for mode in self.host.mode_pages.values():
            texts = [tip.text_getter() for tip in mode._tooltips]
            for text in expected:
                self.assertIn(text, texts)
            tooltip = next(t for t in mode._tooltips if t.text_getter() == expected[0])
            self.assertEqual(tooltip.widget.cget("text"), "?")
            tooltip._show(None)
            self.assertIsNotNone(tooltip.tip)
            tooltip._hide()

    def test_06_theme_and_visual_evidence(self):
        evidence = Path("docs/specs/awp1221_annotations_evidence")
        evidence.mkdir(parents=True, exist_ok=True)
        self.host.show_mode("button_mode")
        self.page._show_button_stage(0)
        self.page._button_stage_vars[0]["use_high_12_bits"].set(True)
        self.page.sinc_depth_var.set("512")
        self.page.sinc_time1_var.set("8")
        self.page.sinc_time2_var.set("10")
        for theme in ("light", "dark", "light"):
            self.app.state.theme = theme
            self.app._apply_visual_settings()
            _flush(self.root)
            for mode in self.host.mode_pages.values():
                colors = {w.cget("background") for w in mode._adc_bit_labels}
                self.assertEqual(colors, {mode.colors["panel"], "#476C9B" if theme == "dark" else "#A9C7EA"})
            error = _capture_window_bmp(self.root, evidence / f"{theme}.bmp")
            self.assertIsNone(error)
            adc_area = self.page._vertical_scroll_areas[0]
            target = self.page._adc_bit_labels[0]
            offset = target.winfo_rooty() - adc_area.content.winfo_rooty()
            adc_area.canvas.yview_moveto(max(0, offset - 180) / adc_area.content.winfo_height())
            self.page._waveform_selector_scroll.canvas.yview_moveto(1)
            _flush(self.root)
            self.assertGreaterEqual(target.winfo_rooty(), adc_area.canvas.winfo_rooty())
            self.assertLessEqual(target.winfo_rooty() + target.winfo_height(),
                                 adc_area.canvas.winfo_rooty() + adc_area.canvas.winfo_height())
            last_bit = self.page._adc_bit_labels[-1]
            self.assertLessEqual(last_bit.winfo_rootx() + last_bit.winfo_width(),
                                 adc_area.canvas.winfo_rootx() + adc_area.canvas.winfo_width())
            self.assertIsNone(_capture_window_bmp(self.root, evidence / f"{theme}-adc-period.bmp"))
            self.page._button_stage_vars[0]["use_high_12_bits"].set(False)
            _flush(self.root)
            self.assertIsNone(_capture_window_bmp(self.root, evidence / f"{theme}-adc-low12.bmp"))
            self.page._button_stage_vars[0]["use_high_12_bits"].set(True)
            adc_area.canvas.yview_moveto(1)
            _flush(self.root)
            self.assertIsNone(_capture_window_bmp(self.root, evidence / f"{theme}-gpio.bmp"))
            adc_area.canvas.yview_moveto(0)
            self.page._waveform_selector_scroll.canvas.yview_moveto(0)


if __name__ == "__main__":
    unittest.main()
