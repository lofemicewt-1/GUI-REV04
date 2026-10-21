from __future__ import annotations

import threading
import tkinter as tk
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
import unittest

from pages.button_mode_chrome import responsive_column_count
from tests.validate_responsive_layout import mocked_application
from tests.tk_cleanup import cancel_after_jobs, collect_tk_garbage


class AWP1018ButtonModeTests(unittest.TestCase):
    def _build_button_pages(self):
        try:
            prepare_tk_test(self)
            probe = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is unavailable: {exc}")
        try:
            cancel_after_jobs(probe)
            probe.destroy()
        finally:
            probe = None
            collect_tk_garbage()
        prepare_tk_test(self)
        app_context = mocked_application()
        app = app_context.__enter__()
        attach_tk_cleanup(self, app.root, app)
        self.addCleanup(app_context.__exit__, None, None, None)
        app.set_animations(False)
        app.navigation.show("awp1018")
        host = app.navigation.pages["awp1018"]
        normal = host.mode_pages["awp1018_normal_mode"]
        diff = host.mode_pages["awp1018_diff_mode"]
        app.root.update_idletasks()
        return app, host, normal, diff

    def test_modes_build_their_capability_sized_cards_and_unsupported_read_message(self):
        _app, host, normal, diff = self._build_button_pages()

        self.assertEqual(normal.channel_count, 8)
        self.assertEqual(len(normal._channel_checks), 8)
        self.assertEqual(len(normal._channel_card_grid.cards), 8)
        self.assertEqual(len(normal._channel_start_combos), 8)
        self.assertEqual(len(normal._diff_status_frames), 8)
        self.assertEqual(normal.stage_vars.keys(), {"Stage0", "Stage1", "Stage2", "Stage3"})
        self.assertTrue(
            normal._labels["awp1018_normal_8ch_live_read_unsupported"].cget("text")
        )
        self.assertTrue(all(value.get() == "" for value in normal._diff_status_value_vars.values()))
        self.assertNotIn("awp1018_realtime_display", normal._buttons)
        self.assertEqual(tuple(normal.preset_toolbar.slot_combo.cget("values")), ("0", "1", "2"))
        self.assertIn("read_and_apply", normal.preset_toolbar.auxiliary_actions)
        self.assertEqual(normal.preset_toolbar.buttons["read_and_apply"].winfo_manager(), "")
        read_callback = normal.preset_toolbar.callbacks["read_and_apply"]
        apply_callback = normal.preset_toolbar.callbacks["apply_slot"]
        self.assertIs(read_callback.__self__, normal)
        self.assertIs(apply_callback.__self__, normal)
        self.assertIsNot(read_callback.__func__, apply_callback.__func__)

        self.assertEqual(diff.channel_count, 4)
        self.assertEqual(len(diff._channel_checks), 4)
        self.assertEqual(len(diff._channel_card_grid.cards), 4)
        self.assertEqual(len(diff._channel_start_combos), 4)
        self.assertEqual(len(diff._diff_status_frames), 4)
        self.assertIn("awp1018_realtime_display", diff._buttons)
        self.assertIn("adc_stop_realtime", diff._buttons)
        self.assertNotIn("awp1018_normal_8ch_live_read_unsupported", diff._labels)
        self.assertEqual(tuple(diff.preset_toolbar.slot_combo.cget("values")), ("0", "1", "2"))
        self.assertEqual(tuple(host.mode_pages), (
            "awp1018_normal_mode",
            "awp1018_diff_mode",
            "full_mode",
        ))
        with self.assertRaises(ValueError):
            diff._validate_imported_settings(normal.export_settings())

    def test_register_import_selects_mode_from_register_34_bit_3(self):
        _app, host, _normal, _diff = self._build_button_pages()

        host._sync_decoded_page_settings(0x48, {0x34: 0x0008}, {})
        self.assertEqual(host.current_mode, "awp1018_diff_mode")
        host._sync_decoded_page_settings(0x48, {0x34: 0x0000}, {})
        self.assertEqual(host.current_mode, "awp1018_normal_mode")

    def test_device_and_mode_drafts_remain_separate(self):
        _app, host, normal, diff = self._build_button_pages()
        normal.activate_button_draft()
        normal.rom_waveform_var.set("Gauss")
        normal.save_button_draft()

        host.addr_var.set("0x49")
        host._on_addr_changed()
        self.assertEqual(normal.rom_waveform_var.get(), "SIN")
        host.addr_var.set("0x48")
        host._on_addr_changed()
        self.assertEqual(normal.rom_waveform_var.get(), "Gauss")

        host.show_mode("awp1018_diff_mode", update_chip=False)
        diff.rom_waveform_var.set("DC")
        diff.save_button_draft()
        host.addr_var.set("0x49")
        host._on_addr_changed()
        host.show_mode("awp1018_normal_mode", update_chip=False)
        self.assertEqual(normal.rom_waveform_var.get(), "SIN")
        host.addr_var.set("0x48")
        host._on_addr_changed()
        host.show_mode("awp1018_normal_mode", update_chip=False)
        self.assertEqual(normal.rom_waveform_var.get(), "Gauss")
        host.show_mode("awp1018_diff_mode", update_chip=False)
        self.assertEqual(diff.rom_waveform_var.get(), "DC")

    def test_hiding_diff_page_cancels_sampling_and_pending_poll(self):
        _app, _host, _normal, diff = self._build_button_pages()
        cancel = threading.Event()
        diff._diff_sample_cancel = cancel
        diff._diff_sample_active = True
        diff._diff_detect_active = True
        diff._diff_value_poll_job = diff.after(60_000, lambda: None)

        diff.on_hide()

        self.assertTrue(cancel.is_set())
        self.assertFalse(diff._diff_sample_active)
        self.assertFalse(diff._diff_detect_active)
        self.assertIsNone(diff._diff_value_poll_job)

    def test_channel_cards_use_four_two_and_one_column_breakpoints(self):
        self.assertEqual(responsive_column_count(900, 200, 8), 4)
        self.assertEqual(responsive_column_count(450, 200, 8), 2)
        self.assertEqual(responsive_column_count(199, 200, 8), 1)
        self.assertEqual(responsive_column_count(900, 200, 4), 4)


if __name__ == "__main__":
    unittest.main()
