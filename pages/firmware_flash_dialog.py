"""Home-page dialog: flash a user-selected .hex onto an Arduino Nano.

All blocking work (driver check, installer, avrdude) runs on a worker thread;
results come back through ``post_to_ui``.  No firmware is bundled, so the
user must always pick a .hex (the last choice is remembered per session).
"""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from config.languages import t
from core import firmware_flasher as ff
from core.ui_dispatch import post_to_ui
from pages.base_page import fit_toplevel_to_screen
from pages.fast_label import make_label

# Session memory of the last chosen .hex (never persisted to disk).
_last_hex_path: str = ""


def last_hex_path() -> str:
    return _last_hex_path


def remember_hex_path(path: str) -> None:
    global _last_hex_path
    _last_hex_path = str(path or "")


class FirmwareFlashDialog(tk.Toplevel):
    def __init__(
        self,
        home: Any,
        *,
        flasher: ff.FirmwareFlasher | None = None,
        check_driver: Callable[[], ff.DriverStatus] = ff.check_ch340_driver,
        install_driver: Callable[[], ff.DriverStatus] = ff.install_ch340_driver,
        ask_yes_no: Callable[..., bool] = messagebox.askyesno,
        start_thread: Callable[[Callable[[], None]], Any] | None = None,
    ) -> None:
        super().__init__(home)
        self.home = home
        self._closed = False
        self.busy = False
        self._flasher = flasher or ff.FirmwareFlasher()
        self._check_driver = check_driver
        self._install_driver = install_driver
        self._ask_yes_no = ask_yes_no
        self._start_thread = start_thread or self._default_start_thread
        self._session: ff.FlashSession | None = None
        self._port: str = ""
        self._connect_thread: Any = None
        self._status_key = "firmware_status_ready"
        self._status_kwargs: dict[str, Any] = {}
        self._labels: list[tuple[Any, str]] = []
        self.last_result: ff.FlashResult | None = None

        self.hex_var = tk.StringVar(self, value=last_hex_path())
        self.port_var = tk.StringVar(self, value=home.port_var.get().strip())
        self.progress_var = tk.DoubleVar(self, value=0.0)
        self.status_var = tk.StringVar(self, value="")

        self.transient(home.winfo_toplevel())
        fit_toplevel_to_screen(self, preferred_width=640, preferred_height=460, minimum_width=420, minimum_height=300)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        self.refresh_language()

    # -- helpers ----------------------------------------------------------------
    @property
    def language(self) -> str:
        return getattr(self.home, "language", "zh")

    def tr(self, key: str, **kwargs: Any) -> str:
        return t(self.language, key, **kwargs)

    @staticmethod
    def _default_start_thread(fn: Callable[[], None]) -> threading.Thread:
        thread = threading.Thread(target=fn, daemon=True, name="firmware-flash")
        thread.start()
        return thread

    def _post(self, fn: Callable[..., Any], *args: Any) -> None:
        post_to_ui(self, fn, *args)

    def _emit_shared_log(self, line: str) -> None:
        """Tk thread: append to the app's serial log."""
        logger = getattr(self.home.ctx.serial, "emit_log", None)
        if callable(logger):
            try:
                logger(f"[flash] {line}")
            except Exception:
                pass

    def _log(self, line: str) -> None:
        """Any thread: shared serial log + dialog log, both on the Tk thread."""
        self._post(self._emit_shared_log, line)
        self._post(self._append_log, line)

    def _hwnd(self) -> int | None:
        if not sys.platform.startswith("win"):
            return None
        try:
            return int(self.wm_frame(), 16) or None
        except (tk.TclError, ValueError):
            return None

    # -- UI -----------------------------------------------------------------------
    def _build_ui(self) -> None:
        body = ttk.Frame(self, style="Panel.TFrame", padding=14)
        body.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(4, weight=1)

        for row, key in ((0, "firmware_hex_file"), (1, "firmware_port")):
            label = make_label(body, style="Panel.TLabel")
            label.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
            self._labels.append((label, key))

        self.hex_entry = ttk.Entry(body, textvariable=self.hex_var)
        self.hex_entry.grid(row=0, column=1, sticky="ew", pady=5)
        self.browse_btn = ttk.Button(body, style="Secondary.TButton", command=self.browse)
        self.browse_btn.grid(row=0, column=2, padx=(8, 0), pady=5)
        self._labels.append((self.browse_btn, "firmware_browse"))

        ports = tuple(str(v) for v in self.home.port_combo.cget("values"))
        self.port_combo = ttk.Combobox(body, textvariable=self.port_var, values=ports, width=22)
        self.port_combo.grid(row=1, column=1, sticky="w", pady=5)

        controls = ttk.Frame(body, style="Panel.TFrame")
        controls.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        controls.columnconfigure(3, weight=1)
        self.start_btn = ttk.Button(controls, style="Primary.TButton", command=self.start)
        self.start_btn.grid(row=0, column=0, padx=(0, 8))
        self._labels.append((self.start_btn, "firmware_start"))
        self.cancel_btn = ttk.Button(controls, style="Secondary.TButton", command=self.cancel, state="disabled")
        self.cancel_btn.grid(row=0, column=1, padx=(0, 8))
        self._labels.append((self.cancel_btn, "firmware_cancel"))
        self.close_btn = ttk.Button(controls, style="Secondary.TButton", command=self.close)
        self.close_btn.grid(row=0, column=2, padx=(0, 8))
        self._labels.append((self.close_btn, "firmware_close"))
        self.progress = ttk.Progressbar(controls, variable=self.progress_var, maximum=100, mode="determinate")
        self.progress.grid(row=0, column=3, sticky="ew")

        self.status_label = make_label(body, textvariable=self.status_var, style="Panel.TLabel", wraplength=560)
        self.status_label.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 6))

        log_frame = ttk.Frame(body, style="Panel.TFrame")
        log_frame.grid(row=4, column=0, columnspan=3, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=10, wrap="none", state="disabled", font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)
        self._apply_text_colors()

    def _apply_text_colors(self) -> None:
        colors = getattr(getattr(self.home, "ctx", None), "colors", None)
        if not isinstance(colors, dict):
            try:
                from styles.theme import get_colors

                colors = get_colors(getattr(self.home.ctx.state, "theme", "light"))
            except Exception:
                colors = None
        if isinstance(colors, dict):
            bg = colors.get("input_bg") or colors.get("panel")
            fg = colors.get("text")
            try:
                if bg and fg:
                    self.log_text.configure(background=bg, foreground=fg, insertbackground=fg)
            except tk.TclError:
                pass

    def refresh_language(self) -> None:
        try:
            self.title(self.tr("firmware_dialog_title"))
            for widget, key in self._labels:
                widget.configure(text=self.tr(key))
            self.status_var.set(self.tr(self._status_key, **self._status_kwargs))
        except tk.TclError:
            pass

    def _set_status(self, key: str, **kwargs: Any) -> None:
        self._status_key, self._status_kwargs = key, kwargs
        if not self._closed:
            try:
                self.status_var.set(self.tr(key, **kwargs))
            except tk.TclError:
                pass

    def _append_log(self, line: str) -> None:
        if self._closed:
            return
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _set_progress(self, value: float) -> None:
        if not self._closed:
            try:
                self.progress_var.set(float(value))
            except tk.TclError:
                pass

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if self._closed:
            return
        try:
            self.cancel_btn.configure(state="normal" if busy else "disabled")
        except tk.TclError:
            pass
        state = "disabled" if busy else "normal"
        for widget in (self.start_btn, self.browse_btn, self.hex_entry, self.port_combo):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def browse(self) -> None:
        current = self.hex_var.get().strip()
        initial_dir = str(Path(current).parent) if current else ""
        path = filedialog.askopenfilename(
            parent=self,
            title=self.tr("firmware_select_hex_title"),
            initialdir=initial_dir or None,
            filetypes=[(self.tr("firmware_hex_filetype"), "*.hex"), (self.tr("firmware_all_files"), "*.*")],
        )
        if path:
            self.hex_var.set(path)
            remember_hex_path(path)

    # -- flow (Tk thread) ---------------------------------------------------------
    def start(self) -> bool:
        if self.busy:
            return False
        port = self.port_var.get().strip()
        hex_path = self.hex_var.get().strip()
        try:
            if not port:
                raise ff.FirmwareFlashError("firmware_error_no_port")
            ff.validate_hex(hex_path)
        except ff.FirmwareFlashError as exc:
            self._set_status(exc.key, **exc.kwargs)
            messagebox.showwarning(self.tr("error"), self.tr(exc.key, **exc.kwargs), parent=self)
            return False
        remember_hex_path(hex_path)
        locker = getattr(self.home, "firmware_lock", None)
        if callable(locker) and not locker(port):
            messagebox.showwarning(self.tr("error"), self.tr("firmware_busy_locked"), parent=self)
            return False
        self._port = port
        self._flasher.cancel_event.clear()
        self._set_busy(True)
        self._set_progress(0)
        self._set_status("firmware_status_checking_driver")
        self._start_thread(lambda: self._driver_check_worker(port, hex_path))
        return True

    def _driver_check_worker(self, port: str, hex_path: str) -> None:
        try:
            status = self._check_driver()
        except Exception:
            status = ff.DriverStatus(checked=False)
        self._post(self._after_driver_check, port, hex_path, status)

    def _fail_on_ui(self, port: str, exc: BaseException) -> None:
        """Any Tk-thread step failed: report it and always release the lock."""
        self._finish_flash(
            port,
            ff.FlashResult(ok=False, error_key="firmware_error_launch", error_kwargs={"detail": str(exc)}),
        )

    def _after_driver_check(self, port: str, hex_path: str, status: ff.DriverStatus) -> None:
        try:
            self._after_driver_check_inner(port, hex_path, status)
        except Exception as exc:
            self._fail_on_ui(port, exc)

    def _after_driver_check_inner(self, port: str, hex_path: str, status: ff.DriverStatus) -> None:
        if status.needs_install:
            key = "ch340_missing_prompt" if not status.installed else "ch340_problem_prompt"
            if self._ask_yes_no(self.tr("info"), self.tr(key), parent=self):
                self._set_status("firmware_status_installing_driver")
                hwnd = self._hwnd()
                self._start_thread(lambda: self._install_worker(port, hex_path, hwnd))
                return
            self._log(self.tr("ch340_skip_continue"))
        elif status.note:
            self._log(status.note)
        self._begin_flash(port, hex_path)

    def _install_worker(self, port: str, hex_path: str, hwnd: int | None) -> None:
        proceed = True
        kwargs: dict[str, Any] = {}
        try:
            self._install_driver(hwnd=hwnd)
            note = "ch340_install_done"
        except ff.FirmwareFlashError as exc:
            note, kwargs = exc.key, dict(exc.kwargs)
            # Installer still running: never flash on top of it.
            proceed = exc.key != "ch340_install_still_running"
        except Exception as exc:  # never leave the dialog busy
            note, kwargs = "ch340_install_failed", {"code": str(exc)}
        kwargs.setdefault("code", "")
        self._log(self.tr(note, **kwargs))
        if proceed:
            self._post(self._begin_flash, port, hex_path)
        else:
            self._post(
                self._finish_flash,
                port,
                ff.FlashResult(ok=False, error_key=note, error_kwargs=kwargs),
            )

    def _begin_flash(self, port: str, hex_path: str) -> None:
        try:
            self._begin_flash_inner(port, hex_path)
        except Exception as exc:
            self._fail_on_ui(port, exc)

    def _begin_flash_inner(self, port: str, hex_path: str) -> None:
        home = self.home
        self._session = ff.FlashSession(
            port,
            is_connected=home.firmware_port_connected,
            disconnect=home.firmware_release_port,
            reconnect=home.firmware_restore_port,
            schedule=lambda ms, fn: home.after(ms, fn),
        )
        if self._flasher.cancel_event.is_set():
            self._finish_flash(port, ff.FlashResult(ok=False, error_key="firmware_error_cancelled"))
            return
        self._session.begin()
        # A cancelled connect may still own the port: the worker joins it.
        self._connect_thread = getattr(home, "_connect_previous_thread", None)
        self._set_status("firmware_status_flashing", port=port)
        self._start_thread(lambda: self._flash_worker(port, hex_path))

    def _flash_worker(self, port: str, hex_path: str) -> None:
        thread = self._connect_thread
        if thread is not None and thread is not threading.current_thread():
            try:
                deadline = time.monotonic() + ff.PORT_RELEASE_TIMEOUT_S + 30
                while thread.is_alive() and time.monotonic() < deadline:
                    if self._flasher.cancel_event.is_set():
                        break  # flasher.flash() reports the cancel
                    thread.join(0.25)
            except Exception:
                pass
        try:
            result = self._flasher.flash(
                port,
                hex_path,
                log=self._log,
                progress=lambda value: self._post(self._set_progress, value),
            )
        except Exception as exc:
            result = ff.FlashResult(ok=False, error_key="firmware_error_launch", error_kwargs={"detail": str(exc)})
        self._post(self._finish_flash, port, result)

    def _finish_flash(self, port: str, result: ff.FlashResult) -> None:
        self.last_result = result
        if result.ok:
            message_key, kwargs = "firmware_success", {"port": port, "baud": result.baudrate}
        else:
            error = self.tr(result.error_key or "firmware_error_avrdude", **(result.error_kwargs or {"code": result.returncode}))
            message_key, kwargs = "firmware_failed", {"error": error}
        final_text = self.tr(message_key, **kwargs)
        self._emit_shared_log(final_text)
        self._append_log(final_text)

        def done() -> None:
            self._set_busy(False)
            self._set_status(message_key, **kwargs)

        unlocker = getattr(self.home, "firmware_unlock", None)
        if callable(unlocker):
            unlocker(port)  # before the reconnect, which Connect-lock would refuse
        session = self._session
        self._session = None
        if session is not None and session.was_connected:
            # Status first: the reconnect callback replaces it with the result.
            self._set_status("firmware_status_reconnecting" if result.ok else "firmware_status_reconnecting_after_fail", port=port)
            session.finish(result.ok, on_reconnect=done)
        else:
            done()
        if result.ok:
            self._set_progress(100)

    def cancel(self) -> None:
        """Stop the running flash; avrdude is killed by the worker."""
        if self.busy:
            self._flasher.cancel()
            self._set_status("firmware_status_cancelling")

    def close(self) -> None:
        if self.busy:
            if not self._ask_yes_no(self.tr("info"), self.tr("firmware_busy_close"), parent=self):
                return
            # Cancel and close; the worker still finishes, unlocks the Home
            # page and restores the connection (all guarded for a dead dialog).
            self._flasher.cancel()
        self._closed = True
        try:
            self.destroy()
        except tk.TclError:
            pass
