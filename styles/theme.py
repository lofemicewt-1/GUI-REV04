from __future__ import annotations

import math
from styles.fonts import page_title_weight
from pathlib import Path
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
    "mode_idle": "#2871F3",
    "mode_selected": "#252DCE",
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
    "mode_idle": "#173B70",
    "mode_selected": "#4128DA",
    "danger": "#F87171",
    "success": "#4ADE80",
    "canvas_bg": "#111827",
}


def get_colors(theme: str) -> dict[str, str]:
    return DARK_COLORS if theme == "dark" else LIGHT_COLORS


BRAND_ENLARGE = 1.15


def logo_asset(theme: str) -> Path | None:
    assets = Path(__file__).resolve().parents[1] / "assets"
    name = "logo_dark.png" if theme == "dark" else "logo_light.png"
    for path in (assets / name, assets / "logo.png"):
        if path.exists():
            return path
    return None


def scale_photoimage(
    image: tk.PhotoImage,
    *,
    max_width: int | None = None,
    max_height: int | None = None,
    enlarge: float = 1.0,
) -> tk.PhotoImage:
    """Scale a PhotoImage with integer zoom/subsample, closer than one subsample step."""
    src_w = max(1, int(image.width()))
    src_h = max(1, int(image.height()))
    limit_w = src_w if max_width is None else max(1, int(max_width))
    limit_h = src_h if max_height is None else max(1, int(max_height))
    target_w = src_w
    target_h = src_h
    if max_width is not None and src_w > limit_w:
        factor = max(1, (src_w + limit_w - 1) // limit_w)
        current_w = max(1, src_w // factor)
        target_w = min(limit_w, max(1, int(round(current_w * enlarge))))
        target_h = max(1, int(round(src_h * target_w / src_w)))
    elif max_height is not None and src_h > limit_h:
        factor = max(1, (src_h + limit_h - 1) // limit_h)
        current_h = max(1, src_h // factor)
        target_h = min(limit_h, max(1, int(round(current_h * enlarge))))
        target_w = max(1, int(round(src_w * target_h / src_h)))
    elif enlarge > 1.0:
        grown_w = max(1, int(round(src_w * enlarge)))
        grown_h = max(1, int(round(src_h * enlarge)))
        target_w = min(limit_w, grown_w)
        target_h = max(1, int(round(src_h * target_w / src_w)))
        if target_h > limit_h:
            target_h = limit_h
            target_w = max(1, int(round(src_w * target_h / src_h)))
    if max_width is not None:
        target_w = min(target_w, limit_w)
    if max_height is not None:
        target_h = min(target_h, limit_h)
    return _resample_photoimage(image, target_w, target_h)


def _resample_photoimage(image: tk.PhotoImage, target_w: int, target_h: int) -> tk.PhotoImage:
    src_w = max(1, int(image.width()))
    src_h = max(1, int(image.height()))
    scale = min(max(1, target_w) / src_w, max(1, target_h) / src_h)
    if abs(scale - 1.0) < 0.02:
        return image
    best_n, best_d = 1, 1
    best_err = abs(1.0 - scale)
    for denominator in range(1, 13):
        numerator = max(1, int(round(scale * denominator)))
        if numerator > 16:
            continue
        produced = src_w * numerator / denominator
        if scale < 1.0 and produced > target_w + 1:
            continue
        err = abs((numerator / denominator) - scale)
        if err < best_err - 1e-9 or (abs(err - best_err) <= 1e-9 and numerator + denominator < best_n + best_d):
            best_n, best_d, best_err = numerator, denominator, err
    if best_n == best_d:
        return image
    scaled = image
    if best_n != 1:
        scaled = scaled.zoom(best_n, best_n)
    if best_d != 1:
        scaled = scaled.subsample(best_d, best_d)
    return scaled


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
        if original_style in {
            "Selected.TButton",
            "Bg.TButton",
            "BgSelected.TButton",
            "Sidebar.TButton",
            "SidebarSelected.TButton",
            "SidebarMode.TButton",
            "SidebarModeSelected.TButton",
            "SidebarParentSelected.TButton",
        }:
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


def _blend_hex(start: str, end: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    sr, sg, sb = int(start[1:3], 16), int(start[3:5], 16), int(start[5:7], 16)
    er, eg, eb = int(end[1:3], 16), int(end[3:5], 16), int(end[5:7], 16)
    return "#{:02x}{:02x}{:02x}".format(
        int(sr + (er - sr) * t + 0.5),
        int(sg + (eg - sg) * t + 0.5),
        int(sb + (eb - sb) * t + 0.5),
    )


def _sd_rounded_rect(px: float, py: float, width: int, height: int, radius: int) -> float:
    cx = px - width / 2
    cy = py - height / 2
    qx = abs(cx) - width / 2 + radius
    qy = abs(cy) - height / 2 + radius
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius


def _make_rounded_rect_image(
    root: tk.Misc,
    *,
    fill: str,
    border: str,
    corner: str,
    size: int = 32,
    radius: int = 8,
    border_width: float = 1.15,
    shadow: float = 0.0,
    inner_shadow: float = 0.0,
    shadow_spread: float = 4.5,
    shadow_offset_y: float = 1.3,
) -> tk.PhotoImage:
    image = tk.PhotoImage(master=root, width=size, height=size)
    shadow_pad = int(math.ceil(shadow_spread + abs(shadow_offset_y))) if shadow > 0 else 0
    inner_w = size - 2 * shadow_pad
    inner_h = size - 2 * shadow_pad
    slice_border = shadow_pad + radius
    shadow_ink = "#0F172A"
    for y in range(size):
        row: list[str] = []
        for x in range(size):
            if (
                inner_shadow <= 0.0
                and x >= slice_border
                and x < size - slice_border
                and y >= slice_border
                and y < size - slice_border
            ):
                row.append(fill)
                continue
            px = x + 0.5 - shadow_pad
            py = y + 0.5 - shadow_pad
            sd = _sd_rounded_rect(px, py, inner_w, inner_h, radius)
            sd_shadow = (
                _sd_rounded_rect(px, py - shadow_offset_y, inner_w, inner_h, radius)
                if shadow > 0
                else sd
            )
            behind = corner
            if shadow > 0 and sd_shadow < shadow_spread:
                falloff = max(0.0, 1.0 - max(sd_shadow, 0.0) / shadow_spread)
                behind = _blend_hex(corner, shadow_ink, shadow * falloff * falloff)
            coverage = max(0.0, min(1.0, 0.55 - sd))
            if coverage <= 0.0:
                row.append(behind)
                continue
            if sd > -border_width:
                color = _blend_hex(corner, border, coverage)
            else:
                color = fill
            if coverage < 1.0:
                color = _blend_hex(behind, color, coverage)
            if inner_shadow > 0.0 and sd < 0.0:
                inset_factor = max(0.0, min(1.0, 1.0 + sd / 2.8))
                top_factor = max(0.0, min(1.0, 1.0 - (py - radius) / 2.8))
                left_factor = max(0.0, min(1.0, 1.0 - (px - radius) / 2.8))
                edge_factor = inset_factor * max(top_factor, left_factor)
                color = _blend_hex(color, shadow_ink, inner_shadow * edge_factor)
            row.append(color)
        image.put("{" + " ".join(row) + "}", to=(0, y))
    return image


def _make_rounded_button_image(
    root: tk.Misc,
    *,
    fill: str,
    border: str,
    corner: str,
    size: int = 32,
    radius: int = 8,
    border_width: float = 1.15,
    shadow: float = 0.0,
    inner_shadow: float = 0.0,
) -> tk.PhotoImage:
    return _make_rounded_rect_image(
        root,
        fill=fill,
        border=border,
        corner=corner,
        size=size,
        radius=radius,
        border_width=border_width,
        shadow=shadow,
        inner_shadow=inner_shadow,
    )


def _button_image_layout(element_name: str) -> list[tuple[str, dict]]:
    return [
        (
            "Button.border",
            {
                "sticky": "nswe",
                "children": [
                    (
                        element_name,
                        {
                            "sticky": "nswe",
                            "children": [
                                (
                                    "Button.padding",
                                    {
                                        "sticky": "nswe",
                                        "children": [("Button.label", {"sticky": "nswe"})],
                                    },
                                )
                            ],
                        },
                    )
                ],
            },
        )
    ]


def _configure_rounded_button_styles(
    root: tk.Misc,
    style: ttk.Style,
    theme: str,
    colors: dict[str, str],
    selected_center: str,
    selected_edge: str,
    scale: float,
    body_size: int,
) -> None:
    # The image border also contributes to the button's content inset.
    # With a 12 pt label, these insets give ~115 x 43 px (formerly 143 x 55).
    radius = 8
    # Keep the rounded corner and inner shadow out of the repeating edge strips.
    slice_border = radius + 3
    image_store = getattr(root, "_rounded_button_images", {})

    def make_set(
        surface: str,
        *,
        selected_fill: str | None = None,
        selected_border: str | None = None,
    ) -> dict[str, tk.PhotoImage]:
        fill = selected_center if selected_fill is None else selected_fill
        edge = selected_edge if selected_border is None else selected_border
        return {
            "normal": _make_rounded_button_image(
                root,
                fill=surface,
                border=colors["border"],
                corner=surface,
                size=32,
                radius=radius,
                shadow=0.0,
            ),
            "pressed": _make_rounded_button_image(
                root,
                fill=fill,
                border=edge,
                corner=surface,
                size=32,
                radius=radius,
                shadow=0.0,
                inner_shadow=0.72,
            ),
            "disabled": _make_rounded_button_image(
                root,
                fill=surface,
                border=colors["muted"],
                corner=surface,
                size=32,
                radius=radius,
                shadow=0.0,
            ),
        }

    def make_mode_set(fill: str, accent: str) -> dict[str, tk.PhotoImage]:
        corner = colors["panel_alt"]
        return {
            "normal": _make_rounded_button_image(
                root,
                fill=fill,
                border=accent,
                corner=corner,
                size=32,
                radius=radius,
                shadow=0.0,
            ),
            "pressed": _make_rounded_button_image(
                root,
                fill=accent,
                border=accent,
                corner=corner,
                size=32,
                radius=radius,
                shadow=0.0,
                inner_shadow=0.72,
            ),
            "disabled": _make_rounded_button_image(
                root,
                fill=fill,
                border=colors["muted"],
                corner=corner,
                size=32,
                radius=radius,
                shadow=0.0,
            ),
        }

    sidebar_selected_fill = selected_center if theme != "dark" else "#2871FA"
    sidebar_selected_edge = selected_edge if theme != "dark" else "#173B70"
    sidebar_selected_text = "#FFFFFF" if theme != "dark" else "#05070D"
    pressed_accent = sidebar_selected_fill if theme == "dark" else colors["mode_selected"]
    if theme not in image_store or "panel" not in image_store[theme]:
        image_store[theme] = {
            "panel": make_set(colors["panel"]),
            "bg": make_set(colors["bg"]),
            "sidebar": make_set(
                colors["panel_alt"],
                selected_fill=sidebar_selected_fill,
                selected_border=sidebar_selected_edge,
            ),
        }
        setattr(root, "_rounded_button_images", image_store)

    store = image_store[theme]
    def _mix(a: str, b: str, t: float) -> str:
        ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb))

    # selected chip parent: a soft tint of the selected-mode blue
    parent_fill = _mix(colors["panel_alt"], sidebar_selected_fill, 0.16 if theme != "dark" else 0.22)
    parent_edge = _mix(colors["panel_alt"], sidebar_selected_fill, 0.45)
    parent_text = _mix(colors["text"], sidebar_selected_fill, 0.55 if theme != "dark" else 0.25)
    if "sidebar_parent" not in store:
        tint = _make_rounded_button_image(
            root, fill=parent_fill, border=parent_edge, corner=colors["panel_alt"],
            size=32, radius=radius, shadow=0.0,
        )
        store["sidebar_parent"] = {"normal": tint, "pressed": tint, "disabled": tint}
    if "sidebar_mode" not in store:
        store["sidebar_mode"] = make_mode_set(colors["mode_idle"], pressed_accent)
        selected_image = store["sidebar"]["pressed"]
        if theme == "dark":
            store["sidebar_mode"]["pressed"] = selected_image
        store["sidebar_mode_selected"] = {
            "normal": selected_image,
            "pressed": selected_image,
            "disabled": _make_rounded_button_image(
                root,
                fill=colors["mode_idle"],
                border=colors["muted"],
                corner=colors["panel_alt"],
                size=32,
                radius=radius,
                shadow=0.0,
            ),
        }

    panel_images = store["panel"]
    bg_images = store["bg"]
    sidebar_images = store["sidebar"]
    sidebar_mode_images = store["sidebar_mode"]
    sidebar_mode_selected_images = store["sidebar_mode_selected"]
    normal_element = f"{theme}.Rounded.Button.button"
    selected_element = f"{theme}.Selected.Button.button"
    bg_element = f"{theme}.Bg.Button.button"
    bg_selected_element = f"{theme}.BgSelected.Button.button"
    sidebar_element = f"{theme}.Sidebar.Button.button"
    sidebar_selected_element = f"{theme}.SidebarSelected.Button.button"
    sidebar_mode_element = f"{theme}.SidebarMode.Button.button"
    sidebar_mode_selected_element = f"{theme}.SidebarModeSelected.Button.button"
    sidebar_parent_element = f"{theme}.SidebarParentSelected.Button.button"

    def ensure_element(name: str, images: dict[str, tk.PhotoImage], *, selected: bool) -> None:
        if name in style.element_names():
            return
        if selected:
            style.element_create(
                name,
                "image",
                images["pressed"],
                ("disabled", images["disabled"]),
                ("pressed", images["pressed"]),
                ("active", images["pressed"]),
                border=slice_border,
                padding=radius,
                sticky="nswe",
            )
            return
        style.element_create(
            name,
            "image",
            images["normal"],
            ("disabled", images["disabled"]),
            ("pressed", images["pressed"]),
            ("active", images["normal"]),
            border=slice_border,
            padding=radius,
            sticky="nswe",
        )

    ensure_element(normal_element, panel_images, selected=False)
    ensure_element(selected_element, panel_images, selected=True)
    ensure_element(bg_element, bg_images, selected=False)
    ensure_element(bg_selected_element, bg_images, selected=True)
    ensure_element(sidebar_element, sidebar_images, selected=False)
    ensure_element(sidebar_selected_element, sidebar_images, selected=True)
    ensure_element(sidebar_mode_element, sidebar_mode_images, selected=False)
    ensure_element(sidebar_mode_selected_element, sidebar_mode_selected_images, selected=True)
    ensure_element(sidebar_parent_element, store["sidebar_parent"], selected=True)

    pad = (0, _scaled(3, scale))
    font = ("Microsoft YaHei UI", body_size)

    def configure_plain(button_style: str, element: str, surface: str, foreground: str) -> None:
        style.layout(button_style, _button_image_layout(element))
        style.configure(
            button_style,
            background=surface,
            foreground=foreground,
            borderwidth=0,
            relief="flat",
            focusthickness=0,
            focuscolor=surface,
            padding=pad,
            font=font,
            anchor="center",
        )

        style.map(
            button_style,
            background=[
                ("disabled", surface),
                ("pressed", selected_center),
                ("active", surface),
            ],
            foreground=[("disabled", colors["muted"]), ("pressed", "#FFFFFF")],
            bordercolor=[
                ("disabled", colors["muted"]),
                ("pressed", selected_edge),
                ("active", colors["border"]),
            ],
            lightcolor=[
                ("disabled", colors["muted"]),
                ("pressed", selected_edge),
                ("active", colors["border"]),
            ],
            darkcolor=[
                ("disabled", colors["muted"]),
                ("pressed", selected_edge),
                ("active", colors["border"]),
            ],
            relief=[("disabled", "flat"), ("pressed", "flat"), ("active", "flat")],
        )

    for button_style in ("TButton", "Primary.TButton", "Secondary.TButton"):
        configure_plain(button_style, normal_element, colors["panel"], colors["text"])
    quiet_element = f"{theme}.Quiet.Button.button"
    if quiet_element not in style.element_names():
        style.element_create(
            quiet_element,
            "image",
            panel_images["normal"],
            ("disabled", panel_images["disabled"]),
            ("pressed", panel_images["normal"]),
            ("active", panel_images["normal"]),
            border=slice_border,
            padding=radius,
            sticky="nswe",
        )
    configure_plain("Quiet.TButton", quiet_element, colors["panel"], colors["text"])
    style.map(
        "Quiet.TButton",
        background=[
            ("disabled", colors["panel"]),
            ("pressed", colors["panel"]),
            ("active", colors["panel"]),
        ],
        foreground=[
            ("disabled", colors["muted"]),
            ("pressed", colors["text"]),
            ("active", colors["text"]),
        ],
        bordercolor=[
            ("disabled", colors["muted"]),
            ("pressed", colors["border"]),
            ("active", colors["border"]),
        ],
        lightcolor=[
            ("disabled", colors["muted"]),
            ("pressed", colors["border"]),
            ("active", colors["border"]),
        ],
        darkcolor=[
            ("disabled", colors["muted"]),
            ("pressed", colors["border"]),
            ("active", colors["border"]),
        ],
        relief=[("disabled", "flat"), ("pressed", "flat"), ("active", "flat")],
    )

    configure_plain("Danger.TButton", normal_element, colors["panel"], colors["danger"])

    for button_style in ("Selected.TButton", "Flash.TButton"):
        configure_plain(button_style, selected_element, colors["panel"], "#FFFFFF")
        style.map(
            button_style,
            background=[("disabled", colors["panel"]), ("active", selected_center), ("pressed", selected_center)],
            foreground=[("disabled", "#D1D5DB")],
            bordercolor=[("disabled", colors["muted"]), ("active", selected_edge), ("pressed", selected_edge)],
            lightcolor=[("disabled", colors["muted"]), ("active", selected_edge), ("pressed", selected_edge)],
            darkcolor=[("disabled", colors["muted"]), ("active", selected_edge), ("pressed", selected_edge)],
            relief=[("disabled", "flat"), ("!disabled", "flat")],
        )

    configure_plain("Bg.TButton", bg_element, colors["bg"], colors["text"])
    configure_plain("BgSelected.TButton", bg_selected_element, colors["bg"], "#FFFFFF")
    style.map(
        "BgSelected.TButton",
        background=[("disabled", colors["bg"]), ("active", selected_center), ("pressed", selected_center)],
        foreground=[("disabled", "#D1D5DB")],
        bordercolor=[("disabled", colors["muted"]), ("active", selected_edge), ("pressed", selected_edge)],
        lightcolor=[("disabled", colors["muted"]), ("active", selected_edge), ("pressed", selected_edge)],
        darkcolor=[("disabled", colors["muted"]), ("active", selected_edge), ("pressed", selected_edge)],
        relief=[("disabled", "flat"), ("!disabled", "flat")],
    )

    configure_plain("Sidebar.TButton", sidebar_element, colors["panel_alt"], colors["text"])
    if theme == "dark":
        style.map(
            "Sidebar.TButton",
            foreground=[("disabled", colors["muted"]), ("pressed", sidebar_selected_text)],
            background=[
                ("disabled", colors["panel_alt"]),
                ("pressed", sidebar_selected_fill),
                ("active", colors["panel_alt"]),
            ],
            bordercolor=[
                ("disabled", colors["muted"]),
                ("pressed", sidebar_selected_edge),
                ("active", colors["border"]),
            ],
            lightcolor=[
                ("disabled", colors["muted"]),
                ("pressed", sidebar_selected_edge),
                ("active", colors["border"]),
            ],
            darkcolor=[
                ("disabled", colors["muted"]),
                ("pressed", sidebar_selected_edge),
                ("active", colors["border"]),
            ],
        )
    configure_plain("SidebarSelected.TButton", sidebar_selected_element, colors["panel_alt"], sidebar_selected_text)
    style.map(
        "SidebarSelected.TButton",
        background=[("disabled", colors["panel_alt"]), ("active", sidebar_selected_fill), ("pressed", sidebar_selected_fill)],
        foreground=[("disabled", "#D1D5DB"), ("pressed", sidebar_selected_text), ("active", sidebar_selected_text)],
        bordercolor=[("disabled", colors["muted"]), ("active", sidebar_selected_edge), ("pressed", sidebar_selected_edge)],
        lightcolor=[("disabled", colors["muted"]), ("active", sidebar_selected_edge), ("pressed", sidebar_selected_edge)],
        darkcolor=[("disabled", colors["muted"]), ("active", sidebar_selected_edge), ("pressed", sidebar_selected_edge)],
        relief=[("disabled", "flat"), ("!disabled", "flat")],
    )
    # Sidebar tree: unselected page-mode children look exactly like the
    # unselected sidebar entries (Sidebar.TButton element and state map).
    configure_plain("SidebarMode.TButton", sidebar_element, colors["panel_alt"], colors["text"])
    for option, spec in style.map("Sidebar.TButton").items():
        style.map("SidebarMode.TButton", **{option: spec})
    configure_plain("SidebarParentSelected.TButton", sidebar_parent_element, colors["panel_alt"], parent_text)
    style.map(
        "SidebarParentSelected.TButton",
        background=[("disabled", colors["panel_alt"]), ("active", parent_fill), ("pressed", parent_fill)],
        foreground=[("disabled", colors["muted"]), ("pressed", parent_text), ("active", parent_text)],
        relief=[("disabled", "flat"), ("!disabled", "flat")],
    )
    configure_plain("SidebarModeSelected.TButton", sidebar_mode_selected_element, colors["panel_alt"], sidebar_selected_text)
    style.map(
        "SidebarModeSelected.TButton",
        background=[
            ("disabled", colors["mode_idle"]),
            ("active", sidebar_selected_fill),
            ("pressed", sidebar_selected_fill),
        ],
        foreground=[("disabled", "#D1D5DB"), ("pressed", sidebar_selected_text), ("active", sidebar_selected_text)],
        bordercolor=[
            ("disabled", colors["muted"]),
            ("active", sidebar_selected_edge),
            ("pressed", sidebar_selected_edge),
        ],
        lightcolor=[
            ("disabled", colors["muted"]),
            ("active", sidebar_selected_edge),
            ("pressed", sidebar_selected_edge),
        ],
        darkcolor=[
            ("disabled", colors["muted"]),
            ("active", sidebar_selected_edge),
            ("pressed", sidebar_selected_edge),
        ],
        relief=[("disabled", "flat"), ("!disabled", "flat")],
    )


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
    try:
        root.winfo_toplevel()._piezo_theme = theme  # type: ignore[attr-defined]
    except Exception:
        pass
    title_weight = page_title_weight(root)
    selected_bg = colors["primary_hover"]
    selected_edge = "#1E40AF" if theme != "dark" else "#1E3A8A"
    sidebar_selected_fill = selected_bg if theme != "dark" else "#2871FA"
    sidebar_selected_edge = selected_edge if theme != "dark" else "#173B70"
    sidebar_selected_text = "#FFFFFF" if theme != "dark" else "#05070D"
    scale = max(0.75, min(1.35, window_scale))
    base_font_size = max(8, min(18, int(font_size)))
    body_size = max(8, min(24, _scaled(base_font_size, scale)))
    title_size = max(14, min(34, _scaled(base_font_size + 8, scale)))
    prominent_size = min(36, title_size + 1)
    subtitle_size = max(8, min(22, _scaled(base_font_size, scale)))
    sidebar_title_size = max(12, min(28, _scaled(base_font_size + 6, scale)))
    small_size = max(7, min(20, _scaled(base_font_size - 1, scale)))
    mono_size = max(7, min(20, _scaled(9, scale * (base_font_size / 10))))

    setattr(root, "_piezo_font_family", "Microsoft YaHei UI")
    setattr(root, "_piezo_body_font_size", body_size)
    setattr(root, "_piezo_title_font_size", title_size)
    setattr(root, "_piezo_prominent_title_font_size", prominent_size)
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
        "SidebarTitle.TLabel",
        background=colors["panel_alt"],
        foreground=colors["text"],
        font=("Microsoft YaHei UI", sidebar_title_size, "bold"),
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
        font=("Microsoft YaHei UI", title_size, title_weight),
    )
    style.configure(
        "Prominent.TLabel",
        background=colors["bg"],
        foreground=colors["text"],
        font=("Microsoft YaHei UI", prominent_size, title_weight),
    )
    chip_title_size = int(getattr(root, "_piezo_chip_title_font_size", 0) or max(prominent_size, 28))
    style.configure(
        "ChipTitle.TLabel",
        background=colors["bg"],
        foreground=colors["text"],
        font=("Microsoft YaHei UI", chip_title_size, title_weight),
    )
    style.configure(
        "Wordmark.TLabel",
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

    try:
        _configure_rounded_button_styles(
            root,
            style,
            theme,
            colors,
            selected_bg,
            selected_edge,
            scale,
            body_size,
        )
    except tk.TclError:
        for button_style, surface, fg in (
            ("TButton", colors["panel"], colors["text"]),
            ("Primary.TButton", colors["panel"], colors["text"]),
            ("Secondary.TButton", colors["panel"], colors["text"]),
            ("Quiet.TButton", colors["panel"], colors["text"]),
            ("Danger.TButton", colors["panel"], colors["danger"]),
            ("Bg.TButton", colors["bg"], colors["text"]),
            ("Sidebar.TButton", colors["panel_alt"], colors["text"]),
            ("SidebarMode.TButton", colors["mode_idle"], "#FFFFFF"),
        ):
            style.configure(
                button_style,
                background=surface,
                foreground=fg,
                borderwidth=1,
                bordercolor=colors["border"],
                lightcolor=colors["border"],
                darkcolor=colors["border"],
                relief="flat",
                focusthickness=0,
                padding=(_scaled(7, scale), _scaled(10, scale)),
                font=("Microsoft YaHei UI", body_size),
            )
            if button_style == "Quiet.TButton":
                pressed_fill = surface
                pressed_text = fg
            elif theme == "dark" and button_style in {"Sidebar.TButton", "SidebarMode.TButton"}:
                pressed_fill = sidebar_selected_fill
                pressed_text = sidebar_selected_text
            else:
                pressed_fill = selected_bg
                pressed_text = "#FFFFFF"
            pressed_edge = colors["border"] if button_style == "Quiet.TButton" else (
                sidebar_selected_edge if pressed_fill == sidebar_selected_fill else selected_edge
            )
            style.map(
                button_style,
                background=[("disabled", surface), ("pressed", pressed_fill), ("active", surface)],
                foreground=[("disabled", colors["muted"]), ("pressed", pressed_text)],
                bordercolor=[("pressed", pressed_edge), ("active", colors["border"])],
                lightcolor=[("pressed", pressed_edge), ("active", colors["border"])],
                darkcolor=[("pressed", pressed_edge), ("active", colors["border"])],
                relief=[("disabled", "flat"), ("pressed", "flat" if button_style == "Quiet.TButton" else "sunken"), ("active", "flat")],
            )
        for button_style, surface, fill, edge in (
            ("Selected.TButton", colors["panel"], selected_bg, selected_edge),
            ("Flash.TButton", colors["panel"], selected_bg, selected_edge),
            ("BgSelected.TButton", colors["bg"], selected_bg, selected_edge),
            ("SidebarSelected.TButton", colors["panel_alt"], sidebar_selected_fill, sidebar_selected_edge),
            ("SidebarModeSelected.TButton", colors["mode_idle"], sidebar_selected_fill, sidebar_selected_edge),
        ):
            style.configure(
                button_style,
                background=fill,
                foreground=sidebar_selected_text if button_style in {"SidebarSelected.TButton", "SidebarModeSelected.TButton"} else "#FFFFFF",
                borderwidth=1,
                bordercolor=edge,
                lightcolor=edge,
                darkcolor=edge,
                relief="sunken",
                focusthickness=0,
                padding=(_scaled(7, scale), _scaled(10, scale)),
                font=("Microsoft YaHei UI", body_size),
            )
            style.map(
                button_style,
                background=[("disabled", surface), ("active", fill), ("pressed", fill)],
                foreground=[("disabled", "#D1D5DB")],
                bordercolor=[("disabled", colors["muted"]), ("active", edge), ("pressed", edge)],
                lightcolor=[("disabled", colors["muted"]), ("active", edge), ("pressed", edge)],
                darkcolor=[("disabled", colors["muted"]), ("active", edge), ("pressed", edge)],
                relief=[("disabled", "flat"), ("!disabled", "sunken")],
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

    return colors
