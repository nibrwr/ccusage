#!/usr/bin/env zsh
set -euo pipefail

script_dir="${0:A:h}"
fleet_root="${CCUSAGE_FLEET_ROOT:-$HOME/Library/Application Support/ccusage-fleet}"
report_dir="${CCUSAGE_REPORT_DIR:-$fleet_root/reports}"
hour="${CCUSAGE_REPORT_HOUR:-8}"
minute="${CCUSAGE_REPORT_MINUTE:-0}"
weekly_weekday="${CCUSAGE_WEEKLY_REPORT_WEEKDAY:-1}"
monthly_day="${CCUSAGE_MONTHLY_REPORT_DAY:-1}"
log_dir="$HOME/Library/Logs/ccusage"
domain="gui/$(id -u)"

install_report_job() {
  local kind="$1"
  local label="com.nibrwr.ccusage-codex-report-$kind"
  local plist="$HOME/Library/LaunchAgents/$label.plist"
  local calendar_key calendar_value

  case "$kind" in
    weekly)
      calendar_key="Weekday"
      calendar_value="$weekly_weekday"
      ;;
    monthly)
      calendar_key="Day"
      calendar_value="$monthly_day"
      ;;
    *)
      print -u2 "Unknown report kind: $kind"
      exit 2
      ;;
  esac

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
    <string>CCUSAGE_FLEET_ROOT='$fleet_root' CCUSAGE_REPORT_DIR='$report_dir' '$script_dir/generate-codex-fleet-report.zsh' '$kind'</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>$calendar_key</key>
    <integer>$calendar_value</integer>
    <key>Hour</key>
    <integer>$hour</integer>
    <key>Minute</key>
    <integer>$minute</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$log_dir/report-$kind.out.log</string>
  <key>StandardErrorPath</key>
  <string>$log_dir/report-$kind.err.log</string>
</dict>
</plist>
EOF

  launchctl bootout "$domain" "$plist" >/dev/null 2>&1 || true
  launchctl bootstrap "$domain" "$plist"
  print "Installed $kind report job: $label"
}

mkdir -p "$HOME/Library/LaunchAgents" "$log_dir" "$report_dir"

install_report_job weekly
install_report_job monthly

print "Fleet root: $fleet_root"
print "Report dir: $report_dir"
print "Weekly schedule: weekday $weekly_weekday at ${hour}:$(printf '%02d' "$minute")"
print "Monthly schedule: day $monthly_day at ${hour}:$(printf '%02d' "$minute")"
