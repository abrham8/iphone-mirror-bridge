#!/usr/bin/env python3
"""Bridge a Linux mouse to an iPhone using BlueZ BLE HID over GATT.

Requires a running BlueZ system daemon, a BLE adapter capable of peripheral
mode, and read access to /dev/input (normally root).
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from typing import Iterable

import dbus
import dbus.mainloop.glib
from evdev import InputDevice, ecodes, list_devices
from gi.repository import GLib

from ble_hid import BleHid, adapter_provides_device_info, find_adapter
from hid_report import HotkeyDetector, ReportAccumulator, TouchpadTranslator

LOG = logging.getLogger("mouse-bridge")
FLUSH_INTERVAL_MS = 8
BUTTONS = {ecodes.BTN_LEFT: 0, ecodes.BTN_RIGHT: 1, ecodes.BTN_MIDDLE: 2}
FINGER_TOOLS = {ecodes.BTN_TOOL_FINGER: 1, ecodes.BTN_TOOL_DOUBLETAP: 2, ecodes.BTN_TOOL_TRIPLETAP: 3,
                ecodes.BTN_TOOL_QUADTAP: 4, ecodes.BTN_TOOL_QUINTTAP: 5}


# -- input device discovery --------------------------------------------------
def device_is_mouse(device: InputDevice) -> bool:
    caps = device.capabilities(absinfo=False)
    rel = set(caps.get(ecodes.EV_REL, []))
    keys = set(caps.get(ecodes.EV_KEY, []))
    return (ecodes.REL_X in rel and ecodes.REL_Y in rel
            and ecodes.BTN_LEFT in keys and ecodes.BTN_RIGHT in keys)


def device_is_touchpad(device: InputDevice) -> bool:
    caps = device.capabilities(absinfo=False)
    absolute = set(caps.get(ecodes.EV_ABS, []))
    keys = set(caps.get(ecodes.EV_KEY, []))
    try:
        direct = ecodes.INPUT_PROP_DIRECT in device.input_props()  # touchscreens
    except (AttributeError, OSError):
        direct = False
    return (ecodes.ABS_X in absolute and ecodes.ABS_Y in absolute
            and ecodes.BTN_TOUCH in keys and ecodes.BTN_TOOL_FINGER in keys and not direct)


def device_is_keyboard(device: InputDevice, hotkey: int = ecodes.KEY_F8) -> bool:
    keys = set(device.capabilities(absinfo=False).get(ecodes.EV_KEY, []))
    return hotkey in keys and (ecodes.KEY_LEFTCTRL in keys or ecodes.KEY_RIGHTCTRL in keys)


def find_mouse_device(path: str | None = None, touchpad: bool = False) -> InputDevice:
    """Open the requested pointing device or auto-select one.

    An explicit path may be a mouse or a touchpad. Without a path, the first
    physical mouse is chosen, or the first touchpad when touchpad=True.
    """
    if path:
        device = InputDevice(path)
        if not (device_is_mouse(device) or device_is_touchpad(device)):
            device.close()
            raise RuntimeError(f"{path} is neither a mouse (REL_X/REL_Y + buttons) nor a touchpad")
        LOG.info("Using %s: %s (%s)", "touchpad" if device_is_touchpad(device) else "mouse",
                 device.path, device.name)
        return device

    kind = "touchpad" if touchpad else "mouse"
    matches = device_is_touchpad if touchpad else device_is_mouse
    candidates: list[InputDevice] = []
    for candidate_path in sorted(list_devices()):
        try:
            device = InputDevice(candidate_path)
        except OSError:
            continue
        if matches(device) and not device.name.lower().startswith(("virtual", "uinput")):
            candidates.append(device)
        else:
            device.close()
    if not candidates:
        raise RuntimeError(f"No readable {kind} found. Run as root or use --device /dev/input/eventN.")
    if len(candidates) > 1:
        LOG.warning("Multiple %ss found (%s); selecting %s. Use --device to choose explicitly.", kind,
                    ", ".join(f"{d.path} {d.name!r}" for d in candidates), candidates[0].path)
    selected = candidates[0]
    for extra in candidates[1:]:
        extra.close()
    LOG.info("Using %s: %s (%s)", kind, selected.path, selected.name)
    return selected


def make_touchpad_translator(device: InputDevice, reports: ReportAccumulator, args) -> TouchpadTranslator:
    def units_per_mm(code):
        info = device.absinfo(code)
        if info.resolution:
            return float(info.resolution)
        return max(1.0, (info.max - info.min) / 100.0)  # assume a ~100 mm wide pad
    return TouchpadTranslator(reports, units_per_mm(ecodes.ABS_X), units_per_mm(ecodes.ABS_Y),
                              speed=args.touchpad_speed, natural_scroll=not args.reverse_scroll,
                              tap_to_click=not args.no_tap)


def find_keyboards(path: str | None = None, exclude: Iterable[str] = (),
                   hotkey: int = ecodes.KEY_F8) -> list[InputDevice]:
    excluded = set(exclude)
    found = []
    for p in ([path] if path else sorted(list_devices())):
        if p in excluded and not path:
            continue
        try:
            dev = InputDevice(p)
        except OSError as exc:
            if path:
                raise RuntimeError(f"Cannot open hotkey device {path}: {exc}") from exc
            continue
        if device_is_keyboard(dev, hotkey):
            found.append(dev)
        else:
            if path:
                dev.close()
                raise RuntimeError(f"{path} has no Ctrl and {key_name(hotkey)} keys")
            dev.close()
    for dev in found:
        LOG.info("Listening for Ctrl+%s on %s (%s)", key_name(hotkey)[4:], dev.path, dev.name)
    return found


# -- mode control ------------------------------------------------------------
class BridgeController:
    """Owns the phone/laptop mode, the evdev grab and the pending HID reports."""

    def __init__(self, mouse: InputDevice, grab: bool = True, reports: ReportAccumulator | None = None,
                 touchpad: TouchpadTranslator | None = None):
        self.mouse = mouse
        self.grab_enabled = grab
        self.reports = reports or ReportAccumulator()
        self.touchpad = touchpad
        self._lock = threading.Lock()
        self._active = False
        self._grabbed = False

    @property
    def active(self) -> bool:
        return self._active

    def toggle(self) -> None:
        with self._lock:
            self._set_active_locked(not self._active)

    def set_active(self, active: bool) -> None:
        with self._lock:
            self._set_active_locked(active)

    def _set_active_locked(self, active: bool) -> None:
        if active == self._active:
            return
        if active:
            if self.grab_enabled and not self._grabbed:
                try:
                    self.mouse.grab()
                    self._grabbed = True
                except OSError as exc:
                    LOG.error("Could not grab the mouse (%s); staying in laptop mode", exc)
                    return
            self.reports.clear()
            if self.touchpad:
                self.touchpad.reset()
            self._active = True
            LOG.info("iPhone control mode (press the hotkey again to return)")
        else:
            self._active = False
            # Always tell the phone every button is up, even with no further input.
            self.reports.release_all()
            self._ungrab_locked()
            LOG.info("Laptop control mode")

    def _ungrab_locked(self) -> None:
        if self._grabbed:
            try:
                self.mouse.ungrab()
            except OSError:
                pass
            self._grabbed = False

    def handle_event(self, event) -> None:
        if not self._active:
            return
        if self.touchpad is not None:
            self._handle_touchpad_event(event)
            return
        if event.type == ecodes.EV_REL:
            if event.code == ecodes.REL_X:
                self.reports.add_motion(dx=event.value)
            elif event.code == ecodes.REL_Y:
                self.reports.add_motion(dy=event.value)
            elif event.code == ecodes.REL_WHEEL:
                self.reports.add_motion(wheel=event.value)
        elif event.type == ecodes.EV_KEY and event.code in BUTTONS and event.value in (0, 1):
            self.reports.set_button(BUTTONS[event.code], bool(event.value))

    def _handle_touchpad_event(self, event) -> None:
        pad = self.touchpad
        if event.type == ecodes.EV_ABS:
            if event.code == ecodes.ABS_X:
                pad.set_x(event.value)
            elif event.code == ecodes.ABS_Y:
                pad.set_y(event.value)
        elif event.type == ecodes.EV_KEY and event.value in (0, 1):
            if event.code == ecodes.BTN_TOUCH:
                pad.set_touch(bool(event.value))
            elif event.code in FINGER_TOOLS:
                pad.set_finger_tool(FINGER_TOOLS[event.code], bool(event.value))
            elif event.code == ecodes.BTN_LEFT:
                pad.set_click(bool(event.value))
            elif event.code in (ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE):
                self.reports.set_button(BUTTONS[event.code], bool(event.value))
        elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
            pad.sync(event.timestamp())

    def shutdown(self) -> None:
        with self._lock:
            if self._active:
                self._active = False
                self.reports.release_all()
            self._ungrab_locked()


# -- worker threads ----------------------------------------------------------
def key_name(code: int) -> str:
    name = ecodes.KEY.get(code) or ecodes.BTN.get(code) or str(code)
    return name if isinstance(name, str) else "/".join(name)


def listen_hotkeys(devices: Iterable[InputDevice], controller: BridgeController, stop: threading.Event,
                   hotkey: int = ecodes.KEY_F8):
    def listen(device: InputDevice):
        detector = HotkeyDetector((ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL), hotkey)
        try:
            for event in device.read_loop():
                if stop.is_set():
                    return
                if event.type != ecodes.EV_KEY:
                    continue
                if event.value == 1 and detector.ctrl_held and event.code not in detector.ctrl_codes:
                    LOG.debug("Ctrl+%s pressed on %s", key_name(event.code), device.path)
                if detector.feed(event.code, event.value):
                    controller.toggle()
        except OSError as exc:
            if not stop.is_set():
                LOG.warning("Hotkey device %s stopped: %s", device.path, exc)

    for dev in devices:
        threading.Thread(target=listen, args=(dev,), name=f"hotkey-{dev.path}", daemon=True).start()


def capture_mouse(controller: BridgeController, stop: threading.Event, on_failure):
    try:
        for event in controller.mouse.read_loop():
            if stop.is_set():
                return
            controller.handle_event(event)
    except OSError as exc:
        if not stop.is_set():
            LOG.error("Mouse input ended: %s", exc)
            on_failure()


# -- main --------------------------------------------------------------------
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="Mouse or touchpad /dev/input/eventN (auto-detected if omitted)")
    parser.add_argument("--touchpad", action="store_true",
                        help="Auto-select the built-in touchpad instead of a mouse")
    parser.add_argument("--touchpad-speed", type=float, default=12.0,
                        help="Touchpad pointer speed in counts per mm (default 12)")
    parser.add_argument("--reverse-scroll", action="store_true",
                        help="Touchpad: traditional instead of natural two-finger scrolling")
    parser.add_argument("--no-tap", action="store_true", help="Touchpad: disable tap-to-click")
    parser.add_argument("--adapter", help="Bluetooth adapter, e.g. hci0 or /org/bluez/hci0 (auto-detected)")
    parser.add_argument("--name", default="iPhone Mirror Bridge", help="BLE advertised name")
    parser.add_argument("--hotkey-device", help="Keyboard /dev/input/eventN for the Ctrl+F8 toggle")
    parser.add_argument("--hotkey", default="F8",
                        help="Key used with Ctrl to switch modes, e.g. F8, F12, M (default F8)")
    parser.add_argument("--start-active", action="store_true", help="Start with the mouse routed to the iPhone")
    parser.add_argument("--no-grab", action="store_true", help="Do not exclusively grab the local mouse")
    parser.add_argument("--no-agent", action="store_true",
                        help="Do not register the built-in auto-accept pairing agent")
    parser.add_argument("--no-device-info", action="store_true",
                        help="Omit the Device Information service (if bluetoothd already provides one)")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    dbus.mainloop.glib.threads_init()
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    hotkey = ecodes.ecodes.get("KEY_" + args.hotkey.upper())
    if hotkey is None:
        LOG.error("Unknown --hotkey %r (examples: F8, F12, M, SPACE)", args.hotkey)
        return 2
    hotkey_label = "Ctrl+" + args.hotkey.upper()
    try:
        bus = dbus.SystemBus()
        adapter = find_adapter(bus, args.adapter)
        LOG.info("Using Bluetooth adapter %s", adapter)
        mouse = find_mouse_device(args.device, touchpad=args.touchpad)
        keyboards = find_keyboards(args.hotkey_device, exclude=[mouse.path], hotkey=hotkey)
    except (RuntimeError, OSError, dbus.DBusException) as exc:
        LOG.error("%s", exc)
        return 2
    if not keyboards:
        LOG.warning("No keyboard with %s found; mode switching is unavailable. Use --hotkey-device.",
                    hotkey_label)

    include_device_info = not args.no_device_info
    if include_device_info and adapter_provides_device_info(bus, adapter):
        LOG.info("bluetoothd already provides a Device Information service; not adding another")
        include_device_info = False

    reports = ReportAccumulator()
    touchpad = make_touchpad_translator(mouse, reports, args) if device_is_touchpad(mouse) else None
    if touchpad:
        LOG.info("Touchpad mode: one finger moves, tap/press clicks, two fingers scroll or right-click")
    controller = BridgeController(mouse, grab=not args.no_grab, reports=reports, touchpad=touchpad)
    ble = BleHid(bus, adapter, args.name, use_agent=not args.no_agent,
                 include_device_info=include_device_info)
    stop = threading.Event()
    loop = GLib.MainLoop()
    exit_code = 0

    stats = {"sent": 0, "dropped": 0, "since": time.monotonic(), "warned": 0.0}

    def flush() -> bool:
        reports = controller.reports.drain()
        now = time.monotonic()
        if reports:
            if ble.subscribed:
                for report in reports:
                    ble.send(report)
                stats["sent"] += len(reports)
            else:
                stats["dropped"] += len(reports)
                if now - stats["warned"] > 5:
                    stats["warned"] = now
                    LOG.warning("Input is being discarded: the iPhone is not subscribed to mouse reports "
                                "(not connected, or not paired yet)")
        if now - stats["since"] >= 2 and (stats["sent"] or stats["dropped"]):
            LOG.debug("Last 2 s: %d reports sent to iPhone, %d discarded", stats["sent"], stats["dropped"])
            stats.update(sent=0, dropped=0, since=now)
        return not stop.is_set()

    def shutdown(code: int = 0) -> bool:
        nonlocal exit_code
        if stop.is_set():
            return False
        exit_code = exit_code or code
        stop.set()
        controller.shutdown()
        flush()  # deliver the final button-release report
        ble.stop()
        loop.quit()
        return False

    def monitor_ble() -> bool:
        if ble.failed is not None:
            shutdown(1)
            return False
        return not stop.is_set()

    try:
        ble.start()
    except dbus.DBusException as exc:
        LOG.error("BlueZ setup failed: %s", exc)
        return 1

    GLib.timeout_add(FLUSH_INTERVAL_MS, flush)
    GLib.timeout_add(250, monitor_ble)
    for signum in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signum, shutdown)

    listen_hotkeys(keyboards, controller, stop, hotkey)
    threading.Thread(target=capture_mouse, name="mouse", daemon=True,
                     args=(controller, stop, lambda: GLib.idle_add(shutdown, 1))).start()
    if args.start_active:
        controller.set_active(True)
    else:
        LOG.info("Laptop control mode. Press %s to control the iPhone.", hotkey_label)

    try:
        loop.run()
    finally:
        shutdown()
        mouse.close()
        for keyboard in keyboards:
            keyboard.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
