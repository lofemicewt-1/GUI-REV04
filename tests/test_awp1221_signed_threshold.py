from __future__ import annotations

import unittest

from pages.awp1221_page import (
    AWP1221Page,
    awp1221_en_high_load_value,
    awp1221_format_threshold_signed,
    awp1221_parse_threshold_text,
    awp1221_threshold_hex_hint,
)
from types import SimpleNamespace


class Awp1221ParseThresholdTextTests(unittest.TestCase):
    def test_positive_max_is_0x7ff(self) -> None:
        self.assertEqual(awp1221_parse_threshold_text("2047"), 0x7FF)

    def test_minus_one_is_0xfff(self) -> None:
        self.assertEqual(awp1221_parse_threshold_text("-1"), 0xFFF)

    def test_minus_2047_is_0x801(self) -> None:
        self.assertEqual(awp1221_parse_threshold_text("-2047"), 0x801)

    def test_hex_0x800_is_raw_code(self) -> None:
        self.assertEqual(awp1221_parse_threshold_text("0x800"), 0x800)

    def test_unsigned_4095_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            awp1221_parse_threshold_text("4095")

    def test_decimal_minus_2048_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            awp1221_parse_threshold_text("-2048")

    def test_decimal_with_leading_zero_is_still_decimal(self) -> None:
        self.assertEqual(awp1221_parse_threshold_text("0001"), 1)

    def test_non_hex_integer_prefix_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            awp1221_parse_threshold_text("0b1")


class Awp1221FormatThresholdSignedTests(unittest.TestCase):
    def test_0xfff_formats_as_minus_one(self) -> None:
        self.assertEqual(awp1221_format_threshold_signed(0xFFF), "-1")

    def test_0x800_formats_as_minus_2048(self) -> None:
        self.assertEqual(awp1221_format_threshold_signed(0x800), "-2048")

    def test_0x7ff_formats_as_2047(self) -> None:
        self.assertEqual(awp1221_format_threshold_signed(0x7FF), "2047")


class Awp1221ThresholdHexHintTests(unittest.TestCase):
    def test_minus_one_hint_is_0xfff(self) -> None:
        self.assertEqual(awp1221_threshold_hex_hint("-1"), "0xFFF")

    def test_minus_2047_hint_is_0x801(self) -> None:
        self.assertEqual(awp1221_threshold_hex_hint("-2047"), "0x801")

    def test_invalid_4095_hint_is_empty(self) -> None:
        self.assertEqual(awp1221_threshold_hex_hint("4095"), "")

    def test_hex_hint_is_normalized_to_three_uppercase_digits(self) -> None:
        self.assertEqual(awp1221_threshold_hex_hint("0x0a"), "0x00A")


class Awp1221EnHighAdcLoadTests(unittest.TestCase):
    def test_helper_sets_bit2_and_preserves_bit3(self) -> None:
        self.assertEqual(awp1221_en_high_load_value(0x0008), 0x000C)
        self.assertEqual(awp1221_en_high_load_value(0xA000), 0xA004)

    def test_en_high_path_updates_chip_and_both_mode_pages(self) -> None:
        writes: list[tuple[int, int, int]] = []
        labels: list[str] = []

        def make_page() -> SimpleNamespace:
            return SimpleNamespace(
                _ensure_reg_1f_bit3=lambda address: 0x0008,
                _write_register_value=lambda register, value, address: writes.append(
                    (register, value, address)
                ),
                adc_ldo_var=SimpleNamespace(set=labels.append),
                _sync_realtime_adc_load=lambda load: labels.append(f"window:{load}"),
            )

        page = object.__new__(AWP1221Page)
        page.current_mode = "button_mode"
        page._device_addr = lambda: 0x50
        page.mode_pages = {
            "button_mode": make_page(),
            "full_mode": make_page(),
        }

        page._set_en_high_adc_load()

        self.assertEqual(writes, [(0x1F, 0x000C, 0x50)])
        self.assertEqual(labels.count("160 uA"), 2)
        self.assertEqual(labels.count("window:160 uA"), 2)
