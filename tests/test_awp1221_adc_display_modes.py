"""Button Mode numeric streaming and graph display have separate lifecycles."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from pages.awp1221_page import AWP1221ModePage


class Value:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class Graph:
    def __init__(self, parent, on_close, **kwargs):
        self.on_close = on_close
        self.exists = True
        self.samples = []
        self.status = Value()

    def winfo_exists(self):
        return self.exists

    def transient(self, parent):
        pass

    def deiconify(self):
        pass

    def lift(self):
        pass

    def destroy(self):
        self.exists = False

    def poll_interval_ms(self):
        return 10

    def add_sample(self, value):
        self.samples.append(value)


class AdcDisplayModeTests(unittest.TestCase):
    def setUp(self):
        self.page = page = object.__new__(AWP1221ModePage)
        page.mode_key = "button_mode"
        page._init_adc_realtime()
        page._adc_lifecycle_generation = 0
        page._adc_read_in_flight = False
        page._ensure_connected = lambda: True
        page.device_addr = lambda: 0x50
        page.ctx = SimpleNamespace(serial=SimpleNamespace(is_connected=True, suppress_logging=nullcontext))
        page.adc_value_var = Value()
        page._refresh_adc_bits = lambda: None
        page._refresh_adc_read_buttons = lambda: None
        page._load_adc_voltage_settings = lambda: None
        page._push_realtime_thresholds = lambda: None
        page.winfo_toplevel = lambda: None
        self.jobs, self.cancelled, self.reads = [], [], []
        page.after = lambda delay, callback: self.jobs.append(callback) or len(self.jobs)
        page.after_cancel = self.cancelled.append

        def read(register, address):
            self.reads.append((register, address))
            return len(self.reads)

        page._read_register_value = read
        self.tasks = []

        def background(widget, work, *, on_done, on_error):
            self.tasks.append((work, on_done, on_error))

        self.worker = patch("pages.awp1221_realtime_sram._run_ui_background_task", background)
        self.factory = patch("pages.awp1221_realtime_sram.ADCVoltageWindow", Graph)
        self.worker.start()
        self.factory.start()
        self.addCleanup(self.worker.stop)
        self.addCleanup(self.factory.stop)

    def finish_sample(self):
        work, done, _error = self.tasks.pop(0)
        done(work())

    def tick(self):
        self.jobs[-1]()
        self.finish_sample()

    def test_numeric_button_streams_values_without_opening_graph_or_reading_gpio(self):
        self.page.toggle_adc_realtime()
        self.finish_sample()
        first = self.page.adc_value_var.get()
        self.tick()
        self.assertNotEqual(self.page.adc_value_var.get(), first)
        self.assertIsNone(self.page._adc_voltage_window)
        self.assertEqual(self.reads, [(0x13, 0x50), (0x13, 0x50)])
        self.page.toggle_adc_realtime()
        self.assertFalse(self.page._adc_realtime_active)
        self.assertTrue(self.cancelled)

    def test_stop_numeric_keeps_graph_plotting_and_freezes_main_value(self):
        self.page.toggle_adc_realtime()
        self.finish_sample()
        scheduled = self.page._adc_realtime_job
        self.page.show_adc_graph()
        graph = self.page._adc_voltage_window
        self.assertEqual(self.page._adc_realtime_job, scheduled)
        self.tick()
        displayed = self.page.adc_value_var.get()
        self.page.toggle_adc_realtime()
        self.tick()
        self.assertEqual(self.page.adc_value_var.get(), displayed)
        self.assertEqual(graph.samples, [2, 3])
        self.assertTrue(self.page._adc_realtime_active)
        graph.on_close()
        self.assertFalse(self.page._adc_realtime_active)
        self.assertFalse(graph.exists)

    def test_closing_graph_keeps_numeric_stream_running(self):
        self.page.toggle_adc_realtime()
        self.finish_sample()
        self.page.show_adc_graph()
        graph = self.page._adc_voltage_window
        graph.on_close()
        self.assertTrue(self.page._adc_value_realtime_active)
        self.assertTrue(self.page._adc_realtime_active)
        self.tick()
        self.assertEqual(self.page.adc_value_var.get(), "0x0002 / 2")
        self.assertFalse(graph.exists)

    def test_graph_can_open_without_enabling_numeric_stream(self):
        self.page.show_adc_graph()
        graph = self.page._adc_voltage_window
        self.page._poll_adc_realtime()
        self.finish_sample()
        self.assertFalse(self.page._adc_value_realtime_active)
        self.assertEqual(self.page.adc_value_var.get(), "")
        self.assertEqual(graph.samples, [1])
        self.page.show_adc_graph()
        self.assertIs(self.page._adc_voltage_window, graph)

    def test_reply_from_stopped_stream_does_not_update_new_stream(self):
        self.page.toggle_adc_realtime()
        self.page.toggle_adc_realtime()
        self.page.toggle_adc_realtime()
        self.finish_sample()
        self.assertEqual(self.page.adc_value_var.get(), "")
        self.tick()
        self.assertEqual(self.page.adc_value_var.get(), "0x0002 / 2")


if __name__ == "__main__":
    unittest.main()
