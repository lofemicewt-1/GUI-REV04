"""Offline visual check for chip and mode selected colors.

Constructs the real Tk application with LocalI2cBridge.start and serial
enumeration replaced. Does not open a port or start the bridge.
"""

from __future__ import annotations

import argparse
import ctypes
import inspect
import json
import struct
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PAGES = ("awp1221", "awp1018", "awp1921_1923")


def _rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def _pixel(image: Any, x: int, y: int) -> tuple[int, int, int]:
    raw = image.get(x, y)
    if isinstance(raw, str):
        parts = raw.replace("#", "").split()
        if len(parts) == 1 and len(parts[0]) == 6:
            return _rgb(parts[0])
        return tuple(int(part) for part in parts[:3])
    return (int(raw[0]), int(raw[1]), int(raw[2]))


def _near(left: tuple[int, int, int], right: tuple[int, int, int], tolerance: int = 18) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(left, right))


def _pump(root: Any, rounds: int = 4) -> None:
    for _ in range(rounds):
        root.update_idletasks()
        root.update()


def _show_mode(page: Any, key: str) -> None:
    show = page.show_mode
    if "update_chip" in inspect.signature(show).parameters:
        show(key, update_chip=False)
    else:
        show(key)


def _capture(root: Any) -> tuple[tuple[int, int, int, int], bytes, int, int] | str:
    if sys.platform != "win32":
        return "skipped: non-Windows runner"

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

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
        printed = user32.PrintWindow(hwnd, memory_dc, 2)
        if not printed and not gdi32.BitBlt(memory_dc, 0, 0, width, height, window_dc, 0, 0, 0x00CC0020 | 0x40000000):
            return "capture failed"
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
        return ((int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)), bytes(data), width, height)
    finally:
        gdi32.SelectObject(memory_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def _write_bmp(path: Path, data: bytes, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_size = 14 + 40 + len(data)
    path.write_bytes(
        b"BM"
        + struct.pack("<IHHI", file_size, 0, 0, 54)
        + struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, len(data), 0, 0, 0, 0)
        + data
    )


def _crop(capture: tuple[Any, ...], widget: Any) -> tuple[bytes, int, int, tuple[int, int, int]] | str:
    rect, data, width, height = capture
    x0 = int(widget.winfo_rootx()) - int(rect[0])
    y0 = int(widget.winfo_rooty()) - int(rect[1])
    crop_w = max(1, int(widget.winfo_width()))
    crop_h = max(1, int(widget.winfo_height()))
    if x0 < 0 or y0 < 0 or x0 + crop_w > width or y0 + crop_h > height:
        return f"widget outside capture x={x0} y={y0} w={crop_w} h={crop_h} cap={width}x{height}"
    out = bytearray(crop_w * crop_h * 4)
    for row in range(crop_h):
        src_y = height - 1 - (y0 + row)
        src = src_y * width * 4 + x0 * 4
        dst_y = crop_h - 1 - row
        dst = dst_y * crop_w * 4
        out[dst : dst + crop_w * 4] = data[src : src + crop_w * 4]
    center_x = crop_w // 2
    center_y = crop_h // 2
    src_y = height - 1 - (y0 + center_y)
    offset = src_y * width * 4 + (x0 + center_x) * 4
    blue, green, red = data[offset], data[offset + 1], data[offset + 2]
    return (bytes(out), crop_w, crop_h, (int(red), int(green), int(blue)))


def _point(capture: tuple[Any, ...], widget: Any, x: int, y: int) -> tuple[int, int, int] | None:
    rect, data, width, height = capture
    origin_x = int(widget.winfo_rootx()) - int(rect[0])
    origin_y = int(widget.winfo_rooty()) - int(rect[1])
    px = origin_x + x
    py = origin_y + y
    if px < 0 or py < 0 or px >= width or py >= height:
        return None
    src_y = height - 1 - py
    offset = src_y * width * 4 + px * 4
    return (int(data[offset + 2]), int(data[offset + 1]), int(data[offset]))


def _fill_samples(capture: Any, widget: Any) -> list[tuple[int, int, int]]:
    if not isinstance(capture, tuple):
        return []
    width = max(1, int(widget.winfo_width()))
    height = max(1, int(widget.winfo_height()))
    points = (
        (width // 2, 7),
        (10, height // 2),
        (width - 11, height // 2),
        (width // 2, height - 8),
    )
    samples = []
    for x, y in points:
        color = _point(capture, widget, x, y)
        if color is not None:
            samples.append(color)
    return samples


def _button_record(button: Any, capture: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "text": str(button.cget("text")),
        "style": str(button.cget("style")),
        "width": int(button.winfo_width()),
        "height": int(button.winfo_height()),
        "state": list(button.state()),
    }
    if isinstance(capture, tuple):
        cropped = _crop(capture, button)
        if isinstance(cropped, str):
            record["screenshot_error"] = cropped
        else:
            _data, _w, _h, color = cropped
            record["screenshot_center"] = list(color)
            record["fill_samples"] = [list(item) for item in _fill_samples(capture, button)]
    return record


def _save_sidebar(path: Path, capture: Any, sidebar: Any) -> str | None:
    if not isinstance(capture, tuple):
        return str(capture)
    cropped = _crop(capture, sidebar)
    if isinstance(cropped, str):
        return cropped
    data, width, height, _color = cropped
    _write_bmp(path, data, width, height)
    return None


def validate(app: Any, out: Path, *, expect_match: bool) -> dict[str, Any]:
    root = app.root
    nav = app.navigation
    root.geometry("1100x760+40+40")
    root.deiconify()
    _pump(root, 6)
    failures: list[str] = []
    shots: list[str] = []
    pages: list[dict[str, Any]] = []
    themes_seen: list[str] = []

    def visit(theme: str) -> None:
        themes_seen.append(theme)
        _pump(root, 3)
        for name in PAGES:
            nav.show(name)
            page = nav.pages[name]
            keys = list(page.mode_pages)
            if not keys:
                failures.append(f"{theme} {name}: no mode pages")
                continue
            for key in keys:
                _show_mode(page, key)
                _pump(root, 3)
                capture = _capture(root)
                shot = out / f"{theme}-{name}-{key}.bmp"
                error = _save_sidebar(shot, capture, nav.sidebar_shell)
                if error:
                    failures.append(f"{theme} {name} {key}: {error}")
                else:
                    shots.append(str(shot.relative_to(ROOT)))
                chip = nav.buttons[name]
                mode = nav.mode_buttons[key]
                chip_color = None
                mode_color = None
                if isinstance(capture, tuple):
                    chip_crop = _crop(capture, chip)
                    mode_crop = _crop(capture, mode)
                    if isinstance(chip_crop, tuple):
                        chip_color = chip_crop[3]
                    if isinstance(mode_crop, tuple):
                        mode_color = mode_crop[3]
                    if expect_match and isinstance(capture, tuple):
                        chip_samples = _fill_samples(capture, chip)
                        mode_samples = _fill_samples(capture, mode)
                        matched = any(_near(left, right) for left in chip_samples for right in mode_samples)
                        if chip_samples and mode_samples and not matched:
                            failures.append(
                                f"{theme} {name} {key}: screenshot fills chip {chip_samples} != mode {mode_samples}"
                            )
                pages.append(
                    {
                        "theme": theme,
                        "page": name,
                        "mode": key,
                        "current_page": app.state.current_page,
                        "current_mode": page.current_mode,
                        "chip": _button_record(chip, capture),
                        "mode_button": _button_record(mode, capture),
                        "screenshot_chip": list(chip_color) if chip_color else None,
                        "screenshot_mode": list(mode_color) if mode_color else None,
                    }
                )
                if app.state.current_page != name or page.current_mode != key:
                    failures.append(f"{theme} {name} {key}: navigation did not stick")

    visit(app.state.theme)
    app.set_theme("dark")
    _pump(root, 4)
    visit("dark")
    app.set_theme("light")
    _pump(root, 4)
    visit("light-return")

    store = getattr(root, "_rounded_button_images", {})
    images: dict[str, Any] = {}
    for theme, images_for_theme in store.items():
        chip = images_for_theme["sidebar"]["pressed"]
        mode = images_for_theme["sidebar_mode_selected"]["pressed"]
        idle = images_for_theme["sidebar_mode"]["normal"]
        disabled = images_for_theme["sidebar_mode_selected"]["disabled"]
        chip_px = _pixel(chip, 16, 16)
        mode_px = _pixel(mode, 16, 16)
        idle_px = _pixel(idle, 16, 16)
        disabled_px = _pixel(disabled, 16, 16)
        same = list(_pixel(chip, x, y) for y in range(32) for x in range(32)) == list(
            _pixel(mode, x, y) for y in range(32) for x in range(32)
        )
        images[theme] = {
            "chip_selected_center": list(chip_px),
            "mode_selected_center": list(mode_px),
            "mode_idle_center": list(idle_px),
            "mode_selected_disabled_center": list(disabled_px),
            "selected_images_identical": same,
        }
        if expect_match and not same:
            failures.append(f"{theme}: selected images differ")
        if expect_match and disabled_px == chip_px:
            failures.append(f"{theme}: disabled selected mode uses selected fill")
        if idle_px == chip_px:
            failures.append(f"{theme}: unselected mode fill matches selected chip")

    state_checks = _state_checks(app, out)
    return {
        "hardware_boundary": "LocalI2cBridge.start and serial enumeration were simulated; no serial/I2C/firmware action",
        "themes_seen": themes_seen,
        "images": images,
        "pages": pages,
        "state_checks": state_checks,
        "screenshots": shots,
        "failures": failures,
    }


def _state_checks(app: Any, out: Path) -> list[dict[str, Any]]:
    root = app.root
    nav = app.navigation
    app.set_theme("light")
    nav.show("awp1221")
    page = nav.pages["awp1221"]
    _show_mode(page, "button_mode")
    _pump(root, 3)
    checks: list[dict[str, Any]] = []
    targets = (
        ("chip", nav.buttons["awp1221"]),
        ("mode", nav.mode_buttons["button_mode"]),
    )
    for label, button in targets:
        saved = list(button.state())
        for state_name in ("selected", "active", "pressed", "disabled"):
            button.state(["!active", "!pressed", "!disabled"])
            if state_name != "selected":
                button.state([state_name])
            _pump(root, 2)
            capture = _capture(root)
            record = _button_record(button, capture)
            record["probe"] = state_name
            record["label"] = label
            shot = out / f"state-{label}-{state_name}.bmp"
            if isinstance(capture, tuple):
                cropped = _crop(capture, button)
                if isinstance(cropped, tuple):
                    _write_bmp(shot, cropped[0], cropped[1], cropped[2])
                    record["shot"] = str(shot.relative_to(ROOT))
            checks.append(record)
        button.state(["!active", "!pressed", "!disabled"])
        if "disabled" in saved:
            button.state(["disabled"])
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--expect-match", action="store_true")
    args = parser.parse_args()
    out = ROOT / "docs" / "specs" / "sidebar_selection_color_evidence" / args.label
    out.mkdir(parents=True, exist_ok=True)
    calls = {"start": 0, "list_ports": 0, "io": 0}

    import app.application as application
    from core.serial_manager import SerialManager, SerialPortInfo

    def start(_self: Any) -> None:
        calls["start"] += 1

    def list_port_info(_self: Any, *, probe_missing: bool = True) -> list[SerialPortInfo]:
        calls["list_ports"] += 1
        return [SerialPortInfo("COM99", "offline-enum")]

    def blocked(*_args: Any, **_kwargs: Any) -> None:
        calls["io"] += 1
        return None

    with (
        patch.object(application.LocalI2cBridge, "start", start),
        patch.object(application.LocalI2cBridge, "close", lambda _self: None),
        patch.object(SerialManager, "list_port_info", list_port_info),
        patch.object(SerialManager, "connect", blocked),
        patch.object(SerialManager, "send_command", blocked),
        patch.object(SerialManager, "read_register_value", blocked),
        patch.object(SerialManager, "write_register_value", blocked),
        patch.object(SerialManager, "disconnect", lambda _self: None),
    ):
        app = application.PiezoApplication()
        try:
            report = validate(app, out, expect_match=args.expect_match)
        finally:
            app.close()
    report["simulated_calls"] = calls
    if calls["start"] < 1 or calls["list_ports"] < 1:
        report["failures"].append(f"simulation was not exercised: {calls}")
    if calls["io"]:
        report["failures"].append(f"blocked serial I/O was invoked {calls['io']} times; no port was opened")
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"label": args.label, "failures": report["failures"], "shots": len(report["screenshots"]), "calls": calls}, indent=2))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
