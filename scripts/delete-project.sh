#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: scripts/delete-project.sh <project> [--force]
EOF
  exit "${1:-1}"
}

PROJECT=""
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --force) FORCE=1; shift ;;
    --) shift; break ;;
    -*)
      echo "error: unknown flag: $1" >&2
      usage 2 ;;
    *)
      if [[ -z "$PROJECT" ]]; then
        PROJECT="$1"
      else
        echo "error: unexpected positional argument: $1" >&2
        usage 2
      fi
      shift ;;
  esac
done

[[ -n "$PROJECT" ]] || usage 2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

args=("$SCRIPT_DIR/../core/scripts/hard_delete.py" project "$PROJECT")
if [[ "$FORCE" == "1" ]]; then
  args+=(--force)
fi

exec "$PYTHON_BIN" "${args[@]}"
