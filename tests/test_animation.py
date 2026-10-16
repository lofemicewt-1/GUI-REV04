from __future__ import annotations

import time
import tkinter as tk
import unittest

from core import animation as anim


def pump(root, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.002)


class EasingTests(unittest.TestCase):
    def test_bezier_endpoints_monotonic_and_linear(self) -> None:
        linear = anim.cubic_bezier(0, 0, 1, 1)
        for x in (0.1, 0.33, 0.5, 0.9):
            self.assertAlmostEqual(linear(x), x, places=3)
        for ease in (anim.EASE_OUT, anim.EASE_IN_OUT, anim.EASE_SNAP, anim.EASE_GRAPHEME):
            self.assertEqual(ease(0), 0.0)
            self.assertEqual(ease(1), 1.0)
            values = [ease(i / 50) for i in range(51)]
            self.assertEqual(values, sorted(values))
        self.assertGreater(anim.EASE_OUT(0.25), 0.5)  # ease-out: fast start
        peak = max(anim.EASE_SETTLE(i / 200) for i in range(201))
        self.assertTrue(1.0 < peak < 1.08)  # small settle overshoot

    def test_lerp_color(self) -> None:
        self.assertEqual(anim.lerp_color("#000000", "#FFFFFF", 0), "#000000")
        self.assertEqual(anim.lerp_color("#000000", "#FFFFFF", 1), "#FFFFFF")
        self.assertEqual(anim.lerp_color("#000", "#FFF", 0.5), "#808080")
        self.assertEqual(anim.lerp_color("red", "#FFFFFF", 1), "#FFFFFF")


class AnimatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.a = anim.Animator(self.root)

    def tearDown(self) -> None:
        self.a.stop()
        self.root.destroy()

    def test_runs_to_exact_end_and_calls_done(self) -> None:
        seen: list[float] = []
        done: list[bool] = []
        self.a.animate("k", 80, seen.append, done=lambda: done.append(True))
        pump(self.root, 0.25)
        self.assertEqual(seen[-1], 1.0)
        self.assertGreater(len(seen), 3)
        self.assertEqual(done, [True])

    def test_busy_ui_slows_motion_instead_of_skipping(self) -> None:
        seen: list[float] = []
        self.a.animate("k", 100, seen.append)
        self.root.update()
        time.sleep(0.3)  # heavy page build right after start: > duration
        pump(self.root, 0.4)
        self.assertGreaterEqual(len([p for p in seen if 0 < p < 1]), 3)
        self.assertEqual(seen[-1], 1.0)

    def test_disabled_jumps_to_end(self) -> None:
        self.a.set_enabled(False)
        seen: list[float] = []
        self.a.animate("k", 500, seen.append)
        self.assertEqual(seen, [1.0])

    def test_same_key_replaces(self) -> None:
        first: list[float] = []
        second: list[float] = []
        self.a.animate("k", 300, first.append)
        self.a.animate("k", 60, second.append)
        pump(self.root, 0.2)
        self.assertNotIn(1.0, first)
        self.assertEqual(second[-1], 1.0)

    def test_disable_mid_flight_finishes(self) -> None:
        seen: list[float] = []
        self.a.animate("k", 1000, seen.append)
        pump(self.root, 0.05)
        self.a.set_enabled(False)
        self.assertEqual(seen[-1], 1.0)


class NavAnimationTests(unittest.TestCase):
    def test_indicator_lands_on_selected_button_with_and_without_animation(self) -> None:
        from app.application import PiezoApplication

        app = PiezoApplication()
        try:
            nav = app.navigation
            app.root.geometry("1200x800+0+0")
            pump(app.root, 0.3)
            for enabled in (True, False):
                app.animator.set_enabled(enabled)
                for name in ("awp1922", "home", "awp1221"):
                    nav.show(name)
                    pump(app.root, 0.4)
                    # sidebar tree: chips with modes mark their selected mode child
                    btn, bar = nav._tree_widget(nav._selected_nav_name()), nav._nav_indicator
                    if name == "awp1221":
                        self.assertIn(":", nav._selected_nav_name())
                    self.assertTrue(bar.winfo_ismapped())
                    bx, by = nav._sidebar_xy(btn)
                    center_btn = by + btn.winfo_height() / 2
                    center_bar = bar.winfo_y() + bar.winfo_height() / 2
                    self.assertLessEqual(abs(center_btn - center_bar), 1.0)
                    self.assertLess(bar.winfo_x() + bar.winfo_width(), bx)
            # hover bar fades in and is removed after leave
            app.animator.set_enabled(True)
            nav._nav_hover("awp1018", True)
            pump(app.root, 0.3)
            self.assertTrue(nav._nav_hover_bar.winfo_ismapped())
            nav._nav_hover("awp1018", False)
            pump(app.root, 0.3)
            self.assertFalse(nav._nav_hover_bar.winfo_ismapped())
        finally:
            app.close()

    def test_sidebar_accordion_only_current_chip_expanded(self) -> None:
        from app.application import PiezoApplication

        app = PiezoApplication()
        try:
            nav = app.navigation
            app.root.geometry("1200x800+0+0")
            pump(app.root, 0.3)
            for enabled in (True, False):
                app.animator.set_enabled(enabled)
                for name in ("awp1018", "home", "awp1221", "awp1922", "awp1921_1923", "settings"):
                    if name not in nav.pages:
                        continue
                    nav.show(name)
                    pump(app.root, 0.45)
                    for chip, clip in nav._tree_clips.items():
                        if chip == name:
                            self.assertTrue(clip.winfo_ismapped(), chip)
                            self.assertGreaterEqual(clip.winfo_height(), nav._tree_frames[chip].winfo_reqheight() - 1)
                        else:
                            self.assertFalse(clip.winfo_ismapped(), (name, chip))
            # clicking a child of a collapsed chip navigates and expands it
            app.animator.set_enabled(True)
            nav.show("home")
            pump(app.root, 0.4)
            nav._on_tree_child("awp1018", "awp1018_diff_mode")
            pump(app.root, 0.5)
            self.assertEqual(app.state.current_page, "awp1018")
            self.assertEqual(nav._tree_open, "awp1018")
        finally:
            app.close()

    def test_combobox_first_open_shows_final_view_and_fixed_list(self) -> None:
        import tkinter as tk
        from tkinter import ttk
        from app.application import PiezoApplication

        app = PiezoApplication()
        try:
            r = app.root
            win = tk.Toplevel(r)
            pump(r, 0.2)
            for enabled in (True, False):
                app.animator.set_enabled(enabled)
                for n, idx, top in ((4, 3, 0), (8, 3, 0), (20, 3, 0), (20, 15, 10), (30, 29, 20)):
                    cb = ttk.Combobox(win, values=[f"v{i}" for i in range(n)], state="readonly")
                    cb.pack()
                    cb.current(idx)
                    pump(r, 0.05)
                    r.tk.call("ttk::combobox::Post", str(cb))
                    lb = f"{cb}.popdown.f.l"
                    heights, tops = set(), set()
                    for _ in range(12):
                        pump(r, 0.02)
                        heights.add(int(r.tk.call("winfo", "height", lb)))
                        tops.add(round(float(r.tk.call(lb, "yview")[0]) * n))
                    self.assertEqual(tops, {top}, (n, idx))
                    self.assertEqual(len(heights), 1, (n, idx, heights))
                    r.tk.call("ttk::combobox::Unpost", str(cb))
                    pump(r, 0.05)
                    self.assertEqual(str(r.tk.call("winfo", "manager", f"{cb}.popdown.f")), "grid")
                    cb.destroy()
        finally:
            app.close()

    def test_settings_toggle_disables_animator(self) -> None:
        from app.application import PiezoApplication

        app = PiezoApplication()
        try:
            page = app.navigation.pages["settings"]
            page.animations_var.set(False)
            page._apply_animations_toggle()
            self.assertFalse(app.animator.enabled)
            self.assertFalse(app.state.extra["animations_enabled"])
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main()
