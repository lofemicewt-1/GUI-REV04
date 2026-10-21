from __future__ import annotations

from pathlib import Path
import sys
import tkinter as tk
from tests.tk_cleanup import attach_tk_cleanup, cancel_after_jobs, prepare_tk_test
from tkinter import ttk
import unittest
from styles.theme import apply_theme, _button_image_layout
from tests.validate_responsive_layout import _capture_window_bmp


@unittest.skipUnless(sys.platform == "win32", "Windows native rendering comparison")
class ButtonRenderTests(unittest.TestCase):
    def test_optimized_buttons_preserve_native_pixels_geometry_and_states(self):
        prepare_tk_test(self)
        root = tk.Tk()
        attach_tk_cleanup(self, root)
        try:
            root.geometry("900x180+20+20")
            folder = Path(__file__).resolve().parents[1]/"outputs"/"button_render_tiles"
            folder.mkdir(parents=True, exist_ok=True)
            for theme in ("light", "dark"):
                apply_theme(root, theme)
                style = ttk.Style(root)
                families = (("TButton", "panel", False, False),
                            ("Quiet.TButton", "panel", False, True),
                            ("SidebarSelected.TButton", "sidebar", True, False))
                for target_style, group, selected, quiet in families:
                    source = root._rounded_button_images[theme][group]
                    normal = source["pressed"] if selected else source["normal"]
                    element = theme+".Reference."+target_style
                    reference_style = "Reference."+target_style
                    style.element_create(element, "image", normal,
                                         ("disabled", source["disabled"]),
                                         ("pressed", source["normal"] if quiet else source["pressed"]),
                                         ("active", normal), border=11, padding=8, sticky="nswe")
                    style.layout(reference_style, _button_image_layout(element))
                    button = ttk.Button(root, text="Test 123", takefocus=False)
                    button.place(x=20, y=20, width=120, height=43)
                    root.update()
                    for width in (120, 238, 570):
                        button.place_configure(width=width)
                        for state in ("normal", "pressed", "active", "disabled"):
                            pixels, sizes = [], []
                            for label, widget_style in (("before", reference_style), ("after", target_style)):
                                button.configure(style=widget_style)
                                button.state(["!active", "!pressed", "!disabled", "!focus"])
                                if state != "normal": button.state([state])
                                root.update_idletasks()
                                root.update()
                                path = folder/f"{theme}-{target_style}-{width}-{state}-{label}.bmp"
                                self.assertIsNone(_capture_window_bmp(button, path))
                                pixels.append(path.read_bytes()[54:])
                                sizes.append((button.winfo_reqwidth(), button.winfo_reqheight()))
                            self.assertEqual(sizes[0], sizes[1], (theme, target_style, width, state))
                            self.assertGreater(len(set(pixels[1])), 8)
                            self.assertEqual(pixels[0], pixels[1], (theme, target_style, width, state))
                    button.destroy()
        finally:
            cancel_after_jobs(root)
            root.destroy()
            root = None


if __name__ == "__main__":
    unittest.main()
