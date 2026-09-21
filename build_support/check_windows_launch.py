"""Manual smoke check: launch a command, verify its GUI, and close only that GUI.

Usage: py -3 build_support/check_windows_launch.py path/to/program.exe
       py -3 build_support/check_windows_launch.py cmd /d /c path/to/run_gui.bat
Uses Win32 process ancestry so an already-running user GUI is never closed.
"""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


class ProcessEntry(c.Structure):
    _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD),
                ("heap", c.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
                ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
                ("exe", w.WCHAR * 260)]


def descendants(pid: int) -> set[int]:
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    entry = ProcessEntry()
    entry.size = c.sizeof(entry)
    pairs = []
    try:
        ok = kernel.Process32FirstW(handle, c.byref(entry))
        while ok:
            pairs.append((entry.pid, entry.parent))
            ok = kernel.Process32NextW(handle, c.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    owned = {pid}
    while True:
        children = {child for child, parent in pairs if parent in owned}
        if children <= owned:
            return owned
        owned.update(children)


def main() -> None:
    user = c.WinDLL("user32", use_last_error=True)
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes = [callback_type, w.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    user.IsWindowVisible.argtypes = [w.HWND]
    user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
    user.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM,
                                       w.UINT, w.UINT, c.POINTER(c.c_size_t)]
    user.SendMessageTimeoutW.restype = c.c_ssize_t
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    env = os.environ.copy()
    # EXE must not depend on a Python installation or inherited Tcl paths.
    if Path(sys.argv[1]).suffix.lower() == ".exe":
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        for key in ("PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY"):
            env[key] = str(Path(tempfile.gettempdir()) / "nonexistent-python-runtime")
    with tempfile.TemporaryDirectory(prefix="gui-cwd-", ignore_cleanup_errors=True) as cwd, tempfile.TemporaryFile() as log:
        process = subprocess.Popen(sys.argv[1:], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 30
            matches = []
            while time.monotonic() < deadline:
                owned = descendants(process.pid)

                @callback_type
                def visit(hwnd, _):
                    pid = w.DWORD()
                    user.GetWindowThreadProcessId(hwnd, c.byref(pid))
                    if pid.value in owned and user.IsWindowVisible(hwnd):
                        title = c.create_unicode_buffer(256)
                        user.GetWindowTextW(hwnd, title, 256)
                        if title.value == "Piezo Control GUI":
                            matches.append(hwnd)
                    return True

                user.EnumWindows(visit, 0)
                if matches or process.poll() is not None:
                    break
                time.sleep(0.2)
            if not matches:
                log.seek(0)
                raise RuntimeError(f"No visible main window; exit={process.poll()}: {log.read(3000)!r}")
            result = c.c_size_t()
            if not user.SendMessageTimeoutW(matches[0], 0, 0, 0, 2, 3000, c.byref(result)):
                raise RuntimeError("Main window does not respond")
            user.PostMessageW(matches[0], 0x10, 0, 0)
            code = process.wait(timeout=15)
            if code:
                raise RuntimeError(f"GUI exited with code {code}")
            print("PASS: visible main window, responsive, clean close; unrelated cwd")
        finally:
            if process.poll() is None:
                cleanup = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                         capture_output=True, timeout=10)
                if cleanup.returncode:
                    print(f"Could not stop test process {process.pid}: {cleanup.stderr!r}", file=sys.stderr)
                else:
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
