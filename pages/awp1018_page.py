from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from copy import deepcopy

import math
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable
from core.lazy_pages import LazyPages
from core.ui_dispatch import post_to_ui

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.awp1018_adc_preset import (
    AWP1018_ADC_FIXED_REGISTERS,
    AWP1018_ADC_REGISTER_30_VALUE,
    AWP1018_ADC_SCHEMA_VERSION,
    AWP1018_ADC_STAGE01_REGISTERS,
    AWP1018_ADC_STAGE4_REGISTERS,
    AWP1018WaveformDescriptor as AWP1018AdcWaveformDescriptor,
    Awp1018AdcPresetModel,
    build_awp1018_adc_write_plan,
    pack_awp1018_adc_stage4_payload,
    required_awp1018_descriptor_slices,
)
from core.awp1018_user_preset import (
    AWP1018_REGISTER_SCRIPT_OPTIONAL_SERIAL4_REGISTERS,
    AWP1018_REGISTER_SCRIPT_SNAPSHOT_REGISTERS,
    LegacyJsonPreset,
    RegisterScriptPreset,
    build_awp1018_register_script,
    decode_awp1018_register_script_page_settings,
    format_awp1018_register_script,
    parse_awp1018_user_preset,
    validate_awp1018_register_script,
)
from core.arduino_preset_sync import decode_awp1018_board_preset_page_settings
from core.base_tool import (
    RegisterScriptOperation,
    SerialResponse,
    execute_register_script_operations,
)
from pages.base_page import BasePage, SimpleTooltip, VerticalScrollArea, fit_toplevel_to_screen
from pages.button_mode_chrome import ResponsiveCardGrid
from pages.chip_settings_model import ChipSettingsStore
from pages.preset_toolbar import PresetToolbar
from pages.waveform_preview import PreviewSegment, compact_preview, draw_omissions
from pages.gpio_controls import GpioControlGroup, GpioControlPanel, active_serial_identity
from styles.fonts import page_title_weight


class AWP1018Page(BasePage):
    page_name = "awp1018"

    DEVICE_ADDRS = tuple(range(0x48, 0x50))
    ID_REG = 0x2B
    CHIP_ID = 0x1018

    def __init__(self, parent: tk.Misc, ctx: Any, *, defer_build: bool = False) -> None:
        super().__init__(parent, ctx)
        self.addr_var = tk.StringVar(value="0x48")
        self.reg_addr_var = tk.StringVar(value="0x00")
        self.write_value_var = tk.StringVar(value="0x0000")
        self.read_value_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        self.current_mode = "awp1018_normal_mode"
        self.mode_buttons: dict[str, ttk.Button] = {}
        self.mode_pages = LazyPages(self._install_mode_page)
        self.button_settings_store = ChipSettingsStore()
        self._board_slot_by_connection: dict[tuple[Any, ...], int] = {}
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
        self.gpio_controls = GpioControlGroup()

        header = self.make_title(self, "awp1018")
        header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())

        self.mode_scroll, self.mode_container = self.make_vertical_scroll_area(
            self,
            horizontal=True,
            min_content_width=1320,
            propagate_height=True,
        )
        self.mode_scroll.grid(row=1, column=0, sticky="nsew")
        yield
        # Keep the first glyph inside the canvas clip edge when a small
        # window is scrolled fully left.
        self.mode_container.configure(padding=(8, 0, 8, 0))
        self.mode_container.rowconfigure(0, weight=1)
        self.mode_container.columnconfigure(0, weight=1)

        self.footer_scroll, footer_content = self.make_vertical_scroll_area(
            self, horizontal=False, reserve_vertical_gutter=True
        )
        self.footer_scroll.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        footer_content.columnconfigure(0, weight=1)
        yield
        footer, actions, reg_panel, identity = self.make_merged_footer(footer_content)
        footer.grid(row=0, column=0, sticky="ew")
        self._footer_panel = footer
        self._footer_fit_job = None
        footer.bind("<Configure>", self._queue_footer_fit)
        self.bind("<Configure>", self._queue_footer_fit, add="+")
        yield
        yield from self._build_state_actions_steps(actions)
        reg_panel.columnconfigure(8, weight=1)
        yield from self._build_register_bar_steps(reg_panel)
        yield from self._build_identity_bar_steps(identity)

        self.mode_pages.register("awp1018_normal_mode", lambda defer_build=False: AWP1018NormalModePage(
            self.mode_container,
            self.ctx,
            import_page_settings=self.import_settings,
            export_page_settings=self.export_settings,
            sync_decoded_settings=self._sync_decoded_page_settings,
            gpio_group=self.gpio_controls,
            settings_store=self.button_settings_store,
            board_slot_provider=self._get_board_slot_selection,
            on_board_slot_selected=self._on_button_board_slot_selected,
            on_gpio_mode_success=self._on_gpio_mode_success,
            on_gpio_mode_failure=self._on_gpio_mode_failure,
            defer_build=defer_build,
        ), staged=True)
        self.mode_pages.register("awp1018_diff_mode", lambda defer_build=False: AWP1018NormalModePage(
            self.mode_container,
            self.ctx,
            diff_mode=True,
            import_page_settings=self.import_settings,
            export_page_settings=self.export_settings,
            sync_decoded_settings=self._sync_decoded_page_settings,
            gpio_group=self.gpio_controls,
            settings_store=self.button_settings_store,
            board_slot_provider=self._get_board_slot_selection,
            on_board_slot_selected=self._on_button_board_slot_selected,
            on_gpio_mode_success=self._on_gpio_mode_success,
            on_gpio_mode_failure=self._on_gpio_mode_failure,
            defer_build=defer_build,
        ), staged=True)
        yield
        self.mode_pages.register("full_mode", lambda defer_build=False: AWP1018FullModePage(
            self.mode_container,
            self.ctx,
            gpio_group=self.gpio_controls,
            on_gpio_mode_success=self._on_gpio_mode_success,
            on_gpio_mode_failure=self._on_gpio_mode_failure,
            defer_build=defer_build,
        ), staged=True)

        self.refresh_language()
        if not self._preparing:
            self._on_addr_changed()
        yield from self.mode_pages.prepare(self.current_mode)
        self.show_mode(self.current_mode, update_chip=not self._preparing)
        self._prepared_mode_pending = self._preparing

    def _install_mode_page(self, _name: str, page: ttk.Frame) -> None:
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        restore = getattr(page, "_restore_scaled_fonts", None)
        if callable(restore):
            restore()
            return
        refresh = getattr(self.ctx.app, "_refresh_widget_fonts", None)
        if callable(refresh):
            refresh(page)

    def _queue_footer_fit(self, _event=None) -> None:
        if self._footer_fit_job is None:
            self._footer_fit_job = self.after(0, self._fit_footer_height)

    def _fit_footer_height(self) -> None:
        self._footer_fit_job = None
        try:
            if self.winfo_height() <= 1:
                return
            available = max(96, int(self.winfo_height() * .35))
            desired = min(self._footer_panel.winfo_reqheight() + 16, available)
            if self.footer_scroll.canvas.winfo_reqheight() != desired:
                self.footer_scroll.canvas.configure(height=desired)
        except tk.TclError:
            pass

    def _build_state_actions(self, parent: tk.Misc) -> None:
        for _ in self._build_state_actions_steps(parent):
            pass

    def _build_state_actions_steps(self, parent: tk.Misc) -> Iterator[None]:
        yield
        buttons = (
            ("awp1018_reset", "Secondary.TButton", self.reset_chip),
            ("awp1018_sleep", "Secondary.TButton", self.sleep_chip),
            ("shutdown", "Danger.TButton", self.shutdown_chip),
            ("wake", "Primary.TButton", self.wake_chip),
        )
        for column, (key, style, command) in enumerate(buttons):
            parent.columnconfigure(column, weight=1, uniform="chip_state_actions")
            button = ttk.Button(parent, style=style, command=command)
            button.grid(row=0, column=column, sticky="ew", padx=(0, 8))
            self._buttons[key] = button

    def _build_identity_bar(self, parent: tk.Misc) -> None:
        for _ in self._build_identity_bar_steps(parent):
            pass

    def _build_identity_bar_steps(self, parent: tk.Misc) -> Iterator[None]:
        serial_group = parent.add_group()
        self.make_serial_selector(serial_group, page_name=self.page_name, row=0, column=0)
        addr_group = parent.add_group()
        addr_label = ttk.Label(addr_group, style="Panel.TLabel")
        addr_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._labels["current_chip_addr"] = addr_label
        yield
        self.addr_combo = ttk.Combobox(
            addr_group,
            textvariable=self.addr_var,
            values=[f"0x{addr:02X}" for addr in self.DEVICE_ADDRS],
            state="readonly",
            width=10,
        )
        self.addr_combo.grid(row=0, column=1, sticky="w")
        self.addr_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_addr_changed())
        detect_group = parent.add_group()
        detect_btn = ttk.Button(detect_group, style="Primary.TButton", command=self.detect_chip)
        detect_btn.grid(row=0, column=0, sticky="w")
        yield
        self._buttons["detect_chip"] = detect_btn
        status_group = parent.add_group()
        ttk.Label(status_group, textvariable=self.status_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")

    def _build_register_bar(self, parent: tk.Misc) -> None:
        for _ in self._build_register_bar_steps(parent):
            pass

    def _build_register_bar_steps(self, parent: tk.Misc) -> Iterator[None]:
        title_group = parent.add_group()
        title_label = ttk.Label(title_group, style="Panel.TLabel")
        title_label.grid(row=0, column=0, sticky="w")
        self._labels["read_write_registers"] = title_label

        addr_group = parent.add_group()
        reg_addr_label = ttk.Label(addr_group, style="Panel.TLabel")
        yield
        reg_addr_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._labels["reg_addr"] = reg_addr_label
        ttk.Entry(addr_group, textvariable=self.reg_addr_var, width=8).grid(row=0, column=1, sticky="w")

        write_group = parent.add_group()
        write_value_label = ttk.Label(write_group, style="Panel.TLabel")
        write_value_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        yield
        self._labels["write_value"] = write_value_label
        ttk.Entry(write_group, textvariable=self.write_value_var, width=10).grid(row=0, column=1, sticky="w")

        read_group = parent.add_group()
        read_value_label = ttk.Label(read_group, style="Panel.TLabel")
        read_value_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._labels["read_value"] = read_value_label
        yield
        ttk.Entry(read_group, textvariable=self.read_value_var, width=12, state="readonly").grid(row=0, column=1, sticky="w")

        button_group = parent.add_group()
        read_btn = ttk.Button(button_group, style="Secondary.TButton", command=self.read_register)
        read_btn.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._buttons["rr"] = read_btn
        write_btn = ttk.Button(button_group, style="Primary.TButton", command=self.write_register)
        yield
        write_btn.grid(row=0, column=1, sticky="w")
        self._buttons["rw"] = write_btn

    def _parse_int(self, text: str) -> int:
        return int(text.strip(), 0)

    def _addr(self) -> int:
        return self._parse_int(self.addr_var.get())

    def _on_addr_changed(self) -> None:
        addr = self._addr()
        previous_addr_value = self.ctx.state.extra.get(
            "awp1018_device_addr",
            self.ctx.state.awp192x_device_addr,
        )
        try:
            previous_addr = (
                int(previous_addr_value, 0)
                if isinstance(previous_addr_value, str)
                else int(previous_addr_value)
            ) & 0xFF
        except (TypeError, ValueError):
            previous_addr = addr
        if previous_addr != addr:
            current_page = dict.get(self.mode_pages, self.current_mode)
            on_hide = getattr(current_page, "on_hide", None)
            if callable(on_hide):
                on_hide()
            for page in self.mode_pages.built_values():
                save_draft = getattr(page, "save_button_draft", None)
                if callable(save_draft):
                    save_draft()
        self.ctx.state.extra["awp1018_device_addr"] = addr
        self.ctx.state.awp192x_device_addr = addr
        if previous_addr != addr:
            page = dict.get(self.mode_pages, self.current_mode)
            activate_draft = getattr(page, "activate_button_draft", None)
            if callable(activate_draft):
                activate_draft()

    def _board_connection_identity(self) -> tuple[Any, ...]:
        identity = active_serial_identity(self.ctx, self.page_name)
        if identity is not None:
            return tuple(identity)
        selected_port = getattr(self.ctx.state, "selected_port", None)
        selector = getattr(self.ctx.serial, "selected_port_for_page", None)
        if callable(selector):
            try:
                selected_port = selector(self.page_name)
            except TypeError:
                selected_port = selector()
        return ("offline", str(selected_port or ""))

    def _get_board_slot_selection(
        self,
        board_identity: tuple[Any, ...],
        model_slot: int | str | None = None,
    ) -> int:
        stored = self._board_slot_by_connection.get(board_identity)
        if stored is None:
            try:
                stored = int(model_slot) if model_slot is not None else 0
            except (TypeError, ValueError):
                stored = 0
            stored = max(0, min(2, stored))
            self._board_slot_by_connection[board_identity] = stored
        return stored

    def _on_button_board_slot_selected(self, mode: str, slot: int | str) -> None:
        try:
            selected = int(slot)
        except (TypeError, ValueError):
            return
        selected = max(0, min(2, selected))
        identity = self._board_connection_identity()
        self._board_slot_by_connection[identity] = selected
        for page in self.mode_pages.built_values():
            if getattr(page, "_draft_board_identity", None) != identity:
                continue
            model = getattr(page, "_draft_model", None)
            if model is not None:
                model.set_slot_selection(selected)
            toolbar = getattr(page, "preset_toolbar", None)
            if toolbar is not None:
                toolbar.slot_var.set(str(selected))

    def _ensure_connected(self) -> bool:
        if not self.ctx.serial.is_connected:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return False
        return True

    def read_register(self) -> None:
        if not self._ensure_connected():
            return
        try:
            reg = self._parse_int(self.reg_addr_var.get())
            address = self._addr()
            resp = self.ctx.serial.read_register_value(address, reg, bits=16)
            if resp.ok:
                value = self.ctx.serial.extract_rdataw_value(
                    resp.data,
                    device_addr=address,
                    reg_addr=reg,
                    bits=16,
                )
                self.read_value_var.set(resp.data if value is None else f"0x{value & 0xFFFF:04X}")
            else:
                messagebox.showerror(self.tr("error"), resp.error)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def write_register(self) -> None:
        if not self._ensure_connected():
            return
        try:
            reg = self._parse_int(self.reg_addr_var.get())
            value = self._parse_int(self.write_value_var.get())
            resp = self.ctx.serial.write_register_value(self._addr(), reg, value, bits=16)
            if not resp.ok:
                messagebox.showerror(self.tr("error"), resp.error)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))

    def _write_reg(self, reg: int, value: int, *, show_error: bool = True) -> bool:
        if not self._ensure_connected():
            return False
        resp = self.ctx.serial.write_register_value(self._addr(), reg, value, bits=16)
        if not resp.ok and show_error:
            messagebox.showerror(self.tr("error"), resp.error)
        return resp.ok

    def _read_reg(self, reg: int, *, show_error: bool = True) -> int | None:
        if not self._ensure_connected():
            return None
        address = self._addr()
        resp = self.ctx.serial.read_register_value(address, reg, bits=16)
        if not resp.ok:
            if show_error:
                messagebox.showerror(self.tr("error"), resp.error)
            return None
        return self.ctx.serial.extract_rdataw_value(
            resp.data,
            device_addr=address,
            reg_addr=reg,
            bits=16,
        )

    def reset_chip(self) -> None:
        self._write_reg(0x01, 0x5283)

    def sleep_chip(self) -> None:
        self._write_reg(0x01, 0x5350)

    def shutdown_chip(self) -> None:
        self._write_reg(0x01, 0x5337)

    def wake_chip(self) -> None:
        if not self._ensure_connected():
            return
        self._wake_attempts_remaining = 20
        self._set_wake_button_state("disabled")
        self._poll_wake_status()

    def _poll_wake_status(self) -> None:
        value = self._read_reg(self.ID_REG, show_error=False)
        if value == self.CHIP_ID:
            self._set_wake_button_state("normal")
            messagebox.showinfo(self.tr("info"), self.tr("chip_detect_success") + f": 0x{value:04X}")
            return

        self._wake_attempts_remaining = getattr(self, "_wake_attempts_remaining", 0) - 1
        if self._wake_attempts_remaining <= 0:
            self._set_wake_button_state("normal")
            messagebox.showerror(self.tr("error"), self.tr("chip_detect_failed"))
            return

        self.after(100, self._poll_wake_status)

    def export_settings(self) -> None:
        page = self.mode_pages.get(self.current_mode)
        if not isinstance(page, AWP1018NormalModePage) or isinstance(page, AWP1018FullModePage):
            messagebox.showerror(self.tr("error"), self.tr("awp1018_settings_mode_unavailable"))
            return
        if not self._ensure_connected():
            return
        try:
            address = self._addr()
            if address not in self.DEVICE_ADDRS:
                raise ValueError(self.tr("awp1018_txt_address_range"))
            page_state = page._capture_adc_preset_state(allow_serial4=True)
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
            return

        path = filedialog.asksaveasfilename(
            title=self.tr("awp1018_export_settings"),
            defaultextension=".txt",
            initialfile=(
                "awp1018_diff_register_script.txt"
                if page.diff_mode
                else "awp1018_normal_register_script.txt"
            ),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        self._set_user_preset_buttons_state("disabled")

        def worker() -> None:
            snapshot: dict[int, int] | None = None
            error_kind = ""
            error_detail = ""
            try:
                with self.ctx.serial.snapshot_transaction():
                    chip_id = self._read_register_value(address, self.ID_REG)
                    if chip_id != self.CHIP_ID:
                        error_kind = "chip_mismatch"
                        error_detail = f"0x{chip_id:04X}"
                    else:
                        registers = list(AWP1018_REGISTER_SCRIPT_SNAPSHOT_REGISTERS)
                        if not page_state.get("serial4_selected"):
                            registers = [
                                register
                                for register in registers
                                if register not in AWP1018_REGISTER_SCRIPT_OPTIONAL_SERIAL4_REGISTERS
                            ]
                        snapshot = {
                            register: self._read_register_value(address, register)
                            for register in registers
                        }
            except Exception as exc:
                error_kind = "read_failed"
                error_detail = str(exc)
            try:
                self.after(
                    0,
                    self._finish_awp1018_txt_export,
                    page,
                    page_state,
                    snapshot,
                    address,
                    path,
                    error_kind,
                    error_detail,
                )
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def import_settings(self) -> None:
        path = filedialog.askopenfilename(
            title=self.tr("awp1018_import_settings"),
            filetypes=[
                ("Text files", "*.txt"),
                ("JSON files", "*.json"),
                ("All supported", "*.txt;*.json"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8-sig")
            preset = parse_awp1018_user_preset(path, content)
        except Exception as exc:
            messagebox.showerror(
                self.tr("error"),
                self._format_awp1018_preset_error(path, exc),
            )
            return

        if isinstance(preset, RegisterScriptPreset):
            self._start_awp1018_txt_import(preset)
            return

        self._import_awp1018_legacy_json(preset)

    def _import_awp1018_legacy_json(self, preset: LegacyJsonPreset) -> None:
        if not self._ensure_connected():
            return
        mode_key = {
            "normal": "awp1018_normal_mode",
            "diff": "awp1018_diff_mode",
        }[preset.mode]
        page = self.mode_pages.get(mode_key)
        if not isinstance(page, AWP1018NormalModePage):
            messagebox.showerror(self.tr("error"), self.tr("awp1018_settings_invalid"))
            return

        self._set_user_preset_buttons_state("disabled")
        try:
            page._validate_imported_settings(preset.settings)
            self.show_mode(mode_key)
            if not page.import_settings(preset.settings):
                return
            page.save_button_draft()
        except Exception as exc:
            messagebox.showerror(
                self.tr("error"),
                f"{self.tr('awp1018_json_import_failed')}\n{exc}",
            )
            return
        finally:
            self._set_user_preset_buttons_state("normal")
        messagebox.showinfo(self.tr("info"), self.tr("awp1018_settings_imported"))

    def _start_awp1018_txt_import(self, preset: RegisterScriptPreset) -> None:
        if not self._ensure_connected():
            return
        cancel = getattr(type(self.ctx.serial), "cancel_register_settings", None)
        if callable(cancel):
            self.ctx.serial.cancel_register_settings(self.page_name)
        self._set_user_preset_buttons_state("disabled")

        def worker() -> None:
            try:
                with self.ctx.serial.snapshot_transaction():
                    error = self._execute_awp1018_txt_preset(preset)
            except Exception as exc:
                error = str(exc)
            try:
                self.after(0, self._finish_awp1018_txt_import, preset, error)
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _execute_awp1018_txt_preset(self, preset: RegisterScriptPreset) -> str | None:
        validated = validate_awp1018_register_script(preset)
        response = self.ctx.serial.read_register_value(
            validated.device_addr,
            self.ID_REG,
            bits=16,
        )
        if not response.ok:
            return self.tr(
                "awp1018_txt_probe_failed",
                address=f"0x{validated.device_addr:02X}",
                error=response.error or response.data,
            )
        chip_id = self.ctx.serial.extract_rdataw_value(
            response.data,
            device_addr=validated.device_addr,
            reg_addr=self.ID_REG,
            bits=16,
        )
        if chip_id != self.CHIP_ID:
            actual = "--" if chip_id is None else f"0x{chip_id & 0xFFFF:04X}"
            return self.tr(
                "awp1018_txt_chip_mismatch",
                address=f"0x{validated.device_addr:02X}",
                actual=actual,
            )

        def transport(operation: RegisterScriptOperation) -> SerialResponse:
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
                    int(operation.value or 0),
                    bits=16,
                )
            return SerialResponse(False, error=f"unsupported operation {operation.action}")

        return execute_register_script_operations(list(validated.operations), transport)

    def _finish_awp1018_txt_import(
        self,
        preset: RegisterScriptPreset,
        error: str | None,
    ) -> None:
        self._set_user_preset_buttons_state("normal")
        if error:
            messagebox.showerror(
                self.tr("error"),
                f"{self.tr('awp1018_txt_import_failed')}\n{error}",
            )
            return
        try:
            registers, descriptors = decode_awp1018_register_script_page_settings(preset)
            self._sync_decoded_page_settings(
                preset.device_addr,
                registers,
                descriptors,
            )
        except Exception as exc:
            messagebox.showerror(
                self.tr("error"),
                f"{self.tr('awp1018_txt_import_failed')}\n{exc}",
            )
            return
        messagebox.showinfo(self.tr("info"), self.tr("awp1018_txt_imported_raw"))

    def _sync_decoded_page_settings(
        self,
        device_addr: int,
        register_values: dict[int, int],
        descriptors: dict[int, tuple[int, int, int, int]],
    ) -> None:
        if 0x34 not in register_values:
            raise ValueError("AWP1018 settings are missing mode register 0x34")
        mode_key = (
            "awp1018_diff_mode"
            if int(register_values[0x34]) & 0x0008
            else "awp1018_normal_mode"
        )
        page = self.mode_pages.get(mode_key)
        apply_settings = getattr(page, "_apply_decoded_page_settings", None)
        if not callable(apply_settings):
            raise ValueError("AWP1018 settings page is unavailable")

        previous_address = self.addr_var.get()
        previous_mode = getattr(self, "current_mode", None)
        try:
            self.addr_var.set(f"0x{int(device_addr) & 0xFF:02X}")
            self._on_addr_changed()
            self.show_mode(mode_key, update_chip=False)
            apply_settings(register_values, descriptors)
            save_draft = getattr(page, "save_button_draft", None)
            if callable(save_draft):
                save_draft()
        except Exception:
            self.addr_var.set(previous_address)
            try:
                self._on_addr_changed()
                if previous_mode in self.mode_pages:
                    self.show_mode(previous_mode, update_chip=False)
            except Exception:
                pass
            raise

    def _finish_awp1018_txt_export(
        self,
        page: AWP1018NormalModePage,
        page_state: dict[str, Any],
        snapshot: dict[int, int] | None,
        address: int,
        path: str,
        error_kind: str,
        error_detail: str,
    ) -> None:
        try:
            if error_kind == "chip_mismatch":
                raise ValueError(
                    self.tr(
                        "awp1018_txt_chip_mismatch",
                        address=f"0x{address:02X}",
                        actual=error_detail or "--",
                    )
                )
            if error_kind or snapshot is None:
                raise ValueError(error_detail or "register snapshot failed")
            operations = page._build_awp1018_register_script_operations(
                page_state,
                snapshot,
                address,
            )
            content = format_awp1018_register_script(page_state["script_mode"], operations)
            Path(path).write_text(content, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(
                self.tr("error"),
                f"{self.tr('awp1018_txt_export_failed')}\n{exc}",
            )
            return
        finally:
            self._set_user_preset_buttons_state("normal")
        messagebox.showinfo(self.tr("info"), self.tr("awp1018_settings_exported"))

    def _read_register_value(
        self,
        address: int,
        register: int,
    ) -> int:
        response = self.ctx.serial.read_register_value(address, register, bits=16)
        if not response.ok:
            raise ValueError(response.error or f"failed to read register 0x{register:02X}")
        value = self.ctx.serial.extract_rdataw_value(
            response.data,
            device_addr=address,
            reg_addr=register,
            bits=16,
        )
        if value is None:
            raise ValueError(f"failed to parse register 0x{register:02X}: {response.data}")
        return value & 0xFFFF

    def _set_user_preset_buttons_state(self, state: str) -> None:
        for page in self.mode_pages.values():
            buttons = getattr(page, "_buttons", {})
            for key in (
                "button_mode_import_user_preset",
                "button_mode_export_user_preset",
            ):
                button = buttons.get(key)
                if button is not None:
                    button.configure(state=state)

    def _format_awp1018_preset_error(self, path: str, exc: Exception) -> str:
        detail = str(exc)
        suffix = Path(path).suffix.lower()
        if suffix not in {".txt", ".json"}:
            return self.tr("awp1018_preset_unsupported_format")
        if suffix == ".json":
            return f"{self.tr('awp1018_json_import_failed')}\n{detail}"
        lowered = detail.lower()
        if "address mismatch" in lowered:
            return self.tr("awp1018_txt_address_mismatch", error=detail)
        if "address range" in lowered:
            return self.tr("awp1018_txt_address_range")
        return self.tr("awp1018_txt_metadata_invalid", error=detail)

    def _set_wake_button_state(self, state: str) -> None:
        wake_button = self._buttons.get("wake")
        if wake_button is not None:
            wake_button.configure(state=state)

    def detect_chip(self) -> None:
        if not self._ensure_connected():
            return

        last_detail = ""
        for addr in self.DEVICE_ADDRS:
            value, error = self._read_chip_id(addr, attempts=3)
            if value == self.CHIP_ID:
                self.addr_var.set(f"0x{addr:02X}")
                self._on_addr_changed()
                self.status_var.set(self.tr("chip_detect_success") + f" 0x{addr:02X}: 0x{value:04X}")
                return

            if value is not None:
                last_detail = f"0x{addr:02X}: 0x{value & 0xFFFF:04X}"
            elif error:
                last_detail = error

        suffix = f" {last_detail}" if last_detail else ""
        self.status_var.set(self.tr("chip_detect_failed") + suffix)

    def _read_chip_id(self, addr: int, *, attempts: int = 3) -> tuple[int | None, str]:
        last_error = ""
        for attempt in range(max(1, attempts)):
            resp = self.ctx.serial.read_register_value(addr, self.ID_REG, bits=16)
            if resp.ok:
                value = self.ctx.serial.extract_rdataw_value(
                    resp.data,
                    device_addr=addr,
                    reg_addr=self.ID_REG,
                    bits=16,
                )
                if value is not None:
                    return value, ""
                last_error = resp.data or "empty response"
            else:
                last_error = resp.error

            if attempt + 1 < attempts:
                self.update_idletasks()
                self.after(50)

        return None, last_error

    def show_mode(self, mode: str, *, update_chip: bool = True) -> None:
        if mode not in self.mode_pages:
            return
        self.mode_scroll.clamp_width = mode != "full_mode"
        previous_mode = self.current_mode
        previous_page = self.mode_pages.get(previous_mode)
        if previous_mode != mode and previous_page is not None:
            on_hide = getattr(previous_page, "on_hide", None)
            if callable(on_hide):
                on_hide()
            save_draft = getattr(previous_page, "save_button_draft", None)
            if callable(save_draft):
                save_draft()
        self.current_mode = mode
        if update_chip and mode == "awp1018_diff_mode":
            self._set_diff_mode_registers(True)
        elif update_chip and mode == "awp1018_normal_mode":
            self._set_diff_mode_registers(False)
        for page in dict.values(self.mode_pages):
            page.grid_remove()
        page = self.mode_pages[mode]
        page.grid()
        activate_draft = getattr(page, "activate_button_draft", None)
        if callable(activate_draft):
            activate_draft()
        if update_chip and mode != "awp1018_diff_mode" and hasattr(page, "on_show"):
            page.on_show()
        self._sync_mode_button_styles()
        # Each mode has a different requested height.  Recompute the outer
        # canvas after Tk has processed the new grid request so a short Full
        # page can use a maximized viewport while Normal/Diff retain their
        # vertical scroll range.
        try:
            self.after(0, self._sync_mode_scroll_layout)
        except tk.TclError:
            pass

    def _sync_mode_scroll_layout(self) -> None:
        try:
            # Configure events complete the geometry pass. Re-entering the
            # idle loop here drains every pending redraw before input can run.
            self.mode_scroll._sync_scroll_region()
        except tk.TclError:
            pass

    def _set_diff_mode_registers(self, enabled: bool) -> None:
        submit = getattr(type(self.ctx.serial), "queue_register_setting", None)
        if callable(submit):
            self.ctx.serial.queue_register_setting(
                self.page_name, self._addr(), 0x34, (1 << 3) if enabled else 0,
                clear_mask=1 << 3, address_state_key="awp1018_device_addr",
            )
            return
        if not self.ctx.serial.is_connected:
            return

        addr = self._addr()
        resp = self.ctx.serial.read_register_value(addr, 0x34, bits=16)
        if not resp.ok:
            return
        value = self.ctx.serial.extract_rdataw_value(
            resp.data,
            device_addr=addr,
            reg_addr=0x34,
            bits=16,
        )
        if value is None:
            return
        next_value = (value | (1 << 3)) if enabled else (value & ~(1 << 3))
        self.ctx.serial.write_register_value(addr, 0x34, next_value, bits=16)

    def _on_gpio_mode_success(self, pin: str, mode: str) -> None:
        if pin != "D4":
            return
        navigation = getattr(getattr(self.ctx, "app", None), "navigation", None)
        pages = getattr(navigation, "pages", {})
        awp1221 = dict.get(pages, "awp1221") if isinstance(pages, dict) else None
        if awp1221 is None:
            return
        source_identity = active_serial_identity(self.ctx, self.page_name)
        target_identity = active_serial_identity(
            getattr(awp1221, "ctx", self.ctx), "awp1221"
        )
        if source_identity is None or source_identity != target_identity:
            return
        sync_mode = getattr(awp1221, "_sync_shared_d4_gpio_mode", None)
        if callable(sync_mode):
            sync_mode(mode)

    def _on_gpio_mode_failure(self, pin: str) -> None:
        if pin != "D4":
            return
        navigation = getattr(getattr(self.ctx, "app", None), "navigation", None)
        pages = getattr(navigation, "pages", {})
        awp1221 = dict.get(pages, "awp1221") if isinstance(pages, dict) else None
        if awp1221 is None:
            return
        source_identity = active_serial_identity(self.ctx, self.page_name)
        target_identity = active_serial_identity(
            getattr(awp1221, "ctx", self.ctx), "awp1221"
        )
        previous_target_identity = getattr(awp1221, "_shared_d4_panel_identity", None)
        same_active_session = (
            source_identity is not None
            and target_identity is not None
            and source_identity == target_identity
        )
        same_disconnected_session = (
            source_identity is None
            and target_identity is None
            and previous_target_identity is not None
        )
        one_side_disconnected_after_same_session = (
            previous_target_identity is not None
            and (
                source_identity == previous_target_identity
                or target_identity == previous_target_identity
            )
            and (source_identity is None or target_identity is None)
        )
        if not (
            same_active_session
            or same_disconnected_session
            or one_side_disconnected_after_same_session
        ):
            return
        invalidate = getattr(awp1221, "_invalidate_shared_d4_gpio_mode", None)
        if callable(invalidate):
            invalidate(source_identity)
            return
        sync_mode = getattr(awp1221, "_sync_shared_d4_gpio_mode", None)
        if callable(sync_mode):
            sync_mode(None)

    def _on_serial_selector_changed(self, control: dict[str, Any]) -> None:
        current_page = dict.get(self.mode_pages, self.current_mode)
        on_hide = getattr(current_page, "on_hide", None)
        if callable(on_hide):
            on_hide()
        for page in self.mode_pages.built_values():
            save_draft = getattr(page, "save_button_draft", None)
            if callable(save_draft):
                save_draft()
        super()._on_serial_selector_changed(control)
        gpio_controls = getattr(self, "gpio_controls", None)
        if gpio_controls is not None:
            gpio_controls.invalidate_modes()
        page = self.mode_pages.get(self.current_mode)
        activate_draft = getattr(page, "activate_button_draft", None)
        if callable(activate_draft):
            activate_draft()

    def on_show(self) -> None:
        if getattr(self, "_prepared_mode_pending", False):
            self._prepared_mode_pending = False
            self._set_diff_mode_registers(self.current_mode == "awp1018_diff_mode")
        gpio_controls = getattr(self, "gpio_controls", None)
        if gpio_controls is not None:
            gpio_controls.invalidate_modes()
        self._on_addr_changed()
        page = self.mode_pages.get(self.current_mode)
        activate_draft = getattr(page, "activate_button_draft", None)
        if callable(activate_draft):
            activate_draft()
        if self.current_mode != "awp1018_diff_mode" and page is not None and hasattr(page, "on_show"):
            page.on_show()

    def on_hide(self) -> None:
        for page in self.mode_pages.built_values():
            on_hide = getattr(page, "on_hide", None)
            if callable(on_hide):
                on_hide()
            save_draft = getattr(page, "save_button_draft", None)
            if callable(save_draft):
                save_draft()

    def refresh_language(self) -> None:
        super().refresh_language()
        for key, label in self._labels.items():
            label.configure(text=self.tr(key))
        for key, btn in self._buttons.items():
            btn.configure(text=self.tr(key))
        for key, btn in self.mode_buttons.items():
            btn.configure(text=self.tr(key))
        for page in dict.values(self.mode_pages):
            if hasattr(page, "refresh_language"):
                page.refresh_language()
        gpio_controls = getattr(self, "gpio_controls", None)
        if gpio_controls is not None:
            gpio_controls.refresh_language()

    def refresh_theme(self) -> None:
        super().refresh_theme()
        for page in dict.values(self.mode_pages):
            if hasattr(page, "refresh_theme"):
                page.refresh_theme()


class AWP1018NormalModePage(BasePage):
    page_name = "awp1018_normal_mode"

    ID_REG = 0x2B
    CHIP_ID = 0x1018
    STATUS_IDLE_COLOR = "#E5E7EB"
    STATUS_READY_COLOR = "#FACC15"
    STATUS_PRESS_COLOR = "#2563EB"
    STATUS_RELEASE_COLOR = "#9333EA"
    STATUS_TIMEOUT_COLOR = "#DC2626"
    ADC_OVV_OPTIONS = ["1 us", "100 us", "500 us", "1 ms", "2 ms", "4 ms", "8 ms", "16 ms"]
    ADC_SAMPLE_UPDATE_OPTIONS = ("8 us", "16 us", "32 us", "64 us")
    ZERO_TIME_OPTIONS = ("100 us", "200 us", "400 us", "800 us")
    ZERO_TIME_CODES = {
        "800 us": 0b00,
        "400 us": 0b01,
        "200 us": 0b10,
        "100 us": 0b11,
    }
    RETURN_BIT_TIME_OPTIONS = ("1024 ms", "256 ms", "128 ms", "64 ms", "32 ms", "16 ms", "8 ms", "1 ms")
    ARBITRATION_MODE_KEYS = (
        "awp1018_arbitration_discard",
        "awp1018_arbitration_interrupt",
        "awp1018_arbitration_sequence",
        "awp1018_arbitration_join",
    )
    ARBITRATION_MODE_CODES = (0b11, 0b10, 0b01, 0b00)
    ROM_WAVEFORMS = ("SIN", "Gauss", "DC")
    ROM_WAVEFORM_CODES = {
        "SIN": 0b00,
        "Gauss": 0b01,
        "DC": 0b10,
    }
    ROM_SLOW_OPTIONS = (
        "00 - Direct",
        "01 - 1 Step",
        "10 - 3 Steps",
        "11 - 7 Steps",
    )
    ROM_AMPLITUDE_OPTIONS = [f"{(index + 1) * 5.9375:.2f} V" for index in range(32)]
    DIFF_ROM_AMPLITUDE_OPTIONS = ROM_AMPLITUDE_OPTIONS
    LEGACY_NORMAL_AMPLITUDE_OPTIONS = [
        f"{min((index + 1) * 2.97, 95.0):.2f} V" for index in range(32)
    ]
    ROM_OFFSET_OPTIONS = ["0: 0.00 V"] + [
        f"{code}: {-0.7421875 * code:.2f} V" for code in range(1, 64)
    ] + [
        f"{code}: {0.7421875 * (128 - code):.2f} V" for code in range(64, 128)
    ]
    DIFF_ROM_OFFSET_OPTIONS = ["0: 0.00 V"] + [
        f"{code}: {-1.484375 * code:.2f} V" for code in range(1, 64)
    ] + [
        f"{code}: {1.484375 * (128 - code):.2f} V" for code in range(64, 128)
    ]
    ROM_REPEAT_OPTIONS = [str(count) for count in range(1, 257)]
    NORMAL_SOFTWARE_PRESETS: tuple[dict[str, Any], ...] = (
        {
            "waveform": "Gauss",
            "depth": "128",
            "point_time_us": "39",
            "repeat": "1",
            "offset_code": 2,
            "amplitude_code": 9,
        },
        {
            "waveform": "Gauss",
            "depth": "256",
            "point_time_us": "13",
            "repeat": "2",
            "offset_code": 2,
            "amplitude_code": 9,
        },
        {
            "waveform": "Gauss",
            "depth": "256",
            "point_time_us": "13",
            "repeat": "2",
            "offset_code": 2,
            "amplitude_code": 15,
        },
    )
    DIFF_SOFTWARE_PRESETS: tuple[dict[str, Any], ...] = (
        {
            "max_voltage": "60",
            "min_voltage": "10",
            "depth": "256",
            "time1_us": "10",
            "time2_us": "10",
            "time3_us": "19",
            "play_count": "1",
        },
        {
            "max_voltage": "60",
            "min_voltage": "10",
            "depth": "512",
            "time1_us": "3",
            "time2_us": "3",
            "time3_us": "7",
            "play_count": "1",
        },
        {
            "max_voltage": "80",
            "min_voltage": "20",
            "depth": "512",
            "time1_us": "3",
            "time2_us": "3",
            "time3_us": "7",
            "play_count": "1",
        },
    )
    STAGE_PRESS_THRESHOLD_REGS = {
        "Stage0": 0x0F,
        "Stage1": 0x14,
        "Stage2": 0x19,
        "Stage3": 0x1E,
    }
    STAGE_RELEASE_THRESHOLD_REGS = {
        "Stage0": 0x10,
        "Stage1": 0x15,
        "Stage2": 0x1A,
        "Stage3": 0x1F,
    }
    STAGE_WAVEFORM_REGS = {
        "Stage0": 0x12,
        "Stage1": 0x17,
        "Stage2": 0x1C,
        "Stage3": 0x21,
    }
    STAGE_JUMP_CONTROL_REGS = {
        "Stage0": 0x0E,
        "Stage1": 0x13,
        "Stage2": 0x18,
        "Stage3": 0x1D,
    }
    BOARD_PRESET_TOOLTIP_KEYS = {
        "save_to_board": "save_no_gui_settings_tooltip",
        "set_active": "set_boot_settings_tooltip",
        "apply_slot": "apply_current_settings_tooltip",
        "read_slot": "board_preset_read_tooltip",
    }
    PAGE_ACTION_TOOLTIP_KEYS = {
        "button_mode_import_user_preset": "button_mode_import_page_settings_tooltip",
        "button_mode_export_user_preset": "button_mode_export_page_settings_tooltip",
    }

    def __init__(
        self,
        parent: tk.Misc,
        ctx: Any,
        *,
        diff_mode: bool = False,
        import_page_settings: Callable[[], None] | None = None,
        export_page_settings: Callable[[], None] | None = None,
        sync_decoded_settings: Callable[
            [int, dict[int, int], dict[int, tuple[int, int, int, int]]],
            None,
        ] | None = None,
        gpio_group: GpioControlGroup | None = None,
        settings_store: ChipSettingsStore | None = None,
        board_slot_provider: Callable[[tuple[Any, ...], int | str | None], int] | None = None,
        on_board_slot_selected: Callable[[str, int | str], None] | None = None,
        on_gpio_mode_success: Callable[[str, str], None] | None = None,
        on_gpio_mode_failure: Callable[[str], None] | None = None,
        defer_build: bool = False,
    ) -> None:
        super().__init__(parent, ctx)
        self.diff_mode = diff_mode
        self.import_page_settings = import_page_settings or (lambda: None)
        self.export_page_settings = export_page_settings or (lambda: None)
        self.sync_decoded_settings = sync_decoded_settings or (
            lambda _address, _registers, _descriptors: None
        )
        self._gpio_group = gpio_group
        self._settings_store = settings_store or ChipSettingsStore()
        self._board_slot_provider = board_slot_provider
        self._on_board_slot_selected_callback = on_board_slot_selected
        self._on_gpio_mode_success = on_gpio_mode_success
        self._on_gpio_mode_failure = on_gpio_mode_failure
        self.button_mode_key = "awp1018_diff_mode" if diff_mode else "awp1018_normal_mode"
        self.channel_count = 4 if diff_mode else 8
        self.supports_live_adc_read = bool(diff_mode)
        self._draft_model = None
        self._draft_board_identity: tuple[Any, ...] | None = None
        self._draft_restoring = False
        self._initial_button_settings: dict[str, Any] | None = None
        self._initial_button_stage = "Stage0"

        self.stage_vars: dict[str, dict[str, tk.Variable]] = {}
        self.active_stage = tk.StringVar(value="Stage0")
        self.channel_vars = [tk.BooleanVar(value=False) for _index in range(self.channel_count)]
        self.channel_start_stage_vars = [
            tk.StringVar(value="Stage0") for _index in range(self.channel_count)
        ]
        self.stage_group_start_vars = {
            stage: [tk.BooleanVar(value=stage == "Stage0") for _index in range(len(self.channel_vars))]
            for stage in ("Stage0", "Stage1", "Stage2", "Stage3")
        }
        self.debounce_time_var = tk.StringVar(value="100")
        self.adc_sample_update_var = tk.StringVar(value=self.ADC_SAMPLE_UPDATE_OPTIONS[0])
        self.return_bit_press_enable_var = tk.BooleanVar(value=False)
        self.return_bit_release_enable_var = tk.BooleanVar(value=False)
        self.return_bit_release_time_enhance_var = tk.BooleanVar(value=False)
        self.return_bit_time_var = tk.StringVar(value=self.RETURN_BIT_TIME_OPTIONS[0])
        self.arbitration_mode_var = tk.StringVar(value="")
        self.arbitration_mode_code = 0b00
        self.zero_time_var = tk.StringVar(value="800 us")
        self.zps_enable_var = tk.BooleanVar(value=False)
        self.interrupt_enable_var = tk.BooleanVar(value=False)
        self.dac_sample_rate_var = tk.StringVar(value=str(self.ctx.state.single_point_duration_us))
        self.rom_waveform_var = tk.StringVar(value="SINC" if diff_mode else self.ROM_WAVEFORMS[0])
        self.rom_slice_var = tk.StringVar(value="slice11" if diff_mode else "slice0")
        self.rom_infinite_loop_var = tk.BooleanVar(value=False)
        self.rom_depth_var = tk.StringVar(value="1024")
        self.rom_point_time_var = tk.StringVar(value="1")
        self.rom_slowup_var = tk.StringVar(value=self.ROM_SLOW_OPTIONS[0])
        self.rom_slowdown_var = tk.StringVar(value=self.ROM_SLOW_OPTIONS[0])
        self.rom_repeat_var = tk.StringVar(value="1")
        self.rom_offset_var = tk.StringVar(value=self.ROM_OFFSET_OPTIONS[0])
        self.rom_amplitude_var = tk.StringVar(
            value=self.DIFF_ROM_AMPLITUDE_OPTIONS[0] if diff_mode else self.ROM_AMPLITUDE_OPTIONS[-1]
        )
        self.diff_sinc_max_voltage_var = tk.StringVar(value="80")
        self.diff_sinc_min_voltage_var = tk.StringVar(value="20")
        self.diff_sinc_time1_var = tk.StringVar(value="4")
        self.diff_sinc_time2_var = tk.StringVar(value="4")
        self.diff_sinc_time3_var = tk.StringVar(value="7")
        self.diff_play_count_var = tk.StringVar(value="1")
        self.diff_auto_serial_var = tk.BooleanVar(value=False)
        self.diff_serial_var = tk.StringVar(value="serial0")
        self.diff_sinc_slice_group_var = tk.StringVar(value="slice17-slice19")
        self.diff_single_waveform_var = tk.StringVar(value="DC")
        self.diff_serial4_segment_vars = [tk.StringVar(value="slice0")] + [
            tk.StringVar(value=self._serial4_end_option()) for _index in range(5)
        ]
        self._diff_slice_preview_cache: dict[int, dict[str, Any]] = self.ctx.state.extra.setdefault(
            "awp1018_diff_slice_preview_cache",
            {},
        )
        self._slice_definition_cache_root: dict[str, dict[int, tuple[int, int, int, int]]] = (
            self.ctx.state.extra.setdefault("awp1018_slice_definitions", {})
        )
        self._pending_import_slice_definitions: dict[int, dict[str, Any]] = {}
        self.rom_metric_var = tk.StringVar(value="")
        self.rom_frequency_var = tk.StringVar(value="")

        self._labels: dict[str, ttk.Label] = {}
        self._selector_widgets: dict[str, list[tk.Widget]] = {}
        self._buttons: dict[str, ttk.Button] = {}
        self._checks: dict[str, ttk.Checkbutton] = {}
        self._stage_buttons: dict[str, ttk.Button] = {}
        self._stage_frames: dict[str, ttk.Frame] = {}
        self._stage_entries: dict[str, dict[str, ttk.Entry]] = {}
        self._stage_debounce_checks: dict[str, dict[str, ttk.Checkbutton]] = {}
        self._stage_group_checks: dict[str, dict[int, ttk.Checkbutton]] = {}
        self._channel_checks: dict[int, ttk.Checkbutton] = {}
        self._channel_start_combos: dict[int, ttk.Combobox] = {}
        self._channel_mapping_labels: dict[int, ttk.Label] = {}
        self._diff_status_titles: dict[int, tk.Label] = {}
        self._diff_status_frames: dict[int, tk.Frame] = {}
        self._diff_status_value_vars: dict[int, tk.StringVar] = {}
        self._tooltips: list[SimpleTooltip] = []
        self._loading_settings = defer_build
        self._diff_detect_active = False
        self._diff_sample_active = False
        self._diff_value_poll_job: str | None = None
        self._diff_status_blink_job: str | None = None
        self._diff_sample_cancel: threading.Event | None = None
        self._diff_sample_thread: threading.Thread | None = None
        self._diff_sample_channels_lock = threading.Lock()
        self._diff_sample_channels: tuple[int, ...] = ()
        self._diff_sample_generation = 0
        self._diff_status_result_queue: queue.Queue[tuple[int, str, Any]] = queue.Queue(maxsize=1)
        self.board_slot_var = tk.StringVar(value="0")
        self.board_slot_combo: ttk.Combobox | None = None
        self._board_preset_buttons: dict[str, ttk.Button] = {}
        self.preset_toolbar: PresetToolbar | None = None
        self._board_preset_result_queue: queue.Queue[tuple[str, str, Any]] = queue.Queue()
        self._board_preset_poll_active = False
        self._rom_draw_job: str | None = None
        self._rom_drawing = False
        self._rom_painted_size: tuple[int, int] | None = None
        self._rom_draw_retries = 0
        self._sinc_segment_cache: tuple[Any, list] | None = None

        self._preparing = defer_build
        self._build_steps = self._initialize_steps()
        if not defer_build:
            for _ in self._build_steps:
                pass

    def _initialize_steps(self) -> Iterator[None]:
        yield from self._build_ui_steps()
        self._preparing = False
        self.ctx.serial.add_log_listener(self._handle_serial_status_log)
        self._loading_settings = False

    def _trace_stage_threshold(self, stage: str, kind: str, variable: tk.StringVar) -> None:
        variable.trace_add("write", lambda *_args, s=stage, k=kind: self._on_stage_threshold_changed(s, k))

    def save_button_draft(self) -> None:
        if self.page_name == "full_mode" or self._draft_model is None:
            return
        if self._draft_model.device_key != self._settings_store.current_device_key:
            return
        try:
            self._draft_model.update_shared(
                {
                    "page_settings": self.export_settings(),
                    "active_stage": str(self.active_stage.get()),
                }
            )
            if self.preset_toolbar is not None:
                self._draft_model.set_slot_selection(self.preset_toolbar.selected_slot)
        except (tk.TclError, ValueError, TypeError):
            return

    def activate_button_draft(self) -> None:
        if self.page_name == "full_mode" or self._draft_restoring:
            return
        if self._initial_button_settings is None:
            self._initial_button_settings = self.export_settings()
            self._initial_button_stage = str(self.active_stage.get())
        board_identity = self._board_identity()
        connection = ("awp1018_button", board_identity, self.button_mode_key)
        model = self._settings_store.select_device(connection, self._device_addr())
        self._draft_model = model
        self._draft_board_identity = board_identity
        saved_settings = model.shared.get("page_settings")
        self._draft_restoring = True
        try:
            if isinstance(saved_settings, dict):
                self.import_settings(saved_settings, apply_to_chip=False)
                active_stage = str(model.shared.get("active_stage", "Stage0"))
                if active_stage in self._stage_frames:
                    self._show_stage(active_stage)
            else:
                initial_settings = deepcopy(self._initial_button_settings)
                initial_settings["slice_definitions"] = self._export_slice_definitions()
                model.update_shared(
                    {
                        "page_settings": initial_settings,
                        "active_stage": self._initial_button_stage,
                    }
                )
                self.import_settings(initial_settings, apply_to_chip=False)
            selected_slot = (
                self._board_slot_provider(board_identity, model.slot_selection)
                if callable(self._board_slot_provider)
                else (int(model.slot_selection) if model.slot_selection is not None else 0)
            )
            selected_slot = max(0, min(2, int(selected_slot)))
            model.set_slot_selection(selected_slot)
            if self.preset_toolbar is not None:
                self.preset_toolbar.slot_var.set(str(selected_slot))
            if callable(self._on_board_slot_selected_callback):
                self._on_board_slot_selected_callback(self.button_mode_key, selected_slot)
        except (tk.TclError, ValueError, TypeError, KeyError):
            return
        finally:
            self._draft_restoring = False

    def _build_preset_header(self, parent: tk.Misc) -> ttk.Frame:
        title_key = self.button_mode_key
        header = self.make_title(parent, title_key, show_logo=False)
        header.columnconfigure(0, weight=0)
        header.columnconfigure(1, weight=1)

        preset_frame = self.make_preset_frame(header)
        board_identity = self._board_identity()
        selected_slot = (
            self._board_slot_provider(board_identity, None)
            if callable(self._board_slot_provider)
            else 0
        )
        self.preset_toolbar = PresetToolbar(
            preset_frame,
            translate=lambda key: self.tr(key),
            callbacks={
                "software_preset_1": lambda: self.apply_software_preset(0),
                "software_preset_2": lambda: self.apply_software_preset(1),
                "software_preset_3": lambda: self.apply_software_preset(2),
                "import_page_settings": self.import_page_settings,
                "export_page_settings": self.export_page_settings,
                "save_slot": self.save_board_preset,
                "set_active_slot": self.set_board_preset_active,
                "apply_slot": self.apply_board_preset,
                "read_and_apply": self.read_board_preset,
            },
            capabilities={
                "software_preset_1": True,
                "software_preset_2": True,
                "software_preset_3": True,
                "import_page_settings": True,
                "export_page_settings": True,
                "slot_selection": True,
                "save_slot": True,
                "set_active_slot": True,
                "apply_slot": True,
                "read_and_apply": True,
            },
            tooltip_keys={
                "import_page_settings": "button_mode_import_page_settings_tooltip",
                "export_page_settings": "button_mode_export_page_settings_tooltip",
                "save_slot": "save_no_gui_settings_tooltip",
                "set_active_slot": "set_boot_settings_tooltip",
                "apply_slot": "apply_current_settings_tooltip",
                "read_and_apply": "board_preset_read_tooltip",
            },
            slot_values=(0, 1, 2),
            selected_slot=selected_slot,
            on_slot_selected=lambda slot: self._on_board_slot_selected(slot),
        )
        self.preset_toolbar.grid(row=0, column=0, sticky="ew")
        self.board_slot_var = self.preset_toolbar.slot_var
        self.board_slot_combo = self.preset_toolbar.slot_combo
        self._labels["board_slot"] = self.preset_toolbar.slot_label
        for index in range(3):
            key = f"button_mode_preset_{index + 1}"
            button = self.preset_toolbar.buttons[f"software_preset_{index + 1}"]
            self._buttons[key] = button
            self._bind_software_preset_tooltip(button, index)
        self._buttons["button_mode_import_user_preset"] = self.preset_toolbar.buttons[
            "import_page_settings"
        ]
        self._buttons["button_mode_export_user_preset"] = self.preset_toolbar.buttons[
            "export_page_settings"
        ]
        self._board_preset_buttons = {
            "save_to_board": self.preset_toolbar.buttons["save_slot"],
            "set_active": self.preset_toolbar.buttons["set_active_slot"],
            "apply_slot": self.preset_toolbar.buttons["apply_slot"],
            "read_slot": self.preset_toolbar.buttons["read_and_apply"],
        }

        return header

    def _board_identity(self) -> tuple[Any, ...]:
        identity = active_serial_identity(self.ctx, "awp1018")
        if identity is not None:
            return tuple(identity)
        selected_port = getattr(self.ctx.state, "selected_port", None)
        selector = getattr(self.ctx.serial, "selected_port_for_page", None)
        if callable(selector):
            try:
                selected_port = selector("awp1018")
            except TypeError:
                selected_port = selector()
        return ("offline", str(selected_port or ""))

    def _on_board_slot_selected(self, slot: int | str) -> None:
        if callable(self._on_board_slot_selected_callback):
            self._on_board_slot_selected_callback(self.button_mode_key, slot)
        if self._draft_model is not None:
            try:
                self._draft_model.set_slot_selection(int(slot))
            except (TypeError, ValueError):
                pass

    def _attach_gpio_controls(self, parent: tk.Misc, row: int, *, columnspan: int = 4) -> int:
        group = getattr(self, "_gpio_group", None)
        if group is None:
            return row
        panel = GpioControlPanel(
            parent,
            self.ctx,
            tr=self.tr,
            gpio_indexes=(0, 1, 2, 3),
            on_mode_success=self._on_gpio_mode_success,
            on_mode_failure=self._on_gpio_mode_failure,
        )
        panel.grid(row=row, column=0, columnspan=columnspan, sticky="ew", pady=(12, 0))
        self._register_module_spacing_widget(panel, before=True, after=False)
        group.add(panel)
        return row + 1

    def _build_ui(self) -> None:
        for _ in self._build_ui_steps():
            pass

    def _build_ui_steps(self) -> Iterator[None]:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = self._build_preset_header(self)
        header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())

        body = ttk.Frame(self, style="App.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        yield
        body.columnconfigure(0, weight=1)
        row = 0
        yield from self._build_button_channel_selector_steps(body, row)
        row += 1

        waveform_panel = ttk.Frame(body, style="Panel.TFrame", padding=12)
        waveform_panel.grid(
            row=row,
            column=0,
            sticky="ew",
            pady=(0, self._module_spacing()),
        )
        waveform_panel.columnconfigure(0, weight=1)
        yield from self._build_rom_selector_steps(waveform_panel)
        row += 1

        self._adc_split = ttk.Frame(body, style="App.TFrame")
        self._adc_split.grid(
            row=row,
            column=0,
            sticky="ew",
            pady=(0, self._module_spacing()),
        )
        self._adc_split.columnconfigure(1, weight=1)
        self._adc_legend_panel = ttk.Frame(self._adc_split, style="Panel.TFrame", padding=12)
        self._adc_editor_panel = ttk.Frame(self._adc_split, style="Panel.TFrame", padding=14)
        self._adc_legend_panel.columnconfigure(0, weight=1)
        self._adc_editor_panel.columnconfigure(1, weight=1)
        yield
        yield from self._build_button_adc_legend_steps(self._adc_legend_panel)
        yield from self._build_button_adc_editor_steps(self._adc_editor_panel)
        self._adc_split.bind("<Configure>", self._queue_adc_split_layout, add="+")
        self._queue_adc_split_layout()
        row += 1

        status_panel = ttk.Frame(body, style="Panel.TFrame", padding=12)
        status_panel.grid(
            row=row,
            column=0,
            sticky="ew",
            pady=(0, self._module_spacing()),
        )
        status_panel.columnconfigure(0, weight=1)
        yield from self._build_button_readings_status_steps(status_panel)
        row += 1

        controls_panel = ttk.Frame(body, style="Panel.TFrame", padding=12)
        controls_panel.grid(
            row=row,
            column=0,
            sticky="ew",
            pady=(0, self._module_spacing()),
        )
        controls_panel.columnconfigure(0, weight=1)
        self.make_module_title_row(
            controls_panel,
            "awp1018_function_controls",
            step=4,
            step_total=4,
            help_key="awp1018_help_function_control",
            row=0,
            columnspan=4,
            pady=(0, 6),
        )
        yield from self._build_control_buttons_steps(controls_panel, 1)
        self._attach_gpio_controls(controls_panel, 2)
        row += 1

        log_frame = self.make_serial_log(body, height=7, subscribe=True)
        log_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        self._register_module_spacing_widget(log_frame, before=True, after=False)
        yield

        self.rom_waveform_var.trace_add("write", self._queue_rom_waveform_draw)
        self.rom_waveform_var.trace_add("write", lambda *_: self._refresh_rom_depth_options())
        for var in (
            self.rom_depth_var,
            self.rom_point_time_var,
            self.rom_amplitude_var,
            self.rom_offset_var,
            self.diff_sinc_max_voltage_var,
            self.diff_sinc_min_voltage_var,
            self.diff_sinc_time1_var,
            self.diff_sinc_time2_var,
            self.diff_sinc_time3_var,
            self.diff_play_count_var,
            self.diff_serial_var,
            self.diff_sinc_slice_group_var,
            self.diff_single_waveform_var,
            *self.diff_serial4_segment_vars,
        ):
            var.trace_add("write", self._queue_rom_waveform_draw)
        yield
        if self.diff_mode:
            self.diff_serial_var.trace_add("write", lambda *_: self._refresh_diff_slice_group_options())
            self.diff_serial_var.trace_add("write", lambda *_: self._refresh_diff_waveform_controls())
            self.rom_waveform_var.trace_add("write", lambda *_: self._refresh_diff_waveform_controls())
            self.rom_slice_var.trace_add("write", lambda *_: self._refresh_diff_waveform_controls())
        self.zero_time_var.trace_add("write", lambda *_: self._on_zero_time_changed())
        self.debounce_time_var.trace_add("write", lambda *_: self._on_debounce_time_changed())
        self.adc_sample_update_var.trace_add("write", lambda *_: self._on_adc_sample_update_changed())
        self.return_bit_time_var.trace_add("write", lambda *_: self._on_return_bit_config_changed())
        self.dac_sample_rate_var.trace_add("write", lambda *_: self._on_dac_sample_rate_changed())
        yield
        self.rom_canvas.bind("<Configure>", self._on_rom_canvas_configure)

        self.refresh_language()
        self._show_stage("Stage0")
        self._queue_rom_waveform_draw()

    def _build_stage_selector(self, parent: tk.Misc, row: int) -> None:
        for _ in self._build_stage_selector_steps(parent, row):
            pass

    def _build_stage_selector_steps(self, parent: tk.Misc, row: int) -> Iterator[None]:
        yield
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 6))
        for index, stage in enumerate(("Stage0", "Stage1", "Stage2", "Stage3")):
            frame.columnconfigure(index, weight=1)
            btn = ttk.Button(
                frame,
                text=self.tr("awp1018_stage_label", index=index),
                style="Secondary.TButton",
                command=lambda s=stage: self._show_stage(s),
            )
            btn.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 4, 0))
            self._stage_buttons[stage] = btn

    def _build_adc_channel_selector(self, parent: tk.Misc, row: int) -> None:
        for _ in self._build_adc_channel_selector_steps(parent, row):
            pass

    def _build_adc_channel_selector_steps(self, parent: tk.Misc, row: int) -> Iterator[None]:
        if self.page_name != "full_mode":
            yield from self._build_button_channel_selector_steps(parent, row)
            return
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(4, 8))
        title_row, _title = self.make_module_title_row(
            section,
            "awp1018_channel_select",
            step=1,
            step_total=4,
            help_key="awp1018_help_full_channel",
        )
        title_row.grid_configure(columnspan=4, pady=(0, 4))
        frame = ttk.Frame(section, style="Panel.TFrame")
        frame.grid(row=1, column=0, columnspan=4, sticky="ew")
        for index, variable in enumerate(self.channel_vars):
            frame.columnconfigure(index % 4, weight=1)
            check = ttk.Checkbutton(
                frame,
                text=self.tr("awp1018_channel_label", index=index),
                variable=variable,
                command=self._on_channel_select_changed,
            )
            check.grid(row=index // 4, column=index % 4, sticky="w", padx=(0, 8), pady=2)
            self._channel_checks[index] = check
        yield

    def _build_button_channel_selector_steps(
        self,
        parent: tk.Misc,
        row: int,
    ) -> Iterator[None]:
        section = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        section.grid(row=row, column=0, sticky="ew", pady=(0, self._module_spacing()))
        section.columnconfigure(0, weight=1)
        title_row, _title = self.make_module_title_row(
            section,
            "awp1018_channel_select",
            step=1,
            step_total=4,
            help_key="awp1018_help_full_channel",
            row=0,
            columnspan=4,
            pady=(0, 6),
        )
        title_row.grid_configure(sticky="ew")
        card_grid = ResponsiveCardGrid(
            section,
            item_count=self.channel_count,
            max_columns=4,
            min_card_width=174,
            gap=8,
        )
        card_grid.grid(row=1, column=0, columnspan=4, sticky="ew")
        self._channel_card_grid = card_grid
        stage_values = [f"Stage{index}" for index in range(4)]
        for index, variable in enumerate(self.channel_vars):
            card = ttk.LabelFrame(card_grid, padding=(8, 5))
            card.columnconfigure(0, weight=1)
            check = ttk.Checkbutton(
                card,
                text=self.tr("awp1018_channel_label", index=index),
                variable=variable,
                command=self._on_channel_select_changed,
            )
            check.grid(row=0, column=0, sticky="w", pady=(0, 3))
            self._channel_checks[index] = check

            mapping_label = ttk.Label(card, style="Panel.TLabel")
            mapping_label.grid(row=1, column=0, sticky="w", pady=(2, 1))
            self._channel_mapping_labels[index] = mapping_label
            start_combo = ttk.Combobox(
                card,
                textvariable=self.channel_start_stage_vars[index],
                values=stage_values,
                state="readonly",
                width=9,
            )
            start_combo.grid(row=2, column=0, sticky="ew")
            start_combo.bind(
                "<<ComboboxSelected>>",
                lambda _event, channel=index: self._on_channel_start_stage_changed(channel),
            )
            self._channel_start_combos[index] = start_combo
            card_grid.add_card(card)
        yield

    def _build_button_adc_legend_steps(self, parent: tk.Misc) -> Iterator[None]:
        title_row, _title = self.make_module_title_row(
            parent,
            "awp1221_adc_diagram",
            step=3,
            step_total=4,
            role="reference",
            help_key="awp1221_help_adc_legend",
            row=0,
            columnspan=2,
            pady=(0, 6),
        )
        title_row.grid_configure(sticky="ew")
        hint = ttk.Label(parent, style="Muted.TLabel", wraplength=240, justify="left")
        hint.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._labels["awp1221_adc_diagram_hint"] = hint
        yield
        canvas = tk.Canvas(
            parent,
            height=148,
            background=self.colors["canvas_bg"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            borderwidth=0,
        )
        canvas.grid(row=2, column=0, columnspan=2, sticky="ew")
        parent.columnconfigure(0, weight=1)
        self._adc_legend_canvas = canvas
        canvas.bind("<Configure>", self._draw_adc_threshold_legend, add="+")

        self._adc_legend_swatches: dict[str, tk.Label] = {}
        for row, key, color in (
            (3, "awp1221_diagram_press", self.colors["primary"]),
            (4, "awp1221_diagram_release", "#7c3aed"),
        ):
            swatch = tk.Label(parent, width=2, background=color, borderwidth=0)
            swatch.grid(row=row, column=0, sticky="w", pady=(5, 0))
            self._adc_legend_swatches[key] = swatch
            label = ttk.Label(parent, style="Panel.TLabel")
            label.grid(row=row, column=1, sticky="w", padx=(7, 0), pady=(5, 0))
            self._labels[key] = label
        self._draw_adc_threshold_legend()

    def _draw_adc_threshold_legend(self, _event: tk.Event | None = None) -> None:
        canvas = getattr(self, "_adc_legend_canvas", None)
        if canvas is None:
            return
        try:
            width = max(180, int(canvas.winfo_width()))
            height = max(110, int(canvas.winfo_height()))
            canvas.delete("all")
            left, right = 24, width - 16
            top, bottom = 16, height - 22
            canvas.create_line(left, top, left, bottom, fill=self.colors["border"])
            canvas.create_line(
                left,
                bottom,
                right,
                bottom,
                fill=self.colors["border"],
                arrow=tk.LAST,
            )
            press_y = top + (bottom - top) * 0.36
            release_y = top + (bottom - top) * 0.68
            canvas.create_line(
                left + 2,
                press_y,
                right,
                press_y,
                fill=self.colors["primary"],
                width=2,
                dash=(5, 3),
            )
            canvas.create_line(
                left + 2,
                release_y,
                right,
                release_y,
                fill="#7c3aed",
                width=2,
                dash=(5, 3),
            )
            canvas.create_text(
                left + 8,
                press_y - 8,
                anchor="sw",
                text="P",
                fill=self.colors["text"],
            )
            canvas.create_text(
                left + 8,
                release_y - 8,
                anchor="sw",
                text="R",
                fill=self.colors["text"],
            )
        except tk.TclError:
            return

    def _build_button_adc_editor_steps(self, parent: tk.Misc) -> Iterator[None]:
        for column in (0, 2, 4):
            parent.columnconfigure(column, weight=0, minsize=92)
        for column in (1, 3, 5):
            parent.columnconfigure(column, weight=1, minsize=110)
        self.make_module_title_row(
            parent,
            "awp1018_adc_threshold_detection",
            step=3,
            step_total=4,
            help_key="awp1018_help_button_detection",
            row=0,
            columnspan=6,
            pady=(0, 6),
        )
        yield
        yield from self._build_stage_selector_steps(parent, 1)
        self.stage_container = ttk.Frame(parent, style="Panel.TFrame")
        self.stage_container.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(2, 7))
        self.stage_container.columnconfigure(0, weight=1)
        for stage in ("Stage0", "Stage1", "Stage2", "Stage3"):
            yield from self._build_stage_frame_steps(stage)

        yield from self._build_adc_timing_row_steps(parent, 3)
        yield from self._build_zero_return_bit_row_steps(parent, 4)
        yield from self._build_arbitration_return_time_row_steps(parent, 5)
        zps_check = ttk.Checkbutton(
            parent,
            variable=self.zps_enable_var,
            command=self._on_zps_enable_changed,
        )
        zps_check.grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 4))
        self._checks["awp1018_zps_enable"] = zps_check
        yield
        interrupt_check = ttk.Checkbutton(
            parent,
            variable=self.interrupt_enable_var,
            command=self._on_interrupt_enable_changed,
        )
        interrupt_check.grid(row=6, column=3, columnspan=3, sticky="w", pady=(8, 4))
        self._checks["awp1018_interrupt_enable"] = interrupt_check

    def _queue_adc_split_layout(self, _event: tk.Event | None = None) -> None:
        if getattr(self, "_adc_split_layout_job", None) is not None:
            return
        try:
            self._adc_split_layout_job = self.after_idle(self._reflow_adc_split)
        except tk.TclError:
            self._adc_split_layout_job = None

    def _reflow_adc_split(self) -> None:
        self._adc_split_layout_job = None
        split = getattr(self, "_adc_split", None)
        legend = getattr(self, "_adc_legend_panel", None)
        editor = getattr(self, "_adc_editor_panel", None)
        if split is None or legend is None or editor is None:
            return
        try:
            width = int(split.winfo_width())
            if width <= 1:
                return
            side_by_side = width >= legend.winfo_reqwidth() + editor.winfo_reqwidth() + 24
            if side_by_side:
                legend.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
                editor.grid(row=0, column=1, sticky="nsew")
                split.columnconfigure(0, weight=0)
                split.columnconfigure(1, weight=1)
            else:
                legend.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
                editor.grid(row=1, column=0, columnspan=2, sticky="ew")
                split.columnconfigure(0, weight=1)
                split.columnconfigure(1, weight=0)
        except tk.TclError:
            return

    def _add_help_button(self, parent: tk.Misc, row: int, column: int, text_key: str) -> None:
        label = ttk.Label(parent, text=self.tr("awp1018_help_symbol"), style="Muted.TLabel", cursor="question_arrow")
        label.grid(row=row, column=column, sticky="w", padx=(4, 0))
        self._tooltips.append(SimpleTooltip(label, lambda key=text_key: self.tr(key)))

    def _build_adc_timing_row(self, parent: tk.Misc, row: int) -> None:
        for _ in self._build_adc_timing_row_steps(parent, row):
            pass

    def _build_adc_timing_row_steps(self, parent: tk.Misc, row: int) -> Iterator[None]:
        debounce_frame = ttk.Frame(parent, style="Panel.TFrame")
        debounce_frame.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        debounce_label = ttk.Label(debounce_frame, style="Panel.TLabel")
        debounce_label.grid(row=0, column=0, sticky="w")
        self._labels["awp1018_debounce"] = debounce_label
        self._add_help_button(debounce_frame, 0, 1, "awp1018_debounce_time_help")
        yield
        ttk.Entry(parent, textvariable=self.debounce_time_var, width=10).grid(row=row, column=1, sticky="ew", pady=7)

        sample_frame = ttk.Frame(parent, style="Panel.TFrame")
        sample_frame.grid(row=row, column=2, sticky="w", pady=7, padx=(12, 8))
        sample_label = ttk.Label(sample_frame, style="Panel.TLabel")
        sample_label.grid(row=0, column=0, sticky="w")
        self._labels["awp1018_adc_sample_update_time"] = sample_label
        yield
        self._add_help_button(sample_frame, 0, 1, "awp1018_adc_sample_update_time_help")
        ttk.Combobox(
            parent,
            textvariable=self.adc_sample_update_var,
            values=list(self.ADC_SAMPLE_UPDATE_OPTIONS),
            state="readonly",
            width=10,
        ).grid(row=row, column=3, sticky="ew", pady=7)

    def _build_generator_dac_rate(self, parent: tk.Misc) -> None:
        for _ in self._build_generator_dac_rate_steps(parent):
            pass

    def _build_generator_dac_rate_steps(self, parent: tk.Misc) -> Iterator[None]:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=(14, 10))
        frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._register_module_spacing_widget(frame, before=True, after=False)
        frame.columnconfigure(1, weight=1)

        label = ttk.Label(frame, style="Panel.TLabel")
        label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._labels["awp1018_dac_sample_rate"] = label
        yield
        ttk.Entry(frame, textvariable=self.dac_sample_rate_var, width=14).grid(row=0, column=1, sticky="w")

    def _build_zero_return_bit_row(self, parent: tk.Misc, row: int) -> None:
        for _ in self._build_zero_return_bit_row_steps(parent, row):
            pass

    def _build_zero_return_bit_row_steps(self, parent: tk.Misc, row: int) -> Iterator[None]:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels["awp1018_zero_time"] = label

        ttk.Combobox(
            parent,
            textvariable=self.zero_time_var,
            values=list(self.ZERO_TIME_OPTIONS),
            state="readonly",
            width=8,
        ).grid(row=row, column=1, sticky="w", pady=7)

        return_frame = ttk.Frame(parent, style="Panel.TFrame")
        return_frame.grid(row=row, column=2, columnspan=2, sticky="ew", pady=7, padx=(12, 0))
        yield
        return_frame.columnconfigure(0, weight=1)
        return_frame.columnconfigure(1, weight=1)

        press_return_check = ttk.Checkbutton(
            return_frame,
            variable=self.return_bit_press_enable_var,
            command=self._on_return_bit_config_changed,
        )
        press_return_check.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        self._checks["awp1018_press_return_bit_enable"] = press_return_check

        release_return_frame = ttk.Frame(return_frame, style="Panel.TFrame")
        yield
        release_return_frame.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=2)
        release_return_check = ttk.Checkbutton(
            release_return_frame,
            variable=self.return_bit_release_enable_var,
            command=self._on_return_bit_config_changed,
        )
        release_return_check.grid(row=0, column=0, sticky="w")
        self._add_help_button(release_return_frame, 0, 1, "awp1018_release_return_bit_enable_help")
        self._checks["awp1018_release_return_bit_enable"] = release_return_check

        enhance_frame = ttk.Frame(return_frame, style="Panel.TFrame")
        yield
        enhance_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        release_time_enhance_check = ttk.Checkbutton(
            enhance_frame,
            variable=self.return_bit_release_time_enhance_var,
            command=self._on_return_bit_config_changed,
        )
        release_time_enhance_check.grid(row=0, column=0, sticky="w")
        self._add_help_button(enhance_frame, 0, 1, "awp1018_release_return_bit_time_enhance_help")
        self._checks["awp1018_release_return_bit_time_enhance"] = release_time_enhance_check

    def _build_arbitration_return_time_row(self, parent: tk.Misc, row: int) -> None:
        for _ in self._build_arbitration_return_time_row_steps(parent, row):
            pass

    def _build_arbitration_return_time_row_steps(self, parent: tk.Misc, row: int) -> Iterator[None]:
        arbitration_label = ttk.Label(parent, style="Panel.TLabel")
        arbitration_label.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels["awp1018_arbitration_mode"] = arbitration_label

        self.arbitration_mode_combo = ttk.Combobox(
            parent,
            textvariable=self.arbitration_mode_var,
            state="readonly",
            width=16,
        )
        self.arbitration_mode_combo.grid(row=row, column=1, sticky="ew", pady=7)
        self.arbitration_mode_combo.bind("<<ComboboxSelected>>", self._on_arbitration_mode_changed)
        yield

        return_time_frame = ttk.Frame(parent, style="Panel.TFrame")
        return_time_frame.grid(row=row, column=2, sticky="w", pady=7, padx=(12, 8))
        return_time_label = ttk.Label(return_time_frame, style="Panel.TLabel")
        return_time_label.grid(row=0, column=0, sticky="w")
        self._labels["awp1018_return_bit_time"] = return_time_label
        self._add_help_button(return_time_frame, 0, 1, "awp1018_return_bit_time_help")
        yield

        ttk.Combobox(
            parent,
            textvariable=self.return_bit_time_var,
            values=list(self.RETURN_BIT_TIME_OPTIONS),
            state="readonly",
            width=10,
        ).grid(row=row, column=3, sticky="ew", pady=7)


    def _build_stage_frame(self, stage: str) -> None:
        for _ in self._build_stage_frame_steps(stage):
            pass

    def _build_stage_frame_steps(self, stage: str) -> Iterator[None]:
        frame = ttk.Frame(self.stage_container, style="Panel.TFrame")
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=0, minsize=92)
        frame.columnconfigure(1, weight=1, minsize=120)
        frame.columnconfigure(2, weight=0, minsize=92)
        frame.columnconfigure(3, weight=1, minsize=120)
        yield
        frame.columnconfigure(5, weight=2, minsize=220)
        frame.grid_remove()

        press_debounce_enabled_var = tk.BooleanVar(value=False)
        release_debounce_enabled_var = tk.BooleanVar(value=False)
        ovv_time_var = tk.StringVar(value="1 ms")
        high_var = tk.StringVar(value="500" if stage == "Stage0" else "0")
        yield
        low_var = tk.StringVar(value="200" if stage == "Stage1" else "0")
        waveform_default = "slice0"
        if self.diff_mode and stage == "Stage0":
            waveform_default = "slice17"
        elif self.diff_mode and stage == "Stage1":
            waveform_default = "slice16"
        waveform_var = tk.StringVar(value=waveform_default)
        jump_condition_var = tk.StringVar(value="release" if stage == "Stage1" else "press")
        if stage == "Stage0":
            jump_target_default = "Stage1"
        elif stage == "Stage1":
            jump_target_default = "Stage0"
        else:
            jump_target_default = stage
        yield
        jump_target_var = tk.StringVar(value=jump_target_default)
        self.stage_vars[stage] = {
            "press_debounce_enabled": press_debounce_enabled_var,
            "release_debounce_enabled": release_debounce_enabled_var,
            "ovv_time": ovv_time_var,
            "high": high_var,
            "low": low_var,
            "waveform": waveform_var,
            "jump_condition": jump_condition_var,
            "jump_target": jump_target_var,
        }
        self._trace_stage_threshold(stage, "press", high_var)
        self._trace_stage_threshold(stage, "release", low_var)

        ovv_label = ttk.Label(frame, style="Panel.TLabel")
        ovv_label.grid(row=0, column=0, sticky="w", pady=5, padx=(0, 8))
        yield
        self._labels[f"{stage}_ovv_time"] = ovv_label
        ttk.Combobox(
            frame,
            textvariable=ovv_time_var,
            values=self.ADC_OVV_OPTIONS,
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky="ew", pady=5)

        jump_target_label = ttk.Label(frame, style="Panel.TLabel")
        jump_target_label.grid(row=0, column=2, sticky="w", pady=5, padx=(12, 8))
        self._labels[f"{stage}_jump_target"] = jump_target_label

        ttk.Combobox(
            frame,
            textvariable=jump_target_var,
            values=["Stage0", "Stage1", "Stage2", "Stage3"],
            state="readonly",
            width=10,
        ).grid(row=0, column=3, sticky="ew", pady=5)
        yield

        high_label = ttk.Label(frame, style="Panel.TLabel")
        high_label.grid(row=1, column=0, sticky="w", pady=5, padx=(0, 8))
        self._labels[f"{stage}_high"] = high_label
        high_entry = ttk.Entry(frame, textvariable=high_var, width=10)
        high_entry.grid(row=1, column=1, sticky="ew", pady=5)

        low_label = ttk.Label(frame, style="Panel.TLabel")
        yield
        low_label.grid(row=1, column=2, sticky="w", pady=5, padx=(12, 8))
        self._labels[f"{stage}_low"] = low_label
        low_entry = ttk.Entry(frame, textvariable=low_var, width=10)
        low_entry.grid(row=1, column=3, sticky="ew", pady=5)
        self._stage_entries[stage] = {"press": high_entry, "release": low_entry}

        waveform_label = ttk.Label(frame, style="Panel.TLabel")
        yield
        waveform_label.grid(row=2, column=0, sticky="w", pady=5, padx=(0, 8))
        self._labels[f"{stage}_waveform"] = waveform_label
        ttk.Combobox(
            frame,
            textvariable=waveform_var,
            values=self._slice_options(),
            state="readonly",
            width=14,
        ).grid(row=2, column=1, sticky="ew", pady=5)

        jump_condition_label = ttk.Label(frame, style="Panel.TLabel")
        jump_condition_label.grid(row=2, column=2, sticky="w", pady=5, padx=(12, 8))
        self._labels[f"{stage}_jump_condition"] = jump_condition_label
        yield

        jump_condition_frame = ttk.Frame(frame, style="Panel.TFrame")
        jump_condition_frame.grid(row=2, column=3, columnspan=3, sticky="ew", pady=5)
        jump_condition_frame.columnconfigure(0, weight=1, minsize=100)
        jump_condition_frame.columnconfigure(1, weight=1, minsize=110)
        press_radio = ttk.Radiobutton(
            jump_condition_frame,
            variable=jump_condition_var,
            value="press",
            style="Check.TRadiobutton",
            command=lambda s=stage: self._on_stage_jump_changed(s),
        )
        press_radio.grid(row=0, column=0, sticky="w")
        yield
        self._checks[f"{stage}_jump_press"] = press_radio

        release_radio = ttk.Radiobutton(
            jump_condition_frame,
            variable=jump_condition_var,
            value="release",
            style="Check.TRadiobutton",
            command=lambda s=stage: self._on_stage_jump_changed(s),
        )
        release_radio.grid(row=0, column=1, sticky="w", padx=(14, 0))
        self._checks[f"{stage}_jump_release"] = release_radio

        press_debounce_check = ttk.Checkbutton(
            frame,
            variable=press_debounce_enabled_var,
            command=lambda s=stage: self._on_stage_debounce_enable_changed(s, "press"),
        )
        press_debounce_check.grid(row=3, column=0, columnspan=3, sticky="w", pady=5, padx=(0, 8))
        yield
        self._checks[f"{stage}_press_debounce_enable"] = press_debounce_check

        release_debounce_check = ttk.Checkbutton(
            frame,
            variable=release_debounce_enabled_var,
            command=lambda s=stage: self._on_stage_debounce_enable_changed(s, "release"),
        )
        release_debounce_check.grid(row=3, column=3, columnspan=3, sticky="w", pady=5, padx=(12, 0))
        self._checks[f"{stage}_release_debounce_enable"] = release_debounce_check
        self._stage_debounce_checks[stage] = {"press": press_debounce_check, "release": release_debounce_check}

        if self.page_name == "full_mode":
            group_label = ttk.Label(frame, style="Panel.TLabel")
            yield
            group_label.grid(row=4, column=0, sticky="nw", pady=5, padx=(0, 8))
            self._labels[f"{stage}_group_start"] = group_label

            group_frame = ttk.Frame(frame, style="Panel.TFrame")
            group_frame.grid(row=4, column=1, columnspan=5, sticky="ew", pady=5)
            self._stage_group_checks[stage] = {}
            for group, variable in enumerate(self.stage_group_start_vars[stage]):
                group_frame.columnconfigure(group % 4, weight=1)
                check = ttk.Checkbutton(
                    group_frame,
                    variable=variable,
                    command=lambda s=stage, g=group: self._on_stage_group_start_changed(s, g),
                )
                check.grid(row=group // 4, column=group % 4, sticky="w", padx=(0, 8), pady=2)
                self._stage_group_checks[stage][group] = check
            yield

        waveform_var.trace_add(
            "write",
            lambda *_args, s=stage: self._on_stage_waveform_changed(s),
        )
        ovv_time_var.trace_add("write", lambda *_args, s=stage: self._on_stage_ovv_time_changed(s))
        jump_target_var.trace_add("write", lambda *_args, s=stage: self._on_stage_jump_changed(s))

        self._update_stage_threshold_entry_states(stage)
        self._stage_frames[stage] = frame

    def _slice_options(self) -> list[str]:
        return [f"slice{i}" for i in range(31)] + ["serial"]

    def _build_control_buttons(self, parent: tk.Misc, row: int) -> None:
        for _ in self._build_control_buttons_steps(parent, row):
            pass

    def _build_control_buttons_steps(self, parent: tk.Misc, row: int) -> Iterator[None]:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self._register_module_spacing_widget(frame, before=True, after=False)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        actions = [
            ("start_detection", self.start_detection, "Primary.TButton"),
            ("stop_detection", self.stop_detection, "Secondary.TButton"),
            ("start_play", self.start_play, "Primary.TButton"),
            ("stop_play", self.stop_play, "Secondary.TButton"),
            ("awp1018_stop_all", self.stop_all, "Danger.TButton"),
        ]
        yield
        for index, (key, command, style) in enumerate(actions):
            is_full_width = key == "awp1018_stop_all"
            grid_row = index // 2
            grid_col = index % 2
            btn = ttk.Button(frame, style=style, command=command)
            btn.grid(
                row=grid_row,
                column=grid_col,
                sticky="ew",
                columnspan=2 if is_full_width else 1,
                padx=0 if is_full_width else ((0, 6) if index % 2 == 0 else (6, 0)),
                pady=(0, 6) if index < 2 else (6, 0),
            )
            self._buttons[key] = btn

    def _bind_board_preset_tooltip(self, button: ttk.Button, action: str) -> None:
        tooltip_key = self.BOARD_PRESET_TOOLTIP_KEYS[action]
        self._tooltips.append(SimpleTooltip(button, lambda key=tooltip_key: self.tr(key)))

    def _bind_page_action_tooltip(self, button: ttk.Button, action: str) -> None:
        tooltip_key = self.PAGE_ACTION_TOOLTIP_KEYS[action]
        self._tooltips.append(SimpleTooltip(button, lambda key=tooltip_key: self.tr(key)))

    def _bind_software_preset_tooltip(self, button: ttk.Button, preset_index: int) -> None:
        self._tooltips.append(
            SimpleTooltip(button, lambda index=preset_index: self._software_preset_tooltip_text(index))
        )

    def _software_preset_tooltip_text(self, preset_index: int) -> str:
        presets = self.DIFF_SOFTWARE_PRESETS if self.diff_mode else self.NORMAL_SOFTWARE_PRESETS
        if not 0 <= preset_index < len(presets):
            return ""
        preset = presets[preset_index]
        if self.diff_mode:
            depth = int(preset["depth"])
            sinc_duration_us = (depth // 2) * sum(
                int(preset[key]) for key in ("time1_us", "time2_us", "time3_us")
            )
            enhance_duration_us = (
                depth * int(preset["time1_us"]) * int(preset["play_count"])
            )
            period_us = sinc_duration_us + enhance_duration_us
            return self.tr(
                "awp1018_diff_software_preset_tooltip",
                max_voltage=preset["max_voltage"],
                min_voltage=preset["min_voltage"],
                period_ms=f"{period_us / 1000:.3f}",
                segment_times=(
                    f"{preset['time1_us']}/{preset['time2_us']}/{preset['time3_us']} us"
                ),
            )

        period_us = (
            int(preset["depth"])
            * int(preset["point_time_us"])
            * int(preset["repeat"])
        )
        voltage = self.ROM_AMPLITUDE_OPTIONS[int(preset["amplitude_code"])].removesuffix(" V")
        return self.tr(
            "awp1018_normal_software_preset_tooltip",
            voltage=voltage,
            period_ms=f"{period_us / 1000:.3f}",
            repeat=preset["repeat"],
            offset_code=preset["offset_code"],
        )

    def apply_software_preset(self, preset_index: int) -> bool:
        presets = self.DIFF_SOFTWARE_PRESETS if self.diff_mode else self.NORMAL_SOFTWARE_PRESETS
        if not 0 <= preset_index < len(presets):
            return False
        preset = presets[preset_index]

        previous_loading_state = self._loading_settings
        self._loading_settings = True
        try:
            if self.diff_mode:
                self.diff_auto_serial_var.set(True)
                self.diff_serial_var.set("serial0")
                self.rom_slice_var.set("slice17")
                self.diff_sinc_slice_group_var.set("slice17-slice19")
                self.rom_waveform_var.set("SINC")
                self._refresh_rom_depth_options()
                self.rom_depth_var.set(preset["depth"])
                self.diff_sinc_max_voltage_var.set(preset["max_voltage"])
                self.diff_sinc_min_voltage_var.set(preset["min_voltage"])
                self.diff_sinc_time1_var.set(preset["time1_us"])
                self.diff_sinc_time2_var.set(preset["time2_us"])
                self.diff_sinc_time3_var.set(preset["time3_us"])
                self.diff_play_count_var.set(preset["play_count"])
                self.diff_single_waveform_var.set("SIN")
                self.rom_repeat_var.set("1")
                self.rom_infinite_loop_var.set(False)
            else:
                self.rom_waveform_var.set(preset["waveform"])
                self._refresh_rom_depth_options()
                self.rom_depth_var.set(preset["depth"])
                self.rom_point_time_var.set(preset["point_time_us"])
                self.rom_slowup_var.set(self.ROM_SLOW_OPTIONS[0])
                self.rom_slowdown_var.set(self.ROM_SLOW_OPTIONS[0])
                self.rom_repeat_var.set(preset["repeat"])
                self.rom_infinite_loop_var.set(False)
                self.rom_offset_var.set(self.ROM_OFFSET_OPTIONS[int(preset["offset_code"])])
                self.rom_amplitude_var.set(self.ROM_AMPLITUDE_OPTIONS[int(preset["amplitude_code"])])
        finally:
            self._loading_settings = previous_loading_state

        self._refresh_rom_depth_options()
        if self.diff_mode:
            self._refresh_diff_slice_group_options()
            self._refresh_diff_waveform_controls()
        self._draw_rom_waveform()
        return self.apply_rom_waveform_selection()

    def _build_button_readings_status_steps(self, parent: tk.Misc) -> Iterator[None]:
        help_key = "awp1221_help_adc_readings" if self.supports_live_adc_read else "awp1018_help_button_adc"
        title_row, _title = self.make_module_title_row(
            parent,
            "awp1221_adc_readings",
            step=3,
            step_total=4,
            role="verify",
            help_key=help_key,
            row=0,
            columnspan=4,
            pady=(0, 6),
        )
        title_row.grid_configure(sticky="ew")
        card_grid = ResponsiveCardGrid(
            parent,
            item_count=self.channel_count,
            max_columns=4,
            min_card_width=92,
            gap=8,
        )
        card_grid.grid(row=1, column=0, columnspan=4, sticky="ew")
        self._status_card_grid = card_grid
        yield
        for channel in range(self.channel_count):
            value_var = tk.StringVar(value="")
            box = tk.Frame(
                card_grid,
                height=54,
                bg=self.STATUS_IDLE_COLOR,
                highlightthickness=1,
                highlightbackground=self.colors["border"],
            )
            box.columnconfigure(0, weight=1)
            box.rowconfigure(0, weight=1)
            box.rowconfigure(1, weight=1)
            title = tk.Label(
                box,
                text=self.tr("awp1018_channel_label", index=channel),
                bg=self.STATUS_IDLE_COLOR,
                fg=self.colors["text"],
            )
            title.grid(row=0, column=0, sticky="s")
            value = tk.Label(
                box,
                textvariable=value_var,
                bg=self.STATUS_IDLE_COLOR,
                fg=self.colors["text"],
                font=("Microsoft YaHei UI", 11, "bold"),
            )
            value.grid(row=1, column=0, sticky="n")
            card_grid.add_card(box)
            self._diff_status_frames[channel] = box
            self._diff_status_titles[channel] = title
            self._diff_status_value_vars[channel] = value_var

        if self.supports_live_adc_read:
            start_button = ttk.Button(
                parent,
                style="Primary.TButton",
                command=self.start_realtime_display,
            )
            start_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(9, 0), padx=(0, 5))
            self._buttons["awp1018_realtime_display"] = start_button
            stop_button = ttk.Button(
                parent,
                style="Secondary.TButton",
                command=self.stop_realtime_display,
            )
            stop_button.grid(row=2, column=2, columnspan=2, sticky="ew", pady=(9, 0), padx=(5, 0))
            self._buttons["adc_stop_realtime"] = stop_button
        else:
            unsupported = ttk.Label(
                parent,
                style="Panel.TLabel",
                wraplength=540,
                justify="left",
            )
            unsupported.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(9, 0))
            self._labels["awp1018_normal_8ch_live_read_unsupported"] = unsupported

    def _show_stage(self, stage: str) -> None:
        self.active_stage.set(stage)
        for frame in self._stage_frames.values():
            frame.grid_remove()
        if stage in self._stage_frames:
            self._stage_frames[stage].grid()
        for name, button in self._stage_buttons.items():
            button.configure(style="Selected.TButton" if name == stage else "Secondary.TButton")

    def _on_stage_group_start_changed(self, stage: str, group: int) -> None:
        variables = self.stage_group_start_vars.get(stage)
        if variables is None or not 0 <= group < len(variables):
            return
        selected = variables[group]
        if not selected.get():
            selected.set(True)
            return

        for other_stage, other_variables in self.stage_group_start_vars.items():
            if other_stage != stage:
                other_variables[group].set(False)
        if 0 <= group < len(self.channel_start_stage_vars):
            self.channel_start_stage_vars[group].set(stage)
        self._write_group_start_register(show_error=False)

    def _on_channel_start_stage_changed(self, channel: int) -> None:
        if not 0 <= channel < len(self.channel_vars):
            return
        stage = str(self.channel_start_stage_vars[channel].get())
        if stage not in {"Stage0", "Stage1", "Stage2", "Stage3"}:
            return
        for stage_name, variables in self.stage_group_start_vars.items():
            variables[channel].set(stage_name == stage)
        if not self._loading_settings:
            self._write_group_start_register(show_error=False)

    def _group_start_stage_indices(self) -> list[int]:
        indices: list[int] = []
        stages = ("Stage0", "Stage1", "Stage2", "Stage3")
        for group in range(len(self.channel_vars)):
            selected_index = 0
            for stage_index, stage in enumerate(stages):
                if self.stage_group_start_vars[stage][group].get():
                    selected_index = stage_index
                    break
            indices.append(selected_index)
        return indices

    @staticmethod
    def _compose_stage_register_values(
        stage_state: tuple[int, int, int, int, str, bool, bool, int],
        control_base: int,
        waveform_base: int,
    ) -> tuple[int, int, int, int]:
        (
            ovv_code,
            press_threshold,
            release_threshold,
            target_index,
            condition,
            press_debounce,
            release_debounce,
            waveform_index,
        ) = stage_state
        if not 0 <= ovv_code <= 0x0F:
            raise ValueError("invalid Stage OVV code")
        if not 0 <= press_threshold <= 0x0FFF or not 0 <= release_threshold <= 0x0FFF:
            raise ValueError("invalid Stage threshold")
        if not 0 <= target_index <= 3 or not 0 <= waveform_index <= 30:
            raise ValueError("invalid Stage target")
        if condition not in {"press", "release"}:
            raise ValueError("invalid Stage jump condition")

        press_mask = (1 << 4) | (1 << 0)
        release_mask = (1 << 5) | (1 << 1)
        event_mask = release_mask if condition == "release" else press_mask
        control_value = (
            (int(control_base) & ~(0xFF00 | press_mask | release_mask))
            | ((ovv_code & 0x0F) << 12)
            | ((ovv_code & 0x0F) << 8)
            | event_mask
        )
        press_value = (press_threshold & 0x0FFF) | ((target_index & 0x03) << 13)
        if press_debounce:
            press_value |= 1 << 15
        release_value = (
            (release_threshold & 0x0FFF)
            | (1 << 12)
            | ((target_index & 0x03) << 13)
        )
        if release_debounce:
            release_value |= 1 << 15
        waveform_value = (
            (int(waveform_base) & ~0x1F1F)
            | (waveform_index & 0x1F)
            | ((waveform_index & 0x1F) << 8)
        )
        return tuple(value & 0xFFFF for value in (
            control_value,
            press_value,
            release_value,
            waveform_value,
        ))

    def _capture_stage_state(
        self,
        stage: str,
    ) -> tuple[int, int, int, int, str, bool, bool, int]:
        variables = self.stage_vars[stage]
        try:
            ovv_code = self.ADC_OVV_OPTIONS.index(str(variables["ovv_time"].get()))
            press_threshold = int(str(variables["high"].get()).strip(), 0)
            release_threshold = int(str(variables["low"].get()).strip(), 0)
            target_index = self._parse_stage_index(str(variables["jump_target"].get()))
            waveform_index = self._parse_slice_index(str(variables["waveform"].get()))
        except Exception as exc:
            raise ValueError(f"invalid {stage} configuration") from exc
        if target_index is None or waveform_index is None:
            raise ValueError(f"invalid {stage} target or waveform")
        return (
            ovv_code,
            press_threshold,
            release_threshold,
            target_index,
            str(variables["jump_condition"].get()),
            bool(variables["press_debounce_enabled"].get()),
            bool(variables["release_debounce_enabled"].get()),
            waveform_index,
        )

    def _compose_adc_fixed_register_values(
        self,
        state: dict[str, Any],
        merge_values: dict[int, int],
    ) -> tuple[int, ...]:
        reg05 = int(merge_values[0x05]) & ~((0b11 << 2) | (1 << 6))
        reg05 |= (int(state["arbitration_mode_code"]) & 0b11) << 2
        if state["auto_serial"]:
            reg05 |= 1 << 6

        reg07 = (int(state["channel_mask"]) & 0x00FF) | (
            (int(state["return_time_code"]) & 0b111) << 8
        )
        if state["return_release_enhance"]:
            reg07 |= 1 << 11
        if state["return_release"]:
            reg07 |= 1 << 12
        if state["return_press"]:
            reg07 |= 1 << 13
        if state["zps_enable"]:
            reg07 |= 0b11 << 14

        reg0d = int(merge_values[0x0D])
        for group, stage_index in enumerate(state["group_start_indices"]):
            if not 0 <= int(stage_index) <= 3:
                raise ValueError("invalid group start Stage")
            shift = group * 2
            reg0d = (reg0d & ~(0b11 << shift)) | ((int(stage_index) & 0b11) << shift)

        debounce_ms = int(state["debounce_ms"])
        if not 0 <= debounce_ms <= 1024:
            raise ValueError("invalid debounce time")
        debounce_code = round(debounce_ms * 0x03FF / 1024) if debounce_ms else 0
        sample_code = int(state["sample_code"])
        if not 0 <= sample_code <= 3:
            raise ValueError("invalid ADC sample update")
        reg22 = (
            (int(merge_values[0x22]) & ~0x0FFF)
            | (debounce_code & 0x03FF)
            | ((sample_code & 0b11) << 10)
        )

        values = [reg05, reg07, reg0d, reg22]
        stage_registers = (
            (0x0E, 0x12),
            (0x13, 0x17),
            (0x18, 0x1C),
            (0x1D, 0x21),
        )
        stages = tuple(state["stages"])
        if len(stages) != 4:
            raise ValueError("all four Stages are required")
        for stage_state, (control_reg, waveform_reg) in zip(stages, stage_registers):
            values.extend(
                self._compose_stage_register_values(
                    stage_state,
                    merge_values[control_reg],
                    merge_values[waveform_reg],
                )
            )

        reg34 = int(merge_values[0x34])
        reg34 = (reg34 | 0x0008) if state["diff_mode"] else (reg34 & ~0x0008)
        values.append(reg34)
        return tuple(value & 0xFFFF for value in values)

    def _build_adc_preset_model(
        self,
        state: dict[str, Any],
        merge_values: dict[int, int],
    ) -> Awp1018AdcPresetModel:
        composed_values = self._compose_adc_fixed_register_values(state, merge_values)
        values_by_register = dict(zip(AWP1018_ADC_FIXED_REGISTERS, composed_values))
        values_by_register[0x30] = AWP1018_ADC_REGISTER_30_VALUE
        include_stage23 = self._board_preset_includes_stage23(state)
        register_order = (
            AWP1018_ADC_STAGE4_REGISTERS
            if include_stage23
            else AWP1018_ADC_STAGE01_REGISTERS
        )
        fixed_values = tuple(values_by_register[register] for register in register_order)
        try:
            required_slices = required_awp1018_descriptor_slices(
                fixed_values,
                registers=register_order,
                force_diff_serial0=True,
            )
        except ValueError as exc:
            if "exceeds 8" in str(exc):
                raise ValueError(self.tr("awp1018_board_preset_descriptor_limit")) from exc
            if "slice31" in str(exc):
                raise ValueError(self.tr("awp1018_board_preset_serial4_unsupported")) from exc
            raise
        descriptor_words = state["descriptor_words"]
        missing = [slice_index for slice_index in required_slices if slice_index not in descriptor_words]
        if missing:
            names = ", ".join(f"slice{slice_index}" for slice_index in missing)
            raise ValueError(self.tr("awp1018_board_preset_missing_descriptor", slices=names))
        model = Awp1018AdcPresetModel(
            schema_version=AWP1018_ADC_SCHEMA_VERSION,
            fixed_register_values=fixed_values,
            waveform_descriptors=tuple(
                AWP1018AdcWaveformDescriptor(
                    slice_index,
                    tuple(int(word) & 0xFFFF for word in descriptor_words[slice_index]),
                )
                for slice_index in required_slices
            ),
            include_stage23=include_stage23,
        )
        build_awp1018_adc_write_plan(model)
        return model

    @staticmethod
    def _board_preset_includes_stage23(state: dict[str, Any]) -> bool:
        group_start_indices = tuple(int(index) for index in state.get("group_start_indices", ()))
        stages = tuple(state.get("stages", ()))
        stage01_only = (
            bool(group_start_indices)
            and all(index == 0 for index in group_start_indices)
            and len(stages) >= 2
            and int(stages[0][3]) == 1
            and int(stages[1][3]) == 0
        )
        return not stage01_only

    @staticmethod
    def _board_preset_merge_registers(include_stage23: bool) -> tuple[int, ...]:
        if include_stage23:
            return AWP1018_ADC_FIXED_REGISTERS
        return tuple(
            register
            for register in AWP1018_ADC_STAGE01_REGISTERS
            if register in AWP1018_ADC_FIXED_REGISTERS
        )

    def _capture_adc_preset_state(
        self,
        *,
        allow_serial4: bool = False,
    ) -> dict[str, Any]:
        serial4_selected = bool(self.diff_mode and self._diff_serial4_selected())
        if serial4_selected and not allow_serial4:
            raise ValueError(self.tr("awp1018_board_preset_serial4_unsupported"))

        stages = []
        for stage in ("Stage0", "Stage1", "Stage2", "Stage3"):
            stages.append(self._capture_stage_state(stage))

        descriptor_words = {
            int(slice_index): tuple(int(word) & 0xFFFF for word in words)
            for slice_index, words in self._slice_definition_cache().items()
        }
        generated_descriptors = [] if serial4_selected else (
            self._build_diff_waveform_descriptors()
            if self.diff_mode
            else [self._build_normal_waveform_descriptor()]
        )
        selected_descriptor_slices: list[int] = []
        for descriptor in generated_descriptors:
            if descriptor is None:
                continue
            slice_index, words = descriptor
            descriptor_words[int(slice_index)] = tuple(int(word) & 0x0FFF for word in words)
            selected_descriptor_slices.append(int(slice_index))
        serial4_codes: tuple[int, ...] = ()
        if serial4_selected:
            serial4_codes = tuple(
                self._parse_serial4_segment_code(variable.get())
                for variable in self.diff_serial4_segment_vars
            )
            selected_descriptor_slices = list(self._serial4_selected_slices())
            missing = [
                slice_index
                for slice_index in selected_descriptor_slices
                if slice_index not in descriptor_words
            ]
            if missing:
                names = ", ".join(f"slice{slice_index}" for slice_index in missing)
                raise ValueError(self.tr("awp1018_settings_missing_slices", slices=names))

        channel_mask = 0
        for index, variable in enumerate(self.channel_vars):
            if variable.get():
                channel_mask |= 1 << index
        try:
            return_time_code = self.RETURN_BIT_TIME_OPTIONS.index(self.return_bit_time_var.get())
            sample_code = self.ADC_SAMPLE_UPDATE_OPTIONS.index(self.adc_sample_update_var.get())
            debounce_ms = int(self.debounce_time_var.get().strip(), 0)
        except (ValueError, AttributeError) as exc:
            raise ValueError(self.tr("invalid_input")) from exc

        state = {
            "arbitration_mode_code": int(self.arbitration_mode_code),
            "auto_serial": bool(self.diff_mode and self.diff_auto_serial_var.get()),
            "channel_mask": channel_mask,
            "return_time_code": return_time_code,
            "return_press": bool(self.return_bit_press_enable_var.get()),
            "return_release": bool(self.return_bit_release_enable_var.get()),
            "return_release_enhance": bool(self.return_bit_release_time_enhance_var.get()),
            "zps_enable": bool(self.zps_enable_var.get()),
            "group_start_indices": tuple(self._group_start_stage_indices()),
            "debounce_ms": debounce_ms,
            "sample_code": sample_code,
            "diff_mode": bool(self.diff_mode),
            "stages": tuple(stages),
            "descriptor_words": descriptor_words,
            "selected_descriptor_slices": tuple(selected_descriptor_slices),
            "serial4_selected": serial4_selected,
            "serial4_codes": serial4_codes,
        }
        if allow_serial4:
            try:
                dac_sample_rate = int(self.dac_sample_rate_var.get().strip(), 0)
                if not 0 <= dac_sample_rate <= 0xFFFF:
                    raise ValueError
                state.update({
                    "zero_time_code": self.ZERO_TIME_CODES[self.zero_time_var.get()],
                    "dac_sample_rate": dac_sample_rate,
                    "interrupt_enabled": bool(self.interrupt_enable_var.get()),
                    "script_mode": "diff" if self.diff_mode else "normal",
                })
            except (KeyError, ValueError, AttributeError) as exc:
                raise ValueError(self.tr("invalid_input")) from exc
        return state

    def _build_awp1018_register_script_operations(
        self,
        state: dict[str, Any],
        live_register_snapshot: dict[int, int],
        device_addr: int,
    ) -> list[RegisterScriptOperation]:
        fixed_values = self._compose_adc_fixed_register_values(
            state,
            live_register_snapshot,
        )
        register_values = dict(zip(AWP1018_ADC_FIXED_REGISTERS, fixed_values))

        reg04, reg08 = self._compose_interrupt_register_values(
            bool(state["interrupt_enabled"]),
            live_register_snapshot[0x04],
            live_register_snapshot[0x08],
        )
        register_values.update({
            0x04: reg04,
            0x06: (
                (live_register_snapshot[0x06] & ~(0b11 << 14))
                | ((int(state["zero_time_code"]) & 0b11) << 14)
            ),
            0x08: reg08,
            0x26: live_register_snapshot[0x26] if state["diff_mode"] else 0x0010,
            0x27: int(state["dac_sample_rate"]),
        })
        if state["serial4_selected"]:
            reg29, reg2a = self._compose_serial4_register_values(
                tuple(state["serial4_codes"]),
                live_register_snapshot[0x29],
                live_register_snapshot[0x2A],
            )
            register_values.update({0x29: reg29, 0x2A: reg2a})

        referenced_slices: list[int] = []

        def add_slice(slice_index: int) -> None:
            if slice_index not in referenced_slices:
                referenced_slices.append(slice_index)

        for stage_state in state["stages"]:
            slice_index = int(stage_state[-1])
            add_slice(slice_index)
            if state["auto_serial"]:
                for dependency in self._serial_slice_group(slice_index):
                    add_slice(dependency)
        for slice_index in state["selected_descriptor_slices"]:
            add_slice(int(slice_index))

        descriptor_words = state["descriptor_words"]
        missing = [
            slice_index
            for slice_index in referenced_slices
            if slice_index not in descriptor_words
        ]
        if missing:
            names = ", ".join(f"slice{slice_index}" for slice_index in missing)
            raise ValueError(self.tr("awp1018_settings_missing_slices", slices=names))
        descriptors = {
            slice_index: tuple(int(word) & 0x0FFF for word in descriptor_words[slice_index])
            for slice_index in referenced_slices
        }
        return build_awp1018_register_script(
            device_addr,
            mode=state["script_mode"],
            register_values=register_values,
            descriptors=descriptors,
            sram_address_base=live_register_snapshot[0x28],
        )

    @staticmethod
    def _serial_slice_group(slice_index: int) -> tuple[int, ...]:
        if 16 <= slice_index <= 19:
            return tuple(range(16, 20))
        if 20 <= slice_index <= 23:
            return tuple(range(20, 24))
        if 24 <= slice_index <= 27:
            return tuple(range(24, 28))
        if 28 <= slice_index <= 30:
            return tuple(range(28, 31))
        return ()

    def _write_group_start_register(self, *, show_error: bool = True) -> bool:
        current = self.read_reg(0x0D, show_error=show_error)
        if current is None:
            return False
        value = current
        for group, stage_index in enumerate(self._group_start_stage_indices()):
            shift = group * 2
            value = (value & ~(0b11 << shift)) | ((stage_index & 0b11) << shift)
        return self.write_reg(0x0D, value, show_error=show_error)

    def _build_rom_selector(self, parent: tk.Misc) -> None:
        for _ in self._build_rom_selector_steps(parent):
            pass

    def _build_rom_selector_steps(self, parent: tk.Misc) -> Iterator[None]:
        selector_body = ttk.Frame(parent, style="Panel.TFrame")
        selector_body.grid(row=1, column=0, columnspan=2, sticky="nsew")
        parent.rowconfigure(1, weight=1)
        title_row, _title = self.make_module_title_row(
            parent,
            "awp1018_waveform_selector",
            step=2,
            step_total=4,
            help_key="awp1018_help_button_waveform",
            row=0,
            columnspan=2,
            pady=(0, 8),
        )
        title_row.grid_configure(sticky="ew")
        parent = selector_body
        parent.columnconfigure(0, weight=0, minsize=280 if self.diff_mode else 340)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)

        controls = ttk.Frame(parent, style="Panel.TFrame")
        controls.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        controls.columnconfigure(1, weight=1)
        yield
        controls.columnconfigure(2, weight=0)
        controls.columnconfigure(3, weight=0)

        row = 0
        if self.diff_mode:
            auto_check = ttk.Checkbutton(
                controls,
                variable=self.diff_auto_serial_var,
                command=self._on_diff_auto_serial_changed,
            )
            auto_check.grid(row=row, column=0, columnspan=4, sticky="w", pady=5)
            self._checks["awp1018_diff_auto_serial"] = auto_check
            self._selector_widgets["awp1018_diff_auto_serial"] = [auto_check]
            row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_diff_serial",
                self.diff_serial_var,
                ["serial0", "serial1", "serial2", "serial3"],
                column=0,
            )
            row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_rom_slice",
                self.rom_slice_var,
                self._diff_slice_options(),
                column=0,
            )
            row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_rom_waveform",
                self.rom_waveform_var,
                ["SINC", "SIN", "Gauss", "DC"],
                column=0,
            )
            row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_diff_sinc_slice_group",
                self.diff_sinc_slice_group_var,
                self._diff_sinc_slice_group_options(),
                column=0,
            )
            row += 1
            self.diff_serial4_segment_combos: list[ttk.Combobox] = []
            for segment_index, variable in enumerate(self.diff_serial4_segment_vars, start=1):
                self._add_selector_combo(
                    controls,
                    row,
                    f"awp1018_serial4_segment_{segment_index}",
                    variable,
                    self._serial4_segment_options(),
                    column=0,
                )
                row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_rom_depth",
                self.rom_depth_var,
                self._rom_depth_options(),
                column=0,
            )
            row += 1
            self._add_selector_entry(controls, row, "awp1018_diff_sinc_max_voltage", self.diff_sinc_max_voltage_var, column=0)
            row += 1
            self._add_selector_entry(controls, row, "awp1018_diff_sinc_min_voltage", self.diff_sinc_min_voltage_var, column=0)
            row += 1
            self._add_selector_entry(controls, row, "awp1018_diff_sinc_time1", self.diff_sinc_time1_var, column=0)
            row += 1
            self._add_selector_entry(controls, row, "awp1018_diff_sinc_time2", self.diff_sinc_time2_var, column=0)
            row += 1
            self._add_selector_entry(controls, row, "awp1018_diff_sinc_time3", self.diff_sinc_time3_var, column=0)
            row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_diff_single_waveform",
                self.diff_single_waveform_var,
                ["SINC", "SIN", "Gauss", "DC"],
                column=0,
            )
            row += 1
            self._add_selector_entry(controls, row, "awp1018_rom_point_time", self.rom_point_time_var, column=0)
            row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_rom_slowup",
                self.rom_slowup_var,
                list(self.ROM_SLOW_OPTIONS),
                column=0,
            )
            row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_rom_slowdown",
                self.rom_slowdown_var,
                list(self.ROM_SLOW_OPTIONS),
                column=0,
            )
            row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_rom_repeat",
                self.rom_repeat_var,
                self.ROM_REPEAT_OPTIONS,
                column=0,
                width=8,
            )
            infinite_check = ttk.Checkbutton(controls, variable=self.rom_infinite_loop_var)
            infinite_check.grid(row=row, column=2, columnspan=2, sticky="w", pady=5, padx=(4, 0))
            self._checks["awp1018_rom_infinite_loop"] = infinite_check
            self._selector_widgets["awp1018_rom_infinite_loop"] = [infinite_check]
            row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_rom_offset",
                self.rom_offset_var,
                self.DIFF_ROM_OFFSET_OPTIONS,
                column=0,
            )
            row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_rom_amplitude",
                self.rom_amplitude_var,
                self.DIFF_ROM_AMPLITUDE_OPTIONS,
                column=0,
            )
            row += 1
            self._add_selector_combo(
                controls,
                row,
                "awp1018_diff_play_count",
                self.diff_play_count_var,
                self.ROM_REPEAT_OPTIONS,
                column=0,
                width=8,
            )
            row += 1

            apply_btn = ttk.Button(controls, style="Primary.TButton", width=-16, command=self.apply_rom_waveform_selection)
            apply_btn.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
            self._buttons["awp1018_write_rom_waveform"] = apply_btn
            row += 1

            self.rom_hint_label = ttk.Label(controls, style="Muted.TLabel", wraplength=280)
            self.rom_hint_label.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))
            row += 1

            self.diff_play_hint_label = ttk.Label(controls, style="Muted.TLabel", wraplength=280)
            self.diff_play_hint_label.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(8, 0))

            yield from self._build_rom_preview_steps(parent)
            self._refresh_diff_waveform_controls()
            self._refresh_rom_depth_options()
            return

        self._add_selector_combo(
            controls,
            row,
            "awp1018_rom_slice",
            self.rom_slice_var,
            [f"slice{i}" for i in range(31)],
            column=0,
        )
        row += 1
        yield
        self._add_selector_combo(
            controls,
            row,
            "awp1018_rom_waveform",
            self.rom_waveform_var,
            list(self.ROM_WAVEFORMS),
            column=0,
        )
        row += 1

        self._add_selector_combo(
            controls,
            row,
            "awp1018_rom_depth",
            self.rom_depth_var,
            self._rom_depth_options(),
            column=0,
        )
        row += 1
        self._add_selector_entry(controls, row, "awp1018_rom_point_time", self.rom_point_time_var, column=0)
        row += 1
        yield

        self._add_selector_combo(
            controls,
            row,
            "awp1018_rom_slowup",
            self.rom_slowup_var,
            list(self.ROM_SLOW_OPTIONS),
            column=0,
        )
        row += 1
        self._add_selector_combo(
            controls,
            row,
            "awp1018_rom_slowdown",
            self.rom_slowdown_var,
            list(self.ROM_SLOW_OPTIONS),
            column=0,
        )
        row += 1

        self._add_selector_combo(
            controls,
            row,
            "awp1018_rom_repeat",
            self.rom_repeat_var,
            self.ROM_REPEAT_OPTIONS,
            column=0,
            width=8,
        )
        infinite_check = ttk.Checkbutton(controls, variable=self.rom_infinite_loop_var)
        yield
        infinite_check.grid(row=row, column=2, columnspan=2, sticky="w", pady=5, padx=(4, 0))
        self._checks["awp1018_rom_infinite_loop"] = infinite_check
        row += 1
        self._add_selector_combo(
            controls,
            row,
            "awp1018_rom_offset",
            self.rom_offset_var,
            self.ROM_OFFSET_OPTIONS,
            column=0,
        )
        row += 1

        self._add_selector_combo(
            controls,
            row,
            "awp1018_rom_amplitude",
            self.rom_amplitude_var,
            self.ROM_AMPLITUDE_OPTIONS,
            column=0,
        )
        yield
        row += 1

        apply_btn = ttk.Button(controls, style="Primary.TButton", command=self.apply_rom_waveform_selection)
        apply_btn.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self._buttons["awp1018_write_rom_waveform"] = apply_btn
        row += 1

        self.rom_hint_label = ttk.Label(controls, style="Muted.TLabel", wraplength=280)
        yield
        self.rom_hint_label.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        yield from self._build_rom_preview_steps(parent)
        self._refresh_rom_depth_options()

    def _build_rom_preview(self, parent: tk.Misc) -> None:
        for _ in self._build_rom_preview_steps(parent):
            pass

    def _build_rom_preview_steps(self, parent: tk.Misc) -> Iterator[None]:
        preview = ttk.Frame(parent, style="Panel.TFrame")
        preview.grid(row=0, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(2, weight=1)

        self.rom_metric_label = ttk.Label(preview, textvariable=self.rom_metric_var, style="Panel.TLabel", wraplength=760)
        self.rom_metric_label.grid(row=0, column=0, sticky="w", pady=(0, 4))
        yield

        self.rom_frequency_label = ttk.Label(preview, textvariable=self.rom_frequency_var, style="Muted.TLabel", wraplength=760)
        self.rom_frequency_label.grid(row=1, column=0, sticky="w", pady=(0, 8))

        self.rom_canvas = tk.Canvas(
            preview,
            bg=self.colors["canvas_bg"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            height=600 if self.diff_mode else 520,
        )
        self.rom_canvas.grid(row=2, column=0, sticky="nsew")

    def _add_selector_entry(
        self,
        parent: tk.Misc,
        row: int,
        key: str,
        variable: tk.StringVar,
        *,
        column: int,
    ) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=column, sticky="w", padx=(0 if column == 0 else 12, 8), pady=5)
        self._labels[key] = label
        entry = ttk.Entry(parent, textvariable=variable, width=12)
        entry.grid(row=row, column=column + 1, sticky="ew", pady=5)
        self._selector_widgets[key] = [label, entry]

    def _add_selector_combo(
        self,
        parent: tk.Misc,
        row: int,
        key: str,
        variable: tk.StringVar,
        values: list[str],
        *,
        column: int,
        width: int = 14,
    ) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=column, sticky="w", padx=(0 if column == 0 else 12, 8), pady=5)
        self._labels[key] = label
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=width)
        combo.grid(
            row=row,
            column=column + 1,
            sticky="ew",
            pady=5,
        )
        self._selector_widgets[key] = [label, combo]
        if key == "awp1018_rom_depth":
            self.rom_depth_combo = combo
        elif key == "awp1018_rom_waveform":
            self.rom_waveform_combo = combo
        elif key == "awp1018_rom_slice":
            self.rom_slice_combo = combo
        elif key == "awp1018_diff_sinc_slice_group":
            self.diff_sinc_slice_group_combo = combo
        elif key.startswith("awp1018_serial4_segment_"):
            self.diff_serial4_segment_combos.append(combo)

    def _rom_depth_options(self) -> list[str]:
        if self.rom_waveform_var.get() == "Gauss":
            start = 512
        else:
            start = 1024
        return [str(start >> code) for code in range(8)]

    def _refresh_rom_depth_options(self) -> None:
        if not hasattr(self, "rom_depth_combo"):
            return
        options = self._rom_depth_options()
        current = self.rom_depth_var.get()
        self.rom_depth_combo.configure(values=options)
        if current not in options:
            self.rom_depth_var.set(options[0])
        self._queue_rom_waveform_draw()

    def _diff_sinc_slice_group_options(self) -> list[str]:
        serial = self.diff_serial_var.get()
        if serial == "serial0":
            return ["slice16-slice18", "slice17-slice19"]
        if serial == "serial1":
            return ["slice20-slice22", "slice21-slice23"]
        if serial == "serial2":
            return ["slice24-slice26", "slice25-slice27"]
        if serial == "serial3":
            return ["slice28-slice30"]
        return ["slice16-slice18", "slice17-slice19", "slice20-slice22", "slice21-slice23", "slice24-slice26", "slice25-slice27", "slice28-slice30"]

    def _diff_slice_options(self) -> list[str]:
        return [f"slice{index}" for index in range(31)] + ["serial4"]

    def _refresh_diff_slice_group_options(self) -> None:
        if not hasattr(self, "diff_sinc_slice_group_combo"):
            return
        options = self._diff_sinc_slice_group_options()
        self.diff_sinc_slice_group_combo.configure(values=options)
        if self.diff_sinc_slice_group_var.get() not in options:
            self.diff_sinc_slice_group_var.set(options[0])
        self._refresh_diff_waveform_controls()

    def _refresh_diff_waveform_controls(self) -> None:
        auto_serial = self.diff_auto_serial_var.get()
        waveform_values = ["SINC", "SIN", "Gauss", "DC"] if auto_serial else ["SIN", "Gauss", "DC"]
        if hasattr(self, "rom_waveform_combo"):
            self.rom_waveform_combo.configure(values=waveform_values)
        if self.rom_waveform_var.get() not in waveform_values:
            self.rom_waveform_var.set(waveform_values[0])
        if hasattr(self, "rom_slice_combo"):
            self.rom_slice_combo.configure(values=self._diff_slice_options())

        is_sinc = self.rom_waveform_var.get() == "SINC"
        serial4_active = self.rom_slice_var.get() == "serial4"
        sinc_detail_keys = [
            "awp1018_diff_sinc_slice_group",
            "awp1018_diff_sinc_max_voltage",
            "awp1018_diff_sinc_min_voltage",
            "awp1018_diff_sinc_time1",
            "awp1018_diff_sinc_time2",
            "awp1018_diff_sinc_time3",
        ]
        slice_param_keys = [
            "awp1018_rom_point_time",
            "awp1018_rom_slowup",
            "awp1018_rom_slowdown",
            "awp1018_rom_repeat",
            "awp1018_rom_infinite_loop",
            "awp1018_rom_offset",
            "awp1018_rom_amplitude",
        ]
        hidden_legacy_keys = [
            "awp1018_diff_single_waveform",
            "awp1018_diff_play_count",
        ]
        self._set_selector_row_visible("awp1018_diff_serial", auto_serial)
        self._set_selector_row_visible("awp1018_rom_slice", not is_sinc)
        self._set_selector_row_visible("awp1018_rom_depth", not serial4_active)
        for key in sinc_detail_keys:
            self._set_selector_row_visible(key, is_sinc and not serial4_active)
        for key in slice_param_keys:
            self._set_selector_row_visible(key, not is_sinc and not serial4_active)
        for key in hidden_legacy_keys:
            self._set_selector_row_visible(key, False)
        for index in range(1, 7):
            self._set_selector_row_visible(f"awp1018_serial4_segment_{index}", serial4_active)

        if hasattr(self, "diff_sinc_slice_group_combo"):
            self.diff_sinc_slice_group_combo.configure(state="readonly" if is_sinc else "disabled")
        if hasattr(self, "diff_serial4_segment_combos"):
            values = self._serial4_segment_options()
            for combo, variable in zip(self.diff_serial4_segment_combos, self.diff_serial4_segment_vars):
                combo.configure(values=values, state="readonly" if serial4_active else "disabled")
                if variable.get() not in values:
                    variable.set(self._serial4_end_option())

    def _set_selector_row_visible(self, key: str, visible: bool) -> None:
        for widget in self._selector_widgets.get(key, []):
            managed = bool(widget.winfo_manager())
            if visible and not managed:
                widget.grid()
            elif not visible and managed:
                widget.grid_remove()

    def _serial4_end_option(self) -> str:
        return f"0x1F - {self.tr('awp1018_waveform_end')}"

    def _serial4_segment_options(self) -> list[str]:
        return [f"slice{index}" for index in range(31)] + [self._serial4_end_option()]

    def _parse_serial4_segment_code(self, text: str) -> int:
        value = text.strip().lower()
        if value.startswith("0x1f"):
            return 0x1F
        if value.startswith("slice"):
            code = int(value.replace("slice", ""), 10)
            if 0 <= code <= 30:
                return code
        raise ValueError(self.tr("awp1018_error_serial4_segment"))

    def _parse_diff_sinc_slice_group(self) -> tuple[int, int, int]:
        text = self.diff_sinc_slice_group_var.get().strip().lower().replace("slice", "")
        parts = text.split("-")
        if len(parts) != 2:
            raise ValueError(self.tr("awp1018_error_invalid_slice_group"))
        start = int(parts[0], 10)
        end = int(parts[1], 10)
        if end - start != 2 or not 0 <= start <= 28 or end > 30:
            raise ValueError(self.tr("awp1018_error_sinc_slice_group"))
        return start, start + 1, end

    def _rom_vpp_voltage(self) -> float:
        text = self.rom_amplitude_var.get().replace("V", "").strip()
        try:
            return float(text)
        except ValueError:
            return 0.0

    def _rom_depth_points(self) -> int:
        try:
            return int(self.rom_depth_var.get().strip(), 0)
        except ValueError:
            return 0

    def _rom_point_time_us(self) -> int:
        try:
            value = int(self.rom_point_time_var.get().strip(), 0)
            return value if 1 <= value <= 256 else 0
        except ValueError:
            return 0

    def _normal_playback_duration_us(self) -> int:
        depth = self._rom_depth_points()
        point_time = self._rom_point_time_us()
        repeat_count = int(self.rom_repeat_var.get().strip(), 0)
        return depth * point_time * max(1, repeat_count)

    def _diff_serial0_chain_includes_standalone(self) -> bool:
        return (
            bool(self.diff_auto_serial_var.get())
            and self.diff_serial_var.get() == "serial0"
            and self._parse_diff_sinc_slice_group() == (17, 18, 19)
        )

    def _diff_sinc_playback_duration_us(self) -> int:
        _max_voltage, _min_voltage, t1, t2, t3, depth = self._diff_sinc_params()
        duration_us = max(2, depth // 2) * (t1 + t2 + t3)
        if self._diff_serial0_chain_includes_standalone():
            duration_us += depth * t1
        return duration_us

    def _diff_sinc_params(self) -> tuple[float, float, int, int, int, int]:
        max_voltage = abs(float(self.diff_sinc_max_voltage_var.get().strip()))
        min_voltage = abs(float(self.diff_sinc_min_voltage_var.get().strip()))
        t1 = int(self.diff_sinc_time1_var.get().strip(), 0)
        t2 = int(self.diff_sinc_time2_var.get().strip(), 0)
        t3 = int(self.diff_sinc_time3_var.get().strip(), 0)
        depth = self._rom_depth_points()
        if not 0 < max_voltage <= 95.0:
            raise ValueError(self.tr("awp1018_error_max_voltage_range"))
        if not 0 <= min_voltage <= 95.0:
            raise ValueError(self.tr("awp1018_error_min_voltage_range"))
        if not 1 <= t1 <= 256 or not 1 <= t2 <= 256 or not 1 <= t3 <= 256:
            raise ValueError(self.tr("awp1018_error_segment_time_range"))
        if depth < 4:
            raise ValueError(self.tr("awp1018_error_waveform_depth_small"))
        return max_voltage, min_voltage, t1, t2, t3, depth

    def _update_rom_metrics(self) -> None:
        if self.diff_mode:
            self._update_diff_rom_metrics()
            return

        if self.diff_mode:
            try:
                max_voltage, min_voltage, t1, t2, t3, depth = self._diff_sinc_params()
                segment_points = max(2, depth // 2)
                period_us = segment_points * (t1 + t2 + t3)
                frequency_hz = 1_000_000.0 / period_us if period_us else 0.0
                if self.language == "zh":
                    self.rom_metric_var.set(self.tr("awp1018_diff_sinc_metric", max_voltage=f"{max_voltage:.2f}", min_voltage=f"{min_voltage:.2f}"))
                    self.rom_frequency_var.set(self.tr("awp1018_diff_frequency", period=f"{period_us:.0f}", frequency=f"{frequency_hz:.3f}"))
                else:
                    self.rom_metric_var.set(self.tr("awp1018_diff_sinc_metric", max_voltage=f"{max_voltage:.2f}", min_voltage=f"{min_voltage:.2f}"))
                    self.rom_frequency_var.set(self.tr("awp1018_diff_frequency", period=f"{period_us:.0f}", frequency=f"{frequency_hz:.3f}"))
            except Exception:
                self.rom_metric_var.set("")
                self.rom_frequency_var.set("")
            return

        amplitude = self._rom_vpp_voltage()
        period_us = self._normal_playback_duration_us()
        frequency_hz = (1_000_000.0 / period_us) if period_us else 0.0
        self.rom_metric_var.set(
            self.tr("awp1018_rom_metric", amplitude=f"{amplitude:.2f}", period=f"{period_us:.0f}")
        )
        self.rom_frequency_var.set(self.tr("awp1018_rom_frequency", frequency=f"{frequency_hz:.3f}"))
        return

        if self.language == "zh":
            self.rom_metric_var.set(self.tr("awp1018_rom_metric", amplitude=f"{amplitude:.2f}", period=f"{period_us:.0f}"))
            self.rom_frequency_var.set(self.tr("awp1018_rom_frequency", frequency=f"{frequency_hz:.3f}"))
        else:
            self.rom_metric_var.set(self.tr("awp1018_rom_metric", amplitude=f"{amplitude:.2f}", period=f"{period_us:.0f}"))
            self.rom_frequency_var.set(self.tr("awp1018_rom_frequency", frequency=f"{frequency_hz:.3f}"))

    def _update_diff_rom_metrics(self) -> None:
        if self._diff_serial4_selected():
            segments = self._generate_serial4_preview_segments()
            labels = [label for label, _values in segments]
            period_us = self._serial4_preview_period_us()
            frequency_hz = 1_000_000.0 / period_us if period_us else 0.0
            self.rom_metric_var.set(
                self.tr("awp1018_diff_serial4_metric", slices=", ".join(labels) if labels else "-")
            )
            self.rom_frequency_var.set(
                self.tr("awp1018_diff_frequency", period=f"{period_us:.0f}", frequency=f"{frequency_hz:.3f}")
            )
            return

        try:
            max_voltage, min_voltage, t1, t2, t3, depth = self._diff_sinc_params()
            waveform = self.rom_waveform_var.get()
            if waveform == "SINC":
                period_us = self._diff_sinc_playback_duration_us()
                display_voltage = self._diff_sinc_actual_vpp_voltage()
            else:
                point_time = self._rom_point_time_us()
                repeat_count = int(self.rom_repeat_var.get().strip(), 0)
                period_us = depth * point_time * max(1, repeat_count)
                display_voltage = self._diff_selected_vpp_voltage()
            frequency_hz = 1_000_000.0 / period_us if period_us else 0.0
        except Exception:
            self.rom_metric_var.set("")
            self.rom_frequency_var.set("")
            return

        if waveform == "SINC":
            self.rom_metric_var.set(
                self.tr(
                    "awp1018_diff_sinc_metric",
                    max_voltage=f"{max_voltage:.2f}",
                    min_voltage=f"{min_voltage:.2f}",
                    vpp=f"{display_voltage:.2f}",
                )
            )
        else:
            self.rom_metric_var.set(
                self.tr("awp1018_diff_waveform_metric", waveform=waveform, voltage=f"{display_voltage:.2f}")
            )
        self.rom_frequency_var.set(
            self.tr("awp1018_diff_frequency", period=f"{period_us:.0f}", frequency=f"{frequency_hz:.3f}")
        )
        return

        if self.language == "zh":
            if waveform == "SINC":
                self.rom_metric_var.set(self.tr("awp1018_diff_sinc_metric", max_voltage=f"{max_voltage:.2f}", min_voltage=f"{min_voltage:.2f}"))
            else:
                self.rom_metric_var.set(self.tr("awp1018_diff_waveform_metric", waveform=waveform, voltage=f"{display_voltage:.2f}"))
            self.rom_frequency_var.set(self.tr("awp1018_diff_frequency", period=f"{period_us:.0f}", frequency=f"{frequency_hz:.3f}"))
        else:
            if waveform == "SINC":
                self.rom_metric_var.set(self.tr("awp1018_diff_sinc_metric", max_voltage=f"{max_voltage:.2f}", min_voltage=f"{min_voltage:.2f}"))
            else:
                self.rom_metric_var.set(self.tr("awp1018_diff_waveform_metric", waveform=waveform, voltage=f"{display_voltage:.2f}"))
            self.rom_frequency_var.set(self.tr("awp1018_diff_frequency", period=f"{period_us:.0f}", frequency=f"{frequency_hz:.3f}"))

    def _add_section_title(self, parent: tk.Misc, row: int, key: str) -> int:
        is_full = self.page_name == "full_mode"
        title_options = {
            "awp1018_adc_threshold_detection": {
                "step": 3,
                "step_total": 4,
                "help_key": "awp1018_help_full_adc" if is_full else "awp1018_help_button_detection",
            },
            "full_mode_adc": {
                "step": 3,
                "step_total": 4,
                "help_key": "awp1018_help_full_adc" if is_full else "awp1018_help_button_adc",
            },
            "awp1018_waveform_selector": {
                "step": 2,
                "step_total": 4,
                "help_key": "awp1018_help_full_waveform" if is_full else "awp1018_help_button_waveform",
            },
            "full_mode_system": {
                "step": 4,
                "step_total": 4,
                "role": "feedback",
                "help_key": "awp1018_help_full_system",
            },
            "full_mode_memory": {
                "role": "advanced",
                "help_key": "awp1018_help_full_memory",
            },
            "awp1018_full_adc_readback": {
                "step": 3,
                "step_total": 4,
                "role": "verify",
                "help_key": "awp1018_help_full_adc_readback",
            },
            "full_mode_serial": {
                "role": "advanced",
                "help_key": "awp1018_help_full_serial",
            },
        }
        _title_row, _label = self.make_module_title_row(
            parent,
            key,
            **title_options.get(key, {}),
            row=row,
            columnspan=4,
            pady=(12 if row else 0, 4),
        )
        return row + 1

    def _add_entry(self, parent: tk.Misc, row: int, key: str, variable: tk.StringVar) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels[key] = label
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, columnspan=3, sticky="ew", pady=7)

    def _add_paired_entries(
        self,
        parent: tk.Misc,
        row: int,
        key1: str,
        variable1: tk.StringVar,
        key2: str,
        variable2: tk.StringVar,
    ) -> None:
        label1 = ttk.Label(parent, style="Panel.TLabel")
        label1.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels[key1] = label1
        ttk.Entry(parent, textvariable=variable1, width=10).grid(row=row, column=1, sticky="ew", pady=7)

        label2 = ttk.Label(parent, style="Panel.TLabel")
        label2.grid(row=row, column=2, sticky="w", pady=7, padx=(12, 8))
        self._labels[key2] = label2
        ttk.Entry(parent, textvariable=variable2, width=10).grid(row=row, column=3, sticky="ew", pady=7)

    def _add_combo(self, parent: tk.Misc, row: int, key: str, variable: tk.StringVar, values: list[str]) -> None:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=7, padx=(0, 8))
        self._labels[key] = label
        ttk.Combobox(parent, textvariable=variable, values=values, state="readonly").grid(
            row=row,
            column=1,
            columnspan=3,
            sticky="ew",
            pady=7,
        )

    def _slice_definition_cache(self) -> dict[int, tuple[int, int, int, int]]:
        key = f"0x{self._device_addr():02X}"
        cache = self._slice_definition_cache_root.setdefault(key, {})
        return cache

    def _remember_slice_definition(self, slice_index: int, values: tuple[int, int, int, int]) -> None:
        if not 0 <= slice_index <= 30:
            return
        self._slice_definition_cache()[slice_index] = tuple(value & 0x0FFF for value in values)

    def _export_slice_definitions(self) -> dict[str, dict[str, Any]]:
        cache = self._slice_definition_cache()
        if self.diff_mode and self._diff_serial4_selected():
            missing = [index for index in self._serial4_selected_slices() if index not in cache]
            if missing:
                names = ", ".join(f"slice{index}" for index in missing)
                raise ValueError(self.tr("awp1018_settings_missing_slices", slices=names))

        definitions: dict[str, dict[str, Any]] = {}
        for slice_index in sorted(cache):
            item: dict[str, Any] = {"words": list(cache[slice_index])}
            preview = self._diff_slice_preview_cache.get(slice_index)
            if preview:
                values = preview.get("values")
                if isinstance(values, list):
                    item["preview_values"] = [float(value) for value in values]
                item["period_us"] = float(preview.get("period_us", 0.0))
                item["waveform"] = str(preview.get("waveform", ""))
            definitions[f"slice{slice_index}"] = item
        return definitions

    def export_settings(self) -> dict[str, Any]:
        stages: dict[str, dict[str, Any]] = {}
        for stage, variables in self.stage_vars.items():
            stages[stage] = {
                "ovv_time": str(variables["ovv_time"].get()),
                "press_threshold": str(variables["high"].get()),
                "release_threshold": str(variables["low"].get()),
                "waveform": str(variables["waveform"].get()),
                "jump_condition": str(variables["jump_condition"].get()),
                "jump_target": str(variables["jump_target"].get()),
                "press_debounce": bool(variables["press_debounce_enabled"].get()),
                "release_debounce": bool(variables["release_debounce_enabled"].get()),
            }

        return {
            "stages": stages,
            "channels": [bool(variable.get()) for variable in self.channel_vars],
            "group_start_stages": [f"Stage{index}" for index in self._group_start_stage_indices()],
            "slice_definitions": self._export_slice_definitions(),
            "general": {
                "debounce_time_ms": str(self.debounce_time_var.get()),
                "adc_sample_update": str(self.adc_sample_update_var.get()),
                "zero_time": str(self.zero_time_var.get()),
                "return_bit_time": str(self.return_bit_time_var.get()),
                "press_return_bit": bool(self.return_bit_press_enable_var.get()),
                "release_return_bit_enhance": bool(self.return_bit_release_enable_var.get()),
                "release_return_bit_time_enhance": bool(self.return_bit_release_time_enhance_var.get()),
                "zps_enable": bool(self.zps_enable_var.get()),
                "nint_enable": bool(self.interrupt_enable_var.get()),
                "arbitration_code": int(self.arbitration_mode_code),
                "dac_sample_rate": str(self.dac_sample_rate_var.get()),
            },
            "waveform_selector": {
                "slice": str(self.rom_slice_var.get()),
                "waveform": str(self.rom_waveform_var.get()),
                "depth": str(self.rom_depth_var.get()),
                "point_time_us": str(self.rom_point_time_var.get()),
                "slow_up": str(self.rom_slowup_var.get()),
                "slow_down": str(self.rom_slowdown_var.get()),
                "repeat": str(self.rom_repeat_var.get()),
                "infinite_loop": bool(self.rom_infinite_loop_var.get()),
                "offset": str(self.rom_offset_var.get()),
                "amplitude": str(self.rom_amplitude_var.get()),
                "auto_serial": bool(self.diff_auto_serial_var.get()),
                "diff_serial": str(self.diff_serial_var.get()),
                "sinc_slice_group": str(self.diff_sinc_slice_group_var.get()),
                "sinc_max_voltage": str(self.diff_sinc_max_voltage_var.get()),
                "sinc_min_voltage": str(self.diff_sinc_min_voltage_var.get()),
                "sinc_time1_us": str(self.diff_sinc_time1_var.get()),
                "sinc_time2_us": str(self.diff_sinc_time2_var.get()),
                "sinc_time3_us": str(self.diff_sinc_time3_var.get()),
                "single_waveform": str(self.diff_single_waveform_var.get()),
                "play_count": str(self.diff_play_count_var.get()),
                "serial4_segments": [
                    "end" if self._parse_serial4_segment_code(str(variable.get())) == 0x1F else str(variable.get())
                    for variable in self.diff_serial4_segment_vars
                ],
            },
        }

    def _apply_decoded_page_settings(
        self,
        register_values: dict[int, int],
        descriptors: dict[int, tuple[int, int, int, int]],
    ) -> None:
        """Refresh page controls from validated register/EEPROM state without I/O."""
        normalized_registers: dict[int, int] = {}
        for raw_register, raw_value in register_values.items():
            register = int(raw_register)
            value = int(raw_value)
            if not 0 <= register <= 0xFF or not 0 <= value <= 0xFFFF:
                raise ValueError("invalid AWP1018 register snapshot")
            normalized_registers[register] = value

        normalized_descriptors: dict[int, tuple[int, int, int, int]] = {}
        for raw_slice, raw_words in descriptors.items():
            slice_index = int(raw_slice)
            words = tuple(int(word) for word in raw_words)
            if (
                not 0 <= slice_index <= 30
                or len(words) != 4
                or any(word < 0 or word > 0x0FFF for word in words)
            ):
                raise ValueError("invalid AWP1018 waveform descriptor snapshot")
            normalized_descriptors[slice_index] = words

        previous_loading_state = self._loading_settings
        original_arbitration_code = self.arbitration_mode_code
        changed_variables: list[tuple[Any, Any]] = []
        changed_variable_ids: set[int] = set()
        descriptor_cache = self._slice_definition_cache()
        original_descriptors = dict(descriptor_cache)

        def set_variable(variable: Any, value: Any) -> None:
            identity = id(variable)
            if identity not in changed_variable_ids:
                changed_variable_ids.add(identity)
                changed_variables.append((variable, variable.get()))
            variable.set(value)

        def set_named_variable(name: str, value: Any) -> None:
            variable = getattr(self, name, None)
            if variable is not None:
                set_variable(variable, value)

        cancel = getattr(type(self.ctx.serial), "cancel_register_settings", None)
        if callable(cancel):
            self.ctx.serial.cancel_register_settings("awp1018")
        self._loading_settings = True
        try:
            if 0x05 in normalized_registers:
                reg05 = normalized_registers[0x05]
                arbitration_code = (reg05 >> 2) & 0b11
                arbitration_index = self.ARBITRATION_MODE_CODES.index(arbitration_code)
                self.arbitration_mode_code = arbitration_code
                set_named_variable(
                    "arbitration_mode_var",
                    self._arbitration_mode_options()[arbitration_index],
                )
                if self.diff_mode:
                    set_named_variable("diff_auto_serial_var", bool(reg05 & (1 << 6)))

            if 0x06 in normalized_registers:
                zero_code = (normalized_registers[0x06] >> 14) & 0b11
                zero_time = next(
                    label for label, code in self.ZERO_TIME_CODES.items() if code == zero_code
                )
                set_named_variable("zero_time_var", zero_time)

            if 0x07 in normalized_registers:
                reg07 = normalized_registers[0x07]
                for channel, variable in enumerate(self.channel_vars):
                    set_variable(variable, bool(reg07 & (1 << channel)))
                set_named_variable(
                    "return_bit_time_var",
                    self.RETURN_BIT_TIME_OPTIONS[(reg07 >> 8) & 0b111],
                )
                set_named_variable("return_bit_release_time_enhance_var", bool(reg07 & (1 << 11)))
                set_named_variable("return_bit_release_enable_var", bool(reg07 & (1 << 12)))
                set_named_variable("return_bit_press_enable_var", bool(reg07 & (1 << 13)))
                zps_code = (reg07 >> 14) & 0b11
                if zps_code in (0, 0b11):
                    set_named_variable("zps_enable_var", zps_code == 0b11)

            if 0x0D in normalized_registers:
                reg0d = normalized_registers[0x0D]
                group_count = min(
                    len(variables) for variables in self.stage_group_start_vars.values()
                )
                for group in range(group_count):
                    selected_stage = (reg0d >> (group * 2)) & 0b11
                    for stage_index, stage in enumerate(("Stage0", "Stage1", "Stage2", "Stage3")):
                        set_variable(
                            self.stage_group_start_vars[stage][group],
                            stage_index == selected_stage,
                        )
                    if group < len(self.channel_start_stage_vars):
                        set_variable(
                            self.channel_start_stage_vars[group],
                            f"Stage{selected_stage}",
                        )

            if 0x22 in normalized_registers:
                reg22 = normalized_registers[0x22]
                debounce_code = reg22 & 0x03FF
                debounce_ms = round(debounce_code * 1024 / 0x03FF) if debounce_code else 0
                set_named_variable("debounce_time_var", str(debounce_ms))
                set_named_variable(
                    "adc_sample_update_var",
                    self.ADC_SAMPLE_UPDATE_OPTIONS[(reg22 >> 10) & 0b11],
                )

            for stage in ("Stage0", "Stage1", "Stage2", "Stage3"):
                variables = self.stage_vars.get(stage)
                if not variables:
                    continue
                control_register = self.STAGE_JUMP_CONTROL_REGS[stage]
                press_register = self.STAGE_PRESS_THRESHOLD_REGS[stage]
                release_register = self.STAGE_RELEASE_THRESHOLD_REGS[stage]
                waveform_register = self.STAGE_WAVEFORM_REGS[stage]

                if control_register in normalized_registers:
                    control = normalized_registers[control_register]
                    ovv_high = (control >> 12) & 0x0F
                    ovv_low = (control >> 8) & 0x0F
                    if ovv_high == ovv_low and ovv_high < len(self.ADC_OVV_OPTIONS):
                        set_variable(variables["ovv_time"], self.ADC_OVV_OPTIONS[ovv_high])

                    event_bits = control & 0x0033
                    if event_bits == 0x0011 and press_register in normalized_registers:
                        set_variable(variables["jump_condition"], "press")
                        target = (normalized_registers[press_register] >> 13) & 0b11
                        set_variable(variables["jump_target"], f"Stage{target}")
                    elif event_bits == 0x0022 and release_register in normalized_registers:
                        set_variable(variables["jump_condition"], "release")
                        target = (normalized_registers[release_register] >> 13) & 0b11
                        set_variable(variables["jump_target"], f"Stage{target}")

                if press_register in normalized_registers:
                    press_value = normalized_registers[press_register]
                    set_variable(variables["high"], str(press_value & 0x0FFF))
                    set_variable(variables["press_debounce_enabled"], bool(press_value & (1 << 15)))
                if release_register in normalized_registers:
                    release_value = normalized_registers[release_register]
                    set_variable(variables["low"], str(release_value & 0x0FFF))
                    set_variable(variables["release_debounce_enabled"], bool(release_value & (1 << 15)))
                if waveform_register in normalized_registers:
                    waveform_value = normalized_registers[waveform_register]
                    low_slice = waveform_value & 0x1F
                    high_slice = (waveform_value >> 8) & 0x1F
                    if low_slice == high_slice and low_slice <= 30:
                        set_variable(variables["waveform"], f"slice{low_slice}")

            if 0x04 in normalized_registers and 0x08 in normalized_registers:
                interrupt_route = normalized_registers[0x04] & 0x0003
                interrupt_mask = normalized_registers[0x08] & 0x00F8
                if interrupt_route == 0x0003 and interrupt_mask == 0x0058:
                    set_named_variable("interrupt_enable_var", True)
                elif interrupt_route == 0 and interrupt_mask == 0:
                    set_named_variable("interrupt_enable_var", False)

            if 0x27 in normalized_registers:
                set_named_variable("dac_sample_rate_var", str(normalized_registers[0x27]))

            if self.diff_mode and 0x29 in normalized_registers and 0x2A in normalized_registers:
                serial_codes = tuple(
                    (normalized_registers[register] >> shift) & 0x1F
                    for register in (0x29, 0x2A)
                    for shift in (0, 5, 10)
                )
                for variable, code in zip(self.diff_serial4_segment_vars, serial_codes):
                    set_variable(variable, self._serial4_end_option() if code == 0x1F else f"slice{code}")
                set_named_variable("rom_slice_var", "serial4")

            descriptor_cache.update(normalized_descriptors)
            self._apply_decoded_waveform_page_settings(normalized_descriptors, set_variable)

            self.refresh_language()
            self._refresh_rom_depth_options()
            if self.diff_mode:
                self._refresh_diff_slice_group_options()
                self._refresh_diff_waveform_controls()
            self._show_stage(self.active_stage.get())
            self._draw_rom_waveform()
        except Exception:
            for variable, original_value in reversed(changed_variables):
                variable.set(original_value)
            self.arbitration_mode_code = original_arbitration_code
            descriptor_cache.clear()
            descriptor_cache.update(original_descriptors)
            raise
        finally:
            self._loading_settings = previous_loading_state

    def _apply_decoded_waveform_page_settings(
        self,
        descriptors: dict[int, tuple[int, int, int, int]],
        set_variable: Callable[[Any, Any], None],
    ) -> None:
        if not descriptors or not hasattr(self, "rom_slice_var"):
            return
        if self.diff_mode and self.rom_slice_var.get() == "serial4":
            return

        selected_slice: int | None = None
        stage0 = self.stage_vars.get("Stage0", {})
        waveform_variable = stage0.get("waveform")
        if waveform_variable is not None:
            selected_slice = self._parse_slice_index(str(waveform_variable.get()))

        if self.diff_mode:
            for start in (16, 17, 20, 21, 24, 25, 28):
                slices = (start, start + 1, start + 2)
                if any(slice_index not in descriptors for slice_index in slices):
                    continue
                words = [descriptors[slice_index] for slice_index in slices]
                phases = tuple((item[0] >> 6) & 0b11 for item in words)
                formats = tuple((item[0] >> 10) & 0b11 for item in words)
                if phases != (0b10, 0b01, 0b10) or formats != (0b10, 0b10, 0b10):
                    continue
                if selected_slice is not None and selected_slice not in slices:
                    continue

                serial_index = 0 if start < 20 else 1 if start < 24 else 2 if start < 28 else 3
                set_variable(self.diff_auto_serial_var, True)
                set_variable(self.diff_serial_var, f"serial{serial_index}")
                set_variable(self.rom_waveform_var, "SINC")
                set_variable(self.rom_slice_var, f"slice{start}")
                set_variable(self.diff_sinc_slice_group_var, f"slice{start}-slice{start + 2}")
                depth_code = (words[0][1] >> 8) & 0b111
                set_variable(self.rom_depth_var, str(1024 >> depth_code))
                for variable, item in zip(
                    (self.diff_sinc_time1_var, self.diff_sinc_time2_var, self.diff_sinc_time3_var),
                    words,
                ):
                    set_variable(variable, str((item[1] & 0xFF) + 1))

                first_amplitude = self._amplitude_code_to_vpk_voltage(words[0][3] & 0x1F)
                first_offset = self._offset_code_to_voltage((words[0][3] >> 5) & 0x7F)
                second_amplitude = self._amplitude_code_to_vpk_voltage(words[1][3] & 0x1F)
                second_offset = self._offset_code_to_voltage((words[1][3] >> 5) & 0x7F)
                max_voltage = max(0.0, first_offset + first_amplitude)
                min_voltage = abs(min(0.0, second_offset - second_amplitude))
                set_variable(self.diff_sinc_max_voltage_var, f"{max_voltage:.4f}".rstrip("0").rstrip("."))
                set_variable(self.diff_sinc_min_voltage_var, f"{min_voltage:.4f}".rstrip("0").rstrip("."))
                return

        if selected_slice is None or selected_slice not in descriptors:
            if len(descriptors) != 1:
                return
            selected_slice = next(iter(descriptors))
        word0, word1, word2, word3 = descriptors[selected_slice]
        if ((word0 >> 10) & 0b11) != 0b10 or (word0 & 0x00C0):
            return
        waveform_code = (word0 >> 8) & 0b11
        waveform = next(
            (name for name, code in self.ROM_WAVEFORM_CODES.items() if code == waveform_code),
            None,
        )
        if waveform is None:
            return

        set_variable(self.rom_slice_var, f"slice{selected_slice}")
        set_variable(self.rom_waveform_var, waveform)
        depth_start = 512 if waveform == "Gauss" else 1024
        set_variable(self.rom_depth_var, str(depth_start >> ((word1 >> 8) & 0b111)))
        set_variable(self.rom_point_time_var, str((word1 & 0xFF) + 1))
        set_variable(self.rom_infinite_loop_var, bool(word1 & (1 << 11)))
        set_variable(self.rom_slowup_var, self.ROM_SLOW_OPTIONS[(word2 >> 10) & 0b11])
        set_variable(self.rom_slowdown_var, self.ROM_SLOW_OPTIONS[(word2 >> 8) & 0b11])
        set_variable(self.rom_repeat_var, str((word2 & 0xFF) + 1))
        offset_options = self.DIFF_ROM_OFFSET_OPTIONS if self.diff_mode else self.ROM_OFFSET_OPTIONS
        amplitude_options = (
            self.DIFF_ROM_AMPLITUDE_OPTIONS if self.diff_mode else self.ROM_AMPLITUDE_OPTIONS
        )
        set_variable(self.rom_offset_var, offset_options[(word3 >> 5) & 0x7F])
        set_variable(self.rom_amplitude_var, amplitude_options[word3 & 0x1F])

    def import_settings(self, data: dict[str, Any], *, apply_to_chip: bool = True) -> bool:
        settings = self._validate_imported_settings(data)
        stages = settings["stages"]
        general = settings["general"]
        waveform = settings["waveform_selector"]

        self._loading_settings = True
        try:
            for stage, values in stages.items():
                variables = self.stage_vars[stage]
                variables["ovv_time"].set(values["ovv_time"])
                variables["high"].set(values["press_threshold"])
                variables["low"].set(values["release_threshold"])
                variables["waveform"].set(values["waveform"])
                variables["jump_condition"].set(values["jump_condition"])
                variables["jump_target"].set(values["jump_target"])
                variables["press_debounce_enabled"].set(values["press_debounce"])
                variables["release_debounce_enabled"].set(values["release_debounce"])

            for variable, enabled in zip(self.channel_vars, settings["channels"]):
                variable.set(enabled)

            for group, stage_name in enumerate(settings["group_start_stages"]):
                for stage in ("Stage0", "Stage1", "Stage2", "Stage3"):
                    self.stage_group_start_vars[stage][group].set(stage == stage_name)
                if group < len(self.channel_start_stage_vars):
                    self.channel_start_stage_vars[group].set(stage_name)

            self._pending_import_slice_definitions = dict(settings["slice_definitions"])
            definition_cache = self._slice_definition_cache()
            for slice_index, item in self._pending_import_slice_definitions.items():
                definition_cache[slice_index] = tuple(item["words"])
                if "preview_values" in item:
                    self._diff_slice_preview_cache[slice_index] = {
                        "label": f"slice{slice_index}",
                        "values": list(item["preview_values"]),
                        "period_us": float(item.get("period_us", 0.0)),
                        "waveform": str(item.get("waveform", "")),
                    }

            self.debounce_time_var.set(general["debounce_time_ms"])
            self.adc_sample_update_var.set(general["adc_sample_update"])
            self.zero_time_var.set(general["zero_time"])
            self.return_bit_time_var.set(general["return_bit_time"])
            self.return_bit_press_enable_var.set(general["press_return_bit"])
            self.return_bit_release_enable_var.set(general["release_return_bit_enhance"])
            self.return_bit_release_time_enhance_var.set(general["release_return_bit_time_enhance"])
            self.zps_enable_var.set(general["zps_enable"])
            self.interrupt_enable_var.set(general["nint_enable"])
            self.arbitration_mode_code = general["arbitration_code"]
            self.dac_sample_rate_var.set(general["dac_sample_rate"])

            self.diff_auto_serial_var.set(waveform["auto_serial"])
            self.diff_serial_var.set(waveform["diff_serial"])
            self.rom_waveform_var.set(waveform["waveform"])
            self.rom_slice_var.set(waveform["slice"])
            self.diff_sinc_slice_group_var.set(waveform["sinc_slice_group"])
            self.rom_depth_var.set(waveform["depth"])
            self.rom_point_time_var.set(waveform["point_time_us"])
            self.rom_slowup_var.set(waveform["slow_up"])
            self.rom_slowdown_var.set(waveform["slow_down"])
            self.rom_repeat_var.set(waveform["repeat"])
            self.rom_infinite_loop_var.set(waveform["infinite_loop"])
            self.rom_offset_var.set(waveform["offset"])
            self.rom_amplitude_var.set(waveform["amplitude"])
            self.diff_sinc_max_voltage_var.set(waveform["sinc_max_voltage"])
            self.diff_sinc_min_voltage_var.set(waveform["sinc_min_voltage"])
            self.diff_sinc_time1_var.set(waveform["sinc_time1_us"])
            self.diff_sinc_time2_var.set(waveform["sinc_time2_us"])
            self.diff_sinc_time3_var.set(waveform["sinc_time3_us"])
            self.diff_single_waveform_var.set(waveform["single_waveform"])
            self.diff_play_count_var.set(waveform["play_count"])
            for variable, value in zip(self.diff_serial4_segment_vars, waveform["serial4_segments"]):
                variable.set(self._serial4_end_option() if value == "end" else value)
        finally:
            self._loading_settings = False

        self.refresh_language()
        self._refresh_rom_depth_options()
        if self.diff_mode:
            self._refresh_diff_slice_group_options()
            self._refresh_diff_waveform_controls()
        self._show_stage(self.active_stage.get())
        self._draw_rom_waveform()
        return self._write_imported_settings_to_chip() if apply_to_chip else True

    def _normalize_imported_amplitude(self, value: Any) -> str:
        text = str(value)
        if not self.diff_mode and text in self.LEGACY_NORMAL_AMPLITUDE_OPTIONS:
            code = self.LEGACY_NORMAL_AMPLITUDE_OPTIONS.index(text)
            return self.ROM_AMPLITUDE_OPTIONS[code]
        choices = self.DIFF_ROM_AMPLITUDE_OPTIONS if self.diff_mode else self.ROM_AMPLITUDE_OPTIONS
        if text not in choices:
            raise ValueError("amplitude")
        return text

    def _validate_imported_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("settings")

        def require_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
            value = parent.get(key)
            if not isinstance(value, dict):
                raise ValueError(key)
            return value

        def require_bool(parent: dict[str, Any], key: str) -> bool:
            value = parent.get(key)
            if not isinstance(value, bool):
                raise ValueError(key)
            return value

        def require_choice(parent: dict[str, Any], key: str, choices: list[str] | tuple[str, ...]) -> str:
            value = parent.get(key)
            if not isinstance(value, str) or value not in choices:
                raise ValueError(key)
            return value

        def require_int(parent: dict[str, Any], key: str, minimum: int, maximum: int) -> str:
            value = parent.get(key)
            if isinstance(value, bool):
                raise ValueError(key)
            try:
                parsed = int(str(value).strip(), 0)
            except Exception as exc:
                raise ValueError(key) from exc
            if not minimum <= parsed <= maximum:
                raise ValueError(key)
            return str(parsed)

        stages_data = require_dict(data, "stages")
        stages: dict[str, dict[str, Any]] = {}
        for stage in ("Stage0", "Stage1", "Stage2", "Stage3"):
            source = require_dict(stages_data, stage)
            stages[stage] = {
                "ovv_time": require_choice(source, "ovv_time", self.ADC_OVV_OPTIONS),
                "press_threshold": require_int(source, "press_threshold", 0, 0x0FFF),
                "release_threshold": require_int(source, "release_threshold", 0, 0x0FFF),
                "waveform": require_choice(source, "waveform", self._slice_options()),
                "jump_condition": require_choice(source, "jump_condition", ("press", "release")),
                "jump_target": require_choice(source, "jump_target", ("Stage0", "Stage1", "Stage2", "Stage3")),
                "press_debounce": require_bool(source, "press_debounce"),
                "release_debounce": require_bool(source, "release_debounce"),
            }

        channels_data = data.get("channels")
        if not isinstance(channels_data, list) or len(channels_data) != len(self.channel_vars):
            raise ValueError("channels")
        channels = []
        for value in channels_data:
            if not isinstance(value, bool):
                raise ValueError("channels")
            channels.append(value)

        group_start_data = data.get("group_start_stages")
        if group_start_data is None:
            group_start_stages = ["Stage0"] * len(self.channel_vars)
        else:
            if not isinstance(group_start_data, list) or len(group_start_data) != len(self.channel_vars):
                raise ValueError("group_start_stages")
            group_start_stages = []
            for value in group_start_data:
                if value not in ("Stage0", "Stage1", "Stage2", "Stage3"):
                    raise ValueError("group_start_stages")
                group_start_stages.append(value)

        slice_definitions_data = data.get("slice_definitions", {})
        if not isinstance(slice_definitions_data, dict):
            raise ValueError("slice_definitions")
        slice_definitions: dict[int, dict[str, Any]] = {}
        for key, item in slice_definitions_data.items():
            if not isinstance(key, str) or not key.lower().startswith("slice") or not isinstance(item, dict):
                raise ValueError("slice_definitions")
            try:
                slice_index = int(key[5:], 10)
            except ValueError as exc:
                raise ValueError("slice_definitions") from exc
            words = item.get("words")
            if not 0 <= slice_index <= 30 or not isinstance(words, list) or len(words) != 4:
                raise ValueError("slice_definitions")
            normalized_words: list[int] = []
            for word in words:
                if isinstance(word, bool) or not isinstance(word, int) or not 0 <= word <= 0x0FFF:
                    raise ValueError("slice_definitions")
                normalized_words.append(word)

            normalized_item: dict[str, Any] = {"words": tuple(normalized_words)}
            preview_values = item.get("preview_values")
            if preview_values is not None:
                if not isinstance(preview_values, list) or len(preview_values) > 2048:
                    raise ValueError("slice_definitions")
                normalized_preview: list[float] = []
                for value in preview_values:
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ValueError("slice_definitions")
                    normalized_preview.append(float(value))
                try:
                    period_us = float(item.get("period_us", 0.0))
                except Exception as exc:
                    raise ValueError("slice_definitions") from exc
                if period_us < 0:
                    raise ValueError("slice_definitions")
                normalized_item["preview_values"] = normalized_preview
                normalized_item["period_us"] = period_us
                normalized_item["waveform"] = str(item.get("waveform", ""))
            slice_definitions[slice_index] = normalized_item

        general_data = require_dict(data, "general")
        arbitration_code = general_data.get("arbitration_code")
        if isinstance(arbitration_code, bool) or arbitration_code not in self.ARBITRATION_MODE_CODES:
            raise ValueError("arbitration_code")
        general = {
            "debounce_time_ms": require_int(general_data, "debounce_time_ms", 0, 1024),
            "adc_sample_update": require_choice(general_data, "adc_sample_update", self.ADC_SAMPLE_UPDATE_OPTIONS),
            "zero_time": require_choice(general_data, "zero_time", self.ZERO_TIME_OPTIONS),
            "return_bit_time": require_choice(general_data, "return_bit_time", self.RETURN_BIT_TIME_OPTIONS),
            "press_return_bit": require_bool(general_data, "press_return_bit"),
            "release_return_bit_enhance": require_bool(general_data, "release_return_bit_enhance"),
            "release_return_bit_time_enhance": require_bool(general_data, "release_return_bit_time_enhance"),
            "zps_enable": require_bool(general_data, "zps_enable"),
            "nint_enable": require_bool(general_data, "nint_enable"),
            "arbitration_code": int(arbitration_code),
            "dac_sample_rate": require_int(general_data, "dac_sample_rate", 0, 0xFFFF),
        }

        waveform_data = require_dict(data, "waveform_selector")
        auto_serial = require_bool(waveform_data, "auto_serial")
        waveform_choices = ["SIN", "Gauss", "DC"]
        if self.diff_mode and auto_serial:
            waveform_choices.insert(0, "SINC")
        waveform_type = require_choice(waveform_data, "waveform", waveform_choices)
        slice_choices = self._diff_slice_options() if self.diff_mode else [f"slice{index}" for index in range(31)]
        slice_value = require_choice(waveform_data, "slice", slice_choices)
        depth_start = 512 if waveform_type == "Gauss" else 1024
        depth_choices = [str(depth_start >> code) for code in range(8)]
        diff_serial = require_choice(waveform_data, "diff_serial", ("serial0", "serial1", "serial2", "serial3"))
        group_choices = {
            "serial0": ("slice16-slice18", "slice17-slice19"),
            "serial1": ("slice20-slice22", "slice21-slice23"),
            "serial2": ("slice24-slice26", "slice25-slice27"),
            "serial3": ("slice28-slice30",),
        }[diff_serial]
        offset_choices = self.DIFF_ROM_OFFSET_OPTIONS if self.diff_mode else self.ROM_OFFSET_OPTIONS
        serial4_data = waveform_data.get("serial4_segments")
        if not isinstance(serial4_data, list) or len(serial4_data) != 6:
            raise ValueError("serial4_segments")
        serial4_segments = []
        for value in serial4_data:
            if not isinstance(value, str) or value not in [f"slice{index}" for index in range(31)] + ["end"]:
                raise ValueError("serial4_segments")
            serial4_segments.append(value)

        def require_float_text(key: str, minimum: float, maximum: float) -> str:
            value = waveform_data.get(key)
            try:
                parsed = float(str(value).strip())
            except Exception as exc:
                raise ValueError(key) from exc
            if not minimum <= parsed <= maximum:
                raise ValueError(key)
            return str(value).strip()

        waveform = {
            "slice": slice_value,
            "waveform": waveform_type,
            "depth": require_choice(waveform_data, "depth", depth_choices),
            "point_time_us": require_int(waveform_data, "point_time_us", 1, 256),
            "slow_up": require_choice(waveform_data, "slow_up", self.ROM_SLOW_OPTIONS),
            "slow_down": require_choice(waveform_data, "slow_down", self.ROM_SLOW_OPTIONS),
            "repeat": require_int(waveform_data, "repeat", 1, 256),
            "infinite_loop": require_bool(waveform_data, "infinite_loop"),
            "offset": require_choice(waveform_data, "offset", offset_choices),
            "amplitude": self._normalize_imported_amplitude(waveform_data.get("amplitude")),
            "auto_serial": auto_serial,
            "diff_serial": diff_serial,
            "sinc_slice_group": require_choice(waveform_data, "sinc_slice_group", group_choices),
            "sinc_max_voltage": require_float_text("sinc_max_voltage", 0.01, 95.0),
            "sinc_min_voltage": require_float_text("sinc_min_voltage", 0.0, 95.0),
            "sinc_time1_us": require_int(waveform_data, "sinc_time1_us", 1, 256),
            "sinc_time2_us": require_int(waveform_data, "sinc_time2_us", 1, 256),
            "sinc_time3_us": require_int(waveform_data, "sinc_time3_us", 1, 256),
            "single_waveform": require_choice(waveform_data, "single_waveform", ("SINC", "SIN", "Gauss", "DC")),
            "play_count": require_int(waveform_data, "play_count", 1, 256),
            "serial4_segments": serial4_segments,
        }
        if self.diff_mode and slice_value == "serial4":
            referenced_slices: list[int] = []
            for value in serial4_segments:
                if value == "end":
                    break
                referenced_slices.append(int(value[5:], 10))
            missing = [index for index in referenced_slices if index not in slice_definitions]
            if missing:
                names = ", ".join(f"slice{index}" for index in missing)
                raise ValueError(self.tr("awp1018_settings_missing_slices", slices=names))
        return {
            "stages": stages,
            "channels": channels,
            "group_start_stages": group_start_stages,
            "slice_definitions": slice_definitions,
            "general": general,
            "waveform_selector": waveform,
        }

    def _write_imported_slice_definitions(self) -> bool:
        if not self._pending_import_slice_definitions:
            return True
        mode_reg = self.read_reg(0x05)
        if mode_reg is None:
            return False
        if not self.write_reg(0x05, (mode_reg & ~0x0003) | 0x0003):
            return False

        success = True
        for slice_index in sorted(self._pending_import_slice_definitions):
            words = tuple(self._pending_import_slice_definitions[slice_index]["words"])
            if not self._write_rom_slice_descriptor(slice_index, words):
                success = False
                break

        current_mode = self.read_reg(0x05)
        restored = current_mode is not None and self.write_reg(0x05, (current_mode & ~0x0003) | 0x0002)
        return success and restored

    def _write_imported_settings_to_chip(self) -> bool:
        if not self._write_imported_slice_definitions():
            return False
        if not self.apply_rom_waveform_selection(show_success=False):
            return False

        reg05 = self.read_reg(0x05)
        if reg05 is None:
            return False
        reg05 = (reg05 & ~((0b11 << 2) | (1 << 6))) | ((self.arbitration_mode_code & 0b11) << 2)
        if self.diff_mode and self.diff_auto_serial_var.get():
            reg05 |= 1 << 6
        if not self.write_reg(0x05, reg05):
            return False

        zero_code = self.ZERO_TIME_CODES[self.zero_time_var.get()]
        reg06 = self.read_reg(0x06)
        if reg06 is None or not self.write_reg(0x06, (reg06 & ~(0b11 << 14)) | (zero_code << 14)):
            return False

        channel_mask = 0
        for index, variable in enumerate(self.channel_vars):
            if variable.get():
                channel_mask |= 1 << index
        time_code = self.RETURN_BIT_TIME_OPTIONS.index(self.return_bit_time_var.get())
        reg07 = (channel_mask & 0x00FF) | ((time_code & 0b111) << 8)
        if self.return_bit_release_time_enhance_var.get():
            reg07 |= 1 << 11
        if self.return_bit_release_enable_var.get():
            reg07 |= 1 << 12
        if self.return_bit_press_enable_var.get():
            reg07 |= 1 << 13
        if self.zps_enable_var.get():
            reg07 |= 0b11 << 14
        if not self.write_reg(0x07, reg07):
            return False

        debounce_ms = int(self.debounce_time_var.get().strip(), 0)
        debounce_code = round(debounce_ms * 0x03FF / 1024) if debounce_ms else 0
        sample_code = self.ADC_SAMPLE_UPDATE_OPTIONS.index(self.adc_sample_update_var.get())
        reg22 = self.read_reg(0x22)
        if reg22 is None:
            return False
        reg22 = (reg22 & ~0x0FFF) | (debounce_code & 0x03FF) | ((sample_code & 0b11) << 10)
        if not self.write_reg(0x22, reg22):
            return False

        if not self.write_reg(0x27, int(self.dac_sample_rate_var.get().strip(), 0)):
            return False
        if not self._configure_interrupt(bool(self.interrupt_enable_var.get())):
            return False
        return self._sync_stage_defaults_before_run()

    def _build_normal_waveform_descriptor(self) -> tuple[int, tuple[int, int, int, int]]:
        slice_index = self._parse_slice_index(self.rom_slice_var.get())
        if slice_index is None or not 0 <= slice_index <= 30:
            raise ValueError(self.tr("awp1018_error_slice_range"))
        waveform_code = self.ROM_WAVEFORM_CODES[self.rom_waveform_var.get()]
        depth_code = self._rom_depth_options().index(self.rom_depth_var.get())
        point_time = int(self.rom_point_time_var.get().strip(), 0)
        repeat_count = int(self.rom_repeat_var.get().strip(), 0)
        offset_code = self.ROM_OFFSET_OPTIONS.index(self.rom_offset_var.get())
        amplitude_code = self.ROM_AMPLITUDE_OPTIONS.index(self.rom_amplitude_var.get())
        if not 1 <= point_time <= 256:
            raise ValueError(self.tr("awp1018_error_point_time_range"))
        if not 1 <= repeat_count <= 256:
            raise ValueError(self.tr("awp1018_error_repeat_range"))
        words = (
            (0b10 << 10) | ((waveform_code & 0b11) << 8),
            ((1 if self.rom_infinite_loop_var.get() else 0) << 11)
            | ((depth_code & 0b111) << 8)
            | ((point_time - 1) & 0xFF),
            (self._parse_rom_slow_code(self.rom_slowup_var.get()) << 10)
            | (self._parse_rom_slow_code(self.rom_slowdown_var.get()) << 8)
            | ((repeat_count - 1) & 0xFF),
            ((offset_code & 0x7F) << 5) | (amplitude_code & 0x1F),
        )
        return slice_index, tuple(value & 0x0FFF for value in words)

    def apply_rom_waveform_selection(self, *, show_success: bool = True) -> bool:
        if self.diff_mode:
            return self._apply_diff_sinc_waveform_selection(show_success=show_success)

        try:
            slice_index, words = self._build_normal_waveform_descriptor()
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return False

        mode_reg = self.read_reg(0x05)
        if mode_reg is None:
            return False
        if not self.write_reg(0x05, (mode_reg & ~0x0003) | 0x0003):
            return False

        sram_reg = self.read_reg(0x28)
        if sram_reg is None:
            return False
        sram_addr = (slice_index * 4) & 0x03FF
        if not self.write_reg(0x28, (sram_reg & ~0x03FF) | sram_addr):
            return False

        for value in words:
            if not self.write_reg(0x00, value & 0x0FFF):
                return False
        self._remember_slice_definition(slice_index, words)

        mode_reg = self.read_reg(0x05)
        if mode_reg is None:
            return False
        if not self.write_reg(0x05, (mode_reg & ~0x0003) | 0x0002):
            return False
        if not self.write_reg(0x26, 0x0010):
            return False
        if not self.write_reg(0x0E, 0x0010):
            return False
        if not self._write_waveform_selection_fields(0x12, slice_index):
            return False

        name = self.rom_waveform_var.get()
        self.ctx.state.extra["awp1018_rom_waveform"] = name
        if show_success:
            messagebox.showinfo(self.tr("info"), self.tr("write_success"))
        return True

    def _apply_diff_sinc_waveform_selection(self, *, show_success: bool = True) -> bool:
        if self._diff_serial4_selected():
            if not self._ensure_connected():
                return False
            if not self._write_serial4_segment_registers():
                return False
            self._draw_rom_waveform()
            if show_success:
                messagebox.showinfo(self.tr("info"), self.tr("write_success"))
            return True

        try:
            descriptors = self._build_diff_waveform_descriptors()
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return False

        if not self._ensure_connected():
            return False

        mode_reg = self.read_reg(0x05)
        if mode_reg is None:
            return False
        if not self.write_reg(0x05, (mode_reg & ~0x0003) | 0x0003):
            return False
        for slice_index, values in descriptors:
            if not self._write_rom_slice_descriptor(slice_index, values):
                return False
        mode_reg = self.read_reg(0x05)
        if mode_reg is None:
            return False
        if not self.write_reg(0x05, (mode_reg & ~0x0003) | 0x0002):
            return False

        self._cache_diff_preview_segments()
        self._draw_rom_waveform()
        if show_success:
            messagebox.showinfo(self.tr("info"), self.tr("write_success"))
        return True

    def _build_diff_waveform_descriptors(self) -> list[tuple[int, tuple[int, int, int, int]]]:
        waveform = self.rom_waveform_var.get()
        if waveform == "SINC":
            descriptors = self._build_diff_sinc_descriptors()
            standalone = self._build_diff_standalone_descriptor()
            return ([standalone] if standalone is not None else []) + descriptors
        return self._build_diff_simple_waveform_descriptors(waveform)

    def _diff_serial_slices(self) -> list[int]:
        serial = self.diff_serial_var.get()
        if serial == "serial0":
            return [16, 17, 18, 19]
        if serial == "serial1":
            return [20, 21, 22, 23]
        if serial == "serial2":
            return [24, 25, 26, 27]
        if serial == "serial3":
            return [28, 29, 30]
        return self._serial4_selected_slices()

    def _serial4_selected_slices(self) -> list[int]:
        slices: list[int] = []
        for variable in self.diff_serial4_segment_vars:
            code = self._parse_serial4_segment_code(variable.get())
            if code == 0x1F:
                break
            slices.append(code)
        return slices

    def _diff_serial4_selected(self) -> bool:
        return self.rom_slice_var.get() == "serial4"

    def _write_serial4_segment_registers(self) -> bool:
        try:
            codes = [self._parse_serial4_segment_code(variable.get()) for variable in self.diff_serial4_segment_vars]
        except Exception as exc:
            messagebox.showerror(self.tr("error"), f"{self.tr('invalid_input')}\n{exc}")
            return False

        reg29_current = self.read_reg(0x29)
        if reg29_current is None:
            return False
        reg2a_current = self.read_reg(0x2A)
        if reg2a_current is None:
            return False

        reg29, reg2a = self._compose_serial4_register_values(
            tuple(codes),
            reg29_current,
            reg2a_current,
        )
        return self.write_reg(0x29, reg29) and self.write_reg(0x2A, reg2a)

    @staticmethod
    def _compose_serial4_register_values(
        codes: tuple[int, ...],
        reg29_base: int,
        reg2a_base: int,
    ) -> tuple[int, int]:
        if len(codes) != 6 or any(int(code) < 0 or int(code) > 0x1F for code in codes):
            raise ValueError("invalid AWP1018 Serial4 segment configuration")
        reg29 = (
            (int(reg29_base) & 0x8000)
            | ((int(codes[0]) & 0x1F) << 0)
            | ((int(codes[1]) & 0x1F) << 5)
            | ((int(codes[2]) & 0x1F) << 10)
        )
        reg2a = (
            (int(reg2a_base) & 0x8000)
            | ((int(codes[3]) & 0x1F) << 0)
            | ((int(codes[4]) & 0x1F) << 5)
            | ((int(codes[5]) & 0x1F) << 10)
        )
        return reg29 & 0xFFFF, reg2a & 0xFFFF

    def _cache_diff_preview_segments(self) -> None:
        if not self.diff_mode or self._diff_serial4_selected():
            return

        waveform = self.rom_waveform_var.get()
        try:
            if waveform == "SINC":
                slice_group = self._parse_diff_sinc_slice_group()
                segments = self._generate_diff_sinc_segments()
                t1, t2, t3 = self._diff_sinc_times()
                for slice_index, segment, point_time in zip(slice_group, segments, (t1, t2, t3)):
                    self._diff_slice_preview_cache[slice_index] = {
                        "label": f"slice{slice_index}",
                        "values": list(segment),
                        "period_us": len(segment) * point_time,
                        "waveform": waveform,
                    }
                return

            slice_index = self._parse_slice_index(self.rom_slice_var.get())
            if slice_index is None:
                return
            segments = self._generate_diff_preview_segments()
            if not segments:
                return
            depth = self._rom_depth_points()
            point_time = self._rom_point_time_us()
            repeat_count = max(1, int(self.rom_repeat_var.get().strip(), 0))
            _label, values = segments[0]
            self._diff_slice_preview_cache[slice_index] = {
                "label": f"slice{slice_index}",
                "values": list(values),
                "period_us": depth * point_time * repeat_count,
                "waveform": waveform,
            }
        except Exception:
            return

    def _generate_serial4_preview_segments(self) -> list[tuple[str, list[float]]]:
        segments: list[tuple[str, list[float]]] = []
        for slice_index in self._serial4_selected_slices():
            item = self._diff_slice_preview_cache.get(slice_index)
            if not item:
                segments.append((f"slice{slice_index}", []))
                continue
            values = item.get("values", [])
            if not isinstance(values, list):
                values = []
            segments.append((str(item.get("label", f"slice{slice_index}")), [float(value) for value in values]))
        return segments

    def _serial4_preview_period_us(self) -> float:
        period = 0.0
        for slice_index in self._serial4_selected_slices():
            item = self._diff_slice_preview_cache.get(slice_index)
            if not item:
                continue
            try:
                period += float(item.get("period_us", 0.0))
            except Exception:
                continue
        return period

    def _build_diff_simple_waveform_descriptors(self, waveform: str) -> list[tuple[int, tuple[int, int, int, int]]]:
        if waveform not in {"SIN", "DC", "Gauss"}:
            raise ValueError(self.tr("awp1018_error_unsupported_diff_waveform"))
        if self._diff_serial4_selected():
            return []
        slice_index = self._parse_slice_index(self.rom_slice_var.get())
        if slice_index is None or not 0 <= slice_index <= 30:
            raise ValueError(self.tr("awp1018_error_slice_range"))
        point_time = int(self.rom_point_time_var.get().strip(), 0)
        repeat_count = int(self.rom_repeat_var.get().strip(), 0)
        if not 1 <= point_time <= 256:
            raise ValueError(self.tr("awp1018_error_point_time_range"))
        if not 1 <= repeat_count <= 256:
            raise ValueError(self.tr("awp1018_error_repeat_range"))
        depth_code = self._rom_depth_options().index(self.rom_depth_var.get())
        waveform_code = self.ROM_WAVEFORM_CODES[waveform]
        vpk_code = self._diff_selected_amplitude_code()
        offset_code = self._diff_selected_offset_code()
        first = (0b10 << 10) | ((waveform_code & 0b11) << 8)
        second = ((1 if self.rom_infinite_loop_var.get() else 0) << 11) | ((depth_code & 0b111) << 8) | ((point_time - 1) & 0xFF)
        third = (
            (self._parse_rom_slow_code(self.rom_slowup_var.get()) << 10)
            | (self._parse_rom_slow_code(self.rom_slowdown_var.get()) << 8)
            | ((repeat_count - 1) & 0xFF)
        )
        fourth = ((offset_code & 0x7F) << 5) | (vpk_code & 0x1F)
        return [(slice_index, (first, second, third, fourth))]

    def _build_diff_sinc_descriptors(self) -> list[tuple[int, tuple[int, int, int, int]]]:
        t1, t2, t3 = self._diff_sinc_times()
        depth_code = self._rom_depth_options().index(self.rom_depth_var.get())

        actual_segments = self._diff_sinc_actual_segments()
        slice0, slice1, slice2 = self._parse_diff_sinc_slice_group()
        segment_settings = (
            (slice0, *actual_segments[0][1:], t1, 0b10, 0),
            (slice1, *actual_segments[1][1:], t2, 0b01, 0),
            (slice2, *actual_segments[2][1:], t3, 0b10, 0),
        )

        descriptors: list[tuple[int, tuple[int, int, int, int]]] = []
        for slice_index, vpk_code, _vpk_actual, offset_code, _offset_actual, point_time, phase_code, loop_code in segment_settings:
            first = (0b10 << 10) | ((phase_code & 0b11) << 6)
            second = ((depth_code & 0b111) << 8) | ((point_time - 1) & 0xFF)
            third = loop_code & 0xFF
            fourth = ((offset_code & 0x7F) << 5) | (vpk_code & 0x1F)
            descriptors.append((slice_index, (first, second, third, fourth)))
        return descriptors

    def _diff_standalone_slice(self) -> int | None:
        try:
            group = set(self._parse_diff_sinc_slice_group())
            free_slices = [slice_index for slice_index in self._diff_serial_slices() if slice_index not in group]
        except Exception:
            return None
        return free_slices[0] if len(free_slices) == 1 else None

    def _diff_selected_offset_code(self) -> int:
        text = self.rom_offset_var.get().strip()
        try:
            return max(0, min(127, int(text.split(":", 1)[0], 0)))
        except Exception:
            return self._voltage_to_offset_code(0.0)

    def _diff_selected_amplitude_code(self) -> int:
        text = self.rom_amplitude_var.get().replace("V", "").strip()
        try:
            return self._vpp_voltage_to_amplitude_code(float(text))
        except Exception:
            return self._vpp_voltage_to_amplitude_code(95.0)

    def _diff_selected_vpp_voltage(self) -> float:
        return self._amplitude_code_to_vpp_voltage(self._diff_selected_amplitude_code())

    def _diff_selected_vpk_voltage(self) -> float:
        return self._amplitude_code_to_vpk_voltage(self._diff_selected_amplitude_code())

    def _build_diff_standalone_descriptor(
        self,
    ) -> tuple[int, tuple[int, int, int, int]] | None:
        slice_index = self._diff_standalone_slice()
        if slice_index is None:
            return None
        max_voltage, _min_voltage, point_time, _t2, _t3, _depth = self._diff_sinc_params()
        depth_code = self._rom_depth_options().index(self.rom_depth_var.get())
        offset_code = 0
        vpk_code = self._vpp_voltage_to_amplitude_code(max_voltage)
        first = (0b10 << 10) | ((self.ROM_WAVEFORM_CODES["SIN"] & 0b11) << 8)
        second = ((depth_code & 0b111) << 8) | ((point_time - 1) & 0xFF)
        third = 0
        fourth = ((offset_code & 0x7F) << 5) | (vpk_code & 0x1F)
        return slice_index, (first, second, third, fourth)

    def _diff_sinc_times(self) -> tuple[int, int, int]:
        _max_voltage, _min_voltage, t1, t2, t3, _depth = self._diff_sinc_params()
        return t1, t2, t3

    def _diff_sinc_actual_segments(self) -> list[tuple[int, int, float, int, float]]:
        try:
            cache_key = (
                self.diff_sinc_max_voltage_var.get(),
                self.diff_sinc_min_voltage_var.get(),
            )
        except Exception:
            cache_key = None
        cached = getattr(self, "_sinc_segment_cache", None)
        if cache_key is not None and cached is not None and cached[0] == cache_key:
            return cached[1]
        segments = self._compute_diff_sinc_actual_segments()
        if cache_key is not None:
            self._sinc_segment_cache = (cache_key, segments)
        return segments

    def _compute_diff_sinc_actual_segments(self) -> list[tuple[int, int, float, int, float]]:
        max_voltage, min_voltage, _t1, _t2, _t3, _depth = self._diff_sinc_params()
        signed_min = -abs(min_voltage)

        vpk1_code = self._vpp_voltage_to_amplitude_code(max_voltage)
        vpk1_actual = self._amplitude_code_to_vpk_voltage(vpk1_code)
        offset1_code = self._best_offset_code_for_segment(vpk1_actual, 0.0, vpk1_actual * 2.0, rising=True)
        offset1_actual = self._offset_code_to_voltage(offset1_code)
        first_end = offset1_actual + vpk1_actual

        vpk2_code = self._vpp_voltage_to_amplitude_code(first_end - signed_min)
        vpk2_actual = self._amplitude_code_to_vpk_voltage(vpk2_code)
        offset2_code = self._best_offset_code_for_segment(vpk2_actual, first_end, signed_min, rising=False)
        offset2_actual = self._offset_code_to_voltage(offset2_code)
        second_end = offset2_actual - vpk2_actual

        vpk3_code, offset3_code = self._best_rising_segment_codes(second_end, 0.0)
        vpk3_actual = self._amplitude_code_to_vpk_voltage(vpk3_code)
        offset3_actual = self._offset_code_to_voltage(offset3_code)

        return [
            (17, vpk1_code, vpk1_actual, offset1_code, offset1_actual),
            (18, vpk2_code, vpk2_actual, offset2_code, offset2_actual),
            (19, vpk3_code, vpk3_actual, offset3_code, offset3_actual),
        ]

    def _diff_sinc_actual_vpp_voltage(self) -> float:
        endpoints: list[float] = []
        for _slice, _vpk_code, vpk, _offset_code, offset in self._diff_sinc_actual_segments():
            endpoints.extend((offset - vpk, offset + vpk))
        return max(endpoints) - min(endpoints) if endpoints else 0.0

    def _best_offset_code_for_segment(self, amplitude: float, target_start: float, target_end: float, *, rising: bool) -> int:
        best_code = 0
        best_score = float("inf")
        for code in range(128):
            offset = self._offset_code_to_voltage(code)
            start = offset - amplitude if rising else offset + amplitude
            end = offset + amplitude if rising else offset - amplitude
            score = abs(start - target_start) + abs(end - target_end)
            if score < best_score:
                best_score = score
                best_code = code
        return best_code

    def _best_rising_segment_codes(self, target_start: float, target_end: float) -> tuple[int, int]:
        desired_amplitude = abs(target_end - target_start) / 2.0
        best_vpk_code = 0
        best_offset_code = 0
        best_score = float("inf")
        for vpk_code in range(32):
            amplitude = self._amplitude_code_to_vpk_voltage(vpk_code)
            offset_code = self._best_offset_code_for_segment(amplitude, target_start, target_end, rising=True)
            offset = self._offset_code_to_voltage(offset_code)
            start = offset - amplitude
            end = offset + amplitude
            score = (abs(start - target_start) + abs(end - target_end)) * 10.0 + abs(amplitude - desired_amplitude)
            if score < best_score:
                best_score = score
                best_vpk_code = vpk_code
                best_offset_code = offset_code
        return best_vpk_code, best_offset_code

    def _vpp_voltage_to_amplitude_code(self, voltage: float) -> int:
        voltage = max(0.0, min(190.0, abs(float(voltage))))
        code = round(voltage / 5.9375) - 1
        return max(0, min(31, code))

    def _amplitude_code_to_vpp_voltage(self, code: int) -> float:
        code = max(0, min(31, int(code)))
        return (code + 1) * 5.9375

    def _amplitude_code_to_vpk_voltage(self, code: int) -> float:
        return self._amplitude_code_to_vpp_voltage(code) / 2.0

    def _voltage_to_offset_code(self, voltage: float) -> int:
        voltage = max(-93.515625, min(95.0, float(voltage)))
        if abs(voltage) < 0.37:
            return 0
        if voltage < 0:
            return max(1, min(63, round(abs(voltage) / 1.484375)))
        return max(64, min(127, 128 - round(voltage / 1.484375)))

    def _offset_code_to_voltage(self, code: int) -> float:
        code = max(0, min(127, int(code)))
        if code == 0:
            return 0.0
        if code <= 63:
            return -(code * 1.484375)
        return (128 - code) * 1.484375

    def _write_rom_slice_descriptor(self, slice_index: int, values: tuple[int, int, int, int]) -> bool:
        sram_reg = self.read_reg(0x28)
        if sram_reg is None:
            return False
        sram_addr = (slice_index * 4) & 0x03FF
        if not self.write_reg(0x28, (sram_reg & ~0x03FF) | sram_addr):
            return False
        for value in values:
            if not self.write_reg(0x00, value & 0x0FFF):
                return False
        self._remember_slice_definition(slice_index, values)
        return True

    def _parse_rom_slow_code(self, value: str) -> int:
        return int(value.strip()[:2], 2)

    def _device_addr(self) -> int:
        return int(self.ctx.state.extra.get("awp1018_device_addr", self.ctx.state.awp192x_device_addr)) & 0xFF

    def _ensure_connected(self, *, show_error: bool = True) -> bool:
        if self.ctx.serial.is_connected:
            return True
        if show_error:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
        return False

    def write_reg(self, reg: int, value: int, show_error: bool = True) -> bool:
        if self._loading_settings:
            return True
        if not self._ensure_connected(show_error=show_error):
            return False
        resp = self.ctx.serial.write_register_value(self._device_addr(), reg, value, bits=16)
        if not resp.ok and show_error:
            messagebox.showerror(self.tr("error"), resp.error)
        return resp.ok

    def write_reg_async(self, reg: int, value: int, *, on_success: Any = None) -> None:
        if not self._ensure_connected():
            return

        dev = self._device_addr()

        def finish(ok: bool, error: str) -> None:
            if not ok:
                messagebox.showerror(self.tr("error"), error)
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
        if self._loading_settings:
            return 0
        if not self._ensure_connected(show_error=show_error):
            return None
        address = self._device_addr()
        resp = self.ctx.serial.read_register_value(address, reg, bits=16)
        if not resp.ok:
            if show_error:
                messagebox.showerror(self.tr("error"), resp.error)
            return None
        return self.ctx.serial.extract_rdataw_value(
            resp.data,
            device_addr=address,
            reg_addr=reg,
            bits=16,
        )

    def _set_board_preset_controls_state(self, state: str) -> None:
        if self.board_slot_combo is not None:
            self.board_slot_combo.configure(state="disabled" if state == "disabled" else "readonly")
        for button in self._board_preset_buttons.values():
            button.configure(state=state)

    def _poll_board_preset_results(self) -> None:
        try:
            result, action, detail = self._board_preset_result_queue.get_nowait()
        except queue.Empty:
            if self._board_preset_poll_active:
                self.after(50, self._poll_board_preset_results)
            return

        self._board_preset_poll_active = False
        self._set_board_preset_controls_state("normal")
        if result == "error":
            error = detail or "unknown"
            if action != "save":
                messagebox.showerror(self.tr("error"), error)
                return
            messagebox.showerror(
                self.tr("error"),
                self.tr("board_preset_snapshot_failed", error=error),
            )
            return
        if action == "save":
            messagebox.showinfo(self.tr("info"), self.tr("board_preset_saved"))
        elif action == "read":
            try:
                summary, device_addr, register_values, descriptors = detail
                self.sync_decoded_settings(device_addr, register_values, descriptors)
            except Exception as exc:
                messagebox.showerror(self.tr("error"), str(exc))
                return
            messagebox.showinfo(self.tr("info"), summary)
        else:
            messagebox.showinfo(
                self.tr("info"),
                self.tr("board_preset_read").format(slot=detail or ""),
            )

    def _start_board_preset_operation(
        self,
        action: str,
        worker: Callable[[], Any],
    ) -> None:
        if self._board_preset_poll_active:
            return
        cancel = getattr(type(self.ctx.serial), "cancel_register_settings", None)
        if callable(cancel):
            self.ctx.serial.cancel_register_settings("awp1018")
        self._set_board_preset_controls_state("disabled")
        self._board_preset_poll_active = True
        self.after(50, self._poll_board_preset_results)

        def run_worker() -> None:
            try:
                detail = worker()
            except Exception as exc:
                self._board_preset_result_queue.put(("error", action, str(exc)))
                return
            self._board_preset_result_queue.put(("ok", action, detail))

        threading.Thread(target=run_worker, daemon=True).start()

    def save_board_preset(self) -> None:
        if not self._ensure_connected():
            return
        if self.page_name == "full_mode":
            return

        slot = int(self.board_slot_var.get())
        address = self._device_addr()
        try:
            gui_state = self._capture_adc_preset_state()
            preflight_model = self._build_adc_preset_model(
                gui_state,
                {register: 0 for register in AWP1018_ADC_FIXED_REGISTERS},
            )
        except Exception as exc:
            messagebox.showerror(self.tr("error"), self.tr("board_preset_snapshot_failed", error=str(exc)))
            return

        def worker() -> None:
            from core.arduino_preset_sync import (
                PAYLOAD_TYPE_AWP1018_ADC_STAGE4,
                V2Preset,
                pack_v2_preset,
                signature_for_address,
            )

            def read_register(register: int) -> int | None:
                resp = self.ctx.serial.read_register_value(address, register, bits=16)
                if not resp.ok:
                    return None
                return self.ctx.serial.extract_rdataw_value(
                    resp.data,
                    device_addr=address,
                    reg_addr=register,
                    bits=16,
                )

            with self.ctx.serial.snapshot_transaction():
                merge_values = {
                    register: 0
                    for register in AWP1018_ADC_FIXED_REGISTERS
                }
                merge_registers = self._board_preset_merge_registers(
                    preflight_model.include_stage23
                )
                for register in merge_registers:
                    value = read_register(register)
                    if value is None:
                        raise ValueError(f"failed to read AWP1018 register 0x{register:02X}")
                    merge_values[register] = value
                preset = self._build_adc_preset_model(gui_state, merge_values)
                payload = pack_v2_preset(
                    V2Preset(
                        PAYLOAD_TYPE_AWP1018_ADC_STAGE4,
                        signature_for_address(address),
                        address,
                        pack_awp1018_adc_stage4_payload(preset),
                    )
                )
                response = self.ctx.serial.sync_config_preset(slot, payload, set_active=False)
                if not response.ok:
                    raise ValueError(response.error or "upload failed")
                readback = self.ctx.serial.cfg_read(slot)
                if not readback.ok:
                    raise ValueError(readback.error or "EEPROM readback failed")
                raw_hex = self.ctx.serial._extract_cfgread_hex(readback.data)
                if raw_hex is None or bytes.fromhex(raw_hex) != payload:
                    raise ValueError("EEPROM readback does not match uploaded preset")

        self._start_board_preset_operation("save", worker)

    def set_board_preset_active(self) -> None:
        if not self._ensure_connected():
            return
        slot = int(self.board_slot_var.get())

        def worker() -> str:
            resp = self.ctx.serial.cfg_use(slot)
            if not resp.ok:
                raise ValueError(resp.error or "CFGUSE failed")
            return str(slot)

        self._start_board_preset_operation("set_active", worker)

    def apply_board_preset(self) -> None:
        if not self._ensure_connected():
            return
        slot = int(self.board_slot_var.get())

        def worker() -> str:
            resp = self.ctx.serial.cfg_apply(slot)
            if not resp.ok:
                raise ValueError(resp.error or "CFGAPPLY failed")
            return str(slot)

        self._start_board_preset_operation("apply", worker)

    def read_board_preset(self) -> None:
        if not self._ensure_connected():
            return
        slot = int(self.board_slot_var.get())
        invalid_message = self.tr("board_preset_read_invalid")

        def worker() -> tuple[
            str,
            int,
            dict[int, int],
            dict[int, tuple[int, int, int, int]],
        ]:
            from core.arduino_preset_sync import format_preset_summary

            resp = self.ctx.serial.cfg_read(slot)
            if not resp.ok:
                raise ValueError(resp.error or "CFGREAD failed")
            raw_hex = self.ctx.serial._extract_cfgread_hex(resp.data)
            if raw_hex is None:
                raise ValueError(invalid_message)
            try:
                raw = bytes.fromhex(raw_hex)
                summary = format_preset_summary(raw)
                device_addr, register_values, descriptors = (
                    decode_awp1018_board_preset_page_settings(raw)
                )
                return summary, device_addr, register_values, descriptors
            except ValueError as exc:
                raise ValueError(f"{invalid_message}\n{exc}") from exc

        self._start_board_preset_operation("read", worker)

    def _queue_general_setting(self, register: int, value: int, clear_mask: int | None = None) -> bool:
        # Standalone editors/legacy transports retain their existing synchronous
        # contract. The application's router can pin the board for worker I/O.
        if not callable(getattr(type(self.ctx.serial), "queue_register_setting", None)):
            return False
        if not self._loading_settings:
            self.ctx.serial.queue_register_setting(
                "awp1018", self._device_addr(), register, value, clear_mask=clear_mask,
                address_state_key="awp1018_device_addr",
            )
        return True

    def _on_zero_time_changed(self) -> None:
        code = self.ZERO_TIME_CODES.get(self.zero_time_var.get())
        if code is None:
            return
        if self._queue_general_setting(0x06, (code & 0b11) << 14, 0b11 << 14):
            return
        current = self.read_reg(0x06, show_error=False)
        if current is None:
            return
        value = (current & ~(0b11 << 14)) | ((code & 0b11) << 14)
        self.write_reg(0x06, value, show_error=False)

    def _on_dac_sample_rate_changed(self) -> None:
        try:
            value = int(self.dac_sample_rate_var.get().strip(), 0)
            if not 0 <= value <= 0xFFFF:
                raise ValueError
        except ValueError:
            return

        self.ctx.state.single_point_duration_us = value
        if self._queue_general_setting(0x27, value):
            return
        self.write_reg(0x27, value, show_error=False)

    def _on_debounce_time_changed(self) -> None:
        try:
            time_value = int(self.debounce_time_var.get().strip(), 0)
            if not 0 <= time_value <= 1024:
                raise ValueError
        except ValueError:
            return

        code = round(time_value * 0x03FF / 1024) if time_value else 0
        code = max(0, min(0x03FF, code))

        if self._queue_general_setting(0x22, code, 0x03FF):
            return
        current = self.read_reg(0x22, show_error=False)
        if current is None:
            return
        value = (current & ~0x03FF) | code
        self.write_reg(0x22, value, show_error=False)

    def _on_diff_auto_serial_changed(self) -> None:
        self._refresh_diff_waveform_controls()
        current = self.read_reg(0x05, show_error=False)
        if current is None:
            return
        mask = 1 << 6
        value = (current | mask) if self.diff_auto_serial_var.get() else (current & ~mask)
        self.write_reg(0x05, value, show_error=False)

    def _on_adc_sample_update_changed(self) -> None:
        try:
            code = self.ADC_SAMPLE_UPDATE_OPTIONS.index(self.adc_sample_update_var.get())
        except ValueError:
            return

        if self._queue_general_setting(0x22, (code & 0b11) << 10, 0b11 << 10):
            return
        current = self.read_reg(0x22, show_error=False)
        if current is None:
            return
        value = (current & ~(0b11 << 10)) | ((code & 0b11) << 10)
        self.write_reg(0x22, value, show_error=False)

    def _on_channel_select_changed(self) -> None:
        mask = 0
        for index, variable in enumerate(self.channel_vars):
            if variable.get():
                mask |= 1 << index

        current = self.read_reg(0x07, show_error=False)
        if current is None:
            return
        value = (current & ~0x00FF) | (mask & 0x00FF)
        self.write_reg(0x07, value, show_error=False)
        if self.diff_mode and self._diff_detect_active:
            self._set_diff_ready_colors()
        if self.diff_mode and self._diff_sample_active:
            self._refresh_diff_sample_channels()
            self._set_diff_ready_colors()

    def _on_return_bit_config_changed(self) -> None:
        try:
            time_code = self.RETURN_BIT_TIME_OPTIONS.index(self.return_bit_time_var.get())
        except ValueError:
            return

        current = self.read_reg(0x07, show_error=False)
        if current is None:
            return

        value = current & ~((1 << 13) | (1 << 12) | (1 << 11) | (0b111 << 8))
        if self.return_bit_press_enable_var.get():
            value |= 1 << 13
        if self.return_bit_release_enable_var.get():
            value |= 1 << 12
        if self.return_bit_release_time_enhance_var.get():
            value |= 1 << 11
        value |= (time_code & 0b111) << 8
        self.write_reg(0x07, value, show_error=False)

    def _arbitration_mode_options(self) -> list[str]:
        return [self.tr(key) for key in self.ARBITRATION_MODE_KEYS]

    def _on_arbitration_mode_changed(self, _event: tk.Event | None = None) -> None:
        try:
            index = self._arbitration_mode_options().index(self.arbitration_mode_var.get())
            code = self.ARBITRATION_MODE_CODES[index]
        except (ValueError, IndexError):
            return

        self.arbitration_mode_code = code
        current = self.read_reg(0x05, show_error=False)
        if current is None:
            return
        value = (current & ~(0b11 << 2)) | ((code & 0b11) << 2)
        self.write_reg(0x05, value, show_error=False)

    def _on_zps_enable_changed(self) -> None:
        current = self.read_reg(0x07, show_error=False)
        if current is None:
            return

        value = current & ~(0b11 << 14)
        if self.zps_enable_var.get():
            value |= 0b11 << 14
        self.write_reg(0x07, value, show_error=False)

    def _on_interrupt_enable_changed(self) -> None:
        enabled = bool(self.interrupt_enable_var.get())
        if not self._configure_interrupt(enabled):
            if enabled:
                self.interrupt_enable_var.set(False)

    def _configure_interrupt(self, enabled: bool) -> bool:
        reg04 = self.read_reg(0x04)
        if reg04 is None:
            return False
        reg08 = self.read_reg(0x08)
        if reg08 is None:
            return False

        next04, next08 = self._compose_interrupt_register_values(enabled, reg04, reg08)
        return self.write_reg(0x04, next04) and self.write_reg(0x08, next08)

    @staticmethod
    def _compose_interrupt_register_values(
        enabled: bool,
        reg04_base: int,
        reg08_base: int,
    ) -> tuple[int, int]:
        if enabled:
            next04 = int(reg04_base) | 0x0003
            next08 = (int(reg08_base) & ~0x00F8) | (1 << 3) | (0x5 << 4)
        else:
            next04 = int(reg04_base) & ~0x0003
            next08 = int(reg08_base) & ~0x00F8
        return next04 & 0xFFFF, next08 & 0xFFFF

    def _handle_nint_serial_event(self) -> None:
        reg02 = self.read_reg(0x02, show_error=False)
        reg03 = self.read_reg(0x03, show_error=False)
        channel_values = self._read_selected_diff_channel_adc_values()
        if reg02 is None or reg03 is None:
            failed = []
            if reg02 is None:
                failed.append("0x02")
            if reg03 is None:
                failed.append("0x03")
            self.ctx.serial.emit_log(f"AWP1018 nINT event: failed to read {','.join(failed)}")
            return
        channel_text = " ".join(f"CH{channel}={value}" for channel, value in channel_values)
        channel_suffix = f" {channel_text}" if channel_text else ""
        self.ctx.serial.emit_log(
            f"AWP1018 nINT event: reg0x02=0x{reg02 & 0xFFFF:04X} "
            f"reg0x03=0x{reg03 & 0xFFFF:04X}{channel_suffix}"
        )

    def _on_stage_threshold_changed(self, stage: str, kind: str) -> None:
        vars_for_stage = self.stage_vars.get(stage, {})
        variable = vars_for_stage.get("high" if kind == "press" else "low")
        reg_map = self.STAGE_PRESS_THRESHOLD_REGS if kind == "press" else self.STAGE_RELEASE_THRESHOLD_REGS
        reg = reg_map.get(stage)
        if variable is None or reg is None:
            return

        try:
            threshold = int(variable.get().strip(), 0)
            if not 0 <= threshold <= 0x0FFF:
                raise ValueError
        except ValueError:
            return

        current = self.read_reg(reg, show_error=False)
        if current is None:
            return
        value = (current & ~0x0FFF) | (threshold & 0x0FFF)
        self.write_reg(reg, value, show_error=False)

    def _on_stage_debounce_enable_changed(self, stage: str, kind: str) -> None:
        vars_for_stage = self.stage_vars.get(stage, {})
        variable = vars_for_stage.get("press_debounce_enabled" if kind == "press" else "release_debounce_enabled")
        reg_map = self.STAGE_PRESS_THRESHOLD_REGS if kind == "press" else self.STAGE_RELEASE_THRESHOLD_REGS
        reg = reg_map.get(stage)
        if variable is None or reg is None:
            return

        current = self.read_reg(reg, show_error=False)
        if current is None:
            return

        enabled = bool(variable.get())
        value = (current | (1 << 15)) if enabled else (current & ~(1 << 15))
        self.write_reg(reg, value, show_error=False)

    def _parse_slice_index(self, value: str) -> int | None:
        text = value.strip().lower()
        if text == "serial":
            return 0x1F
        try:
            if text.startswith("slice"):
                index = int(text.replace("slice", "", 1), 10)
            else:
                index = int(text, 0)
        except ValueError:
            return None
        if not 0 <= index <= 30:
            return None
        return index

    def _write_waveform_selection_fields(self, reg: int, index: int, *, show_error: bool = True) -> bool:
        current = self.read_reg(reg, show_error=show_error)
        if current is None:
            return False
        value = (current & ~(0x001F | (0x001F << 8))) | (index & 0x001F) | ((index & 0x001F) << 8)
        return self.write_reg(reg, value, show_error=show_error)

    def _on_stage_ovv_time_changed(self, stage: str) -> None:
        vars_for_stage = self.stage_vars.get(stage, {})
        variable = vars_for_stage.get("ovv_time")
        reg = self.STAGE_JUMP_CONTROL_REGS.get(stage)
        if variable is None or reg is None:
            return

        try:
            code = self.ADC_OVV_OPTIONS.index(str(variable.get()))
        except ValueError:
            return

        current = self.read_reg(reg, show_error=False)
        if current is None:
            return
        value = (current & ~0xFF00) | ((code & 0x0F) << 12) | ((code & 0x0F) << 8)
        self.write_reg(reg, value, show_error=False)

    def _on_stage_waveform_changed(self, stage: str) -> None:
        vars_for_stage = self.stage_vars.get(stage, {})
        variable = vars_for_stage.get("waveform")
        reg = self.STAGE_WAVEFORM_REGS.get(stage)
        if variable is None or reg is None:
            return

        index = self._parse_slice_index(str(variable.get()))
        if index is None:
            return

        self._write_waveform_selection_fields(reg, index, show_error=False)

    def _parse_stage_index(self, value: str) -> int | None:
        text = value.strip().lower()
        if not text.startswith("stage"):
            return None
        try:
            index = int(text.replace("stage", "", 1), 10)
        except ValueError:
            return None
        if not 0 <= index <= 3:
            return None
        return index

    def _on_stage_jump_changed(self, stage: str) -> None:
        vars_for_stage = self.stage_vars.get(stage, {})
        condition_var = vars_for_stage.get("jump_condition")
        target_var = vars_for_stage.get("jump_target")
        control_reg = self.STAGE_JUMP_CONTROL_REGS.get(stage)
        if condition_var is None or target_var is None or control_reg is None:
            return

        condition = str(condition_var.get())
        self._update_stage_threshold_entry_states(stage)
        target_index = self._parse_stage_index(str(target_var.get()))
        if condition not in {"press", "release"} or target_index is None:
            return

        current_control = self.read_reg(control_reg, show_error=False)
        if current_control is not None:
            press_mask = (1 << 4) | (1 << 0)
            release_mask = (1 << 5) | (1 << 1)
            event_mask = press_mask if condition == "press" else release_mask
            control_value = (current_control & ~(press_mask | release_mask)) | event_mask
            self.write_reg(control_reg, control_value, show_error=False)

        threshold_reg_map = self.STAGE_PRESS_THRESHOLD_REGS if condition == "press" else self.STAGE_RELEASE_THRESHOLD_REGS
        threshold_reg = threshold_reg_map.get(stage)
        if threshold_reg is None:
            return

        current_threshold = self.read_reg(threshold_reg, show_error=False)
        if current_threshold is None:
            return

        condition_bit = 0 if condition == "press" else 1
        threshold_value = (current_threshold & ~0x7000) | ((condition_bit & 0x01) << 12) | ((target_index & 0x03) << 13)
        self.write_reg(threshold_reg, threshold_value, show_error=False)

    def _update_stage_threshold_entry_states(self, stage: str) -> None:
        vars_for_stage = self.stage_vars.get(stage, {})
        condition_var = vars_for_stage.get("jump_condition")
        entries = self._stage_entries.get(stage, {})
        debounce_checks = self._stage_debounce_checks.get(stage, {})
        condition = str(condition_var.get()) if condition_var is not None else "press"
        press_state = "normal" if condition == "press" else "disabled"
        release_state = "normal" if condition == "release" else "disabled"
        if "press" in entries:
            entries["press"].configure(state=press_state)
        if "release" in entries:
            entries["release"].configure(state=release_state)
        if "press" in debounce_checks:
            debounce_checks["press"].configure(state=press_state)
        if "release" in debounce_checks:
            debounce_checks["release"].configure(state=release_state)

    def _set_diff_channel_color(self, channel: int, color: str) -> None:
        frame = self._diff_status_frames.get(channel)
        if frame is None:
            return
        frame.configure(bg=color, highlightbackground=self.colors["border"])
        for child in frame.winfo_children():
            try:
                child.configure(bg=color)
            except Exception:
                pass

    def _set_all_diff_channel_colors(self, color: str) -> None:
        for channel in range(4):
            self._set_diff_channel_color(channel, color)

    def _enabled_diff_channels(self) -> list[int]:
        return [
            channel
            for channel, variable in enumerate(self.channel_vars[:4])
            if bool(variable.get())
        ]

    def _set_diff_ready_colors(self) -> None:
        enabled = set(self._enabled_diff_channels())
        for channel in range(4):
            color = self.STATUS_READY_COLOR if channel in enabled else self.STATUS_IDLE_COLOR
            self._set_diff_channel_color(channel, color)

    def _start_diff_status_monitor(self) -> None:
        if not self.diff_mode:
            return
        self._diff_sample_active = True
        self._refresh_diff_sample_channels()
        self._set_diff_ready_colors()
        self._cancel_diff_status_jobs()
        self._diff_sample_generation += 1
        generation = self._diff_sample_generation
        cancel = threading.Event()
        self._diff_sample_cancel = cancel
        address = self._device_addr()
        board_identity = self._board_identity()
        serial = self._diff_sample_transport()
        self._set_button_selected("awp1018_realtime_display", True)
        self._diff_value_poll_job = self.after(50, self._poll_diff_sample_results)
        self._diff_sample_thread = threading.Thread(
            target=self._diff_sample_loop,
            args=(cancel, generation, address, board_identity, serial),
            daemon=True,
        )
        self._diff_sample_thread.start()

    def _diff_sample_transport(self) -> Any:
        serial = self.ctx.serial
        selected_port = getattr(serial, "selected_port_for_page", None)
        manager_for_port = getattr(serial, "manager_for_port", None)
        if callable(selected_port) and callable(manager_for_port):
            try:
                port = selected_port("awp1018")
            except TypeError:
                port = selected_port()
            manager = manager_for_port(port)
            if manager is not None:
                return manager
        return serial

    def _stop_diff_status_monitor(self) -> None:
        if not self.diff_mode:
            return
        self._diff_detect_active = False
        self._diff_sample_active = False
        self._cancel_diff_status_jobs()
        self._set_button_selected("awp1018_realtime_display", False)
        self._set_all_diff_channel_colors(self.STATUS_IDLE_COLOR)

    def _cancel_diff_status_jobs(self) -> None:
        cancel = self._diff_sample_cancel
        if cancel is not None:
            cancel.set()
        for job_attr in ("_diff_value_poll_job", "_diff_status_blink_job"):
            job = getattr(self, job_attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, job_attr, None)

    def _refresh_diff_sample_channels(self) -> None:
        channels = tuple(self._enabled_diff_channels())
        with self._diff_sample_channels_lock:
            self._diff_sample_channels = channels

    def _publish_diff_sample_result(self, generation: int, status: str, payload: Any) -> None:
        try:
            self._diff_status_result_queue.put_nowait((generation, status, payload))
        except queue.Full:
            try:
                self._diff_status_result_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._diff_status_result_queue.put_nowait((generation, status, payload))
            except queue.Full:
                pass

    def _diff_sample_loop(
        self,
        cancel: threading.Event,
        generation: int,
        address: int,
        board_identity: tuple[Any, ...],
        serial: Any,
    ) -> None:
        while not cancel.is_set():
            if not serial.is_connected or self._board_identity() != board_identity:
                self._publish_diff_sample_result(generation, "disconnected", None)
                return
            with self._diff_sample_channels_lock:
                channels = self._diff_sample_channels
            values: list[tuple[int, int | None]] = []
            transaction_factory = getattr(serial, "snapshot_transaction", None)
            for channel in channels:
                if cancel.is_set():
                    return
                transaction = transaction_factory() if callable(transaction_factory) else nullcontext()
                try:
                    with transaction:
                        if cancel.is_set():
                            return
                        select_value = (channel & 0x03) << 13
                        write_response = serial.write_register_value(
                            address,
                            0x24,
                            select_value,
                            bits=16,
                        )
                        if not write_response.ok:
                            values.append((channel, None))
                            if not serial.is_connected:
                                self._publish_diff_sample_result(generation, "disconnected", None)
                                return
                            continue
                        read_response = serial.read_register_value(address, 0x24, bits=16)
                        if not read_response.ok:
                            values.append((channel, None))
                            if not serial.is_connected:
                                self._publish_diff_sample_result(generation, "disconnected", None)
                                return
                            continue
                        raw_value = serial.extract_rdataw_value(
                            read_response.data,
                            device_addr=address,
                            reg_addr=0x24,
                            bits=16,
                        )
                        if raw_value is None:
                            values.append((channel, None))
                            continue
                        values.append((channel, self._adc12_to_signed(raw_value & 0x0FFF)))
                except Exception:
                    if not cancel.is_set():
                        self._publish_diff_sample_result(generation, "error", None)
                    return
            if cancel.is_set():
                return
            if not serial.is_connected or self._board_identity() != board_identity:
                self._publish_diff_sample_result(generation, "disconnected", None)
                return
            self._publish_diff_sample_result(generation, "values", values)
            cancel.wait(0.12)

    def _poll_diff_sample_results(self) -> None:
        self._diff_value_poll_job = None
        if not self._diff_sample_active:
            return
        latest: tuple[int, str, Any] | None = None
        while True:
            try:
                latest = self._diff_status_result_queue.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            generation, status, payload = latest
            if generation == self._diff_sample_generation:
                if status == "disconnected":
                    self.stop_realtime_display()
                    return
                if status == "error":
                    for channel in self._enabled_diff_channels():
                        self._set_diff_channel_color(channel, self.STATUS_TIMEOUT_COLOR)
                    self.stop_realtime_display()
                    return
                elif status == "values":
                    current_channels = set(self._enabled_diff_channels())
                    for channel in range(4):
                        if channel not in current_channels:
                            self._set_diff_channel_color(channel, self.STATUS_IDLE_COLOR)
                    for channel, value in payload:
                        if channel not in current_channels:
                            continue
                        if value is None:
                            self._set_diff_channel_color(channel, self.STATUS_TIMEOUT_COLOR)
                            continue
                        value_var = self._diff_status_value_vars.get(channel)
                        if value_var is not None:
                            value_var.set(str(value))
                        self._set_diff_channel_color(channel, self.STATUS_READY_COLOR)
        if not self.ctx.serial.is_connected:
            self.stop_realtime_display()
            return
        if self._diff_sample_active:
            self._diff_value_poll_job = self.after(50, self._poll_diff_sample_results)

    def _read_selected_diff_channel_adc_values(self) -> list[tuple[int, int]]:
        values: list[tuple[int, int]] = []
        for channel in self._enabled_diff_channels():
            value_var = self._diff_status_value_vars.get(channel)
            if value_var is None:
                continue
            try:
                values.append((channel, int(value_var.get(), 10)))
            except ValueError:
                continue
        return values

    @staticmethod
    def _adc12_to_signed(value: int) -> int:
        value &= 0x0FFF
        return value - 0x1000 if value & 0x0800 else value

    def _handle_serial_status_log(self, line: str) -> None:
        try:
            post_to_ui(self, self._process_serial_status_log, line)
        except Exception:
            pass

    def _process_serial_status_log(self, line: str) -> None:
        self._process_interrupt_status_log(line)
        if self.diff_mode:
            self._process_diff_status_log(line)

    def _process_interrupt_status_log(self, line: str) -> None:
        if not self.interrupt_enable_var.get():
            return
        text = line.upper()
        if "MCU I REG_DATA=" in text:
            self._handle_nint_serial_event()

    def _process_diff_status_log(self, line: str) -> None:
        text = line.upper()
        if "RELEASE TIMEOUT R" in text or "ERROR" in text:
            for channel in self._enabled_diff_channels():
                self._set_diff_channel_color(channel, self.STATUS_TIMEOUT_COLOR)
            return
        if not self._diff_detect_active:
            return

        payload = self._extract_mcu_text_payload(line)
        if payload == "P":
            self._blink_diff_channel_status(self.STATUS_PRESS_COLOR)
        elif payload == "R":
            self._blink_diff_channel_status(self.STATUS_RELEASE_COLOR)

    def _extract_mcu_text_payload(self, line: str) -> str:
        marker = "MCU_TEXT"
        upper = line.upper()
        index = upper.find(marker)
        if index < 0:
            return ""
        return upper[index + len(marker):].strip()

    def _blink_diff_channel_status(self, color: str) -> None:
        if self._diff_status_blink_job is not None:
            try:
                self.after_cancel(self._diff_status_blink_job)
            except Exception:
                pass
        for channel in self._enabled_diff_channels():
            self._set_diff_channel_color(channel, color)
        self._diff_status_blink_job = self.after(180, self._restore_diff_ready_after_blink)

    def _restore_diff_ready_after_blink(self) -> None:
        self._diff_status_blink_job = None
        if self._diff_detect_active or self._diff_sample_active:
            self._set_diff_ready_colors()

    def _sync_stage_defaults_before_run(self) -> bool:
        if not self._ensure_connected():
            return False

        if not self._write_group_start_register():
            return False

        for stage in ("Stage0", "Stage1", "Stage2", "Stage3"):
            if not self._sync_stage_to_chip(stage):
                return False

        return True

    def _sync_stage_to_chip(self, stage: str) -> bool:
        control_reg = self.STAGE_JUMP_CONTROL_REGS.get(stage)
        press_reg = self.STAGE_PRESS_THRESHOLD_REGS.get(stage)
        release_reg = self.STAGE_RELEASE_THRESHOLD_REGS.get(stage)
        waveform_reg = self.STAGE_WAVEFORM_REGS.get(stage)
        if control_reg is None or press_reg is None or release_reg is None or waveform_reg is None:
            return False

        try:
            stage_state = self._capture_stage_state(stage)
        except Exception:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return False

        current_control = self.read_reg(control_reg)
        if current_control is None:
            return False
        current_waveform = self.read_reg(waveform_reg)
        if current_waveform is None:
            return False
        try:
            control_value, press_value, release_value, waveform_value = (
                self._compose_stage_register_values(
                    stage_state,
                    current_control,
                    current_waveform,
                )
            )
        except ValueError:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return False
        return (
            self.write_reg(control_reg, control_value)
            and self.write_reg(press_reg, press_value)
            and self.write_reg(release_reg, release_value)
            and self.write_reg(waveform_reg, waveform_value)
        )

    def start_detection(self) -> None:
        if not self._sync_stage_defaults_before_run():
            return
        if self.write_reg(0x02, 0x0001):
            self._set_button_selected("start_detection", True)
            if self.diff_mode:
                self._diff_detect_active = True
                self._set_diff_ready_colors()

    def start_realtime_display(self) -> None:
        if not self.diff_mode:
            return
        if self._diff_sample_active:
            return
        if not self._ensure_connected():
            return
        self._start_diff_status_monitor()

    def stop_realtime_display(self) -> None:
        if not self.diff_mode:
            return
        self._diff_sample_active = False
        cancel = self._diff_sample_cancel
        if cancel is not None:
            cancel.set()
        if self._diff_value_poll_job is not None:
            try:
                self.after_cancel(self._diff_value_poll_job)
            except Exception:
                pass
            self._diff_value_poll_job = None
        self._set_button_selected("awp1018_realtime_display", False)
        if self._diff_detect_active:
            self._set_diff_ready_colors()
        else:
            self._set_all_diff_channel_colors(self.STATUS_IDLE_COLOR)

    def stop_detection(self) -> None:
        if self.diff_mode:
            self._stop_diff_status_monitor()
        if self.write_reg(0x02, 0x0002):
            self._set_button_selected("start_detection", False)

    def start_play(self) -> None:
        self.write_reg_async(0x02, 0x0004)

    def stop_play(self) -> None:
        self.write_reg_async(0x02, 0x0008)

    def stop_all(self) -> None:
        if self.diff_mode:
            self._stop_diff_status_monitor()
        self.write_reg_async(
            0x02,
            0x0010,
            on_success=lambda: (
                self._set_button_selected("start_detection", False),
                self._set_button_selected("awp1018_realtime_display", False),
            ),
        )

    def reset_chip(self) -> None:
        self.write_reg(0x01, 0x5283)

    def sleep_chip(self) -> None:
        self.write_reg(0x01, 0x5350)

    def shutdown_chip(self) -> None:
        self.write_reg(0x01, 0x5337)

    def wake_chip(self) -> None:
        if not self._ensure_connected():
            return
        self._wake_attempts_remaining = 20
        self._set_wake_button_state("disabled")
        self._poll_wake_status()

    def _poll_wake_status(self) -> None:
        value = self.read_reg(self.ID_REG, show_error=False)
        if value == self.CHIP_ID:
            self._set_wake_button_state("normal")
            messagebox.showinfo(self.tr("info"), self.tr("chip_detect_success") + f": 0x{value:04X}")
            return

        self._wake_attempts_remaining = getattr(self, "_wake_attempts_remaining", 0) - 1
        if self._wake_attempts_remaining <= 0:
            self._set_wake_button_state("normal")
            messagebox.showerror(self.tr("error"), self.tr("chip_detect_failed"))
            return

        self.after(100, self._poll_wake_status)

    def _set_wake_button_state(self, state: str) -> None:
        wake_button = self._buttons.get("wake")
        if wake_button is not None:
            wake_button.configure(state=state)

    def on_show(self) -> None:
        self._on_dac_sample_rate_changed()
        self._on_zero_time_changed()
        self._on_debounce_time_changed()
        self._on_adc_sample_update_changed()

    def _generate_diff_sinc_segments(self) -> tuple[list[float], list[float], list[float]]:
        _max_voltage, _min_voltage, _t1, _t2, _t3, depth = self._diff_sinc_params()
        points = max(2, depth // 2)

        def sine_segment(start_deg: float, end_deg: float, amplitude: float, offset: float) -> list[float]:
            out: list[float] = []
            for index in range(points):
                ratio = index / max(1, points - 1)
                phase = math.radians(start_deg + (end_deg - start_deg) * ratio)
                out.append(max(-95.0, min(95.0, math.sin(phase) * amplitude + offset)))
            return out

        actual_segments = self._diff_sinc_actual_segments()
        seg1 = sine_segment(270.0, 90.0, actual_segments[0][2], actual_segments[0][4])
        seg2 = sine_segment(90.0, 270.0, actual_segments[1][2], actual_segments[1][4])
        seg3 = sine_segment(270.0, 90.0, actual_segments[2][2], actual_segments[2][4])
        return seg1, seg2, seg3

    def _generate_diff_preview_segments(self) -> list[tuple[str, list[float]]]:
        if self._diff_serial4_selected():
            return self._generate_serial4_preview_segments()

        waveform = self.rom_waveform_var.get()
        if waveform == "SINC":
            entries = [
                (slice_index, f"slice{slice_index}", segment)
                for slice_index, segment in zip(self._parse_diff_sinc_slice_group(), self._generate_diff_sinc_segments())
            ]
            return [(label, segment) for _slice_index, label, segment in sorted(entries, key=lambda item: item[0])]

        max_voltage, _min_voltage, _t1, _t2, _t3, depth = self._diff_sinc_params()
        points = max(2, depth)
        amplitude = self._diff_selected_vpk_voltage()
        offset = self._offset_code_to_voltage(self._diff_selected_offset_code())
        segments: list[tuple[str, list[float]]] = []
        if self._diff_serial4_selected():
            selected_slices = self._serial4_selected_slices()
        else:
            slice_index = self._parse_slice_index(self.rom_slice_var.get())
            if slice_index is None:
                return []
            selected_slices = [slice_index]
        for slice_index in selected_slices:
            values: list[float] = []
            for index in range(points):
                x = index / max(1, points - 1)
                if waveform == "Gauss":
                    sigma = 0.16
                    value = math.exp(-((x - 0.5) ** 2) / (2 * sigma * sigma)) * amplitude + offset
                elif waveform == "DC":
                    value = amplitude + offset
                else:
                    phase = math.radians(270.0) + x * math.tau
                    value = math.sin(phase) * amplitude + offset
                values.append(max(-95.0, min(95.0, value)))
            segments.append((f"slice{slice_index}", values))
        return segments

    def _generate_diff_single_preview_values(self, waveform: str, points: int) -> list[float]:
        amplitude = self._diff_selected_vpk_voltage()
        offset = self._offset_code_to_voltage(self._diff_selected_offset_code())
        values: list[float] = []
        for index in range(points):
            x = index / max(1, points - 1)
            if waveform == "Gauss":
                sigma = 0.16
                value = math.exp(-((x - 0.5) ** 2) / (2 * sigma * sigma)) * amplitude + offset
            elif waveform == "DC":
                value = amplitude + offset
            elif waveform == "SINC":
                phase = math.radians(270.0 + 180.0 * x)
                value = math.sin(phase) * amplitude + offset
            else:
                phase = math.radians(270.0) + x * math.tau
                value = math.sin(phase) * amplitude + offset
            values.append(max(-95.0, min(95.0, value)))
        return values

    def _cancel_rom_draw_job(self) -> None:
        job = getattr(self, "_rom_draw_job", None)
        if job is None:
            return
        try:
            self.after_cancel(job)
        except tk.TclError:
            pass
        self._rom_draw_job = None

    def _queue_rom_waveform_draw(self, *_args) -> None:
        if getattr(self, "_rom_drawing", False) or getattr(self, "_rom_draw_job", None) is not None:
            return
        try:
            self._rom_draw_job = self.after_idle(self._run_queued_rom_waveform_draw)
        except tk.TclError:
            self._rom_draw_job = None

    def _run_queued_rom_waveform_draw(self) -> None:
        self._rom_draw_job = None
        canvas = getattr(self, "rom_canvas", None)
        if canvas is None:
            return
        try:
            width = int(canvas.winfo_width())
            height = int(canvas.winfo_height())
            mapped = bool(canvas.winfo_ismapped())
        except tk.TclError:
            return
        if width <= 1 or height <= 1:
            retries = getattr(self, "_rom_draw_retries", 0)
            if mapped and retries < 4:
                self._rom_draw_retries = retries + 1
                self._queue_rom_waveform_draw()
            return
        self._rom_draw_retries = 0
        self._draw_rom_waveform()

    def _on_rom_canvas_configure(self, event: tk.Event) -> None:
        if getattr(self, "_rom_drawing", False):
            return
        canvas = getattr(self, "rom_canvas", None)
        if event.widget is not canvas:
            return
        try:
            size = (int(event.width), int(event.height))
        except (TypeError, ValueError, tk.TclError):
            size = None
        if size and size[0] > 1 and size[1] > 1 and size == getattr(self, "_rom_painted_size", None):
            return
        self._queue_rom_waveform_draw()

    def _restore_scaled_fonts(self) -> None:
        root = self.winfo_toplevel()
        body = int(getattr(root, "_piezo_body_font_size", 12))
        module_title = int(
            getattr(root, "_piezo_module_title_font_size", max(body + 1, int(round(body * 1.15))))
        )
        prominent = int(getattr(root, "_piezo_prominent_title_font_size", body + 9))
        title = int(getattr(root, "_piezo_title_font_size", body + 8))
        mono = int(getattr(root, "_piezo_mono_font_size", 9))
        small = max(7, body - 1)
        sidebar = int(getattr(root, "_piezo_sidebar_title_font_size", title))
        weight = page_title_weight(root)
        family = "Microsoft YaHei UI"

        def desired(widget: tk.Misc) -> tuple[Any, ...] | None:
            if isinstance(widget, tk.Text):
                return ("Consolas", mono)
            if isinstance(widget, tk.Label):
                spec = str(widget.cget("font"))
                parts: list[Any] = [family, body]
                if "bold" in spec:
                    parts.append("bold")
                if "underline" in spec:
                    parts.append("underline")
                return tuple(parts)
            if not isinstance(widget, ttk.Widget):
                return None
            style = str(widget.cget("style"))
            underline = "underline" in str(widget.cget("font"))
            font: tuple[Any, ...] | None = None
            if style == "Prominent.TLabel":
                font = (family, prominent, weight)
            elif style == "Title.TLabel":
                font = (family, title, weight)
            elif style == "PanelTitle.TLabel":
                font = (family, title, "bold")
            elif style == "ModuleTitle.TLabel":
                font = (family, module_title, "bold")
            elif style == "SectionTitle.TLabel":
                font = (family, body, "bold")
            elif style == "SidebarTitle.TLabel":
                font = (family, sidebar, "bold")
            elif style == "SidebarVersion.TLabel":
                font = (family, small)
            if font is not None and underline:
                font = font + ("underline",)
            return font

        def unchanged(widget: tk.Misc, font: tuple[Any, ...]) -> bool:
            current = str(widget.cget("font")).replace("{", "").replace("}", "")
            return current.split() == [str(part) for part in font]

        def walk(widget: tk.Misc) -> None:
            font = None
            try:
                font = desired(widget)
            except tk.TclError:
                font = None
            if font is not None:
                try:
                    if not unchanged(widget, font):
                        widget.configure(font=font)
                except tk.TclError:
                    pass
            for child in widget.winfo_children():
                walk(child)

        walk(self)

    def _draw_rom_waveform(self) -> None:
        self._cancel_rom_draw_job()
        self._update_rom_metrics()
        if not hasattr(self, "rom_canvas"):
            return
        canvas = self.rom_canvas
        try:
            width = int(canvas.winfo_width())
            height = int(canvas.winfo_height())
        except tk.TclError:
            return
        if width <= 1 or height <= 1:
            self._queue_rom_waveform_draw()
            return
        self._rom_drawing = True
        try:
            self._paint_rom_waveform(canvas, width, height)
            self._rom_painted_size = (width, height)
            self._rom_draw_retries = 0
        finally:
            self._rom_drawing = False

    def _paint_rom_waveform(self, canvas: tk.Canvas, w: int, h: int) -> None:
        canvas.delete("all")
        pad_l, pad_r, pad_t, pad_b = 48, 20, 24, 54
        plot_w = max(1, w - pad_l - pad_r)
        plot_h = max(1, h - pad_t - pad_b)
        mid = pad_t + plot_h / 2
        amp = plot_h * 0.38
        colors = self.colors

        canvas.create_rectangle(pad_l, pad_t, pad_l + plot_w, pad_t + plot_h, outline=colors["border"])
        canvas.create_line(pad_l, mid, pad_l + plot_w, mid, fill=colors["border"])

        waveform = self.rom_waveform_var.get()
        diff_segments: list[tuple[str, list[float]]] = []
        if self.diff_mode:
            try:
                diff_segments = self._generate_diff_preview_segments()
                diff_values = [v for _label, segment in diff_segments for v in segment]
            except Exception:
                diff_values = []
            values = [value / 95.0 for value in diff_values]
        else:
            values: list[float] = []
            points = 160
            for i in range(points):
                x = i / max(1, points - 1)
                if waveform == "Gauss":
                    sigma = 0.16
                    value = math.exp(-((x - 0.5) ** 2) / (2 * sigma * sigma))
                    value = (value * 2.0) - 1.0
                elif waveform == "DC":
                    value = 0.5
                else:
                    phase = math.radians(270.0) + x * math.tau
                    value = math.sin(phase)
                values.append(value)

        if self.diff_mode and diff_segments:
            palette = (colors["primary"], "#7c3aed", "#0f766e", "#d97706", "#dc2626")
            serial4 = self._diff_serial4_selected()
            try:
                repeats = max(1, int(self.rom_repeat_var.get().strip(), 0))
            except ValueError:
                repeats = 1
            if waveform == "SINC" or serial4:
                repeats = 1
            selected = self._serial4_selected_slices() if serial4 else []
            preview = []
            for index, (label, segment) in enumerate(diff_segments):
                period = None
                if serial4:
                    item = self._diff_slice_preview_cache.get(selected[index], {})
                    period = float(item.get("period_us", 0)) or None
                preview.append(PreviewSegment(label, segment, repeats, period))
            layout = compact_preview(preview, merge_adjacent=serial4)
            legend_x = pad_l + 8
            for part in layout.parts:
                if part.omitted:
                    continue
                segment_coords = []
                for index, value in enumerate(part.samples):
                    position = (part.start + (part.end-part.start)*index/max(1, len(part.samples)-1)) / layout.display_total
                    segment_coords.extend((pad_l + position*plot_w, mid - value/95.0*amp))
                color = palette[part.source_index % len(palette)]
                if len(segment_coords) >= 4:
                    canvas.create_line(*segment_coords, fill=color, width=2, smooth=True, tags="wave_curve")
                canvas.create_rectangle(legend_x, pad_t + 8, legend_x + 10, pad_t + 18, fill=color, outline=color)
                canvas.create_text(legend_x + 14, pad_t + 13, anchor="w", text=part.label, fill=colors["muted"])
                legend_x += 68
            draw_omissions(canvas, layout, pad_l, plot_w, mid, colors)
        elif len(values) >= 2:
            try:
                repeats = max(1, int(self.rom_repeat_var.get().strip(), 0))
            except ValueError:
                repeats = 1
            layout = compact_preview([PreviewSegment(waveform, values, repeats)])
            for part in layout.parts:
                if part.omitted:
                    continue
                segment_coords = []
                for index, value in enumerate(part.samples):
                    position = (part.start + (part.end-part.start)*index/max(1, len(part.samples)-1)) / layout.display_total
                    segment_coords.extend((pad_l + position*plot_w, mid - value*amp))
                canvas.create_line(*segment_coords, fill=colors["primary"], width=2, smooth=True, tags="wave_curve")
            draw_omissions(canvas, layout, pad_l, plot_w, mid, colors)

        if self.diff_mode:
            try:
                if self._diff_serial4_selected():
                    diff_values = [value for _label, segment in diff_segments for value in segment]
                    period_us = self._serial4_preview_period_us()
                    amplitude = max((abs(value) for value in diff_values), default=0.0)
                    waveform_label = "Serial4"
                else:
                    max_voltage, _min_voltage, t1, t2, t3, depth = self._diff_sinc_params()
                    waveform_label = waveform
                    if waveform == "SINC":
                        period_us = self._diff_sinc_playback_duration_us()
                    else:
                        period_us = depth * self._rom_point_time_us() * max(1, int(self.rom_repeat_var.get().strip(), 0))
                    amplitude = (
                        self._diff_sinc_actual_vpp_voltage()
                        if waveform == "SINC"
                        else self._diff_selected_vpp_voltage()
                    )
            except Exception:
                period_us = 0
                amplitude = 0.0
                waveform_label = "Serial4" if self._diff_serial4_selected() else waveform
        else:
            amplitude = self._rom_vpp_voltage()
            period_us = self._normal_playback_duration_us()
            waveform_label = waveform
        frequency_hz = (1_000_000.0 / period_us) if period_us else 0.0

        canvas.create_text(
            pad_l,
            h - 30,
            anchor="w",
            text=self.tr(
                "awp1018_canvas_summary",
                waveform=waveform_label,
                polarity="+/-" if self.diff_mode else "",
                amplitude=f"{amplitude:.2f}",
                period=f"{period_us:.0f}",
            ),
            fill=colors["muted"],
        )
        canvas.create_text(
            pad_l,
            h - 12,
            anchor="w",
            text=self.tr("awp1018_canvas_frequency", frequency=f"{frequency_hz:.3f}"),
            fill=colors["muted"],
        )

    def on_hide(self) -> None:
        self.save_button_draft()
        if self.diff_mode:
            self._stop_diff_status_monitor()

    def destroy(self) -> None:
        self.on_hide()
        self._cancel_rom_draw_job()
        try:
            self.ctx.serial.remove_log_listener(self._handle_serial_status_log)
        except Exception:
            pass
        super().destroy()

    def refresh_language(self) -> None:
        super().refresh_language()
        if self.preset_toolbar is not None:
            self.preset_toolbar.refresh_language()
        for key, label in self._labels.items():
            if key.endswith("_high"):
                label.configure(text=self.tr("awp1018_stage_high_threshold"))
            elif key.endswith("_low"):
                label.configure(text=self.tr("awp1018_stage_low_threshold"))
            elif key.endswith("_ovv_time"):
                label.configure(text=self.tr("adc_ovv_time"))
            elif key.endswith("_waveform"):
                label.configure(text=self.tr("awp1018_waveform_selection"))
            elif key.endswith("_jump_condition"):
                label.configure(text=self.tr("awp1018_jump_condition"))
            elif key.endswith("_jump_target"):
                label.configure(text=self.tr("awp1018_jump_target"))
            elif key.endswith("_group_start"):
                label.configure(text=self.tr("awp1018_group_start_selection"))
            else:
                label.configure(text=self.tr(key))

        for key, check in self._checks.items():
            if key.endswith("_press_debounce_enable"):
                check.configure(text=self.tr("awp1018_press_debounce_enable"))
            elif key.endswith("_release_debounce_enable"):
                check.configure(text=self.tr("awp1018_release_debounce_enable"))
            elif key == "awp1018_return_bit_enable":
                check.configure(text=self.tr("awp1018_return_bit_enable"))
            elif key == "awp1018_return_bit_enhance":
                check.configure(text=self.tr("awp1018_return_bit_enhance"))
            elif key.endswith("_jump_press"):
                check.configure(text=self.tr("awp1018_jump_press"))
            elif key.endswith("_jump_release"):
                check.configure(text=self.tr("awp1018_jump_release"))
            elif key.startswith("Stage") and key.endswith("_enable"):
                check.configure(text=self.tr("enable"))
            else:
                check.configure(text=self.tr(key))

        for key, btn in self._buttons.items():
            btn.configure(text=self.tr(key))

        for stage, btn in self._stage_buttons.items():
            btn.configure(text=self.tr("awp1018_stage_label", index=stage[-1]))

        for channel, check in self._channel_checks.items():
            check.configure(text=self.tr("awp1018_channel_label", index=channel))

        for channel, label in self._channel_mapping_labels.items():
            label.configure(text=self.tr("awp1018_group_channel_label", index=channel))

        for checks in self._stage_group_checks.values():
            for group, check in checks.items():
                check.configure(text=self.tr("awp1018_group_channel_label", index=group))

        for channel, label in self._diff_status_titles.items():
            label.configure(text=self.tr("awp1018_channel_label", index=channel))

        if hasattr(self, "arbitration_mode_combo"):
            options = self._arbitration_mode_options()
            self.arbitration_mode_combo.configure(values=options)
            try:
                index = self.ARBITRATION_MODE_CODES.index(self.arbitration_mode_code)
            except ValueError:
                index = len(options) - 1
            self.arbitration_mode_var.set(options[index])

        if hasattr(self, "tabs"):
            self.tabs.tab(0, text=self.tr("awp1018_waveform_selector"))
        if hasattr(self, "rom_hint_label"):
            hint_key = "awp1018_diff_sinc_hint" if self.diff_mode else "awp1018_rom_waveform_hint"
            self.rom_hint_label.configure(text=self.tr(hint_key))
        if hasattr(self, "diff_play_hint_label"):
            self.diff_play_hint_label.configure(text=self.tr("awp1018_diff_play_hint"))
        if hasattr(self, "diff_serial4_segment_combos"):
            self._refresh_diff_waveform_controls()
        self._update_rom_metrics()

    def refresh_theme(self) -> None:
        super().refresh_theme()
        legend = getattr(self, "_adc_legend_canvas", None)
        if legend is not None:
            legend.configure(
                background=self.colors["canvas_bg"],
                highlightbackground=self.colors["border"],
            )
            self._draw_adc_threshold_legend()
        for key, swatch in getattr(self, "_adc_legend_swatches", {}).items():
            color = self.colors["primary"] if key == "awp1221_diagram_press" else "#7c3aed"
            swatch.configure(background=color)
        for frame in self._diff_status_frames.values():
            frame.configure(highlightbackground=self.colors["border"])
            for child in frame.winfo_children():
                try:
                    child.configure(fg=self.colors["text"])
                except tk.TclError:
                    pass
        if hasattr(self, "rom_canvas"):
            self.rom_canvas.configure(
                bg=self.colors["canvas_bg"],
                highlightbackground=self.colors["border"],
            )
            self._draw_rom_waveform()


class AWP1018FullModePage(AWP1018NormalModePage):
    page_name = "full_mode"

    def __init__(self, parent: tk.Misc, ctx: Any, **kwargs: Any) -> None:
        super().__init__(parent, ctx, diff_mode=False, **kwargs)

    def _build_ui(self) -> None:
        for _ in self._build_ui_steps():
            pass

    def _build_ui_steps(self) -> Iterator[None]:
        self.full_status_value_var = tk.StringVar(value="")
        self.full_chip_id_var = tk.StringVar(value="")
        self.full_ram_addr_var = tk.StringVar(value="0x0000")
        self.full_sram_data_var = tk.StringVar(value="0x0000")
        self.full_adc_channel_var = tk.StringVar(value="CH0")
        self.full_adc_value_var = tk.StringVar(value="")
        yield
        self.full_action_status_var = tk.StringVar(value="")
        self.full_serial4_segment_combos: list[ttk.Combobox] = []
        self._rom_selector_window: tk.Toplevel | None = None
        self._rom_selector_owned_keys: dict[str, set[str]] = {
            "labels": set(),
            "buttons": set(),
            "checks": set(),
        }

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        yield

        header = self.make_title(self, "full_mode", show_logo=False)
        header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())

        body = ttk.Frame(self, style="App.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        # Keep the ADC column at its safe minimum, then let it grow with the
        # other Full Mode columns.  A fixed zero-weight column leaves its
        # inner scroll area 10 px narrower than the ADC form at ordinary
        # desktop widths, even though the outer page has spare space.
        body.columnconfigure(0, weight=2, minsize=760)
        body.columnconfigure(1, weight=4, minsize=360)
        yield
        body.columnconfigure(2, weight=3, minsize=360)
        body.rowconfigure(0, weight=1)

        left = self._make_full_scroll_column(body, 0, padx=(0, 12), propagate_width=True)
        middle = self._make_full_scroll_column(body, 1, padx=(0, 12))

        right = ttk.Frame(body, style="Panel.TFrame", padding=0)
        right.grid(row=0, column=2, sticky="nsew")
        yield
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=2)

        self._build_full_left_column(left)
        self._build_full_middle_column(middle)

        register_panel = ttk.Frame(right, style="Panel.TFrame", padding=0)
        yield
        register_panel.grid(row=0, column=0, sticky="nsew")
        register_panel.rowconfigure(0, weight=1)
        register_panel.columnconfigure(0, weight=1)
        self._build_full_function_tools(register_panel)

        log_frame = self.make_serial_log(right, height=14, width=42, subscribe=True)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        self._register_module_spacing_widget(log_frame, before=True, after=False)
        yield

        self._bind_full_mode_traces()

        self.refresh_language()
        self._show_stage("Stage0")
        self._draw_rom_waveform()

    def _make_full_scroll_column(
        self,
        parent: tk.Misc,
        column: int,
        *,
        padx: tuple[int, int],
        propagate_width: bool = False,
    ) -> ttk.Frame:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=0)
        panel.grid(row=0, column=column, sticky="nsew", padx=padx)
        panel.rowconfigure(0, weight=1)
        panel.columnconfigure(0, weight=1)

        scroll, content = self.make_vertical_scroll_area(panel, propagate_width=propagate_width)
        scroll.grid(row=0, column=0, sticky="nsew")
        content.configure(style="Panel.TFrame", padding=14)
        return content

    def _build_full_left_column(self, left: tk.Misc) -> None:
        left.columnconfigure(1, weight=2, minsize=145)
        left.columnconfigure(3, weight=2, minsize=145)

        row = 0
        self._build_adc_channel_selector(left, row)
        row += 1
        row = self._add_section_title(left, row, "awp1018_adc_threshold_detection")
        self._build_stage_selector(left, row)
        row += 1

        self.stage_container = ttk.Frame(left, style="Panel.TFrame")
        self.stage_container.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(4, 8))
        self._register_module_spacing_widget(self.stage_container, before=True, after=True)
        self.stage_container.columnconfigure(0, weight=1)
        for stage in ("Stage0", "Stage1", "Stage2", "Stage3"):
            self._build_stage_frame(stage)
        row += 1

        row = self._add_section_title(left, row, "full_mode_adc")
        self._build_adc_timing_row(left, row)
        row += 1
        self._build_zero_return_bit_row(left, row)
        row += 1
        self._build_arbitration_return_time_row(left, row)
        row += 1

        zps_check = ttk.Checkbutton(left, variable=self.zps_enable_var, command=self._on_zps_enable_changed)
        zps_check.grid(row=row, column=0, columnspan=4, sticky="w", pady=(12, 7))
        self._checks["awp1018_zps_enable"] = zps_check
        row += 1

        interrupt_check = ttk.Checkbutton(left, variable=self.interrupt_enable_var, command=self._on_interrupt_enable_changed)
        interrupt_check.grid(row=row, column=0, columnspan=4, sticky="w", pady=(4, 7))
        self._checks["awp1018_interrupt_enable"] = interrupt_check
        row += 1

        self.make_module_title_row(
            left,
            "awp1018_function_controls",
            step=4,
            step_total=4,
            help_key="awp1018_help_function_control",
            row=row,
            columnspan=4,
            pady=(10 if row else 0, 4),
        )
        row += 1
        self._build_control_buttons(left, row)
        row += 1
        self._attach_gpio_controls(left, row)

    def _build_full_middle_column(self, middle: tk.Misc) -> None:
        middle.columnconfigure(0, weight=1)

        row = 0
        row = self._add_section_title(middle, row, "awp1018_waveform_selector")

        selector_btn = ttk.Button(
            middle,
            style="Primary.TButton",
            command=self.open_rom_selector_window,
        )
        selector_btn.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(4, 10))
        self._buttons["awp1018_open_waveform_selector"] = selector_btn
        row += 1

        dac_frame = ttk.Frame(middle, style="Panel.TFrame", padding=(0, 10))
        dac_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        self._register_module_spacing_widget(dac_frame, before=True, after=False)
        dac_frame.columnconfigure(1, weight=1)

        label = ttk.Label(dac_frame, style="Panel.TLabel")
        label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._labels["awp1018_dac_sample_rate"] = label
        ttk.Entry(dac_frame, textvariable=self.dac_sample_rate_var, width=14).grid(
            row=0,
            column=1,
            sticky="w",
        )

    def _bind_full_mode_traces(self) -> None:
        self.rom_waveform_var.trace_add("write", self._queue_rom_waveform_draw)
        self.rom_waveform_var.trace_add("write", lambda *_: self._refresh_rom_depth_options())
        for var in (
            self.rom_depth_var,
            self.rom_point_time_var,
            self.rom_amplitude_var,
            self.rom_offset_var,
            self.diff_sinc_max_voltage_var,
            self.diff_sinc_min_voltage_var,
            self.diff_sinc_time1_var,
            self.diff_sinc_time2_var,
            self.diff_sinc_time3_var,
            self.diff_play_count_var,
            self.diff_serial_var,
            self.diff_sinc_slice_group_var,
            self.diff_single_waveform_var,
            *self.diff_serial4_segment_vars,
        ):
            var.trace_add("write", self._queue_rom_waveform_draw)
        self.zero_time_var.trace_add("write", lambda *_: self._on_zero_time_changed())
        self.debounce_time_var.trace_add("write", lambda *_: self._on_debounce_time_changed())
        self.adc_sample_update_var.trace_add("write", lambda *_: self._on_adc_sample_update_changed())
        self.return_bit_time_var.trace_add("write", lambda *_: self._on_return_bit_config_changed())
        self.dac_sample_rate_var.trace_add("write", lambda *_: self._on_dac_sample_rate_changed())
        if hasattr(self, "rom_canvas"):
            self.rom_canvas.bind("<Configure>", self._on_rom_canvas_configure)

    def open_rom_selector_window(self) -> None:
        if self._rom_selector_window is not None and self._rom_selector_window.winfo_exists():
            self._rom_selector_window.lift()
            self._rom_selector_window.focus_force()
            return

        window = tk.Toplevel(self)
        window.title(self.tr("awp1018_waveform_selector"))
        fit_toplevel_to_screen(
            window,
            preferred_width=1120,
            preferred_height=760,
            minimum_width=480,
            minimum_height=320,
        )
        window.configure(bg=self.colors["bg"])
        window.rowconfigure(0, weight=1)
        window.columnconfigure(0, weight=1)
        window.protocol("WM_DELETE_WINDOW", self._close_rom_selector_window)
        self._rom_selector_window = window

        scroll_area = VerticalScrollArea(
            window,
            self.ctx,
            horizontal=True,
            min_content_width=980,
        )
        scroll_area.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self._rom_selector_scroll_area = scroll_area
        panel = ttk.Frame(scroll_area.content, style="Panel.TFrame", padding=14)
        panel.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        scroll_area.content.columnconfigure(0, weight=1)

        before_labels = set(self._labels)
        before_buttons = set(self._buttons)
        before_checks = set(self._checks)
        self._selector_widgets.clear()
        self._build_rom_selector(panel)
        self._rom_selector_owned_keys = {
            "labels": set(self._labels) - before_labels,
            "buttons": set(self._buttons) - before_buttons,
            "checks": set(self._checks) - before_checks,
        }

        if hasattr(self, "rom_canvas"):
            self.rom_canvas.bind("<Configure>", self._on_rom_canvas_configure)
        self.refresh_language()
        self._refresh_rom_depth_options()
        self._draw_rom_waveform()

    def _close_rom_selector_window(self) -> None:
        if self._rom_selector_window is not None:
            try:
                self._rom_selector_window.destroy()
            except Exception:
                pass
        self._rom_selector_window = None

        for key in self._rom_selector_owned_keys.get("labels", set()):
            self._labels.pop(key, None)
        for key in self._rom_selector_owned_keys.get("buttons", set()):
            self._buttons.pop(key, None)
        for key in self._rom_selector_owned_keys.get("checks", set()):
            self._checks.pop(key, None)
        self._rom_selector_owned_keys = {"labels": set(), "buttons": set(), "checks": set()}
        self._selector_widgets.clear()

        for attr in (
            "rom_canvas",
            "rom_depth_combo",
            "rom_waveform_combo",
            "rom_slice_combo",
            "rom_hint_label",
            "rom_metric_label",
            "rom_frequency_label",
        ):
            if hasattr(self, attr):
                delattr(self, attr)

    def _build_full_function_tools(self, parent: tk.Misc) -> None:
        scroll, content = self.make_vertical_scroll_area(parent)
        scroll.grid(row=0, column=0, sticky="nsew")
        content.configure(style="Panel.TFrame", padding=12)
        content.columnconfigure(0, weight=1)

        row = 0
        row = self._add_section_title(content, row, "full_mode_system")
        row = self._add_full_system_summary(content, row)
        row = self._add_section_title(content, row, "full_mode_memory")
        row = self._add_full_memory_tools(content, row)
        row = self._add_section_title(content, row, "awp1018_full_adc_readback")
        row = self._add_full_adc_readback(content, row)
        row = self._add_section_title(content, row, "full_mode_serial")
        row = self._add_full_serial4_tools(content, row)

        status_label = ttk.Label(content, textvariable=self.full_action_status_var, style="Muted.TLabel", wraplength=340)
        status_label.grid(row=row, column=0, sticky="ew", pady=(8, 0))

    def _add_full_memory_tools(self, parent: tk.Misc, row: int) -> int:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=row, column=0, sticky="ew", pady=(4, 10))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)
        frame.columnconfigure(3, weight=1)

        addr_label = ttk.Label(frame, style="Panel.TLabel")
        addr_label.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        self._labels["ram_address"] = addr_label
        ttk.Entry(frame, textvariable=self.full_ram_addr_var, width=12).grid(row=0, column=1, sticky="ew", pady=5)
        write_addr_btn = ttk.Button(frame, style="Secondary.TButton", command=self.write_ram_address)
        write_addr_btn.grid(row=0, column=2, sticky="ew", padx=(8, 0), pady=5)
        self._buttons["full_write_ram_addr"] = write_addr_btn
        read_addr_btn = ttk.Button(frame, style="Secondary.TButton", command=self.read_ram_address)
        read_addr_btn.grid(row=0, column=3, sticky="ew", padx=(8, 0), pady=5)
        self._buttons["full_read_ram_addr"] = read_addr_btn

        data_label = ttk.Label(frame, style="Panel.TLabel")
        data_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        self._labels["sram_data"] = data_label
        ttk.Entry(frame, textvariable=self.full_sram_data_var, width=12).grid(row=1, column=1, sticky="ew", pady=5)
        write_data_btn = ttk.Button(frame, style="Secondary.TButton", command=self.write_sram_data)
        write_data_btn.grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=5)
        self._buttons["full_write_sram_data"] = write_data_btn
        read_data_btn = ttk.Button(frame, style="Secondary.TButton", command=self.read_sram_data)
        read_data_btn.grid(row=1, column=3, sticky="ew", padx=(8, 0), pady=5)
        self._buttons["full_read_sram_data"] = read_data_btn
        return row + 1

    def _add_full_adc_readback(self, parent: tk.Misc, row: int) -> int:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=row, column=0, sticky="ew", pady=(4, 10))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        channel_label = ttk.Label(frame, style="Panel.TLabel")
        channel_label.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        self._labels["awp1018_full_adc_channel"] = channel_label
        ttk.Combobox(
            frame,
            textvariable=self.full_adc_channel_var,
            values=[f"CH{index}" for index in range(4)],
            state="readonly",
            width=8,
        ).grid(row=0, column=1, sticky="ew", pady=5)

        value_label = ttk.Label(frame, style="Panel.TLabel")
        value_label.grid(row=0, column=2, sticky="w", padx=(12, 8), pady=5)
        self._labels["awp1018_full_adc_value"] = value_label
        ttk.Entry(frame, textvariable=self.full_adc_value_var, state="readonly", width=12).grid(
            row=0,
            column=3,
            sticky="ew",
            pady=5,
        )

        read_btn = ttk.Button(frame, style="Primary.TButton", command=self.read_full_adc_value)
        read_btn.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(7, 0))
        self._buttons["awp1018_full_read_adc"] = read_btn
        return row + 1

    def _add_full_serial4_tools(self, parent: tk.Misc, row: int) -> int:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=row, column=0, sticky="ew", pady=(4, 10))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        self.full_serial4_segment_combos = []
        options = self._serial4_segment_options()
        for index, variable in enumerate(self.diff_serial4_segment_vars, start=1):
            label = ttk.Label(frame, style="Panel.TLabel")
            grid_row = (index - 1) // 2
            grid_column = 0 if index % 2 else 2
            label.grid(row=grid_row, column=grid_column, sticky="w", padx=(0 if grid_column == 0 else 12, 8), pady=5)
            key = f"awp1018_serial4_segment_{index}"
            self._labels[key] = label
            combo = ttk.Combobox(frame, textvariable=variable, values=options, state="readonly", width=13)
            combo.grid(row=grid_row, column=grid_column + 1, sticky="ew", pady=5)
            self.full_serial4_segment_combos.append(combo)

        action_frame = ttk.Frame(frame, style="Panel.TFrame")
        action_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        action_frame.columnconfigure(0, weight=1)
        action_frame.columnconfigure(1, weight=1)

        write_btn = ttk.Button(action_frame, style="Primary.TButton", command=self.write_full_serial4_config)
        write_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self._buttons["full_write_serial_config"] = write_btn

        read_btn = ttk.Button(action_frame, style="Secondary.TButton", command=self.read_full_serial4_config)
        read_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self._buttons["full_read_serial_config"] = read_btn
        return row + 1

    def _add_full_system_summary(self, parent: tk.Misc, row: int) -> int:
        summary = ttk.Frame(parent, style="Panel.TFrame")
        summary.grid(row=row, column=0, sticky="ew", pady=(4, 8))
        summary.columnconfigure(1, weight=1)

        cmd_frame = ttk.Frame(summary, style="Panel.TFrame")
        cmd_frame.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        for column in range(4):
            cmd_frame.columnconfigure(column, weight=1)

        commands = (
            ("awp1018_reset", self.reset_chip, "Secondary.TButton"),
            ("awp1018_sleep", self.sleep_chip, "Secondary.TButton"),
            ("shutdown", self.shutdown_chip, "Danger.TButton"),
            ("wake", self.wake_chip, "Primary.TButton"),
        )
        for column, (key, command, style) in enumerate(commands):
            button = ttk.Button(cmd_frame, style=style, command=command)
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0))
            self._buttons[key] = button

        status_label = ttk.Label(summary, style="Panel.TLabel")
        status_label.grid(row=1, column=0, sticky="w", pady=6, padx=(0, 8))
        self._labels["full_status"] = status_label
        ttk.Entry(summary, textvariable=self.full_status_value_var, state="readonly").grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=6,
        )
        status_btn = ttk.Button(summary, style="Secondary.TButton", command=self.read_full_status)
        status_btn.grid(row=1, column=3, sticky="ew", pady=6, padx=(8, 0))
        self._buttons["full_read_status"] = status_btn

        chip_label = ttk.Label(summary, style="Panel.TLabel")
        chip_label.grid(row=2, column=0, sticky="w", pady=6, padx=(0, 8))
        self._labels["chip_id"] = chip_label
        ttk.Entry(summary, textvariable=self.full_chip_id_var, state="readonly").grid(
            row=2,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=6,
        )
        chip_btn = ttk.Button(summary, style="Secondary.TButton", command=self.read_full_chip_id)
        chip_btn.grid(row=2, column=3, sticky="ew", pady=6, padx=(8, 0))
        self._buttons["full_read_chip_id"] = chip_btn
        return row + 1

    def _device_addr(self) -> int:
        return int(self.ctx.state.extra.get("awp1018_device_addr", self.ctx.state.awp192x_device_addr)) & 0xFF

    def _ensure_connected(self, *, show_error: bool = True) -> bool:
        if self.ctx.serial.is_connected:
            return True
        if show_error:
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
        return False

    def _read_reg(self, reg: int, *, show_error: bool = True) -> int | None:
        if not self._ensure_connected(show_error=show_error):
            return None
        address = self._device_addr()
        resp = self.ctx.serial.read_register_value(address, reg, bits=16)
        if not resp.ok:
            if show_error:
                messagebox.showerror(self.tr("error"), resp.error)
            return None
        value = self.ctx.serial.extract_rdataw_value(
            resp.data,
            device_addr=address,
            reg_addr=reg,
            bits=16,
        )
        if value is None and show_error:
            messagebox.showerror(self.tr("error"), resp.data or self.tr("invalid_input"))
        return value

    def _write_reg(self, reg: int, value: int, *, show_error: bool = True) -> bool:
        if not self._ensure_connected(show_error=show_error):
            return False
        resp = self.ctx.serial.write_register_value(
            self._device_addr(),
            reg,
            value & 0xFFFF,
            bits=16,
        )
        if not resp.ok and show_error:
            messagebox.showerror(self.tr("error"), resp.error)
        return resp.ok

    def _parse_full_u16(self, text: str) -> int:
        value = int(text, 0)
        if not 0 <= value <= 0xFFFF:
            raise ValueError
        return value

    def write_ram_address(self) -> None:
        try:
            value = self._parse_full_u16(self.full_ram_addr_var.get().strip())
        except Exception:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return
        current = self._read_reg(0x28)
        if current is None:
            return
        write_value = (current & ~0x03FF) | (value & 0x03FF)
        if self._write_reg(0x28, write_value):
            self.full_ram_addr_var.set(f"0x{write_value & 0xFFFF:04X}")
            self.full_action_status_var.set(self.tr("awp1018_full_write_done", addr="0x28"))

    def read_ram_address(self) -> None:
        value = self._read_reg(0x28)
        if value is None:
            return
        self.full_ram_addr_var.set(f"0x{value & 0xFFFF:04X}")
        self.full_action_status_var.set(self.tr("awp1018_full_read_done", addr="0x28"))

    def write_sram_data(self) -> None:
        try:
            value = self._parse_full_u16(self.full_sram_data_var.get().strip())
        except Exception:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return
        if self._write_reg(0x00, value):
            self.full_sram_data_var.set(f"0x{value & 0xFFFF:04X}")
            self.full_action_status_var.set(self.tr("awp1018_full_write_done", addr="0x00"))

    def read_sram_data(self) -> None:
        value = self._read_reg(0x00)
        if value is None:
            return
        self.full_sram_data_var.set(f"0x{value & 0xFFFF:04X}")
        self.full_action_status_var.set(self.tr("awp1018_full_read_done", addr="0x00"))

    def read_full_adc_value(self) -> None:
        try:
            channel = int(self.full_adc_channel_var.get().replace("CH", ""), 10)
        except Exception:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return
        if not 0 <= channel <= 3:
            messagebox.showerror(self.tr("error"), self.tr("invalid_input"))
            return

        select_value = (channel & 0x03) << 13
        if not self._write_reg(0x24, select_value, show_error=False):
            messagebox.showerror(self.tr("error"), self.tr("awp1018_full_adc_read_failed"))
            return
        value = self._read_reg(0x24, show_error=False)
        if value is None:
            messagebox.showerror(self.tr("error"), self.tr("awp1018_full_adc_read_failed"))
            return
        adc_value = self._adc12_to_signed(value & 0x0FFF)
        self.full_adc_value_var.set(str(adc_value))
        self.full_action_status_var.set(self.tr("awp1018_full_read_done", addr="0x24"))

    def write_full_serial4_config(self) -> None:
        if self._write_serial4_segment_registers():
            self.full_action_status_var.set(self.tr("awp1018_full_serial_write_done"))

    def read_full_serial4_config(self) -> None:
        reg29 = self._read_reg(0x29)
        if reg29 is None:
            return
        reg2a = self._read_reg(0x2A)
        if reg2a is None:
            return

        codes = [
            (reg29 >> 0) & 0x1F,
            (reg29 >> 5) & 0x1F,
            (reg29 >> 10) & 0x1F,
            (reg2a >> 0) & 0x1F,
            (reg2a >> 5) & 0x1F,
            (reg2a >> 10) & 0x1F,
        ]
        for variable, code in zip(self.diff_serial4_segment_vars, codes):
            if code == 0x1F:
                variable.set(self._serial4_end_option())
            else:
                variable.set(f"slice{code}")
        self._refresh_full_serial4_options()
        self.full_action_status_var.set(self.tr("awp1018_full_serial_read_done"))

    def _refresh_full_serial4_options(self) -> None:
        values = self._serial4_segment_options()
        for combo in self.full_serial4_segment_combos:
            combo.configure(values=values)
        for variable in self.diff_serial4_segment_vars:
            text = variable.get()
            if text.startswith("0x1F") and text not in values:
                variable.set(self._serial4_end_option())

    def read_full_status(self) -> None:
        value02 = self._read_reg(0x02)
        if value02 is None:
            return
        value03 = self._read_reg(0x03, show_error=False)
        if value03 is None:
            self.full_status_value_var.set(f"0x02=0x{value02 & 0xFFFF:04X}")
            return
        self.full_status_value_var.set(
            f"0x02=0x{value02 & 0xFFFF:04X} 0x03=0x{value03 & 0xFFFF:04X}"
        )

    def read_full_chip_id(self) -> None:
        value = self._read_reg(self.ID_REG)
        if value is not None:
            self.full_chip_id_var.set(f"0x{value & 0xFFFF:04X}")

    def on_show(self) -> None:
        self._on_dac_sample_rate_changed()

    def refresh_language(self) -> None:
        AWP1018NormalModePage.refresh_language(self)
        self._refresh_full_serial4_options()

    def refresh_theme(self) -> None:
        AWP1018NormalModePage.refresh_theme(self)

    def destroy(self) -> None:
        self._close_rom_selector_window()
        super().destroy()


class AWP1018ReservedModePage(BasePage):
    def __init__(self, parent: tk.Misc, ctx: Any, title_key: str) -> None:
        super().__init__(parent, ctx)
        self.title_key = title_key
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = self.make_title(self, self.title_key, show_logo=False)
        header.grid(row=0, column=0, sticky="ew", pady=self._title_grid_padding())
        self.title_label = header.grid_slaves(row=0, column=0)[0]

        panel = ttk.Frame(self, style="Panel.TFrame", padding=18)
        panel.grid(row=1, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)

        self.hint_label = ttk.Label(panel, style="Muted.TLabel", wraplength=720)
        self.hint_label.grid(row=1, column=0, sticky="w", pady=(10, 0))

        self.refresh_language()

    def refresh_language(self) -> None:
        super().refresh_language()
        self.title_label.configure(text=self.tr(self.title_key))
        self.hint_label.configure(text=self.tr("placeholder_hint"))
