"""AWP1921/1923 page settings shared by Button and Full Mode."""

from __future__ import annotations

from typing import Any

from pages.chip_settings_model import ChipSettingsModel, ChipSettingsStore


class Awp192xChipSettingsModel(ChipSettingsModel):
    """192x settings draft with the Type-3 slot capability preflight."""

    def can_save_slot(self) -> tuple[bool, str | None]:
        if self.advanced.get("slot_blocked"):
            return False, "preset_slot_advanced_block_warning"
        return True, None


def settings_store_for_context(ctx: Any) -> ChipSettingsStore:
    """Return the chip-host-owned settings store without keeping page refs."""
    state = ctx.state
    store = getattr(state, "_awp192x_chip_settings_store", None)
    if store is None:
        store = ChipSettingsStore(Awp192xChipSettingsModel)
        setattr(state, "_awp192x_chip_settings_store", store)
    return store


def connection_key_for_context(ctx: Any) -> tuple[int, str]:
    """Identify the active serial router/port; DeviceKey also includes address."""
    state = ctx.state
    port = getattr(state, "selected_port", None)
    if port is None:
        port = getattr(state, "extra", {}).get("selected_port_name", "")
    return id(ctx.serial), str(port or "")


def waveform_number(value: Any) -> int:
    """Convert a Button or Full waveform selector into its register number."""
    text = str(value).strip().lower()
    if "——" in text:
        text = text.split("——", 1)[0].strip()
    if "rom sin" in text or text in {"sin", "0x1f", "31"}:
        return 0x1F
    if text == "serial" or text.startswith("0x1e") or text == "30":
        return 0x1E
    if text.startswith("slice"):
        result = int(text[5:], 10)
    else:
        result = int(text, 0)
    if not 0 <= result <= 0x1F:
        raise ValueError("waveform must be in the 0-31 range")
    return result


def waveform_label(value: int, *, full: bool = False) -> str:
    number = int(value)
    if number == 0x1F and full:
        return "ROM sin"
    if number == 0x1E:
        return "serial"
    if 0 <= number <= 29:
        return f"slice{number}"
    return f"0x{number:02X}"


__all__ = [
    "Awp192xChipSettingsModel",
    "settings_store_for_context",
    "connection_key_for_context",
    "waveform_number",
    "waveform_label",
]
