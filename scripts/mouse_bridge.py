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
import dbus.service
from evdev import InputDevice, ecodes, list_devices
from gi.repository import GLib

from ble_hid import BleHid, adapter_provides_device_info, find_adapter, set_le_connection_interval
from control import BUS_NAME, DEFAULT_SETTINGS_PATH, BridgeControl, SettingsStore, logind_authorizer
from hid_report import HotkeyDetector, ReportAccumulator, TouchpadTranslator

__version__ = "0.1.0"
LOG = logging.getLogger("mouse-bridge")
DEFAULT_REPORT_INTERVAL_MS = 15
# Preferred LE connection interval in 1.25 ms units: 15-30 ms (Apple HID guidelines).
CONN_INTERVAL_MIN, CONN_INTERVAL_MAX = 12, 24
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


def touchpad_score(device: InputDevice) -> int:
    """Rank touchpad candidates; real built-in touchpads score highest.

    Some USB receivers (e.g. Logitech Unifying) advertise touchpad-like
    capabilities without being one, so prefer devices the kernel marks as
    pointers/clickpads with multitouch and "touchpad" in the name.
    """
    try:
        props = set(device.input_props())
    except (AttributeError, OSError):
        props = set()
    absolute = set(device.capabilities(absinfo=False).get(ecodes.EV_ABS, []))
    score = 0
    score += 2 if ecodes.INPUT_PROP_POINTER in props else 0
    score += 1 if ecodes.INPUT_PROP_BUTTONPAD in props else 0
    score += 1 if ecodes.ABS_MT_POSITION_X in absolute else 0
    score += 2 if "touchpad" in device.name.lower() else 0
    return score


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
    if touchpad:
        candidates.sort(key=touchpad_score, reverse=True)  # stable: keeps path order on ties
    if len(candidates) > 1:
        # Only warn when the choice is a guess (no clear best touchpad).
        clear_winner = touchpad and touchpad_score(candidates[0]) > touchpad_score(candidates[1])
        (LOG.debug if clear_winner else LOG.warning)(
            "Multiple %ss found (%s); selecting %s. Use --device to choose explicitly.", kind,
            ", ".join(f"{d.path} {d.name!r}" for d in candidates), candidates[0].path)
    selected = candidates[0]
    for extra in candidates[1:]:
        extra.close()
    LOG.info("Using %s: %s (%s)", kind, selected.path, selected.name)
    return selected


def find_default_device() -> InputDevice:
    """Laptops: use the built-in touchpad. Otherwise: use the first mouse."""
    try:
        pad = find_mouse_device(touchpad=True)
        if touchpad_score(pad) >= 2:  # a real built-in touchpad, not a USB receiver
            return pad
        pad.close()
    except RuntimeError:
        pass
    return find_mouse_device()


def make_touchpad_translator(device: InputDevice, reports: ReportAccumulator, args) -> TouchpadTranslator:
    def units_per_mm(code):
        info = device.absinfo(code)
        if info.resolution:
            return float(info.resolution)
        return max(1.0, (info.max - info.min) / 100.0)  # assume a ~100 mm wide pad
    return TouchpadTranslator(reports, units_per_mm(ecodes.ABS_X), units_per_mm(ecodes.ABS_Y),
                              speed=args.touchpad_speed, scroll_mm_per_step=1.0 / args.scroll_speed,
                              natural_scroll=not args.reverse_scroll,
                              tap_to_click=not args.no_tap, three_finger_drag=not args.no_three_finger_drag)


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
                 touchpad: TouchpadTranslator | None = None, phone_connected=lambda: True):
        self.mouse = mouse
        # Only take the device away from the desktop while the phone can use it.
        self.phone_connected = phone_connected
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
            if not self._active and not self.phone_connected():
                LOG.warning("iPhone is not connected yet; staying in laptop mode. Connect it in "
                            "iPhone Settings > Bluetooth, then press the hotkey again.")
                return
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


# -- single instance -----------------------------------------------------------
DEFAULT_LOCK_FILE = "/run/iphone-mirror-bridge/bridge.lock"


def acquire_instance_lock(path: str):
    """Return an open, exclusively locked file, or None if another bridge holds it."""
    import fcntl
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


# -- main --------------------------------------------------------------------
def parse_args(argv=None, defaults: dict | None = None):
    """Parse options. `defaults` (from the UI's saved settings) replace the
    built-in defaults; options given explicitly on the command line win."""
    parser = argparse.ArgumentParser(prog="iphone-mirror-bridge", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--device", help="Mouse or touchpad /dev/input/eventN (default: built-in "
                                         "touchpad if present, otherwise the first mouse)")
    pick = parser.add_mutually_exclusive_group()
    pick.add_argument("--touchpad", action="store_true",
                      help="Use the built-in touchpad (default on laptops)")
    pick.add_argument("--mouse", action="store_true", help="Use a mouse even if a touchpad exists")
    parser.add_argument("--touchpad-speed", type=float, default=20.0,
                        help="Touchpad pointer speed in counts per mm (default 20)")
    parser.add_argument("--scroll-speed", type=float, default=1.0,
                        help="Touchpad scroll steps per mm of two-finger travel (default 1)")
    parser.add_argument("--reverse-scroll", action="store_true",
                        help="Touchpad: traditional instead of natural two-finger scrolling")
    parser.add_argument("--no-tap", action="store_true", help="Touchpad: disable tap-to-click")
    parser.add_argument("--no-three-finger-drag", action="store_true",
                        help="Touchpad: disable three-finger hold/drag")
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
    parser.add_argument("--report-interval", type=int, default=DEFAULT_REPORT_INTERVAL_MS,
                        help="Milliseconds between HID reports; movement in between is combined "
                             f"(default {DEFAULT_REPORT_INTERVAL_MS})")
    parser.add_argument("--keep-conn-interval", action="store_true",
                        help="Do not request a faster Bluetooth LE connection interval")
    parser.add_argument("--no-keyboard", action="store_true",
                        help="Act as a mouse only (no typing or Home button; re-pair after changing)")
    parser.add_argument("--pairing", choices=("code", "just-works"), default="code",
                        help="code: iPhone asks for a 6-digit code shown here (default); "
                             "just-works: no code")
    parser.add_argument("--settings-file", default=DEFAULT_SETTINGS_PATH,
                        help="Settings saved by the UI (default %(default)s)")
    parser.add_argument("--lock-file", default=DEFAULT_LOCK_FILE, help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true")
    if defaults:
        parser.set_defaults(**defaults)
        if defaults.get("touchpad") and defaults.get("mouse"):
            parser.set_defaults(mouse=False)
    args = parser.parse_args(argv)
    if args.touchpad and args.mouse:  # one from settings, the other from the command line
        explicit = argv if argv is not None else sys.argv[1:]
        args.touchpad = "--touchpad" in explicit
        args.mouse = not args.touchpad
    return args


def load_settings(argv=None) -> SettingsStore:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--settings-file", default=DEFAULT_SETTINGS_PATH)
    known, _ = pre.parse_known_args(argv)
    return SettingsStore(known.settings_file)


def main(argv=None) -> int:
    settings = load_settings(argv)
    args = parse_args(argv, settings.as_parser_defaults())
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        lock = acquire_instance_lock(args.lock_file)
    except OSError as exc:
        LOG.error("Cannot create lock file %s: %s (run with sudo)", args.lock_file, exc)
        return 2
    if lock is None:
        LOG.error("Another copy of the bridge is already running. If it is the boot service, stop it "
                  "with: sudo systemctl stop mouse-bridge")
        return 3
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
        if args.device or args.touchpad or args.mouse:
            mouse = find_mouse_device(args.device, touchpad=args.touchpad)
        else:
            mouse = find_default_device()
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
        LOG.info("Touchpad mode: one finger moves, tap/press clicks, two fingers scroll or right-click, "
                 "three fingers hold/drag")
    ble_ref = {}
    controller = BridgeController(mouse, grab=not args.no_grab, reports=reports, touchpad=touchpad,
                                  phone_connected=lambda: "ble" in ble_ref and ble_ref["ble"].subscribed)
    hci = adapter.rsplit("/", 1)[-1]
    old_interval = None
    if not args.keep_conn_interval:
        old_interval = set_le_connection_interval(hci, CONN_INTERVAL_MIN, CONN_INTERVAL_MAX)
        if old_interval:
            LOG.info("Requested LE connection interval %.2f-%.2f ms (was %.2f-%.2f ms). "
                     "Reconnect the iPhone if it was already connected.",
                     CONN_INTERVAL_MIN * 1.25, CONN_INTERVAL_MAX * 1.25,
                     old_interval[0] * 1.25, old_interval[1] * 1.25)

    control_ref = {}
    ble = BleHid(bus, adapter, args.name, use_agent=not args.no_agent,
                 include_device_info=include_device_info, keyboard=not args.no_keyboard,
                 pairing=args.pairing,
                 on_passkey=lambda dev, code: control_ref["c"].passkey(dev, code) if "c" in control_ref else None,
                 on_pairing_cancel=lambda: control_ref["c"].pairing_cancelled() if "c" in control_ref else None)
    ble_ref["ble"] = ble
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
        if old_interval:
            set_le_connection_interval(hci, *old_interval)
        loop.quit()
        return False

    pending_start = {"active": args.start_active}

    def monitor_ble() -> bool:
        if ble.failed is not None:
            shutdown(1)
            return False
        if ble.subscribed and pending_start["active"]:
            pending_start["active"] = False
            controller.set_active(True)
        elif controller.active and not ble.subscribed:
            LOG.warning("iPhone disconnected; returning the %s to the laptop",
                        "touchpad" if touchpad else "mouse")
            controller.set_active(False)
        return not stop.is_set()

    try:
        ble.start()
    except dbus.DBusException as exc:
        LOG.error("BlueZ setup failed: %s", exc)
        if old_interval:
            set_le_connection_interval(hci, *old_interval)
        return 1

    control_ref["c"] = BridgeControl(
        bus, controller=controller, ble=ble, adapter_path=adapter, settings=settings,
        authorize=logind_authorizer(bus), input_kind="touchpad" if touchpad else "mouse",
        version=__version__, on_restart=lambda: shutdown(0))
    try:
        control_ref["name"] = dbus.service.BusName(BUS_NAME, bus, do_not_queue=True)
        LOG.info("Control API available for the desktop app")
    except dbus.DBusException as exc:
        LOG.warning("Desktop app control API unavailable (%s). Is the D-Bus policy file installed?", exc)

    GLib.timeout_add(max(1, args.report_interval), flush)
    GLib.timeout_add(250, monitor_ble)
    for signum in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signum, shutdown)

    listen_hotkeys(keyboards, controller, stop, hotkey)
    threading.Thread(target=capture_mouse, name="mouse", daemon=True,
                     args=(controller, stop, lambda: GLib.idle_add(shutdown, 1))).start()
    if args.start_active:
        LOG.info("Waiting for the iPhone to connect; control switches to it automatically.")
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
