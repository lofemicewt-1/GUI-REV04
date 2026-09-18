from __future__ import annotations

import struct
from dataclasses import dataclass


AWP1018_ADC_LEGACY_SCHEMA_VERSION = 1
AWP1018_ADC_SCHEMA_VERSION = 2
AWP1018_ADC_TWO_STAGE_FLAG = 0x80
AWP1018_ADC_DESCRIPTOR_COUNT_MASK = 0x0F
AWP1018_ADC_CONTROL_MASK = AWP1018_ADC_TWO_STAGE_FLAG | AWP1018_ADC_DESCRIPTOR_COUNT_MASK
AWP1018_ADC_MAX_DESCRIPTORS = 8
# Schema v1 register order. Keep this public alias for GUI merge reads and
# legacy payload compatibility; schema v2 uses one of the layouts below.
AWP1018_ADC_FIXED_REGISTERS = (
    0x05, 0x07, 0x0D, 0x22,
    0x0E, 0x0F, 0x10, 0x12,
    0x13, 0x14, 0x15, 0x17,
    0x18, 0x19, 0x1A, 0x1C,
    0x1D, 0x1E, 0x1F, 0x21,
    0x34,
)
AWP1018_ADC_STAGE01_REGISTERS = (
    0x05, 0x07, 0x0D, 0x22,
    0x0E, 0x0F, 0x10, 0x12,
    0x13, 0x14, 0x15, 0x17,
    0x30, 0x34,
)
AWP1018_ADC_STAGE4_REGISTERS = (
    0x05, 0x07, 0x0D, 0x22,
    0x0E, 0x0F, 0x10, 0x12,
    0x13, 0x14, 0x15, 0x17,
    0x18, 0x19, 0x1A, 0x1C,
    0x1D, 0x1E, 0x1F, 0x21,
    0x30, 0x34,
)
AWP1018_ADC_REGISTER_30_VALUE = 0x2100
AWP1018_ADC_STAGE_WAVEFORM_REGISTERS = (0x12, 0x17, 0x1C, 0x21)
AWP1018_ADC_BASE_PAYLOAD_SIZE = 2 + (len(AWP1018_ADC_FIXED_REGISTERS) * 2)


@dataclass(frozen=True)
class AWP1018RegisterWrite:
    register: int
    value: int


@dataclass(frozen=True)
class AWP1018WaveformDescriptor:
    slice_index: int
    words: tuple[int, int, int, int]


@dataclass(frozen=True)
class Awp1018AdcPresetModel:
    schema_version: int
    fixed_register_values: tuple[int, ...]
    waveform_descriptors: tuple[AWP1018WaveformDescriptor, ...]
    include_stage23: bool = True


@dataclass(frozen=True)
class Awp1018AdcWritePlan:
    register_writes: tuple[AWP1018RegisterWrite, ...]
    waveform_descriptors: tuple[AWP1018WaveformDescriptor, ...]

    @property
    def writes(self) -> tuple[AWP1018RegisterWrite, ...]:
        return self.register_writes

    @property
    def descriptors(self) -> tuple[AWP1018WaveformDescriptor, ...]:
        return self.waveform_descriptors


def required_awp1018_descriptor_slices(
    fixed_register_values: tuple[int, ...],
    *,
    registers: tuple[int, ...] = AWP1018_ADC_FIXED_REGISTERS,
    force_diff_serial0: bool = False,
) -> tuple[int, ...]:
    register_order = tuple(registers)
    values = _validate_register_values(fixed_register_values, register_order)
    by_register = dict(zip(register_order, values))

    required: list[int] = []
    if force_diff_serial0 and (by_register.get(0x34, 0) & 0x0008):
        required.extend(range(16, 20))
    for register in AWP1018_ADC_STAGE_WAVEFORM_REGISTERS:
        if register not in by_register:
            continue
        value = by_register[register]
        for shift in (0, 8):
            slice_index = (value >> shift) & 0x1F
            if slice_index == 31:
                raise ValueError(
                    "AWP1018 slice31/Serial4 terminator is not supported by ADC Stage4 presets"
                )
            if slice_index not in required:
                required.append(slice_index)

    if by_register.get(0x05, 0) & (1 << 6):
        stage_references = tuple(required)
        for slice_index in stage_references:
            serial_group = _serial_group_for_slice(slice_index)
            if serial_group is None:
                continue
            for dependency in serial_group:
                if dependency not in required:
                    required.append(dependency)

    if len(required) > AWP1018_ADC_MAX_DESCRIPTORS:
        raise ValueError(
            "AWP1018 waveform dependency closure exceeds 8 descriptors"
        )
    return tuple(required)


def build_awp1018_adc_write_plan(
    model: Awp1018AdcPresetModel,
) -> Awp1018AdcWritePlan:
    validated = _validate_model(model)
    register_order = awp1018_adc_registers_for_model(validated)
    required_slices = required_awp1018_descriptor_slices(
        validated.fixed_register_values,
        registers=register_order,
        force_diff_serial0=validated.schema_version == AWP1018_ADC_SCHEMA_VERSION,
    )
    descriptors_by_slice = {
        descriptor.slice_index: descriptor
        for descriptor in validated.waveform_descriptors
    }
    missing = [slice_index for slice_index in required_slices if slice_index not in descriptors_by_slice]
    if missing:
        formatted = ", ".join(f"slice{slice_index}" for slice_index in missing)
        raise ValueError(f"AWP1018 preset is missing waveform descriptor(s): {formatted}")

    return Awp1018AdcWritePlan(
        register_writes=tuple(
            AWP1018RegisterWrite(register, value)
            for register, value in zip(
                register_order,
                validated.fixed_register_values,
            )
        ),
        waveform_descriptors=tuple(
            descriptors_by_slice[slice_index]
            for slice_index in required_slices
        ),
    )


def pack_awp1018_adc_stage4_payload(model: Awp1018AdcPresetModel) -> bytes:
    plan = build_awp1018_adc_write_plan(model)
    validated = _validate_model(model)
    descriptor_control = len(plan.waveform_descriptors)
    if (
        validated.schema_version == AWP1018_ADC_SCHEMA_VERSION
        and not validated.include_stage23
    ):
        descriptor_control |= AWP1018_ADC_TWO_STAGE_FLAG
    encoded = bytearray(
        (
            validated.schema_version,
            descriptor_control,
        )
    )
    encoded.extend(struct.pack(
        f"<{len(plan.register_writes)}H",
        *(write.value for write in plan.register_writes),
    ))
    for descriptor in plan.waveform_descriptors:
        encoded.append(descriptor.slice_index)
        encoded.extend(struct.pack("<4H", *descriptor.words))
    return bytes(encoded)


def unpack_awp1018_adc_stage4_payload(payload: bytes) -> Awp1018AdcPresetModel:
    raw = bytes(payload)
    if len(raw) < 2:
        raise ValueError("AWP1018 ADC Stage4 payload is too short")

    schema_version = raw[0]
    descriptor_control = raw[1]
    if schema_version == AWP1018_ADC_LEGACY_SCHEMA_VERSION:
        include_stage23 = True
        descriptor_count = descriptor_control
        register_order = AWP1018_ADC_FIXED_REGISTERS
    elif schema_version == AWP1018_ADC_SCHEMA_VERSION:
        if descriptor_control & ~AWP1018_ADC_CONTROL_MASK:
            raise ValueError("AWP1018 ADC Stage4 control byte has unsupported flags")
        include_stage23 = not bool(descriptor_control & AWP1018_ADC_TWO_STAGE_FLAG)
        descriptor_count = descriptor_control & AWP1018_ADC_DESCRIPTOR_COUNT_MASK
        register_order = (
            AWP1018_ADC_STAGE4_REGISTERS
            if include_stage23
            else AWP1018_ADC_STAGE01_REGISTERS
        )
    else:
        raise ValueError("unsupported AWP1018 ADC Stage4 schema version")
    if not 1 <= descriptor_count <= AWP1018_ADC_MAX_DESCRIPTORS:
        raise ValueError("AWP1018 ADC Stage4 descriptor count must be between 1 and 8")

    base_payload_size = 2 + (len(register_order) * 2)
    expected_length = base_payload_size + (descriptor_count * 9)
    if len(raw) != expected_length:
        raise ValueError("AWP1018 ADC Stage4 payload length does not match count formula")

    fixed_register_values = struct.unpack_from(f"<{len(register_order)}H", raw, 2)
    descriptors = []
    offset = base_payload_size
    for _index in range(descriptor_count):
        descriptors.append(
            AWP1018WaveformDescriptor(
                slice_index=raw[offset],
                words=struct.unpack_from("<4H", raw, offset + 1),
            )
        )
        offset += 9

    model = Awp1018AdcPresetModel(
        schema_version=schema_version,
        fixed_register_values=fixed_register_values,
        waveform_descriptors=tuple(descriptors),
        include_stage23=include_stage23,
    )
    build_awp1018_adc_write_plan(model)
    return model


def _validate_model(model: Awp1018AdcPresetModel) -> Awp1018AdcPresetModel:
    if not isinstance(model, Awp1018AdcPresetModel):
        raise ValueError("AWP1018 ADC Stage4 payload requires a complete preset model")
    schema_version = int(model.schema_version)
    if schema_version not in (
        AWP1018_ADC_LEGACY_SCHEMA_VERSION,
        AWP1018_ADC_SCHEMA_VERSION,
    ):
        raise ValueError("unsupported AWP1018 ADC Stage4 schema version")
    if not isinstance(model.include_stage23, bool):
        raise ValueError("AWP1018 ADC Stage4 include_stage23 must be boolean")
    if schema_version == AWP1018_ADC_LEGACY_SCHEMA_VERSION and not model.include_stage23:
        raise ValueError("legacy AWP1018 ADC Stage4 payload always includes Stage2/Stage3")

    register_order = _registers_for_layout(schema_version, model.include_stage23)
    fixed_register_values = _validate_register_values(model.fixed_register_values, register_order)
    if schema_version == AWP1018_ADC_SCHEMA_VERSION:
        reg30_index = register_order.index(0x30)
        if fixed_register_values[reg30_index] != AWP1018_ADC_REGISTER_30_VALUE:
            raise ValueError("AWP1018 ADC Stage4 register 0x30 must be 0x2100")
    if not 1 <= len(model.waveform_descriptors) <= AWP1018_ADC_MAX_DESCRIPTORS:
        raise ValueError("AWP1018 ADC Stage4 descriptor count must be between 1 and 8")

    descriptors = []
    seen_slices: set[int] = set()
    for descriptor in model.waveform_descriptors:
        if not isinstance(descriptor, AWP1018WaveformDescriptor):
            raise ValueError("invalid AWP1018 waveform descriptor")
        slice_index = _u8(descriptor.slice_index, "slice_index")
        if slice_index > 30:
            raise ValueError("AWP1018 slice31/Serial4 terminator is not supported")
        if slice_index in seen_slices:
            raise ValueError("duplicate AWP1018 waveform descriptor slice")
        if len(descriptor.words) != 4:
            raise ValueError("AWP1018 waveform descriptor must have four words")
        seen_slices.add(slice_index)
        descriptors.append(
            AWP1018WaveformDescriptor(
                slice_index,
                tuple(_u16(word, "descriptor word") for word in descriptor.words),
            )
        )

    return Awp1018AdcPresetModel(
        schema_version=schema_version,
        fixed_register_values=fixed_register_values,
        waveform_descriptors=tuple(descriptors),
        include_stage23=model.include_stage23,
    )


def awp1018_adc_registers_for_model(
    model: Awp1018AdcPresetModel,
) -> tuple[int, ...]:
    if not isinstance(model, Awp1018AdcPresetModel):
        raise ValueError("AWP1018 ADC Stage4 payload requires a complete preset model")
    return _registers_for_layout(int(model.schema_version), model.include_stage23)


def _registers_for_layout(
    schema_version: int,
    include_stage23: bool,
) -> tuple[int, ...]:
    if schema_version == AWP1018_ADC_LEGACY_SCHEMA_VERSION:
        if not include_stage23:
            raise ValueError("legacy AWP1018 ADC Stage4 payload always includes Stage2/Stage3")
        return AWP1018_ADC_FIXED_REGISTERS
    if schema_version == AWP1018_ADC_SCHEMA_VERSION:
        return AWP1018_ADC_STAGE4_REGISTERS if include_stage23 else AWP1018_ADC_STAGE01_REGISTERS
    raise ValueError("unsupported AWP1018 ADC Stage4 schema version")


def _validate_fixed_register_values(values: tuple[int, ...]) -> tuple[int, ...]:
    return _validate_register_values(values, AWP1018_ADC_FIXED_REGISTERS)


def _validate_register_values(
    values: tuple[int, ...],
    registers: tuple[int, ...],
) -> tuple[int, ...]:
    try:
        normalized = tuple(values)
    except TypeError:
        raise ValueError("AWP1018 fixed register values must be a sequence") from None
    if len(normalized) != len(registers):
        raise ValueError(
            f"AWP1018 ADC Stage4 requires exactly {len(registers)} register values"
        )
    return tuple(_u16(value, "fixed register value") for value in normalized)


def _serial_group_for_slice(slice_index: int) -> tuple[int, ...] | None:
    if 16 <= slice_index <= 19:
        return tuple(range(16, 20))
    if 20 <= slice_index <= 23:
        return tuple(range(20, 24))
    if 24 <= slice_index <= 27:
        return tuple(range(24, 28))
    if 28 <= slice_index <= 30:
        return tuple(range(28, 31))
    return None


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
