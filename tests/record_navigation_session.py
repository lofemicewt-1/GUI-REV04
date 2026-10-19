"""Interactive real-window recording companion; all hardware is fenced.

Run with ffmpeg gdigrab recording the named window, then use actual mouse input.
It records input/presentation gaps without nested Tk pumps or per-frame captures.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import traceback
import tkinter as tk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.validate_responsive_layout import mocked_application


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="baseline")
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--no-animations", action="store_true")
    parser.add_argument("--probe", choices=("normal", "no-cover", "no-sidebar"), default="normal")
    parser.add_argument("--analyze", type=Path)
    parser.add_argument("--warm-budget-ms", type=float, default=400)
    args = parser.parse_args()
    if args.analyze:
        payload = json.loads(args.analyze.read_text(encoding="utf-8"))
        rows, current = [], None
        for item in payload["events"]:
            if item["kind"] == "click":
                current = item
            elif item["kind"] == "cover_removed" and current is not None:
                if item["target"] == current["target"]:
                    rows.append({"page": item["target"], "cold": current["cold"],
                                 "click_to_visible_ms": round((item["t"]-current["t"])*1000, 1)})
                current = None
        slow = [row for row in rows if not row["cold"] and row["click_to_visible_ms"] > args.warm_budget_ms]
        print(json.dumps({"rows": rows, "warm_budget_ms": args.warm_budget_ms, "slow_revisits": slow,
                          "errors": payload["errors"]}, ensure_ascii=False, indent=2))
        return int(bool(slow or payload["errors"] or not rows))
    folder = ROOT / "outputs" / "navigation_recording"
    folder.mkdir(parents=True, exist_ok=True)
    events, gaps, errors, callbacks = [], [], [], {}
    from app import transitions as native
    pending = native._native_paint_pending
    request_paint = native._request_native_paint
    paint_stats = {"checks": 0, "pending": 0, "check_ms": 0, "requests": 0, "request_ms": 0}
    def checked(area):
        begin = time.perf_counter()
        result = pending(area)
        paint_stats["checks"] += 1
        paint_stats["pending"] += int(result)
        paint_stats["check_ms"] += (time.perf_counter()-begin)*1000
        return result
    def requested_paint(area):
        begin = time.perf_counter()
        request_paint(area)
        paint_stats["requests"] += 1
        paint_stats["request_ms"] += (time.perf_counter()-begin)*1000
    call = tk.CallWrapper.__call__
    def measured(wrapper, *args):
        begin = time.perf_counter()
        try:
            return call(wrapper, *args)
        finally:
            callback = wrapper.func
            if getattr(callback, "__qualname__", "") == "Misc.after.<locals>.callit":
                closed = dict(zip(callback.__code__.co_freevars,
                                  (cell.cell_contents for cell in callback.__closure__)))
                callback = closed.get("func", callback)
            name = getattr(callback, "__qualname__", str(callback))
            item = callbacks.setdefault(name, {"calls": 0, "total_ms": 0, "max_ms": 0})
            elapsed = (time.perf_counter()-begin)*1000
            item["calls"] += 1
            item["total_ms"] += elapsed
            item["max_ms"] = max(item["max_ms"], elapsed)
    started = time.perf_counter()
    def event(kind, **data):
        item = {"t": round(time.perf_counter()-started, 4), "kind": kind, **data}
        events.append(item)
        with (folder/f"{args.output}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False)+"\n")
        print(json.dumps(item, ensure_ascii=False), flush=True)
    with patch.object(native, "_native_paint_pending", checked), patch.object(native, "_request_native_paint", requested_paint), patch.object(tk.CallWrapper, "__call__", measured), mocked_application() as app:
        root, nav = app.root, app.navigation
        root.title("Piezo Control GUI - Performance recording")
        root.geometry("1700x980+0+0")
        root.protocol("WM_DELETE_WINDOW", root.quit)
        if args.no_animations:
            app.set_animations(False)
        if args.probe == "no-cover":
            app.transitions.cover = lambda area: False
        if args.probe == "no-sidebar":
            def immediate_modes(target, done=None):
                nav._mode_height = float(target)
                nav.mode_clip.configure(height=max(1, target))
                if done:
                    done()
            nav._animate_modes = immediate_modes
        root.report_callback_exception = lambda k,v,tb: errors.append("".join(traceback.format_exception(k,v,tb)))
        request = nav.request_show
        transition = nav._transition_to
        finish = app.transitions.finish
        animate = app.animator.animate
        def animated(key, duration, step, **kwargs):
            if isinstance(key, tuple) and key[0] == "snapshot":
                event("fade_start", target=app.state.current_page, paint_stats=dict(paint_stats))
            return animate(key, duration, step, **kwargs)
        app.animator.animate = animated
        def requested(name):
            event("click", target=name, cold=not dict.__contains__(nav.pages, name))
            for key in paint_stats:
                paint_stats[key] = 0
            request(name)
        def changed(name):
            event("build_start", target=name)
            transition(name)
            event("build_return", target=name)
        def finished():
            active = app.transitions.active
            finish()
            if active:
                event("cover_removed", target=app.state.current_page, paint_stats=dict(paint_stats))
        nav.request_show, nav._transition_to, app.transitions.finish = requested, changed, finished
        last = time.perf_counter()
        def beat():
            nonlocal last
            now = time.perf_counter()
            gap = now-last
            if gap > .05:
                gaps.append({"t": round(now-started, 4), "ms": round(gap*1000, 2)})
            last = now
            root.after(16, beat)
        root.after(16, beat)
        root.after(args.duration*1000, root.quit)
        event("ready", animations=app.animator.enabled, hardware_io="fenced")
        root.mainloop()
    (folder/f"{args.output}.json").write_text(json.dumps({"events": events, "gaps_over_50ms": gaps,
        "errors": errors, "callbacks": sorted(callbacks.items(), key=lambda pair: pair[1]["total_ms"], reverse=True)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
