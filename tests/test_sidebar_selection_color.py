from __future__ import annotations

import unittest

import tkinter as tk
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
from tkinter import ttk

from styles.theme import apply_theme, get_colors


def _rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def _pixel(image: tk.PhotoImage, x: int, y: int) -> tuple[int, int, int]:
    raw = image.get(x, y)
    if isinstance(raw, str):
        parts = raw.replace("#", "").split()
        if len(parts) == 1 and len(parts[0]) == 6:
            return _rgb(parts[0])
        return tuple(int(part) for part in parts[:3])
    return (int(raw[0]), int(raw[1]), int(raw[2]))


def _pixels(image: tk.PhotoImage) -> list[tuple[int, int, int]]:
    pixels: list[tuple[int, int, int]] = []
    for y in range(int(image.height())):
        for x in range(int(image.width())):
            raw = image.get(x, y)
            if isinstance(raw, str):
                parts = raw.replace("#", "").split()
                if len(parts) == 1 and len(parts[0]) == 6:
                    text = parts[0]
                    pixels.append((int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)))
                    continue
                pixels.append(tuple(int(part) for part in parts[:3]))
                continue
            pixels.append((int(raw[0]), int(raw[1]), int(raw[2])))
    return pixels


class SidebarSelectionColorTests(unittest.TestCase):
    def setUp(self) -> None:
        prepare_tk_test(self)
        self.root = tk.Tk()
        attach_tk_cleanup(self, self.root)
        self.root.withdraw()
    def test_mode_selected_image_matches_chip_selected_for_light_and_dark(self) -> None:
        for theme in ("light", "dark"):
            apply_theme(self.root, theme)
            store = self.root._rounded_button_images[theme]
            chip = _pixels(store["sidebar"]["pressed"])
            mode = _pixels(store["sidebar_mode_selected"]["pressed"])
            self.assertEqual(mode, chip, theme)

    def test_mode_selected_style_map_uses_chip_selected_colors(self) -> None:
        for theme in ("light", "dark"):
            apply_theme(self.root, theme)
            style = ttk.Style(self.root)
            colors = get_colors(theme)
            for option in ("background", "bordercolor", "lightcolor", "darkcolor"):
                for state in (["active"], ["pressed"]):
                    self.assertEqual(
                        style.lookup("SidebarModeSelected.TButton", option, state),
                        style.lookup("SidebarSelected.TButton", option, state),
                        f"{theme} {option} {state}",
                    )
            self.assertEqual(
                style.lookup("SidebarModeSelected.TButton", "background", ["disabled"]),
                colors["panel_alt"],
                theme,
            )
            self.assertEqual(
                style.lookup("SidebarModeSelected.TButton", "background"),
                style.lookup("Sidebar.TFrame", "background"),
                theme,
            )

    def test_unselected_mode_has_sidebar_surface_and_disabled_wins(self) -> None:
        for theme in ("light", "dark"):
            apply_theme(self.root, theme)
            colors = get_colors(theme)
            store = self.root._rounded_button_images[theme]
            idle = store["sidebar_mode"]["normal"]
            disabled = store["sidebar_mode_selected"]["disabled"]
            selected = store["sidebar"]["pressed"]
            self.assertEqual(_pixel(idle, 16, 16), _rgb(colors["panel_alt"]), theme)
            self.assertEqual(_pixel(disabled, 16, 16), _rgb(colors["panel_alt"]), theme)
            self.assertNotEqual(_pixel(disabled, 16, 16), _pixel(selected, 16, 16), theme)
            style = ttk.Style(self.root)
            self.assertEqual(style.lookup("SidebarMode.TButton", "background"), colors["panel_alt"], theme)

    def test_unselected_mode_pixels_and_state_maps_match_chip_buttons(self) -> None:
        for theme in ("light", "dark", "light"):
            apply_theme(self.root, theme)
            store = self.root._rounded_button_images[theme]
            style = ttk.Style(self.root)
            for state in ("normal", "pressed", "disabled"):
                self.assertEqual(_pixels(store["sidebar_mode"][state]),
                                 _pixels(store["sidebar"][state]))
            for states in ([], ["active"], ["disabled"], ["disabled", "active"]):
                for option in ("background", "foreground", "bordercolor"):
                    self.assertEqual(style.lookup("SidebarMode.TButton", option, states),
                                     style.lookup("Sidebar.TButton", option, states))

    def test_dark_selected_fill_is_distinct_from_unselected_mode(self) -> None:
        apply_theme(self.root, "dark")
        store = self.root._rounded_button_images["dark"]
        selected = _pixel(store["sidebar"]["pressed"], 16, 16)
        idle = _pixel(store["sidebar_mode"]["normal"], 16, 16)
        distance = sum((left - right) ** 2 for left, right in zip(selected, idle)) ** 0.5
        self.assertGreater(distance, 80, (selected, idle))
        self.assertEqual(selected, _pixel(store["sidebar_mode_selected"]["pressed"], 16, 16))
        self.assertEqual(selected, (0x28, 0x71, 0xFA))
        self.assertEqual(idle, _rgb(get_colors("dark")["panel_alt"]))
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup("SidebarSelected.TButton", "foreground").lower(), "#05070d")
        self.assertEqual(style.lookup("SidebarModeSelected.TButton", "foreground").lower(), "#05070d")
        self.assertEqual(style.lookup("SidebarMode.TButton", "foreground").lower(), get_colors("dark")["text"].lower())


if __name__ == "__main__":
    unittest.main()
