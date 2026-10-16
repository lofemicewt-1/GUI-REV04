from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import unittest

from core.awp1221_page_script import (
    Awp1221PageScript,
    Awp1221StageScript,
    awp1221_sram_mode_value,
    build_awp1221_page_script_operations,
    extract_awp1221_page_script,
)
from core.base_tool import format_register_script, parse_register_script
from pages.awp1221_page import (
    AWP1221_INTERFACE_CONFIG_0_DEFAULT,
    AWP1221_SRAM_PLAY_MODE,
    AWP1221_SRAM_WRITE_READ_MODE,
)


def _stage(mode: str, threshold: str, wave: int) -> Awp1221StageScript:
    return Awp1221StageScript(mode, 3, 1 if mode == "press" else 0, threshold, False, True, wave, 0)


def _snapshot(**overrides: object) -> Awp1221PageScript:
    values = dict(
        kind="rom",
        auto_serial=True,
        serial="serial1",
        depth="512",
        window_index=0,
        max_voltage="80",
        min_voltage="12",
        times_us=("8", "10", "50", "4"),
        sin_repeat="1",
        slice_index=2,
        source="ROM",
        waveform="SINE",
        fine=1,
        coarse=4,
        offset=0,
        half=0,
        phase=0,
        point_time="4",
        rom_repeat="2",
        infinite=False,
        stages=(_stage("press", "300", 18), _stage("release", "-12", 18)),
        debounce_ms="100",
        sample_index=0,
        zero_index=2,
        press_return_index=1,
        release_return_index=1,
        zps_enabled=True,
        zps_detect=False,
        sleep_sensitivity=False,
        ldo_index=0,
    )
    values.update(overrides)
    return Awp1221PageScript(**values)


class Awp1221PageScriptTests(unittest.TestCase):
    def test_format_parse_round_trip_keeps_page_fields(self) -> None:
        original = _snapshot()
        operations = build_awp1221_page_script_operations(
            0x50,
            original,
            sram_slices=((2, (0x111, 0x222, 0x333, 0x044)),),
            stage_words=({0x08: 0x1111, 0x09: 0x2222}, {0x0D: 0x3333}),
            settings_words={0x05: 0x442E, 0x1F: 0x0008},
            play_slice=2,
        )
        restored = extract_awp1221_page_script(parse_register_script(format_register_script(operations)))
        self.assertEqual(restored, original)

    def test_script_replays_sram_before_stage_and_settings(self) -> None:
        operations = build_awp1221_page_script_operations(
            0x51,
            _snapshot(),
            sram_slices=((2, (0x111, 0x222, 0x333, 0x044)),),
            stage_words=({0x08: 0x1111, 0x0C: 0xABCD},),
            settings_words={0x05: 0x1234, 0x1F: 0x0808},
            play_slice=2,
        )
        writes = [
            (operation.device_addr, operation.reg_addr, operation.value)
            for operation in operations
            if operation.action == "WRITE"
        ]
        self.assertEqual(
            writes[:7],
            [
                (0x51, 0x05, awp1221_sram_mode_value(AWP1221_SRAM_WRITE_READ_MODE)),
                (0x51, 0x15, 8),
                (0x51, 0x00, 0x111),
                (0x51, 0x00, 0x222),
                (0x51, 0x00, 0x333),
                (0x51, 0x00, 0x044),
                (0x51, 0x05, awp1221_sram_mode_value(AWP1221_SRAM_PLAY_MODE)),
            ],
        )
        self.assertEqual(writes[7], (0x51, 0x0C, 0x0202))
        self.assertEqual(writes[-2:], [(0x51, 0x05, 0x1234), (0x51, 0x1F, 0x0808)])
        self.assertLess(writes.index((0x51, 0x0C, 0xABCD)), writes.index((0x51, 0x05, 0x1234)))
        self.assertEqual(AWP1221_INTERFACE_CONFIG_0_DEFAULT & ~0x0003 | AWP1221_SRAM_PLAY_MODE, writes[6][2])

    def test_script_without_metadata_does_not_invent_a_page(self) -> None:
        text = format_register_script([])
        text += "0x50, WRITE, 0x05, 0x0001, plain register write\n"
        self.assertIsNone(extract_awp1221_page_script(parse_register_script(text)))


class _FakeSerial:
    is_connected = True

    def __init__(self) -> None:
        self.writes: list[tuple[int, int, int]] = []
        self.memory = {0x1F: 0x0700}

    def snapshot_transaction(self):
        from contextlib import nullcontext
        return nullcontext()

    def read_register_value(self, device_addr: int, register: int, bits: int = 16):
        del bits
        value = self.memory.get((device_addr, register), self.memory.get(register, 0))
        return SimpleNamespace(ok=True, data=str(value), error="")

    def extract_rdataw_value(self, data: str, **_kwargs: object) -> int:
        return int(data)

    def write_register_value(self, device_addr: int, register: int, value: int, bits: int = 16):
        del bits
        self.writes.append((device_addr, register, value))
        self.memory[(device_addr, register)] = value
        self.memory[register] = value
        return SimpleNamespace(ok=True, error="")


class Awp1221PageSettingsButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.validate_responsive_layout import _flush, mocked_application

        cls.context = mocked_application()
        cls.app = cls.context.__enter__()
        cls.host = cls.app.navigation.pages["awp1221"]
        cls.root = cls.app.root
        cls.app.navigation.show("awp1221")
        _flush(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.context.__exit__(None, None, None)

    def setUp(self) -> None:
        self.page = self.host.mode_pages["button_mode"]

    def test_export_button_saves_script_without_chip_io(self) -> None:
        with (
            patch("pages.awp1221_page.filedialog.asksaveasfilename", return_value="") as ask,
            patch("pages.awp1221_page.messagebox.showerror") as error,
        ):
            self.page._buttons["button_mode_export_user_preset"].invoke()
        self.assertTrue(ask.called)
        self.assertFalse(error.called)

    def test_import_button_is_no_longer_a_placeholder(self) -> None:
        with (
            patch("pages.awp1221_page.filedialog.askopenfilename", return_value="") as ask,
            patch("pages.awp1221_page.messagebox.showerror") as error,
        ):
            self.page._buttons["button_mode_import_user_preset"].invoke()
        self.assertTrue(ask.called)
        self.assertFalse(error.called)

    def test_built_script_round_trips_into_the_page(self) -> None:
        self.page._load_button_software_preset(self.page.BUTTON_SOFTWARE_PRESETS[0])
        operations = self.page._build_current_register_script_operations()
        snapshot = extract_awp1221_page_script(operations)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.page.sinc_max_voltage_var.set("55")
        self.page._button_stage_vars[0]["threshold"].set("1")
        self.page._apply_page_script_snapshot(snapshot, operations[0].device_addr)
        self.assertEqual(self.page.sinc_max_voltage_var.get(), snapshot.max_voltage)
        self.assertEqual(self.page._button_stage_vars[0]["threshold"].get(), snapshot.stages[0].threshold)
        self.assertEqual(self.page.rom_waveform_var.get(), "SINC")
        self.assertEqual(self.page._sinc_verified_key[1], snapshot.serial)

    def test_import_execution_sets_command_gate_then_writes_script(self) -> None:
        operations = self.page._build_current_register_script_operations()
        serial = _FakeSerial()
        original = self.page.ctx.serial
        self.page.ctx.serial = serial
        try:
            error = self.page._execute_page_script_operations(operations)
        finally:
            self.page.ctx.serial = original
        self.assertIsNone(error)
        self.assertIn((self.page.device_addr(), 0x1F, 0x0708), serial.writes)
        script_writes = [
            (operation.device_addr, operation.reg_addr, operation.value)
            for operation in operations
            if operation.action == "WRITE"
        ]
        self.assertEqual(serial.writes[1:], script_writes)

    def test_ram_waveform_is_rejected_before_a_file_is_written(self) -> None:
        self.page.rom_waveform_var.set("SINE")
        self.page.rom_source_var.set("RAM")
        with self.assertRaises(ValueError):
            self.page._build_current_register_script_operations()
