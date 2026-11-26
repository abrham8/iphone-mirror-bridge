"""Per-user UI preferences (~/.config/iphone-mirror-bridge/ui.json) and autostart."""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

DEFAULTS = {
    "windowSize": "actual",        # small | actual | large
    "fps": 60,                     # 30 | 60
    "onTop": True,
    "deviceFrame": True,
    "hideInactive": False,
    "displayServer": "auto",       # auto | wayland | x11
    "autoStartMirroring": True,
    "showOnMirror": True,
    "notifyConnection": True,
    "notifyMirroring": True,
    "hintShown": False,
}
CHOICES = {"windowSize": {"small", "actual", "large"}, "fps": {30, 60},
           "displayServer": {"auto", "wayland", "x11"}}
AUTOSTART_NAME = "iphone-mirror-bridge-ui.desktop"


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def load_raw(path: Path) -> dict:
    values = dict(DEFAULTS)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return values
    for key, value in stored.items():
        if key in DEFAULTS and type(value) is type(DEFAULTS[key]) and value in CHOICES.get(key, [value]):
            values[key] = value
    return values


class Prefs(QObject):
    changed = Signal()

    def __init__(self, path: Path | None = None, parent=None):
        super().__init__(parent)
        self.path = path or config_dir() / "iphone-mirror-bridge" / "ui.json"
        self._values = load_raw(self.path)

    def _get(self, key):
        return self._values[key]

    @Slot(str, result="QVariant")
    def get(self, key):
        return self._values.get(key)

    @Slot(str, "QVariant")
    def set(self, key, value):
        if key not in DEFAULTS:
            return
        if isinstance(DEFAULTS[key], bool):
            value = bool(value)
        elif isinstance(DEFAULTS[key], int):
            value = int(value)
        else:
            value = str(value)
        if value not in CHOICES.get(key, [value]) or self._values[key] == value:
            return
        self._values[key] = value
        self.save()
        self.changed.emit()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._values, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    # QML-friendly read-only properties (write through set()).
    windowSize = Property(str, lambda s: s._get("windowSize"), notify=changed)
    fps = Property(int, lambda s: s._get("fps"), notify=changed)
    onTop = Property(bool, lambda s: s._get("onTop"), notify=changed)
    deviceFrame = Property(bool, lambda s: s._get("deviceFrame"), notify=changed)
    hideInactive = Property(bool, lambda s: s._get("hideInactive"), notify=changed)
    displayServer = Property(str, lambda s: s._get("displayServer"), notify=changed)
    autoStartMirroring = Property(bool, lambda s: s._get("autoStartMirroring"), notify=changed)
    showOnMirror = Property(bool, lambda s: s._get("showOnMirror"), notify=changed)
    notifyConnection = Property(bool, lambda s: s._get("notifyConnection"), notify=changed)
    notifyMirroring = Property(bool, lambda s: s._get("notifyMirroring"), notify=changed)
    hintShown = Property(bool, lambda s: s._get("hintShown"), notify=changed)

    # -- start at login (XDG autostart; the package installs the system entry) --
    autostartChanged = Signal()

    def _user_autostart(self) -> Path:
        return config_dir() / "autostart" / AUTOSTART_NAME

    def _get_start_at_login(self) -> bool:
        user = self._user_autostart()
        if user.exists():
            text = user.read_text(encoding="utf-8", errors="replace")
            return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text
        return Path("/etc/xdg/autostart", AUTOSTART_NAME).exists()

    @Slot(bool)
    def setStartAtLogin(self, enabled: bool) -> None:
        user = self._user_autostart()
        user.parent.mkdir(parents=True, exist_ok=True)
        user.write_text(
            "[Desktop Entry]\nType=Application\nName=iPhone Mirror Bridge\n"
            "Exec=iphone-mirror-bridge-ui --tray\nIcon=iphone-mirror-bridge\n"
            f"Hidden={'false' if enabled else 'true'}\n", encoding="utf-8")
        self.autostartChanged.emit()

    startAtLogin = Property(bool, _get_start_at_login, notify=autostartChanged)
