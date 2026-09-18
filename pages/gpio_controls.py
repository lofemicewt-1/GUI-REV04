from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Iterable

from core.base_tool import (
    GPIO_MODE_DETECT,
    GPIO_MODE_HIGH,
    GPIO_MODE_LOW,
)
from pages.base_page import SimpleTooltip


GPIO_PIN_MAP = ((0, "D2"), (1, "D3"), (2, "D4"), (3, "D5"))
GPIO_MODES = (GPIO_MODE_HIGH, GPIO_MODE_LOW, GPIO_MODE_DETECT)


def active_serial_identity(ctx: Any, page_name: str) -> tuple[Any, ...] | None:
    """Return one page's active board-session identity.

    Cross-page D4 synchronization is valid only when both pages resolve to
    the same connected transport session.  ``SerialRouter`` supplies a
    manager/generation identity that fences reconnects on a reused COM port;
    small widget-test fakes may provide only a connected selected port.
    """
    serial = getattr(ctx, "serial", None)
    if serial is None:
        return None

    selector = getattr(serial, "selected_port_for_page", None)
    if callable(selector):
        try:
            port = selector(page_name)
        except TypeError:
            port = selector()
    else:
        port = getattr(getattr(ctx, "state", None), "selected_port", None)
    if not port:
        return None
    normalized_port = str(port).strip()
    if not normalized_port:
        return None

    page_identity = getattr(serial, "page_connection_identity", None)
    if callable(page_identity):
        try:
            identity = page_identity(page_name)
        except TypeError:
            identity = page_identity()
        return tuple(identity) if identity is not None else None

    manager_for_port = getattr(serial, "manager_for_port", None)
    if callable(manager_for_port):
        manager = manager_for_port(normalized_port)
        if manager is None:
            return None
        return (
            normalized_port,
            id(manager),
            int(getattr(manager, "connection_generation", 0)),
        )

    if bool(getattr(serial, "is_connected", False)):
        return (normalized_port,)
    return None


class NullGpioControlPanel:
    """No-op stand-in used by widget-free page unit tests."""

    def grid(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def invalidate_modes(self) -> None:
        return None

    def refresh_language(self) -> None:
        return None

    def apply_external_mode(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def invalidate_external_mode(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class GpioControlPanel(ttk.Frame):
    """Shared output/detection controls for the board's four public GPIOs."""

    def __init__(
        self,
        parent: tk.Misc,
        ctx: Any,
        *,
        tr: Callable[..., str],
        gpio_indexes: Iterable[int],
        on_mode_success: Callable[[str, str], None] | None = None,
        on_mode_failure: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent, style="Panel.TFrame", padding=(12, 8))
        self.ctx = ctx
        self.tr = tr
        self.gpio_indexes = tuple(int(index) for index in gpio_indexes)
        self.on_mode_success = on_mode_success
        self.on_mode_failure = on_mode_failure
        self._confirmed_modes: dict[int, str | None] = {
            index: None for index in self.gpio_indexes
        }
        self._labels: dict[int, ttk.Label] = {}
        self._status_labels: dict[int, ttk.Label] = {}
        self._mode_vars: dict[int, tk.StringVar] = {}
        self._combos: dict[int, ttk.Combobox] = {}
        self._tooltips: list[SimpleTooltip] = []

        self.columnconfigure(2, weight=1)
        self._title_label = ttk.Label(self, style="Panel.TLabel")
        self._title_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self._hint_label = ttk.Label(self, style="Muted.TLabel")
        self._hint_label.grid(row=0, column=1, columnspan=2, sticky="w")

        for row, gpio_index in enumerate(self.gpio_indexes, start=1):
            pin = self._pin_for_index(gpio_index)
            label = ttk.Label(self, style="Panel.TLabel")
            label.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=(4, 0))
            self._labels[gpio_index] = label

            mode_var = tk.StringVar()
            combo = ttk.Combobox(
                self,
                textvariable=mode_var,
                values=self._display_values(),
                state="readonly",
                width=18,
            )
            combo.grid(row=row, column=1, sticky="w", padx=(0, 10), pady=(4, 0))
            combo.bind(
                "<<ComboboxSelected>>",
                lambda _event, index=gpio_index: self._on_mode_selected(index),
            )
            self._mode_vars[gpio_index] = mode_var
            self._combos[gpio_index] = combo
            self._tooltips.append(SimpleTooltip(combo, lambda index=gpio_index: self._tooltip(index)))

            status = ttk.Label(self, style="Muted.TLabel")
            status.grid(row=row, column=2, sticky="w", pady=(4, 0))
            self._status_labels[gpio_index] = status

        self.refresh_language()

    @staticmethod
    def _pin_for_index(gpio_index: int) -> str:
        for index, pin in GPIO_PIN_MAP:
            if index == gpio_index:
                return pin
        raise ValueError(f"unsupported GPIO index: {gpio_index}")

    def _display_values(self) -> list[str]:
        return [
            self.tr("gpio_mode_high"),
            self.tr("gpio_mode_low"),
            self.tr("gpio_mode_detect"),
        ]

    def _display_mode(self, mode: str) -> str:
        key = {
            GPIO_MODE_HIGH: "gpio_mode_high",
            GPIO_MODE_LOW: "gpio_mode_low",
            GPIO_MODE_DETECT: "gpio_mode_detect",
        }.get(mode)
        return self.tr(key) if key else mode

    def _mode_for_display(self, value: str) -> str | None:
        for mode in GPIO_MODES:
            if value == self._display_mode(mode):
                return mode
        return None

    def _tooltip(self, gpio_index: int) -> str:
        value = self._mode_vars[gpio_index].get()
        mode = self._mode_for_display(value)
        key = {
            GPIO_MODE_HIGH: "gpio_mode_high_tip",
            GPIO_MODE_LOW: "gpio_mode_low_tip",
            GPIO_MODE_DETECT: "gpio_mode_detect_tip",
        }.get(mode, "gpio_controls_hint")
        return self.tr(key)

    def _on_mode_selected(self, gpio_index: int) -> None:
        mode = self._mode_for_display(self._mode_vars[gpio_index].get())
        if mode is None:
            return
        pin = self._pin_for_index(gpio_index)
        previous = self._confirmed_modes.get(gpio_index)
        serial = getattr(self.ctx, "serial", None)
        if serial is None or not bool(getattr(serial, "is_connected", False)):
            self._restore_mode(gpio_index, previous)
            self._clear_confirmation(gpio_index, pin)
            self._notify_mode_failure(pin)
            messagebox.showwarning(self.tr("error"), self.tr("not_connected"))
            return

        try:
            configure = getattr(serial, "configure_gpio")
            response = configure(pin, mode)
        except Exception as exc:
            response = None
            error = str(exc)
        else:
            error = str(getattr(response, "error", "") or "").strip()

        if response is None or not bool(getattr(response, "ok", False)):
            self._restore_mode(gpio_index, previous)
            detail = error or str(getattr(response, "data", "") or "GPIOCFG failed")
            self._clear_confirmation(gpio_index, pin)
            self._notify_mode_failure(pin)
            messagebox.showerror(self.tr("error"), detail)
            return

        self._confirmed_modes[gpio_index] = mode
        self._status_labels[gpio_index].configure(
            text=self.tr("gpio_mode_applied", mode=self._display_mode(mode))
        )
        extra = getattr(getattr(self.ctx, "state", None), "extra", None)
        if isinstance(extra, dict):
            extra[f"gpio_mode_{pin}"] = mode
        if self.on_mode_success is not None:
            self.on_mode_success(pin, mode)

    def _notify_mode_failure(self, pin: str) -> None:
        if self.on_mode_failure is None:
            return
        try:
            self.on_mode_failure(pin)
        except Exception:
            # A panel failure must still leave its own state visibly
            # unconfirmed if a page has already been torn down.
            return

    def _restore_mode(self, gpio_index: int, mode: str | None) -> None:
        self._mode_vars[gpio_index].set(self._display_mode(mode) if mode else "")

    def _clear_confirmation(self, gpio_index: int, pin: str) -> None:
        """Drop the ACK cache after a failed or disconnected mode change."""
        self._confirmed_modes[gpio_index] = None
        self._status_labels[gpio_index].configure(
            text=self.tr("gpio_mode_unconfirmed")
        )
        extra = getattr(getattr(self.ctx, "state", None), "extra", None)
        if isinstance(extra, dict):
            extra.pop(f"gpio_mode_{pin}", None)

    def invalidate_modes(self) -> None:
        """Forget hardware confirmations when the serial/page session changes."""
        for gpio_index in self.gpio_indexes:
            pin = self._pin_for_index(gpio_index)
            self.invalidate_external_mode(pin)

    def apply_external_mode(self, pin: str, mode: str) -> None:
        """Reflect a separately acknowledged shared-pin write in this panel."""
        index = next(
            (gpio_index for gpio_index in self.gpio_indexes if self._pin_for_index(gpio_index) == pin),
            None,
        )
        if index is None or mode not in GPIO_MODES:
            return
        self._confirmed_modes[index] = mode
        self._mode_vars[index].set(self._display_mode(mode))
        self._status_labels[index].configure(
            text=self.tr("gpio_mode_applied", mode=self._display_mode(mode))
        )
        extra = getattr(getattr(self.ctx, "state", None), "extra", None)
        if isinstance(extra, dict):
            extra[f"gpio_mode_{pin}"] = mode

    def invalidate_external_mode(self, pin: str) -> None:
        """Forget one shared-pin confirmation without sending a command."""
        index = next(
            (gpio_index for gpio_index in self.gpio_indexes if self._pin_for_index(gpio_index) == pin),
            None,
        )
        if index is None:
            return
        self._confirmed_modes[index] = None
        self._mode_vars[index].set("")
        self._status_labels[index].configure(text=self.tr("gpio_mode_unconfirmed"))
        extra = getattr(getattr(self.ctx, "state", None), "extra", None)
        if isinstance(extra, dict):
            extra.pop(f"gpio_mode_{pin}", None)

    def refresh_language(self) -> None:
        self._title_label.configure(text=self.tr("gpio_controls_title"))
        self._hint_label.configure(text=self.tr("gpio_controls_hint"))
        values = self._display_values()
        for gpio_index in self.gpio_indexes:
            pin = self._pin_for_index(gpio_index)
            self._labels[gpio_index].configure(text=f"GPIO{gpio_index} ({pin})")
            self._combos[gpio_index].configure(values=values)
            mode = self._confirmed_modes[gpio_index]
            self._mode_vars[gpio_index].set(self._display_mode(mode) if mode else "")
            if mode:
                self._status_labels[gpio_index].configure(
                    text=self.tr("gpio_mode_applied", mode=self._display_mode(mode))
                )
            else:
                self._status_labels[gpio_index].configure(text=self.tr("gpio_mode_unconfirmed"))
