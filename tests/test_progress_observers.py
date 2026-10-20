from __future__ import annotations

from types import MethodType, SimpleNamespace
import threading
import unittest
from unittest import mock

from core.base_tool import SerialResponse
from core.register_settings_queue import RegisterSetting, RegisterSettingsQueue
from pages.awp1018_page import AWP1018NormalModePage
from pages.awp1221_page import AWP1221ModePage, AWP1221Page
from pages.awp192x_modes import ButtonModePage
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


class StatusProgressIntegrationTests(unittest.TestCase):
    def test_1221_en_ack_remains_success_when_followup_ldo_write_fails(self) -> None:
        calls = []
        statuses = []
        page = SimpleNamespace(
            en_enabled_var=SimpleNamespace(get=lambda: True),
            ctx=SimpleNamespace(serial=SimpleNamespace(
                configure_gpio=lambda pin, mode: calls.append((pin, mode)) or SerialResponse(True, data="ACK")
            )),
            _invalidate_sinc_for_en_disable=lambda: None,
            _invalidate_step_progress=lambda _key: None,
            _ensure_connected=lambda: True,
            _begin_step_progress=lambda key: statuses.append(("progress_begin", key)) or key,
            _mark_step_progress=lambda token, state, **_kwargs: statuses.append(("progress", token, state)),
            begin_status=lambda event: statuses.append(("begin", event)) or event,
            finish_status=lambda token, *, success, error="": statuses.append(("finish", token, success, error)),
            _gpio_write_ack_matches=lambda _response, _requested: True,
            _set_en_high_adc_load=lambda: (_ for _ in ()).throw(RuntimeError("fake LDO NACK")),
            _set_en_state=lambda state: statuses.append(("en_state", state)),
            _notify_shared_d4_gpio_panel=lambda mode: statuses.append(("panel", mode)),
            tr=lambda key, **_kwargs: key,
        )

        with mock.patch("pages.awp1221_page.messagebox.showerror"):
            AWP1221Page._on_en_toggled(page)

        self.assertEqual(calls, [("D4", "HIGH")])
        self.assertIn(("progress", "en", "done"), statuses)
        self.assertIn(("finish", "adc", False, "fake LDO NACK"), statuses)
        self.assertIn(("en_state", True), statuses)
        self.assertIn(("panel", "HIGH"), statuses)
        self.assertNotIn(("finish", "en", False, "fake LDO NACK"), statuses)

    def test_192x_configuration_and_start_have_separate_true_completion_points(self) -> None:
        events = []
        page = SimpleNamespace(
            page_name="full_mode",
            step_progress=None,
            _detect_generation=3,
            ctx=SimpleNamespace(serial=SimpleNamespace(is_connected=True)),
            _invalidate_detect_session=lambda: events.append("invalidate"),
            begin_status=lambda event: events.append(("begin", event)) or event,
            finish_status=lambda token, *, success, error="": events.append(("finish", token, success, error)),
            _write_button_output_timing_config=lambda: events.append("timing") or True,
            _apply_adc_threshold_config=lambda: events.append("threshold") or True,
            _apply_serial_waveform_config=lambda: events.append("serial") or True,
            _apply_sense_event_config=lambda: events.append("sense") or True,
            _write_button_adc_event_config=lambda **_kwargs: events.append("adc_event") or True,
            write_reg=lambda reg, value: events.append(("write", reg, value)) or True,
            _set_button_selected=lambda *_args: None,
            _reset_direct_detect_cycle=lambda: None,
            _set_sense_ready=lambda: None,
        )

        ButtonModePage._start_detection_direct(page)

        self.assertLess(events.index(("finish", "preset", True, "")), events.index(("begin", "detection")))
        self.assertLess(events.index(("begin", "detection")), events.index(("write", 0x06, 0x0001)))
        self.assertIn(("finish", "detection", True, ""), events)
        self.assertTrue(page._direct_detection_active)

        failed = SimpleNamespace(**vars(page))
        failed._detect_generation = 4
        failed_events = []
        failed.begin_status = lambda event: failed_events.append(("begin", event)) or event
        failed.finish_status = lambda token, *, success, error="": failed_events.append(("finish", token, success, error))
        failed._apply_adc_threshold_config = lambda: False
        ButtonModePage._start_detection_direct(failed)
        self.assertEqual(failed_events, [("begin", "preset"), ("finish", "preset", False, "configuration write failed")])

    def test_stale_sinc_revision_cannot_publish_completion_or_update_the_waveform(self) -> None:
        updates = []
        owner = SimpleNamespace(accepts_sinc_write=lambda *_args: False)
        page = SimpleNamespace(
            _shared_state_owner=owner,
            ctx=SimpleNamespace(serial=SimpleNamespace(is_connected=True)),
            _set_waveform_write_busy=lambda value: updates.append(("busy", value)),
            _mark_step_progress=lambda *args, **kwargs: updates.append(("progress", args, kwargs)),
            _current_settings_model=lambda: updates.append(("model",)) or None,
            tr=lambda key: key,
        )

        AWP1221ModePage._finish_sinc_write(
            page,
            None,
            object(),
            0x50,
            shared_state=object(),
            revision=2,
            progress_token=object(),
        )

        self.assertEqual(updates, [])

    def test_1018_queued_outcomes_are_current_fingerprint_gated_and_explicit(self) -> None:
        context = object()
        events = []
        host = SimpleNamespace(_progress_context=lambda: context)
        page = SimpleNamespace(
            step_progress=object(),
            _loading_settings=False,
            _preparing=False,
            _progress_host=host,
            _refresh_step_progress_params=lambda: None,
            _progress_fingerprints=lambda: {"adc": {"zero_time": "current"}},
            _progress_token=lambda key: events.append(("token", key)) or "adc-token",
            _progress_mark_part=lambda key, part, state, token: events.append(("part", key, part, state, token)),
            report_progress_status=lambda token, state, **kwargs: events.append(("status", token, state, kwargs)),
        )

        AWP1018NormalModePage._handle_queued_general_setting_result(
            page, "zero_time", "current", "completed", context,
        )
        AWP1018NormalModePage._handle_queued_general_setting_result(
            page, "zero_time", "current", "failed", context,
        )
        AWP1018NormalModePage._handle_queued_general_setting_result(
            page, "zero_time", "old", "failed", context,
        )
        AWP1018NormalModePage._handle_queued_general_setting_result(
            page, "zero_time", "current", "rejected", object(),
        )

        self.assertEqual(events, [
            ("token", "adc"),
            ("part", "adc", "zero_time", "done", "adc-token"),
            ("status", "adc-token", "done", {"detail": ""}),
            ("token", "adc"),
            ("part", "adc", "zero_time", "error", "adc-token"),
            ("status", "adc-token", "error", {"detail": "queued register write failed"}),
        ])


if __name__ == "__main__":
    unittest.main()
