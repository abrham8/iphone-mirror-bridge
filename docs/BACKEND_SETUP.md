# Debian backend setup

The bridge uses the system BlueZ daemon over system D-Bus, Python evdev for Linux input events, and GLib for the D-Bus event loop.

## Install packages

```bash
sudo apt update
sudo apt install bluez bluetooth python3-dbus python3-gi python3-evdev   python3-pip python3-venv gir1.2-glib-2.0
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

## BlueZ GATT support

The app registers a GATT application and LE peripheral advertisement through BlueZ D-Bus. The adapter must support LE peripheral/advertising operation. Some USB adapters or firmware only support central mode or have unreliable advertising.

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

Choose devices explicitly:

```bash
sudo python3 scripts/mouse_bridge.py --device /dev/input/eventN --hotkey-device /dev/input/eventM
```

Use `--start-active` to start in phone mode. Keep a keyboard available: phone mode grabs the mouse from the local desktop.

## Known risks and troubleshooting

- The D-Bus GATT object model and advertisement properties need validation against the exact BlueZ release.
- iOS may require pairing, bonding, encryption, or HID descriptor details not yet implemented.
- The report descriptor is a basic three-button relative mouse with vertical wheel only.
- If multiple mice are present, automatic discovery chooses the first matching device. Specify `--device` to avoid ambiguity.
- Do not enable the systemd service until manual startup, pairing, reconnect, and safe shutdown have been tested.
- If the iPhone cannot discover the device, verify adapter peripheral support, BlueZ advertisement registration, and visibility with a BLE scanner.
- If pairing works but no pointer appears, inspect HID service discovery, Report Map, Report Reference descriptor, notification subscription, and AssistiveTouch Pointer Devices settings.

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
