#!/usr/bin/env zsh
set -euo pipefail

kind="${1:-monthly}"
script_dir="${0:A:h}"
repo_root="${script_dir:h:h}"
fleet_root="${CCUSAGE_FLEET_ROOT:-$HOME/Library/Application Support/ccusage-fleet}"
report_dir="${CCUSAGE_REPORT_DIR:-$fleet_root/reports}"
refresh_before_report="${CCUSAGE_REFRESH_BEFORE_REPORT:-0}"
timestamp="$(date '+%Y-%m-%d_%H-%M-%S')"
archive_period="$(date '+%Y-%m')"
archive_dir="$report_dir/$archive_period"

case "$kind" in
  weekly | monthly) ;;
  *)
    print -u2 "usage: $0 [weekly|monthly] [ccusage options...]"
    exit 2
    ;;
esac
shift || true

if [[ "$refresh_before_report" == "1" ]]; then
  if [[ -z "${CCUSAGE_MACHINE_ID:-}" ]]; then
    print -u2 "CCUSAGE_MACHINE_ID is required for automatic refresh."
    print -u2 "Source ops/macos/machines/<machine>.env first."
    exit 2
  fi
  "$script_dir/export-codex-logs.zsh"
fi

mkdir -p "$report_dir" "$archive_dir"

for existing_report in "$report_dir"/codex-fleet-by-machine-*-20??-??-??_??-??-??.txt(N); do
  existing_name="${existing_report:t}"
  if [[ "$existing_name" =~ '^codex-fleet-by-machine-(weekly|monthly)-([0-9]{4}-[0-9]{2})-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}\.txt$' ]]; then
    existing_archive_dir="$report_dir/$match[2]"
    mkdir -p "$existing_archive_dir"
    mv "$existing_report" "$existing_archive_dir/$existing_name"
  fi
done

out="$archive_dir/codex-fleet-by-machine-$kind-$timestamp.txt"
latest="$report_dir/codex-fleet-by-machine-$kind-latest.txt"
tmp="$report_dir/.codex-fleet-by-machine-$kind-$timestamp.tmp"
trap 'rm -f "$tmp"' EXIT INT TERM

{
  print "ccusage Codex fleet by-machine $kind report"
  print "Generated: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  print "Fleet root: $fleet_root"
  print "Machine snapshots:"
  for machine_dir in "$fleet_root"/codex/*(N/); do
    machine="${machine_dir:t}"
    files=("$machine_dir"/sessions/**/*.jsonl(.N) "$machine_dir"/archived_sessions/**/*.jsonl(.N))
    newest_epoch=0
    for file in "${files[@]}"; do
      modified="$(stat -f '%m' "$file")"
      (( modified > newest_epoch )) && newest_epoch="$modified"
    done

    if [[ -f "$machine_dir/.last-export" ]]; then
      exported_time="$(tail -n 1 "$machine_dir/.last-export")"
    else
      exported_time="unknown (legacy snapshot)"
    fi
    if (( newest_epoch > 0 )); then
      newest_time="$(date -r "$newest_epoch" '+%Y-%m-%d %H:%M:%S %Z')"
    else
      newest_time="no data"
    fi
    print "  $machine: exported $exported_time; newest data file $newest_time"
  done
  print ""
  "$script_dir/report-codex-fleet-by-machine.zsh" --no-refresh "$kind" "$@"
} > "$tmp"

mv "$tmp" "$out"
cp "$out" "$latest"

if [[ "${CCUSAGE_REPORT_NOTIFY:-1}" == "1" ]] && command -v osascript >/dev/null 2>&1; then
  osascript -e "display notification \"Saved to $out\" with title \"ccusage $kind report\""
fi

if [[ "${CCUSAGE_REPORT_OPEN:-0}" == "1" ]]; then
  open "$out"
fi

print "Wrote $out"
