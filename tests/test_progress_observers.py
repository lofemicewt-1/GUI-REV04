from __future__ import annotations

from types import MethodType, SimpleNamespace
import threading
import unittest
from unittest import mock

from core.base_tool import SerialResponse
from core.register_settings_queue import RegisterSetting, RegisterSettingsQueue
from pages.waveform_generator import WaveformGeneratorPage
from tests.test_awp1018_show_io import SlowManager


class FakeWaveformTransport:
    def __init__(self, fail_at: int | None = None) -> None:
        self.is_connected = True
        self._lock = threading.RLock()
        self.calls: list[tuple] = []
        self.fail_at = fail_at

    def write_register_value(self, address, register, value, *, bits):
        self.calls.append(("write", address, register, value, bits))
        ok = self.fail_at is None or len(self.calls) != self.fail_at
        return SerialResponse(ok, error="fake write failure" if not ok else "", data="OK" if ok else "")

    def write_register_values(self, address, register, values, *, bits=16):
        self.calls.append(("write_many", address, register, tuple(values), bits))
        ok = self.fail_at is None or len(self.calls) != self.fail_at
        return SerialResponse(ok, error="fake write failure" if not ok else "", data="OK" if ok else "")


class RegisterObserverTests(unittest.TestCase):
    @staticmethod
    def _normalized_calls(manager: SlowManager) -> list[tuple]:
        return [call[:3] + call[4:] if call[0] == "write" else call[:3] for call in manager.calls]

    def _run_one_setting(self, observed: bool) -> tuple[list[tuple], list[str]]:
        manager = SlowManager(0)
        tasks = RegisterSettingsQueue()
        outcomes: list[str] = []
        callback = outcomes.append if observed else None
        tasks.submit("one", RegisterSetting(manager, 0x48, 0x27, 9, None, lambda: True, callback))
        self.assertTrue(tasks.wait_idle(1))
        return self._normalized_calls(manager), outcomes

    def test_completed_observer_does_not_change_transport_call_sequence(self) -> None:
        baseline, _ = self._run_one_setting(False)
        observed, outcomes = self._run_one_setting(True)
        self.assertEqual(observed, baseline)
        self.assertEqual(outcomes, ["completed"])

    def test_coalesced_rejected_cancelled_and_callback_exception_outcomes(self) -> None:
        manager = SlowManager(0)
        entered, release = threading.Event(), threading.Event()
        manager.write_hook = lambda register: (entered.set(), release.wait(2)) if register == 0x27 else None
        tasks = RegisterSettingsQueue(capacity=1)
        results: list[tuple[str, str]] = []
        tasks.submit("active", RegisterSetting(manager, 0x48, 0x27, 1, None, lambda: True,
                                                 lambda result: results.append(("active", result))))
        self.assertTrue(entered.wait(1))
        tasks.submit("pending", RegisterSetting(manager, 0x48, 0x22, 2, None, lambda: True,
                                                  lambda result: results.append(("old", result))))
        tasks.submit("pending", RegisterSetting(manager, 0x48, 0x22, 3, None, lambda: True,
                                                  lambda result: results.append(("new", result))))
        accepted = tasks.submit("overflow", RegisterSetting(manager, 0x48, 0x06, 4, None, lambda: True,
                                                               lambda result: results.append(("overflow", result))))
        self.assertFalse(accepted)
        tasks.close()
        release.set()
        self.assertTrue(tasks.wait_idle(1))
        self.assertIn(("old", "coalesced"), results)
        self.assertIn(("new", "cancelled"), results)
        self.assertIn(("overflow", "rejected"), results)
        self.assertIn(("active", "completed"), results)
        self.assertEqual([call[2] for call in manager.calls if call[0] == "write"], [0x27])

        second = SlowManager(0)
        queue = RegisterSettingsQueue()
        queue.submit("safe", RegisterSetting(second, 0x48, 0x27, 5, None, lambda: True,
                                               lambda _outcome: (_ for _ in ()).throw(RuntimeError("observer"))))
        self.assertTrue(queue.wait_idle(1))
        self.assertEqual([call[2] for call in second.calls if call[0] == "write"], [0x27])


class WaveformObserverTests(unittest.TestCase):
    @staticmethod
    def _make_page(transport: FakeWaveformTransport, outcomes: list[tuple] | None = None):
        page = SimpleNamespace()
        page.ctx = SimpleNamespace(
            serial=transport,
            state=SimpleNamespace(current_max_output_reg_value=0x8888),
        )
        page.storage = SimpleNamespace(SRAM_MAX=0x7FFF)
        page.write_points_per_batch_var = SimpleNamespace(get=lambda: "1")
        page._analyzed_ranges = []
        page._set_waveform_write_busy = lambda _busy: None
        page._update_chip_cache_after_write = lambda *_args: None
        page._sync_read_slices_text = lambda: None
        page._notify_metrics_changed = lambda: None
        page._preview_waveform = lambda: None
        page.tr = lambda key, **_kwargs: key
        page.storage.add_or_replace = lambda *_args, **_kwargs: None
        page.storage.record_successful_write = lambda _slice: None
        page.after = lambda _delay, callback, *args: callback(*args)
        page._write_waveform_to_device = MethodType(WaveformGeneratorPage._write_waveform_to_device, page)
        page._write_waveform_to_device_locked = MethodType(WaveformGeneratorPage._write_waveform_to_device_locked, page)
        page._capture_write_observation = MethodType(WaveformGeneratorPage._capture_write_observation, page)
        page._selected_write_points_per_batch = lambda: 1
        page._finish_waveform_write = MethodType(WaveformGeneratorPage._finish_waveform_write, page)
        page._write_waveform_worker = MethodType(WaveformGeneratorPage._write_waveform_worker, page)
        page._report_write_observation = WaveformGeneratorPage._report_write_observation
        page._update_chip_cache_after_write = lambda *_args: None
        page._sync_read_slices_text = lambda: None
        page._notify_metrics_changed = lambda: None
        page._preview_waveform = lambda: None
        if outcomes is not None:
            page._waveform_write_observer = (lambda: ("opaque", 0x50, 3),
                                             lambda *args: outcomes.append(args))
        else:
            page._waveform_write_observer = None
        return page

    @staticmethod
    def _write_data():
        return ([0x10, 0x11], [1.0, 2.0], 3, 0x0020, 0x0022, "sine", None)

    def _run_write(self, observed: bool, *, fail_at: int | None = None):
        transport = FakeWaveformTransport(fail_at)
        results: list[tuple] = []
        page = self._make_page(transport, results if observed else None)
        if observed:
            observation = page._capture_write_observation(0x50, 3)
        else:
            observation = None
        with mock.patch("pages.waveform_generator.messagebox.showinfo"), \
             mock.patch("pages.waveform_generator.messagebox.showerror"):
            page._write_waveform_worker(self._write_data(), 0x50, observation)
        return transport.calls, results

    def test_waveform_done_and_error_observation_preserve_full_fake_write_sequence(self) -> None:
        baseline, _ = self._run_write(False)
        observed, outcomes = self._run_write(True)
        self.assertEqual(observed, baseline)
        self.assertEqual(outcomes, [(("opaque", 0x50, 3), "done", 0x50, 3)])

        baseline_error, _ = self._run_write(False, fail_at=4)
        observed_error, error_outcomes = self._run_write(True, fail_at=4)
        self.assertEqual(observed_error, baseline_error)
        self.assertEqual(error_outcomes, [(("opaque", 0x50, 3), "error", 0x50, 3)])

    def test_connected_write_captures_opaque_token_before_worker_and_offline_never_completes(self) -> None:
        transport = FakeWaveformTransport()
        page = SimpleNamespace()
        page._waveform_write_busy = False
        page._generate_current = lambda **_kwargs: ([1], [1.0], 4, 0x10, 0x11, "sine")
        page._power_on_recipe_for_values = lambda **_kwargs: None
        page.ctx = SimpleNamespace(serial=transport, state=SimpleNamespace(awp192x_device_addr=0x50))
        page._set_waveform_write_busy = lambda _busy: None
        page._waveform_write_observer = None
        captured: list[tuple] = []
        results: list[tuple] = []
        WaveformGeneratorPage.set_write_observer(page, lambda: captured.append(("token_factory",)) or "token",
                                                 lambda *args: results.append(args))
        page._capture_write_observation = MethodType(WaveformGeneratorPage._capture_write_observation, page)
        page._prepare_waveform_write_worker = lambda *_args: None

        class CapturedThread:
            def __init__(self, *, target, args, daemon):
                self.target, self.args, self.daemon = target, args, daemon
                captured.append(("thread", args))

            def start(self):
                return None

        with mock.patch("pages.waveform_generator.threading.Thread", CapturedThread):
            WaveformGeneratorPage._write_current_waveform(page, use_imported=False)
        self.assertEqual(captured[0], ("token_factory",))
        self.assertEqual(captured[1][0], "thread")
        self.assertEqual(captured[1][1][0][2], 4)
        self.assertEqual(captured[1][1][1], 0x50)
        self.assertEqual(captured[1][1][2][1], "token")

        offline_transport = FakeWaveformTransport()
        offline_transport.is_connected = False
        offline = SimpleNamespace()
        offline._waveform_write_busy = False
        offline._generate_current = page._generate_current
        offline._power_on_recipe_for_values = page._power_on_recipe_for_values
        offline.ctx = SimpleNamespace(serial=offline_transport, state=SimpleNamespace(awp192x_device_addr=0x50))
        offline._set_waveform_write_busy = lambda _busy: None
        offline._waveform_write_observer = (lambda *_args: self.fail("offline token factory called"),
                                             lambda *args: results.append(args))
        offline._find_waveform_conflicts = lambda *_args: []
        offline.storage = SimpleNamespace(add_or_replace=lambda *_args, **_kwargs: None)
        offline._analyzed_ranges = []
        offline._update_chip_cache_after_write = lambda *_args: None
        offline._sync_read_slices_text = lambda: None
        offline._notify_metrics_changed = lambda: None
        offline._preview_waveform = lambda: None
        offline.tr = lambda key, **_kwargs: key
        with mock.patch("pages.waveform_generator.messagebox.showwarning"):
            WaveformGeneratorPage._write_current_waveform(offline, use_imported=False)
        self.assertEqual(offline_transport.calls, [])
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
