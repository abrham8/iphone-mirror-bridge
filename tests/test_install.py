"""Build the .deb and check its contents (needs dpkg-deb; no root)."""
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb is required")
class DebPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run(["bash", str(REPO / "scripts/build_deb.sh")], cwd=REPO,
                             capture_output=True, text=True, check=True)
        cls.deb = REPO / out.stdout.strip().splitlines()[-1]

    def dpkg(self, *args):
        return subprocess.run(["dpkg-deb", *args, str(self.deb)], capture_output=True,
                              text=True, check=True).stdout

    def test_control_fields(self):
        control = self.dpkg("-f")
        self.assertIn("Package: iphone-mirror-bridge", control)
        for dep in ("python3-dbus", "python3-gi", "python3-evdev", "bluez"):
            self.assertIn(dep, control)
        self.assertIn("Architecture: all", control)

    def test_files(self):
        listing = self.dpkg("-c")
        for path in ("./usr/bin/iphone-mirror-bridge",
                     "./usr/lib/iphone-mirror-bridge/mouse_bridge.py",
                     "./usr/lib/iphone-mirror-bridge/ble_hid.py",
                     "./usr/lib/iphone-mirror-bridge/hid_report.py",
                     "./usr/lib/systemd/system/iphone-mirror-bridge.service",
                     "./etc/iphone-mirror-bridge/bridge.conf",
                     "./usr/share/man/man1/iphone-mirror-bridge.1.gz",
                     "./usr/share/doc/iphone-mirror-bridge/copyright"):
            self.assertIn(path, listing)

    def test_service_runs_installed_command(self):
        unit = (REPO / "packaging/debian/iphone-mirror-bridge.service").read_text()
        self.assertIn("ExecStart=/usr/bin/iphone-mirror-bridge $BRIDGE_ARGS", unit)
        self.assertIn("RuntimeDirectory=iphone-mirror-bridge", unit)  # lock file location


if __name__ == "__main__":
    unittest.main()
