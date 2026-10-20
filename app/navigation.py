from __future__ import annotations

import math
import time
from datetime import datetime
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any, Callable

from core import animation as anim
from core.lazy_pages import LazyPages
from app.sidebar_motion import SidebarMotion

from config.app_info import APP_VERSION
from config.languages import t
from styles.fonts import sidebar_brand_font_spec
from pages.base_page import hide_scroll_chrome, relay_vertical_scroll, scaled_scroll_px
from pages.user_guide import UserGuideWindow
from styles.theme import BRAND_ENLARGE, logo_asset, photoimage_scale_token, scale_photoimage
from core.status_bus import StatusMessage, StatusUpdate


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
        self._user_guide_window: UserGuideWindow | None = None

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
        self._sidebar_yview_key: tuple[str, str] | None = None
        self._content_yview_key: tuple[str, str] | None = None
        self._content_xview_key: tuple[str, str] | None = None
        self.sidebar_canvas = tk.Canvas(
            self.sidebar_shell,
            width=1,
            height=1,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            yscrollincrement=1,
        )
        self.sidebar_canvas.grid(row=0, column=0, sticky="nsew")
        self.sidebar_shell.bind("<Map>", self._on_sidebar_shell_map, add="+")
        self.sidebar_scrollbar = ttk.Scrollbar(
            self.sidebar_shell,
            orient="vertical",
            command=self.sidebar_canvas.yview,
        )
        self.sidebar_canvas.configure(yscrollcommand=self._on_sidebar_yview)
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

        self.content_canvas = tk.Canvas(
            self.content_shell,
            highlightthickness=0,
            borderwidth=0,
            yscrollincrement=1,
        )
        self.content_canvas.grid(row=0, column=0, sticky="nsew")

        self.v_scrollbar = ttk.Scrollbar(self.content_shell, orient="vertical", command=self.content_canvas.yview)
        self.h_scrollbar = ttk.Scrollbar(self.content_shell, orient="horizontal", command=self.content_canvas.xview)
        self.content_canvas.configure(yscrollcommand=self._on_content_yview, xscrollcommand=self._on_content_xview)

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
        self._owner_tag = f"NavigationOwner{id(self)}"
        self.root.bindtags((self._owner_tag, *self.root.bindtags()))
        self.root.bind_class(self._owner_tag, "<Map>", self._on_root_map)
        self.root.bind_class(self._owner_tag, "<Unmap>", self._on_root_unmap)

        self.pages = LazyPages(self._install_page)
        self.buttons: dict[str, ttk.Button] = {}
        self.label_keys: dict[str, str] = {}
        self._chip_group_by_page = {
            "awp1221": "awp1221",
            "awp1921_1923": "awp1921_1923",
            "awp1018": "awp1018",
            "awp1922": "awp1922",
        }
        self._chip_page_by_device = {
            "AWP1221": "awp1221",
            "AWP192X": "awp1921_1923",
            "AWP1018": "awp1018",
            "AWP1922": "awp1922",
        }
        self._visibility = {
            "phase": "offline",
            "visible": False,
            "highlight": None,
            "keep_group": None,
            "disabled": False,
        }
        self._developer_visibility_override = False
        self._connection_notice_key: str | None = None
        self.mode_host: Any | None = None
        self.mode_buttons: dict[str, ttk.Button] = {}
        self._pending_page: str | None = None
        self._preparation = None
        self._prepare_commit = None
        self._pending_mode: str | None = None
        self._transition_pending = False
        self._pending_navigation: str | None = None
        self._pending_navigation_fallback: str | None = None

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
        self._suspend_brand_configure = False

        self.mode_clip = ttk.Frame(self.sidebar, style="Sidebar.TFrame", height=1)
        self.mode_clip.grid_propagate(False)
        self.mode_frame = ttk.Frame(self.mode_clip, style="Sidebar.TFrame")
        self.mode_frame.place(x=0, y=0, relwidth=1)
        self.mode_frame.columnconfigure(0, weight=1)
        self.mode_frame.bind("<Configure>", self._on_mode_frame_configure, add="+")
        self._mode_height = 0.0
        self._mode_anchor = ""
        self._mode_visible = False
        self._retired_mode_buttons: list[ttk.Button] = []
        self.motion = SidebarMotion(self)

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
        self._status_frame = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        self._status_frame.grid(row=101, column=0, sticky="ew", pady=(2, 0))
        self._status_frame.columnconfigure(1, weight=1)
        self._status_dot = tk.Label(self._status_frame, text="●", bd=0, padx=0, pady=0)
        self._status_dot.grid(row=0, column=0, sticky="w", padx=(0, 5))
        self._status_text = ttk.Label(self._status_frame, style="SidebarVersion.TLabel", anchor="w")
        self._status_text.grid(row=0, column=1, sticky="ew")
        self._status_detail: Any = None
        self._status_error = ""
        self._status_state = "ready"
        self._status_timestamp: float | None = None
        self._status_display_sequence = 0
        self._status_expire_after: str | None = None
        self._status_unsubscribe: Callable[[], None] | None = None
        self._status_tooltip = None
        try:
            from pages.base_page import SimpleTooltip
            self._status_tooltip = SimpleTooltip(self._status_frame, self._status_tooltip_text)
            self._status_tooltip_text_widget = SimpleTooltip(self._status_text, self._status_tooltip_text)
        except Exception:
            self._status_tooltip_text_widget = None
        self._connection_notice_frame = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        self._connection_notice_frame.grid(row=102, column=0, sticky="ew", pady=(2, 0))
        self._connection_notice_label = ttk.Label(
            self._connection_notice_frame, style="SidebarVersion.TLabel", wraplength=self._scaled(196),
        )
        self._connection_notice_label.grid(row=0, column=0, sticky="ew")
        self._connection_home_button = ttk.Button(
            self._connection_notice_frame, style="SidebarMode.TButton", command=lambda: self.show("home"),
        )
        self._connection_home_button.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self._connection_notice_frame.grid_remove()
        bus = getattr(self.ctx, "status_bus", None)
        if bus is not None:
            self._status_unsubscribe = bus.subscribe(self._on_status_update)
        self.sidebar.rowconfigure(98, weight=1)
        self._settings_bar = ttk.Frame(self.sidebar_shell, style="Sidebar.TFrame", padding=self._pad(10, 8))
        self._settings_bar.grid(row=1, column=0, sticky="ew")
        self._settings_bar.columnconfigure(0, weight=1)
        self._settings_images: dict[bool, tk.PhotoImage] = {}
        self._settings_button = ttk.Button(
            self._settings_bar,
            style="Sidebar.TButton",
            command=lambda: self.request_show("settings"),
        )
        self._settings_button.grid(row=0, column=0, sticky="ew")
        self._refresh_settings_button()
        self._refresh_version_label()
        self._render_status()
        self._render_connection_notice()
        self._escape_binding = root.bind('<Escape>', self._cancel_preparation, add='+')
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
        self.register_page(name, label_key, lambda: page, sidebar_button=sidebar_button)
        self.pages[name]

    def _install_page(self, name: str, page: ttk.Frame) -> None:
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        refresh = getattr(self.ctx.app, "_refresh_widget_fonts", None)
        if callable(refresh):
            refresh(page)
        self._sync_content_window()

    def register_page(self, name: str, label_key: str, factory: Callable[..., ttk.Frame], *, sidebar_button: bool = True, staged: bool = False) -> None:
        self.pages.register(name, factory, staged=staged)
        if sidebar_button:
            row = len(self.buttons) + 1
            btn = ttk.Button(
                self.sidebar,
                text=t(self.ctx.state.language, label_key),
                style="Sidebar.TButton",
                command=lambda n=name: self.request_show(n),
            )
            self.buttons[name] = btn
            self.label_keys[name] = label_key
            self.motion.bind(btn)
            btn.grid(row=row, column=0, sticky="ew", pady=(0, 3) if row == 1 else 3)
            self._configure_chip_button(name)

        self.sidebar.columnconfigure(0, weight=1)
        self._relayout_sidebar()

    def show(self, name: str) -> None:
        """Immediate navigation for application/configuration callers."""
        if name not in self.pages:
            return
        self._finish_waiting_transition()
        self.cancel_pending_navigation()
        self._transition_to(name)

    def request_show(self, name: str) -> None:
        """Acknowledge clicks before construction; keep only the newest request."""
        if name not in self.pages or name == self._pending_page:
            return
        if not self._page_navigation_enabled(name):
            return
        self._finish_waiting_transition()
        self.cancel_pending_navigation()
        if name == self.ctx.state.current_page:
            return
        self._pending_page = name
        self._refresh_button_styles()
        self.motion.refresh(animate=True)
        self._pending_navigation = self.root.after(0, self._commit_navigation)

    def _commit_navigation(self) -> None:
        # Existing sidebar widgets have already queued their redraw. Let
        # that idle batch paint the selection before a cold constructor can
        # occupy the main thread. New input can still cancel this idle job.
        self._pending_navigation = self.root.after_idle(self._build_pending_navigation)
        # A continuous stream of timers must not starve an idle-only request.
        self._pending_navigation_fallback = self.root.after(anim.DUR_FAST, self._build_pending_navigation)

    def _build_pending_navigation(self) -> None:
        self._cancel_navigation_jobs()
        name = self._pending_page
        if name is not None and self.pages.can_prepare(name):
            self._start_preparation(self.pages, name, lambda: self._transition_to(name, defer_activation=True))
            return
        try:
            if name is not None:
                self._transition_to(name, defer_activation=True)
        finally:
            self._pending_page = None
            self._refresh_button_styles()
            self.motion.refresh()

    def _start_preparation(self, pages: LazyPages, name: str, commit: Callable[[], None]) -> None:
        transition = getattr(self.ctx.app, 'transitions', None)
        if transition is not None:
            transition.prime()
        self._preparation = pages.prepare(name)
        self._prepare_commit = commit
        self._last_preparation_steps_ms = []
        self._refresh_version_label()
        self._run_preparation()

    def _run_preparation(self) -> None:
        self._pending_navigation = None
        job = self._preparation
        if job is None:
            return
        started = time.perf_counter()
        try:
            # A factory opts in only when its individual builders can yield.
            # All Tk work remains on this thread; input runs between batches.
            while True:
                step_start = time.perf_counter()
                next(job)
                self._last_preparation_steps_ms.append((time.perf_counter()-step_start)*1000)
                if time.perf_counter()-started >= .006:
                    break
        except StopIteration:
            self._preparation = None
            commit, self._prepare_commit = self._prepare_commit, None
            try:
                if commit is not None:
                    commit()
            finally:
                self.cancel_pending_navigation()
            return
        except BaseException:
            self.cancel_pending_navigation()
            raise
        self._pending_navigation = self.root.after(1, self._run_preparation)

    def _cancel_navigation_jobs(self) -> None:
        if self._pending_navigation is not None:
            self.root.after_cancel(self._pending_navigation)
            self._pending_navigation = None
        if self._pending_navigation_fallback is not None:
            self.root.after_cancel(self._pending_navigation_fallback)
            self._pending_navigation_fallback = None

    def cancel_pending_navigation(self) -> None:
        self._cancel_navigation_jobs()
        job, self._preparation = self._preparation, None
        self._prepare_commit = None
        self._pending_mode = None
        if job is not None:
            job.close()
        if self._pending_page is not None:
            self._pending_page = None
            self._refresh_button_styles()
            self.motion.refresh()
        self._refresh_mode_button_styles()
        self._refresh_version_label()

    def _finish_waiting_transition(self) -> None:
        if self._transition_pending:
            self.ctx.app.transitions.finish()

    def _cancel_preparation(self, _event=None) -> None:
        self.cancel_pending_navigation()
        self._finish_waiting_transition()

    def close(self) -> None:
        self.cancel_pending_navigation()
        if self._status_expire_after is not None:
            try:
                self.root.after_cancel(self._status_expire_after)
            except tk.TclError:
                pass
            self._status_expire_after = None
        if self._status_unsubscribe is not None:
            self._status_unsubscribe()
            self._status_unsubscribe = None
        self.motion.close()
        self.root.unbind('<Escape>', self._escape_binding)
        for sequence in ("<Map>", "<Unmap>"):
            self.root.unbind_class(self._owner_tag, sequence)

    def _transition_to(self, name: str, *, defer_activation: bool = False) -> None:
        hide_scroll_chrome()
        if name not in self.pages:
            return

        # Build under the old-page cover; register/refresh never builds hidden pages.
        changed = name != self.ctx.state.current_page
        transition = getattr(self.ctx.app, "transitions", None)
        if changed and transition is not None and self.root.winfo_viewable():
            if defer_activation:
                previous = self.ctx.state.current_page
                self._transition_pending = True
                self._refresh_version_label()
                def activate():
                    self._transition_pending = False
                    self._refresh_version_label()
                    page = dict.get(self.pages, name)
                    if page is not None and hasattr(page, 'on_show'):
                        page.on_show()
                def restore():
                    self._transition_pending = False
                    self._refresh_version_label()
                    if not getattr(self.ctx.app, '_closing', False):
                        self._show_page(previous)
                transition.change(self.content_canvas, anim.DUR_SNAP,
                                  lambda: self._show_page(name, activate=False),
                                  ready=self._page_layout_ready, wait_s=2.0,
                                  on_ready=activate, on_cancel=restore)
            else:
                transition.change(self.content_canvas, anim.DUR_SNAP, lambda: self._show_page(name),
                                  ready=self._page_layout_ready)
        else:
            self._show_page(name)

    def _show_page(self, name: str, *, activate: bool = True) -> None:
        page = self.pages[name]
        current_name = self.ctx.state.current_page
        if current_name == name and page.winfo_ismapped():
            self._sync_content_window()
            return
        if current_name != name:
            current_page = dict.get(self.pages, current_name)
            if current_page is not None and hasattr(current_page, "on_hide"):
                current_page.on_hide()

        page = self.pages[name]
        for other in self.pages.built_values():
            if other is not page:
                other.grid_remove()
        page.grid()
        page.lift()
        self.ctx.state.current_page = name
        focus = self.root.focus_get()
        if focus is not None and self._is_content_widget(focus):
            self.buttons.get(name, self._settings_button).focus_set()
        self._refresh_button_styles()

        if activate and hasattr(page, "on_show"):
            page.on_show()
        self._sync_mode_buttons(page)
        self._sync_content_window()
        self.motion.refresh(animate=True)

    def _page_layout_ready(self) -> bool:
        page = dict.get(self.pages, self.ctx.state.current_page)
        if page is None or not page.winfo_ismapped() or page.winfo_height() <= 1:
            return False
        if getattr(page, "pin_window_footer", False) and (
            self.content.winfo_width() != self.content_canvas.winfo_width()
            or self.content.winfo_height() != self.content_canvas.winfo_height()
        ):
            return False
        # A coalesced scroll layout is not yet reflected in native update
        # regions. It must complete before a snapshot is allowed to fade.
        prefix = str(page) + "."
        for area in tuple(getattr(self.root, "_piezo_pending_scroll_layouts", ())):
            if str(area).startswith(prefix) and area.winfo_ismapped():
                return False
        # Headers, the active mode viewport and the footer must all be laid out.
        return all(child.winfo_ismapped() and child.winfo_height() > 1
                   for row in (0, 1, 2) for child in page.grid_slaves(row=row))

    def refresh_language(self) -> None:
        self.cancel_pending_navigation()
        lang = self.ctx.state.language
        self._redraw_sidebar_brand()
        self._refresh_version_label()
        self._render_status()
        self._render_connection_notice()
        self._guide_button.configure(text=t(lang, "user_guide"))
        if self._user_guide_window is not None:
            self._user_guide_window.refresh_language()

        for name, btn in self.buttons.items():
            key = self.label_keys.get(name, name)
            btn.configure(text=t(lang, key))
        for key, btn in self.mode_buttons.items():
            btn.configure(text=t(lang, key))

        self._refresh_visibility()

        for page in self.pages.built_values():
            if hasattr(page, "refresh_language"):
                page.refresh_language()

    def refresh_theme(self) -> None:
        self.cancel_pending_navigation()
        self.motion.reset()
        self.refresh_layout()
        self._settings_images.clear()
        self._refresh_settings_button()
        self._render_status()
        self._render_connection_notice()
        self._refresh_visibility()
        for page in self.pages.built_values():
            if hasattr(page, "refresh_theme"):
                page.refresh_theme()

    def refresh_layout(self) -> None:
        self.container.columnconfigure(0, minsize=self._scaled(round(224 * BRAND_ENLARGE)))
        self.sidebar.configure(padding=self._pad(10, 8))
        if getattr(self, "_settings_bar", None) is not None:
            self._settings_bar.configure(padding=self._pad(10, 8))
        self.sidebar_canvas.configure(bg=self.ctx.colors["panel_alt"], yscrollincrement=1)
        self.content.configure(padding=self._pad(16, 8))
        self._title_canvas.grid_configure(pady=(0, self._scaled(8)))
        self.content_canvas.configure(bg=self.ctx.colors["bg"], yscrollincrement=1)
        self._relayout_sidebar()
        self._brand_state = None
        if not self._suspend_brand_configure:
            self._redraw_sidebar_brand()
        for page in self.pages.built_values():
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
        path_key = str(path)
        sources = getattr(self, "_sidebar_logo_sources", None)
        if sources is None:
            sources = {}
            self._sidebar_logo_sources = sources
        source = sources.get(path_key)
        if source is None:
            source = tk.PhotoImage(file=path_key, master=self.root)
            sources[path_key] = source
        token = photoimage_scale_token(
            int(source.width()),
            int(source.height()),
            max_width=max_width,
            enlarge=BRAND_ENLARGE,
        )
        cache = getattr(self, "_sidebar_logo_cache", None)
        if cache is None:
            cache = {}
            self._sidebar_logo_cache = cache
        key = (path_key, token)
        image = cache.get(key)
        if image is None:
            image = scale_photoimage(source, max_width=max_width, enlarge=BRAND_ENLARGE)
            cache[key] = image
        return image

    def _redraw_sidebar_brand(self, _event: tk.Event | None = None) -> None:
        if _event is not None and self._suspend_brand_configure:
            return
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

    def _align_home_panel(self) -> None:
        if getattr(self, "_aligning_home", False):
            return
        home = dict.get(self.pages, "home")
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
        current_page = self._pending_page or self.ctx.state.current_page
        for name, btn in self.buttons.items():
            btn.configure(style="SidebarSelected.TButton" if name == current_page else "Sidebar.TButton")
        self._refresh_settings_button()

    def _mode_keys_for(self, page: Any) -> list[str]:
        mode_pages = getattr(page, "mode_pages", None)
        if not isinstance(mode_pages, dict) or not mode_pages:
            return []
        return list(mode_pages.keys())

    def _clear_mode_buttons(self, *, animate: bool = False) -> None:
        animator = anim.animator_for(self.root)
        if animator is not None:
            animator.cancel("nav-modes", finish=True)
        for btn in self._retired_mode_buttons:
            btn.destroy()
        self._retired_mode_buttons.clear()
        host = self.mode_host
        for key, btn in self.mode_buttons.items():
            current = getattr(host, "mode_buttons", None)
            if isinstance(current, dict) and current.get(key) is btn:
                current.pop(key, None)
            self._retired_mode_buttons.append(btn)
        self.mode_buttons = {}
        self.mode_host = None

        def cleared() -> None:
            for btn in self._retired_mode_buttons:
                btn.destroy()
            self._retired_mode_buttons.clear()
            self._mode_visible = False
            self.mode_clip.grid_remove()
            self._mode_height = 0.0
            self._relayout_sidebar()
            self.motion.refresh()

        if animate and self._mode_visible:
            self._animate_modes(0, cleared)
        else:
            cleared()

    def _animate_modes(self, target: int, done: Callable[[], None] | None = None) -> None:
        if target > 0 and not self._mode_sidebar_allowed():
            target = 0
        start = self._mode_height
        animator = anim.animator_for(self.root)
        def step(p: float) -> None:
            self._mode_height = max(0.0, start+(target-start)*p)
            self.mode_clip.configure(height=max(1, round(self._mode_height)))
            self._sync_sidebar_scroll_region()
        def finished() -> None:
            if target > 0 and self.mode_host is not None:
                self._mode_height = float(self.mode_frame.winfo_reqheight())
                self.mode_clip.configure(height=max(1, round(self._mode_height)))
            if done is not None:
                done()
            self.motion.refresh(animate=True)
        if animator is None:
            step(1)
            finished()
        else:
            animator.animate("nav-modes", anim.DUR_BASE, step, ease=anim.SPRING_CARD, done=finished)

    def _on_mode_frame_configure(self, _event: tk.Event | None = None) -> None:
        animator = anim.animator_for(self.root)
        if self.mode_host is not None and (animator is None or not animator.is_running("nav-modes")):
            self._mode_height = float(self.mode_frame.winfo_reqheight())
            self.mode_clip.configure(height=max(1, round(self._mode_height)))
            self.motion.refresh()

    def set_chip_pages_visible(
        self,
        visible: bool,
        highlight: str | None = None,
        *,
        keep_group: str | None = None,
        disabled: bool = False,
    ) -> None:
        """Apply the one sidebar visibility state for all chip page groups."""
        highlight = self._resolve_chip_group(highlight) if highlight else None
        keep_group = self._resolve_chip_group(keep_group) if keep_group else None
        phase = self._visibility.get("phase", "offline")
        if disabled:
            phase = "disconnected"
        elif visible:
            phase = "ready_identified" if highlight else "ready_unidentified"
        elif phase not in {"connecting", "waiting_ready"}:
            phase = "offline"
        self._visibility = {
            "phase": phase,
            "visible": bool(visible),
            "highlight": highlight,
            "keep_group": keep_group,
            "disabled": bool(disabled),
        }
        self._refresh_visibility()

    def set_connection_phase(self, phase: str) -> None:
        """Track connection progress while keeping startup chip pages hidden."""
        if phase not in {"offline", "connecting", "waiting_ready", "disconnected"}:
            raise ValueError(f"invalid connection phase: {phase}")
        if phase in {"offline", "connecting", "waiting_ready"}:
            self._visibility = {
                "phase": phase,
                "visible": False,
                "highlight": None,
                "keep_group": None,
                "disabled": False,
            }
        self._refresh_visibility()

    def set_connection_notice(self, key: str | None) -> None:
        self._connection_notice_key = str(key) if key else None
        self._render_connection_notice()

    def set_developer_visibility_override(self, enabled: bool) -> None:
        self._developer_visibility_override = bool(enabled)
        if enabled and self._visibility.get("phase") in {"offline", "connecting", "waiting_ready"}:
            self._visibility["visible"] = True
        elif not enabled and self._visibility.get("phase") in {"offline", "connecting", "waiting_ready"}:
            self._visibility["visible"] = False
        self._refresh_visibility()

    def _resolve_chip_group(self, value: str | None) -> str | None:
        if value is None:
            return None
        value = str(value)
        if value in self._chip_group_by_page.values():
            return value
        mapped = self._chip_page_by_device.get(value.upper())
        return self._chip_group_by_page.get(mapped) if mapped else None

    def _page_visible(self, name: str) -> bool:
        group = self._chip_group_by_page.get(name)
        if group is None:
            return True
        state = self._visibility
        phase = state.get("phase")
        visible = bool(state.get("visible")) or self._developer_visibility_override
        if not visible:
            return False
        keep_group = state.get("keep_group")
        return self._developer_visibility_override or keep_group is None or group == keep_group

    def _page_navigation_enabled(self, name: str) -> bool:
        return self._page_visible(name) and not self._page_disabled(name)

    def _page_disabled(self, name: str) -> bool:
        group = self._chip_group_by_page.get(name)
        return bool(group is not None and self._visibility.get("disabled"))

    def _mode_sidebar_allowed(self) -> bool:
        return self._page_visible(str(self._mode_anchor))

    def _configure_chip_button(self, name: str) -> None:
        button = self.buttons.get(name)
        if button is None or name not in self._chip_group_by_page:
            return
        group = self._chip_group_by_page[name]
        key = self.label_keys.get(name, name)
        prefix = "● " if group == self._visibility.get("highlight") else ""
        button.configure(text=prefix + t(self.ctx.state.language, key))
        if self._page_disabled(name):
            button.state(["disabled"])
        else:
            button.state(["!disabled"])

    def _refresh_visibility(self) -> None:
        for name in self.buttons:
            self._configure_chip_button(name)
        for button in self.mode_buttons.values():
            if self._visibility.get("disabled") and self._mode_sidebar_allowed():
                button.state(["disabled"])
            else:
                button.state(["!disabled"])
        if self._pending_page is not None and not self._page_navigation_enabled(self._pending_page):
            self.cancel_pending_navigation()
        self._relayout_sidebar()
        if (
            self.mode_host is not None
            and self._mode_sidebar_allowed()
            and self._mode_visible
            and self._mode_height <= 0
        ):
            try:
                self.root.after_idle(lambda page=self.mode_host: self._expand_current_modes(page))
            except tk.TclError:
                pass

    def _relayout_sidebar(self) -> None:
        row = 1
        for index, (name, btn) in enumerate(self.buttons.items()):
            if not self._page_visible(name):
                btn.grid_remove()
                continue
            pady = (0, 3) if index == 0 else 3
            btn.grid(row=row, column=0, sticky="ew", pady=pady)
            row += 1
            if name == self._mode_anchor and self._mode_visible and self._mode_sidebar_allowed():
                self.mode_clip.grid(row=row, column=0, sticky="ew", padx=(self._scaled(16), 0), pady=(0, 3))
                row += 1
        if not self._mode_visible or not self._mode_sidebar_allowed():
            self.mode_clip.grid_remove()
        self._sync_sidebar_scroll_region()

    @staticmethod
    def _short_status(text: str, limit: int = 14) -> str:
        text = str(text or "")
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 1)] + "…"

    def _resolve_status_text(self, value: Any) -> str:
        if isinstance(value, StatusMessage):
            return t(self.ctx.state.language, value.key, **dict(value.args))
        return str(value or "")

    def _on_status_update(self, update: StatusUpdate) -> None:
        bus = getattr(self.ctx, "status_bus", None)
        if bus is None or not bus.accepts(update.token):
            return
        if update.token.sequence < bus.display_sequence:
            return
        if update.state == "receipt":
            # Part receipts feed the progress subscriber; the aggregate outcome
            # controls sidebar text after all matching parts have completed.
            return
        if update.state not in {"running", "success", "failure"}:
            return
        self._status_display_sequence = update.token.sequence
        self._status_state = update.state
        self._status_detail = update.text
        self._status_error = str(update.error or "")
        self._status_timestamp = float(update.timestamp)
        self._cancel_status_expiry()
        self._render_status()
        if update.state == "success":
            self._status_expire_after = self.root.after(
                8000, self._expire_status, update.token.sequence, update.token,
            )

    def _cancel_status_expiry(self) -> None:
        if self._status_expire_after is None:
            return
        try:
            self.root.after_cancel(self._status_expire_after)
        except tk.TclError:
            pass
        self._status_expire_after = None

    def _expire_status(self, sequence: int, token_or_session: Any) -> None:
        self._status_expire_after = None
        if sequence != self._status_display_sequence or self._status_state != "success":
            return
        bus = getattr(self.ctx, "status_bus", None)
        if bus is None or sequence != bus.display_sequence:
            return
        token = token_or_session if hasattr(token_or_session, "context") else None
        accepts = getattr(bus, "accepts", None)
        if token is not None and callable(accepts) and not accepts(token):
            return
        token_session = getattr(token, "port_session", token_or_session)
        token_context = getattr(token, "context", None)
        page_name = str(getattr(token_context, "page_name", "") or "")
        from core.status_bus import normalized_port_session
        normalized = normalized_port_session(token_session) if token_session is not None else None
        token_port = normalized[0] if isinstance(normalized, tuple) and normalized else None
        serial = getattr(self.ctx, "serial", None)
        selector = getattr(serial, "selected_port_for_page", None)
        selected_port = getattr(getattr(self.ctx, "state", None), "selected_port", None)
        if token_port and not page_name and selected_port:
            if str(selected_port).strip() != str(token_port).strip():
                return
        if token_port and page_name and callable(selector):
            try:
                current_page_port = selector(page_name)
            except TypeError:
                try:
                    current_page_port = selector()
                except Exception:
                    return
            except Exception:
                return
            if current_page_port and str(current_page_port).strip() != str(token_port).strip():
                return
        identity_reader = getattr(serial, "connection_identity", None)
        if token_session is not None and token_port and callable(identity_reader):
            try:
                current_identity = identity_reader(str(token_port))
            except Exception:
                current_identity = None
            # A disconnected operation may still expire normally. A live
            # manager on the reused port must match the captured generation.
            if current_identity is not None and normalized_port_session(current_identity) != normalized:
                return
        self._status_state = "ready"
        self._status_detail = None
        self._status_error = ""
        self._render_status()

    def _status_tooltip_text(self) -> str:
        lang = self.ctx.state.language
        state_key = {
            "running": "sidebar_status_running",
            "success": "sidebar_status_success",
            "failure": "sidebar_status_failure",
            "ready": "sidebar_status_ready",
        }.get(self._status_state, "sidebar_status_ready")
        state = t(lang, state_key)
        detail = self._resolve_status_text(self._status_detail)
        if self._status_error and self._status_error not in detail:
            detail = f"{detail}\n{self._status_error}" if detail else self._status_error
        timestamp = ""
        if self._status_timestamp is not None:
            try:
                timestamp = datetime.fromtimestamp(self._status_timestamp).strftime("%Y-%m-%d %H:%M:%S")
            except (OSError, OverflowError, ValueError):
                timestamp = str(self._status_timestamp)
        return t(lang, "sidebar_status_tooltip", state=state, detail=detail, time=timestamp)

    def _render_status(self) -> None:
        if not hasattr(self, "_status_text"):
            return
        lang = self.ctx.state.language
        if self._status_state == "ready":
            label = t(lang, "sidebar_status_ready")
            color = "#6B7280"
        else:
            label = self._resolve_status_text(self._status_detail)
            color = {
                "running": "#D99A00",
                "success": "#2563EB",
                "failure": "#DC2626",
            }.get(self._status_state, "#6B7280")
        try:
            self._status_dot.configure(fg=color, bg=self.ctx.colors.get("panel_alt", "#F3F4F6"))
            self._status_text.configure(text=self._short_status(label))
            if self._status_tooltip is not None:
                self._status_tooltip.text_getter = self._status_tooltip_text
            if self._status_tooltip_text_widget is not None:
                self._status_tooltip_text_widget.text_getter = self._status_tooltip_text
        except tk.TclError:
            pass

    def _render_connection_notice(self) -> None:
        if not hasattr(self, "_connection_notice_frame"):
            return
        if not self._connection_notice_key:
            self._connection_notice_frame.grid_remove()
            return
        key = self._connection_notice_key
        self._connection_notice_label.configure(text=t(self.ctx.state.language, key))
        if key == "sidebar_disconnected_reconnect":
            self._connection_home_button.configure(text=t(self.ctx.state.language, "sidebar_reconnect_home"))
            self._connection_home_button.grid()
        else:
            self._connection_home_button.grid_remove()
        self._connection_notice_frame.grid()

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
            region = (0, 0, content_width, content_height)
            if tuple(map(float, self.sidebar_canvas.tk.splitlist(self.sidebar_canvas.cget("scrollregion")))) != region:
                self.sidebar_canvas.configure(scrollregion=region)
            needs_scrollbar = content_height > canvas_height
            if needs_scrollbar and not self._sidebar_scrollbar_visible:
                self.sidebar_scrollbar.grid(row=0, column=1, sticky="ns")
                self._sidebar_scrollbar_visible = True
            elif not needs_scrollbar and self._sidebar_scrollbar_visible:
                self.sidebar_scrollbar.grid_remove()
                self._sidebar_scrollbar_visible = False
        except tk.TclError:
            pass

    def _on_sidebar_yview(self, first: str, last: str) -> None:
        self._sidebar_yview_key = relay_vertical_scroll(
            self.sidebar_scrollbar, self._sidebar_yview_key, first, last
        )

    def _on_content_yview(self, first: str, last: str) -> None:
        self._content_yview_key = relay_vertical_scroll(
            self.v_scrollbar, self._content_yview_key, first, last
        )

    def _on_content_xview(self, first: str, last: str) -> None:
        self._content_xview_key = relay_vertical_scroll(
            self.h_scrollbar, self._content_xview_key, first, last
        )

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
            self.sidebar_canvas.yview_scroll(delta * scaled_scroll_px(self._scale()), "units")
        except (tk.TclError, AttributeError, TypeError, ValueError):
            pass

    def _sync_mode_buttons(self, page: Any) -> None:
        keys = self._mode_keys_for(page)
        if not keys:
            if self.mode_host is not None:
                self._clear_mode_buttons(animate=True)
            return
        if self.mode_host is not page or list(self.mode_buttons.keys()) != keys:
            self._clear_mode_buttons()
            self.mode_host = page
            self._mode_anchor = self.ctx.state.current_page
            self._mode_visible = True
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
                self.motion.bind(btn)
            # Requested height is known without a nested update()/input loop.
            self.root.after(0, lambda: self._expand_current_modes(page))
        self._relayout_sidebar()
        sync = getattr(page, "_sync_mode_button_styles", None)
        if callable(sync):
            sync()
        else:
            self._refresh_mode_button_styles()

    def _expand_current_modes(self, page: Any) -> None:
        if self.mode_host is page:
            self._animate_modes(self.mode_frame.winfo_reqheight())

    def _on_mode_clicked(self, key: str) -> None:
        hide_scroll_chrome()
        self._finish_waiting_transition()
        self.cancel_pending_navigation()
        page = self.mode_host
        if page is None or not hasattr(page, "show_mode"):
            return
        pages = getattr(page, 'mode_pages', None)
        if isinstance(pages, LazyPages) and pages.can_prepare(key):
            self._pending_mode = key
            self._refresh_mode_button_styles()
            self._start_preparation(pages, key, lambda: self._commit_mode(page, key))
            return
        self._commit_mode(page, key)

    def _commit_mode(self, page: Any, key: str) -> None:
        transition = getattr(self.ctx.app, "transitions", None)
        if transition is not None and key != getattr(page, "current_mode", None):
            previous = page.current_mode
            self._transition_pending = True
            self._refresh_version_label()
            def ready():
                self._transition_pending = False
                self._refresh_version_label()
            def restore():
                ready()
                if not getattr(self.ctx.app, '_closing', False):
                    page.show_mode(previous)
                    self._refresh_mode_button_styles()
            transition.change(self.content_canvas, anim.DUR_SNAP, lambda: page.show_mode(key),
                              ready=self._page_layout_ready, wait_s=2.0,
                              on_ready=ready, on_cancel=restore)
        else:
            page.show_mode(key)
        self.motion.refresh(animate=True)
        sync = getattr(page, "_sync_mode_button_styles", None)
        if callable(sync):
            sync()
        else:
            self._refresh_mode_button_styles()

    def _refresh_mode_button_styles(self) -> None:
        current = self._pending_mode or getattr(self.mode_host, "current_mode", None)
        for key, btn in self.mode_buttons.items():
            btn.configure(style="SidebarModeSelected.TButton" if key == current else "SidebarMode.TButton")

    def _refresh_version_label(self) -> None:
        if self._preparation is not None or self._transition_pending:
            text = '正在准备… Esc 取消' if self.ctx.state.language == 'zh' else 'Preparing… Esc to cancel'
            self._version_label.configure(text=text)
            return
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
        selected = (self._pending_page or self.ctx.state.current_page) == "settings"
        image = self._gear_image(selected)
        button.configure(
            style="SidebarSelected.TButton" if selected else "Sidebar.TButton",
            image=image,
        )
        button.image = image

    def _open_user_guide(self) -> None:
        guide = self._user_guide_window
        if guide is not None:
            try:
                if guide.window.winfo_exists():
                    guide.refresh_language()
                    guide.window.deiconify()
                    guide.window.lift()
                    return
            except tk.TclError:
                self._user_guide_window = None
        self._user_guide_window = UserGuideWindow(
            self.root,
            translate=lambda key: t(self.ctx.state.language, key),
            colors=getattr(self.ctx, "colors", None),
        )

    def _sync_content_window(self) -> None:
        if self._sync_after is not None:
            return
        try:
            # Native Expose traffic can starve idle callbacks on Windows.
            # Coalesce into the next timer turn so the viewport does not
            # retain the previous window size while a page is painting.
            self._sync_after = self.root.after(0, self._sync_content_window_now)
        except tk.TclError:
            self._sync_after = None

    def _sync_content_window_now(self) -> None:
        self._sync_after = None
        try:
            canvas_width = max(1, self.content_canvas.winfo_width())
            canvas_height = max(1, self.content_canvas.winfo_height())
            current_name = getattr(self.ctx.state, "current_page", "")
            page = dict.get(self.pages, current_name)
            padx, pady = self._pad(16, 8)
            required_width = max(1, page.winfo_reqwidth()+2*padx) if page is not None else 1
            required_height = max(1, page.winfo_reqheight()+2*pady) if page is not None else 1
            pin = bool(getattr(page, "pin_window_footer", False)) if page is not None else False
            window_width = canvas_width if pin else max(canvas_width, required_width)
            window_height = canvas_height if pin else max(canvas_height, required_height)
            state = (canvas_width, canvas_height, required_width, required_height, current_name, pin)
            if state == self._last_canvas_state:
                return
            self._last_canvas_state = state

            # Reconfiguring a Canvas window invalidates its whole rectangle,
            # even when the dimensions are unchanged. Page identity/request
            # changes must not cause another full repaint of a pinned viewport.
            current_size = tuple(float(self.content_canvas.itemcget(self.content_window, key))
                                 for key in ("width", "height"))
            if current_size != (window_width, window_height):
                self.content_canvas.itemconfigure(self.content_window, width=window_width, height=window_height)
            region = (0, 0, window_width, window_height)
            if tuple(map(float, self.content_canvas.tk.splitlist(self.content_canvas.cget("scrollregion")))) != region:
                self.content_canvas.configure(scrollregion=region)
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
            self.content_canvas.yview_scroll(delta * scaled_scroll_px(self._scale()), "units")
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
