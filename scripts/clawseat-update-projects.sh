#!/usr/bin/env bash
# clawseat-update-projects.sh — bulk-update project workspaces when ClawSeat repo advances.
#
# Detects stale workspaces by comparing rendered_from_clawseat_sha against
# the current ClawSeat HEAD. Default mode is dry-run; --apply invokes
# agent_admin engineer regenerate-workspace --all-seats per stale project.
#
# Frozen projects (memory-host, test fixtures, deprecated waves) are skipped
# by default. Override with --include-frozen for forced bumps.
#
# Tier A — soft updates only (SKILL text / workspace md / hooks). Does NOT
# rewrite profile.toml. For Tier B (new protocol opt-in) use install.sh
# --mode multi per project. For Tier C (breaking schema) use
# install.sh --reinstall <project>.
set -euo pipefail

DRY_RUN=1
TARGET_PROJECT=""
EXTRA_FROZEN=""
INCLUDE_FROZEN=0

# Frozen list (prefix-match). Default-frozen = memory-host + test fixtures.
DEFAULT_FROZEN_PREFIXES="install dp-e2e- test-fix demo-m-verify testbed-"

usage() {
  cat <<EOF
Usage: $0 [--apply] [--project <name>] [--also-frozen <csv>] [--include-frozen]

Bulk-regenerate ClawSeat project workspaces when ClawSeat HEAD has advanced
ahead of rendered_from_clawseat_sha in workspaces. Tier A (soft) updates only.

Flags:
  --apply              Actually invoke agent_admin regenerate-workspace.
                       Default is dry-run that lists what would update.
  --project <name>     Restrict to one project (still respects frozen list).
  --also-frozen <csv>  Comma-separated additional frozen prefixes to skip.
  --include-frozen     Override default frozen list and include them too.
                       Use only when operator forcibly bumps a frozen project.
  --help               This message.

Default-frozen prefix list (skipped unless --include-frozen):
  install / dp-e2e-* / test-fix / demo-m-verify / testbed-*

Exit codes:
  0  success (dry-run or --apply both 0 on success)
  1  argument error / missing prerequisites
  2  some projects failed to regenerate during --apply
EOF
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) DRY_RUN=0; shift ;;
    --project) TARGET_PROJECT="$2"; shift 2 ;;
    --also-frozen) EXTRA_FROZEN="${2//,/ }"; shift 2 ;;
    --include-frozen) INCLUDE_FROZEN=1; shift ;;
    --help|-h) usage 0 ;;
    *) echo "unknown arg: $1" >&2; usage 1 ;;
  esac
done

REAL_HOME="${CLAWSEAT_REAL_HOME:-$HOME}"
PROJECTS_DIR="$REAL_HOME/.agents/projects"
WORKSPACES_DIR="$REAL_HOME/.agents/workspaces"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_ADMIN="$REPO_ROOT/core/scripts/agent_admin.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d "$PROJECTS_DIR" ]; then
  echo "no projects dir at $PROJECTS_DIR" >&2
  exit 1
fi

if [ ! -x "$AGENT_ADMIN" ] && [ ! -f "$AGENT_ADMIN" ]; then
  echo "agent_admin.py not found at $AGENT_ADMIN" >&2
  exit 1
fi

CLAWSEAT_SHA=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "")
if [ -z "$CLAWSEAT_SHA" ]; then
  echo "could not read ClawSeat git HEAD at $REPO_ROOT" >&2
  exit 1
fi

echo "ClawSeat HEAD: $CLAWSEAT_SHA"
echo "Projects dir:  $PROJECTS_DIR"
if [ "$DRY_RUN" = 1 ]; then
  echo "Mode:          dry-run (use --apply to regenerate)"
else
  echo "Mode:          APPLY (will invoke agent_admin regenerate-workspace)"
fi
echo ""

is_frozen() {
  local p="$1"
  [ "$INCLUDE_FROZEN" = 1 ] && return 1
  local prefixes="$DEFAULT_FROZEN_PREFIXES $EXTRA_FROZEN"
  for prefix in $prefixes; do
    # Suffix `-` in pattern means prefix-match; else exact match
    case "$prefix" in
      *-) [[ "$p" == "${prefix}"* ]] && return 0 ;;
      *)  [ "$p" = "$prefix" ] && return 0 ;;
    esac
  done
  return 1
}

active_workspace_roots() {
  local project_dir="$1"
  local workspace_dir="$2"
  local project_toml="$project_dir/project.toml"

  if [ -f "$project_toml" ]; then
    "$PYTHON_BIN" - "$project_toml" "$workspace_dir" <<'PY' || true
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

project_toml = Path(sys.argv[1])
workspace_dir = Path(sys.argv[2])

try:
    data = tomllib.loads(project_toml.read_text(encoding="utf-8"))
except Exception:
    data = {}

engineers = data.get("engineers") or []
if isinstance(engineers, list):
    for engineer in engineers:
        seat = str(engineer).strip()
        if seat:
            print(workspace_dir / seat)
PY
  fi
}

# Resolve escalation auth profile for --apply mode
if [ "$DRY_RUN" = 0 ] && [ -z "${CLAWSEAT_ENGINEER_PROFILE:-}" ]; then
  for candidate in \
    "$REAL_HOME/.agents/engineers/memory/engineer.toml" \
    "$REAL_HOME/.agents/engineers/operator/engineer.toml"; do
    if [ -f "$candidate" ]; then
      export CLAWSEAT_ENGINEER_PROFILE="$candidate"
      echo "Auth profile:  $CLAWSEAT_ENGINEER_PROFILE"
      break
    fi
  done
  if [ -z "${CLAWSEAT_ENGINEER_PROFILE:-}" ]; then
    echo "warn: no escalation profile found; regenerate-workspace will likely fail" >&2
    echo "  set CLAWSEAT_ENGINEER_PROFILE=<path/to/engineer.toml> explicitly" >&2
  fi
fi

fresh_count=0
stale_count=0
skipped_count=0
applied_count=0
failed_count=0

for proj_dir in "$PROJECTS_DIR"/*/; do
  [ -d "$proj_dir" ] || continue
  proj="$(basename "$proj_dir")"

  if [ -n "$TARGET_PROJECT" ] && [ "$proj" != "$TARGET_PROJECT" ]; then
    continue
  fi

  if is_frozen "$proj"; then
    printf "  skip      %-32s (frozen)\n" "$proj"
    skipped_count=$((skipped_count + 1))
    continue
  fi

  ws_dir="$WORKSPACES_DIR/$proj"
  if [ ! -d "$ws_dir" ]; then
    printf "  skip      %-32s (no workspace dir)\n" "$proj"
    skipped_count=$((skipped_count + 1))
    continue
  fi

  marker_roots=()
  while IFS= read -r active_root; do
    [ -n "$active_root" ] && marker_roots+=("$active_root")
  done < <(active_workspace_roots "$proj_dir" "$ws_dir")
  if [ "${#marker_roots[@]}" -eq 0 ]; then
    marker_roots=("$ws_dir")
  fi

  ws_sha=$(
    for marker_root in "${marker_roots[@]}"; do
      [ -d "$marker_root" ] || continue
      find "$marker_root" -type f \
        \( -name 'AGENTS.md' -o -name 'CLAUDE.md' -o -name 'GEMINI.md' \) \
        ! -path '*/.backup-*/*' -print0
    done \
      | while IFS= read -r -d '' marker_file; do
          grep -oh 'rendered_from_clawseat_sha=[a-f0-9]\+' "$marker_file" 2>/dev/null || true
        done \
      | head -1 | cut -d= -f2 || true
  )

  if [ -z "$ws_sha" ]; then
    printf "  unknown   %-32s (no rendered_from_clawseat_sha marker)\n" "$proj"
    stale_count=$((stale_count + 1))
  elif [ "$ws_sha" = "$CLAWSEAT_SHA" ]; then
    printf "  fresh     %-32s\n" "$proj"
    fresh_count=$((fresh_count + 1))
    continue
  else
    printf "  stale     %-32s (was %s)\n" "$proj" "${ws_sha:0:8}"
    stale_count=$((stale_count + 1))
  fi

  if [ "$DRY_RUN" = 1 ]; then
    continue
  fi

  if "$PYTHON_BIN" "$AGENT_ADMIN" engineer regenerate-workspace \
      --project "$proj" --all-seats --yes; then
    applied_count=$((applied_count + 1))
  else
    failed_count=$((failed_count + 1))
    echo "  FAILED    $proj" >&2
  fi
done

echo ""
echo "Summary: fresh=$fresh_count stale=$stale_count skipped=$skipped_count"
if [ "$DRY_RUN" = 0 ]; then
  echo "         applied=$applied_count failed=$failed_count"
  [ "$failed_count" -gt 0 ] && exit 2
elif [ "$stale_count" -gt 0 ]; then
  echo "Re-run with --apply to regenerate stale workspaces."
fi

exit 0
