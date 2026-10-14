# AWP1221 Realtime Threshold Bands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Overlay press/release threshold dashed lines and a green press vibration band on the existing AWP1221 realtime voltage window.

**Architecture:** Pure conversion helpers live in `pages/awp1221_realtime_sram.py`. `ADCVoltageWindow` stores threshold ADC counts and vibration bands and draws them in `redraw()`. `AWP1221ModePage` reads current form values, pushes counts into the window, and starts a band on `MCU_TEXT P`.

**Tech Stack:** Python 3.11, tkinter Canvas, unittest (`py -3 -m unittest`)

## Global Constraints

- Upper line color `#2563EB` dashed `(6, 4)`, label `上阈值`
- Lower line color `#9333EA` dashed `(6, 4)`, label `下阈值`
- Vibration band fill `#86EFAC`, no outline, drawn under the ADC curve
- Precision on: `code << 4`; off: 12-bit code as-is
- mV formula: `counts * sample_time_us * load_uA / C_uF / 1000`
- Y ceiling: `max(1.0, max(|samples|, |upper mV|, |lower mV|) * 1.15)`
- Band width: `max(event_repeat, 1) * waveform_period_s + debounce_ms / 1000`
- Infinite ROM loop still uses one period
- `MCU_TEXT P` starts a band even when trigger-read is off
- Do not write registers from the realtime window
- Do not add release (`R`) bands

---

### Task 1: Conversion helpers

**Files:**
- Modify: `pages/awp1221_realtime_sram.py`
- Test: `tests/test_awp1221_realtime_overlay.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `awp1221_threshold_adc_counts(code: int, use_high_12_bits: bool) -> int`
  - `awp1221_adc_counts_to_mv(counts: float, sample_us: float, load_ua: float, capacitance_uf: float) -> float`
  - `awp1221_plot_ceiling(*magnitudes: float) -> float`
  - `awp1221_vibration_band_seconds(waveform_period_s: float, event_repeat: int, debounce_ms: float) -> float`
  - `awp1221_visible_band(start: float, duration_s: float, now: float, span: float) -> tuple[float, float] | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_awp1221_realtime_overlay.py`:

```python
from __future__ import annotations

import unittest

from pages.awp1221_realtime_sram import (
    awp1221_adc_counts_to_mv,
    awp1221_plot_ceiling,
    awp1221_threshold_adc_counts,
    awp1221_vibration_band_seconds,
    awp1221_visible_band,
)


class Awp1221RealtimeHelperTests(unittest.TestCase):
    def test_precision_off_keeps_12_bit_code(self) -> None:
        self.assertEqual(awp1221_threshold_adc_counts(400, False), 400)

    def test_precision_on_shifts_four_bits(self) -> None:
        self.assertEqual(awp1221_threshold_adc_counts(400, True), 6400)

    def test_invalid_code_is_masked_to_12_bits(self) -> None:
        self.assertEqual(awp1221_threshold_adc_counts(0x1400, False), 0x400)

    def test_mv_conversion_matches_live_curve_formula(self) -> None:
        self.assertAlmostEqual(awp1221_adc_counts_to_mv(400, 4.0, 40.0, 2.0), 32.0)

    def test_ceiling_includes_thresholds_when_signal_is_small(self) -> None:
        self.assertAlmostEqual(awp1221_plot_ceiling(0.0, 32.0, 8.0), 32.0 * 1.15)

    def test_ceiling_floor_is_one(self) -> None:
        self.assertEqual(awp1221_plot_ceiling(), 1.0)

    def test_band_width_uses_repeat_and_debounce(self) -> None:
        self.assertAlmostEqual(awp1221_vibration_band_seconds(0.010, 2, 100), 0.120)

    def test_repeat_zero_plays_once(self) -> None:
        self.assertAlmostEqual(awp1221_vibration_band_seconds(0.010, 0, 0), 0.010)

    def test_negative_period_and_debounce_clamp_to_zero(self) -> None:
        self.assertEqual(awp1221_vibration_band_seconds(-1.0, 1, -50), 0.0)

    def test_visible_band_clips_to_window(self) -> None:
        self.assertEqual(awp1221_visible_band(8.0, 4.0, 10.0, 5.0), (8.0, 10.0))

    def test_visible_band_none_when_outside_span(self) -> None:
        self.assertIsNone(awp1221_visible_band(0.0, 1.0, 10.0, 5.0))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m unittest tests.test_awp1221_realtime_overlay -v`

Expected: FAIL with `ImportError` for the helper names.

- [ ] **Step 3: Write minimal implementation**

Add to `pages/awp1221_realtime_sram.py` after `decode_awp1221_adc_value`:

```python
THRESHOLD_UPPER_COLOR = "#2563EB"
THRESHOLD_LOWER_COLOR = "#9333EA"
VIBRATION_BAND_COLOR = "#86EFAC"


def awp1221_threshold_adc_counts(code: int, use_high_12_bits: bool) -> int:
    value = int(code) & 0xFFF
    return value << 4 if use_high_12_bits else value


def awp1221_adc_counts_to_mv(
    counts: float,
    sample_us: float,
    load_ua: float,
    capacitance_uf: float,
) -> float:
    return float(counts) * float(sample_us) * float(load_ua) / float(capacitance_uf) / 1000.0


def awp1221_plot_ceiling(*magnitudes: float) -> float:
    peak = max((abs(float(value)) for value in magnitudes), default=0.0)
    return max(1.0, peak * 1.15)


def awp1221_vibration_band_seconds(
    waveform_period_s: float,
    event_repeat: int,
    debounce_ms: float,
) -> float:
    period = max(0.0, float(waveform_period_s))
    debounce = max(0.0, float(debounce_ms)) / 1000.0
    return max(int(event_repeat), 1) * period + debounce


def awp1221_visible_band(
    start: float,
    duration_s: float,
    now: float,
    span: float,
) -> tuple[float, float] | None:
    if span <= 0 or duration_s <= 0:
        return None
    left = now - span
    right = now
    band_left = max(float(start), left)
    band_right = min(float(start) + float(duration_s), right)
    if band_right <= band_left:
        return None
    return band_left, band_right
```

Export the new names from `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m unittest tests.test_awp1221_realtime_overlay -v`

Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add pages/awp1221_realtime_sram.py tests/test_awp1221_realtime_overlay.py
git commit -m "Add AWP1221 realtime overlay conversion helpers."
```

---

### Task 2: Draw thresholds and bands in ADCVoltageWindow

**Files:**
- Modify: `pages/awp1221_realtime_sram.py` (`ADCVoltageWindow`)
- Test: `tests/test_awp1221_realtime_overlay.py`

**Interfaces:**
- Consumes: helpers from Task 1
- Produces:
  - `ADCVoltageWindow.set_threshold_counts(upper: int | None, lower: int | None) -> None`
  - `ADCVoltageWindow.add_vibration_band(duration_s: float) -> None`
  - `ADCVoltageWindow._conversion_params() -> tuple[float, float, float] | None` returning `(sample_us, load_ua, capacitance_uf)`

- [ ] **Step 1: Write the failing window tests**

Append to `tests/test_awp1221_realtime_overlay.py`:

```python
import time
import tkinter as tk

from pages.awp1221_realtime_sram import ADCVoltageWindow, VIBRATION_BAND_COLOR


class Awp1221RealtimeWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = ADCVoltageWindow(self.root, lambda: None)
        self.window.sample_time.set("4 us")
        self.window.load.set("40 uA")
        self.window.capacitance.set("2")
        self.window.time_scale.set("10")

    def tearDown(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        self.root.destroy()

    def test_set_threshold_counts_stores_values(self) -> None:
        self.window.set_threshold_counts(6400, 1600)
        self.assertEqual(self.window.upper_counts, 6400)
        self.assertEqual(self.window.lower_counts, 1600)

    def test_add_vibration_band_ignores_non_positive_duration(self) -> None:
        self.window.add_vibration_band(0)
        self.window.add_vibration_band(-1)
        self.assertEqual(len(self.window.vibration_bands), 0)

    def test_add_vibration_band_records_start_and_duration(self) -> None:
        before = time.monotonic()
        self.window.add_vibration_band(0.12)
        after = time.monotonic()
        self.assertEqual(len(self.window.vibration_bands), 1)
        start, duration = self.window.vibration_bands[0]
        self.assertTrue(before <= start <= after)
        self.assertAlmostEqual(duration, 0.12)

    def test_redraw_creates_threshold_and_band_items(self) -> None:
        self.window.update_idletasks()
        self.window.canvas.config(width=400, height=300)
        self.window.update_idletasks()
        self.window.set_threshold_counts(6400, 1600)
        self.window.add_vibration_band(1.0)
        self.window.redraw()
        fills = [self.window.canvas.itemcget(item, "fill") for item in self.window.canvas.find_all()]
        self.assertIn("#2563EB", fills)
        self.assertIn("#9333EA", fills)
        self.assertIn(VIBRATION_BAND_COLOR, fills)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `py -3 -m unittest tests.test_awp1221_realtime_overlay -v`

Expected: FAIL on missing `set_threshold_counts` / `upper_counts`.

- [ ] **Step 3: Implement window overlay**

In `ADCVoltageWindow.__init__`, after `self.points = ...`:

```python
        self.upper_counts: int | None = None
        self.lower_counts: int | None = None
        self.vibration_bands: deque[tuple[float, float]] = deque(maxlen=64)
```

Add methods before `add_sample`:

```python
    def set_threshold_counts(self, upper: int | None, lower: int | None) -> None:
        self.upper_counts = upper
        self.lower_counts = lower
        self.redraw()

    def add_vibration_band(self, duration_s: float) -> None:
        width = float(duration_s)
        if width <= 0:
            return
        self.vibration_bands.append((time.monotonic(), width))
        self.redraw()

    def _conversion_params(self) -> tuple[float, float, float] | None:
        try:
            capacitance = float(self.capacitance.get())
            sample_us = float(self.sample_time.get().split()[0])
            load_ua = float(self.load.get().split()[0])
            if capacitance <= 0:
                raise ValueError
        except ValueError:
            return None
        return sample_us, load_ua, capacitance
```

Replace `redraw()` so conversion, ceiling, bands, dashed lines, then the curve use the helpers. Keep the existing control/layout code. The body of `redraw` after canvas size checks:

```python
        params = self._conversion_params()
        try:
            span = float(self.time_scale.get())
            if span <= 0 or params is None:
                raise ValueError
            duration, current, capacitance = params
        except ValueError:
            self.voltage.set("请输入有效的 C 和时间范围")
            return
        values = [
            (stamp, awp1221_adc_counts_to_mv(signed, duration, current, capacitance))
            for stamp, signed in self.points
        ]
        self.voltage.set(f"{values[-1][1]:.3f} mV" if values else "— mV")
        now = time.monotonic()
        visible = [(stamp, value) for stamp, value in values if stamp >= now - span]
        upper_mv = (
            awp1221_adc_counts_to_mv(self.upper_counts, duration, current, capacitance)
            if self.upper_counts is not None
            else None
        )
        lower_mv = (
            awp1221_adc_counts_to_mv(self.lower_counts, duration, current, capacitance)
            if self.lower_counts is not None
            else None
        )
        ceiling = awp1221_plot_ceiling(
            *(value for _, value in visible),
            *(item for item in (upper_mv, lower_mv) if item is not None),
        )
        def y_at(value: float) -> float:
            return (top + bottom) / 2 - value / ceiling * (bottom - top) / 2
        def x_at(stamp: float) -> float:
            return left + (stamp - now + span) / span * (right - left)
        for start, band_duration in self.vibration_bands:
            visible_band = awp1221_visible_band(start, band_duration, now, span)
            if visible_band is None:
                continue
            canvas.create_rectangle(
                x_at(visible_band[0]),
                top,
                x_at(visible_band[1]),
                bottom,
                fill=VIBRATION_BAND_COLOR,
                outline="",
            )
        for index in range(6):
            y = bottom - (bottom - top) * index / 5
            canvas.create_line(left, y, right, y, fill="#e4e8ed")
            canvas.create_text(left - 8, y, text=f"{ceiling * (2 * index / 5 - 1):.1f}", anchor="e", fill="#596575")
            x = left + (right - left) * index / 5
            canvas.create_line(x, top, x, bottom, fill="#e4e8ed")
            canvas.create_text(x, bottom + 15, text=f"{-span + span * index / 5:.0f}", fill="#596575")
        canvas.create_text(12, top, text="mV", anchor="w")
        canvas.create_text(right, bottom + 32, text="Time (s)", anchor="e")
        for mv, color, label in (
            (upper_mv, THRESHOLD_UPPER_COLOR, "上阈值"),
            (lower_mv, THRESHOLD_LOWER_COLOR, "下阈值"),
        ):
            if mv is None:
                continue
            y = y_at(mv)
            canvas.create_line(left, y, right, y, fill=color, dash=(6, 4), width=2)
            canvas.create_text(right - 4, y - 8, text=f"{label} {mv:.3f} mV", anchor="e", fill=color)
        coords: list[float] = []
        for stamp, value in visible:
            coords.extend((x_at(stamp), y_at(value)))
        if len(coords) >= 4:
            canvas.create_line(*coords, fill="#2f80ed", width=2)
```

Keep `canvas.delete("all")` and the width/height early-return at the top of `redraw`.

- [ ] **Step 4: Run tests**

Run: `py -3 -m unittest tests.test_awp1221_realtime_overlay -v`

Expected: PASS. If Tk cannot open a display, skip window tests with `if os.environ.get("TK_SILENT_DESTROY")` is not needed; Windows desktop has Tk.

- [ ] **Step 5: Commit**

```bash
git add pages/awp1221_realtime_sram.py tests/test_awp1221_realtime_overlay.py
git commit -m "Draw AWP1221 realtime threshold lines and vibration bands."
```

---

### Task 3: Feed page form values and MCU_TEXT P into the window

**Files:**
- Modify: `pages/awp1221_page.py`
- Test: `tests/test_awp1221_realtime_overlay.py`

**Interfaces:**
- Consumes: `awp1221_threshold_adc_counts`, `awp1221_vibration_band_seconds`, `ADCVoltageWindow.set_threshold_counts`, `ADCVoltageWindow.add_vibration_band`
- Produces:
  - `AWP1221ModePage._realtime_threshold_counts() -> tuple[int | None, int | None]`
  - `AWP1221ModePage._realtime_waveform_period_s() -> float`
  - `AWP1221ModePage._realtime_press_repeat() -> int`
  - `AWP1221ModePage._realtime_vibration_duration_s() -> float`
  - `AWP1221ModePage._push_realtime_thresholds() -> None`

- [ ] **Step 1: Write failing page-logic tests**

Append:

```python
from types import SimpleNamespace
from pages.awp1221_page import AWP1221ModePage


def _var(value: object) -> SimpleNamespace:
    return SimpleNamespace(get=lambda: value)


class Awp1221RealtimePageLogicTests(unittest.TestCase):
    def test_button_mode_uses_first_press_and_release_stages(self) -> None:
        page = object.__new__(AWP1221ModePage)
        page.mode_key = "button_mode"
        page._button_stage_vars = [
            {
                "event_mode": _var("press"),
                "threshold": _var("400"),
                "use_high_12_bits": _var(True),
                "repeat": _var("2"),
            },
            {
                "event_mode": _var("release"),
                "threshold": _var("100"),
                "use_high_12_bits": _var(True),
                "repeat": _var("0"),
            },
        ]
        self.assertEqual(page._realtime_threshold_counts(), (6400, 1600))
        self.assertEqual(page._realtime_press_repeat(), 2)

    def test_full_mode_uses_visible_stage_press_release(self) -> None:
        page = object.__new__(AWP1221ModePage)
        page.mode_key = "full_mode"
        page._visible_stage_index = 1
        page._stage_vars = [
            {},
            {
                "press_threshold": {"threshold": _var("300")},
                "release_threshold": {"threshold": _var("80")},
                "use_high_12_bits": _var(False),
                "wave": {"press_repeat": _var("3")},
            },
        ]
        self.assertEqual(page._realtime_threshold_counts(), (300, 80))
        self.assertEqual(page._realtime_press_repeat(), 3)

    def test_invalid_threshold_omits_that_line(self) -> None:
        page = object.__new__(AWP1221ModePage)
        page.mode_key = "button_mode"
        page._button_stage_vars = [
            {
                "event_mode": _var("press"),
                "threshold": _var("abc"),
                "use_high_12_bits": _var(False),
                "repeat": _var("0"),
            },
            {
                "event_mode": _var("release"),
                "threshold": _var("100"),
                "use_high_12_bits": _var(False),
                "repeat": _var("0"),
            },
        ]
        self.assertEqual(page._realtime_threshold_counts(), (None, 100))

    def test_vibration_duration_combines_period_repeat_debounce(self) -> None:
        page = object.__new__(AWP1221ModePage)
        page._realtime_waveform_period_s = lambda: 0.01
        page._realtime_press_repeat = lambda: 2
        page.debounce_time_var = _var("100")
        self.assertAlmostEqual(page._realtime_vibration_duration_s(), 0.12)

    def test_process_p_starts_band_without_trigger_read(self) -> None:
        bands: list[float] = []
        page = object.__new__(AWP1221ModePage)
        page._adc_realtime_active = True
        page._adc_trigger_read_active = False
        page._adc_detection_active = True
        page._shared_state_for = lambda: None
        page._adc_voltage_window = SimpleNamespace(
            winfo_exists=lambda: True,
            add_vibration_band=lambda duration: bands.append(duration),
        )
        page._realtime_vibration_duration_s = lambda: 0.12
        page._request_adc_value_read = lambda **kwargs: None
        AWP1221ModePage._process_adc_trigger_log(page, "MCU_TEXT P")
        self.assertEqual(bands, [0.12])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -3 -m unittest tests.test_awp1221_realtime_overlay -v`

Expected: FAIL, `_realtime_threshold_counts` missing.

- [ ] **Step 3: Implement page wiring**

Import helpers at the existing realtime import:

```python
from pages.awp1221_realtime_sram import (
    AWP1221RealtimeMixin,
    AWP1221SramEditor,
    AWP1221_ADC_LIVE_REGISTER,
    awp1221_threshold_adc_counts,
    awp1221_vibration_band_seconds,
    decode_awp1221_adc_value,
)
```

In `AWP1221ModePage.__init__`, set `self._visible_stage_index = 0` before `_build_ui()`, and after `_build_ui()` call `self._bind_realtime_overlay_traces()`.

Add `self._visible_stage_index = stage_index` at the end of `_show_stage`, then `self._push_realtime_thresholds()`.

Add these methods near `_request_adc_value_read`:

```python
    def toggle_adc_realtime(self) -> None:
        super().toggle_adc_realtime()
        if self._adc_realtime_active:
            self._push_realtime_thresholds()

    def _parse_realtime_code(self, text: object) -> int | None:
        try:
            return int(str(text).strip(), 0)
        except (TypeError, ValueError):
            return None

    def _realtime_threshold_counts(self) -> tuple[int | None, int | None]:
        if self.mode_key == "button_mode":
            upper = lower = None
            for variables in getattr(self, "_button_stage_vars", ()):
                code = self._parse_realtime_code(variables["threshold"].get())
                if code is None:
                    continue
                counts = awp1221_threshold_adc_counts(
                    code,
                    bool(variables["use_high_12_bits"].get()),
                )
                mode = str(variables["event_mode"].get())
                if mode == "press" and upper is None:
                    upper = counts
                elif mode == "release" and lower is None:
                    lower = counts
            return upper, lower
        stages = getattr(self, "_stage_vars", ())
        if not stages:
            return None, None
        index = min(max(getattr(self, "_visible_stage_index", 0), 0), len(stages) - 1)
        variables = stages[index]
        precision = bool(variables["use_high_12_bits"].get())
        press = self._parse_realtime_code(variables["press_threshold"]["threshold"].get())
        release = self._parse_realtime_code(variables["release_threshold"]["threshold"].get())
        return (
            awp1221_threshold_adc_counts(press, precision) if press is not None else None,
            awp1221_threshold_adc_counts(release, precision) if release is not None else None,
        )

    def _realtime_press_repeat(self) -> int:
        if self.mode_key == "button_mode":
            for variables in getattr(self, "_button_stage_vars", ()):
                if str(variables["event_mode"].get()) != "press":
                    continue
                value = self._parse_realtime_code(variables["repeat"].get())
                return value if value is not None else 0
            return 0
        stages = getattr(self, "_stage_vars", ())
        if not stages:
            return 0
        index = min(max(getattr(self, "_visible_stage_index", 0), 0), len(stages) - 1)
        value = self._parse_realtime_code(stages[index]["wave"]["press_repeat"].get())
        return value if value is not None else 0

    def _realtime_waveform_period_s(self) -> float:
        if self._sinc_selected():
            try:
                return float(self._build_current_sinc_plan().duration_us) / 1_000_000.0
            except Exception:
                return 0.0
        try:
            depth = int(self.rom_depth_var.get().strip(), 0)
            point_time = int(self.rom_point_time_var.get().strip(), 0)
            half_cycle_code = self._parse_binary_option(self.rom_half_cycle_var.get())
            if depth <= 0 or point_time <= 0:
                return 0.0
            effective = awp1221_waveform_effective_depth_points(
                self.rom_waveform_var.get(),
                depth,
                half_cycle_code,
            )
            return effective * point_time / 1_000_000.0
        except Exception:
            return 0.0

    def _realtime_vibration_duration_s(self) -> float:
        try:
            debounce_ms = float(str(self.debounce_time_var.get()).strip())
        except (TypeError, ValueError):
            debounce_ms = 0.0
        return awp1221_vibration_band_seconds(
            self._realtime_waveform_period_s(),
            self._realtime_press_repeat(),
            debounce_ms,
        )

    def _push_realtime_thresholds(self) -> None:
        window = getattr(self, "_adc_voltage_window", None)
        if window is None or not window.winfo_exists():
            return
        upper, lower = self._realtime_threshold_counts()
        window.set_threshold_counts(upper, lower)

    def _bind_realtime_overlay_traces(self) -> None:
        variables: list[tk.Variable] = [self.debounce_time_var]
        for stage in getattr(self, "_button_stage_vars", ()):
            variables.extend(
                (
                    stage["threshold"],
                    stage["event_mode"],
                    stage["use_high_12_bits"],
                    stage["repeat"],
                )
            )
        for stage in getattr(self, "_stage_vars", ()):
            variables.extend(
                (
                    stage["press_threshold"]["threshold"],
                    stage["release_threshold"]["threshold"],
                    stage["use_high_12_bits"],
                    stage["wave"]["press_repeat"],
                )
            )
        for variable in (
            self.rom_waveform_var,
            self.rom_depth_var,
            self.rom_point_time_var,
            self.rom_half_cycle_var,
            *self._sinc_time_variables(),
            getattr(self, "sinc_depth_var", None),
        ):
            if variable is not None:
                variables.append(variable)
        for variable in variables:
            variable.trace_add("write", lambda *_: self._push_realtime_thresholds())
```

Replace `_process_adc_trigger_log` with:

```python
    def _process_adc_trigger_log(self, line: str) -> None:
        shared_state = self._shared_state_for()
        detection_active = (
            bool(shared_state.detection_active)
            if shared_state is not None
            else bool(getattr(self, "_adc_detection_active", False))
        )
        detection_device_addr = (
            shared_state.detection_device_addr
            if shared_state is not None
            else getattr(self, "_adc_detection_device_addr", None)
        )
        upper = line.upper()
        marker = "MCU_TEXT"
        marker_index = upper.find(marker)
        if marker_index < 0:
            return
        payload = upper[marker_index + len(marker):].strip()
        if payload == "P" and self._adc_realtime_active:
            window = self._adc_voltage_window
            if window is not None and window.winfo_exists():
                window.add_vibration_band(self._realtime_vibration_duration_s())
        if not self._adc_trigger_read_active or not detection_active:
            return
        if payload in {"P", "R"}:
            self._request_adc_value_read(
                show_error=False,
                device_addr=detection_device_addr,
            )
```

- [ ] **Step 4: Run tests**

Run: `py -3 -m unittest tests.test_awp1221_realtime_overlay -v`

Expected: PASS.

Also: `py -3 -m py_compile pages/awp1221_realtime_sram.py pages/awp1221_page.py`

- [ ] **Step 5: Commit**

```bash
git add pages/awp1221_page.py pages/awp1221_realtime_sram.py tests/test_awp1221_realtime_overlay.py
git commit -m "Overlay AWP1221 realtime thresholds and press vibration bands."
```

---

## Spec coverage

- Upper/lower colors, dashes, labels: Task 2
- Button first press/release stages, full visible stage, Precision `<< 4`: Task 1 + Task 3
- Same mV formula, Y includes thresholds: Task 1 + Task 2
- Page form live update: Task 3 traces + `_push_realtime_thresholds`
- `MCU_TEXT P` band without trigger-read: Task 3
- Width = repeat * period + debounce; infinite loop one period: Task 1 + Task 3 `_realtime_waveform_period_s`
- Band under curve, clipped to time window: Task 2
- Out of scope (no window writes, no R band): no task adds them
