from __future__ import annotations

from dataclasses import replace
import struct
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from contextlib import nullcontext

from core.arduino_preset_sync import (
    V2Preset, crc16_ccitt_false, format_preset_summary, pack_v2_preset, unpack_v2_preset,
)
from core.awp1221_preset import (
    Awp1221PresetModel, Awp1221WaveRecipe, allocate_awp1221_slices,
    awp1221_descriptor_words, pack_awp1221_payload, unpack_awp1221_payload,
)
from core.awp1221_sinc import Awp1221SincParams, build_awp1221_sinc_plan
from core.serial_manager import SerialManager, SerialResponse
from core.base_tool import build_cfg_begin_command, build_cfg_data_command


def model(*waves):
    return Awp1221PresetModel(7, 6, 5, 80.0 if any(w.kind >= 4 for w in waves) else 0.0,
                              12.0 if any(w.kind >= 4 for w in waves) else 0.0,
                              (0xFFF, 0x800), (0, len(waves) - 1), (0, 7), tuple(waves))


class Type4CodecTests(unittest.TestCase):
    def test_sin_and_shared_recipe_roundtrip(self):
        original = model(Awp1221WaveRecipe(0, 31, 3, 127, (256,), 256))
        payload = pack_awp1221_payload(original)
        self.assertEqual(len(payload), 36)
        self.assertEqual(unpack_awp1221_payload(payload), original)
        self.assertEqual(awp1221_descriptor_words(original), ((0, (0x8C0, 255, 255, 0xFFF)),))
        for address in range(0x50, 0x58):
            preset = V2Preset(4, 0x1221, address, payload)
            raw = pack_v2_preset(preset)
            self.assertEqual(len(raw), 256)
            self.assertEqual(unpack_v2_preset(raw), preset)

    def test_legacy_rom_records_remain_readable_but_cannot_be_newly_saved(self):
        sin = bytearray(pack_awp1221_payload(model(Awp1221WaveRecipe(0, 31, 3, 127, (256,), 256))))
        del sin[21]
        sin[0] = 1
        for kind in (1, 2, 3):
            payload = bytearray(sin)
            payload[21] = kind  # Existing v1 bytes, with the original ROM kind.
            original = model(Awp1221WaveRecipe(kind, 31, 3, 127, (256,), 256))
            with self.subTest(kind=kind):
                self.assertEqual(unpack_awp1221_payload(payload), original)
                self.assertEqual(awp1221_descriptor_words(original),
                                 ((0, (0x8C0 | (kind << 3), 255, 255, 0xFFF)),))
                record = pack_v2_preset(V2Preset(4, 0x1221, 0x50, bytes(payload)))
                self.assertEqual(unpack_v2_preset(record).payload, payload)
                with self.assertRaisesRegex(ValueError, "only SIN and SINC"):
                    pack_awp1221_payload(original)

    def test_sinc_and_serial34_match_current_generation(self):
        waves = (Awp1221WaveRecipe(4, times=(4, 7, 256)),
                 Awp1221WaveRecipe(5, times=(5, 9, 11, 13), repeat=256))
        original = model(*waves)
        self.assertEqual(unpack_awp1221_payload(pack_awp1221_payload(original)), original)
        self.assertEqual(len(pack_awp1221_payload(original)), 50)
        actual = awp1221_descriptor_words(original)
        expected = []
        for wave, serial in zip(waves, ("serial1", "serial3")):
            plan = build_awp1221_sinc_plan(Awp1221SincParams(80, 12, wave.times, 512, wave.repeat), serial=serial)
            for i, start in enumerate(plan.slices):
                expected.append((start, (*plan.descriptor_words[i], (plan.offset_codes[i] << 5) | plan.vpk_codes[i])))
        self.assertEqual(actual, tuple(expected))

    def test_two_distinct_sinc_groups_and_combined_groups_allocate(self):
        for kind, times, starts in ((4, (1, 2, 3), (18, 21)), (5, (1, 2, 3, 4), (24, 28))):
            waves = (Awp1221WaveRecipe(kind, times=times), Awp1221WaveRecipe(kind, times=tuple(t + 1 for t in times)))
            self.assertEqual(allocate_awp1221_slices(waves), starts)
            self.assertEqual(unpack_awp1221_payload(pack_awp1221_payload(model(*waves))).waves, waves)

    def test_malformed_or_unused_recipe_is_rejected(self):
        valid = model(Awp1221WaveRecipe(4, times=(1, 2, 3)))
        for invalid in (replace(valid, wave_refs=(1, 0)), replace(valid, peak=float("nan")),
                        replace(valid, peak=1e40),
                        replace(valid, trough=0.5), replace(valid, thresholds=(4096, 0)),
                        replace(valid, waves=valid.waves * 2), replace(valid, event_repeats=(8, 0))):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                pack_awp1221_payload(invalid)
        raw = bytearray(pack_awp1221_payload(valid))
        raw[0] = 0
        with self.assertRaises(ValueError):
            unpack_awp1221_payload(raw)
        raw = bytearray(pack_v2_preset(V2Preset(4, 0x1221, 0x50, pack_awp1221_payload(valid))))
        raw[12] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC"):
            unpack_v2_preset(raw)
        with self.assertRaises(ValueError):
            unpack_v2_preset(raw[:128])

    def test_new_save_stores_both_stage_jumps_in_v2(self):
        original = replace(model(Awp1221WaveRecipe(0, 31, 3, 127, (256,), 256)), jumps=(0, 1))
        payload = pack_awp1221_payload(original)
        self.assertEqual(payload[0], 2)
        self.assertEqual(len(payload), 36)
        self.assertEqual(payload[21], 0x02)
        self.assertEqual(unpack_awp1221_payload(payload), original)
        two = replace(model(Awp1221WaveRecipe(0), Awp1221WaveRecipe(0, 1)), jumps=(1, 1))
        self.assertEqual(len(pack_awp1221_payload(two)), 50)
        self.assertEqual(unpack_awp1221_payload(pack_awp1221_payload(two)).jumps, (1, 1))

    def test_every_jump_combination_roundtrips(self):
        for jumps in ((0, 0), (1, 0), (0, 1), (1, 1)):
            original = replace(model(Awp1221WaveRecipe(0)), jumps=jumps)
            with self.subTest(jumps=jumps):
                self.assertEqual(unpack_awp1221_payload(pack_awp1221_payload(original)), original)

    def test_legacy_v1_record_decodes_mutual_jump_and_resave_is_v2(self):
        raw = bytes.fromhex("01010706050000000000000000ff0f000800000007001f037f00010000000000000001")
        decoded = unpack_awp1221_payload(raw)
        self.assertEqual(decoded.jumps, (1, 0))
        self.assertEqual(decoded.waves[0].kind, 0)
        saved = pack_awp1221_payload(decoded)
        self.assertEqual(saved[0], 2)
        self.assertEqual(saved[21], 0x01)
        self.assertEqual(unpack_awp1221_payload(saved).jumps, (1, 0))

    def test_bad_jump_version_or_length_is_rejected(self):
        valid = bytearray(pack_awp1221_payload(replace(model(Awp1221WaveRecipe(0)), jumps=(1, 0))))
        self.assertEqual(valid[0], 2)
        valid[0] = 3
        with self.assertRaises(ValueError):
            unpack_awp1221_payload(valid)
        valid[0] = 2
        valid[21] = 4
        with self.assertRaises(ValueError):
            unpack_awp1221_payload(valid)
        with self.assertRaises(ValueError):
            unpack_awp1221_payload(valid[:21])
        with self.assertRaises(ValueError):
            pack_awp1221_payload(replace(model(Awp1221WaveRecipe(0)), jumps=(2, 0)))

    def test_summary_names_version_and_saved_jumps(self):
        original = replace(model(Awp1221WaveRecipe(0)), jumps=(0, 1))
        raw = pack_v2_preset(V2Preset(4, 0x1221, 0x50, pack_awp1221_payload(original)))
        text = format_preset_summary(raw)
        self.assertIn("Type-4 v2", text)
        self.assertIn("Stage0/1 jumps: Stage0, Stage1", text)

    def test_firmware_ieee_quantization_matches_solver(self):
        # Check inward quantization at exact steps and their adjacent float32 values.
        for k in range(1, 65):
            bits = struct.unpack("<I", struct.pack("<f", k * 1.5625))[0]
            for value_bits in (bits - 1, bits, bits + 1):
                value = struct.unpack("<f", struct.pack("<I", value_bits))[0]
                if value > 100 or value < 1.5625:
                    continue
                exponent = (value_bits >> 23) & 255
                firmware_steps = ((value_bits & 0x7FFFFF) | 0x800000) // (25 << (146 - exponent))
                plan = build_awp1221_sinc_plan(Awp1221SincParams(value, 12, (1, 2, 3), 512), serial="serial1")
                self.assertTrue(plan.writable)
                self.assertEqual(firmware_steps, plan.solution.segments[0].k)


class Type4TransportTests(unittest.TestCase):
    def test_full_256_upload_crosses_offset_127_and_never_activates(self):
        raw = pack_v2_preset(V2Preset(4, 0x1221, 0x50, pack_awp1221_payload(model(Awp1221WaveRecipe(0)))))
        manager = SerialManager()
        events = []
        transfer = f"CFGREAD 2 {raw.hex()} CRC={crc16_ccitt_false(raw):04X}"
        def response(name, *args):
            events.append((name, *args))
            return SerialResponse(True, data=transfer if name == "read" else "OK")
        with patch.object(manager, "cfg_begin", side_effect=lambda *a: response("begin", *a)), \
             patch.object(manager, "cfg_data", side_effect=lambda *a: response("data", *a)), \
             patch.object(manager, "cfg_end", side_effect=lambda *a: response("end", *a)), \
             patch.object(manager, "cfg_read", side_effect=lambda *a: response("read", *a)), \
             patch.object(manager, "cfg_use", side_effect=lambda *a: response("use", *a)):
            result = manager.sync_config_preset(2, raw, set_active=False)
        self.assertTrue(result.ok, result.error)
        self.assertTrue(any(name == "data" and args[0] > 127 for name, *args in events))
        self.assertFalse(any(event[0] == "use" for event in events))
        self.assertEqual(build_cfg_begin_command(2, 256, 0xABCD), "CFGBEGIN 2 256 ABCD")
        self.assertEqual(build_cfg_data_command(255, "FF"), "CFGDATA 255 FF")
        self.assertIsNone(manager._extract_cfgread_hex(transfer[:-4] + "0000"))


class Type4PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.validate_responsive_layout import mocked_application
        cls.fixture = mocked_application()
        cls.app = cls.fixture.__enter__()
        cls.page = cls.app.navigation.pages["awp1221"].mode_pages["button_mode"]
        cls.initial_debounce = tuple(v["debounce"].get() for v in cls.page._button_stage_vars)
        cls.original_ctx = cls.page.ctx

    @classmethod
    def tearDownClass(cls):
        cls.page.ctx = cls.original_ctx
        cls.fixture.__exit__(None, None, None)

    def setUp(self):
        self.page.ctx = self.original_ctx
        self.page._board_preset_busy = False

    def test_rom_and_sinc_readback_roundtrip_and_excluded_fields_survive(self):
        page = self.page
        original = model(Awp1221WaveRecipe(0, 31, 3, 127, (256,), 256),
                         Awp1221WaveRecipe(5, times=(5, 7, 9, 11), repeat=256))
        page.debounce_time_var.set("deliberately invalid: excluded")
        for variables in page._button_stage_vars:
            variables["debounce"].set(True)
        page._restore_type4_model(original)
        self.assertEqual(page.debounce_time_var.get(), "deliberately invalid: excluded")
        self.assertTrue(all(not v["debounce"].get() for v in page._button_stage_vars))
        self.assertEqual(page.rom_waveform_var.get(), "SINE")
        self.assertEqual(page.rom_depth_var.get(), "512")
        self.assertEqual(page.rom_repeat_var.get(), "256")
        self.assertEqual(pack_awp1221_payload(page._capture_type4_model()), pack_awp1221_payload(original))

    def test_capture_and_restore_use_stage_jump_targets(self):
        page = self.page
        page._restore_type4_model(replace(model(Awp1221WaveRecipe(0)), jumps=(0, 0)))
        self.assertEqual([v["jump_target"].get() for v in page._button_stage_vars], ["Stage0", "Stage0"])
        page._button_stage_vars[0]["jump_target"].set("Stage1")
        page._button_stage_vars[1]["jump_target"].set("Stage1")
        self.assertEqual(page._capture_type4_model().jumps, (1, 1))
        with self.assertRaises(ValueError):
            page._button_stage_vars[0]["jump_target"].set("Stage2")
            page._capture_type4_model()

    def test_debounce_controls_are_excluded_from_type4_record(self):
        page = self.page
        page._restore_type4_model(model(Awp1221WaveRecipe(0)))
        before = pack_awp1221_payload(page._capture_type4_model())
        for variables in page._button_stage_vars:
            variables["debounce"].set(True)
        self.assertEqual(pack_awp1221_payload(page._capture_type4_model()), before)
        page._restore_type4_model(unpack_awp1221_payload(before))
        self.assertTrue(all(not v["debounce"].get() for v in page._button_stage_vars))

    def test_button_software_presets_default_to_debounce_off(self):
        page = self.page
        for preset in page.BUTTON_SOFTWARE_PRESETS:
            for variables in page._button_stage_vars:
                variables["debounce"].set(True)
            page._load_button_software_preset(preset)
            self.assertTrue(all(not v["debounce"].get() for v in page._button_stage_vars))

    def test_initial_button_debounce_controls_are_off(self):
        self.assertEqual(self.initial_debounce, (False, False))

    def test_current_unapplied_edits_and_two_rom_slices_are_captured(self):
        page = self.page
        page._restore_type4_model(model(Awp1221WaveRecipe(0)))
        page.rom_slice_var.set("slice0")
        page.rom_waveform_var.set("SINE")
        page.rom_point_time_var.set("7")
        page.rom_slice_var.set("slice1")
        page.rom_waveform_var.set("SINE")
        page.rom_point_time_var.set("19")
        page._button_stage_vars[0]["wave"].set("slice0")
        page._button_stage_vars[1]["wave"].set("slice1")
        actual = page._capture_type4_model()
        self.assertEqual(tuple(w.kind for w in actual.waves), (0, 0))
        self.assertEqual(tuple(w.times for w in actual.waves), ((7,), (19,)))
        page.rom_slice_var.set("slice0")
        self.assertEqual(page.rom_waveform_var.get(), "SINE")
        self.assertEqual(page.rom_point_time_var.get(), "7")

    def test_excluded_rom_kind_in_either_stage_prevents_entire_save(self):
        page = self.page
        for stage in (0, 1):
            for kind in ("Gauss", "Fixed-slope Ramp", "DC"):
                with self.subTest(stage=stage, kind=kind):
                    page._restore_type4_model(model(Awp1221WaveRecipe(0)))
                    page.rom_slice_var.set("slice1")
                    page.rom_waveform_var.set(kind)
                    page._button_stage_vars[stage]["wave"].set("slice1")
                    with self.assertRaisesRegex(ValueError, kind):
                        page._capture_type4_model()

    def test_legacy_rom_readback_restores_editor_without_new_save(self):
        page = self.page
        for kind, name in ((1, "Gauss"), (2, "Fixed-slope Ramp"), (3, "DC")):
            original = model(Awp1221WaveRecipe(kind, 31, 3, 127, (256,), 256))
            page._restore_type4_model(original)
            self.assertEqual(page.rom_waveform_var.get(), name)
            with self.assertRaisesRegex(ValueError, name):
                page._capture_type4_model()

    def test_sinc_readback_preserves_two_groups_and_shared_voltage(self):
        page = self.page
        for kind, times in ((4, (4, 7, 9)), (5, (5, 9, 11, 13))):
            original = model(Awp1221WaveRecipe(kind, times=times),
                             Awp1221WaveRecipe(kind, times=tuple(t + 1 for t in times)))
            page._restore_type4_model(original)
            self.assertEqual(pack_awp1221_payload(page._capture_type4_model()), pack_awp1221_payload(original))

    def test_referenced_ram_slice_is_rejected_without_reading_samples(self):
        page = self.page
        page._restore_type4_model(model(Awp1221WaveRecipe(0)))
        page.rom_source_var.set("RAM")
        with self.assertRaisesRegex(ValueError, "RAM"):
            page._capture_type4_model()

    def test_four_board_actions_have_distinct_side_effects(self):
        from pages import awp1221_page
        page = self.page
        original = model(Awp1221WaveRecipe(4, times=(4, 7, 9)))
        page._restore_type4_model(original)
        raw = pack_v2_preset(V2Preset(4, 0x1221, page.device_addr(), pack_awp1221_payload(original)))
        calls = []
        def read(slot):
            calls.append("read")
            return SerialResponse(True, data=f"CFGREAD {slot} {raw.hex()} CRC={crc16_ccitt_false(raw):04X}")
        def response(name):
            calls.append(name)
            return SerialResponse(True, data="OK")
        def upload(slot, encoded, *, set_active):
            self.assertEqual(encoded, raw)
            self.assertFalse(set_active)
            return response("save")
        serial = SimpleNamespace(is_connected=True, cfg_read=read, cfg_use=lambda slot: response("use"),
                                 cfg_apply=lambda slot: response("apply"), sync_config_preset=upload,
                                 _extract_cfgread_hex=SerialManager()._extract_cfgread_hex,
                                 snapshot_transaction=nullcontext)
        page.ctx = SimpleNamespace(**{**vars(self.original_ctx), "serial": serial})
        def synchronous(widget, worker, *, on_done, on_error):
            try:
                result = worker()
            except Exception as exc:
                on_error(exc)
            else:
                on_done(result)
        with patch.object(awp1221_page, "_run_ui_background_task", synchronous), \
             patch("pages.awp1221_board_presets.messagebox.showinfo"), \
             patch("pages.awp1221_board_presets.messagebox.showerror") as error:
            for key, expected in (("save_to_board", ["save"]), ("set_active", ["read", "use"]),
                                  ("read_slot", ["read", "apply"]), ("apply_slot", ["read", "apply"])):
                calls.clear()
                page._buttons[key].invoke()
                error.assert_not_called()
                self.assertEqual(calls, expected)
                self.assertFalse(page._board_preset_busy)


if __name__ == "__main__":
    unittest.main()
