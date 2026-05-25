#!/usr/bin/env bash
# ClawSeat v3 multi-team minimal install (Phase 1).
#
# Renders v3 project.toml from approved config proposals + creates
# workspace skeleton. Can also seed the built-in MULTI_TEAM_MINIMAL proposal
# pack used by the legacy clawseat-solo alias.
#
# Usage:
#   bash scripts/install_multi.sh --project <name> [--repo-root <path>]
#   bash scripts/install_multi.sh --project <name> --seed-template multi-team-minimal
#
# Prerequisites:
#   tasks/<project>/_config-proposals/<team>__approved.yaml for each team
#
# Spec ref: §4.1, §9, §16.7 in clawseat-v3-multi-team-protocol.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PROJECT=""
REPO_ROOT_OVERRIDE=""
TEAMS_FILTER=""
DRY_RUN=0
UPGRADE_TEAM=""
SEED_TEMPLATE=""
SEED_ARCHETYPE="auto"
SEED_FORCE=0
SEED_TMP_DIR=""
SEED_PROFILE="dev-minimal"
TEMPLATE_NAME="clawseat-minimal"

usage() {
  cat <<EOF
Usage: $0 --project <name> [--teams <csv>] [--repo-root <path>] [--dry-run]
       $0 --project <name> --upgrade-team <team> [--dry-run]
       $0 --project <name> --seed-template multi-team-minimal [--profile dev-minimal|dev-standard|test|planner-only] [--teams <csv>] [--dry-run]

Render v3 project.toml from approved config proposals.
Prerequisite: \$HOME/.agents/tasks/<project>/_config-proposals/<team>__approved.yaml exists.

Flags:
  --teams <csv>       Optional comma-separated filter (default: all approved teams).
                      Unknown team names hard-fail.
  --seed-template t   Seed approved proposals before rendering. Supported:
                      multi-team-minimal. Used by the clawseat-solo alias.
  --profile p         Subgroup profile for seed. Default: dev-minimal.
                      dev-minimal  : planner + builder, planner self-reviews (default).
                      dev-standard : planner + 2 builders + reviewer, reviewer gate required.
                      test         : planner + patrol, QA-only, no product code edits by default.
                      planner-only : memory + planner(s), no builder; planner self-contains
                                     diagnosis, implementation, testing, self-review, and closeout.
                      All profiles: local review/latest validation, no push/no PR, CI opt-in,
                      OpenClaw/Koder/Feishu/Lark as optional adapters, hot-plug without history loss.
  --template-name n   Template name to embed in project.toml.
  --seed-archetype a  auto | generic | cartooner (default: auto).
  --seed-force        Overwrite existing approved proposals when seeding.
  --upgrade-team <t>  Incremental: re-render project.toml to include team <t>
                      while preserving existing teams. Requires the new team's
                      __approved.yaml to be present. Existing teams discovered
                      from existing tasks/<project>/<team>/ dirs.

EOF
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --teams) TEAMS_FILTER="$2"; shift 2 ;;
    --upgrade-team) UPGRADE_TEAM="$2"; shift 2 ;;
    --seed-template) SEED_TEMPLATE="$2"; shift 2 ;;
    --seed-archetype) SEED_ARCHETYPE="$2"; shift 2 ;;
    --seed-force) SEED_FORCE=1; shift ;;
    --profile) SEED_PROFILE="$2"; shift 2 ;;
    --template-name) TEMPLATE_NAME="$2"; shift 2 ;;
    --repo-root) REPO_ROOT_OVERRIDE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --help|-h) usage 0 ;;
    *) echo "unknown arg: $1" >&2; usage 1 ;;
  esac
done

if [ -z "$PROJECT" ]; then
  echo "--project required" >&2
  usage 1
fi

REAL_HOME="${CLAWSEAT_REAL_HOME:-$HOME}"
AGENTS_ROOT="$REAL_HOME/.agents"
PROPOSALS_DIR="$AGENTS_ROOT/tasks/$PROJECT/_config-proposals"

if [ -n "$SEED_TEMPLATE" ]; then
  case "$SEED_TEMPLATE" in
    multi-team-minimal) ;;
    *) echo "unknown seed template: $SEED_TEMPLATE" >&2; exit 2 ;;
  esac
  case "$SEED_ARCHETYPE" in
    auto|generic|cartooner) ;;
    *) echo "--seed-archetype must be auto | generic | cartooner" >&2; exit 2 ;;
  esac
  if [ "$DRY_RUN" -eq 1 ]; then
    SEED_TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$SEED_TMP_DIR"' EXIT
    PROPOSALS_DIR="$SEED_TMP_DIR/_config-proposals"
  fi
  seed_args=(
    "--project" "$PROJECT"
    "--output-dir" "$PROPOSALS_DIR"
    "--repo-root" "${REPO_ROOT_OVERRIDE:-$REPO_ROOT}"
    "--archetype" "$SEED_ARCHETYPE"
    "--profile" "$SEED_PROFILE"
  )
  [ -n "$TEAMS_FILTER" ] && seed_args+=("--teams" "$TEAMS_FILTER")
  [ "$SEED_FORCE" -eq 1 ] && seed_args+=("--force")
  echo "→ seeding $SEED_TEMPLATE proposals${TEAMS_FILTER:+ (teams=$TEAMS_FILTER)}"
  "$PYTHON_BIN" "$REPO_ROOT/core/scripts/seed_multi_team_minimal.py" "${seed_args[@]}"
fi

# --upgrade-team: derive --teams from existing team dirs + new team
if [ -n "$UPGRADE_TEAM" ]; then
  if [ -n "$TEAMS_FILTER" ]; then
    echo "--upgrade-team and --teams are mutually exclusive" >&2
    exit 2
  fi
  if [ ! -f "$PROPOSALS_DIR/${UPGRADE_TEAM}__approved.yaml" ]; then
    echo "approved config for new team '$UPGRADE_TEAM' missing: $PROPOSALS_DIR/${UPGRADE_TEAM}__approved.yaml" >&2
    exit 2
  fi
  existing_teams=""
  if [ -d "$AGENTS_ROOT/tasks/$PROJECT" ]; then
    for d in "$AGENTS_ROOT/tasks/$PROJECT"/*/; do
      [ -d "$d" ] || continue
      tname="$(basename "$d")"
      [ "$tname" = "_config-proposals" ] && continue
      [ "$tname" = "contracts" ] && continue
      [ -f "$d/tasks.queue.jsonl" ] || [ -d "$d/brief" ] || continue
      existing_teams="${existing_teams:+$existing_teams,}$tname"
    done
  fi
  if echo ",$existing_teams," | grep -q ",$UPGRADE_TEAM,"; then
    TEAMS_FILTER="$existing_teams"
  else
    TEAMS_FILTER="${existing_teams:+$existing_teams,}$UPGRADE_TEAM"
  fi
  echo "→ upgrade-team: rendering teams=$TEAMS_FILTER"
fi
PROFILE_OUT="$AGENTS_ROOT/profiles/${PROJECT}-profile-dynamic.toml"
PROJECT_RECORD_OUT="$AGENTS_ROOT/projects/$PROJECT/project.toml"
TEAM_OWNERSHIP_OUT="$AGENTS_ROOT/tasks/$PROJECT/TEAM_OWNERSHIP.md"
RENDER_SCRIPT="$REPO_ROOT/core/scripts/render_project_toml_v3.py"
VALIDATOR="$REPO_ROOT/core/lib/proposal_validator.py"

# Step 1: proposals dir must exist + have at least 1 approved yaml
if [ ! -d "$PROPOSALS_DIR" ]; then
  echo "proposals dir missing: $PROPOSALS_DIR" >&2
  echo "Memory must first write per-team config proposals (§16) and operator approves." >&2
  exit 2
fi

approved_count=0
for f in "$PROPOSALS_DIR"/*__approved.yaml; do
  [ -f "$f" ] && approved_count=$((approved_count + 1))
done
if [ "$approved_count" -eq 0 ]; then
  echo "no *__approved.yaml in $PROPOSALS_DIR" >&2
  exit 2
fi

# Step 2: validate proposals (§16.7 render validation)
echo "→ validating $approved_count approved config(s) in $PROPOSALS_DIR"
if ! "$PYTHON_BIN" "$VALIDATOR" "$PROPOSALS_DIR"; then
  echo "validation failed; refusing to render" >&2
  exit 3
fi

# Step 3: render project.toml (with optional --teams filter)
echo "→ rendering project.toml${TEAMS_FILTER:+ (teams=$TEAMS_FILTER)}"
if [ "$DRY_RUN" -eq 1 ]; then
  "$PYTHON_BIN" "$RENDER_SCRIPT" \
    --project "$PROJECT" \
    --proposals-dir "$PROPOSALS_DIR" \
    ${REPO_ROOT_OVERRIDE:+--repo-root "$REPO_ROOT_OVERRIDE"} \
    ${TEAMS_FILTER:+--teams "$TEAMS_FILTER"} \
    --template-name "$TEMPLATE_NAME" \
    --output -
  echo "→ dry-run; not writing"
  exit 0
fi

mkdir -p "$(dirname "$PROFILE_OUT")"
"$PYTHON_BIN" "$RENDER_SCRIPT" \
  --project "$PROJECT" \
  --proposals-dir "$PROPOSALS_DIR" \
  ${REPO_ROOT_OVERRIDE:+--repo-root "$REPO_ROOT_OVERRIDE"} \
  ${TEAMS_FILTER:+--teams "$TEAMS_FILTER"} \
  --template-name "$TEMPLATE_NAME" \
  --project-record-output "$PROJECT_RECORD_OUT" \
  --ownership-output "$TEAM_OWNERSHIP_OUT" \
  --output "$PROFILE_OUT"

# Step 4: workspace skeleton dirs (only for teams included in render)
echo "→ creating workspace skeleton"
if [ -n "$TEAMS_FILTER" ]; then
  # Skeleton only for explicitly requested teams
  IFS=',' read -ra _TEAM_LIST <<< "$TEAMS_FILTER"
  for team in "${_TEAM_LIST[@]}"; do
    team="$(echo "$team" | tr -d '[:space:]')"
    team_dir="$AGENTS_ROOT/tasks/$PROJECT/$team"
    mkdir -p "$team_dir/brief" "$team_dir/workflow" "$team_dir/DELIVERY" "$team_dir/acceptance"
    : > "$team_dir/tasks.queue.jsonl.placeholder"
  done
else
  for f in "$PROPOSALS_DIR"/*__approved.yaml; do
    team="$(basename "$f" __approved.yaml)"
    team_dir="$AGENTS_ROOT/tasks/$PROJECT/$team"
    mkdir -p "$team_dir/brief" "$team_dir/workflow" "$team_dir/DELIVERY" "$team_dir/acceptance"
    : > "$team_dir/tasks.queue.jsonl.placeholder"
  done
fi

# Step 5: contracts dir (cross-team)
mkdir -p "$AGENTS_ROOT/tasks/$PROJECT/contracts"

# Step 6: verify with v3 loader (round-trip sanity)
echo "→ verifying with v3 loader"
"$PYTHON_BIN" - <<EOF
import sys
sys.path.insert(0, "$REPO_ROOT/core/lib")
from profile_loader_v3 import load_profile_v3
p = load_profile_v3("$PROFILE_OUT")
print(f"  project: {p.project_name}")
print(f"  mode: {p.team_structure}")
print(f"  teams: {sorted(p.teams.keys())}")
print(f"  total seats: {len(p.seats)}")
EOF

echo ""
echo "v3 multi-mode render complete:"
echo "  profile: $PROFILE_OUT"
echo "  ownership: $TEAM_OWNERSHIP_OUT"
echo "  workspace: $AGENTS_ROOT/tasks/$PROJECT/"
