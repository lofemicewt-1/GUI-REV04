"""The combined ROM/RAM editor keeps SIN inputs and its write path consistent."""
import faulthandler
import unittest
from types import SimpleNamespace
from unittest.mock import patch, PropertyMock

from tests.validate_responsive_layout import _flush, mocked_application


class RomLayoutTests(unittest.TestCase):
    def test_auto_sine_inputs_remain_visible_and_register_routes_match(self):
        faulthandler.dump_traceback_later(60, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        with mocked_application() as app:
            app.set_animations(False)
            errors = []
            app.root.report_callback_exception = lambda *args: errors.append(str(args))
            app.root.geometry("1920x1080+0+0")
            app.navigation.show("awp1921_1923")
            host = app.navigation.pages["awp1921_1923"]
            host.show_mode("button_mode")
            _flush(app.root)
            page = host.mode_pages["button_mode"]
            wave = page.waveform_page
            self.assertEqual(set(wave.source_buttons), {"sinc", "ram"})
            self.assertEqual(wave.source_buttons["ram"].cget("text"), "ROM / RAM")
            for voltage, frequency, source in (("59.4", "200", "rom"), ("60", "200", "ram"),
                                                ("95.04", "0.9765625", "rom"), ("60", "123", "ram")):
                wave.source_buttons["ram"].invoke()
                wave.voltage_amplitude_var.set(voltage)
                wave.sine_frequency_var.set(frequency)
                _flush(app.root)
                self.assertTrue(wave.fields.winfo_ismapped())
                for key in ("voltage_amplitude", "frequency", "points"):
                    self.assertTrue(wave.cells[key].winfo_ismapped(), key)
                self.assertEqual(wave.source, source)
                self.assertEqual(page.play_waveform_var.get(), "0x1F" if source == "rom" else "slice0")
                self.assertTrue(wave.canvas.find_withtag("wave_curve"))
                plan = wave.get_sine_plan()
                operations = page._build_current_register_script_operations()
                values = {op.reg_addr: op.value for op in operations if op.action == "WRITE"}
                self.assertEqual(values[0x14], plan.reg14)
                self.assertEqual(values[0x15], plan.reg15)
                self.assertEqual(any(op.reg_addr == 0 for op in operations), source == "ram")
                if source == "ram":
                    self.assertEqual(len(wave._generate_current(use_imported=False)[0]), plan.points)
                self.assertTrue(str(wave._field_widgets["points"][1].cget("state")) == "readonly")

            wave.voltage_amplitude_var.set("59.4")
            wave.sine_frequency_var.set("200")
            writes = []
            class ImmediateThread:
                def __init__(self, target, daemon):
                    self.target = target
                def start(self):
                    self.target()
            with patch.object(type(app.ctx.serial), "is_connected", new_callable=PropertyMock, return_value=True), \
                 patch.object(app.ctx.serial, "write_register_value", side_effect=lambda dev, reg, value, **kw:
                    writes.append((reg, value)) or SimpleNamespace(ok=True)), \
                 patch("pages.awp192x_button_view.threading.Thread", ImmediateThread), \
                 patch("pages.awp192x_button_view.post_to_ui", side_effect=lambda _w, fn, *a: fn(*a)), \
                 patch("pages.awp192x_button_view.messagebox.showinfo"):
                wave.write_waveform()
            plan = wave.get_sine_plan()
            self.assertEqual(writes, [(0x14, plan.reg14), (0x15, plan.reg15)])
            writes.clear()
            with patch.object(type(app.ctx.serial), "is_connected", new_callable=PropertyMock, return_value=True), \
                 patch.object(app.ctx.serial, "write_register_value", side_effect=lambda dev, reg, value, **kw:
                    writes.append(reg) or SimpleNamespace(ok=False, error="write failed")), \
                 patch("pages.awp192x_button_view.threading.Thread", ImmediateThread), \
                 patch("pages.awp192x_button_view.post_to_ui", side_effect=lambda _w, fn, *a: fn(*a)), \
                 patch("pages.awp192x_button_view.messagebox.showerror") as error_dialog:
                wave.write_waveform()
            self.assertEqual(writes, [0x14])
            error_dialog.assert_called_once()
            self.assertFalse(wave._waveform_write_busy)

            metadata = page._parse_page_settings_metadata(page._prepend_page_settings_metadata(""))
            wave.voltage_amplitude_var.set("60")
            wave.sine_frequency_var.set("123")
            page._apply_page_settings_metadata(metadata)
            _flush(app.root)
            self.assertEqual(wave.source, "rom")
            self.assertEqual(wave.sine_frequency_var.get(), "200")

            page._capture_shared_settings()
            page._sync_awp192x_settings_from_model()
            self.assertEqual(wave.get_sine_plan().actual_frequency, 200)
            self.assertEqual(wave.get_sine_plan().points, 8)

            wave.voltage_amplitude_var.set("60")
            wave.sine_frequency_var.set("123")
            wave.fill_zero_tail_var.set(True)
            _flush(app.root)
            generated = wave._generate_current(use_imported=False)[0]
            self.assertEqual(len(generated), wave.effective_points())
            self.assertEqual(wave.get_sine_plan().total_points, 1022)
            wave.fill_zero_tail_var.set(False)

            registers = {reg: 0 for reg in page.CURRENT_SETTING_REGISTERS}
            registers.update({0x14: 0x03CE, 0x15: 624, 0x13: 0x001F})
            page._apply_current_settings_snapshot(registers, [], [])
            self.assertEqual(wave.get_sine_plan().actual_frequency, 200)
            self.assertEqual(wave.get_sine_plan().reg14, 0x03CE)

            wave.waveform_type_var.set("triangle")
            self.assertEqual(wave.source, "ram")
            self.assertEqual(app.ctx.state.current_max_output_reg_value, 0x007E)
            preset = page.BUTTON_PRESETS[0]
            page._load_preset_gui_values(preset)
            self.assertEqual(wave.waveform_type_var.get(), "sinc")
            self.assertEqual(int(page.duration_var.get()), preset["duration"])

            wave.adopt_sine_registers(0x0C7E, 6, rom_selected=True)
            self.assertEqual(wave.get_sine_plan().reg14, 0x0C7E)
            self.assertEqual((wave.start_phase_var.get(), wave.end_phase_var.get()), ("180", "360"))
            wave.sine_frequency_var.set("0")
            _flush(app.root)
            self.assertIn("disabled", wave._buttons["write_waveform"].state())
            with patch("pages.awp192x_modes.messagebox.showerror"):
                self.assertFalse(page._write_button_output_timing_config())
            self.assertFalse(errors, errors)


if __name__ == "__main__":
    unittest.main()
