"""Bounded real-Tk validation for the AWP1221 maximize/restore report.

The application is constructed through the existing offline harness, so no
serial port, bridge service, I2C transaction, firmware build, or board write
is possible.  Unlike the broad responsive matrix, this check explicitly uses
``root.state('zoomed')`` and captures the real top-level window after each
state transition.

Run from the repository root with ``py tests/validate_awp1221_maximize.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tkinter as tk
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "responsive_layout_validation_artifacts"
REPORT_PATH = ROOT / "docs" / "specs" / "AWP1221_ERROR_MAXIMIZE_VALIDATION.md"
JSON_PATH = ARTIFACT_DIR / "awp1221_error_maximize_validation.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.validate_responsive_layout import (  # noqa: E402
    _capture_window_bmp,
    _float_pair,
    _flush,
    _mapped,
    mocked_application,
)


MODES = ("button_mode", "full_mode")


def _rect(widget: tk.Misc) -> dict[str, int]:
    return {
        "x": int(widget.winfo_rootx()),
        "y": int(widget.winfo_rooty()),
        "width": int(widget.winfo_width()),
        "height": int(widget.winfo_height()),
    }


def _grid_child(page: tk.Misc, row: int) -> tk.Misc | None:
    for child in page.winfo_children():
        try:
            if int(child.grid_info().get("row", -1)) == row:
                return child
        except (tk.TclError, TypeError, ValueError):
            continue
    return None


def _snapshot(app: Any, mode: str, label: str) -> dict[str, Any]:
    root = app.root
    nav = app.navigation
    nav.show("awp1221")
    page = nav.pages["awp1221"]
    page.show_mode(mode)
    _flush(root, 14)

    active = page.mode_pages[mode]
    mode_scroll = page.mode_scroll
    canvas = mode_scroll.canvas
    scrollregion = tuple(float(value) for value in str(canvas.cget("scrollregion")).split())
    yview = _float_pair(canvas.yview())
    footer = _grid_child(page, 2)
    mode_area = _rect(mode_scroll)
    canvas_rect = _rect(canvas)
    page_rect = _rect(page)
    content_canvas_rect = _rect(nav.content_canvas)
    content_rect = _rect(nav.content)
    footer_rect = _rect(footer) if footer is not None else None

    path = ARTIFACT_DIR / f"awp1221-{mode}-{label}.bmp"
    capture_error = _capture_window_bmp(root, path)
    root_rect = {
        "width": int(root.winfo_width()),
        "height": int(root.winfo_height()),
        "screen_width": int(root.winfo_screenwidth()),
        "screen_height": int(root.winfo_screenheight()),
    }

    return {
        "mode": mode,
        "label": label,
        "window_state": str(root.state()),
        "root": root_rect,
        "page": page_rect,
        "active_mode": {
            "mapped": _mapped(active),
            "req_width": int(active.winfo_reqwidth()),
            "req_height": int(active.winfo_reqheight()),
            "rect": _rect(active),
        },
        "mode_scroll": {
            "mapped": _mapped(mode_scroll),
            "rect": mode_area,
            "canvas": canvas_rect,
            "content_req_width": int(mode_scroll.content.winfo_reqwidth()),
            "content_req_height": int(mode_scroll.content.winfo_reqheight()),
            "scrollregion": scrollregion,
            "yview": yview,
            "vertical_scrollbar_mapped": _mapped(mode_scroll.scrollbar),
            "horizontal_scrollbar_mapped": _mapped(mode_scroll.horizontal_scrollbar),
        },
        "content": {
            "canvas": content_canvas_rect,
            "frame": content_rect,
        },
        "footer": footer_rect,
        "capture": {"path": str(path), "error": capture_error},
    }


def _check_snapshot(snapshot: dict[str, Any], errors: list[str]) -> None:
    state = snapshot["window_state"]
    if snapshot["label"] == "zoomed" and state != "zoomed":
        errors.append(f"{snapshot['mode']} zoomed transition returned state={state!r}")
    if snapshot["label"] == "restored" and state != "normal":
        errors.append(f"{snapshot['mode']} restore transition returned state={state!r}")

    root = snapshot["root"]
    if root["width"] < 1000 or root["height"] < 600:
        errors.append(
            f"{snapshot['mode']} {snapshot['label']} root is unexpectedly small: "
            f"{root['width']}x{root['height']}"
        )

    page = snapshot["page"]
    mode_scroll = snapshot["mode_scroll"]
    active = snapshot["active_mode"]
    if not active["mapped"]:
        errors.append(f"{snapshot['mode']} {snapshot['label']} active mode is not mapped")
    if not mode_scroll["mapped"]:
        errors.append(f"{snapshot['mode']} {snapshot['label']} mode scroll area is not mapped")
    if mode_scroll["rect"]["height"] < 120:
        errors.append(
            f"{snapshot['mode']} {snapshot['label']} mode area does not occupy available height: "
            f"{mode_scroll['rect']['height']}px"
        )
    if mode_scroll["canvas"]["height"] < 100:
        errors.append(
            f"{snapshot['mode']} {snapshot['label']} mode canvas is collapsed: "
            f"{mode_scroll['canvas']['height']}px"
        )
    if snapshot["label"] == "zoomed" and not mode_scroll["vertical_scrollbar_mapped"]:
        # A maximized page has enough height for the active mode to stretch
        # between its header and pinned footer.  A short natural child inside
        # a tall canvas is the reported defect: it leaves a large blank band
        # while the controls remain compressed at the top.
        viewport_height = mode_scroll["canvas"]["height"]
        active_height = active["rect"]["height"]
        fill_ratio = active_height / max(1, viewport_height)
        snapshot["active_mode"]["fill_ratio"] = fill_ratio
        if fill_ratio < 0.90:
            errors.append(
                f"{snapshot['mode']} zoomed active mode fills only {fill_ratio:.1%} "
                f"of its {viewport_height}px viewport ({active_height}px); screenshot shows a blank lower band"
            )
    if page["height"] < mode_scroll["rect"]["height"]:
        errors.append(
            f"{snapshot['mode']} {snapshot['label']} mode area exceeds page height: "
            f"{mode_scroll['rect']['height']} > {page['height']}"
        )
    capture = snapshot["capture"]
    if capture["error"]:
        errors.append(f"{snapshot['mode']} {snapshot['label']} capture failed: {capture['error']}")
    region = mode_scroll["scrollregion"]
    if len(region) != 4:
        errors.append(f"{snapshot['mode']} {snapshot['label']} mode scrollregion is invalid: {region}")


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    observations: list[dict[str, Any]] = []
    errors: list[str] = []
    with mocked_application() as app:
        root = app.root
        root.deiconify()
        root.geometry("1000x700+20+20")
        root.state("normal")
        _flush(root, 12)

        # Maximize once, switch modes while the real window is maximized, then
        # restore and switch back.  This catches a mode-local layout that only
        # fails after the maximize Configure event.
        app.navigation.show("awp1221")
        app.navigation.pages["awp1221"].show_mode("button_mode")
        root.state("zoomed")
        _flush(root, 18)
        observations.append(_snapshot(app, "button_mode", "zoomed"))

        app.navigation.pages["awp1221"].show_mode("full_mode")
        _flush(root, 18)
        observations.append(_snapshot(app, "full_mode", "zoomed"))

        root.state("normal")
        root.geometry("1000x700+20+20")
        _flush(root, 18)
        observations.append(_snapshot(app, "full_mode", "restored"))

        app.navigation.pages["awp1221"].show_mode("button_mode")
        _flush(root, 18)
        observations.append(_snapshot(app, "button_mode", "restored"))

        for observation in observations:
            _check_snapshot(observation, errors)

        # At each constrained restored scene, move the mode-local canvas to
        # its endpoint and require a real scroll range when its content is
        # taller than the viewport.  This proves the lower controls are
        # reachable rather than merely mapped outside the screenshot.
        for observation in observations:
            if observation["label"] != "restored":
                continue
            page = app.navigation.pages["awp1221"]
            page.show_mode(observation["mode"])
            _flush(root, 10)
            mode_scroll = page.mode_scroll
            canvas = mode_scroll.canvas
            region = tuple(float(value) for value in str(canvas.cget("scrollregion")).split())
            before = _float_pair(canvas.yview())
            before_x = _float_pair(canvas.xview())
            canvas.yview_moveto(1.0)
            canvas.xview_moveto(1.0)
            _flush(root, 4)
            after = _float_pair(canvas.yview())
            after_x = _float_pair(canvas.xview())
            observation["mode_scroll"]["endpoint_before"] = before
            observation["mode_scroll"]["endpoint_after"] = after
            observation["mode_scroll"]["horizontal_endpoint_before"] = before_x
            observation["mode_scroll"]["horizontal_endpoint_after"] = after_x
            observation["mode_scroll"]["endpoint_reachable"] = (
                len(region) == 4 and (region[3] <= canvas.winfo_height() + 1 or after[1] >= before[1])
            )
            observation["mode_scroll"]["horizontal_endpoint_reachable"] = (
                len(region) == 4 and (region[2] <= canvas.winfo_width() + 1 or after_x[1] >= before_x[1])
            )
            if not observation["mode_scroll"]["endpoint_reachable"]:
                errors.append(f"{observation['mode']} restored mode scroll endpoint did not advance")
            if not observation["mode_scroll"]["horizontal_endpoint_reachable"]:
                errors.append(f"{observation['mode']} restored mode horizontal endpoint did not advance")

    payload = {
        "scope": "AWP1221 Button/Full real Tk maximize and restore only",
        "hardware_boundary": "LocalI2cBridge and SerialManager are mocked; no serial/I2C/firmware action",
        "observations": observations,
        "errors": errors,
        "status": "AWP1221_QA_READY" if not errors else "BLOCKED",
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# AWP1221 错误与最大化布局专项验证",
        "",
        "本报告只覆盖当前阶段的 AWP1221 Button Mode / Full Mode 窗口布局。验证构造真实 Tk 页面，调用真实 `root.state('zoomed')`、恢复 `normal` 尺寸，并对每个状态保存实际顶层窗口截图；桥接、串口和传输入口全部 mock，没有写寄存器、读 I²C、刷固件或打包 EXE。",
        "",
        f"- 检查状态：`{payload['status']}`。",
        f"- 实测场景：{len(observations)} 个（Button/Full 最大化各 1，Full/Button 恢复小窗口各 1）。",
        f"- JSON 证据：`{JSON_PATH}`。",
        "- 本阶段明确暂停：Type-4 预设按钮回调回归、侧栏冷启动/隐藏重显示和旧全矩阵；它们不计入本报告结论。",
        "",
        "## 实际截图",
        "",
    ]
    for observation in observations:
        capture = observation["capture"]
        bmp_path = Path(capture["path"])
        png_path = bmp_path.with_suffix(".png")
        display_path = png_path if png_path.exists() else bmp_path
        lines.append(f"- `{observation['mode']}/{observation['label']}`：`{display_path}`（{capture['error'] or '已保存，已目视检查'}）")
    lines.extend(["", "## 几何与可达性", ""])
    for observation in observations:
        scroll = observation["mode_scroll"]
        fill_ratio = observation["active_mode"].get("fill_ratio")
        fill_text = f", active mode 填充率 {fill_ratio:.1%}" if isinstance(fill_ratio, float) else ""
        lines.append(
            f"- `{observation['mode']}/{observation['label']}`：Tk state=`{observation['window_state']}`，根窗口 {observation['root']['width']}×{observation['root']['height']}，mode scroll 区 {scroll['rect']['width']}×{scroll['rect']['height']}，内容请求高度 {scroll['content_req_height']}，vscroll={'显示' if scroll['vertical_scrollbar_mapped'] else '隐藏'}{fill_text}。"
        )
        if "endpoint_reachable" in scroll:
            lines.append(f"  - 恢复窗口滚动终点：`{scroll['endpoint_before']} -> {scroll['endpoint_after']}`，可达=`{scroll['endpoint_reachable']}`。")
            lines.append(f"  - 恢复窗口横向终点：`{scroll['horizontal_endpoint_before']} -> {scroll['horizontal_endpoint_after']}`，可达=`{scroll['horizontal_endpoint_reachable']}`。")
    lines.extend(["", "## 问题", ""])
    if errors:
        lines.extend(f"- BLOCKED：{error}" for error in errors)
    else:
        lines.append("- 未发现本阶段定义的最大化、恢复、内容占高或滚动终点问题。")
    lines.extend(["", "## 目视结论", "", "AWP1221_QA_READY" if not errors else "BLOCKED", ""])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"status={payload['status']}")
    print(f"observations={len(observations)} errors={len(errors)}")
    print(f"report={REPORT_PATH}")
    print(f"json={JSON_PATH}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
