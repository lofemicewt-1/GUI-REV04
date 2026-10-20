"""Type-4 recipes. EEPROM stores parameters; firmware assigns descriptor slices."""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Any

from core.awp1221_sinc import Awp1221SincParams, build_awp1221_sinc_plan

TYPE4_VERSION = 2
TYPE4_LEGACY_VERSION = 1
ROM_KINDS = ("SINE", "Gauss", "Fixed-slope Ramp", "DC")
TYPE4_SAVE_KINDS = (0, 4, 5)
# kind 4 = three SINC halves; kind 5 = full SIN followed by those halves.
_HEADER = struct.Struct("<BBBBBffHHBBBB")
_HEADER_V2 = struct.Struct("<BBBBBffHHBBBBB")
_RECIPE = struct.Struct("<BBBBHHHHH")


@dataclass(frozen=True)
class Awp1221WaveRecipe:
    kind: int
    coarse: int = 0
    fine: int = 0
    offset: int = 0
    times: tuple[int, ...] = (1,)
    repeat: int = 1


@dataclass(frozen=True)
class Awp1221PresetModel:
    zero_time: int
    press_return: int
    release_return: int
    peak: float
    trough: float
    thresholds: tuple[int, int]
    wave_refs: tuple[int, int]
    event_repeats: tuple[int, int]
    waves: tuple[Awp1221WaveRecipe, ...]
    jumps: tuple[int, int] = (1, 0)


def _bounded(value: int, maximum: int, label: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be in range {minimum}..{maximum}")
    return value


def allocate_awp1221_slices(waves: tuple[Awp1221WaveRecipe, ...]) -> tuple[int, ...]:
    """Use single ROM slots 0..15, SINC groups 1/2, SIN+SINC groups 3/4."""
    rom, sinc, combined = 0, 0, 0
    assigned = []
    for wave in waves:
        if wave.kind < 4:
            if rom >= 16:
                raise ValueError("Too many ROM waveforms")
            assigned.append(rom)
            rom += 1
        elif wave.kind == 4:
            if sinc >= 2:
                raise ValueError("Only two three-segment SINC groups are available")
            assigned.append(18 + sinc * 3)
            sinc += 1
        elif wave.kind == 5:
            if combined >= 2:
                raise ValueError("Only two SIN+SINC groups are available")
            assigned.append(24 + combined * 4)
            combined += 1
        else:
            raise ValueError("Unsupported Type-4 waveform")
    return tuple(assigned)


def pack_awp1221_payload(model: Awp1221PresetModel) -> bytes:
    """Create a new v2 record; old v1 records and ROM kinds remain decodable."""
    return _pack_awp1221_payload(model, allow_legacy_kinds=False, version=TYPE4_VERSION)


def _pack_awp1221_payload(
    model: Awp1221PresetModel,
    *,
    allow_legacy_kinds: bool,
    version: int = TYPE4_VERSION,
) -> bytes:
    count = len(model.waves)
    _bounded(count, 2, "Used waveform count", 1)
    for label, values, maximum in (
        ("threshold", model.thresholds, 0xFFF),
        ("wave reference", model.wave_refs, count - 1),
        ("event repeat", model.event_repeats, 7),
    ):
        if len(values) != 2:
            raise ValueError(f"Type-4 requires two {label} values")
        for value in values:
            _bounded(value, maximum, label)
    if len(model.jumps) != 2:
        raise ValueError("Type-4 requires two jump targets")
    jump = 0
    for index, value in enumerate(model.jumps):
        jump |= _bounded(value, 1, "Jump target") << index
    if version == TYPE4_LEGACY_VERSION and model.jumps != (1, 0):
        raise ValueError("Type-4 v1 cannot store jump targets")
    if version not in (TYPE4_LEGACY_VERSION, TYPE4_VERSION):
        raise ValueError("Invalid Type-4 version")
    if set(model.wave_refs) != set(range(count)) or len(set(model.waves)) != count:
        raise ValueError("Type-4 must contain only distinct, referenced recipes")
    if not allow_legacy_kinds and any(w.kind not in TYPE4_SAVE_KINDS for w in model.waves):
        raise ValueError("New Type-4 records can save only SIN and SINC waveforms")
    for value in (model.zero_time, model.press_return, model.release_return):
        _bounded(value, 7, "ADC setting code")
    if not math.isfinite(model.peak) or not math.isfinite(model.trough):
        raise ValueError("SINC limits must be finite")
    if any(w.kind >= 4 for w in model.waves) and not (
        0 < model.peak <= 100 and 0 < model.trough <= 98.4375
    ):
        raise ValueError("SINC limits exceed the continuous waveform generator's range")
    peak, trough = struct.unpack("<ff", struct.pack("<ff", model.peak, model.trough))
    if not any(w.kind >= 4 for w in model.waves):
        if peak or trough:
            raise ValueError("Unused SINC limits must be zero")
        peak = trough = 0.0
    fields = (version, count, model.zero_time, model.press_return, model.release_return,
              peak, trough, *model.thresholds, *model.wave_refs, *model.event_repeats)
    encoded = _HEADER.pack(*fields) if version == TYPE4_LEGACY_VERSION else _HEADER_V2.pack(*fields, jump)
    slices = allocate_awp1221_slices(model.waves)
    for wave, start in zip(model.waves, slices):
        _bounded(wave.repeat, 256, "Internal repeat", 1)
        expected_times = 1 if wave.kind < 4 else (3 if wave.kind == 4 else 4)
        if len(wave.times) != expected_times:
            raise ValueError("Wrong segment count for Type-4 waveform")
        for value in wave.times:
            _bounded(value, 256, "Point time", 1)
        if wave.kind < 4:
            _bounded(wave.coarse, 31, "VPK")
            _bounded(wave.fine, 3, "Micro Bias")
            _bounded(wave.offset, 127, "Offset")
        else:
            if wave.coarse or wave.fine or wave.offset or (wave.kind == 4 and wave.repeat != 1):
                raise ValueError("SINC amplitudes are derived from shared limits")
            serial = {18: "serial1", 21: "serial2", 24: "serial3", 28: "serial4"}[start]
            plan = build_awp1221_sinc_plan(Awp1221SincParams(peak, trough, wave.times, 512,
                                                          wave.repeat), serial=serial)
            if not plan.writable:
                raise ValueError(f"SINC limits cannot be reconstructed: {plan.reason}")
        times = (*wave.times, *(0 for _ in range(4 - len(wave.times))))
        encoded += _RECIPE.pack(wave.kind, wave.coarse, wave.fine, wave.offset, *times, wave.repeat)
    return encoded


def unpack_awp1221_payload(payload: bytes) -> Awp1221PresetModel:
    raw = bytes(payload)
    if len(raw) < _HEADER.size:
        raise ValueError("Truncated Type-4 payload")
    version = raw[0]
    if version == TYPE4_LEGACY_VERSION:
        header, jumps = _HEADER, (1, 0)
    elif version == TYPE4_VERSION:
        header = _HEADER_V2
        if len(raw) < header.size or raw[21] > 3:
            raise ValueError("Invalid Type-4 version or length")
        jumps = (raw[21] & 1, (raw[21] >> 1) & 1)
    else:
        raise ValueError("Invalid Type-4 version or length")
    try:
        fields = header.unpack_from(raw)
    except struct.error as exc:
        raise ValueError("Invalid Type-4 version or length") from exc
    version, count, zero, press, release, peak, trough, t0, t1, r0, r1, n0, n1 = fields[:13]
    if not 1 <= count <= 2 or len(raw) != header.size + count * _RECIPE.size:
        raise ValueError("Invalid Type-4 version or length")
    waves = []
    for index in range(count):
        kind, coarse, fine, offset, a, b, c, d, repeat = _RECIPE.unpack_from(raw, header.size + index * _RECIPE.size)
        length = 1 if kind < 4 else (3 if kind == 4 else 4)
        all_times = (a, b, c, d)
        if any(all_times[length:]):
            raise ValueError("Nonzero unused Type-4 segment times")
        waves.append(Awp1221WaveRecipe(kind, coarse, fine, offset, all_times[:length], repeat))
    model = Awp1221PresetModel(zero, press, release, peak, trough, (t0, t1), (r0, r1),
                              (n0, n1), tuple(waves), jumps)
    if _pack_awp1221_payload(model, allow_legacy_kinds=True, version=version) != raw:
        raise ValueError("Noncanonical Type-4 payload")
    return model


def awp1221_type4_save_preflight(settings_model: Any) -> tuple[bool, str | None]:
    """Reject Full-mode stage combinations that Type-4 cannot encode.

    The chip settings adapter records the unsupported Full-only combinations
    separately from the shared Type-4 fields. Independent Full settings (for
    example GPIO) do not prevent saving; they remain in the page settings file.
    """
    advanced = getattr(settings_model, "advanced", {})
    unsupported = advanced.get("type4_unsupported", ()) if isinstance(advanced, dict) else ()
    if unsupported:
        return False, "preset_slot_advanced_block_warning"
    return True, None


def awp1221_descriptor_words(model: Awp1221PresetModel) -> tuple[tuple[int, tuple[int, ...]], ...]:
    """Reference decoder for readback/UI and firmware parity tests."""
    _pack_awp1221_payload(model, allow_legacy_kinds=True, version=TYPE4_VERSION)
    result = []
    for wave, start in zip(model.waves, allocate_awp1221_slices(model.waves)):
        if wave.kind < 4:
            result.append((start, (0x800 | (wave.fine << 6) | (wave.kind << 3),
                                   wave.times[0] - 1, wave.repeat - 1,
                                   (wave.offset << 5) | wave.coarse)))
        else:
            serial = {18: "serial1", 21: "serial2", 24: "serial3", 28: "serial4"}[start]
            plan = build_awp1221_sinc_plan(Awp1221SincParams(model.peak, model.trough, wave.times,
                                                          512, wave.repeat), serial=serial)
            for i, slice_index in enumerate(plan.slices):
                result.append((slice_index, (*plan.descriptor_words[i],
                                             (plan.offset_codes[i] << 5) | plan.vpk_codes[i])))
    return tuple(result)
