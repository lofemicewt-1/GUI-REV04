from __future__ import annotations

import subprocess
import sys


MIN_VERSION = (3, 10)


def _fail(message: str) -> int:
    print(message)
    return 1


def ensure_python_version() -> int:
    if sys.version_info < MIN_VERSION:
        return _fail(
            f"Python {MIN_VERSION[0]}.{MIN_VERSION[1]}+ is required. "
            f"This interpreter is {sys.version.split()[0]}:\n{sys.executable}"
        )
    return 0


def ensure_tkinter() -> int:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.destroy()
    except Exception as exc:
        return _fail(
            "tkinter/tcl/tk is not usable with this Python:\n"
            f"{sys.executable}\n{exc}\n"
            "Install Python from python.org (not Microsoft Store) and enable tcl/tk."
        )
    return 0


def ensure_pyserial() -> int:
    try:
        from serial import Serial  # noqa: F401
        from serial.tools import list_ports  # noqa: F401
        return 0
    except ImportError:
        pass
    print("pyserial is missing; installing...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "--disable-pip-version-check",
             "install", "--timeout", "15", "--retries", "1", "pyserial>=3.5"],
            check=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return _fail(
            "Failed to install pyserial. Run:\n"
            f'"{sys.executable}" -m pip install -r requirements.txt'
        )
    try:
        from serial import Serial  # noqa: F401
        from serial.tools import list_ports  # noqa: F401
    except ImportError:
        return _fail("pyserial installed but still cannot be imported.")
    return 0


def main() -> int:
    for step in (ensure_python_version, ensure_tkinter, ensure_pyserial):
        print(f"Checking {step.__name__.removeprefix('ensure_')}...", flush=True)
        code = step()
        if code:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
