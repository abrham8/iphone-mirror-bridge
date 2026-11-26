import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from hid_report import HotkeyDetector, MouseState, ReportAccumulator, build_hid_report


def decode(report):
    def s8(b):
        return b - 256 if b > 127 else b
    return report[0], s8(report[1]), s8(report[2]), s8(report[3])


class HidReportTests(unittest.TestCase):
    def test_zero_report(self):
        self.assertEqual(build_hid_report(0), bytes([0, 0, 0, 0]))

    def test_button_bits_are_limited_to_three_buttons(self):
        self.assertEqual(build_hid_report(0xFF)[0], 0x07)

    def test_signed_motion_is_twos_complement(self):
        self.assertEqual(build_hid_report(1, -1, 2, -3), bytes([1, 255, 2, 253]))

    def test_motion_is_clamped_to_signed_byte_range(self):
        self.assertEqual(build_hid_report(0, -1000, 1000, 500), bytes([0, 129, 127, 127]))

    def test_state_report_resets_motion_but_keeps_buttons(self):
        state = MouseState(buttons=1, dx=5, dy=-4, wheel=1)
        self.assertEqual(state.report(), bytes([1, 5, 252, 1]))
        self.assertEqual(state.report(), bytes([1, 0, 0, 0]))


class ReportAccumulatorTests(unittest.TestCase):
    def test_nothing_pending_produces_no_reports(self):
        self.assertEqual(ReportAccumulator().drain(), [])

    def test_motion_is_coalesced(self):
        acc = ReportAccumulator()
        acc.add_motion(dx=3)
        acc.add_motion(dx=4, dy=-2)
        acc.add_motion(wheel=1)
        self.assertEqual([decode(r) for r in acc.drain()], [(0, 7, -2, 1)])
        self.assertEqual(acc.drain(), [])

    def test_large_motion_is_split_not_lost(self):
        acc = ReportAccumulator()
        acc.add_motion(dx=300, dy=-130)
        reports = [decode(r) for r in acc.drain()]
        self.assertEqual(reports, [(0, 127, -127, 0), (0, 127, -3, 0), (0, 46, 0, 0)])
        self.assertEqual(sum(r[1] for r in reports), 300)
        self.assertEqual(sum(r[2] for r in reports), -130)

    def test_motion_reports_per_drain_are_bounded_and_carried_over(self):
        acc = ReportAccumulator(max_motion_reports_per_drain=2)
        acc.add_motion(dx=127 * 3)
        self.assertEqual(len(acc.drain()), 2)
        self.assertEqual([decode(r) for r in acc.drain()], [(0, 127, 0, 0)])

    def test_pending_motion_is_capped(self):
        acc = ReportAccumulator(max_motion_reports_per_drain=1000, max_pending_motion=200)
        acc.add_motion(dx=10_000)
        self.assertEqual(sum(decode(r)[1] for r in acc.drain()), 200)

    def test_quick_click_is_not_merged(self):
        acc = ReportAccumulator()
        acc.set_button(0, True)
        acc.set_button(0, False)
        self.assertEqual([decode(r) for r in acc.drain()], [(1, 0, 0, 0), (0, 0, 0, 0)])

    def test_motion_before_press_keeps_old_buttons(self):
        acc = ReportAccumulator()
        acc.add_motion(dx=5)
        acc.set_button(0, True)
        acc.add_motion(dx=2)
        self.assertEqual([decode(r) for r in acc.drain()], [(0, 5, 0, 0), (1, 0, 0, 0), (1, 2, 0, 0)])

    def test_repeated_button_state_is_ignored(self):
        acc = ReportAccumulator()
        acc.set_button(1, False)
        acc.set_button(2, True)
        acc.set_button(2, True)
        self.assertEqual([decode(r) for r in acc.drain()], [(4, 0, 0, 0)])

    def test_release_all_always_queues_release(self):
        acc = ReportAccumulator()
        acc.release_all()
        self.assertEqual(acc.drain(), [bytes(4)])
        acc.set_button(0, True)
        acc.add_motion(dx=9)
        acc.release_all()
        self.assertEqual([decode(r) for r in acc.drain()], [(1, 0, 0, 0), (0, 0, 0, 0)])
        self.assertEqual(acc.buttons, 0)

    def test_clear_drops_queue_and_motion(self):
        acc = ReportAccumulator()
        acc.add_motion(dx=9)
        acc.set_button(0, True)
        acc.clear()
        self.assertFalse(acc.has_pending())
        self.assertEqual(acc.drain(), [])


class HotkeyDetectorTests(unittest.TestCase):
    LCTRL, RCTRL, F8, A = 29, 97, 66, 30

    def setUp(self):
        self.det = HotkeyDetector((self.LCTRL, self.RCTRL), self.F8)

    def test_ctrl_f8_fires_once(self):
        self.assertFalse(self.det.feed(self.LCTRL, 1))
        self.assertTrue(self.det.feed(self.F8, 1))
        self.assertFalse(self.det.feed(self.F8, 2))  # autorepeat
        self.assertFalse(self.det.feed(self.F8, 0))

    def test_f8_without_ctrl_does_not_fire(self):
        self.assertFalse(self.det.feed(self.F8, 1))

    def test_releasing_one_ctrl_keeps_other_held(self):
        self.det.feed(self.LCTRL, 1)
        self.det.feed(self.RCTRL, 1)
        self.det.feed(self.LCTRL, 0)
        self.assertTrue(self.det.feed(self.F8, 1))
        self.det.feed(self.RCTRL, 0)
        self.assertFalse(self.det.feed(self.F8, 1))


if __name__ == "__main__":
    unittest.main()
