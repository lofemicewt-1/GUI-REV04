"""Read-only supported-chip discovery definitions for the REV04 GUI protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class ChipProbe:
    """One supported I2C address range and its immutable identification read."""

    name: str
    addresses: tuple[int, ...]
    id_register: int
    expected_id: int
    attempts: int = 2


@dataclass(frozen=True)
class ChipDiscovery:
    """A positive read-only identification result."""

    probe: ChipProbe
    address: int
    chip_id: int


AWP192X_PROBE = ChipProbe(
    name="AWP192X",
    addresses=(0x44, 0x45, 0x46),
    id_register=0x1E,
    expected_id=0x5081,
)
AWP1018_PROBE = ChipProbe(
    name="AWP1018",
    addresses=tuple(range(0x48, 0x50)),
    id_register=0x2B,
    expected_id=0x1018,
    attempts=3,
)
AWP1221_PROBE = ChipProbe(
    name="AWP1221",
    addresses=tuple(range(0x50, 0x58)),
    id_register=0x16,
    expected_id=0x1221,
)

# Preserve the GUI's existing AWP1018-first preference while allowing the
# complete supported address map to be probed without an MCU scan command.
CHIP_PROBES = (AWP1018_PROBE, AWP192X_PROBE, AWP1221_PROBE)


def iter_chip_candidates(probes: Iterable[ChipProbe] = CHIP_PROBES):
    """Yield every allowed identification read without performing I2C writes."""
    for probe in probes:
        for address in probe.addresses:
            yield probe, address


def discover_first(
    read_id: Callable[[ChipProbe, int], int | None],
    *,
    probes: Iterable[ChipProbe] = CHIP_PROBES,
) -> ChipDiscovery | None:
    """Return the first exact chip-ID match from a caller-supplied read function."""
    for probe, address in iter_chip_candidates(probes):
        value = read_id(probe, address)
        if value == probe.expected_id:
            return ChipDiscovery(probe=probe, address=address, chip_id=value)
    return None
