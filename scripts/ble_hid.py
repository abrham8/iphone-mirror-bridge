"""BlueZ D-Bus objects for a BLE HID-over-GATT mouse peripheral.

Contains the GATT application (HID, Battery and Device Information services),
the LE advertisement, and a NoInputNoOutput pairing agent. Nothing here reads
Linux input devices, so it can be exercised on any D-Bus bus in tests.
"""
from __future__ import annotations

import logging
from typing import Callable

import dbus
import dbus.exceptions
import dbus.service

from hid_report import build_hid_report

LOG = logging.getLogger("mouse-bridge.ble")

BLUEZ = "org.bluez"
DBUS_OM = "org.freedesktop.DBus.ObjectManager"
DBUS_PROPS = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
GATT_MANAGER = "org.bluez.GattManager1"
LE_ADV_MANAGER = "org.bluez.LEAdvertisingManager1"
AGENT_MANAGER = "org.bluez.AgentManager1"
GATT_SERVICE = "org.bluez.GattService1"
GATT_CHRC = "org.bluez.GattCharacteristic1"
GATT_DESC = "org.bluez.GattDescriptor1"
LE_ADV = "org.bluez.LEAdvertisement1"
AGENT = "org.bluez.Agent1"


def uuid16(short: int) -> str:
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


HID_UUID = uuid16(0x1812)
HID_INFO_UUID = uuid16(0x2A4A)
REPORT_MAP_UUID = uuid16(0x2A4B)
HID_CONTROL_UUID = uuid16(0x2A4C)
REPORT_UUID = uuid16(0x2A4D)
PROTOCOL_MODE_UUID = uuid16(0x2A4E)
REPORT_REF_UUID = uuid16(0x2908)
BATTERY_UUID = uuid16(0x180F)
BATTERY_LEVEL_UUID = uuid16(0x2A19)
DEVICE_INFO_UUID = uuid16(0x180A)
MANUFACTURER_UUID = uuid16(0x2A29)
PNP_ID_UUID = uuid16(0x2A50)

APPEARANCE_MOUSE = 0x03C2
REPORT_ID = 1

# Mouse, report ID 1: buttons (1 byte), relative X/Y/wheel (signed bytes).
REPORT_MAP = bytes([
    0x05, 0x01,       # Usage Page (Generic Desktop)
    0x09, 0x02,       # Usage (Mouse)
    0xA1, 0x01,       # Collection (Application)
    0x85, REPORT_ID,  #   Report ID (1)
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

# bcdHID 1.11, country 0, flags: normally connectable.
HID_INFORMATION = bytes([0x11, 0x01, 0x00, 0x02])
# PnP ID: vendor ID source 0x02 (USB-IF), vendor 0x1d6b (Linux Foundation),
# product 0x0246, version 0x0001. Little endian.
PNP_ID = bytes([0x02, 0x6B, 0x1D, 0x46, 0x02, 0x01, 0x00])


def to_dbus_bytes(value: bytes) -> dbus.Array:
    return dbus.Array([dbus.Byte(b) for b in value], signature="y")


def read_offset(options) -> int:
    try:
        return int(options.get("offset", 0))
    except (TypeError, ValueError):
        return 0


class InvalidOffset(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.InvalidOffset"


class NotPermitted(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.NotPermitted"


class NotSupported(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.NotSupported"


class InvalidValueLength(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.InvalidValueLength"


class ReadOnlyProperty(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.PropertyReadOnly"


class Rejected(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.Rejected"


class PropertiesObject(dbus.service.Object):
    """Exported object with a read-only org.freedesktop.DBus.Properties."""

    interface = ""

    def __init__(self, bus, path: str):
        self.path = path
        super().__init__(bus, path)

    def get_properties(self) -> dict:
        raise NotImplementedError

    @dbus.service.method(DBUS_PROPS, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        props = self.get_properties().get(interface, {})
        if prop not in props:
            raise dbus.exceptions.DBusException(
                f"No property {prop} on {interface}",
                name="org.freedesktop.DBus.Error.InvalidArgs")
        return props[prop]

    @dbus.service.method(DBUS_PROPS, in_signature="ssv")
    def Set(self, interface, prop, value):
        raise ReadOnlyProperty(f"{interface}.{prop} is read-only")

    @dbus.service.method(DBUS_PROPS, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.get_properties().get(interface, {})

    @dbus.service.signal(DBUS_PROPS, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass


class Descriptor(PropertiesObject):
    def __init__(self, bus, path, uuid: str, flags: list[str], value: bytes, characteristic):
        super().__init__(bus, path)
        self.uuid, self.flags, self.value = uuid, flags, bytes(value)
        self.characteristic = characteristic

    def get_properties(self):
        return {GATT_DESC: {"UUID": dbus.String(self.uuid),
                            "Characteristic": dbus.ObjectPath(self.characteristic.path),
                            "Flags": dbus.Array(self.flags, signature="s")}}

    @dbus.service.method(GATT_DESC, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        offset = read_offset(options)
        if offset > len(self.value):
            raise InvalidOffset()
        return to_dbus_bytes(self.value[offset:])

    @dbus.service.method(GATT_DESC, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        raise NotPermitted("Descriptor is read-only")


class Characteristic(PropertiesObject):
    def __init__(self, bus, path, uuid: str, flags: list[str], value: bytes, service,
                 on_write: Callable[[bytes], bytes | None] | None = None):
        super().__init__(bus, path)
        self.bus = bus
        self.uuid, self.flags, self.value = uuid, flags, bytes(value)
        self.service = service
        self.on_write = on_write
        self.notifying = False
        self.descriptors: list[Descriptor] = []

    def add_descriptor(self, uuid: str, flags: list[str], value: bytes) -> Descriptor:
        desc = Descriptor(self.bus, f"{self.path}/desc{len(self.descriptors)}", uuid, flags, value, self)
        self.descriptors.append(desc)
        return desc

    def get_properties(self):
        return {GATT_CHRC: {"UUID": dbus.String(self.uuid),
                            "Service": dbus.ObjectPath(self.service.path),
                            "Flags": dbus.Array(self.flags, signature="s"),
                            "Descriptors": dbus.Array([dbus.ObjectPath(d.path) for d in self.descriptors],
                                                      signature="o")}}

    @dbus.service.method(GATT_CHRC, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        offset = read_offset(options)
        if offset > len(self.value):
            raise InvalidOffset()
        return to_dbus_bytes(self.value[offset:])

    @dbus.service.method(GATT_CHRC, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        if self.on_write is None:
            raise NotPermitted("Characteristic is not writable")
        if read_offset(options):
            raise InvalidOffset()
        result = self.on_write(bytes(value))
        if result is not None:
            self.value = bytes(result)

    @dbus.service.method(GATT_CHRC)
    def StartNotify(self):
        if self.notifying:
            return
        self.notifying = True
        LOG.info("Host subscribed to notifications on %s", self.uuid[4:8])

    @dbus.service.method(GATT_CHRC)
    def StopNotify(self):
        if not self.notifying:
            return
        self.notifying = False
        LOG.info("Host unsubscribed from notifications on %s", self.uuid[4:8])

    def notify(self, value: bytes) -> bool:
        """Update the value and emit a notification if subscribed.

        Returns False so it can be used directly as a GLib idle callback.
        """
        self.value = bytes(value)
        if self.notifying:
            self.PropertiesChanged(GATT_CHRC, {"Value": to_dbus_bytes(self.value)}, [])
        return False


class Service(PropertiesObject):
    def __init__(self, bus, path, uuid: str, primary: bool = True):
        super().__init__(bus, path)
        self.bus = bus
        self.uuid, self.primary = uuid, primary
        self.characteristics: list[Characteristic] = []

    def add_characteristic(self, uuid, flags, value, on_write=None) -> Characteristic:
        chrc = Characteristic(self.bus, f"{self.path}/char{len(self.characteristics)}",
                              uuid, flags, value, self, on_write)
        self.characteristics.append(chrc)
        return chrc

    def get_properties(self):
        return {GATT_SERVICE: {"UUID": dbus.String(self.uuid),
                               "Primary": dbus.Boolean(self.primary),
                               "Characteristics": dbus.Array(
                                   [dbus.ObjectPath(c.path) for c in self.characteristics], signature="o")}}


def _accept_protocol_mode(value: bytes) -> bytes:
    # 0x00 = boot protocol (not offered), 0x01 = report protocol.
    if len(value) != 1:
        raise InvalidValueLength()
    if value != b"\x01":
        LOG.warning("Host requested unsupported protocol mode %r; staying in report mode", value)
    return b"\x01"


def _accept_control_point(value: bytes) -> bytes:
    # 0x00 = suspend, 0x01 = exit suspend. Informational only for a mouse.
    if len(value) != 1:
        raise InvalidValueLength()
    LOG.debug("HID control point: %s", "suspend" if value == b"\x00" else "exit suspend")
    return value


class GattApplication(PropertiesObject):
    ROOT = "/com/iphone_mirror_bridge"

    def __init__(self, bus, include_device_info: bool = True):
        super().__init__(bus, self.ROOT)
        self.bus = bus
        self.services: list[Service] = []

        hid = self._add_service(HID_UUID)
        hid.add_characteristic(HID_INFO_UUID, ["encrypt-read"], HID_INFORMATION)
        hid.add_characteristic(REPORT_MAP_UUID, ["encrypt-read"], REPORT_MAP)
        hid.add_characteristic(HID_CONTROL_UUID, ["write-without-response", "encrypt-write"], b"\x00",
                               on_write=_accept_control_point)
        hid.add_characteristic(PROTOCOL_MODE_UUID, ["encrypt-read", "write-without-response", "encrypt-write"],
                               b"\x01", on_write=_accept_protocol_mode)
        self.report = hid.add_characteristic(REPORT_UUID, ["encrypt-read", "encrypt-notify"], build_hid_report(0))
        # Report Reference: report ID 1, type 1 = input report.
        self.report.add_descriptor(REPORT_REF_UUID, ["encrypt-read"], bytes([REPORT_ID, 0x01]))

        battery = self._add_service(BATTERY_UUID)
        self.battery_level = battery.add_characteristic(BATTERY_LEVEL_UUID, ["read", "notify"], bytes([100]))

        if include_device_info:
            info = self._add_service(DEVICE_INFO_UUID)
            info.add_characteristic(MANUFACTURER_UUID, ["read"], b"iPhone Mirror Bridge")
            info.add_characteristic(PNP_ID_UUID, ["read"], PNP_ID)

    def _add_service(self, uuid: str) -> Service:
        service = Service(self.bus, f"{self.ROOT}/service{len(self.services)}", uuid)
        self.services.append(service)
        return service

    def get_properties(self):
        return {}

    def managed_objects(self) -> dict:
        objects = {}
        for service in self.services:
            objects[dbus.ObjectPath(service.path)] = service.get_properties()
            for chrc in service.characteristics:
                objects[dbus.ObjectPath(chrc.path)] = chrc.get_properties()
                for desc in chrc.descriptors:
                    objects[dbus.ObjectPath(desc.path)] = desc.get_properties()
        return objects

    @dbus.service.method(DBUS_OM, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        return self.managed_objects()


class Advertisement(PropertiesObject):
    PATH = "/com/iphone_mirror_bridge/advertisement0"

    def __init__(self, bus, local_name: str):
        super().__init__(bus, self.PATH)
        self.local_name = local_name

    def get_properties(self):
        # Legacy advertising data is limited to 31 bytes: flags (3) + HID UUID (4)
        # + appearance (4) leave 20 bytes, so BlueZ may shorten long names.
        return {LE_ADV: {"Type": dbus.String("peripheral"),
                         "Discoverable": dbus.Boolean(True),
                         "ServiceUUIDs": dbus.Array([HID_UUID], signature="s"),
                         "LocalName": dbus.String(self.local_name),
                         "Appearance": dbus.UInt16(APPEARANCE_MOUSE)}}

    @dbus.service.method(LE_ADV)
    def Release(self):
        LOG.info("BlueZ released the advertisement")


class PairingAgent(dbus.service.Object):
    """NoInputNoOutput agent: accepts "Just Works" pairing and trusts the device.

    BlueZ rejects incoming pairing when no agent is registered, so without
    this (or a desktop agent) the iPhone cannot bond.
    """

    PATH = "/com/iphone_mirror_bridge/agent"
    CAPABILITY = "NoInputNoOutput"

    def __init__(self, bus, on_accept: Callable[[str], None] | None = None):
        super().__init__(bus, self.PATH)
        self.on_accept = on_accept

    def _accept(self, device):
        LOG.info("Accepting pairing/authorization from %s", device)
        if self.on_accept:
            self.on_accept(str(device))

    @dbus.service.method(AGENT)
    def Release(self):
        LOG.info("BlueZ released the pairing agent")

    @dbus.service.method(AGENT, in_signature="os")
    def AuthorizeService(self, device, uuid):
        self._accept(device)

    @dbus.service.method(AGENT, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        raise Rejected("PIN codes are not supported")

    @dbus.service.method(AGENT, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        raise Rejected("Passkey entry is not supported")

    @dbus.service.method(AGENT, in_signature="ouq")
    def DisplayPasskey(self, device, passkey, entered):
        LOG.info("Passkey for %s: %06d", device, passkey)

    @dbus.service.method(AGENT, in_signature="os")
    def DisplayPinCode(self, device, pincode):
        LOG.info("PIN code for %s: %s", device, pincode)

    @dbus.service.method(AGENT, in_signature="ou")
    def RequestConfirmation(self, device, passkey):
        self._accept(device)

    @dbus.service.method(AGENT, in_signature="o")
    def RequestAuthorization(self, device):
        self._accept(device)

    @dbus.service.method(AGENT)
    def Cancel(self):
        LOG.info("Pairing request cancelled")


def find_adapter(bus, requested: str | None = None) -> str:
    """Return the object path of a powered adapter with LE peripheral APIs.

    requested may be an object path (/org/bluez/hci0) or a name (hci0).
    """
    manager = dbus.Interface(bus.get_object(BLUEZ, "/"), DBUS_OM)
    objects = manager.GetManagedObjects()
    if requested and not requested.startswith("/"):
        requested = f"/org/bluez/{requested}"
    problems = []
    for path, interfaces in sorted(objects.items()):
        props = interfaces.get(ADAPTER)
        if props is None or (requested and str(path) != requested):
            continue
        missing = [i for i in (GATT_MANAGER, LE_ADV_MANAGER) if i not in interfaces]
        if missing:
            problems.append(f"{path} lacks {', '.join(missing)}")
            continue
        if not bool(props.get("Powered", False)):
            problems.append(f"{path} is powered off (run: bluetoothctl power on)")
            continue
        return str(path)
    if requested and not problems:
        raise RuntimeError(f"Bluetooth adapter {requested} not found")
    detail = "; ".join(problems) if problems else "no Bluetooth adapter present"
    raise RuntimeError(f"No usable Bluetooth LE adapter: {detail}")


def adapter_provides_device_info(bus, adapter_path: str) -> bool:
    """True if bluetoothd already exposes a Device Information service (DeviceID set)."""
    try:
        props = dbus.Interface(bus.get_object(BLUEZ, adapter_path), DBUS_PROPS)
        uuids = props.Get(ADAPTER, "UUIDs")
    except dbus.DBusException:
        return False
    return DEVICE_INFO_UUID in {str(u).lower() for u in uuids}


DEBUGFS_BLUETOOTH = "/sys/kernel/debug/bluetooth"


def set_le_connection_interval(hci: str, min_units: int, max_units: int,
                               base: str = DEBUGFS_BLUETOOTH) -> tuple[int, int] | None:
    """Set the kernel's preferred LE connection interval (units of 1.25 ms).

    As a peripheral, Linux asks the central (the iPhone) to move the link into
    this range; the kernel default of 30-50 ms makes a pointer feel laggy.
    Returns the previous (min, max) so it can be restored, or None if the
    debugfs files are unavailable (not root, debugfs not mounted).
    """
    from pathlib import Path
    folder = Path(base) / hci
    min_file, max_file = folder / "conn_min_interval", folder / "conn_max_interval"
    try:
        old = (int(min_file.read_text().strip()), int(max_file.read_text().strip()))
        # Keep min <= max at every step: lowering min first is always valid.
        if min_units < old[1]:
            min_file.write_text(str(min_units))
            max_file.write_text(str(max_units))
        else:
            max_file.write_text(str(max_units))
            min_file.write_text(str(min_units))
        return old
    except (OSError, ValueError) as exc:
        LOG.warning("Could not set the LE connection interval via %s (%s). "
                    "Pointer latency may be higher; see docs/BACKEND_SETUP.md.", folder, exc)
        return None


class BleHid:
    """Registers the GATT app, advertisement and agent with BlueZ."""

    def __init__(self, bus, adapter_path: str, local_name: str, use_agent: bool = True,
                 include_device_info: bool = True):
        self.bus = bus
        self.adapter_path = adapter_path
        adapter = bus.get_object(BLUEZ, adapter_path)
        self.adapter_props = dbus.Interface(adapter, DBUS_PROPS)
        self.gatt = dbus.Interface(adapter, GATT_MANAGER)
        self.advertising = dbus.Interface(adapter, LE_ADV_MANAGER)
        self.app = GattApplication(bus, include_device_info)
        self.adv = Advertisement(bus, local_name)
        self.agent = PairingAgent(bus, self._trust_device) if use_agent else None
        self.report = self.app.report
        self.failed: Exception | None = None
        self.app_registered = False
        self.adv_registered = False
        self.agent_registered = False

    # -- startup -----------------------------------------------------------
    def start(self):
        try:
            self.adapter_props.Set(ADAPTER, "Pairable", dbus.Boolean(True))
        except dbus.DBusException as exc:
            LOG.warning("Could not make adapter pairable: %s", exc)
        if self.agent is not None:
            self._register_agent()
        self.gatt.RegisterApplication(dbus.ObjectPath(self.app.ROOT), {},
                                      reply_handler=self._application_registered,
                                      error_handler=self._registration_error)

    def _register_agent(self):
        manager = dbus.Interface(self.bus.get_object(BLUEZ, "/org/bluez"), AGENT_MANAGER)
        try:
            manager.RegisterAgent(dbus.ObjectPath(PairingAgent.PATH), PairingAgent.CAPABILITY)
            self.agent_registered = True
        except dbus.DBusException as exc:
            LOG.warning("Could not register pairing agent: %s", exc)
            return
        try:
            manager.RequestDefaultAgent(dbus.ObjectPath(PairingAgent.PATH))
            LOG.info("Pairing agent registered (%s)", PairingAgent.CAPABILITY)
        except dbus.DBusException as exc:
            LOG.warning("Pairing agent registered but is not the default agent: %s", exc)

    def _application_registered(self):
        self.app_registered = True
        LOG.info("BlueZ GATT HID service registered")
        self.advertising.RegisterAdvertisement(dbus.ObjectPath(self.adv.PATH), {},
                                               reply_handler=self._advertisement_registered,
                                               error_handler=self._advertisement_error)

    def _advertisement_registered(self):
        self.adv_registered = True
        LOG.info("BLE HID advertisement active as %r", self.adv.local_name)

    def _advertisement_error(self, error):
        self.failed = error
        LOG.error("Advertisement registration failed: %s", error)

    def _registration_error(self, error):
        self.failed = error
        LOG.error("GATT application registration failed: %s", error)

    def _trust_device(self, device_path: str):
        try:
            props = dbus.Interface(self.bus.get_object(BLUEZ, device_path), DBUS_PROPS)
            props.Set(DEVICE, "Trusted", dbus.Boolean(True),
                      reply_handler=lambda: LOG.info("Marked %s as trusted", device_path),
                      error_handler=lambda e: LOG.warning("Could not trust %s: %s", device_path, e))
        except dbus.DBusException as exc:
            LOG.warning("Could not trust %s: %s", device_path, exc)

    # -- runtime -----------------------------------------------------------
    @property
    def subscribed(self) -> bool:
        return self.report.notifying

    def send(self, report: bytes) -> None:
        """Send one input report. Must be called from the GLib main loop thread."""
        self.report.notify(report)

    # -- shutdown ----------------------------------------------------------
    def stop(self):
        calls = []
        if self.adv_registered:
            calls.append((self.advertising.UnregisterAdvertisement, self.adv.PATH))
        if self.app_registered:
            calls.append((self.gatt.UnregisterApplication, self.app.ROOT))
        if self.agent_registered:
            manager = dbus.Interface(self.bus.get_object(BLUEZ, "/org/bluez"), AGENT_MANAGER)
            calls.append((manager.UnregisterAgent, PairingAgent.PATH))
        self.adv_registered = self.app_registered = self.agent_registered = False
        for method, path in calls:
            try:
                method(dbus.ObjectPath(path), timeout=3)
            except dbus.DBusException as exc:
                LOG.debug("Cleanup call failed: %s", exc)
