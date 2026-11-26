# iPhone Mirror Bridge

See your iPhone's screen on your Linux computer and control it with the touchpad (or a mouse).

- **Control:** the computer acts as a Bluetooth mouse for the iPhone. With AssistiveTouch on, you get a pointer on the iPhone that you move, click, scroll and drag from the touchpad. Press **Ctrl+F8** to switch the touchpad between the computer and the iPhone.
- **Screen:** the **iPhone Screen Mirroring** app shows the iPhone screen in a window using AirPlay.

> Status: beta (0.1.0). Tested on Debian 13 with an ASUS laptop touchpad and an iPhone. Other hardware should work but has not been tested yet; reports are welcome.

## Install

**Requirements:** Debian 13 or Ubuntu 24.04 (or newer) with Bluetooth, and an iPhone.

1. Download `iphone-mirror-bridge_<version>_all.deb` from the [Releases page](https://github.com/abrham8/iphone-mirror-bridge/releases).
2. Install it (this also installs everything it needs):
   ```bash
   sudo apt install ./iphone-mirror-bridge_*_all.deb
   ```

The bridge starts right away and again at every boot. Nothing else to configure.

## Set up the iPhone (once)

1. **Settings → Accessibility → Touch → AssistiveTouch**: turn it on.
2. **Settings → Bluetooth**: tap **iPhone Mirror Bridge** and accept the pairing request. (After pairing it may be listed under your computer's name.)
3. Optional: **Settings → Accessibility → Pointer Control → Automatically Hide Pointer**: turn off.

The iPhone reconnects by itself from now on.

## Use it

| Action | Touchpad |
|---|---|
| Switch between computer and iPhone | **Ctrl+F8** |
| Move the pointer | one finger |
| Click | tap, or press the pad |
| Right-click | two-finger tap, or press with two fingers |
| Scroll | slide two fingers |
| Long-press / drag | rest three fingers, move to drag, lift to drop |

While the iPhone is in control, the touchpad does not move the computer's cursor. If the iPhone disconnects, the touchpad returns to the computer automatically.

## See the iPhone screen

1. Open **iPhone Screen Mirroring** from your application menu. A notification says it is ready.
2. On the iPhone: open **Control Center → Screen Mirroring** and choose **iPhone Mirror Bridge**.

The iPhone and computer must be on the same Wi-Fi network. To stop, open the app again (or right-click it → Stop). From a terminal: `iphone-mirror-screen --start`, `--stop`, `--status`.

## Settings

Edit `/etc/iphone-mirror-bridge/bridge.conf`, then run `sudo systemctl restart iphone-mirror-bridge`. Examples:

```bash
BRIDGE_ARGS="--touchpad-speed 30"   # faster pointer (default 20)
BRIDGE_ARGS="--mouse"               # use a mouse instead of the touchpad
BRIDGE_ARGS="--hotkey F12"          # switch with Ctrl+F12
```

All options: `iphone-mirror-bridge --help` or `man iphone-mirror-bridge`. The iPhone also has a speed slider under **AssistiveTouch → Tracking Speed**.

## Troubleshooting

- **The iPhone does not see "iPhone Mirror Bridge":** check `systemctl status iphone-mirror-bridge` and that Bluetooth is on (`bluetoothctl show`).
- **Connected, but the pointer does not move:** forget the device on the iPhone (Settings → Bluetooth → ⓘ → Forget This Device), run `bluetoothctl devices` and `bluetoothctl remove <iPhone address>` on the computer, then pair again.
- **Ctrl+F8 does nothing:** on some laptops F8 is a media key; try Fn+Ctrl+F8, or set `--hotkey F12`.
- **Screen mirroring does not appear on the iPhone:** both devices must be on the same network, and a firewall must allow mDNS (UDP 5353), TCP 7000, 7001, 7100 and UDP 6000, 6001, 7011. Log: `~/.cache/iphone-mirror-bridge/uxplay.log`.
- **Logs:** `journalctl -u iphone-mirror-bridge -f`

## Uninstall

```bash
sudo apt remove iphone-mirror-bridge
```


## Limitations

- No Face ID: a pointer can enter the passcode but cannot unlock with Face ID.
- First-time AssistiveTouch setup and pairing need the iPhone in your hand.
- This is not Apple's iPhone Mirroring feature and does not use any private Apple protocol.

## Development

```bash
git clone https://github.com/abrham8/iphone-mirror-bridge.git && cd iphone-mirror-bridge
sudo python3 scripts/mouse_bridge.py --debug    # run from source (stop the service first)
python3 -m unittest discover -s tests -v        # tests (need python3-dbus, python3-gi, python3-evdev, dbus)
scripts/build_deb.sh                            # build dist/*.deb
sudo scripts/install.sh                         # build and install the .deb
```

- `scripts/mouse_bridge.py`: entry point; input devices, touchpad gestures, Ctrl+F8 switching.
- `scripts/ble_hid.py`: BlueZ GATT HID service, advertisement and pairing agent.
- `scripts/hid_report.py`: HID report encoding, buffering and touchpad gesture logic (no dependencies).
- `packaging/debian/`: package metadata, systemd unit, default settings, man page.
- `tests/`: unit, D-Bus (fake BlueZ) and end-to-end tests. CI also installs the package on a clean Debian 13.
- `docs/`: [backend setup](docs/BACKEND_SETUP.md), [hardware validation](docs/HARDWARE_VALIDATION.md), [iPhone setup](docs/IPHONE_SETUP.md), [remote access](docs/REMOTE_ACCESS.md).

Hardware results so far (Debian 13.8 with Kicksecure hardening, kernel 7.3, BlueZ 5.82, Intel Bluetooth, ASUS touchpad): .deb install, boot service, reconnect after reboot, pairing, movement, clicks, right-click, scrolling, three-finger drag and mode switching verified. Not yet verified: external mouse mode, other adapters and distributions.

Input stays on your computer and Bluetooth link; there is no cloud service.
