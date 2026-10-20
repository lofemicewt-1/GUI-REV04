from __future__ import annotations

from dataclasses import dataclass
from types import MethodType, SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch

from pages.awp192x_modes import ButtonModePage, PumpModePage
from pages.chip_pages import AWP1921AWP1923Page
from pages.step_progress import ProgressContext
from styles.theme import apply_theme


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@dataclass(frozen=True)
class _Token:
    step: str
    version: int
    operation: int
    fingerprints: tuple[tuple[str, object], ...]


class _Progress:
    """Small deterministic progress double for business-hook tests."""

    def __init__(self, steps):
        self.steps = tuple(steps)
        self.index = {key: i for i, key in enumerate(self.steps)}
        self.fingerprints = {key: {} for key in self.steps}
        self.versions = {key: 0 for key in self.steps}
        self.operations = {key: 0 for key in self.steps}
        self.states = {key: "idle" for key in self.steps}
        self.receipts = {key: {} for key in self.steps}

    def update_params(self, step, fingerprints):
        new = dict(fingerprints)
        old = self.fingerprints[step]
        if new == old:
            return
        changed = {key for key in set(old) | set(new) if old.get(key) != new.get(key)}
        prior_receipts = dict(self.receipts[step])
        for key in self.steps[self.index[step] :]:
            self.versions[key] += 1
            self.operations[key] += 1
            self.states[key] = "idle"
            self.receipts[key].clear()
        self.fingerprints[step] = new
        if set(prior_receipts) and changed.issubset(set(self.steps) | set(old) | set(new)):
            self.receipts[step].update(
                {key: value for key, value in prior_receipts.items() if new.get(key) == value}
            )

    def begin(self, step):
        self.operations[step] += 1
        self.states[step] = "running"
        return _Token(
            step,
            self.versions[step],
            self.operations[step],
            tuple(sorted(self.fingerprints[step].items())),
        )

    def _current(self, step, token):
        return (
            token is not None
            and token.step == step
            and token.version == self.versions[step]
            and token.operation == self.operations[step]
        )

    def mark(self, step, state, token):
        if self._current(step, token):
            self.states[step] = state

    def mark_part(self, step, part, state, token):
        if not self._current(step, token):
            return
        if state == "done":
            captured = dict(token.fingerprints)
            if part in captured and captured[part] == self.fingerprints[step].get(part):
                self.receipts[step][part] = captured[part]
        elif state == "error":
            self.receipts[step].pop(part, None)
        expected = {"frequency", "amplitude"} if step == "output" else set()
        if state == "error":
            self.states[step] = "error"
        else:
            self.states[step] = "done" if expected and expected.issubset(self.receipts[step]) else "running"

    def invalidate_from(self, step):
        for key in self.steps[self.index[step] :]:
            self.versions[key] += 1
            self.operations[key] += 1
            self.states[key] = "idle"
            self.receipts[key].clear()

    def reset_all(self):
        for key in self.steps:
            self.versions[key] += 1
            self.operations[key] += 1
            self.states[key] = "idle"
            self.receipts[key].clear()


def _bind(page, cls, *names):
    for name in names:
        setattr(page, name, MethodType(getattr(cls, name), page))
    return page


class Awp192xWaveformProgressTests(unittest.TestCase):
    def _owner(self, requirements=(0,)):
        page = SimpleNamespace(
            page_name="button_mode",
            step_progress=_Progress(("waveform", "config", "detection")),
            _button_active_waveform_requirements=lambda: requirements,
            _sync_button_step_progress_params=lambda: None,
        )
        return _bind(
            page,
            ButtonModePage,
            "_mark_button_progress_step",
            "_reconcile_rom_waveform_progress",
            "_on_button_waveform_write_result",
        )

    def test_rom_completion_requires_all_active_waveform_requirements_to_be_rom(self):
        mixed = self._owner((0,))
        mixed._reconcile_rom_waveform_progress()
        self.assertEqual(mixed.step_progress.states["waveform"], "idle")

        rom_only = self._owner(())
        rom_only._reconcile_rom_waveform_progress()
        self.assertEqual(rom_only.step_progress.states["waveform"], "done")

        unknown = self._owner(None)
        unknown._reconcile_rom_waveform_progress()
        token = unknown.step_progress.begin("waveform")
        unknown._on_button_waveform_write_result(token, "done", 0x44, 0)
        self.assertEqual(unknown.step_progress.states["waveform"], "running")

    def test_offline_conflict_error_and_stale_write_results_do_not_false_complete(self):
        owner = self._owner((0,))
        bar = owner.step_progress

        # An offline local preview never calls the connected-write observer.
        self.assertEqual(bar.states["waveform"], "idle")

        conflict = bar.begin("waveform")
        owner._on_button_waveform_write_result(conflict, "cancelled", 0x44, 0)
        self.assertEqual(bar.states["waveform"], "idle")

        failed = bar.begin("waveform")
        owner._on_button_waveform_write_result(failed, "error", 0x44, 0)
        self.assertEqual(bar.states["waveform"], "error")

        stale = bar.begin("waveform")
        bar.update_params("waveform", {"settings": ("new waveform inputs",)})
        owner._on_button_waveform_write_result(stale, "done", 0x44, 0)
        self.assertEqual(bar.states["waveform"], "idle")


class Awp192xButtonProgressTests(unittest.TestCase):
    def _detection_page(self, outcomes, *, start_ok=True):
        progress = _Progress(("waveform", "config", "detection"))
        page = SimpleNamespace(
            page_name="button_mode",
            STATUS_TIMEOUT_COLOR=ButtonModePage.STATUS_TIMEOUT_COLOR,
            step_progress=progress,
            _detect_generation=0,
            _direct_detection_active=False,
            _sync_button_step_progress_params=lambda: None,
            _reset_direct_detect_cycle=lambda: None,
            _set_button_selected=lambda *_args: None,
            _set_sense_status_color=lambda _color: None,
            _set_sense_ready=lambda: None,
            _clear_sense_playback_registers=lambda **_kwargs: None,
            _finish_detection_stop_ui=lambda: None,
        )
        page._invalidate_detect_session = lambda: setattr(page, "_detect_generation", page._detect_generation + 1)
        for name in (
            "_begin_button_progress_step",
            "_mark_button_progress_step",
        ):
            setattr(page, name, MethodType(getattr(ButtonModePage, name), page))
        calls = []
        helper_names = (
            "_write_button_output_timing_config",
            "_apply_adc_threshold_config",
            "_apply_serial_waveform_config",
            "_apply_sense_event_config",
            "_write_button_adc_event_config",
        )
        for index, name in enumerate(helper_names):
            def helper(*_args, _index=index, _name=name, **_kwargs):
                calls.append(_name)
                return outcomes[_index]
            setattr(page, name, helper)
        page.write_reg = lambda reg, value: calls.append(("start", reg, value)) or start_ok
        return page, calls, progress

    def test_each_configuration_helper_failure_leaves_both_steps_failed(self):
        for failed_index in range(5):
            with self.subTest(helper=failed_index):
                outcomes = [True] * 5
                outcomes[failed_index] = False
                page, calls, progress = self._detection_page(outcomes)
                ButtonModePage._start_detection_direct(page)
                self.assertNotIn(("start", 0x06, 0x0001), calls)
                self.assertEqual(progress.states["config"], "error")
                self.assertEqual(progress.states["detection"], "error")

    def test_final_start_failure_keeps_config_done_and_detection_red(self):
        page, calls, progress = self._detection_page([True] * 5, start_ok=False)
        ButtonModePage._start_detection_direct(page)
        self.assertEqual(calls[-1], ("start", 0x06, 0x0001))
        self.assertEqual(progress.states["config"], "done")
        self.assertEqual(progress.states["detection"], "error")
        self.assertFalse(page._direct_detection_active)

    def test_stop_detection_clears_detection_after_success_only(self):
        page, _calls, progress = self._detection_page([True] * 5)
        progress.states["config"] = "done"
        progress.states["detection"] = "done"
        page.write_reg = lambda _reg, _value: True
        ButtonModePage.stop_detection(page)
        self.assertEqual(progress.states["config"], "done")
        self.assertEqual(progress.states["detection"], "idle")

    def test_preset_error_does_not_complete_waveform_or_config(self):
        progress = _Progress(("waveform", "config"))
        tokens = {key: progress.begin(key) for key in ("waveform", "config")}
        page = SimpleNamespace(
            step_progress=progress,
            page_name="button_mode",
            _preset_waveform_write_busy=True,
            waveform_page=SimpleNamespace(_set_waveform_write_busy=lambda _busy: None),
            _set_preset_buttons_state=lambda _state: None,
            _update_period_hint=lambda **_kwargs: None,
            tr=lambda key: key,
        )
        _bind(page, ButtonModePage, "_finish_button_preset_progress", "_mark_button_progress_step")
        with patch("pages.awp192x_modes.messagebox.showerror"):
            ButtonModePage._finish_preset_write(page, ([], [], 0, 2, 3, "sinc"), "waveform write failed", progress_tokens=tokens)
        self.assertEqual(progress.states["waveform"], "error")
        self.assertEqual(progress.states["config"], "error")

    def test_preset_success_completes_only_the_captured_waveform_and_config(self):
        progress = _Progress(("waveform", "config"))
        tokens = {key: progress.begin(key) for key in ("waveform", "config")}

        class Storage:
            def add_or_replace(self, *_args, **_kwargs):
                return None

            def record_successful_write(self, _slice):
                return None

        waveform_page = SimpleNamespace(
            _set_waveform_write_busy=lambda _busy: None,
            _analyzed_ranges=[],
            _update_chip_cache_after_write=lambda *_args: None,
            _sync_read_slices_text=lambda: None,
            _preview_waveform=lambda: None,
        )
        page = SimpleNamespace(
            step_progress=progress,
            page_name="button_mode",
            _preset_waveform_write_busy=True,
            waveform_page=waveform_page,
            _set_preset_buttons_state=lambda _state: None,
            _update_period_hint=lambda **_kwargs: None,
            _power_on_recipe_for_values=lambda **_kwargs: "recipe",
            storage=Storage(),
            _chip_settings_model=None,
            tr=lambda key: key,
        )
        _bind(page, ButtonModePage, "_finish_button_preset_progress", "_mark_button_progress_step")
        with patch("pages.awp192x_modes.messagebox.showinfo"):
            ButtonModePage._finish_preset_write(
                page,
                ([1, 2], [], 0, 2, 4, "sinc"),
                None,
                "recipe",
                progress_tokens=tokens,
            )
        self.assertEqual(progress.states["waveform"], "done")
        self.assertEqual(progress.states["config"], "done")


class Awp192xPumpProgressTests(unittest.TestCase):
    def _pump(self, *, fail_reg=None):
        progress = _Progress(("output", "start"))
        calls = []
        page = SimpleNamespace(
            step_progress=progress,
            amp_options=[("5.94 V", 5.94, 0x0004)],
            amp_var=_Value("5.94 V"),
            freq_var=_Value("200 Hz"),
            slow_up_var=_Value("0"),
            slow_down_var=_Value("0"),
            precise_frequency_var=_Value(False),
            _current_frequency_option=lambda: ("200 Hz", 200, 4, "1024"),
            ctx=SimpleNamespace(state=SimpleNamespace()),
        )
        page.compose_pump_reg14_value = PumpModePage.compose_pump_reg14_value
        page.write_reg = lambda reg, value, show_error=False: (calls.append((reg, value)) or reg != fail_reg)
        _bind(
            page,
            PumpModePage,
            "_pump_output_fingerprints",
            "_sync_pump_step_progress_params",
            "_begin_pump_output_attempt",
            "_record_pump_output_part",
            "_current_data_depth_label",
            "_on_amp_changed",
            "_on_freq_changed",
        )
        return page, calls, progress

    def test_frequency_then_amplitude_receipts_preserve_existing_write_order(self):
        page, calls, progress = self._pump()
        page._on_freq_changed()
        self.assertEqual([reg for reg, _value in calls], [0x15, 0x14])
        self.assertEqual(progress.states["output"], "done")
        self.assertEqual(set(progress.receipts["output"]), {"frequency", "amplitude"})

    def test_partial_output_failure_still_runs_later_write_and_never_completes(self):
        page, calls, progress = self._pump(fail_reg=0x15)
        page._on_freq_changed()
        self.assertEqual([reg for reg, _value in calls], [0x15, 0x14])
        self.assertEqual(progress.states["output"], "error")
        self.assertEqual(set(progress.receipts["output"]), {"amplitude"})

    def test_start_only_completes_in_success_callback_and_stop_resets_both_steps(self):
        page, _calls, progress = self._pump()
        page.ctx.serial = SimpleNamespace(is_connected=True)
        page._preparing = False
        page._set_button_selected = lambda *_args: None
        async_calls = []
        page.write_reg_async = lambda reg, value, **callbacks: async_calls.append((reg, value, callbacks))
        PumpModePage.start_play(page)
        self.assertEqual(progress.states["start"], "running")
        async_calls[-1][2]["on_success"]()
        self.assertEqual(progress.states["start"], "done")

        host_context = ProgressContext(("COM1", 1, 1), 0x44, "pump_mode")
        host = SimpleNamespace(_step_progress_context=lambda: host_context)
        page._pump_step_host = lambda: host
        page.step_progress.states["output"] = "done"
        page.step_progress.states["start"] = "done"
        PumpModePage.stop_play(page)
        async_calls[-1][2]["on_success"]()
        self.assertEqual(progress.states, {"output": "idle", "start": "idle"})


class Awp192xHostProgressTests(unittest.TestCase):
    def test_successful_reset_state_command_resets_progress_without_extra_writes(self):
        progress = _Progress(("output", "start"))
        progress.states["output"] = "done"
        progress.states["start"] = "done"
        calls = []
        serial = SimpleNamespace(
            is_connected=True,
            write_register_value=lambda *args, **kwargs: calls.append((args, kwargs))
            or SimpleNamespace(ok=True, error=""),
        )
        context = ProgressContext(("COM1", 2, 3), 0x44, "pump_mode")
        page = SimpleNamespace(
            ctx=SimpleNamespace(serial=serial, state=SimpleNamespace(awp192x_device_addr=0x44)),
            state_buttons={},
            _step_progress_bars=[progress],
            _step_progress_context=lambda: context,
            tr=lambda key: key,
        )

        class ImmediateThread:
            def __init__(self, *, target, args=(), daemon):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        with patch("pages.chip_pages.threading.Thread", ImmediateThread), patch(
            "core.ui_dispatch.post_to_ui", lambda _widget, callback, *args: callback(*args)
        ):
            AWP1921AWP1923Page.write_chip_state(page, 0x5283)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][1:3], (0x01, 0x5283))
        self.assertEqual(progress.states, {"output": "idle", "start": "idle"})

    def test_successful_custom_register_write_resets_progress(self):
        progress = _Progress(("output", "start"))
        progress.states["output"] = "done"
        progress.states["start"] = "done"
        calls = []
        serial = SimpleNamespace(
            is_connected=True,
            write_register_value=lambda *args, **kwargs: calls.append((args, kwargs))
            or SimpleNamespace(ok=True, error=""),
        )
        page = SimpleNamespace(
            ctx=SimpleNamespace(serial=serial, state=SimpleNamespace(awp192x_device_addr=0x44)),
            detail_mode_active=lambda: False,
            _current_detail_definition=lambda: None,
            _active_register_address=lambda: 0x12,
            _parse_int=lambda text: int(text, 0),
            write_value_var=_Value("0x1234"),
            _step_progress_bars=[progress],
            tr=lambda key: key,
        )

        AWP1921AWP1923Page.write_register(page)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][1:3], (0x12, 0x1234))
        self.assertEqual(progress.states, {"output": "idle", "start": "idle"})


class Awp192xChromeTkTests(unittest.TestCase):
    class _OfflineSerial:
        is_connected = False
        log_history: list[str] = []

        def __init__(self):
            self.calls = []

        def add_log_listener(self, _listener):
            return None

        def remove_log_listener(self, _listener):
            return None

        def add_log_clear_listener(self, _listener):
            return None

        def remove_log_clear_listener(self, _listener):
            return None

        def connected_port_options(self):
            return []

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        colors = apply_theme(self.root, "light", 10, 1.0)
        self.serial = self._OfflineSerial()
        ctx = SimpleNamespace(
            serial=self.serial,
            colors=colors,
            state=SimpleNamespace(
                language="en",
                theme="light",
                extra={},
                selected_port=None,
                awp192x_device_addr=0x44,
                single_point_duration_us=10,
            ),
        )
        self.page = AWP1921AWP1923Page(self.root, ctx)

    def tearDown(self):
        try:
            self.page.destroy()
        except (tk.TclError, AttributeError):
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def test_full_mode_uses_shared_trapezoid_for_detail_editor_without_progress(self):
        self.assertIs(self.page.reg_panel, self.page.trapezoid.registers)
        self.assertIs(self.page._detail_host.master, self.page.trapezoid.panel)
        self.assertTrue(self.page.identity_bar.grid_info())

        self.page.show_mode("full_mode")
        full = self.page.mode_pages["full_mode"]
        self.assertIsNone(full.step_progress)
        self.page.detail_mode_var.set(True)
        self.page._on_detail_mode_changed()
        self.assertTrue(self.page.detail_mode_active())
        self.assertTrue(self.page._detail_host.grid_info())
        self.assertEqual(str(self.page.write_value_entry.cget("textvariable")), str(self.page.write_value_var))
        self.page.write_value_var.set("0x1234")
        self.assertEqual(self.serial.calls, [])


if __name__ == "__main__":
    unittest.main()
