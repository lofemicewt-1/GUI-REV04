from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
import unittest

import pages.awp1221_page as page_mod
import pages.awp1221_realtime_sram as realtime_mod
from pages.awp1221_page import (
    AWP1221ModePage,
    AWP1221_ADC_LIVE_REGISTER,
    AWP1221_ADC_SAMPLE_UPDATE_OPTIONS,
    decode_awp1221_adc_settings_registers,
    encode_awp1221_adc_settings_registers,
)
from pages.awp1221_realtime_sram import (
    AWP1221RealtimeMixin,
    AWP1221_VIBRATION_GPIO_FUNCTION,
    awp1221_adc_load_label,
    awp1221_merge_adc_load,
    awp1221_merge_sample_time,
)


class Awp1221SampleTimeOptionTests(unittest.TestCase):
    def test_options_are_4_8_16_32(self) -> None:
        self.assertEqual(
            AWP1221_ADC_SAMPLE_UPDATE_OPTIONS,
            ("4 us", "8 us", "16 us", "32 us"),
        )

    def test_encode_decode_round_trip(self) -> None:
        for label in AWP1221_ADC_SAMPLE_UPDATE_OPTIONS:
            encoded_05, _encoded_06 = encode_awp1221_adc_settings_registers(
                0x442E,
                0x0000,
                sample_update=label,
                zero_time="1 ms",
                press_return_bit_time="off",
                release_return_bit_time="off",
                zps_enabled=False,
                zps_detect=False,
                sleep_sensitivity=False,
            )
            decoded = decode_awp1221_adc_settings_registers(encoded_05, 0x0000)
            self.assertEqual(decoded["sample_update"], label)


class Awp1221AdcLoadBitTests(unittest.TestCase):
    def test_label_uses_bit11(self) -> None:
        self.assertEqual(awp1221_adc_load_label(0x0000), "40 uA")
        self.assertEqual(awp1221_adc_load_label(1 << 11), "160 uA")
        self.assertEqual(awp1221_adc_load_label(1 << 2), "40 uA")

    def test_merge_sets_only_bit11_and_preserves_bit3(self) -> None:
        current = 0x0008
        self.assertEqual(awp1221_merge_adc_load(current, "160 uA"), 0x0808)
        self.assertEqual(awp1221_merge_adc_load(0x0808, "40 uA"), 0x0008)

    def test_merge_sample_time_only_bits_11_10(self) -> None:
        current = 0x442E
        updated = awp1221_merge_sample_time(current, "16 us")
        self.assertEqual((updated >> 10) & 3, 2)
        self.assertEqual(updated & ~0x0C00, current & ~0x0C00)


class Awp1221QuietRealtimeReadTests(unittest.TestCase):
    def test_live_poll_reads_adc_inside_suppressed_background_task(self) -> None:
        events: list[str] = []

        @contextmanager
        def suppress_logging():
            events.append("suppress")
            yield

        page = object.__new__(AWP1221ModePage)
        page._adc_realtime_active = True
        page._adc_read_in_flight = False
        page._adc_lifecycle_generation = 0
        page.device_addr = lambda: 0x50
        page.ctx = SimpleNamespace(
            serial=SimpleNamespace(
                is_connected=True,
                suppress_logging=suppress_logging,
            )
        )
        page._read_register_value = lambda register, address: events.append(
            f"read:{register}:{address:02X}"
        ) or 1
        page._note_vibration_gpio_level = lambda level: None
        page._finish_adc_value_read = lambda **kwargs: None
        page._schedule_next_adc_poll = lambda: None
        page._adc_voltage_window = None
        tasks: list = []

        def run_task(widget, task, *, on_done, on_error):
            del widget, on_done, on_error
            tasks.append(task)

        original = realtime_mod._run_ui_background_task
        realtime_mod._run_ui_background_task = run_task
        try:
            AWP1221RealtimeMixin._poll_adc_realtime(page)
            self.assertEqual(tasks[0](), (None, 1))
        finally:
            realtime_mod._run_ui_background_task = original

        self.assertEqual(events, ["suppress", f"read:{AWP1221_ADC_LIVE_REGISTER}:50"])

    def test_quiet_read_wraps_suppress_logging(self) -> None:
        events: list[str] = []

        @contextmanager
        def suppress_logging():
            events.append("suppress")
            yield

        page = object.__new__(AWP1221ModePage)
        page._adc_read_in_flight = False
        page._adc_lifecycle_generation = 0
        page.device_addr = lambda: 0x50
        page.ctx = SimpleNamespace(serial=SimpleNamespace(suppress_logging=suppress_logging))
        page._read_register_value = lambda register, address: events.append(f"read:{register}") or 1
        tasks: list = []

        def run_task(widget, task, *, on_done, on_error):
            del widget, on_done, on_error
            tasks.append(task)

        original = page_mod._run_ui_background_task
        page_mod._run_ui_background_task = run_task
        try:
            self.assertTrue(
                AWP1221ModePage._request_adc_value_read(
                    page,
                    show_error=False,
                    register=AWP1221_ADC_LIVE_REGISTER,
                    quiet=True,
                    on_complete=None,
                )
            )
            tasks[0]()
        finally:
            page_mod._run_ui_background_task = original
        self.assertEqual(events, ["suppress", f"read:{AWP1221_ADC_LIVE_REGISTER}"])


class Awp1221WindowSettingsWriteTests(unittest.TestCase):
    def test_write_settings_rmw_sample_time_and_load_bit11(self) -> None:
        writes: list[tuple[int, int]] = []
        page = object.__new__(AWP1221RealtimeMixin)
        page._read_register_value = lambda register, address: {
            0x05: 0x442E,
            0x1F: 0x0008,
        }[register]
        page._write_register_value = lambda register, value, address: writes.append((register, value))
        page._adc_voltage_window = SimpleNamespace(
            sample_time=SimpleNamespace(get=lambda: "16 us"),
            load=SimpleNamespace(get=lambda: "160 uA"),
        )
        page.adc_sample_update_var = SimpleNamespace(set=lambda value: setattr(page, "synced", value))
        page._write_adc_voltage_settings(0x50)
        self.assertEqual(page.synced, "16 us")
        self.assertEqual(
            writes,
            [
                (0x05, awp1221_merge_sample_time(0x442E, "16 us")),
                (0x1F, awp1221_merge_adc_load(0x0008, "160 uA")),
            ],
        )
        self.assertEqual(writes[1][1] & 0x0008, 0x0008)

    def test_load_settings_uses_bit11(self) -> None:
        self.assertEqual(awp1221_adc_load_label(0x0008), "40 uA")
        self.assertEqual(awp1221_adc_load_label(0x0808), "160 uA")
        self.assertEqual(awp1221_adc_load_label(0x070C), "40 uA")


class Awp1221RealtimeGpioPreparationTests(unittest.TestCase):
    def test_gpio0_uses_playback_function_without_register_04_or_03_access(self) -> None:
        reads: list[int] = []
        writes: list[tuple[int, int]] = []
        gpio_modes: list[tuple[str, str]] = []
        page = object.__new__(AWP1221RealtimeMixin)
        page._read_register_value = lambda register, address: reads.append(register) or 0
        page._write_register_value = lambda register, value, address: writes.append((register, value))
        page.ctx = SimpleNamespace(
            serial=SimpleNamespace(
                configure_gpio=lambda pin, mode: gpio_modes.append((pin, mode))
                or SimpleNamespace(ok=True)
            )
        )

        page._prepare_vibration_gpio(0x50)

        self.assertEqual(AWP1221_VIBRATION_GPIO_FUNCTION, 0x01)
        self.assertEqual(reads, [0x07])
        self.assertEqual(writes, [(0x07, 0x0002)])
        self.assertEqual(gpio_modes, [("D2", "DETECT")])
        self.assertNotIn(0x03, reads)
        self.assertNotIn(0x04, reads)
        self.assertNotIn(0x04, [register for register, _value in writes])


if __name__ == "__main__":
    unittest.main()
