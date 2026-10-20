"""Small presentation helpers shared by chip Button Mode pages.

This module contains no chip protocol or page-specific behavior.  A page owns
the cards and decides what each control means; this module only lays cards out
to fit the width made available by the current viewport.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def responsive_column_count(
    available_width: int,
    card_width: int,
    item_count: int,
    max_columns: int = 4,
) -> int:
    """Return the 4/2/1-style column count used by channel cards."""
    if item_count <= 0:
        return 1
    card_width = max(1, int(card_width))
    available_width = max(1, int(available_width))
    max_columns = max(1, min(int(max_columns), item_count))
    fit_count = available_width // card_width
    if fit_count >= 4 and max_columns >= 4:
        return 4
    if fit_count >= 2 and max_columns >= 2:
        return 2
    return 1


class ResponsiveCardGrid(ttk.Frame):
    """Grid cards in four, two, or one columns as viewport width changes."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        item_count: int,
        max_columns: int = 4,
        min_card_width: int = 132,
        gap: int = 8,
        **kwargs,
    ) -> None:
        kwargs.setdefault("style", "Panel.TFrame")
        super().__init__(parent, **kwargs)
        self.item_count = max(0, int(item_count))
        self.max_columns = max(1, min(int(max_columns), max(1, self.item_count)))
        self.min_card_width = max(1, int(min_card_width))
        self.gap = max(0, int(gap))
        self.cards: list[tk.Widget] = []
        self.columns = 1
        self.bind("<Configure>", self._on_configure, add="+")

    def add_card(self, card: tk.Widget) -> None:
        self.cards.append(card)
        self._layout_cards()

    def _on_configure(self, event: tk.Event) -> None:
        self._layout_cards(int(getattr(event, "width", 0)))

    def _layout_cards(self, available_width: int | None = None) -> None:
        if not self.cards:
            return
        if available_width is None:
            try:
                available_width = int(self.winfo_width())
            except tk.TclError:
                available_width = 0
        requested_width = self.min_card_width
        for card in self.cards:
            try:
                requested_width = max(requested_width, int(card.winfo_reqwidth()))
            except tk.TclError:
                pass
        column_width = requested_width + self.gap
        next_columns = responsive_column_count(
            max(1, available_width),
            column_width,
            len(self.cards),
            self.max_columns,
        )
        if next_columns == self.columns and all(card.winfo_manager() == "grid" for card in self.cards):
            return
        self.columns = next_columns
        for index, card in enumerate(self.cards):
            card.grid(
                row=index // next_columns,
                column=index % next_columns,
                sticky="ew",
                padx=(0 if index % next_columns == 0 else self.gap, 0),
                pady=(0, self.gap),
            )
        for column in range(self.max_columns):
            self.columnconfigure(column, weight=1 if column < next_columns else 0)


__all__ = ["ResponsiveCardGrid", "responsive_column_count"]
