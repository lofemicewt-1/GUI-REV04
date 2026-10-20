from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.awp192x_button_runtime import (
    AWP192X_GENERATOR_SINC,
    AWP192X_GENERATOR_SINC_VERSION,
    Awp192xSineWaveformRecipe,
    Awp192xWaveformRecipe,
    generate_awp192x_waveform_points,
)
from core.awp192x_settings import (
    Awp192xChipSettingsModel,
    connection_key_for_context,
    waveform_number,
)
from core.waveform_storage import WaveformStorage
from pages.awp192x_modes import (
    ButtonModePage,
    FullModePage,
    build_awp192x_amp_options,
)
from pages.chip_settings_model import can_save_slot


class Value:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class SynchronousThread:
    def __init__(self, target, daemon=True):
        self.target = target

    def start(self):
        self.target()


class FakeToolbarButton:
    def __init__(self, callback, enabled):
        self.callback = callback
        self.state = "normal" if enabled and callable(callback) else "disabled"

    def invoke(self):
        if self.state == "normal":
            return self.callback()
        return None


class FakePresetToolbar:
    def __init__(self, _parent, **options):
        self.callbacks = options["callbacks"]
        self.capabilities = options["capabilities"]
        self.auxiliary_actions = tuple(options.get("auxiliary_actions", ()))
        self.slot_values = tuple(options["slot_values"])
        self.slot_callback = options["on_slot_selected"]
        self.slot_var = Value(str(options["selected_slot"]))
        self.slot_label = object()
        self._tooltips = []
        actions = ButtonModePage.PRESET_ACTION_KEYS
        self.buttons = {
            action: FakeToolbarButton(
                self.callbacks.get(action),
                self.capabilities.get(action, True),
            )
            for action in actions
        }

    def select_slot(self):
        value = next((slot for slot in self.slot_values if str(slot) == self.slot_var.get()), None)
        if value is not None and self.capabilities.get("slot_selection", True):
            self.slot_callback(value)


class Awp192xSettingsTests(unittest.TestCase):
    def make_full_toolbar_page(self):
        page = SimpleNamespace(
            page_name="full_mode",
            PRESET_ACTION_KEYS=ButtonModePage.PRESET_ACTION_KEYS,
            READ_SETTINGS_TOOLTIP=ButtonModePage.READ_SETTINGS_TOOLTIP,
            board_slot_var=Value("1"),
            _buttons={},
            _labels={},
            _tooltips=[],
            language="en",
            tr=lambda key, **_kwargs: key,
            _read_settings_tooltip_text=lambda: "Read current chip settings into this page.",
            _save_slot_tooltip_text=lambda: "Save slot",
            _software_preset_tooltip_text=lambda _index: "Software preset",
            import_user_preset_script=lambda: None,
            export_user_preset_script=lambda: None,
            save_board_preset=lambda: None,
            set_board_preset_active=lambda: None,
            apply_board_preset=lambda: None,
            read_current_settings=lambda: None,
        )
        page._chip_settings_model = Awp192xChipSettingsModel()
        page._on_toolbar_slot_selected = lambda slot: ButtonModePage._on_toolbar_slot_selected(page, slot)
        return page

    def test_full_toolbar_actions_are_enabled_and_preset_works_without_waveform_window(self):
        page = self.make_full_toolbar_page()
        page.apply_preset = lambda index: ButtonModePage._apply_button_preset(
            page, ButtonModePage.BUTTON_PRESETS[index]
        )
        page.ctx = SimpleNamespace(
            state=SimpleNamespace(awp192x_device_addr=0x44),
            serial=SimpleNamespace(is_connected=True),
        )
        writes = []
        page._button_preset_recipe = ButtonModePage._button_preset_recipe
        page._load_preset_gui_values = lambda _config: None
        page._update_period_hint = lambda **_kwargs: None
        page._selected_button_adc_gain_code = lambda: 0
        page._map_adc_voltage_to_code = lambda _voltage, gain: 0x800 | gain
        page._set_repeat_and_waveform_fields = lambda base, repeat, wave: base | ((repeat - 1) << 5) | wave
        page._compose_button_adc_filter_config = lambda: 0x44
        page._compose_full_reg14 = lambda: 0xA57E
        page._compose_adc_config_0b = lambda: 0x0221
        page._compose_interrupt_mask = lambda: 0x1234
        register_configs = []
        page._write_preset_registers = lambda *args, **kwargs: register_configs.append(kwargs) or None
        page._write_waveform_values_to_device = lambda *args: writes.append(args) or None
        page._set_preset_buttons_state = lambda _state: None
        page._finish_preset_write = lambda *_args: None
        page.after = lambda _delay, callback, *args: callback(*args)
        page.ADC_DETECT_ENABLE = ButtonModePage.ADC_DETECT_ENABLE

        with patch("pages.awp192x_modes.PresetToolbar", FakePresetToolbar), patch(
            "pages.awp192x_modes.SimpleTooltip", lambda *_args: object()
        ), patch(
            "pages.preset_toolbar.SimpleTooltip", lambda *_args: object()
        ), patch("pages.awp192x_modes.threading.Thread", SynchronousThread):
            toolbar = FullModePage._create_preset_toolbar(page, object())

            self.assertEqual(set(toolbar.buttons), set(ButtonModePage.PRESET_ACTION_KEYS))
            self.assertEqual(toolbar.auxiliary_actions, ("read_and_apply",))
            self.assertIs(toolbar.callbacks["read_and_apply"], page.read_current_settings)
            self.assertIsNot(toolbar.callbacks["read_and_apply"], toolbar.callbacks["apply_slot"])
            for button in toolbar.buttons.values():
                self.assertEqual(button.state, "normal")

            # The shared toolbar can change the chip-scoped slot model.
            toolbar.slot_var.set("2")
            toolbar.select_slot()
            self.assertEqual(page._chip_settings_model.slot_selection, 2)

            # The Full Mode software preset callback writes its waveform without
            # a Full waveform editor attribute or a deleted editor reference.
            self.assertFalse(hasattr(page, "waveform_page"))
            toolbar.buttons["software_preset_1"].invoke()

        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][0], 0x44)
        self.assertEqual(register_configs[0]["full_register_values"], (0xA57E, 0x0221, 0x1234))

    def test_full_settings_capture_and_button_apply_need_no_view_widgets(self):
        model = Awp192xChipSettingsModel()
        full = SimpleNamespace(
            page_name="full_mode",
            amp_options=build_awp192x_amp_options(),
            _chip_settings_model=model,
            ADC_AVERAGE_OPTIONS=ButtonModePage.ADC_AVERAGE_OPTIONS,
            ADC_OVV_OPTIONS=ButtonModePage.ADC_OVV_OPTIONS,
            _option_code=ButtonModePage._option_code,
            max_voltage_var=Value(build_awp192x_amp_options()[2][0]),
            duration_var=Value("27"),
            adc_threshold_h_var=Value("2.5"),
            adc_threshold_l_var=Value("-0.2"),
            adc_pol_var=Value("OUT-"),
            adc_gain_var=Value("0-19V"),
            full_event_repeat_vars={
                "adc_over_h_posedge": Value("3"),
                "adc_over_l_posedge": Value("2"),
            },
            full_event_wave_vars={
                "adc_over_h_posedge": Value("slice7"),
                "adc_over_l_posedge": Value("slice2"),
            },
            full_event_endless_vars={
                "adc_over_h_posedge": Value(False),
                "adc_over_l_posedge": Value(False),
            },
            full_event_enable_vars={
                "adc_over_h_posedge": Value(True),
                "adc_over_l_posedge": Value(False),
            },
            serial_wave_vars=[Value(f"slice{index}") for index in range(7)]
            + [Value("0x1F——波形终止")],
            serial_loop_vars=[Value(str(index + 1)) for index in range(8)],
            adc_average_time_var=Value(ButtonModePage.ADC_AVERAGE_OPTIONS[3][0]),
            adc_ovv_time_var=Value(ButtonModePage.ADC_OVV_OPTIONS[2][0]),
            play_repeat_count_var=Value("4"),
            play_waveform_var=Value("slice5"),
            endless_play_var=Value(True),
        )

        captured = ButtonModePage._capture_shared_settings(full)

        self.assertEqual(captured["point_duration"], 27)
        self.assertEqual(captured["thresholds"], {"press": 2.5, "release": -0.2})
        self.assertEqual(captured["polarity"], 1)
        self.assertEqual(captured["range"], 1)
        self.assertEqual(captured["press_action"]["wave"], 7)
        self.assertEqual(captured["release_action"]["repeat"], 2)
        self.assertEqual(captured["manual_playback"], {"repeat": 4, "wave": 5, "endless": True})
        self.assertEqual(captured["serial_waveforms"][7], {"wave": 31, "repeat": 8})
        self.assertEqual(model.shared, captured)

        button = SimpleNamespace(
            page_name="button_mode",
            amp_options=build_awp192x_amp_options(),
            ADC_AVERAGE_OPTIONS=ButtonModePage.ADC_AVERAGE_OPTIONS,
            ADC_OVV_OPTIONS=ButtonModePage.ADC_OVV_OPTIONS,
            ctx=SimpleNamespace(state=SimpleNamespace(single_point_duration_us=1)),
            _current_max_output_reg_value=lambda: 2,
            _adc_average_label=lambda code: ButtonModePage._adc_average_label(button, code),
            _adc_ovv_label=lambda code: ButtonModePage._adc_ovv_label(button, code),
            _refresh_threshold_register_value_labels=lambda: None,
            _update_period_hint=lambda **_kwargs: None,
            _syncing_chip_settings=False,
            max_voltage_var=Value(""),
            duration_var=Value(""),
            press_threshold_var=Value(""),
            release_threshold_var=Value(""),
            adc_average_time_var=Value(""),
            adc_ovv_time_var=Value(""),
            button_adc_pol_var=Value(""),
            button_adc_gain_var=Value(""),
            press_repeat_count_var=Value(""),
            release_repeat_count_var=Value(""),
            press_waveform_var=Value(""),
            release_waveform_var=Value(""),
            play_repeat_count_var=Value(""),
            play_waveform_var=Value(""),
            endless_play_var=Value(False),
        )
        ButtonModePage._apply_shared_settings(button, model.shared)

        self.assertEqual(button.duration_var.get(), "27")
        self.assertEqual(button.press_threshold_var.get(), "2.5")
        self.assertEqual(button.release_threshold_var.get(), "-0.2")
        self.assertEqual(button.button_adc_pol_var.get(), "OUT-")
        self.assertEqual(button.button_adc_gain_var.get(), "0-19V")
        self.assertEqual(button.press_repeat_count_var.get(), "3")
        self.assertEqual(button.press_waveform_var.get(), "slice7")
        self.assertEqual(button.release_waveform_var.get(), "slice2")
        self.assertTrue(button.endless_play_var.get())
        self.assertEqual(waveform_number("0x1F——波形终止"), 31)

    def test_unsupported_full_advanced_state_blocks_and_warns_before_slot_write(self):
        advanced = {
            "adc_mode": 0,
            "adc_triggers": {"adc_over_h_posedge": True, "adc_over_h": True},
            "adc_config_fixed_bits": 1,
            "full_event_action_flags": {},
        }
        self.assertTrue(FullModePage._full_type3_slot_is_blocked(advanced))

        model = Awp192xChipSettingsModel(advanced={"slot_blocked": True})
        self.assertEqual(can_save_slot(model), (False, "preset_slot_advanced_block_warning"))
        writes = []
        page = SimpleNamespace(
            _ensure_connected=lambda: True,
            _chip_settings_model=model,
            tr=lambda key: key,
            ctx=SimpleNamespace(serial=SimpleNamespace(sync_config_preset=lambda *args, **kwargs: writes.append(args))),
        )
        with patch("pages.awp192x_modes.messagebox.showwarning") as warning:
            ButtonModePage.save_board_preset(page)
        warning.assert_called_once_with("error", "preset_slot_advanced_block_warning")
        self.assertEqual(writes, [])

    def test_read_current_settings_reads_chip_only_and_tooltip_explains_scope(self):
        reads = []
        finished = []
        serial = SimpleNamespace(
            is_connected=True,
            read_register_value=lambda dev, reg, bits: reads.append((dev, reg, bits))
            or SimpleNamespace(ok=True, data=reg),
            extract_rdataw_value=lambda data, **_kwargs: data,
        )
        page = SimpleNamespace(
            CURRENT_SETTING_REGISTERS=ButtonModePage.CURRENT_SETTING_REGISTERS,
            _ensure_connected=lambda: True,
            _set_preset_buttons_state=lambda _state: None,
            ctx=SimpleNamespace(state=SimpleNamespace(awp192x_device_addr=0x44), serial=serial),
            _read_current_settings_snapshot=lambda dev: ButtonModePage._read_current_settings_snapshot(page, dev),
            _finish_current_settings_read=lambda *args: finished.append(args),
            after=lambda _delay, callback, *args: callback(*args),
        )
        with patch("pages.awp192x_modes.threading.Thread", SynchronousThread):
            ButtonModePage.read_current_settings(page)

        self.assertEqual(
            reads,
            [(0x44, register, 16) for register in ButtonModePage.CURRENT_SETTING_REGISTERS],
        )
        self.assertEqual(len(finished), 1)
        self.assertIsNone(finished[0][3])

        tooltip_page = SimpleNamespace(
            READ_SETTINGS_TOOLTIP=ButtonModePage.READ_SETTINGS_TOOLTIP,
            language="en",
        )
        tooltip = ButtonModePage._read_settings_tooltip_text(tooltip_page)
        self.assertIn("into the page", tooltip)
        self.assertIn("does not load the selected slot", tooltip)
        self.assertIn("write any slot", tooltip)

    def test_waveform_storage_isolated_when_host_address_changes(self):
        state = SimpleNamespace(awp192x_device_addr=0x44, selected_port="COM1", extra={})
        page = SimpleNamespace(
            ctx=SimpleNamespace(state=state, serial=object()),
            page_name="button_mode",
            storage=WaveformStorage(),
            _chip_settings_device_key=None,
            _syncing_chip_settings=False,
            _sync_toolbar_slot=lambda: None,
        )
        first_model = ButtonModePage._select_awp192x_settings_model(page)
        first_storage = page.storage
        first_storage.add_or_replace(0, 0, 1, 1, "sine", [0x800])

        state.awp192x_device_addr = 0x45
        second_model = ButtonModePage._select_awp192x_settings_model(page)

        self.assertNotEqual(first_model.device_key, second_model.device_key)
        self.assertIsNot(first_storage, page.storage)
        self.assertEqual(page.storage.slices, {})

    def test_full_preset_preserves_shared_register_fields_owned_by_full_mode(self):
        writes = []
        state = SimpleNamespace()
        serial = SimpleNamespace(
            write_register_value=lambda dev, reg, value, bits: writes.append((dev, reg, value, bits))
            or SimpleNamespace(ok=True, error=None),
            read_register_value=lambda _dev, reg, bits: SimpleNamespace(ok=True, data=reg),
            extract_rdataw_value=lambda data, **_kwargs: 0x0040 if data == 0x11 else 0x4000,
        )
        full = SimpleNamespace(
            page_name="full_mode",
            ctx=SimpleNamespace(state=state, serial=serial),
            ADC_OUTPUT_ENABLE=0x8000,
            ADC_D2_CONFIG=0x4000,
            ENDLESS_PLAY_BIT=0x4000,
            _compose_full_reg14=lambda: 0xA57E,
            _compose_adc_config_0b=lambda: 0xA321,
            _compose_interrupt_mask=lambda: 0x5A5A,
            _compose_button_adc_event_config=lambda: 0x0221,
            _set_repeat_and_waveform_fields=lambda base, repeat, wave: base | ((repeat - 1) << 5) | wave,
        )

        result = ButtonModePage._write_preset_registers(
            full,
            0x44,
            duration=9,
            press_code=0x800,
            release_code=0x800,
            press_value=0x8020,
            release_repeat=1,
            adc_filter_config=0x44,
        )

        self.assertIsNone(result)
        self.assertIn((0x44, 0x14, 0xA57E, 16), writes)
        self.assertIn((0x44, 0x03, 0x5A5A, 16), writes)
        self.assertIn((0x44, 0x0B, 0xA321, 16), writes)
        self.assertNotIn((0x44, 0x14, 0x007E, 16), writes)
        self.assertNotIn((0x44, 0x0B, 0x0221, 16), writes)
        self.assertEqual(state.current_max_output_reg_value, 0xA57E)

        voltage_writes = []
        voltage_page = SimpleNamespace(
            _syncing_chip_settings=True,
            _reading_current_settings=False,
            max_voltage_var=Value(build_awp192x_amp_options()[0][0]),
            amp_options=build_awp192x_amp_options(),
            ctx=SimpleNamespace(state=SimpleNamespace()),
            _compose_full_reg14=lambda: 0xA502,
            _write_full_reg14=lambda **_kwargs: voltage_writes.append("write"),
        )
        FullModePage._on_voltage_changed(voltage_page)
        self.assertEqual(voltage_page.ctx.state.current_max_output_reg_value, 0xA502)
        self.assertEqual(voltage_writes, [])

    def test_full_script_export_operations_build_without_waveform_editor(self):
        page = SimpleNamespace(
            page_name="full_mode",
            ctx=SimpleNamespace(state=SimpleNamespace(awp192x_device_addr=0x44)),
            storage=WaveformStorage(),
            ADC_EVENT_CONFIGS=FullModePage.ADC_EVENT_CONFIGS,
            ADC_DETECT_ENABLE=0x8000,
            ADC_OUTPUT_ENABLE=0x8000,
            ADC_D2_CONFIG=0x4000,
            ENDLESS_PLAY_BIT=0x4000,
            press_repeat_count_var=Value("1"),
            release_repeat_count_var=Value("1"),
            play_repeat_count_var=Value("1"),
            press_waveform_var=Value("slice0"),
            release_waveform_var=Value("slice0"),
            play_waveform_var=Value("ROM sin"),
            endless_play_var=Value(False),
            _current_max_output_reg_value=lambda: 0x007E,
            _parse_script_duration=lambda: 10,
            _current_threshold_codes=lambda: (0x800, 0x800),
            _parse_full_adc_thresholds=lambda **_kwargs: (0x801, 0x7FF),
            _parse_script_repeat_count=lambda _value, _name: 1,
            _parse_script_waveform_slice=lambda value, _name: 31 if value == "ROM sin" else 0,
            _set_repeat_and_waveform_fields=lambda base, _repeat, wave: base | wave,
            _set_waveform_field=lambda _base, wave: wave,
            _set_repeat_field=lambda base, _repeat: base,
            _compose_full_reg14=lambda: 0xA57E,
            _compose_full_event_value=lambda key, **_kwargs: 0x8000 | (len(key) & 0x1F),
            _compose_full_manual_play_value=lambda **_kwargs: 0x401F,
            _compose_interrupt_mask=lambda: 0x1234,
            _compose_adc_config_0c=lambda: 0x0044,
            _compose_adc_config_0b=lambda: 0x0221,
            _compose_global_config=lambda: 0x5678,
            _compose_serial_register=lambda _index: 0x0101,
        )

        self.assertFalse(hasattr(page, "waveform_page"))
        operations = FullModePage._build_current_register_script_operations(page)
        writes = {(operation.reg_addr, operation.value) for operation in operations if operation.action == "WRITE"}

        self.assertIn((0x14, 0xA57E), writes)
        self.assertIn((0x03, 0x1234), writes)
        self.assertIn((0x0B, 0x0221), writes)
        self.assertIn((0x04, 0x5678), writes)
        self.assertTrue({0x0D, 0x0E, 0x10, 0x12}.issubset({reg for reg, _value in writes}))
        self.assertTrue({0x17, 0x18, 0x19, 0x1A}.issubset({reg for reg, _value in writes}))

    def test_type3_payload_blocks_more_than_eight_required_waveforms_without_editor(self):
        storage = WaveformStorage()
        for index in range(10):
            recipe = Awp192xWaveformRecipe(
                generator_type=AWP192X_GENERATOR_SINC,
                generator_version=AWP192X_GENERATOR_SINC_VERSION,
                slice_index=index,
                start_addr=index * 4,
                points1=1,
                points2=1,
                points3=1,
                positive_mv=1000,
                negative_mv=-1000,
            )
            values = list(generate_awp192x_waveform_points(recipe))
            storage.add_or_replace(index, recipe.start_addr, recipe.start_addr + len(values), len(values), "sinc", values, recipe)

        page = SimpleNamespace(
            storage=storage,
            max_voltage_var=Value(build_awp192x_amp_options()[0][0]),
            amp_options=build_awp192x_amp_options(),
            duration_var=Value("10"),
            press_threshold_var=Value("1"),
            release_threshold_var=Value("-0.01"),
            adc_average_time_var=Value(ButtonModePage.ADC_AVERAGE_OPTIONS[0][0]),
            adc_ovv_time_var=Value(ButtonModePage.ADC_OVV_OPTIONS[0][0]),
            press_repeat_count_var=Value("1"),
            release_repeat_count_var=Value("1"),
            play_repeat_count_var=Value("1"),
            press_waveform_var=Value("serial"),
            release_waveform_var=Value("slice8"),
            play_waveform_var=Value("slice9"),
            endless_play_var=Value(False),
            serial_wave_vars=[Value(f"slice{index}") for index in range(8)],
            serial_loop_vars=[Value("1") for _ in range(8)],
            SERIAL_WAVE_NUMBER=0x1E,
            ADC_DETECT_ENABLE=0x8000,
            ENDLESS_PLAY_BIT=0x4000,
            BUTTON_RUNTIME_PAYLOAD_VERSION=2,
            BUTTON_RUNTIME_FLAG_SERIAL_VALID=1,
            WAVEFORM_FIELD_MASK=ButtonModePage.WAVEFORM_FIELD_MASK,
            REPEAT_FIELD_MASK=ButtonModePage.REPEAT_FIELD_MASK,
            ADC_OUTPUT_ENABLE=0x8000,
            ADC_D2_CONFIG=0x4000,
            _current_max_output_reg_value=lambda: 2,
            _current_threshold_codes=lambda: (0x800, 0x800),
            _compose_button_adc_filter_config=lambda: 0,
            _parse_script_repeat_count=lambda text, _name: int(text, 0),
            _parse_script_waveform_slice=lambda text, _name: int(text.replace("slice", ""), 0) if text.startswith("slice") else 0x1E,
            _set_repeat_and_waveform_fields=lambda base, repeat, wave: base | ((repeat - 1) << 5) | wave,
            _set_waveform_field=lambda base, wave: base | wave,
            _set_repeat_field=lambda base, repeat: base | ((repeat - 1) << 5),
            _compose_button_adc_event_config=lambda: 0x0021,
        )

        self.assertFalse(hasattr(page, "waveform_page"))
        with self.assertRaisesRegex(ValueError, "at most 8 waveforms"):
            ButtonModePage.build_button_runtime_payload(page)

    def test_full_reg14_decode_and_sync_never_write_amplitude_register(self):
        options = build_awp192x_amp_options()
        state = SimpleNamespace()
        page = SimpleNamespace(
            SLOW_TIME_OPTIONS=FullModePage.SLOW_TIME_OPTIONS,
            DATA_DEPTH_OPTIONS=FullModePage.DATA_DEPTH_OPTIONS,
            DAC_MODE_OPTIONS=FullModePage.DAC_MODE_OPTIONS,
            amp_options=options,
            full_slow_up_var=Value("0"),
            full_slow_down_var=Value("0"),
            full_half_cycle_var=Value(False),
            full_phase_var=Value("0"),
            full_data_depth_var=Value("1024"),
            full_dac_mode_var=Value("SRAM/ROM"),
            max_voltage_var=Value(""),
            ctx=SimpleNamespace(state=state),
            _option_label=lambda table, code: FullModePage._option_label(table, code),
        )

        FullModePage._apply_full_reg14_settings(page, 0xA57E)

        self.assertEqual(page.full_slow_up_var.get(), FullModePage._option_label(FullModePage.SLOW_TIME_OPTIONS, 2))
        self.assertEqual(page.full_slow_down_var.get(), FullModePage._option_label(FullModePage.SLOW_TIME_OPTIONS, 2))
        self.assertFalse(page.full_half_cycle_var.get())
        self.assertEqual(page.full_phase_var.get(), "180")
        self.assertEqual(page.full_data_depth_var.get(), FullModePage._option_label(FullModePage.DATA_DEPTH_OPTIONS, (0xA57E >> 7) & 7))
        self.assertEqual(page.full_dac_mode_var.get(), FullModePage._option_label(FullModePage.DAC_MODE_OPTIONS, 2))
        self.assertEqual(page.max_voltage_var.get(), options[-1][0])
        self.assertEqual(state.current_max_output_reg_value, 0xA57E)

        writes = []
        page._syncing_chip_settings = False
        page._reading_current_settings = True
        page._compose_full_reg14 = lambda: (_ for _ in ()).throw(AssertionError("must return before compose"))
        page._write_full_reg14 = lambda **_kwargs: writes.append("write")
        page.max_voltage_var.set("unmatched amplitude label")
        FullModePage._on_voltage_changed(page)
        self.assertEqual(writes, [])

    def test_full_duration_sync_updates_hint_without_writing_register_15(self):
        writes = []
        page = SimpleNamespace(
            duration_var=Value("27"),
            ctx=SimpleNamespace(state=SimpleNamespace()),
            _syncing_chip_settings=True,
            _reading_current_settings=False,
            write_reg=lambda *args, **kwargs: writes.append((args, kwargs)),
            _full_period_points=lambda: 256,
            period_hint_var=Value(""),
            language="en",
        )

        FullModePage._update_period_hint(page)

        self.assertEqual(writes, [])
        self.assertEqual(page.ctx.state.single_point_duration_us, 27)
        self.assertIn("Waveform period", page.period_hint_var.get())

    def test_full_slot_payload_uses_fresh_shared_settings_snapshot(self):
        storage = WaveformStorage()
        recipes = {}
        for index in (2, 7):
            recipe = Awp192xSineWaveformRecipe(
                slice_index=index,
                start_addr=index * 8,
                points=4,
                start_phase_deg=0,
                end_phase_deg=90,
                amplitude_mv=1000,
                offset_mv=0,
            )
            recipes[index] = recipe
            values = list(generate_awp192x_waveform_points(recipe))
            storage.add_or_replace(index, recipe.start_addr, recipe.start_addr + len(values), len(values), "sine", values, recipe)

        model = Awp192xChipSettingsModel(shared={
            "output_amplitude": 0x007E,
            "point_duration": 42,
            "thresholds": {"press": 12.0, "release": -4.0},
            "filter_settings": {"average": 3, "ovv": 4},
            "polarity": 1,
            "range": 1,
            "press_action": {"repeat": 5, "wave": 7, "endless": False, "enabled": True},
            "release_action": {"repeat": 4, "wave": 2, "endless": False, "enabled": False},
            "manual_playback": {"repeat": 6, "wave": 7, "endless": False},
            "serial_waveforms": [{"wave": 0x1F, "repeat": 1} for _ in range(8)],
        })
        page = SimpleNamespace(
            page_name="full_mode",
            storage=storage,
            waveform_page=None,
            SERIAL_WAVE_NUMBER=0x1E,
            ADC_DETECT_ENABLE=0x8000,
            ENDLESS_PLAY_BIT=0x4000,
            ADC_EVENT_CONFIG=0x0321,
            BUTTON_RUNTIME_PAYLOAD_VERSION=2,
            BUTTON_RUNTIME_FLAG_SERIAL_VALID=1,
            WAVEFORM_FIELD_MASK=ButtonModePage.WAVEFORM_FIELD_MASK,
            REPEAT_FIELD_MASK=ButtonModePage.REPEAT_FIELD_MASK,
            _map_adc_voltage_to_code=lambda voltage, gain=0: 0x800 + round(float(voltage) * 10) + gain,
            _parse_script_repeat_count=lambda text, name: ButtonModePage._parse_script_repeat_count(page, text, name),
            _parse_script_waveform_slice=lambda text, name: ButtonModePage._parse_script_waveform_slice(page, text, name),
            _set_repeat_and_waveform_fields=lambda base, repeat, wave: ButtonModePage._set_repeat_and_waveform_fields(page, base, repeat, wave),
            _set_waveform_field=lambda base, wave: ButtonModePage._set_waveform_field(page, base, wave),
            _set_repeat_field=lambda base, repeat: ButtonModePage._set_repeat_field(page, base, repeat),
        )

        payload = ButtonModePage.build_button_runtime_payload(page, settings_model=model)

        self.assertEqual(payload.max_output_reg, 0x007E)
        self.assertEqual(payload.duration_reg, 41)
        self.assertEqual((payload.press_threshold_reg, payload.release_threshold_reg), (2169, 2009))
        self.assertEqual(payload.adc_filter_reg, 0x34)
        self.assertEqual(payload.adc_event_wait_press, 0x0321)
        self.assertEqual(payload.press_action_base, (4 << 5) | 7)
        self.assertEqual(payload.release_action_base, (3 << 5) | 2)
        self.assertEqual(payload.manual_play_reg, (5 << 5) | 7)
        self.assertEqual(payload.waveforms, (recipes[7], recipes[2]))

    def test_full_slot_save_preflights_and_serializes_the_same_snapshot(self):
        model = Awp192xChipSettingsModel(shared={"output_amplitude": 2})
        preflight = []
        serialized = []
        uploaded = []

        def capture_shared():
            model.update_shared({"press_action": {"wave": 7, "repeat": 5}})
            return model.shared

        def capture_advanced():
            model.set_advanced("slot_omitted", True)
            return model.advanced

        def preflight_snapshot(snapshot):
            preflight.append(snapshot)
            return True, None

        page = SimpleNamespace(
            page_name="full_mode",
            _ensure_connected=lambda: True,
            _chip_settings_model=model,
            _capture_shared_settings=capture_shared,
            _capture_full_advanced_settings=capture_advanced,
            build_board_stored_preset=lambda _page, settings_model: serialized.append(settings_model) or object(),
            board_slot_var=Value("2"),
            tr=lambda key: key,
            ctx=SimpleNamespace(
                state=SimpleNamespace(awp192x_device_addr=0x44),
                serial=SimpleNamespace(
                    sync_config_preset=lambda *args, **kwargs: uploaded.append((args, kwargs))
                    or SimpleNamespace(ok=True),
                ),
            ),
        )

        with patch("pages.awp192x_modes.can_save_slot", side_effect=preflight_snapshot), \
             patch("core.awp192x_button_runtime.pack_awp192x_button_payload", return_value=b"runtime"), \
             patch("core.arduino_preset_sync.pack_v2_preset", return_value=b"preset"), \
             patch("core.arduino_preset_sync.signature_for_address", return_value=0x1923), \
             patch("pages.awp192x_modes.messagebox.showwarning") as warning, \
             patch("pages.awp192x_modes.messagebox.showinfo"):
            ButtonModePage.save_board_preset(page)

        warning.assert_called_once()
        self.assertIs(preflight[0], serialized[0])
        self.assertIsNot(preflight[0], model)
        self.assertEqual(preflight[0].shared["press_action"], {"wave": 7, "repeat": 5})
        self.assertTrue(preflight[0].advanced["slot_omitted"])
        self.assertEqual(uploaded[0][0][0], 2)

    def test_settings_key_and_selector_switch_follow_page_session_generation(self):
        state = SimpleNamespace(
            awp192x_device_addr=0x44,
            selected_port="COM1",
            extra={"serial_port_awp1921_1923": "COM1", "generation": 1},
        )
        serial = SimpleNamespace(
            selected_port_for_page=lambda name: state.extra.get(f"serial_port_{name}", state.selected_port),
            page_connection_identity=lambda name: (
                state.extra.get(f"serial_port_{name}", state.selected_port),
                51,
                state.extra["generation"],
            ),
        )
        ctx = SimpleNamespace(state=state, serial=serial)
        self.assertEqual(
            connection_key_for_context(ctx, "awp1921_1923"),
            (id(serial), "COM1", 51, 1),
        )

        class Combo:
            def bind(self, _sequence, callback, add=None):
                self.callback = callback

        combo = Combo()
        host = SimpleNamespace(
            page_name="awp1921_1923",
            _serial_selectors=[{"page_name": "awp1921_1923", "combo": combo}],
            current_mode="button_mode",
            mode_pages={},
        )
        page = SimpleNamespace(
            master=host,
            ctx=ctx,
            page_name="button_mode",
            storage=WaveformStorage(),
            _chip_settings_device_key=None,
            _syncing_chip_settings=False,
            _sync_toolbar_slot=lambda: None,
            _capture_shared_settings=lambda: {},
            after_idle=lambda callback: callback(),
            _awp192x_settings_host=lambda: host,
            _awp192x_settings_page_name=lambda: host.page_name,
        )
        page._select_awp192x_settings_model = lambda: ButtonModePage._select_awp192x_settings_model(page)
        page._on_awp192x_settings_connection_changed = lambda event=None: ButtonModePage._on_awp192x_settings_connection_changed(page, event)
        host.mode_pages["button_mode"] = page
        first = page._select_awp192x_settings_model()

        ButtonModePage._bind_awp192x_settings_connection_selector(page)
        state.extra["serial_port_awp1921_1923"] = "COM2"
        state.extra["generation"] = 2
        combo.callback(None)

        self.assertIsNot(page._chip_settings_model, first)
        self.assertNotEqual(page._chip_settings_model.device_key, first.device_key)
        self.assertEqual(page._chip_settings_model.device_key.connection, (id(serial), "COM2", 51, 2))


if __name__ == "__main__":
    unittest.main()
