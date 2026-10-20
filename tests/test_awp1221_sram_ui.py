from __future__ import annotations

import ctypes
import json
import os
import struct
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import tkinter as tk

from core.awp1221_sram import SramWaveform
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
        self.assertTrue(editor._sram_write_btn.instate(["!disabled"]))
        self.assertIsNotNone(editor._sram_result)


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


class Awp1221ButtonModeSramSwapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.page = _mode_page(self.root, "button_mode")
        self.addCleanup(self.page.destroy)
        self.page.update_idletasks()

    def test_en_module_is_above_waveform_module(self) -> None:
        en_section = self.page._button_en_section
        waveform_section = self.page._button_waveform_section
        self.assertIs(en_section.master, waveform_section.master)
        self.assertEqual(int(en_section.grid_info()["row"]), 0)
        self.assertEqual(int(waveform_section.grid_info()["row"]), 1)
        self.assertLess(
            int(en_section.grid_info()["row"]),
            int(waveform_section.grid_info()["row"]),
        )

    def test_default_rom_hides_sram_editor(self) -> None:
        self.assertIsInstance(self.page.sram_editor, AWP1221SramEditor)
        self.assertEqual(self.page.sram_editor.grid_info(), {})
        self.assertTrue(self.page._waveform_selector_shell.grid_info())

    def test_ram_tab_shows_editor_without_source_combo(self) -> None:
        self.page._select_button_wave_tab("sram")
        self.page.update_idletasks()
        self.assertTrue(self.page.sram_editor.grid_info())
        source_widgets = self.page._selector_widgets["awp1221_rom_source"]
        self.assertFalse(any(widget.grid_info() for widget in source_widgets))
        self.assertEqual(self.page.rom_source_var.get(), "RAM")
        self.page._select_button_wave_tab("sinc")
        self.assertEqual(self.page.rom_source_var.get(), "ROM")

    def test_ram_collapses_rom_preview_so_editor_is_taller(self) -> None:
        self.page.rom_source_var.set("RAM")
        self.page.update_idletasks()
        shell = self.page._waveform_selector_shell
        editor = self.page.sram_editor
        self.assertGreater(editor.winfo_reqheight(), shell.winfo_reqheight())
        self.assertEqual(self.page.rom_canvas.grid_info(), {})
        preview = getattr(self.page, "_waveform_selector_preview", None)
        if preview is not None:
            self.assertEqual(preview.grid_info(), {})

    def test_sinc_hides_editor_even_if_source_is_ram(self) -> None:
        self.page.rom_source_var.set("RAM")
        self.page.rom_waveform_var.set("SINC")
        self.page.update_idletasks()
        self.assertEqual(self.page.sram_editor.grid_info(), {})

    def test_returning_to_rom_hides_editor(self) -> None:
        self.page.rom_source_var.set("RAM")
        self.page.rom_source_var.set("ROM")
        self.page.update_idletasks()
        self.assertEqual(self.page.sram_editor.grid_info(), {})


def _editor(root: tk.Misc, owner: _Owner | None = None) -> AWP1221SramEditor:
    editor = AWP1221SramEditor(root, _ctx(), owner or _Owner())
    editor.grid(row=0, column=0, sticky="nsew")
    return editor


def _pump(root: tk.Misc, predicate, timeout: float = 3.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for SRAM editor state")


class _RecordingSerial(_Serial):
    def __init__(self) -> None:
        self.manager = _RecordingManager()

    def _current_manager(self):
        return self.manager


class _RecordingManager:
    def __init__(self) -> None:
        self.writes: list[tuple[int, int]] = []
        self.fail = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False

    def snapshot_transaction(self):
        return nullcontext()

    def read_register_value(self, _address, reg, bits=16):
        if self.block:
            self.entered.set()
            self.release.wait(5)
        if self.fail:
            return SimpleNamespace(ok=False, error="read failed", data=b"")
        return SimpleNamespace(ok=True, data=b"")

    def extract_rdataw_value(self, _data, device_addr, reg_addr, bits=16):
        return {5: 0x442E, 0x14: 0, 0x15: 0}.get(reg_addr, 0)

    def write_register_value(self, _address, reg, value, bits=16):
        self.writes.append((reg, value))
        if self.block and not self.entered.is_set():
            self.entered.set()
            self.release.wait(5)
        if self.fail:
            return SimpleNamespace(ok=False, error="write failed")
        return SimpleNamespace(ok=True)


class _ConnectedOwner(_Owner):
    def _ensure_connected(self) -> bool:
        return True


class Awp1221SegmentRatioUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.geometry("980x720+30+30")
        self.addCleanup(self.root.destroy)

    def test_default_four_points_show_three_equal_segments(self) -> None:
        editor = _editor(self.root)
        self.addCleanup(editor.destroy)
        editor.update_idletasks()
        self.assertEqual(len(editor._sram_ratio_rows), 3)
        self.assertEqual(editor._sram_ratio_mode, "equal")
        self.assertEqual(
            [row["variable"].get() for row in editor._sram_ratio_rows],
            ["33.34", "33.33", "33.33"],
        )
        self.assertEqual(str(editor._sram_ratio_rows[-1]["entry"].cget("state")), "readonly")
        self.assertIn("Start points", editor._sram_points_label.cget("text"))
        self.assertIn("may increase", editor._sram_points_hint.cget("text"))
        self.assertIsNone(editor._sram_explicit_weights)

    def test_three_points_show_left_right_and_amplitude_keeps_ratio(self) -> None:
        editor = _editor(self.root)
        self.addCleanup(editor.destroy)
        editor.sram_values_var.set("0, 100, 0")
        editor._apply_sram_values()
        self.assertFalse(editor._sram_input_invalid)
        self.assertEqual(len(editor._sram_ratio_rows), 2)
        self.assertIn("Left", editor._sram_ratio_rows[0]["label"].cget("text"))
        self.assertIn("Right", editor._sram_ratio_rows[1]["label"].cget("text"))
        self.assertIn("auto", editor._sram_ratio_rows[1]["label"].cget("text"))
        self.assertIn("reset to equal", editor.sram_status_var.get())
        editor._sram_ratio_rows[0]["variable"].set("30")
        editor._apply_sram_values()
        self.assertEqual(editor._sram_ratio_mode, "explicit")
        self.assertEqual(editor._sram_explicit_weights, (3000, 7000))
        editor.sram_values_var.set("0, 80, 0")
        editor._apply_sram_values()
        self.assertEqual(editor._sram_controls, [0.0, 80.0, 0.0])
        self.assertEqual(editor._sram_explicit_weights, (3000, 7000))
        self.assertEqual(editor._sram_ratio_rows[0]["variable"].get(), "30.00")
        self.assertEqual(editor._sram_ratio_rows[1]["variable"].get(), "70.00")

    def test_ratio_dots_curve_and_hit_use_actual_intervals(self) -> None:
        self.root.deiconify()
        editor = _editor(self.root)
        self.addCleanup(editor.destroy)
        editor.sram_values_var.set("0, 100, 0")
        editor._apply_sram_values()
        editor._sram_ratio_rows[0]["variable"].set("30")
        editor._apply_sram_values()
        editor.update()
        result = editor._sram_result
        self.assertIsNotNone(result)
        intervals = result.segment_intervals
        left, _top, width, height = editor._sram_geometry()
        expected_x = left + intervals[0] / (len(result.points) - 1) * width
        ovals = [editor.rom_canvas.coords(item) for item in editor.rom_canvas.find_withtag("all")
                 if editor.rom_canvas.type(item) == "oval"]
        centers = [((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) for box in ovals]
        middle = min(centers, key=lambda item: abs(item[0] - expected_x))
        self.assertAlmostEqual(middle[0], expected_x, delta=1.5)
        line = next(item for item in editor.rom_canvas.find_withtag("all")
                    if editor.rom_canvas.type(item) == "line" and float(editor.rom_canvas.itemcget(item, "width")) == 2)
        coords = editor.rom_canvas.coords(line)
        curve_x = coords[2 * intervals[0]]
        self.assertAlmostEqual(curve_x, expected_x, delta=1.5)
        xs = editor._sram_control_xs(left, width, len(result.points), intervals, len(editor._sram_controls))
        tie_x = (xs[0] + xs[1]) / 2
        editor._sram_press(SimpleNamespace(x=tie_x, y=_top + height / 2))
        self.assertEqual(editor._sram_drag_index, 0)
        editor._sram_press(SimpleNamespace(x=expected_x, y=middle[1]))
        self.assertEqual(editor._sram_drag_index, 1)
        before = list(editor._sram_controls)
        editor._sram_drag_index = 0
        editor._sram_drag(SimpleNamespace(x=left, y=_top + height))
        self.assertEqual(editor._sram_controls[1:], before[1:])
        self.assertNotEqual(editor._sram_controls[0], before[0])

    def test_ui_uses_returned_intervals_when_final_count_grows(self) -> None:
        self.root.deiconify()
        editor = _editor(self.root)
        self.addCleanup(editor.destroy)
        editor.sram_values_var.set("0, 100, 0")
        editor._apply_sram_values()
        fake = SramWaveform(tuple(1800 for _ in range(22)), (0, 1800), (0,), (3, 18))

        def generate(*_args, **_kwargs):
            return fake

        with patch("pages.awp1221_sram_editor.generate_waveform", generate):
            editor._draw_sram_waveform()
            editor.update()
            left, top, width, _height = editor._sram_geometry()
            expected_x = left + 3 / 21 * width
            ovals = [editor.rom_canvas.coords(item) for item in editor.rom_canvas.find_withtag("all")
                     if editor.rom_canvas.type(item) == "oval"]
            centers = sorted((box[0] + box[2]) / 2 for box in ovals)
            self.assertAlmostEqual(centers[1], expected_x, delta=1.5)
            self.assertGreater(abs(centers[1] - (left + 10 / 21 * width)), 5)
            self.assertIn("3 intervals", editor._sram_ratio_rows[0]["actual_var"].get())
            editor._sram_press(SimpleNamespace(x=expected_x, y=top + 20))
            self.assertEqual(editor._sram_drag_index, 1)

    def test_illegal_ratio_blocks_refresh_drag_and_serial_write(self) -> None:
        serial = _RecordingSerial()
        ctx = _ctx()
        ctx.serial = serial
        owner = _ConnectedOwner()
        editor = AWP1221SramEditor(self.root, ctx, owner)
        self.addCleanup(editor.destroy)
        editor.update_idletasks()
        editor._sram_ratio_rows[0]["variable"].set("nan")
        editor._apply_sram_values()
        self.assertTrue(editor._sram_input_invalid)
        self.assertIsNone(editor._sram_result)
        self.assertIn("disabled", editor._sram_write_btn.state())
        editor.event_generate("<Configure>")
        editor.refresh_theme()
        ctx.state.language = "zh"
        editor.refresh_language()
        editor._sram_drag_index = 1
        editor._sram_drag(SimpleNamespace(x=80, y=40))
        editor._start_sram_write()
        self.root.update()
        self.assertEqual(serial.manager.writes, [])
        self.assertIsNone(editor._sram_result)
        self.assertIn("disabled", editor._sram_write_btn.state())
        editor._sram_ratio_rows[0]["variable"].set("20")
        editor._sram_ratio_rows[1]["variable"].set("50")
        editor._apply_sram_values()
        self.assertFalse(editor._sram_input_invalid)
        self.assertIsNotNone(editor._sram_result)
        self.assertTrue(editor._sram_write_btn.instate(["!disabled"]))

    def test_unencodable_legal_ratio_still_allows_amplitude_drag(self) -> None:
        serial = _RecordingSerial()
        ctx = _ctx()
        ctx.serial = serial
        editor = AWP1221SramEditor(self.root, ctx, _ConnectedOwner())
        self.addCleanup(editor.destroy)
        editor.sram_values_var.set("0, 100, 0")
        editor._apply_sram_values()
        editor._sram_ratio_rows[0]["variable"].set("1")
        editor._apply_sram_values()
        self.assertFalse(editor._sram_input_invalid)
        self.assertIsNone(editor._sram_result)
        self.assertIn("disabled", editor._sram_write_btn.state())
        before = editor._sram_controls[1]
        _left, top, _width, height = editor._sram_geometry()
        editor._sram_drag_index = 1
        editor._sram_drag(SimpleNamespace(x=_left, y=top + height))
        self.assertNotEqual(editor._sram_controls[1], before)
        self.assertFalse(editor._sram_input_invalid)
        editor._start_sram_write()
        self.root.update()
        self.assertEqual(serial.manager.writes, [])

    def test_invalid_ratio_text_is_rejected_and_not_writable(self) -> None:
        editor = _editor(self.root)
        self.addCleanup(editor.destroy)
        editor.update_idletasks()
        cases = ("", "0", "0.00", "-1", "30.001", "nan", "inf", "abc", "100")
        for text in cases:
            editor._sram_ratio_rows[0]["variable"].set(text)
            editor._apply_sram_values()
            self.assertTrue(editor._sram_input_invalid, text)
            self.assertIsNone(editor._sram_result, text)
            self.assertIn("disabled", editor._sram_write_btn.state())
        editor._sram_ratio_rows[0]["variable"].set("60")
        editor._sram_ratio_rows[1]["variable"].set("40")
        editor._apply_sram_values()
        self.assertTrue(editor._sram_input_invalid)
        self.assertIsNone(editor._sram_result)

    def test_keystroke_does_not_popup(self) -> None:
        editor = _editor(self.root)
        self.addCleanup(editor.destroy)
        editor.update_idletasks()
        with patch("pages.awp1221_sram_editor.messagebox.showerror") as showerror, patch(
            "pages.awp1221_sram_editor.messagebox.showwarning"
        ) as showwarning:
            editor._sram_ratio_rows[0]["variable"].set("")
            editor._sram_ratio_rows[0]["entry"].event_generate("<KeyRelease>")
            self.root.update()
        showerror.assert_not_called()
        showwarning.assert_not_called()
        self.assertIsNotNone(editor._sram_result)

    def test_write_locks_nested_ratio_controls_and_restores(self) -> None:
        serial = _RecordingSerial()
        serial.manager.block = True
        ctx = _ctx()
        ctx.serial = serial
        editor = AWP1221SramEditor(self.root, ctx, _ConnectedOwner())
        self.addCleanup(editor.destroy)
        editor.update_idletasks()
        with patch("pages.awp1221_sram_editor.messagebox.showerror"):
            editor._start_sram_write()
            self.assertTrue(serial.manager.entered.wait(2))
            self.assertTrue(editor._sram_busy)
            for widget in editor._sram_ratio_lock_widgets:
                self.assertEqual(str(widget.cget("state")), "disabled")
            serial.manager.release.set()
            _pump(self.root, lambda: not editor._sram_busy)
        self.assertEqual(str(editor._sram_equal_btn.cget("state")), "normal")
        self.assertEqual(str(editor._sram_ratio_rows[0]["entry"].cget("state")), "normal")
        self.assertEqual(str(editor._sram_ratio_rows[-1]["entry"].cget("state")), "readonly")
        serial.manager.writes.clear()
        serial.manager.fail = True
        serial.manager.block = True
        serial.manager.entered.clear()
        serial.manager.release.clear()
        with patch("pages.awp1221_sram_editor.messagebox.showerror") as showerror:
            editor._start_sram_write()
            self.assertTrue(serial.manager.entered.wait(2))
            self.assertEqual(str(editor._sram_ratio_rows[0]["entry"].cget("state")), "disabled")
            serial.manager.release.set()
            _pump(self.root, lambda: not editor._sram_busy)
            showerror.assert_called()
        self.assertEqual(str(editor._sram_ratio_rows[0]["entry"].cget("state")), "normal")
        self.assertEqual(str(editor._sram_equal_btn.cget("state")), "normal")

    def test_mode_switch_keeps_instance_ratio_and_full_mode_has_rows(self) -> None:
        page = _mode_page(self.root, "button_mode")
        self.addCleanup(page.destroy)
        page.rom_source_var.set("RAM")
        page.update_idletasks()
        editor = page.sram_editor
        editor._sram_ratio_rows[0]["variable"].set("20")
        editor._sram_ratio_rows[1]["variable"].set("50")
        editor._apply_sram_values()
        self.assertEqual(editor._sram_explicit_weights, (2000, 5000, 3000))
        page.rom_waveform_var.set("SINC")
        page.update_idletasks()
        self.assertEqual(editor.grid_info(), {})
        page.rom_waveform_var.set("SINE")
        page.rom_source_var.set("ROM")
        page.rom_source_var.set("RAM")
        page.update_idletasks()
        self.assertEqual(editor._sram_explicit_weights, (2000, 5000, 3000))
        self.assertTrue(editor.grid_info())
        full = _mode_page(self.root, "full_mode")
        self.addCleanup(full.destroy)
        full.update_idletasks()
        self.assertEqual(len(full.sram_editor._sram_ratio_rows), 3)
        self.assertEqual(full.sram_editor._sram_ratio_mode, "equal")


def _capture_bmp(root: tk.Tk, path: Path) -> str | None:
    if ctypes.windll is None:
        return "skipped"
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
            ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
            ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 1)]

    hwnd = ctypes.c_void_p(int(root.winfo_id()))
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return "GetWindowRect failed"
    width = max(1, int(rect.right - rect.left))
    height = max(1, int(rect.bottom - rect.top))
    window_dc = user32.GetWindowDC(hwnd)
    if not window_dc:
        return "GetWindowDC failed"
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    if not memory_dc or not bitmap:
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)
        return "CreateCompatibleBitmap failed"
    old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not gdi32.BitBlt(memory_dc, 0, 0, width, height, window_dc, 0, 0, 0x00CC0020 | 0x40000000):
            return "BitBlt failed"
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        data = (ctypes.c_ubyte * (width * height * 4))()
        if gdi32.GetDIBits(memory_dc, bitmap, 0, height, data, ctypes.byref(info), 0) != height:
            return "GetDIBits failed"
        pixel_bytes = bytes(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_size = 14 + 40 + len(pixel_bytes)
        path.write_bytes(
            b"BM" + struct.pack("<IHHI", file_size, 0, 0, 54)
            + struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, len(pixel_bytes), 0, 0, 0, 0)
            + pixel_bytes
        )
        return None
    finally:
        gdi32.SelectObject(memory_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def _boxes_overlap(first, second, pad: int = 1) -> bool:
    return first[0] < second[2] + pad and second[0] < first[2] + pad and first[1] < second[3] + pad and second[1] < first[3] + pad


def _scroll_into_view(scroll, widget) -> None:
    scroll.update_idletasks()
    canvas = scroll.canvas
    content = scroll.content
    region = [float(item) for item in str(canvas.cget("scrollregion")).split()]
    region_w = max(1.0, region[2] - region[0])
    region_h = max(1.0, region[3] - region[1])
    view_w = max(1, canvas.winfo_width())
    view_h = max(1, canvas.winfo_height())
    x = widget.winfo_rootx() - content.winfo_rootx()
    y = widget.winfo_rooty() - content.winfo_rooty()
    x_max = max(0.0, 1.0 - view_w / region_w)
    y_max = max(0.0, 1.0 - view_h / region_h)
    canvas.xview_moveto(0.0 if region_w <= view_w else max(0.0, min(x_max, (x - 4) / region_w)))
    canvas.yview_moveto(0.0 if region_h <= view_h else max(0.0, min(y_max, (y - 4) / region_h)))
    scroll.update_idletasks()


def _assert_widget_in_viewport(test: unittest.TestCase, viewport: tk.Misc, widget: tk.Misc) -> None:
    test.assertGreater(widget.winfo_width(), 0, widget)
    test.assertGreater(widget.winfo_height(), 0, widget)
    vx0, vy0 = viewport.winfo_rootx(), viewport.winfo_rooty()
    vx1, vy1 = vx0 + viewport.winfo_width(), vy0 + viewport.winfo_height()
    wx0, wy0 = widget.winfo_rootx(), widget.winfo_rooty()
    wx1, wy1 = wx0 + widget.winfo_width(), wy0 + widget.winfo_height()
    test.assertLess(wx0, vx1, widget)
    test.assertGreater(wx1, vx0, widget)
    test.assertLess(wy0, vy1, widget)
    test.assertGreater(wy1, vy0, widget)
    if widget.winfo_width() <= viewport.winfo_width():
        test.assertGreaterEqual(wx0, vx0 - 2, widget)
        test.assertLessEqual(wx1, vx1 + 2, widget)
    if widget.winfo_height() <= viewport.winfo_height():
        test.assertGreaterEqual(wy0, vy0 - 2, widget)
        test.assertLessEqual(wy1, vy1 + 2, widget)


def _assert_axis_labels_do_not_overlap(test: unittest.TestCase, canvas: tk.Canvas) -> None:
    boxes = [canvas.bbox(item) for item in canvas.find_withtag("sram-axis")]
    boxes = [box for box in boxes if box]
    test.assertGreaterEqual(len(boxes), 2)
    for index, first in enumerate(boxes):
        for second in boxes[index + 1:]:
            test.assertFalse(_boxes_overlap(first, second), f"{first} overlaps {second}")


class Awp1221SegmentRatioLayoutTests(unittest.TestCase):
    def test_ratio_controls_stay_reachable_at_default_min_and_zoom(self) -> None:
        evidence = Path(os.environ.get("PIEZO_TEST_SEGMENT_RATIO_ARTIFACTS", "docs/specs/awp1221_segment_ratio_evidence/review"))
        evidence.mkdir(parents=True, exist_ok=True)
        records = []
        cases = (
            ("default", "980x720+40+40", None),
            ("minimum", "420x280+40+40", None),
            ("zoom", "980x720+40+40", 1.75),
        )
        for name, geometry, scaling in cases:
            root = tk.Tk()
            try:
                if scaling is not None:
                    root.tk.call("tk", "scaling", scaling)
                root.geometry(geometry)
                root.deiconify()
                root.rowconfigure(0, weight=1)
                root.columnconfigure(0, weight=1)
                editor = _editor(root)
                root.update()
                editor.sram_values_var.set("0, 10, 30, 50, 70, 90, 0")
                editor._apply_sram_values()
                self.assertEqual(len(editor._sram_ratio_rows), 6)
                scroll = editor._sram_scroll
                scroll.canvas.yview_moveto(0.0)
                scroll.canvas.xview_moveto(0.0)
                root.update()
                editor._draw_sram_waveform()
                _assert_axis_labels_do_not_overlap(self, editor.rom_canvas)
                top_shot = evidence / f"segment_ratio_{name}_top.bmp"
                self.assertIsNone(_capture_bmp(root, top_shot))
                widgets = [
                    editor._sram_ratio_title,
                    editor._sram_equal_btn,
                    editor._sram_points_label,
                    editor._sram_points_entry,
                    editor._sram_write_btn,
                ]
                widgets.extend(row["label"] for row in editor._sram_ratio_rows)
                widgets.extend(row["entry"] for row in editor._sram_ratio_rows)
                reached = []
                for widget in widgets:
                    _scroll_into_view(scroll, widget)
                    root.update()
                    _assert_widget_in_viewport(self, scroll.canvas, widget)
                    reached.append(str(widget.cget("text")) if "text" in widget.keys() else str(widget))
                last = editor._sram_ratio_rows[-1]["entry"]
                _scroll_into_view(scroll, last)
                root.update()
                item_shot = evidence / f"segment_ratio_{name}_row.bmp"
                self.assertIsNone(_capture_bmp(root, item_shot))
                records.append({
                    "name": name,
                    "geometry": geometry,
                    "scaling": scaling,
                    "segments": len(editor._sram_ratio_rows),
                    "reached": reached,
                    "top_screenshot": str(top_shot),
                    "row_screenshot": str(item_shot),
                })
            finally:
                root.destroy()
        (evidence / "geometry.json").write_text(json.dumps(records, indent=2), encoding="utf-8")

    def test_button_and_full_pages_scroll_each_ratio_row_into_view(self) -> None:
        evidence = Path(os.environ.get("PIEZO_TEST_SEGMENT_RATIO_ARTIFACTS", "docs/specs/awp1221_segment_ratio_evidence/review"))
        evidence.mkdir(parents=True, exist_ok=True)
        records = []
        cases = (
            ("button", "button_mode", "1100x760+20+20"),
            ("button_min", "button_mode", "760x520+20+20"),
            ("full", "full_mode", "1100x760+20+20"),
            ("full_min", "full_mode", "760x520+20+20"),
        )
        for name, mode, geometry in cases:
            root = tk.Tk()
            try:
                root.geometry(geometry)
                root.deiconify()
                page = _mode_page(root, mode)
                page.grid(row=0, column=0, sticky="nsew")
                root.rowconfigure(0, weight=1)
                root.columnconfigure(0, weight=1)
                if mode == "button_mode":
                    page.rom_source_var.set("RAM")
                elif hasattr(page, "tools_notebook"):
                    page.tools_notebook.select(0)
                root.update()
                editor = page.sram_editor
                self.assertTrue(editor.winfo_ismapped())
                editor.sram_values_var.set("0, 15, 30, 45, 60, 75, 90, 0")
                editor._apply_sram_values()
                self.assertEqual(len(editor._sram_ratio_rows), 7)
                scroll = editor._sram_scroll
                scroll.canvas.yview_moveto(0.0)
                root.update()
                editor._draw_sram_waveform()
                _assert_axis_labels_do_not_overlap(self, editor.rom_canvas)
                self.assertIsNone(_capture_bmp(root, evidence / f"page_{name}_top.bmp"))
                for row in editor._sram_ratio_rows:
                    _scroll_into_view(scroll, row["label"])
                    root.update()
                    _assert_widget_in_viewport(self, scroll.canvas, row["label"])
                    _scroll_into_view(scroll, row["entry"])
                    root.update()
                    _assert_widget_in_viewport(self, scroll.canvas, row["entry"])
                _scroll_into_view(scroll, editor._sram_write_btn)
                root.update()
                _assert_widget_in_viewport(self, scroll.canvas, editor._sram_write_btn)
                self.assertIsNone(_capture_bmp(root, evidence / f"page_{name}_write.bmp"))
                records.append({"name": name, "mode": mode, "segments": 7, "mapped": True})
            finally:
                root.destroy()
        (evidence / "pages.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
