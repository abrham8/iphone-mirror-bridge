#!/usr/bin/env bash
# Install from a source checkout: builds the .deb and installs it with apt,
# exactly like the release download. Run: sudo scripts/install.sh
# Remove later with: sudo apt remove iphone-mirror-bridge
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0" >&2; exit 1; }
if pgrep -f "python3 scripts/mouse_bridge.py" >/dev/null; then
  echo "A bridge started by hand is running. Stop it (Ctrl+C in its terminal) first." >&2
  exit 1
fi
deb=$(scripts/build_deb.sh)
# apt's download user (_apt) cannot read files under /home; install from /tmp.
tmp=$(mktemp -d /tmp/iphone-mirror-bridge.XXXXXX)  # not $TMPDIR: may be private (e.g. /tmp/user/0)
trap 'rm -rf "$tmp"' EXIT
install -m 0644 "$deb" "$tmp/"
chmod 0755 "$tmp"
# --reinstall: a rebuilt package with an unchanged version must still replace the old files.
apt-get install -y --reinstall "$tmp/$(basename "$deb")"
