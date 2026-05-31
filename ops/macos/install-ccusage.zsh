#!/usr/bin/env zsh
set -euo pipefail

repo_url="${CCUSAGE_REPO_URL:-https://github.com/nibrwr/ccusage.git}"
branch="${CCUSAGE_BRANCH:-main}"
script_dir="${0:A:h}"
repo_root="${script_dir:h:h}"

if ! command -v cargo >/dev/null 2>&1; then
  cat >&2 <<'EOF'
cargo was not found.

Install Rust first, then rerun this script:
  https://www.rust-lang.org/tools/install
EOF
  exit 1
fi

if [[ -f "$repo_root/rust/crates/ccusage/Cargo.toml" ]]; then
  cargo install --path "$repo_root/rust/crates/ccusage" --locked --force
else
  cargo install --git "$repo_url" --branch "$branch" --locked --force ccusage
fi

zshrc="${ZDOTDIR:-$HOME}/.zshrc"
path_line='export PATH="$HOME/.cargo/bin:$PATH"'
if [[ -f "$zshrc" ]]; then
  if ! grep -Fq '.cargo/bin' "$zshrc"; then
    printf '\n%s\n' "$path_line" >> "$zshrc"
    print "Added Cargo bin directory to $zshrc"
  fi
else
  printf '%s\n' "$path_line" > "$zshrc"
  print "Created $zshrc with Cargo bin directory on PATH"
fi

print "Installed: $("$HOME/.cargo/bin/ccusage" --version)"
