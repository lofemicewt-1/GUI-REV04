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

MODE_KEYS = {
    "awp1221": ("button_mode", "full_mode"),
    "awp1018": ("awp1018_normal_mode", "awp1018_diff_mode", "full_mode"),
    "awp1921_1923": ("button_mode", "pump_mode", "full_mode"),
}


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
        nav = app.navigation
        # LazyPages.items() builds every registered page. Use registry keys and
        # the declared mode names so this benchmark measures the cold path.
        for name in nav.pages.keys():
            if args.page and name != args.page:
                continue
            steps.append((name, None, "1000x700"))
            for mode in MODE_KEYS.get(name, ()):
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
            page_was_built = dict.__contains__(nav.pages, name)
            host = dict.get(nav.pages, name)
            mode_was_built = (
                mode is not None
                and host is not None
                and dict.__contains__(getattr(host, "mode_pages", {}), mode)
            )
            construction_started = time.perf_counter()
            if app.state.current_page != name:
                nav.show(name)
            page_construction_ms = (time.perf_counter() - construction_started) * 1000 if not page_was_built else 0.0
            host = dict.get(nav.pages, name)
            mode_started = time.perf_counter()
            if mode is not None and host is not None and host.current_mode != mode:
                host.show_mode(mode)
            mode_construction_ms = (time.perf_counter() - mode_started) * 1000 if mode is not None and not mode_was_built else 0.0
            resize_started = time.perf_counter()
            if size == "zoomed":
                root.state("zoomed")
            else:
                root.state("normal")
                root.geometry(size)
            record = {
                "stage": stage[0],
                "page": name,
                "mode": mode,
                "cold_page": not page_was_built,
                "cold_mode": mode is not None and not mode_was_built,
                "page_construction_ms": page_construction_ms,
                "mode_construction_ms": mode_construction_ms,
                "switch_call_ms": (time.perf_counter() - start) * 1000,
            }
            actions.append(record)

            def settle(last_size=None, stable=0):
                current_size = (root.winfo_width(), root.winfo_height())
                stable = stable + 1 if current_size == last_size and nav._page_layout_ready() else 0
                if stable >= 2:
                    record["resize_settling_ms"] = (time.perf_counter() - resize_started) * 1000
                    root.after(100, step)
                    return
                if time.perf_counter() - resize_started > 5.0:
                    record["resize_settling_timeout"] = True
                    errors.append(f"layout did not settle: {stage[0]}")
                    root.after(100, step)
                    return
                root.after(20, lambda: settle(current_size, stable))

            root.after(20, settle)

        root.after(500, beat)
        root.after(600, step)
        root.mainloop()
    faulthandler.cancel_dump_traceback_later()
    worst = sorted(gaps, key=lambda item: item["ms"], reverse=True)[:10]
    callback_counts = sorted(callbacks.items(), key=lambda item: item[1]["total_ms"], reverse=True)
    result = {
        "cold_construction": [row for row in actions if row["cold_page"] or row["cold_mode"]],
        "warm_switching": [row for row in actions if not row["cold_page"] and not row["cold_mode"]],
        "resize_settling": [
            {"stage": row["stage"], "ms": row.get("resize_settling_ms"),
             "timeout": row.get("resize_settling_timeout", False)}
            for row in actions
        ],
        "callback_counts": callback_counts,
        "worst_event_gaps": worst,
        "errors": errors,
        "actions": actions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"steps": len(actions), "worst_event_gaps": worst[:5], "errors": errors,
                      "cold_construction": len(result["cold_construction"]),
                      "warm_switching": len(result["warm_switching"]),
                      "resize_settling": result["resize_settling"][:5],
                      "callback_counts": result["callback_counts"][:12]}, ensure_ascii=False, indent=2))
    passed = not errors and len(actions) == len(steps) and worst[0]["ms"] <= args.max_gap_ms
    print("UI_RESPONSIVE" if passed else "FAIL: UI event loop stalled")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
