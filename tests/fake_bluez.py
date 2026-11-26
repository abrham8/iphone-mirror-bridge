"""Minimal fake BlueZ daemon for tests (runs on a private D-Bus bus).

It exports one adapter at /org/bluez/hci0 with GattManager1,
LEAdvertisingManager1 and AgentManager1 and, like real bluetoothd, calls
back into the registering application to read its object tree and
advertisement properties. Structural problems are returned as D-Bus errors.
"""
import sys

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

ADAPTER_PATH = "/org/bluez/hci0"
VALID_FLAGS = {
    "broadcast", "read", "write-without-response", "write", "notify", "indicate",
    "authenticated-signed-writes", "extended-properties", "reliable-write",
    "writable-auxiliaries", "encrypt-read", "encrypt-write", "encrypt-notify",
    "encrypt-indicate", "encrypt-authenticated-read", "encrypt-authenticated-write",
    "secure-read", "secure-write", "authorize",
}


class Failed(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.Failed"


def validate_tree(objects):
    services, chrcs = {}, {}
    for path, ifaces in objects.items():
        if "org.bluez.GattService1" in ifaces:
            services[str(path)] = ifaces["org.bluez.GattService1"]
    for path, ifaces in objects.items():
        if "org.bluez.GattCharacteristic1" in ifaces:
            props = ifaces["org.bluez.GattCharacteristic1"]
            if str(props["Service"]) not in services:
                raise Failed(f"{path}: unknown service {props['Service']}")
            if not str(path).startswith(str(props["Service"]) + "/"):
                raise Failed(f"{path}: not below its service")
            bad = set(map(str, props["Flags"])) - VALID_FLAGS
            if bad:
                raise Failed(f"{path}: invalid flags {bad}")
            chrcs[str(path)] = props
    for path, ifaces in objects.items():
        if "org.bluez.GattDescriptor1" in ifaces:
            props = ifaces["org.bluez.GattDescriptor1"]
            if str(props["Characteristic"]) not in chrcs:
                raise Failed(f"{path}: unknown characteristic")
            bad = set(map(str, props["Flags"])) - VALID_FLAGS
            if bad:
                raise Failed(f"{path}: invalid flags {bad}")
    if not services:
        raise Failed("no services")
    return len(services), len(chrcs)


class Root(dbus.service.Object):
    @dbus.service.method("org.freedesktop.DBus.ObjectManager", out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        return {
            dbus.ObjectPath(ADAPTER_PATH): {
                "org.bluez.Adapter1": {"Powered": dbus.Boolean(True), "Address": "00:11:22:33:44:55"},
                "org.bluez.GattManager1": {},
                "org.bluez.LEAdvertisingManager1": {},
            },
        }


class Agents(dbus.service.Object):
    def __init__(self, bus, log):
        super().__init__(bus, "/org/bluez")
        self.log = log

    @dbus.service.method("org.bluez.AgentManager1", in_signature="os")
    def RegisterAgent(self, path, capability):
        self.log.append(f"RegisterAgent {path} {capability}")

    @dbus.service.method("org.bluez.AgentManager1", in_signature="o")
    def RequestDefaultAgent(self, path):
        self.log.append(f"RequestDefaultAgent {path}")

    @dbus.service.method("org.bluez.AgentManager1", in_signature="o")
    def UnregisterAgent(self, path):
        self.log.append(f"UnregisterAgent {path}")


class Device(dbus.service.Object):
    def __init__(self, bus, log):
        super().__init__(bus, ADAPTER_PATH + "/dev_X")
        self.log = log

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ssv")
    def Set(self, iface, prop, value):
        self.log.append(f"Device Set {prop}={bool(value)}")


class Adapter(dbus.service.Object):
    def __init__(self, bus, log):
        super().__init__(bus, ADAPTER_PATH)
        self.bus, self.log = bus, log
        self.props = {"Powered": True, "Pairable": False}

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ssv")
    def Set(self, iface, prop, value):
        self.props[str(prop)] = value
        self.log.append(f"Set {prop}={bool(value)}")

    @dbus.service.method("org.bluez.GattManager1", in_signature="oa{sv}",
                         sender_keyword="sender", async_callbacks=("ok", "err"))
    def RegisterApplication(self, path, options, sender, ok, err):
        om = dbus.Interface(self.bus.get_object(sender, path), "org.freedesktop.DBus.ObjectManager")

        def got(objects):
            try:
                n_services, n_chrcs = validate_tree(objects)
            except Failed as exc:
                self.log.append(f"RegisterApplication rejected: {exc}")
                err(exc)
                return
            self.log.append(f"RegisterApplication {path} services={n_services} chrcs={n_chrcs}")
            self.log.append(f"sender {sender}")
            ok()

        om.GetManagedObjects(reply_handler=got, error_handler=err)

    @dbus.service.method("org.bluez.GattManager1", in_signature="o")
    def UnregisterApplication(self, path):
        self.log.append(f"UnregisterApplication {path}")

    @dbus.service.method("org.bluez.LEAdvertisingManager1", in_signature="oa{sv}",
                         sender_keyword="sender", async_callbacks=("ok", "err"))
    def RegisterAdvertisement(self, path, options, sender, ok, err):
        props = dbus.Interface(self.bus.get_object(sender, path), "org.freedesktop.DBus.Properties")

        def got(values):
            if str(values.get("Type")) != "peripheral":
                err(Failed("advertisement type must be peripheral"))
                return
            self.log.append(f"RegisterAdvertisement {path} name={values.get('LocalName')}")
            ok()

        props.GetAll("org.bluez.LEAdvertisement1", reply_handler=got, error_handler=err)

    @dbus.service.method("org.bluez.LEAdvertisingManager1", in_signature="o")
    def UnregisterAdvertisement(self, path):
        self.log.append(f"UnregisterAdvertisement {path}")

    @dbus.service.method("test.FakeBlueZ", out_signature="as")
    def Log(self):
        return dbus.Array(self.log, signature="s")


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.bus.BusConnection(sys.argv[1])
    log = []
    keep = [Root(bus, "/"), Agents(bus, log), Adapter(bus, log), Device(bus, log),
            dbus.service.BusName("org.bluez", bus)]
    print("ready", flush=True)
    GLib.MainLoop().run()
    return keep


if __name__ == "__main__":
    main()
