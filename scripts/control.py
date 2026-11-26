"""Control API of the bridge service for the desktop UI (system D-Bus).

Bus name / object / interface: io.github.abrham8.IPhoneMirrorBridge
/io/github/abrham8/IPhoneMirrorBridge, io.github.abrham8.IPhoneMirrorBridge1

Only root and users with an active, local (non-remote) login session may
call it, because it can type on the paired iPhone.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Callable

LOG = logging.getLogger("mouse-bridge.control")

BUS_NAME = "io.github.abrham8.IPhoneMirrorBridge"
OBJECT_PATH = "/io/github/abrham8/IPhoneMirrorBridge"
INTERFACE = "io.github.abrham8.IPhoneMirrorBridge1"
DEFAULT_SETTINGS_PATH = "/var/lib/iphone-mirror-bridge/settings.json"

# key: (default, type, allowed values or (min, max), applies_live)
SETTINGS_SCHEMA = {
    "input": ("auto", str, {"auto", "touchpad", "mouse"}, False),
    "touchpad_speed": (20.0, float, (5.0, 60.0), True),
    "scroll_speed": (1.0, float, (0.25, 4.0), True),
    "natural_scroll": (True, bool, None, True),
    "tap_to_click": (True, bool, None, True),
    "three_finger_drag": (True, bool, None, True),
    "hotkey": ("F8", str, None, False),
    "keyboard": (True, bool, None, False),
    "pairing": ("code", str, {"code", "just-works"}, False),
}


class SettingsError(ValueError):
    pass


class SettingsStore:
    """Validated settings persisted as JSON (written atomically)."""

    def __init__(self, path: str = DEFAULT_SETTINGS_PATH):
        self.path = path
        self.values = {key: spec[0] for key, spec in SETTINGS_SCHEMA.items()}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            LOG.warning("Ignoring unreadable settings file %s: %s", self.path, exc)
            return
        for key, value in stored.items():
            try:
                self.values[key] = self.validate(key, value)
            except SettingsError as exc:
                LOG.warning("Ignoring setting %s: %s", key, exc)

    @staticmethod
    def validate(key: str, value):
        if key not in SETTINGS_SCHEMA:
            raise SettingsError(f"unknown setting {key!r}")
        _default, kind, allowed, _live = SETTINGS_SCHEMA[key]
        if kind is bool:
            if not isinstance(value, (bool, int)):
                raise SettingsError(f"{key} must be true or false")
            return bool(value)
        if kind is float:
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise SettingsError(f"{key} must be a number") from exc
            low, high = allowed
            return max(low, min(high, value))
        value = str(value)
        if key == "hotkey":
            value = value.upper()
            if not value or not value.replace("_", "").isalnum():
                raise SettingsError("hotkey must be a key name such as F8")
        elif allowed is not None and value not in allowed:
            raise SettingsError(f"{key} must be one of {sorted(allowed)}")
        return value

    def set(self, key: str, value) -> bool:
        """Validate, store and save. Returns True if the change applies live."""
        self.values[key] = self.validate(key, value)
        self.save()
        return SETTINGS_SCHEMA[key][3]

    def save(self) -> None:
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".settings-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.values, handle, indent=2, sort_keys=True)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def as_parser_defaults(self) -> dict:
        """Translate settings into mouse_bridge argparse defaults."""
        v = self.values
        return {
            "touchpad": v["input"] == "touchpad",
            "mouse": v["input"] == "mouse",
            "touchpad_speed": v["touchpad_speed"],
            "scroll_speed": v["scroll_speed"],
            "reverse_scroll": not v["natural_scroll"],
            "no_tap": not v["tap_to_click"],
            "no_three_finger_drag": not v["three_finger_drag"],
            "hotkey": v["hotkey"],
            "no_keyboard": not v["keyboard"],
            "pairing": v["pairing"],
        }


# -- D-Bus object ---------------------------------------------------------------
import dbus  # noqa: E402  (kept below the pure-Python part for readability)
import dbus.exceptions  # noqa: E402
import dbus.service  # noqa: E402
from gi.repository import GLib  # noqa: E402

from hid_report import CONSUMER_HOME, KeyboardState, consumer_report  # noqa: E402

BLUEZ = "org.bluez"
DBUS_OM = "org.freedesktop.DBus.ObjectManager"
DBUS_PROPS = "org.freedesktop.DBus.Properties"


class AccessDenied(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.AccessDenied"


class InvalidArgs(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


def logind_authorizer(bus) -> Callable[[str], bool]:
    """Allow root and users with an active local session (checked via logind)."""
    cache: dict[str, tuple[float, bool]] = {}

    def check(sender: str) -> bool:
        now = time.monotonic()
        if sender in cache and now - cache[sender][0] < 5:
            return cache[sender][1]
        allowed = False
        try:
            uid = int(bus.get_unix_user(sender))
            if uid == 0:
                allowed = True
            else:
                manager = dbus.Interface(bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1"),
                                         "org.freedesktop.login1.Manager")
                for _sid, session_uid, _user, _seat, path in manager.ListSessions():
                    if int(session_uid) != uid:
                        continue
                    props = dbus.Interface(bus.get_object("org.freedesktop.login1", path), DBUS_PROPS)
                    info = props.GetAll("org.freedesktop.login1.Session")
                    if bool(info.get("Active")) and not bool(info.get("Remote")):
                        allowed = True
                        break
        except dbus.DBusException as exc:
            LOG.warning("Could not check caller %s: %s", sender, exc)
        cache[sender] = (now, allowed)
        return allowed

    return check


class BridgeControl(dbus.service.Object):
    """The UI-facing API. All methods run in the GLib main loop thread."""

    PAIRING_WINDOW_S = 120

    def __init__(self, bus, *, controller, ble, adapter_path: str, settings: SettingsStore,
                 authorize: Callable[[str], bool], input_kind: str, version: str,
                 on_restart: Callable[[], None] | None = None):
        super().__init__(bus, OBJECT_PATH)
        self.bus = bus
        self.controller = controller
        self.ble = ble
        self.adapter_path = adapter_path
        self.settings = settings
        self.authorize = authorize
        self.input_kind = input_kind
        self.version = version
        self.on_restart = on_restart
        self.keyboard = KeyboardState()
        self.pairing_code = ""
        self.pairing_device = ""
        self._pairing_until = 0.0
        self._last_state = None
        self.bus_name = None
        GLib.timeout_add(400, self._poll_state)

    # -- helpers -----------------------------------------------------------
    def _check(self, sender):
        if sender is not None and not self.authorize(sender):
            raise AccessDenied("Only the user signed in at this computer can control the bridge")

    def _bluez_devices(self) -> list[dict]:
        try:
            manager = dbus.Interface(self.bus.get_object(BLUEZ, "/"), DBUS_OM)
            objects = manager.GetManagedObjects()
        except dbus.DBusException:
            return []
        devices = []
        for path, ifaces in objects.items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev or not str(path).startswith(self.adapter_path + "/"):
                continue
            if not bool(dev.get("Paired", False)):
                continue
            devices.append({
                "path": str(path),
                "address": str(dev.get("Address", "")),
                "name": str(dev.get("Alias", dev.get("Name", "iPhone"))),
                "connected": bool(dev.get("Connected", False)),
                "icon": str(dev.get("Icon", "")),
            })
        devices.sort(key=lambda d: (not d["connected"], d["name"].lower()))
        return devices

    def state(self) -> dict:
        connected = bool(self.ble.subscribed)
        device = next((d for d in self._bluez_devices() if d["connected"]), None) if connected else None
        pairing = time.monotonic() < self._pairing_until
        return {
            "connected": connected,
            "active": bool(self.controller.active),
            "device_name": device["name"] if device else "",
            "device_address": device["address"] if device else "",
            "keyboard": bool(self.ble.keyboard_subscribed),
            "input": self.input_kind,
            "pairing": pairing,
            "pairing_code": self.pairing_code,
            "version": self.version,
        }

    @staticmethod
    def _to_dbus(state: dict) -> dbus.Dictionary:
        return dbus.Dictionary({k: v for k, v in state.items()}, signature="sv")

    def _poll_state(self) -> bool:
        state = self.state()
        if state["connected"] and self.pairing_code:
            # Pairing finished: the iPhone subscribed to input reports.
            self.pairing_code = ""
            self._pairing_until = 0.0
            state = self.state()
        if state != self._last_state:
            self._last_state = state
            self.StateChanged(self._to_dbus(state))
        return True

    # -- pairing callbacks (from the BlueZ agent) ---------------------------
    def passkey(self, device_path: str, code: str) -> None:
        self.pairing_code = code
        self.pairing_device = device_path
        self._pairing_until = max(self._pairing_until, time.monotonic() + 60)
        self.PasskeyDisplay(device_path, code)
        self._poll_state()

    def pairing_cancelled(self) -> None:
        self.pairing_code = ""
        self._poll_state()

    # -- methods -------------------------------------------------------------
    @dbus.service.method(INTERFACE, out_signature="a{sv}", sender_keyword="sender")
    def GetState(self, sender=None):
        self._check(sender)
        return self._to_dbus(self.state())

    @dbus.service.method(INTERFACE, in_signature="b", sender_keyword="sender")
    def SetActive(self, active, sender=None):
        self._check(sender)
        if bool(active) and not self.controller.active:
            self.controller.toggle()  # refuses (and logs) if the iPhone is not connected
        elif not bool(active) and self.controller.active:
            self.controller.set_active(False)
        self._poll_state()

    @dbus.service.method(INTERFACE, sender_keyword="sender")
    def Toggle(self, sender=None):
        self._check(sender)
        self.controller.toggle()
        self._poll_state()

    @dbus.service.method(INTERFACE, in_signature="ub", sender_keyword="sender")
    def KeyEvent(self, evdev_code, pressed, sender=None):
        """Forward a key from the UI window (Linux evdev key code)."""
        self._check(sender)
        report = self.keyboard.key(int(evdev_code), bool(pressed))
        if report is not None and self.ble.keyboard_subscribed:
            self.ble.send_keyboard(report)

    @dbus.service.method(INTERFACE, sender_keyword="sender")
    def ReleaseKeys(self, sender=None):
        self._check(sender)
        report = self.keyboard.release_all()
        if self.ble.keyboard_subscribed:
            self.ble.send_keyboard(report)

    @dbus.service.method(INTERFACE, in_signature="s", sender_keyword="sender")
    def PressButton(self, name, sender=None):
        """'home' or 'app_switcher' (double Home press)."""
        self._check(sender)
        name = str(name)
        if name not in ("home", "app_switcher"):
            raise InvalidArgs(f"unknown button {name!r}")
        presses = 2 if name == "app_switcher" else 1
        self._press_consumer(CONSUMER_HOME, presses)

    def _press_consumer(self, usage: int, presses: int) -> None:
        def press(remaining):
            self.ble.send_consumer(consumer_report(usage))
            GLib.timeout_add(60, release, remaining)
            return False

        def release(remaining):
            self.ble.send_consumer(consumer_report(0))
            if remaining > 1:
                GLib.timeout_add(150, press, remaining - 1)
            return False

        press(presses)

    @dbus.service.method(INTERFACE, out_signature="aa{sv}", sender_keyword="sender")
    def ListDevices(self, sender=None):
        self._check(sender)
        return dbus.Array([dbus.Dictionary(d, signature="sv") for d in self._bluez_devices()],
                          signature="a{sv}")

    @dbus.service.method(INTERFACE, in_signature="s", sender_keyword="sender")
    def ForgetDevice(self, address, sender=None):
        self._check(sender)
        device = next((d for d in self._bluez_devices() if d["address"] == str(address)), None)
        if device is None:
            raise InvalidArgs(f"no paired device {address}")
        adapter = dbus.Interface(self.bus.get_object(BLUEZ, self.adapter_path), "org.bluez.Adapter1")
        adapter.RemoveDevice(dbus.ObjectPath(device["path"]))
        LOG.info("Forgot device %s (%s)", device["name"], device["address"])
        self._poll_state()

    @dbus.service.method(INTERFACE, sender_keyword="sender")
    def StartPairing(self, sender=None):
        """Open a pairing window: adapter pairable, advertisement active."""
        self._check(sender)
        try:
            props = dbus.Interface(self.bus.get_object(BLUEZ, self.adapter_path), DBUS_PROPS)
            props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(True))
        except dbus.DBusException as exc:
            LOG.warning("Could not make adapter pairable: %s", exc)
        self._pairing_until = time.monotonic() + self.PAIRING_WINDOW_S
        self._poll_state()

    @dbus.service.method(INTERFACE, sender_keyword="sender")
    def StopPairing(self, sender=None):
        self._check(sender)
        self._pairing_until = 0.0
        self.pairing_code = ""
        self._poll_state()

    @dbus.service.method(INTERFACE, out_signature="a{sv}", sender_keyword="sender")
    def GetSettings(self, sender=None):
        self._check(sender)
        return dbus.Dictionary(self.settings.values, signature="sv")

    @dbus.service.method(INTERFACE, in_signature="sv", out_signature="b", sender_keyword="sender")
    def SetSetting(self, key, value, sender=None):
        """Store a setting. Returns True if it applied immediately, False if
        the service must restart (call Restart)."""
        self._check(sender)
        try:
            live = self.settings.set(str(key), value)
        except SettingsError as exc:
            raise InvalidArgs(str(exc)) from exc
        if live:
            self._apply_live(str(key))
        self.SettingsChanged(dbus.Dictionary(self.settings.values, signature="sv"))
        return live

    def _apply_live(self, key: str) -> None:
        pad = getattr(self.controller, "touchpad", None)
        if pad is None:
            return
        v = self.settings.values
        if key == "touchpad_speed":
            pad.speed = v["touchpad_speed"]
        elif key == "scroll_speed":
            pad.scroll_mm_per_step = 1.0 / v["scroll_speed"]
        elif key == "natural_scroll":
            pad.natural_scroll = v["natural_scroll"]
        elif key == "tap_to_click":
            pad.tap_to_click = v["tap_to_click"]
        elif key == "three_finger_drag":
            pad.three_finger_drag = v["three_finger_drag"]

    @dbus.service.method(INTERFACE, sender_keyword="sender")
    def Restart(self, sender=None):
        """Restart the service (systemd brings it back) to apply settings."""
        self._check(sender)
        if self.on_restart:
            GLib.timeout_add(200, lambda: (self.on_restart(), False)[1])

    # -- signals -------------------------------------------------------------
    @dbus.service.signal(INTERFACE, signature="a{sv}")
    def StateChanged(self, state):
        pass

    @dbus.service.signal(INTERFACE, signature="ss")
    def PasskeyDisplay(self, device, code):
        pass

    @dbus.service.signal(INTERFACE, signature="a{sv}")
    def SettingsChanged(self, settings):
        pass
