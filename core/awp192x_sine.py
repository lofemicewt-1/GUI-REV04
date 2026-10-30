"""Choose the AWP192x ROM registers or a sampled SRAM sine period."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SinePlan:
    source: str
    points: int
    point_us: int
    reg14: int
    requested_frequency: float
    playback_points: int | None = None

    @property
    def total_points(self) -> int:
        return self.points if self.playback_points is None else self.playback_points

    @property
    def reg15(self) -> int:
        return self.point_us - 1

    @property
    def actual_frequency(self) -> float:
        return 1_000_000 / (self.total_points * self.point_us)


def plan_sine(voltage: float, frequency: float, *, max_points: int = 1020,
              start_phase: float = 0, end_phase: float = 360, offset: float = 0,
              fill_zero_tail: bool = False) -> SinePlan:
    """Prefer an exact ROM representation, otherwise minimize SRAM frequency error.

    Register 0x14: amplitude bits 6:2, depth bits 9:7, phase bit 10,
    half-cycle bit 11, SRAM/ROM playback mode 2. Register 0x15 is dt_us - 1.
    Ties in SRAM timing favor more samples. All decisions are software-only.
    """
    if not all(math.isfinite(v) for v in (voltage, frequency, start_phase, end_phase, offset)):
        raise ValueError("Voltage, frequency and phase must be finite numbers")
    if not 0 <= voltage <= 95.04 or abs(offset) + voltage > 95.04:
        raise ValueError("Voltage amplitude plus offset must stay within +/-95.04 V")
    if frequency <= 0:
        raise ValueError("Frequency must be greater than 0 Hz")
    if not 1_000_000 / (1024 * 65535) <= frequency <= 250_000:
        raise ValueError("Frequency is outside the AWP192x sample timing range")
    if end_phase <= start_phase:
        raise ValueError("End phase must be greater than start phase")

    amplitude_code = round(voltage / 2.97) - 1
    span = end_phase - start_phase
    phase = start_phase % 360
    if (not fill_zero_tail and offset == 0 and phase in (0, 180) and span in (180, 360)
            and 0 <= amplitude_code <= 31
            and math.isclose(voltage, 2.97 * (amplitude_code + 1), abs_tol=1e-9)):
        for depth_code in range(8):
            points = (1024 >> depth_code) // (2 if span == 180 else 1)
            point_us = round(1_000_000 / (frequency * points))
            if 1 <= point_us <= 65535 and math.isclose(
                    1_000_000 / (points * point_us), frequency, rel_tol=1e-10):
                reg14 = (amplitude_code << 2) | 2 | (depth_code << 7)
                reg14 |= (int(phase == 180) << 10) | (int(span == 180) << 11)
                return SinePlan("rom", points, point_us, reg14, frequency)

    available = min(1024, int(max_points))
    capacity = min(1020, available)
    if capacity < 8:
        raise ValueError("At least 8 SRAM samples must be available")
    timing_points = available if fill_zero_tail else capacity
    max_frequency = 1_000_000 / available if fill_zero_tail else 125_000
    if not 1_000_000 / (timing_points * 65535) <= frequency <= max_frequency:
        raise ValueError(f"SRAM frequency must be {1_000_000 / (timing_points * 65535):.6g}-{max_frequency:.6g} Hz")
    period_us = 1_000_000 / frequency
    best = None
    for points in range(8, capacity + 1):
        ideal = period_us / (available if fill_zero_tail else points)
        for point_us in {max(1, min(65535, math.floor(ideal))), max(1, min(65535, math.ceil(ideal)))}:
            # A zero tail occupies the complete selected SRAM range.
            actual_points = available if fill_zero_tail else points
            error = abs(1_000_000 / (actual_points * point_us) - frequency)
            key = (error, -points)
            if best is None or key < best[0]:
                best = (key, points, point_us)
    return SinePlan("ram", best[1], best[2], 0x007E, frequency, available if fill_zero_tail else None)
