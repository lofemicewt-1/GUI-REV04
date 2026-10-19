"""Real Tk validation with serial I/O fenced by the offline app harness."""
from pathlib import Path
import time
import faulthandler
import json
import sys
from unittest.mock import patch,PropertyMock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.validate_responsive_layout import mocked_application,_flush,_capture_window_bmp
from pages.awp192x_button_view import ButtonWaveformPage


def main():
    faulthandler.dump_traceback_later(55,exit=True)
    out=ROOT/"outputs/awp1923_button_20261019/revision3_graph"
    out.mkdir(parents=True,exist_ok=True)
    records,errors=[],[]
    with mocked_application() as app:
        root=app.root
        root.report_callback_exception=lambda *a: errors.append(str(a))
        app.navigation.show("awp1921_1923")
        host=app.navigation.pages["awp1921_1923"]
        page=host.mode_pages["button_mode"]
        wave=page.waveform_page
        for theme,language,size in (("light","zh","1700x980"),("dark","zh","1450x820"),("light","en","1280x720")):
            app.state.theme,app.state.language=theme,language
            app._apply_visual_settings()
            host.refresh_language()
            root.geometry(size)
            _flush(root)
            for source in ("sinc","rom","ram"):
                wave.select_source(source)
                _flush(root)
                assert wave.source==source
                assert wave.canvas.winfo_height()>=400
                assert wave.controls.winfo_reqheight()<=wave.controls.winfo_height()
                assert all(b.winfo_ismapped() for b in wave.source_buttons.values())
                if source=="sinc":
                    page.duration_var.set("10")
                    for var,value in ((wave.sinc_points1_var,"180"),(wave.sinc_points2_var,"160"),(wave.sinc_points3_var,"90")):
                        var.set(value)
                    _flush(root)
                    texts=[wave.canvas.itemcget(i,"text") for i in wave.canvas.find_withtag("segment_duration")]
                    for text,duration,end in zip(texts,(1.8,1.6,.9),(1.8,3.4,4.3)):
                        assert f"{duration:g} ms" in text and f"{end:g} ms" in text,text
                    assert len(wave.canvas.find_withtag("time_axis"))==4
                if source=="rom":
                    assert page.play_waveform_var.get()=="0x1F"
                    assert wave.effective_points()==1024
                    operations=page._build_current_register_script_operations()
                    assert not any(op.reg_addr in (0x16,0x00) for op in operations)
                    assert any(op.reg_addr==0x13 and op.value is not None and op.value&31==31 for op in operations)
                host.mode_scroll.canvas.yview_moveto(0)
                _flush(root)
                if source=="sinc":
                    assert _capture_window_bmp(root,out/f"{theme}-{language}-sinc.bmp") is None
                records.append(dict(theme=theme,language=language,size=size,source=source,canvas_height=wave.canvas.winfo_height()))
            page._set_button_adc_display(0xA3C0)
            assert len(page._adc_nibbles)==3
            assert "".join(w.cget("text").split("\n")[0] for w in page._adc_nibbles)=="3C0"
            assert all(w.cget("background")==page.colors["primary"] for w in page._adc_nibbles)
            assert [w.cget("text").split("\n")[1] for w in page._adc_nibbles]==["11-8","7-4","3-0"]
            page.press_threshold_var.set("3")
            page.release_threshold_var.set("-0.2")
            for phase in ("press","release"):
                page._button_adc_phase_buttons[phase].invoke()
                assert page._button_adc_diagram_phase==phase
                assert page._button_adc_phase_buttons[phase].cget("style")=="Selected.TButton"
                assert len(page.adc_diagram.find_withtag("adc_phase_band"))==3
                host.mode_scroll.canvas.yview_moveto(1)
                _flush(root)
                assert _capture_window_bmp(root,out/f"{theme}-{language}-adc-{phase}.bmp") is None
            page._button_adc_phase_buttons["press"].invoke()
            realtime=page._buttons["adc_read_realtime"]
            graph=page._buttons["awp1221_adc_graph"]
            assert realtime.master is page._buttons["adc_read_current_value"].master
            assert realtime.master is not graph.master
            with patch.object(type(app.ctx.serial),"is_connected",new_callable=PropertyMock,return_value=True),patch.object(page,"_poll_button_adc_realtime"):
                realtime.invoke()
                assert page._adc_realtime_active and page._button_graph_window is None
                graph.invoke()
                _flush(root)
                assert page._button_graph_active()
                page._record_button_adc_sample(0xA800)
                page._record_button_adc_sample(0xAFFF)
                assert len(page._button_graph_canvas.find_withtag("adc_curve"))==1
                assert len(page._button_graph_canvas.find_withtag("adc_threshold"))==2
                assert page._button_graph_window.poll_interval_ms()==10
                assert page._button_graph_window.adc_code.get()=="0xFFF"
                window=page._button_graph_window
                window.points.clear()
                now=time.monotonic()
                for index,raw in enumerate((0xA800,0xA400,0xA000,0xA400,0xA800,0xAC00,0xAFFF,0xAC00,0xA800,0xAFFF)):
                    with patch("pages.awp192x_realtime.time.monotonic",return_value=now-9+index):
                        page._record_button_adc_sample(raw)
                window.redraw()
                _flush(root)
                assert _capture_window_bmp(page._button_graph_window,out/f"{theme}-{language}-voltage.bmp") is None
                realtime.invoke()
                assert not page._adc_realtime_active and page._button_graph_active()
                page._close_button_adc_graph()
                realtime.invoke()
                graph.invoke()
                page._close_button_adc_graph()
                assert page._adc_realtime_active
                realtime.invoke()
            host.mode_scroll.canvas.yview_moveto(1)
            _flush(root)
            assert _capture_window_bmp(root,out/f"{theme}-{language}-readings.bmp") is None
        wave.select_source("ram")
        for index,count in enumerate((40,50,60,70)):
            values=[0x800,0xFFF,0x800,0]* (count//4)+[0x800]*(count%4)
            page.storage.add_or_replace(index,index*100,index*100+count,count,"imported",values)
            wave.serial_slice_vars[index].set(f"slice{index}")
            wave.serial_repeat_vars[index].set(str(index+1))
        wave.serial_slice_vars[4].set("0x1F")
        wave.waveform_type_var.set("serial")
        _flush(root)
        assert len(wave.canvas.find_withtag("segment_duration"))==4
        assert any("6 ms" in wave.canvas.itemcget(i,"text") for i in wave.canvas.find_withtag("wave_duration"))
        wave.select_source("sinc")
        log_records=[]
        page._toggle_button_log()
        for size in ("1450x820","1280x720","1024x768","640x480"):
            root.geometry(size)
            _flush(root)
            page._sync_button_log()
            _flush(root)
            area=host.mode_scroll
            assert area.canvas.yview()[1]>.999,(size,area.canvas.yview())
            bottom=page._button_log.winfo_rooty()+page._button_log.winfo_height()
            viewport=area.canvas.winfo_rooty()+area.canvas.winfo_height()
            assert bottom<=viewport+2,(size,bottom,viewport)
            assert area.canvas.winfo_height()>30
            assert host.footer_scroll.canvas.winfo_height()<=int(host.winfo_height()*.35)+2
            host.footer_scroll.canvas.yview_moveto(1)
            _flush(root)
            assert host.addr_combo.winfo_rooty()+host.addr_combo.winfo_height()<=host.footer_scroll.canvas.winfo_rooty()+host.footer_scroll.canvas.winfo_height()+2
            assert _capture_window_bmp(root,out/f"log-{size}.bmp") is None
            log_records.append(dict(size=size,log_bottom=bottom,viewport_bottom=viewport))
        page._toggle_button_log()
        root.geometry("1700x980")
        host.show_mode("full_mode")
        _flush(root)
        full=host.mode_pages["full_mode"]
        full.open_waveform_generator()
        _flush(root)
        assert not isinstance(full.waveform_page,ButtonWaveformPage)
        full._close_waveform_window()
        host.show_mode("pump_mode")
        _flush(root)
        host.show_mode("button_mode")
        _flush(root)
        assert not errors,errors
    result=dict(cases=records,log_cases=log_records,tk_errors=errors,hardware="fenced; no physical I/O")
    (out/"ui_validation.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    faulthandler.cancel_dump_traceback_later()
    print(f"PASS {len(records)} layout cases, {len(log_records)} log sizes, Full/Pump retained")

if __name__=="__main__":
    main()
