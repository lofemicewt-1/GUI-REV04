from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Any

from config.app_info import APP_VERSION, USER_GUIDE_URL
from config.languages import t


class Navigation:
    """
    \u5de6\u4fa7\u5bfc\u822a\u680f + \u9875\u9762\u5bb9\u5668\u3002

    \u5de6\u4fa7\u53ea\u653e\u9876\u5c42\u76ee\u5f55\uff1a
        HOME
        AWP1921/AWP1923
        AWP1922
        AWP1018
        Setting
    """

    def __init__(self, root: tk.Misc, ctx: Any) -> None:
        self.root = root
        self.ctx = ctx

        self.container = ttk.Frame(root, style="App.TFrame")
        self.container.grid(row=0, column=0, sticky="nsew")
        self.container.columnconfigure(0, minsize=self._scaled(210))
        self.container.columnconfigure(1, weight=1)
        self.container.rowconfigure(0, weight=1)

        self.sidebar = ttk.Frame(self.container, style="Sidebar.TFrame", padding=self._pad(14, 18))
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        self.content_shell = ttk.Frame(self.container, style="App.TFrame")
        self.content_shell.grid(row=0, column=1, sticky="nsew")
        self.content_shell.rowconfigure(0, weight=1)
        self.content_shell.columnconfigure(0, weight=1)

        self.content_canvas = tk.Canvas(self.content_shell, highlightthickness=0, borderwidth=0)
        self.content_canvas.grid(row=0, column=0, sticky="nsew")

        self.v_scrollbar = ttk.Scrollbar(self.content_shell, orient="vertical", command=self.content_canvas.yview)
        self.h_scrollbar = ttk.Scrollbar(self.content_shell, orient="horizontal", command=self.content_canvas.xview)
        self.content_canvas.configure(yscrollcommand=self.v_scrollbar.set, xscrollcommand=self.h_scrollbar.set)

        self.content = ttk.Frame(self.content_canvas, style="App.TFrame", padding=self._pad(18, 18))
        self.content_window = self.content_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        self.content.bind("<Configure>", lambda _event: self._sync_content_window())
        self.content_canvas.bind("<Configure>", lambda _event: self._sync_content_window())
        self.content_canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self._vbar_visible = False
        self._hbar_visible = False
        self._sync_after: str | None = None
        self._last_canvas_state: tuple[int, int, int, int] = (0, 0, 0, 0)

        self.pages: dict[str, ttk.Frame] = {}
        self.buttons: dict[str, ttk.Button] = {}
        self.label_keys: dict[str, str] = {}

        self._title_label = ttk.Label(
            self.sidebar,
            text=t(self.ctx.state.language, "app_title"),
            style="Panel.TLabel",
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        self._title_label.grid(row=0, column=0, sticky="w", pady=(0, 18))

        self._guide_button = ttk.Button(
            self.sidebar,
            text="\u4f7f\u7528\u8bf4\u660e",
            style="Secondary.TButton",
            command=self._open_user_guide,
        )
        self._guide_button.grid(row=99, column=0, sticky="ew", pady=(16, 4))

        self._version_label = ttk.Label(
            self.sidebar,
            style="SidebarVersion.TLabel",
        )
        self._version_label.grid(row=100, column=0, sticky="sw", pady=(4, 0))
        self.sidebar.rowconfigure(98, weight=1)
        self._refresh_version_label()
        self.refresh_layout()

    def _scale(self) -> float:
        return float(getattr(self.ctx.state, "ui_scale", 1.0) or 1.0)

    def _scaled(self, value: int | float) -> int:
        return max(1, int(round(value * self._scale())))

    def _pad(self, x: int, y: int) -> tuple[int, int]:
        return (self._scaled(x), self._scaled(y))

    def add_page(self, name: str, label_key: str, page: ttk.Frame) -> None:
        row = len(self.buttons) + 1
        btn = ttk.Button(
            self.sidebar,
            text=t(self.ctx.state.language, label_key),
            style="Secondary.TButton",
            command=lambda n=name: self.show(n),
        )
        btn.grid(row=row, column=0, sticky="ew", pady=4)

        self.sidebar.columnconfigure(0, weight=1)

        self.buttons[name] = btn
        self.label_keys[name] = label_key
        self.pages[name] = page
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        self._sync_content_window()

    def show(self, name: str) -> None:
        if name not in self.pages:
            return

        current_name = self.ctx.state.current_page
        if current_name != name:
            current_page = self.pages.get(current_name)
            if current_page is not None and hasattr(current_page, "on_hide"):
                current_page.on_hide()

        for page in self.pages.values():
            page.grid_remove()

        page = self.pages[name]
        page.grid()
        self.ctx.state.current_page = name
        self._refresh_button_styles()

        if hasattr(page, "on_show"):
            page.on_show()
        self._sync_content_window()

    def refresh_language(self) -> None:
        lang = self.ctx.state.language
        self._title_label.configure(text=t(lang, "app_title"))
        self._refresh_version_label()

        for name, btn in self.buttons.items():
            key = self.label_keys.get(name, name)
            btn.configure(text=t(lang, key))

        for page in self.pages.values():
            if hasattr(page, "refresh_language"):
                page.refresh_language()

    def refresh_theme(self) -> None:
        self.refresh_layout()
        for page in self.pages.values():
            if hasattr(page, "refresh_theme"):
                page.refresh_theme()

    def refresh_layout(self) -> None:
        self.container.columnconfigure(0, minsize=self._scaled(210))
        self.sidebar.configure(padding=self._pad(14, 18))
        self.content.configure(padding=self._pad(18, 18))
        self.content_canvas.configure(bg=self.ctx.colors["bg"])
        title_size = int(getattr(self.root, "_piezo_sidebar_title_font_size", self._scaled(13)))
        self._title_label.configure(font=("Microsoft YaHei UI", title_size, "bold"))
        self._sync_content_window()

    def _refresh_button_styles(self) -> None:
        current_page = self.ctx.state.current_page
        for name, btn in self.buttons.items():
            btn.configure(style="Selected.TButton" if name == current_page else "Secondary.TButton")

    def _refresh_version_label(self) -> None:
        if self.ctx.state.language == "zh":
            text = f"\u7248\u672c\uff1a{APP_VERSION}"
        else:
            text = f"Rev : {APP_VERSION}"
        self._version_label.configure(text=text)

    def _open_user_guide(self) -> None:
        if USER_GUIDE_URL:
            webbrowser.open(USER_GUIDE_URL)

    def _sync_content_window(self) -> None:
        if self._sync_after is not None:
            return
        try:
            self._sync_after = self.root.after_idle(self._sync_content_window_now)
        except tk.TclError:
            self._sync_after = None

    def _sync_content_window_now(self) -> None:
        self._sync_after = None
        try:
            canvas_width = max(1, self.content_canvas.winfo_width())
            canvas_height = max(1, self.content_canvas.winfo_height())
            required_width = max(1, self.content.winfo_reqwidth())
            required_height = max(1, self.content.winfo_reqheight())
            state = (canvas_width, canvas_height, required_width, required_height)
            if state == self._last_canvas_state:
                return
            self._last_canvas_state = state

            self.content_canvas.itemconfigure(
                self.content_window,
                width=max(canvas_width, required_width),
                height=max(canvas_height, required_height),
            )
            self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))
            self._update_scrollbars(required_width > canvas_width, required_height > canvas_height)
        except tk.TclError:
            pass

    def _update_scrollbars(self, show_h: bool, show_v: bool) -> None:
        if show_v and not self._vbar_visible:
            self.v_scrollbar.grid(row=0, column=1, sticky="ns")
            self._vbar_visible = True
        elif not show_v and self._vbar_visible:
            self.v_scrollbar.grid_remove()
            self._vbar_visible = False

        if show_h and not self._hbar_visible:
            self.h_scrollbar.grid(row=1, column=0, sticky="ew")
            self._hbar_visible = True
        elif not show_h and self._hbar_visible:
            self.h_scrollbar.grid_remove()
            self._hbar_visible = False

    def _on_mousewheel(self, event: tk.Event) -> None:
        try:
            if (
                not self.content_canvas.winfo_ismapped()
                or not self._is_content_widget(event.widget)
                or self._has_nested_scroll_owner(event.widget)
            ):
                return
            delta = -1 if event.delta > 0 else 1
            self.content_canvas.yview_scroll(delta, "units")
        except tk.TclError:
            pass

    def _is_content_widget(self, widget: tk.Misc) -> bool:
        current: tk.Misc | None = widget
        while current is not None:
            if current in (self.content_shell, self.content_canvas, self.content):
                return True
            current = getattr(current, "master", None)
        return False

    def _has_nested_scroll_owner(self, widget: tk.Misc) -> bool:
        current: tk.Misc | None = widget
        while current is not None and current is not self.content:
            if current.__class__.__name__ == "VerticalScrollArea":
                return True
            current = getattr(current, "master", None)
        return False
