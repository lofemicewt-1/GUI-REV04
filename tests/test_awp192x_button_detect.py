from __future__ import annotations

import unittest
from types import SimpleNamespace

from pages.awp192x_modes import ButtonModePage


class _Var:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class Awp192xButtonDetectTests(unittest.TestCase):
    def _page(self, **overrides):
        page = object.__new__(ButtonModePage)
        page.press_repeat_count_var = _Var("1")
        page.release_repeat_count_var = _Var("1")
        page.press_waveform_var = _Var("slice0")
        page.release_waveform_var = _Var("slice1")
        page.duration_var = _Var("9")
        page._adc_trigger_read_active = False
        page._sense_status_active = True
        page._sense_timeout_mode = False
        page._direct_detection_active = True
        page._detect_generation = 1
        page._detect_gui_state = "ready"
        page._detect_phase = "press"
        page._detect_reconfigure_pending = False
        page._detect_await_adc_busy = False
        page._detect_poll_job = None
        page._compose_button_adc_event_config = lambda: 0x0221
        page.ctx = SimpleNamespace(state=SimpleNamespace(awp192x_device_addr=0x44))
        for name, value in overrides.items():
            setattr(page, name, value)
        return page

    def test_wait_release_event_clears_press_enable_and_uses_0x0209(self):
        page = self._page()
        event, press, release = ButtonModePage._detect_phase_register_values(
            page, wait_release=True
        )
        self.assertEqual(event, 0x0209)
        self.assertEqual(press, 0x0000)
        self.assertEqual(release, 0x8001)

    def test_wait_press_event_enables_press_and_keeps_0x0221(self):
        page = self._page()
        event, press, release = ButtonModePage._detect_phase_register_values(
            page, wait_release=False
        )
        self.assertEqual(event, 0x0221)
        self.assertEqual(press, 0x8000)
        self.assertEqual(release, 0x0001)

    def test_direct_detect_cycles_press_to_release_then_back_to_press(self):
        writes = []
        colors = []
        page = self._page()
        page._blink_sense_status = lambda color: colors.append(color)
        page.write_reg = lambda reg, value, show_error=True: writes.append((reg, value)) or True
        ButtonModePage._reset_direct_detect_cycle(page)

        ButtonModePage._handle_direct_detect_status(page, 0x0800)
        self.assertEqual(page._detect_phase, "release")
        self.assertEqual(colors, [ButtonModePage.STATUS_PRESS_COLOR])
        self.assertEqual(
            writes,
            [
                (0x0B, 0x0209),
                (0x0F, 0x0000),
                (0x11, 0x8001),
                (0x03, 0x4000),
                (0x06, 0x0001),
            ],
        )

        writes.clear()
        ButtonModePage._handle_direct_detect_status(page, 0x0200)
        self.assertEqual(page._detect_phase, "press")
        self.assertEqual(colors[-1], ButtonModePage.STATUS_RELEASE_COLOR)
        self.assertEqual(
            writes,
            [
                (0x0B, 0x0221),
                (0x0F, 0x8000),
                (0x11, 0x0001),
                (0x03, 0x4000),
                (0x06, 0x0001),
            ],
        )

    def test_direct_detect_reads_status_only_on_nint(self):
        reads = []
        handled = []
        writes = []
        page = self._page()
        page.read_reg = lambda reg, show_error=False: reads.append(reg) or 0x0800
        page.write_reg = lambda reg, value, show_error=True: writes.append((reg, value)) or True
        page._handle_direct_detect_status = lambda status: handled.append(status) or True

        ButtonModePage._process_sense_status_log(page, "MCU_TEXT nINT")

        self.assertEqual(reads, [0x02])
        self.assertEqual(handled, [0x0800])
        self.assertEqual(writes, [])

    def test_gpioev_d2_triggers_direct_detect_status_read(self):
        reads = []
        handled = []
        page = self._page()
        page.read_reg = lambda reg, show_error=False: reads.append(reg) or 0x0800
        page.write_reg = lambda *_args, **_kwargs: True
        page._handle_direct_detect_status = lambda status: handled.append(status) or True

        ButtonModePage._process_sense_status_log(
            page,
            "MCU ^GPIOEV GPIO0 D2 SEQ=00000001 LEVEL=0 SRC=INT0 CHIP=BOARD~",
        )

        self.assertEqual(reads, [0x02])
        self.assertEqual(handled, [0x0800])

    def test_gpioev_other_pin_does_not_drive_192x_phase(self):
        reads = []
        page = self._page()
        page.read_reg = lambda reg, show_error=False: reads.append(reg) or 0x0800
        page._handle_direct_detect_status = lambda status: reads.append(status)

        ButtonModePage._process_sense_status_log(
            page,
            "MCU ^GPIOEV GPIO1 D3 SEQ=00000002 LEVEL=0 SRC=PCINT2 CHIP=BOARD~",
        )

        self.assertEqual(reads, [])
        self.assertEqual(page._detect_phase, "press")

    def test_offline_press_release_logs_still_blink_without_direct_detect(self):
        colors = []
        page = self._page(_direct_detection_active=False)
        page._blink_sense_status = lambda color: colors.append(color)

        ButtonModePage._process_sense_status_log(page, "MCU_TEXT P")
        ButtonModePage._process_sense_status_log(page, "MCU_TEXT R")

        self.assertEqual(
            colors,
            [ButtonModePage.STATUS_PRESS_COLOR, ButtonModePage.STATUS_RELEASE_COLOR],
        )

    def test_queued_sense_log_from_old_generation_is_ignored(self):
        reads = []
        writes = []
        page = self._page(_detect_generation=2)
        page.read_reg = lambda reg, show_error=False: reads.append(reg) or 0x0800
        page.write_reg = lambda *args, **kwargs: writes.append((args, kwargs)) or True
        page._handle_direct_detect_status = lambda status: writes.append(("phase", status))

        ButtonModePage._process_sense_status_log(
            page, "MCU_TEXT nINT", generation=1, address=0x44
        )

        self.assertEqual(reads, [])
        self.assertEqual(writes, [])
        self.assertEqual(page._detect_phase, "press")

    def test_valid_event_rreg_allows_phase_change(self):
        page = self._page()
        page._blink_sense_status = lambda *_args: None
        page._schedule_detect_rearm = lambda: None
        page.read_reg = lambda *_args, **_kwargs: 0x0800
        page.write_reg = lambda *_args, **_kwargs: True

        ButtonModePage._process_sense_status_log(
            page, "MCU_TEXT nINT", generation=1, address=0x44
        )

        self.assertEqual(page._detect_phase, "release")

    def test_nint_without_phase_change_still_restarts_adc(self):
        writes = []
        page = self._page()
        page.read_reg = lambda *_args, **_kwargs: 0x0000
        page.write_reg = lambda reg, value, show_error=True: writes.append((reg, value)) or True

        ButtonModePage._on_direct_nint_event(page)

        self.assertEqual(writes, [(0x06, 0x0001)])
        self.assertEqual(page._detect_phase, "press")


if __name__ == "__main__":
    unittest.main()
