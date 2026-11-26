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
