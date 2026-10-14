from __future__ import annotations

import threading
import time
import tkinter as tk
import unittest

from core import ui_dispatch
from core.ui_dispatch import UiDispatcher, post_to_ui


def _pump(root: tk.Misc, until, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if until():
            return True
        time.sleep(0.002)
    return until()


class UiDispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.d = UiDispatcher(self.root).start()

    def tearDown(self) -> None:
        self.d.close()
        self.root.destroy()

    def test_fifo_across_threads_and_main_thread_execution(self) -> None:
        seen: list[tuple[int, int, bool]] = []

        def worker(tid: int) -> None:
            for i in range(200):
                post_to_ui(self.root, lambda t=tid, k=i: seen.append((t, k, threading.current_thread() is threading.main_thread())))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(_pump(self.root, lambda: len(seen) == 800))
        self.assertTrue(all(on_main for *_x, on_main in seen))
        for tid in range(4):  # per-producer FIFO
            self.assertEqual([k for t, k, _m in seen if t == tid], list(range(200)))

    def test_global_fifo_single_producer(self) -> None:
        seen: list[int] = []
        for i in range(5000):
            self.d.post(seen.append, i)
        self.assertTrue(_pump(self.root, lambda: len(seen) == 5000))
        self.assertEqual(seen, list(range(5000)))

    def test_tick_is_bounded_and_leftover_continues(self) -> None:
        seen: list[int] = []
        for i in range(5000):
            self.d.post(seen.append, i)
        ran = self.d.drain(max_items=100)
        self.assertEqual(ran, 100)
        self.assertEqual(self.d.pending(), 4900)
        self.assertTrue(_pump(self.root, lambda: len(seen) == 5000))

    def test_time_budget_limits_one_tick(self) -> None:
        for _ in range(500):
            self.d.post(time.sleep, 0.001)
        t0 = time.perf_counter()
        self.d.drain()
        self.assertLess(time.perf_counter() - t0, 0.2)
        self.assertGreater(self.d.pending(), 0)

    def test_exception_is_reported_and_queue_continues(self) -> None:
        errors: list[BaseException] = []
        self.root.report_callback_exception = lambda typ, exc, tb: errors.append(exc)
        seen: list[int] = []
        self.d.post(lambda: 1 / 0)
        self.d.post(seen.append, 1)
        self.assertTrue(_pump(self.root, lambda: seen == [1]))
        self.assertEqual(len(errors), 1)

    def test_destroyed_widget_callback_is_ignored(self) -> None:
        label = tk.Label(self.root)
        label.destroy()
        seen: list[int] = []
        self.d.post(label.configure, {"text": "x"})
        self.d.post(seen.append, 1)
        self.assertTrue(_pump(self.root, lambda: seen == [1]))

    def test_post_after_close_is_dropped_without_tk_calls(self) -> None:
        self.d.close()
        seen: list[int] = []
        self.assertFalse(self.d.post(seen.append, 1))
        self.assertIsNone(ui_dispatch.active_dispatcher())


class FallbackTests(unittest.TestCase):
    def test_without_dispatcher_falls_back_to_after(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            self.assertIsNone(ui_dispatch.active_dispatcher())
            seen: list[int] = []
            post_to_ui(root, seen.append, 7)
            self.assertTrue(_pump(root, lambda: seen == [7]))
        finally:
            root.destroy()


class SerialLogBatchingTests(unittest.TestCase):
    def test_worker_lines_batched_in_order_and_clear_respected(self) -> None:
        from app.application import PiezoApplication

        app = PiezoApplication()
        try:
            page = app.navigation.pages["awp1922"]
            text = page._serial_log_text
            page.clear_serial_log_view()

            def worker() -> None:
                for i in range(300):
                    page.append_serial_log(f"A{i}")
                page.clear_serial_log_view()
                for i in range(5):
                    page.append_serial_log(f"B{i}")

            t = threading.Thread(target=worker)
            t.start()
            t.join()
            self.assertTrue(_pump(app.root, lambda: app.ui_dispatcher.pending() == 0))
            app.root.update()
            self.assertEqual(text.get("1.0", "end-1c").split(), [f"B{i}" for i in range(5)])
            page.append_serial_log("C0")  # main thread, appended after
            self.assertEqual(text.get("1.0", "end-1c").split()[-1], "C0")
        finally:
            app.close()


class FakeSerialIntegrationTests(unittest.TestCase):
    """Real application + fake serial: worker results arrive in order on the main thread."""

    def test_awp1922_async_writes_finish_in_order_with_button_state(self) -> None:
        from app.application import PiezoApplication
        from core.base_tool import SerialResponse

        app = PiezoApplication()
        try:
            page = app.navigation.pages["awp1922"]
            order: list[tuple[str, bool]] = []
            gate = threading.Lock()

            real_serial = page.ctx.serial

            class FakeSerial:
                is_connected = True

                def __getattr__(self, name):  # everything else: real manager
                    return getattr(real_serial, name)

                def write_register_value(self, dev, reg, value, bits=8):
                    with gate:  # serialise like the real command lock
                        time.sleep(0.01)
                    return SerialResponse(True, data=f"OK WREGW {dev:02X} {reg:02X} 8 1")

                def read_register_value(self, dev, reg, bits=8):
                    return SerialResponse(True, data=f"RDATAW {dev:02X} {reg:02X} 8 1 80")

                def extract_rdataw_value(self, data, **_k):
                    return int(data.split()[-1], 16)

                def emit_log(self, _m):
                    pass

            page.ctx.serial = FakeSerial()
            page._ensure_connected = lambda: True
            original = page._set_button_selected

            def record(key, selected):
                order.append((key, selected, threading.current_thread() is threading.main_thread()))
                original(key, selected)

            page._set_button_selected = record
            page.start_play()
            page.stop_play()
            page.start_play()
            self.assertTrue(_pump(app.root, lambda: len(order) == 3, timeout=5))
            self.assertTrue(all(m for *_x, m in order))
            self.assertEqual(sorted(s for _k, s, _m in order), [False, True, True])
        finally:
            page.ctx.serial = real_serial
            app.close()
        self.assertIsNone(ui_dispatch.active_dispatcher())


if __name__ == "__main__":
    unittest.main()
