"""Display-only repeat compaction; playback samples and descriptors stay intact."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence


@dataclass(frozen=True)
class PreviewSegment:
    label: str
    samples: Sequence[float]
    repeats: int = 1
    period: float | None = None

    @property
    def cycle_duration(self) -> float:
        return float(len(self.samples) if self.period is None else self.period)


@dataclass(frozen=True)
class PreviewPart:
    label: str
    samples: Sequence[float]
    start: float
    end: float
    time_start: float
    time_end: float
    source_index: int
    omitted: int = 0


@dataclass(frozen=True)
class PreviewLayout:
    segments: tuple[PreviewSegment, ...]
    parts: tuple[PreviewPart, ...]
    segment_ends: tuple[float, ...]
    total: float
    display_total: float

    def position(self, time: float) -> float:
        """Map real time to a discontinuous display axis, including gap edges."""
        if not self.parts or self.display_total <= 0:
            return 0.0
        for part in self.parts:
            if time <= part.time_end:
                fraction = max(0.0, (time - part.time_start) / max(part.time_end - part.time_start, 1e-12))
                return (part.start + fraction * (part.end - part.start)) / self.display_total
        return 1.0


def compact_preview(segments, *, merge_adjacent: bool = True) -> PreviewLayout:
    segments = tuple(item if isinstance(item, PreviewSegment) else PreviewSegment(*item) for item in segments)
    runs = []
    ends = [0.0]
    for index, segment in enumerate(segments):
        period = segment.cycle_duration
        repeats = max(1, int(segment.repeats))
        ends.append(ends[-1] + period * repeats)
        if not segment.samples or period <= 0:
            continue
        if (merge_adjacent and runs and runs[-1][3] == index - 1 and runs[-1][0].cycle_duration == period
                and (runs[-1][0].samples is segment.samples or runs[-1][0].samples == segment.samples)):
            runs[-1][1] += repeats
            runs[-1][3] = index
        else:
            runs.append([segment, repeats, index, index, ends[-2]])
    parts = []
    display = 0.0
    for segment, repeats, first, last, elapsed in runs:
        period = segment.cycle_duration
        parts.append(PreviewPart(segment.label, segment.samples, display, display + period,
                                 elapsed, elapsed + period, first))
        display += period
        if repeats > 2:
            # A short blank span replaces all middle cycles, without sampling
            # or allocating them. Its two edges retain their real timestamps.
            parts.append(PreviewPart("...", (), display, display + period * .6,
                                     elapsed + period, elapsed + period * (repeats - 1), first, repeats - 2))
            display += period * .6
        if repeats > 1:
            parts.append(PreviewPart(segments[last].label, segment.samples, display, display + period,
                                     elapsed + period * (repeats - 1), elapsed + period * repeats, last))
            display += period
    return PreviewLayout(segments, tuple(parts), tuple(ends), ends[-1], display)


def draw_omissions(canvas, layout: PreviewLayout, left, width, y, colors) -> None:
    for part in layout.parts:
        if part.omitted:
            x = left + width * (part.start + part.end) / (2 * layout.display_total)
            if width * (part.end - part.start) / layout.display_total >= 30:
                canvas.create_rectangle(x - 12, y - 8, x + 12, y + 8, outline="",
                                        fill=colors.get("canvas_bg", colors.get("bg", "#FFFFFF")))
            canvas.create_text(x, y, text="...", anchor="center", fill=colors.get("muted", "#64748B"),
                               font=("Microsoft YaHei UI", 13, "bold"), tags="repeat_omission")
