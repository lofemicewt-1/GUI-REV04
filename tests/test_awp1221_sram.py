from __future__ import annotations

import unittest

import math

from core.awp1221_sram import (
    DATA_END,
    DATA_START,
    SHAPE_PEAK_CODE,
    SegmentRatioCapacityError,
    ZeroIntervalCandidate,
    allocate_segment_intervals,
    allocate_words,
    build_descriptor,
    compress_waveform,
    decompress_waveform,
    encode_val2,
    generate_smooth_points,
    generate_waveform,
    write_waveform,
)


class EncodeVal2Tests(unittest.TestCase):
    def test_non_negative_passthrough(self) -> None:
        self.assertEqual(encode_val2(0), 0)
        self.assertEqual(encode_val2(3), 3)

    def test_negative_uses_sign_bit(self) -> None:
        self.assertEqual(encode_val2(-1), 5)
        self.assertEqual(encode_val2(-3), 7)

    def test_out_of_range_raises(self) -> None:
        with self.assertRaises(ValueError):
            encode_val2(4)
        with self.assertRaises(ValueError):
            encode_val2(-4)


class WaveformRoundTripTests(unittest.TestCase):
    def test_compress_decompress_matches_centered_shape(self) -> None:
        points = generate_smooth_points([0.0, 2.0, 0.0], 18, centered=True)
        words, _differences = compress_waveform(points)
        self.assertEqual(decompress_waveform(words), points)
        self.assertEqual(len(words), 2 + (len(points) - 2) // 4)

    def test_generate_waveform_round_trips(self) -> None:
        result = generate_waveform([0.0, 100.0, 0.0], total_points=18, centered=True)
        self.assertEqual(decompress_waveform(result.words), list(result.points))
        self.assertGreaterEqual(len(result.words), 2)

    def test_invalid_point_count_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_waveform([0.0, 100.0], total_points=5, centered=True)


class AllocationTests(unittest.TestCase):
    def test_empty_occupied_starts_at_data_start(self) -> None:
        self.assertEqual(allocate_words(4, ()), DATA_START)

    def test_skips_occupied_range(self) -> None:
        start = allocate_words(4, ((DATA_START, DATA_START + 7),))
        self.assertEqual(start, DATA_START + 8)
        self.assertLessEqual(start + 3, DATA_END)

    def test_raises_when_sram_full(self) -> None:
        occupied = ((DATA_START, DATA_END),)
        with self.assertRaises(ValueError):
            allocate_words(4, occupied)


class DescriptorTests(unittest.TestCase):
    def test_descriptor_encodes_range_vpk_offset(self) -> None:
        words = build_descriptor(0x100, 8, vpk=9, offset=2)
        self.assertEqual(words[0], 0x400 | 0x100)
        self.assertEqual(words[1], 0x100 + 8 - 1)
        self.assertEqual(words[2], 0)
        self.assertEqual(words[3], (2 << 5) | 9)

    def test_descriptor_rejects_range_outside_sram_data(self) -> None:
        with self.assertRaises(ValueError):
            build_descriptor(0x0F0, 4, 0, 0)


class WriteWaveformTests(unittest.TestCase):
    def test_write_enters_access_mode_then_restores_play_mode(self) -> None:
        regs = {5: 0x442E, 0x14: 0x0000, 0x15: 0x0000}
        writes: list[tuple[int, int]] = []

        def read(reg: int) -> int:
            return regs[reg]

        def write(reg: int, value: int) -> bool:
            writes.append((reg, value))
            regs[reg] = value
            return True

        waveform = generate_waveform([0.0, 50.0, 0.0], total_points=6, centered=True)
        start, descriptor = write_waveform(
            read,
            write,
            wave_number=3,
            waveform=waveform,
            sample_time=1,
            vpk=1,
            offset=0,
            occupied=(),
            start_address=0x100,
        )
        self.assertEqual(start, 0x100)
        self.assertEqual(writes[0], (5, 0x442F))
        self.assertEqual(writes[-1], (5, 2))
        self.assertEqual(descriptor[0] & 0x3FF, 0x100)
        self.assertIn((0x15, 3 * 4), [(reg, value & 0x3FF) for reg, value in writes])


def _legacy_equal_points(control_values, total_points, peak_code=SHAPE_PEAK_CODE, centered=False):
    if centered:
        controls = [round(peak_code * (value + 100) / 200) for value in control_values]
    else:
        maximum = max(control_values)
        controls = [round(value / maximum * peak_code) if maximum else 0 for value in control_values]
    base, remainder = divmod(total_points - 1, len(controls) - 1)
    points = []
    for index, (start, end) in enumerate(zip(controls, controls[1:])):
        intervals = base + (index < remainder)
        points.extend(
            round(start + (end - start) * (1 - math.cos(math.pi * j / intervals)) / 2)
            for j in range(intervals)
        )
    return points + [controls[-1]]


class SegmentIntervalAllocationTests(unittest.TestCase):
    def test_single_segment_takes_every_interval(self) -> None:
        self.assertEqual(allocate_segment_intervals(18, (10000,)), (17,))
        self.assertEqual(allocate_segment_intervals(18, (1,)), (17,))

    def test_thirty_seventy_and_reverse_match_spec_examples(self) -> None:
        self.assertEqual(allocate_segment_intervals(102, (30, 70)), (30, 71))
        self.assertEqual(allocate_segment_intervals(102, (3000, 7000)), (30, 71))
        self.assertEqual(allocate_segment_intervals(102, (70, 30)), (71, 30))
        self.assertEqual(allocate_segment_intervals(102, (7000, 3000)), (71, 30))

    def test_twenty_fifty_thirty_matches_spec_example(self) -> None:
        self.assertEqual(allocate_segment_intervals(102, (20, 50, 30)), (20, 51, 30))
        self.assertEqual(allocate_segment_intervals(102, (2000, 5000, 3000)), (20, 51, 30))

    def test_tied_remainders_prefer_earlier_segment(self) -> None:
        self.assertEqual(allocate_segment_intervals(7, (2, 2, 1)), (3, 2, 1))

    def test_allocation_invariants(self) -> None:
        cases = (
            (18, (1,)),
            (102, (3000, 7000)),
            (102, (7000, 3000)),
            (102, (2000, 5000, 3000)),
            (26, (3000, 7000)),
            (34, (2000, 5000, 3000)),
        )
        for total_points, weights in cases:
            intervals = allocate_segment_intervals(total_points, weights)
            self.assertEqual(sum(intervals), total_points - 1)
            self.assertTrue(all(isinstance(interval, int) and interval >= 1 for interval in intervals))
            indices = [0]
            for interval in intervals:
                indices.append(indices[-1] + interval)
            self.assertEqual(indices[-1], total_points - 1)
            self.assertEqual(len(set(indices)), len(indices))

    def test_zero_interval_is_not_a_weight_error(self) -> None:
        with self.assertRaises(ZeroIntervalCandidate):
            allocate_segment_intervals(6, (1, 9999))

    def test_invalid_weights_are_rejected(self) -> None:
        for weights in ((), (0, 100), (-1, 101), (1.5, 98.5), (True, 99), ("30", "70")):
            with self.assertRaises(ValueError):
                allocate_segment_intervals(18, weights)


class SegmentWeightGenerationTests(unittest.TestCase):
    def test_omitted_weights_match_legacy_equal_split(self) -> None:
        cases = (
            ([0.0, 100.0, 0.0], 18, True),
            ([0.0, 100.0, -36.0, 0.0], 22, True),
            ([0.0, 50.0, 25.0, 0.0], 30, True),
            ([1.0, 4.0, 2.0, 8.0, 1.0], 26, False),
            ([10.0, 0.0], 6, False),
        )
        for values, total_points, centered in cases:
            self.assertEqual(
                generate_smooth_points(values, total_points, centered=centered),
                _legacy_equal_points(values, total_points, centered=centered),
            )
            result = generate_waveform(values, total_points=total_points, centered=centered)
            self.assertEqual(
                list(result.points),
                _legacy_equal_points(values, len(result.points), centered=centered),
            )
            self.assertEqual(
                result.segment_intervals,
                allocate_segment_intervals(len(result.points), (1,) * (len(values) - 1)),
            )

    def test_weighted_points_keep_shared_endpoints_once(self) -> None:
        values = (0.0, 50.0, 0.0)
        total_points = 102
        weights = (3000, 7000)
        points = generate_smooth_points(values, total_points, centered=True, segment_weights=weights)
        intervals = (30, 71)
        self.assertEqual(len(points), total_points)
        self.assertEqual(sum(intervals), total_points - 1)
        expected = [round(SHAPE_PEAK_CODE * (value + 100) / 200) for value in values]
        index = 0
        for control, interval in zip(expected, intervals):
            self.assertEqual(points[index], control)
            index += interval
        self.assertEqual(index, total_points - 1)
        self.assertEqual(points[index], expected[-1])
        self.assertEqual(len(points), total_points)

    def test_small_amplitude_boundary_codes_follow_requested_ratio(self) -> None:
        result = generate_waveform(
            [0.0, 2.0, 0.0],
            total_points=26,
            centered=True,
            segment_weights=(3000, 7000),
        )
        self.assertEqual(len(result.points), 26)
        self.assertEqual(result.segment_intervals, (8, 17))
        expected = [round(SHAPE_PEAK_CODE * (value + 100) / 200) for value in (0.0, 2.0, 0.0)]
        self.assertEqual(result.points[0], expected[0])
        self.assertEqual(result.points[8], expected[1])
        self.assertEqual(result.points[25], expected[2])
        self.assertEqual(decompress_waveform(result.words), list(result.points))
        self.assertTrue(all(-3 <= difference <= 3 for difference in result.differences))
        self.assertLessEqual(len(result.words), 256)

    def test_large_amplitude_search_reapplies_the_same_ratio(self) -> None:
        weights = (2000, 5000, 3000)
        result = generate_waveform(
            [0.0, 100.0, -36.0, 0.0],
            total_points=6,
            centered=True,
            segment_weights=weights,
        )
        self.assertGreater(len(result.points), 6)
        self.assertEqual(result.segment_intervals, allocate_segment_intervals(len(result.points), weights))
        self.assertEqual(decompress_waveform(result.words), list(result.points))
        self.assertTrue(all(-3 <= difference <= 3 for difference in result.differences))
        self.assertLessEqual(len(result.words), 256)
        start = allocate_words(len(result.words), ())
        descriptor = build_descriptor(start, len(result.words), vpk=1, offset=0)
        self.assertGreaterEqual(descriptor[0] & 0x3FF, DATA_START)
        self.assertLessEqual(descriptor[1], DATA_END)

    def test_bad_weights_fail_before_capacity_search(self) -> None:
        values = [0.0, 1.0, 0.0]
        cases = ((1,), (1, 2, 3), (0, 10000), (-5, 10005), (30.0, 70.0), (True, 9999))
        for weights in cases:
            with self.assertRaises(ValueError) as caught:
                generate_waveform(values, total_points=6, centered=True, segment_weights=weights)
            self.assertNotIsInstance(caught.exception, SegmentRatioCapacityError)

    def test_non_finite_controls_are_rejected(self) -> None:
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                generate_smooth_points([0.0, bad, 0.0], 18, centered=True)

    def test_tiny_ratio_reports_capacity_without_mutating_weights(self) -> None:
        weights = [1, 9999]
        with self.assertRaises(SegmentRatioCapacityError) as caught:
            generate_waveform([0.0, 1.0, 0.0], total_points=6, centered=True, segment_weights=weights)
        self.assertIn("分段比例", str(caught.exception))
        self.assertEqual(weights, [1, 9999])

    def test_compression_failure_keeps_ratio_and_reports_capacity(self) -> None:
        weights = [2000, 5000, 3000]
        with self.assertRaises(SegmentRatioCapacityError):
            generate_waveform(
                [0.0, 100.0, -36.0, 0.0],
                total_points=6,
                centered=True,
                segment_weights=weights,
                max_words=3,
            )
        self.assertEqual(weights, [2000, 5000, 3000])

    def test_three_argument_waveform_still_defaults_intervals(self) -> None:
        from core.awp1221_sram import SramWaveform

        waveform = SramWaveform((0, 1), (0, 0), (0,))
        self.assertEqual(waveform.segment_intervals, ())


if __name__ == "__main__":
    unittest.main()
