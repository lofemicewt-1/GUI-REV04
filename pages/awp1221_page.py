from __future__ import annotations

from collections.abc import Iterator

from contextlib import contextmanager, nullcontext
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable
from core.lazy_pages import LazyPages
from core import chip_status_cache

from core.awp1221_command_gate import AWP1221_CMD_GATE_BIT, ensure_awp1221_cmd_gate
from core.awp1221_page_script import (
    Awp1221PageScript,
    Awp1221StageScript,
    build_awp1221_page_script_operations,
    extract_awp1221_page_script,
)
from core.awp1221_sinc import (
    awp1221_sinc_duration_us,
    awp1221_sinc_parse_params,
    awp1221_sinc_plan_key,
    awp1221_sinc_slice_windows,
    awp1221_sinc_write_words,
    build_awp1221_sinc_plan,
)
from core.awp1221_gpio import (
    AWP1221_GPIO_PINS,
    AWP1221_GPIO_REGISTER,
    Awp1221GpioController,
    GPIO_CATEGORY_FUNCTIONS,
    GPIO_CATEGORY_LABELS,
    GPIO_FUNCTION_LABELS,
    GpioChannelBits,
    gpio_function_category,
    gpio_mcu_role,
)
from core.base_tool import (
    GPIO_MODE_DETECT,
    GPIO_MODE_HIGH,
    GPIO_MODE_LOW,
    execute_register_script_operations,
    format_register_script,
    parse_register_script,
    run_async_write,
)
from pages.base_page import BasePage, SimpleTooltip
from pages.waveform_preview import PreviewSegment, compact_preview, draw_omissions
from pages.awp1221_board_presets import AWP1221BoardPresetMixin
from pages.awp1221_realtime_sram import (
    AWP1221RealtimeMixin,
    AWP1221SramEditor,
    AWP1221_ADC_LIVE_REGISTER,
    AWP1221_ADC_LOAD_REGISTER,
    awp1221_adc_load_label,
    awp1221_merge_adc_load,
    awp1221_threshold_adc_counts,
    awp1221_vibration_band_seconds,
    awp1221_zero_time_seconds,
    decode_awp1221_adc_value,
)
from pages.gpio_controls import (
    NullGpioControlPanel,
    active_serial_identity,
)

_GPIO_CATEGORY_BY_LABEL = {label: key for key, label in GPIO_CATEGORY_LABELS.items()}
_GPIO_FUNCTION_BY_LABEL = {label: code for code, label in GPIO_FUNCTION_LABELS.items()}


AWP1221_DEVICE_ADDRS = tuple(range(0x50, 0x58))
AWP1221_ID_REG = 0x16
AWP1221_CHIP_ID = 0x1221
AWP1221_CONTROL_REGISTER = 0x02
AWP1221_DEBOUNCE_REGISTER = 0x12
AWP1221_DEBOUNCE_MASK = 0x03FF
AWP1221_ADC_SETTINGS_REGISTER = 0x05
AWP1221_INTERFACE_CONFIG_0_DEFAULT = 0x442E
AWP1221_RETURN_BIT_REGISTER = 0x06
AWP1221_SLEEP_SENSITIVITY_MASK = 1 << 12
AWP1221_ADC_VALUE_REGISTER = 0x13
AWP1221_ADC_THRESHOLD_PRECISION_MASK = 1 << 14
AWP1221_SRAM_DATA_REGISTER = 0x00
AWP1221_SRAM_ADDRESS_REGISTER = 0x15
AWP1221_PLAY_WAVE_REGISTER = 0x0C
AWP1221_SRAM_MODE_MASK = 0x0003
AWP1221_SRAM_WRITE_READ_MODE = 0x0003
AWP1221_SRAM_PLAY_MODE = 0x0002
AWP1221_VPK_CODE_MASK = 0x001F
AWP1221_VPK_LSB_VOLTS = 6.25
AWP1221_FINE_AMPLITUDE_CODE_MASK = 0x0003
AWP1221_FINE_AMPLITUDE_LSB_VOLTS = 200.0 / 128
AWP1221_OFFSET_CODE_MASK = 0x007F
AWP1221_OFFSET_WORD_MASK = AWP1221_OFFSET_CODE_MASK << 5
AWP1221_OFFSET_LSB_VOLTS = 100.0 / 128
AWP1221_AUTO_SERIAL_MASK = 1 << 5
AWP1221_WAVEFORM_SLICE_COUNT = 32
AWP1221_WAVEFORM_WORDS_PER_SLICE = 4
AWP1221_SERIAL_WAVEFORMS = (
    ("serial0 slice16-17", 16),
    ("serial1 slice18-20", 18),
    ("serial2 slice21-23", 21),
    ("serial3 slice24-27", 24),
    ("serial4 slice28-31", 28),
)
AWP1221_SINC_SERIAL_GROUPS = (
    ("serial1", 0x12, 0x14),
    ("serial2", 0x15, 0x17),
    ("serial3", 0x18, 0x1B),
    ("serial4", 0x1C, 0x1F),
)


def awp1221_sinc_serial_group_label(serial: str, start_slice: int, end_slice: int) -> str:
    """Format a page-only SINC serial group with its hexadecimal slice range."""
    serial = str(serial).strip()
    start_slice = int(start_slice)
    end_slice = int(end_slice)
    if not 0 <= start_slice < AWP1221_WAVEFORM_SLICE_COUNT:
        raise ValueError("SINC serial start must be in range 0..31")
    if not 0 <= end_slice < AWP1221_WAVEFORM_SLICE_COUNT:
        raise ValueError("SINC serial end must be in range 0..31")
    if end_slice < start_slice:
        raise ValueError("SINC serial end must not precede its start")
    return f"{serial} 0x{start_slice:02X}-0x{end_slice:02X}"


AWP1221_SINC_SERIAL_OPTIONS = tuple(
    awp1221_sinc_serial_group_label(serial, start_slice, end_slice)
    for serial, start_slice, end_slice in AWP1221_SINC_SERIAL_GROUPS
)
AWP1221_ADC_SAMPLE_UPDATE_OPTIONS = ("4 us", "8 us", "16 us", "32 us")
AWP1221_ADC_LDO_OPTIONS = ("40 uA", "160 uA")
AWP1221_ZERO_TIME_OPTIONS = ("1 ms", "2 ms", "4 ms", "8 ms", "16 ms", "32 ms", "64 ms", "128 ms")
AWP1221_RETURN_BIT_TIME_OPTIONS = (
    "off",
    "6400 us",
    "3200 us",
    "1600 us",
    "800 us",
    "400 us",
    "200 us",
    "100 us",
)

AWP1221_STATE_COMMANDS = {
    "awp1221_reset": 0x5283,
    "awp1221_sleep": 0x5350,
    "awp1221_shutdown": 0x5337,
    "awp1221_default_state": 0x4399,
}

AWP1221_CONTROL_COMMANDS = {
    "start_detection": 0x0001,
    "stop_detection": 0x0002,
    "start_play": 0x0004,
    "stop_play": 0x0008,
    "awp1221_stop_all": 0x0010,
}


class AWP1221SharedDeviceState:
    """Shared SINC qualification and activity state for one device identity."""

    def __init__(self) -> None:
        self.revision = 0
        self.sinc_key: tuple[Any, ...] | None = None
        self.detection_active = False
        self.detection_device_addr: int | None = None
        self.waveform_play_active = False

    @property
    def active(self) -> bool:
        return self.detection_active or self.waveform_play_active


class AWP1221SharedStateOwner:
    """Own shared state without coupling either visible mode page to the other."""

    def __init__(self) -> None:
        self._states: dict[tuple[Any, ...], AWP1221SharedDeviceState] = {}

    @staticmethod
    def _state_key(serial: Any, device_addr: int, port: str | None) -> tuple[Any, ...]:
        transport = (id(serial), str(port)) if port else (id(serial),)
        return (*transport, int(device_addr))

    def state_for(
        self,
        serial: Any,
        device_addr: int,
        *,
        port: str | None = None,
    ) -> AWP1221SharedDeviceState:
        key = self._state_key(serial, device_addr, port)
        return self._states.setdefault(key, AWP1221SharedDeviceState())

    def invalidate_state(
        self,
        state: AWP1221SharedDeviceState,
        *,
        clear_active: bool = False,
    ) -> int:
        state.revision += 1
        state.sinc_key = None
        if clear_active:
            state.detection_active = False
            state.detection_device_addr = None
            state.waveform_play_active = False
        return state.revision

    def invalidate_device(
        self,
        serial: Any,
        device_addr: int,
        *,
        port: str | None = None,
        clear_active: bool = False,
    ) -> int:
        return self.invalidate_state(
            self.state_for(serial, device_addr, port=port),
            clear_active=clear_active,
        )

    def invalidate_all(self, *, clear_active: bool = False) -> None:
        for state in self._states.values():
            self.invalidate_state(state, clear_active=clear_active)

    @staticmethod
    def accepts_sinc_write(
        state: AWP1221SharedDeviceState,
        revision: int,
    ) -> bool:
        return state.revision == revision

AWP1221_ADC_TIME_LABELS = (
    "1 us",
    "100 us",
    "200 us",
    "500 us",
    "1 ms",
    "2 ms",
    "4 ms",
    "6 ms",
    "8 ms",
    "10 ms",
    "12 ms",
    "16 ms",
    "20 ms",
    "25 ms",
)
AWP1221_ADC_TIME_CODE_LABELS = AWP1221_ADC_TIME_LABELS + ("25 ms", "25 ms")

AWP1221_ADC_FLAG_BITS = {
    "stop_r": 7,
    "stop_p": 6,
    "trigger_r": 5,
    "trigger_p": 4,
    "s_r": 3,
    "s_p": 2,
    "t_r": 1,
    "t_p": 0,
}


def encode_awp1221_debounce_time(time_ms: int) -> int:
    """Convert the AWP1018-compatible 0..1024 ms value to 0x12[9:0]."""
    if not 0 <= time_ms <= 1024:
        raise ValueError("Debounce time must be between 0 and 1024 ms")
    return round(time_ms * AWP1221_DEBOUNCE_MASK / 1024) if time_ms else 0


def decode_awp1221_debounce_time(register_value: int) -> int:
    """Convert 0x12[9:0] back to the displayed millisecond value."""
    code = register_value & AWP1221_DEBOUNCE_MASK
    return round(code * 1024 / AWP1221_DEBOUNCE_MASK) if code else 0


def encode_awp1221_adc_control(
    release_time: int,
    press_time: int,
    flags: dict[str, bool],
) -> int:
    """Encode the confirmed 0x08/0x0D AWP1221 ADC control layout."""
    if not 0 <= int(release_time) <= 0xF or not 0 <= int(press_time) <= 0xF:
        raise ValueError("ADC time code must be in range 0x0..0xF")
    value = (int(release_time) << 12) | (int(press_time) << 8)
    for name, bit in AWP1221_ADC_FLAG_BITS.items():
        if bool(flags.get(name, False)):
            value |= 1 << bit
    return value


def decode_awp1221_adc_control(value: int) -> dict[str, Any]:
    value = int(value)
    if not 0 <= value <= 0xFFFF:
        raise ValueError("ADC register value must be in range 0x0000..0xFFFF")
    return {
        "release_time": (value >> 12) & 0xF,
        "press_time": (value >> 8) & 0xF,
        "flags": {
            name: bool(value & (1 << bit))
            for name, bit in AWP1221_ADC_FLAG_BITS.items()
        },
    }


def _require_unsigned(value: int, maximum: int, name: str) -> int:
    value = int(value)
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be in range 0..{maximum}")
    return value


def _awp1221_option_code(value: str, options: tuple[str, ...], name: str) -> int:
    try:
        return options.index(str(value))
    except ValueError as exc:
        raise ValueError(f"Unsupported {name}: {value}") from exc


def encode_awp1221_adc_settings_registers(
    current_05: int,
    current_06: int,
    *,
    sample_update: str,
    zero_time: str,
    press_return_bit_time: str,
    release_return_bit_time: str,
    zps_enabled: bool,
    zps_detect: bool,
    sleep_sensitivity: bool,
) -> tuple[int, int]:
    """Apply the confirmed ADC settings fields while preserving all other bits."""
    register_05 = _require_unsigned(current_05, 0xFFFF, "Register 0x05")
    register_06 = _require_unsigned(current_06, 0xFFFF, "Register 0x06")
    register_05 &= ~0xEC0C
    register_05 |= _awp1221_option_code(
        sample_update, AWP1221_ADC_SAMPLE_UPDATE_OPTIONS, "ADC sample update time"
    ) << 10
    register_05 |= _awp1221_option_code(zero_time, AWP1221_ZERO_TIME_OPTIONS, "Zero Time") << 13
    if zps_enabled:
        register_05 |= 1 << 3
    if zps_detect:
        register_05 |= 1 << 2

    register_06 &= ~(0x00FC | AWP1221_SLEEP_SENSITIVITY_MASK)
    register_06 |= _awp1221_option_code(
        press_return_bit_time, AWP1221_RETURN_BIT_TIME_OPTIONS, "Press return-bit time"
    ) << 5
    register_06 |= _awp1221_option_code(
        release_return_bit_time, AWP1221_RETURN_BIT_TIME_OPTIONS, "Release return-bit time"
    ) << 2
    if sleep_sensitivity:
        register_06 |= AWP1221_SLEEP_SENSITIVITY_MASK
    return register_05, register_06


AWP1221_SETTINGS_REGISTERS = (
    AWP1221_ADC_SETTINGS_REGISTER,
    AWP1221_RETURN_BIT_REGISTER,
    AWP1221_DEBOUNCE_REGISTER,
    AWP1221_ADC_LOAD_REGISTER,
)


def awp1221_settings_target_words(current: dict[int, int], settings: dict[str, Any]) -> dict[int, int]:
    """Words the ADC settings writer would store, preserving bits it does not own."""
    register_05, register_06 = encode_awp1221_adc_settings_registers(
        current[AWP1221_ADC_SETTINGS_REGISTER],
        current[AWP1221_RETURN_BIT_REGISTER],
        sample_update=settings["sample_update"],
        zero_time=settings["zero_time"],
        press_return_bit_time=settings["press_return_bit_time"],
        release_return_bit_time=settings["release_return_bit_time"],
        zps_enabled=settings["zps_enabled"],
        zps_detect=settings["zps_detect"],
        sleep_sensitivity=settings["sleep_sensitivity"],
    )
    if settings["auto_serial"]:
        register_05 |= AWP1221_AUTO_SERIAL_MASK
    else:
        register_05 &= ~AWP1221_AUTO_SERIAL_MASK
    debounce_value = (
        int(current[AWP1221_DEBOUNCE_REGISTER]) & ~AWP1221_DEBOUNCE_MASK
    ) | int(settings["debounce_code"])
    load_value = awp1221_merge_adc_load(
        current[AWP1221_ADC_LOAD_REGISTER],
        settings.get("adc_ldo", "40 uA"),
    )
    return {
        AWP1221_ADC_SETTINGS_REGISTER: register_05 & 0xFFFF,
        AWP1221_RETURN_BIT_REGISTER: register_06 & 0xFFFF,
        AWP1221_DEBOUNCE_REGISTER: debounce_value & 0xFFFF,
        AWP1221_ADC_LOAD_REGISTER: load_value & 0xFFFF,
    }


def awp1221_full_stage_words(
    registers: tuple[int, int, int, int, int],
    values: tuple[int, int, int, int, int],
    current: dict[int, int],
    event_mode: str,
) -> dict[int, int]:
    """Full-mode stage words, keeping the inactive threshold precision bit."""
    words = [int(value) & 0xFFFF for value in values]
    if event_mode in {"press", "release"}:
        inactive = 2 if event_mode == "press" else 1
        register = registers[inactive]
        words[inactive] = (
            words[inactive] & ~AWP1221_ADC_THRESHOLD_PRECISION_MASK
        ) | (int(current[register]) & AWP1221_ADC_THRESHOLD_PRECISION_MASK)
    return {register: word for register, word in zip(registers, words)}


def awp1221_stage_compare_registers(mode: str, stage_index: int) -> tuple[int, ...]:
    registers = (
        (0x08, 0x09, 0x0A, 0x0B, 0x0C),
        (0x0D, 0x0E, 0x0F, 0x10, 0x11),
    )[stage_index]
    if mode == "button_mode":
        return (registers[0], registers[1], registers[2], registers[4])
    return registers


def awp1221_mismatch_groups(
    current: dict[int, int],
    expected: dict[int, int],
    groups: dict[str, tuple[int, ...]],
) -> tuple[str, ...]:
    found: list[str] = []
    for name, registers in groups.items():
        if any((int(current[register]) & 0xFFFF) != (int(expected[register]) & 0xFFFF) for register in registers):
            found.append(name)
    return tuple(found)


def awp1221_verified_start_plan(mismatches: tuple[str, ...], choice: str | None) -> str:
    if not mismatches:
        return "command"
    if choice == "rewrite":
        return "rewrite-then-command"
    if choice == "start":
        return "command"
    return "abort"


def decode_awp1221_adc_settings_registers(register_05: int, register_06: int) -> dict[str, Any]:
    register_05 = _require_unsigned(register_05, 0xFFFF, "Register 0x05")
    register_06 = _require_unsigned(register_06, 0xFFFF, "Register 0x06")
    return {
        "sample_update": AWP1221_ADC_SAMPLE_UPDATE_OPTIONS[(register_05 >> 10) & 0x3],
        "zero_time": AWP1221_ZERO_TIME_OPTIONS[(register_05 >> 13) & 0x7],
        "press_return_bit_time": AWP1221_RETURN_BIT_TIME_OPTIONS[(register_06 >> 5) & 0x7],
        "release_return_bit_time": AWP1221_RETURN_BIT_TIME_OPTIONS[(register_06 >> 2) & 0x7],
        "zps_enabled": bool(register_05 & (1 << 3)),
        "zps_detect": bool(register_05 & (1 << 2)),
        "sleep_sensitivity": bool(register_06 & AWP1221_SLEEP_SENSITIVITY_MASK),
    }


def awp1221_adc_time_code(label: str) -> int:
    try:
        return AWP1221_ADC_TIME_LABELS.index(str(label))
    except ValueError as exc:
        raise ValueError(f"Unsupported ADC OVV time: {label}") from exc


def awp1221_adc_time_label(code: int) -> str:
    return AWP1221_ADC_TIME_CODE_LABELS[_require_unsigned(code, 0xF, "ADC OVV time code")]


def awp1221_event_mode_flags(mode: str, flags: dict[str, bool] | None = None) -> dict[str, bool]:
    values = {name: bool((flags or {}).get(name, False)) for name in AWP1221_ADC_FLAG_BITS}
    for name in ("t_p", "trigger_p", "t_r", "trigger_r"):
        values[name] = False
    if mode == "press":
        values["t_p"] = True
        values["trigger_p"] = True
    elif mode == "release":
        values["t_r"] = True
        values["trigger_r"] = True
    else:
        raise ValueError("ADC event mode must be press or release")
    return values


def awp1221_format_threshold_signed(code: int) -> str:
    value = int(code) & 0xFFF
    if value & 0x800:
        value -= 0x1000
    return str(value)


def awp1221_parse_threshold_text(text: str) -> int:
    value_text = str(text).strip()
    if not value_text:
        raise ValueError("Threshold is empty")
    if value_text[:2].lower() == "0x":
        value = int(value_text[2:], 16)
        if not 0 <= value <= 0xFFF:
            raise ValueError("Threshold must be in range 0x000..0xFFF")
        return value
    value = int(value_text, 10)
    if not -2047 <= value <= 2047:
        raise ValueError("Threshold must be in range -2047..2047")
    return value & 0xFFF


def awp1221_threshold_hex_hint(text: str) -> str:
    value_text = str(text).strip()
    if not value_text:
        return ""
    try:
        value = awp1221_parse_threshold_text(value_text)
    except ValueError:
        return ""
    return f"0x{value:03X}"


def awp1221_en_high_load_value(current: int) -> int:
    """Set ADC_LDO to 160 uA while preserving the 0x1F command gate bit."""
    return awp1221_merge_adc_load(current, "160 uA")


def encode_awp1221_adc_threshold(
    *,
    debounce_enabled: bool,
    next_config: int,
    below_threshold: bool,
    threshold: int,
    use_high_12_bits: bool = False,
) -> int:
    """Encode the shared 0x09/0x0A and 0x0E/0x0F threshold layout."""
    value = _require_unsigned(threshold, 0xFFF, "Threshold")
    value |= _require_unsigned(next_config, 1, "CFG_NXT") << 13
    if debounce_enabled:
        value |= 1 << 15
    if use_high_12_bits:
        value |= AWP1221_ADC_THRESHOLD_PRECISION_MASK
    if below_threshold:
        value |= 1 << 12
    return value


def decode_awp1221_adc_threshold(value: int) -> dict[str, Any]:
    value = _require_unsigned(value, 0xFFFF, "ADC threshold register")
    return {
        "debounce_enabled": bool(value & 0x8000),
        "next_config": (value >> 13) & 0x1,
        "use_high_12_bits": bool(value & AWP1221_ADC_THRESHOLD_PRECISION_MASK),
        "below_threshold": bool(value & 0x1000),
        "threshold": value & 0x0FFF,
    }


def encode_awp1221_adc_slope(
    *,
    release_below: bool,
    release_slope: int,
    press_below: bool,
    press_slope: int,
) -> int:
    """Encode the shared 0x0B/0x10 slope layout."""
    value = _require_unsigned(release_slope, 0x7F, "SLOPE_R") << 8
    value |= _require_unsigned(press_slope, 0x7F, "SLOPE_P")
    if release_below:
        value |= 1 << 15
    if press_below:
        value |= 1 << 7
    return value


def decode_awp1221_adc_slope(value: int) -> dict[str, Any]:
    value = _require_unsigned(value, 0xFFFF, "ADC slope register")
    return {
        "release_below": bool(value & 0x8000),
        "release_slope": (value >> 8) & 0x7F,
        "press_below": bool(value & 0x0080),
        "press_slope": value & 0x7F,
    }


def encode_awp1221_adc_wave(
    *,
    release_repeat: int,
    release_wave: int,
    press_repeat: int,
    press_wave: int,
) -> int:
    """Encode the shared 0x0C/0x11 event waveform layout."""
    return (
        (_require_unsigned(release_repeat, 0x7, "Repeat timesR") << 13)
        | (_require_unsigned(release_wave, 31, "SliceR") << 8)
        | (_require_unsigned(press_repeat, 0x7, "Repeat timesP") << 5)
        | _require_unsigned(press_wave, 31, "SliceP")
    )


def decode_awp1221_adc_wave(value: int) -> dict[str, int]:
    value = _require_unsigned(value, 0xFFFF, "ADC wave register")
    return {
        "release_repeat": (value >> 13) & 0x7,
        "release_wave": (value >> 8) & 0x1F,
        "press_repeat": (value >> 5) & 0x7,
        "press_wave": value & 0x1F,
    }


def encode_awp1221_waveform_descriptor(
    *,
    source_code: int,
    micro_bias_code: int,
    waveform_code: int,
    depth_code: int,
    half_cycle_code: int,
    phase_code: int,
    point_time: int,
    repeat_count: int,
    infinite_loop: bool,
) -> tuple[int, int, int]:
    """Encode the three confirmed words of one AWP1221 waveform slice."""
    point_time = _require_unsigned(point_time, 256, "Point time")
    repeat_count = _require_unsigned(repeat_count, 256, "Repeat count")
    if point_time == 0:
        raise ValueError("Point time must be in range 1..256")
    if repeat_count == 0:
        raise ValueError("Repeat count must be in range 1..256")
    source_code = _require_unsigned(source_code, 0x3, "Waveform source")
    if source_code not in (0b01, 0b10):
        raise ValueError("Waveform source must be RAM (0x1) or ROM slice mark (0x2)")
    first = (
        (source_code << 10)
        | (_require_unsigned(micro_bias_code, 0x3, "Micro bias") << 6)
        | (_require_unsigned(depth_code, 0x1, "Waveform depth") << 5)
        | (_require_unsigned(waveform_code, 0x3, "Waveform") << 3)
        | (_require_unsigned(half_cycle_code, 0x3, "Half cycle") << 1)
        | _require_unsigned(phase_code, 0x1, "Phase")
    )
    second = (
        (int(bool(infinite_loop)) << 11)
        | (point_time - 1)
    )
    third = repeat_count - 1
    return tuple(value & 0x0FFF for value in (first, second, third))


def awp1221_waveform_offset_voltage(offset_code: int) -> float:
    """Decode the confirmed N*4+3 bit11..5 signed offset table."""
    offset_code = _require_unsigned(
        offset_code,
        AWP1221_OFFSET_CODE_MASK,
        "Waveform offset",
    )
    if offset_code == 0:
        return 0.0
    if offset_code < 0x40:
        return -(offset_code * AWP1221_OFFSET_LSB_VOLTS)
    return (0x80 - offset_code) * AWP1221_OFFSET_LSB_VOLTS


def awp1221_waveform_offset_label(offset_code: int) -> str:
    voltage = awp1221_waveform_offset_voltage(offset_code)
    voltage_text = "0" if voltage == 0 else f"{voltage:+.5f}".rstrip("0").rstrip(".")
    return f"0x{offset_code:02X} {voltage_text} V"


def awp1221_waveform_coarse_amplitude_voltage(code: int) -> float:
    """Convert Vpk[4:0] to the confirmed coarse amplitude in volts."""
    code = _require_unsigned(code, AWP1221_VPK_CODE_MASK, "Coarse amplitude")
    return code * AWP1221_VPK_LSB_VOLTS


def awp1221_waveform_fine_amplitude_voltage(code: int) -> float:
    """Convert the 2-bit fine amplitude code to 200*(code+1)/128 volts."""
    code = _require_unsigned(code, AWP1221_FINE_AMPLITUDE_CODE_MASK, "Fine amplitude")
    return (code + 1) * AWP1221_FINE_AMPLITUDE_LSB_VOLTS


def awp1221_waveform_peak_to_peak_voltage(coarse_code: int, fine_code: int) -> float:
    """Return the confirmed displayed peak-to-peak sum without an extra factor of two."""
    return awp1221_waveform_coarse_amplitude_voltage(
        coarse_code
    ) + awp1221_waveform_fine_amplitude_voltage(fine_code)


def awp1221_waveform_peak_to_peak_label(coarse_code: int, fine_code: int) -> str:
    voltage = awp1221_waveform_peak_to_peak_voltage(coarse_code, fine_code)
    return f"{voltage:.4f}".rstrip("0").rstrip(".") + " V"


def awp1221_sinc_serial_group(value: str) -> tuple[str, int, int]:
    """Resolve a displayed SINC serial option to its serial name and slice range."""
    text = str(value).strip().lower()
    for serial, start_slice, end_slice in AWP1221_SINC_SERIAL_GROUPS:
        if text == serial or text.startswith(f"{serial} "):
            return serial, start_slice, end_slice
    raise ValueError(f"Unsupported SINC serial group: {value}")


def awp1221_waveform_effective_depth_points(
    waveform: str,
    depth_points: int,
    half_cycle_code: int,
) -> int:
    """Return displayed/preview points without changing the hardware depth code."""
    depth_points = int(depth_points)
    if depth_points <= 0:
        raise ValueError("Waveform depth must be positive")
    half_cycle_code = _require_unsigned(half_cycle_code, 0x3, "Half cycle")
    waveform_key = str(waveform).strip().lower()
    if waveform_key in {"sine", "sin"}:
        effective = depth_points
    elif waveform_key in {"gauss", "gaussian", "fixed-slope ramp", "slope"}:
        effective = min(depth_points, 256)
    elif waveform_key == "dc":
        effective = min(depth_points, 128)
    else:
        effective = depth_points
    if half_cycle_code in (0b01, 0b10):
        effective = max(1, effective // 2)
    return effective


AWP1221_WAVEFORM_DEPTH_CODES = {
    "512": 0,
    "256": 1,
}


def awp1221_waveform_depth_code(value: str) -> int:
    """Map a displayed depth label to the confirmed 1-bit depth_code, not a list index."""
    text = str(value).strip()
    if text not in AWP1221_WAVEFORM_DEPTH_CODES:
        raise ValueError(f"Unsupported waveform depth: {value}")
    return AWP1221_WAVEFORM_DEPTH_CODES[text]


def awp1221_phase_options(waveform: str, half_cycle_code: int) -> tuple[str, ...]:
    """Return phase labels for the current waveform while keeping the original 1-bit codes."""
    half_cycle_code = _require_unsigned(half_cycle_code, 0x3, "Half cycle")
    waveform_key = str(waveform).strip().lower()
    if waveform_key in {"sine", "sin"}:
        if half_cycle_code in (0b00, 0b11):
            return ("0 - -90° → -90°", "1 - 0° → 0°")
        if half_cycle_code == 0b01:
            return ("0 - -90° → 90°", "1 - 0° → 180°")
        return ("0 - 90° → -90°", "1 - 180° → 0°")
    return ("0 - Positive", "1 - Negative")


def awp1221_sine_preview_path(half_cycle_code: int, phase_code: int) -> tuple[float, float]:
    """Return the SINE preview start angle and span in degrees from the screenshot rules."""
    half_cycle_code = _require_unsigned(half_cycle_code, 0x3, "Half cycle")
    phase_code = _require_unsigned(phase_code, 0x1, "Phase")
    if half_cycle_code in (0b00, 0b11):
        return ((-90.0, 360.0) if phase_code == 0 else (0.0, 360.0))
    if half_cycle_code == 0b01:
        return ((-90.0, 180.0) if phase_code == 0 else (0.0, 180.0))
    return ((90.0, -180.0) if phase_code == 0 else (180.0, -180.0))


def awp1221_waveform_preview_value(
    waveform: str,
    position: float,
    half_cycle_code: int,
    phase_code: int,
) -> float:
    """Sample one normalized ROM preview point using the screenshot semantics."""
    position = max(0.0, min(1.0, float(position)))
    half_cycle_code = _require_unsigned(half_cycle_code, 0x3, "Half cycle")
    phase_code = _require_unsigned(phase_code, 0x1, "Phase")
    waveform_key = str(waveform).strip().lower()
    polarity = 1.0 if phase_code == 0 else -1.0
    if waveform_key in {"sine", "sin"}:
        start_angle, span = awp1221_sine_preview_path(half_cycle_code, phase_code)
        return math.sin(math.radians(start_angle + position * span))
    if waveform_key in {"gauss", "gaussian"}:
        if half_cycle_code == 0b01:
            center = 1.0
        elif half_cycle_code == 0b10:
            center = 0.0
        else:
            center = 0.5
        return polarity * math.exp(-((position - center) ** 2) / (2 * 0.16 * 0.16))
    if waveform_key in {"fixed-slope ramp", "slope"}:
        ramp = position if half_cycle_code != 0b10 else 1.0 - position
        return polarity * ramp
    if waveform_key == "dc":
        return polarity * 0.5
    return 0.0


def awp1221_waveform_preview_voltage(
    waveform: str,
    position: float,
    half_cycle_code: int,
    phase_code: int,
    coarse_code: int,
    fine_code: int,
    offset_code: int,
) -> float:
    shape = awp1221_waveform_preview_value(
        waveform, position, half_cycle_code, phase_code
    )
    amplitude = awp1221_waveform_peak_to_peak_voltage(coarse_code, fine_code) / 2
    return awp1221_waveform_offset_voltage(offset_code) + amplitude * shape


def merge_awp1221_waveform_word3(
    current_word: int,
    offset_code: int,
    vpk_code: int,
) -> int:
    """Replace bit11..5 offset and bit4..0 Vpk while preserving bit15..12."""
    current_word = _require_unsigned(current_word, 0xFFFF, "Waveform word 3")
    offset_code = _require_unsigned(
        offset_code,
        AWP1221_OFFSET_CODE_MASK,
        "Waveform offset",
    )
    vpk_code = _require_unsigned(vpk_code, AWP1221_VPK_CODE_MASK, "Vpk")
    field_mask = AWP1221_OFFSET_WORD_MASK | AWP1221_VPK_CODE_MASK
    return (current_word & ~field_mask) | (offset_code << 5) | vpk_code


def _schedule_ui(widget: tk.Misc, callback: Callable[..., None], *args: Any) -> None:
    try:
        widget.after(0, callback, *args)
    except tk.TclError:
        pass


def _run_ui_background_task(
    widget: tk.Misc,
    task: Callable[[], Any],
    *,
    on_done: Callable[[Any], None],
    on_error: Callable[[Exception], None],
) -> None:
    run_async_write(
        task,
        on_done=lambda result: _schedule_ui(widget, on_done, result),
        on_error=lambda error: _schedule_ui(widget, on_error, error),
    )


def _serial_batch(serial: Any) -> Any:
    snapshot_transaction = getattr(serial, "snapshot_transaction", None)
    return snapshot_transaction() if callable(snapshot_transaction) else nullcontext()


class AWP1221ModePage(AWP1221BoardPresetMixin, AWP1221RealtimeMixin, BasePage):
    """One independent AWP1221 mode view using the shared register contract."""

    LAYOUT_COLUMNS = 2
    PRESET_HEADER_COLUMNS = 5
    STAGE_REGISTERS = (0x08, 0x0D)
    STAGE_REGISTER_RANGES = (
        (0x08, 0x09, 0x0A, 0x0B, 0x0C),
        (0x0D, 0x0E, 0x0F, 0x10, 0x11),
    )
    WAVEFORM_SOURCES = ("ROM", "RAM")
    WAVEFORM_SOURCE_CODES = {"ROM": 0b10, "RAM": 0b01}
    MICRO_BIAS_OPTIONS = tuple(
        f"{code:02b} - +{awp1221_waveform_fine_amplitude_voltage(code):g} V"
        for code in range(AWP1221_FINE_AMPLITUDE_CODE_MASK + 1)
    )
    ROM_WAVEFORMS = ("SINE", "Gauss", "Fixed-slope Ramp", "DC", "SINC")
    ROM_WAVEFORM_CODES = {
        "SINE": 0b00,
        "Gauss": 0b01,
        "Fixed-slope Ramp": 0b10,
        "DC": 0b11,
    }
    ROM_DEPTH_OPTIONS = ("512", "256")
    HALF_CYCLE_OPTIONS_BY_WAVEFORM = {
        "SINE": (
            "00 - Full",
            "01 - First half",
            "10 - Second half",
            "11 - Full",
        ),
        "Gauss": (
            "00 - Full",
            "01 - Rising half",
            "10 - Falling half",
            "11 - Full",
        ),
        "Fixed-slope Ramp": (
            "00 - Full",
            "01 - Rising half",
            "10 - Falling half",
            "11 - Full",
        ),
        "DC": (
            "00 - Full",
            "01 - Half",
            "10 - Half",
            "11 - Full",
        ),
    }
    HALF_CYCLE_OPTIONS = (
        "00 - Full",
        "01 - First half",
        "10 - Second half",
        "11 - Full",
    )
    PHASE_OPTIONS = awp1221_phase_options("SINE", 0b00)
    VPK_OPTIONS = tuple(
        f"0x{code:02X} - {awp1221_waveform_coarse_amplitude_voltage(code):.2f} V"
        for code in range(AWP1221_VPK_CODE_MASK + 1)
    )
    OFFSET_OPTIONS = tuple(
        awp1221_waveform_offset_label(code)
        for code in range(AWP1221_OFFSET_CODE_MASK + 1)
    )
    ROM_REPEAT_OPTIONS = tuple(str(count) for count in range(1, 257))
    SINC_WAVEFORM = "SINC"
    _BUTTON_SOFTWARE_PRESET_STAGES = (
        {
            "ovv_time": "500 us",
            "jump_target": "Stage1",
            "event_mode": "press",
            "threshold": "400",
            "debounce": False,
            "use_high_12_bits": True,
            "wave": "slice18",
            "repeat": "0",
        },
        {
            "ovv_time": "500 us",
            "jump_target": "Stage0",
            "event_mode": "release",
            "threshold": "100",
            "debounce": False,
            "use_high_12_bits": True,
            "wave": "slice18",
            "repeat": "0",
        },
    )
    _BUTTON_SOFTWARE_PRESET_SETTINGS = {
        "debounce_time": "100",
        "sample_update": "4 us",
        "zero_time": "32 ms",
        "press_return_bit_time": "1600 us",
        "release_return_bit_time": "1600 us",
        "zps_enabled": True,
        "zps_detect": True,
        "sleep_sensitivity": False,
    }
    _BUTTON_SOFTWARE_PRESET_EXTRA_WRITES = ((0x1F, 0x0808),)
    BUTTON_SOFTWARE_PRESETS: tuple[dict[str, Any], ...] = (
        {
            "stages": (
                {
                    "ovv_time": "500 us",
                    "jump_target": "Stage1",
                    "event_mode": "press",
                    "threshold": "300",
                    "debounce": False,
                    "use_high_12_bits": True,
                    "wave": "slice18",
                    "repeat": "0",
                },
                {
                    "ovv_time": "500 us",
                    "jump_target": "Stage0",
                    "event_mode": "release",
                    "threshold": "200",
                    "debounce": False,
                    "use_high_12_bits": True,
                    "wave": "slice18",
                    "repeat": "0",
                },
            ),
            "settings": {
                "debounce_time": "100",
                "sample_update": "4 us",
                "zero_time": "32 ms",
                "press_return_bit_time": "800 us",
                "release_return_bit_time": "800 us",
                "zps_enabled": True,
                "zps_detect": True,
                "sleep_sensitivity": False,
            },
            "waveform": {
                "auto_serial": True,
                "serial": AWP1221_SINC_SERIAL_OPTIONS[0],
                "waveform": "SINC",
                "depth": "512",
                "max_voltage": "85",
                "min_voltage": "12",
                "times_us": ("7", "7", "40"),
                "sin_repeat": 1,
            },
            "extra_register_writes": _BUTTON_SOFTWARE_PRESET_EXTRA_WRITES,
        },
        {
            "stages": (
                {
                    "ovv_time": "500 us",
                    "jump_target": "Stage1",
                    "event_mode": "press",
                    "threshold": "600",
                    "debounce": False,
                    "use_high_12_bits": True,
                    "wave": "slice18",
                    "repeat": "0",
                },
                {
                    "ovv_time": "500 us",
                    "jump_target": "Stage0",
                    "event_mode": "release",
                    "threshold": "350",
                    "debounce": False,
                    "use_high_12_bits": True,
                    "wave": "slice18",
                    "repeat": "0",
                },
            ),
            "settings": {
                "debounce_time": "100",
                "sample_update": "4 us",
                "zero_time": "32 ms",
                "press_return_bit_time": "1600 us",
                "release_return_bit_time": "200 us",
                "zps_enabled": True,
                "zps_detect": True,
                "sleep_sensitivity": False,
            },
            "waveform": {
                "auto_serial": True,
                "serial": AWP1221_SINC_SERIAL_OPTIONS[0],
                "waveform": "SINC",
                "depth": "512",
                "max_voltage": "80",
                "min_voltage": "12",
                "times_us": ("8", "10", "50"),
                "sin_repeat": 1,
            },
            "extra_register_writes": _BUTTON_SOFTWARE_PRESET_EXTRA_WRITES,
        },
        {
            "stages": _BUTTON_SOFTWARE_PRESET_STAGES,
            "settings": _BUTTON_SOFTWARE_PRESET_SETTINGS,
            "waveform": {
                "auto_serial": True,
                "serial": AWP1221_SINC_SERIAL_OPTIONS[0],
                "waveform": "SINC",
                "depth": "512",
                "max_voltage": "80",
                "min_voltage": "20",
                "times_us": ("8", "8", "15"),
                "sin_repeat": 1,
            },
            "extra_register_writes": _BUTTON_SOFTWARE_PRESET_EXTRA_WRITES,
        },
    )

    def __init__(
        self,
        parent: tk.Misc,
        ctx: Any,
        *,
        tr: Callable[[str], str],
        device_addr: Callable[[], int],
        mode_key: str,
        auto_serial_var: tk.BooleanVar | None = None,
        shared_state_owner: AWP1221SharedStateOwner | None = None,
        host: Any | None = None,
        defer_build: bool = False,
    ) -> None:
        super().__init__(parent, ctx)
        self.tr = tr
        self.device_addr = device_addr
        self.mode_key = mode_key
        self._shared_state_owner = shared_state_owner
        self.host = host
        self._sram_editor: AWP1221SramEditor | None = None
        self._labels: dict[str, ttk.Label] = {}
        self._buttons: dict[str, ttk.Button] = {}
        self._translated_widgets: list[tuple[tk.Widget, str]] = []
        self._tooltips: list[SimpleTooltip] = []
        self._stage_vars: list[dict[str, Any]] = []
        self._stage_buttons: dict[int, ttk.Button] = {}
        self._stage_frames: dict[int, ttk.Frame] = {}
        self._button_stage_vars: list[dict[str, Any]] = []
        self._button_stage_buttons: dict[int, ttk.Button] = {}
        self._button_stage_frames: dict[int, ttk.Frame] = {}
        self._button_wave_combos: list[ttk.Combobox] = []
        self._full_wave_combos: list[ttk.Combobox] = []
        self._selector_widgets: dict[str, list[tk.Widget]] = {}
        self._auto_serial_check: ttk.Checkbutton | None = None
        self.debounce_time_var = tk.StringVar(value="100")
        self.adc_sample_update_var = tk.StringVar(value=AWP1221_ADC_SAMPLE_UPDATE_OPTIONS[0])
        self.adc_ldo_var = tk.StringVar(value=AWP1221_ADC_LDO_OPTIONS[0])
        self.zero_time_var = tk.StringVar(value=AWP1221_ZERO_TIME_OPTIONS[0])
        self.press_return_bit_time_var = tk.StringVar(value=AWP1221_RETURN_BIT_TIME_OPTIONS[1])
        self.release_return_bit_time_var = tk.StringVar(value=AWP1221_RETURN_BIT_TIME_OPTIONS[1])
        self.zps_enabled_var = tk.BooleanVar(value=True)
        self.zps_detect_var = tk.BooleanVar(value=True)
        self.sleep_sensitivity_var = tk.BooleanVar(value=False)
        self.adc_value_var = tk.StringVar(value="")
        self._init_adc_realtime()
        self._adc_trigger_read_active = False
        self._adc_detection_active = False
        self._adc_detection_device_addr: int | None = None
        self._waveform_play_active = False
        self._adc_read_in_flight = False
        self._adc_lifecycle_generation = 0
        self._adc_live_write_depth = 0
        self._verified_start_busy = False
        self.rom_slice_var = tk.StringVar(value="slice0")
        self.rom_source_var = tk.StringVar(value=self.WAVEFORM_SOURCES[0])
        self.rom_micro_bias_var = tk.StringVar(value=self.MICRO_BIAS_OPTIONS[0])
        self.rom_waveform_var = tk.StringVar(value=self.ROM_WAVEFORMS[0])
        self.rom_depth_var = tk.StringVar(value=self.ROM_DEPTH_OPTIONS[0])
        self.rom_half_cycle_var = tk.StringVar(value=self.HALF_CYCLE_OPTIONS[0])
        self.rom_phase_var = tk.StringVar(value=self.PHASE_OPTIONS[0])
        self.rom_offset_var = tk.StringVar(value=self.OFFSET_OPTIONS[0])
        self.rom_vpk_var = tk.StringVar(value=self.VPK_OPTIONS[0])
        self.rom_vpp_var = tk.StringVar(value=awp1221_waveform_peak_to_peak_label(0, 0))
        self.rom_point_time_var = tk.StringVar(value="1")
        self.rom_repeat_var = tk.StringVar(value="1")
        self.rom_infinite_loop_var = tk.BooleanVar(value=False)
        self.sinc_max_voltage_var = tk.StringVar(value="100")
        self.sinc_min_voltage_var = tk.StringVar(value="12")
        self.sinc_time_vars = [tk.StringVar(value=value) for value in ("10", "10", "10", "4")]
        self.sinc_sin_repeat_var = tk.StringVar(value="1")
        (
            self.sinc_time1_var,
            self.sinc_time2_var,
            self.sinc_time3_var,
            self.sinc_time4_var,
        ) = self.sinc_time_vars
        self.sinc_serial_var = tk.StringVar(value=AWP1221_SINC_SERIAL_OPTIONS[0])
        self.sinc_depth_var = tk.StringVar(value=self.ROM_DEPTH_OPTIONS[0])
        self.sinc_window_var = tk.StringVar(value="-")
        self.sinc_match_var = tk.StringVar(value="")
        sinc_defaults = (
            self.sinc_max_voltage_var.get(),
            self.sinc_min_voltage_var.get(),
            *(variable.get() for variable in self.sinc_time_vars),
            self.sinc_sin_repeat_var.get(),
            self.sinc_depth_var.get(),
            self.sinc_window_var.get(),
        )
        self._sinc_group_values: dict[str, tuple[str, ...]] = {
            serial: sinc_defaults for serial, _start_slice, _end_slice in AWP1221_SINC_SERIAL_GROUPS
        }
        self._sinc_active_group = AWP1221_SINC_SERIAL_GROUPS[0][0]
        self._sinc_verified_key: tuple[Any, ...] | None = None
        self.sinc_group_hint_var = tk.StringVar(value="")
        self.sinc_haptic_period_var = tk.StringVar(value="")
        self.auto_serial_var = (
            auto_serial_var if auto_serial_var is not None else tk.BooleanVar(value=True)
        )
        self.rom_metric_var = tk.StringVar(value="")
        self.rom_frequency_var = tk.StringVar(value="")
        self.board_slot_var = tk.StringVar(value="0")
        self._visible_stage_index = 0
        self._preparing = defer_build
        self._build_steps = self._initialize_steps()
        if not defer_build:
            for _ in self._build_steps:
                pass

    def _initialize_steps(self) -> Iterator[None]:
        yield from self._build_ui_steps()
        if self.mode_key == "button_mode":
            self._init_board_recipe_tracking()
        self._bind_realtime_overlay_traces()
        self._auto_serial_trace = self.auto_serial_var.trace_add(
            "write",
            lambda *_: self._sync_auto_serial_page_state(),
        )
        self._adc_trigger_log_listener = self._handle_adc_trigger_log
        self.ctx.serial.add_log_listener(self._adc_trigger_log_listener)
        self._preparing = False

    def _build_ui(self) -> None:
        for _ in self._build_ui_steps():
            pass

    def _build_ui_steps(self) -> Iterator[None]:
        if self.mode_key == "button_mode":
            yield from self._build_button_mode_ui_steps()
            return

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = self.make_title(self, self.mode_key, show_logo=False)
        header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())
        yield
        self.mode_title_label = header.grid_slaves(row=0, column=0)[0]

        preset_frame = self.make_preset_frame(header)
        yield
        preset_keys = (
            "awp1221_preset_1_reserved",
            "awp1221_preset_2_reserved",
            "awp1221_preset_3_reserved",
            "awp1221_import_reserved",
            "awp1221_export_reserved",
        )
        for column, key in enumerate(preset_keys):
            button = ttk.Button(preset_frame, style="Bg.TButton", state="disabled")
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0))
            self._buttons[key] = button

        body = ttk.Frame(self, style="App.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        # Keep the ADC controls and waveform/log area close to the same width.
        body.columnconfigure(0, weight=1, uniform="awp1221_full_mode_panels")
        body.columnconfigure(1, weight=1, uniform="awp1221_full_mode_panels")
        yield
        body.rowconfigure(0, weight=1)

        left_scroll, left = self.make_vertical_scroll_area(body, propagate_width=True)
        left_scroll.horizontal = False
        left_scroll.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.configure(style="Panel.TFrame", padding=(14, 14, 14, 28))
        left.columnconfigure(0, weight=1)
        yield
        self._build_chip_en_section(left, row=0)
        self._labels["awp1221_adc"] = ttk.Label(left, style="SectionTitle.TLabel")
        self._labels["awp1221_adc"].grid(row=1, column=0, sticky="w", pady=(0, 10))

        stage_selector = ttk.Frame(left, style="Panel.TFrame")
        stage_selector.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for stage_index in range(2):
            stage_selector.columnconfigure(stage_index, weight=1)
            button = ttk.Button(
                stage_selector,
                text=f"Stage{stage_index}",
                style="Secondary.TButton",
                command=lambda index=stage_index: self._show_stage(index),
            )
            button.grid(row=0, column=stage_index, sticky="ew", padx=(0 if stage_index == 0 else 4, 0))
            self._stage_buttons[stage_index] = button
        yield

        stage_container = ttk.Frame(left, style="Panel.TFrame")
        stage_container.grid(row=3, column=0, sticky="ew")
        stage_container.columnconfigure(0, weight=1)
        for stage_index in range(2):
            stage = ttk.Frame(stage_container, style="Panel.TFrame")
            stage.grid(row=0, column=0, sticky="ew")
            stage.grid_remove()
            self._stage_frames[stage_index] = stage
            yield from self._build_adc_stage_steps(stage, stage_index)

        yield from self._build_adc_settings_steps(left, row=4)
        yield from self._build_control_buttons_steps(left, row=5)
        yield
        self._build_gpio_settings(left, row=6)

        right = ttk.Frame(body, style="Panel.TFrame", padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=3)
        right.rowconfigure(2, weight=2)
        yield
        self._labels["awp1221_basic_parameters"] = ttk.Label(right, style="Muted.TLabel")
        self._labels["awp1221_basic_parameters"].grid(row=0, column=0, sticky="w", padx=4, pady=(4, 10))

        self.tools_notebook = ttk.Notebook(right)
        self.tools_notebook.grid(row=1, column=0, sticky="nsew")
        waveform_tab = ttk.Frame(self.tools_notebook, style="Panel.TFrame", padding=14)
        rom_tab = ttk.Frame(self.tools_notebook, style="Panel.TFrame", padding=14)
        yield
        self.tools_notebook.add(waveform_tab, text=self.tr("awp1221_waveform_tab"))
        self.tools_notebook.add(rom_tab, text=self.tr("awp1221_rom_tab"))
        waveform_tab.columnconfigure(0, weight=1)
        waveform_tab.rowconfigure(0, weight=1)
        self._sram_editor = AWP1221SramEditor(waveform_tab, self.ctx, self)
        self._sram_editor.grid(row=0, column=0, sticky="nsew")
        yield
        yield from self._build_waveform_selector_steps(rom_tab)

        log_frame = self.make_serial_log(right, height=7, subscribe=True)
        log_frame.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        self._hide_serial_log_hscroll(log_frame)

        self._show_stage(0)
        self.refresh_language()
        yield

    def _build_button_mode_ui(self) -> None:
        for _ in self._build_button_mode_ui_steps():
            pass

    def _build_button_mode_ui_steps(self) -> Iterator[None]:
        self.columnconfigure(0, weight=1)
        self._build_button_preset_header()
        body = ttk.Frame(self, style="App.TFrame")
        body.grid(row=1, column=0, sticky="ew")
        body.columnconfigure(0, weight=1)
        body.bind("<Configure>", self._queue_communication_log_layout)
        yield

        waveform = self._button_section(body, "awp1221_button_wave_settings", row=0)
        waveform.columnconfigure(0, weight=1)
        selector_holder = ttk.Frame(waveform, style="Panel.TFrame", height=520)
        selector_holder.grid(row=0, column=0, sticky="ew")
        selector_holder.grid_propagate(False)
        selector_holder.columnconfigure(0, weight=1)
        yield
        self._sram_selector_holder = selector_holder
        yield from self._build_waveform_selector_steps(selector_holder)
        self._waveform_selector_title.grid_remove()
        tabs = ttk.Frame(self._waveform_selector_shell, style="Panel.TFrame")
        tabs.grid(row=0, column=0, sticky="ew", padx=(0, 18), pady=(0, 8))
        self._button_wave_tabs = {}
        yield
        for column, (key, title) in enumerate((
            ("sinc", "awp1221_sinc_tab"), ("rom", "awp1221_button_rom_tab"),
            ("sram", "awp1221_button_sram_tab"),
        )):
            button = ttk.Button(tabs, style="Secondary.TButton",
                                command=lambda value=key: self._select_button_wave_tab(value))
            button.grid(row=0, column=column, sticky="ew", padx=(0, 4))
            tabs.columnconfigure(column, weight=1)
            self._register_text(button, title)
            self._button_wave_tabs[key] = button
        self._waveform_selector_preview.grid_configure(row=0, rowspan=2)
        selector_holder.rowconfigure(0, weight=1)
        # The ROM/SINC view does not need the hidden SRAM controls. Build
        # those once on RAM selection (or explicit editor access) instead.
        self._compact_button_waveform_form()

        middle = ttk.Frame(body, style="App.TFrame")
        middle.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        yield
        middle.columnconfigure(0, weight=2, uniform="button_adc_panels")
        middle.columnconfigure(1, weight=3, uniform="button_adc_panels")
        diagram = self._button_section(middle, "awp1221_adc_diagram", row=0)
        diagram.grid_configure(column=0, sticky="nsew", padx=(0, 10))
        diagram.columnconfigure(0, weight=1)
        diagram.rowconfigure(0, weight=1)
        yield
        self._button_adc_canvas = tk.Canvas(diagram, width=260, height=200,
                                             highlightthickness=0)
        self._button_adc_canvas.grid(row=0, column=0, sticky="nsew")
        self._button_adc_canvas.bind("<Configure>", lambda _event: self._schedule_button_adc_diagram())
        adc_panel = self._button_section(middle, "awp1221_button_adc_settings", row=0)
        adc_panel.grid_configure(column=1, sticky="nsew")
        adc_panel.columnconfigure(0, weight=1)
        yield
        yield from self._build_button_adc_panel_steps(adc_panel, row=0)
        yield from self._build_adc_settings_steps(adc_panel, row=2)

        lower = ttk.Frame(body, style="App.TFrame")
        lower.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        lower.columnconfigure(1, weight=3)
        lower.columnconfigure(2, weight=2)
        yield
        left_status = ttk.Frame(lower, style="App.TFrame")
        left_status.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left_status.columnconfigure(0, weight=1)
        en = self._button_section(left_status, "awp1221_chip_en_title", row=0)
        self._build_chip_en_section(en, row=0)
        pressure = self._button_section(left_status, "sense_status_title", row=1)
        yield
        pressure.grid_configure(pady=(8, 0), sticky="nsew")
        pressure.columnconfigure(0, weight=1)
        self._pressure_status_label = tk.Label(pressure, width=11, pady=5)
        self._pressure_status_label.grid(row=0, column=0, sticky="ew")
        hint = ttk.Label(pressure, style="Muted.TLabel", wraplength=135)
        hint.grid(row=1, column=0, sticky="w", pady=(6, 0))
        yield
        self._register_text(hint, "sense_status_hint")
        self._tooltips.append(SimpleTooltip(self._pressure_status_label,
            lambda: self.tr("awp1221_pressure_status_tip")))
        self._set_pressure_status("idle")
        readings = self._button_section(lower, "awp1221_adc_readings", row=0)
        readings.grid_configure(column=1, sticky="nsew", padx=(0, 10))
        readings.columnconfigure(0, weight=1)
        yield
        yield from self._build_adc_readout_steps(readings, row=0)
        switches = self._button_section(lower, "awp1221_control_buttons", row=0)
        switches.grid_configure(column=2, sticky="nsew")
        switches.columnconfigure(0, weight=1)
        yield from self._build_control_buttons_steps(switches, row=0)

        details = ttk.Frame(body, style="Panel.TFrame", padding=10)
        yield
        details.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        details.columnconfigure(0, weight=1)
        extra = ttk.Frame(details, style="Panel.TFrame")
        extra.columnconfigure(0, weight=1)
        self._communication_log_panel = extra
        toggle = ttk.Button(details, style="Secondary.TButton",
                            command=self._toggle_communication_log)
        yield
        toggle.grid(row=0, column=0, sticky="w")
        self._register_text(toggle, "awp1221_communication_log")
        self._communication_log_toggle = toggle
        log_frame = self.make_serial_log(extra, height=3, subscribe=True)
        log_frame.grid(row=0, column=0, sticky="ew", pady=(8, 0))
        self._hide_serial_log_hscroll(log_frame)
        yield
        self.refresh_language()
        self._schedule_button_rom_preview()

    def _hide_serial_log_hscroll(self, frame: ttk.Frame) -> None:
        text = getattr(self, "_serial_log_text", None)
        if text is not None:
            text.configure(wrap="word")
        for child in frame.winfo_children():
            if child.winfo_class() != "TScrollbar":
                continue
            try:
                if str(child.cget("orient")) == "horizontal":
                    child.grid_remove()
            except tk.TclError:
                pass

    def _toggle_communication_log(self) -> None:
        panel = self._communication_log_panel
        if panel.winfo_manager():
            panel.grid_remove()
        else:
            panel.grid(row=1, column=0, sticky="ew")
        self._queue_communication_log_layout()

    def _queue_communication_log_layout(self, _event: tk.Event | None = None) -> None:
        if getattr(self, '_preparing', False):
            return
        if getattr(self, "_comm_log_layout_job", None) is not None:
            return
        def run() -> None:
            self._comm_log_layout_job = None
            self._sync_communication_log_layout()
        try:
            self._comm_log_layout_job = self.after_idle(run)
        except tk.TclError:
            self._comm_log_layout_job = None

    def _sync_communication_log_layout(self) -> None:
        if self.host is None or getattr(self, "_comm_log_syncing", False):
            return
        area = self.host.mode_scroll
        self._comm_log_syncing = True
        try:
            area._sync_scroll_region()
            if self._communication_log_panel.winfo_manager():
                self._queue_communication_log_scroll()
        finally:
            self._comm_log_syncing = False

    def _queue_communication_log_scroll(self) -> None:
        if getattr(self, "_comm_log_scroll_job", None) is not None:
            return
        def run() -> None:
            self._comm_log_scroll_job = None
            try:
                host = self.host
                if host is None or not self._communication_log_panel.winfo_manager():
                    return
                host.mode_scroll.canvas.yview_moveto(1)
            except tk.TclError:
                pass
        try:
            self._comm_log_scroll_job = self.after_idle(run)
        except tk.TclError:
            self._comm_log_scroll_job = None

    def _button_section(self, parent: tk.Misc, key: str, *, row: int) -> ttk.LabelFrame:
        section = ttk.LabelFrame(parent, padding=10)
        section.grid(row=row, column=0, sticky="ew")
        self._register_text(section, key)
        return section

    def _select_button_wave_tab(self, key: str) -> None:
        if key == "sinc":
            self.auto_serial_var.set(True)
            self.rom_source_var.set("ROM")
            self.rom_waveform_var.set("SINC")
        else:
            if self._sinc_selected():
                self.rom_waveform_var.set("SINE")
            self.rom_source_var.set("RAM" if key == "sram" else "ROM")
        self._refresh_waveform_controls()

    def _compact_button_waveform_form(self) -> None:
        controls = self._button_waveform_controls
        for column in range(3):
            controls.columnconfigure(column, weight=1, uniform="button_wave_fields")
        for widgets in self._selector_widgets.values():
            for widget in widgets:
                if widget.master != controls:
                    continue
                if isinstance(widget, (ttk.Entry, ttk.Combobox)):
                    widget.configure(width=16)
                    self._tooltips.append(SimpleTooltip(widget, lambda value=widget: value.get()))
                elif isinstance(widget, ttk.Label):
                    widget.configure(wraplength=200)
        self._button_waveform_layout_ready = True
        self._waveform_selector_form.configure(width=680)
        self._waveform_selector_form.grid_propagate(False)
        self._waveform_selector_shell.columnconfigure(0, weight=0, minsize=680)
        self._waveform_selector_shell.columnconfigure(1, weight=3)
        self.rom_canvas.configure(height=400)
        self._tooltips.append(SimpleTooltip(self._selector_widgets["awp1221_rom_offset"][0],
                              lambda: self.tr("awp1221_waveform_word3_pending")))
        self._refresh_waveform_controls()

    def _layout_button_waveform_form(self) -> None:
        if not getattr(self, "_button_waveform_layout_ready", False):
            return
        if self._sram_source_selected():
            self._auto_serial_check.grid_remove()
            self._set_selector_row_visible("awp1221_rom_waveform", False)
            return
        controls = self._button_waveform_controls
        self._auto_serial_check.grid_configure(row=0, column=0, columnspan=2, sticky="w")
        self._set_selector_row_visible("awp1221_rom_source", False)
        self._set_selector_row_visible("awp1221_waveform_word3_pending", False)
        fields = (
            "awp1221_sinc_serial_group", "awp1221_rom_slice", "awp1221_rom_waveform",
            "awp1221_sinc_depth", "awp1221_sinc_max_voltage", "awp1221_sinc_min_voltage",
            "awp1221_sinc_slice_group", "awp1221_sinc_time1", "awp1221_sinc_time2",
            "awp1221_sinc_time3", "awp1221_sinc_time4", "awp1221_sinc_sin_repeat",
            "awp1221_rom_amplitude", "awp1221_rom_micro_bias", "awp1221_rom_peak_to_peak",
            "awp1221_rom_depth", "awp1221_rom_half_cycle", "awp1221_rom_phase",
            "awp1221_rom_offset", "awp1221_rom_point_time", "awp1221_rom_repeat",
        )
        visible = [key for key in fields if any(w.winfo_manager() == "grid"
                   for w in self._selector_widgets.get(key, ()))]
        for position, key in enumerate(visible):
            field_row, column = divmod(position, 3)
            for index, widget in enumerate(self._selector_widgets[key]):
                widget.grid_configure(row=1 + field_row * 2 + min(index, 1),
                                      column=column, columnspan=1, sticky="ew",
                                      padx=(0, 10), pady=(0, 5))
        infinite = self._selector_widgets["awp1221_rom_infinite_loop"][0]
        infinite.grid_configure(row=0, column=2, columnspan=1, sticky="e")
        if self._sinc_selected() or self._sram_source_selected():
            infinite.grid_remove()
        info_row = 1 + ((len(visible) + 2) // 3) * 2
        for key in ("awp1221_sinc_depth_hint", "awp1221_sinc_match",
                    "awp1221_sinc_haptic_period", "awp1221_waveform_word3_pending"):
            for widget in self._selector_widgets.get(key, ()):
                visible_info = bool(widget.winfo_manager())
                widget.grid_configure(row=info_row, column=0, columnspan=3, sticky="w",
                                      padx=0, pady=(2, 3))
                widget.configure(wraplength=640)
                if not visible_info:
                    widget.grid_remove()
            info_row += 1
        hint = self._sinc_min_voltage_help
        if self._sinc_selected() and "awp1221_sinc_min_voltage" in visible:
            position = visible.index("awp1221_sinc_min_voltage")
            field_row, column = divmod(position, 3)
            hint.grid(row=1 + field_row * 2, column=column, columnspan=1,
                      sticky="e", padx=(0, 12))
        else:
            hint.grid_remove()

    def _layout_full_waveform_form(self) -> None:
        if self.mode_key == "button_mode":
            return
        scroll = getattr(self, "_waveform_selector_scroll", None)
        if scroll is None:
            return
        scroll.horizontal = False
        content = scroll.content
        content.columnconfigure(0, weight=1, minsize=0)
        content.columnconfigure(1, weight=0, minsize=0)
        content.columnconfigure(2, weight=0, minsize=0)
        order = (
            "awp1221_auto_serial",
            "awp1221_sinc_serial_group",
            "awp1221_rom_slice",
            "awp1221_rom_source",
            "awp1221_rom_waveform",
            "awp1221_sinc_slice_group",
            "awp1221_sinc_depth",
            "awp1221_sinc_depth_hint",
            "awp1221_sinc_max_voltage",
            "awp1221_sinc_min_voltage",
            "awp1221_sinc_time1",
            "awp1221_sinc_time2",
            "awp1221_sinc_time3",
            "awp1221_sinc_time4",
            "awp1221_sinc_sin_repeat",
            "awp1221_rom_micro_bias",
            "awp1221_rom_amplitude",
            "awp1221_rom_peak_to_peak",
            "awp1221_rom_depth",
            "awp1221_rom_half_cycle",
            "awp1221_rom_phase",
            "awp1221_rom_offset",
            "awp1221_rom_point_time",
            "awp1221_rom_repeat",
            "awp1221_rom_infinite_loop",
            "awp1221_sinc_match",
            "awp1221_sinc_haptic_period",
            "awp1221_sinc_group_hint",
            "awp1221_sinc_hint",
            "awp1221_waveform_word3_pending",
        )
        row = 0
        min_voltage_row = None
        for key in order:
            widgets = [widget for widget in self._selector_widgets.get(key, ())
                       if widget.winfo_manager() == "grid"]
            if not widgets:
                continue
            for widget in widgets:
                if isinstance(widget, (ttk.Combobox, ttk.Entry)):
                    widget.configure(width=12)
                    widget.grid_configure(row=row, column=0, columnspan=2, sticky="ew",
                                          padx=0, pady=(0, 6))
                elif isinstance(widget, ttk.Checkbutton):
                    widget.grid_configure(row=row, column=0, columnspan=2, sticky="w",
                                          padx=0, pady=(0, 6))
                else:
                    try:
                        widget.configure(wraplength=210)
                    except tk.TclError:
                        pass
                    widget.grid_configure(row=row, column=0, columnspan=2, sticky="ew",
                                          padx=0, pady=(4, 0))
                    if key == "awp1221_sinc_min_voltage":
                        min_voltage_row = row
                row += 1
        help_widget = getattr(self, "_sinc_min_voltage_help", None)
        if help_widget is not None:
            if min_voltage_row is None:
                help_widget.grid_remove()
            else:
                help_widget.grid(row=min_voltage_row, column=1, columnspan=1,
                                 sticky="e", padx=(4, 0), pady=0)
        for widget in self._selector_widgets.get("awp1221_rom_preview_captions", ()):
            try:
                widget.configure(wraplength=210)
            except tk.TclError:
                pass

    def _schedule_button_adc_diagram(self) -> None:
        if getattr(self, "_button_adc_diagram_job", None) is not None:
            return
        def run() -> None:
            self._button_adc_diagram_job = None
            try:
                if not self.winfo_ismapped():
                    return
                canvas = getattr(self, "_button_adc_canvas", None)
                if canvas is not None and canvas.winfo_width() <= 1:
                    retries = getattr(self, "_button_adc_diagram_retries", 0)
                    if retries < 2:
                        self._button_adc_diagram_retries = retries + 1
                        self._schedule_button_adc_diagram()
                    return
                self._button_adc_diagram_retries = 0
                self._draw_button_adc_diagram()
            except tk.TclError:
                pass
        try:
            self._button_adc_diagram_job = self.after_idle(run)
        except tk.TclError:
            self._button_adc_diagram_job = None

    def _button_adc_diagram_signature(self):
        canvas = getattr(self, "_button_adc_canvas", None)
        if canvas is None:
            return None
        try:
            size = (canvas.winfo_width(), canvas.winfo_height())
        except tk.TclError:
            return None
        if size[0] <= 1 or size[1] <= 1:
            return None
        stages = tuple(
            (variables["event_mode"].get(), variables["threshold"].get(), variables["jump_target"].get())
            for variables in getattr(self, "_button_stage_vars", ())
        )
        colors = self.colors
        return (
            size, stages, getattr(self, "_visible_button_stage_index", 0),
            colors.get("primary"), colors.get("canvas_bg"), colors.get("muted"), colors.get("border"),
            getattr(self, "language", None),
        )

    @staticmethod
    def _signed_threshold_value(text: Any) -> int | None:
        try:
            value = awp1221_parse_threshold_text(str(text))
        except (TypeError, ValueError):
            return None
        return value - 0x1000 if value & 0x800 else value

    @staticmethod
    def _stage_index_from_text(text: Any) -> int | None:
        digits = "".join(ch for ch in str(text) if ch.isdigit())
        return int(digits) if digits else None

    def _mix_canvas_color(self, canvas: tk.Canvas, color: str, background: str, ratio: float) -> str:
        try:
            fg = canvas.winfo_rgb(color)
            bg = canvas.winfo_rgb(background)
        except tk.TclError:
            return background
        mixed = (int((f * ratio + b * (1 - ratio)) / 257) for f, b in zip(fg, bg))
        return "#{:02X}{:02X}{:02X}".format(*mixed)

    def _draw_button_adc_diagram(self) -> None:
        canvas = getattr(self, "_button_adc_canvas", None)
        if canvas is None:
            return
        signature = self._button_adc_diagram_signature()
        if signature is None or signature == getattr(self, "_button_adc_diagram_drawn", None):
            return
        self._button_adc_diagram_drawn = signature
        colors = self.colors
        background = colors.get("canvas_bg", "#FFFFFF")
        primary = colors.get("primary", "#2F6FDE")
        accent = "#E8833A"
        muted = colors.get("muted", "#6B7280")
        canvas.configure(bg=background)
        canvas.delete("all")
        width, height = max(240, canvas.winfo_width()), max(200, canvas.winfo_height())
        left, right = 16, width - 16
        band_top, plot_top, plot_bottom = 24, 62, height - 26
        plot_width = right - left

        # Resolve which stage detects press / release from the live Stage settings.
        stages = getattr(self, "_button_stage_vars", [])
        press_stage = release_stage = None
        for index, variables in enumerate(stages):
            mode = variables["event_mode"].get()
            if mode == "press" and press_stage is None:
                press_stage = index
            elif mode == "release" and release_stage is None:
                release_stage = index
        press_value = (self._signed_threshold_value(stages[press_stage]["threshold"].get())
                       if press_stage is not None else None)
        release_value = (self._signed_threshold_value(stages[release_stage]["threshold"].get())
                         if release_stage is not None else None)

        values = [value for value in (press_value, release_value) if value is not None]
        low, high = min([0, *values]), max([0, *values])
        span = max(high - low, 200)
        base = low - span * 0.04
        peak = high + span * 0.35

        def y_of(value: float) -> float:
            return plot_bottom - (value - base) / (peak - base) * (plot_bottom - plot_top)

        # Trapezoid ADC curve: idle -> press ramp -> hold -> release ramp -> idle.
        x_rise0, x_rise1 = left + plot_width * .05, left + plot_width * .32
        x_fall0, x_fall1 = left + plot_width * .60, left + plot_width * .88
        y_base, y_peak = y_of(base), y_of(peak)

        def rise_x(value: float) -> float:
            return x_rise0 + (value - base) / (peak - base) * (x_rise1 - x_rise0)

        def fall_x(value: float) -> float:
            return x_fall0 + (peak - value) / (peak - base) * (x_fall1 - x_fall0)

        x_press = rise_x(press_value) if press_value is not None else x_rise1
        x_release = fall_x(release_value) if release_value is not None else x_fall1

        # Stage bands: idle stage -> (press) -> target stage -> (release) -> target stage.
        def stage_target(stage_index: int | None) -> int | None:
            if stage_index is None or stage_index >= len(stages):
                return None
            return self._stage_index_from_text(stages[stage_index]["jump_target"].get())

        first_stage = press_stage if press_stage is not None else 0
        middle_stage = stage_target(press_stage)
        if middle_stage is None:
            middle_stage = release_stage if release_stage is not None else 1
        last_stage = stage_target(release_stage)
        if last_stage is None:
            last_stage = first_stage
        visible = getattr(self, "_visible_button_stage_index", 0)
        stage_colors = (primary, accent)

        def stage_caption(stage_index: int) -> str:
            mode = (stages[stage_index]["event_mode"].get()
                    if 0 <= stage_index < len(stages) else "")
            key = ("awp1221_diagram_detect_press" if mode == "press"
                   else "awp1221_diagram_detect_release")
            return f"Stage{stage_index}\n{self.tr(key)}"

        bands = ((left, x_press, first_stage), (x_press, x_release, middle_stage),
                 (x_release, right, last_stage))
        for x0, x1, stage_index in bands:
            if x1 - x0 < 1:
                continue
            color = stage_colors[stage_index % 2]
            selected = stage_index == visible
            canvas.create_rectangle(
                x0, band_top, x1, plot_bottom, outline="",
                fill=self._mix_canvas_color(canvas, color, background, .20 if selected else .07),
            )
            canvas.create_text(
                (x0 + x1) / 2, band_top + 4, text=stage_caption(stage_index),
                anchor="n", justify="center",
                fill=color if selected else self._mix_canvas_color(canvas, color, background, .65),
            )

        canvas.create_line(left, plot_bottom, right, plot_bottom, fill=colors.get("border", muted))
        canvas.create_line(left, y_base, x_rise0, y_base, x_rise1, y_peak, x_fall0, y_peak,
                           x_fall1, y_base, right, y_base, fill=primary, width=2)

        def keep_inside(item: int) -> None:
            box = canvas.bbox(item)
            if not box:
                return
            if box[2] > width - 4:
                canvas.move(item, width - 4 - box[2], 0)
            elif box[0] < 4:
                canvas.move(item, 4 - box[0], 0)

        markers = (
            (press_value, x_press, middle_stage, "awp1221_diagram_press", primary, "se", -7, 0),
            (release_value, x_release, last_stage, "awp1221_diagram_release", accent, "sw", 7, 16),
        )
        for value, x, target, key, color, anchor, dx, lift in markers:
            if value is None:
                continue
            y = y_of(value)
            canvas.create_line(left, y, right, y, fill=color, dash=(4, 3))
            canvas.create_line(x, band_top, x, plot_bottom, fill=color, dash=(2, 3))
            canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill=color, outline=background, width=2)
            label = canvas.create_text(x + dx, y - 4, anchor=anchor, fill=color,
                                       text=f"{self.tr(key)} {value}")
            keep_inside(label)
            # Jump marker under the plateau, where the curve leaves free space.
            jump = canvas.create_text(
                x + (4 if anchor == "se" else -4), plot_bottom - 4 - lift,
                anchor="sw" if anchor == "se" else "se", fill=color,
                text=f"\u2192 Stage{target}",
            )
            keep_inside(jump)

        canvas.create_text(left, 6, text=self.tr("awp1221_adc_diagram_hint"),
                           anchor="nw", fill=muted)
        canvas.create_text(left, plot_bottom + 6, text=self.tr("awp1221_time_axis"),
                           anchor="nw", fill=muted)

    def _build_waveform_selector(self, parent: ttk.Frame) -> None:
        for _ in self._build_waveform_selector_steps(parent):
            pass

    def _build_waveform_selector_steps(self, parent: ttk.Frame) -> Iterator[None]:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)
        self._waveform_selector_shell = ttk.Frame(parent, style="Panel.TFrame")
        self._waveform_selector_shell.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self._waveform_selector_shell.columnconfigure(0, weight=1)
        yield
        self._waveform_selector_shell.columnconfigure(1, weight=1)
        self._waveform_selector_shell.rowconfigure(1, weight=1)
        title = ttk.Label(self._waveform_selector_shell, style="SectionTitle.TLabel")
        title.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self._register_text(title, "awp1221_rom_selector_title")
        self._waveform_selector_title = title
        yield

        form_column = ttk.Frame(self._waveform_selector_shell, style="Panel.TFrame")
        form_column.grid(row=1, column=0, sticky="nsew", padx=(0, 18))
        form_column.columnconfigure(0, weight=1)
        form_column.rowconfigure(0, weight=1)
        self._waveform_selector_form = form_column
        if self.mode_key == "button_mode":
            controls = ttk.Frame(form_column, style="Panel.TFrame")
            controls.grid(row=0, column=0, sticky="nsew")
            self._button_waveform_controls = controls
        else:
            controls_scroll, controls = self.make_vertical_scroll_area(form_column)
            controls_scroll.grid(row=0, column=0, sticky="nsew")
            controls_scroll.horizontal = False
            controls_scroll.canvas.configure(width=200)
            self._waveform_selector_scroll = controls_scroll
        yield
        controls.configure(style="Panel.TFrame")
        controls.columnconfigure(1, weight=1)

        auto_serial = ttk.Checkbutton(
            controls,
            variable=self.auto_serial_var,
            command=self._on_auto_serial_changed,
        )
        auto_serial.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._register_text(auto_serial, "awp1221_auto_serial")
        self._selector_widgets["awp1221_auto_serial"] = [auto_serial]
        yield
        self._auto_serial_check = auto_serial
        self._rom_combo(
            controls,
            1,
            "awp1221_sinc_serial_group",
            self.sinc_serial_var,
            AWP1221_SINC_SERIAL_OPTIONS,
        )
        self._rom_combo(controls, 2, "awp1221_rom_waveform", self.rom_waveform_var, self.ROM_WAVEFORMS)
        self._rom_combo(
            controls,
            3,
            "awp1221_sinc_slice_group",
            self.sinc_window_var,
            ("-",),
        )
        self._rom_combo(
            controls,
            4,
            "awp1221_sinc_depth",
            self.sinc_depth_var,
            self.ROM_DEPTH_OPTIONS,
        )
        sinc_depth_hint = ttk.Label(
            controls,
            style="Muted.TLabel",
            wraplength=340,
        )
        yield
        sinc_depth_hint.grid(row=5, column=0, columnspan=3, sticky="w")
        self._selector_widgets["awp1221_sinc_depth_hint"] = [sinc_depth_hint]
        self._rom_entry(controls, 6, "awp1221_sinc_max_voltage", self.sinc_max_voltage_var)
        self._rom_entry(controls, 7, "awp1221_sinc_min_voltage", self.sinc_min_voltage_var)
        self._add_help_button(controls, 7, 2, "awp1221_sinc_min_voltage_tip")
        self._sinc_min_voltage_help = self._tooltips[-1].widget
        yield
        for index, variable in enumerate(self.sinc_time_vars):
            self._rom_entry(
                controls,
                8 + index,
                f"awp1221_sinc_time{index + 1}",
                variable,
            )
        self._rom_entry(controls, 12, "awp1221_sinc_sin_repeat", self.sinc_sin_repeat_var)
        sinc_match = ttk.Label(
            controls,
            textvariable=self.sinc_match_var,
            style="Muted.TLabel",
            wraplength=280,
        )
        sinc_match.grid(row=13, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self._selector_widgets["awp1221_sinc_match"] = [sinc_match]
        sinc_period = ttk.Label(
            controls, textvariable=self.sinc_haptic_period_var,
            style="Panel.TLabel", wraplength=280,
        )
        yield
        sinc_period.grid(row=14, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._selector_widgets["awp1221_sinc_haptic_period"] = [sinc_period]
        sinc_group_hint = ttk.Label(
            controls,
            textvariable=self.sinc_group_hint_var,
            style="Muted.TLabel",
            wraplength=280,
        )
        self._selector_widgets["awp1221_sinc_group_hint"] = [sinc_group_hint]
        sinc_hint = ttk.Label(controls, style="Muted.TLabel", wraplength=280)
        self._register_text(sinc_hint, "awp1221_sinc_hint")
        yield
        self._selector_widgets["awp1221_sinc_hint"] = [sinc_hint]

        self._rom_combo(
            controls,
            15,
            "awp1221_rom_slice",
            self.rom_slice_var,
            tuple(f"slice{i}" for i in range(AWP1221_WAVEFORM_SLICE_COUNT)),
        )
        self._rom_combo(controls, 16, "awp1221_rom_source", self.rom_source_var, self.WAVEFORM_SOURCES)
        self._rom_combo(
            controls,
            17,
            "awp1221_rom_micro_bias",
            self.rom_micro_bias_var,
            self.MICRO_BIAS_OPTIONS,
        )
        self._rom_combo(controls, 18, "awp1221_rom_amplitude", self.rom_vpk_var, self.VPK_OPTIONS)
        peak_to_peak_label = self._form_label(controls, "awp1221_rom_peak_to_peak", row=19, column=0)
        yield
        peak_to_peak_value = ttk.Label(
            controls,
            textvariable=self.rom_vpp_var,
            style="Muted.TLabel",
        )
        peak_to_peak_value.grid(row=19, column=1, sticky="w", pady=5)
        self._selector_widgets["awp1221_rom_peak_to_peak"] = [
            peak_to_peak_label,
            peak_to_peak_value,
        ]
        self._rom_combo(controls, 20, "awp1221_rom_depth", self.rom_depth_var, self.ROM_DEPTH_OPTIONS)
        self._rom_combo(
            controls,
            21,
            "awp1221_rom_half_cycle",
            self.rom_half_cycle_var,
            self.HALF_CYCLE_OPTIONS,
        )
        self._rom_combo(controls, 22, "awp1221_rom_phase", self.rom_phase_var, self.PHASE_OPTIONS)
        yield
        self._rom_combo(controls, 23, "awp1221_rom_offset", self.rom_offset_var, self.OFFSET_OPTIONS)
        self._rom_entry(controls, 24, "awp1221_rom_point_time", self.rom_point_time_var)
        self._rom_combo(controls, 25, "awp1221_rom_repeat", self.rom_repeat_var, self.ROM_REPEAT_OPTIONS)
        infinite = ttk.Checkbutton(controls, variable=self.rom_infinite_loop_var)
        infinite.grid(row=25, column=2, sticky="w", padx=(8, 0), pady=5)
        self._register_text(infinite, "awp1221_rom_infinite_loop")
        yield
        self._selector_widgets["awp1221_rom_infinite_loop"] = [infinite]
        offset_hint = ttk.Label(controls, style="Muted.TLabel", wraplength=280)
        offset_hint.grid(row=26, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self._register_text(offset_hint, "awp1221_waveform_word3_pending")
        self._selector_widgets["awp1221_waveform_word3_pending"] = [offset_hint]

        write_btn = ttk.Button(
            form_column,
            style="Primary.TButton",
            command=self.apply_waveform_selection,
        )
        yield
        write_btn.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._buttons["awp1221_rom_write_pending"] = write_btn
        self._selector_widgets["awp1221_rom_write_pending"] = [write_btn]

        preview = ttk.Frame(self._waveform_selector_shell, style="Panel.TFrame")
        preview.grid(row=1, column=1, sticky="nsew")
        self._waveform_selector_preview = preview
        yield
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(2, weight=1)
        metric_label = ttk.Label(preview, textvariable=self.rom_metric_var, style="Panel.TLabel", wraplength=280)
        metric_label.grid(row=0, column=0, sticky="w")
        frequency_label = ttk.Label(preview, textvariable=self.rom_frequency_var, style="Muted.TLabel", wraplength=280)
        frequency_label.grid(row=1, column=0, sticky="w", pady=(4, 8))
        yield
        self._selector_widgets["awp1221_rom_preview_captions"] = [metric_label, frequency_label]
        colors = getattr(self.ctx, "colors", {})
        self.rom_canvas = tk.Canvas(
            preview,
            bg=colors.get("canvas_bg", "#FFFFFF"),
            highlightthickness=1,
            highlightbackground=colors.get("border", "#CBD5E1"),
            width=280,
            height=280,
        )
        self.rom_canvas.grid(row=2, column=0, sticky="nsew")
        self.rom_canvas.bind("<Configure>", lambda _event: self._schedule_button_rom_preview())
        self._bind_rom_preview_traces()
        yield
        for variable in (self.rom_micro_bias_var, self.rom_vpk_var):
            variable.trace_add("write", lambda *_: self._refresh_waveform_peak_to_peak())
        self.sinc_serial_var.trace_add("write", lambda *_: self._on_sinc_serial_changed())
        for variable in (
            self.sinc_max_voltage_var,
            self.sinc_min_voltage_var,
            self.sinc_depth_var,
            self.sinc_window_var,
            *self.sinc_time_vars,
            self.sinc_sin_repeat_var,
        ):
            variable.trace_add("write", lambda *_: self._on_sinc_parameter_changed())
        self.rom_half_cycle_var.trace_add("write", lambda *_: self._refresh_playback_option_labels())
        self.rom_waveform_var.trace_add("write", lambda *_: self._refresh_waveform_controls())
        self.rom_source_var.trace_add("write", lambda *_: self._refresh_waveform_controls())
        yield
        self._refresh_waveform_peak_to_peak()
        self._refresh_sinc_group_hint()
        self._refresh_waveform_controls()

    def _build_button_adc_panel(self, parent: ttk.Frame, *, row: int = 0) -> None:
        for _ in self._build_button_adc_panel_steps(parent, row=row):
            pass

    def _build_button_adc_panel_steps(self, parent: ttk.Frame, *, row: int = 0) -> Iterator[None]:
        selector = ttk.Frame(parent, style="Panel.TFrame")
        selector.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        for stage_index in range(2):
            selector.columnconfigure(stage_index, weight=1)
            button = ttk.Button(
                selector,
                style="Secondary.TButton",
                command=lambda index=stage_index: self._show_button_stage(index),
            )
            button.grid(row=0, column=stage_index, sticky="ew", padx=(0 if stage_index == 0 else 4, 0))
            self._button_stage_buttons[stage_index] = button
            self._register_text(button, f"awp1221_button_stage{stage_index}")

        container = ttk.Frame(parent, style="Panel.TFrame")
        container.grid(row=row + 1, column=0, sticky="ew")
        container.columnconfigure(0, weight=1)
        yield
        for stage_index in range(2):
            frame = ttk.Frame(container, style="Panel.TFrame")
            frame.grid(row=0, column=0, sticky="ew")
            frame.grid_remove()
            self._button_stage_frames[stage_index] = frame
            yield from self._build_button_adc_stage_steps(frame, stage_index)
        self._show_button_stage(0)

    def _build_button_adc_stage(self, parent: ttk.Frame, stage_index: int) -> None:
        for _ in self._build_button_adc_stage_steps(parent, stage_index):
            pass

    def _build_button_adc_stage_steps(self, parent: ttk.Frame, stage_index: int) -> Iterator[None]:
        parent.columnconfigure(0, weight=0, minsize=110)
        parent.columnconfigure(1, weight=1, minsize=120)
        parent.columnconfigure(2, weight=0, minsize=110)
        parent.columnconfigure(3, weight=1, minsize=120)

        if stage_index == 0:
            variables = {
                "ovv_time": tk.StringVar(value="500 us"),
                "jump_target": tk.StringVar(value="Stage1"),
                "event_mode": tk.StringVar(value="press"),
                "threshold": tk.StringVar(value="300"),
                "debounce": tk.BooleanVar(value=False),
                "use_high_12_bits": tk.BooleanVar(value=True),
                "wave": tk.StringVar(value="slice18"),
                "repeat": tk.StringVar(value="0"),
            }
        else:
            variables = {
                "ovv_time": tk.StringVar(value="500 us"),
                "jump_target": tk.StringVar(value="Stage0"),
                "event_mode": tk.StringVar(value="release"),
                "threshold": tk.StringVar(value="100"),
                "debounce": tk.BooleanVar(value=False),
                "use_high_12_bits": tk.BooleanVar(value=True),
                "wave": tk.StringVar(value="slice18"),
                "repeat": tk.StringVar(value="0"),
            }
        self._button_stage_vars.append(variables)
        yield
        variables["use_high_12_bits"].trace_add("write", lambda *_: self._refresh_adc_bits())
        for key in ("threshold", "event_mode", "jump_target"):
            variables[key].trace_add("write", lambda *_: self._schedule_button_adc_diagram())

        commit_stage = lambda index=stage_index: self._commit_live_stage(index)
        self._form_label(parent, "awp1221_ovv_time", row=0, column=0)
        ovv_combo = ttk.Combobox(
            parent,
            textvariable=variables["ovv_time"],
            values=AWP1221_ADC_TIME_LABELS,
            state="readonly",
        )
        ovv_combo.grid(row=0, column=1, sticky="ew", pady=5)
        yield
        self._bind_live_choice(ovv_combo, commit_stage)

        self._form_label(parent, "awp1221_jump_target", row=0, column=2)
        jump_combo = ttk.Combobox(
            parent,
            textvariable=variables["jump_target"],
            values=("Stage0", "Stage1"),
            state="readonly",
        )
        jump_combo.grid(row=0, column=3, sticky="ew", pady=5)
        self._bind_live_choice(jump_combo, commit_stage)

        self._form_label(parent, "awp1221_detection_threshold", row=1, column=0)
        yield
        self._bind_live_text(
            self._threshold_entry_with_hint(parent, variables["threshold"], row=1, column=1),
            commit_stage,
        )

        self._form_label(parent, "awp1221_adc_event_mode", row=1, column=2)
        mode_frame = ttk.Frame(parent, style="Panel.TFrame")
        mode_frame.grid(row=1, column=3, sticky="w", pady=5)
        press_radio = ttk.Radiobutton(
            mode_frame, style="Check.TRadiobutton",
            variable=variables["event_mode"],
            value="press",
            command=commit_stage,
        )
        press_radio.grid(row=0, column=0, sticky="w")
        yield
        self._register_text(press_radio, "awp1221_press_mode")
        release_radio = ttk.Radiobutton(
            mode_frame, style="Check.TRadiobutton",
            variable=variables["event_mode"],
            value="release",
            command=commit_stage,
        )
        release_radio.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._register_text(release_radio, "awp1221_release_mode")

        self._form_label(parent, "awp1221_event_waveform", row=3, column=0)
        wave_combo = ttk.Combobox(
            parent,
            textvariable=variables["wave"],
            values=self._trigger_waveform_options(),
            state="readonly",
        )
        yield
        wave_combo.grid(row=3, column=1, sticky="ew", pady=5)
        self._bind_live_choice(wave_combo, commit_stage)
        self._button_wave_combos.append(wave_combo)
        self._form_label(parent, "awp1221_event_repeat", row=3, column=2)
        repeat_combo = ttk.Combobox(
            parent,
            textvariable=variables["repeat"],
            values=tuple(str(value) for value in range(8)),
            state="readonly",
        )
        repeat_combo.grid(row=3, column=3, sticky="ew", pady=5)
        yield
        self._bind_live_choice(repeat_combo, commit_stage)

        self._form_check(
            parent,
            "awp1221_debounce_enable",
            variables["debounce"],
            row=4,
            column=0,
            columnspan=2,
            command=commit_stage,
        )
        self._form_check_with_help(
            parent,
            "awp1221_adc_precision",
            variables["use_high_12_bits"],
            "awp1221_adc_precision_tip",
            row=4,
            column=2,
            columnspan=2,
            command=commit_stage,
        )

        # Label above each value, in the same order as the Button Mode diagram.
        positions = {"ovv_time": (0, 0), "jump_target": (0, 1), "threshold": (0, 2),
                     "wave": (0, 3), "repeat": (0, 4), "event_mode": (1, 0)}
        original = {(0, 0): "ovv_time", (0, 2): "jump_target", (1, 0): "threshold",
                    (1, 2): "event_mode", (3, 0): "wave", (3, 2): "repeat"}
        pairs: dict[str, list[tk.Widget | None]] = {}
        yield
        checks: list[tk.Widget] = []
        for widget in parent.winfo_children():
            info = widget.grid_info()
            old_row, old_column = int(info["row"]), int(info["column"])
            if old_row == 4:
                checks.append(widget)
                widget.grid_configure(row=3, column=2 if old_column == 0 else 3,
                                      columnspan=1 if old_column == 0 else 2,
                                      padx=(0, 8), pady=0, sticky="w")
                continue
            key = original.get((old_row, old_column - old_column % 2))
            if key is not None:
                pairs.setdefault(key, [None, None])[old_column % 2] = widget
                field_row, field_column = positions[key]
                widget.grid_configure(row=field_row * 2 + old_column % 2,
                                      column=field_column, columnspan=2 if key == "event_mode" else 1,
                                      padx=(0, 12), pady=(0, 3),
                                      sticky="ew" if old_column % 2 else "w")
                if isinstance(widget, ttk.Combobox):
                    widths = {"repeat": 5, "wave": max(len(label) for label, _ in AWP1221_SERIAL_WAVEFORMS) + 1}
                    widget.configure(width=widths.get(key, 8))
        for column in range(5):
            parent.columnconfigure(column, minsize=0, weight=1)
        order = ("ovv_time", "jump_target", "threshold", "wave", "repeat")
        layouts = getattr(self, "_button_stage_layouts", None)
        if layouts is None:
            self._button_stage_layouts = []
            self._button_stage_columns = 5
            layouts = self._button_stage_layouts
        yield
        layouts.append({
            "frame": parent,
            "fields": [(pairs[key][0], pairs[key][1]) for key in order],
            "event": (pairs["event_mode"][0], pairs["event_mode"][1]),
            "checks": tuple(checks),
        })
        threshold_holder = parent.grid_slaves(row=1, column=2)[0]
        for child in threshold_holder.winfo_children():
            if isinstance(child, ttk.Entry):
                child.configure(width=7)
        release_radio.grid_configure(row=0, column=1, padx=(8, 0), pady=0)

    def _show_button_stage(self, stage_index: int) -> None:
        self._visible_button_stage_index = stage_index
        self._refresh_adc_bits()
        for frame in self._button_stage_frames.values():
            frame.grid_remove()
        self._button_stage_frames[stage_index].grid()
        for index, button in self._button_stage_buttons.items():
            button.configure(style="Selected.TButton" if index == stage_index else "Secondary.TButton")
        self._draw_button_adc_diagram()

    def _read_adc_value(self, register: int, device_addr: int | None = None) -> int:
        address = self.device_addr() if device_addr is None else device_addr
        response = self.ctx.serial.read_register_value(address, register, bits=16)
        if not response.ok:
            raise ValueError(response.error)
        value = self.ctx.serial.extract_rdataw_value(
            response.data,
            device_addr=address,
            reg_addr=register,
            bits=16,
        )
        if value is None:
            raise ValueError(response.data or f"invalid ADC register 0x{register:02X} response")
        return value

    def _button_stage_config(self, stage_index: int) -> dict[str, Any]:
        variables = self._button_stage_vars[stage_index]
        mode = variables["event_mode"].get()
        time_code = awp1221_adc_time_code(variables["ovv_time"].get())
        if mode not in {"press", "release"}:
            raise ValueError("ADC event mode must be press or release")
        return {
            "mode": mode,
            "time_code": time_code,
            "target": self._stage_target_var(variables["jump_target"], "Jump target"),
            "debounce": bool(variables["debounce"].get()),
            "use_high_12_bits": bool(variables["use_high_12_bits"].get()),
            "threshold": awp1221_parse_threshold_text(str(variables["threshold"].get())),
            "wave": self._parse_trigger_waveform(variables["wave"].get()),
            "repeat": self._numeric_var(variables["repeat"], 0x7, "Repeat times"),
        }

    def _button_stage_values_from_config(
        self,
        stage_index: int,
        current: dict[int, int],
        config: dict[str, Any],
    ) -> dict[int, int]:
        control_reg, press_reg, release_reg, _slope_reg, wave_reg = (
            self.STAGE_REGISTER_RANGES[stage_index]
        )
        mode = config["mode"]
        event_bits = 0x0011 if mode == "press" else 0x0022
        time_code = config["time_code"]
        control = (
            (current[control_reg] & ~0xFF33)
            | (time_code << 12)
            | (time_code << 8)
            | event_bits
        )
        common_bits = (
            (1 if config["debounce"] else 0) << 15
        ) | (config["target"] << 13)
        press_value = (current[press_reg] & ~0xA000) | common_bits
        release_value = (current[release_reg] & ~0xA000) | common_bits
        active_value = press_value if mode == "press" else release_value
        active_value = (active_value & ~AWP1221_ADC_THRESHOLD_PRECISION_MASK) | (
            int(bool(config["use_high_12_bits"])) * AWP1221_ADC_THRESHOLD_PRECISION_MASK
        )
        if mode == "press":
            press_value = active_value
        else:
            release_value = active_value
        if mode == "press":
            press_value = (press_value & ~0x0FFF) | config["threshold"]
        else:
            release_value = (release_value & ~0x0FFF) | config["threshold"]

        wave_value = encode_awp1221_adc_wave(
            release_repeat=config["repeat"],
            release_wave=config["wave"],
            press_repeat=config["repeat"],
            press_wave=config["wave"],
        )
        return {
            control_reg: control & 0xFFFF,
            press_reg: press_value & 0xFFFF,
            release_reg: release_value & 0xFFFF,
            wave_reg: wave_value,
        }

    def _button_stage_values(self, stage_index: int, current: dict[int, int]) -> dict[int, int]:
        return self._button_stage_values_from_config(
            stage_index,
            current,
            self._button_stage_config(stage_index),
        )

    def _write_button_adc_stage(
        self,
        stage_index: int,
        config: dict[str, Any] | None = None,
        device_addr: int | None = None,
    ) -> None:
        address = self.device_addr() if device_addr is None else device_addr
        registers = self.STAGE_REGISTER_RANGES[stage_index]
        target_registers = (registers[0], registers[1], registers[2], registers[4])
        current = {
            register: self._read_adc_value(register, address)
            for register in target_registers
        }
        values = self._button_stage_values_from_config(
            stage_index,
            current,
            config if config is not None else self._button_stage_config(stage_index),
        )
        for register, value in values.items():
            response = self.ctx.serial.write_register_value(address, register, value, bits=16)
            if not response.ok:
                raise ValueError(response.error)

    def apply_button_adc_stage(self, stage_index: int) -> None:
        if not self._ensure_connected():
            return
        try:
            self._write_button_adc_stage(stage_index)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def read_button_adc_stage(self, stage_index: int) -> None:
        if not self._ensure_connected():
            return
        registers = self.STAGE_REGISTER_RANGES[stage_index]
        control_reg, press_reg, release_reg, _slope_reg, wave_reg = registers
        try:
            values = {
                register: self._read_adc_value(register)
                for register in (control_reg, press_reg, release_reg, wave_reg)
            }
            control = decode_awp1221_adc_control(values[control_reg])
            press_selected = control["flags"]["t_p"] and control["flags"]["trigger_p"]
            release_selected = control["flags"]["t_r"] and control["flags"]["trigger_r"]
            mode = "release" if release_selected and not press_selected else "press"
            variables = self._button_stage_vars[stage_index]
            wave = decode_awp1221_adc_wave(values[wave_reg])
            slice_index = wave["press_wave" if mode == "press" else "release_wave"]
            with self._suspend_adc_live_writes():
                variables["event_mode"].set(mode)
                variables["ovv_time"].set(awp1221_adc_time_label(control["press_time"]))
                active_threshold = values[press_reg] if mode == "press" else values[release_reg]
                variables["threshold"].set(awp1221_format_threshold_signed(active_threshold))
                variables["jump_target"].set(f"Stage{(active_threshold >> 13) & 0x1}")
                variables["debounce"].set(bool(values[press_reg] & 0x8000) and bool(values[release_reg] & 0x8000))
                variables["use_high_12_bits"].set(
                    bool(
                        (values[press_reg] if mode == "press" else values[release_reg])
                        & AWP1221_ADC_THRESHOLD_PRECISION_MASK
                    )
                )
                variables["wave"].set(self._format_trigger_waveform(slice_index))
                variables["repeat"].set(str(wave["press_repeat" if mode == "press" else "release_repeat"]))
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def _build_button_preset_header(self) -> None:
        header = self.make_title(self, self.mode_key, show_logo=False)
        header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())

        self.mode_title_label = header.grid_slaves(row=0, column=0)[0]

        preset_frame = self.make_preset_frame(header)

        first_row_keys = (
            "button_mode_preset_1",
            "button_mode_preset_2",
            "button_mode_preset_3",
            "button_mode_import_user_preset",
            "button_mode_export_user_preset",
        )
        for column, key in enumerate(first_row_keys):
            if key == "button_mode_import_user_preset":
                command: Callable[[], Any] = self.import_user_preset_script
            elif key == "button_mode_export_user_preset":
                command = self.export_user_preset_script
            elif key.startswith("button_mode_preset_"):
                command = lambda preset_index=column: self.apply_software_preset(preset_index)
            else:
                command = self._show_preset_pending
            button = ttk.Button(
                preset_frame,
                style="Bg.TButton",
                command=command,
            )
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0))
            self._buttons[key] = button
            if key in ("button_mode_import_user_preset", "button_mode_export_user_preset"):
                tooltip_key = {
                    "button_mode_import_user_preset": "button_mode_import_page_settings_tooltip",
                    "button_mode_export_user_preset": "button_mode_export_page_settings_tooltip",
                }[key]
                self._tooltips.append(SimpleTooltip(button, lambda tip=tooltip_key: self.tr(tip)))
            if key.startswith("button_mode_preset_"):
                self._tooltips.append(
                    SimpleTooltip(
                        button,
                        lambda preset_index=column: self._software_preset_tooltip_text(preset_index),
                    )
                )

        slot_cell = ttk.Frame(preset_frame, style="App.TFrame")
        slot_cell.grid(row=1, column=0, sticky="ew", pady=(self._module_spacing(), 0))
        slot_label = ttk.Label(slot_cell, style="App.TLabel")
        slot_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._register_text(slot_label, "board_slot")
        ttk.Combobox(
            slot_cell,
            textvariable=self.board_slot_var,
            values=("0", "1", "2"),
            state="readonly",
            width=4,
        ).grid(row=0, column=1, sticky="w")

        board_actions = (self.save_board_preset, self.set_board_preset_active,
                         self.apply_board_preset, self.read_board_preset)
        for column, key in enumerate(("save_to_board", "set_active", "apply_slot", "read_slot"), start=1):
            button = ttk.Button(
                preset_frame,
                style="Bg.TButton",
                command=board_actions[column - 1],
            )
            button.grid(row=1, column=column, sticky="ew", pady=(self._module_spacing(), 0), padx=(6, 0))
            self._buttons[key] = button

            self._tooltips.append(SimpleTooltip(
                button, lambda action=key: self.tr(f"awp1221_type4_{action}_tip")
            ))

    def _show_preset_pending(self) -> None:
        messagebox.showerror(
            self.tr("error"),
            "This operation is not implemented yet.",
        )

    def export_user_preset_script(self) -> None:
        try:
            content = format_register_script(self._build_current_register_script_operations())
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return
        path = filedialog.asksaveasfilename(
            title=self.tr("button_mode_export_user_preset"),
            defaultextension=".txt",
            initialfile="awp1221_page_settings.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(content, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        messagebox.showinfo(self.tr("info"), self.tr("register_script_export_success"))

    def import_user_preset_script(self) -> None:
        path = filedialog.askopenfilename(
            title=self.tr("button_mode_import_user_preset"),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            operations = parse_register_script(Path(path).read_text(encoding="utf-8-sig"))
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return
        if not operations:
            messagebox.showerror(self.tr("error"), self.tr("register_script_empty"))
            return
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return
        self._set_page_script_buttons_state("disabled")

        def worker() -> None:
            with _serial_batch(self.ctx.serial):
                error = self._execute_page_script_operations(operations)
            if error:
                raise RuntimeError(error)

        _run_ui_background_task(
            self,
            worker,
            on_done=lambda _result: self._finish_page_script_import(None, operations),
            on_error=lambda error: self._finish_page_script_import(str(error), operations),
        )

    def _finish_page_script_import(self, error: str | None, operations: list[Any]) -> None:
        self._set_page_script_buttons_state("normal")
        if error:
            messagebox.showerror(self.tr("error"), error)
            return
        snapshot = extract_awp1221_page_script(operations)
        if snapshot is not None:
            try:
                self._apply_page_script_snapshot(snapshot, operations[0].device_addr)
            except Exception as exc:
                messagebox.showerror(self.tr("error"), str(exc))
                return
        messagebox.showinfo(self.tr("info"), self.tr("register_script_import_success"))

    def _set_page_script_buttons_state(self, state: str) -> None:
        for key in ("button_mode_import_user_preset", "button_mode_export_user_preset"):
            button = self._buttons.get(key)
            if button is not None:
                button.configure(state=state)

    def _execute_page_script_operations(self, operations: list[Any]) -> str | None:
        addresses = {
            int(operation.device_addr)
            for operation in operations
            if operation.action == "WRITE"
        }
        try:
            for address in addresses:
                self._prepare_device_for_sram_write(address)
                self._ensure_reg_1f_bit3(address)
        except Exception as exc:
            return str(exc)

        def transport(operation: Any):
            if operation.action == "READ":
                return self.ctx.serial.read_register_value(
                    operation.device_addr,
                    operation.reg_addr,
                    bits=16,
                )
            if operation.action == "WRITE":
                return self.ctx.serial.write_register_value(
                    operation.device_addr,
                    operation.reg_addr,
                    operation.value or 0,
                    bits=16,
                )
            raise ValueError(f"unsupported operation {operation.action}")

        return execute_register_script_operations(operations, transport)

    def _build_current_register_script_operations(self) -> list[Any]:
        snapshot, sram_slices, play_slice = self._capture_page_script_waveform()
        stage_words = tuple(
            self._button_stage_values_from_config(
                stage_index,
                {register: 0 for register in self.STAGE_REGISTER_RANGES[stage_index]},
                self._button_stage_config(stage_index),
            )
            for stage_index in range(2)
        )
        settings_words = awp1221_settings_target_words(
            {
                AWP1221_ADC_SETTINGS_REGISTER: AWP1221_INTERFACE_CONFIG_0_DEFAULT,
                AWP1221_RETURN_BIT_REGISTER: 0,
                AWP1221_DEBOUNCE_REGISTER: 0,
                AWP1221_ADC_LOAD_REGISTER: AWP1221_CMD_GATE_BIT,
            },
            self._adc_settings_config(),
        )
        return build_awp1221_page_script_operations(
            self.device_addr(),
            snapshot,
            sram_slices=sram_slices,
            stage_words=stage_words,
            settings_words=settings_words,
            play_slice=play_slice,
        )

    def _capture_page_script_waveform(
        self,
    ) -> tuple[Awp1221PageScript, tuple[tuple[int, tuple[int, ...]], ...], int]:
        if self._sinc_selected():
            plan = self._build_current_sinc_plan()
            if not plan.writable:
                raise ValueError(self._sinc_reason_message(plan.reason))
            grouped: dict[int, list[int]] = {}
            order: list[int] = []
            for address, value in awp1221_sinc_write_words(plan, {}):
                slice_index = int(address) // AWP1221_WAVEFORM_WORDS_PER_SLICE
                if slice_index not in grouped:
                    order.append(slice_index)
                    grouped[slice_index] = []
                grouped[slice_index].append(int(value) & 0xFFFF)
            sram_slices = tuple((index, tuple(grouped[index])) for index in order)
            play_slice = int(plan.slices[0])
            kind = "sinc"
            waveform = self.SINC_WAVEFORM
        else:
            if str(self.rom_source_var.get()) == "RAM":
                raise ValueError(self.tr("awp1221_page_settings_ram_unsupported"))
            play_slice = self._parse_waveform_slice(self.rom_slice_var.get())
            descriptor = encode_awp1221_waveform_descriptor(
                source_code=self.WAVEFORM_SOURCE_CODES[self.rom_source_var.get()],
                micro_bias_code=self._parse_binary_option(self.rom_micro_bias_var.get()),
                waveform_code=self.ROM_WAVEFORM_CODES[self.rom_waveform_var.get()],
                depth_code=awp1221_waveform_depth_code(self.rom_depth_var.get()),
                half_cycle_code=self._parse_binary_option(self.rom_half_cycle_var.get()),
                phase_code=self._parse_binary_option(self.rom_phase_var.get()),
                point_time=int(self.rom_point_time_var.get().strip(), 0),
                repeat_count=int(self.rom_repeat_var.get().strip(), 0),
                infinite_loop=bool(self.rom_infinite_loop_var.get()),
            )
            word3 = merge_awp1221_waveform_word3(
                0,
                self._parse_binary_option(self.rom_offset_var.get()),
                self._parse_binary_option(self.rom_vpk_var.get()),
            )
            sram_slices = ((play_slice, (*descriptor, word3)),)
            kind = "rom"
            waveform = str(self.rom_waveform_var.get())
        serial, _start, _end = awp1221_sinc_serial_group(self.sinc_serial_var.get())
        times = tuple(str(variable.get()).strip() for variable in self._sinc_time_variables())
        if len(times) < 4:
            times = (*times, *("1",) * (4 - len(times)))
        stages = tuple(self._page_script_stage(index) for index in range(2))
        settings = self._adc_settings_config()
        snapshot = Awp1221PageScript(
            kind=kind,
            auto_serial=self._auto_serial_enabled(),
            serial=serial,
            depth=self._sinc_depth_value() if kind == "sinc" else str(self.rom_depth_var.get()).strip(),
            window_index=self._sinc_window_index(serial),
            max_voltage=str(self.sinc_max_voltage_var.get()).strip(),
            min_voltage=str(self.sinc_min_voltage_var.get()).strip(),
            times_us=(times[0], times[1], times[2], times[3]),
            sin_repeat=str(self.sinc_sin_repeat_var.get()).strip() or "1",
            slice_index=self._parse_waveform_slice(self.rom_slice_var.get()),
            source=str(self.rom_source_var.get()),
            waveform=waveform,
            fine=self._parse_binary_option(self.rom_micro_bias_var.get()),
            coarse=self._parse_binary_option(self.rom_vpk_var.get()),
            offset=self._parse_binary_option(self.rom_offset_var.get()),
            half=self._parse_binary_option(self.rom_half_cycle_var.get()),
            phase=self._parse_binary_option(self.rom_phase_var.get()),
            point_time=str(self.rom_point_time_var.get()).strip(),
            rom_repeat=str(self.rom_repeat_var.get()).strip(),
            infinite=bool(self.rom_infinite_loop_var.get()),
            stages=stages,
            debounce_ms=str(self.debounce_time_var.get()).strip(),
            sample_index=AWP1221_ADC_SAMPLE_UPDATE_OPTIONS.index(settings["sample_update"]),
            zero_index=AWP1221_ZERO_TIME_OPTIONS.index(settings["zero_time"]),
            press_return_index=AWP1221_RETURN_BIT_TIME_OPTIONS.index(settings["press_return_bit_time"]),
            release_return_index=AWP1221_RETURN_BIT_TIME_OPTIONS.index(settings["release_return_bit_time"]),
            zps_enabled=bool(settings["zps_enabled"]),
            zps_detect=bool(settings["zps_detect"]),
            sleep_sensitivity=bool(settings["sleep_sensitivity"]),
            ldo_index=AWP1221_ADC_LDO_OPTIONS.index(settings["adc_ldo"]),
        )
        return snapshot, sram_slices, play_slice

    def _page_script_stage(self, stage_index: int) -> Awp1221StageScript:
        variables = self._button_stage_vars[stage_index]
        config = self._button_stage_config(stage_index)
        threshold = str(variables["threshold"].get()).strip()
        awp1221_parse_threshold_text(threshold)
        return Awp1221StageScript(
            mode=config["mode"],
            ovv_code=config["time_code"],
            jump=config["target"],
            threshold=threshold,
            debounce=bool(config["debounce"]),
            use_high_12_bits=bool(config["use_high_12_bits"]),
            wave=config["wave"],
            repeat=config["repeat"],
        )

    def _apply_page_script_snapshot(self, snapshot: Awp1221PageScript, device_addr: int) -> None:
        serial_label = next(
            awp1221_sinc_serial_group_label(serial, start, end)
            for serial, start, end in AWP1221_SINC_SERIAL_GROUPS
            if serial == snapshot.serial
        )
        window_options = self._sinc_window_options(snapshot.serial)
        window_label = window_options[min(snapshot.window_index, len(window_options) - 1)]
        group = (
            snapshot.max_voltage,
            snapshot.min_voltage,
            *snapshot.times_us,
            snapshot.sin_repeat,
            snapshot.depth,
            window_label,
        )
        suspend = getattr(self, "_suspend_adc_live_writes", None)
        with suspend() if suspend is not None else nullcontext():
            self._board_rom_loading = True
            self._sinc_loading_group = True
            try:
                self.auto_serial_var.set(snapshot.auto_serial)
                self._sinc_group_values[snapshot.serial] = group
                self._sinc_active_group = snapshot.serial
                if self.sinc_serial_var.get() != serial_label:
                    self.sinc_serial_var.set(serial_label)
                else:
                    for variable, value in zip(
                        (
                            self.sinc_max_voltage_var,
                            self.sinc_min_voltage_var,
                            *self._sinc_time_variables(),
                            self.sinc_sin_repeat_var,
                            self.sinc_depth_var,
                            self.sinc_window_var,
                        ),
                        group,
                    ):
                        variable.set(value)
                self.rom_waveform_var.set(snapshot.waveform)
                self.rom_source_var.set(snapshot.source)
                self.rom_slice_var.set(f"slice{snapshot.slice_index}")
                self.rom_depth_var.set(snapshot.depth)
                self.sinc_depth_var.set(snapshot.depth)
                self.rom_micro_bias_var.set(self.MICRO_BIAS_OPTIONS[snapshot.fine])
                self.rom_vpk_var.set(self.VPK_OPTIONS[snapshot.coarse])
                self.rom_offset_var.set(self.OFFSET_OPTIONS[snapshot.offset])
                half_options = self._half_cycle_options_for_waveform()
                self.rom_half_cycle_var.set(half_options[min(snapshot.half, len(half_options) - 1)])
                phase_options = awp1221_phase_options(snapshot.waveform, snapshot.half)
                self.rom_phase_var.set(phase_options[min(snapshot.phase, len(phase_options) - 1)])
                self.rom_point_time_var.set(snapshot.point_time)
                self.rom_repeat_var.set(snapshot.rom_repeat)
                self.rom_infinite_loop_var.set(snapshot.infinite)
                for stage_index, stage in enumerate(snapshot.stages):
                    variables = self._button_stage_vars[stage_index]
                    variables["event_mode"].set(stage.mode)
                    variables["ovv_time"].set(awp1221_adc_time_label(stage.ovv_code))
                    variables["jump_target"].set(f"Stage{stage.jump}")
                    variables["threshold"].set(stage.threshold)
                    variables["debounce"].set(stage.debounce)
                    variables["use_high_12_bits"].set(stage.use_high_12_bits)
                    variables["wave"].set(self._format_trigger_waveform(stage.wave))
                    variables["repeat"].set(str(stage.repeat))
                self.debounce_time_var.set(snapshot.debounce_ms)
                self.adc_sample_update_var.set(AWP1221_ADC_SAMPLE_UPDATE_OPTIONS[snapshot.sample_index])
                self.zero_time_var.set(AWP1221_ZERO_TIME_OPTIONS[snapshot.zero_index])
                self.press_return_bit_time_var.set(
                    AWP1221_RETURN_BIT_TIME_OPTIONS[snapshot.press_return_index]
                )
                self.release_return_bit_time_var.set(
                    AWP1221_RETURN_BIT_TIME_OPTIONS[snapshot.release_return_index]
                )
                self.zps_enabled_var.set(snapshot.zps_enabled)
                self.zps_detect_var.set(snapshot.zps_detect)
                self.sleep_sensitivity_var.set(snapshot.sleep_sensitivity)
                self.adc_ldo_var.set(AWP1221_ADC_LDO_OPTIONS[snapshot.ldo_index])
            finally:
                self._board_rom_loading = False
                self._sinc_loading_group = False
        if snapshot.kind != "sinc":
            self._board_rom_slice = snapshot.slice_index
            self._remember_board_rom()
        self._sync_auto_serial_page_state()
        self._refresh_waveform_controls()
        self._schedule_button_rom_preview()
        if snapshot.kind != "sinc" or int(device_addr) != self.device_addr():
            return
        try:
            plan = self._build_current_sinc_plan()
        except (TypeError, ValueError, AttributeError):
            return
        if not plan.writable:
            return
        key = awp1221_sinc_plan_key(plan, int(device_addr))
        self._sinc_verified_key = key
        shared = self._shared_state_for(int(device_addr))
        if shared is not None:
            shared.sinc_key = key

    def _software_preset_tooltip_text(self, preset_index: int) -> str:
        if not 0 <= preset_index < len(self.BUTTON_SOFTWARE_PRESETS):
            return ""
        waveform = self.BUTTON_SOFTWARE_PRESETS[preset_index]["waveform"]
        times = tuple(int(value) for value in waveform["times_us"])
        depth = int(waveform["depth"])
        serial, _start, _end = awp1221_sinc_serial_group(waveform["serial"])
        repeat_count = int(waveform.get("sin_repeat", 1)) if serial in {"serial3", "serial4"} else 1
        period_us = awp1221_sinc_duration_us(
            depth,
            *times,
            sin_repeat_count=repeat_count,
        )

        return self.tr(
            "sinc_software_preset_tooltip",
            max_voltage=waveform["max_voltage"],
            min_voltage=waveform["min_voltage"],
            period_ms=f"{period_us / 1000:.3f}",
            repeat=1,
            segment_times=(
                "/".join(str(value) for value in times)
                + f" us (SIN x{repeat_count})"
            ),
        )

    def apply_software_preset(self, preset_index: int) -> bool:
        if self.mode_key != "button_mode" or not 0 <= preset_index < len(self.BUTTON_SOFTWARE_PRESETS):
            self._show_preset_pending()
            return False
        preset = self.BUTTON_SOFTWARE_PRESETS[preset_index]
        try:
            self._load_button_software_preset(preset)
            plan = self._build_current_sinc_plan()
            stage_configs = tuple(
                self._button_stage_config(stage_index)
                for stage_index in range(len(preset["stages"]))
            )
            settings_config = self._adc_settings_config()
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return False
        if not plan.writable:
            messagebox.showinfo(self.tr("info"), self._sinc_reason_message(plan.reason))
            return False
        if not self._ensure_connected():
            return False
        device_addr = self.device_addr()
        extra_writes = tuple(preset.get("extra_register_writes", ()))
        shared_state, revision = self._begin_sinc_write(device_addr)
        self._set_waveform_write_busy(True)
        _run_ui_background_task(
            self,
            lambda: self._write_software_preset(
                device_addr,
                plan,
                stage_configs,
                settings_config,
                extra_writes,
            ),
            on_done=lambda _result: self._finish_sinc_write(
                None,
                plan,
                device_addr,
                shared_state=shared_state,
                revision=revision,
            ),
            on_error=lambda error: self._finish_sinc_write(
                str(error),
                plan,
                device_addr,
                shared_state=shared_state,
                revision=revision,
            ),
        )
        return True

    def _load_button_software_preset(self, preset: dict[str, Any]) -> None:
        suspend = getattr(self, "_suspend_adc_live_writes", None)
        load = AWP1221ModePage._load_button_software_preset_vars
        if suspend is None:
            load(self, preset)
            return
        with suspend():
            load(self, preset)

    def _load_button_software_preset_vars(self, preset: dict[str, Any]) -> None:
        waveform = preset["waveform"]
        serial, _start_slice, _end_slice = awp1221_sinc_serial_group(waveform["serial"])
        times = tuple(waveform["times_us"])
        if serial in {"serial3", "serial4"} and len(times) != 4:
            raise ValueError("Serial3/4 software presets must retain all four slice times")
        if serial in {"serial1", "serial2"} and len(times) < 3:
            raise ValueError("Serial1/2 software presets require three slice times")
        self.auto_serial_var.set(waveform["auto_serial"])
        self._sinc_loading_group = True
        try:
            self.rom_waveform_var.set(waveform["waveform"])
            self.sinc_serial_var.set(waveform["serial"])
            self.sinc_depth_var.set(waveform["depth"])
            self.sinc_max_voltage_var.set(waveform["max_voltage"])
            self.sinc_min_voltage_var.set(waveform["min_voltage"])
            for variable, value in zip(self._sinc_time_variables(), times):
                variable.set(value)
            self.sinc_sin_repeat_var.set(str(waveform.get("sin_repeat", 1)))
        finally:
            self._sinc_loading_group = False
        self._sinc_active_group = serial
        if getattr(self, "_sinc_group_values", None) is not None:
            self._sinc_group_values[serial] = self._sinc_group_snapshot()
        self._sync_auto_serial_page_state()
        for stage_index, stage in enumerate(preset["stages"]):
            variables = self._button_stage_vars[stage_index]
            for key, value in stage.items():
                variables[key].set(value)
        settings = preset["settings"]
        self.debounce_time_var.set(settings["debounce_time"])
        self.adc_sample_update_var.set(settings["sample_update"])
        self.zero_time_var.set(settings["zero_time"])
        self.press_return_bit_time_var.set(settings["press_return_bit_time"])
        self.release_return_bit_time_var.set(settings["release_return_bit_time"])
        self.zps_enabled_var.set(settings["zps_enabled"])
        self.zps_detect_var.set(settings["zps_detect"])
        self.sleep_sensitivity_var.set(settings["sleep_sensitivity"])
        for register, value in preset.get("extra_register_writes", ()):
            if int(register) == AWP1221_ADC_LOAD_REGISTER:
                self.adc_ldo_var.set(awp1221_adc_load_label(int(value)))

    def _write_software_preset(
        self,
        device_addr: int,
        plan: Any,
        stage_configs: tuple[Any, ...],
        settings_config: dict[str, Any],
        extra_writes: tuple[tuple[int, int], ...],
    ) -> None:
        with _serial_batch(self.ctx.serial):
            self._write_sinc_plan_batch(device_addr, plan)
            for stage_index, config in enumerate(stage_configs):
                self._write_button_adc_stage(stage_index, config, device_addr)
            self._write_adc_settings(settings_config, device_addr)
            for register, value in extra_writes:
                self._write_register_value(register, value, device_addr)

    def _rom_combo(
        self,
        parent: tk.Misc,
        row: int,
        key: str,
        variable: tk.StringVar,
        values: tuple[str, ...],
    ) -> None:
        label_key = "awp1221_button_playback" if self.mode_key == "button_mode" and key == "awp1221_rom_half_cycle" else key
        label = self._form_label(parent, label_key, row=row, column=0)
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=18)
        combo.grid(row=row, column=1, sticky="ew", pady=5)
        self._selector_widgets[key] = [label, combo]
        if key == "awp1221_rom_depth":
            self.rom_depth_combo = combo
        elif key == "awp1221_sinc_depth":
            self.sinc_depth_combo = combo
        elif key == "awp1221_sinc_slice_group":
            self.sinc_window_combo = combo
        elif key == "awp1221_rom_waveform":
            self.rom_waveform_combo = combo
        elif key == "awp1221_rom_half_cycle":
            self.rom_half_cycle_combo = combo
        elif key == "awp1221_rom_phase":
            self.rom_phase_combo = combo

    def _rom_entry(
        self,
        parent: tk.Misc,
        row: int,
        key: str,
        variable: tk.StringVar,
    ) -> None:
        label = self._form_label(parent, key, row=row, column=0)
        entry = ttk.Entry(parent, textvariable=variable, width=18)
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        self._selector_widgets[key] = [label, entry]

    def _sinc_selected(self) -> bool:
        variable = getattr(self, "rom_waveform_var", None)
        return variable is not None and str(variable.get()).strip().upper() == self.SINC_WAVEFORM

    def _sram_source_selected(self) -> bool:
        if self.mode_key != "button_mode":
            return False
        if self._sinc_selected():
            return False
        return str(self.rom_source_var.get()) == "RAM"

    @property
    def sram_editor(self) -> AWP1221SramEditor:
        """Keep explicit editor access compatible while ROM visits stay lazy."""
        if self._sram_editor is None:
            editor = AWP1221SramEditor(self._sram_selector_holder, self.ctx, self)
            try:
                editor.grid(row=1, column=0, columnspan=2, sticky="nsew")
                editor.grid_remove()
                refresh = getattr(getattr(self.ctx, "app", None), "_refresh_widget_fonts", None)
                if callable(refresh):
                    refresh(editor)
            except Exception:
                editor.destroy()
                raise
            self._sram_editor = editor
        return self._sram_editor

    def _sync_sram_editor_visibility(self) -> None:
        editor = self._sram_editor
        shell = getattr(self, "_waveform_selector_shell", None)
        if shell is None or self.mode_key != "button_mode":
            return
        holder = getattr(self, "_sram_selector_holder", None)
        form = getattr(self, "_waveform_selector_form", None)
        preview = getattr(self, "_waveform_selector_preview", None)
        scroll = getattr(self, "_waveform_selector_scroll", None)
        if self._sram_source_selected():
            editor = self.sram_editor
            if holder is not None:
                holder.configure(height=520)
            for key in (
                "awp1221_rom_preview_captions",
                "awp1221_rom_write_pending",
            ):
                self._set_selector_row_visible(key, False)
            if hasattr(self, "rom_canvas"):
                self.rom_canvas.grid_remove()
            if preview is not None:
                preview.grid_remove()
            if form is not None:
                form.grid_remove()
            if scroll is not None:
                scroll.update_idletasks()
                scroll.canvas.configure(height=max(1, scroll.content.winfo_reqheight()))
            shell.rowconfigure(1, weight=0)
            shell.columnconfigure(1, weight=0)
            if holder is not None:
                holder.rowconfigure(0, weight=0)
                holder.rowconfigure(1, weight=1)
            editor.grid()
            editor.refresh_language()
            editor.refresh_theme()
            return
        if editor is not None:
            editor.grid_remove()
        if holder is not None:
            holder.configure(height=520)
        if form is not None:
            form.grid(row=1, column=0, columnspan=1, sticky="nsew", padx=(0, 18))
            form.rowconfigure(0, weight=1)
        if preview is not None:
            preview.grid(row=0, column=1, rowspan=2, sticky="nsew")
        if scroll is not None:
            scroll.canvas.configure(height=1)
        shell.rowconfigure(1, weight=1)
        shell.columnconfigure(1, weight=1)
        shell.columnconfigure(0, weight=0, minsize=680)
        if holder is not None:
            holder.rowconfigure(0, weight=1)
            holder.rowconfigure(1, weight=0)
        self._set_selector_row_visible("awp1221_rom_write_pending", True)
        if hasattr(self, "rom_canvas"):
            self.rom_canvas.grid()

    def _bind_rom_preview_traces(self) -> None:
        for variable in (
            self.rom_waveform_var,
            self.rom_depth_var,
            self.rom_point_time_var,
            self.rom_half_cycle_var,
            self.rom_phase_var,
            self.rom_vpk_var,
            self.rom_micro_bias_var,
            self.rom_offset_var,
        ):
            variable.trace_add("write", lambda *_: self._schedule_button_rom_preview())

    def _waveform_options(self) -> tuple[str, ...]:
        if self._auto_serial_enabled():
            return self.ROM_WAVEFORMS
        return tuple(name for name in self.ROM_WAVEFORMS if name != self.SINC_WAVEFORM)

    def _sync_waveform_options_with_auto_serial(self) -> None:
        options = self._waveform_options()
        combo = getattr(self, "rom_waveform_combo", None)
        if combo is not None:
            combo.configure(values=options)
        variable = getattr(self, "rom_waveform_var", None)
        if variable is not None and str(variable.get()) not in options:
            variable.set(options[0] if options else "SINE")

    def _set_selector_row_visible(self, key: str, visible: bool) -> None:
        for widget in getattr(self, "_selector_widgets", {}).get(key, ()):
            if visible:
                widget.grid()
            else:
                widget.grid_remove()

    def _half_cycle_options_for_waveform(self) -> tuple[str, ...]:
        variable = getattr(self, "rom_waveform_var", None)
        waveform = (
            str(variable.get()).strip()
            if variable is not None and hasattr(variable, "get")
            else str(variable or "").strip()
        )
        return self.HALF_CYCLE_OPTIONS_BY_WAVEFORM.get(waveform, self.HALF_CYCLE_OPTIONS)

    def _sinc_time_variables(self) -> tuple[tk.StringVar, ...]:
        variables = getattr(self, "sinc_time_vars", None)
        if variables:
            return tuple(variables)
        return tuple(
            variable
            for variable in (
                getattr(self, "sinc_time1_var", None),
                getattr(self, "sinc_time2_var", None),
                getattr(self, "sinc_time3_var", None),
                getattr(self, "sinc_time4_var", None),
            )
            if variable is not None
        )

    def _sinc_depth_value(self) -> str:
        variable = getattr(self, "sinc_depth_var", None)
        if variable is None:
            return "512"
        return str(variable.get()).strip() or "512"

    def _sinc_window_value(self) -> str:
        variable = getattr(self, "sinc_window_var", None)
        if variable is None:
            return "-"
        return str(variable.get()).strip() or "-"

    def _sinc_group_snapshot(self) -> tuple[str, ...]:
        return (
            self.sinc_max_voltage_var.get(),
            self.sinc_min_voltage_var.get(),
            *(variable.get() for variable in self._sinc_time_variables()),
            self.sinc_sin_repeat_var.get(),
            self._sinc_depth_value(),
            self._sinc_window_value(),
        )

    def _shared_port_identity(self) -> str | None:
        serial = self.ctx.serial
        selector = getattr(serial, "selected_port_for_page", None)
        if callable(selector):
            try:
                port = selector("awp1221")
            except TypeError:
                port = selector()
            if port:
                return str(port)
        state = getattr(self.ctx, "state", None)
        port = getattr(state, "selected_port", None)
        return str(port) if port else None

    def _shared_state_for(
        self,
        device_addr: int | None = None,
    ) -> AWP1221SharedDeviceState | None:
        owner = getattr(self, "_shared_state_owner", None)
        if owner is None:
            return None
        try:
            address = self.device_addr() if device_addr is None else int(device_addr)
            return owner.state_for(
                self.ctx.serial,
                address,
                port=self._shared_port_identity(),
            )
        except (AttributeError, TypeError, ValueError):
            return None

    def _invalidate_sinc_verified(self) -> None:
        self._sinc_verified_key = None
        owner = getattr(self, "_shared_state_owner", None)
        if owner is None:
            return
        try:
            owner.invalidate_device(
                self.ctx.serial,
                self.device_addr(),
                port=self._shared_port_identity(),
            )
        except (AttributeError, TypeError, ValueError):
            pass

    def _begin_sinc_write(
        self,
        device_addr: int,
    ) -> tuple[AWP1221SharedDeviceState | None, int | None]:
        self._sinc_verified_key = None
        owner = getattr(self, "_shared_state_owner", None)
        if owner is None:
            return None, None
        state = self._shared_state_for(device_addr)
        if state is None:
            return None, None
        return state, owner.invalidate_state(state)

    def _set_detection_state(self, active: bool, device_addr: int | None = None) -> None:
        address = device_addr
        if address is None:
            try:
                address = self.device_addr()
            except (AttributeError, TypeError, ValueError):
                address = None
        state = self._shared_state_for(address) if address is not None else None
        if state is not None:
            state.detection_active = bool(active)
            state.detection_device_addr = address if active else None
        self._adc_detection_active = bool(active)
        self._adc_detection_device_addr = address if active else None
        self._pressure_sensing = bool(active)
        self._set_pressure_status("sense" if active else "idle")

    def _set_pressure_status(self, status: str) -> None:
        if self.mode_key != "button_mode":
            return
        self._cancel_idle_job("_pressure_flash_job")
        self._pressure_status = status
        self._refresh_pressure_status()
        if status in {"press", "release"} and getattr(self, "_pressure_status_label", None) is not None:
            self._pressure_flash_job = self.after(180, self._restore_pressure_status)

    def _restore_pressure_status(self) -> None:
        self._pressure_flash_job = None
        self._set_pressure_status("sense" if getattr(self, "_pressure_sensing", False) else "idle")

    def _refresh_pressure_status(self) -> None:
        label = getattr(self, "_pressure_status_label", None)
        if label is None:
            return
        status = getattr(self, "_pressure_status", "idle")
        dark = self.ctx.state.theme == "dark"
        colors = {"idle": "#4B5563" if dark else "#E5E7EB", "sense": "#FACC15",
                  "press": "#2563EB", "release": "#9333EA", "error": "#DC2626", "play": "#FACC15"}
        label.configure(text=self.tr(f"awp1221_pressure_{status}"), bg=colors[status],
                        fg="#111827" if status in {"sense", "play"} or (status == "idle" and not dark) else "#FFFFFF")

    def _apply_pressure_status_cache(self, port: str, state: chip_status_cache.SnapshotState) -> None:
        # Consume only validated, current target samples; do not add chip I/O.
        if self.mode_key != "button_mode" or not self.ctx.serial.is_connected:
            return
        if port != self._shared_port_identity():
            return
        try:
            address = self.device_addr()
        except (TypeError, ValueError):
            return
        for record in state.records.values():
            identity = record.identity
            if (identity is None or identity.chip_type != chip_status_cache.CHIP_TYPE_1221
                    or identity.address != address or state.monitor_enabled(record.slot) is not True):
                continue
            if record.flags & chip_status_cache.FLAG_STALE:
                continue
            key = (port, state.session_id, identity, record.sample_seq, record.status, record.flags, record.error)
            if key == getattr(self, "_pressure_cache_key", None):
                return
            if not record.failed and not record.valid:
                continue
            self._pressure_cache_key = key
            raw = record.status
            self._pressure_sensing = bool(raw & (1 << 4)) or self._adc_detection_active
            if record.failed or raw & 0xFC00:
                status = "error"
            elif (raw & 3) == 1:
                status = "press"
            elif (raw & 3) == 2:
                status = "release"
            elif raw & (1 << 4) or (raw & 3) == 3:
                status = "sense"
            elif raw & (1 << 3):
                status = "play"
            else:
                status = "idle"
            self._set_pressure_status(status)
            return

    def _set_waveform_play_state(self, active: bool, device_addr: int | None = None) -> None:
        address = device_addr
        if address is None:
            try:
                address = self.device_addr()
            except (AttributeError, TypeError, ValueError):
                address = None
        state = self._shared_state_for(address) if address is not None else None
        if state is not None:
            state.waveform_play_active = bool(active)
        self._waveform_play_active = bool(active)

    def _sync_local_activity(self) -> None:
        state = self._shared_state_for()
        if state is None:
            return
        self._adc_detection_active = bool(state.detection_active)
        self._adc_detection_device_addr = state.detection_device_addr
        self._waveform_play_active = bool(state.waveform_play_active)
        self._pressure_sensing = self._adc_detection_active
        self._set_pressure_status("sense" if self._pressure_sensing else "idle")

    def _on_sinc_parameter_changed(self) -> None:
        if getattr(self, "_sinc_loading_group", False):
            return
        active_group = getattr(self, "_sinc_active_group", None)
        if active_group is None:
            return
        self._sinc_group_values[active_group] = self._sinc_group_snapshot()
        self._invalidate_sinc_verified()
        self._refresh_sinc_group_hint()
        self._refresh_sinc_match()
        self._schedule_button_rom_preview()

    def _on_sinc_serial_changed(self) -> None:
        try:
            serial, _start_slice, _end_slice = awp1221_sinc_serial_group(self.sinc_serial_var.get())
        except (TypeError, ValueError):
            return
        previous_group = getattr(self, "_sinc_active_group", None)
        if previous_group is not None:
            self._on_sinc_parameter_changed()
        self._sinc_active_group = serial
        values = self._sinc_group_values.get(serial)
        if values is not None:
            self._sinc_loading_group = True
            try:
                variables = [
                    self.sinc_max_voltage_var,
                    self.sinc_min_voltage_var,
                    *self._sinc_time_variables(),
                    self.sinc_sin_repeat_var,
                ]
                if getattr(self, "sinc_depth_var", None) is not None:
                    variables.append(self.sinc_depth_var)
                if getattr(self, "sinc_window_var", None) is not None:
                    variables.append(self.sinc_window_var)
                for variable, value in zip(variables, values):
                    variable.set(value)
            finally:
                self._sinc_loading_group = False
        self._invalidate_sinc_verified()
        self._refresh_sinc_window_options()
        self._refresh_sinc_group_hint()
        self._refresh_waveform_controls()
        self._schedule_button_rom_preview()

    def _sinc_window_options(self, serial: str) -> tuple[str, ...]:
        windows = awp1221_sinc_slice_windows(serial)
        if not windows:
            return ("-",)
        return tuple(f"0x{start:02X}-0x{end:02X}" for start, end in windows)

    def _sinc_window_index(self, serial: str) -> int:
        windows = awp1221_sinc_slice_windows(serial)
        if not windows:
            return 0
        label = self._sinc_window_value()
        for index, (start, end) in enumerate(windows):
            if label == f"0x{start:02X}-0x{end:02X}":
                return index
        return 0

    def _sinc_active_start_slice(self) -> int | None:
        serial_var = getattr(self, "sinc_serial_var", None)
        if serial_var is None:
            return None
        try:
            serial, start_slice, _end_slice = awp1221_sinc_serial_group(serial_var.get())
        except (TypeError, ValueError):
            return None
        windows = awp1221_sinc_slice_windows(serial)
        if windows:
            start_slice, _end = windows[self._sinc_window_index(serial)]
        return start_slice

    def _refresh_sinc_window_options(self) -> None:
        combo = getattr(self, "sinc_window_combo", None)
        serial_var = getattr(self, "sinc_serial_var", None)
        if combo is None or serial_var is None:
            return
        try:
            serial, _start, _end = awp1221_sinc_serial_group(serial_var.get())
        except (TypeError, ValueError):
            return
        options = self._sinc_window_options(serial)
        combo.configure(values=options)
        variable = getattr(self, "sinc_window_var", None)
        if variable is not None and str(variable.get()) not in options:
            self._sinc_loading_group = True
            try:
                variable.set(options[0])
            finally:
                self._sinc_loading_group = False

    def _refresh_sinc_slice_time_labels(self) -> None:
        start_slice = self._sinc_active_start_slice()
        if start_slice is None:
            return
        for index in range(4):
            widgets = getattr(self, "_selector_widgets", {}).get(f"awp1221_sinc_time{index + 1}", ())
            if not widgets:
                continue
            widgets[0].configure(
                text=self.tr("awp1221_button_time", index=index + 1) if self.mode_key == "button_mode"
                else self.tr("awp1221_sinc_slice_time", slice=f"0x{start_slice + index:02X}")
            )
        if self.mode_key == "button_mode":
            for key, short_key in (("awp1221_sinc_serial_group", "awp1221_button_serial"),
                                   ("awp1221_sinc_depth", "awp1221_button_depth"),
                                   ("awp1221_sinc_max_voltage", "awp1221_button_max_voltage"),
                                   ("awp1221_sinc_min_voltage", "awp1221_button_min_voltage")):
                widgets = self._selector_widgets.get(key, ())
                if widgets:
                    widgets[0].configure(text=self.tr(short_key))

    def _refresh_sinc_group_hint(self) -> None:
        self._refresh_sinc_haptic_period()
        output = getattr(self, "sinc_group_hint_var", None)
        serial_var = getattr(self, "sinc_serial_var", None)
        if output is None or serial_var is None:
            return
        try:
            serial, start_slice, end_slice = awp1221_sinc_serial_group(serial_var.get())
        except (TypeError, ValueError):
            output.set("")
            return
        slice_count = end_slice - start_slice + 1
        times = self._sinc_time_variables()
        durations = "/".join(
            variable.get() for variable in times[:4 if serial in {"serial3", "serial4"} else 3]
        )
        output.set(
            self.tr(
                "awp1221_sinc_group_hint",
                serial=serial,
                start=f"0x{start_slice:02X}",
                end=f"0x{end_slice:02X}",
                slices=slice_count,
                durations=durations,
            )
        )
        self._refresh_sinc_slice_time_labels()
        self._refresh_sinc_depth_hint()
        self._refresh_sinc_match()

    def _refresh_sinc_haptic_period(self) -> None:
        output = getattr(self, "sinc_haptic_period_var", None)
        if output is None:
            return
        period = "—"
        try:
            _serial, start, end = awp1221_sinc_serial_group(self.sinc_serial_var.get())
            times = self._sinc_time_variables()
            if start <= 0x12 < 0x13 <= end:
                first = int(times[0x12 - start].get())
                second = int(times[0x13 - start].get())
                if first > 0 and second > 0:
                    period = f"{(first + second) * 256} μs"
        except (TypeError, ValueError, IndexError):
            pass
        output.set(self.tr("awp1221_sinc_haptic_period", period=period))

    def _refresh_sinc_depth_hint(self) -> None:
        widgets = getattr(self, "_selector_widgets", {}).get("awp1221_sinc_depth_hint", ())
        if not widgets:
            return
        try:
            plan = self._build_current_sinc_plan()
            points = plan.points_per_segment
        except (TypeError, ValueError, AttributeError):
            depth = self._sinc_depth_value()
            points = 256 if depth == "512" else 128 if depth == "256" else 0
            plan = None
        try:
            serial, _start_slice, _end_slice = awp1221_sinc_serial_group(
                self.sinc_serial_var.get()
            )
        except (AttributeError, TypeError, ValueError):
            serial = "serial1"
        if serial in {"serial3", "serial4"}:
            depth_points = plan.params.depth_points if plan is not None else (512 if points == 256 else 256 if points == 128 else 0)
            sinc_points = depth_points // 2
            widgets[0].configure(
                text=self.tr(
                    "awp1221_sinc_depth_hint_four",
                    sin_points=depth_points,
                    sinc_points=sinc_points,
                )
            )
        else:
            widgets[0].configure(text=self.tr("awp1221_sinc_depth_hint", points=points))

    def _sinc_reason_message(self, reason: str) -> str:
        keys = {
            "insufficient_slots": "awp1221_sinc_serial0_blocked",
            "fourth_slot_unconfirmed": "awp1221_sinc_fourth_slot_blocked",
            "four_times_required": "awp1221_sinc_four_times_required",
            "span_exceeds_vpp": "awp1221_sinc_span_exceeds",
            "no_continuous_candidate": "awp1221_sinc_no_continuous_candidate",
            "not_verified": "awp1221_sinc_not_verified",
        }
        return self.tr(keys.get(reason, "awp1221_sinc_write_unavailable"))

    def _build_current_sinc_plan(self):
        serial, _start_slice, _end_slice = awp1221_sinc_serial_group(self.sinc_serial_var.get())
        times = self._sinc_time_variables()
        params = awp1221_sinc_parse_params(
            self.sinc_max_voltage_var.get(),
            self.sinc_min_voltage_var.get(),
            times[0].get() if len(times) > 0 else "4",
            times[1].get() if len(times) > 1 else "4",
            times[2].get() if len(times) > 2 else "7",
            self._sinc_depth_value(),
            times[3].get() if serial in {"serial3", "serial4"} and len(times) > 3 else None,
            self.sinc_sin_repeat_var.get() if serial in {"serial3", "serial4"} else "1",
        )
        window_index = self._sinc_window_index(serial)
        key = (params, serial, window_index)
        cached = getattr(self, "_sinc_plan_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        # The plan is immutable and independent of device readback. Repeated
        # preview/label refreshes need not repeat the same solver search.
        plan = build_awp1221_sinc_plan(params, serial=serial, window_index=window_index)
        self._sinc_plan_cache = (key, plan)
        return plan

    def _refresh_sinc_match(self) -> None:
        output = getattr(self, "sinc_match_var", None)
        if output is None:
            return
        try:
            plan = self._build_current_sinc_plan()
        except (TypeError, ValueError, AttributeError) as exc:
            output.set(str(exc))
            return
        if plan.solution is None:
            output.set(self._sinc_reason_message(plan.reason))
            return
        output.set(
            self.tr(
                "awp1221_sinc_match",
                peak=f"{plan.solution.peak_volts:g}",
                trough=f"{plan.solution.trough_volts:g}",
                vpp=f"{plan.solution.vpp_volts:g}",
                duration=plan.duration_us,
            )
        )

    def _refresh_playback_option_labels(self) -> None:
        if getattr(self, "_refreshing_playback_options", False):
            return
        self._refreshing_playback_options = True
        try:
            if hasattr(self, "rom_half_cycle_combo"):
                half_options = self._half_cycle_options_for_waveform()
                self.rom_half_cycle_combo.configure(values=half_options)
                try:
                    half_code = self._parse_binary_option(self.rom_half_cycle_var.get())
                except (TypeError, ValueError):
                    half_code = 0
                if 0 <= half_code < len(half_options):
                    self.rom_half_cycle_var.set(half_options[half_code])
            else:
                try:
                    half_code = self._parse_binary_option(self.rom_half_cycle_var.get())
                except (TypeError, ValueError, AttributeError):
                    half_code = 0
            if hasattr(self, "rom_phase_combo"):
                waveform = (
                    self.rom_waveform_var.get()
                    if getattr(self, "rom_waveform_var", None) is not None
                    else "SINE"
                )
                phase_options = awp1221_phase_options(waveform, half_code)
                self.rom_phase_combo.configure(values=phase_options)
                try:
                    phase_code = self._parse_binary_option(self.rom_phase_var.get())
                except (TypeError, ValueError):
                    phase_code = 0
                if 0 <= phase_code < len(phase_options):
                    self.rom_phase_var.set(phase_options[phase_code])
        finally:
            self._refreshing_playback_options = False

    def _refresh_waveform_controls(self) -> None:
        if getattr(self, "_refreshing_waveform_controls", False):
            return
        self._refreshing_waveform_controls = True
        try:
            self._sync_waveform_options_with_auto_serial()
            self._refresh_waveform_control_visibility()
        finally:
            self._refreshing_waveform_controls = False

    def _refresh_waveform_control_visibility(self) -> None:
        is_sinc = self._sinc_selected()
        legacy_keys = (
            "awp1221_rom_slice",
            "awp1221_rom_source",
            "awp1221_rom_micro_bias",
            "awp1221_rom_peak_to_peak",
            "awp1221_rom_amplitude",
            "awp1221_rom_depth",
            "awp1221_rom_half_cycle",
            "awp1221_rom_phase",
            "awp1221_rom_offset",
            "awp1221_rom_point_time",
            "awp1221_rom_repeat",
            "awp1221_rom_infinite_loop",
            "awp1221_waveform_word3_pending",
        )
        for key in legacy_keys:
            self._set_selector_row_visible(key, not is_sinc)
        try:
            serial, start_slice, end_slice = awp1221_sinc_serial_group(self.sinc_serial_var.get())
            window_count = len(awp1221_sinc_slice_windows(serial))
            slice_count = end_slice - start_slice + 1
        except (TypeError, ValueError, AttributeError):
            serial = ""
            window_count = 0
            slice_count = 0
        sinc_keys = (
            "awp1221_sinc_haptic_period",
            "awp1221_sinc_serial_group",
            "awp1221_sinc_depth",
            "awp1221_sinc_depth_hint",
            "awp1221_sinc_max_voltage",
            "awp1221_sinc_min_voltage",
            "awp1221_sinc_match",
        )
        for key in ("awp1221_sinc_group_hint", "awp1221_sinc_hint"):
            self._set_selector_row_visible(key, False)
        for key in sinc_keys:
            self._set_selector_row_visible(key, is_sinc)
        self._set_selector_row_visible(
            "awp1221_sinc_haptic_period",
            is_sinc and serial in {"serial1", "serial2"},
        )
        self._set_selector_row_visible("awp1221_sinc_slice_group", is_sinc and window_count > 1)
        self._set_selector_row_visible("awp1221_rom_preview_captions", not is_sinc)
        for index in range(4):
            self._set_selector_row_visible(
                f"awp1221_sinc_time{index + 1}",
                is_sinc and index < slice_count,
            )
        self._set_selector_row_visible(
            "awp1221_sinc_sin_repeat",
            is_sinc and serial in {"serial3", "serial4"},
        )
        self._refresh_playback_option_labels()
        auto_serial_check = getattr(self, "_auto_serial_check", None)
        if auto_serial_check is not None:
            auto_serial_check.configure(state="normal")
        write_button = getattr(self, "_buttons", {}).get("awp1221_rom_write_pending")
        if write_button is not None:
            write_button.configure(state="normal")
        self._refresh_sinc_window_options()
        self._refresh_sinc_group_hint()
        self._schedule_button_rom_preview()
        self._set_selector_row_visible("awp1221_auto_serial", not self._sram_source_selected())
        self._set_selector_row_visible("awp1221_rom_waveform", not self._sram_source_selected())
        if self._sram_source_selected():
            for key in legacy_keys:
                self._set_selector_row_visible(key, False)
        self._sync_sram_editor_visibility()
        if self.mode_key == "button_mode":
            self._layout_button_waveform_form()
        else:
            self._layout_full_waveform_form()
        selected_tab = "sram" if self._sram_source_selected() else "sinc" if is_sinc else "rom"
        for key, button in getattr(self, "_button_wave_tabs", {}).items():
            button.configure(style="Selected.TButton" if key == selected_tab else "Secondary.TButton")

    def _refresh_waveform_peak_to_peak(self) -> None:
        output = getattr(self, "rom_vpp_var", None)
        if output is None:
            return
        try:
            fine_code = self._parse_binary_option(self.rom_micro_bias_var.get())
            coarse_code = self._parse_binary_option(self.rom_vpk_var.get())
            output.set(awp1221_waveform_peak_to_peak_label(coarse_code, fine_code))
        except (TypeError, ValueError):
            output.set("")

    def _rom_depth_options(self) -> list[str]:
        return list(self.ROM_DEPTH_OPTIONS)

    def _refresh_button_rom_depths(self) -> None:
        options = self._rom_depth_options()
        self.rom_depth_combo.configure(values=options)
        if self.rom_depth_var.get() not in options:
            self.rom_depth_var.set(options[0])
        self._schedule_button_rom_preview()

    def _trigger_waveform_options(self) -> tuple[str, ...]:
        options = [f"slice{value}" for value in range(AWP1221_WAVEFORM_SLICE_COUNT)]
        if self._auto_serial_enabled():
            options.extend(label for label, _start_slice in AWP1221_SERIAL_WAVEFORMS)
        return tuple(options)

    @staticmethod
    def _parse_trigger_waveform(value: str) -> int:
        text = str(value).strip().lower()
        for label, start_slice in AWP1221_SERIAL_WAVEFORMS:
            if text == label.lower():
                return start_slice
        if text.startswith("slice"):
            return _require_unsigned(
                int(text.removeprefix("slice"), 10),
                AWP1221_WAVEFORM_SLICE_COUNT - 1,
                "Trigger waveform",
            )
        raise ValueError("Trigger waveform must be slice0..slice31 or serial0..serial4")

    def _format_trigger_waveform(self, slice_index: int) -> str:
        slice_index = _require_unsigned(
            slice_index,
            AWP1221_WAVEFORM_SLICE_COUNT - 1,
            "Trigger waveform",
        )
        if self._auto_serial_enabled():
            for label, start_slice in AWP1221_SERIAL_WAVEFORMS:
                if slice_index == start_slice:
                    return label
        return f"slice{slice_index}"

    def _refresh_trigger_waveform_options(self) -> None:
        options = self._trigger_waveform_options()
        for combo in (
            *getattr(self, "_button_wave_combos", ()),
            *getattr(self, "_full_wave_combos", ()),
        ):
            combo.configure(values=options)
        variables = [stage["wave"] for stage in getattr(self, "_button_stage_vars", ())]
        for stage in getattr(self, "_stage_vars", ()):
            variables.extend((stage["wave"]["press_wave"], stage["wave"]["release_wave"]))
        for variable in variables:
            try:
                variable.set(self._format_trigger_waveform(self._parse_trigger_waveform(variable.get())))
            except (TypeError, ValueError):
                variable.set("slice0")

    def _auto_serial_enabled(self) -> bool:
        variable = getattr(self, "auto_serial_var", None)
        return True if variable is None else bool(variable.get())

    def _apply_auto_serial_bit(self, register_value: int) -> int:
        if self._auto_serial_enabled():
            return register_value | AWP1221_AUTO_SERIAL_MASK
        return register_value & ~AWP1221_AUTO_SERIAL_MASK

    def _sync_auto_serial_page_state(self) -> None:
        self._refresh_trigger_waveform_options()
        self._sync_waveform_options_with_auto_serial()
        if not self._auto_serial_enabled():
            self._invalidate_sinc_verified()
        self._refresh_waveform_controls()

    def _on_auto_serial_changed(self) -> None:
        self._sync_auto_serial_page_state()
        self._commit_live_settings()

    @staticmethod
    def _parse_waveform_slice(value: str) -> int:
        text = str(value).strip().lower()
        if not text.startswith("slice"):
            raise ValueError("Slice must be slice0..slice31")
        return _require_unsigned(
            int(text.removeprefix("slice"), 10),
            AWP1221_WAVEFORM_SLICE_COUNT - 1,
            "Slice",
        )

    @staticmethod
    def _parse_binary_option(value: str) -> int:
        token = str(value).strip().split(" ", 1)[0]
        if token.lower().startswith("0x"):
            return int(token, 16)
        return int(token, 2)

    def _read_register_value(self, register: int, device_addr: int | None = None) -> int:
        address = self.device_addr() if device_addr is None else device_addr
        response = self.ctx.serial.read_register_value(address, register, bits=16)
        if not response.ok:
            raise ValueError(response.error)
        value = self.ctx.serial.extract_rdataw_value(
            response.data,
            device_addr=address,
            reg_addr=register,
            bits=16,
        )
        if value is None:
            raise ValueError(response.data or f"invalid register 0x{register:02X} response")
        return value

    def _write_register_value(
        self,
        register: int,
        value: int,
        device_addr: int | None = None,
    ) -> None:
        address = self.device_addr() if device_addr is None else device_addr
        response = self.ctx.serial.write_register_value(address, register, value, bits=16)
        if not response.ok:
            raise ValueError(response.error)

    def _ensure_reg_1f_bit3(self, device_addr: int | None = None) -> int:
        address = self.device_addr() if device_addr is None else device_addr
        return ensure_awp1221_cmd_gate(
            lambda register: self._read_register_value(register, address),
            lambda register, value: self._write_register_value(register, value, address),
        )

    def _set_sram_mode(
        self,
        mode: int,
        device_addr: int | None = None,
        auto_serial_enabled: bool | None = None,
    ) -> None:
        del auto_serial_enabled
        self._write_register_value(
            AWP1221_ADC_SETTINGS_REGISTER,
            (AWP1221_INTERFACE_CONFIG_0_DEFAULT & ~AWP1221_SRAM_MODE_MASK) | mode,
            device_addr,
        )

    def _write_waveform_selection(
        self,
        device_addr: int,
        auto_serial_enabled: bool,
        slice_index: int,
        descriptor: tuple[int, int, int],
        offset_code: int,
        vpk_code: int,
    ) -> None:
        self._ensure_reg_1f_bit3(device_addr)
        with _serial_batch(self.ctx.serial):
            self._write_waveform_selection_batch(
                device_addr,
                auto_serial_enabled,
                slice_index,
                descriptor,
                offset_code,
                vpk_code,
            )

    def _write_waveform_selection_batch(
        self,
        device_addr: int,
        auto_serial_enabled: bool,
        slice_index: int,
        descriptor: tuple[int, int, int],
        offset_code: int,
        vpk_code: int,
    ) -> None:
        entered_write_mode = False
        write_error: Exception | None = None
        try:
            self._prepare_device_for_sram_write(device_addr)
            self._set_sram_mode(
                AWP1221_SRAM_WRITE_READ_MODE,
                device_addr,
                auto_serial_enabled,
            )
            entered_write_mode = True
            sram_address = (slice_index * AWP1221_WAVEFORM_WORDS_PER_SLICE) & 0x03FF
            word3 = merge_awp1221_waveform_word3(0, offset_code, vpk_code)
            self._write_sram_words(device_addr, sram_address, (*descriptor, word3))
        except Exception as exc:
            write_error = exc

        restore_error: Exception | None = None
        if entered_write_mode:
            try:
                self._set_sram_mode(
                    AWP1221_SRAM_PLAY_MODE,
                    device_addr,
                    auto_serial_enabled,
                )
            except Exception as exc:
                restore_error = exc

        if write_error is None and restore_error is None:
            try:
                self._write_play_wave_select(device_addr, slice_index)
            except Exception as exc:
                write_error = exc

        if write_error is not None or restore_error is not None:
            errors = [str(error) for error in (write_error, restore_error) if error is not None]
            raise ValueError("\n".join(errors))

    def _write_play_wave_select(self, device_addr: int, slice_index: int) -> None:
        current = self._read_register_value(AWP1221_PLAY_WAVE_REGISTER, device_addr)
        wave = int(slice_index) & 0x1F
        self._write_register_value(
            AWP1221_PLAY_WAVE_REGISTER,
            (current & ~(0x001F | (0x001F << 8))) | wave | (wave << 8),
            device_addr,
        )

    def _set_sram_word_address(self, device_addr: int, word_address: int) -> None:
        self._write_register_value(
            AWP1221_SRAM_ADDRESS_REGISTER,
            int(word_address) & 0x03FF,
            device_addr,
        )

    def _write_sram_words(self, device_addr: int, start_address: int, values: tuple[int, ...] | list[int]) -> None:
        self._set_sram_word_address(device_addr, start_address)
        for value in values:
            self._write_register_value(
                AWP1221_SRAM_DATA_REGISTER,
                value,
                device_addr,
            )

    def _write_sinc_plan(self, device_addr: int, plan: Any) -> None:
        self._ensure_reg_1f_bit3(device_addr)
        with _serial_batch(self.ctx.serial):
            self._write_sinc_plan_batch(device_addr, plan)

    def _prepare_device_for_sram_write(self, device_addr: int) -> None:
        shared_state = self._shared_state_for(device_addr)
        detection_active = (
            bool(shared_state.detection_active)
            if shared_state is not None
            else bool(getattr(self, "_adc_detection_active", False))
        )
        play_active = (
            bool(shared_state.waveform_play_active)
            if shared_state is not None
            else bool(getattr(self, "_waveform_play_active", False))
        )
        if not detection_active and not play_active:
            return
        if detection_active:
            self._write_control_command("stop_detection", device_addr)
            self._set_detection_state(False, device_addr)
        if detection_active or play_active:
            self._write_control_command("stop_play", device_addr)
            self._set_waveform_play_state(False, device_addr)

    def _write_sinc_plan_batch(self, device_addr: int, plan: Any) -> None:
        entered_write_mode = False
        write_error: Exception | None = None
        try:
            self._prepare_device_for_sram_write(device_addr)
            self._set_sram_mode(
                AWP1221_SRAM_WRITE_READ_MODE,
                device_addr,
                True,
            )
            entered_write_mode = True
            written = dict(awp1221_sinc_write_words(plan, {}))
            for slice_index in plan.slices:
                base = slice_index * AWP1221_WAVEFORM_WORDS_PER_SLICE
                self._write_sram_words(
                    device_addr,
                    base,
                    tuple(written[base + word] for word in range(AWP1221_WAVEFORM_WORDS_PER_SLICE)),
                )
        except Exception as exc:
            write_error = exc

        restore_error: Exception | None = None
        if entered_write_mode:
            try:
                self._set_sram_mode(
                    AWP1221_SRAM_PLAY_MODE,
                    device_addr,
                    True,
                )
            except Exception as exc:
                restore_error = exc

        if write_error is None and restore_error is None:
            try:
                self._write_play_wave_select(device_addr, plan.slices[0])
            except Exception as exc:
                write_error = exc

        if write_error is not None or restore_error is not None:
            errors = [str(error) for error in (write_error, restore_error) if error is not None]
            raise ValueError("\n".join(errors))

    def _set_waveform_write_busy(self, busy: bool) -> None:
        button = getattr(self, "_buttons", {}).get("awp1221_rom_write_pending")
        if button is not None:
            button.configure(state="disabled" if busy else "normal")

    def _finish_waveform_selection(self, error: str | None) -> None:
        self._set_waveform_write_busy(False)
        if error:
            messagebox.showerror(self.tr("error"), error)
            return
        messagebox.showinfo(self.tr("info"), self.tr("write_success"))

    def _apply_sinc_waveform_selection(self) -> bool:
        try:
            plan = self._build_current_sinc_plan()
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return False
        if not plan.writable:
            messagebox.showinfo(self.tr("info"), self._sinc_reason_message(plan.reason))
            return False
        if not self._ensure_connected():
            return False
        device_addr = self.device_addr()
        shared_state, revision = self._begin_sinc_write(device_addr)
        self._set_waveform_write_busy(True)
        _run_ui_background_task(
            self,
            lambda: self._write_sinc_plan(device_addr, plan),
            on_done=lambda _result: self._finish_sinc_write(
                None,
                plan,
                device_addr,
                shared_state=shared_state,
                revision=revision,
            ),
            on_error=lambda error: self._finish_sinc_write(
                str(error),
                plan,
                device_addr,
                shared_state=shared_state,
                revision=revision,
            ),
        )
        return True

    def _finish_sinc_write(
        self,
        error: str | None,
        plan: Any,
        device_addr: int,
        *,
        shared_state: AWP1221SharedDeviceState | None = None,
        revision: int | None = None,
    ) -> None:
        owner = getattr(self, "_shared_state_owner", None)
        if (
            owner is not None
            and shared_state is not None
            and not getattr(self.ctx.serial, "is_connected", True)
        ):
            owner.invalidate_state(shared_state, clear_active=True)
            return
        if (
            owner is not None
            and shared_state is not None
            and revision is not None
            and not owner.accepts_sinc_write(shared_state, revision)
        ):
            return
        self._set_waveform_write_busy(False)
        if error:
            self._sinc_verified_key = None
            messagebox.showerror(self.tr("error"), error)
            return
        key = awp1221_sinc_plan_key(plan, device_addr)
        if owner is not None and shared_state is not None and revision is not None:
            shared_state.sinc_key = key
        self._sinc_verified_key = key
        messagebox.showinfo(self.tr("info"), self.tr("write_success"))

    def _sinc_play_allowed(self) -> bool:
        if not self._auto_serial_enabled():
            return False
        try:
            plan = self._build_current_sinc_plan()
        except (TypeError, ValueError, AttributeError):
            return False
        if not plan.writable:
            return False
        key = awp1221_sinc_plan_key(plan, self.device_addr())
        owner = getattr(self, "_shared_state_owner", None)
        if owner is not None and not getattr(self.ctx.serial, "is_connected", True):
            owner.invalidate_all(clear_active=True)
            return False
        shared_state = self._shared_state_for()
        if shared_state is not None:
            return shared_state.sinc_key == key
        return self._sinc_verified_key == key

    def apply_waveform_selection(self) -> bool:
        if self._sinc_selected():
            return self._apply_sinc_waveform_selection()
        try:
            slice_index = self._parse_waveform_slice(self.rom_slice_var.get())
            descriptor = encode_awp1221_waveform_descriptor(
                source_code=self.WAVEFORM_SOURCE_CODES[self.rom_source_var.get()],
                micro_bias_code=self._parse_binary_option(self.rom_micro_bias_var.get()),
                waveform_code=self.ROM_WAVEFORM_CODES[self.rom_waveform_var.get()],
                depth_code=awp1221_waveform_depth_code(self.rom_depth_var.get()),
                half_cycle_code=self._parse_binary_option(self.rom_half_cycle_var.get()),
                phase_code=self._parse_binary_option(self.rom_phase_var.get()),
                point_time=int(self.rom_point_time_var.get().strip(), 0),
                repeat_count=int(self.rom_repeat_var.get().strip(), 0),
                infinite_loop=bool(self.rom_infinite_loop_var.get()),
            )
            offset_code = self._parse_binary_option(self.rom_offset_var.get())
            vpk_code = self._parse_binary_option(self.rom_vpk_var.get())
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return False
        if not self._ensure_connected():
            return False
        device_addr = self.device_addr()
        auto_serial_enabled = self._auto_serial_enabled()
        self._invalidate_sinc_verified()

        self._set_waveform_write_busy(True)
        _run_ui_background_task(
            self,
            lambda: self._write_waveform_selection(
                device_addr,
                auto_serial_enabled,
                slice_index,
                descriptor,
                offset_code,
                vpk_code,
            ),
            on_done=lambda _result: self._finish_waveform_selection(None),
            on_error=lambda error: self._finish_waveform_selection(str(error)),
        )
        return True

    def _schedule_button_rom_preview(self, *_args) -> None:
        if getattr(self, "_rom_preview_job", None) is not None:
            return
        def run() -> None:
            self._rom_preview_job = None
            try:
                if not self.winfo_ismapped():
                    return
                if not hasattr(self, "rom_canvas") or self.rom_canvas.winfo_width() <= 1:
                    retries = getattr(self, "_rom_preview_retries", 0)
                    if retries < 2:
                        self._rom_preview_retries = retries + 1
                        self._schedule_button_rom_preview()
                    return
                self._rom_preview_retries = 0
                self._draw_button_rom_preview()
            except tk.TclError:
                pass
        try:
            self._rom_preview_job = self.after_idle(run)
        except tk.TclError:
            self._rom_preview_job = None

    def _button_rom_preview_signature(self):
        canvas = getattr(self, "rom_canvas", None)
        if canvas is None:
            return None
        try:
            size = (canvas.winfo_width(), canvas.winfo_height())
        except tk.TclError:
            return None
        if size[0] <= 1 or size[1] <= 1:
            return None
        colors = getattr(self.ctx, "colors", {})
        theme = (
            colors.get("primary"), colors.get("border"), colors.get("canvas_bg"),
            getattr(self, "language", None), self.mode_key,
        )
        if self._sinc_selected():
            try:
                plan = self._build_current_sinc_plan()
            except (TypeError, ValueError, AttributeError) as exc:
                return ("sinc-error", size, str(exc), theme)
            return ("sinc", size, plan, theme)
        try:
            return (
                "rom", size, theme,
                self.rom_waveform_var.get(), self.rom_depth_var.get(), self.rom_point_time_var.get(),
                self.rom_phase_var.get(), self.rom_half_cycle_var.get(), self.rom_vpk_var.get(),
                self.rom_micro_bias_var.get(), self.rom_offset_var.get(),
            )
        except (AttributeError, tk.TclError):
            return None

    def _draw_button_rom_preview(self) -> None:
        if not hasattr(self, "rom_canvas"):
            return
        signature = self._button_rom_preview_signature()
        if signature is None or signature == getattr(self, "_rom_preview_drawn", None):
            return
        self._rom_preview_drawn = signature
        if self._sinc_selected():
            self._draw_sinc_preview()
            return
        canvas = self.rom_canvas
        try:
            waveform = self.rom_waveform_var.get()
            depth = int(self.rom_depth_var.get().strip(), 0)
            point_time = int(self.rom_point_time_var.get().strip(), 0)
            phase_code = self._parse_binary_option(self.rom_phase_var.get())
            half_cycle_code = self._parse_binary_option(self.rom_half_cycle_var.get())
            coarse_code = self._parse_binary_option(self.rom_vpk_var.get())
            fine_code = self._parse_binary_option(self.rom_micro_bias_var.get())
            offset_code = self._parse_binary_option(self.rom_offset_var.get())
            if depth <= 0 or point_time <= 0:
                raise ValueError
            effective_depth = awp1221_waveform_effective_depth_points(
                waveform,
                depth,
                half_cycle_code,
            )
            period_us = effective_depth * point_time
            frequency_hz = 1_000_000.0 / period_us
        except (TypeError, ValueError, ZeroDivisionError, AttributeError):
            canvas.delete("all")
            metric = getattr(self, "rom_metric_var", None)
            frequency = getattr(self, "rom_frequency_var", None)
            if metric is not None:
                metric.set("")
            if frequency is not None:
                frequency.set("")
            return

        self.rom_metric_var.set(
            self.tr("awp1221_rom_metric", depth=effective_depth, period=f"{period_us:.0f}")
        )
        self.rom_frequency_var.set(self.tr("awp1221_rom_frequency", frequency=f"{frequency_hz:.3f}"))

        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        left, right, top, bottom = 48, 20, 24, 72
        plot_width = max(1, width - left - right)
        plot_height = max(1, height - top - bottom)
        middle = top + plot_height / 2
        colors = getattr(self.ctx, "colors", {})
        border = colors.get("border", "#CBD5E1")
        primary = colors.get("primary", "#2563EB")
        muted = colors.get("muted", "#64748B")
        canvas.create_rectangle(left, top, left + plot_width, top + plot_height, outline=border)
        canvas.create_line(left, middle, left + plot_width, middle, fill=border)

        positions = [index / 160 for index in range(161)]
        values = [
            awp1221_waveform_preview_voltage(
                waveform,
                position,
                half_cycle_code,
                phase_code,
                coarse_code,
                fine_code,
                offset_code,
            )
            for position in positions
        ]
        peak = max((abs(value) for value in values), default=0.0)
        scale = plot_height * 0.38 / max(peak, 1.0)
        coordinates: list[float] = []
        for position, value in zip(positions, values):
            coordinates.extend((left + position * plot_width, middle - value * scale))
        canvas.create_line(*coordinates, fill=primary, width=2, smooth=True)
        if getattr(self, "mode_key", None) == "button_mode":
            self._annotate_button_preview(values, positions, left, plot_width, middle, scale)
            self._draw_button_time_axis(period_us, left, plot_width, height - bottom, total=False)
            return
        vmin = min(values)
        vmax = max(values)
        canvas.create_text(
            left,
            height - 34,
            anchor="w",
            fill=muted,
            text=f"{waveform} | {effective_depth} points | {period_us:.0f} us | {frequency_hz:.3f} Hz",
        )
        canvas.create_text(
            left,
            height - 16,
            anchor="w",
            fill=muted,
            text=f"{vmin:g} V .. {vmax:g} V",
        )

    def _draw_sinc_preview(self) -> None:
        colors = getattr(self.ctx, "colors", {})
        canvas = self.rom_canvas
        canvas.delete("all")
        try:
            plan = self._build_current_sinc_plan()
        except (TypeError, ValueError, AttributeError) as exc:
            canvas.create_text(
                max(1, canvas.winfo_width() // 2),
                max(1, canvas.winfo_height() // 2),
                anchor="center",
                fill=colors.get("muted", "#64748B"),
                text=str(exc),
            )
            self.rom_metric_var.set("")
            self.rom_frequency_var.set("")
            return
        if plan.solution is None:
            message = self._sinc_reason_message(plan.reason)
            canvas.create_text(
                max(1, canvas.winfo_width() // 2),
                max(1, canvas.winfo_height() // 2),
                anchor="center",
                fill=colors.get("muted", "#64748B"),
                text=message,
            )
            self.rom_metric_var.set("")
            self.rom_frequency_var.set("")
            return
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        left, right, top, bottom = 48, 20, 24, 150 if getattr(self, "mode_key", None) == "button_mode" else 24
        plot_width = max(1, width - left - right)
        segment_count = 4 if plan.serial in {"serial3", "serial4"} else 3
        if getattr(self, "mode_key", None) == "button_mode" and plot_width < segment_count * 130:
            bottom += 66
        plot_height = max(1, height - top - bottom)
        middle = top + plot_height / 2
        peak = max(plan.solution.peak_volts, plan.solution.trough_volts, 1.0)
        scale = plot_height * 0.38 / peak
        border = colors.get("border", "#CBD5E1")
        primary = colors.get("primary", "#2563EB")
        canvas.create_rectangle(left, top, left + plot_width, top + plot_height, outline=border)
        canvas.create_line(left, middle, left + plot_width, middle, fill=border)
        samples_per_segment = 54
        values: list[float] = []
        serial = plan.serial
        if serial in {"serial3", "serial4"}:
            full_sine = (
                plan.solution.segments[0],
                0b00,
                plan.params.depth_points
                * plan.params.times_us[0]
                * plan.params.sin_repeat_count,
                plan.params.sin_repeat_count,
                True,
            )
            preview_segments = [full_sine]
            preview_segments.extend(
                (
                    segment,
                    segment.half_cycle_code,
                    (plan.params.depth_points // 2) * point_time,
                    1,
                    False,
                )
                for segment, point_time in zip(plan.solution.segments, plan.params.times_us[1:])
            )
            total_time = sum(duration for _segment, _code, duration, _repeat, _wrap in preview_segments)
        else:
            preview_segments = [
                (
                    segment,
                    segment.half_cycle_code,
                    (plan.params.depth_points // 2) * point_time,
                    1,
                    False,
                )
                for segment, point_time in zip(plan.solution.segments, plan.params.times_us)
            ]
            total_time = sum(duration for _segment, _code, duration, _repeat, _wrap in preview_segments)
        # Generate each different cycle once; repeat count only affects the
        # display plan and the true time labels, never the sampling budget.
        cycles = []
        for index, (segment, half_cycle_code, duration, repeats, _wrap) in enumerate(preview_segments):
            amplitude = segment.vpp_volts / 2
            samples = [segment.offset_volts + amplitude * awp1221_waveform_preview_value(
                "SINE", sample / (samples_per_segment - 1), half_cycle_code, 0)
                for sample in range(samples_per_segment)]
            cycles.append(PreviewSegment(str(index), samples, repeats, duration / repeats))
        layout = compact_preview(cycles, merge_adjacent=False)
        x_positions: list[float] = []
        segment_ranges = []
        for source_index in range(len(cycles)):
            first_sample = len(values)
            for part in layout.parts:
                if part.omitted or part.source_index != source_index:
                    continue
                coordinates = []
                for index, value in enumerate(part.samples):
                    position = (part.start + (part.end-part.start)*index/(len(part.samples)-1)) / layout.display_total
                    values.append(value)
                    x_positions.append(position)
                    coordinates.extend((left + position*plot_width, middle - value*scale))
                if len(coordinates) >= 4:
                    canvas.create_line(*coordinates, fill=primary, width=2, smooth=True, tags="wave_curve")
            segment_ranges.append((first_sample, len(values)))
        draw_omissions(canvas, layout, left, plot_width, middle, colors)
        if getattr(self, "mode_key", None) == "button_mode":
            self._annotate_button_preview(values, x_positions, left, plot_width, middle, scale,
                                          segment_ranges, plot_bottom=height - bottom)
            self._draw_button_time_axis(total_time, left, plot_width, height - bottom, total=True,
                segment_durations=tuple(segment[2] for segment in preview_segments),
                sin_repeat=plan.params.sin_repeat_count,
                node_positions=tuple(layout.position(time) for time in layout.segment_ends))
        if serial in {"serial3", "serial4"}:
            sine_period = plan.params.depth_points * plan.params.times_us[0]
            frequency_hz = 1_000_000.0 / sine_period
            self.rom_metric_var.set(
                self.tr("awp1221_rom_metric", depth=plan.params.depth_points, period=total_time)
            )
            self.rom_frequency_var.set(
                self.tr("awp1221_sinc_sin_frequency", frequency=f"{frequency_hz:.3f}")
            )
        else:
            self.rom_metric_var.set("")
            self.rom_frequency_var.set("")

    def _draw_button_time_axis(self, duration_us, left, plot_width, bottom, *, total,
                               segment_durations=(), sin_repeat=1, node_positions=()) -> None:
        canvas = self.rom_canvas
        color = self.colors["muted"]
        times = [duration_us * index / 4 for index in range(5)]
        if segment_durations:
            times = [0]
            for duration in segment_durations:
                times.append(times[-1] + duration)
        occupied = []
        axis_labels = []
        for index, time_us in enumerate(times):
            position = node_positions[index] if node_positions else time_us / max(duration_us, 1)
            x = left + plot_width * position
            canvas.create_line(x, bottom, x, bottom + 4, fill=color)
            label = canvas.create_text(x, bottom + 8,
                anchor="nw" if index == 0 else "ne" if index == len(times) - 1 else "n",
                fill=color, text=f"{time_us / 1000:g} ms", tags="time_axis")
            if segment_durations:
                # Very short segments may share nearly the same x position.
                # Stagger the node labels; each segment card also states its endpoint.
                box = canvas.bbox(label)
                if any(box[0] < other[2] + 5 and box[2] > other[0] - 5 and
                       box[1] < other[3] + 2 and box[3] > other[1] - 2 for other in occupied):
                    canvas.move(label, 0, 17)
                    box = canvas.bbox(label)
                if any(box[0] < other[2] + 5 and box[2] > other[0] - 5 and
                       box[1] < other[3] + 2 and box[3] > other[1] - 2 for other in occupied):
                    canvas.delete(label)
                else:
                    occupied.append(box)
            else:
                box = canvas.bbox(label)
                clashes = [(item, old) for item, old in axis_labels
                           if box[0] < old[2]+5 and box[2] > old[0]-5]
                if clashes and index != len(times)-1:
                    canvas.delete(label)
                else:
                    for item, old in clashes:
                        if item == axis_labels[0][0]:
                            canvas.move(label, 0, 17)
                        else:
                            canvas.delete(item)
                    axis_labels.append((label, canvas.bbox(label)))
        if segment_durations:
            names = [self.tr("awp1221_segment_" + key) for key in ("rise", "fall", "return")]
            if len(segment_durations) == 4:
                names.insert(0, self.tr("awp1221_segment_sin", repeat=sin_repeat))
            columns = len(segment_durations) if plot_width >= len(segment_durations) * 130 else 2
            card_width = plot_width / columns
            for index, (name, duration) in enumerate(zip(names, segment_durations)):
                row, column = divmod(index, columns)
                x = left + card_width * column
                y = bottom + row * 66
                canvas.create_rectangle(x + 2, y + 48, x + card_width - 2, y + 108,
                    fill=self.colors["panel_alt"], outline=self.colors["border"], tags="sinc_segment_card")
                canvas.create_text(x + card_width / 2, y + 53, anchor="n", justify="center",
                    width=max(1, card_width - 10), fill=self.colors["text"],
                    text=self.tr("awp1221_segment_time", name=name, duration=f"{duration / 1000:g}",
                                 end=f"{times[index + 1] / 1000:g}"),
                    tags=("sinc_segment_duration", f"sinc_segment_{index}"))
        key = "awp1221_preview_total_time" if total else "awp1221_preview_period"
        summary_offset = 120 + (66 if segment_durations and len(segment_durations) > columns else 0) if segment_durations else 36
        canvas.create_text(left, bottom + summary_offset, anchor="nw", fill=self.colors["text"],
                           text=self.tr(key, duration=f"{duration_us / 1000:g}"), tags="wave_duration")

    def _annotate_button_preview(self, values, positions, left, plot_width, ground, scale,
                                 segments=None, *, plot_bottom=None) -> None:
        if self.mode_key != "button_mode" or not values:
            return
        canvas = self.rom_canvas
        colors = self.colors
        canvas.create_line(left, ground, left + plot_width, ground,
                           fill=colors["muted"], dash=(4, 3))
        canvas.create_text(left + 4, ground + 8, text="GND 0 V", anchor="nw", fill=colors["muted"])
        marked = set()
        for index, (start, end) in enumerate(segments or [(0, len(values))]):
            segment = values[start:end]
            if not segment:
                continue
            if start:
                x = left + positions[start] * plot_width
                canvas.create_line(x, 20, x, plot_bottom if plot_bottom is not None else canvas.winfo_height() - 70,
                                   fill=colors["border"], dash=(2, 4))
            for kind, extreme, color, anchor in (
                ("peak", max(segment), colors.get("danger", "#DC2626"), "sw"),
                ("trough", min(segment), colors["primary"], "nw"),
            ):
                if abs(extreme) < 0.005:
                    continue
                sample = start + segment.index(extreme)
                x = left + positions[sample] * plot_width
                marker = (round(x, 1), round(extreme, 2), kind)
                if marker in marked:
                    continue
                marked.add(marker)
                y = ground - extreme * scale
                canvas.create_line(x, ground, x, y, fill=color, arrow=tk.LAST, width=1.5)
                canvas.create_line(x - 5, y, x + 5, y, fill=color)
                canvas.create_text(min(x + 4, left + plot_width - 90), y, anchor=anchor, fill=color,
                                   text=f"{self.tr('awp1221_preview_' + kind)} {extreme:+.2f} V")

    def _show_stage(self, stage_index: int) -> None:
        if stage_index not in self._stage_frames:
            return
        for frame in self._stage_frames.values():
            frame.grid_remove()
        self._stage_frames[stage_index].grid()
        for index, button in self._stage_buttons.items():
            button.configure(style="Selected.TButton" if index == stage_index else "Secondary.TButton")
        self._visible_stage_index = stage_index
        self._refresh_adc_bits()
        self._push_realtime_thresholds()

    def _register_text(self, widget: tk.Widget, key: str) -> None:
        self._translated_widgets.append((widget, key))

    def _add_help_button(self, parent: tk.Misc, row: int, column: int, text_key: str) -> None:
        label = ttk.Label(
            parent,
            text=self.tr("awp1018_help_symbol"),
            style="Muted.TLabel",
            cursor="question_arrow",
        )
        label.grid(row=row, column=column, sticky="w", padx=(4, 0))
        self._tooltips.append(SimpleTooltip(label, lambda key=text_key: self.tr(key)))

    def _form_label(
        self,
        parent: tk.Misc,
        key: str,
        *,
        row: int,
        column: int,
    ) -> ttk.Label:
        help_keys = {
            "awp1221_ovv_time": "awp1221_ovv_time_help",
            "awp1221_press_ovv_time": "awp1221_ovv_time_help",
            "awp1221_release_ovv_time": "awp1221_ovv_time_help",
            "awp1221_zero_time": "awp1221_zero_time_help",
            "awp1221_press_return_bit_time": "awp1221_press_return_bit_time_help",
            "awp1221_release_return_bit_time": "awp1221_release_return_bit_time_help",
        }
        if key in help_keys:
            holder = ttk.Frame(parent, style="Panel.TFrame")
            holder.grid(row=row, column=column, sticky="w", padx=(0 if column == 0 else 12, 8), pady=5)
            label = ttk.Label(holder, style="Panel.TLabel")
            label.grid(row=0, column=0, sticky="w")
            self._register_text(label, key)
            self._add_help_button(holder, 0, 1, help_keys[key])
            return label
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=column, sticky="w", padx=(0 if column == 0 else 12, 8), pady=5)
        self._register_text(label, key)
        return label

    def _form_check(
        self,
        parent: tk.Misc,
        key: str,
        variable: tk.Variable,
        *,
        row: int,
        column: int,
        columnspan: int = 1,
        command: Callable[[], None] | None = None,
    ) -> ttk.Checkbutton:
        check = ttk.Checkbutton(parent, variable=variable, command=command)
        check.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            sticky="w",
            padx=(0 if column == 0 else 12, 8),
            pady=5,
        )
        self._register_text(check, key)
        return check

    def _form_check_with_help(
        self,
        parent: tk.Misc,
        key: str,
        variable: tk.Variable,
        help_key: str,
        *,
        row: int,
        column: int,
        columnspan: int = 1,
        command: Callable[[], None] | None = None,
    ) -> ttk.Frame:
        holder = ttk.Frame(parent, style="Panel.TFrame")
        holder.grid(row=row, column=column, columnspan=columnspan, sticky="w", pady=5)
        self._form_check(holder, key, variable, row=0, column=0, command=command)
        self._add_help_button(holder, 0, 1, help_key)
        return holder

    def _section_label(self, parent: tk.Misc, key: str, *, row: int) -> ttk.Label:
        label = ttk.Label(parent, style="SectionTitle.TLabel")
        label.grid(row=row, column=0, columnspan=2, sticky="w", pady=(12, 5))
        self._register_text(label, key)
        return label

    def _build_adc_stage(self, parent: ttk.Frame, stage_index: int) -> None:
        for _ in self._build_adc_stage_steps(parent, stage_index):
            pass

    def _build_adc_stage_steps(self, parent: ttk.Frame, stage_index: int) -> Iterator[None]:
        parent.columnconfigure(0, weight=0, minsize=0)
        parent.columnconfigure(1, weight=1, minsize=0)
        parent.columnconfigure(2, weight=0, minsize=0)
        parent.columnconfigure(3, weight=0, minsize=0)

        release_var = tk.StringVar(value=AWP1221_ADC_TIME_LABELS[0])
        press_var = tk.StringVar(value=AWP1221_ADC_TIME_LABELS[0])
        yield
        flag_vars = {name: tk.BooleanVar(value=False) for name in AWP1221_ADC_FLAG_BITS}
        press_threshold = self._make_threshold_vars()
        release_threshold = self._make_threshold_vars()
        slope_vars = {
            "release_below": tk.BooleanVar(value=False),
            "release_slope": tk.StringVar(value="0"),
            "press_below": tk.BooleanVar(value=False),
            "press_slope": tk.StringVar(value="0"),
        }
        wave_vars = {
            "release_repeat": tk.StringVar(value="0"),
            "release_wave": tk.StringVar(value="slice0"),
            "press_repeat": tk.StringVar(value="0"),
            "press_wave": tk.StringVar(value="slice0"),
        }
        event_mode_var = tk.StringVar(value="press")
        yield
        use_high_12_bits = tk.BooleanVar(value=False)
        use_high_12_bits.trace_add("write", lambda *_: self._refresh_adc_bits())
        threshold_entries: dict[str, ttk.Entry] = {}
        self._stage_vars.append(
            {
                "release": release_var,
                "press": press_var,
                "flags": flag_vars,
                "press_threshold": press_threshold,
                "release_threshold": release_threshold,
                "slope": slope_vars,
                "wave": wave_vars,
                "event_mode": event_mode_var,
                "use_high_12_bits": use_high_12_bits,
                "threshold_entries": threshold_entries,
            }
        )

        commit_stage = lambda index=stage_index: self._commit_live_stage(index)
        self._form_label(parent, "awp1221_press_ovv_time", row=0, column=0)
        yield
        press_time_combo = ttk.Combobox(
            parent,
            textvariable=press_var,
            values=AWP1221_ADC_TIME_LABELS,
            state="readonly",
            width=16,
        )
        press_time_combo.grid(row=0, column=1, sticky="ew", pady=5)
        self._bind_live_choice(press_time_combo, commit_stage)
        self._form_label(parent, "awp1221_release_ovv_time", row=1, column=0)
        release_time_combo = ttk.Combobox(
            parent,
            textvariable=release_var,
            values=AWP1221_ADC_TIME_LABELS,
            state="readonly",
            width=16,
        )
        release_time_combo.grid(row=1, column=1, sticky="ew", pady=5)
        yield
        self._bind_live_choice(release_time_combo, commit_stage)

        self._form_label(parent, "awp1221_press_jump_target", row=2, column=0)
        press_jump_combo = ttk.Combobox(
            parent,
            textvariable=press_threshold["next_config"],
            values=("Stage0", "Stage1"),
            state="readonly",
            width=12,
        )
        press_jump_combo.grid(row=2, column=1, sticky="ew", pady=5)
        self._bind_live_choice(press_jump_combo, commit_stage)
        self._form_label(parent, "awp1221_release_jump_target", row=3, column=0)
        yield
        release_jump_combo = ttk.Combobox(
            parent,
            textvariable=release_threshold["next_config"],
            values=("Stage0", "Stage1"),
            state="readonly",
            width=12,
        )
        release_jump_combo.grid(row=3, column=1, sticky="ew", pady=5)
        self._bind_live_choice(release_jump_combo, commit_stage)

        self._form_label(parent, "awp1221_press_threshold", row=4, column=0)
        threshold_entries["press"] = self._threshold_entry_with_hint(
            parent, press_threshold["threshold"], row=4, column=1
        )
        self._bind_live_text(threshold_entries["press"], commit_stage)
        yield
        self._form_label(parent, "awp1221_release_threshold", row=5, column=0)
        threshold_entries["release"] = self._threshold_entry_with_hint(
            parent, release_threshold["threshold"], row=5, column=1
        )
        self._bind_live_text(threshold_entries["release"], commit_stage)

        self._form_label(parent, "awp1221_press_waveform", row=6, column=0)
        press_wave_combo = ttk.Combobox(
            parent,
            textvariable=wave_vars["press_wave"],
            values=self._trigger_waveform_options(),
            state="readonly",
            width=12,
        )
        press_wave_combo.grid(row=6, column=1, sticky="ew", pady=5)
        yield
        self._bind_live_choice(press_wave_combo, commit_stage)
        self._full_wave_combos.append(press_wave_combo)
        self._form_label(parent, "awp1221_release_waveform", row=7, column=0)
        release_wave_combo = ttk.Combobox(
            parent,
            textvariable=wave_vars["release_wave"],
            values=self._trigger_waveform_options(),
            state="readonly",
            width=12,
        )
        release_wave_combo.grid(row=7, column=1, sticky="ew", pady=5)
        self._bind_live_choice(release_wave_combo, commit_stage)
        yield
        self._full_wave_combos.append(release_wave_combo)

        repeat_values = tuple(str(value) for value in range(8))
        self._form_label(parent, "awp1221_press_repeat", row=8, column=0)
        press_repeat_combo = ttk.Combobox(
            parent,
            textvariable=wave_vars["press_repeat"],
            values=repeat_values,
            state="readonly",
            width=12,
        )
        press_repeat_combo.grid(row=8, column=1, sticky="ew", pady=5)
        self._bind_live_choice(press_repeat_combo, commit_stage)
        yield
        self._form_label(parent, "awp1221_release_repeat", row=9, column=0)
        release_repeat_combo = ttk.Combobox(
            parent,
            textvariable=wave_vars["release_repeat"],
            values=repeat_values,
            state="readonly",
            width=12,
        )
        release_repeat_combo.grid(row=9, column=1, sticky="ew", pady=5)
        self._bind_live_choice(release_repeat_combo, commit_stage)

        debounce_row = ttk.Frame(parent, style="Panel.TFrame")
        debounce_row.grid(row=10, column=0, columnspan=2, sticky="ew", pady=5)
        yield
        debounce_row.columnconfigure(0, weight=1)
        self._form_check(
            debounce_row,
            "awp1221_press_debounce_enable",
            press_threshold["debounce_enabled"],
            row=0,
            column=0,
            command=commit_stage,
        )
        self._form_check(
            debounce_row,
            "awp1221_release_debounce_enable",
            release_threshold["debounce_enabled"],
            row=1,
            column=0,
            command=commit_stage,
        )
        self._form_check_with_help(
            debounce_row,
            "awp1221_adc_precision",
            use_high_12_bits,
            "awp1221_adc_precision_tip",
            row=2,
            column=0,
            command=commit_stage,
        )
        self._form_check(
            parent,
            "awp1221_press_below_threshold",
            press_threshold["below_threshold"],
            row=11,
            column=0,
            columnspan=2,
            command=commit_stage,
        )
        self._form_check(
            parent,
            "awp1221_release_below_threshold",
            release_threshold["below_threshold"],
            row=12,
            column=0,
            columnspan=2,
            command=commit_stage,
        )
        yield
        self._section_label(parent, "awp1221_adc_event_mode", row=13)
        press_mode_radio = ttk.Radiobutton(
            parent,
            variable=event_mode_var,
            value="press",
            command=lambda index=stage_index: self._on_full_event_mode(index),
        )
        press_mode_radio.grid(row=14, column=0, columnspan=2, sticky="w", pady=5)
        self._register_text(press_mode_radio, "awp1221_press_mode")
        release_mode_radio = ttk.Radiobutton(
            parent,
            variable=event_mode_var,
            value="release",
            command=lambda index=stage_index: self._on_full_event_mode(index),
        )
        release_mode_radio.grid(row=15, column=0, columnspan=2, sticky="w", pady=5)
        yield
        self._register_text(release_mode_radio, "awp1221_release_mode")

        self._section_label(parent, "awp1221_event_actions", row=16)
        press_actions = ttk.Frame(parent, style="Panel.TFrame")
        press_actions.grid(row=17, column=0, columnspan=2, sticky="ew")
        release_actions = ttk.Frame(parent, style="Panel.TFrame")
        release_actions.grid(row=18, column=0, columnspan=2, sticky="ew")
        yield
        self._form_check(
            press_actions,
            "awp1221_press_stop_adc",
            flag_vars["stop_p"],
            row=0,
            column=0,
            command=commit_stage,
        )
        self._form_check(
            release_actions,
            "awp1221_release_stop_adc",
            flag_vars["stop_r"],
            row=0,
            column=0,
            command=commit_stage,
        )

        self._form_label(parent, "awp1221_press_slope_threshold", row=19, column=0)
        press_slope_entry = ttk.Entry(parent, textvariable=slope_vars["press_slope"], width=12)
        press_slope_entry.grid(row=19, column=1, sticky="ew", pady=5)
        self._bind_live_text(press_slope_entry, commit_stage)
        yield
        self._form_label(parent, "awp1221_release_slope_threshold", row=20, column=0)
        release_slope_entry = ttk.Entry(parent, textvariable=slope_vars["release_slope"], width=12)
        release_slope_entry.grid(row=20, column=1, sticky="ew", pady=5)
        self._bind_live_text(release_slope_entry, commit_stage)
        self._form_check(
            parent,
            "awp1221_press_slope_below",
            slope_vars["press_below"],
            row=21,
            column=0,
            columnspan=2,
            command=commit_stage,
        )
        self._form_check(
            parent,
            "awp1221_release_slope_below",
            slope_vars["release_below"],
            row=22,
            column=0,
            columnspan=2,
            command=commit_stage,
        )
        yield

        self._sync_full_event_mode(stage_index)

    @staticmethod
    def _make_threshold_vars() -> dict[str, tk.Variable]:
        return {
            "debounce_enabled": tk.BooleanVar(value=False),
            "next_config": tk.StringVar(value="Stage0"),
            "below_threshold": tk.BooleanVar(value=False),
            "threshold": tk.StringVar(value="0"),
        }

    def _threshold_entry_with_hint(
        self,
        parent: tk.Misc,
        variable: tk.StringVar,
        *,
        row: int,
        column: int,
    ) -> ttk.Entry:
        holder = ttk.Frame(parent, style="Panel.TFrame")
        holder.grid(row=row, column=column, sticky="ew", pady=5)
        holder.columnconfigure(0, weight=1)
        entry = ttk.Entry(holder, textvariable=variable, width=12)
        entry.grid(row=0, column=0, sticky="ew")
        hint_var = tk.StringVar(value=awp1221_threshold_hex_hint(variable.get()))
        ttk.Label(holder, textvariable=hint_var, style="Muted.TLabel", width=8).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        variable.trace_add("write", lambda *_: hint_var.set(awp1221_threshold_hex_hint(variable.get())))
        return entry

    def _on_full_event_mode(self, stage_index: int) -> None:
        self._sync_full_event_mode(stage_index)
        self._commit_live_stage(stage_index)

    def _sync_full_event_mode(self, stage_index: int) -> None:
        variables = self._stage_vars[stage_index]
        mode = variables["event_mode"].get()
        entries = variables.get("threshold_entries", {})
        if "press" in entries:
            entries["press"].configure(state="normal" if mode == "press" else "disabled")
        if "release" in entries:
            entries["release"].configure(state="normal" if mode == "release" else "disabled")

    def _build_chip_en_section(self, parent: ttk.Frame, *, row: int) -> None:
        host = self.host
        if host is None:
            return
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.grid(row=row, column=0, sticky="ew", pady=(0, self._module_spacing()))
        self._register_module_spacing_widget(section)
        section.columnconfigure(1, weight=1)
        title = ttk.Label(section, style="SectionTitle.TLabel")
        title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._register_text(title, "awp1221_chip_en_title")
        if self.mode_key == "button_mode":
            title.grid_remove()
            self._en_switch_canvas = tk.Canvas(section, width=82, height=40,
                                                highlightthickness=0, takefocus=True,
                                                cursor="hand2")
            self._en_switch_canvas.grid(row=1, column=0, sticky="w", padx=(0, 8))
            def toggle(_event=None):
                host.en_enabled_var.set(not host.en_enabled_var.get())
                host._on_en_toggled()
                return "break"
            for event in ("<Button-1>", "<space>", "<Return>"):
                self._en_switch_canvas.bind(event, toggle)
            trace = host.en_enabled_var.trace_add("write", lambda *_: self._draw_button_en_switch())
            self._en_switch_canvas.bind("<Destroy>", lambda _event: host.en_enabled_var.trace_remove("write", trace))
            self._draw_button_en_switch()
            status = ttk.Label(section, textvariable=host.en_status_var, style="Muted.TLabel", wraplength=135)
            status.grid(row=2, column=0, sticky="w", pady=(8, 0))
            return
        check = ttk.Checkbutton(
            section,
            variable=host.en_enabled_var,
            command=host._on_en_toggled,
        )
        check.grid(row=1, column=0, sticky="w", padx=(0, 8))
        self._register_text(check, "awp1221_en_enabled")
        status = ttk.Label(section, textvariable=host.en_status_var, style="Muted.TLabel")
        status.grid(row=1, column=1, sticky="w")

    def _draw_button_en_switch(self) -> None:
        canvas = getattr(self, "_en_switch_canvas", None)
        if canvas is None:
            return
        colors = self.colors
        canvas.configure(bg=colors["panel"])
        canvas.delete("all")
        enabled = self.host.en_enabled_var.get()
        fill = colors["primary"] if enabled else colors["border"]
        canvas.create_line(21, 20, 61, 20, fill=fill, width=34, capstyle=tk.ROUND)
        center = 61 if enabled else 21
        canvas.create_oval(center - 14, 6, center + 14, 34, fill="#FFFFFF", outline=colors["border"])

    def _build_gpio_settings(self, parent: ttk.Frame, *, row: int) -> None:
        host = self.host
        if host is None:
            return
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.grid(row=row, column=0, sticky="ew", pady=(12, 8))
        section.columnconfigure(1, weight=1)
        title = ttk.Label(section, style="SectionTitle.TLabel")
        title.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        self._register_text(title, "awp1221_gpio_settings")
        for index, (name, pin) in enumerate(AWP1221_GPIO_PINS):
            self._build_gpio_channel(section, host, index, name, pin, row=1 + index)
        buttons = ttk.Frame(section, style="Panel.TFrame")
        buttons.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        read_btn = ttk.Button(
            buttons,
            style="Secondary.TButton",
            command=host.read_gpio_settings,
        )
        read_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["awp1221_gpio_read"] = read_btn
        apply_btn = ttk.Button(
            buttons,
            style="Primary.TButton",
            command=host.apply_gpio_settings,
        )
        apply_btn.grid(row=0, column=1, sticky="ew")
        self._buttons["awp1221_gpio_apply"] = apply_btn

    def _build_gpio_channel(
        self,
        parent: ttk.Frame,
        host: Any,
        index: int,
        name: str,
        pin: str,
        *,
        row: int,
    ) -> None:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        frame.columnconfigure(1, weight=1)
        heading = ttk.Label(frame, style="Panel.TLabel")
        heading.grid(row=0, column=0, sticky="w")
        heading.configure(text=f"{name} ({pin})")
        mcu_label = ttk.Label(
            frame,
            textvariable=host.gpio_mcu_role_vars[index],
            style="Muted.TLabel",
        )
        mcu_label.grid(row=0, column=1, sticky="e")
        host._refresh_gpio_mcu_label(index)
        self._form_label(frame, "awp1221_gpio_category", row=1, column=0)
        category = ttk.Combobox(
            frame,
            textvariable=host.gpio_category_vars[index],
            values=tuple(label for key, label in GPIO_CATEGORY_LABELS.items() if key != "debug"),
            state="readonly",
            width=16,
        )
        category.grid(row=1, column=1, sticky="ew", pady=3)
        category.bind(
            "<<ComboboxSelected>>",
            lambda _event, channel=index: host._on_gpio_category_selected(channel),
        )
        self._form_label(frame, "awp1221_gpio_function", row=2, column=0)
        function = ttk.Combobox(
            frame,
            textvariable=host.gpio_function_vars[index],
            values=host._gpio_function_labels(index),
            state="readonly",
            width=16,
        )
        function.grid(row=2, column=1, sticky="ew", pady=3)
        host.gpio_function_combos[index].append(function)
        drive = ttk.Frame(frame, style="Panel.TFrame")
        drive.grid(row=3, column=0, columnspan=2, sticky="w", pady=3)
        high_btn = ttk.Button(
            drive,
            style="Secondary.TButton",
            command=lambda channel=index: host.gpio_drive(channel, True),
        )
        high_btn.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._register_text(high_btn, "awp1221_gpio_drive_high")
        low_btn = ttk.Button(
            drive,
            style="Secondary.TButton",
            command=lambda channel=index: host.gpio_drive(channel, False),
        )
        low_btn.grid(row=0, column=1, sticky="w")
        self._register_text(low_btn, "awp1221_gpio_drive_low")
        host.gpio_action_widgets[index].extend((high_btn, low_btn))
        host._sync_gpio_actions(index)
        ttk.Label(frame, textvariable=host.gpio_chip_status_vars[index], style="Muted.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(frame, textvariable=host.gpio_mcu_status_vars[index], style="Muted.TLabel").grid(
            row=5, column=0, columnspan=2, sticky="w"
        )

    def _build_adc_settings(self, parent: ttk.Frame, *, row: int) -> None:
        for _ in self._build_adc_settings_steps(parent, row=row):
            pass

    def _build_adc_settings_steps(self, parent: ttk.Frame, *, row: int) -> Iterator[None]:
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.grid(row=row, column=0, sticky="ew", pady=(16, 0))
        section.columnconfigure(1, weight=1)
        section.columnconfigure(3, weight=1)
        title = ttk.Label(section, style="SectionTitle.TLabel")
        title.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        yield
        self._register_text(title, "awp1221_adc_settings")

        debounce_label = ttk.Frame(section, style="Panel.TFrame")
        debounce_label.grid(row=1, column=0, sticky="w", pady=5, padx=(0, 8))
        debounce_text = ttk.Label(debounce_label, style="Panel.TLabel")
        debounce_text.grid(row=0, column=0, sticky="w")
        self._register_text(debounce_text, "awp1221_debounce_time")
        yield
        self._add_help_button(debounce_label, 0, 1, "awp1221_debounce_time_help")
        debounce_entry = ttk.Entry(section, textvariable=self.debounce_time_var, width=16)
        debounce_entry.grid(row=1, column=1, sticky="ew", pady=5)
        self._bind_live_text(debounce_entry, self._commit_live_settings)

        self._form_label(section, "awp1221_adc_sample_update", row=1, column=2)
        sample_combo = ttk.Combobox(
            section,
            textvariable=self.adc_sample_update_var,
            values=AWP1221_ADC_SAMPLE_UPDATE_OPTIONS,
            state="readonly",
            width=16,
        )
        yield
        sample_combo.grid(row=1, column=3, sticky="ew", pady=5)
        self._bind_live_choice(sample_combo, self._commit_live_settings)

        self._form_label(section, "awp1221_zero_time", row=2, column=0)
        zero_combo = ttk.Combobox(
            section,
            textvariable=self.zero_time_var,
            values=AWP1221_ZERO_TIME_OPTIONS,
            state="readonly",
            width=16,
        )
        zero_combo.grid(row=2, column=1, sticky="ew", pady=5)
        self._bind_live_choice(zero_combo, self._commit_live_settings)
        yield

        self._form_label(section, "awp1221_adc_ldo", row=2, column=2)
        ldo_combo = ttk.Combobox(
            section,
            textvariable=self.adc_ldo_var,
            values=AWP1221_ADC_LDO_OPTIONS,
            state="readonly",
            width=16,
        )
        ldo_combo.grid(row=2, column=3, sticky="ew", pady=5)
        self._bind_live_choice(ldo_combo, self._commit_live_settings)

        self._form_label(section, "awp1221_press_return_bit_time", row=3, column=0)
        press_return_combo = ttk.Combobox(
            section,
            textvariable=self.press_return_bit_time_var,
            values=AWP1221_RETURN_BIT_TIME_OPTIONS,
            state="readonly",
            width=16,
        )
        yield
        press_return_combo.grid(row=3, column=1, sticky="ew", pady=5)
        self._bind_live_choice(press_return_combo, self._commit_live_settings)

        self._form_label(section, "awp1221_release_return_bit_time", row=3, column=2)
        release_return_combo = ttk.Combobox(
            section,
            textvariable=self.release_return_bit_time_var,
            values=AWP1221_RETURN_BIT_TIME_OPTIONS,
            state="readonly",
            width=16,
        )
        release_return_combo.grid(row=3, column=3, sticky="ew", pady=5)
        self._bind_live_choice(release_return_combo, self._commit_live_settings)
        yield

        zps_frame = ttk.Frame(section, style="Panel.TFrame")
        zps_frame.grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))
        zps_check = ttk.Checkbutton(
            zps_frame,
            variable=self.zps_enabled_var,
            command=self._commit_live_settings,
        )
        zps_check.grid(row=0, column=0, sticky="w")
        self._register_text(zps_check, "awp1221_zps_enable")
        self._add_help_button(zps_frame, 0, 1, "awp1221_zps_enable_help")
        yield
        zps_detect_check = ttk.Checkbutton(
            zps_frame,
            variable=self.zps_detect_var,
            command=self._commit_live_settings,
        )
        zps_detect_check.grid(row=0, column=2, sticky="w", padx=(14, 0))
        self._register_text(zps_detect_check, "awp1221_zps_detect")
        self._add_help_button(zps_frame, 0, 3, "awp1221_zps_detect_help")
        sleep_sensitivity_check = ttk.Checkbutton(
            zps_frame,
            variable=self.sleep_sensitivity_var,
            command=self._commit_live_settings,
        )
        sleep_sensitivity_check.grid(row=0, column=4, sticky="w", padx=(14, 0))
        yield
        self._register_text(sleep_sensitivity_check, "awp1221_sleep_sensitivity")
        self._add_help_button(zps_frame, 0, 5, "awp1221_sleep_sensitivity_help")

        if self.mode_key != "button_mode":
            self._stack_full_adc_settings(section)
            yield from self._build_adc_readout_steps(section, row=8)
        else:
            # Global ADC values: labels always stay on one line; the six fields
            # use a single row when wide enough, otherwise three per row.
            fields: list[list[tk.Widget | None]] = [[None, None] for _ in range(6)]
            for widget in section.winfo_children():
                info = widget.grid_info()
                old_row, old_column = int(info["row"]), int(info["column"])
                if old_row in (1, 2, 3):
                    fields[(old_row - 1) * 2 + old_column // 2][old_column % 2] = widget
                    if isinstance(widget, (ttk.Entry, ttk.Combobox)):
                        widget.configure(width=7)
                    elif isinstance(widget, ttk.Label):
                        widget.configure(wraplength=0)
                    else:
                        for child in widget.winfo_children():
                            if isinstance(child, ttk.Label) and child.cget("text") != "?":
                                child.configure(wraplength=0)
                elif old_row == 4:
                    self._button_adc_zps_frame = widget
                elif old_row == 0:
                    widget.grid_configure(columnspan=6)
            self._button_adc_fields = [(label, value) for label, value in fields
                                       if label is not None and value is not None]
            self._button_adc_columns = 0
            self._button_adc_reflow_job = None
            self._button_adc_reflowing = False
            self._reflow_button_adc_settings(section, columns=3)
            reflow = lambda _event=None, frame=section: self._queue_button_adc_reflow(frame)
            section.bind("<Configure>", reflow, add="+")
            for label, _value in self._button_adc_fields:
                label.bind("<Configure>", reflow, add="+")

    def _stack_full_adc_settings(self, section: ttk.Frame) -> None:
        pairs: dict[tuple[int, int], list[tk.Widget | None]] = {}
        zps = None
        title = None
        for widget in section.winfo_children():
            info = widget.grid_info()
            if not info:
                continue
            row, column = int(info["row"]), int(info["column"])
            if row == 0:
                title = widget
            elif row == 4:
                zps = widget
            elif row in (1, 2, 3):
                pairs.setdefault((row, column // 2), [None, None])[column % 2] = widget
        section.columnconfigure(0, weight=0, minsize=0)
        section.columnconfigure(1, weight=1, minsize=0)
        section.columnconfigure(2, weight=0, minsize=0)
        section.columnconfigure(3, weight=0, minsize=0)
        if title is not None:
            title.grid_configure(columnspan=2)
        for index, key in enumerate(((1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1))):
            label, value = pairs[key]
            if label is None or value is None:
                continue
            label.grid_configure(row=1 + index, column=0, columnspan=1, sticky="w",
                                 padx=(0, 8), pady=5)
            value.grid_configure(row=1 + index, column=1, columnspan=1, sticky="ew",
                                 padx=0, pady=5)
            if isinstance(value, (ttk.Combobox, ttk.Entry)):
                value.configure(width=12)
        if zps is None:
            return
        zps.grid_configure(row=7, column=0, columnspan=2, sticky="ew")
        children = [child for child in zps.winfo_children() if child.grid_info()]
        children.sort(key=lambda child: int(child.grid_info()["column"]))
        for index, child in enumerate(children):
            child.grid_configure(row=index // 2, column=index % 2, sticky="w",
                                 padx=(0, 8), pady=2)

    def _queue_button_adc_reflow(self, section: ttk.Frame, _event: tk.Event | None = None) -> None:
        if (
            getattr(self, "_button_adc_reflow_job", None) is not None
            or getattr(self, "_button_adc_reflowing", False)
            or getattr(self, "_button_stage_reflowing", False)
        ):
            return
        def run() -> None:
            self._button_adc_reflow_job = None
            try:
                if section.winfo_exists():
                    self._reflow_button_adc_settings(section)
                    self._reflow_button_stage_fields(section)
            except tk.TclError:
                pass
        try:
            self._button_adc_reflow_job = self.after_idle(run)
        except tk.TclError:
            self._button_adc_reflow_job = None

    def _button_adc_decision_width(self, section: ttk.Frame) -> int:
        try:
            width = section.winfo_width()
        except tk.TclError:
            return 0
        if width <= 1:
            return width
        host = getattr(self, "host", None)
        area = getattr(host, "mode_scroll", None) if host is not None else None
        if area is None:
            return width
        try:
            canvas_width = area.canvas.winfo_width()
            window_width = area._window_item_span("width")
        except tk.TclError:
            return width
        if canvas_width > 1 and window_width > canvas_width:
            return max(1, int(width * canvas_width / window_width))
        return width

    def _reflow_button_adc_settings(self, section: ttk.Frame, *, columns: int | None = None) -> None:
        fields = getattr(self, "_button_adc_fields", ())
        if not fields or not section.winfo_exists() or getattr(self, "_button_adc_reflowing", False):
            return
        current = getattr(self, "_button_adc_columns", 0)
        if columns is None:
            width = self._button_adc_decision_width(section)
            if width <= 1:
                retries = getattr(self, "_button_adc_size_retries", 0)
                if retries < 3:
                    self._button_adc_size_retries = retries + 1
                    self._queue_button_adc_reflow(section)
                return
            self._button_adc_size_retries = 0
            needed = sum(max(label.winfo_reqwidth(), value.winfo_reqwidth()) + 14
                         for label, value in fields)
            wide = len(fields)
            slack = 24
            if current == wide:
                columns = wide if width + slack >= needed else 3
            else:
                columns = wide if width >= needed + slack else 3
        if columns == current:
            return
        self._button_adc_reflowing = True
        self._button_adc_columns = columns
        try:
            for index, (label, value) in enumerate(fields):
                field_row, column = divmod(index, columns)
                label.grid_configure(row=1 + field_row * 2, column=column, columnspan=1,
                                     sticky="w", padx=(0, 14), pady=(4 if field_row else 0, 3))
                value.grid_configure(row=2 + field_row * 2, column=column, columnspan=1,
                                     sticky="w", padx=(0, 14), pady=(0, 3))
            rows = (len(fields) + columns - 1) // columns
            zps = getattr(self, "_button_adc_zps_frame", None)
            if zps is not None:
                zps.grid_configure(row=1 + rows * 2, column=0, columnspan=6, pady=(6, 0))
            for column in range(6):
                section.columnconfigure(column, weight=0)
        finally:
            self._button_adc_reflowing = False

    def _reflow_button_stage_fields(self, section: ttk.Frame, *, columns: int | None = None) -> None:
        layouts = getattr(self, "_button_stage_layouts", ())
        if not layouts or not section.winfo_exists() or getattr(self, "_button_stage_reflowing", False):
            return
        current = getattr(self, "_button_stage_columns", 5)
        if columns is None:
            width = self._button_adc_decision_width(section)
            if width <= 1:
                return
            fields = layouts[0]["fields"]
            needed = sum(
                max(label.winfo_reqwidth(), value.winfo_reqwidth()) + 12
                for label, value in fields
            ) + 24
            wide = 5
            slack = 16
            if current == wide:
                columns = wide if width + slack >= needed else 3
            else:
                columns = wide if width >= needed + slack else 3
        if columns == current:
            return
        self._button_stage_reflowing = True
        self._button_stage_columns = columns
        try:
            for layout in layouts:
                self._apply_button_stage_columns(layout, columns)
        finally:
            self._button_stage_reflowing = False

    def _apply_button_stage_columns(self, layout: dict, columns: int) -> None:
        parent = layout["frame"]
        for index, (label, value) in enumerate(layout["fields"]):
            field_row, column = divmod(index, columns)
            label.grid_configure(row=field_row * 2, column=column, columnspan=1,
                                 sticky="w", padx=(0, 12), pady=(0, 3))
            value.grid_configure(row=field_row * 2 + 1, column=column, columnspan=1,
                                 sticky="ew", padx=(0, 12), pady=(0, 3))
        rows = (len(layout["fields"]) + columns - 1) // columns
        base = rows * 2
        event_label, event_value = layout["event"]
        event_label.grid_configure(row=base, column=0, columnspan=min(2, columns),
                                   sticky="w", padx=(0, 12), pady=(4, 3))
        event_value.grid_configure(row=base + 1, column=0, columnspan=min(2, columns),
                                   sticky="w", padx=(0, 12), pady=(0, 3))
        checks = layout["checks"]
        if columns >= 5 and len(checks) >= 2:
            checks[0].grid_configure(row=base + 1, column=2, columnspan=1,
                                     sticky="w", padx=(0, 8), pady=0)
            checks[1].grid_configure(row=base + 1, column=3, columnspan=2,
                                     sticky="w", padx=(0, 8), pady=0)
        else:
            for offset, check in enumerate(checks):
                check.grid_configure(row=base + 2 + offset, column=0, columnspan=columns,
                                     sticky="w", padx=(0, 8), pady=(4, 0))
        for column in range(6):
            parent.columnconfigure(column, weight=1 if column < columns else 0, minsize=0)

    def _build_adc_readout(self, section: ttk.Frame, *, row: int) -> None:
        for _ in self._build_adc_readout_steps(section, row=row):
            pass

    def _build_adc_readout_steps(self, section: ttk.Frame, *, row: int) -> Iterator[None]:
        adc_value_label = ttk.Label(section, style="Panel.TLabel")
        button_mode = self.mode_key == "button_mode"
        adc_value_label.grid(row=row, column=0, columnspan=1 if button_mode else 2,
                             sticky="w", pady=(0, 5), padx=(0, 8))
        self._register_text(adc_value_label, "button_adc_value")
        adc_display = ttk.Frame(section, style="Panel.TFrame")
        adc_display.grid(row=row + 1, column=0, columnspan=4 if button_mode else 2,
                         sticky="ew", pady=(0, 5))
        yield
        adc_display.columnconfigure(0, weight=1)
        ttk.Entry(adc_display, textvariable=self.adc_value_var, state="readonly").grid(row=0, column=0, sticky="ew")
        bits = ttk.Frame(adc_display, style="Panel.TFrame")
        bits.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._adc_bit_labels = []
        for column, bit in enumerate((15, 11, 7, 3) if self.mode_key == "button_mode" else range(15, -1, -1)):
            bits.columnconfigure(column, weight=1, uniform="adc_bits")
            label = tk.Label(
                bits,
                text=f"{bit}\n—",
                padx=8 if self.mode_key == "button_mode" else 2,
                pady=3,
                font=("Consolas", 12 if self.mode_key == "button_mode" else 9),
                borderwidth=0,
            )
            label.grid(row=0, column=column, sticky="ew", padx=1)
            self._adc_bit_labels.append(label)
        yield
        self._refresh_adc_bits()

        adc_buttons = ttk.Frame(section, style="Panel.TFrame")
        adc_buttons.grid(row=row + 2, column=0, columnspan=4 if button_mode else 2, sticky="ew", pady=(2, 0))
        for column in range(3):
            adc_buttons.columnconfigure(column, weight=1)

        read_button = ttk.Button(
            adc_buttons,
            style="Secondary.TButton",
            command=self.read_adc_value,
        )
        read_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        yield
        self._buttons["adc_read_current_value"] = read_button

        realtime_button = ttk.Button(
            adc_buttons,
            style="Secondary.TButton",
            command=self.toggle_adc_realtime,
        )
        realtime_button.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self._buttons["adc_read_realtime"] = realtime_button
        if button_mode:
            graph_button = ttk.Button(section, style="Secondary.TButton", command=self.show_adc_graph)
            graph_button.grid(row=row, column=1, columnspan=3, sticky="e", pady=(0, 6))
            self._buttons["awp1221_adc_graph"] = graph_button

        trigger_button = ttk.Button(
            adc_buttons,
            style="Secondary.TButton",
            command=self.toggle_adc_trigger_read,
        )
        yield
        trigger_button.grid(row=0, column=2, sticky="ew")
        self._buttons["adc_read_on_trigger"] = trigger_button

    def _refresh_adc_bits(self) -> None:
        labels = getattr(self, "_adc_bit_labels", ())
        if not labels:
            return
        stages = self._button_stage_vars if self.mode_key == "button_mode" else self._stage_vars
        index = getattr(self, "_visible_button_stage_index", 0) if self.mode_key == "button_mode" else getattr(self, "_visible_stage_index", 0)
        high = bool(stages[index]["use_high_12_bits"].get()) if stages else False
        selected = range(4, 16) if high else range(12)
        dark = self.ctx.state.theme == "dark"
        active_bg, active_fg = ("#476C9B", "#FFFFFF") if dark else ("#A9C7EA", "#111827")
        value = getattr(self, "_adc_raw_value", None)
        if self.mode_key == "button_mode":
            for shift, label in zip((12, 8, 4, 0), labels):
                digit = "—" if value is None else f"{(value >> shift) & 0xF:X}"
                active = shift >= 4 if high else shift <= 8
                label.configure(text=f"{digit}\n{shift + 3}-{shift}",
                                bg=active_bg if active else self.colors["panel"],
                                fg=active_fg if active else self.colors["muted"])
            return
        for bit, label in zip(range(15, -1, -1), labels):
            digit = "—" if value is None else str((value >> bit) & 1)
            label.configure(text=f"{bit}\n{digit}",
                            bg=active_bg if bit in selected else self.colors["panel"],
                            fg=active_fg if bit in selected else self.colors["muted"])

    def read_adc_value(self) -> None:
        if self._ensure_connected():
            self._request_adc_value_read(show_error=True)

    def toggle_adc_trigger_read(self) -> None:
        self._adc_trigger_read_active = not self._adc_trigger_read_active
        self._refresh_adc_read_buttons()

    def toggle_adc_realtime(self) -> None:
        super().toggle_adc_realtime()
        if self._adc_realtime_active:
            self._push_realtime_thresholds()

    def _parse_realtime_code(self, text: object) -> int | None:
        try:
            return awp1221_parse_threshold_text(str(text).strip())
        except (TypeError, ValueError):
            return None

    def _realtime_threshold_counts(self) -> tuple[int | None, int | None]:
        if self.mode_key == "button_mode":
            upper = lower = None
            for variables in getattr(self, "_button_stage_vars", ()):
                code = self._parse_realtime_code(variables["threshold"].get())
                if code is None:
                    continue
                counts = awp1221_threshold_adc_counts(
                    code,
                    bool(variables["use_high_12_bits"].get()),
                )
                mode = str(variables["event_mode"].get())
                if mode == "press" and upper is None:
                    upper = counts
                elif mode == "release" and lower is None:
                    lower = counts
            return upper, lower
        stages = getattr(self, "_stage_vars", ())
        if not stages:
            return None, None
        index = min(max(getattr(self, "_visible_stage_index", 0), 0), len(stages) - 1)
        variables = stages[index]
        precision = bool(variables["use_high_12_bits"].get())
        press = self._parse_realtime_code(variables["press_threshold"]["threshold"].get())
        release = self._parse_realtime_code(variables["release_threshold"]["threshold"].get())
        return (
            awp1221_threshold_adc_counts(press, precision) if press is not None else None,
            awp1221_threshold_adc_counts(release, precision) if release is not None else None,
        )

    def _realtime_press_repeat(self) -> int:
        if self.mode_key == "button_mode":
            for variables in getattr(self, "_button_stage_vars", ()):
                if str(variables["event_mode"].get()) != "press":
                    continue
                value = self._parse_realtime_code(variables["repeat"].get())
                return value if value is not None else 0
            return 0
        stages = getattr(self, "_stage_vars", ())
        if not stages:
            return 0
        index = min(max(getattr(self, "_visible_stage_index", 0), 0), len(stages) - 1)
        value = self._parse_realtime_code(stages[index]["wave"]["press_repeat"].get())
        return value if value is not None else 0

    def _realtime_waveform_period_s(self) -> float:
        if self._sinc_selected():
            try:
                return float(self._build_current_sinc_plan().duration_us) / 1_000_000.0
            except Exception:
                return 0.0
        try:
            depth = int(self.rom_depth_var.get().strip(), 0)
            point_time = int(self.rom_point_time_var.get().strip(), 0)
            half_cycle_code = self._parse_binary_option(self.rom_half_cycle_var.get())
            if depth <= 0 or point_time <= 0:
                return 0.0
            effective = awp1221_waveform_effective_depth_points(
                self.rom_waveform_var.get(),
                depth,
                half_cycle_code,
            )
            return effective * point_time / 1_000_000.0
        except Exception:
            return 0.0

    def _realtime_press_debounce_enabled(self) -> bool:
        if self.mode_key == "button_mode":
            for variables in getattr(self, "_button_stage_vars", ()):
                if str(variables["event_mode"].get()) != "press":
                    continue
                return bool(variables["debounce"].get())
            return False
        stages = getattr(self, "_stage_vars", ())
        if not stages:
            return False
        index = min(max(getattr(self, "_visible_stage_index", 0), 0), len(stages) - 1)
        return bool(stages[index]["press_threshold"]["debounce_enabled"].get())

    def _realtime_vibration_duration_s(self) -> float:
        try:
            debounce_ms = float(str(self.debounce_time_var.get()).strip())
        except (TypeError, ValueError):
            debounce_ms = 0.0
        zero_label = ""
        zero_var = getattr(self, "zero_time_var", None)
        if zero_var is not None:
            zero_label = str(zero_var.get())
        return awp1221_vibration_band_seconds(
            self._realtime_waveform_period_s(),
            self._realtime_press_repeat(),
            debounce_ms,
            debounce_enabled=self._realtime_press_debounce_enabled(),
            zero_time_s=awp1221_zero_time_seconds(zero_label),
        )

    def _push_realtime_thresholds(self) -> None:
        window = getattr(self, "_adc_voltage_window", None)
        if window is None or not window.winfo_exists():
            return
        upper, lower = self._realtime_threshold_counts()
        window.set_threshold_counts(upper, lower)

    def _bind_realtime_overlay_traces(self) -> None:
        variables: list[tk.Variable] = [self.debounce_time_var]
        for stage in getattr(self, "_button_stage_vars", ()):
            variables.extend(
                (
                    stage["threshold"],
                    stage["event_mode"],
                    stage["use_high_12_bits"],
                    stage["repeat"],
                )
            )
        for stage in getattr(self, "_stage_vars", ()):
            variables.extend(
                (
                    stage["press_threshold"]["threshold"],
                    stage["release_threshold"]["threshold"],
                    stage["use_high_12_bits"],
                    stage["wave"]["press_repeat"],
                )
            )
        for variable in (
            self.rom_waveform_var,
            self.rom_depth_var,
            self.rom_point_time_var,
            self.rom_half_cycle_var,
            *self._sinc_time_variables(),
            getattr(self, "sinc_depth_var", None),
        ):
            if variable is not None:
                variables.append(variable)
        for variable in variables:
            variable.trace_add("write", lambda *_: self._push_realtime_thresholds())

    def _request_adc_value_read(
        self,
        *,
        show_error: bool,
        device_addr: int | None = None,
        on_complete: Callable[[], None] | None = None,
        register: int = AWP1221_ADC_VALUE_REGISTER,
        quiet: bool = False,
    ) -> bool:
        if self._adc_read_in_flight:
            return False
        try:
            address = self.device_addr() if device_addr is None else device_addr
        except Exception as exc:
            if show_error:
                messagebox.showerror(self.tr("error"), str(exc))
            return False

        generation = self._adc_lifecycle_generation
        self._adc_read_in_flight = True

        def read_value() -> int:
            if quiet:
                with self.ctx.serial.suppress_logging():
                    return self._read_register_value(register, address)
            return self._read_register_value(register, address)

        _run_ui_background_task(
            self,
            read_value,
            on_done=lambda value: self._finish_adc_value_read(
                value=value,
                error=None,
                show_error=show_error,
                generation=generation,
                on_complete=on_complete,
                register=register,
            ),
            on_error=lambda error: self._finish_adc_value_read(
                value=None,
                error=error,
                show_error=show_error,
                generation=generation,
                on_complete=on_complete,
                register=register,
            ),
        )
        return True

    def _finish_adc_value_read(
        self,
        *,
        value: int | None,
        error: Exception | None,
        show_error: bool,
        generation: int,
        on_complete: Callable[[], None] | None,
        register: int = AWP1221_ADC_VALUE_REGISTER,
        update_display: bool = True,
    ) -> None:
        self._adc_read_in_flight = False
        if generation != getattr(self, "_adc_lifecycle_generation", 0):
            return
        if error is not None:
            if show_error:
                messagebox.showerror(self.tr("error"), str(error))
            elif register == AWP1221_ADC_LIVE_REGISTER:
                self._show_adc_voltage_error(error)
        elif value is not None:
            raw_value = value & 0xFFFF
            if update_display:
                self._adc_raw_value = raw_value
                self._refresh_adc_bits()
                self.adc_value_var.set(
                    f"0x{raw_value:04X} / {decode_awp1221_adc_value(raw_value)}"
                )
            if register == AWP1221_ADC_LIVE_REGISTER and self._adc_realtime_active:
                window = self._adc_voltage_window
                if window is not None and window.winfo_exists():
                    window.add_sample(raw_value)
        if on_complete is not None:
            on_complete()

    def _refresh_adc_read_buttons(self) -> None:
        value_active = self._adc_value_realtime_active if self.mode_key == "button_mode" else self._adc_realtime_active
        realtime_button = self._buttons.get("adc_read_realtime")
        if realtime_button is not None:
            realtime_button.configure(
                text=self.tr("adc_stop_realtime") if value_active else self.tr("adc_read_realtime"),
                style="Selected.TButton" if value_active else "Secondary.TButton",
            )
        graph_button = self._buttons.get("awp1221_adc_graph")
        if graph_button is not None:
            graph_button.configure(style="Selected.TButton" if self._adc_voltage_window is not None else "Secondary.TButton")
        trigger_button = self._buttons.get("adc_read_on_trigger")
        if trigger_button is not None:
            trigger_button.configure(
                text=self.tr("adc_stop_trigger_read")
                if self._adc_trigger_read_active
                else self.tr("adc_read_on_trigger"),
                style="Selected.TButton" if self._adc_trigger_read_active else "Secondary.TButton",
            )

    def _handle_adc_trigger_log(self, line: str) -> None:
        try:
            self.after(0, self._process_adc_trigger_log, line)
        except Exception:
            pass

    def _process_adc_trigger_log(self, line: str) -> None:
        shared_state = self._shared_state_for()
        detection_active = (
            bool(shared_state.detection_active)
            if shared_state is not None
            else bool(getattr(self, "_adc_detection_active", False))
        )
        detection_device_addr = (
            shared_state.detection_device_addr
            if shared_state is not None
            else getattr(self, "_adc_detection_device_addr", None)
        )
        upper = line.upper()
        marker = "MCU_TEXT"
        marker_index = upper.find(marker)
        if marker_index < 0:
            return
        payload = upper[marker_index + len(marker):].strip()
        if not self._adc_trigger_read_active or not detection_active:
            return
        if payload in {"P", "R"}:
            self._request_adc_value_read(
                show_error=False,
                device_addr=detection_device_addr,
            )

    def on_hide(self) -> None:
        self._adc_lifecycle_generation += 1
        self._stop_adc_realtime()
        self._adc_trigger_read_active = False
        self._adc_detection_active = False
        self._adc_detection_device_addr = None
        self._pressure_sensing = False
        self._pressure_cache_key = None
        self._set_pressure_status("idle")
        self._refresh_adc_read_buttons()

    def _cancel_idle_job(self, name: str) -> None:
        job = getattr(self, name, None)
        if not job:
            return
        try:
            self.after_cancel(job)
        except tk.TclError:
            pass
        setattr(self, name, None)

    def destroy(self) -> None:
        for name in (
            "_button_adc_reflow_job",
            "_comm_log_layout_job",
            "_comm_log_scroll_job",
            "_button_adc_diagram_job",
            "_rom_preview_job",
        ):
            self._cancel_idle_job(name)
        if not getattr(self, '_preparing', False):
            self.on_hide()
        try:
            self.ctx.serial.remove_log_listener(self._adc_trigger_log_listener)
        except Exception:
            pass
        try:
            self.auto_serial_var.trace_remove("write", self._auto_serial_trace)
        except Exception:
            pass
        super().destroy()

    def _build_control_buttons(self, parent: ttk.Frame, *, row: int) -> None:
        for _ in self._build_control_buttons_steps(parent, row=row):
            pass

    def _build_control_buttons_steps(self, parent: ttk.Frame, *, row: int) -> Iterator[None]:
        controls = ttk.Frame(parent, style="Panel.TFrame")
        controls.grid(row=row, column=0, sticky="ew", pady=(14, 0))
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)
        actions = (
            ("start_detection", self.start_detection, "Primary.TButton"),
            ("stop_detection", self.stop_detection, "Secondary.TButton"),
            ("start_play", self.start_play, "Primary.TButton"),
            ("stop_play", self.stop_play, "Secondary.TButton"),
        )
        for index, (key, command, style) in enumerate(actions):
            button = ttk.Button(controls, style=style, command=command)
            button.grid(
                row=index // 2,
                column=index % 2,
                sticky="ew",
                padx=(0, 4) if index % 2 == 0 else (4, 0),
                pady=(0, 6) if index < 2 else (6, 0),
            )
            self._buttons[key] = button
        yield
        stop_all = ttk.Button(controls, style="Danger.TButton", command=self.stop_all)
        stop_all.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._buttons["awp1221_stop_all"] = stop_all

    def _ensure_connected(self) -> bool:
        if self.ctx.serial.is_connected:
            return True
        owner = getattr(self, "_shared_state_owner", None)
        if owner is not None:
            owner.invalidate_all(clear_active=True)
        messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
        return False

    @staticmethod
    def _numeric_var(variable: tk.Variable, maximum: int, name: str) -> int:
        return _require_unsigned(int(str(variable.get()).strip(), 0), maximum, name)

    @staticmethod
    def _stage_target_var(variable: tk.Variable, name: str) -> int:
        text = str(variable.get()).strip()
        if text in {"Stage0", "0"}:
            return 0
        if text in {"Stage1", "1"}:
            return 1
        raise ValueError(f"{name} must be Stage0 or Stage1")

    def _stage_values(self, stage_index: int) -> tuple[int, int, int, int, int]:
        variables = self._stage_vars[stage_index]
        release_code = awp1221_adc_time_code(variables["release"].get())
        press_code = awp1221_adc_time_code(variables["press"].get())
        flags = awp1221_event_mode_flags(
            variables["event_mode"].get(),
            {name: variable.get() for name, variable in variables["flags"].items()},
        )
        press_threshold = variables["press_threshold"]
        release_threshold = variables["release_threshold"]
        mode = variables["event_mode"].get()
        shared_precision = bool(variables["use_high_12_bits"].get())
        press_precision = shared_precision if mode == "press" else False
        release_precision = shared_precision if mode == "release" else False
        slope = variables["slope"]
        wave = variables["wave"]
        return (
            encode_awp1221_adc_control(release_code, press_code, flags),
            encode_awp1221_adc_threshold(
                debounce_enabled=press_threshold["debounce_enabled"].get(),
                next_config=self._stage_target_var(press_threshold["next_config"], "Press jump target"),
                below_threshold=press_threshold["below_threshold"].get(),
                threshold=awp1221_parse_threshold_text(str(press_threshold["threshold"].get())),
                use_high_12_bits=press_precision,
            ),
            encode_awp1221_adc_threshold(
                debounce_enabled=release_threshold["debounce_enabled"].get(),
                next_config=self._stage_target_var(release_threshold["next_config"], "Release jump target"),
                below_threshold=release_threshold["below_threshold"].get(),
                threshold=awp1221_parse_threshold_text(str(release_threshold["threshold"].get())),
                use_high_12_bits=release_precision,
            ),
            encode_awp1221_adc_slope(
                release_below=slope["release_below"].get(),
                release_slope=self._numeric_var(slope["release_slope"], 0x7F, "SLOPE_R"),
                press_below=slope["press_below"].get(),
                press_slope=self._numeric_var(slope["press_slope"], 0x7F, "SLOPE_P"),
            ),
            encode_awp1221_adc_wave(
                release_repeat=self._numeric_var(wave["release_repeat"], 0x7, "Repeat timesR"),
                release_wave=self._parse_trigger_waveform(wave["release_wave"].get()),
                press_repeat=self._numeric_var(wave["press_repeat"], 0x7, "Repeat timesP"),
                press_wave=self._parse_trigger_waveform(wave["press_wave"].get()),
            ),
        )

    def _adc_settings_config(self) -> dict[str, Any]:
        time_ms = int(self.debounce_time_var.get().strip(), 0)
        return {
            "debounce_code": encode_awp1221_debounce_time(time_ms),
            "sample_update": self.adc_sample_update_var.get(),
            "zero_time": self.zero_time_var.get(),
            "press_return_bit_time": self.press_return_bit_time_var.get(),
            "release_return_bit_time": self.release_return_bit_time_var.get(),
            "zps_enabled": bool(self.zps_enabled_var.get()),
            "zps_detect": bool(self.zps_detect_var.get()),
            "sleep_sensitivity": bool(self.sleep_sensitivity_var.get()),
            "adc_ldo": self.adc_ldo_var.get(),
            "auto_serial": self._auto_serial_enabled(),
        }

    def _write_adc_settings(
        self,
        config: dict[str, Any] | None = None,
        device_addr: int | None = None,
    ) -> None:
        settings = config if config is not None else self._adc_settings_config()
        address = self.device_addr() if device_addr is None else device_addr
        current_values: dict[int, int] = {}
        for register in (
            AWP1221_ADC_SETTINGS_REGISTER,
            AWP1221_RETURN_BIT_REGISTER,
            AWP1221_DEBOUNCE_REGISTER,
            AWP1221_ADC_LOAD_REGISTER,
        ):
            resp = self.ctx.serial.read_register_value(address, register, bits=16)
            if not resp.ok:
                raise ValueError(resp.error)
            current = self.ctx.serial.extract_rdataw_value(
                resp.data,
                device_addr=address,
                reg_addr=register,
                bits=16,
            )
            if current is None:
                raise ValueError(resp.data or f"invalid register 0x{register:02X} response")
            current_values[register] = current

        targets = awp1221_settings_target_words(current_values, settings)
        for register, value in targets.items():
            resp = self.ctx.serial.write_register_value(address, register, value, bits=16)
            if not resp.ok:
                raise ValueError(resp.error)

    def apply_adc_settings(self) -> None:
        if not self._ensure_connected():
            return
        try:
            self._write_adc_settings()
            self._sync_realtime_adc_load()
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def _sync_realtime_adc_load(self, load: str | None = None) -> None:
        selected = str(load if load is not None else self.adc_ldo_var.get())
        window = getattr(self, "_adc_voltage_window", None)
        if window is None:
            return
        try:
            if not window.winfo_exists():
                return
        except (AttributeError, tk.TclError):
            return
        window.load.set(selected)
        redraw = getattr(window, "redraw", None)
        if callable(redraw):
            redraw()

    def _write_control_command(
        self,
        command_key: str,
        device_addr: int | None = None,
    ) -> None:
        address = self.device_addr() if device_addr is None else device_addr
        response = self.ctx.serial.write_register_value(
            address,
            AWP1221_CONTROL_REGISTER,
            AWP1221_CONTROL_COMMANDS[command_key],
            bits=16,
        )
        if not response.ok:
            raise ValueError(response.error)

    def _write_start_detection(
        self,
        device_addr: int,
        stage_configs: tuple[Any, Any],
        settings_config: dict[str, Any],
    ) -> None:
        with _serial_batch(self.ctx.serial):
            self._ensure_reg_1f_bit3(device_addr)
            if self.mode_key == "button_mode":
                for stage_index, config in enumerate(stage_configs):
                    self._write_button_adc_stage(stage_index, config, device_addr)
            else:
                for stage_index, values in enumerate(stage_configs):
                    self._write_adc_stage(stage_index, values, device_addr)
            self._write_adc_settings(settings_config, device_addr)
            self._write_control_command("start_detection", device_addr)

    def _set_start_detection_busy(self, busy: bool) -> None:
        button = getattr(self, "_buttons", {}).get("start_detection")
        if button is not None:
            button.configure(state="disabled" if busy else "normal")

    def _finish_start_detection(
        self,
        error: str | None,
        *,
        device_addr: int,
        generation: int,
    ) -> None:
        self._set_start_detection_busy(False)
        if generation != getattr(self, "_adc_lifecycle_generation", 0):
            return
        self._set_detection_state(error is None, device_addr)
        if error is None:
            self._sync_realtime_adc_load()
        if error:
            messagebox.showerror(self.tr("error"), error)

    @contextmanager
    def _suspend_adc_live_writes(self):
        self._adc_live_write_depth = getattr(self, "_adc_live_write_depth", 0) + 1
        try:
            yield
        finally:
            self._adc_live_write_depth -= 1

    def _live_write_allowed(self) -> bool:
        if getattr(self, "_adc_live_write_depth", 0):
            return False
        serial = getattr(self.ctx, "serial", None)
        return bool(serial is not None and getattr(serial, "is_connected", False))

    def _show_live_write_error(self, exc: Exception) -> None:
        messagebox.showerror(self.tr("error"), str(exc))

    def _commit_live_settings(self) -> None:
        if not self._live_write_allowed():
            return
        try:
            self._write_adc_settings()
            self._sync_realtime_adc_load()
        except Exception as exc:
            self._show_live_write_error(exc)

    def _commit_live_stage(self, stage_index: int) -> None:
        if not self._live_write_allowed():
            return
        try:
            if self.mode_key == "button_mode":
                self._write_button_adc_stage(stage_index)
            else:
                self._write_adc_stage(stage_index)
        except Exception as exc:
            self._show_live_write_error(exc)

    def _bind_live_choice(self, widget: tk.Widget, callback: Callable[[], None]) -> None:
        widget.bind("<<ComboboxSelected>>", lambda _event: callback(), add="+")

    def _bind_live_text(self, widget: tk.Widget, callback: Callable[[], None]) -> None:
        widget.bind("<FocusOut>", lambda _event: callback(), add="+")
        widget.bind("<Return>", lambda _event: callback(), add="+")

    def _capture_owned_snapshot(self) -> dict[str, Any]:
        device_addr = self.device_addr()
        if self.mode_key == "button_mode":
            stages = tuple(self._button_stage_config(index) for index in range(2))
            event_modes = None
        else:
            stages = tuple(self._stage_values(index) for index in range(2))
            event_modes = tuple(self._stage_vars[index]["event_mode"].get() for index in range(2))
        return {
            "device_addr": device_addr,
            "mode": self.mode_key,
            "settings": self._adc_settings_config(),
            "stages": stages,
            "event_modes": event_modes,
        }

    def _owned_compare_groups(self, mode: str) -> dict[str, tuple[int, ...]]:
        groups = {"settings": AWP1221_SETTINGS_REGISTERS}
        for index in range(2):
            groups[f"stage{index}"] = awp1221_stage_compare_registers(mode, index)
        return groups

    def _expected_owned_words(self, current: dict[int, int], snapshot: dict[str, Any]) -> dict[int, int]:
        expected = awp1221_settings_target_words(current, snapshot["settings"])
        if snapshot["mode"] == "button_mode":
            for index, config in enumerate(snapshot["stages"]):
                expected.update(self._button_stage_values_from_config(index, current, config))
            return expected
        for index, values in enumerate(snapshot["stages"]):
            expected.update(
                awp1221_full_stage_words(
                    self.STAGE_REGISTER_RANGES[index],
                    values,
                    current,
                    snapshot["event_modes"][index],
                )
            )
        return expected

    def _read_owned_mismatches(self, device_addr: int, snapshot: dict[str, Any]) -> tuple[str, ...]:
        self._ensure_reg_1f_bit3(device_addr)
        registers = []
        for group in self._owned_compare_groups(snapshot["mode"]).values():
            registers.extend(group)
        current = {
            register: self._read_register_value(register, device_addr)
            for register in dict.fromkeys(registers)
        }
        expected = self._expected_owned_words(current, snapshot)
        return awp1221_mismatch_groups(
            current,
            expected,
            self._owned_compare_groups(snapshot["mode"]),
        )

    def _write_mismatched_groups(self, snapshot: dict[str, Any], mismatches: tuple[str, ...]) -> None:
        address = snapshot["device_addr"]
        if "settings" in mismatches:
            self._write_adc_settings(snapshot["settings"], address)
        for index, config in enumerate(snapshot["stages"]):
            if f"stage{index}" not in mismatches:
                continue
            if snapshot["mode"] == "button_mode":
                self._write_button_adc_stage(index, config, address)
            else:
                self._write_adc_stage(
                    index,
                    config,
                    address,
                    event_mode=snapshot["event_modes"][index],
                )

    def _apply_verified_start(
        self,
        command_key: str,
        snapshot: dict[str, Any],
        mismatches: tuple[str, ...],
        choice: str | None,
    ) -> None:
        plan = awp1221_verified_start_plan(mismatches, choice)
        if plan == "abort":
            return
        with _serial_batch(self.ctx.serial):
            if plan == "rewrite-then-command":
                self._write_mismatched_groups(snapshot, mismatches)
            self._write_control_command(command_key, snapshot["device_addr"])

    def _mismatch_group_label(self, group: str) -> str:
        if group == "settings":
            return self.tr("awp1221_mismatch_settings")
        return self.tr("awp1221_mismatch_stage").format(index=group.removeprefix("stage"))

    def _ask_register_mismatch(self, groups: tuple[str, ...]) -> str:
        chooser = getattr(self, "_mismatch_chooser", None)
        if callable(chooser):
            return str(chooser(groups))
        choice = {"value": "cancel"}
        window = tk.Toplevel(self)
        window.title(self.tr("awp1221_register_mismatch_title"))
        window.transient(self.winfo_toplevel())
        window.resizable(False, False)
        names = "\n".join(self._mismatch_group_label(group) for group in groups)
        ttk.Label(
            window,
            text=self.tr("awp1221_register_mismatch_body").format(groups=names),
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        buttons = ttk.Frame(window)
        buttons.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))

        def pick(value: str) -> None:
            choice["value"] = value
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", lambda: pick("cancel"))
        for column, (key, value) in enumerate(
            (
                ("awp1221_mismatch_rewrite_start", "rewrite"),
                ("awp1221_mismatch_start_only", "start"),
                ("awp1221_mismatch_cancel", "cancel"),
            )
        ):
            ttk.Button(buttons, text=self.tr(key), command=lambda picked=value: pick(picked)).grid(
                row=0, column=column, padx=(0 if column == 0 else 8, 0)
            )
        window.grab_set()
        window.wait_window()
        return choice["value"]

    def _begin_verified_command(self, command_key: str, snapshot: dict[str, Any]) -> None:
        if getattr(self, "_verified_start_busy", False):
            return
        self._verified_start_busy = True
        device_addr = snapshot["device_addr"]
        generation = getattr(self, "_adc_lifecycle_generation", 0)
        if command_key == "start_detection":
            self._set_start_detection_busy(True)
        _run_ui_background_task(
            self,
            lambda: self._read_owned_mismatches(device_addr, snapshot),
            on_done=lambda mismatches: self._prompt_verified_command(
                command_key,
                snapshot,
                tuple(mismatches),
                generation,
            ),
            on_error=lambda error: self._finish_verified_command(
                command_key,
                str(error),
                started=False,
                device_addr=device_addr,
                generation=generation,
            ),
        )

    def _prompt_verified_command(
        self,
        command_key: str,
        snapshot: dict[str, Any],
        mismatches: tuple[str, ...],
        generation: int,
    ) -> None:
        if generation != getattr(self, "_adc_lifecycle_generation", 0):
            self._finish_verified_command(
                command_key,
                None,
                started=False,
                device_addr=snapshot["device_addr"],
                generation=generation,
            )
            return
        choice = None if not mismatches else self._ask_register_mismatch(mismatches)
        plan = awp1221_verified_start_plan(mismatches, choice)
        if plan == "abort":
            self._finish_verified_command(
                command_key,
                None,
                started=False,
                device_addr=snapshot["device_addr"],
                generation=generation,
            )
            return
        device_addr = snapshot["device_addr"]
        _run_ui_background_task(
            self,
            lambda: self._apply_verified_start(command_key, snapshot, mismatches, choice),
            on_done=lambda _result: self._finish_verified_command(
                command_key,
                None,
                started=True,
                device_addr=device_addr,
                generation=generation,
            ),
            on_error=lambda error: self._finish_verified_command(
                command_key,
                str(error),
                started=False,
                device_addr=device_addr,
                generation=generation,
            ),
        )

    def _finish_verified_command(
        self,
        command_key: str,
        error: str | None,
        *,
        started: bool,
        device_addr: int,
        generation: int,
    ) -> None:
        self._verified_start_busy = False
        self._set_start_detection_busy(False)
        if generation != getattr(self, "_adc_lifecycle_generation", 0):
            return
        if error:
            self._set_pressure_status("error")
            messagebox.showerror(self.tr("error"), error)
            return
        if not started:
            return
        if command_key == "start_detection":
            self._set_detection_state(True, device_addr)
            self._sync_realtime_adc_load()
        elif command_key == "start_play":
            self._set_waveform_play_state(True, device_addr)

    def start_detection(self) -> None:
        if not self._ensure_connected():
            return
        try:
            snapshot = self._capture_owned_snapshot()
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        self._begin_verified_command("start_detection", snapshot)

    def _send_control_command(self, command_key: str) -> bool:
        if not self._ensure_connected():
            return False
        try:
            self._ensure_reg_1f_bit3()
            self._write_control_command(command_key)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return False
        return True

    def stop_detection(self) -> None:
        if self._send_control_command("stop_detection"):
            self._set_detection_state(False)

    def start_play(self) -> None:
        if self._sinc_selected() and not self._sinc_play_allowed():
            messagebox.showinfo(self.tr("info"), self._sinc_reason_message("not_verified"))
            return
        if not self._ensure_connected():
            return
        try:
            snapshot = self._capture_owned_snapshot()
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        self._begin_verified_command("start_play", snapshot)

    def stop_play(self) -> None:
        if self._send_control_command("stop_play"):
            self._set_waveform_play_state(False)

    def stop_all(self) -> None:
        if self._send_control_command("awp1221_stop_all"):
            self._set_detection_state(False)
            self._set_waveform_play_state(False)

    def read_adc_settings(self) -> None:
        if not self._ensure_connected():
            return
        try:
            values: dict[int, int] = {}
            for register in (
                AWP1221_ADC_SETTINGS_REGISTER,
                AWP1221_RETURN_BIT_REGISTER,
                AWP1221_DEBOUNCE_REGISTER,
                AWP1221_ADC_LOAD_REGISTER,
            ):
                address = self.device_addr()
                resp = self.ctx.serial.read_register_value(address, register, bits=16)
                if not resp.ok:
                    raise ValueError(resp.error)
                value = self.ctx.serial.extract_rdataw_value(
                    resp.data,
                    device_addr=address,
                    reg_addr=register,
                    bits=16,
                )
                if value is None:
                    raise ValueError(resp.data or f"invalid register 0x{register:02X} response")
                values[register] = value

            settings = decode_awp1221_adc_settings_registers(
                values[AWP1221_ADC_SETTINGS_REGISTER],
                values[AWP1221_RETURN_BIT_REGISTER],
            )
            with self._suspend_adc_live_writes():
                if hasattr(self, "auto_serial_var"):
                    self.auto_serial_var.set(
                        bool(values[AWP1221_ADC_SETTINGS_REGISTER] & AWP1221_AUTO_SERIAL_MASK)
                    )
                    self._sync_auto_serial_page_state()
                self.adc_sample_update_var.set(settings["sample_update"])
                self.zero_time_var.set(settings["zero_time"])
                self.press_return_bit_time_var.set(settings["press_return_bit_time"])
                self.release_return_bit_time_var.set(settings["release_return_bit_time"])
                self.zps_enabled_var.set(settings["zps_enabled"])
                self.zps_detect_var.set(settings["zps_detect"])
                self.sleep_sensitivity_var.set(settings["sleep_sensitivity"])
                self.adc_ldo_var.set(awp1221_adc_load_label(values[AWP1221_ADC_LOAD_REGISTER]))
                self.debounce_time_var.set(
                    str(decode_awp1221_debounce_time(values[AWP1221_DEBOUNCE_REGISTER]))
                )
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def _write_adc_stage(
        self,
        stage_index: int,
        stage_values: tuple[int, int, int, int, int] | None = None,
        device_addr: int | None = None,
        event_mode: str | None = None,
    ) -> None:
        address = self.device_addr() if device_addr is None else device_addr
        values = tuple(
            stage_values if stage_values is not None else self._stage_values(stage_index)
        )
        registers = self.STAGE_REGISTER_RANGES[stage_index]
        if event_mode is None:
            all_stage_variables = getattr(self, "_stage_vars", ())
            stage_variables = (
                all_stage_variables[stage_index]
                if stage_index < len(all_stage_variables)
                else {}
            )
            live_mode = stage_variables.get("event_mode")
            event_mode = live_mode.get() if live_mode is not None else ""
        inactive = 2 if event_mode == "press" else 1 if event_mode == "release" else None
        current = {}
        if inactive is not None:
            current[registers[inactive]] = self._read_adc_value(registers[inactive], address)
        words = awp1221_full_stage_words(registers, values, current, event_mode)
        resp = self.ctx.serial.write_register_values(
            address,
            self.STAGE_REGISTERS[stage_index],
            [words[register] for register in registers],
            bits=16,
        )
        if not resp.ok:
            raise ValueError(resp.error)

    def apply_adc_stage(self, stage_index: int) -> None:
        if not self._ensure_connected():
            return
        try:
            self._write_adc_stage(stage_index)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def read_adc_stage(self, stage_index: int) -> None:
        if not self._ensure_connected():
            return
        registers = self.STAGE_REGISTER_RANGES[stage_index]
        try:
            values: list[int] = []
            for reg in registers:
                address = self.device_addr()
                resp = self.ctx.serial.read_register_value(address, reg, bits=16)
                if not resp.ok:
                    raise ValueError(resp.error)
                value = self.ctx.serial.extract_rdataw_value(
                    resp.data,
                    device_addr=address,
                    reg_addr=reg,
                    bits=16,
                )
                if value is None:
                    raise ValueError(resp.data or f"invalid ADC register 0x{reg:02X} response")
                values.append(value)
            with self._suspend_adc_live_writes():
                self._set_stage_values(stage_index, tuple(values))
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def _set_stage_values(self, stage_index: int, values: tuple[int, int, int, int, int]) -> None:
        control = decode_awp1221_adc_control(values[0])
        press_threshold = decode_awp1221_adc_threshold(values[1])
        release_threshold = decode_awp1221_adc_threshold(values[2])
        slope = decode_awp1221_adc_slope(values[3])
        wave = decode_awp1221_adc_wave(values[4])
        variables = self._stage_vars[stage_index]
        variables["release"].set(awp1221_adc_time_label(control["release_time"]))
        variables["press"].set(awp1221_adc_time_label(control["press_time"]))
        for name, enabled in control["flags"].items():
            variables["flags"][name].set(enabled)
        press_selected = control["flags"]["t_p"] and control["flags"]["trigger_p"]
        release_selected = control["flags"]["t_r"] and control["flags"]["trigger_r"]
        variables["event_mode"].set("release" if release_selected and not press_selected else "press")
        selected_threshold = (
            release_threshold
            if variables["event_mode"].get() == "release"
            else press_threshold
        )
        shared_precision = variables.get("use_high_12_bits")
        if shared_precision is not None:
            shared_precision.set(bool(selected_threshold["use_high_12_bits"]))
        for key, value in press_threshold.items():
            if key == "next_config":
                variables["press_threshold"][key].set(f"Stage{value}")
            else:
                target = variables["press_threshold"].get(key)
                if target is not None:
                    target.set(
                        awp1221_format_threshold_signed(value) if key == "threshold" else value
                    )
        for key, value in release_threshold.items():
            if key == "next_config":
                variables["release_threshold"][key].set(f"Stage{value}")
            else:
                target = variables["release_threshold"].get(key)
                if target is not None:
                    target.set(
                        awp1221_format_threshold_signed(value) if key == "threshold" else value
                    )
        for key, value in slope.items():
            variables["slope"][key].set(str(value) if key in {"release_slope", "press_slope"} else value)
        variables["wave"]["release_repeat"].set(str(wave["release_repeat"]))
        variables["wave"]["release_wave"].set(
            self._format_trigger_waveform(wave["release_wave"])
        )
        variables["wave"]["press_repeat"].set(str(wave["press_repeat"]))
        variables["wave"]["press_wave"].set(
            self._format_trigger_waveform(wave["press_wave"])
        )
        self._sync_full_event_mode(stage_index)

    def refresh_language(self) -> None:
        super().refresh_language()
        self._refresh_waveform_peak_to_peak()
        self._refresh_waveform_controls()
        self.mode_title_label.configure(text=self.tr(self.mode_key))
        if self.mode_key == "button_mode":
            self._refresh_pressure_status()
            for widget, key in self._translated_widgets:
                widget.configure(text=self.tr(key))
            board_keys = {"set_active": "awp1221_type4_active", "apply_slot": "awp1221_type4_apply",
                          "read_slot": "awp1221_type4_read"}
            for key, button in self._buttons.items():
                button.configure(text=self.tr(board_keys.get(key, key)))
            self._refresh_adc_read_buttons()
            self._refresh_sinc_slice_time_labels()
            self._schedule_button_rom_preview()
            self._schedule_button_adc_diagram()
            fields = getattr(self, "_button_adc_fields", ())
            if fields:
                self._queue_button_adc_reflow(fields[0][0].master)
            sram_editor = self._sram_editor
            if sram_editor is not None:
                sram_editor.refresh_language()
            self._sync_sram_editor_visibility()
            return
        self._labels["awp1221_basic_parameters"].configure(text=self.tr("awp1221_basic_parameters"))
        self._labels["awp1221_adc"].configure(text=self.tr("awp1221_adc"))
        sram_editor = self._sram_editor
        if sram_editor is not None:
            sram_editor.refresh_language()
        self.tools_notebook.tab(0, text=self.tr("awp1221_waveform_tab"))
        self.tools_notebook.tab(1, text=self.tr("awp1221_rom_tab"))
        for widget, key in self._translated_widgets:
            widget.configure(text=self.tr(key))
        for key, button in self._buttons.items():
            button.configure(text=self.tr(key))
        self._refresh_adc_read_buttons()
        self._refresh_sinc_slice_time_labels()


    def refresh_theme(self) -> None:
        super().refresh_theme()
        self._refresh_pressure_status()
        self._refresh_adc_bits()
        self._button_adc_diagram_drawn = None
        self._schedule_button_adc_diagram()
        self._draw_button_en_switch()
        colors = self.ctx.colors
        canvas = getattr(self, "rom_canvas", None)
        if canvas is not None:
            try:
                canvas.configure(
                    bg=colors.get("canvas_bg", colors["bg"]),
                    highlightbackground=colors.get("border", "#CBD5E1"),
                )
            except tk.TclError:
                pass
            self._rom_preview_drawn = None
            self._schedule_button_rom_preview()
        editor = self._sram_editor
        if editor is not None and hasattr(editor, "refresh_theme"):
            editor.refresh_theme()
        window = getattr(self, "_adc_voltage_window", None)
        if window is not None:
            try:
                if int(window.winfo_exists()):
                    window.refresh_theme(colors)
            except tk.TclError:
                pass


class AWP1221Page(BasePage):
    page_name = "awp1221"
    DEVICE_ADDRS = AWP1221_DEVICE_ADDRS
    ID_REG = AWP1221_ID_REG
    CHIP_ID = AWP1221_CHIP_ID

    def __init__(self, parent: tk.Misc, ctx: Any, *, defer_build: bool = False) -> None:
        super().__init__(parent, ctx)
        self.addr_var = tk.StringVar(value="0x50")
        self.reg_addr_var = tk.StringVar(value="0x00")
        self.write_value_var = tk.StringVar(value="0x0000")
        self.read_value_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        self.auto_serial_var = tk.BooleanVar(value=True)
        self.en_enabled_var = tk.BooleanVar(value=False)
        self.en_status_var = tk.StringVar(value="")
        self._en_state: bool | None = None
        self._en_identity: tuple[Any, ...] | None = None
        self._shared_d4_panel_identity: tuple[Any, ...] | None = None
        self._init_gpio_state()
        self.current_mode = "button_mode"
        self.mode_buttons: dict[str, ttk.Button] = {}
        self.mode_pages = LazyPages(self._install_mode_page)
        self._shared_state_owner = AWP1221SharedStateOwner()
        self._shared_addr: int | None = None
        self._labels: dict[str, ttk.Label] = {}
        self._buttons: dict[str, ttk.Button] = {}
        self._preparing = defer_build
        self._build_steps = self._initialize_steps()
        if not defer_build:
            for _ in self._build_steps:
                pass

    def _initialize_steps(self) -> Iterator[None]:
        yield from self._build_ui_steps()
        self._preparing = False

    def _build_ui(self) -> None:
        for _ in self._build_ui_steps():
            pass

    def _build_ui_steps(self) -> Iterator[None]:
        self.enable_pinned_footer_layout()

        self.make_title(self, "awp1221").grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())

        # The footer stays visible on short windows, so the mode area must
        # own its scrolling.  Without this wrapper the full mode page (often
        # >900px tall) extended below the viewport and had no reachable
        # bottom controls at 640x480 or when the user enlarged the font.
        self.mode_scroll, mode_container = self.make_vertical_scroll_area(
            self,
            horizontal=True,
            propagate_height=True,
            clamp_width=True,
        )
        self.mode_scroll.grid(row=1, column=0, sticky="nsew")
        # Leave a small gutter so mode headers and panel edges are not flush
        # with the inner canvas clip boundary at narrow widths.
        mode_container.configure(padding=(8, 0, 8, 0))
        mode_container.columnconfigure(0, weight=1)
        yield
        mode_container.rowconfigure(0, weight=1)
        self.mode_container = mode_container

        self.footer_scroll, footer_content = self.make_vertical_scroll_area(
            self, horizontal=False, reserve_vertical_gutter=True
        )
        self.footer_scroll.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        footer_content.columnconfigure(0, weight=1)
        footer, actions, register_panel, identity = self.make_merged_footer(footer_content)
        yield
        footer.grid(row=0, column=0, sticky="ew")
        self._footer_panel = footer
        self._footer_fit_job = None
        footer.bind("<Configure>", self._queue_footer_fit)
        self.bind("<Configure>", self._queue_footer_fit, add="+")
        self._build_state_actions(actions)
        yield
        register_panel.columnconfigure(8, weight=1)
        self._build_register_bar(register_panel)
        self._build_identity_bar(identity)

        self.gpio_controls = NullGpioControlPanel()
        for mode in ("button_mode", "full_mode"):
            self.mode_pages.register(mode, lambda mode=mode, defer_build=False: AWP1221ModePage(
                mode_container,
                self.ctx,
                tr=self.tr,
                device_addr=self._device_addr,
                mode_key=mode,
                auto_serial_var=self.auto_serial_var,
                shared_state_owner=self._shared_state_owner,
                host=self,
                defer_build=defer_build,
            ), staged=True)

        self._on_addr_changed()
        yield
        yield from self.mode_pages.prepare("button_mode")
        self.show_mode("button_mode")
        self.refresh_language()

    def _install_mode_page(self, _name: str, page: AWP1221ModePage) -> None:
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        if not self.winfo_ismapped():
            return
        refresh = getattr(self.ctx.app, "_refresh_widget_fonts", None)
        if callable(refresh):
            refresh(page)

    def _queue_footer_fit(self, _event=None) -> None:
        if self._footer_fit_job is not None:
            return
        try:
            self._footer_fit_job = self.after(0, self._fit_footer_height)
        except tk.TclError:
            self._footer_fit_job = None

    def _fit_footer_height(self) -> None:
        self._footer_fit_job = None
        try:
            height = self.winfo_height()
            if height <= 1:
                return
            footer_req = self._footer_panel.winfo_reqheight()
            desired = min(footer_req + 16, max(96, int(height * .35)))
            key = (height, footer_req, desired)
            if key == getattr(self, "_footer_fit_key", None):
                return
            self._footer_fit_key = key
            canvas = self.footer_scroll.canvas
            if int(canvas.winfo_reqheight()) != desired:
                canvas.configure(height=desired)
        except tk.TclError:
            self._footer_fit_key = None

    def _build_state_actions(self, parent: ttk.Frame) -> None:
        button_styles = {
            "awp1221_reset": "Secondary.TButton",
            "awp1221_sleep": "Secondary.TButton",
            "awp1221_shutdown": "Danger.TButton",
            "awp1221_default_state": "Primary.TButton",
        }
        for column, (key, value) in enumerate(AWP1221_STATE_COMMANDS.items()):
            parent.columnconfigure(column, weight=1, uniform="chip_state_actions")
            button = ttk.Button(
                parent,
                style=button_styles[key],
                command=lambda command_value=value: self.write_chip_state(command_value),
            )
            button.grid(row=0, column=column, sticky="ew", padx=(0, 8))
            self._buttons[key] = button

    def _build_identity_bar(self, parent: ttk.Frame) -> None:
        serial_group = parent.add_group()
        self.make_serial_selector(serial_group, page_name=self.page_name, row=0, column=0)
        addr_group = parent.add_group()
        self._labels["current_chip_addr"] = ttk.Label(addr_group, style="Panel.TLabel")
        self._labels["current_chip_addr"].grid(row=0, column=0, padx=(0, 8))
        self.addr_combo = ttk.Combobox(
            addr_group,
            textvariable=self.addr_var,
            values=[f"0x{addr:02X}" for addr in self.DEVICE_ADDRS],
            state="readonly",
            width=8,
        )
        self.addr_combo.grid(row=0, column=1)
        self.addr_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_addr_changed())
        detect_group = parent.add_group()
        detect_btn = ttk.Button(detect_group, style="Primary.TButton", command=self.detect_chip)
        detect_btn.grid(row=0, column=0)
        self._buttons["detect_chip"] = detect_btn
        status_group = parent.add_group()
        ttk.Label(status_group, textvariable=self.status_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")

    def _build_register_bar(self, parent: ttk.Frame) -> None:
        title_group = parent.add_group()
        self._labels["read_write_registers"] = ttk.Label(title_group, style="Panel.TLabel")
        self._labels["read_write_registers"].grid(row=0, column=0, sticky="w")
        addr_group = parent.add_group()
        self._labels["reg_addr"] = ttk.Label(addr_group, style="Panel.TLabel")
        self._labels["reg_addr"].grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(addr_group, textvariable=self.reg_addr_var, width=8).grid(row=0, column=1, sticky="w")
        write_group = parent.add_group()
        self._labels["write_value"] = ttk.Label(write_group, style="Panel.TLabel")
        self._labels["write_value"].grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(write_group, textvariable=self.write_value_var, width=10).grid(row=0, column=1, sticky="w")
        read_group = parent.add_group()
        self._labels["read_value"] = ttk.Label(read_group, style="Panel.TLabel")
        self._labels["read_value"].grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(read_group, textvariable=self.read_value_var, width=12, state="readonly").grid(
            row=0, column=1, sticky="w"
        )
        button_group = parent.add_group()
        read_btn = ttk.Button(button_group, style="Secondary.TButton", command=self.read_register)
        read_btn.grid(row=0, column=0, sticky="w", padx=(0, 6))
        write_btn = ttk.Button(button_group, style="Primary.TButton", command=self.write_register)
        write_btn.grid(row=0, column=1, sticky="w")
        self._buttons["rr"] = read_btn
        self._buttons["rw"] = write_btn

    def _init_gpio_state(self) -> None:
        self.gpio_controller = Awp1221GpioController(self.ctx.serial, self._device_addr)
        self.gpio_category_vars = [
            tk.StringVar(value=GPIO_CATEGORY_LABELS["input"]),
            tk.StringVar(value=GPIO_CATEGORY_LABELS["input"]),
        ]
        self.gpio_function_vars = [tk.StringVar(value=""), tk.StringVar(value="")]
        self.gpio_od_vars = [tk.BooleanVar(value=True), tk.BooleanVar(value=True)]
        self.gpio_drive_mode_vars = [tk.StringVar(value="OpenDrian"), tk.StringVar(value="OpenDrian")]
        for index, variable in enumerate(self.gpio_od_vars):
            variable.trace_add("write", lambda *_, channel=index: self.gpio_drive_mode_vars[channel].set(
                "OpenDrian" if self.gpio_od_vars[channel].get() else "Push-Pull"
            ))
        self.gpio_chip_status_vars = [
            tk.StringVar(value=""),
            tk.StringVar(value=""),
        ]
        self.gpio_mcu_status_vars = [tk.StringVar(value=""), tk.StringVar(value="")]
        self.gpio_mcu_role_vars = [tk.StringVar(value=""), tk.StringVar(value="")]
        self.gpio_function_combos: list[list[ttk.Combobox]] = [[], []]
        self.gpio_action_widgets: list[list[tk.Widget]] = [[], []]

    def _gpio_function_labels(self, channel: int) -> tuple[str, ...]:
        category = _GPIO_CATEGORY_BY_LABEL.get(self.gpio_category_vars[channel].get())
        if category is None or category == "debug":
            return ()
        return tuple(GPIO_FUNCTION_LABELS[code] for code in GPIO_CATEGORY_FUNCTIONS[category])

    def _on_gpio_category_selected(self, channel: int) -> None:
        labels = self._gpio_function_labels(channel)
        for combo in self.gpio_function_combos[channel]:
            combo.configure(values=labels)
        current = self.gpio_function_vars[channel].get()
        if current not in labels:
            self.gpio_function_vars[channel].set("")
        self._refresh_gpio_mcu_label(channel)
        self._sync_gpio_actions(channel)

    def _refresh_gpio_mcu_label(self, channel: int) -> None:
        label = self.gpio_function_vars[channel].get()
        code = _GPIO_FUNCTION_BY_LABEL.get(label)
        if code is None:
            self.gpio_mcu_role_vars[channel].set("")
            return
        role = gpio_mcu_role(code)
        key = {
            "drive": "awp1221_gpio_mcu_drive",
            "detect": "awp1221_gpio_detect",
            "hiz": "awp1221_gpio_hiz",
        }[role]
        self.gpio_mcu_role_vars[channel].set(self.tr(key))

    def _sync_gpio_actions(self, channel: int) -> None:
        enabled = False
        controller = getattr(self, "gpio_controller", None)
        if controller is not None:
            enabled = controller.drive_allowed(channel)
        state = "normal" if enabled else "disabled"
        widgets = getattr(self, "gpio_action_widgets", None)
        if not widgets:
            return
        for widget in widgets[channel]:
            widget.configure(state=state)

    def _gpio_pending_from_vars(self) -> tuple[GpioChannelBits, GpioChannelBits] | None:
        channels: list[GpioChannelBits] = []
        for index in range(2):
            code = _GPIO_FUNCTION_BY_LABEL.get(self.gpio_function_vars[index].get())
            if code is None:
                return None
            channels.append(GpioChannelBits(code, bool(self.gpio_od_vars[index].get())))
        return channels[0], channels[1]

    def _apply_pending_to_vars(self, gpio0: GpioChannelBits, gpio1: GpioChannelBits) -> None:
        for index, bits in enumerate((gpio0, gpio1)):
            category = gpio_function_category(bits.function)
            self.gpio_category_vars[index].set(
                f"0x{bits.function:02X} (readback)" if category == "debug" else GPIO_CATEGORY_LABELS[category]
            )
            self.gpio_function_vars[index].set(GPIO_FUNCTION_LABELS[bits.function])
            self.gpio_od_vars[index].set(bool(bits.open_drain))
            for combo in self.gpio_function_combos[index]:
                combo.configure(values=self._gpio_function_labels(index))
            self._refresh_gpio_mcu_label(index)

    def _refresh_gpio_status(self) -> None:
        controller = getattr(self, "gpio_controller", None)
        if controller is None:
            return
        for index in range(2):
            if controller.chip_confirmed:
                self.gpio_chip_status_vars[index].set(self.tr("awp1221_gpio_chip_confirmed"))
            else:
                self.gpio_chip_status_vars[index].set(self.tr("awp1221_gpio_chip_unconfirmed"))
            mcu = controller.confirmed_mcu[index] if controller.chip_confirmed else None
            if mcu:
                self.gpio_mcu_status_vars[index].set(self.tr("awp1221_gpio_mcu_confirmed", mode=mcu))
            else:
                self.gpio_mcu_status_vars[index].set(self.tr("awp1221_gpio_mcu_unconfirmed"))
            self._sync_gpio_actions(index)

    def _invalidate_gpio_settings(self) -> None:
        controller = getattr(self, "gpio_controller", None)
        if controller is not None:
            controller.invalidate()
        if getattr(self, "gpio_chip_status_vars", None) is not None:
            self._refresh_gpio_status()

    def apply_gpio_settings(self) -> None:
        if not self._ensure_connected():
            self._invalidate_gpio_settings()
            return
        pending = self._gpio_pending_from_vars()
        if pending is None:
            messagebox.showerror(self.tr("error"), self.tr("awp1221_gpio_pending_incomplete"))
            return
        self.gpio_controller.set_pending(*pending)
        error = self.gpio_controller.apply()
        self._refresh_gpio_status()
        if error:
            messagebox.showerror(self.tr("error"), error)

    def read_gpio_settings(self) -> None:
        if not self._ensure_connected():
            self._invalidate_gpio_settings()
            return
        error = self.gpio_controller.read()
        if error:
            self._refresh_gpio_status()
            messagebox.showerror(self.tr("error"), error)
            return
        confirmed = self.gpio_controller.confirmed_channels
        if confirmed[0] is not None and confirmed[1] is not None:
            self._apply_pending_to_vars(confirmed[0], confirmed[1])
        self._refresh_gpio_status()

    def gpio_drive(self, channel: int, high: bool) -> None:
        if not self._ensure_connected():
            return
        error = self.gpio_controller.drive(channel, high)
        self._refresh_gpio_status()
        if error:
            messagebox.showerror(self.tr("error"), error)

    @staticmethod
    def _parse_bounded(text: str, maximum: int, name: str) -> int:
        value = int(text.strip(), 0)
        if not 0 <= value <= maximum:
            raise ValueError(f"{name} out of range")
        return value

    def _device_addr(self) -> int:
        value = self._parse_bounded(self.addr_var.get(), 0xFF, "device address")
        if value not in self.DEVICE_ADDRS:
            raise ValueError("AWP1221 device address must be in range 0x50..0x57")
        return value

    def _reg_addr(self) -> int:
        return self._parse_bounded(self.reg_addr_var.get(), 0xFF, "register address")

    def _write_value(self) -> int:
        return self._parse_bounded(self.write_value_var.get(), 0xFFFF, "register value")

    def _shared_port_identity(self) -> str | None:
        selector = getattr(self.ctx.serial, "selected_port_for_page", None)
        if callable(selector):
            try:
                port = selector(self.page_name)
            except TypeError:
                port = selector()
            if port:
                return str(port)
        port = getattr(getattr(self.ctx, "state", None), "selected_port", None)
        return str(port) if port else None

    def _set_en_state(self, state: bool | None) -> None:
        self._en_state = None if state is None else bool(state)
        self._en_identity = (
            active_serial_identity(self.ctx, self.page_name)
            if self._en_state is not None else None
        )
        variable = getattr(self, "en_enabled_var", None)
        if variable is not None:
            variable.set(self._en_state is True)
        status_var = getattr(self, "en_status_var", None)
        translator = getattr(self, "tr", None)
        if status_var is not None and callable(translator):
            key = (
                "awp1221_en_unknown"
                if self._en_state is None
                else "awp1221_en_high"
                if self._en_state
                else "awp1221_en_low"
            )
            status_var.set(translator(key))

    def _refresh_en_status(self) -> None:
        state = getattr(self, "_en_state", None)
        status_var = getattr(self, "en_status_var", None)
        if status_var is None:
            return
        key = (
            "awp1221_en_unknown"
            if state is None
            else "awp1221_en_high"
            if state
            else "awp1221_en_low"
        )
        status_var.set(self.tr(key))

    def _invalidate_sinc_for_en_disable(self) -> None:
        owner = getattr(self, "_shared_state_owner", None)
        if owner is not None:
            owner.invalidate_all()

    def _sync_shared_d4_gpio_mode(self, mode: str | None) -> None:
        """Keep the AWP1221 EN/SINC state aligned with the shared D4 mode."""
        identity = active_serial_identity(self.ctx, self.page_name)
        if mode is None:
            self._set_en_state(None)
            self._invalidate_sinc_for_en_disable()
            self._shared_d4_panel_identity = None
            return
        self._shared_d4_panel_identity = identity
        if mode == GPIO_MODE_HIGH:
            self._set_en_state(True)
            return
        if mode == GPIO_MODE_LOW:
            self._set_en_state(False)
        else:
            self._set_en_state(None)
        self._invalidate_sinc_for_en_disable()

    def _invalidate_shared_d4_gpio_mode(
        self, source_identity: tuple[Any, ...] | None = None
    ) -> None:
        """Clear D4 EN/SINC state after a failed AWP1018 shared-pin write."""
        target_identity = active_serial_identity(self.ctx, self.page_name)
        previous_identity = getattr(self, "_shared_d4_panel_identity", None)
        same_active_session = (
            source_identity is not None
            and target_identity is not None
            and source_identity == target_identity
        )
        same_disconnected_session = (
            source_identity is None
            and target_identity is None
            and previous_identity is not None
        )
        one_side_disconnected_after_same_session = (
            previous_identity is not None
            and (
                source_identity == previous_identity
                or target_identity == previous_identity
            )
            and (source_identity is None or target_identity is None)
        )
        if not (
            same_active_session
            or same_disconnected_session
            or one_side_disconnected_after_same_session
        ):
            return
        self._set_en_state(None)
        self._invalidate_sinc_for_en_disable()
        self._shared_d4_panel_identity = None

    def _notify_shared_d4_gpio_panel(self, mode: str | None) -> None:
        """Reflect the acknowledged EN write in the AWP1018 shared-pin panel."""
        navigation = getattr(getattr(self.ctx, "app", None), "navigation", None)
        pages = getattr(navigation, "pages", {})
        # Notifications only update an existing view. A lazy registry lookup
        # here would construct another chip's entire UI during navigation.
        awp1018 = dict.get(pages, "awp1018") if isinstance(pages, dict) else None
        controls = getattr(awp1018, "gpio_controls", None)
        if controls is None:
            return
        source_identity = active_serial_identity(self.ctx, self.page_name)
        target_identity = active_serial_identity(
            getattr(awp1018, "ctx", self.ctx), "awp1018"
        )
        if mode is None:
            previous_identity = getattr(self, "_shared_d4_panel_identity", None)
            same_active_session = (
                source_identity is not None
                and target_identity is not None
                and source_identity == target_identity
            )
            same_disconnected_session = (
                source_identity is None
                and target_identity is None
                and previous_identity is not None
            )
            one_side_disconnected_after_same_session = (
                previous_identity is not None
                and (
                    source_identity == previous_identity
                    or target_identity == previous_identity
                )
                and (source_identity is None or target_identity is None)
            )
            if not (
                same_active_session
                or same_disconnected_session
                or one_side_disconnected_after_same_session
            ):
                return
            invalidate = getattr(controls, "invalidate_external_mode", None)
            if callable(invalidate):
                invalidate("D4")
            self._shared_d4_panel_identity = None
            return
        if source_identity is None or source_identity != target_identity:
            return
        apply_mode = getattr(controls, "apply_external_mode", None)
        if callable(apply_mode):
            apply_mode("D4", mode)
            self._shared_d4_panel_identity = source_identity

    @staticmethod
    def _gpio_write_ack_matches(response: Any, enabled: bool) -> bool:
        if not bool(getattr(response, "ok", False)):
            return False
        frame = str(getattr(response, "data", "") or "").strip()
        if frame.startswith("^") and frame.endswith("~"):
            frame = frame[1:-1].strip()
        fields = [part.upper() for part in frame.split()]
        return fields == ["GPIOCFG", "D4", "HIGH" if enabled else "LOW"]

    def _set_en_high_adc_load(self) -> None:
        mode_page = self.mode_pages.get(self.current_mode)
        if mode_page is None:
            raise RuntimeError("AWP1221 mode page is unavailable")
        address = self._device_addr()
        current = mode_page._ensure_reg_1f_bit3(address)
        mode_page._write_register_value(
            AWP1221_ADC_LOAD_REGISTER,
            awp1221_en_high_load_value(current),
            address,
        )
        for page in self.mode_pages.values():
            page.adc_ldo_var.set("160 uA")
            sync_load = getattr(page, "_sync_realtime_adc_load", None)
            if callable(sync_load):
                sync_load("160 uA")

    def _on_en_toggled(self) -> None:
        requested = bool(self.en_enabled_var.get())
        if not requested:
            self._invalidate_sinc_for_en_disable()
        if not self._ensure_connected():
            self._set_en_state(None)
            self._notify_shared_d4_gpio_panel(None)
            return

        try:
            response = self.ctx.serial.configure_gpio("D4", GPIO_MODE_HIGH if requested else GPIO_MODE_LOW)
        except Exception as exc:
            self._set_en_state(None)
            self._invalidate_sinc_for_en_disable()
            self._notify_shared_d4_gpio_panel(None)
            messagebox.showerror(self.tr("error"), str(exc))
            return

        if not self._gpio_write_ack_matches(response, requested):
            self._set_en_state(None)
            self._invalidate_sinc_for_en_disable()
            self._notify_shared_d4_gpio_panel(None)
            detail = str(getattr(response, "error", "") or "").strip()
            if not detail:
                data = str(getattr(response, "data", "") or "").strip()
                detail = f"Unexpected GPIO ACK: {data or '<empty>'}"
            messagebox.showerror(self.tr("error"), detail)
            return

        if requested:
            try:
                self._set_en_high_adc_load()
            except Exception as exc:
                # GPIO ACK already confirmed EN HIGH.  Keep that state visible
                # even when the follow-up ADC_LDO write fails.
                self._set_en_state(True)
                self._notify_shared_d4_gpio_panel(GPIO_MODE_HIGH)
                messagebox.showerror(self.tr("error"), str(exc))
                return

        self._set_en_state(requested)
        self._notify_shared_d4_gpio_panel(GPIO_MODE_HIGH if requested else GPIO_MODE_LOW)

    def _ensure_connected(self) -> bool:
        if self.ctx.serial.is_connected:
            return True
        self._set_en_state(None)
        self._invalidate_gpio_settings()
        owner = getattr(self, "_shared_state_owner", None)
        if owner is not None:
            owner.invalidate_all(clear_active=True)
        messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
        return False

    def _on_addr_changed(self) -> None:
        try:
            address = self._device_addr()
        except (AttributeError, ValueError):
            pass
        else:
            if getattr(self, "_shared_addr", None) != address:
                owner = getattr(self, "_shared_state_owner", None)
                if owner is not None and getattr(self, "_shared_addr", None) is not None:
                    owner.invalidate_all()
                self._set_en_state(None)
                self._invalidate_gpio_settings()
                self._shared_addr = address
            self.ctx.state.extra["awp1221_device_addr"] = address

    def _on_serial_selector_changed(self, control: dict[str, Any]) -> None:
        super()._on_serial_selector_changed(control)
        self._set_en_state(None)
        self._shared_d4_panel_identity = None
        gpio_controls = getattr(self, "gpio_controls", None)
        if gpio_controls is not None:
            gpio_controls.invalidate_modes()
        self._invalidate_gpio_settings()
        self._notify_shared_d4_gpio_panel(None)
        owner = getattr(self, "_shared_state_owner", None)
        if owner is not None:
            owner.invalidate_all()

    def _scan_for_chip(self) -> tuple[int | None, int | None, str]:
        last_detail = ""
        with _serial_batch(self.ctx.serial):
            for addr in self.DEVICE_ADDRS:
                try:
                    resp = self.ctx.serial.read_register_value(addr, self.ID_REG, bits=16)
                except Exception as exc:
                    last_detail = str(exc)
                    continue
                if not resp.ok:
                    last_detail = resp.error or resp.data
                    continue
                try:
                    value = self.ctx.serial.extract_rdataw_value(
                        resp.data,
                        device_addr=addr,
                        reg_addr=self.ID_REG,
                        bits=16,
                    )
                except Exception as exc:
                    last_detail = str(exc)
                    continue
                if value == self.CHIP_ID:
                    return addr, value, last_detail
                last_detail = (
                    resp.data
                    if value is None
                    else f"0x{addr:02X}: 0x{value & 0xFFFF:04X}"
                )
        return None, None, last_detail

    def _set_detection_busy(self, busy: bool) -> None:
        button = getattr(self, "_buttons", {}).get("detect_chip")
        if button is not None:
            button.configure(state="disabled" if busy else "normal")

    def _finish_chip_detection(
        self,
        original_addr: str,
        result: tuple[int | None, int | None, str] | None,
        error: str | None = None,
    ) -> None:
        self._set_detection_busy(False)
        addr, value, last_detail = result or (None, None, "")
        if addr is not None and value is not None:
            self.addr_var.set(f"0x{addr:02X}")
            self._on_addr_changed()
            self.status_var.set(
                self.tr("chip_detect_success") + f" 0x{addr:02X}: 0x{value:04X}"
            )
            return
        self.addr_var.set(original_addr)
        if error:
            last_detail = error
        suffix = f" {last_detail}" if last_detail else ""
        self.status_var.set(self.tr("chip_detect_failed") + suffix)

    def detect_chip(self) -> None:
        if not self._ensure_connected():
            return
        original_addr = self.addr_var.get()
        self._set_detection_busy(True)
        _run_ui_background_task(
            self,
            self._scan_for_chip,
            on_done=lambda result: self._finish_chip_detection(original_addr, result),
            on_error=lambda error: self._finish_chip_detection(
                original_addr,
                None,
                str(error),
            ),
        )

    def read_register(self) -> None:
        if not self._ensure_connected():
            return
        try:
            reg = self._reg_addr()
            address = self._device_addr()
            resp = self.ctx.serial.read_register_value(address, reg, bits=16)
            if not resp.ok:
                messagebox.showerror(self.tr("error"), resp.error)
                return
            value = self.ctx.serial.extract_rdataw_value(
                resp.data,
                device_addr=address,
                reg_addr=reg,
                bits=16,
            )
            if value is None:
                raise ValueError(resp.data or "invalid register response")
            self.read_value_var.set(f"0x{value:04X}")
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def write_register(self) -> None:
        if not self._ensure_connected():
            return
        try:
            register = self._reg_addr()
            resp = self.ctx.serial.write_register_value(
                self._device_addr(),
                register,
                self._write_value(),
                bits=16,
            )
        except Exception as exc:
            try:
                if self._reg_addr() == AWP1221_GPIO_REGISTER:
                    self._invalidate_gpio_settings()
            except Exception:
                pass
            messagebox.showerror(self.tr("error"), str(exc))
            return
        if register == AWP1221_GPIO_REGISTER:
            self._invalidate_gpio_settings()
        if not resp.ok:
            messagebox.showerror(self.tr("error"), resp.error)

    def _read_chip_register(self, address: int, register: int) -> int:
        resp = self.ctx.serial.read_register_value(address, register, bits=16)
        if not resp.ok:
            raise ValueError(resp.error)
        value = self.ctx.serial.extract_rdataw_value(
            resp.data,
            device_addr=address,
            reg_addr=register,
            bits=16,
        )
        if value is None:
            raise ValueError(resp.data or f"invalid register 0x{register:02X} response")
        return value

    def _write_chip_register(self, address: int, register: int, value: int) -> None:
        resp = self.ctx.serial.write_register_value(address, register, value, bits=16)
        if not resp.ok:
            raise ValueError(resp.error)

    def write_chip_state(self, value: int) -> None:
        if not self._ensure_connected():
            return
        address = self._device_addr()
        try:
            ensure_awp1221_cmd_gate(
                lambda register: self._read_chip_register(address, register),
                lambda register, data: self._write_chip_register(address, register, data),
            )
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        owner = getattr(self, "_shared_state_owner", None)
        if owner is not None:
            owner.invalidate_device(
                self.ctx.serial,
                address,
                port=self._shared_port_identity(),
                clear_active=True,
            )
        resp = self.ctx.serial.write_register_value(address, 0x01, value, bits=16)
        if not resp.ok:
            messagebox.showerror(self.tr("error"), resp.error)

    def show_mode(self, mode: str) -> None:
        if mode not in self.mode_pages:
            return
        self.mode_scroll.clamp_width = mode == "button_mode"
        previous_page = self.mode_pages.get(self.current_mode)
        next_page = self.mode_pages[mode]
        if previous_page is not None and previous_page is not next_page and hasattr(previous_page, "on_hide"):
            previous_page.on_hide()
        self.current_mode = mode
        for page in dict.values(self.mode_pages):
            page.grid_remove()
        self.mode_pages[mode].grid()
        next_page = self.mode_pages[mode]
        sync_activity = getattr(next_page, "_sync_local_activity", None)
        if callable(sync_activity):
            sync_activity()
        self._sync_mode_button_styles()
        # Switching modes can change the embedded page request height while
        # the outer canvas keeps its previous window height.  Let Tk settle
        # the new request, then re-run the fill/scroll calculation so Full
        # mode remains reachable after Button mode and vice versa.
        try:
            job = getattr(self, "_mode_scroll_layout_job", None)
            if job is not None:
                self.after_cancel(job)
            self._mode_scroll_layout_job = self.after(0, self._run_mode_scroll_layout)
        except tk.TclError:
            self._mode_scroll_layout_job = None

    def _run_mode_scroll_layout(self) -> None:
        self._mode_scroll_layout_job = None
        self._sync_mode_scroll_layout()

    def _sync_mode_scroll_layout(self) -> None:
        try:
            # Configure events complete the geometry pass. Re-entering the
            # idle loop here drains every pending redraw before input can run.
            self.mode_scroll._sync_scroll_region()
        except tk.TclError:
            pass

    def on_hide(self) -> None:
        current_page = self.mode_pages.get(self.current_mode)
        if current_page is not None:
            current_page.on_hide()

    def destroy(self) -> None:
        for name in ("_footer_fit_job", "_mode_scroll_layout_job"):
            job = getattr(self, name, None)
            if not job:
                continue
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
            setattr(self, name, None)
        super().destroy()

    def on_show(self) -> None:
        super().on_show()
        # Navigation does not change D4. Retain its ACK only for the same
        # connected manager/generation; a reconnect still needs confirmation.
        identity = active_serial_identity(self.ctx, self.page_name)
        same_en_session = identity is not None and identity == getattr(self, "_en_identity", None)
        if same_en_session:
            self._refresh_en_status()
        else:
            self._set_en_state(None)
            self._shared_d4_panel_identity = None
        gpio_controls = getattr(self, "gpio_controls", None)
        if gpio_controls is not None:
            gpio_controls.invalidate_modes()
        self._invalidate_gpio_settings()
        if not same_en_session:
            self._notify_shared_d4_gpio_panel(None)

    def refresh_language(self) -> None:
        super().refresh_language()
        self._refresh_en_status()
        for key, label in self._labels.items():
            label.configure(text=self.tr(key))
        for key, button in self._buttons.items():
            button.configure(text=self.tr(key))
        for key, button in self.mode_buttons.items():
            button.configure(text=self.tr(key))
        for page in dict.values(self.mode_pages):
            page.refresh_language()
        gpio_controls = getattr(self, "gpio_controls", None)
        if gpio_controls is not None:
            gpio_controls.refresh_language()
        if getattr(self, "gpio_controller", None) is not None:
            for index in range(2):
                self._on_gpio_category_selected(index)
            self._refresh_gpio_status()

    def refresh_theme(self) -> None:
        super().refresh_theme()
        for page in dict.values(self.mode_pages):
            refresh = getattr(page, "refresh_theme", None)
            if callable(refresh):
                refresh()
