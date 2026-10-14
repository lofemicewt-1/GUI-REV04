"""Bounded real-Tk QA for the AWP1018/1221 full-mode layout follow-up.

This artifact deliberately exercises only the layout boundary described in
``FULL_MODE_LAYOUT_FOLLOWUP_PLAN.md``.  The application is built through the
offline harness from ``validate_responsive_layout``; serial enumeration,
bridge startup, and all transport entry points remain mocked.

Run from the repository root with ``py tests/validate_full_mode_layout.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "responsive_layout_validation_artifacts"
REPORT_PATH = ROOT / "docs" / "specs" / "FULL_MODE_LAYOUT_VALIDATION.md"
JSON_PATH = ARTIFACT_DIR / "full_mode_layout_validation.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pages.base_page import VerticalScrollArea  # noqa: E402
from tests.validate_responsive_layout import (  # noqa: E402
    _capture_window_bmp,
    _float_pair,
    _flush,
    _mapped,
    mocked_application,
)


SCENES: tuple[tuple[str, str], ...] = (
    ("awp1018", "awp1018_normal_mode"),
    ("awp1018", "awp1018_diff_mode"),
    ("awp1018", "full_mode"),
    ("awp1221", "button_mode"),
    ("awp1221", "full_mode"),
)


def _rect(widget: tk.Misc) -> dict[str, int]:
    return {
        "x": int(widget.winfo_rootx()),
        "y": int(widget.winfo_rooty()),
        "width": int(widget.winfo_width()),
        "height": int(widget.winfo_height()),
    }


def _descendants(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def _region(canvas: tk.Canvas) -> tuple[float, ...]:
    raw = str(canvas.cget("scrollregion")).split()
    try:
        return tuple(float(value) for value in raw)
    except (TypeError, ValueError):
        return ()


def _area_snapshot(area: Any) -> dict[str, Any]:
    canvas = area.canvas
    return {
        "class": area.__class__.__name__,
        "rect": _rect(area),
        "canvas": _rect(canvas),
        "content_req_width": int(area.content.winfo_reqwidth()),
        "content_req_height": int(area.content.winfo_reqheight()),
        "content_width": int(area.content.winfo_width()),
        "content_height": int(area.content.winfo_height()),
        "scrollregion": _region(canvas),
        "xview": _float_pair(canvas.xview()),
        "yview": _float_pair(canvas.yview()),
        "vertical_scrollbar_mapped": _mapped(area.scrollbar),
        "horizontal_scrollbar_mapped": _mapped(area.horizontal_scrollbar),
        "horizontal": bool(getattr(area, "horizontal", False)),
        "propagate_width": bool(getattr(area, "propagate_width", False)),
        "propagate_height": bool(getattr(area, "propagate_height", False)),
    }


def _nearest_area(widget: tk.Misc) -> Any | None:
    current: tk.Misc | None = widget
    while current is not None:
        if current.__class__.__name__ == "VerticalScrollArea":
            return current
        current = getattr(current, "master", None)
    return None


def _active_stage(active: Any) -> tuple[str, Any | None]:
    for attr, key, label in (
        ("_stage_frames", "Stage0", "stage0"),
        ("_stage_frames", 0, "stage0"),
        ("_button_stage_frames", 0, "button_stage0"),
    ):
        frames = getattr(active, attr, None)
        if isinstance(frames, dict) and key in frames:
            return label, frames[key]
    return "none", None


def _stage_snapshot(active: Any, outer_canvas: tk.Canvas) -> dict[str, Any]:
    label, stage = _active_stage(active)
    if stage is None:
        return {"label": label, "mapped": False, "error": "active stage frame not found"}

    area = _nearest_area(stage)
    canvas = area.canvas if area is not None else outer_canvas
    mapped_children = [child for child in _descendants(stage) if _mapped(child)]
    direct_children = [child for child in stage.winfo_children() if _mapped(child)]
    right_edge = canvas.winfo_rootx() + canvas.winfo_width()
    farthest = max(
        (child.winfo_rootx() + child.winfo_width() for child in mapped_children),
        default=canvas.winfo_rootx(),
    )
    return {
        "label": label,
        "mapped": _mapped(stage),
        "rect": _rect(stage),
        "req_width": int(stage.winfo_reqwidth()),
        "req_height": int(stage.winfo_reqheight()),
        "direct_mapped_children": len(direct_children),
        "mapped_descendants": len(mapped_children),
        "canvas": _rect(canvas),
        "nearest_scroll_area": area.__class__.__name__ if area is not None else None,
        "overflow_at_current_x": max(0, int(farthest - right_edge)),
    }


def _body_widget(active: Any) -> Any | None:
    for child in active.winfo_children():
        try:
            if int(child.grid_info().get("row", -1)) == 1:
                return child
        except (tk.TclError, TypeError, ValueError):
            continue
    return None


def _body_snapshot(active: Any) -> dict[str, Any]:
    body = _body_widget(active)
    if body is None:
        return {"found": False, "columns": []}
    columns: list[dict[str, Any]] = []
    for child in body.winfo_children():
        try:
            info = child.grid_info()
            if int(info.get("row", -1)) != 0:
                continue
            columns.append(
                {
                    "column": int(info.get("column", -1)),
                    "mapped": _mapped(child),
                    "rect": _rect(child),
                    "req_width": int(child.winfo_reqwidth()),
                    "req_height": int(child.winfo_reqheight()),
                }
            )
        except (tk.TclError, TypeError, ValueError):
            continue
    columns.sort(key=lambda item: item["column"])
    return {
        "found": True,
        "rect": _rect(body),
        "req_width": int(body.winfo_reqwidth()),
        "req_height": int(body.winfo_reqheight()),
        "columns": columns,
    }


def _snapshot(app: Any, page_name: str, mode: str, label: str) -> dict[str, Any]:
    root = app.root
    page = app.navigation.pages[page_name]
    active = page.mode_pages[mode]
    outer = page.mode_scroll
    nested = [
        area
        for area in _descendants(active)
        if area.__class__.__name__ == "VerticalScrollArea" and _mapped(area)
    ]
    path = ARTIFACT_DIR / f"full-followup-{page_name}-{mode}-{label}.bmp"
    capture_error = _capture_window_bmp(root, path)
    outer_snapshot = _area_snapshot(outer)
    active_snapshot = {
        "mapped": _mapped(active),
        "rect": _rect(active),
        "req_width": int(active.winfo_reqwidth()),
        "req_height": int(active.winfo_reqheight()),
        "fill_ratio": int(active.winfo_height()) / max(1, outer.canvas.winfo_height()),
    }
    return {
        "page": page_name,
        "mode": mode,
        "label": label,
        "window_state": str(root.state()),
        "root": {
            "width": int(root.winfo_width()),
            "height": int(root.winfo_height()),
            "screen_width": int(root.winfo_screenwidth()),
            "screen_height": int(root.winfo_screenheight()),
        },
        "page_rect": _rect(page),
        "active_mode": active_snapshot,
        "outer_scroll": outer_snapshot,
        "body": _body_snapshot(active),
        "stage": _stage_snapshot(active, outer.canvas),
        "nested_scroll": [_area_snapshot(area) for area in nested],
        "capture": {"path": str(path), "error": capture_error},
    }


def _show_mode(app: Any, page_name: str, mode: str) -> None:
    app.navigation.show(page_name)
    page = app.navigation.pages[page_name]
    if page_name == "awp1018":
        page.show_mode(mode, update_chip=False)
    else:
        page.show_mode(mode)


def _check_scene(observation: dict[str, Any], errors: list[str]) -> None:
    page = observation["page"]
    mode = observation["mode"]
    label = observation["label"]
    prefix = f"{page}/{mode}/{label}"
    if observation["window_state"] != ("zoomed" if label == "zoomed" else "normal"):
        errors.append(f"{prefix}: Tk state={observation['window_state']!r}")

    root = observation["root"]
    if label == "zoomed" and (root["width"] < 1100 or root["height"] < 650):
        errors.append(f"{prefix}: maximized root unexpectedly small {root['width']}x{root['height']}")
    if label == "restored" and (root["width"] < 980 or root["height"] < 650):
        errors.append(f"{prefix}: restored root unexpectedly small {root['width']}x{root['height']}")

    active = observation["active_mode"]
    outer = observation["outer_scroll"]
    if not active["mapped"]:
        errors.append(f"{prefix}: active mode is not mapped")
    if not outer["rect"]["height"] >= 120 or not outer["canvas"]["height"] >= 100:
        errors.append(f"{prefix}: outer mode viewport is collapsed {outer['canvas']}")
    if label == "zoomed" and active["fill_ratio"] < 0.90:
        errors.append(
            f"{prefix}: active form fills only {active['fill_ratio']:.1%} of the maximized viewport"
        )

    stage = observation["stage"]
    if not stage.get("mapped"):
        errors.append(f"{prefix}: ADC stage frame is not mapped ({stage.get('error', '')})")
    elif stage["direct_mapped_children"] < 2 or stage["mapped_descendants"] < 6:
        errors.append(
            f"{prefix}: ADC stage is mapped but has too few visible controls "
            f"(direct={stage['direct_mapped_children']}, descendants={stage['mapped_descendants']})"
        )

    body = observation["body"]
    columns = body.get("columns", [])
    if page == "awp1018" and mode == "full_mode" and label == "zoomed":
        if len(columns) < 3:
            errors.append(f"{prefix}: full mode body exposes only {len(columns)} columns")
        for column in columns:
            if column["rect"]["width"] < 280:
                errors.append(
                    f"{prefix}: body column {column['column']} is compressed to "
                    f"{column['rect']['width']}px"
                )
    elif page == "awp1018" and label == "zoomed":
        if len(columns) < 2:
            errors.append(f"{prefix}: normal/diff body exposes only {len(columns)} columns")
        for column in columns:
            if column["rect"]["width"] < 450:
                errors.append(
                    f"{prefix}: body column {column['column']} is compressed to "
                    f"{column['rect']['width']}px"
                )
    elif page == "awp1221" and label == "zoomed":
        if len(columns) < 2:
            errors.append(f"{prefix}: AWP1221 body exposes only {len(columns)} columns")
        for column in columns:
            if column["rect"]["width"] < 260:
                errors.append(
                    f"{prefix}: AWP1221 body column {column['column']} is compressed to "
                    f"{column['rect']['width']}px"
                )

    if stage.get("overflow_at_current_x", 0) > 2:
        nearest_area = stage.get("nearest_scroll_area")
        # A horizontally scrollable nested column may legitimately start with
        # a clipped far edge, but it must expose a real horizontal endpoint;
        # the endpoint check below records that separately.
        if not any(
            area["horizontal_scrollbar_mapped"]
            for area in observation["nested_scroll"]
            if area["class"] == nearest_area
        ) and not outer["horizontal_scrollbar_mapped"]:
            errors.append(
                f"{prefix}: ADC stage overflows its visible canvas by "
                f"{stage['overflow_at_current_x']}px without a horizontal endpoint"
            )

    capture = observation["capture"]
    if capture["error"]:
        errors.append(f"{prefix}: screenshot capture failed: {capture['error']}")


def _propagate_width_probe(root: tk.Tk, ctx: Any) -> dict[str, Any]:
    """Reproduce the focused shared VerticalScrollArea width regression."""

    probe = tk.Toplevel(root)
    probe.title("propagate_width QA probe")
    probe.geometry("900x400+20+20")
    body = ttk.Frame(probe)
    body.pack(fill="both", expand=True)
    body.columnconfigure(0, weight=0)
    body.columnconfigure(1, weight=1)

    area = VerticalScrollArea(body, ctx, propagate_width=True)
    area.grid(row=0, column=0, sticky="nsew")
    form = ttk.Frame(area.content)
    form.grid(row=0, column=0, sticky="ew")
    for column, minsize in enumerate((126, 180, 126, 180)):
        form.columnconfigure(column, minsize=minsize)
        ttk.Entry(form, width=16).grid(row=0, column=column, sticky="ew")
    fat = ttk.Frame(body, width=700, height=200)
    fat.grid(row=0, column=1, sticky="nsew")
    fat.grid_propagate(False)
    _flush(root, 8)

    canvas = area.canvas
    right_edge = canvas.winfo_rootx() + canvas.winfo_width()
    farthest = max(
        (child.winfo_rootx() + child.winfo_width() for child in form.winfo_children() if _mapped(child)),
        default=canvas.winfo_rootx(),
    )
    before_x = _float_pair(canvas.xview())
    canvas.xview_moveto(1.0)
    _flush(root, 3)
    after_x = _float_pair(canvas.xview())
    result = {
        "overflow": max(0, int(farthest - right_edge)),
        "area_width": int(area.winfo_width()),
        "canvas_width": int(canvas.winfo_width()),
        "form_width": int(form.winfo_width()),
        "form_req_width": int(form.winfo_reqwidth()),
        "scrollregion": _region(canvas),
        "horizontal_scrollbar_mapped": _mapped(area.horizontal_scrollbar),
        "xview_before": before_x,
        "xview_after": after_x,
    }
    probe.destroy()
    _flush(root, 3)
    return result


def _check_endpoints(app: Any, observation: dict[str, Any], errors: list[str]) -> None:
    if observation["label"] != "restored":
        return
    page = app.navigation.pages[observation["page"]]
    mode = observation["mode"]
    _show_mode(app, observation["page"], mode)
    _flush(app.root, 8)
    areas = [page.mode_scroll]
    active = page.mode_pages[mode]
    areas.extend(
        area
        for area in _descendants(active)
        if area.__class__.__name__ == "VerticalScrollArea" and _mapped(area)
    )
    endpoint_results: list[dict[str, Any]] = []
    for area in areas:
        canvas = area.canvas
        before_y = _float_pair(canvas.yview())
        before_x = _float_pair(canvas.xview())
        canvas.yview_moveto(1.0)
        canvas.xview_moveto(1.0)
        _flush(app.root, 3)
        after_y = _float_pair(canvas.yview())
        after_x = _float_pair(canvas.xview())
        region = _region(canvas)
        y_reachable = len(region) == 4 and (region[3] <= canvas.winfo_height() + 1 or after_y[1] >= before_y[1])
        x_reachable = len(region) == 4 and (region[2] <= canvas.winfo_width() + 1 or after_x[1] >= before_x[1])
        endpoint_results.append(
            {
                "propagate_width": bool(getattr(area, "propagate_width", False)),
                "canvas": _rect(canvas),
                "region": region,
                "before_y": before_y,
                "after_y": after_y,
                "before_x": before_x,
                "after_x": after_x,
                "vertical_reachable": y_reachable,
                "horizontal_reachable": x_reachable,
            }
        )
        if not y_reachable:
            errors.append(f"{observation['page']}/{mode}/restored: vertical endpoint did not advance")
        if not x_reachable:
            errors.append(f"{observation['page']}/{mode}/restored: horizontal endpoint did not advance")
    observation["endpoints"] = endpoint_results


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    observations: list[dict[str, Any]] = []
    errors: list[str] = []
    with mocked_application() as app:
        root = app.root
        root.deiconify()
        root.state("normal")
        root.geometry("1000x700+20+20")
        _flush(root, 12)

        propagate_probe = _propagate_width_probe(root, app.ctx)
        if propagate_probe["overflow"] > 1:
            errors.append(
                "shared propagate_width probe overflows by "
                f"{propagate_probe['overflow']}px (area={propagate_probe['area_width']}px, "
                f"form={propagate_probe['form_width']}px)"
            )

        for page_name, mode in SCENES:
            _show_mode(app, page_name, mode)
            root.state("zoomed")
            _flush(root, 20)
            zoomed = _snapshot(app, page_name, mode, "zoomed")
            observations.append(zoomed)
            _check_scene(zoomed, errors)

            root.state("normal")
            root.geometry("1000x700+20+20")
            _flush(root, 20)
            restored = _snapshot(app, page_name, mode, "restored")
            observations.append(restored)
            _check_scene(restored, errors)

        for observation in observations:
            _check_endpoints(app, observation, errors)

    status = "FULL_MODE_QA_READY" if not errors else "BLOCKED"
    payload = {
        "scope": "AWP1018 Normal/Diff/Full and AWP1221 Button/Full real Tk zoomed/1000x700 follow-up",
        "hardware_boundary": "LocalI2cBridge and SerialManager are mocked; no serial/I2C/firmware action",
        "propagate_width_probe": propagate_probe,
        "observations": observations,
        "errors": errors,
        "status": status,
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# AWP1018 / AWP1221 Full Mode 布局专项验证",
        "",
        "本报告只覆盖后续计划定义的真实 Tk 布局边界：AWP1018 Normal、Diff、Full，AWP1221 Button、Full；每个场景均执行真实 `root.state('zoomed')`，随后恢复到 `1000x700`，并保存顶层窗口实际截图。脚本通过离线 harness mock 桥接、串口和传输入口，不读写 I²C、不写寄存器、不刷固件。",
        "",
        f"- 检查状态：`{status}`。",
        "- 场景：5 个模式，每个模式最大化和恢复各 1 张，共 10 张实际截图。",
        f"- JSON 证据：`{JSON_PATH}`。",
        "- `propagate_width` 共享回归探针与页面内部 ADC/多列区域均纳入几何断言。",
        "- 本专项没有写入产品源文件；实现者的并行产品改动不在本报告中归因。",
        "",
        "## propagate_width 探针",
        "",
        f"- overflow=`{propagate_probe['overflow']}px`，area=`{propagate_probe['area_width']}px`，canvas=`{propagate_probe['canvas_width']}px`，form=`{propagate_probe['form_width']}px`，form request=`{propagate_probe['form_req_width']}px`，横向滚动条={'显示' if propagate_probe['horizontal_scrollbar_mapped'] else '隐藏'}。",
        f"- 横向视图：`{propagate_probe['xview_before']} -> {propagate_probe['xview_after']}`。",
        "",
        "## 实际截图和布局证据",
        "",
    ]
    for observation in observations:
        capture = observation["capture"]
        bmp = Path(capture["path"])
        display = bmp.with_suffix(".png") if bmp.with_suffix(".png").exists() else bmp
        outer = observation["outer_scroll"]
        active = observation["active_mode"]
        lines.append(
            f"- `{observation['page']}/{observation['mode']}/{observation['label']}`：`{display}`；state=`{observation['window_state']}`；根窗口 {observation['root']['width']}×{observation['root']['height']}；mode canvas {outer['canvas']['width']}×{outer['canvas']['height']}；active 填充率 {active['fill_ratio']:.1%}。"
        )
        lines.append(
            f"  - ADC stage `{observation['stage'].get('label')}` mapped=`{observation['stage'].get('mapped')}`，stage {observation['stage'].get('rect')}，request width=`{observation['stage'].get('req_width')}`，当前横向溢出=`{observation['stage'].get('overflow_at_current_x', 0)}px`；body columns=`{observation['body'].get('columns', [])}`。"
        )
        for index, area in enumerate(observation["nested_scroll"]):
            lines.append(
                f"  - nested scroll {index}: canvas {area['canvas']['width']}×{area['canvas']['height']}，content request {area['content_req_width']}×{area['content_req_height']}，hscroll={'显示' if area['horizontal_scrollbar_mapped'] else '隐藏'}，propagate_width=`{area['propagate_width']}`。"
            )
        for index, endpoint in enumerate(observation.get("endpoints", [])):
            lines.append(
                f"  - endpoint {index}: region=`{endpoint['region']}`，Y `{endpoint['before_y']} -> {endpoint['after_y']}`，X `{endpoint['before_x']} -> {endpoint['after_x']}`，可达=`{endpoint['vertical_reachable']}/{endpoint['horizontal_reachable']}`。"
            )
    lines.extend(["", "## 问题", ""])
    if errors:
        lines.extend(f"- BLOCKED：{error}" for error in errors)
    else:
        lines.append("- 未发现本阶段定义的最大化、恢复、ADC 表单压缩、内部多列不可达或 propagate_width 问题。")
    lines.extend(["", "## 结论", "", status, ""])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"status={status}")
    print(f"observations={len(observations)} errors={len(errors)}")
    print(f"propagate_width_overflow={propagate_probe['overflow']}")
    print(f"report={REPORT_PATH}")
    print(f"json={JSON_PATH}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
