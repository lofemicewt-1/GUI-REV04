from __future__ import annotations

from types import SimpleNamespace
import unittest

from pages.awp1221_page import (
    AWP1221ModePage,
    AWP1221_ADC_LOAD_REGISTER,
    AWP1221_ADC_SETTINGS_REGISTER,
    AWP1221_DEBOUNCE_REGISTER,
    AWP1221_RETURN_BIT_REGISTER,
    awp1221_mismatch_groups,
    awp1221_settings_target_words,
    awp1221_verified_start_plan,
)


def _settings(**overrides):
    values = {
        "sample_update": "16 us",
        "zero_time": "4 ms",
        "press_return_bit_time": "off",
        "release_return_bit_time": "6400 us",
        "zps_enabled": True,
        "zps_detect": True,
        "sleep_sensitivity": True,
        "auto_serial": False,
        "debounce_code": 0,
        "adc_ldo": "160 uA",
    }
    values.update(overrides)
    return values


class Awp1221SettingsTargetTests(unittest.TestCase):
    def test_owned_fields_and_preserved_bits(self) -> None:
        current = {
            AWP1221_ADC_SETTINGS_REGISTER: 0xFFFF,
            AWP1221_RETURN_BIT_REGISTER: 0xFFFF,
            AWP1221_DEBOUNCE_REGISTER: 0xABCD,
            AWP1221_ADC_LOAD_REGISTER: 0x0708,
        }
        words = awp1221_settings_target_words(current, _settings())
        self.assertEqual(words[AWP1221_ADC_SETTINGS_REGISTER], 0x5BDF)
        self.assertEqual(words[AWP1221_RETURN_BIT_REGISTER], 0xFF07)
        self.assertEqual(words[AWP1221_DEBOUNCE_REGISTER], 0xA800)
        self.assertEqual(words[AWP1221_ADC_LOAD_REGISTER], 0x0F08)

    def test_sram_mode_bit_is_not_a_settings_mismatch(self) -> None:
        current = {
            AWP1221_ADC_SETTINGS_REGISTER: 0x0001,
            AWP1221_RETURN_BIT_REGISTER: 0x0000,
            AWP1221_DEBOUNCE_REGISTER: 0x0000,
            AWP1221_ADC_LOAD_REGISTER: 0x0008,
        }
        settings = _settings(
            sample_update="4 us",
            zero_time="1 ms",
            release_return_bit_time="off",
            zps_enabled=False,
            zps_detect=False,
            sleep_sensitivity=False,
            adc_ldo="40 uA",
        )
        expected = awp1221_settings_target_words(current, settings)
        self.assertEqual(expected[AWP1221_ADC_SETTINGS_REGISTER], 0x0001)
        self.assertEqual(expected[AWP1221_ADC_LOAD_REGISTER], 0x0008)
        groups = awp1221_mismatch_groups(
            current,
            expected,
            {"settings": tuple(current)},
        )
        self.assertEqual(groups, ())


class Awp1221VerifiedStartPlanTests(unittest.TestCase):
    def test_match_sends_command(self) -> None:
        self.assertEqual(awp1221_verified_start_plan((), None), "command")

    def test_rewrite_writes_then_starts(self) -> None:
        self.assertEqual(awp1221_verified_start_plan(("settings",), "rewrite"), "rewrite-then-command")

    def test_start_without_rewrite_sends_command(self) -> None:
        self.assertEqual(awp1221_verified_start_plan(("stage0",), "start"), "command")

    def test_cancel_aborts(self) -> None:
        self.assertEqual(awp1221_verified_start_plan(("stage1",), "cancel"), "abort")
        self.assertEqual(awp1221_verified_start_plan(("settings",), None), "abort")


class _Serial:
    def __init__(self, connected: bool = True) -> None:
        self.is_connected = connected
        self.writes: list[tuple[int, int]] = []

    def snapshot_transaction(self):
        from contextlib import nullcontext
        return nullcontext()


class Awp1221LiveWriteTests(unittest.TestCase):
    def _page(self, connected: bool = True) -> AWP1221ModePage:
        page = object.__new__(AWP1221ModePage)
        page.ctx = SimpleNamespace(serial=_Serial(connected))
        page.mode_key = "button_mode"
        page._adc_live_write_depth = 0
        page._ops: list[str] = []
        page._errors: list[str] = []
        page._write_adc_settings = lambda *args, **kwargs: page._ops.append("settings")
        page._write_button_adc_stage = lambda *args, **kwargs: page._ops.append("stage")
        page._write_adc_stage = lambda *args, **kwargs: page._ops.append("full-stage")
        page._sync_realtime_adc_load = lambda *args, **kwargs: None
        page._show_live_write_error = lambda exc: page._errors.append(str(exc))
        page.tr = lambda key: key
        return page

    def test_settings_commit_writes_when_connected(self) -> None:
        page = self._page()
        page._commit_live_settings()
        self.assertEqual(page._ops, ["settings"])

    def test_disconnected_commit_does_not_write(self) -> None:
        page = self._page(connected=False)
        page._commit_live_settings()
        page._commit_live_stage(0)
        self.assertEqual(page._ops, [])

    def test_suspend_suppresses_write(self) -> None:
        page = self._page()
        with page._suspend_adc_live_writes():
            page._commit_live_settings()
            page._commit_live_stage(1)
        self.assertEqual(page._ops, [])
        page._commit_live_stage(1)
        self.assertEqual(page._ops, ["stage"])

    def test_invalid_stage_text_does_not_write(self) -> None:
        page = self._page()

        def reject(*_args, **_kwargs):
            raise ValueError("bad threshold")

        page._write_button_adc_stage = reject
        page._commit_live_stage(0)
        self.assertEqual(page._errors, ["bad threshold"])


class Awp1221VerifiedStartTests(unittest.TestCase):
    def _page(self) -> AWP1221ModePage:
        page = object.__new__(AWP1221ModePage)
        page.ctx = SimpleNamespace(serial=_Serial())
        page.mode_key = "button_mode"
        page._ops: list[str] = []
        page._write_adc_settings = lambda *args, **kwargs: page._ops.append("settings")
        page._write_button_adc_stage = lambda *args, **kwargs: page._ops.append(f"stage{args[0]}")
        page._write_adc_stage = lambda *args, **kwargs: page._ops.append(f"full{args[0]}")
        page._write_control_command = lambda *args, **kwargs: page._ops.append("command")
        page._ensure_reg_1f_bit3 = lambda *args, **kwargs: page._ops.append("ensure")
        page._reads: list[int] = []

        def read(register, _address=None):
            page._reads.append(register)
            if register == AWP1221_ADC_SETTINGS_REGISTER:
                return 0x0000
            return 0

        page._read_register_value = read
        return page

    def test_rewrite_writes_only_differing_group_then_command(self) -> None:
        page = self._page()
        snapshot = {"device_addr": 0x50, "mode": "button_mode", "settings": {}, "stages": ({}, {}), "event_modes": None}
        page._apply_verified_start("start_play", snapshot, ("stage1",), "rewrite")
        self.assertEqual(page._ops, ["stage1", "command"])

    def test_start_without_rewrite_sends_only_command(self) -> None:
        page = self._page()
        snapshot = {"device_addr": 0x50, "mode": "button_mode", "settings": {}, "stages": ({}, {}), "event_modes": None}
        page._apply_verified_start("start_detection", snapshot, ("settings",), "start")
        self.assertEqual(page._ops, ["command"])

    def test_cancel_sends_nothing(self) -> None:
        page = self._page()
        snapshot = {"device_addr": 0x50, "mode": "button_mode", "settings": {}, "stages": ({}, {}), "event_modes": None}
        page._apply_verified_start("start_detection", snapshot, ("settings",), "cancel")
        self.assertEqual(page._ops, [])

    def test_read_gates_before_register_reads(self) -> None:
        page = self._page()
        page._expected_owned_words = lambda current, snapshot: dict(current)
        snapshot = {"device_addr": 0x50, "mode": "button_mode"}
        mismatches = page._read_owned_mismatches(0x50, snapshot)
        self.assertEqual(mismatches, ())
        self.assertEqual(page._ops[0], "ensure")
        self.assertTrue(page._reads)
        self.assertNotIn("command", page._ops)


if __name__ == "__main__":
    unittest.main()
