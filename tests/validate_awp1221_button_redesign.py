"""Real Tk checks with all serial I/O fenced by the existing offline harness."""
from pathlib import Path
import json
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.validate_responsive_layout import mocked_application, _flush, _capture_window_bmp


def main():
    out = ROOT / "outputs/button_mode_20261019/revision3"
    out.mkdir(parents=True, exist_ok=True)
    records, errors = [], []
    with mocked_application() as app:
        root = app.root
        root.report_callback_exception = lambda *args: errors.append(str(args))
        app.navigation.show("awp1221")
        host = app.navigation.pages["awp1221"]
        page = host.mode_pages["button_mode"]
        host.show_mode("button_mode")
        for theme, language, size in (("light", "zh", "1700x980"),
                                      ("dark", "zh", "1700x980"),
                                      ("light", "en", "1450x820")):
            app.state.theme, app.state.language = theme, language
            app._apply_visual_settings()
            host.refresh_language()
            root.geometry(size)
            _flush(root)
            for tab in ("sinc", "rom", "sram", "sinc"):
                page._select_button_wave_tab(tab)
                _flush(root)
                assert page.sram_editor.winfo_ismapped() == (tab == "sram")
                assert page.rom_source_var.get() == ("RAM" if tab == "sram" else "ROM")
                assert all(not w.winfo_manager() for w in page._selector_widgets["awp1221_rom_source"])
                assert not page._waveform_selector_title.winfo_ismapped()
                assert all(button.winfo_ismapped() for button in page._button_wave_tabs.values())
                if tab != "sram":
                    assert page.rom_canvas.winfo_height() >= 390
                    captions = [page.rom_canvas.itemcget(item, "text") for item in page.rom_canvas.find_withtag("wave_duration")]
                    assert len(captions) == 1 and "ms" in captions[0]
                    assert len(page.rom_canvas.find_withtag("time_axis")) == (4 if tab == "sinc" else 5)
                    assert len(page.rom_canvas.find_withtag("sinc_segment_duration")) == (3 if tab == "sinc" else 0)
                    controls = page._button_waveform_controls
                    assert controls.winfo_reqheight() <= controls.winfo_height(), (theme, language, tab,
                        controls.winfo_reqheight(), controls.winfo_height())
                    if tab == "rom":
                        first = page._selector_widgets["awp1221_rom_slice"][1]
                        wave = page._selector_widgets["awp1221_rom_waveform"][1]
                        assert first.winfo_y() == wave.winfo_y()
                        assert int(wave.grid_info()["column"]) == 1
                host.mode_scroll.canvas.yview_moveto(0)
                _flush(root)
                assert _capture_window_bmp(root, out / f"{theme}-{language}-{tab}.bmp") is None
                records.append({"theme": theme, "language": language, "size": size,
                                "waveform": tab, "canvas_height": page.rom_canvas.winfo_height()})
            page._finish_adc_value_read(value=0xA3C0, error=None, show_error=False,
                generation=page._adc_lifecycle_generation, on_complete=None)
            for stage in (0, 1):
                page._show_button_stage(stage)
                for high in (True, False):
                    page._button_stage_vars[stage]["use_high_12_bits"].set(high)
                    labels = page._adc_bit_labels
                    assert "".join(w.cget("text").split("\n")[0] for w in labels) == "A3C0"
                    highlighted = [i for i, w in enumerate(labels) if w.cget("background") != page.colors["panel"]]
                    assert highlighted == ([0, 1, 2] if high else [1, 2, 3])
            page._show_button_stage(0)
            page._button_stage_vars[0]["use_high_12_bits"].set(True)
            assert not any(key in page._buttons for key in ("awp1221_gpio_read", "awp1221_gpio_apply"))
            realtime = page._buttons["adc_read_realtime"]
            graph = page._buttons["awp1221_adc_graph"]
            assert realtime.master is page._buttons["adc_read_current_value"].master
            assert graph.master is not realtime.master
            with patch.object(page, "_ensure_connected", return_value=True), \
                 patch.object(page, "_load_adc_voltage_settings"), \
                 patch.object(page, "_poll_adc_realtime"), \
                 patch.object(page, "_push_realtime_thresholds"):
                realtime.invoke()
                _flush(root)
                assert page._adc_value_realtime_active and page._adc_voltage_window is None
                graph.invoke()
                _flush(root)
                assert page._adc_voltage_window.winfo_exists()
                realtime.invoke()
                assert not page._adc_value_realtime_active and page._adc_voltage_window.winfo_exists()
                page._close_adc_graph()
                assert not page._adc_realtime_active
                realtime.invoke()
                graph.invoke()
                page._close_adc_graph()
                assert page._adc_value_realtime_active and page._adc_realtime_active
                realtime.invoke()
                _flush(root)
                assert not page._adc_realtime_active
            diagram_width = page._button_adc_canvas.master.winfo_width()
            settings_width = page._button_stage_buttons[0].master.master.winfo_width()
            assert .38 <= diagram_width / (diagram_width + settings_width) <= .42
            for name, target in (("waveform", page.rom_canvas),
                                 ("adc", page._button_stage_buttons[0]),
                                 ("readings", page._adc_bit_labels[0])):
                area = host.mode_scroll
                offset = target.winfo_rooty() - area.content.winfo_rooty()
                area.canvas.yview_moveto(max(0, offset - 25) / area.content.winfo_height())
                _flush(root)
                assert target.winfo_rooty() >= area.canvas.winfo_rooty() - 1
                if target.winfo_height() <= area.canvas.winfo_height() - 25:
                    assert target.winfo_rooty() + target.winfo_height() <= area.canvas.winfo_rooty() + area.canvas.winfo_height() + 1
                else:
                    bottom = offset + target.winfo_height()
                    area.canvas.yview_moveto(max(0, bottom - area.canvas.winfo_height()) / area.content.winfo_height())
                    _flush(root)
                    assert target.winfo_rooty() + target.winfo_height() <= area.canvas.winfo_rooty() + area.canvas.winfo_height() + 1
                if name == "readings":
                    assert realtime.winfo_ismapped() and realtime.winfo_width() >= 80
                    assert realtime.winfo_rooty() >= area.canvas.winfo_rooty()
                    page._communication_log_toggle.invoke()
                    _flush(root)
                    assert page._serial_log_text.winfo_ismapped()
                    page._communication_log_toggle.invoke()
                for key in ("awp1221_reset", "awp1221_sleep", "awp1221_shutdown", "awp1221_default_state"):
                    assert host._buttons[key].winfo_ismapped()
                bmp = out / f"{theme}-{language}-{name}.bmp"
                assert _capture_window_bmp(root, bmp) is None
            host.show_mode("full_mode")
            _flush(root)
            assert len(host.mode_pages["full_mode"]._adc_bit_labels) == 16
            host.show_mode("button_mode")
        for size in ("1450x820", "1280x720", "1024x768", "640x480"):
            root.geometry(size)
            _flush(root)
            page._communication_log_toggle.invoke()
            _flush(root, 6)
            area = host.mode_scroll
            area.canvas.yview_moveto(1)
            _flush(root, 6)
            log = page._serial_log_text.master
            bottom = log.winfo_rooty() + log.winfo_height()
            assert bottom <= area.canvas.winfo_rooty() + area.canvas.winfo_height(), (size, "log bottom clipped")
            assert area.canvas.yview()[1] >= .999
            host.footer_scroll.canvas.yview_moveto(1)
            _flush(root, 6)
            detect = host._buttons["detect_chip"]
            assert detect.winfo_rooty() + detect.winfo_height() <= host.footer_scroll.canvas.winfo_rooty() + host.footer_scroll.canvas.winfo_height()
            assert _capture_window_bmp(root, out / f"log-{size}.bmp") is None
            page._communication_log_toggle.invoke()
            _flush(root)
        assert not errors, errors
    (out / "ui_validation.json").write_text(json.dumps({"status": "PASS", "records": records,
        "callback_errors": errors, "hardware": "Offline; all transport calls forbidden"}, indent=2), encoding="utf-8")
    print(f"PASS: {len(records)} waveform/theme/language/size states; Stage precision, scrolling and Full Mode checked")


if __name__ == "__main__":
    main()
