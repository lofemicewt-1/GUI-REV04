"""Literal ADC Stage transition diagram and rule table shared by chip pages."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Iterable, Mapping


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
        self._render_job: str | None = None
        self._disposed = False
        self._canvas_bind_id: str | None = None

        self.columnconfigure(0, weight=1)
        title = ttk.Label(self, text=self._tr("adc_legend_title"), style="Panel.TLabel")
        title.grid(row=0, column=0, sticky="w", pady=(0, 5))

        self._live_frame = tk.Frame(self, highlightthickness=2, bd=0)
        self._live_frame.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self._live_frame.columnconfigure(1, weight=1)
        self._live_dot = tk.Label(self._live_frame, text="●", font=("TkDefaultFont", 11, "bold"), bd=0)
        self._live_dot.grid(row=0, column=0, sticky="w", padx=(9, 5), pady=6)
        self._live_label = tk.Label(self._live_frame, anchor="w", font=("TkDefaultFont", 10, "bold"), bd=0)
        self._live_label.grid(row=0, column=1, sticky="w", padx=(0, 4), pady=6)
        self._live_detail_label = tk.Label(self._live_frame, anchor="w", bd=0)
        self._live_detail_label.grid(row=0, column=2, sticky="w", padx=(0, 9), pady=6)

        self._live_tip = ttk.Label(self, text=self._tr("adc_legend_live_tip"), style="Muted.TLabel", wraplength=820)
        self._live_tip.grid(row=2, column=0, sticky="ew", pady=(0, 5))

        self.canvas = tk.Canvas(
            self,
            width=self._minimum_width(),
            height=260,
            highlightthickness=1,
            highlightbackground=self.colors.get("border", "#D1D5DB"),
            borderwidth=0,
        )
        self.canvas.grid(row=3, column=0, sticky="ew")
        self._canvas_bind_id = self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")

        self._table = ttk.Frame(self, style="Panel.TFrame")
        self._table.grid(row=4, column=0, sticky="ew", pady=(5, 0))
        for column, weight in enumerate((1, 2, 3, 2)):
            self._table.columnconfigure(column, weight=weight, uniform="adc_legend_rule")

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
        self._refresh_rule_table()
        self._schedule_render()

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
            self.canvas.configure(bg=self.colors.get("canvas_bg", "#FFFFFF"))
            self.canvas.delete("all")
            stages = self._stages[: self.stage_count]
            if not stages:
                return
            count = len(stages)
            margin = 18
            gap = 26 if count > 2 else 36
            card_width = max(110, (width - 2 * margin - gap * (count - 1)) / count)
            card_top, card_height = 78, 112
            card_bottom = card_top + card_height
            by_id = {stage["id"]: index for index, stage in enumerate(stages)}
            lower_edges = [
                (index, stage, by_id.get(stage["target"]))
                for index, stage in enumerate(stages)
                if stage["target"] in by_id
                and (by_id[stage["target"]] != index + 1)
                and not (stage["unused"] and stage["target"] == stage["id"])
            ]
            needed_height = min(390, card_bottom + 32 + max(1, len(lower_edges)) * 28)
            self.canvas.configure(height=needed_height)
            boxes: list[tuple[float, float, float, float]] = []
            for index, stage in enumerate(stages):
                x0 = margin + index * (card_width + gap)
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
                    width=3 if preview else 2, tags=("stage_box", stage["id"]),
                )
                heading = stage["id"]
                self.canvas.create_text(
                    (x0 + x1) / 2, y0 + 16, text=heading, fill=text,
                    font=("TkDefaultFont", 10, "bold"), width=card_width - 12,
                    tags=("stage_heading", stage["id"]),
                )
                markers = self._starts.get(stage["id"], [])
                marker_text = " · ".join(
                    ([self._tr("adc_legend_start")] if stage["id"] in self._starts else []) + markers
                )
                if preview:
                    marker_text = " · ".join(filter(None, (marker_text, self._tr("adc_legend_preview"))))
                if marker_text:
                    self.canvas.create_text(
                        (x0 + x1) / 2, y0 + 37, text=marker_text, fill=border,
                        font=("TkDefaultFont", 8, "bold"), width=card_width - 12,
                        tags=("stage_marker", stage["id"]),
                    )
                if unused:
                    body_text = self._tr("adc_legend_unused_self")
                    wave_text = ""
                else:
                    body_text = self._wait_text(stage)
                    wave_text = stage["waveform"]
                self.canvas.create_text(
                    (x0 + x1) / 2, y0 + 66, text=body_text, fill=text,
                    font=("TkDefaultFont", 9), width=card_width - 12,
                    tags=("stage_wait", stage["id"]),
                )
                if wave_text:
                    self.canvas.create_text(
                        (x0 + x1) / 2, y0 + 91,
                        text=f"{self._tr('adc_legend_play_wave')} {wave_text}", fill=self.colors.get("muted", "#6B7280"),
                        font=("TkDefaultFont", 9), width=card_width - 12,
                        tags=("stage_waveform", stage["id"]),
                    )

            # Immediate forward transitions stay above the row; returns and
            # skips use separate lower lanes so each configured edge remains visible.
            lower_lane = 0
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
                    self.canvas.create_line(start_x, arrow_y, end_x, arrow_y, fill=color, width=2, arrow=tk.LAST, tags=("transition",))
                    self.canvas.create_text(
                        (start_x + end_x) / 2, 25,
                        text=self._edge_text(stage, target_index, back=False),
                        fill=color, font=("TkDefaultFont", 8, "bold"),
                        width=max(90, end_x - start_x + card_width * 0.65),
                        tags=("transition_label",),
                    )
                else:
                    lane_y = card_bottom + 16 + lower_lane * 26
                    lower_lane += 1
                    if target_index == index:
                        start_x, end_x = source_x + card_width * 0.26, source_x - card_width * 0.26
                    else:
                        start_x, end_x = source_x, target_x
                    self.canvas.create_line(
                        start_x, card_bottom - 1,
                        start_x, lane_y,
                        end_x, lane_y,
                        end_x, card_bottom - 1,
                        fill=color, width=2, smooth=True, splinesteps=18,
                        arrow=tk.LAST, tags=("transition",),
                    )
                    label_width = max(100, abs(end_x - start_x) - 8)
                    self.canvas.create_text(
                        (start_x + end_x) / 2, lane_y + 3,
                        text=self._edge_text(stage, target_index, back=target_index <= index),
                        fill=color, font=("TkDefaultFont", 8, "bold"),
                        width=min(width - 24, label_width), anchor="n",
                        tags=("transition_label",),
                    )
        except tk.TclError:
            return

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
            headers = (
                "adc_legend_col_stage", "adc_legend_col_wave",
                "adc_legend_col_wait", "adc_legend_col_target",
            )
            for column, key in enumerate(headers):
                ttk.Label(self._table, text=self._tr(key), style="Muted.TLabel").grid(
                    row=0, column=column, sticky="w", padx=(5, 8), pady=(0, 2)
                )
            for row, stage in enumerate(self._stages[: self.stage_count], start=1):
                stage_id = stage["id"]
                marks = []
                if stage_id in self._starts:
                    marks.append(self._tr("adc_legend_start"))
                    marks.extend(self._starts[stage_id])
                if stage_id == self._preview_stage:
                    marks.append(self._tr("adc_legend_preview"))
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
                for column, value in enumerate((label, wave, wait, target)):
                    ttk.Label(self._table, text=value, style=style, wraplength=220).grid(
                        row=row, column=column, sticky="w", padx=(5, 8), pady=3
                    )
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
            self._live_tip.configure(text=self._tr("adc_legend_preview_note"))
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

