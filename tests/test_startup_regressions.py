from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class StartupRegressionTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows batch launcher")
    def test_missing_configured_python_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "probe.cmd"
            wrapper.write_text(
                '@echo off\ncall "%s"\nif defined PYTHON_EXE (exit /b 8)\nexit /b 0\n'
                % (ROOT / "build_support" / "find_python.cmd"), encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(PYTHON_EXE=str(Path(tmp) / "missing.exe"),
                       PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                       USERPROFILE=tmp)
            result = subprocess.run([os.environ["COMSPEC"], "/d", "/c", str(wrapper)],
                                    env=env, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(sys.platform == "win32", "Windows font registration")
    def test_gui_starts_with_an_unresponsive_foreign_window(self) -> None:
        # A real top-level window whose owning thread stops pumping messages.
        # It models an unrelated hung desktop app, without touching user apps.
        helper = subprocess.Popen(
            [sys.executable, "-u", "-c",
             "import tkinter as tk; r=tk.Tk(); r.withdraw(); r.update(); "
             "print('READY', flush=True); input(); r.destroy()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=ROOT,
        )
        try:
            self.assertEqual(helper.stdout.readline().strip(), "READY")
            code = (
                "import faulthandler; faulthandler.dump_traceback_later(8); "
                "from app.application import PiezoApplication; "
                "app=PiezoApplication(); "
                "app.root.after(300, app.close); app.run(); "
                "print('GUI_READY_AND_CLOSED', flush=True)"
            )
            try:
                result = subprocess.run(
                    [sys.executable, "-c", code], cwd=ROOT,
                    capture_output=True, text=True, timeout=15,
                )
            except subprocess.TimeoutExpired as exc:
                self.fail(f"GUI hung before the event loop: {exc.stderr!r}")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("GUI_READY_AND_CLOSED", result.stdout)
        finally:
            helper.kill()
            helper.communicate(timeout=5)

    def test_failure_dialog_does_not_require_tk(self) -> None:
        import main
        import ctypes

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(main, "crash_log_path", return_value=Path(tmp) / "error.log"), \
                patch.dict(sys.modules, {"tkinter": None}), \
                patch.object(sys, "stderr", None), \
                patch.object(ctypes.windll.user32, "MessageBoxW", return_value=1) as dialog:
            main._report_startup_failure(RuntimeError("missing Tcl runtime"))
            dialog.assert_called_once()
            self.assertIn("missing Tcl runtime", dialog.call_args.args[1])

    def test_failure_log_falls_back_to_user_directory(self) -> None:
        import main
        import ctypes

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Opening a directory as a file deterministically raises OSError.
            with patch.object(main, "crash_log_path", return_value=base), \
                    patch.dict(os.environ, {"LOCALAPPDATA": str(base / "local")}), \
                    patch.dict(sys.modules, {"tkinter": None}), \
                    patch.object(ctypes.windll.user32, "MessageBoxW", return_value=1):
                main._report_startup_failure(RuntimeError("read-only install"))
            logs = list((base / "local").rglob("*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("read-only install", logs[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
