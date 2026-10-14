from __future__ import annotations

import unittest

from core.base_tool import SerialResponse, format_firmware_error, is_i2c_address_nack, parse_firmware_error
from pages.awp1922_page import AWP1922Page, awp1922_ctrl_write_value


class _R:
    def __init__(self, ok, data="", error=""):
        self.ok, self.data, self.error = ok, data, error


def _page(chip):
    page = object.__new__(AWP1922Page)
    calls = []

    class Serial:
        is_connected = True

        def emit_log(self, _m):
            pass

        def write_register_value(self, _a, reg, value, bits=8):
            calls.append(("W", reg, value))
            chip[reg] = value
            return _R(True, f"OK WREGW 58 {reg:02X} 8 1")

        def read_register_value(self, _a, reg, bits=8):
            calls.append(("R", reg))
            return _R(True, f"RDATAW 58 {reg:02X} 8 1 {chip.get(reg, 0):02X}")

        def extract_rdataw_value(self, data, **_k):
            return int(data.split()[-1], 16)

    page.ctx = type("C", (), {"serial": Serial()})()
    page._ensure_connected = lambda: True
    page._log_local = lambda *_a: None
    page._addr = lambda: 0x58
    page.current_ctrl_reg = 0x80
    return page, calls


class Awp1922ReenableTests(unittest.TestCase):
    def test_value_helper(self):
        self.assertEqual(awp1922_ctrl_write_value(0x40), (0xC0, True))
        self.assertEqual(awp1922_ctrl_write_value(0xC0), (0xC0, False))
        self.assertEqual(awp1922_ctrl_write_value(0x00, allow_disable=True), (0x00, False))

    def test_reenable_in_play_rewrites_freq_and_amp(self):
        # 0x05 readback carries RO I2C_ADDR bits6-4; only bits3-0 are rewritten.
        page, calls = _page({0x05: 0x53, 0x06: 0xFF})
        self.assertTrue(page._write8(0x00, 0x40))
        self.assertEqual(calls, [("W", 0x00, 0xC0), ("R", 0x05), ("W", 0x05, 0x03), ("R", 0x06), ("W", 0x06, 0x1F)])

    def test_reenable_without_play_no_rewrite(self):
        page, calls = _page({})
        page._write8(0x00, 0x00)
        self.assertEqual(calls, [("W", 0x00, 0x80)])

    def test_already_enabled_no_rewrite(self):
        page, calls = _page({})
        page._write8(0x00, 0xC0)
        self.assertEqual(calls, [("W", 0x00, 0xC0)])

    def test_shutdown_keeps_disable(self):
        page, calls = _page({})
        page.shutdown()
        self.assertEqual(calls, [("W", 0x00, 0x00)])


class Awp1922ShutdownStateTests(unittest.TestCase):
    def test_no_force_after_user_shutdown_until_wake(self):
        page, calls = _page({0x05: 0x01, 0x06: 0x02})
        page.shutdown()
        page._write8(0x00, 0x40)          # manual/play write while shut down
        self.assertEqual(calls[-1], ("W", 0x00, 0x40))
        page.wake()
        self.assertEqual(calls[-1][:2], ("W", 0x00))
        self.assertTrue(calls[-1][2] & 0x80)
        calls.clear()
        page._write8(0x00, 0x40)          # forced again after Wake
        self.assertEqual(calls[0], ("W", 0x00, 0xC0))

    def test_shutdown_is_per_address(self):
        page, calls = _page({})
        page.shutdown()
        page._addr = lambda: 0x59
        page._write8(0x00, 0x00)
        self.assertEqual(calls[-1], ("W", 0x00, 0x80))


class FirmwareErrorTests(unittest.TestCase):
    def test_parse_and_format(self):
        e = parse_firmware_error("^ERR WREGW I2C 3~")
        self.assertEqual((e.command, e.reason, e.code), ("WREGW", "I2C", 3))
        self.assertIn("data NACK", format_firmware_error("ERR WREGW I2C 3"))
        self.assertIsNone(parse_firmware_error("OK WREGW 58 00 8 1"))

    def test_retry_rules(self):
        nack = lambda t: is_i2c_address_nack(SerialResponse(False, data=t, error=format_firmware_error(t)))
        self.assertTrue(nack("ERR WREGW I2C"))
        self.assertTrue(nack("ERR WREGW I2C 2"))
        self.assertFalse(nack("ERR WREGW I2C 3"))
        self.assertFalse(nack("ERR WREGW STOP"))
        self.assertTrue(nack("ERR RREGW I2C 2"))
        self.assertFalse(nack("ERR RREGW I2C 5"))

    def test_error_has_request_context(self):
        from core.base_tool import read_register_value, write_register_value
        seq = [SerialResponse(True, data="ERR WREGW I2C"), SerialResponse(True, data="ERR WREGW I2C")]
        err = write_register_value(lambda b, **k: seq.pop(0), 0x58, 0x06, 1, bits=8).response.error
        for part in ("WREGW", "dev=0x58", "reg=0x06", "width=8", "count=1", "attempts=2", "^ERR WREGW I2C~"):
            self.assertIn(part, err)
        err = read_register_value(lambda b, **k: SerialResponse(True, data="ERR RREGW I2C 5"), 0x44, 0x1E, bits=16).response.error
        self.assertIn("code=5 (I2C timeout)", err)
        self.assertIn("attempts=1", err)
        busy = read_register_value(lambda b, **k: SerialResponse(False, error="Serial command is busy"), 0x44, 0, bits=16)
        self.assertEqual(busy.response.error, "Serial command is busy")


if __name__ == "__main__":
    unittest.main()


class Awp1221AddressRangeTests(unittest.TestCase):
    def test_all_0x50_0x57_supported(self):
        from app.application import HomePage  # noqa: F401  (import check only)
        from core.arduino_preset_sync import signature_for_address
        from core.chip_discovery import AWP1221_PROBE
        from core.chip_status_cache import CHIP_TYPE_1221
        import core.chip_status_cache as csc
        from pages.awp1221_page import AWP1221_DEVICE_ADDRS
        for addr in range(0x50, 0x58):
            self.assertEqual(signature_for_address(addr), 0x1221)
            self.assertIn(addr, AWP1221_PROBE.addresses)
            self.assertIn(addr, AWP1221_DEVICE_ADDRS)
        self.assertEqual(set(range(0x50, 0x58)), set(next(v for k, v in vars(csc).items()
                         if isinstance(v, dict) and CHIP_TYPE_1221 in v)[CHIP_TYPE_1221]))
