# Optional remote access

Remote access is separate from AirPlay and the BLE bridge.

1. Configure a private VPN such as Tailscale or WireGuard between the remote device and Linux host.
2. Enable a remote desktop server on Linux (for example, VNC or RDP).
3. Restrict remote desktop access to the VPN interface and use strong authentication.
4. Keep the Linux host awake, the iPhone powered on, and the phone within Bluetooth range.

Remote desktop access controls the Linux host. It does not remove the need for first-time physical iPhone setup and Bluetooth pairing. Do not expose VNC/RDP directly to the public internet.
