from __future__ import annotations

from collections.abc import Iterator

import base64
import json
import math
import re
import threading
import tkinter as tk
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from config.lang_r2_c import install as _install_r2c_language
from styles.theme import waveform_choice_styles

from core.awp192x_button_runtime import (
    AWP192X_BUTTON_MAX_WAVEFORMS,
    AWP192X_BUTTON_PAYLOAD_V2_VERSION,
    AWP192X_GENERATOR_SINC,
    AWP192X_GENERATOR_SINC_VERSION,
    Awp192xButtonPayloadV2,
    Awp192xSineWaveformRecipe,
    Awp192xWaveformRecipe,
    derive_wait_release_adc_event,
    generate_awp192x_sinc_points,
    generate_awp192x_waveform_points,
    required_awp192x_button_waveform_slices,
)
from core.base_tool import (
    RegisterScriptOperation,
    format_register_script,
    parse_register_script,
)
from core.chip_discovery import AWP192X_PROBE
from core.waveform_storage import WaveformStorage
from core.awp192x_settings import (
    connection_key_for_context,
    settings_store_for_context,
    waveform_label,
    waveform_number,
)
from pages.chip_settings_model import SYNC_FIELDS_192X, can_save_slot
from pages.awp192x_button_view import ButtonModeView
from pages.base_page import (
    BasePage,
    CanvasTitle,
    SimpleTooltip,
    VerticalScrollArea,
    attach_glossary_tooltips,
    fit_toplevel_to_screen,
)
from pages.preset_toolbar import PresetToolbar
from pages.step_progress import StepSpec
from pages.waveform_generator import WAVEFORM_DEFAULT_POINTS_PER_WRITE, WaveformGeneratorPage
from core.ui_dispatch import post_to_ui

_install_r2c_language()


def build_awp192x_amp_options() -> list[tuple[str, float, int]]:
    """
    Pump/Button Mode \u6700\u5927\u8f93\u51fa\u7535\u538b\u9009\u9879\uff1a
        2.97, 5.94, ..., 95.04
        reg: 0x0002, 0x0006, 0x000A, ..., 0x007E
    """
    options = []
    reg = 0x0002
    value = 2.97
    while reg <= 0x007E:
        label = f"{value:.2f} V"
        options.append((label, round(value, 2), reg))
        reg += 0x0004
        value += 2.97
    return options


def build_frequency_options() -> list[tuple[str, int, int]]:
    freqs = [1000, 500, 333, 250, 200, 167, 142, 125, 111, 100]
    return [(f"{freq} Hz", freq, idx) for idx, freq in enumerate(freqs)]


BUTTON_PRESET_SINC_NEGATIVE_VOLTAGE = -20
BUTTON_PRESET_SINC_POINTS = (256, 256, 128)

# WREGW advances the target register for each value. That behavior is only
# part of the AWP192x SRAM-write contract, never a generic property inferred
# from an imported script's register sequence.
AWP192X_SRAM_AUTO_INCREMENT_ADDRESSES = frozenset(AWP192X_PROBE.addresses)
AWP192X_WAVEFORM_METADATA_TAG = "AWP192X_WAVEFORM_V1"
AWP192X_PAGE_SETTINGS_TAG = "AWP192X_PAGE_SETTINGS_V1"


def _begin_status(page: Any, event: str, **kwargs: Any) -> Any:
    """Status instrumentation stays optional for lightweight page harnesses."""
    begin = getattr(page, "begin_status", None)
    return begin(event, **kwargs) if callable(begin) else None


def _finish_status(page: Any, token: Any, *, success: bool, error: str = "") -> bool:
    finish = getattr(page, "finish_status", None)
    return bool(finish(token, success=success, error=error)) if callable(finish) else False


def _report_progress_status(page: Any, progress_token: Any, state: str, *, detail: str = "") -> bool:
    report = getattr(page, "report_progress_status", None)
    return bool(report(progress_token, state, detail=detail)) if callable(report) else False


@dataclass(frozen=True)
class Awp192xScriptWaveform:
    device_addr: int
    slice_index: int
    start_addr: int
    end_addr: int
    values: tuple[int, ...]
    recipe: Awp192xWaveformRecipe | None = None


def _format_awp192x_waveform_recipe_metadata(recipe: Awp192xWaveformRecipe) -> str:
    generate_awp192x_sinc_points(recipe)
    return (
        f"{AWP192X_WAVEFORM_METADATA_TAG} "
        f"type=sinc generator={recipe.generator_type} version={recipe.generator_version} "
        f"slice={recipe.slice_index} start=0x{recipe.start_addr:04X} "
        f"points1={recipe.points1} points2={recipe.points2} points3={recipe.points3} "
        f"positive_mv={recipe.positive_mv} negative_mv={recipe.negative_mv}"
    )


def _parse_awp192x_waveform_recipe_metadata(
    operations: list[RegisterScriptOperation],
) -> Awp192xWaveformRecipe | None:
    for operation in operations:
        marker = operation.comment.find(AWP192X_WAVEFORM_METADATA_TAG)
        if marker < 0:
            continue
        fields: dict[str, str] = {}
        for token in operation.comment[marker + len(AWP192X_WAVEFORM_METADATA_TAG) :].split():
            if "=" not in token:
                return None
            key, value = token.split("=", 1)
            fields[key] = value
        try:
            if fields.pop("type").lower() != "sinc" or fields.keys() != {
                "generator",
                "version",
                "slice",
                "start",
                "points1",
                "points2",
                "points3",
                "positive_mv",
                "negative_mv",
            }:
                return None
            recipe = Awp192xWaveformRecipe(
                generator_type=int(fields["generator"], 0),
                generator_version=int(fields["version"], 0),
                slice_index=int(fields["slice"], 0),
                start_addr=int(fields["start"], 0),
                points1=int(fields["points1"], 0),
                points2=int(fields["points2"], 0),
                points3=int(fields["points3"], 0),
                positive_mv=int(fields["positive_mv"], 0),
                negative_mv=int(fields["negative_mv"], 0),
            )
            generate_awp192x_sinc_points(recipe)
        except (KeyError, TypeError, ValueError):
            return None
        return recipe
    return None


def extract_awp192x_script_waveform(
    operations: list[RegisterScriptOperation],
) -> Awp192xScriptWaveform | None:
    """Extract a complete waveform block without inferring parameters absent from the TXT."""
    recipe = _parse_awp192x_waveform_recipe_metadata(operations)
    imported: Awp192xScriptWaveform | None = None
    for index in range(max(0, len(operations) - 5)):
        header = operations[index : index + 6]
        if len(header) != 6:
            continue
        mode, select_start, write_start, select_end, write_end, select_data = header
        device_addr = mode.device_addr
        if not (
            mode.action == "WRITE"
            and mode.reg_addr == 0x14
            and mode.value == 0x0003
            and all(operation.device_addr == device_addr for operation in header)
            and select_start.action == "WRITE"
            and select_start.reg_addr == 0x16
            and select_start.value is not None
            and 0 <= select_start.value < WaveformStorage.MAX_SLICES * 2
            and select_start.value % 2 == 0
            and write_start.action == "WRITE"
            and write_start.reg_addr == 0x00
            and write_start.value is not None
            and select_end.action == "WRITE"
            and select_end.reg_addr == 0x16
            and select_end.value == select_start.value + 1
            and write_end.action == "WRITE"
            and write_end.reg_addr == 0x00
            and write_end.value is not None
            and select_data.action == "WRITE"
            and select_data.reg_addr == 0x16
            and select_data.value == write_start.value
        ):
            continue

        values: list[int] = []
        data_index = index + 6
        while data_index < len(operations):
            operation = operations[data_index]
            if not (
                operation.device_addr == device_addr
                and operation.action == "WRITE"
                and operation.reg_addr == 0x00
                and operation.value is not None
            ):
                break
            values.append(operation.value)
            data_index += 1

        start_addr = write_start.value
        end_addr = start_addr + len(values)
        if (
            not values
            or start_addr < WaveformStorage.SRAM_MIN
            or end_addr > WaveformStorage.SRAM_MAX + 1
            or write_end.value != min(end_addr, WaveformStorage.SRAM_MAX)
            or any(value < 0 or value > 0x0FFF for value in values)
        ):
            continue

        slice_index = select_start.value // 2
        matching_recipe = recipe
        if matching_recipe is not None and (
            matching_recipe.slice_index != slice_index
            or matching_recipe.start_addr != start_addr
            or generate_awp192x_sinc_points(matching_recipe) != tuple(values)
        ):
            matching_recipe = None
        imported = Awp192xScriptWaveform(
            device_addr=device_addr,
            slice_index=slice_index,
            start_addr=start_addr,
            end_addr=end_addr,
            values=tuple(values),
            recipe=matching_recipe,
        )
    return imported


def encode_single_point_duration(duration_us: int) -> int:
    if not 1 <= duration_us <= 0xFFFF:
        raise ValueError("single point duration must be 1-65535")
    return duration_us - 1


def build_waveform_register_script_operations(
    device_addr: int,
    *,
    values: list[int],
    slice_index: int,
    start_addr: int,
    end_addr: int,
    max_output_reg_value: int,
    waveform_recipe: Awp192xWaveformRecipe | None = None,
) -> list[RegisterScriptOperation]:
    values = list(values)
    mode_comment = "Enter waveform SRAM write mode"
    if waveform_recipe is not None:
        expected_values = generate_awp192x_sinc_points(waveform_recipe)
        if (
            waveform_recipe.slice_index != int(slice_index)
            or waveform_recipe.start_addr != int(start_addr)
            or int(end_addr) != int(start_addr) + len(values)
            or expected_values != tuple(values)
        ):
            raise ValueError("waveform recipe does not match waveform data")
        mode_comment += f"; {_format_awp192x_waveform_recipe_metadata(waveform_recipe)}"
    index_start_addr = int(slice_index) * 2
    index_end_addr = index_start_addr + 1
    register_end_addr = min(int(end_addr), WaveformStorage.SRAM_MAX)
    operations = [
        RegisterScriptOperation(device_addr, "WRITE", 0x14, 0x0003, mode_comment),
        RegisterScriptOperation(device_addr, "WRITE", 0x16, index_start_addr, "Select slice start index entry"),
        RegisterScriptOperation(device_addr, "WRITE", 0x00, start_addr, "Write slice start SRAM address"),
        RegisterScriptOperation(device_addr, "WRITE", 0x16, index_end_addr, "Select slice end index entry"),
        RegisterScriptOperation(device_addr, "WRITE", 0x00, register_end_addr, "Write slice end SRAM address"),
        RegisterScriptOperation(device_addr, "WRITE", 0x16, start_addr, "Set waveform SRAM data start address"),
    ]
    operations.extend(
        RegisterScriptOperation(device_addr, "WRITE", 0x00, value, "Write waveform data point")
        for value in values
    )
    operations.append(RegisterScriptOperation(device_addr, "WRITE", 0x14, max_output_reg_value, "Restore max output voltage setting"))
    return operations


def build_button_mode_register_script_operations(
    device_addr: int,
    *,
    max_output_reg_value: int,
    duration: int,
    press_code: int,
    release_code: int,
    adc_output_enable: int,
    d2_config: int,
    adc_filter_config: int,
    press_value: int,
    adc_event_config: int,
    release_value: int,
    play_mode_value: int,
) -> list[RegisterScriptOperation]:
    register_duration = encode_single_point_duration(duration)
    return [
        RegisterScriptOperation(device_addr, "WRITE", 0x14, max_output_reg_value, "Set max output voltage"),
        RegisterScriptOperation(device_addr, "WRITE", 0x15, register_duration, "Set single-point duration"),
        RegisterScriptOperation(device_addr, "WRITE", 0x08, press_code, "Set press threshold ADC code"),
        RegisterScriptOperation(device_addr, "WRITE", 0x09, release_code, "Set release threshold ADC code"),
        RegisterScriptOperation(device_addr, "WRITE", 0x0A, adc_output_enable, "Enable ADC output"),
        RegisterScriptOperation(device_addr, "WRITE", 0x03, d2_config, "Configure D2 button detection"),
        RegisterScriptOperation(device_addr, "WRITE", 0x0C, adc_filter_config, "Set ADC debounce/filter"),
        RegisterScriptOperation(device_addr, "WRITE", 0x0F, press_value, "Set press repeat and waveform slice"),
        RegisterScriptOperation(device_addr, "WRITE", 0x0B, adc_event_config, "Enable ADC event detection"),
        RegisterScriptOperation(device_addr, "READ", 0x11, None, "Read release configuration"),
        RegisterScriptOperation(device_addr, "WRITE", 0x11, release_value, "Set release repeat and waveform slice"),
        RegisterScriptOperation(device_addr, "READ", 0x13, None, "Read playback configuration"),
        RegisterScriptOperation(device_addr, "WRITE", 0x13, play_mode_value, "Set playback repeat/waveform/endless"),
    ]


def _format_preset_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)





class ButtonModePage(ButtonModeView, BasePage):
    page_name = "button_mode"
    CURRENT_SETTING_REGISTERS = (
        0x14,
        0x15,
        0x08,
        0x09,
        0x0C,
        0x0B,
        0x0F,
        0x11,
        0x13,
        0x17,
        0x18,
        0x19,
        0x1A,
    )
    BUTTON_PRESETS: tuple[dict[str, Any], ...] = (
        {
            "duration": 9,
            "press_repeat": 2,
            "release_repeat": 1,
            "sinc_positive": 80,
        },
        {
            "duration": 4,
            "press_repeat": 4,
            "release_repeat": 2,
            "sinc_positive": 80,
        },
        {
            "duration": 11,
            "press_repeat": 2,
            "release_repeat": 1,
            "sinc_positive": 60,
            "sinc_negative": -12,
            "sinc_points": (180, 160, 90),
            "press_threshold": 1.2,
            "release_threshold": -0.03,
        },
    )
    ACTION_TOOLTIP_KEYS = {
        "button_mode_import_user_preset": "button_mode_import_page_settings_tooltip",
        "button_mode_export_user_preset": "button_mode_export_page_settings_tooltip",
        "save_to_board": "save_no_gui_settings_tooltip",
        "set_active": "set_boot_settings_tooltip",
        "apply_slot": "apply_current_settings_tooltip",
        "read_slot": "read_current_settings_tooltip",
    }
    PRESET_ACTION_KEYS = (
        "software_preset_1",
        "software_preset_2",
        "software_preset_3",
        "import_page_settings",
        "export_page_settings",
        "save_slot",
        "set_active_slot",
        "apply_slot",
        "read_and_apply",
    )
    READ_SETTINGS_TOOLTIP = {
        "zh": "读取芯片当前设置并同步到页面；不读取所选板载槽位，也不会额外写入槽位。",
        "en": "Read the chip's current settings into the page. This does not load the selected slot or write any slot.",
    }

    def _ensure_connected(self) -> bool:
        """检查当前串口是否已经连接。"""
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(
                self.tr("error"),
                self.tr("not_connected"),
            )
            return False

        return True

    ADC_GAIN_SMALL_RANGE = 1
    ADC_SMALL_RANGE_VOLTAGE = 18.0
    ADC_LARGE_RANGE_VOLTAGE = 100.0
    ADC_POS_ZERO = 0x0800
    ADC_NEG_ZERO = 0x07FF
    ADC_MIN_CODE = 0x0000
    ADC_MAX_CODE = 0x0FFF
    RELEASE_THRESHOLD_MIN_V = -0.35
    ADC_EVENT_CONFIG = (1 << 9) | (1 << 5) | 0x0001
    ADC_DETECT_ENABLE = 0x8000
    ADC_OUTPUT_ENABLE = 0x8000
    ADC_D2_CONFIG = 0x4000
    ADC_DEBOUNCE_CONFIG = 0x0044
    BUTTON_RUNTIME_PAYLOAD_VERSION = AWP192X_BUTTON_PAYLOAD_V2_VERSION
    BUTTON_RUNTIME_GENERATOR_SINC = 1
    BUTTON_RUNTIME_GENERATOR_VERSION = 1
    BUTTON_RUNTIME_FLAG_SERIAL_VALID = 0x01
    SERIAL_WAVE_NUMBER = 0x1E
    BUTTON_RUNTIME_RELEASE_TIMEOUT_US = 3_000_000
    ADC_AVERAGE_OPTIONS = (
        ("input", 0x00),
        ("1/2 input + 1/2 average", 0x01),
        ("1/4 input + 3/4 average", 0x02),
        ("1/8 input + 7/8 average", 0x03),
        ("1/16 input + 15/16 average", 0x04),
        ("1/32 input + 31/32 average", 0x05),
    )
    ADC_OVV_OPTIONS = (
        ("1 time", 0x0),
        ("2 times", 0x1),
        ("4 times", 0x2),
        ("8 times", 0x3),
        ("16 times", 0x4),
        ("32 times", 0x5),
        ("64 times", 0x6),
        ("128 times", 0x7),
        ("256 times", 0x8),
        ("512 times", 0x9),
        ("1023 times", 0xA),
    )
    WAVEFORM_FIELD_MASK = 0x001F
    REPEAT_FIELD_MASK = 0x1FF << 5
    ENDLESS_PLAY_BIT = 1 << 14
    STATUS_IDLE_COLOR = "#E5E7EB"
    STATUS_READY_COLOR = "#FACC15"
    STATUS_PRESS_COLOR = "#2563EB"
    STATUS_RELEASE_COLOR = "#9333EA"
    STATUS_TIMEOUT_COLOR = "#DC2626"
    DETECT_ADC_BUSY_RETRY_MS = 250
    DETECT_STATUS_PRESS = 0x0800
    DETECT_STATUS_RELEASE = 0x0200

    def __init__(self, parent: tk.Misc, ctx: Any, *, defer_build: bool = False) -> None:
        super().__init__(parent, ctx)

        self.amp_options = build_awp192x_amp_options()
        self.storage = WaveformStorage()

        self.max_voltage_var = tk.StringVar(value=self.amp_options[-1][0])
        self.duration_var = tk.StringVar(value=str(self.ctx.state.single_point_duration_us))
        self.press_threshold_var = tk.StringVar(value="0")
        self.release_threshold_var = tk.StringVar(value="0")
        self.press_threshold_hex_var = tk.StringVar(value="")
        self.release_threshold_hex_var = tk.StringVar(value="")
        self.press_repeat_count_var = tk.StringVar(value="1")
        self.release_repeat_count_var = tk.StringVar(value="1")
        self.play_repeat_count_var = tk.StringVar(value="1")

        self.press_waveform_var = tk.StringVar(value="slice0")
        self.release_waveform_var = tk.StringVar(value="slice0")
        self.play_waveform_var = tk.StringVar(value="slice0")
        self._waveform_choice_bindings: list[dict[str, Any]] = []
        self._waveform_choice_trace_ids: list[tuple[tk.Variable, str]] = []
        self._waveform_choice_notice_persistent = False
        self.endless_play_var = tk.BooleanVar(value=False)
        self.board_slot_var = tk.StringVar(value="0")
        self.period_hint_var = tk.StringVar(value="")
        self.adc_value_var = tk.StringVar(value="")
        self.adc_average_time_var = tk.StringVar(
            value=self._adc_average_label((self.ADC_DEBOUNCE_CONFIG >> 4) & 0x0F)
        )
        self.adc_ovv_time_var = tk.StringVar(value=self._adc_ovv_label(self.ADC_DEBOUNCE_CONFIG & 0x0F))
        self.button_adc_pol_var = tk.StringVar(value="OUT+")
        self.button_adc_gain_var = tk.StringVar(value="0-19V")

        self._labels: dict[str, ttk.Label] = {}
        self._buttons: dict[str, ttk.Button] = {}
        self._checks: dict[str, ttk.Checkbutton] = {}
        self._repeat_entries: dict[str, ttk.Entry] = {}
        self.adc_legend = None
        self._sense_status_active = False
        self._sense_timeout_mode = False
        self._sense_live_state = "idle"
        self._sense_live_detail = ""
        self._sense_live_generation = 0
        self._sense_blink_job: str | None = None
        self._sense_ready_poll_job: str | None = None
        self._button_adc_diagram_job: str | None = None
        self._button_adc_diagram_drawn = None
        self._adc_realtime_active = False
        self._adc_realtime_job: str | None = None
        self._button_adc_generation = 0
        self._button_adc_pending = False
        self._adc_trigger_read_active = False
        self._reading_current_settings = False
        self._button_runtime_dirty = True
        self._direct_detection_active = False
        self._detect_phase = "press"
        self._detect_gui_state = "idle"
        self._detect_generation = 0
        self._detect_target_addr: int | None = None
        self._detect_reconfigure_pending = False
        self._detect_await_adc_busy = False
        self._detect_adc_busy_tries = 0
        self._detect_poll_job: str | None = None
        self._adc_legend_target_addr = getattr(self.ctx.state, "awp192x_device_addr", None)
        self._tooltips: list[SimpleTooltip] = []
        self._button_rom_reconcile_job: str | None = None

        self._preparing = defer_build
        self._prepared_registers_pending = defer_build
        self.bind("<Destroy>", self._on_button_page_destroy, add="+")
        self._build_steps = self._initialize_steps()
        if not defer_build:
            for _ in self._build_steps:
                pass

    def _initialize_steps(self) -> Iterator[None]:
        yield from self._build_ui_steps()
        self._update_period_hint(write_register=not self._preparing)
        self.ctx.serial.add_log_listener(self._handle_sense_status_log)
        self._preparing = False

    def _adc_average_label(self, code: int) -> str:
        return next((label for label, value in self.ADC_AVERAGE_OPTIONS if value == code), self.ADC_AVERAGE_OPTIONS[-1][0])

    def _adc_ovv_label(self, code: int) -> str:
        return next((label for label, value in self.ADC_OVV_OPTIONS if value == code), self.ADC_OVV_OPTIONS[-1][0])

    def _build_ui(self) -> None:
        for _ in self._build_ui_steps():
            pass

    def _build_ui_steps(self) -> Iterator[None]:
        yield
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        title_header = self.make_title(header, "button_mode", show_logo=False)
        title_header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())
        self.mode_title_label = title_header.grid_slaves(row=0, column=0)[0]
        self._build_waveform_choice_notice(header, persistent=True)
        yield

        self.preset_toolbar = self._create_preset_toolbar(header)
        self.preset_toolbar.mount_header()
        yield

        yield from self._build_button_body_steps()
        yield

        self.duration_var.trace_add("write", lambda *_: self._on_button_runtime_duration_changed())
        for variable in (
            self.press_repeat_count_var,
            self.release_repeat_count_var,
            self.play_repeat_count_var,
            self.press_waveform_var,
            self.release_waveform_var,
            self.play_waveform_var,
        ):
            variable.trace_add("write", lambda *_: self._on_button_runtime_playback_changed())
        self.max_voltage_var.trace_add("write", lambda *_: self._on_voltage_changed())
        self.press_threshold_var.trace_add("write", lambda *_: self._on_button_runtime_threshold_changed())
        self.release_threshold_var.trace_add("write", lambda *_: self._on_button_runtime_threshold_changed())
        for variable in (
            self.press_threshold_var,
            self.release_threshold_var,
            self.press_waveform_var,
            self.release_waveform_var,
        ):
            variable.trace_add("write", lambda *_: self._refresh_button_adc_legend_rules())
        self.button_adc_pol_var.trace_add("write", lambda *_: self._on_button_adc_pol_changed())
        yield
        self.button_adc_gain_var.trace_add("write", lambda *_: self._on_button_adc_gain_changed())
        self.endless_play_var.trace_add("write", lambda *_: self._on_endless_play_changed())

        self.refresh_language()
        self._refresh_threshold_register_value_labels()
        self._on_endless_play_changed()
        self._initialize_awp192x_settings_model()
        self._refresh_button_adc_legend_rules()
        self._refresh_sense_legend()
        self._attach_shared_settings_traces()
        self._mount_button_step_progress()

    def _create_preset_toolbar(self, parent: tk.Misc) -> PresetToolbar:
        callbacks = {
            "software_preset_1": lambda: self.apply_preset(0),
            "software_preset_2": lambda: self.apply_preset(1),
            "software_preset_3": lambda: self.apply_preset(2),
            "import_page_settings": self.import_user_preset_script,
            "export_page_settings": self.export_user_preset_script,
            "save_slot": self.save_board_preset,
            "set_active_slot": self.set_board_preset_active,
            "apply_slot": self.apply_board_preset,
            "read_and_apply": self.read_current_settings,
        }
        label_keys = {
            "set_active_slot": "preset_toolbar_set_active_slot",
            "apply_slot": "preset_toolbar_apply_slot",
            "read_and_apply": "r2b_aux_read_current_settings",
        }
        tooltip_keys = {
            "import_page_settings": "button_mode_import_page_settings_tooltip",
            "export_page_settings": "button_mode_export_page_settings_tooltip",
            "set_active_slot": "set_boot_settings_tooltip",
            "apply_slot": "apply_current_settings_tooltip",
        }
        toolbar = PresetToolbar(
            parent,
            translate=lambda key: self.tr(key),
            bordered=False,
            callbacks=callbacks,
            capabilities={action: True for action in (*self.PRESET_ACTION_KEYS, "slot_selection")},
            label_keys=label_keys,
            tooltip_keys=tooltip_keys,
            slot_values=(0, 1, 2),
            selected_slot=self.board_slot_var.get(),
            on_slot_selected=self._on_toolbar_slot_selected,
            auxiliary_actions=("read_and_apply",),
        )
        self._buttons.update({
            "button_mode_preset_1": toolbar.buttons["software_preset_1"],
            "button_mode_preset_2": toolbar.buttons["software_preset_2"],
            "button_mode_preset_3": toolbar.buttons["software_preset_3"],
            "button_mode_import_user_preset": toolbar.buttons["import_page_settings"],
            "button_mode_export_user_preset": toolbar.buttons["export_page_settings"],
            "save_to_board": toolbar.buttons["save_slot"],
            "set_active": toolbar.buttons["set_active_slot"],
            "apply_slot": toolbar.buttons["apply_slot"],
            "read_slot": toolbar.buttons["read_and_apply"],
        })
        self._labels["board_slot"] = toolbar.slot_label
        self._tooltips.extend(toolbar._tooltips)
        read_tooltip = SimpleTooltip(toolbar.buttons["read_and_apply"], self._read_settings_tooltip_text)
        toolbar._tooltips.append(read_tooltip)
        self._tooltips.append(read_tooltip)
        save_tooltip = SimpleTooltip(toolbar.buttons["save_slot"], self._save_slot_tooltip_text)
        toolbar._tooltips.append(save_tooltip)
        self._tooltips.append(save_tooltip)
        for index in range(3):
            tooltip = SimpleTooltip(
                toolbar.buttons[f"software_preset_{index + 1}"],
                lambda preset_index=index: self._software_preset_tooltip_text(preset_index),
            )
            toolbar._tooltips.append(tooltip)
            self._tooltips.append(tooltip)
        return toolbar

    def _read_settings_tooltip_text(self) -> str:
        return self.READ_SETTINGS_TOOLTIP["zh" if self.language.startswith("zh") else "en"]

    def _save_slot_tooltip_text(self) -> str:
        text = self.tr("save_no_gui_settings_tooltip")
        if self.page_name == "full_mode":
            text += "\n" + self.tr("preset_slot_advanced_scope_warning")
        return text

    def _on_toolbar_slot_selected(self, slot: int | str) -> None:
        self.board_slot_var.set(str(slot))
        model = getattr(self, "_chip_settings_model", None)
        if model is not None:
            model.set_slot_selection(slot)

    def _sync_toolbar_slot(self) -> None:
        toolbar = getattr(self, "preset_toolbar", None)
        model = getattr(self, "_chip_settings_model", None)
        if toolbar is None or model is None:
            return
        slot = model.slot_selection
        if slot is None:
            slot = 0
        toolbar.slot_var.set(str(slot))
        self.board_slot_var.set(str(slot))

    def _select_awp192x_settings_model(self):
        store = settings_store_for_context(self.ctx)
        page_name_resolver = getattr(self, "_awp192x_settings_page_name", None)
        settings_page_name = page_name_resolver() if callable(page_name_resolver) else "awp1921_1923"
        model = store.select_device(
            connection_key_for_context(self.ctx, settings_page_name),
            int(self.ctx.state.awp192x_device_addr),
        )
        previous_key = getattr(self, "_chip_settings_device_key", None)
        self._chip_settings_model = model
        self._chip_settings_device_key = model.device_key

        storage_by_device = getattr(self.ctx.state, "_awp192x_waveform_storage_by_device", None)
        if storage_by_device is None:
            storage_by_device = {}
            setattr(self.ctx.state, "_awp192x_waveform_storage_by_device", storage_by_device)
        if model.device_key not in storage_by_device:
            storage_by_device[model.device_key] = (
                self.storage if previous_key is None else WaveformStorage()
            )
        self.storage = storage_by_device[model.device_key]
        waveform_page = getattr(self, "waveform_page", None)
        if waveform_page is not None:
            waveform_page.storage = self.storage

        if previous_key != model.device_key:
            self._syncing_chip_settings = True
            try:
                if model.shared:
                    self._apply_shared_settings(model.shared)
                elif getattr(self, "_default_shared_settings", None):
                    self._apply_shared_settings(self._default_shared_settings)
                    self._capture_shared_settings()
                if self.page_name == "full_mode":
                    if model.advanced:
                        self._apply_full_advanced_settings(model.advanced)
                    elif getattr(self, "_default_full_advanced_settings", None):
                        self._apply_full_advanced_settings(self._default_full_advanced_settings)
                        self._capture_full_advanced_settings()
            finally:
                self._syncing_chip_settings = False
        if model.slot_selection is None:
            model.set_slot_selection(0)
        self._sync_toolbar_slot()
        return model

    def _awp192x_settings_host(self):
        parent = getattr(self, "master", None)
        while parent is not None:
            if getattr(parent, "page_name", None) == "awp1921_1923":
                return parent
            parent = getattr(parent, "master", None)
        return None

    def _awp192x_settings_page_name(self) -> str:
        host = self._awp192x_settings_host()
        return str(getattr(host, "page_name", "awp1921_1923"))

    def _bind_awp192x_settings_connection_selector(self) -> None:
        host = self._awp192x_settings_host()
        if host is None or getattr(host, "_awp192x_settings_selector_bound", False):
            return
        for control in getattr(host, "_serial_selectors", ()):
            if control.get("page_name") != self._awp192x_settings_page_name():
                continue
            control["combo"].bind(
                "<<ComboboxSelected>>",
                self._on_awp192x_settings_connection_changed,
                add="+",
            )
            host._awp192x_settings_selector_bound = True
            return

    def _on_awp192x_settings_connection_changed(self, _event=None) -> None:
        host = self._awp192x_settings_host()
        if host is None:
            return

        def refresh_models() -> None:
            active = host.mode_pages.get(host.current_mode)
            capture_shared = getattr(active, "_capture_shared_settings", None)
            if callable(capture_shared):
                capture_shared()
                if getattr(active, "page_name", None) == "full_mode":
                    capture_advanced = getattr(active, "_capture_full_advanced_settings", None)
                    if callable(capture_advanced):
                        capture_advanced()
            for mode_page in tuple(dict.values(host.mode_pages)):
                reset_legend = getattr(mode_page, "_reset_adc_legend_lifecycle", None)
                if callable(reset_legend):
                    reset_legend()
                select_model = getattr(mode_page, "_select_awp192x_settings_model", None)
                if callable(select_model):
                    select_model()

        try:
            self.after_idle(refresh_models)
        except tk.TclError:
            pass

    def _initialize_awp192x_settings_model(self) -> None:
        self._syncing_chip_settings = True
        try:
            self._default_shared_settings = self._capture_shared_settings()
            if self.page_name == "full_mode":
                self._default_full_advanced_settings = self._capture_full_advanced_settings()
        finally:
            self._syncing_chip_settings = False
        model = self._select_awp192x_settings_model()
        if not model.shared:
            self._capture_shared_settings()
        if self.page_name == "full_mode" and not model.advanced:
            self._capture_full_advanced_settings()
        self._bind_host_address_changes()
        self._bind_awp192x_settings_connection_selector()

    def _bind_host_address_changes(self) -> None:
        if getattr(self, "_host_address_var", None) is not None:
            return
        parent = getattr(self, "master", None)
        while parent is not None and not hasattr(parent, "addr_var"):
            parent = getattr(parent, "master", None)
        address_var = getattr(parent, "addr_var", None)
        if address_var is None or not hasattr(address_var, "trace_add"):
            return
        self._host_address_var = address_var
        self._host_address_trace_id = address_var.trace_add(
            "write",
            lambda *_: self._queue_device_model_sync(),
        )

    def _queue_device_model_sync(self) -> None:
        current_address = getattr(self.ctx.state, "awp192x_device_addr", None)
        previous_address = getattr(self, "_adc_legend_target_addr", current_address)
        if current_address != previous_address:
            self._adc_legend_target_addr = current_address
            self._reset_adc_legend_lifecycle()
        try:
            self.after_idle(self._sync_awp192x_settings_from_model)
        except tk.TclError:
            pass

    def _attach_shared_settings_traces(self) -> None:
        variables = [
            self.max_voltage_var,
            self.duration_var,
            self.press_threshold_var,
            self.release_threshold_var,
            self.adc_average_time_var,
            self.adc_ovv_time_var,
            self.button_adc_pol_var,
            self.button_adc_gain_var,
            self.press_repeat_count_var,
            self.release_repeat_count_var,
            self.play_repeat_count_var,
            self.press_waveform_var,
            self.release_waveform_var,
            self.play_waveform_var,
            self.endless_play_var,
        ]
        if self.page_name == "full_mode":
            variables.extend(
                [
                    self.adc_threshold_h_var,
                    self.adc_threshold_l_var,
                    self.adc_gain_var,
                    self.adc_pol_var,
                    *self.full_event_enable_vars.values(),
                    *self.full_event_endless_vars.values(),
                    *self.full_event_repeat_vars.values(),
                    *self.full_event_wave_vars.values(),
                    *self.serial_wave_vars,
                    *self.serial_loop_vars,
                ]
            )
        waveform_page = getattr(self, "waveform_page", None)
        if waveform_page is not None:
            variables.extend([*waveform_page.serial_slice_vars, *waveform_page.serial_repeat_vars])
        for variable in variables:
            variable.trace_add("write", lambda *_: self._on_shared_settings_changed())
            if self.page_name == "button_mode":
                variable.trace_add("write", lambda *_: self._on_button_progress_inputs_changed())
        if self.page_name == "full_mode":
            advanced_variables = [
                self.adc_mode_var,
                *self.adc_trigger_vars.values(),
                self.full_dac_mode_var,
                self.full_slow_up_var,
                self.full_slow_down_var,
                self.full_half_cycle_var,
                self.full_phase_var,
                self.full_data_depth_var,
                *self.int_mask_vars.values(),
                self.fifo_depth_var,
                self.str_auto_addr_var,
                self.dis_plt_var,
                self.auto_end_var,
                self.auto_zero_var,
                self.reg_lock_var,
                self.clk_sync_var,
                self.clk_out_var,
                self.sync_play_var,
                self.ram_address_var,
            ]
            for variable in advanced_variables:
                variable.trace_add("write", lambda *_: self._on_full_advanced_settings_changed())

    def _on_shared_settings_changed(self) -> None:
        if getattr(self, "_syncing_chip_settings", False) or getattr(self, "_reading_current_settings", False):
            return
        model = getattr(self, "_chip_settings_model", None)
        if model is None:
            return
        self._capture_shared_settings()
        if self.page_name == "full_mode":
            self._capture_full_advanced_settings()

    def _on_full_advanced_settings_changed(self) -> None:
        if getattr(self, "_syncing_chip_settings", False) or getattr(self, "_reading_current_settings", False):
            return
        self._capture_full_advanced_settings()

    @staticmethod
    def _option_code(label: str, options: tuple[tuple[str, int], ...], default: int = 0) -> int:
        return next((int(code) for text, code in options if text == label), int(default))

    def _capture_shared_settings(self) -> dict[str, Any]:
        model = getattr(self, "_chip_settings_model", None)
        try:
            output = next(code for label, _voltage, code in self.amp_options if label == self.max_voltage_var.get())
            if self.page_name == "full_mode":
                thresholds = {
                    "press": float(self.adc_threshold_h_var.get()),
                    "release": float(self.adc_threshold_l_var.get()),
                }
                polarity = 1 if self.adc_pol_var.get() == "OUT-" else 0
                adc_range = 1 if self.adc_gain_var.get() == "0-19V" else 0
                press_key, release_key = "adc_over_h_posedge", "adc_over_l_posedge"
                press_action = {
                    "repeat": int(self.full_event_repeat_vars[press_key].get(), 0),
                    "wave": waveform_number(self.full_event_wave_vars[press_key].get()),
                    "endless": bool(self.full_event_endless_vars[press_key].get()),
                    "enabled": bool(self.full_event_enable_vars[press_key].get()),
                }
                release_action = {
                    "repeat": int(self.full_event_repeat_vars[release_key].get(), 0),
                    "wave": waveform_number(self.full_event_wave_vars[release_key].get()),
                    "endless": bool(self.full_event_endless_vars[release_key].get()),
                    "enabled": bool(self.full_event_enable_vars[release_key].get()),
                }
                serials = [
                    {"wave": waveform_number(wave.get()), "repeat": int(loop.get(), 0)}
                    for wave, loop in zip(self.serial_wave_vars, self.serial_loop_vars)
                ]
                average_label = self.adc_average_time_var.get()
                ovv_label = self.adc_ovv_time_var.get()
            else:
                thresholds = {
                    "press": float(self.press_threshold_var.get()),
                    "release": float(self.release_threshold_var.get()),
                }
                polarity = 1 if self.button_adc_pol_var.get() == "OUT-" else 0
                adc_range = 1 if self.button_adc_gain_var.get() == "0-19V" else 0
                press_action = {
                    "repeat": int(self.press_repeat_count_var.get(), 0),
                    "wave": waveform_number(self.press_waveform_var.get()),
                    "endless": bool(model.shared.get("press_action", {}).get("endless", False)) if model is not None else False,
                    "enabled": True,
                }
                release_action = {
                    "repeat": int(self.release_repeat_count_var.get(), 0),
                    "wave": waveform_number(self.release_waveform_var.get()),
                    "endless": bool(model.shared.get("release_action", {}).get("endless", False)) if model is not None else False,
                    "enabled": False,
                }
                serial_page = getattr(self, "waveform_page", None)
                if serial_page is not None:
                    serials = [
                        {"wave": waveform_number(wave.get()), "repeat": int(loop.get(), 0)}
                        for wave, loop in zip(serial_page.serial_slice_vars, serial_page.serial_repeat_vars)
                    ]
                else:
                    serials = model.shared.get("serial_waveforms", []) if model is not None else []
                average_label = self.adc_average_time_var.get()
                ovv_label = self.adc_ovv_time_var.get()
            manual = {
                "repeat": int(self.play_repeat_count_var.get(), 0),
                "wave": waveform_number(self.play_waveform_var.get()),
                "endless": bool(self.endless_play_var.get()),
            }
            average = self._option_code(average_label, self.ADC_AVERAGE_OPTIONS)
            ovv = self._option_code(ovv_label, self.ADC_OVV_OPTIONS)
            shared = {
                "output_amplitude": int(output),
                "point_duration": int(self.duration_var.get(), 0),
                "thresholds": thresholds,
                "filter_settings": {"average": average, "ovv": ovv},
                "polarity": polarity,
                "range": adc_range,
                "press_action": press_action,
                "release_action": release_action,
                "manual_playback": manual,
                "serial_waveforms": serials,
            }
        except (AttributeError, KeyError, StopIteration, TypeError, ValueError, tk.TclError):
            return {}
        if model is not None:
            model.update_shared({key: shared[key] for key in SYNC_FIELDS_192X})
        return shared

    def _apply_shared_settings(self, shared: dict[str, Any]) -> None:
        if not shared:
            return
        self._syncing_chip_settings = True
        try:
            output = int(shared.get("output_amplitude", self._current_max_output_reg_value()))
            label = next(
                (label for label, _voltage, code in self.amp_options if code == output),
                self.max_voltage_var.get(),
            )
            self.max_voltage_var.set(label)
            self.duration_var.set(str(shared.get("point_duration", self.ctx.state.single_point_duration_us)))
            thresholds = shared.get("thresholds", {})
            press_threshold = str(thresholds.get("press", self.press_threshold_var.get()))
            release_threshold = str(thresholds.get("release", self.release_threshold_var.get()))
            self.press_threshold_var.set(press_threshold)
            self.release_threshold_var.set(release_threshold)
            filters = shared.get("filter_settings", {})
            average = int(filters.get("average", 0))
            ovv = int(filters.get("ovv", 0))
            self.adc_average_time_var.set(self._adc_average_label(average))
            self.adc_ovv_time_var.set(self._adc_ovv_label(ovv))
            polarity = "OUT-" if int(shared.get("polarity", 0)) else "OUT+"
            adc_range = "0-19V" if int(shared.get("range", 0)) else "0-100V"
            self.button_adc_pol_var.set(polarity)
            self.button_adc_gain_var.set(adc_range)
            press = dict(shared.get("press_action", {}))
            release = dict(shared.get("release_action", {}))
            manual = dict(shared.get("manual_playback", {}))
            self.press_repeat_count_var.set(str(press.get("repeat", 1)))
            self.release_repeat_count_var.set(str(release.get("repeat", 1)))
            self.press_waveform_var.set(waveform_label(int(press.get("wave", 0))))
            self.release_waveform_var.set(waveform_label(int(release.get("wave", 0))))
            self.play_repeat_count_var.set(str(manual.get("repeat", 1)))
            self.play_waveform_var.set(waveform_label(int(manual.get("wave", 0x1F)), full=self.page_name == "full_mode"))
            self.endless_play_var.set(bool(manual.get("endless", False)))

            if self.page_name == "full_mode":
                self.adc_threshold_h_var.set(press_threshold)
                self.adc_threshold_l_var.set(release_threshold)
                self.adc_pol_var.set(polarity)
                self.adc_gain_var.set(adc_range)
                for key, action in (("adc_over_h_posedge", press), ("adc_over_l_posedge", release)):
                    self.full_event_wave_vars[key].set(waveform_label(int(action.get("wave", 0)), full=True))
                    self.full_event_repeat_vars[key].set(str(action.get("repeat", 1)))
                    self.full_event_endless_vars[key].set(bool(action.get("endless", False)))
                    self.full_event_enable_vars[key].set(bool(action.get("enabled", key == "adc_over_h_posedge")))
                for variables, values in (
                    (self.serial_wave_vars, shared.get("serial_waveforms", [])),
                    (self.serial_loop_vars, shared.get("serial_waveforms", [])),
                ):
                    for index, variable in enumerate(variables):
                        if index >= len(values):
                            continue
                        entry = values[index]
                        variable.set(
                            self._format_serial_wave(int(entry.get("wave", 0x1F)))
                            if variables is self.serial_wave_vars
                            else str(entry.get("repeat", 1))
                        )
                self._set_full_shared_event_enable_defaults()
            else:
                serial_page = getattr(self, "waveform_page", None)
                if serial_page is not None:
                    for index, entry in enumerate(shared.get("serial_waveforms", [])):
                        if index >= len(serial_page.serial_slice_vars):
                            break
                        serial_page.serial_slice_vars[index].set(waveform_label(int(entry.get("wave", 0x1F))))
                        serial_page.serial_repeat_vars[index].set(str(entry.get("repeat", 1)))
            self._refresh_threshold_register_value_labels()
            self._update_period_hint(write_register=False)
        finally:
            self._syncing_chip_settings = False

    def _set_full_shared_event_enable_defaults(self) -> None:
        # Button Mode's press detector is represented by bit 15 on 0x0F. The
        # Full event table's independent enable bits remain Full-only state.
        return

    def _capture_full_advanced_settings(self) -> dict[str, Any]:
        try:
            return self._capture_full_advanced_settings_unchecked()
        except (AttributeError, KeyError, TypeError, ValueError, tk.TclError):
            # Full Mode has editable numeric fields. During typing, intermediate
            # values can be incomplete; keep the last valid model snapshot until
            # the field is valid again.
            return {}

    def _capture_full_advanced_settings_unchecked(self) -> dict[str, Any]:
        if self.page_name != "full_mode" or not hasattr(self, "adc_mode_var"):
            return {}
        event_details = {}
        for key, _reg, _source_bit in self.ADC_EVENT_CONFIGS:
            if key in {"adc_over_h_posedge", "adc_over_l_posedge"}:
                continue
            event_details[key] = {
                "enable": bool(self.full_event_enable_vars[key].get()),
                "endless": bool(self.full_event_endless_vars[key].get()),
                "repeat": int(self.full_event_repeat_vars[key].get(), 0),
                "wave": waveform_number(self.full_event_wave_vars[key].get()),
            }
        triggers = {key: bool(value.get()) for key, value in self.adc_trigger_vars.items()}
        waveform_editor = self._capture_full_waveform_editor_settings()
        extra = {
            "adc_mode": self._option_code(self.adc_mode_var.get(), (("Direct", 0), ("INPT", 1), ("AUTO", 2))),
            "adc_triggers": triggers,
            "adc_config_fixed_bits": int(getattr(self, "_full_adc_config_fixed_bits", 0)),
            "full_events": event_details,
            "full_event_action_flags": {
                "press_enable": bool(self.full_event_enable_vars["adc_over_h_posedge"].get()),
                "release_enable": bool(self.full_event_enable_vars["adc_over_l_posedge"].get()),
                "press_endless": bool(self.full_event_endless_vars["adc_over_h_posedge"].get()),
                "release_endless": bool(self.full_event_endless_vars["adc_over_l_posedge"].get()),
            },
            "dac_mode": self._option_code(self.full_dac_mode_var.get(), self.DAC_MODE_OPTIONS),
            "slow_up": self._option_code(self.full_slow_up_var.get(), self.SLOW_TIME_OPTIONS),
            "slow_down": self._option_code(self.full_slow_down_var.get(), self.SLOW_TIME_OPTIONS),
            "half_cycle": bool(self.full_half_cycle_var.get()),
            "phase": int(self.full_phase_var.get(), 0),
            "data_depth": self._option_code(self.full_data_depth_var.get(), self.DATA_DEPTH_OPTIONS),
            "interrupt_mask": {key: bool(value.get()) for key, value in self.int_mask_vars.items()},
            "fifo_depth": self._option_code(self.fifo_depth_var.get(), self.FIFO_DEPTH_OPTIONS),
            "global_config": {
                "str_auto_addr": bool(self.str_auto_addr_var.get()),
                "dis_plt": bool(self.dis_plt_var.get()),
                "auto_end": bool(self.auto_end_var.get()),
                "auto_zero": bool(self.auto_zero_var.get()),
                "reg_lock": self._option_code(self.reg_lock_var.get(), self.REG_LOCK_OPTIONS),
                "clk_sync": bool(self.clk_sync_var.get()),
                "clk_out": bool(self.clk_out_var.get()),
                "sync_play": bool(self.sync_play_var.get()),
            },
            "ram_address": str(self.ram_address_var.get()),
            "waveform_editor": waveform_editor,
        }
        model = getattr(self, "_chip_settings_model", None)
        if model is not None:
            model.update_advanced(extra)
            model.advanced["slot_blocked"] = self._full_type3_slot_is_blocked(extra)
            model.advanced["slot_omitted"] = bool(event_details or extra.get("interrupt_mask") or extra.get("global_config"))
        return extra

    @staticmethod
    def _full_type3_slot_is_blocked(advanced: dict[str, Any]) -> bool:
        expected_triggers = {key: key == "adc_over_h_posedge" for key in advanced.get("adc_triggers", {})}
        expected_fixed_bits = 0x0001
        flags = advanced.get("full_event_action_flags", {})
        return bool(
            advanced.get("adc_mode", 0) != 0
            or advanced.get("adc_triggers") != expected_triggers
            or advanced.get("adc_config_fixed_bits", 0) != expected_fixed_bits
            or not flags.get("press_enable", True)
            or flags.get("release_enable", False)
            or flags.get("press_endless", False)
            or flags.get("release_endless", False)
        )

    def _apply_full_advanced_settings(self, advanced: dict[str, Any]) -> None:
        if self.page_name != "full_mode" or not hasattr(self, "adc_mode_var"):
            return
        self._syncing_chip_settings = True
        try:
            mode = int(advanced.get("adc_mode", 0))
            self.adc_mode_var.set({0: "Direct", 1: "INPT", 2: "AUTO"}.get(mode, "Direct"))
            for key, value in advanced.get("adc_triggers", {}).items():
                if key in self.adc_trigger_vars:
                    self.adc_trigger_vars[key].set(bool(value))
            self._full_adc_config_fixed_bits = int(advanced.get("adc_config_fixed_bits", 0))
            for key, event in advanced.get("full_events", {}).items():
                if key not in self.full_event_enable_vars:
                    continue
                self.full_event_enable_vars[key].set(bool(event.get("enable", False)))
                self.full_event_endless_vars[key].set(bool(event.get("endless", False)))
                self.full_event_repeat_vars[key].set(str(event.get("repeat", 1)))
                self.full_event_wave_vars[key].set(waveform_label(int(event.get("wave", 0x1F)), full=True))
            flags = advanced.get("full_event_action_flags", {})
            for key, name in (("adc_over_h_posedge", "press"), ("adc_over_l_posedge", "release")):
                if f"{name}_enable" in flags:
                    self.full_event_enable_vars[key].set(bool(flags[f"{name}_enable"]))
                if f"{name}_endless" in flags:
                    self.full_event_endless_vars[key].set(bool(flags[f"{name}_endless"]))
            self.full_dac_mode_var.set(self._option_label(self.DAC_MODE_OPTIONS, advanced.get("dac_mode", 2)))
            self.full_slow_up_var.set(self._option_label(self.SLOW_TIME_OPTIONS, advanced.get("slow_up", 0)))
            self.full_slow_down_var.set(self._option_label(self.SLOW_TIME_OPTIONS, advanced.get("slow_down", 0)))
            self.full_half_cycle_var.set(bool(advanced.get("half_cycle", False)))
            self.full_phase_var.set(str(advanced.get("phase", 0)))
            self.full_data_depth_var.set(self._option_label(self.DATA_DEPTH_OPTIONS, advanced.get("data_depth", 0)))
            for key, value in advanced.get("interrupt_mask", {}).items():
                if key in self.int_mask_vars:
                    self.int_mask_vars[key].set(bool(value))
            self.fifo_depth_var.set(self._option_label(self.FIFO_DEPTH_OPTIONS, advanced.get("fifo_depth", 0)))
            global_config = advanced.get("global_config", {})
            for name in ("str_auto_addr", "dis_plt", "auto_end", "auto_zero", "clk_sync", "clk_out", "sync_play"):
                if name in global_config:
                    getattr(self, f"{name}_var").set(bool(global_config[name]))
            self.reg_lock_var.set(self._option_label(self.REG_LOCK_OPTIONS, global_config.get("reg_lock", 0)))
            self.ram_address_var.set(str(advanced.get("ram_address", "0x0000")))
            editor_state = advanced.get("waveform_editor")
            if isinstance(editor_state, dict) and getattr(self, "waveform_page", None) is not None:
                self._apply_full_waveform_editor_settings(editor_state)
        finally:
            self._syncing_chip_settings = False

    def _capture_full_waveform_editor_settings(self) -> dict[str, Any] | None:
        page = getattr(self, "waveform_page", None)
        if page is None:
            model = getattr(self, "_chip_settings_model", None)
            return model.advanced.get("waveform_editor") if model is not None else None
        try:
            return {
                "waveform_type": str(page.waveform_type_var.get()),
                "slice": int(page.slice_var.get()),
                "points": int(page.points_var.get(), 0),
                "start_phase": str(page.start_phase_var.get()),
                "end_phase": str(page.end_phase_var.get()),
                "offset": str(page.offset_var.get()),
                "amplitude": str(page.voltage_amplitude_var.get()),
                "sinc_positive": str(page.sinc_positive_voltage_var.get()),
                "sinc_negative": str(page.sinc_negative_voltage_var.get()),
                "sinc_points": [
                    int(page.sinc_points1_var.get(), 0),
                    int(page.sinc_points2_var.get(), 0),
                    int(page.sinc_points3_var.get(), 0),
                ],
                "start_addr": str(page.start_addr_var.get()),
                "fill_zero_tail": bool(page.fill_zero_tail_var.get()),
            }
        except (AttributeError, TypeError, ValueError, tk.TclError):
            return None

    def _apply_full_waveform_editor_settings(self, settings: dict[str, Any]) -> None:
        page = getattr(self, "waveform_page", None)
        if page is None:
            return
        try:
            page.waveform_type_var.set(str(settings.get("waveform_type", "sine")))
            page.slice_var.set(int(settings.get("slice", 0)))
            page.points_var.set(str(settings.get("points", 256)))
            page.start_phase_var.set(str(settings.get("start_phase", "0")))
            page.end_phase_var.set(str(settings.get("end_phase", "360")))
            page.offset_var.set(str(settings.get("offset", "0")))
            page.voltage_amplitude_var.set(str(settings.get("amplitude", "95")))
            page.sinc_positive_voltage_var.set(str(settings.get("sinc_positive", "95")))
            page.sinc_negative_voltage_var.set(str(settings.get("sinc_negative", "-95")))
            sinc_points = settings.get("sinc_points", (85, 86, 85))
            page.sinc_points1_var.set(str(sinc_points[0]))
            page.sinc_points2_var.set(str(sinc_points[1]))
            page.sinc_points3_var.set(str(sinc_points[2]))
            page.start_addr_var.set(str(settings.get("start_addr", "0x0002")))
            page.fill_zero_tail_var.set(bool(settings.get("fill_zero_tail", False)))
            page._update_end_addr()
        except (AttributeError, IndexError, TypeError, ValueError, tk.TclError):
            return

    def _on_full_editor_serial_changed(self, page: WaveformGeneratorPage) -> None:
        if getattr(self, "_syncing_chip_settings", False):
            return
        self._syncing_chip_settings = True
        try:
            for index, (wave, repeat) in enumerate(zip(page.serial_slice_vars, page.serial_repeat_vars)):
                if index >= len(self.serial_wave_vars):
                    break
                self.serial_wave_vars[index].set(wave.get())
                self.serial_loop_vars[index].set(repeat.get())
        finally:
            self._syncing_chip_settings = False
        self._capture_shared_settings()
        self._capture_full_advanced_settings()

    @staticmethod
    def _option_label(options: tuple[tuple[str, int], ...], code: Any) -> str:
        value = int(code)
        return next((label for label, item_code in options if int(item_code) == value), options[0][0])

    def _sync_awp192x_settings_from_model(self) -> None:
        model = self._select_awp192x_settings_model()
        if model.shared:
            self._apply_shared_settings(model.shared)
        if self.page_name == "full_mode" and model.advanced:
            self._apply_full_advanced_settings(model.advanced)

    def _mark_button_runtime_dirty(self) -> None:
        if (
            self.page_name == "button_mode"
            and not getattr(self, "_reading_current_settings", False)
            and not getattr(self, "_syncing_chip_settings", False)
        ):
            self._button_runtime_dirty = True

    def _on_button_runtime_waveform_changed(self) -> None:
        self._mark_button_runtime_dirty()
        self._update_period_hint(write_register=False)

    def _on_button_runtime_duration_changed(self) -> None:
        self._mark_button_runtime_dirty()
        self._update_period_hint(write_register=False)

    def _on_button_runtime_playback_changed(self) -> None:
        self._mark_button_runtime_dirty()

    def _on_button_runtime_threshold_changed(self) -> None:
        self._mark_button_runtime_dirty()
        self._refresh_threshold_register_value_labels()

    def _awp192x_chip_host(self) -> Any | None:
        parent = getattr(self, "master", None)
        while parent is not None:
            if callable(getattr(parent, "_step_progress_context", None)):
                return parent
            parent = getattr(parent, "master", None)
        return None

    def _mount_button_step_progress(self) -> None:
        if self.page_name != "button_mode":
            return
        host = self._awp192x_chip_host()
        if host is None:
            return
        self.mount_step_progress(
            host=host,
            steps=(
                StepSpec("waveform", 1, "step_done_192x_waveform"),
                StepSpec("config", 2, "step_done_192x_config"),
                StepSpec("detection", 3, "step_done_192x_detection"),
            ),
            context=host._step_progress_context,
        )
        self._sync_button_step_progress_params()
        self._reconcile_rom_waveform_progress()

    def _button_active_waveform_requirements(self) -> tuple[int, ...] | None:
        waveform_page = getattr(self, "waveform_page", None)
        if waveform_page is None:
            return None
        try:
            actions = (
                waveform_number(self.press_waveform_var.get()),
                waveform_number(self.release_waveform_var.get()),
                waveform_number(self.play_waveform_var.get()),
            )
            serial_values = {0x17: 0, 0x18: 0, 0x19: 0, 0x1A: 0}
            if self.SERIAL_WAVE_NUMBER in actions:
                serial_values.update(dict(waveform_page.build_serial_register_values()))
            return required_awp192x_button_waveform_slices(
                press_action_base=actions[0],
                release_action_base=actions[1],
                manual_play_reg=actions[2],
                serial_config_0x17=serial_values[0x17],
                serial_config_0x18=serial_values[0x18],
                serial_config_0x19=serial_values[0x19],
                serial_config_0x1A=serial_values[0x1A],
            )
        except (AttributeError, KeyError, TypeError, ValueError, tk.TclError):
            return None

    def _button_waveform_progress_fingerprint(self) -> tuple[Any, ...]:
        waveform_page = getattr(self, "waveform_page", None)
        editor = (
            waveform_page.progress_input_fingerprint()
            if waveform_page is not None and hasattr(waveform_page, "progress_input_fingerprint")
            else ()
        )
        requirements = self._button_active_waveform_requirements()
        return (
            editor,
            str(self.duration_var.get()),
            str(self.max_voltage_var.get()),
            ("invalid",) if requirements is None else tuple(requirements),
        )

    def _button_config_progress_fingerprint(self) -> tuple[Any, ...]:
        variables = (
            self.press_threshold_var,
            self.release_threshold_var,
            self.button_adc_gain_var,
            self.button_adc_pol_var,
            self.adc_average_time_var,
            self.adc_ovv_time_var,
            self.press_repeat_count_var,
            self.release_repeat_count_var,
            self.press_waveform_var,
            self.release_waveform_var,
            self.play_repeat_count_var,
            self.play_waveform_var,
            self.endless_play_var,
        )
        values: list[Any] = [variable.get() for variable in variables]
        waveform_page = getattr(self, "waveform_page", None)
        if waveform_page is not None:
            values.extend(variable.get() for variable in waveform_page.serial_slice_vars)
            values.extend(variable.get() for variable in waveform_page.serial_repeat_vars)
        return tuple(values)

    def _sync_button_step_progress_params(self) -> None:
        bar = getattr(self, "step_progress", None)
        if bar is None or self.page_name != "button_mode":
            return
        waveform = self._button_waveform_progress_fingerprint()
        if getattr(self, "_button_waveform_progress_fingerprint_seen", None) != waveform:
            self._clear_button_waveform_slice_receipts()
            self._button_waveform_progress_fingerprint_seen = waveform
        config = self._button_config_progress_fingerprint()
        bar.update_params("waveform", {"settings": waveform})
        bar.update_params("config", {"settings": config})
        bar.update_params("detection", {"settings": (waveform, config)})

    def _clear_button_waveform_slice_receipts(self) -> None:
        self._button_waveform_slice_receipts: set[int] = set()
        self._button_waveform_receipt_token = None

    def _on_button_progress_inputs_changed(self) -> None:
        if (
            getattr(self, "_syncing_chip_settings", False)
            or getattr(self, "_reading_current_settings", False)
            or getattr(self, "_preparing", False)
        ):
            return
        self._sync_button_step_progress_params()
        if self._button_rom_reconcile_job is not None:
            return
        try:
            self._button_rom_reconcile_job = self.after_idle(self._run_button_rom_progress_reconcile)
        except tk.TclError:
            self._button_rom_reconcile_job = None

    def _run_button_rom_progress_reconcile(self) -> None:
        self._button_rom_reconcile_job = None
        self._reconcile_rom_waveform_progress()

    def _reconcile_rom_waveform_progress(self) -> None:
        if self.page_name != "button_mode" or self.step_progress is None:
            return
        if self._button_active_waveform_requirements() != ():
            return
        self._sync_button_step_progress_params()
        self._clear_button_waveform_slice_receipts()
        token = self.step_progress.begin("waveform")
        self._button_waveform_receipt_token = token
        self.step_progress.mark("waveform", "done", token)

    def _begin_button_progress_step(self, step_key: str) -> Any | None:
        if self.page_name != "button_mode" or self.step_progress is None:
            return None
        self._sync_button_step_progress_params()
        if step_key == "waveform":
            self._clear_button_waveform_slice_receipts()
        token = self.step_progress.begin(step_key)
        if step_key == "waveform":
            self._button_waveform_receipt_token = token
        event = {
            "waveform": "waveform",
            "config": "preset",
            "detection": "detection",
        }.get(step_key, "detection")
        _begin_status(self, event, progress_token=token)
        return token

    def _mark_button_progress_step(self, step_key: str, state: str, token: Any | None) -> None:
        if token is not None:
            _report_progress_status(self, token, state)
        if self.step_progress is not None and token is not None:
            self.step_progress.mark(step_key, state, token)

    def _begin_button_preset_progress(self) -> dict[str, Any]:
        if self.page_name != "button_mode" or self.step_progress is None:
            return {}
        self._sync_button_step_progress_params()
        self._clear_button_waveform_slice_receipts()
        tokens = {
            "waveform": self.step_progress.begin("waveform"),
            "config": self.step_progress.begin("config"),
        }
        self._button_waveform_receipt_token = tokens["waveform"]
        _begin_status(self, "waveform", progress_token=tokens["waveform"])
        _begin_status(self, "preset", progress_token=tokens["config"])
        return tokens

    def _finish_button_preset_progress(self, tokens: dict[str, Any] | None, *, success: bool) -> None:
        if self.step_progress is None or not tokens:
            return
        state = "done" if success else "error"
        for step_key in ("waveform", "config"):
            self._mark_button_progress_step(step_key, state, tokens.get(step_key))

    def _on_button_waveform_write_result(
        self,
        token: Any,
        outcome: str,
        _device_addr: int,
        slice_index: int,
    ) -> None:
        from core.status_bus import StatusToken

        if isinstance(token, StatusToken):
            if outcome == "done":
                _finish_status(self, token, success=True)
            elif outcome == "error":
                _finish_status(self, token, success=False, error="waveform write failed")
            elif outcome == "cancelled":
                bus = getattr(getattr(self, "ctx", None), "status_bus", None)
                if bus is not None:
                    bus.invalidate_context(token=token)
            return
        if token is None or self.step_progress is None:
            return
        if outcome in {"done", "error"}:
            self._sync_button_step_progress_params()
        if outcome == "done":
            requirements = self._button_active_waveform_requirements()
            if (
                requirements is None
                or not requirements
                or token != getattr(self, "_button_waveform_receipt_token", None)
                or int(slice_index) not in requirements
                or not self.step_progress.accepts(token)
            ):
                return
            receipts = getattr(self, "_button_waveform_slice_receipts", None)
            if receipts is None:
                receipts = set()
                self._button_waveform_slice_receipts = receipts
            receipts.add(int(slice_index))
            if set(requirements).issubset(receipts):
                self._mark_button_progress_step("waveform", "done", token)
            else:
                self._mark_button_progress_step("waveform", "running", token)
        elif outcome == "error":
            if (
                token == getattr(self, "_button_waveform_receipt_token", None)
                and self.step_progress.accepts(token)
            ):
                getattr(self, "_button_waveform_slice_receipts", set()).discard(int(slice_index))
            self._mark_button_progress_step("waveform", "error", token)
        elif outcome == "cancelled":
            bus = getattr(getattr(self, "ctx", None), "status_bus", None)
            if bus is not None:
                bus.invalidate_progress(token)
            self._mark_button_progress_step("waveform", "idle", token)

    def _begin_waveform_write_observation(self) -> Any:
        if self.page_name == "button_mode" and self.step_progress is not None:
            return self._begin_button_progress_step("waveform")
        return _begin_status(self, "waveform")

    def _add_entry(self, parent: tk.Misc, row: int, key: str, variable: tk.StringVar, unit: str = "") -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels[key] = label

        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=7)

        if unit:
            ttk.Label(parent, text=unit, style="Muted.TLabel").grid(row=row, column=2, sticky="w", padx=(8, 0))

    def _add_paired_entries(
        self,
        parent: tk.Misc,
        row: int,
        key1: str,
        variable1: tk.StringVar,
        key2: str,
        variable2: tk.StringVar,
        *,
        unit1: str = "",
        unit2: str = "",
        value_var1: tk.StringVar | None = None,
        value_var2: tk.StringVar | None = None,
    ) -> None:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=row, column=0, columnspan=6, sticky="ew", pady=7)
        frame.columnconfigure(0, weight=1)

        first = ttk.Frame(frame, style="Panel.TFrame")
        first.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        first.columnconfigure(1, weight=1)

        second = ttk.Frame(frame, style="Panel.TFrame")
        second.grid(row=1, column=0, sticky="ew")
        second.columnconfigure(1, weight=1)

        label1 = ttk.Label(first, style="Panel.TLabel")
        label1.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._labels[key1] = label1

        ttk.Entry(first, textvariable=variable1, width=8).grid(row=0, column=1, sticky="ew")
        if unit1:
            ttk.Label(first, text=unit1, style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=(6, 0))
        if value_var1 is not None:
            ttk.Label(first, textvariable=value_var1, style="Muted.TLabel", width=7).grid(
                row=0,
                column=3,
                sticky="w",
                padx=(8, 0),
            )

        label2 = ttk.Label(second, style="Panel.TLabel")
        label2.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._labels[key2] = label2

        ttk.Entry(second, textvariable=variable2, width=8).grid(row=0, column=1, sticky="ew")
        if unit2:
            ttk.Label(second, text=unit2, style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=(6, 0))
        if value_var2 is not None:
            ttk.Label(second, textvariable=value_var2, style="Muted.TLabel", width=7).grid(
                row=0,
                column=3,
                sticky="w",
                padx=(8, 0),
            )

    def _slice_options(self) -> list[str]:
        if self.page_name == "button_mode":
            return ["slice0", "0x1F"]
        return [f"slice{i}" for i in range(30)] + ["serial", "0x1F"]

    def _build_waveform_choice_notice(self, parent: tk.Misc, *, persistent: bool = False) -> None:
        self._waveform_choice_notice_persistent = bool(persistent)
        frame = ttk.Frame(parent, style="App.TFrame")
        frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(8, 0), pady=(3, 0))
        frame.columnconfigure(0, weight=1)
        label = ttk.Label(frame, style="Muted.TLabel", wraplength=640)
        label.grid(row=0, column=0, sticky="w")
        link = ttk.Button(
            frame,
            text=self.tr("r2c_full_mode_link"),
            style="Secondary.TButton",
            command=self._open_full_mode,
        )
        link.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._waveform_choice_notice_frame = frame
        self._waveform_choice_notice_label = label
        self._waveform_choice_notice_link = link
        if not persistent:
            frame.grid_remove()
        self._refresh_waveform_choice_labels()

    def _bind_waveform_choice(
        self,
        combo: ttk.Combobox,
        raw_variable: tk.Variable,
        choices: tuple[str, ...] | list[str],
        callback: Callable[[], None] | None = None,
        *,
        label_keys: dict[str, str] | None = None,
        display_labels: dict[str, str] | None = None,
        aliases: dict[str, str] | None = None,
    ) -> None:
        """Keep localized combo labels out of the raw waveform variables."""
        display_variable = tk.StringVar(master=combo, value="")
        binding = {
            "combo": combo,
            "raw": raw_variable,
            "display": display_variable,
            "choices": tuple(str(value) for value in choices),
            "callback": callback,
            "label_keys": dict(label_keys or {}),
            "display_labels": dict(display_labels or {}),
            "aliases": dict(aliases or {}),
        }
        self._waveform_choice_bindings.append(binding)

        def refresh(*_args: Any) -> None:
            self._refresh_waveform_choice_labels()

        trace_id = raw_variable.trace_add("write", refresh)
        self._waveform_choice_trace_ids.append((raw_variable, trace_id))

        def selected(_event: tk.Event | None = None) -> None:
            selected_label = display_variable.get()
            display_to_raw = {
                self._waveform_choice_display(binding, raw): raw
                for raw in binding["choices"]
            }
            raw = display_to_raw.get(selected_label)
            if raw is None:
                return
            raw_variable.set(raw)
            if callback is not None:
                callback()

        combo.configure(textvariable=display_variable)
        combo.bind("<<ComboboxSelected>>", selected, add="+")
        self._refresh_waveform_choice_labels()

    def _waveform_choice_display(self, binding: dict[str, Any], raw: str) -> str:
        label_key = binding["label_keys"].get(raw)
        if label_key:
            return self.tr(label_key)
        return binding["display_labels"].get(raw, raw)

    def _refresh_waveform_choice_labels(self) -> None:
        unsupported: list[str] = []
        colors = getattr(self.ctx, "colors", {})
        normal_style, unsupported_style = waveform_choice_styles(self, colors)
        for binding in tuple(getattr(self, "_waveform_choice_bindings", ())):
            combo = binding["combo"]
            try:
                if not combo.winfo_exists():
                    continue
                raw = str(binding["raw"].get())
                choices = binding["choices"]
                aliases = binding["aliases"]
                is_supported = raw in choices or raw in aliases
                display_raw = aliases.get(raw, raw)
                display_value = self._waveform_choice_display(binding, display_raw)
                display_values = [self._waveform_choice_display(binding, choice) for choice in choices]
                if not is_supported:
                    display_values.append(raw)
                    unsupported.append(raw)
                combo.configure(
                    values=tuple(display_values),
                    style=normal_style if is_supported else unsupported_style,
                )
                binding["display"].set(display_value)
            except (tk.TclError, AttributeError):
                continue

        notice_frame = getattr(self, "_waveform_choice_notice_frame", None)
        if notice_frame is None:
            return
        label = getattr(self, "_waveform_choice_notice_label", None)
        link = getattr(self, "_waveform_choice_notice_link", None)
        if link is not None:
            try:
                link.configure(text=self.tr("r2c_full_mode_link"))
            except tk.TclError:
                pass
        if unsupported:
            if label is not None:
                label.configure(text=self.tr("r2c_unsupported_waveform_notice", value=unsupported[0]))
            notice_frame.grid()
        elif getattr(self, "_waveform_choice_notice_persistent", False):
            if label is not None:
                label.configure(text=self.tr("r2c_more_waveforms_notice"))
            notice_frame.grid()
        else:
            notice_frame.grid_remove()

    def _open_full_mode(self) -> None:
        navigation = getattr(getattr(self.ctx, "app", None), "navigation", None)
        on_mode_clicked = getattr(navigation, "_on_mode_clicked", None)
        if callable(on_mode_clicked):
            on_mode_clicked("full_mode")

    def _add_repeat_row(
        self,
        parent: tk.Misc,
        row: int,
        key: str,
        count_var: tk.StringVar,
        waveform_var: tk.StringVar,
        *,
        include_endless: bool = False,
    ) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels[key] = label

        repeat_controls = ttk.Frame(parent, style="Panel.TFrame")
        repeat_controls.grid(row=row, column=1, columnspan=2, sticky="w", pady=7)

        entry = ttk.Entry(repeat_controls, textvariable=count_var, width=5)
        entry.grid(row=0, column=0, sticky="w")
        self._repeat_entries[key] = entry

        combo = ttk.Combobox(
            repeat_controls,
            textvariable=waveform_var,
            values=self._slice_options(),
            state="readonly",
            width=10,
        )
        combo.grid(row=0, column=1, sticky="w", padx=(8, 0))

        hover_widgets: list[tk.Misc] = [label, entry, combo]
        if include_endless:
            check = ttk.Checkbutton(repeat_controls, variable=self.endless_play_var)
            check.grid(row=0, column=2, sticky="w", padx=(8, 0))
            self._checks["endless_play"] = check
            hover_widgets.append(check)
        self._bind_repeat_playback_tooltip(hover_widgets, count_var, waveform_var)

    def _selected_slice_points(self, waveform_var: tk.StringVar) -> int | None:
        try:
            selected = waveform_var.get().strip().lower()
            if selected in ("0x1f", "31"):
                return 1024
            if selected == "serial":
                return self.waveform_page.effective_points()
            if selected.startswith("slice"):
                slice_index = int(selected.replace("slice", "", 1), 10)
            else:
                slice_index = int(selected, 0)
            if not 0 <= slice_index < WaveformStorage.MAX_SLICES:
                return None
        except (AttributeError, ValueError):
            return None

        waveform_type = getattr(self.waveform_page, "waveform_type_var", None)
        is_editing_serial = waveform_type is not None and waveform_type.get() == "serial"
        if not is_editing_serial and slice_index == int(self.waveform_page.slice_var.get()):
            return self.waveform_page.effective_points()
        item = self.storage.slices.get(slice_index)
        return item.points if item is not None else None

    def _repeat_playback_metrics(self, count_var: tk.StringVar, waveform_var: tk.StringVar) -> tuple[str, str]:
        try:
            count = int(count_var.get().strip(), 0)
            duration_us = int(self.duration_var.get().strip(), 0)
            if count < 1 or duration_us < 1:
                return "—", "—"
            points = self._selected_slice_points(waveform_var)
            if points is None:
                return "—", "—"
            total_us = count * points * duration_us
            time_text = f"{total_us / 1000:.3f} ms"
            hz = 1_000_000 / total_us
            freq_text = f"{hz / 1000:.3f} kHz" if hz >= 1000 else f"{hz:.3f} Hz"
            return time_text, freq_text
        except (AttributeError, ValueError, ZeroDivisionError):
            return "—", "—"

    def _bind_repeat_playback_tooltip(
        self,
        widgets: list[tk.Misc],
        count_var: tk.StringVar,
        waveform_var: tk.StringVar,
    ) -> None:
        def text(cv: tk.StringVar = count_var, wv: tk.StringVar = waveform_var) -> str:
            time_text, freq_text = self._repeat_playback_metrics(cv, wv)
            return self.tr("repeat_playback_hover_tip").format(time=time_text, freq=freq_text)

        for widget in widgets:
            self._tooltips.append(SimpleTooltip(widget, text))

    def _add_combo(self, parent: tk.Misc, row: int, key: str, variable: tk.StringVar, values: list[str]) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels[key] = label

        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=7)

    def refresh_language(self) -> None:
        super().refresh_language()
        self._refresh_waveform_choice_labels()
        toolbar = getattr(self, "preset_toolbar", None)
        if toolbar is not None:
            toolbar.refresh_language()
        for key, label in self._labels.items():
            label.configure(text=self.tr(key))
        for key, btn in self._buttons.items():
            if toolbar is not None and btn in toolbar.buttons.values():
                continue
            btn.configure(text=self.tr(key))
        for key, check in self._checks.items():
            check.configure(text=self.tr(key))
        if hasattr(self, "endless_hint_label"):
            self.endless_hint_label.configure(text=self.tr("endless_play_hint"))
        if hasattr(self, "waveform_page"):
            self.waveform_page.refresh_language()
        self._update_period_hint()
        self._refresh_adc_read_buttons()
        self._refresh_button_view()
        self._refresh_sense_legend()
        if not self._awp192x_target_is_connected():
            self._reset_adc_legend_lifecycle()
        attach_glossary_tooltips(self, self.tr, self._tooltips)

    def refresh_theme(self) -> None:
        super().refresh_theme()
        self._refresh_waveform_choice_labels()
        if hasattr(self, "waveform_page"):
            self.waveform_page.refresh_theme()
        legend = getattr(self, "adc_legend", None)
        if legend is not None:
            legend.refresh_style(self.colors)
        self._refresh_sense_legend()
        self._refresh_button_adc_legend_rules()
        self._refresh_preset_tooltip_theme()
        self._refresh_button_view()

    def _queue_button_adc_split_reflow(self, _event: tk.Event | None = None) -> None:
        if getattr(self, "_button_adc_split_job", None) is not None:
            return
        try:
            self._button_adc_split_job = self.after_idle(self._reflow_button_adc_split)
        except tk.TclError:
            self._button_adc_split_job = None

    def _reflow_button_adc_split(self) -> None:
        self._button_adc_split_job = None
        split = getattr(self, "_button_adc_split", None)
        panels = getattr(self, "_button_adc_panels", ())
        if split is None or len(panels) != 2:
            return
        try:
            width = int(split.winfo_width())
            if width <= 1:
                return
            legend, settings = panels
            previous = self._button_adc_split_signature
            if previous is True:
                side_by_side = width >= 960
            elif previous is False:
                side_by_side = width >= 1160
            else:
                side_by_side = width >= 1080
            if side_by_side == previous:
                return
            self._button_adc_split_signature = side_by_side
            if side_by_side:
                split.columnconfigure(0, weight=2, uniform="adc_sections")
                split.columnconfigure(1, weight=3, uniform="adc_sections")
                legend.grid_configure(row=0, column=0, columnspan=1, sticky="nsew", padx=(0, 10), pady=0)
                settings.grid_configure(row=0, column=1, columnspan=1, sticky="nsew", padx=0, pady=0)
            else:
                split.columnconfigure(0, weight=1, uniform="")
                split.columnconfigure(1, weight=0, uniform="")
                legend.grid_configure(row=0, column=0, columnspan=2, sticky="ew", padx=0, pady=(0, 8))
                settings.grid_configure(row=1, column=0, columnspan=2, sticky="ew", padx=0, pady=0)
        except tk.TclError:
            return

    def _has_pending_layout_work(self) -> bool:
        if any(getattr(self, name, None) is not None for name in
               ("_button_adc_diagram_job", "_button_adc_split_job")):
            return True
        waveform_page = getattr(self, "waveform_page", None)
        return any(getattr(waveform_page, name, None) is not None for name in
                   ("_preview_job", "_button_layout_job"))

    def _cleanup_waveform_choice_work(self) -> None:
        for variable, trace_id in self._waveform_choice_trace_ids:
            try:
                variable.trace_remove("write", trace_id)
            except (AttributeError, tk.TclError):
                pass
        self._waveform_choice_trace_ids.clear()
        for name in ("_button_adc_diagram_job", "_button_adc_split_job"):
            job = getattr(self, name, None)
            setattr(self, name, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        waveform_page = getattr(self, "waveform_page", None)
        cancel_preview = getattr(waveform_page, "_cancel_pending_preview", None)
        if callable(cancel_preview):
            cancel_preview()

    def _on_button_page_destroy(self, event) -> None:
        if getattr(event, "widget", None) is self:
            self._cleanup_waveform_choice_work()

    def destroy(self) -> None:
        self._cleanup_waveform_choice_work()
        self._button_adc_generation += 1
        if hasattr(self, "_sense_live_generation"):
            self._reset_adc_legend_lifecycle()
        legend = getattr(self, "adc_legend", None)
        if legend is not None:
            legend.dispose()
        address_var = getattr(self, "_host_address_var", None)
        trace_id = getattr(self, "_host_address_trace_id", None)
        if address_var is not None and trace_id is not None:
            try:
                address_var.trace_remove("write", trace_id)
            except (tk.TclError, AttributeError):
                pass
        if self.page_name == "button_mode":
            self._close_button_adc_graph()
        self._hide_tooltips()
        if self._adc_realtime_job is not None:
            try:
                self.after_cancel(self._adc_realtime_job)
            except Exception:
                pass
            self._adc_realtime_job = None
        self._adc_realtime_active = False
        try:
            self.ctx.serial.remove_log_listener(self._handle_sense_status_log)
        except Exception:
            pass
        super().destroy()

    def _bind_action_tooltip(self, button: ttk.Button, button_key: str) -> None:
        tooltip_key = self.ACTION_TOOLTIP_KEYS[button_key]
        self._tooltips.append(SimpleTooltip(button, lambda key=tooltip_key: self.tr(key)))

    def _hide_tooltips(self) -> None:
        for tooltip in self._tooltips:
            try:
                tooltip._hide()
            except tk.TclError:
                pass

    def _hide_preset_tooltip(self) -> None:
        self._hide_tooltips()

    def _refresh_preset_tooltip_theme(self) -> None:
        self._hide_tooltips()

    def export_user_preset_script(self) -> None:
        try:
            content = format_register_script(self._build_current_register_script_operations())
            content = self._prepend_page_settings_metadata(content)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return

        path = filedialog.asksaveasfilename(
            title=self.tr("button_mode_export_user_preset"),
            defaultextension=".txt",
            initialfile="button_mode_register_script.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        status_token = _begin_status(self, "preset")
        try:
            Path(path).write_text(content, encoding="utf-8")
        except Exception as exc:
            _finish_status(self, status_token, success=False, error=str(exc))
            messagebox.showerror(self.tr("error"), str(exc))
            return

        _finish_status(self, status_token, success=True)
        messagebox.showinfo(self.tr("info"), self.tr("register_script_export_success"))

    def import_user_preset_script(self) -> None:
        path = filedialog.askopenfilename(
            title=self.tr("button_mode_import_user_preset"),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            content = Path(path).read_text(encoding="utf-8-sig")
            metadata = self._parse_page_settings_metadata(content)
            operations = parse_register_script(content)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return

        if not operations:
            messagebox.showerror(self.tr("error"), self.tr("register_script_empty"))
            return
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return

        status_token = _begin_status(self, "preset")
        self._set_register_script_buttons_state("disabled")

        def worker() -> None:
            error = self._execute_register_script_operations(operations)
            try:
                self.after(0, self._finish_register_script_import, error, operations, metadata, status_token)
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_register_script_import(
        self,
        error: str | None,
        operations: list[RegisterScriptOperation] | None = None,
        metadata: dict[str, Any] | None = None,
        status_token: Any = None,
    ) -> None:
        self._set_register_script_buttons_state("normal")
        if error:
            _finish_status(self, status_token, success=False, error=error)
            messagebox.showerror(self.tr("error"), error)
            return
        try:
            if operations:
                self._apply_register_script_waveform_page_state(operations)
            if metadata:
                self._apply_page_settings_metadata(metadata)
        except Exception as exc:
            _finish_status(self, status_token, success=False, error=str(exc))
            messagebox.showerror(self.tr("error"), str(exc))
            return
        _finish_status(self, status_token, success=True)
        messagebox.showinfo(self.tr("info"), self.tr("register_script_import_success"))
        self.read_current_settings(show_success=False)

    def _prepend_page_settings_metadata(self, content: str) -> str:
        model = getattr(self, "_chip_settings_model", None)
        if model is None:
            return content
        waveforms = []
        for item in self.storage.slices.values():
            recipe = item.power_on_recipe
            recipe_data = None
            if isinstance(recipe, (Awp192xWaveformRecipe, Awp192xSineWaveformRecipe)):
                recipe_data = {
                    "kind": "sine" if isinstance(recipe, Awp192xSineWaveformRecipe) else "sinc",
                    **asdict(recipe),
                }
            waveforms.append({
                "index": item.index,
                "start_addr": item.start_addr,
                "end_addr": item.end_addr,
                "points": item.points,
                "waveform_type": item.waveform_type,
                "values": list(item.values),
                "recipe": recipe_data,
            })
        payload = {
            "format": "AWP192X_PAGE_SETTINGS",
            "version": 1,
            "mode": self.page_name,
            "shared": model.shared,
            "advanced": model.advanced,
            "waveforms": waveforms,
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        metadata_line = f"# {AWP192X_PAGE_SETTINGS_TAG} {base64.urlsafe_b64encode(raw).decode('ascii')}\n"
        return metadata_line + content

    @staticmethod
    def _parse_page_settings_metadata(content: str) -> dict[str, Any] | None:
        prefix = f"# {AWP192X_PAGE_SETTINGS_TAG} "
        for line in content.splitlines():
            if not line.startswith(prefix):
                continue
            try:
                raw = base64.urlsafe_b64decode(line[len(prefix) :].strip().encode("ascii"))
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("invalid AWP192x page settings metadata") from exc
            if (
                not isinstance(payload, dict)
                or payload.get("format") != "AWP192X_PAGE_SETTINGS"
                or payload.get("version") != 1
            ):
                raise ValueError("unsupported AWP192x page settings metadata")
            return payload
        return None

    def _apply_page_settings_metadata(self, payload: dict[str, Any]) -> None:
        model = getattr(self, "_chip_settings_model", None)
        shared = payload.get("shared")
        advanced = payload.get("advanced")
        self._syncing_chip_settings = True
        try:
            if isinstance(shared, dict):
                if model is not None:
                    model.update_shared(shared)
                self._apply_shared_settings(shared)
            if isinstance(advanced, dict) and model is not None:
                model.update_advanced(advanced)
                if self.page_name == "full_mode":
                    self._apply_full_advanced_settings(advanced)
            waveforms = payload.get("waveforms")
            if isinstance(waveforms, list):
                self.storage.clear()
                for raw_item in waveforms:
                    if not isinstance(raw_item, dict):
                        continue
                    recipe_data = raw_item.get("recipe")
                    recipe = None
                    if isinstance(recipe_data, dict):
                        recipe_fields = {key: value for key, value in recipe_data.items() if key != "kind"}
                        if recipe_data.get("kind") == "sinc":
                            recipe = Awp192xWaveformRecipe(**recipe_fields)
                        elif recipe_data.get("kind") == "sine":
                            recipe = Awp192xSineWaveformRecipe(**recipe_fields)
                    self.storage.add_or_replace(
                        int(raw_item["index"]),
                        int(raw_item["start_addr"]),
                        int(raw_item["end_addr"]),
                        int(raw_item["points"]),
                        str(raw_item["waveform_type"]),
                        [int(value) for value in raw_item.get("values", [])],
                        power_on_recipe=recipe,
                    )
        except (KeyError, TypeError, ValueError) as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
        finally:
            self._syncing_chip_settings = False

    def _set_register_script_buttons_state(self, state: str) -> None:
        for key in ("button_mode_import_user_preset", "button_mode_export_user_preset"):
            button = self._buttons.get(key)
            if button is not None:
                button.configure(state=state)

    def _execute_register_script_operations(self, operations: list[RegisterScriptOperation]) -> str | None:
        def transport(operation: RegisterScriptOperation):
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

        try:
            points_per_write = self.waveform_page._selected_write_points_per_batch()
        except (AttributeError, TypeError, ValueError, tk.TclError):
            points_per_write = WAVEFORM_DEFAULT_POINTS_PER_WRITE

        active_awp192x_sram_devices: set[int] = set()
        operation_index = 0
        while operation_index < len(operations):
            operation = operations[operation_index]
            line_no = operation.source_line or operation_index + 1

            if (
                points_per_write > 1
                and operation.device_addr in active_awp192x_sram_devices
                and operation.action == "WRITE"
                and operation.reg_addr == 0x00
                and operation.value is not None
            ):
                batch = [operation]
                next_index = operation_index + 1
                while next_index < len(operations) and len(batch) < points_per_write:
                    candidate = operations[next_index]
                    if not (
                        candidate.device_addr == operation.device_addr
                        and candidate.action == "WRITE"
                        and candidate.reg_addr == 0x00
                        and candidate.value is not None
                    ):
                        break
                    batch.append(candidate)
                    next_index += 1
                if len(batch) > 1:
                    try:
                        response = self.ctx.serial.write_register_values(
                            operation.device_addr,
                            operation.reg_addr,
                            [item.value for item in batch],
                            bits=16,
                        )
                    except Exception as exc:
                        return f"Line {line_no}: {exc}"
                    if not response.ok:
                        return f"Line {line_no}: {response.error}"
                    operation_index = next_index
                    continue

            try:
                response = transport(operation)
            except Exception as exc:
                return f"Line {line_no}: {exc}"
            if not response.ok:
                return f"Line {line_no}: {response.error}"

            if operation.action == "WRITE" and operation.reg_addr == 0x14:
                if (
                    operation.value == 0x0003
                    and operation.device_addr in AWP192X_SRAM_AUTO_INCREMENT_ADDRESSES
                ):
                    active_awp192x_sram_devices.add(operation.device_addr)
                else:
                    active_awp192x_sram_devices.discard(operation.device_addr)
            operation_index += 1
        return None

    def _build_current_register_script_operations(self) -> list[RegisterScriptOperation]:
        page = getattr(self, "waveform_page", None)
        use_imported = getattr(page, "_preview_imported_waveform", False)
        dev = self.ctx.state.awp192x_device_addr
        runtime = None
        if (
            page is not None
            and self.page_name != "full_mode"
            and page.waveform_type_var.get() == "sinc"
            and not use_imported
            and getattr(page, "source", None) != "rom"
        ):
            runtime = self.build_button_runtime_payload(self)
            max_output_reg_value = runtime.max_output_reg
            duration = runtime.duration_reg + 1
            press_code = runtime.press_threshold_reg
            release_code = runtime.release_threshold_reg
            adc_output_enable = runtime.adc_output_reg
            d2_config = runtime.d2_config_reg
            adc_filter_config = runtime.adc_filter_reg
            press_value = runtime.press_action_base | self.ADC_DETECT_ENABLE
            adc_event_config = runtime.adc_event_wait_press
            release_value = runtime.release_action_base
            play_mode_value = runtime.manual_play_reg
        else:
            max_output_reg_value = self._current_max_output_reg_value()
            duration = self._parse_script_duration()
            if self.page_name == "full_mode":
                thresholds = self._parse_full_adc_thresholds(show_error=False)
                if thresholds is None:
                    raise ValueError("Full Mode ADC threshold settings are incomplete")
                press_code, release_code = thresholds
            else:
                press_code, release_code = self._current_threshold_codes()
            press_repeat = self._parse_script_repeat_count(
                self.press_repeat_count_var.get(),
                "press repeat",
            )
            release_repeat = self._parse_script_repeat_count(
                self.release_repeat_count_var.get(),
                "release repeat",
            )
            press_slice = self._parse_script_waveform_slice(
                self.press_waveform_var.get(),
                "press waveform",
            )
            release_slice = self._parse_script_waveform_slice(
                self.release_waveform_var.get(),
                "release waveform",
            )
            play_slice = self._parse_script_waveform_slice(
                self.play_waveform_var.get(),
                "play waveform",
            )
            if self.page_name == "full_mode":
                max_output_reg_value = self._compose_full_reg14()
                press_value = self._compose_full_event_value("adc_over_h_posedge", show_error=False)
                release_value = self._compose_full_event_value("adc_over_l_posedge", show_error=False)
                play_mode_value = self._compose_full_manual_play_value(show_error=False)
                if None in (press_value, release_value, play_mode_value):
                    raise ValueError("Full Mode playback settings are incomplete")
                adc_output_enable = self.ADC_OUTPUT_ENABLE
                d2_config = self._compose_interrupt_mask()
                adc_filter_config = self._compose_adc_config_0c()
                adc_event_config = self._compose_adc_config_0b()
            else:
                press_value = self._set_repeat_and_waveform_fields(
                    self.ADC_DETECT_ENABLE,
                    press_repeat,
                    press_slice,
                )
                release_value = self._set_repeat_and_waveform_fields(
                    0,
                    release_repeat,
                    release_slice,
                )
                play_mode_value = self._set_waveform_field(0, play_slice)
                if self.endless_play_var.get():
                    play_mode_value |= self.ENDLESS_PLAY_BIT
                else:
                    play_repeat = self._parse_script_repeat_count(
                        self.play_repeat_count_var.get(),
                        "play repeat",
                    )
                    play_mode_value = self._set_repeat_field(play_mode_value, play_repeat)
                adc_output_enable = self.ADC_OUTPUT_ENABLE
                d2_config = self.ADC_D2_CONFIG
                adc_filter_config = self._compose_button_adc_filter_config()
                adc_event_config = self._compose_button_adc_event_config()

        operations = build_button_mode_register_script_operations(
            dev,
            max_output_reg_value=max_output_reg_value,
            duration=duration,
            press_code=press_code,
            release_code=release_code,
            adc_output_enable=adc_output_enable,
            d2_config=d2_config,
            adc_filter_config=adc_filter_config,
            press_value=press_value,
            adc_event_config=adc_event_config,
            release_value=release_value,
            play_mode_value=play_mode_value,
        )

        if (
            page is not None
            and self.page_name != "full_mode"
            and getattr(page, "source", None) == "rom"
        ):
            return operations

        if self.page_name == "full_mode":
            for event_key, reg, _source_bit in self.ADC_EVENT_CONFIGS:
                if reg in (0x0F, 0x11):
                    continue
                event_value = self._compose_full_event_value(event_key, show_error=False)
                if event_value is None:
                    raise ValueError(f"Full Mode {event_key} settings are incomplete")
                operations.append(
                    RegisterScriptOperation(
                        dev,
                        "WRITE",
                        reg,
                        event_value,
                        f"Set Full Mode {event_key} action",
                    )
                )
            operations.append(
                RegisterScriptOperation(
                    dev,
                    "WRITE",
                    0x04,
                    self._compose_global_config(),
                    "Set Full Mode global configuration",
                )
            )
            operations.extend(
                RegisterScriptOperation(
                    dev,
                    "WRITE",
                    reg,
                    self._compose_serial_register(pair * 2),
                    "Set Full Mode serial waveform chain",
                )
                for pair, reg in enumerate((0x17, 0x18, 0x19, 0x1A))
            )
        elif page is not None and page.waveform_type_var.get() == "serial":
            operations.extend(
                RegisterScriptOperation(dev, "WRITE", reg, value, "Set serial waveform playback configuration")
                for reg, value in page.build_serial_register_values()
            )
            return operations

        if page is not None and page.waveform_type_var.get() != "serial":
            values, _voltages, slice_index, start_addr, end_addr, _waveform_type = page._generate_current(
                use_imported=use_imported
            )
            recipe = runtime.waveform_recipe if runtime is not None else None
            if recipe is None:
                item = self.storage.slices.get(slice_index)
                recipe = getattr(item, "power_on_recipe", None)
            operations.extend(
                build_waveform_register_script_operations(
                    dev,
                    values=values,
                    slice_index=slice_index,
                    start_addr=start_addr,
                    end_addr=end_addr,
                    max_output_reg_value=max_output_reg_value,
                    waveform_recipe=recipe if isinstance(recipe, Awp192xWaveformRecipe) else None,
                )
            )
        if self.page_name == "full_mode":
            page_slice = int(page.slice_var.get()) if page is not None else -1
            for slice_index, item in sorted(self.storage.slices.items()):
                if slice_index == page_slice:
                    continue
                operations.extend(
                    build_waveform_register_script_operations(
                        dev,
                        values=list(item.values),
                        slice_index=slice_index,
                        start_addr=item.start_addr,
                        end_addr=item.end_addr,
                        max_output_reg_value=max_output_reg_value,
                        waveform_recipe=(
                            item.power_on_recipe
                            if isinstance(item.power_on_recipe, Awp192xWaveformRecipe)
                            else None
                        ),
                    )
                )
        return operations

    def _parse_script_duration(self) -> int:
        duration = int(self.duration_var.get().strip(), 0)
        if not 0 <= duration <= 0xFFFE:
            raise ValueError("duration must be 0-65534")
        return duration

    def _current_threshold_codes(self, *, gain: int | None = None) -> tuple[int, int]:
        press_v = float(self.press_threshold_var.get())
        release_v = float(self.release_threshold_var.get())
        if release_v < self.RELEASE_THRESHOLD_MIN_V:
            raise ValueError(f"release threshold must be >= {self.RELEASE_THRESHOLD_MIN_V:.2f} V")
        if gain is None:
            gain = self._selected_button_adc_gain_code()
        return self._map_adc_voltage_to_code(press_v, gain=gain), self._map_adc_voltage_to_code(
            release_v,
            gain=gain,
        )

    def _threshold_register_hex_text(self, text: str, *, is_release: bool = False) -> str:
        try:
            voltage = float(text)
        except ValueError:
            return ""
        if not math.isfinite(voltage):
            return ""
        if is_release and voltage < self.RELEASE_THRESHOLD_MIN_V:
            return ""
        return f"0x{self._map_adc_voltage_to_code(voltage, gain=self._selected_button_adc_gain_code()):04X}"

    def _refresh_threshold_register_value_labels(self) -> None:
        self.press_threshold_hex_var.set(self._threshold_register_hex_text(self.press_threshold_var.get()))
        self.release_threshold_hex_var.set(
            self._threshold_register_hex_text(self.release_threshold_var.get(), is_release=True)
        )

    def _current_max_output_reg_value(self) -> int:
        selected = self.max_voltage_var.get()
        for label, _voltage, reg_value in self.amp_options:
            if label == selected:
                return reg_value
        raise ValueError(f"invalid max output voltage: {selected}")

    def _parse_script_repeat_count(self, text: str, field_name: str) -> int:
        count = int(text.strip(), 0)
        if not 1 <= count <= 512:
            raise ValueError(f"{field_name} must be 1-512")
        return count

    def _parse_script_waveform_slice(self, text: str, field_name: str) -> int:
        value = text.strip().lower()
        if value == "serial":
            index = self.SERIAL_WAVE_NUMBER
        elif value in {"rom sin", "sin", "0x1f", "31"}:
            index = 0x1F
        elif value.startswith("slice"):
            index = int(value.replace("slice", "", 1), 10)
        else:
            index = int(value, 0)
        if not (0 <= index <= 31):
            raise ValueError(f"{field_name} must be slice0-slice29, serial or 0x1F")
        return index

    def _update_period_hint(self, *, write_register: bool = True) -> None:
        try:
            us = int(self.duration_var.get())
            register_duration = encode_single_point_duration(us)
            self.ctx.state.single_point_duration_us = us
            if (
                write_register
                and not getattr(self, "_reading_current_settings", False)
                and not getattr(self, "_syncing_chip_settings", False)
            ):
                self.write_reg(0x15, register_duration, show_error=False)
            # \u8fd9\u91cc\u7528\u5f53\u524d\u6ce2\u5f62\u70b9\u6570\u4f30\u7b97
            points = self.waveform_page.effective_points() if hasattr(self, "waveform_page") else 0
            period_ms = points * us / 1000
            freq = 1000 / period_ms if period_ms > 0 else 0
            if self.language == "zh":
                self.period_hint_var.set(f"\u6ce2\u5f62\u5468\u671f\u7ea6\u4e3a {period_ms:.3f} ms\n\u9891\u7387\u7ea6\u4e3a {freq:.1f} Hz")
            else:
                self.period_hint_var.set(f"Waveform period about {period_ms:.3f} ms\nFrequency about {freq:.1f} Hz")
        except Exception:
            self.period_hint_var.set("")

    def _on_voltage_changed(self) -> None:
        selected = self.max_voltage_var.get()
        for label, voltage, reg_value in self.amp_options:
            if label == selected:
                self.ctx.state.current_max_output_voltage = voltage
                self.ctx.state.current_max_output_reg_value = reg_value
                if self.page_name == "button_mode":
                    self._mark_button_runtime_dirty()
                elif not getattr(self, "_reading_current_settings", False) and not getattr(self, "_syncing_chip_settings", False):
                    self.write_reg(0x14, reg_value, show_error=False)
                break

    def _compose_button_adc_filter_config(self) -> int:
        average_code = next(
            (value for label, value in self.ADC_AVERAGE_OPTIONS if label == self.adc_average_time_var.get()),
            (self.ADC_DEBOUNCE_CONFIG >> 4) & 0x0F,
        )
        ovv_code = next(
            (value for label, value in self.ADC_OVV_OPTIONS if label == self.adc_ovv_time_var.get()),
            self.ADC_DEBOUNCE_CONFIG & 0x0F,
        )
        return ((average_code & 0x0F) << 4) | (ovv_code & 0x0F)

    def _write_button_adc_filter_config(self, *, show_error: bool = False) -> bool:
        return self.write_reg(0x0C, self._compose_button_adc_filter_config(), show_error=show_error)

    def _compose_button_adc_event_config(self) -> int:
        gain_code = self._selected_button_adc_gain_code()
        pol_code = self._selected_button_adc_pol_code()
        value = self.ADC_EVENT_CONFIG & ~((1 << 9) | (1 << 8))
        return value | (gain_code << 9) | (pol_code << 8)

    def _selected_button_adc_gain_code(self) -> int:
        variable = getattr(self, "button_adc_gain_var", None)
        return 0 if variable is not None and variable.get() == "0-100V" else self.ADC_GAIN_SMALL_RANGE

    def _selected_button_adc_pol_code(self) -> int:
        variable = getattr(self, "button_adc_pol_var", None)
        return 1 if variable is not None and variable.get() == "OUT-" else 0

    def _write_button_adc_event_config(self, *, show_error: bool = False) -> bool:
        return self.write_reg(0x0B, self._compose_button_adc_event_config(), show_error=show_error)

    def _on_button_adc_filter_changed(self) -> None:
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        if self.page_name == "button_mode":
            self._mark_button_runtime_dirty()
            return
        self._write_button_adc_filter_config(show_error=False)

    def _on_button_adc_pol_changed(self) -> None:
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        self._mark_button_runtime_dirty()

    def _on_button_adc_gain_changed(self) -> None:
        self._refresh_threshold_register_value_labels()
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        self._mark_button_runtime_dirty()

    def write_reg(self, reg: int, value: int, show_error: bool = True) -> bool:
        if getattr(self, '_preparing', False):
            return False
        if not self.ctx.serial.is_connected:
            if show_error:
                messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return False

        resp = self.ctx.serial.write_register_value(
            self.ctx.state.awp192x_device_addr,
            reg,
            value,
            bits=16,
        )
        if not resp.ok and show_error:
            messagebox.showerror(self.tr("error"), resp.error)
        return resp.ok

    def write_reg_async(
        self,
        reg: int,
        value: int,
        *,
        on_success: Any = None,
        on_failure: Any = None,
    ) -> None:
        if getattr(self, '_preparing', False):
            return
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return

        dev = self.ctx.state.awp192x_device_addr

        def finish(ok: bool, error: str) -> None:
            if not ok:
                messagebox.showerror(self.tr("error"), error)
                if on_failure is not None:
                    on_failure(error)
                return
            if on_success is not None:
                on_success()

        def worker() -> None:
            resp = self.ctx.serial.write_register_value(dev, reg, value, bits=16)
            try:
                self.after(0, finish, resp.ok, resp.error)
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def read_reg(self, reg: int, show_error: bool = True) -> int | None:
        if getattr(self, '_preparing', False):
            return None
        if not self.ctx.serial.is_connected:
            if show_error:
                messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return None

        dev = self.ctx.state.awp192x_device_addr
        resp = self.ctx.serial.read_register_value(dev, reg, bits=16)
        if not resp.ok:
            if show_error:
                messagebox.showerror(self.tr("error"), resp.error)
            return None
        return self.ctx.serial.extract_rdataw_value(
            resp.data,
            device_addr=dev,
            reg_addr=reg,
            bits=16,
        )

    def _map_adc_voltage_to_code(self, voltage: float, *, gain: int = ADC_GAIN_SMALL_RANGE) -> int:
        full_scale = self.ADC_SMALL_RANGE_VOLTAGE if gain else self.ADC_LARGE_RANGE_VOLTAGE
        voltage = max(-full_scale, min(full_scale, float(voltage)))

        if voltage >= 0:
            ratio = voltage / full_scale if full_scale else 0.0
            code = self.ADC_POS_ZERO + round(ratio * (self.ADC_MAX_CODE - self.ADC_POS_ZERO))
        else:
            ratio = abs(voltage) / full_scale if full_scale else 0.0
            code = self.ADC_NEG_ZERO - round(ratio * self.ADC_NEG_ZERO)

        return max(self.ADC_MIN_CODE, min(self.ADC_MAX_CODE, int(code)))

    def _parse_thresholds(self) -> tuple[int, int] | None:
        try:
            press_v = float(self.press_threshold_var.get())
            release_v = float(self.release_threshold_var.get())
        except ValueError:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return None

        if release_v < self.RELEASE_THRESHOLD_MIN_V:
            messagebox.showerror(
                self.tr("error"),
                f"{self.tr('release_threshold')} must be >= {self.RELEASE_THRESHOLD_MIN_V:.2f} V",
            )
            return None

        gain = self._selected_button_adc_gain_code()
        press_code = self._map_adc_voltage_to_code(press_v, gain=gain)
        release_code = self._map_adc_voltage_to_code(release_v, gain=gain)
        return press_code, release_code

    def _apply_adc_threshold_config(self) -> bool:
        thresholds = self._parse_thresholds()
        if thresholds is None:
            return False

        press_code, release_code = thresholds
        if not self.write_reg(0x08, press_code):
            return False
        if not self.write_reg(0x09, release_code):
            return False
        if not self.write_reg(0x0A, self.ADC_OUTPUT_ENABLE):
            return False
        if not self.write_reg(0x03, self.ADC_D2_CONFIG):
            return False
        if not self._write_button_adc_filter_config(show_error=True):
            return False

        adc_status = self.read_reg(0x07, show_error=False)
        if adc_status is not None:
            self.ctx.state.extra["adc_gain"] = 1 if (adc_status & (1 << 13)) else 0
        return True

    def _set_d2_press_threshold_pull_down(self) -> bool:
        current = self.read_reg(0x03)
        if current is None:
            return False
        return self.write_reg(0x03, current | (1 << 13))

    def _on_endless_play_changed(self) -> None:
        entry = self._repeat_entries.get("play_repeat_count")
        if entry is not None:
            entry.configure(state="disabled" if self.endless_play_var.get() else "normal")
        if getattr(self, "_reading_current_settings", False):
            return
        if self.page_name == "button_mode":
            self._mark_button_runtime_dirty()
            return
        self._write_endless_play_bit()

    def _write_endless_play_bit(self) -> bool:
        current_mode = self.read_reg(0x13, show_error=False)
        if current_mode is None:
            return False

        if self.endless_play_var.get():
            current_mode |= self.ENDLESS_PLAY_BIT
        else:
            current_mode &= ~self.ENDLESS_PLAY_BIT
        return self.write_reg(0x13, current_mode, show_error=False)

    def _software_preset_tooltip_text(self, preset_index: int) -> str:
        if not 0 <= preset_index < len(self.BUTTON_PRESETS):
            return ""
        config = self.BUTTON_PRESETS[preset_index]
        duration = int(config["duration"])
        points = config.get("sinc_points", BUTTON_PRESET_SINC_POINTS)
        press_repeat = int(config["press_repeat"])
        period_us = sum(int(point) for point in points) * duration * press_repeat
        sinc_positive = config["sinc_positive"]
        sinc_negative = config.get("sinc_negative", BUTTON_PRESET_SINC_NEGATIVE_VOLTAGE)
        return self.tr(
            "sinc_software_preset_tooltip",
            max_voltage=_format_preset_number(sinc_positive),
            min_voltage=_format_preset_number(sinc_negative),
            period_ms=f"{period_us / 1000:.3f}",
            repeat=press_repeat,
            segment_times="/".join(str(int(point) * duration) for point in points) + " us",
        )

    def apply_preset(self, preset_index: int) -> None:
        if not 0 <= preset_index < len(self.BUTTON_PRESETS):
            return
        self._apply_button_preset(self.BUTTON_PRESETS[preset_index])

    def _apply_button_preset(self, config: dict[str, Any]) -> None:
        self._load_preset_gui_values(config)
        page = getattr(self, "waveform_page", None)
        if page is not None:
            page._preview_imported_waveform = False
            page._update_end_addr()
            page._preview_waveform()
        self._update_period_hint(write_register=False)

        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return
        if getattr(page, "_waveform_write_busy", False) or getattr(self, "_preset_waveform_write_busy", False):
            return

        try:
            recipe = self._button_preset_recipe(config)
            values = list(generate_awp192x_waveform_points(recipe))
            write_data = (values, [], recipe.slice_index, recipe.start_addr, recipe.start_addr + len(values), "sinc")
            gain = self._selected_button_adc_gain_code()
            press_code = self._map_adc_voltage_to_code(
                float(config.get("press_threshold", 1.2)),
                gain=gain,
            )
            release_code = self._map_adc_voltage_to_code(
                float(config.get("release_threshold", -0.01)),
                gain=gain,
            )
            press_value = self._set_repeat_and_waveform_fields(
                self.ADC_DETECT_ENABLE,
                int(config["press_repeat"]),
                0,
            )
            adc_filter_config = self._compose_button_adc_filter_config()
            full_register_values = None
            if self.page_name == "full_mode":
                full_register_values = (
                    self._compose_full_reg14(),
                    self._compose_adc_config_0b(),
                    self._compose_interrupt_mask(),
                )
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return

        begin_progress = getattr(self, "_begin_button_preset_progress", None)
        progress_tokens = begin_progress() if callable(begin_progress) else {}
        preset_status = _begin_status(self, "preset") if not progress_tokens else None

        self._preset_waveform_write_busy = True
        if page is not None:
            page._set_waveform_write_busy(True)
        self._set_preset_buttons_state("disabled")
        dev = self.ctx.state.awp192x_device_addr

        def worker() -> None:
            error = self._write_preset_registers(
                dev,
                duration=int(config["duration"]),
                press_code=press_code,
                release_code=release_code,
                press_value=press_value,
                release_repeat=int(config["release_repeat"]),
                adc_filter_config=adc_filter_config,
                full_register_values=full_register_values,
            )
            if not error:
                values_to_write, _voltages, slice_index, start_addr, end_addr, _waveform_type = write_data
                if page is not None and self.page_name != "full_mode":
                    error = page._write_waveform_to_device(
                        dev,
                        values_to_write,
                        slice_index,
                        start_addr,
                        end_addr,
                    )
                else:
                    error = self._write_waveform_values_to_device(
                        dev,
                        values_to_write,
                        slice_index,
                        start_addr,
                        end_addr,
                    )
            try:
                post_to_ui(
                    self,
                    self._finish_preset_write,
                    write_data,
                    error,
                    recipe,
                    progress_tokens,
                    preset_status,
                )
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _button_preset_recipe(config: dict[str, Any]) -> Awp192xWaveformRecipe:
        points = config.get("sinc_points", BUTTON_PRESET_SINC_POINTS)
        return Awp192xWaveformRecipe(
            generator_type=AWP192X_GENERATOR_SINC,
            generator_version=AWP192X_GENERATOR_SINC_VERSION,
            slice_index=0,
            start_addr=0x0002,
            points1=int(points[0]),
            points2=int(points[1]),
            points3=int(points[2]),
            positive_mv=round(float(config["sinc_positive"]) * 1000),
            negative_mv=round(float(config.get("sinc_negative", BUTTON_PRESET_SINC_NEGATIVE_VOLTAGE)) * 1000),
        )

    def _write_waveform_values_to_device(
        self,
        dev: int,
        values: list[int],
        slice_index: int,
        start_addr: int,
        end_addr: int,
    ) -> str | None:
        """Write an SRAM waveform without relying on a waveform editor window."""
        if not self.ctx.serial.is_connected:
            return "Serial port is not connected"
        lock = getattr(self.ctx.serial, "_lock", None)

        def write_locked() -> str | None:
            restore_max_output = int(
                getattr(self.ctx.state, "current_max_output_reg_value", 0x007E)
            )
            writes = (
                (0x14, 0x0003),
                (0x16, int(slice_index) * 2),
                (0x00, int(start_addr)),
                (0x16, int(slice_index) * 2 + 1),
                (0x00, min(int(end_addr), WaveformStorage.SRAM_MAX)),
                (0x16, int(start_addr)),
            )
            for reg, value in writes:
                response = self.ctx.serial.write_register_value(dev, reg, value, bits=16)
                if not response.ok:
                    return response.error
            for offset in range(0, len(values), WAVEFORM_DEFAULT_POINTS_PER_WRITE):
                response = self.ctx.serial.write_register_values(
                    dev,
                    0x00,
                    values[offset : offset + WAVEFORM_DEFAULT_POINTS_PER_WRITE],
                    bits=16,
                )
                if not response.ok:
                    return response.error
            response = self.ctx.serial.write_register_value(
                dev,
                0x14,
                restore_max_output,
                bits=16,
            )
            return None if response.ok else response.error

        if lock is None:
            return write_locked()
        with lock:
            return write_locked()

    def _load_preset_gui_values(self, config: dict[str, Any]) -> None:
        was_syncing = getattr(self, "_syncing_chip_settings", False)
        self._syncing_chip_settings = True
        points = config.get("sinc_points", BUTTON_PRESET_SINC_POINTS)
        page = getattr(self, "waveform_page", None)
        try:
            self.max_voltage_var.set(self.amp_options[-1][0])
            self.duration_var.set(str(config["duration"]))
            self.press_threshold_var.set(_format_preset_number(config.get("press_threshold", 1.2)))
            self.release_threshold_var.set(_format_preset_number(config.get("release_threshold", -0.01)))
            self.press_repeat_count_var.set(str(config["press_repeat"]))
            self.release_repeat_count_var.set(str(config["release_repeat"]))
            self.press_waveform_var.set("slice0")
            self.release_waveform_var.set("slice0")
            self.play_waveform_var.set("slice0")
            self.play_repeat_count_var.set("1")
            self.endless_play_var.set(False)

            if page is not None:
                page.waveform_type_var.set("sinc")
                page.slice_var.set(0)
                page.sinc_positive_voltage_var.set(str(config["sinc_positive"]))
                page.sinc_negative_voltage_var.set(str(config.get("sinc_negative", BUTTON_PRESET_SINC_NEGATIVE_VOLTAGE)))
                page.sinc_points1_var.set(str(points[0]))
                page.sinc_points2_var.set(str(points[1]))
                page.sinc_points3_var.set(str(points[2]))
                page.start_addr_var.set("0x0002")
            if self.page_name == "full_mode":
                self.adc_threshold_h_var.set(_format_preset_number(config.get("press_threshold", 1.2)))
                self.adc_threshold_l_var.set(_format_preset_number(config.get("release_threshold", -0.01)))
                self.full_event_wave_vars["adc_over_h_posedge"].set("slice0")
                self.full_event_wave_vars["adc_over_l_posedge"].set("slice0")
                self.full_event_repeat_vars["adc_over_h_posedge"].set(str(config["press_repeat"]))
                self.full_event_repeat_vars["adc_over_l_posedge"].set(str(config["release_repeat"]))
                self.full_event_endless_vars["adc_over_h_posedge"].set(False)
                self.full_event_endless_vars["adc_over_l_posedge"].set(False)
                self.full_event_enable_vars["adc_over_h_posedge"].set(True)
                self.full_event_enable_vars["adc_over_l_posedge"].set(False)
        finally:
            self._syncing_chip_settings = was_syncing
        self._capture_shared_settings()
        if self.page_name == "full_mode":
            self._capture_full_advanced_settings()

    def _write_preset_registers(
        self,
        dev: int,
        *,
        duration: int,
        press_code: int,
        release_code: int,
        press_value: int,
        release_repeat: int,
        adc_filter_config: int,
        full_register_values: tuple[int, int, int] | None = None,
    ) -> str | None:
        register_duration = encode_single_point_duration(duration)
        max_output_register = (
            full_register_values[0]
            if full_register_values is not None
            else self._compose_full_reg14()
            if self.page_name == "full_mode"
            else 0x007E
        )
        adc_event_config = (
            full_register_values[1]
            if full_register_values is not None
            else self._compose_adc_config_0b()
            if self.page_name == "full_mode"
            else self._compose_button_adc_event_config()
        )
        interrupt_or_d2_config = (
            full_register_values[2]
            if full_register_values is not None
            else self._compose_interrupt_mask()
            if self.page_name == "full_mode"
            else self.ADC_D2_CONFIG
        )
        self.ctx.state.current_max_output_reg_value = max_output_register
        register_values = (
            (0x14, max_output_register),
            (0x15, register_duration),
            (0x08, press_code),
            (0x09, release_code),
            (0x0A, self.ADC_OUTPUT_ENABLE),
            (0x03, interrupt_or_d2_config),
            (0x0C, adc_filter_config),
            (0x0F, press_value),
            (0x0B, adc_event_config),
        )
        for reg, value in register_values:
            resp = self.ctx.serial.write_register_value(dev, reg, value, bits=16)
            if not resp.ok:
                return resp.error

        release_resp = self.ctx.serial.read_register_value(dev, 0x11, bits=16)
        if not release_resp.ok:
            return release_resp.error
        release_current = self.ctx.serial.extract_rdataw_value(
            release_resp.data,
            device_addr=dev,
            reg_addr=0x11,
            bits=16,
        )
        release_value = self._set_repeat_and_waveform_fields(
            release_current or 0,
            release_repeat,
            0,
        )
        resp = self.ctx.serial.write_register_value(dev, 0x11, release_value, bits=16)
        if not resp.ok:
            return resp.error

        mode_resp = self.ctx.serial.read_register_value(dev, 0x13, bits=16)
        if not mode_resp.ok:
            return mode_resp.error
        mode_value = self.ctx.serial.extract_rdataw_value(
            mode_resp.data,
            device_addr=dev,
            reg_addr=0x13,
            bits=16,
        ) or 0
        mode_value = self._set_repeat_and_waveform_fields(mode_value, 1, 0)
        mode_value &= ~self.ENDLESS_PLAY_BIT
        resp = self.ctx.serial.write_register_value(dev, 0x13, mode_value, bits=16)
        return None if resp.ok else resp.error

    def _finish_preset_write(
        self,
        write_data: tuple[list[int], list[float], int, int, int, str],
        error: str | None,
        power_on_recipe: Awp192xWaveformRecipe | Awp192xSineWaveformRecipe | None = None,
        progress_tokens: dict[str, Any] | None = None,
        preset_status: Any = None,
    ) -> None:
        page = getattr(self, "waveform_page", None)
        self._preset_waveform_write_busy = False
        if page is not None:
            page._set_waveform_write_busy(False)
        self._set_preset_buttons_state("normal")
        if error:
            finish_progress = getattr(self, "_finish_button_preset_progress", None)
            if callable(finish_progress):
                finish_progress(progress_tokens, success=False)
            _finish_status(self, preset_status, success=False, error=error)
            messagebox.showerror(self.tr("error"), error)
            return

        values, _voltages, slice_index, start_addr, end_addr, waveform_type = write_data
        if power_on_recipe is None and page is not None:
            power_on_recipe = page._power_on_recipe_for_values(
                waveform_type=waveform_type,
                slice_index=slice_index,
                start_addr=start_addr,
                values=values,
            )
        self.storage.add_or_replace(
            slice_index,
            start_addr,
            end_addr,
            len(values),
            waveform_type,
            values,
            power_on_recipe=power_on_recipe,
        )
        self.storage.record_successful_write(slice_index)
        if page is not None:
            page._analyzed_ranges = [
                (idx, existing_start, existing_end)
                for idx, existing_start, existing_end in getattr(page, "_analyzed_ranges", [])
                if idx != slice_index
            ]
            page._analyzed_ranges.append((slice_index, start_addr, end_addr))
            page._update_chip_cache_after_write(slice_index, start_addr, end_addr, values)
            page._sync_read_slices_text()
            page._preview_waveform()
        model = getattr(self, "_chip_settings_model", None)
        if model is not None:
            model.mark_applied()
        self._update_period_hint(write_register=False)
        finish_progress = getattr(self, "_finish_button_preset_progress", None)
        if callable(finish_progress):
            finish_progress(progress_tokens, success=True)
        _finish_status(self, preset_status, success=True)
        messagebox.showinfo(self.tr("info"), self.tr("preset_write_success"))

    @staticmethod
    def build_button_runtime_payload(page, settings_model=None):
        """Build the versioned AWP192x Button configuration shared by live and EEPROM paths."""
        wf_page = getattr(page, "waveform_page", None)
        shared = getattr(settings_model, "shared", None)
        if getattr(page, "page_name", "button_mode") == "full_mode" and isinstance(shared, dict):
            max_output_reg = int(shared["output_amplitude"])
            duration_us = int(shared["point_duration"])
            thresholds = shared["thresholds"]
            gain_code = 1 if int(shared.get("range", 0)) else 0
            press_threshold_code = page._map_adc_voltage_to_code(
                float(thresholds["press"]), gain=gain_code
            )
            release_threshold_code = page._map_adc_voltage_to_code(
                float(thresholds["release"]), gain=gain_code
            )
            filters = shared.get("filter_settings", {})
            adc_filter_config = ((int(filters.get("average", 0)) & 0x0F) << 4) | (
                int(filters.get("ovv", 0)) & 0x0F
            )
            press = shared["press_action"]
            release = shared["release_action"]
            manual = shared["manual_playback"]
            press_repeat = page._parse_script_repeat_count(str(press["repeat"]), "press repeat")
            release_repeat = page._parse_script_repeat_count(str(release["repeat"]), "release repeat")
            press_slice = page._parse_script_waveform_slice(str(press["wave"]), "press waveform")
            release_slice = page._parse_script_waveform_slice(str(release["wave"]), "release waveform")
            play_slice = page._parse_script_waveform_slice(str(manual["wave"]), "play waveform")
            endless_play = bool(manual.get("endless", False))
            play_repeat = None if endless_play else page._parse_script_repeat_count(
                str(manual["repeat"]), "play repeat"
            )
            adc_event_wait_press = (
                page.ADC_EVENT_CONFIG & ~((1 << 9) | (1 << 8))
            ) | (gain_code << 9) | (int(bool(shared.get("polarity", 0))) << 8)
            serial_settings = shared.get("serial_waveforms", [])
        else:
            max_output_reg = page._current_max_output_reg_value()
            duration_us = int(page.duration_var.get())
            press_threshold_code, release_threshold_code = page._current_threshold_codes()
            adc_filter_config = page._compose_button_adc_filter_config()

            press_repeat = page._parse_script_repeat_count(page.press_repeat_count_var.get(), "press repeat")
            release_repeat = page._parse_script_repeat_count(page.release_repeat_count_var.get(), "release repeat")

            press_slice = page._parse_script_waveform_slice(page.press_waveform_var.get(), "press waveform")
            release_slice = page._parse_script_waveform_slice(page.release_waveform_var.get(), "release waveform")
            play_slice = page._parse_script_waveform_slice(page.play_waveform_var.get(), "play waveform")
            endless_play = bool(page.endless_play_var.get())
            play_repeat = None if endless_play else page._parse_script_repeat_count(
                page.play_repeat_count_var.get(), "play repeat"
            )
            adc_event_wait_press = page._compose_button_adc_event_config()
            serial_settings = None

        duration_reg = encode_single_point_duration(duration_us)

        press_config = page._set_repeat_and_waveform_fields(page.ADC_DETECT_ENABLE, press_repeat, press_slice)
        release_config = page._set_repeat_and_waveform_fields(0, release_repeat, release_slice)

        play_config = page._set_waveform_field(0, play_slice)
        if endless_play:
            play_config |= page.ENDLESS_PLAY_BIT
        else:
            play_config = page._set_repeat_field(play_config, play_repeat)

        serial_selected = page.SERIAL_WAVE_NUMBER in (press_slice, release_slice, play_slice)
        serial_values = {0x17: 0, 0x18: 0, 0x19: 0, 0x1A: 0}
        if serial_selected:
            if serial_settings is not None:
                if len(serial_settings) != 8:
                    raise ValueError("serial waveform configuration is incomplete")
                codes = [int(entry["wave"]) for entry in serial_settings]
                repeats = [int(entry["repeat"]) for entry in serial_settings]
                if any(not 0 <= code <= 0x1F for code in codes) or any(
                    not 1 <= count <= 8 for count in repeats
                ):
                    raise ValueError("serial waveform configuration is incomplete")
                serial_values.update({
                    0x17 + pair: ((repeats[pair * 2] - 1) & 0x07)
                    | ((codes[pair * 2] & 0x1F) << 3)
                    | (((repeats[pair * 2 + 1] - 1) & 0x07) << 8)
                    | ((codes[pair * 2 + 1] & 0x1F) << 11)
                    for pair in range(4)
                })
            elif hasattr(page, "serial_wave_vars") and hasattr(page, "serial_loop_vars"):
                codes = [waveform_number(variable.get()) for variable in page.serial_wave_vars]
                repeats = [int(variable.get(), 0) for variable in page.serial_loop_vars]
                if len(codes) != 8 or len(repeats) != 8 or any(not 1 <= count <= 8 for count in repeats):
                    raise ValueError("serial waveform configuration is incomplete")
                serial_values.update({
                    0x17 + pair: ((repeats[pair * 2] - 1) & 0x07)
                    | ((codes[pair * 2] & 0x1F) << 3)
                    | (((repeats[pair * 2 + 1] - 1) & 0x07) << 8)
                    | ((codes[pair * 2 + 1] & 0x1F) << 11)
                    for pair in range(4)
                })
            elif wf_page is not None and hasattr(wf_page, "build_serial_register_values"):
                serial_values.update(dict(wf_page.build_serial_register_values()))
            else:
                raise ValueError("serial waveform configuration is incomplete")

        required_slices = required_awp192x_button_waveform_slices(
            press_action_base=press_config & ~page.ADC_DETECT_ENABLE,
            release_action_base=release_config & ~page.ADC_DETECT_ENABLE,
            manual_play_reg=play_config,
            serial_config_0x17=serial_values[0x17],
            serial_config_0x18=serial_values[0x18],
            serial_config_0x19=serial_values[0x19],
            serial_config_0x1A=serial_values[0x1A],
        )
        stored_slices = getattr(getattr(page, "storage", None), "slices", {})
        current_slice = int(wf_page.slice_var.get()) if wf_page is not None and hasattr(wf_page, "slice_var") else -1
        current_type = wf_page.waveform_type_var.get() if wf_page is not None and hasattr(wf_page, "waveform_type_var") else ""

        waveforms: list[Awp192xWaveformRecipe | Awp192xSineWaveformRecipe] = []
        for slice_index in required_slices:
            item = stored_slices.get(slice_index)
            waveform_type = getattr(item, "waveform_type", "missing") if item is not None else "missing"
            if item is not None and str(waveform_type).lower() not in {"sinc", "sine"}:
                raise ValueError(
                    f"slice{slice_index} cannot be saved as a Type-3 power-on preset: "
                    f"{waveform_type} is not a SINC/SIN waveform"
                )
            recipe = getattr(item, "power_on_recipe", None)
            if recipe is None and slice_index == current_slice:
                if current_type == "sinc":
                    recipe = Awp192xWaveformRecipe(
                        generator_type=AWP192X_GENERATOR_SINC,
                        generator_version=AWP192X_GENERATOR_SINC_VERSION,
                        slice_index=slice_index,
                        start_addr=int(wf_page.start_addr_var.get(), 0),
                        points1=int(wf_page.sinc_points1_var.get()),
                        points2=int(wf_page.sinc_points2_var.get()),
                        points3=int(wf_page.sinc_points3_var.get()),
                        positive_mv=round(float(wf_page.sinc_positive_voltage_var.get()) * 1000),
                        negative_mv=round(float(wf_page.sinc_negative_voltage_var.get()) * 1000),
                    )
                elif current_type == "sine":
                    start_phase = float(wf_page.start_phase_var.get())
                    end_phase = float(wf_page.end_phase_var.get())
                    if not start_phase.is_integer() or not end_phase.is_integer():
                        raise ValueError(
                            f"slice{slice_index} SIN power-on phases must use whole degrees"
                        )
                    normalized_start = int(start_phase) % 360
                    recipe = Awp192xSineWaveformRecipe(
                        slice_index=slice_index,
                        start_addr=int(wf_page.start_addr_var.get(), 0),
                        points=int(wf_page.points_var.get()),
                        start_phase_deg=normalized_start,
                        end_phase_deg=normalized_start + int(end_phase - start_phase),
                        amplitude_mv=round(abs(float(wf_page.voltage_amplitude_var.get())) * 1000),
                        offset_mv=round(float(wf_page.offset_var.get()) * 1000),
                    )

            if recipe is None:
                raise ValueError(
                    f"slice{slice_index} cannot be saved for power-on: "
                    f"{waveform_type} has no SINC/SIN generation recipe"
                )
            if item is not None and list(generate_awp192x_waveform_points(recipe)) != list(item.values):
                raise ValueError(
                    f"slice{slice_index} waveform data no longer matches its SINC/SIN recipe"
                )
            waveforms.append(recipe)

        if not waveforms:
            raise ValueError(
                "AWP192x Type-3 slots require at least one SINC/SIN SRAM waveform recipe"
            )
        if len(waveforms) > AWP192X_BUTTON_MAX_WAVEFORMS:
            raise ValueError(
                "AWP192x Type-3 slots support at most "
                f"{AWP192X_BUTTON_MAX_WAVEFORMS} waveforms; "
                f"the current chain requires {len(waveforms)}"
            )

        return Awp192xButtonPayloadV2(
            payload_version=page.BUTTON_RUNTIME_PAYLOAD_VERSION,
            flags=page.BUTTON_RUNTIME_FLAG_SERIAL_VALID if serial_selected else 0,
            max_output_reg=max_output_reg,
            duration_reg=duration_reg,
            press_threshold_reg=press_threshold_code,
            release_threshold_reg=release_threshold_code,
            adc_filter_reg=adc_filter_config,
            adc_event_wait_press=adc_event_wait_press,
            press_action_base=press_config & ~page.ADC_DETECT_ENABLE,
            release_action_base=release_config & ~page.ADC_DETECT_ENABLE,
            manual_play_reg=play_config,
            serial_config_0x17=serial_values[0x17],
            serial_config_0x18=serial_values[0x18],
            serial_config_0x19=serial_values[0x19],
            serial_config_0x1A=serial_values[0x1A],
            waveforms=tuple(waveforms),
        )

    @staticmethod
    def build_board_stored_preset(page, settings_model=None):
        """Compatibility name for callers that save the shared Button Runtime payload."""
        return ButtonModePage.build_button_runtime_payload(page, settings_model=settings_model)

    def save_board_preset(self) -> None:
        if not self._ensure_connected():
            return

        page_name = getattr(self, "page_name", "button_mode")
        model = getattr(self, "_chip_settings_model", None)
        settings_snapshot = None
        if page_name == "full_mode":
            if not self._capture_shared_settings():
                messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
                return
            self._capture_full_advanced_settings()
            model = getattr(self, "_chip_settings_model", None)
            if model is None:
                messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
                return
            settings_snapshot = deepcopy(model)
        preflight_model = settings_snapshot if settings_snapshot is not None else model
        allowed, warning_key = can_save_slot(preflight_model) if preflight_model is not None else (True, None)
        if not allowed:
            messagebox.showwarning(self.tr("error"), self.tr(warning_key or "preset_slot_advanced_block_warning"))
            return

        try:
            preset = self.build_board_stored_preset(self, settings_model=settings_snapshot)
            from core.awp192x_button_runtime import pack_awp192x_button_payload
            from core.arduino_preset_sync import (
                PAYLOAD_TYPE_AWP192X_BUTTON_RUNTIME,
                V2Preset,
                pack_v2_preset,
                signature_for_address,
            )

            payload = pack_v2_preset(
                V2Preset(
                    PAYLOAD_TYPE_AWP192X_BUTTON_RUNTIME,
                    signature_for_address(self.ctx.state.awp192x_device_addr),
                    self.ctx.state.awp192x_device_addr,
                    pack_awp192x_button_payload(preset),
                )
            )
        except ValueError as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return

        slot = int(self.board_slot_var.get())
        status_token = _begin_status(self, "preset")
        try:
            resp = self.ctx.serial.sync_config_preset(slot, payload, set_active=False)
        except Exception as exc:
            _finish_status(self, status_token, success=False, error=str(exc))
            messagebox.showerror(self.tr("error"), str(exc))
            return

        if resp.ok:
            _finish_status(self, status_token, success=True)
            if page_name == "full_mode" and getattr(self._chip_settings_model, "advanced", {}).get("slot_omitted"):
                messagebox.showwarning(
                    self.tr("info"),
                    f"{self.tr('board_preset_saved')}\n{self.tr('preset_slot_advanced_scope_warning')}",
                )
            else:
                messagebox.showinfo(self.tr("info"), self.tr("board_preset_saved"))
        else:
            _finish_status(self, status_token, success=False, error=resp.error)
            messagebox.showerror(self.tr("error"), resp.error)

    def set_board_preset_active(self) -> None:
        if not self._ensure_connected():
            return

        slot = int(self.board_slot_var.get())
        status_token = _begin_status(self, "preset")
        try:
            resp = self.ctx.serial.cfg_use(slot)
        except Exception as exc:
            _finish_status(self, status_token, success=False, error=str(exc))
            messagebox.showerror(self.tr("error"), str(exc))
            return

        if resp.ok:
            _finish_status(self, status_token, success=True)
            messagebox.showinfo(self.tr("info"), self.tr("board_preset_read").format(slot=slot))
        else:
            _finish_status(self, status_token, success=False, error=resp.error)
            messagebox.showerror(self.tr("error"), resp.error)

    def apply_board_preset(self) -> None:
        if not self._ensure_connected():
            return

        slot = int(self.board_slot_var.get())
        status_token = _begin_status(self, "preset")
        try:
            resp = self.ctx.serial.cfg_apply(slot)
        except Exception as exc:
            _finish_status(self, status_token, success=False, error=str(exc))
            messagebox.showerror(self.tr("error"), str(exc))
            return

        if resp.ok:
            messagebox.showinfo(self.tr("info"), self.tr("board_preset_read").format(slot=slot))
            self.read_board_preset(show_success=False, status_token=status_token)
            self.read_current_settings(show_success=False)
        else:
            _finish_status(self, status_token, success=False, error=resp.error)
            messagebox.showerror(self.tr("error"), resp.error)

    def read_board_preset(self, *, show_success: bool = True, status_token: Any = None) -> None:
        if not self._ensure_connected():
            _finish_status(self, status_token, success=False, error=self.tr("not_connected"))
            return

        slot = int(self.board_slot_var.get())
        owns_status = status_token is None
        if owns_status:
            status_token = _begin_status(self, "preset")
        try:
            resp = self.ctx.serial.cfg_read(slot)
        except Exception as exc:
            _finish_status(self, status_token, success=False, error=str(exc))
            messagebox.showerror(self.tr("error"), str(exc))
            return

        if resp.ok:
            raw_hex = self.ctx.serial._extract_cfgread_hex(resp.data)
            if raw_hex is None:
                _finish_status(self, status_token, success=False, error=self.tr("board_preset_read_invalid"))
                messagebox.showerror(self.tr("error"), self.tr("board_preset_read_invalid"))
                return
            try:
                from core.arduino_preset_sync import (
                    PAYLOAD_TYPE_AWP192X_BUTTON_RUNTIME,
                    format_preset_summary,
                    unpack_v2_preset,
                )
                from core.awp192x_button_runtime import unpack_awp192x_button_payload

                raw = bytes.fromhex(raw_hex)
                summary = format_preset_summary(raw)
                stored = unpack_v2_preset(raw)
                if stored.payload_type != PAYLOAD_TYPE_AWP192X_BUTTON_RUNTIME:
                    raise ValueError("CFG_UNSUPPORTED: expected an AWP192x Type-3 v2 preset")
                self._apply_button_runtime_page_state(
                    unpack_awp192x_button_payload(stored.payload)
                )
            except ValueError as exc:
                _finish_status(self, status_token, success=False, error=str(exc))
                messagebox.showerror(self.tr("error"), f"{self.tr('board_preset_read_invalid')}\n{exc}")
                return
            _finish_status(self, status_token, success=True)
            if show_success:
                messagebox.showinfo(self.tr("info"), summary)
        else:
            _finish_status(self, status_token, success=False, error=resp.error)
            messagebox.showerror(self.tr("error"), resp.error)

    def _apply_button_runtime_page_state(self, runtime) -> None:
        registers = {
            0x14: runtime.max_output_reg,
            0x15: runtime.duration_reg,
            0x08: runtime.press_threshold_reg,
            0x09: runtime.release_threshold_reg,
            0x0C: runtime.adc_filter_reg,
            0x0B: runtime.adc_event_wait_press,
            0x0F: runtime.press_action_base | self.ADC_DETECT_ENABLE,
            0x11: runtime.release_action_base,
            0x13: runtime.manual_play_reg,
            0x17: runtime.serial_config_0x17,
            0x18: runtime.serial_config_0x18,
            0x19: runtime.serial_config_0x19,
            0x1A: runtime.serial_config_0x1A,
        }
        self._apply_current_settings_snapshot(
            registers,
            [],
            [],
            waveform_recipes=runtime.waveforms,
        )
        self._button_runtime_dirty = False

    def read_current_settings(self, *, show_success: bool = True) -> None:
        if not self._ensure_connected():
            return

        self._set_preset_buttons_state("disabled")
        dev = self.ctx.state.awp192x_device_addr

        def worker() -> None:
            registers, ram_values, analyzed, error = self._read_current_settings_snapshot(dev)
            try:
                callback_args = (registers, ram_values, analyzed, error)
                if not show_success:
                    callback_args += (False,)
                self.after(0, self._finish_current_settings_read, *callback_args)
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _read_current_settings_snapshot(
        self,
        dev: int,
    ) -> tuple[dict[int, int], list[int], list[tuple[int, int, int]], str | None]:
        """Read the confirmed register set without entering SRAM access mode."""
        registers: dict[int, int] = {}
        for reg in self.CURRENT_SETTING_REGISTERS:
            resp = self.ctx.serial.read_register_value(dev, reg, bits=16)
            if not resp.ok:
                return registers, [], [], resp.error
            value = self.ctx.serial.extract_rdataw_value(
                resp.data,
                device_addr=dev,
                reg_addr=reg,
                bits=16,
            )
            if value is None:
                return registers, [], [], f"Invalid register response for 0x{reg:02X}"
            registers[reg] = value
        return registers, [], [], None

    @staticmethod
    def _analyze_current_waveform_ranges(values: list[int]) -> list[tuple[int, int, int]]:
        analyzed: list[tuple[int, int, int]] = []
        for slice_index in range(WaveformStorage.MAX_SLICES):
            index_start_addr = slice_index * 2
            index_end_addr = index_start_addr + 1
            if any(
                start <= index_start_addr < end or start <= index_end_addr < end
                for _idx, start, end in analyzed
            ):
                break
            if index_end_addr >= len(values):
                break

            start_value = values[index_start_addr]
            end_value = values[index_end_addr]
            if not (0 <= start_value <= WaveformStorage.SRAM_MAX and 0 <= end_value <= WaveformStorage.SRAM_MAX + 1):
                if analyzed:
                    break
                continue
            if start_value == 0 and end_value == 0:
                if analyzed:
                    break
                continue
            if end_value <= start_value:
                if analyzed:
                    break
                continue
            analyzed.append((slice_index, start_value, end_value))
        return analyzed

    def _finish_current_settings_read(
        self,
        registers: dict[int, int],
        ram_values: list[int],
        analyzed: list[tuple[int, int, int]],
        error: str | None,
        show_success: bool = True,
    ) -> None:
        self._set_preset_buttons_state("normal")
        if error:
            messagebox.showerror(
                self.tr("error"),
                self.tr("current_settings_read_failed").format(error=error),
            )
            return

        self._apply_current_settings_snapshot(registers, ram_values, analyzed)
        if show_success:
            waveform_points = sum(end - start for _idx, start, end in analyzed)
            messagebox.showinfo(
                self.tr("info"),
                self.tr("current_settings_read_success").format(
                    slices=len(analyzed),
                    points=waveform_points,
                ),
            )

    @classmethod
    def _format_current_waveform_slice(cls, value: int) -> str:
        if 0 <= value <= 29:
            return f"slice{value}"
        if value == cls.SERIAL_WAVE_NUMBER:
            return "serial"
        return f"0x{value:02X}"

    @staticmethod
    def _format_current_serial_slice(value: int) -> str:
        return f"slice{value}" if 0 <= value <= 29 else f"0x{value:02X}"

    @staticmethod
    def _format_current_voltage(value: float) -> str:
        if abs(value) < 0.0005:
            value = 0.0
        return f"{value:.3f}".rstrip("0").rstrip(".")

    def _apply_full_reg14_settings(self, value: int) -> None:
        """Decode all Full Mode fields from register 0x14 before syncing its view."""
        value = int(value) & 0xFFFF
        self.full_slow_up_var.set(self._option_label(self.SLOW_TIME_OPTIONS, (value >> 14) & 0x03))
        self.full_slow_down_var.set(self._option_label(self.SLOW_TIME_OPTIONS, (value >> 12) & 0x03))
        self.full_half_cycle_var.set(bool(value & (1 << 11)))
        self.full_phase_var.set("180" if value & (1 << 10) else "0")
        self.full_data_depth_var.set(self._option_label(self.DATA_DEPTH_OPTIONS, (value >> 7) & 0x07))
        self.full_dac_mode_var.set(self._option_label(self.DAC_MODE_OPTIONS, value & 0x03))
        amplitude_code = (value >> 2) & 0x1F
        label = next(
            (name for name, _voltage, reg_value in self.amp_options
             if ((reg_value >> 2) & 0x1F) == amplitude_code),
            f"0x{value:04X}",
        )
        self.max_voltage_var.set(label)
        voltage = next(
            (voltage for name, voltage, reg_value in self.amp_options
             if name == label and ((reg_value >> 2) & 0x1F) == amplitude_code),
            None,
        )
        if voltage is not None:
            self.ctx.state.current_max_output_voltage = voltage
        self.ctx.state.current_max_output_reg_value = value

    def _apply_current_settings_snapshot(
        self,
        registers: dict[int, int],
        ram_values: list[int],
        analyzed: list[tuple[int, int, int]],
        *,
        waveform_recipe=None,
        waveform_recipes=None,
    ) -> None:
        previous_reading = getattr(self, "_reading_current_settings", False)
        self._reading_current_settings = True
        try:
            max_output = registers[0x14]
            if self.page_name == "full_mode":
                self._apply_full_reg14_settings(max_output)
            else:
                max_label = next(
                    (label for label, _voltage, reg_value in self.amp_options if reg_value == max_output),
                    f"0x{max_output:04X}",
                )
                self.max_voltage_var.set(max_label)
            self.duration_var.set(str((registers[0x15] & 0xFFFF) + 1))

            gain_label = "0-19V" if registers[0x0B] & (1 << 9) else "0-100V"
            polarity_label = "OUT-" if registers[0x0B] & (1 << 8) else "OUT+"
            press_voltage = self._code_to_voltage(registers[0x08] & 0x0FFF, gain_label, polarity_label)
            release_voltage = self._code_to_voltage(registers[0x09] & 0x0FFF, gain_label, polarity_label)
            self.press_threshold_var.set(self._format_current_voltage(press_voltage))
            self.release_threshold_var.set(self._format_current_voltage(release_voltage))
            if self.page_name == "full_mode":
                self.adc_threshold_h_var.set(self._format_current_voltage(press_voltage))
                self.adc_threshold_l_var.set(self._format_current_voltage(release_voltage))

            adc_filter = registers[0x0C]
            self.adc_average_time_var.set(self._adc_average_label((adc_filter >> 4) & 0x0F))
            self.adc_ovv_time_var.set(self._adc_ovv_label(adc_filter & 0x0F))
            self.button_adc_pol_var.set("OUT-" if registers[0x0B] & (1 << 8) else "OUT+")
            self.button_adc_gain_var.set(gain_label)

            press_config = registers[0x0F]
            release_config = registers[0x11]
            play_config = registers[0x13]
            self.press_repeat_count_var.set(str(((press_config & self.REPEAT_FIELD_MASK) >> 5) + 1))
            self.release_repeat_count_var.set(str(((release_config & self.REPEAT_FIELD_MASK) >> 5) + 1))
            self.play_repeat_count_var.set(str(((play_config & self.REPEAT_FIELD_MASK) >> 5) + 1))
            self.press_waveform_var.set(self._format_current_waveform_slice(press_config & self.WAVEFORM_FIELD_MASK))
            self.release_waveform_var.set(self._format_current_waveform_slice(release_config & self.WAVEFORM_FIELD_MASK))
            self.play_waveform_var.set(self._format_current_waveform_slice(play_config & self.WAVEFORM_FIELD_MASK))
            self.endless_play_var.set(bool(play_config & self.ENDLESS_PLAY_BIT))

            if self.page_name == "full_mode":
                full_gain = "0-19V" if registers[0x0B] & (1 << 9) else "0-100V"
                full_pol = "OUT-" if registers[0x0B] & (1 << 8) else "OUT+"
                self.adc_gain_var.set(full_gain)
                self.adc_pol_var.set(full_pol)
                self.adc_mode_var.set({0: "Direct", 1: "INPT", 2: "AUTO"}.get(registers[0x0B] & 0x03, "Direct"))
                represented_mask = (1 << 9) | (1 << 8) | 0x0003
                for event_key, _reg, source_bit in self.ADC_EVENT_CONFIGS:
                    represented_mask |= 1 << source_bit
                    self.adc_trigger_vars[event_key].set(bool(registers[0x0B] & (1 << source_bit)))
                self._full_adc_config_fixed_bits = registers[0x0B] & ~represented_mask
                for key, action, value in (
                    ("adc_over_h_posedge", press_config, press_config),
                    ("adc_over_l_posedge", release_config, release_config),
                ):
                    self.full_event_enable_vars[key].set(bool(value & (1 << 15)))
                    self.full_event_endless_vars[key].set(bool(value & self.ENDLESS_PLAY_BIT))
                    self.full_event_repeat_vars[key].set(str(((value & self.REPEAT_FIELD_MASK) >> 5) + 1))
                    self.full_event_wave_vars[key].set(
                        waveform_label(value & self.WAVEFORM_FIELD_MASK, full=True)
                    )
                self.play_waveform_var.set(
                    waveform_label(play_config & self.WAVEFORM_FIELD_MASK, full=True)
                )

            for pair, reg in enumerate(range(0x17, 0x1B)):
                value = registers[reg]
                even_index = pair * 2
                entries = (
                    ((value >> 3) & 0x1F, (value & 0x0007) + 1),
                    ((value >> 11) & 0x1F, ((value >> 8) & 0x0007) + 1),
                )
                for offset, (wave, repeat) in enumerate(entries):
                    index = even_index + offset
                    if self.page_name == "full_mode":
                        self.serial_wave_vars[index].set(self._format_serial_wave(wave))
                        self.serial_loop_vars[index].set(str(repeat))
                    waveform_page = getattr(self, "waveform_page", None)
                    if waveform_page is not None:
                        waveform_page.serial_repeat_vars[index].set(str(repeat))
                        waveform_page.serial_slice_vars[index].set(self._format_current_serial_slice(wave))

            if waveform_recipes is not None:
                self._apply_waveform_recipes_page_state(
                    waveform_recipes,
                    replace_all=True,
                )
            elif waveform_recipe is not None:
                self._apply_waveform_recipe_page_state(waveform_recipe)

            if ram_values:
                self.storage.clear()
                for slice_index, start_addr, end_addr in analyzed:
                    values = ram_values[start_addr:end_addr]
                    self.storage.add_or_replace(
                        slice_index,
                        start_addr,
                        end_addr,
                        len(values),
                        "chip",
                        values,
                    )

                waveform_page = getattr(self, "waveform_page", None)
                if waveform_page is not None:
                    waveform_page._analyzed_ranges = list(analyzed)
                    waveform_page._chip_values_cache = list(ram_values)
                    waveform_page._chip_voltages = waveform_page._chip_values_to_voltages(ram_values, analyzed)
                    waveform_page._plot_mode = "chip"
                    waveform_page._save_chip_cache(self.ctx.state.awp192x_device_addr, ram_values, analyzed)
                    waveform_page._render_analyzed_ranges_text(analyzed)
                    waveform_page._draw_plot(waveform_page._chip_voltages)
        finally:
            self._reading_current_settings = previous_reading

        self._refresh_threshold_register_value_labels()
        self._update_period_hint(write_register=False)
        self._capture_shared_settings()
        if self.page_name == "full_mode":
            self._capture_full_advanced_settings()

    @staticmethod
    def _normalize_waveform_recipe(
        recipe,
    ) -> Awp192xWaveformRecipe | Awp192xSineWaveformRecipe:
        if isinstance(recipe, (Awp192xWaveformRecipe, Awp192xSineWaveformRecipe)):
            return recipe
        raise ValueError("CFG_UNSUPPORTED: legacy AWP192x waveform recipes are not supported")

    def _remember_waveform_page_values(
        self,
        *,
        values: list[int],
        slice_index: int,
        start_addr: int,
        end_addr: int,
        waveform_type: str,
        power_on_recipe: Awp192xWaveformRecipe | Awp192xSineWaveformRecipe | None = None,
    ) -> None:
        self.storage.add_or_replace(
            slice_index,
            start_addr,
            end_addr,
            len(values),
            waveform_type,
            values,
            power_on_recipe=power_on_recipe,
        )
        page = self.waveform_page
        page._analyzed_ranges = [
            (index, existing_start, existing_end)
            for index, existing_start, existing_end in getattr(page, "_analyzed_ranges", [])
            if index != slice_index
        ]
        page._analyzed_ranges.append((slice_index, start_addr, end_addr))
        page._sync_read_slices_text()

    def _apply_waveform_recipe_page_state(self, recipe) -> None:
        self._apply_waveform_recipes_page_state((recipe,), replace_all=False)

    def _apply_waveform_recipes_page_state(
        self,
        recipes,
        *,
        replace_all: bool,
    ) -> None:
        normalized = tuple(self._normalize_waveform_recipe(recipe) for recipe in recipes)
        if not normalized:
            raise ValueError("AWP192x Button Runtime contains no waveform recipes")

        page = getattr(self, "waveform_page", None)
        if replace_all:
            self.storage.clear()
            if page is not None:
                page._analyzed_ranges = []

        generated: list[
            tuple[
                Awp192xWaveformRecipe | Awp192xSineWaveformRecipe,
                list[int],
                int,
                str,
            ]
        ] = []
        for recipe in normalized:
            values = list(generate_awp192x_waveform_points(recipe))
            end_addr = self.storage.make_end_addr(recipe.start_addr, len(values))
            waveform_type = "sine" if isinstance(recipe, Awp192xSineWaveformRecipe) else "sinc"
            generated.append((recipe, values, end_addr, waveform_type))

        if page is None:
            for recipe, values, end_addr, waveform_type in generated:
                self.storage.add_or_replace(
                    recipe.slice_index,
                    recipe.start_addr,
                    end_addr,
                    len(values),
                    waveform_type,
                    values,
                    power_on_recipe=recipe,
                )
            return

        recipe, values, end_addr, waveform_type = generated[0]
        page._imported_values = None
        page._preview_imported_waveform = False
        page._plot_mode = "current"
        page.waveform_type_var.set(waveform_type)
        page.slice_var.set(recipe.slice_index)
        page.start_addr_var.set(f"0x{recipe.start_addr:04X}")
        if isinstance(recipe, Awp192xSineWaveformRecipe):
            page.start_phase_var.set(str(recipe.start_phase_deg))
            page.end_phase_var.set(str(recipe.end_phase_deg))
            page.voltage_amplitude_var.set(
                self._format_current_voltage(recipe.amplitude_mv / 1000.0)
            )
            page.offset_var.set(
                self._format_current_voltage(recipe.offset_mv / 1000.0)
            )
        else:
            page.sinc_points1_var.set(str(recipe.points1))
            page.sinc_points2_var.set(str(recipe.points2))
            page.sinc_points3_var.set(str(recipe.points3))
            page.sinc_positive_voltage_var.set(
                self._format_current_voltage(recipe.positive_mv / 1000.0)
            )
            page.sinc_negative_voltage_var.set(
                self._format_current_voltage(recipe.negative_mv / 1000.0)
            )
        if hasattr(page, "fill_zero_tail_var"):
            page.fill_zero_tail_var.set(False)
        page.points_var.set(str(len(values)))
        page.end_addr_var.set(f"0x{end_addr:04X}")
        page._update_waveform_specific_fields()
        page._update_end_addr()
        page.points_var.set(str(len(values)))
        page.end_addr_var.set(f"0x{end_addr:04X}")
        for stored_recipe, stored_values, stored_end, stored_type in generated:
            self._remember_waveform_page_values(
                values=stored_values,
                slice_index=stored_recipe.slice_index,
                start_addr=stored_recipe.start_addr,
                end_addr=stored_end,
                waveform_type=stored_type,
                power_on_recipe=stored_recipe,
            )
        page._notify_metrics_changed()
        page._preview_waveform()

    def _apply_register_script_waveform_page_state(
        self,
        operations: list[RegisterScriptOperation],
    ) -> bool:
        imported = extract_awp192x_script_waveform(operations)
        if imported is None:
            return False
        if imported.recipe is not None:
            if getattr(self, "waveform_page", None) is not None:
                self._apply_waveform_recipe_page_state(imported.recipe)
            else:
                values = list(generate_awp192x_waveform_points(imported.recipe))
                end_addr = self.storage.make_end_addr(imported.recipe.start_addr, len(values))
                waveform_type = "sine" if isinstance(imported.recipe, Awp192xSineWaveformRecipe) else "sinc"
                self.storage.add_or_replace(
                    imported.recipe.slice_index,
                    imported.recipe.start_addr,
                    end_addr,
                    len(values),
                    waveform_type,
                    values,
                    power_on_recipe=imported.recipe,
                )
            self.storage.record_successful_write(imported.slice_index)
            return True

        page = getattr(self, "waveform_page", None)
        values = list(imported.values)
        if page is not None:
            page._imported_values = list(values)
            page._preview_imported_waveform = True
            page._plot_mode = "current"
            page.waveform_type_var.set("imported")
            page.slice_var.set(imported.slice_index)
            page.start_addr_var.set(f"0x{imported.start_addr:04X}")
            if hasattr(page, "fill_zero_tail_var"):
                page.fill_zero_tail_var.set(False)
            page.points_var.set(str(len(values)))
            page.end_addr_var.set(f"0x{imported.end_addr:04X}")
            update_fields = getattr(page, "_update_waveform_specific_fields", None)
            if callable(update_fields):
                update_fields()
            update_end = getattr(page, "_update_end_addr", None)
            if callable(update_end):
                update_end()
            page.points_var.set(str(len(values)))
            page.end_addr_var.set(f"0x{imported.end_addr:04X}")
        self.storage.add_or_replace(
            values=values,
            waveform_type="imported",
            index=imported.slice_index,
            start_addr=imported.start_addr,
            end_addr=imported.end_addr,
            points=len(values),
        )
        self.storage.record_successful_write(imported.slice_index)
        if page is not None:
            page._last_values = list(values)
            page._last_voltages = [page.engine.map_dac_to_voltage(value) for value in values]
            page._notify_metrics_changed()
            preview = getattr(page, "_preview_waveform", None)
            if callable(preview):
                preview()
        return True

    def _set_preset_buttons_state(self, state: str) -> None:
        if state == "disabled":
            self._hide_tooltips()
        for index in range(1, 4):
            button = self._buttons.get(f"button_mode_preset_{index}")
            if button is not None:
                button.configure(state=state)
        for key in ("save_to_board", "set_active", "apply_slot", "read_slot"):
            button = self._buttons.get(key)
            if button is not None:
                button.configure(state=state)

    def _parse_repeat_count(self, variable: tk.StringVar) -> int | None:
        try:
            count = int(variable.get().strip(), 0)
        except ValueError:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return None

        if not 1 <= count <= 512:
            messagebox.showerror(self.tr("error"), self.tr("repeat_count_range"))
            return None
        return count

    def _parse_waveform_slice(self, variable: tk.StringVar) -> int | None:
        text = variable.get().strip().lower()
        try:
            if text == "serial":
                index = self.SERIAL_WAVE_NUMBER
            elif text.startswith("slice"):
                index = int(text.replace("slice", "", 1), 10)
            else:
                index = int(text, 0)
        except ValueError:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return None

        if not (0 <= index <= 31):
            messagebox.showerror(self.tr("error"), self.tr("waveform_slice_range"))
            return None
        return index

    def _set_repeat_field(self, value: int, repeat_count: int) -> int:
        repeat_code = (repeat_count - 1) & 0x1FF
        return (value & ~self.REPEAT_FIELD_MASK) | (repeat_code << 5)

    def _set_waveform_field(self, value: int, waveform_slice: int) -> int:
        return (value & ~self.WAVEFORM_FIELD_MASK) | (waveform_slice & self.WAVEFORM_FIELD_MASK)

    def _set_repeat_and_waveform_fields(self, value: int, repeat_count: int, waveform_slice: int) -> int:
        value = self._set_waveform_field(value, waveform_slice)
        return self._set_repeat_field(value, repeat_count)

    def _serial_waveform_is_selected(self) -> bool:
        return any(
            variable is not None and variable.get().strip().lower() == "serial"
            for variable in (
                getattr(self, "press_waveform_var", None),
                getattr(self, "release_waveform_var", None),
                getattr(self, "play_waveform_var", None),
            )
        )

    def _apply_serial_waveform_config(self) -> bool:
        if not self._serial_waveform_is_selected():
            return True
        try:
            register_values = self.waveform_page.build_serial_register_values()
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return False
        for reg, value in register_values:
            if not self.write_reg(reg, value):
                return False
        return True

    def _apply_sense_event_config(self) -> bool:
        press_repeat = self._parse_repeat_count(self.press_repeat_count_var)
        release_repeat = self._parse_repeat_count(self.release_repeat_count_var)
        press_slice = self._parse_waveform_slice(self.press_waveform_var)
        release_slice = self._parse_waveform_slice(self.release_waveform_var)
        if None in (press_repeat, release_repeat, press_slice, release_slice):
            return False

        press_value = self._set_repeat_and_waveform_fields(
            self.ADC_DETECT_ENABLE,
            press_repeat,
            press_slice,
        )
        if not self.write_reg(0x0F, press_value):
            return False

        release_current = self.read_reg(0x11, show_error=False)
        release_base = 0 if release_current is None else release_current
        release_value = self._set_repeat_and_waveform_fields(
            release_base,
            release_repeat,
            release_slice,
        )
        return self.write_reg(0x11, release_value)

    def _apply_playback_options(self) -> bool:
        play_slice = self._parse_waveform_slice(self.play_waveform_var)
        if play_slice is None:
            return False

        current_mode = self.read_reg(0x13)
        if current_mode is None:
            return False

        current_mode = self._set_waveform_field(current_mode, play_slice)
        if self.endless_play_var.get():
            current_mode &= ~self.REPEAT_FIELD_MASK
            current_mode |= self.ENDLESS_PLAY_BIT
        else:
            play_repeat = self._parse_repeat_count(self.play_repeat_count_var)
            if play_repeat is None:
                return False
            current_mode = self._set_repeat_field(current_mode, play_repeat)
            current_mode &= ~self.ENDLESS_PLAY_BIT

        return self.write_reg(0x13, current_mode)

    def _write_button_output_timing_config(self) -> bool:
        try:
            max_output_reg_value = self._current_max_output_reg_value()
            duration = int(self.duration_var.get().strip(), 0)
            register_duration = encode_single_point_duration(duration)
        except Exception:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return False

        self.ctx.state.single_point_duration_us = duration
        if not self.write_reg(0x14, max_output_reg_value):
            return False
        if not self.write_reg(0x15, register_duration):
            return False
        return True

    def _write_button_playback_start_config(self) -> bool:
        if not self._write_button_output_timing_config():
            return False
        if not self._apply_serial_waveform_config():
            return False
        return self._apply_playback_options()

    def _finish_detection_stop_ui(self) -> None:
        self._direct_detection_active = False
        self._sense_status_active = False
        self._sense_timeout_mode = False
        self._cancel_sense_jobs()
        self._set_sense_status_color(self.STATUS_IDLE_COLOR)
        self._set_button_selected("start_detection", False)
        self._set_button_selected("start_play", False)

    def on_show(self) -> None:
        self._sync_awp192x_settings_from_model()
        if self.page_name == "button_mode":
            self._sync_button_step_progress_params()
            self._reconcile_rom_waveform_progress()
            self._mark_button_runtime_dirty()
            self._update_period_hint(write_register=bool(getattr(self, '_prepared_registers_pending', False)))
            self._prepared_registers_pending = False
            if not self._sense_status_active:
                self._set_sense_status_color(self.STATUS_IDLE_COLOR)
            return
        self.write_reg(0x0A, 0x0000, show_error=False)
        self.write_reg(0x13, 0x0000, show_error=False)
        self._clear_sense_playback_registers(show_error=False)
        self._write_endless_play_bit()
        self._write_button_adc_filter_config(show_error=False)
        self._on_voltage_changed()
        self._update_period_hint()
        if not self._sense_status_active:
            self._set_sense_status_color(self.STATUS_IDLE_COLOR)

    def start_detection(self) -> None:
        self._start_detection_direct()

    def _invalidate_detect_session(self) -> None:
        self._cancel_sense_jobs()
        self._detect_generation = getattr(self, "_detect_generation", 0) + 1
        self._detect_await_adc_busy = False
        self._detect_reconfigure_pending = False
        self._direct_detection_active = False
        self._sense_status_active = False
        self._detect_target_addr = None

    def _start_detection_direct(self) -> None:
        self._invalidate_detect_session()
        detect_generation = self._detect_generation
        has_button_progress = self.page_name == "button_mode" and self.step_progress is not None
        config_token = self._begin_button_progress_step("config") if has_button_progress else None
        config_status = None if has_button_progress else _begin_status(self, "preset")
        detection_token = self.step_progress.begin("detection") if has_button_progress else None

        def config_failed() -> None:
            if detect_generation != self._detect_generation:
                return
            setter = getattr(self, "_set_sense_status_color", None)
            if callable(setter):
                setter(self.STATUS_TIMEOUT_COLOR)
            if has_button_progress:
                self._mark_button_progress_step("config", "error", config_token)
                if detection_token is not None:
                    self.step_progress.mark("detection", "error", detection_token)
            else:
                _finish_status(self, config_status, success=False, error="configuration write failed")

        if not self._write_button_output_timing_config():
            config_failed()
            return
        if not self._apply_adc_threshold_config():
            config_failed()
            return
        if not self._apply_serial_waveform_config():
            config_failed()
            return
        if not self._apply_sense_event_config():
            config_failed()
            return
        if not self._write_button_adc_event_config(show_error=True):
            config_failed()
            return
        if detect_generation == self._detect_generation:
            if has_button_progress:
                self._mark_button_progress_step("config", "done", config_token)
            else:
                _finish_status(self, config_status, success=True)
        detection_status = (
            _begin_status(self, "detection", progress_token=detection_token)
            if has_button_progress
            else _begin_status(self, "detection")
        )
        started = self.write_reg(0x06, 0x0001)
        _finish_status(self, 
            detection_status,
            success=started,
            error="start detection write failed" if not started else "",
        )
        if not started:
            if detect_generation == self._detect_generation:
                self._set_sense_status_color(self.STATUS_TIMEOUT_COLOR)
                if has_button_progress:
                    self._mark_button_progress_step("detection", "error", detection_token)
            return
        self._direct_detection_active = True
        if detect_generation == self._detect_generation:
            if has_button_progress:
                self._mark_button_progress_step("detection", "done", detection_token)
        self._reset_direct_detect_cycle()
        self._detect_gui_state = "ready"
        self._set_button_selected("start_detection", True)
        self._set_sense_ready()

    def _button_legend_wave_label(self, value: Any) -> str:
        raw = str(value or "").strip()
        key = {
            "slice0": "adc_legend_wave_custom",
            "0x1f": "adc_legend_wave_rom_sine",
        }.get(raw.lower())
        return self.tr(key) if key else raw

    def _refresh_button_adc_legend_rules(self) -> None:
        if self.page_name != "button_mode":
            return
        legend = getattr(self, "adc_legend", None)
        if legend is None:
            return
        stage_data: list[dict[str, Any]] = []
        for index, (condition, threshold_var, wave_var, target) in enumerate((
            ("press", self.press_threshold_var, self.press_waveform_var, "Stage1"),
            ("release", self.release_threshold_var, self.release_waveform_var, "Stage0"),
        )):
            raw_threshold = str(threshold_var.get()).strip()
            try:
                numeric = float(raw_threshold)
                valid = math.isfinite(numeric)
            except (TypeError, ValueError, OverflowError):
                valid = False
            stage_data.append({
                "id": f"Stage{index}",
                "condition": condition if valid else "unknown",
                "comparator": ">=" if condition == "press" else "<=",
                "threshold": raw_threshold if valid else None,
                "unit": "V",
                "waveform": self._button_legend_wave_label(wave_var.get()),
                "target": target,
                "unused": False,
            })
        legend.set_rules(stage_data, {"Stage0": []})

    def on_hide(self) -> None:
        progress = getattr(self, "step_progress", None)
        if progress is not None:
            progress.reset_all()
        clear_receipts = getattr(self, "_clear_button_waveform_slice_receipts", None)
        if callable(clear_receipts):
            clear_receipts()
        if getattr(self, "_sense_blink_job", None) is not None:
            try:
                self.after_cancel(self._sense_blink_job)
            except Exception:
                pass
            self._sense_blink_job = None
        self._sense_live_generation = getattr(self, "_sense_live_generation", 0) + 1
        self._sense_live_state = "detecting" if self._sense_status_active else "idle"
        self._refresh_sense_legend()

    def stop_detection(self) -> None:
        status_token = _begin_status(self, "detection")
        self._invalidate_detect_session()
        success = self.write_reg(0x06, 0x0002)
        _finish_status(self, status_token, success=success, error="stop detection write failed" if not success else "")
        if not success:
            self._set_sense_status_color(self.STATUS_TIMEOUT_COLOR)
            return
        if self.page_name == "button_mode" and self.step_progress is not None:
            self.step_progress.invalidate_from("detection")
        self._clear_sense_playback_registers(show_error=False)
        self._finish_detection_stop_ui()

    def start_play(self) -> None:
        if not self.ctx.serial.is_connected:
            return
        status_token = _begin_status(self, "playback")
        if self._adc_trigger_read_active:
            self._read_and_update_button_adc(show_error=False)
        if not self._write_button_playback_start_config():
            _finish_status(self, status_token, success=False, error="playback configuration failed")
            return
        self.write_reg_async(
            0x06,
            0x0004,
            on_success=lambda: (
                self._set_button_selected("start_play", True),
                _finish_status(self, status_token, success=True),
            ),
            on_failure=lambda error: _finish_status(self, status_token, success=False, error=error),
        )

    def stop_play(self) -> None:
        if not self.ctx.serial.is_connected:
            return
        status_token = _begin_status(self, "playback")
        self.write_reg_async(
            0x06,
            0x0008,
            on_success=lambda: (
                self._set_button_selected("start_play", False),
                _finish_status(self, status_token, success=True),
            ),
            on_failure=lambda error: _finish_status(self, status_token, success=False, error=error),
        )

    def read_button_adc_value(self) -> None:
        self._read_and_update_button_adc(show_error=True)

    def toggle_button_adc_realtime(self) -> None:
        if self._adc_realtime_active:
            self._stop_button_adc_realtime()
            return

        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return

        self._adc_realtime_active = True
        self._button_adc_generation += 1
        self._refresh_adc_read_buttons()
        if self._adc_realtime_job is None:
            self._poll_button_adc_realtime()

    def toggle_button_adc_trigger_read(self) -> None:
        self._adc_trigger_read_active = not self._adc_trigger_read_active
        self._refresh_adc_read_buttons()

    def _code_to_voltage(self, code: int, gain_label: str, pol_label: str) -> float:
        full_scale = 18.0 if gain_label == "0-19V" else 100.0
        if code >= self.ADC_POS_ZERO:
            voltage = ((code - self.ADC_POS_ZERO) / (self.ADC_MAX_CODE - self.ADC_POS_ZERO)) * full_scale
        else:
            voltage = -((self.ADC_NEG_ZERO - code) / self.ADC_NEG_ZERO) * full_scale

        if pol_label == "OUT-":
            voltage = -voltage

        return voltage

    def _read_and_update_button_adc(self, *, show_error: bool = True) -> bool:
        value = self.read_reg(0x07, show_error=show_error)
        if value is None:
            return False
        self._set_button_adc_display(value)
        return True

    def _poll_button_adc_realtime(self) -> None:
        if self.page_name == "button_mode":
            self._poll_button_display()
            return
        self._adc_realtime_job = None
        if not self._adc_realtime_active:
            return
        if not self.ctx.serial.is_connected:
            self._stop_button_adc_realtime()
            return

        self._read_and_update_button_adc(show_error=False)
        self._adc_realtime_job = self.after(300, self._poll_button_adc_realtime)

    def _stop_button_adc_realtime(self) -> None:
        self._adc_realtime_active = False
        self._button_adc_generation += 1
        if self.page_name == "button_mode" and self._button_graph_active():
            self._refresh_adc_read_buttons()
            return
        if self._adc_realtime_job is not None:
            try:
                self.after_cancel(self._adc_realtime_job)
            except Exception:
                pass
        self._adc_realtime_job = None
        self._refresh_adc_read_buttons()

    def _refresh_adc_read_buttons(self) -> None:
        graph_btn = self._buttons.get("awp1221_adc_graph")
        if graph_btn is not None:
            graph_btn.configure(style="Selected.TButton" if self._button_graph_active() else "Secondary.TButton")
        realtime_btn = self._buttons.get("adc_read_realtime")
        if realtime_btn is not None:
            realtime_btn.configure(
                text=self.tr("adc_stop_realtime") if self._adc_realtime_active else self.tr("adc_read_realtime"),
                style="Selected.TButton" if self._adc_realtime_active else "Secondary.TButton",
            )

        trigger_btn = self._buttons.get("adc_read_on_trigger")
        if trigger_btn is not None:
            trigger_btn.configure(
                text=self.tr("adc_stop_trigger_read") if self._adc_trigger_read_active else self.tr("adc_read_on_trigger"),
                style="Selected.TButton" if self._adc_trigger_read_active else "Secondary.TButton",
            )

    def _prepare_playback_without_detection(self) -> bool:
        self._invalidate_detect_session()
        self.write_reg(0x06, 0x0002, show_error=False)
        if not self._clear_sense_playback_registers(show_error=True):
            return False

        self._sense_status_active = False
        self._sense_timeout_mode = False
        self._cancel_sense_jobs()
        self._set_sense_status_color(self.STATUS_IDLE_COLOR)
        self._set_button_selected("start_detection", False)
        return True

    def _clear_sense_playback_registers(self, *, show_error: bool = True) -> bool:
        for reg in (0x03, 0x0A, 0x0B, 0x0C, 0x0F, 0x11):
            if not self.write_reg(reg, 0x0000, show_error=show_error):
                return False
        return True

    def _clear_press_detect_enable(self, *, show_error: bool = True) -> bool:
        current = self.read_reg(0x0F, show_error=show_error)
        if current is None:
            return False
        if not (current & self.ADC_DETECT_ENABLE):
            return True
        return self.write_reg(0x0F, current & ~self.ADC_DETECT_ENABLE, show_error=show_error)

    def _set_sense_status_color(self, color: str) -> None:
        state = {
            self.STATUS_IDLE_COLOR: "idle",
            self.STATUS_READY_COLOR: "detecting",
            self.STATUS_PRESS_COLOR: "press",
            self.STATUS_RELEASE_COLOR: "release",
            self.STATUS_TIMEOUT_COLOR: "error",
        }.get(color, "idle")
        self._sense_live_state = state
        self._sense_live_generation = getattr(self, "_sense_live_generation", 0) + 1
        self._refresh_sense_legend()

    def _refresh_sense_legend(self) -> None:
        legend = getattr(self, "adc_legend", None)
        if legend is not None:
            legend.set_live(
                getattr(self, "_sense_live_state", "idle"),
                getattr(self, "_sense_live_detail", ""),
            )

    def _reset_adc_legend_lifecycle(self) -> None:
        self._invalidate_detect_session()
        self._sense_timeout_mode = False
        self._sense_status_active = False
        self._sense_live_detail = ""
        self._set_sense_status_color(self.STATUS_IDLE_COLOR)

    def _awp192x_target_is_connected(self) -> bool:
        serial = getattr(self.ctx, "serial", None)
        host = self._awp192x_settings_host()
        page_name = str(getattr(host, "page_name", "awp1921_1923"))
        selector = getattr(serial, "selected_port_for_page", None)
        if callable(selector):
            try:
                port = selector(page_name)
            except TypeError:
                port = selector()
            checker = getattr(serial, "is_port_connected", None)
            if port and callable(checker):
                try:
                    return bool(checker(port))
                except Exception:
                    pass
        return bool(getattr(serial, "is_connected", False))

    def _set_sense_ready(self) -> None:
        self._sense_status_active = True
        self._sense_timeout_mode = False
        self._cancel_sense_jobs()
        self._set_sense_status_color(self.STATUS_READY_COLOR)

    def _cancel_sense_jobs(self) -> None:
        self._sense_live_generation = getattr(self, "_sense_live_generation", 0) + 1
        for job in (
            getattr(self, "_sense_blink_job", None),
            getattr(self, "_sense_ready_poll_job", None),
        ):
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        self._sense_blink_job = None
        self._sense_ready_poll_job = None
        self._cancel_detect_status_poll()

    def _reset_direct_detect_cycle(self) -> None:
        self._detect_phase = "press"
        self._detect_gui_state = "idle"
        self._detect_reconfigure_pending = False
        self._detect_await_adc_busy = False
        self._detect_adc_busy_tries = 0

    def _detect_phase_register_values(self, *, wait_release: bool) -> tuple[int, int, int] | None:
        press_repeat = self._parse_repeat_count(self.press_repeat_count_var)
        release_repeat = self._parse_repeat_count(self.release_repeat_count_var)
        press_slice = self._parse_waveform_slice(self.press_waveform_var)
        release_slice = self._parse_waveform_slice(self.release_waveform_var)
        if None in (press_repeat, release_repeat, press_slice, release_slice):
            return None
        press_base = self._set_repeat_and_waveform_fields(0, press_repeat, press_slice)
        release_base = self._set_repeat_and_waveform_fields(0, release_repeat, release_slice)
        press_event = self._compose_button_adc_event_config()
        if wait_release:
            return (
                derive_wait_release_adc_event(press_event),
                press_base & ~self.ADC_DETECT_ENABLE,
                release_base | self.ADC_DETECT_ENABLE,
            )
        return (
            press_event,
            press_base | self.ADC_DETECT_ENABLE,
            release_base & ~self.ADC_DETECT_ENABLE,
        )

    def _apply_detect_phase(self, *, wait_release: bool) -> bool:
        values = self._detect_phase_register_values(wait_release=wait_release)
        if values is None:
            return False
        event_config, press_value, release_value = values
        if not self.write_reg(0x0B, event_config):
            return False
        if not self.write_reg(0x0F, press_value):
            return False
        if not self.write_reg(0x11, release_value):
            return False
        if not self.write_reg(0x03, self.ADC_D2_CONFIG):
            return False
        return self.write_reg(0x06, 0x0001)

    def _detect_playback_wait_ms(self) -> int:
        duration_us = int(self.duration_var.get().strip(), 0)
        if self._detect_phase == "release":
            count = int(self.press_repeat_count_var.get().strip(), 0)
            points = self._selected_slice_points(self.press_waveform_var)
        else:
            count = int(self.release_repeat_count_var.get().strip(), 0)
            points = self._selected_slice_points(self.release_waveform_var)
        if points is None or points < 1 or count < 1 or duration_us < 1:
            raise ValueError("playback wait requires points, repeat count, and duration")
        return max(1, (count * points * duration_us + 999) // 1000)

    def _schedule_detect_rearm(self) -> None:
        self._cancel_detect_status_poll()
        self._finish_detect_rearm()

    def _cancel_detect_status_poll(self) -> None:
        job = getattr(self, "_detect_poll_job", None)
        if job is None:
            return
        try:
            self.after_cancel(job)
        except Exception:
            pass
        self._detect_poll_job = None

    def _finish_detect_rearm(self, generation: int | None = None) -> None:
        if generation is not None and generation != getattr(self, "_detect_generation", 0):
            return
        self._detect_poll_job = None
        if not getattr(self, "_direct_detection_active", False):
            return
        wait_release = self._detect_phase == "release"
        if not self._apply_detect_phase(wait_release=wait_release):
            self.write_reg(0x06, 0x0001, show_error=False)
            return
        self._detect_reconfigure_pending = False
        self._detect_await_adc_busy = False
        self._detect_gui_state = "ready"
        self._sense_status_active = True
        self._sense_timeout_mode = False

    def _begin_wait_adc_busy(self) -> None:
        self._detect_gui_state = "wait_adc_busy"
        self._detect_await_adc_busy = True
        self._detect_adc_busy_tries = 0
        self._schedule_adc_busy_check()

    def _schedule_adc_busy_check(self) -> None:
        after = getattr(self, "after", None)
        if not callable(after) or getattr(self, "tk", None) is None:
            return
        generation = getattr(self, "_detect_generation", 0)
        self._detect_poll_job = after(
            self.DETECT_ADC_BUSY_RETRY_MS,
            self._verify_adc_busy,
            generation,
        )

    def _verify_adc_busy(self, generation: int | None = None) -> None:
        if generation is not None and generation != getattr(self, "_detect_generation", 0):
            return
        self._detect_poll_job = None
        if not getattr(self, "_direct_detection_active", False):
            return
        if getattr(self, "_detect_gui_state", "") != "wait_adc_busy":
            return
        if not getattr(self, "_detect_await_adc_busy", False):
            return
        status = self.read_reg(0x02, show_error=False)
        if status is not None and (status & 0x0001):
            self._detect_await_adc_busy = False
            self._detect_gui_state = "ready"
            self._set_sense_ready()
            return
        self._detect_adc_busy_tries = getattr(self, "_detect_adc_busy_tries", 0) + 1
        if self._detect_adc_busy_tries < 3:
            self._schedule_adc_busy_check()
            return
        self._enter_detect_fault()

    def _enter_detect_fault(self, message: str | None = None) -> None:
        self._cancel_sense_jobs()
        self._direct_detection_active = False
        self._detect_await_adc_busy = False
        self._detect_gui_state = "fault"
        self._sense_status_active = False
        self._set_sense_status_color(self.STATUS_TIMEOUT_COLOR)
        messagebox.showerror(self.tr("error"), message or self.tr("detect_adc_not_ready"))
        try:
            ok = self.write_reg(0x06, 0x0002, show_error=False)
        except Exception as exc:
            self._log_detect_fault(f"ADC stop failed: {exc}")
            return
        if not ok:
            self._log_detect_fault("ADC stop WREG 0x06=0x0002 failed")

    def _log_detect_fault(self, message: str) -> None:
        serial = getattr(getattr(self, "ctx", None), "serial", None)
        emit = getattr(serial, "emit_log", None)
        if callable(emit):
            emit(f"DETECT FAULT {message}")

    def _handle_direct_detect_status(self, status: int) -> bool:
        is_stage0 = bool(status & self.DETECT_STATUS_RELEASE)
        is_stage1 = bool(status & self.DETECT_STATUS_PRESS)

        if self._detect_phase == "press" and is_stage1:
            self._blink_sense_status(self.STATUS_PRESS_COLOR)
            if self._adc_trigger_read_active:
                self._read_and_update_button_adc(show_error=False)
            self._detect_phase = "release"
            self._detect_gui_state = "reconfiguring"
            self._detect_reconfigure_pending = True
            self._schedule_detect_rearm()
            return True
        if self._detect_phase == "release" and is_stage0:
            self._blink_sense_status(self.STATUS_RELEASE_COLOR)
            if self._adc_trigger_read_active:
                self._read_and_update_button_adc(show_error=False)
            self._detect_phase = "press"
            self._detect_gui_state = "reconfiguring"
            self._detect_reconfigure_pending = True
            self._schedule_detect_rearm()
            return True
        return False

    def _detect_session_is_current(self, generation: int | None = None, address: int | None = None) -> bool:
        if generation is not None and generation != getattr(self, "_detect_generation", 0):
            return False
        current = getattr(getattr(getattr(self, "ctx", None), "state", None), "awp192x_device_addr", None)
        if address is not None and current is not None and int(address) != int(current):
            return False
        return True

    def _handle_sense_status_log(self, line: str) -> None:
        generation = getattr(self, "_detect_generation", 0)
        address = getattr(getattr(getattr(self, "ctx", None), "state", None), "awp192x_device_addr", None)
        try:
            self.after(0, self._process_sense_status_log, line, generation, address)
        except Exception:
            pass

    def _process_sense_status_log(self, line: str, generation: int | None = None, address: int | None = None) -> None:
        if not self._detect_session_is_current(generation, address):
            return
        text = line.upper()
        if "RELEASE TIMEOUT R" in text:
            if getattr(self, "_direct_detection_active", False):
                return
            self._set_sense_timeout()
            return
        if not self._sense_status_active or self._sense_timeout_mode:
            return

        payload = self._extract_mcu_text_payload(line)
        if payload.startswith("REARM "):
            return
        if getattr(self, "_direct_detection_active", False) and self._is_nint_event_log(text, payload):
            self._on_direct_nint_event()
            return
        token = payload.split()[-1] if payload else ""
        if token == "P":
            self._blink_sense_status(self.STATUS_PRESS_COLOR)
            if self._adc_trigger_read_active:
                self._read_and_update_button_adc(show_error=False)
            return
        if token == "R":
            self._blink_sense_status(self.STATUS_RELEASE_COLOR)
            if self._adc_trigger_read_active:
                self._read_and_update_button_adc(show_error=False)

    def _is_gpioev_d2_log(self, text: str) -> bool:
        if "GPIOEV" not in text:
            return False
        return " D2 " in text or "GPIO0" in text or "SRC=INT0" in text

    def _is_nint_event_log(self, text: str, payload: str) -> bool:
        token = payload.split()[-1] if payload else ""
        if token in {"P", "R", "NINT"}:
            return True
        if "NINT" in text or "MCU I " in text:
            return True
        return self._is_gpioev_d2_log(text)

    def _on_direct_nint_event(self, text: str | None = None) -> None:
        if not getattr(self, "_direct_detection_active", False):
            return
        if getattr(self, "_detect_gui_state", "") == "fault":
            return
        status = self.read_reg(0x02, show_error=False)
        switched = False
        if status is not None:
            switched = bool(self._handle_direct_detect_status(status))
        if not switched:
            self.write_reg(0x06, 0x0001, show_error=False)

    def _extract_mcu_text_payload(self, line: str) -> str:
        match = re.search(r"MCU_TEXT\s+(.+)$", line, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().upper()
        return ""

    def _blink_sense_status(self, color: str) -> None:
        if self._sense_blink_job is not None:
            try:
                self.after_cancel(self._sense_blink_job)
            except Exception:
                pass
        self._set_sense_status_color(color)
        generation = self._sense_live_generation
        self._sense_blink_job = self.after(180, self._restore_sense_ready_after_blink, generation)

    def _restore_sense_ready_after_blink(self, generation: int | None = None) -> None:
        if generation is not None and generation != getattr(self, "_sense_live_generation", 0):
            return
        self._sense_blink_job = None
        if self._sense_status_active and not self._sense_timeout_mode:
            self._set_sense_status_color(self.STATUS_READY_COLOR)

    def _set_sense_timeout(self) -> None:
        self._sense_status_active = False
        self._sense_timeout_mode = True
        self._cancel_sense_jobs()
        self._set_sense_status_color(self.STATUS_TIMEOUT_COLOR)
        self._sense_ready_poll_job = self.after(150, self._poll_sense_ready_from_reg)

    def _poll_sense_ready_from_reg(self) -> None:
        self._sense_ready_poll_job = None
        if not self.ctx.serial.is_connected:
            self._reset_adc_legend_lifecycle()
            return

        value = self.read_reg(0x0F, show_error=False)
        if value is not None and (value & self.ADC_DETECT_ENABLE):
            self._set_sense_ready()
            return

        if self._sense_timeout_mode:
            self._sense_ready_poll_job = self.after(500, self._poll_sense_ready_from_reg)


class FullModePage(ButtonModePage):
    page_name = "full_mode"
    SERIAL_STOP_SUFFIX = "\u2014\u2014\u6ce2\u5f62\u7ec8\u6b62"

    ADC_AVERAGE_OPTIONS = (
        ("input", 0x00),
        ("1/2 input + 1/2 average", 0x01),
        ("1/4 input + 3/4 average", 0x02),
        ("1/8 input + 7/8 average", 0x03),
        ("1/16 input + 15/16 average", 0x04),
        ("1/32 input + 31/32 average", 0x05),
    )
    ADC_OVV_OPTIONS = (
        ("1 time", 0x0),
        ("2 times", 0x1),
        ("4 times", 0x2),
        ("8 times", 0x3),
        ("16 times", 0x4),
        ("32 times", 0x5),
        ("64 times", 0x6),
        ("128 times", 0x7),
        ("256 times", 0x8),
        ("512 times", 0x9),
        ("1023 times", 0xA),
    )

    ADC_TRIGGER_KEYS = (
        "adc_over_h",
        "adc_over_l",
        "adc_over_h_posedge",
        "adc_over_h_negedge",
        "adc_over_l_posedge",
        "adc_over_l_negedge",
    )
    ADC_EVENT_CONFIGS = (
        ("adc_over_h", 0x0D, 7),
        ("adc_over_l", 0x0E, 6),
        ("adc_over_h_posedge", 0x0F, 5),
        ("adc_over_h_negedge", 0x10, 4),
        ("adc_over_l_posedge", 0x11, 3),
        ("adc_over_l_negedge", 0x12, 2),
    )
    DAC_MODE_OPTIONS = (
        ("Direct", 0x0),
        ("FIFO", 0x1),
        ("SRAM/ROM", 0x2),
        ("SRAM Write", 0x3),
    )
    SLOW_TIME_OPTIONS = (
        ("0", 0x0),
        ("1", 0x1),
        ("3", 0x2),
        ("7", 0x3),
    )
    DATA_DEPTH_OPTIONS = (
        ("1024", 0x0),
        ("512", 0x1),
        ("256", 0x2),
        ("128", 0x3),
        ("64", 0x4),
        ("32", 0x5),
        ("16", 0x6),
        ("8", 0x7),
    )
    FIFO_DEPTH_OPTIONS = (
        ("1024", 0x0),
        ("960", 0x1),
        ("896", 0x2),
        ("832", 0x3),
        ("768", 0x4),
        ("704", 0x5),
        ("640", 0x6),
        ("576", 0x7),
        ("512", 0x8),
        ("448", 0x9),
        ("384", 0xA),
        ("320", 0xB),
        ("256", 0xC),
        ("192", 0xD),
        ("128", 0xE),
        ("64", 0xF),
    )
    REG_LOCK_OPTIONS = (
        ("0", 0x0),
        ("1", 0x1),
        ("2", 0x2),
        ("3", 0x3),
    )
    STATUS_FLAG_BITS = (
        ("status_bst_out_h", 15),
        ("status_on_play", 14),
        ("adc_over_h", 13),
        ("adc_over_l", 12),
        ("adc_over_h_posedge", 11),
        ("adc_over_h_negedge", 10),
        ("adc_over_l_posedge", 9),
        ("adc_over_l_negedge", 8),
        ("status_unv", 6),
        ("status_ovt", 4),
        ("status_half_empty", 3),
        ("status_fifo_full", 2),
        ("status_empty", 1),
        ("status_adc_busy", 0),
    )
    INT_MASK_BITS = STATUS_FLAG_BITS

    def _init_full_mode_vars(self) -> None:
        self.adc_threshold_h_var = tk.StringVar(value="100")
        self.adc_threshold_l_var = tk.StringVar(value="-100")
        self.full_adc_threshold_h_hex_var = tk.StringVar(value="")
        self.full_adc_threshold_l_hex_var = tk.StringVar(value="")
        self.adc_average_time_var = tk.StringVar(value=self.ADC_AVERAGE_OPTIONS[0][0])
        self.adc_ovv_time_var = tk.StringVar(value=self.ADC_OVV_OPTIONS[0][0])
        self.adc_gain_var = tk.StringVar(value="0-100V")
        self.adc_pol_var = tk.StringVar(value="OUT+")
        self.adc_mode_var = tk.StringVar(value="Direct")
        self.adc_trigger_vars = {key: tk.BooleanVar(value=False) for key in self.ADC_TRIGGER_KEYS}
        self.adc_trigger_vars["adc_over_h_posedge"].set(True)
        self._full_adc_config_fixed_bits = 0x0001
        self.play_waveform_var.set("ROM sin")
        self.play_repeat_count_var.set("1")
        self.endless_play_var.set(False)
        self.full_dac_mode_var = tk.StringVar(value="SRAM/ROM")
        self.full_slow_up_var = tk.StringVar(value=self.SLOW_TIME_OPTIONS[0][0])
        self.full_slow_down_var = tk.StringVar(value=self.SLOW_TIME_OPTIONS[0][0])
        self.full_half_cycle_var = tk.BooleanVar(value=False)
        self.full_phase_var = tk.StringVar(value="0")
        self.full_data_depth_var = tk.StringVar(value=self.DATA_DEPTH_OPTIONS[0][0])
        self.full_event_enable_vars = {key: tk.BooleanVar(value=False) for key, _reg, _bit in self.ADC_EVENT_CONFIGS}
        self.full_event_endless_vars = {key: tk.BooleanVar(value=False) for key, _reg, _bit in self.ADC_EVENT_CONFIGS}
        self.full_event_repeat_vars = {key: tk.StringVar(value="1") for key, _reg, _bit in self.ADC_EVENT_CONFIGS}
        self.full_event_wave_vars = {key: tk.StringVar(value="ROM sin") for key, _reg, _bit in self.ADC_EVENT_CONFIGS}
        self.full_event_enable_vars["adc_over_h_posedge"].set(True)
        self.status_value_var = tk.StringVar(value="")
        self.chip_id_var = tk.StringVar(value="")
        self.int_mask_vars = {key: tk.BooleanVar(value=False) for key, _bit in self.INT_MASK_BITS}
        self.fifo_depth_var = tk.StringVar(value=self.FIFO_DEPTH_OPTIONS[0][0])
        self.str_auto_addr_var = tk.BooleanVar(value=False)
        self.dis_plt_var = tk.BooleanVar(value=False)
        self.auto_end_var = tk.BooleanVar(value=True)
        self.auto_zero_var = tk.BooleanVar(value=True)
        self.reg_lock_var = tk.StringVar(value=self.REG_LOCK_OPTIONS[0][0])
        self.clk_sync_var = tk.BooleanVar(value=False)
        self.clk_out_var = tk.BooleanVar(value=False)
        self.sync_play_var = tk.BooleanVar(value=False)
        self.ram_address_var = tk.StringVar(value="0x0000")
        self.sram_data_var = tk.StringVar(value="0x0000")
        self.serial_wave_vars = [tk.StringVar(value=self._format_serial_wave(0x1F)) for _ in range(8)]
        self.serial_loop_vars = [tk.StringVar(value="1") for _ in range(8)]
        self._full_direct_labels: list[tuple[ttk.Label, str]] = []
        self._waveform_window: tk.Toplevel | None = None
        self._adc_detect_window: tk.Toplevel | None = None
        self._adc_detect_poll_job: str | None = None
        self._adc_detect_max_v: float | None = None
        self._adc_detect_min_v: float | None = None

    def _build_ui(self) -> None:
        for _ in self._build_ui_steps():
            pass

    def _build_ui_steps(self) -> Iterator[None]:
        yield
        self._init_full_mode_vars()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = self.make_title(self, "full_mode", show_logo=False)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        body = ttk.Frame(self, style="App.TFrame")
        yield
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3, uniform="full_mode_cols")
        body.columnconfigure(1, weight=4, uniform="full_mode_cols")
        body.columnconfigure(2, weight=3, uniform="full_mode_cols")
        body.rowconfigure(0, weight=1)

        left_panel = ttk.Frame(body, style="Panel.TFrame", padding=0)
        yield
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left_panel.rowconfigure(0, weight=1)
        left_panel.columnconfigure(0, weight=1)

        left_canvas = tk.Canvas(left_panel, highlightthickness=0, bd=0)
        self._full_left_canvas = left_canvas
        left_canvas.grid(row=0, column=0, sticky="nsew")
        yield
        left_scrollbar = ttk.Scrollbar(left_panel, orient="vertical", command=left_canvas.yview)
        left_scrollbar.grid(row=0, column=1, sticky="ns")
        left_canvas.configure(yscrollcommand=left_scrollbar.set)

        left = ttk.Frame(left_canvas, style="Panel.TFrame", padding=14)
        left_window = left_canvas.create_window((0, 0), window=left, anchor="nw")
        left_canvas.configure(background=self.colors["panel"])
        yield

        def _sync_left_scroll_region(_event: tk.Event) -> None:
            bbox = left_canvas.bbox("all")
            if not bbox or getattr(left_canvas, "_scroll_bbox", None) == bbox:
                return
            left_canvas._scroll_bbox = bbox
            left_canvas.configure(scrollregion=bbox)

        def _sync_left_width(event: tk.Event) -> None:
            width = int(event.width)
            if width <= 1:
                return
            try:
                current = int(float(left_canvas.itemcget(left_window, "width")))
            except (tk.TclError, TypeError, ValueError):
                current = -1
            if current != width:
                left_canvas.itemconfigure(left_window, width=width)

        def _on_left_mousewheel(event: tk.Event) -> None:
            left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        left.bind("<Configure>", _sync_left_scroll_region)
        left_canvas.bind("<Configure>", _sync_left_width)
        left_canvas.bind("<MouseWheel>", _on_left_mousewheel)
        yield
        left.bind("<MouseWheel>", _on_left_mousewheel)
        left.columnconfigure(1, weight=1)
        left.columnconfigure(4, weight=1)

        middle = ttk.Frame(body, style="Panel.TFrame", padding=14)
        middle.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        middle.columnconfigure(1, weight=1)
        yield
        middle.columnconfigure(4, weight=1)

        right = ttk.Frame(body, style="Panel.TFrame", padding=14)
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=2)
        yield

        right_settings_panel = ttk.Frame(right, style="Panel.TFrame", padding=0)
        right_settings_panel.grid(row=0, column=0, sticky="nsew")
        right_settings_panel.rowconfigure(0, weight=1)
        right_settings_panel.columnconfigure(0, weight=1)

        right_canvas = tk.Canvas(right_settings_panel, highlightthickness=0, bd=0)
        self._full_right_canvas = right_canvas
        yield
        right_canvas.grid(row=0, column=0, sticky="nsew")
        right_scrollbar = ttk.Scrollbar(right_settings_panel, orient="vertical", command=right_canvas.yview)
        right_scrollbar.grid(row=0, column=1, sticky="ns")
        right_canvas.configure(yscrollcommand=right_scrollbar.set)

        right_controls = ttk.Frame(right_canvas, style="Panel.TFrame")
        right_window = right_canvas.create_window((0, 0), window=right_controls, anchor="nw")
        yield
        right_canvas.configure(background=self.colors["panel"])

        def _sync_right_scroll_region(_event: tk.Event) -> None:
            bbox = right_canvas.bbox("all")
            if not bbox or getattr(right_canvas, "_scroll_bbox", None) == bbox:
                return
            right_canvas._scroll_bbox = bbox
            right_canvas.configure(scrollregion=bbox)

        def _sync_right_width(event: tk.Event) -> None:
            width = int(event.width)
            if width <= 1:
                return
            try:
                current = int(float(right_canvas.itemcget(right_window, "width")))
            except (tk.TclError, TypeError, ValueError):
                current = -1
            if current != width:
                right_canvas.itemconfigure(right_window, width=width)

        def _on_right_mousewheel(event: tk.Event) -> None:
            right_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        right_controls.bind("<Configure>", _sync_right_scroll_region)
        right_canvas.bind("<Configure>", _sync_right_width)
        yield
        right_canvas.bind("<MouseWheel>", _on_right_mousewheel)
        right_controls.bind("<MouseWheel>", _on_right_mousewheel)
        right_controls.columnconfigure(1, weight=1)
        right_controls.columnconfigure(4, weight=1)

        row = 0
        row = self._add_section_title(left, row, "full_mode_adc")
        yield
        self._add_full_single_entry(
            left,
            row,
            "adc_threshold_h",
            self.adc_threshold_h_var,
            unit="V",
            value_var=self.full_adc_threshold_h_hex_var,
        )
        row += 1
        self._add_full_single_entry(
            left,
            row,
            "adc_threshold_l",
            self.adc_threshold_l_var,
            unit="V",
            value_var=self.full_adc_threshold_l_hex_var,
        )
        row += 1
        self._add_full_single_combo(left, row, "adc_ovv_time", self.adc_ovv_time_var, [x[0] for x in self.ADC_OVV_OPTIONS])
        row += 1
        yield
        self._add_full_single_combo(left, row, "adc_gain", self.adc_gain_var, ["0-100V", "0-19V"])
        row += 1
        self._add_full_single_combo(
            left,
            row,
            "adc_average_time",
            self.adc_average_time_var,
            [x[0] for x in self.ADC_AVERAGE_OPTIONS],
        )
        row += 1
        self._add_full_single_combo(left, row, "adc_pol", self.adc_pol_var, ["OUT+", "OUT-"])
        row += 1
        yield
        self._add_full_single_combo(left, row, "adc_mode", self.adc_mode_var, ["Direct", "INPT", "AUTO"])
        row += 1
        row = self._add_adc_trigger_grid(left, row)
        row += 1

        adc_value_frame = ttk.Frame(left, style="Panel.TFrame")
        adc_value_frame.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(6, 0))
        yield
        adc_value_frame.columnconfigure(1, weight=1)

        adc_value_label = ttk.Label(adc_value_frame, style="Panel.TLabel")
        adc_value_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._labels["button_adc_value"] = adc_value_label

        ttk.Entry(adc_value_frame, textvariable=self.adc_value_var, state="readonly").grid(
            row=0,
            column=1,
            sticky="ew",
        )
        row += 1
        yield

        adc_btn_frame = ttk.Frame(left, style="Panel.TFrame")
        adc_btn_frame.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(10, 0))
        for index in range(3):
            adc_btn_frame.columnconfigure(index, weight=1)

        adc_start_btn = ttk.Button(adc_btn_frame, style="Primary.TButton", command=self.start_detection)
        adc_start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["adc_start"] = adc_start_btn
        yield

        adc_stop_btn = ttk.Button(adc_btn_frame, style="Secondary.TButton", command=self.stop_detection)
        adc_stop_btn.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self._buttons["adc_stop"] = adc_stop_btn

        adc_read_btn = ttk.Button(adc_btn_frame, style="Secondary.TButton", command=self.read_button_adc_value)
        adc_read_btn.grid(row=0, column=2, sticky="ew")
        self._buttons["adc_read_value"] = adc_read_btn
        yield

        row += 1

        adc_aux_frame = ttk.Frame(left, style="Panel.TFrame")
        adc_aux_frame.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        adc_aux_frame.columnconfigure(0, weight=1)
        adc_aux_frame.columnconfigure(1, weight=1)

        adc_calibe_btn = ttk.Button(adc_aux_frame, style="Secondary.TButton", command=self.adc_calibrate)
        yield
        adc_calibe_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["adc_calibe"] = adc_calibe_btn

        adc_detect_btn = ttk.Button(adc_aux_frame, style="Secondary.TButton", command=self.open_adc_detect_window)
        adc_detect_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._buttons["adc_detect_value"] = adc_detect_btn
        row += 1
        yield

        row = self._add_section_title(left, row, "full_mode_sense_events")
        row = self._add_full_sense_event_table(left, row)
        yield

        row = 0
        row = self._add_section_title(middle, row, "full_mode_basic")
        self._add_combo(middle, row, "max_output_voltage", self.max_voltage_var, [x[0] for x in self.amp_options])
        row += 1
        yield
        self._add_entry(middle, row, "single_point_duration", self.duration_var, unit="us")
        row += 1

        hint = ttk.Label(middle, textvariable=self.period_hint_var, style="Muted.TLabel", wraplength=300)
        hint.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        row += 1

        self._add_paired_combos(
            middle,
            row,
            "dac_mode",
            self.full_dac_mode_var,
            [x[0] for x in self.DAC_MODE_OPTIONS],
            "data_depth",
            self.full_data_depth_var,
            [x[0] for x in self.DATA_DEPTH_OPTIONS],
        )
        yield
        row += 1
        self._add_paired_combos(
            middle,
            row,
            "slow_up_time",
            self.full_slow_up_var,
            [x[0] for x in self.SLOW_TIME_OPTIONS],
            "slow_down_time",
            self.full_slow_down_var,
            [x[0] for x in self.SLOW_TIME_OPTIONS],
        )
        row += 1
        self._add_full_half_phase_row(middle, row)
        row += 1

        row = self._add_section_title(middle, row, "full_mode_playback")
        yield
        self._add_full_play_row(middle, row)
        row += 1

        self.endless_hint_label = ttk.Label(middle, style="Muted.TLabel", wraplength=360)
        self.endless_hint_label.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(0, 8))
        row += 1

        play_frame = ttk.Frame(middle, style="Panel.TFrame")
        yield
        play_frame.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        play_frame.columnconfigure(0, weight=1)
        play_frame.columnconfigure(1, weight=1)

        start_play = ttk.Button(play_frame, style="Primary.TButton", command=self.start_play)
        start_play.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["start_play"] = start_play
        yield

        stop_play = ttk.Button(play_frame, style="Secondary.TButton", command=self.stop_play)
        stop_play.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._buttons["stop_play"] = stop_play
        row += 1

        waveform_btn = ttk.Button(middle, style="Secondary.TButton", command=self.open_waveform_generator)
        waveform_btn.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(0, 8))
        yield
        self._buttons["waveform_generator"] = waveform_btn
        row += 1

        row = self._add_section_title(middle, row, "full_mode_serial")
        row = self._add_full_serial_controls(middle, row)

        right_row = 0
        right_row = self._add_section_title(right_controls, right_row, "full_mode_system")
        yield
        right_row = self._add_full_system_controls(right_controls, right_row)
        yield
        right_row = self._add_section_title(right_controls, right_row, "full_mode_interrupt")
        right_row = self._add_full_interrupt_controls(right_controls, right_row)
        yield
        right_row = self._add_section_title(right_controls, right_row, "full_mode_global")
        right_row = self._add_full_global_controls(right_controls, right_row)
        yield
        right_row = self._add_section_title(right_controls, right_row, "full_mode_memory")
        yield
        right_row = self._add_full_memory_controls(right_controls, right_row)

        log_frame = self.make_serial_log(right, height=18, subscribe=True)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

        self.duration_var.trace_add("write", lambda *_: self._update_period_hint())
        self.max_voltage_var.trace_add("write", lambda *_: self._on_voltage_changed())
        self.endless_play_var.trace_add("write", lambda *_: self._on_endless_play_changed())
        yield
        self.adc_average_time_var.trace_add("write", lambda *_: self._on_button_adc_filter_changed())
        self.adc_ovv_time_var.trace_add("write", lambda *_: self._on_button_adc_filter_changed())
        self.play_repeat_count_var.trace_add("write", lambda *_: self._on_full_manual_play_changed())
        self.play_waveform_var.trace_add("write", lambda *_: self._on_full_manual_play_changed())
        self.adc_threshold_h_var.trace_add("write", lambda *_: self._on_adc_threshold_changed())
        self.adc_threshold_l_var.trace_add("write", lambda *_: self._on_adc_threshold_changed())
        yield
        self.adc_average_time_var.trace_add("write", lambda *_: self._on_adc_average_changed())
        self.adc_ovv_time_var.trace_add("write", lambda *_: self._on_adc_ovv_changed())
        self.adc_gain_var.trace_add("write", lambda *_: self._on_adc_gain_pol_changed())
        self.adc_pol_var.trace_add("write", lambda *_: self._on_adc_gain_pol_changed())
        self.adc_mode_var.trace_add("write", lambda *_: self._on_adc_mode_changed())
        self.full_dac_mode_var.trace_add("write", lambda *_: self._on_voltage_changed())
        yield
        self.full_slow_up_var.trace_add("write", lambda *_: self._on_voltage_changed())
        self.full_slow_down_var.trace_add("write", lambda *_: self._on_voltage_changed())
        self.full_half_cycle_var.trace_add("write", lambda *_: self._on_voltage_changed())
        self.full_phase_var.trace_add("write", lambda *_: self._on_voltage_changed())
        self.full_data_depth_var.trace_add("write", lambda *_: self._on_full_data_depth_changed())
        for event_key in self.full_event_enable_vars:
            self.full_event_enable_vars[event_key].trace_add(
                "write",
                lambda *_args, key=event_key: self._on_full_sense_event_changed(key),
            )
            self.full_event_endless_vars[event_key].trace_add(
                "write",
                lambda *_args, key=event_key: self._on_full_sense_event_changed(key),
            )
            self.full_event_repeat_vars[event_key].trace_add(
                "write",
                lambda *_args, key=event_key: self._on_full_sense_event_changed(key),
            )
            self.full_event_wave_vars[event_key].trace_add(
                "write",
                lambda *_args, key=event_key: self._on_full_sense_event_changed(key),
            )
        yield
        for trigger_var in self.adc_trigger_vars.values():
            trigger_var.trace_add("write", lambda *_: self._on_adc_inpt_source_changed())
        yield
        self.refresh_language()
        yield
        self._refresh_full_adc_threshold_register_value_labels()
        self._on_endless_play_changed()
        yield
        self._initialize_awp192x_settings_model()
        self._attach_shared_settings_traces()

    def _add_section_title(self, parent: tk.Misc, row: int, key: str) -> int:
        title_options = {
            "full_mode_basic": {
                "step": 1,
                "step_total": 4,
                "help_key": "awp192x_help_full_basic",
            },
            "full_mode_adc": {
                "step": 2,
                "step_total": 4,
                "help_key": "awp192x_help_full_adc",
            },
            "full_mode_sense_events": {
                "step": 3,
                "step_total": 4,
                "help_key": "awp192x_help_full_events",
            },
            "full_mode_playback": {
                "step": 4,
                "step_total": 4,
                "help_key": "awp192x_help_full_playback",
            },
            "full_mode_serial": {
                "step": 4,
                "step_total": 4,
                "role": "advanced",
                "help_key": "awp192x_help_full_serial",
            },
            "full_mode_system": {
                "step": 4,
                "step_total": 4,
                "role": "feedback",
                "help_key": "awp192x_help_full_system",
            },
            "full_mode_interrupt": {
                "role": "advanced",
                "help_key": "awp192x_help_full_interrupt",
            },
            "full_mode_global": {
                "role": "advanced",
                "help_key": "awp192x_help_full_global",
            },
            "full_mode_memory": {
                "role": "advanced",
                "help_key": "awp192x_help_full_memory",
            },
        }
        _title_row, _label = self.make_module_title_row(
            parent,
            key,
            **title_options.get(key, {}),
            row=row,
            columnspan=6,
            pady=(10 if row else 0, 4),
        )
        return row + 1

    def _add_paired_entries(
        self,
        parent: tk.Misc,
        row: int,
        key1: str,
        variable1: tk.StringVar,
        key2: str,
        variable2: tk.StringVar,
        *,
        unit1: str = "",
        unit2: str = "",
    ) -> None:
        label1 = ttk.Label(parent, style="Panel.TLabel")
        label1.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels[key1] = label1

        ttk.Entry(parent, textvariable=variable1, width=10).grid(row=row, column=1, sticky="ew", pady=7)
        if unit1:
            ttk.Label(parent, text=unit1, style="Muted.TLabel").grid(row=row, column=2, sticky="w", padx=(6, 12))

        label2 = ttk.Label(parent, style="Panel.TLabel")
        label2.grid(row=row, column=3, sticky="w", pady=7, padx=(12, 8))
        self._labels[key2] = label2

        ttk.Entry(parent, textvariable=variable2, width=10).grid(row=row, column=4, sticky="ew", pady=7)
        if unit2:
            ttk.Label(parent, text=unit2, style="Muted.TLabel").grid(row=row, column=5, sticky="w", padx=(6, 0))

    def _add_paired_combos(
        self,
        parent: tk.Misc,
        row: int,
        key1: str,
        variable1: tk.StringVar,
        values1: list[str],
        key2: str,
        variable2: tk.StringVar,
        values2: list[str],
    ) -> None:
        label1 = ttk.Label(parent, style="Panel.TLabel")
        label1.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels[key1] = label1

        combo1 = ttk.Combobox(parent, textvariable=variable1, values=values1, state="readonly")
        combo1.grid(row=row, column=1, columnspan=2, sticky="ew", pady=7, padx=(0, 12))

        label2 = ttk.Label(parent, style="Panel.TLabel")
        label2.grid(row=row, column=3, sticky="w", pady=7, padx=(12, 8))
        self._labels[key2] = label2

        combo2 = ttk.Combobox(parent, textvariable=variable2, values=values2, state="readonly")
        combo2.grid(row=row, column=4, columnspan=2, sticky="ew", pady=7)

    def _add_full_single_entry(
        self,
        parent: tk.Misc,
        row: int,
        key: str,
        variable: tk.StringVar,
        *,
        unit: str = "",
        value_var: tk.StringVar | None = None,
    ) -> None:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=row, column=0, columnspan=6, sticky="ew", pady=5)
        frame.columnconfigure(1, weight=1)

        label = ttk.Label(frame, style="Panel.TLabel", width=18)
        label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._labels[key] = label

        ttk.Entry(frame, textvariable=variable, width=16).grid(row=0, column=1, sticky="ew")
        if unit:
            ttk.Label(frame, text=unit, style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=(8, 0))
        if value_var is not None:
            ttk.Label(frame, textvariable=value_var, style="Muted.TLabel", width=7).grid(
                row=0,
                column=3,
                sticky="w",
                padx=(8, 0),
            )

    def _add_full_single_combo(
        self,
        parent: tk.Misc,
        row: int,
        key: str,
        variable: tk.StringVar,
        values: list[str],
    ) -> None:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=row, column=0, columnspan=6, sticky="ew", pady=5)
        frame.columnconfigure(1, weight=1)

        label = ttk.Label(frame, style="Panel.TLabel", width=18)
        label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._labels[key] = label

        ttk.Combobox(
            frame,
            textvariable=variable,
            values=values,
            state="readonly",
            width=16,
        ).grid(row=0, column=1, sticky="ew")

    def _add_adc_trigger_grid(self, parent: tk.Misc, row: int) -> int:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, columnspan=6, sticky="w", pady=(7, 4), padx=(0, 8))
        self._labels["adc_inpt_stop_source"] = label

        grid = ttk.Frame(parent, style="Panel.TFrame")
        grid.grid(row=row + 1, column=0, columnspan=6, sticky="ew", pady=(0, 4))
        grid.columnconfigure(0, weight=1)

        for index, key in enumerate(self.ADC_TRIGGER_KEYS):
            check = ttk.Checkbutton(grid, variable=self.adc_trigger_vars[key])
            check.grid(row=index, column=0, sticky="ew", pady=2, padx=(0, 10))
            self._checks[key] = check

        return row + 2

    def _full_wave_options(self) -> list[str]:
        return [f"slice{i}" for i in range(30)] + ["serial", "ROM sin"]

    def _add_full_half_phase_row(self, parent: tk.Misc, row: int) -> None:
        half_label = ttk.Label(parent, style="Panel.TLabel")
        half_label.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels["half_cycle_en"] = half_label

        half_check = ttk.Checkbutton(parent, variable=self.full_half_cycle_var)
        half_check.grid(row=row, column=1, sticky="w", pady=7, padx=(0, 12))

        phase_label = ttk.Label(parent, style="Panel.TLabel")
        phase_label.grid(row=row, column=3, sticky="w", pady=7, padx=(12, 8))
        self._labels["phase"] = phase_label

        ttk.Combobox(
            parent,
            textvariable=self.full_phase_var,
            values=["0", "180"],
            state="readonly",
        ).grid(row=row, column=4, columnspan=2, sticky="ew", pady=7)

    def _add_full_play_row(self, parent: tk.Misc, row: int) -> None:
        wave_label = ttk.Label(parent, style="Panel.TLabel")
        wave_label.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels["manual_waveform"] = wave_label

        ttk.Combobox(
            parent,
            textvariable=self.play_waveform_var,
            values=self._full_wave_options(),
            state="readonly",
        ).grid(row=row, column=1, columnspan=2, sticky="ew", pady=7, padx=(0, 12))

        repeat_label = ttk.Label(parent, style="Panel.TLabel")
        repeat_label.grid(row=row, column=3, sticky="w", pady=7, padx=(12, 8))
        self._labels["play_repeat_count"] = repeat_label

        repeat_entry = ttk.Entry(parent, textvariable=self.play_repeat_count_var, width=10)
        repeat_entry.grid(row=row, column=4, sticky="ew", pady=7)
        self._repeat_entries["play_repeat_count"] = repeat_entry

        endless_check = ttk.Checkbutton(parent, variable=self.endless_play_var)
        endless_check.grid(row=row, column=5, sticky="w", pady=7, padx=(8, 0))
        self._checks["endless_play"] = endless_check

    def _add_full_sense_event_table(self, parent: tk.Misc, row: int) -> int:
        table = ttk.Frame(parent, style="Panel.TFrame")
        table.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(4, 0))
        table.columnconfigure(2, weight=1)
        table.columnconfigure(3, weight=1)

        headers = ("adc_trigger_type", "event_enable", "manual_waveform", "event_loop_count", "event_endless")
        for column, key in enumerate(headers):
            label = ttk.Label(table, style="Muted.TLabel")
            label.grid(row=0, column=column, sticky="w", padx=(0, 8), pady=(0, 4))
            self._labels[f"full_event_header_{key}"] = label

        for event_row, (event_key, _reg, _source_bit) in enumerate(self.ADC_EVENT_CONFIGS, start=1):
            event_label = ttk.Label(table, style="Panel.TLabel")
            event_label.grid(row=event_row, column=0, sticky="w", padx=(0, 8), pady=4)
            self._labels[f"full_event_label_{event_key}"] = event_label

            ttk.Checkbutton(table, variable=self.full_event_enable_vars[event_key]).grid(
                row=event_row,
                column=1,
                sticky="w",
                padx=(0, 8),
                pady=4,
            )
            ttk.Combobox(
                table,
                textvariable=self.full_event_wave_vars[event_key],
                values=self._full_wave_options(),
                state="readonly",
                width=10,
            ).grid(row=event_row, column=2, sticky="ew", padx=(0, 8), pady=4)
            ttk.Entry(table, textvariable=self.full_event_repeat_vars[event_key], width=8).grid(
                row=event_row,
                column=3,
                sticky="ew",
                padx=(0, 8),
                pady=4,
            )
            ttk.Checkbutton(table, variable=self.full_event_endless_vars[event_key]).grid(
                row=event_row,
                column=4,
                sticky="w",
                pady=4,
            )

        return row + 1

    def _add_full_system_controls(self, parent: tk.Misc, row: int) -> int:
        status_label = ttk.Label(parent, style="Panel.TLabel")
        status_label.grid(row=row, column=0, sticky="w", pady=6, padx=(0, 8))
        self._labels["full_status"] = status_label
        ttk.Entry(parent, textvariable=self.status_value_var, state="readonly").grid(
            row=row,
            column=1,
            columnspan=5,
            sticky="ew",
            pady=6,
        )
        row += 1
        status_btn = ttk.Button(parent, style="Secondary.TButton", command=self.read_full_status)
        status_btn.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(0, 6))
        self._buttons["full_read_status"] = status_btn
        row += 1

        chip_label = ttk.Label(parent, style="Panel.TLabel")
        chip_label.grid(row=row, column=0, sticky="w", pady=6, padx=(0, 8))
        self._labels["chip_id"] = chip_label
        ttk.Entry(parent, textvariable=self.chip_id_var, state="readonly").grid(
            row=row,
            column=1,
            columnspan=5,
            sticky="ew",
            pady=6,
        )
        row += 1
        chip_btn = ttk.Button(parent, style="Secondary.TButton", command=self.read_chip_id)
        chip_btn.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(0, 0))
        self._buttons["full_read_chip_id"] = chip_btn
        return row + 1

    def _add_full_interrupt_controls(self, parent: tk.Misc, row: int) -> int:
        grid = ttk.Frame(parent, style="Panel.TFrame")
        grid.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(4, 8))
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        for index, (key, _bit) in enumerate(self.INT_MASK_BITS):
            check = ttk.Checkbutton(grid, variable=self.int_mask_vars[key])
            check.grid(row=index // 2, column=(index % 2) * 2, sticky="w", pady=2, padx=(0, 4))
            label = ttk.Label(grid, text=self.tr(key), style="Muted.TLabel")
            label.grid(
                row=index // 2,
                column=(index % 2) * 2 + 1,
                sticky="w",
                pady=2,
                padx=(0, 10),
            )
            self._full_direct_labels.append((label, key))
        row += 1

        btn_frame = ttk.Frame(parent, style="Panel.TFrame")
        btn_frame.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(2, 6))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        write_btn = ttk.Button(btn_frame, style="Secondary.TButton", command=self.write_interrupt_mask)
        write_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["full_write_int_mask"] = write_btn

        read_btn = ttk.Button(btn_frame, style="Secondary.TButton", command=self.read_interrupt_mask)
        read_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._buttons["full_read_int_mask"] = read_btn
        return row + 1

    def _add_full_global_controls(self, parent: tk.Misc, row: int) -> int:
        self._add_paired_combos(
            parent,
            row,
            "fifo_depth",
            self.fifo_depth_var,
            [x[0] for x in self.FIFO_DEPTH_OPTIONS],
            "reg_lock",
            self.reg_lock_var,
            [x[0] for x in self.REG_LOCK_OPTIONS],
        )
        row += 1

        checks = (
            ("str_auto_addr", self.str_auto_addr_var),
            ("dis_plt", self.dis_plt_var),
            ("auto_end", self.auto_end_var),
            ("auto_zero", self.auto_zero_var),
            ("clk_sync", self.clk_sync_var),
            ("clk_out", self.clk_out_var),
            ("sync_play", self.sync_play_var),
        )
        grid = ttk.Frame(parent, style="Panel.TFrame")
        grid.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(4, 8))
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)
        for index, (key, variable) in enumerate(checks):
            check = ttk.Checkbutton(grid, variable=variable)
            check.grid(row=index // 2, column=(index % 2) * 2, sticky="w", pady=2, padx=(0, 4))
            label = ttk.Label(grid, text=self.tr(key), style="Muted.TLabel")
            label.grid(
                row=index // 2,
                column=(index % 2) * 2 + 1,
                sticky="w",
                pady=2,
                padx=(0, 10),
            )
            self._full_direct_labels.append((label, key))
        row += 1

        btn_frame = ttk.Frame(parent, style="Panel.TFrame")
        btn_frame.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(2, 6))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        write_btn = ttk.Button(btn_frame, style="Secondary.TButton", command=self.write_global_config)
        write_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["full_write_global_config"] = write_btn

        read_btn = ttk.Button(btn_frame, style="Secondary.TButton", command=self.read_global_config)
        read_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._buttons["full_read_global_config"] = read_btn
        return row + 1

    def _add_full_memory_controls(self, parent: tk.Misc, row: int) -> int:
        ram_label = ttk.Label(parent, style="Panel.TLabel")
        ram_label.grid(row=row, column=0, sticky="w", pady=6, padx=(0, 8))
        self._labels["ram_address"] = ram_label
        ttk.Entry(parent, textvariable=self.ram_address_var, width=12).grid(
            row=row,
            column=1,
            columnspan=5,
            sticky="ew",
            pady=6,
        )
        row += 1

        sram_label = ttk.Label(parent, style="Panel.TLabel")
        sram_label.grid(row=row, column=0, sticky="w", pady=6, padx=(0, 8))
        self._labels["sram_data"] = sram_label
        ttk.Entry(parent, textvariable=self.sram_data_var, width=12).grid(
            row=row,
            column=1,
            columnspan=5,
            sticky="ew",
            pady=6,
        )
        row += 1

        btn_frame = ttk.Frame(parent, style="Panel.TFrame")
        btn_frame.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(2, 6))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        buttons = (
            ("full_write_ram_addr", self.write_ram_address),
            ("full_read_ram_addr", self.read_ram_address),
            ("full_write_sram_data", self.write_sram_data),
            ("full_read_sram_data", self.read_sram_data),
        )
        for index, (key, command) in enumerate(buttons):
            button = ttk.Button(btn_frame, style="Secondary.TButton", command=command)
            button.grid(
                row=index // 2,
                column=index % 2,
                sticky="ew",
                padx=(0, 6) if index % 2 == 0 else (6, 0),
                pady=(0, 6) if index < 2 else (0, 0),
            )
            self._buttons[key] = button
        return row + 1

    def _serial_wave_options(self) -> list[str]:
        return [f"slice{i}" for i in range(30)] + [
            self._format_serial_wave(0x1E),
            self._format_serial_wave(0x1F),
        ]

    def _add_full_serial_controls(self, parent: tk.Misc, row: int) -> int:
        table = ttk.Frame(parent, style="Panel.TFrame")
        table.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(4, 8))
        table.columnconfigure(1, weight=1)
        table.columnconfigure(2, weight=1)

        headers = ("serial_index", "serial_wave", "serial_loop")
        for column, key in enumerate(headers):
            label = ttk.Label(table, style="Muted.TLabel")
            label.grid(row=0, column=column, sticky="w", padx=(0, 8), pady=(0, 4))
            self._labels[f"full_serial_header_{key}"] = label

        for index in range(8):
            ttk.Label(table, text=f"serial{index}", style="Panel.TLabel").grid(
                row=index + 1,
                column=0,
                sticky="w",
                padx=(0, 8),
                pady=3,
            )
            ttk.Combobox(
                table,
                textvariable=self.serial_wave_vars[index],
                values=self._serial_wave_options(),
                state="readonly",
                width=10,
            ).grid(row=index + 1, column=1, sticky="ew", padx=(0, 8), pady=3)
            ttk.Entry(table, textvariable=self.serial_loop_vars[index], width=8).grid(
                row=index + 1,
                column=2,
                sticky="ew",
                pady=3,
            )
        row += 1

        btn_frame = ttk.Frame(parent, style="Panel.TFrame")
        btn_frame.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(2, 6))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        write_btn = ttk.Button(btn_frame, style="Secondary.TButton", command=self.write_serial_config)
        write_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["full_write_serial_config"] = write_btn

        read_btn = ttk.Button(btn_frame, style="Secondary.TButton", command=self.read_serial_config)
        read_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._buttons["full_read_serial_config"] = read_btn
        return row + 1

    def refresh_language(self) -> None:
        super().refresh_language()
        header_names = {
            "full_event_header_adc_trigger_type": "adc_trigger_type",
            "full_event_header_event_enable": "event_enable",
            "full_event_header_manual_waveform": "manual_waveform",
            "full_event_header_event_loop_count": "event_loop_count",
            "full_event_header_event_endless": "event_endless",
        }
        for label_key, tr_key in header_names.items():
            label = self._labels.get(label_key)
            if label is not None:
                label.configure(text=self.tr(tr_key))
        for event_key, _reg, _source_bit in self.ADC_EVENT_CONFIGS:
            label = self._labels.get(f"full_event_label_{event_key}")
            if label is not None:
                label.configure(text=self.tr(event_key))
        serial_headers = {
            "full_serial_header_serial_index": "serial_index",
            "full_serial_header_serial_wave": "serial_wave",
            "full_serial_header_serial_loop": "serial_loop",
        }
        for label_key, tr_key in serial_headers.items():
            label = self._labels.get(label_key)
            if label is not None:
                label.configure(text=self.tr(tr_key))
        for label, key in getattr(self, "_full_direct_labels", []):
            label.configure(text=self.tr(key))
        attach_glossary_tooltips(self, self.tr, self._tooltips)



    def _style_only_action(self) -> None:
        pass

    def open_waveform_generator(self) -> None:
        if self._waveform_window is not None and self._waveform_window.winfo_exists():
            self._waveform_window.lift()
            self._waveform_window.focus_force()
            return

        window = tk.Toplevel(self)
        window.title(self.tr("waveform_generator"))
        fit_toplevel_to_screen(
            window,
            preferred_width=1120,
            preferred_height=720,
            minimum_width=480,
            minimum_height=320,
        )
        window.configure(bg=self.colors["bg"])
        window.rowconfigure(0, weight=1)
        window.columnconfigure(0, weight=1)
        window.protocol("WM_DELETE_WINDOW", self._close_waveform_window)

        self._waveform_window = window
        scroll_area = VerticalScrollArea(
            window,
            self.ctx,
            horizontal=True,
            min_content_width=920,
        )
        scroll_area.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self._waveform_scroll_area = scroll_area
        self.waveform_page = WaveformGeneratorPage(
            scroll_area.content,
            self.ctx,
            embedded=False,
            storage=self.storage,
        )
        self.waveform_page.set_write_observer(
            self._begin_waveform_write_observation,
            self._on_button_waveform_write_result,
        )
        self.waveform_page.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._syncing_chip_settings = True
        try:
            for index, (wave, repeat) in enumerate(zip(self.serial_wave_vars, self.serial_loop_vars)):
                self.waveform_page.serial_slice_vars[index].set(wave.get())
                self.waveform_page.serial_repeat_vars[index].set(repeat.get())
            model = getattr(self, "_chip_settings_model", None)
            editor_state = model.advanced.get("waveform_editor") if model is not None else None
            if isinstance(editor_state, dict):
                self._apply_full_waveform_editor_settings(editor_state)
        finally:
            self._syncing_chip_settings = False
        for variable in [*self.waveform_page.serial_slice_vars, *self.waveform_page.serial_repeat_vars]:
            variable.trace_add(
                "write",
                lambda *_args, page=self.waveform_page: self._on_full_editor_serial_changed(page),
            )
        for variable in (
            self.waveform_page.waveform_type_var,
            self.waveform_page.slice_var,
            self.waveform_page.points_var,
            self.waveform_page.start_phase_var,
            self.waveform_page.end_phase_var,
            self.waveform_page.offset_var,
            self.waveform_page.voltage_amplitude_var,
            self.waveform_page.sinc_positive_voltage_var,
            self.waveform_page.sinc_negative_voltage_var,
            self.waveform_page.sinc_points1_var,
            self.waveform_page.sinc_points2_var,
            self.waveform_page.sinc_points3_var,
            self.waveform_page.start_addr_var,
            self.waveform_page.fill_zero_tail_var,
        ):
            variable.trace_add("write", lambda *_args: self._capture_full_advanced_settings())
        scroll_area.content.columnconfigure(0, weight=1)
        self.waveform_page.metrics_revision_var.trace_add(
            "write",
            lambda *_: self._update_period_hint(write_register=False),
        )
        self.waveform_page.refresh_language()
        self._update_period_hint(write_register=False)

    def _close_waveform_window(self) -> None:
        if hasattr(self, "waveform_page"):
            if self.page_name == "full_mode":
                self._capture_shared_settings()
                self._capture_full_advanced_settings()
            delattr(self, "waveform_page")
        if self._waveform_window is not None:
            try:
                self._waveform_window.destroy()
            except Exception:
                pass
        self._waveform_window = None
        self._update_period_hint(write_register=False)

    def _option_code(self, selected: str, options: tuple[tuple[str, int], ...], default: int = 0) -> int:
        return next((value for label, value in options if label == selected), default)

    def _selected_output_vpk_code(self) -> int:
        selected = self.max_voltage_var.get()
        for label, _voltage, reg_value in self.amp_options:
            if label == selected:
                return (reg_value >> 2) & 0x1F
        return 0

    def _compose_full_reg14(self) -> int:
        value = 0
        value |= (self._option_code(self.full_slow_up_var.get(), self.SLOW_TIME_OPTIONS) & 0x03) << 14
        value |= (self._option_code(self.full_slow_down_var.get(), self.SLOW_TIME_OPTIONS) & 0x03) << 12
        value |= (1 if self.full_half_cycle_var.get() else 0) << 11
        value |= (1 if self.full_phase_var.get() == "180" else 0) << 10
        value |= (self._option_code(self.full_data_depth_var.get(), self.DATA_DEPTH_OPTIONS) & 0x07) << 7
        value |= (self._selected_output_vpk_code() & 0x1F) << 2
        value |= self._option_code(self.full_dac_mode_var.get(), self.DAC_MODE_OPTIONS, 0x2) & 0x03
        return value & 0xFFFF

    def _selected_full_data_depth_points(self) -> int:
        selected = self.full_data_depth_var.get()
        for label, _code in self.DATA_DEPTH_OPTIONS:
            if label == selected:
                return int(label)
        return int(self.DATA_DEPTH_OPTIONS[0][0])

    def _full_period_points(self) -> int:
        wave_number = self._parse_full_wave_number(self.play_waveform_var, show_error=False)
        if wave_number == 0x1F:
            return self._selected_full_data_depth_points()
        page = getattr(self, "waveform_page", None)
        return page.effective_points() if page is not None else 256

    def _map_full_adc_voltage_to_code(self, voltage: float) -> int:
        full_scale = 18.0 if self.adc_gain_var.get() == "0-19V" else 100.0
        voltage = max(-full_scale, min(full_scale, float(voltage)))

        if voltage >= 0:
            ratio = voltage / full_scale if full_scale else 0.0
            code = self.ADC_POS_ZERO + round(ratio * (self.ADC_MAX_CODE - self.ADC_POS_ZERO))
        else:
            ratio = abs(voltage) / full_scale if full_scale else 0.0
            code = self.ADC_NEG_ZERO - round(ratio * self.ADC_NEG_ZERO)
        return max(self.ADC_MIN_CODE, min(self.ADC_MAX_CODE, int(code)))

    def _full_adc_threshold_register_hex_text(self, text: str) -> str:
        try:
            voltage = float(text)
        except ValueError:
            return ""
        if not math.isfinite(voltage):
            return ""
        return f"0x{self._map_full_adc_voltage_to_code(voltage):04X}"

    def _refresh_full_adc_threshold_register_value_labels(self) -> None:
        self.full_adc_threshold_h_hex_var.set(self._full_adc_threshold_register_hex_text(self.adc_threshold_h_var.get()))
        self.full_adc_threshold_l_hex_var.set(self._full_adc_threshold_register_hex_text(self.adc_threshold_l_var.get()))

    def _parse_full_adc_thresholds(self, *, show_error: bool = True) -> tuple[int, int] | None:
        try:
            high_v = float(self.adc_threshold_h_var.get())
            low_v = float(self.adc_threshold_l_var.get())
        except ValueError:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return None
        return self._map_full_adc_voltage_to_code(high_v), self._map_full_adc_voltage_to_code(low_v)

    def _parse_full_repeat_count(self, variable: tk.StringVar, *, show_error: bool = True) -> int | None:
        try:
            count = int(variable.get().strip(), 0)
        except ValueError:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return None
        if not 1 <= count <= 512:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("repeat_count_range"))
            return None
        return count

    def _parse_full_wave_number(self, variable: tk.StringVar, *, show_error: bool = True) -> int | None:
        text = variable.get().strip().lower()
        try:
            if text in {"rom sin", "sin", "0x1f"}:
                index = 0x1F
            elif text == "serial":
                index = 0x1E
            elif text.startswith("slice"):
                index = int(text.replace("slice", "", 1), 10)
            else:
                index = int(text, 0)
        except ValueError:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return None

        if not 0 <= index <= 0x1F:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("full_wave_number_range"))
            return None
        return index

    def _compose_full_event_value(self, event_key: str, *, show_error: bool = True) -> int | None:
        wave_number = self._parse_full_wave_number(self.full_event_wave_vars[event_key], show_error=show_error)
        if wave_number is None:
            return None

        value = wave_number & 0x1F
        if self.full_event_enable_vars[event_key].get():
            value |= 1 << 15
        if self.full_event_endless_vars[event_key].get():
            value |= 1 << 14
        else:
            repeat = self._parse_full_repeat_count(self.full_event_repeat_vars[event_key], show_error=show_error)
            if repeat is None:
                return None
            value |= ((repeat - 1) & 0x1FF) << 5
        return value

    def _compose_full_manual_play_value(self, *, show_error: bool = True) -> int | None:
        wave_number = self._parse_full_wave_number(self.play_waveform_var, show_error=show_error)
        if wave_number is None:
            return None

        value = wave_number & 0x1F
        if self.endless_play_var.get():
            value |= 1 << 14
        else:
            repeat = self._parse_full_repeat_count(self.play_repeat_count_var, show_error=show_error)
            if repeat is None:
                return None
            value |= ((repeat - 1) & 0x1FF) << 5
        return value

    def _compose_adc_config_0b(self) -> int:
        mode_code = {"Direct": 0x0, "INPT": 0x1, "AUTO": 0x2}.get(self.adc_mode_var.get(), 0x0)
        gain_code = 1 if self.adc_gain_var.get() == "0-19V" else 0
        pol_code = 1 if self.adc_pol_var.get() == "OUT-" else 0

        value = (int(getattr(self, "_full_adc_config_fixed_bits", 0)) & 0xFFFF)
        value |= (gain_code << 9) | (pol_code << 8) | mode_code
        for event_key, _reg, source_bit in self.ADC_EVENT_CONFIGS:
            if self.adc_trigger_vars[event_key].get():
                value |= 1 << source_bit
        return value & 0xFFFF

    def _compose_adc_config_0c(self) -> int:
        average_code = next((value for label, value in self.ADC_AVERAGE_OPTIONS if label == self.adc_average_time_var.get()), 0)
        ovv_code = next((value for label, value in self.ADC_OVV_OPTIONS if label == self.adc_ovv_time_var.get()), 0)
        return ((average_code & 0x0F) << 4) | (ovv_code & 0x0F)

    def _write_full_adc_thresholds(self, *, show_error: bool = True) -> bool:
        thresholds = self._parse_full_adc_thresholds(show_error=show_error)
        if thresholds is None:
            return False
        high_code, low_code = thresholds
        return self.write_reg(0x08, high_code, show_error=show_error) and self.write_reg(
            0x09,
            low_code,
            show_error=show_error,
        )

    def _write_adc_config_0b(self, *, show_error: bool = False) -> bool:
        return self.write_reg(0x0B, self._compose_adc_config_0b(), show_error=show_error)

    def _write_adc_config_0c(self, *, show_error: bool = False) -> bool:
        return self.write_reg(0x0C, self._compose_adc_config_0c(), show_error=show_error)

    def _write_full_reg14(self, *, show_error: bool = False) -> bool:
        selected = self.max_voltage_var.get()
        for label, voltage, _reg_value in self.amp_options:
            if label == selected:
                self.ctx.state.current_max_output_voltage = voltage
                break
        value = self._compose_full_reg14()
        self.ctx.state.current_max_output_reg_value = value
        return self.write_reg(0x14, value, show_error=show_error)

    def _write_duration_reg(self, *, show_error: bool = True) -> bool:
        try:
            us = int(self.duration_var.get())
            register_duration = encode_single_point_duration(us)
        except Exception:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return False
        self.ctx.state.single_point_duration_us = us
        return self.write_reg(0x15, register_duration, show_error=show_error)

    def _write_full_manual_play_config(self, *, show_error: bool = True) -> bool:
        value = self._compose_full_manual_play_value(show_error=show_error)
        if value is None:
            return False
        return self.write_reg(0x13, value, show_error=show_error)

    def _write_full_sense_event_config(self, *, show_error: bool = True) -> bool:
        for event_key, reg, _source_bit in self.ADC_EVENT_CONFIGS:
            value = self._compose_full_event_value(event_key, show_error=show_error)
            if value is None:
                return False
            if not self.write_reg(reg, value, show_error=show_error):
                return False
        return True

    def _parse_u16(self, variable: tk.StringVar, *, show_error: bool = True) -> int | None:
        try:
            value = int(variable.get().strip(), 0)
        except ValueError:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return None
        if not 0 <= value <= 0xFFFF:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return None
        return value

    def _set_combo_by_code(self, variable: tk.StringVar, options: tuple[tuple[str, int], ...], code: int) -> None:
        label = next((label for label, value in options if value == code), options[0][0])
        variable.set(label)

    def read_full_status(self) -> None:
        value = self.read_reg(0x02)
        if value is None:
            return
        active = [self.tr(key) for key, bit in self.STATUS_FLAG_BITS if value & (1 << bit)]
        active_text = ", ".join(active) if active else self.tr("no_active_flags")
        self.status_value_var.set(f"0x{value:04X}  {active_text}")

    def read_chip_id(self) -> None:
        value = self.read_reg(0x1E)
        if value is not None:
            self.chip_id_var.set(f"0x{value:04X}")

    def _compose_interrupt_mask(self) -> int:
        value = 0
        for key, bit in self.INT_MASK_BITS:
            if self.int_mask_vars[key].get():
                value |= 1 << bit
        return value & 0xFFFF

    def write_interrupt_mask(self) -> None:
        self.write_reg(0x03, self._compose_interrupt_mask())

    def read_interrupt_mask(self) -> None:
        value = self.read_reg(0x03)
        if value is None:
            return
        for key, bit in self.INT_MASK_BITS:
            self.int_mask_vars[key].set(bool(value & (1 << bit)))

    def _compose_global_config(self) -> int:
        value = 0
        value |= (self._option_code(self.fifo_depth_var.get(), self.FIFO_DEPTH_OPTIONS) & 0x0F) << 12
        value |= (1 if self.str_auto_addr_var.get() else 0) << 11
        value |= (1 if self.dis_plt_var.get() else 0) << 10
        value |= (1 if self.auto_end_var.get() else 0) << 9
        value |= (1 if self.auto_zero_var.get() else 0) << 8
        value |= (self._option_code(self.reg_lock_var.get(), self.REG_LOCK_OPTIONS) & 0x03) << 4
        value |= (1 if self.clk_sync_var.get() else 0) << 3
        value |= (1 if self.clk_out_var.get() else 0) << 2
        value |= 1 if self.sync_play_var.get() else 0
        return value & 0xFFFF

    def write_global_config(self) -> None:
        self.write_reg(0x04, self._compose_global_config())

    def read_global_config(self) -> None:
        value = self.read_reg(0x04)
        if value is None:
            return
        self._set_combo_by_code(self.fifo_depth_var, self.FIFO_DEPTH_OPTIONS, (value >> 12) & 0x0F)
        self.str_auto_addr_var.set(bool(value & (1 << 11)))
        self.dis_plt_var.set(bool(value & (1 << 10)))
        self.auto_end_var.set(bool(value & (1 << 9)))
        self.auto_zero_var.set(bool(value & (1 << 8)))
        self._set_combo_by_code(self.reg_lock_var, self.REG_LOCK_OPTIONS, (value >> 4) & 0x03)
        self.clk_sync_var.set(bool(value & (1 << 3)))
        self.clk_out_var.set(bool(value & (1 << 2)))
        self.sync_play_var.set(bool(value & 0x0001))

    def write_ram_address(self) -> None:
        value = self._parse_u16(self.ram_address_var)
        if value is not None:
            self.write_reg(0x16, value)

    def read_ram_address(self) -> None:
        value = self.read_reg(0x16)
        if value is not None:
            self.ram_address_var.set(f"0x{value:04X}")

    def write_sram_data(self) -> None:
        value = self._parse_u16(self.sram_data_var)
        if value is not None:
            self.write_reg(0x00, value & 0x0FFF)

    def read_sram_data(self) -> None:
        value = self.read_reg(0x00)
        if value is not None:
            self.sram_data_var.set(f"0x{value & 0x0FFF:04X}")

    def _parse_serial_wave(self, variable: tk.StringVar, *, show_error: bool = True) -> int | None:
        text = variable.get().strip()
        if self.SERIAL_STOP_SUFFIX in text:
            text = text.split(self.SERIAL_STOP_SUFFIX, 1)[0].strip()
        text = text.lower()
        try:
            if text.startswith("slice"):
                value = int(text.replace("slice", "", 1), 10)
            else:
                value = int(text, 0)
        except ValueError:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return None
        if not 0 <= value <= 0x1F:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("full_wave_number_range"))
            return None
        return value

    def _parse_serial_loop(self, variable: tk.StringVar, *, show_error: bool = True) -> int | None:
        try:
            value = int(variable.get().strip(), 0)
        except ValueError:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return None
        if not 1 <= value <= 8:
            if show_error:
                messagebox.showerror(self.tr("error"), self.tr("serial_loop_range"))
            return None
        return value

    def _format_serial_wave(self, value: int) -> str:
        if 0 <= value <= 29:
            return f"slice{value}"
        if value in (0x1E, 0x1F):
            return f"0x{value:02X}{self.SERIAL_STOP_SUFFIX}"
        return f"0x{value:02X}"

    def _compose_serial_register(self, even_index: int) -> int | None:
        even_wave = self._parse_serial_wave(self.serial_wave_vars[even_index])
        even_loop = self._parse_serial_loop(self.serial_loop_vars[even_index])
        odd_wave = self._parse_serial_wave(self.serial_wave_vars[even_index + 1])
        odd_loop = self._parse_serial_loop(self.serial_loop_vars[even_index + 1])
        if None in (even_wave, even_loop, odd_wave, odd_loop):
            return None

        value = ((even_loop - 1) & 0x07) | ((even_wave & 0x1F) << 3)
        value |= ((odd_loop - 1) & 0x07) << 8
        value |= (odd_wave & 0x1F) << 11
        return value & 0xFFFF

    def write_serial_config(self) -> None:
        for pair, reg in enumerate((0x17, 0x18, 0x19, 0x1A)):
            value = self._compose_serial_register(pair * 2)
            if value is None:
                return
            if not self.write_reg(reg, value):
                return

    def read_serial_config(self) -> None:
        for pair, reg in enumerate((0x17, 0x18, 0x19, 0x1A)):
            value = self.read_reg(reg)
            if value is None:
                return
            even_index = pair * 2
            self.serial_loop_vars[even_index].set(str((value & 0x0007) + 1))
            self.serial_wave_vars[even_index].set(self._format_serial_wave((value >> 3) & 0x1F))
            self.serial_loop_vars[even_index + 1].set(str(((value >> 8) & 0x0007) + 1))
            self.serial_wave_vars[even_index + 1].set(self._format_serial_wave((value >> 11) & 0x1F))

    def adc_calibrate(self) -> None:
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return

        if self._sense_status_active or self._sense_timeout_mode:
            if not self.write_reg(0x06, 0x0002):
                return
            self._sense_status_active = False
            self._sense_timeout_mode = False
            self._cancel_sense_jobs()
            self._set_sense_status_color(self.STATUS_IDLE_COLOR)

        if not self.write_reg(0x01, 0x5F76):
            return

        current = self.read_reg(0x0A)
        if current is None:
            return

        cleared = current & ~(1 << 15)
        if not self.write_reg(0x0A, cleared):
            return
        self.write_reg(0x0A, cleared | (1 << 15))

    def open_adc_detect_window(self) -> None:
        if self._adc_detect_window is not None and self._adc_detect_window.winfo_exists():
            self._adc_detect_window.lift()
            self._adc_detect_window.focus_force()
            return

        window = tk.Toplevel(self)
        window.title(self.tr("adc_detect_value"))
        fit_toplevel_to_screen(
            window,
            preferred_width=420,
            preferred_height=220,
            minimum_width=320,
            minimum_height=180,
        )
        window.configure(bg=self.colors["bg"])
        scroll_area = VerticalScrollArea(
            window,
            self.ctx,
            horizontal=True,
            min_content_width=380,
        )
        scroll_area.grid(row=0, column=0, sticky="nsew")
        window.rowconfigure(0, weight=1)
        window.columnconfigure(0, weight=1)
        body = scroll_area.content
        body.columnconfigure(1, weight=1)
        window.protocol("WM_DELETE_WINDOW", self._close_adc_detect_window)

        self._adc_detect_window = window
        self._adc_detect_gain_var = tk.StringVar(value=self.adc_gain_var.get())
        self._adc_detect_pol_var = tk.StringVar(value=self.adc_pol_var.get())
        self._adc_detect_result_var = tk.StringVar(value="")

        gain_label = ttk.Label(body, text=self.tr("adc_gain"), style="Panel.TLabel")
        gain_label.grid(row=0, column=0, sticky="w", padx=(16, 8), pady=(16, 8))
        ttk.Combobox(
            body,
            textvariable=self._adc_detect_gain_var,
            values=["0-19V", "0-100V"],
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=(16, 8))

        pol_label = ttk.Label(body, text=self.tr("adc_pol"), style="Panel.TLabel")
        pol_label.grid(row=1, column=0, sticky="w", padx=(16, 8), pady=8)
        ttk.Combobox(
            body,
            textvariable=self._adc_detect_pol_var,
            values=["OUT+", "OUT-"],
            state="readonly",
        ).grid(row=1, column=1, sticky="ew", padx=(0, 16), pady=8)

        read_btn = ttk.Button(body, text=self.tr("adc_read_value"), style="Primary.TButton", command=self.read_adc_detect_value)
        read_btn.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 8))

        result_label = ttk.Label(body, textvariable=self._adc_detect_result_var, style="Panel.TLabel")
        result_label.grid(row=3, column=0, columnspan=2, sticky="w", padx=16, pady=(8, 16))

        self.start_adc_detect_monitor(reset_records=True)

    def _close_adc_detect_window(self) -> None:
        if self._adc_detect_poll_job is not None:
            try:
                self.after_cancel(self._adc_detect_poll_job)
            except Exception:
                pass
        self._adc_detect_poll_job = None
        if self._adc_detect_window is not None:
            try:
                self._adc_detect_window.destroy()
            except Exception:
                pass
        self._adc_detect_window = None

    def read_adc_detect_value(self) -> None:
        self.start_adc_detect_monitor(reset_records=True)

    def start_adc_detect_monitor(self, *, reset_records: bool = False) -> None:
        if self._adc_detect_poll_job is not None:
            try:
                self.after_cancel(self._adc_detect_poll_job)
            except Exception:
                pass
            self._adc_detect_poll_job = None

        if reset_records:
            self._adc_detect_max_v = None
            self._adc_detect_min_v = None

        self.adc_mode_var.set("AUTO")
        self._on_adc_mode_changed()

        if not self._write_full_adc_thresholds():
            return
        if not self._write_adc_config_0c(show_error=True):
            return
        if not self._write_adc_config_0b(show_error=True):
            return
        if not self.write_reg(0x06, 0x0001):
            return

        self._poll_adc_detect_value()

    def _poll_adc_detect_value(self) -> None:
        self._adc_detect_poll_job = None
        if self._adc_detect_window is None or not self._adc_detect_window.winfo_exists():
            return

        value = self.read_reg(0x07, show_error=False)
        if value is None:
            self._adc_detect_poll_job = self.after(300, self._poll_adc_detect_value)
            return

        code = value & 0x0FFF
        voltage = self._adc_code_to_voltage(code)

        self._adc_detect_max_v = voltage if self._adc_detect_max_v is None else max(self._adc_detect_max_v, voltage)
        self._adc_detect_min_v = voltage if self._adc_detect_min_v is None else min(self._adc_detect_min_v, voltage)

        self._adc_detect_result_var.set(
            f"ADC: 0x{code:03X} | {voltage:.3f} V\n"
            f"Max: {self._adc_detect_max_v:.3f} V\n"
            f"Min: {self._adc_detect_min_v:.3f} V"
        )
        self._adc_detect_poll_job = self.after(300, self._poll_adc_detect_value)

    def _code_to_voltage(self, code: int, gain_label: str, pol_label: str) -> float:
        full_scale = 18.0 if gain_label == "0-19V" else 100.0
        if code >= self.ADC_POS_ZERO:
            voltage = ((code - self.ADC_POS_ZERO) / (self.ADC_MAX_CODE - self.ADC_POS_ZERO)) * full_scale
        else:
            voltage = -((self.ADC_NEG_ZERO - code) / self.ADC_NEG_ZERO) * full_scale

        if pol_label == "OUT-":
            voltage = -voltage

        return voltage

    def _adc_code_to_voltage(self, code: int) -> float:
        return self._code_to_voltage(code, self._adc_detect_gain_var.get(), self._adc_detect_pol_var.get())

    def on_show(self) -> None:
        self._sync_awp192x_settings_from_model()
        self._refresh_full_adc_threshold_register_value_labels()
        self._update_period_hint(write_register=False)
        if not self._sense_status_active:
            self._set_sense_status_color(self.STATUS_IDLE_COLOR)

    def refresh_theme(self) -> None:
        super().refresh_theme()
        if hasattr(self, "_full_left_canvas"):
            self._full_left_canvas.configure(background=self.colors["panel"])
        if hasattr(self, "_full_right_canvas"):
            self._full_right_canvas.configure(background=self.colors["panel"])

    def _update_period_hint(self, *, write_register: bool = True) -> None:
        try:
            us = int(self.duration_var.get())
            register_duration = encode_single_point_duration(us)
            self.ctx.state.single_point_duration_us = us
            if (
                write_register
                and not getattr(self, "_syncing_chip_settings", False)
                and not getattr(self, "_reading_current_settings", False)
            ):
                self.write_reg(0x15, register_duration, show_error=False)
            points = self._full_period_points()
            period_ms = points * us / 1000
            freq = 1000 / period_ms if period_ms > 0 else 0
            if self.language == "zh":
                self.period_hint_var.set(f"\u6ce2\u5f62\u5468\u671f\u7ea6\u4e3a {period_ms:.3f} ms\n\u9891\u7387\u7ea6\u4e3a {freq:.1f} Hz")
            else:
                self.period_hint_var.set(f"Waveform period about {period_ms:.3f} ms\nFrequency about {freq:.1f} Hz")
        except Exception:
            self.period_hint_var.set("")

    def _on_voltage_changed(self) -> None:
        if getattr(self, "_syncing_chip_settings", False) or getattr(self, "_reading_current_settings", False):
            selected = self.max_voltage_var.get()
            for label, voltage, _reg_value in self.amp_options:
                if label == selected:
                    self.ctx.state.current_max_output_voltage = voltage
                    self.ctx.state.current_max_output_reg_value = self._compose_full_reg14()
                    break
            return
        self._write_full_reg14(show_error=False)

    def _on_adc_average_changed(self) -> None:
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        selected = self.adc_average_time_var.get()
        code = next((value for label, value in self.ADC_AVERAGE_OPTIONS if label == selected), 0x00)
        self.ctx.state.extra["adc_average_code"] = code
        self._write_adc_config_0c(show_error=False)

    def _on_adc_ovv_changed(self) -> None:
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        selected = self.adc_ovv_time_var.get()
        code = next((value for label, value in self.ADC_OVV_OPTIONS if label == selected), 0x0)
        self.ctx.state.extra["adc_ovv_code"] = code
        self._write_adc_config_0c(show_error=False)

    def _on_adc_gain_pol_changed(self) -> None:
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        gain_code = 1 if self.adc_gain_var.get() == "0-19V" else 0
        pol_code = 1 if self.adc_pol_var.get() == "OUT-" else 0
        self.ctx.state.extra["adc_gain_code"] = gain_code
        self.ctx.state.extra["adc_pol_code"] = pol_code
        self._write_adc_config_0b(show_error=False)
        self._on_adc_threshold_changed()

    def _on_adc_mode_changed(self) -> None:
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        selected = self.adc_mode_var.get()
        if selected == "INPT":
            mode_code = 0x01
        elif selected == "AUTO":
            mode_code = 0x02
        else:
            mode_code = 0x00
        self.ctx.state.extra["adc_mode_code"] = mode_code
        self._write_adc_config_0b(show_error=False)

    def _on_adc_inpt_source_changed(self) -> None:
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        self._write_adc_config_0b(show_error=False)

    def _on_adc_threshold_changed(self) -> None:
        self._refresh_full_adc_threshold_register_value_labels()
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        self._write_full_adc_thresholds(show_error=False)

    def _on_full_manual_play_changed(self) -> None:
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        self._write_full_manual_play_config(show_error=False)
        self._update_period_hint(write_register=False)

    def _on_full_sense_event_changed(self, event_key: str) -> None:
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        value = self._compose_full_event_value(event_key, show_error=False)
        if value is None:
            return
        reg = next((reg for key, reg, _source_bit in self.ADC_EVENT_CONFIGS if key == event_key), None)
        if reg is not None:
            self.write_reg(reg, value, show_error=False)

    def _on_endless_play_changed(self) -> None:
        entry = self._repeat_entries.get("play_repeat_count")
        if entry is not None:
            entry.configure(state="disabled" if self.endless_play_var.get() else "normal")
        if getattr(self, "_reading_current_settings", False) or getattr(self, "_syncing_chip_settings", False):
            return
        self._on_full_manual_play_changed()

    def _on_full_data_depth_changed(self) -> None:
        self._on_voltage_changed()
        self._update_period_hint(write_register=False)

    def _apply_playback_options(self) -> bool:
        return self._write_full_manual_play_config(show_error=True)

    def _read_and_update_button_adc(self, *, show_error: bool = True) -> bool:
        value = self.read_reg(0x07, show_error=show_error)
        if value is None:
            return False
        code = value & 0x0FFF
        gain_label = "0-19V" if (value & (1 << 13)) else "0-100V"
        pol_label = "OUT-" if (value & (1 << 15)) else "OUT+"
        voltage = self._code_to_voltage(code, gain_label, pol_label)
        self.adc_value_var.set(f"0x{code:03X} / {code} / {voltage:.3f} V")
        return True

    def start_detection(self) -> None:
        if not self.ctx.serial.is_connected:
            return
        status_token = _begin_status(self, "detection")
        if not self._write_full_reg14(show_error=True):
            _finish_status(self, status_token, success=False, error="configuration write failed")
            return
        if not self._write_duration_reg(show_error=True):
            _finish_status(self, status_token, success=False, error="configuration write failed")
            return
        if not self._write_full_adc_thresholds(show_error=True):
            _finish_status(self, status_token, success=False, error="configuration write failed")
            return
        if not self._write_adc_config_0c(show_error=True):
            _finish_status(self, status_token, success=False, error="configuration write failed")
            return
        if not self._write_adc_config_0b(show_error=True):
            _finish_status(self, status_token, success=False, error="configuration write failed")
            return
        if not self._write_full_sense_event_config(show_error=True):
            _finish_status(self, status_token, success=False, error="configuration write failed")
            return
        started = self.write_reg(0x06, 0x0001)
        _finish_status(self, status_token, success=started,
                           error="start detection write failed" if not started else "")
        if started:
            self._set_button_selected("adc_start", True)

    def stop_detection(self) -> None:
        if not self.ctx.serial.is_connected:
            return
        status_token = _begin_status(self, "detection")
        stopped = self.write_reg(0x06, 0x0002)
        _finish_status(self, status_token, success=stopped,
                           error="stop detection write failed" if not stopped else "")
        if stopped:
            self._set_button_selected("adc_start", False)

    def start_play(self) -> None:
        if not self.ctx.serial.is_connected:
            return
        status_token = _begin_status(self, "playback")
        if not self._write_full_reg14(show_error=True):
            _finish_status(self, status_token, success=False, error="playback configuration failed")
            return
        if not self._write_duration_reg(show_error=True):
            _finish_status(self, status_token, success=False, error="playback configuration failed")
            return
        if not self._write_full_manual_play_config(show_error=True):
            _finish_status(self, status_token, success=False, error="playback configuration failed")
            return
        self.write_reg_async(
            0x06, 0x0004,
            on_success=lambda: (self._set_button_selected("start_play", True),
                                _finish_status(self, status_token, success=True)),
            on_failure=lambda error: _finish_status(self, status_token, success=False, error=error),
        )

    def stop_play(self) -> None:
        if not self.ctx.serial.is_connected:
            return
        status_token = _begin_status(self, "playback")
        self.write_reg_async(
            0x06, 0x0008,
            on_success=lambda: (self._set_button_selected("start_play", False),
                                _finish_status(self, status_token, success=True)),
            on_failure=lambda error: _finish_status(self, status_token, success=False, error=error),
        )


class PumpModePage(BasePage):
    page_name = "pump_mode"
    PUMP_ROM_TITLE_PARTS = ("——", "ROM", " Play for Liquid Pump")
    PUMP_ROM_TOOLTIP = "ROM内存储的波形仅为正弦波"
    PUMP_SLOW_TIME_OPTIONS = (
        ("0", 0x0),
        ("1", 0x1),
        ("3", 0x2),
        ("7", 0x3),
    )
    PUMP_DATA_DEPTH_OPTIONS = (
        ("1024", 0x0, 1024),
        ("512", 0x1, 512),
        ("256", 0x2, 256),
        ("128", 0x3, 128),
    )

    def __init__(self, parent: tk.Misc, ctx: Any, *, defer_build: bool = False) -> None:
        super().__init__(parent, ctx)

        self.amp_options = build_awp192x_amp_options()
        self.freq_options = build_frequency_options()

        self.amp_var = tk.StringVar(
            value=next(label for label, voltage, _reg in self.amp_options if voltage == 59.4)
        )
        self.freq_var = tk.StringVar(value=next(label for label, freq, _reg in self.freq_options if freq == 200))
        self.slow_up_var = tk.StringVar(value=self.PUMP_SLOW_TIME_OPTIONS[0][0])
        self.slow_down_var = tk.StringVar(value=self.PUMP_SLOW_TIME_OPTIONS[0][0])
        self.precise_frequency_var = tk.BooleanVar(value=False)
        self.precise_freq_options = self.build_precise_frequency_options()

        self._labels: dict[str, ttk.Label] = {}
        self._buttons: dict[str, ttk.Button] = {}
        self._checks: dict[str, ttk.Checkbutton] = {}
        self._tooltips: list[SimpleTooltip] = []
        self.freq_combo: ttk.Combobox | None = None

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
        yield
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = self._make_pump_title()
        header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())
        self._register_title_spacing_widget(header)

        panel = ttk.Frame(self, style="Panel.TFrame", padding=18)
        panel.grid(row=1, column=0, sticky="nsew")
        yield
        self.register_centered_panel(panel)
        panel.columnconfigure(1, weight=1)

        self.make_module_title_row(
            panel,
            "pump_output_settings",
            step=1,
            step_total=2,
            help_key="awp192x_help_pump_output",
            row=0,
            columnspan=2,
        )
        row = 1
        self._add_combo(panel, row, "chip_single_output", self.amp_var, [x[0] for x in self.amp_options])
        row += 1
        self._add_frequency_row(panel, row)
        row += 1
        yield
        self._add_combo(panel, row, "slow_up_time", self.slow_up_var, [x[0] for x in self.PUMP_SLOW_TIME_OPTIONS])
        row += 1
        self._add_combo(panel, row, "slow_down_time", self.slow_down_var, [x[0] for x in self.PUMP_SLOW_TIME_OPTIONS])
        row += 1

        self.hint_label = ttk.Label(panel, style="Muted.TLabel")
        self.hint_label.grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 14))
        yield
        row += 1

        self.make_module_title_row(
            panel,
            "pump_switches",
            step=2,
            step_total=2,
            help_key="awp192x_help_pump_switch",
            row=row,
            columnspan=2,
            pady=(4, 0),
        )
        row += 1

        btn_frame = ttk.Frame(panel, style="Panel.TFrame")
        btn_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        start = ttk.Button(btn_frame, style="Primary.TButton", command=self.start_play)
        yield
        start.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["start_play"] = start

        stop = ttk.Button(btn_frame, style="Secondary.TButton", command=self.stop_play)
        stop.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._buttons["stop_play"] = stop
        row += 1
        yield

        log_frame = self.make_serial_log(panel, subscribe=True)
        log_frame.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(16, 0))
        self._register_module_spacing_widget(log_frame, before=True, after=False)
        panel.rowconfigure(row, weight=1)

        self.amp_var.trace_add("write", lambda *_: self._on_amp_changed())
        self.freq_var.trace_add("write", lambda *_: self._on_freq_changed())
        self.slow_up_var.trace_add("write", lambda *_: self._on_amp_changed())
        yield
        self.slow_down_var.trace_add("write", lambda *_: self._on_amp_changed())
        self.precise_frequency_var.trace_add("write", lambda *_: self._sync_pump_step_progress_params())

        self._refresh_frequency_options()
        self.refresh_language()
        self._mount_pump_step_progress()

    def _pump_step_host(self) -> Any | None:
        parent = getattr(self, "master", None)
        while parent is not None:
            if callable(getattr(parent, "_step_progress_context", None)):
                return parent
            parent = getattr(parent, "master", None)
        return None

    def _mount_pump_step_progress(self) -> None:
        host = self._pump_step_host()
        if host is None:
            return
        self.mount_step_progress(
            host=host,
            steps=(
                StepSpec("output", 1, "step_done_pump_output", ("frequency", "amplitude")),
                StepSpec("start", 2, "step_done_pump_start"),
            ),
            context=host._step_progress_context,
        )
        for variable in (
            self.amp_var,
            self.freq_var,
            self.slow_up_var,
            self.slow_down_var,
            self.precise_frequency_var,
        ):
            variable.trace_add("write", lambda *_: self._sync_pump_step_progress_params())
        self._sync_pump_step_progress_params()

    def _pump_output_fingerprints(self) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
        option = self._current_frequency_option()
        depth = option[3] if option is not None else None
        frequency = (
            str(self.freq_var.get()),
            bool(self.precise_frequency_var.get()),
            None if option is None else tuple(option[1:]),
        )
        amplitude = (
            str(self.amp_var.get()),
            str(self.slow_up_var.get()),
            str(self.slow_down_var.get()),
            depth,
        )
        return frequency, amplitude

    def _sync_pump_step_progress_params(self) -> None:
        bar = getattr(self, "step_progress", None)
        if bar is None:
            return
        frequency, amplitude = self._pump_output_fingerprints()
        bar.update_params("output", {"frequency": frequency, "amplitude": amplitude})
        bar.update_params("start", {"settings": (frequency, amplitude)})

    def _begin_pump_output_attempt(self, *, aggregate: bool = False) -> Any | None:
        bar = getattr(self, "step_progress", None)
        if bar is not None:
            self._sync_pump_step_progress_params()
            token = bar.begin("output")
            _begin_status(self, "pump_output", progress_token=token, aggregate_parts=aggregate)
            return token
        bus = getattr(getattr(self, "ctx", None), "status_bus", None)
        if bus is None:
            return None
        from core.status_bus import StatusContext, StatusMessage
        context = self._status_context(event="pump_output")
        if aggregate:
            context = StatusContext(
                context.port_session, context.chip_addr, context.mode,
                context.activation_epoch, context.param_version, context.operation_id,
                context.page_name, ("frequency", "amplitude"),
            )
        return bus.begin(
            StatusMessage.make("sidebar_status_pump_output_begin"),
            context=context,
            event="pump_output",
        )

    def _record_pump_output_part(self, part_key: str, ok: bool, token: Any | None) -> None:
        from core.status_bus import StatusToken
        if isinstance(token, StatusToken):
            bus = getattr(getattr(self, "ctx", None), "status_bus", None)
            if bus is None:
                return
            state = "done" if ok else "error"
            if bus.receipt(token, part_key, state, error="Pump output write failed" if not ok else ""):
                if not token.context.part_keys:
                    _finish_status(self, token, success=ok, error="Pump output write failed" if not ok else "")
            return
        bar = getattr(self, "step_progress", None)
        report_part = getattr(self, "report_progress_part_status", None)
        if token is not None and callable(report_part):
            report_part(token, part_key, "done" if ok else "error")
        if bar is not None and token is not None:
            bar.mark_part("output", part_key, "done" if ok else "error", token)

    def _make_pump_title(self) -> ttk.Frame:
        header = ttk.Frame(self, style="App.TFrame")
        self._mode_headers.append(header)
        self._sync_mode_header(header)

        title_group = ttk.Frame(header, style="App.TFrame")
        title_group.grid(row=0, column=0, columnspan=2, sticky="nw")

        title = CanvasTitle(title_group, style="Prominent.TLabel")
        title.grid(row=0, column=0, sticky="w")
        self.bind_text(title, "pump_mode")

        for index, text in enumerate(self.PUMP_ROM_TITLE_PARTS, start=1):
            label = CanvasTitle(title_group, text=text, style="Prominent.TLabel")
            label.grid(row=0, column=index, sticky="w")
            if text == "ROM":
                label.configure(cursor="question_arrow")
                self._tooltips.append(SimpleTooltip(label, lambda: self.PUMP_ROM_TOOLTIP))

        return header

    @classmethod
    def compose_pump_reg14_value(
        cls,
        amp_reg_value: int,
        *,
        slow_up_label: str,
        slow_down_label: str,
        data_depth_label: str = "1024",
    ) -> int:
        slow_options = dict(cls.PUMP_SLOW_TIME_OPTIONS)
        data_depth_options = {label: code for label, code, _depth in cls.PUMP_DATA_DEPTH_OPTIONS}
        value = int(amp_reg_value) & 0xFFFF
        value &= ~((0x03 << 14) | (0x03 << 12) | (0x07 << 7))
        value |= (slow_options.get(slow_up_label, 0x0) & 0x03) << 14
        value |= (slow_options.get(slow_down_label, 0x0) & 0x03) << 12
        value |= (data_depth_options.get(data_depth_label, 0x0) & 0x07) << 7
        return value & 0xFFFF

    @classmethod
    def build_precise_frequency_options(cls) -> list[tuple[str, int, int, str]]:
        by_frequency: dict[int, tuple[str, int, int, str, int]] = {}
        for data_depth_label, _data_depth_code, data_depth in cls.PUMP_DATA_DEPTH_OPTIONS:
            multiplier = 1024 / data_depth
            for reg_value in range(256):
                freq = round(1000 * multiplier / (reg_value + 1))
                if not 100 <= freq <= 1000:
                    continue
                label = f"{freq}Hz -- Datadeep({data_depth_label})* Dac Sample Rate({reg_value})"
                candidate = (label, freq, reg_value, data_depth_label, data_depth)
                current = by_frequency.get(freq)
                if current is None or data_depth > current[4]:
                    by_frequency[freq] = candidate
        return [
            (label, freq, reg_value, data_depth_label)
            for label, freq, reg_value, data_depth_label, _data_depth in sorted(
                by_frequency.values(),
                key=lambda item: item[1],
                reverse=True,
            )
        ]

    def on_show(self) -> None:
        self._sync_pump_step_progress_params()
        self.write_reg(0x13, 0x401F, show_error=False)
        self._on_amp_changed()
        self._on_freq_changed()

    def on_hide(self) -> None:
        progress = getattr(self, "step_progress", None)
        if progress is not None:
            progress.reset_all()

    def _add_combo(self, parent: tk.Misc, row: int, key: str, variable: tk.StringVar, values: list[str]) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=8, padx=(0, 10))
        self._labels[key] = label

        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=8)

    def _add_frequency_row(self, parent: tk.Misc, row: int) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=8, padx=(0, 10))
        self._labels["chip_frequency"] = label

        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=row, column=1, sticky="ew", pady=8)
        frame.columnconfigure(0, weight=1)

        combo = ttk.Combobox(frame, textvariable=self.freq_var, values=[], state="readonly")
        combo.grid(row=0, column=0, sticky="ew")
        self.freq_combo = combo

        precise_check = ttk.Checkbutton(
            frame,
            variable=self.precise_frequency_var,
            command=self._on_precise_frequency_changed,
        )
        precise_check.grid(row=0, column=1, sticky="e", padx=(10, 0))
        self._checks["precise_frequency"] = precise_check

    def refresh_language(self) -> None:
        super().refresh_language()
        for key, label in self._labels.items():
            label.configure(text=self.tr(key))
        for key, btn in self._buttons.items():
            btn.configure(text=self.tr(key))
        for key, check in self._checks.items():
            check.configure(text=self.tr(key))
        self.hint_label.configure(text=self.tr("actual_output_hint"))

    def _rough_frequency_options(self) -> list[tuple[str, int, int, str]]:
        return [(label, freq, reg_value, "1024") for label, freq, reg_value in self.freq_options]

    def _active_frequency_options(self) -> list[tuple[str, int, int, str]]:
        if self.precise_frequency_var.get():
            return self.precise_freq_options
        return self._rough_frequency_options()

    def _current_frequency_option(self) -> tuple[str, int, int, str] | None:
        selected = self.freq_var.get()
        for option in self._active_frequency_options():
            if option[0] == selected:
                return option
        return None

    def _frequency_for_label(self, label: str) -> int:
        for option in self._rough_frequency_options() + self.precise_freq_options:
            if option[0] == label:
                return option[1]
        match = re.search(r"\d+", label)
        return int(match.group(0)) if match else 200

    def _refresh_frequency_options(self) -> None:
        options = self._active_frequency_options()
        values = [label for label, _freq, _reg_value, _data_depth_label in options]
        if self.freq_combo is not None:
            self.freq_combo.configure(values=values)
        if self.freq_var.get() in values:
            return
        target_freq = self._frequency_for_label(self.freq_var.get())
        closest = min(options, key=lambda option: abs(option[1] - target_freq))
        self.freq_var.set(closest[0])

    def _on_precise_frequency_changed(self) -> None:
        self._refresh_frequency_options()
        self._on_freq_changed()

    def _on_amp_changed(self, *, progress_token: Any | None = None) -> bool:
        self._sync_pump_step_progress_params()
        token = progress_token if progress_token is not None else self._begin_pump_output_attempt()
        selected = self.amp_var.get()
        for label, voltage, reg_value in self.amp_options:
            if label == selected:
                value = self.compose_pump_reg14_value(
                    reg_value,
                    slow_up_label=self.slow_up_var.get(),
                    slow_down_label=self.slow_down_var.get(),
                    data_depth_label=self._current_data_depth_label(),
                )
                self.ctx.state.current_max_output_voltage = voltage
                self.ctx.state.current_max_output_reg_value = value
                ok = self.write_reg(0x14, value, show_error=False)
                self._record_pump_output_part("amplitude", ok, token)
                if progress_token is None:
                    _report_progress_status(self, token, "done" if ok else "error")
                return ok
        return False

    def _on_freq_changed(self) -> None:
        self._sync_pump_step_progress_params()
        option = self._current_frequency_option()
        if option is None:
            return
        token = self._begin_pump_output_attempt(aggregate=True)
        _label, _freq, reg_value, _data_depth_label = option
        frequency_ok = self.write_reg(0x15, reg_value, show_error=False)
        self._record_pump_output_part("frequency", frequency_ok, token)
        # Preserve the established write order: amplitude is still attempted
        # after the frequency response, and the two explicit receipts aggregate.
        self._on_amp_changed(progress_token=token)
        if not frequency_ok:
            # Re-assert the failed frequency outcome after amplitude so the
            # aggregate step stays red (amplitude done must not clear it).
            self._record_pump_output_part("frequency", False, token)

    def _current_data_depth_label(self) -> str:
        option = self._current_frequency_option()
        return option[3] if option is not None else "1024"

    def write_reg(self, reg: int, value: int, show_error: bool = True) -> bool:
        if getattr(self, '_preparing', False):
            return False
        if not self.ctx.serial.is_connected:
            if show_error:
                messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return False

        resp = self.ctx.serial.write_register_value(
            self.ctx.state.awp192x_device_addr,
            reg,
            value,
            bits=16,
        )
        if not resp.ok and show_error:
            messagebox.showerror(self.tr("error"), resp.error)
        return resp.ok

    def write_reg_async(
        self,
        reg: int,
        value: int,
        *,
        on_success: Any = None,
        on_failure: Any = None,
    ) -> None:
        if getattr(self, '_preparing', False):
            return
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return

        dev = self.ctx.state.awp192x_device_addr

        def finish(ok: bool, error: str) -> None:
            if not ok:
                messagebox.showerror(self.tr("error"), error)
                if on_failure is not None:
                    on_failure(error)
                return
            if on_success is not None:
                on_success()

        def worker() -> None:
            resp = self.ctx.serial.write_register_value(dev, reg, value, bits=16)
            post_to_ui(self, finish, resp.ok, resp.error)

        threading.Thread(target=worker, daemon=True).start()

    def start_play(self) -> None:
        if not self.ctx.serial.is_connected or getattr(self, "_preparing", False):
            return
        bar = getattr(self, "step_progress", None)
        token = None
        if bar is not None and self.ctx.serial.is_connected and not getattr(self, "_preparing", False):
            self._sync_pump_step_progress_params()
            token = bar.begin("start")
        status_token = _begin_status(self, "playback", progress_token=token)

        def started() -> None:
            self._set_button_selected("start_play", True)
            _finish_status(self, status_token, success=True)
            if bar is not None and token is not None and not status_token:
                bar.mark("start", "done", token)

        def failed(_error: str) -> None:
            _finish_status(self, status_token, success=False, error=_error)
            if bar is not None and token is not None and not status_token:
                bar.mark("start", "error", token)

        self.write_reg_async(0x06, 0x0004, on_success=started, on_failure=failed)

    def stop_play(self) -> None:
        if not self.ctx.serial.is_connected or getattr(self, "_preparing", False):
            return
        status_token = _begin_status(self, "playback")
        host = self._pump_step_host()
        stop_context = host._step_progress_context() if host is not None else None

        def stopped() -> None:
            self._set_button_selected("start_play", False)
            _finish_status(self, status_token, success=True)
            if (
                self.step_progress is not None
                and stop_context is not None
                and host._step_progress_context() == stop_context
            ):
                self.step_progress.reset_all()

        self.write_reg_async(
            0x06, 0x0008, on_success=stopped,
            on_failure=lambda error: _finish_status(self, status_token, success=False, error=error),
        )
