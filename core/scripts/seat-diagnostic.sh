#!/usr/bin/env bash
set -uo pipefail

usage() {
  printf 'usage: bash core/scripts/seat-diagnostic.sh <project> <seat>\n' >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

PROJECT="$1"
SEAT="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REAL_HOME="${AGENT_HOME:-$HOME}"
AGENTS_ROOT="${AGENTS_ROOT:-$REAL_HOME/.agents}"
SESSIONS_ROOT="$AGENTS_ROOT/sessions"
PROJECTS_ROOT="$AGENTS_ROOT/projects"
ENGINEERS_ROOT="$AGENTS_ROOT/engineers"
WORKSPACES_ROOT="$AGENTS_ROOT/workspaces"
SECRETS_ROOT="$AGENTS_ROOT/secrets"
LEGACY_SECRETS_ROOT="$REAL_HOME/.agent-runtime/secrets"

SESSION_NAME=""
TOOL=""
AUTH_MODE=""
PROVIDER=""
PROVIDER_FAMILY=""
RUNTIME_DIR=""
SECRET_FILE=""

metadata="$(
  python3 - "$AGENTS_ROOT" "$PROJECT" "$SEAT" "$REPO_ROOT" <<'PY'
from __future__ import annotations

import shlex
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

agents_root = Path(sys.argv[1]).expanduser()
project = sys.argv[2]
seat = sys.argv[3]
# argv[4] is the shell-computed REPO_ROOT; use it directly so the inline
# Python block does not need __file__ (which is undefined in a heredoc).
repo_root = Path(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else agents_root.parent.parent


def load(path: Path) -> dict:
    try:
        if path.exists():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


session = load(agents_root / "sessions" / project / seat / "session.toml")
project_data = load(agents_root / "projects" / project / "project.toml")
last_harness = {}
for candidate in (
    agents_root / "workspaces" / project / seat / "last-harness.toml",
    agents_root / "engineers" / seat / "last-harness.toml",
):
    last_harness = load(candidate)
    if last_harness:
        break

seat_overrides = project_data.get("seat_overrides", {})
override = seat_overrides.get(seat, {}) if isinstance(seat_overrides, dict) else {}
if not isinstance(override, dict):
    override = {}

provider_name = (
    session.get("provider") or override.get("provider") or last_harness.get("provider") or ""
)

# Resolve provider family from the local provider registry so required_keys()
# can match by family (e.g. baidu-glm has family=minimax) rather than by
# provider name alone.  Fails silently so the diagnostic still works offline.
provider_family = ""
try:
    lib_path = str(repo_root / "core" / "lib")
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
    from providers import get_provider  # type: ignore[import]
    p = get_provider(provider_name)
    provider_family = str(getattr(p, "family", "") or "") if p else ""
except Exception:
    pass

fields = {
    "SESSION_NAME": session.get("session") or override.get("session_name") or "",
    "TOOL": session.get("tool") or override.get("tool") or last_harness.get("tool") or "",
    "AUTH_MODE": session.get("auth_mode") or override.get("auth_mode") or last_harness.get("auth_mode") or "",
    "PROVIDER": provider_name,
    "PROVIDER_FAMILY": provider_family,
    "RUNTIME_DIR": session.get("runtime_dir") or "",
    "SECRET_FILE": session.get("secret_file") or "",
}

for key, value in fields.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
)"
eval "$metadata"

resolve_session_name() {
  if [[ -n "$SESSION_NAME" ]]; then
    printf '%s\n' "$SESSION_NAME"
    return 0
  fi
  if command -v agentctl >/dev/null 2>&1; then
    agentctl session-name "$SEAT" --project "$PROJECT" 2>/dev/null && return 0
  fi
  if [[ -x "$REPO_ROOT/core/shell-scripts/agentctl.sh" ]]; then
    bash "$REPO_ROOT/core/shell-scripts/agentctl.sh" session-name "$SEAT" --project "$PROJECT" 2>/dev/null && return 0
  fi
  printf '%s-%s-%s\n' "$PROJECT" "$SEAT" "${TOOL:-claude}"
}

SESSION_NAME="$(resolve_session_name)"

env_value() {
  local file="$1"
  local key="$2"
  [[ -f "$file" ]] || return 1
  python3 - "$file" "$key" <<'PY'
from __future__ import annotations

import shlex
import sys
from pathlib import Path

path = Path(sys.argv[1])
wanted = sys.argv[2]
for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[len("export "):].strip()
    key, sep, value = line.partition("=")
    if not sep or key.strip() != wanted:
        continue
    value = value.strip()
    if value:
        try:
            parts = shlex.split(value, posix=True)
            print(parts[0] if parts else "")
        except ValueError:
            print(value.strip("\"'"))
    else:
        print("")
    raise SystemExit(0)
raise SystemExit(1)
PY
}

env_keys() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' "$file" \
    | sort -u \
    | paste -sd ', ' -
}

secret_candidates() {
  [[ -n "$SECRET_FILE" ]] && printf '%s\n' "$SECRET_FILE"
  [[ -n "$TOOL" && -n "$PROVIDER" ]] && printf '%s\n' "$SECRETS_ROOT/$TOOL/$PROVIDER/$SEAT.env"
  case "$TOOL:$PROVIDER" in
    codex:xcode-best) printf '%s\n' "$LEGACY_SECRETS_ROOT/codex/xcode.env" ;;
    claude:xcode-best) printf '%s\n' "$LEGACY_SECRETS_ROOT/claude/xcode.env" ;;
    claude:minimax) printf '%s\n' "$LEGACY_SECRETS_ROOT/claude/minimax.env" ;;
    claude:ark) printf '%s\n' "$LEGACY_SECRETS_ROOT/claude/ark.env" ;;
    gemini:google-api-key) printf '%s\n' "$LEGACY_SECRETS_ROOT/gemini/primary.env" ;;
  esac
}

resolve_secret_file() {
  local candidate
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(secret_candidates)
  return 1
}

SECRET_PATH="$(resolve_secret_file || true)"

provider_default_base_url() {
  case "$TOOL:$PROVIDER" in
    codex:xcode-best) printf 'https://api.xcode.best/v1\n' ;;
    claude:xcode-best) printf 'https://api.xcode.best/v1\n' ;;
    claude:minimax) printf 'https://api.minimaxi.com/anthropic\n' ;;
    claude:ark) printf 'https://ark.cn-beijing.volces.com/api/v3\n' ;;
    claude:anthropic-console) printf 'https://api.anthropic.com/v1\n' ;;
    gemini:google-api-key) printf 'https://generativelanguage.googleapis.com/v1beta\n' ;;
    *) return 1 ;;
  esac
}

endpoint_base_url() {
  local key value
  if [[ -n "$SECRET_PATH" ]]; then
    for key in OPENAI_BASE_URL OPENAI_API_BASE ANTHROPIC_BASE_URL GEMINI_BASE_URL GOOGLE_API_BASE BASE_URL; do
      value="$(env_value "$SECRET_PATH" "$key" 2>/dev/null || true)"
      if [[ -n "$value" ]]; then
        printf '%s\n' "$value"
        return 0
      fi
    done
  fi
  provider_default_base_url
}

models_url_for() {
  local base="${1%/}"
  case "$base" in
    */v1|*/v1beta|*/api/v3) printf '%s/models\n' "$base" ;;
    *) printf '%s/v1/models\n' "$base" ;;
  esac
}

endpoint_key() {
  local key value
  [[ -n "$SECRET_PATH" ]] || return 1
  for key in OPENAI_API_KEY ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ARK_API_KEY GEMINI_API_KEY GOOGLE_API_KEY; do
    value="$(env_value "$SECRET_PATH" "$key" 2>/dev/null || true)"
    if [[ -n "$value" ]]; then
      printf '%s=%s\n' "$key" "$value"
      return 0
    fi
  done
  return 1
}

required_keys() {
  # Match on PROVIDER_FAMILY first (populated from registry) so that providers
  # registered with family=minimax (e.g. baidu-glm / Qianfan) accept
  # ANTHROPIC_AUTH_TOKEN even when their provider name is not "minimax".
  case "$TOOL:$AUTH_MODE:${PROVIDER_FAMILY}:$PROVIDER" in
    codex:api:*:*)            printf 'OPENAI_API_KEY\n' ;;
    claude:api:minimax:*)     printf 'ANTHROPIC_AUTH_TOKEN\n' ;;
    claude:api:*:minimax)     printf 'ANTHROPIC_AUTH_TOKEN\n' ;;
    claude:api:ark:*)         printf 'ARK_API_KEY\n' ;;
    claude:api:*:ark)         printf 'ARK_API_KEY\n' ;;
    claude:api:*:*)           printf 'ANTHROPIC_API_KEY\n' ;;
    gemini:api:*:*)           printf 'GEMINI_API_KEY GOOGLE_API_KEY\n' ;;
    *) printf '\n' ;;
  esac
}

latest_file_in_dir() {
  local dir="$1"
  [[ -d "$dir" ]] || return 1
  find "$dir" -type f -print0 2>/dev/null \
    | xargs -0 ls -t 2>/dev/null \
    | head -1
}

log_candidates() {
  case "$TOOL" in
    codex)
      [[ -n "$RUNTIME_DIR" ]] && printf '%s\n' \
        "$RUNTIME_DIR/codex-home/log/codex-tui.log" \
        "$RUNTIME_DIR/codex/log/codex-tui.log" \
        "$RUNTIME_DIR/home/.codex/log/codex-tui.log"
      ;;
    claude)
      [[ -n "$RUNTIME_DIR" ]] && latest_file_in_dir "$RUNTIME_DIR/home/Library/Logs/Claude" || true
      latest_file_in_dir "$REAL_HOME/Library/Logs/Claude" || true
      ;;
    gemini)
      [[ -n "$RUNTIME_DIR" ]] && printf '%s\n' "$RUNTIME_DIR/home/.gemini/log/gemini-cli.log"
      [[ -n "$RUNTIME_DIR" ]] && latest_file_in_dir "$RUNTIME_DIR/home/.gemini/log" || true
      printf '%s\n' "$REAL_HOME/.gemini/log/gemini-cli.log"
      latest_file_in_dir "$REAL_HOME/.gemini/log" || true
      ;;
  esac
}

resolve_log_file() {
  local candidate
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(log_candidates)
  return 1
}

print_tmux_block() {
  printf '=== TMUX ===\n'
  printf 'project = %s\n' "$PROJECT"
  printf 'seat = %s\n' "$SEAT"
  printf 'session = %s\n' "$SESSION_NAME"
  if ! command -v tmux >/dev/null 2>&1; then
    printf 'tmux = <not found>\n\n'
    return
  fi

  # Unset TMUX / TMUX_PANE so all tmux calls below use the default server socket,
  # not the socket inherited from the calling Claude Code or tmux pane.
  # This mirrors send-and-verify.sh's "env -u TMUX" pattern and is the root
  # cause of "session_alive = no" when the diagnostic is run from inside a
  # planner pane whose TMUX env points to a different socket than the launcher.
  unset TMUX TMUX_PANE

  local err clients capture rc
  err="$(mktemp)"
  if tmux has-session -t "=$SESSION_NAME" 2>"$err"; then
    printf 'session_alive = yes\n'
  else
    rc=$?
    printf 'session_alive = no (rc=%s)\n' "$rc"
    sed 's/^/has-session stderr: /' "$err"
  fi
  rm -f "$err"

  clients="$(tmux list-clients -t "=$SESSION_NAME" 2>&1 || true)"
  if [[ -n "$clients" ]]; then
    printf 'clients = %s\n' "$(printf '%s\n' "$clients" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
    printf '%s\n' "$clients" | sed 's/^/client: /'
  else
    printf 'clients = 0\n'
  fi

  capture="$(tmux capture-pane -t "=$SESSION_NAME:" -p 2>&1 | tail -10 || true)"
  if [[ -n "$capture" ]]; then
    printf '%s\n' "$capture" | sed 's/^/pane: /'
  else
    printf 'pane: <empty or unavailable>\n'
  fi
  printf '\n'
}

print_log_block() {
  printf '=== LOG (tail 30) ===\n'
  printf 'tool = %s\n' "${TOOL:-<unknown>}"
  printf 'runtime_dir = %s\n' "${RUNTIME_DIR:-<unknown>}"

  local log_file candidates
  log_file="$(resolve_log_file || true)"
  if [[ -z "$log_file" ]]; then
    candidates="$(log_candidates | paste -sd ';' -)"
    printf '<no log file at %s>\n\n' "${candidates:-<no candidate path>}"
    return
  fi
  printf 'log_file = %s\n' "$log_file"
  tail -30 "$log_file" 2>&1 | sed 's/^/log: /'
  printf '\n'
}

print_endpoint_block() {
  printf '=== ENDPOINT ===\n'
  printf 'tool = %s\n' "${TOOL:-<unknown>}"
  printf 'auth_mode = %s\n' "${AUTH_MODE:-<unknown>}"
  printf 'provider = %s\n' "${PROVIDER:-<unknown>}"

  if [[ "$AUTH_MODE" != "api" ]]; then
    printf 'curl = skipped (auth_mode=%s; check OAuth status for this tool)\n\n' "${AUTH_MODE:-<unknown>}"
    return
  fi
  if ! command -v curl >/dev/null 2>&1; then
    printf 'curl = <not found>\n\n'
    return
  fi

  local base url key_pair key_name key_value code rc err
  base="$(endpoint_base_url 2>/dev/null || true)"
  if [[ -z "$base" ]]; then
    printf 'curl = skipped (no base URL found)\n\n'
    return
  fi
  url="$(models_url_for "$base")"
  printf 'models_url = %s\n' "$url"

  key_pair="$(endpoint_key 2>/dev/null || true)"
  err="$(mktemp)"
  if [[ -n "$key_pair" ]]; then
    key_name="${key_pair%%=*}"
    key_value="${key_pair#*=}"
    printf 'auth_key = %s\n' "$key_name"
    case "$key_name" in
      ANTHROPIC_API_KEY) code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -H "x-api-key: $key_value" "$url" 2>"$err")" ;;
      *) code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -H "Authorization: Bearer $key_value" "$url" 2>"$err")" ;;
    esac
  else
    printf 'auth_key = <missing>\n'
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$url" 2>"$err")"
  fi
  rc=$?
  if [[ $rc -eq 0 ]]; then
    printf 'http_code = %s\n' "${code:-<empty>}"
  else
    printf 'curl_error_rc = %s\n' "$rc"
    sed 's/^/curl stderr: /' "$err"
  fi
  rm -f "$err"
  printf '\n'
}

print_secrets_block() {
  printf '=== SECRETS ===\n'
  printf 'secret_file = %s\n' "${SECRET_PATH:-<not found>}"
  if [[ -z "$SECRET_PATH" ]]; then
    printf 'keys_present = <none>\n'
  else
    printf 'keys_present = %s\n' "$(env_keys "$SECRET_PATH" || printf '<none>')"
  fi

  local required key found_any=0 missing_any=0
  required="$(required_keys)"
  if [[ -z "$required" ]]; then
    printf 'required_keys = <none for auth_mode=%s>\n' "${AUTH_MODE:-<unknown>}"
    printf '\n'
    return
  fi
  printf 'required_keys = %s\n' "$required"
  for key in $required; do
    if [[ -n "$SECRET_PATH" ]] && env_value "$SECRET_PATH" "$key" >/dev/null 2>&1; then
      printf 'key %s = present\n' "$key"
      found_any=1
    else
      printf 'key %s = missing\n' "$key"
      missing_any=1
    fi
  done
  if [[ $found_any -eq 1 && "$TOOL" == "gemini" ]]; then
    missing_any=0
  fi
  if [[ $missing_any -eq 0 ]]; then
    printf 'secret_status = ok\n'
  else
    printf 'secret_status = missing-required-key\n'
  fi
  printf '\n'
}

print_tmux_block
print_log_block
print_endpoint_block
print_secrets_block
