#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0; PROJECT="install"; REPO_ROOT_OVERRIDE=""; FORCE_REPO_ROOT=""
INSTALL_MODE="single"; INSTALL_TEAMS=""  # v3 multi-team mode (spec §4.1 §12)
_PROJECT_EXPLICIT=0; _TEMPLATE_EXPLICIT=0  # set to 1 when flag is passed explicitly
UNINSTALL_PROJECT=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CLAWSEAT_ROOT="${CLAWSEAT_ROOT_OVERRIDE:-$REPO_ROOT}"
PYTHON_BIN_WAS_SET="${PYTHON_BIN+1}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
FORCE_REINSTALL=0
OPEN_NATIVE_WINDOWS=1
PROVISION_KEYS=0
ENABLE_AUTO_PATROL=0
LOAD_ALL_SKILLS=0
DETECT_ONLY=0
CALLER_HOME="${HOME:-}"

# HOME is intentionally rebound once for the whole script: keep CALLER_HOME only
# for diagnostics, and use exported HOME for all persisted user state paths.
# Resolve real user $HOME. install.sh writes user-level state under
# ~/.agents/... and reads user-level python user-site (iterm2 etc).
# When invoked from inside a sandboxed seat (minimax smoke tests),
# $HOME points to ~/.agent-runtime/identities/.../home which would
# scatter install state into the sandbox and hide iterm2/oauth.
# Resolve real HOME explicitly and re-export, so every subprocess
# (preflight, scan, iterm driver, memory hook, brief render) sees it.
REAL_HOME="${CLAWSEAT_REAL_HOME:-}"
if [[ -z "$REAL_HOME" ]]; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    REAL_HOME="$(/usr/bin/dscl . -read "/Users/$(id -un)" NFSHomeDirectory 2>/dev/null | awk 'NR==1{print $2}')"
  fi
  if [[ -z "$REAL_HOME" ]] && command -v getent >/dev/null 2>&1; then
    REAL_HOME="$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6)"
  fi
  [[ -z "$REAL_HOME" ]] && REAL_HOME="$HOME"
fi
export HOME="$REAL_HOME"
SCAN_SCRIPT="$REPO_ROOT/core/skills/memory-oracle/scripts/scan_environment.py"
SCAN_INDEX_SCRIPT="$REPO_ROOT/core/skills/memory-oracle/scripts/scan_index.py"
ITERM_DRIVER="$REPO_ROOT/core/scripts/iterm_panes_driver.py"
ITERM_DRIVER_TIMEOUT_SECONDS=30
MEMORY_BRIEF_TEMPLATE="$REPO_ROOT/core/templates/memory-bootstrap.template.md"
MEMORY_PATROL_TEMPLATE="$REPO_ROOT/core/templates/patrol.plist.in"
MIGRATE_ANCESTOR_PATHS_SCRIPT="$REPO_ROOT/core/scripts/migrate_ancestor_paths.py"
RECONCILE_SEAT_STATES_SCRIPT="$REPO_ROOT/core/scripts/reconcile_seat_states.py"
LAUNCHER_SCRIPT="$REPO_ROOT/core/launchers/agent-launcher.sh"
AGENT_ADMIN_SCRIPT="$REPO_ROOT/core/scripts/agent_admin.py"
PROJECTS_REGISTRY_SCRIPT="$REPO_ROOT/core/scripts/projects_registry.py"
CLAWSEAT_CLI_SCRIPT="$REPO_ROOT/core/scripts/clawseat-cli.sh"
SEND_AND_VERIFY_SCRIPT="$REPO_ROOT/core/shell-scripts/send-and-verify.sh"
WAIT_FOR_SEAT_SCRIPT="$REPO_ROOT/scripts/wait-for-seat.sh"
CLAWSEAT_AUTOUPDATE_INSTALLER="$REPO_ROOT/scripts/install_clawseat_autoupdate.py"
PATROL_HOOK_INSTALLER="$REPO_ROOT/core/skills/patrol/scripts/install_patrol_hook.py"
PATROL_CRON_INSTALLER="$REPO_ROOT/core/skills/patrol/scripts/install_patrol_cron.py"
SEAT_CLEAR_WATCHDOG_INSTALLER="$REPO_ROOT/core/skills/clawseat-install/scripts/install_seat_clear_watchdog.py"
MEMORY_ROOT="$HOME/.agents/memory"; PROVIDER_ENV=""; BRIEF_PATH=""
MEMORY_WORKSPACE=""
GRID_WINDOW_ID=""
GUIDE_FILE=""
KICKOFF_FILE=""
MEMORY_PATROL_PLIST_LABEL=""
MEMORY_PATROL_PLIST_PATH=""
MEMORY_PATROL_LOG_DIR=""
# Compatibility aliases for callers that source install.sh internals.
ANCESTOR_PATROL_PLIST_LABEL=""
ANCESTOR_PATROL_PLIST_PATH=""
ANCESTOR_PATROL_LOG_DIR=""
PROVIDER_MODE=""
PROVIDER_KEY=""
PROVIDER_BASE=""
PROVIDER_MODEL=""
FORCE_PROVIDER=""
FORCE_ALL_API_PROVIDER=""
FORCE_PROVIDER_CHOICE="${CLAWSEAT_INSTALL_PROVIDER:-}"
FORCE_BASE_URL=""
FORCE_API_KEY=""
FORCE_MODEL=""
REPO_ROOT_FORCED_NOTICE=""
MEMORY_TOOL="${CLAWSEAT_MEMORY_TOOL:-}"
MEMORY_TOOL_EXPLICIT="${CLAWSEAT_MEMORY_TOOL:+1}"
MEMORY_MODEL="${CLAWSEAT_MEMORY_MODEL:-gpt-5.4-mini}"
MEMORY_MODEL_EXPLICIT=0
STATUS_FILE=""
PROJECT_LOCAL_TOML=""
PROJECT_RECORD_PATH=""
AGENTS_TEMPLATES_ROOT="$HOME/.agents/templates"
CLAWSEAT_TEMPLATE_NAME="clawseat-engineering"
BOOTSTRAP_TEMPLATE_DIR=""
BOOTSTRAP_TEMPLATE_PATH=""
PENDING_SEATS=(planner builder reviewer patrol designer)
# PRIMARY_SEAT_ID = the seat user dialogs with (always one per project).
# Canonical templates use "memory"; imported legacy templates may use "ancestor".
# Set by resolve_pending_seats() based on template's first primary engineer.
PRIMARY_SEAT_ID="memory"

die() { local n="$1" code="$2" msg="$3"; printf '%s\nERR_CODE: %s\n' "$msg" "$code" >&2; exit "$n"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
note() { printf '==> %s\n' "$*"; }
PYTHON_BIN_OVERRIDE="${PYTHON_BIN:-}"
PYTHON_BIN_VERSION=""
PYTHON_BIN_RESOLUTION=""

refresh_clawseat_repo_paths() {
  CLAWSEAT_ROOT="${CLAWSEAT_ROOT_OVERRIDE:-$REPO_ROOT}"
  SCAN_SCRIPT="$REPO_ROOT/core/skills/memory-oracle/scripts/scan_environment.py"
  SCAN_INDEX_SCRIPT="$REPO_ROOT/core/skills/memory-oracle/scripts/scan_index.py"
  ITERM_DRIVER="$REPO_ROOT/core/scripts/iterm_panes_driver.py"
  MEMORY_BRIEF_TEMPLATE="$REPO_ROOT/core/templates/memory-bootstrap.template.md"
  MEMORY_PATROL_TEMPLATE="$REPO_ROOT/core/templates/patrol.plist.in"
  MIGRATE_ANCESTOR_PATHS_SCRIPT="$REPO_ROOT/core/scripts/migrate_ancestor_paths.py"
  RECONCILE_SEAT_STATES_SCRIPT="$REPO_ROOT/core/scripts/reconcile_seat_states.py"
  LAUNCHER_SCRIPT="$REPO_ROOT/core/launchers/agent-launcher.sh"
  AGENT_ADMIN_SCRIPT="$REPO_ROOT/core/scripts/agent_admin.py"
  PROJECTS_REGISTRY_SCRIPT="$REPO_ROOT/core/scripts/projects_registry.py"
  CLAWSEAT_CLI_SCRIPT="$REPO_ROOT/core/scripts/clawseat-cli.sh"
  SEND_AND_VERIFY_SCRIPT="$REPO_ROOT/core/shell-scripts/send-and-verify.sh"
  WAIT_FOR_SEAT_SCRIPT="$REPO_ROOT/scripts/wait-for-seat.sh"
  CLAWSEAT_AUTOUPDATE_INSTALLER="$REPO_ROOT/scripts/install_clawseat_autoupdate.py"
  PATROL_HOOK_INSTALLER="$REPO_ROOT/core/skills/patrol/scripts/install_patrol_hook.py"
  PATROL_CRON_INSTALLER="$REPO_ROOT/core/skills/patrol/scripts/install_patrol_cron.py"
  SEAT_CLEAR_WATCHDOG_INSTALLER="$REPO_ROOT/core/skills/clawseat-install/scripts/install_seat_clear_watchdog.py"
  export REPO_ROOT CLAWSEAT_ROOT
}

configure_clawseat_repo_root() {
  local selected_root=""
  if [[ -n "$FORCE_REPO_ROOT" ]]; then
    [[ -d "$FORCE_REPO_ROOT" ]] || die 2 INVALID_REPO_ROOT "--force-repo-root must be an existing directory: $FORCE_REPO_ROOT"
    REPO_ROOT="$(cd "$FORCE_REPO_ROOT" && pwd)"
    REPO_ROOT_FORCED_NOTICE="info: REPO_ROOT forced to $REPO_ROOT (--force-repo-root)"
  else
    selected_root="$(_select_fresh_clawseat_root "$REPO_ROOT" "$CLAWSEAT_TEMPLATE_NAME")"
    [[ -n "$selected_root" ]] && REPO_ROOT="$selected_root"
  fi
  refresh_clawseat_repo_paths
}

emit_repo_root_forced_notice() {
  if [[ -n "$REPO_ROOT_FORCED_NOTICE" ]]; then
    printf '%s\n' "$REPO_ROOT_FORCED_NOTICE" >&2
    REPO_ROOT_FORCED_NOTICE=""
  fi
}

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] '
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi
  "$@" || die 99 COMMAND_FAILED "command failed: $*"
}

refresh_memory_kb_index() {
  local project_memory_dir="$HOME/.agents/memory/projects/$PROJECT"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] refresh KB index for %s via %q rebuild --project %q\n' "$PROJECT" "$SCAN_INDEX_SCRIPT" "$PROJECT" >&2
    return 0
  fi
  if [[ ! -f "$SCAN_INDEX_SCRIPT" || ! -d "$project_memory_dir" ]]; then
    return 0
  fi
  "$PYTHON_BIN" "$SCAN_INDEX_SCRIPT" rebuild --project "$PROJECT" >/dev/null \
    || warn "memory KB index refresh failed for $PROJECT; continuing install"
}

INSTALL_LIB_DIR="$SCRIPT_DIR/install/lib"
_INSTALL_LIB_MODULES=(
  self_update.sh
  preflight.sh
  detect.sh
  i18n.sh
  provider.sh
  project.sh
  secrets.sh
  skills.sh
  window.sh
)
for _install_lib_module in "${_INSTALL_LIB_MODULES[@]}"; do
  # shellcheck source=/dev/null
  source "$INSTALL_LIB_DIR/$_install_lib_module"
done
unset _install_lib_module _INSTALL_LIB_MODULES

resolve_supported_python_bin
if [[ "$PYTHON_BIN_RESOLUTION" == "auto" ]]; then
  printf '==> Using Python %s at %s\n' "$PYTHON_BIN_VERSION" "$PYTHON_BIN" >&2
fi

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY_RUN=1; shift ;;
      --no-window|--no-windows) OPEN_NATIVE_WINDOWS=0; shift ;;
      --detect-only) DETECT_ONLY=1; shift ;;
      --project) PROJECT="$2"; _PROJECT_EXPLICIT=1; shift 2 ;;
      --repo-root) REPO_ROOT_OVERRIDE="$2"; shift 2 ;;
      --force-repo-root) FORCE_REPO_ROOT="$2"; shift 2 ;;
      --provider) FORCE_PROVIDER="$2"; shift 2 ;;
      --all-api-provider) FORCE_ALL_API_PROVIDER="$2"; shift 2 ;;
      --base-url) FORCE_BASE_URL="$2"; shift 2 ;;
      --api-key) FORCE_API_KEY="$2"; shift 2 ;;
      --model) FORCE_MODEL="$2"; shift 2 ;;
      --provision-keys) PROVISION_KEYS=1; shift ;;
      --memory-tool) MEMORY_TOOL="$2"; MEMORY_TOOL_EXPLICIT=1; shift 2 ;;
      --memory-model) MEMORY_MODEL="$2"; MEMORY_MODEL_EXPLICIT=1; shift 2 ;;
      --reinstall|--force)
        FORCE_REINSTALL=1
        shift
        if [[ $# -gt 0 && "$1" != --* ]]; then
          PROJECT="$1"
          _PROJECT_EXPLICIT=1
          shift
        fi
        ;;
      --uninstall) UNINSTALL_PROJECT="$2"; shift 2 ;;
      --enable-auto-patrol) ENABLE_AUTO_PATROL=1; shift ;;
      --load-all-skills) LOAD_ALL_SKILLS=1; shift ;;
      --template) CLAWSEAT_TEMPLATE_NAME="$2"; _TEMPLATE_EXPLICIT=1; shift 2 ;;
      --mode) INSTALL_MODE="$2"; shift 2 ;;
      --teams) INSTALL_TEAMS="$2"; shift 2 ;;
      --reset-harness-memory)
        "$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/core/scripts")
from seat_harness_memory import reset_all_harness_memory
removed = reset_all_harness_memory()
if removed:
    print("reset harness memory for: " + ", ".join(sorted(removed)))
else:
    print("no harness memory files found")
PY
        exit 0
        ;;
      --help|-h) cat <<'EOF'
Usage: scripts/install.sh [--project <name>] [--mode single|multi --teams <csv>] [--repo-root <path>] [--force-repo-root <path>] [--template clawseat-engineering|clawseat-creative|clawseat-minimal|clawseat-solo] [--memory-tool claude|codex|gemini] [--memory-model <model>] [--provider <mode|n>] [--all-api-provider <mode>] [--base-url <url> --api-key <key> [--model <name>]] [--provision-keys] [--reinstall|--force] [--uninstall <project>] [--enable-auto-patrol] [--load-all-skills] [--detect-only] [--dry-run] [--no-window] [--reset-harness-memory]
--mode multi --teams a,b,c   v3 multi-team flow (delegates to install_multi.sh; reads approved configs from tasks/<project>/_config-proposals/).
--template clawseat-minimal  v3 MULTI_TEAM_MINIMAL; seeds planner+builder subteams plus quality-docs, then delegates to --mode multi.
--template clawseat-solo     legacy alias for v3 MULTI_TEAM_MINIMAL; seeds planner+builder subteams plus quality-docs, then delegates to --mode multi.
--repo-root sets the target project repo; --force-repo-root overrides the ClawSeat install code root.
--detect-only prints one JSON environment summary and exits.
--no-window skips native iTerm workers/memories windows; tmux seats still launch.
--provider now controls the memory seat only; use --all-api-provider for global api-seat provider override.
--provision-keys prompts for missing template API keys and writes ~/.agents/.env.global.

Provider modes (--provider, --all-api-provider):
  oauth              option 3, Anthropic Pro / Claude Pro OAuth (host login, no API key)
  anthropic_console  option 4, Anthropic Console API key mode (ANTHROPIC_API_KEY)
  minimax            option 5, MiniMax API key mode
  deepseek           option 6, DeepSeek API key mode
  ark                option 7, Volcano ARK API key mode
  xcode-best         option 8, Xcode Best API key mode
  custom_api         custom endpoint; requires --base-url --api-key

Non-TTY environments (agent-launcher sandbox, CI, detached agent sessions) must pass --provider <mode>; install cannot prompt interactively and exits with code 2.

Templates (--template):
  clawseat-engineering   5-seat engineering flow (memory + planner + builder + reviewer + patrol), gstack-bound
  clawseat-creative      5-seat cartooner-bound creative team (memory + writer + builder-image + builder-av + patrol)
  clawseat-minimal       v3 MULTI_TEAM_MINIMAL (project-memory + one or more planner+builder subteams + quality-docs)
  clawseat-solo          legacy alias for MULTI_TEAM_MINIMAL (project-memory + one or more planner+builder subteams + quality-docs)
EOF
        exit 0
        ;;
      *) die 2 UNKNOWN_FLAG "unknown flag: $1" ;;
    esac
  done
  if [[ -n "$UNINSTALL_PROJECT" ]]; then
    [[ "$UNINSTALL_PROJECT" =~ ^[a-z0-9-]+$ ]] || die 2 INVALID_PROJECT "--uninstall project must match ^[a-z0-9-]+$"
  fi
  [[ "$PROJECT" =~ ^[a-z0-9-]+$ ]] || die 2 INVALID_PROJECT "project must match ^[a-z0-9-]+$"
  case "$INSTALL_MODE" in
    single|multi) ;;
    *) die 2 INVALID_MODE "--mode must be single | multi, got: $INSTALL_MODE" ;;
  esac
  if [[ "$INSTALL_MODE" == "multi" && -z "$INSTALL_TEAMS" && "$CLAWSEAT_TEMPLATE_NAME" != "clawseat-minimal" && "$CLAWSEAT_TEMPLATE_NAME" != "clawseat-solo" ]]; then
    die 2 INVALID_FLAGS "--mode multi requires --teams <csv> (e.g. core,content,shell)"
  fi
  case "$CLAWSEAT_TEMPLATE_NAME" in
    clawseat-engineering|clawseat-creative|clawseat-minimal|clawseat-solo) ;;
    *) die 2 INVALID_TEMPLATE "--template must be clawseat-engineering | clawseat-creative | clawseat-minimal | clawseat-solo, got: $CLAWSEAT_TEMPLATE_NAME" ;;
  esac
  if [[ -n "$MEMORY_TOOL" ]]; then
    case "$MEMORY_TOOL" in
      claude|codex|gemini) ;;
      *) die 2 INVALID_MEMORY_TOOL "--memory-tool must be claude | codex | gemini, got: $MEMORY_TOOL" ;;
    esac
  fi
  if [[ -n "$FORCE_ALL_API_PROVIDER" ]]; then
    case "$FORCE_ALL_API_PROVIDER" in
      minimax|deepseek|ark|xcode-best|anthropic_console|custom_api) ;;
      *) die 2 INVALID_FLAGS "--all-api-provider must be minimax | deepseek | ark | xcode-best | anthropic_console | custom_api, got: $FORCE_ALL_API_PROVIDER" ;;
    esac
  fi
  [[ -n "$MEMORY_MODEL" ]] || die 2 INVALID_MEMORY_MODEL "--memory-model must not be empty"
  if [[ -n "$REPO_ROOT_OVERRIDE" ]]; then
    [[ -d "$REPO_ROOT_OVERRIDE" ]] || die 2 INVALID_REPO_ROOT "--repo-root must be an existing directory: $REPO_ROOT_OVERRIDE"
  fi
  if [[ -n "$FORCE_REPO_ROOT" ]]; then
    [[ -d "$FORCE_REPO_ROOT" ]] || die 2 INVALID_REPO_ROOT "--force-repo-root must be an existing directory: $FORCE_REPO_ROOT"
  fi
  configure_clawseat_repo_root
  PROJECT_REPO_ROOT="${REPO_ROOT_OVERRIDE:-$REPO_ROOT}"
  if [[ -n "$FORCE_BASE_URL" ]]; then
    [[ -n "$FORCE_API_KEY" ]] || die 2 INVALID_FLAGS "--base-url 必须和 --api-key 成对"
    [[ -z "$FORCE_PROVIDER" || "$FORCE_PROVIDER" == "custom_api" ]] \
      || die 2 INVALID_FLAGS "--base-url/--api-key 只能配 --provider custom_api 或不传 --provider"
  elif [[ -n "$FORCE_API_KEY" ]]; then
    case "$FORCE_PROVIDER" in
      minimax|anthropic_console|deepseek|ark|xcode-best) ;;
      *)
        die 2 INVALID_FLAGS "--base-url 必须和 --api-key 成对"
        ;;
    esac
  fi
  if [[ -n "$FORCE_MODEL" ]]; then
    if [[ -n "$FORCE_BASE_URL" && -n "$FORCE_API_KEY" ]]; then
      :
    elif [[ -n "$FORCE_API_KEY" && ( "$FORCE_PROVIDER" == "minimax" || "$FORCE_PROVIDER" == "anthropic_console" || "$FORCE_PROVIDER" == "deepseek" || "$FORCE_PROVIDER" == "ark" || "$FORCE_PROVIDER" == "xcode-best" ) ]]; then
      :
    else
      die 2 INVALID_FLAGS "--model 只能与 --base-url/--api-key 一起使用，或配合 --provider minimax|anthropic_console|deepseek|ark|xcode-best + --api-key"
    fi
  fi
  compute_project_paths
}

compute_project_paths() {
  STATUS_FILE="$HOME/.agents/tasks/$PROJECT/STATUS.md"
  PROVIDER_ENV="$HOME/.agents/tasks/$PROJECT/memory-provider.env"
  BRIEF_PATH="$HOME/.agents/tasks/$PROJECT/patrol/handoffs/memory-bootstrap.md"
  MEMORY_WORKSPACE="$HOME/.agents/workspaces/$PROJECT/memory"
  PROJECT_LOCAL_TOML="$HOME/.agents/tasks/$PROJECT/project-local.toml"
  PROJECT_RECORD_PATH="$HOME/.agents/projects/$PROJECT/project.toml"
  GUIDE_FILE="$HOME/.agents/tasks/$PROJECT/OPERATOR-START-HERE.md"
  KICKOFF_FILE="$HOME/.agents/tasks/$PROJECT/patrol/handoffs/memory-kickoff.txt"
  MEMORY_PATROL_PLIST_LABEL="com.clawseat.${PROJECT}.patrol"
  MEMORY_PATROL_PLIST_PATH="$HOME/Library/LaunchAgents/${MEMORY_PATROL_PLIST_LABEL}.plist"
  MEMORY_PATROL_LOG_DIR="$HOME/.agents/tasks/$PROJECT/patrol/logs"
  ANCESTOR_PATROL_PLIST_LABEL="$MEMORY_PATROL_PLIST_LABEL"
  ANCESTOR_PATROL_PLIST_PATH="$MEMORY_PATROL_PLIST_PATH"
  ANCESTOR_PATROL_LOG_DIR="$MEMORY_PATROL_LOG_DIR"
  BOOTSTRAP_TEMPLATE_DIR="$AGENTS_TEMPLATES_ROOT/$CLAWSEAT_TEMPLATE_NAME"
  BOOTSTRAP_TEMPLATE_PATH="$BOOTSTRAP_TEMPLATE_DIR/template.toml"
}

delegate_multi_team_install() {
  local reason="${1:-multi}"
  local multi_script="$REPO_ROOT/scripts/install_multi.sh"
  [[ -x "$multi_script" ]] || die 2 MISSING_SCRIPT "install_multi.sh not found at $multi_script"
  local multi_args=("--project" "$PROJECT")
  [[ -n "$INSTALL_TEAMS" ]] && multi_args+=("--teams" "$INSTALL_TEAMS")
  [[ "$DRY_RUN" == "1" ]] && multi_args+=("--dry-run")
  [[ -n "$REPO_ROOT_OVERRIDE" ]] && multi_args+=("--repo-root" "$REPO_ROOT_OVERRIDE")
  if [[ "$CLAWSEAT_TEMPLATE_NAME" == "clawseat-minimal" || "$CLAWSEAT_TEMPLATE_NAME" == "clawseat-solo" ]]; then
    multi_args+=("--seed-template" "multi-team-minimal" "--seed-archetype" "auto" "--template-name" "clawseat-minimal")
    if [[ "$CLAWSEAT_TEMPLATE_NAME" == "clawseat-solo" ]]; then
      printf '==> clawseat-solo is a legacy alias for v3 MULTI_TEAM_MINIMAL; delegating to install_multi.sh\n' >&2
    else
      printf '==> clawseat-minimal uses v3 MULTI_TEAM_MINIMAL; delegating to install_multi.sh\n' >&2
    fi
  else
    printf '==> v3 multi-team mode: delegating to install_multi.sh (teams=%s)\n' "$INSTALL_TEAMS" >&2
  fi
  [[ -n "$reason" ]] && printf '==> multi-team delegate reason: %s\n' "$reason" >&2
  exec "$multi_script" "${multi_args[@]}"
}

main() {
  parse_args "$@"
  if [[ "$DETECT_ONLY" == "1" ]]; then
    detect_all
    exit 0
  fi
  if [[ -n "$UNINSTALL_PROJECT" ]]; then
    uninstall_project_registry_entry "$UNINSTALL_PROJECT"
    exit 0
  fi
  # v3 multi-team mode (Phase 1 minimal bridge — spec §12)
  # Delegates to install_multi.sh; legacy single flow runs only when --mode=single (default).
  if [[ "$INSTALL_MODE" == "multi" ]]; then
    delegate_multi_team_install "explicit --mode multi"
  fi
  if [[ ( "$CLAWSEAT_TEMPLATE_NAME" == "clawseat-minimal" || "$CLAWSEAT_TEMPLATE_NAME" == "clawseat-solo" ) && "$_TEMPLATE_EXPLICIT" == "1" ]]; then
    delegate_multi_team_install "explicit --template $CLAWSEAT_TEMPLATE_NAME"
  fi
  self_update_check "$@"
  prompt_kind_first_flow; resolve_pending_seats; normalize_provider_choice
  if [[ "$CLAWSEAT_TEMPLATE_NAME" == "clawseat-minimal" || "$CLAWSEAT_TEMPLATE_NAME" == "clawseat-solo" ]]; then
    delegate_multi_team_install "interactive $CLAWSEAT_TEMPLATE_NAME selection"
  fi
  run_legacy_path_migration
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] CLAWSEAT_TEMPLATE_NAME=%s\n' "$CLAWSEAT_TEMPLATE_NAME" >&2
    printf '[dry-run] PENDING_SEATS=(%s)\n' "${PENDING_SEATS[*]}" >&2
  fi
  _check_api_keys_for_template || exit 0
  if [[ "$FORCE_REINSTALL" == "1" ]]; then
    _reinstall_project
    trap '_reinstall_exit_trap $?' EXIT
  fi
  # Provider selection runs before bootstrap preflight so non-TTY installs can
  # fail with provider guidance instead of a claude_required hard block, and so
  # memory-tool overrides can inform the later preflight gate.
  ensure_python_tomllib_fallback; scan_machine; select_provider
  emit_repo_root_forced_notice
  ensure_host_deps; reconcile_seat_liveness_state; prompt_autoupdate_optin
  bootstrap_project_profile
  if [[ "$FORCE_REINSTALL" == "1" ]]; then
    _restore_reinstall_project_seat_overrides
  fi
  migrate_project_profile_to_v2
  render_brief
  note "Step 5: launch primary seat ($PRIMARY_SEAT_ID) via agent-launcher"
  launch_seat "$PROJECT-$PRIMARY_SEAT_ID" "$MEMORY_WORKSPACE" "$BRIEF_PATH" "$PRIMARY_SEAT_ID"
  ensure_privacy_kb_template
  install_skills_by_tier
  install_privacy_pre_commit_hook
  register_project_registry
  install_clawseat_cli_symlink
  install_primary_patrol_plist
  install_seat_clear_watchdog
  install_patrol_bootstrap

  if [[ "$OPEN_NATIVE_WINDOWS" == "1" ]]; then
    # v2 split window topology (per RFC-001 §3): one workers window per project +
    # one shared memories window across all projects (rebuilt on each install).
    if [[ "$PRIMARY_SEAT_ID" == "memory" ]]; then
      note "Step 7a: open per-project workers window (${#PENDING_SEATS[@]} template workers)"
      open_iterm_window "$(workers_payload)" GRID_WINDOW_ID

      [[ ! -f "$REPO_ROOT/scripts/cleanup-stale-memories-window.sh" ]] || bash "$REPO_ROOT/scripts/cleanup-stale-memories-window.sh" || true
      note "Step 7b: ensure shared memories window (tab per project)"
      local _memories_payload
      _memories_payload="$(memories_payload)"
      if [[ -n "$_memories_payload" && "$_memories_payload" != *'"status": "skip"'* ]]; then
        local _mem_window_id=""
        open_iterm_window "$_memories_payload" _mem_window_id
      else
        warn "memories_payload returned skip — no project memory tmux sessions found"
      fi
    else
      # Legacy single-window topology for imported ancestor-primary templates.
      note "Step 7: open legacy iTerm worker grid"; open_iterm_window "$(grid_payload)" GRID_WINDOW_ID
    fi
  else
    note "Step 7: skip native iTerm windows (--no-window); tmux seats remain available for embedded xterm attach"
  fi

  # v1 LEGACY (M4 remove): machine-memory-claude tmux session may still be running on machines
  # upgraded from v0.6 or earlier; v2 install no longer creates/manages it.
  # M4 will retire the legacy session entirely.
  note "Step 9: focus primary seat ($PRIMARY_SEAT_ID) and persist operator guide"
  if [[ -n "$GRID_WINDOW_ID" ]]; then
    run sleep 3
    focus_iterm_window "$GRID_WINDOW_ID" "$PRIMARY_SEAT_ID"
  else
    warn "Skipping primary seat focus because no iTerm grid window was opened."
  fi
  note "Step 9.5: persist Phase-A kickoff prompt to memory-kickoff.txt"
  persist_phase_a_kickoff_prompt
  touch_project_registry
  refresh_memory_kb_index
  write_operator_guide
  print_operator_banner
  _clear_reinstall_backups
  trap - EXIT
}


if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
