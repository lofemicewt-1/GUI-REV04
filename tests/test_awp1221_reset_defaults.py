import unittest

from pages.awp1221_page import (
    AWP1221_INTERFACE_CONFIG_0_DEFAULT,
    AWP1221_RETURN_BIT_RESET,
    AWP1221_DEBOUNCE_RESET,
    AWP1221_STAGE_RESET,
    decode_awp1221_adc_settings_registers,
    decode_awp1221_debounce_time,
    decode_awp1221_adc_slope,
)


class Awp1221DatasheetDefaultsTests(unittest.TestCase):
    """Register defaults from the AWP1221 register guide Rev.0.1."""

    def test_settings_defaults(self) -> None:
        self.assertEqual(AWP1221_INTERFACE_CONFIG_0_DEFAULT, 0x442E)
        self.assertEqual(AWP1221_RETURN_BIT_RESET, 0x0204)
        s = decode_awp1221_adc_settings_registers(AWP1221_INTERFACE_CONFIG_0_DEFAULT, AWP1221_RETURN_BIT_RESET)
        self.assertEqual(s["sample_update"], "8 us")
        self.assertEqual(s["zero_time"], "4 ms")
        self.assertEqual(s["press_return_bit_time"], "off")
        self.assertEqual(s["release_return_bit_time"], "6400 us")
        self.assertTrue(s["zps_enabled"] and s["zps_detect"])
        self.assertFalse(s["sleep_sensitivity"])
        self.assertEqual(decode_awp1221_debounce_time(AWP1221_DEBOUNCE_RESET), 0)

    def test_stage_defaults(self) -> None:
        self.assertEqual(AWP1221_STAGE_RESET, (0x0003, 0x07FF, 0x1800, 0xC03F, 0x0000))
        slope = decode_awp1221_adc_slope(AWP1221_STAGE_RESET[3])
        self.assertEqual((slope["release_below"], slope["release_slope"], slope["press_below"], slope["press_slope"]),
                         (True, 0x40, False, 0x3F))


if __name__ == "__main__":
    unittest.main()
