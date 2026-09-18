from __future__ import annotations

from pathlib import Path
import re
import tkinter as tk
import threading
import webbrowser
from tkinter import filedialog, messagebox, ttk
from typing import Any

from config.app_info import CHIP_WEBSITE_URLS
from config.languages import t

def build_serial_log_export_content(history: list[str], visible_text: str) -> str:
    """Return the complete serial log export text.

    Prefer the serial manager history, but merge in visible Text content so
    response frames such as ``MCU ^Done~`` are not lost if a page appended them
    directly to the log widget.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for source_line in [*history, *visible_text.splitlines()]:
        line = source_line.rstrip()
        if not line or line in seen:
            continue
        lines.append(line)
        seen.add(line)
    return "\n".join(lines)

class SimpleTooltip:
    def __init__(self, widget: tk.Widget, text_getter: Any) -> None:
        self.widget = widget
        self.text_getter = text_getter
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event: tk.Event) -> None:
        self._hide()
        text = self.text_getter() if callable(self.text_getter) else str(self.text_getter)
        if not text:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tip,
            text=text,
            justify="left",
            bg="#111827",
            fg="#F9FAFB",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
            wraplength=320,
        )
        label.pack()

    def _hide(self, _event: tk.Event | None = None) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class VerticalScrollArea(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        ctx: Any,
        *,
        horizontal: bool = False,
        min_content_width: int = 0,
    ) -> None:
        super().__init__(parent, style="App.TFrame")
        self.ctx = ctx
        self.horizontal = horizontal
        self.min_content_width = max(0, min_content_width)
        self._scrollbar_visible = False
        self._horizontal_scrollbar_visible = False

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.horizontal_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(
            yscrollcommand=self.scrollbar.set,
            xscrollcommand=self.horizontal_scrollbar.set,
        )

        self.content = ttk.Frame(self.canvas, style="App.TFrame")
        self.content.columnconfigure(0, weight=1)
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.content.bind("<Configure>", self._sync_scroll_region)
        self.canvas.bind("<Configure>", self._sync_content_width)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.canvas.bind_all("<Shift-MouseWheel>", self._on_shift_mousewheel, add="+")
        self.refresh_theme()

    def _sync_scroll_region(self, _event: tk.Event | None = None) -> None:
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            needs_scrollbar = self.content.winfo_reqheight() > self.canvas.winfo_height()
            needs_horizontal_scrollbar = (
                self.horizontal and self.content.winfo_reqwidth() > self.canvas.winfo_width()
            )
            if needs_scrollbar and not self._scrollbar_visible:
                self.scrollbar.grid(row=0, column=1, sticky="ns")
                self._scrollbar_visible = True
            elif not needs_scrollbar and self._scrollbar_visible:
                self.scrollbar.grid_remove()
                self._scrollbar_visible = False
            if needs_horizontal_scrollbar and not self._horizontal_scrollbar_visible:
                self.horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
                self._horizontal_scrollbar_visible = True
            elif not needs_horizontal_scrollbar and self._horizontal_scrollbar_visible:
                self.horizontal_scrollbar.grid_remove()
                self._horizontal_scrollbar_visible = False
        except tk.TclError:
            pass

    def _sync_content_width(self, event: tk.Event) -> None:
        try:
            if self.horizontal:
                width = max(1, event.width, self.min_content_width, self.content.winfo_reqwidth())
            else:
                width = max(1, event.width)
            self.canvas.itemconfigure(self.window_id, width=width)
            self._sync_scroll_region()
        except tk.TclError:
            pass

    def _on_mousewheel(self, event: tk.Event) -> None:
        if not self.winfo_ismapped() or self._nearest_scroll_area(event.widget) is not self:
            return
        try:
            delta = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(delta, "units")
        except tk.TclError:
            pass

    def _on_shift_mousewheel(self, event: tk.Event) -> None:
        if not self.horizontal or not self.winfo_ismapped() or self._nearest_scroll_area(event.widget) is not self:
            return
        try:
            delta = -1 if event.delta > 0 else 1
            self.canvas.xview_scroll(delta, "units")
        except tk.TclError:
            pass

    def _contains_widget(self, widget: tk.Misc) -> bool:
        return self._nearest_scroll_area(widget) is self

    @staticmethod
    def _nearest_scroll_area(widget: tk.Misc) -> VerticalScrollArea | None:
        current: tk.Misc | None = widget
        while current is not None:
            if isinstance(current, VerticalScrollArea):
                return current
            current = getattr(current, "master", None)
        return None

    def refresh_theme(self) -> None:
        self.canvas.configure(bg=self.ctx.colors["bg"])


class BasePage(ttk.Frame):
    """
    \u6240\u6709\u9875\u9762\u7684\u57fa\u7c7b\u3002

    \u5b50\u9875\u9762\u5efa\u8bae\u5b9e\u73b0\uff1a
        build_ui()
        refresh_language()
        refresh_theme()
        on_show()
    """

    page_name: str = "base"

    def __init__(self, parent: tk.Misc, ctx: Any) -> None:
        super().__init__(parent, style="App.TFrame")
        self.ctx = ctx
        self._text_widgets: list[tuple[Any, str]] = []
        self._logo_widgets: list[ttk.Label] = []
        self._logo_images: list[tk.PhotoImage] = []
        self._serial_log_text: tk.Text | None = None
        self._serial_log_listener: Any = None
        self._serial_log_clear_listener: Any = None
        self._vertical_scroll_areas: list[VerticalScrollArea] = []
        self._serial_selectors: list[dict[str, Any]] = []

    @property
    def language(self) -> str:
        return self.ctx.state.language

    @property
    def colors(self) -> dict[str, str]:
        return self.ctx.colors

    def tr(self, key: str, **kwargs: Any) -> str:
        return t(self.language, key, **kwargs)

    def bind_text(self, widget: Any, key: str) -> None:
        """
        \u6ce8\u518c\u9700\u8981\u968f\u8bed\u8a00\u5207\u6362\u66f4\u65b0 text \u7684\u63a7\u4ef6\u3002
        """
        self._text_widgets.append((widget, key))
        try:
            widget.configure(text=self.tr(key))
        except Exception:
            pass

    def refresh_language(self) -> None:
        for widget, key in self._text_widgets:
            try:
                widget.configure(text=self.tr(key))
            except Exception:
                pass
        self._refresh_serial_selectors()

    def refresh_theme(self) -> None:
        """
        \u5b50\u7c7b\u91cc\u6709 Canvas / Text \u8fd9\u79cd\u975e ttk \u63a7\u4ef6\u65f6\uff0c\u8986\u76d6\u8fd9\u4e2a\u65b9\u6cd5\u66f4\u65b0\u989c\u8272\u3002
        """
        self._refresh_header_logos()
        self._refresh_serial_log_theme()
        for area in self._vertical_scroll_areas:
            area.refresh_theme()

    def on_show(self) -> None:
        self._refresh_serial_selectors()

    def make_serial_selector(
        self,
        parent: tk.Misc,
        *,
        page_name: str | None = None,
        row: int = 0,
        column: int = 0,
        padx: tuple[int, int] = (0, 8),
    ) -> int:
        label = ttk.Label(parent, style="Panel.TLabel")
        label.grid(row=row, column=column, sticky="w", padx=padx)
        self.bind_text(label, "communication_port")

        variable = tk.StringVar()
        combo = ttk.Combobox(parent, textvariable=variable, values=[], state="readonly", width=22)
        combo.grid(row=row, column=column + 1, sticky="w", padx=(0, 18))

        control: dict[str, Any] = {
            "page_name": page_name or self.page_name,
            "variable": variable,
            "combo": combo,
            "options": {},
        }
        combo.bind("<<ComboboxSelected>>", lambda _event, item=control: self._on_serial_selector_changed(item))
        self._serial_selectors.append(control)
        self._refresh_serial_selector(control)
        return column + 2

    def _serial_options(self) -> list[tuple[str, str]]:
        provider = getattr(self.ctx.serial, "connected_port_options", None)
        if callable(provider):
            return list(provider())
        selected = getattr(self.ctx.state, "selected_port", None)
        return [(selected, selected)] if selected else []

    def _refresh_serial_selectors(self) -> None:
        for control in self._serial_selectors:
            self._refresh_serial_selector(control)

    def _refresh_serial_selector(self, control: dict[str, Any]) -> None:
        options = self._serial_options()
        option_map = {label: port for label, port in options}
        control["options"] = option_map
        combo = control["combo"]
        variable = control["variable"]
        combo.configure(values=list(option_map))

        selected_port = None
        selector = getattr(self.ctx.serial, "selected_port_for_page", None)
        if callable(selector):
            selected_port = selector(control["page_name"])
        else:
            selected_port = getattr(self.ctx.state, "selected_port", None)

        selected_label = next((label for label, port in options if port == selected_port), "")
        if variable.get() != selected_label:
            variable.set(selected_label)

    def _on_serial_selector_changed(self, control: dict[str, Any]) -> None:
        label = control["variable"].get()
        port = control.get("options", {}).get(label)
        binder = getattr(self.ctx.serial, "set_page_port", None)
        if callable(binder):
            binder(control["page_name"], port)

    def make_vertical_scroll_area(
        self,
        parent: tk.Misc,
        *,
        horizontal: bool = False,
        min_content_width: int = 0,
    ) -> tuple[VerticalScrollArea, ttk.Frame]:
        area = VerticalScrollArea(
            parent,
            self.ctx,
            horizontal=horizontal,
            min_content_width=min_content_width,
        )
        self._vertical_scroll_areas.append(area)
        return area, area.content

    def make_title(
        self,
        parent: tk.Misc,
        title_key: str,
        subtitle: str | None = None,
        *,
        show_logo: bool = True,
    ) -> ttk.Frame:
        header = ttk.Frame(parent, style="App.TFrame")
        header.columnconfigure(0, weight=1)

        if title_key == "awp1921_1923":
            title_group = ttk.Frame(header, style="App.TFrame")
            title_group.grid(row=0, column=0, sticky="w")
            self._make_linked_title_part(title_group, "AWP1921", "awp1921", 0)
            ttk.Label(title_group, text="/", style="Title.TLabel").grid(row=0, column=1, sticky="w")
            self._make_linked_title_part(title_group, "AWP1923", "awp1923", 2)
        else:
            title = ttk.Label(header, style="Title.TLabel")
            title.grid(row=0, column=0, sticky="w")
            self.bind_text(title, title_key)
            self._configure_title_link(title, title_key)

        if subtitle:
            sub = ttk.Label(header, style="Subtitle.TLabel")
            sub.grid(row=1, column=0, sticky="w", pady=(3, 0))
            if subtitle.startswith("@"):
                self.bind_text(sub, subtitle[1:])
            else:
                sub.configure(text=subtitle)

        if show_logo:
            logo = self._make_header_logo(header)
            logo.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))

        return header

    def _configure_title_link(self, title: ttk.Label, title_key: str) -> None:
        url = CHIP_WEBSITE_URLS.get(title_key, "").strip()
        if not url:
            return

        size = int(getattr(self.winfo_toplevel(), "_piezo_title_font_size", 18))
        title.configure(cursor="hand2", font=("Microsoft YaHei UI", size, "bold", "underline"))
        title.bind("<Button-1>", lambda _event, link=url: webbrowser.open(link))

    def _set_button_selected(self, key: str, selected: bool) -> None:
        buttons = getattr(self, "_buttons", {})
        button = buttons.get(key) if isinstance(buttons, dict) else None
        if button is not None:
            button.configure(style="Selected.TButton" if selected else "Secondary.TButton")

    def _make_linked_title_part(self, parent: tk.Misc, text: str, title_key: str, column: int) -> None:
        title = ttk.Label(parent, text=text, style="Title.TLabel")
        title.grid(row=0, column=column, sticky="w")
        self._configure_title_link(title, title_key)

    def _make_header_logo(self, parent: tk.Misc) -> ttk.Label:
        label = ttk.Label(parent, style="Title.TLabel")
        self._logo_widgets.append(label)
        self._configure_header_logo(label)
        return label

    def _refresh_header_logos(self) -> None:
        for label in self._logo_widgets:
            try:
                self._configure_header_logo(label)
            except Exception:
                pass

    def _configure_header_logo(self, label: ttk.Label) -> None:
        path = self._logo_path_for_theme()
        if path is not None:
            image = tk.PhotoImage(file=str(path))
            if image.height() > 64:
                factor = max(1, image.height() // 64)
                image = image.subsample(factor, factor)
            self._logo_images.append(image)
            label.configure(image=image, text="")
            return

        label.configure(text="PIEZO\nGUI", justify="center")

    def make_serial_log(
        self,
        parent: tk.Misc,
        *,
        height: int = 7,
        width: int | None = None,
        subscribe: bool = True,
    ) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        title = ttk.Label(frame, style="SectionTitle.TLabel")
        title.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.bind_text(title, "serial_log")

        clear_btn = ttk.Button(frame, style="Secondary.TButton", command=self.clear_serial_log)
        clear_btn.grid(row=0, column=1, sticky="e", pady=(0, 6), padx=(8, 0))
        self.bind_text(clear_btn, "clear_serial_log")

        export_btn = ttk.Button(frame, style="Secondary.TButton", command=self.export_serial_log)
        export_btn.grid(row=0, column=2, sticky="e", pady=(0, 6), padx=(8, 0))
        self.bind_text(export_btn, "export_serial_log")

        text_options: dict[str, Any] = {}
        if width is not None:
            text_options["width"] = width
        text = tk.Text(
            frame,
            height=height,
            wrap="none",
            relief="flat",
            highlightthickness=1,
            font=("Consolas", int(getattr(self.winfo_toplevel(), "_piezo_mono_font_size", 9))),
            **text_options,
        )
        text.grid(row=1, column=0, columnspan=3, sticky="nsew")
        text.configure(state="disabled")
        self._serial_log_text = text
        self._refresh_serial_log_theme()

        if subscribe:
            self._serial_log_listener = self.append_serial_log
            self.ctx.serial.add_log_listener(self._serial_log_listener)
            self._serial_log_clear_listener = self.clear_serial_log_view
            self.ctx.serial.add_log_clear_listener(self._serial_log_clear_listener)
            for line in self.ctx.serial.log_history[-10_000:]:
                self.append_serial_log(line)

        return frame

    def export_serial_log(self) -> None:
        text = self._serial_log_text
        if text is None:
            return

        history = self.ctx.serial.log_history
        visible_content = text.get("1.0", "end-1c")
        content = build_serial_log_export_content(history, visible_content)

        path = filedialog.asksaveasfilename(
            title=self.tr("export_serial_log"),
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            Path(path).write_text(content, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(self.tr("error"), str(exc))
    def append_serial_log(self, line: str) -> None:
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, self.append_serial_log, line)
            except Exception:
                pass
            return

        text = self._serial_log_text
        if text is None:
            return
        try:
            text.configure(state="normal")
            text.insert("end", line + "\n")
            lines = int(text.index("end-1c").split(".")[0])
            if lines > 10_000:
                text.delete("1.0", f"{lines - 10_000}.0")
            text.see("end")
            text.configure(state="disabled")
        except Exception:
            pass

    def clear_serial_log(self) -> None:
        self.ctx.serial.clear_log()
        self.clear_serial_log_view()

    def clear_serial_log_view(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, self.clear_serial_log_view)
            except Exception:
                pass
            return

        text = self._serial_log_text
        if text is None:
            return
        try:
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.configure(state="disabled")
        except Exception:
            pass
    def destroy(self) -> None:
        if self._serial_log_listener is not None:
            self.ctx.serial.remove_log_listener(self._serial_log_listener)
            self._serial_log_listener = None
        if self._serial_log_clear_listener is not None:
            self.ctx.serial.remove_log_clear_listener(self._serial_log_clear_listener)
            self._serial_log_clear_listener = None
        super().destroy()

    def _refresh_serial_log_theme(self) -> None:
        if self._serial_log_text is None:
            return
        self._serial_log_text.configure(
            bg=self.colors["panel_alt"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            highlightbackground=self.colors["border"],
            font=("Consolas", int(getattr(self.winfo_toplevel(), "_piezo_mono_font_size", 9))),
        )

    def _logo_path_for_theme(self) -> Path | None:
        assets_dir = Path(__file__).resolve().parents[1] / "assets"
        themed_name = "logo_dark.png" if self.ctx.state.theme == "dark" else "logo_light.png"
        for name in (themed_name, "logo.png"):
            path = assets_dir / name
            if path.exists():
                return path
        return None
