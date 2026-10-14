from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from app.application import PiezoApplication
from core.base_tool import SerialResponse


def pump(root, until, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if until():
            return True
        time.sleep(0.005)
    return until()


class MaximizeRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = PiezoApplication()
        self.root = self.app.root
        self.root.geometry("1024x640+0+0")
        pump(self.root, lambda: False, timeout=0.3)

    def tearDown(self) -> None:
        self.app.close()

    def test_hidden_page_has_current_size_before_on_show(self) -> None:
        nav = self.app.navigation
        for name in nav.pages:  # lay every page out at the small size
            nav.show(name)
            self.root.update()
        nav.show("home")
        self.root.update()
        self.root.geometry("2200x1300+0+0")
        pump(self.root, lambda: False, timeout=0.3)
        for name, page in nav.pages.items():
            if name == "home":
                continue
            seen: list[int] = []
            original = getattr(page, "on_show", None)

            def spy(original=original, page=page) -> None:
                seen.append(page.winfo_width())
                if original is not None:
                    original()

            page.on_show = spy
            nav.show(name)
            self.root.update()
            final = page.winfo_width()
            self.assertEqual(seen, [final], name)
            self.assertGreater(final, 1500, name)

    def test_connect_runs_off_ui_thread_and_reports_write_timeout(self) -> None:
        home = self.app.navigation.pages["home"]
        home.port_var.set("COM4")
        calls: list[bool] = []

        def slow_connect(port, baudrate=115200):
            calls.append(threading.current_thread() is threading.main_thread())
            time.sleep(1.0)  # e.g. Bluetooth SPP open + handshake write
            return SerialResponse(False, error="Write timeout")

        with mock.patch.object(self.app.serial, "connect", side_effect=slow_connect), mock.patch(
            "app.application.messagebox.showerror"
        ) as showerror:
            t0 = time.perf_counter()
            home.toggle_connection()
            self.assertLess(time.perf_counter() - t0, 0.2)
            self.assertTrue(home.connect_btn.instate(["disabled"]))
            home.toggle_connection()  # ignored while in flight
            gaps: list[float] = []
            last = [time.perf_counter()]

            def beat():
                now = time.perf_counter()
                gaps.append(now - last[0])
                last[0] = now

            self.assertTrue(
                pump(self.root, lambda: (beat(), showerror.called)[1], timeout=5)
            )
            self.assertEqual(calls, [False])
            self.assertLess(max(gaps), 0.2)  # UI kept running during the 1 s open
            self.assertEqual(showerror.call_args[0][1], "Write timeout")
            self.assertFalse(home.connect_btn.instate(["disabled"]))


if __name__ == "__main__":
    unittest.main()
