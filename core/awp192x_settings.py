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


def connection_key_for_context(
    ctx: Any,
    page_name: str = "awp1921_1923",
) -> tuple[Any, ...]:
    """Identify the chip page's active transport session.

    The page-level selector can override the application's global port. A
    SerialRouter identity also includes its manager and connection generation
    so reconnecting on the same port does not reuse an old draft.
    """
    state = ctx.state
    serial = ctx.serial
    selector = getattr(serial, "selected_port_for_page", None)
    try:
        port = selector(page_name) if callable(selector) else None
    except TypeError:
        port = selector() if callable(selector) else None
    if port is None:
        port = getattr(state, "selected_port", None)
    if port is None:
        port = getattr(state, "extra", {}).get("selected_port_name", "")

    identity_provider = getattr(serial, "page_connection_identity", None)
    if callable(identity_provider):
        try:
            identity = identity_provider(page_name)
        except TypeError:
            identity = identity_provider()
        if identity is not None:
            return (id(serial), *tuple(identity))

    manager_provider = getattr(serial, "manager_for_port", None)
    manager = manager_provider(str(port)) if callable(manager_provider) and port else None
    if manager is not None:
        generations = getattr(serial, "_manager_connection_generations", {})
        generation = max(
            int(getattr(manager, "connection_generation", 0)),
            int(generations.get(id(manager), 0)),
        )
        return id(serial), str(port or ""), id(manager), generation

    return id(serial), str(port or ""), int(getattr(serial, "connection_generation", 0))


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
