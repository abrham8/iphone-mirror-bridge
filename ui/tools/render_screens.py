"""Render the UI windows to PNG with sample data (for design review).

    QT_QPA_PLATFORM=offscreen python3 ui/tools/render_screens.py OUTDIR [--frame IMAGE]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QLinearGradient  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iphone_mirror_bridge_ui.mirror import VideoFrameItem  # noqa: E402
from iphone_mirror_bridge_ui.prefs import Prefs  # noqa: E402


class FakeBridge(QObject):
    stateChanged = Signal(); devicesChanged = Signal(); settingsChanged = Signal(); errorChanged = Signal()
    connectionChanged = Signal(bool, str)
    available = Property(bool, lambda s: True, notify=stateChanged)
    connected = Property(bool, lambda s: s._connected, notify=stateChanged)
    active = Property(bool, lambda s: False, notify=stateChanged)
    deviceName = Property(str, lambda s: "Abrham's Phone" if s._connected else "", notify=stateChanged)
    keyboard = Property(bool, lambda s: True, notify=stateChanged)
    pairing = Property(bool, lambda s: s._code != "", notify=stateChanged)
    pairingCode = Property(str, lambda s: s._code, notify=stateChanged)
    inputKind = Property(str, lambda s: "touchpad", notify=stateChanged)
    serviceVersion = Property(str, lambda s: "0.2.0", notify=stateChanged)
    devices = Property("QVariantList", lambda s: s._devices, notify=devicesChanged)
    settings = Property("QVariantMap", lambda s: {"input": "auto", "touchpad_speed": 20.0, "scroll_speed": 1.0,
                                                    "natural_scroll": True, "tap_to_click": True,
                                                    "three_finger_drag": True, "hotkey": "F8",
                                                    "keyboard": True, "pairing": "code"}, notify=settingsChanged)
    error = Property(str, lambda s: "", notify=errorChanged)

    def __init__(self):
        super().__init__()
        self._connected, self._code = False, "482913"
        self._devices = [{"name": "Abrham's Phone", "address": "AA:BB:CC:DD:EE:FF", "connected": True},
                         {"name": "Family iPad", "address": "11:22:33:44:55:66", "connected": False}]

    @Slot()
    def toggle(self): pass
    @Slot()
    def releaseKeys(self): pass
    @Slot()
    def home(self): pass
    @Slot()
    def appSwitcher(self): pass
    @Slot()
    def startPairing(self): pass
    @Slot()
    def stopPairing(self): pass
    @Slot()
    def refreshDevices(self): pass
    @Slot()
    def restartService(self): pass
    @Slot(bool)
    def setActive(self, v): pass
    @Slot(int, bool)
    def key(self, c, p): pass
    @Slot(str)
    def forget(self, a): pass
    @Slot(str, "QVariant")
    def setSetting(self, k, v): pass


class FakeMirror(QObject):
    runningChanged = Signal(); streamingChanged = Signal(); sizeChanged = Signal(); firewallChanged = Signal()
    frameReady = Signal(QImage)
    running = Property(bool, lambda s: True, notify=runningChanged)
    streaming = Property(bool, lambda s: s._streaming, notify=streamingChanged)
    videoWidth = Property(int, lambda s: 590, notify=sizeChanged)
    videoHeight = Property(int, lambda s: 1280, notify=sizeChanged)
    landscape = Property(bool, lambda s: False, notify=sizeChanged)
    firewallBlocked = Property(bool, lambda s: False, notify=firewallChanged)

    def __init__(self):
        super().__init__()
        self._streaming = "--live" in sys.argv
    @Slot()
    def start(self): pass
    @Slot()
    def stop(self): pass
    @Slot()
    def restart(self): pass
    @Slot()
    def allowFirewall(self): pass
    @Slot()
    def checkFirewall(self): pass


class FakeApp(QObject):
    mirrorRequested = Signal(); mirrorToggleRequested = Signal(); connectRequested = Signal()
    settingsRequested = Signal(str)
    version = Property(str, lambda s: "0.2.0", constant=True)
    displayServer = Property(str, lambda s: "Wayland", constant=True)
    showMirror = Slot()(lambda self: None)
    showConnect = Slot()(lambda self: None)
    showSettings = Slot(str)(lambda self, p: None)


def sample_frame(path=None):
    if path:
        return QImage(path)
    img = QImage(590, 1280, QImage.Format_RGBX8888)
    p = QPainter(img)
    g = QLinearGradient(0, 0, 0, 1280)
    g.setColorAt(0, QColor("#203a5c")); g.setColorAt(1, QColor("#c98f5a"))
    p.fillRect(img.rect(), g)
    p.setPen(QColor("white"))
    f = p.font(); f.setPixelSize(64); p.setFont(f)
    p.drawText(img.rect(), 0x84, "Live iPhone\nvideo")
    p.end()
    return img


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    frame_path = sys.argv[sys.argv.index("--frame") + 1] if "--frame" in sys.argv else None
    app = QApplication(sys.argv[:1])
    prefs = Prefs(Path(out, "ui.json"))
    bridge, mirror, fake_app = FakeBridge(), FakeMirror(), FakeApp()
    qmlRegisterType(VideoFrameItem, "IPhoneMirrorBridge", 1, 0, "VideoFrame")
    engine = QQmlApplicationEngine()
    for name, obj in (("bridge", bridge), ("receiver", mirror), ("prefs", prefs), ("app", fake_app)):
        engine.rootContext().setContextProperty(name, obj)
    engine.load(str(ROOT / "iphone_mirror_bridge_ui/qml/Main.qml"))
    assert engine.rootObjects()
    shots = []

    def grab(window, name):
        window.show()
        def do():
            window.grabWindow().save(str(out / f"{name}.png"))
            shots.append(name)
        QTimer.singleShot(600, do)

    from PySide6.QtGui import QGuiApplication
    windows = {w.title(): w for w in QGuiApplication.topLevelWindows()}
    connect = windows["Connect your iPhone"]
    settings = windows["iPhone Mirror Bridge Settings"]
    mirror_win = windows["iPhone Mirror Bridge"]
    grab(connect, "connect")
    grab(settings, "settings-display")
    grab(mirror_win, "mirror-waiting")

    def live():
        bridge._connected = True; bridge.stateChanged.emit()
        mirror._streaming = True; mirror.streamingChanged.emit()
        mirror.frameReady.emit(sample_frame(frame_path))
        QTimer.singleShot(600, lambda: (mirror_win.grabWindow().save(str(out / "mirror-live.png")), shots.append("live")))
        for i, page in enumerate(["general", "controls", "notifications", "devices", "privacy"]):
            QTimer.singleShot(900 + i * 400, lambda p=page: settings.setProperty("page", p))
            QTimer.singleShot(1200 + i * 400, lambda p=page: settings.grabWindow().save(str(out / f"settings-{p}.png")))
    QTimer.singleShot(1200, live)
    QTimer.singleShot(4500, app.quit)
    app.exec()
    del engine
    print("rendered:", sorted(p.name for p in out.glob("*.png")))


if __name__ == "__main__":
    main()
