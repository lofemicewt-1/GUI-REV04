"""Real main-loop measurements of cross-chip switching and footer paint."""
from __future__ import annotations

import json
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.transitions import _native_paint_pending
from tests.validate_responsive_layout import mocked_application, _capture_window_bmp


def _selection_pixel_ready(button, expected: str) -> bool:
    if sys.platform != "win32":
        return not _native_paint_pending(button)
    from ctypes import wintypes
    from app.transitions import _user32, _gdi32
    _user32.GetDC.restype = wintypes.HDC
    _gdi32.GetPixel.restype = wintypes.DWORD
    hwnd = wintypes.HWND(int(button.winfo_id()))
    dc = _user32.GetDC(hwnd)
    if not dc:
        return False
    try:
        pixel = _gdi32.GetPixel(wintypes.HDC(dc), 14, 14)
        rgb = (pixel & 255, (pixel >> 8) & 255, (pixel >> 16) & 255)
        target = tuple(int(expected[i:i+2], 16) for i in (1, 3, 5))
        return rgb == target
    finally:
        _user32.ReleaseDC(hwnd, wintypes.HDC(dc))


def main(*, clicks: bool = False) -> int:
    output = ROOT / "outputs" / ("navigation_click_latency" if clicks else "animation_paint_fix")
    output.mkdir(parents=True, exist_ok=True)
    records, errors = [], []
    phases = [(name, "1700x980") for name in
              ("awp1221", "awp1018", "awp1221", "awp1018", "awp1922",
               "awp1221", "home", "awp1921_1923", "awp1221")]
    phases += [("awp1221", size) for size in ("zoomed", "1200x800", "1700x980")]
    with mocked_application() as app:
        root, nav = app.root, app.navigation
        root.geometry("1700x980+0+0")
        root.report_callback_exception = lambda k, v, tb: errors.append(f"{k.__name__}: {v}")
        current = [None]
        startup_at = time.perf_counter()
        last_paint = [time.perf_counter()]
        def activity(event):
            if str(event.widget).startswith(str(nav.content_canvas)):
                last_paint[0] = time.perf_counter()
        root.bind("<Configure>", activity, add="+")
        root.bind("<Expose>", activity, add="+")
        original = app.animator.animate

        def animate(key, ms, step, **kwargs):
            if isinstance(key, tuple) and key[0] == "snapshot" and current[0] is not None:
                record = current[0]
                paint = step
                def step(p):
                    if p == 0:
                        record["reveal_ms"] = round((time.perf_counter()-record["start"])*1000, 2)
                        record["layout_ready_at_reveal"] = nav._page_layout_ready()
                        record["pending_at_reveal"] = _native_paint_pending(nav.content_canvas)
                        if clicks and len(records) in (1, 3):
                            path = output/f"first-frame-{len(records)}.bmp"
                            record["first_frame_error"] = _capture_window_bmp(nav.content_canvas, path, include_layers=False)
                            record["first_frame"] = path.name
                    paint(p)
            return original(key, ms, step, **kwargs)

        app.animator.animate = animate
        last_beat = [time.perf_counter()]
        def beat():
            now = time.perf_counter()
            if current[0] is not None:
                current[0]["max_event_gap_ms"] = max(current[0]["max_event_gap_ms"],
                                                     (now-last_beat[0])*1000)
            last_beat[0] = now
            root.after(16, beat)

        def next_phase():
            if len(records) == len(phases):
                root.quit()
                return
            name, size = phases[len(records)]
            record = {"page": name, "size": size, "cold": not dict.__contains__(nav.pages, name),
                      "max_event_gap_ms": 0.0, "start": time.perf_counter()}
            current[0] = record
            records.append(record)
            if size == "zoomed":
                root.state("zoomed")
            else:
                root.state("normal")
                root.geometry(size)
            t0 = time.perf_counter()
            if clicks:
                nav.buttons[name].invoke()
                record["selected_on_return"] = nav.buttons[name].cget("style") == "SidebarSelected.TButton"
            else:
                nav.show(name)
            record["call_ms"] = round((time.perf_counter()-t0)*1000, 2)
            if clicks:
                def first_turn():
                    record["first_callback_ms"] = round((time.perf_counter()-t0)*1000, 2)
                root.after(0, first_turn)
                def selection_painted():
                    if current[0] is not record:
                        return
                    if _selection_pixel_ready(nav.buttons[name], app.colors["primary_hover"]):
                        record["selection_painted_ms"] = round((time.perf_counter()-t0)*1000, 2)
                    else:
                        root.after(16, selection_painted)
                root.after(0, selection_painted)
            root.after(20, settled)

        def settled():
            record = current[0]
            elapsed = time.perf_counter()-record["start"]
            size = record["size"]
            geometry_ready = (root.state() == "zoomed") if size == "zoomed" else (
                root.state() == "normal" and
                (root.winfo_width(), root.winfo_height()) == tuple(map(int, size.split("x"))))
            ready = (geometry_ready and app.state.current_page == record["page"]
                     and nav._pending_page is None
                     and not app.transitions.active and nav._page_layout_ready()
                     and time.perf_counter()-last_paint[0] > .05
                     and not _native_paint_pending(nav.content_canvas))
            if not ready and elapsed < 15:
                root.after(20, settled)
                return
            if not ready:
                errors.append(f"{record['page']} {size}: paint/geometry timeout")
            if clicks and (not record.get("selected_on_return") or "selection_painted_ms" not in record):
                errors.append(f"{record['page']} {size}: selection feedback missing")
            record["actual_size"] = [root.winfo_width(), root.winfo_height()]
            record["settled_ms"] = round(elapsed*1000, 2)
            record["max_event_gap_ms"] = round(record["max_event_gap_ms"], 2)
            page = dict.get(nav.pages, record["page"])
            footer = getattr(page, "_footer_panel", None)
            if footer is not None:
                record["footer"] = {"mapped": bool(footer.winfo_ismapped()),
                                    "y": footer.winfo_rooty()-root.winfo_rooty(),
                                    "height": footer.winfo_height(), "root_height": root.winfo_height()}
                if not record["footer"]["mapped"]:
                    errors.append(f"{record['page']}: missing footer")
                viewport = page.footer_scroll
                bottom = viewport.winfo_rooty()-root.winfo_rooty()+viewport.winfo_height()
                record["footer"]["viewport_bottom"] = bottom
                if bottom > root.winfo_height():
                    errors.append(f"{record['page']} {size}: footer viewport outside window")
                if abs(bottom-(root.winfo_height()-nav._scaled(8))) > 2:
                    errors.append(f"{record['page']} {size}: footer retained old viewport height")
            if record["page"] != "awp1221" or record["size"] == "1700x980":
                if record.get("pending_at_reveal") or record.get("layout_ready_at_reveal") is False:
                    errors.append(f"{record['page']}: reveal before paint")
            if record["page"] == "awp1221" and (record["cold"] or record["size"] == "zoomed"):
                target = output / f"awp1221-{'cold' if record['cold'] else record['size']}.bmp"
                record["capture_error"] = _capture_window_bmp(root, target)
            if record.get("first_frame"):
                path = output/f"final-frame-{len(records)}.bmp"
                capture_error = _capture_window_bmp(nav.content_canvas, path, include_layers=False)
                record["first_frame_matches_final"] = (not capture_error and not record["first_frame_error"]
                    and path.read_bytes() == (output/record["first_frame"]).read_bytes())
                if not record["first_frame_matches_final"]:
                    errors.append(f"{record['page']}: first fade frame differs from settled page")
            record.pop("start")
            print(json.dumps(record, ensure_ascii=False), flush=True)
            current[0] = None
            root.after(50, next_phase)

        def startup_ready():
            # A human chip click follows the startup sidebar paint. Starting
            # at a fixed 200 ms mixes the initial application paint into the
            # cold-chip measurement and can capture a still blank sidebar.
            if (root.winfo_width(), root.winfo_height()) == (1700, 980) and not _native_paint_pending(root):
                next_phase()
            elif time.perf_counter()-startup_at > 15:
                errors.append("startup paint timeout")
                root.quit()
            else:
                root.after(20, startup_ready)
        root.after(100, beat)
        root.after(200, startup_ready)
        root.mainloop()
    (output / "after.json").write_text(json.dumps({"records": records, "errors": errors},
                                                  ensure_ascii=False, indent=2), encoding="utf-8")
    print("errors", errors)
    return 1 if errors else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clicks", action="store_true", help="Measure actual sidebar button commands")
    raise SystemExit(main(clicks=parser.parse_args().clicks))
