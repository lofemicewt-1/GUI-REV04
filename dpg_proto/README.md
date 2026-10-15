# Dear PyGui prototype (Home + AWP1221 Button Mode)

UI-only prototype of the Piezo Control GUI in [Dear PyGui](https://github.com/hoffstadt/DearPyGui) (MIT).
Only the Home page and AWP1221 Button Mode are implemented; the other sidebar entries show a placeholder.
No serial / backend: all values are stubs. The waveform preview uses `core/waveform_engine.py` read-only
(falls back to a stub sine if it cannot be imported). The Tk app is untouched.

## Run (Windows, from the repo root)

    py -3 -m pip install -r dpg_proto/requirements.txt
    py -3 dpg_proto/main.py

## Notes
- Fonts: Microsoft YaHei (`C:\Windows\Fonts\msyh.ttc` / `msyhbd.ttc`) when present, otherwise the bundled
  Noto Sans SC subset in `fonts/` (ASCII + GB2312, SIL OFL). `PIEZO CONTROL` uses `assets/fonts/JetBrainsMono-ExtraBold.ttf`.
- DPI aware (per-monitor v2). Glyphs are rasterised at the final pixel size, so text is not stretched.
- Env: `PIEZO_DPG_SCALE=1.5` forces a DPI scale, `PIEZO_DPG_UI_K` (default 1.3) sizes text/widgets relative
  to the Tk build, `PIEZO_DPG_FONT=noto` skips YaHei.
- `bench.py` (dev only): startup + Home<->AWP1221 click-to-settled timings, `--shots DIR` writes screenshots.
