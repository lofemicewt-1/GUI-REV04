from __future__ import annotations

import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Any, Callable

from core import base_tool
from core import chip_status_cache
from core.base_tool import SerialResponse


_LOOSE_RUNTIME_EVENT_RE = re.compile(
    r"E\s+(?P<address>[0-9A-Fa-f]{2})\s+(?P<index>\d+)\s+"
    r"(?P<irqseq>[0-9A-Fa-f]{8})\s+(?P<scan>\d+)\s+"
    r"(?P<status>----|[0-9A-Fa-f]{4})\s+(?P<event>[PRNE])"
)

try:
    import serial
    import serial.tools.list_ports
except Exception:
    serial = None


# CFGEND/CFGDEL rewrite up to 256 EEPROM bytes (~3.3 ms each on AVR, ~0.85 s)
# and CFGAPPLY drives many I2C writes before replying, which can exceed the
# default 0.8 s command timeout.  A late reply would otherwise be reported as
# "No response" and then leak into the next command's read.
CFG_EEPROM_WRITE_TIMEOUT_S = 3.0


def is_fatal_serial_exception(exc: BaseException) -> bool:
    """Return whether a transport exception invalidates the current handle.

    A missing response is represented by an empty frame and is deliberately
    not included here.  Protocol ``ERR`` frames and command-lock contention
    are responses/state decisions too, so they must leave a usable connection
    in place.  OS-level read/write failures and pyserial's ``SerialException``
    mean that subsequent writes would only repeat the same failure.
    """

    if isinstance(exc, TimeoutError):
        return False
    serial_exception = getattr(serial, "SerialException", None)
    if isinstance(serial_exception, type) and isinstance(exc, serial_exception):
        return True
    return isinstance(exc, (PermissionError, BrokenPipeError, ConnectionError, OSError))


@dataclass(frozen=True)
class SerialPortInfo:
    device: str
    name: str


class SerialManager:
    MAX_LOG_HISTORY = 100_000
    CONNECT_READY_TIMEOUT = 30.0
    CONNECT_READY_PROBE_S = 0.3
    """
    Arduino serial protocol adapter.

    The public protocol is PING-less: wait for ^READY REV04~, or probe CAP?
    once a board that is already ready.  Register traffic is RREGW/WREGW.
    """

    def __init__(self, timeout: float = 0.8) -> None:
        self._serial = None
        self._lock = threading.RLock()
        self._command_lock = threading.RLock()
        self._transaction_state_lock = threading.Lock()
        self._transaction_owner: int | None = None
        self._transaction_depth = 0
        self.timeout = timeout
        self.last_error: str = ""
        self.port: str | None = None
        self.baudrate: int = 115200
        self.board_identity: str = ""
        self.board_ready = False
        # Monotonic identity for each successfully opened transport session.
        # The router uses this together with the manager object and port so a
        # delayed page callback from a previous connection cannot update a
        # newly connected board on the same COM port.
        self.connection_generation = 0
        self._log_listeners: list[Callable[[str], None]] = []
        self._log_clear_listeners: list[Callable[[], None]] = []
        self._log_history: list[str] = []
        self._loose_text_lock = threading.Lock()
        self._loose_text_buffer = ""
        self._log_local = threading.local()
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._connection_listeners: list[Callable[[str, str], None]] = []
        # EVSTAT is an unsolicited frame.  Keep it separate from command
        # replies and use an unbounded FIFO so a D2 burst cannot replace the
        # previous event while Tk is busy applying a snapshot.
        self._event_listeners: list[Callable[[chip_status_cache.Snapshot], None]] = []
        self._event_queue: Queue[chip_status_cache.Snapshot] = Queue()
        self._event_dispatch_lock = threading.Lock()
        # The monitor and synchronous command reader share this assembler.
        # In particular, a monitor read may leave a half EVSTAT frame here and
        # the command reader must continue that same frame on the next read.
        self._rx_frame_started = False
        self._rx_frame_chars: list[str] = []
        self._rx_loose_chars: list[str] = []
        self._rx_last_loose_at = 0.0
        self._transport_error = ""

    @property
    def log_history(self) -> list[str]:
        return list(self._log_history)

    def clear_log(self) -> None:
        self._log_history.clear()
        for listener in list(self._log_clear_listeners):
            try:
                listener()
            except Exception:
                self.remove_log_clear_listener(listener)

    @contextmanager
    def capture_logging(self):
        """Capture this thread's command traffic until the caller classifies it."""
        captured: list[str] = []
        stack = getattr(self._log_local, "capture_stack", None)
        if stack is None:
            stack = []
            self._log_local.capture_stack = stack
        stack.append(captured)
        try:
            yield captured
        finally:
            if stack and stack[-1] is captured:
                stack.pop()
            else:
                try:
                    stack.remove(captured)
                except ValueError:
                    pass
            if not stack:
                try:
                    del self._log_local.capture_stack
                except AttributeError:
                    pass

    @contextmanager
    def suppress_logging(self):
        """Keep legacy callers local to their thread; sidecar events still publish."""
        with self.capture_logging():
            yield

    @contextmanager
    def snapshot_transaction(self):
        """Keep a multi-command board snapshot contiguous on the serial port."""
        owner = threading.get_ident()
        self._command_lock.acquire()
        with self._transaction_state_lock:
            self._transaction_owner = owner
            self._transaction_depth += 1
        try:
            yield
        finally:
            with self._transaction_state_lock:
                self._transaction_depth -= 1
                if self._transaction_depth == 0:
                    self._transaction_owner = None
            self._command_lock.release()

    def _transaction_allows_current_thread(self) -> bool:
        owner = threading.get_ident()
        with self._transaction_state_lock:
            return self._transaction_depth == 0 or self._transaction_owner == owner

    def add_log_listener(self, listener: Callable[[str], None]) -> None:
        if listener not in self._log_listeners:
            self._log_listeners.append(listener)

    def remove_log_listener(self, listener: Callable[[str], None]) -> None:
        try:
            self._log_listeners.remove(listener)
        except ValueError:
            pass

    def add_log_clear_listener(self, listener: Callable[[], None]) -> None:
        if listener not in self._log_clear_listeners:
            self._log_clear_listeners.append(listener)

    def remove_log_clear_listener(self, listener: Callable[[], None]) -> None:
        try:
            self._log_clear_listeners.remove(listener)
        except ValueError:
            pass

    def add_connection_listener(self, listener: Callable[[str, str], None]) -> None:
        """Subscribe to one fatal transport loss as ``(port, error)``."""

        if listener not in self._connection_listeners:
            self._connection_listeners.append(listener)

    def remove_connection_listener(self, listener: Callable[[str, str], None]) -> None:
        try:
            self._connection_listeners.remove(listener)
        except ValueError:
            pass

    def add_event_listener(
        self, listener: Callable[[chip_status_cache.Snapshot], None]
    ) -> None:
        """Subscribe to validated unsolicited ``EVSTAT`` snapshots.

        Callbacks run after the serial lock is released.  The router callback
        only enqueues the event; GUI code consumes that queue from Tk's event
        loop rather than being called by this reader thread.
        """

        if listener not in self._event_listeners:
            self._event_listeners.append(listener)
        self._dispatch_event_queue()

    def remove_event_listener(
        self, listener: Callable[[chip_status_cache.Snapshot], None]
    ) -> None:
        try:
            self._event_listeners.remove(listener)
        except ValueError:
            pass

    def _clear_event_queue(self) -> None:
        while True:
            try:
                self._event_queue.get_nowait()
            except Empty:
                return

    def _dispatch_event_queue(self) -> None:
        """Deliver queued events outside ``_lock`` in FIFO order."""

        listeners = list(self._event_listeners)
        if not listeners:
            return
        with self._event_dispatch_lock:
            while True:
                try:
                    snapshot = self._event_queue.get_nowait()
                except Empty:
                    return
                for listener in list(listeners):
                    try:
                        listener(snapshot)
                    except Exception:
                        self.remove_event_listener(listener)

    def _reset_rx_state(self) -> None:
        self._rx_frame_started = False
        self._rx_frame_chars = []
        self._rx_loose_chars = []
        self._rx_last_loose_at = 0.0

    def _feed_rx_bytes(self, data: bytes | bytearray | str) -> list[str]:
        """Feed bytes into the one shared framed/sidecar parser."""

        if isinstance(data, (bytes, bytearray)):
            text = bytes(data).decode("ascii", errors="ignore")
        else:
            text = str(data)
        frames: list[str] = []
        for ch in text:
            if ch == "^":
                self._emit_loose_mcu_text("".join(self._rx_loose_chars), final=True)
                self._rx_loose_chars = []
                self._rx_frame_started = True
                self._rx_frame_chars = []
            elif ch == "~" and self._rx_frame_started:
                frame = "".join(self._rx_frame_chars).strip()
                if frame:
                    frames.append(frame)
                self._rx_frame_started = False
                self._rx_frame_chars = []
            elif self._rx_frame_started:
                self._rx_frame_chars.append(ch)
            else:
                self._rx_loose_chars.append(ch)
                self._rx_last_loose_at = time.monotonic()
        return frames

    def _flush_rx_loose_if_due(self, *, final: bool = False) -> None:
        if not self._rx_loose_chars:
            return
        if not final and time.monotonic() - self._rx_last_loose_at <= 0.08:
            return
        text = "".join(self._rx_loose_chars)
        self._rx_loose_chars = []
        self._rx_last_loose_at = 0.0
        self._emit_loose_mcu_text(text, final=final)

    @staticmethod
    def _is_evstat_frame(frame: str) -> bool:
        # Recognize the frame family before validating its revision/records;
        # a malformed or future EVSTAT must be consumed as an unsolicited
        # event and cannot steal the synchronous command response.
        return frame.strip().split()[:1] == ["EVSTAT"]

    @staticmethod
    def _is_gpio_event_frame(frame: str) -> bool:
        # GPIOEV is a push notification generated by the firmware detector.
        # It must never satisfy a synchronous GPIOCFG/EN command reply.
        return frame.strip().split()[:1] == ["GPIOEV"]

    def _queue_event_frame(self, frame: str) -> None:
        try:
            snapshot = chip_status_cache.parse_evstat_frame(f"^{frame}~")
        except (TypeError, ValueError) as exc:
            self.emit_log(f"ERROR EVSTAT {exc}")
            return
        self.emit_log(f"MCU ^{frame}~")
        self._event_queue.put(snapshot)

    def _process_complete_frame(self, frame: str, *, synchronous: bool = False) -> str:
        """Classify one complete frame; return only a command reply."""

        frame = frame.strip()
        if not frame:
            return ""
        if self._is_evstat_frame(frame):
            self._queue_event_frame(frame)
            return ""
        if self._is_gpio_event_frame(frame):
            self.emit_log(f"MCU ^{frame}~")
            return ""
        if self._is_ready_notification(frame):
            self._update_board_ready_from_frame(frame)
            self.emit_log(f"MCU ^{frame}~")
            return ""
        if synchronous:
            return frame
        self._update_board_ready_from_frame(frame)
        self.emit_log(f"MCU ^{frame}~")
        return ""

    def _publish_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self._log_history.append(line)
        if len(self._log_history) > self.MAX_LOG_HISTORY:
            self._log_history = self._log_history[-self.MAX_LOG_HISTORY:]
        for listener in list(self._log_listeners):
            try:
                listener(line)
            except Exception:
                self.remove_log_listener(listener)

    @staticmethod
    def _is_sidecar_log(message: str) -> bool:
        return message.startswith(("MCU EVT ", "MCU I ", "MCU_TEXT "))

    def _publish_captured_logs(self, messages: list[str]) -> None:
        for message in messages:
            self._publish_log(message)

    def emit_log(self, message: str) -> None:
        if not (
            message.startswith("TX ")
            or message.startswith("MCU")
            or message.startswith("ERROR ")
        ):
            return
        if self._is_sidecar_log(message):
            self._publish_log(message)
            return
        stack = getattr(self._log_local, "capture_stack", None)
        if stack:
            stack[-1].append(message)
            return
        self._publish_log(message)

    @property
    def is_available(self) -> bool:
        return serial is not None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and getattr(self._serial, "is_open", False)

    def list_ports(self) -> list[str]:
        return [info.device for info in self.list_port_info()]

    def list_port_info(self, *, probe_missing: bool = True) -> list[SerialPortInfo]:
        self.last_error = ""
        if serial is None:
            self.last_error = "pyserial is not installed"
            return []

        ports: dict[str, str] = {}

        try:
            for p in serial.tools.list_ports.comports():
                if p.device and p.device not in ports:
                    ports[p.device] = self._port_display_name(p)
        except Exception as exc:
            self.last_error = str(exc)

        if sys.platform.startswith("win") and not ports and probe_missing:
            for port in self._scan_windows_com_ports():
                ports[port] = port

        return [
            SerialPortInfo(device=port, name=name)
            for port, name in sorted(ports.items(), key=lambda item: self._port_sort_key(item[0]))
        ]

    def _port_display_name(self, port_info: object) -> str:
        device = str(getattr(port_info, "device", "") or "")
        description = str(getattr(port_info, "description", "") or "").strip()
        name = str(getattr(port_info, "name", "") or "").strip()
        manufacturer = str(getattr(port_info, "manufacturer", "") or "").strip()

        parts: list[str] = []
        for value in (description, manufacturer, name):
            if value and value not in parts and value != device:
                parts.append(value)

        return " / ".join(parts) if parts else device

    def _scan_windows_com_ports(self) -> list[str]:
        found: list[str] = []
        if serial is None:
            return found

        for i in range(1, 257):
            port = f"COM{i}"
            try:
                s = serial.Serial(port=port, baudrate=115200, timeout=0.05, write_timeout=0.05)
                s.close()
                found.append(port)
            except Exception:
                continue
        return found

    def _port_sort_key(self, port: str) -> tuple[int, str]:
        upper = port.upper()
        if upper.startswith("COM"):
            try:
                return (int(upper[3:]), upper)
            except Exception:
                return (9999, upper)
        return (9999, upper)

    def connect(self, port: str, baudrate: int = 115200) -> SerialResponse:
        if serial is None:
            return SerialResponse(False, error="pyserial is not installed. Please run: pip install pyserial")

        with self._lock:
            try:
                self.disconnect()

                open_port = port
                if sys.platform.startswith("win") and port.upper().startswith("COM"):
                    try:
                        n = int(port[3:])
                        if n >= 10:
                            open_port = rf"\\.\{port}"
                    except Exception:
                        pass

                self._serial = serial.Serial(
                    port=open_port,
                    baudrate=baudrate,
                    timeout=self.timeout,
                    write_timeout=self.timeout,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                )

                self.board_ready = False
                self.board_identity = ""
                self.port = port
                self.baudrate = baudrate
                self.last_error = ""
                self._transport_error = ""

                ready = self._complete_connect_handshake()
                if not ready.ok:
                    try:
                        self._serial.close()
                    except Exception:
                        pass
                    self._serial = None
                    self.port = None
                    self.board_identity = ""
                    self.board_ready = False
                    return ready

                self.connection_generation += 1
                self._start_monitor()
                return SerialResponse(True, data=self.board_identity)
            except Exception as exc:
                self._serial = None
                self.port = None
                self.board_identity = ""
                self.board_ready = False
                self.last_error = str(exc)
                return SerialResponse(False, error=str(exc))

    def disconnect(self) -> None:
        self._monitor_stop.set()
        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None
            self.port = None
            self.board_identity = ""
            self.board_ready = False
            self._reset_rx_state()
            self._clear_event_queue()
        with self._loose_text_lock:
            self._loose_text_buffer = ""

    def _mark_transport_failure(self, exc: BaseException, expected_serial: Any = None) -> bool:
        """Close and invalidate one failed handle, notifying subscribers once."""

        if not is_fatal_serial_exception(exc):
            return False

        error = str(exc).strip() or exc.__class__.__name__
        failed_handle = None
        failed_port = ""
        with self._lock:
            current = self._serial
            if expected_serial is not None and current is not expected_serial:
                return False
            failed_handle = expected_serial if expected_serial is not None else current
            if failed_handle is None:
                return False
            failed_port = str(self.port or "").strip()
            self._monitor_stop.set()
            self._serial = None
            self.port = None
            self.board_identity = ""
            self.board_ready = False
            self._reset_rx_state()
            self._clear_event_queue()
            if not self._transport_error:
                self._transport_error = error
                self.last_error = error

        try:
            failed_handle.close()
        except Exception:
            pass
        with self._loose_text_lock:
            self._loose_text_buffer = ""

        self.emit_log(f"ERROR {error}")

        for listener in list(self._connection_listeners):
            try:
                listener(failed_port, error)
            except Exception:
                self.remove_connection_listener(listener)
        return True

    def _update_board_ready_from_frame(self, frame: str) -> None:
        if base_tool.is_ready_notification(frame):
            self.board_identity = base_tool.ready_notification_body()
            self.board_ready = True

    def _is_ready_notification(self, frame: str) -> bool:
        return base_tool.is_ready_notification(frame)

    def _wait_for_ready_notification(self, timeout: float) -> bool:
        handle = self._serial
        if handle is None or not getattr(handle, "is_open", False):
            return False
        deadline = time.monotonic() + max(0.0, timeout)
        saved_timeout = getattr(handle, "timeout", self.timeout)
        try:
            while time.monotonic() < deadline:
                if self.board_ready:
                    return True
                remaining = deadline - time.monotonic()
                handle.timeout = min(0.2, max(0.01, remaining))
                try:
                    data = handle.read(1)
                except Exception as exc:
                    if is_fatal_serial_exception(exc):
                        raise
                    continue
                if not data:
                    continue
                for frame in self._feed_rx_bytes(data):
                    self._process_complete_frame(frame, synchronous=False)
                    if self.board_ready:
                        return True
        finally:
            try:
                handle.timeout = saved_timeout
            except Exception:
                pass
        return self.board_ready

    def _probe_cap_during_connect(self) -> SerialResponse:
        handle = self._serial
        if handle is None or not getattr(handle, "is_open", False):
            return SerialResponse(False, error="Serial is not connected")
        command = base_tool.build_frame(chip_status_cache.build_capability_body())
        self.emit_log(f"TX {command}")
        saved_timeout = getattr(handle, "timeout", self.timeout)
        try:
            handle.write(command.encode("ascii", errors="ignore"))
            handle.flush()
            deadline = time.monotonic() + self.timeout
            handle.timeout = min(0.2, self.timeout)
            while time.monotonic() < deadline:
                try:
                    data = handle.read(1)
                except Exception as exc:
                    if is_fatal_serial_exception(exc):
                        raise
                    return SerialResponse(False, error=str(exc))
                if not data:
                    continue
                for frame in self._feed_rx_bytes(data):
                    if self._is_ready_notification(frame):
                        self._update_board_ready_from_frame(frame)
                        self.emit_log(f"MCU ^{frame}~")
                        continue
                    if self._is_evstat_frame(frame):
                        self._queue_event_frame(frame)
                        continue
                    if self._is_gpio_event_frame(frame):
                        self.emit_log(f"MCU ^{frame}~")
                        continue
                    self.emit_log(f"MCU ^{frame}~")
                    return SerialResponse(True, data=frame)
        except Exception as exc:
            return SerialResponse(False, error=str(exc))
        finally:
            try:
                handle.timeout = saved_timeout
            except Exception:
                pass
        return SerialResponse(False, error="No response for command: ^CAP?~")

    def _complete_connect_handshake(self) -> SerialResponse:
        if self._wait_for_ready_notification(self.CONNECT_READY_PROBE_S):
            return SerialResponse(True, data=self.board_identity)
        cap_response = self._probe_cap_during_connect()
        if self.board_ready:
            return SerialResponse(True, data=self.board_identity)
        if cap_response.ok:
            try:
                capabilities = chip_status_cache.parse_cap_frame(
                    chip_status_cache.frame_body(cap_response.data)
                )
            except (TypeError, ValueError) as exc:
                cap_response = SerialResponse(
                    False,
                    data=cap_response.data,
                    error=str(exc),
                )
            else:
                if capabilities.revision == "FLAG3" and capabilities.push_event:
                    self.board_ready = True
                    self.board_identity = "CAP FLAG3"
                    return SerialResponse(True, data=self.board_identity)
        remaining = self.CONNECT_READY_TIMEOUT - self.CONNECT_READY_PROBE_S
        if remaining > 0 and self._wait_for_ready_notification(remaining):
            return SerialResponse(True, data=self.board_identity)
        return SerialResponse(
            False,
            error=cap_response.error or "No READY REV04 from board",
        )

    def _start_monitor(self) -> None:
        self._monitor_stop.set()
        stop_event = threading.Event()
        self._monitor_stop = stop_event
        self._monitor_thread = threading.Thread(
            target=self._monitor_serial_output,
            args=(stop_event,),
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor_serial_output(self, stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or self._monitor_stop

        while not stop_event.is_set():
            if not self.is_connected:
                time.sleep(0.05)
                continue

            got_lock = self._lock.acquire(blocking=False)
            if not got_lock:
                time.sleep(0.01)
                continue

            transport_error: BaseException | None = None
            expected_serial = None
            frames: list[str] = []
            flush_loose = False
            try:
                expected_serial = self._serial
                waiting = getattr(expected_serial, "in_waiting", 0)
                if waiting:
                    data = expected_serial.read(waiting)
                    frames = self._feed_rx_bytes(data)
                else:
                    data = b""
                    flush_loose = bool(self._rx_loose_chars)
            except Exception as exc:
                transport_error = exc
                data = b""
            finally:
                self._lock.release()

            if transport_error is not None:
                if stop_event.is_set():
                    break
                if self._mark_transport_failure(transport_error, expected_serial):
                    break

            for frame in frames:
                self._process_complete_frame(frame)
            if flush_loose:
                with self._lock:
                    self._flush_rx_loose_if_due()
            self._dispatch_event_queue()

            time.sleep(0.01)

    # ---------------------------------------------------------------------
    # Low-level command helpers
    # ---------------------------------------------------------------------
    def send_command(
        self,
        body: str,
        *,
        expect_response: bool = True,
        display_body: str | None = None,
        response_timeout: float | None = None,
    ) -> SerialResponse:
        """
        body does not include ^ or ~, for example:
            "RREGW 44 1E 16 1"
            "WREGW 44 14 16 0003"
        """
        if not self._transaction_allows_current_thread():
            error = "Serial transaction busy"
            self.emit_log(f"ERROR {error}")
            return SerialResponse(False, error=error)

        if not self.is_connected:
            error = "Serial is not connected"
            self.emit_log(f"ERROR {error}")
            return SerialResponse(False, error=error)

        cmd = base_tool.build_frame(body)
        display_cmd = f"^{display_body if display_body is not None else body}~"
        payload = cmd.encode("ascii", errors="ignore")

        if not self._command_lock.acquire(blocking=False):
            if not self._transaction_allows_current_thread():
                error = "Serial transaction busy"
            else:
                error = "Serial command is busy"
            self.emit_log(f"ERROR {error}")
            return SerialResponse(False, error=error)

        try:
            with self._lock:
                handle = self._serial
                if handle is None or not getattr(handle, "is_open", False):
                    error = "Serial is not connected"
                    self.emit_log(f"ERROR {error}")
                    return SerialResponse(False, error=error)
                try:
                    self.emit_log(f"TX {display_cmd}")
                    handle.write(payload)
                    handle.flush()

                    if not expect_response:
                        return SerialResponse(True, data=cmd)

                    data = self._read_frame(handle, timeout=response_timeout)
                    if not data:
                        error = f"No response for command: {display_cmd}"
                        self.emit_log(f"ERROR {error}")
                        return SerialResponse(False, error=error)
                    self._update_board_ready_from_frame(data)
                    self.emit_log(f"MCU ^{data}~")
                    # Success/failure semantics are unchanged (callers decide);
                    # this only states where the firmware said it failed.
                    description = base_tool.describe_firmware_error(data)
                    if description is not None:
                        self.emit_log(f"ERROR {description}")
                    return SerialResponse(True, data=data)
                except Exception as exc:
                    marked = self._mark_transport_failure(exc, handle)
                    if not marked and not is_fatal_serial_exception(exc):
                        self.emit_log(f"ERROR {exc}")
                    return SerialResponse(False, error=str(exc))
        finally:
            try:
                self._command_lock.release()
            except RuntimeError:
                pass
            # ``_read_frame`` may have consumed an EVSTAT while waiting for a
            # normal command reply.  Publish it only after the serial lock is
            # released so a listener cannot re-enter transport/Tk code.
            self._dispatch_event_queue()

    def _read_frame(self, serial_handle: Any = None, *, timeout: float | None = None) -> str:
        """
        Read one Arduino ^...~ frame.

        Examples:
            ^READY REV04~
            ^RDATAW 44 1E 16 1 5081~
            ^OK WREGW 44 14 16 1~
        """
        handle = serial_handle if serial_handle is not None else self._serial
        if handle is None or not getattr(handle, "is_open", False):
            return ""

        deadline = time.monotonic() + (self.timeout if timeout is None else max(self.timeout, timeout))

        while time.monotonic() < deadline:
            try:
                b = handle.read(1)
            except Exception as exc:
                if is_fatal_serial_exception(exc):
                    raise
                return ""

            if not b:
                continue

            for frame in self._feed_rx_bytes(b):
                response = self._process_complete_frame(frame, synchronous=True)
                if response:
                    return response
            self._flush_rx_loose_if_due()

        self._flush_rx_loose_if_due(final=True)
        return ""

    def _drain_loose_mcu_text(self) -> None:
        handle = self._serial
        if handle is None or not getattr(handle, "is_open", False):
            return

        deadline = time.monotonic() + 0.03
        chars: list[str] = []
        while time.monotonic() < deadline:
            try:
                waiting = getattr(handle, "in_waiting", 0)
                if not waiting:
                    time.sleep(0.002)
                    continue
                b = handle.read(1)
            except Exception as exc:
                self._mark_transport_failure(exc, handle)
                break
            if not b:
                continue
            ch = b.decode("ascii", errors="ignore")
            if ch == "^":
                break
            chars.append(ch)

        self._emit_loose_mcu_text("".join(chars), final=True)

    def _emit_loose_mcu_text(self, text: str, *, final: bool = False) -> None:
        """Publish complete unframed MCU lines without contaminating ^...~ replies."""
        with self._loose_text_lock:
            pending = self._loose_text_buffer + text
            lines: list[str] = []
            self._loose_text_buffer = ""
            for piece in pending.splitlines(keepends=True):
                if piece.endswith(("\r", "\n")):
                    lines.append(piece.rstrip("\r\n"))
                else:
                    self._loose_text_buffer = piece
            if final and self._loose_text_buffer:
                lines.append(self._loose_text_buffer)
                self._loose_text_buffer = ""

        for line in lines:
            self._emit_loose_mcu_line(line)

    def _emit_loose_mcu_line(self, text: str) -> None:
        text = text.strip()
        if not text:
            return

        event = _LOOSE_RUNTIME_EVENT_RE.fullmatch(text)
        if event is not None:
            address = int(event.group("address"), 16)
            status = event.group("status").upper()
            status_text = status if status == "----" else f"0x{int(status, 16):04X}"
            self.emit_log(
                "MCU EVT "
                f"addr=0x{address:02X} idx={int(event.group('index'))} "
                f"irqseq={event.group('irqseq').upper()} scan={int(event.group('scan'))} "
                f"status={status_text} event={event.group('event')}"
            )
            return

        for match in re.finditer(r"\bI\s+([0-9A-Fa-f]+)\s+(\d+)\s+(\d+)", text):
            reg_data, is_stage0, is_stage1 = match.groups()
            self.emit_log(
                f"MCU I reg_data=0x{int(reg_data, 16):X} isStage0={is_stage0} isStage1={is_stage1}"
            )

        unmatched = re.sub(r"\bI\s+[0-9A-Fa-f]+\s+\d+\s+\d+", "", text).strip()
        if unmatched:
            self.emit_log(f"MCU_TEXT {unmatched}")

    # ---------------------------------------------------------------------
    # Arduino protocol commands
    # ---------------------------------------------------------------------
    def read_board_flag(self, *, suppress_idle: bool = False) -> SerialResponse:
        if not suppress_idle:
            return base_tool.read_board_flag(self.send_command)

        with self.capture_logging() as captured:
            response = base_tool.read_board_flag(self.send_command)
        if not base_tool.is_idle_board_flag_response(response) and not base_tool.is_serial_contention_error(
            response.error
        ):
            self._publish_captured_logs(captured)
        return response

    def read_board_flag_for_poll(self) -> SerialResponse:
        """Read FLAG for Home polling and mute only a successfully idle reply."""
        return self.read_board_flag(suppress_idle=True)

    @staticmethod
    def _status_frame(response: SerialResponse) -> str:
        if not response.ok:
            raise chip_status_cache.FrameError(response.error or response.data or "status command failed")
        return chip_status_cache.frame_body(response.data)

    def _validated_status_command(
        self,
        body: str,
        parser: Callable[[str], Any],
    ) -> SerialResponse:
        """Send one status-cache command and validate its framed reply.

        The command remains a normal manager transaction, so page I/O and the
        status worker share the existing command lock.  Validation here keeps
        malformed or old-firmware replies out of the Home cache.
        """

        response = self.send_command(body, expect_response=True)
        if not response.ok:
            return response
        try:
            parser(self._status_frame(response))
        except (TypeError, ValueError) as exc:
            description = base_tool.describe_firmware_error(response.data)
            error = base_tool.format_firmware_error(response.data) if description else str(exc)
            return SerialResponse(False, data=response.data, error=error)
        return response

    def read_status_capabilities(self) -> SerialResponse:
        return self._validated_status_command(
            chip_status_cache.build_capability_body(), chip_status_cache.parse_cap_frame
        )

    def set_status_session(self, session_id: int) -> SerialResponse:
        return self._validated_status_command(
            chip_status_cache.build_session_body(session_id), chip_status_cache.parse_session_frame
        )

    def configure_status_target(
        self,
        slot: int,
        chip_type: str,
        address: int,
        generation: int,
    ) -> SerialResponse:
        expected = chip_status_cache.TargetIdentity(slot, chip_type, address, generation)
        expected.validate()
        response = self.send_command(
            chip_status_cache.build_tgt_body(slot, address, generation, chip_type=chip_type),
            expect_response=True,
        )
        if not response.ok:
            return response
        try:
            reply = chip_status_cache.parse_tgt_frame(self._status_frame(response))
        except (TypeError, ValueError) as exc:
            return SerialResponse(False, data=response.data, error=str(exc))
        if reply.result == "ERR" or reply.identity != expected:
            return SerialResponse(False, data=response.data, error=response.data or "TGT reply mismatch")
        return response

    def clear_status_target(self, slot: int, generation: int) -> SerialResponse:
        response = self.send_command(
            chip_status_cache.build_tgt_clear_body(slot, generation),
            expect_response=True,
        )
        if not response.ok:
            return response
        try:
            reply = chip_status_cache.parse_tgt_clear_frame(self._status_frame(response))
        except (TypeError, ValueError) as exc:
            return SerialResponse(False, data=response.data, error=str(exc))
        if reply.result != "OK" or reply.slot != slot or reply.generation != generation:
            return SerialResponse(False, data=response.data, error=response.data or "TGTCLR reply mismatch")
        return response

    def set_status_monitor(self, slot: int, enabled: bool, *, chip_type: str | None = None) -> SerialResponse:
        response = self.send_command(
            chip_status_cache.build_monitor_body(slot, enabled),
            expect_response=True,
        )
        if not response.ok:
            return response
        try:
            reply = chip_status_cache.parse_mon_frame(self._status_frame(response))
        except (TypeError, ValueError) as exc:
            return SerialResponse(False, data=response.data, error=str(exc))
        if (
            reply.result != "OK"
            or reply.slot != slot
            or reply.enabled is not bool(enabled)
            or (chip_type is not None and reply.chip_type != chip_type)
        ):
            return SerialResponse(False, data=response.data, error=response.data or "MON reply mismatch")
        return response

    def read_status_snapshot(self) -> SerialResponse:
        return self._validated_status_command(
            chip_status_cache.build_stat_body(), chip_status_cache.parse_stat_frame
        )

    def acknowledge_interrupt(self, sequence: int) -> SerialResponse:
        return base_tool.acknowledge_interrupt(self.send_command, sequence)

    def read_gpio(self, pin: str | int) -> SerialResponse:
        return base_tool.read_gpio(self.send_command, pin)

    def configure_gpio(self, pin: str | int, mode: str) -> SerialResponse:
        return base_tool.configure_gpio_mode(self.send_command, pin, mode)

    def read_adc(self, pin: str) -> SerialResponse:
        return base_tool.read_adc(self.send_command, pin)

    def read_frequency(self) -> SerialResponse:
        return base_tool.read_frequency(self.send_command)

    def set_i2c_speed(self, speed_hz: int) -> SerialResponse:
        return base_tool.set_i2c_speed(self.send_command, speed_hz)

    def read_i2c_speed(self) -> SerialResponse:
        return base_tool.read_i2c_speed(self.send_command)

    def write_register_values(
        self,
        device_addr: int,
        reg_addr: int,
        values: list[int] | tuple[int, ...],
        *,
        bits: int = 16,
    ) -> SerialResponse:
        return base_tool.write_register_values(
            self.send_command,
            device_addr,
            reg_addr,
            values,
            bits=bits,
            log=self.emit_log,
        ).response

    def read_register_values(
        self,
        device_addr: int,
        reg_addr: int,
        *,
        bits: int = 16,
        count: int = 1,
    ) -> SerialResponse:
        return base_tool.read_register_values(
            self.send_command,
            device_addr,
            reg_addr,
            bits=bits,
            count=count,
            log=self.emit_log,
        ).response

    def write_register_value(
        self,
        device_addr: int,
        reg_addr: int,
        value: int,
        *,
        bits: int,
    ) -> SerialResponse:
        """Write one raw 8/16-bit value through the unified WREGW protocol."""
        return base_tool.write_register_value(
            self.send_command,
            device_addr,
            reg_addr,
            value,
            bits=bits,
            log=self.emit_log,
        ).response

    def read_register_value(self, device_addr: int, reg_addr: int, *, bits: int) -> SerialResponse:
        """Read one raw 8/16-bit value through the unified RREGW protocol."""
        return base_tool.read_register_value(
            self.send_command,
            device_addr,
            reg_addr,
            bits=bits,
            log=self.emit_log,
        ).response

    def _cfg_response(self, response: SerialResponse) -> SerialResponse:
        if response.ok and response.data.strip().upper().startswith("ERR "):
            return SerialResponse(False, data=response.data, error=base_tool.format_firmware_error(response.data))
        return response

    def cfg_query(self) -> SerialResponse:
        """Query EEPROM preset summary: CFG? -> ^CFG ACTIVE=... SLOTS=...~"""
        return self._cfg_response(self.send_command(base_tool.build_cfg_query_command()))

    def cfg_read(self, slot: int) -> SerialResponse:
        """Read one EEPROM preset slot: CFGREAD <slot> -> ^CFGREAD <slot> <HEX> CRC=<crc>~"""
        return self._cfg_response(self.send_command(base_tool.build_cfg_read_command(slot)))

    def cfg_begin(self, slot: int, length: int, crc: int) -> SerialResponse:
        """Begin preset upload: CFGBEGIN <slot> <len> <crc> -> ^CFGBEGIN OK~"""
        return self._cfg_response(self.send_command(base_tool.build_cfg_begin_command(slot, length, crc)))

    def cfg_data(self, offset: int, chunk_hex: str) -> SerialResponse:
        """Send preset data chunk: CFGDATA <offset> <hex> -> ^CFGDATA OK~"""
        return self._cfg_response(self.send_command(base_tool.build_cfg_data_command(offset, chunk_hex)))

    def cfg_end(self, slot: int) -> SerialResponse:
        """End preset upload: CFGEND <slot> -> ^CFGEND OK CRC=<crc>~"""
        return self._cfg_response(self.send_command(base_tool.build_cfg_end_command(slot), response_timeout=CFG_EEPROM_WRITE_TIMEOUT_S))

    def cfg_use(self, slot: int) -> SerialResponse:
        """Mark a slot as active boot preset: CFGUSE <slot> -> ^CFGUSE OK <slot>~"""
        return self._cfg_response(self.send_command(base_tool.build_cfg_use_command(slot)))

    def cfg_apply(self, slot: int) -> SerialResponse:
        """Apply a stored preset immediately: CFGAPPLY <slot> -> ^CFGAPPLY OK <slot>~ or ^ERR CFG_UNSUPPORTED~"""
        return self._cfg_response(self.send_command(base_tool.build_cfg_apply_command(slot), response_timeout=CFG_EEPROM_WRITE_TIMEOUT_S))

    def cfg_delete(self, slot: int) -> SerialResponse:
        """Clear a stored preset slot: CFGDEL <slot> -> ^CFGDEL OK <slot>~"""
        return self._cfg_response(self.send_command(base_tool.build_cfg_delete_command(slot), response_timeout=CFG_EEPROM_WRITE_TIMEOUT_S))

    def sync_config_preset(self, slot: int, payload: bytes, *, set_active: bool = True) -> SerialResponse:
        """
        Pack and upload a BoardStoredPreset to EEPROM, verify readback, optionally set as active.
        Returns ok=False on any failure or readback mismatch.
        """
        from core.arduino_preset_sync import chunk_payload_hex, crc16_ccitt_false

        payload = bytes(payload)
        length = len(payload)
        if length not in (128, 256):
            return SerialResponse(False, error="CFG record length must be 128 or 256 bytes")
        crc = crc16_ccitt_false(payload)

        with self.snapshot_transaction():
            resp = self.cfg_begin(slot, length, crc)
            if not resp.ok:
                return resp

            for offset, chunk_hex in chunk_payload_hex(payload):
                resp = self.cfg_data(offset, chunk_hex)
                if not resp.ok:
                    return resp

            resp = self.cfg_end(slot)
            if not resp.ok:
                return resp

            resp = self.cfg_read(slot)
            if not resp.ok:
                return resp

            readback_hex = self._extract_cfgread_hex(resp.data)
            if readback_hex is None:
                return SerialResponse(False, error="CFGREAD response format invalid")

            if bytes.fromhex(readback_hex) != payload:
                return SerialResponse(False, error="CFG readback mismatch")

            if set_active:
                resp = self.cfg_use(slot)
                if not resp.ok:
                    return resp

            return SerialResponse(True, data=resp.data if set_active else "CFG readback verified")

    def _extract_cfgread_hex(self, text: str) -> str | None:
        """
        Extract the HEX field from a CFGREAD response.
        Format: CFGREAD <slot> <HEX> CRC=<crc>
        """
        match = re.fullmatch(r"CFGREAD\s+\d+\s+([0-9A-Fa-f]+)\s+CRC=([0-9A-Fa-f]{4})", text.strip())
        if not match:
            return None
        from core.arduino_preset_sync import crc16_ccitt_false
        try:
            raw = bytes.fromhex(match.group(1))
        except ValueError:
            return None
        if len(raw) not in (128, 256) or crc16_ccitt_false(raw) != int(match.group(2), 16):
            return None
        return match.group(1).upper()

    # ---------------------------------------------------------------------
    # Response parsing
    # ---------------------------------------------------------------------
    def extract_rdataw_values(
        self,
        text: str,
        *,
        device_addr: int,
        reg_addr: int,
        bits: int,
        count: int,
    ) -> tuple[int, ...] | None:
        """Extract a response only when the full RDATAW echo matches the request."""
        return base_tool.extract_rdataw_values(
            text,
            device_addr=device_addr,
            reg_addr=reg_addr,
            bits=bits,
            count=count,
        )

    def extract_rdataw_value(
        self,
        text: str,
        *,
        device_addr: int,
        reg_addr: int,
        bits: int,
    ) -> int | None:
        """Extract one exact RDATAW value; legacy RDATA frames are rejected."""
        return base_tool.extract_rdataw_value(
            text,
            device_addr=device_addr,
            reg_addr=reg_addr,
            bits=bits,
        )


class SerialRouter:
    """
    Route the existing ``ctx.serial`` API to one of several connected boards.

    Page code uses the same strict RREGW/WREGW methods while the router chooses
    the SerialManager bound to the current page.
    """

    def __init__(
        self,
        state: Any,
        *,
        manager_factory: Callable[..., SerialManager] = SerialManager,
        timeout: float = 0.8,
    ) -> None:
        self.state = state
        self.timeout = timeout
        self._manager_factory = manager_factory
        self._scanner = self._make_manager()
        self._managers: dict[str, SerialManager] = {}
        # Keep a router-side generation as a second source of truth for
        # SerialManager-compatible test/extension managers that do not expose
        # ``connection_generation`` themselves.
        self._manager_connection_generations: dict[int, int] = {}
        self._log_listeners: list[Callable[[str], None]] = []
        self._log_clear_listeners: list[Callable[[], None]] = []
        self._router_log_history: list[str] = []
        self._connection_listeners: list[Callable[[str, str], None]] = []
        self._manager_connection_listeners: dict[int, tuple[Any, Callable[[str, str], None]]] = {}
        self._manager_event_listeners: dict[int, tuple[Any, Callable[[chip_status_cache.Snapshot], None]]] = {}
        self._event_queue: Queue[tuple[str, Any, chip_status_cache.Snapshot]] = Queue()
        self._event_listeners: list[Callable[[str, chip_status_cache.Snapshot], None]] = []
        self._connection_errors: dict[str, str] = {}
        self.last_error = ""

    def _make_manager(self) -> SerialManager:
        try:
            return self._manager_factory(timeout=self.timeout)
        except TypeError:
            return self._manager_factory()

    @property
    def is_available(self) -> bool:
        return self._scanner.is_available

    @property
    def is_connected(self) -> bool:
        manager = self._current_manager()
        return manager is not None and manager.is_connected

    @property
    def log_history(self) -> list[str]:
        manager = self._current_manager()
        if manager is not None:
            return manager.log_history
        lines: list[str] = []
        for item in self._managers.values():
            lines.extend(item.log_history)
        return lines or list(self._router_log_history)

    def list_ports(self) -> list[str]:
        return [info.device for info in self.list_port_info()]

    def list_port_info(self, *, probe_missing: bool = True) -> list[SerialPortInfo]:
        self.last_error = ""
        try:
            info = self._scanner.list_port_info(probe_missing=probe_missing)
        except TypeError:
            info = self._scanner.list_port_info()
        self.last_error = getattr(self._scanner, "last_error", "")
        seen = {item.device for item in info}
        for port in self.connected_ports():
            if port not in seen:
                info.append(SerialPortInfo(port, self._port_label(port)))
        return info

    def connected_ports(self) -> list[str]:
        return [port for port, manager in self._managers.items() if manager.is_connected]

    def is_port_connected(self, port: str | None) -> bool:
        if not port:
            return False
        manager = self._managers.get(str(port).strip())
        return manager is not None and manager.is_connected

    def is_port_ready(self, port: str | None) -> bool:
        if not port:
            return False
        manager = self._managers.get(str(port).strip())
        return manager is not None and manager.is_connected and bool(manager.board_ready)

    def manager_for_port(self, port: str | None) -> SerialManager | None:
        """Return one connected manager for port-pinned background work."""
        if not port:
            return None
        manager = self._managers.get(str(port).strip())
        return manager if manager is not None and manager.is_connected else None

    def connection_identity(self, port: str | None) -> tuple[str, int, int] | None:
        """Return the active board-session identity for one routed port.

        The port alone is insufficient because a board can disconnect and be
        replaced while the operating system keeps the same COM name.  The
        manager identity fences a removed manager, and the generation fences
        a reconnect that reuses that manager object.
        """
        if not port:
            return None
        normalized = str(port).strip()
        manager = self._managers.get(normalized)
        if manager is None or not manager.is_connected:
            return None
        return (
            normalized,
            id(manager),
            max(
                int(getattr(manager, "connection_generation", 0)),
                self._manager_connection_generations.get(id(manager), 0),
            ),
        )

    def page_connection_identity(
        self, page_name: str | None = None
    ) -> tuple[str, int, int] | tuple[str] | None:
        """Return the active transport identity selected by one page."""
        port = self.selected_port_for_page(page_name)
        identity = self.connection_identity(port)
        if identity is not None:
            return identity
        return None

    def connected_port_options(self) -> list[tuple[str, str]]:
        return [(self._port_label(port), port) for port in self.connected_ports()]

    def _port_label(self, port: str) -> str:
        manager = self._managers.get(port)
        identity = ""
        if manager is not None:
            identity = manager.board_identity.strip()
        return f"{port} - {identity}" if identity else port

    def selected_port_for_page(self, page_name: str | None = None) -> str | None:
        page = page_name or getattr(self.state, "current_page", None)
        if page:
            selected = self.state.extra.get(f"serial_port_{page}")
            if selected:
                return str(selected)
        return getattr(self.state, "selected_port", None)

    def set_page_port(self, page_name: str, port: str | None) -> None:
        key = f"serial_port_{page_name}"
        if port:
            self.state.extra[key] = port
        else:
            self.state.extra.pop(key, None)

    def connect(self, port: str, baudrate: int = 115200) -> SerialResponse:
        port = str(port).strip()
        if not port:
            return SerialResponse(False, error="Serial port is not connected")

        manager = self._managers.get(port)
        was_known = manager is not None
        if manager is None:
            manager = self._make_manager()
            self._attach_existing_log_listeners(manager)
            # Install the event fence before connect starts the monitor.  A
            # push can legally follow the first ready response immediately.
            self._managers[port] = manager
        self._attach_connection_listener(manager)
        self._attach_event_listener(manager, port)

        if not manager.is_connected:
            resp = manager.connect(port, baudrate)
            if not resp.ok:
                if not was_known and self._managers.get(port) is manager:
                    self._managers.pop(port, None)
                if self._managers.get(port) is not manager:
                    self._detach_connection_listener(manager)
                    self._detach_event_listener(manager)
                self.last_error = resp.error
                return resp
            manager_key = id(manager)
            self._manager_connection_generations[manager_key] = (
                self._manager_connection_generations.get(manager_key, 0) + 1
            )
        else:
            resp = SerialResponse(True, data=manager.board_identity or f"Connected to {port}")

        self._managers[port] = manager
        self._connection_errors.pop(port, None)
        self.state.selected_port = port
        self.state.baudrate = int(baudrate)
        self.state.serial_connected = bool(self.connected_ports())
        return resp

    def disconnect(self, port: str | None = None) -> None:
        selected = port or self.selected_port_for_page()
        if selected is None:
            return
        manager = self._managers.get(selected)
        if manager is not None:
            manager.disconnect()
        if getattr(self.state, "selected_port", None) == selected:
            remaining = self.connected_ports()
            self.state.selected_port = remaining[0] if remaining else None
        self.state.serial_connected = bool(self.connected_ports())

        stale_keys = [
            key for key, value in self.state.extra.items()
            if key.startswith("serial_port_") and value == selected
        ]
        for key in stale_keys:
            self.state.extra.pop(key, None)

    def disconnect_all(self) -> None:
        for manager in list(self._managers.values()):
            manager.disconnect()
        self.state.selected_port = None
        self.state.serial_connected = False

    def add_event_listener(
        self, listener: Callable[[str, chip_status_cache.Snapshot], None]
    ) -> None:
        """Register a main-thread consumer for drained push events."""

        if listener not in self._event_listeners:
            self._event_listeners.append(listener)

    def remove_event_listener(
        self, listener: Callable[[str, chip_status_cache.Snapshot], None]
    ) -> None:
        try:
            self._event_listeners.remove(listener)
        except ValueError:
            pass

    def drain_events(self) -> list[tuple[str, Any, chip_status_cache.Snapshot]]:
        """Return all queued EVSTAT events in arrival order.

        This method performs no serial I/O.  Home calls it from Tk's event
        loop, so any registered listener is invoked on that thread rather than
        from a serial reader callback.
        """

        events: list[tuple[str, Any, chip_status_cache.Snapshot]] = []
        while True:
            try:
                events.append(self._event_queue.get_nowait())
            except Empty:
                break
        for port, _manager, snapshot in events:
            for listener in list(self._event_listeners):
                try:
                    listener(port, snapshot)
                except Exception:
                    self.remove_event_listener(listener)
        return events

    def _current_manager(self) -> SerialManager | None:
        port = self.selected_port_for_page()
        if port is None:
            return None
        manager = self._managers.get(port)
        if manager is None or not manager.is_connected:
            return None
        return manager

    def _delegate(self) -> SerialManager:
        manager = self._current_manager()
        return manager if manager is not None else self._scanner

    def _not_connected_response(self, *_args: Any, **_kwargs: Any) -> SerialResponse:
        return SerialResponse(False, error="Serial is not connected")

    def _attach_existing_log_listeners(self, manager: SerialManager) -> None:
        for listener in self._log_listeners:
            manager.add_log_listener(listener)
        for listener in self._log_clear_listeners:
            manager.add_log_clear_listener(listener)

    def add_log_listener(self, listener: Callable[[str], None]) -> None:
        if listener not in self._log_listeners:
            self._log_listeners.append(listener)
        for manager in self._managers.values():
            manager.add_log_listener(listener)

    def remove_log_listener(self, listener: Callable[[str], None]) -> None:
        try:
            self._log_listeners.remove(listener)
        except ValueError:
            pass
        for manager in self._managers.values():
            manager.remove_log_listener(listener)

    def add_log_clear_listener(self, listener: Callable[[], None]) -> None:
        if listener not in self._log_clear_listeners:
            self._log_clear_listeners.append(listener)
        for manager in self._managers.values():
            manager.add_log_clear_listener(listener)

    def remove_log_clear_listener(self, listener: Callable[[], None]) -> None:
        try:
            self._log_clear_listeners.remove(listener)
        except ValueError:
            pass
        for manager in self._managers.values():
            manager.remove_log_clear_listener(listener)

    def clear_log(self) -> None:
        self._router_log_history.clear()
        for manager in self._managers.values():
            manager.clear_log()
        for listener in list(self._log_clear_listeners):
            listener()

    def add_connection_listener(self, listener: Callable[[str, str], None]) -> None:
        if listener not in self._connection_listeners:
            self._connection_listeners.append(listener)

    def remove_connection_listener(self, listener: Callable[[str, str], None]) -> None:
        try:
            self._connection_listeners.remove(listener)
        except ValueError:
            pass

    def _attach_connection_listener(self, manager: Any) -> None:
        add_listener = getattr(manager, "add_connection_listener", None)
        if not callable(add_listener):
            return
        key = id(manager)
        if key in self._manager_connection_listeners:
            return

        def on_connection_lost(port: str, error: str, _manager: Any = manager) -> None:
            self._on_manager_connection_lost(_manager, port, error)

        self._manager_connection_listeners[key] = (manager, on_connection_lost)
        add_listener(on_connection_lost)

    def _attach_event_listener(self, manager: Any, port: str) -> None:
        add_listener = getattr(manager, "add_event_listener", None)
        if not callable(add_listener):
            return
        key = id(manager)
        if key in self._manager_event_listeners:
            return

        def on_event(
            snapshot: chip_status_cache.Snapshot,
            _manager: Any = manager,
            _port: str = port,
        ) -> None:
            # The mapping is the port/handle generation fence.  A callback
            # from a superseded manager must never enter the new session FIFO.
            if self._managers.get(_port) is not _manager:
                return
            self._event_queue.put((_port, _manager, snapshot))

        self._manager_event_listeners[key] = (manager, on_event)
        add_listener(on_event)

    def _detach_connection_listener(self, manager: Any) -> None:
        entry = self._manager_connection_listeners.pop(id(manager), None)
        if entry is None:
            return
        remove_listener = getattr(manager, "remove_connection_listener", None)
        if callable(remove_listener):
            remove_listener(entry[1])

    def _detach_event_listener(self, manager: Any) -> None:
        entry = self._manager_event_listeners.pop(id(manager), None)
        if entry is None:
            return
        remove_listener = getattr(manager, "remove_event_listener", None)
        if callable(remove_listener):
            remove_listener(entry[1])

    def _drop_events_for_manager(self, manager: Any, port: str) -> None:
        retained: list[tuple[str, Any, chip_status_cache.Snapshot]] = []
        while True:
            try:
                event = self._event_queue.get_nowait()
            except Empty:
                break
            if event[0] != port or event[1] is not manager:
                retained.append(event)
        for event in retained:
            self._event_queue.put(event)

    def _on_manager_connection_lost(self, manager: Any, port: str, error: str) -> None:
        """Remove only the manager that lost its transport handle."""

        lost_port = str(port or "").strip()
        if not lost_port or self._managers.get(lost_port) is not manager:
            # A late callback from a superseded manager must not touch a new
            # connection on the same COM port.
            return

        self._managers.pop(lost_port, None)
        self._detach_connection_listener(manager)
        self._detach_event_listener(manager)
        self._drop_events_for_manager(manager, lost_port)
        first_error = self._connection_errors.setdefault(lost_port, str(error).strip() or "transport error")
        if not self.last_error:
            self.last_error = first_error

        if getattr(self.state, "selected_port", None) == lost_port:
            self.state.selected_port = None
        self.state.serial_connected = bool(self.connected_ports())

        stale_keys = [
            key for key, value in getattr(self.state, "extra", {}).items()
            if key.startswith("serial_port_") and value == lost_port
        ]
        for key in stale_keys:
            self.state.extra.pop(key, None)

        for listener in list(self._connection_listeners):
            try:
                listener(lost_port, first_error)
            except Exception:
                self.remove_connection_listener(listener)

    @contextmanager
    def suppress_logging(self):
        manager = self._current_manager()
        if manager is None:
            yield
            return
        with manager.suppress_logging():
            yield

    def emit_log(self, message: str) -> None:
        manager = self._current_manager()
        if manager is not None:
            manager.emit_log(message)
            return
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self._router_log_history.append(line)
        for listener in list(self._log_listeners):
            listener(line)

    def __getattr__(self, name: str) -> Any:
        if self._current_manager() is None and name in {
            "send_command",
            "read_board_flag",
            "read_board_flag_for_poll",
            "acknowledge_interrupt",
            "read_status_capabilities",
            "set_status_session",
            "configure_status_target",
            "clear_status_target",
            "set_status_monitor",
            "read_status_snapshot",
            "read_gpio",
            "configure_gpio",
            "read_adc",
            "read_frequency",
            "set_i2c_speed",
            "read_i2c_speed",
            "write_register_values",
            "read_register_values",
            "write_register_value",
            "read_register_value",
            "cfg_query",
            "cfg_read",
            "cfg_begin",
            "cfg_data",
            "cfg_end",
            "cfg_use",
            "cfg_apply",
            "cfg_delete",
            "sync_config_preset",
        }:
            return self._not_connected_response
        return getattr(self._delegate(), name)
