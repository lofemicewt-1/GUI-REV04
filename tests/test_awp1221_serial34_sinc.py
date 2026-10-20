from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.awp1221_sinc import (
    awp1221_sinc_parse_params,
    awp1221_sinc_plan_key,
    awp1221_sinc_write_words,
    build_awp1221_sinc_plan,
)
from pages.awp1221_page import (
    AWP1221ModePage,
    AWP1221_SINC_SERIAL_OPTIONS,
    awp1221_sine_preview_path,
)


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Canvas:
    def __init__(self):
        self.calls = []

    def delete(self, *args):
        self.calls.clear()

    def winfo_width(self):
        return 1000

    def winfo_height(self):
        return 400

    def create_rectangle(self, *coords, **kwargs):
        self.calls.append(("rectangle", coords, kwargs))

    def create_line(self, *coords, **kwargs):
        self.calls.append(("line", coords, kwargs))

    def create_text(self, *coords, **kwargs):
        self.calls.append(("text", coords, kwargs))


def _draw_canvas_data(plan):
    page = SimpleNamespace(
        ctx=SimpleNamespace(colors={}),
        rom_canvas=_Canvas(),
        rom_metric_var=_Value(""),
        rom_frequency_var=_Value(""),
        _build_current_sinc_plan=lambda: plan,
        _sinc_reason_message=lambda reason: reason,
        tr=lambda key, **kwargs: key.format(**kwargs),
    )
    AWP1221ModePage._draw_sinc_preview(page)
    waveform = tuple(value for kind, coords, options in page.rom_canvas.calls
                     if kind == "line" and options.get("tags") == "wave_curve" for value in coords)
    points = list(zip(waveform[::2], waveform[1::2]))
    return points


def _params(times, depth="512", repeat="1"):
    return awp1221_sinc_parse_params(
        "80", "12", str(times[0]), str(times[1]), str(times[2]), depth,
        str(times[3]) if len(times) == 4 else None,
        str(repeat),
    )


class Awp1221Serial34SincTests(unittest.TestCase):
    def test_canvas_four_slice_endpoints_and_repeated_sine_returns_to_zero(self):
        for repeat in (1, 2, 256):
            with self.subTest(repeat=repeat):
                plan = build_awp1221_sinc_plan(
                    _params((4, 7, 8, 9), repeat=str(repeat)), serial="serial3"
                )
                points = _draw_canvas_data(plan)
                sine_samples = {1: 54, 2: 108, 256: 108}[repeat]
                segment_starts = (0, sine_samples, sine_samples + 54, sine_samples + 108)
                segment_ends = tuple(index + count - 1 for index, count in zip(segment_starts, (sine_samples, 54, 54, 54)))
                middle = 200.0
                peak = max(plan.solution.peak_volts, plan.solution.trough_volts, 1.0)
                scale = (400 - 24 - 24) * 0.38 / peak

                def actual_voltage(index):
                    return (middle - points[index][1]) / scale

                self.assertAlmostEqual(actual_voltage(segment_starts[0]), 0.0, places=6)
                self.assertAlmostEqual(actual_voltage(segment_ends[0]), 0.0, places=6)
                self.assertAlmostEqual(actual_voltage(segment_starts[1]), 0.0, places=6)
                self.assertAlmostEqual(actual_voltage(segment_ends[1]), plan.solution.peak_volts, places=5)
                self.assertAlmostEqual(actual_voltage(segment_starts[2]), plan.solution.peak_volts, places=5)
                self.assertAlmostEqual(actual_voltage(segment_ends[2]), -plan.solution.trough_volts, places=5)
                self.assertAlmostEqual(actual_voltage(segment_starts[3]), -plan.solution.trough_volts, places=5)
                self.assertAlmostEqual(actual_voltage(segment_ends[3]), 0.0, places=6)
                self.assertAlmostEqual(points[-1][1], 200.0, places=6)

    def test_canvas_legacy_segments_fill_time_axis_and_preserve_continuity(self):
        plan = build_awp1221_sinc_plan(_params((4, 8, 16)), serial="serial1")
        points = _draw_canvas_data(plan)
        left, right = 48.0, 980.0
        end_indices = (53, 107, 161)
        expected_x = (left + 932 * (1 / 7), left + 932 * (3 / 7), right)
        for index, expected in zip(end_indices, expected_x):
            self.assertAlmostEqual(points[index][0], expected, places=6)
        self.assertAlmostEqual(points[-1][0], right, places=6)
        self.assertAlmostEqual(points[53][1], points[54][1], places=6)
        self.assertAlmostEqual(points[107][1], points[108][1], places=6)
        self.assertAlmostEqual(points[161][1], 200.0, places=6)

    def test_depth_hint_distinguishes_full_sine_from_half_cycle_segments(self):
        for serial, option_index, times in (
            ("serial3", 2, (4, 7, 8, 9)),
            ("serial4", 3, (4, 7, 8, 9)),
            ("serial1", 0, (4, 7, 8)),
        ):
            for depth in ("512", "256"):
                with self.subTest(serial=serial, depth=depth):
                    expected = (
                        f"First SIN: {depth} points; each later SINC: {int(depth) // 2} points"
                        if serial in {"serial3", "serial4"}
                        else f"Actual points per segment: {int(depth) // 2}"
                    )
                    hint = SimpleNamespace(text=None)
                    hint.configure = lambda **kwargs: setattr(hint, "text", kwargs["text"])
                    plan = build_awp1221_sinc_plan(_params(times, depth=depth), serial=serial)
                    page = SimpleNamespace(
                        _selector_widgets={"awp1221_sinc_depth_hint": [hint]},
                        _build_current_sinc_plan=lambda: plan,
                        sinc_serial_var=_Value(AWP1221_SINC_SERIAL_OPTIONS[option_index]),
                        _sinc_depth_value=lambda: depth,
                        tr=lambda key, **kwargs: {
                            "awp1221_sinc_depth_hint_four": "First SIN: {sin_points} points; each later SINC: {sinc_points} points",
                            "awp1221_sinc_depth_hint": "Actual points per segment: {points}",
                        }[key].format(**kwargs),
                    )
                    AWP1221ModePage._refresh_sinc_depth_hint(page)
                    self.assertEqual(hint.text, expected)

    def test_serial34_builds_full_sine_then_three_sinc_slices(self):
        for serial, first_slice in (("serial3", 0x18), ("serial4", 0x1C)):
            for depth in ("512", "256"):
                with self.subTest(serial=serial, depth=depth):
                    times = (4, 7, 8, 9)
                    plan = build_awp1221_sinc_plan(_params(times, depth), serial=serial)
                    sinc = build_awp1221_sinc_plan(
                        _params(times[1:], depth), serial="serial1"
                    )
                    self.assertTrue(plan.writable)
                    self.assertEqual(plan.slices, tuple(range(first_slice, first_slice + 4)))
                    self.assertEqual(
                        plan.sram_words,
                        tuple(first_slice * 4 + offset for offset in range(16)),
                    )
                    self.assertEqual(len(awp1221_sinc_write_words(plan, {})), 16)
                    self.assertEqual(
                        plan.descriptor_words[0],
                        (
                            0b10 << 10
                            | plan.solution.segments[0].fine_code << 6
                            | plan.depth_code << 5,
                            times[0] - 1,
                            0,
                        ),
                    )
                    self.assertEqual(plan.descriptor_words[1:], sinc.descriptor_words)
                    self.assertEqual(plan.offset_codes[0], plan.solution.segments[0].offset_code)
                    self.assertEqual(plan.vpk_codes[0], plan.solution.segments[0].coarse_code)
                    self.assertEqual(plan.half_cycle_codes, (0, 0b01, 0b10, 0b01))
                    expected_duration = int(depth) * times[0] + int(depth) // 2 * sum(times[1:])
                    self.assertEqual(plan.duration_us, expected_duration)
        self.assertEqual(awp1221_sine_preview_path(0, 0), (-90.0, 360.0))

    def test_first_time_changes_plan_key_and_frequency_basis(self):
        before = build_awp1221_sinc_plan(_params((4, 7, 8, 9)), serial="serial3")
        after = build_awp1221_sinc_plan(_params((5, 7, 8, 9)), serial="serial3")
        self.assertNotEqual(
            awp1221_sinc_plan_key(before, 0x50), awp1221_sinc_plan_key(after, 0x50)
        )
        self.assertEqual(after.descriptor_words[0][1], 4)
        self.assertEqual(after.duration_us - before.duration_us, 512)

    def test_serial34_requires_four_times(self):
        for serial in ("serial3", "serial4"):
            with self.subTest(serial=serial):
                plan = build_awp1221_sinc_plan(_params((4, 7, 8)), serial=serial)
                self.assertFalse(plan.writable)
                self.assertEqual(plan.reason, "four_times_required")

    def test_sin_repeat_count_limits_descriptor_and_total_duration(self):
        for repeat in (1, 2, 256):
            with self.subTest(repeat=repeat):
                plan = build_awp1221_sinc_plan(
                    _params((4, 7, 8, 9), repeat=str(repeat)), serial="serial3"
                )
                self.assertTrue(plan.writable)
                self.assertEqual(plan.params.sin_repeat_count, repeat)
                self.assertEqual(plan.descriptor_words[0][2], repeat - 1)
                self.assertEqual(
                    plan.duration_us,
                    512 * 4 * repeat + 256 * (7 + 8 + 9),
                )

    def test_illegal_sin_repeat_counts_are_rejected(self):
        for repeat in ("0", "257", "1.5", "many"):
            with self.subTest(repeat=repeat), self.assertRaises(ValueError):
                _params((4, 7, 8, 9), repeat=repeat)

    def test_serial12_keeps_legacy_three_slice_descriptors(self):
        plan = build_awp1221_sinc_plan(_params((4, 7, 8)), serial="serial1")
        self.assertTrue(plan.writable)
        self.assertEqual(plan.slices, (0x12, 0x13, 0x14))
        self.assertEqual(len(plan.descriptor_words), 3)
        self.assertEqual(len(awp1221_sinc_write_words(plan, {})), 12)

    def test_page_plan_uses_four_physical_slice_times(self):
        for serial, expected_start in (("serial3", 0x18), ("serial4", 0x1C)):
            with self.subTest(serial=serial):
                page = SimpleNamespace(
                    sinc_serial_var=_Value(serial),
                    sinc_max_voltage_var=_Value("80"),
                    sinc_min_voltage_var=_Value("12"),
                    sinc_depth_var=_Value("512"),
                    sinc_time_vars=[_Value(value) for value in ("4", "7", "8", "9")],
                    sinc_sin_repeat_var=_Value("1"),
                )
                page._sinc_time_variables = lambda: page.sinc_time_vars
                page._sinc_depth_value = lambda: page.sinc_depth_var.get()
                page._sinc_window_index = lambda _serial: 0
                plan = AWP1221ModePage._build_current_sinc_plan(page)
                self.assertEqual(plan.params.times_us, (4, 7, 8, 9))
                self.assertEqual(plan.slices[0], expected_start)
                self.assertEqual(plan.slices[-1], expected_start + 3)

    def test_playback_key_tracks_first_slice_time(self):
        page = SimpleNamespace(
            _shared_state_owner=None,
            _sinc_verified_key=None,
            _auto_serial_enabled=lambda: True,
            device_addr=lambda: 0x50,
            _shared_state_for=lambda: None,
        )
        before = build_awp1221_sinc_plan(_params((4, 7, 8, 9)), serial="serial3")
        page._build_current_sinc_plan = lambda: before
        page._sinc_verified_key = awp1221_sinc_plan_key(before, 0x50)
        self.assertTrue(AWP1221ModePage._sinc_play_allowed(page))

        after = build_awp1221_sinc_plan(_params((5, 7, 8, 9)), serial="serial3")
        page._build_current_sinc_plan = lambda: after
        self.assertFalse(AWP1221ModePage._sinc_play_allowed(page))

    def test_disconnect_and_write_error_clear_sinc_eligibility(self):
        invalidations = []
        page = SimpleNamespace(
            _shared_state_owner=SimpleNamespace(
                invalidate_all=lambda **kwargs: invalidations.append(kwargs)
            ),
            _sinc_verified_key=("stale",),
            _auto_serial_enabled=lambda: True,
            _build_current_sinc_plan=lambda: build_awp1221_sinc_plan(
                _params((4, 7, 8, 9)), serial="serial3"
            ),
            _shared_state_for=lambda: SimpleNamespace(sinc_key=("stale",)),
            device_addr=lambda: 0x50,
            ctx=SimpleNamespace(serial=SimpleNamespace(is_connected=False)),
            _set_waveform_write_busy=lambda _busy: None,
            tr=lambda key: key,
        )
        self.assertFalse(AWP1221ModePage._sinc_play_allowed(page))
        self.assertEqual(invalidations, [{"clear_active": True}])
        with patch("pages.awp1221_page.messagebox.showerror") as showerror:
            AWP1221ModePage._finish_sinc_write(
                page,
                "SRAM write failed",
                build_awp1221_sinc_plan(_params((4, 7, 8, 9)), serial="serial3"),
                0x50,
            )
        self.assertIsNone(page._sinc_verified_key)
        showerror.assert_called_once()

    def test_software_preset_load_preserves_all_serial34_times(self):
        page = SimpleNamespace(
            auto_serial_var=_Value(False),
            rom_waveform_var=_Value("SINE"),
            sinc_serial_var=_Value("serial1"),
            sinc_depth_var=_Value("256"),
            sinc_max_voltage_var=_Value("50"),
            sinc_min_voltage_var=_Value("5"),
            sinc_time_vars=[_Value("1") for _ in range(4)],
            sinc_sin_repeat_var=_Value("1"),
            _sinc_active_group=None,
            _sinc_group_values={},
            _sinc_loading_group=False,
            _button_stage_vars=({"mode": _Value("")},),
            debounce_time_var=_Value("0"),
            adc_sample_update_var=_Value("4 us"),
            zero_time_var=_Value("1 ms"),
            press_return_bit_time_var=_Value("off"),
            release_return_bit_time_var=_Value("off"),
            zps_enabled_var=_Value(False),
            zps_detect_var=_Value(False),
            sleep_sensitivity_var=_Value(False),
        )
        page._sinc_group_snapshot = lambda: tuple(
            variable.get() for variable in page.sinc_time_vars
        )
        page._sinc_time_variables = lambda: tuple(page.sinc_time_vars)
        page._sync_auto_serial_page_state = lambda: None
        preset = {
            "waveform": {
                "auto_serial": True,
                "serial": "serial3",
                "waveform": "SINC",
                "depth": "512",
                "max_voltage": "80",
                "min_voltage": "12",
                "times_us": ("4", "7", "8", "9"),
                "sin_repeat": 2,
            },
            "stages": ({"mode": "press"},),
            "settings": {
                "debounce_time": "100",
                "sample_update": "4 us",
                "zero_time": "32 ms",
                "press_return_bit_time": "1600 us",
                "release_return_bit_time": "1600 us",
                "zps_enabled": True,
                "zps_detect": True,
                "sleep_sensitivity": False,
            },
        }
        AWP1221ModePage._load_button_software_preset(page, preset)
        self.assertEqual(
            tuple(variable.get() for variable in page.sinc_time_vars),
            ("4", "7", "8", "9"),
        )
        self.assertEqual(page._sinc_active_group, "serial3")
        self.assertEqual(page.sinc_sin_repeat_var.get(), "2")
        del preset["waveform"]["sin_repeat"]
        AWP1221ModePage._load_button_software_preset(page, preset)
        self.assertEqual(page.sinc_sin_repeat_var.get(), "1")
