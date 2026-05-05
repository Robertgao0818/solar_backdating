#!/usr/bin/env bash
# link_data_dirs.sh — bind in-repo data/ symlinks to ~/zasolar_data/.
#
# This repo keeps real data outside the git tree (in ~/zasolar_data/) and
# uses in-repo symlinks for ergonomics. The symlinks themselves are
# gitignored. Run this once after cloning.
#
# Override the data root with SOLAR_DATA_ROOT.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOLAR_DATA_ROOT="${SOLAR_DATA_ROOT:-$HOME/zasolar_data}"

if [[ ! -d "$SOLAR_DATA_ROOT" ]]; then
  echo "[link_data_dirs] $SOLAR_DATA_ROOT does not exist" >&2
  echo "[link_data_dirs] Create it or set SOLAR_DATA_ROOT to your data root." >&2
  exit 1
fi

link_one() {
  local rel_path="$1"
  local target="$SOLAR_DATA_ROOT/$rel_path"
  local link_path="$REPO_ROOT/data/$rel_path"

  mkdir -p "$target"

  if [[ -L "$link_path" ]]; then
    echo "[link_data_dirs] $link_path already a symlink → $(readlink "$link_path")"
    return 0
  fi
  if [[ -e "$link_path" ]]; then
    echo "[link_data_dirs] $link_path exists and is not a symlink — refusing to overwrite" >&2
    return 1
  fi

  ln -s "$target" "$link_path"
  echo "[link_data_dirs] linked $link_path → $target"
}

link_one geid_temporal
link_one geid_vintage_probe

echo "[link_data_dirs] done"
