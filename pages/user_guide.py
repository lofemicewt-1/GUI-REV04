"""Small in-app user guide with a localized glossary section."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from pages.base_page import fit_toplevel_to_screen


GLOSSARY_ENTRIES: tuple[tuple[str, str], ...] = (
    ("EN", "glossary_en"),
    ("ADC", "glossary_adc"),
    ("SINC", "glossary_sinc"),
    ("SINE / SIN", "glossary_sine_sin"),
    ("ROM", "glossary_rom"),
    ("RAM / SRAM", "glossary_sram"),
    ("VPK", "glossary_vpk"),
    ("Vpp", "glossary_vpp"),
    ("OVV (ADC)", "glossary_ovv_adc"),
    ("Debounce", "glossary_debounce"),
    ("Slice", "glossary_slice"),
    ("Serial", "glossary_serial"),
    ("Stage", "glossary_stage"),
    ("OUT+ / OUT-", "glossary_out_polarity"),
    ("nINT", "glossary_nint"),
    ("EEPROM", "glossary_eeprom"),
    ("Type-4", "glossary_type4"),
    ("GPIO", "glossary_gpio"),
    ("自动串联 / Auto Serial", "glossary_auto_serial"),
)


class UserGuideWindow:
    """A reusable guide window containing the translated glossary."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        translate: Callable[[str], str],
        colors: dict[str, str] | None = None,
    ) -> None:
        self.translate = translate
        self.colors = colors or {}
        self.window = tk.Toplevel(parent)
        self.window.transient(parent.winfo_toplevel())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        fit_toplevel_to_screen(
            self.window,
            preferred_width=700,
            preferred_height=650,
            minimum_width=440,
            minimum_height=360,
        )
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        shell = ttk.Frame(self.window, style="App.TFrame", padding=16)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)

        self.title_label = ttk.Label(shell, style="Title.TLabel")
        self.title_label.grid(row=0, column=0, sticky="w")
        self.intro_label = ttk.Label(shell, style="Muted.TLabel", wraplength=640)
        self.intro_label.grid(row=1, column=0, sticky="w", pady=(4, 10))

        section = ttk.Frame(shell, style="Panel.TFrame", padding=12)
        section.grid(row=2, column=0, sticky="nsew")
        section.columnconfigure(0, weight=1)
        section.rowconfigure(1, weight=1)
        self.section_title = ttk.Label(section, style="ModuleTitle.TLabel")
        self.section_title.grid(row=0, column=0, sticky="w", pady=(0, 8))

        scroll_frame = ttk.Frame(section, style="Panel.TFrame")
        scroll_frame.grid(row=1, column=0, sticky="nsew")
        scroll_frame.columnconfigure(0, weight=1)
        scroll_frame.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            scroll_frame,
            highlightthickness=0,
            background=self.colors.get("panel", "#FFFFFF"),
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(scroll_frame, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.entries_frame = ttk.Frame(self.canvas, style="Panel.TFrame")
        self.entries_frame.columnconfigure(0, minsize=140)
        self.entries_frame.columnconfigure(1, weight=1)
        self._canvas_window = self.canvas.create_window(
            (0, 0), window=self.entries_frame, anchor="nw"
        )
        self.entries_frame.bind("<Configure>", self._update_scroll_region, add="+")
        self.canvas.bind("<Configure>", self._fit_entries_width, add="+")

        self.term_labels: list[ttk.Label] = []
        self.definition_labels: list[ttk.Label] = []
        for row, (term, _key) in enumerate(GLOSSARY_ENTRIES):
            term_label = ttk.Label(self.entries_frame, text=term, style="Panel.TLabel")
            term_label.grid(row=row, column=0, sticky="nw", padx=(0, 14), pady=5)
            definition_label = ttk.Label(
                self.entries_frame,
                style="Panel.TLabel",
                wraplength=480,
                justify="left",
            )
            definition_label.grid(row=row, column=1, sticky="new", pady=5)
            self.term_labels.append(term_label)
            self.definition_labels.append(definition_label)

        self.close_button = ttk.Button(
            shell,
            style="Secondary.TButton",
            command=self.close,
        )
        self.close_button.grid(row=3, column=0, sticky="e", pady=(12, 0))
        self.refresh_language()

    def refresh_language(self) -> None:
        try:
            self.window.title(self.translate("user_guide"))
            self.title_label.configure(text=self.translate("user_guide"))
            self.intro_label.configure(text=self.translate("glossary_intro"))
            self.section_title.configure(text=self.translate("glossary_section_title"))
            self.close_button.configure(text=self.translate("module_help_close"))
            for label, (_term, key) in zip(self.definition_labels, GLOSSARY_ENTRIES):
                label.configure(text=self.translate(key))
        except tk.TclError:
            return

    def _update_scroll_region(self, _event: tk.Event | None = None) -> None:
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            pass

    def _fit_entries_width(self, event: tk.Event) -> None:
        try:
            self.canvas.itemconfigure(self._canvas_window, width=max(1, event.width))
            wrap = max(220, event.width - 170)
            for label in self.definition_labels:
                label.configure(wraplength=wrap)
        except tk.TclError:
            pass

    def close(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:
            pass
