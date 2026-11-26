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
                     "./usr/share/doc/iphone-mirror-bridge/copyright",
                     "./usr/bin/iphone-mirror-screen",
                     "./usr/share/applications/iphone-mirror-screen.desktop",
                     "./usr/share/icons/hicolor/scalable/apps/iphone-mirror-bridge.svg"):
            self.assertIn(path, listing)

    def test_service_runs_installed_command(self):
        unit = (REPO / "packaging/debian/iphone-mirror-bridge.service").read_text()
        self.assertIn("ExecStart=/usr/bin/iphone-mirror-bridge $BRIDGE_ARGS", unit)
        self.assertIn("RuntimeDirectory=iphone-mirror-bridge", unit)  # lock file location

    def test_screen_mirroring_depends_on_uxplay(self):
        self.assertIn("uxplay", self.dpkg("-f").split("Depends:")[1].splitlines()[0])


@unittest.skipUnless(shutil.which("pgrep") and shutil.which("pkill"), "procps is required")
class ScreenToggleTests(unittest.TestCase):
    """Run iphone-mirror-screen against a fake uxplay."""

    def setUp(self):
        import os
        import tempfile
        self.tmp = tempfile.mkdtemp()
        bin_dir = Path(self.tmp, "bin")
        bin_dir.mkdir()
        fake = bin_dir / "uxplay"
        fake.write_text('#!/bin/sh\necho "fake uxplay $*"\nwhile true; do sleep 1; done\n')
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", HOME=self.tmp,
                        XDG_CACHE_HOME=str(Path(self.tmp, "cache")))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(lambda: self.run_cmd("--stop"))

    def run_cmd(self, *args):
        return subprocess.run(["sh", str(REPO / "packaging/debian/iphone-mirror-screen.sh"), *args],
                              env=self.env, capture_output=True, text=True)

    def test_toggle_start_and_stop(self):
        self.assertIn("Ready for screen mirroring", self.run_cmd().stdout)
        self.assertEqual(self.run_cmd("--status").stdout.strip(), "running")
        log = Path(self.tmp, "cache/iphone-mirror-bridge/uxplay.log").read_text()
        self.assertIn("-n iPhone Mirror Bridge -nh -p", log)
        self.assertIn("-vsync no -fps 60", log)  # low-latency defaults
        self.assertIn("Already running", self.run_cmd("--start").stdout)
        self.assertIn("stopped", self.run_cmd().stdout)
        self.assertEqual(self.run_cmd("--status").returncode, 3)

    def test_video_args_override(self):
        self.env["IPHONE_MIRROR_VIDEO_ARGS"] = "-fps 30"
        self.assertIn("Ready for screen mirroring", self.run_cmd("--start").stdout)
        log = Path(self.tmp, "cache/iphone-mirror-bridge/uxplay.log").read_text()
        self.assertIn("-nh -p -key", log)
        self.assertTrue(log.strip().endswith("uxplay.pem -fps 30"), log)


class WildosFirewallTests(unittest.TestCase):
    """iphone-mirror-screen --allow-firewall against a fake Wild OS firewall."""

    def setUp(self):
        import json
        import os
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        self.calls = self.tmp / "calls"
        fw = self.tmp / "settings-firewall"
        fw.write_text(f'#!/bin/sh\necho "$*" >> {self.calls}\n')
        fw.chmod(0o755)
        pkexec = bin_dir / "pkexec"
        pkexec.write_text('#!/bin/sh\nexec "$@"\n')
        pkexec.chmod(0o755)
        self.state = self.tmp / "state.json"
        self.config = self.tmp / "firewall.json"
        self.state.write_text(json.dumps({"enabled": True, "loaded": True, "level": "standard",
                                          "networks": [{"uuid": "e538feab-05bf", "trust": ""}]}))
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", HOME=str(self.tmp),
                        WILDOS_FIREWALL=str(fw), WILDOS_STATE=str(self.state),
                        WILDOS_CONFIG=str(self.config))

    def run_cmd(self, *args):
        return subprocess.run(["sh", str(REPO / "packaging/debian/iphone-mirror-screen.sh"), *args],
                              env=self.env, capture_output=True, text=True)

    def test_marks_network_home_and_allows_ports(self):
        out = self.run_cmd("--allow-firewall")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        calls = self.calls.read_text().splitlines()
        self.assertEqual(calls[0], "trust e538feab-05bf home")
        self.assertIn("allow iPhone Screen Mirroring 7000 tcp network - forever", calls)
        self.assertIn("allow iPhone Screen Mirroring 7011 udp network - forever", calls)
        self.assertEqual(len(calls), 7)

    def test_only_missing_rules_are_added(self):
        import json
        self.state.write_text(json.dumps({"enabled": True, "loaded": True, "level": "standard",
                                          "networks": [{"uuid": "e538feab-05bf", "trust": "home"}]}))
        self.config.write_text(json.dumps({"rules": [
            {"port": p, "proto": "tcp", "scope": "network", "on": True} for p in (7000, 7001, 7100)]
            + [{"port": p, "proto": "udp", "scope": "network"} for p in (6000, 6001)]}))
        self.run_cmd("--allow-firewall")
        self.assertEqual(self.calls.read_text().splitlines(),
                         ["allow iPhone Screen Mirroring 7011 udp network - forever"])
        self.config.write_text(self.config.read_text().replace("6001", "7011").replace(
            '"rules": [', '"rules": [{"port": 6001, "proto": "both", "scope": "anyone"}, '))
        self.calls.unlink()
        self.assertIn("Nothing to change", self.run_cmd("--allow-firewall").stdout)
        self.assertFalse(self.calls.exists())

    def test_nothing_without_wildos(self):
        self.env["WILDOS_FIREWALL"] = str(self.tmp / "missing")
        self.assertIn("Nothing to change", self.run_cmd("--allow-firewall").stdout)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
