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
from typing import Iterable

import dbus
import dbus.mainloop.glib
from evdev import InputDevice, ecodes, list_devices
from gi.repository import GLib

from ble_hid import BleHid, adapter_provides_device_info, find_adapter
from hid_report import HotkeyDetector, ReportAccumulator

LOG = logging.getLogger("mouse-bridge")
FLUSH_INTERVAL_MS = 8
BUTTONS = {ecodes.BTN_LEFT: 0, ecodes.BTN_RIGHT: 1, ecodes.BTN_MIDDLE: 2}


# -- input device discovery --------------------------------------------------
def device_is_mouse(device: InputDevice) -> bool:
    caps = device.capabilities(absinfo=False)
    rel = set(caps.get(ecodes.EV_REL, []))
    keys = set(caps.get(ecodes.EV_KEY, []))
    return (ecodes.REL_X in rel and ecodes.REL_Y in rel
            and ecodes.BTN_LEFT in keys and ecodes.BTN_RIGHT in keys)


def device_is_keyboard(device: InputDevice) -> bool:
    keys = set(device.capabilities(absinfo=False).get(ecodes.EV_KEY, []))
    return ecodes.KEY_F8 in keys and (ecodes.KEY_LEFTCTRL in keys or ecodes.KEY_RIGHTCTRL in keys)


def find_mouse_device(path: str | None = None) -> InputDevice:
    """Open the requested mouse or select the first plausible physical mouse."""
    if path:
        device = InputDevice(path)
        if not device_is_mouse(device):
            device.close()
            raise RuntimeError(f"{path} does not expose REL_X/REL_Y and mouse buttons")
        LOG.info("Using mouse: %s (%s)", device.path, device.name)
        return device

    candidates: list[InputDevice] = []
    for candidate_path in sorted(list_devices()):
        try:
            device = InputDevice(candidate_path)
        except OSError:
            continue
        if device_is_mouse(device) and not device.name.lower().startswith(("virtual", "uinput")):
            candidates.append(device)
        else:
            device.close()
    if not candidates:
        raise RuntimeError("No readable physical mouse found. Run as root or use --device /dev/input/eventN.")
    if len(candidates) > 1:
        LOG.warning("Multiple mice found (%s); selecting %s. Use --device to choose explicitly.",
                    ", ".join(f"{d.path} {d.name!r}" for d in candidates), candidates[0].path)
    selected = candidates[0]
    for extra in candidates[1:]:
        extra.close()
    LOG.info("Using mouse: %s (%s)", selected.path, selected.name)
    return selected


def find_keyboards(path: str | None = None, exclude: Iterable[str] = ()) -> list[InputDevice]:
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
        if device_is_keyboard(dev):
            found.append(dev)
        else:
            if path:
                dev.close()
                raise RuntimeError(f"{path} has no Ctrl and F8 keys")
            dev.close()
    for dev in found:
        LOG.info("Listening for Ctrl+F8 on %s (%s)", dev.path, dev.name)
    return found


# -- mode control ------------------------------------------------------------
class BridgeController:
    """Owns the phone/laptop mode, the evdev grab and the pending HID reports."""

    def __init__(self, mouse: InputDevice, grab: bool = True):
        self.mouse = mouse
        self.grab_enabled = grab
        self.reports = ReportAccumulator()
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
            self._active = True
            LOG.info("iPhone control mode (Ctrl+F8 to return)")
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
        if event.type == ecodes.EV_REL:
            if event.code == ecodes.REL_X:
                self.reports.add_motion(dx=event.value)
            elif event.code == ecodes.REL_Y:
                self.reports.add_motion(dy=event.value)
            elif event.code == ecodes.REL_WHEEL:
                self.reports.add_motion(wheel=event.value)
        elif event.type == ecodes.EV_KEY and event.code in BUTTONS and event.value in (0, 1):
            self.reports.set_button(BUTTONS[event.code], bool(event.value))

    def shutdown(self) -> None:
        with self._lock:
            if self._active:
                self._active = False
                self.reports.release_all()
            self._ungrab_locked()


# -- worker threads ----------------------------------------------------------
def listen_hotkeys(devices: Iterable[InputDevice], controller: BridgeController, stop: threading.Event):
    def listen(device: InputDevice):
        detector = HotkeyDetector((ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL), ecodes.KEY_F8)
        try:
            for event in device.read_loop():
                if stop.is_set():
                    return
                if event.type == ecodes.EV_KEY and detector.feed(event.code, event.value):
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
    parser.add_argument("--device", help="Mouse /dev/input/eventN (auto-detected if omitted)")
    parser.add_argument("--adapter", help="Bluetooth adapter, e.g. hci0 or /org/bluez/hci0 (auto-detected)")
    parser.add_argument("--name", default="iPhone Mirror Bridge", help="BLE advertised name")
    parser.add_argument("--hotkey-device", help="Keyboard /dev/input/eventN for the Ctrl+F8 toggle")
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
    try:
        bus = dbus.SystemBus()
        adapter = find_adapter(bus, args.adapter)
        LOG.info("Using Bluetooth adapter %s", adapter)
        mouse = find_mouse_device(args.device)
        keyboards = find_keyboards(args.hotkey_device, exclude=[mouse.path])
    except (RuntimeError, OSError, dbus.DBusException) as exc:
        LOG.error("%s", exc)
        return 2
    if not keyboards:
        LOG.warning("No keyboard with Ctrl+F8 found; mode switching is unavailable. Use --hotkey-device.")

    include_device_info = not args.no_device_info
    if include_device_info and adapter_provides_device_info(bus, adapter):
        LOG.info("bluetoothd already provides a Device Information service; not adding another")
        include_device_info = False

    controller = BridgeController(mouse, grab=not args.no_grab)
    ble = BleHid(bus, adapter, args.name, use_agent=not args.no_agent,
                 include_device_info=include_device_info)
    stop = threading.Event()
    loop = GLib.MainLoop()
    exit_code = 0

    def flush() -> bool:
        reports = controller.reports.drain()
        if ble.subscribed:
            for report in reports:
                ble.send(report)
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

    listen_hotkeys(keyboards, controller, stop)
    threading.Thread(target=capture_mouse, name="mouse", daemon=True,
                     args=(controller, stop, lambda: GLib.idle_add(shutdown, 1))).start()
    if args.start_active:
        controller.set_active(True)

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
