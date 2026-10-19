"""Pure parser and host-side cache helpers for the formal FLAG status path.

This module deliberately has no serial dependency and performs no I/O.  The
formal FLAG1/FLAG2 protocol is a small, fixed-width, ``^...~`` frame protocol
described in the status and D2 scan documents.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional


SLOT_COUNT = 3
CHIP_TYPE_192 = "192"
CHIP_TYPE_1221 = "1221"
# Keep the old name as a compatibility alias for the preceding 192x-only
# fixtures.  New validation uses the per-type address table below.
CHIP_TYPE = CHIP_TYPE_192
SUPPORTED_CHIP_TYPES = frozenset((CHIP_TYPE_192, CHIP_TYPE_1221))
ALLOWED_ADDRESSES_BY_TYPE = {
    CHIP_TYPE_192: frozenset((0x44, 0x45, 0x46)),
    CHIP_TYPE_1221: frozenset(range(0x50, 0x58)),
}
ALLOWED_ADDRESSES = ALLOWED_ADDRESSES_BY_TYPE[CHIP_TYPE_192]
MAX_U32 = 0xFFFFFFFF
MAX_U16 = 0xFFFF

FLAG_CONFIGURED = 0x01
FLAG_VALID = 0x02
FLAG_ERROR = 0x04
FLAG_STALE = 0x08
FLAG_NO_SAMPLE = 0x10
FLAG_SEQ_WRAP = 0x20
FLAG_MONITOR_ENABLED = 0x40

SOURCE_NONE = 0x00
SOURCE_IRQ_02 = 0x01
SOURCE_REARM_02 = 0x02
SOURCE_1221_STATE = 0x03
SOURCE_D2_SCAN = 0x04
ERROR_NONE = 0x00
ERROR_I2C_READ = 0x01


# This table mirrors the existing AWP192x 0x02 page definition.  Reserved bits
# remain visible through the raw value and ``unknown_mask`` rather than being
# assigned a state label.
STATUS_BITS_192 = (
    (15, "BST_OUT_H"),
    (14, "ON_PLAY"),
    (13, "ADC_OVER_H"),
    (12, "ADC_OVER_L"),
    (11, "ADC_OVER_H_POSEDGE"),
    (10, "ADC_OVER_H_NEGEDGE"),
    (9, "ADC_OVER_L_POSEDGE"),
    (8, "ADC_OVER_L_NEGEDGE"),
    (6, "UNV"),
    (4, "OVT"),
    (3, "HALF_EMPTY"),
    (2, "FIFO_FULL"),
    (1, "EMPTY"),
    (0, "ADC_BUSY"),
)

# User-provided AWP1221 State diagram, bit 15 down to bit 0.
STATUS_BITS_1221 = (
    (15, "LOOP_ERR"),
    (14, "CP_RDY_ERROR"),
    (13, "UNV"),
    (12, "OVV"),
    (11, "TSD"),
    (10, "SC"),
    (9, "ADC_FULL_L"),
    (8, "ADC_FULL_H"),
    (7, "Init Done"),
    (6, "ON_ZERO"),
    (5, "ON_DEBOUNCE"),
    (4, "ON_SENSE"),
    (3, "ON_PLAY"),
    (2, "PLAY_DONE"),
    (1, "ON_RELEASE"),
    (0, "ON_PRESS"),
)


@dataclass(frozen=True)
class StatusDecode:
    """Type-specific labels for one raw 16-bit State word."""

    chip_type: str
    raw: int
    active_bits: tuple[tuple[int, str], ...]
    unknown_mask: int

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(label for _, label in self.active_bits)


class ProtocolError(ValueError):
    """A frame or state value violates the isolated trial contract."""


class FrameError(ProtocolError):
    """A serial frame cannot be parsed as a trial response."""


class StateError(ProtocolError):
    """A parsed response cannot be applied to the host state."""


def decode_status(chip_type: str, raw: int) -> StatusDecode:
    """Decode known bits without sharing a mask between chip families.

    The 192x table contains only names established by the existing source;
    unknown raw bits remain visible through ``unknown_mask``.  The 1221 table
    is the complete bit-label mapping supplied in the State diagram.
    """

    if chip_type not in SUPPORTED_CHIP_TYPES:
        raise ProtocolError(f"unsupported chip type: {chip_type}")
    if raw < 0 or raw > MAX_U16:
        raise ProtocolError(f"status out of range: {raw}")
    bit_table = STATUS_BITS_192 if chip_type == CHIP_TYPE_192 else STATUS_BITS_1221
    known_mask = 0
    active: list[tuple[int, str]] = []
    for bit, label in bit_table:
        mask = 1 << bit
        known_mask |= mask
        if raw & mask:
            active.append((bit, label))
    return StatusDecode(
        chip_type=chip_type,
        raw=raw,
        active_bits=tuple(active),
        unknown_mask=raw & (MAX_U16 ^ known_mask),
    )


@dataclass(frozen=True)
class TargetIdentity:
    """A configured target key, including the slot used by the MCU cache."""

    slot: int
    chip_type: str
    address: int
    generation: int

    def validate(self) -> None:
        if self.slot < 0 or self.slot >= SLOT_COUNT:
            raise ProtocolError(f"slot out of range: {self.slot}")
        if self.chip_type not in SUPPORTED_CHIP_TYPES:
            raise ProtocolError(f"unsupported chip type: {self.chip_type}")
        if self.address not in ALLOWED_ADDRESSES_BY_TYPE[self.chip_type]:
            raise ProtocolError(
                f"unsupported {self.chip_type} address: 0x{self.address:02X}"
            )
        if self.generation <= 0 or self.generation > MAX_U16:
            raise ProtocolError(f"invalid generation: {self.generation}")

    @property
    def address_text(self) -> str:
        return f"{self.address:02X}"

    @property
    def generation_text(self) -> str:
        return f"{self.generation:04X}"


@dataclass(frozen=True)
class StatusTargetBinding:
    """A host cache identity including the physical port and board session."""

    port: str
    session_id: int
    identity: TargetIdentity

    def validate(self) -> None:
        if not str(self.port).strip():
            raise ProtocolError("status target port must not be empty")
        if not (1 <= self.session_id <= MAX_U32):
            raise ProtocolError("status target session must be non-zero uint32")
        self.identity.validate()


@dataclass(frozen=True)
class StatusRecord:
    """One fixed cache record from a ``STAT`` snapshot.

    An unconfigured slot has ``identity is None`` and is represented on the
    wire by ``U<slot>``.  Numeric fields then carry only neutral host values;
    they are not interpreted as a sample.
    """

    slot: int
    identity: Optional[TargetIdentity]
    flags: int = 0
    status: int = 0
    sample_seq: int = 0
    irq_seq: int = 0
    last_good_ms: int = 0
    age_ms: int = MAX_U32
    error: int = ERROR_NONE
    source: int = SOURCE_NONE

    @property
    def configured(self) -> bool:
        return self.identity is not None

    @property
    def valid(self) -> bool:
        return bool(self.flags & FLAG_VALID)

    @property
    def failed(self) -> bool:
        return bool(self.flags & FLAG_ERROR)

    @property
    def address_text(self) -> str:
        if self.identity is None:
            return "--"
        return self.identity.address_text

    @property
    def generation_text(self) -> str:
        if self.identity is None:
            return "----"
        return self.identity.generation_text

    @property
    def decoded_status(self) -> Optional[StatusDecode]:
        if self.identity is None:
            return None
        return decode_status(self.identity.chip_type, self.status)


@dataclass(frozen=True)
class Snapshot:
    """A complete, three-record ``STAT`` response."""

    irq_seq: int
    records: tuple[StatusRecord, ...]
    session_id: int | None = None

    def record(self, slot: int) -> StatusRecord:
        for record in self.records:
            if record.slot == slot:
                return record
        raise KeyError(slot)


@dataclass(frozen=True)
class TargetReply:
    """Parsed response to an explicit ``TGT`` request."""

    result: str
    identity: Optional[TargetIdentity] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class CapabilityReply:
    """Parsed ``CAP?`` response for formal FLAG1, FLAG2, or push firmware."""

    revision: str
    slots: int
    state_index: int
    monitor_explicit: bool
    stat_revision: str
    session_explicit: bool
    target_clear: bool
    d2_scan: bool = False
    push_event: bool = False

    @property
    def push_events(self) -> bool:
        """Compatibility spelling used by the push design handoff."""

        return self.push_event


@dataclass(frozen=True)
class MonitorReply:
    """Parsed response to an explicit RAM-only 1221 monitor toggle."""

    result: str
    slot: Optional[int] = None
    chip_type: Optional[str] = None
    enabled: Optional[bool] = None
    reason: Optional[str] = None


def _clean_frame(frame: str) -> str:
    if not isinstance(frame, str):
        raise FrameError("frame must be text")
    # Serial readers commonly retain line endings after the protocol terminator;
    # they are transport decoration, not part of the frame.
    cleaned = frame.strip("\r\n")
    if not cleaned.startswith("^") or not cleaned.endswith("~"):
        raise FrameError("frame must start with ^ and end with ~")
    if cleaned.count("^") != 1 or cleaned.count("~") != 1:
        raise FrameError("frame contains an extra delimiter")
    return cleaned[1:-1]


def _hex_field(value: str, width: int, field: str, *, allow_zero: bool = True) -> int:
    if len(value) != width or re.fullmatch(rf"[0-9A-F]{{{width}}}", value) is None:
        raise FrameError(f"{field} must be {width} uppercase hexadecimal digits")
    parsed = int(value, 16)
    if not allow_zero and parsed == 0:
        raise FrameError(f"{field} must be non-zero")
    return parsed


def _slot_text(value: str, field: str = "slot") -> int:
    if len(value) != 1 or value not in "012":
        raise FrameError(f"{field} must be 0, 1, or 2")
    return int(value)


def _validate_record(record: StatusRecord) -> None:
    if record.slot < 0 or record.slot >= SLOT_COUNT:
        raise FrameError(f"slot out of range: {record.slot}")
    if record.identity is None:
        return
    if record.identity.slot != record.slot:
        raise FrameError(f"identity slot mismatch for slot {record.slot}")
    try:
        record.identity.validate()
    except ProtocolError as exc:
        raise FrameError(str(exc)) from exc
    for name, value, maximum in (
        ("flags", record.flags, 0xFF),
        ("status", record.status, MAX_U16),
        ("sample_seq", record.sample_seq, MAX_U32),
        ("irq_seq", record.irq_seq, MAX_U32),
        ("last_good_ms", record.last_good_ms, MAX_U32),
        ("age_ms", record.age_ms, MAX_U32),
        ("error", record.error, 0xFF),
        ("source", record.source, 0xFF),
    ):
        if value < 0 or value > maximum:
            raise FrameError(f"{name} out of range")
    if not (record.flags & FLAG_CONFIGURED):
        raise FrameError(f"configured record {record.slot} lacks CONFIGURED flag")
    if (record.flags & FLAG_VALID) and (record.flags & FLAG_NO_SAMPLE):
        raise FrameError(f"record {record.slot} is both VALID and NO_SAMPLE")
    has_valid = bool(record.flags & FLAG_VALID)
    has_no_sample = bool(record.flags & FLAG_NO_SAMPLE)
    if not has_valid and not has_no_sample:
        raise FrameError(f"record {record.slot} is neither VALID nor NO_SAMPLE")
    # millis()==0 is a legal successful sample time.  NO_SAMPLE, rather than
    # the timestamp value, identifies the never-successful state.
    if has_no_sample and (record.last_good_ms != 0 or record.age_ms != MAX_U32):
        raise FrameError(f"record {record.slot} has invalid no-sample age")
    if record.error not in (ERROR_NONE, ERROR_I2C_READ):
        raise FrameError(f"unsupported error code: {record.error:02X}")
    valid_sources = (
        (SOURCE_NONE, SOURCE_IRQ_02, SOURCE_REARM_02, SOURCE_D2_SCAN)
        if record.identity.chip_type == CHIP_TYPE_192
        else (SOURCE_NONE, SOURCE_1221_STATE, SOURCE_D2_SCAN)
    )
    if record.source not in valid_sources:
        raise FrameError(
            f"source {record.source:02X} is invalid for {record.identity.chip_type}"
        )
    if record.source == SOURCE_D2_SCAN and record.irq_seq == 0:
        raise FrameError("D2 scan records must carry a non-zero irq_seq")
    if record.identity.chip_type == CHIP_TYPE_1221 and record.irq_seq != 0 and record.source != SOURCE_D2_SCAN:
        raise FrameError("1221 State records must not fabricate irq_seq")
    if record.error == ERROR_NONE and (record.flags & FLAG_ERROR):
        raise FrameError(f"record {record.slot} flags ERROR without error code")
    if record.error != ERROR_NONE and not (record.flags & FLAG_ERROR):
        raise FrameError(f"record {record.slot} error code without ERROR flag")
    # The trial firmware starts its attempt counter at one.  Zero is reserved
    # for a configured slot that has never attempted a read.
    if record.sample_seq == 0 and not (
        has_no_sample
        and record.source == SOURCE_NONE
        and record.error == ERROR_NONE
        and not (record.flags & FLAG_ERROR)
    ):
        raise FrameError("sample_seq=00000000 is only valid for an unattempted NO_SAMPLE record")


def _parse_record(token: str) -> StatusRecord:
    if re.fullmatch(r"U[0-2]", token):
        return StatusRecord(slot=int(token[1]), identity=None)

    fields = token.split(",")
    if len(fields) != 12 or re.fullmatch(r"D[0-2]", fields[0]) is None:
        raise FrameError("malformed STAT record")
    slot = _slot_text(fields[0][1:])
    chip_type = fields[1]
    if chip_type not in SUPPORTED_CHIP_TYPES:
        raise FrameError(f"unsupported STAT chip type: {chip_type}")
    address = _hex_field(fields[2], 2, "address")
    if address not in ALLOWED_ADDRESSES_BY_TYPE[chip_type]:
        raise FrameError(f"unsupported {chip_type} STAT address: {fields[2]}")
    generation = _hex_field(fields[3], 4, "generation", allow_zero=False)
    identity = TargetIdentity(slot, chip_type, address, generation)
    record = StatusRecord(
        slot=slot,
        identity=identity,
        flags=_hex_field(fields[4], 2, "flags"),
        status=_hex_field(fields[5], 4, "status"),
        sample_seq=_hex_field(fields[6], 8, "sample_seq"),
        irq_seq=_hex_field(fields[7], 8, "irq_seq"),
        last_good_ms=_hex_field(fields[8], 8, "last_good_ms"),
        age_ms=_hex_field(fields[9], 8, "age_ms"),
        error=_hex_field(fields[10], 2, "error"),
        source=_hex_field(fields[11], 2, "source"),
    )
    _validate_record(record)
    return record


def parse_stat_frame(frame: str) -> Snapshot:
    """Parse and validate one complete ``STAT`` frame.

    Records may arrive in any textual order.  The returned tuple is sorted by
    the embedded slot, so callers never associate a value by response order.
    Duplicate slots, missing slots, and duplicate target addresses are errors.
    """

    body = _clean_frame(frame)
    parts = body.split()
    if not parts:
        raise FrameError("STAT frame is empty")
    if parts[0] != "STAT" or len(parts) != 6:
        raise FrameError("STAT must contain session, irq_seq, and exactly three records")
    session_id = _hex_field(parts[1], 8, "snapshot session", allow_zero=False)
    irq_seq = _hex_field(parts[2], 8, "snapshot irq_seq")
    records = tuple(_parse_record(token) for token in parts[3:])
    slots = [record.slot for record in records]
    if len(set(slots)) != SLOT_COUNT:
        raise FrameError("duplicate STAT slot")
    if set(slots) != set(range(SLOT_COUNT)):
        raise FrameError("missing STAT slot")
    addresses = [
        record.identity.address
        for record in records
        if record.identity is not None
    ]
    if len(addresses) != len(set(addresses)):
        raise FrameError("duplicate configured STAT address")
    ordered = tuple(sorted(records, key=lambda record: record.slot))
    return Snapshot(irq_seq=irq_seq, records=ordered, session_id=session_id)


def parse_evstat_frame(frame: str) -> Snapshot:
    """Parse one self-contained ``EVSTAT`` D2 push event.

    ``EVSTAT`` deliberately reuses the validated ``STAT`` record grammar;
    only the event marker and push capability revision differ.  A configured
    monitor-enabled record must be a completed D2 result (source 04 and a
    valid/error outcome) for the event sequence.  A configured monitor-off
    record may remain ``NO_SAMPLE`` with source ``NONE``; any record carrying
    source 04 still has to carry the event sequence.  Returning the existing
    :class:`Snapshot` keeps the identity, raw status, error, and source
    handling identical for manual and pushed snapshots.
    """

    body = _clean_frame(frame)
    parts = body.split()
    if len(parts) != 7 or parts[:2] != ["EVSTAT", "FLAG3"]:
        raise FrameError("EVSTAT must contain FLAG3, session, irq_seq, and exactly three records")
    snapshot = parse_stat_frame("^STAT " + " ".join(parts[2:]) + "~")
    if snapshot.irq_seq == 0:
        raise FrameError("EVSTAT irq_seq must be non-zero")
    for record in snapshot.records:
        if record.identity is None:
            continue
        monitor_enabled = bool(record.flags & FLAG_MONITOR_ENABLED)
        if record.source == SOURCE_D2_SCAN and record.irq_seq != snapshot.irq_seq:
            raise FrameError("EVSTAT D2 record irq_seq must match event irq_seq")
        if monitor_enabled:
            if record.source != SOURCE_D2_SCAN:
                raise FrameError("EVSTAT enabled records must use D2_SCAN source")
            if not (record.valid or record.failed):
                raise FrameError("EVSTAT enabled records must contain VALID or ERROR")
    return snapshot


def parse_tgt_frame(frame: str) -> TargetReply:
    """Parse an explicit ``TGT`` result without applying it to host state."""

    body = _clean_frame(frame)
    parts = body.split()
    if len(parts) == 3 and parts[:2] == ["ERR", "TGT"]:
        reason = parts[2]
        if re.fullmatch(r"[A-Z_]+", reason) is None:
            raise FrameError("malformed TGT error reason")
        return TargetReply(result="ERR", reason=reason)
    if len(parts) != 6 or parts[0] != "TGT" or parts[1] not in ("OK", "SAME"):
        raise FrameError("malformed TGT response")
    slot = _slot_text(parts[2])
    if parts[3] not in SUPPORTED_CHIP_TYPES:
        raise FrameError(f"unsupported TGT chip type: {parts[3]}")
    chip_type = parts[3]
    address = _hex_field(parts[4], 2, "address")
    if address not in ALLOWED_ADDRESSES_BY_TYPE[chip_type]:
        raise FrameError(f"unsupported {chip_type} TGT address: {parts[4]}")
    generation = _hex_field(parts[5], 4, "generation", allow_zero=False)
    identity = TargetIdentity(slot, chip_type, address, generation)
    return TargetReply(result=parts[1], identity=identity)


def parse_cap_frame(frame: str) -> CapabilityReply:
    """Parse ``^CAP FLAG3~``. FLAG3 in the frame enables EVSTAT push."""

    body = _clean_frame(frame)
    parts = body.split()
    if not parts or parts[0] != "CAP" or "FLAG3" not in parts:
        raise FrameError("unsupported or malformed FLAG status capability response")
    return CapabilityReply(
        revision="FLAG3",
        slots=3,
        state_index=0x03,
        monitor_explicit=True,
        stat_revision="FLAG3",
        session_explicit=True,
        target_clear=True,
        d2_scan=True,
        push_event=True,
    )


def parse_mon_frame(frame: str) -> MonitorReply:
    """Parse ``MON OK`` or an ``ERR MON`` response without sending anything."""

    body = _clean_frame(frame)
    parts = body.split()
    if len(parts) == 3 and parts[:2] == ["ERR", "MON"]:
        reason = parts[2]
        if re.fullmatch(r"[A-Z_]+", reason) is None:
            raise FrameError("malformed MON error reason")
        return MonitorReply(result="ERR", reason=reason)
    if len(parts) != 5 or parts[:2] != ["MON", "OK"]:
        raise FrameError("malformed MON response")
    slot = _slot_text(parts[2])
    if parts[3] not in SUPPORTED_CHIP_TYPES or parts[4] not in ("ON", "OFF"):
        raise FrameError("MON response must identify a supported type and ON/OFF")
    return MonitorReply(
        result="OK",
        slot=slot,
        chip_type=parts[3],
        enabled=parts[4] == "ON",
    )


@dataclass(frozen=True)
class SessionReply:
    result: str
    session_id: int | None = None
    reason: str | None = None


def parse_session_frame(frame: str) -> SessionReply:
    """Parse a formal ``SESSION OK/SAME`` reply."""

    body = _clean_frame(frame)
    parts = body.split()
    if len(parts) == 3 and parts[:2] == ["ERR", "SESSION"]:
        if re.fullmatch(r"[A-Z_]+", parts[2]) is None:
            raise FrameError("malformed SESSION error reason")
        return SessionReply("ERR", reason=parts[2])
    if len(parts) != 3 or parts[0] != "SESSION" or parts[1] not in ("OK", "SAME"):
        raise FrameError("malformed SESSION response")
    session_id = _hex_field(parts[2], 8, "session", allow_zero=False)
    return SessionReply(parts[1], session_id=session_id)


@dataclass(frozen=True)
class TargetClearReply:
    result: str
    slot: int | None = None
    generation: int | None = None
    reason: str | None = None


def parse_tgt_clear_frame(frame: str) -> TargetClearReply:
    """Parse a formal ``TGTCLR`` reply without changing host state."""

    body = _clean_frame(frame)
    parts = body.split()
    if len(parts) == 3 and parts[:2] == ["ERR", "TGTCLR"]:
        if re.fullmatch(r"[A-Z_]+", parts[2]) is None:
            raise FrameError("malformed TGTCLR error reason")
        return TargetClearReply("ERR", reason=parts[2])
    if len(parts) != 4 or parts[:2] != ["TGTCLR", "OK"]:
        raise FrameError("malformed TGTCLR response")
    slot = _slot_text(parts[2])
    generation = _hex_field(parts[3], 4, "generation", allow_zero=False)
    return TargetClearReply("OK", slot=slot, generation=generation)


def build_cap_query() -> str:
    """Return the read-only formal capability request."""

    return "^CAP?~"


def build_capability_body() -> str:
    return "CAP?"


def build_monitor_command(slot: int, enabled: bool) -> str:
    """Format an inert, explicit status-monitor toggle."""

    if slot < 0 or slot >= SLOT_COUNT:
        raise ProtocolError("slot must be 0, 1, or 2")
    return f"^MON {slot} {'ON' if enabled else 'OFF'}~"


def build_monitor_body(slot: int, enabled: bool) -> str:
    return f"MON {slot} {'ON' if enabled else 'OFF'}"


def build_stat_query() -> str:
    """Return the read-only snapshot request; this function performs no I/O."""

    return "^STAT?~"


def build_stat_body() -> str:
    return "STAT?"


def build_session_command(session_id: int) -> str:
    if not (1 <= int(session_id) <= MAX_U32):
        raise ProtocolError("session must be a non-zero uint32")
    return f"^SESSION {int(session_id):08X}~"


def build_session_body(session_id: int) -> str:
    return build_session_command(session_id)[1:-1]


def build_tgt_clear_command(slot: int, generation: int) -> str:
    if slot < 0 or slot >= SLOT_COUNT:
        raise ProtocolError("slot must be 0, 1, or 2")
    if not (1 <= int(generation) <= MAX_U16):
        raise ProtocolError("generation must be a non-zero uint16")
    return f"^TGTCLR {slot} {int(generation):04X}~"


def build_tgt_clear_body(slot: int, generation: int) -> str:
    return build_tgt_clear_command(slot, generation)[1:-1]


def build_tgt_body(
    slot: int | str,
    address: int,
    generation: int,
    *,
    chip_type: str = CHIP_TYPE,
) -> str:
    return build_tgt_command(slot, address, generation, chip_type=chip_type)[1:-1]


def frame_body(frame: str) -> str:
    """Return a parser-ready frame from either a body or ``^...~`` text."""

    if not isinstance(frame, str):
        raise FrameError("frame must be text")
    text = frame.strip()
    if text.startswith("^") and text.endswith("~"):
        return text
    if text:
        return f"^{text}~"
    raise FrameError("frame must not be empty")


def build_tgt_command(
    slot: int | str,
    address: int,
    generation: int,
    *,
    chip_type: str = CHIP_TYPE,
) -> str:
    """Format an explicit RAM-only target configuration request.

    The returned string is inert.  The host CLI never sends it automatically;
    callers must deliberately choose a transport in a later hardware step.
    """

    if not isinstance(slot, int) or slot < 0 or slot >= SLOT_COUNT:
        raise ProtocolError("slot must be 0, 1, or 2")
    identity = TargetIdentity(slot, chip_type, address, generation)
    identity.validate()
    return f"^TGT {slot} {chip_type} {address:02X} {generation:04X}~"


def format_record(record: StatusRecord) -> str:
    """Format a validated record for fixtures and the offline demonstration."""

    if record.identity is None:
        return f"U{record.slot}"
    _validate_record(record)
    identity = record.identity
    return (
        f"D{record.slot},{identity.chip_type},{identity.address:02X},"
        f"{identity.generation:04X},{record.flags:02X},{record.status:04X},"
        f"{record.sample_seq:08X},{record.irq_seq:08X},"
        f"{record.last_good_ms:08X},{record.age_ms:08X},"
        f"{record.error:02X},{record.source:02X}"
    )


def format_stat_frame(snapshot: Snapshot, *, preserve_order: bool = True) -> str:
    """Format one complete snapshot frame from validated records."""

    if len(snapshot.records) != SLOT_COUNT:
        raise ProtocolError("a STAT frame must contain three records")
    if snapshot.irq_seq < 0 or snapshot.irq_seq > MAX_U32:
        raise ProtocolError("snapshot irq_seq is outside uint32 range")
    for record in snapshot.records:
        _validate_record(record)
    slots = [record.slot for record in snapshot.records]
    if len(set(slots)) != SLOT_COUNT or set(slots) != set(range(SLOT_COUNT)):
        raise ProtocolError("snapshot records must contain slots 0, 1, and 2")
    addresses = [
        record.identity.address
        for record in snapshot.records
        if record.identity is not None
    ]
    if len(addresses) != len(set(addresses)):
        raise ProtocolError("snapshot addresses must be unique")
    records = snapshot.records if preserve_order else tuple(sorted(snapshot.records, key=lambda r: r.slot))
    if snapshot.session_id is None or not (1 <= snapshot.session_id <= MAX_U32):
        raise ProtocolError("snapshot session must be a non-zero uint32")
    return f"^STAT {snapshot.session_id:08X} {snapshot.irq_seq:08X} {' '.join(format_record(r) for r in records)}~"


def make_identity(
    slot: int,
    address: int,
    generation: int = 1,
    *,
    chip_type: str = CHIP_TYPE_192,
) -> TargetIdentity:
    """Small fixture helper shared by the offline tests and demonstration."""

    identity = TargetIdentity(slot, chip_type, address, generation)
    identity.validate()
    return identity


def make_record(
    identity: TargetIdentity,
    *,
    flags: int,
    status: int,
    sample_seq: int,
    irq_seq: int,
    last_good_ms: int,
    age_ms: int,
    error: int = ERROR_NONE,
    source: int = SOURCE_IRQ_02,
) -> StatusRecord:
    """Create a validated configured record for offline fixtures."""

    record = StatusRecord(
        slot=identity.slot,
        identity=identity,
        flags=flags,
        status=status,
        sample_seq=sample_seq,
        irq_seq=irq_seq,
        last_good_ms=last_good_ms,
        age_ms=age_ms,
        error=error,
        source=source,
    )
    _validate_record(record)
    return record


def make_unconfigured(slot: int) -> StatusRecord:
    if slot < 0 or slot >= SLOT_COUNT:
        raise ProtocolError("slot out of range")
    return StatusRecord(slot=slot, identity=None)


HALF_RANGE = 0x80000000


def is_newer_u32(candidate: int, previous: int) -> bool:
    """Return whether *candidate* is newer in a wrapping uint32 sequence."""

    if not (0 <= candidate <= MAX_U32 and 0 <= previous <= MAX_U32):
        raise StateError("sequence value is outside uint32 range")
    distance = (candidate - previous) & MAX_U32
    return 0 < distance < HALF_RANGE


def _validate_targets(targets: Iterable[TargetIdentity]) -> dict[int, TargetIdentity]:
    target_map: dict[int, TargetIdentity] = {}
    addresses: set[int] = set()
    for target in targets:
        target.validate()
        if target.slot in target_map:
            raise StateError(f"duplicate configured slot: {target.slot}")
        if target.address in addresses:
            raise StateError(f"duplicate configured address: 0x{target.address:02X}")
        target_map[target.slot] = target
        addresses.add(target.address)
    return target_map


@dataclass(frozen=True)
class ApplyResult:
    """Per-snapshot result; ignored records never overwrite accepted state."""

    accepted_slots: tuple[int, ...]
    ignored_slots: tuple[int, ...]
    snapshot_irq_seq: int


@dataclass(frozen=True)
class MonitorControlState:
    """Host-confirmed MON control, kept separate from sampling fields."""

    identity: TargetIdentity
    enabled: bool
    confirmed: bool


@dataclass(frozen=True)
class MonitorRequest:
    """Local association for one serialized MON command and reply."""

    request_id: int
    identity: TargetIdentity
    enabled: bool


class SnapshotState:
    """Apply fixed snapshots against an explicit per-slot target table."""

    def __init__(
        self,
        targets: Iterable[TargetIdentity] = (),
        *,
        session_id: int | None = None,
    ) -> None:
        self._targets: dict[int, TargetIdentity] = {}
        self._records: dict[int, StatusRecord] = {}
        self._last_snapshot_irq_seq: int | None = None
        self._monitor_states: dict[int, MonitorControlState] = {}
        self._pending_monitor: dict[int, MonitorRequest] = {}
        self._next_monitor_request_id = 1
        self.session_id: int | None = None
        self.set_session(session_id)
        self.set_targets(targets)

    @property
    def targets(self) -> dict[int, TargetIdentity]:
        return dict(self._targets)

    @property
    def records(self) -> dict[int, StatusRecord]:
        return dict(self._records)

    @property
    def last_snapshot_irq_seq(self) -> int | None:
        return self._last_snapshot_irq_seq

    def target(self, slot: int) -> TargetIdentity | None:
        return self._targets.get(slot)

    def record(self, slot: int) -> StatusRecord | None:
        return self._records.get(slot)

    def monitor_state(self, slot: int) -> MonitorControlState | None:
        state = self._monitor_states.get(slot)
        if state is None or state.identity != self._targets.get(slot):
            return None
        return state

    def monitor_enabled(self, slot: int) -> bool | None:
        state = self.monitor_state(slot)
        return None if state is None else state.enabled

    def begin_monitor_request(self, slot: int, enabled: bool) -> MonitorRequest:
        identity = self._targets.get(slot)
        if identity is None:
            raise StateError(f"cannot monitor unconfigured slot {slot}")
        request = MonitorRequest(self._next_monitor_request_id, identity, bool(enabled))
        self._next_monitor_request_id += 1
        self._pending_monitor[slot] = request
        return request

    def apply_monitor_reply(self, reply: MonitorReply, request: MonitorRequest) -> bool:
        """Apply only the current target's latest explicit MON confirmation."""

        pending = self._pending_monitor.get(request.identity.slot)
        if pending != request or self._targets.get(request.identity.slot) != request.identity:
            return False
        self._pending_monitor.pop(request.identity.slot, None)
        if reply.result != "OK":
            return False
        if (
            reply.slot != request.identity.slot
            or reply.chip_type != request.identity.chip_type
            or reply.enabled is None
            or reply.enabled != request.enabled
        ):
            raise StateError("MON reply does not match its request identity or state")
        self._monitor_states[request.identity.slot] = MonitorControlState(
            request.identity, request.enabled, True
        )
        if not request.enabled:
            self._invalidate_sample_after_monitor_off(request.identity.slot, request.identity)
        return True

    def _invalidate_sample_after_monitor_off(self, slot: int, identity: TargetIdentity) -> None:
        """Mirror firmware MON OFF clearing while retaining the sequence fence."""

        previous = self._records.get(slot)
        if previous is None:
            return
        flags = FLAG_CONFIGURED | FLAG_NO_SAMPLE
        if previous.flags & FLAG_SEQ_WRAP:
            flags |= FLAG_SEQ_WRAP
        self._records[slot] = StatusRecord(
            slot=slot,
            identity=identity,
            flags=flags,
            status=0,
            sample_seq=previous.sample_seq,
            irq_seq=previous.irq_seq,
            last_good_ms=0,
            age_ms=MAX_U32,
            error=ERROR_NONE,
            source=SOURCE_NONE,
        )

    def set_targets(self, targets: Iterable[TargetIdentity]) -> None:
        new_targets = _validate_targets(targets)
        for slot in range(SLOT_COUNT):
            old = self._targets.get(slot)
            new = new_targets.get(slot)
            if old != new:
                self._records.pop(slot, None)
                self._monitor_states.pop(slot, None)
                self._pending_monitor.pop(slot, None)
        self._targets = new_targets

    def set_session(self, session_id: int | None) -> None:
        if session_id is not None and not (1 <= int(session_id) <= MAX_U32):
            raise StateError("session must be a non-zero uint32")
        if self.session_id == session_id:
            return
        self.session_id = None if session_id is None else int(session_id)
        self._records.clear()
        self._monitor_states.clear()
        self._pending_monitor.clear()
        self._last_snapshot_irq_seq = None

    def configure_target(
        self,
        slot: int,
        address: int,
        generation: int,
        *,
        chip_type: str = CHIP_TYPE_192,
    ) -> str:
        candidate = TargetIdentity(slot, chip_type, address, generation)
        candidate.validate()
        other = next(
            (target for target in self._targets.values() if target.address == address and target.slot != slot),
            None,
        )
        if other is not None:
            raise StateError(f"duplicate configured address: 0x{address:02X}")
        current = self._targets.get(slot)
        if current == candidate:
            return "SAME"
        if current is not None:
            if generation < current.generation:
                raise StateError(
                    f"generation moved backwards for slot {slot}: "
                    f"{generation:04X} < {current.generation:04X}"
                )
            if generation == current.generation:
                raise StateError("generation reused for a changed target")
        self._records.pop(slot, None)
        self._monitor_states.pop(slot, None)
        self._pending_monitor.pop(slot, None)
        self._targets[slot] = candidate
        return "OK"

    def remove_target(self, slot: int) -> None:
        if slot < 0 or slot >= SLOT_COUNT:
            raise StateError(f"slot out of range: {slot}")
        self._targets.pop(slot, None)
        self._records.pop(slot, None)
        self._monitor_states.pop(slot, None)
        self._pending_monitor.pop(slot, None)

    def _validate_identity_view(self, snapshot: Snapshot) -> None:
        if self.session_id is not None and snapshot.session_id != self.session_id:
            raise StateError(
                f"stale status session: expected {self.session_id:08X}, "
                f"got {snapshot.session_id if snapshot.session_id is not None else 'NONE'}"
            )
        if len(snapshot.records) != SLOT_COUNT:
            raise StateError("snapshot must contain three records")
        by_slot = {record.slot: record for record in snapshot.records}
        if set(by_slot) != set(range(SLOT_COUNT)):
            raise StateError("snapshot slots are incomplete or duplicated")
        for slot in range(SLOT_COUNT):
            expected = self._targets.get(slot)
            record = by_slot[slot]
            if expected is None:
                if record.configured:
                    raise StateError(f"unexpected configured target in slot {slot}")
            elif record.identity != expected:
                observed = record.identity
                if observed is None:
                    raise StateError(f"configured target {slot} disappeared from snapshot")
                raise StateError(
                    f"stale target identity in slot {slot}: expected "
                    f"{expected.chip_type}@{expected.address:02X}/{expected.generation:04X}, "
                    f"got {observed.chip_type}@{observed.address:02X}/{observed.generation:04X}"
                )

    def apply_snapshot(self, snapshot: Snapshot) -> ApplyResult:
        """Accept newer records and safe same-sequence age/stale progress."""

        self._validate_identity_view(snapshot)
        accepted: list[int] = []
        ignored: list[int] = []
        for record in sorted(snapshot.records, key=lambda item: item.slot):
            if not record.configured:
                ignored.append(record.slot)
                continue
            previous = self._records.get(record.slot)
            if previous is None or is_newer_u32(record.sample_seq, previous.sample_seq):
                self._records[record.slot] = record
                self._observe_monitor_flag(record)
                accepted.append(record.slot)
            elif record.sample_seq == previous.sample_seq and self._same_sample_age_update(previous, record):
                self._records[record.slot] = record
                self._observe_monitor_flag(record)
                accepted.append(record.slot)
            else:
                ignored.append(record.slot)
        if self._last_snapshot_irq_seq is None or is_newer_u32(snapshot.irq_seq, self._last_snapshot_irq_seq):
            self._last_snapshot_irq_seq = snapshot.irq_seq
        return ApplyResult(tuple(accepted), tuple(ignored), snapshot.irq_seq)

    def _observe_monitor_flag(self, record: StatusRecord) -> None:
        current = self._monitor_states.get(record.slot)
        if current is not None and current.confirmed:
            return
        self._monitor_states[record.slot] = MonitorControlState(
            record.identity,
            bool(record.flags & FLAG_MONITOR_ENABLED),
            False,
        )

    @staticmethod
    def _same_sample_age_update(previous: StatusRecord, candidate: StatusRecord) -> bool:
        immutable_flags = 0xFF ^ (FLAG_STALE | FLAG_MONITOR_ENABLED)
        same_sample = (
            previous.identity == candidate.identity
            and previous.status == candidate.status
            and previous.last_good_ms == candidate.last_good_ms
            and previous.error == candidate.error
            and previous.source == candidate.source
            and previous.irq_seq == candidate.irq_seq
            and (previous.flags & immutable_flags) == (candidate.flags & immutable_flags)
        )
        if not same_sample or candidate.age_ms < previous.age_ms:
            return False
        if previous.flags & FLAG_STALE and not candidate.flags & FLAG_STALE:
            return False
        return candidate.age_ms > previous.age_ms or (
            not (previous.flags & FLAG_STALE) and bool(candidate.flags & FLAG_STALE)
        )


__all__ = [
    "ALLOWED_ADDRESSES_BY_TYPE",
    "ApplyResult",
    "CapabilityReply",
    "CHIP_TYPE_1221",
    "CHIP_TYPE_192",
    "ERROR_I2C_READ",
    "FLAG_CONFIGURED",
    "FLAG_ERROR",
    "FLAG_MONITOR_ENABLED",
    "FLAG_NO_SAMPLE",
    "FLAG_SEQ_WRAP",
    "FLAG_STALE",
    "FLAG_VALID",
    "SOURCE_D2_SCAN",
    "FrameError",
    "MonitorControlState",
    "MonitorReply",
    "MonitorRequest",
    "ProtocolError",
    "SessionReply",
    "SnapshotState",
    "Snapshot",
    "StateError",
    "StatusDecode",
    "StatusRecord",
    "StatusTargetBinding",
    "TargetIdentity",
    "TargetClearReply",
    "TargetReply",
    "build_capability_body",
    "build_cap_query",
    "build_monitor_body",
    "build_monitor_command",
    "build_session_body",
    "build_session_command",
    "build_stat_body",
    "build_stat_query",
    "build_tgt_body",
    "build_tgt_clear_body",
    "build_tgt_command",
    "decode_status",
    "frame_body",
    "format_stat_frame",
    "is_newer_u32",
    "make_identity",
    "make_record",
    "make_unconfigured",
    "parse_cap_frame",
    "parse_mon_frame",
    "parse_session_frame",
    "parse_stat_frame",
    "parse_tgt_frame",
    "parse_tgt_clear_frame",
]

