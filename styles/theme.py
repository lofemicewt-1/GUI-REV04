from __future__ import annotations

import tkinter as tk
from tkinter import ttk


LIGHT_COLORS = {
    "bg": "#F5F7FA",
    "panel": "#FFFFFF",
    "panel_alt": "#F9FAFB",
    "text": "#111827",
    "muted": "#6B7280",
    "border": "#D1D5DB",
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "danger": "#DC2626",
    "success": "#16A34A",
    "canvas_bg": "#FFFFFF",
}

DARK_COLORS = {
    "bg": "#111827",
    "panel": "#1F2937",
    "panel_alt": "#172033",
    "text": "#F9FAFB",
    "muted": "#9CA3AF",
    "border": "#374151",
    "primary": "#60A5FA",
    "primary_hover": "#3B82F6",
    "danger": "#F87171",
    "success": "#4ADE80",
    "canvas_bg": "#111827",
}


def get_colors(theme: str) -> dict[str, str]:
    return DARK_COLORS if theme == "dark" else LIGHT_COLORS


def _restore_button_style(widget: ttk.Button, original_style: str, flash_style: str) -> None:
    try:
        if widget.winfo_exists() and widget.cget("style") == flash_style:
            widget.configure(style=original_style)
    except tk.TclError:
        pass


def _flash_button(event: tk.Event) -> None:
    widget = event.widget
    try:
        if not isinstance(widget, ttk.Button) or widget.instate(["disabled"]):
            return
        original_style = widget.cget("style")
        if original_style == "Selected.TButton":
            return
        flash_style = "Flash.TButton"
        widget.configure(style=flash_style)
        widget.after(140, _restore_button_style, widget, original_style, flash_style)
    except tk.TclError:
        pass


def _replace_layout_element(layout: list[tuple[str, dict]], old_name: str, new_name: str) -> list[tuple[str, dict]]:
    updated = []
    for element_name, options in layout:
        next_options = dict(options)
        if "children" in next_options:
            next_options["children"] = _replace_layout_element(next_options["children"], old_name, new_name)
        updated.append((new_name if element_name == old_name else element_name, next_options))
    return updated


def _make_checkmark_images(root: tk.Misc, colors: dict[str, str]) -> tuple[tk.PhotoImage, ...]:
    size = 16

    def make_image(*, checked: bool = False, disabled: bool = False) -> tk.PhotoImage:
        image = tk.PhotoImage(master=root, width=size, height=size)
        image.put(colors["panel"], to=(0, 0, size, size))

        border = colors["muted"] if disabled else colors["border"]
        mark = colors["muted"] if disabled else colors["primary"]
        left, top, right, bottom = 2, 2, 13, 13

        image.put(border, to=(left, top, right + 1, top + 1))
        image.put(border, to=(left, bottom, right + 1, bottom + 1))
        image.put(border, to=(left, top, left + 1, bottom + 1))
        image.put(border, to=(right, top, right + 1, bottom + 1))

        if checked:
            points = ((5, 8), (6, 9), (7, 10), (8, 8), (9, 7), (10, 6), (11, 5))
            for x, y in points:
                image.put(mark, to=(x, y, x + 2, y + 2))

        return image

    return (
        make_image(),
        make_image(checked=True),
        make_image(disabled=True),
        make_image(checked=True, disabled=True),
    )


def _configure_checkmark_styles(root: tk.Misc, style: ttk.Style, colors: dict[str, str], theme: str) -> None:
    element_name = f"{theme}.Checkmark.indicator"
    image_store = getattr(root, "_checkmark_style_images", {})
    if theme not in image_store:
        image_store[theme] = _make_checkmark_images(root, colors)
        setattr(root, "_checkmark_style_images", image_store)

    unchecked, checked, disabled, disabled_checked = image_store[theme]
    if element_name not in style.element_names():
        style.element_create(
            element_name,
            "image",
            unchecked,
            ("selected", "disabled", disabled_checked),
            ("selected", checked),
            ("disabled", disabled),
            sticky="",
        )

    check_layout = _replace_layout_element(style.layout("TCheckbutton"), "Checkbutton.indicator", element_name)
    style.layout("TCheckbutton", check_layout)
    style.layout("Check.TRadiobutton", check_layout)


def _scaled(value: int | float, scale: float) -> int:
    return max(1, int(round(value * scale)))


def apply_theme(root: tk.Misc, theme: str = "light", font_size: int = 10, window_scale: float = 1.0) -> dict[str, str]:
    """
    对 ttk 和普通 Tk 组件应用统一主题。
    """
    colors = get_colors(theme)
    button_bg = colors["bg"]
    selected_bg = "#1D4ED8" if theme != "dark" else "#1E40AF"
    scale = max(0.75, min(1.35, window_scale))
    base_font_size = max(8, min(18, int(font_size)))
    body_size = max(8, min(24, _scaled(base_font_size, scale)))
    title_size = max(14, min(34, _scaled(base_font_size + 8, scale)))
    subtitle_size = max(8, min(22, _scaled(base_font_size, scale)))
    sidebar_title_size = max(10, min(26, _scaled(base_font_size + 3, scale)))
    small_size = max(7, min(20, _scaled(base_font_size - 1, scale)))
    mono_size = max(7, min(20, _scaled(9, scale * (base_font_size / 10))))

    setattr(root, "_piezo_font_family", "Microsoft YaHei UI")
    setattr(root, "_piezo_body_font_size", body_size)
    setattr(root, "_piezo_title_font_size", title_size)
    setattr(root, "_piezo_sidebar_title_font_size", sidebar_title_size)
    setattr(root, "_piezo_mono_font_size", mono_size)
    setattr(root, "_piezo_spacing_scale", scale)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(
        ".",
        background=colors["bg"],
        foreground=colors["text"],
        fieldbackground=colors["panel"],
        bordercolor=colors["border"],
        lightcolor=colors["border"],
        darkcolor=colors["border"],
        troughcolor=colors["panel_alt"],
        font=("Microsoft YaHei UI", body_size),
    )

    style.configure(
        "App.TFrame",
        background=colors["bg"],
    )

    style.configure(
        "Panel.TFrame",
        background=colors["panel"],
        relief="flat",
        borderwidth=1,
    )

    style.configure(
        "Card.TFrame",
        background=colors["panel"],
        relief="flat",
        borderwidth=1,
    )

    style.configure(
        "Sidebar.TFrame",
        background=colors["panel_alt"],
    )

    style.configure(
        "SidebarVersion.TLabel",
        background=colors["panel_alt"],
        foreground=colors["muted"],
        font=("Microsoft YaHei UI", small_size),
    )

    style.configure(
        "Title.TLabel",
        background=colors["bg"],
        foreground=colors["text"],
        font=("Microsoft YaHei UI", title_size, "bold"),
    )

    style.configure(
        "Subtitle.TLabel",
        background=colors["bg"],
        foreground=colors["muted"],
        font=("Microsoft YaHei UI", subtitle_size),
    )

    style.configure(
        "Panel.TLabel",
        background=colors["panel"],
        foreground=colors["text"],
        font=("Microsoft YaHei UI", body_size),
    )

    style.configure(
        "PanelTitle.TLabel",
        background=colors["panel"],
        foreground=colors["text"],
        font=("Microsoft YaHei UI", title_size, "bold"),
    )

    style.configure(
        "SectionTitle.TLabel",
        background=colors["panel"],
        foreground=colors["text"],
        font=("Microsoft YaHei UI", body_size, "bold"),
    )

    style.configure(
        "Muted.TLabel",
        background=colors["panel"],
        foreground=colors["muted"],
        font=("Microsoft YaHei UI", body_size),
    )

    for check_style in ("TCheckbutton", "Check.TRadiobutton"):
        style.configure(
            check_style,
            background=colors["panel"],
            foreground=colors["text"],
            focuscolor=colors["panel"],
            indicatorbackground=colors["panel"],
            indicatorforeground=colors["text"],
            indicatorcolor=colors["panel"],
            padding=(2, 2),
        )
        style.map(
            check_style,
            background=[("active", colors["panel"])],
            foreground=[("disabled", colors["muted"])],
            indicatorbackground=[("selected", colors["panel"]), ("active", colors["panel"])],
            indicatorforeground=[("selected", colors["primary"]), ("disabled", colors["muted"])],
            indicatorcolor=[("selected", colors["primary"]), ("active", colors["panel"])],
        )

    try:
        _configure_checkmark_styles(root, style, colors, theme)
    except tk.TclError:
        pass

    neutral_button_styles = ("TButton", "Primary.TButton", "Secondary.TButton")
    for button_style in neutral_button_styles:
        style.configure(
            button_style,
            background=button_bg,
            foreground=colors["text"],
            borderwidth=1,
            bordercolor=colors["border"],
            lightcolor=colors["border"],
            darkcolor=colors["border"],
            focusthickness=0,
            padding=(_scaled(12, scale), _scaled(7, scale)),
            font=("Microsoft YaHei UI", body_size),
        )
        style.map(
            button_style,
            background=[("pressed", selected_bg), ("active", button_bg)],
            foreground=[("pressed", "#FFFFFF"), ("disabled", "#9CA3AF")],
            bordercolor=[("pressed", selected_bg), ("active", colors["border"])],
        )

    style.configure(
        "Danger.TButton",
        background=button_bg,
        foreground=colors["danger"],
        borderwidth=1,
        bordercolor=colors["border"],
        lightcolor=colors["border"],
        darkcolor=colors["border"],
        focusthickness=0,
        padding=(_scaled(12, scale), _scaled(7, scale)),
        font=("Microsoft YaHei UI", body_size),
    )
    style.map(
        "Danger.TButton",
        background=[("pressed", selected_bg), ("active", button_bg)],
        foreground=[("pressed", "#FFFFFF"), ("disabled", "#9CA3AF")],
        bordercolor=[("pressed", selected_bg), ("active", colors["border"])],
    )

    for button_style in ("Selected.TButton", "Flash.TButton"):
        style.configure(
            button_style,
            background=selected_bg,
            foreground="#FFFFFF",
            borderwidth=1,
            bordercolor=selected_bg,
            lightcolor=selected_bg,
            darkcolor=selected_bg,
            focusthickness=0,
            padding=(_scaled(12, scale), _scaled(7, scale)),
            font=("Microsoft YaHei UI", body_size),
        )
        style.map(
            button_style,
            background=[("active", selected_bg), ("pressed", selected_bg)],
            foreground=[("disabled", "#D1D5DB")],
            bordercolor=[("active", selected_bg), ("pressed", selected_bg)],
        )

    style.configure(
        "TEntry",
        fieldbackground=colors["panel"],
        foreground=colors["text"],
        insertcolor=colors["text"],
        borderwidth=1,
        padding=(_scaled(6, scale), _scaled(5, scale)),
        font=("Microsoft YaHei UI", body_size),
    )

    style.configure(
        "TCombobox",
        background=colors["panel"],
        fieldbackground=colors["panel"],
        foreground=colors["text"],
        arrowcolor=colors["text"],
        bordercolor=colors["border"],
        selectbackground=colors["primary"],
        selectforeground="#FFFFFF",
        padding=(_scaled(6, scale), _scaled(5, scale)),
        font=("Microsoft YaHei UI", body_size),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", colors["panel"])],
        foreground=[("readonly", colors["text"])],
        background=[("readonly", colors["panel"]), ("active", colors["panel"])],
        arrowcolor=[("readonly", colors["text"]), ("active", colors["primary"])],
    )

    style.configure(
        "TNotebook",
        background=colors["panel"],
        borderwidth=0,
    )

    style.configure(
        "TNotebook.Tab",
        background=colors["panel"],
        foreground=colors["text"],
        padding=(_scaled(12, scale), _scaled(6, scale)),
        font=("Microsoft YaHei UI", body_size),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", colors["panel"]), ("active", colors["panel"])],
        foreground=[("selected", colors["text"]), ("active", colors["text"])],
    )

    root.option_add("*TCombobox*Listbox.background", colors["panel"])
    root.option_add("*TCombobox*Listbox.foreground", colors["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", colors["primary"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.font", f"{{Microsoft YaHei UI}} {body_size}")
    root.option_add("*Font", f"{{Microsoft YaHei UI}} {body_size}")

    if isinstance(root, (tk.Tk, tk.Toplevel)):
        root.configure(bg=colors["bg"])

    if not getattr(root, "_button_flash_bound", False):
        root.bind_class("TButton", "<ButtonRelease-1>", _flash_button, add="+")
        setattr(root, "_button_flash_bound", True)

    return colors
