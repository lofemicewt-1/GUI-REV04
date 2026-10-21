from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from core import base_tool


WaveformType = Literal["sine", "square", "triangle", "dc", "sinc", "gauss"]


@dataclass
class WaveformResult:
    values: list[int]
    voltages: list[float]


class WaveformEngine:
    """
    \u6ce2\u5f62\u751f\u6210\u4e0e DAC \u6620\u5c04\u903b\u8f91\u3002

    \u6620\u5c04\u89c4\u5219\uff1a
        +Vmax  -> 0xFFF
        +0 V   -> 0x800
        -0 V   -> 0x7FF
        -Vmax  -> 0x000

    \u6ce8\u610f\uff1a\u6570\u5b66\u4e0a\u7684 0 \u8fd9\u91cc\u9ed8\u8ba4\u6620\u5c04\u5230 +0V\uff0c\u5373 0x800\u3002
    \u5982\u679c\u540e\u7eed\u9700\u8981\u533a\u5206 +0V / -0V\uff0c\u53ef\u4ee5\u5728 map_voltage_to_dac \u589e\u52a0 direction \u53c2\u6570\u3002
    """

    MAX_POINTS = 1020

    DAC_MIN = base_tool.DAC_MIN
    DAC_MAX = base_tool.DAC_MAX
    POS_ZERO = base_tool.DAC_POS_ZERO
    NEG_ZERO = base_tool.DAC_NEG_ZERO

    DEFAULT_VOLTAGE_RANGE = base_tool.DEFAULT_VOLTAGE_RANGE

    def __init__(self, voltage_range: float = DEFAULT_VOLTAGE_RANGE) -> None:
        self.voltage_range = float(voltage_range)

    def generate(
        self,
        waveform_type: WaveformType,
        points: int,
        start_phase_deg: float = 0.0,
        end_phase_deg: float = 360.0,
        offset: float = 0.0,
        amplitude: float | None = None,
        *,
        sinc_vmax: float | None = None,
        sinc_vmin: float | None = None,
        sinc_points1: int | None = None,
        sinc_points2: int | None = None,
        sinc_points3: int | None = None,
        gauss_peak: float | None = None,
        gauss_start_positive_zero: bool = True,
    ) -> WaveformResult:
        points = self._validate_points(points)
        amp = self.voltage_range if amplitude is None else abs(float(amplitude))
        amp = min(amp, self.voltage_range)

        if waveform_type == "sine":
            voltages = self._generate_sine(points, start_phase_deg, end_phase_deg, offset, amp)
        elif waveform_type == "square":
            voltages = self._generate_square(points, start_phase_deg, end_phase_deg, offset, amp)
        elif waveform_type == "triangle":
            voltages = self._generate_triangle(points, start_phase_deg, end_phase_deg, offset, amp)
        elif waveform_type == "dc":
            voltages = [offset for _ in range(points)]
        elif waveform_type == "sinc":
            from core.awp192x_button_runtime import (
                AWP192X_GENERATOR_SINC,
                AWP192X_GENERATOR_SINC_VERSION,
                Awp192xWaveformRecipe,
                generate_awp192x_sinc_points,
            )

            n1 = sinc_points1 if sinc_points1 is not None else max(1, points // 3)
            n2 = sinc_points2 if sinc_points2 is not None else max(1, points // 3)
            n3 = sinc_points3 if sinc_points3 is not None else max(1, points - 2 * (points // 3))
            values = list(
                generate_awp192x_sinc_points(
                    Awp192xWaveformRecipe(
                        AWP192X_GENERATOR_SINC,
                        AWP192X_GENERATOR_SINC_VERSION,
                        0,
                        0,
                        n1,
                        n2,
                        n3,
                        round((sinc_vmax if sinc_vmax is not None else self.voltage_range) * 1000),
                        round((sinc_vmin if sinc_vmin is not None else -self.voltage_range) * 1000),
                    )
                )
            )
            return WaveformResult(values=values, voltages=[self.map_dac_to_voltage(value) for value in values])
        elif waveform_type == "gauss":
            voltages = self._generate_gauss(
                points=points,
                peak=gauss_peak if gauss_peak is not None else amp,
                start_positive_zero=gauss_start_positive_zero,
            )
            # Bias is applied in volts before the same signed DAC mapping used
            # by sine: zero tails encode as POS_ZERO, never DAC_MIN.
            voltages = [voltage + offset for voltage in voltages]
        else:
            raise ValueError(f"Unsupported waveform type: {waveform_type}")

        voltages = [self._clip_voltage(v) for v in voltages]
        values = [self.map_voltage_to_dac(v) for v in voltages]
        return WaveformResult(values=values, voltages=voltages)

    def map_voltage_to_dac(self, voltage: float) -> int:
        return base_tool.map_voltage_to_dac(voltage, self.voltage_range)

    def map_dac_to_voltage(self, code: int) -> float:
        return base_tool.map_dac_to_voltage(code, self.voltage_range)

    def _validate_points(self, points: int) -> int:
        points = int(points)
        if points <= 0:
            raise ValueError("points must be greater than 0")
        if points > self.MAX_POINTS:
            raise ValueError(f"points must be <= {self.MAX_POINTS}")
        return points

    def _clip_voltage(self, voltage: float) -> float:
        return base_tool.clip_voltage(voltage, self.voltage_range)

    def _phase_values(self, points: int, start_phase_deg: float, end_phase_deg: float) -> list[float]:
        if points == 1:
            return [math.radians(start_phase_deg)]
        start = math.radians(start_phase_deg)
        end = math.radians(end_phase_deg)
        return [start + (end - start) * i / (points - 1) for i in range(points)]

    def _generate_sine(self, points: int, start_phase_deg: float, end_phase_deg: float, offset: float, amplitude: float) -> list[float]:
        phases = self._phase_values(points, start_phase_deg, end_phase_deg)
        return [math.sin(p) * amplitude + offset for p in phases]

    def _generate_square(self, points: int, start_phase_deg: float, end_phase_deg: float, offset: float, amplitude: float) -> list[float]:
        phases = self._phase_values(points, start_phase_deg, end_phase_deg)
        return [
            (amplitude if math.sin(p) >= 0 else -amplitude) + offset
            for p in phases
        ]

    def _generate_triangle(self, points: int, start_phase_deg: float, end_phase_deg: float, offset: float, amplitude: float) -> list[float]:
        phases = self._phase_values(points, start_phase_deg, end_phase_deg)
        out: list[float] = []
        for p in phases:
            normalized = (p / (2 * math.pi)) % 1.0
            if normalized < 0.25:
                y = normalized * 4
            elif normalized < 0.75:
                y = 2 - normalized * 4
            else:
                y = normalized * 4 - 4
            out.append(y * amplitude + offset)
        return out

    def _generate_sinc(self, vmax: float, vmin: float, n1: int, n2: int, n3: int) -> list[float]:
        """
        \u6839\u636e\u4f60\u4e4b\u524d\u5b9a\u4e49\u7684 SINC\uff1a
            \u6bb51\uff1a270\u00b0 -> 90\u00b0\uff0c0V -> Vmax
            \u6bb52\uff1a90\u00b0  -> 270\u00b0\uff0cVmax -> Vmin
            \u6bb53\uff1a270\u00b0 -> 90\u00b0\uff0cVmin -> 0V
        \u8fd9\u91cc\u4f7f\u7528 half-sine \u63d2\u503c\uff0c\u4fbf\u4e8e\u51c6\u786e\u63a7\u5236\u7aef\u70b9\u3002
        """
        n1, n2, n3 = max(1, int(n1)), max(1, int(n2)), max(1, int(n3))
        if n1 + n2 + n3 > self.MAX_POINTS:
            raise ValueError(f"SINC total points must be <= {self.MAX_POINTS}")

        seg1 = self._smooth_segment(0.0, vmax, n1)
        seg2 = self._smooth_segment(vmax, vmin, n2)
        seg3 = self._smooth_segment(vmin, 0.0, n3)
        return seg1 + seg2 + seg3

    def _smooth_segment(self, start: float, end: float, n: int) -> list[float]:
        if n == 1:
            return [end]
        out = []
        for i in range(n):
            t = i / (n - 1)
            # half cosine ease: endpoints exact, \u4e2d\u95f4\u5e73\u6ed1
            s = (1 - math.cos(math.pi * t)) / 2
            out.append(start + (end - start) * s)
        return out

    def _generate_gauss(self, points: int, peak: float, start_positive_zero: bool = True) -> list[float]:
        """
        \u7b80\u5355\u9ad8\u65af\u5305\u7edc\uff1a\u8d77\u70b9\u63a5\u8fd1 0\uff0c\u4e2d\u95f4\u8fbe\u5230 peak\uff0c\u672b\u7aef\u56de\u5230 0\u3002
        """
        sigma = 0.18
        center = 0.5
        raw = []
        for i in range(points):
            x = i / (points - 1) if points > 1 else 0.5
            y = math.exp(-((x - center) ** 2) / (2 * sigma * sigma))
            raw.append(y)

        min_y = min(raw)
        max_y = max(raw)
        normalized = [(y - min_y) / (max_y - min_y) if max_y != min_y else 0 for y in raw]
        voltages = [v * peak for v in normalized]

        # \u8d77\u70b9\u9700\u8981 +0V \u6216 -0V \u65f6\uff0c\u5b9e\u9645\u7535\u538b\u4ecd\u662f 0\uff1b\u6620\u5c04\u5c42\u76ee\u524d\u9ed8\u8ba4 +0V\u3002
        # \u82e5\u5fc5\u987b\u533a\u5206 -0V\uff0c\u53ef\u540e\u7eed\u8ba9 map_voltage_to_dac \u652f\u6301 zero_sign\u3002
        return voltages
