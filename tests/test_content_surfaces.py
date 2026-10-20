import unittest
import tkinter as tk
from tkinter import ttk
from styles.theme import apply_theme

class ContentSurfaceTests(unittest.TestCase):
    def test_surfaces_follow_theme_and_light_backgrounds_are_white(self):
        root=tk.Tk(); root.withdraw()
        try:
            for theme in ('light','dark','light'):
                with self.subTest(theme=theme):
                    colors=apply_theme(root,theme)
                    style=ttk.Style(root)
                    for role in ('panel','canvas_bg'):
                        self.assertEqual(colors[role],colors['bg'])
                    for name in ('App.TFrame','Panel.TFrame','TLabelframe','Panel.TLabel','Muted.TLabel','SectionTitle.TLabel'):
                        self.assertEqual(style.lookup(name,'background'),colors['bg'],name)
                    if theme == 'light':
                        for role in ('bg', 'panel', 'panel_alt', 'canvas_bg', 'field'):
                            self.assertEqual(colors[role], '#FFFFFF')
                    else:
                        self.assertNotEqual(style.lookup('TEntry','fieldbackground'),colors['bg'])
                    self.assertEqual(style.lookup('TEntry','fieldbackground'),style.lookup('TCombobox','fieldbackground',['readonly']))
        finally:root.destroy()

