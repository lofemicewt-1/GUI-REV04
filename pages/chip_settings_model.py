"""UI-independent, device-keyed chip settings drafts.

Chip adapters should store semantic settings and register field values here.
Do not place Tk variables, widgets, translated display text, or page instances
in ``shared`` or ``advanced``. Those values must remain usable when a view is
destroyed and rebuilt.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


# AWP1221 Button ↔ Full fields from GUI-REV04 section 4: waveform recipe and
# descriptors, ADC settings, and corresponding Stage0/Stage1 semantic fields.
SYNC_FIELDS_1221: tuple[str, ...] = (
    "waveform_recipe",
    "waveform_descriptors",
    "adc_settings",
    "stage0_semantics",
    "stage1_semantics",
)

# AWP1921/1923 Button ↔ Full fields from GUI-REV04 section 4: output amplitude,
# point duration, thresholds, filters, polarity/range, press/release actions,
# manual playback, and serial waveforms.
SYNC_FIELDS_192X: tuple[str, ...] = (
    "output_amplitude",
    "point_duration",
    "thresholds",
    "filter_settings",
    "polarity",
    "range",
    "press_action",
    "release_action",
    "manual_playback",
    "serial_waveforms",
)


@dataclass(frozen=True)
class DeviceKey:
    """Connection identity plus chip address for an isolated draft."""

    connection: Hashable
    chip_address: Hashable

    def __post_init__(self) -> None:
        try:
            hash(self.connection)
            hash(self.chip_address)
        except TypeError as exc:
            raise TypeError("connection and chip_address must be hashable") from exc

    @classmethod
    def create(cls, connection: Hashable, chip_address: int | str) -> DeviceKey:
        return cls(connection, _normalize_address(chip_address))


@dataclass
class ChipSettingsModel:
    """One chip's shared and Full-only semantic settings draft."""

    shared: dict[str, Any] = field(default_factory=dict)
    advanced: dict[str, Any] = field(default_factory=dict)
    slot_selection: int | str | None = None
    revision: int = 0
    dirty: bool = False
    applied: bool = False
    applied_revision: int | None = None
    device_key: DeviceKey | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Copy input containers so separate models never share nested drafts.
        self.shared = deepcopy(self.shared)
        self.advanced = deepcopy(self.advanced)

    def set_shared(self, name: str, value: Any) -> bool:
        return self._set_value(self.shared, name, value)

    def update_shared(self, values: Mapping[str, Any]) -> bool:
        return self._update_values(self.shared, values)

    def set_advanced(self, name: str, value: Any) -> bool:
        return self._set_value(self.advanced, name, value)

    def update_advanced(self, values: Mapping[str, Any]) -> bool:
        return self._update_values(self.advanced, values)

    def set_slot_selection(self, slot: int | str | None) -> bool:
        """Store this device's slot picker value without dirtying chip data."""
        if self.slot_selection == slot:
            return False
        self.slot_selection = slot
        return True

    def mark_modified(self) -> None:
        """Record an edit made through a bulk adapter update."""
        self.revision += 1
        self.dirty = True
        self.invalidate_applied()

    def mark_applied(self) -> None:
        """Record that the current revision was confirmed on the hardware."""
        self.applied = True
        self.applied_revision = self.revision
        self.dirty = False

    def invalidate_applied(self) -> None:
        self.applied = False
        self.applied_revision = None

    def can_save_slot(self) -> tuple[bool, str | None]:
        """Base board-slot policy; chip-specific model types may override it."""
        return True, None

    def _set_value(self, target: dict[str, Any], name: str, value: Any) -> bool:
        if name in target and target[name] == value:
            return False
        target[name] = deepcopy(value)
        self.mark_modified()
        return True

    def _update_values(self, target: dict[str, Any], values: Mapping[str, Any]) -> bool:
        changed = {
            name: deepcopy(value)
            for name, value in values.items()
            if name not in target or target[name] != value
        }
        if not changed:
            return False
        target.update(changed)
        self.mark_modified()
        return True


class ChipSettingsStore:
    """Keep independent drafts for each connection/address pair.

    Switching to another device selects its existing draft or creates an empty
    one. The outgoing and incoming drafts both lose their applied confirmation
    because hardware confirmation belongs to the currently selected device.
    """

    def __init__(
        self,
        model_factory: Callable[[], ChipSettingsModel] = ChipSettingsModel,
    ) -> None:
        self._drafts: dict[DeviceKey, ChipSettingsModel] = {}
        self._current_key: DeviceKey | None = None
        self._model_factory = model_factory

    @property
    def current_device_key(self) -> DeviceKey | None:
        return self._current_key

    @property
    def current_model(self) -> ChipSettingsModel | None:
        if self._current_key is None:
            return None
        return self._drafts[self._current_key]

    def select_device(self, connection: Hashable, chip_address: int | str) -> ChipSettingsModel:
        """Activate a device draft, invalidating stale applied status on switch."""
        key = DeviceKey.create(connection, chip_address)
        if key == self._current_key:
            return self._drafts[key]

        if self._current_key is not None:
            self._drafts[self._current_key].invalidate_applied()

        model = self._drafts.get(key)
        if model is None:
            model = self._model_factory()
            model.device_key = key
            self._drafts[key] = model
        else:
            model.device_key = key
        model.invalidate_applied()
        self._current_key = key
        return model

    def get_draft(self, connection: Hashable, chip_address: int | str) -> ChipSettingsModel | None:
        """Read a stored draft without changing the active device."""
        return self._drafts.get(DeviceKey.create(connection, chip_address))


def can_save_slot(model: ChipSettingsModel) -> tuple[bool, str | None]:
    """Default board-slot preflight; chip adapters can override this policy.

    Return ``(False, warning_key)`` to block the save and show the translated
    warning. A capable chip's default is allowed with no warning.
    """
    return model.can_save_slot()


def _normalize_address(address: int | str) -> int | str:
    if isinstance(address, str):
        value = address.strip()
        try:
            return int(value, 0)
        except ValueError:
            return value
    return address


__all__ = [
    "DeviceKey",
    "ChipSettingsModel",
    "ChipSettingsStore",
    "SYNC_FIELDS_1221",
    "SYNC_FIELDS_192X",
    "can_save_slot",
]
