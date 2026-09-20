# Chip page footer chrome

Date: 2026-09-20

## Goal

On every chip page, merge the two bars that currently sit above the mode content (communication / chip address / detect, and register read-write / chip-state actions) into one footer and pin that footer to the bottom of the window. Mode content fills the space between the chip title and the footer.

## Out of scope

- Home and Settings pages
- Moving mode-selection buttons (already in the left sidebar)
- Changing register, detect, or chip-state behavior
- Restyling controls except as needed for the new grid

## Page structure

Each chip page uses three rows:

1. Chip title (`make_title`) — no weight
2. Mode content — `rowconfigure` weight 1, sticky `nsew`
3. Merged chrome footer — no weight, sticky `ew`

The footer is one `Panel.TFrame` with two rows:

- Row 0: existing communication / address / detect / status controls for that chip
- Row 1+: existing register read-write bar and chip-state buttons for that chip

Do not squeeze both bars onto a single line.

## Sticky footer

The footer must stay visible at the bottom of the window, not trail off below tall mode content.

- The chip page fills the navigation content area.
- Overflow stays in the mode-content row (existing inner `VerticalScrollArea` / mode-page scrolls).
- If the outer navigation canvas would otherwise grow with page `reqheight` and scroll the footer away, cap the chip page (or that canvas window) to the visible canvas height so the footer cannot leave the window.

## Pages

| Page | Footer row 0 | Footer remainder | Notes |
| --- | --- | --- | --- |
| AWP1221 | Serial, address, detect, status | Register R/W, reset / sleep / shutdown / default | Mode buttons stay in sidebar |
| AWP1018 | Serial, address, detect, status | Register R/W, reset / sleep / shutdown / wake | GPIO leaves this chrome (see below) |
| AWP1921/AWP1923 | Serial, address | Register R/W, state commands, detail-register mode | Keep detail-register UI in the footer |
| AWP1922 | Serial, address, detect, status | Register R/W only | No chip-state row today; do not add one |
| AWP1744/AWP1766 | Serial, address, import, export | Register R/W | Import/export travel with the communication row |

Reuse each page’s existing widgets and handlers. Relayout only.

## AWP1018 GPIO

Remove the page-level `GpioControlPanel` from between the register bar and mode content.

Keep a single `GpioControlPanel` owned by `AWP1018Page` (preserve D4 sync callbacks). Grid that same panel into the active mode page’s left column, after ADC settings and control buttons, matching AWP1221 (`_build_gpio_settings` at the bottom of the left ADC panel).

Applies to Normal, Diff, and Full mode left columns. `show_mode` re-grids the shared panel into the newly shown page.

## Non-goals for GPIO

- Do not copy AWP1221’s per-pin function/OD editor onto AWP1018
- Do not put GPIO into the footer

## Verification

- Open each chip page: title at top, mode content in the middle, merged footer visible at the window bottom without scrolling the outer page
- Resize the window: footer stays pinned; mode content shrinks/grows and inner scrolls still work
- Switch Button / Full / other modes from the sidebar: footer stays; only mode content changes
- AWP1018: GPIO is gone from the top chrome and appears under ADC content in Normal, Diff, and Full
- Register read/write, detect, and chip-state buttons still work on each chip
