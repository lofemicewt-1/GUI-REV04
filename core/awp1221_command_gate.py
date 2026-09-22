from __future__ import annotations

from collections.abc import Callable

AWP1221_CMD_GATE_REGISTER = 0x1F
AWP1221_CMD_GATE_BIT = 0x0008


def awp1221_cmd_gate_write_value(current: int) -> int | None:
    value = int(current) & 0xFFFF
    if value & AWP1221_CMD_GATE_BIT:
        return None
    return value | AWP1221_CMD_GATE_BIT


def ensure_awp1221_cmd_gate(
    read_register: Callable[[int], int],
    write_register: Callable[[int, int], None],
) -> int:
    current = int(read_register(AWP1221_CMD_GATE_REGISTER)) & 0xFFFF
    updated = awp1221_cmd_gate_write_value(current)
    if updated is None:
        return current
    write_register(AWP1221_CMD_GATE_REGISTER, updated)
    return updated
