from __future__ import annotations

import unittest

from core.base_tool import SerialResponse, extract_rdataw_value, read_register_value, write_register_value
from pages.awp1922_page import AWP1922Page, awp1922_play_value


class _ScriptedTransport:
    def __init__(self, responses: list[SerialResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def __call__(self, body: str, *, expect_response: bool = True, display_body: str | None = None) -> SerialResponse:
        self.calls.append(body)
        if not self.responses:
            raise AssertionError(body)
        return self.responses.pop(0)


class Awp1922I2cRetryTests(unittest.TestCase):
    def test_8bit_read_retries_once_after_address_nack(self) -> None:
        transport = _ScriptedTransport(
            [
                SerialResponse(True, data="ERR RREGW I2C 2"),
                SerialResponse(True, data="RDATAW 58 00 8 1 80"),
            ]
        )
        response = read_register_value(transport, 0x58, 0x00, bits=8).response
        self.assertTrue(response.ok, response.error)
        self.assertEqual(transport.calls, ["RREGW 58 00 8 1", "RREGW 58 00 8 1"])
        self.assertEqual(
            extract_rdataw_value(response.data, device_addr=0x58, reg_addr=0x00, bits=8),
            0x80,
        )

    def test_8bit_write_retries_once_after_bare_wregw_i2c_error(self) -> None:
        transport = _ScriptedTransport(
            [
                SerialResponse(True, data="ERR WREGW I2C"),
                SerialResponse(True, data="OK WREGW 58 06 8 1"),
            ]
        )
        response = write_register_value(transport, 0x58, 0x06, 0x01, bits=8).response
        self.assertTrue(response.ok, response.error)
        self.assertEqual(transport.calls, ["WREGW 58 06 8 01", "WREGW 58 06 8 01"])

    def test_8bit_write_does_not_retry_data_nack(self) -> None:
        transport = _ScriptedTransport([SerialResponse(True, data="ERR WREGW I2C 3")])
        response = write_register_value(transport, 0x58, 0x06, 0x01, bits=8).response
        self.assertFalse(response.ok)
        self.assertEqual(len(transport.calls), 1)

    def test_16bit_address_nack_is_not_retried(self) -> None:
        transport = _ScriptedTransport([SerialResponse(True, data="ERR RREGW I2C 2")])
        response = read_register_value(transport, 0x44, 0x1E, bits=16).response
        self.assertFalse(response.ok)
        self.assertEqual(len(transport.calls), 1)

    def test_start_does_not_read_or_rewrite_waveform(self) -> None:
        page = object.__new__(AWP1922Page)
        page.current_ctrl_reg = 0x80
        writes: list[tuple[int, int]] = []
        page._write8_async = lambda reg, value, on_success=None: writes.append((reg, value))
        page.start_play()
        self.assertEqual(writes, [(0x00, awp1922_play_value(0x80))])

    def test_i2c_write_failure_clears_status_once_then_retries(self) -> None:
        page = object.__new__(AWP1922Page)
        calls: list[tuple[str, int]] = []

        class Serial:
            is_connected = True

            def write_register_value(self, _addr, reg, value, bits=8):
                calls.append(("W", reg))
                if calls.count(("W", reg)) == 1:
                    return type("R", (), {"ok": False, "data": "ERR WREGW I2C", "error": "ERR WREGW I2C"})()
                return type("R", (), {"ok": True, "data": "OK WREGW 58 05 8 1", "error": ""})()

            def read_register_value(self, _addr, reg, bits=8):
                calls.append(("R", reg))
                return type("R", (), {"ok": True, "data": "RDATAW 58 01 8 1 00", "error": ""})()

        page.ctx = type("C", (), {"serial": Serial()})()
        page._ensure_connected = lambda: True
        page._log_local = lambda *_args: None
        page._addr = lambda: 0x58
        self.assertTrue(page._write8(0x05, 0x0F, show_error=True))
        self.assertEqual(calls, [("W", 0x05), ("R", 0x01), ("W", 0x05)])


if __name__ == "__main__":
    unittest.main()
