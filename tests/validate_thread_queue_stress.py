"""Manual stress/latency harness for worker->UI hand-off (not a unittest).

usage: python tests/validate_thread_queue_stress.py close|flood|pages
Prints one JSON line.  Run from the repo root with a display.
"""
from __future__ import annotations

import io
import json
import statistics
import sys
import threading
import time
import contextlib

sys.path.insert(0, ".")
from app.application import PiezoApplication  # noqa: E402


def heartbeat(app, gaps, stop):
    last = [time.perf_counter()]

    def beat():
        now = time.perf_counter()
        gaps.append((now - last[0]) * 1000)
        last[0] = now
        if not stop.is_set():
            app.root.after(10, beat)

    app.root.after(10, beat)


def flood(app, threads, seconds, rate=None):
    stop = threading.Event()
    sent = [0] * threads

    def run(i):
        n = 0
        while not stop.is_set():
            try:
                app.serial.emit_log(f"TX flood {i} {n}")
            except Exception:
                pass
            n += 1
            sent[i] = n
            if rate:
                time.sleep(threads / rate)
            elif n % 50 == 0:
                time.sleep(0)

    ts = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(threads)]
    for t in ts:
        t.start()
    return stop, ts, sent


def mode_close(rounds=8):
    problems = 0
    errs = []
    for _ in range(rounds):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            app = PiezoApplication()
            stop, ts, _sent = flood(app, 6, 0)
            app.root.after(400, app.close)
            app.root.mainloop()
            time.sleep(0.4)  # workers keep emitting after the loop has exited
            stop.set()
            for t in ts:
                t.join(2)
        text = buf.getvalue()
        if "Traceback" in text or "main loop" in text or "Exception" in text:
            problems += 1
            errs.append(text.strip().splitlines()[-1][:160])
    return {"rounds": rounds, "rounds_with_errors": problems, "last_errors": errs[-2:]}


def mode_flood(rate=5000, seconds=3.0):
    app = PiezoApplication()
    app.navigation.show("awp1922")
    gaps, hb_stop = [], threading.Event()
    out = {}
    switch_ms = []

    def start():
        heartbeat(app, gaps, hb_stop)
        out["stop"], out["ts"], out["sent"] = flood(app, 4, 0, rate=rate)
        app.root.after(int(seconds * 1000), finish)
        app.root.after(500, switch)

    def switch(i=[0]):
        if "t_end" in out:
            return
        keys = ["awp1922", "awp1018", "awp1221", "home"]
        t0 = time.perf_counter()
        app.navigation.show(keys[i[0] % 4])
        app.root.update_idletasks()
        switch_ms.append((time.perf_counter() - t0) * 1000)
        i[0] += 1
        app.root.after(250, switch)

    def finish():
        out["stop"].set()
        out["t_end"] = time.perf_counter()
        app.root.after(1, wait_drain)

    def wait_drain():
        disp = getattr(app, "ui_dispatcher", None)
        if any(t.is_alive() for t in out["ts"]) or (disp and disp.pending()):
            if time.perf_counter() - out["t_end"] < 30:
                app.root.after(5, wait_drain)
                return
        out["drain_s"] = time.perf_counter() - out["t_end"]
        hb_stop.set()
        app.root.quit()

    app.root.after(300, start)
    app.root.mainloop()
    for t in out["ts"]:
        t.join(5)
    total = sum(out["sent"])
    g = sorted(gaps[1:])
    res = {
        "target_rate": rate,
        "lines_per_s": round(total / seconds),
        "gap_p50_ms": round(statistics.median(g), 1),
        "gap_p99_ms": round(g[max(0, int(len(g) * 0.99) - 1)], 1),
        "gap_max_ms": round(g[-1], 1),
        "page_switch_med_max_ms": [round(statistics.median(switch_ms), 1), round(max(switch_ms), 1)] if switch_ms else None,
        "drain_after_stop_s": round(out["drain_s"], 2),
    }
    app.close()
    return res


def mode_pages(rate=2000):
    app = PiezoApplication()
    keys = [k for k in app.navigation.pages if k != "home"] + ["home"]
    for k in keys:  # warm
        app.navigation.show(k)
        app.root.update()

    def bench():
        times = []
        for _ in range(3):
            for k in keys:
                t0 = time.perf_counter()
                app.navigation.show(k)
                app.root.update()
                times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        return round(statistics.median(times), 1), round(times[-1], 1)

    idle = bench()
    stop, ts, _ = flood(app, 2, 0, rate=rate)
    busy = bench()
    stop.set()
    for t in ts:
        t.join()
    app.close()
    return {"idle_med_max_ms": idle, f"flood{rate}_med_max_ms": busy}


if __name__ == "__main__":
    mode = sys.argv[1]
    args = [int(a) for a in sys.argv[2:]]
    print(json.dumps({"mode": mode, **{"close": mode_close, "flood": mode_flood, "pages": mode_pages}[mode](*args)}))
