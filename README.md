# iPhone Mirror Bridge

A Debian-first experiment for viewing and controlling a nearby iPhone from Linux. UxPlay receives AirPlay mirroring; the companion Python app advertises a Bluetooth Low Energy HID mouse through BlueZ so iOS AssistiveTouch can use the Linux mouse as a pointer. This does **not** implement Apple's private iPhone Mirroring protocol.

## Status

Early hardware-integration project. The mouse bridge is implemented and covered by automated tests (including an end-to-end run against a fake BlueZ), but has **not** been validated against a physical BLE adapter or iPhone. Treat it as experimental, not as a working release.

| Component | Status |
|---|---|
| evdev mouse discovery and event parsing | Implemented; hardware testing needed |
| Laptop touchpad (move, tap, click, two-finger scroll) | Implemented; hardware testing needed |
| HID mouse report descriptor and encoding | Implemented; descriptor validation needed |
| BlueZ GATT HID service and advertisement | Implemented; tested against fake BlueZ; real BlueZ/iOS testing needed |
| Pairing agent (Just Works, auto-trust) | Implemented; iOS testing needed |
| Ctrl+F8 mode toggle and evdev grab | Implemented; desktop testing needed |
| UxPlay user service | Implemented; display/network testing needed |
| AppImage | Not implemented |

## Debian quick start

See [Backend setup](docs/BACKEND_SETUP.md). Install Debian's BlueZ, D-Bus, GI, and evdev packages, then run with input-device permissions:

```bash
sudo python3 scripts/mouse_bridge.py --debug
```

To help with hardware testing, run `sudo scripts/check_host.sh` and follow [Hardware validation](docs/HARDWARE_VALIDATION.md).

Enable **Settings → Accessibility → Touch → AssistiveTouch** on the iPhone, then pair with **iPhone Mirror Bridge**. Ctrl+F8 switches between laptop and phone control. The default mode is laptop control; use `--start-active` to start in phone mode. Use `--device /dev/input/eventN` and `--hotkey-device /dev/input/eventM` to select devices explicitly. To use a laptop touchpad, pass `--touchpad` (or `--device` with the touchpad's event node).

> Warning: phone mode grabs the mouse from the local desktop. Keep a keyboard available. Ctrl+C or SIGTERM releases the mouse.

## Screen mirroring

Install and run UxPlay separately using the user service in `systemd/uxplay.service`; see [Backend setup](docs/BACKEND_SETUP.md). The iPhone and Linux receiver must share a network that allows AirPlay discovery and traffic. UxPlay, BlueZ, and one-time pairing are system-level components and are not intended to be bundled in the AppImage.

## Limitations

- No Face ID support. A pointer may allow passcode entry, but cannot authenticate biometrically.
- First-time AssistiveTouch setup and pairing require physical access to the iPhone.
- The iPhone must be powered on.
- BLE advertising, pairing, reconnect behavior, HID descriptor compatibility, and iOS pointer behavior remain unverified on real hardware.

## Project layout

- `scripts/mouse_bridge.py`: entry point; evdev input, Ctrl+F8 mode switching.
- `scripts/ble_hid.py`: BlueZ GATT HID service, advertisement and pairing agent.
- `scripts/hid_report.py`: HID report encoding and buffering (no dependencies).
- `scripts/check_host.sh`: collects host details for hardware testing.
- `tests/`: unit, D-Bus and end-to-end tests (`/usr/bin/python3 -m unittest discover -s tests -v`).
- `systemd/`: draft service units.
- `docs/`: Debian, iPhone, and optional remote-access setup.

Input events are processed locally. No cloud service or network relay is part of the bridge. Secure AirPlay and remote-desktop networks independently.
