from __future__ import annotations

import json
import tempfile
from pathlib import Path
import tkinter as tk
from unittest import mock
import unittest

from app.ui_state_store import UiStateStore
from pages.base_page import BasePage
from styles.theme import apply_theme


class UiStateStoreTests(unittest.TestCase):
    def test_default_restart_distinct_keys_and_unrelated_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ui_state.json"
            store = UiStateStore(path)
            self.assertFalse(store.get_trapezoid_expanded("awp1221"))
            store.set_trapezoid_expanded("awp1221", True)
            store.set_trapezoid_expanded("awp1921_1923", False)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["unrelated"] = {"keep": 7}
            path.write_text(json.dumps(data), encoding="utf-8")

            restarted = UiStateStore(path)
            self.assertTrue(restarted.get_trapezoid_expanded("awp1221"))
            self.assertFalse(restarted.get_trapezoid_expanded("awp1921_1923", default=True))
            self.assertFalse(restarted.get_trapezoid_expanded("awp1018"))
            restarted.set_trapezoid_expanded("awp1018", True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["unrelated"], {"keep": 7})

    def test_corrupt_json_and_unwritable_replacement_are_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ui_state.json"
            path.write_text("{broken", encoding="utf-8")
            store = UiStateStore(path)
            self.assertFalse(store.get_trapezoid_expanded("chip"))
            store.set_trapezoid_expanded("chip", True)
            original = path.read_text(encoding="utf-8")
            with mock.patch("app.ui_state_store.os.replace", side_effect=OSError("read only")):
                store.set_trapezoid_expanded("chip", False)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_temporary_small_viewport_collapse_does_not_change_preference(self) -> None:
        root = tk.Tk()
        root.geometry("440x260+0+0")
        root.deiconify()
        colors = apply_theme(root, "light", 10, 1.0)
        ctx = type("Context", (), {})()
        ctx.colors = colors
        ctx.state = type("State", (), {"language": "en", "theme": "light"})()
        ctx.serial = type("Serial", (), {})()
        page = BasePage(root, ctx)
        page.pack(fill="both", expand=True)
        area, _content = page.make_vertical_scroll_area(page)
        area.pack(fill="both", expand=True)
        with tempfile.TemporaryDirectory() as directory:
            store = UiStateStore(Path(directory) / "ui_state.json")
            store.set_trapezoid_expanded("chip", True)
            try:
                with mock.patch("pages.step_progress.UiStateStore", return_value=store):
                    trapezoid = page.mount_trapezoid(lambda _frame: None, lambda _bar: None, state_key="chip")
                root.update()
                self.assertFalse(trapezoid.expanded)
                self.assertTrue(store.get_trapezoid_expanded("chip"))

                root.geometry("900x600+0+0")
                root.update()
                self.assertTrue(trapezoid.expanded)
                self.assertTrue(store.get_trapezoid_expanded("chip"))
            finally:
                page.destroy()
                root.destroy()


if __name__ == "__main__":
    unittest.main()
