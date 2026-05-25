#!/usr/bin/env bash
# restart-project.sh — restart every materialized seat in a ClawSeat project.
#
# Usage:
#   scripts/restart-project.sh <project> [--no-window]
#
# This intentionally delegates each seat restart to restart-seat.sh so provider
# SSOT, launcher auth translation, and custom env overlays stay in one place.

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: scripts/restart-project.sh <project> [--no-window]

Options:
  --no-window     Skip iTerm grid refresh; only restart tmux sessions.
  -h, --help      Show this help.
EOF
  exit "${1:-1}"
}

PROJECT=""
NO_WINDOW=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --no-window) NO_WINDOW=1; shift ;;
    --) shift; break ;;
    -*)
      echo "error: unknown flag: $1" >&2
      usage 2
      ;;
    *)
      if [[ -z "$PROJECT" ]]; then
        PROJECT="$1"
      else
        echo "error: unexpected positional argument: $1" >&2
        usage 2
      fi
      shift
      ;;
  esac
done

[[ -n "$PROJECT" ]] || usage 2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_ADMIN="$REPO_ROOT/core/scripts/agent_admin.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RESTART_SEAT_SCRIPT="${RESTART_SEAT_SCRIPT:-$SCRIPT_DIR/restart-seat.sh}"

if [[ ! -x "$RESTART_SEAT_SCRIPT" && ! -f "$RESTART_SEAT_SCRIPT" ]]; then
  echo "error: restart-seat.sh not found: $RESTART_SEAT_SCRIPT" >&2
  exit 3
fi

if ! plan_out="$(
  "$PYTHON_BIN" - "$REPO_ROOT" "$PROJECT" 2>&1 <<'PY'
import shlex
import sys

repo_root, project_name = sys.argv[1:3]
sys.path.insert(0, f"{repo_root}/core/scripts")
sys.path.insert(0, f"{repo_root}/core/lib")

try:
    import agent_admin  # type: ignore

    project = agent_admin.load_project_or_current(project_name)
    sessions = agent_admin.load_project_sessions(project.name)
    seats = [seat for seat in project.engineers if seat in sessions]
    if not seats:
        print(f"error: project {project.name!r} has no materialized seat sessions", file=sys.stderr)
        raise SystemExit(1)
    print(f"PROJECT_NAME={shlex.quote(project.name)}")
    for seat in seats:
        print(f"SEAT={shlex.quote(seat)}")
except Exception as exc:  # noqa: BLE001 - shell caller needs one sanitized line.
    print(f"error: agent_admin restart project plan failed for {project_name}: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
)"; then
  echo "error: agent_admin project restart plan failed for $PROJECT:" >&2
  printf '%s\n' "$plan_out" >&2
  exit 1
fi

PROJECT_NAME=""
SEATS=()
while IFS= read -r line; do
  case "$line" in
    PROJECT_NAME=*)
      eval "$line"
      ;;
    SEAT=*)
      eval "$line"
      SEATS+=("$SEAT")
      ;;
  esac
done <<< "$plan_out"

if [[ -z "$PROJECT_NAME" || "${#SEATS[@]}" -eq 0 ]]; then
  echo "error: incomplete project restart plan:" >&2
  printf '%s\n' "$plan_out" >&2
  exit 1
fi

printf 'restart-project:\n  project: %s\n  seats:   %s\n' "$PROJECT_NAME" "${SEATS[*]}"

failures=()
for seat in "${SEATS[@]}"; do
  echo "restart-project: restarting $PROJECT_NAME/$seat"
  if ! seat_out="$(bash "$RESTART_SEAT_SCRIPT" "$PROJECT_NAME" "$seat" --no-window 2>&1)"; then
    failures+=("$seat")
    printf '%s\n' "$seat_out" >&2
    continue
  fi
  printf '%s\n' "$seat_out"
done

if [[ "${#failures[@]}" -gt 0 ]]; then
  printf 'error: failed to restart %s seat(s): %s\n' "${#failures[@]}" "${failures[*]}" >&2
  exit 1
fi

if [[ "$NO_WINDOW" == "0" ]]; then
  if ! "$PYTHON_BIN" "$AGENT_ADMIN" window open-grid "$PROJECT_NAME" --quiet 2>&1; then
    echo "warn: window open-grid $PROJECT_NAME failed; tmux sessions are alive" >&2
  fi
fi

echo "done."
