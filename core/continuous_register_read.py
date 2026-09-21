from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Any, Callable

from core.base_tool import is_serial_contention_error


@dataclass(frozen=True)
class ContinuousReadSettings:
    device_addr: int
    reg_addr: int
    bits: int
    expected_value: int | None
    repeat_count: int | None
    interval_ms: int
    mismatch_policy: str = "continue"
    bit_match_enabled: bool = False
    bit_index: int | None = None
    expected_bit: int | None = None
    failure_policy: str = "continue"


@dataclass(frozen=True)
class ContinuousReadResult:
    sequence: int
    value: int | None
    matches: bool | None
    error: str = ""
    bit_value: int | None = None
    bit_matches: bool | None = None
    mismatch_alert: bool = False
    bit_match_alert: bool = False


@dataclass(frozen=True)
class ContinuousReadOutcome:
    attempts: int
    terminal_error: str = ""
    stop_reason: str = ""


def parse_continuous_read_settings(
    device_text: str,
    reg_text: str,
    bits: int | str,
    expected_text: str | None,
    repeat_text: str | None,
    interval_text: str,
    mismatch_policy: str = "continue",
    bit_match_enabled: bool = False,
    bit_index: int | str | None = None,
    expected_bit: int | str | None = None,
    failure_policy: str = "continue",
) -> ContinuousReadSettings:
    """Validate and normalize the values entered in the Home read dialog."""

    try:
        device_addr = int(str(device_text).strip(), 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Device address must be hexadecimal or decimal") from exc
    if not 0 <= device_addr <= 0x7F:
        raise ValueError("Device address must be between 0x00 and 0x7F")

    try:
        reg_addr = int(str(reg_text).strip(), 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Register address must be hexadecimal or decimal") from exc
    if not 0 <= reg_addr <= 0xFF:
        raise ValueError("Register address must be between 0x00 and 0xFF")

    try:
        width = int(str(bits).split("-", 1)[0].strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Data width must be 8 or 16 bits") from exc
    if width not in (8, 16):
        raise ValueError("Data width must be 8 or 16 bits")

    expected_value: int | None = None
    normalized_expected = str(expected_text or "").strip()
    if normalized_expected:
        try:
            expected_value = int(normalized_expected, 0)
        except ValueError as exc:
            raise ValueError("Expected data must be hexadecimal or decimal") from exc
        if not 0 <= expected_value <= (1 << width) - 1:
            raise ValueError(f"Expected data does not fit in {width} bits")

    normalized_repeat = str(repeat_text or "").strip()
    repeat_count: int | None = None
    if normalized_repeat:
        try:
            repeat_count = int(normalized_repeat, 10)
        except ValueError as exc:
            raise ValueError("Read count must be a positive integer") from exc
        if repeat_count <= 0:
            raise ValueError("Read count must be a positive integer")

    try:
        interval_ms = int(str(interval_text).strip(), 10)
    except (TypeError, ValueError) as exc:
        raise ValueError("Interval must be a positive integer in milliseconds") from exc
    if interval_ms <= 0:
        raise ValueError("Interval must be a positive integer in milliseconds")

    normalized_policy = str(mismatch_policy or "").strip()
    if normalized_policy not in {"continue", "stop", "error_continue"}:
        raise ValueError("Mismatch policy must be continue, stop, or error_continue")

    normalized_failure_policy = str(failure_policy or "").strip()
    if normalized_failure_policy not in {"continue", "stop"}:
        raise ValueError("Failure policy must be continue or stop")

    normalized_bit_enabled = bool(bit_match_enabled)
    normalized_bit_index: int | None = None
    normalized_expected_bit: int | None = None
    if normalized_bit_enabled:
        try:
            normalized_bit_index = int(str(bit_index).strip(), 10)
        except (TypeError, ValueError) as exc:
            raise ValueError("Bit index must be an integer within the selected width") from exc
        if not 0 <= normalized_bit_index < width:
            raise ValueError("Bit index must be an integer within the selected width")
        try:
            normalized_expected_bit = int(str(expected_bit).strip(), 10)
        except (TypeError, ValueError) as exc:
            raise ValueError("Expected bit must be 0 or 1") from exc
        if normalized_expected_bit not in (0, 1):
            raise ValueError("Expected bit must be 0 or 1")

    return ContinuousReadSettings(
        device_addr=device_addr,
        reg_addr=reg_addr,
        bits=width,
        expected_value=expected_value,
        repeat_count=repeat_count,
        interval_ms=interval_ms,
        mismatch_policy=normalized_policy,
        bit_match_enabled=normalized_bit_enabled,
        bit_index=normalized_bit_index,
        expected_bit=normalized_expected_bit,
        failure_policy=normalized_failure_policy,
    )


def run_continuous_register_reads(
    settings: ContinuousReadSettings,
    transport: Any,
    cancel_event: Event,
    emit_result: Callable[[ContinuousReadResult], bool | None],
    *,
    is_connection_current: Callable[[], bool],
    wait: Callable[[float], bool] | None = None,
    busy_retry_limit: int = 3,
    busy_retry_interval_s: float = 0.05,
) -> ContinuousReadOutcome:
    """Read one register sequentially, with cancellation and a session fence.

    The first read starts immediately. Subsequent reads wait ``interval_ms``
    after the preceding attempt. Internal command-lock contention retries do
    not count as attempts. If contention remains after retries are exhausted,
    it produces one final failed cycle and an error result row; that cycle
    counts toward ``repeat_count`` and ``failure_policy`` determines whether
    the runner waits for the next interval and continues or stops with a
    terminal error.
    """

    wait_for_cancel = wait or cancel_event.wait
    attempts = 0
    retries_limit = max(0, int(busy_retry_limit))
    mismatch_alert_sent = False
    bit_match_alert_sent = False

    while settings.repeat_count is None or attempts < settings.repeat_count:
        if cancel_event.is_set():
            return ContinuousReadOutcome(attempts, stop_reason="cancelled")
        if not _connection_is_current(is_connection_current):
            return ContinuousReadOutcome(attempts, stop_reason="connection_changed")

        if attempts:
            if wait_for_cancel(settings.interval_ms / 1000.0) or cancel_event.is_set():
                return ContinuousReadOutcome(attempts, stop_reason="cancelled")
            if not _connection_is_current(is_connection_current):
                return ContinuousReadOutcome(attempts, stop_reason="connection_changed")

        retries = 0
        while True:
            if cancel_event.is_set():
                return ContinuousReadOutcome(attempts, stop_reason="cancelled")
            if not _connection_is_current(is_connection_current):
                return ContinuousReadOutcome(attempts, stop_reason="connection_changed")

            try:
                response = transport.read_register_value(
                    settings.device_addr,
                    settings.reg_addr,
                    bits=settings.bits,
                )
            except Exception as exc:
                if not _connection_is_current(is_connection_current):
                    return ContinuousReadOutcome(attempts, stop_reason="connection_changed")
                error = str(exc).strip() or exc.__class__.__name__
                if is_serial_contention_error(error):
                    if retries >= retries_limit:
                        attempts += 1
                        outcome = _emit_failure(attempts, error, emit_result)
                        if outcome.stop_reason == "result_sink_closed":
                            return outcome
                        if settings.failure_policy == "stop":
                            return ContinuousReadOutcome(
                                attempts,
                                terminal_error=error,
                                stop_reason="serial_busy",
                            )
                        break
                    retries += 1
                    if wait_for_cancel(busy_retry_interval_s) or cancel_event.is_set():
                        return ContinuousReadOutcome(attempts, stop_reason="cancelled")
                    continue
                attempts += 1
                outcome = _emit_failure(attempts, error, emit_result)
                if outcome.stop_reason == "result_sink_closed" or settings.failure_policy == "stop":
                    return outcome
                break

            if not _connection_is_current(is_connection_current):
                # The reply belongs to a superseded connection session. Do not
                # expose it to the current window or allow another request.
                return ContinuousReadOutcome(attempts, stop_reason="connection_changed")

            if not bool(getattr(response, "ok", False)):
                error = (
                    str(getattr(response, "error", "") or "").strip()
                    or str(getattr(response, "data", "") or "").strip()
                    or "Register read failed"
                )
                if is_serial_contention_error(error):
                    if retries >= retries_limit:
                        attempts += 1
                        outcome = _emit_failure(attempts, error, emit_result)
                        if outcome.stop_reason == "result_sink_closed":
                            return outcome
                        if settings.failure_policy == "stop":
                            return ContinuousReadOutcome(
                                attempts,
                                terminal_error=error,
                                stop_reason="serial_busy",
                            )
                        break
                    retries += 1
                    if wait_for_cancel(busy_retry_interval_s) or cancel_event.is_set():
                        return ContinuousReadOutcome(attempts, stop_reason="cancelled")
                    continue

                attempts += 1
                outcome = _emit_failure(attempts, error, emit_result)
                if outcome.stop_reason == "result_sink_closed" or settings.failure_policy == "stop":
                    return outcome
                break

            attempts += 1
            try:
                value = transport.extract_rdataw_value(
                    response.data,
                    device_addr=settings.device_addr,
                    reg_addr=settings.reg_addr,
                    bits=settings.bits,
                )
            except Exception as exc:
                value = None
                error = str(exc).strip() or "Invalid register read response"
            else:
                error = "" if value is not None else "Invalid or mismatched RDATAW response"

            if value is None:
                outcome = _emit_failure(attempts, error, emit_result)
                if outcome.stop_reason == "result_sink_closed" or settings.failure_policy == "stop":
                    return outcome
                break

            matches = (value == settings.expected_value) if settings.expected_value is not None else None
            bit_value = (
                (value >> settings.bit_index) & 1
                if settings.bit_match_enabled and settings.bit_index is not None
                else None
            )
            bit_matches = (
                bit_value == settings.expected_bit
                if bit_value is not None and settings.expected_bit is not None
                else None
            )
            mismatch_alert = (
                settings.mismatch_policy == "error_continue"
                and matches is False
                and not mismatch_alert_sent
            )
            bit_match_alert = bit_matches is True and not bit_match_alert_sent
            if mismatch_alert:
                mismatch_alert_sent = True
            if bit_match_alert:
                bit_match_alert_sent = True

            result = ContinuousReadResult(
                sequence=attempts,
                value=value,
                matches=matches,
                bit_value=bit_value,
                bit_matches=bit_matches,
                mismatch_alert=mismatch_alert,
                bit_match_alert=bit_match_alert,
            )
            if not _emit(emit_result, result):
                return ContinuousReadOutcome(attempts, stop_reason="result_sink_closed")
            if matches is False and settings.mismatch_policy == "stop":
                return ContinuousReadOutcome(attempts, stop_reason="expected_mismatch")
            break

        if settings.repeat_count is not None and attempts >= settings.repeat_count:
            return ContinuousReadOutcome(attempts, stop_reason="completed")

    return ContinuousReadOutcome(attempts, stop_reason="completed")


def _emit_failure(
    sequence: int,
    error: str,
    emit_result: Callable[[ContinuousReadResult], bool | None],
) -> ContinuousReadOutcome:
    result = ContinuousReadResult(sequence=sequence, value=None, matches=None, error=error)
    if not _emit(emit_result, result):
        return ContinuousReadOutcome(sequence, stop_reason="result_sink_closed")
    return ContinuousReadOutcome(sequence, terminal_error=error, stop_reason="read_error")


def _emit(
    emit_result: Callable[[ContinuousReadResult], bool | None],
    result: ContinuousReadResult,
) -> bool:
    try:
        return emit_result(result) is not False
    except Exception:
        return False


def _connection_is_current(check: Callable[[], bool]) -> bool:
    try:
        return bool(check())
    except Exception:
        return False
