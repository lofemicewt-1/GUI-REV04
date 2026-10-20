from __future__ import annotations

from contextlib import contextmanager
import threading
import time
import unittest

from core.base_tool import SerialResponse
from tests.validate_responsive_layout import mocked_application


class SlowManager:
    def __init__(self, delay=.025):
        self.delay = delay
        self.is_connected = True
        self.connection_generation = 1
        self.board_ready = False
        self.board_identity = "offline fake"
        self.log_history = []
        self.calls = []
        self.registers = {0x06: 0x3FFF, 0x22: 0xF800, 0x34: 0xABCD}
        self.lock = threading.RLock()
        self.transaction_owner = None
        self.read_hook = None
        self.write_hook = None
        self.fail_reads = set()
        self.invalid_reads = set()

    @contextmanager
    def snapshot_transaction(self):
        with self.lock:
            previous = self.transaction_owner
            self.transaction_owner = threading.get_ident()
            try:
                yield
            finally:
                self.transaction_owner = previous

    def owns_snapshot_transaction(self):
        return self.transaction_owner == threading.get_ident()

    def read_register_value(self, address, register, *, bits):
        self.calls.append(("read", address, register, threading.get_ident()))
        time.sleep(self.delay)
        if self.read_hook is not None:
            self.read_hook(register)
        if register in self.fail_reads:
            return SerialResponse(False, error="fake timeout")
        return SerialResponse(True, data="invalid" if register in self.invalid_reads else f"{self.registers.get(register, 0):X}")

    def write_register_value(self, address, register, value, *, bits):
        self.calls.append(("write", address, register, threading.get_ident(), value))
        time.sleep(self.delay)
        if self.write_hook is not None:
            self.write_hook(register)
        self.registers[register] = value
        return SerialResponse(True, data="OK")

    def write_register_values(self, address, register, values, *, bits=16):
        for offset,value in enumerate(values):
            self.write_register_value(address,register+offset,value,bits=bits)
        return SerialResponse(True,data="OK")

    def extract_rdataw_value(self, data, **kwargs):
        return None if data == "invalid" else int(data, 16)

    def emit_log(self, message):
        self.log_history.append(message)

    def clear_log(self):
        self.log_history.clear()

    def add_log_clear_listener(self, listener):
        pass

    def add_log_listener(self, listener):
        pass

    def remove_log_listener(self, listener):
        pass

    def remove_log_clear_listener(self, listener):
        pass

    def disconnect(self):
        self.is_connected = False


class ConnectedShowIoTests(unittest.TestCase):
    def test_normal_mode_show_returns_without_slow_io_in_tk_thread(self):
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show("awp1018")
            host = app.navigation.pages["awp1018"]
            manager = SlowManager()
            router = app.serial
            router._managers["FAKE_A"] = manager
            router.set_page_port("awp1018", "FAKE_A")
            app.state.selected_port = "FAKE_A"
            started = time.perf_counter()
            host.on_show()
            callback_ms = (time.perf_counter()-started)*1000
            self.assertLess(callback_ms, 80, f"show callback blocked {callback_ms:.1f} ms on fake serial I/O")
            self.assertTrue(router._register_settings_queue.wait_idle(2))
            self.assertEqual([c[2] for c in manager.calls if c[0] == "write"], [0x27, 0x06, 0x22, 0x22])
            self.assertTrue(all(c[3] != threading.get_ident() for c in manager.calls), manager.calls)
            # Preserve unrelated fields across consecutive RMWs of shared registers.
            self.assertEqual(manager.registers[0x06] & 0x3FFF, 0x3FFF)
            self.assertEqual(manager.registers[0x22] & 0xF000, 0xF000)

    def test_setting_changes_keep_latest_value_and_never_read_tk_in_worker(self):
        from unittest.mock import patch
        import tkinter as tk
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show("awp1018")
            page = app.navigation.pages["awp1018"].mode_pages["awp1018_normal_mode"]
            router, manager = app.serial, SlowManager(.005)
            router._managers["FAKE_A"] = manager
            router.set_page_port("awp1018", "FAKE_A")
            app.state.selected_port = "FAKE_A"
            original = tk.StringVar.get
            def checked(variable):
                if threading.current_thread() is not threading.main_thread():
                    raise AssertionError("worker accessed Tk variable")
                return original(variable)
            with patch.object(tk.StringVar, "get", checked):
                page.on_show()
                page.debounce_time_var.set("250")
                page.adc_sample_update_var.set(page.ADC_SAMPLE_UPDATE_OPTIONS[-1])
                self.assertTrue(router._register_settings_queue.wait_idle(2))
            self.assertEqual(manager.registers[0x22], 0xFCFA)
            self.assertEqual(router._register_settings_queue.failed, 0)

    def test_cold_navigation_initialization_and_mode_bit_run_on_fixed_worker(self):
        with mocked_application() as app:
            app.set_animations(False)
            router, manager = app.serial, SlowManager(.005)
            router._managers["FAKE_A"] = manager
            router.set_page_port("awp1018", "FAKE_A")
            app.state.selected_port = "FAKE_A"
            app.navigation.show("awp1018")
            host = app.navigation.pages["awp1018"]
            self.assertTrue(router._register_settings_queue.wait_idle(2))
            self.assertEqual(manager.registers[0x34], 0xABC5)
            self.assertTrue(all(c[3] != threading.get_ident() for c in manager.calls))
            host._set_diff_mode_registers(True)
            self.assertTrue(router._register_settings_queue.wait_idle(1))
            self.assertEqual(manager.registers[0x34], 0xABCD)
            self.assertTrue(all(c[1] == 0x48 for c in manager.calls))

    def test_board_preset_supersedes_pending_display_rmw_before_worker_starts(self):
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show("awp1018")
            page=app.navigation.pages["awp1018"].mode_pages["awp1018_normal_mode"]
            router,manager=app.serial,SlowManager(0)
            router._managers["FAKE_A"]=manager
            router.set_page_port("awp1018","FAKE_A")
            app.state.selected_port="FAKE_A"
            entered,release,done=threading.Event(),threading.Event(),threading.Event()
            manager.read_hook=lambda reg:(entered.set(),release.wait(2))
            router.queue_register_setting("awp1018",0x48,0x22,100,clear_mask=0x3FF)
            self.assertTrue(entered.wait(1))
            def preset():
                with manager.snapshot_transaction():
                    manager.registers[0x22]=0xA5A5
                done.set()
                return "fake result"
            try:
                page._start_board_preset_operation("read",preset)
            finally:
                release.set()
            self.assertTrue(done.wait(1))
            self.assertTrue(router._register_settings_queue.wait_idle(1))
            self.assertEqual(manager.registers[0x22],0xA5A5)
            self.assertFalse([c for c in manager.calls if c[0]=="write"])

    def test_worker_status_logs_use_dispatcher_without_calling_tk_after(self):
        from unittest.mock import patch
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show("awp1018")
            page = app.navigation.pages["awp1018"].mode_pages["awp1018_normal_mode"]
            seen = []
            with patch.object(page, "after", side_effect=AssertionError("worker touched Tk")), patch.object(page, "_process_serial_status_log", side_effect=lambda line: seen.append((line, threading.get_ident()))):
                worker = threading.Thread(target=page._handle_serial_status_log,args=("fake status",))
                worker.start()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                app.ui_dispatcher.drain()
            self.assertEqual(seen, [("fake status", threading.get_ident())])


if __name__ == "__main__":
    unittest.main()
