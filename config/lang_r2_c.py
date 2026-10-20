"""Stream C language additions and explicit updates to existing glossary text."""
from __future__ import annotations

ZH = {
    "r2c_1221_normal_vibration": "正常震感",
    "r2c_1221_strong_vibration": "强震感",
    "r2c_custom_waveform": "自定义波形",
    "r2c_rom_sine": "ROM 正弦",
    "r2c_auto_serial_required": "需勾选自动串联才能用此效果",
    "r2c_unsupported_waveform_notice": "当前波形 {value} 不在简化选项中，请进入完整界面调试。",
    "r2c_more_waveforms_notice": "需要更多波形？请使用完整界面。",
    "r2c_full_mode_link": "完整界面",
}

EN = {
    "r2c_1221_normal_vibration": "Normal vibration",
    "r2c_1221_strong_vibration": "Strong vibration",
    "r2c_custom_waveform": "Custom waveform",
    "r2c_rom_sine": "ROM sine",
    "r2c_auto_serial_required": "Enable auto serial to use this effect.",
    "r2c_unsupported_waveform_notice": "Waveform {value} is outside the simplified choices. Open Full Mode to inspect it.",
    "r2c_more_waveforms_notice": "Need more waveforms? Open Full Mode.",
    "r2c_full_mode_link": "Full Mode",
}

# These are intentional replacements for glossary entries already present in
# config/languages.py. Keep the additions above namespaced; the integrator can
# apply this small override list when merging the stream snippet.
EXISTING_KEY_OVERRIDES = (
    (
        "glossary_serial",
        "串联播放：将多个波形片段按顺序连续播放；serial1 为正常震感（slice18–20），serial3 为强震感（slice24–27）。",
        "Play waveform segments in sequence; serial1 is normal vibration (slice18–20), and serial3 is strong vibration (slice24–27).",
    ),
)


def install(languages=None) -> None:
    if languages is None:
        from config.languages import LANGUAGES as languages
    languages["zh"].update(ZH)
    languages["en"].update(EN)
    for key, zh_text, en_text in EXISTING_KEY_OVERRIDES:
        languages["zh"][key] = zh_text
        languages["en"][key] = en_text
