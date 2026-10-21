"""Cancel/close must never block the Tk thread, whatever pyserial is doing."""
from __future__ import annotations

import faulthandler
import os
import threading
import time
import traceback
import tkinter as tk
import unittest
from unittest import mock

import core.serial_manager as sm
from app.application import PiezoApplication
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test


def _new_application(test: unittest.TestCase) -> PiezoApplication:
    prepare_tk_test(test)
    with mock.patch.dict(os.environ, {"PIEZO_BG_BUILD": "0"}):
        app = PiezoApplication()
    attach_tk_cleanup(test, app.root)

    def close_app() -> None:
        try:
            if app.root.winfo_exists():
                app.close()
        except tk.TclError:
            pass

    # Registered after attach_tk_cleanup so shutdown happens before root
    # teardown and worker joins. TestCase cleanups run in LIFO order.
    test.addCleanup(close_app)
    return app


def _capture_callback_exceptions(root) -> tuple[list[str], dict[int, BaseException]]:
    errors: list[str] = []
    wrapped_exceptions: dict[int, BaseException] = {}

    def report_callback_exception(exc_type, exc_value, exc_tb) -> None:
        # Guarded callbacks already saved the original traceback before
        # re-raising; avoid reporting the same exception twice.
        if wrapped_exceptions.pop(id(exc_value), None) is not exc_value:
            errors.append("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))

    root.report_callback_exception = report_callback_exception
    return errors, wrapped_exceptions


def _guard_callback(callback, errors: list[str], wrapped_exceptions: dict[int, BaseException]):
    def run() -> None:
        try:
            callback()
        except BaseException as exc:
            wrapped_exceptions[id(exc)] = exc
            errors.append(traceback.format_exc())
            raise

    return run


def _assert_mainloop_health(
    test: unittest.TestCase, callback_errors: list[str], watchdog_recovery: list[bool]
) -> None:
    failures = []
    if callback_errors:
        failures.append("Tk callback exceptions:\n" + "\n".join(callback_errors))
    if watchdog_recovery:
        failures.append("Tk mainloop watchdog had to recover the test")
    if failures:
        test.fail("\n".join(failures))


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


def _reset_fake_ch340(*, open_s: float = 0.0) -> None:
    _FakeCH340.open_s = open_s
    _FakeCH340.write_s = 2.0
    _FakeCH340.close_s = 2.0
    _FakeCH340.instances = []


class BlockingCancelTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_fake_ch340()
        from app.application import LocalI2cBridge
        # A real asynchronous port scan must not replace this test's fake
        # selection before the simulated click. No live bridge/port probing.
        for patcher in (
            mock.patch.object(sm.serial, "Serial", _FakeCH340),
            mock.patch.object(sm.SerialManager, "list_port_info", return_value=[sm.SerialPortInfo("COM14", "Fake CH340")]),
            mock.patch("app.application.is_known_bluetooth_port", return_value=False),
            mock.patch.object(LocalI2cBridge, "start", lambda _self: None),
            mock.patch.object(LocalI2cBridge, "close", lambda _self: None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, open_s: float) -> None:
        _reset_fake_ch340(open_s=open_s)
        # Measures connect/cancel stalls only; background page builds are
        # covered elsewhere and would add their own (expected) slices.
        app = _new_application(self)
        home = app.navigation.pages["home"]
        root = app.root
        home.port_var.set("COM14")
        gaps: list[float] = []
        out: dict[str, object] = {}
        last = [time.perf_counter()]
        callback_errors, wrapped_exceptions = _capture_callback_exceptions(root)

        def schedule(delay_ms: int, callback) -> None:
            root.after(delay_ms, _guard_callback(callback, callback_errors, wrapped_exceptions))

        def beat() -> None:
            now = time.perf_counter()
            gaps.append(now - last[0])
            last[0] = now
            schedule(10, beat)

        def step_connect() -> None:
            home.port_var.set("COM14")
            t0 = time.perf_counter()
            home.toggle_connection()
            out["connect_s"] = time.perf_counter() - t0
            out["text_after_connect"] = home.connect_btn.cget("text")
            schedule(600, step_cancel)

        def step_cancel() -> None:
            out["text_before_cancel"] = home.connect_btn.cget("text")
            t0 = time.perf_counter()
            home.toggle_connection()  # = cancel
            out["cancel_s"] = time.perf_counter() - t0
            out["text_after_cancel"] = home.connect_btn.cget("text")
            out["status_after_cancel"] = home.status_label.cget("text")
            schedule(int((open_s + 5.0) * 1000), step_check)

        def step_check() -> None:
            out["text_final"] = home.connect_btn.cget("text")
            out["connected"] = app.serial.is_port_connected("COM14")
            out["closes"] = [(i.closed_on_main, i.is_open) for i in _FakeCH340.instances]
            root.quit()

        deadline_s = max(12.0, open_s + 12.0)
        watchdog_recovery: list[bool] = []

        def watchdog() -> None:
            watchdog_recovery.append(True)
            try:
                root.quit()
            except tk.TclError:
                pass

        schedule(int(deadline_s * 1000), watchdog)
        faulthandler.dump_traceback_later(10.0, repeat=True)
        try:
            schedule(200, step_connect)
            schedule(200, beat)
            root.mainloop()
        finally:
            faulthandler.cancel_dump_traceback_later()
        t0 = time.perf_counter()
        app.close()
        close_s = time.perf_counter() - t0

        _assert_mainloop_health(self, callback_errors, watchdog_recovery)

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
        _reset_fake_ch340()
        app = _new_application(self)
        home = app.navigation.pages["home"]
        home.port_var.set("COM14")
        out: dict[str, float] = {}
        root = app.root
        callback_errors, wrapped_exceptions = _capture_callback_exceptions(root)
        watchdog_recovery: list[bool] = []

        def close() -> None:
            t0 = time.perf_counter()
            app.close()
            out["close_s"] = time.perf_counter() - t0

        def hard_deadline() -> None:
            watchdog_recovery.append(True)
            try:
                root.quit()
            except tk.TclError:
                pass

        faulthandler.dump_traceback_later(10.0, repeat=True)
        try:
            root.after(100, _guard_callback(home.toggle_connection, callback_errors, wrapped_exceptions))
            root.after(700, _guard_callback(close, callback_errors, wrapped_exceptions))
            root.after(12_000, _guard_callback(hard_deadline, callback_errors, wrapped_exceptions))
            root.mainloop()
        finally:
            faulthandler.cancel_dump_traceback_later()
        _assert_mainloop_health(self, callback_errors, watchdog_recovery)
        self.assertLess(out["close_s"], 1.0)


class _QuietPort:
    """Opens immediately and never sends READY. read() honors the short timeout."""

    def __init__(self, *args, **kwargs) -> None:
        self.timeout = kwargs.get("timeout", 0.8)
        self.write_timeout = kwargs.get("write_timeout", 0.8)
        self.is_open = True
        self.in_waiting = 0

    def read(self, n: int = 1) -> bytes:
        time.sleep(min(0.2, self.timeout or 0.01))
        return b""

    def write(self, data: bytes) -> int:
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False


class NoBoardConnectTests(unittest.TestCase):
    """A missing board used to block the caller for CONNECT_READY_TIMEOUT (30s)."""

    def test_cancelled_handshake_returns_well_under_ready_timeout(self) -> None:
        self.assertGreaterEqual(sm.SerialManager.CONNECT_READY_TIMEOUT, 30.0)
        manager = sm.SerialManager(timeout=0.8)
        done = threading.Event()
        out: dict[str, object] = {}

        def run() -> None:
            with mock.patch.object(sm.serial, "Serial", _QuietPort):
                out["resp"] = manager.connect("COM21")
            done.set()

        worker = threading.Thread(target=run, name="no-board-connect")
        started = time.perf_counter()
        worker.start()
        deadline = time.perf_counter() + 2.0
        while not manager.is_connecting and time.perf_counter() < deadline:
            time.sleep(0.01)
        self.assertTrue(manager.is_connecting)
        t_cancel = time.perf_counter()
        manager.cancel_connect()
        self.assertLess(time.perf_counter() - t_cancel, 0.05)
        self.assertTrue(done.wait(2.0), "cancelled no-board connect blocked")
        self.assertLess(time.perf_counter() - started, 5.0)
        resp = out["resp"]
        self.assertFalse(resp.ok)
        self.assertEqual(resp.error, sm.CONNECT_CANCELLED_ERROR)
        self.assertFalse(manager.is_connected)
        worker.join(1.0)

    def test_ui_caller_returns_before_ready_timeout(self) -> None:
        self.assertGreaterEqual(sm.SerialManager.CONNECT_READY_TIMEOUT, 30.0)
        saved = (_FakeCH340.open_s, _FakeCH340.write_s, _FakeCH340.close_s, _FakeCH340.instances)
        _FakeCH340.open_s = 0.0
        _FakeCH340.write_s = 0.0
        _FakeCH340.close_s = 0.0
        _FakeCH340.instances = []
        try:
            serial_patcher = mock.patch.object(sm.serial, "Serial", _FakeCH340)
            serial_patcher.start()
            self.addCleanup(serial_patcher.stop)
            app = _new_application(self)
            home = app.navigation.pages["home"]
            home.port_var.set("COM21")
            t0 = time.perf_counter()
            home.toggle_connection()
            elapsed = time.perf_counter() - t0
            self.assertLess(elapsed, 0.25)
            self.assertIsNotNone(home._connect_attempt)
            self.assertEqual(home.connect_btn.cget("text"), home.tr("cancel_connect"))
            self.assertFalse(app.serial.is_port_connected("COM21"))
            t1 = time.perf_counter()
            home.cancel_connection()
            self.assertLess(time.perf_counter() - t1, 0.1)
            t2 = time.perf_counter()
            app.close()
            self.assertLess(time.perf_counter() - t2, 1.0)
        finally:
            _FakeCH340.open_s, _FakeCH340.write_s, _FakeCH340.close_s, _FakeCH340.instances = saved


if __name__ == "__main__":
    unittest.main()
