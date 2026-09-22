from __future__ import annotations

from types import SimpleNamespace
import unittest

from core.awp1221_command_gate import (
    AWP1221_CMD_GATE_REGISTER,
    awp1221_cmd_gate_write_value,
    ensure_awp1221_cmd_gate,
)
from pages.awp1221_page import AWP1221ModePage, AWP1221Page
from pages.awp1221_realtime_sram import AWP1221RealtimeMixin


class Awp1221CmdGateTests(unittest.TestCase):
    def test_bit3_clear_returns_value_with_bit3_set(self) -> None:
        self.assertEqual(awp1221_cmd_gate_write_value(0x0700), 0x0708)

    def test_bit3_set_returns_none(self) -> None:
        self.assertIsNone(awp1221_cmd_gate_write_value(0x0708))

    def test_other_bits_are_preserved(self) -> None:
        self.assertEqual(awp1221_cmd_gate_write_value(0x1230), 0x1238)

    def test_ensure_writes_only_when_bit3_clear(self) -> None:
        writes: list[tuple[int, int]] = []
        result = ensure_awp1221_cmd_gate(
            lambda register: 0x0700 if register == AWP1221_CMD_GATE_REGISTER else 0,
            lambda register, value: writes.append((register, value)),
        )
        self.assertEqual(result, 0x0708)
        self.assertEqual(writes, [(AWP1221_CMD_GATE_REGISTER, 0x0708)])

    def test_ensure_does_not_write_when_bit3_set(self) -> None:
        writes: list[tuple[int, int]] = []
        result = ensure_awp1221_cmd_gate(
            lambda register: 0x0708,
            lambda register, value: writes.append((register, value)),
        )
        self.assertEqual(result, 0x0708)
        self.assertEqual(writes, [])

    def test_ensure_propagates_read_error(self) -> None:
        with self.assertRaises(ValueError):
            ensure_awp1221_cmd_gate(
                lambda register: (_ for _ in ()).throw(ValueError("read failed")),
                lambda register, value: None,
            )


class _Serial:
    def snapshot_transaction(self):
        from contextlib import nullcontext
        return nullcontext()


class Awp1221CmdGateWiringTests(unittest.TestCase):
    def _mode_page(self) -> AWP1221ModePage:
        page = object.__new__(AWP1221ModePage)
        page.ctx = SimpleNamespace(serial=_Serial())
        page.mode_key = "button_mode"
        page._ops: list[str] = []
        page._ensure_reg_1f_bit3 = lambda device_addr=None: page._ops.append("ensure")
        page._write_button_adc_stage = lambda *args, **kwargs: page._ops.append("stage")
        page._write_adc_stage = lambda *args, **kwargs: page._ops.append("stage")
        page._write_adc_settings = lambda *args, **kwargs: page._ops.append("settings")
        page._write_control_command = lambda *args, **kwargs: page._ops.append("command")
        page._write_waveform_selection_batch = lambda *args, **kwargs: page._ops.append("wave")
        page._write_sinc_plan_batch = lambda *args, **kwargs: page._ops.append("sinc")
        page._ensure_connected = lambda: True
        return page

    def test_start_detection_gates_before_payload(self) -> None:
        page = self._mode_page()
        AWP1221ModePage._write_start_detection(page, 0x50, (None, None), {})
        self.assertEqual(page._ops[0], "ensure")
        self.assertIn("stage", page._ops)
        self.assertEqual(page._ops[-1], "command")

    def test_send_control_command_gates_before_0x02(self) -> None:
        page = self._mode_page()
        AWP1221ModePage._send_control_command(page, "stop_play")
        self.assertEqual(page._ops, ["ensure", "command"])

    def test_waveform_write_gates_before_batch(self) -> None:
        page = self._mode_page()
        AWP1221ModePage._write_waveform_selection(page, 0x50, True, 0, (0, 0, 0), 0, 0)
        self.assertEqual(page._ops, ["ensure", "wave"])

    def test_sinc_write_gates_before_batch(self) -> None:
        page = self._mode_page()
        AWP1221ModePage._write_sinc_plan(page, 0x50, object())
        self.assertEqual(page._ops, ["ensure", "sinc"])

    def test_write_chip_state_gates_before_0x01(self) -> None:
        writes: list[tuple[int, int]] = []

        class Serial:
            def read_register_value(self, address, register, bits=16):
                return SimpleNamespace(ok=True, error=None, data="ok")

            def extract_rdataw_value(self, data, device_addr, reg_addr, bits=16):
                return 0x0700

            def write_register_value(self, address, register, value, bits=16):
                writes.append((register, value))
                return SimpleNamespace(ok=True, error=None)

        page = object.__new__(AWP1221Page)
        page.ctx = SimpleNamespace(serial=Serial())
        page._ensure_connected = lambda: True
        page._device_addr = lambda: 0x50
        page._shared_state_owner = None
        page.tr = lambda key: key
        AWP1221Page.write_chip_state(page, 0x5283)
        self.assertEqual(writes[0], (0x1F, 0x0708))
        self.assertEqual(writes[-1], (0x01, 0x5283))

    def test_adc_realtime_start_gates_then_reads_settings(self) -> None:
        page = object.__new__(AWP1221RealtimeMixin)
        page._ops = []
        page.device_addr = lambda: 0x50
        page._adc_voltage_window = object()
        page._adc_realtime_active = True
        page._ensure_reg_1f_bit3 = lambda address: page._ops.append(("ensure", address)) or 0x0708
        page._read_register_value = lambda register, address: page._ops.append(("read", register)) or 0
        captured = {}

        def capture(widget, task, on_done, on_error):
            captured["task"] = task

        import pages.awp1221_realtime_sram as realtime

        original = realtime._run_ui_background_task
        realtime._run_ui_background_task = capture
        try:
            AWP1221RealtimeMixin._load_adc_voltage_settings(page)
            captured["task"]()
        finally:
            realtime._run_ui_background_task = original
        self.assertEqual(page._ops[0], ("ensure", 0x50))
        self.assertEqual(page._ops[1][0], "read")


if __name__ == "__main__":
    unittest.main()

