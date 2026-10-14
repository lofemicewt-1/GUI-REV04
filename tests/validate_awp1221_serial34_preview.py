"""Offline Tk preview check for AWP1221 serial3/4 SIN+SINC plans.

Run with ``py tests/validate_awp1221_serial34_preview.py``. Serial and I2C
transport methods are fenced by the shared mocked-application fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.validate_responsive_layout import _capture_window_bmp, _flush, mocked_application
from pages.awp1221_page import AWP1221_SINC_SERIAL_OPTIONS


EVIDENCE_DIR = ROOT / "docs" / "specs" / "awp1221_serial34_evidence"


def _show_repeat_control(mode, root) -> dict[str, int]:
    controls_canvas = mode._waveform_selector_scroll.canvas
    repeat_entry = mode._selector_widgets["awp1221_sinc_sin_repeat"][1]
    for step in range(21):
        controls_canvas.yview_moveto(step / 20)
        _flush(root)
        viewport_top = controls_canvas.winfo_rooty()
        viewport_bottom = viewport_top + controls_canvas.winfo_height()
        entry_top = repeat_entry.winfo_rooty()
        entry_bottom = entry_top + repeat_entry.winfo_height()
        if entry_top >= viewport_top and entry_bottom <= viewport_bottom:
            return {
                "viewport_top": viewport_top,
                "viewport_bottom": viewport_bottom,
                "entry_top": entry_top,
                "entry_bottom": entry_bottom,
            }
    raise AssertionError(
        "SIN repeat entry is not fully visible in selector viewport: "
        f"entry=({repeat_entry.winfo_rooty()}, {repeat_entry.winfo_rooty() + repeat_entry.winfo_height()}), "
        f"viewport=({controls_canvas.winfo_rooty()}, {controls_canvas.winfo_rooty() + controls_canvas.winfo_height()})"
    )


def main() -> int:
    records = []
    with mocked_application() as app:
        app.navigation.show("awp1221")
        page = app.navigation.pages["awp1221"]
        page.show_mode("button_mode")
        mode = page.mode_pages["button_mode"]
        serial_options = {
            f"serial{index + 1}": option
            for index, option in enumerate(AWP1221_SINC_SERIAL_OPTIONS)
        }
        mode.rom_waveform_var.set("SINC")
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        for serial, times in (
            ("serial3", ("4", "7", "8", "9")),
            ("serial4", ("5", "9", "11", "13")),
        ):
            mode.sinc_serial_var.set(serial_options[serial])
            for variable, value in zip(mode._sinc_time_variables(), times):
                variable.set(value)
            repeat_count = "2" if serial == "serial3" else "1"
            mode.sinc_sin_repeat_var.set(repeat_count)
            mode._draw_button_rom_preview()
            _flush(app.root)
            plan = mode._build_current_sinc_plan()
            assert plan.writable, (serial, plan.reason)
            assert len(plan.slices) == 4
            assert len(plan.sram_words) == 16
            assert len(mode.rom_canvas.find_all()) >= 3
            assert mode.rom_frequency_var.get()
            controls_canvas = mode._waveform_selector_scroll.canvas
            controls_canvas.yview_moveto(0.0)
            _flush(app.root)
            capture = EVIDENCE_DIR / f"{serial}_preview.bmp"
            error = _capture_window_bmp(app.root, capture)
            assert error is None, error
            controls_canvas.yview_moveto(1.0)
            _flush(app.root)
            times_capture = EVIDENCE_DIR / f"{serial}_four_times.bmp"
            error = _capture_window_bmp(app.root, times_capture)
            assert error is None, error
            repeat_geometry = _show_repeat_control(mode, app.root)
            _flush(app.root)
            repeat_capture = EVIDENCE_DIR / f"{serial}_repeat_control.bmp"
            error = _capture_window_bmp(app.root, repeat_capture)
            assert error is None, error
            records.append(
                {
                    "serial": serial,
                    "times_us_by_slice": list(times),
                    "sin_repeat_count": plan.params.sin_repeat_count,
                    "slices": list(plan.slices),
                    "sram_word_addresses": list(plan.sram_words),
                    "duration_us": plan.duration_us,
                    "sine_frequency_hz": 1_000_000 / (plan.params.depth_points * int(times[0])),
                    "match_text": mode.sinc_match_var.get(),
                    "frequency_text": mode.rom_frequency_var.get(),
                    "repeat_control_geometry": repeat_geometry,
                    "screenshots": [
                        str(capture.relative_to(ROOT).as_posix()),
                        str(times_capture.relative_to(ROOT).as_posix()),
                        str(repeat_capture.relative_to(ROOT).as_posix()),
                    ],
                }
            )
        mode.sinc_serial_var.set(serial_options["serial3"])
        assert mode.sinc_sin_repeat_var.get() == "2"
        mode.sinc_serial_var.set(serial_options["serial4"])
        assert mode.sinc_sin_repeat_var.get() == "1"
        records.append({"group_repeat_restore": "serial3=2, serial4=1"})
        mode.sinc_serial_var.set(serial_options["serial3"])
        mode.sinc_sin_repeat_var.set("256")
        mode._draw_button_rom_preview()
        _flush(app.root)
        max_repeat_plan = mode._build_current_sinc_plan()
        waveform_item = mode.rom_canvas.find_all()[-1]
        sample_coordinate_count = len(mode.rom_canvas.coords(waveform_item))
        assert max_repeat_plan.duration_us == 512 * 4 * 256 + 256 * (7 + 8 + 9)
        assert sample_coordinate_count <= 2 * (2048 + 3 * 54)
        max_repeat_capture = EVIDENCE_DIR / "serial3_repeat256_preview.bmp"
        error = _capture_window_bmp(app.root, max_repeat_capture)
        assert error is None, error
        records.append(
            {
                "serial": "serial3",
                "sin_repeat_count": 256,
                "duration_us": max_repeat_plan.duration_us,
                "sine_frequency_hz": 1_000_000 / (512 * 4),
                "preview_coordinate_count": sample_coordinate_count,
                "screenshot": str(max_repeat_capture.relative_to(ROOT).as_posix()),
            }
        )
        page.show_mode("full_mode")
        full_mode = page.mode_pages["full_mode"]
        full_mode.rom_waveform_var.set("SINC")
        full_mode.sinc_serial_var.set(serial_options["serial4"])
        for variable, value in zip(full_mode._sinc_time_variables(), ("5", "9", "11", "13")):
            variable.set(value)
        full_mode.sinc_sin_repeat_var.set("2")
        full_mode._draw_button_rom_preview()
        full_mode.tools_notebook.select(full_mode.tools_notebook.tabs()[1])
        _flush(app.root)
        full_plan = full_mode._build_current_sinc_plan()
        assert full_plan.writable and len(full_plan.sram_words) == 16
        assert full_mode.rom_canvas.find_all()[-1]
        full_capture = EVIDENCE_DIR / "full_mode_serial4_preview.bmp"
        error = _capture_window_bmp(app.root, full_capture)
        assert error is None, error
        records.append(
            {
                "mode": "full_mode",
                "serial": "serial4",
                "times_us_by_slice": [5, 9, 11, 13],
                "sin_repeat_count": full_plan.params.sin_repeat_count,
                "duration_us": full_plan.duration_us,
                "waveform_items": len(full_mode.rom_canvas.find_all()),
                "screenshot": str(full_capture.relative_to(ROOT).as_posix()),
            }
        )
    result = EVIDENCE_DIR / "offline_preview_result.json"
    result.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result)
    print(json.dumps(records, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
