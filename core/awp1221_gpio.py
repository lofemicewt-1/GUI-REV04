from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.base_tool import GPIO_MODE_DETECT, GPIO_MODE_HIGH, GPIO_MODE_HIZ, GPIO_MODE_LOW


AWP1221_GPIO_REGISTER = 0x07
AWP1221_GPIO_RESERVED_MASK = 0xC0C0
AWP1221_GPIO_EDIT_MASK = 0x3F3F
AWP1221_GPIO0_FIELD_MASK = 0x003F
AWP1221_GPIO1_FIELD_MASK = 0x3F00
AWP1221_GPIO_PINS = (("GPIO0", "D2"), ("GPIO1", "D3"))

_OUTPUT_FUNCTIONS = frozenset(range(0x00, 0x06))
_INPUT_FUNCTIONS = frozenset({0x07, 0x08, 0x09, 0x0A, 0x0B})
_DEBUG_FUNCTIONS = frozenset({0x0C, 0x0D, 0x0E, 0x0F})
_SYNC_FUNCTIONS = frozenset({0x10, 0x11, 0x12})
_KNOWN_FUNCTIONS = (
    _OUTPUT_FUNCTIONS | _INPUT_FUNCTIONS | _DEBUG_FUNCTIONS | _SYNC_FUNCTIONS
)

GPIO_FUNCTION_LABELS = {
    0x00: "Pressed=0 / Released=1",
    0x01: "播放=0 / 非播放=1",
    0x02: "Error state / masked",
    0x03: "ADC state / masked",
    0x04: "Sense and Play state / masked",
    0x05: "Button state / masked",
    0x07: "Floating",
    0x08: "Extern Trigger active Low (EN)",
    0x09: "PE for press",
    0x0A: "NE for release",
    0x0B: "PE for press and NE for release",
    0x0C: "cmp_press",
    0x0D: "cmp_release",
    0x0E: "clk/2",
    0x0F: "Analog debug",
    0x10: "sync press",
    0x11: "sync release",
    0x12: "sync press and release",
}

GPIO_CATEGORY_FUNCTIONS = {
    "output": tuple(sorted(_OUTPUT_FUNCTIONS)),
    "input": tuple(sorted(_INPUT_FUNCTIONS)),
    "debug": tuple(sorted(_DEBUG_FUNCTIONS)),
    "sync": tuple(sorted(_SYNC_FUNCTIONS)),
}
GPIO_CATEGORY_LABELS = {
    "output": "OUTPUT Functions",
    "input": "INPUT Functions",
    "debug": "DEBUG_OUTPUT",
    "sync": "SYNC MODE",
}


@dataclass(frozen=True)
class GpioChannelBits:
    function: int
    open_drain: bool


def _require_function(code: int) -> int:
    value = int(code)
    if value not in _KNOWN_FUNCTIONS:
        raise ValueError(f"unsupported GPIO function: 0x{value:02X}")
    return value


def gpio_function_category(code: int) -> str:
    value = _require_function(code)
    if value in _OUTPUT_FUNCTIONS:
        return "output"
    if value in _INPUT_FUNCTIONS:
        return "input"
    if value in _DEBUG_FUNCTIONS:
        return "debug"
    return "sync"


def gpio_mcu_role(code: int) -> str:
    category = gpio_function_category(code)
    if category == "output":
        return "detect"
    if category == "input":
        return "drive"
    return "hiz"


def gpio_pulse_allowed(code: int) -> bool:
    return gpio_mcu_role(code) == "drive"


def gpio_mcu_mode_for_function(code: int) -> str:
    return GPIO_MODE_DETECT if gpio_mcu_role(code) == "detect" else GPIO_MODE_HIZ


def unpack_gpio_register(value: int) -> tuple[GpioChannelBits, GpioChannelBits, int]:
    word = int(value)
    if not 0 <= word <= 0xFFFF:
        raise ValueError("GPIO register must be in range 0x0000..0xFFFF")
    gpio0 = GpioChannelBits((word >> 1) & 0x1F, bool(word & 0x0001))
    gpio1 = GpioChannelBits((word >> 9) & 0x1F, bool(word & 0x0100))
    return gpio0, gpio1, word & AWP1221_GPIO_RESERVED_MASK


def pack_gpio_register(
    old: int,
    gpio0: GpioChannelBits,
    gpio1: GpioChannelBits,
) -> int:
    word = int(old)
    if not 0 <= word <= 0xFFFF:
        raise ValueError("GPIO register must be in range 0x0000..0xFFFF")
    func0 = _require_function(gpio0.function)
    func1 = _require_function(gpio1.function)
    return (
        (word & AWP1221_GPIO_RESERVED_MASK)
        | ((func0 & 0x1F) << 1)
        | (1 if gpio0.open_drain else 0)
        | ((func1 & 0x1F) << 9)
        | ((1 if gpio1.open_drain else 0) << 8)
    )


def pack_gpio_channel(old: int, channel: int, bits: GpioChannelBits) -> int:
    word = int(old)
    if not 0 <= word <= 0xFFFF:
        raise ValueError("GPIO register must be in range 0x0000..0xFFFF")
    func = _require_function(bits.function)
    od = 1 if bits.open_drain else 0
    if channel == 0:
        return (word & ~AWP1221_GPIO0_FIELD_MASK) | ((func & 0x1F) << 1) | od
    if channel == 1:
        return (word & ~AWP1221_GPIO1_FIELD_MASK) | ((func & 0x1F) << 9) | (od << 8)
    raise ValueError("GPIO channel must be 0 or 1")


def validate_gpio_pulse(count: int, high_ms: int, gap_ms: int) -> tuple[int, int, int]:
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


def gpio_pulse_steps(count: int, high_ms: int, gap_ms: int) -> tuple[tuple[int, int], ...]:
    count_value, high_value, gap_value = validate_gpio_pulse(count, high_ms, gap_ms)
    steps: list[tuple[int, int]] = []
    for index in range(count_value):
        if index:
            steps.append((0, gap_value))
        steps.append((1, high_value))
    return tuple(steps)


class Awp1221GpioController:
    def __init__(
        self,
        serial: Any,
        device_addr: Callable[[], int],
    ) -> None:
        self.serial = serial
        self.device_addr = device_addr
        self.pending: tuple[GpioChannelBits, GpioChannelBits] | None = None
        self.chip_confirmed = False
        self.confirmed_channels: tuple[GpioChannelBits | None, GpioChannelBits | None] = (
            None,
            None,
        )
        self.confirmed_register: int | None = None
        self.confirmed_mcu: tuple[str | None, str | None] = (None, None)

    def invalidate(self) -> None:
        self.chip_confirmed = False
        self.confirmed_channels = (None, None)
        self.confirmed_register = None
        self.confirmed_mcu = (None, None)

    def set_pending(self, gpio0: GpioChannelBits, gpio1: GpioChannelBits) -> None:
        self.pending = (
            GpioChannelBits(_require_function(gpio0.function), bool(gpio0.open_drain)),
            GpioChannelBits(_require_function(gpio1.function), bool(gpio1.open_drain)),
        )

    def pulse_allowed(self, channel: int) -> bool:
        bits = self.confirmed_channels[channel] if self.chip_confirmed else None
        if bits is None or not gpio_pulse_allowed(bits.function):
            return False
        return self.confirmed_mcu[channel] is not None

    def apply(self) -> str | None:
        if self.pending is None:
            self.invalidate()
            return "GPIO pending values are incomplete"
        if not self._stop_and_release():
            return self._fail("failed to release GPIO")
        old = self._read_register()
        if old is None:
            return self._fail("failed to read GPIO register 0x07")
        new = pack_gpio_register(old, self.pending[0], self.pending[1])
        write = self.serial.write_register_value(
            self.device_addr(),
            AWP1221_GPIO_REGISTER,
            new,
            bits=16,
        )
        if not bool(getattr(write, "ok", False)):
            return self._fail(self._error_text(write, "failed to write GPIO register 0x07"))
        readback = self._read_register()
        if readback is None or readback != new:
            return self._fail("GPIO register 0x07 readback mismatch")
        mcu_modes: list[str | None] = [None, None]
        for index, bits in enumerate(self.pending):
            pin = AWP1221_GPIO_PINS[index][1]
            mode = gpio_mcu_mode_for_function(bits.function)
            response = self.serial.configure_gpio(pin, mode)
            if not bool(getattr(response, "ok", False)):
                self.confirmed_register = new
                self.confirmed_channels = self.pending
                self.chip_confirmed = True
                self.confirmed_mcu = tuple(mcu_modes)  # type: ignore[assignment]
                return self._error_text(response, f"GPIOCFG {pin} {mode} failed")
            mcu_modes[index] = mode
        self.confirmed_register = new
        self.confirmed_channels = self.pending
        self.confirmed_mcu = (mcu_modes[0], mcu_modes[1])
        self.chip_confirmed = True
        return None

    def read(self) -> str | None:
        value = self._read_register()
        if value is None:
            return self._fail("failed to read GPIO register 0x07")
        gpio0, gpio1, _reserved = unpack_gpio_register(value)
        try:
            self.set_pending(gpio0, gpio1)
        except ValueError as exc:
            return self._fail(str(exc))
        self.confirmed_register = value
        self.confirmed_channels = self.pending
        self.chip_confirmed = True
        return None

    def start_pulse(self, channel: int, count: int, high_ms: int, gap_ms: int) -> str | None:
        if not self.pulse_allowed(channel):
            return "pulse requires a confirmed chip INPUT function"
        try:
            validate_gpio_pulse(count, high_ms, gap_ms)
        except ValueError as exc:
            return str(exc)
        pin = AWP1221_GPIO_PINS[channel][1]
        response = self.serial.pulse_gpio(pin, count, high_ms, gap_ms)
        if not bool(getattr(response, "ok", False)):
            return self._error_text(response, "GPIOPULSE failed")
        confirmed = list(self.confirmed_mcu)
        confirmed[channel] = GPIO_MODE_HIGH
        self.confirmed_mcu = (confirmed[0], confirmed[1])
        return None

    def stop_pulse(self, channel: int) -> str | None:
        pin = AWP1221_GPIO_PINS[channel][1]
        mode = GPIO_MODE_HIZ
        bits = self.confirmed_channels[channel] if self.chip_confirmed else None
        if bits is not None:
            role = gpio_mcu_role(bits.function)
            current = self.confirmed_mcu[channel]
            if role == "drive" and current in {GPIO_MODE_HIGH, GPIO_MODE_LOW}:
                mode = GPIO_MODE_LOW
            else:
                mode = gpio_mcu_mode_for_function(bits.function)
        response = self.serial.configure_gpio(pin, mode)
        if not bool(getattr(response, "ok", False)):
            return self._error_text(response, f"GPIOCFG {pin} {mode} failed")
        confirmed = list(self.confirmed_mcu)
        confirmed[channel] = mode
        self.confirmed_mcu = (confirmed[0], confirmed[1])
        return None

    def drive(self, channel: int, high: bool) -> str | None:
        if not self.pulse_allowed(channel):
            return "MCU drive requires a confirmed chip INPUT function"
        pin = AWP1221_GPIO_PINS[channel][1]
        mode = "HIGH" if high else "LOW"
        response = self.serial.configure_gpio(pin, mode)
        if not bool(getattr(response, "ok", False)):
            return self._error_text(response, f"GPIOCFG {pin} {mode} failed")
        confirmed = list(self.confirmed_mcu)
        confirmed[channel] = mode
        self.confirmed_mcu = (confirmed[0], confirmed[1])
        return None

    def _stop_and_release(self) -> bool:
        for pin in (AWP1221_GPIO_PINS[0][1], AWP1221_GPIO_PINS[1][1]):
            release = self.serial.configure_gpio(pin, GPIO_MODE_HIZ)
            if not bool(getattr(release, "ok", False)):
                return False
        return True

    def _read_register(self) -> int | None:
        address = self.device_addr()
        response = self.serial.read_register_value(
            address,
            AWP1221_GPIO_REGISTER,
            bits=16,
        )
        if not bool(getattr(response, "ok", False)):
            return None
        extractor = getattr(self.serial, "extract_rdataw_value", None)
        if callable(extractor):
            value = extractor(
                getattr(response, "data", ""),
                device_addr=address,
                reg_addr=AWP1221_GPIO_REGISTER,
                bits=16,
            )
        else:
            value = None
        try:
            word = int(value)
        except (TypeError, ValueError):
            return None
        if not 0 <= word <= 0xFFFF:
            return None
        return word

    def _fail(self, message: str) -> str:
        self.invalidate()
        return message

    @staticmethod
    def _error_text(response: Any, fallback: str) -> str:
        detail = str(getattr(response, "error", "") or "").strip()
        if detail:
            return detail
        data = str(getattr(response, "data", "") or "").strip()
        return data or fallback
