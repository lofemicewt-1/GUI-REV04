from __future__ import annotations

import unittest
from types import SimpleNamespace

import tkinter as tk
from tests.tk_cleanup import attach_tk_cleanup, cancel_after_jobs, prepare_tk_test

from pages.chip_pages import AWP1921AWP1923Page
from styles.theme import scale_photoimage


class _Serial:
    is_connected = False
    log_history: list[str] = []

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add_log_listener(self, _listener) -> None:
        return None

    def remove_log_listener(self, _listener) -> None:
        return None

    def add_log_clear_listener(self, _listener) -> None:
        return None

    def remove_log_clear_listener(self, _listener) -> None:
        return None

    def connected_port_options(self) -> list[tuple[str, str]]:
        return []

    def read_register_value(self, *args, **kwargs):
        self.calls.append(("read", args, kwargs))
        return SimpleNamespace(ok=False, error="offline", data="")

    def write_register_value(self, *args, **kwargs):
        self.calls.append(("write", args, kwargs))
        return SimpleNamespace(ok=False, error="offline")


def _ctx(serial: _Serial) -> SimpleNamespace:
    return SimpleNamespace(
        serial=serial,
        colors={
            "bg": "#111827",
            "panel": "#1F2937",
            "panel_alt": "#172033",
            "text": "#F9FAFB",
            "muted": "#9CA3AF",
            "border": "#374151",
            "primary": "#60A5FA",
            "canvas_bg": "#111827",
        },
        state=SimpleNamespace(
            language="en",
            theme="dark",
            extra={},
            selected_port=None,
            awp192x_device_addr=0x44,
            single_point_duration_us=10,
        ),
    )


class DetailModeGateTests(unittest.TestCase):
    def setUp(self) -> None:
        prepare_tk_test(self)
        self.root = tk.Tk()
        attach_tk_cleanup(self, self.root)
        self.root.withdraw()
        self.serial = _Serial()
        self.page = AWP1921AWP1923Page(self.root, _ctx(self.serial))
        self.addCleanup(self._destroy_page)

    def _destroy_page(self) -> None:
        cancel_after_jobs(self.root)
        try:
            self.page.destroy()
        except tk.TclError:
            pass
        self.page = None
    def test_detail_stays_inactive_outside_full_mode(self) -> None:
        self.page.detail_mode_var.set(True)
        self.page.reg_addr_var.set("0x11")
        self.page.write_value_var.set("0x2222")
        self.page.show_mode("button_mode")

        self.assertFalse(self.page.detail_mode_active())
        self.assertFalse(self.page._detail_entry_group.grid_info())
        self.assertFalse(self.page._detail_host.grid_info())
        self.assertEqual(self.page._active_register_address(), 0x11)
        self.assertEqual(self.serial.calls, [])

    def test_full_mode_round_trip_keeps_drafts_and_does_not_transmit(self) -> None:
        self.page.show_mode("full_mode")
        self.page.reg_addr_var.set("0x22")
        self.page.write_value_var.set("0x1111")
        self.page.detail_mode_var.set(True)
        self.page._on_detail_mode_changed()
        detail_address = self.page._active_register_address()
        self.page.write_value_var.set("0xABCD")
        self.serial.calls.clear()

        self.page.show_mode("button_mode")
        self.page.show_mode("pump_mode")
        self.assertFalse(self.page.detail_mode_active())
        self.assertFalse(self.page._detail_host.grid_info())
        self.assertEqual(self.page.reg_addr_var.get(), "0x22")
        self.assertEqual(self.page.write_value_var.get(), "0x1111")
        self.assertEqual(self.serial.calls, [])

        self.page.show_mode("full_mode")
        self.assertTrue(self.page.detail_mode_var.get())
        self.assertTrue(self.page.detail_mode_active())
        self.assertTrue(self.page._detail_host.grid_info())
        self.assertEqual(self.page._active_register_address(), detail_address)
        self.assertEqual(self.page.write_value_var.get(), "0xABCD")
        self.assertEqual(self.serial.calls, [])


class LogoScaleTests(unittest.TestCase):
    def setUp(self) -> None:
        prepare_tk_test(self)
        self.root = tk.Tk()
        attach_tk_cleanup(self, self.root)
        self.root.withdraw()
    def test_fractional_scale_beats_integer_subsample_jump(self) -> None:
        image = tk.PhotoImage(master=self.root, width=100, height=40)
        factor = max(1, (100 + 30 - 1) // 30)
        integer_width = 100 // factor
        scaled = scale_photoimage(image, max_width=30, enlarge=1.15)
        self.assertGreater(scaled.width(), integer_width)
        self.assertLessEqual(scaled.width(), 30)
        self.assertGreater(scaled.height(), 0)
