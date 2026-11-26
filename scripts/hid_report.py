"""Pure HID mouse report encoding helpers (no Linux or D-Bus dependencies)."""
from dataclasses import dataclass


def clamp_i8(value: int) -> int:
    return max(-127, min(127, int(value)))


def build_hid_report(buttons: int, dx: int = 0, dy: int = 0, wheel: int = 0) -> bytes:
    """Return a four-byte HID input payload: buttons, signed X, Y, and wheel."""
    return bytes((buttons & 0x07, clamp_i8(dx) & 0xFF,
                  clamp_i8(dy) & 0xFF, clamp_i8(wheel) & 0xFF))


@dataclass
class MouseState:
    buttons: int = 0
    dx: int = 0
    dy: int = 0
    wheel: int = 0

    def report(self) -> bytes:
        result = build_hid_report(self.buttons, self.dx, self.dy, self.wheel)
        self.dx = self.dy = self.wheel = 0
        return result
