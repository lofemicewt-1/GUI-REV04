"""Exercise real Tk page/size transitions offline; fail on an event-loop stall.

Run: py tests/validate_ui_responsiveness.py --output outputs/ui_responsiveness/run.json
"""
from __future__ import annotations

import argparse
import faulthandler
import json
from pathlib import Path
import sys
import time
import tkinter as tk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.validate_responsive_layout import mocked_application


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-gap-ms", type=float, default=500)
    parser.add_argument("--page")
    args = parser.parse_args()
    callbacks: dict[str, dict[str, float]] = {}
    gaps: list[dict[str, object]] = []
    actions: list[dict[str, object]] = []
    errors: list[str] = []
    stage = ["warmup"]
    original = tk.CallWrapper.__call__

    def measured(wrapper, *values):
        start = time.perf_counter()
        try:
            return original(wrapper, *values)
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            if stage[0] != "warmup":
                callback = wrapper.func
                if getattr(callback, "__qualname__", "") == "Misc.after.<locals>.callit":
                    closed = dict(zip(callback.__code__.co_freevars,
                                      (cell.cell_contents for cell in callback.__closure__)))
                    callback = closed.get("func", callback)
                name = getattr(callback, "__qualname__", str(callback))
                item = callbacks.setdefault(name, {"calls": 0, "total_ms": 0, "max_ms": 0})
                item["calls"] += 1
                item["total_ms"] += elapsed
                item["max_ms"] = max(item["max_ms"], elapsed)

    faulthandler.dump_traceback_later(40, exit=True)
    with patch.object(tk.CallWrapper, "__call__", measured), mocked_application() as app:
        root = app.root
        root.report_callback_exception = lambda kind, value, tb: errors.append(f"{kind.__name__}: {value}")
        steps = []
        for name, page in app.navigation.pages.items():
            if args.page and name != args.page:
                continue
            steps.append((name, None, "1000x700"))
            for mode in getattr(page, "mode_pages", {}):
                for size in ("1200x800", "zoomed", "700x500"):
                    steps.append((name, mode, size))
        index = [0]
        last = [0.0]

        def beat():
            now = time.perf_counter()
            if last[0]:
                gaps.append({"stage": stage[0], "ms": (now - last[0]) * 1000})
            last[0] = now
            root.after(20, beat)

        def step():
            if index[0] == len(steps):
                root.quit()
                return
            name, mode, size = steps[index[0]]
            index[0] += 1
            stage[0] = f"{name}/{mode or '-'}/{size}"
            start = time.perf_counter()
            if app.state.current_page != name:
                app.navigation.show(name)
            if mode is not None and app.navigation.pages[name].current_mode != mode:
                app.navigation.pages[name].show_mode(mode)
            if size == "zoomed":
                root.state("zoomed")
            else:
                root.state("normal")
                root.geometry(size)
            actions.append({"stage": stage[0], "ms": (time.perf_counter() - start) * 1000})
            root.after(300, step)

        root.after(500, beat)
        root.after(600, step)
        root.mainloop()
    faulthandler.cancel_dump_traceback_later()
    worst = sorted(gaps, key=lambda item: item["ms"], reverse=True)[:10]
    result = {"actions": actions, "worst_event_gaps": worst, "errors": errors,
              "callbacks": sorted(callbacks.items(), key=lambda item: item[1]["total_ms"], reverse=True)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"steps": len(actions), "worst_event_gaps": worst[:5], "errors": errors,
                      "callbacks": result["callbacks"][:12]}, ensure_ascii=False, indent=2))
    passed = not errors and len(actions) == len(steps) and worst[0]["ms"] <= args.max_gap_ms
    print("UI_RESPONSIVE" if passed else "FAIL: UI event loop stalled")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
