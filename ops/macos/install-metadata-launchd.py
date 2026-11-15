#!/usr/bin/env python3
"""Render and qualify the existing ccusage user LaunchAgents in metadata mode."""

import argparse
import fcntl
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import time

EXPORT = "com.nibrwr.ccusage-codex-export"
WEEKLY = "com.nibrwr.ccusage-codex-report-weekly"
AGENTS = {EXPORT, WEEKLY}
INTERVALS = {"mac-mini-m4-1": 900, "macbook-air-m4": 3600, "mac-mini-m6": 900}
HARDWARE = {
    "mac-mini-m4-1": ("Mac mini", "Apple M4"),
    "macbook-air-m4": ("MacBook Air", "Apple M4"),
    "mac-mini-m6": ("Mac mini", "Apple M6"),
}
CLOUD = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/ccusage-fleet"
LAUNCHCTL = "/bin/launchctl"


def call(*command, check=True):
    return subprocess.run(
        command, check=check, capture_output=True, text=True, timeout=30
    )


def path_env(key, fallback):
    path = Path(os.environ.get(key) or fallback).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{key} must be absolute")
    return path


def specification(args):
    python = path_env("CCUSAGE_METADATA_PYTHON", "/usr/bin/python3")
    fleet = path_env("CCUSAGE_FLEET_ROOT", CLOUD)
    log_dir = Path.home() / "Library/Logs/ccusage"
    if not os.access(python, os.X_OK) or not fleet.is_dir():
        raise ValueError("Verify the interpreter and existing fleet root first")
    if args.job == "export":
        machine = os.environ.get("CCUSAGE_MACHINE_ID")
        if machine not in INTERVALS:
            raise ValueError("Choose a verified fleet machine ID")
        collector = Path(__file__).with_name("export-codex-usage.py").resolve()
        binary = path_env("CCUSAGE_BINARY", Path.home() / ".local/bin/ccusage")
        codex = path_env("CODEX_HOME", Path.home() / ".codex")
        if (
            "," in str(codex)
            or not collector.is_file()
            or not os.access(binary, os.X_OK)
        ):
            raise ValueError(
                "Verify the local Codex home, collector and ccusage binary"
            )
        label = EXPORT
        argv = [
            python,
            collector,
            "--machine",
            machine,
            "--fleet-root",
            fleet,
            "--codex-home",
            codex,
            "--ccusage",
            binary,
        ]
        timing = {"RunAtLoad": True, "StartInterval": INTERVALS[machine]}
    else:
        machine = "mac-mini-m6"
        analyzer = path_env("CCUSAGE_IMPROVEMENT_SCRIPT", "/nonexistent")
        if not analyzer.is_file():
            raise ValueError("Identify the tested M6 report script")
        label = WEEKLY
        gh = Path(shutil.which("gh") or "/usr/bin/false").resolve()
        argv = [
            python,
            analyzer,
            "weekly",
            "--fleet-root",
            fleet,
            "--gh",
            gh,
            "--require-github",
        ]
        for variable, option in (
            ("CCUSAGE_IMPROVEMENT_PRS", "--github-prs"),
            ("CCUSAGE_IMPROVEMENT_BINDINGS", "--bindings"),
        ):
            if os.environ.get(variable):
                candidate = path_env(variable, "/nonexistent")
                if not candidate.is_file():
                    raise ValueError(f"Verify {variable} first")
                argv.extend([option, candidate])
        timing = {"StartCalendarInterval": {"Weekday": 1, "Hour": 8, "Minute": 0}}
    spec = {
        "Label": label,
        "ProgramArguments": [str(arg) for arg in argv],
        **timing,
        "StandardOutPath": str(log_dir / f"{label}.out.log"),
        "StandardErrorPath": str(log_dir / f"{label}.err.log"),
        "Umask": 0o077,
    }
    return machine, fleet, spec


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".ccusage-", delete=False
    ) as stream:
        staged = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)


def installed(target):
    return call(LAUNCHCTL, "print", target, check=False)


def loaded_arguments(output):
    program = re.search(r"(?m)^\s*program = (.+)$", output)
    args = re.search(r"(?m)^\targuments = \{\n(.*?)^\t\}$", output, re.DOTALL)
    if not program or not args:
        raise ValueError("Could not verify the loaded program and arguments")
    values = [line.removeprefix("\t\t") for line in args[1].splitlines()]
    if not values or values[0] != program[1]:
        raise ValueError("Loaded program does not match its arguments")
    return values


def loaded_from(path, output):
    matched = re.search(r"(?m)^\s*path = (.+)$", output)
    return matched is not None and Path(matched[1]) == path


def check_owner(path, previous, current, loaded, *, migration=None):
    if path.is_symlink() or (path.exists() and path.stat().st_uid != os.getuid()):
        raise ValueError("Existing label has unverified ownership")
    if loaded.returncode == 0 and (
        not loaded_from(path, loaded.stdout) or previous is None
    ):
        raise ValueError("Loaded label is owned by a different installation")
    if previous is None:
        return
    old = plistlib.loads(previous)
    label = current["Label"]
    argv = old.get("ProgramArguments", [])
    if (
        old.get("Label") != label
        or not isinstance(argv, list)
        or loaded.returncode == 0
        and loaded_arguments(loaded.stdout) != argv
    ):
        raise ValueError("Existing plist and loaded ccusage job do not agree")
    if argv == current["ProgramArguments"]:
        return
    if argv[:2] == ["/bin/zsh", "-lc"]:
        if migration is None or label != EXPORT:
            raise ValueError(
                "Legacy job needs a separately verified non-destructive rollback"
            )
        machine, fleet = migration
        verify_legacy_export(old, current, loaded, machine, fleet)
        return True
    expected = (
        "export-codex-usage.py" if label == EXPORT else "continuous-improvement.py"
    )
    support = Path.home() / "Library/Application Support/ccusage-fleet"
    if (
        len(argv) < 2
        or not Path(argv[1]).is_file()
        or Path(argv[1]).name != expected
        or support not in Path(argv[1]).resolve().parents
    ):
        raise ValueError("Existing label is not a recognized metadata installation")


def verify_legacy_export(old, current, loaded, machine, fleet):
    argv = old["ProgramArguments"]
    managed = current["ProgramArguments"]
    codex = managed[managed.index("--codex-home") + 1]
    required = [
        f"CCUSAGE_MACHINE_ID={machine}",
        f"CCUSAGE_FLEET_ROOT={fleet}",
        f"CODEX_HOME={codex}",
    ]
    parts = shlex.split(argv[2]) if len(argv) == 3 else []
    script = Path(parts[-1]) if len(parts) == 4 else Path()
    supported_keys = {
        "Label",
        "ProgramArguments",
        "RunAtLoad",
        "StartInterval",
        "Disabled",
        "StandardOutPath",
        "StandardErrorPath",
    }
    if (
        machine == "mac-mini-m6"
        or not old.keys() <= supported_keys
        or old.get("RunAtLoad") is not True
        or type(old.get("StartInterval")) is not int
        or old["StartInterval"] <= 0
        or len(parts) != 4
        or parts[:3] != required
        or script.name != "export-codex-logs.zsh"
        or not script.is_absolute()
        or script.is_symlink()
        or not script.is_file()
        or script.stat().st_uid != os.getuid()
    ):
        raise ValueError("Existing job is outside the verified legacy exporter layout")
    state = re.search(r"(?m)^\tstate = (.+)$", loaded.stdout)
    if loaded.returncode != 0 or not state or state[1] != "not running":
        raise ValueError(
            "Wait for the loaded legacy exporter to finish before migration"
        )


def verified_export(fleet, machine):
    path = fleet / "summaries" / machine / "latest.json"
    if any(item.is_symlink() for item in (path.parent, path.parent.parent)):
        raise ValueError("Refused a symlink in the summary path")
    limit = 8 * 1024 * 1024
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Expected a regular summary file")
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Summary exceeds the readback size limit")
    wrapped = json.loads(payload)
    data = wrapped["data"]
    canonical = json.dumps(
        data, separators=(",", ":"), sort_keys=True, ensure_ascii=True
    ).encode()
    observed = datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
    if (
        data["schema_version"] != 1
        or data["source"]["collector"] != "ccusage-codex-session+native-metadata"
        or data["machine_id"] != machine
        or data["scope"] != "fleet"
        or hashlib.sha256(canonical).hexdigest() != wrapped["sha256"]
        or not isinstance(data["sessions"], list)
        or not data["sessions"]
        or type(data["coverage"]["complete"]) is not int
        or data["coverage"]["complete"] <= 0
        or data["coverage"]["complete"]
        != sum(row["quality"] == "complete" for row in data["sessions"])
        or observed.tzinfo is None
        or observed > datetime.now(timezone.utc) + timedelta(minutes=5)
    ):
        raise ValueError("The export did not publish verifiable usage metadata")
    return path, payload, observed


def output_check(args, machine, fleet, process_started):
    if args.job == "export":
        _, _, observed = verified_export(fleet, machine)
        if observed < process_started.replace(microsecond=0):
            raise ValueError("The export did not publish fresh usage metadata")
    else:
        today = date.today()
        week = today - timedelta(days=today.weekday() + 7)
        path = fleet / "reports" / f"improvement-week-{week}.md"
        content = path.read_text()
        if not content.startswith(f"# Continuous improvement · {week} to "):
            raise ValueError("The expected weekly report did not pass readback")


def wait_for_exit(target):
    until = time.monotonic() + 60
    while time.monotonic() < until:
        current = installed(target)
        if current.returncode != 0:
            raise ValueError("The named job became unloaded")
        runs = re.search(r"(?m)^\s*runs = (\d+)$", current.stdout)
        status = re.search(r"(?m)^\s*last exit code = (-?\d+)", current.stdout)
        state = re.search(r"(?m)^\s*state = (.*)$", current.stdout)
        if runs and int(runs[1]) > 0 and status and state and state[1] != "running":
            if int(status[1]) == 0:
                return
            raise ValueError(f"Named job exited {status[1]}")
        time.sleep(1)
    raise ValueError("Named job did not complete within 60 seconds")


def launch_and_check(args, machine, fleet, spec, domain, path, target, data):
    started = datetime.now(timezone.utc)
    atomic_write(path, data)
    call(LAUNCHCTL, "bootstrap", domain, str(path))
    if args.job == "weekly":
        call(LAUNCHCTL, "kickstart", target)
    wait_for_exit(target)
    output_check(args, machine, fleet, started)
    current = installed(target)
    if (
        current.returncode != 0
        or not loaded_from(path, current.stdout)
        or loaded_arguments(current.stdout) != spec["ProgramArguments"]
    ):
        raise ValueError("The loaded job does not match the verified specification")


def require_unloaded(target):
    current = installed(target)
    if current.returncode == 0 or "Could not find service" not in current.stderr:
        raise ValueError("Could not verify that the named exporter is unloaded")


def migrate_legacy(
    args, machine, fleet, spec, domain, path, target, data, previous, log_dir
):
    try:
        summary, last_good, observed = verified_export(fleet, machine)
        initial = summary.stat()
    except (OSError, TypeError, ValueError, KeyError, AttributeError) as error:
        raise ValueError(
            "Publish and verify a last good summary before migration"
        ) from error
    recovery = Path(tempfile.mkdtemp(prefix="legacy-export-", dir=log_dir))
    atomic_write(recovery / "last-good-summary.json", last_good)
    previous_args = None
    if previous is not None:
        if path.is_symlink() or path.read_bytes() != previous:
            raise ValueError("The legacy plist changed during migration preflight")
        current = installed(target)
        if not check_owner(path, previous, spec, current, migration=(machine, fleet)):
            raise ValueError("The legacy job changed during migration preflight")
        previous_args = plistlib.loads(previous)["ProgramArguments"]
        # Remove the launchable plist before unloading the old job.
        os.replace(path, recovery / "legacy-export.plist")
    try:
        if previous_args is not None:
            call(LAUNCHCTL, "bootout", target)
        require_unloaded(target)
        launch_and_check(args, machine, fleet, spec, domain, path, target, data)
        after = summary.stat()
        if (after.st_ino, after.st_mtime_ns) == (initial.st_ino, initial.st_mtime_ns):
            raise ValueError("The replacement did not write a summary")
    except (
        OSError,
        TypeError,
        ValueError,
        KeyError,
        AttributeError,
        KeyboardInterrupt,
        subprocess.SubprocessError,
    ) as error:
        current = installed(target)
        if current.returncode == 0:
            if not loaded_from(path, current.stdout) or loaded_arguments(
                current.stdout
            ) not in (previous_args, spec["ProgramArguments"]):
                raise ValueError(
                    f"Exporter ownership changed. Recovery files are in {recovery}"
                ) from error
            call(LAUNCHCTL, "bootout", target, check=False)
        try:
            require_unloaded(target)
        except ValueError as verification:
            raise ValueError(
                f"{verification}. Recovery files are in {recovery}"
            ) from error
        if path.exists() or path.is_symlink():
            if path.is_symlink() or path.read_bytes() != data:
                raise ValueError(
                    f"The active plist changed. Recovery files are in {recovery}"
                ) from error
            os.replace(path, recovery / "failed-export.plist")
        try:
            _, _, latest = verified_export(fleet, machine)
        except (OSError, TypeError, ValueError, KeyError, AttributeError):
            latest = None
        if latest is None or latest < observed:
            if any(
                item.is_symlink()
                for item in (summary, summary.parent, summary.parent.parent)
            ):
                raise ValueError(
                    f"The summary path changed. Recovery files are in {recovery}"
                ) from error
            atomic_write(summary, last_good)
        raise ValueError(
            f"Metadata migration failed. The legacy exporter remains unloaded. "
            f"Verified summary retained. Recovery files are in {recovery}"
        ) from error
    print(f"{spec['Label']} loaded. First run passed. Recovery files are in {recovery}")


def activate(args, machine, fleet, spec):
    name = call("/usr/sbin/scutil", "--get", "LocalHostName").stdout.strip()
    hardware = json.loads(
        call(
            "/usr/sbin/system_profiler",
            "-detailLevel",
            "mini",
            "-json",
            "SPHardwareDataType",
        ).stdout
    )["SPHardwareDataType"][0]
    if (
        not name
        or name != os.environ.get("CCUSAGE_VERIFIED_HOSTNAME")
        or (hardware["machine_name"], hardware["chip_type"]) != HARDWARE[machine]
    ):
        raise ValueError(
            "Verify the local host name and hardware for the selected fleet ID"
        )
    if args.migrate_legacy_export and machine == "mac-mini-m6":
        raise ValueError("Legacy export migration is limited to the M4 and Air")
    if fleet.resolve() != CLOUD.resolve():
        raise ValueError(
            "Host identity or iCloud fleet root does not match the named target"
        )
    if args.job == "weekly" and not str(Path("/etc/localtime").resolve()).endswith(
        "/America/Los_Angeles"
    ):
        raise ValueError("The reporting Mac is not on Pacific local time")
    if args.job == "weekly" and not args.schedule_qualified:
        raise ValueError(
            "Verify the existing weekly job or schedule capacity before activation"
        )
    if args.job == "weekly" and not os.environ.get("CCUSAGE_IMPROVEMENT_PRS"):
        raise ValueError("Provide the verified GitHub evidence list before activation")
    user = os.getuid()
    if user == 0 or Path("/dev/console").stat().st_uid != user:
        raise ValueError("Use the owner’s logged-in GUI session")
    domain = f"gui/{user}"
    label = spec["Label"]
    assert label in AGENTS
    path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    log_dir = Path(spec["StandardOutPath"]).parent
    if path.parent.is_symlink() or log_dir.is_symlink():
        raise ValueError("Refused symlink for the local installation")
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = log_dir.stat()
    if info.st_uid != user or info.st_mode & 0o022:
        raise ValueError("Installation log directory has unverified ownership")
    lock = log_dir / f"{label}.install.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "Another installation of this named job is running"
            ) from error
        install_checked_job(args, machine, fleet, spec, domain, path, log_dir)


def install_checked_job(args, machine, fleet, spec, domain, path, log_dir):
    label = spec["Label"]
    target = f"{domain}/{label}"
    disabled = call(LAUNCHCTL, "print-disabled", domain).stdout
    if re.search(rf'"{re.escape(label)}"\s*=>\s*(true|disabled)\b', disabled):
        raise ValueError("Named job is intentionally disabled")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Refused unexpected existing plist path")
    previous = path.read_bytes() if path.exists() else None
    current = installed(target)
    was_loaded = current.returncode == 0
    legacy = check_owner(
        path,
        previous,
        spec,
        current,
        migration=(machine, fleet) if args.migrate_legacy_export else None,
    )
    if previous and plistlib.loads(previous).get("Disabled") is True:
        raise ValueError("Named job plist is intentionally disabled")
    if previous and not was_loaded:
        raise ValueError(
            "Existing inactive plist requires an ownership and disablement decision"
        )
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = plistlib.dumps(spec, sort_keys=False)
    if legacy or (args.migrate_legacy_export and previous is None):
        migrate_legacy(
            args, machine, fleet, spec, domain, path, target, data, previous, log_dir
        )
        return
    if previous == data and was_loaded:
        print(
            f"{label} already loaded with the requested configuration. "
            "Verify a named manual run and output freshness before relying on it."
        )
        return
    before = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if previous:
        kept = log_dir / f"{label}.{before}.prior.plist"
        with kept.open("xb") as stream:
            stream.write(previous)
        kept.chmod(0o600)
    if was_loaded:
        call(LAUNCHCTL, "bootout", target)
    try:
        launch_and_check(args, machine, fleet, spec, domain, path, target, data)
    except (
        OSError,
        TypeError,
        ValueError,
        KeyError,
        AttributeError,
        subprocess.SubprocessError,
    ) as error:
        if installed(target).returncode == 0:
            call(LAUNCHCTL, "bootout", target)
        if previous is None:
            failed = log_dir / f"{label}.{before}.failed.plist"
            if path.exists():
                os.replace(path, failed)
        else:
            atomic_write(path, previous)
            if was_loaded:
                call(LAUNCHCTL, "bootstrap", domain, str(path))
                if (
                    loaded_arguments(installed(target).stdout)
                    != plistlib.loads(previous)["ProgramArguments"]
                ):
                    raise ValueError(
                        "Prior plist restored but its loaded state requires verification"
                    )
        raise ValueError(
            "Named job failed health check. Prior configuration restored."
        ) from error
    print(f"{label} loaded. First run passed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=("export", "weekly"))
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--schedule-qualified", action="store_true")
    parser.add_argument(
        "--migrate-legacy-export",
        action="store_true",
        help="park the verified legacy exporter and preserve its last verified summary on failure",
    )
    args = parser.parse_args()
    if args.migrate_legacy_export and args.job != "export":
        parser.error("--migrate-legacy-export requires the export job")
    try:
        machine, fleet, spec = specification(args)
        if args.render:
            print(plistlib.dumps(spec, sort_keys=False).decode(), end="")
        else:
            os.umask(0o077)
            activate(args, machine, fleet, spec)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Could not qualify the named ccusage job: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
