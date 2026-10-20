from __future__ import annotations

import unittest
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import patch

from pages.awp192x_modes import ButtonModePage
from pages.awp192x_button_view import ButtonModeView


class Value:
    def __init__(self, value=""):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class ButtonDisplayTests(unittest.TestCase):
    def page(self, numeric=True, graph=False):
        page = object.__new__(ButtonModePage)
        page.ctx = SimpleNamespace(state=SimpleNamespace(awp192x_device_addr=0x44), serial=SimpleNamespace(is_connected=True))
        page._button_adc_generation = 5
        page._button_adc_pending = True
        page._adc_realtime_active = numeric
        page._adc_realtime_job = None
        page._buttons = {}
        page._button_graph_active = lambda: graph
        page._button_graph_window = SimpleNamespace(status=Value())
        page._button_display_poll_ms = lambda: 10 if graph else 300
        page._note_button_graph_level = lambda level: None
        page.winfo_exists = lambda: True
        page.events = []
        page._set_button_adc_display = lambda v: page.events.append(("number",v))
        page._record_button_adc_sample = lambda v: page.events.append(("graph",v))
        page.after = lambda *args: "poll-job"
        page.after_cancel = lambda job: page.events.append(("cancel",job))
        page._refresh_adc_read_buttons = lambda: None
        return page

    def test_one_response_feeds_both_views(self):
        page = self.page(graph=True)
        page._finish_button_display(5,0x44,True,0xA123)
        self.assertEqual(page.events, [("number",0xA123),("graph",0xA123)])
        self.assertEqual(page._adc_realtime_job,"poll-job")

    def test_graph_only_does_not_change_numeric_display(self):
        page = self.page(numeric=False,graph=True)
        page._finish_button_display(5,0x44,False,0xA123)
        self.assertEqual(page.events,[("graph",0xA123)])

    def test_stale_session_address_and_disconnected_replies_are_discarded(self):
        for generation,address,connected in ((4,0x44,True),(5,0x45,True),(5,0x44,False)):
            with self.subTest(generation=generation,address=address,connected=connected):
                page=self.page(graph=True)
                page.ctx.serial.is_connected=connected
                page._finish_button_display(generation,address,True,0xA123)
                self.assertEqual(page.events,[])

    def test_stopping_numeric_preserves_graph_poll(self):
        page=self.page(graph=True)
        page._adc_realtime_job="existing-job"
        page._stop_button_adc_realtime()
        self.assertFalse(page._adc_realtime_active)
        self.assertEqual(page._adc_realtime_job,"existing-job")
        self.assertEqual(page.events,[])
        self.assertEqual(page._button_adc_generation,6)

    def test_stopping_last_consumer_cancels_poll(self):
        page=self.page()
        page._adc_realtime_job="existing-job"
        page._stop_button_adc_realtime()
        self.assertIsNone(page._adc_realtime_job)
        self.assertEqual(page.events,[("cancel","existing-job")])

    def test_closing_graph_keeps_numeric_active(self):
        page=self.page()
        page._button_graph_window=SimpleNamespace(destroy=lambda: page.events.append("destroy"))
        page._close_button_adc_graph()
        self.assertTrue(page._adc_realtime_active)
        self.assertIsNone(page._button_graph_window)
        self.assertEqual(page.events,["destroy"])

    def test_rom_selectors_preserve_chip_wave_number(self):
        page=self.page()
        self.assertEqual(page._parse_script_waveform_slice("0x1F","wave"),31)
        self.assertEqual(page._parse_waveform_slice(Value("0x1F")),31)
        self.assertEqual(page._set_repeat_and_waveform_fields(0,3,31),0x005F)
        self.assertEqual(page._parse_script_waveform_slice("serial","wave"),30)

    def test_adc_flags_are_not_treated_as_sample_bits(self):
        page=self.page()
        page._set_button_adc_display = ButtonModeView._set_button_adc_display.__get__(page)
        page.adc_value_var=Value()
        page._adc_nibbles=[]
        page._set_button_adc_display(0xA800)
        self.assertIn("0x800 / 2048 / 0.000 V",page.adc_value_var.get())
        page._set_button_adc_display(0x2FFF)
        self.assertIn("/ 4095 / 18.000 V",page.adc_value_var.get())
        page._set_button_adc_display(0xAFFF)
        self.assertIn("/ 4095 / -18.000 V",page.adc_value_var.get())

    def test_pending_worker_does_not_start_second_read(self):
        page=self.page(graph=True)
        with patch("pages.awp192x_button_view.threading.Thread") as thread:
            page._poll_button_display()
        thread.assert_not_called()
        self.assertEqual(page._adc_realtime_job,"poll-job")

    def test_numeric_poll_reads_only_chip_adc_in_background_and_quietly(self):
        page=self.page()
        page._button_adc_pending=False
        calls=[]
        serial=page.ctx.serial
        serial.suppress_logging=lambda: nullcontext()
        serial.read_register_value=lambda address,reg,bits: calls.append((address,reg,bits)) or SimpleNamespace(ok=True,data="reply")
        serial.extract_rdataw_value=lambda *a,**kw: 0xA123
        class Thread:
            def __init__(self,target,daemon):
                self.target=target
            def start(self):
                self.target()
        with patch("pages.awp192x_button_view.threading.Thread",Thread),patch("pages.awp192x_button_view.post_to_ui",lambda widget,fn,*a:fn(*a)):
            page._poll_button_display()
        self.assertEqual(calls,[(0x44,0x07,16)])
        self.assertEqual(page.events,[("number",0xA123),("graph",0xA123)])
        self.assertFalse(page._button_adc_pending)


class TimingCanvasTests(unittest.TestCase):
    def test_adc_diagram_switches_phase_and_keeps_threshold_markers_on_curve(self):
        import tkinter as tk
        from tkinter import ttk
        from config.languages import t
        from styles.theme import get_colors
        root=tk.Tk()
        self.addCleanup(root.destroy)
        canvas=tk.Canvas(root,highlightthickness=0)
        canvas.pack(fill="both",expand=True)
        page=SimpleNamespace(adc_diagram=canvas,colors=get_colors("light"),
            tr=lambda key:t("zh",key),press_threshold_var=Value("3"),release_threshold_var=Value("-1"),
            _button_adc_diagram_phase="press", language="zh",
            _button_adc_phase_buttons={phase:ttk.Button(root) for phase in ("press","release")})
        page._button_adc_diagram_signature=lambda:ButtonModeView._button_adc_diagram_signature(page)
        page._draw_button_adc_diagram=lambda:ButtonModeView._draw_button_adc_diagram(page)
        page._push_button_graph_thresholds=lambda:None
        for width in (280,540):
            root.geometry(f"{width}x240")
            root.update()
            for phase in ("press","release"):
                ButtonModeView._show_button_adc_phase(page,phase)
                self.assertEqual(page._button_adc_phase_buttons[phase].cget("style"),"Selected.TButton")
                self.assertEqual(len(canvas.find_withtag("adc_phase_band")),3)
                self.assertEqual(len(canvas.find_withtag("adc_phase_transition")),2)
                selected=canvas.coords(canvas.find_withtag(f"adc_marker_{phase}")[0])
                self.assertEqual(selected[2]-selected[0],10)
                other="release" if phase=="press" else "press"
                inactive=canvas.coords(canvas.find_withtag(f"adc_marker_{other}")[0])
                self.assertEqual(inactive[2]-inactive[0],6)
                curve=canvas.coords(canvas.find_withtag("adc_diagram_curve")[0])
                for name,start,end in (("press",1,2),("release",3,4)):
                    marker=canvas.coords(canvas.find_withtag(f"adc_marker_{name}")[0])
                    x,y=(marker[0]+marker[2])/2,(marker[1]+marker[3])/2
                    x0,y0,x1,y1=curve[start*2:start*2+2]+curve[end*2:end*2+2]
                    expected=y0+(x-x0)/(x1-x0)*(y1-y0)
                    self.assertAlmostEqual(y,expected)
                annotations=canvas.find_withtag("adc_threshold_label")+canvas.find_withtag("adc_phase_transition")
                for item in annotations:
                    box=canvas.bbox(item)
                    self.assertGreaterEqual(box[0],0)
                    self.assertLessEqual(box[2],width)
                for i,first in enumerate(annotations):
                    a=canvas.bbox(first)
                    for second in annotations[i+1:]:
                        b=canvas.bbox(second)
                        self.assertFalse(a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3])
        page.press_threshold_var.set("0")
        page.release_threshold_var.set("0")
        page._draw_button_adc_diagram()
        labels=[canvas.bbox(item) for item in canvas.find_withtag("adc_threshold_label")]
        a,b=labels
        self.assertFalse(a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3])
        for text in ("", "-", "nan", "inf"):
            page.press_threshold_var.set(text)
            page._draw_button_adc_diagram()
            self.assertEqual(len(canvas.find_withtag("adc_phase_band")),3)

    def test_duration_cards_fit_wrapped_text_at_large_scaling(self):
        import tkinter as tk
        from config.languages import t
        from styles.theme import get_colors
        from pages.awp192x_button_view import draw_timed_waveform
        root=tk.Tk()
        self.addCleanup(root.destroy)
        scale=float(root.tk.call("tk", "scaling"))
        self.addCleanup(lambda: root.tk.call("tk", "scaling", scale))
        canvas=tk.Canvas(root,highlightthickness=0)
        canvas.pack(fill="both",expand=True)
        for scaling in (2.0,3.0):
            root.tk.call("tk","scaling",scaling)
            for width in (360,820):
                root.geometry(f"{width}x600")
                root.update()
                segments=[(f"serial{i}: slice{i} x8",[0,10,0,-2]*i+[0]) for i in range(8)]
                draw_timed_waveform(canvas,get_colors("light"),lambda key:t("en",key),segments,10)
                root.update()
                for text,card in zip(canvas.find_withtag("segment_duration"),canvas.find_withtag("segment_card")):
                    box,bounds=canvas.bbox(text),canvas.coords(card)
                    self.assertGreaterEqual(box[0],bounds[0])
                    self.assertLessEqual(box[2],bounds[2])
                    self.assertGreaterEqual(box[1],bounds[1])
                    self.assertLessEqual(box[3],bounds[3])
                    self.assertLessEqual(box[3],canvas.winfo_height())

    def test_three_four_and_eight_segments_fit_wide_and_narrow_views(self):
        import tkinter as tk
        from config.languages import t
        from styles.theme import get_colors
        from pages.awp192x_button_view import draw_timed_waveform
        root=tk.Tk()
        self.addCleanup(root.destroy)
        canvas=tk.Canvas(root,highlightthickness=0)
        canvas.pack(fill="both",expand=True)
        for width in (360,820):
            root.geometry(f"{width}x600")
            root.update()
            for count in (3,4,8):
                segments=[(f"serial{i}: slice{i} x8",[0,10,0,-2]*i+[0]) for i in range(count)]
                draw_timed_waveform(canvas,get_colors("light"),lambda key:t("en",key),segments,10)
                root.update()
                texts=canvas.find_withtag("segment_duration")
                cards=canvas.find_withtag("segment_card")
                self.assertEqual(len(texts),count)
                for text,card in zip(texts,cards):
                    box,bounds=canvas.bbox(text),canvas.coords(card)
                    self.assertGreaterEqual(box[0],bounds[0])
                    self.assertLessEqual(box[2],bounds[2])
                    self.assertGreaterEqual(box[1],bounds[1])
                    self.assertLessEqual(box[3],bounds[3])
                    self.assertLessEqual(box[3],canvas.winfo_height())


if __name__ == "__main__":
    unittest.main()
