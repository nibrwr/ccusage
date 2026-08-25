#!/usr/bin/env zsh
set -euo pipefail

machine_id="${CCUSAGE_MACHINE_ID:-${HOST%%.*}}"
fleet_root="${CCUSAGE_FLEET_ROOT:-$HOME/Library/Application Support/ccusage-fleet}"
codex_home="${CODEX_HOME:-$HOME/.codex}"
lock_dir="${TMPDIR:-/tmp}/ccusage-codex-export-$machine_id.lock"

if ! mkdir "$lock_dir" 2>/dev/null; then
  print "Codex export already running for $machine_id; skipping this run."
  exit 0
fi
trap 'rmdir "$lock_dir"' EXIT INT TERM

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
    for source_file in "$codex_home/$name"/**/*(.N); do
      relative_path="${source_file#$codex_home/$name/}"
      target_file="$dest/$name/$relative_path"
      source_size="$(stat -f '%z' "$source_file")"
      target_size="$(stat -f '%z' "$target_file" 2>/dev/null || print -1)"

      if [[ ! -f "$target_file" ]]; then
        mkdir -p "${target_file:h}"
        cp "$source_file" "$target_file"
      elif (( source_size > target_size )); then
        tail -c "+$((target_size + 1))" "$source_file" >> "$target_file"
      elif (( source_size < target_size )); then
        print -u2 "Exported file is larger than its source: $target_file"
        print -u2 "Run this exporter manually from Terminal to repair the snapshot."
        exit 1
      fi
    done
  fi
done

if [[ -f "$codex_home/config.toml" && ! -f "$dest/config.toml" ]]; then
  cp "$codex_home/config.toml" "$dest/config.toml"
fi

date '+%Y-%m-%d %H:%M:%S %Z' >> "$dest/.last-export"
print "Exported Codex logs for $machine_id to $dest"
