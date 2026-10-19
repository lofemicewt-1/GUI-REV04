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
        import gc
        gc.collect()
        self.app = PiezoApplication()
        self.root = self.app.root
        self.root.geometry("1024x640+0+0")
        pump(self.root, lambda: False, timeout=0.3)

    def tearDown(self) -> None:
        self.app.close()

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
            self.assertEqual(home.connect_btn.cget("text"), home.tr("cancel_connect"))
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
            self.assertEqual(home.connect_btn.cget("text"), home.tr("connect"))


class BluetoothPortTests(unittest.TestCase):
    def test_detection(self) -> None:
        from core.serial_manager import is_bluetooth_port_info as bt

        self.assertTrue(bt("COM4", "\u84dd\u7259\u94fe\u63a5\u4e0a\u7684\u6807\u51c6\u4e32\u884c (COM4)"))
        self.assertTrue(bt("COM5", "Standard Serial over Bluetooth link (COM5)"))
        self.assertTrue(bt("COM6", "x", "BTHENUM\\{00001101-0000}_LOCALMFG&0000"))
        self.assertTrue(bt("/dev/rfcomm0"))
        self.assertFalse(bt("COM3", "USB-SERIAL CH340 (COM3)", "USB VID:PID=1A86:7523"))
        self.assertFalse(bt("/dev/ttyUSB0", "USB Serial"))

    def _fake_ports(self):
        from types import SimpleNamespace as N

        return [
            N(device="COM4", description="\u84dd\u7259\u94fe\u63a5\u4e0a\u7684\u6807\u51c6\u4e32\u884c (COM4)", hwid="BTHENUM\\X", manufacturer="Microsoft", name="COM4"),
            N(device="COM7", description="USB-SERIAL CH340 (COM7)", hwid="USB VID:PID=1A86:7523", manufacturer="wch.cn", name="COM7"),
        ]

    def test_listing_flags_and_manager_refuses_without_opening(self) -> None:
        import core.serial_manager as sm

        with mock.patch.object(sm.serial.tools.list_ports, "comports", return_value=self._fake_ports()):
            info = sm.SerialManager().list_port_info()
        self.assertEqual({i.device: i.bluetooth for i in info}, {"COM4": True, "COM7": False})
        with mock.patch.object(sm.serial, "Serial") as opener:
            resp = sm.SerialManager().connect("COM4")
        self.assertFalse(resp.ok)
        self.assertTrue(resp.error.startswith(sm.BLUETOOTH_PORT_ERROR))
        opener.assert_not_called()

    def test_gui_prefers_non_bluetooth_and_refuses_bluetooth(self) -> None:
        import core.serial_manager as sm

        app = PiezoApplication()
        try:
            home = app.navigation.pages["home"]
            with mock.patch.object(sm.serial.tools.list_ports, "comports", return_value=self._fake_ports()):
                home.port_var.set("")
                home._apply_port_info(app.serial.list_port_info())
            self.assertEqual(home.port_var.get(), "COM7")
            home.port_var.set("COM4")
            with mock.patch.object(app.serial, "connect") as connect, mock.patch(
                "app.application.messagebox.showwarning"
            ) as warn:
                home.toggle_connection()
            connect.assert_not_called()
            self.assertIn("COM4", warn.call_args[0][1])
        finally:
            app.close()


class CancelConnectTests(unittest.TestCase):
    def setUp(self) -> None:
        import gc
        gc.collect()
        self.app = PiezoApplication()
        self.home = self.app.navigation.pages["home"]
        self.home.port_var.set("COM9")

    def tearDown(self) -> None:
        self.app.close()

    def test_cancel_returns_ui_immediately_and_late_success_is_closed(self) -> None:
        release = threading.Event()
        disconnected: list[str] = []

        def blocked_connect(port, baudrate=115200):
            release.wait(5)  # pyserial blocked inside open()
            return SerialResponse(True, data="READY REV04")

        with mock.patch.object(self.app.serial, "connect", side_effect=blocked_connect), mock.patch.object(
            self.app.serial, "disconnect", side_effect=lambda port=None: disconnected.append(port)
        ), mock.patch("app.application.messagebox.showerror") as err:
            self.home.toggle_connection()
            self.assertEqual(self.home.connect_btn.cget("text"), self.home.tr("cancel_connect"))
            t0 = time.perf_counter()
            self.home.toggle_connection()  # the button now cancels
            self.assertLess(time.perf_counter() - t0, 0.1)
            self.assertEqual(self.home.connect_btn.cget("text"), self.home.tr("connect"))
            self.assertIn(self.home.tr("connect_cancelled"), self.home.status_label.cget("text"))
            release.set()
            self.assertTrue(pump(self.app.root, lambda: disconnected == ["COM9"], timeout=3))
            pump(self.app.root, lambda: False, timeout=0.2)
            err.assert_not_called()
            self.assertFalse(self.app.state.serial_connected)

    def test_new_attempt_waits_for_cancelled_cleanup(self) -> None:
        release = threading.Event()
        order: list[str] = []

        def connect(port, baudrate=115200):
            if not order:
                order.append("first-start")
                release.wait(5)
                order.append("first-end")
                return SerialResponse(True, data="x")
            order.append("second")
            return SerialResponse(False, error="boom")

        with mock.patch.object(self.app.serial, "connect", side_effect=connect), mock.patch.object(
            self.app.serial, "disconnect", side_effect=lambda port=None: order.append("close-first")
        ), mock.patch("app.application.messagebox.showerror") as err:
            self.home.toggle_connection()
            pump(self.app.root, lambda: order == ["first-start"], timeout=2)
            self.home.cancel_connection()
            self.home.toggle_connection()  # new attempt while the first is still blocked
            self.app.root.after(200, release.set)
            self.assertTrue(pump(self.app.root, lambda: err.called, timeout=5))
            self.assertEqual(order, ["first-start", "first-end", "close-first", "second"])
            self.assertEqual(err.call_args[0][1], "boom")


if __name__ == "__main__":
    unittest.main()
