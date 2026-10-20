from __future__ import annotations

import tkinter as tk
import unittest

from config.languages import t
from pages.chip_settings_model import ChipSettingsModel, ChipSettingsStore, can_save_slot
from pages.preset_toolbar import PRESET_ACTIONS, PresetToolbar


class PresetToolbarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)

    def test_construction_matches_two_rows_and_none_callbacks_are_safe(self) -> None:
        calls: list[str] = []
        toolbar = PresetToolbar(
            self.root,
            translate=lambda key: t("en", key),
            callbacks={"software_preset_1": lambda: calls.append("preset-1")},
            on_slot_selected=None,
        )

        self.assertEqual(tuple(toolbar.buttons), PRESET_ACTIONS)
        self.assertEqual(toolbar.buttons["software_preset_1"].grid_info()["row"], 0)
        self.assertEqual(toolbar.buttons["export_page_settings"].grid_info()["column"], 4)
        self.assertEqual(toolbar.slot_frame.grid_info()["row"], 1)
        self.assertEqual(toolbar.buttons["save_slot"].grid_info()["column"], 1)
        self.assertEqual(toolbar.buttons["read_and_apply"].grid_info()["column"], 4)
        self.assertEqual(str(toolbar.slot_combo.cget("state")), "readonly")

        toolbar.buttons["software_preset_1"].invoke()
        toolbar.buttons["read_and_apply"].invoke()
        toolbar.slot_var.set("2")
        toolbar._on_slot_selected()
        self.assertEqual(calls, ["preset-1"])
        self.assertEqual(toolbar.selected_slot, 2)

    def test_unsupported_capabilities_can_hide_or_disable_controls(self) -> None:
        toolbar = PresetToolbar(
            self.root,
            translate=lambda key: t("en", key),
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
        toolbar = PresetToolbar(
            self.root,
            translate=lambda key: t(language[0], key),
            callbacks={"software_preset_1": lambda: None},
        )

        self.assertEqual(toolbar.buttons["software_preset_1"].cget("text"), "Built-in Example 1 (writes chip)")
        self.assertEqual(toolbar.slot_label.cget("text"), "Offline slot")
        language[0] = "zh"
        toolbar.refresh_language()
        self.assertEqual(toolbar.buttons["software_preset_1"].cget("text"), "内置示例 1（写入芯片）")
        self.assertEqual(toolbar.slot_label.cget("text"), "脱机槽位")
        self.assertEqual(toolbar.buttons["set_active_slot"].cget("text"), "设为板上按键用的槽位")


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
