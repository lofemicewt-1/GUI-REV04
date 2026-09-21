from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TclTkBundle:
    tcl_data: Path
    tk_data: Path
    tcl_dll: Path
    tk_dll: Path
    tkinter_pyd: Path

    def as_datas(self) -> list[tuple[str, str]]:
        return [
            (str(self.tcl_data), "_tcl_data"),
            (str(self.tk_data), "_tk_data"),
        ]

    def as_binaries(self) -> list[tuple[str, str]]:
        return [
            (str(self.tcl_dll), "."),
            (str(self.tk_dll), "."),
            (str(self.tkinter_pyd), "."),
        ]


def _version_key(name: str, prefix: str) -> tuple[int, ...]:
    rest = name[len(prefix) :] if name.startswith(prefix) else name
    numbers: list[int] = []
    current = ""
    for char in rest:
        if char.isdigit():
            current += char
        elif current:
            numbers.append(int(current))
            current = ""
    if current:
        numbers.append(int(current))
    return tuple(numbers) if numbers else (0,)


def _newest_dir(parent: Path, prefix: str) -> Path | None:
    if not parent.is_dir():
        return None
    matches = [path for path in parent.iterdir() if path.is_dir() and path.name.startswith(prefix)]
    if not matches:
        return None
    return max(matches, key=lambda path: _version_key(path.name, prefix))


def _newest_file(parent: Path, prefix: str, suffix: str) -> Path | None:
    if not parent.is_dir():
        return None
    matches = [
        path
        for path in parent.iterdir()
        if path.is_file()
        and path.name.startswith(prefix)
        and path.name.endswith(suffix)
        and "stub" not in path.name.lower()
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: _version_key(path.name, prefix))


def tcl_tk_bundle(base_prefix: str | Path) -> TclTkBundle:
    base = Path(base_prefix)
    tcl_root = base / "tcl"
    dll_root = base / "DLLs"
    tcl_data = _newest_dir(tcl_root, "tcl")
    tk_data = _newest_dir(tcl_root, "tk")
    tcl_dll = _newest_file(dll_root, "tcl", ".dll")
    tk_dll = _newest_file(dll_root, "tk", ".dll")
    tkinter_pyd = dll_root / "_tkinter.pyd"
    bundle = TclTkBundle(
        tcl_data=tcl_data or tcl_root / "tcl8.6",
        tk_data=tk_data or tcl_root / "tk8.6",
        tcl_dll=tcl_dll or dll_root / "tcl86t.dll",
        tk_dll=tk_dll or dll_root / "tk86t.dll",
        tkinter_pyd=tkinter_pyd,
    )
    missing = [
        path
        for path in (
            bundle.tcl_data,
            bundle.tk_data,
            bundle.tcl_dll,
            bundle.tk_dll,
            bundle.tkinter_pyd,
        )
        if not path.exists()
    ]
    if missing:
        raise FileNotFoundError(
            "Tcl/Tk files missing from Python prefix "
            f"{base}: " + ", ".join(str(path) for path in missing)
        )
    return bundle
