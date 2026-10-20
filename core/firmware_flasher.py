"""Flash an Intel-HEX firmware onto an Arduino Nano with the bundled avrdude.

Everything here is UI-free and safe to run on a worker thread.  The Tk side
(``pages/firmware_flash_dialog.py``) only posts results back with
``post_to_ui``.

* ``validate_hex``          - existence + Intel-HEX record/checksum check.
* ``build_avrdude_args``    - fixed Nano command line (ATmega328P, arduino).
* ``FirmwareFlasher.flash`` - waits until the COM port is really free, copies
  the hex + avrdude.conf to an ASCII-named work dir, runs avrdude at 115200
  (new/Optiboot bootloader) and retries once at 57600 (old bootloader) only
  when the first try fails to *sync*.  Every run has an overall timeout and
  can be cancelled; the process is killed in both cases.
* ``FlashSession``          - disconnect the app's connection on that port
  before flashing and restore it afterwards (after the board reset delay).
* CH340 helpers             - Windows-only driver detection and elevated
  launch of the bundled WCH installer.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from core.resource_paths import resource_root

NANO_PART = "m328p"
NANO_PROGRAMMER = "arduino"
BAUD_OLD_BOOTLOADER = 57600
BAUD_NEW_BOOTLOADER = 115200
FLASH_BAUDRATES = (BAUD_NEW_BOOTLOADER, BAUD_OLD_BOOTLOADER)
RECONNECT_DELAY_MS = 2000
RECONNECT_RETRIES = 1
MAX_HEX_BYTES = 4 * 1024 * 1024
FLASH_TIMEOUT_S = 120.0
PORT_RELEASE_TIMEOUT_S = 3.0
PORT_RELEASE_POLL_S = 0.25

CH340_VID = "VID_1A86"
CH340_VID_INT = 0x1A86
CH340_INF_NAME = "ch341ser.inf"
CH340_SILENT_ARGS = "/S"
INSTALLER_TIMEOUT_MS = 10 * 60 * 1000

_IS_WINDOWS = sys.platform.startswith("win")
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Port could not be opened at all: busy / denied / missing.  Never retried at
# another baud rate, the user must free or pick the port instead.
_PORT_OPEN_FAILURE_PATTERNS = (
    "can't open device",
    "cannot open device",
    "can't open port",
    "cannot open port",
    "could not open port",
    "unable to open port",
    "unable to open programmer",
    "access is denied",
    "ser_open",
    "the system cannot find the file specified",
)
# Port opened but the bootloader did not answer (wrong baud / no reset).
_SYNC_FAILURE_PATTERNS = (
    "not in sync",
    "stk500_getsync",
    "stk500_recv",
    "programmer is not responding",
    "cannot get into sync",
)
_PROGRESS_RE = re.compile(r"(Reading|Writing)\s*\|[#\s]*\|\s*(\d{1,3})%")


class FirmwareFlashError(Exception):
    """User-facing problem (missing file, bad hex, missing tool)."""

    def __init__(self, key: str, **kwargs: Any) -> None:
        super().__init__(key)
        self.key = key
        self.kwargs = kwargs


# -- console decoding -----------------------------------------------------------
def decode_console(data: bytes, *, platform: str | None = None) -> str:
    """Decode child-process output.

    Windows console tools write the OEM code page to a pipe (``pnputil`` may
    also write UTF-16); avrdude (an ANSI program) writes the ANSI code page.
    Try UTF-16 when the bytes clearly are, then strict UTF-8, then oem/mbcs.
    """
    if not data:
        return ""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(data) >= 4 and data[1:4:2] == b"\x00\x00"):
        return data.decode("utf-16", errors="replace").lstrip("\ufeff")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if (platform or sys.platform).startswith("win"):
        for codec in ("oem", "mbcs"):
            try:
                return data.decode(codec)
            except (LookupError, UnicodeDecodeError):
                continue
    return data.decode("utf-8", errors="replace")


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
WORK_HEX_NAME = "firmware.hex"
WORK_CONF_NAME = "avrdude.conf"


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
    # The flasher passes *relative* ASCII names for conf/hex and runs avrdude
    # with cwd set to the work dir: avrdude 8 opens files with the ANSI API,
    # so non-ASCII directories never reach it.
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


def is_port_open_failure(output: str) -> bool:
    lowered = output.lower()
    return any(pattern in lowered for pattern in _PORT_OPEN_FAILURE_PATTERNS)


def is_sync_failure(output: str) -> bool:
    if is_port_open_failure(output):
        return False
    lowered = output.lower()
    return any(pattern in lowered for pattern in _SYNC_FAILURE_PATTERNS)


def _default_port_probe(port: str) -> bool:
    """True when ``port`` can be opened (and is closed again) right now."""
    try:
        import serial  # type: ignore
    except Exception:
        return True  # cannot probe: let avrdude report
    try:
        handle = serial.Serial(port=avrdude_port_name(port), baudrate=115200, timeout=0.1)
    except Exception as exc:
        text = str(exc).lower()
        if "filenotfound" in text or "could not find" in text or "no such file" in text:
            return True  # missing port: avrdude reports it clearly
        return False
    try:
        handle.close()
    except Exception:
        pass
    return True


def wait_port_free(
    port: str,
    *,
    probe: Callable[[str], bool] = _default_port_probe,
    timeout_s: float = PORT_RELEASE_TIMEOUT_S,
    poll_s: float = PORT_RELEASE_POLL_S,
    cancel: threading.Event | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Retry opening ``port`` until it is released (Windows closes lazily)."""
    deadline = clock() + max(0.0, timeout_s)
    while True:
        if cancel is not None and cancel.is_set():
            return False
        if probe(port):
            return True
        if clock() >= deadline:
            return False
        sleep(poll_s)


def make_ascii_workdir(hex_path: Path, conf: Path) -> Path:
    """Copy hex+conf into a fresh temp dir under ASCII file names."""
    work = Path(tempfile.mkdtemp(prefix="piezo_flash_"))
    shutil.copyfile(hex_path, work / WORK_HEX_NAME)
    shutil.copyfile(conf, work / WORK_CONF_NAME)
    return work


# Live avrdude processes, so the app can kill them when it closes.
_active_lock = threading.Lock()
_active_procs: set[Any] = set()


def _kill(proc: Any) -> None:
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass


def kill_active_processes() -> int:
    """Kill every running avrdude started by this module (app shutdown)."""
    with _active_lock:
        procs = list(_active_procs)
    for proc in procs:
        _kill(proc)
    return len(procs)


@dataclass
class FlashResult:
    ok: bool
    baudrate: int | None = None
    attempts: list[int] = field(default_factory=list)
    returncode: int | None = None
    error_key: str = ""
    error_kwargs: dict[str, Any] = field(default_factory=dict)
    output: str = ""


class _Stopped(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class FirmwareFlasher:
    def __init__(
        self,
        *,
        root: str | Path | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        baudrates: Sequence[int] = FLASH_BAUDRATES,
        timeout_s: float = FLASH_TIMEOUT_S,
        port_probe: Callable[[str], bool] = _default_port_probe,
        port_release_timeout_s: float = PORT_RELEASE_TIMEOUT_S,
    ) -> None:
        self.root = root
        self._popen = popen
        self.baudrates = tuple(baudrates)
        self.timeout_s = float(timeout_s)
        self._port_probe = port_probe
        self.port_release_timeout_s = port_release_timeout_s
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        """Any thread: stop the running flash (kills avrdude)."""
        self.cancel_event.set()

    @staticmethod
    def _report_progress(text: str, progress: Callable[[int], None]) -> None:
        match = _PROGRESS_RE.search(text)
        if match:
            percent = int(match.group(2))
            # Writing = first half, Reading (verify) = second half.
            phase = 1 if match.group(1) == "Reading" else 0
            progress(min(100, int((phase * 100 + percent) / 2)))

    def _run_once(
        self,
        args: list[str],
        cwd: Path,
        deadline: float,
        log: Callable[[str], None],
        progress: Callable[[int], None],
    ) -> tuple[int, str]:
        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "cwd": str(cwd),
            "bufsize": 0,
        }
        if _CREATE_NO_WINDOW:
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        proc = self._popen(args, **kwargs)
        with _active_lock:
            _active_procs.add(proc)
        chunks: "queue.Queue[bytes | None]" = queue.Queue()

        def reader() -> None:
            stream = proc.stdout
            try:
                while stream is not None:
                    # avrdude draws its progress bar with '#' and no newline;
                    # small reads keep the bar and the log live.
                    data = stream.read(64) if not hasattr(stream, "read1") else stream.read1(256)
                    if not data:
                        break
                    chunks.put(data)
            except (OSError, ValueError):
                pass
            finally:
                chunks.put(None)

        threading.Thread(target=reader, daemon=True, name="avrdude-output").start()
        lines: list[str] = []
        pending = b""
        stopped: str = ""
        try:
            while True:
                if self.cancel_event.is_set():
                    stopped = "firmware_error_cancelled"
                elif time.monotonic() >= deadline:
                    stopped = "firmware_error_timeout"
                if stopped:
                    _kill(proc)
                    break
                try:
                    data = chunks.get(timeout=0.1)
                except queue.Empty:
                    continue
                if data is None:
                    break
                pending += data
                while True:
                    cut = min((i for i in (pending.find(b"\n"), pending.find(b"\r")) if i >= 0), default=-1)
                    if cut < 0:
                        break
                    line = decode_console(pending[:cut]).strip()
                    pending = pending[cut + 1 :]
                    if line:
                        lines.append(line)
                        log(line)
                        self._report_progress(line, progress)
                self._report_progress(decode_console(pending), progress)
            tail = decode_console(pending).strip()
            if tail:
                lines.append(tail)
                log(tail)
            if stopped:
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                raise _Stopped(stopped)
            try:
                remaining = max(0.5, deadline - time.monotonic())
                returncode = proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                _kill(proc)
                raise _Stopped("firmware_error_timeout")
            return int(returncode if returncode is not None else -1), "\n".join(lines)
        finally:
            with _active_lock:
                _active_procs.discard(proc)
            for stream in (getattr(proc, "stdout", None),):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass

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

        deadline = time.monotonic() + self.timeout_s
        if not wait_port_free(
            port,
            probe=self._port_probe,
            timeout_s=self.port_release_timeout_s,
            cancel=self.cancel_event,
        ):
            key = "firmware_error_cancelled" if self.cancel_event.is_set() else "firmware_error_port_busy"
            result.error_key, result.error_kwargs = key, {"port": port}
            return result

        try:
            work = make_ascii_workdir(hex_file, conf)
        except OSError as exc:
            result.error_key, result.error_kwargs = "firmware_error_launch", {"detail": str(exc)}
            return result

        outputs: list[str] = []
        progress(0)
        try:
            for baudrate in self.baudrates:
                result.attempts.append(baudrate)
                args = build_avrdude_args(exe, WORK_CONF_NAME, port, WORK_HEX_NAME, baudrate)
                log("$ " + subprocess.list2cmdline(args) + f"   (hex: {hex_file})")
                try:
                    code, output = self._run_once(args, work, deadline, log, progress)
                except _Stopped as stop:
                    result.error_key = stop.key
                    result.error_kwargs = {"seconds": int(self.timeout_s), "port": port}
                    result.output = "\n".join(outputs)
                    return result
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
                    break  # port busy, verify failed ...: another baud won't help
                log(f"avrdude: no sync at {baudrate} baud")
        finally:
            shutil.rmtree(work, ignore_errors=True)

        result.output = "\n".join(outputs)
        last = outputs[-1] if outputs else ""
        if is_port_open_failure(last):
            result.error_key, result.error_kwargs = "firmware_error_port_busy", {"port": port}
        elif is_sync_failure(last):
            result.error_key = "firmware_error_sync"
            result.error_kwargs = {"bauds": " / ".join(str(b) for b in result.attempts)}
        else:
            result.error_key, result.error_kwargs = "firmware_error_avrdude", {"code": result.returncode}
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

        Same delay either way: after success the board resets into the new
        firmware; after a failure it may still sit in the bootloader or the
        port may only just have been released.  (The UI retries once.)
        """
        if not self.was_connected:
            return False

        def _restore() -> None:
            self._reconnect(self.port)
            if on_reconnect is not None:
                on_reconnect()

        self._schedule(self.reconnect_delay_ms, _restore)
        return True


# -- CH340 driver (Windows) ------------------------------------------------------
def _run_quiet(args: list[str], runner: Callable[..., Any]) -> str:
    kwargs: dict[str, Any] = {"capture_output": True, "timeout": 20}
    if _CREATE_NO_WINDOW:
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    try:
        completed = runner(args, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return ""
    out = getattr(completed, "stdout", b"") or b""
    return decode_console(out) if isinstance(out, (bytes, bytearray)) else str(out)


def _pnputil() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    # A 32-bit process would be redirected away from the 64-bit pnputil.
    sysnative = Path(system_root) / "Sysnative" / "pnputil.exe"
    return str(sysnative) if sysnative.is_file() else "pnputil"


def _serial_ch340_ports() -> list[str]:
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return []
    try:
        return [p.device for p in list_ports.comports() if getattr(p, "vid", None) == CH340_VID_INT]
    except Exception:
        return []


@dataclass
class DriverStatus:
    checked: bool
    installed: bool = True
    problem_device: bool = False
    device_present: bool = False
    note: str = ""

    @property
    def needs_install(self) -> bool:
        # Only nag when a WCH (VID 1A86) device is actually plugged in, so
        # FTDI / 16U2 boards are never asked about CH340.
        return self.checked and self.device_present and (self.problem_device or not self.installed)


def check_ch340_driver(
    *,
    platform: str | None = None,
    runner: Callable[..., Any] = subprocess.run,
    serial_ports: Callable[[], list[str]] = _serial_ch340_ports,
) -> DriverStatus:
    """Windows: is a WCH device plugged in, and does it have a working driver?

    Signals (all language-independent strings):
    * a COM port with VID 0x1A86 (pyserial) => device bound to a working
      serial driver (whatever its INF name, e.g. from Windows Update);
    * ``pnputil /enum-devices /connected`` lists the device even unbound;
    * ``pnputil /enum-devices /problem`` lists it with a problem code;
    * ``ch341ser.inf`` in ``/enum-drivers`` = WCH package in the store.
    ``/enum-devices`` needs Windows 10 2004+; on older pnputil only the
    pyserial signal is available (a driverless device is then not detected).
    Non-Windows returns ``checked=False``.
    """
    if not (platform or sys.platform).startswith("win"):
        return DriverStatus(checked=False)
    tool = _pnputil()
    bound_ports = serial_ports()
    connected = _run_quiet([tool, "/enum-devices", "/connected"], runner)
    problems = _run_quiet([tool, "/enum-devices", "/problem"], runner)
    drivers = _run_quiet([tool, "/enum-drivers"], runner)
    vid = CH340_VID.lower()
    enum_supported = vid in connected.lower() or "instance id" in connected.lower() or bool(
        re.search(r"usb\\vid_", connected.lower())
    )
    present = bool(bound_ports) or vid in connected.lower() or vid in problems.lower()
    problem = vid in problems.lower()
    installed = bool(bound_ports) or CH340_INF_NAME in drivers.lower()
    note = "" if enum_supported else "pnputil /enum-devices unavailable (Windows < 10 2004?)"
    return DriverStatus(
        checked=True,
        installed=installed,
        problem_device=problem,
        device_present=present,
        note=note,
    )


# -- elevated installer ----------------------------------------------------------
STILL_ACTIVE = 259
ERROR_CANCELLED = 1223
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102


@dataclass
class ElevateResult:
    status: str  # "ok" | "cancelled" | "failed" | "timeout"
    exit_code: int | None = None
    error: int = 0


def run_elevated_and_wait(
    exe: str | Path,
    params: str = "",
    *,
    hwnd: int | None = None,
    timeout_ms: int = INSTALLER_TIMEOUT_MS,
) -> ElevateResult:
    """ShellExecuteExW 'runas', wait, and report how it ended."""
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
    SEE_MASK_NOASYNC = 0x00000100
    SW_SHOWNORMAL = 1
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.hwnd = wintypes.HWND(hwnd) if hwnd else None
    info.lpVerb = "runas"
    info.lpFile = str(exe)
    info.lpParameters = params or None
    info.lpDirectory = str(Path(exe).parent)
    info.nShow = SW_SHOWNORMAL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        return ElevateResult("cancelled" if error == ERROR_CANCELLED else "failed", error=error)
    if not info.hProcess:
        return ElevateResult("failed", error=0)
    try:
        waited = kernel32.WaitForSingleObject(info.hProcess, int(timeout_ms))
        if waited != WAIT_OBJECT_0:
            return ElevateResult("timeout", error=int(waited))
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            return ElevateResult("failed", error=ctypes.get_last_error())
        if code.value == STILL_ACTIVE:
            return ElevateResult("timeout", exit_code=STILL_ACTIVE)
        return ElevateResult("ok", exit_code=int(code.value))
    finally:
        kernel32.CloseHandle(info.hProcess)


def install_ch340_driver(
    *,
    root: str | Path | None = None,
    hwnd: int | None = None,
    elevate: Callable[..., ElevateResult] = run_elevated_and_wait,
    check: Callable[[], DriverStatus] = check_ch340_driver,
) -> DriverStatus:
    """Run the bundled WCH installer elevated, then re-check.

    Tries the silent switch first; if the driver is still not working (switch
    unsupported or non-zero exit) runs the installer UI once.  Raises
    ``FirmwareFlashError``: ``ch340_install_cancelled`` (UAC declined),
    ``ch340_install_still_running`` (wait timed out - do NOT flash),
    ``ch340_install_failed`` (launch failure / non-zero exit and no driver).
    """
    installer = ch340_installer_path(root)
    if not installer.is_file():
        raise FirmwareFlashError("firmware_error_tool_missing", path=str(installer))

    def run(params: str) -> ElevateResult:
        outcome = elevate(installer, params, hwnd=hwnd)
        if outcome.status == "cancelled":
            raise FirmwareFlashError("ch340_install_cancelled")
        if outcome.status == "timeout":
            raise FirmwareFlashError("ch340_install_still_running")
        if outcome.status != "ok":
            raise FirmwareFlashError("ch340_install_failed", code=outcome.error)
        return outcome

    def working(status: DriverStatus) -> bool:
        return status.installed and not status.problem_device

    first = run(CH340_SILENT_ARGS)
    status = check()
    if working(status):
        return status
    second = run("")
    status = check()
    if working(status):
        return status
    raise FirmwareFlashError("ch340_install_failed", code=second.exit_code if second.exit_code else first.exit_code)
