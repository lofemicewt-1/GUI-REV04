"""Offline checks for target-bound 1221 legend events (no chip I/O)."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from app.application import HomePage
from core import chip_status_cache as cache
from pages.awp1221_page import AWP1221ModePage


def snapshot(identity, raw, seq=1, flags=None, error=0, session=7):
    if flags is None:
        flags = cache.FLAG_CONFIGURED | cache.FLAG_VALID | cache.FLAG_MONITOR_ENABLED
    no_sample = bool(flags & cache.FLAG_NO_SAMPLE)
    record = cache.make_record(identity, flags=flags, status=raw,
        sample_seq=0 if no_sample else seq, irq_seq=0,
        last_good_ms=0 if no_sample else seq, age_ms=cache.MAX_U32 if no_sample else 0,
        error=error, source=cache.SOURCE_NONE if no_sample else (
            cache.SOURCE_1221_STATE if identity.chip_type == "1221" else cache.SOURCE_IRQ_02))
    records = tuple(record if slot == identity.slot else cache.make_unconfigured(slot)
                    for slot in range(cache.SLOT_COUNT))
    return cache.Snapshot(irq_seq=seq, records=records, session_id=session)


class PressureStatusTests(unittest.TestCase):
    def setUp(self):
        self.page = page = object.__new__(AWP1221ModePage)
        page.mode_key = "button_mode"
        page.ctx = SimpleNamespace(state=SimpleNamespace(theme="light"),
                                   serial=SimpleNamespace(is_connected=True))
        page.tr = lambda key: key
        page.adc_legend = Mock()
        page._adc_detection_active = False
        page._shared_state_for = lambda *_: None
        page._shared_port_identity = lambda: "COM14"
        page.device_addr = lambda: 0x50
        self.jobs, self.cancelled = [], []
        page.after = lambda delay, cb, *args: self.jobs.append((delay, cb, args)) or len(self.jobs)
        page.after_cancel = self.cancelled.append
        self.identity = cache.make_identity(0, 0x50, chip_type="1221")
        self.state = cache.SnapshotState([self.identity], session_id=7)
        page._set_pressure_status("idle")

    def apply(self, raw, seq=1, **kwargs):
        self.state.apply_snapshot(snapshot(self.identity, raw, seq, **kwargs))
        self.page._apply_pressure_status_cache("COM14", self.state)

    def live_state(self):
        return self.page.adc_legend.set_live.call_args.args[0]

    def test_detection_command_and_stop_cancel_flash(self):
        self.assertEqual(self.live_state(), "idle")
        self.page._set_detection_state(True)
        self.assertEqual(self.live_state(), "detecting")
        self.apply(0x11)
        self.assertEqual(self.live_state(), "press")
        self.page._set_detection_state(False)
        self.assertEqual(self.live_state(), "idle")
        self.assertTrue(self.cancelled)
        self.assertIsNone(self.page._pressure_flash_job)

    def test_press_release_flash_then_restore_sensing(self):
        for seq, raw, expected in ((1, 0x11, "press"), (2, 0x12, "release")):
            self.apply(raw, seq)
            self.assertEqual(self.live_state(), expected)
            self.assertEqual(self.jobs[-1][0], 180)
            self.jobs[-1][1](*self.jobs[-1][2])
            self.assertEqual(self.live_state(), "detecting")

    def test_old_flash_cannot_replace_new_error_and_play_wording_survives(self):
        self.apply(0x11)
        delay, restore, args = self.jobs[-1]
        self.assertEqual(delay, 180)
        self.page._set_pressure_status("error")
        restore(*args)
        self.assertEqual(self.live_state(), "error")
        self.page._set_pressure_status("play")
        self.assertEqual(self.live_state(), "play")
        self.assertEqual(self.page.adc_legend.set_live.call_args.args[1], "awp1221_pressure_play")

    def test_target_change_invalidates_pending_event_restore(self):
        self.apply(0x12)
        delay, restore, args = self.jobs[-1]
        self.assertEqual(delay, 180)
        self.page._stop_adc_realtime = Mock()
        self.page._refresh_adc_read_buttons = Mock()
        self.page._on_adc_target_changed()
        restore(*args)
        self.assertEqual(self.live_state(), "idle")
        self.page._stop_adc_realtime.assert_called_once_with()

    def test_fault_bits_and_read_error_are_red(self):
        for seq, bit in enumerate(range(10, 16), 1):
            self.apply(1 << bit, seq)
            self.assertEqual(self.live_state(), "error")
        self.apply(0, 7, flags=cache.FLAG_CONFIGURED | cache.FLAG_VALID | cache.FLAG_ERROR | cache.FLAG_MONITOR_ENABLED,
                   error=cache.ERROR_I2C_READ)
        self.assertEqual(self.live_state(), "error")

    def test_nonfault_bits_play_and_idle(self):
        for seq, raw, expected in ((1, 0x380, "idle"), (2, 8, "play"),
                                   (3, 0x10, "sense"), (4, 0x13, "sense"), (5, 0, "idle")):
            self.apply(raw, seq)
            self.assertEqual(self.page._pressure_status, expected)

    def test_duplicate_cache_does_not_restart_flash(self):
        self.apply(0x11)
        self.page._apply_pressure_status_cache("COM14", self.state)
        self.assertEqual(len(self.jobs), 1)
        self.jobs[-1][1]()
        self.page._apply_pressure_status_cache("COM14", self.state)
        self.assertEqual(self.page._pressure_status, "sense")

    def test_other_port_address_type_disabled_stale_and_no_sample_ignored(self):
        self.state.apply_snapshot(snapshot(self.identity, 0x11))
        self.page._apply_pressure_status_cache("COM15", self.state)
        self.page.ctx.serial.is_connected = False
        self.page._apply_pressure_status_cache("COM14", self.state)
        self.page.ctx.serial.is_connected = True
        for identity, flags in (
            (cache.make_identity(0, 0x51, chip_type="1221"), 0x43),
            (cache.make_identity(0, 0x44, chip_type="192"), 0x43),
            (self.identity, 0x03), (self.identity, 0x4B), (self.identity, 0x51)):
            state = cache.SnapshotState([identity], session_id=7)
            state.apply_snapshot(snapshot(identity, 0 if flags == 0x51 else 0x11,
                flags=flags))
            self.page._apply_pressure_status_cache("COM14", state)
        self.assertEqual(self.page._pressure_status, "idle")
        self.assertEqual(self.jobs, [])

    def test_language_theme_preserve_current_status(self):
        self.apply(0x12)
        self.page.tr = lambda key: "Release" if key.endswith("release") else key
        self.page.ctx.state.theme = "dark"
        self.page._refresh_pressure_status()
        self.assertEqual(self.live_state(), "release")
        self.assertEqual(self.page.adc_legend.set_live.call_args.args[1], "")
        self.page._set_pressure_status("idle")
        self.assertEqual(self.live_state(), "idle")

    def test_command_failure_is_red(self):
        self.page._set_start_detection_busy = Mock()
        self.page._adc_lifecycle_generation = 2
        with patch("pages.awp1221_page.messagebox.showerror"):
            self.page._finish_verified_command("start_detection", "read failed", started=False,
                                              device_addr=0x50, generation=2)
        self.assertEqual(self.live_state(), "error")

    def test_full_mode_does_not_create_status_or_timer(self):
        self.page.mode_key = "full_mode"
        self.page._set_pressure_status("press")
        self.page._apply_pressure_status_cache("COM14", self.state)
        self.assertEqual(self.page._pressure_status, "idle")
        self.assertEqual(self.jobs, [])

    def test_invalid_address_edit_does_not_break_home_event_delivery(self):
        self.state.apply_snapshot(snapshot(self.identity, 0x11))
        self.page.device_addr = Mock(side_effect=ValueError("incomplete address"))
        self.page._apply_pressure_status_cache("COM14", self.state)
        self.assertEqual(self.page._pressure_status, "idle")

    def test_return_to_button_mode_restores_shared_detection_color(self):
        self.page._shared_state_for = lambda *_: SimpleNamespace(detection_active=True,
            detection_device_addr=0x50, waveform_play_active=False)
        self.page._sync_local_activity()
        self.assertEqual(self.live_state(), "detecting")

    def test_home_forwards_only_accepted_current_session_samples(self):
        home = object.__new__(HomePage)
        home._status_state_by_port = {"COM14": self.state}
        home._status_errors_by_port = {}
        home._refresh_status_rows = Mock()
        home._set_status_error = Mock()
        home.ctx = SimpleNamespace(app=SimpleNamespace(navigation=SimpleNamespace(pages={
            "awp1221": SimpleNamespace(mode_pages={"button_mode": self.page})})))
        self.assertTrue(home._apply_status_snapshot("COM14", snapshot(self.identity, 0x11)))
        self.assertEqual(self.live_state(), "press")
        self.assertFalse(home._apply_status_snapshot("COM14", snapshot(self.identity, 0x12, 2, session=8)))
        self.assertEqual(self.live_state(), "press")
        home._set_status_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
