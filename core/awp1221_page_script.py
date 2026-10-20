"""AWP1221 button-page settings as a replayable register script.

Export builds the same kind of TXT 192x button mode writes: current page
settings, no chip I/O. Import parses that TXT and executes the operations.
A metadata comment restores the page widgets the raw registers do not name.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.base_tool import RegisterScriptOperation


AWP1221_PAGE_SCRIPT_TAG = "AWP1221_PAGE"
AWP1221_PAGE_SCRIPT_VERSION = 2

AWP1221_SCRIPT_SETTINGS_REGISTER = 0x05
AWP1221_SCRIPT_SRAM_ADDRESS_REGISTER = 0x15
AWP1221_SCRIPT_SRAM_DATA_REGISTER = 0x00
AWP1221_SCRIPT_PLAY_WAVE_REGISTER = 0x0C
AWP1221_SCRIPT_INTERFACE_DEFAULT = 0x442E
AWP1221_SCRIPT_SRAM_MODE_MASK = 0x0003
AWP1221_SCRIPT_SRAM_WRITE_MODE = 0x0003
AWP1221_SCRIPT_SRAM_PLAY_MODE = 0x0002
AWP1221_SCRIPT_WORDS_PER_SLICE = 4

_WAVEFORM_TOKENS = {
    "SINE": "SINE",
    "Gauss": "Gauss",
    "Fixed-slope Ramp": "Ramp",
    "DC": "DC",
    "SINC": "SINC",
}
_WAVEFORM_NAMES = {token: name for name, token in _WAVEFORM_TOKENS.items()}
_SERIALS = ("serial1", "serial2", "serial3", "serial4")
_BASE_METADATA_KEYS = (
    "v",
    "kind",
    "auto",
    "serial",
    "depth",
    "window",
    "vmax",
    "vmin",
    "t0",
    "t1",
    "t2",
    "t3",
    "sinrep",
    "slice",
    "source",
    "wave",
    "fine",
    "coarse",
    "offset",
    "half",
    "phase",
    "point",
    "romrep",
    "infinite",
    "s0mode",
    "s0ovv",
    "s0jump",
    "s0th",
    "s0deb",
    "s0prec",
    "s0wave",
    "s0rep",
    "s1mode",
    "s1ovv",
    "s1jump",
    "s1th",
    "s1deb",
    "s1prec",
    "s1wave",
    "s1rep",
    "debms",
    "sample",
    "zero",
    "pret",
    "rret",
    "zps",
    "zpsd",
    "sleep",
    "ldo",
)
_V2_METADATA_KEYS = (*_BASE_METADATA_KEYS, "x")


@dataclass(frozen=True)
class Awp1221StageScript:
    mode: str
    ovv_code: int
    jump: int
    threshold: str
    debounce: bool
    use_high_12_bits: bool
    wave: int
    repeat: int


@dataclass(frozen=True)
class Awp1221PageScript:
    kind: str
    auto_serial: bool
    serial: str
    depth: str
    window_index: int
    max_voltage: str
    min_voltage: str
    times_us: tuple[str, str, str, str]
    sin_repeat: str
    slice_index: int
    source: str
    waveform: str
    fine: int
    coarse: int
    offset: int
    half: int
    phase: int
    point_time: str
    rom_repeat: str
    infinite: bool
    stages: tuple[Awp1221StageScript, Awp1221StageScript]
    debounce_ms: str
    sample_index: int
    zero_index: int
    press_return_index: int
    release_return_index: int
    zps_enabled: bool
    zps_detect: bool
    sleep_sensitivity: bool
    ldo_index: int
    advanced_state: str = ""


def awp1221_sram_mode_value(mode: int) -> int:
    return (AWP1221_SCRIPT_INTERFACE_DEFAULT & ~AWP1221_SCRIPT_SRAM_MODE_MASK) | (int(mode) & AWP1221_SCRIPT_SRAM_MODE_MASK)


def build_awp1221_page_script_operations(
    device_addr: int,
    snapshot: Awp1221PageScript,
    *,
    sram_slices: tuple[tuple[int, tuple[int, ...]], ...],
    stage_words: tuple[dict[int, int], ...],
    settings_words: dict[int, int],
    play_slice: int,
) -> list[RegisterScriptOperation]:
    if not sram_slices:
        raise ValueError("page settings have no waveform words")
    address = int(device_addr) & 0xFF
    operations = [
        RegisterScriptOperation(
            address,
            "WRITE",
            AWP1221_SCRIPT_SETTINGS_REGISTER,
            awp1221_sram_mode_value(AWP1221_SCRIPT_SRAM_WRITE_MODE),
            f"Enter waveform SRAM write mode; {_format_page_metadata(snapshot)}",
        )
    ]
    for slice_index, words in sram_slices:
        slice_index = int(slice_index)
        if not 0 <= slice_index <= 31:
            raise ValueError("waveform slice must be in range 0..31")
        if len(words) != AWP1221_SCRIPT_WORDS_PER_SLICE:
            raise ValueError("each waveform slice must contain 4 words")
        operations.append(
            RegisterScriptOperation(
                address,
                "WRITE",
                AWP1221_SCRIPT_SRAM_ADDRESS_REGISTER,
                (slice_index * AWP1221_SCRIPT_WORDS_PER_SLICE) & 0x03FF,
                f"Set waveform SRAM address for slice{slice_index}",
            )
        )
        operations.extend(
            RegisterScriptOperation(
                address,
                "WRITE",
                AWP1221_SCRIPT_SRAM_DATA_REGISTER,
                int(word) & 0xFFFF,
                "Write waveform SRAM word",
            )
            for word in words
        )
    play = int(play_slice) & 0x1F
    operations.extend(
        (
            RegisterScriptOperation(
                address,
                "WRITE",
                AWP1221_SCRIPT_SETTINGS_REGISTER,
                awp1221_sram_mode_value(AWP1221_SCRIPT_SRAM_PLAY_MODE),
                "Restore waveform play mode",
            ),
            RegisterScriptOperation(
                address,
                "WRITE",
                AWP1221_SCRIPT_PLAY_WAVE_REGISTER,
                play | (play << 8),
                "Select playback slice",
            ),
        )
    )
    for stage_index, words in enumerate(stage_words):
        for register, value in words.items():
            operations.append(
                RegisterScriptOperation(
                    address,
                    "WRITE",
                    int(register) & 0xFF,
                    int(value) & 0xFFFF,
                    f"Set button stage{stage_index} register",
                )
            )
    for register, value in settings_words.items():
        operations.append(
            RegisterScriptOperation(
                address,
                "WRITE",
                int(register) & 0xFF,
                int(value) & 0xFFFF,
                "Set ADC page settings",
            )
        )
    if extract_awp1221_page_script(operations) != snapshot:
        raise ValueError("page settings cannot be represented in the register script")
    return operations


def extract_awp1221_page_script(
    operations: list[RegisterScriptOperation],
) -> Awp1221PageScript | None:
    for operation in operations:
        parsed = _parse_page_metadata(operation.comment)
        if parsed is not None:
            return parsed
    return None


def _format_page_metadata(snapshot: Awp1221PageScript) -> str:
    stage0, stage1 = snapshot.stages
    if snapshot.kind not in {"sinc", "rom"} or (snapshot.kind == "sinc") != (snapshot.waveform == "SINC"):
        raise ValueError("SINC page settings must use the SINC waveform")
    try:
        waveform_token = _WAVEFORM_TOKENS[snapshot.waveform]
    except KeyError as exc:
        raise ValueError(f"Unsupported waveform: {snapshot.waveform}") from exc
    fields = {
        "v": AWP1221_PAGE_SCRIPT_VERSION,
        "kind": snapshot.kind,
        "auto": int(snapshot.auto_serial),
        "serial": snapshot.serial,
        "depth": snapshot.depth,
        "window": snapshot.window_index,
        "vmax": snapshot.max_voltage,
        "vmin": snapshot.min_voltage,
        "t0": snapshot.times_us[0],
        "t1": snapshot.times_us[1],
        "t2": snapshot.times_us[2],
        "t3": snapshot.times_us[3],
        "sinrep": snapshot.sin_repeat,
        "slice": snapshot.slice_index,
        "source": snapshot.source,
        "wave": waveform_token,
        "fine": snapshot.fine,
        "coarse": snapshot.coarse,
        "offset": snapshot.offset,
        "half": snapshot.half,
        "phase": snapshot.phase,
        "point": snapshot.point_time,
        "romrep": snapshot.rom_repeat,
        "infinite": int(snapshot.infinite),
        "s0mode": stage0.mode,
        "s0ovv": stage0.ovv_code,
        "s0jump": stage0.jump,
        "s0th": stage0.threshold,
        "s0deb": int(stage0.debounce),
        "s0prec": int(stage0.use_high_12_bits),
        "s0wave": stage0.wave,
        "s0rep": stage0.repeat,
        "s1mode": stage1.mode,
        "s1ovv": stage1.ovv_code,
        "s1jump": stage1.jump,
        "s1th": stage1.threshold,
        "s1deb": int(stage1.debounce),
        "s1prec": int(stage1.use_high_12_bits),
        "s1wave": stage1.wave,
        "s1rep": stage1.repeat,
        "debms": snapshot.debounce_ms,
        "sample": snapshot.sample_index,
        "zero": snapshot.zero_index,
        "pret": snapshot.press_return_index,
        "rret": snapshot.release_return_index,
        "zps": int(snapshot.zps_enabled),
        "zpsd": int(snapshot.zps_detect),
        "sleep": int(snapshot.sleep_sensitivity),
        "ldo": snapshot.ldo_index,
        "x": snapshot.advanced_state or "-",
    }
    return AWP1221_PAGE_SCRIPT_TAG + " " + " ".join(
        f"{key}={_script_token(fields[key])}" for key in _V2_METADATA_KEYS
    )


def _parse_page_metadata(comment: str) -> Awp1221PageScript | None:
    marker = comment.find(AWP1221_PAGE_SCRIPT_TAG)
    if marker < 0:
        return None
    fields: dict[str, str] = {}
    for token in comment[marker + len(AWP1221_PAGE_SCRIPT_TAG) :].split():
        if "=" not in token:
            return None
        key, value = token.split("=", 1)
        fields[key] = value
    version = fields.get("v")
    required_keys = _BASE_METADATA_KEYS if version == "1" else _V2_METADATA_KEYS
    if any(key not in fields for key in required_keys):
        return None
    if version not in {"1", str(AWP1221_PAGE_SCRIPT_VERSION)}:
        return None
    try:
        kind = fields["kind"]
        waveform = _WAVEFORM_NAMES[fields["wave"]]
        if kind not in {"sinc", "rom"}:
            return None
        if (kind == "sinc") != (waveform == "SINC"):
            return None
        serial = fields["serial"]
        if serial not in _SERIALS:
            return None
        depth = fields["depth"]
        if depth not in {"512", "256"}:
            return None
        source = fields["source"]
        if source not in {"ROM", "RAM"}:
            return None
        stages = (
            _parse_stage(fields, "s0"),
            _parse_stage(fields, "s1"),
        )
        return Awp1221PageScript(
            kind=kind,
            auto_serial=_parse_flag(fields["auto"]),
            serial=serial,
            depth=depth,
            window_index=_parse_bounded(fields["window"], 31, "window"),
            max_voltage=_script_token(fields["vmax"]),
            min_voltage=_script_token(fields["vmin"]),
            times_us=(
                _script_token(fields["t0"]),
                _script_token(fields["t1"]),
                _script_token(fields["t2"]),
                _script_token(fields["t3"]),
            ),
            sin_repeat=str(_parse_range(fields["sinrep"], 1, 256, "sin repeat")),
            slice_index=_parse_bounded(fields["slice"], 31, "slice"),
            source=source,
            waveform=waveform,
            fine=_parse_bounded(fields["fine"], 3, "fine"),
            coarse=_parse_bounded(fields["coarse"], 31, "coarse"),
            offset=_parse_bounded(fields["offset"], 127, "offset"),
            half=_parse_bounded(fields["half"], 3, "half"),
            phase=_parse_bounded(fields["phase"], 1, "phase"),
            point_time=str(_parse_range(fields["point"], 1, 256, "point time")),
            rom_repeat=str(_parse_range(fields["romrep"], 1, 256, "repeat")),
            infinite=_parse_flag(fields["infinite"]),
            stages=stages,
            debounce_ms=str(_parse_bounded(fields["debms"], 1024, "debounce")),
            sample_index=_parse_bounded(fields["sample"], 3, "sample"),
            zero_index=_parse_bounded(fields["zero"], 7, "zero"),
            press_return_index=_parse_bounded(fields["pret"], 7, "press return"),
            release_return_index=_parse_bounded(fields["rret"], 7, "release return"),
            zps_enabled=_parse_flag(fields["zps"]),
            zps_detect=_parse_flag(fields["zpsd"]),
            sleep_sensitivity=_parse_flag(fields["sleep"]),
            ldo_index=_parse_bounded(fields["ldo"], 1, "ldo"),
            advanced_state="" if version == "1" or fields.get("x") == "-" else fields["x"],
        )
    except (KeyError, TypeError, ValueError):
        return None


def _parse_stage(fields: dict[str, str], prefix: str) -> Awp1221StageScript:
    mode = fields[f"{prefix}mode"]
    if mode not in {"press", "release"}:
        raise ValueError("stage mode")
    threshold = _script_token(fields[f"{prefix}th"])
    if threshold[:2].lower() == "0x":
        if not 0 <= int(threshold, 16) <= 0xFFF:
            raise ValueError("threshold")
    else:
        if not -2047 <= int(threshold, 10) <= 2047:
            raise ValueError("threshold")
    return Awp1221StageScript(
        mode=mode,
        ovv_code=_parse_bounded(fields[f"{prefix}ovv"], 15, "ovv"),
        jump=_parse_bounded(fields[f"{prefix}jump"], 1, "jump"),
        threshold=threshold,
        debounce=_parse_flag(fields[f"{prefix}deb"]),
        use_high_12_bits=_parse_flag(fields[f"{prefix}prec"]),
        wave=_parse_bounded(fields[f"{prefix}wave"], 31, "wave"),
        repeat=_parse_bounded(fields[f"{prefix}rep"], 7, "repeat"),
    )


def _script_token(value: object) -> str:
    text = str(value).strip()
    if not text or any(char.isspace() or char in "=|" for char in text):
        raise ValueError(f"page script field is empty or contains spaces: {value}")
    return text


def _parse_flag(text: str) -> bool:
    if text not in {"0", "1"}:
        raise ValueError("flag")
    return text == "1"


def _parse_bounded(text: str, maximum: int, name: str) -> int:
    return _parse_range(text, 0, maximum, name)


def _parse_range(text: str, minimum: int, maximum: int, name: str) -> int:
    value = int(str(text).strip(), 0)
    if not minimum <= value <= maximum:
        raise ValueError(name)
    return value
