"""Run the real bridge process against fake BlueZ and a simulated mouse."""
import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ble_hid import HAVE_DBUS, HERE, BleHidDbusTests, call, wait_for

if HAVE_DBUS:
    import dbus
    import ble_hid
try:
    import evdev  # noqa: F401
    HAVE_EVDEV = True
except ImportError:  # pragma: no cover
    HAVE_EVDEV = False


@unittest.skipUnless(HAVE_DBUS and HAVE_EVDEV, "dbus-python, PyGObject, evdev and dbus-daemon are required")
class EndToEndTests(unittest.TestCase):
    _stop_process = staticmethod(BleHidDbusTests._stop_process)

    @classmethod
    def setUpClass(cls):
        BleHidDbusTests.start_bus.__func__(cls)

    def setUp(self):
        env = dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=self.address)
        self.bridge = subprocess.Popen(
            [sys.executable, str(HERE / "run_bridge_with_fake_mouse.py"), "--start-active", "--debug", "--keep-conn-interval"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self._stop_process, self.bridge)

    def fake_log(self):
        return list(map(str, call(self.client.get_object("org.bluez", "/org/bluez/hci0").Log,
                                  dbus_interface="test.FakeBlueZ")))

    def test_reports_flow_and_shutdown_releases(self):
        wait_for(lambda: any(l.startswith("RegisterAdvertisement") for l in self.fake_log()), timeout=10)
        self.assertIn("RegisterApplication /com/iphone_mirror_bridge services=2 chrcs=6", self.fake_log())
        sender = next(l.split()[1] for l in self.fake_log() if l.startswith("sender "))
        objects = call(dbus.Interface(self.client.get_object(sender, ble_hid.GattApplication.ROOT),
                                      ble_hid.DBUS_OM).GetManagedObjects)
        report_path = next(p for p, i in objects.items()
                           if ble_hid.GATT_CHRC in i and str(i[ble_hid.GATT_CHRC]["UUID"]) == ble_hid.REPORT_UUID)
        received = []
        self.client.add_signal_receiver(lambda iface, changed, inv: received.append(bytes(changed["Value"])),
                                        signal_name="PropertiesChanged", dbus_interface=ble_hid.DBUS_PROPS,
                                        path=report_path, bus_name=sender)
        call(dbus.Interface(self.client.get_object(sender, report_path), ble_hid.GATT_CHRC).StartNotify)
        wait_for(lambda: len(received) >= 6, timeout=10)
        # Button held (bit 0) and motion split into <=127 steps, all positive X.
        moving = [r for r in received if r[1]]
        self.assertTrue(moving)
        self.assertTrue(all(r[0] == 1 and r[1] <= 127 for r in moving), received)

        self.bridge.send_signal(signal.SIGTERM)
        wait_for(lambda: self.bridge.poll() is not None, timeout=10)
        output = self.bridge.stdout.read()
        self.assertEqual(self.bridge.returncode, 0, output)
        wait_for(lambda: received[-1] == bytes(4), timeout=2)
        self.assertIn("ungrabbed", output)
        self.assertIn("already provides a Device Information service", output)
        log = self.fake_log()
        for entry in ("UnregisterAdvertisement", "UnregisterApplication", "UnregisterAgent"):
            self.assertTrue(any(line.startswith(entry) for line in log), (entry, log))


if __name__ == "__main__":
    unittest.main()
