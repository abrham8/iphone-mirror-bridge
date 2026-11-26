"""Tests for the UI control API: settings store and the D-Bus object."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

import test_ble_hid  # noqa: E402
from test_ble_hid import HAVE_DBUS, call, wait_for  # noqa: E402

if HAVE_DBUS:
    import dbus
    import control
    from hid_report import TouchpadTranslator, ReportAccumulator


@unittest.skipUnless(HAVE_DBUS, "dbus-python and PyGObject are required")
class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = str(Path(self.tmp, "sub", "settings.json"))

    def test_defaults_and_round_trip(self):
        store = control.SettingsStore(self.path)
        self.assertEqual(store.values["touchpad_speed"], 20.0)
        self.assertTrue(store.set("touchpad_speed", 30))
        self.assertFalse(store.set("hotkey", "f12"))
        reloaded = control.SettingsStore(self.path)
        self.assertEqual(reloaded.values["touchpad_speed"], 30.0)
        self.assertEqual(reloaded.values["hotkey"], "F12")

    def test_validation(self):
        store = control.SettingsStore(self.path)
        with self.assertRaises(control.SettingsError):
            store.set("input", "joystick")
        with self.assertRaises(control.SettingsError):
            store.set("nope", 1)
        store.set("touchpad_speed", 999)
        self.assertEqual(store.values["touchpad_speed"], 60.0)  # clamped

    def test_corrupt_file_is_ignored(self):
        Path(self.path).parent.mkdir(parents=True)
        Path(self.path).write_text("{not json")
        self.assertEqual(control.SettingsStore(self.path).values["input"], "auto")

    def test_parser_defaults(self):
        store = control.SettingsStore(self.path)
        store.set("input", "mouse")
        store.set("natural_scroll", False)
        store.set("keyboard", False)
        d = store.as_parser_defaults()
        self.assertTrue(d["mouse"] and not d["touchpad"])
        self.assertTrue(d["reverse_scroll"] and d["no_keyboard"])

    def test_settings_feed_mouse_bridge_parser(self):
        try:
            import mouse_bridge
        except ImportError:
            self.skipTest("python3-evdev required")
        store = control.SettingsStore(self.path)
        store.set("touchpad_speed", 33)
        store.set("input", "mouse")
        args = mouse_bridge.parse_args([], store.as_parser_defaults())
        self.assertEqual(args.touchpad_speed, 33.0)
        self.assertTrue(args.mouse)
        args = mouse_bridge.parse_args(["--touchpad-speed", "10", "--touchpad"], store.as_parser_defaults())
        self.assertEqual(args.touchpad_speed, 10.0)  # command line wins
        self.assertTrue(args.touchpad and not args.mouse)


class FakeController:
    def __init__(self):
        self.active = False
        self.touchpad = TouchpadTranslator(ReportAccumulator(), 10, 10) if HAVE_DBUS else None

    def toggle(self):
        self.active = not self.active

    def set_active(self, value):
        self.active = value


class FakeBle:
    subscribed = True
    keyboard_subscribed = True

    def __init__(self):
        self.keyboard, self.consumer = [], []

    def send_keyboard(self, report):
        self.keyboard.append(bytes(report))

    def send_consumer(self, report):
        self.consumer.append(bytes(report))


@unittest.skipUnless(HAVE_DBUS, "dbus-python, PyGObject and dbus-daemon are required")
class BridgeControlDbusTests(unittest.TestCase):
    _stop_process = staticmethod(test_ble_hid.BleHidDbusTests._stop_process) if HAVE_DBUS else None

    @classmethod
    def setUpClass(cls):
        test_ble_hid.BleHidDbusTests.start_bus.__func__(cls)
        cls.allowed = {"value": True}
        cls.restarted = []
        cls.controller, cls.ble = FakeController(), FakeBle()
        cls.settings = control.SettingsStore(str(Path(cls.tmp, "settings.json")))
        cls.ctl = control.BridgeControl(
            cls.bus, controller=cls.controller, ble=cls.ble, adapter_path="/org/bluez/hci0",
            settings=cls.settings, authorize=lambda sender: cls.allowed["value"], input_kind="touchpad",
            version="9.9", on_restart=lambda: cls.restarted.append(True))
        cls.api = dbus.Interface(cls.client.get_object(cls.bus.get_unique_name(), control.OBJECT_PATH),
                                 control.INTERFACE)

    def setUp(self):
        self.allowed["value"] = True
        self.ble.keyboard.clear()
        self.ble.consumer.clear()

    def test_state(self):
        state = call(self.api.GetState)
        self.assertTrue(state["connected"])
        self.assertEqual(state["device_name"], "Test iPhone")
        self.assertEqual(state["input"], "touchpad")
        self.assertEqual(state["version"], "9.9")

    def test_set_active_and_toggle(self):
        call(self.api.SetActive, True)
        self.assertTrue(self.controller.active)
        call(self.api.Toggle)
        self.assertFalse(self.controller.active)

    def test_key_events_reach_keyboard_report(self):
        call(self.api.KeyEvent, dbus.UInt32(42), True)   # Left Shift
        call(self.api.KeyEvent, dbus.UInt32(30), True)   # A
        call(self.api.ReleaseKeys)
        self.assertEqual(self.ble.keyboard, [bytes([2, 0, 0, 0, 0, 0, 0, 0]),
                                             bytes([2, 0, 4, 0, 0, 0, 0, 0]), bytes(8)])

    def test_home_and_app_switcher(self):
        call(self.api.PressButton, "home")
        wait_for(lambda: len(self.ble.consumer) == 2)
        self.assertEqual(self.ble.consumer, [bytes([0x23, 0x02]), bytes(2)])
        self.ble.consumer.clear()
        call(self.api.PressButton, "app_switcher")
        wait_for(lambda: len(self.ble.consumer) == 4)
        with self.assertRaises(dbus.DBusException):
            call(self.api.PressButton, "volume")

    def test_settings_live_and_restart_required(self):
        self.assertTrue(call(self.api.SetSetting, "touchpad_speed", dbus.Double(31.0)))
        self.assertEqual(self.controller.touchpad.speed, 31.0)
        self.assertFalse(call(self.api.SetSetting, "hotkey", "F12"))
        saved = json.loads(Path(self.settings.path).read_text())
        self.assertEqual(saved["hotkey"], "F12")
        self.assertEqual(call(self.api.GetSettings)["touchpad_speed"], 31.0)
        with self.assertRaises(dbus.DBusException) as ctx:
            call(self.api.SetSetting, "input", "joystick")
        self.assertEqual(ctx.exception.get_dbus_name(), "org.freedesktop.DBus.Error.InvalidArgs")

    def test_passkey_is_signalled_and_in_state(self):
        codes = []
        self.client.add_signal_receiver(lambda dev, code: codes.append(str(code)), signal_name="PasskeyDisplay",
                                        dbus_interface=control.INTERFACE)
        self.ble.subscribed = False
        try:
            call(self.api.StartPairing)
            self.ctl.passkey("/org/bluez/hci0/dev_X", "482913")
            wait_for(lambda: codes)
            self.assertEqual(codes, ["482913"])
            state = call(self.api.GetState)
            self.assertTrue(state["pairing"])
            self.assertEqual(state["pairing_code"], "482913")
        finally:
            self.ble.subscribed = True
        wait_for(lambda: not call(self.api.GetState)["pairing_code"])  # cleared once connected

    def test_devices_and_forget(self):
        devices = call(self.api.ListDevices)
        self.assertEqual([str(d["name"]) for d in devices], ["Test iPhone", "Family iPad"])
        call(self.api.ForgetDevice, "11:22:33:44:55:66")
        self.assertEqual(len(call(self.api.ListDevices)), 1)
        with self.assertRaises(dbus.DBusException):
            call(self.api.ForgetDevice, "00:00:00:00:00:00")

    def test_restart(self):
        call(self.api.Restart)
        wait_for(lambda: self.restarted)

    def test_unauthorized_callers_are_refused(self):
        self.allowed["value"] = False
        for method, args in ((self.api.GetState, ()), (self.api.KeyEvent, (dbus.UInt32(30), True)),
                             (self.api.SetSetting, ("touchpad_speed", dbus.Double(10.0)))):
            with self.assertRaises(dbus.DBusException) as ctx:
                call(method, *args)
            self.assertEqual(ctx.exception.get_dbus_name(), "org.freedesktop.DBus.Error.AccessDenied")
        self.assertEqual(self.ble.keyboard, [])


if __name__ == "__main__":
    unittest.main()
