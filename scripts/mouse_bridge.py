#!/usr/bin/env python3
"""Bridge a Linux mouse to an iPhone using BlueZ BLE HID over GATT.

This is a first hardware-oriented implementation. It requires a running
BlueZ system daemon and a BLE adapter capable of peripheral mode.
"""
from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from typing import Iterable

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from evdev import InputDevice, ecodes, list_devices
from hid_report import MouseState, build_hid_report
from gi.repository import GLib

LOG = logging.getLogger("mouse-bridge")
BLUEZ = "org.bluez"
DBUS_OM = "org.freedesktop.DBus.ObjectManager"
GATT_MANAGER = "org.bluez.GattManager1"
LE_ADV_MANAGER = "org.bluez.LEAdvertisingManager1"
GATT_SERVICE = "org.bluez.GattService1"
GATT_CHRC = "org.bluez.GattCharacteristic1"
GATT_DESC = "org.bluez.GattDescriptor1"
LE_ADV = "org.bluez.LEAdvertisement1"

HID_UUID = "00001812-0000-1000-8000-00805f9b34fb"
HID_INFO_UUID = "00002a4a-0000-1000-8000-00805f9b34fb"
REPORT_MAP_UUID = "00002a4b-0000-1000-8000-00805f9b34fb"
HID_CONTROL_UUID = "00002a4c-0000-1000-8000-00805f9b34fb"
REPORT_UUID = "00002a4d-0000-1000-8000-00805f9b34fb"
PROTOCOL_MODE_UUID = "00002a4e-0000-1000-8000-00805f9b34fb"
REPORT_REF_UUID = "00002908-0000-1000-8000-00805f9b34fb"

# Mouse, report ID 1: buttons (1 byte), relative X/Y/wheel (signed bytes).
REPORT_MAP = bytes([
    0x05, 0x01,       # Usage Page (Generic Desktop)
    0x09, 0x02,       # Usage (Mouse)
    0xA1, 0x01,       # Collection (Application)
    0x85, 0x01,       #   Report ID (1)
    0x09, 0x01,       #   Usage (Pointer)
    0xA1, 0x00,       #   Collection (Physical)
    0x05, 0x09,       #     Usage Page (Button)
    0x19, 0x01,       #     Usage Minimum (1)
    0x29, 0x03,       #     Usage Maximum (3)
    0x15, 0x00,       #     Logical Minimum (0)
    0x25, 0x01,       #     Logical Maximum (1)
    0x95, 0x03,       #     Report Count (3)
    0x75, 0x01,       #     Report Size (1)
    0x81, 0x02,       #     Input (Data, Variable, Absolute)
    0x95, 0x01,       #     Report Count (1)
    0x75, 0x05,       #     Report Size (5)
    0x81, 0x03,       #     Input (Constant, Variable, Absolute)
    0x05, 0x01,       #     Usage Page (Generic Desktop)
    0x09, 0x30,       #     Usage (X)
    0x09, 0x31,       #     Usage (Y)
    0x09, 0x38,       #     Usage (Wheel)
    0x15, 0x81,       #     Logical Minimum (-127)
    0x25, 0x7F,       #     Logical Maximum (127)
    0x75, 0x08,       #     Report Size (8)
    0x95, 0x03,       #     Report Count (3)
    0x81, 0x06,       #     Input (Data, Variable, Relative)
    0xC0,             #   End Collection
    0xC0,             # End Collection
])


def device_is_mouse(device: InputDevice) -> bool:
    caps = device.capabilities(absinfo=False)
    rel = set(caps.get(ecodes.EV_REL, []))
    keys = set(caps.get(ecodes.EV_KEY, []))
    return (ecodes.REL_X in rel and ecodes.REL_Y in rel
            and ecodes.BTN_LEFT in keys and ecodes.BTN_RIGHT in keys)


def find_mouse_device(path: str | None = None) -> InputDevice:
    """Open the requested mouse or select the first plausible physical mouse."""
    if path:
        device = InputDevice(path)
        if not device_is_mouse(device):
            device.close()
            raise RuntimeError(f"{path} does not expose REL_X/REL_Y and mouse buttons")
        return device

    candidates: list[InputDevice] = []
    for candidate_path in list_devices():
        try:
            device = InputDevice(candidate_path)
            if device_is_mouse(device) and not device.name.lower().startswith(("virtual", "uinput")):
                candidates.append(device)
            else:
                device.close()
        except (OSError, PermissionError):
            continue
    if not candidates:
        raise RuntimeError("No readable physical mouse found. Check --device and /dev/input permissions.")
    if len(candidates) > 1:
        LOG.warning("Multiple mice found; selecting %s (%s). Use --device to choose explicitly.",
                    candidates[0].path, candidates[0].name)
    selected = candidates[0]
    for extra in candidates[1:]:
        extra.close()
    LOG.info("Using mouse: %s (%s)", selected.path, selected.name)
    return selected


def grab_device(device: InputDevice) -> None:
    device.grab()


def release_device(device: InputDevice) -> None:
    try:
        device.ungrab()
    except OSError:
        pass


class DBusObject(dbus.service.Object):
    def __init__(self, bus, path: str):
        self.path = path
        super().__init__(bus, path)


class GattApplication(DBusObject):
    ROOT = "/com/iphone_mirror_bridge"

    def __init__(self, bus):
        super().__init__(bus, self.ROOT)
        self.service = HidService(bus, self.ROOT + "/service0")

    @dbus.service.method(DBUS_OM, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        objects = {dbus.ObjectPath(self.service.path): self.service.get_properties()}
        for characteristic in self.service.characteristics:
            objects[dbus.ObjectPath(characteristic.path)] = characteristic.get_properties()
            if characteristic.descriptor:
                objects[dbus.ObjectPath(characteristic.descriptor.path)] = characteristic.descriptor.get_properties()
        return objects


class HidService(DBusObject):
    path = GattApplication.ROOT + "/service0"

    def __init__(self, bus, path):
        super().__init__(bus, path)
        self.characteristics = [
            HidCharacteristic(bus, path + "/char0", HID_INFO_UUID, ["encrypt-read"], bytes([0x11, 0x01, 0x00, 0x02])),
            HidCharacteristic(bus, path + "/char1", REPORT_MAP_UUID, ["encrypt-read"], REPORT_MAP),
            HidCharacteristic(bus, path + "/char2", HID_CONTROL_UUID, ["write-without-response", "encrypt-write"], b"\x00"),
            HidCharacteristic(bus, path + "/char3", PROTOCOL_MODE_UUID, ["encrypt-read", "encrypt-write"], b"\x01"),
            HidCharacteristic(bus, path + "/char4", REPORT_UUID, ["encrypt-read", "encrypt-notify"], build_hid_report(0), report=True),
        ]
        self.characteristics[-1].descriptor = ReportReference(bus, path + "/char4/desc0")

    def get_properties(self):
        return {GATT_SERVICE: {"UUID": dbus.String(HID_UUID),
                               "Primary": dbus.Boolean(True),
                               "Characteristics": dbus.Array([dbus.ObjectPath(c.path) for c in self.characteristics], signature="o")}}

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        return self.get_properties()[interface][prop]

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ssv")
    def Set(self, interface, prop, value):
        raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.PropertyReadOnly")

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.get_properties().get(interface, {})


class HidCharacteristic(DBusObject):
    def __init__(self, bus, path, uuid, flags, value, report=False):
        super().__init__(bus, path)
        self.uuid, self.flags = uuid, flags
        self.value = bytes(value)
        self.notifying = False
        self.report = report
        self.descriptor = None

    def get_properties(self):
        props = {"UUID": dbus.String(self.uuid),
                 "Service": dbus.ObjectPath(HidService.path),
                 "Flags": dbus.Array(self.flags, signature="s"),
                 "Descriptors": dbus.Array([dbus.ObjectPath(self.descriptor.path)] if self.descriptor else [], signature="o"),
                 "Value": dbus.Array([dbus.Byte(b) for b in self.value], signature="y")}
        return {GATT_CHRC: props}

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        return self.get_properties()[interface][prop]

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ssv")
    def Set(self, interface, prop, value):
        if prop == "Value" and self.uuid == PROTOCOL_MODE_UUID:
            self.value = bytes(value)
            return
        raise dbus.exceptions.DBusException("org.bluez.Error.NotPermitted")

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.get_properties().get(interface, {})

    @dbus.service.method(GATT_CHRC, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        return dbus.Array([dbus.Byte(b) for b in self.value], signature="y")

    @dbus.service.method(GATT_CHRC, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        if self.uuid == HID_CONTROL_UUID:
            self.value = bytes(value)
        elif self.uuid == PROTOCOL_MODE_UUID:
            if bytes(value) != b"\x01":
                raise dbus.exceptions.DBusException("org.bluez.Error.NotSupported")
            self.value = b"\x01"

    @dbus.service.method(GATT_CHRC)
    def StartNotify(self):
        self.notifying = True
        LOG.info("iPhone subscribed to HID input reports")

    @dbus.service.method(GATT_CHRC)
    def StopNotify(self):
        self.notifying = False
        LOG.info("iPhone unsubscribed from HID input reports")

    @dbus.service.signal("org.freedesktop.DBus.Properties", signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def notify(self, report: bytes):
        self.value = report
        if self.notifying:
            self.PropertiesChanged(GATT_CHRC, {"Value": dbus.Array([dbus.Byte(b) for b in report], signature="y")}, [])


class ReportReference(DBusObject):
    def __init__(self, bus, path):
        super().__init__(bus, path)

    def get_properties(self):
        return {GATT_DESC: {"UUID": dbus.String(REPORT_REF_UUID),
                            "Characteristic": dbus.ObjectPath(HidService.path + "/char4"),
                            "Flags": dbus.Array(["encrypt-read"], signature="s"),
                            "Value": dbus.Array([dbus.Byte(1), dbus.Byte(1)], signature="y")}}

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        return self.get_properties()[interface][prop]

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ssv")
    def Set(self, interface, prop, value):
        raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.PropertyReadOnly")

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.get_properties().get(interface, {})

    @dbus.service.method(GATT_DESC, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        return dbus.Array([dbus.Byte(1), dbus.Byte(1)], signature="y")


class Advertisement(DBusObject):
    PATH = "/com/iphone_mirror_bridge/advertisement0"

    def __init__(self, bus, adapter_path, local_name):
        super().__init__(bus, self.PATH)
        self.local_name = local_name

    def get_properties(self):
        return {LE_ADV: {"Type": dbus.String("peripheral"),
                         "ServiceUUIDs": dbus.Array([HID_UUID], signature="s"),
                         "LocalName": dbus.String(self.local_name),
                         "Appearance": dbus.UInt16(0x03C2),
                         "Includes": dbus.Array(["tx-power"], signature="s")}}

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        return self.get_properties()[interface][prop]

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ssv")
    def Set(self, interface, prop, value):
        raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.PropertyReadOnly")

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.get_properties().get(interface, {})

    @dbus.service.method(LE_ADV)
    def Release(self):
        LOG.info("BlueZ released the advertisement")


class BleHid:
    def __init__(self, adapter: str, local_name: str):
        self.bus = dbus.SystemBus()
        self.adapter_path = adapter
        self.adapter = self.bus.get_object(BLUEZ, adapter)
        self.app = GattApplication(self.bus)
        self.adv = Advertisement(self.bus, adapter, local_name)
        self.report = self.app.service.characteristics[-1]
        self.gatt = dbus.Interface(self.adapter, GATT_MANAGER)
        self.advertising = dbus.Interface(self.adapter, LE_ADV_MANAGER)
        self.registered = threading.Event()
        self.failed: Exception | None = None

    def start(self):
        self.gatt.RegisterApplication(dbus.ObjectPath(self.app.ROOT), {},
            reply_handler=self._application_registered,
            error_handler=self._registration_error)

    def _application_registered(self):
        LOG.info("BlueZ GATT HID service registered")
        self.registered.set()
        self.advertising.RegisterAdvertisement(dbus.ObjectPath(self.adv.PATH), {},
            reply_handler=lambda: LOG.info("BLE HID advertisement active"),
            error_handler=self._advertisement_error)

    def _advertisement_error(self, error):
        self.failed = error
        LOG.error("Advertisement registration failed: %s", error)

    def _registration_error(self, error):
        self.failed = error
        self.registered.set()
        LOG.error("GATT application registration failed: %s", error)

    def publish(self, report: bytes):
        GLib.idle_add(self.report.notify, report)

    def stop(self):
        try:
            self.advertising.UnregisterAdvertisement(dbus.ObjectPath(self.adv.PATH))
        except dbus.DBusException:
            pass
        try:
            self.gatt.UnregisterApplication(dbus.ObjectPath(self.app.ROOT))
        except dbus.DBusException:
            pass


def find_adapter(bus) -> str:
    manager = dbus.Interface(bus.get_object(BLUEZ, "/"), DBUS_OM)
    objects = manager.GetManagedObjects()
    for path, interfaces in objects.items():
        adapter_props = interfaces.get("org.bluez.Adapter1")
        if (adapter_props and bool(adapter_props.get("Powered", False))
                and GATT_MANAGER in interfaces and LE_ADV_MANAGER in interfaces):
            return str(path)
    raise RuntimeError("No powered Bluetooth adapter found. Enable Bluetooth with bluetoothctl.")


def find_keyboard(path: str | None = None) -> list[InputDevice]:
    found = []
    for p in ([path] if path else list_devices()):
        try:
            dev = InputDevice(p)
            keys = set(dev.capabilities(absinfo=False).get(ecodes.EV_KEY, []))
            if ecodes.KEY_F8 in keys and ecodes.KEY_LEFTCTRL in keys:
                found.append(dev)
            else:
                dev.close()
        except OSError:
            continue
    return found


def keyboard_hotkey(devices: Iterable[InputDevice], active: threading.Event, stop: threading.Event):
    """Ctrl+F8 toggles phone mode; the mouse is grabbed only in phone mode."""
    def listen(device):
        ctrl = False
        try:
            for event in device.read_loop():
                if stop.is_set():
                    break
                if event.type != ecodes.EV_KEY:
                    continue
                if event.code in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL):
                    ctrl = event.value != 0
                elif event.code == ecodes.KEY_F8 and event.value == 1 and ctrl:
                    if active.is_set():
                        active.clear()
                        LOG.info("Laptop control mode")
                    else:
                        active.set()
                        LOG.info("iPhone control mode")
        except OSError as exc:
            LOG.warning("Hotkey device stopped: %s", exc)
    for dev in devices:
        threading.Thread(target=listen, args=(dev,), daemon=True).start()


def capture_mouse(device: InputDevice, ble: BleHid, active: threading.Event, stop: threading.Event):
    state = MouseState()
    last_sent = time.monotonic()
    was_active = active.is_set()
    try:
        for event in device.read_loop():
            if stop.is_set():
                break
            if event.type == ecodes.EV_REL:
                if event.code == ecodes.REL_X: state.dx += event.value
                elif event.code == ecodes.REL_Y: state.dy += event.value
                elif event.code == ecodes.REL_WHEEL:
                    state.wheel += event.value
            elif event.type == ecodes.EV_KEY:
                mask = {ecodes.BTN_LEFT: 0, ecodes.BTN_RIGHT: 1, ecodes.BTN_MIDDLE: 2}.get(event.code)
                if mask is not None:
                    if event.value: state.buttons |= 1 << mask
                    else: state.buttons &= ~(1 << mask)
            now = time.monotonic()
            is_active = active.is_set()
            if was_active and not is_active:
                ble.publish(build_hid_report(0))
                state.buttons = 0
            was_active = is_active
            if not is_active:
                state.dx = state.dy = state.wheel = 0
                continue
            if state.dx or state.dy or state.wheel or now - last_sent > 0.03:
                ble.publish(state.report())
                last_sent = now
    except OSError as exc:
        LOG.error("Mouse input ended: %s", exc)
    finally:
        ble.publish(build_hid_report(0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="Mouse /dev/input/eventN (auto-detected if omitted)")
    parser.add_argument("--adapter", help="BlueZ adapter object path (auto-detected if omitted)")
    parser.add_argument("--name", default="iPhone Mirror Bridge", help="BLE advertised name")
    parser.add_argument("--hotkey-device", help="Keyboard event device for Ctrl+F8 mode toggle")
    parser.add_argument("--start-active", action="store_true", help="Start with mouse routed to iPhone")
    parser.add_argument("--no-grab", action="store_true", help="Do not exclusively grab the local mouse")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    adapter = args.adapter or find_adapter(bus)
    mouse = find_mouse_device(args.device)
    keyboards = find_keyboard(args.hotkey_device)
    if not keyboards:
        LOG.warning("No keyboard with Ctrl+F8 detected. Use --hotkey-device or restart to change modes.")
    active, stop = threading.Event(), threading.Event()
    if args.start_active: active.set()
    ble = BleHid(adapter, args.name)
    loop = GLib.MainLoop()
    ble.start()

    def shutdown(*_):
        stop.set()
        active.clear()
        release_device(mouse)
        ble.stop()
        loop.quit()

    def monitor_ble():
        if ble.failed is not None:
            shutdown()
            return False
        return not stop.is_set()

    GLib.timeout_add(250, monitor_ble)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    keyboard_hotkey(keyboards, active, stop)
    threading.Thread(target=capture_mouse, args=(mouse, ble, active, stop), daemon=True).start()

    try:
        if not args.no_grab:
            # Grab/ungrab is done by the main thread so mode changes are safe.
            def update_grab():
                desired = active.is_set()
                try:
                    if desired and not getattr(update_grab, "grabbed", False):
                        grab_device(mouse)
                        update_grab.grabbed = True
                    elif not desired and getattr(update_grab, "grabbed", False):
                        release_device(mouse)
                        update_grab.grabbed = False
                except OSError as exc:
                    LOG.error("Could not change mouse grab state: %s", exc)
                    active.clear()
                return not stop.is_set()
            GLib.timeout_add(100, update_grab)
        loop.run()
    finally:
        shutdown()
        mouse.close()
        for keyboard in keyboards:
            keyboard.close()


if __name__ == "__main__":
    main()
