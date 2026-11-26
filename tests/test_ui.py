"""UI tests (offscreen Qt): service client against the real bridge process,
QML loading, and the video receiver. Skipped when PySide6 is missing."""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "ui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import test_ble_hid  # noqa: E402
from test_ble_hid import HAVE_DBUS  # noqa: E402

try:
    from PySide6.QtCore import QCoreApplication, QtMsgType, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    HAVE_UI = HAVE_DBUS
except (ImportError, ValueError):  # pragma: no cover - depends on host packages
    HAVE_UI = False


def qt_app():
    return QApplication.instance() or QApplication([])


def spin(predicate, timeout=8.0):
    app = qt_app()
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        app.processEvents()
        time.sleep(0.01)


@unittest.skipUnless(HAVE_UI, "PySide6, GStreamer introspection and dbus are required")
class BridgeClientEndToEndTests(unittest.TestCase):
    """UI client <-> real bridge service <-> fake BlueZ, with a simulated iPhone."""
    _stop_process = staticmethod(test_ble_hid.BleHidDbusTests._stop_process) if HAVE_DBUS else None

    @classmethod
    def setUpClass(cls):
        import dbus
        qt_app()
        test_ble_hid.BleHidDbusTests.start_bus.__func__(cls)
        env = dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=cls.address)
        cls.bridge_proc = subprocess.Popen(
            [sys.executable, str(HERE / "run_bridge_with_fake_mouse.py"), "--keep-conn-interval",
             "--lock-file", str(Path(cls.tmp) / "lock"), "--settings-file", str(Path(cls.tmp) / "s.json")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        cls.addClassCleanup(cls._stop_process, cls.bridge_proc)
        from iphone_mirror_bridge_ui.bridge import BridgeClient
        cls.client = BridgeClient(bus=dbus.bus.BusConnection(cls.address))
        spin(lambda: cls.client.available)
        # Play the iPhone: subscribe to the mouse and keyboard input reports.
        iphone = dbus.bus.BusConnection(cls.address)
        log = lambda: [str(x) for x in iphone.get_object("org.bluez", "/org/bluez/hci0").Log(
            dbus_interface="test.FakeBlueZ")]
        spin(lambda: any(line.startswith("sender ") for line in log()))
        sender = next(line.split()[1] for line in log() if line.startswith("sender "))
        objects = iphone.get_object(sender, "/com/iphone_mirror_bridge").GetManagedObjects(
            dbus_interface="org.freedesktop.DBus.ObjectManager")
        reports = sorted(str(p) for p, i in objects.items() if "org.bluez.GattCharacteristic1" in i
                         and str(i["org.bluez.GattCharacteristic1"]["UUID"]).startswith("00002a4d"))
        cls.keyboard_values = []
        iphone.add_signal_receiver(lambda iface, changed, inv: cls.keyboard_values.append(bytes(changed["Value"])),
                                   signal_name="PropertiesChanged", path=reports[1], bus_name=sender)
        for path in reports:
            iphone.get_object(sender, path).StartNotify(dbus_interface="org.bluez.GattCharacteristic1")
        cls.iphone = iphone

    def test_client_sees_connected_iphone(self):
        spin(lambda: self.client.connected and self.client.keyboard)
        self.assertEqual(self.client.deviceName, "Test iPhone")
        self.assertEqual(self.client.inputKind, "mouse")
        spin(lambda: self.client.settings.get("hotkey") == "F8")
        spin(lambda: len(self.client.devices) == 2)

    def test_typing_reaches_keyboard_report(self):
        spin(lambda: self.client.connected and self.client.keyboard)
        before = len(self.keyboard_values)
        self.client.key(30, True)    # A
        self.client.key(30, False)
        spin(lambda: len(self.keyboard_values) >= before + 2)
        self.assertEqual(self.keyboard_values[before:before + 2], [bytes([0, 0, 4, 0, 0, 0, 0, 0]), bytes(8)])

    def test_mode_switch_hotkey_is_not_forwarded(self):
        spin(lambda: self.client.connected and self.client.keyboard)
        before = len(self.keyboard_values)
        self.client.key(29, True)    # Ctrl (forwarded)
        self.client.key(66, True)    # F8 with Ctrl: the bridge's own shortcut, not forwarded
        self.client.releaseKeys()
        spin(lambda: len(self.keyboard_values) >= before + 2)
        time.sleep(0.2)
        qt_app().processEvents()
        sent = self.keyboard_values[before:]
        self.assertEqual(sent[0], bytes([1, 0, 0, 0, 0, 0, 0, 0]))
        self.assertNotIn(bytes([1, 0, 0x41, 0, 0, 0, 0, 0]), sent)

    def test_settings_round_trip(self):
        self.client.setSetting("touchpad_speed", 30)
        spin(lambda: self.client.settings.get("touchpad_speed") == 30.0)


@unittest.skipUnless(HAVE_UI, "PySide6, GStreamer introspection and dbus are required")
class QmlLoadTests(unittest.TestCase):
    def test_all_windows_load_without_qml_warnings(self):
        import dbus
        from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
        from iphone_mirror_bridge_ui.bridge import BridgeClient
        from iphone_mirror_bridge_ui.mirror import MirrorController, VideoFrameItem
        from iphone_mirror_bridge_ui.prefs import Prefs

        qt_app()
        messages = []
        qInstallMessageHandler(lambda mode, ctx, msg: messages.append(msg)
                               if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg) else None)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        conf = Path(tmp, "bus.conf")
        conf.write_text(test_ble_hid.BUS_CONFIG)
        daemon = subprocess.Popen(["dbus-daemon", "--config-file", str(conf), "--print-address", "--nofork"],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(daemon.terminate)
        bus = dbus.bus.BusConnection(daemon.stdout.readline().strip())
        prefs = Prefs(Path(tmp, "ui.json"))
        objects = {"bridge": BridgeClient(bus=bus), "prefs": prefs,
                   "receiver": MirrorController(prefs, socket_path=str(Path(tmp, "v.sock")))}
        from PySide6.QtCore import QObject, Property, Signal, Slot

        class App(QObject):
            mirrorRequested = Signal(); mirrorToggleRequested = Signal(); connectRequested = Signal()
            settingsRequested = Signal(str)
            version = Property(str, lambda s: "test", constant=True)
            displayServer = Property(str, lambda s: "X11", constant=True)

            @Slot()
            def showMirror(self): self.mirrorRequested.emit()

            @Slot()
            def showConnect(self): self.connectRequested.emit()

            @Slot(str)
            def showSettings(self, page): self.settingsRequested.emit(page)

        objects["app"] = App()
        qmlRegisterType(VideoFrameItem, "IPhoneMirrorBridge", 1, 0, "VideoFrame")
        engine = QQmlApplicationEngine()
        for name, obj in objects.items():
            engine.rootContext().setContextProperty(name, obj)
        engine.load(str(REPO / "ui/iphone_mirror_bridge_ui/qml/Main.qml"))
        self.assertTrue(engine.rootObjects())
        objects["app"].showMirror()
        objects["app"].showConnect()
        for page in ("general", "display", "controls", "notifications", "devices", "privacy"):
            objects["app"].showSettings(page)
            qt_app().processEvents()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            qt_app().processEvents()
        qInstallMessageHandler(None)
        relevant = [m for m in messages if "qml" in m.lower() or "binding" in m.lower()
                    or "TypeError" in m or "ReferenceError" in m]
        self.assertEqual(relevant, [])
        del engine


@unittest.skipUnless(HAVE_UI and shutil.which("gst-launch-1.0"), "GStreamer tools are required")
class FrameSourceTests(unittest.TestCase):
    def test_frames_arrive_from_unixfdsink(self):
        Gst.init(None)
        if not Gst.ElementFactory.find("unixfdsink"):
            self.skipTest("unixfdsink (gstreamer1.0-plugins-bad) not available")
        from iphone_mirror_bridge_ui.mirror import FrameSource
        qt_app()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sock = str(Path(tmp, "video.sock"))
        frames = []
        source = FrameSource(sock)
        source.frame.connect(lambda image: frames.append((image.width(), image.height())))
        source.start()  # before the socket exists: it must retry
        producer = subprocess.Popen(
            ["gst-launch-1.0", "-q", "videotestsrc", "is-live=true", "!",
             "video/x-raw,width=320,height=640,framerate=30/1,format=NV12", "!", "unixfdsink",
             f"socket-path={sock}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (producer.send_signal(signal.SIGINT), producer.wait(5)))
        spin(lambda: len(frames) >= 3, timeout=10)
        source.stop()
        self.assertEqual(frames[0], (320, 640))


if __name__ == "__main__":
    unittest.main()
