from __future__ import annotations

import json
import socketserver
import threading
import time
from contextlib import nullcontext
from typing import Any, Callable


BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 47631
BRIDGE_PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_TRANSACTION_STEPS = 256
REGISTER_WIDTHS = (8, 16)


class BridgeRequestError(ValueError):
    pass


class I2cBridgeService:
    """Execute validated register transactions through the GUI-owned serial port."""

    def __init__(
        self,
        manager_provider: Callable[[], Any | None],
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._manager_provider = manager_provider
        self._sleep = sleep
        self._monotonic = monotonic

    def handle_request(self, request: object) -> dict[str, Any]:
        try:
            if not isinstance(request, dict):
                raise BridgeRequestError("Request must be a JSON object")
            version = request.get("version", BRIDGE_PROTOCOL_VERSION)
            if version != BRIDGE_PROTOCOL_VERSION:
                raise BridgeRequestError(
                    f"Unsupported bridge protocol version: {version!r}"
                )

            operation = str(request.get("op", "")).strip().lower()
            if operation in {"ping", "status"}:
                return self._status_response()
            if operation in {"read", "write"}:
                return self._execute_transaction([request])
            if operation == "transaction":
                return self._execute_transaction(request.get("steps"))
            raise BridgeRequestError(f"Unsupported bridge operation: {operation!r}")
        except BridgeRequestError as exc:
            return self._error_response(str(exc))
        except Exception as exc:
            return self._error_response(f"Bridge request failed: {exc}")

    def _status_response(self) -> dict[str, Any]:
        manager = self._manager_provider()
        connected = bool(manager is not None and getattr(manager, "is_connected", False))
        return {
            "ok": True,
            "version": BRIDGE_PROTOCOL_VERSION,
            "bridge": "AnalogwinPiezoGUI",
            "connected": connected,
            "serial_port": getattr(manager, "port", None) if connected else None,
        }

    def _execute_transaction(self, steps_value: object) -> dict[str, Any]:
        if not isinstance(steps_value, list) or not steps_value:
            raise BridgeRequestError("Transaction steps must be a non-empty JSON array")
        if len(steps_value) > MAX_TRANSACTION_STEPS:
            raise BridgeRequestError(
                f"Transaction exceeds {MAX_TRANSACTION_STEPS} steps"
            )

        manager = self._manager_provider()
        if manager is None or not getattr(manager, "is_connected", False):
            raise BridgeRequestError(
                "Serial is not connected in AnalogwinPiezoGUI; connect a COM port first"
            )

        transaction = getattr(manager, "snapshot_transaction", None)
        transaction_context = transaction() if callable(transaction) else nullcontext()
        results: list[dict[str, Any]] = []

        with transaction_context:
            for index, step in enumerate(steps_value):
                try:
                    if not isinstance(step, dict):
                        raise BridgeRequestError("Each transaction step must be a JSON object")
                    results.append(self._execute_step(manager, step))
                except BridgeRequestError as exc:
                    return self._error_response(
                        str(exc),
                        failed_step=index,
                        results=results,
                        serial_port=getattr(manager, "port", None),
                    )
                except Exception as exc:
                    return self._error_response(
                        f"Step failed: {exc}",
                        failed_step=index,
                        results=results,
                        serial_port=getattr(manager, "port", None),
                    )

        return {
            "ok": True,
            "version": BRIDGE_PROTOCOL_VERSION,
            "serial_port": getattr(manager, "port", None),
            "results": results,
        }

    def _execute_step(self, manager: Any, step: dict[str, Any]) -> dict[str, Any]:
        operation = str(step.get("op", "")).strip().lower()
        if operation == "write":
            return self._write_register(manager, step)
        if operation == "read":
            return self._read_register(manager, step)
        if operation == "delay":
            milliseconds = self._number_field(
                step,
                "milliseconds",
                minimum=0,
                maximum=60_000,
            )
            self._sleep(milliseconds / 1000.0)
            return {"op": "delay", "milliseconds": milliseconds}
        if operation == "poll":
            return self._poll_register(manager, step)
        raise BridgeRequestError(f"Unsupported transaction step: {operation!r}")

    def _write_register(self, manager: Any, step: dict[str, Any]) -> dict[str, Any]:
        device_addr, register_addr, bits = self._register_fields(step)
        value = self._int_field(
            step,
            "value",
            minimum=0,
            maximum=(1 << bits) - 1,
        )
        response = manager.write_register_value(
            device_addr,
            register_addr,
            value,
            bits=bits,
        )
        if not response.ok:
            raise BridgeRequestError(response.error or "Register write failed")
        return {
            "op": "write",
            "device": device_addr,
            "register": register_addr,
            "bits": bits,
            "value": value,
            "data": response.data,
        }

    def _read_register(self, manager: Any, step: dict[str, Any]) -> dict[str, Any]:
        device_addr, register_addr, bits = self._register_fields(step)
        response = manager.read_register_value(
            device_addr,
            register_addr,
            bits=bits,
        )
        if not response.ok:
            raise BridgeRequestError(response.error or "Register read failed")
        value = manager.extract_rdataw_value(
            response.data,
            device_addr=device_addr,
            reg_addr=register_addr,
            bits=bits,
        )
        if value is None:
            raise BridgeRequestError(
                f"Unable to parse {bits}-bit register response: {response.data!r}"
            )
        return {
            "op": "read",
            "device": device_addr,
            "register": register_addr,
            "bits": bits,
            "value": value,
            "data": response.data,
        }

    def _poll_register(self, manager: Any, step: dict[str, Any]) -> dict[str, Any]:
        device_addr, register_addr, bits = self._register_fields(step)
        expected = self._int_field(
            step,
            "expected",
            minimum=0,
            maximum=(1 << bits) - 1,
        )
        interval_ms = self._number_field(
            step,
            "interval_ms",
            minimum=1,
            maximum=1_000,
            default=5,
        )
        timeout_ms = self._number_field(
            step,
            "timeout_ms",
            minimum=1,
            maximum=60_000,
            default=1_000,
        )
        deadline = self._monotonic() + (timeout_ms / 1000.0)
        attempts = 0

        while True:
            attempts += 1
            result = self._read_register(manager, step)
            if result["value"] == expected:
                result.update({"op": "poll", "expected": expected, "attempts": attempts})
                return result
            if self._monotonic() >= deadline:
                raise BridgeRequestError(
                    f"Poll timeout at register 0x{register_addr:02X}: "
                    f"expected 0x{expected:0{bits // 4}X}, "
                    f"last value 0x{result['value']:0{bits // 4}X}"
                )
            self._sleep(interval_ms / 1000.0)

    def _register_fields(self, step: dict[str, Any]) -> tuple[int, int, int]:
        device_addr = self._int_field(step, "device", minimum=0, maximum=0x7F)
        register_addr = self._int_field(step, "register", minimum=0, maximum=0xFF)
        bits = self._int_field(step, "bits", minimum=8, maximum=16)
        if bits not in REGISTER_WIDTHS:
            raise BridgeRequestError("bits must be one of 8 or 16")
        return device_addr, register_addr, bits

    @staticmethod
    def _int_field(
        values: dict[str, Any],
        name: str,
        *,
        minimum: int,
        maximum: int,
    ) -> int:
        value = values.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise BridgeRequestError(f"{name} must be an integer")
        if value < minimum or value > maximum:
            raise BridgeRequestError(
                f"{name} must be between {minimum} and {maximum}"
            )
        return value

    @staticmethod
    def _number_field(
        values: dict[str, Any],
        name: str,
        *,
        minimum: float,
        maximum: float,
        default: float | None = None,
    ) -> float:
        value = values.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BridgeRequestError(f"{name} must be a number")
        result = float(value)
        if result < minimum or result > maximum:
            raise BridgeRequestError(
                f"{name} must be between {minimum} and {maximum}"
            )
        return result

    @staticmethod
    def _error_response(message: str, **extra: Any) -> dict[str, Any]:
        response: dict[str, Any] = {
            "ok": False,
            "version": BRIDGE_PROTOCOL_VERSION,
            "error": message,
        }
        response.update(extra)
        return response


class _BridgeTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: I2cBridgeService,
    ) -> None:
        self.service = service
        super().__init__(server_address, _BridgeRequestHandler)


class _BridgeRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_REQUEST_BYTES:
            response = I2cBridgeService._error_response("Bridge request is too large")
        else:
            try:
                request = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                response = I2cBridgeService._error_response(f"Invalid JSON request: {exc}")
            else:
                response = self.server.service.handle_request(request)  # type: ignore[attr-defined]
        payload = json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.wfile.write(payload + b"\n")


class LocalI2cBridge:
    """Host the GUI register bridge on a loopback-only TCP socket."""

    def __init__(
        self,
        manager_provider: Callable[[], Any | None],
        *,
        host: str = BRIDGE_HOST,
        port: int = BRIDGE_PORT,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.service = I2cBridgeService(manager_provider)
        self.last_error = ""
        self._server: _BridgeTcpServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.is_running:
            return True
        self.last_error = ""
        try:
            server = _BridgeTcpServer((self.host, self.port), self.service)
        except OSError as exc:
            self.last_error = str(exc)
            return False
        self._server = server
        self.port = int(server.server_address[1])
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="AnalogwinI2cBridge",
            daemon=True,
        )
        self._thread.start()
        return True

    def close(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)


__all__ = [
    "BRIDGE_HOST",
    "BRIDGE_PORT",
    "BRIDGE_PROTOCOL_VERSION",
    "I2cBridgeService",
    "LocalI2cBridge",
]
