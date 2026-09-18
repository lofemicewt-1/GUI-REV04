from __future__ import annotations

import math
import struct
from dataclasses import dataclass


AWP192X_BUTTON_PAYLOAD_V2_VERSION = 2
AWP192X_GENERATOR_SINC = 1
AWP192X_GENERATOR_SINC_VERSION = 1
AWP192X_GENERATOR_SINE = 2
AWP192X_GENERATOR_SINE_VERSION = 1

AWP192X_BUTTON_RUNTIME_FLAG_SERIAL_CONFIG = 0x01

AWP192X_VOLTAGE_RANGE_MV = 95_000
AWP192X_SRAM_MAX = 0x03FF
AWP192X_MAX_WAVEFORM_POINTS = 1020
AWP192X_DAC_POS_ZERO = 0x0800
AWP192X_DAC_NEG_ZERO = 0x07FF
AWP192X_BUTTON_DEFAULT_RELEASE_TIMEOUT_US = 3_000_000
AWP192X_BUTTON_MAX_WAVEFORMS = 8

# The only supported payload stores eight compact 11-byte SINC/SIN recipes:
# 4 uint8, 13 uint16, followed by eight recipe slots.
AWP192X_BUTTON_PAYLOAD_V2_BASE_FORMAT = "<BBBB" + ("H" * 13)
_AWP192X_BUTTON_PAYLOAD_V2_BASE_STRUCT = struct.Struct(
    AWP192X_BUTTON_PAYLOAD_V2_BASE_FORMAT
)
_AWP192X_WAVEFORM_RECIPE_V2_STRUCT = struct.Struct("<BHII")
AWP192X_WAVEFORM_RECIPE_V2_SIZE = _AWP192X_WAVEFORM_RECIPE_V2_STRUCT.size
AWP192X_BUTTON_PAYLOAD_V2_SIZE = (
    _AWP192X_BUTTON_PAYLOAD_V2_BASE_STRUCT.size
    + AWP192X_BUTTON_MAX_WAVEFORMS * AWP192X_WAVEFORM_RECIPE_V2_SIZE
)


@dataclass(frozen=True)
class Awp192xWaveformRecipe:
    generator_type: int
    generator_version: int
    slice_index: int
    start_addr: int
    points1: int
    points2: int
    points3: int
    positive_mv: int
    negative_mv: int


@dataclass(frozen=True)
class Awp192xSineWaveformRecipe:
    slice_index: int
    start_addr: int
    points: int
    start_phase_deg: int
    end_phase_deg: int
    amplitude_mv: int
    offset_mv: int


@dataclass(frozen=True)
class Awp192xButtonPayloadV2:
    payload_version: int
    flags: int
    max_output_reg: int
    duration_reg: int
    press_threshold_reg: int
    release_threshold_reg: int
    adc_filter_reg: int
    adc_event_wait_press: int
    press_action_base: int
    release_action_base: int
    manual_play_reg: int
    serial_config_0x17: int
    serial_config_0x18: int
    serial_config_0x19: int
    serial_config_0x1A: int
    waveforms: tuple[Awp192xWaveformRecipe | Awp192xSineWaveformRecipe, ...]

    @property
    def adc_output_reg(self) -> int:
        return 0x8000

    @property
    def d2_config_reg(self) -> int:
        return 0x4000

    @property
    def adc_event_wait_release(self) -> int:
        return derive_wait_release_adc_event(self.adc_event_wait_press)

    @property
    def release_timeout_us(self) -> int:
        return AWP192X_BUTTON_DEFAULT_RELEASE_TIMEOUT_US

    @property
    def waveform_recipe(self) -> Awp192xWaveformRecipe | Awp192xSineWaveformRecipe:
        return self.waveforms[0]


def derive_wait_release_adc_event(wait_press: int) -> int:
    value = _u16(wait_press, "wait_press")
    if value & ~0x0300 != 0x0021:
        raise ValueError("wait_press must use the AWP192x Button WAIT_PRESS event bits")
    return (value & 0x0300) | 0x0009


def pack_awp192x_button_payload(payload: Awp192xButtonPayloadV2) -> bytes:
    validated = _validate_awp192x_button_payload_v2(payload)
    base = _AWP192X_BUTTON_PAYLOAD_V2_BASE_STRUCT.pack(
        validated.payload_version,
        validated.flags,
        len(validated.waveforms),
        0,
        validated.max_output_reg,
        validated.duration_reg,
        validated.press_threshold_reg,
        validated.release_threshold_reg,
        validated.adc_filter_reg,
        validated.adc_event_wait_press,
        validated.press_action_base,
        validated.release_action_base,
        validated.manual_play_reg,
        validated.serial_config_0x17,
        validated.serial_config_0x18,
        validated.serial_config_0x19,
        validated.serial_config_0x1A,
    )
    recipes = b"".join(
        _pack_awp192x_waveform_recipe_v2(recipe)
        for recipe in validated.waveforms
    )
    unused = AWP192X_BUTTON_MAX_WAVEFORMS - len(validated.waveforms)
    return base + recipes + (b"\xFF" * (unused * AWP192X_WAVEFORM_RECIPE_V2_SIZE))


def unpack_awp192x_button_payload(raw: bytes) -> Awp192xButtonPayloadV2:
    data = bytes(raw)
    if not data:
        raise ValueError("AWP192x Button payload is empty")
    if data[0] != AWP192X_BUTTON_PAYLOAD_V2_VERSION:
        raise ValueError("unsupported AWP192x Button payload version")
    if len(data) != AWP192X_BUTTON_PAYLOAD_V2_SIZE:
        raise ValueError(
            f"AWP192x Button v2 payload must be exactly "
            f"{AWP192X_BUTTON_PAYLOAD_V2_SIZE} bytes"
        )
    unpacked = _AWP192X_BUTTON_PAYLOAD_V2_BASE_STRUCT.unpack_from(data)
    payload_version, flags, waveform_count, reserved = unpacked[:4]
    if reserved != 0:
        raise ValueError("AWP192x Button v2 reserved byte must be zero")
    if not 1 <= waveform_count <= AWP192X_BUTTON_MAX_WAVEFORMS:
        raise ValueError("AWP192x Button v2 waveform count must be 1-8")

    recipe_offset = _AWP192X_BUTTON_PAYLOAD_V2_BASE_STRUCT.size
    waveforms = tuple(
        _unpack_awp192x_waveform_recipe_v2(
            data[
                recipe_offset + index * AWP192X_WAVEFORM_RECIPE_V2_SIZE :
                recipe_offset + (index + 1) * AWP192X_WAVEFORM_RECIPE_V2_SIZE
            ]
        )
        for index in range(waveform_count)
    )
    unused = data[
        recipe_offset + waveform_count * AWP192X_WAVEFORM_RECIPE_V2_SIZE :
    ]
    if any(value != 0xFF for value in unused):
        raise ValueError("AWP192x Button v2 unused waveform slots must be 0xFF")

    values = unpacked[4:]
    return _validate_awp192x_button_payload_v2(
        Awp192xButtonPayloadV2(
            payload_version=payload_version,
            flags=flags,
            max_output_reg=values[0],
            duration_reg=values[1],
            press_threshold_reg=values[2],
            release_threshold_reg=values[3],
            adc_filter_reg=values[4],
            adc_event_wait_press=values[5],
            press_action_base=values[6],
            release_action_base=values[7],
            manual_play_reg=values[8],
            serial_config_0x17=values[9],
            serial_config_0x18=values[10],
            serial_config_0x19=values[11],
            serial_config_0x1A=values[12],
            waveforms=waveforms,
        )
    )


def generate_awp192x_sinc_points(recipe: Awp192xWaveformRecipe) -> tuple[int, ...]:
    validated = _validate_awp192x_waveform_recipe(recipe)
    total = validated.points1 + validated.points2 + validated.points3
    return tuple(
        _map_awp192x_mv_to_dac(_awp192x_sinc_point_mv(validated, index))
        for index in range(total)
    )


def generate_awp192x_waveform_points(
    recipe: Awp192xWaveformRecipe | Awp192xSineWaveformRecipe,
) -> tuple[int, ...]:
    if isinstance(recipe, Awp192xWaveformRecipe):
        return generate_awp192x_sinc_points(recipe)
    validated = _validate_awp192x_sine_waveform_recipe(recipe)
    phase_span = validated.end_phase_deg - validated.start_phase_deg
    values: list[int] = []
    for index in range(validated.points):
        phase_deg = validated.start_phase_deg
        if validated.points > 1:
            phase_deg += phase_span * index / (validated.points - 1)
        millivolts = _round_half_away_from_zero(
            math.cos(math.radians(phase_deg - 90.0)) * validated.amplitude_mv
            + validated.offset_mv
        )
        values.append(_map_awp192x_mv_to_dac(millivolts))
    return tuple(values)


def awp192x_waveform_recipe_points(
    recipe: Awp192xWaveformRecipe | Awp192xSineWaveformRecipe,
) -> int:
    if isinstance(recipe, Awp192xWaveformRecipe):
        validated = _validate_awp192x_waveform_recipe(recipe)
        return validated.points1 + validated.points2 + validated.points3
    return _validate_awp192x_sine_waveform_recipe(recipe).points


def required_awp192x_button_waveform_slices(
    *,
    press_action_base: int,
    release_action_base: int,
    manual_play_reg: int,
    serial_config_0x17: int,
    serial_config_0x18: int,
    serial_config_0x19: int,
    serial_config_0x1A: int,
) -> tuple[int, ...]:
    serial_values = (
        _u16(serial_config_0x17, "serial_config_0x17"),
        _u16(serial_config_0x18, "serial_config_0x18"),
        _u16(serial_config_0x19, "serial_config_0x19"),
        _u16(serial_config_0x1A, "serial_config_0x1A"),
    )
    serial_slices: list[int] = []
    stopped = False
    for value in serial_values:
        for shift in (3, 11):
            slice_index = (value >> shift) & 0x1F
            if slice_index >= 0x1E:
                stopped = True
                break
            if slice_index not in serial_slices:
                serial_slices.append(slice_index)
        if stopped:
            break

    required: list[int] = []
    for action in (press_action_base, release_action_base, manual_play_reg):
        slice_index = int(action) & 0x1F
        selected = serial_slices if slice_index == 0x1E else [slice_index]
        for item in selected:
            if item < 0x1E and item not in required:
                required.append(item)
    return tuple(required)


def _pack_awp192x_waveform_recipe_v2(
    recipe: Awp192xWaveformRecipe | Awp192xSineWaveformRecipe,
) -> bytes:
    if isinstance(recipe, Awp192xWaveformRecipe):
        validated = _validate_awp192x_waveform_recipe(recipe)
        kind = AWP192X_GENERATOR_SINC
        params = (
            validated.points1
            | (validated.points2 << 10)
            | (validated.points3 << 20)
            | (validated.positive_mv << 30)
            | ((-validated.negative_mv) << 47)
        )
    elif isinstance(recipe, Awp192xSineWaveformRecipe):
        validated = _validate_awp192x_sine_waveform_recipe(recipe)
        kind = AWP192X_GENERATOR_SINE
        phase_span = validated.end_phase_deg - validated.start_phase_deg
        params = (
            validated.points
            | (validated.start_phase_deg << 10)
            | ((phase_span & 0x03FF) << 19)
            | (validated.amplitude_mv << 29)
            | ((validated.offset_mv & 0x03FFFF) << 46)
        )
    else:
        raise ValueError("unsupported AWP192x power-on waveform recipe")

    kind_and_slice = (kind << 5) | validated.slice_index
    return _AWP192X_WAVEFORM_RECIPE_V2_STRUCT.pack(
        kind_and_slice,
        validated.start_addr,
        params & 0xFFFFFFFF,
        (params >> 32) & 0xFFFFFFFF,
    )


def _unpack_awp192x_waveform_recipe_v2(
    raw: bytes,
) -> Awp192xWaveformRecipe | Awp192xSineWaveformRecipe:
    if len(raw) != AWP192X_WAVEFORM_RECIPE_V2_SIZE:
        raise ValueError("AWP192x waveform recipe v2 has an invalid length")
    kind_and_slice, start_addr, params_low, params_high = (
        _AWP192X_WAVEFORM_RECIPE_V2_STRUCT.unpack(raw)
    )
    kind = kind_and_slice >> 5
    slice_index = kind_and_slice & 0x1F
    params = params_low | (params_high << 32)
    if kind == AWP192X_GENERATOR_SINC:
        return _validate_awp192x_waveform_recipe(
            Awp192xWaveformRecipe(
                generator_type=AWP192X_GENERATOR_SINC,
                generator_version=AWP192X_GENERATOR_SINC_VERSION,
                slice_index=slice_index,
                start_addr=start_addr,
                points1=params & 0x03FF,
                points2=(params >> 10) & 0x03FF,
                points3=(params >> 20) & 0x03FF,
                positive_mv=(params >> 30) & 0x1FFFF,
                negative_mv=-((params >> 47) & 0x1FFFF),
            )
        )
    if kind == AWP192X_GENERATOR_SINE:
        start_phase_deg = (params >> 10) & 0x01FF
        phase_span = _sign_extend((params >> 19) & 0x03FF, 10)
        return _validate_awp192x_sine_waveform_recipe(
            Awp192xSineWaveformRecipe(
                slice_index=slice_index,
                start_addr=start_addr,
                points=params & 0x03FF,
                start_phase_deg=start_phase_deg,
                end_phase_deg=start_phase_deg + phase_span,
                amplitude_mv=(params >> 29) & 0x1FFFF,
                offset_mv=_sign_extend((params >> 46) & 0x03FFFF, 18),
            )
        )
    raise ValueError(f"unsupported AWP192x waveform recipe type {kind}")


def _awp192x_sinc_point_mv(recipe: Awp192xWaveformRecipe, index: int) -> int:
    if index < recipe.points1:
        return _smooth_segment_mv(0, recipe.positive_mv, index, recipe.points1)
    index -= recipe.points1
    if index < recipe.points2:
        return _smooth_segment_mv(recipe.positive_mv, recipe.negative_mv, index, recipe.points2)
    index -= recipe.points2
    return _smooth_segment_mv(recipe.negative_mv, 0, index, recipe.points3)


def _smooth_segment_mv(start_mv: int, end_mv: int, index: int, count: int) -> int:
    if count <= 1:
        return end_mv
    t = index / (count - 1)
    eased = (1.0 - math.cos(math.pi * t)) / 2.0
    return _round_half_away_from_zero(start_mv + ((end_mv - start_mv) * eased))


def _round_half_away_from_zero(value: float) -> int:
    return int(value + 0.5) if value >= 0 else int(value - 0.5)


def _map_awp192x_mv_to_dac(millivolts: int) -> int:
    mv = max(-AWP192X_VOLTAGE_RANGE_MV, min(AWP192X_VOLTAGE_RANGE_MV, int(millivolts)))
    rounding = AWP192X_VOLTAGE_RANGE_MV // 2
    if mv >= 0:
        scaled = (mv * (AWP192X_DAC_POS_ZERO - 1) + rounding) // AWP192X_VOLTAGE_RANGE_MV
        return AWP192X_DAC_POS_ZERO + scaled
    scaled = ((-mv) * AWP192X_DAC_NEG_ZERO + rounding) // AWP192X_VOLTAGE_RANGE_MV
    return AWP192X_DAC_NEG_ZERO - scaled


def _validate_awp192x_button_payload_v2(
    payload: Awp192xButtonPayloadV2,
) -> Awp192xButtonPayloadV2:
    if not isinstance(payload, Awp192xButtonPayloadV2):
        raise ValueError("AWP192x Button v2 payload has an invalid type")
    payload_version = _u8(payload.payload_version, "payload_version")
    if payload_version != AWP192X_BUTTON_PAYLOAD_V2_VERSION:
        raise ValueError("unsupported AWP192x Button v2 payload version")

    flags = _u8(payload.flags, "flags")
    if flags & ~AWP192X_BUTTON_RUNTIME_FLAG_SERIAL_CONFIG:
        raise ValueError("AWP192x Button v2 supports only the serial-config flag")

    max_output_reg = _u16(payload.max_output_reg, "max_output_reg")
    if not 0x0002 <= max_output_reg <= 0x007E or (max_output_reg - 0x0002) % 4:
        raise ValueError("max_output_reg is not an AWP192x Button voltage option")
    duration_reg = _u16(payload.duration_reg, "duration_reg")
    if duration_reg > 0xFFFE:
        raise ValueError("duration_reg must be 0-65534")

    press_threshold_reg = _u16(payload.press_threshold_reg, "press_threshold_reg")
    release_threshold_reg = _u16(payload.release_threshold_reg, "release_threshold_reg")
    if press_threshold_reg > 0x0FFF or release_threshold_reg > 0x0FFF:
        raise ValueError("AWP192x ADC thresholds must be 12-bit values")

    adc_filter_reg = _u16(payload.adc_filter_reg, "adc_filter_reg")
    if adc_filter_reg & ~0x00FF or ((adc_filter_reg >> 4) & 0x0F) > 5 or (adc_filter_reg & 0x0F) > 0x0A:
        raise ValueError("adc_filter_reg contains an unsupported filter setting")
    wait_press = _u16(payload.adc_event_wait_press, "adc_event_wait_press")
    derive_wait_release_adc_event(wait_press)

    press_action_base = _masked_u16(payload.press_action_base, "press_action_base", 0x3FFF)
    release_action_base = _masked_u16(payload.release_action_base, "release_action_base", 0x3FFF)
    manual_play_reg = _masked_u16(payload.manual_play_reg, "manual_play_reg", 0x7FFF)
    serial_values = tuple(
        _u16(value, name)
        for value, name in (
            (payload.serial_config_0x17, "serial_config_0x17"),
            (payload.serial_config_0x18, "serial_config_0x18"),
            (payload.serial_config_0x19, "serial_config_0x19"),
            (payload.serial_config_0x1A, "serial_config_0x1A"),
        )
    )
    serial_selected = any(
        (value & 0x1F) == 0x1E
        for value in (press_action_base, release_action_base, manual_play_reg)
    )
    serial_flag = bool(flags & AWP192X_BUTTON_RUNTIME_FLAG_SERIAL_CONFIG)
    if serial_selected != serial_flag:
        raise ValueError("serial waveform actions and serial-config flag must match")
    if not serial_flag and any(serial_values):
        raise ValueError("serial configuration values require the serial-config flag")

    if not 1 <= len(payload.waveforms) <= AWP192X_BUTTON_MAX_WAVEFORMS:
        raise ValueError("AWP192x Button v2 requires 1-8 waveform recipes")
    normalized_waveforms: list[Awp192xWaveformRecipe | Awp192xSineWaveformRecipe] = []
    seen_slices: set[int] = set()
    ranges: list[tuple[int, int, int]] = []
    for recipe in payload.waveforms:
        if isinstance(recipe, Awp192xWaveformRecipe):
            normalized = _validate_awp192x_waveform_recipe(recipe)
        elif isinstance(recipe, Awp192xSineWaveformRecipe):
            normalized = _validate_awp192x_sine_waveform_recipe(recipe)
        else:
            raise ValueError("AWP192x Button v2 supports only SINC and SIN waveforms")
        if normalized.slice_index in seen_slices:
            raise ValueError(f"duplicate waveform recipe for slice{normalized.slice_index}")
        seen_slices.add(normalized.slice_index)
        points = awp192x_waveform_recipe_points(normalized)
        end_addr = normalized.start_addr + points
        for other_slice, other_start, other_end in ranges:
            if normalized.start_addr < other_end and other_start < end_addr:
                raise ValueError(
                    f"slice{normalized.slice_index} overlaps slice{other_slice} in SRAM"
                )
        ranges.append((normalized.slice_index, normalized.start_addr, end_addr))
        normalized_waveforms.append(normalized)

    required_slices = set(
        required_awp192x_button_waveform_slices(
            press_action_base=press_action_base,
            release_action_base=release_action_base,
            manual_play_reg=manual_play_reg,
            serial_config_0x17=serial_values[0],
            serial_config_0x18=serial_values[1],
            serial_config_0x19=serial_values[2],
            serial_config_0x1A=serial_values[3],
        )
    )
    missing = sorted(required_slices - seen_slices)
    if missing:
        raise ValueError(
            "missing waveform recipe for "
            + ", ".join(f"slice{index}" for index in missing)
        )
    unused = sorted(seen_slices - required_slices)
    if unused:
        raise ValueError(
            "unused waveform recipe for "
            + ", ".join(f"slice{index}" for index in unused)
        )

    return Awp192xButtonPayloadV2(
        payload_version=payload_version,
        flags=flags,
        max_output_reg=max_output_reg,
        duration_reg=duration_reg,
        press_threshold_reg=press_threshold_reg,
        release_threshold_reg=release_threshold_reg,
        adc_filter_reg=adc_filter_reg,
        adc_event_wait_press=wait_press,
        press_action_base=press_action_base,
        release_action_base=release_action_base,
        manual_play_reg=manual_play_reg,
        serial_config_0x17=serial_values[0],
        serial_config_0x18=serial_values[1],
        serial_config_0x19=serial_values[2],
        serial_config_0x1A=serial_values[3],
        waveforms=tuple(normalized_waveforms),
    )


def _validate_awp192x_waveform_recipe(recipe: Awp192xWaveformRecipe) -> Awp192xWaveformRecipe:
    if not isinstance(recipe, Awp192xWaveformRecipe):
        raise ValueError("AWP192x waveform recipe must be an Awp192xWaveformRecipe")
    generator_type = _u8(recipe.generator_type, "generator_type")
    generator_version = _u8(recipe.generator_version, "generator_version")
    if generator_type != AWP192X_GENERATOR_SINC:
        raise ValueError("unsupported AWP192x waveform generator")
    if generator_version != AWP192X_GENERATOR_SINC_VERSION:
        raise ValueError("unsupported AWP192x waveform generator version")

    slice_index = _u8(recipe.slice_index, "slice_index")
    if slice_index > 29:
        raise ValueError("slice_index must be 0-29")
    start_addr = _u16(recipe.start_addr, "start_addr")
    if start_addr > AWP192X_SRAM_MAX:
        raise ValueError("start_addr must be within AWP192x SRAM")

    points = tuple(
        _u16(value, name)
        for value, name in (
            (recipe.points1, "points1"),
            (recipe.points2, "points2"),
            (recipe.points3, "points3"),
        )
    )
    if any(value < 1 or value > AWP192X_MAX_WAVEFORM_POINTS for value in points):
        raise ValueError("each SINC point count must be 1-1020")
    total_points = sum(points)
    if total_points > AWP192X_MAX_WAVEFORM_POINTS:
        raise ValueError("SINC total points must be <= 1020")
    if start_addr + total_points > AWP192X_SRAM_MAX + 1:
        raise ValueError("SINC recipe exceeds AWP192x SRAM")

    positive_mv = _i32(recipe.positive_mv, "positive_mv")
    negative_mv = _i32(recipe.negative_mv, "negative_mv")
    if not 0 <= positive_mv <= AWP192X_VOLTAGE_RANGE_MV:
        raise ValueError("positive_mv must be 0-95000")
    if not -AWP192X_VOLTAGE_RANGE_MV <= negative_mv <= 0:
        raise ValueError("negative_mv must be -95000-0")

    return Awp192xWaveformRecipe(
        generator_type=generator_type,
        generator_version=generator_version,
        slice_index=slice_index,
        start_addr=start_addr,
        points1=points[0],
        points2=points[1],
        points3=points[2],
        positive_mv=positive_mv,
        negative_mv=negative_mv,
    )


def _validate_awp192x_sine_waveform_recipe(
    recipe: Awp192xSineWaveformRecipe,
) -> Awp192xSineWaveformRecipe:
    if not isinstance(recipe, Awp192xSineWaveformRecipe):
        raise ValueError("AWP192x SIN recipe has an invalid type")
    slice_index = _u8(recipe.slice_index, "slice_index")
    if slice_index > 29:
        raise ValueError("slice_index must be 0-29")
    start_addr = _u16(recipe.start_addr, "start_addr")
    if start_addr > AWP192X_SRAM_MAX:
        raise ValueError("start_addr must be within AWP192x SRAM")
    points = _u16(recipe.points, "points")
    if not 1 <= points <= AWP192X_MAX_WAVEFORM_POINTS:
        raise ValueError("SIN points must be 1-1020")
    if start_addr + points > AWP192X_SRAM_MAX + 1:
        raise ValueError("SIN recipe exceeds AWP192x SRAM")

    start_phase_deg = _exact_int(recipe.start_phase_deg, "start_phase_deg")
    end_phase_deg = _exact_int(recipe.end_phase_deg, "end_phase_deg")
    if not 0 <= start_phase_deg <= 359:
        raise ValueError("SIN start phase must be normalized to 0-359 degrees")
    phase_span = end_phase_deg - start_phase_deg
    if not -512 <= phase_span <= 511:
        raise ValueError("SIN phase span must be -512 to 511 degrees")

    amplitude_mv = _exact_int(recipe.amplitude_mv, "amplitude_mv")
    offset_mv = _exact_int(recipe.offset_mv, "offset_mv")
    if not 0 <= amplitude_mv <= AWP192X_VOLTAGE_RANGE_MV:
        raise ValueError("SIN amplitude_mv must be 0-95000")
    if not -AWP192X_VOLTAGE_RANGE_MV <= offset_mv <= AWP192X_VOLTAGE_RANGE_MV:
        raise ValueError("SIN offset_mv must be -95000-95000")

    return Awp192xSineWaveformRecipe(
        slice_index=slice_index,
        start_addr=start_addr,
        points=points,
        start_phase_deg=start_phase_deg,
        end_phase_deg=end_phase_deg,
        amplitude_mv=amplitude_mv,
        offset_mv=offset_mv,
    )


def _sign_extend(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return int(value) - (1 << bits) if int(value) & sign_bit else int(value)


def _exact_int(value: int, name: str) -> int:
    integer = int(value)
    if value != integer:
        raise ValueError(f"{name} must be an integer")
    return integer


def _masked_u16(value: int, name: str, allowed_mask: int) -> int:
    validated = _u16(value, name)
    if validated & ~allowed_mask:
        raise ValueError(f"{name} contains unsupported bits")
    return validated


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


def _u32(value: int, name: str) -> int:
    integer = int(value)
    if integer < 0 or integer > 0xFFFFFFFF:
        raise ValueError(f"{name} must fit uint32")
    return integer


def _i32(value: int, name: str) -> int:
    integer = int(value)
    if integer < -0x80000000 or integer > 0x7FFFFFFF:
        raise ValueError(f"{name} must fit int32")
    return integer
