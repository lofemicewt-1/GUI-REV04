from collections import deque
from types import SimpleNamespace
import tkinter as tk
from tests.tk_cleanup import attach_tk_cleanup, prepare_tk_test
import unittest
from unittest.mock import patch

from pages.awp192x_modes import ButtonModePage
from pages.awp192x_button_view import ButtonModeView
from pages.awp192x_realtime import AWP192xADCVoltageWindow
from tests import test_awp192x_button_view as button_view_tests


class RealtimeWindowTests(unittest.TestCase):
    def setUp(self):
        prepare_tk_test(self)
        self.root=tk.Tk()
        self.root._code_to_voltage=ButtonModePage._code_to_voltage.__get__(self.root,ButtonModePage)
        # The conversion method uses the AWP192x constants on its owner.
        for key in ("ADC_POS_ZERO","ADC_NEG_ZERO","ADC_MAX_CODE"):
            setattr(self.root,key,getattr(ButtonModePage,key))
        self.window=AWP192xADCVoltageWindow(self.root,lambda:None)
        self.window.update()
        attach_tk_cleanup(self, self.root, self.window)

    def test_voltage_and_readback_keep_each_samples_own_flags(self):
        self.window.add_sample(0x2FFF)
        self.assertEqual(self.window.voltage.get(),"18000.000 mV")
        self.window.add_sample(0xAFFF)
        self.assertEqual(self.window.voltage.get(),"-18000.000 mV")
        self.assertEqual(self.window.gain.get(),"0-19V")
        self.assertEqual(self.window.polarity.get(),"OUT-")
        self.assertEqual(self.window.adc_code.get(),"0xFFF")
        self.window.add_sample(0x0FFF)
        self.assertEqual(self.window.voltage.get(),"100000.000 mV")
        self.assertEqual(self.window._sample_to_mv(self.window.points[0][1],()),18000)
        self.assertEqual(self.window._sample_to_mv(self.window.points[1][1],()),-18000)
        self.window.add_sample(0xA800)
        self.assertEqual(self.window.voltage.get(),"0.000 mV")

    def test_rolling_window_clips_history_and_scales_with_thresholds(self):
        window=self.window
        window.points=deque([(94,0x0FFF),(96,0xA800),(99,0xA900)],maxlen=2000)
        with patch("pages.awp1221_realtime_sram.time.monotonic",return_value=100):
            window.time_scale.set("5")
            window.set_threshold_voltages(3,-.2)
            coords=window.canvas.coords(window.canvas.find_withtag("adc_curve")[0])
            self.assertEqual(len(coords),4)
            self.assertEqual(len(window.canvas.find_withtag("adc_threshold")),2)
            texts=[window.canvas.itemcget(i,"text") for i in window.canvas.find_withtag("adc_threshold_label")]
            self.assertIn("按压阈值 3000.000 mV",texts)
            self.assertIn("松手阈值 -200.000 mV",texts)
            window.time_scale.set("10")
            self.assertEqual(len(window.canvas.coords(window.canvas.find_withtag("adc_curve")[0])),6)
            window.set_threshold_voltages(0,0)
            boxes=[window.canvas.bbox(i) for i in window.canvas.find_withtag("adc_threshold_label")]
            a,b=boxes
            self.assertFalse(a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3])

    def test_poll_menu_bounded_history_and_small_window_controls(self):
        self.window.poll_interval.set("50")
        self.assertEqual(self.window.poll_interval_ms(),50)
        self.window.poll_interval.set("bad")
        self.assertEqual(self.window.poll_interval_ms(),10)
        with patch.object(self.window,"redraw"):
            for _ in range(2005):
                self.window.add_sample(0x2800)
        self.assertEqual(len(self.window.points),2000)
        for invalid in ("0","bad","nan","inf"):
            self.window.time_scale.set(invalid)
            self.assertEqual(self.window.voltage.get(),"请输入有效的时间范围")
        self.window.time_scale.set("10")
        self.window.geometry("608x384")
        self.window.update()
        for child,_,_ in self.window._control_cells:
            self.assertGreaterEqual(child.winfo_x(),0)
            self.assertLessEqual(child.winfo_x()+child.winfo_width(),self.window._controls.winfo_width())


class RealtimePollingTests(unittest.TestCase):
    def page(self):
        return button_view_tests.ButtonDisplayTests().page(numeric=False,graph=True)

    def test_gpio_low_adds_one_band_until_a_new_falling_edge(self):
        page=self.page()
        page._button_graph_gpio_level=None
        bands=[]
        page._detect_playback_wait_ms=lambda:25
        page._button_graph_window.add_vibration_band=bands.append
        for level in (0,0,1,0,0):
            ButtonModeView._note_button_graph_level(page,level)
        self.assertEqual(bands,[.025,.025])

    def test_read_failure_updates_window_and_keeps_polling(self):
        page=self.page()
        page._finish_button_display(5,0x44,False,None,error="I2C failed")
        self.assertEqual(page._button_graph_window.status.get(),"读取失败: I2C failed")
        self.assertEqual(page._adc_realtime_job,"poll-job")

    def test_graph_poll_reads_d2_and_adc_without_updating_numeric(self):
        page=self.page()
        page._button_adc_pending=False
        calls=[]
        serial=page.ctx.serial
        from contextlib import nullcontext
        serial.suppress_logging=lambda:nullcontext()
        serial.read_gpio=lambda pin:calls.append(("gpio",pin)) or SimpleNamespace(ok=True,data="GPIOR D2 0")
        serial.read_register_value=lambda addr,reg,bits:calls.append((addr,reg,bits)) or SimpleNamespace(ok=True,data="reply")
        serial.extract_rdataw_value=lambda *a,**k:0xA123
        class Thread:
            def __init__(self,target,daemon):self.target=target
            def start(self):self.target()
        with patch("pages.awp192x_button_view.threading.Thread",Thread),patch(
                "pages.awp192x_button_view.post_to_ui",lambda widget,fn,*a:fn(*a)):
            page._poll_button_display()
        self.assertEqual(calls,[("gpio","D2"),(0x44,0x07,16)])
        self.assertEqual(page.events,[("graph",0xA123)])
        self.assertEqual(page._button_graph_window.status.get(),"D2=0 / nINT · ADC 0x07: 0x123")

    def test_selected_window_poll_interval_is_used(self):
        page=self.page()
        page._button_graph_window.poll_interval_ms=lambda:50
        self.assertEqual(ButtonModeView._button_display_poll_ms(page),50)


if __name__=="__main__":
    unittest.main()
