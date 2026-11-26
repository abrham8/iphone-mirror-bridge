"""D-Bus level tests for the BLE HID objects, using a private bus and a fake BlueZ."""
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

try:
    import dbus
    import dbus.mainloop.glib
    from gi.repository import GLib
    import ble_hid
    HAVE_DBUS = shutil.which("dbus-daemon") is not None
except ImportError:  # pragma: no cover - depends on host packages
    HAVE_DBUS = False

BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


def wait_for(predicate, timeout=5.0):
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for D-Bus activity")
        ctx.iteration(False)
        time.sleep(0.002)


def call(method, *args, **kwargs):
    """Make an asynchronous D-Bus call while servicing our own exported objects."""
    result = {}
    method(*args, reply_handler=lambda *r: result.setdefault("ok", r),
           error_handler=lambda e: result.setdefault("err", e), **kwargs)
    wait_for(lambda: result)
    if "err" in result:
        raise result["err"]
    ok = result["ok"]
    return ok[0] if len(ok) == 1 else (ok or None)


@unittest.skipUnless(HAVE_DBUS, "dbus-python, PyGObject and dbus-daemon are required")
class BleHidDbusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.start_bus()
        cls.ble = ble_hid.BleHid(cls.bus, ble_hid.find_adapter(cls.bus, "hci0"), "iPhone Mirror Bridge")
        cls.ble.ADV_RETRY_MS = 50
        cls.ble.start()
        wait_for(lambda: cls.ble.adv_registered or cls.ble.failed is not None)

    @classmethod
    def start_bus(cls):
        """Start a private dbus-daemon plus fake BlueZ; cleaned up automatically."""
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        cls.tmp = tempfile.mkdtemp()
        config = Path(cls.tmp) / "bus.conf"
        config.write_text(BUS_CONFIG)
        cls.daemon = subprocess.Popen(["dbus-daemon", "--config-file", str(config), "--print-address", "--nofork"],
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        cls.addClassCleanup(cls._stop_process, cls.daemon)
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.address = cls.daemon.stdout.readline().strip()
        cls.fake = subprocess.Popen([sys.executable, str(HERE / "fake_bluez.py"), cls.address],
                                    stdout=subprocess.PIPE, text=True)
        cls.addClassCleanup(cls._stop_process, cls.fake)
        assert cls.fake.stdout.readline().strip() == "ready"
        cls.bus = dbus.bus.BusConnection(cls.address)       # the bridge
        cls.client = dbus.bus.BusConnection(cls.address)    # plays the iPhone/BlueZ side

    @staticmethod
    def _stop_process(proc):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if proc.stdout:
            proc.stdout.close()

    def fake_log(self):
        return list(map(str, call(self.client.get_object("org.bluez", "/org/bluez/hci0").Log,
                                  dbus_interface="test.FakeBlueZ")))

    def obj(self, path, iface):
        return dbus.Interface(self.client.get_object(self.bus.get_unique_name(), path), iface)

    def chrc_by_uuid(self, uuid):
        for chrc in (c for s in self.ble.app.services for c in s.characteristics):
            if chrc.uuid == uuid:
                return chrc
        raise KeyError(uuid)

    def test_registration_succeeds_with_agent_and_pairable(self):
        self.assertIsNone(self.ble.failed)
        self.assertTrue(self.ble.app_registered)
        log = self.fake_log()
        self.assertIn("Set Pairable=True", log)
        self.assertIn("RegisterAdvertisement transient failure", log)  # retried successfully
        self.assertIn("RegisterAgent /com/iphone_mirror_bridge/agent NoInputNoOutput", log)
        self.assertIn("RegisterApplication /com/iphone_mirror_bridge services=3 chrcs=8", log)
        self.assertIn("RegisterAdvertisement /com/iphone_mirror_bridge/advertisement0 name=iPhone Mirror Bridge", log)

    def test_managed_objects_hierarchy(self):
        objects = call(self.obj(ble_hid.GattApplication.ROOT, ble_hid.DBUS_OM).GetManagedObjects)
        uuids = {str(v[ble_hid.GATT_SERVICE]["UUID"]) for v in objects.values() if ble_hid.GATT_SERVICE in v}
        self.assertEqual(uuids, {ble_hid.HID_UUID, ble_hid.BATTERY_UUID, ble_hid.DEVICE_INFO_UUID})
        descs = [v[ble_hid.GATT_DESC] for v in objects.values() if ble_hid.GATT_DESC in v]
        self.assertEqual(len(descs), 1)
        self.assertEqual(str(descs[0]["UUID"]), ble_hid.REPORT_REF_UUID)

    def test_long_read_of_report_map_honours_offset(self):
        report_map = self.chrc_by_uuid(ble_hid.REPORT_MAP_UUID)
        iface = self.obj(report_map.path, ble_hid.GATT_CHRC)
        data = b""
        while True:
            chunk = bytes(call(iface.ReadValue, {"offset": dbus.UInt16(len(data))}))[:22]
            data += chunk
            if len(chunk) < 22:
                break
        self.assertEqual(data, ble_hid.REPORT_MAP)
        with self.assertRaises(dbus.DBusException) as ctx:
            call(iface.ReadValue, {"offset": dbus.UInt16(500)})
        self.assertEqual(ctx.exception.get_dbus_name(), "org.bluez.Error.InvalidOffset")

    def test_report_reference_descriptor(self):
        desc = self.ble.report.descriptors[0]
        value = call(self.obj(desc.path, ble_hid.GATT_DESC).ReadValue, {})
        self.assertEqual(bytes(value), bytes([1, 1]))

    def test_protocol_mode_and_control_point_writes(self):
        mode = self.chrc_by_uuid(ble_hid.PROTOCOL_MODE_UUID)
        call(self.obj(mode.path, ble_hid.GATT_CHRC).WriteValue, dbus.ByteArray(b"\x00"), {})
        self.assertEqual(mode.value, b"\x01")  # boot protocol not offered
        control = self.chrc_by_uuid(ble_hid.HID_CONTROL_UUID)
        call(self.obj(control.path, ble_hid.GATT_CHRC).WriteValue, dbus.ByteArray(b"\x01"), {})
        self.assertEqual(control.value, b"\x01")
        info = self.chrc_by_uuid(ble_hid.HID_INFO_UUID)
        with self.assertRaises(dbus.DBusException):
            call(self.obj(info.path, ble_hid.GATT_CHRC).WriteValue, dbus.ByteArray(b"\x00"), {})

    def test_notifications_reach_subscriber(self):
        received = []
        self.client.add_signal_receiver(
            lambda iface, changed, inv, path=None: received.append(bytes(changed["Value"])),
            signal_name="PropertiesChanged", dbus_interface=ble_hid.DBUS_PROPS,
            path=self.ble.report.path, path_keyword="path")
        self.ble.send(bytes([1, 2, 3, 4]))  # not subscribed yet: value only
        call(self.obj(self.ble.report.path, ble_hid.GATT_CHRC).StartNotify)
        self.assertTrue(self.ble.subscribed)
        self.ble.send(bytes([1, 5, 0, 0]))
        wait_for(lambda: received)
        self.assertEqual(received, [bytes([1, 5, 0, 0])])
        call(self.obj(self.ble.report.path, ble_hid.GATT_CHRC).StopNotify)
        self.assertFalse(self.ble.subscribed)

    def test_agent_accepts_just_works_pairing(self):
        agent = self.obj(ble_hid.PairingAgent.PATH, ble_hid.AGENT)
        call(agent.RequestConfirmation, dbus.ObjectPath("/org/bluez/hci0/dev_X"), dbus.UInt32(0))
        wait_for(lambda: "Device Set Trusted=True" in self.fake_log())
        with self.assertRaises(dbus.DBusException):
            call(agent.RequestPinCode, dbus.ObjectPath("/org/bluez/hci0/dev_X"))

    def test_advertisement_properties(self):
        props = call(self.obj(ble_hid.Advertisement.PATH, ble_hid.DBUS_PROPS).GetAll, ble_hid.LE_ADV)
        self.assertEqual(int(props["Appearance"]), 0x03C2)
        self.assertEqual(list(map(str, props["ServiceUUIDs"])), [ble_hid.HID_UUID])

    def test_zz_stop_unregisters_everything(self):
        # Runs last (alphabetical). stop() uses blocking calls; the fake replies itself.
        self.ble.stop()
        log = self.fake_log()
        for entry in ("UnregisterAdvertisement", "UnregisterApplication", "UnregisterAgent"):
            self.assertTrue(any(line.startswith(entry) for line in log), entry)


class ReportMapTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_DBUS, "dbus-python required to import ble_hid")
    def test_report_map_input_size_matches_report(self):
        # 3 button bits + 5 padding bits + 3 signed bytes = 4 bytes.
        from hid_report import build_hid_report
        self.assertEqual(len(build_hid_report(0)), 4)
        self.assertEqual(ble_hid.REPORT_MAP[:2], bytes([0x05, 0x01]))
        self.assertEqual(ble_hid.REPORT_MAP.count(0xC0), 2)


if __name__ == "__main__":
    unittest.main()
