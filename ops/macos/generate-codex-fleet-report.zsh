#!/usr/bin/env zsh
set -euo pipefail

kind="${1:-monthly}"
script_dir="${0:A:h}"
repo_root="${script_dir:h:h}"
fleet_root="${CCUSAGE_FLEET_ROOT:-$HOME/Library/Application Support/ccusage-fleet}"
report_dir="${CCUSAGE_REPORT_DIR:-$fleet_root/reports}"
timestamp="$(date '+%Y-%m-%d_%H-%M-%S')"

case "$kind" in
  weekly | monthly) ;;
  *)
    print -u2 "usage: $0 [weekly|monthly] [ccusage options...]"
    exit 2
    ;;
esac
shift || true

mkdir -p "$report_dir"

out="$report_dir/codex-fleet-by-machine-$kind-$timestamp.txt"
latest="$report_dir/codex-fleet-by-machine-$kind-latest.txt"

{
  print "ccusage Codex fleet by-machine $kind report"
  print "Generated: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  print "Fleet root: $fleet_root"
  print ""
  "$script_dir/report-codex-fleet-by-machine.zsh" --no-refresh "$kind" "$@"
} > "$out"

cp "$out" "$latest"

if [[ "${CCUSAGE_REPORT_NOTIFY:-1}" == "1" ]] && command -v osascript >/dev/null 2>&1; then
  osascript -e "display notification \"Saved to $out\" with title \"ccusage $kind report\""
fi

if [[ "${CCUSAGE_REPORT_OPEN:-0}" == "1" ]]; then
  open "$out"
fi

print "Wrote $out"
