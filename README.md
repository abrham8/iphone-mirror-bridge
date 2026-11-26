# iPhone Mirror Bridge

See your iPhone's screen on your Linux computer and control it with the touchpad and keyboard.

- **Screen:** the iPhone's screen appears in a window with a device frame (AirPlay).
- **Control:** click the screen and the touchpad moves a pointer on the iPhone; the keyboard types on it. The computer acts as a Bluetooth mouse and keyboard for the iPhone. Press **Ctrl+F8** to take the touchpad back.

> Status: beta (0.2.3). Tested on Debian 13 with an ASUS laptop touchpad and an iPhone. Other hardware should work but has not been tested yet; reports are welcome.

## Install

**Requirements:** Debian 13 (or newer) with Bluetooth, and an iPhone.

1. Download `iphone-mirror-bridge_<version>_all.deb` from the [Releases page](https://github.com/abrham8/iphone-mirror-bridge/releases).
2. Install it (this also installs everything it needs):
   ```bash
   sudo apt install ./iphone-mirror-bridge_*_all.deb
   ```

The bridge service starts right away and at every boot, and the app's tray icon starts when you log in.

## Connect your iPhone (once)

1. Open **iPhone Mirror Bridge** from the application menu and choose **Connect iPhone** (tray icon menu, or Settings → Devices).
2. On the iPhone: **Settings → Accessibility → Touch → AssistiveTouch**: turn it on.
3. On the iPhone: **Settings → Bluetooth** → tap **iPhone Mirror Bridge**, then type the **6-digit code** the Connect window shows.

The iPhone reconnects by itself from now on. Optional: **Settings → Accessibility → Pointer Control → Automatically Hide Pointer**: off.

## See and control the iPhone

1. Open **iPhone Mirror Bridge**. It shows a phone-shaped window waiting for the iPhone.
2. On the iPhone: **Control Center → Screen Mirroring → iPhone Mirror Bridge**. The screen appears in the window.
3. **Click the screen** to control the iPhone with the touchpad and keyboard. **Ctrl+F8** gives them back to the computer.

Toolbar: **Home**, **App Switcher**, keep on top, settings, and stop (×). Drag the toolbar to move the window.

| Action | Touchpad |
|---|---|
| Take / give back control | click the screen / **Ctrl+F8** |
| Move the pointer | one finger |
| Click | tap, or press the pad |
| Right-click | two-finger tap, or press with two fingers |
| Scroll | slide two fingers |
| Long-press / drag | rest three fingers, move to drag, lift to drop |

The iPhone and computer must be on the same Wi-Fi network for the screen. If the iPhone disconnects, the touchpad returns to the computer automatically.

## Settings

Everything is in the app's **Settings** window: window size, frame rate, keep on top, device frame, pointer and scroll speed, tap to click, three-finger drag, switch shortcut, typing on the iPhone, pairing code, paired devices and the firewall. Command-line users can still pass options in `/etc/iphone-mirror-bridge/bridge.conf` (they override the app's settings); see `man iphone-mirror-bridge`.

## Troubleshooting

- **The iPhone does not see "iPhone Mirror Bridge":** check `systemctl status iphone-mirror-bridge` and that Bluetooth is on (`bluetoothctl show`).
- **Connected, but the pointer does not move:** in the app, Settings → Devices → **Forget**; on the iPhone, Settings → Bluetooth → ⓘ → **Forget This Device**; then connect again. Also needed after turning "Type on the iPhone" on or off.
- **Ctrl+F8 does nothing:** on some laptops F8 is a media key; try Fn+Ctrl+F8, or pick another shortcut in Settings → Controls.
- **Screen mirroring does not appear on the iPhone:** both devices must be on the same network, and a firewall must allow mDNS (UDP 5353), TCP 7000, 7001, 7100 and UDP 6000, 6001, 7011. Log: `~/.cache/iphone-mirror-bridge/uxplay.log`.
- **The receiver is listed on the iPhone but it says "Unable to connect":** a firewall lets the iPhone find the receiver (mDNS) but blocks the connection. On Wild OS, the app shows this in the mirror window and offers **Allow** (also Settings → Privacy, or `iphone-mirror-screen --allow-firewall`). This marks the current network as Home and allows the ports above for devices on that network only. With other firewalls, allow the ports above yourself.
- **Mirroring lags or stutters:** the app uses up to 60 fps (Settings → Display), GPU decoding without frame buffering, and keeps UxPlay's timestamp sync so video plays smoothly. `iphone-mirror-screen --start` (without the app) shows video in UxPlay's own window with `waylandsink`, which uses a little less CPU. Most remaining lag comes from Wi-Fi power saving on the computer; turn it off for your network with `nmcli connection modify "<Wi-Fi name>" 802-11-wireless.powersave 2` and reconnect. A 5 GHz network helps too. To use other UxPlay video options, set `IPHONE_MIRROR_VIDEO_ARGS`; for example `IPHONE_MIRROR_VIDEO_ARGS="-vsync no -fps 60" iphone-mirror-screen --start` gives a little less delay but choppier video.
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
python3 -m unittest discover -s tests -v        # tests (need python3-dbus, python3-gi, python3-evdev, dbus;
                                                #  UI tests also PySide6 and GStreamer)
python3 -m iphone_mirror_bridge_ui              # run the app from ui/ (cd ui first)
QT_QPA_PLATFORM=offscreen python3 ui/tools/render_screens.py /tmp/shots   # render the windows
scripts/build_deb.sh                            # build dist/*.deb
sudo scripts/install.sh                         # build and install the .deb
```

- `scripts/mouse_bridge.py`: entry point; input devices, touchpad gestures, Ctrl+F8 switching.
- `scripts/ble_hid.py`: BlueZ GATT HID service, advertisement and pairing agent.
- `scripts/hid_report.py`: HID report encoding, buffering, keyboard state and touchpad gestures (no dependencies).
- `scripts/control.py`: the service's D-Bus API for the app (state, keys, devices, settings).
- `ui/iphone_mirror_bridge_ui/`: the desktop app (PySide6 + QML, built from the Figma design "Screen Mirror App UI").
- `packaging/debian/`: package metadata, systemd unit, D-Bus policy, desktop/autostart entries, man pages.
- `tests/`: unit, D-Bus (fake BlueZ) and end-to-end tests. CI also installs the package on a clean Debian 13.
- `docs/`: [backend setup](docs/BACKEND_SETUP.md), [hardware validation](docs/HARDWARE_VALIDATION.md), [iPhone setup](docs/IPHONE_SETUP.md), [remote access](docs/REMOTE_ACCESS.md).

Hardware results so far (Debian 13.8 with Kicksecure hardening, kernel 7.3, BlueZ 5.82, Intel Bluetooth, ASUS touchpad): .deb install, boot service, reconnect after reboot, pairing, movement, clicks, right-click, scrolling, three-finger drag and mode switching verified. Not yet verified: external mouse mode, other adapters and distributions.

Video and input stay on your computer, local network and Bluetooth link; there is no cloud service.

## License

MIT. See [LICENSE](LICENSE).
