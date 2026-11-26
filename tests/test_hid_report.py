import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from hid_report import HotkeyDetector, MouseState, ReportAccumulator, TouchpadTranslator, build_hid_report


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


class TouchpadTranslatorTests(unittest.TestCase):
    """Device units are 10 per mm; speed 10 counts/mm => 1 count per unit."""

    def setUp(self):
        self.acc = ReportAccumulator(max_motion_reports_per_drain=100)
        self.pad = TouchpadTranslator(self.acc, 10, 10, speed=10, scroll_mm_per_step=2.5)
        self.t = 0.0

    def frame(self, x=None, y=None, dt=0.01):
        if x is not None:
            self.pad.set_x(x)
        if y is not None:
            self.pad.set_y(y)
        self.t += dt
        self.pad.sync(self.t)

    def touch(self, fingers=1, x=100, y=100):
        self.pad.set_touch(True)
        self.pad.set_finger_tool(fingers, True)
        self.frame(x, y)

    def lift(self, fingers=1, dt=0.01):
        self.pad.set_touch(False)
        self.pad.set_finger_tool(fingers, False)
        self.frame(dt=dt)

    def reports(self):
        return [decode(r) for r in self.acc.drain()]

    def test_one_finger_moves_without_initial_jump(self):
        self.touch(x=500, y=500)
        self.frame(x=530, y=480)
        self.frame(x=540)
        self.assertEqual(self.reports(), [(0, 40, -20, 0)])

    def test_fractional_motion_is_kept(self):
        self.pad.speed = 5  # 0.5 counts per unit
        self.touch()
        for x in (101, 102, 103, 104):
            self.frame(x=x)
        self.assertEqual(sum(r[1] for r in self.reports()), 2)

    def test_tap_clicks_left(self):
        self.touch()
        self.lift()
        self.assertEqual(self.reports(), [(1, 0, 0, 0), (0, 0, 0, 0)])

    def test_two_finger_tap_clicks_right(self):
        self.touch(fingers=2)
        self.lift(fingers=2)
        self.assertEqual(self.reports(), [(2, 0, 0, 0), (0, 0, 0, 0)])

    def test_slow_touch_or_movement_is_not_a_tap(self):
        self.touch()
        self.lift(dt=0.5)
        self.touch()
        self.frame(x=200)
        self.lift()
        self.assertEqual([r for r in self.reports() if r[0]], [])

    def test_tap_can_be_disabled(self):
        self.pad.tap_to_click = False
        self.touch()
        self.lift()
        self.assertEqual(self.reports(), [])

    def test_two_finger_natural_scroll(self):
        self.touch(fingers=2, y=100)
        self.frame(y=150)  # fingers down 5 mm => 2 steps up (natural)
        self.assertEqual(self.reports(), [(0, 0, 0, 2)])
        self.pad.natural_scroll = False
        self.frame(y=200)
        self.assertEqual(self.reports(), [(0, 0, 0, -2)])

    def test_finger_count_change_does_not_jump(self):
        self.touch(fingers=1, x=100)
        self.pad.set_finger_tool(1, False)
        self.pad.set_finger_tool(2, True)
        self.frame(x=900)  # second finger lands: single-touch position may jump
        self.frame(x=900)
        self.assertEqual(self.reports(), [])

    def test_clickpad_press_left_and_two_finger_press_right(self):
        self.touch()
        self.pad.set_click(True)
        self.frame()
        self.pad.set_click(False)
        self.lift()
        self.assertEqual(self.reports(), [(1, 0, 0, 0), (0, 0, 0, 0)])  # no extra tap click
        self.touch(fingers=2)
        self.pad.set_click(True)
        self.pad.set_click(False)
        self.lift(fingers=2)
        self.assertEqual(self.reports(), [(2, 0, 0, 0), (0, 0, 0, 0)])

    def test_click_and_drag(self):
        self.touch(x=100)
        self.pad.set_click(True)
        self.frame(x=120, dt=0.3)
        self.pad.set_click(False)
        self.lift()
        self.assertEqual(self.reports(), [(1, 0, 0, 0), (1, 20, 0, 0), (0, 0, 0, 0)])


if __name__ == "__main__":
    unittest.main()
