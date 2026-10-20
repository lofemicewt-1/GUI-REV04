from __future__ import annotations

import base64
import json
from unittest.mock import patch
import unittest

from pages.awp1221_page import AWP1221ChipSettingsModel
from pages.chip_settings_model import can_save_slot


class Awp1221Type4ModelPreflightTests(unittest.TestCase):
    def test_full_stage_combination_outside_type4_blocks_slot_save(self) -> None:
        model = AWP1221ChipSettingsModel()
        model.set_advanced("type4_unsupported", ("stage0:slope",))

        self.assertEqual(
            can_save_slot(model),
            (False, "preset_slot_advanced_block_warning"),
        )


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
