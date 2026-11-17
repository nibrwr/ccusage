#!/usr/bin/env python3
"""Publish an allowlisted metadata projection from local ccusage Codex records."""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

MACHINES = ("mac-mini-m4-1", "macbook-air-m4", "mac-mini-m6")
SESSION_DIRECTORIES = ("sessions", "archived_sessions")
UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
SESSION_ID = re.compile(UUID)
LOG_ID = re.compile(r"(?:^|-)" + f"({UUID})$")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-zA-Z0-9.-]{1,32})?")
MODEL = re.compile(r"(?:gpt-|codex-|o[134])[a-z0-9.-]{0,42}")
EFFORTS = frozenset(
    ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
)
EVENT = re.compile(
    rb'^\{\s*(?:(?:"timestamp"\s*:\s*"[^"]+"|"ordinal"\s*:\s*[0-9]+)\s*,\s*)*"type"\s*:\s*"(session_meta|turn_context|event_msg)"'
)
TOKEN_EVENT = re.compile(rb'"type"\s*:\s*"token_count"')
COUNTERS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def utc(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError, ValueError):
        pass
    return None


def canonical(value):
    return json.dumps(
        value, separators=(",", ":"), sort_keys=True, ensure_ascii=True
    ).encode()


def consistent(tokens):
    return (
        isinstance(tokens, dict)
        and all(type(tokens.get(key)) is int and tokens[key] >= 0 for key in COUNTERS)
        and tokens["cached_input_tokens"] <= tokens["input_tokens"]
        and tokens["reasoning_output_tokens"] <= tokens["output_tokens"]
        and tokens["total_tokens"] == tokens["input_tokens"] + tokens["output_tokens"]
    )


def verify_fleet_metadata(data, machine, as_of):
    """Check that the summary contains usable sanitized usage and return its time."""
    observed = datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
    projected = {
        "schema_version",
        "machine_id",
        "scope",
        "observed_at",
        "source",
        "coverage",
        "sessions",
    }
    source = data["source"]
    required_source = {"collector", "ccusage_version"}
    directories = source.get("directories", [])
    if (
        data.keys() != projected
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["machine_id"] != machine
        or data["scope"] != "fleet"
        or not required_source <= source.keys() <= required_source | {"directories"}
        or source["collector"] != "ccusage-codex-session+native-metadata"
        or not VERSION.fullmatch(source["ccusage_version"])
        or not isinstance(directories, list)
        or not all(directory in SESSION_DIRECTORIES for directory in directories)
        or observed.tzinfo is None
        or observed > as_of + timedelta(minutes=5)
        or not isinstance(data["sessions"], list)
    ):
        raise ValueError("The export did not publish verifiable usage metadata")

    seen = set()
    counts = Counter()
    latest_activity = observed + timedelta(minutes=5)
    for row in data["sessions"]:
        session, quality = verify_usage_row(row, latest_activity)
        if session in seen:
            raise ValueError("The export contains a duplicate session")
        seen.add(session)
        counts[quality] += 1

    coverage = data["coverage"]
    required = {"complete", "partial", "unlinked"}
    if (
        not required <= coverage.keys() <= required | {"ccusage_only"}
        or any(type(number) is not int or number < 0 for number in coverage.values())
        or any(coverage[key] != counts[key] for key in ("complete", "partial"))
        or not counts["complete"]
    ):
        raise ValueError("The export did not report verifiable coverage")
    return observed


def verify_usage_row(row, latest_activity):
    required = {
        "session_id",
        "quality",
        "started_at",
        "last_activity",
        "models",
        "efforts",
        "tokens",
    }
    if not required <= row.keys() <= required | {"codex_version"}:
        raise ValueError("The summary includes fields outside the metadata projection")
    session, quality, version = (
        row["session_id"],
        row["quality"],
        row.get("codex_version"),
    )
    start, last = utc(row["started_at"]), utc(row["last_activity"])
    if (
        not SESSION_ID.fullmatch(session)
        or (version is not None and not VERSION.fullmatch(version))
        or not isinstance(row["models"], list)
        or not all(MODEL.fullmatch(model) for model in row["models"])
        or not isinstance(row["efforts"], list)
        or not all(effort in EFFORTS for effort in row["efforts"])
        or (start is None and row["started_at"] is not None)
        or (last is None and row["last_activity"] is not None)
    ):
        raise ValueError("The export contains invalid session metadata")
    tokens = row["tokens"]
    if quality == "partial" and tokens is None:
        return session, quality
    if (
        quality != "complete"
        or not consistent(tokens)
        or tokens.keys() != set(COUNTERS)
        or any(number >= 2**63 for number in tokens.values())
        or start is None
        or last is None
        or not (
            datetime.fromisoformat(start.replace("Z", "+00:00"))
            <= datetime.fromisoformat(last.replace("Z", "+00:00"))
            <= latest_activity
        )
    ):
        raise ValueError("The export contains an invalid usage row")
    return session, quality


def find_logs(folders, selected):
    def unreadable(error):
        raise error

    files = {}
    unlinked = 0
    for folder in folders:
        for directory, _, names in os.walk(folder, onerror=unreadable):
            for name in names:
                if not name.endswith(".jsonl"):
                    continue
                path = Path(directory) / name
                if path.is_symlink() or not path.is_file():
                    continue
                match = LOG_ID.search(path.stem)
                if not match:
                    unlinked += 1
                elif not selected or match[1] == selected:
                    files.setdefault(match[1], []).append(path)
    return files, unlinked


def native_metadata(path, session_id):
    result = {
        "session_id": session_id,
        "started_at": None,
        "last_activity": None,
        "codex_version": None,
        "efforts": [],
        "tokens": None,
    }
    efforts = set()
    complete = True
    last_counters = None
    with path.open("rb") as stream:
        for line in stream:
            match = EVENT.match(line[:160])
            if not match or (
                match[1] == b"event_msg" and not TOKEN_EVENT.search(line[:1024])
            ):
                continue
            if not line.endswith(b"\n"):
                complete = False
                continue
            try:
                event = json.loads(line)
                payload = event["payload"]
                if not isinstance(payload, dict):
                    raise ValueError()
            except (ValueError, KeyError, UnicodeError):
                complete = False
                continue
            if match[1] == b"session_meta":
                if payload.get("id") != session_id or payload.get("forked_from_id"):
                    complete = False
                    continue
                result["started_at"] = utc(payload.get("timestamp")) or utc(
                    event.get("timestamp")
                )
                version = payload.get("cli_version")
                if isinstance(version, str) and VERSION.fullmatch(version):
                    result["codex_version"] = version
            elif match[1] == b"turn_context":
                effort = payload.get("effort") or payload.get("reasoning_effort")
                if isinstance(effort, str) and effort in EFFORTS:
                    efforts.add(effort)
            elif payload.get("type") == "token_count" and isinstance(
                payload.get("info"), dict
            ):
                total = payload["info"].get("total_token_usage")
                if not consistent(total):
                    complete = False
                    continue
                counters = {key: total[key] for key in COUNTERS}
                if last_counters and any(
                    counters[key] < last_counters[key] for key in COUNTERS
                ):
                    complete = False
                last_counters = counters
                result["last_activity"] = utc(event.get("timestamp"))
    result["efforts"] = sorted(efforts)
    if complete and result["started_at"] and result["last_activity"]:
        result["tokens"] = last_counters
    return result


def ccusage_rows(binary, folders, selected):
    paths = [str(folder) for folder in folders]
    if any("," in path for path in paths):
        raise ValueError("Unsupported local source directory")
    env = {**os.environ, "CODEX_HOME": ",".join(paths)}
    cmd = [
        binary,
        "codex",
        "session",
        "--offline",
        "--no-cost",
        "--json",
        "--timezone",
        "UTC",
        "--single-thread",
    ]
    if selected:
        cmd += ["--id", selected]
    response = subprocess.run(cmd, env=env, capture_output=True, check=True, timeout=90)
    payload = json.loads(response.stdout)
    if isinstance(payload, dict) and isinstance(payload.get("sessions"), list):
        return payload["sessions"]
    if selected and isinstance(payload, dict) and "sessionId" in payload:
        return [payload]
    raise ValueError("Unsupported ccusage session schema")


def project_row(row):
    if not isinstance(row, dict) or not isinstance(row.get("sessionId"), str):
        raise ValueError("Unsupported ccusage row schema")
    match = LOG_ID.search(row["sessionId"])
    if not match:
        return None, None
    cache = row.get("cacheReadTokens", row.get("cachedInputTokens"))
    input_count = row.get("inputTokens")
    tokens = {
        "input_tokens": input_count + cache
        if type(input_count) is type(cache) is int
        else None,
        "cached_input_tokens": cache,
        "output_tokens": row.get("outputTokens"),
        "reasoning_output_tokens": row.get("reasoningOutputTokens"),
        "total_tokens": row.get("totalTokens"),
    }
    models = row.get("models")
    if not isinstance(models, dict):
        raise ValueError("Unsupported ccusage model schema")
    verified = sorted(
        name
        for name, info in models.items()
        if MODEL.fullmatch(name)
        and isinstance(info, dict)
        and info.get("isFallback") is False
    )
    return match[1], {
        "tokens": tokens if consistent(tokens) else None,
        "models": verified,
        "last_activity": utc(row.get("lastActivity")),
    }


def collect(args):
    codex = args.codex_home
    folders = [
        directory
        for name in SESSION_DIRECTORIES
        if (directory := codex / name).is_dir() and not directory.is_symlink()
    ]
    if not folders:
        raise ValueError("Local Codex log directories are unavailable")
    files, unlinked = find_logs(folders, args.session)
    if args.session and not files:
        raise ValueError("Requested session metadata is unavailable")
    version = (
        subprocess.run(
            [args.ccusage, "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        .stdout.strip()
        .removeprefix("ccusage ")
    )
    if not VERSION.fullmatch(version):
        raise ValueError("Unrecognized ccusage version")
    grouped = {}
    for row in ccusage_rows(args.ccusage, folders, args.session):
        session_id, projected = project_row(row)
        if session_id is not None:
            grouped.setdefault(session_id, []).append(projected)

    without_native_file = sum(
        len(group) for key, group in grouped.items() if key not in files
    )
    sessions = []
    for session_id, paths in sorted(files.items()):
        reports = grouped.get(session_id, [])
        native = [native_metadata(path, session_id) for path in paths]
        primary = native[0]
        if not reports and len(native) == 1 and primary["last_activity"] is None:
            unlinked += 1
            continue
        models = reports[0]["models"] if reports else []
        complete = (
            all(item == primary for item in native)
            and bool(primary["tokens"])
            and bool(reports)
            and all(item == reports[0] for item in reports)
            and primary["tokens"] == reports[0]["tokens"]
            and primary["last_activity"] == reports[0]["last_activity"]
        )
        sessions.append(
            {
                **primary,
                "tokens": primary["tokens"] if complete else None,
                "models": models if complete else [],
                "quality": "complete" if complete else "partial",
            }
        )

    return {
        "schema_version": 1,
        "machine_id": args.machine,
        "scope": "proof" if args.session else "fleet",
        "observed_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "source": {
            "collector": "ccusage-codex-session+native-metadata",
            "ccusage_version": version,
            "directories": [path.name for path in folders],
        },
        "coverage": {
            "complete": sum(row["quality"] == "complete" for row in sessions),
            "partial": sum(row["quality"] == "partial" for row in sessions),
            "unlinked": unlinked,
            "ccusage_only": without_native_file,
        },
        "sessions": sessions,
    }


def publish(args):
    if not args.fleet_root.is_dir():
        raise ValueError("Existing fleet root is unavailable")
    output_parent = args.fleet_root / "summaries"
    output = output_parent / args.machine
    if output_parent.is_symlink() or output.is_symlink():
        raise ValueError("Summary directories must not be symlinks")
    support = Path.home() / "Library/Application Support/ccusage-fleet"
    lock_dir = support / "locks"
    if support.is_symlink() or lock_dir.is_symlink():
        raise ValueError("Local lock directories must not be symlinks")
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = lock_dir.stat()
    if state.st_uid != os.getuid() or state.st_mode & 0o077:
        raise ValueError("Local lock directory is not owner-only")
    local_lock = lock_dir / f"metadata-{args.machine}.lock"
    lock_fd = os.open(local_lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        data = collect(args)
        envelope = {"data": data, "sha256": hashlib.sha256(canonical(data)).hexdigest()}
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = output / (
            f"proof-{args.session}.json" if args.session else "latest.json"
        )
        with tempfile.NamedTemporaryFile(
            dir=output, prefix=".usage-", delete=False
        ) as stream:
            staged = Path(stream.name)
            try:
                stream.write(canonical(envelope) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(staged, target)
            finally:
                staged.unlink(missing_ok=True)
        coverage = data["coverage"]
        print(
            f"{args.machine}: {coverage['complete']} complete, "
            f"{coverage['partial']} partial, {coverage['unlinked']} without linkage, "
            f"{coverage['ccusage_only']} ccusage-only."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", choices=MACHINES, required=True)
    parser.add_argument("--fleet-root", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--ccusage", required=True)
    parser.add_argument(
        "--session", help="Prove one UUID in a separate file without replacing latest"
    )
    args = parser.parse_args()
    if args.session and not SESSION_ID.fullmatch(args.session):
        parser.error("Expected a Codex session UUID")
    try:
        publish(args)
    except BlockingIOError:
        parser.exit(75, "A local metadata export is already running.\n")
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.exit(
            1,
            "Could not produce a verified Codex usage snapshot. Prior output retained.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
