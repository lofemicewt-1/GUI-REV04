"""AWP192x Button presentation; chip I/O remains in awp192x_modes."""
from __future__ import annotations

import math
import threading
from core.ui_dispatch import post_to_ui
from core.base_tool import parse_gpio_read_response
import tkinter as tk
from tkinter import ttk

from pages.base_page import fit_toplevel_to_screen
from pages.awp192x_realtime import AWP192xADCVoltageWindow
from pages.waveform_generator import WaveformGeneratorPage, WAVEFORM_WRITE_POINT_OPTIONS


def draw_timed_waveform(canvas, colors, tr, segments, point_us):
    """Plot sample-and-hold time, with every segment's true end node."""
    canvas.delete("all")
    canvas.configure(bg=colors["canvas_bg"], highlightbackground=colors["border"])
    width, height = max(300, canvas.winfo_width()), max(220, canvas.winfo_height())
    left, right, top = 16, width - 16, 28
    columns = 2 if width < 720 else 3
    columns = min(columns, max(1, len(segments)))
    rows = (len(segments) + columns - 1) // columns
    desired_height = max(420, 260 + rows * 54)
    if int(canvas.cget("height")) != desired_height:
        canvas.configure(height=desired_height)
    bottom = height - 60 - rows * 54
    count = sum(len(values) for _, values in segments)
    if not count:
        return
    values = [v for _, data in segments for v in data]
    low, high = min(0, min(values)), max(0, max(values))
    span = max(1, high - low)
    low, high = low - span * .18, high + span * .18
    x_of = lambda n: left + (right - left) * n / count
    y_of = lambda v: bottom - (v - low) / (high - low) * (bottom - top)
    y_zero = y_of(0)
    canvas.create_rectangle(left, top, right, bottom, outline=colors["border"])
    canvas.create_line(left, y_zero, right, y_zero, fill=colors["muted"], dash=(3, 3))
    canvas.create_text(left + 3, y_zero + 3, text="GND 0 V", anchor="nw", fill=colors["muted"])
    coords = []
    for index, value in enumerate(values):
        coords.extend((x_of(index), y_of(value)))
    coords.extend((right, y_of(values[-1])))
    if len(coords) >= 4:
        canvas.create_line(*coords, fill=colors["primary"], width=2, tags="wave_curve")
    for value, key, color in ((max(values), "awp1221_preview_peak", "#F04444"),
                               (min(values), "awp1221_preview_trough", colors["primary"])):
        index = values.index(value)
        x, y = x_of(index), y_of(value)
        canvas.create_line(x, y_zero, x, y, fill=color, arrow="last")
        canvas.create_text(x, y - 5 if value >= 0 else y + 5,
                           anchor="s" if value >= 0 else "n",
                           text=f"{tr(key)} {value:+.2f} V", fill=color, tags="extremum")
    previous_right = -1
    ends = [0]
    for _, data in segments:
        ends.append(ends[-1] + len(data))
    for i, end in enumerate(ends):
        x = x_of(end)
        canvas.create_line(x, top, x, bottom, fill=colors["border"], dash=(2, 3))
        item = canvas.create_text(x, bottom + 7, text=f"{end * point_us / 1000:g} ms",
                                  anchor="nw" if i == 0 else "ne" if i == len(ends)-1 else "n",
                                  fill=colors["muted"], tags="time_axis")
        box = canvas.bbox(item)
        if box and box[0] <= previous_right + 3:
            if i == len(ends)-1:
                canvas.move(item, 0, 14)
            else:
                canvas.itemconfigure(item, state="hidden")
        elif box:
            previous_right = box[2]
    total = count * point_us / 1000
    canvas.create_text(left, bottom + 34, anchor="nw", fill=colors["text"], tags="wave_duration",
                       text=tr("awp1221_preview_total_time").format(duration=f"{total:g}"))
    for i, (name, data) in enumerate(segments):
        row, col = divmod(i, columns)
        cell = (right - left) / columns
        card_top = bottom + 55 + row * 54
        canvas.create_rectangle(left + col * cell, card_top, left + (col+1) * cell - 4, card_top + 52,
                                outline="", tags="segment_card")
        canvas.create_text(left + col * cell + 3, card_top + 2, anchor="nw",
                           fill=colors["primary"], font=("Microsoft YaHei UI", 9),
                           width=max(30, cell-10), tags="segment_duration",
                           text=tr("awp1221_segment_time").format(name=name,
                               duration=f"{len(data)*point_us/1000:g}", end=f"{ends[i+1]*point_us/1000:g}"))
    # Peak/trough labels must also stay inside narrow canvases.
    for item in canvas.find_withtag("extremum"):
        box = canvas.bbox(item)
        if box:
            canvas.move(item, max(4-box[0], min(0, width-4-box[2])), 0)


class ButtonWaveformPage(WaveformGeneratorPage):
    """Compact Button-only editor; standard generator and Full Mode stay intact."""
    def __init__(self, parent, ctx, *, storage, owner):
        self.owner = owner
        self.source = "ram"
        super().__init__(parent, ctx, embedded=True, storage=storage)

    def _build_ui(self):
        self.columnconfigure(0, weight=0, minsize=550)
        self.columnconfigure(1, weight=1, minsize=380)
        self.rowconfigure(0, weight=1)
        self.controls = ttk.Frame(self, style="Panel.TFrame", padding=8)
        self.controls.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        for col in range(3):
            self.controls.columnconfigure(col, weight=1, uniform="wave_fields")
        tabs = ttk.Frame(self.controls, style="Panel.TFrame")
        tabs.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        self.source_buttons = {}
        for i, (source, text) in enumerate((("sinc", "SINC / SRAM"), ("rom", "ROM SIN"), ("ram", "RAM"))):
            tabs.columnconfigure(i, weight=1, uniform="wave_tabs")
            button = ttk.Button(tabs, text=text, command=lambda key=source: self.select_source(key))
            button.grid(row=0, column=i, sticky="ew", padx=(0, 4))
            self.source_buttons[source] = button
        self.fields = ttk.Frame(self.controls, style="Panel.TFrame")
        self.fields.grid(row=1, column=0, columnspan=3, sticky="ew")
        for col in range(3):
            self.fields.columnconfigure(col, weight=1, uniform="wave_cells")
        self.cells = {}
        specs = (
            ("waveform_type", self.waveform_type_var, ["sine", "square", "triangle", "dc", "sinc", "gauss", "imported", "serial"]),
            ("slice_index", self.slice_var, [str(i) for i in range(30)]),
            ("points", self.points_var, None), ("start_phase", self.start_phase_var, None),
            ("end_phase", self.end_phase_var, None), ("offset", self.offset_var, None),
            ("voltage_amplitude", self.voltage_amplitude_var, None),
            ("sinc_positive_voltage", self.sinc_positive_voltage_var, None),
            ("sinc_negative_voltage", self.sinc_negative_voltage_var, None),
            ("sinc_points1", self.sinc_points1_var, None), ("sinc_points2", self.sinc_points2_var, None),
            ("sinc_points3", self.sinc_points3_var, None),
            ("start_addr", self.start_addr_var, None), ("end_addr", self.end_addr_var, None),
            ("write_points_per_batch", self.write_points_per_batch_var, [str(i) for i in WAVEFORM_WRITE_POINT_OPTIONS]),
        )
        for key, variable, options in specs:
            cell = ttk.Frame(self.fields, style="Panel.TFrame")
            cell.columnconfigure(0, weight=1)
            label = ttk.Label(cell, style="Panel.TLabel", wraplength=165)
            label.grid(row=0, column=0, sticky="w")
            self._labels[key] = label
            widget = (ttk.Entry(cell, textvariable=variable, width=12) if options is None else
                      ttk.Combobox(cell, textvariable=variable, values=options, state="readonly", width=12))
            widget.grid(row=1, column=0, sticky="ew", pady=(3, 5))
            if key == "end_addr":
                widget.configure(state="readonly")
            self._field_widgets[key] = [label, widget]
            self.cells[key] = cell
        fill = ttk.Checkbutton(self.controls, variable=self.fill_zero_tail_var)
        fill.grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        self.bind_text(fill, "fill_zero_tail")
        self._field_widgets["fill_zero_tail"] = [fill]
        self.serial_editor = self._build_serial_editor(self.controls)
        self.serial_editor.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.serial_editor.grid_remove()
        self.controls.rowconfigure(4, weight=1)
        self.source_hint = ttk.Label(self.controls, style="Muted.TLabel", wraplength=525)
        self.source_hint.grid(row=4, column=0, columnspan=3, sticky="nw", pady=4)
        actions = ttk.Frame(self.controls, style="Panel.TFrame")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew")
        for col in range(3):
            actions.columnconfigure(col, weight=1, uniform="wave_actions")
        for i, (key, command) in enumerate((("write_waveform", self.write_waveform),
                ("import_waveform", self.import_waveform), ("export_waveform", self.export_waveform),
                ("write_imported_waveform", self.write_imported_waveform),
                ("verify_written_waveform", self.verify_written_waveform))):
            btn = ttk.Button(actions, command=command, style="Primary.TButton" if i == 0 else "Secondary.TButton")
            btn.grid(row=i//3, column=i%3, sticky="ew", padx=(0,4), pady=3)
            self._buttons[key] = btn
        self.verify_waveform_button = self._buttons["verify_written_waveform"]
        plot = ttk.Frame(self, style="Panel.TFrame", padding=8)
        plot.grid(row=0, column=1, sticky="nsew")
        plot.columnconfigure(0, weight=1)
        plot.rowconfigure(1, weight=1)
        self.plot_toolbar = ttk.Frame(plot, style="Panel.TFrame")
        self.plot_toolbar.grid(row=0, column=0, sticky="ew")
        for i, (key, command) in enumerate((("show_current_waveform", self.show_current_waveform),
                                          ("show_chip_waveform", self.show_chip_waveform))):
            self.plot_toolbar.columnconfigure(i, weight=1)
            btn = ttk.Button(self.plot_toolbar, command=command, style="Secondary.TButton")
            btn.grid(row=0, column=i, sticky="ew", padx=3, pady=(0,6))
            self._buttons[key] = btn
        self.canvas = tk.Canvas(plot, height=420, width=380, highlightthickness=1)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.read_slices_text = tk.Text(plot, height=2, width=8, wrap="none",
                                        bg=self.colors["panel"], fg=self.colors["text"],
                                        insertbackground=self.colors["text"])
        self.read_slices_text.grid(row=2, column=0, sticky="ew", pady=(5,0))
        self.read_slices_scrollbar = ttk.Scrollbar(plot, command=self.read_slices_text.yview)
        self.read_slices_scrollbar.grid(row=2, column=1, sticky="ns")
        self.read_slices_text.configure(yscrollcommand=self.read_slices_scrollbar.set, state="disabled")
        self.read_slices_xscrollbar = None
        for _, variable, _ in specs:
            variable.trace_add("write", lambda *_: self._on_param_changed())
        self.fill_zero_tail_var.trace_add("write", lambda *_: self._on_param_changed())
        for variable in [*self.serial_slice_vars, *self.serial_repeat_vars]:
            variable.trace_add("write", lambda *_: self._on_serial_param_changed())
        self.canvas.bind("<Configure>", lambda _: self._redraw_active_plot())
        self.refresh_language()
        self._update_waveform_specific_fields()

    def select_source(self, source):
        self._plot_mode = "current"
        self.source = source
        if source == "rom":
            self.owner.play_waveform_var.set("0x1F")
            self.waveform_type_var.set("rom")
        else:
            if self.owner.play_waveform_var.get() == "0x1F":
                self.owner.play_waveform_var.set(f"slice{self.slice_var.get()}")
            self._preview_imported_waveform = False
            self.waveform_type_var.set("sinc" if source == "sinc" else "sine")
        self._update_waveform_specific_fields()
        self._preview_waveform()
        self._notify_metrics_changed()

    def _update_waveform_specific_fields(self):
        super()._update_waveform_specific_fields()
        self.source = {"sinc": "sinc", "rom": "rom"}.get(self.waveform_type_var.get(), "ram")
        for cell in self.cells.values():
            cell.grid_remove()
        visible = [key for key in self.cells if any(w.winfo_manager() for w in self._field_widgets[key])]
        if self.source == "rom":
            visible = []
        for i, key in enumerate(visible):
            self.cells[key].grid(row=i//3, column=i%3, sticky="ew", padx=(0,8))
        for source, button in self.source_buttons.items():
            button.configure(style="Selected.TButton" if source == self.source else "Secondary.TButton")
        rom = self.source == "rom"
        for widget in self._buttons.values():
            widget.state(["disabled"] if rom else ["!disabled"])
        if rom:
            self.serial_editor.grid_remove()
            self._field_widgets["fill_zero_tail"][0].grid_remove()
        self.source_hint.configure(text=(
            "ROM SIN：手动播放选用 0x1F；1024 点，使用上方电压和单点时长。" if rom else
            "SINC 由软件生成后写入 SRAM；每段时长 = 该段点数 × 单点时长。" if self.source == "sinc" else ""
        ) if self.language == "zh" else (
            "ROM SIN: manual playback selects 0x1F; 1024 samples, using voltage and point time above." if rom else
            "SINC is generated into SRAM. Segment duration = sample count × point time." if self.source == "sinc" else ""))

    def _refresh_write_button_text(self):
        button = self._buttons.get("write_waveform")
        if button is not None:
            key = "write_serial_config" if self.waveform_type_var.get() == "serial" else "awp192x_write_waveform"
            button.configure(text=self.tr(key))

    def refresh_language(self):
        super().refresh_language()
        for i in range(1, 4):
            self._labels[f"sinc_points{i}"].configure(text=self.tr(f"awp192x_sinc_points{i}"))
        if hasattr(self, "cells"):
            self._update_waveform_specific_fields()

    def effective_points(self):
        return 1024 if self.source == "rom" else super().effective_points()

    def _redraw_active_plot(self):
        if not hasattr(self, "canvas"):
            return
        if self.waveform_type_var.get() == "serial":
            self._preview_serial_waveform()
            return
        if self._plot_mode == "chip" and self._chip_voltages:
            self._draw_plot(self._chip_voltages)
        else:
            self._preview_waveform()

    def _preview_waveform(self):
        if not hasattr(self, "canvas"):
            return
        if self.source == "rom":
            peak = next(v for label, v, _ in self.owner.amp_options if label == self.owner.max_voltage_var.get())
            data = [peak * math.sin(2*math.pi*i/1024) for i in range(1024)]
            segments = [("ROM SIN", data)]
        elif self.waveform_type_var.get() == "serial":
            self._preview_serial_waveform()
            return
        else:
            try:
                values, volts, _, _, _, waveform = self._generate_current(use_imported=self._preview_imported_waveform)
                self._last_values, self._last_voltages = values, volts
                segments = [(waveform.upper(), volts)]
                if waveform == "sinc":
                    lengths = [int(v.get()) for v in (self.sinc_points1_var, self.sinc_points2_var, self.sinc_points3_var)]
                    names = [self.tr("awp1221_segment_" + key) for key in ("rise", "fall", "return")]
                    ends = [0, lengths[0], lengths[0]+lengths[1], sum(lengths)]
                    segments = [(name, volts[ends[i]:ends[i+1]]) for i,name in enumerate(names)]
                    if len(volts) > ends[-1]:
                        segments.append(("0 V", volts[ends[-1]:]))
            except (ValueError, TypeError):
                self.canvas.delete("all")
                return
        try:
            point_us = int(self.owner.duration_var.get())
            if not 1 <= point_us <= 65535:
                raise ValueError
        except ValueError:
            self.canvas.delete("all")
            return
        draw_timed_waveform(self.canvas, self.colors, self.tr, segments, point_us)

    def _draw_serial_plot(self, segments):
        try:
            point_us = int(self.owner.duration_var.get())
            if not 1 <= point_us <= 65535:
                raise ValueError
        except ValueError:
            self.canvas.delete("all")
            return
        draw_timed_waveform(self.canvas, self.colors, self.tr, segments, point_us)


class ButtonModeView:
    def _build_button_body(self):
        body = ttk.Frame(self, style="App.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        waveform = ttk.LabelFrame(body, text=self.tr("awp1221_button_wave_settings"), padding=8)
        waveform.grid(row=0, column=0, sticky="ew")
        self.bind_text(waveform, "awp1221_button_wave_settings")
        waveform.columnconfigure(0, weight=1)
        global_fields = ttk.Frame(waveform, style="Panel.TFrame")
        global_fields.grid(row=0, column=0, sticky="ew")
        self._add_combo(global_fields, 0, "max_output_voltage", self.max_voltage_var, [x[0] for x in self.amp_options])
        self._add_entry(global_fields, 1, "single_point_duration", self.duration_var, "us")
        ttk.Label(global_fields, textvariable=self.period_hint_var, style="Muted.TLabel").grid(row=0, column=4, rowspan=2, padx=20, sticky="w")
        self.waveform_page = ButtonWaveformPage(waveform, self.ctx, storage=self.storage, owner=self)
        self.waveform_page.grid(row=1, column=0, sticky="nsew")
        self.waveform_page.metrics_revision_var.trace_add("write", lambda *_: self._on_button_runtime_waveform_changed())
        for variable in (self.duration_var, self.max_voltage_var):
            variable.trace_add("write", lambda *_: self.waveform_page._redraw_active_plot())
        middle = ttk.Frame(body, style="App.TFrame")
        middle.grid(row=1, column=0, sticky="ew", pady=10)
        middle.columnconfigure(0, weight=2, uniform="adc_sections")
        middle.columnconfigure(1, weight=3, uniform="adc_sections")
        diagram = ttk.LabelFrame(middle, padding=8)
        diagram.grid(row=0, column=0, sticky="nsew", padx=(0,10))
        self.bind_text(diagram, "awp1221_adc_diagram")
        diagram.columnconfigure(0, weight=1)
        diagram.rowconfigure(1, weight=1)
        selector = ttk.Frame(diagram, style="Panel.TFrame")
        selector.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self._button_adc_diagram_phase = "press"
        self._button_adc_phase_buttons = {}
        for column, phase in enumerate(("press", "release")):
            selector.columnconfigure(column, weight=1, uniform="adc_diagram_phases")
            button = ttk.Button(selector, command=lambda value=phase: self._show_button_adc_phase(value))
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0))
            self.bind_text(button, f"awp1221_diagram_detect_{phase}")
            self._button_adc_phase_buttons[phase] = button
        self.adc_diagram = tk.Canvas(diagram, height=200, width=280, highlightthickness=0)
        self.adc_diagram.grid(row=1, column=0, sticky="nsew")
        self.adc_diagram.bind("<Configure>", lambda _: self._draw_button_adc_diagram())
        params = ttk.LabelFrame(middle, padding=8)
        params.grid(row=0, column=1, sticky="nsew")
        self.bind_text(params, "awp1221_button_adc_settings")
        for col in range(4):
            params.columnconfigure(col, weight=1)
        self._button_field(params, 0, 0, "press_threshold", self.press_threshold_var, value=self.press_threshold_hex_var)
        self._button_field(params, 0, 1, "release_threshold", self.release_threshold_var, value=self.release_threshold_hex_var)
        self._button_field(params, 0, 2, "adc_average_time", self.adc_average_time_var, [x[0] for x in self.ADC_AVERAGE_OPTIONS])
        self._button_field(params, 0, 3, "adc_ovv_time", self.adc_ovv_time_var, [x[0] for x in self.ADC_OVV_OPTIONS])
        self._button_field(params, 1, 0, "adc_pol", self.button_adc_pol_var, ["OUT+", "OUT-"])
        self._button_field(params, 1, 1, "adc_gain", self.button_adc_gain_var, ["0-19V", "0-100V"])
        for i, (key, variable, wave) in enumerate((("press_repeat_count", self.press_repeat_count_var, self.press_waveform_var),
                ("release_repeat_count", self.release_repeat_count_var, self.release_waveform_var),
                ("play_repeat_count", self.play_repeat_count_var, self.play_waveform_var))):
            cell = ttk.Frame(params, style="Panel.TFrame")
            cell.grid(row=2, column=i, sticky="nw", padx=(0,8))
            label = ttk.Label(cell, style="Panel.TLabel", wraplength=165)
            label.grid(row=0, column=0, columnspan=2, sticky="w")
            self._labels[key] = label
            entry = ttk.Entry(cell, textvariable=variable, width=4)
            entry.grid(row=1, column=0, sticky="w", pady=3)
            self._repeat_entries[key] = entry
            combo = ttk.Combobox(cell, textvariable=wave, values=self._slice_options(), state="readonly", width=8)
            combo.grid(row=1, column=1, padx=(5,0), pady=3)
            self._bind_repeat_playback_tooltip([label, entry, combo], variable, wave)
        endless = ttk.Checkbutton(params, variable=self.endless_play_var)
        endless.grid(row=3, column=0, sticky="w")
        self._checks["endless_play"] = endless
        self.endless_hint_label = ttk.Label(params, style="Muted.TLabel", wraplength=430)
        self.endless_hint_label.grid(row=3, column=1, columnspan=3, sticky="w")
        for variable in (self.press_threshold_var, self.release_threshold_var):
            variable.trace_add("write", lambda *_: self._draw_button_adc_diagram())
        lower = ttk.Frame(body, style="App.TFrame")
        lower.grid(row=2, column=0, sticky="ew")
        lower.columnconfigure(1, weight=3)
        lower.columnconfigure(2, weight=2)
        status = ttk.LabelFrame(lower, padding=8)
        status.grid(row=0, column=0, sticky="nsew", padx=(0,10))
        self.bind_text(status, "sense_status_title")
        self._sense_status_block = tk.Frame(status, width=72, height=28, bg=self.STATUS_IDLE_COLOR)
        self._sense_status_block.grid(row=0, column=0, pady=10)
        status_hint = ttk.Label(status, style="Muted.TLabel", wraplength=110)
        status_hint.grid(row=1, column=0)
        self.bind_text(status_hint, "sense_status_hint")
        readings = ttk.LabelFrame(lower, padding=8)
        readings.grid(row=0, column=1, sticky="nsew", padx=(0,10))
        self.bind_text(readings, "awp1221_adc_readings")
        readings.columnconfigure(0, weight=1)
        top = ttk.Frame(readings, style="Panel.TFrame")
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        title = ttk.Label(top, style="Panel.TLabel")
        title.grid(row=0, column=0, sticky="w")
        self._labels["button_adc_value"] = title
        graph = ttk.Button(top, style="Secondary.TButton", command=self._open_button_adc_graph)
        graph.grid(row=0, column=1)
        self._buttons["awp1221_adc_graph"] = graph
        ttk.Entry(readings, textvariable=self.adc_value_var, state="readonly").grid(row=1, column=0, sticky="ew", pady=5)
        bits = ttk.Frame(readings, style="Panel.TFrame")
        bits.grid(row=2, column=0, sticky="w")
        self._adc_nibbles = []
        self._last_button_adc_word = None
        for i in range(3):
            label = tk.Label(bits, width=5, text=f"—\n{11-i*4}-{8-i*4}")
            label.grid(row=0, column=i, padx=(0,2))
            self._adc_nibbles.append(label)
        row = ttk.Frame(readings, style="Panel.TFrame")
        row.grid(row=3, column=0, sticky="ew", pady=(6,0))
        for i, (key, command) in enumerate((("adc_read_current_value", self.read_button_adc_value),
                ("adc_read_realtime", self.toggle_button_adc_realtime), ("adc_read_on_trigger", self.toggle_button_adc_trigger_read))):
            row.columnconfigure(i, weight=1, uniform="adc_actions")
            button = ttk.Button(row, command=command, style="Secondary.TButton")
            button.grid(row=0, column=i, sticky="ew", padx=(0,4))
            self._buttons[key] = button
        switches = ttk.LabelFrame(lower, padding=8)
        switches.grid(row=0, column=2, sticky="nsew")
        self.bind_text(switches, "awp1221_control_buttons")
        for col in range(2):
            switches.columnconfigure(col, weight=1)
        for i, (key, command) in enumerate((("start_detection", self.start_detection), ("stop_detection", self.stop_detection),
                ("start_play", self.start_play), ("stop_play", self.stop_play))):
            button = ttk.Button(switches, command=command, style="Secondary.TButton")
            button.grid(row=i//2, column=i%2, sticky="ew", padx=3, pady=3)
            self._buttons[key] = button
        close = ttk.Button(switches, command=self._stop_all_button_actions, style="Danger.TButton")
        close.grid(row=2, column=0, columnspan=2, sticky="ew", padx=3, pady=3)
        self.bind_text(close, "awp192x_close_all")
        details = ttk.Frame(body, style="Panel.TFrame", padding=8)
        details.grid(row=3, column=0, sticky="ew", pady=(10,0))
        details.columnconfigure(0, weight=1)
        toggle = ttk.Button(details, command=self._toggle_button_log, style="Secondary.TButton")
        toggle.grid(row=0, column=0, sticky="w")
        self.bind_text(toggle, "awp1221_communication_log")
        self._button_log = self.make_serial_log(details, height=4, subscribe=True)
        self._button_log.grid(row=1, column=0, sticky="ew", pady=(6,0))
        self._button_log.grid_remove()
        self._button_graph_window = None
        self._button_graph_gpio_level = None
        self._refresh_button_view()

    def _button_field(self, parent, row, col, key, variable, options=None, value=None):
        cell = ttk.Frame(parent, style="Panel.TFrame")
        cell.grid(row=row, column=col, sticky="nw", padx=(0,10), pady=(0,8))
        label = ttk.Label(cell, style="Panel.TLabel", wraplength=130)
        label.grid(row=0, column=0, sticky="w")
        self._labels[key] = label
        widget = (ttk.Entry(cell, textvariable=variable, width=11) if options is None else
                  ttk.Combobox(cell, textvariable=variable, values=options, state="readonly", width=11))
        widget.grid(row=1, column=0, sticky="w", pady=3)
        if value is not None:
            ttk.Label(cell, textvariable=value, style="Muted.TLabel").grid(row=2, column=0, sticky="w")

    def _show_button_adc_phase(self, phase):
        # Select the explanatory view without reconfiguring chip detection.
        self._button_adc_diagram_phase = phase
        self._draw_button_adc_diagram()

    def _draw_button_adc_diagram(self):
        canvas = getattr(self, "adc_diagram", None)
        if canvas is None:
            return
        colors = self.colors
        background, primary, muted = colors["canvas_bg"], colors["primary"], colors["muted"]
        accent = "#E8833A"
        selected = self._button_adc_diagram_phase
        for phase, button in self._button_adc_phase_buttons.items():
            button.configure(style="Selected.TButton" if phase == selected else "Secondary.TButton")
        canvas.configure(bg=background)
        canvas.delete("all")
        width, height = max(260, canvas.winfo_width()), max(200, canvas.winfo_height())
        left, right, band_top, top, bottom = 16, width-16, 24, 65, height-26
        span_x = right-left
        values = {}
        for phase, variable in (("press", self.press_threshold_var), ("release", self.release_threshold_var)):
            try:
                value = float(variable.get())
                if math.isfinite(value):
                    values[phase] = value
            except ValueError:
                pass
        low, high = min([0, *values.values()]), max([0, *values.values()])
        span = max(high-low, 1)
        base, peak = low-span*.08, high+span*.35
        y_of = lambda value: bottom-(value-base)/(peak-base)*(bottom-top)
        rise0, rise1 = left+span_x*.05, left+span_x*.32
        fall0, fall1 = left+span_x*.60, left+span_x*.92
        press_x = (rise0+(values["press"]-base)/(peak-base)*(rise1-rise0)
                   if "press" in values else rise1)
        release_x = (fall0+(peak-values["release"])/(peak-base)*(fall1-fall0)
                     if "release" in values else fall1)

        def tint(color, ratio):
            fg, bg = canvas.winfo_rgb(color), canvas.winfo_rgb(background)
            return "#{:02X}{:02X}{:02X}".format(*(int((f*ratio+b*(1-ratio))/257) for f,b in zip(fg,bg)))

        for x0, x1, phase, color in ((left,press_x,"press",primary),
                                    (press_x,release_x,"release",accent),
                                    (release_x,right,"press",primary)):
            canvas.create_rectangle(x0,band_top,x1,bottom,outline="",
                fill=tint(color,.20 if phase == selected else .06),
                tags=("adc_phase_band",f"adc_band_{phase}"))
            if x1-x0 >= 80:
                canvas.create_text((x0+x1)/2,band_top+5,anchor="n",
                    text=self.tr(f"awp1221_diagram_detect_{phase}"),width=x1-x0-8,
                    font=("Microsoft YaHei UI",9),fill=color if selected == phase else muted,
                    tags=("adc_phase_caption",f"adc_caption_{phase}"))
        canvas.create_line(left,y_of(base),rise0,y_of(base),rise1,y_of(peak),fall0,y_of(peak),
            fall1,y_of(base),right,y_of(base),fill=primary,width=2,tags="adc_diagram_curve")
        canvas.create_line(left,bottom,right,bottom,fill=colors["border"])
        labels = []
        for phase, x, color, target in (("press",press_x,primary,"release"),
                                         ("release",release_x,accent,"press")):
            if phase not in values:
                continue
            value, active = values[phase], phase == selected
            y = y_of(value)
            # Equal thresholds retain distinct colors on their respective halves.
            x0,x1 = (left,(left+right)/2) if phase == "press" else ((left+right)/2,right)
            canvas.create_line(x0,y,x1,y,fill=color,dash=(4,3),width=2 if active else 1,
                               tags=("adc_threshold_line",f"adc_threshold_{phase}"))
            canvas.create_line(x,band_top,x,bottom,fill=color,dash=(2,3))
            radius = 5 if active else 3
            canvas.create_oval(x-radius,y-radius,x+radius,y+radius,fill=color,outline=background,
                               tags=("adc_threshold_marker",f"adc_marker_{phase}"))
            label = canvas.create_text(left+3 if phase == "press" else right-3,y-4,
                anchor="sw" if phase == "press" else "se",fill=color,
                text=f"{self.tr('awp1221_diagram_'+phase)} {value:g} V",tags="adc_threshold_label")
            labels.append(label)
            canvas.create_text(left+3 if phase == "press" else right-3,top-5,
                anchor="sw" if phase == "press" else "se",fill=color,font=("Microsoft YaHei UI",9),
                text="→ "+self.tr(f"awp1221_diagram_detect_{target}"),tags="adc_phase_transition")
        if len(labels) == 2:
            first, second = (canvas.bbox(item) for item in labels)
            if first and second and first[0]<second[2] and second[0]<first[2] and first[1]<second[3] and second[1]<first[3]:
                canvas.move(labels[1],0,-(second[3]-first[1]+4))
        for item in canvas.find_withtag("adc_threshold_label")+canvas.find_withtag("adc_phase_transition"):
            box = canvas.bbox(item)
            if box:
                canvas.move(item,max(3-box[0],min(0,width-3-box[2])),max(3-box[1],min(0,height-3-box[3])))
        canvas.create_text(left,6,text=self.tr("awp1221_adc_diagram_hint"),anchor="nw",fill=muted)
        canvas.create_text(left,bottom+5,text=self.tr("awp1221_time_axis"),anchor="nw",fill=muted)
        self._push_button_graph_thresholds()

    def _set_button_adc_display(self, value):
        code = value & 0xFFF
        gain = "0-19V" if value & (1<<13) else "0-100V"
        pol = "OUT-" if value & (1<<15) else "OUT+"
        voltage = self._code_to_voltage(code, gain, pol)
        if abs(voltage) < .0005:
            voltage = 0.0
        self.adc_value_var.set(f"0x{code:03X} / {code} / {voltage:.3f} V")
        self._last_button_adc_word = value
        self._refresh_button_nibbles()
        return voltage

    def _refresh_button_nibbles(self):
        value = getattr(self, "_last_button_adc_word", None)
        for i, label in enumerate(getattr(self, "_adc_nibbles", [])):
            label.configure(text=f"{format(value & 0xFFF, '03X')[i] if value is not None else '—'}\n{11-i*4}-{8-i*4}",
                            bg=self.colors["primary"], fg="#FFFFFF")

    def _button_graph_active(self):
        window = getattr(self, "_button_graph_window", None)
        return window is not None and bool(window.winfo_exists())

    def _open_button_adc_graph(self):
        if self._button_graph_active():
            self._button_graph_window.deiconify()
            self._button_graph_window.lift()
            return
        if not self._ensure_connected():
            return
        self._button_adc_generation += 1
        window = AWP192xADCVoltageWindow(self, self._close_button_adc_graph)
        window.transient(self.winfo_toplevel())
        fit_toplevel_to_screen(window, preferred_width=900, preferred_height=560,
                               minimum_width=620, minimum_height=380)
        self._button_graph_window = window
        self._button_graph_canvas = window.canvas
        self._button_graph_gpio_level = None
        self._draw_button_adc_graph()
        self._refresh_adc_read_buttons()
        if self.ctx.serial.is_connected:
            if self._adc_realtime_job is not None:
                self.after_cancel(self._adc_realtime_job)
                self._adc_realtime_job = None
            self._poll_button_adc_realtime()

    def _close_button_adc_graph(self):
        self._button_adc_generation += 1
        window = getattr(self, "_button_graph_window", None)
        self._button_graph_window = None
        self._button_graph_gpio_level = None
        if window is not None:
            window.destroy()
        if not self._adc_realtime_active:
            self._stop_button_adc_realtime()
        self._refresh_adc_read_buttons()

    def _draw_button_adc_graph(self):
        if not self._button_graph_active():
            return
        self._button_graph_window.refresh_theme(self.colors)
        self._push_button_graph_thresholds()

    def _push_button_graph_thresholds(self):
        if not self._button_graph_active():
            return
        values = []
        for variable in (self.press_threshold_var, self.release_threshold_var):
            try:
                value = float(variable.get())
                values.append(value if math.isfinite(value) else None)
            except ValueError:
                values.append(None)
        self._button_graph_window.set_threshold_voltages(*values)

    def _record_button_adc_sample(self, value):
        if self._button_graph_active():
            self._button_graph_window.add_sample(value)

    def _note_button_graph_level(self, level):
        if level is None or not self._button_graph_active():
            return
        previous = self._button_graph_gpio_level
        self._button_graph_gpio_level = level
        if previous != 0 and level == 0:
            try:
                duration = self._detect_playback_wait_ms()/1000.0
            except (ValueError, AttributeError):
                duration = .001
            self._button_graph_window.add_vibration_band(max(.001, duration))

    def _button_display_poll_ms(self):
        return self._button_graph_window.poll_interval_ms() if self._button_graph_active() else 300

    def _stop_all_button_actions(self):
        self.stop_detection()
        self.stop_play()

    def _toggle_button_log(self):
        panel = self._button_log
        if panel.winfo_manager():
            panel.grid_remove()
        else:
            panel.grid()
        self.after_idle(self._sync_button_log)

    def _sync_button_log(self):
        owner = self.master
        while owner is not None and not hasattr(owner, "mode_scroll"):
            owner = getattr(owner, "master", None)
        if owner is not None:
            owner.mode_scroll._sync_scroll_region()
            if self._button_log.winfo_manager():
                self.after_idle(lambda: owner.mode_scroll.canvas.yview_moveto(1))

    def _refresh_button_view(self):
        if self.page_name != "button_mode":
            return
        self._draw_button_adc_diagram()
        self._refresh_button_nibbles()
        self._draw_button_adc_graph()

    def _poll_button_display(self):
        self._adc_realtime_job = None
        if not (self._adc_realtime_active or self._button_graph_active()):
            return
        if not self.ctx.serial.is_connected:
            if self._button_graph_active():
                self._close_button_adc_graph()
            self._stop_button_adc_realtime()
            return
        if self._button_adc_pending:
            self._adc_realtime_job = self.after(self._button_display_poll_ms(), self._poll_button_adc_realtime)
            return
        self._button_adc_pending = True
        generation = self._button_adc_generation
        address = self.ctx.state.awp192x_device_addr
        numeric = self._adc_realtime_active
        graph = self._button_graph_active()
        serial = self.ctx.serial

        def worker():
            value, level, error = None, None, None
            try:
                with serial.suppress_logging():
                    if graph:
                        try:
                            response = serial.read_gpio("D2")
                            if response.ok:
                                level = parse_gpio_read_response(response.data).level
                        except Exception:
                            pass
                    response = serial.read_register_value(address, 0x07, bits=16)
                    if response.ok:
                        value = serial.extract_rdataw_value(response.data, device_addr=address, reg_addr=0x07, bits=16)
                    else:
                        error = response.error
            except Exception as exc:
                error = str(exc)
            post_to_ui(self, self._finish_button_display, generation, address, numeric, value, level, error)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_button_display(self, generation, address, numeric, value, level=None, error=None):
        self._button_adc_pending = False
        if not self.winfo_exists():
            return
        if (generation == self._button_adc_generation and address == self.ctx.state.awp192x_device_addr
                and self.ctx.serial.is_connected):
            if value is not None:
                if numeric and self._adc_realtime_active:
                    self._set_button_adc_display(value)
                self._record_button_adc_sample(value)
                self._note_button_graph_level(level)
                if self._button_graph_active():
                    marker = "D2=?" if level is None else "D2=0 / nINT" if level == 0 else "D2=1"
                    self._button_graph_window.status.set(f"{marker} · ADC 0x07: 0x{value & 0xFFF:03X}")
            elif error and self._button_graph_active():
                self._button_graph_window.status.set(f"读取失败: {error}")
        if (self._adc_realtime_active or self._button_graph_active()) and self._adc_realtime_job is None:
            self._adc_realtime_job = self.after(self._button_display_poll_ms(), self._poll_button_adc_realtime)
