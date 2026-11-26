# Debian backend setup

> End users: install the .deb as described in the [README](../README.md). This page is for running from source and troubleshooting.

The bridge uses the system BlueZ daemon over system D-Bus, Python evdev for Linux input events, and GLib for the D-Bus event loop.

## Install packages

```bash
sudo apt update
sudo apt install bluez bluetooth python3-dbus python3-gi python3-evdev gir1.2-glib-2.0
```

Prefer Debian's Python packages for dbus-python, PyGObject, and evdev because they integrate with system libraries. Check the installed versions and service:

```bash
bluetoothd -v
systemctl status bluetooth
bluetoothctl show
```

Power on the adapter if necessary with `bluetoothctl` and the `power on` command.

## Python environment

The simplest initial run uses Debian's system Python:

```bash
sudo /usr/bin/python3 scripts/mouse_bridge.py --debug
```

If you use a virtual environment, expose Debian's system packages:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
sudo .venv/bin/python scripts/mouse_bridge.py --debug
```

## Input permissions

Reading `/dev/input/event*` usually requires root or membership in the `input` group. Check device permissions with `ls -l /dev/input/event*`. Access to input devices can expose keystrokes, so grant it only to a trusted account. Running with sudo is the simplest first hardware test.

## Pairing

The bridge registers its own BlueZ pairing agent (`NoInputNoOutput`, "Just Works") while it runs. It auto-accepts pairing requests and marks the paired device as trusted so it can reconnect. Without an agent, BlueZ rejects the iPhone's pairing request. Only run the bridge when you are ready to pair: any nearby device could pair with it as a mouse while it is advertising.

If you prefer to use your desktop's Bluetooth agent instead, pass `--no-agent`.

If pairing fails, or the iPhone connects but the pointer does not move (seen after restarting bluetooth or changing `main.conf`), remove the device on both sides and pair again:

- iPhone: Settings → Bluetooth → (i) next to the bridge → Forget This Device.
- Linux: `bluetoothctl devices`, then `bluetoothctl remove <iPhone address>`.

The advertised name may be shortened by BlueZ to fit the 31-byte advertisement. After connecting, iOS may show the adapter's Bluetooth name (`bluetoothctl show`, "Alias") instead of "iPhone Mirror Bridge".

## BlueZ GATT support

The app registers a GATT application (HID, Battery and Device Information services) and an LE peripheral advertisement through BlueZ D-Bus. The adapter must support LE peripheral/advertising operation. Some USB adapters or firmware only support central mode or have unreliable advertising.

Do not blindly edit `/etc/bluetooth/main.conf`, disable plugins, or enable experimental mode. The correct settings depend on the installed BlueZ version and adapter. This first implementation has not yet been tested on real Debian hardware, so BlueZ version-specific requirements remain open.

Capture daemon logs with:

```bash
sudo journalctl -u bluetooth -b --no-pager
```

Do not start a second bluetoothd while the system service is running. If you need foreground debug mode, stop the service first and restore it afterward.

## Run and select devices

List available input devices:

```bash
sudo python3 -c 'from evdev import list_devices, InputDevice; [print(p, InputDevice(p).name) for p in list_devices()]'
```

Start in laptop-control mode (default). Ctrl+F8 switches to phone mode:

```bash
sudo python3 scripts/mouse_bridge.py --debug
```

Use `--adapter hci1` to pick a specific Bluetooth adapter.

Choose devices explicitly:

```bash
sudo python3 scripts/mouse_bridge.py --device /dev/input/eventN --hotkey-device /dev/input/eventM
```

### Laptop touchpad

```bash
sudo python3 scripts/mouse_bridge.py --touchpad --debug
```

One finger moves the pointer, tapping clicks (two-finger tap = right click), pressing the pad clicks (two fingers = right click), and two fingers scroll. Three fingers press and hold: keep them still for a long-press, move them to drag, lift to drop (`--no-three-finger-drag` disables this). Options: `--touchpad-speed 20` (higher = faster), `--scroll-speed 1` (higher = faster scrolling), `--reverse-scroll`, `--no-tap`. iOS also has its own speed setting under AssistiveTouch → Tracking Speed. In phone mode the touchpad is grabbed, so the desktop does not move at the same time.

Use `--start-active` to switch to phone mode automatically once the iPhone connects. Phone mode is only entered while the iPhone is connected, and the device returns to the laptop if it disconnects. Keep a keyboard available: phone mode grabs the mouse from the local desktop.

## Pointer latency

The iPhone decides how often it exchanges data with the bridge (the LE connection interval). As a peripheral, Linux asks it for 30-50 ms by default, which makes the pointer lag. While running, the bridge asks for 15-30 ms by writing `/sys/kernel/debug/bluetooth/hciN/conn_min_interval` and `conn_max_interval` (root only; restored on exit). The request is made when the iPhone connects, so start the bridge before the iPhone reconnects.

If the log says it could not set the interval (common with Secure Boot, which blocks debugfs), run `sudo scripts/set_le_interval.sh` once. It backs up and edits `/etc/bluetooth/main.conf` and restarts bluetooth. The change it makes is:

```ini
[LE]
MinConnectionInterval=12
MaxConnectionInterval=24
```

Values are in 1.25 ms units. `--report-interval 15` controls how often the bridge sends combined movement (lower = smoother but can queue up on a slow link). To check the actual interval, run `sudo btmon` while the iPhone connects and look for "Connection interval" in "LE Connection Update Complete".

## Known risks and troubleshooting

- The D-Bus GATT object model is tested against a fake BlueZ in CI, but still needs validation against the real BlueZ release.
- If BlueZ reports a duplicate Device Information service (only when `DeviceID` is set in `/etc/bluetooth/main.conf`), use `--no-device-info`.
- iOS may require pairing, bonding, encryption, or HID descriptor details not yet implemented.
- The report descriptor is a basic three-button relative mouse with vertical wheel only.
- If multiple mice are present, automatic discovery chooses the first matching device. Specify `--device` to avoid ambiguity.
- If the iPhone cannot discover the device, verify adapter peripheral support, BlueZ advertisement registration, and visibility with a BLE scanner.
- If pairing works but no pointer appears, inspect HID service discovery, Report Map, Report Reference descriptor, notification subscription, and AssistiveTouch Pointer Devices settings.

## Automated tests

```bash
/usr/bin/python3 -m unittest discover -s tests -v
```

The tests cover HID report encoding, mode switching, the D-Bus GATT objects (using a private `dbus-daemon` and a fake BlueZ), and an end-to-end run of the bridge with a simulated mouse. They need `python3-dbus`, `python3-gi`, `python3-evdev` and `dbus`; tests are skipped if those are missing. They do **not** replace testing with a real adapter and iPhone.

## UxPlay AirPlay receiver

Install UxPlay separately from the BLE bridge. On Debian releases that package it:

```bash
sudo apt install uxplay
uxplay -h
```

The repository includes a **user** service unit at `systemd/uxplay.service`. Install it for the desktop account (not as root):

```bash
mkdir -p ~/.config/systemd/user
cp systemd/uxplay.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now uxplay.service
systemctl --user status uxplay.service
```

The unit assumes UxPlay is installed at `/usr/bin/uxplay`. If `command -v uxplay` reports another path, edit `ExecStart` accordingly. UxPlay needs an active graphical session to display video unless you configure a suitable headless video sink. Ensure mDNS/DNS-SD traffic (UDP 5353) and UxPlay's negotiated media ports are allowed on the local network. See the [upstream UxPlay documentation](https://github.com/FDH2/UxPlay) for GStreamer sink, firewall, and display configuration.
