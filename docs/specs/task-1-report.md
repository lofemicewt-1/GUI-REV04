# Task 1 Report: Core SRAM library

## What you implemented

Tk-free AWP1221 SRAM helpers in `core/awp1221_sram.py`, copied from the task brief (do not simplify `write_waveform`):

- Constants: `DATA_START=0x100`, `DATA_END=0x1FF`, `SHAPE_PEAK_CODE=3600`
- `encode_val2`, `generate_smooth_points`, `compress_waveform`, `decompress_waveform`
- `SramWaveform`, `generate_waveform` (retry point count until VAL2 fits)
- `build_descriptor`, `allocate_words`
- `write_waveform` with access-mode enter, SRAM write, descriptor write, play-mode restore, and restore-on-failure

Tests created exactly as specified in `tests/test_awp1221_sram.py`.

No commit. No other files changed for this task.

## What you tested and test results

Command: `py -3 -m unittest tests.test_awp1221_sram -v`

After implementation: **11 passed, 1 error**.

| Test | Result |
|---|---|
| `AllocationTests.test_empty_occupied_starts_at_data_start` | ok |
| `AllocationTests.test_raises_when_sram_full` | ok |
| `AllocationTests.test_skips_occupied_range` | ok |
| `DescriptorTests.test_descriptor_encodes_range_vpk_offset` | ok |
| `DescriptorTests.test_descriptor_rejects_range_outside_sram_data` | ok |
| `EncodeVal2Tests.test_negative_uses_sign_bit` | ok |
| `EncodeVal2Tests.test_non_negative_passthrough` | ok |
| `EncodeVal2Tests.test_out_of_range_raises` | ok |
| `WaveformRoundTripTests.test_compress_decompress_matches_centered_shape` | ERROR |
| `WaveformRoundTripTests.test_generate_waveform_round_trips` | ok |
| `WaveformRoundTripTests.test_invalid_point_count_rejected` | ok |
| `WriteWaveformTests.test_write_enters_access_mode_then_restores_play_mode` | ok |

Failing test:

```
ValueError: VAL2 out of range: 208
  File "...\tests\test_awp1221_sram.py", line 38, in test_compress_decompress_matches_centered_shape
    words, _differences = compress_waveform(points)
```

Diagnostic (not a test change):

- `generate_smooth_points([0.0, 100.0, -36.0, 0.0], 18, centered=True)` → 18 points
- max `|second difference|` = 285 (VAL2 allows only -3..3)
- `generate_waveform` on the same controls succeeds by raising count to **202 points / 52 words**

The brief expected all tests PASS. The exact uploaded module cannot compress that 18-point shape; `generate_waveform` exists specifically to bump `N` until VAL2 fits. The failing test calls `compress_waveform` directly at 18 points, bypassing that retry.

Implementation and test file were **not** altered to force a pass.

## TDD Evidence

### RED

Command: `py -3 -m unittest tests.test_awp1221_sram -v`

```
test_awp1221_sram (unittest.loader._FailedTest.test_awp1221_sram) ... ERROR

======================================================================
ERROR: test_awp1221_sram (unittest.loader._FailedTest.test_awp1221_sram)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_awp1221_sram
Traceback (most recent call last):
  File "C:\Users\wasd1\AppData\Local\Programs\Python\Python311\Lib\unittest\loader.py", line 154, in loadTestsFromName
    module = __import__(module_name)
             ^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\wasd1\Desktop\GUI REV04\tests\test_awp1221_sram.py", line 5, in <module>
    from core.awp1221_sram import (
ModuleNotFoundError: No module named 'core.awp1221_sram'

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (errors=1)
```

Matches brief expected RED.

### GREEN

Command: `py -3 -m unittest tests.test_awp1221_sram -v`

```
test_empty_occupied_starts_at_data_start ... ok
test_raises_when_sram_full ... ok
test_skips_occupied_range ... ok
test_descriptor_encodes_range_vpk_offset ... ok
test_descriptor_rejects_range_outside_sram_data ... ok
test_negative_uses_sign_bit ... ok
test_non_negative_passthrough ... ok
test_out_of_range_raises ... ok
test_compress_decompress_matches_centered_shape ... ERROR
test_generate_waveform_round_trips ... ok
test_invalid_point_count_rejected ... ok
test_write_enters_access_mode_then_restores_play_mode ... ok

ERROR: ... ValueError: VAL2 out of range: 208

Ran 12 tests in 0.008s
FAILED (errors=1)
```

Does **not** match brief expected GREEN (all tests). See Issues.

## Files changed

Created (this task only):

- `core/awp1221_sram.py`
- `tests/test_awp1221_sram.py`

Not committed. Pre-existing dirty files in the checkout were left untouched.

## Self-review findings

Spec match:

- Public interfaces from the brief are present with the specified signatures and constants.
- `write_waveform` restore-on-failure path is intact (not simplified).
- Module is Tk-free.

Standards:

- Matches neighboring `core/*.py` style (annotations, unittest, no extra comments).
- `core/__init__.py` left empty (existing pattern).

Gaps vs later needs (not in Task 1 tests):

- No unit coverage of restore-on-failure, `previous_range` stale-clear, `progress`, overlap errors, or `start_address=None` allocation inside `write_waveform`.
- `SHAPE_PEAK_CODE` is produced by the module but not asserted in tests.

No code-review subagent (no Task tool in this harness); this is a local spec/standards pass.

## Issues or concerns

1. **Spec vs algorithm mismatch (blocking for “all tests pass”).** `test_compress_decompress_matches_centered_shape` cannot pass against the uploaded `compress_waveform`/`encode_val2` at 18 points. Need a decision:
   - Change the test to a compressible fixture (gentler shape and/or more points, or assert via `generate_waveform` then compress/decompress), **or**
   - Keep the test and accept 11/12, **or**
   - Change compression (forbidden by brief: use uploaded module as-is).
2. Did not guess a test or algorithm fix.
3. Commit skipped as instructed.

## Review fix (post-review)

Changed only `tests/test_awp1221_sram.py` `test_compress_decompress_matches_centered_shape` fixture to a centered shape that compresses at 18 points:

```python
points = generate_smooth_points([0.0, 2.0, 0.0], 18, centered=True)
```

`core/awp1221_sram.py` unchanged. No extra tests. No compressor rewrite. No commit.

Command: `py -3 -m unittest tests.test_awp1221_sram -v`

```
test_empty_occupied_starts_at_data_start (tests.test_awp1221_sram.AllocationTests.test_empty_occupied_starts_at_data_start) ... ok
test_raises_when_sram_full (tests.test_awp1221_sram.AllocationTests.test_raises_when_sram_full) ... ok
test_skips_occupied_range (tests.test_awp1221_sram.AllocationTests.test_skips_occupied_range) ... ok
test_descriptor_encodes_range_vpk_offset (tests.test_awp1221_sram.DescriptorTests.test_descriptor_encodes_range_vpk_offset) ... ok
test_descriptor_rejects_range_outside_sram_data (tests.test_awp1221_sram.DescriptorTests.test_descriptor_rejects_range_outside_sram_data) ... ok
test_negative_uses_sign_bit (tests.test_awp1221_sram.EncodeVal2Tests.test_negative_uses_sign_bit) ... ok
test_non_negative_passthrough (tests.test_awp1221_sram.EncodeVal2Tests.test_non_negative_passthrough) ... ok
test_out_of_range_raises (tests.test_awp1221_sram.EncodeVal2Tests.test_out_of_range_raises) ... ok
test_compress_decompress_matches_centered_shape (tests.test_awp1221_sram.WaveformRoundTripTests.test_compress_decompress_matches_centered_shape) ... ok
test_generate_waveform_round_trips (tests.test_awp1221_sram.WaveformRoundTripTests.test_generate_waveform_round_trips) ... ok
test_invalid_point_count_rejected (tests.test_awp1221_sram.WaveformRoundTripTests.test_invalid_point_count_rejected) ... ok
test_write_enters_access_mode_then_restores_play_mode (tests.test_awp1221_sram.WriteWaveformTests.test_write_enters_access_mode_then_restores_play_mode) ... ok

----------------------------------------------------------------------
Ran 12 tests in 0.003s

OK
```

**12/12 passing.**
