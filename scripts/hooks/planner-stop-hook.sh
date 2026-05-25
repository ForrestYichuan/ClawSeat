#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_CLAWSEAT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLAWSEAT_ROOT="${CLAWSEAT_ROOT:-$DEFAULT_CLAWSEAT_ROOT}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAX_CHARS="${PLANNER_STOP_HOOK_MAX_CHARS:-18000}"

[[ "${PLANNER_STOP_HOOK_ENABLED:-1}" == "1" ]] || exit 0

parse_payload() {
  local payload_json="$1"
  HOOK_PAYLOAD_JSON="$payload_json" HOOK_MAX_CHARS="$MAX_CHARS" HOOK_CLAWSEAT_ROOT="$CLAWSEAT_ROOT" "$PYTHON_BIN" - <<'PY'
import json
import os
import re
import shlex
import sys
from pathlib import Path


def emit(name: str, value: str) -> None:
    print(f"{name}={shlex.quote(value)}")


raw = os.environ.get("HOOK_PAYLOAD_JSON", "").strip()
if not raw:
    raise SystemExit(0)
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    raise SystemExit(0)

clawseat_root = Path(os.environ.get("HOOK_CLAWSEAT_ROOT", "")).expanduser()
core_lib = clawseat_root / "core" / "lib"
if core_lib.exists():
    text = str(core_lib)
    if text not in sys.path:
        sys.path.insert(0, text)

try:
    from real_home import real_user_home  # type: ignore
except Exception:  # pragma: no cover - best effort for hook runtime
    def real_user_home() -> Path:  # type: ignore[redef]
        return Path.home()


last_assistant_message = str(payload.get("last_assistant_message", "") or "")
transcript_path = str(payload.get("transcript_path", "") or "").strip()
cwd = str(payload.get("cwd", "") or "").strip()

text = last_assistant_message
if not text and transcript_path:
    try:
        text = Path(transcript_path).expanduser().read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
if not text:
    raise SystemExit(0)

max_chars = int(os.environ.get("HOOK_MAX_CHARS", "18000"))
if len(text) > max_chars:
    half = max_chars // 2
    omitted = len(text) - max_chars
    text = f"{text[:half]}\n...[omitted {omitted} chars]...\n{text[-half:]}"

project = str(os.environ.get("CLAWSEAT_PROJECT", "") or "").strip()
binding_path = ""
pattern = re.compile(r"(?P<agents_root>.+/\.agents)/workspaces/(?P<project>[^/]+)/planner(?:/.*)?$")
pattern_any_seat = re.compile(r"(?P<agents_root>.+/\.agents)/workspaces/(?P<project>[^/]+)/(?P<seat>[^/]+)(?:/.*)?$")
for candidate in (cwd, os.environ.get("CLAUDE_PROJECT_DIR", ""), os.getcwd()):
    candidate = str(candidate or "").strip()
    if not candidate:
        continue
    resolved = str(Path(candidate).expanduser())
    match = pattern.search(resolved)
    if not match:
        match = pattern_any_seat.search(resolved)
    if not match:
        continue
    if not project:
        project = match.group("project")
    binding_path = str(Path(match.group("agents_root")) / "tasks" / project / "PROJECT_BINDING.toml")
    break

if project and not binding_path:
    binding_path = str(real_user_home() / ".agents" / "tasks" / project / "PROJECT_BINDING.toml")

emit("PLANNER_HOOK_TEXT", text)
emit("PLANNER_HOOK_PROJECT", project)
emit("PLANNER_HOOK_BINDING_PATH", binding_path)
emit("PLANNER_HOOK_WORKSPACE", cwd or os.environ.get("CLAUDE_PROJECT_DIR", "") or os.getcwd())
PY
}

read_group_id() {
  local binding_path="$1"
  "$PYTHON_BIN" - "$binding_path" <<'PY' || true
import sys
from pathlib import Path
try:
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

path = Path(sys.argv[1]).expanduser()
if not path.is_file():
    raise SystemExit(0)
try:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)

group_id = str(data.get("feishu_group_id") or "").strip()
if not group_id:
    bridge = data.get("bridge") or {}
    if isinstance(bridge, dict):
        group_id = str(bridge.get("group_id") or "").strip()
print(group_id)
PY
}

send_broadcast() {
  local project="$1" group_id="$2" text="$3"
  local body="[planner@${project}]"
  body+=$'\n'
  body+="$text"
  lark-cli im +messages-send --chat-id "$group_id" --text "$body" 2>&1 | while IFS= read -r line; do
    echo "[planner-hook] $line" >&2
  done || true
}

run_auto_compact() {
  local project="$1" text="$2" workspace="$3"
  local helper="$CLAWSEAT_ROOT/core/skills/planner/scripts/planner_auto_compact.py"
  [[ "${PLANNER_AUTO_COMPACT_ENABLED:-1}" == "1" ]] || return 0
  [[ -f "$helper" ]] || { echo "[planner-hook] planner_auto_compact helper missing; skip" >&2; return 0; }
  PLANNER_AUTO_COMPACT_PROJECT="$project" \
  PLANNER_AUTO_COMPACT_TEXT="$text" \
  PLANNER_AUTO_COMPACT_WORKSPACE="$workspace" \
  "$PYTHON_BIN" "$helper" 2>&1 | while IFS= read -r line; do
    echo "[planner-hook] $line" >&2
  done || true
}

main() {
  local payload_json="" parsed="" project="" binding_path="" group_id="" workspace=""
  payload_json="$(cat || true)"
  [[ -n "$payload_json" ]] || exit 0

  parsed="$(parse_payload "$payload_json" || true)"
  [[ -n "$parsed" ]] || exit 0
  eval "$parsed"

  project="${PLANNER_HOOK_PROJECT:-}"
  binding_path="${PLANNER_HOOK_BINDING_PATH:-}"
  workspace="${PLANNER_HOOK_WORKSPACE:-${CLAUDE_PROJECT_DIR:-$PWD}}"
  [[ -n "$project" ]] || { echo "[planner-hook] no project resolvable; skip" >&2; exit 0; }

  run_auto_compact "$project" "${PLANNER_HOOK_TEXT:-}" "$workspace"

  [[ -n "$binding_path" && -f "$binding_path" ]] || { echo "[planner-hook] no PROJECT_BINDING.toml; skip" >&2; exit 0; }

  [[ "${CLAWSEAT_FEISHU_ENABLED:-1}" == "0" ]] && { echo "[planner-hook] CLAWSEAT_FEISHU_ENABLED=0; skip" >&2; exit 0; }

  group_id="$(read_group_id "$binding_path")"
  [[ -n "$group_id" ]] || { echo "[planner-hook] no feishu_group_id; skip" >&2; exit 0; }

  command -v lark-cli >/dev/null 2>&1 || { echo "[planner-hook] lark-cli not installed; skip" >&2; exit 0; }

  send_broadcast "$project" "$group_id" "${PLANNER_HOOK_TEXT:-}" || true
}

main "$@" || true
exit 0
