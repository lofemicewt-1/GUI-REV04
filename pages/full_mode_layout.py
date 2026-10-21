"""Presentation compatibility for the original Full Mode forms.

The current transport, settings models, page lifecycle and Button Mode chrome
remain owned by the host. This module only selects layout and widget references.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any


def is_full_mode(page: Any) -> bool:
    return getattr(page, "mode_key", None) == "full_mode" or getattr(page, "page_name", None) == "full_mode"


class FullModeFooter:
    """Keep the original inline tools separate from the Button tools widgets.

    Both sets use the host's existing variables and command callbacks. Selecting
    the corresponding widget references keeps busy/disabled states and the 192x
    detail editor attached to the visible controls, without rebuilding either UI.
    """

    def __init__(self, host: Any) -> None:
        self.host = host
        self.active = False
        self.full_refs: dict[str, Any] = {}
        self.button_refs: dict[str, Any] = {}
        self.scroll_options: dict[str, Any] = {}

    def _build(self) -> None:
        host = self.host
        before = dict(vars(host))
        for name in ("_labels", "_buttons", "state_buttons"):
            if hasattr(host, name):
                setattr(host, name, dict(getattr(host, name)))
        area, content = host.make_vertical_scroll_area(host, horizontal=False)
        self.area = area
        area.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        content.columnconfigure(0, weight=1)
        footer, actions, registers, identity = host.make_merged_footer(content)
        footer.grid(row=0, column=0, sticky="ew")
        host._build_state_actions(actions)
        registers.columnconfigure(8, weight=1)
        host._build_register_bar(registers)
        host._build_identity_bar(identity)
        if host.page_name == "awp1921_1923":
            host.reg_panel = registers
            host._build_detail_host(footer)
        host.footer_scroll = area
        host._footer_panel = footer
        if hasattr(host, "_footer"):
            host._footer = footer
        # Only the builders' replaced references belong to this presentation.
        # Lifecycle registries (serial selectors, child areas, text bindings)
        # intentionally retain both widget sets for refresh and destruction.
        for name, value in vars(host).items():
            if name not in before or value is not before[name]:
                self.full_refs[name] = value
                self.button_refs[name] = before.get(name)
        queue_fit = (getattr(host, "_queue_identity_strip_fit", None)
                     or getattr(host, "_queue_footer_fit", None)
                     or (lambda _event: self.fit()))
        footer.bind("<Configure>", queue_fit, add="+")
        # AWP1018 has no identity-strip fitter. Recompute the Full footer cap
        # when the host shrinks, even if the footer's requested size is stable.
        host.bind("<Configure>", lambda _event: self.fit(), add="+")
        self._translate_controls()

    def _translate_controls(self) -> None:
        host = self.host
        for name in ("_labels", "_buttons", "state_buttons"):
            old = self.button_refs.get(name, {}) or {}
            for key, widget in getattr(host, name, {}).items():
                if widget is not old.get(key):
                    host.bind_text(widget, key)
        # 192x stores these controls as attributes rather than in _labels and
        # _buttons. The builders leave their text empty until translation.
        if host.page_name == "awp1921_1923":
            for name, key in (
                ("addr_label", "current_chip_addr"),
                ("reg_title_label", "read_write_registers"),
                ("reg_addr_label", "reg_addr"),
                ("write_value_label", "write_value"),
                ("read_value_label", "read_value"),
                ("rr_btn", "rr"),
                ("rw_btn", "rw"),
                ("detail_mode_check", "detail_mode"),
                ("detail_register_label", "detail_register"),
            ):
                host.bind_text(getattr(host, name), key)

    def select(self, full: bool) -> None:
        full = bool(full)
        if full == self.active:
            return
        host = self.host
        area = host.mode_scroll
        if full:
            self.scroll_options = {name: getattr(area, name) for name in (
                "horizontal", "clamp_width", "min_content_width", "propagate_width", "propagate_height",
            )}
            original_footer = getattr(host, "footer_scroll", None)
            if original_footer is None:
                original_footer = host.identity_bar.master
            self.button_footer = original_footer
            if not self.full_refs:
                self._build()
            else:
                for name in self.button_refs:
                    self.button_refs[name] = getattr(host, name, None)
                for name, value in self.full_refs.items():
                    setattr(host, name, value)
            original_footer.grid_remove()
            self.area.grid()
            area.horizontal = True
            area.clamp_width = False
            area.min_content_width = 1320 if host.page_name == "awp1018" else 0
            area.propagate_width = False
            area.propagate_height = host.page_name != "awp1921_1923"
        else:
            for name in self.full_refs:
                self.full_refs[name] = getattr(host, name, None)
            self.area.grid_remove()
            for name, value in self.button_refs.items():
                setattr(host, name, value)
            self.button_footer.grid()
            for name, value in self.scroll_options.items():
                setattr(area, name, value)
        self.active = full
        # The host owns the floating Button chrome and all of its clearances.
        host.trapezoid._last_layout_signature = None
        host.trapezoid._reserve_scroll_clearance()
        host.trapezoid._queue_layout()
        area._queue_scroll_layout()
        self.fit()

    def fit(self) -> bool:
        if not self.active:
            return False
        host = self.host
        try:
            available = max(96, int(host.winfo_height() * .35))
            desired = min(host._footer_panel.winfo_reqheight() + 16, available)
            if self.area.canvas.winfo_reqheight() != desired:
                self.area.canvas.configure(height=desired)
        except tk.TclError:
            pass
        return True


def build_full_mode_log(
    self,
    parent: tk.Misc,
    *,
    height: int = 7,
    width: int | None = None,
    subscribe: bool = True,
) -> ttk.Frame:
    from pages.base_page import WrappingGroupBar

    frame = ttk.Frame(parent, style="Panel.TFrame")
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(1, weight=1)

    toolbar = WrappingGroupBar(frame)
    toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
    title_group = toolbar.add_group()
    title = ttk.Label(title_group, style="SectionTitle.TLabel")
    title.grid(row=0, column=0, sticky="w")
    self.bind_text(title, "serial_log")

    clear_group = toolbar.add_group()
    clear_btn = ttk.Button(clear_group, style="Secondary.TButton", command=self.clear_serial_log)
    clear_btn.grid(row=0, column=0, sticky="w")
    self.bind_text(clear_btn, "clear_serial_log")

    export_group = toolbar.add_group()
    export_btn = ttk.Button(export_group, style="Secondary.TButton", command=self.export_serial_log)
    export_btn.grid(row=0, column=0, sticky="w")
    self.bind_text(export_btn, "export_serial_log")

    text_width = 8 if width is None else width
    text = tk.Text(
        frame,
        height=height,
        width=text_width,
        wrap="none",
        relief="flat",
        highlightthickness=1,
        font=("Consolas", int(getattr(self.winfo_toplevel(), "_piezo_mono_font_size", 9))),
    )
    text.grid(row=1, column=0, sticky="nsew")
    xscroll = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
    xscroll.grid(row=2, column=0, sticky="ew")
    text.configure(xscrollcommand=xscroll.set, state="disabled")
    self._serial_log_frame = frame
    self._serial_log_header = toolbar
    self._serial_log_title = title
    self._serial_log_text = text
    self._refresh_serial_log_theme()

    if subscribe:
        self._serial_log_listener = self.append_serial_log
        self.ctx.serial.add_log_listener(self._serial_log_listener)
        self._serial_log_clear_listener = self.clear_serial_log_view
        self.ctx.serial.add_log_clear_listener(self._serial_log_clear_listener)
        for line in self.ctx.serial.log_history[-10_000:]:
            self.append_serial_log(line)

    return frame
