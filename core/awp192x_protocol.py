"""AWP192x command construction through the shared REV04 register protocol."""

from __future__ import annotations

from core import base_tool


class AWP192XProtocol:
    """Small explicit-address facade for callers that need raw AWP192x frames."""

    def build_write_values_command(
        self,
        device_addr: int,
        reg_addr: int,
        values: list[int] | tuple[int, ...],
        *,
        bits: int = 16,
    ) -> str:
        return base_tool.build_write_register_values_command(
            device_addr,
            reg_addr,
            values,
            bits=bits,
        )

    def build_read_values_command(
        self,
        device_addr: int,
        reg_addr: int,
        *,
        bits: int = 16,
        count: int = 1,
    ) -> str:
        return base_tool.build_read_register_values_command(
            device_addr,
            reg_addr,
            bits=bits,
            count=count,
        )

    def build_write_value_command(
        self,
        device_addr: int,
        reg_addr: int,
        value: int,
        *,
        bits: int = 16,
    ) -> str:
        return base_tool.build_write_register_value_command(
            device_addr,
            reg_addr,
            value,
            bits=bits,
        )

    def build_read_value_command(
        self,
        device_addr: int,
        reg_addr: int,
        *,
        bits: int = 16,
    ) -> str:
        return base_tool.build_read_register_value_command(
            device_addr,
            reg_addr,
            bits=bits,
        )
