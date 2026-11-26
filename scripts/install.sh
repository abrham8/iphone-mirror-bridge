#!/usr/bin/env bash
# Install iPhone Mirror Bridge as a boot service.
#
#   sudo scripts/install.sh               # touchpad (default options: --touchpad)
#   sudo scripts/install.sh --device /dev/input/event3   # any bridge options
#   sudo scripts/install.sh --uninstall
#
# Copies the bridge to /opt/iphone-mirror-bridge, writes the options to
# /etc/default/iphone-mirror-bridge and enables mouse-bridge.service.
# ROOT and SKIP_SYSTEMCTL are for testing only.
set -euo pipefail

ROOT="${ROOT:-}"
DEST="$ROOT/opt/iphone-mirror-bridge"
UNIT="$ROOT/etc/systemd/system/mouse-bridge.service"
CONFIG="$ROOT/etc/default/iphone-mirror-bridge"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

systemctl_() { [ -n "${SKIP_SYSTEMCTL:-}" ] || systemctl "$@"; }

if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo $0 $*" >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  systemctl_ disable --now mouse-bridge.service 2>/dev/null || true
  rm -f "$UNIT" "$CONFIG"
  rm -rf "$DEST"
  systemctl_ daemon-reload
  echo "Uninstalled. Bluetooth pairings and /etc/bluetooth are unchanged."
  exit 0
fi

for pkg in python3-dbus python3-gi python3-evdev bluez; do
  if [ -z "$ROOT" ] && ! dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "Missing package $pkg. Install with:" >&2
    echo "  sudo apt install bluez python3-dbus python3-gi python3-evdev gir1.2-glib-2.0" >&2
    exit 1
  fi
done

args="${*:---touchpad}"

install -d "$DEST/scripts" "$(dirname "$UNIT")" "$(dirname "$CONFIG")"
install -m 0644 "$SRC"/scripts/ble_hid.py "$SRC"/scripts/hid_report.py "$DEST/scripts/"
install -m 0755 "$SRC"/scripts/mouse_bridge.py "$DEST/scripts/"
install -m 0644 "$SRC/systemd/mouse-bridge.service" "$UNIT"
printf '# Options for mouse_bridge.py (see: python3 %s/scripts/mouse_bridge.py --help)\nBRIDGE_ARGS="%s"\n' \
  "/opt/iphone-mirror-bridge" "$args" > "$CONFIG"

# A manually started copy would block the service (single-instance lock).
if [ -z "$ROOT" ] && pgrep -f "python3 scripts/mouse_bridge.py" >/dev/null; then
  echo "Note: a manually started bridge is running; stop it (Ctrl+C) so the service can start."
fi

systemctl_ daemon-reload
systemctl_ enable mouse-bridge.service
systemctl_ restart mouse-bridge.service

echo "Installed. Options: $args"
echo "  Status:  systemctl status mouse-bridge"
echo "  Logs:    journalctl -u mouse-bridge -f"
echo "  Change options: edit $CONFIG, then: sudo systemctl restart mouse-bridge"
echo "  Update after git pull: sudo scripts/install.sh $args"
