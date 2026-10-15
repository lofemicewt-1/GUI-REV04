"""Timing + screenshots for the Dear PyGui prototype (dev tool, not needed to run the app).
py -3 dpg_proto/bench.py [--shots OUTDIR]"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as M  # noqa: E402  (T_START set at import)
dpg = M.dpg
app = M.App(); R = dpg.render_dearpygui_frame
R(); R(); app._layout()
print(f"startup_to_first_frame {(time.perf_counter() - M.T_START) * 1000:.0f} ms (scale {M.SCALE}, font {app.font_source})", flush=True)

def pump(s):
    e = time.perf_counter() + s
    while time.perf_counter() < e: R()

def settled(fn):
    t = time.perf_counter(); fn(); R(); R(); return (time.perf_counter() - t) * 1000

pump(0.5)
print(f"first home->awp1221 {settled(lambda: app.show_page('awp1221')):.1f} ms", flush=True); pump(0.3)
print(f"first awp1221->home {settled(lambda: app.show_page('home')):.1f} ms", flush=True); pump(0.3)
a, b = [], []
for _ in range(10):
    a.append(settled(lambda: app.show_page("awp1221"))); pump(0.1)
    b.append(settled(lambda: app.show_page("home"))); pump(0.1)
med = lambda x: sorted(x)[len(x) // 2]
print(f"repeat home->awp1221 median {med(a):.1f} ms max {max(a):.1f}", flush=True)
print(f"repeat awp1221->home median {med(b):.1f} ms max {max(b):.1f}", flush=True)
fr = []
t0 = time.perf_counter()
for _ in range(120):
    t = time.perf_counter(); R(); fr.append((time.perf_counter() - t) * 1000)
print(f"idle frame median {med(fr):.1f} ms", flush=True)
if "--shots" in sys.argv:
    out = sys.argv[sys.argv.index("--shots") + 1]
    for k in ("home", "awp1221"):
        app.show_page(k); pump(0.5); dpg.output_frame_buffer(file=os.path.join(out, f"dpg2_{k}.png")); pump(0.5)
    print("shots saved", flush=True)
dpg.destroy_context()
