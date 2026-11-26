# iPhone setup

1. Open **Settings → Accessibility → Touch → AssistiveTouch**.
2. Turn on AssistiveTouch.
3. Open **Devices → Bluetooth Devices** (wording can vary by iOS version).
4. Start `mouse_bridge.py` on Linux and wait for **iPhone Mirror Bridge** to appear.
5. Select it and approve pairing on the iPhone if prompted.
6. Check AssistiveTouch pointer/device settings, then switch the Linux bridge to phone mode with Ctrl+F8.

The exact menu wording can vary by iOS version. Pairing and pointer behavior have not yet been tested end-to-end with this repository.

## Important limitations

- Initial AssistiveTouch setup and Bluetooth pairing require physical access to the iPhone.
- A Bluetooth pointer cannot perform Face ID. It may be possible to enter a passcode through the on-screen keypad, but this is not guaranteed.
- The iPhone must be powered on.
- If the pointer does not appear, remove the pairing on both sides, restart the bridge, and pair again after checking Linux BlueZ logs.
