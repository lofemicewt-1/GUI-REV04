import math
import unittest

from core.awp192x_sine import plan_sine


class SinePlanTests(unittest.TestCase):
    def test_exact_rom_uses_largest_representable_depth(self):
        plan = plan_sine(59.4, 200)
        self.assertEqual((plan.source, plan.points, plan.point_us), ("rom", 8, 625))
        self.assertEqual(plan.reg14, (19 << 2) | (7 << 7) | 2)
        self.assertEqual(plan.reg15, 624)
        self.assertEqual(plan.actual_frequency, 200)

    def test_full_depth_rom_and_phase_half_cycle(self):
        plan = plan_sine(95.04, 1_000_000 / (1024 * 7))
        self.assertEqual((plan.source, plan.points, plan.reg14, plan.reg15), ("rom", 1024, 0x7E, 6))
        half = plan_sine(95.04, 1_000_000 / (512 * 7), start_phase=180, end_phase=360)
        self.assertEqual((half.source, half.points, half.reg14), ("rom", 512, 0x0C7E))

    def test_non_rom_voltage_uses_ram_and_exact_timing(self):
        plan = plan_sine(60, 200)
        self.assertEqual((plan.source, plan.points, plan.point_us, plan.reg14), ("ram", 1000, 5, 0x7E))
        self.assertEqual(plan.actual_frequency, 200)

    def test_ram_error_is_minimal_within_capacity(self):
        plan = plan_sine(60, 123, max_points=900)
        self.assertEqual((plan.points, plan.point_us), (813, 10))
        self.assertAlmostEqual(plan.actual_frequency, 1_000_000 / 8130)
        self.assertEqual(plan_sine(59.4, 200, offset=1).source, "ram")
        self.assertEqual(plan_sine(59.4, 200, start_phase=90, end_phase=450).source, "ram")

    def test_invalid_inputs_are_rejected(self):
        for voltage, frequency in ((math.nan, 200), (50, math.inf), (-1, 200), (96, 200), (60, 0), (60, -1), (60, 200000), (60, .0001), (59.4, 1e-308)):
            with self.subTest(voltage=voltage, frequency=frequency), self.assertRaises(ValueError):
                plan_sine(voltage, frequency)
        with self.assertRaises(ValueError):
            plan_sine(60, 200, max_points=4)

    def test_zero_fill_timing_includes_the_entire_remaining_ram(self):
        plan = plan_sine(60, 200, max_points=1022, fill_zero_tail=True)
        self.assertEqual((plan.source, plan.points, plan.total_points), ("ram", 1020, 1022))
        self.assertAlmostEqual(plan.actual_frequency, 1_000_000 / (1022 * plan.point_us))


if __name__ == "__main__":
    unittest.main()
