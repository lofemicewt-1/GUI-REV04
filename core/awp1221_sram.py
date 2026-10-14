"""AWP1221 second-difference SRAM format supplied with the device example."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

DATA_START = 0x100
DATA_END = 0x1FF
SHAPE_PEAK_CODE = 3600


class ZeroIntervalCandidate(Exception):
    """This point count cannot give every requested segment at least one interval."""


class SegmentRatioCapacityError(ValueError):
    english = (
        "Current amplitudes and segment ratios cannot be encoded within SRAM capacity. "
        "Increase short-segment ratios or reduce amplitude changes."
    )

    def __init__(self) -> None:
        super().__init__("当前幅值与分段比例在 SRAM 容量内无法编码，请增大过短段的比例或减小幅值变化。")


def allocate_segment_intervals(total_points: int, segment_weights: Sequence[int]) -> tuple[int, ...]:
    if total_points < 2:
        raise ValueError("Point count must be at least 2")
    if isinstance(segment_weights, (str, bytes)) or not isinstance(segment_weights, Sequence) or not segment_weights:
        raise ValueError("Segment weights must be positive integers")
    weights: list[int] = []
    for weight in segment_weights:
        if type(weight) is not int or weight <= 0:
            raise ValueError("Segment weights must be positive integers")
        weights.append(weight)
    total_weight = sum(weights)
    intervals_total = total_points - 1
    quotients: list[int] = []
    remainders: list[int] = []
    for weight in weights:
        quotient, remainder = divmod(intervals_total * weight, total_weight)
        quotients.append(quotient)
        remainders.append(remainder)
    remaining = intervals_total - sum(quotients)
    intervals = quotients[:]
    for index in sorted(range(len(weights)), key=lambda item: (-remainders[item], item))[:remaining]:
        intervals[index] += 1
    if any(interval <= 0 for interval in intervals):
        raise ZeroIntervalCandidate("Segment ratio leaves a zero interval at this point count")
    if sum(intervals) != intervals_total:
        raise RuntimeError("interval allocation did not sum to N-1")
    return tuple(intervals)


def _normalize_segment_weights(control_count: int, segment_weights: Sequence[int] | None) -> tuple[int, ...]:
    expected = control_count - 1
    if expected < 1:
        raise ValueError("At least two finite shape values are required")
    if segment_weights is None:
        return tuple(1 for _ in range(expected))
    if isinstance(segment_weights, (str, bytes)) or not isinstance(segment_weights, Sequence):
        raise ValueError("Segment weights must be positive integers")
    if len(segment_weights) != expected:
        raise ValueError(f"Segment weight count must equal control count minus one ({expected})")
    normalized: list[int] = []
    for weight in segment_weights:
        if type(weight) is not int or weight <= 0:
            raise ValueError("Segment weights must be positive integers")
        normalized.append(weight)
    return tuple(normalized)


def encode_val2(value: int) -> int:
    if not -3 <= value <= 3:
        raise ValueError(f"VAL2 out of range: {value}")
    return value if value >= 0 else 4 | -value


def generate_smooth_points(control_values: Sequence[float], total_points: int = 522,
                           peak_code: int = SHAPE_PEAK_CODE,
                           *, centered: bool = False,
                           segment_weights: Sequence[int] | None = None) -> list[int]:
    if len(control_values) < 2 or any(not math.isfinite(v) for v in control_values):
        raise ValueError("At least two finite shape values are required")
    weights = _normalize_segment_weights(len(control_values), segment_weights)
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
    intervals = allocate_segment_intervals(total_points, weights)
    points = []
    for (start, end), segment_intervals in zip(zip(controls, controls[1:]), intervals):
        points.extend(round(start + (end - start) * (1 - math.cos(math.pi * j / segment_intervals)) / 2)
                      for j in range(segment_intervals))
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
    segment_intervals: tuple[int, ...] = ()


def generate_waveform(control_values: Sequence[float], *, max_words: int = 256,
                      total_points: int = 522,
                      centered: bool = False,
                      segment_weights: Sequence[int] | None = None) -> SramWaveform:
    if total_points < 2 or (total_points - 2) % 4:
        raise ValueError("Point count must be 2, 6, 10, ... ((N - 2) % 4 == 0)")
    if len(control_values) >= 2:
        _normalize_segment_weights(len(control_values), segment_weights)
    max_points = 2 + 4 * (max_words - 2)
    for count in range(total_points, max_points + 1, 4):
        try:
            points = generate_smooth_points(
                control_values, count, centered=centered, segment_weights=segment_weights)
        except ZeroIntervalCandidate:
            continue
        try:
            words, differences = compress_waveform(points)
        except ValueError:
            continue
        if decompress_waveform(words) != points:
            raise ValueError("SRAM round-trip verification failed")
        weights = _normalize_segment_weights(len(control_values), segment_weights)
        return SramWaveform(
            tuple(points), tuple(words), tuple(differences), allocate_segment_intervals(count, weights))
    if segment_weights is not None:
        raise SegmentRatioCapacityError()
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
