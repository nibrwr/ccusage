#!/usr/bin/env zsh
set -euo pipefail

machine_id="${CCUSAGE_MACHINE_ID:-${HOST%%.*}}"
fleet_root="${CCUSAGE_FLEET_ROOT:-$HOME/Library/Application Support/ccusage-fleet}"
codex_home="${CODEX_HOME:-$HOME/.codex}"

if [[ "$codex_home" == *","* ]]; then
  print -u2 "CODEX_HOME contains multiple paths. Set it to this Mac's local Codex home before exporting."
  exit 1
fi

if [[ ! -d "$codex_home" ]]; then
  print -u2 "Codex home does not exist: $codex_home"
  exit 1
fi

dest="$fleet_root/codex/$machine_id"
mkdir -p "$dest"

for name in sessions archived_sessions; do
  if [[ -d "$codex_home/$name" ]]; then
    mkdir -p "$dest/$name"
    rsync -a --delete "$codex_home/$name/" "$dest/$name/"
  fi
done

if [[ -f "$codex_home/config.toml" ]]; then
  cp "$codex_home/config.toml" "$dest/config.toml"
fi

print "Exported Codex logs for $machine_id to $dest"

