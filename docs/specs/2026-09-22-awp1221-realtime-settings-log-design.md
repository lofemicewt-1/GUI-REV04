# AWP1221 realtime settings write and quiet 0x13 poll

Date: 2026-09-22

## Goal

While the AWP1221 realtime voltage window is polling:

- Do not log 0x13 poll traffic
- Changing ADC sample time / ADC load in the window writes the chip
- Rename C to 陶瓷电容等效容值
- Unify sample-time labels with 0x05[11:10] as 4 / 8 / 16 / 32 us

## Quiet 0x13 poll log

Only the periodic realtime read of register `0x13` is silent: no `TX` and no `MCU` line for that command.

Still logged:

- Manual “read current ADC”
- Window or page register writes
- Errors
- `MCU_TEXT P` / `R` (vibration band and trigger-read)

Implementation: `SerialManager.read_register_value(..., quiet=False)` and `send_command(..., log_traffic=True)`. Realtime poll calls quiet read. Quiet skips TX/MCU publish; ERROR still publishes.

## Sample time (0x05 bit11-10)

Codes, same on the main page combobox and the realtime window:

| bits 11-10 | Label |
|---|---|
| 00 | 4 us |
| 01 | 8 us |
| 10 | 16 us |
| 11 | 32 us |

Replace `AWP1221_ADC_SAMPLE_UPDATE_OPTIONS` `6/10/18/34 us` with `4/8/16/32 us`. Software presets that used `6 us` use `4 us`.

Window combobox change: background read-modify-write of 0x05, only bits 11-10, then set `adc_sample_update_var` to the same label. Conversion formula uses the new sample time immediately.

C / poll interval / time scale do not write the chip.

## ADC load (0x1F bit2)

| bit2 | Label |
|---|---|
| 0 | 40 uA |
| 1 | 160 uA |

Window combobox change: background RMW of 0x1F, only bit2. Do not change bit3 (command gate). Opening the window displays load from bit2, not bit11.

## Capacitance label

Realtime window control currently `C (uF)` becomes `陶瓷电容等效容值 (uF)`. Still conversion-only.

## Errors

Failed settings write: set the window status to `读取失败` / write error text. Do not stop polling.

## Out of scope

- Removing 0x1F bit3 command gate
- Writing 0x05/0x1F from poll interval or capacitance
- Muting logs other than 0x13 polls
- AWP1018 sample-time options

## Test

- Decode/encode 0x05[11:10] against 4/8/16/32 us
- Load display and RMW mask uses bit2; bit3 preserved
- Quiet read does not call emit_log for TX/MCU
