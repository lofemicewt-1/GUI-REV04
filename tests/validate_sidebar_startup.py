"""Bounded cold-start validation for the REV04 sidebar.

This is an evidence harness, not product code.  It constructs the real
``PiezoApplication`` with serial/I2C boundaries mocked, enters the real Tk
``mainloop`` without an initial ``update()``, resize, page switch, or visual
settings call, and inspects the first mapped frame.  Later phases deliberately
exercise hide/show and one small-window scroll scene.

Run from the repository root with ``py tests/validate_sidebar_startup.py``.
"""

from __future__ import annotations

from collections import Counter
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "sidebar_startup_validation_artifacts"
REPORT_PATH = ROOT / "SIDEBAR_STARTUP_VALIDATION.md"
PRODUCT_FILES = (
    ROOT / "app" / "application.py",
    ROOT / "app" / "navigation.py",
    ROOT / "styles" / "theme.py",
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.validate_responsive_layout import mocked_application  # noqa: E402


def _hash_product_files() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in PRODUCT_FILES
    }


def _mapped(widget: Any) -> bool:
    try:
        return bool(widget.winfo_ismapped())
    except Exception:
        return False


def _write_bmp(path: Path, width: int, height: int, pixels: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"BM"
        + struct.pack("<IHHI", 54 + len(pixels), 0, 0, 54)
        + struct.pack(
            "<IiiHHIIiiII",
            40,
            width,
            height,
            1,
            32,
            0,
            len(pixels),
            0,
            0,
            0,
            0,
        )
        + pixels
    )


def _capture_window_crop(root: Any, path: Path, *, width: int = 640, height: int = 600) -> dict[str, Any]:
    """Capture a bounded top-left window crop for human visual inspection."""

    if os.name != "nt":
        return {"error": "skipped: non-Windows runner"}

    class BitmapInfoHeader(ctypes.Structure):
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

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", ctypes.c_uint32 * 1)]

    width = max(1, int(width))
    height = max(1, int(height))
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hwnd = ctypes.c_void_p(int(root.winfo_id()))
    window_dc = user32.GetWindowDC(hwnd)
    if not window_dc:
        return {"error": "GetWindowDC failed"}
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    if not memory_dc or not bitmap:
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)
        return {"error": "CreateCompatibleBitmap failed"}
    old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not gdi32.BitBlt(memory_dc, 0, 0, width, height, window_dc, 0, 0, 0x00CC0020 | 0x40000000):
            return {"error": "BitBlt failed"}
        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        data = (ctypes.c_ubyte * (width * height * 4))()
        copied = gdi32.GetDIBits(memory_dc, bitmap, 0, height, data, ctypes.byref(info), 0)
        if copied != height:
            return {"error": f"GetDIBits returned {copied}"}
        pixels = bytes(data)
        _write_bmp(path, width, height, pixels)
        sample_indices = range(0, len(pixels), 4 * 16)
        colors = Counter(
            (pixels[index], pixels[index + 1], pixels[index + 2])
            for index in sample_indices
        )
        dominant, dominant_count = colors.most_common(1)[0]
        total = max(1, len(range(0, len(pixels), 4 * 16)))
        dark = sum(
            1
            for index in range(0, len(pixels), 4 * 16)
            if max(pixels[index], pixels[index + 1], pixels[index + 2]) < 180
        )
        return {
            "path": str(path),
            "width": width,
            "height": height,
            "dominant_bgr": list(dominant),
            "non_dominant_ratio": round((total - dominant_count) / total, 5),
            "dark_pixel_ratio": round(dark / total, 5),
        }
    finally:
        gdi32.SelectObject(memory_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


class SidebarStartupProbe:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.root = app.root
        self.nav = app.navigation
        self.records: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._finished = False
        self._started = time.monotonic()
        self._product_hash_before = _hash_product_files()

    def _snapshot(self, label: str) -> dict[str, Any]:
        title = self.nav._title_canvas
        brand_texts = [str(title.itemcget(item, "text")) for item in title.find_all()]
        buttons = {
            name: {
                "mapped": _mapped(button),
                "width": int(button.winfo_width()),
                "height": int(button.winfo_height()),
                "text": str(button.cget("text")),
                "root_x": int(button.winfo_rootx()),
                "root_y": int(button.winfo_rooty()),
            }
            for name, button in self.nav.buttons.items()
        }
        return {
            "label": label,
            "root": {
                "width": int(self.root.winfo_width()),
                "height": int(self.root.winfo_height()),
                "mapped": _mapped(self.root),
            },
            "sidebar_canvas": {
                "width": int(self.nav.sidebar_canvas.winfo_width()),
                "height": int(self.nav.sidebar_canvas.winfo_height()),
                "mapped": _mapped(self.nav.sidebar_canvas),
                "scrollregion": str(self.nav.sidebar_canvas.cget("scrollregion")),
                "yview": [float(value) for value in self.nav.sidebar_canvas.yview()],
            },
            "sidebar": {
                "width": int(self.nav.sidebar.winfo_width()),
                "height": int(self.nav.sidebar.winfo_height()),
                "request_height": int(self.nav.sidebar.winfo_reqheight()),
            },
            "brand": {
                "mapped": _mapped(title),
                "width": int(title.winfo_width()),
                "height": int(title.winfo_height()),
                "items": len(title.find_all()),
                "texts": brand_texts,
            },
            "buttons": buttons,
            "footer": {
                "guide_mapped": _mapped(self.nav._guide_button),
                "guide_y": int(self.nav._guide_button.winfo_rooty()),
                "version_mapped": _mapped(self.nav._version_label),
                "version_y": int(self.nav._version_label.winfo_rooty()),
            },
        }

    def _capture_sidebar(self, label: str) -> dict[str, Any]:
        # A bounded crop keeps the evidence viewable while the snapshot still
        # records exact sidebar widget geometry separately.
        path = ARTIFACT_DIR / f"{label}-sidebar-crop.bmp"
        capture = _capture_window_crop(self.root, path)
        if capture.get("error"):
            return capture
        payload = path.read_bytes()
        width, height = struct.unpack_from("<ii", payload, 18)
        pixels = payload[54:]
        # Sample a bounded subset so first-frame capture remains a seconds-
        # scale feedback loop even at the default 1700x980 window size.
        sample_indices = range(0, len(pixels), 4 * 16)
        colors = Counter(
            (pixels[index], pixels[index + 1], pixels[index + 2])
            for index in sample_indices
        )
        dominant, dominant_count = colors.most_common(1)[0]
        total = max(1, len(range(0, len(pixels), 4 * 16)))
        dark = sum(
            1
            for index in range(0, len(pixels), 4 * 16)
            if max(pixels[index], pixels[index + 1], pixels[index + 2]) < 180
        )
        return {
            "path": str(path),
            "width": width,
            "height": height,
            "dominant_bgr": list(dominant),
            "non_dominant_ratio": round((total - dominant_count) / total, 5),
            "dark_pixel_ratio": round(dark / total, 5),
        }

    def _record(self, label: str, *, capture: bool = True) -> dict[str, Any]:
        record = self._snapshot(label)
        if capture:
            record["capture"] = self._capture_sidebar(label)
        self.records.append(record)
        return record

    def _capture_default_if_ready(self) -> None:
        if any(item["label"] == "default-first-mapped" for item in self.records):
            return
        if _mapped(self.root) and _mapped(self.nav.sidebar_shell):
            self._record("default-first-mapped")

    def _check_default_first_frame(self) -> None:
        self._record("default-settled")
        first = next(
            (item for item in self.records if item["label"] == "default-first-mapped"),
            None,
        )
        if first is None:
            self._fail("no default first-mapped sample was observed")
        else:
            self._check_visible_contract(first, "default-first-mapped")
            self._compare_capture("default-first-mapped", "default-settled")
        self.root.withdraw()

    def _show_after_hidden(self) -> None:
        if self._finished:
            return
        self.root.deiconify()

    def _check_hidden_first_frame(self) -> None:
        if any(item["label"] == "hidden-first-mapped" for item in self.records):
            return
        # The root can be mapped while the re-shown child hierarchy is still
        # absent.  Record that exact state so a hidden/show regression cannot
        # be mistaken for a test timeout or a child-only geometry success.
        if _mapped(self.root):
            record = self._record("hidden-first-mapped")
            self._check_visible_contract(record, "hidden-first-mapped")

    def _prepare_small_window(self) -> None:
        self.root.geometry("640x480")
        self.nav.show("awp1921_1923")

    def _check_small_window(self) -> None:
        if self._finished:
            return
        if any(item["label"] == "small-window-before-scroll" for item in self.records):
            return
        record = self._record("small-window-before-scroll")
        canvas = self.nav.sidebar_canvas
        if int(self.root.winfo_width()) > 700 or int(self.root.winfo_height()) > 560:
            self._fail(f"small-window request was not applied: {record['root']}")
            return
        if int(record["sidebar"]["request_height"]) <= int(record["sidebar_canvas"]["height"]) + 1:
            self._fail(
                "small-window sidebar did not require scrolling: "
                f"{record['sidebar']} vs {record['sidebar_canvas']}"
            )
            return
        if not _mapped(self.nav.sidebar_scrollbar):
            self._fail("small-window sidebar scrollbar is not mapped")
            return
    def _scroll_small_window(self) -> None:
        if _mapped(self.nav.sidebar_canvas):
            self.nav.sidebar_canvas.yview_moveto(1.0)

    def _check_small_scroll_end(self) -> None:
        record = self._record("small-window-scroll-end")
        canvas = self.nav.sidebar_canvas
        yview = canvas.yview()
        canvas_bottom = int(canvas.winfo_rooty()) + int(canvas.winfo_height())
        guide_bottom = int(self.nav._guide_button.winfo_rooty()) + int(self.nav._guide_button.winfo_height())
        version_bottom = int(self.nav._version_label.winfo_rooty()) + int(self.nav._version_label.winfo_height())
        if yview[1] < 0.999:
            self._fail(f"small-window sidebar did not reach scroll end: yview={yview}")
            return
        if int(self.nav._guide_button.winfo_rooty()) < int(canvas.winfo_rooty()) - 1 or guide_bottom > canvas_bottom + 1:
            self._fail("guide button is not reachable at small-window scroll end")
            return
        if int(self.nav._version_label.winfo_rooty()) < int(canvas.winfo_rooty()) - 1 or version_bottom > canvas_bottom + 1:
            self._fail("version label is not reachable at small-window scroll end")
            return
        self._finish()

    def _check_visible_contract(self, record: dict[str, Any], phase: str) -> None:
        root = record["root"]
        brand = record["brand"]
        if not root["mapped"] or root["width"] <= 10 or root["height"] <= 10:
            self._fail(f"{phase}: root is not visibly mapped: {root}")
            return
        canvas = record["sidebar_canvas"]
        if not canvas["mapped"] or canvas["width"] <= 10 or canvas["height"] <= 10:
            self._fail(f"{phase}: sidebar canvas is not mapped/paintable: {canvas}")
            return
        if not brand["mapped"] or brand["width"] <= 10 or brand["height"] <= 10:
            self._fail(f"{phase}: brand canvas is not visibly mapped: {brand}")
            return
        text = "".join(brand["texts"])
        if brand["items"] < 10 or "PIEZO" not in text or "CONTROL" not in text:
            self._fail(f"{phase}: brand items/text are missing: {brand}")
            return
        missing = {
            name: info
            for name, info in record["buttons"].items()
            if not info["mapped"] or info["width"] <= 30 or info["height"] <= 15 or not info["text"].strip()
        }
        if missing:
            self._fail(f"{phase}: navigation buttons are missing or blank: {missing}")

    def _compare_capture(self, first_label: str, settled_label: str) -> None:
        first = next(item for item in self.records if item["label"] == first_label).get("capture", {})
        settled = next(item for item in self.records if item["label"] == settled_label).get("capture", {})
        if first.get("error") or settled.get("error"):
            self.warnings.append(f"pixel comparison skipped: {first.get('error') or settled.get('error')}")
            return
        # The first mapped frame and settled frame should have the same painted
        # sidebar.  A large change means the user would need a later expose,
        # resize, or other event to see the controls.
        delta = abs(
            float(first.get("non_dominant_ratio", 0.0))
            - float(settled.get("non_dominant_ratio", 0.0))
        )
        first["settled_non_dominant_delta"] = round(delta, 5)
        if delta > 0.08:
            self._fail(
                "first mapped sidebar paint differs materially from settled paint: "
                f"first={first} settled={settled}"
            )

    def _fail(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)
        self._finish()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        # The pre-registered bounded quit in run() owns loop termination.

    def run(self) -> dict[str, Any]:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        # No explicit root.update/update_idletasks, geometry, page switch, or
        # visual-settings call occurs before this first mapped-frame probe.
        # Every callback is registered before mainloop.  The product's initial
        # Map-boundary geometry pass can synchronously drain idle work; adding
        # another timer from inside that callback is therefore an unreliable
        # test seam.  Pre-registering the bounded sample points preserves the
        # cold-start path while avoiding that test-only starvation.
        for delay in range(0, 181, 10):
            self.root.after(delay, self._capture_default_if_ready)
        self.root.after(240, self._check_default_first_frame)
        self.root.after(300, self._show_after_hidden)
        for delay in (340, 370, 400, 430):
            self.root.after(delay, self._check_hidden_first_frame)
        self.root.after(1500, self._prepare_small_window)
        # Tk/Windows applies this application's post-show resize request
        # asynchronously; leave one bounded wait for the actual 640x480 map.
        self.root.after(7000, self._check_small_window)
        self.root.after(7150, self._scroll_small_window)
        self.root.after(7300, self._check_small_scroll_end)
        self.root.after(7600, self.root.quit)
        self.app.run()
        product_hash_after = _hash_product_files()
        if product_hash_after != self._product_hash_before:
            self.errors.append("product files changed while the validation run was active")
        return {
            "records": self.records,
            "errors": self.errors,
            "warnings": self.warnings,
            "product_hash_before": self._product_hash_before,
            "product_hash_after": product_hash_after,
            "elapsed_seconds": round(time.monotonic() - self._started, 3),
        }


def _write_report(result: dict[str, Any]) -> None:
    errors = result["errors"]
    warnings = result["warnings"]
    lines = [
        "# Sidebar Startup Validation",
        "",
        "本报告由 `tests/validate_sidebar_startup.py` 生成。验证构造真实 Tk `PiezoApplication`，只 mock 本地 I2C 桥接启动/关闭、串口枚举和传输入口；没有硬件 I/O、没有重新打包 EXE。",
        "",
        "## 冷启动边界",
        "",
        "- 首个默认场景只进入真实 `mainloop`；在首个 mapped 帧之前没有调用 `update()`、`update_idletasks()`、手动 `geometry()`、切页或应用视觉设置。",
        "- 首个 mapped 帧检查品牌 Canvas 的实际映射尺寸、`PIEZO`/`CONTROL` 文本项和全部顶层导航按钮；同时保存侧栏屏幕像素 BMP，并与 120 ms 后的稳定帧比较。",
        "- 随后的独立场景检查隐藏后首次显示；最后只运行一个 640×480 小屏场景，在切换到 AWP1921/AWP1923 后验证侧栏滚动条、滚动终点和 footer 可达。",
        "",
        "## 结果",
        "",
        f"- 记录：{len(result['records'])}；错误：{len(errors)}；警告：{len(warnings)}；耗时：{result['elapsed_seconds']} s。",
        f"- 产品文件 hash（运行前）：`{json.dumps(result['product_hash_before'], ensure_ascii=False)}`。",
        f"- 产品文件 hash（运行后）：`{json.dumps(result['product_hash_after'], ensure_ascii=False)}`。",
        "",
        "## 记录",
        "",
    ]
    for record in result["records"]:
        lines.append(f"- `{record['label']}`：`{json.dumps(record, ensure_ascii=False)}`")
    lines.extend(["", "## 错误与限制", ""])
    if errors:
        lines.extend(f"- **ERROR** {item}" for item in errors)
    else:
        lines.append("- 未发现冷启动侧栏错误。")
    if warnings:
        lines.extend(f"- **WARNING** {item}" for item in warnings)
    lines.extend(
        [
            "",
            "截图文件位于 `sidebar_startup_validation_artifacts/`；BMP 是屏幕像素证据，不代表真实硬件、串口或人工鼠标输入验收。",
            "",
            "SIDEBAR_QA_READY" if not errors else "SIDEBAR_QA_BLOCKED",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    with mocked_application() as app:
        result = SidebarStartupProbe(app).run()
    _write_report(result)
    print(
        f"records={len(result['records'])} errors={len(result['errors'])} "
        f"warnings={len(result['warnings'])} report={REPORT_PATH}",
        flush=True,
    )
    print("SIDEBAR_QA_READY" if not result["errors"] else "SIDEBAR_QA_BLOCKED", flush=True)
    return 0 if not result["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
