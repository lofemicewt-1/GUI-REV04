"""Real Tk colors, flash lifecycle, and scroll reachability; all transport forbidden."""
from pathlib import Path
import json
import sys
import time
from unittest.mock import patch, PropertyMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import chip_status_cache as cache
from tests.test_awp1221_pressure_status import snapshot
from tests.validate_responsive_layout import mocked_application, _flush, _capture_window_bmp


def main():
    out = ROOT / "outputs/button_mode_20261019/revision5_pressure"
    out.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    with mocked_application() as app:
        root = app.root
        root.report_callback_exception = lambda *args: errors.append(str(args))
        app.navigation.show("awp1221")
        host = app.navigation.pages["awp1221"]
        host.show_mode("button_mode")
        page = host.mode_pages["button_mode"]
        label = page._pressure_status_label
        identity = cache.make_identity(0, 0x50, chip_type="1221")
        state = cache.SnapshotState([identity], session_id=7)
        sequence = 0
        for theme, language, size in (("light", "zh", "1700x980"),
                                       ("dark", "zh", "1280x720"),
                                       ("light", "en", "640x480")):
            app.state.theme, app.state.language = theme, language
            app._apply_visual_settings()
            host.refresh_language()
            root.geometry(size)
            _flush(root)
            area = host.mode_scroll
            offset = label.master.winfo_rooty() - area.content.winfo_rooty()
            area.canvas.yview_moveto(max(0, offset - 15) / area.content.winfo_height())
            _flush(root)
            assert label.winfo_ismapped()
            assert label.winfo_rooty() >= area.canvas.winfo_rooty()
            assert label.winfo_rooty() + label.winfo_height() <= area.canvas.winfo_rooty() + area.canvas.winfo_height()
            for raw, status, color in ((0, "idle", "#4B5563" if theme == "dark" else "#E5E7EB"),
                                       (0x10, "sense", "#FACC15"),
                                       (0x11, "press", "#2563EB"),
                                       (0x12, "release", "#9333EA"),
                                       (0x400, "error", "#DC2626")):
                sequence += 1
                state.apply_snapshot(snapshot(identity, raw, sequence))
                with patch.object(page, "_shared_port_identity", return_value="OFFLINE"), \
                     patch.object(page, "device_addr", return_value=0x50), \
                     patch.object(type(page.ctx.serial), "is_connected", new_callable=PropertyMock, return_value=True):
                    page._apply_pressure_status_cache("OFFLINE", state)
                assert label.cget("background") == color
                assert label.cget("text") == page.tr(f"awp1221_pressure_{status}")
                if status in {"press", "release"}:
                    # Freeze only the screenshot's blink; verify the real timer separately below.
                    page._cancel_idle_job("_pressure_flash_job")
                root.update_idletasks()
                if status in {"press", "release", "error"}:
                    assert _capture_window_bmp(root, out / f"{theme}-{language}-{size}-{status}.bmp") is None
                checks.append(dict(theme=theme, language=language, size=size, state=status, color=color))
            page._pressure_sensing = True
            page._set_pressure_status("press")
            deadline = time.monotonic() + .25
            while time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            assert page._pressure_status == "sense"
            page._set_pressure_status("release")
            host.show_mode("full_mode")
            _flush(root)
            assert page._pressure_status == "idle" and page._pressure_flash_job is None
            host.show_mode("button_mode")
            page._communication_log_toggle.invoke()
            _flush(root)
            area.canvas.yview_moveto(1)
            _flush(root)
            log = page._serial_log_text.master
            assert log.winfo_rooty() + log.winfo_height() <= area.canvas.winfo_rooty() + area.canvas.winfo_height()
            page._communication_log_toggle.invoke()
        assert not errors, errors
    (out / "ui_validation.json").write_text(json.dumps(dict(status="PASS", checks=checks,
        callback_errors=errors, hardware="Offline synthetic status; transport forbidden"), indent=2), encoding="utf-8")
    print(f"PASS: {len(checks)} color/theme/language/size cases; flash, hide, log scrolling checked")


if __name__ == "__main__":
    main()
