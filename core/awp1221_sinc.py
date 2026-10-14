from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


AWP1221_SINC_Q_VOLTS = 200.0 / 128
AWP1221_SINC_HALF_Q_VOLTS = AWP1221_SINC_Q_VOLTS / 2
AWP1221_SINC_MIN_OFFSET_STEPS = -63
AWP1221_SINC_MAX_OFFSET_STEPS = 64
AWP1221_SINC_MAX_K = 128
AWP1221_SINC_WRITABLE_SERIALS = frozenset({"serial1", "serial2", "serial3", "serial4"})
AWP1221_SINC_DEPTH_POINTS = {
    "512": 512,
    "256": 256,
}
AWP1221_SINC_DEPTH_CODES = {
    512: 0,
    256: 1,
}
AWP1221_SINC_SLICE_WINDOWS = {
    "serial0": (),
    "serial1": ((0x12, 0x14),),
    "serial2": ((0x15, 0x17),),
    "serial3": ((0x18, 0x1B),),
    "serial4": ((0x1C, 0x1F),),
}
AWP1221_SINC_HALF_CYCLE_CODES = (0b01, 0b10, 0b01)


@dataclass(frozen=True)
class Awp1221SincParams:
    peak_target: float
    trough_target: float
    times_us: tuple[int, ...]
    depth_points: int
    sin_repeat_count: int = 1


@dataclass(frozen=True)
class Awp1221SincSegment:
    coarse_code: int
    fine_code: int
    offset_code: int
    k: int
    offset_steps: int
    vpp_volts: float
    offset_volts: float
    start_volts: float
    end_volts: float
    half_cycle_code: int


@dataclass(frozen=True)
class Awp1221SincSolution:
    peak_volts: float
    trough_volts: float
    peak_error_volts: float
    trough_error_volts: float
    vpp_volts: float
    junction_error_ticks: int
    segments: tuple[Awp1221SincSegment, Awp1221SincSegment, Awp1221SincSegment]


@dataclass(frozen=True)
class Awp1221SincSolveResult:
    ok: bool
    reason: str
    solution: Awp1221SincSolution | None


@dataclass(frozen=True)
class Awp1221SincPlan:
    serial: str
    writable: bool
    reason: str
    slices: tuple[int, ...]
    sram_words: tuple[int, ...]
    params: Awp1221SincParams
    solution: Awp1221SincSolution | None
    descriptor_words: tuple[tuple[int, int, int], ...]
    offset_codes: tuple[int, ...]
    vpk_codes: tuple[int, ...]
    half_cycle_codes: tuple[int, ...]
    duration_us: int
    points_per_segment: int
    depth_code: int


def awp1221_sinc_k_from_codes(coarse_code: int, fine_code: int) -> int:
    coarse_code = int(coarse_code)
    fine_code = int(fine_code)
    if not 0 <= coarse_code <= 31:
        raise ValueError("Coarse amplitude must be in range 0..31")
    if not 0 <= fine_code <= 3:
        raise ValueError("Fine amplitude must be in range 0..3")
    return 4 * coarse_code + fine_code + 1


def awp1221_sinc_codes_from_k(k: int) -> tuple[int, int]:
    k = int(k)
    if not 1 <= k <= AWP1221_SINC_MAX_K:
        raise ValueError("SINC k must be in range 1..128")
    return (k - 1) // 4, (k - 1) % 4


def awp1221_sinc_offset_steps_from_code(offset_code: int) -> int:
    offset_code = int(offset_code)
    if not 0 <= offset_code <= 127:
        raise ValueError("Waveform offset must be in range 0..127")
    if offset_code == 0:
        return 0
    if offset_code < 0x40:
        return -offset_code
    return 0x80 - offset_code


def awp1221_sinc_offset_code_from_steps(steps: int) -> int:
    steps = int(steps)
    if not AWP1221_SINC_MIN_OFFSET_STEPS <= steps <= AWP1221_SINC_MAX_OFFSET_STEPS:
        raise ValueError("Offset steps must be in range -63..64")
    if steps == 0:
        return 0
    if steps < 0:
        return -steps
    return 0x80 - steps


def _ticks_to_volts(ticks: int) -> float:
    return ticks * AWP1221_SINC_Q_VOLTS


def _segment(k: int, offset_steps: int, half_cycle_code: int, *, rising: bool) -> Awp1221SincSegment:
    coarse_code, fine_code = awp1221_sinc_codes_from_k(k)
    # The VPK/fine code is the complete waveform Vpp.  The preview uses
    # half of that value around the decoded offset, just like the normal
    # AWP1221 waveform selector.
    amplitude = k * AWP1221_SINC_HALF_Q_VOLTS
    offset = offset_steps * AWP1221_SINC_HALF_Q_VOLTS
    start_volts = offset - amplitude
    end_volts = offset + amplitude
    if not rising:
        start_volts, end_volts = end_volts, start_volts
    return Awp1221SincSegment(
        coarse_code=coarse_code,
        fine_code=fine_code,
        offset_code=awp1221_sinc_offset_code_from_steps(offset_steps),
        k=k,
        offset_steps=offset_steps,
        vpp_volts=2 * amplitude,
        offset_volts=offset,
        start_volts=start_volts,
        end_volts=end_volts,
        half_cycle_code=half_cycle_code,
    )


def awp1221_sinc_parse_params(
    peak_text: str,
    trough_text: str,
    time1_text: str,
    time2_text: str,
    time3_text: str,
    depth_text: str,
    time4_text: str | None = None,
    sin_repeat_text: str = "1",
) -> Awp1221SincParams:
    peak = _parse_positive_voltage(peak_text, "Maximum")
    trough = _parse_positive_voltage(trough_text, "Minimum")
    time_texts = (time1_text, time2_text, time3_text)
    if time4_text is not None:
        time_texts += (time4_text,)
    times = tuple(_parse_point_time(value) for value in time_texts)
    repeat_count = _parse_repeat_count(sin_repeat_text)
    depth_key = str(depth_text).strip()
    if depth_key not in AWP1221_SINC_DEPTH_POINTS:
        raise ValueError(f"Unsupported waveform depth: {depth_text}")
    return Awp1221SincParams(
        peak_target=peak,
        trough_target=trough,
        times_us=times,
        depth_points=AWP1221_SINC_DEPTH_POINTS[depth_key],
        sin_repeat_count=repeat_count,
    )


def _parse_positive_voltage(text: str, name: str) -> float:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite value greater than zero")
    return value


def _parse_point_time(text: str) -> int:
    try:
        value = int(str(text).strip(), 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Point time must be in range 1..256") from exc
    if not 1 <= value <= 256:
        raise ValueError("Point time must be in range 1..256")
    return value


def _parse_repeat_count(text: str) -> int:
    try:
        value = int(str(text).strip(), 10)
    except (TypeError, ValueError) as exc:
        raise ValueError("SIN repeat count must be an integer in range 1..256") from exc
    if not 1 <= value <= 256:
        raise ValueError("SIN repeat count must be in range 1..256")
    return value


def awp1221_sinc_duration_us(
    depth_points: int,
    time1: int,
    time2: int,
    time3: int,
    time4: int | None = None,
    sin_repeat_count: int = 1,
) -> int:
    if depth_points not in AWP1221_SINC_DEPTH_CODES:
        raise ValueError(f"Unsupported waveform depth: {depth_points}")
    if time4 is None:
        return (depth_points // 2) * sum(map(int, (time1, time2, time3)))
    else:
        repeat_count = int(sin_repeat_count)
        if not 1 <= repeat_count <= 256:
            raise ValueError("SIN repeat count must be in range 1..256")
        return (
            depth_points * int(time1) * repeat_count
            + (depth_points // 2) * sum(map(int, (time2, time3, time4)))
        )


def awp1221_sinc_slice_windows(serial: str) -> tuple[tuple[int, int], ...]:
    try:
        return AWP1221_SINC_SLICE_WINDOWS[str(serial)]
    except KeyError as exc:
        raise ValueError(f"Unsupported SINC serial group: {serial}") from exc


def awp1221_sinc_solve(peak_target: float, trough_target: float) -> Awp1221SincSolveResult:
    if not math.isfinite(peak_target) or not math.isfinite(trough_target):
        return Awp1221SincSolveResult(False, "invalid", None)
    if peak_target <= 0 or trough_target <= 0:
        return Awp1221SincSolveResult(False, "invalid", None)
    if peak_target + trough_target > 200.0:
        return Awp1221SincSolveResult(False, "span_exceeds_vpp", None)
    if (
        peak_target > AWP1221_SINC_MAX_OFFSET_STEPS * AWP1221_SINC_Q_VOLTS
        or trough_target > -AWP1221_SINC_MIN_OFFSET_STEPS * AWP1221_SINC_Q_VOLTS
    ):
        return Awp1221SincSolveResult(False, "no_continuous_candidate", None)

    best_key: tuple[Any, ...] | None = None
    best: tuple[int, int] | None = None
    for k1 in range(1, min(AWP1221_SINC_MAX_K, AWP1221_SINC_MAX_OFFSET_STEPS) + 1):
        max_k3 = min(AWP1221_SINC_MAX_K - k1, -AWP1221_SINC_MIN_OFFSET_STEPS)
        for k3 in range(1, max_k3 + 1):
            m1 = k1
            m3 = -k3
            m2 = k1 - k3
            if not _offset_steps_ok(m1) or not _offset_steps_ok(m2) or not _offset_steps_ok(m3):
                continue
            peak = k1 * AWP1221_SINC_Q_VOLTS
            trough = k3 * AWP1221_SINC_Q_VOLTS
            # Targets are output limits: rounding outward can clip at the supply
            # rails (for example, a requested -12 V must not become -12.5 V).
            if peak > peak_target or trough > trough_target:
                continue
            score = (peak - peak_target) ** 2 + (trough - trough_target) ** 2
            max_error = max(abs(peak - peak_target), abs(trough - trough_target))
            codes = _code_tiebreak(k1, k3)
            key = (score, max_error, *codes)
            if best_key is None or key < best_key:
                best_key = key
                best = (k1, k3)
    if best is None:
        return Awp1221SincSolveResult(False, "no_continuous_candidate", None)
    return Awp1221SincSolveResult(True, "", _solution_from_k(*best, peak_target, trough_target))


def _offset_steps_ok(steps: int) -> bool:
    return AWP1221_SINC_MIN_OFFSET_STEPS <= steps <= AWP1221_SINC_MAX_OFFSET_STEPS


def _code_tiebreak(k1: int, k3: int) -> tuple[int, ...]:
    k2 = k1 + k3
    m1 = k1
    m2 = k1 - k3
    m3 = -k3
    c1, f1 = awp1221_sinc_codes_from_k(k1)
    c2, f2 = awp1221_sinc_codes_from_k(k2)
    c3, f3 = awp1221_sinc_codes_from_k(k3)
    return (
        c1,
        f1,
        awp1221_sinc_offset_code_from_steps(m1),
        c2,
        f2,
        awp1221_sinc_offset_code_from_steps(m2),
        c3,
        f3,
        awp1221_sinc_offset_code_from_steps(m3),
    )


def _solution_from_k(k1: int, k3: int, peak_target: float, trough_target: float) -> Awp1221SincSolution:
    k2 = k1 + k3
    m1 = k1
    m2 = k1 - k3
    m3 = -k3
    segments = (
        _segment(k1, m1, AWP1221_SINC_HALF_CYCLE_CODES[0], rising=True),
        _segment(k2, m2, AWP1221_SINC_HALF_CYCLE_CODES[1], rising=False),
        _segment(k3, m3, AWP1221_SINC_HALF_CYCLE_CODES[2], rising=True),
    )
    peak = k1 * AWP1221_SINC_Q_VOLTS
    trough = k3 * AWP1221_SINC_Q_VOLTS
    return Awp1221SincSolution(
        peak_volts=peak,
        trough_volts=trough,
        peak_error_volts=peak - peak_target,
        trough_error_volts=trough - trough_target,
        vpp_volts=peak + trough,
        junction_error_ticks=0,
        segments=segments,
    )


def encode_awp1221_sinc_word0(fine_code: int, depth_code: int, half_cycle_code: int) -> int:
    if not 0 <= int(fine_code) <= 3:
        raise ValueError("Fine amplitude must be in range 0..3")
    if int(depth_code) not in (0, 1):
        raise ValueError("Waveform depth code must be 0 or 1")
    if not 0 <= int(half_cycle_code) <= 3:
        raise ValueError("Half cycle must be in range 0..3")
    return (
        (0b10 << 10)
        | (int(fine_code) << 6)
        | (int(depth_code) << 5)
        | (int(half_cycle_code) << 1)
    ) & 0x0FFF


def encode_awp1221_sinc_descriptor_words(
    segment: Awp1221SincSegment,
    *,
    depth_code: int,
    point_time: int,
) -> tuple[int, int, int]:
    if not 1 <= int(point_time) <= 256:
        raise ValueError("Point time must be in range 1..256")
    return (
        encode_awp1221_sinc_word0(segment.fine_code, depth_code, segment.half_cycle_code),
        (int(point_time) - 1) & 0x0FFF,
        0,
    )


def build_awp1221_sinc_plan(
    params: Awp1221SincParams,
    *,
    serial: str,
    window_index: int = 0,
) -> Awp1221SincPlan:
    windows = awp1221_sinc_slice_windows(serial)
    depth_code = AWP1221_SINC_DEPTH_CODES[params.depth_points]
    points_per_segment = params.depth_points // 2
    duration_us = awp1221_sinc_duration_us(
        params.depth_points,
        *params.times_us,
        sin_repeat_count=params.sin_repeat_count,
    )
    solved = awp1221_sinc_solve(params.peak_target, params.trough_target)
    if serial in {"serial3", "serial4"} and len(params.times_us) != 4:
        return Awp1221SincPlan(
            serial=serial,
            writable=False,
            reason="four_times_required",
            slices=(),
            sram_words=(),
            params=params,
            solution=solved.solution,
            descriptor_words=(),
            offset_codes=(),
            vpk_codes=(),
            half_cycle_codes=(),
            duration_us=duration_us,
            points_per_segment=points_per_segment,
            depth_code=depth_code,
        )
    if not windows:
        return Awp1221SincPlan(
            serial=serial,
            writable=False,
            reason="insufficient_slots",
            slices=(),
            sram_words=(),
            params=params,
            solution=solved.solution,
            descriptor_words=(),
            offset_codes=(),
            vpk_codes=(),
            half_cycle_codes=(),
            duration_us=duration_us,
            points_per_segment=points_per_segment,
            depth_code=depth_code,
        )
    if not 0 <= int(window_index) < len(windows):
        raise ValueError("SINC slice window index is out of range")
    start_slice, end_slice = windows[int(window_index)]
    slice_count = 4 if serial in {"serial3", "serial4"} else 3
    slices = tuple(range(start_slice, start_slice + slice_count))
    if slices[-1] > end_slice:
        raise ValueError("Waveform slice window does not contain the required slices")
    sram_words = tuple(
        slice_index * 4 + word
        for slice_index in slices
        for word in range(4)
    )
    writable = serial in AWP1221_SINC_WRITABLE_SERIALS and solved.ok
    if serial not in AWP1221_SINC_WRITABLE_SERIALS:
        reason = "fourth_slot_unconfirmed"
        writable = False
    elif not solved.ok:
        reason = solved.reason
    else:
        reason = ""
    solution = solved.solution
    if solution is None:
        return Awp1221SincPlan(
            serial=serial,
            writable=False,
            reason=reason,
            slices=slices,
            sram_words=sram_words,
            params=params,
            solution=None,
            descriptor_words=(),
            offset_codes=(),
            vpk_codes=(),
            half_cycle_codes=(),
            duration_us=duration_us,
            points_per_segment=points_per_segment,
            depth_code=depth_code,
        )
    sinc_descriptor_words = tuple(
        encode_awp1221_sinc_descriptor_words(
            segment,
            depth_code=depth_code,
            point_time=point_time,
        )
        for segment, point_time in zip(solution.segments, params.times_us[-3:])
    )
    if slice_count == 4:
        if len(params.times_us) != 4:
            raise ValueError("Serial3/4 require one SIN and three SINC point times")
        first = solution.segments[0]
        sin_descriptor_words = (
            (0b10 << 10)
            | (first.fine_code << 6)
            | (depth_code << 5),
            params.times_us[0] - 1,
            params.sin_repeat_count - 1,
        )
        descriptor_words = (sin_descriptor_words, *sinc_descriptor_words)
        offset_codes = (first.offset_code, *(segment.offset_code for segment in solution.segments))
        vpk_codes = (first.coarse_code, *(segment.coarse_code for segment in solution.segments))
        half_cycle_codes = (0, *(segment.half_cycle_code for segment in solution.segments))
    else:
        descriptor_words = sinc_descriptor_words
        offset_codes = tuple(segment.offset_code for segment in solution.segments)
        vpk_codes = tuple(segment.coarse_code for segment in solution.segments)
        half_cycle_codes = tuple(segment.half_cycle_code for segment in solution.segments)
    return Awp1221SincPlan(
        serial=serial,
        writable=writable,
        reason=reason,
        slices=slices,
        sram_words=sram_words,
        params=params,
        solution=solution,
        descriptor_words=descriptor_words,
        offset_codes=offset_codes,
        vpk_codes=vpk_codes,
        half_cycle_codes=half_cycle_codes,
        duration_us=duration_us,
        points_per_segment=points_per_segment,
        depth_code=depth_code,
    )


def awp1221_sinc_write_words(
    plan: Awp1221SincPlan,
    old_words: dict[int, int],
) -> tuple[tuple[int, int], ...]:
    if plan.solution is None or not plan.slices:
        raise ValueError("SINC plan has no slice words to write")
    written: list[tuple[int, int]] = []
    for index, slice_index in enumerate(plan.slices):
        base = slice_index * 4
        word0, word1, word2 = plan.descriptor_words[index]
        current_word3 = int(old_words.get(base + 3, 0))
        if not 0 <= current_word3 <= 0xFFFF:
            raise ValueError("Waveform word 3 must be in range 0x0000..0xFFFF")
        word3 = (
            (current_word3 & ~0x0FFF)
            | (plan.offset_codes[index] << 5)
            | plan.vpk_codes[index]
        )
        written.extend(
            (
                (base, word0),
                (base + 1, word1),
                (base + 2, word2),
                (base + 3, word3),
            )
        )
    return tuple(written)


def awp1221_sinc_plan_key(plan: Awp1221SincPlan, device_addr: int) -> tuple[Any, ...]:
    return (
        int(device_addr),
        plan.serial,
        plan.slices,
        plan.params,
        plan.descriptor_words,
        plan.offset_codes,
        plan.vpk_codes,
        plan.depth_code,
    )
