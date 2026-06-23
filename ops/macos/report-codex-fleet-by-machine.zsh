#!/usr/bin/env zsh
set -euo pipefail

fleet_root="${CCUSAGE_FLEET_ROOT:-$HOME/Library/Application Support/ccusage-fleet}"
ccusage_bin="${CCUSAGE_BIN:-ccusage}"
refresh_before_report="${CCUSAGE_REFRESH_BEFORE_REPORT:-0}"
kind="${1:-monthly}"

if [[ "$kind" == "-h" || "$kind" == "--help" ]]; then
  cat <<'EOF'
usage: report-codex-fleet-by-machine.zsh [--refresh|--no-refresh] [daily|weekly|monthly|session] [ccusage options...]

Examples:
  ops/macos/report-codex-fleet-by-machine.zsh monthly
  ops/macos/report-codex-fleet-by-machine.zsh --refresh monthly
  ops/macos/report-codex-fleet-by-machine.zsh weekly
  ops/macos/report-codex-fleet-by-machine.zsh daily --since 2026-05-01
  ops/macos/report-codex-fleet-by-machine.zsh monthly --offline
EOF
  exit 0
fi

while [[ "${1:-}" == "--refresh" || "${1:-}" == "--no-refresh" ]]; do
  case "$1" in
    --refresh) refresh_before_report=1 ;;
    --no-refresh) refresh_before_report=0 ;;
  esac
  shift
done

kind="${1:-monthly}"

case "$kind" in
  daily | weekly | monthly | session) shift || true ;;
  *)
    print -u2 "usage: $0 [daily|weekly|monthly|session] [ccusage options...]"
    exit 2
    ;;
esac

if ! command -v "$ccusage_bin" >/dev/null 2>&1; then
  if [[ -x "$HOME/.cargo/bin/ccusage" ]]; then
    ccusage_bin="$HOME/.cargo/bin/ccusage"
  else
    print -u2 "ccusage was not found."
    print -u2 "Run ops/macos/install-ccusage.zsh, then open a new terminal or set CCUSAGE_BIN=/path/to/ccusage."
    exit 127
  fi
fi

if [[ "$refresh_before_report" == "1" ]]; then
  if [[ -z "${CCUSAGE_MACHINE_ID:-}" ]]; then
    print -u2 "CCUSAGE_MACHINE_ID is required for automatic refresh."
    print -u2 "Source ops/macos/machines/<machine>.env first, or pass --no-refresh."
    exit 2
  fi
  "${0:A:h}/export-codex-logs.zsh"
fi

homes=()
for home in "$fleet_root"/codex/*(N/); do
  homes+=("$home")
done

if [[ ${#homes[@]} -eq 0 ]]; then
  print -u2 "No exported Codex logs found under: $fleet_root/codex"
  exit 1
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ccusage-fleet-by-machine.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT

ccusage_args=()
for arg in "$@"; do
  [[ "$arg" == "--json" ]] && continue
  ccusage_args+=("$arg")
done

ruby_args=("$kind")
for home in "${homes[@]}"; do
  machine="${home:t}"
  out="$tmp_dir/$machine.json"
  print -u2 "Reading $machine..."
  ccusage_kind="$kind"
  if [[ "$kind" == "weekly" ]]; then
    ccusage_kind="daily"
  fi
  CODEX_HOME="$home" "$ccusage_bin" codex "$ccusage_kind" --json "${ccusage_args[@]}" > "$out"
  ruby_args+=("$machine" "$out")
done

ruby -r json -r date - "${ruby_args[@]}" <<'RUBY'
kind = ARGV.shift
rows_key = { "daily" => "daily", "weekly" => "daily", "monthly" => "monthly", "session" => "sessions" }.fetch(kind)
period_key = { "daily" => "date", "weekly" => "week", "monthly" => "month", "session" => "sessionId" }.fetch(kind)
period_label = { "daily" => "Date", "weekly" => "Week", "monthly" => "Month", "session" => "Session" }.fetch(kind)

def comma(value)
  value.to_i.to_s.reverse.gsub(/(\d{3})(?=\d)/, '\\1,').reverse
end

def money(value)
  format("$%.2f", value.to_f)
end

def shorten(value, max)
  text = value.to_s
  return text if text.length <= max

  "#{text[0, max - 3]}..."
end

table = []
fleet = Hash.new(0)

def weekly_rows(rows)
  grouped = Hash.new do |hash, key|
    hash[key] = {
      "week" => key,
      "inputTokens" => 0,
      "outputTokens" => 0,
      "cachedInputTokens" => 0,
      "totalTokens" => 0,
      "costUSD" => 0.0,
    }
  end

  rows.each do |row|
    date = Date.iso8601(row.fetch("date"))
    week = (date - (date.cwday - 1)).iso8601
    group = grouped[week]
    group["inputTokens"] += row.fetch("inputTokens")
    group["outputTokens"] += row.fetch("outputTokens")
    group["cachedInputTokens"] += row.fetch("cachedInputTokens")
    group["totalTokens"] += row.fetch("totalTokens")
    group["costUSD"] += row.fetch("costUSD")
  end

  grouped.values.sort_by { |row| row.fetch("week") }
end

ARGV.each_slice(2) do |machine, path|
  data = JSON.parse(File.read(path))
  rows = data.fetch(rows_key, [])
  rows = weekly_rows(rows) if kind == "weekly"

  rows.each do |row|
    table << [
      machine,
      shorten(row.fetch(period_key), kind == "session" ? 36 : 10),
      comma(row.fetch("inputTokens")),
      comma(row.fetch("outputTokens")),
      comma(row.fetch("cachedInputTokens")),
      comma(row.fetch("totalTokens")),
      money(row.fetch("costUSD")),
    ]
  end

  totals = data.fetch("totals")
  table << [
    machine,
    "Total",
    comma(totals.fetch("inputTokens")),
    comma(totals.fetch("outputTokens")),
    comma(totals.fetch("cachedInputTokens")),
    comma(totals.fetch("totalTokens")),
    money(totals.fetch("costUSD")),
  ]

  fleet[:input] += totals.fetch("inputTokens")
  fleet[:output] += totals.fetch("outputTokens")
  fleet[:cached] += totals.fetch("cachedInputTokens")
  fleet[:total] += totals.fetch("totalTokens")
  fleet[:cost] += totals.fetch("costUSD")
end

table << [
  "Fleet",
  "Total",
  comma(fleet[:input]),
  comma(fleet[:output]),
  comma(fleet[:cached]),
  comma(fleet[:total]),
  money(fleet[:cost]),
]

headers = ["Machine", period_label, "Input", "Output", "Cache Read", "Total Tokens", "Cost"]
widths = headers.each_index.map do |index|
  ([headers[index]] + table.map { |row| row[index] }).map(&:length).max
end

separator = "+-" + widths.map { |width| "-" * width }.join("-+-") + "-+"
puts separator
puts "| " + headers.each_with_index.map { |header, index| header.ljust(widths[index]) }.join(" | ") + " |"
puts separator
table.each do |row|
  puts "| " + row.each_with_index.map { |cell, index|
    if index >= 2
      cell.rjust(widths[index])
    else
      cell.ljust(widths[index])
    end
  }.join(" | ") + " |"
end
puts separator
RUBY
