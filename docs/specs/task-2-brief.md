### Task 2: SRAM editor widget and stable re-export

**Files:**
- Create: `pages/awp1221_sram_editor.py`
- Modify: `pages/awp1221_realtime_sram.py` (replace the `try/except ImportError` block at lines 17–20 with a direct import)
- Test: `tests/test_awp1221_sram_ui.py`

**Interfaces:**
- Consumes: `core.awp1221_sram.generate_waveform`, `generate_smooth_points`, `write_waveform`, `allocate_words`
- Produces:
  - `class AWP1221SramEditor(BasePage)` with `__init__(self, parent, ctx, owner)`
  - Host must provide `VPK_OPTIONS`, `OFFSET_OPTIONS`, `device_addr()`, `_ensure_connected()`, `_shared_state_for()`, `_invalidate_sinc_verified()`, `auto_serial_var`, `rom_slice_var`
  - `pages.awp1221_realtime_sram.AWP1221SramEditor` is the class, never `None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_awp1221_sram_ui.py`:

```python
from __future__ import annotations

import unittest
from types import SimpleNamespace

import tkinter as tk

from pages.awp1221_realtime_sram import AWP1221SramEditor


def _colors() -> dict[str, str]:
    return {
        "bg": "#ffffff",
        "panel": "#ffffff",
        "panel_alt": "#f3f4f6",
        "text": "#111827",
        "muted": "#64748B",
        "border": "#CBD5E1",
        "primary": "#2563EB",
        "canvas_bg": "#ffffff",
    }


class _Serial:
    is_connected = False
    log_history: list[str] = []

    def add_log_listener(self, _listener) -> None:
        return None

    def remove_log_listener(self, _listener) -> None:
        return None

    def add_log_clear_listener(self, _listener) -> None:
        return None

    def remove_log_clear_listener(self, _listener) -> None:
        return None

    def selected_port_for_page(self, *_args, **_kwargs):
        return None


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        serial=_Serial(),
        state=SimpleNamespace(language="en", theme="light", extra={}, selected_port=None),
        colors=_colors(),
    )


class _Owner:
    VPK_OPTIONS = ("0x00 - 0.00 V", "0x01 - 6.25 V")
    OFFSET_OPTIONS = ("0x00 - 0 V",)

    def __init__(self) -> None:
        self.auto_serial_var = tk.BooleanVar(value=True)
        self.rom_slice_var = tk.StringVar(value="slice0")

    def device_addr(self) -> int:
        return 0x50

    def _ensure_connected(self) -> bool:
        return False

    def _shared_state_for(self, _addr=None):
        return None

    def _invalidate_sinc_verified(self) -> None:
        return None


class Awp1221SramEditorImportTests(unittest.TestCase):
    def test_realtime_entry_exports_editor_class(self) -> None:
        self.assertIsNotNone(AWP1221SramEditor)
        self.assertTrue(callable(AWP1221SramEditor))


class Awp1221SramEditorWidgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_editor_constructs_and_enables_write_for_default_shape(self) -> None:
        owner = _Owner()
        editor = AWP1221SramEditor(self.root, _ctx(), owner)
        self.addCleanup(editor.destroy)
        editor.update_idletasks()
        self.assertIn("!disabled", editor._sram_write_btn.state())
        self.assertIsNotNone(editor._sram_result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m unittest tests.test_awp1221_sram_ui.Awp1221SramEditorImportTests -v`

Expected: FAIL because `AWP1221SramEditor` is `None` (current `ImportError` fallback).

- [ ] **Step 3: Add the editor and drop the fallback**

Create `pages/awp1221_sram_editor.py` as the uploaded editor (the September 20 `AWP1221SramEditor` that subclasses `BasePage`, uses `core.awp1221_sram`, drags 2–32 control points, minimum `-36%`, writes on a worker thread with a 40 ms poll). Do not change its write algorithm.

In `pages/awp1221_realtime_sram.py` replace:

```python
try:
    from pages.awp1221_sram_editor import AWP1221SramEditor
except ImportError:
    AWP1221SramEditor = None
```

with:

```python
from pages.awp1221_sram_editor import AWP1221SramEditor
```

Keep `__all__` including `"AWP1221SramEditor"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m unittest tests.test_awp1221_sram_ui.Awp1221SramEditorImportTests tests.test_awp1221_sram_ui.Awp1221SramEditorWidgetTests -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pages/awp1221_sram_editor.py pages/awp1221_realtime_sram.py tests/test_awp1221_sram_ui.py
git commit -m "feat: add AWP1221 SRAM editor and re-export it from realtime entry"
```

Skip unless the user asked to commit.

---

