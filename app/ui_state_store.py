"""Small, best-effort persistence for presentation-only UI state."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


class UiStateStore:
    """Persist expanded/collapsed preferences without making startup fragile.

    The file is intentionally separate from device settings.  Reads and writes
    tolerate malformed or unavailable files so presentation preferences can
    never prevent the application from opening.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else self._default_path()

    @staticmethod
    def _default_path() -> Path:
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "AnalogwinPiezoGUI" / "ui_state.json"
        if os.name == "nt":
            return Path.home() / "AppData" / "Roaming" / "AnalogwinPiezoGUI" / "ui_state.json"
        return Path.home() / ".config" / "AnalogwinPiezoGUI" / "ui_state.json"

    def _read(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as source:
                value = json.load(source)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def get_trapezoid_expanded(self, key: str, default: bool = False) -> bool:
        data = self._read()
        trapezoids = data.get("trapezoids")
        if not isinstance(trapezoids, dict) or key not in trapezoids:
            return bool(default)
        value = trapezoids[key]
        return value if isinstance(value, bool) else bool(default)

    def set_trapezoid_expanded(self, key: str, value: bool) -> None:
        data = self._read()
        data["version"] = 1
        trapezoids = data.get("trapezoids")
        if not isinstance(trapezoids, dict):
            trapezoids = {}
            data["trapezoids"] = trapezoids
        trapezoids[str(key)] = bool(value)
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as target:
                temporary = target.name
                json.dump(data, target, ensure_ascii=False, indent=2)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        except (OSError, TypeError, ValueError):
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def get_always_show_chip_pages(self, default: bool = False) -> bool:
        value = self._read().get("always_show_chip_pages", default)
        return value if isinstance(value, bool) else bool(default)

    def set_always_show_chip_pages(self, value: bool) -> None:
        data = self._read()
        data["version"] = 1
        data["always_show_chip_pages"] = bool(value)
        self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as target:
                temporary = target.name
                json.dump(data, target, ensure_ascii=False, indent=2)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        except (OSError, TypeError, ValueError):
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

