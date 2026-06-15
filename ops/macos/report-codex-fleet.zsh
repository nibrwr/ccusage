#!/usr/bin/env zsh
set -euo pipefail

fleet_root="${CCUSAGE_FLEET_ROOT:-$HOME/Library/Application Support/ccusage-fleet}"
machine_id=""
ccusage_bin="${CCUSAGE_BIN:-ccusage}"
refresh_before_report="${CCUSAGE_REFRESH_BEFORE_REPORT:-0}"

if ! command -v "$ccusage_bin" >/dev/null 2>&1; then
  if [[ -x "$HOME/.cargo/bin/ccusage" ]]; then
    ccusage_bin="$HOME/.cargo/bin/ccusage"
  else
    print -u2 "ccusage was not found."
    print -u2 "Run ops/macos/install-ccusage.zsh, then open a new terminal or set CCUSAGE_BIN=/path/to/ccusage."
    exit 127
  fi
fi

while [[ "${1:-}" == "--refresh" || "${1:-}" == "--no-refresh" ]]; do
  case "$1" in
    --refresh) refresh_before_report=1 ;;
    --no-refresh) refresh_before_report=0 ;;
  esac
  shift
done

if [[ "$refresh_before_report" == "1" ]]; then
  if [[ -z "${CCUSAGE_MACHINE_ID:-}" ]]; then
    print -u2 "CCUSAGE_MACHINE_ID is required for automatic refresh."
    print -u2 "Source ops/macos/machines/<machine>.env first, or pass --no-refresh."
    exit 2
  fi
  "${0:A:h}/export-codex-logs.zsh"
fi

if [[ "${1:-}" == "--machine" ]]; then
  if [[ -z "${2:-}" ]]; then
    print -u2 "usage: $0 [--machine MACHINE_ID] [daily|monthly|session] [ccusage options...]"
    exit 2
  fi
  machine_id="$2"
  shift 2
fi

if [[ $# -eq 0 ]]; then
  set -- monthly
fi

homes=()
if [[ -n "$machine_id" ]]; then
  home="$fleet_root/codex/$machine_id"
  if [[ ! -d "$home" ]]; then
    print -u2 "No exported Codex logs found for machine: $machine_id"
    print -u2 "Expected directory: $home"
    exit 1
  fi
  homes+=("$home")
else
  for home in "$fleet_root"/codex/*(N/); do
    homes+=("$home")
  done
fi

if [[ ${#homes[@]} -eq 0 ]]; then
  print -u2 "No exported Codex logs found under: $fleet_root/codex"
  exit 1
fi

codex_home="${(j:,:)homes}"
CODEX_HOME="$codex_home" "$ccusage_bin" codex "$@"
