from __future__ import annotations

import unittest
import threading
import time
from types import SimpleNamespace

import tkinter as tk

from app.application import ApplicationContext, HomePage
from app.state import AppState
from core.serial_manager import SerialPortInfo
from styles.theme import get_colors


class FakeSerial:
    def __init__(self, ports: list[SerialPortInfo]) -> None:
        self.ports = list(ports)
        self.is_available = True
        self.is_connected = False

    def list_port_info(self) -> list[SerialPortInfo]:
        return list(self.ports)

    def is_port_connected(self, port: str | None) -> bool:
        return False

    def add_connection_listener(self, _listener) -> None:
        return None

    def remove_connection_listener(self, _listener) -> None:
        return None


class HomePortNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.serial = FakeSerial(
            [
                SerialPortInfo(device="COM3", name="Arduino Uno"),
                SerialPortInfo(device="COM5", name="USB-SERIAL CH340"),
            ]
        )
        ctx = ApplicationContext(
            state=AppState(),
            serial=self.serial,
            colors=get_colors("light"),
            app=SimpleNamespace(),
        )
        self.page = HomePage(self.root, ctx)
        self.addCleanup(self.page.destroy)
        self._wait_for_scan()

    def _wait_for_scan(self) -> None:
        deadline = time.monotonic() + 2
        while getattr(self.page, "_port_scan_results", None) is not None:
            self.root.update()
            if time.monotonic() >= deadline:
                self.fail("port scan did not finish")
            time.sleep(0.005)

    def test_selecting_another_port_updates_current_serial_name(self) -> None:
        self.assertEqual(self.page.port_var.get(), "COM3")
        self.assertEqual(self.page.current_port_name_var.get(), "Arduino Uno (COM3)")

        self.page.port_var.set("COM5")
        self.page._on_status_port_changed()

        self.assertEqual(self.page.current_port_name_var.get(), "USB-SERIAL CH340 (COM5)")

    def test_i2c_speed_combobox_keeps_default_and_offers_all_steps(self) -> None:
        self.assertEqual(self.page.i2c_speed_var.get(), "400000")
        self.assertEqual(list(self.page.i2c_speed_combo.cget("values")),
                         ["100000", "400000", "500000", "615385", "666667",
                          "727273", "800000"])
        self.assertEqual(str(self.page.i2c_speed_combo.cget("state")), "readonly")

    def test_port_list_poll_picks_up_new_serial_name(self) -> None:
        self.assertEqual(self.page.current_port_name_var.get(), "Arduino Uno (COM3)")

        self.serial.ports = [
            SerialPortInfo(device="COM3", name="AnalogWin Piezo REV04"),
            SerialPortInfo(device="COM5", name="USB-SERIAL CH340"),
        ]
        self.page._poll_port_list()
        self.assertFalse(self.page.refresh_btn.instate(["disabled"]))
        self._wait_for_scan()

        self.assertEqual(self.page.current_port_name_var.get(), "AnalogWin Piezo REV04 (COM3)")
        self.assertEqual(list(self.page.port_combo.cget("values")), ["COM3", "COM5"])

    def test_slow_port_enumeration_leaves_tk_responsive(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def slow_scan(**_kwargs):
            calls.append(threading.get_ident())
            entered.set()
            release.wait(0.5)
            return [SerialPortInfo(device="COM9", name="Slow driver")]

        self.serial.list_port_info = slow_scan
        try:
            start = time.perf_counter()
            self.page.refresh_ports()
            self.assertLess(time.perf_counter() - start, 0.1)
            self.assertTrue(entered.wait(0.5))
            self.page.refresh_ports()  # A repeated click must reuse this scan.
            ticks = []
            self.root.after(0, lambda: ticks.append(True))
            self.root.update()
            self.assertEqual(ticks, [True])
            self.assertEqual(len(calls), 1)
            self.assertNotEqual(calls[0], threading.get_ident())
            self.assertFalse(self.page.refresh_btn.instate(["disabled"]))
        finally:
            release.set()
        self._wait_for_scan()
        self.assertEqual(self.page.port_var.get(), "COM9")
        self.assertFalse(self.page.refresh_btn.instate(["disabled"]))


if __name__ == "__main__":
    unittest.main()
