from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.application import HomePage
from core import base_tool


class I2cSpeedTests(unittest.TestCase):
    def test_gui_and_protocol_offer_all_test_steps(self):
        self.assertEqual(HomePage.I2C_SPEEDS, (
            100000, 400000, 500000, 615385, 666667, 727273, 800000,
        ))
        for speed in HomePage.I2C_SPEEDS:
            with self.subTest(speed=speed):
                self.assertEqual(base_tool.build_set_i2c_speed_command(speed), f"I2CSPD {speed}")

    def test_invalid_speeds_are_rejected_before_transmission(self):
        for speed in (0, -1, 600000, 615384, 666666, 888888, 888889, 1000000, 1000001):
            with self.subTest(speed=speed):
                with self.assertRaises(ValueError):
                    base_tool.set_i2c_speed(lambda *_a, **_kw: self.fail("sent invalid speed"), speed)

    def test_only_matching_acknowledgement_succeeds(self):
        for speed in HomePage.I2C_SPEEDS:
            for framed in (False, True):
                with self.subTest(speed=speed, framed=framed):
                    text = f"I2CSPD {speed}"
                    if framed:
                        text = f"^{text}~"
                    calls = []

                    def transport(command, **kwargs):
                        calls.append((command, kwargs))
                        return base_tool.SerialResponse(True, data=text)

                    self.assertTrue(base_tool.set_i2c_speed(transport, speed).ok)
                    self.assertEqual(calls, [(f"I2CSPD {speed}", {"expect_response": True})])

    def test_old_firmware_error_or_wrong_speed_is_a_failure(self):
        for text in ("^ERR I2CSPD DENY~", "^I2CSPD 400000~", "^READY REV04~", ""):
            with self.subTest(text=text):
                response = base_tool.set_i2c_speed(
                    lambda *_a, **_kw: base_tool.SerialResponse(True, data=text), 800000
                )
                self.assertFalse(response.ok)
                self.assertTrue(response.error)

    def test_transport_failure_is_preserved(self):
        failed = base_tool.SerialResponse(False, error="timeout")
        self.assertIs(base_tool.set_i2c_speed(lambda *_a, **_kw: failed, 800000), failed)

    def test_home_records_speed_only_after_success(self):
        for success in (False, True):
            with self.subTest(success=success):
                state = SimpleNamespace(extra={"i2c_speed": 400000}, selected_port="COM_TEST")
                page = SimpleNamespace(
                    ctx=SimpleNamespace(
                        state=state,
                        serial=SimpleNamespace(
                            is_connected=True,
                            set_i2c_speed=lambda _speed: base_tool.SerialResponse(
                                success, data="I2CSPD 800000", error="DENY" if not success else ""
                            ),
                        ),
                    ),
                    i2c_speed_var=SimpleNamespace(get=lambda: "800000"),
                    tr=lambda text: text,
                    refresh_language=lambda: None,
                )
                with patch("app.application.messagebox.showerror") as error:
                    HomePage.apply_i2c_speed(page)
                self.assertEqual(state.extra["i2c_speed"], 800000 if success else 400000)
                self.assertEqual(error.call_count, 0 if success else 1)


if __name__ == "__main__":
    unittest.main()
