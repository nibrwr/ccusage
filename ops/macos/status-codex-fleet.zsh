#!/usr/bin/env zsh
set -euo pipefail

fleet_root="${CCUSAGE_FLEET_ROOT:-$HOME/Library/Application Support/ccusage-fleet}"
machine_id="${CCUSAGE_MACHINE_ID:-${HOST%%.*}}"
label="com.nibrwr.ccusage-codex-export"
plist="$HOME/Library/LaunchAgents/$label.plist"
domain="gui/$(id -u)"

print "Machine: $machine_id"
print "Fleet root: $fleet_root"
print "LaunchAgent: $plist"

if [[ -f "$plist" ]]; then
  interval="$(sed -n '/<key>StartInterval<\/key>/{n;s/.*<integer>\(.*\)<\/integer>.*/\1/p;}' "$plist" | head -n 1)"
  [[ -n "$interval" ]] && print "Interval: ${interval}s"
else
  print "LaunchAgent plist is not installed."
fi

if launchctl print "$domain/$label" >/dev/null 2>&1; then
  print "LaunchAgent status: loaded"
else
  print "LaunchAgent status: not loaded"
fi

root="$fleet_root/codex"
if [[ ! -d "$root" ]]; then
  print "No exported Codex logs found under: $root"
  exit 0
fi

print ""
print "Exported machines:"
for machine_dir in "$root"/*(N/); do
  machine="${machine_dir:t}"
  files=("$machine_dir"/sessions/**/*.jsonl(.N) "$machine_dir"/archived_sessions/**/*.jsonl(.N))

  if [[ ${#files[@]} -eq 0 ]]; then
    print "  $machine: no files"
    continue
  fi

  newest_epoch=0
  newest_path=""
  for file in "${files[@]}"; do
    modified="$(stat -f '%m' "$file")"
    if (( modified > newest_epoch )); then
      newest_epoch="$modified"
      newest_path="$file"
    fi
  done

  newest_time="$(date -r "$newest_epoch" '+%Y-%m-%d %H:%M:%S %Z')"
  if [[ -f "$machine_dir/.last-export" ]]; then
    exported_time="$(tail -n 1 "$machine_dir/.last-export")"
  else
    exported_time="unknown (legacy snapshot)"
  fi
  print "  $machine:"
  print "    Last successful export: $exported_time"
  print "    Newest data file: $newest_time  ${newest_path#$machine_dir/}"
done
