# AWP1221 signed press/release thresholds and ADC_LDO

Date: 2026-09-22

## Goal

1. Press/release detection thresholds accept signed decimal voltage codes and encode them as 12-bit two's complement in the existing 12-bit threshold field.
2. Add an ADC_LDO current control (0x1F bit2, same as realtime ADC_LOAD) in ADC settings. After EN goes HIGH, default the chip and UI to 160 µA.

## Threshold encoding

12-bit two's complement in bits [11:0] of 0x09/0x0A and 0x0E/0x0F. Control bits (debounce, CFG_NXT, precision, below) are unchanged.

| Input | 12-bit code | Meaning |
|---|---|---|
| 2047 | 0x7FF | positive max |
| 0 | 0x000 | zero |
| -1 | 0xFFF | least negative |
| -2047 | 0x801 | |
| 0x800 (hex only) | 0x800 | most negative (-2048) |

Parse rules:

- `0x` / `0X` prefix: raw 12-bit code `0..0xFFF`.
- Otherwise: signed decimal `-2047..2047`. Store `value & 0xFFF`.
- Reject other values.

Display:

- Entry stays signed decimal on readback (`0xFFF` → `-1`, `0x800` → `-2048`).
- Right-hand hint stays hex (`-1` → `0xFFF`, `-2047` → `0x801`). Empty or invalid input → empty hint.
- Labels: `按压阈值 (-2047~2047)` / `松手阈值 (-2047~2047)` (EN/ZH). Detection threshold in button mode uses the same parse/hint; no range suffix unless already present.

`encode_awp1221_adc_threshold` still takes the 12-bit code `0..0xFFF`. UI parse happens before encode. Readback formats the 12-bit field with a signed helper before `.set()`.

## Realtime overlay

`awp1221_threshold_adc_counts` sign-extends the 12-bit code, then applies precision (`<< 4` when high-12-bit). Negative thresholds plot below zero. Form parse for overlay uses the same threshold parser; invalid text still omits that line.

## ADC_LDO

Same bit as realtime `ADC_LOAD`: 0x1F bit2, `0` = 40 µA, `1` = 160 µA. Preserve bit3 (command gate) via existing `awp1221_merge_adc_load`.

ADC settings layout (four columns):

| Row | Left | Right |
|---|---|---|
| 1 | Debounce time | ADC sample update |
| 2 | Zero Time | **ADC_LDO** `40 uA` / `160 uA` |
| 3 | Press return-bit | Release return-bit |
| 4+ | ZPS / ADC value / buttons unchanged | |

Default combobox before EN: `40 uA`.

Writes:

- Apply ADC settings: RMW 0x1F bit2 from the combobox, in addition to 0x05/0x06/debounce.
- Read ADC settings: also read 0x1F and set the combobox from bit2.
- EN HIGH after GPIO ACK: set combobox to `160 uA`, RMW 0x1F bit2 = 1. EN LOW does not change load. If the 0x1F write fails, keep EN HIGH and show the error.
- Realtime window load changes already write bit2; also set the page `adc_ldo_var`. Page apply/EN writes set `window.load` when the realtime window exists.

Software presets that already write `0x1F = 0x070C` keep that write; set the combobox to `160 uA` after that preset.

## Out of scope

- Renaming the realtime window `ADC_LOAD` label
- Changing below-threshold / precision / debounce bits
- Offset-binary (0x800 = 0 V) encoding
- EN LOW restoring 40 µA

## Test

- Parse/format/hint: `2047→0x7FF`, `-1→0xFFF`, `-2047→0x801`, `0x800→0x800`, `4095` rejected, `-2048` decimal rejected
- `awp1221_threshold_adc_counts(0xFFF, False) == -1`; with precision `== -16`
- `awp1221_merge_adc_load` unchanged (bit2 only, bit3 preserved)
- EN HIGH helper/path sets 160 µA without clearing bit3
