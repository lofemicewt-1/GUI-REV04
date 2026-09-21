"""Capture bounded scale-2 views for responsive-layout evidence."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.validate_responsive_layout import (
    ARTIFACT_DIR,
    _capture_window_bmp,
    _flush,
    mocked_application,
)


with mocked_application() as app:
    root = app.root
    root.tk.call("tk", "scaling", 2.0)
    app._apply_visual_settings(scale=2.0)
    root.geometry("640x480")
    root.deiconify()
    _flush(root, 12)
    for name, page_name, mode in (
        ("scale2-awp1018-diff", "awp1018", "awp1018_diff_mode"),
        ("scale2-awp1221-full", "awp1221", "full_mode"),
    ):
        app.navigation.show(page_name)
        page = app.navigation.pages[page_name]
        page.show_mode(mode)
        _flush(root, 12)
        path = ARTIFACT_DIR / f"{name}.bmp"
        error = _capture_window_bmp(root, path)
        print(f"{name}={path} error={error or 'none'}", flush=True)
