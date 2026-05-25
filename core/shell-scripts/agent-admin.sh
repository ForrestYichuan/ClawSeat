#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  exec "$PYTHON_BIN" "$REPO_ROOT/core/scripts/agent_admin.py" "$@"
fi

if command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.12)"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.11)"
elif [[ -x /opt/homebrew/bin/python3.12 ]]; then
  PYTHON_BIN=/opt/homebrew/bin/python3.12
elif [[ -x /opt/homebrew/bin/python3.11 ]]; then
  PYTHON_BIN=/opt/homebrew/bin/python3.11
elif [[ -x /usr/local/bin/python3.12 ]]; then
  PYTHON_BIN=/usr/local/bin/python3.12
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "python3 is required for agent-admin" >&2
  exit 1
fi

exec "$PYTHON_BIN" "$REPO_ROOT/core/scripts/agent_admin.py" "$@"
