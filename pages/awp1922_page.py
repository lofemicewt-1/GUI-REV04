from __future__ import annotations

import tkinter as tk
import threading
from tkinter import messagebox, ttk
from typing import Any

from pages.base_page import BasePage
from core.base_tool import parse_firmware_error
from core.ui_dispatch import post_to_ui
from pages.fast_label import make_label


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


# Control register 0x00 (8-bit).  Bit numbers below are 0-based.
# NOTE: the requirement says "bit8 = enable"; an 8-bit register has no 0-based
# bit8 (0x100), so this uses the documented wake/enable bit 7 (0x80, i.e.
# 1-based "bit8").  Play is 0-based bit6 (0x40) as documented.  Adjust these two
# constants if the datasheet numbering differs.
AWP1922_CTRL_ENABLE_BIT = 0x80
AWP1922_CTRL_PLAY_BIT = 0x40
# Datasheet: 0x05 bits6-4 I2C_ADDR (RO), bits3-0 FREQ_CONTROL;
#            0x06 bits4-0 VOUT_CONTROL.  Only R/W bits are rewritten.
AWP1922_REWRITE_ON_REENABLE_MASKS = {0x05: 0x0F, 0x06: 0x1F}
AWP1922_REWRITE_ON_REENABLE_REGS = tuple(AWP1922_REWRITE_ON_REENABLE_MASKS)  # frequency, amplitude


def awp1922_play_value(current: int) -> int:
    return (int(current) | AWP1922_CTRL_PLAY_BIT) & 0xFF


def awp1922_ctrl_write_value(value: int, *, allow_disable: bool = False) -> tuple[int, bool]:
    """Return (value to write to 0x00, whether the enable bit had to be set)."""
    value = int(value) & 0xFF
    if allow_disable or value & AWP1922_CTRL_ENABLE_BIT:
        return value, False
    return value | AWP1922_CTRL_ENABLE_BIT, True


def awp1922_needs_rewrite_after_reenable(value: int, reenabled: bool) -> bool:
    return bool(reenabled and (int(value) & AWP1922_CTRL_PLAY_BIT))


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
        addr_label = make_label(addr_group, style="Panel.TLabel")
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
        make_label(status_group, textvariable=self.status_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")

        title_group = reg_panel.add_group()
        reg_title = make_label(title_group, style="Panel.TLabel")
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

        self.hint_label = make_label(left, style="Muted.TLabel", wraplength=420)
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
        label = make_label(group, style="Panel.TLabel")
        label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._labels[key] = label
        entry = ttk.Entry(group, textvariable=variable, width=width)
        if readonly:
            entry.configure(state="readonly")
        entry.grid(row=0, column=1, sticky="w")

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
        left_w = max(1, int(self._left.winfo_reqwidth()))
        right_w = max(1, int(self._right.winfo_reqwidth()))
        stack = width < left_w + right_w + 24
        if stack == getattr(self, "_columns_stacked", None):
            self.hint_label.configure(wraplength=max(140, int(self._left.winfo_width()) - 36))
            return
        self._reflowing_columns = True
        try:
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
            self.hint_label.configure(wraplength=max(140, (width if stack else width // 2) - 48))
        finally:
            self._reflowing_columns = False

    def _amp_labels(self) -> list[str]:
        return [f"{(i + 1) * 2.8:.2f} V" for i in range(0x20)]

    def _add_combo(self, parent: tk.Misc, row: int, key: str, variable: tk.StringVar, values: list[str]) -> None:
        label = make_label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=8, padx=(0, 10))
        self._labels[key] = label

        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=8)

    def _add_entry(self, parent: tk.Misc, row: int, key: str, variable: tk.StringVar, readonly: bool = False) -> None:
        label = make_label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=8, padx=(0, 10))
        self._labels[key] = label

        entry = ttk.Entry(parent, textvariable=variable)
        if readonly:
            entry.configure(state="readonly")
        entry.grid(row=row, column=1, sticky="ew", pady=8)

    def refresh_language(self) -> None:
        super().refresh_language()
        for key, label in self._labels.items():
            label.configure(text=self.tr(key))
        for key, btn in self._buttons.items():
            btn.configure(text=self.tr(key))
        self.hint_label.configure(text=self.tr("write_twice_hint"))

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

    def _serial_log(self, message: str) -> None:
        emit = getattr(self.ctx.serial, "emit_log", None)
        if callable(emit):
            emit(f"AWP1922 {message}")

    def _i2c_failed(self, response: Any) -> bool:
        text = f"{getattr(response, 'error', '')} {getattr(response, 'data', '')}".upper()
        parsed = parse_firmware_error(getattr(response, "data", "")) or parse_firmware_error(
            getattr(response, "error", "")
        )
        if parsed is not None and parsed.code is not None:
            # A firmware that reports a code: only address NACK (2) is recoverable.
            return parsed.reason == "I2C" and parsed.code == 2
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

    def _user_shutdown_addrs(self) -> set[int]:
        addrs = getattr(self, "_shutdown_by_user", None)
        if addrs is None:
            addrs = set()
            self._shutdown_by_user = addrs
        return addrs

    def _prepare_ctrl_write(self, reg: int, value: int, allow_disable: bool, dev: int | None = None) -> tuple[int, bool]:
        if (reg & 0xFF) != 0x00:
            return value, False
        if dev is not None and dev in self._user_shutdown_addrs():
            # User pressed Shutdown on this chip: no auto re-enable until Wake.
            allow_disable = True
        value, reenabled = awp1922_ctrl_write_value(value, allow_disable=allow_disable)
        self.current_ctrl_reg = value
        if reenabled:
            self._serial_log(f"0x00 bit7(enable)=0 -> set to 1, write 0x{value:02X}")
        return value, reenabled

    def _rewrite_after_reenable(self, dev: int, value: int, reenabled: bool) -> None:
        """After re-enable in play mode, rewrite 0x05/0x06 with their current values."""
        if not awp1922_needs_rewrite_after_reenable(value, reenabled):
            return
        for reg, mask in AWP1922_REWRITE_ON_REENABLE_MASKS.items():
            current = None
            resp = self.ctx.serial.read_register_value(dev, reg, bits=8)
            if resp.ok:
                current = self.ctx.serial.extract_rdataw_value(resp.data, device_addr=dev, reg_addr=reg, bits=8)
            if current is None:
                self._serial_log(f"ERROR re-enable rewrite: read 0x{reg:02X} failed: {resp.error or resp.data}")
                continue
            value_rw = current & mask  # drop RO bits (0x05 I2C_ADDR bits6-4)
            wresp = self._write_recovered(dev, reg, value_rw)
            if wresp.ok:
                self._serial_log(f"re-enable rewrite reg=0x{reg:02X} value=0x{value_rw:02X} (read 0x{current & 0xFF:02X})")
            else:
                self._serial_log(f"ERROR re-enable rewrite 0x{reg:02X}: {wresp.error}")

    def _write8(self, reg: int, value: int, show_error: bool = True, *, allow_disable: bool = False) -> bool:
        if not self._ensure_connected():
            return False
        dev = self._addr()
        value, reenabled = self._prepare_ctrl_write(reg, value, allow_disable, dev)
        self._log_local(f"WRITE8 dev=0x{dev:02X} reg=0x{reg & 0xFF:02X} value=0x{value & 0xFF:02X}")
        resp = self._write_recovered(dev, reg, value)
        if resp.ok:
            self._rewrite_after_reenable(dev, value, reenabled)
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
        value, reenabled = self._prepare_ctrl_write(reg, value, False, dev)
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
            if resp.ok:
                self._rewrite_after_reenable(dev, value, reenabled)
            try:
                post_to_ui(self, finish, resp.ok, resp.data, resp.error)
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
        """Set only EN (bit7); no write if already 1; never rewrites 0x05/0x06."""
        if not self._ensure_connected():
            return
        self._user_shutdown_addrs().discard(self._addr())
        current = self._read_control_reg()  # chip readback; falls back to cached value
        if current & AWP1922_CTRL_ENABLE_BIT:
            return
        self.current_ctrl_reg = (current | AWP1922_CTRL_ENABLE_BIT) & 0xFF
        # Explicit enable, not a forced re-enable: _write8 sees EN=1 -> no rewrite.
        self._write8(0x00, self.current_ctrl_reg)

    def shutdown(self) -> None:
        dev = self._addr()
        self._user_shutdown_addrs().add(dev)
        self.current_ctrl_reg = self.current_ctrl_reg & ~AWP1922_CTRL_ENABLE_BIT
        if not self._write8(0x00, self.current_ctrl_reg, allow_disable=True):
            self._user_shutdown_addrs().discard(dev)

    def _write_selected_frequency(self, show_error: bool = False) -> bool:
        code = awp1922_frequency_code(self.freq_var.get(), self.FREQ_OPTIONS)
        if code is None:
            return True
        return self._write8(0x05, code, show_error=show_error)

    def _write_selected_amplitude(self, show_error: bool = False) -> bool:
        code = awp1922_amplitude_code(self.amp_var.get())
        if code is None:
            return True
        return self._write8(0x06, code, show_error=show_error)

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
