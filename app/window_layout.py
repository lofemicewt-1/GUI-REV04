"""Shared layout breakpoints for the resizable application window."""

from __future__ import annotations


CONTENT_MAX_WIDTH = 1800
WAVEFORM_MAX_WIDTH = 960


def ui_scale_for_width(width: int) -> float:
    """Return the frozen application scale tier for a client-area width."""
    width = max(1, int(width))
    if width >= 2200:
        return 1.25
    if width >= 1600:
        return 1.10
    return 1.00


def is_compact_window(width: int, height: int) -> bool:
    """Whether the window should start with its floating chrome collapsed."""
    return int(width) < 1400 or int(height) < 800


def scaled_content_max_width(scale: float, available: int) -> int:
    return max(1, min(max(1, int(available)), int(round(CONTENT_MAX_WIDTH * scale))))


def scaled_waveform_max_width(scale: float) -> int:
    return max(1, int(round(WAVEFORM_MAX_WIDTH * scale)))


def clamped_waveform_width(scale: float, available: int, reserved: int = 0) -> int:
    """Return a waveform column width bounded by its tier and free space."""
    free_width = max(1, int(available) - max(0, int(reserved)))
    return min(scaled_waveform_max_width(scale), free_width)
