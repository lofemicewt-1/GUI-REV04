"""Offline real-page checks for every visible Button Mode question mark."""
from pathlib import Path
import json
import sys
from tkinter import ttk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.validate_responsive_layout import mocked_application, _flush, _capture_window_bmp


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def settle(root):
    _flush(root, 6)
    # Scroll presentation commits on a timed frame; let that frame finish
    # before opening help (scrolling deliberately hides existing tooltips).
    root.after(100, root.quit)
    root.mainloop()
    _flush(root, 6)


def help_widgets(page, root):
    seen = set()
    for tab in ("sinc", "rom", "sram"):
        page._select_button_wave_tab(tab)
        settle(root)
        for widget in descendants(page):
            if (isinstance(widget, (ttk.Label, ttk.Button)) and str(widget.cget("text")) == "?"
                    and widget.winfo_ismapped() and str(widget) not in seen):
                seen.add(str(widget))
                yield widget


def main():
    out = ROOT / "outputs/adc_help_layout_20261021"
    out.mkdir(parents=True, exist_ok=True)
    records, errors = [], []
    with mocked_application() as app:
        app.set_animations(False)
        root = app.root
        root.report_callback_exception = lambda *args: errors.append(str(args))
        app.navigation.show("awp1221")
        host = app.navigation.pages["awp1221"]
        host.show_mode("button_mode")
        page = host.mode_pages["button_mode"]
        area = host.mode_scroll
        for theme, language, size, font_size, scale in (
            ("light", "zh", "1700x980", 10, 1.5),
            ("light", "zh", "1450x820", 10, 1.5),
            ("dark", "zh", "1280x720", 10, 2.0),
            ("light", "en", "1700x980", 10, 1.5),
            ("light", "zh", "1280x720", 14, 1.5),
            ("light", "zh", "640x480", 10, 1.5),
        ):
            root.tk.call("tk", "scaling", scale)
            app.state.theme, app.state.language, app.state.font_size = theme, language, font_size
            app._apply_visual_settings()
            host.refresh_language()
            root.geometry(size)
            settle(root)
            checked = 0
            for widget in help_widgets(page, root):
                wave_area = page._button_waveform_scroll
                ancestor = widget.master
                while ancestor is not None and ancestor is not wave_area.content:
                    ancestor = ancestor.master
                if ancestor is wave_area.content:
                    region = [float(value) for value in wave_area.canvas.cget("scrollregion").split()]
                    offset_x = widget.winfo_rootx() - wave_area.content.winfo_rootx() - 20
                    wave_area.canvas.xview_moveto(max(0, offset_x) / (region[2] - region[0]))
                    settle(root)
                # Include all ancestors: the original button itself retained
                # its natural size while its fixed-size parent clipped it.
                anchor = widget
                while anchor is not area.content and anchor.master is not None:
                    parent = anchor.master
                    if anchor.winfo_manager() == "grid":
                        assert anchor.winfo_x() >= 0 and anchor.winfo_y() >= 0
                        assert widget.winfo_rootx() + widget.winfo_width() <= parent.winfo_rootx() + parent.winfo_width(), (size, str(widget), "horizontal clip", str(anchor), anchor.winfo_x(), anchor.winfo_width(), parent.winfo_width(), [(str(c), str(c.cget("text"))) for c in anchor.winfo_children() if isinstance(c, ttk.Label)])
                        assert anchor.winfo_y() + anchor.winfo_height() <= parent.winfo_height(), (size, str(widget), "vertical clip", str(anchor), anchor.winfo_y(), anchor.winfo_height(), parent.winfo_height())
                    anchor = parent
                assert widget.winfo_width() >= widget.winfo_reqwidth(), (size, str(widget))
                assert widget.winfo_height() >= widget.winfo_reqheight(), (size, str(widget))
                offset = widget.winfo_rooty() - area.content.winfo_rooty()
                area.canvas.yview_moveto(max(0, offset - 60) / area.content.winfo_height())
                settle(root)
                tip = getattr(widget, "_piezo_simple_tooltip", None)
                assert tip is not None
                tip._show(None)
                _flush(root)
                assert tip.tip is not None
                assert tip.tip.winfo_width() >= tip.tip.winfo_reqwidth()
                assert tip.tip.winfo_height() >= tip.tip.winfo_reqheight()
                assert tip.tip.winfo_rootx() + tip.tip.winfo_width() <= root.winfo_screenwidth()
                assert tip.tip.winfo_rooty() + tip.tip.winfo_height() <= root.winfo_screenheight()
                tip._hide()
                checked += 1
            assert checked >= 10, checked
            page._select_button_wave_tab("sinc")
            settle(root)
            canvas = page.adc_legend.canvas
            for item in canvas.find_all():
                if canvas.type(item) == "text":
                    left, top, right, bottom = canvas.bbox(item)
                    assert left >= 0 and top >= 0 and right <= canvas.winfo_width() and bottom <= canvas.winfo_height(), (size, canvas.itemcget(item, "text"))
            for cell in page.adc_legend._table.winfo_children():
                column, row = int(cell.grid_info()["column"]), int(cell.grid_info()["row"])
                allocated = page.adc_legend._table.grid_bbox(column, row)[2]
                assert int(cell.cget("wraplength")) <= allocated - 8
            section = page._button_adc_panels[0]
            offset = section.winfo_rooty() - area.content.winfo_rooty() - 16
            area.canvas.yview_moveto(max(0, offset) / area.content.winfo_height())
            settle(root)
            region = [float(value) for value in area.canvas.cget("scrollregion").split()]
            correction = section.winfo_rooty() - area.canvas.winfo_rooty() - 16
            area.canvas.yview_moveto(area.canvas.yview()[0] + correction / (region[3] - region[1]))
            settle(root)
            stem = f"{theme}-{language}-{size}-font{font_size}"
            bmp = out / f"{stem}.bmp"
            assert _capture_window_bmp(root, bmp) is None
            from PIL import Image
            Image.open(bmp).save(out / f"{stem}.png")
            records.append(dict(theme=theme, language=language, size=size, font_size=font_size,
                                tk_scaling=scale, question_marks=checked, legend_width=canvas.winfo_width()))
        assert not errors, errors
    (out / "validation.json").write_text(json.dumps(dict(status="PASS", records=records,
        callback_errors=errors, hardware="Offline; transport calls forbidden"), indent=2), encoding="utf-8")
    print(f"PASS: {len(records)} page states, {sum(r['question_marks'] for r in records)} help anchors/popups")


if __name__ == "__main__":
    main()
