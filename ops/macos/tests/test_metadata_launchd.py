import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest


OPS = Path(__file__).resolve().parents[1]
EXPORT = "com.nibrwr.ccusage-codex-export"
WEEKLY = "com.nibrwr.ccusage-codex-report-weekly"
MONTHLY = "com.nibrwr.ccusage-codex-report-monthly"


class MetadataLaunchdTests(unittest.TestCase):
    def setUp(self):
        test_dir = tempfile.TemporaryDirectory()
        self.addCleanup(test_dir.cleanup)
        self.home = Path(test_dir.name) / "Sample Home"
        self.home.mkdir()
        self.fleet = self.home / "existing cloud"
        self.fleet.mkdir()
        self.collector = OPS / "export-codex-usage.py"
        self.analyzer = self.home / "review script.py"
        self.analyzer.touch()
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "CCUSAGE_FLEET_ROOT": str(self.fleet),
            "CCUSAGE_METADATA_PYTHON": sys.executable,
            "CCUSAGE_BINARY": "/usr/bin/true",
        }

    def render(self, name, *extra, **env):
        result = subprocess.run(
            ["/bin/zsh", str(OPS / name), *extra],
            env={**self.env, **env},
            capture_output=True,
            check=True,
        )
        self.assertFalse((self.home / "Library" / "LaunchAgents").exists())
        return plistlib.loads(result.stdout)

    def test_reuses_the_export_label_and_expected_intervals(self):
        for host, interval in [
            ("macbook-air-m4", 3600),
            ("mac-mini-m4-1", 900),
            ("mac-mini-m6", 900),
        ]:
            with self.subTest(host=host):
                payload = self.render(
                    "install-export-launchd.zsh",
                    "--metadata",
                    "--render",
                    CCUSAGE_MACHINE_ID=host,
                )
                self.assertEqual(payload["Label"], EXPORT)
                self.assertIs(payload["RunAtLoad"], True)
                self.assertEqual(payload["StartInterval"], interval)
                self.assertNotIn("KeepAlive", payload)
                self.assertNotIn("rsync", str(payload))
                self.assertIn(str(self.fleet), payload["ProgramArguments"])
                self.assertIn(
                    self.collector.name, Path(payload["ProgramArguments"][1]).name
                )

    def test_metadata_mode_only_renders_the_weekly_reporter(self):
        payload = self.render(
            "install-report-launchd.zsh",
            "--metadata",
            "--render",
            CCUSAGE_IMPROVEMENT_SCRIPT=str(self.analyzer),
        )
        self.assertEqual(payload["Label"], WEEKLY)
        self.assertNotIn(MONTHLY, str(payload))
        self.assertNotIn("KeepAlive", payload)
        self.assertEqual(
            payload["StartCalendarInterval"], {"Weekday": 1, "Hour": 8, "Minute": 0}
        )
        self.assertIn(str(self.analyzer), payload["ProgramArguments"])
        self.assertIn("weekly", payload["ProgramArguments"])

    def test_declared_model_and_role_cannot_override_real_install_preflight(self):
        result = subprocess.run(
            ["/bin/zsh", str(OPS / "install-export-launchd.zsh"), "--metadata"],
            env={**self.env, "CCUSAGE_MACHINE_ID": "macbook-air-m4"},
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / "Library" / "LaunchAgents").exists())


if __name__ == "__main__":
    unittest.main()
