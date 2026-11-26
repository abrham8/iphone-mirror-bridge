"""Client for the bridge service's control API (system D-Bus).

dbus-python is driven by the GLib main loop, which Qt uses as its event
dispatcher on Linux, so replies and signals arrive on the Qt main thread.
"""
from __future__ import annotations

import logging

import dbus
import dbus.mainloop.glib
from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

LOG = logging.getLogger("iphone-mirror-bridge-ui")

BUS_NAME = "io.github.abrham8.IPhoneMirrorBridge"
OBJECT_PATH = "/io/github/abrham8/IPhoneMirrorBridge"
INTERFACE = "io.github.abrham8.IPhoneMirrorBridge1"

# evdev codes of keys the bridge uses as its own Ctrl+<key> mode switch.
KEY_LEFTCTRL, KEY_RIGHTCTRL = 29, 97
HOTKEY_CODES = {"F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65, "F8": 66,
                "F9": 67, "F10": 68, "F11": 87, "F12": 88}


def _plain(value):
    """Convert dbus types to plain Python for QML."""
    if isinstance(value, dbus.Boolean):
        return bool(value)
    if isinstance(value, (dbus.Int16, dbus.Int32, dbus.Int64, dbus.UInt16, dbus.UInt32, dbus.UInt64,
                          dbus.Byte)):
        return int(value)
    if isinstance(value, dbus.Double):
        return float(value)
    if isinstance(value, (dbus.String, dbus.ObjectPath)):
        return str(value)
    if isinstance(value, dbus.Dictionary):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, dbus.Array):
        return [_plain(v) for v in value]
    return value


class BridgeClient(QObject):
    stateChanged = Signal()
    devicesChanged = Signal()
    settingsChanged = Signal()
    errorChanged = Signal()
    passkeyShown = Signal(str)
    connectionChanged = Signal(bool, str)   # connected, device name (for notifications)

    def __init__(self, bus=None, parent=None):
        super().__init__(parent)
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = bus or dbus.SystemBus()
        self._api = None
        self._state: dict = {}
        self._devices: list = []
        self._settings: dict = {}
        self._error = ""
        self._ctrl_down: set[int] = set()
        self._bus.add_signal_receiver(self._on_state, signal_name="StateChanged", dbus_interface=INTERFACE,
                                      path=OBJECT_PATH)
        self._bus.add_signal_receiver(self._on_passkey, signal_name="PasskeyDisplay", dbus_interface=INTERFACE,
                                      path=OBJECT_PATH)
        self._bus.add_signal_receiver(self._on_settings, signal_name="SettingsChanged",
                                      dbus_interface=INTERFACE, path=OBJECT_PATH)
        self._bus.watch_name_owner(BUS_NAME, self._on_owner)

    # -- service presence ------------------------------------------------------
    def _on_owner(self, owner):
        if owner:
            self._api = dbus.Interface(self._bus.get_object(BUS_NAME, OBJECT_PATH, introspect=False), INTERFACE)
            self._call("GetState", reply=self._on_state)
            self._call("GetSettings", reply=self._on_settings)
            self.refreshDevices()
        else:
            self._api = None
            self._set_state({})
        self.stateChanged.emit()

    def _call(self, method, *args, reply=None, error=None):
        if self._api is None:
            return
        def on_error(exc):
            name = exc.get_dbus_name() if isinstance(exc, dbus.DBusException) else ""
            if name == "org.freedesktop.DBus.Error.AccessDenied":
                self._set_error("Only the person signed in at this computer can control the bridge.")
            else:
                self._set_error(str(exc.get_dbus_message() if isinstance(exc, dbus.DBusException) else exc))
            LOG.warning("%s failed: %s", method, exc)
            if error:
                error(exc)
        getattr(self._api, method)(*args, reply_handler=reply or (lambda *a: None), error_handler=on_error)

    def _set_error(self, text):
        if text != self._error:
            self._error = text
            self.errorChanged.emit()

    # -- incoming ---------------------------------------------------------------
    def _set_state(self, state):
        was_connected = bool(self._state.get("connected"))
        self._state = state
        now_connected = bool(state.get("connected"))
        if was_connected != now_connected:
            self.connectionChanged.emit(now_connected, state.get("device_name", ""))
            self.refreshDevices()
        self.stateChanged.emit()

    def _on_state(self, state):
        self._set_state(_plain(state))

    def _on_passkey(self, _device, code):
        self.passkeyShown.emit(str(code))

    def _on_settings(self, settings):
        self._settings = _plain(settings)
        self.settingsChanged.emit()

    # -- properties ---------------------------------------------------------------
    def _s(self, key, default):
        return self._state.get(key, default)

    available = Property(bool, lambda s: s._api is not None, notify=stateChanged)
    connected = Property(bool, lambda s: bool(s._s("connected", False)), notify=stateChanged)
    active = Property(bool, lambda s: bool(s._s("active", False)), notify=stateChanged)
    deviceName = Property(str, lambda s: s._s("device_name", ""), notify=stateChanged)
    keyboard = Property(bool, lambda s: bool(s._s("keyboard", False)), notify=stateChanged)
    pairing = Property(bool, lambda s: bool(s._s("pairing", False)), notify=stateChanged)
    pairingCode = Property(str, lambda s: s._s("pairing_code", ""), notify=stateChanged)
    inputKind = Property(str, lambda s: s._s("input", ""), notify=stateChanged)
    serviceVersion = Property(str, lambda s: s._s("version", ""), notify=stateChanged)
    devices = Property("QVariantList", lambda s: s._devices, notify=devicesChanged)
    settings = Property("QVariantMap", lambda s: s._settings, notify=settingsChanged)
    error = Property(str, lambda s: s._error, notify=errorChanged)

    @property
    def hotkey_code(self) -> int:
        return HOTKEY_CODES.get(str(self._settings.get("hotkey", "F8")).upper(), 66)

    # -- actions ------------------------------------------------------------------
    @Slot()
    def toggle(self):
        self._call("Toggle")

    @Slot(bool)
    def setActive(self, active):
        self._call("SetActive", dbus.Boolean(active))

    @Slot(int, bool)
    def key(self, evdev_code, pressed):
        """Forward a key typed in the mirror window. The bridge's own mode
        switch (Ctrl+F8) is not forwarded."""
        if evdev_code in (KEY_LEFTCTRL, KEY_RIGHTCTRL):
            (self._ctrl_down.add if pressed else self._ctrl_down.discard)(evdev_code)
        if evdev_code == self.hotkey_code and self._ctrl_down:
            return
        if self.connected and self.keyboard:
            self._call("KeyEvent", dbus.UInt32(evdev_code), dbus.Boolean(pressed))

    @Slot()
    def releaseKeys(self):
        self._ctrl_down.clear()
        if self.connected and self.keyboard:
            self._call("ReleaseKeys")

    @Slot()
    def home(self):
        self._call("PressButton", "home")

    @Slot()
    def appSwitcher(self):
        self._call("PressButton", "app_switcher")

    @Slot()
    def startPairing(self):
        self._call("StartPairing")

    @Slot()
    def stopPairing(self):
        self._call("StopPairing")

    @Slot()
    def refreshDevices(self):
        def done(devices):
            self._devices = _plain(devices)
            self.devicesChanged.emit()
        self._call("ListDevices", reply=done)

    @Slot(str)
    def forget(self, address):
        self._call("ForgetDevice", address, reply=lambda: self.refreshDevices())

    @Slot(str, "QVariant")
    def setSetting(self, key, value):
        """Change a service setting; restarts the service when required."""
        if isinstance(value, bool):
            value = dbus.Boolean(value)
        elif isinstance(value, float):
            value = dbus.Double(value)
        elif isinstance(value, int):
            value = dbus.Double(float(value)) if key.endswith("_speed") else dbus.Int32(value)
        else:
            value = dbus.String(str(value))

        def done(live):
            if not bool(live):
                QTimer.singleShot(300, self.restartService)
        self._call("SetSetting", key, value, reply=done)

    @Slot()
    def restartService(self):
        self._call("Restart")
