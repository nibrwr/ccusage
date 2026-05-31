#!/usr/bin/env zsh
set -euo pipefail

script_dir="${0:A:h}"
machine_id="${CCUSAGE_MACHINE_ID:-${HOST%%.*}}"
fleet_root="${CCUSAGE_FLEET_ROOT:-$HOME/Library/Application Support/ccusage-fleet}"
interval="${CCUSAGE_EXPORT_INTERVAL_SECONDS:-900}"
label="com.nibrwr.ccusage-codex-export"
plist="$HOME/Library/LaunchAgents/$label.plist"
log_dir="$HOME/Library/Logs/ccusage"

mkdir -p "$HOME/Library/LaunchAgents" "$log_dir"

cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>CCUSAGE_MACHINE_ID='$machine_id' CCUSAGE_FLEET_ROOT='$fleet_root' '$script_dir/export-codex-logs.zsh'</string>
  </array>
  <key>StartInterval</key>
  <integer>$interval</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$log_dir/export-codex-logs.out.log</string>
  <key>StandardErrorPath</key>
  <string>$log_dir/export-codex-logs.err.log</string>
</dict>
</plist>
EOF

launchctl unload "$plist" >/dev/null 2>&1 || true
launchctl load "$plist"

print "Installed launchd exporter for $machine_id"
print "Fleet root: $fleet_root"
print "Interval: ${interval}s"
