# AWP1221 SRAM editor integration

Date: 2026-09-22

## Goal

Ship the uploaded AWP1221 SRAM waveform editor into this repo and show it in two places:

- Full Mode: the existing **Waveform Generator** notebook tab
- Button Mode: when Waveform Source is **RAM**, replace the whole right-hand selector with the same editor

Do not rewrite SRAM compression, allocation, or the hardware write/verify sequence. Follow `AWP1221_REALTIME_SRAM_INTEGRATION.md`.

## Current gap

- `pages/awp1221_page.py` already imports `AWP1221SramEditor` from `pages/awp1221_realtime_sram.py` and Full Mode already grids it into the waveform tab.
- `pages/awp1221_realtime_sram.py` currently catches `ImportError` and sets `AWP1221SramEditor = None` because these files are missing:
  - `pages/awp1221_sram_editor.py`
  - `core/awp1221_sram.py`
- Button Mode already has Source `ROM` / `RAM` (`WAVEFORM_SOURCE_CODES`: ROM=`0b10`, RAM=`0b01`) but selecting RAM does not swap the UI.

## Architecture

Keep three layers. GUI callers import only the page-level entry.

| File | Role |
|---|---|
| `core/awp1221_sram.py` | Pure SRAM: smooth points, second-difference encode/decode, allocate `0x100–0x1FF`, descriptor words, write + readback. No Tk. |
| `pages/awp1221_sram_editor.py` | Controls, drag handles, background write, progress queue. |
| `pages/awp1221_realtime_sram.py` | Re-export `AWP1221SramEditor`. Realtime ADC mixin stays here. |

`AWP1221ModePage` already inherits `AWP1221RealtimeMixin` and calls `_init_adc_realtime()`. Do not move realtime logic back into the main page.

Button Mode and Full Mode are **separate** `AWP1221ModePage` instances. Each owns its own `AWP1221SramEditor`. Do not share one editor widget across modes.

## Files to add or change

### Add `core/awp1221_sram.py`

Use the uploaded module as-is. Public surface used by the editor:

- `generate_smooth_points`, `generate_waveform`, `compress_waveform`, `decompress_waveform`
- `allocate_words`, `build_descriptor`, `write_waveform`
- `DATA_START = 0x100`, `DATA_END = 0x1FF`

Do not call this module from Tk widgets except through the editor.

### Add `pages/awp1221_sram_editor.py`

Use the uploaded editor as-is for algorithm and write flow. Host (`owner`) must keep providing:

- `VPK_OPTIONS`, `OFFSET_OPTIONS`
- `device_addr()`, `_ensure_connected()`
- `_shared_state_for()`, `_invalidate_sinc_verified()`
- `auto_serial_var`, `rom_slice_var`

On successful write the editor already sets `auto_serial_var` False and `rom_slice_var` to `slice{wave}` when the device address still matches.

### Change `pages/awp1221_realtime_sram.py`

Remove the `try/except ImportError` fallback. Import `AWP1221SramEditor` directly and re-export it. Missing editor is a hard failure in tests and at runtime.

### Change `pages/awp1221_page.py`

**Full Mode:** keep the existing notebook wiring. After the editor file exists, drop the `if AWP1221SramEditor is not None` reserved-button branch and always grid the editor in the waveform tab.

**Button Mode:** create the same editor in the right panel, hidden until Source is RAM.

- Grid `AWP1221SramEditor` in the same cell as `_build_waveform_selector` (row 1 of the right panel).
- Default: selector visible, SRAM editor `grid_remove()`.
- When `rom_source_var == "RAM"` and waveform is not SINC: hide the ROM selector (form + preview + write button), show the SRAM editor, refresh language/theme/plot.
- When Source is ROM or waveform is SINC: hide the SRAM editor, show the selector (existing SINC vs ROM field rules unchanged).
- Trace `rom_source_var` (and existing waveform traces) so the swap happens immediately.
- Selecting RAM does not write the chip by itself. Chip writes stay on the editor's Write SRAM button, or on the selector Write Waveform button when Source is ROM.

SRAM editor write still uses `core/awp1221_sram.write_waveform`. Selector Write Waveform when Source is RAM still encodes a RAM slice mark (`0b01`) into the four-word descriptor; that path is unchanged and is not a substitute for custom SRAM sample data.

## Button Mode layout

Right column stays two rows: waveform area (weight 3) and serial log (weight 2).

RAM selected:

```
[ SRAM editor: controls | plot ]
[ serial log ]
```

ROM / SINC selected:

```
[ existing waveform selector + preview ]
[ serial log ]
```

No extra notebook tab in Button Mode.

## Write data flow

1. User enters or drags 2–32 control points (relative to Vpk, minimum -36%).
2. `generate_waveform` builds a smooth curve and second-difference compressed words.
3. Editor allocates a contiguous range in `0x100–0x1FF` that does not overlap other waveforms written in this session for the same `(port, device)`.
4. Background thread: stop detection/play if shared state says they are active, enter SRAM access mode, write samples then WAVE descriptor, verify by readback, restore registers.
5. UI thread polls a `Queue` about every 40 ms, updates the progress bar, unlocks controls, and syncs slice on success.

## Error handling

Show an error dialog and leave other session waveforms untouched when:

- serial is disconnected
- control-point count or values are invalid
- requested point count cannot compress into SRAM
- address is outside `0x100–0x1FF` or overlaps another waveform from this session
- any register write/readback fails

While a write is running: disable editor inputs, ignore extra Write clicks, keep the plot frozen until finish.

Do not write SRAM from the Tk main thread in a blocking loop.

## Testing

Add:

- `tests/test_awp1221_sram.py` — compress/decompress round-trip, allocate without overlap, descriptor bounds, invalid point counts
- `tests/test_awp1221_sram_ui.py` — editor constructs; Button Mode RAM shows editor and hides selector; ROM/SINC hides editor; write path does not block the UI thread (mock serial)

Also:

```
python -m py_compile pages/awp1221_realtime_sram.py pages/awp1221_page.py pages/awp1221_sram_editor.py core/awp1221_sram.py
python -m unittest tests.test_awp1221_sram tests.test_awp1221_sram_ui
```

Manual on hardware (not automated here): realtime window start/stop, disconnect stops polling, SRAM progress visible, failed readback reports, WAVE number and slice sync after write.

## Out of scope

- Changing realtime ADC sampling or threshold overlays
- Sharing one editor instance between Button Mode and Full Mode
- Persisting SRAM occupancy across GUI restarts (session map only)
- Button Mode software presets that generate custom SRAM shapes
- Arduino EEPROM Type-4 AWP1221 board presets
