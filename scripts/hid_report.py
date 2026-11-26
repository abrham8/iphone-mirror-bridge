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
    * Tapping clicks: one finger = left, two = right, three = middle.

    Positions are in device units; units_per_mm converts them to millimetres.
    Output goes to a ReportAccumulator.
    """

    TAP_MAX_SECONDS = 0.18
    TAP_MAX_TRAVEL_MM = 1.5

    def __init__(self, reports: ReportAccumulator, units_per_mm_x: float, units_per_mm_y: float,
                 speed: float = 12.0, scroll_mm_per_step: float = 2.5, natural_scroll: bool = True,
                 tap_to_click: bool = True):
        self.reports = reports
        self.upmm_x = units_per_mm_x or 1.0
        self.upmm_y = units_per_mm_y or 1.0
        self.speed = speed  # HID counts per millimetre of finger travel
        self.scroll_mm_per_step = scroll_mm_per_step
        self.natural_scroll = natural_scroll
        self.tap_to_click = tap_to_click
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
        if fingers == 1:
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
        is_tap = (self.tap_to_click and not self._clicked
                  and timestamp - self._tap_start <= self.TAP_MAX_SECONDS
                  and self._travel_mm <= self.TAP_MAX_TRAVEL_MM)
        if is_tap:
            button = {1: 0, 2: 1, 3: 2}.get(self._tap_fingers)
            if button is not None:
                self.reports.set_button(button, True)
                self.reports.set_button(button, False)
        self._last = None
        self._frac_x = self._frac_y = 0.0
        self._scroll_mm = 0.0
