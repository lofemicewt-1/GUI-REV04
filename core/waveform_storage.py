from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.awp192x_button_runtime import (
        Awp192xSineWaveformRecipe,
        Awp192xWaveformRecipe,
    )


@dataclass
class SliceInfo:
    index: int
    start_addr: int
    end_addr: int
    points: int
    waveform_type: str
    values: list[int] = field(default_factory=list)
    power_on_recipe: Awp192xWaveformRecipe | Awp192xSineWaveformRecipe | None = None

    def overlaps(self, other_start: int, other_end: int) -> bool:
        """
        \u51b2\u7a81\u5224\u65ad\uff1a
        - start == previous_end \u5141\u8bb8
        - end == next_start \u5141\u8bb8
        - \u53ea\u6709\u771f\u6b63\u4ea4\u53c9\u624d\u7b97\u51b2\u7a81
        """
        return self.start_addr < other_end and other_start < self.end_addr


class WaveformStorage:
    """
    \u7ba1\u7406 slice \u5230 sRAM \u5730\u5740\u533a\u95f4\u7684\u6620\u5c04\u3002
    """

    SRAM_MIN = 0x0000
    SRAM_MAX = 0x03FF
    MAX_SLICES = 30

    def __init__(self) -> None:
        self._slices: dict[int, SliceInfo] = {}
        self._successful_writes: dict[int, SliceInfo] = {}

    @property
    def slices(self) -> dict[int, SliceInfo]:
        return dict(self._slices)

    def clear(self) -> None:
        self._slices.clear()
        self._successful_writes.clear()

    def validate_range(self, start_addr: int, end_addr: int) -> None:
        if start_addr < self.SRAM_MIN:
            raise ValueError("start address is below SRAM minimum")
        if end_addr > self.SRAM_MAX + 1:
            raise ValueError("end address is above SRAM maximum")
        if end_addr < start_addr:
            raise ValueError("end address must be >= start address")

    def make_end_addr(self, start_addr: int, points: int) -> int:
        """
        end_addr \u4f7f\u7528\u534a\u5f00\u533a\u95f4\u98ce\u683c\uff1a
            [start_addr, end_addr)
        \u6240\u4ee5 points=10, start=0x0000 => end=0x000A\u3002
        \u8fd9\u6837 start == previous_end \u53ef\u4ee5\u81ea\u7136\u8868\u793a\u76f8\u90bb\u4f46\u4e0d\u51b2\u7a81\u3002
        """
        return int(start_addr) + int(points)

    def conflicts(self, start_addr: int, end_addr: int, ignore_index: int | None = None) -> list[SliceInfo]:
        self.validate_range(start_addr, end_addr)
        found: list[SliceInfo] = []
        for idx, item in self._slices.items():
            if ignore_index is not None and idx == ignore_index:
                continue
            if item.overlaps(start_addr, end_addr):
                found.append(item)
        return found

    def add_or_replace(
        self,
        index: int,
        start_addr: int,
        end_addr: int,
        points: int,
        waveform_type: str,
        values: list[int],
        power_on_recipe: Awp192xWaveformRecipe | Awp192xSineWaveformRecipe | None = None,
    ) -> SliceInfo:
        if index < 0 or index >= self.MAX_SLICES:
            raise ValueError(f"slice index must be 0-{self.MAX_SLICES - 1}")

        self.validate_range(start_addr, end_addr)

        item = SliceInfo(
            index=index,
            start_addr=start_addr,
            end_addr=end_addr,
            points=points,
            waveform_type=waveform_type,
            values=list(values),
            power_on_recipe=power_on_recipe,
        )
        self._slices[index] = item
        return item

    def remove(self, index: int) -> None:
        self._slices.pop(index, None)
        self._successful_writes.pop(index, None)

    def record_successful_write(self, index: int) -> SliceInfo:
        """Freeze the software values that were last written successfully to the device."""
        item = self._slices.get(index)
        if item is None:
            raise ValueError(f"slice{index} is not available")

        snapshot = SliceInfo(
            index=item.index,
            start_addr=item.start_addr,
            end_addr=item.end_addr,
            points=item.points,
            waveform_type=item.waveform_type,
            values=list(item.values),
            power_on_recipe=item.power_on_recipe,
        )
        self._successful_writes[index] = snapshot
        return snapshot

    def get_successful_write(self, index: int) -> SliceInfo | None:
        """Return an isolated copy of the last successful device-write snapshot."""
        item = self._successful_writes.get(index)
        if item is None:
            return None
        return SliceInfo(
            index=item.index,
            start_addr=item.start_addr,
            end_addr=item.end_addr,
            points=item.points,
            waveform_type=item.waveform_type,
            values=list(item.values),
            power_on_recipe=item.power_on_recipe,
        )

    def describe(self) -> str:
        if not self._slices:
            return ""

        lines: list[str] = []
        for idx in sorted(self._slices):
            item = self._slices[idx]
            lines.append(
                f"slice{idx}: "
                f"0x{item.start_addr:04X} - 0x{item.end_addr:04X}, "
                f"{item.points} pts, {item.waveform_type}"
            )
        return "\n".join(lines)
