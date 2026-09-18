from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from core.base_tool import (
    RegisterScriptOperation,
    format_register_script,
    parse_register_script,
    u8,
    u16,
)


AWP1018_REGISTER_SCRIPT_FORMAT = "AWP1018_REGISTER_SCRIPT"
AWP1018_REGISTER_SCRIPT_VERSION = 1
AWP1018_REGISTER_SCRIPT_CONFIG_ORDER = (
    0x04,
    0x05,
    0x06,
    0x07,
    0x08,
    0x0D,
    0x22,
    0x26,
    0x27,
    0x0E,
    0x0F,
    0x10,
    0x12,
    0x13,
    0x14,
    0x15,
    0x17,
    0x18,
    0x19,
    0x1A,
    0x1C,
    0x1D,
    0x1E,
    0x1F,
    0x21,
    0x34,
)
AWP1018_REGISTER_SCRIPT_OPTIONAL_SERIAL4_REGISTERS = (0x29, 0x2A)
AWP1018_REGISTER_SCRIPT_ALLOWED_REGISTERS = frozenset(
    (0x00, 0x28)
    + AWP1018_REGISTER_SCRIPT_CONFIG_ORDER
    + AWP1018_REGISTER_SCRIPT_OPTIONAL_SERIAL4_REGISTERS
)
AWP1018_REGISTER_SCRIPT_SNAPSHOT_REGISTERS = tuple(dict.fromkeys(
    AWP1018_REGISTER_SCRIPT_CONFIG_ORDER
    + (0x28,)
    + AWP1018_REGISTER_SCRIPT_OPTIONAL_SERIAL4_REGISTERS
))

_METADATA_PATTERN = re.compile(
    r"^\s*#\s*(Format|Version|Mode|Device)\s*:\s*(.*?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LegacyJsonPreset:
    version: int
    mode: str
    settings: dict
    chip_address: object = None


@dataclass(frozen=True)
class RegisterScriptPreset:
    mode: str
    device_addr: int
    operations: tuple[RegisterScriptOperation, ...]


def build_awp1018_register_script(
    device_addr: int,
    *,
    mode: str,
    register_values: Mapping[int, int],
    descriptors: Mapping[int, Sequence[int]],
    sram_address_base: int,
) -> list[RegisterScriptOperation]:
    """Build one deterministic, write-only AWP1018 page-configuration script."""
    address = u8(device_addr, name="device_addr")
    if not 0x48 <= address <= 0x4F:
        raise ValueError("AWP1018 device address is outside the 0x48..0x4F address range")
    normalized_mode = _normalize_mode(mode)

    normalized_registers = {
        u8(register, name="register"): u16(value, name="register value")
        for register, value in register_values.items()
    }
    allowed_model_registers = set(AWP1018_REGISTER_SCRIPT_CONFIG_ORDER)
    allowed_model_registers.update(AWP1018_REGISTER_SCRIPT_OPTIONAL_SERIAL4_REGISTERS)
    unknown = sorted(set(normalized_registers) - allowed_model_registers)
    if unknown:
        raise ValueError(f"unsupported AWP1018 configuration register 0x{unknown[0]:02X}")
    missing = [
        register
        for register in AWP1018_REGISTER_SCRIPT_CONFIG_ORDER
        if register not in normalized_registers
    ]
    if missing:
        raise ValueError(f"missing AWP1018 configuration register 0x{missing[0]:02X}")

    has_serial4 = [
        register in normalized_registers
        for register in AWP1018_REGISTER_SCRIPT_OPTIONAL_SERIAL4_REGISTERS
    ]
    if any(has_serial4) and not all(has_serial4):
        raise ValueError("AWP1018 Serial4 registers 0x29 and 0x2A must be provided together")
    if all(has_serial4) and normalized_mode != "diff":
        raise ValueError("AWP1018 Serial4 registers are only valid in diff mode")

    normalized_descriptors: dict[int, tuple[int, int, int, int]] = {}
    for slice_index, raw_words in descriptors.items():
        index = int(slice_index)
        if not 0 <= index <= 30:
            raise ValueError("AWP1018 descriptor slice must be between 0 and 30")
        words = tuple(int(word) for word in raw_words)
        if len(words) != 4 or any(word < 0 or word > 0x0FFF for word in words):
            raise ValueError("AWP1018 descriptor must contain four 12-bit words")
        normalized_descriptors[index] = words

    sram_base = u16(sram_address_base, name="sram_address_base")
    final_mode = normalized_registers[0x05]
    operations = [
        RegisterScriptOperation(
            address,
            "WRITE",
            0x05,
            (final_mode & ~0x0003) | 0x0003,
            "Enter SRAM access mode",
        )
    ]
    for slice_index in sorted(normalized_descriptors):
        operations.append(
            RegisterScriptOperation(
                address,
                "WRITE",
                0x28,
                (sram_base & ~0x03FF) | ((slice_index * 4) & 0x03FF),
                f"Select slice{slice_index} descriptor",
            )
        )
        for word_index, word in enumerate(normalized_descriptors[slice_index]):
            operations.append(
                RegisterScriptOperation(
                    address,
                    "WRITE",
                    0x00,
                    word,
                    f"Write slice{slice_index} descriptor word{word_index}",
                )
            )
    operations.extend((
        RegisterScriptOperation(
            address,
            "WRITE",
            0x28,
            sram_base,
            "Restore SRAM address",
        ),
        RegisterScriptOperation(
            address,
            "WRITE",
            0x05,
            final_mode,
            "Restore target operating mode",
        ),
    ))

    comments = {
        0x04: "Configure global interrupt routing",
        0x06: "Configure Zero Time",
        0x07: "Configure channels and return bits",
        0x08: "Configure nINT mask",
        0x0D: "Configure group start Stages",
        0x22: "Configure ADC debounce and sample update",
        0x26: "Configure waveform playback path",
        0x27: "Configure DAC sample rate",
        0x29: "Configure Diff Serial4 segments 0-2",
        0x2A: "Configure Diff Serial4 segments 3-5",
        0x34: "Select AWP1018 Normal or Diff mode",
    }
    for register in AWP1018_REGISTER_SCRIPT_CONFIG_ORDER:
        if register == 0x05:
            continue
        if register == 0x34 and all(has_serial4):
            for serial_register in AWP1018_REGISTER_SCRIPT_OPTIONAL_SERIAL4_REGISTERS:
                operations.append(
                    RegisterScriptOperation(
                        address,
                        "WRITE",
                        serial_register,
                        normalized_registers[serial_register],
                        comments[serial_register],
                    )
                )
        operations.append(
            RegisterScriptOperation(
                address,
                "WRITE",
                register,
                normalized_registers[register],
                comments.get(register, f"Configure register 0x{register:02X}"),
            )
        )

    validate_awp1018_register_script(
        RegisterScriptPreset(normalized_mode, address, tuple(operations))
    )
    return operations


def format_awp1018_register_script(
    mode: str,
    operations: Sequence[RegisterScriptOperation],
) -> str:
    operation_list = list(operations)
    if not operation_list:
        raise ValueError("AWP1018 register script is empty")
    device_addresses = {operation.device_addr for operation in operation_list}
    if len(device_addresses) != 1:
        raise ValueError("AWP1018 register script has an address mismatch")
    address = next(iter(device_addresses))
    preset = validate_awp1018_register_script(
        RegisterScriptPreset(_normalize_mode(mode), address, tuple(operation_list))
    )
    metadata = (
        f"# Format: {AWP1018_REGISTER_SCRIPT_FORMAT}\n"
        f"# Version: {AWP1018_REGISTER_SCRIPT_VERSION}\n"
        f"# Mode: {preset.mode}\n"
        f"# Device: 0x{preset.device_addr:02X}\n"
    )
    content = metadata + format_register_script(operation_list)
    content.encode("ascii")
    return content


def parse_awp1018_user_preset(path: str | Path, text: str) -> LegacyJsonPreset | RegisterScriptPreset:
    suffix = Path(path).suffix.lower()
    content = str(text).lstrip("\ufeff")
    if suffix == ".json":
        return _parse_legacy_json_preset(content)
    if suffix == ".txt":
        return _parse_register_script_preset(content)
    raise ValueError("unsupported AWP1018 user preset format")


def validate_awp1018_register_script(preset: RegisterScriptPreset) -> RegisterScriptPreset:
    mode = _normalize_mode(preset.mode)
    address = u8(preset.device_addr, name="device_addr")
    if not 0x48 <= address <= 0x4F:
        raise ValueError("AWP1018 register script device address is outside the address range")

    operations = tuple(preset.operations)
    if not operations:
        raise ValueError("AWP1018 register script is empty")
    mode_values: list[int] = []
    for operation in operations:
        if operation.device_addr != address:
            raise ValueError("AWP1018 register script address mismatch")
        if operation.action not in {"READ", "WRITE"}:
            raise ValueError("AWP1018 register script operation must be READ or WRITE")
        if operation.reg_addr not in AWP1018_REGISTER_SCRIPT_ALLOWED_REGISTERS:
            raise ValueError(f"AWP1018 register 0x{operation.reg_addr:02X} is not allowed")
        if operation.action == "WRITE" and operation.value is None:
            raise ValueError("AWP1018 WRITE operation requires a register value")
        if operation.action == "READ" and operation.value is not None:
            raise ValueError("AWP1018 READ operation must use -- as its value")
        if operation.action == "WRITE" and operation.reg_addr == 0x34:
            mode_values.append(int(operation.value or 0))

    expected_diff_bit = mode == "diff"
    if not mode_values or any(bool(value & 0x0008) != expected_diff_bit for value in mode_values):
        raise ValueError("AWP1018 register script mode does not match register 0x34 bit3")
    return RegisterScriptPreset(mode, address, operations)


def decode_awp1018_register_script_page_settings(
    preset: RegisterScriptPreset,
) -> tuple[dict[int, int], dict[int, tuple[int, int, int, int]]]:
    """Decode the deterministic page state represented by a validated TXT script."""
    validated = validate_awp1018_register_script(preset)
    register_values: dict[int, int] = {}
    descriptor_words: dict[int, dict[int, int]] = {}
    sram_mode = False
    sram_address: int | None = None

    for operation in validated.operations:
        if operation.action != "WRITE":
            continue
        register = int(operation.reg_addr)
        value = int(operation.value or 0) & 0xFFFF
        if register == 0x05:
            register_values[register] = value
            sram_mode = (value & 0x0003) == 0x0003
            continue
        if register == 0x28:
            sram_address = value & 0x03FF
            continue
        if register == 0x00:
            if sram_mode and sram_address is not None and sram_address < 31 * 4:
                slice_index, word_index = divmod(sram_address, 4)
                descriptor_words.setdefault(slice_index, {})[word_index] = value & 0x0FFF
            if sram_address is not None:
                sram_address = (sram_address + 1) & 0x03FF
            continue
        register_values[register] = value

    descriptors = {
        slice_index: tuple(words[word_index] for word_index in range(4))
        for slice_index, words in descriptor_words.items()
        if all(word_index in words for word_index in range(4))
    }
    return register_values, descriptors


def _parse_legacy_json_preset(text: str) -> LegacyJsonPreset:
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid AWP1018 JSON preset: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid AWP1018 JSON preset: top level must be an object")
    version = payload.get("version")
    mode = payload.get("mode")
    settings = payload.get("settings")
    if (
        payload.get("format") != "AWP1018_SETTINGS"
        or version not in (1, 2, 3)
        or mode not in {"normal", "diff"}
        or not isinstance(settings, dict)
    ):
        raise ValueError("invalid AWP1018 JSON preset metadata")
    return LegacyJsonPreset(int(version), str(mode), settings, payload.get("chip_address"))


def _parse_register_script_preset(text: str) -> RegisterScriptPreset:
    metadata: dict[str, str] = {}
    for raw_line in text.splitlines():
        match = _METADATA_PATTERN.match(raw_line)
        if match is None:
            continue
        key = match.group(1).lower()
        if key in metadata:
            raise ValueError(f"invalid AWP1018 TXT metadata: duplicate {key}")
        metadata[key] = match.group(2).strip()

    missing = [key for key in ("format", "version", "mode", "device") if key not in metadata]
    if missing:
        raise ValueError(f"invalid AWP1018 TXT metadata: missing {', '.join(missing)}")
    if metadata["format"] != AWP1018_REGISTER_SCRIPT_FORMAT:
        raise ValueError("invalid AWP1018 TXT metadata: format")
    try:
        version = int(metadata["version"], 0)
        address = int(metadata["device"], 0)
    except ValueError as exc:
        raise ValueError("invalid AWP1018 TXT metadata: version or device") from exc
    if version != AWP1018_REGISTER_SCRIPT_VERSION:
        raise ValueError("invalid AWP1018 TXT metadata: version")
    mode = _normalize_mode(metadata["mode"])
    try:
        operations = tuple(parse_register_script(text))
    except ValueError as exc:
        raise ValueError(f"invalid AWP1018 TXT script: {exc}") from exc
    return validate_awp1018_register_script(RegisterScriptPreset(mode, address, operations))


def _normalize_mode(mode: object) -> str:
    normalized = str(mode).strip().lower()
    if normalized not in {"normal", "diff"}:
        raise ValueError("invalid AWP1018 mode")
    return normalized
