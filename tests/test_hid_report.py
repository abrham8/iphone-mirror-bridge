import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from hid_report import MouseState, build_hid_report


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


if __name__ == "__main__":
    unittest.main()
