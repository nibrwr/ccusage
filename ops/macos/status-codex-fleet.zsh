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
for home in "$root"/*(N/); do
  machine="${home:t}"
  files=("$home"/sessions/**/*(.N) "$home"/archived_sessions/**/*(.N) "$home"/config.toml(.N))

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
  print "  $machine: $newest_time  ${newest_path#$home/}"
done
