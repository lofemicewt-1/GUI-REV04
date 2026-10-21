"""Literal ADC Stage transition diagram and rule table shared by chip pages."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk, font as tkfont
from typing import Any, Callable, Iterable, Mapping

try:
    from PIL import Image, ImageDraw, ImageTk
except (ImportError, OSError):  # Canvas remains usable if Pillow DLLs cannot load.
    Image = ImageDraw = ImageTk = None


_EVENT_COLORS = {
    "press": "#2563EB",
    "release": "#9333EA",
    "detecting": "#FACC15",
    "play": "#FACC15",
    "error": "#DC2626",
    "idle": "#6B7280",
}


class AdcLegend(ttk.Frame):
    """Render configured ADC stages, their transitions, and the live event line.

    Stage records accept ``id``, ``condition`` (``press``/``release``),
    ``comparator``, ``threshold``, ``unit``, ``waveform``/``wave_label``,
    ``target`` and ``unused``. ``start_markers`` maps a Stage ID to one or more
    already-localized channel/group labels. The widget only renders the
    supplied configuration; live P/R events never set a Stage as actual.
    """

    def __init__(
        self,
        parent: tk.Misc,
        tr: Callable[..., str],
        colors: Mapping[str, str],
        stage_count: int = 2,
        *,
        on_stage_activate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.tr = tr
        self.colors = dict(colors)
        self.stage_count = max(1, int(stage_count))
        self._stages: list[dict[str, Any]] = []
        self._starts: dict[str, list[str]] = {}
        self._preview_stage: str | None = None
        self._live_state = "idle"
        self._live_detail = ""
        self._channel_states: dict[Any, Any] = {}
        self._on_stage_activate = on_stage_activate
        self._render_job: str | None = None
        self._disposed = False
        self._connector_cache = None
        self._connector_photo = None
        self._rule_rows = {}
        self._canvas_bind_id: str | None = None

        self.columnconfigure(0, weight=1)
        title = ttk.Label(self, text=self._tr("adc_legend_title"), style="Panel.TLabel")
        title.grid(row=0, column=0, sticky="w", pady=(0, 5))

        self._live_frame = tk.Frame(self, highlightthickness=2, bd=0)
        self._live_frame.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self._live_frame.columnconfigure(1, weight=1)
        self._live_dot = tk.Label(self._live_frame, text="●", font=("TkDefaultFont", 11, "bold"), bd=0)
        self._live_dot.grid(row=0, column=0, sticky="w", padx=(9, 5), pady=6)
        self._live_label = tk.Label(self._live_frame, anchor="w", font=self._ui_font(1.0, True), bd=0, wraplength=220)
        self._live_label.grid(row=0, column=1, sticky="w", padx=(0, 4), pady=6)
        self._live_detail_label = tk.Label(self._live_frame, anchor="w", bd=0, wraplength=260)
        self._live_detail_label.grid(row=0, column=2, sticky="ew", padx=(0, 9), pady=6)

        self._live_tip = ttk.Label(self, text=self._tr("adc_legend_live_tip"), style="Muted.TLabel", wraplength=820)
        self._live_tip.grid(row=2, column=0, sticky="ew", pady=(0, 5))

        self.canvas = tk.Canvas(
            self,
            # Modest reqwidth so the ADC side-by-side split does not thrash;
            # grid sticky=ew expands to the allocated panel width.
            width=320,
            height=260,
            highlightthickness=1,
            highlightbackground=self.colors.get("border", "#D1D5DB"),
            borderwidth=0,
        )
        self.canvas.grid(row=3, column=0, sticky="ew")
        self._canvas_bind_id = self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")
        self.canvas.tag_bind("stage_click", "<Button-1>", self._on_canvas_stage_click)
        self.canvas.tag_bind("stage_click", "<Enter>", self._on_canvas_stage_enter)
        self.canvas.tag_bind("stage_click", "<Leave>", self._on_canvas_stage_leave)

        self._table = ttk.Frame(self, style="Panel.TFrame")
        self._table.grid(row=4, column=0, sticky="ew", pady=(5, 0))
        self._table.bind("<Configure>", self._queue_fit_rule_columns, add="+")
        # Parent allocation drives width; avoid inflating ADC split measurements.
        self.columnconfigure(0, weight=1, minsize=0)
        self._fit_job = None
        self._fit_retries = 0

        self._refresh_live_line()
        self._refresh_rule_table()

    def _tr(self, key: str, **values: Any) -> str:
        try:
            result = self.tr(key, **values)
        except (KeyError, TypeError, ValueError):
            result = key
        return str(result)

    def _minimum_width(self) -> int:
        per_stage = 190 if self.stage_count <= 2 else 142
        return max(400, 40 + self.stage_count * per_stage + max(0, self.stage_count - 1) * 24)

    @staticmethod
    def _stage_id(value: Any) -> str:
        if isinstance(value, int):
            return f"Stage{value}"
        text = str(value or "").strip()
        if text.isdigit():
            return f"Stage{text}"
        return text

    @classmethod
    def _target_id(cls, value: Any) -> str | None:
        target = cls._stage_id(value)
        return target if target.startswith("Stage") and target[5:].isdigit() else None

    @classmethod
    def _normalize_stage(cls, raw: Mapping[str, Any], index: int) -> dict[str, Any]:
        stage_id = cls._stage_id(raw.get("id", raw.get("stage_id", raw.get("stage", f"Stage{index}"))))
        condition = str(raw.get("condition", "unknown")).strip().lower()
        if condition not in {"press", "release"}:
            condition = "unknown"
        threshold_raw = raw.get("threshold")
        threshold = "" if threshold_raw is None else str(threshold_raw).strip()
        threshold_valid = bool(threshold)
        if threshold_raw is not None and not isinstance(threshold_raw, str):
            try:
                number = float(threshold_raw)
                if math.isfinite(number):
                    precision = raw.get("precision")
                    threshold = f"{number:.{int(precision)}f}" if precision is not None else f"{number:g}"
                else:
                    threshold = ""
                    threshold_valid = False
            except (TypeError, ValueError, OverflowError):
                threshold = ""
                threshold_valid = False
        elif threshold:
            try:
                threshold_valid = math.isfinite(float(threshold))
            except (TypeError, ValueError, OverflowError):
                try:
                    int(threshold, 0)
                    threshold_valid = True
                except (TypeError, ValueError, OverflowError):
                    try:
                        int(threshold, 10)
                        threshold_valid = True
                    except (TypeError, ValueError, OverflowError):
                        threshold_valid = False
        comparator = str(raw.get("comparator") or (">=" if condition == "press" else "<=" if condition == "release" else "?"))
        if condition == "unknown" or not threshold_valid:
            condition = "unknown"
            comparator = "?"
        target = raw.get("target")
        waveform = raw.get("wave_label", raw.get("waveform", raw.get("wave", "")))
        return {
            "id": stage_id,
            "condition": condition,
            "comparator": comparator,
            "threshold": threshold,
            "unit": str(raw.get("unit") or "").strip(),
            "waveform": str(waveform or "").strip(),
            "target": cls._stage_id(target) if target is not None else None,
            "unused": bool(raw.get("unused", False)),
        }

    @staticmethod
    def _normalize_starts(start_markers: Any) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        if isinstance(start_markers, Mapping):
            items: Iterable[tuple[Any, Any]] = start_markers.items()
        else:
            items = start_markers or ()
        for stage, markers in items:
            stage_id = AdcLegend._stage_id(stage)
            if markers is True or markers is None:
                values = []
            elif isinstance(markers, str):
                values = [markers] if markers else []
            else:
                try:
                    values = [str(marker) for marker in markers if marker]
                except TypeError:
                    values = [str(markers)] if markers else []
            result.setdefault(stage_id, []).extend(values)
        return result

    def set_rules(self, stages: Iterable[Mapping[str, Any]], start_markers: Any, preview_stage: Any = None) -> None:
        if self._disposed:
            return
        self._stages = [self._normalize_stage(stage, index) for index, stage in enumerate(stages)]
        self._starts = self._normalize_starts(start_markers)
        self._preview_stage = self._stage_id(preview_stage) if preview_stage is not None else None
        self._render_locked_width = None
        self._diagram_signature = None
        self._refresh_rule_table()
        self._schedule_render()

    def set_stage_activate(self, callback: Callable[[str], None] | None) -> None:
        """Set the callback used when a Stage card or table row is activated."""
        self._on_stage_activate = callback

    def activate_stage(self, stage_id: Any) -> None:
        """Activate a Stage using the same ID normalization as the legend."""
        if self._disposed or self._on_stage_activate is None:
            return
        self._preview_stage = self._stage_id(stage_id)
        self._render_locked_width = None
        self._diagram_signature = None
        self._schedule_render()
        self._on_stage_activate(self._preview_stage)

    def _current_canvas_stage(self) -> str | None:
        try:
            current = self.canvas.find_withtag("current")
            if not current:
                return None
            for tag in self.canvas.gettags(current[-1]):
                if self._target_id(tag) == tag:
                    return tag
        except tk.TclError:
            return None
        return None

    def _on_canvas_stage_click(self, _event: tk.Event | None = None) -> None:
        if self._disposed:
            return
        stage_id = self._current_canvas_stage()
        if stage_id is not None:
            self.activate_stage(stage_id)

    def _on_canvas_stage_enter(self, _event: tk.Event | None = None) -> None:
        if self._disposed or self._on_stage_activate is None or self._current_canvas_stage() is None:
            return
        try:
            self.canvas.configure(cursor="hand2")
        except tk.TclError:
            pass

    def _on_canvas_stage_leave(self, _event: tk.Event | None = None) -> None:
        if self._disposed:
            return
        try:
            self.canvas.configure(cursor="")
        except tk.TclError:
            pass

    def _on_rule_stage_click(self, stage_id: str, _event: tk.Event | None = None) -> None:
        self.activate_stage(stage_id)

    def _on_rule_stage_enter(self, event: tk.Event) -> None:
        if self._disposed or self._on_stage_activate is None:
            return
        self._highlight_rule_row(event.widget, True)

    def _on_rule_stage_leave(self, event: tk.Event) -> None:
        self._highlight_rule_row(event.widget, False)

    def _highlight_rule_row(self, widget: tk.Misc, hovered: bool) -> None:
        panel = self.colors.get("panel", "#FFFFFF")
        hover_bg = self._tint(self.colors.get("primary", "#2563EB"), panel, 0.08)
        try:
            row = int(widget.grid_info()["row"])
        except (tk.TclError, AttributeError, KeyError, TypeError, ValueError):
            return
        previous = getattr(self, "_hovered_rule_row", None)
        if hovered:
            if previous is not None and previous != row:
                self._apply_rule_row_chrome(previous, False, panel, hover_bg)
            self._hovered_rule_row = row
            self._apply_rule_row_chrome(row, True, panel, hover_bg)
            return
        if previous == row:
            self._hovered_rule_row = None
        self._apply_rule_row_chrome(row, False, panel, hover_bg)

    def _apply_rule_row_chrome(self, row: int, hovered: bool, panel: str, hover_bg: str) -> None:
        for cell in self._rule_rows.get(row, ()):
            try:
                base = getattr(cell, "_adc_rule_bg", panel)
                cell.configure(cursor="hand2" if hovered else "", background=hover_bg if hovered else base)
            except (tk.TclError, AttributeError):
                pass

    def _ui_font(self, size: float = 1.0, bold: bool = False) -> tuple:
        root = self.winfo_toplevel()
        family = getattr(self, "_ui_font_family", None)
        if family is None:
            family = getattr(root, "_piezo_font_family", "Microsoft YaHei UI")
            try:
                available = set(tkfont.families(root))
            except tk.TclError:
                available = set()
            if family not in available:
                try:
                    family = tkfont.nametofont("TkDefaultFont", root=root).actual("family")
                except tk.TclError:
                    family = "TkDefaultFont"
            self._ui_font_family = family
        body = getattr(root, "_piezo_body_font_size", 10)
        return (family, max(1, round(body * size)), "bold" if bold else "normal")

    def _queue_fit_rule_columns(self, _event=None) -> None:
        if self._disposed or self._fit_job is not None:
            return
        try:
            self._fit_job = self.after_idle(self._fit_rule_columns)
        except tk.TclError:
            self._fit_job = None
        self._fit_retries = 0

    def _fit_rule_columns(self, _event=None) -> None:
        self._fit_job = None
        self._fit_retries = 0
        if self._disposed or not hasattr(self, "_table"):
            return
        if not self._table.winfo_children() or getattr(self, "_fitting_columns", False):
            return
        self._fitting_columns = True
        try:
            self.update_idletasks()
            cells = [cell for cell in self._table.winfo_children() if cell.grid_info()]
            if not cells:
                return
            natural = [0] * 4
            for cell in cells:
                column = int(cell.grid_info()["column"])
                font = tkfont.Font(root=self, font=cell.cget("font"))
                natural[column] = max(natural[column], font.measure(str(cell.cget("text"))) + 16)
            available = max(1, int(self._table.winfo_width()))
            if available <= 40:
                retries = getattr(self, "_fit_retries", 0)
                if retries < 6:
                    self._fit_retries = retries + 1
                    try:
                        self.after(40, self._queue_fit_rule_columns)
                    except tk.TclError:
                        pass
                return
            self._fit_retries = 0
            target = max(48, min(natural[3] or 48, max(48, available // 8)))
            budget = max(3, available - target)
            base = [max(1, natural[i] or 1) for i in range(3)]
            # Prefer one-line 「在等什么」; keep 「满足后」 tight; widen 「阶段」.
            stage_w = min(max(base[0], 96), max(96, int(budget * 0.30)))
            wave_w = min(max(base[1], 72), max(72, int(budget * 0.18)))
            wait_w = max(120, budget - stage_w - wave_w)
            if base[2] > wait_w:
                # Borrow from wave then stage to keep wait on one line when possible.
                need = base[2] - wait_w
                take = min(need, max(0, wave_w - 72))
                wave_w -= take; need -= take; wait_w += take
                take = min(need, max(0, stage_w - 96))
                stage_w -= take; wait_w += take
            widths = [stage_w, wave_w, wait_w, target]
            changed = getattr(self, "_column_widths", None) != widths
            self._column_widths = widths
            if changed:
                for column, width in enumerate(widths):
                    self._table.columnconfigure(column, weight=max(1, width), minsize=24)
                self._table.update_idletasks()
            for cell in cells:
                column = int(cell.grid_info()["column"])
                # Grid distributes space using both requested sizes and weights.
                # Wrap to the allocated cell, not the ideal column budget.
                allocated = self._table.grid_bbox(column, int(cell.grid_info()["row"]))[2]
                padding = 16 if isinstance(cell, tk.Label) else 0
                want = max(1, allocated - padding)
                try:
                    if int(cell.cget("wraplength") or 0) != want:
                        cell.configure(wraplength=want)
                except (tk.TclError, TypeError, ValueError):
                    cell.configure(wraplength=want)
        except (tk.TclError, TypeError, ValueError, AttributeError):
            return
        finally:
            self._fitting_columns = False

    def set_live(self, state: str, detail: str = "", channel_states: Mapping[Any, Any] | None = None) -> None:
        if self._disposed:
            return
        normalized = str(state or "idle").lower()
        if normalized not in _EVENT_COLORS:
            normalized = "error" if normalized in {"fault", "timeout"} else "idle"
        self._live_state = normalized
        self._live_detail = str(detail or "")
        self._channel_states = dict(channel_states or {})
        self._refresh_live_line()

    def refresh_style(self, colors: Mapping[str, str] | None = None) -> None:
        if self._disposed:
            return
        if colors is not None:
            self.colors = dict(colors)
        try:
            self.canvas.configure(highlightbackground=self.colors.get("border", "#D1D5DB"))
        except tk.TclError:
            return
        self._refresh_live_line()
        self._refresh_rule_table()
        self._schedule_render()

    def reset_live(self) -> None:
        self.set_live("idle", "")

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._render_job is not None:
            try:
                self.after_cancel(self._render_job)
            except tk.TclError:
                pass
            self._render_job = None
        if getattr(self, "_fit_job", None) is not None:
            try:
                self.after_cancel(self._fit_job)
            except tk.TclError:
                pass
            self._fit_job = None
        self._fit_retries = 0
        if self._canvas_bind_id is not None:
            try:
                self.canvas.unbind("<Configure>", self._canvas_bind_id)
            except tk.TclError:
                pass
            self._canvas_bind_id = None

    def destroy(self) -> None:
        self.dispose()
        super().destroy()

    def _on_canvas_configure(self, _event: tk.Event | None = None) -> None:
        if self._disposed:
            return
        try:
            width = int(self.canvas.winfo_width())
        except (tk.TclError, TypeError, ValueError):
            width = 0
        locked = getattr(self, "_render_locked_width", None)
        if locked is not None and width > 1 and abs(width - locked) < 180:
            return
        self._schedule_render()

    def _schedule_render(self) -> None:
        if self._disposed or self._render_job is not None:
            return
        try:
            self._render_job = self.after_idle(self._render_diagram)
        except tk.TclError:
            self._render_job = None

    def _render_diagram(self) -> None:
        self._render_job = None
        if self._disposed:
            return
        try:
            actual_width = max(1, self.canvas.winfo_width())
            width = max(actual_width, self._minimum_width())
            stages = self._stages[: self.stage_count]
            if not stages:
                return
            signature = (
                int(width),
                self.stage_count,
                self._preview_stage,
                tuple(
                    (
                        stage.get("id"),
                        stage.get("condition"),
                        stage.get("threshold"),
                        stage.get("unit"),
                        stage.get("waveform"),
                        stage.get("target"),
                        stage.get("unused"),
                    )
                    for stage in stages
                ),
                tuple(sorted((key, tuple(values)) for key, values in self._starts.items())),
                tuple(sorted(self.colors.items())),
                self._ui_font(1.0, False),
            )
            if getattr(self, "_diagram_signature", None) == signature and self.canvas.find_all():
                return
            self._diagram_signature = signature
            self._connector_cache = None
            self.canvas.configure(bg=self.colors.get("canvas_bg", "#FFFFFF"))
            self.canvas.delete("all")
            count = len(stages)
            margin = 18
            gap = 26 if count > 2 else 36
            slot_width = (width - 2 * margin) / count
            card_width = min(260 if count <= 2 else 230, max(110, slot_width - gap))
            card_top, card_height = 64, 78
            by_id = {stage["id"]: index for index, stage in enumerate(stages)}
            # Measure wrapped forward labels before placing the cards. Centered
            # labels at a fixed y can extend above the canvas with larger fonts.
            for index, stage in enumerate(stages):
                if by_id.get(stage["target"]) != index + 1:
                    continue
                center = margin + (index + 1) * slot_width
                item = self.canvas.create_text(
                    center, 8, anchor="n",
                    text=self._edge_text(stage, index + 1, back=False),
                    fill=self._condition_color(stage["condition"]),
                    font=self._ui_font(1.35, True),
                    width=max(90, slot_width - card_width - 3 + card_width * 0.65),
                    tags=("transition_label",),
                )
                card_top = max(card_top, self.canvas.bbox(item)[3] + 8)
            card_bottom = card_top + card_height
            boxes: list[tuple[float, float, float, float]] = []
            content_bottom = card_bottom - 8
            for index, stage in enumerate(stages):
                x0 = margin + index * slot_width + (slot_width - card_width) / 2
                x1 = x0 + card_width
                y0, y1 = card_top, card_bottom
                boxes.append((x0, y0, x1, y1))
                unused = bool(stage["unused"] and stage["target"] == stage["id"])
                preview = stage["id"] == self._preview_stage
                border = self.colors.get("border", "#D1D5DB")
                text = self.colors.get("text", "#1F2937")
                fill = self.colors.get("panel", "#FFFFFF")
                if unused:
                    border = self.colors.get("muted", "#9CA3AF")
                    text = self.colors.get("muted", "#9CA3AF")
                    fill = self._tint(border, fill, 0.08)
                elif preview:
                    border = self.colors.get("primary", "#2563EB")
                    fill = self._tint(border, fill, 0.10)
                self.canvas.create_rectangle(
                    x0, y0, x1, y1, fill=fill, outline=border,
                    width=3 if preview else 2, tags=("stage_box", "stage_click", stage["id"]),
                )
                heading = stage["id"]
                heading_item = self.canvas.create_text(
                    (x0 + x1) / 2, y0 + 8, anchor="n", text=heading, fill=text,
                    font=self._ui_font(1.55, True), width=card_width - 18,
                    tags=("stage_heading", "stage_click", stage["id"]),
                )
                text_top = self.canvas.bbox(heading_item)[3] + 2
                markers = self._starts.get(stage["id"], [])
                marker_text = " · ".join(
                    ([self._tr("adc_legend_start")] if stage["id"] in self._starts else []) + markers
                )
                # Do not annotate cards with 「示意 Stage」 / illustrative labels.
                if marker_text:
                    marker_item = self.canvas.create_text(
                        (x0 + x1) / 2, text_top, anchor="n", text=marker_text, fill=border,
                        font=self._ui_font(1.4, True), width=card_width - 18,
                        tags=("stage_marker", "stage_click", stage["id"]),
                    )
                    text_top = self.canvas.bbox(marker_item)[3] + 2
                if unused:
                    body_text = self._tr("adc_legend_unused_self")
                    wave_text = ""
                else:
                    body_text = self._wait_text(stage)
                    wave_text = stage["waveform"]
                body_item = self.canvas.create_text(
                    (x0 + x1) / 2, text_top, anchor="n", text=body_text, fill=text,
                    font=self._ui_font(1.45), width=card_width - 18,
                    tags=("stage_wait", "stage_click", stage["id"]),
                )
                content_bottom = max(content_bottom, self.canvas.bbox(body_item)[3])
                if wave_text:
                    wave_item = self.canvas.create_text(
                        (x0 + x1) / 2, self.canvas.bbox(body_item)[3] + 2, anchor="n",
                        text=f"{self._tr('adc_legend_play_wave')} {wave_text}", fill=self.colors.get("muted", "#6B7280"),
                        font=self._ui_font(1.4), width=card_width - 18,
                        tags=("stage_waveform", "stage_click", stage["id"]),
                    )
                    content_bottom = max(content_bottom, self.canvas.bbox(wave_item)[3])

            # Let wrapped labels expand compact cards instead of overlapping.
            card_bottom = max(card_bottom, content_bottom + 8)
            card_height = card_bottom - card_top
            for index, item in enumerate(self.canvas.find_withtag("stage_box")):
                x0, y0, x1, _ = boxes[index]
                boxes[index] = (x0, y0, x1, card_bottom)
                self.canvas.coords(item, x0, y0, x1, card_bottom)
            # Immediate forward transitions stay above the row; returns and
            # skips use separate lower lanes so each configured edge remains visible.
            connectors = []
            lane_y = card_bottom + 16
            for index, stage in enumerate(stages):
                target_index = by_id.get(stage["target"])
                if target_index is None or (stage["unused"] and stage["target"] == stage["id"]):
                    continue
                x0, _y0, x1, _y1 = boxes[index]
                tx0, _ty0, tx1, _ty1 = boxes[target_index]
                source_x = (x0 + x1) / 2
                target_x = (tx0 + tx1) / 2
                color = self._condition_color(stage["condition"])
                if target_index == index + 1:
                    start_x, end_x = x1 + 1, tx0 - 2
                    arrow_y = card_top + card_height / 2
                    points = [(start_x, arrow_y), (end_x, arrow_y)]
                    connectors.append((points, color))
                    self.canvas.create_line(
                        start_x, arrow_y, end_x, arrow_y, fill=color, width=2,
                        smooth=True, splinesteps=48, arrow=tk.LAST, tags=("transition",),
                    )
                else:
                    if target_index == index:
                        start_x, end_x = source_x + card_width * 0.26, source_x - card_width * 0.26
                    else:
                        start_x, end_x = source_x, target_x
                    points = self._bezier_points(start_x, end_x, card_bottom - 1, lane_y)
                    connectors.append((points, color))
                    self.canvas.create_line(
                        start_x, card_bottom - 1,
                        start_x, lane_y,
                        end_x, lane_y,
                        end_x, card_bottom - 1,
                        fill=color, width=2, smooth=True, splinesteps=48,
                        arrow=tk.LAST, tags=("transition",),
                    )
                    label_width = max(100, abs(end_x - start_x) - 8)
                    label = self.canvas.create_text(
                        (start_x + end_x) / 2, lane_y + 3,
                        text=self._edge_text(stage, target_index, back=target_index <= index),
                        fill=color, font=self._ui_font(1.35, True),
                        width=min(width - 24, label_width), anchor="n",
                        tags=("transition_label",),
                    )
                    # A wrapped label owns its full lane, including descent.
                    lane_y = self.canvas.bbox(label)[3] + 12
            # Include every text bbox, rather than estimating from lane count.
            needed_height = math.ceil(self.canvas.bbox("all")[3] + 8)
            if int(self.canvas.cget("height")) != needed_height:
                self.canvas.configure(height=needed_height)
            self._render_connectors(width, needed_height, connectors)
            self._render_locked_width = int(width)
            self._queue_fit_rule_columns()
        except tk.TclError:
            return

    @staticmethod
    def _bezier_points(start_x, end_x, top, lane):
        points = []
        control_y = top + (lane - top) * 4 / 3
        for index in range(33):
            t = index / 32
            u = 1 - t
            points.append((u**3 * start_x + 3*u*u*t*start_x + 3*u*t*t*end_x + t**3*end_x,
                           u**3 * top + 3*u*u*t*control_y + 3*u*t*t*control_y + t**3*top))
        return points

    def _render_connectors(self, width, height, connectors) -> None:
        global ImageTk
        if Image is None or ImageDraw is None or ImageTk is None:
            self._connector_photo = None
            self._connector_cache = None
            return
        try:
            scale = self._ui_font()[1] / 10
            key = (int(width), int(height), scale, repr(connectors), tuple(sorted(self.colors.items())))
            if self._connector_cache != key:
                ss = 3
                raster = Image.new("RGBA", (int(width)*ss, int(height)*ss))
                draw = ImageDraw.Draw(raster)
                for points, color in connectors:
                    scaled = [(x*ss, y*ss) for x, y in points]
                    draw.line(scaled, fill=color, width=2*ss, joint="curve")
                    x, y = points[-1]
                    px, py = points[-2]
                    angle = math.atan2(y-py, x-px)
                    length, half = 9, 4
                    bx, by = x-length*math.cos(angle), y-length*math.sin(angle)
                    draw.polygon([(x*ss, y*ss), ((bx-half*math.sin(angle))*ss, (by+half*math.cos(angle))*ss),
                                  ((bx+half*math.sin(angle))*ss, (by-half*math.cos(angle))*ss)], fill=color)
                raster = raster.resize((int(width), int(height)), getattr(Image, "Resampling", Image).LANCZOS)
                self._connector_photo = ImageTk.PhotoImage(raster, master=self.canvas)
                self._connector_cache = key
            item = self.canvas.create_image(0, 0, anchor="nw", image=self._connector_photo, tags=("connector_image",))
            self.canvas.tag_lower(item)
            self.canvas.itemconfigure("transition", state="hidden")
        except (ImportError, OSError, tk.TclError):
            # ImageTk loads _imagingtk lazily; Windows App Control may block
            # that DLL. Disable raster connectors process-wide to avoid an
            # exception storm in subsequent after() callbacks and legends.
            ImageTk = None
            self._connector_photo = None
            self._connector_cache = None
            try:
                self.canvas.delete("connector_image")
                self.canvas.itemconfigure("transition", state="normal")
            except tk.TclError:
                pass  # The canvas may already have been destroyed.

    def _wait_text(self, stage: Mapping[str, Any]) -> str:
        condition = stage["condition"]
        if condition == "unknown":
            return self._tr("adc_legend_unknown")
        name = self._tr("adc_legend_wait_press" if condition == "press" else "adc_legend_wait_release")
        value = f"{stage['comparator']} {stage['threshold']} {stage['unit']}".strip()
        return f"{name} {value}".strip()

    def _edge_text(self, stage: Mapping[str, Any], target_index: int, *, back: bool) -> str:
        condition = stage["condition"]
        if condition == "unknown":
            condition_text = self._tr("adc_legend_unknown")
        else:
            name = self._tr("adc_legend_wait_press" if condition == "press" else "adc_legend_wait_release")
            condition_text = f"{name} {stage['comparator']} {stage['threshold']} {stage['unit']}".strip()
        target = self._stages[target_index]["id"]
        action_key = "adc_legend_self_loop" if target == stage["id"] else "adc_legend_return" if back else "adc_legend_enter"
        action = self._tr(action_key).format(stage=target)
        return f"{condition_text}\n{action}"

    def _condition_color(self, condition: str) -> str:
        if condition == "press":
            return _EVENT_COLORS["press"]
        if condition == "release":
            return _EVENT_COLORS["release"]
        return self.colors.get("muted", "#6B7280")

    def _refresh_rule_table(self) -> None:
        if not hasattr(self, "_table"):
            return
        try:
            for child in self._table.winfo_children():
                child.destroy()
            self._rule_rows = {}
            headers = (
                "adc_legend_col_stage", "adc_legend_col_wave",
                "adc_legend_col_wait", "adc_legend_col_target",
            )
            for column, key in enumerate(headers):
                hdr = ttk.Label(self._table, text=self._tr(key), style="Muted.TLabel", font=self._ui_font(), width=1)
                hdr.grid(row=0, column=column, sticky="ew", padx=0, ipadx=0, pady=(0, 2))
            for row, stage in enumerate(self._stages[: self.stage_count], start=1):
                stage_id = stage["id"]
                marks = []
                if stage_id in self._starts:
                    marks.append(self._tr("adc_legend_start"))
                    marks.extend(self._starts[stage_id])
                # Skip adc_legend_preview (「示意 Stage」) in the rule table.
                label = stage_id + (f" · {' / '.join(marks)}" if marks else "")
                if stage["unused"] and stage["target"] == stage_id:
                    wave = self._tr("adc_legend_unused")
                    wait = self._tr("adc_legend_unused_self")
                    target = stage_id
                    style = "Muted.TLabel"
                else:
                    wave = stage["waveform"] or self._tr("adc_legend_unknown")
                    wait = self._wait_text(stage)
                    target = stage["target"] or self._tr("adc_legend_unknown")
                    style = "Panel.TLabel"
                panel_bg = self.colors.get("panel", "#FFFFFF")
                text_fg = self.colors.get("muted", "#6B7280") if style == "Muted.TLabel" else self.colors.get("text", "#1F2937")
                for column, value in enumerate((label, wave, wait, target)):
                    # tk.Label so row hover can tint backgrounds independently (ttk shares styles).
                    cell = tk.Label(
                        self._table,
                        text=value,
                        font=self._ui_font(),
                        anchor="w",
                        justify="left",
                        padx=8,
                        pady=3,
                        background=panel_bg,
                        foreground=text_fg,
                        bd=0,
                        highlightthickness=0,
                        # Character width keeps reqwidth tiny so the ADC
                        # side-by-side split does not thrash; grid expands cells.
                        width=1,
                        wraplength=160,
                    )
                    cell.grid(row=row, column=column, sticky="nsew")
                    self._rule_rows.setdefault(row, []).append(cell)
                    cell._adc_rule_bg = panel_bg
                    cell.bind(
                        "<Button-1>",
                        lambda event, selected=stage_id: self._on_rule_stage_click(selected, event),
                    )
                    cell.bind("<Enter>", self._on_rule_stage_enter)
                    cell.bind("<Leave>", self._on_rule_stage_leave)
            self._fit_retries = 0
            self._fit_rule_columns()
            # Table often has width=1 during first build; retry shortly after layout.
            try:
                self.after(30, self._queue_fit_rule_columns)
                self.after(120, self._queue_fit_rule_columns)
            except tk.TclError:
                pass
        except tk.TclError:
            return

    def _refresh_live_line(self) -> None:
        if not hasattr(self, "_live_frame"):
            return
        color = _EVENT_COLORS[self._live_state]
        panel = self.colors.get("panel", "#FFFFFF")
        dark_text = self.colors.get("text", "#1F2937")
        background = self._tint(color, panel, 0.16)
        foreground = dark_text
        detail_parts = [self._live_detail] if self._live_detail and self._live_state != "play" else []
        if self._live_state == "play" and not detail_parts:
            detail_parts.append(self._tr("adc_legend_play_wave"))
        if self._channel_states:
            names = [str(name) for name, state in self._channel_states.items() if state in {"error", "fault", "timeout", False}]
            if names:
                channel_text = ", ".join(self._tr("adc_legend_live_channel", channel=name) for name in names)
                detail_parts.append(channel_text)
        try:
            self._live_frame.configure(bg=background, highlightbackground=color)
            for label in (self._live_dot, self._live_label, self._live_detail_label):
                label.configure(bg=background)
            self._live_dot.configure(fg=color)
            live_label_state = "detecting" if self._live_state == "play" else self._live_state
            live_text = self._tr("adc_legend_live_" + live_label_state)
            if self._live_state == "play" and self._live_detail:
                live_text = self._live_detail
            self._live_label.configure(
                text=f"{self._tr('adc_legend_live')}：{live_text}",
                fg=foreground,
            )
            self._live_detail_label.configure(text=" · ".join(part for part in detail_parts if part), fg=foreground)
            self._live_tip.configure(text=self._tr("adc_legend_live_tip"))  # keep color tip; no 示意 note
        except tk.TclError:
            return

    def _tint(self, foreground: str, background: str, ratio: float) -> str:
        try:
            fg = self.winfo_rgb(foreground)
            bg = self.winfo_rgb(background)
            mixed = [int((f * ratio + b * (1 - ratio)) / 257) for f, b in zip(fg, bg)]
            return "#{:02X}{:02X}{:02X}".format(*mixed)
        except tk.TclError:
            return background

