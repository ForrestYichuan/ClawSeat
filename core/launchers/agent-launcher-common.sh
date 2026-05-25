#!/usr/bin/env bash

set -euo pipefail

launcher_state_store_path() {
  local launcher_home default_store desktop_store
  launcher_home="${REAL_HOME:-$HOME}"
  if [[ -n "${LAUNCHER_STATE_STORE:-}" ]]; then
    printf '%s\n' "$LAUNCHER_STATE_STORE"
    return
  fi
  desktop_store="$launcher_home/Desktop/.agent-launcher-state.json"
  # Prefer legacy Desktop path only when it exists AND is writable.
  # An unwritable Desktop file (macOS TCC/permission denial) falls through to
  # the XDG config default so the launcher is not killed by a permission error.
  if [[ -f "$desktop_store" && -w "$desktop_store" ]]; then
    printf '%s\n' "$desktop_store"
    return
  fi
  default_store="$launcher_home/.config/clawseat/launcher-state.json"
  mkdir -p "$(dirname "$default_store")"
  printf '%s\n' "$default_store"
}

launcher_remember_recent_dir() {
  local path="$1"
  local store
  store="$(launcher_state_store_path)"
  python3 - "$store" "$path" <<'PY'
import json
import os
import sys

store_path, raw_path = sys.argv[1:3]
path = os.path.realpath(os.path.expanduser(raw_path))
if not os.path.isdir(path):
    raise SystemExit(0)

data = {}
if os.path.exists(store_path):
    try:
        with open(store_path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
            if isinstance(loaded, dict):
                data = loaded
    except Exception:
        data = {}

recent = [
    entry
    for entry in data.get("recent_dirs", [])
    if isinstance(entry, str) and entry != path and os.path.isdir(entry)
]
recent.insert(0, path)
data["recent_dirs"] = recent[:12]

# Best-effort write: permission failure must not abort a seat launch.
try:
    with open(store_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
except OSError:
    pass
PY
}

launcher_resolve_directory_path() {
  local raw_path="$1"
  python3 - "$raw_path" <<'PY'
from pathlib import Path
import sys

raw = sys.argv[1].strip()
if not raw:
    raise SystemExit(1)

path = Path(raw).expanduser()
if not path.is_dir():
    raise SystemExit(1)

print(path.resolve())
PY
}

launcher_slugify() {
  local raw="${1:-session}"
  local slug
  slug="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' | tr -cs '[:alnum:]' '-')"
  slug="${slug#-}"
  slug="${slug%-}"
  if [[ -z "$slug" ]]; then
    slug="session"
  fi
  printf '%s\n' "$slug"
}

launcher_active_session_dir() {
  local launcher_home="${REAL_HOME:-${HOME:-}}"
  [[ -n "$launcher_home" ]] || return 1
  printf '%s\n' "$launcher_home/.agent-runtime/active"
}

launcher_active_session_file() {
  local seat="${1:-${CLAWSEAT_SEAT:-}}"
  local active_dir=""
  [[ -n "$seat" ]] || return 1
  active_dir="$(launcher_active_session_dir)"
  printf '%s/%s.session\n' "$active_dir" "$seat"
}

launcher_read_active_session_id() {
  local seat="${1:-${CLAWSEAT_SEAT:-}}"
  local active_file=""
  active_file="$(launcher_active_session_file "$seat")" || return 1
  [[ -f "$active_file" ]] || return 1
  python3 - "$active_file" <<'PY'
import re
import sys
from pathlib import Path

# Only return session IDs that look like real Codex UUIDs (019xxxxx-... format)
# or standard UUIDs. Plain words like "session-123" are stale/corrupt → ignore
# and clear the file so the next launch starts a fresh session instead of
# repeatedly trying to resume a non-existent one.
_VALID_SESSION_RE = re.compile(
    r'^[0-9a-f]{8,}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

path = Path(sys.argv[1])
try:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
except OSError:
    raise SystemExit(1)
if text and _VALID_SESSION_RE.match(text):
    print(text)
elif text:
    # Invalid format — clear the file so future launches don't repeat the error
    try:
        path.write_text("", encoding="utf-8")
    except OSError:
        pass
PY
}

launcher_write_active_session_id() {
  local seat="${1:-${CLAWSEAT_SEAT:-}}"
  local session_id="${2:-}"
  local active_file=""
  local active_dir=""
  [[ -n "$seat" ]] || return 0
  [[ -n "$session_id" ]] || return 0
  active_file="$(launcher_active_session_file "$seat")" || return 0
  active_dir="$(dirname "$active_file")"
  mkdir -p "$active_dir" 2>/dev/null || return 0
  printf '%s\n' "$session_id" >"$active_file" 2>/dev/null || return 0
  chmod 600 "$active_file" 2>/dev/null || true
}

launcher_resume_banner() {
  local session_id="${1:-<unknown>}"
  local when="${2:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
  printf 'Resuming session %s from %s\n' "$session_id" "$when"
}

# Crash-recovery fallbacks for auto-resume. The .session file only gets
# written when the Stop hook fires (clean exit). When the seat dies hard
# (tmux killed, server reaped, system reboot), the hook never runs — so
# we read the tool's own cwd-scoped session store instead and fall back
# to --continue / resume --last. Both tools write their session files
# continuously as the conversation progresses, surviving any crash.

_has_claude_cwd_history() {
  local home_for_claude="$1" cwd="$2"
  [[ -n "$home_for_claude" && -n "$cwd" ]] || return 1
  # Claude Code stores sessions at ~/.claude/projects/<encoded>/*.jsonl
  # where <encoded> = cwd with both '/' AND '.' replaced by '-'. Confirmed
  # against on-disk samples: /home/u/.agents/... -> -home-u--agents-...
  # (the double-dash comes from /. -> --).
  cwd="${cwd%/}"
  local encoded
  encoded="$(printf '%s\n' "$cwd" | tr '/.' '--')"
  compgen -G "$home_for_claude/.claude/projects/${encoded}/*.jsonl" >/dev/null 2>&1
}

_has_codex_history() {
  local codex_home="$1"
  [[ -n "$codex_home" && -d "$codex_home/sessions" ]] || return 1
  # `codex resume --last` filters by cwd internally, so we just need to
  # confirm any rollout exists. Empty result → no recoverable history.
  [[ -n "$(find "$codex_home/sessions" -maxdepth 5 -name 'rollout-*.jsonl' -print -quit 2>/dev/null)" ]]
}
