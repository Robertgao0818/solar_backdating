#!/usr/bin/env bash
# activate_env.sh — shared-venv plugin activator for solar_backdating.
#
# Sources main repo (ZAsolar) venv, then prepends this repo's paths to
# PYTHONPATH so in-repo `scripts.temporal.*` shadows any older copy in
# main repo. Resolves repo root from BASH_SOURCE, not $(pwd), so it works
# from any cwd.
#
# Usage (from any directory):
#   source /path/to/solar_backdating/scripts/activate_env.sh
#
# Override main-repo location:
#   ZASOLAR_ROOT=/workspace/ZAsolar source scripts/activate_env.sh

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "Use: source scripts/activate_env.sh"
  exit 1
fi

# Resolve subrepo root from script location (not $(pwd))
SOLAR_BACKDATING_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SOLAR_BACKDATING_ROOT

# Resolve main repo root (overridable)
ZASOLAR_ROOT="${ZASOLAR_ROOT:-/home/gaosh/projects/ZAsolar}"
export ZASOLAR_ROOT

if [[ ! -d "$ZASOLAR_ROOT" ]]; then
  echo "[solar_backdating] ZAsolar main repo not found at $ZASOLAR_ROOT" >&2
  echo "[solar_backdating] Set ZASOLAR_ROOT to override." >&2
  return 1
fi

if [[ ! -x "$ZASOLAR_ROOT/.venv/bin/python" ]]; then
  echo "[solar_backdating] ZAsolar venv not found at $ZASOLAR_ROOT/.venv" >&2
  echo "[solar_backdating] Run: cd $ZASOLAR_ROOT && ./scripts/bootstrap_env.sh" >&2
  return 1
fi

# Source main repo activator (handles VIRTUAL_ENV, PATH, XDG_*, etc.)
# But its PYTHONPATH only puts ZASOLAR_ROOT first; we override below.
source "$ZASOLAR_ROOT/scripts/activate_env.sh" >/dev/null

# Enforce subrepo-first PYTHONPATH order:
#   1. $SOLAR_BACKDATING_ROOT      → for `from scripts.temporal.geid_temporal_common import ...`
#   2. $SOLAR_BACKDATING_ROOT/src  → for `import solar_backdating`
#   3. $ZASOLAR_ROOT               → for `from core import ...`
# Main repo's activate_env.sh already added $ZASOLAR_ROOT; we prepend ours.
export PYTHONPATH="$SOLAR_BACKDATING_ROOT:$SOLAR_BACKDATING_ROOT/src:${PYTHONPATH}"

echo "[solar_backdating] activated"
echo "  SOLAR_BACKDATING_ROOT = $SOLAR_BACKDATING_ROOT"
echo "  ZASOLAR_ROOT          = $ZASOLAR_ROOT"
echo "  Python                = $(command -v python)"
