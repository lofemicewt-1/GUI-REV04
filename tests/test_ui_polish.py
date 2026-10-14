from __future__ import annotations

import os
import random
import time
import tkinter as tk
import unittest
from tkinter import font as tkfont
from tkinter import ttk
from unittest import mock

from core.text_wrap import guard_orphans, orphan_safe, tk_wrap


def pump(root, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.002)


class OrphanTests(unittest.TestCase):
    def test_pure_logic(self) -> None:
        m = lambda s: len(s) * 10  # noqa: E731
        self.assertEqual(tk_wrap("一二三四五六七", m, 30), ["一二三", "四五六", "七"])
        fixed = orphan_safe("一二三四五六七", m, 30)
        self.assertEqual(tk_wrap(fixed, m, 30)[-1], "六七")
        self.assertEqual(orphan_safe("一二三四五六", m, 60), "一二三四五六")  # fits: untouched
        self.assertEqual(orphan_safe("ab cd e", m, 50), "ab cd e")  # space break: not forced

    def test_simulation_matches_tk_and_guard_rewraps(self) -> None:
        root = tk.Tk()
        try:
            font = tkfont.Font(root=root, family="Microsoft YaHei UI", size=10)
            chars = "的一是了我不在人有这中大为上个国和地到以说时要就出，。、ABC 0x12 8-bit"
            rng = random.Random(7)
            for _ in range(150):
                text = "".join(rng.choice(chars) for _ in range(rng.randint(5, 80)))
                width = rng.randint(60, 300)
                label = ttk.Label(root, text=text, wraplength=width, font=font)
                lines = round((label.winfo_reqheight() - 2) / font.metrics("linespace"))
                self.assertEqual(len(tk_wrap(text, font.measure, width)), lines, text)
                label.destroy()
            # guard: find a text that orphans at this width
            width = 200
            text = "一" * 1
            while len(tk_wrap(text, font.measure, width)) < 2 or len(tk_wrap(text, font.measure, width)[-1]) != 1:
                text += "二"
            label = ttk.Label(root, text="x", wraplength=width, font=font, style="Muted.TLabel")
            label.pack()
            self.assertEqual(guard_orphans(root), 1)
            label.configure(text=text)
            shown = str(label.cget("text"))
            self.assertIn("\n", shown)
            self.assertGreaterEqual(len(tk_wrap(shown, font.measure, width)[-1]), 2)
            label.configure(wraplength=2000)  # wider: manual break must go away
            self.assertEqual(str(label.cget("text")), text)
        finally:
            root.destroy()


class AppPolishTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.application import PiezoApplication

        self.app = PiezoApplication()
        self.app.root.geometry("1300x850+0+0")
        pump(self.app.root, 0.3)

    def tearDown(self) -> None:
        self.app.close()

    def _font_of(self, widget) -> tkfont.Font:
        return tkfont.Font(root=self.app.root, font=widget.cget("font"))

    def test_entries_use_tabular_digits_without_growing(self) -> None:
        home = self.app.navigation.pages["home"]
        entries = [w for w in _walk(home) if isinstance(w, ttk.Entry) and not isinstance(w, ttk.Combobox)]
        self.assertTrue(entries)
        body = tkfont.Font(root=self.app.root, family="Microsoft YaHei UI", size=int(getattr(self.app.root, "_piezo_body_font_size", 10)))
        for entry in entries:
            f = self._font_of(entry)
            self.assertTrue(f.metrics("fixed") or f.measure("1") == f.measure("8"), f.actual())
            self.assertLessEqual(f.metrics("linespace"), body.metrics("linespace"))
        label = home.mark_digits(ttk.Label(home, text="123.45 Hz"))
        f = self._font_of(label)
        self.assertEqual(f.measure("1"), f.measure("8"))
        label.destroy()

    def test_dark_mode_lightens_page_titles_only(self) -> None:
        app = self.app
        nav = app.navigation
        nav.show("awp1922")
        pump(app.root, 0.2)
        page = nav.pages["awp1922"]

        def title_weights():
            return {
                str(self._font_of(w).actual("weight"))
                for w in _walk(page)
                if isinstance(w, ttk.Label) and str(w.cget("style")) in {"ChipTitle.TLabel", "Title.TLabel", "Prominent.TLabel"}
            }

        self.assertEqual(title_weights(), {"bold"})
        app.apply_settings(app.state.language, "dark", app.state.font_size)
        pump(app.root, 0.2)
        self.assertEqual(title_weights(), {"normal"})
        style = ttk.Style(app.root)
        self.assertIn("bold", str(style.lookup("SectionTitle.TLabel", "font")))  # other bold text untouched
        app.apply_settings(app.state.language, "light", app.state.font_size)
        pump(app.root, 0.2)
        self.assertEqual(title_weights(), {"bold"})

    def test_page_fade_overlay_runs_and_parks(self) -> None:
        nav = self.app.navigation
        with mock.patch.dict(os.environ, {"PIEZO_X11_FADE": "1", "PIEZO_PAGE_TRANSITION": "fade"}):
            nav._fade_capable = None
            alphas: list[float] = []
            nav.show("awp1922")
            pump(self.app.root, 0.05)
            overlay = nav._fade_overlay
            original = overlay.attributes

            def spy(*args):
                if len(args) == 2 and args[0] == "-alpha":
                    alphas.append(float(args[1]))
                return original(*args)

            overlay.attributes = spy
            nav.show("home")
            pump(self.app.root, 1.0)
            self.assertEqual(alphas[0], 1.0)
            self.assertEqual(alphas[-1], 0.0)
            self.assertEqual(alphas, sorted(alphas, reverse=True))
            self.assertLess(overlay.winfo_rootx(), -1000)  # parked
            alphas.clear()
            self.app.animator.set_enabled(False)
            nav.show("awp1018")
            pump(self.app.root, 0.1)
            self.assertEqual(alphas, [])  # toggle off: no overlay at all

    def test_default_slide_transition_is_visible_and_settles(self) -> None:
        nav = self.app.navigation
        os.environ.pop("PIEZO_PAGE_TRANSITION", None)
        ys: list[float] = []
        canvas = nav.content_canvas
        original = canvas.coords

        def spy(*args):
            if len(args) == 3:
                ys.append(float(args[2]))
            return original(*args)

        canvas.coords = spy
        nav.show("awp1922")
        pump(self.app.root, 0.6)
        self.assertGreaterEqual(len(ys), 8)  # many intermediate frames
        self.assertGreater(ys[0], 0)
        self.assertEqual(ys[-1], 0.0)
        self.assertEqual(ys[:-1], sorted(ys[:-1], reverse=True))
        self.assertEqual(original(nav.content_window)[1], 0.0)
        ys.clear()
        self.app.animator.set_enabled(False)
        nav.show("home")
        pump(self.app.root, 0.1)
        self.assertTrue(all(y == 0.0 for y in ys))

    def test_no_fade_without_compositor_support(self) -> None:
        nav = self.app.navigation
        with mock.patch.dict(os.environ, {"PIEZO_X11_FADE": ""}):
            nav._fade_capable = None
            if str(self.app.root.tk.call("tk", "windowingsystem")) == "x11":
                self.assertFalse(nav._begin_page_fade())


def _walk(widget):
    stack = [widget]
    while stack:
        w = stack.pop()
        yield w
        stack.extend(w.winfo_children())


if __name__ == "__main__":
    unittest.main()


class PageShowFinalFontsTests(unittest.TestCase):
    def test_no_font_or_layout_change_after_page_is_revealed(self) -> None:
        import tkinter.font as tf
        from app.application import PiezoApplication
        app = PiezoApplication()
        try:
            r = app.root
            nav = app.navigation
            r.geometry("1000x680+0+0"); pump(r, 0.3)
            r.geometry("1700x1000+0+0"); pump(r, 0.3)
            calls = {"families": 0}
            orig = tf.families
            def fam(*a, **k):
                calls["families"] += 1
                return orig(*a, **k)
            with mock.patch.object(tf, "families", fam):
                for name in nav.pages:
                    if name == app.state.current_page:
                        continue
                    nav.show(name)
                    r.update_idletasks()
                    def snap(w, out):
                        for c in w.winfo_children():
                            keys = c.keys()
                            out[str(c)] = (str(c.cget("font")) if "font" in keys else "",
                                           str(c.cget("text")) if "text" in keys else "",
                                           c.winfo_width(), c.winfo_height(), c.winfo_x(), c.winfo_y())
                            snap(c, out)
                        return out
                    before = snap(nav.pages[name], {})
                    pump(r, 0.4)
                    after = snap(nav.pages[name], {})
                    self.assertEqual(before, after, name)
                    self.assertIsNone(nav._layout_cover.place_info().get("in"))
            self.assertEqual(calls["families"], 0)
        finally:
            app.close()
