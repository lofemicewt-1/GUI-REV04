"""Capture the real offline chip pages; every transport boundary is mocked.

Run with DISPLAY=:4 python3 -m tests.capture_stepbar_c.
Override filenames with --prefix or STEPBAR_CAPTURE_PREFIX.
Artifacts stay inside this worktree by default.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from PIL import ImageGrab

from tests.validate_responsive_layout import _flush, mocked_application


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=os.environ.get("STEPBAR_CAPTURE_PREFIX", "after_stepbar_c150"),
                        help="filename prefix (default: after_stepbar_c150)")
    args = parser.parse_args()
    root_path = Path(__file__).resolve().parents[1]
    output = root_path / "review"
    output.mkdir(exist_ok=True)
    os.environ["APPDATA"] = str(root_path / ".tmp" / "capture-appdata")
    with mocked_application() as app:
        app.set_animations(False)
        app.set_language("zh")
        app.set_theme("light")
        root = app.root
        root.overrideredirect(True)
        for chip, mode in (("awp1221", "button_mode"), ("awp1018", "awp1018_normal_mode")):
            app.navigation.show(chip)
            host = app.navigation.pages[chip]
            host.show_mode(mode)
            for width, height in ((1024, 608), (1366, 768), (1920, 1080)):
                root.geometry(f"{width}x{height}+0+0")
                _flush(root, 5)
                app._apply_visual_settings()
                host.trapezoid.set_expanded(False, persist=False)
                bar = host.mode_pages[mode].step_progress
                # Presentation demo only: no receipts, commands or hardware I/O.
                for step, state in zip(bar.steps, ("done", "running", "idle", "error")):
                    bar._data[step.key].state = state
                bar.active_step_key = bar.steps[1].key
                bar._render()
                _flush(root, 6)
                assert (root.winfo_width(), root.winfo_height()) == (width, height)
                area = host.trapezoid._owning_area
                assert area.canvas.winfo_rooty() + area.canvas.winfo_height() <= bar.winfo_rooty()
                filename = output / f"{args.prefix}_{chip}_{width}x{height}.png"
                x, y = root.winfo_rootx(), root.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x+width, y+height)).save(filename)
                print(json.dumps({"path": str(filename), "scale": app.state.ui_scale,
                                  "strip_height_with_shadow": bar.canvas.winfo_height(),
                                  "advanced_height": host.trapezoid.button.winfo_height(),
                                  "bottom_band": area.bottom_inset}))


if __name__ == "__main__":
    main()
