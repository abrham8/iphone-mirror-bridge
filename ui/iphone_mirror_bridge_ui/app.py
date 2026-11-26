"""iPhone Mirror Bridge desktop app: tray icon plus the mirror, connect and
settings windows (Qt Quick).

    iphone-mirror-bridge-ui            open the mirror window
    iphone-mirror-bridge-ui --tray     start in the system tray only (autostart)
    iphone-mirror-bridge-ui --connect  open the Connect iPhone window
    iphone-mirror-bridge-ui --settings open Settings
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .prefs import Prefs, config_dir, load_raw

LOG = logging.getLogger("iphone-mirror-bridge-ui")
HERE = Path(__file__).resolve().parent
SOCKET_NAME = f"iphone-mirror-bridge-ui-{os.getuid()}"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="iphone-mirror-bridge-ui", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--tray", action="store_true", help="Start in the system tray without opening a window")
    group.add_argument("--connect", action="store_true", help="Open the Connect iPhone window")
    group.add_argument("--settings", action="store_true", help="Open Settings")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def apply_display_server() -> str:
    """Honour the "Display server" preference before Qt starts."""
    choice = load_raw(config_dir() / "iphone-mirror-bridge" / "ui.json")["displayServer"]
    if choice == "wayland":
        os.environ["QT_QPA_PLATFORM"] = "wayland"
    elif choice == "x11":
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    return choice


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    apply_display_server()

    from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
    from PySide6.QtGui import QAction, QGuiApplication, QIcon
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    from .bridge import BridgeClient
    from .mirror import MirrorController, VideoFrameItem

    command = "connect" if args.connect else "settings" if args.settings else "tray" if args.tray else "mirror"

    # Single instance: hand the request to the running app.
    probe = QLocalSocket()
    probe.connectToServer(SOCKET_NAME)
    if probe.waitForConnected(300):
        probe.write(command.encode())
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return 0

    QApplication.setApplicationName("iPhone Mirror Bridge")
    QApplication.setDesktopFileName("iphone-mirror-bridge-ui")
    qt_app = QApplication(sys.argv[:1])
    qt_app.setQuitOnLastWindowClosed(False)
    icon_path = HERE / "assets" / "app-logo.svg"
    qt_app.setWindowIcon(QIcon(str(icon_path)))

    prefs = Prefs()
    bridge = BridgeClient()
    mirror = MirrorController(prefs)

    class App(QObject):
        mirrorRequested = Signal()
        mirrorToggleRequested = Signal()
        connectRequested = Signal()
        settingsRequested = Signal(str)

        version = Property(str, lambda self: __version__, constant=True)
        displayServer = Property(str, lambda self: "Wayland" if QGuiApplication.platformName() == "wayland"
                                 else "X11", constant=True)

        @Slot()
        def showMirror(self):
            if not mirror.running:
                mirror.start()
            self.mirrorRequested.emit()

        @Slot()
        def showConnect(self):
            self.connectRequested.emit()

        @Slot(str)
        def showSettings(self, page=""):
            self.settingsRequested.emit(page)

        @Slot()
        def quit(self):
            mirror.stop()
            QTimer.singleShot(300, qt_app.quit)

    app = App()

    qmlRegisterType(VideoFrameItem, "IPhoneMirrorBridge", 1, 0, "VideoFrame")
    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    for name, obj in (("bridge", bridge), ("receiver", mirror), ("prefs", prefs), ("app", app)):
        ctx.setContextProperty(name, obj)
    engine.load(str(HERE / "qml" / "Main.qml"))
    if not engine.rootObjects():
        LOG.error("Could not load the interface (QML errors above)")
        return 1

    # -- notifications -------------------------------------------------------
    tray = QSystemTrayIcon(QIcon(str(HERE / "assets" / "tray-icon.svg")))

    def notify(title, body):
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "iPhone Mirror Bridge", "-i", "iphone-mirror-bridge",
                              title, body])
        elif tray.isVisible():
            tray.showMessage(title, body, QIcon(str(icon_path)))

    def on_connection(connected, name):
        if prefs.notifyConnection:
            notify("iPhone connected" if connected else "iPhone disconnected",
                   name or ("Press Ctrl+F8 to control it" if connected else ""))

    bridge.connectionChanged.connect(on_connection)
    mirror.message.connect(lambda t, b: prefs.notifyMirroring and notify(t, b))
    bridge.passkeyShown.connect(lambda code: (app.showConnect(), notify("Pairing code", f"Enter {code} on your iPhone")))
    mirror.streamingChanged.connect(lambda: mirror.streaming and prefs.showOnMirror and app.mirrorRequested.emit())

    # -- tray ------------------------------------------------------------------
    menu = QMenu()
    status_action = menu.addAction("")
    status_action.setEnabled(False)
    menu.addSeparator()
    control_action = QAction("Control iPhone", menu, checkable=True)
    control_action.triggered.connect(lambda checked: bridge.setActive(checked))
    menu.addAction(control_action)
    screen_action = menu.addAction("Show iPhone screen", app.showMirror)
    receiver_action = menu.addAction("", lambda: mirror.stop() if mirror.running else mirror.start())
    menu.addSeparator()
    menu.addAction("Connect iPhone…", app.showConnect)
    menu.addAction("Settings…", lambda: app.showSettings(""))
    menu.addSeparator()
    menu.addAction("Quit", app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: app.mirrorToggleRequested.emit()
                           if reason == QSystemTrayIcon.Trigger else None)

    def refresh_tray():
        if not bridge.available:
            status = "Bridge service not running"
        elif bridge.connected:
            status = f"Connected · {bridge.deviceName or 'iPhone'}"
        else:
            status = "iPhone not connected"
        status_action.setText(status)
        control_action.setEnabled(bridge.connected)
        control_action.setChecked(bridge.active)
        receiver_action.setText("Stop screen mirroring" if mirror.running else "Start screen mirroring")
        screen_action.setEnabled(True)
        tray.setToolTip(f"iPhone Mirror Bridge — {status}")

    for sig in (bridge.stateChanged, mirror.runningChanged):
        sig.connect(refresh_tray)
    refresh_tray()
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray.show()

    # -- single-instance server ------------------------------------------------
    server = QLocalServer()
    QLocalServer.removeServer(SOCKET_NAME)
    server.listen(SOCKET_NAME)

    def handle(cmd):
        {"connect": app.showConnect, "settings": lambda: app.showSettings(""),
         "mirror": app.showMirror, "tray": lambda: None}.get(cmd, app.showMirror)()

    def on_connection_request():
        sock = server.nextPendingConnection()
        sock.waitForReadyRead(300)
        handle(bytes(sock.readAll()).decode(errors="replace").strip())
        sock.disconnectFromServer()

    server.newConnection.connect(on_connection_request)

    # -- startup -----------------------------------------------------------------
    if prefs.autoStartMirroring or command == "mirror":
        mirror.start()

    def first_view():
        if command == "tray":
            # First run: nothing paired yet, so help the user connect.
            if bridge.available and not bridge.devices:
                app.showConnect()
            return
        handle(command)

    QTimer.singleShot(800, first_view)

    # Quit cleanly (stopping the receiver) on SIGINT/SIGTERM; the timer lets
    # Python run its signal handlers while Qt's event loop is in C++.
    import signal
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: app.quit())
    heartbeat = QTimer(interval=400, timeout=lambda: None)
    heartbeat.start()

    code = qt_app.exec()
    mirror.stop()
    del engine
    return code


if __name__ == "__main__":
    sys.exit(main())
