from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RegisterAccess(str, Enum):
    READ_ONLY = "read_only"
    WRITE_ONLY = "write_only"
    READ_WRITE = "read_write"
    READ_WRITE_DIFFERENT = "read_write_different"


@dataclass(frozen=True)
class BitFieldDefinition:
    high_bit: int
    low_bit: int
    key: str
    label: str
    tooltip: str = ""
    read_label: str | None = None
    read_tooltip: str | None = None
    write_label: str | None = None
    write_tooltip: str | None = None
    choices: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.low_bit < 0 or self.high_bit < self.low_bit:
            raise ValueError("bit field range is invalid")
        for value in self.choices:
            if int(value) < 0 or int(value) >= (1 << self.width):
                raise ValueError("field choice value exceeds field width")

    @property
    def width(self) -> int:
        return self.high_bit - self.low_bit + 1

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.low_bit

    @property
    def span(self) -> int:
        return self.width

    @property
    def is_reserved(self) -> bool:
        return self.key.lower().startswith("reserved") or self.label.strip().lower() == "reserved"

    def extract(self, value: int) -> int:
        return (int(value) & self.mask) >> self.low_bit

    def update(self, current_value: int, field_value: int) -> int:
        clipped = int(field_value) & ((1 << self.width) - 1)
        return (int(current_value) & ~self.mask) | (clipped << self.low_bit)

    def label_for_view(self, view: str = "write") -> str:
        if view == "read" and self.read_label is not None:
            return self.read_label
        if view == "write" and self.write_label is not None:
            return self.write_label
        return self.label

    def tooltip_for_view(self, view: str = "write") -> str:
        if view == "read" and self.read_tooltip is not None:
            return self.read_tooltip
        if view == "write" and self.write_tooltip is not None:
            return self.write_tooltip
        return self.tooltip


@dataclass(frozen=True)
class RegisterFieldBlock:
    high_bit: int
    low_bit: int
    key: str
    label: str
    tooltip: str
    value: int
    span: int
    mask: int
    is_reserved: bool = False
    choices: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RegisterFieldLayout:
    block: RegisterFieldBlock
    column: int
    columnspan: int


@dataclass(frozen=True)
class RegisterFieldChoice:
    label: str
    value: int


@dataclass(frozen=True)
class RegisterDefinition:
    address: int
    width: int = 16
    summary: str = ""
    access: RegisterAccess = RegisterAccess.READ_WRITE
    note: str = ""
    default_value: int = 0
    fields: list[BitFieldDefinition] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.width not in (8, 16):
            raise ValueError("register width must be 8 or 16")
        if self.address < 0 or self.address > 0xFF:
            raise ValueError("register address must fit in 8 bits")
        if self.default_value < 0:
            raise ValueError("register default value must be non-negative")
        for bit_field in self.fields:
            if bit_field.high_bit >= self.width:
                raise ValueError("bit field range exceeds register width")

    @property
    def can_read(self) -> bool:
        return self.access in (
            RegisterAccess.READ_ONLY,
            RegisterAccess.READ_WRITE,
            RegisterAccess.READ_WRITE_DIFFERENT,
        )

    @property
    def can_write(self) -> bool:
        return self.access in (
            RegisterAccess.WRITE_ONLY,
            RegisterAccess.READ_WRITE,
            RegisterAccess.READ_WRITE_DIFFERENT,
        )

    @property
    def value_mask(self) -> int:
        return (1 << self.width) - 1


def build_field_blocks(
    definition: RegisterDefinition,
    value: int,
    *,
    view: str = "write",
) -> list[RegisterFieldBlock]:
    clipped = int(value) & definition.value_mask
    fields = sorted(definition.fields, key=lambda item: item.high_bit, reverse=True)
    return [
        RegisterFieldBlock(
            high_bit=item.high_bit,
            low_bit=item.low_bit,
            key=item.key,
            label=item.label_for_view(view),
            tooltip=item.tooltip_for_view(view),
            value=item.extract(clipped),
            span=item.span,
            mask=item.mask,
            is_reserved=item.is_reserved,
            choices=dict(item.choices),
        )
        for item in fields
    ]


def build_field_layout(
    definition: RegisterDefinition,
    value: int,
    *,
    view: str = "write",
) -> list[RegisterFieldLayout]:
    return [
        RegisterFieldLayout(
            block=block,
            column=definition.width - 1 - block.high_bit,
            columnspan=block.span,
        )
        for block in build_field_blocks(definition, value, view=view)
    ]


def build_field_value_choices(
    block: RegisterFieldBlock,
    *,
    max_choices: int = 512,
) -> list[RegisterFieldChoice]:
    if block.is_reserved:
        return []
    if block.choices:
        return [
            RegisterFieldChoice(f"0x{int(value):X} - {label}", int(value))
            for value, label in sorted(block.choices.items(), key=lambda item: int(item[0]))
        ]
    total = 1 << block.span
    if total > max_choices:
        return []
    if block.span == 1:
        return [
            RegisterFieldChoice("0 - Clear", 0),
            RegisterFieldChoice("1 - Set", 1),
        ]
    return [RegisterFieldChoice(f"0x{value:X}", value) for value in range(total)]


def parse_field_choice_value(text: str) -> int:
    token = text.strip().split(maxsplit=1)[0]
    return int(token, 0)


def update_register_field(
    definition: RegisterDefinition,
    current_value: int,
    field_key: str,
    field_value: int,
) -> int:
    for bit_field in definition.fields:
        if bit_field.key == field_key:
            return bit_field.update(int(current_value) & definition.value_mask, field_value) & definition.value_mask
    raise KeyError(field_key)


def format_register_value(definition: RegisterDefinition, value: int) -> str:
    clipped = int(value) & definition.value_mask
    digits = 2 if definition.width == 8 else 4
    return f"0x{clipped:0{digits}X}"
