from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Generic, Protocol, TypeVar


FRAME_START = "^"
FRAME_END = "~"
PROTOCOL_REVISION = "REV04"
REGISTER_DATA_WIDTHS = (8, 16)
RREGW_MAX_VALUES = {8: 32, 16: 16}
WREGW_MAX_VALUES = {8: 10, 16: 5}
ALLOWED_I2C_SPEEDS = (100000, 400000)
I2C_ADDRESS_NACK_RETRY_DELAY_S = 0.002
GPIO_MODE_HIGH = "HIGH"
GPIO_MODE_LOW = "LOW"
GPIO_MODE_DETECT = "DETECT"
GPIO_MODE_HIZ = "HIZ"
GPIO_MODE_PULLUP = "PULLUP"
GPIO_MODES = (
    GPIO_MODE_HIGH,
    GPIO_MODE_LOW,
    GPIO_MODE_DETECT,
    GPIO_MODE_HIZ,
    GPIO_MODE_PULLUP,
)
GPIO_DETECT_PINS = ("D2", "D3", "D4", "D5")
GPIO_PUBLIC_PINS = (
    "D2",
    "D3",
    "D4",
    "D5",
    "D6",
    "D8",
    "D9",
    "D10",
    "D11",
    "D12",
    "D13",
    "A1",
    "A2",
    "A3",
)
GPIO_MODE_PINS = GPIO_PUBLIC_PINS
GPIO_ADC_PINS = ("A1", "A2", "A3")
GPIO_FREQ_PIN = "D8"

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
class GpioReadAck:
    pin: str
    level: int


@dataclass(frozen=True)
class AdcReadAck:
    pin: str
    value: int


@dataclass(frozen=True)
class FreqAck:
    pin: str
    period_us: int

    @property
    def frequency_hz(self) -> float:
        return 1_000_000.0 / self.period_us if self.period_us else 0.0


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
        raise ValueError("register data width must be 8 or 16 bits")
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
    r"RDATAW\s+([0-9A-Fa-f]{2})\s+([0-9A-Fa-f]{2})\s+(8|16)\s+([1-9][0-9]*)\s+([0-9A-Fa-f]+)"
)
_OK_WREGW_PATTERN = re.compile(
    r"OK\s+WREGW\s+([0-9A-Fa-f]{2})\s+([0-9A-Fa-f]{2})\s+(8|16)\s+([1-9][0-9]*)"
)
_I2C_ADDRESS_NACK_PATTERN = re.compile(
    r"ERR\s+(?:RREGW|WREGW)\s+I2C\s+2\b|ERR\s+WREGW\s+I2C\s*(?:$|[^0-9])",
    re.IGNORECASE,
)
_I2C_OTHER_CODE_PATTERN = re.compile(r"\bI2C\s+(?!2\b)\d+", re.IGNORECASE)


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
    if token not in GPIO_PUBLIC_PINS:
        raise ValueError(f"{token} is reserved and unavailable to public GPIO")
    return token


def _flag_value(value: bool | int) -> int:
    return 1 if bool(value) else 0


def ready_notification_body() -> str:
    return f"READY {PROTOCOL_REVISION}"


def is_ready_notification(text: str) -> bool:
    return _normalized_frame(text).upper() == ready_notification_body()


def build_read_board_flag_command() -> str:
    return "FLAG?"


def build_interrupt_ack_command(sequence: int) -> str:
    value = int(sequence)
    if not 1 <= value <= 0xFFFFFFFF:
        raise ValueError("sequence must be between 1 and FFFFFFFF")
    return f"INTACK {value:08X}"


_FLAG_STATUS_PATTERN = re.compile(r"FLAG\s+INTF=([01])\s+SEQ=([0-9A-Fa-f]{8})", re.IGNORECASE)
_INTACK_OK_PATTERN = re.compile(r"OK\s+INTACK\s+([0-9A-Fa-f]{8})")
_GPIO_MODE_PATTERN = re.compile(
    r"GPIOCFG\s+(D(?:[2-9]|1[0-3])|A[1-3])\s+(HIGH|LOW|DETECT|HIZ|PULLUP)",
    re.IGNORECASE,
)
_GPIO_READ_PATTERN = re.compile(
    r"GPIOR\s+(D(?:[2-9]|1[0-3])|A[1-3]|[0-9]{1,2})\s+([01])",
    re.IGNORECASE,
)
_ADCR_PATTERN = re.compile(r"ADCR\s+(A[1-3])\s+(\d{1,4})", re.IGNORECASE)
_FREQ_PATTERN = re.compile(r"FREQ\s+D8\s+([1-9][0-9]*)", re.IGNORECASE)


def parse_board_flag_response(text: str) -> BoardFlagStatus:
    match = _FLAG_STATUS_PATTERN.fullmatch(_normalized_frame(text))
    if match is None:
        raise ValueError("invalid FLAG response")
    intf_text, sequence_text = match.groups()
    sequence = int(sequence_text, 16)
    intf = bool(int(intf_text))
    if intf != bool(sequence):
        raise ValueError("FLAG INTF and SEQ are inconsistent")
    return BoardFlagStatus(intf, intf, sequence)


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


def build_read_gpio_command(pin: str | int) -> str:
    return f"GPIOR {_public_gpio_pin(pin)}"


def _gpio_mode_pin(pin: str | int) -> str:
    return _public_gpio_pin(pin)


def _gpio_mode_token(mode: str, *, pin: str | None = None) -> str:
    token = str(mode).strip().upper()
    if token not in GPIO_MODES:
        raise ValueError(f"unsupported GPIO mode: {mode}")
    if token == GPIO_MODE_DETECT and pin is not None and pin not in GPIO_DETECT_PINS:
        raise ValueError(f"{pin} does not support DETECT")
    return token


def build_gpio_mode_command(pin: str | int, mode: str) -> str:
    pin_token = _gpio_mode_pin(pin)
    return f"GPIOCFG {pin_token} {_gpio_mode_token(mode, pin=pin_token)}"


def parse_gpio_mode_response(text: str) -> GpioModeAck:
    match = _GPIO_MODE_PATTERN.fullmatch(_normalized_frame(text).upper())
    if match is None:
        raise ValueError("invalid GPIOCFG response")
    pin = match.group(1).upper()
    mode = match.group(2).upper()
    if pin not in GPIO_PUBLIC_PINS:
        raise ValueError("invalid GPIOCFG response")
    if mode == GPIO_MODE_DETECT and pin not in GPIO_DETECT_PINS:
        raise ValueError("invalid GPIOCFG response")
    return GpioModeAck(pin=pin, mode=mode)


def parse_gpio_read_response(text: str) -> GpioReadAck:
    match = _GPIO_READ_PATTERN.fullmatch(_normalized_frame(text))
    if match is None:
        raise ValueError("invalid GPIOR response")
    pin = _public_gpio_pin(match.group(1))
    return GpioReadAck(pin=pin, level=int(match.group(2)))


def build_adc_read_command(pin: str) -> str:
    token = str(pin).strip().upper()
    if token not in GPIO_ADC_PINS:
        raise ValueError("ADC pin must be A1, A2, or A3")
    return f"ADCR {token}"


def parse_adc_read_response(text: str) -> AdcReadAck:
    match = _ADCR_PATTERN.fullmatch(_normalized_frame(text))
    if match is None:
        raise ValueError("invalid ADCR response")
    value = int(match.group(2))
    if not 0 <= value <= 1023:
        raise ValueError("invalid ADCR response")
    return AdcReadAck(pin=match.group(1).upper(), value=value)


def build_freq_command() -> str:
    return "FREQ?"


def parse_freq_response(text: str) -> FreqAck:
    match = _FREQ_PATTERN.fullmatch(_normalized_frame(text))
    if match is None:
        raise ValueError("invalid FREQ response")
    return FreqAck(pin=GPIO_FREQ_PIN, period_us=int(match.group(1)))


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
    if value not in (128, 256):
        raise ValueError("length must be 128 or 256")
    return value


def _cfg_crc(crc: int) -> str:
    value = int(crc)
    if not 0 <= value <= 0xFFFF:
        raise ValueError("crc must fit uint16")
    return f"{value:04X}"


def _cfg_offset(offset: int) -> int:
    value = int(offset)
    if not 0 <= value <= 255:
        raise ValueError("offset must be between 0 and 255")
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


# Arduino Wire endTransmission()/requestFrom codes plus firmware-local ones.
I2C_ERROR_CODE_TEXT = {
    1: "data too long for Wire buffer",
    2: "address NACK (chip not responding)",
    3: "data NACK",
    4: "other I2C bus error",
    5: "I2C timeout",
    6: "short read (fewer bytes than requested)",
    7: "invalid read size",
}

_ERR_FRAME_PATTERN = re.compile(r"ERR\s+(\S+)(?:\s+(\S+))?(?:\s+(.*))?", re.IGNORECASE)


@dataclass(frozen=True)
class FirmwareError:
    command: str
    reason: str
    code: int | None
    detail: str

    def describe(self) -> str:
        """Readable location of the failure, e.g. ``WREGW failed: I2C code=3 (data NACK)``."""
        if self.command == "UNKNOWN":
            return "firmware does not support this command (ERR UNKNOWN CMD)"
        text = f"{self.command} failed: {self.reason or '?'}"
        if self.code is not None:
            text += f" code={self.code}"
            meaning = I2C_ERROR_CODE_TEXT.get(self.code) if self.reason.upper() == "I2C" else None
            if meaning:
                text += f" ({meaning})"
        elif self.detail:
            text += f" {self.detail}"
        return text


def parse_firmware_error(text: str) -> FirmwareError | None:
    """Parse ``^ERR <CMD> <REASON> [code|detail]~``; None when not an ERR frame."""
    match = _ERR_FRAME_PATTERN.fullmatch(_normalized_frame(str(text or "")))
    if match is None:
        return None
    command, reason, rest = match.group(1), match.group(2) or "", (match.group(3) or "").strip()
    code: int | None = None
    if rest.isdecimal():
        code = int(rest)
        rest = ""
    return FirmwareError(command.upper(), reason.upper(), code, rest)


def describe_firmware_error(text: str) -> str | None:
    parsed = parse_firmware_error(text)
    return None if parsed is None else parsed.describe()


def format_firmware_error(text: str) -> str:
    """Raw ERR frame followed by a decoded description (raw text kept first)."""
    description = describe_firmware_error(text)
    raw = str(text or "")
    return raw if description is None else f"{raw} [{description}]"


def _firmware_error_response(response: SerialResponse) -> SerialResponse:
    """Treat a framed firmware error as a failed public-protocol operation."""
    if response.ok and _normalized_frame(response.data).upper().startswith("ERR "):
        return SerialResponse(False, data=response.data, error=format_firmware_error(response.data))
    return response


def is_i2c_address_nack(response: SerialResponse) -> bool:
    """True for Wire address NACK (error 2), including WREGW frames that omit the code."""
    text = f"{response.error} {response.data}"
    parsed = parse_firmware_error(response.data) or parse_firmware_error(response.error)
    if parsed is not None and parsed.command in {"RREGW", "WREGW"}:
        # Forward compatible: a present code decides (only 2 = address NACK);
        # bare "ERR WREGW I2C" (current firmware, no code) keeps the old retry.
        if parsed.reason != "I2C":
            return False
        return parsed.code == 2 or (parsed.code is None and parsed.command == "WREGW")
    if _I2C_OTHER_CODE_PATTERN.search(text):
        return False
    return _I2C_ADDRESS_NACK_PATTERN.search(text) is not None


def _retry_8bit_address_nack(bits: int, operation: Callable[[], SerialResponse]) -> SerialResponse:
    response = operation()
    if int(bits) != 8 or response.ok or not is_i2c_address_nack(response):
        return response
    time.sleep(I2C_ADDRESS_NACK_RETRY_DELAY_S)
    return operation()


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


def read_gpio(transport: CommandTransport, pin: str | int) -> SerialResponse:
    expected_pin = _public_gpio_pin(pin)
    response = _firmware_error_response(
        transport(build_read_gpio_command(expected_pin), expect_response=True)
    )
    if not response.ok:
        return response
    try:
        ack = parse_gpio_read_response(response.data)
    except ValueError as exc:
        return _unexpected_response(response, str(exc))
    if ack.pin != expected_pin:
        return _unexpected_response(response, "GPIOR pin mismatch")
    return response


def configure_gpio_mode(
    transport: CommandTransport, pin: str | int, mode: str
) -> SerialResponse:
    expected_pin = _gpio_mode_pin(pin)
    expected_mode = _gpio_mode_token(mode, pin=expected_pin)
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


def read_adc(transport: CommandTransport, pin: str) -> SerialResponse:
    expected_pin = str(pin).strip().upper()
    response = _firmware_error_response(
        transport(build_adc_read_command(expected_pin), expect_response=True)
    )
    if not response.ok:
        return response
    try:
        ack = parse_adc_read_response(response.data)
    except ValueError as exc:
        return _unexpected_response(response, str(exc))
    if ack.pin != expected_pin:
        return _unexpected_response(response, "ADCR pin mismatch")
    return response


def read_frequency(transport: CommandTransport) -> SerialResponse:
    response = _firmware_error_response(
        transport(build_freq_command(), expect_response=True)
    )
    if not response.ok:
        return response
    try:
        parse_freq_response(response.data)
    except ValueError as exc:
        return _unexpected_response(response, str(exc))
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

    def once() -> SerialResponse:
        if log is not None:
            log(
                f"WRITE{_register_data_width(bits)} "
                f"dev=0x{_device_address(device_addr):02X} "
                f"reg=0x{u8(reg_addr, name='reg_addr'):02X} count={len(data)}"
            )
        response = _firmware_error_response(
            transport(
                build_write_register_values_command(device_addr, reg_addr, data, bits=bits),
                expect_response=True,
            )
        )
        if not response.ok:
            return response
        return expect_wregw_response(
            response,
            device_addr=device_addr,
            reg_addr=reg_addr,
            bits=bits,
            count=len(data),
        )

    return RegisterTransaction(_retry_8bit_address_nack(bits, once))


def read_register_values(
    transport: CommandTransport,
    device_addr: int,
    reg_addr: int,
    *,
    bits: int,
    count: int = 1,
    log: Callable[[str], None] | None = None,
) -> RegisterTransaction:
    def once() -> SerialResponse:
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
        return response

    return RegisterTransaction(_retry_8bit_address_nack(bits, once))


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
