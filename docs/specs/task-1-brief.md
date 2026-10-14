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

