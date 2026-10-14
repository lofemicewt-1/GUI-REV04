### Task 3: Full Mode waveform tab always hosts the editor

**Files:**
- Modify: `pages/awp1221_page.py` around `_build_ui` Full Mode notebook (the `if AWP1221SramEditor is not None` branch)
- Test: `tests/test_awp1221_sram_ui.py`

**Interfaces:**
- Consumes: `AWP1221SramEditor(parent, ctx, owner)` from Task 2
- Produces: `AWP1221ModePage.sram_editor` always set in `full_mode`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_awp1221_sram_ui.py`:

```python
from pages.awp1221_page import AWP1221ModePage


def _mode_page(root: tk.Misc, mode_key: str) -> AWP1221ModePage:
    return AWP1221ModePage(
        root,
        _ctx(),
        tr=lambda key, **_kwargs: key,
        device_addr=lambda: 0x50,
        mode_key=mode_key,
        host=None,
    )


class Awp1221FullModeSramTabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_full_mode_grids_sram_editor_in_waveform_tab(self) -> None:
        page = _mode_page(self.root, "full_mode")
        self.addCleanup(page.destroy)
        page.update_idletasks()
        self.assertIsInstance(page.sram_editor, AWP1221SramEditor)
        self.assertTrue(page.sram_editor.grid_info())
        self.assertFalse(hasattr(page, "_buttons") and "awp1221_waveform_reserved" in page._buttons)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m unittest tests.test_awp1221_sram_ui.Awp1221FullModeSramTabTests -v`

Expected: FAIL on the reserved-button assertion if the `else` branch still exists, or FAIL if editor is missing. After Task 2 the editor exists, so this should fail only while the `else` reserved branch remains (or pass if the `if` already succeeds). If it already passes because `AWP1221SramEditor is not None`, keep the `else` deletion anyway so a future import failure is loud.

- [ ] **Step 3: Always grid the editor**

In `pages/awp1221_page.py` `_build_ui` (full mode), replace:

```python
        if AWP1221SramEditor is not None:
            waveform_tab.columnconfigure(0, weight=1)
            waveform_tab.rowconfigure(0, weight=1)
            self.sram_editor = AWP1221SramEditor(waveform_tab, self.ctx, self)
            self.sram_editor.grid(row=0, column=0, sticky="nsew")
        else:
            waveform_btn = ttk.Button(waveform_tab, style="Secondary.TButton", state="disabled")
            waveform_btn.grid(row=0, column=0, sticky="w")
            self._buttons["awp1221_waveform_reserved"] = waveform_btn
        self._build_waveform_selector(rom_tab)
```

with:

```python
        waveform_tab.columnconfigure(0, weight=1)
        waveform_tab.rowconfigure(0, weight=1)
        self.sram_editor = AWP1221SramEditor(waveform_tab, self.ctx, self)
        self.sram_editor.grid(row=0, column=0, sticky="nsew")
        self._build_waveform_selector(rom_tab)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m unittest tests.test_awp1221_sram_ui.Awp1221FullModeSramTabTests tests.test_awp1221_sram -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pages/awp1221_page.py tests/test_awp1221_sram_ui.py
git commit -m "feat: host AWP1221 SRAM editor in Full Mode waveform tab"
```

Skip unless the user asked to commit.

---

