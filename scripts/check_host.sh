#!/usr/bin/env bash
# Collect the host details needed for docs/HARDWARE_VALIDATION.md.
# Read-only: it changes nothing. Run from the repository root.
set -u
section() { printf '\n== %s ==\n' "$1"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1 || printf '(failed: exit %s)\n' "$?"; }

section "System"
run cat /etc/debian_version
run uname -a
printf 'Session: %s\n' "${XDG_SESSION_TYPE:-unknown} ${XDG_CURRENT_DESKTOP:-}"

section "Bluetooth"
run bluetoothd -v
run systemctl is-active bluetooth
run bluetoothctl show
command -v btmgmt >/dev/null && run btmgmt info

section "Python packages"
run /usr/bin/python3 --version
for mod in dbus gi evdev; do
  /usr/bin/python3 -c "import $mod, sys; print('$mod', getattr($mod, '__version__', 'ok'))" 2>&1 \
    || echo "$mod MISSING (sudo apt install python3-dbus python3-gi python3-evdev)"
done

section "Input devices (needs root to read names)"
if [ "$(id -u)" -eq 0 ]; then
  /usr/bin/python3 - <<'PY'
from evdev import InputDevice, ecodes, list_devices
for p in sorted(list_devices()):
    d = InputDevice(p)
    caps = d.capabilities(absinfo=False)
    rel, keys = set(caps.get(ecodes.EV_REL, [])), set(caps.get(ecodes.EV_KEY, []))
    kind = []
    if ecodes.REL_X in rel and ecodes.BTN_LEFT in keys: kind.append("mouse")
    if ecodes.KEY_F8 in keys and ecodes.KEY_LEFTCTRL in keys: kind.append("keyboard")
    print(p, repr(d.name), ",".join(kind))
PY
else
  echo "Re-run with sudo to list input devices."
fi

section "Unit tests"
run /usr/bin/python3 -m unittest discover -s tests
