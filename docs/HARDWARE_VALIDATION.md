# Hardware validation checklist

Use this checklist when testing on new hardware. First results (Debian 13.8, BlueZ 5.82, Intel adapter, ASUS touchpad, iPhone) are recorded in the README. When running from source, stop the installed service first: `sudo systemctl stop iphone-mirror-bridge`.

## Host details to record

`sudo scripts/check_host.sh` collects all of the following (read-only):

- Debian release: `cat /etc/debian_version`
- Kernel: `uname -a`
- BlueZ: `bluetoothd -v`
- Adapter: `bluetoothctl show`
- Python: `python3 --version`
- evdev: `python3 -c 'import evdev; print(evdev.__version__)'`
- Desktop/session: X11 or Wayland, desktop environment, and whether a physical keyboard is attached.

## Test A: pure report encoding

From the repository root:

```bash
/usr/bin/python3 -m unittest discover -s tests -v
/usr/bin/python3 -m py_compile scripts/*.py
```

Expected: all tests pass with **none skipped**, and all Python files compile.

## Test B: input device detection

```bash
sudo python3 -c 'from evdev import list_devices, InputDevice; [print(p, InputDevice(p).name) for p in list_devices()]'
sudo python3 scripts/mouse_bridge.py --device /dev/input/eventN --hotkey-device /dev/input/eventM --debug
```

Expected: the selected mouse and keyboard are detected. The program should report whether a BLE GATT service and advertisement register. At this stage, do not use `--start-active` until pairing and pointer behavior have been tested.

## Test C: BLE discovery and pairing

1. In a second terminal, run `sudo journalctl -fu bluetooth`.
2. Start the bridge with `--debug`.
3. Confirm the log shows, in order: `Pairing agent registered`, `BlueZ GATT HID service registered`, `BLE HID advertisement active`.
4. On the iPhone, enable AssistiveTouch and open Bluetooth Devices.
5. Pair with the advertised bridge name.
6. Expected bridge log: `Accepting pairing/authorization`, `Marked ... as trusted`, then `Host subscribed to notifications on 2a4d`. The last line means iOS is ready to receive mouse reports.
7. Record whether pairing succeeds, whether the device bonds (`bluetoothctl info <iPhone address>` shows `Paired: yes`, `Bonded: yes`), and any BlueZ or iOS error messages.

If the advertisement is not visible, record adapter model/firmware and BlueZ version before changing BlueZ configuration.

## Test D: pointer behavior and mode switching

1. Start in laptop mode (default) and confirm the local desktop mouse still works.
2. Press Ctrl+F8. Confirm the local mouse is grabbed and the iPhone pointer responds.
3. Test X/Y motion (slow and fast flicks), left/right/middle click, quick double-click, drag, and vertical wheel.
4. Hold the left button, press Ctrl+F8, then release. Confirm the iPhone does not stay in a pressed/dragging state and the local desktop mouse works.
5. Stop with Ctrl+C. Confirm the local mouse is released.
6. Reboot and test reconnection only after the manual test is stable.

Record failures separately: discovery, pairing/bonding, HID service enumeration, notification subscription, pointer visibility, pointer motion, buttons, wheel, mode switching, reconnect, and shutdown.

## Test E: AirPlay

Install UxPlay and start the user service. Confirm the receiver appears in the iPhone's Screen Mirroring list and that video/audio render correctly. Record the Linux display session, GStreamer sink, and firewall state.

## Required evidence before release

- Debian version, kernel, BlueZ version, and adapter model.
- Successful unit-test and compile output.
- BlueZ logs showing service and advertisement registration.
- Confirmation that iOS paired/bonded and subscribed to the HID input report.
- Results for movement, buttons, wheel, mode switching, reconnect, and shutdown.
- Separate confirmation that UxPlay AirPlay mirroring works.
