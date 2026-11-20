import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
SESSION = "01957b95-dc00-7000-8000-000000000084"
LATER = "01957b95-dc00-7000-8000-000000000085"
MODEL = "gpt-5.6-sol"
COUNTERS = {
    "input_tokens": 100,
    "cached_input_tokens": 40,
    "output_tokens": 30,
    "reasoning_output_tokens": 12,
    "total_tokens": 130,
}


class UsageExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fleet = self.root / "existing icloud"
        self.fleet.mkdir()
        self.codex = self.root / "local Codex"
        (self.codex / "sessions").mkdir(parents=True)
        self.ccusage = os.environ.get("CCUSAGE_TEST_BINARY") or shutil.which("ccusage")
        self.assertIsNotNone(self.ccusage, "Run with the existing ccusage CLI on PATH")

    def write_session(self, session=SESSION, total=None, archived=False):
        counters = COUNTERS if total is None else total
        directory = self.codex / ("archived_sessions" if archived else "sessions")
        directory.mkdir(exist_ok=True)
        target = directory / f"rollout-2026-11-10T00-00-00-{session}.jsonl"
        rows = [
            {
                "type": "session_meta",
                "timestamp": "2026-11-10T00:00:00Z",
                "payload": {
                    "id": session,
                    "cli_version": "0.169.0",
                    "cwd": "/Users/fictional-never-share",
                },
            },
            {
                "type": "turn_context",
                "timestamp": "2026-11-10T00:00:01Z",
                "payload": {"model": MODEL, "effort": "medium"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-11-10T00:00:02Z",
                "payload": {"type": "user_message", "message": "fictional-never-share"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-11-10T00:00:03Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": counters,
                        "last_token_usage": counters,
                    },
                },
            },
        ]
        events = [
            {
                "timestamp": row["timestamp"],
                "ordinal": ordinal,
                "type": row["type"],
                "payload": row["payload"],
            }
            for ordinal, row in enumerate(rows)
        ]
        target.write_text(
            "\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8"
        )
        return target

    def export(self, *extra, check=True):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "export-codex-usage.py"),
                "--machine",
                "mac-mini-m6",
                "--fleet-root",
                str(self.fleet),
                "--codex-home",
                str(self.codex),
                "--ccusage",
                self.ccusage,
                *extra,
            ],
            capture_output=True,
            text=True,
            check=check,
            env={**os.environ, "HOME": str(self.root)},
        )

    def snapshot(self, path="latest.json"):
        raw = (self.fleet / "summaries" / "mac-mini-m6" / path).read_text(
            encoding="utf-8"
        )
        self.assertNotIn("fictional-never-share", raw)
        self.assertNotIn("rollout-", raw)
        self.assertNotIn("sessionFile", raw)
        return json.loads(raw)["data"]

    def test_uses_installed_ccusage_and_projects_only_verified_metadata(self):
        self.write_session()
        self.export()
        snapshot = self.snapshot()
        self.assertEqual(snapshot["coverage"]["complete"], 1)
        self.assertEqual(
            snapshot["sessions"],
            [
                {
                    "session_id": SESSION,
                    "started_at": "2026-11-10T00:00:00Z",
                    "last_activity": "2026-11-10T00:00:03Z",
                    "codex_version": "0.169.0",
                    "models": [MODEL],
                    "efforts": ["medium"],
                    "quality": "complete",
                    "tokens": COUNTERS,
                }
            ],
        )

    def test_next_run_replaces_same_session_and_catches_up_archived_sessions(self):
        self.write_session()
        self.export()
        self.write_session(total={**COUNTERS, "input_tokens": 140, "total_tokens": 170})
        self.write_session(LATER, archived=True)
        self.export()
        rows = self.snapshot()["sessions"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["session_id"]: row["tokens"]["total_tokens"] for row in rows},
            {SESSION: 170, LATER: 130},
        )

    def compress(self, source):
        zstd = shutil.which("zstd")
        self.assertIsNotNone(zstd, "Run with the zstd CLI on PATH")
        target = source.with_suffix(".jsonl.zst")
        subprocess.run([zstd, "-q", "-o", str(target), str(source)], check=True)
        return target

    def test_compressed_archive_recovers_verified_usage(self):
        source = self.write_session(archived=True)
        self.compress(source)
        source.unlink()
        self.export()
        data = self.snapshot()
        self.assertEqual(data["coverage"]["complete"], 1)
        self.assertEqual(data["coverage"]["ccusage_only"], 0)
        self.assertEqual(data["sessions"][0]["tokens"], COUNTERS)

    def test_mixed_formats_keep_one_verified_session(self):
        source = self.write_session(archived=True)
        self.compress(source)
        self.export()
        self.assertEqual(self.snapshot()["coverage"]["complete"], 1)

    def test_conflicting_compressed_copy_is_partial(self):
        self.write_session()
        source = self.write_session(
            total={**COUNTERS, "input_tokens": 140, "total_tokens": 170}, archived=True
        )
        self.compress(source)
        source.unlink()
        self.export()
        self.assertEqual(self.snapshot()["coverage"]["partial"], 1)
        self.assertEqual(self.snapshot()["coverage"]["complete"], 0)

    def test_damaged_compressed_copy_cannot_verify_a_session(self):
        source = self.write_session()
        archive = self.compress(source)
        archive.write_bytes(archive.read_bytes()[:-4])
        self.export()
        self.assertEqual(self.snapshot()["coverage"]["complete"], 0)
        self.assertEqual(self.snapshot()["coverage"]["partial"], 1)

    def test_targeted_proof_does_not_overwrite_fleet_snapshot(self):
        self.write_session()
        self.write_session(LATER, archived=True)
        self.export("--session", SESSION)
        self.assertFalse(
            (self.fleet / "summaries" / "mac-mini-m6" / "latest.json").exists()
        )
        self.assertEqual(
            self.snapshot(f"proof-{SESSION}.json")["coverage"]["complete"], 1
        )

    def test_conflicting_copies_are_partial_without_tokens(self):
        self.write_session()
        self.write_session(
            total={**COUNTERS, "input_tokens": 140, "total_tokens": 170}, archived=True
        )
        self.export()
        snapshot = self.snapshot()
        self.assertEqual(snapshot["coverage"]["partial"], 1)
        self.assertIsNone(snapshot["sessions"][0]["tokens"])

    def test_failed_ccusage_run_leaves_previous_snapshot_unchanged(self):
        self.write_session()
        self.export()
        output = self.fleet / "summaries" / "mac-mini-m6" / "latest.json"
        before = output.read_bytes()
        self.ccusage = "/usr/bin/false"
        failure = self.export(check=False)
        self.assertNotEqual(failure.returncode, 0)
        self.assertEqual(output.read_bytes(), before)

    def test_missing_local_usage_is_a_collection_failure(self):
        (self.codex / "sessions").rmdir()
        failure = self.export(check=False)
        self.assertNotEqual(failure.returncode, 0)
        self.assertFalse((self.fleet / "summaries").exists())

    def test_empty_readable_directory_records_missing_usage_instead_of_zero(self):
        self.export()
        snapshot = self.snapshot()
        self.assertEqual(
            snapshot["coverage"],
            {"complete": 0, "partial": 0, "unlinked": 0, "ccusage_only": 0},
        )
        self.assertEqual(snapshot["sessions"], [])


if __name__ == "__main__":
    unittest.main()
