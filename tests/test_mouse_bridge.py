"""Tests for mode switching and event routing (no hardware needed)."""
import sys
import unittest
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

try:
    from evdev import ecodes
    import mouse_bridge
    HAVE_DEPS = True
except ImportError:  # pragma: no cover - depends on host packages
    HAVE_DEPS = False

Event = namedtuple("Event", "type code value")


class FakeMouse:
    path = "/dev/input/event99"
    name = "Fake Mouse"

    def __init__(self, fail_grab=False):
        self.grabbed = False
        self.fail_grab = fail_grab

    def grab(self):
        if self.fail_grab:
            raise OSError("busy")
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False


@unittest.skipUnless(HAVE_DEPS, "python3-evdev, dbus-python and PyGObject are required")
class BridgeControllerTests(unittest.TestCase):
    def setUp(self):
        self.mouse = FakeMouse()
        self.ctl = mouse_bridge.BridgeController(self.mouse)

    def rel(self, code, value):
        self.ctl.handle_event(Event(ecodes.EV_REL, code, value))

    def key(self, code, value):
        self.ctl.handle_event(Event(ecodes.EV_KEY, code, value))

    def test_laptop_mode_ignores_input_and_does_not_grab(self):
        self.rel(ecodes.REL_X, 10)
        self.key(ecodes.BTN_LEFT, 1)
        self.assertFalse(self.mouse.grabbed)
        self.assertEqual(self.ctl.reports.drain(), [])

    def test_phone_mode_grabs_and_routes(self):
        self.ctl.toggle()
        self.assertTrue(self.ctl.active)
        self.assertTrue(self.mouse.grabbed)
        self.rel(ecodes.REL_X, 10)
        self.rel(ecodes.REL_Y, -3)
        self.rel(ecodes.REL_WHEEL, 1)
        self.key(ecodes.BTN_RIGHT, 1)
        self.assertEqual(self.ctl.reports.drain(), [bytes([0, 10, 253, 1]), bytes([2, 0, 0, 0])])

    def test_leaving_phone_mode_sends_release_without_further_input(self):
        self.ctl.toggle()
        self.key(ecodes.BTN_LEFT, 1)
        self.ctl.reports.drain()
        self.ctl.toggle()
        self.assertFalse(self.mouse.grabbed)
        self.assertEqual(self.ctl.reports.drain(), [bytes(4)])

    def test_grab_failure_stays_in_laptop_mode(self):
        ctl = mouse_bridge.BridgeController(FakeMouse(fail_grab=True))
        with self.assertLogs("mouse-bridge", "ERROR"):
            ctl.toggle()
        self.assertFalse(ctl.active)

    def test_no_grab_option(self):
        ctl = mouse_bridge.BridgeController(self.mouse, grab=False)
        ctl.toggle()
        self.assertTrue(ctl.active)
        self.assertFalse(self.mouse.grabbed)

    def test_key_autorepeat_and_other_keys_ignored(self):
        self.ctl.toggle()
        self.key(ecodes.BTN_LEFT, 2)
        self.key(ecodes.BTN_SIDE, 1)
        self.assertEqual(self.ctl.reports.drain(), [])

    def test_shutdown_releases_grab_and_buttons(self):
        self.ctl.toggle()
        self.key(ecodes.BTN_MIDDLE, 1)
        self.ctl.reports.drain()
        self.ctl.shutdown()
        self.assertFalse(self.mouse.grabbed)
        self.assertEqual(self.ctl.reports.drain(), [bytes(4)])

    def test_reentering_phone_mode_discards_stale_reports(self):
        self.ctl.toggle()
        self.rel(ecodes.REL_X, 5)
        self.ctl.toggle()
        self.ctl.toggle()
        self.assertEqual(self.ctl.reports.drain(), [])

    def test_touchpad_events_are_translated(self):
        from hid_report import ReportAccumulator, TouchpadTranslator
        reports = ReportAccumulator()
        pad = TouchpadTranslator(reports, 10, 10, speed=10)
        ctl = mouse_bridge.BridgeController(FakeMouse(), reports=reports, touchpad=pad)
        ctl.toggle()

        class Ev(namedtuple("Ev", "type code value t")):
            def timestamp(self):
                return self.t

        for ev in [Ev(ecodes.EV_KEY, ecodes.BTN_TOUCH, 1, 0), Ev(ecodes.EV_KEY, ecodes.BTN_TOOL_FINGER, 1, 0),
                   Ev(ecodes.EV_ABS, ecodes.ABS_X, 100, 0), Ev(ecodes.EV_ABS, ecodes.ABS_Y, 100, 0),
                   Ev(ecodes.EV_SYN, ecodes.SYN_REPORT, 0, 0.0),
                   Ev(ecodes.EV_ABS, ecodes.ABS_X, 130, 0), Ev(ecodes.EV_SYN, ecodes.SYN_REPORT, 0, 0.5),
                   Ev(ecodes.EV_KEY, ecodes.BTN_TOUCH, 0, 0), Ev(ecodes.EV_KEY, ecodes.BTN_TOOL_FINGER, 0, 0),
                   Ev(ecodes.EV_SYN, ecodes.SYN_REPORT, 0, 0.6)]:
            ctl.handle_event(ev)
        self.assertEqual(reports.drain(), [bytes([0, 30, 0, 0])])  # moved; too slow for a tap

    def test_argument_parsing(self):
        args = mouse_bridge.parse_args(["--adapter", "hci1", "--no-agent", "--start-active"])
        self.assertEqual(args.adapter, "hci1")
        self.assertTrue(args.no_agent and args.start_active)
        self.assertEqual(args.name, "iPhone Mirror Bridge")
        args = mouse_bridge.parse_args(["--touchpad", "--touchpad-speed", "20", "--reverse-scroll", "--no-tap"])
        self.assertTrue(args.touchpad and args.reverse_scroll and args.no_tap)
        self.assertEqual(args.touchpad_speed, 20.0)


if __name__ == "__main__":
    unittest.main()
