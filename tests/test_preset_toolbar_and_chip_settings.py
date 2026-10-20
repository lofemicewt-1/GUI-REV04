from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import unittest

from config.lang_r2_b import install
from config.languages import t
from pages.chip_settings_model import ChipSettingsModel, ChipSettingsStore, can_save_slot
from pages.preset_toolbar import PRESET_ACTIONS, PresetToolbar


class PresetToolbarTests(unittest.TestCase):
    def setUp(self) -> None:
        install()
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def _make_toolbar(self, **kwargs) -> PresetToolbar:
        host = ttk.Frame(self.root)
        host.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        translate = kwargs.pop("translate", lambda key: t("en", key))
        toolbar = PresetToolbar(
            host,
            translate=translate,
            **kwargs,
        )
        toolbar.mount_header()
        return toolbar

    def _settle(self) -> None:
        self.root.update_idletasks()
        self.root.update()

    def test_construction_groups_controls_and_none_callbacks_are_safe(self) -> None:
        calls: list[str] = []
        toolbar = self._make_toolbar(
            callbacks={"software_preset_1": lambda: calls.append("preset-1")},
            on_slot_selected=None,
        )
        toolbar.reflow(2000)

        self.assertEqual(tuple(toolbar.buttons), PRESET_ACTIONS)
        self.assertEqual(toolbar.software_frame.cget("style"), toolbar.board_frame.cget("style"))
        self.assertIs(toolbar.buttons["software_preset_1"].master, toolbar.software_frame)
        self.assertIs(toolbar.buttons["export_page_settings"].master, toolbar.software_frame)
        self.assertIs(toolbar.buttons["save_slot"].master, toolbar.board_frame)
        self.assertIs(toolbar.slot_frame.master, toolbar.board_frame)
        for action, button in toolbar.buttons.items():
            self.assertEqual(int(button.grid_info()["row"]), 0, action)
        self.assertEqual(int(toolbar.slot_frame.grid_info()["row"]), 0)
        self.assertEqual(str(toolbar.slot_combo.cget("state")), "readonly")

        toolbar.buttons["software_preset_1"].invoke()
        toolbar.buttons["read_and_apply"].invoke()
        toolbar.slot_var.set("2")
        toolbar._on_slot_selected()
        self.assertEqual(calls, ["preset-1"])
        self.assertEqual(toolbar.selected_slot, 2)

    def test_unsupported_capabilities_can_hide_or_disable_controls(self) -> None:
        toolbar = self._make_toolbar(
            callbacks={"software_preset_1": lambda: None, "save_slot": lambda: None},
            capabilities={"software_preset_1": False, "save_slot": False},
            hidden_capabilities={"software_preset_1"},
        )

        self.assertEqual(toolbar.buttons["software_preset_1"].winfo_manager(), "")
        self.assertEqual(str(toolbar.buttons["software_preset_1"].cget("state")), "disabled")
        self.assertEqual(toolbar.buttons["save_slot"].winfo_manager(), "grid")
        self.assertEqual(str(toolbar.buttons["save_slot"].cget("state")), "disabled")

    def test_labels_refresh_from_translation_keys(self) -> None:
        language = ["en"]
        toolbar = self._make_toolbar(
            translate=lambda key: t(language[0], key),
            callbacks={"software_preset_1": lambda: None},
        )

        self._settle()
        self.assertEqual(toolbar.buttons["software_preset_1"].cget("text"), "Built-in Example 1 (writes chip)")
        self.assertEqual(toolbar.slot_label.cget("text"), "Offline slot")
        self.assertEqual(toolbar._software_title_label.cget("text"), "Software")
        language[0] = "zh"
        toolbar.refresh_language()
        self._settle()
        self.assertEqual(toolbar.buttons["software_preset_1"].cget("text"), "内置示例 1（写入芯片）")
        self.assertEqual(toolbar.slot_label.cget("text"), "脱机槽位")
        self.assertEqual(toolbar._software_title_label.cget("text"), "软件")
        self.assertEqual(toolbar._board_title_label.cget("text"), "上电启动")
        self.assertEqual(toolbar.buttons["set_active_slot"].cget("text"), "设为板上按键用的槽位")

    def test_auxiliary_reads_keep_callbacks_and_follow_disabled_button_state(self) -> None:
        calls: list[str] = []
        toolbar = self._make_toolbar(
            callbacks={"apply_slot": lambda: calls.append("apply"),
                       "read_and_apply": lambda: calls.append("read")},
            auxiliary_actions=("read_and_apply",),
            label_keys={"read_and_apply": "r2b_aux_read_current_settings"},
        )
        self._settle()

        self.assertEqual(toolbar.buttons["read_and_apply"].winfo_manager(), "")
        self.assertEqual(toolbar._auxiliary_menu.entrycget(0, "label"), "Read current chip settings")
        self.assertEqual(toolbar._auxiliary_menu.entrycget(0, "state"), "normal")
        toolbar._auxiliary_menu.invoke(0)
        self.assertEqual(calls, ["read"])

        toolbar.buttons["read_and_apply"].configure(state="disabled")
        toolbar._refresh_auxiliary_menu_states()
        self.assertEqual(toolbar._auxiliary_menu.entrycget(0, "state"), "disabled")
        toolbar._auxiliary_menu.invoke(0)
        self.assertEqual(calls, ["read"])

    def test_reflow_stacks_whole_boxes_then_expands_without_rebuilding(self) -> None:
        self.root.geometry("800x360")
        self.root.deiconify()
        toolbar = self._make_toolbar(
            callbacks={action: (lambda: None) for action in PRESET_ACTIONS},
            auxiliary_actions=("read_and_apply",),
        )
        self._settle()
        widget_ids = {
            action: id(button)
            for action, button in toolbar.buttons.items()
        }
        widget_ids["software_group"] = id(toolbar.software_frame)
        widget_ids["board_group"] = id(toolbar.board_frame)

        self.assertEqual(toolbar.reflow(1), "stacked")
        self._settle()
        self.assertEqual(int(toolbar.software_frame.grid_info()["row"]), 0)
        self.assertEqual(int(toolbar.board_frame.grid_info()["row"]), 1)
        for action, button in toolbar.buttons.items():
            if button.winfo_manager() == "grid":
                self.assertEqual(int(button.grid_info()["row"]), 0, action)

        minimum_width = sum(toolbar._measured_widths.values()) + 8
        # Widen the window before asserting side-by-side so idle/_settle
        # auto-layout does not immediately re-stack at the prior 800px width.
        self.root.geometry(f"{max(minimum_width + 80, 1600)}x360")
        self._settle()
        self.assertEqual(toolbar.reflow(minimum_width), "side_by_side")
        self._settle()
        self.assertEqual(int(toolbar.software_frame.grid_info()["row"]), 0)
        self.assertEqual(int(toolbar.board_frame.grid_info()["row"]), 0)
        self.assertEqual(
            {action: id(button) for action, button in toolbar.buttons.items()},
            {key: value for key, value in widget_ids.items() if key not in {"software_group", "board_group"}},
        )

        before_width = int(toolbar.software_frame.winfo_width()) + int(toolbar.board_frame.winfo_width())
        self.root.geometry("2200x360")
        self._settle()
        after_width = int(toolbar.software_frame.winfo_width()) + int(toolbar.board_frame.winfo_width())
        self.assertGreater(after_width, before_width)
        self.assertGreaterEqual(after_width + 8, int(toolbar.winfo_width()))

    def test_layout_requests_coalesce_and_destroy_cancels_pending_job(self) -> None:
        toolbar = self._make_toolbar(callbacks={"software_preset_1": lambda: None})
        self._settle()
        initial_runs = toolbar._layout_run_count
        for _ in range(20):
            toolbar._queue_layout()
            toolbar.refresh_theme()
            toolbar.refresh_language()
        pending = toolbar._layout_job
        self.assertIsNotNone(pending)
        self.assertEqual(toolbar._layout_job, pending)
        toolbar.destroy()
        self.assertIsNone(toolbar._layout_job)
        self.root.update_idletasks()
        self.assertEqual(toolbar._layout_run_count, initial_runs)


class ChipSettingsModelTests(unittest.TestCase):
    def test_device_drafts_are_isolated_and_applied_state_is_cleared_on_switch(self) -> None:
        store = ChipSettingsStore()
        first = store.select_device("COM7", 0x44)
        first.set_shared("waveform_recipe", {"kind": "sinc", "times": [10, 20]})
        first.set_advanced("gpio", {"pin": 2, "mode": "detect"})
        revision_before_slot_choice = first.revision
        first.set_slot_selection(3)
        self.assertEqual(first.revision, revision_before_slot_choice)
        first.mark_applied()
        self.assertTrue(first.applied)
        self.assertFalse(first.dirty)

        second = store.select_device("COM7", "0x45")
        self.assertIsNot(first, second)
        self.assertEqual(second.shared, {})
        self.assertEqual(second.advanced, {})
        self.assertIsNone(second.slot_selection)
        self.assertFalse(first.applied)
        self.assertFalse(second.applied)

        second.set_shared("waveform_recipe", {"kind": "rom", "slice": 4})
        other_connection = store.select_device("COM8", 0x44)
        self.assertEqual(other_connection.shared, {})
        self.assertNotEqual(other_connection.device_key, first.device_key)

        restored = store.select_device("COM7", 0x44)
        self.assertIs(restored, first)
        self.assertEqual(restored.shared["waveform_recipe"], {"kind": "sinc", "times": [10, 20]})
        self.assertEqual(restored.advanced["gpio"], {"pin": 2, "mode": "detect"})
        self.assertEqual(restored.slot_selection, 3)
        self.assertFalse(restored.applied)
        self.assertIsNone(restored.applied_revision)

    def test_value_edits_track_revision_and_default_slot_preflight_allows_save(self) -> None:
        model = ChipSettingsModel()
        self.assertTrue(model.set_shared("thresholds", (100, 40)))
        self.assertEqual(model.revision, 1)
        self.assertTrue(model.dirty)
        self.assertFalse(model.applied)
        self.assertFalse(model.set_shared("thresholds", (100, 40)))
        self.assertEqual(model.revision, 1)
        self.assertEqual(can_save_slot(model), (True, None))


if __name__ == "__main__":
    unittest.main()
