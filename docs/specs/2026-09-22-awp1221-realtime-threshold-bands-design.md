# AWP1221 realtime threshold lines and press vibration band

Date: 2026-09-22

## Goal

On the existing AWP1221 realtime voltage window, overlay:

- Upper (press) threshold as a blue dashed line
- Lower (release) threshold as a purple dashed line
- After a press event, a green vertical band covering waveform play + debounce

Do not change register writes, detection, or the conversion formula already used by the live curve.

## Colors

Match AWP1923 sense-status press/release:

- Upper threshold line: `#2563EB`, dashed `(6, 4)`, label `上阈值`
- Lower threshold line: `#9333EA`, dashed `(6, 4)`, label `下阈值`
- Vibration band: `#86EFAC`, no outline, drawn under the ADC curve

## Threshold source

Use the **current page form**, not a chip readback. Update the window immediately when those fields change, while the realtime window exists.

Button mode:

- Upper = Detection threshold of the first stage whose event mode is press
- Lower = Detection threshold of the first stage whose event mode is release
- Apply that stage's Precision (`use_high_12_bits`) independently

Full mode:

- Use the currently visible ADC stage
- Upper = Press threshold
- Lower = Release threshold
- Apply that stage's shared Precision

If a field is missing or not a valid integer, omit that line.

Precision on: treat the 12-bit code as the high 12 bits of the 16-bit ADC sample (`code << 4`) before conversion. Precision off: use the 12-bit code as-is.

## Conversion and Y scale

Convert threshold counts with the same window formula as live samples:

`mV = counts * sample_time_us * load_uA / C_uF / 1000`

Y-axis ceiling is `1.15 * max(1.0, |visible samples|, |upper mV|, |lower mV|)`. Thresholds stay on-screen even when the live signal is small.

Draw labels on the right end of each visible line, including the current mV.

## Vibration band

When the realtime window is open, a `MCU_TEXT P` log starts one band at `time.monotonic()`. Trigger-read does not need to be on. Detection must be running for the MCU to emit `P`; the GUI does not software-detect threshold crossings.

Width in seconds:

`max(event_repeat, 1) * waveform_period_s + debounce_ms / 1000`

- `debounce_ms` = page Debounce time field (0 if invalid)
- `event_repeat` = press-stage Repeat in button mode, or visible-stage Press repeat in full mode
- `waveform_period_s`:
  - SINC selected: current SINC plan `duration_us / 1e6` (if the plan cannot be built, period is 0)
  - otherwise: `effective_depth * point_time` microseconds
- ROM infinite loop still uses **one** period, not an open-ended band

Keep recent bands and clip them to the visible time window. Do not start a band on `R`.

## Architecture

- `ADCVoltageWindow` stores threshold counts, draws lines/bands in `redraw()`, and exposes `set_threshold_counts(upper, lower)` plus `add_vibration_band(duration_s)`
- `AWP1221ModePage` computes counts from form state, traces threshold/precision/debounce/waveform fields, and on `MCU_TEXT P` (existing log listener) calls `add_vibration_band` if the window exists
- Conversion stays inside the window so C / LOAD / Sample Time changes move the lines with the curve

## Out of scope

- Writing thresholds or debounce from the realtime window
- Release (`R`) bands
- Auto-detecting press from the ADC trace
- SRAM editor
- Changing poll-interval behavior

## Test

Pure helpers: Precision shift, mV conversion, band width, omit-invalid-threshold. No hardware required.
