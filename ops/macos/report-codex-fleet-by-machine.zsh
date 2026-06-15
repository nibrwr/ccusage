#!/usr/bin/env zsh
set -euo pipefail

fleet_root="${CCUSAGE_FLEET_ROOT:-$HOME/Library/Application Support/ccusage-fleet}"
ccusage_bin="${CCUSAGE_BIN:-ccusage}"
refresh_before_report="${CCUSAGE_REFRESH_BEFORE_REPORT:-0}"
kind="${1:-monthly}"

if [[ "$kind" == "-h" || "$kind" == "--help" ]]; then
  cat <<'EOF'
usage: report-codex-fleet-by-machine.zsh [--refresh|--no-refresh] [daily|monthly|session] [ccusage options...]

Examples:
  ops/macos/report-codex-fleet-by-machine.zsh monthly
  ops/macos/report-codex-fleet-by-machine.zsh --refresh monthly
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
  daily | monthly | session) shift || true ;;
  *)
    print -u2 "usage: $0 [daily|monthly|session] [ccusage options...]"
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
  CODEX_HOME="$home" "$ccusage_bin" codex "$kind" --json "${ccusage_args[@]}" > "$out"
  ruby_args+=("$machine" "$out")
done

ruby -r json - "${ruby_args[@]}" <<'RUBY'
kind = ARGV.shift
rows_key = { "daily" => "daily", "monthly" => "monthly", "session" => "sessions" }.fetch(kind)
period_key = { "daily" => "date", "monthly" => "month", "session" => "sessionId" }.fetch(kind)
period_label = { "daily" => "Date", "monthly" => "Month", "session" => "Session" }.fetch(kind)

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

ARGV.each_slice(2) do |machine, path|
  data = JSON.parse(File.read(path))
  data.fetch(rows_key, []).each do |row|
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
