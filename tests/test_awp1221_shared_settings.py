from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from pages.awp1221_page import (
    AWP1221ChipSettingsModel,
    AWP1221ModePage,
    decode_awp1221_adc_wave,
)
from pages.awp1221_board_presets import AWP1221BoardPresetMixin
from pages.chip_settings_model import can_save_slot


class Value:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Awp1221Type4ModelPreflightTests(unittest.TestCase):
    def test_full_stage_combination_outside_type4_blocks_slot_save(self) -> None:
        model = AWP1221ChipSettingsModel()
        model.set_advanced("type4_unsupported", ("stage0:slope",))

        self.assertEqual(
            can_save_slot(model),
            (False, "preset_slot_advanced_block_warning"),
        )

    def test_button_capture_preserves_unedited_full_stage_register_words_and_blockers(self) -> None:
        control = (4 << 12) | (5 << 8) | 0x00DD
        press = 0xF800
        release = 0x4123
        slope = 0x0102
        wave = 0x6422
        registers = AWP1221ModePage.STAGE_REGISTER_RANGES[0]
        button = {
            "mode": "press", "time_code": 5, "target": 1, "debounce": True,
            "use_high_12_bits": True, "threshold": 0x800, "wave": 2, "repeat": 1,
        }
        old_values = (control, press, release, slope, wave)
        unsupported = AWP1221ModePage._type4_unsupported_stage(old_values, button, 0)
        model = AWP1221ChipSettingsModel(shared={
            "stage0_semantics": {
                "button": dict(button),
                "register_values": old_values,
                "type4_unsupported": unsupported,
            },
        })
        page = SimpleNamespace(
            mode_key="button_mode",
            STAGE_REGISTER_RANGES=AWP1221ModePage.STAGE_REGISTER_RANGES,
            _button_stage_vars=[{}],
            _stage_vars=[],
            _button_stage_config=lambda _index: dict(button),
            _merge_button_stage_semantics=lambda index, current, previous, current_button: (
                AWP1221ModePage._merge_button_stage_semantics(
                    page, index, current, previous, current_button
                )
            ),
            _button_stage_values_from_config=lambda index, current, config: (
                AWP1221ModePage._button_stage_values_from_config(page, index, current, config)
            ),
            _type4_unsupported_stage=AWP1221ModePage._type4_unsupported_stage,
        )

        captured = AWP1221ModePage._capture_stage_semantics(page, 0, model)

        self.assertEqual(captured["register_values"], old_values)
        self.assertEqual(captured["type4_unsupported"], unsupported)
        self.assertIn("stage0:slope", unsupported)
        model.set_shared("stage0_semantics", captured)
        model.set_advanced("type4_unsupported", unsupported)
        self.assertEqual(can_save_slot(model), (False, "preset_slot_advanced_block_warning"))

        edited = dict(button, wave=7)
        merged = AWP1221ModePage._merge_button_stage_semantics(
            page,
            0,
            dict(zip(registers, old_values)),
            button,
            edited,
        )
        self.assertEqual(merged[registers[0]], control)
        self.assertEqual(merged[registers[3]], slope)
        merged_wave = decode_awp1221_adc_wave(merged[registers[4]])
        self.assertEqual(merged_wave["press_wave"], 7)
        self.assertEqual(merged_wave["release_wave"], 7)
        self.assertEqual(merged_wave["press_repeat"], 1)
        self.assertEqual(merged_wave["release_repeat"], 3)

    def test_full_type4_capture_uses_stage_recipes_from_settings_model(self) -> None:
        from pages.awp1221_page import AWP1221_RETURN_BIT_TIME_OPTIONS, AWP1221_ZERO_TIME_OPTIONS

        buttons = (
            {"mode": "press", "time_code": 3, "target": 1, "debounce": False,
             "use_high_12_bits": True, "threshold": 0x800, "wave": 5, "repeat": 2},
            {"mode": "release", "time_code": 3, "target": 0, "debounce": False,
             "use_high_12_bits": True, "threshold": 0x123, "wave": 5, "repeat": 3},
        )
        model = AWP1221ChipSettingsModel(shared={
            "stage0_semantics": {"button": buttons[0]},
            "stage1_semantics": {"button": buttons[1]},
        })
        page = SimpleNamespace(
            mode_key="full_mode",
            _button_stage_vars=[],
            _capture_settings_model=lambda: model,
            _remember_board_rom=lambda: None,
            _sinc_active_group=None,
            _sinc_selected=lambda: False,
            _board_rom_values={5: ("SINE", "0x1F", "0x2", "0x3", "7", "256", "ROM")},
            _sinc_group_values={},
            _format_trigger_waveform=lambda number: f"slice{number}",
            _parse_trigger_waveform=AWP1221ModePage._parse_trigger_waveform,
            _parse_binary_option=AWP1221ModePage._parse_binary_option,
            zero_time_var=Value(AWP1221_ZERO_TIME_OPTIONS[0]),
            press_return_bit_time_var=Value(AWP1221_RETURN_BIT_TIME_OPTIONS[1]),
            release_return_bit_time_var=Value(AWP1221_RETURN_BIT_TIME_OPTIONS[2]),
            sinc_max_voltage_var=Value("80"),
            sinc_min_voltage_var=Value("12"),
            tr=lambda key, **_kwargs: key,
        )

        captured = AWP1221BoardPresetMixin._capture_type4_model(page)

        self.assertEqual(captured.thresholds, (0x800, 0x123))
        self.assertEqual(captured.event_repeats, (2, 3))
        self.assertEqual(captured.wave_refs, (0, 0))
        self.assertEqual(captured.jumps, (1, 0))
        self.assertEqual(captured.waves[0].times, (7,))


class Awp1221SharedSettingsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.validate_responsive_layout import _flush, mocked_application

        cls._flush = staticmethod(_flush)
        cls.fixture = mocked_application()
        cls.app = cls.fixture.__enter__()
        cls.host = cls.app.navigation.pages["awp1221"]
        cls.root = cls.app.root
        cls.app.navigation.show("awp1221")
        cls._flush(cls.root)
        cls.host.show_mode("full_mode")
        cls._flush(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.__exit__(None, None, None)

    def setUp(self) -> None:
        self.host.show_mode("full_mode")
        self._flush(self.root)
        self.full = self.host.mode_pages["full_mode"]
        self.button = self.host.mode_pages["button_mode"]

    def test_full_mode_toolbar_actions_are_enabled(self) -> None:
        toolbar = self.full.preset_toolbar
        self.assertIsNotNone(toolbar)
        self.assertEqual(len(toolbar.buttons), 9)
        for action, button in toolbar.buttons.items():
            with self.subTest(action=action):
                self.assertNotEqual(str(button.cget("state")), "disabled")
        self.assertNotIn("awp1221_preset_1_reserved", self.full._buttons)

    def test_shared_model_updates_both_views_and_full_adapter_stands_alone(self) -> None:
        model = self.host._settings_model
        recipe = {
            "kind": "rom", "auto_serial": True, "serial": "serial1",
            "slice_index": 6, "source": "ROM", "waveform": "SINE",
            "rom_depth": "512", "micro_bias": self.full.MICRO_BIAS_OPTIONS[0],
            "vpk": self.full.VPK_OPTIONS[4], "offset": self.full.OFFSET_OPTIONS[0],
            "half_cycle": self.full.HALF_CYCLE_OPTIONS_BY_WAVEFORM["SINE"][0],
            "phase": "0", "point_time": "1", "rom_repeat": "1",
            "infinite_loop": False,
            "sinc_group": ["80", "12", "8", "10", "50", "4", "1", "512", "-"],
        }
        settings = dict(model.shared.get("adc_settings", {}))
        settings.update({
            "debounce_time": "100", "sample_update": "8 us", "zero_time": "32 ms",
            "press_return_bit_time": "800 us", "release_return_bit_time": "800 us",
            "zps_enabled": True, "zps_detect": True, "sleep_sensitivity": False,
            "adc_ldo": "40 uA", "auto_serial": True,
        })
        button_config = {
            "mode": "press", "time_code": 3, "target": 1, "debounce": False,
            "use_high_12_bits": True, "threshold": 321, "wave": 6, "repeat": 2,
        }
        current = {register: 0 for register in self.full.STAGE_REGISTER_RANGES[0]}
        words = self.full._button_stage_values_from_config(0, current, button_config)
        full_words = tuple(words.get(register, 0) for register in self.full.STAGE_REGISTER_RANGES[0])
        model.update_shared({
            "waveform_recipe": recipe,
            "waveform_descriptors": {
                "rom_by_slice": {"6": ["SINE", self.full.VPK_OPTIONS[4], self.full.MICRO_BIAS_OPTIONS[0], self.full.OFFSET_OPTIONS[0], "1", "1", "ROM"]},
                "sinc_groups": {},
            },
            "adc_settings": settings,
            "stage0_semantics": {
                "button": button_config, "register_values": full_words,
                "type4_unsupported": (),
            },
        })
        self.host.apply_settings_model(model)

        self.assertEqual(self.full.rom_slice_var.get(), "slice6")
        self.assertEqual(self.button.rom_slice_var.get(), "slice6")
        self.assertEqual(self.full.adc_sample_update_var.get(), "8 us")
        self.assertEqual(self.button.adc_sample_update_var.get(), "8 us")
        self.assertEqual(self.button._button_stage_vars[0]["threshold"].get(), "321")
        self.assertEqual(self.full._stage_vars[0]["press_threshold"]["threshold"].get(), "321")

        saved_button_stages = self.button._button_stage_vars
        self.button._button_stage_vars = []
        try:
            self.full._apply_settings_model_to_view(model)
        finally:
            self.button._button_stage_vars = saved_button_stages

    def test_full_software_preset_uses_model_without_button_stage_widgets(self) -> None:
        saved_button_page = dict.pop(self.host.mode_pages, "button_mode")
        try:
            with patch.object(self.full, "_ensure_connected", return_value=False):
                self.assertFalse(self.full.apply_software_preset(0))
            self.assertEqual(self.host._settings_model.shared["waveform_recipe"]["waveform"], "SINC")
            self.assertEqual(self.full.rom_waveform_var.get(), "SINC")
        finally:
            dict.__setitem__(self.host.mode_pages, "button_mode", saved_button_page)

    def test_full_page_file_metadata_keeps_full_stage_fields(self) -> None:
        slope = self.full._stage_vars[0]["slope"]["press_slope"]
        slope.set("9")
        self._flush(self.root)

        operations = self.full._build_current_register_script_operations()
        from core.awp1221_page_script import extract_awp1221_page_script

        snapshot = extract_awp1221_page_script(operations)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        payload = json.loads(base64.urlsafe_b64decode(snapshot.advanced_state + "=" * (-len(snapshot.advanced_state) % 4)))
        stage0 = payload["shared"]["stage0_semantics"]["register_values"]
        self.assertEqual(stage0[3], 9)
        self.assertTrue(any(operation.action == "WRITE" and operation.reg_addr == 0x0B and operation.value == 9 for operation in operations))


if __name__ == "__main__":
    unittest.main()
