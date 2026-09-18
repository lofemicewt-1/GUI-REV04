from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Generic, Protocol, TypeVar


FRAME_START = "^"
FRAME_END = "~"
PROTOCOL_REVISION = "REV04"
REGISTER_DATA_WIDTHS = (8, 16, 32)
RREGW_MAX_VALUES = {8: 32, 16: 16, 32: 8}
WREGW_MAX_VALUES = {8: 10, 16: 5, 32: 2}
ALLOWED_I2C_SPEEDS = (100000, 400000)
GPIO_MODE_HIGH = "HIGH"
GPIO_MODE_LOW = "LOW"
GPIO_MODE_DETECT = "DETECT"
GPIO_MODE_HIZ = "HIZ"
GPIO_MODES = (GPIO_MODE_HIGH, GPIO_MODE_LOW, GPIO_MODE_DETECT, GPIO_MODE_HIZ)
GPIO_MODE_PINS = ("D2", "D3", "D4", "D5")

DAC_MIN = 0x000
DAC_MAX = 0xFFF
DAC_POS_ZERO = 0x800
DAC_NEG_ZERO = 0x7FF
DEFAULT_VOLTAGE_RANGE = 95.0

T = TypeVar("T")


@dataclass
class SerialResponse:
    ok: bool
    data: str = ""
    error: str = ""


@dataclass(frozen=True)
class RegisterTransaction:
    response: SerialResponse


@dataclass(frozen=True)
class RdatawFrame:
    device_addr: int
    reg_addr: int
    bits: int
    values: tuple[int, ...]


@dataclass(frozen=True)
class BoardFlagStatus:
    flag: bool
    intf: bool
    sequence: int


@dataclass(frozen=True)
class GpioModeAck:
    pin: str
    mode: str


@dataclass(frozen=True)
class GpioPulseAck:
    pin: str
    count: int | None = None
    high_ms: int | None = None
    gap_ms: int | None = None
    stopped: bool = False


@dataclass(frozen=True)
class RegisterScriptOperation:
    device_addr: int
    action: str
    reg_addr: int
    value: int | None
    comment: str = ""
    source_line: int | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class AsyncWriteResult(Generic[T]):
    ok: bool
    value: T | None = None
    error: str = ""


class CommandTransport(Protocol):
    def __call__(
        self,
        body: str,
        *,
        expect_response: bool = True,
        display_body: str | None = None,
    ) -> SerialResponse:
        """Send one firmware command body and return the parsed serial response."""


class RegisterScriptTransport(Protocol):
    def __call__(self, operation: RegisterScriptOperation) -> SerialResponse:
        """Execute one parsed register script row."""


class AsyncWriteHandle(Generic[T]):
    def __init__(
        self,
        thread: threading.Thread,
        result_holder: list[AsyncWriteResult[T]],
    ) -> None:
        self._thread = thread
        self._result_holder = result_holder

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float | None = None) -> AsyncWriteResult[T] | None:
        self._thread.join(timeout)
        if self._thread.is_alive() or not self._result_holder:
            return None
        return self._result_holder[0]


def to_uint(value: int, bits: int, *, name: str = "value") -> int:
    """Mask an integer to an unsigned field, used by existing script codecs."""
    if bits <= 0:
        raise ValueError("bits must be greater than 0")
    try:
        integer = int(value)
    except Exception as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return integer & ((1 << bits) - 1)


def u8(value: int, *, name: str = "value") -> int:
    return to_uint(value, 8, name=name)


def u16(value: int, *, name: str = "value") -> int:
    return to_uint(value, 16, name=name)


def _register_data_width(bits: int) -> int:
    width = int(bits)
    if width not in REGISTER_DATA_WIDTHS:
        raise ValueError("register data width must be 8, 16, or 32 bits")
    return width


def _device_address(value: int) -> int:
    address = int(value)
    if not 0 <= address <= 0x7F:
        raise ValueError("device_addr must be a 7-bit address")
    return address


def _register_value(value: int, bits: int) -> int:
    width = _register_data_width(bits)
    integer = int(value)
    maximum = (1 << width) - 1
    if not 0 <= integer <= maximum:
        raise ValueError(f"value must fit uint{width}")
    return integer


def register_values_limit(bits: int, *, operation: str) -> int:
    """Return the approved single-frame RREGW/WREGW element limit."""
    width = _register_data_width(bits)
    if operation == "read":
        return RREGW_MAX_VALUES[width]
    if operation == "write":
        return WREGW_MAX_VALUES[width]
    raise ValueError("operation must be read or write")


def _register_count(count: int, *, bits: int, operation: str) -> int:
    value = int(count)
    maximum = register_values_limit(bits, operation=operation)
    if not 1 <= value <= maximum:
        raise ValueError(
            f"{operation} count must be between 1 and {maximum} for {bits}-bit data"
        )
    return value


def build_frame(body: str) -> str:
    return f"{FRAME_START}{str(body).strip()}{FRAME_END}"


def build_read_register_values_command(
    device_addr: int,
    reg_addr: int,
    *,
    bits: int,
    count: int = 1,
) -> str:
    """Build the sole explicit-address bulk read: RREGW AA RR W NN."""
    width = _register_data_width(bits)
    length = _register_count(count, bits=width, operation="read")
    return (
        f"RREGW {_device_address(device_addr):02X} "
        f"{u8(reg_addr, name='reg_addr'):02X} {width} {length}"
    )


def build_write_register_values_command(
    device_addr: int,
    reg_addr: int,
    values: list[int] | tuple[int, ...],
    *,
    bits: int,
) -> str:
    """Build the sole explicit-address bulk write: WREGW AA RR W HEXBLOB."""
    width = _register_data_width(bits)
    data = tuple(_register_value(value, width) for value in values)
    _register_count(len(data), bits=width, operation="write")
    digits = width // 4
    hexblob = "".join(f"{value:0{digits}X}" for value in data)
    return (
        f"WREGW {_device_address(device_addr):02X} "
        f"{u8(reg_addr, name='reg_addr'):02X} {width} {hexblob}"
    )


def build_read_register_value_command(device_addr: int, reg_addr: int, *, bits: int) -> str:
    return build_read_register_values_command(device_addr, reg_addr, bits=bits, count=1)


def build_write_register_value_command(
    device_addr: int,
    reg_addr: int,
    value: int,
    *,
    bits: int,
) -> str:
    return build_write_register_values_command(device_addr, reg_addr, (value,), bits=bits)


_RDATAW_PATTERN = re.compile(
    r"RDATAW\s+([0-9A-Fa-f]{2})\s+([0-9A-Fa-f]{2})\s+(8|16|32)\s+([1-9][0-9]*)\s+([0-9A-Fa-f]+)"
)
_OK_WREGW_PATTERN = re.compile(
    r"OK\s+WREGW\s+([0-9A-Fa-f]{2})\s+([0-9A-Fa-f]{2})\s+(8|16|32)\s+([1-9][0-9]*)"
)


def _normalized_frame(text: str) -> str:
    return str(text).strip().strip(FRAME_START + FRAME_END).strip()


def parse_rdataw_response(text: str) -> RdatawFrame:
    """Parse only the approved RDATAW frame; legacy RDATA is rejected."""
    match = _RDATAW_PATTERN.fullmatch(_normalized_frame(text))
    if match is None:
        raise ValueError("invalid RDATAW response")
    addr_text, reg_text, bits_text, count_text, hexblob = match.groups()
    width = _register_data_width(int(bits_text))
    count = _register_count(int(count_text), bits=width, operation="read")
    address = _device_address(int(addr_text, 16))
    register = int(reg_text, 16)
    expected_hex_chars = count * (width // 4)
    if len(hexblob) != expected_hex_chars:
        raise ValueError("RDATAW HEXBLOB length does not match count and width")
    digits = width // 4
    values = tuple(
        int(hexblob[index : index + digits], 16)
        for index in range(0, len(hexblob), digits)
    )
    return RdatawFrame(address, register, width, values)


def extract_rdataw_values(
    text: str,
    *,
    device_addr: int,
    reg_addr: int,
    bits: int,
    count: int,
) -> tuple[int, ...] | None:
    """Return values only when every echoed RDATAW field matches the request."""
    try:
        frame = parse_rdataw_response(text)
        expected = (
            _device_address(device_addr),
            u8(reg_addr, name="reg_addr"),
            _register_data_width(bits),
            _register_count(count, bits=bits, operation="read"),
        )
    except (TypeError, ValueError):
        return None
    if (
        frame.device_addr,
        frame.reg_addr,
        frame.bits,
        len(frame.values),
    ) != expected:
        return None
    return frame.values


def extract_rdataw_value(
    text: str,
    *,
    device_addr: int,
    reg_addr: int,
    bits: int,
) -> int | None:
    values = extract_rdataw_values(
        text,
        device_addr=device_addr,
        reg_addr=reg_addr,
        bits=bits,
        count=1,
    )
    return None if values is None else values[0]


def expect_wregw_response(
    response: SerialResponse,
    *,
    device_addr: int,
    reg_addr: int,
    bits: int,
    count: int,
) -> SerialResponse:
    """Require the exact WREGW acknowledgement, including derived count."""
    if not response.ok:
        return response
    match = _OK_WREGW_PATTERN.fullmatch(_normalized_frame(response.data))
    if match is None:
        return SerialResponse(False, data=response.data, error=f"Unexpected response: {response.data}")
    addr_text, reg_text, bits_text, count_text = match.groups()
    try:
        expected = (
            _device_address(device_addr),
            u8(reg_addr, name="reg_addr"),
            _register_data_width(bits),
            _register_count(count, bits=bits, operation="write"),
        )
    except (TypeError, ValueError):
        return SerialResponse(False, data=response.data, error=f"Unexpected response: {response.data}")
    actual = (int(addr_text, 16), int(reg_text, 16), int(bits_text), int(count_text))
    if actual != expected:
        return SerialResponse(False, data=response.data, error=f"Unexpected response: {response.data}")
    return response


def _pin_token(pin: str | int) -> str:
    token = str(pin).strip().upper()
    if token.isdecimal():
        token = f"D{int(token)}"
    return token


def _public_gpio_pin(pin: str | int) -> str:
    token = _pin_token(pin)
    if token in {"D0", "D1", "D2", "D7", "A0", "A4", "A5", "A6", "A7"}:
        raise ValueError(f"{token} is reserved and unavailable to public GPIO")
    return token


def _flag_value(value: bool | int) -> int:
    return 1 if bool(value) else 0


def build_ping_command() -> str:
    return "PING"


def parse_ping_response(text: str) -> bool:
    """Return READY state only for the current protocol revision."""
    normalized = _normalized_frame(text).upper()
    if normalized == f"PONG {PROTOCOL_REVISION} READY":
        return True
    if normalized == f"PONG {PROTOCOL_REVISION} BOOTING":
        return False
    raise ValueError(f"protocol mismatch: expected PONG {PROTOCOL_REVISION}")


def build_set_board_flag_command(value: bool | int) -> str:
    return f"FLAG {_flag_value(value)}"


def build_read_board_flag_command() -> str:
    return "FLAG?"


def build_interrupt_ack_command(sequence: int) -> str:
    value = int(sequence)
    if not 1 <= value <= 0xFFFFFFFF:
        raise ValueError("sequence must be between 1 and FFFFFFFF")
    return f"INTACK {value:08X}"


_FLAG_STATUS_PATTERN = re.compile(r"FLAG\s+([01])\s+INTF=([01])\s+SEQ=([0-9A-Fa-f]{8})")
_INTACK_OK_PATTERN = re.compile(r"OK\s+INTACK\s+([0-9A-Fa-f]{8})")


def parse_board_flag_response(text: str) -> BoardFlagStatus:
    match = _FLAG_STATUS_PATTERN.fullmatch(_normalized_frame(text))
    if match is None:
        raise ValueError("invalid FLAG response")
    flag_text, intf_text, sequence_text = match.groups()
    sequence = int(sequence_text, 16)
    if bool(int(intf_text)) != bool(sequence):
        raise ValueError("FLAG INTF and SEQ are inconsistent")
    return BoardFlagStatus(bool(int(flag_text)), bool(int(intf_text)), sequence)


def is_idle_board_flag_response(response: SerialResponse) -> bool:
    """Return True only for a successfully parsed zero-sequence FLAG reply."""
    if not response.ok:
        return False
    try:
        return parse_board_flag_response(response.data).sequence == 0
    except (TypeError, ValueError):
        return False


def is_serial_contention_error(error: str | None) -> bool:
    return str(error or "").strip() in {"Serial command is busy", "Serial transaction busy"}


def build_write_gpio_command(pin: str | int, enabled: bool | int) -> str:
    return f"GPIO {_public_gpio_pin(pin)} {_flag_value(enabled)}"


def build_read_gpio_command(pin: str | int) -> str:
    return f"GPIOR {_public_gpio_pin(pin)}"


def build_gpio_pullup_command(pin: str | int) -> str:
    return f"GPIOU {_public_gpio_pin(pin)}"


def _gpio_mode_pin(pin: str | int) -> str:
    token = _pin_token(pin)
    if token not in GPIO_MODE_PINS:
        raise ValueError(f"{token} is not a supported GPIO mode pin")
    return token


def _gpio_mode_token(mode: str) -> str:
    token = str(mode).strip().upper()
    if token not in GPIO_MODES:
        raise ValueError(f"unsupported GPIO mode: {mode}")
    return token


def build_gpio_mode_command(pin: str | int, mode: str) -> str:
    return f"GPIOCFG {_gpio_mode_pin(pin)} {_gpio_mode_token(mode)}"


_GPIO_MODE_PATTERN = re.compile(r"GPIOCFG\s+(D[2-5])\s+(HIGH|LOW|DETECT|HIZ)")


def parse_gpio_mode_response(text: str) -> GpioModeAck:
    match = _GPIO_MODE_PATTERN.fullmatch(_normalized_frame(text).upper())
    if match is None:
        raise ValueError("invalid GPIOCFG response")
    return GpioModeAck(pin=match.group(1), mode=match.group(2))


def _gpio_pulse_bounds(count: int, high_ms: int, gap_ms: int) -> tuple[int, int, int]:
    count_value = int(count)
    high_value = int(high_ms)
    gap_value = int(gap_ms)
    if not 1 <= count_value <= 255:
        raise ValueError("pulse count must be in range 1..255")
    if not 1 <= high_value <= 60000:
        raise ValueError("pulse high time must be in range 1..60000 ms")
    if not 0 <= gap_value <= 60000:
        raise ValueError("pulse gap time must be in range 0..60000 ms")
    return count_value, high_value, gap_value


def build_gpio_pulse_command(
    pin: str | int,
    count: int,
    high_ms: int,
    gap_ms: int,
) -> str:
    count_value, high_value, gap_value = _gpio_pulse_bounds(count, high_ms, gap_ms)
    return f"GPIOCFG {_gpio_mode_pin(pin)} HIGH {count_value} {high_value} {gap_value}"


def build_gpio_pulse_stop_command(pin: str | int) -> str:
    return f"GPIOCFG {_gpio_mode_pin(pin)} HIZ"


def parse_gpio_pulse_response(text: str) -> GpioPulseAck:
    ack = parse_gpio_mode_response(text)
    if ack.mode == GPIO_MODE_HIGH:
        return GpioPulseAck(pin=ack.pin, stopped=False)
    if ack.mode in {GPIO_MODE_HIZ, GPIO_MODE_DETECT, GPIO_MODE_LOW}:
        return GpioPulseAck(pin=ack.pin, stopped=True)
    raise ValueError("invalid GPIO pulse response")


def build_set_i2c_speed_command(speed_hz: int) -> str:
    speed = int(speed_hz)
    if speed not in ALLOWED_I2C_SPEEDS:
        raise ValueError("I2C speed must be 100000 or 400000")
    return f"I2CSPD {speed}"


def build_read_i2c_speed_command() -> str:
    return "I2CSPD?"


def build_cfg_query_command() -> str:
    return "CFG?"


def _cfg_slot(slot: int) -> int:
    value = int(slot)
    if not 0 <= value <= 2:
        raise ValueError("slot must be between 0 and 2")
    return value


def _cfg_length(length: int) -> int:
    value = int(length)
    if not 1 <= value <= 128:
        raise ValueError("length must be between 1 and 128")
    return value


def _cfg_crc(crc: int) -> str:
    value = int(crc)
    if not 0 <= value <= 0xFFFF:
        raise ValueError("crc must fit uint16")
    return f"{value:04X}"


def _cfg_offset(offset: int) -> int:
    value = int(offset)
    if not 0 <= value <= 127:
        raise ValueError("offset must be between 0 and 127")
    return value


def _cfg_chunk_hex(chunk_hex: str) -> str:
    chunk = str(chunk_hex).strip()
    if not chunk or len(chunk) % 2:
        raise ValueError("chunk_hex must have a nonzero even number of hex chars")
    if len(chunk) > 22:
        raise ValueError("chunk_hex must be no longer than 22 hex chars")
    if re.fullmatch(r"[0-9A-Fa-f]+", chunk) is None:
        raise ValueError("chunk_hex must contain only hex chars")
    return chunk.upper()


def build_cfg_read_command(slot: int) -> str:
    return f"CFGREAD {_cfg_slot(slot)}"


def build_cfg_begin_command(slot: int, length: int, crc: int) -> str:
    return f"CFGBEGIN {_cfg_slot(slot)} {_cfg_length(length)} {_cfg_crc(crc)}"


def build_cfg_data_command(offset: int, chunk_hex: str) -> str:
    return f"CFGDATA {_cfg_offset(offset)} {_cfg_chunk_hex(chunk_hex)}"


def build_cfg_end_command(slot: int) -> str:
    return f"CFGEND {_cfg_slot(slot)}"


def build_cfg_use_command(slot: int) -> str:
    return f"CFGUSE {_cfg_slot(slot)}"


def build_cfg_apply_command(slot: int) -> str:
    return f"CFGAPPLY {_cfg_slot(slot)}"


def build_cfg_delete_command(slot: int) -> str:
    return f"CFGDEL {_cfg_slot(slot)}"


REGISTER_SCRIPT_SEPARATOR = ", "
REGISTER_SCRIPT_HEADER = "# Device Address, Operation, Register Address, Register Content, Comment"


def format_register_script_operation(operation: RegisterScriptOperation) -> str:
    action = operation.action.upper()
    value = "--" if operation.value is None else f"0x{u16(operation.value, name='value'):04X}"
    fields = (
        f"0x{u8(operation.device_addr, name='device_addr'):02X}",
        action,
        f"0x{u8(operation.reg_addr, name='reg_addr'):02X}",
        value,
        operation.comment.replace("\n", " "),
    )
    return REGISTER_SCRIPT_SEPARATOR.join(fields)


def format_register_script(operations: list[RegisterScriptOperation]) -> str:
    lines = [REGISTER_SCRIPT_HEADER]
    lines.extend(format_register_script_operation(operation) for operation in operations)
    return "\n".join(lines) + "\n"


def _parse_script_int(text: str, *, line_no: int, field_name: str) -> int:
    try:
        return int(text.strip(), 0)
    except ValueError as exc:
        raise ValueError(f"Line {line_no}: invalid {field_name}: {text}") from exc


def parse_register_script(text: str) -> list[RegisterScriptOperation]:
    operations: list[RegisterScriptOperation] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|" if "|" in line else ",", 4)]
        if len(parts) != 5:
            raise ValueError(f"Line {line_no}: expected 5 fields separated by comma or |")
        device_addr = _parse_script_int(parts[0], line_no=line_no, field_name="device address")
        action = parts[1].upper()
        if action not in {"READ", "WRITE"}:
            raise ValueError(f"Line {line_no}: operation must be READ or WRITE")
        reg_addr = _parse_script_int(parts[2], line_no=line_no, field_name="register address")
        value = None if parts[3] == "--" else _parse_script_int(
            parts[3], line_no=line_no, field_name="register content"
        )
        if action == "WRITE" and value is None:
            raise ValueError(f"Line {line_no}: WRITE requires register content")
        operations.append(
            RegisterScriptOperation(
                _device_address(device_addr),
                action,
                u8(reg_addr, name="reg_addr"),
                None if value is None else u16(value, name="value"),
                parts[4],
                line_no,
            )
        )
    return operations


def execute_register_script_operations(
    operations: list[RegisterScriptOperation],
    transport: RegisterScriptTransport,
) -> str | None:
    for operation_index, operation in enumerate(operations, start=1):
        line_no = operation.source_line or operation_index
        try:
            response = transport(operation)
        except Exception as exc:
            return f"Line {line_no}: {exc}"
        if not response.ok:
            return f"Line {line_no}: {response.error}"
    return None


def clip_voltage(voltage: float, voltage_range: float = DEFAULT_VOLTAGE_RANGE) -> float:
    vrange = abs(float(voltage_range))
    return max(-vrange, min(vrange, float(voltage)))


def map_voltage_to_dac(voltage: float, voltage_range: float = DEFAULT_VOLTAGE_RANGE) -> int:
    vrange = abs(float(voltage_range))
    clipped = clip_voltage(voltage, vrange)
    if clipped >= 0:
        ratio = clipped / vrange if vrange else 0.0
        code = DAC_POS_ZERO + round(ratio * (DAC_MAX - DAC_POS_ZERO))
    else:
        ratio = abs(clipped) / vrange if vrange else 0.0
        code = DAC_NEG_ZERO - round(ratio * DAC_NEG_ZERO)
    return max(DAC_MIN, min(DAC_MAX, int(code)))


def map_dac_to_voltage(code: int, voltage_range: float = DEFAULT_VOLTAGE_RANGE) -> float:
    vrange = abs(float(voltage_range))
    dac_code = max(DAC_MIN, min(DAC_MAX, int(code)))
    if dac_code >= DAC_POS_ZERO:
        return (dac_code - DAC_POS_ZERO) / (DAC_MAX - DAC_POS_ZERO) * vrange
    return -((DAC_NEG_ZERO - dac_code) / DAC_NEG_ZERO * vrange)


def run_async_write(
    task: Callable[[], T],
    *,
    on_done: Callable[[T], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    daemon: bool = True,
) -> AsyncWriteHandle[T]:
    result_holder: list[AsyncWriteResult[T]] = []

    def worker() -> None:
        try:
            value = task()
            result_holder.append(AsyncWriteResult(True, value=value))
            if on_done is not None:
                on_done(value)
        except Exception as exc:
            result_holder.append(AsyncWriteResult(False, error=str(exc)))
            if on_error is not None:
                on_error(exc)

    thread = threading.Thread(target=worker, daemon=daemon)
    thread.start()
    return AsyncWriteHandle(thread, result_holder)


def _unexpected_response(response: SerialResponse, reason: str | None = None) -> SerialResponse:
    detail = f" ({reason})" if reason else ""
    return SerialResponse(
        False,
        data=response.data,
        error=f"Unexpected response: {response.data}{detail}",
    )


def _firmware_error_response(response: SerialResponse) -> SerialResponse:
    """Treat a framed firmware error as a failed public-protocol operation."""
    if response.ok and _normalized_frame(response.data).upper().startswith("ERR "):
        return SerialResponse(False, data=response.data, error=response.data)
    return response


def ping_board(transport: CommandTransport) -> SerialResponse:
    response = transport(build_ping_command(), expect_response=True)
    if not response.ok:
        return response
    try:
        parse_ping_response(response.data)
    except ValueError as exc:
        return SerialResponse(False, data=response.data, error=str(exc))
    return response


def set_board_flag(transport: CommandTransport, value: bool | int) -> SerialResponse:
    return _firmware_error_response(
        transport(build_set_board_flag_command(value), expect_response=True)
    )


def read_board_flag(transport: CommandTransport) -> SerialResponse:
    response = _firmware_error_response(
        transport(build_read_board_flag_command(), expect_response=True)
    )
    if not response.ok:
        return response
    try:
        parse_board_flag_response(response.data)
    except ValueError as exc:
        return _unexpected_response(response, str(exc))
    return response


def acknowledge_interrupt(transport: CommandTransport, sequence: int) -> SerialResponse:
    response = _firmware_error_response(
        transport(build_interrupt_ack_command(sequence), expect_response=True)
    )
    if not response.ok:
        return response
    match = _INTACK_OK_PATTERN.fullmatch(_normalized_frame(response.data))
    if match is None or int(match.group(1), 16) != int(sequence):
        return _unexpected_response(response)
    return response


def write_gpio(transport: CommandTransport, pin: str | int, enabled: bool | int) -> SerialResponse:
    return _firmware_error_response(
        transport(build_write_gpio_command(pin, enabled), expect_response=True)
    )


def read_gpio(transport: CommandTransport, pin: str | int) -> SerialResponse:
    return _firmware_error_response(
        transport(build_read_gpio_command(pin), expect_response=True)
    )


def set_gpio_pullup(transport: CommandTransport, pin: str | int) -> SerialResponse:
    return _firmware_error_response(
        transport(build_gpio_pullup_command(pin), expect_response=True)
    )


def configure_gpio_mode(
    transport: CommandTransport, pin: str | int, mode: str
) -> SerialResponse:
    expected_pin = _gpio_mode_pin(pin)
    expected_mode = _gpio_mode_token(mode)
    response = _firmware_error_response(
        transport(
            build_gpio_mode_command(expected_pin, expected_mode),
            expect_response=True,
        )
    )
    if not response.ok:
        return response
    try:
        ack = parse_gpio_mode_response(response.data)
    except ValueError as exc:
        return _unexpected_response(response, str(exc))
    if ack.pin != expected_pin or ack.mode != expected_mode:
        return _unexpected_response(response, "GPIOCFG pin/mode mismatch")
    return response


def pulse_gpio(
    transport: CommandTransport,
    pin: str | int,
    count: int | None = None,
    high_ms: int | None = None,
    gap_ms: int | None = None,
    *,
    stop: bool = False,
) -> SerialResponse:
    expected_pin = _gpio_mode_pin(pin)
    if stop:
        command = build_gpio_pulse_stop_command(expected_pin)
    else:
        if count is None or high_ms is None or gap_ms is None:
            raise ValueError("pulse count, high_ms, and gap_ms are required")
        command = build_gpio_pulse_command(expected_pin, count, high_ms, gap_ms)
    response = _firmware_error_response(transport(command, expect_response=True))
    if not response.ok:
        return response
    try:
        ack = parse_gpio_pulse_response(response.data)
    except ValueError as exc:
        return _unexpected_response(response, str(exc))
    if ack.pin != expected_pin or ack.stopped != stop:
        return _unexpected_response(response, "GPIO pulse pin/mode mismatch")
    return response


def set_i2c_speed(transport: CommandTransport, speed_hz: int) -> SerialResponse:
    return _firmware_error_response(
        transport(build_set_i2c_speed_command(speed_hz), expect_response=True)
    )


def read_i2c_speed(transport: CommandTransport) -> SerialResponse:
    return _firmware_error_response(
        transport(build_read_i2c_speed_command(), expect_response=True)
    )


def write_register_values(
    transport: CommandTransport,
    device_addr: int,
    reg_addr: int,
    values: list[int] | tuple[int, ...],
    *,
    bits: int,
    log: Callable[[str], None] | None = None,
) -> RegisterTransaction:
    data = tuple(values)
    if log is not None:
        log(
            f"WRITE{_register_data_width(bits)} "
            f"dev=0x{_device_address(device_addr):02X} "
            f"reg=0x{u8(reg_addr, name='reg_addr'):02X} count={len(data)}"
        )
    response = transport(
        build_write_register_values_command(device_addr, reg_addr, data, bits=bits),
        expect_response=True,
    )
    response = expect_wregw_response(
        response,
        device_addr=device_addr,
        reg_addr=reg_addr,
        bits=bits,
        count=len(data),
    )
    return RegisterTransaction(response)


def read_register_values(
    transport: CommandTransport,
    device_addr: int,
    reg_addr: int,
    *,
    bits: int,
    count: int = 1,
    log: Callable[[str], None] | None = None,
) -> RegisterTransaction:
    if log is not None:
        log(
            f"READ{_register_data_width(bits)} "
            f"dev=0x{_device_address(device_addr):02X} "
            f"reg=0x{u8(reg_addr, name='reg_addr'):02X} count={count}"
        )
    response = _firmware_error_response(
        transport(
            build_read_register_values_command(device_addr, reg_addr, bits=bits, count=count),
            expect_response=True,
        )
    )
    if response.ok and extract_rdataw_values(
        response.data,
        device_addr=device_addr,
        reg_addr=reg_addr,
        bits=bits,
        count=count,
    ) is None:
        response = _unexpected_response(response)
    return RegisterTransaction(response)


def write_register_value(
    transport: CommandTransport,
    device_addr: int,
    reg_addr: int,
    value: int,
    *,
    bits: int,
    log: Callable[[str], None] | None = None,
) -> RegisterTransaction:
    return write_register_values(
        transport,
        device_addr,
        reg_addr,
        (value,),
        bits=bits,
        log=log,
    )


def read_register_value(
    transport: CommandTransport,
    device_addr: int,
    reg_addr: int,
    *,
    bits: int,
    log: Callable[[str], None] | None = None,
) -> RegisterTransaction:
    return read_register_values(
        transport,
        device_addr,
        reg_addr,
        bits=bits,
        count=1,
        log=log,
    )
