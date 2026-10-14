from __future__ import annotations

import math
import tkinter as tk
import webbrowser
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any

from config.app_info import APP_VERSION, USER_GUIDE_URL
from config.languages import t
from styles.fonts import sidebar_brand_font_spec
from styles.theme import BRAND_ENLARGE, logo_asset, scale_photoimage


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
        # Reserve room for the sidebar scrollbar while retaining the button
        # labels' natural width.  The canvas and scrollbar share this column.
        self.container.columnconfigure(0, minsize=self._scaled(round(224 * BRAND_ENLARGE)))
        self.container.columnconfigure(1, weight=1)
        self.container.rowconfigure(0, weight=1)

        # Keep the navigation controls reachable when the window is shorter
        # than the expanded mode list.  The old sidebar was a plain frame;
        # rows after the viewport (especially the guide/version footer) were
        # then painted outside the client area with no way to reach them.
        self.sidebar_shell = ttk.Frame(self.container, style="Sidebar.TFrame")
        self.sidebar_shell.grid(row=0, column=0, sticky="nsew")
        self.sidebar_shell.rowconfigure(0, weight=1)
        self.sidebar_shell.columnconfigure(0, weight=1)
        self.sidebar_canvas = tk.Canvas(
            self.sidebar_shell,
            width=1,
            height=1,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        self.sidebar_canvas.grid(row=0, column=0, sticky="nsew")
        self.sidebar_shell.bind("<Map>", self._on_sidebar_shell_map, add="+")
        self.sidebar_scrollbar = ttk.Scrollbar(
            self.sidebar_shell,
            orient="vertical",
            command=self.sidebar_canvas.yview,
        )
        self.sidebar_canvas.configure(yscrollcommand=self.sidebar_scrollbar.set)
        self.sidebar = ttk.Frame(
            self.sidebar_canvas,
            style="Sidebar.TFrame",
            padding=self._pad(10, 8),
        )
        self.sidebar_window = self.sidebar_canvas.create_window(
            (0, 0),
            window=self.sidebar,
            anchor="nw",
        )
        self.sidebar.bind("<Configure>", self._sync_sidebar_scroll_region)
        self.sidebar_canvas.bind("<Configure>", self._sync_sidebar_content_width)
        self.sidebar_canvas.bind_all("<MouseWheel>", self._on_sidebar_mousewheel, add="+")
        self._sidebar_scrollbar_visible = False

        self.content_shell = ttk.Frame(self.container, style="App.TFrame")
        self.content_shell.grid(row=0, column=1, sticky="nsew")
        self.content_shell.rowconfigure(0, weight=1)
        self.content_shell.columnconfigure(0, weight=1)

        self.content_canvas = tk.Canvas(self.content_shell, highlightthickness=0, borderwidth=0)
        self.content_canvas.grid(row=0, column=0, sticky="nsew")

        self.v_scrollbar = ttk.Scrollbar(self.content_shell, orient="vertical", command=self.content_canvas.yview)
        self.h_scrollbar = ttk.Scrollbar(self.content_shell, orient="horizontal", command=self.content_canvas.xview)
        self.content_canvas.configure(yscrollcommand=self.v_scrollbar.set, xscrollcommand=self.h_scrollbar.set)

        self.content = ttk.Frame(self.content_canvas, style="App.TFrame", padding=self._pad(16, 8))
        self.content_window = self.content_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        self.content.bind("<Configure>", lambda _event: self._sync_content_window())
        self.content_canvas.bind("<Configure>", lambda _event: self._sync_content_window())
        self.content_canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.content_canvas.bind_all("<Shift-MouseWheel>", self._on_shift_mousewheel, add="+")
        self._vbar_visible = False
        self._hbar_visible = False
        self._sync_after: str | None = None
        self._last_canvas_state: tuple[Any, ...] = (0, 0, 0, 0)
        self._initial_map_layout_synced = False
        # The first idle geometry pass can run before the Tk toplevel is
        # mapped.  In that state the root grid gives this container 1x1 and
        # the sidebar canvas remains unmapped until a later resize.  A single
        # Map-boundary sync makes the initial layout deterministic without a
        # polling timer or a synthetic resize.
        # Bind Map/Unmap on a tag owned only by the root.  Binding them on the
        # toplevel itself would also fire for every descendant widget (Tk
        # toplevel bindtags), costing hundreds of Python callbacks per page
        # switch even though the handlers ignore non-root events.
        root_tag = f"NavRootMap{id(self)}"
        self.root.bindtags((root_tag,) + tuple(self.root.bindtags()))
        self.root.bind_class(root_tag, "<Map>", self._on_root_map, add="+")
        self.root.bind_class(root_tag, "<Unmap>", self._on_root_unmap, add="+")

        self.pages: dict[str, ttk.Frame] = {}
        self.buttons: dict[str, ttk.Button] = {}
        self.label_keys: dict[str, str] = {}
        self.mode_host: Any | None = None
        self.mode_buttons: dict[str, ttk.Button] = {}

        # width=1：画布自身不撑宽侧栏，侧栏宽度由导航按钮自然宽度决定，
        # 避免默认 Canvas 请求宽度（约378px）把按钮列撑得过大。
        self._title_canvas = tk.Canvas(
            self.sidebar,
            width=1,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        self._title_canvas.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._title_canvas.bind("<Configure>", self._redraw_sidebar_brand)
        self._brand_state: tuple[Any, ...] | None = None

        self.mode_frame = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        self.mode_frame.columnconfigure(0, weight=1)

        self._guide_button = ttk.Button(
            self.sidebar,
            text=t(self.ctx.state.language, "user_guide"),
            style="Sidebar.TButton",
            command=self._open_user_guide,
        )
        self._guide_button.grid(row=99, column=0, sticky="ew", pady=(16, 4))

        self._version_label = ttk.Label(
            self.sidebar,
            style="SidebarVersion.TLabel",
        )
        self._version_label.grid(row=100, column=0, sticky="sw", pady=(4, 0))
        self.sidebar.rowconfigure(98, weight=1)
        self._settings_bar = ttk.Frame(self.sidebar_shell, style="Sidebar.TFrame", padding=self._pad(10, 8))
        self._settings_bar.grid(row=1, column=0, sticky="ew")
        self._settings_bar.columnconfigure(0, weight=1)
        self._settings_images: dict[bool, tk.PhotoImage] = {}
        self._settings_button = ttk.Button(
            self._settings_bar,
            style="Sidebar.TButton",
            command=lambda: self.show("settings"),
        )
        self._settings_button.grid(row=0, column=0, sticky="ew")
        self._refresh_settings_button()
        self._refresh_version_label()
        self.refresh_layout()

    def _scale(self) -> float:
        return float(getattr(self.ctx.state, "ui_scale", 1.0) or 1.0)

    def _on_root_map(self, event: tk.Event | None = None) -> None:
        if event is not None and getattr(event, "widget", None) is not self.root:
            return
        if self._initial_map_layout_synced:
            return
        self._initial_map_layout_synced = True
        try:
            self.root.update_idletasks()
            # The inner canvas can receive its initial Configure while the
            # shell is still being mapped.  Reassert its existing grid slot
            # once the root is mapped so Tk performs the child Map pass too.
            self.sidebar_canvas.grid()
            self.root.update_idletasks()
        except tk.TclError:
            return
        self._sync_content_window()
        self._sync_sidebar_scroll_region()

    def _on_root_unmap(self, event: tk.Event | None = None) -> None:
        if event is None or getattr(event, "widget", None) is self.root:
            # A withdrawn toplevel unmaps the complete child hierarchy.  The
            # next Map is a new layout boundary and must run the same one-shot
            # geometry repair as the initial startup.
            self._initial_map_layout_synced = False

    def _on_sidebar_shell_map(self, event: tk.Event | None = None) -> None:
        if event is not None and getattr(event, "widget", None) is not self.sidebar_shell:
            return
        try:
            # A shell can be remapped before Tk replays the Canvas child Map
            # event.  Reasserting the existing slot at that boundary keeps
            # the sidebar paintable on a withdraw/deiconify cycle as well.
            self.sidebar_canvas.grid()
            self.root.update_idletasks()
        except tk.TclError:
            pass

    def _scaled(self, value: int | float) -> int:
        return max(1, int(round(value * self._scale())))

    def _pad(self, x: int, y: int) -> tuple[int, int]:
        return (self._scaled(x), self._scaled(y))

    def add_page(self, name: str, label_key: str, page: ttk.Frame, *, sidebar_button: bool = True) -> None:
        if sidebar_button:
            row = len(self.buttons) + 1
            btn = ttk.Button(
                self.sidebar,
                text=t(self.ctx.state.language, label_key),
                style="Sidebar.TButton",
                command=lambda n=name: self.show(n),
            )
            btn.grid(row=row, column=0, sticky="ew", pady=(0, 3) if row == 1 else 3)
            self.buttons[name] = btn
            self.label_keys[name] = label_key

        self.sidebar.columnconfigure(0, weight=1)
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
        self._sync_mode_buttons(page)
        self._sync_content_window()

    def refresh_language(self) -> None:
        lang = self.ctx.state.language
        self._redraw_sidebar_brand()
        self._refresh_version_label()
        self._guide_button.configure(text=t(lang, "user_guide"))

        for name, btn in self.buttons.items():
            key = self.label_keys.get(name, name)
            btn.configure(text=t(lang, key))
        for key, btn in self.mode_buttons.items():
            btn.configure(text=t(lang, key))

        for page in self.pages.values():
            if hasattr(page, "refresh_language"):
                page.refresh_language()

    def refresh_theme(self) -> None:
        self.refresh_layout()
        self._settings_images.clear()
        self._refresh_settings_button()
        for page in self.pages.values():
            if hasattr(page, "refresh_theme"):
                page.refresh_theme()

    def refresh_layout(self) -> None:
        self.container.columnconfigure(0, minsize=self._scaled(round(224 * BRAND_ENLARGE)))
        self.sidebar.configure(padding=self._pad(10, 8))
        if getattr(self, "_settings_bar", None) is not None:
            self._settings_bar.configure(padding=self._pad(10, 8))
        self.sidebar_canvas.configure(bg=self.ctx.colors["panel_alt"])
        self.content.configure(padding=self._pad(16, 8))
        self._title_canvas.grid_configure(pady=(0, self._scaled(8)))
        self.content_canvas.configure(bg=self.ctx.colors["bg"])
        self._relayout_sidebar()
        self._brand_state = None
        self._redraw_sidebar_brand()
        for page in self.pages.values():
            sync = getattr(page, "_sync_header_chrome", None)
            if callable(sync):
                sync()
            logos = getattr(page, "_refresh_header_logos", None)
            if callable(logos):
                logos()
        self._sync_content_window()
        self._sync_sidebar_scroll_region()
        try:
            self.root.after_idle(self._align_home_panel)
        except tk.TclError:
            pass

    def _fit_brand_font(self, family: str, weight: str, max_width: int) -> tkfont.Font:
        # 从大往小收，直到最长一行（CONTROL）恰好填满可用宽度，
        # 使品牌文字块与下方导航按钮列同宽。
        size = max(12, self._scaled(64))
        font = tkfont.Font(root=self.root, family=family, size=size, weight=weight)
        while size > 9 and font.measure("CONTROL") > max_width:
            size -= 1
            font.configure(size=size)
        size = max(9, int(round(size * 0.82)))
        font.configure(size=size)
        return font

    def _sidebar_logo(self, theme: str, max_width: int) -> tk.PhotoImage | None:
        path = logo_asset(theme)
        if path is None:
            return None
        cache = getattr(self, "_sidebar_logo_cache", {})
        key = (str(path), max_width, BRAND_ENLARGE)
        image = cache.get(key)
        if image is None:
            image = tk.PhotoImage(file=str(path), master=self.root)
            image = scale_photoimage(image, max_width=max_width, enlarge=BRAND_ENLARGE)
            cache[key] = image
            self._sidebar_logo_cache = cache
        return image

    def _refresh_header_wordmarks(self) -> None:
        for page in self.pages.values():
            refresh = getattr(page, "_refresh_header_logos", None)
            if callable(refresh):
                refresh()

    def _redraw_sidebar_brand(self, _event: tk.Event | None = None) -> None:
        canvas = getattr(self, "_title_canvas", None)
        if canvas is None:
            return
        bg = self.ctx.colors["panel_alt"]
        theme = str(getattr(self.ctx.state, "theme", "light"))
        try:
            allocated = int(canvas.winfo_width())
        except tk.TclError:
            allocated = 0
        max_width = allocated if allocated > 10 else self._scaled(round(184 * BRAND_ENLARGE))
        image = self._sidebar_logo(theme, max_width)
        chrome_h = max(int(image.height()) if image is not None else 0, self._scaled(36))
        setattr(self.root, "_piezo_top_chrome_height", chrome_h)
        state = (max_width, chrome_h, bg, theme, None if image is None else (int(image.width()), int(image.height())))
        if state == self._brand_state:
            return
        self._brand_state = state
        self._brand_logo = image
        canvas.configure(bg=bg, height=chrome_h)
        canvas.delete("all")
        if image is None:
            return
        canvas.create_image(max_width / 2, chrome_h / 2, image=image, anchor="center")
        self._refresh_header_wordmarks()

    def _align_home_panel(self) -> None:
        if getattr(self, "_aligning_home", False):
            return
        home = self.pages.get("home")
        btn = self.buttons.get("home")
        if home is None or btn is None:
            return
        panel = getattr(home, "main_panel", None)
        band = getattr(home, "_header_band", None)
        if panel is None or band is None:
            return
        self._aligning_home = True
        try:
            # Called after a geometry event/idle pass; do not start a nested
            # idle loop while the user is resizing the window.
            delta = int(panel.winfo_rooty()) - int(btn.winfo_rooty())
            if abs(delta) <= 1:
                return
            pady = band.grid_info().get("pady", (0, 14))
            if isinstance(pady, tuple):
                top_p, bot_p = int(pady[0]), int(pady[1])
            else:
                top_p, bot_p = 0, int(pady)
            if delta > 0:
                reduce = min(delta, bot_p)
                band.grid_configure(pady=(top_p, bot_p - reduce))
            else:
                band.grid_configure(pady=(top_p, bot_p - delta))
        except tk.TclError:
            pass
        finally:
            self._aligning_home = False

    def _draw_justified_line(
        self,
        canvas: tk.Canvas,
        text: str,
        font: tkfont.Font,
        x0: int,
        y: int,
        width: int,
        fill: str,
    ) -> None:
        chars = list(text)
        if not chars:
            return
        widths = [font.measure(char) for char in chars]
        extra = max(0, width - sum(widths))
        gap = extra / (len(chars) - 1) if len(chars) > 1 else 0
        x = float(x0)
        for index, char in enumerate(chars):
            canvas.create_text(x, y, text=char, font=font, fill=fill, anchor="nw")
            x += widths[index]
            if index < len(chars) - 1:
                x += gap

    def _refresh_button_styles(self) -> None:
        current_page = self.ctx.state.current_page
        for name, btn in self.buttons.items():
            btn.configure(style="SidebarSelected.TButton" if name == current_page else "Sidebar.TButton")
        self._refresh_settings_button()

    def _mode_keys_for(self, page: Any) -> list[str]:
        mode_pages = getattr(page, "mode_pages", None)
        if not isinstance(mode_pages, dict) or not mode_pages:
            return []
        return list(mode_pages.keys())

    def _clear_mode_buttons(self) -> None:
        host = self.mode_host
        for key, btn in self.mode_buttons.items():
            if host is not None:
                current = getattr(host, "mode_buttons", None)
                if isinstance(current, dict) and current.get(key) is btn:
                    current.pop(key, None)
            btn.destroy()
        self.mode_buttons = {}
        self.mode_host = None
        self.mode_frame.grid_remove()
        self._relayout_sidebar()

    def _relayout_sidebar(self) -> None:
        current = self.ctx.state.current_page
        row = 1
        for index, (name, btn) in enumerate(self.buttons.items()):
            pady = (0, 3) if index == 0 else 3
            btn.grid(row=row, column=0, sticky="ew", pady=pady)
            row += 1
            if name == current and self.mode_buttons:
                self.mode_frame.grid(row=row, column=0, sticky="ew", pady=(0, 3))
                row += 1
        if not self.mode_buttons:
            self.mode_frame.grid_remove()
        self._sync_sidebar_scroll_region()

    def _sync_sidebar_content_width(self, event: tk.Event) -> None:
        try:
            self.sidebar_canvas.itemconfigure(
                self.sidebar_window,
                width=max(1, int(event.width)),
            )
            self._sync_sidebar_scroll_region()
        except tk.TclError:
            pass

    def _sync_sidebar_scroll_region(self, _event: tk.Event | None = None) -> None:
        try:
            canvas_height = max(1, self.sidebar_canvas.winfo_height())
            content_height = max(1, self.sidebar.winfo_reqheight(), self.sidebar.winfo_height())
            content_width = max(1, self.sidebar_canvas.winfo_width())
            self.sidebar_canvas.configure(
                scrollregion=(0, 0, content_width, content_height)
            )
            needs_scrollbar = content_height > canvas_height
            if needs_scrollbar and not self._sidebar_scrollbar_visible:
                self.sidebar_scrollbar.grid(row=0, column=1, sticky="ns")
                self._sidebar_scrollbar_visible = True
            elif not needs_scrollbar and self._sidebar_scrollbar_visible:
                self.sidebar_scrollbar.grid_remove()
                self._sidebar_scrollbar_visible = False
        except tk.TclError:
            pass

    def _on_sidebar_mousewheel(self, event: tk.Event) -> None:
        try:
            widget = self.root.winfo_containing(int(event.x_root), int(event.y_root))
            current: tk.Misc | None = widget
            inside = False
            while current is not None:
                if current is self.sidebar:
                    inside = True
                    break
                current = getattr(current, "master", None)
            if not inside or not self.sidebar_canvas.winfo_ismapped():
                return
            delta = -1 if event.delta > 0 else 1
            self.sidebar_canvas.yview_scroll(delta, "units")
        except (tk.TclError, AttributeError, TypeError, ValueError):
            pass

    def _sync_mode_buttons(self, page: Any) -> None:
        keys = self._mode_keys_for(page)
        if not keys:
            self._clear_mode_buttons()
            return
        if self.mode_host is not page or list(self.mode_buttons.keys()) != keys:
            self._clear_mode_buttons()
            self.mode_host = page
            host_buttons = getattr(page, "mode_buttons", None)
            if not isinstance(host_buttons, dict):
                host_buttons = {}
                page.mode_buttons = host_buttons
            lang = self.ctx.state.language
            for index, key in enumerate(keys):
                btn = ttk.Button(
                    self.mode_frame,
                    text=t(lang, key),
                    style="SidebarMode.TButton",
                    command=lambda k=key: self._on_mode_clicked(k),
                )
                btn.grid(row=index, column=0, sticky="ew", pady=(0, 3) if index == 0 else 3)
                self.mode_buttons[key] = btn
                host_buttons[key] = btn
        self._relayout_sidebar()
        sync = getattr(page, "_sync_mode_button_styles", None)
        if callable(sync):
            sync()
        else:
            self._refresh_mode_button_styles()

    def _on_mode_clicked(self, key: str) -> None:
        page = self.mode_host
        if page is None or not hasattr(page, "show_mode"):
            return
        page.show_mode(key)
        sync = getattr(page, "_sync_mode_button_styles", None)
        if callable(sync):
            sync()
        else:
            self._refresh_mode_button_styles()

    def _refresh_mode_button_styles(self) -> None:
        current = getattr(self.mode_host, "current_mode", None)
        for key, btn in self.mode_buttons.items():
            btn.configure(style="SidebarModeSelected.TButton" if key == current else "SidebarMode.TButton")

    def _refresh_version_label(self) -> None:
        if self.ctx.state.language == "zh":
            text = f"\u7248\u672c\uff1a{APP_VERSION}"
        else:
            text = f"Rev : {APP_VERSION}"
        self._version_label.configure(text=text)

    def _gear_image(self, selected: bool) -> tk.PhotoImage:
        theme = str(getattr(self.ctx.state, "theme", "light"))
        color = "#05070D" if selected and theme == "dark" else "#FFFFFF" if selected else self.ctx.colors["text"]
        size = max(24, self._scaled(30))
        cached = self._settings_images.get(selected)
        key = (selected, color, theme, size)
        if cached is not None and getattr(cached, "_gear_key", None) == key:
            return cached
        image = tk.PhotoImage(master=self.root, width=size, height=size)
        # Eight broad teeth and an open hub remain legible at sidebar size.
        for y in range(size):
            for x in range(size):
                dx = (x + 0.5 - size / 2) / size
                dy = (y + 0.5 - size / 2) / size
                radius = math.hypot(dx, dy)
                angle = math.atan2(dy, dx)
                tooth = abs((angle + math.pi / 8) % (math.pi / 4) - math.pi / 8)
                outer = 0.47 if tooth < math.pi / 15 else 0.36
                if 0.16 <= radius <= outer:
                    image.put(color, to=(x, y, x + 1, y + 1))
        image._gear_key = key
        self._settings_images[selected] = image
        return image

    def _refresh_settings_button(self) -> None:
        button = getattr(self, "_settings_button", None)
        if button is None:
            return
        selected = self.ctx.state.current_page == "settings"
        image = self._gear_image(selected)
        button.configure(
            style="SidebarSelected.TButton" if selected else "Sidebar.TButton",
            image=image,
        )
        button.image = image

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
            current_name = getattr(self.ctx.state, "current_page", "")
            page = self.pages.get(current_name)
            pin = bool(getattr(page, "pin_window_footer", False)) if page is not None else False
            window_width = canvas_width if pin else max(canvas_width, required_width)
            window_height = canvas_height if pin else max(canvas_height, required_height)
            state = (canvas_width, canvas_height, required_width, required_height, current_name, pin)
            if state == self._last_canvas_state:
                return
            self._last_canvas_state = state

            self.content_canvas.itemconfigure(
                self.content_window,
                width=window_width,
                height=window_height,
            )
            self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))
            self._update_scrollbars((not pin) and required_width > canvas_width, (not pin) and required_height > canvas_height)
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

    def _pointer_widget(self, event: tk.Event) -> tk.Misc:
        try:
            widget = self.root.winfo_containing(int(event.x_root), int(event.y_root))
            if widget is not None:
                return widget
        except (tk.TclError, AttributeError, TypeError, ValueError):
            pass
        return event.widget

    def _on_mousewheel(self, event: tk.Event) -> None:
        try:
            widget = self._pointer_widget(event)
            if (
                not self.content_canvas.winfo_ismapped()
                or not self._is_content_widget(widget)
                or self._has_nested_scroll_owner(widget)
            ):
                return
            delta = -1 if event.delta > 0 else 1
            self.content_canvas.yview_scroll(delta, "units")
        except tk.TclError:
            pass

    def _on_shift_mousewheel(self, event: tk.Event) -> None:
        try:
            widget = self._pointer_widget(event)
            if (
                not self.content_canvas.winfo_ismapped()
                or not self._is_content_widget(widget)
                or self._has_nested_scroll_owner(widget)
            ):
                return
            delta = -1 if event.delta > 0 else 1
            self.content_canvas.xview_scroll(delta, "units")
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
