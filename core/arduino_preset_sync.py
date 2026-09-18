from __future__ import annotations

import struct
from dataclasses import dataclass

from core.awp1018_adc_preset import (
    AWP1018_ADC_SCHEMA_VERSION,
    Awp1018AdcPresetModel,
    build_awp1018_adc_write_plan,
    unpack_awp1018_adc_stage4_payload,
)
from core.awp192x_button_runtime import (
    AWP192X_BUTTON_PAYLOAD_V2_SIZE,
    Awp192xSineWaveformRecipe,
    awp192x_waveform_recipe_points,
    unpack_awp192x_button_payload,
)


MAX_PRESET_SLOTS = 3
V2_PRESET_SLOT_SIZE = 128
V2_PRESET_MAGIC = 0xA192
V2_PRESET_VERSION = 2
V2_HEADER_FORMAT = "<HBBHBB"
V2_HEADER_SIZE = struct.calcsize(V2_HEADER_FORMAT)
V2_MAX_PAYLOAD_SIZE = V2_PRESET_SLOT_SIZE - V2_HEADER_SIZE - 2

PAYLOAD_TYPE_AWP192X_BUTTON_RUNTIME = 3
PAYLOAD_TYPE_AWP1221_RUNTIME = 4
PAYLOAD_TYPE_AWP1018_ADC_STAGE4 = 5

CHIP_SIGNATURES = {
    0x192A: (0x44, 0x1E, 0x5081),
    0x192B: (0x45, 0x1E, 0x5081),
    0x192C: (0x46, 0x1E, 0x5081),
    **{0x1018 + index: (0x48 + index, 0x2B, 0x1018) for index in range(8)},
}

_CRC_STRUCT = struct.Struct("<H")


@dataclass(frozen=True)
class V2Preset:
    payload_type: int
    chip_signature: int
    device_addr: int
    payload: bytes


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def signature_for_address(address: int) -> int:
    try:
        device_addr = int(address)
    except (TypeError, ValueError):
        raise ValueError("unsupported preset device address") from None

    for chip_signature, (allowed_address, _, _) in CHIP_SIGNATURES.items():
        if device_addr == allowed_address:
            return chip_signature
    raise ValueError("unsupported preset device address")


def pack_v2_preset(preset: V2Preset) -> bytes:
    payload = bytes(preset.payload)
    payload_type, chip_signature, device_addr = _validate_v2_preset(
        preset.payload_type,
        preset.chip_signature,
        preset.device_addr,
        payload,
    )
    header = struct.pack(
        V2_HEADER_FORMAT,
        V2_PRESET_MAGIC,
        V2_PRESET_VERSION,
        payload_type,
        chip_signature,
        device_addr,
        len(payload),
    )
    body = header + payload
    encoded = body + _CRC_STRUCT.pack(crc16_ccitt_false(body))
    return encoded + (b"\xFF" * (V2_PRESET_SLOT_SIZE - len(encoded)))


def unpack_v2_preset(data: bytes) -> V2Preset:
    raw = bytes(data)
    if len(raw) != V2_PRESET_SLOT_SIZE:
        raise ValueError("v2 preset record must be exactly 128 bytes")

    magic, version, payload_type, chip_signature, device_addr, payload_length = struct.unpack_from(
        V2_HEADER_FORMAT,
        raw,
    )
    if magic != V2_PRESET_MAGIC:
        raise ValueError("invalid v2 preset magic")
    if version != V2_PRESET_VERSION:
        raise ValueError("invalid v2 preset version")
    if payload_length < 1 or payload_length > V2_MAX_PAYLOAD_SIZE:
        raise ValueError("invalid v2 payload length")

    crc_offset = V2_HEADER_SIZE + payload_length
    expected_crc = _CRC_STRUCT.unpack(raw[crc_offset : crc_offset + _CRC_STRUCT.size])[0]
    body = raw[:crc_offset]
    actual_crc = crc16_ccitt_false(body)
    if actual_crc != expected_crc:
        raise ValueError(f"CRC mismatch: expected 0x{expected_crc:04X}, got 0x{actual_crc:04X}")

    payload = raw[V2_HEADER_SIZE:crc_offset]
    _validate_v2_preset(payload_type, chip_signature, device_addr, payload)
    return V2Preset(
        payload_type=payload_type,
        chip_signature=chip_signature,
        device_addr=device_addr,
        payload=payload,
    )


def format_preset_summary(raw: bytes) -> str:
    preset = unpack_v2_preset(raw)
    summary_lines = [
        "Format: v2",
        f"Target: 0x{preset.chip_signature:04X} @ 0x{preset.device_addr:02X}",
    ]
    if preset.payload_type == PAYLOAD_TYPE_AWP192X_BUTTON_RUNTIME:
        payload = unpack_awp192x_button_payload(preset.payload)
        summary_lines.append("Payload: AWP192x Button Runtime v2")
        summary_lines.append(
            f"ADC events: WAIT_PRESS=0x{payload.adc_event_wait_press:04X}, "
            f"WAIT_RELEASE=0x{payload.adc_event_wait_release:04X}"
        )
        summary_lines.append(f"Waveforms: {len(payload.waveforms)}")
        for recipe in payload.waveforms:
            waveform_name = "SIN" if isinstance(recipe, Awp192xSineWaveformRecipe) else "SINC"
            summary_lines.append(
                f"- {waveform_name}: Slice {recipe.slice_index}, "
                f"{awp192x_waveform_recipe_points(recipe)} points"
            )
    elif preset.payload_type == PAYLOAD_TYPE_AWP1018_ADC_STAGE4:
        model = _unpack_awp1018_schema2_payload(preset.payload)
        plan = build_awp1018_adc_write_plan(model)
        summary_lines.append("Payload: AWP1018 ADC Stage4 schema2")
        summary_lines.append(
            f"Registers: {len(plan.register_writes)} fixed register values "
            f"({'Stage0-Stage3' if model.include_stage23 else 'Stage0-Stage1 only'})"
        )
        summary_lines.extend(
            f"0x{write.register:02X}=0x{write.value:04X}"
            for write in plan.register_writes
        )
        summary_lines.extend(
            f"Slice {descriptor.slice_index}: "
            + " ".join(f"0x{word:04X}" for word in descriptor.words)
            for descriptor in plan.waveform_descriptors
        )
    else:
        raise ValueError("CFG_UNSUPPORTED: Type-4 is reserved for a later phase")
    return "\n".join(summary_lines)


def decode_awp1018_board_preset_page_settings(
    raw: bytes,
) -> tuple[int, dict[int, int], dict[int, tuple[int, int, int, int]]]:
    """Return schema2 AWP1018 register and descriptor state stored in a board slot."""
    preset = unpack_v2_preset(raw)
    if preset.payload_type != PAYLOAD_TYPE_AWP1018_ADC_STAGE4:
        raise ValueError("CFG_UNSUPPORTED: board preset does not contain AWP1018 schema2 settings")

    plan = build_awp1018_adc_write_plan(_unpack_awp1018_schema2_payload(preset.payload))
    return (
        preset.device_addr,
        {int(write.register): int(write.value) & 0xFFFF for write in plan.register_writes},
        {
            int(descriptor.slice_index): tuple(int(word) & 0x0FFF for word in descriptor.words)
            for descriptor in plan.waveform_descriptors
        },
    )


def chunk_payload_hex(data: bytes, max_chunk_bytes: int = 11) -> list[tuple[int, str]]:
    chunk_size = int(max_chunk_bytes)
    if chunk_size < 1 or chunk_size > 11:
        raise ValueError("max_chunk_bytes must be between 1 and 11")
    raw = bytes(data)
    return [
        (offset, raw[offset : offset + chunk_size].hex().upper())
        for offset in range(0, len(raw), chunk_size)
    ]


def _validate_v2_preset(
    payload_type: int,
    chip_signature: int,
    device_addr: int,
    payload: bytes,
) -> tuple[int, int, int]:
    validated_payload_type = _u8(payload_type, "payload_type")
    if validated_payload_type == PAYLOAD_TYPE_AWP1221_RUNTIME:
        raise ValueError("CFG_UNSUPPORTED: Type-4 is reserved for a later phase")
    if validated_payload_type not in (
        PAYLOAD_TYPE_AWP192X_BUTTON_RUNTIME,
        PAYLOAD_TYPE_AWP1018_ADC_STAGE4,
    ):
        raise ValueError("CFG_UNSUPPORTED: only Type-3 v2 and Type-5 schema2 are supported")

    validated_signature = _u16(chip_signature, "chip_signature")
    validated_address = _u8(device_addr, "device_addr")
    if signature_for_address(validated_address) != validated_signature:
        raise ValueError("unsupported preset device address")

    if validated_payload_type == PAYLOAD_TYPE_AWP192X_BUTTON_RUNTIME:
        if validated_signature not in (0x192A, 0x192B, 0x192C):
            raise ValueError("AWP192x Button Runtime payload requires an AWP192x signature")
        if len(payload) != AWP192X_BUTTON_PAYLOAD_V2_SIZE:
            raise ValueError(
                "AWP192x Button Runtime v2 payload must be exactly "
                f"{AWP192X_BUTTON_PAYLOAD_V2_SIZE} bytes"
            )
        unpack_awp192x_button_payload(payload)
    else:
        if not 0x1018 <= validated_signature <= 0x101F:
            raise ValueError("AWP1018 ADC Stage4 payload requires an AWP1018 signature")
        if (validated_signature & 0x0F) != (validated_address & 0x0F):
            raise ValueError("unsupported preset device address")
        _unpack_awp1018_schema2_payload(payload)
    return validated_payload_type, validated_signature, validated_address


def _unpack_awp1018_schema2_payload(payload: bytes) -> Awp1018AdcPresetModel:
    model = unpack_awp1018_adc_stage4_payload(payload)
    if model.schema_version != AWP1018_ADC_SCHEMA_VERSION:
        raise ValueError("CFG_UNSUPPORTED: AWP1018 Type-5 requires schema2")
    return model


def _u8(value: int, name: str) -> int:
    integer = int(value)
    if integer < 0 or integer > 0xFF:
        raise ValueError(f"{name} must fit uint8")
    return integer


def _u16(value: int, name: str) -> int:
    integer = int(value)
    if integer < 0 or integer > 0xFFFF:
        raise ValueError(f"{name} must fit uint16")
    return integer
