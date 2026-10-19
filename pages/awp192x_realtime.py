"""AWP192x data conversion on the same live plotting window used by AWP1221."""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from pages.awp1221_realtime_sram import ADCVoltageWindow


class AWP192xADCVoltageWindow(ADCVoltageWindow):
    WINDOW_TITLE = "AWP1921/AWP1923 实时电压"
    THRESHOLD_LABELS = ("按压阈值", "松手阈值")
    INVALID_PARAMETERS_MESSAGE = "请输入有效的时间范围"

    def __init__(self, parent, on_close):
        self._code_to_voltage = parent._code_to_voltage
        super().__init__(parent, on_close)
        self._control_cells = [(child, int(child.grid_info()["column"]), int(child.grid_info()["row"]))
                               for child in self._controls.winfo_children()]
        self._control_columns = None
        self._controls.bind("<Configure>", self._fit_controls)

    def _fit_controls(self, event):
        cell_width = max(child.winfo_reqwidth() for child,_,_ in self._control_cells) + 12
        columns = max(1, min(5, (event.width-24) // cell_width))
        if columns == self._control_columns:
            return
        self._control_columns = columns
        for column in range(5):
            self._controls.columnconfigure(column, weight=1 if column < columns else 0)
        for child, column, row in self._control_cells:
            child.grid_configure(column=column % columns, row=2*(column // columns)+row)

    def _build_conversion_controls(self, controls):
        self._controls = controls
        self.gain = tk.StringVar(value="—")
        self.polarity = tk.StringVar(value="—")
        self.adc_code = tk.StringVar(value="—")
        for column, (label, variable) in enumerate((("ADC Gain（回读）", self.gain),
                ("ADC POL（回读）", self.polarity), ("12-bit ADC", self.adc_code))):
            ttk.Label(controls, text=label).grid(row=0, column=column, sticky="w")
            ttk.Entry(controls, textvariable=variable, state="readonly", width=12).grid(
                row=1, column=column, sticky="ew", padx=(0,12))

    def _conversion_params(self):
        # Each word carries its own gain/polarity flags; historical samples
        # must retain the conversion that belonged to that word.
        return ()

    def _sample_to_mv(self, raw, params):
        word = int(raw) & 0xFFFF
        voltage = self._code_to_voltage(word & 0xFFF,
            "0-19V" if word & (1 << 13) else "0-100V",
            "OUT-" if word & (1 << 15) else "OUT+") * 1000.0
        return 0.0 if abs(voltage) < .0005 else voltage

    def _threshold_to_mv(self, voltage, params):
        return float(voltage) * 1000.0

    def set_threshold_voltages(self, press, release):
        self.set_threshold_counts(press, release)

    def add_sample(self, raw):
        word = int(raw) & 0xFFFF
        self.points.append((time.monotonic(), word))
        self.gain.set("0-19V" if word & (1 << 13) else "0-100V")
        self.polarity.set("OUT-" if word & (1 << 15) else "OUT+")
        self.adc_code.set(f"0x{word & 0xFFF:03X}")
        self.status.set(f"ADC 0x07: 0x{word & 0xFFF:03X} ({word & 0xFFF})")
        self.redraw()
