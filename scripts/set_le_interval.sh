#!/usr/bin/env bash
# Make BlueZ ask for a 15-30 ms LE connection interval (lower pointer lag).
# Edits /etc/bluetooth/main.conf (backup kept as main.conf.before-iphone-bridge)
# and restarts bluetooth. Run with sudo. Undo: restore the backup and restart.
set -euo pipefail
conf=/etc/bluetooth/main.conf
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
[ -f "$conf" ] || { echo "$conf not found"; exit 1; }
[ -f "$conf.before-iphone-bridge" ] || cp "$conf" "$conf.before-iphone-bridge"
# Remove earlier active values, then add ours under the [LE] section.
sed -i -e '/^[[:space:]]*MinConnectionInterval[[:space:]]*=/d' \
       -e '/^[[:space:]]*MaxConnectionInterval[[:space:]]*=/d' "$conf"
if grep -q '^\[LE\]' "$conf"; then
  sed -i '/^\[LE\]/a MinConnectionInterval=12\nMaxConnectionInterval=24' "$conf"
else
  printf '\n[LE]\nMinConnectionInterval=12\nMaxConnectionInterval=24\n' >> "$conf"
fi
echo "Updated $conf:"
sed -n '/^\[LE\]/,/^\[/p' "$conf" | grep -v '^#' | grep -v '^$'
systemctl restart bluetooth
echo "Bluetooth restarted. Start the bridge, then reconnect the iPhone."
