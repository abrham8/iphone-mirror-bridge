"""Run mouse_bridge.main() with a simulated mouse (used by test_end_to_end)."""
import sys
import time
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evdev import ecodes  # noqa: E402
import mouse_bridge  # noqa: E402

Event = namedtuple("Event", "type code value")


class SimulatedMouse:
    path = "/dev/input/event-sim"
    name = "Simulated Mouse"
    grabbed = False

    def grab(self):
        self.grabbed = True
        print("grabbed", flush=True)

    def ungrab(self):
        self.grabbed = False
        print("ungrabbed", flush=True)

    def read_loop(self):
        # Hold the left button and move right in big steps (forces report splitting).
        time.sleep(0.3)  # let --start-active take effect first
        yield Event(ecodes.EV_KEY, ecodes.BTN_LEFT, 1)
        while True:
            time.sleep(0.05)
            yield Event(ecodes.EV_REL, ecodes.REL_X, 200)
            yield Event(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)

    def close(self):
        pass


mouse_bridge.find_mouse_device = lambda path=None, touchpad=False: SimulatedMouse()
mouse_bridge.device_is_touchpad = lambda device: False
mouse_bridge.find_keyboards = lambda path=None, exclude=(): []
sys.exit(mouse_bridge.main(sys.argv[1:]))
