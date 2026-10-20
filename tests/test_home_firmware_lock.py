from __future__ import annotations

import gc
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest import mock

from app import application as appmod
from app.application import ApplicationContext, HomePage
from app.state import AppState
from core.serial_manager import SerialPortInfo, SerialResponse
from styles.theme import get_colors
from tests.test_home_port_name import FakeSerial


class ConnectableSerial(FakeSerial):
    def __init__(self, ports) -> None:
        super().__init__(ports)
        self.connect_results: list[SerialResponse] = []
        self.connect_calls: list[str] = []
        self.logs: list[str] = []

    def connect(self, port, baudrate=115200):
        self.connect_calls.append(port)
        return self.connect_results.pop(0) if self.connect_results else SerialResponse(False, error="no board")

    def disconnect(self, port=None):
        pass

    def emit_log(self, message):
        self.logs.append(message)


class HomeFirmwareLockTests(unittest.TestCase):
    def setUp(self) -> None:
        # Collect Tk garbage left by earlier tests on the main thread, so a GC
        # pass triggered inside a background thread (e.g. port scan) cannot
        # finalize tkinter Variables there (Windows: Tcl_AsyncDelete crash).
        gc.collect()
        self._threads_before = set(threading.enumerate())
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self._final_cleanup)
        self.addCleanup(self.root.destroy)
        self.serial = ConnectableSerial([SerialPortInfo(device="COM3", name="USB-SERIAL CH340")])
        ctx = ApplicationContext(state=AppState(), serial=self.serial, colors=get_colors("light"), app=SimpleNamespace())
        self.page = HomePage(self.root, ctx)
        self.addCleanup(self.page.destroy)

    def _final_cleanup(self) -> None:
        for thread in set(threading.enumerate()) - self._threads_before:
            if thread is not threading.main_thread():
                thread.join(timeout=2.0)
        self.page = None
        self.root = None
        gc.collect()

    def pump(self, seconds: float, until=lambda: False) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not until():
            self.root.update()
            time.sleep(0.005)

    def test_flash_button_present_and_localized(self) -> None:
        self.page.refresh_language()
        self.assertIn(self.page.flash_btn.cget("text"), ("烧录固件…", "Flash Firmware…"))

    def test_lock_blocks_connect_and_disables_buttons(self) -> None:
        self.assertTrue(self.page.firmware_lock("COM3"))
        self.assertFalse(self.page.firmware_lock("COM3"))  # second flash refused
        self.assertTrue(self.page.connect_btn.instate(["disabled"]))
        self.page.port_var.set("COM3")
        with mock.patch.object(appmod.messagebox, "showwarning") as warn:
            self.page.toggle_connection()
        warn.assert_called_once()
        self.assertIsNone(getattr(self.page, "_connect_attempt", None))
        self.page.firmware_unlock("COM3")
        self.assertFalse(self.page.connect_btn.instate(["disabled"]))

    def test_restore_retries_once_quietly_then_reports(self) -> None:
        with mock.patch("core.firmware_flasher.RECONNECT_DELAY_MS", 10), \
             mock.patch.object(appmod.messagebox, "showerror") as err:
            self.page.firmware_restore_port("COM3")
            self.pump(3, until=lambda: err.called)
        self.assertEqual(self.serial.connect_calls, ["COM3", "COM3"])
        err.assert_called_once()  # only after the retry failed
        self.assertTrue(any("[flash]" in line for line in self.serial.logs))


if __name__ == "__main__":
    unittest.main()
