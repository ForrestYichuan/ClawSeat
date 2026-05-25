#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MARK_EXPR="${CLAWSEAT_FAST_MARK_EXPR:-not host and not slow}"
DURATIONS="${PYTEST_DURATIONS:-25}"

python3 -m pytest tests/ -q -m "$MARK_EXPR" --durations="$DURATIONS" "$@"
