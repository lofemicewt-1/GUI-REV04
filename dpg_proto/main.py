"""Dear PyGui (MIT) prototype of the Piezo Control GUI: Home + AWP1221 Button Mode only.

UI only: no serial, no backend. Values are stubs; the waveform preview uses the repo's
core.waveform_engine read-only when importable. Run:  py -3 dpg_proto/main.py
Env: PIEZO_DPG_SCALE=1.5 (force DPI scale), PIEZO_DPG_UI_K=1.3 (size factor vs Tk), PIEZO_DPG_FONT=noto (skip Microsoft YaHei).
"""
from __future__ import annotations

import math
import os
import sys
import time

T_START = time.perf_counter()
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _dpi_scale() -> float:
    env = os.environ.get("PIEZO_DPG_SCALE")
    if env:
        return max(0.75, float(env))
    if sys.platform != "win32":
        return 1.0
    import ctypes
    try:  # per-monitor v2 -> v1 -> system aware; must happen before the window exists
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
    except Exception:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
    return max(1.0, dpi / 96.0)


SCALE = _dpi_scale()
import dearpygui.dearpygui as dpg  # noqa: E402


# Tk sizes fonts in points (10 pt body); Dear PyGui sizes them by line height in pixels.
# UI_K = 1.3 makes text and widget metrics match the Tk build at the same DPI.
UI_K = float(os.environ.get("PIEZO_DPG_UI_K", "1.3"))


def S(v: float) -> int:
    return int(round(v * SCALE * UI_K))


# Same palette as styles/theme.py (light)
C = dict(bg="#F5F7FA", panel="#FFFFFF", panel_alt="#F9FAFB", text="#111827", muted="#6B7280",
         border="#D1D5DB", primary="#2563EB", primary_hover="#1D4ED8", sel="#1F4FD8", sel_edge="#1E40AF",
         mode_idle="#2871F3", mode_sel="#252DCE", danger="#DC2626", hover="#EEF2F7")
NAV = [("home", "首页"), ("awp1921_1923", "AWP1921/AWP1923"), ("awp1922", "AWP1922"),
       ("awp1018", "AWP1018"), ("awp1221", "AWP1221")]
VERSION = "0.42"


def rgb(h: str, a: int = 255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def _waveform(points: int = 512):
    """Preview samples from the repo's core layer (read-only); stub sine if unavailable."""
    try:
        if REPO not in sys.path:
            sys.path.insert(0, REPO)
        from core.waveform_engine import WaveformEngine
        return list(WaveformEngine().generate("sine", points, amplitude=1.5625).voltages), "core.waveform_engine"
    except Exception:
        return [1.5625 * math.sin(2 * math.pi * i / points) for i in range(points)], "stub"


class App:
    def __init__(self) -> None:
        dpg.create_context()
        self.fonts: dict[str, int] = {}
        self.font_source = self._load_fonts()
        self.it: dict[str, int] = {}
        self._themes()
        self.pages: dict[str, int] = {}
        self.current = ""
        self.mode = "button_mode"
        tex = self._logo()
        vw, vh = int(1300 * SCALE), int(800 * SCALE)
        with dpg.window(tag="root", no_scrollbar=True):
            with dpg.group(horizontal=True, horizontal_spacing=S(8)):
                with dpg.child_window(tag="side", width=S(200), border=False, no_scrollbar=True):
                    self._sidebar(tex)
                with dpg.child_window(tag="content", border=False, no_scrollbar=True):
                    self._home()
                    self._awp1221()
                    self._placeholder()
        dpg.bind_item_theme("side", self.it["side"])
        dpg.bind_item_theme("content", self.it["side"])
        dpg.create_viewport(title="Piezo Control (Dear PyGui prototype)", width=vw, height=vh,
                            min_width=int(800 * SCALE), min_height=int(500 * SCALE), clear_color=rgb(C["bg"]))
        ico = os.path.join(REPO, "assets", "app_icon.ico")
        if os.path.exists(ico):
            dpg.set_viewport_small_icon(ico); dpg.set_viewport_large_icon(ico)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("root", True)
        dpg.set_viewport_resize_callback(lambda: self._layout())
        self.show_page("home")

    # ---------------- fonts / themes ----------------
    def _load_fonts(self) -> str:
        win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        yahei, yahei_bd = os.path.join(win, "msyh.ttc"), os.path.join(win, "msyhbd.ttc")
        fdir = os.path.join(HERE, "fonts")
        reg, bold, src = os.path.join(fdir, "NotoSansSC-Regular-subset.otf"), os.path.join(fdir, "NotoSansSC-Bold-subset.otf"), "Noto Sans SC (bundled subset)"
        if os.environ.get("PIEZO_DPG_FONT", "").lower() != "noto" and os.path.exists(yahei):
            reg, src = yahei, "Microsoft YaHei"
            bold = yahei_bd if os.path.exists(yahei_bd) else yahei
        mono = os.path.join(REPO, "assets", "fonts", "JetBrainsMono-ExtraBold.ttf")
        with dpg.font_registry():
            # Dear PyGui 2.x bakes glyphs on demand at the final pixel size (size * DPI scale):
            # every CJK character is available and text is never bitmap-stretched.
            def add(path, size, _common=False):
                return dpg.add_font(path, S(size))
            self.fonts["body"] = add(reg, 15, True)
            self.fonts["small"] = add(reg, 13, False)
            self.fonts["h2"] = add(bold, 15, False)
            self.fonts["mode"] = add(bold, 22, False)
            self.fonts["page"] = add(bold, 26, False)
            self.fonts["chip"] = add(bold, 34, False)
            self.fonts["word"] = dpg.add_font(mono if os.path.exists(mono) else bold, S(34))
        dpg.bind_font(self.fonts["body"])
        return src

    def _themes(self) -> None:
        with dpg.theme() as th:
            with dpg.theme_component(dpg.mvAll):
                col, sty = dpg.add_theme_color, dpg.add_theme_style
                col(dpg.mvThemeCol_WindowBg, rgb(C["bg"])); col(dpg.mvThemeCol_ChildBg, rgb(C["panel"]))
                col(dpg.mvThemeCol_PopupBg, rgb(C["panel"])); col(dpg.mvThemeCol_Text, rgb(C["text"]))
                col(dpg.mvThemeCol_TextDisabled, rgb(C["muted"])); col(dpg.mvThemeCol_Border, rgb(C["border"]))
                col(dpg.mvThemeCol_FrameBg, rgb(C["panel"])); col(dpg.mvThemeCol_FrameBgHovered, rgb(C["panel"]))
                col(dpg.mvThemeCol_FrameBgActive, rgb(C["panel"])); col(dpg.mvThemeCol_Button, rgb(C["panel"]))
                col(dpg.mvThemeCol_ButtonHovered, rgb(C["hover"])); col(dpg.mvThemeCol_ButtonActive, rgb("#E0E7FF"))
                col(dpg.mvThemeCol_CheckMark, rgb(C["primary"])); col(dpg.mvThemeCol_Header, rgb("#DBEAFE"))
                col(dpg.mvThemeCol_HeaderHovered, rgb("#EFF6FF")); col(dpg.mvThemeCol_HeaderActive, rgb("#DBEAFE"))
                col(dpg.mvThemeCol_ScrollbarBg, rgb(C["panel_alt"])); col(dpg.mvThemeCol_ScrollbarGrab, rgb("#C4C9D2"))
                col(dpg.mvThemeCol_TableRowBg, (0, 0, 0, 0)); col(dpg.mvThemeCol_TextSelectedBg, rgb("#BFDBFE"))
                col(dpg.mvThemeCol_Separator, rgb(C["border"])); col(dpg.mvThemeCol_BorderShadow, (0, 0, 0, 0))
                sty(dpg.mvStyleVar_FrameRounding, S(5)); sty(dpg.mvStyleVar_ChildRounding, S(6))
                sty(dpg.mvStyleVar_FrameBorderSize, 1); sty(dpg.mvStyleVar_FramePadding, S(8), S(5))
                sty(dpg.mvStyleVar_ItemSpacing, S(8), S(8)); sty(dpg.mvStyleVar_CellPadding, S(4), S(3))
                sty(dpg.mvStyleVar_WindowPadding, S(10), S(8)); sty(dpg.mvStyleVar_ScrollbarSize, S(12))
                sty(dpg.mvStyleVar_ScrollbarRounding, S(6)); sty(dpg.mvStyleVar_GrabRounding, S(4))
                sty(dpg.mvStyleVar_PopupRounding, S(4)); sty(dpg.mvStyleVar_ButtonTextAlign, 0.5, 0.5)
            for comp in (dpg.mvCheckbox, dpg.mvRadioButton):
                with dpg.theme_component(comp):
                    dpg.add_theme_style(dpg.mvStyleVar_FramePadding, S(2), S(2))
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, S(3))
                    dpg.add_theme_style(dpg.mvStyleVar_ItemInnerSpacing, S(5), S(4))
            with dpg.theme_component(dpg.mvInputText, enabled_state=False):
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, rgb(C["panel_alt"]))
        dpg.bind_theme(th)

        def mk(colors=None, styles=None, comp=dpg.mvAll):
            with dpg.theme() as t:
                with dpg.theme_component(comp):
                    for k, v in (colors or {}).items():
                        dpg.add_theme_color(k, v)
                    for k, v in (styles or {}).items():
                        dpg.add_theme_style(k, *v)
            return t
        self.it["side"] = mk({dpg.mvThemeCol_ChildBg: rgb(C["bg"])}, {dpg.mvStyleVar_WindowPadding: (S(10), S(8))})
        self.it["muted"] = mk({dpg.mvThemeCol_Text: rgb(C["muted"])})
        self.it["danger"] = mk({dpg.mvThemeCol_Text: rgb(C["danger"])}, comp=dpg.mvButton)
        white = (255, 255, 255, 255)
        self.it["sel"] = mk({dpg.mvThemeCol_Button: rgb(C["sel"]), dpg.mvThemeCol_ButtonHovered: rgb(C["primary_hover"]),
                             dpg.mvThemeCol_ButtonActive: rgb(C["sel_edge"]), dpg.mvThemeCol_Text: white,
                             dpg.mvThemeCol_Border: rgb(C["sel_edge"])}, comp=dpg.mvButton)
        self.it["mode"] = mk({dpg.mvThemeCol_Button: rgb(C["mode_idle"]), dpg.mvThemeCol_ButtonHovered: rgb(C["primary_hover"]),
                              dpg.mvThemeCol_ButtonActive: rgb(C["mode_sel"]), dpg.mvThemeCol_Text: white,
                              dpg.mvThemeCol_Border: rgb(C["mode_idle"])}, comp=dpg.mvButton)
        self.it["mode_sel"] = mk({dpg.mvThemeCol_Button: rgb(C["mode_sel"]), dpg.mvThemeCol_ButtonHovered: rgb(C["mode_sel"]),
                                  dpg.mvThemeCol_ButtonActive: rgb(C["mode_sel"]), dpg.mvThemeCol_Text: white,
                                  dpg.mvThemeCol_Border: rgb(C["sel_edge"])}, comp=dpg.mvButton)
        self.it["stage_sel"] = self.it["sel"]
        self.it["card"] = mk({dpg.mvThemeCol_ChildBg: rgb(C["panel"]), dpg.mvThemeCol_Border: rgb("#E5E7EB")},
                             {dpg.mvStyleVar_ChildBorderSize: (1,)})

    def _logo(self):
        path = os.path.join(REPO, "assets", "logo_light.png")
        if not os.path.exists(path):
            return None
        w, h, _, data = dpg.load_image(path)
        with dpg.texture_registry():
            tex = dpg.add_static_texture(w, h, data)
        return tex, w, h

    # ---------------- widget helpers ----------------
    def lbl(self, text, parent=0, muted=False, font=None, **kw):
        t = dpg.add_text(text, parent=parent, **kw)
        if muted:
            dpg.bind_item_theme(t, self.it["muted"])
        if font:
            dpg.bind_item_font(t, self.fonts[font])
        return t

    def help(self, text, parent=0):
        g = dpg.add_group(horizontal=True, horizontal_spacing=S(4), parent=parent)
        self.lbl(text, g); self.lbl("?", g, muted=True)
        return g

    def btn(self, text, w=0, h=None, parent=0, theme=None, cb=None, user=None):
        b = dpg.add_button(label=text, width=S(w) if w > 0 else w, height=S(h or 30), parent=parent,
                           callback=cb or (lambda: None), user_data=user)
        if theme:
            dpg.bind_item_theme(b, self.it[theme])
        return b

    def combo(self, items, value, w, parent=0):
        return dpg.add_combo([str(i) for i in items], default_value=str(value), width=S(w) if w > 0 else w, parent=parent)

    def entry(self, value, w, parent=0, mono=False):
        return dpg.add_input_text(default_value=value, width=S(w) if w > 0 else w, parent=parent)

    def table(self, parent, widths):
        t = dpg.add_table(parent=parent, header_row=False, policy=dpg.mvTable_SizingFixedFit, pad_outerX=False,
                          borders_innerH=False, borders_outerH=False, borders_innerV=False, borders_outerV=False)
        for w in widths:
            if w:
                dpg.add_table_column(parent=t, width_fixed=True, init_width_or_weight=S(w))
            else:
                dpg.add_table_column(parent=t, width_stretch=True)
        return t

    def row(self, table, *cells):
        """cells: callables taking a parent (or None for empty)."""
        r = dpg.add_table_row(parent=table)
        for c in cells:
            g = dpg.add_group(parent=r)
            if c:
                c(g)
        return r

    def header(self, parent, title, subtitle=None, chip=False):
        tbl = self.table(parent, [0, 272])
        r = dpg.add_table_row(parent=tbl)
        left = dpg.add_group(parent=r)
        t = self.lbl(title, left, font="chip" if chip else "page")
        if chip:  # Tk ChipTitle is underlined
            dl = dpg.add_drawlist(width=S(196), height=S(4), parent=left)
            dpg.draw_line((0, S(1)), (S(196), S(1)), color=rgb(C["text"]), thickness=S(3), parent=dl)
        if subtitle:
            self.lbl(subtitle, left, muted=True, font="small")
        right = dpg.add_group(parent=r)
        self.lbl("PIEZO CONTROL", right, font="word")
        return tbl

    # ---------------- sidebar ----------------
    def _sidebar(self, tex) -> None:
        if tex:
            t, w, h = tex
            lw = S(180); dpg.add_image(t, width=lw, height=int(lw * h / w))
        else:
            self.lbl("Analogwin", font="page")
        dpg.add_spacer(height=S(4))
        self.nav_btns = {}
        for key, text in NAV:
            self.nav_btns[key] = self.btn(text, -1, 30, cb=lambda s, a, u: self.show_page(u), user=key)
            if key == "awp1221":
                self.mode_box = dpg.add_group(show=False)
                self.mode_btns = {
                    "button_mode": self.btn("Button Mode", -1, 30, parent=self.mode_box, cb=lambda s, a, u: self.show_mode(u), user="button_mode"),
                    "full_mode": self.btn("Full Mode", -1, 30, parent=self.mode_box, cb=lambda s, a, u: self.show_mode(u), user="full_mode"),
                }
        dpg.add_spacer(height=S(10))
        self.nav_btns["manual"] = self.btn("使用说明", -1, 30, cb=lambda s, a, u: self.show_page(u), user="manual")
        self.lbl(f"版本: {VERSION}", muted=True, font="small")
        self.gear_spacer = dpg.add_spacer(height=S(300))
        # Settings gear: a drawn button (the UI fonts have no ⚙ glyph)
        self.gear_dl = dpg.add_drawlist(width=S(180), height=S(38))
        self._draw_gear()
        with dpg.item_handler_registry() as hr:
            dpg.add_item_clicked_handler(callback=lambda: self.show_page("settings"))
        dpg.bind_item_handler_registry(self.gear_dl, hr)

    def _draw_gear(self, hover=False):
        dl = self.gear_dl
        dpg.delete_item(dl, children_only=True)
        fill = rgb(C["primary_hover"] if hover or self.current == "settings" else C["primary"])
        dpg.draw_rectangle((1, 1), (S(180) - 1, S(38) - 1), fill=fill, color=rgb(C["sel_edge"]), rounding=S(6), parent=dl)
        cx, cy, r = S(90), S(19), S(8)
        white = (255, 255, 255, 255)
        for i in range(8):
            a = i * math.pi / 4
            dpg.draw_line((cx + math.cos(a) * r * 0.6, cy + math.sin(a) * r * 0.6),
                          (cx + math.cos(a) * r * 1.25, cy + math.sin(a) * r * 1.25), color=white, thickness=S(4), parent=dl)
        dpg.draw_circle((cx, cy), r, fill=white, color=white, parent=dl)
        dpg.draw_circle((cx, cy), r * 0.42, fill=fill, color=fill, parent=dl)

    # ---------------- pages ----------------
    def _home(self) -> None:
        p = self.pages["home"] = dpg.add_group(show=False)
        self.header(p, "首页", "芯享程压电陶瓷系列控制界面")
        card = dpg.add_child_window(parent=p, border=True, height=-1)
        dpg.bind_item_theme(card, self.it["card"])
        self.lbl("连接通讯板", card, font="h2")
        self.lbl("状态: 未连接", card)
        self.lbl("未检测到串口，请确认通讯板已连接，或在设备管理器确认 COM 口。", card, muted=True)
        t = self.table(card, [90, 260, 70, 140, 100, 100])
        self.row(t, lambda g: self.lbl("串口", g), lambda g: self.combo([], "", 250, g), lambda g: self.lbl("波特率", g),
                 lambda g: self.combo([9600, 57600, 115200, 230400], 115200, 130, g),
                 lambda g: self.btn("刷新串口", 90, parent=g), lambda g: self.btn("连接", 90, parent=g))
        self.row(t, lambda g: self.lbl("当前串口名称:", g), lambda g: self.lbl("", g), None, None, None, None)
        self.row(t, lambda g: self.lbl("I2C 速度", g), lambda g: self.combo([100000, 400000], 400000, 130, g), None,
                 lambda g: self.btn("设置I2C速率", 92, parent=g), None, None)
        dpg.add_spacer(height=S(6), parent=card)
        t = self.table(card, [70, 110, 90, 110, 60, 110, 100, 0])
        self.row(t, lambda g: self.lbl("设备地址", g), lambda g: self.entry("0x44", 80, g), lambda g: self.lbl("寄存器地址", g),
                 lambda g: self.entry("0x00", 80, g), lambda g: self.lbl("写入值", g), lambda g: self.entry("0x0000", 80, g),
                 lambda g: self.lbl("I2C 数据位宽", g), lambda g: self.combo(["8-bit", "16-bit"], "16-bit", 80, g))
        g = dpg.add_group(horizontal=True, parent=card)
        self.lbl("读取值", g); dpg.add_spacer(width=S(14), parent=g); self.entry("", 100, g); dpg.add_spacer(width=S(90), parent=g)
        for s in ("读取", "写入", "连续读"):
            self.btn(s, 90, parent=g)

    def _placeholder(self) -> None:
        p = self.pages["placeholder"] = dpg.add_group(show=False)
        self.ph_header = self.header(p, "AWP")
        card = dpg.add_child_window(parent=p, border=True, height=-1)
        dpg.bind_item_theme(card, self.it["card"])
        dpg.add_spacer(height=S(40), parent=card)
        self.ph_text = self.lbl("", card, muted=True)

    def _awp1221(self) -> None:
        p = self.pages["awp1221"] = dpg.add_group(show=False)
        self.header(p, "AWP1221", chip=True)
        self.full_ph = dpg.add_group(parent=p, show=False)
        self.lbl("Full Mode 未包含在此 Dear PyGui 原型中（只做 Button Mode）。", self.full_ph, muted=True)
        self.bm = dpg.add_group(parent=p)
        self.scroll = dpg.add_child_window(parent=self.bm, border=True, height=-S(150), horizontal_scrollbar=True)
        dpg.bind_item_theme(self.scroll, self.it["card"])
        self._button_mode(self.scroll)
        self._bottom_bar(self.bm)

    def _button_mode(self, parent) -> None:
        top = dpg.add_group(horizontal=True, parent=parent)
        lg = dpg.add_group(parent=top)
        dpg.add_spacer(height=S(14), parent=lg)
        self.lbl("Button Mode", lg, font="mode")
        dpg.add_spacer(width=S(6), parent=top)
        grid = dpg.add_group(parent=top)
        r1 = dpg.add_group(horizontal=True, parent=grid)
        for s in ("软件预设 1", "软件预设 2", "软件预设 3", "导入页面设置", "导出页面设置"):
            self.btn(s, 160, 32, parent=r1)
        r2 = dpg.add_group(horizontal=True, parent=grid)
        g = dpg.add_group(horizontal=True, parent=r2, width=S(160))
        self.lbl("无界面设置", g); self.combo(range(4), 0, 58, g)
        for s in ("保存设置", "设为活动槽", "应用槽位", "读取并应用"):
            self.btn(s, 160, 32, parent=r2)

        cols = dpg.add_group(horizontal=True, parent=parent, horizontal_spacing=S(10))
        left = dpg.add_child_window(parent=cols, width=S(560), height=S(470), border=True)
        right = dpg.add_group(parent=cols)
        self._left_panel(left)
        self._right_panel(right)

    def _left_panel(self, L) -> None:
        self.lbl("芯片工作 / EN", L, font="h2")
        g = dpg.add_group(horizontal=True, parent=L)
        dpg.add_checkbox(label="芯片工作", parent=g); self.lbl("EN 状态未知", g, muted=True)
        self.lbl("ADC 阈值检测", L, font="h2")
        g = dpg.add_group(horizontal=True, parent=L)
        self.stage_btns = [self.btn("Stage0", 262, 32, parent=g, theme="sel", cb=lambda s, a, u: self._stage(u), user=0),
                           self.btn("Stage1", 262, 32, parent=g, cb=lambda s, a, u: self._stage(u), user=1)]
        t = self.table(L, [110, 175, 76, 0])
        self.row(t, lambda g: self.help("ADC OVV Time", g), lambda g: self.combo(["100 us", "200 us", "500 us", "1 ms"], "500 us", 165, g),
                 lambda g: self.lbl("跳转目标", g), lambda g: self.combo(["Stage0", "Stage1"], "Stage1", 150, g))

        def thr(g):
            h = dpg.add_group(horizontal=True, parent=g)
            self.entry("300", 100, h); self.lbl("0x12C", h, muted=True)
        self.row(t, lambda g: self.lbl("检测阈值", g), thr, lambda g: self.lbl("检测模式", g),
                 lambda g: dpg.add_radio_button(["按压模式", "松手模式"], default_value="按压模式", parent=g))
        self.row(t, lambda g: self.lbl("触发波形", g), lambda g: self.combo([f"slice{i}" for i in range(32)], "slice18", 165, g),
                 lambda g: self.lbl("重复次数", g), lambda g: self.combo(range(16), 0, 150, g))
        self.row(t, lambda g: dpg.add_checkbox(label="开启 Debounce", parent=g), None,
                 lambda g: self._chk_help("ADC 精确度", g, True), None)

        self.lbl("ADC 设置", L, font="h2")
        t = self.table(L, [140, 140, 120, 0])
        self.row(t, lambda g: self.help("Debounce 时间 (ms)", g), lambda g: self.entry("100", 130, g),
                 lambda g: self.lbl("ADC 采样更新时间", g), lambda g: self.combo(["1 us", "2 us", "4 us", "8 us"], "4 us", 130, g))
        self.row(t, lambda g: self.help("Zero Time", g), lambda g: self.combo(["0 ms", "1 ms", "5 ms"], "1 ms", 130, g),
                 lambda g: self.lbl("ADC_LDO", g), lambda g: self.combo(["1.8 V", "2.0 V", "2.2 V"], "1.8 V", 130, g))
        self.row(t, lambda g: self.help("按压退 bit 时间", g), lambda g: self.combo(["0", "1", "2"], "0", 130, g),
                 lambda g: self.help("松手退 bit 时间", g), lambda g: self.combo(["0", "1", "2"], "0", 130, g))
        g = dpg.add_group(horizontal=True, parent=L)
        self._chk_help("ZPS 开启", g); self._chk_help("ZPS 检测", g); self._chk_help("睡眠灵敏度", g)
        g = dpg.add_group(horizontal=True, parent=L)
        self.lbl("ADC 值", g)
        v = dpg.add_group(parent=g)
        self.entry("", 440, v)
        bits = dpg.add_group(horizontal=True, parent=v, horizontal_spacing=S(10))
        for i in range(15, -1, -1):
            self.lbl(f"{i}\n—", bits, muted=True, font="small")
        g = dpg.add_group(horizontal=True, parent=L)
        for s in ("读取当前值", "实时读取", "触发读取"):
            self.btn(s, 172, 32, parent=g)
        dpg.add_separator(parent=L)
        t = self.table(L, [0, 0])
        self.row(t, lambda g: self.btn("开始检测", -1, 32, parent=g), lambda g: self.btn("停止检测", -1, 32, parent=g))
        self.row(t, lambda g: self.btn("开始播放", -1, 32, parent=g), lambda g: self.btn("停止播放", -1, 32, parent=g))
        self.btn("关闭全部", -1, 32, parent=L)
        dpg.add_separator(parent=L)
        self.lbl("GPIO 设置", L, font="h2")
        for name in ("GPIO0 (D2)", "GPIO1 (D3)"):
            self.lbl(name, L)
            t = self.table(L, [100, 190, 64, 0])
            self.row(t, lambda g: self.lbl("芯片功能分类", g), lambda g: self.combo(["通用", "按键", "LED"], "通用", 180, g),
                     lambda g: self.lbl("芯片功能", g), lambda g: self.combo(["未配置"], "未配置", 170, g))
            g = dpg.add_group(horizontal=True, parent=L)
            self.btn("常高", 100, 30, parent=g); self.btn("常低", 100, 30, parent=g)
            g = dpg.add_group(horizontal=True, parent=L)
            self.lbl("芯片配置未确认", g, muted=True); dpg.add_spacer(width=S(20), parent=g); self.lbl("MCU 模式未确认", g, muted=True)
        g = dpg.add_group(horizontal=True, parent=L)
        self.btn("读取 GPIO 设置", 258, 32, parent=g); self.btn("应用 GPIO 设置", 258, 32, parent=g)

    def _chk_help(self, text, parent, value=False):
        g = dpg.add_group(horizontal=True, horizontal_spacing=S(4), parent=parent)
        dpg.add_checkbox(label=text, default_value=value, parent=g); self.lbl("?", g, muted=True)
        return g

    def _right_panel(self, R) -> None:
        self.lbl("波形选择器", R, font="h2")
        g = dpg.add_group(horizontal=True, parent=R)
        sel_col = dpg.add_group(parent=g)
        sel = dpg.add_child_window(parent=sel_col, width=S(270), height=S(232), border=True)
        dpg.add_checkbox(label="自动串联模式", default_value=True, parent=sel)
        t = self.table(sel, [92, 0])
        rows = [("波形", ["SINE", "SQUARE", "TRIANGLE", "DC", "SINC", "GAUSS"], "SINE"),
                ("写入 Slice", [f"slice{i}" for i in range(32)], "slice0"),
                ("波形来源", ["ROM", "SRAM"], "ROM"),
                ("精调幅度", ["00 - +1.5625 V"], "00 - +1.5625 V"),
                ("VPK", ["0x00 - 0.00 V"], "0x00 - 0.00 V")]
        for name, items, val in rows:
            self.row(t, lambda g, n=name: self.lbl(n, g), lambda g, i=items, v=val: self.combo(i, v, -1, g))
        self.row(t, lambda g: self.lbl("波形峰峰值 (Vpp)", g), lambda g: self.lbl("1.5625 V", g))
        for name, items, val in [("波形深度", [256, 512, 1024], 512), ("播放内容（完整或一半）", ["完整", "一半"], "完整"),
                                 ("相位", ["0°", "90°", "180°", "270°"], "0°"), ("偏置电压", ["0.00 V"], "0.00 V")]:
            self.row(t, lambda g, n=name: self.lbl(n, g, wrap=S(88)), lambda g, i=items, v=val: self.combo(i, v, -1, g))
        self.row(t, lambda g: self.lbl("单点时长(us)", g), lambda g: self.entry("1", -1, g))
        self.row(t, lambda g: self.lbl("播放次数", g), lambda g: self.combo(range(1, 17), 1, -1, g))
        dpg.add_checkbox(label="无限循环", parent=sel)
        self.lbl("偏置步进为 100/128 V（0.78125 V）；全 0 为无偏置。", sel, muted=True, wrap=S(240), font="small")
        self.btn("写入波形", 270, 32, parent=sel_col)

        plot_col = dpg.add_group(parent=g)
        self.lbl("有效点数: 512 点    周期: 512 us", plot_col)
        self.lbl("当前波形的频率为 1953.125 Hz", plot_col, muted=True)
        self._plot(plot_col, 210, 196)

        dpg.add_spacer(height=S(2), parent=R)
        g = dpg.add_group(horizontal=True, parent=R)
        self.lbl("通信日志", g, font="h2")
        self.btn("清除通信日志", 110, 30, parent=g); self.btn("导出通信日志", 110, 30, parent=g)
        dpg.add_input_text(multiline=True, readonly=True, width=S(500), height=S(118), parent=R,
                           default_value="[10:52:01] 原型：未连接通讯板（UI only，无串口）\n")

    def _plot(self, parent, w, h) -> None:
        vals, self.wave_src = _waveform(512)
        w, h = S(w), S(h)
        with dpg.drawlist(width=w, height=h, parent=parent):
            dpg.draw_rectangle((0, 0), (w - 1, h - 1), fill=rgb(C["panel"]), color=rgb(C["border"]))
            for i in range(1, 8):
                dpg.draw_line((i * w / 8, 0), (i * w / 8, h), color=rgb("#EEF0F3"))
                dpg.draw_line((0, i * h / 8), (w, i * h / 8), color=rgb("#EEF0F3"))
            dpg.draw_line((0, h / 2), (w, h / 2), color=rgb(C["border"]))
            peak = max(1e-9, max(abs(v) for v in vals))
            step = max(1, len(vals) // w)
            pts = [(i / (len(vals) - 1) * (w - 2) + 1, h / 2 - (vals[i] / peak) * h * 0.42) for i in range(0, len(vals), step)]
            dpg.draw_polyline(pts, color=rgb(C["primary"]), thickness=max(1.5, S(2)))

    def _bottom_bar(self, parent) -> None:
        bar = dpg.add_child_window(parent=parent, border=True, height=-1, no_scrollbar=True)
        dpg.bind_item_theme(bar, self.it["card"])
        t = self.table(bar, [0, 0, 0, 0])
        r = dpg.add_table_row(parent=t)
        for s in ("重置芯片", "进入睡眠模式", "进入关断模式", "进入默认状态"):
            self.btn(s, -1, 32, parent=dpg.add_group(parent=r), theme="danger" if s == "进入关断模式" else None)
        g = dpg.add_group(horizontal=True, parent=bar)
        self.lbl("寄存器读写", g, muted=True); self.lbl("寄存器地址", g); self.entry("0x00", 72, g)
        self.lbl("写入值", g); self.entry("0x0000", 72, g); self.lbl("读取值", g); self.entry("", 92, g)
        self.btn("读取", 80, 30, parent=g); self.btn("写入", 80, 30, parent=g)
        g = dpg.add_group(horizontal=True, parent=bar)
        self.lbl("通信板", g); self.combo([], "", 170, g); self.lbl("芯片地址", g)
        self.combo(["0x50", "0x51", "0x52", "0x53"], "0x50", 78, g); self.btn("检测芯片", 90, 30, parent=g)

    def _stage(self, idx) -> None:
        for i, b in enumerate(self.stage_btns):
            dpg.bind_item_theme(b, self.it["sel"] if i == idx else 0)

    # ---------------- navigation ----------------
    def show_page(self, key: str) -> None:
        target = key if key in ("home", "awp1221") else "placeholder"
        if key not in ("home", "awp1221"):
            names = dict(NAV, manual="使用说明", settings="设置")
            dpg.set_value(self.ph_text, f"{names.get(key, key)} 页面未包含在此 Dear PyGui 原型中（只做首页和 AWP1221 Button Mode）。")
            title = dpg.get_item_children(dpg.get_item_children(dpg.get_item_children(self.ph_header, 1)[0], 1)[0], 1)[0]
            dpg.set_value(title, names.get(key, key))
        for k, g in self.pages.items():
            dpg.configure_item(g, show=(k == target))
        self.current = key
        for k, b in self.nav_btns.items():
            dpg.bind_item_theme(b, self.it["sel"] if k == key else 0)
        dpg.configure_item(self.mode_box, show=(key == "awp1221"))
        self._sync_modes()
        self._layout()

    def show_mode(self, mode: str) -> None:
        self.mode = mode
        dpg.configure_item(self.bm, show=(mode == "button_mode"))
        dpg.configure_item(self.full_ph, show=(mode != "button_mode"))
        self._sync_modes()

    def _sync_modes(self) -> None:
        for m, b in self.mode_btns.items():
            dpg.bind_item_theme(b, self.it["mode_sel"] if m == self.mode else self.it["mode"])

    def _layout(self) -> None:
        """Pin the gear button to the sidebar bottom."""
        vh = dpg.get_viewport_client_height()
        used = S(48) + 7 * (S(30) + S(8)) + S(10) + S(30) + S(30) + (2 * (S(30) + S(8)) if self.current == "awp1221" else 0)
        dpg.configure_item(self.gear_spacer, height=max(S(10), vh - used - S(38) - S(30)))
        self._draw_gear()


def main() -> None:
    app = App()
    dpg.render_dearpygui_frame(); dpg.render_dearpygui_frame()
    app._layout()
    print(f"[dpg_proto] ready in {(time.perf_counter() - T_START) * 1000:.0f} ms, scale {SCALE:.2f}, font {app.font_source}, "
          f"wave {app.wave_src}", flush=True)
    while dpg.is_dearpygui_running():
        dpg.render_dearpygui_frame()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
