# AWP1221 SRAM Editor Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the uploaded AWP1221 SRAM editor and write path, show it in Full Mode's Waveform Generator tab, and swap Button Mode's right-hand selector for that editor when Source is RAM.

**Architecture:** `core/awp1221_sram.py` owns compression, allocation, and hardware writes. `pages/awp1221_sram_editor.py` owns the Tk editor. `pages/awp1221_realtime_sram.py` re-exports the editor. `AWP1221ModePage` hosts one editor per mode instance. Button Mode hides the ROM selector shell when Source is RAM (SINC still wins).

**Tech Stack:** Python 3.11, tkinter, unittest (`py -3 -m unittest`)

## Global Constraints

- Do not rewrite SRAM compression, allocation, or `write_waveform` sequencing; use the uploaded modules.
- GUI callers import `AWP1221SramEditor` only from `pages/awp1221_realtime_sram.py`.
- Button Mode and Full Mode are separate `AWP1221ModePage` instances; each owns its own editor.
- Source combo stays visible in Button Mode so the user can leave RAM.
- SINC selected hides the SRAM editor even if Source is RAM.
- Do not write SRAM on the Tk main thread in a blocking loop.
- Occupancy is session-only: key `(port, device_addr)`, range `0x100–0x1FF`.
- Test command: `py -3 -m unittest tests.test_awp1221_sram tests.test_awp1221_sram_ui -v`

## File map

| File | Responsibility |
|---|---|
| Create: `core/awp1221_sram.py` | Smooth points, VAL2 encode, allocate, descriptor, write+restore |
| Create: `pages/awp1221_sram_editor.py` | Drag handles, progress queue, background write |
| Modify: `pages/awp1221_realtime_sram.py` | Direct import + re-export; drop `ImportError` fallback |
| Modify: `pages/awp1221_page.py` | Full Mode always grid editor; Button Mode RAM swap |
| Create: `tests/test_awp1221_sram.py` | Core unit tests |
| Create: `tests/test_awp1221_sram_ui.py` | Editor + Button/Full Mode visibility |

---

### Task 1: Core SRAM library

**Files:**
- Create: `core/awp1221_sram.py`
- Test: `tests/test_awp1221_sram.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `DATA_START: int = 0x100`
  - `DATA_END: int = 0x1FF`
  - `SHAPE_PEAK_CODE: int = 3600`
  - `encode_val2(value: int) -> int`
  - `generate_smooth_points(control_values, total_points=522, peak_code=3600, *, centered=False) -> list[int]`
  - `compress_waveform(points) -> tuple[list[int], list[int]]`
  - `decompress_waveform(words) -> list[int]`
  - `generate_waveform(control_values, *, max_words=256, total_points=522, centered=False) -> SramWaveform`
  - `SramWaveform(points: tuple[int, ...], words: tuple[int, ...], differences: tuple[int, ...])`
  - `build_descriptor(start: int, word_count: int, vpk: int, offset: int) -> tuple[int, int, int, int]`
  - `allocate_words(word_count: int, occupied: Sequence[tuple[int, int]]) -> int`
  - `write_waveform(read, write, wave_number, waveform, sample_time, vpk, offset, progress=None, occupied=(), start_address=None, previous_range=None) -> tuple[int, tuple[int, ...]]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_awp1221_sram.py`:

```python
from __future__ import annotations

import unittest

from core.awp1221_sram import (
    DATA_END,
    DATA_START,
    allocate_words,
    build_descriptor,
    compress_waveform,
    decompress_waveform,
    encode_val2,
    generate_smooth_points,
    generate_waveform,
    write_waveform,
)


class EncodeVal2Tests(unittest.TestCase):
    def test_non_negative_passthrough(self) -> None:
        self.assertEqual(encode_val2(0), 0)
        self.assertEqual(encode_val2(3), 3)

    def test_negative_uses_sign_bit(self) -> None:
        self.assertEqual(encode_val2(-1), 5)
        self.assertEqual(encode_val2(-3), 7)

    def test_out_of_range_raises(self) -> None:
        with self.assertRaises(ValueError):
            encode_val2(4)
        with self.assertRaises(ValueError):
            encode_val2(-4)


class WaveformRoundTripTests(unittest.TestCase):
    def test_compress_decompress_matches_centered_shape(self) -> None:
        points = generate_smooth_points([0.0, 100.0, -36.0, 0.0], 18, centered=True)
        words, _differences = compress_waveform(points)
        self.assertEqual(decompress_waveform(words), points)
        self.assertEqual(len(words), 2 + (len(points) - 2) // 4)

    def test_generate_waveform_round_trips(self) -> None:
        result = generate_waveform([0.0, 100.0, 0.0], total_points=18, centered=True)
        self.assertEqual(decompress_waveform(result.words), list(result.points))
        self.assertGreaterEqual(len(result.words), 2)

    def test_invalid_point_count_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_waveform([0.0, 100.0], total_points=5, centered=True)


class AllocationTests(unittest.TestCase):
    def test_empty_occupied_starts_at_data_start(self) -> None:
        self.assertEqual(allocate_words(4, ()), DATA_START)

    def test_skips_occupied_range(self) -> None:
        start = allocate_words(4, ((DATA_START, DATA_START + 7),))
        self.assertEqual(start, DATA_START + 8)
        self.assertLessEqual(start + 3, DATA_END)

    def test_raises_when_sram_full(self) -> None:
        occupied = ((DATA_START, DATA_END),)
        with self.assertRaises(ValueError):
            allocate_words(4, occupied)


class DescriptorTests(unittest.TestCase):
    def test_descriptor_encodes_range_vpk_offset(self) -> None:
        words = build_descriptor(0x100, 8, vpk=9, offset=2)
        self.assertEqual(words[0], 0x400 | 0x100)
        self.assertEqual(words[1], 0x100 + 8 - 1)
        self.assertEqual(words[2], 0)
        self.assertEqual(words[3], (2 << 5) | 9)

    def test_descriptor_rejects_range_outside_sram_data(self) -> None:
        with self.assertRaises(ValueError):
            build_descriptor(0x0F0, 4, 0, 0)


class WriteWaveformTests(unittest.TestCase):
    def test_write_enters_access_mode_then_restores_play_mode(self) -> None:
        regs = {5: 0x442E, 0x14: 0x0000, 0x15: 0x0000}
        writes: list[tuple[int, int]] = []

        def read(reg: int) -> int:
            return regs[reg]

        def write(reg: int, value: int) -> bool:
            writes.append((reg, value))
            regs[reg] = value
            return True

        waveform = generate_waveform([0.0, 50.0, 0.0], total_points=6, centered=True)
        start, descriptor = write_waveform(
            read,
            write,
            wave_number=3,
            waveform=waveform,
            sample_time=1,
            vpk=1,
            offset=0,
            occupied=(),
            start_address=0x100,
        )
        self.assertEqual(start, 0x100)
        self.assertEqual(writes[0], (5, 0x442F))
        self.assertEqual(writes[-1], (5, 2))
        self.assertEqual(descriptor[0] & 0x3FF, 0x100)
        self.assertIn((0x15, 3 * 4), [(reg, value & 0x3FF) for reg, value in writes])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m unittest tests.test_awp1221_sram -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'core.awp1221_sram'`

- [ ] **Step 3: Write minimal implementation**

Create `core/awp1221_sram.py` with this exact uploaded module (do not simplify `write_waveform` restore-on-failure):

```python
"""AWP1221 second-difference SRAM format supplied with the device example."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

DATA_START = 0x100
DATA_END = 0x1FF
SHAPE_PEAK_CODE = 3600


def encode_val2(value: int) -> int:
    if not -3 <= value <= 3:
        raise ValueError(f"VAL2 out of range: {value}")
    return value if value >= 0 else 4 | -value


def generate_smooth_points(control_values: Sequence[float], total_points: int = 522,
                           peak_code: int = SHAPE_PEAK_CODE,
                           *, centered: bool = False) -> list[int]:
    if len(control_values) < 2 or any(not math.isfinite(v) for v in control_values):
        raise ValueError("At least two finite shape values are required")
    if total_points < len(control_values) or (total_points - 2) % 4:
        raise ValueError("Point count must be >= control count and satisfy (N - 2) % 4 == 0")
    if not 0 <= peak_code <= 4095:
        raise ValueError("Internal peak must be in 0..4095")
    if centered:
        if any(not -100 <= value <= 100 for value in control_values):
            raise ValueError("Shape percentages must be within -100..+100")
        controls = [round(peak_code * (value + 100) / 200) for value in control_values]
    else:
        if any(value < 0 for value in control_values):
            raise ValueError("Shape values must be nonnegative")
        maximum = max(control_values)
        controls = [round(v / maximum * peak_code) if maximum else 0 for v in control_values]
    base, remainder = divmod(total_points - 1, len(controls) - 1)
    points = []
    for index, (start, end) in enumerate(zip(controls, controls[1:])):
        intervals = base + (index < remainder)
        points.extend(round(start + (end - start) * (1 - math.cos(math.pi * j / intervals)) / 2)
                      for j in range(intervals))
    return points + [controls[-1]]


def compress_waveform(points: Sequence[int]) -> tuple[list[int], list[int]]:
    if len(points) < 2 or (len(points) - 2) % 4:
        raise ValueError("Point count must satisfy (N - 2) % 4 == 0")
    if any(not isinstance(p, int) or not 0 <= p <= 4095 for p in points):
        raise ValueError("Shape points must be unsigned 12-bit integers")
    val1 = points[1] - points[0]
    if val1 < 0:
        raise ValueError("Negative VAL1 encoding is unspecified; increase point count")
    differences = [c - 2 * b + a for a, b, c in zip(points, points[1:], points[2:])]
    codes = [encode_val2(value) for value in differences]
    words = [val1, points[0]]
    words.extend(sum(codes[i + j] << (3 * j) for j in range(4)) for i in range(0, len(codes), 4))
    return words, differences


def decompress_waveform(words: Sequence[int]) -> list[int]:
    if len(words) < 2 or any(not 0 <= w <= 0xFFF for w in words):
        raise ValueError("Invalid SRAM words")
    slope = words[0]
    points = [words[1], words[1] + slope]
    for word in words[2:]:
        for shift in (0, 3, 6, 9):
            code = (word >> shift) & 7
            slope += -(code & 3) if code & 4 else code
            points.append(points[-1] + slope)
    return points


@dataclass(frozen=True)
class SramWaveform:
    points: tuple[int, ...]
    words: tuple[int, ...]
    differences: tuple[int, ...]


def generate_waveform(control_values: Sequence[float], *, max_words: int = 256,
                      total_points: int = 522,
                      centered: bool = False) -> SramWaveform:
    if total_points < 2 or (total_points - 2) % 4:
        raise ValueError("Point count must be 2, 6, 10, ... ((N - 2) % 4 == 0)")
    max_points = 2 + 4 * (max_words - 2)
    for count in range(total_points, max_points + 1, 4):
        points = generate_smooth_points(control_values, count, centered=centered)
        try:
            words, differences = compress_waveform(points)
        except ValueError:
            continue
        if decompress_waveform(words) != points:
            raise ValueError("SRAM round-trip verification failed")
        return SramWaveform(tuple(points), tuple(words), tuple(differences))
    raise ValueError("SRAM capacity exceeded: smooth the shape or use fewer control points")


def build_descriptor(start: int, word_count: int, vpk: int, offset: int) -> tuple[int, int, int, int]:
    end = start + word_count - 1
    if not DATA_START <= start <= end <= DATA_END or word_count < 2:
        raise ValueError("SRAM address range must stay within 0x100..0x1FF")
    if not 0 <= vpk <= 31 or not 0 <= offset <= 127:
        raise ValueError("Invalid VPK or OFFSET")
    return (0x400 | start, end, 0, (offset << 5) | vpk)


def allocate_words(word_count: int, occupied: Sequence[tuple[int, int]]) -> int:
    for start in range(DATA_START, DATA_END - word_count + 2):
        end = start + word_count - 1
        if all(end < lo or start > hi for lo, hi in occupied):
            return start
    raise ValueError("No contiguous SRAM space; existing waveforms are preserved")


def write_waveform(read: Callable[[int], int | None], write: Callable[[int, int], bool],
                   wave_number: int, waveform: SramWaveform, sample_time: int,
                   vpk: int, offset: int,
                   progress: Callable[[int], None] | None = None,
                   occupied: Sequence[tuple[int, int]] = (),
                   start_address: int | None = None,
                   previous_range: tuple[int, int] | None = None) -> tuple[int, tuple[int, ...]]:
    if not 0 <= wave_number <= 30 or not 1 <= sample_time <= 256:
        raise ValueError("Invalid WAVE NUMBER or SAMPLE_TIME")
    if start_address is None:
        start = allocate_words(len(waveform.words), occupied)
    else:
        start = start_address
        end = start + len(waveform.words) - 1
        if any(not (end < lo or start > hi) for lo, hi in occupied):
            raise ValueError("SRAM address overlaps another waveform written in this session")
    descriptor = build_descriptor(start, len(waveform.words), vpk, offset)
    end = descriptor[1]
    if previous_range is not None:
        previous_start, previous_end = previous_range
        if not DATA_START <= previous_start <= previous_end <= DATA_END:
            raise ValueError("Invalid previous SRAM address range")
        if any(not (previous_end < lo or previous_start > hi) for lo, hi in occupied):
            raise ValueError("Previous SRAM address overlaps another waveform")

    def checked_read(reg: int, context: str) -> int:
        try:
            value = read(reg)
        except Exception as exc:
            raise IOError(f"{context}: read register 0x{reg:02X}: {exc}") from exc
        if value is None:
            raise IOError(f"{context}: no valid data from register 0x{reg:02X}")
        return value

    old_mode = checked_read(5, "Save access mode")
    old_rate = checked_read(0x14, "Save DAC sample rate")
    old_pointer = checked_read(0x15, "Save SRAM pointer")
    completed = False
    try:
        def send(reg: int, value: int, context: str = "SRAM configuration") -> None:
            try:
                if not write(reg, value):
                    raise IOError("device did not acknowledge write")
            except Exception as exc:
                raise IOError(f"{context}: register 0x{reg:02X} <- 0x{value:04X}: {exc}") from exc
        send(5, 0x442F)
        send(0x14, (old_rate & 0xFF00) | (sample_time - 1))
        send(0x15, (old_pointer & ~0x3FF) | start)
        for index, word in enumerate(waveform.words):
            send(0, word, f"Wave data {index + 1}/{len(waveform.words)}, SRAM 0x{start + index:03X}")
            if progress:
                progress(int(85 * (index + 1) / len(waveform.words)))
        if previous_range is not None:
            stale = [address for address in range(previous_start, previous_end + 1)
                     if not start <= address <= end]
            for index, address in enumerate(stale):
                if index == 0 or address != stale[index - 1] + 1:
                    send(0x15, (old_pointer & ~0x3FF) | address)
                send(0, 0, f"Clear old WAVE {wave_number} data, SRAM 0x{address:03X}")
                if progress:
                    progress(85 + int(10 * (index + 1) / len(stale)))
        send(0x15, (old_pointer & ~0x3FF) | wave_number * 4)
        for index, word in enumerate(descriptor):
            send(0, word, f"WAVE {wave_number} descriptor {index}, SRAM 0x{wave_number * 4 + index:03X}")
        send(5, 2)
        completed = True
        if progress:
            progress(100)
    except Exception as failure:
        if not completed:
            restoration_errors = []
            for reg, value in ((0x14, old_rate), (0x15, old_pointer), (5, old_mode)):
                try:
                    if not write(reg, value):
                        raise IOError("device did not acknowledge write")
                except Exception as exc:
                    restoration_errors.append(f"0x{reg:02X}: {exc}")
            if restoration_errors:
                raise IOError(f"{failure}\nRegister restoration also failed: {'; '.join(restoration_errors)}") from failure
        raise
    return start, descriptor
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m unittest tests.test_awp1221_sram -v`

Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add core/awp1221_sram.py tests/test_awp1221_sram.py
git commit -m "feat: add AWP1221 SRAM encode, allocate, and write helpers"
```

Skip this commit step unless the user asked to commit.

---

### Task 2: SRAM editor widget and stable re-export

**Files:**
- Create: `pages/awp1221_sram_editor.py`
- Modify: `pages/awp1221_realtime_sram.py` (replace the `try/except ImportError` block at lines 17–20 with a direct import)
- Test: `tests/test_awp1221_sram_ui.py`

**Interfaces:**
- Consumes: `core.awp1221_sram.generate_waveform`, `generate_smooth_points`, `write_waveform`, `allocate_words`
- Produces:
  - `class AWP1221SramEditor(BasePage)` with `__init__(self, parent, ctx, owner)`
  - Host must provide `VPK_OPTIONS`, `OFFSET_OPTIONS`, `device_addr()`, `_ensure_connected()`, `_shared_state_for()`, `_invalidate_sinc_verified()`, `auto_serial_var`, `rom_slice_var`
  - `pages.awp1221_realtime_sram.AWP1221SramEditor` is the class, never `None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_awp1221_sram_ui.py`:

```python
from __future__ import annotations

import unittest
from types import SimpleNamespace

import tkinter as tk

from pages.awp1221_realtime_sram import AWP1221SramEditor


def _colors() -> dict[str, str]:
    return {
        "bg": "#ffffff",
        "panel": "#ffffff",
        "panel_alt": "#f3f4f6",
        "text": "#111827",
        "muted": "#64748B",
        "border": "#CBD5E1",
        "primary": "#2563EB",
        "canvas_bg": "#ffffff",
    }


class _Serial:
    is_connected = False
    log_history: list[str] = []

    def add_log_listener(self, _listener) -> None:
        return None

    def remove_log_listener(self, _listener) -> None:
        return None

    def add_log_clear_listener(self, _listener) -> None:
        return None

    def remove_log_clear_listener(self, _listener) -> None:
        return None

    def selected_port_for_page(self, *_args, **_kwargs):
        return None


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        serial=_Serial(),
        state=SimpleNamespace(language="en", theme="light", extra={}, selected_port=None),
        colors=_colors(),
    )


class _Owner:
    VPK_OPTIONS = ("0x00 - 0.00 V", "0x01 - 6.25 V")
    OFFSET_OPTIONS = ("0x00 - 0 V",)

    def __init__(self) -> None:
        self.auto_serial_var = tk.BooleanVar(value=True)
        self.rom_slice_var = tk.StringVar(value="slice0")

    def device_addr(self) -> int:
        return 0x50

    def _ensure_connected(self) -> bool:
        return False

    def _shared_state_for(self, _addr=None):
        return None

    def _invalidate_sinc_verified(self) -> None:
        return None


class Awp1221SramEditorImportTests(unittest.TestCase):
    def test_realtime_entry_exports_editor_class(self) -> None:
        self.assertIsNotNone(AWP1221SramEditor)
        self.assertTrue(callable(AWP1221SramEditor))


class Awp1221SramEditorWidgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_editor_constructs_and_enables_write_for_default_shape(self) -> None:
        owner = _Owner()
        editor = AWP1221SramEditor(self.root, _ctx(), owner)
        self.addCleanup(editor.destroy)
        editor.update_idletasks()
        self.assertIn("!disabled", editor._sram_write_btn.state())
        self.assertIsNotNone(editor._sram_result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m unittest tests.test_awp1221_sram_ui.Awp1221SramEditorImportTests -v`

Expected: FAIL because `AWP1221SramEditor` is `None` (current `ImportError` fallback).

- [ ] **Step 3: Add the editor and drop the fallback**

Create `pages/awp1221_sram_editor.py` as the uploaded editor (the September 20 `AWP1221SramEditor` that subclasses `BasePage`, uses `core.awp1221_sram`, drags 2–32 control points, minimum `-36%`, writes on a worker thread with a 40 ms poll). Do not change its write algorithm.

In `pages/awp1221_realtime_sram.py` replace:

```python
try:
    from pages.awp1221_sram_editor import AWP1221SramEditor
except ImportError:
    AWP1221SramEditor = None
```

with:

```python
from pages.awp1221_sram_editor import AWP1221SramEditor
```

Keep `__all__` including `"AWP1221SramEditor"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m unittest tests.test_awp1221_sram_ui.Awp1221SramEditorImportTests tests.test_awp1221_sram_ui.Awp1221SramEditorWidgetTests -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pages/awp1221_sram_editor.py pages/awp1221_realtime_sram.py tests/test_awp1221_sram_ui.py
git commit -m "feat: add AWP1221 SRAM editor and re-export it from realtime entry"
```

Skip unless the user asked to commit.

---

### Task 3: Full Mode waveform tab always hosts the editor

**Files:**
- Modify: `pages/awp1221_page.py` around `_build_ui` Full Mode notebook (the `if AWP1221SramEditor is not None` branch)
- Test: `tests/test_awp1221_sram_ui.py`

**Interfaces:**
- Consumes: `AWP1221SramEditor(parent, ctx, owner)` from Task 2
- Produces: `AWP1221ModePage.sram_editor` always set in `full_mode`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_awp1221_sram_ui.py`:

```python
from pages.awp1221_page import AWP1221ModePage


def _mode_page(root: tk.Misc, mode_key: str) -> AWP1221ModePage:
    return AWP1221ModePage(
        root,
        _ctx(),
        tr=lambda key, **_kwargs: key,
        device_addr=lambda: 0x50,
        mode_key=mode_key,
        host=None,
    )


class Awp1221FullModeSramTabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_full_mode_grids_sram_editor_in_waveform_tab(self) -> None:
        page = _mode_page(self.root, "full_mode")
        self.addCleanup(page.destroy)
        page.update_idletasks()
        self.assertIsInstance(page.sram_editor, AWP1221SramEditor)
        self.assertTrue(page.sram_editor.grid_info())
        self.assertFalse(hasattr(page, "_buttons") and "awp1221_waveform_reserved" in page._buttons)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m unittest tests.test_awp1221_sram_ui.Awp1221FullModeSramTabTests -v`

Expected: FAIL on the reserved-button assertion if the `else` branch still exists, or FAIL if editor is missing. After Task 2 the editor exists, so this should fail only while the `else` reserved branch remains (or pass if the `if` already succeeds). If it already passes because `AWP1221SramEditor is not None`, keep the `else` deletion anyway so a future import failure is loud.

- [ ] **Step 3: Always grid the editor**

In `pages/awp1221_page.py` `_build_ui` (full mode), replace:

```python
        if AWP1221SramEditor is not None:
            waveform_tab.columnconfigure(0, weight=1)
            waveform_tab.rowconfigure(0, weight=1)
            self.sram_editor = AWP1221SramEditor(waveform_tab, self.ctx, self)
            self.sram_editor.grid(row=0, column=0, sticky="nsew")
        else:
            waveform_btn = ttk.Button(waveform_tab, style="Secondary.TButton", state="disabled")
            waveform_btn.grid(row=0, column=0, sticky="w")
            self._buttons["awp1221_waveform_reserved"] = waveform_btn
        self._build_waveform_selector(rom_tab)
```

with:

```python
        waveform_tab.columnconfigure(0, weight=1)
        waveform_tab.rowconfigure(0, weight=1)
        self.sram_editor = AWP1221SramEditor(waveform_tab, self.ctx, self)
        self.sram_editor.grid(row=0, column=0, sticky="nsew")
        self._build_waveform_selector(rom_tab)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m unittest tests.test_awp1221_sram_ui.Awp1221FullModeSramTabTests tests.test_awp1221_sram -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pages/awp1221_page.py tests/test_awp1221_sram_ui.py
git commit -m "feat: host AWP1221 SRAM editor in Full Mode waveform tab"
```

Skip unless the user asked to commit.

---

### Task 4: Button Mode RAM swaps in the SRAM editor

**Files:**
- Modify: `pages/awp1221_page.py`
  - `_build_button_mode_ui`
  - `_build_waveform_selector` (wrap selector widgets in `self._waveform_selector_shell`)
  - `_refresh_waveform_control_visibility`
  - `refresh_language` (button_mode early return)
- Test: `tests/test_awp1221_sram_ui.py`

**Interfaces:**
- Consumes: `AWP1221SramEditor` from Task 2
- Produces:
  - `AWP1221ModePage._sram_source_selected() -> bool`
  - `AWP1221ModePage._sync_sram_editor_visibility() -> None`
  - `self._waveform_selector_shell: ttk.Frame` in button mode
  - `self.sram_editor` in button mode, hidden until RAM

Keep visible when RAM is shown: Auto Serial, Waveform type, Source (`awp1221_auto_serial`, `awp1221_rom_waveform`, `awp1221_rom_source`). Hide the rest of the ROM selector (preview, write button, ROM/SINC detail rows) and show the editor.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_awp1221_sram_ui.py`:

```python
class Awp1221ButtonModeSramSwapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.page = _mode_page(self.root, "button_mode")
        self.addCleanup(self.page.destroy)
        self.page.update_idletasks()

    def test_default_rom_hides_sram_editor(self) -> None:
        self.assertIsInstance(self.page.sram_editor, AWP1221SramEditor)
        self.assertEqual(self.page.sram_editor.grid_info(), {})
        self.assertTrue(self.page._waveform_selector_shell.grid_info())

    def test_ram_source_shows_editor_and_keeps_source_combo(self) -> None:
        self.page.rom_source_var.set("RAM")
        self.page.update_idletasks()
        self.assertTrue(self.page.sram_editor.grid_info())
        source_widgets = self.page._selector_widgets["awp1221_rom_source"]
        self.assertTrue(any(widget.winfo_ismapped() for widget in source_widgets))

    def test_sinc_hides_editor_even_if_source_is_ram(self) -> None:
        self.page.rom_source_var.set("RAM")
        self.page.rom_waveform_var.set("SINC")
        self.page.update_idletasks()
        self.assertEqual(self.page.sram_editor.grid_info(), {})

    def test_returning_to_rom_hides_editor(self) -> None:
        self.page.rom_source_var.set("RAM")
        self.page.rom_source_var.set("ROM")
        self.page.update_idletasks()
        self.assertEqual(self.page.sram_editor.grid_info(), {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m unittest tests.test_awp1221_sram_ui.Awp1221ButtonModeSramSwapTests -v`

Expected: FAIL (`AttributeError: sram_editor` or `_waveform_selector_shell`)

- [ ] **Step 3: Wire Button Mode visibility**

In `_build_waveform_selector`, wrap the existing title/form/preview/write widgets in:

```python
        self._waveform_selector_shell = ttk.Frame(parent, style="Panel.TFrame")
        self._waveform_selector_shell.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self._waveform_selector_shell.columnconfigure(0, weight=1)
        self._waveform_selector_shell.columnconfigure(1, weight=1)
        self._waveform_selector_shell.rowconfigure(1, weight=1)
```

Grid the current title, form column, preview, and write button onto `self._waveform_selector_shell` instead of `parent`. Leave `parent.rowconfigure(1, weight=1)` so the SRAM editor can share that row.

In `_build_button_mode_ui`, after `_build_waveform_selector(panel)`:

```python
        self.sram_editor = AWP1221SramEditor(panel, self.ctx, self)
        self.sram_editor.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.sram_editor.grid_remove()
```

Move the selector shell to `row=1` as well (log stays `row=2`). Resulting grid:

- row 1: selector shell **or** SRAM editor
- row 2: serial log

Add:

```python
    def _sram_source_selected(self) -> bool:
        if self.mode_key != "button_mode":
            return False
        if self._sinc_selected():
            return False
        return str(self.rom_source_var.get()) == "RAM"

    def _sync_sram_editor_visibility(self) -> None:
        editor = getattr(self, "sram_editor", None)
        shell = getattr(self, "_waveform_selector_shell", None)
        if editor is None or shell is None or self.mode_key != "button_mode":
            return
        if self._sram_source_selected():
            shell.grid_remove()
            editor.grid()
            editor.refresh_language()
            editor.refresh_theme()
            return
        editor.grid_remove()
        shell.grid()
```

Call `_sync_sram_editor_visibility()` at the end of `_refresh_waveform_control_visibility`.

When RAM is selected, `_refresh_waveform_control_visibility` must still `grid()` Auto Serial, Waveform, and Source rows on the shell **before** hiding the shell is wrong — those three stay mapped because they live in the shell. To keep Source visible while the editor shows, **do not hide the whole shell**. Instead:

1. Keep the shell gridded.
2. Hide preview (`rom_canvas` parent), write button, and ROM/SINC detail rows as today.
3. Force-visible keys when `_sram_source_selected()`: `"awp1221_auto_serial"`, `"awp1221_rom_waveform"`, `"awp1221_rom_source"`.
4. Grid the SRAM editor in `panel` row 1 **beside/under** those three rows.

Concrete layout that matches the spec without trapping the user on RAM:

In `_build_button_mode_ui`:

```python
        selector_holder = ttk.Frame(panel, style="Panel.TFrame")
        selector_holder.grid(row=1, column=0, columnspan=2, sticky="nsew")
        selector_holder.columnconfigure(0, weight=1)
        selector_holder.rowconfigure(0, weight=0)
        selector_holder.rowconfigure(1, weight=1)
        self._build_waveform_selector(selector_holder)
        self.sram_editor = AWP1221SramEditor(selector_holder, self.ctx, self)
        self.sram_editor.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.sram_editor.grid_remove()
```

`_sync_sram_editor_visibility`:

```python
        if self._sram_source_selected():
            for key in (
                "awp1221_rom_preview_captions",
                "awp1221_rom_write_pending",
            ):
                self._set_selector_row_visible(key, False)
            if hasattr(self, "rom_canvas"):
                self.rom_canvas.grid_remove()
            editor.grid()
        else:
            editor.grid_remove()
            if hasattr(self, "rom_canvas"):
                self.rom_canvas.grid()
```

Add `"awp1221_rom_write_pending"` hide when RAM; show it again for ROM (existing `write_button.configure(state="normal")` path).

Trace `rom_source_var` next to the other ROM traces in `_build_waveform_selector`:

```python
        self.rom_source_var.trace_add("write", lambda *_: self._refresh_waveform_controls())
```

In `refresh_language` button_mode branch, before `return`, add:

```python
            sram_editor = getattr(self, "sram_editor", None)
            if sram_editor is not None:
                sram_editor.refresh_language()
            self._sync_sram_editor_visibility()
```

- [ ] **Step 4: Run tests**

Run: `py -3 -m unittest tests.test_awp1221_sram tests.test_awp1221_sram_ui -v`

Expected: PASS

Also: `py -3 -m py_compile core/awp1221_sram.py pages/awp1221_sram_editor.py pages/awp1221_realtime_sram.py pages/awp1221_page.py`

Expected: no output, exit 0

- [ ] **Step 5: Commit**

```bash
git add pages/awp1221_page.py tests/test_awp1221_sram_ui.py
git commit -m "feat: show AWP1221 SRAM editor in Button Mode when source is RAM"
```

Skip unless the user asked to commit.

---

## Spec coverage

| Spec requirement | Task |
|---|---|
| Add `core/awp1221_sram.py` | Task 1 |
| Add `pages/awp1221_sram_editor.py` | Task 2 |
| Drop `ImportError` fallback / re-export | Task 2 |
| Full Mode notebook always grids editor | Task 3 |
| Button Mode RAM shows editor | Task 4 |
| Source combo remains so user can leave RAM | Task 4 |
| SINC hides editor even if RAM | Task 4 |
| Session occupancy / write on worker thread | Task 1 + uploaded editor in Task 2 |
| Core + UI tests | Task 1, 2, 3, 4 |
| No Type-4 presets / no shared editor instance | Out of scope, no task |

## Type consistency

- Editor class name is always `AWP1221SramEditor`.
- Visibility helper is `_sram_source_selected` / `_sync_sram_editor_visibility`.
- Attribute is `self.sram_editor` in both modes.
- Button Mode selector wrapper is `_waveform_selector_shell` or the selector_holder described in Task 4; implement the holder variant so Source stays visible.
