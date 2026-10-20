from __future__ import annotations

import io
import subprocess
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import firmware_flasher as ff

VALID_HEX = ":100000000C9434000C943E000C943E000C943E0082\n:00000001FF\n"


def make_tools(root: Path) -> None:
    (root / "assets" / "tools" / "avrdude").mkdir(parents=True)
    (root / "assets" / "tools" / "ch340").mkdir(parents=True)
    (root / "assets" / "tools" / "avrdude" / "avrdude.exe").write_bytes(b"MZ")
    (root / "assets" / "tools" / "avrdude" / "avrdude.conf").write_text("#")
    (root / "assets" / "tools" / "ch340" / "CH341SER.EXE").write_bytes(b"MZ")


class FakeProc:
    def __init__(self, output: str, code: int) -> None:
        self.stdout = io.StringIO(output)
        self._code = code

    def wait(self) -> int:
        return self._code


class FakePopen:
    def __init__(self, script: list[tuple[str, int]]) -> None:
        self.script = list(script)
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        self.kwargs.append(kwargs)
        output, code = self.script.pop(0)
        return FakeProc(output, code)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        make_tools(self.root)
        self.hex = self.root / "fw.hex"
        self.hex.write_text(VALID_HEX)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class HexValidationTests(Base):
    def test_valid(self) -> None:
        self.assertEqual(ff.validate_hex(self.hex), self.hex)

    def test_errors(self) -> None:
        cases = {
            "": "firmware_error_no_file",
            str(self.root / "nope.hex"): "firmware_error_file_missing",
        }
        bad = {
            "text.hex": "hello world\n",
            "checksum.hex": ":100000000C9434000C943E000C943E000C943E0083\n:00000001FF\n",
            "noeof.hex": ":100000000C9434000C943E000C943E000C943E0082\n",
            "ino.hex": "void setup() {}\n",
        }
        for name, text in bad.items():
            path = self.root / name
            path.write_text(text)
            cases[str(path)] = "firmware_error_invalid_hex"
        binary = self.root / "bin.hex"
        binary.write_bytes(b"\xff\xfe\x00")
        cases[str(binary)] = "firmware_error_invalid_hex"
        for path, key in cases.items():
            with self.subTest(path=path):
                with self.assertRaises(ff.FirmwareFlashError) as ctx:
                    ff.validate_hex(path)
                self.assertEqual(ctx.exception.key, key)


class AvrdudeTests(Base):
    def test_args(self) -> None:
        args = ff.build_avrdude_args("a/avrdude.exe", "a/avrdude.conf", "COM3", "C:/x/fw.hex", 57600)
        self.assertEqual(
            args,
            ["a/avrdude.exe", "-C", "a/avrdude.conf", "-c", "arduino", "-p", "m328p",
             "-P", "COM3", "-b", "57600", "-D", "-U", "flash:w:C:/x/fw.hex:i"],
        )

    def test_high_com_port_on_windows(self) -> None:
        with mock.patch.object(ff, "_IS_WINDOWS", True):
            self.assertEqual(ff.avrdude_port_name("COM12"), r"\\.\COM12")
            self.assertEqual(ff.avrdude_port_name("COM3"), "COM3")

    def test_success_first_try(self) -> None:
        popen = FakePopen([("Writing | ################ | 100% 0.5s\navrdude done.  Thank you.\n", 0)])
        logs, progress = [], []
        result = ff.FirmwareFlasher(root=self.root, popen=popen).flash("COM3", self.hex, log=logs.append, progress=progress.append)
        self.assertTrue(result.ok)
        self.assertEqual(result.baudrate, 115200)
        self.assertEqual(len(popen.calls), 1)
        self.assertIn("avrdude done.  Thank you.", logs)
        self.assertEqual(progress[-1], 100)
        self.assertIn(50, progress)  # writing 100% = half (verify is the other half)
        self.assertEqual(popen.kwargs[0]["stderr"], subprocess.STDOUT)

    def test_retry_57600_after_sync_failure(self) -> None:
        popen = FakePopen([
            ("avrdude: stk500_getsync() attempt 10 of 10: not in sync: resp=0x00\n", 1),
            ("avrdude done.  Thank you.\n", 0),
        ])
        result = ff.FirmwareFlasher(root=self.root, popen=popen).flash("COM3", self.hex)
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, [115200, 57600])
        self.assertEqual(result.baudrate, 57600)
        self.assertEqual(popen.calls[0][popen.calls[0].index("-b") + 1], "115200")
        self.assertEqual(popen.calls[1][popen.calls[1].index("-b") + 1], "57600")

    def test_both_bauds_fail_sync(self) -> None:
        sync = ("avrdude: stk500_recv(): programmer is not responding\n", 1)
        popen = FakePopen([sync, sync])
        result = ff.FirmwareFlasher(root=self.root, popen=popen).flash("COM3", self.hex)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_key, "firmware_error_sync")
        self.assertEqual(result.attempts, [115200, 57600])

    def test_non_sync_error_does_not_retry(self) -> None:
        popen = FakePopen([("avrdude: verification error, first mismatch at byte 0x0000\n", 1)])
        result = ff.FirmwareFlasher(root=self.root, popen=popen).flash("COM3", self.hex)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_key, "firmware_error_avrdude")
        self.assertEqual(len(popen.calls), 1)

    def test_missing_tool_and_bad_input_never_launch(self) -> None:
        popen = FakePopen([])
        (self.root / "assets" / "tools" / "avrdude" / "avrdude.exe").unlink()
        flasher = ff.FirmwareFlasher(root=self.root, popen=popen)
        self.assertEqual(flasher.flash("COM3", self.hex).error_key, "firmware_error_tool_missing")
        self.assertEqual(flasher.flash("COM3", self.root / "x.hex").error_key, "firmware_error_file_missing")
        self.assertEqual(flasher.flash("", self.hex).error_key, "firmware_error_no_port")
        self.assertEqual(popen.calls, [])

    def test_launch_oserror(self) -> None:
        def boom(*_a, **_k):
            raise OSError("denied")
        result = ff.FirmwareFlasher(root=self.root, popen=boom).flash("COM3", self.hex)
        self.assertEqual(result.error_key, "firmware_error_launch")


class SessionTests(unittest.TestCase):
    def make(self, connected: bool):
        events: list[tuple] = []
        scheduled: list[tuple] = []
        session = ff.FlashSession(
            "COM3",
            is_connected=lambda p: connected,
            disconnect=lambda p: events.append(("disconnect", p)),
            reconnect=lambda p: events.append(("reconnect", p)),
            schedule=lambda ms, fn: scheduled.append((ms, fn)),
        )
        return session, events, scheduled

    def test_connected_success(self) -> None:
        session, events, scheduled = self.make(True)
        self.assertTrue(session.begin())
        self.assertEqual(events, [("disconnect", "COM3")])
        done = []
        self.assertTrue(session.finish(True, on_reconnect=lambda: done.append(1)))
        self.assertEqual(scheduled[0][0], ff.RECONNECT_DELAY_MS)
        scheduled[0][1]()
        self.assertEqual(events[-1], ("reconnect", "COM3"))
        self.assertEqual(done, [1])

    def test_connected_failure_still_reconnects(self) -> None:
        session, events, scheduled = self.make(True)
        session.begin()
        self.assertTrue(session.finish(False))
        scheduled[0][1]()
        self.assertEqual(events, [("disconnect", "COM3"), ("reconnect", "COM3")])

    def test_not_connected(self) -> None:
        session, events, scheduled = self.make(False)
        self.assertFalse(session.begin())
        self.assertFalse(session.finish(True))
        self.assertEqual((events, scheduled), ([], []))


class DriverTests(Base):
    @staticmethod
    def runner(drivers: str, problems: str = ""):
        def run(args, **_kwargs):
            out = drivers if "/enum-drivers" in args else problems
            return SimpleNamespace(stdout=out, returncode=0)
        return run

    def test_non_windows_skips(self) -> None:
        run = mock.Mock()
        status = ff.check_ch340_driver(platform="linux", runner=run)
        self.assertFalse(status.checked)
        self.assertFalse(status.needs_install)
        run.assert_not_called()

    def test_installed(self) -> None:
        status = ff.check_ch340_driver(platform="win32", runner=self.runner("Original Name: ch341ser.inf\n"))
        self.assertTrue(status.installed)
        self.assertFalse(status.needs_install)

    def test_missing(self) -> None:
        status = ff.check_ch340_driver(platform="win32", runner=self.runner("Original Name: oem1.inf\n"))
        self.assertTrue(status.needs_install)
        self.assertFalse(status.installed)

    def test_problem_device(self) -> None:
        status = ff.check_ch340_driver(
            platform="win32",
            runner=self.runner("Original Name: ch341ser.inf\n", r"Instance ID: USB\VID_1A86&PID_7523\5&1" "\n"),
        )
        self.assertTrue(status.problem_device)
        self.assertTrue(status.needs_install)

    def test_install_silent_then_interactive_fallback(self) -> None:
        calls = []
        statuses = [ff.DriverStatus(True, installed=False), ff.DriverStatus(True, installed=True)]
        status = ff.install_ch340_driver(
            root=self.root,
            elevate=lambda exe, params: calls.append((Path(exe).name, params)) or 0,
            check=lambda: statuses.pop(0),
        )
        self.assertTrue(status.installed)
        self.assertEqual(calls, [("CH341SER.EXE", "/S"), ("CH341SER.EXE", "")])

    def test_install_uac_declined(self) -> None:
        with self.assertRaises(ff.FirmwareFlashError) as ctx:
            ff.install_ch340_driver(root=self.root, elevate=lambda *_: None, check=mock.Mock())
        self.assertEqual(ctx.exception.key, "ch340_install_cancelled")


class FakeRouter:
    def __init__(self) -> None:
        self.logs: list[str] = []

    def emit_log(self, message: str) -> None:
        self.logs.append(message)


class FakeHome(tk.Frame):
    def __init__(self, master, connected: bool) -> None:
        super().__init__(master)
        self.language = "zh"
        self.ctx = SimpleNamespace(serial=FakeRouter(), colors={"panel": "#FFFFFF", "text": "#111827"},
                                   state=SimpleNamespace(theme="light"))
        self.port_var = tk.StringVar(self, value="COM3")
        self.port_combo = SimpleNamespace(cget=lambda _k: ("COM3",))
        self.connected = connected
        self.events: list[tuple] = []

    def firmware_port_connected(self, port):
        return self.connected

    def firmware_release_port(self, port):
        self.connected = False
        self.events.append(("release", port))

    def firmware_restore_port(self, port):
        self.connected = True
        self.events.append(("restore", port))

    def after(self, ms, fn=None, *args):  # run scheduled reconnects immediately
        if fn is None:
            return super().after(ms)
        self.events.append(("after", ms))
        fn(*args)
        return "x"


class DialogFlowTests(Base):
    def setUp(self) -> None:
        super().setUp()
        try:
            self.tk_root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.tk_root.withdraw()

    def tearDown(self) -> None:
        self.tk_root.destroy()
        super().tearDown()

    def make_dialog(self, connected=True, driver=None, answer=True, popen_script=None):
        from pages import firmware_flash_dialog as dlg

        dlg.remember_hex_path("")
        home = FakeHome(self.tk_root, connected)
        self.prompts: list[str] = []
        self.installs: list[int] = []
        popen = FakePopen(popen_script or [("avrdude done.  Thank you.\n", 0)])

        def ask(_title, message, **_kw):
            self.prompts.append(message)
            return answer

        def install():
            self.installs.append(1)
            return ff.DriverStatus(True, installed=True)

        with mock.patch.object(dlg, "post_to_ui", lambda _w, fn, *a: fn(*a)):
            dialog = dlg.FirmwareFlashDialog(
                home,
                flasher=ff.FirmwareFlasher(root=self.root, popen=popen),
                check_driver=lambda: driver or ff.DriverStatus(checked=False),
                install_driver=install,
                ask_yes_no=ask,
                start_thread=lambda fn: fn(),
            )
        self.popen = popen
        return dlg, home, dialog

    def run_start(self, dlg, dialog):
        with mock.patch.object(dlg, "post_to_ui", lambda _w, fn, *a: fn(*a)), \
             mock.patch.object(dlg.messagebox, "showwarning") as warn:
            started = dialog.start()
        return started, warn

    def test_requires_hex_choice_no_default(self) -> None:
        dlg, home, dialog = self.make_dialog()
        self.assertEqual(dialog.hex_var.get(), "")
        started, warn = self.run_start(dlg, dialog)
        self.assertFalse(started)
        warn.assert_called_once()
        self.assertEqual(self.popen.calls, [])
        self.assertEqual(home.events, [])

    def test_invalid_hex_rejected_before_disconnect(self) -> None:
        dlg, home, dialog = self.make_dialog()
        bad = self.root / "bad.hex"
        bad.write_text("void loop(){}")
        dialog.hex_var.set(str(bad))
        started, _warn = self.run_start(dlg, dialog)
        self.assertFalse(started)
        self.assertEqual(home.events, [])

    def test_full_flow_disconnect_flash_reconnect(self) -> None:
        dlg, home, dialog = self.make_dialog(connected=True)
        dialog.hex_var.set(str(self.hex))
        started, _ = self.run_start(dlg, dialog)
        self.assertTrue(started)
        self.assertTrue(dialog.last_result.ok)
        self.assertEqual(home.events, [("release", "COM3"), ("after", ff.RECONNECT_DELAY_MS), ("restore", "COM3")])
        self.assertFalse(dialog.busy)
        self.assertTrue(any("[flash]" in line for line in home.ctx.serial.logs))
        self.assertEqual(dlg.last_hex_path(), str(self.hex))
        self.assertIn("COM3", dialog.status_var.get())

    def test_driver_missing_prompt_yes_installs_then_flashes(self) -> None:
        dlg, home, dialog = self.make_dialog(connected=False, driver=ff.DriverStatus(True, installed=False))
        dialog.hex_var.set(str(self.hex))
        self.run_start(dlg, dialog)
        self.assertEqual(self.prompts, ["未检测到 CH340 驱动，是否现在安装？"])
        self.assertEqual(self.installs, [1])
        self.assertEqual(len(self.popen.calls), 1)
        self.assertEqual(home.events, [])  # was not connected: nothing to restore

    def test_driver_missing_prompt_no_skips_install(self) -> None:
        dlg, home, dialog = self.make_dialog(driver=ff.DriverStatus(True, installed=False), answer=False)
        dialog.hex_var.set(str(self.hex))
        self.run_start(dlg, dialog)
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(self.installs, [])
        self.assertEqual(len(self.popen.calls), 1)

    def test_failure_reconnects_and_reports(self) -> None:
        dlg, home, dialog = self.make_dialog(connected=True, popen_script=[("avrdude: verification error\n", 1)])
        dialog.hex_var.set(str(self.hex))
        self.run_start(dlg, dialog)
        self.assertFalse(dialog.last_result.ok)
        self.assertEqual(home.events[0], ("release", "COM3"))
        self.assertEqual(home.events[-1], ("restore", "COM3"))
        self.assertIn("烧录失败", dialog.status_var.get())


class BundleTests(unittest.TestCase):
    def test_repo_ships_tools_and_no_firmware(self) -> None:
        from build_support.bundle_paths import firmware_tool_files

        repo = Path(__file__).resolve().parent.parent
        self.assertEqual(len(firmware_tool_files(repo)), 3)
        self.assertFalse((repo / "assets" / "firmware").exists())
        self.assertEqual(list((repo / "assets").rglob("*.hex")), [])

    def test_meipass_root(self) -> None:
        import sys
        from build_support import bundle_paths

        with mock.patch.object(sys, "_MEIPASS", "/frozen", create=True):
            self.assertEqual(bundle_paths.resource_root(), Path("/frozen"))
            self.assertEqual(ff.avrdude_paths()[0], Path("/frozen/assets/tools/avrdude/avrdude.exe"))

    def test_language_keys_match(self) -> None:
        from config.languages import LANGUAGES

        keys = [k for k in LANGUAGES["zh"] if k.startswith(("firmware_", "ch340_", "flash_firmware"))]
        self.assertGreater(len(keys), 20)
        for key in keys:
            self.assertIn(key, LANGUAGES["en"])


if __name__ == "__main__":
    unittest.main()
