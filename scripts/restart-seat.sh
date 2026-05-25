#!/usr/bin/env bash
# restart-seat.sh — kill and re-launch a single project seat's tmux session.
#
# Why this exists: `agent_admin.py session start-engineer` requires a
# CLAWSEAT_ENGINEER_PROFILE with dispatch_authority. Most project profiles
# don't grant this to any seat, so operators have no convenient way to
# restart a single dead seat — they have to hand-write the tmux + launcher
# invocation. This script does the wrapping.
#
# Usage:
#   scripts/restart-seat.sh <project> <seat> [--auth <mode>] [--no-window]
#
# Examples:
#   scripts/restart-seat.sh cartooner-front memory
#   scripts/restart-seat.sh cartooner-front memory --auth chatgpt
#   scripts/restart-seat.sh install reviewer --no-window
#
# What it does:
#   1. Reads (tool, auth_mode, workspace) from `agent_admin session
#      effective-launch <project> <seat>`.
#   2. Kills the existing tmux session for that seat if alive.
#   3. Launches a new tmux session via core/launchers/agent-launcher.sh
#      with --headless (which re-execs with --exec-agent inside tmux).
#   4. Unless --no-window, asks agent_admin to refresh the project grid
#      so any orphaned iTerm pane re-attaches to the fresh session.

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: scripts/restart-seat.sh <project> <seat> [--auth <mode>] [--no-window]

Options:
  --auth <mode>   Override the engineer profile's default auth mode
                  (e.g. chatgpt to access global ~/.codex session history).
  --no-window     Skip iTerm grid refresh; only restart the tmux session.
  -h, --help      Show this help.
EOF
  exit "${1:-1}"
}

PROJECT=""
SEAT=""
AUTH_OVERRIDE=""
NO_WINDOW=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --auth)
      [[ $# -ge 2 ]] || { echo "error: --auth requires a value" >&2; usage 2; }
      AUTH_OVERRIDE="$2"; shift 2 ;;
    --no-window) NO_WINDOW=1; shift ;;
    --) shift; break ;;
    -*)
      echo "error: unknown flag: $1" >&2; usage 2 ;;
    *)
      if [[ -z "$PROJECT" ]]; then PROJECT="$1"
      elif [[ -z "$SEAT" ]]; then SEAT="$1"
      else echo "error: unexpected positional argument: $1" >&2; usage 2
      fi
      shift ;;
  esac
done

[[ -n "$PROJECT" && -n "$SEAT" ]] || usage 2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_ADMIN="$REPO_ROOT/core/scripts/agent_admin.py"
LAUNCHER="$REPO_ROOT/core/launchers/agent-launcher.sh"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TMUX_BIN="${TMUX_BIN:-tmux}"

command -v "$TMUX_BIN" >/dev/null 2>&1 || {
  echo "error: tmux not found in PATH (set TMUX_BIN to override)" >&2
  exit 3
}

tmux_outer() {
  env -u TMUX -u TMUX_PANE "$TMUX_BIN" "$@"
}

CUSTOM_ENV_FILE=""
CUSTOM_ENV_FILE_DELEGATED_TO_TMUX=0
cleanup_custom_env_file() {
  if [[ "$CUSTOM_ENV_FILE_DELEGATED_TO_TMUX" == "0" && -n "$CUSTOM_ENV_FILE" && -f "$CUSTOM_ENV_FILE" ]]; then
    rm -f "$CUSTOM_ENV_FILE"
  fi
}
trap cleanup_custom_env_file EXIT

# Backfill the seat's `.claude/settings.local.json` with a Stop hook pointing
# at memory-stop-hook.sh. Without this hook fired, the auto-resume read in
# core/launchers/runtimes/*.sh never has a `<seat>.session` file to read,
# so every restart cold-starts even though the resume read path is wired up.
# Idempotent: merges with existing settings (preserves workspace_label etc.)
# and skips if the same hook script is already registered.
ensure_stop_hook() {
  local workspace="$1"
  local repo_root="$2"
  local settings_dir="$workspace/.claude"
  local settings_file="$settings_dir/settings.local.json"
  local hook_path="$repo_root/scripts/hooks/memory-stop-hook.sh"

  if [[ ! -d "$workspace" ]]; then
    echo "  warn:      workspace missing, skipping stop-hook backfill: $workspace" >&2
    return 0
  fi
  if [[ ! -f "$hook_path" ]]; then
    echo "  warn:      stop-hook script missing, skipping: $hook_path" >&2
    return 0
  fi

  mkdir -p "$settings_dir" 2>/dev/null || true
  [[ -f "$settings_file" ]] || printf '{}' > "$settings_file"

  "$PYTHON_BIN" - "$settings_file" "$hook_path" <<'PY' || true
import json
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
hook_path = sys.argv[2]
hook_cmd = f"bash {hook_path}"

try:
    data = json.loads(settings_path.read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError):
    data = {}
if not isinstance(data, dict):
    data = {}

hooks = data.setdefault("hooks", {})
if not isinstance(hooks, dict):
    hooks = {}
    data["hooks"] = hooks

stop_list = hooks.setdefault("Stop", [])
if not isinstance(stop_list, list):
    stop_list = []
    hooks["Stop"] = stop_list

# Idempotent check: skip if any existing entry already points at our hook
already = any(
    isinstance(entry, dict)
    and isinstance(entry.get("hooks"), list)
    and any(
        isinstance(h, dict)
        and isinstance(h.get("command"), str)
        and hook_path in h["command"]
        for h in entry["hooks"]
    )
    for entry in stop_list
)
if not already:
    stop_list.append({
        "matcher": "",
        "hooks": [
            {
                "type": "command",
                "command": hook_cmd,
                "timeout": 10,
            }
        ],
    })
    settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("injected")
else:
    print("present")
PY
}

# Build the launch plan through agent_admin internals — single source of truth
# for provider SSOT, launcher auth labels, and custom env overlays.
if ! plan_out="$(
  "$PYTHON_BIN" - "$REPO_ROOT" "$PROJECT" "$SEAT" "$AUTH_OVERRIDE" 2>&1 <<'PY'
import shlex
import sys
import io
from contextlib import redirect_stdout
from types import SimpleNamespace

repo_root, project, seat, auth_override = sys.argv[1:5]
sys.path.insert(0, f"{repo_root}/core/scripts")
sys.path.insert(0, f"{repo_root}/core/lib")

try:
    import agent_admin  # type: ignore
    from agent_admin_session_base import _engineer_profile_path  # type: ignore

    def materialize_declared_session():
        project_obj = agent_admin.load_project(project)
        project_engineers = list(getattr(project_obj, "engineers", []) or [])
        if project_engineers and seat not in project_engineers:
            raise RuntimeError(
                f"{seat} is not declared in project {project}; add the seat before restarting"
            )
        overrides = getattr(project_obj, "seat_overrides", {}) or {}
        override = overrides.get(seat) or {}
        tool = str(override.get("tool") or "claude")
        mode = str(override.get("auth_mode") or override.get("mode") or "oauth")
        provider = str(
            override.get("provider")
            or ("openai" if tool == "codex" else "google" if tool == "gemini" else "anthropic")
        )
        model = str(override.get("model") or "")
        args = SimpleNamespace(engineer=seat, tool=tool, mode=mode, provider=provider)
        with redirect_stdout(io.StringIO()):
            return agent_admin.SWITCH_HANDLERS.create_declared_session_for_switch(project_obj, args, model)

    try:
        session = agent_admin.resolve_engineer_session(seat, project_name=project)
    except Exception:
        session = materialize_declared_session()
    launcher_auth = auth_override.strip() or agent_admin.SESSION_SERVICE._launcher_auth_for(session)
    if (
        not auth_override.strip()
        and session.tool == "claude"
        and session.auth_mode == "oauth"
        and session.provider == "anthropic"
    ):
        # Cartooner/ClawSeat policy: Claude Code official OAuth seats run via
        # long-lived CLAUDE_CODE_OAUTH_TOKEN, not host Keychain OAuth. This
        # keeps old session.toml records from reopening browser/login flows.
        launcher_auth = "oauth_token"
    custom_env_file = ""
    if not auth_override.strip():
        agent_admin.SESSION_SERVICE._sync_launcher_secret_file(session, launcher_auth)
        if launcher_auth in ("custom", "minimax", "deepseek", "xcode"):
            custom_env_file = agent_admin.SESSION_SERVICE._write_launcher_custom_env_file(session)

    runtime_dir = agent_admin.SESSION_SERVICE._launcher_runtime_dir(session, launcher_auth)
    if runtime_dir is not None and session.runtime_dir != str(runtime_dir):
        session.runtime_dir = str(runtime_dir)
        agent_admin.write_session(session)

    real_home = agent_admin.SESSION_SERVICE._real_home_for_tool_seeding()
    engineer_profile = _engineer_profile_path(session.engineer_id)
    fields = {
        "TOOL": session.tool,
        "AUTH_MODE": session.auth_mode,
        "PROVIDER": session.provider,
        "WORKSPACE": session.workspace,
        "SESSION_NAME": session.session,
        "ENGINEER_ID": session.engineer_id,
        "LAUNCHER_AUTH": launcher_auth,
        "CUSTOM_ENV_FILE": custom_env_file,
        "REAL_HOME_VALUE": str(real_home),
        "ENGINEER_PROFILE": str(engineer_profile),
    }
    for key, value in fields.items():
        print(f"{key}={shlex.quote(str(value))}")
except Exception as exc:  # noqa: BLE001 - shell caller needs one sanitized line.
    print(f"error: agent_admin restart launch plan failed for {project}/{seat}: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
)"; then
  echo "error: agent_admin launch plan failed for $PROJECT/$SEAT:" >&2
  printf '%s\n' "$plan_out" >&2
  exit 1
fi

eval "$plan_out"

if [[ -z "$TOOL" || -z "$AUTH_MODE" || -z "$WORKSPACE" ]]; then
  echo "error: incomplete launch plan from agent_admin:" >&2
  printf '%s\n' "$plan_out" >&2
  exit 1
fi

printf 'restart-seat:\n  project:       %s\n  seat:          %s\n  tool:          %s\n  auth:          %s\n  launcher_auth: %s\n  workspace:     %s\n  session:       %s\n' \
  "$PROJECT" "$SEAT" "$TOOL" "$AUTH_MODE" "$LAUNCHER_AUTH" "$WORKSPACE" "$SESSION_NAME"

if tmux_outer has-session -t "=$SESSION_NAME" 2>/dev/null; then
  echo "  status:    killing existing session"
  tmux_outer kill-session -t "=$SESSION_NAME"
else
  echo "  status:    no existing session"
fi

# Ensure the seat's Claude settings register our Stop hook so
# launcher_write_active_session_id fires on each clean exit (enabling
# auto-resume on next restart). Status line surfaces injected/present/skip.
hook_status="$(ensure_stop_hook "$WORKSPACE" "$REPO_ROOT" 2>/dev/null || true)"
echo "  stop-hook: ${hook_status:-skipped}"

# agent-launcher.sh --headless creates the tmux session itself (don't wrap
# in `tmux new-session`). It validates inputs, wires env (HOME, CODEX_HOME,
# etc.), and re-execs as --exec-agent inside the session it spawns.
launcher_cmd=(
  bash "$LAUNCHER"
  --headless
  --tool "$TOOL"
  --session "$SESSION_NAME"
  --auth "$LAUNCHER_AUTH"
  --dir "$WORKSPACE"
)
if [[ -n "$CUSTOM_ENV_FILE" ]]; then
  launcher_cmd+=(--custom-env-file "$CUSTOM_ENV_FILE")
fi
CLAWSEAT_PROJECT="$PROJECT" CLAWSEAT_SEAT="$ENGINEER_ID" \
  CLAWSEAT_PROVIDER="$PROVIDER" CLAWSEAT_ENGINEER_ID="$ENGINEER_ID" \
  CLAWSEAT_ENGINEER_PROFILE="$ENGINEER_PROFILE" REAL_HOME="$REAL_HOME_VALUE" \
  "${launcher_cmd[@]}" >/dev/null
CUSTOM_ENV_FILE_DELEGATED_TO_TMUX=1

# Verify the session came up.
for _ in 1 2 3 4 5; do
  sleep 0.5
  tmux_outer has-session -t "=$SESSION_NAME" 2>/dev/null && break
done

if ! tmux_outer has-session -t "=$SESSION_NAME" 2>/dev/null; then
  echo "error: tmux session $SESSION_NAME failed to come up" >&2
  exit 1
fi

echo "  result:    tmux session alive"

if [[ "$NO_WINDOW" == "0" ]]; then
  # Refresh the project grid so any iTerm pane re-attaches to the fresh
  # session. Tolerate failure — the tmux session is the durable artifact.
  if ! "$PYTHON_BIN" "$AGENT_ADMIN" window open-grid "$PROJECT" --quiet 2>&1; then
    echo "warn: window open-grid $PROJECT failed; tmux is alive — attach manually with:" >&2
    echo "  env -u TMUX -u TMUX_PANE $TMUX_BIN attach -t '=$SESSION_NAME'" >&2
  fi
fi

echo "done."
