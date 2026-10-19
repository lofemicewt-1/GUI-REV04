"""Bounded real-Tk animation/lazy-load validation with all transports fenced."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_motion import pump
from tests.validate_responsive_layout import mocked_application, _capture_window_bmp


def settle(app, timeout: float = 12) -> None:
    """Wait for actual completion, including first-use construction/painting."""
    deadline = time.perf_counter()+timeout
    while time.perf_counter() < deadline:
        pump(app.root, .02)
        if (not app.transitions.active and not app.animator._tweens
                and app.navigation._pending_page is None
                and app.navigation._page_layout_ready()):
            return
    nav = app.navigation
    page = dict.get(nav.pages, app.state.current_page)
    detail = {"page": app.state.current_page, "geometry": app.root.geometry(),
              "transition": app.transitions.active, "tweens": list(app.animator._tweens),
              "content": (nav.content.winfo_width(), nav.content.winfo_height()),
              "canvas": (nav.content_canvas.winfo_width(), nav.content_canvas.winfo_height()),
              "rows": [(str(child), child.winfo_ismapped(), child.winfo_height())
                       for row in (0, 1, 2) for child in page.grid_slaves(row=row)]}
    raise AssertionError(f"navigation/layout did not settle: {detail}")


def main() -> int:
    output = ROOT / "outputs" / "animation_migration"
    output.mkdir(parents=True, exist_ok=True)
    errors, records, captures = [], [], []
    started = time.perf_counter()
    with mocked_application() as app:
        root, nav = app.root, app.navigation
        startup_ms = (time.perf_counter()-started)*1000
        root.report_callback_exception = lambda k, v, tb: errors.append("".join(traceback.format_exception(k, v, tb)))
        root.geometry("1200x800+40+40")
        pump(root, 0.35)
        assert [p.page_name for p in nav.pages.built_values()] == ["home"]
        app.apply_settings("zh", "dark", 11)
        settle(app)
        assert len(nav.pages.built_values()) == 1, "theme broadcast eagerly built hidden pages"
        for name in nav.pages:
            begin = time.perf_counter()
            nav.show(name)
            call_ms = (time.perf_counter()-begin)*1000
            settle(app)
            assert app.state.current_page == name
            assert nav.pages[name].winfo_ismapped()
            assert not app.transitions.active
            page = nav.pages[name]
            if nav.mode_buttons:
                assert nav.mode_clip.winfo_height() >= nav.mode_frame.winfo_reqheight()
                for mode in getattr(page, "mode_pages", {}):
                    nav._on_mode_clicked(mode)
                    settle(app)
                    assert page.current_mode == mode
                    assert page.mode_pages[mode].winfo_ismapped()
            records.append({"phase": "first_visit", "page": name, "call_ms": round(call_ms, 2)})
        # Rapid input takes over the cover, without a nested event pump.
        for name in ("awp1018", "home", "awp1221", "awp1922", "awp1221"):
            nav.show(name)
        settle(app)
        assert app.state.current_page == "awp1221"
        assert nav.mode_host is nav.pages["awp1221"]
        selected = nav.mode_buttons[nav.mode_host.current_mode]
        center = selected.winfo_rooty()-nav.sidebar.winfo_rooty()+selected.winfo_height()/2
        indicator_center = nav.motion.indicator.winfo_y()+nav.motion.indicator.winfo_height()/2
        assert abs(center-indicator_center) <= 1, (center, indicator_center)
        assert not app.animator._tweens
        # Worker serial logs are coalesced, ordered, and cleared by generation.
        page = nav.pages["awp1922"]
        page.clear_serial_log_view()
        def log_worker():
            for i in range(500):
                page.append_serial_log(f"animation-log-{i}")
            page.clear_serial_log_view()
            page.append_serial_log("after-clear")
        thread = threading.Thread(target=log_worker)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()
        pump(root, 0.1)
        assert page._serial_log_text.get("1.0", "end").strip() == "after-clear"
        for enabled in (True, False):
            app.set_animations(enabled)
            for theme in ("light", "dark", "light"):
                app.apply_settings("zh", theme, 11)
                settle(app)
                for size in ("1200x800", "640x480", "480x320"):
                    root.geometry(size)
                    nav.show("awp1018")
                    settle(app)
                    assert nav.pages["awp1018"].winfo_ismapped()
                    if nav.mode_buttons:
                        assert nav.mode_clip.winfo_height() >= nav.mode_frame.winfo_reqheight()
                    assert nav.sidebar_canvas.winfo_ismapped()
                    assert nav._settings_button.winfo_ismapped()
                    records.append({"phase": "layout", "enabled": enabled, "theme": theme, "size": size})
            root.geometry("1200x800")
            nav.show("settings")
            settle(app)
            settings = nav.pages["settings"]
            assert settings.animations_check.winfo_ismapped()
            if not enabled:
                assert not app.animator._tweens
        app.set_animations(True)
        root.geometry("1200x800")
        nav.show("awp1221")
        settle(app)
        for theme in ("light", "dark"):
            app.set_theme(theme)
            settle(app)
            target = output / f"awp1221_{theme}.bmp"
            error = _capture_window_bmp(root, target)
            captures.append({"path": str(target), "error": error})
        # Close in the middle of an active transition: owner, bitmap and timers release.
        app.set_theme("light")
        assert not errors, errors
    payload = {"startup_ms": round(startup_ms, 2), "records": records, "errors": errors,
               "captures": captures, "hardware_io": "mocked"}
    (output / "validation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"startup_ms": payload["startup_ms"], "records": len(records), "errors": errors,
                      "captures": captures}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
