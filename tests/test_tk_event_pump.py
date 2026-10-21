"""Regression coverage for deadlines that outlive their Tk callbacks."""
from __future__ import annotations

import time
import tkinter as tk
import unittest

from tests.test_connect_cancel_blocking import (
    _capture_callback_exceptions,
    _guard_callback,
    _pump_until,
)
from tests.tk_cleanup import attach_tk_cleanup, cancel_after_jobs, prepare_tk_test


class TclEventPumpTests(unittest.TestCase):
    def setUp(self) -> None:
        prepare_tk_test(self)
        self.root = tk.Tcl()
        attach_tk_cleanup(self, self.root)

    def test_deleted_completion_callback_still_hits_wall_clock_deadline(self) -> None:
        done = []
        self.root.after(60_000, lambda: done.append(True))
        self.root.after(0, lambda: cancel_after_jobs(self.root))
        started = time.perf_counter()
        with self.assertRaisesRegex(AssertionError, "wall-clock deadline before completion"):
            _pump_until(self, self.root, lambda: bool(done), [], timeout_s=0.05)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertFalse(done)
        self.assertFalse(self.root.tk.call("after", "info"))

    def test_callback_exception_fails_with_original_traceback(self) -> None:
        errors, wrapped = _capture_callback_exceptions(self.root)

        def broken_callback() -> None:
            raise RuntimeError("scheduled callback failed")

        self.root.after(0, _guard_callback(broken_callback, errors, wrapped))
        with self.assertRaisesRegex(AssertionError, "RuntimeError: scheduled callback failed"):
            _pump_until(self, self.root, lambda: False, errors, timeout_s=1.0)
        self.assertEqual(len(errors), 1)
        self.assertIn("broken_callback", errors[0])


class TkEventPumpTests(unittest.TestCase):
    def test_destroyed_root_with_another_live_root_still_hits_deadline(self) -> None:
        prepare_tk_test(self)
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        attach_tk_cleanup(self, root)
        other_root = tk.Tk()
        attach_tk_cleanup(self, other_root)
        root.withdraw()
        other_root.withdraw()
        errors, wrapped = _capture_callback_exceptions(root)
        root.after(0, _guard_callback(root.destroy, errors, wrapped))
        started = time.perf_counter()
        with self.assertRaisesRegex(AssertionError, "wall-clock deadline before completion"):
            _pump_until(self, root, lambda: False, errors, timeout_s=0.1)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertTrue(other_root.winfo_exists())
        with self.assertRaises(tk.TclError):
            root.winfo_exists()


if __name__ == "__main__":
    unittest.main()
