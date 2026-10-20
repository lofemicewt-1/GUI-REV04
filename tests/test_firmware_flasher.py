from __future__ import annotations

import io
import os
import subprocess
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import firmware_flasher as ff

VALID_HEX = ":100000000C9434000C943E000C943E000C943E0082\n:00000001FF\n"
DONE = ("avrdude done.  Thank you.\n", 0)


def make_tools(root: Path) -> None:
    (root / "assets" / "tools" / "avrdude").mkdir(parents=True)
    (root / "assets" / "tools" / "ch340").mkdir(parents=True)
    (root / "assets" / "tools" / "avrdude" / "avrdude.exe").write_bytes(b"MZ")
    (root / "assets" / "tools" / "avrdude" / "avrdude.conf").write_text("#")
    (root / "assets" / "tools" / "ch340" / "CH341SER.EXE").write_bytes(b"MZ")


def flasher(root, popen, **kw):
    kw.setdefault("port_probe", lambda _p: True)
    return ff.FirmwareFlasher(root=root, popen=popen, **kw)


class FakeProc:
    def __init__(self, output: str | bytes, code: int) -> None:
        data = output.encode("utf-8") if isinstance(output, str) else output
        self.stdout = io.BytesIO(data)
        self._code = code
        self.killed = False

    def poll(self):
        return -9 if self.killed else None

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None) -> int:
        return self._code


class HangingStream:
    """Never returns data until killed: a stuck avrdude."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def read1(self, _n):
        self.release.wait(5)
        return b""

    def close(self) -> None:
        self.release.set()


class HangingProc(FakeProc):
    def __init__(self) -> None:
        super().__init__(b"", 0)
        self.stdout = HangingStream()

    def kill(self) -> None:
        self.killed = True
        self.stdout.release.set()


class FakePopen:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.workdir_files: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        self.kwargs.append(kwargs)
        self.workdir_files.append(sorted(os.listdir(kwargs["cwd"])))
        item = self.script.pop(0)
        return FakeProc(*item) if isinstance(item, tuple) else item


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
        cases = {"": "firmware_error_no_file", str(self.root / "nope.hex"): "firmware_error_file_missing"}
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
        args = ff.build_avrdude_args("a/avrdude.exe", "avrdude.conf", "COM3", "firmware.hex", 115200)
        self.assertEqual(
            args,
            ["a/avrdude.exe", "-C", "avrdude.conf", "-c", "arduino", "-p", "m328p",
             "-P", "COM3", "-b", "115200", "-D", "-U", "flash:w:firmware.hex:i"],
        )

    def test_high_com_port_on_windows(self) -> None:
        with mock.patch.object(ff, "_IS_WINDOWS", True):
            self.assertEqual(ff.avrdude_port_name("COM12"), r"\\.\COM12")
            self.assertEqual(ff.avrdude_port_name("COM3"), "COM3")

    def test_success_first_try_115200_in_ascii_workdir(self) -> None:
        popen = FakePopen([("Writing | ################ | 100% 0.5s\n" + DONE[0], 0)])
        logs, progress = [], []
        result = flasher(self.root, popen).flash("COM3", self.hex, log=logs.append, progress=progress.append)
        self.assertTrue(result.ok)
        self.assertEqual(result.baudrate, 115200)
        self.assertEqual(len(popen.calls), 1)
        self.assertIn("avrdude done.  Thank you.", logs)
        self.assertEqual(progress[-1], 100)
        self.assertIn(50, progress)
        self.assertEqual(popen.kwargs[0]["stderr"], subprocess.STDOUT)
        args = popen.calls[0]
        self.assertEqual(args[args.index("-C") + 1], "avrdude.conf")
        self.assertEqual(args[-1], "flash:w:firmware.hex:i")
        self.assertEqual(popen.workdir_files[0], ["avrdude.conf", "firmware.hex"])
        self.assertFalse(Path(popen.kwargs[0]["cwd"]).exists())  # cleaned up

    def test_unicode_and_space_hex_path_is_copied(self) -> None:
        odd_dir = self.root / "固件 目录 ✓"
        odd_dir.mkdir()
        odd = odd_dir / "我的 固件.hex"
        odd.write_text(VALID_HEX)
        popen = FakePopen([DONE])
        self.assertTrue(flasher(self.root, popen).flash("COM3", odd).ok)
        self.assertTrue(all(arg.isascii() for arg in popen.calls[0][1:]))

    def test_console_decoding(self) -> None:
        text = "avrdude: 无法打开 C:/用户/fw.hex\n"
        self.assertEqual(ff.decode_console(text.encode("utf-8")), text)
        self.assertEqual(ff.decode_console("pnputil 枚举\r\n".encode("utf-16")), "pnputil 枚举\r\n")
        self.assertEqual(ff.decode_console("ABC".encode("utf-16-le")), "ABC")
        self.assertIsInstance(ff.decode_console("设备".encode("gbk"), platform="linux"), str)

    def test_progress_bar_without_newline(self) -> None:
        popen = FakePopen([(b"Writing | ########################## | 100% 0.4s", 0)])
        progress = []
        flasher(self.root, popen).flash("COM3", self.hex, progress=progress.append)
        self.assertIn(50, progress)

    def test_retry_57600_after_sync_failure(self) -> None:
        popen = FakePopen([("avrdude: stk500_getsync() attempt 10 of 10: not in sync: resp=0x00\n", 1), DONE])
        result = flasher(self.root, popen).flash("COM3", self.hex)
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, [115200, 57600])
        self.assertEqual(result.baudrate, 57600)
        self.assertEqual(popen.calls[0][popen.calls[0].index("-b") + 1], "115200")
        self.assertEqual(popen.calls[1][popen.calls[1].index("-b") + 1], "57600")

    def test_both_bauds_fail_sync(self) -> None:
        sync = ("avrdude: stk500_recv(): programmer is not responding\n", 1)
        result = flasher(self.root, FakePopen([sync, sync])).flash("COM3", self.hex)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_key, "firmware_error_sync")
        self.assertEqual(result.attempts, [115200, 57600])
        self.assertEqual(result.error_kwargs["bauds"], "115200 / 57600")

    def test_port_open_failure_is_port_busy_without_retry(self) -> None:
        for text in (
            'avrdude: ser_open(): can\'t open device "\\\\.\\COM3": Access is denied.\n'
            "avrdude: unable to open programmer arduino on port COM3\n",
            "avrdude error: cannot open port COM3: The system cannot find the file specified.\n",
        ):
            with self.subTest(text=text):
                popen = FakePopen([(text, 1)])
                result = flasher(self.root, popen).flash("COM3", self.hex)
                self.assertEqual(result.error_key, "firmware_error_port_busy")
                self.assertEqual(len(popen.calls), 1)
                self.assertFalse(ff.is_sync_failure(text))

    def test_non_sync_error_does_not_retry(self) -> None:
        popen = FakePopen([("avrdude: verification error, first mismatch at byte 0x0000\n", 1)])
        result = flasher(self.root, popen).flash("COM3", self.hex)
        self.assertEqual(result.error_key, "firmware_error_avrdude")
        self.assertEqual(len(popen.calls), 1)

    def test_waits_for_port_release_then_flashes(self) -> None:
        answers = [False, False, True]
        result = flasher(self.root, FakePopen([DONE]), port_probe=lambda _p: answers.pop(0)).flash("COM3", self.hex)
        self.assertTrue(result.ok)
        self.assertEqual(answers, [])

    def test_port_never_released(self) -> None:
        popen = FakePopen([])
        result = flasher(self.root, popen, port_probe=lambda _p: False, port_release_timeout_s=0.3).flash("COM3", self.hex)
        self.assertEqual(result.error_key, "firmware_error_port_busy")
        self.assertEqual(popen.calls, [])

    def test_timeout_kills_hung_avrdude(self) -> None:
        proc = HangingProc()
        popen = FakePopen([proc])
        result = flasher(self.root, popen, timeout_s=0.5).flash("COM3", self.hex)
        self.assertEqual(result.error_key, "firmware_error_timeout")
        self.assertTrue(proc.killed)
        self.assertEqual(len(popen.calls), 1)

    def test_cancel_kills_avrdude(self) -> None:
        proc = HangingProc()
        f = flasher(self.root, FakePopen([proc]))
        threading.Timer(0.3, f.cancel).start()
        self.assertEqual(f.flash("COM3", self.hex).error_key, "firmware_error_cancelled")
        self.assertTrue(proc.killed)

    def test_kill_active_processes(self) -> None:
        proc = HangingProc()
        f = flasher(self.root, FakePopen([proc]), timeout_s=10)
        done = threading.Event()
        threading.Thread(target=lambda: (f.flash("COM3", self.hex), done.set()), daemon=True).start()
        for _ in range(100):
            if ff._active_procs:
                break
            threading.Event().wait(0.05)
        self.assertEqual(ff.kill_active_processes(), 1)
        self.assertTrue(done.wait(5))
        self.assertTrue(proc.killed)

    def test_missing_tool_and_bad_input_never_launch(self) -> None:
        popen = FakePopen([])
        (self.root / "assets" / "tools" / "avrdude" / "avrdude.exe").unlink()
        f = flasher(self.root, popen)
        self.assertEqual(f.flash("COM3", self.hex).error_key, "firmware_error_tool_missing")
        self.assertEqual(f.flash("COM3", self.root / "x.hex").error_key, "firmware_error_file_missing")
        self.assertEqual(f.flash("", self.hex).error_key, "firmware_error_no_port")
        self.assertEqual(popen.calls, [])

    def test_launch_oserror(self) -> None:
        def boom(*_a, **_k):
            raise OSError("denied")
        self.assertEqual(flasher(self.root, boom).flash("COM3", self.hex).error_key, "firmware_error_launch")


class SessionTests(unittest.TestCase):
    def make(self, connected: bool):
        events, scheduled = [], []
        session = ff.FlashSession(
            "COM3",
            is_connected=lambda p: connected,
            disconnect=lambda p: events.append(("disconnect", p)),
            reconnect=lambda p: events.append(("reconnect", p)),
            schedule=lambda ms, fn: scheduled.append((ms, fn)),
        )
        return session, events, scheduled

    def test_connected_success_and_failure_same_delay(self) -> None:
        for ok in (True, False):
            session, events, scheduled = self.make(True)
            self.assertTrue(session.begin())
            done = []
            self.assertTrue(session.finish(ok, on_reconnect=lambda: done.append(1)))
            self.assertEqual(scheduled[0][0], ff.RECONNECT_DELAY_MS)
            scheduled[0][1]()
            self.assertEqual(events, [("disconnect", "COM3"), ("reconnect", "COM3")])
            self.assertEqual(done, [1])

    def test_not_connected(self) -> None:
        session, events, scheduled = self.make(False)
        self.assertFalse(session.begin())
        self.assertFalse(session.finish(True))
        self.assertEqual((events, scheduled), ([], []))


ZH_CONNECTED = (
    "Microsoft PnP 工具\r\n\r\n"
    "实例 ID:                USB\\VID_1A86&PID_7523\\5&2D5F1C0&0&2\r\n"
    "设备描述:               USB-SERIAL CH340\r\n"
    "状态:                   已启动\r\n\r\n"
    "实例 ID:                USB\\VID_046D&PID_C52B\\6&1\r\n"
)
ZH_PROBLEM = (
    "Microsoft PnP 工具\r\n\r\n"
    "实例 ID:                USB\\VID_1A86&PID_7523\\5&2D5F1C0&0&2\r\n"
    "状态:                   有问题\r\n"
    "问题代码:               28 (0x1C) [CM_PROB_FAILED_INSTALL]\r\n"
)
ZH_DRIVERS_WITH = "发布名称:     oem12.inf\r\n原始名称:     ch341ser.inf\r\n提供程序名称: wch.cn\r\n"
ZH_DRIVERS_WITHOUT = "发布名称:     oem3.inf\r\n原始名称:     ftdiport.inf\r\n"
FTDI_CONNECTED = "实例 ID:                USB\\VID_0403&PID_6001\\A1\r\n"


class DriverTests(Base):
    def check(self, drivers, connected="", problems="", ports=(), codec="gbk"):
        def run(args, **_kwargs):
            out = drivers if "/enum-drivers" in args else problems if "/problem" in args else connected
            return SimpleNamespace(stdout=out.encode(codec), returncode=0)

        with mock.patch.object(ff.sys, "platform", "win32"):
            return ff.check_ch340_driver(platform="win32", runner=run, serial_ports=lambda: list(ports))

    def test_non_windows_skips(self) -> None:
        run = mock.Mock()
        status = ff.check_ch340_driver(platform="linux", runner=run)
        self.assertFalse(status.checked or status.needs_install)
        run.assert_not_called()

    def test_no_wch_device_never_prompts(self) -> None:
        status = self.check(ZH_DRIVERS_WITHOUT, connected=FTDI_CONNECTED)
        self.assertFalse(status.device_present)
        self.assertFalse(status.needs_install)

    def test_chinese_pnputil_device_bound(self) -> None:
        status = self.check(ZH_DRIVERS_WITH, connected=ZH_CONNECTED, ports=["COM3"])
        self.assertTrue(status.device_present and status.installed)
        self.assertFalse(status.needs_install)

    def test_bound_via_other_inf_name(self) -> None:
        self.assertFalse(self.check(ZH_DRIVERS_WITHOUT, connected=ZH_CONNECTED, ports=["COM5"]).needs_install)

    def test_device_without_driver_prompts(self) -> None:
        status = self.check(ZH_DRIVERS_WITHOUT, connected=ZH_CONNECTED)
        self.assertTrue(status.device_present)
        self.assertFalse(status.installed)
        self.assertTrue(status.needs_install)

    def test_problem_device_prompts_even_with_inf(self) -> None:
        status = self.check(ZH_DRIVERS_WITH, connected=ZH_CONNECTED, problems=ZH_PROBLEM)
        self.assertTrue(status.problem_device and status.needs_install)

    def test_utf16_pnputil_output(self) -> None:
        self.assertTrue(self.check(ZH_DRIVERS_WITHOUT, connected=ZH_CONNECTED, codec="utf-16").needs_install)

    def test_old_pnputil_notes_limitation(self) -> None:
        usage = "PnPUtil\r\nUsage: pnputil [-f | -i] [ -? | -a | -d | -e ] <INF name>\r\n"
        status = self.check(usage, connected=usage, problems=usage, codec="utf-8")
        self.assertFalse(status.needs_install)
        self.assertIn("2004", status.note)

    def elevate_script(self, *outcomes):
        calls, items = [], list(outcomes)

        def elevate(exe, params, hwnd=None):
            calls.append((Path(exe).name, params, hwnd))
            return items.pop(0)
        return elevate, calls

    def test_install_silent_ok_passes_hwnd(self) -> None:
        elevate, calls = self.elevate_script(ff.ElevateResult("ok", 0))
        status = ff.install_ch340_driver(root=self.root, hwnd=1234, elevate=elevate,
                                         check=lambda: ff.DriverStatus(True, installed=True, device_present=True))
        self.assertTrue(status.installed)
        self.assertEqual(calls, [("CH341SER.EXE", "/S", 1234)])

    def test_install_silent_then_interactive_fallback(self) -> None:
        statuses = [ff.DriverStatus(True, installed=False), ff.DriverStatus(True, installed=True)]
        elevate, calls = self.elevate_script(ff.ElevateResult("ok", 1), ff.ElevateResult("ok", 0))
        self.assertTrue(ff.install_ch340_driver(root=self.root, elevate=elevate, check=lambda: statuses.pop(0)).installed)
        self.assertEqual([c[1] for c in calls], ["/S", ""])

    def test_install_nonzero_exit_and_still_missing_fails(self) -> None:
        elevate, _ = self.elevate_script(ff.ElevateResult("ok", 2), ff.ElevateResult("ok", 5))
        with self.assertRaises(ff.FirmwareFlashError) as ctx:
            ff.install_ch340_driver(root=self.root, elevate=elevate, check=lambda: ff.DriverStatus(True, installed=False))
        self.assertEqual((ctx.exception.key, ctx.exception.kwargs["code"]), ("ch340_install_failed", 5))

    def test_install_uac_declined_1223(self) -> None:
        elevate, _ = self.elevate_script(ff.ElevateResult("cancelled", error=ff.ERROR_CANCELLED))
        check = mock.Mock()
        with self.assertRaises(ff.FirmwareFlashError) as ctx:
            ff.install_ch340_driver(root=self.root, elevate=elevate, check=check)
        self.assertEqual(ctx.exception.key, "ch340_install_cancelled")
        check.assert_not_called()

    def test_install_still_running_259(self) -> None:
        elevate, _ = self.elevate_script(ff.ElevateResult("timeout", exit_code=ff.STILL_ACTIVE))
        check = mock.Mock()
        with self.assertRaises(ff.FirmwareFlashError) as ctx:
            ff.install_ch340_driver(root=self.root, elevate=elevate, check=check)
        self.assertEqual(ctx.exception.key, "ch340_install_still_running")
        check.assert_not_called()

    def test_install_launch_failure(self) -> None:
        elevate, _ = self.elevate_script(ff.ElevateResult("failed", error=2))
        with self.assertRaises(ff.FirmwareFlashError) as ctx:
            ff.install_ch340_driver(root=self.root, elevate=elevate, check=mock.Mock())
        self.assertEqual(ctx.exception.key, "ch340_install_failed")


class FakeRouter:
    def __init__(self) -> None:
        self.logs: list[str] = []

    def emit_log(self, message: str) -> None:
        self.logs.append(message)


class FakeHome(tk.Frame):
    _connect_previous_thread = None

    def __init__(self, master, connected: bool) -> None:
        super().__init__(master)
        self.language = "zh"
        self.ctx = SimpleNamespace(serial=FakeRouter(), colors={"panel": "#FFFFFF", "text": "#111827"},
                                   state=SimpleNamespace(theme="light"))
        self.port_var = tk.StringVar(self, value="COM3")
        self.port_combo = SimpleNamespace(cget=lambda _k: ("COM3",))
        self.connected = connected
        self.events: list[tuple] = []

    def firmware_lock(self, port):
        self.events.append(("lock", port))
        return True

    def firmware_unlock(self, port=None):
        self.events.append(("unlock", port))

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
    install_outcome: Exception | None = None

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
        self.prompts, self.installs = [], []
        popen = FakePopen(popen_script or [DONE])
        outcome = self.install_outcome

        def ask(_title, message, **_kw):
            self.prompts.append(message)
            return answer

        def install(hwnd=None):
            self.installs.append(hwnd)
            if outcome is not None:
                raise outcome
            return ff.DriverStatus(True, installed=True)

        with mock.patch.object(dlg, "post_to_ui", lambda _w, fn, *a: fn(*a)):
            dialog = dlg.FirmwareFlashDialog(
                home,
                flasher=flasher(self.root, popen),
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

    def missing_driver(self):
        return ff.DriverStatus(True, installed=False, device_present=True)

    def test_requires_hex_choice_no_default(self) -> None:
        dlg, home, dialog = self.make_dialog()
        self.assertEqual(dialog.hex_var.get(), "")
        started, warn = self.run_start(dlg, dialog)
        self.assertFalse(started)
        warn.assert_called_once()
        self.assertEqual((self.popen.calls, home.events), ([], []))

    def test_invalid_hex_rejected_before_disconnect(self) -> None:
        dlg, home, dialog = self.make_dialog()
        bad = self.root / "bad.hex"
        bad.write_text("void loop(){}")
        dialog.hex_var.set(str(bad))
        self.assertFalse(self.run_start(dlg, dialog)[0])
        self.assertEqual(home.events, [])

    def test_full_flow_lock_disconnect_flash_unlock_reconnect(self) -> None:
        dlg, home, dialog = self.make_dialog(connected=True)
        dialog.hex_var.set(str(self.hex))
        self.assertTrue(self.run_start(dlg, dialog)[0])
        self.assertTrue(dialog.last_result.ok)
        self.assertEqual(
            home.events,
            [("lock", "COM3"), ("release", "COM3"), ("unlock", "COM3"),
             ("after", ff.RECONNECT_DELAY_MS), ("restore", "COM3")],
        )
        self.assertFalse(dialog.busy)
        self.assertTrue(any("[flash]" in line for line in home.ctx.serial.logs))
        self.assertEqual(dlg.last_hex_path(), str(self.hex))
        self.assertIn("COM3", dialog.status_var.get())

    def test_driver_missing_prompt_yes_installs_then_flashes(self) -> None:
        dlg, home, dialog = self.make_dialog(connected=False, driver=self.missing_driver())
        dialog.hex_var.set(str(self.hex))
        self.run_start(dlg, dialog)
        self.assertEqual(self.prompts, ["未检测到 CH340 驱动，是否现在安装？"])
        self.assertEqual(len(self.installs), 1)
        self.assertEqual(len(self.popen.calls), 1)
        self.assertEqual(home.events, [("lock", "COM3"), ("unlock", "COM3")])

    def test_driver_missing_prompt_no_skips_install(self) -> None:
        dlg, home, dialog = self.make_dialog(driver=self.missing_driver(), answer=False)
        dialog.hex_var.set(str(self.hex))
        self.run_start(dlg, dialog)
        self.assertEqual((len(self.prompts), self.installs, len(self.popen.calls)), (1, [], 1))

    def test_no_wch_device_no_prompt(self) -> None:
        dlg, home, dialog = self.make_dialog(driver=ff.DriverStatus(True, installed=False, device_present=False))
        dialog.hex_var.set(str(self.hex))
        self.run_start(dlg, dialog)
        self.assertEqual(self.prompts, [])

    def test_installer_still_running_aborts_flash(self) -> None:
        self.install_outcome = ff.FirmwareFlashError("ch340_install_still_running")
        dlg, home, dialog = self.make_dialog(connected=True, driver=self.missing_driver())
        dialog.hex_var.set(str(self.hex))
        self.run_start(dlg, dialog)
        self.assertEqual(self.popen.calls, [])
        self.assertNotIn(("release", "COM3"), home.events)
        self.assertIn(("unlock", "COM3"), home.events)
        self.assertFalse(dialog.busy)

    def test_uac_cancel_still_flashes(self) -> None:
        self.install_outcome = ff.FirmwareFlashError("ch340_install_cancelled")
        dlg, home, dialog = self.make_dialog(connected=False, driver=self.missing_driver())
        dialog.hex_var.set(str(self.hex))
        self.run_start(dlg, dialog)
        self.assertEqual(len(self.popen.calls), 1)

    def test_cancel_button_and_logs_posted_to_ui(self) -> None:
        dlg, home, dialog = self.make_dialog()
        self.assertEqual(str(dialog.cancel_btn.cget("state")), "disabled")
        dialog._set_busy(True)
        self.assertEqual(str(dialog.cancel_btn.cget("state")), "normal")
        dialog.cancel()
        self.assertTrue(dialog._flasher.cancel_event.is_set())
        dialog._set_busy(False)
        posted = []
        with mock.patch.object(dlg, "post_to_ui", lambda _w, fn, *a: posted.append(fn)):
            dialog._log("x")
        self.assertEqual(home.ctx.serial.logs, [])  # emit_log never called off the UI thread
        self.assertEqual(len(posted), 2)

    def test_close_while_busy_cancels(self) -> None:
        dlg, home, dialog = self.make_dialog()
        dialog._set_busy(True)
        dialog.close()  # ask_yes_no answers True
        self.assertTrue(dialog._flasher.cancel_event.is_set())
        self.assertTrue(dialog._closed)

    def test_failure_reconnects_and_reports(self) -> None:
        dlg, home, dialog = self.make_dialog(connected=True, popen_script=[("avrdude: verification error\n", 1)])
        dialog.hex_var.set(str(self.hex))
        self.run_start(dlg, dialog)
        self.assertFalse(dialog.last_result.ok)
        self.assertEqual(home.events[1], ("release", "COM3"))
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

        with mock.patch.object(sys, "_MEIPASS", "/frozen", create=True):
            self.assertEqual(ff.avrdude_paths()[0], Path("/frozen/assets/tools/avrdude/avrdude.exe"))

    def test_language_keys_match(self) -> None:
        from config.languages import LANGUAGES

        keys = [k for k in LANGUAGES["zh"] if k.startswith(("firmware_", "ch340_", "flash_firmware"))]
        self.assertGreater(len(keys), 30)
        for key in keys:
            self.assertIn(key, LANGUAGES["en"])
        self.assertIn("{bauds}", LANGUAGES["zh"]["firmware_error_sync"])


if __name__ == "__main__":
    unittest.main()
