"""Cancel/close must never block the Tk thread, whatever pyserial is doing."""
from __future__ import annotations

import threading
import os
import time
import unittest
from unittest import mock

import core.serial_manager as sm
from app.application import PiezoApplication


class _FakeCH340:
    """Port that opens (optionally slowly), never sends READY, and whose
    write/close block - like a Nano resetting behind a CH340 or a stuck driver."""

    open_s = 0.0
    write_s = 2.0
    close_s = 2.0
    instances: list["_FakeCH340"] = []

    def __init__(self, *args, **kwargs) -> None:
        time.sleep(self.open_s)
        self.timeout = kwargs.get("timeout", 0.8)
        self.write_timeout = kwargs.get("write_timeout", 0.8)
        self.is_open = True
        self.in_waiting = 0
        self.closed_on_main: bool | None = None
        _FakeCH340.instances.append(self)

    def read(self, n: int = 1) -> bytes:
        time.sleep(min(0.2, self.timeout or 0.01))
        return b""

    def write(self, data: bytes) -> int:
        time.sleep(self.write_s)
        return len(data)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        pass

    def reset_output_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.closed_on_main = threading.current_thread() is threading.main_thread()
        time.sleep(self.close_s)
        self.is_open = False


class BlockingCancelTests(unittest.TestCase):
    def _run(self, open_s: float) -> None:
        # Collect Tk objects (e.g. PhotoImage) left by earlier tests' apps now,
        # on the main thread; otherwise a GC pass inside the connect worker
        # runs their __del__ against a dead interpreter and blocks it.
        import gc
        gc.collect()
        _FakeCH340.instances = []
        _FakeCH340.open_s = open_s
        # Measures connect/cancel stalls only; background page builds are
        # covered elsewhere and would add their own (expected) slices.
        with mock.patch.dict(os.environ, {"PIEZO_BG_BUILD": "0"}):
            app = PiezoApplication()
        home = app.navigation.pages["home"]
        root = app.root
        home.port_var.set("COM14")
        gaps: list[float] = []
        out: dict[str, object] = {}
        last = [time.perf_counter()]

        def beat() -> None:
            now = time.perf_counter()
            gaps.append(now - last[0])
            last[0] = now
            root.after(10, beat)

        def step_connect() -> None:
            t0 = time.perf_counter()
            home.toggle_connection()
            out["connect_s"] = time.perf_counter() - t0
            out["text_after_connect"] = home.connect_btn.cget("text")
            root.after(600, step_cancel)

        def step_cancel() -> None:
            out["text_before_cancel"] = home.connect_btn.cget("text")
            t0 = time.perf_counter()
            home.toggle_connection()  # = cancel
            out["cancel_s"] = time.perf_counter() - t0
            out["text_after_cancel"] = home.connect_btn.cget("text")
            out["status_after_cancel"] = home.status_label.cget("text")
            root.after(int((open_s + 5.0) * 1000), step_check)

        def step_check() -> None:
            out["text_final"] = home.connect_btn.cget("text")
            out["connected"] = app.serial.is_port_connected("COM14")
            out["closes"] = [(i.closed_on_main, i.is_open) for i in _FakeCH340.instances]
            root.quit()

        with mock.patch.object(sm.serial, "Serial", _FakeCH340):
            root.after(200, step_connect)
            root.after(200, beat)
            root.mainloop()
            t0 = time.perf_counter()
            app.close()
            close_s = time.perf_counter() - t0

        self.assertLess(out["connect_s"], 0.1)
        self.assertEqual(out["text_after_connect"], home.tr("cancel_connect"))
        self.assertEqual(out["text_before_cancel"], home.tr("cancel_connect"))
        self.assertLess(out["cancel_s"], 0.1)
        self.assertEqual(out["text_after_cancel"], home.tr("connect"))
        self.assertIn(home.tr("connect_cancelled"), out["status_after_cancel"])
        self.assertEqual(out["text_final"], home.tr("connect"))  # never flipped to disconnect
        self.assertFalse(out["connected"])
        self.assertTrue(out["closes"])
        self.assertTrue(all(c == (False, False) for c in out["closes"]), out["closes"])
        self.assertLess(max(gaps[1:]), 0.15, f"UI stalled {max(gaps[1:]):.3f}s")
        self.assertLess(close_s, 1.0)  # ~0.34 s is plain widget teardown

    def test_cancel_during_handshake(self) -> None:
        self._run(open_s=0.0)

    def test_cancel_while_open_blocks(self) -> None:
        self._run(open_s=2.0)

    def test_close_window_during_connect_is_immediate(self) -> None:
        _FakeCH340.open_s = 0.0
        app = PiezoApplication()
        home = app.navigation.pages["home"]
        home.port_var.set("COM14")
        out: dict[str, float] = {}

        def close() -> None:
            t0 = time.perf_counter()
            app.close()
            out["close_s"] = time.perf_counter() - t0

        with mock.patch.object(sm.serial, "Serial", _FakeCH340):
            app.root.after(100, home.toggle_connection)
            app.root.after(700, close)
            app.root.mainloop()
        self.assertLess(out["close_s"], 1.0)


if __name__ == "__main__":
    unittest.main()
