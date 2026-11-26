"""Pure HID mouse report encoding helpers (no Linux or D-Bus dependencies)."""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

I8_MAX = 127
BUTTON_MASK = 0x07


def clamp_i8(value: int) -> int:
    return max(-I8_MAX, min(I8_MAX, int(value)))


def build_hid_report(buttons: int, dx: int = 0, dy: int = 0, wheel: int = 0) -> bytes:
    """Return a four-byte HID input payload: buttons, signed X, Y, and wheel."""
    return bytes((buttons & BUTTON_MASK, clamp_i8(dx) & 0xFF,
                  clamp_i8(dy) & 0xFF, clamp_i8(wheel) & 0xFF))


@dataclass
class MouseState:
    """Simple single-report state (kept for compatibility; clamps excess motion)."""
    buttons: int = 0
    dx: int = 0
    dy: int = 0
    wheel: int = 0

    def report(self) -> bytes:
        result = build_hid_report(self.buttons, self.dx, self.dy, self.wheel)
        self.dx = self.dy = self.wheel = 0
        return result


class ReportAccumulator:
    """Thread-safe queue of HID reports built from Linux mouse events.

    * Motion is accumulated and split into several reports instead of being
      clamped, so fast movements are not lost.
    * Every button change produces its own report, so a quick click
      (press + release between two flushes) is never merged away.
    * Motion that happened before a button change is sent with the old
      button state, preserving drag start positions.
    """

    def __init__(self, max_motion_reports_per_drain: int = 8, max_pending_motion: int = I8_MAX * 16):
        self._lock = threading.Lock()
        self._queue: deque[bytes] = deque()
        self._buttons = 0
        self._dx = self._dy = self._wheel = 0
        self.max_motion_reports_per_drain = max_motion_reports_per_drain
        self.max_pending_motion = max_pending_motion

    @property
    def buttons(self) -> int:
        with self._lock:
            return self._buttons

    def add_motion(self, dx: int = 0, dy: int = 0, wheel: int = 0) -> None:
        limit = self.max_pending_motion
        with self._lock:
            self._dx = max(-limit, min(limit, self._dx + dx))
            self._dy = max(-limit, min(limit, self._dy + dy))
            self._wheel = max(-limit, min(limit, self._wheel + wheel))

    def set_button(self, index: int, pressed: bool) -> None:
        with self._lock:
            new = self._buttons | (1 << index) if pressed else self._buttons & ~(1 << index)
            self._set_buttons_locked(new & BUTTON_MASK)

    def set_buttons(self, buttons: int) -> None:
        with self._lock:
            self._set_buttons_locked(buttons & BUTTON_MASK)

    def release_all(self) -> None:
        """Drop pending motion and queue a report with every button released.

        A release report is queued even if no button appears pressed, so the
        host never keeps a stale pressed state after a mode switch.
        """
        with self._lock:
            self._dx = self._dy = self._wheel = 0
            self._buttons = 0
            self._queue.append(build_hid_report(0))

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()
            self._dx = self._dy = self._wheel = 0

    def drain(self) -> list[bytes]:
        """Return queued reports plus a bounded number of motion reports."""
        with self._lock:
            out = list(self._queue)
            self._queue.clear()
            out.extend(self._motion_reports_locked(self.max_motion_reports_per_drain))
            return out

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._queue or self._dx or self._dy or self._wheel)

    # -- internal ---------------------------------------------------------
    def _set_buttons_locked(self, new: int) -> None:
        if new == self._buttons:
            return
        # Flush earlier motion with the old button state, then the change itself.
        self._queue.extend(self._motion_reports_locked(None))
        self._buttons = new
        self._queue.append(build_hid_report(new))

    def _motion_reports_locked(self, limit: int | None) -> list[bytes]:
        reports = []
        while (self._dx or self._dy or self._wheel) and (limit is None or len(reports) < limit):
            dx, dy, wheel = clamp_i8(self._dx), clamp_i8(self._dy), clamp_i8(self._wheel)
            self._dx -= dx
            self._dy -= dy
            self._wheel -= wheel
            reports.append(build_hid_report(self._buttons, dx, dy, wheel))
        return reports


class HotkeyDetector:
    """Detect Ctrl+<key> presses from a stream of (code, value) key events."""

    def __init__(self, ctrl_codes: tuple[int, ...], trigger_code: int):
        self.ctrl_codes = set(ctrl_codes)
        self.trigger_code = trigger_code
        self._held: set[int] = set()

    @property
    def ctrl_held(self) -> bool:
        return bool(self._held)

    def feed(self, code: int, value: int) -> bool:
        """Feed one EV_KEY event; return True when the hotkey fires.

        value: 0 = release, 1 = press, 2 = autorepeat (ignored for trigger).
        """
        if code in self.ctrl_codes:
            if value:
                self._held.add(code)
            else:
                self._held.discard(code)
            return False
        return code == self.trigger_code and value == 1 and bool(self._held)


class TouchpadTranslator:
    """Turn absolute touchpad frames into relative mouse input.

    * One finger moves the pointer.
    * Two fingers scroll vertically.
    * Pressing the pad clicks: left, or right with two fingers on the pad.
    * Tapping clicks: one finger = left, two = right.
    * Three fingers press and hold the left button: keep still for a
      long-press, move to drag, lift to release (three-finger drag).

    Positions are in device units; units_per_mm converts them to millimetres.
    Output goes to a ReportAccumulator.
    """

    TAP_MAX_SECONDS = 0.18
    TAP_MAX_TRAVEL_MM = 1.5

    def __init__(self, reports: ReportAccumulator, units_per_mm_x: float, units_per_mm_y: float,
                 speed: float = 20.0, scroll_mm_per_step: float = 1.0, natural_scroll: bool = True,
                 tap_to_click: bool = True, three_finger_drag: bool = True):
        self.reports = reports
        self.upmm_x = units_per_mm_x or 1.0
        self.upmm_y = units_per_mm_y or 1.0
        self.speed = speed  # HID counts per millimetre of finger travel
        self.scroll_mm_per_step = scroll_mm_per_step
        self.natural_scroll = natural_scroll
        self.tap_to_click = tap_to_click
        self.three_finger_drag = three_finger_drag
        self.reset()

    def reset(self) -> None:
        self._touching = False
        self._was_touching = False
        self._tools: set[int] = set()
        self._x = self._y = None
        self._last = None
        self._last_fingers = 0
        self._frac_x = self._frac_y = 0.0
        self._scroll_mm = 0.0
        self._tap_start = 0.0
        self._tap_fingers = 0
        self._travel_mm = 0.0
        self._clicked = False
        self._click_button: int | None = None
        self._dragging = False

    @property
    def dragging(self) -> bool:
        return self._dragging

    # -- event inputs ---------------------------------------------------------
    def set_touch(self, touching: bool) -> None:
        self._touching = touching

    def set_finger_tool(self, count: int, down: bool) -> None:
        if down:
            self._tools.add(count)
        else:
            self._tools.discard(count)

    def set_x(self, value: int) -> None:
        self._x = value

    def set_y(self, value: int) -> None:
        self._y = value

    @property
    def fingers(self) -> int:
        return max(self._tools) if self._tools else (1 if self._touching else 0)

    def set_click(self, pressed: bool) -> None:
        """Physical clickpad press (BTN_LEFT on a clickpad)."""
        if pressed:
            self._clicked = True
            self._click_button = 1 if self.fingers >= 2 else 0
            self.reports.set_button(self._click_button, True)
        elif self._click_button is not None:
            self.reports.set_button(self._click_button, False)
            self._click_button = None

    def sync(self, timestamp: float) -> None:
        """Process one complete frame (EV_SYN/SYN_REPORT)."""
        fingers = self.fingers if self._touching else 0
        if fingers:
            if not self._was_touching:
                self._tap_start = timestamp
                self._tap_fingers = 0
                self._travel_mm = 0.0
                self._clicked = self._click_button is not None
                self._last = None
            if fingers != self._last_fingers:
                self._last = None  # finger count changed: avoid a jump
                self._scroll_mm = 0.0
            self._tap_fingers = max(self._tap_fingers, fingers)
            if fingers >= 3 and self.three_finger_drag and not self._dragging:
                self._dragging = True
                self._clicked = True  # not a tap
                self.reports.set_button(0, True)
            if self._x is not None and self._y is not None:
                if self._last is not None:
                    self._move(fingers, self._x - self._last[0], self._y - self._last[1])
                self._last = (self._x, self._y)
        elif self._was_touching:
            self._finish_touch(timestamp)
        self._was_touching = bool(fingers)
        self._last_fingers = fingers

    # -- internal -------------------------------------------------------------
    def _move(self, fingers: int, du: int, dv: int) -> None:
        dx_mm, dy_mm = du / self.upmm_x, dv / self.upmm_y
        self._travel_mm += abs(dx_mm) + abs(dy_mm)
        if fingers == 1 or self._dragging:
            self._frac_x += dx_mm * self.speed
            self._frac_y += dy_mm * self.speed
            dx, dy = int(self._frac_x), int(self._frac_y)
            self._frac_x -= dx
            self._frac_y -= dy
            if dx or dy:
                self.reports.add_motion(dx=dx, dy=dy)
        elif fingers == 2:
            self._scroll_mm += dy_mm
            steps = int(self._scroll_mm / self.scroll_mm_per_step)
            if steps:
                self._scroll_mm -= steps * self.scroll_mm_per_step
                # Linux/HID wheel: positive scrolls up. Natural scrolling moves
                # content with the fingers, so fingers down = scroll up.
                self.reports.add_motion(wheel=steps if self.natural_scroll else -steps)

    def _finish_touch(self, timestamp: float) -> None:
        if self._dragging:
            self._dragging = False
            self.reports.set_button(0, False)
        is_tap = (self.tap_to_click and not self._clicked
                  and timestamp - self._tap_start <= self.TAP_MAX_SECONDS
                  and self._travel_mm <= self.TAP_MAX_TRAVEL_MM)
        if is_tap:
            button = {1: 0, 2: 1, 3: 0}.get(self._tap_fingers)
            if button is not None:
                self.reports.set_button(button, True)
                self.reports.set_button(button, False)
        self._last = None
        self._frac_x = self._frac_y = 0.0
        self._scroll_mm = 0.0


# -- keyboard ---------------------------------------------------------------
# Linux evdev key codes -> USB HID Keyboard/Keypad usages (page 0x07).
EVDEV_TO_HID = {
    1: 0x29, 2: 0x1E, 3: 0x1F, 4: 0x20, 5: 0x21, 6: 0x22, 7: 0x23, 8: 0x24, 9: 0x25, 10: 0x26, 11: 0x27,
    12: 0x2D, 13: 0x2E, 14: 0x2A, 15: 0x2B, 16: 0x14, 17: 0x1A, 18: 0x08, 19: 0x15, 20: 0x17, 21: 0x1C,
    22: 0x18, 23: 0x0C, 24: 0x12, 25: 0x13, 26: 0x2F, 27: 0x30, 28: 0x28, 29: 0xE0, 30: 0x04, 31: 0x16,
    32: 0x07, 33: 0x09, 34: 0x0A, 35: 0x0B, 36: 0x0D, 37: 0x0E, 38: 0x0F, 39: 0x33, 40: 0x34, 41: 0x35,
    42: 0xE1, 43: 0x31, 44: 0x1D, 45: 0x1B, 46: 0x06, 47: 0x19, 48: 0x05, 49: 0x11, 50: 0x10, 51: 0x36,
    52: 0x37, 53: 0x38, 54: 0xE5, 55: 0x55, 56: 0xE2, 57: 0x2C, 58: 0x39,
    59: 0x3A, 60: 0x3B, 61: 0x3C, 62: 0x3D, 63: 0x3E, 64: 0x3F, 65: 0x40, 66: 0x41, 67: 0x42, 68: 0x43,
    69: 0x53, 70: 0x47, 71: 0x5F, 72: 0x60, 73: 0x61, 74: 0x56, 75: 0x5C, 76: 0x5D, 77: 0x5E, 78: 0x57,
    79: 0x59, 80: 0x5A, 81: 0x5B, 82: 0x62, 83: 0x63, 86: 0x64, 87: 0x44, 88: 0x45, 96: 0x58, 97: 0xE4,
    98: 0x54, 99: 0x46, 100: 0xE6, 102: 0x4A, 103: 0x52, 104: 0x4B, 105: 0x50, 106: 0x4F, 107: 0x4D,
    108: 0x51, 109: 0x4E, 110: 0x49, 111: 0x4C, 119: 0x48, 125: 0xE3, 126: 0xE7, 127: 0x65,
}

# Consumer-page usages used by the UI buttons.
CONSUMER_HOME = 0x0223          # AC Home


class KeyboardState:
    """Tracks pressed keys and builds 8-byte boot-keyboard input reports."""

    MAX_KEYS = 6

    def __init__(self):
        self._lock = threading.Lock()
        self._modifiers = 0
        self._keys: list[int] = []

    def key(self, evdev_code: int, pressed: bool) -> bytes | None:
        """Apply a key event; return the new report, or None if unmapped/unchanged."""
        usage = EVDEV_TO_HID.get(int(evdev_code))
        if usage is None:
            return None
        with self._lock:
            before = (self._modifiers, tuple(self._keys))
            if 0xE0 <= usage <= 0xE7:
                bit = 1 << (usage - 0xE0)
                self._modifiers = self._modifiers | bit if pressed else self._modifiers & ~bit
            elif pressed:
                if usage not in self._keys and len(self._keys) < self.MAX_KEYS:
                    self._keys.append(usage)
            elif usage in self._keys:
                self._keys.remove(usage)
            if (self._modifiers, tuple(self._keys)) == before:
                return None
            return self._report_locked()

    def release_all(self) -> bytes:
        with self._lock:
            self._modifiers = 0
            self._keys.clear()
            return self._report_locked()

    def _report_locked(self) -> bytes:
        keys = self._keys + [0] * (self.MAX_KEYS - len(self._keys))
        return bytes([self._modifiers, 0, *keys])


def consumer_report(usage: int) -> bytes:
    """Two-byte consumer-control report (little endian); 0 releases."""
    return int(usage).to_bytes(2, "little")
