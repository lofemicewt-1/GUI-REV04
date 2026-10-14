# Task 2 re-review (after approved ttk assertion fix)

Task-scoped gate. Diff: `.superpowers/sdd/review-task-2-rereview.diff` (base `cd12818`, working tree). Editor SHA-256 matches uploaded (`681598D7212EFD38326F377A675BCE5F22BC214FF8F753C3DF5807D35B81FECD`).

### Spec Compliance

Met.

- **Editor as-is:** `pages/awp1221_sram_editor.py` is byte-identical to `.superpowers/sdd/uploaded-awp1221_sram_editor.py`. `AWP1221SramEditor(BasePage)`, `__init__(parent, ctx, owner)`, `core.awp1221_sram` (`generate_waveform`, `generate_smooth_points`, `write_waveform`, `allocate_words`), 2–32 control points, min `-36%`, worker thread + 40 ms poll. Write algorithm not rewritten.
- **Re-export:** `pages/awp1221_realtime_sram.py` dropped the `try/except ImportError` fallback for a direct import. `pages.awp1221_realtime_sram.AWP1221SramEditor` is the class, never `None`. `__all__` still includes `"AWP1221SramEditor"`.
- **Import seam:** GUI callers import `AWP1221SramEditor` only from `pages/awp1221_realtime_sram.py`. The sole `from pages.awp1221_sram_editor import` is the re-export. Tests import from the realtime entry.
- **No blocking SRAM writes on Tk main thread:** `_start_sram_write` snapshots Tk state, runs `write_waveform` in `threading.Thread`, polls the queue with `after(40, poll)`.
- **Occupancy session-only `0x100–0x1FF`:** `_sram_saved` is in-memory; key `(port, device_addr)`; manual path enforces `0x100 <= start <= end <= 0x1FF`; auto path uses `allocate_words` (`DATA_START`/`DATA_END`).
- **Host contract:** editor uses `VPK_OPTIONS`, `OFFSET_OPTIONS`, `device_addr()`, `_ensure_connected()`, `_shared_state_for()`, `_invalidate_sinc_verified()`, `auto_serial_var`, `rom_slice_var`. Widget-test `_Owner` stub matches.
- **Tests 2/2 PASS:**
  ```
  py -3 -m unittest tests.test_awp1221_sram_ui.Awp1221SramEditorImportTests tests.test_awp1221_sram_ui.Awp1221SramEditorWidgetTests -v
  ok    test_realtime_entry_exports_editor_class
  ok    test_editor_constructs_and_enables_write_for_default_shape
  Ran 2 tests in 0.172s
  OK
  ```
  Widget assertion uses approved `editor._sram_write_btn.instate(["!disabled"])`.

### Strengths

- Verbatim uploaded editor; no opportunistic rewrite of compression, allocation, or write sequencing.
- Stable re-export: missing editor is now a hard import failure, as specified.
- Default shape constructs, sets `_sram_result`, and enables Write.

### Issues

None. The ttk `instate` assertion is an approved deviation and is not re-flagged.

### Assessment

Approved
