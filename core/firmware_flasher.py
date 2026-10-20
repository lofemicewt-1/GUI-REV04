"""Flash an Intel-HEX firmware onto an Arduino Nano with the bundled avrdude.

Everything here is UI-free and safe to run on a worker thread.  The Tk side
(``pages/firmware_flash_dialog.py``) only posts results back with
``post_to_ui``.

* ``validate_hex``          - existence + Intel-HEX record/checksum check.
* ``build_avrdude_args``    - fixed Nano command line (ATmega328P, arduino).
* ``FirmwareFlasher.flash`` - runs avrdude at 115200 (new/Optiboot bootloader)
  and retries once at 57600 (old bootloader) when the first try fails to sync.
* ``FlashSession``          - disconnect the app's connection on that port
  before flashing and restore it afterwards (after the board reset delay).
* CH340 helpers             - Windows-only driver detection and elevated
  launch of the bundled WCH installer.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from build_support.bundle_paths import resource_root

NANO_PART = "m328p"
NANO_PROGRAMMER = "arduino"
BAUD_OLD_BOOTLOADER = 57600
BAUD_NEW_BOOTLOADER = 115200
FLASH_BAUDRATES = (BAUD_NEW_BOOTLOADER, BAUD_OLD_BOOTLOADER)
RECONNECT_DELAY_MS = 2000
MAX_HEX_BYTES = 4 * 1024 * 1024

CH340_VID_PID = "VID_1A86&PID_7523"
CH340_INF_NAME = "ch341ser.inf"
CH340_SILENT_ARGS = "/S"

_IS_WINDOWS = sys.platform.startswith("win")
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_SYNC_FAILURE_PATTERNS = (
    "not in sync",
    "stk500_getsync",
    "stk500_recv",
    "programmer is not responding",
    "unable to open programmer",
    "cannot get into sync",
)
_PROGRESS_RE = re.compile(r"^(Reading|Writing)\s*\|[#\s]*\|\s*(\d{1,3})%")


class FirmwareFlashError(Exception):
    """User-facing problem (missing file, bad hex, missing tool)."""

    def __init__(self, key: str, **kwargs: Any) -> None:
        super().__init__(key)
        self.key = key
        self.kwargs = kwargs


# -- tool paths ---------------------------------------------------------------
def tools_dir(root: str | Path | None = None) -> Path:
    return Path(root if root is not None else resource_root()) / "assets" / "tools"


def avrdude_paths(root: str | Path | None = None) -> tuple[Path, Path]:
    base = tools_dir(root) / "avrdude"
    return base / "avrdude.exe", base / "avrdude.conf"


def ch340_installer_path(root: str | Path | None = None) -> Path:
    return tools_dir(root) / "ch340" / "CH341SER.EXE"


def resolve_avrdude(root: str | Path | None = None) -> tuple[Path, Path]:
    exe, conf = avrdude_paths(root)
    if not exe.is_file():
        raise FirmwareFlashError("firmware_error_tool_missing", path=str(exe))
    if not conf.is_file():
        raise FirmwareFlashError("firmware_error_tool_missing", path=str(conf))
    return exe, conf


# -- hex validation -----------------------------------------------------------
def validate_hex(path: str | Path) -> Path:
    """Return the path if it is a readable Intel-HEX file, else raise."""
    text_path = str(path or "").strip()
    if not text_path:
        raise FirmwareFlashError("firmware_error_no_file")
    hex_path = Path(text_path)
    if not hex_path.is_file():
        raise FirmwareFlashError("firmware_error_file_missing", path=str(hex_path))
    try:
        if hex_path.stat().st_size > MAX_HEX_BYTES:
            raise FirmwareFlashError("firmware_error_invalid_hex", path=str(hex_path), detail="file too large")
        raw = hex_path.read_text(encoding="ascii", errors="strict")
    except UnicodeDecodeError:
        raise FirmwareFlashError("firmware_error_invalid_hex", path=str(hex_path), detail="not ASCII text")
    except OSError as exc:
        raise FirmwareFlashError("firmware_error_file_missing", path=str(hex_path)) from exc

    data_records = 0
    saw_eof = False
    for number, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if saw_eof:
            raise FirmwareFlashError("firmware_error_invalid_hex", path=str(hex_path), detail=f"data after EOF (line {number})")
        if not line.startswith(":") or len(line) < 11 or (len(line) - 1) % 2:
            raise FirmwareFlashError("firmware_error_invalid_hex", path=str(hex_path), detail=f"line {number}")
        try:
            record = bytes.fromhex(line[1:])
        except ValueError:
            raise FirmwareFlashError("firmware_error_invalid_hex", path=str(hex_path), detail=f"line {number}")
        if len(record) != record[0] + 5:
            raise FirmwareFlashError("firmware_error_invalid_hex", path=str(hex_path), detail=f"length, line {number}")
        if sum(record) & 0xFF:
            raise FirmwareFlashError("firmware_error_invalid_hex", path=str(hex_path), detail=f"checksum, line {number}")
        rtype = record[3]
        if rtype == 0x00:
            data_records += 1
        elif rtype == 0x01:
            saw_eof = True
        elif rtype > 0x05:
            raise FirmwareFlashError("firmware_error_invalid_hex", path=str(hex_path), detail=f"record type, line {number}")
    if not saw_eof or data_records == 0:
        raise FirmwareFlashError("firmware_error_invalid_hex", path=str(hex_path), detail="no data / no EOF record")
    return hex_path


# -- avrdude ------------------------------------------------------------------
def avrdude_port_name(port: str) -> str:
    port = str(port).strip()
    if _IS_WINDOWS and port.upper().startswith("COM"):
        try:
            if int(port[3:]) >= 10:
                return rf"\\.\{port}"
        except ValueError:
            pass
    return port


def build_avrdude_args(exe: str | Path, conf: str | Path, port: str, hex_path: str | Path, baudrate: int) -> list[str]:
    # ``:i`` forces Intel-HEX; a Windows drive letter in the path is fine
    # because avrdude splits the -U operand from the right.
    return [
        str(exe),
        "-C", str(conf),
        "-c", NANO_PROGRAMMER,
        "-p", NANO_PART,
        "-P", avrdude_port_name(port),
        "-b", str(int(baudrate)),
        "-D",
        "-U", f"flash:w:{hex_path}:i",
    ]


def is_sync_failure(output: str) -> bool:
    lowered = output.lower()
    return any(pattern in lowered for pattern in _SYNC_FAILURE_PATTERNS)


@dataclass
class FlashResult:
    ok: bool
    baudrate: int | None = None
    attempts: list[int] = field(default_factory=list)
    returncode: int | None = None
    error_key: str = ""
    error_kwargs: dict[str, Any] = field(default_factory=dict)
    output: str = ""


class FirmwareFlasher:
    def __init__(
        self,
        *,
        root: str | Path | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        baudrates: Sequence[int] = FLASH_BAUDRATES,
    ) -> None:
        self.root = root
        self._popen = popen
        self.baudrates = tuple(baudrates)

    def _run_once(
        self,
        args: list[str],
        log: Callable[[str], None],
        progress: Callable[[int], None],
        phase_offset: int,
        phases: int,
    ) -> tuple[int, str]:
        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if _CREATE_NO_WINDOW:
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        proc = self._popen(args, **kwargs)
        lines: list[str] = []
        stream = proc.stdout
        buffer = ""
        # avrdude draws its progress bar with '#' characters without newlines;
        # read char-wise so the bar and the log stay live.
        while True:
            chunk = stream.read(1) if stream is not None else ""
            if not chunk:
                break
            if chunk in "\r\n":
                line = buffer.strip()
                buffer = ""
                if line:
                    lines.append(line)
                    log(line)
                continue
            buffer += chunk
            match = _PROGRESS_RE.match(buffer.strip())
            if match:
                percent = int(match.group(2))
                # Writing = phase 0, Reading (verify) = phase 1 of each try.
                phase = phase_offset + (1 if match.group(1) == "Reading" else 0)
                progress(min(100, int((phase * 100 + percent) / phases)))
        if buffer.strip():
            lines.append(buffer.strip())
            log(buffer.strip())
        returncode = proc.wait()
        return int(returncode if returncode is not None else -1), "\n".join(lines)

    def flash(
        self,
        port: str,
        hex_path: str | Path,
        *,
        log: Callable[[str], None] = lambda _line: None,
        progress: Callable[[int], None] = lambda _value: None,
    ) -> FlashResult:
        result = FlashResult(ok=False)
        try:
            if not str(port or "").strip():
                raise FirmwareFlashError("firmware_error_no_port")
            hex_file = validate_hex(hex_path)
            exe, conf = resolve_avrdude(self.root)
        except FirmwareFlashError as exc:
            result.error_key, result.error_kwargs = exc.key, dict(exc.kwargs)
            return result

        outputs: list[str] = []
        progress(0)
        for baudrate in self.baudrates:
            result.attempts.append(baudrate)
            args = build_avrdude_args(exe, conf, port, hex_file, baudrate)
            log("$ " + subprocess.list2cmdline(args))
            try:
                code, output = self._run_once(args, log, progress, 0, 2)
            except OSError as exc:
                result.error_key = "firmware_error_launch"
                result.error_kwargs = {"detail": str(exc)}
                result.output = "\n".join(outputs)
                return result
            outputs.append(output)
            result.returncode = code
            if code == 0:
                result.ok = True
                result.baudrate = baudrate
                result.output = "\n".join(outputs)
                progress(100)
                return result
            if not is_sync_failure(output):
                break  # real error (verify failed, port busy ...): no retry
            log(f"avrdude: no sync at {baudrate} baud")
        result.output = "\n".join(outputs)
        result.error_key = "firmware_error_sync" if is_sync_failure(result.output) else "firmware_error_avrdude"
        result.error_kwargs = {"code": result.returncode}
        return result


# -- connection hand-off -------------------------------------------------------
class FlashSession:
    """Release the app's connection on ``port`` and restore it afterwards.

    ``is_connected(port)``/``disconnect(port)``/``reconnect(port)`` and
    ``schedule(ms, fn)`` are supplied by the UI so the existing Home page
    connect/disconnect bookkeeping is reused (all called on the Tk thread).
    """

    def __init__(
        self,
        port: str,
        *,
        is_connected: Callable[[str], bool],
        disconnect: Callable[[str], None],
        reconnect: Callable[[str], None],
        schedule: Callable[[int, Callable[[], None]], Any],
        reconnect_delay_ms: int = RECONNECT_DELAY_MS,
    ) -> None:
        self.port = port
        self._is_connected = is_connected
        self._disconnect = disconnect
        self._reconnect = reconnect
        self._schedule = schedule
        self.reconnect_delay_ms = reconnect_delay_ms
        self.was_connected = False

    def begin(self) -> bool:
        self.was_connected = bool(self._is_connected(self.port))
        if self.was_connected:
            self._disconnect(self.port)
        return self.was_connected

    def finish(self, ok: bool, on_reconnect: Callable[[], None] | None = None) -> bool:
        """Schedule a reconnect if the port was connected before (success or not).

        After success the board resets into the new firmware, so wait for it.
        """
        if not self.was_connected:
            return False

        def _restore() -> None:
            self._reconnect(self.port)
            if on_reconnect is not None:
                on_reconnect()

        self._schedule(self.reconnect_delay_ms if ok else 500, _restore)
        return True


# -- CH340 driver (Windows) ------------------------------------------------------
def _run_quiet(args: list[str], runner: Callable[..., Any]) -> str:
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "errors": "replace", "timeout": 20}
    if _CREATE_NO_WINDOW:
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    try:
        completed = runner(args, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return ""
    return str(getattr(completed, "stdout", "") or "")


def _pnputil() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    # A 32-bit process would be redirected away from the 64-bit pnputil.
    sysnative = Path(system_root) / "Sysnative" / "pnputil.exe"
    return str(sysnative) if sysnative.is_file() else "pnputil"


@dataclass
class DriverStatus:
    checked: bool
    installed: bool = True
    problem_device: bool = False

    @property
    def needs_install(self) -> bool:
        return self.checked and (not self.installed or self.problem_device)


def check_ch340_driver(
    *,
    platform: str | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> DriverStatus:
    """Windows: is ch341ser.inf in the driver store; is a CH340 device unbound?

    Non-Windows returns ``checked=False`` (the kernel ships ch341 support).
    If pnputil itself cannot run we do not nag: report installed.
    """
    if not (platform or sys.platform).startswith("win"):
        return DriverStatus(checked=False)
    tool = _pnputil()
    drivers = _run_quiet([tool, "/enum-drivers"], runner)
    if not drivers.strip():
        return DriverStatus(checked=False)
    installed = CH340_INF_NAME in drivers.lower()
    # ``/problem`` lists only devices with a problem code (needs Win10 2004+;
    # older pnputil prints usage text which simply will not match).
    problems = _run_quiet([tool, "/enum-devices", "/problem"], runner)
    problem_device = CH340_VID_PID.lower() in problems.lower()
    return DriverStatus(checked=True, installed=installed, problem_device=problem_device)


def run_elevated_and_wait(exe: str | Path, params: str = "", timeout_ms: int = 10 * 60 * 1000) -> int | None:
    """ShellExecuteExW 'runas' and wait.  Returns exit code, or None if refused."""
    if not _IS_WINDOWS:
        raise OSError("elevation is only supported on Windows")
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_SHOWNORMAL = 1
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = str(exe)
    info.lpParameters = params or None
    info.lpDirectory = str(Path(exe).parent)
    info.nShow = SW_SHOWNORMAL
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        return None  # UAC declined (ERROR_CANCELLED) or launch failure
    if not info.hProcess:
        return 0
    try:
        kernel32.WaitForSingleObject(info.hProcess, int(timeout_ms))
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def install_ch340_driver(
    *,
    root: str | Path | None = None,
    elevate: Callable[..., int | None] = run_elevated_and_wait,
    check: Callable[[], DriverStatus] = check_ch340_driver,
) -> DriverStatus:
    """Run the bundled WCH installer elevated, then re-check.

    Tries the silent switch first; if the driver is still missing (switch
    unsupported by that installer build) runs the installer UI once.
    Raises ``FirmwareFlashError`` when the installer is missing or UAC is
    declined.
    """
    installer = ch340_installer_path(root)
    if not installer.is_file():
        raise FirmwareFlashError("firmware_error_tool_missing", path=str(installer))
    if elevate(installer, CH340_SILENT_ARGS) is None:
        raise FirmwareFlashError("ch340_install_cancelled")
    status = check()
    if status.checked and not status.installed:
        if elevate(installer, "") is None:
            raise FirmwareFlashError("ch340_install_cancelled")
        status = check()
    return status
