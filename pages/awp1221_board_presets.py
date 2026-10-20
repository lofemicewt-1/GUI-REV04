"""AWP1221 board-slot actions and GUI recipe snapshots (no chip reads on save)."""
from __future__ import annotations

from tkinter import messagebox

from core.arduino_preset_sync import (
    PAYLOAD_TYPE_AWP1221_RUNTIME, V2Preset, format_preset_summary,
    pack_v2_preset, unpack_v2_preset,
)
from core.awp1221_preset import (
    Awp1221PresetModel, Awp1221WaveRecipe, ROM_KINDS,
    allocate_awp1221_slices, pack_awp1221_payload, unpack_awp1221_payload,
)
from pages.chip_settings_model import can_save_slot


class AWP1221BoardPresetMixin:
    def _init_board_recipe_tracking(self) -> None:
        self._board_rom_values = {}
        self._board_rom_loading = False
        self._board_rom_slice = self._parse_waveform_slice(self.rom_slice_var.get())
        self._board_rom_defaults = self._board_rom_snapshot()
        self._board_preset_busy = False
        self._remember_board_rom()
        self.rom_slice_var.trace_add("write", lambda *_: self._on_board_rom_slice_changed())
        for variable in self._board_rom_variables():
            variable.trace_add("write", lambda *_: self._remember_board_rom())

    def _board_rom_variables(self):
        return (self.rom_waveform_var, self.rom_vpk_var, self.rom_micro_bias_var,
                self.rom_offset_var, self.rom_point_time_var, self.rom_repeat_var, self.rom_source_var)

    def _board_rom_snapshot(self):
        return tuple(str(variable.get()) for variable in self._board_rom_variables())

    def _remember_board_rom(self) -> None:
        if getattr(self, "_board_rom_loading", True) or self._sinc_selected():
            return
        self._board_rom_values[self._board_rom_slice] = self._board_rom_snapshot()

    def _on_board_rom_slice_changed(self) -> None:
        if self._board_rom_loading:
            return
        self._remember_board_rom()
        self._board_rom_slice = self._parse_waveform_slice(self.rom_slice_var.get())
        values = self._board_rom_values.get(self._board_rom_slice, self._board_rom_defaults)
        self._board_rom_loading = True
        try:
            for variable, value in zip(self._board_rom_variables(), values):
                variable.set(value)
        finally:
            self._board_rom_loading = False
        self._remember_board_rom()

    def _capture_type4_model(self) -> Awp1221PresetModel:
        from pages.awp1221_page import (
            AWP1221_RETURN_BIT_TIME_OPTIONS, AWP1221_ZERO_TIME_OPTIONS,
            awp1221_format_threshold_signed, awp1221_parse_threshold_text,
            awp1221_sinc_serial_group,
        )
        self._remember_board_rom()
        active = getattr(self, "_sinc_active_group", None)
        if active:
            self._sinc_group_values[active] = self._sinc_group_snapshot()
        # The currently visible limits are shared by every referenced SINC recipe.
        current_sinc = None
        if self._sinc_selected():
            current_sinc = awp1221_sinc_serial_group(self.sinc_serial_var.get())[1]
        settings_model = None
        stage_values = []
        if self._button_stage_vars:
            # Keep Button Mode's raw view reads: these preserve its established
            # validation text and the exact threshold spelling (including 0x800).
            stage_values = [
                {
                    "wave": variables["wave"].get(),
                    "threshold": variables["threshold"].get(),
                    "repeat": variables["repeat"].get(),
                    "jump_target": variables["jump_target"].get(),
                }
                for variables in self._button_stage_vars
            ]
        elif self.mode_key == "full_mode":
            settings_model = self._capture_settings_model()
            if settings_model is None:
                raise ValueError("AWP1221 settings are not available")
            for stage_index in range(2):
                semantics = settings_model.shared.get(f"stage{stage_index}_semantics", {})
                button = semantics.get("button") if isinstance(semantics, dict) else None
                if not isinstance(button, dict):
                    raise ValueError(f"Full Mode Stage{stage_index} settings are unavailable")
                threshold = int(button.get("threshold", 0)) & 0x0FFF
                threshold_text = (
                    "0x800" if threshold == 0x800
                    else awp1221_format_threshold_signed(threshold)
                )
                target = int(button.get("target", 0))
                stage_values.append({
                    "wave": self._format_trigger_waveform(int(button.get("wave", 0))),
                    "threshold": threshold_text,
                    "repeat": str(button.get("repeat", 0)),
                    "jump_target": f"Stage{target}",
                })
        recipes, refs, thresholds, repeats, jumps = [], [], [], [], []
        for values_for_stage in stage_values:
            start = self._parse_trigger_waveform(values_for_stage["wave"])
            values = self._board_rom_values.get(start)
            if values is not None and start != current_sinc:
                kind, coarse, fine, offset, time, repeat, source = values
                if source == "RAM":
                    raise ValueError(f"slice{start} contains raw RAM samples, not a reconstructible ROM/SINC recipe")
                if kind != "SINE":
                    raise ValueError(self.tr("awp1221_type4_waveform_unsupported").format(
                        slice=start, waveform=kind))
                recipe = Awp1221WaveRecipe(ROM_KINDS.index(kind), self._parse_binary_option(coarse),
                                          self._parse_binary_option(fine), self._parse_binary_option(offset),
                                          (int(time.strip(), 0),), int(repeat.strip(), 0))
            elif start in (18, 21, 24, 28):
                serial = {18: "serial1", 21: "serial2", 24: "serial3", 28: "serial4"}[start]
                values = self._sinc_group_values[serial]
                # Snapshot stores four editable times for every group; legacy groups use first three.
                count = 4 if start >= 24 else 3
                times = tuple(int(text.strip(), 0) for text in values[2:2 + count])
                recipe = Awp1221WaveRecipe(5 if count == 4 else 4, times=times,
                                          repeat=int(values[6].strip(), 10) if count == 4 else 1)
            else:
                raise ValueError(f"slice{start} has no GUI waveform recipe; select that slice and enter its ROM parameters first")
            if recipe not in recipes:
                recipes.append(recipe)
            refs.append(recipes.index(recipe))
            thresholds.append(awp1221_parse_threshold_text(str(values_for_stage["threshold"])))
            repeats.append(int(str(values_for_stage["repeat"]), 0))
            target = str(values_for_stage["jump_target"])
            if target not in ("Stage0", "Stage1"):
                raise ValueError(f"Jump target must be Stage0 or Stage1, not {target}")
            jumps.append(0 if target == "Stage0" else 1)
        has_sinc = any(recipe.kind >= 4 for recipe in recipes)
        if settings_model is None:
            settings_model = self._capture_settings_model()
        if settings_model is None:
            raise ValueError("AWP1221 settings are not available")
        allowed, warning_key = can_save_slot(settings_model)
        if not allowed:
            raise ValueError(self.tr(warning_key or "preset_slot_advanced_block_warning"))
        model = Awp1221PresetModel(
            AWP1221_ZERO_TIME_OPTIONS.index(self.zero_time_var.get()),
            AWP1221_RETURN_BIT_TIME_OPTIONS.index(self.press_return_bit_time_var.get()),
            AWP1221_RETURN_BIT_TIME_OPTIONS.index(self.release_return_bit_time_var.get()),
            float(self.sinc_max_voltage_var.get()) if has_sinc else 0.0,
            float(self.sinc_min_voltage_var.get()) if has_sinc else 0.0,
            tuple(thresholds), tuple(refs), tuple(repeats), tuple(recipes), tuple(jumps),
        )
        pack_awp1221_payload(model)
        return model

    def _read_type4_record(self, slot: int, address: int):
        response = self.ctx.serial.cfg_read(slot)
        if not response.ok:
            raise ValueError(response.error or "CFGREAD failed")
        encoded = self.ctx.serial._extract_cfgread_hex(response.data)
        if encoded is None:
            raise ValueError("Invalid CFGREAD length or transport CRC")
        raw = bytes.fromhex(encoded)
        preset = unpack_v2_preset(raw)
        if preset.payload_type != PAYLOAD_TYPE_AWP1221_RUNTIME or preset.device_addr != address:
            raise ValueError("This slot does not contain a Type-4 preset for the selected AWP1221 address")
        return raw, unpack_awp1221_payload(preset.payload)

    def _board_operation(self, action: str) -> None:
        if getattr(self, "_board_preset_busy", False):
            return
        if not self._ensure_connected():
            return
        from pages.awp1221_page import _run_ui_background_task, _serial_batch
        try:
            settings_model = self._capture_settings_model()
            if settings_model is None:
                raise ValueError("AWP1221 settings are not available")
            slot = int(settings_model.slot_selection if settings_model.slot_selection is not None else self.board_slot_var.get())
            address = self.device_addr()
            raw = None
            if action == "save":
                try:
                    model = self._capture_type4_model()
                except ValueError as exc:
                    warning = self.tr("preset_slot_advanced_block_warning")
                    if str(exc) == warning:
                        messagebox.showwarning(self.tr("info"), warning)
                        return
                    raise
                raw = pack_v2_preset(V2Preset(PAYLOAD_TYPE_AWP1221_RUNTIME, 0x1221,
                                              address, pack_awp1221_payload(model)))
        except (ValueError, TypeError) as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        self._board_preset_busy = True
        status_token = self.begin_status("preset")
        if action in ("read", "apply"):
            self._invalidate_sinc_verified()
        self._set_board_action_buttons_state("disabled")

        def worker():
            with _serial_batch(self.ctx.serial):
                if action == "save":
                    response = self.ctx.serial.sync_config_preset(slot, raw, set_active=False)
                    if not response.ok:
                        raise ValueError(response.error or "CFG upload failed")
                    return None
                stored, decoded = self._read_type4_record(slot, address)
                response = (self.ctx.serial.cfg_use(slot) if action == "set_active"
                            else self.ctx.serial.cfg_apply(slot))
                if not response.ok:
                    raise ValueError(response.error or "CFG operation failed")
                readback = self._read_type4_applied_registers(address) if action in ("read", "apply") else None
                return (stored, decoded, readback)

        def finish(result=None, error=None):
            self._board_preset_busy = False
            self._set_board_action_buttons_state("normal")
            if error is not None:
                self.finish_status(status_token, success=False, error=str(error))
                if action in ("read", "apply"):
                    self._set_detection_state(False, address)
                    self._set_waveform_play_state(False, address)
                messagebox.showerror(self.tr("error"), str(error))
                return
            try:
                if action in ("read", "apply"):
                    self._restore_type4_model(result[1])
                    self._restore_type4_applied_registers(result[2])
                    settings_model = self._current_settings_model()
                    if settings_model is not None:
                        settings_model.mark_applied()
                    self._invalidate_sinc_verified()
                    self._set_detection_state(True, address)
                    self._set_waveform_play_state(False, address)
                    if self._sinc_selected():
                        from core.awp1221_sinc import awp1221_sinc_plan_key
                        plan = self._build_current_sinc_plan()
                        key = awp1221_sinc_plan_key(plan, address)
                        self._sinc_verified_key = key
                        shared = self._shared_state_for(address)
                        if shared is not None:
                            shared.sinc_key = key
                    self._refresh_waveform_controls()
                text = (format_preset_summary(result[0]) if action == "read" else
                        self.tr("board_preset_saved") if action == "save" else
                        self.tr("board_preset_read").format(slot=slot))
            except Exception as exc:
                self.finish_status(status_token, success=False, error=str(exc))
                messagebox.showerror(self.tr("error"), str(exc))
                return
            if action == "save" and self._has_independent_advanced_settings():
                text = f"{text}\n\n{self.tr('preset_slot_advanced_scope_warning')}"
            self.finish_status(status_token, success=True)
            messagebox.showinfo(self.tr("info"), text)

        _run_ui_background_task(self, worker, on_done=finish,
                                on_error=lambda error: finish(error=error))

    def save_board_preset(self):
        self._board_operation("save")

    def set_board_preset_active(self):
        self._board_operation("set_active")

    def apply_board_preset(self):
        self._board_operation("apply")

    def read_board_preset(self):
        self._board_operation("read")

    def _set_board_action_buttons_state(self, state: str) -> None:
        toolbar = getattr(self, "preset_toolbar", None)
        if toolbar is not None:
            for action in ("save_slot", "set_active_slot", "apply_slot", "read_and_apply"):
                toolbar.buttons[action].configure(state=state)
        for key in ("save_to_board", "set_active", "apply_slot", "read_slot"):
            button = getattr(self, "_buttons", {}).get(key)
            if button is not None:
                button.configure(state=state)

    def _read_type4_applied_registers(self, address: int) -> dict[int, int]:
        if not callable(getattr(self.ctx.serial, "read_register_value", None)) or not callable(
            getattr(self.ctx.serial, "extract_rdataw_value", None)
        ):
            return {}
        registers = (0x05, 0x06, 0x08, 0x09, 0x0A, 0x0B, 0x0C,
                     0x0D, 0x0E, 0x0F, 0x10, 0x11)
        values: dict[int, int] = {}
        for register in registers:
            response = self.ctx.serial.read_register_value(address, register, bits=16)
            if not response.ok:
                raise ValueError(response.error or f"Failed to read register 0x{register:02X} after Type-4 apply")
            value = self.ctx.serial.extract_rdataw_value(
                response.data, device_addr=address, reg_addr=register, bits=16
            )
            if value is None:
                raise ValueError(response.data or f"Invalid register 0x{register:02X} readback")
            values[register] = int(value) & 0xFFFF
        return values

    def _restore_type4_applied_registers(self, values: dict[int, int] | None) -> None:
        if not values:
            return
        model = self._current_settings_model()
        if model is None:
            return
        for stage_index, registers in enumerate(self.STAGE_REGISTER_RANGES):
            stage_values = tuple(values[register] for register in registers)
            control = stage_values[0]
            event_mode = "press" if (control & 0x0033) == 0x0011 else "release"
            current = dict(model.shared.get(f"stage{stage_index}_semantics", {}))
            current["register_values"] = stage_values
            current["button"] = self._button_config_from_register_values(stage_index, stage_values, event_mode)
            current["type4_unsupported"] = self._type4_unsupported_stage(
                stage_values, current["button"], stage_index
            )
            model.set_shared(f"stage{stage_index}_semantics", current)
        model.update_shared({
            "type4_applied_settings": {
                "register_05": values[0x05], "register_06": values[0x06]
            }
        })
        unsupported = tuple(sorted({
            reason
            for index in range(2)
            for reason in model.shared[f"stage{index}_semantics"].get("type4_unsupported", ())
        }))
        model.set_advanced("type4_unsupported", unsupported)
        if self.host is not None:
            self.host.apply_settings_model(model)
        else:
            self._apply_settings_model_to_view(model)

    def _has_independent_advanced_settings(self) -> bool:
        model = self._current_settings_model()
        if model is None:
            return False
        advanced = dict(model.advanced)
        advanced.pop("type4_unsupported", None)
        gpio = advanced.pop("gpio", None)
        if advanced and any(bool(value) for value in advanced.values()):
            return True
        channels = gpio.get("channels", ()) if isinstance(gpio, dict) else ()
        return any(isinstance(channel, dict) and channel.get("function") is not None for channel in channels)

    def _restore_type4_model(self, model: Awp1221PresetModel) -> None:
        from pages.awp1221_page import AWP1221_RETURN_BIT_TIME_OPTIONS, AWP1221_ZERO_TIME_OPTIONS
        settings_model = self._current_settings_model()
        if settings_model is None:
            return
        starts = allocate_awp1221_slices(model.waves)
        descriptors = dict(settings_model.shared.get("waveform_descriptors", {}))
        rom_by_slice = dict(descriptors.get("rom_by_slice", {}))
        sinc_groups = dict(descriptors.get("sinc_groups", {}))
        for wave, start in zip(model.waves, starts):
            if wave.kind < 4:
                rom_by_slice[str(start)] = [
                    ROM_KINDS[wave.kind], self.VPK_OPTIONS[wave.coarse],
                    self.MICRO_BIAS_OPTIONS[wave.fine], self.OFFSET_OPTIONS[wave.offset],
                    str(wave.times[0]), str(wave.repeat), "ROM",
                ]
            else:
                serial = {18: "serial1", 21: "serial2", 24: "serial3", 28: "serial4"}[start]
                times = (*wave.times, *(1 for _ in range(4 - len(wave.times))))
                sinc_groups[serial] = [str(model.peak), str(model.trough),
                                       *(str(t) for t in times), str(wave.repeat), "512", "-"]
        semantic_values = {}
        for stage_index in range(2):
            old = settings_model.shared.get(f"stage{stage_index}_semantics", {})
            old_registers = tuple(old.get("register_values", (0, 0, 0, 0, 0)))
            registers = self.STAGE_REGISTER_RANGES[stage_index]
            current = {register: int(value) & 0xFFFF for register, value in zip(registers, old_registers)}
            if len(current) != 5:
                current = {register: 0 for register in registers}
            mode = "press" if stage_index == 0 else "release"
            config = {
                "mode": mode, "time_code": 3, "target": model.jumps[stage_index],
                "debounce": False, "use_high_12_bits": True,
                "threshold": model.thresholds[stage_index],
                "wave": starts[model.wave_refs[stage_index]],
                "repeat": model.event_repeats[stage_index],
            }
            current.update(self._button_stage_values_from_config(stage_index, current, config))
            values = tuple(current[register] for register in registers)
            semantic_values[f"stage{stage_index}_semantics"] = {
                "button": config,
                "register_values": values,
                "type4_unsupported": self._type4_unsupported_stage(values, config, stage_index),
            }
        zero_time = self._option_value(model.zero_time, AWP1221_ZERO_TIME_OPTIONS)
        press_return = self._option_value(model.press_return, AWP1221_RETURN_BIT_TIME_OPTIONS)
        release_return = self._option_value(model.release_return, AWP1221_RETURN_BIT_TIME_OPTIONS)
        settings = dict(settings_model.shared.get("adc_settings", {}))
        settings.update({"zero_time": zero_time, "press_return_bit_time": press_return,
                         "release_return_bit_time": release_return})
        first_recipe = model.waves[model.wave_refs[0]]
        first_start = starts[model.wave_refs[0]]
        first_serial = next((
            serial for serial, start, _end in (
                ("serial1", 18, 20), ("serial2", 21, 23),
                ("serial3", 24, 27), ("serial4", 28, 31),
            ) if start == first_start
        ), "serial1")
        first_waveform = "SINC" if first_recipe.kind >= 4 else ROM_KINDS[first_recipe.kind]
        recipe_update = {
            "kind": "sinc" if first_recipe.kind >= 4 else "rom",
            "serial": first_serial,
            "slice_index": first_start,
            "source": "ROM",
            "waveform": first_waveform,
            "auto_serial": True,
            "sinc_group": sinc_groups.get(first_serial, []),
        }
        if first_recipe.kind < 4:
            from pages.awp1221_page import awp1221_phase_options

            half_cycle = self.HALF_CYCLE_OPTIONS_BY_WAVEFORM[first_waveform][0]
            recipe_update.update({
                "rom_depth": "512",
                "micro_bias": self.MICRO_BIAS_OPTIONS[first_recipe.fine],
                "vpk": self.VPK_OPTIONS[first_recipe.coarse],
                "offset": self.OFFSET_OPTIONS[first_recipe.offset],
                "half_cycle": half_cycle,
                "phase": awp1221_phase_options(first_waveform, 0)[0],
                "point_time": str(first_recipe.times[0]),
                "rom_repeat": str(first_recipe.repeat),
                "infinite_loop": False,
            })
        settings_model.update_shared({
            "waveform_descriptors": {"rom_by_slice": rom_by_slice, "sinc_groups": sinc_groups},
            **semantic_values,
            "adc_settings": settings,
            "waveform_recipe": {
                **dict(settings_model.shared.get("waveform_recipe", {})),
                **recipe_update,
            },
        })
        unsupported = tuple(sorted({
            reason for stage in semantic_values.values()
            for reason in stage.get("type4_unsupported", ())
        }))
        settings_model.set_advanced("type4_unsupported", unsupported)
        settings_model.mark_applied()
        if self.host is not None:
            self.host.apply_settings_model(settings_model)
        else:
            self._apply_settings_model_to_view(settings_model)

    @staticmethod
    def _option_value(index: int, options: tuple[str, ...]) -> str:
        return options[int(index)]

    def _restore_type4_model_vars(self, model: Awp1221PresetModel) -> None:
        from pages.awp1221_page import (
            AWP1221_RETURN_BIT_TIME_OPTIONS, AWP1221_ZERO_TIME_OPTIONS,
            awp1221_format_threshold_signed,
        )
        starts = allocate_awp1221_slices(model.waves)
        self.zero_time_var.set(AWP1221_ZERO_TIME_OPTIONS[model.zero_time])
        self.press_return_bit_time_var.set(AWP1221_RETURN_BIT_TIME_OPTIONS[model.press_return])
        self.release_return_bit_time_var.set(AWP1221_RETURN_BIT_TIME_OPTIONS[model.release_return])
        for stage, variables in enumerate(self._button_stage_vars):
            threshold = model.thresholds[stage]
            variables["threshold"].set("0x800" if threshold == 0x800 else awp1221_format_threshold_signed(threshold))
            variables["wave"].set(self._format_trigger_waveform(starts[model.wave_refs[stage]]))
            variables["repeat"].set(str(model.event_repeats[stage]))
            variables["debounce"].set(False)
            variables["event_mode"].set("press" if stage == 0 else "release")
            variables["ovv_time"].set("500 us")
            variables["jump_target"].set(f"Stage{model.jumps[stage]}")
            variables["use_high_12_bits"].set(True)
        self._board_rom_values.clear()
        for wave, start in zip(model.waves, starts):
            if wave.kind < 4:
                self._board_rom_values[start] = (
                    ROM_KINDS[wave.kind], self.VPK_OPTIONS[wave.coarse], self.MICRO_BIAS_OPTIONS[wave.fine],
                    self.OFFSET_OPTIONS[wave.offset], str(wave.times[0]), str(wave.repeat), "ROM")
            else:
                serial = {18: "serial1", 21: "serial2", 24: "serial3", 28: "serial4"}[start]
                times = (*wave.times, *(1 for _ in range(4 - len(wave.times))))
                self._sinc_group_values[serial] = (str(model.peak), str(model.trough),
                                                 *(str(t) for t in times), str(wave.repeat), "512", "-")
        # Restore the editor for the first referenced recipe without serial writes.
        first = model.wave_refs[0]
        wave, start = model.waves[first], starts[first]
        self._board_rom_loading = self._sinc_loading_group = True
        try:
            if any(recipe.kind >= 4 for recipe in model.waves):
                self.sinc_max_voltage_var.set(str(model.peak))
                self.sinc_min_voltage_var.set(str(model.trough))
            if wave.kind < 4:
                self._board_rom_slice = start
                self.rom_slice_var.set(f"slice{start}")
                for variable, value in zip(self._board_rom_variables(), self._board_rom_values[start]):
                    variable.set(value)
                self.rom_depth_var.set("512")
                self.rom_half_cycle_var.set(self.HALF_CYCLE_OPTIONS_BY_WAVEFORM[ROM_KINDS[wave.kind]][0])
                from pages.awp1221_page import awp1221_phase_options
                self.rom_phase_var.set(awp1221_phase_options(ROM_KINDS[wave.kind], 0)[0])
                self.rom_infinite_loop_var.set(False)
            else:
                serial = {18: "serial1", 21: "serial2", 24: "serial3", 28: "serial4"}[start]
                self.rom_waveform_var.set("SINC")
                self.rom_source_var.set("ROM")
                self.sinc_serial_var.set(serial)
                self._sinc_active_group = serial
                values = self._sinc_group_values[serial]
                for variable, value in zip((self.sinc_max_voltage_var, self.sinc_min_voltage_var,
                                             *self._sinc_time_variables(), self.sinc_sin_repeat_var,
                                             self.sinc_depth_var, self.sinc_window_var), values):
                    variable.set(value)
        finally:
            self._board_rom_loading = self._sinc_loading_group = False
        self._refresh_waveform_controls()
        self._schedule_button_rom_preview()
