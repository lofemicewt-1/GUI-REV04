# AWP1221 Realtime Settings Write and Quiet 0x13 Poll Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make realtime 0x13 polls silent in the serial log, write sample-time and ADC load from the voltage window, unify 0x05[11:10] labels to 4/8/16/32 us, and rename C to 陶瓷电容等效容值.

**Architecture:** Sample-time/load bit helpers live next to the existing ADC encode/decode. Quiet polls wrap the existing `SerialManager.suppress_logging()` around the 0x13 worker read. The realtime window comboboxes call host RMW writes.

**Tech Stack:** Python 3.11, tkinter, unittest (`py -3 -m unittest`)

## Global Constraints

- 0x13 realtime polls: no `TX` and no `MCU` log lines
- Manual ADC read, window/page writes, errors, and `MCU_TEXT P`/`R` still log
- 0x05 bit11-10 labels: `4 us`, `8 us`, `16 us`, `32 us`
- 0x1F bit2: 0 = `40 uA`, 1 = `160 uA`; do not change bit3
- Capacitance label: `陶瓷电容等效容值 (uF)`; conversion only
- Presets that used `6 us` become `4 us`

---

### Task 1: Sample-time options and load bit2 helpers

**Files:**
- Modify: `pages/awp1221_page.py` (`AWP1221_ADC_SAMPLE_UPDATE_OPTIONS` and preset `"6 us"` strings)
- Modify: `pages/awp1221_realtime_sram.py` (helpers + constants)
- Test: `tests/test_awp1221_realtime_settings.py`

**Interfaces:**
- Consumes: existing `encode_awp1221_adc_settings_registers` / `decode_awp1221_adc_settings_registers`
- Produces:
  - `AWP1221_ADC_SAMPLE_UPDATE_OPTIONS = ("4 us", "8 us", "16 us", "32 us")`
  - `AWP1221_ADC_LOAD_BIT = 1 << 2`
  - `awp1221_adc_load_label(register_1f: int) -> str`
  - `awp1221_merge_adc_load(current: int, load: str) -> int`
  - `awp1221_merge_sample_time(current: int, sample_time: str) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_awp1221_realtime_settings.py`:

```python
from __future__ import annotations

import unittest

from pages.awp1221_page import (
    AWP1221_ADC_SAMPLE_UPDATE_OPTIONS,
    decode_awp1221_adc_settings_registers,
    encode_awp1221_adc_settings_registers,
)
from pages.awp1221_realtime_sram import (
    awp1221_adc_load_label,
    awp1221_merge_adc_load,
    awp1221_merge_sample_time,
)


class Awp1221SampleTimeOptionTests(unittest.TestCase):
    def test_options_are_4_8_16_32(self) -> None:
        self.assertEqual(
            AWP1221_ADC_SAMPLE_UPDATE_OPTIONS,
            ("4 us", "8 us", "16 us", "32 us"),
        )

    def test_encode_decode_round_trip(self) -> None:
        for label in AWP1221_ADC_SAMPLE_UPDATE_OPTIONS:
            encoded_05, _encoded_06 = encode_awp1221_adc_settings_registers(
                0x442E,
                0x0000,
                sample_update=label,
                zero_time="1 ms",
                press_return_bit_time="off",
                release_return_bit_time="off",
                zps_enabled=False,
                zps_detect=False,
                sleep_sensitivity=False,
            )
            decoded = decode_awp1221_adc_settings_registers(encoded_05, 0x0000)
            self.assertEqual(decoded["sample_update"], label)


class Awp1221AdcLoadBitTests(unittest.TestCase):
    def test_label_uses_bit2(self) -> None:
        self.assertEqual(awp1221_adc_load_label(0x0000), "40 uA")
        self.assertEqual(awp1221_adc_load_label(1 << 2), "160 uA")
        self.assertEqual(awp1221_adc_load_label(1 << 11), "40 uA")

    def test_merge_sets_only_bit2_and_preserves_bit3(self) -> None:
        current = 0x0008
        self.assertEqual(awp1221_merge_adc_load(current, "160 uA"), 0x000C)
        self.assertEqual(awp1221_merge_adc_load(0x000C, "40 uA"), 0x0008)

    def test_merge_sample_time_only_bits_11_10(self) -> None:
        current = 0x442E
        updated = awp1221_merge_sample_time(current, "16 us")
        self.assertEqual((updated >> 10) & 3, 2)
        self.assertEqual(updated & ~0x0C00, current & ~0x0C00)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m unittest tests.test_awp1221_realtime_settings -v`

Expected: FAIL on options still `6 us` / missing helpers.

- [ ] **Step 3: Write minimal implementation**

In `pages/awp1221_page.py` change:

```python
AWP1221_ADC_SAMPLE_UPDATE_OPTIONS = ("4 us", "8 us", "16 us", "32 us")
```

Replace every preset `"sample_update": "6 us"` with `"sample_update": "4 us"`.

In `pages/awp1221_realtime_sram.py` after `AWP1221_ADC_LOAD_REGISTER`:

```python
AWP1221_ADC_LOAD_BIT = 1 << 2
AWP1221_ADC_SAMPLE_TIME_OPTIONS = ("4 us", "8 us", "16 us", "32 us")
AWP1221_ADC_SAMPLE_TIME_MASK = 0x0C00


def awp1221_adc_load_label(register_1f: int) -> str:
    return "160 uA" if int(register_1f) & AWP1221_ADC_LOAD_BIT else "40 uA"


def awp1221_merge_adc_load(current: int, load: str) -> int:
    value = int(current) & 0xFFFF
    if str(load) == "160 uA":
        return value | AWP1221_ADC_LOAD_BIT
    return value & ~AWP1221_ADC_LOAD_BIT


def awp1221_merge_sample_time(current: int, sample_time: str) -> int:
    code = AWP1221_ADC_SAMPLE_TIME_OPTIONS.index(str(sample_time))
    value = int(current) & 0xFFFF
    return (value & ~AWP1221_ADC_SAMPLE_TIME_MASK) | (code << 10)
```

Use `AWP1221_ADC_SAMPLE_TIME_OPTIONS` in `ADCVoltageWindow` instead of the inline `("4 us", "8 us", "16 us", "32 us")` tuple.

Export the new helpers from `__all__`.

- [ ] **Step 4: Run tests**

Run: `py -3 -m unittest tests.test_awp1221_realtime_settings -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pages/awp1221_page.py pages/awp1221_realtime_sram.py tests/test_awp1221_realtime_settings.py
git commit -m "Unify AWP1221 ADC sample-time labels and 0x1F load bit2 helpers."
```

---

### Task 2: Quiet 0x13 realtime polls

**Files:**
- Modify: `pages/awp1221_page.py` (`_request_adc_value_read`, `_poll_adc_realtime` path)
- Test: `tests/test_awp1221_realtime_settings.py`

**Interfaces:**
- Consumes: `SerialManager.suppress_logging()` (already exists; captures TX/MCU, still publishes `MCU_TEXT`)
- Produces: `_request_adc_value_read(..., quiet: bool = False)` wrapping the worker read in `suppress_logging()` when `quiet=True`. Mixin `_poll_adc_realtime` passes `quiet=True`.

- [ ] **Step 1: Write the failing test**

Append:

```python
from contextlib import contextmanager
from types import SimpleNamespace
from pages.awp1221_page import AWP1221ModePage, AWP1221_ADC_VALUE_REGISTER
from pages.awp1221_realtime_sram import AWP1221_ADC_LIVE_REGISTER


class Awp1221QuietRealtimeReadTests(unittest.TestCase):
    def test_live_poll_uses_quiet_flag(self) -> None:
        captured: dict[str, bool] = {}

        def request(**kwargs):
            captured.update(kwargs)
            return True

        page = object.__new__(AWP1221ModePage)
        page._adc_realtime_active = True
        page.ctx = SimpleNamespace(serial=SimpleNamespace(is_connected=True))
        page._stop_adc_realtime = lambda: None
        page._schedule_next_adc_poll = lambda: None
        page._request_adc_value_read = request
        page._adc_realtime_job = "job"
        AWP1221ModePage._poll_adc_realtime(page)
        self.assertTrue(captured.get("quiet"))
        self.assertEqual(captured.get("register"), AWP1221_ADC_LIVE_REGISTER)

    def test_quiet_read_wraps_suppress_logging(self) -> None:
        events: list[str] = []

        @contextmanager
        def suppress_logging():
            events.append("suppress")
            yield

        page = object.__new__(AWP1221ModePage)
        page._adc_read_in_flight = False
        page._adc_lifecycle_generation = 0
        page.device_addr = lambda: 0x50
        page.ctx = SimpleNamespace(serial=SimpleNamespace(suppress_logging=suppress_logging))
        page._read_register_value = lambda register, address: events.append(f"read:{register}") or 1
        tasks: list = []

        def run_task(widget, task, *, on_done, on_error):
            del widget, on_error
            tasks.append(task)
            on_done(task())

        import pages.awp1221_page as page_mod
        original = page_mod._run_ui_background_task
        page_mod._run_ui_background_task = run_task
        try:
            AWP1221ModePage._request_adc_value_read(
                page,
                show_error=False,
                register=AWP1221_ADC_LIVE_REGISTER,
                quiet=True,
                on_complete=None,
            )
        finally:
            page_mod._run_ui_background_task = original
        self.assertEqual(events, ["suppress", f"read:{AWP1221_ADC_LIVE_REGISTER}"])
```

If `_poll_adc_realtime` lives on the mixin, call `AWP1221RealtimeMixin._poll_adc_realtime(page)` instead and import that class.

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m unittest tests.test_awp1221_realtime_settings -v`

Expected: FAIL, `quiet` not accepted / not passed.

- [ ] **Step 3: Implement quiet poll**

In mixin `_poll_adc_realtime`, pass `quiet=True`:

```python
        if not self._request_adc_value_read(
            show_error=False,
            on_complete=self._schedule_next_adc_poll,
            register=AWP1221_ADC_LIVE_REGISTER,
            quiet=True,
        ):
            self._schedule_next_adc_poll()
```

In `AWP1221ModePage._request_adc_value_read` add `quiet: bool = False`. Inside the worker:

```python
        def read_value() -> int:
            if quiet:
                with self.ctx.serial.suppress_logging():
                    return self._read_register_value(register, address)
            return self._read_register_value(register, address)
```

Use `read_value` as the background task instead of the current lambda. Manual `read_adc_value` stays `quiet=False`.

Do not change `send_command` defaults.

- [ ] **Step 4: Run tests**

Run: `py -3 -m unittest tests.test_awp1221_realtime_settings tests.test_awp1221_realtime_overlay -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pages/awp1221_page.py pages/awp1221_realtime_sram.py tests/test_awp1221_realtime_settings.py
git commit -m "Silence AWP1221 realtime 0x13 poll serial logs."
```

---

### Task 3: Window writes, load bit2 display, capacitance label

**Files:**
- Modify: `pages/awp1221_realtime_sram.py` (`ADCVoltageWindow`, mixin load/apply)
- Modify: `pages/awp1221_page.py` (optional `_apply_realtime_window_settings` if the mixin calls host read/write)
- Test: `tests/test_awp1221_realtime_settings.py`

**Interfaces:**
- Consumes: `awp1221_merge_sample_time`, `awp1221_merge_adc_load`, `awp1221_adc_load_label`, host `_read_register_value` / `_write_register_value` / `adc_sample_update_var`
- Produces:
  - Window combobox `<<ComboboxSelected>>` for sample time and load calls `on_settings_change`
  - Mixin `_apply_adc_voltage_settings()` RMW 0x05 bits 11-10 and 0x1F bit2 on a background task
  - Load display uses bit2
  - Label `陶瓷电容等效容值 (uF)`

- [ ] **Step 1: Write failing tests**

Append:

```python
from pages.awp1221_realtime_sram import ADCVoltageWindow, AWP1221RealtimeMixin


class Awp1221WindowSettingsWriteTests(unittest.TestCase):
    def test_apply_settings_writes_sample_time_and_load_bit2(self) -> None:
        writes: list[tuple[int, int]] = []
        page = object.__new__(AWP1221RealtimeMixin)
        page.device_addr = lambda: 0x50
        page._read_register_value = lambda register, address: {
            0x05: 0x442E,
            0x1F: 0x0008,
        }[register]
        page._write_register_value = lambda register, value, address: writes.append((register, value))
        page._adc_voltage_window = SimpleNamespace(
            sample_time=SimpleNamespace(get=lambda: "16 us"),
            load=SimpleNamespace(get=lambda: "160 uA"),
            winfo_exists=lambda: True,
        )
        page.adc_sample_update_var = SimpleNamespace(set=lambda value: setattr(page, "synced", value))
        page._apply_adc_voltage_settings()
        self.assertEqual(page.synced, "16 us")
        self.assertEqual(
            writes,
            [
                (0x05, awp1221_merge_sample_time(0x442E, "16 us")),
                (0x1F, awp1221_merge_adc_load(0x0008, "160 uA")),
            ],
        )
        self.assertEqual(writes[1][1] & 0x0008, 0x0008)

    def test_load_settings_uses_bit2(self) -> None:
        self.assertEqual(awp1221_adc_load_label(0x0008), "40 uA")
        self.assertEqual(awp1221_adc_load_label(0x000C), "160 uA")
```

If `_apply_adc_voltage_settings` must run off the UI thread in production, the test may call the inner RMW function extracted as `_write_adc_voltage_settings(address: int) -> None` synchronously. Prefer that:

```python
    def _write_adc_voltage_settings(self, address: int) -> None:
        window = self._adc_voltage_window
        sample = window.sample_time.get()
        load = window.load.get()
        current_05 = self._read_register_value(AWP1221_ADC_SETTINGS_REGISTER, address)
        current_1f = self._read_register_value(AWP1221_ADC_LOAD_REGISTER, address)
        self._write_register_value(
            AWP1221_ADC_SETTINGS_REGISTER,
            awp1221_merge_sample_time(current_05, sample),
            address,
        )
        self._write_register_value(
            AWP1221_ADC_LOAD_REGISTER,
            awp1221_merge_adc_load(current_1f, load),
            address,
        )
        setter = getattr(self, "adc_sample_update_var", None)
        if setter is not None:
            setter.set(sample)
```

Then `_apply_adc_voltage_settings` runs that on a background task and reports errors via `_show_adc_voltage_error`. Test the synchronous `_write_adc_voltage_settings`.

- [ ] **Step 2: Run tests to fail**

Run: `py -3 -m unittest tests.test_awp1221_realtime_settings -v`

Expected: FAIL, missing `_write_adc_voltage_settings`.

- [ ] **Step 3: Implement window wiring**

1. `ADCVoltageWindow.__init__`: add `on_settings_change: Callable[[], None] | None = None`. Store it. Bind sample-time and load comboboxes:

```python
            combo.bind("<<ComboboxSelected>>", lambda _event: self._on_conversion_or_settings())
...
    def _on_conversion_or_settings(self) -> None:
        self.redraw()
        callback = getattr(self, "on_settings_change", None)
        if callback is not None:
            callback()
```

Capacitance and time-scale traces stay redraw-only.

Change the C label text to `陶瓷电容等效容值 (uF)`.

2. Mixin `toggle_adc_realtime` constructs:

```python
        self._adc_voltage_window = ADCVoltageWindow(
            self,
            self._stop_adc_realtime,
            on_settings_change=self._apply_adc_voltage_settings,
        )
```

3. Replace load display bit11 with `awp1221_adc_load_label(values[1])`.

4. Add `_write_adc_voltage_settings` / `_apply_adc_voltage_settings` as specified. `_apply` no-ops if the window is gone; on error call `_show_adc_voltage_error` and keep polling.

Do not write capacitance. Do not clear 0x1F bit3.

- [ ] **Step 4: Run tests**

Run: `py -3 -m unittest tests.test_awp1221_realtime_settings tests.test_awp1221_realtime_overlay -v`

Expected: PASS.

Also: `py -3 -m py_compile pages/awp1221_page.py pages/awp1221_realtime_sram.py core/serial_manager.py`

- [ ] **Step 5: Commit**

```bash
git add pages/awp1221_realtime_sram.py pages/awp1221_page.py tests/test_awp1221_realtime_settings.py
git commit -m "Write AWP1221 realtime sample-time and load; rename capacitance."
```

---

## Spec coverage

- Quiet 0x13 poll TX/MCU: Task 2 (`suppress_logging`)
- Manual read / writes / MCU_TEXT still log: Task 2 does not wrap those
- 4/8/16/32 us on page and window: Task 1
- 0x1F bit2 load, bit3 preserved: Task 1 + Task 3
- Window writes RMW 0x05[11:10] and 0x1F bit2, sync page combobox: Task 3
- Capacitance label, no chip write: Task 3
- Presets `6 us` → `4 us`: Task 1
