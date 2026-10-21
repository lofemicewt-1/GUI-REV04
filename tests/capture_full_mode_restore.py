"""Capture a read-only source checkout on private Xvfb :79 at 1366x768.

Run as a script so --source controls all application imports, for example:
python3 tests/capture_full_mode_restore.py --source /workspace/uiopt-base --label baseline
python3 tests/capture_full_mode_restore.py --source /workspace/uiopt-fmr --label after
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--label", required=True, choices=("baseline", "after"))
    parser.add_argument("--output", type=Path, default=Path("/workspace/review"))
    args = parser.parse_args()
    os.environ["DISPLAY"] = ":79"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(args.source.resolve()))

    from PIL import ImageGrab
    from tests.validate_responsive_layout import _flush, mocked_application

    # Keep UI preferences out of both source checkouts.
    with tempfile.TemporaryDirectory(prefix="fullmode-capture-") as preferences:
        os.environ["APPDATA"] = preferences
        with mocked_application() as app:
            app.set_animations(False)
            app.set_language("zh")
            app.set_theme("light")
            root = app.root
            root.overrideredirect(True)
            root.geometry("1366x768+0+0")
            _flush(root, 12)
            assert (root.winfo_screenwidth(), root.winfo_screenheight()) == (1366, 768)
            args.output.mkdir(parents=True, exist_ok=True)
            for chip, suffix in (("awp1221", "1221"), ("awp1018", "1018"),
                                 ("awp1921_1923", "1921")):
                app.navigation.show(chip)
                host = app.navigation.pages[chip]
                if chip == "awp1018":
                    host.show_mode("full_mode", update_chip=False)
                else:
                    host.show_mode("full_mode")
                _flush(root, 12)
                host.mode_scroll.canvas.xview_moveto(0)
                host.mode_scroll.canvas.yview_moveto(0)
                _flush(root, 5)
                path = args.output / f"fullmode_restore_{args.label}_{suffix}.png"
                ImageGrab.grab(bbox=(0, 0, 1366, 768), xdisplay=":79").save(path)
                print(path, flush=True)


if __name__ == "__main__":
    main()
