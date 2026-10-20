from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pages.awp1221_page import GPIO_MODE_HIGH, GPIO_MODE_LOW
from tests.validate_responsive_layout import _flush, mocked_application


class Awp1221StepProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app_context = mocked_application()
        cls.app = cls.app_context.__enter__()
        cls.root = cls.app.root
        cls.app.set_animations(False)
        cls.app.navigation.show("awp1221")
        _flush(cls.root, 5)
        cls.host = cls.app.navigation.pages["awp1221"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app_context.__exit__(None, None, None)

    def setUp(self) -> None:
        # StepProgressBar.mark* posts through UiDispatcher (16ms tick).
        # Deliver synchronously in tests so receipts land before asserts.
        self.dispatch_patch = patch(
            "pages.step_progress.post_to_ui",
            lambda _widget, callback, *args: callback(*args),
        )
        self.dispatch_patch.start()
        self.addCleanup(self.dispatch_patch.stop)
        self.identity = ("COM_PROGRESS", 1221, 4)
        self.identity_patch = patch(
            "pages.awp1221_page.active_serial_identity",
            side_effect=lambda _ctx, _page: self.identity,
        )
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)
        self.host.show_mode("button_mode")
        self.page = self.host.mode_pages["button_mode"]
        self.progress = self.page.step_progress
        self.progress.reset_all()
        self.progress.activate()
        _flush(self.root, 4)

    def _mark_done(self, key: str) -> None:
        token = self.progress.begin(key)
        if key == "adc":
            for part in ("settings", "stage0", "stage1"):
                self.progress.mark_part(key, part, "done", token)
        else:
            self.progress.mark(key, "done", token)
        _flush(self.root, 4)

    def test_mounts_button_progress_and_keeps_trapezoid_for_full(self) -> None:
        self.assertEqual(
            [(step.key, step.ordinal) for step in self.progress.steps],
            [("en", 1), ("waveform", 2), ("adc", 3), ("detection", 4)],
        )
        self.assertEqual(self.progress._metadata[1]["title_key"], "awp1221_chip_en_title")
        self.assertEqual(self.progress._metadata[2]["title_key"], "awp1221_button_wave_settings")
        self.assertEqual(self.progress._metadata[3]["title_key"], "awp1221_button_adc_settings")
        self.assertEqual(self.progress._metadata[4]["title_key"], "awp1221_control_buttons")
        trapezoid = self.host.trapezoid
        self.assertIsNotNone(trapezoid)
        self.assertIn("rr", self.host._buttons)
        self.assertIn("rw", self.host._buttons)
        self.assertIsNotNone(self.host._buttons["detect_chip"])

        self.host.show_mode("full_mode")
        _flush(self.root, 5)
        full = self.host.mode_pages["full_mode"]
        self.assertIsNone(full.step_progress)
        self.assertIs(self.host.trapezoid, trapezoid)
        self.assertIsNone(self.host._active_step_page)

        self.host.show_mode("button_mode")
        _flush(self.root, 5)
        self.assertIs(self.host._active_step_page, self.page)

    def test_high_ack_is_blue_before_adc_ldo_failure_and_bad_ack_is_red(self) -> None:
        self.host.en_enabled_var.set(True)
        with (
            patch.object(self.host, "_ensure_connected", return_value=True),
            patch.object(self.host, "_set_en_high_adc_load", side_effect=RuntimeError("ADC_LDO failed")) as load,
            patch.object(
                self.host.ctx.serial,
                "configure_gpio",
                return_value=SimpleNamespace(ok=True, data="GPIOCFG D4 HIGH", error=""),
            ) as configure_gpio,
            patch("pages.awp1221_page.messagebox.showerror"),
        ):
            self.host._on_en_toggled()
        _flush(self.root, 4)
        self.assertEqual(self.progress.snapshot()["en"]["state"], "done")
        configure_gpio.assert_called_once_with("D4", GPIO_MODE_HIGH)
        load.assert_called_once_with()

        self.progress.reset_all()
        with (
            patch.object(self.host, "_ensure_connected", return_value=True),
            patch.object(
                self.host.ctx.serial,
                "configure_gpio",
                return_value=SimpleNamespace(ok=True, data="GPIOCFG D4 LOW", error=""),
            ) as configure_gpio,
            patch("pages.awp1221_page.messagebox.showerror"),
        ):
            self.host._on_en_toggled()
        _flush(self.root, 4)
        self.assertEqual(self.progress.snapshot()["en"]["state"], "error")
        configure_gpio.assert_called_once_with("D4", GPIO_MODE_HIGH)

    def test_en_low_clears_all_later_proofs_without_extra_gpio_calls(self) -> None:
        for key in ("en", "waveform", "adc", "detection"):
            self._mark_done(key)
        self.host.en_enabled_var.set(False)
        with (
            patch.object(self.host, "_ensure_connected", return_value=True),
            patch.object(
                self.host.ctx.serial,
                "configure_gpio",
                return_value=SimpleNamespace(ok=True, data="GPIOCFG D4 LOW", error=""),
            ) as configure_gpio,
        ):
            self.host._on_en_toggled()
        _flush(self.root, 4)
        self.assertEqual(
            [self.progress.snapshot()[key]["state"] for key in ("en", "waveform", "adc", "detection")],
            ["idle", "idle", "idle", "idle"],
        )
        configure_gpio.assert_called_once_with("D4", GPIO_MODE_LOW)

    def test_stop_commands_clear_only_detection_after_success(self) -> None:
        for key in ("en", "waveform", "adc", "detection"):
            self._mark_done(key)
        with (
            patch.object(self.page, "_send_control_command", return_value=True) as send,
            patch.object(self.page, "_set_detection_state"),
        ):
            self.page.stop_detection()
        _flush(self.root, 4)
        state = self.progress.snapshot()
        self.assertEqual([state[key]["state"] for key in ("en", "waveform", "adc")], ["done"] * 3)
        self.assertEqual(state["detection"]["state"], "idle")
        send.assert_called_once_with("stop_detection")

        self._mark_done("detection")
        with (
            patch.object(self.page, "_send_control_command", return_value=True) as send,
            patch.object(self.page, "_set_detection_state"),
            patch.object(self.page, "_set_waveform_play_state"),
        ):
            self.page.stop_all()
        _flush(self.root, 4)
        state = self.progress.snapshot()
        self.assertEqual([state[key]["state"] for key in ("en", "waveform", "adc")], ["done"] * 3)
        self.assertEqual(state["detection"]["state"], "idle")
        send.assert_called_once_with("awp1221_stop_all")

    def test_adc_receipts_aggregate_and_parameter_edit_invalidates_downstream(self) -> None:
        with (
            patch.object(self.page, "_live_write_allowed", return_value=True),
            patch.object(self.page, "_write_adc_settings"),
            patch.object(self.page, "_write_button_adc_stage") as write_stage,
            patch.object(self.page, "_show_live_write_error"),
        ):
            self.page._commit_live_settings()
            _flush(self.root, 4)
            state = self.progress.snapshot()["adc"]
            self.assertEqual(state["parts"], {"settings": state["fingerprints"]["settings"]})

            write_stage.side_effect = ValueError("stage write failed")
            self.page._commit_live_stage(0)
            _flush(self.root, 4)
            failed = self.progress.snapshot()["adc"]
            self.assertEqual(failed["state"], "error")
            self.assertIn("settings", failed["parts"])
            self.assertNotIn("stage0", failed["parts"])

            write_stage.side_effect = None
            self.page._commit_live_stage(0)
            self.page._commit_live_stage(1)
            _flush(self.root, 5)
            self.assertEqual(self.progress.snapshot()["adc"]["state"], "done")

        self.page._button_stage_vars[0]["threshold"].set("333")
        _flush(self.root, 5)
        adc = self.progress.snapshot()["adc"]
        # Shared update_params retains unchanged aggregate parts (settings);
        # only the edited stage receipt is dropped, and the step leaves "done".
        self.assertEqual(adc["state"], "idle")
        self.assertIn("settings", adc["parts"])
        self.assertNotIn("stage0", adc["parts"])
        # Unchanged stage1 fingerprint is retained by shared update_params.
        self.assertIn("stage1", adc["parts"])
        self.assertEqual(self.progress.snapshot()["detection"]["state"], "idle")

    def test_each_adc_component_failure_has_no_receipt(self) -> None:
        for part, apply in (
            ("settings", lambda: self.page.apply_adc_settings()),
            ("stage0", lambda: self.page.apply_button_adc_stage(0)),
            ("stage1", lambda: self.page.apply_button_adc_stage(1)),
        ):
            with self.subTest(part=part):
                self.progress.reset_all()
                self.page._adc_progress_token = None
                writer = "_write_adc_settings" if part == "settings" else "_write_button_adc_stage"
                with (
                    patch.object(self.page, "_ensure_connected", return_value=True),
                    patch.object(self.page, writer, side_effect=RuntimeError(f"{part} failed")),
                    patch("pages.awp1221_page.messagebox.showerror"),
                ):
                    apply()
                _flush(self.root, 4)
                adc = self.progress.snapshot()["adc"]
                self.assertEqual(adc["state"], "error")
                self.assertNotIn(part, adc["parts"])

    def test_parameter_edit_rejects_late_waveform_and_detection_results(self) -> None:
        waveform_token = self.progress.begin("waveform")
        detection_token = self.progress.begin("detection")
        self.page.rom_point_time_var.set("3")
        self.page._button_stage_vars[0]["threshold"].set("321")
        self.progress.mark("waveform", "done", waveform_token)
        self.progress.mark("detection", "done", detection_token)
        _flush(self.root, 5)
        self.assertEqual(self.progress.snapshot()["waveform"]["state"], "idle")
        self.assertEqual(self.progress.snapshot()["detection"]["state"], "idle")

    def test_verified_start_continue_does_not_certify_mismatched_adc_and_trial_is_only_a_note(self) -> None:
        adc_token = self.page._capture_adc_progress_token()
        detection_token = self.page._capture_step_progress_token("detection")
        snapshot = {"device_addr": 0x50, "mode": "button_mode", "settings": {}, "stages": ()}

        def run_task(_widget, task, on_done, on_error):
            try:
                on_done(task())
            except Exception as exc:  # keep worker error semantics visible to the page callback
                on_error(exc)

        with (
            patch.object(self.page, "_ask_register_mismatch", return_value="start"),
            patch.object(self.page, "_write_control_command") as command,
            patch.object(self.page, "_set_detection_state"),
            patch.object(self.page, "_sync_realtime_adc_load"),
            patch("pages.awp1221_page._run_ui_background_task", side_effect=run_task),
        ):
            self.page._prompt_verified_command(
                "start_detection",
                snapshot,
                ("settings",),
                self.page._adc_lifecycle_generation,
                {"adc": adc_token, "detection": detection_token},
            )
        _flush(self.root, 5)
        adc = self.progress.snapshot()["adc"]
        self.assertEqual(adc["state"], "error")
        self.assertNotIn("settings", adc["parts"])
        self.assertEqual(self.progress.snapshot()["detection"]["state"], "done")
        command.assert_called_once_with("start_detection", 0x50)

        self.progress.invalidate_from("detection")
        _flush(self.root, 3)
        trial_token = self.page._capture_step_progress_token("detection")
        with patch.object(self.page, "_set_waveform_play_state"):
            self.page._finish_verified_command(
                "start_play",
                None,
                started=True,
                device_addr=0x50,
                generation=self.page._adc_lifecycle_generation,
                progress_tokens={"detection": trial_token},
            )
        _flush(self.root, 4)
        detection = self.progress.snapshot()["detection"]
        self.assertNotEqual(detection["state"], "done")
        self.assertEqual(detection["note_key"], "step_trial_played")

    def test_verified_start_cancel_invalidates_mismatched_receipt_and_preserves_other_steps(self) -> None:
        self._mark_done("waveform")
        self._mark_done("adc")
        adc_token = self.page._capture_adc_progress_token(force_new=True)
        detection_token = self.page._capture_step_progress_token("detection")
        _flush(self.root, 4)
        snapshot = {"device_addr": 0x50, "mode": "button_mode", "settings": {}, "stages": ()}
        with (
            patch.object(self.page, "_ask_register_mismatch", return_value="cancel"),
            patch.object(self.page, "_set_start_detection_busy"),
            patch("pages.awp1221_page._run_ui_background_task") as run_task,
        ):
            self.page._prompt_verified_command(
                "start_detection",
                snapshot,
                ("settings",),
                self.page._adc_lifecycle_generation,
                {"adc": adc_token, "detection": detection_token},
            )
        _flush(self.root, 4)
        states = self.progress.snapshot()
        self.assertEqual(states["waveform"]["state"], "done")
        self.assertEqual(states["adc"]["state"], "error")
        self.assertEqual(set(states["adc"]["parts"]), {"stage0", "stage1"})
        self.assertEqual(states["detection"]["state"], "idle")
        run_task.assert_not_called()

    def test_verified_start_failure_marks_detection_error(self) -> None:
        adc_token = self.page._capture_adc_progress_token(force_new=True)
        detection_token = self.page._capture_step_progress_token("detection")
        _flush(self.root, 4)
        snapshot = {"device_addr": 0x50, "mode": "button_mode", "settings": {}, "stages": ()}

        def run_task(_widget, task, on_done, on_error):
            try:
                on_done(task())
            except Exception as exc:
                on_error(exc)

        with (
            patch.object(self.page, "_set_start_detection_busy"),
            patch.object(self.page, "_apply_verified_start", side_effect=RuntimeError("start failed")),
            patch("pages.awp1221_page._run_ui_background_task", side_effect=run_task),
            patch("pages.awp1221_page.messagebox.showerror"),
        ):
            self.page._prompt_verified_command(
                "start_detection",
                snapshot,
                (),
                self.page._adc_lifecycle_generation,
                {"adc": adc_token, "detection": detection_token},
            )
        _flush(self.root, 5)
        self.assertEqual(self.progress.snapshot()["detection"]["state"], "error")
        self.assertEqual(self.progress.snapshot()["adc"]["state"], "idle")

    def test_stale_sinc_revision_does_not_mark_waveform(self) -> None:
        token = self.progress.begin("waveform")
        owner = SimpleNamespace(accepts_sinc_write=Mock(return_value=False))
        with (
            patch.object(self.page, "_shared_state_owner", owner),
            patch.object(self.page, "ctx", SimpleNamespace(serial=SimpleNamespace(is_connected=True))),
            patch.object(self.page, "_set_waveform_write_busy"),
        ):
            self.page._finish_sinc_write(
                None,
                object(),
                0x50,
                shared_state=object(),
                revision=2,
                progress_token=token,
            )
        _flush(self.root, 4)
        self.assertEqual(self.progress.snapshot()["waveform"]["state"], "running")


if __name__ == "__main__":
    unittest.main()
