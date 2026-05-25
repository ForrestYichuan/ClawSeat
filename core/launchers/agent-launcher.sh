#!/usr/bin/env bash
# INTERNAL — do not call directly.
# This is the L3 execution primitive in the v0.7 Seat Lifecycle Pyramid:
#   L1 (user-facing): scripts/install.sh, scripts/apply-koder-overlay.sh
#   L2 (CLI ops):     agent_admin session start-engineer, agent_admin project ...
#   L3 (this file):   agent-launcher.sh — sandbox HOME + secrets + runtime_dir
# See docs/ARCHITECTURE.md §3z for the full contract.
# If you find yourself calling this script directly from a TODO or doc,
# reconsider — L1 or L2 should already cover your case.
# Unified deterministic launcher for Claude Code, Codex, and Gemini CLI.
#
# Merged into clawseat from ~/Desktop/agent-launcher.command.
# All intra-launcher paths are now self-relative so this file can live in
# any directory. User-specific defaults (preset store, workspace
# bookmarks) read from env vars with desktop-compat defaults for back-compat.

set -euo pipefail

# Seat sessions live on the default tmux socket and are managed by this
# launcher from *outside* tmux. If invoked while $TMUX is set (e.g. the
# operator runs `restart-seat.sh` from inside a tmux pane, or the host
# desktop app inherits TMUX from its launching shell), `tmux new-session`
# below either refuses with "sessions should be nested with care" or
# nests the new server inside the caller's pane — both break seat
# lifecycle. Strip the inherited env up front so every downstream tmux
# call lands on the outer/server we own. (`send-and-verify.sh` defends
# per-invocation with `env -u TMUX`; this is the launcher-side mirror.)
unset TMUX TMUX_PANE

REAL_HOME="${REAL_HOME:-$HOME}"
LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER_REPO_ROOT="$(cd "$LAUNCHER_DIR/../.." && pwd)"
LAUNCHER_PYTHON_BIN="${PYTHON_BIN:-python3}"
HELPER="$LAUNCHER_DIR/agent-launcher-common.sh"
DISCOVER_HELPER="$LAUNCHER_DIR/agent-launcher-discover.py"
# Export so launcher helpers can resolve sibling files self-relatively.
export AGENT_LAUNCHER_DIR="$LAUNCHER_DIR"
# User preset storage — default to XDG config, fall back to legacy desktop path
# if it exists (seamless upgrade for users migrating from the desktop-only era).
if [[ -n "${AGENT_LAUNCHER_CUSTOM_PRESET_STORE:-}" ]]; then
  CUSTOM_PRESET_STORE="$AGENT_LAUNCHER_CUSTOM_PRESET_STORE"
elif [[ -f "$REAL_HOME/Desktop/.agent-launcher-custom-presets.json" ]]; then
  CUSTOM_PRESET_STORE="$REAL_HOME/Desktop/.agent-launcher-custom-presets.json"
else
  CUSTOM_PRESET_STORE="$REAL_HOME/.config/clawseat/launcher-custom-presets.json"
fi

if [[ ! -f "$HELPER" ]]; then
  echo "error: missing helper script: $HELPER" >&2
  exit 1
fi

# shellcheck source=./agent-launcher-common.sh
source "$HELPER"

for _launcher_lib in \
  "$LAUNCHER_DIR/helpers/env.sh" \
  "$LAUNCHER_DIR/helpers/auth.sh" \
  "$LAUNCHER_DIR/helpers/sandbox.sh" \
  "$LAUNCHER_DIR/runtimes/claude.sh" \
  "$LAUNCHER_DIR/runtimes/codex.sh" \
  "$LAUNCHER_DIR/runtimes/gemini.sh"; do
  if [[ ! -f "$_launcher_lib" ]]; then
    echo "error: missing launcher library: $_launcher_lib" >&2
    exit 1
  fi
  # shellcheck source=/dev/null
  source "$_launcher_lib"
done
unset _launcher_lib

TOOL_NAME=""
SESSION_NAME=""
SESSION_NAME_EXPLICIT="0"
AUTH_MODE=""
WORKDIR=""
EXEC_MODE=""
CUSTOM_ENV_FILE=""
GENERATED_CUSTOM_ENV_FILE="0"
HEADLESS="0"
DRY_RUN="0"
CHECK_SECRETS_TOOL=""      # set by --check-secrets <tool> (needs --auth too)

print_help() {
  cat <<'EOF'
Usage: agent-launcher.sh [options]

Options:
  --tool <claude|codex|gemini>   Agent CLI to launch
  --auth <mode>                  Authentication mode for the selected tool
  --dir <path>                   Startup directory
  --session <name>               tmux session name
  --custom-env-file <path>       Internal one-shot custom API env file
  --headless                     Compatibility flag; launcher is tmux-only
  --dry-run                      Print resolved launch config and exit
  --exec-agent                   Internal flag used inside tmux
  --check-secrets <tool>         Report secret-file readiness as JSON
  -h, --help                     Show this help
EOF
}

uppercase_ascii() {
  printf '%s' "$1" | tr '[:lower:]' '[:upper:]'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tool) TOOL_NAME="$2"; shift 2 ;;
    --session) SESSION_NAME="$2"; SESSION_NAME_EXPLICIT="1"; shift 2 ;;
    --auth) AUTH_MODE="$2"; shift 2 ;;
    --dir) WORKDIR="$2"; shift 2 ;;
    --custom-env-file) CUSTOM_ENV_FILE="$2"; shift 2 ;;
    --headless) HEADLESS="1"; shift ;;
    --dry-run) DRY_RUN="1"; shift ;;
    --exec-agent) EXEC_MODE="1"; shift ;;
    # Preflight hook: agent-launcher.sh --check-secrets <tool> --auth <mode>
    # prints one JSON line saying whether the auth's secret file/key is ready.
    --check-secrets) CHECK_SECRETS_TOOL="$2"; shift 2 ;;
    --help|-h) print_help; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

handle_check_secrets

validate_tool_name() {
  case "$1" in
    claude|codex|gemini) return 0 ;;
    *)
      echo "error: --tool must be claude|codex|gemini, got '$1'" >&2
      exit 2
      ;;
  esac
}

resolve_launcher_workdir() {
  local raw_path="${1:-}"
  if [[ -z "$raw_path" ]]; then
    pwd -P
    return 0
  fi
  launcher_resolve_directory_path "$raw_path" 2>/dev/null || {
    echo "error: startup directory does not exist: $raw_path" >&2
    exit 1
  }
}

default_session_name() {
  local tool="$1" auth="$2" workdir="$3"
  printf '%s\n' "${tool}-${auth}-$(launcher_slugify "$(basename "$workdir")")"
}

normalize_explicit_session_name() {
  if [[ "$SESSION_NAME_EXPLICIT" != "1" || -z "$SESSION_NAME" || -z "$TOOL_NAME" ]]; then
    return 0
  fi
  case "$SESSION_NAME" in
    *-"$TOOL_NAME") ;;
    *) SESSION_NAME="${SESSION_NAME}-${TOOL_NAME}" ;;
  esac
}

prompt_tool_and_auth_interactive() {
  # Only prompt when stdin is a TTY and not running headless.
  if [[ ! -t 0 ]] || [[ "$HEADLESS" == "1" ]]; then
    return 1
  fi
  if [[ -z "$TOOL_NAME" ]]; then
    printf 'Tool [claude/codex/gemini]: ' >&2
    read -r TOOL_NAME
  fi
  if [[ -z "$AUTH_MODE" ]]; then
    printf 'Auth mode [oauth/oauth_token/api/custom/...]: ' >&2
    read -r AUTH_MODE
  fi
  return 0
}

validate_top_level_inputs() {
  if [[ -z "$TOOL_NAME" ]]; then
    if ! prompt_tool_and_auth_interactive; then
      echo "error: --tool is required" >&2
      exit 2
    fi
  fi
  validate_tool_name "$TOOL_NAME"

  if [[ -z "$AUTH_MODE" ]]; then
    if ! prompt_tool_and_auth_interactive; then
      echo "error: --auth is required" >&2
      exit 2
    fi
  fi
  validate_auth_mode "$TOOL_NAME" "$AUTH_MODE"

  WORKDIR="$(resolve_launcher_workdir "$WORKDIR")"
  [[ -n "$SESSION_NAME" ]] || SESSION_NAME="$(default_session_name "$TOOL_NAME" "$AUTH_MODE" "$WORKDIR")"
  normalize_explicit_session_name
}


exec_agent_shell_command() {
  # tmux new-session doesn't propagate non-allowlisted env vars from the
  # caller to the spawned shell. Worse, an already-running tmux server can
  # carry stale CLAWSEAT_* values from a previous seat in its global
  # environment. Clear every managed key first, then explicitly inject this
  # launch's values into the pane command.
  local -a managed_env_vars=(
    CARTOONER_CLAUDE_CODE_EXECUTABLE
    REAL_HOME
    CLAWSEAT_ROOT
    CLAWSEAT_PROJECT
    CLAWSEAT_PROVIDER
    CLAWSEAT_SEAT
    CLAWSEAT_ENGINEER_ID
    CLAWSEAT_ENGINEER_PROFILE
    CLAWSEAT_TOOLS_ISOLATION
    CLAWSEAT_PROJECT_TOOL_ROOT
    CLAWSEAT_NO_AUTO_RESUME
    CLAWSEAT_MEMORY_BRIEF
    CLAWSEAT_ANCESTOR_BRIEF
  )
  local -a cmd=(env)
  local key value
  for key in "${managed_env_vars[@]}"; do
    cmd+=("-u" "$key")
  done
  for key in "${managed_env_vars[@]}"; do
    value="${!key:-}"
    if [[ -n "$value" ]]; then
      cmd+=("$key=$value")
    fi
  done
  cmd+=(bash "$0" --tool "$TOOL_NAME" --session "$SESSION_NAME" --auth "$AUTH_MODE" --dir "$WORKDIR" --exec-agent)
  if [[ -n "$CUSTOM_ENV_FILE" ]]; then
    cmd+=(--custom-env-file "$CUSTOM_ENV_FILE")
  fi
  printf '%q ' "${cmd[@]}"
}

exec_inline_agent() {
  if [[ -n "$CUSTOM_ENV_FILE" ]]; then
    exec "$0" --tool "$TOOL_NAME" --session "$SESSION_NAME" --auth "$AUTH_MODE" --dir "$WORKDIR" --custom-env-file "$CUSTOM_ENV_FILE" --exec-agent
  fi
  exec "$0" --tool "$TOOL_NAME" --session "$SESSION_NAME" --auth "$AUTH_MODE" --dir "$WORKDIR" --exec-agent
}

run_selected_tool() {
  case "$1" in
    claude) run_claude_runtime "$2" "$3" "$4" ;;
    codex) run_codex_runtime "$2" "$3" "$4" ;;
    gemini) run_gemini_runtime "$2" "$3" "$4" ;;
    *) echo "error: unsupported tool: $1" >&2; exit 2 ;;
  esac
}

# Audit finding #11 (2026-05-11): inject ~/.agents/secrets/shared/*.env
# into the launching tool's env BEFORE runtime-specific auth secret loads.
# Auth-tied secrets (sourced inside runtimes/<tool>.sh) thus override the
# shared baseline. Without this, builder-image / builder-av had no
# MINIMAX_API_KEY / GEMINI_API_KEY / XCODE_BEST_GPT_IMAGE_API_KEY in
# their sandbox env even though shared/ files existed — agent_admin's
# build_runtime added them but start_engineer_launch bypassed build_runtime.
# Loading at the launcher (L3) instead means every launch path inherits.
load_shared_secrets() {
  local shared_dir="${CLAWSEAT_SHARED_SECRETS_DIR:-${REAL_HOME:-$HOME}/.agents/secrets/shared}"
  if [[ ! -d "$shared_dir" ]]; then
    return 0
  fi
  set -a
  local f
  for f in "$shared_dir"/*.env; do
    [[ -f "$f" ]] || continue
    # shellcheck source=/dev/null
    source "$f"
  done
  set +a
}

if [[ "${CLAWSEAT_AGENT_LAUNCHER_LIBRARY_ONLY:-}" == "1" ]]; then
  return 0 2>/dev/null || exit 0
fi

if [[ -z "$EXEC_MODE" ]]; then
  validate_top_level_inputs
  ensure_launcher_proxy_env
  ensure_custom_env_file_for_auth

  if [[ "$DRY_RUN" == "1" ]]; then
    cat <<EOF
Unified launcher dry-run
  tool:     $TOOL_NAME
  auth:     $AUTH_MODE
  dir:      $WORKDIR
  session:  $SESSION_NAME
  custom:   $([[ -n "$CUSTOM_ENV_FILE" ]] && printf yes || printf no)
  headless: $HEADLESS
EOF
    cleanup_generated_custom_env_file
    exit 0
  fi

  if ! command -v tmux >/dev/null 2>&1; then
    echo "warn: tmux not found — falling back to inline $TOOL_NAME" >&2
    launcher_remember_recent_dir "$WORKDIR"
    exec_inline_agent
  fi

  if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "reusing existing tmux session '$SESSION_NAME'"
    tmux set-option -t "$SESSION_NAME" detach-on-destroy off
    cleanup_generated_custom_env_file
  else
    tmux new-session -d -s "$SESSION_NAME" -x 220 -y 60 \
      "$(exec_agent_shell_command)" \
      \; set-option -t "$SESSION_NAME" detach-on-destroy off
    echo "launched tmux session '$SESSION_NAME'"
  fi

  launcher_remember_recent_dir "$WORKDIR"

  # bash 3.2 on macOS has no ${VAR^} uppercase-first operator; use awk
  # (top-level scope here, so no `local` — that's a bash syntax error
  # outside a function in some shells).
  _tool_title="$(printf '%s' "$TOOL_NAME" | awk '{print toupper(substr($0,1,1)) substr($0,2)}')"
  cat <<EOF

${_tool_title} session ready
  auth:     $AUTH_MODE
  dir:      $WORKDIR
  tmux:     $SESSION_NAME
  mode:     tmux-only deterministic launcher

Manual attach:
  tmux attach -t $SESSION_NAME

Kill session:
  tmux kill-session -t $SESSION_NAME

EOF
  exit 0
fi

validate_top_level_inputs
ensure_launcher_proxy_env
ensure_custom_env_file_for_auth

load_shared_secrets

run_selected_tool "$TOOL_NAME" "$AUTH_MODE" "$WORKDIR" "$SESSION_NAME"
