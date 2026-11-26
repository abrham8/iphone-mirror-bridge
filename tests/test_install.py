"""Dry-run the installer into a temporary root (no systemctl, no root needed)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class InstallScriptTests(unittest.TestCase):
    def run_install(self, root, *args):
        env = dict(os.environ, ROOT=root, SKIP_SYSTEMCTL="1")
        return subprocess.run(["bash", str(REPO / "scripts/install.sh"), *args], env=env,
                              capture_output=True, text=True, check=True)

    def test_install_and_uninstall(self):
        with tempfile.TemporaryDirectory() as root:
            self.run_install(root)
            config = Path(root, "etc/default/iphone-mirror-bridge").read_text()
            self.assertIn('BRIDGE_ARGS="--touchpad"', config)
            for name in ("mouse_bridge.py", "ble_hid.py", "hid_report.py"):
                self.assertTrue(Path(root, "opt/iphone-mirror-bridge/scripts", name).is_file(), name)
            unit = Path(root, "etc/systemd/system/mouse-bridge.service").read_text()
            self.assertIn("ExecStart=/usr/bin/python3 /opt/iphone-mirror-bridge/scripts/mouse_bridge.py $BRIDGE_ARGS",
                          unit)
            self.assertIn("RuntimeDirectory=iphone-mirror-bridge", unit)  # lock file location

            self.run_install(root, "--device", "/dev/input/event3")
            self.assertIn('BRIDGE_ARGS="--device /dev/input/event3"',
                          Path(root, "etc/default/iphone-mirror-bridge").read_text())

            self.run_install(root, "--uninstall")
            self.assertEqual([p for p in Path(root).rglob("*") if p.is_file()], [])


if __name__ == "__main__":
    unittest.main()
