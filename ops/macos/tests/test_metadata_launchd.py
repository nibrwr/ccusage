from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


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


def load_installer():
    spec = importlib.util.spec_from_file_location(
        "metadata_launchd", OPS / "install-metadata-launchd.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LegacyExporterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "Sample Home"
        self.machine = "mac-mini-m4-1"
        self.fleet = self.home / "iCloud Fleet"
        self.summary = self.fleet / "summaries" / self.machine / "latest.json"
        self.summary.parent.mkdir(parents=True)
        self.summary.write_bytes(self.snapshot_bytes("2026-11-15T16:04:00Z"))
        self.last_good = self.summary.read_bytes()
        self.plist = self.home / "Library" / "LaunchAgents" / f"{EXPORT}.plist"
        self.plist.parent.mkdir(parents=True)
        original = self.home / "ccusage" / "ops" / "macos" / "export-codex-logs.zsh"
        original.parent.mkdir(parents=True)
        original.write_text("#!/bin/zsh\nexit 100\n")
        original.chmod(0o700)
        self.raw_spec = {
            "Label": EXPORT,
            "ProgramArguments": [
                "/bin/zsh",
                "-lc",
                f"CCUSAGE_MACHINE_ID='{self.machine}' CCUSAGE_FLEET_ROOT='{self.fleet}' "
                f"CODEX_HOME='{self.home / '.codex'}' '{original}'",
            ],
            "StartInterval": 900,
            "RunAtLoad": True,
        }
        self.plist.write_bytes(plistlib.dumps(self.raw_spec))
        self.original = self.plist.read_bytes()
        self.loaded = dict(self.raw_spec)
        self.bootstrap_specs = []
        self.fail_run = False
        self.skip_write = False
        self.old_archive = self.fleet / "codex" / self.machine / "fictional-archive"
        self.old_archive.parent.mkdir(parents=True)
        self.old_archive.write_bytes(b"historical fixture")
        self.installer = load_installer()

    def snapshot_bytes(self, observed):
        data = {
            "schema_version": 1,
            "source": {"collector": "ccusage-codex-session+native-metadata"},
            "machine_id": self.machine,
            "scope": "fleet",
            "observed_at": observed,
            "sessions": [{"quality": "complete"}],
            "coverage": {"complete": 1},
        }
        canonical = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
        return json.dumps(
            {"data": data, "sha256": hashlib.sha256(canonical).hexdigest()}
        ).encode()

    def call(self, exe, *args, check=True):
        if exe == "/usr/sbin/scutil":
            stdout = "verified-mini\n"
        elif exe == "/usr/sbin/system_profiler":
            stdout = json.dumps(
                {
                    "SPHardwareDataType": [
                        {"machine_name": "Mac mini", "chip_type": "Apple M4"}
                    ]
                }
            )
        else:
            self.assertEqual(exe, "/bin/launchctl")
            return self.launchctl(args)
        return subprocess.CompletedProcess(args, 0, stdout, "")

    def launchctl(self, args):
        verb = args[0]
        if verb == "print-disabled":
            return subprocess.CompletedProcess(args, 0, "", "")
        if verb == "print":
            if self.loaded is None:
                return subprocess.CompletedProcess(
                    args, 1, "", "Could not find service"
                )
            argv = self.loaded["ProgramArguments"]
            lines = [
                f"\tpath = {self.plist}",
                "\tstate = not running",
                f"\tprogram = {argv[0]}",
                "\targuments = {",
                *[f"\t\t{arg}" for arg in argv],
                "\t}",
                "\truns = 1",
                f"\tlast exit code = {int(self.fail_run and self.loaded != self.raw_spec)}",
            ]
            return subprocess.CompletedProcess(args, 0, "\n".join(lines), "")
        if verb == "bootout":
            self.loaded = None
        elif verb == "bootstrap":
            self.assertEqual(Path(args[2]), self.plist)
            self.loaded = plistlib.loads(self.plist.read_bytes())
            self.bootstrap_specs.append(self.loaded)
            self.assertEqual(
                Path(self.loaded["ProgramArguments"][1]).name, "export-codex-usage.py"
            )
            if self.fail_run:
                self.summary.write_bytes(b"interrupted snapshot")
            elif not self.skip_write:
                observed = datetime.now(timezone.utc).isoformat()
                self.summary.write_bytes(self.snapshot_bytes(observed))
        else:
            self.fail(f"Unexpected launchctl action {verb}")
        return subprocess.CompletedProcess(args, 0, "", "")

    def execute(self, *flags):
        config = {
            "CCUSAGE_FLEET_ROOT": str(self.fleet),
            "CCUSAGE_MACHINE_ID": self.machine,
            "CCUSAGE_VERIFIED_HOSTNAME": "verified-mini",
            "CCUSAGE_METADATA_PYTHON": sys.executable,
            "CCUSAGE_BINARY": "/usr/bin/true",
        }
        original_stat = Path.stat

        def fixture_stat(path, *args, **kwargs):
            if path == Path("/dev/console"):
                return SimpleNamespace(st_uid=os.getuid())
            return original_stat(path, *args, **kwargs)

        output = io.StringIO()
        with (
            patch.object(self.installer, "CLOUD", self.fleet),
            patch.object(self.installer, "call", side_effect=self.call),
            patch.object(Path, "home", return_value=self.home),
            patch.object(Path, "stat", fixture_stat),
            patch.dict(os.environ, config),
            patch.object(
                sys, "argv", ["install-metadata-launchd.py", "export", *flags]
            ),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            try:
                status = self.installer.main()
            except SystemExit as error:
                status = error.code
        return status, output.getvalue()

    def parked_bytes(self):
        log = self.home / "Library" / "Logs" / "ccusage"
        return [path.read_bytes() for path in log.glob("*/legacy-export.plist")]

    def test_legacy_requires_explicit_migration(self):
        status, message = self.execute()
        self.assertEqual(status, 1)
        self.assertIn("non-destructive rollback", message)
        self.assertEqual(self.plist.read_bytes(), self.original)
        self.assertEqual(self.bootstrap_specs, [])

    def test_migration_parks_legacy_and_installs_metadata(self):
        status, message = self.execute("--migrate-legacy-export")
        self.assertEqual(status, 0, message)
        self.assertEqual(len(self.bootstrap_specs), 1)
        self.assertEqual(self.parked_bytes(), [self.original])
        self.assertEqual(self.loaded["StartInterval"], 900)
        self.assertEqual(self.old_archive.read_bytes(), b"historical fixture")

    def test_failed_migration_leaves_legacy_unloaded_and_restores_last_summary(self):
        self.fail_run = True
        status, message = self.execute("--migrate-legacy-export")
        self.assertEqual(status, 1)
        self.assertIn("legacy exporter remains unloaded", message)
        self.assertEqual(len(self.bootstrap_specs), 1)
        self.assertIsNone(self.loaded)
        self.assertFalse(self.plist.exists())
        self.assertEqual(self.parked_bytes(), [self.original])
        self.assertEqual(self.summary.read_bytes(), self.last_good)
        self.assertEqual(self.old_archive.read_bytes(), b"historical fixture")

    def test_migration_rejects_a_successful_job_that_did_not_publish(self):
        fixed = datetime.now(timezone.utc).replace(microsecond=0)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz)

        self.summary.write_bytes(self.snapshot_bytes(fixed.isoformat()))
        self.skip_write = True
        with patch.object(self.installer, "datetime", FixedDatetime):
            status, message = self.execute("--migrate-legacy-export")
        self.assertEqual(status, 1, message)
        self.assertIn("legacy exporter remains unloaded", message)
        self.assertEqual(self.parked_bytes(), [self.original])
        self.assertIsNone(self.loaded)

    def test_retry_installs_metadata_after_the_legacy_job_was_parked(self):
        self.fail_run = True
        status, message = self.execute("--migrate-legacy-export")
        self.assertEqual(status, 1, message)
        self.fail_run = False
        status, message = self.execute("--migrate-legacy-export")
        self.assertEqual(status, 0, message)
        self.assertEqual(len(self.bootstrap_specs), 2)
        self.assertEqual(self.parked_bytes(), [self.original])
        self.assertEqual(self.loaded["StartInterval"], 900)

    def test_migration_leaves_an_unrecognized_legacy_job_untouched(self):
        self.raw_spec["ProgramArguments"][2] += " ; another-command"
        self.original = plistlib.dumps(self.raw_spec)
        self.plist.write_bytes(self.original)
        status, message = self.execute("--migrate-legacy-export")
        self.assertEqual(status, 1, message)
        self.assertIn("outside the verified legacy exporter layout", message)
        self.assertEqual(self.plist.read_bytes(), self.original)
        self.assertEqual(self.bootstrap_specs, [])

    def test_migration_requires_a_verified_fallback_snapshot(self):
        self.summary.write_bytes(b"corrupt")
        status, message = self.execute("--migrate-legacy-export")
        self.assertEqual(status, 1)
        self.assertIn("last good summary", message)
        self.assertEqual(self.plist.read_bytes(), self.original)
        self.assertEqual(self.loaded, self.raw_spec)
        self.assertEqual(self.bootstrap_specs, [])


if __name__ == "__main__":
    unittest.main()
