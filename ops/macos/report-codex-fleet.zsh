#!/usr/bin/env zsh
set -euo pipefail

fleet_root="${CCUSAGE_FLEET_ROOT:-$HOME/Library/Application Support/ccusage-fleet}"
machine_id=""

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
CODEX_HOME="$codex_home" ccusage codex "$@"
