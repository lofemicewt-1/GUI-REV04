from __future__ import annotations

import tkinter as tk
import threading
from tkinter import messagebox, ttk
from typing import Any

from pages.base_page import BasePage


def awp1922_amplitude_code(label: str) -> int | None:
    labels = [f"{(i + 1) * 2.8:.2f} V" for i in range(0x20)]
    if label not in labels:
        return None
    return labels.index(label) & 0x1F


def awp1922_frequency_code(label: str, options: list[int]) -> int | None:
    text = label.replace("Hz", "").strip()
    try:
        freq = int(text)
    except ValueError:
        return None
    if freq not in options:
        return None
    return options.index(freq) & 0xFF


def awp1922_play_value(current: int) -> int:
    return (int(current) | 0x40) & 0xFF


class AWP1922Page(BasePage):
    """
    AWP1922 \u72ec\u7acb\u82af\u7247\u9875\u9762\u3002

    \u8981\u70b9\uff1a
    - \u5730\u5740\uff1a0x58-0x5F
    - \u68c0\u6d4b\uff1a\u8bfb 0x00 == 0x80
    - 8-bit \u5bc4\u5b58\u5668
    - \u5e45\u5ea6\uff1a0x06\uff0c00-1F\uff0c\u5355\u6b65 2.8V
    - \u9891\u7387\uff1a0x05\uff0c\u9009\u9879 25-375Hz
    - Start/Stop Play\uff1a0x00 bit6\uff0c1=start\uff0c0=stop
    - Shutdown/Wake\uff1a0x00 bit7\uff0c0=shutdown\uff0c1=wake
    - \u5bc4\u5b58\u5668\u8bfb\u5199\uff1a\u4f7f\u7528\u663e\u5f0f\u5730\u5740 RREGW/WREGW \u548c\u539f\u59cb 8-bit \u503c
    """

    page_name = "awp1922"

    FREQ_OPTIONS = [25, 50, 75, 100, 125, 150, 175, 200, 220, 240, 270, 290, 310, 330, 350, 375]
    _HINT_MEASURE_WRAP = 420

    def __init__(self, parent: tk.Misc, ctx: Any) -> None:
        super().__init__(parent, ctx)

        self.addr_var = tk.StringVar(value="0x58")
        self.status_var = tk.StringVar(value="")
        self.amp_var = tk.StringVar(value="2.80 V")
        self.freq_var = tk.StringVar(value="25 Hz")

        self.reg_addr_var = tk.StringVar(value="0x00")
        self.write_value_var = tk.StringVar(value="0x80")
        self.read_value_var = tk.StringVar(value="")

        self.current_ctrl_reg = 0x80

        self._labels: dict[str, ttk.Label] = {}
        self._buttons: dict[str, ttk.Button] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        self.enable_pinned_footer_layout()

        header = self.make_title(self, "awp1922")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        footer, actions, reg_panel, identity = self.make_merged_footer()
        footer.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        actions.grid_remove()

        serial_group = identity.add_group()
        self.make_serial_selector(serial_group, page_name=self.page_name, row=0, column=0)
        addr_group = identity.add_group()
        addr_label = ttk.Label(addr_group, style="Panel.TLabel")
        addr_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._labels["awp1922_addr"] = addr_label
        ttk.Combobox(
            addr_group,
            textvariable=self.addr_var,
            values=[f"0x{x:02X}" for x in range(0x58, 0x60)],
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky="w")

        detect_group = identity.add_group()
        detect_btn = ttk.Button(detect_group, style="Primary.TButton", command=self.detect_chip)
        detect_btn.grid(row=0, column=0, sticky="w")
        self._buttons["detect_chip"] = detect_btn

        status_group = identity.add_group()
        ttk.Label(status_group, textvariable=self.status_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")

        title_group = reg_panel.add_group()
        reg_title = ttk.Label(title_group, style="Panel.TLabel")
        reg_title.grid(row=0, column=0, sticky="w")
        self._labels["register_tools"] = reg_title
        self._add_footer_field(reg_panel, "reg_addr", self.reg_addr_var, 8)
        self._add_footer_field(reg_panel, "write_value", self.write_value_var, 10)
        self._add_footer_field(reg_panel, "read_value", self.read_value_var, 12, readonly=True)

        button_group = reg_panel.add_group()
        rr_btn = ttk.Button(button_group, style="Secondary.TButton", command=self.read_register)
        rr_btn.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._buttons["rr"] = rr_btn
        rw_btn = ttk.Button(button_group, style="Primary.TButton", command=self.write_register)
        rw_btn.grid(row=0, column=1, sticky="w")
        self._buttons["rw"] = rw_btn

        scroll_area, body = self.make_vertical_scroll_area(self, propagate_height=True)
        scroll_area.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1, uniform="awp1922_pair")
        body.columnconfigure(1, weight=1, uniform="awp1922_pair")
        body.rowconfigure(0, weight=1)
        self._body = body
        self._body_scroll = scroll_area

        left = ttk.Frame(body, style="Panel.TFrame", padding=18)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.columnconfigure(1, weight=1)
        self._left = left

        right = ttk.Frame(body, style="Panel.TFrame", padding=18)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self._right = right
        scroll_area.canvas.bind("<Configure>", self._reflow_columns, add="+")

        row = 0
        self._add_combo(left, row, "amplitude", self.amp_var, self._amp_labels())
        row += 1
        self._add_combo(left, row, "frequency", self.freq_var, [f"{f} Hz" for f in self.FREQ_OPTIONS])
        row += 1

        play_frame = ttk.Frame(left, style="Panel.TFrame")
        play_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        play_frame.columnconfigure(0, weight=1)
        play_frame.columnconfigure(1, weight=1)

        start_btn = ttk.Button(play_frame, style="Primary.TButton", command=self.start_play)
        start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["start_play"] = start_btn

        stop_btn = ttk.Button(play_frame, style="Secondary.TButton", command=self.stop_play)
        stop_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._buttons["stop_play"] = stop_btn
        row += 1

        power_frame = ttk.Frame(left, style="Panel.TFrame")
        power_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        power_frame.columnconfigure(0, weight=1)
        power_frame.columnconfigure(1, weight=1)

        wake_btn = ttk.Button(power_frame, style="Primary.TButton", command=self.wake)
        wake_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._buttons["wake"] = wake_btn

        shutdown_btn = ttk.Button(power_frame, style="Danger.TButton", command=self.shutdown)
        shutdown_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._buttons["shutdown"] = shutdown_btn

        self.hint_label = ttk.Label(left, style="Muted.TLabel", wraplength=self._HINT_MEASURE_WRAP)
        self.hint_label.grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=(14, 0))

        log_frame = self.make_serial_log(right, height=12, subscribe=True)
        log_frame.grid(row=0, column=0, sticky="nsew")

        self.amp_var.trace_add("write", lambda *_: self._on_amp_changed())
        self.freq_var.trace_add("write", lambda *_: self._on_freq_changed())

        self.refresh_language()

    def _add_footer_field(
        self,
        bar: Any,
        key: str,
        variable: tk.StringVar,
        width: int,
        *,
        readonly: bool = False,
    ) -> None:
        group = bar.add_group()
        label = ttk.Label(group, style="Panel.TLabel")
        label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._labels[key] = label
        entry = ttk.Entry(group, textvariable=variable, width=width)
        if readonly:
            entry.configure(state="readonly")
        entry.grid(row=0, column=1, sticky="w")

    def _stable_column_widths(self) -> tuple[int, int]:
        cached = getattr(self, "_column_width_cache", None)
        if cached is not None:
            return cached
        hint = self.hint_label
        measure = self._HINT_MEASURE_WRAP
        try:
            current = int(hint.cget("wraplength"))
        except (tk.TclError, ValueError):
            current = measure
        if current != measure:
            hint.configure(wraplength=measure)
        try:
            left_w = max(1, int(self._left.winfo_reqwidth()))
            right_w = max(1, int(self._right.winfo_reqwidth()))
        except tk.TclError:
            return (1, 1)
        if left_w <= 1 or right_w <= 1:
            return (left_w, right_w)
        self._column_width_cache = (left_w, right_w)
        return left_w, right_w

    def _reflow_columns(self, event: tk.Event | None = None) -> None:
        if getattr(self, "_reflowing_columns", False):
            return
        canvas = self._body_scroll.canvas
        try:
            width = int(canvas.winfo_width())
        except tk.TclError:
            return
        if width <= 1:
            return
        self._reflowing_columns = True
        try:
            left_w, right_w = self._stable_column_widths()
            stack = width < left_w + right_w + 24
            target_wrap = max(140, (width if stack else width // 2) - 48)
            try:
                current_wrap = int(self.hint_label.cget("wraplength"))
            except (tk.TclError, ValueError):
                current_wrap = None
            if current_wrap != target_wrap:
                self.hint_label.configure(wraplength=target_wrap)
            if stack == getattr(self, "_columns_stacked", None):
                return
            self._columns_stacked = stack
            if stack:
                self._body.columnconfigure(0, weight=1, uniform="")
                self._body.columnconfigure(1, weight=0, uniform="")
                self._body.rowconfigure(1, weight=1)
                self._left.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=0, pady=(0, 12))
                self._right.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=0, pady=0)
            else:
                self._body.columnconfigure(0, weight=1, uniform="awp1922_pair")
                self._body.columnconfigure(1, weight=1, uniform="awp1922_pair")
                self._body.rowconfigure(1, weight=0)
                self._left.grid(row=0, column=0, columnspan=1, sticky="nsew", padx=(0, 6), pady=0)
                self._right.grid(row=0, column=1, columnspan=1, sticky="nsew", padx=(6, 0), pady=0)
        finally:
            self._reflowing_columns = False

    def _amp_labels(self) -> list[str]:
        return [f"{(i + 1) * 2.8:.2f} V" for i in range(0x20)]

    def _add_combo(self, parent: tk.Misc, row: int, key: str, variable: tk.StringVar, values: list[str]) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=8, padx=(0, 10))
        self._labels[key] = label

        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=8)

    def _add_entry(self, parent: tk.Misc, row: int, key: str, variable: tk.StringVar, readonly: bool = False) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=8, padx=(0, 10))
        self._labels[key] = label

        entry = ttk.Entry(parent, textvariable=variable)
        if readonly:
            entry.configure(state="readonly")
        entry.grid(row=row, column=1, sticky="ew", pady=8)

    def refresh_language(self) -> None:
        self._column_width_cache = None
        super().refresh_language()
        for key, label in self._labels.items():
            label.configure(text=self.tr(key))
        for key, btn in self._buttons.items():
            btn.configure(text=self.tr(key))
        self.hint_label.configure(text=self.tr("write_twice_hint"))
        self._reflow_columns()

    def refresh_theme(self) -> None:
        self._column_width_cache = None
        super().refresh_theme()
        self._reflow_columns()

    def _parse_int(self, text: str) -> int:
        return int(text.strip(), 0)

    def _addr(self) -> int:
        return self._parse_int(self.addr_var.get())

    def _ensure_connected(self) -> bool:
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return False
        return True

    def _log_local(self, message: str) -> None:
        return

    def _i2c_failed(self, response: Any) -> bool:
        text = f"{getattr(response, 'error', '')} {getattr(response, 'data', '')}".upper()
        return "I2C" in text

    def _clear_status(self, dev: int | None = None) -> None:
        if getattr(self, "_clearing_status", False):
            return
        self._clearing_status = True
        try:
            if not self.ctx.serial.is_connected:
                return
            self.ctx.serial.read_register_value(self._addr() if dev is None else dev, 0x01, bits=8)
        finally:
            self._clearing_status = False

    def _write_recovered(self, dev: int, reg: int, value: int) -> Any:
        resp = self.ctx.serial.write_register_value(dev, reg, value, bits=8)
        if resp.ok or not self._i2c_failed(resp):
            return resp
        self._clear_status(dev)
        return self.ctx.serial.write_register_value(dev, reg, value, bits=8)

    def detect_chip(self) -> None:
        if not self._ensure_connected():
            return

        addr = self._addr()
        self._log_local(f"READ8 dev=0x{addr:02X} reg=0x00")
        resp = self.ctx.serial.read_register_value(addr, 0x00, bits=8)
        if not resp.ok:
            self._log_local(f"ERROR {resp.error}")
            messagebox.showerror(self.tr("error"), resp.error)
            return

        self._log_local(f"MCU ^{resp.data}~")
        value = self.ctx.serial.extract_rdataw_value(
            resp.data,
            device_addr=addr,
            reg_addr=0x00,
            bits=8,
        )
        if value is not None:
            self._log_local(f"READ8_DATA reg=0x00 value=0x{value & 0xFF:02X}")
        if value == 0x80:
            self.current_ctrl_reg = 0x80
            self.status_var.set(self.tr("chip_detect_success") + " 0x00=0x80")
        else:
            show = "None" if value is None else f"0x{value:02X}"
            self.status_var.set(self.tr("chip_detect_failed") + f" 0x00={show}")
        self._clear_status(addr)

    def _write8(self, reg: int, value: int, show_error: bool = True) -> bool:
        if not self._ensure_connected():
            return False
        dev = self._addr()
        self._log_local(f"WRITE8 dev=0x{dev:02X} reg=0x{reg & 0xFF:02X} value=0x{value & 0xFF:02X}")
        resp = self._write_recovered(dev, reg, value)
        if resp.ok and (reg & 0xFF) == 0x00 and (value & 0x40):
            self._clear_status(dev)
        if not resp.ok:
            self._log_local(f"ERROR {resp.error}")
            if show_error:
                messagebox.showerror(self.tr("error"), resp.error)
            return False
        self._log_local(f"MCU ^{resp.data}~")
        return True

    def _write8_async(self, reg: int, value: int, *, on_success: Any = None) -> None:
        if not self._ensure_connected():
            return

        dev = self._addr()
        self._log_local(f"WRITE8 dev=0x{dev:02X} reg=0x{reg & 0xFF:02X} value=0x{value & 0xFF:02X}")

        def finish(ok: bool, data: str, error: str) -> None:
            if not ok:
                self._log_local(f"ERROR {error}")
                messagebox.showerror(self.tr("error"), error)
                return
            self._log_local(f"MCU ^{data}~")
            if on_success is not None:
                on_success()

        def worker() -> None:
            resp = self._write_recovered(dev, reg, value)
            if resp.ok and (reg & 0xFF) == 0x00 and (value & 0x40):
                self._clear_status(dev)
            try:
                self.after(0, finish, resp.ok, resp.data, resp.error)
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _read_control_reg(self) -> int:
        addr = self._addr()
        self._log_local(f"READ8 dev=0x{addr:02X} reg=0x00")
        resp = self.ctx.serial.read_register_value(addr, 0x00, bits=8)
        if resp.ok:
            self._log_local(f"MCU ^{resp.data}~")
            value = self.ctx.serial.extract_rdataw_value(
                resp.data,
                device_addr=addr,
                reg_addr=0x00,
                bits=8,
            )
            if value is not None:
                self.current_ctrl_reg = value & 0xFF
                self._log_local(f"READ8_DATA reg=0x00 value=0x{self.current_ctrl_reg:02X}")
        else:
            self._log_local(f"ERROR {resp.error}")
        return self.current_ctrl_reg

    def start_play(self) -> None:
        self.current_ctrl_reg = awp1922_play_value(self.current_ctrl_reg)
        self._write8_async(0x00, self.current_ctrl_reg, on_success=lambda: self._set_button_selected("start_play", True))

    def stop_play(self) -> None:
        self.current_ctrl_reg = self.current_ctrl_reg & ~0x40
        self._write8_async(0x00, self.current_ctrl_reg, on_success=lambda: self._set_button_selected("start_play", False))

    def wake(self) -> None:
        self.current_ctrl_reg = self.current_ctrl_reg | 0x80
        self._write8(0x00, self.current_ctrl_reg)

    def shutdown(self) -> None:
        self.current_ctrl_reg = self.current_ctrl_reg & ~0x80
        self._write8(0x00, self.current_ctrl_reg)

    def _write_selected_frequency(self, show_error: bool = False) -> bool:
        code = awp1922_frequency_code(self.freq_var.get(), self.FREQ_OPTIONS)
        if code is None:
            return True
        if not show_error:
            self._silent_write(0x05, code)
            return True
        return self._write8(0x05, code, show_error=True)

    def _write_selected_amplitude(self, show_error: bool = False) -> bool:
        code = awp1922_amplitude_code(self.amp_var.get())
        if code is None:
            return True
        if not show_error:
            self._silent_write(0x06, code)
            return True
        return self._write8(0x06, code, show_error=True)

    def _silent_write(self, reg: int, value: int) -> None:
        serial = getattr(self.ctx, "serial", None)
        if serial is None or not getattr(serial, "is_connected", False):
            return
        state = self.__dict__.get("_silent_write_state")
        if state is None:
            state = {"lock": threading.Lock(), "pending": {}, "running": False}
            self._silent_write_state = state
        with state["lock"]:
            state["pending"][int(reg) & 0xFF] = int(value) & 0xFF
            if state["running"]:
                return
            state["running"] = True
        dev = self._addr()

        def worker() -> None:
            try:
                while True:
                    with state["lock"]:
                        if not state["pending"]:
                            state["running"] = False
                            return
                        batch = list(state["pending"].items())
                        state["pending"].clear()
                    for reg_addr, reg_value in batch:
                        self._write_recovered(dev, reg_addr, reg_value)
            except Exception:
                with state["lock"]:
                    state["running"] = False

        threading.Thread(target=worker, daemon=True).start()

    def _on_amp_changed(self) -> None:
        self._write_selected_amplitude(show_error=False)

    def _on_freq_changed(self) -> None:
        self._write_selected_frequency(show_error=False)

    def read_register(self) -> None:
        if not self._ensure_connected():
            return
        try:
            reg = self._parse_int(self.reg_addr_var.get())
            addr = self._addr()
            self._log_local(f"READ8 dev=0x{addr:02X} reg=0x{reg & 0xFF:02X}")
            resp = self.ctx.serial.read_register_value(addr, reg, bits=8)
            if resp.ok:
                self._log_local(f"MCU ^{resp.data}~")
                value = self.ctx.serial.extract_rdataw_value(
                    resp.data,
                    device_addr=addr,
                    reg_addr=reg,
                    bits=8,
                )
                self.read_value_var.set(resp.data if value is None else f"0x{value & 0xFF:02X}")
                if value is not None:
                    self._log_local(f"READ8_DATA reg=0x{reg & 0xFF:02X} value=0x{value & 0xFF:02X}")
                if (reg & 0xFF) != 0x01:
                    self._clear_status(addr)
            else:
                self._log_local(f"ERROR {resp.error}")
                messagebox.showerror(self.tr("error"), resp.error)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def write_register(self) -> None:
        try:
            reg = self._parse_int(self.reg_addr_var.get())
            value = self._parse_int(self.write_value_var.get())
            self._write8(reg, value)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
