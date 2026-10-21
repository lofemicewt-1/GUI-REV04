from __future__ import annotations

from contextlib import contextmanager
import threading
import unittest
from unittest.mock import patch

from core.base_tool import SerialResponse
from pages.awp1018_page import AWP1018FullModePage
from tests.test_motion import pump
from tests.validate_responsive_layout import mocked_application


class ProgressManager:
    def __init__(self) -> None:
        self.is_connected = True
        self.connection_generation = 1
        self.registers = {0x2B: 0x1018}
        self.calls: list[tuple] = []
        self.fail_registers: set[int] = set()
        self.fail_write_at: int | None = None
        self.write_index = 0
        self.lock = threading.RLock()
        self.read_started = threading.Event()
        self.release_read = threading.Event()
        self.block_reads = False
        self.log_history: list[str] = []

    @contextmanager
    def snapshot_transaction(self):
        with self.lock:
            yield

    def connection_guard(self):
        return self.lock

    def read_register_value(self, address, register, *, bits):
        self.calls.append(("read", address, register, bits))
        if self.block_reads:
            self.read_started.set()
            self.release_read.wait(2)
        return SerialResponse(True, data=f"{self.registers.get(register, 0):X}")

    def write_register_value(self, address, register, value, *, bits):
        self.write_index += 1
        self.calls.append(("write", address, register, value, bits))
        if register in self.fail_registers or self.write_index == self.fail_write_at:
            return SerialResponse(False, error="fake write rejected")
        self.registers[register] = value
        return SerialResponse(True, data="OK")

    def extract_rdataw_value(self, data, **_kwargs):
        return int(data, 16)

    def emit_log(self, message):
        self.log_history.append(message)

    def add_log_listener(self, _listener):
        return None

    def remove_log_listener(self, _listener):
        return None

    def add_log_clear_listener(self, _listener):
        return None

    def remove_log_clear_listener(self, _listener):
        return None


class Awp1018StepProgressTests(unittest.TestCase):
    def open_host(self):
        app_context = mocked_application()
        app = app_context.__enter__()
        self.addCleanup(app_context.__exit__, None, None, None)
        app.set_animations(False)
        app.navigation.show("awp1018")
        pump(app.root, 0.08)
        return app, app.navigation.pages["awp1018"]

    def attach_manager(self, app, host):
        manager = ProgressManager()
        app.serial._managers["FAKE_A"] = manager
        app.serial.set_page_port("awp1018", "FAKE_A")
        app.state.selected_port = "FAKE_A"
        pump(app.root, 0.04)
        return manager

    def flush_progress(self, app):
        # Drain UiDispatcher so mark*/mark_part land before asserts.
        # root.update() alone won't wait for the 16ms dispatcher tick.
        dispatcher = getattr(app, "ui_dispatcher", None) or getattr(
            app.root, "_piezo_ui_dispatcher", None
        )
        if dispatcher is not None:
            for _ in range(5):
                if dispatcher.drain(max_items=10000) == 0:
                    break
        pump(app.root, 0.06)

    def test_normal_diff_steps_and_full_trapezoid_only(self):
        app, host = self.open_host()
        normal = host.mode_pages["awp1018_normal_mode"]
        self.assertEqual([step.key for step in normal.step_progress.steps], [
            "channels", "waveform", "adc", "detection",
        ])
        self.assertIsNotNone(host.trapezoid)
        self.assertIs(host._floating_group.master, host.mode_scroll)
        self.assertGreaterEqual(host.mode_scroll.bottom_inset, 42)

        host.show_mode("full_mode", update_chip=False)
        full = host.mode_pages["full_mode"]
        self.assertIsInstance(full, AWP1018FullModePage)
        self.assertIsNone(full.step_progress)
        self.assertIsNone(host._active_step_page)
        self.assertEqual(len(host._step_progress_bars), 1)

        host.show_mode("awp1018_diff_mode", update_chip=False)
        diff = host.mode_pages["awp1018_diff_mode"]
        self.assertEqual([step.key for step in diff.step_progress.steps], [
            "channels", "waveform", "adc", "detection",
        ])
        self.assertIs(host._active_step_page, diff)

    def test_channel_receipts_and_failures_follow_mask_and_mapping_writes(self):
        app, host = self.open_host()
        page = host.mode_pages["awp1018_normal_mode"]
        manager = self.attach_manager(app, host)

        page.channel_vars[0].set(True)
        page._on_channel_select_changed()
        self.flush_progress(app)
        page.channel_start_stage_vars[0].set("Stage1")
        page._on_channel_start_stage_changed(0)
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["channels"]["state"], "done")
        self.assertEqual(
            [call[2] for call in manager.calls if call[0] == "write"],
            [0x07, 0x0D],
        )

        page.step_progress.reset_all()
        manager.fail_registers.add(0x07)
        page.channel_vars[1].set(True)
        page._on_channel_select_changed()
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["channels"]["state"], "error")
        self.assertNotIn("mask", page.step_progress.snapshot()["channels"]["parts"])

        manager.fail_registers.clear()
        manager.fail_registers.add(0x0D)
        page.channel_start_stage_vars[0].set("Stage2")
        page._on_channel_start_stage_changed(0)
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["channels"]["state"], "error")
        self.assertNotIn("mapping", page.step_progress.snapshot()["channels"]["parts"])

    def test_normal_waveform_is_blue_only_after_all_ten_writes_ack(self):
        app, host = self.open_host()
        page = host.mode_pages["awp1018_normal_mode"]
        manager = self.attach_manager(app, host)
        manager.calls.clear()

        self.assertTrue(page.apply_rom_waveform_selection(show_success=False))
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["waveform"]["state"], "done")
        self.assertEqual(
            [call[2] for call in manager.calls if call[0] == "write"],
            [0x05, 0x28, 0x00, 0x00, 0x00, 0x00, 0x05, 0x26, 0x0E, 0x12],
        )

        for failed_write in range(1, 11):
            manager.write_index = 0
            manager.fail_write_at = failed_write
            with patch("pages.awp1018_page.messagebox.showerror"):
                self.assertFalse(page.apply_rom_waveform_selection(show_success=False))
            self.flush_progress(app)
            self.assertEqual(
                page.step_progress.snapshot()["waveform"]["state"],
                "error",
                f"write {failed_write} must prevent waveform completion",
            )
        manager.fail_write_at = None

    def test_diff_serial4_and_partial_descriptor_failure(self):
        app, host = self.open_host()
        host.show_mode("awp1018_diff_mode", update_chip=False)
        page = host.mode_pages["awp1018_diff_mode"]
        manager = self.attach_manager(app, host)

        page.rom_slice_var.set("serial4")
        manager.calls.clear()
        self.assertTrue(page.apply_rom_waveform_selection(show_success=False))
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["waveform"]["state"], "done")
        self.assertEqual(
            [call[2] for call in manager.calls if call[0] == "write"],
            [0x29, 0x2A],
        )

        manager.fail_registers.add(0x2A)
        with patch("pages.awp1018_page.messagebox.showerror"):
            self.assertFalse(page.apply_rom_waveform_selection(show_success=False))
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["waveform"]["state"], "error")
        manager.fail_registers.clear()

        page.rom_slice_var.set("slice11")
        manager.calls.clear()
        manager.write_index = 0
        # Diff SIN emits one 4-word descriptor (writes: 0x05, 0x28, four 0x00, 0x05).
        # Fail on the second word write so the step stays red.
        manager.fail_write_at = 4
        with patch("pages.awp1018_page.messagebox.showerror"):
            self.assertFalse(page.apply_rom_waveform_selection(show_success=False))
        self.flush_progress(app)
        successful_slice_words = [
            call for call in manager.calls if call[0] == "write" and call[2] == 0x00
        ]
        # fail_write_at still records the rejected 0x00 call, so two 0x00 writes appear.
        self.assertEqual(len(successful_slice_words), 2)
        self.assertEqual(page.step_progress.snapshot()["waveform"]["state"], "error")

    def test_queued_settings_stage_snapshots_detection_stop_reset_and_loading(self):
        app, host = self.open_host()
        page = host.mode_pages["awp1018_normal_mode"]
        manager = self.attach_manager(app, host)
        router = app.serial

        manager.block_reads = True
        page.debounce_time_var.set("150")
        page._on_debounce_time_changed()
        self.assertTrue(manager.read_started.wait(1))
        page.debounce_time_var.set("200")
        page._on_debounce_time_changed()
        page.debounce_time_var.set("300")
        page._on_debounce_time_changed()
        manager.release_read.set()
        self.assertTrue(router._register_settings_queue.wait_idle(2))
        manager.block_reads = False
        self.flush_progress(app)
        adc = page.step_progress.snapshot()["adc"]
        self.assertEqual(adc["parts"]["debounce"], "300")

        manager.fail_registers.add(0x22)
        page.debounce_time_var.set("400")
        page._on_debounce_time_changed()
        self.assertTrue(router._register_settings_queue.wait_idle(2))
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["adc"]["state"], "error")
        self.assertNotIn("debounce", page.step_progress.snapshot()["adc"]["parts"])
        manager.fail_registers.clear()

        page.on_show()
        self.assertTrue(router._register_settings_queue.wait_idle(2))
        self.flush_progress(app)
        page._on_return_bit_config_changed()
        self.flush_progress(app)
        page.arbitration_mode_var.set(page._arbitration_mode_options()[0])
        page._on_arbitration_mode_changed()
        self.flush_progress(app)
        page._on_zps_enable_changed()
        self.flush_progress(app)
        page._on_interrupt_enable_changed()
        self.flush_progress(app)
        self.assertTrue(page._sync_stage_defaults_before_run())
        self.assertTrue(router._register_settings_queue.wait_idle(2))
        self.flush_progress(app)
        adc = page.step_progress.snapshot()["adc"]
        self.assertEqual(adc["state"], "done")
        self.assertTrue(all(f"stage{index}" in adc["parts"] for index in range(4)))
        self.assertIn("group_start", adc["parts"])

        manager.fail_registers.add(0x02)
        with patch("pages.awp1018_page.messagebox.showerror"):
            page.start_detection()
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["detection"]["state"], "error")
        manager.fail_registers.clear()
        with patch("pages.awp1018_page.messagebox.showinfo"), patch(
            "pages.awp1018_page.messagebox.showerror"
        ):
            page.start_detection()
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["detection"]["state"], "done")
        with patch("pages.awp1018_page.messagebox.showinfo"), patch(
            "pages.awp1018_page.messagebox.showerror"
        ):
            page.stop_detection()
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["detection"]["state"], "idle")

        with patch("pages.awp1018_page.messagebox.showinfo"), patch(
            "pages.awp1018_page.messagebox.showerror"
        ):
            page.start_detection()
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["detection"]["state"], "done")
        with patch("pages.awp1018_page.messagebox.showinfo"), patch(
            "pages.awp1018_page.messagebox.showerror"
        ):
            host.reset_chip()
        self.flush_progress(app)
        self.assertTrue(all(
            value["state"] == "idle" for value in page.step_progress.snapshot().values()
        ))

        page._loading_settings = True
        manager.calls.clear()
        self.assertTrue(page.apply_rom_waveform_selection(show_success=False))
        self.assertEqual(manager.calls, [])
        self.assertEqual(page.step_progress.snapshot()["waveform"]["state"], "idle")

    def test_mode_address_and_session_changes_clear_proofs(self):
        app, host = self.open_host()
        page = host.mode_pages["awp1018_normal_mode"]
        manager = self.attach_manager(app, host)
        self.assertTrue(page.apply_rom_waveform_selection(show_success=False))
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["waveform"]["state"], "done")

        host.show_mode("awp1018_diff_mode", update_chip=False)
        self.assertTrue(all(
            value["state"] == "idle" for value in page.step_progress.snapshot().values()
        ))
        diff = host.mode_pages["awp1018_diff_mode"]
        self.assertTrue(diff.apply_rom_waveform_selection(show_success=False))
        self.flush_progress(app)
        self.assertEqual(diff.step_progress.snapshot()["waveform"]["state"], "done")

        host.addr_var.set("0x49")
        host._on_addr_changed()
        self.assertTrue(all(
            value["state"] == "idle" for value in diff.step_progress.snapshot().values()
        ))
        self.assertTrue(diff.apply_rom_waveform_selection(show_success=False))
        self.flush_progress(app)
        manager.connection_generation += 1
        diff.step_progress._recheck_context()
        self.assertTrue(all(
            value["state"] == "idle" for value in diff.step_progress.snapshot().values()
        ))

    def test_queued_ack_keeps_its_submission_token_across_reset_all(self):
        app, host = self.open_host()
        page = host.mode_pages["awp1018_normal_mode"]
        manager = self.attach_manager(app, host)
        router = app.serial
        queued = {}
        router.queue_register_setting = lambda *args, **kwargs: queued.update(
            args=args, kwargs=kwargs
        )
        page._progress_tokens.pop("adc", None)

        with patch(
            "pages.awp1018_page.post_to_ui",
            lambda _widget, callback, *args: callback(*args),
        ):
            self.assertTrue(
                page._queue_general_setting(0x22, 150, progress_part="debounce")
            )
            submitted_token = page._progress_tokens["adc"]
            self.assertTrue(page.step_progress.accepts(submitted_token))

            page.step_progress.reset_all()
            self.assertFalse(page.step_progress.accepts(submitted_token))
            queued["kwargs"]["on_result"]("completed")

        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["adc"]["state"], "idle")
        self.assertNotIn("debounce", page.step_progress.snapshot()["adc"]["parts"])
        self.assertEqual(manager.calls, [])

    def test_successful_custom_register_sleep_and_shutdown_clear_proofs_but_reads_do_not(self):
        app, host = self.open_host()
        page = host.mode_pages["awp1018_normal_mode"]
        manager = self.attach_manager(app, host)
        progress = page.step_progress

        def mark_channels_done():
            # channels is an aggregate step (mask + mapping); mark(done)
            # alone is rejected until both part receipts exist.
            progress.update_params("channels", {"mask": "m1", "mapping": "g1"})
            token = progress.begin("channels")
            progress.mark_part("channels", "mask", "done", token)
            progress.mark_part("channels", "mapping", "done", token)
            self.flush_progress(app)
            self.assertEqual(progress.snapshot()["channels"]["state"], "done")

        host.reg_addr_var.set("0x12")
        manager.registers[0x12] = 0x1234
        mark_channels_done()
        host.read_register()
        self.flush_progress(app)
        self.assertEqual(progress.snapshot()["channels"]["state"], "done")

        actions = (
            ("write_register", host.write_register),
            ("sleep_chip", host.sleep_chip),
            ("shutdown_chip", host.shutdown_chip),
        )
        for action_name, action in actions:
            with self.subTest(action=action_name):
                progress.reset_all()
                mark_channels_done()
                manager.calls.clear()
                if action_name == "write_register":
                    host.write_value_var.set("0x1234")
                action()
                self.flush_progress(app)
                self.assertEqual(progress.snapshot()["channels"]["state"], "idle")
                self.assertEqual(len(manager.calls), 1)
                self.assertEqual(manager.calls[0][0], "write")

    def test_import_without_chip_apply_reconciles_changed_draft_fingerprints(self):
        app, host = self.open_host()
        page = host.mode_pages["awp1018_normal_mode"]
        manager = self.attach_manager(app, host)
        settings = page.export_settings()
        settings["channels"][0] = not settings["channels"][0]

        progress = page.step_progress
        progress.update_params("channels", {"mask": "m1", "mapping": "g1"})
        token = progress.begin("channels")
        progress.mark_part("channels", "mask", "done", token)
        progress.mark_part("channels", "mapping", "done", token)
        self.flush_progress(app)
        self.assertEqual(progress.snapshot()["channels"]["state"], "done")

        manager.calls.clear()
        self.assertTrue(page.import_settings(settings, apply_to_chip=False))
        self.flush_progress(app)
        self.assertEqual(page.step_progress.snapshot()["channels"]["state"], "idle")
        self.assertEqual(manager.calls, [])


if __name__ == "__main__":
    unittest.main()
