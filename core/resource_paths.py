"""Runtime resource location (source tree or PyInstaller ``sys._MEIPASS``)."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """Folder holding ``assets``: PyInstaller's ``sys._MEIPASS`` or the repo root."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parent.parent
