from __future__ import annotations

import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config.lang_r2_c import EN, EXISTING_KEY_OVERRIDES, ZH, install
from config.languages import t
from pages.awp1221_page import (
    AWP1221ModePage,
    AWP1221_SINC_SERIAL_OPTIONS,
)
from pages.awp192x_modes import ButtonModePage


class FakeVariable:
    def __init__(self, value=""):
        self.value = value
        self.callbacks = {}
        self.next_trace = 0
        self.writes = 0

    def get(self):
        return self.value

    def set(self, value):
        self.writes += 1
        self.value = value
        for callback in tuple(self.callbacks.values()):
            callback("", "", "write")

    def trace_add(self, _mode, callback):
        self.next_trace += 1
        trace_id = str(self.next_trace)
        self.callbacks[trace_id] = callback
        return trace_id

    def trace_remove(self, _mode, trace_id):
        self.callbacks.pop(trace_id, None)


class FakeWidget:
    def __init__(self):
        self.options = {}
        self.mapped = True

    def configure(self, **kwargs):
        self.options.update(kwargs)

    config = configure

    def grid(self, **_kwargs):
        self.mapped = True

    def grid_remove(self):
        self.mapped = False

    def bind(self, sequence, callback, add=None):
        self.options.setdefault("bindings", {})[sequence] = callback

    def winfo_exists(self):
        return True


class FakeCombo(FakeWidget):
    def __init__(self, interpreter):
        super().__init__()
        self.tk = interpreter.tk
        self.interpreter = interpreter

    def _root(self):
        return self.interpreter


class FakeStyle:
    def __init__(self, *_args, **_kwargs):
        self.configured = {}

    def configure(self, style, query_opt=None, **kwargs):
        options = self.configured.setdefault(style, {})
        if query_opt is not None:
            return options.get(query_opt, "")
        if not kwargs:
            return dict(options)
        options.update(kwargs)


class FakeSerialTrace:
    def __init__(self):
        self.writes = []

    def write_register_value(self, device_addr, register, value, *, bits):
        self.writes.append((device_addr, register, value, bits))
        return SimpleNamespace(ok=True, error="")


class ChoiceHarness:
    _bind_waveform_choice = AWP1221ModePage._bind_waveform_choice
    _waveform_choice_display = AWP1221ModePage._waveform_choice_display
    _refresh_waveform_choice_labels = AWP1221ModePage._refresh_waveform_choice_labels
    _refresh_auto_serial_warning = AWP1221ModePage._refresh_auto_serial_warning
    _auto_serial_enabled = AWP1221ModePage._auto_serial_enabled
    _open_full_mode = AWP1221ModePage._open_full_mode

    def __init__(self, language="zh"):
        self.language = language
        self.ctx = SimpleNamespace(colors={"text": "#111111", "muted": "#777777"})
        self.ctx.app = SimpleNamespace(navigation=SimpleNamespace(clicked=[]))
        self.ctx.app.navigation._on_mode_clicked = lambda key: self.ctx.app.navigation.clicked.append(key)
        self.mode_key = "button_mode"
        self.auto_serial = False
        self._waveform_choice_bindings = []
        self._waveform_choice_trace_ids = []
        self._button_stage_vars = []
        self._waveform_choice_notice_persistent = False

    def tr(self, key, **kwargs):
        return t(self.language, key, **kwargs)

    def _make_notice(self, persistent=False):
        self._waveform_choice_notice_persistent = persistent
        self._waveform_choice_notice_frame = FakeWidget()
        self._waveform_choice_notice_label = FakeWidget()
        self._waveform_choice_notice_link = FakeWidget()
        if not persistent:
            self._waveform_choice_notice_frame.grid_remove()


class R2cWaveformChoiceTests(unittest.TestCase):
    def setUp(self):
        install()
        self.interpreter = tk.Tcl()
        self.style_patch = patch("tkinter.ttk.Style", FakeStyle)
        self.style_patch.start()
        self.addCleanup(self.style_patch.stop)

    def combo(self):
        return FakeCombo(self.interpreter)

    def test_language_snippet_has_namespaced_text_and_glossary_override(self):
        self.assertTrue(all(key.startswith("r2c_") for key in ZH))
        self.assertTrue(all(key.startswith("r2c_") for key in EN))
        self.assertEqual(t("zh", "r2c_1221_normal_vibration"), "正常震感")
        self.assertEqual(t("en", "r2c_1221_normal_vibration"), "Normal vibration")
        self.assertEqual(t("zh", "r2c_1221_strong_vibration"), "强震感")
        self.assertEqual(t("en", "r2c_custom_waveform"), "Custom waveform")
        self.assertNotIn("中等振动效果", EXISTING_KEY_OVERRIDES[0][1])
        self.assertIn("正常震感", t("zh", "glossary_serial"))
        self.assertIn("normal vibration", t("en", "glossary_serial"))

    def test_1221_button_choices_and_full_options_remain_independent(self):
        button = ChoiceHarness()
        button.mode_key = "button_mode"
        self.assertEqual(
            AWP1221ModePage._trigger_waveform_options(button),
            ("serial1", "serial3", "slice0"),
        )
        full = ChoiceHarness()
        full.mode_key = "full_mode"
        full.auto_serial = True
        full_options = AWP1221ModePage._trigger_waveform_options(full)
        self.assertIn("slice5", full_options)
        self.assertIn("serial1 slice18-20", full_options)
        self.assertEqual(AWP1221ModePage._parse_trigger_waveform("serial1"), 18)
        self.assertEqual(AWP1221ModePage._parse_trigger_waveform("serial3"), 24)

    def test_1221_sinc_group_and_write_slice_dropdowns_are_narrowed(self):
        owner = ChoiceHarness()
        owner._form_label = lambda *_args, **_kwargs: FakeWidget()
        owner._selector_widgets = {}
        var = FakeVariable(AWP1221_SINC_SERIAL_OPTIONS[0])
        combo = self.combo()
        with patch("pages.awp1221_page.ttk.Combobox", return_value=combo):
            AWP1221ModePage._rom_combo(
                owner, FakeWidget(), 1, "awp1221_sinc_serial_group", var, AWP1221_SINC_SERIAL_OPTIONS
            )
        self.assertEqual(combo.options["values"], ("serial1", "serial3"))

        slice_var = FakeVariable("slice0")
        slice_combo = self.combo()
        with patch("pages.awp1221_page.ttk.Combobox", return_value=slice_combo):
            AWP1221ModePage._rom_combo(
                owner, FakeWidget(), 2, "awp1221_rom_slice", slice_var, tuple(f"slice{i}" for i in range(32))
            )
        self.assertEqual(slice_combo.options["values"], ("自定义波形",))
        self.assertEqual(slice_var.get(), "slice0")

    def test_192x_button_choices_keep_full_parser_range(self):
        self.assertEqual(ButtonModePage._slice_options(SimpleNamespace(page_name="button_mode")), ["slice0", "0x1F"])
        full = ButtonModePage._slice_options(SimpleNamespace(page_name="full_mode"))
        self.assertEqual(full, [*(f"slice{i}" for i in range(30)), "serial", "0x1F"])

    def test_localized_binding_preserves_raw_value_and_only_commits_user_selection(self):
        owner = ChoiceHarness("zh")
        owner._make_notice()
        raw = FakeVariable("slice7")
        combo = self.combo()
        commits = []
        owner._bind_waveform_choice(
            combo,
            raw,
            ("serial1", "serial3", "slice0"),
            callback=lambda: commits.append(raw.get()),
            label_keys={
                "serial1": "r2c_1221_normal_vibration",
                "serial3": "r2c_1221_strong_vibration",
                "slice0": "r2c_custom_waveform",
            },
            aliases={"slice18": "serial1"},
        )
        binding = owner._waveform_choice_bindings[0]
        self.assertEqual(binding["display"].get(), "slice7")
        self.assertIn("slice7", combo.options["values"])
        self.assertEqual(combo.options["style"], "R2cUnsupportedWaveform.TCombobox")
        self.assertTrue(owner._waveform_choice_notice_frame.mapped)
        self.assertIn("slice7", owner._waveform_choice_notice_label.options["text"])
        self.assertEqual(raw.get(), "slice7")
        self.assertEqual(commits, [])

        owner.language = "en"
        owner._refresh_waveform_choice_labels()
        self.assertEqual(raw.get(), "slice7")
        self.assertEqual(commits, [])

        raw.set("serial1")
        self.assertEqual(binding["display"].get(), "Normal vibration")
        self.assertFalse(owner._waveform_choice_notice_frame.mapped)
        binding["display"].set("Strong vibration")
        combo.options["bindings"]["<<ComboboxSelected>>"](None)
        self.assertEqual(raw.get(), "serial3")
        self.assertEqual(commits, ["serial3"])

    def test_press_and_release_bindings_are_independent(self):
        owner = ChoiceHarness("en")
        press, release = FakeVariable("serial1"), FakeVariable("slice0")
        press_combo, release_combo = self.combo(), self.combo()
        options = ("serial1", "serial3", "slice0")
        labels = {
            "serial1": "r2c_1221_normal_vibration",
            "serial3": "r2c_1221_strong_vibration",
            "slice0": "r2c_custom_waveform",
        }
        owner._bind_waveform_choice(press_combo, press, options, label_keys=labels)
        owner._bind_waveform_choice(release_combo, release, options, label_keys=labels)
        release_display = owner._waveform_choice_bindings[1]["display"]
        owner._waveform_choice_bindings[0]["display"].set("Strong vibration")
        press_combo.options["bindings"]["<<ComboboxSelected>>"](None)
        self.assertEqual(press.get(), "serial3")
        self.assertEqual(release.get(), "slice0")
        self.assertEqual(release_display.get(), "Custom waveform")

    def test_auto_serial_off_warns_without_changing_checkbox_or_raw_values(self):
        owner = ChoiceHarness("zh")
        owner.auto_serial = False
        owner.auto_serial_var = FakeVariable(False)
        owner._auto_serial_warning_label = FakeWidget()
        wave = FakeVariable("serial1")
        owner._button_stage_vars = [{"wave": wave}]
        owner._refresh_auto_serial_warning()
        self.assertTrue(owner._auto_serial_warning_label.mapped)
        self.assertEqual(owner._auto_serial_warning_label.options["text"], "需勾选自动串联才能用此效果")
        self.assertFalse(owner.auto_serial_var.get())
        self.assertEqual(wave.get(), "serial1")

        owner.auto_serial_var.set(True)
        owner._refresh_auto_serial_warning()
        self.assertFalse(owner._auto_serial_warning_label.mapped)

    def test_opening_sinc_does_not_enable_auto_serial_and_full_link_uses_navigation(self):
        owner = ChoiceHarness()
        owner.auto_serial_var = FakeVariable(False)
        owner.rom_source_var = FakeVariable("RAM")
        owner.rom_waveform_var = FakeVariable("SINE")
        owner._sinc_selected = lambda: False
        owner._refresh_waveform_controls = lambda: None
        AWP1221ModePage._select_button_wave_tab(owner, "sinc")
        self.assertFalse(owner.auto_serial_var.get())
        self.assertEqual(owner.rom_source_var.get(), "ROM")
        self.assertEqual(owner.rom_waveform_var.get(), "SINC")
        owner._open_full_mode()
        self.assertEqual(owner.ctx.app.navigation.clicked, ["full_mode"])

    def test_192x_display_labels_do_not_replace_the_raw_rom_number(self):
        owner = ChoiceHarness("zh")
        owner._make_notice(persistent=True)
        raw = FakeVariable("0x1F")
        combo = self.combo()
        owner._bind_waveform_choice(
            combo,
            raw,
            ("slice0", "0x1F"),
            label_keys={"slice0": "r2c_custom_waveform", "0x1F": "r2c_rom_sine"},
        )
        display = owner._waveform_choice_bindings[0]["display"]
        self.assertEqual(display.get(), "ROM 正弦")
        owner.language = "en"
        owner._refresh_waveform_choice_labels()
        self.assertEqual(display.get(), "ROM sine")
        self.assertEqual(raw.get(), "0x1F")

    def test_1221_equivalent_button_choices_keep_ordered_write_trace(self):
        current = {register: register * 0x101 for register in (0x08, 0x09, 0x0A, 0x0C)}

        def write_trace(raw_wave):
            serial = FakeSerialTrace()
            page = object.__new__(AWP1221ModePage)
            page.ctx = SimpleNamespace(serial=serial)
            page._read_adc_value = lambda register, _address=None: current[register]
            config = {
                "mode": "press",
                "time_code": 2,
                "target": 1,
                "debounce": True,
                "use_high_12_bits": False,
                "threshold": 0x234,
                "wave": AWP1221ModePage._parse_trigger_waveform(raw_wave),
                "repeat": 3,
            }
            AWP1221ModePage._write_button_adc_stage(page, 0, config, 0x50)
            return serial.writes

        for old_label, new_value in (
            ("serial1 slice18-20", "serial1"),
            ("serial3 slice24-27", "serial3"),
            ("slice0", "slice0"),
        ):
            with self.subTest(choice=new_value):
                self.assertEqual(write_trace(new_value), write_trace(old_label))

        # Display binding and repeated label refreshes are local UI work only.
        serial = FakeSerialTrace()
        owner = ChoiceHarness("en")
        owner.ctx.serial = serial
        owner._make_notice()
        combo = self.combo()
        raw = FakeVariable("serial1")
        owner._bind_waveform_choice(
            combo,
            raw,
            ("serial1", "serial3", "slice0"),
            label_keys={
                "serial1": "r2c_1221_normal_vibration",
                "serial3": "r2c_1221_strong_vibration",
                "slice0": "r2c_custom_waveform",
            },
        )
        for _ in range(4):
            owner._refresh_waveform_choice_labels()
        self.assertEqual(serial.writes, [])


if __name__ == "__main__":
    unittest.main()
