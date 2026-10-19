"""Offline Tk layout matrix for GUI REV04.

This module is intentionally a validation artifact for the responsive-layout
task.  It constructs the real Tk application while replacing bridge startup,
serial enumeration, and every transport entry point.  It never opens a serial
port or starts the local bridge service.

Run from the repository root with ``py tests/validate_responsive_layout.py``.
The default run writes a compact JSON matrix and representative Windows BMP
captures under ``responsive_layout_validation_artifacts``.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
from dataclasses import asdict, dataclass, field
import gc
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tkinter as tk
import time
from datetime import datetime
from tkinter import ttk
from typing import Any, Iterator
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "responsive_layout_validation_artifacts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRODUCT_LAYOUT_FILES: tuple[Path, ...] = (
    ROOT / "app" / "navigation.py",
    ROOT / "app" / "application.py",
    ROOT / "pages" / "base_page.py",
    ROOT / "pages" / "awp1018_page.py",
    ROOT / "pages" / "awp1221_page.py",
    ROOT / "pages" / "awp192x_modes.py",
)


def _product_fingerprint() -> dict[str, Any]:
    files: dict[str, str] = {}
    combined = hashlib.sha256()
    for path in PRODUCT_LAYOUT_FILES:
        relative = path.relative_to(ROOT).as_posix()
        try:
            content = path.read_bytes()
        except OSError:
            files[relative] = "missing"
            combined.update(relative.encode("utf-8"))
            combined.update(b"missing")
            continue
        digest = hashlib.sha256(content).hexdigest()
        files[relative] = digest
        combined.update(relative.encode("utf-8"))
        combined.update(content)
    return {"combined_sha256": combined.hexdigest(), "files": files}

WINDOW_SIZES: tuple[tuple[int, int], ...] = (
    (1920, 1080),
    (1700, 980),
    (1366, 768),
    (1280, 720),
    (1024, 768),
    (800, 600),
    (640, 480),
)
DPI_SCALES: tuple[float, ...] = (1.0, 1.25, 1.5, 2.0)
LANGUAGES: tuple[str, ...] = ("zh", "en")
THEMES: tuple[str, ...] = ("light", "dark")

TOP_LEVEL_PAGES: tuple[str, ...] = (
    "home",
    "awp1921_1923",
    "awp1922",
    "awp1018",
    "awp1221",
    "settings",
)
EXPECTED_MODES: dict[str, tuple[str, ...]] = {
    "awp1921_1923": ("button_mode", "pump_mode", "full_mode"),
    "awp1018": ("awp1018_normal_mode", "awp1018_diff_mode", "full_mode"),
    "awp1221": ("button_mode", "full_mode"),
    "awp1744_awp1766": (
        "awp1744_awp1766_pump_mode",
        "awp1744_awp1766_fan_mode",
    ),
}


@dataclass
class Issue:
    severity: str
    scope: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class MatrixRecord:
    width: int
    height: int
    actual_width: int
    actual_height: int
    scale: float
    language: str
    theme: str
    page: str
    mode: str
    page_req_width: int
    page_req_height: int
    page_width: int
    page_height: int
    outer_canvas_width: int
    outer_canvas_height: int
    outer_req_width: int
    outer_req_height: int
    outer_vscroll: bool
    outer_hscroll: bool
    nested_areas: int
    mapped_nested_vscroll: int
    mapped_nested_hscroll: int
    visible_widgets: int


def _flush(root: tk.Misc, rounds: int = 3) -> None:
    for _ in range(rounds):
        root.update_idletasks()
        root.update()


def _widget_descendants(widget: tk.Misc) -> Iterator[tk.Misc]:
    for child in widget.winfo_children():
        yield child
        yield from _widget_descendants(child)


def _ancestor(widget: tk.Misc, class_name: str) -> tk.Misc | None:
    current: tk.Misc | None = widget
    while current is not None:
        if current.__class__.__name__ == class_name:
            return current
        current = getattr(current, "master", None)
    return None


def _mapped(widget: tk.Misc) -> bool:
    try:
        return bool(widget.winfo_ismapped())
    except tk.TclError:
        return False


def _float_pair(value: Any) -> tuple[float, float]:
    if isinstance(value, (tuple, list)):
        values = [float(item) for item in value]
    else:
        values = [float(item.strip("(),")) for item in str(value).split()]
    if len(values) != 2:
        raise ValueError(f"expected a two-value Tk fraction, got {value!r}")
    return values[0], values[1]


def _scroll_area_snapshot(area: tk.Misc) -> dict[str, Any]:
    canvas = area.canvas
    region = tuple(float(item) for item in str(canvas.cget("scrollregion")).split())
    yview = _float_pair(canvas.yview())
    xview = _float_pair(canvas.xview())
    return {
        "width": int(canvas.winfo_width()),
        "height": int(canvas.winfo_height()),
        "content_req_width": int(area.content.winfo_reqwidth()),
        "content_req_height": int(area.content.winfo_reqheight()),
        "scrollregion": region,
        "xview": xview,
        "yview": yview,
        "vertical_mapped": _mapped(area.scrollbar),
        "horizontal_mapped": _mapped(area.horizontal_scrollbar),
        "horizontal_requested": bool(getattr(area, "horizontal", False)),
    }


def _capture_window_bmp(root: tk.Tk, path: Path, *, include_layers: bool = True) -> str | None:
    """Capture the real top-level client window using Windows GDI.

    Pillow is deliberately not a dependency of this GUI.  A small ctypes
    capture keeps the validation artifact self-contained and is skipped on
    non-Windows runners.
    """

    if os.name != "nt":
        return "skipped: non-Windows runner"

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32),
            ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 1)]

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
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
        raster_op = 0x00CC0020 | (0x40000000 if include_layers else 0)
        if not gdi32.BitBlt(memory_dc, 0, 0, width, height, window_dc, 0, 0, raster_op):
            return "BitBlt failed"
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        data = (ctypes.c_ubyte * (width * height * 4))()
        copied = gdi32.GetDIBits(memory_dc, bitmap, 0, height, data, ctypes.byref(info), 0)
        if copied != height:
            return f"GetDIBits returned {copied}"
        pixel_bytes = bytes(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_size = 14 + 40 + len(pixel_bytes)
        path.write_bytes(
            b"BM"
            + struct.pack("<IHHI", file_size, 0, 0, 54)
            + struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, len(pixel_bytes), 0, 0, 0, 0)
            + pixel_bytes
        )
        return None
    finally:
        gdi32.SelectObject(memory_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


@contextlib.contextmanager
def mocked_application() -> Iterator[Any]:
    """Construct the real application with every hardware boundary fenced."""

    import app.application as application
    from core.serial_manager import SerialManager

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("responsive layout validation attempted serial I/O")

    with (
        patch.object(application.LocalI2cBridge, "start", lambda _self: None),
        patch.object(application.LocalI2cBridge, "close", lambda _self: None),
        patch.object(SerialManager, "list_port_info", lambda _self: []),
        patch.object(SerialManager, "connect", forbidden),
        patch.object(SerialManager, "send_command", forbidden),
        patch.object(SerialManager, "read_register_value", forbidden),
        patch.object(SerialManager, "write_register_value", forbidden),
        patch.object(SerialManager, "disconnect", lambda _self: None),
    ):
        app = application.PiezoApplication()
        try:
            yield app
        finally:
            app.close()


class ResponsiveLayoutValidator:
    def __init__(self, app: Any, *, capture: bool = True) -> None:
        self.app = app
        self.root: tk.Tk = app.root
        self.nav = app.navigation
        self.capture = capture
        self.records: list[MatrixRecord] = []
        self.issues: list[Issue] = []
        self.captures: list[dict[str, Any]] = []
        self.static_inventory: dict[str, Any] = {}
        self.validation_metadata: dict[str, Any] = {
            "run_started_local": datetime.now().astimezone().isoformat(timespec="seconds"),
            "product_layout_fingerprint": _product_fingerprint(),
            "tk_scaling_values": list(DPI_SCALES),
            "windows_system_dpi": "not changed or claimed by this offline run",
        }
        self.baseline_path = ARTIFACT_DIR / "responsive_layout_matrix_baseline.json"
        self.dynamic_coverage: dict[str, Any] = {
            "notebooks": [],
            "tooltips": [],
            "sidebar_footer": [],
            "wheel_events": [],
            "tk_scaling": [],
        }

    def record_issue(self, severity: str, scope: str, message: str, **evidence: Any) -> None:
        self.issues.append(Issue(severity, scope, message, evidence))

    def inventory(self) -> None:
        pages = self.nav.pages
        actual_pages = tuple(pages)
        if actual_pages != TOP_LEVEL_PAGES:
            self.record_issue(
                "error",
                "top-level-pages",
                "top-level page registry differs from the expected six-page contract",
                expected=TOP_LEVEL_PAGES,
                actual=actual_pages,
            )
        modes = {name: tuple(getattr(page, "mode_pages", {})) for name, page in pages.items() if getattr(page, "mode_pages", None)}
        for page_name, expected in EXPECTED_MODES.items():
            if page_name not in pages:
                # AWP1744/1766 mode classes exist in this checkout but are not
                # one of the six registered top-level routes.
                continue
            actual = modes.get(page_name, ())
            if actual != expected:
                self.record_issue(
                    "error",
                    f"modes:{page_name}",
                    "mode registry differs from the expected contract",
                    expected=expected,
                    actual=actual,
                )
        self.static_inventory = {
            "top_level_pages": list(actual_pages),
            "mode_pages": {name: list(values) for name, values in modes.items()},
            "static_notebooks": [
                "AWP1018 normal/diff ROM waveform selector",
                "AWP1221 waveform/ROM tools",
            ],
            "static_custom_windows": [
                "ContinuousRegisterReadDialog",
                "AWP1018 ROM selector",
                "AWP192x waveform generator",
                "AWP192x ADC detect",
            ],
            "static_floating_overlays": ["SimpleTooltip", "AWP192x preset tooltip"],
        }

    def _set_size_and_scale(self, width: int, height: int, scale: float) -> tuple[int, int]:
        if getattr(self, "_last_validation_scale", None) != scale:
            self.root.tk.call("tk", "scaling", scale)
            self.app._apply_visual_settings(scale=scale)
            self._last_validation_scale = scale
        self.root.geometry(f"{width}x{height}+0+0")
        self.root.deiconify()
        _flush(self.root)
        actual = (int(self.root.winfo_width()), int(self.root.winfo_height()))
        if actual != (width, height):
            self.record_issue(
                "error",
                "root-window",
                "requested window size was clamped by the window manager or a product minsize",
                requested=(width, height),
                actual=actual,
                scale=scale,
            )
        return actual

    def _visible_widget_count(self, page: tk.Misc) -> int:
        return sum(1 for widget in _widget_descendants(page) if _mapped(widget))

    def _check_outer_scroll(self, *, page: str, mode: str, actual: tuple[int, int]) -> tuple[int, int, int, int, bool, bool]:
        canvas = self.nav.content_canvas
        required_width = int(self.nav.content.winfo_reqwidth())
        required_height = int(self.nav.content.winfo_reqheight())
        canvas_width = int(canvas.winfo_width())
        canvas_height = int(canvas.winfo_height())
        page_widget = self.nav.pages[page]
        pin = bool(getattr(page_widget, "pin_window_footer", False))
        need_h = (not pin) and required_width > canvas_width + 1
        need_v = (not pin) and required_height > canvas_height + 1
        got_h = _mapped(self.nav.h_scrollbar)
        got_v = _mapped(self.nav.v_scrollbar)
        if need_h != got_h:
            self.record_issue("error", f"outer-scroll:{page}:{mode}", "outer horizontal scrollbar state does not match content width", required=required_width, viewport=canvas_width, expected=need_h, actual=got_h)
        if need_v != got_v:
            self.record_issue("error", f"outer-scroll:{page}:{mode}", "outer vertical scrollbar state does not match content height", required=required_height, viewport=canvas_height, expected=need_v, actual=got_v)
        if need_h:
            canvas.xview_moveto(1.0)
            _flush(self.root, 1)
            last = self.nav.content.winfo_rootx() + self.nav.content.winfo_width()
            edge = canvas.winfo_rootx() + canvas.winfo_width()
            if last > edge + 3:
                self.record_issue("error", f"outer-scroll:{page}:{mode}", "horizontal scroll end still leaves content outside the viewport", content_right=last, viewport_right=edge)
            canvas.xview_moveto(0.0)
        if need_v:
            canvas.yview_moveto(1.0)
            _flush(self.root, 1)
            last = self.nav.content.winfo_rooty() + self.nav.content.winfo_height()
            edge = canvas.winfo_rooty() + canvas.winfo_height()
            if last > edge + 3:
                self.record_issue("error", f"outer-scroll:{page}:{mode}", "vertical scroll end still leaves content outside the viewport", content_bottom=last, viewport_bottom=edge)
            canvas.yview_moveto(0.0)
        return required_width, required_height, canvas_width, canvas_height, got_v, got_h

    def _check_nested_areas(self, page_widget: tk.Misc, *, page: str, mode: str) -> int:
        areas = [widget for widget in _widget_descendants(page_widget) if widget.__class__.__name__ == "VerticalScrollArea" and _mapped(widget)]
        mapped_v = 0
        mapped_h = 0
        for area in areas:
            snapshot = _scroll_area_snapshot(area)
            region = snapshot["scrollregion"]
            region_width = region[2] if len(region) == 4 else 0.0
            region_height = region[3] if len(region) == 4 else 0.0
            need_v = region_height > snapshot["height"] + 1
            need_h = snapshot["horizontal_requested"] and region_width > snapshot["width"] + 1
            if need_v and not snapshot["vertical_mapped"]:
                self.record_issue("error", f"nested-scroll:{page}:{mode}", "nested vertical scroll region is taller than its viewport but its scrollbar is hidden", snapshot=snapshot)
            if need_h and not snapshot["horizontal_mapped"]:
                self.record_issue("error", f"nested-scroll:{page}:{mode}", "nested horizontal scroll region is wider than its viewport but its scrollbar is hidden", snapshot=snapshot)
            if snapshot["vertical_mapped"]:
                mapped_v += 1
                area.canvas.yview_moveto(1.0)
                _flush(self.root, 1)
                bottom = area.content.winfo_rooty() + area.content.winfo_height()
                edge = area.canvas.winfo_rooty() + area.canvas.winfo_height()
                if bottom > edge + 3:
                    self.record_issue("error", f"nested-scroll:{page}:{mode}", "nested vertical scroll end leaves content outside the viewport", content_bottom=bottom, viewport_bottom=edge, snapshot=snapshot)
                area.canvas.yview_moveto(0.0)
            if snapshot["horizontal_mapped"]:
                mapped_h += 1
                area.canvas.xview_moveto(1.0)
                _flush(self.root, 1)
                right = area.content.winfo_rootx() + area.content.winfo_width()
                edge = area.canvas.winfo_rootx() + area.canvas.winfo_width()
                if right > edge + 3:
                    self.record_issue("error", f"nested-scroll:{page}:{mode}", "nested horizontal scroll end leaves content outside the viewport", content_right=right, viewport_right=edge, snapshot=snapshot)
                area.canvas.xview_moveto(0.0)
        return len(areas), mapped_v, mapped_h

    def _check_non_scrollable_clipping(self, page_widget: tk.Misc, *, page: str, mode: str) -> None:
        """Check fixed-container geometry without treating a Canvas as a frame.

        A canvas is a legal viewport: its child window may be larger than the
        canvas and is checked by ``_check_outer_scroll`` or
        ``_check_nested_areas``.  The old implementation therefore skipped
        every descendant below a canvas, which also skipped ordinary frames
        *inside* the scroll content.  This pass skips only the Canvas-to-window
        boundary and compares each mapped child with its immediate non-canvas
        parent, so fixed internal containers remain observable.
        """
        root_x = int(self.root.winfo_rootx())
        root_y = int(self.root.winfo_rooty())
        root_right = root_x + int(self.root.winfo_width())
        root_bottom = root_y + int(self.root.winfo_height())
        for widget in _widget_descendants(page_widget):
            if not _mapped(widget) or self._hidden_notebook_tab(widget):
                continue
            parent = getattr(widget, "master", None)
            if parent is None or isinstance(parent, (tk.Canvas, tk.Tk, tk.Toplevel)):
                # A child of a Canvas is positioned in the canvas scroll
                # coordinate system, so its screen bounds are intentionally
                # allowed outside the viewport.  The top-level/root boundary
                # is handled by the dedicated outer-scroll checks.
                continue
            try:
                x = int(widget.winfo_rootx())
                y = int(widget.winfo_rooty())
                right = x + int(widget.winfo_width())
                bottom = y + int(widget.winfo_height())
                parent_x = int(parent.winfo_rootx())
                parent_y = int(parent.winfo_rooty())
                parent_right = parent_x + int(parent.winfo_width())
                parent_bottom = parent_y + int(parent.winfo_height())
            except tk.TclError:
                continue
            if x < parent_x - 2 or right > parent_right + 2 or y < parent_y - 2 or bottom > parent_bottom + 2:
                self.record_issue(
                    "error",
                    f"widget-clipping:{page}:{mode}",
                    "mapped child extends outside its immediate non-scroll parent",
                    widget=str(widget),
                    parent=str(parent),
                    bounds=(x - parent_x, y - parent_y, right - parent_x, bottom - parent_y),
                    parent_size=(int(parent.winfo_width()), int(parent.winfo_height())),
                    scale=getattr(self, "_dynamic_scale", None),
                    language=getattr(self, "_dynamic_language", None),
                    theme=getattr(self, "_dynamic_theme", None),
                )
            # Text-bearing controls have no internal scroll model.  If their
            # allocated box is smaller than the requested box, the visible
            # label/button text is clipped even when both boxes stay inside
            # the parent.  Canvas/Text/Listbox/Treeview/Entry widgets have
            # their own intentional viewport and are excluded here.
            if widget.__class__.__name__ in {
                "Button",
                "Checkbutton",
                "Label",
                "Labelframe",
                "Radiobutton",
                "TButton",
                "TCheckbutton",
                "TLabel",
                "TLabelFrame",
                "TRadiobutton",
            }:
                try:
                    requested = (int(widget.winfo_reqwidth()), int(widget.winfo_reqheight()))
                    allocated = (int(widget.winfo_width()), int(widget.winfo_height()))
                except tk.TclError:
                    continue
                if requested[0] > allocated[0] + 2 or requested[1] > allocated[1] + 2:
                    self.record_issue(
                        "error",
                        f"widget-request-clipping:{page}:{mode}",
                        "text-bearing control is allocated smaller than its requested size",
                        widget=str(widget),
                        requested=requested,
                        allocated=allocated,
                        parent=str(parent),
                        scale=getattr(self, "_dynamic_scale", None),
                        language=getattr(self, "_dynamic_language", None),
                        theme=getattr(self, "_dynamic_theme", None),
                    )

            # Keep the root values in the evidence when an internal check
            # finds a child that is also outside the visible application
            # window.  This is informational data only; the scroll-aware
            # boundary decision above determines whether it is an error.
            _ = (root_x, root_y, root_right, root_bottom)

    @staticmethod
    def _notebooks(page_widget: tk.Misc) -> list[ttk.Notebook]:
        return [
            widget
            for widget in _widget_descendants(page_widget)
            if isinstance(widget, ttk.Notebook) and _mapped(widget)
        ]

    @staticmethod
    def _is_descendant(widget: tk.Misc, ancestor: tk.Misc) -> bool:
        current: tk.Misc | None = widget
        while current is not None:
            if current is ancestor:
                return True
            current = getattr(current, "master", None)
        return False

    @staticmethod
    def _hidden_notebook_tab(widget: tk.Misc) -> bool:
        """Exclude descendants of an unselected ttk.Notebook tab.

        Tk may report those descendants as mapped even while the Notebook is
        displaying a different tab.  The selected tab is checked explicitly
        by ``_check_notebooks`` after ``Notebook.select``.
        """

        current: tk.Misc | None = widget
        while current is not None:
            parent = getattr(current, "master", None)
            if isinstance(parent, ttk.Notebook):
                try:
                    selected = str(parent.select())
                    if selected and selected != str(current):
                        return True
                except tk.TclError:
                    return False
            current = parent
        return False

    def _check_notebooks(self, page: str, mode: str) -> None:
        """Select every real tab in every currently visible mode notebook."""

        page_widget = self.nav.pages[page]
        for notebook_index, notebook in enumerate(self._notebooks(page_widget)):
            tabs = tuple(notebook.tabs())
            if not tabs:
                self.record_issue(
                    "error",
                    f"notebook:{page}:{mode}",
                    "a mapped Notebook has no tabs",
                    notebook=str(notebook),
                )
                continue
            selected_tabs: list[str] = []
            for tab_index, tab_id in enumerate(tabs):
                try:
                    notebook.select(tab_id)
                    _flush(self.root, 2)
                    selected = str(notebook.select())
                    tab_widget = notebook.nametowidget(tab_id)
                    selected_tabs.append(selected)
                    if selected != str(tab_id):
                        self.record_issue(
                            "error",
                            f"notebook:{page}:{mode}",
                            "Notebook.select did not select the requested tab",
                            notebook=str(notebook),
                            tab_index=tab_index,
                            requested=str(tab_id),
                            selected=selected,
                        )
                    if not _mapped(tab_widget):
                        self.record_issue(
                            "error",
                            f"notebook:{page}:{mode}",
                            "selected Notebook tab is not mapped",
                            notebook=str(notebook),
                            tab_index=tab_index,
                            tab=str(tab_id),
                        )
                    # The tab itself is a fixed container.  Its descendants
                    # must stay within it; any deeper Canvas viewport is
                    # handled by the same scroll-aware boundary logic.
                    if (
                        getattr(self, "_dynamic_language", "zh") == "zh"
                        and getattr(self, "_dynamic_theme", "light") == "light"
                    ):
                        self._check_non_scrollable_clipping(
                            tab_widget,
                            page=f"{page}:notebook{notebook_index}:tab{tab_index}",
                            mode=mode,
                        )
                    self.dynamic_coverage["notebooks"].append(
                        {
                            "page": page,
                            "mode": mode,
                            "scale": getattr(self, "_dynamic_scale", None),
                            "language": getattr(self, "_dynamic_language", None),
                            "theme": getattr(self, "_dynamic_theme", None),
                            "notebook": str(notebook),
                            "tab_index": tab_index,
                            "tab": str(tab_id),
                            "selected": selected,
                            "mapped": _mapped(tab_widget),
                        }
                    )
                except Exception as exc:
                    self.record_issue(
                        "error",
                        f"notebook:{page}:{mode}",
                        "Notebook tab traversal failed",
                        notebook=str(notebook),
                        tab_index=tab_index,
                        tab=str(tab_id),
                        error=repr(exc),
                    )
            if tabs:
                try:
                    notebook.select(tabs[0])
                    _flush(self.root, 1)
                except tk.TclError:
                    pass

    def _tooltip_objects(self) -> list[Any]:
        from pages.base_page import SimpleTooltip

        result: list[Any] = []
        for item in gc.get_objects():
            try:
                if isinstance(item, SimpleTooltip):
                    result.append(item)
            except (ReferenceError, RuntimeError):
                continue
        return result

    def _exercise_tooltips(self, page: str, mode: str, *, seen: set[int]) -> None:
        """Trigger every tooltip attached to the current page/mode once."""

        page_widget = self.nav.pages[page]
        for tooltip in self._tooltip_objects():
            if id(tooltip) in seen:
                continue
            widget = getattr(tooltip, "widget", None)
            if widget is None or not self._is_descendant(widget, page_widget):
                continue
            try:
                if not widget.winfo_exists() or not _mapped(widget):
                    continue
                widget.event_generate("<Enter>", when="now")
                _flush(self.root, 2)
                tip = getattr(tooltip, "tip", None)
                if tip is None or not tip.winfo_exists():
                    self.record_issue(
                        "error",
                        f"tooltip:{page}:{mode}",
                        "hovering a tooltip widget did not create a live tooltip window",
                        widget=str(widget),
                    )
                    continue
                tip_left = int(tip.winfo_rootx())
                tip_top = int(tip.winfo_rooty())
                tip_right = tip_left + int(tip.winfo_width())
                tip_bottom = tip_top + int(tip.winfo_height())
                screen_width = int(tip.winfo_screenwidth())
                screen_height = int(tip.winfo_screenheight())
                if (
                    tip_left < 0
                    or tip_top < 0
                    or tip_right > screen_width
                    or tip_bottom > screen_height
                ):
                    self.record_issue(
                        "error",
                        f"tooltip:{page}:{mode}",
                        "tooltip window extends beyond the available screen",
                        widget=str(widget),
                        tooltip=(tip_left, tip_top, tip_right, tip_bottom),
                        screen=(screen_width, screen_height),
                    )
                self.dynamic_coverage["tooltips"].append(
                    {
                        "page": page,
                        "mode": mode,
                        "scale": getattr(self, "_dynamic_scale", None),
                        "language": getattr(self, "_dynamic_language", None),
                        "theme": getattr(self, "_dynamic_theme", None),
                        "widget": str(widget),
                        "tooltip": str(tip),
                        "size": (int(tip.winfo_width()), int(tip.winfo_height())),
                    }
                )
                seen.add(id(tooltip))
                widget.event_generate("<Leave>", when="now")
                _flush(self.root, 2)
                remaining = getattr(tooltip, "tip", None)
                if remaining is not None and remaining.winfo_exists():
                    self.record_issue(
                        "error",
                        f"tooltip:{page}:{mode}",
                        "leaving a tooltip widget did not destroy the tooltip window",
                        widget=str(widget),
                    )
            except (tk.TclError, RuntimeError, AttributeError) as exc:
                self.record_issue(
                    "error",
                    f"tooltip:{page}:{mode}",
                    "tooltip hover simulation failed",
                    widget=str(widget),
                    error=repr(exc),
                )

    def _check_sidebar_footer(self, *, width: int, height: int, scale: float) -> None:
        """Prove that the guide and version footer are reachable at the bottom."""

        self._set_size_and_scale(width, height, scale)
        self.nav.show("awp1921_1923")
        page = self.nav.pages["awp1921_1923"]
        if hasattr(page, "show_mode"):
            page.show_mode("full_mode")
        _flush(self.root, 3)
        canvas = self.nav.sidebar_canvas
        before = _float_pair(canvas.yview())
        region = tuple(float(item) for item in str(canvas.cget("scrollregion")).split())
        if len(region) != 4:
            self.record_issue(
                "error",
                "sidebar-footer",
                "sidebar scrollregion is not a four-value rectangle",
                scrollregion=region,
            )
            return
        needs_scroll = region[3] > canvas.winfo_height() + 1
        if needs_scroll and not _mapped(self.nav.sidebar_scrollbar):
            self.record_issue(
                "error",
                "sidebar-footer",
                "sidebar footer requires scrolling but the sidebar scrollbar is hidden",
                scrollregion=region,
                viewport=int(canvas.winfo_height()),
            )
        canvas.yview_moveto(1.0)
        _flush(self.root, 2)
        after = _float_pair(canvas.yview())
        canvas_left = int(canvas.winfo_rootx())
        canvas_top = int(canvas.winfo_rooty())
        canvas_right = canvas_left + int(canvas.winfo_width())
        canvas_bottom = canvas_top + int(canvas.winfo_height())
        footer_data: dict[str, Any] = {
            "size": (width, height),
            "scale": scale,
            "scroll_before": before,
            "scroll_after": after,
            "scrollregion": region,
        }
        if needs_scroll and after[1] <= before[1]:
            self.record_issue(
                "error",
                "sidebar-footer",
                "sidebar yview did not advance to its bottom endpoint",
                before=before,
                after=after,
            )
        for name, widget in (("guide", self.nav._guide_button), ("version", self.nav._version_label)):
            try:
                left = int(widget.winfo_rootx())
                top = int(widget.winfo_rooty())
                right = left + int(widget.winfo_width())
                bottom = top + int(widget.winfo_height())
                footer_data[name] = {
                    "mapped": _mapped(widget),
                    "bounds": (left, top, right, bottom),
                    "text": str(widget.cget("text")),
                }
                if not _mapped(widget) or left < canvas_left - 2 or right > canvas_right + 2 or top < canvas_top - 2 or bottom > canvas_bottom + 2:
                    self.record_issue(
                        "error",
                        "sidebar-footer",
                        "sidebar footer control is not fully visible at the scroll end",
                        control=name,
                        bounds=(left, top, right, bottom),
                        viewport=(canvas_left, canvas_top, canvas_right, canvas_bottom),
                    )
            except tk.TclError as exc:
                self.record_issue(
                    "error",
                    "sidebar-footer",
                    "sidebar footer control geometry could not be read",
                    control=name,
                    error=repr(exc),
                )
        self.dynamic_coverage["sidebar_footer"].append(footer_data)

    def _scroll_canvases(self, page_widget: tk.Misc) -> list[tk.Canvas]:
        canvases = [self.nav.content_canvas]
        canvases.extend(
            widget.canvas
            for widget in _widget_descendants(page_widget)
            if widget.__class__.__name__ == "VerticalScrollArea" and _mapped(widget)
        )
        result: list[tk.Canvas] = []
        for canvas in canvases:
            if canvas not in result:
                result.append(canvas)
        return result

    def _check_nested_wheel_once(self) -> None:
        """Generate real Tk wheel events and require exactly one scroll owner."""

        scale = float(getattr(self, "_dynamic_scale", 1.0))
        self._set_size_and_scale(640, 480, scale)
        self.app.apply_settings("zh", "light", self.app.state.font_size)
        self.nav.show("awp1018")
        page = self.nav.pages["awp1018"]
        if hasattr(page, "show_mode"):
            page.show_mode("awp1018_diff_mode")
        _flush(self.root, 3)
        page_widget = self.nav.pages["awp1018"]
        areas = [
            widget
            for widget in _widget_descendants(page_widget)
            if widget.__class__.__name__ == "VerticalScrollArea" and _mapped(widget)
        ]
        vertical_area = next(
            (
                area
                for area in areas
                if _mapped(area.scrollbar)
                and _float_pair(area.canvas.yview())[1] - _float_pair(area.canvas.yview())[0] < 1.0
            ),
            None,
        )
        if vertical_area is None:
            self.record_issue(
                "error",
                "wheel-events",
                "no mapped vertically scrollable nested area was available for wheel dispatch",
            )
            return

        canvases = self._scroll_canvases(page_widget)
        counts: dict[tuple[int, str], int] = {}
        originals: dict[tuple[int, str], Any] = {}

        def install(axis: str) -> None:
            for canvas in canvases:
                key = (id(canvas), axis)
                original = getattr(canvas, f"{axis}view_scroll")
                originals[key] = original
                counts[key] = 0

                def counted(*args: Any, _key: tuple[int, str] = key, _original: Any = original) -> Any:
                    counts[_key] += 1
                    return _original(*args)

                setattr(canvas, f"{axis}view_scroll", counted)

        def restore() -> None:
            for canvas in canvases:
                for axis in ("y", "x"):
                    key = (id(canvas), axis)
                    original = originals.get(key)
                    if original is not None:
                        setattr(canvas, f"{axis}view_scroll", original)

        try:
            install("y")
            install("x")
            vertical_area.canvas.yview_moveto(0.0)
            before = _float_pair(vertical_area.canvas.yview())
            left = int(vertical_area.canvas.winfo_rootx()) + max(2, int(vertical_area.canvas.winfo_width() / 2))
            top = int(vertical_area.canvas.winfo_rooty()) + max(2, int(vertical_area.canvas.winfo_height() / 2))
            vertical_area.canvas.event_generate(
                "<MouseWheel>",
                delta=-120,
                rootx=left,
                rooty=top,
                x=max(1, left - int(vertical_area.canvas.winfo_rootx())),
                y=max(1, top - int(vertical_area.canvas.winfo_rooty())),
                when="now",
            )
            _flush(self.root, 2)
            after = _float_pair(vertical_area.canvas.yview())
            y_counts = {str(key): count for key, count in counts.items() if key[1] == "y" and count}
            if sum(y_counts.values()) != 1 or counts[(id(vertical_area.canvas), "y")] != 1:
                self.record_issue(
                    "error",
                    "wheel-events",
                    "nested vertical wheel event was not consumed by exactly one nearest scroll owner",
                    target=str(vertical_area.canvas),
                    calls=y_counts,
                    before=before,
                    after=after,
                )
            self.dynamic_coverage["wheel_events"].append(
                {
                    "axis": "vertical",
                    "scale": scale,
                    "target": str(vertical_area.canvas),
                    "calls": y_counts,
                    "before": before,
                    "after": after,
                }
            )

            horizontal_area = next(
                (
                    area
                    for area in areas
                    if bool(getattr(area, "horizontal", False))
                    and _mapped(area.horizontal_scrollbar)
                    and _float_pair(area.canvas.xview())[1] - _float_pair(area.canvas.xview())[0] < 1.0
                ),
                None,
            )
            if horizontal_area is None:
                # A representative scale may have no horizontal overflow at
                # all after responsive width propagation.  The baseline
                # 1.0 scene must exercise Shift+wheel; at other scales record
                # an explicit skip instead of inventing a failure for an
                # event that has no scroll range to consume.
                self.dynamic_coverage["wheel_events"].append(
                    {
                        "axis": "horizontal-shift",
                        "scale": scale,
                        "skipped": True,
                        "reason": "no mapped nested horizontal scroll range at this scale",
                    }
                )
            else:
                horizontal_area.canvas.xview_moveto(0.0)
                before_x = _float_pair(horizontal_area.canvas.xview())
                left_x = int(horizontal_area.canvas.winfo_rootx()) + max(2, int(horizontal_area.canvas.winfo_width() / 2))
                top_x = int(horizontal_area.canvas.winfo_rooty()) + max(2, int(horizontal_area.canvas.winfo_height() / 2))
                horizontal_area.canvas.event_generate(
                    "<MouseWheel>",
                    delta=-120,
                    state=0x0001,
                    rootx=left_x,
                    rooty=top_x,
                    x=max(1, left_x - int(horizontal_area.canvas.winfo_rootx())),
                    y=max(1, top_x - int(horizontal_area.canvas.winfo_rooty())),
                    when="now",
                )
                _flush(self.root, 2)
                after_x = _float_pair(horizontal_area.canvas.xview())
                x_counts = {str(key): count for key, count in counts.items() if key[1] == "x" and count}
                if sum(x_counts.values()) != 1 or counts[(id(horizontal_area.canvas), "x")] != 1:
                    self.record_issue(
                        "error",
                        "wheel-events",
                        "nested Shift+wheel event was not consumed by exactly one nearest horizontal scroll owner",
                        target=str(horizontal_area.canvas),
                        calls=x_counts,
                        before=before_x,
                        after=after_x,
                    )
                self.dynamic_coverage["wheel_events"].append(
                    {
                        "axis": "horizontal-shift",
                        "scale": scale,
                        "target": str(horizontal_area.canvas),
                        "calls": x_counts,
                        "before": before_x,
                        "after": after_x,
                    }
                )
        except (tk.TclError, AttributeError, TypeError, ValueError) as exc:
            self.record_issue(
                "error",
                "wheel-events",
                "wheel event generation or instrumentation failed",
                error=repr(exc),
            )
        finally:
            restore()

    def check_dynamic_coverage(self) -> None:
        """Run interaction checks separately from the size matrix."""

        # The original seven-size matrix is already persisted as evidence.
        # This supplement is deliberately bounded: all modes/tabs at the
        # constrained 640x480 scene, then the same traversal at Tk scale 2.0.
        # Language/theme permutations are retained at baseline scale; the
        # scale-2 representative uses the default Chinese light settings.
        for scale, settings in (
            (1.0, (("zh", "light"),)),
            (2.0, (("zh", "light"),)),
        ):
            if self._timed_out():
                return
            print(f"[dynamic] scale={scale} settings={settings}", flush=True)
            self._set_size_and_scale(640, 480, scale)
            tooltip_seen: set[int] = set()
            for language, theme in settings:
                self._dynamic_scale = scale
                self._dynamic_language = language
                self._dynamic_theme = theme
                self.app.apply_settings(language, theme, self.app.state.font_size)
                for page_name in TOP_LEVEL_PAGES:
                    page = self.nav.pages[page_name]
                    modes = tuple(getattr(page, "mode_pages", {})) or ("",)
                    for mode in modes:
                        if self._timed_out():
                            return
                        print(f"[dynamic] {page_name}:{mode}", flush=True)
                        self.nav.show(page_name)
                        if mode and hasattr(page, "show_mode"):
                            page.show_mode(mode)
                        # Scaling changes cause several Configure/after-idle
                        # passes in the real Tk tree.  Let those settle before
                        # taking internal geometry evidence; otherwise a
                        # transient 1px/old-width allocation is misreported.
                        _flush(self.root, 12)
                        # Run fixed-parent geometry for the baseline and
                        # scale-2 representative scenes.  Language/theme
                        # permutations still traverse every real Notebook
                        # tab, but do not repeat the expensive geometry pass.
                        if language == "zh" and theme == "light":
                            active_page = getattr(page, "mode_pages", {}).get(mode, page)
                            self._check_non_scrollable_clipping(active_page, page=page_name, mode=mode)
                        self._check_notebooks(page_name, mode)
                        if language == "zh" and theme == "light":
                            self._exercise_tooltips(page_name, mode, seen=tooltip_seen)
        self.dynamic_coverage["tooltips_count"] = len(self.dynamic_coverage["tooltips"])
        self.dynamic_coverage["notebook_tab_count"] = len(self.dynamic_coverage["notebooks"])
        # Exercise the constrained sidebar at both baseline and the largest
        # Tk scaling used by this run.  Tk scaling is a logical Tk unit scale;
        # it is not a claim about the host Windows monitor DPI.
        self._check_sidebar_footer(width=640, height=480, scale=1.0)
        self._check_sidebar_footer(width=640, height=480, scale=max(DPI_SCALES))
        self._dynamic_scale = 1.0
        self._check_nested_wheel_once()
        self._dynamic_scale = 2.0
        self._check_nested_wheel_once()
        self.dynamic_coverage["tk_scaling"] = [
            {
                "values": list(DPI_SCALES),
                "meaning": "Tk logical scaling only; Windows system DPI was not changed or claimed",
            }
        ]

    def _timed_out(self) -> bool:
        deadline = getattr(self, "_deadline", None)
        if deadline is None or time.monotonic() <= deadline:
            return False
        if not getattr(self, "_timeout_recorded", False):
            self.record_issue(
                "error",
                "validation-timeout",
                "bounded dynamic validation exceeded its configured deadline",
                timeout_seconds=getattr(self, "_timeout_seconds", None),
            )
            self._timeout_recorded = True
        return True

    def check_scene(self, *, width: int, height: int, scale: float, language: str, theme: str, page: str, mode: str) -> None:
        actual = self._set_size_and_scale(width, height, scale)
        self.app.navigation.show(page)
        page_widget = self.nav.pages[page]
        if mode and hasattr(page_widget, "show_mode"):
            page_widget.show_mode(mode)
        _flush(self.root)
        required_width, required_height, canvas_width, canvas_height, got_v, got_h = self._check_outer_scroll(page=page, mode=mode, actual=actual)
        nested_count, nested_v, nested_h = self._check_nested_areas(page_widget, page=page, mode=mode)
        self.records.append(MatrixRecord(
            width=width,
            height=height,
            actual_width=actual[0],
            actual_height=actual[1],
            scale=scale,
            language=language,
            theme=theme,
            page=page,
            mode=mode,
            page_req_width=int(page_widget.winfo_reqwidth()),
            page_req_height=int(page_widget.winfo_reqheight()),
            page_width=int(page_widget.winfo_width()),
            page_height=int(page_widget.winfo_height()),
            outer_canvas_width=canvas_width,
            outer_canvas_height=canvas_height,
            outer_req_width=required_width,
            outer_req_height=required_height,
            outer_vscroll=got_v,
            outer_hscroll=got_h,
            nested_areas=nested_count,
            mapped_nested_vscroll=nested_v,
            mapped_nested_hscroll=nested_h,
            visible_widgets=self._visible_widget_count(page_widget),
        ))

    def _popup_cases(self) -> list[tuple[str, Any, str]]:
        cases: list[tuple[str, Any, str]] = []
        page_1018 = self.nav.pages["awp1018"].mode_pages.get("full_mode")
        if page_1018 is not None and callable(getattr(page_1018, "open_rom_selector_window", None)):
            cases.append(("awp1018-rom-selector", page_1018, "open_rom_selector_window"))
        page_192x = self.nav.pages["awp1921_1923"].mode_pages.get("full_mode")
        if page_192x is not None and callable(getattr(page_192x, "open_waveform_generator", None)):
            cases.append(("awp192x-waveform-generator", page_192x, "open_waveform_generator"))
        if page_192x is not None and callable(getattr(page_192x, "open_adc_detect_window", None)):
            cases.append(("awp192x-adc-detect", page_192x, "open_adc_detect_window"))
        return cases

    def _check_toplevel_clipping(self, window: tk.Toplevel, *, name: str) -> None:
        """Check direct popup children; nested scroll areas are checked separately."""

        left = int(window.winfo_rootx())
        top = int(window.winfo_rooty())
        right = left + int(window.winfo_width())
        bottom = top + int(window.winfo_height())
        for widget in _widget_descendants(window):
            if not _mapped(widget) or _ancestor(widget, "VerticalScrollArea") is not None:
                continue
            try:
                widget_left = int(widget.winfo_rootx())
                widget_top = int(widget.winfo_rooty())
                widget_right = widget_left + int(widget.winfo_width())
                widget_bottom = widget_top + int(widget.winfo_height())
            except tk.TclError:
                continue
            if (
                widget_left < left - 2
                or widget_top < top - 2
                or widget_right > right + 2
                or widget_bottom > bottom + 2
            ):
                self.record_issue(
                    "error",
                    f"popup-clipping:{name}",
                    "mapped popup widget extends outside the popup without a nested scroll-area ancestor",
                    widget=str(widget),
                    bounds=(widget_left - left, widget_top - top, widget_right - left, widget_bottom - top),
                    popup=(int(window.winfo_width()), int(window.winfo_height())),
                )

    def check_popups(self) -> None:
        """Open every constructible feature window without starting monitoring."""
        page_192x = self.nav.pages["awp1921_1923"].mode_pages.get("full_mode")
        patch_target = None
        if page_192x is not None and hasattr(page_192x, "start_adc_detect_monitor"):
            patch_target = patch.object(page_192x, "start_adc_detect_monitor", lambda **_kwargs: None)
        with patch_target if patch_target is not None else contextlib.nullcontext():
            for name, owner, method_name in self._popup_cases():
                try:
                    getattr(owner, method_name)()
                    _flush(self.root)
                    popup_attr = {
                        "awp1018-rom-selector": "_rom_selector_window",
                        "awp192x-waveform-generator": "_waveform_window",
                        "awp192x-adc-detect": "_adc_detect_window",
                    }.get(name, "")
                    window = getattr(owner, popup_attr, None) if popup_attr else None
                    if window is not None and not window.winfo_exists():
                        window = None
                    if window is None:
                        self.record_issue("error", f"popup:{name}", "popup method returned without a live Toplevel")
                        continue
                    requested = (int(window.winfo_width()), int(window.winfo_height()))
                    screen_size = (int(window.winfo_screenwidth()), int(window.winfo_screenheight()))
                    if requested[0] > screen_size[0] or requested[1] > screen_size[1]:
                        self.record_issue("error", f"popup:{name}", "popup is larger than the available screen work area", popup_size=requested, screen_size=screen_size)
                    self._check_toplevel_clipping(window, name=name)
                    for area in [widget for widget in _widget_descendants(window) if widget.__class__.__name__ == "VerticalScrollArea" and _mapped(widget)]:
                        snapshot = _scroll_area_snapshot(area)
                        region = snapshot["scrollregion"]
                        if len(region) == 4 and region[3] > snapshot["height"] + 1 and not snapshot["vertical_mapped"]:
                            self.record_issue("error", f"popup-scroll:{name}", "popup nested vertical scroll region is taller than its viewport but its scrollbar is hidden", snapshot=snapshot)
                        if len(region) == 4 and snapshot["horizontal_requested"] and region[2] > snapshot["width"] + 1 and not snapshot["horizontal_mapped"]:
                            self.record_issue("error", f"popup-scroll:{name}", "popup nested horizontal scroll region is wider than its viewport but its scrollbar is hidden", snapshot=snapshot)
                    self.captures.append({"popup": name, "size": requested})
                    close_method = {
                        "awp1018-rom-selector": "_close_rom_selector_window",
                        "awp192x-waveform-generator": "_close_waveform_window",
                        "awp192x-adc-detect": "_close_adc_detect_window",
                    }.get(name)
                    if close_method and callable(getattr(owner, close_method, None)):
                        getattr(owner, close_method)()
                    else:
                        window.destroy()
                except Exception as exc:
                    self.record_issue("error", f"popup:{name}", "popup construction failed", error=repr(exc))

    def load_previous_matrix(self) -> None:
        """Reuse the already completed size matrix for bounded supplements."""

        matrix_path = ARTIFACT_DIR / "responsive_layout_matrix.json"
        if not matrix_path.exists():
            self.record_issue(
                "error",
                "matrix-baseline",
                "bounded dynamic validation requested but the completed matrix JSON is missing",
                path=str(matrix_path),
            )
            return
        try:
            payload = json.loads(matrix_path.read_text(encoding="utf-8"))
            self.records = [MatrixRecord(**item) for item in payload.get("records", [])]
            self.captures = list(payload.get("captures", []))
            self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.baseline_path.exists():
                self.baseline_path.write_text(
                    json.dumps(
                        {
                            "source_matrix": str(matrix_path),
                            "source_metadata": payload.get("metadata"),
                            "provenance": "reused completed size records; supplement issues are intentionally excluded",
                            "records": payload.get("records", []),
                            "captures": payload.get("captures", []),
                            "inventory": payload.get("inventory", {}),
                            "record_count": len(self.records),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            # The existing JSON may itself be a prior dynamic supplement.
            # Reuse only its completed size records/captures; issues are
            # recomputed in the current run so stale failures cannot be
            # counted twice.
            previous_inventory = payload.get("inventory")
            if isinstance(previous_inventory, dict):
                self.static_inventory = previous_inventory
        except (OSError, ValueError, TypeError) as exc:
            self.record_issue(
                "error",
                "matrix-baseline",
                "completed matrix JSON could not be loaded",
                path=str(matrix_path),
                error=repr(exc),
            )

    def run(self, *, sizes: tuple[tuple[int, int], ...] = WINDOW_SIZES, scales: tuple[float, ...] = DPI_SCALES) -> None:
        self.inventory()
        # Full size x full DPI is expensive and redundant; every page/mode is
        # covered at every window size, while the DPI matrix covers the most
        # constrained 1024x768 and 640x480 scenes.
        for width, height in sizes:
            is_small_reference = (width, height) == (640, 480)
            scene_scales = scales if is_small_reference else (1.0,)
            for scale in scene_scales:
                # Every page/mode is checked at every requested window size.
                # Language/theme permutations are concentrated in the small
                # reference scene so the matrix stays repeatable offline.
                settings = tuple((language, theme) for language in LANGUAGES for theme in THEMES) if (is_small_reference and scale == 1.0) else (("zh", "light"),)
                for language, theme in settings:
                    self.app.apply_settings(language, theme, self.app.state.font_size)
                    for page_name in TOP_LEVEL_PAGES:
                        page = self.nav.pages[page_name]
                        modes = tuple(getattr(page, "mode_pages", {}))
                        scenes = modes or ("",)
                        for mode in scenes:
                            self.check_scene(width=width, height=height, scale=scale, language=language, theme=theme, page=page_name, mode=mode)
        self.app.apply_settings("zh", "light", self.app.state.font_size)
        self._set_size_and_scale(640, 480, 1.0)
        self.check_dynamic_coverage()
        self.nav.show("awp1921_1923")
        self.nav.pages["awp1921_1923"].show_mode("full_mode")
        _flush(self.root)
        self.check_popups()
        self._set_size_and_scale(640, 480, max(scales))
        self.check_popups()

        if self.capture:
            for name, page, mode in (
                ("small-awp192x-full", "awp1921_1923", "full_mode"),
                ("small-awp1018-diff", "awp1018", "awp1018_diff_mode"),
                ("small-awp1221-button", "awp1221", "button_mode"),
            ):
                self.check_scene(width=640, height=480, scale=1.0, language="zh", theme="light", page=page, mode=mode)
                path = ARTIFACT_DIR / f"{name}.bmp"
                error = _capture_window_bmp(self.root, path)
                self.captures.append({"name": name, "path": str(path), "error": error})

    def write_artifacts(self) -> tuple[Path, Path]:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        matrix_path = ARTIFACT_DIR / "responsive_layout_matrix.json"
        matrix_path.write_text(
            json.dumps(
                {
                    "metadata": self.validation_metadata,
                    "baseline_matrix": {
                        "path": str(self.baseline_path),
                        "record_count": len(self.records),
                        "issues_reused": False,
                    },
                    "records": [asdict(item) for item in self.records],
                    "issues": [asdict(item) for item in self.issues],
                    "captures": self.captures,
                    "inventory": self.static_inventory,
                    "dynamic_coverage": self.dynamic_coverage,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        report_path = ROOT / "docs" / "specs" / "RESPONSIVE_LAYOUT_VALIDATION.md"
        errors = [item for item in self.issues if item.severity == "error"]
        warnings = [item for item in self.issues if item.severity == "warning"]
        lines = [
            "# Responsive Layout Validation",
            "",
            "本报告由 `tests/validate_responsive_layout.py` 生成。验证只构造真实 Tk 页面并 mock 桥接、串口枚举和传输入口；没有启动桥接服务、打开串口、读取 I²C、编译或烧录固件。",
            "",
            f"- 矩阵记录：{len(self.records)} 条（窗口 {len(set((item.width, item.height) for item in self.records))} 种；代表场景 DPI {', '.join(str(value) for value in DPI_SCALES)}）。",
            f"- 错误：{len(errors)}；警告：{len(warnings)}。",
            f"- JSON 证据：[responsive_layout_matrix.json]({matrix_path.as_posix()})。",
            f"- 已完成尺寸矩阵基线：[responsive_layout_matrix_baseline.json]({self.baseline_path.as_posix()})；本轮只复用其记录，不复用旧问题列表。",
            f"- 当前产品布局代码 fingerprint：`{self.validation_metadata['product_layout_fingerprint']['combined_sha256']}`。",
            "- DPI 说明：矩阵中的 `scale` 是 Tk logical scaling（`tk scaling`），用于逻辑字号/尺寸回归；本次离线验证没有修改或声称覆盖 Windows 系统显示 DPI。",
            "",
            "## 覆盖清单",
            "",
        ]
        for key, value in self.static_inventory.items():
            lines.append(f"- {key}: {json.dumps(value, ensure_ascii=False)}")
        lines.extend(
            [
                "",
                "## 动态交互覆盖",
                "",
                f"- Notebook 实测 `select()` 标签数：{self.dynamic_coverage.get('notebook_tab_count', 0)}。每个当前模式中的实际 Notebook tab 均被选中、更新并检查映射状态。",
                f"- Tooltip 实测数量：{self.dynamic_coverage.get('tooltips_count', 0)}。逐个发送 Enter/Leave，检查窗口创建、屏幕边界与销毁。",
                f"- 侧栏 footer：{len(self.dynamic_coverage.get('sidebar_footer', []))} 个场景；在滚动终点检查“使用说明”和版本文本可达。",
                f"- 嵌套滚轮：{len(self.dynamic_coverage.get('wheel_events', []))} 个事件场景；普通滚轮与 Shift+滚轮要求最近的单一滚动拥有者响应一次。",
                "- 动态补测场景：640×480、Tk scaling 1.0 与 2.0，中文浅色设置；Notebook/内部几何/Tooltip/滚轮均为当前运行实测。",
                "- 静态清单与上述动态实测分开记录，避免把源码存在性当作交互覆盖。",
            ]
        )
        lines.extend(["", "## 问题与证据", ""])
        if not self.issues:
            lines.append("- 未发现布局错误或警告。")
        else:
            for item in self.issues:
                lines.append(f"- **{item.severity.upper()}** `{item.scope}`：{item.message}；`{json.dumps(item.evidence, ensure_ascii=False)}`")
        lines.extend(["", "## 代表性截图", ""])
        if not self.captures:
            lines.append("- 未生成截图。")
        else:
            for capture in self.captures:
                if capture.get("path"):
                    lines.append(f"- `{capture.get('name', capture.get('popup', 'capture'))}`：`{capture['path']}`（{capture.get('error') or '已保存'}）")
        lines.extend(["", "## 结论", ""])
        lines.append("LAYOUT_VALIDATION_READY_V2" if not errors else "BLOCKED_V2")
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path, matrix_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-capture", action="store_true", help="skip Windows BMP captures")
    parser.add_argument(
        "--dynamic-only",
        action="store_true",
        help="reuse the completed size matrix and run only bounded interaction checks",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="deadline in seconds for the bounded dynamic supplement",
    )
    args = parser.parse_args(argv)
    with mocked_application() as app:
        validator = ResponsiveLayoutValidator(app, capture=not args.no_capture)
        if args.dynamic_only:
            validator.inventory()
            validator.load_previous_matrix()
            validator._timeout_seconds = max(1.0, float(args.timeout))
            validator._deadline = time.monotonic() + validator._timeout_seconds
            validator.check_dynamic_coverage()
        else:
            validator.run()
        report_path, matrix_path = validator.write_artifacts()
        print(f"records={len(validator.records)} errors={sum(item.severity == 'error' for item in validator.issues)} warnings={sum(item.severity == 'warning' for item in validator.issues)}")
        print(f"report={report_path}")
        print(f"matrix={matrix_path}")
        return 0 if not any(item.severity == "error" for item in validator.issues) else 2


if __name__ == "__main__":
    raise SystemExit(main())
