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
            awp1221_parse_threshold_text, awp1221_sinc_serial_group,
        )
        self._remember_board_rom()
        active = getattr(self, "_sinc_active_group", None)
        if active:
            self._sinc_group_values[active] = self._sinc_group_snapshot()
        # The currently visible limits are shared by every referenced SINC recipe.
        current_sinc = None
        if self._sinc_selected():
            current_sinc = awp1221_sinc_serial_group(self.sinc_serial_var.get())[1]
        recipes, refs, thresholds, repeats, jumps = [], [], [], [], []
        for variables in self._button_stage_vars:
            start = self._parse_trigger_waveform(variables["wave"].get())
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
            thresholds.append(awp1221_parse_threshold_text(variables["threshold"].get()))
            repeats.append(int(variables["repeat"].get(), 0))
            target = str(variables["jump_target"].get())
            if target not in ("Stage0", "Stage1"):
                raise ValueError(f"Jump target must be Stage0 or Stage1, not {target}")
            jumps.append(0 if target == "Stage0" else 1)
        has_sinc = any(recipe.kind >= 4 for recipe in recipes)
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
        if self.mode_key != "button_mode" or getattr(self, "_board_preset_busy", False):
            return
        if not self._ensure_connected():
            return
        from pages.awp1221_page import _run_ui_background_task, _serial_batch
        try:
            slot, address = int(self.board_slot_var.get()), self.device_addr()
            raw = None
            if action == "save":
                model = self._capture_type4_model()
                raw = pack_v2_preset(V2Preset(PAYLOAD_TYPE_AWP1221_RUNTIME, 0x1221,
                                              address, pack_awp1221_payload(model)))
        except (ValueError, TypeError) as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return
        self._board_preset_busy = True
        if action in ("read", "apply"):
            self._invalidate_sinc_verified()
        for key in ("save_to_board", "set_active", "apply_slot", "read_slot"):
            self._buttons[key].configure(state="disabled")

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
                return (stored, decoded)

        def finish(result=None, error=None):
            self._board_preset_busy = False
            for key in ("save_to_board", "set_active", "apply_slot", "read_slot"):
                self._buttons[key].configure(state="normal")
            if error is not None:
                if action in ("read", "apply"):
                    self._set_detection_state(False, address)
                    self._set_waveform_play_state(False, address)
                messagebox.showerror(self.tr("error"), str(error))
                notify = getattr(self, "_notify_header_done", None)
                if callable(notify):
                    notify()
                return
            if action in ("read", "apply"):
                self._restore_type4_model(result[1])
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
            messagebox.showinfo(self.tr("info"), text)
            notify = getattr(self, "_notify_header_done", None)
            if callable(notify):
                notify()

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

    def _restore_type4_model(self, model: Awp1221PresetModel) -> None:
        suspend = getattr(self, "_suspend_adc_live_writes", None)
        restore = type(self)._restore_type4_model_vars
        if suspend is None:
            restore(self, model)
            return
        with suspend():
            restore(self, model)

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
        self._draw_button_rom_preview()
