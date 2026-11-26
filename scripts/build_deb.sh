#!/usr/bin/env bash
# Build dist/iphone-mirror-bridge_<version>_all.deb (no root needed).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' scripts/mouse_bridge.py)
[ -n "$version" ] || { echo "Cannot read __version__ from scripts/mouse_bridge.py" >&2; exit 1; }
pkg=iphone-mirror-bridge
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
root="$build/$pkg"
deb=packaging/debian

install -d "$root/DEBIAN" "$root/usr/bin" "$root/usr/lib/$pkg" "$root/usr/share/man/man1" \
           "$root/usr/share/applications" "$root/usr/share/icons/hicolor/scalable/apps" \
           "$root/usr/share/dbus-1/system.d" "$root/etc/xdg/autostart" \
           "$root/usr/lib/systemd/system" "$root/etc/$pkg" "$root/usr/share/doc/$pkg"

sed "s/@VERSION@/$version/" "$deb/control.in" > "$root/DEBIAN/control"
for f in postinst prerm postrm; do install -m 0755 "$deb/$f" "$root/DEBIAN/$f"; done
printf '%s\n' "/etc/$pkg/bridge.conf" "/etc/xdg/autostart/iphone-mirror-bridge-ui.desktop" > "$root/DEBIAN/conffiles"

install -m 0755 "$deb/iphone-mirror-bridge.sh" "$root/usr/bin/$pkg"
install -m 0755 "$deb/iphone-mirror-screen.sh" "$root/usr/bin/iphone-mirror-screen"
install -m 0755 "$deb/iphone-mirror-bridge-ui.sh" "$root/usr/bin/iphone-mirror-bridge-ui"
install -m 0644 "$deb/iphone-mirror-bridge-ui.desktop" "$root/usr/share/applications/"
install -m 0644 "$deb/iphone-mirror-bridge-ui-autostart.desktop" "$root/etc/xdg/autostart/iphone-mirror-bridge-ui.desktop"
# App icon: the logo from the Figma design.
install -m 0644 ui/iphone_mirror_bridge_ui/assets/app-logo.svg "$root/usr/share/icons/hicolor/scalable/apps/iphone-mirror-bridge.svg"
# Desktop app (Python package, QML and design assets).
ui_dest="$root/usr/lib/$pkg/ui/iphone_mirror_bridge_ui"
install -d "$ui_dest/qml" "$ui_dest/assets"
install -m 0644 ui/iphone_mirror_bridge_ui/*.py "$ui_dest/"
install -m 0644 ui/iphone_mirror_bridge_ui/qml/* "$ui_dest/qml/"
install -m 0644 ui/iphone_mirror_bridge_ui/assets/*.svg "$ui_dest/assets/"
install -m 0644 scripts/mouse_bridge.py scripts/ble_hid.py scripts/hid_report.py scripts/control.py "$root/usr/lib/$pkg/"
install -m 0644 "$deb/io.github.abrham8.IPhoneMirrorBridge.conf" "$root/usr/share/dbus-1/system.d/"
install -m 0644 "$deb/iphone-mirror-bridge.service" "$root/usr/lib/systemd/system/"
install -m 0644 "$deb/bridge.conf" "$root/etc/$pkg/bridge.conf"
install -m 0644 "$deb/copyright" "$root/usr/share/doc/$pkg/copyright"
gzip -9n < "$deb/iphone-mirror-bridge.1" > "$root/usr/share/man/man1/$pkg.1.gz"
gzip -9n < "$deb/iphone-mirror-screen.1" > "$root/usr/share/man/man1/iphone-mirror-screen.1.gz"
gzip -9n < "$deb/iphone-mirror-bridge-ui.1" > "$root/usr/share/man/man1/iphone-mirror-bridge-ui.1.gz"
chmod 0644 "$root/usr/share/man/man1/"*.gz
install -m 0644 README.md "$root/usr/share/doc/$pkg/README.md"
printf '%s (%s) unstable; urgency=medium\n\n  * See https://github.com/abrham8/iphone-mirror-bridge/commits/main\n\n -- abrham8 <abrham8@users.noreply.github.com>  %s\n' \
  "$pkg" "$version" "$(date -R -d @"${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date +%s)}")" \
  | gzip -9n > "$root/usr/share/doc/$pkg/changelog.gz"

installed_kb=$(du -sk --exclude=DEBIAN "$root" | cut -f1)
echo "Installed-Size: $installed_kb" >> "$root/DEBIAN/control"

mkdir -p dist
out="dist/${pkg}_${version}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$root" "$out" >/dev/null
echo "$out"
