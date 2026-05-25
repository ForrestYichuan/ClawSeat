#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DEFAULT_AGENT_HOME="$HOME"

# Many seat runtimes execute inside isolated identity homes where `~/.agents`
# does not point at the shared engineer/session records. Normalize HOME back to
# the shared agent root when needed so transport/status helpers resolve the
# correct project sessions.
if [ -n "${AGENT_HOME:-}" ]; then
  export HOME="$AGENT_HOME"
elif [ ! -d "${HOME:-}/.agents" ] && [ -d "$DEFAULT_AGENT_HOME/.agents" ]; then
  export HOME="$DEFAULT_AGENT_HOME"
fi

if [ -n "${PYTHON_BIN:-}" ]; then
  :
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.12)"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.11)"
elif [ -x /opt/homebrew/bin/python3.12 ]; then
  PYTHON_BIN=/opt/homebrew/bin/python3.12
elif [ -x /opt/homebrew/bin/python3.11 ]; then
  PYTHON_BIN=/opt/homebrew/bin/python3.11
elif [ -x /usr/local/bin/python3.12 ]; then
  PYTHON_BIN=/usr/local/bin/python3.12
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "python3 is required for agentctl" >&2
  exit 1
fi

exec "$PYTHON_BIN" "$REPO_ROOT/core/scripts/agentctl.py" "$@"
