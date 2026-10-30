"""Windows offline tab benchmark; real widgets, forbidden serial/bridge I/O.

Run: python -m tests.benchmark_button_tabs --output scratch/tabs.json
Uses the event loop (no nested idle draining in the measured click path).
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import importlib.abc
import importlib.util
from pathlib import Path
import sys
import time

from tests.tk_cleanup import cancel_after_jobs
from tests.validate_responsive_layout import mocked_application


def fingerprints():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("app", "core", "pages", "tests", "styles", "config")
            for p in Path(folder).glob("*.py")}


class BaselineSources(importlib.abc.MetaPathFinder):
    """Load saved dirty source without swapping any working-tree files."""
    def __init__(self, folder):
        self.folder = folder

    def find_spec(self, fullname, path=None, target=None):
        source = self.folder.joinpath(*fullname.split(".")).with_suffix(".py")
        if source.is_file():
            original = Path.cwd().joinpath(*fullname.split(".")).with_suffix(".py")
            return importlib.util.spec_from_loader(fullname, SavedSource(source, original),
                                                   origin=str(original))
        return None


class SavedSource(importlib.abc.Loader):
    def __init__(self, saved, original):
        self.saved, self.original = saved, original

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        # Asset/document paths must still resolve against the live project.
        module.__file__ = str(self.original)
        exec(compile(self.saved.read_bytes(), str(self.original), "exec"), module.__dict__)


def run(repeat=3, profiling=False):
    before = fingerprints()
    results = []
    with mocked_application() as app:
        app.set_animations(True)
        root = app.root
        root.geometry("1700x980")

        def pump(ms=250):
            root.after(ms, root.quit)
            root.mainloop()

        pump()
        for key in ("awp1221", "awp1921_1923"):
            app.navigation.show(key)
            pump(700)
            host = app.navigation.pages[key]
            page = host.mode_pages["button_mode"]
            # Construct Full once, then return, so hidden-view work is visible.
            host.show_mode("full_mode")
            pump()
            host.show_mode("button_mode")
            pump()
            if key == "awp1221":
                actions = [(name, page._button_wave_tabs[name].invoke)
                           for name in ("sinc", "rom", "sram", "rom")]
            else:
                wave = page.waveform_page
                actions = [(name, wave.source_buttons[name].invoke)
                           for name in ("sinc", "ram", "sinc", "ram")]
            for name, action in actions * repeat:
                gaps = []
                times = [time.perf_counter()]
                jobs = []

                def beat():
                    now = time.perf_counter()
                    gaps.append((now-times[-1])*1000)
                    times.append(now)
                    jobs[:] = [root.after(16, beat)]

                jobs[:] = [root.after(16, beat)]
                profile = cProfile.Profile()
                if profiling:
                    profile.enable()
                click = time.perf_counter()
                action()
                returned = time.perf_counter()
                pump(300)
                if profiling:
                    profile.disable()
                root.after_cancel(jobs[0])
                tracked = {"_refresh_waveform_controls", "_apply_settings_model_to_view",
                           "_build_current_sinc_plan", "draw_timed_waveform", "_draw_strip",
                           "_fit_rule_columns", "_draw_button", "_update_waveform_specific_fields"}
                counts = {}
                for stat in profile.getstats():
                    code = stat.code
                    name_code = getattr(code, "co_name", "")
                    if name_code in tracked:
                        counts[name_code] = {"calls": stat.callcount,
                                             "total_ms": round(stat.totaltime*1000, 3)}
                results.append({"page": key, "tab": name,
                                "click_return_ms": round((returned-click)*1000, 3),
                                "max_heartbeat_gap_ms": round(max(gaps, default=0), 3),
                                "calls": counts})
            subset = [item for item in results if item["page"] == key]
            print(key, json.dumps(subset, ensure_ascii=False), flush=True)
        cancel_after_jobs(root)
    after = fingerprints()
    return {"records": results, "source_before": before,
            "changed_source": [p for p in before if before[p] != after.get(p)]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--baseline-source", type=Path)
    options = parser.parse_args()
    if options.baseline_source:
        sys.meta_path.insert(0, BaselineSources(options.baseline_source.resolve()))
    data = run(options.repeat, options.profile)
    data["baseline_source"] = str(options.baseline_source) if options.baseline_source else None
    data["profiling"] = options.profile
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    assert not data["changed_source"], data["changed_source"]
