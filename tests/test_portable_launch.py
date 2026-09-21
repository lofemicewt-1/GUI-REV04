from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MACHINE_MARKERS = (
    r"C:\Users\wasd1",
    r"C:\\Users\\wasd1",
    "C:/Users/wasd1",
)


class PortableLaunchTests(unittest.TestCase):
    def test_scripts_are_not_tied_to_this_pc(self) -> None:
        files = [
            ROOT / "run_gui.bat",
            ROOT / "build_exe.bat",
            ROOT / "AnalogwinPiezoGUI.spec",
            ROOT / "build_support" / "find_python.cmd",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            for marker in MACHINE_MARKERS:
                self.assertNotIn(marker, text, msg=path.name)


class TclTkBundleTests(unittest.TestCase):
    def test_uses_given_prefix(self) -> None:
        from build_support.bundle_paths import tcl_tk_bundle

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "tcl" / "tcl8.6").mkdir(parents=True)
            (base / "tcl" / "tk8.6").mkdir(parents=True)
            (base / "DLLs").mkdir()
            (base / "DLLs" / "tcl86t.dll").write_bytes(b"")
            (base / "DLLs" / "tk86t.dll").write_bytes(b"")
            (base / "DLLs" / "_tkinter.pyd").write_bytes(b"")
            bundle = tcl_tk_bundle(base)
            self.assertEqual(bundle.tcl_data, base / "tcl" / "tcl8.6")
            self.assertEqual(bundle.tk_data, base / "tcl" / "tk8.6")
            self.assertEqual(bundle.tcl_dll, base / "DLLs" / "tcl86t.dll")
            self.assertTrue(str(bundle.tcl_dll).startswith(str(base)))

    def test_discovers_newer_tcl_layout(self) -> None:
        from build_support.bundle_paths import tcl_tk_bundle

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "tcl" / "tcl8.7").mkdir(parents=True)
            (base / "tcl" / "tk8.7").mkdir(parents=True)
            (base / "DLLs").mkdir()
            (base / "DLLs" / "tcl87t.dll").write_bytes(b"")
            (base / "DLLs" / "tk87t.dll").write_bytes(b"")
            (base / "DLLs" / "_tkinter.pyd").write_bytes(b"")
            bundle = tcl_tk_bundle(base)
            self.assertEqual(bundle.tcl_data, base / "tcl" / "tcl8.7")
            self.assertEqual(bundle.tk_data, base / "tcl" / "tk8.7")
            self.assertEqual(bundle.tcl_dll, base / "DLLs" / "tcl87t.dll")
            self.assertEqual(bundle.tk_dll, base / "DLLs" / "tk87t.dll")

    def test_missing_tcl_raises(self) -> None:
        from build_support.bundle_paths import tcl_tk_bundle

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                tcl_tk_bundle(Path(tmp))


class CrashLogTests(unittest.TestCase):
    def test_frozen_log_sits_beside_exe(self) -> None:
        from main import crash_log_path

        path = crash_log_path(
            frozen=True,
            executable=r"D:\share\AnalogwinPiezoGUI.exe",
            script=r"C:\src\main.py",
        )
        self.assertEqual(path, Path(r"D:\share\AnalogwinPiezoGUI.error.log"))

    def test_dev_log_sits_beside_main(self) -> None:
        from main import crash_log_path

        path = crash_log_path(
            frozen=False,
            executable=r"D:\share\AnalogwinPiezoGUI.exe",
            script=r"C:\src\main.py",
        )
        self.assertEqual(path, Path(r"C:\src\AnalogwinPiezoGUI.error.log"))


if __name__ == "__main__":
    unittest.main()
