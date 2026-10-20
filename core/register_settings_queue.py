"""Bounded background register settings, with no Tk or current-page routing."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import dataclass
import threading
from typing import Any, Callable


@dataclass(frozen=True)
class RegisterSetting:
    manager: Any
    address: int
    register: int
    value: int
    clear_mask: int | None
    valid: Callable[[], bool]


class RegisterSettingsQueue:
    """One worker; latest pending value per target/field; atomic read-modify-write.

    A queued job owns a fixed manager and address. Its validator must use only
    plain connection/state data, never Tk. An in-flight command can complete;
    cancellation prevents the next command (especially a stale RMW write).
    """
    def __init__(self, capacity: int = 128):
        self.capacity = capacity
        self._lock = threading.Lock()
        self._pending = OrderedDict()
        self._thread: threading.Thread | None = None
        self._active: RegisterSetting | None = None
        self._closed = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self.completed = self.failed = self.cancelled = self.coalesced = 0

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def submit(self, key, setting: RegisterSetting) -> bool:
        with self._lock:
            if self.closed:
                return False
            if key in self._pending:
                self.coalesced += 1
            elif len(self._pending) >= self.capacity:
                return False
            self._pending[key] = setting
            self._idle.clear()
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="register-settings", daemon=True)
                self._thread.start()
            return True

    def busy_for(self, manager: Any, address: int) -> bool:
        """Non-blocking gate for legacy actions that must follow mode setup."""
        with self._lock:
            jobs = list(self._pending.values())
            if self._active is not None:
                jobs.append(self._active)
        if self.closed:
            return False
        for setting in jobs:
            if setting.manager is manager and setting.address == address:
                try:
                    if setting.valid():
                        return True
                except Exception:
                    return True
        return False

    def close(self) -> None:
        """Cancel pending settings without joining a potentially slow command."""
        with self._lock:
            self._closed.set()
            self.cancelled += len(self._pending)
            self._pending.clear()
            if self._thread is None:
                self._idle.set()

    def wait_idle(self, timeout: float) -> bool:
        """Test/diagnostic synchronization; never called by the Tk UI."""
        return self._idle.wait(timeout)

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._thread = None
                    self._idle.set()
                    return
                _key, setting = self._pending.popitem(last=False)
                self._active = setting
            try:
                outcome = self._execute(setting)
            except Exception as exc:
                outcome = "failed"
                try:
                    setting.manager.emit_log(f"ERROR Register setting failed: {exc}")
                except Exception:
                    pass
            with self._lock:
                setattr(self, outcome, getattr(self, outcome)+1)
                self._active = None

    def _execute(self, setting: RegisterSetting) -> str:
        def current():
            return not self.closed and setting.valid()
        if not current():
            return "cancelled"
        manager = setting.manager
        with manager.snapshot_transaction():
            if not current():
                return "cancelled"
            value = setting.value
            if setting.clear_mask is not None:
                response = manager.read_register_value(setting.address, setting.register, bits=16)
                if not current():
                    return "cancelled"
                if not response.ok:
                    return "failed"
                previous = manager.extract_rdataw_value(response.data, device_addr=setting.address,
                                                       reg_addr=setting.register, bits=16)
                if previous is None:
                    return "failed"
                value = (previous & ~setting.clear_mask) | (value & setting.clear_mask)
            # SerialManager reconnect/disconnect owns this same transport lock.
            # Keep the last identity check and write in one critical section.
            guard = getattr(manager, "connection_guard", nullcontext)
            with guard():
                if not current():
                    return "cancelled"
                response = manager.write_register_value(setting.address, setting.register, value & 0xFFFF, bits=16)
                return "completed" if response.ok else "failed"
