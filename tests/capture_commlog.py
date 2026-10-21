"""Capture offline communication logs: DISPLAY=:4 python3 -m tests.capture_commlog."""
from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import ImageGrab

from tests.validate_responsive_layout import _flush, mocked_application


def main() -> None:
    root_path = Path(__file__).resolve().parents[1]
    output = Path("/workspace/review")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(root_path / ".tmp" / "capture-commlog-appdata")
    with mocked_application() as app:
        app.set_animations(False)
        app.set_language("zh")
        app.set_theme("light")
        root = app.root
        root.overrideredirect(True)
        for chip, mode in (("awp1221", "button_mode"), ("awp1018", "awp1018_normal_mode"),
                           ("awp1921_1923", "button_mode")):
            app.navigation.show(chip)
            host = app.navigation.pages[chip]
            host.show_mode(mode)
            _flush(root, 5)
            page = host.mode_pages[mode]
            if not page._serial_log_body.winfo_manager():
                page._serial_log_toggle.invoke()
            # Presentation data goes directly to the Text view, never transport/history.
            page.clear_serial_log_view()
            for index in range(4):
                page.append_serial_log(
                    f"[12:34:{index:02d}.123] LOCAL DEMO {chip.upper()} | "
                    + " ".join(f"{value:02X}" for value in range(64))
                    + " | long log line: horizontal scrolling preserves the complete record"
                )
            for width, height in ((1024, 608), (1366, 768), (1920, 1080)):
                root.geometry(f"{width}x{height}+0+0")
                _flush(root, 5)
                app._apply_visual_settings()
                host.trapezoid.set_expanded(False, persist=False)
                _flush(root, 5)
                area = host.mode_scroll
                area._sync_scroll_region()
                area.canvas.yview_moveto(1)
                page._serial_log_text.xview_moveto(0)
                _flush(root, 6)
                assert (root.winfo_width(), root.winfo_height()) == (width, height)
                frame = page._serial_log_frame
                assert frame.winfo_rooty() >= area.canvas.winfo_rooty()
                assert frame.winfo_rooty() + frame.winfo_height() <= area.canvas.winfo_rooty() + area.canvas.winfo_height()
                assert int(page._serial_log_title.cget("wraplength")) == 0
                filename = output / f"after_commlog_{chip}_{width}x{height}.png"
                x, y = root.winfo_rootx(), root.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + width, y + height)).save(filename)
                print(json.dumps({"path": str(filename), "title": page._serial_log_title.cget("text"),
                                  "wrap": page._serial_log_text.cget("wrap")}))


if __name__ == "__main__":
    main()
