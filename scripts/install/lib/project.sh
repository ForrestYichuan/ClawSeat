#!/usr/bin/env bash
# shellcheck shell=bash
# Loaded by scripts/install.sh. Resolve this file with BASH_SOURCE so
# callers may source install.sh from any current working directory.
_CLAWSEAT_INSTALL_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REINSTALL_ROLLBACK_ARMED=0
REINSTALL_BACKUP_SUFFIX=""
REINSTALL_PROJECT_TOML_BACKUP=""
REINSTALL_PROFILE_BACKUP=""
REINSTALL_PROJECT_LOCAL_BACKUP=""
REINSTALL_AGENTS_SECRETS_BACKUP=""
REINSTALL_AGENT_RUNTIME_SECRETS_BACKUP=""
REINSTALL_PROJECT_TOML_EXISTED=0
REINSTALL_PROFILE_EXISTED=0
REINSTALL_TEMPLATE_CHANGED=0
REINSTALL_PREVIOUS_TEMPLATE_NAME=""
REINSTALL_PREVIOUS_MEMORY_TOOL=""
REINSTALL_PREVIOUS_MEMORY_MODEL=""
REINSTALL_PROJECT_LOCAL_EXISTED=0
REINSTALL_AGENTS_SECRETS_EXISTED=0
REINSTALL_AGENT_RUNTIME_SECRETS_EXISTED=0

_prompt_i18n_get() {
  local key="$1" fallback="${2:-$1}"
  if declare -F i18n_get >/dev/null 2>&1; then
    i18n_get "$key"
  else
    printf '%s\n' "$fallback"
  fi
}

prompt_template_for_choice() {
  case "${1:-1}" in
    ""|1) printf '%s\n' "clawseat-engineering" ;;
    2) printf '%s\n' "clawseat-creative" ;;
    3) printf '%s\n' "clawseat-minimal" ;;
    *) return 1 ;;
  esac
}

prompt_placeholder_for_template() {
  case "$1" in
    clawseat-engineering) printf '%s\n' "e.g. coding-project, webapp" ;;
    clawseat-creative)    printf '%s\n' "e.g. cartooner-project, creative-campaign" ;;
    clawseat-minimal|clawseat-solo) printf '%s\n' "e.g. minimal-project, cartooner-front" ;;
    *)                    printf '%s\n' "e.g. myproject, experiment-01" ;;
  esac
}

prompt_kind_first_flow() {
  # Skip when either flag was explicitly provided.
  [[ "$_PROJECT_EXPLICIT" == "0" && "$_TEMPLATE_EXPLICIT" == "0" ]] || return 0
  if [[ ! -t 0 || ! -t 1 ]]; then
    die 2 NON_TTY_NO_TEMPLATE \
      "non-TTY environment detected; use --template <name>. Run: bash scripts/install.sh --help"
  fi

  printf '\n%s\n' "$(_prompt_i18n_get kind_first_title 'ClawSeat — 新项目配置 / New project setup')" >&2
  printf '\n%s\n' "$(_prompt_i18n_get kind_first_prompt '选择项目类型 / Choose project mode:')" >&2
  printf '  %s\n' "$(_prompt_i18n_get kind_first_creative '1) 创作项目 (5 seat: memory + planner + builder + patrol + designer)  [default]')" >&2
  printf '  %s\n' "$(_prompt_i18n_get kind_first_engineering '2) 工程项目 (6 seat: + reviewer 独立审查)')" >&2
  printf '  %s\n' "$(_prompt_i18n_get kind_first_solo '3) 极简项目组 (v3 multi-team: memory + planner+builder subteam + quality-docs)')" >&2
  printf '  %s\n' "$(_prompt_i18n_get kind_first_cartooner '4) Cartooner 创作席位 (4 seat: memory+writer+visual+patrol，工具混合)')" >&2
  printf '%s\n' "$(_prompt_i18n_get kind_first_recommend '推荐★：创作项目；理由：覆盖 planner/builder/patrol/designer，最适合首次安装。')" >&2

  local _kind=""
  while true; do
    printf '%s' "$(_prompt_i18n_get kind_first_choice '选择 [1-4, Enter=1]: ')" >&2
    read -r _kind < /dev/tty
    if CLAWSEAT_TEMPLATE_NAME="$(prompt_template_for_choice "$_kind")"; then
      break
    fi
    printf '%s\n' "$(_prompt_i18n_get kind_first_invalid '请输入 1、2、3 或 4 (回车 = 1 创作项目)')" >&2
  done

  local _placeholder
  _placeholder="$(prompt_placeholder_for_template "$CLAWSEAT_TEMPLATE_NAME")"

  local _name="" _attempt=0
  while [[ $_attempt -lt 3 ]]; do
    _attempt=$((_attempt + 1))
    printf '\n%s (%s): ' "$(_prompt_i18n_get project_name_prompt '项目名')" "$_placeholder" >&2
    read -r _name < /dev/tty
    if [[ "$_name" =~ ^[a-z0-9-]+$ ]]; then
      PROJECT="$_name"
      compute_project_paths
      return 0
    fi
    printf '%s\n' "$(_prompt_i18n_get invalid_project_name '无效：项目名必须匹配 ^[a-z0-9-]+$')" >&2
  done
  die 2 INVALID_PROJECT "项目名 3 次输入均无效，请用 --project 传入合法名称"
}

resolve_pending_seats() {
  # PRIMARY_SEAT_ID is the seat the user dialogs with. Current canonical
  # templates are v2 memory-primary; PENDING_SEATS is everyone else (workers).
  local template_file="$REPO_ROOT/templates/${CLAWSEAT_TEMPLATE_NAME}.toml"
  if [[ ! -f "$template_file" ]]; then
    PRIMARY_SEAT_ID="memory"
    return 0
  fi
  local primary seats
  primary="$("$PYTHON_BIN" - "$template_file" <<'PY'
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib
with open(sys.argv[1], "rb") as f:
    data = tomllib.load(f)
PRIMARY_IDS = ("ancestor", "memory")
for e in data.get("engineers", []):
    if e.get("id") in PRIMARY_IDS:
        print(e["id"])
        break
PY
  2>/dev/null)"
  PRIMARY_SEAT_ID="${primary:-memory}"

  seats="$("$PYTHON_BIN" - "$template_file" "$PRIMARY_SEAT_ID" <<'PY'
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib
with open(sys.argv[1], "rb") as f:
    data = tomllib.load(f)
primary = sys.argv[2]
seats = [e["id"] for e in data.get("engineers", []) if e.get("id") != primary]
print(" ".join(seats))
PY
  2>/dev/null)"
  [[ -n "$seats" ]] && read -ra PENDING_SEATS <<< "$seats"
}

_engineers_from_template() {
  local template_file="${1:-$REPO_ROOT/templates/${CLAWSEAT_TEMPLATE_NAME}.toml}"
  [[ -f "$template_file" ]] || return 1
  "$PYTHON_BIN" - "$template_file" <<'PY'
from __future__ import annotations

import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

with open(sys.argv[1], "rb") as handle:
    data = tomllib.load(handle)

ids: list[str] = []
for item in data.get("engineers", []):
    seat_id = str(item.get("id", "")).strip()
    if not seat_id:
        continue
    if seat_id not in ids:
        ids.append(seat_id)
print(" ".join(ids))
PY
}

_existing_project_repo_root() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  "$PYTHON_BIN" - "$path" <<'PY'
from __future__ import annotations

import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

with open(sys.argv[1], "rb") as handle:
    data = tomllib.load(handle)
repo_root = str(data.get("repo_root") or "").strip()
if repo_root:
    print(repo_root)
PY
}

_backup_reinstall_file() {
  local path="$1" existed_var="$2" backup_var="$3" backup_path=""
  printf -v "$existed_var" '%s' 0
  printf -v "$backup_var" '%s' ""
  [[ -f "$path" ]] || return 0
  backup_path="${path}.bak.${REINSTALL_BACKUP_SUFFIX}"
  cp "$path" "$backup_path" \
    || die 31 REINSTALL_BACKUP_FAILED "unable to backup $path"
  printf -v "$existed_var" '%s' 1
  printf -v "$backup_var" '%s' "$backup_path"
}

_restore_reinstall_file() {
  local path="$1" existed="$2" backup="$3"
  if [[ "$existed" == "1" && -n "$backup" && -f "$backup" ]]; then
    mkdir -p "$(dirname "$path")"
    cp "$backup" "$path" || true
  elif [[ "$existed" == "0" ]]; then
    rm -f "$path" || true
  fi
}

_backup_reinstall_dir() {
  local path="$1" existed_var="$2" backup_var="$3" backup_path=""
  printf -v "$existed_var" '%s' 0
  printf -v "$backup_var" '%s' ""
  [[ -d "$path" ]] || return 0
  backup_path="${path}.bak.${REINSTALL_BACKUP_SUFFIX}"
  cp -a "$path" "$backup_path" || die 31 REINSTALL_BACKUP_FAILED "unable to backup $path"
  printf -v "$existed_var" '%s' 1
  printf -v "$backup_var" '%s' "$backup_path"
}

_restore_reinstall_dir() {
  local path="$1" existed="$2" backup="$3"
  if [[ "$existed" == "1" && -n "$backup" && -d "$backup" ]]; then
    rm -rf "$path" || true
    cp -a "$backup" "$path" || true
  elif [[ "$existed" == "0" ]]; then
    rm -rf "$path" || true
  fi
}

_rollback_reinstall_project() {
  [[ "$REINSTALL_ROLLBACK_ARMED" == "1" ]] || return 0
  warn "reinstall failed; restoring project backups for $PROJECT"
  _restore_reinstall_file "$HOME/.agents/tasks/$PROJECT/project-local.toml" "$REINSTALL_PROJECT_LOCAL_EXISTED" "$REINSTALL_PROJECT_LOCAL_BACKUP"
  _restore_reinstall_file "$HOME/.agents/profiles/${PROJECT}-profile-dynamic.toml" "$REINSTALL_PROFILE_EXISTED" "$REINSTALL_PROFILE_BACKUP"
  _restore_reinstall_dir "$HOME/.agents/secrets" "$REINSTALL_AGENTS_SECRETS_EXISTED" "$REINSTALL_AGENTS_SECRETS_BACKUP"
  _restore_reinstall_dir "$HOME/.agent-runtime/secrets" "$REINSTALL_AGENT_RUNTIME_SECRETS_EXISTED" "$REINSTALL_AGENT_RUNTIME_SECRETS_BACKUP"
  REINSTALL_ROLLBACK_ARMED=0
}

_clear_reinstall_backups() {
  REINSTALL_ROLLBACK_ARMED=0
}

_reinstall_exit_trap() {
  local rc="$1"
  if [[ "$rc" != "0" ]]; then
    _rollback_reinstall_project
  fi
  exit "$rc"
}

_kill_existing_project_sessions() {
  local project="${1:-$PROJECT}" session
  if command -v tmux >/dev/null 2>&1; then
    while IFS= read -r session; do
      [[ -n "$session" ]] || continue
      case "$session" in
        "$project"-*) tmux kill-session -t "=$session" >/dev/null 2>&1 || true ;;
      esac
    done < <(tmux list-sessions -F '#S' 2>/dev/null || true)
  fi

  if command -v osascript >/dev/null 2>&1; then
    osascript >/dev/null 2>&1 <<OSA || true
tell application "iTerm2"
  repeat with w in windows
    try
      if (name of w as text) contains "clawseat-${project}" then close w
    end try
  end repeat
end tell
OSA
  fi
}

read_reinstall_project_metadata() {
  REINSTALL_PREVIOUS_TEMPLATE_NAME=""
  REINSTALL_PREVIOUS_MEMORY_TOOL=""
  REINSTALL_PREVIOUS_MEMORY_MODEL=""
  REINSTALL_TEMPLATE_CHANGED=0
  [[ -f "$PROJECT_RECORD_PATH" ]] || return 0
  local metadata_line="" output=""
  output="$(
    "$PYTHON_BIN" - "$PROJECT_RECORD_PATH" <<'PY'
from __future__ import annotations

import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

with open(sys.argv[1], "rb") as handle:
    data = tomllib.load(handle)

template_name = data.get("template_name", "").strip()
seat_overrides = data.get("seat_overrides", {}) or {}
memory_override = seat_overrides.get("memory", {})
memory_tool = str(memory_override.get("tool", "")).strip()
memory_model = str(memory_override.get("model", "")).strip()
print(f"template_name={template_name}")
print(f"memory_tool={memory_tool}")
print(f"memory_model={memory_model}")
PY
  )"
  while IFS= read -r metadata_line; do
    case "$metadata_line" in
      template_name=*) REINSTALL_PREVIOUS_TEMPLATE_NAME="${metadata_line#template_name=}" ;;
      memory_tool=*) REINSTALL_PREVIOUS_MEMORY_TOOL="${metadata_line#memory_tool=}" ;;
      memory_model=*) REINSTALL_PREVIOUS_MEMORY_MODEL="${metadata_line#memory_model=}" ;;
    esac
  done <<< "$output"
  if [[ -n "$REINSTALL_PREVIOUS_TEMPLATE_NAME" && "$REINSTALL_PREVIOUS_TEMPLATE_NAME" != "$CLAWSEAT_TEMPLATE_NAME" ]]; then
    REINSTALL_TEMPLATE_CHANGED=1
  else
    REINSTALL_TEMPLATE_CHANGED=0
  fi
}

_reinstall_project() {
  local existing_repo_root="" profile_path="$HOME/.agents/profiles/${PROJECT}-profile-dynamic.toml"
  read_reinstall_project_metadata
  if [[ ! -f "$PROJECT_RECORD_PATH" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
      warn "project.toml missing for $PROJECT (dry-run); continuing reinstall dry-run bootstrap path"
      return 0
    fi
    if [[ -f "$STATUS_FILE" ]]; then
      warn "project.toml missing for $PROJECT; treating --reinstall as repair from existing task state"
      _kill_existing_project_sessions "$PROJECT"
      rm -rf "$HOME/.agents/sessions/$PROJECT"
      return 0
    fi
    die 31 REINSTALL_PROJECT_MISSING "cannot reinstall missing project: $PROJECT_RECORD_PATH"
  fi

  if [[ -z "$FORCE_REPO_ROOT" ]]; then
    existing_repo_root="$(_existing_project_repo_root "$PROJECT_RECORD_PATH" 2>/dev/null || true)"
    if [[ -n "$existing_repo_root" ]]; then
      PROJECT_REPO_ROOT="$existing_repo_root"
    fi
  fi

  REINSTALL_BACKUP_SUFFIX="$(date +%Y%m%d-%H%M%S)"
  local project_local_path="${HOME}/.agents/tasks/${PROJECT}/project-local.toml"
  _backup_reinstall_file "$PROJECT_RECORD_PATH" REINSTALL_PROJECT_TOML_EXISTED REINSTALL_PROJECT_TOML_BACKUP
  _backup_reinstall_file "$profile_path" REINSTALL_PROFILE_EXISTED REINSTALL_PROFILE_BACKUP
  _backup_reinstall_file "$project_local_path" REINSTALL_PROJECT_LOCAL_EXISTED REINSTALL_PROJECT_LOCAL_BACKUP
  _backup_reinstall_dir "$HOME/.agents/secrets" REINSTALL_AGENTS_SECRETS_EXISTED REINSTALL_AGENTS_SECRETS_BACKUP
  _backup_reinstall_dir "$HOME/.agent-runtime/secrets" REINSTALL_AGENT_RUNTIME_SECRETS_EXISTED REINSTALL_AGENT_RUNTIME_SECRETS_BACKUP
  REINSTALL_ROLLBACK_ARMED=1

  if [[ "$MEMORY_TOOL_EXPLICIT" != "1" && -n "$REINSTALL_PREVIOUS_MEMORY_TOOL" ]]; then
    case "$REINSTALL_PREVIOUS_MEMORY_TOOL" in
      codex|gemini)
        warn "[install] reinstall: reusing existing project memory tool: ${REINSTALL_PREVIOUS_MEMORY_TOOL}"
        MEMORY_TOOL="$REINSTALL_PREVIOUS_MEMORY_TOOL"
        MEMORY_TOOL_EXPLICIT=1
        if [[ "$REINSTALL_PREVIOUS_MEMORY_TOOL" == "codex" && -n "$REINSTALL_PREVIOUS_MEMORY_MODEL" && "$MEMORY_MODEL_EXPLICIT" == "0" ]]; then
          MEMORY_MODEL="$REINSTALL_PREVIOUS_MEMORY_MODEL"
        fi
        ;;
    esac
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] reinstall %s: backup project.toml/profile, kill old sessions/windows, re-bootstrap\n' "$PROJECT"
    return 0
  fi

  _kill_existing_project_sessions "$PROJECT"
  rm -f "$PROJECT_RECORD_PATH"
  rm -rf "$HOME/.agents/sessions/$PROJECT"
  note "[install] reinstall prepared for $PROJECT (backup suffix: $REINSTALL_BACKUP_SUFFIX)"
}

_restore_reinstall_project_seat_overrides() {
  [[ "$DRY_RUN" == "1" ]] && return 0
  [[ "$REINSTALL_PROJECT_TOML_EXISTED" == "1" ]] || return 0
  [[ -n "$REINSTALL_PROJECT_TOML_BACKUP" && -f "$REINSTALL_PROJECT_TOML_BACKUP" ]] || return 0
  [[ -f "$PROJECT_RECORD_PATH" ]] || return 0
  "$PYTHON_BIN" - "$REINSTALL_PROJECT_TOML_BACKUP" "$PROJECT_RECORD_PATH" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

backup_path = Path(sys.argv[1])
current_path = Path(sys.argv[2])

section_re = re.compile(r"^\[seat_overrides\.([^\]]+)\]\s*$", re.MULTILINE)


def extract_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in section_re.finditer(text):
        seat = match.group(1)
        after = text[match.end():]
        next_header = re.search(r"^\[", after, re.MULTILINE)
        block_end = match.end() + (next_header.start() if next_header else len(after))
        block = text[match.start():block_end].rstrip("\n") + "\n\n"
        blocks.append((seat, block))
    return blocks


def upsert_block(text: str, seat: str, block: str) -> str:
    match = re.search(rf"^\[seat_overrides\.{re.escape(seat)}\]\s*$", text, re.MULTILINE)
    if match:
        after = text[match.end():]
        next_header = re.search(r"^\[", after, re.MULTILINE)
        block_end = match.end() + (next_header.start() if next_header else len(after))
        return text[:match.start()] + block + text[block_end:]
    return text.rstrip("\n") + "\n\n" + block


backup_text = backup_path.read_text(encoding="utf-8")
current_text = current_path.read_text(encoding="utf-8")
merged = current_text
for seat, block in extract_blocks(backup_text):
    merged = upsert_block(merged, seat, block)
if merged != current_text:
    current_path.write_text(merged, encoding="utf-8")
PY
}

memory_primary_uses_codex() {
  [[ "$PRIMARY_SEAT_ID" == "memory" && "$(primary_effective_tool)" == "codex" ]]
}

memory_primary_uses_gemini() {
  [[ "$PRIMARY_SEAT_ID" == "memory" && "$(primary_effective_tool)" == "gemini" ]]
}

memory_primary_skips_claude_provider() {
  [[ "$PRIMARY_SEAT_ID" == "memory" && "$(primary_effective_tool)" != "claude" ]]
}

project_record_memory_model() {
  [[ -f "$PROJECT_RECORD_PATH" ]] || return 0
  "$PYTHON_BIN" - "$PROJECT_RECORD_PATH" <<'PY'
from __future__ import annotations

import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

with open(sys.argv[1], "rb") as handle:
    data = tomllib.load(handle)

seat_overrides = data.get("seat_overrides", {}) or {}
memory_override = seat_overrides.get("memory") or {}
memory_model = memory_override.get("model")
if memory_model:
    print(str(memory_model))
PY
}

memory_effective_model() {
  local seat_memory_model=""
  case "$MEMORY_TOOL" in
    codex)
      if [[ "$MEMORY_MODEL_EXPLICIT" == "0" ]]; then
        seat_memory_model="$(project_record_memory_model || true)"
        if [[ -n "$seat_memory_model" ]]; then
          printf '%s\n' "$seat_memory_model"
          return 0
        fi
      fi
      printf '%s\n' "$MEMORY_MODEL"
      ;;
    gemini)
      [[ "$MEMORY_MODEL_EXPLICIT" == "1" ]] && printf '%s\n' "$MEMORY_MODEL" || true
      ;;
    *) return 0 ;;
  esac
}

primary_effective_tool() {
  local template_tool template_auth template_provider template_model
  read -r template_tool template_auth template_provider template_model < <(
    template_seat_config "$PRIMARY_SEAT_ID" 2>/dev/null || printf 'claude oauth anthropic \n'
  )
  if [[ "$PRIMARY_SEAT_ID" == "memory" && "$MEMORY_TOOL_EXPLICIT" == "1" ]]; then
    printf '%s\n' "$MEMORY_TOOL"
  else
    printf '%s\n' "${template_tool:-claude}"
  fi
}

template_seat_config() {
  local seat="$1"
  local template_file="$REPO_ROOT/templates/${CLAWSEAT_TEMPLATE_NAME}.toml"
  [[ -f "$template_file" ]] || return 1
  "$PYTHON_BIN" - "$template_file" "$seat" <<'PY'
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib

with open(sys.argv[1], "rb") as f:
    data = tomllib.load(f)
target = sys.argv[2]
for e in data.get("engineers", []):
    if e.get("id") == target:
        print(
            e.get("tool", "claude"),
            e.get("auth_mode", "oauth"),
            e.get("provider", "anthropic"),
            e.get("model", ""),
        )
        raise SystemExit(0)
raise SystemExit(1)
PY
}

seat_tmux_name() {
  local seat="$1" tool="$2"
  case "$seat" in
    *-"$tool") printf '%s\n' "$seat" ;;
    *) printf '%s-%s\n' "$seat" "$tool" ;;
  esac
}

primary_tmux_name() {
  local primary_tool="claude"
  [[ "$PRIMARY_SEAT_ID" == "memory" ]] && primary_tool="$(primary_effective_tool)"
  seat_tmux_name "${PROJECT}-${PRIMARY_SEAT_ID}" "$primary_tool"
}

write_bootstrap_template() {
  local seat_auth_mode seat_provider seat_model
  seat_auth_mode="$(seat_auth_mode_for_provider_mode)"
  seat_provider="$(seat_provider_for_provider_mode)"
  seat_model="$(seat_model_for_provider_mode || true)"

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] write %s\n' "$BOOTSTRAP_TEMPLATE_PATH"
    return 0
  fi

  mkdir -p "$BOOTSTRAP_TEMPLATE_DIR" || die 31 TEMPLATE_DIR_CREATE_FAILED "unable to create $BOOTSTRAP_TEMPLATE_DIR"
  cat >"$BOOTSTRAP_TEMPLATE_PATH" <<EOF
version = 1
template_name = "$CLAWSEAT_TEMPLATE_NAME"
description = "install.sh-generated ClawSeat spawn template"

[defaults]
window_mode = "tabs-1up"
monitor_max_panes = 5
open_detail_windows = false

[[engineers]]
id = "memory"
display_name = "Memory"
role = "memory"
monitor = true
tool = "claude"
auth_mode = "$seat_auth_mode"
provider = "$seat_provider"
EOF
  if [[ -n "$seat_model" ]]; then
    printf 'model = "%s"\n' "$seat_model" >>"$BOOTSTRAP_TEMPLATE_PATH"
  fi

  local seat role title
  for seat in "${PENDING_SEATS[@]}"; do
    role="$seat"
    title="$(printf '%s%s' "$(printf '%s' "${seat:0:1}" | tr '[:lower:]' '[:upper:]')" "${seat:1}")"
    cat >>"$BOOTSTRAP_TEMPLATE_PATH" <<EOF

[[engineers]]
id = "$seat"
display_name = "$title"
role = "$role"
monitor = true
tool = "claude"
auth_mode = "$seat_auth_mode"
provider = "$seat_provider"
EOF
    if [[ -n "$seat_model" ]]; then
      printf 'model = "%s"\n' "$seat_model" >>"$BOOTSTRAP_TEMPLATE_PATH"
    fi
  done
  chmod 600 "$BOOTSTRAP_TEMPLATE_PATH" || die 31 TEMPLATE_CHMOD_FAILED "unable to chmod $BOOTSTRAP_TEMPLATE_PATH"
}

write_project_local_toml() {
  local seat_auth_mode seat_provider seat_model seat primary_tool primary_auth primary_provider primary_model primary_session_name
  local primary_template_tool primary_template_auth primary_template_provider primary_template_model
  seat_auth_mode="$(seat_auth_mode_for_provider_mode)"
  seat_provider="$(seat_provider_for_provider_mode)"
  seat_model="$(seat_model_for_provider_mode || true)"
  read -r primary_template_tool primary_template_auth primary_template_provider primary_template_model < <(
    template_seat_config "$PRIMARY_SEAT_ID" 2>/dev/null || printf 'claude oauth anthropic \n'
  )
  primary_tool="$primary_template_tool"
  primary_auth="$primary_template_auth"
  primary_provider="$primary_template_provider"
  primary_model="$primary_template_model"
  if [[ "$PRIMARY_SEAT_ID" == "memory" && "$MEMORY_TOOL_EXPLICIT" == "1" ]]; then
    primary_tool="$MEMORY_TOOL"
    case "$MEMORY_TOOL" in
      claude)
        primary_auth="$seat_auth_mode"
        primary_provider="$seat_provider"
        primary_model="$seat_model"
        ;;
      codex)
        primary_auth="oauth"
        primary_provider="openai"
        primary_model="$(memory_effective_model)"
        ;;
      gemini)
        primary_auth="oauth"
        primary_provider="google"
        primary_model="$(memory_effective_model)"
        ;;
    esac
  elif [[ "$primary_tool" == "claude" ]]; then
    primary_auth="$seat_auth_mode"
    primary_provider="$seat_provider"
    primary_model="${seat_model:-$primary_model}"
  fi
  primary_session_name="$(seat_tmux_name "$PROJECT-$PRIMARY_SEAT_ID" "$primary_tool")"

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] write %s\n' "$PROJECT_LOCAL_TOML"
    return 0
  fi

  mkdir -p "$(dirname "$PROJECT_LOCAL_TOML")" || die 31 PROJECT_LOCAL_DIR_FAILED "unable to create $(dirname "$PROJECT_LOCAL_TOML")"
  # Build seat_order from PRIMARY_SEAT_ID (ancestor or memory) + PENDING_SEATS workers
  local _seat_order_str="\"$PRIMARY_SEAT_ID\""
  for seat in "${PENDING_SEATS[@]}"; do
    _seat_order_str="${_seat_order_str}, \"${seat}\""
  done
  cat >"$PROJECT_LOCAL_TOML" <<EOF
project_name = "$PROJECT"
repo_root = "$PROJECT_REPO_ROOT"
seat_order = [$_seat_order_str]

[[overrides]]
id = "$PRIMARY_SEAT_ID"
session_name = "$primary_session_name"
tool = "$primary_tool"
auth_mode = "$primary_auth"
provider = "$primary_provider"
EOF
  if [[ -n "$primary_model" ]]; then
    printf 'model = "%s"\n' "$primary_model" >>"$PROJECT_LOCAL_TOML"
  fi

  for seat in "${PENDING_SEATS[@]}"; do
    local _seat_tool _seat_auth _seat_provider _seat_template_model
    read -r _seat_tool _seat_auth _seat_provider _seat_template_model < <(
      template_seat_config "$seat" 2>/dev/null || true
    )
    # Fallback to memory provider values only if template read failed.
    _seat_tool="${_seat_tool:-claude}"
    _seat_auth="${_seat_auth:-$seat_auth_mode}"
    _seat_provider="${_seat_provider:-$seat_provider}"
    if [[ -n "$FORCE_ALL_API_PROVIDER" && "$_seat_auth" == "api" ]]; then
      _seat_provider="$(seat_provider_for_explicit_provider "$FORCE_ALL_API_PROVIDER")"
      _seat_template_model="$(seat_model_for_explicit_provider "$FORCE_ALL_API_PROVIDER")"
    fi
    cat >>"$PROJECT_LOCAL_TOML" <<EOF

[[overrides]]
id = "$seat"
tool = "$_seat_tool"
auth_mode = "$_seat_auth"
provider = "$_seat_provider"
EOF
    # Write model for claude seats only. Template-specified models stay scoped
    # to their seats; --provider is memory-only, and --all-api-provider is the
    # explicit global override for API seats.
    if [[ "$_seat_tool" == "claude" ]]; then
      local _effective_model="${_seat_template_model:-}"
      if [[ -n "$_effective_model" ]]; then
        printf 'model = "%s"\n' "$_effective_model" >>"$PROJECT_LOCAL_TOML"
      fi
    fi
  done
  chmod 600 "$PROJECT_LOCAL_TOML" || die 31 PROJECT_LOCAL_CHMOD_FAILED "unable to chmod $PROJECT_LOCAL_TOML"
}

project_profile_needs_template_migration() {
  [[ -f "$PROJECT_RECORD_PATH" ]] || return 1
  local template_file="$REPO_ROOT/templates/${CLAWSEAT_TEMPLATE_NAME}.toml"
  [[ -f "$template_file" ]] || return 1
  "$PYTHON_BIN" - "$PROJECT_RECORD_PATH" "$template_file" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

path = Path(sys.argv[1])
template_path = Path(sys.argv[2])
data = tomllib.loads(path.read_text(encoding="utf-8"))
template = tomllib.loads(template_path.read_text(encoding="utf-8"))
raw_engineers = [str(item) for item in data.get("engineers", [])]
raw_monitor_engineers = [str(item) for item in data.get("monitor_engineers", [])]
raw_overrides = data.get("seat_overrides") or {}
def normalize_seat(value: object) -> str:
    return str(value)

engineers = [normalize_seat(item) for item in data.get("engineers", [])]
monitor_engineers = [normalize_seat(item) for item in data.get("monitor_engineers", [])]
overrides = {normalize_seat(key): value for key, value in (data.get("seat_overrides") or {}).items()}
needs = False
for spec in template.get("engineers", []):
    seat = str(spec.get("id", ""))
    if not seat:
        continue
    if seat not in engineers or seat not in monitor_engineers:
        needs = True
        break
    current = overrides.get(seat) or {}
    for key in ("tool", "auth_mode", "provider"):
        if key not in current and key in spec:
            needs = True
            break
    if needs:
        break
    if spec.get("model") and "model" not in current:
        needs = True
        break
raise SystemExit(0 if needs else 1)
PY
}

project_template_name_changed() {
  local expected_template="$1"
  [[ -f "$PROJECT_RECORD_PATH" ]] || return 1
  local current_template
  current_template="$($PYTHON_BIN - "$PROJECT_RECORD_PATH" <<'PY'
from __future__ import annotations

import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

path = sys.argv[1]
with open(path, "rb") as f:
    data = tomllib.loads(f.read().decode("utf-8") if hasattr(f, "read") else "")
print(str(data.get("template_name") or ""), end="")
PY
  )"
  [[ "$current_template" != "$expected_template" ]]
}

migrate_project_profile_to_v2() {
  note "Step 5.6: migrate project profile from template defaults"
  if [[ ! -f "$PROJECT_RECORD_PATH" ]]; then
    warn "project profile migration skipped; missing $PROJECT_RECORD_PATH"
    return 0
  fi
  local template_name_changed=0
  local project_template_name
  project_template_name="$($PYTHON_BIN - "$PROJECT_RECORD_PATH" <<'PY'
from __future__ import annotations

import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

path = sys.argv[1]
with open(path, "rb") as f:
    data = tomllib.loads(f.read().decode("utf-8") if hasattr(f, "read") else "")
print(str(data.get("template_name") or ""), end="")
PY
  )"
  if [[ "$project_template_name" != "$CLAWSEAT_TEMPLATE_NAME" ]]; then
    template_name_changed=1
    note "Step 5.6: detected template_name change: ${project_template_name:-<none>} => ${CLAWSEAT_TEMPLATE_NAME}"
  fi

  local needs_migration=0
  if project_profile_needs_template_migration; then
    needs_migration=1
  elif [[ "$template_name_changed" != "1" ]]; then
    note "[install] project.toml already contains template-defined seats and override defaults"
    return 0
  fi

  local answer="${CLAWSEAT_PATROL_PROFILE_MIGRATE:-${CLAWSEAT_QA_PROFILE_MIGRATE:-}}"
  if [[ "$needs_migration" == "1" ]]; then
    if [[ -z "$answer" ]]; then
      if [[ -t 0 && -t 1 ]]; then
        printf '[install] 检测到 project.toml 缺 patrol engineer，是否升级? (Y/n) '
        read -r answer
      else
        answer="y"
      fi
    fi
    if [[ "$answer" =~ ^[Nn]$ ]]; then
      if [[ "$template_name_changed" != "1" ]]; then
        warn "project.toml patrol engineer migration skipped by operator"
        return 0
      fi
      warn "project.toml migration skipped for patrol overrides, but template change metadata will still be aligned"
    else
      answer="y"
    fi
  fi

  if [[ "$needs_migration" == "0" && "$template_name_changed" != "1" ]]; then
    note "[install] project.toml template_name already current: $project_template_name"
    return 0
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    if [[ "$needs_migration" == "1" ]]; then
      printf '[dry-run] migrate %q from template %q; preserve existing seat_overrides\n' "$PROJECT_RECORD_PATH" "$CLAWSEAT_TEMPLATE_NAME"
    else
      printf '[dry-run] update %q template_name from %q to %q\n' "$PROJECT_RECORD_PATH" "$project_template_name" "$CLAWSEAT_TEMPLATE_NAME"
    fi
    if [[ "$template_name_changed" == "1" ]]; then
      printf '[dry-run] regenerate workspace: %q engineer regenerate-workspace --project %q --all-seats --yes\n' "$AGENT_ADMIN_SCRIPT" "$PROJECT"
    fi
    return 0
  fi

  local backup_path="${PROJECT_RECORD_PATH}.bak.$(date +%Y%m%d-%H%M%S)"
  cp "$PROJECT_RECORD_PATH" "$backup_path" \
    || die 31 PROJECT_PROFILE_BACKUP_FAILED "unable to backup $PROJECT_RECORD_PATH"
  "$PYTHON_BIN" - "$PROJECT_RECORD_PATH" "$REPO_ROOT/templates/${CLAWSEAT_TEMPLATE_NAME}.toml" "$CLAWSEAT_TEMPLATE_NAME" "$template_name_changed" <<'PY' \
    || die 31 PROJECT_PROFILE_MIGRATE_FAILED "unable to migrate $PROJECT_RECORD_PATH"
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

path = Path(sys.argv[1])
template_path = Path(sys.argv[2])
template_name = sys.argv[3] if len(sys.argv) > 2 else ""
template_changed = len(sys.argv) > 4 and sys.argv[4] == "1"
text = path.read_text(encoding="utf-8")
data = tomllib.loads(text)
template = tomllib.loads(template_path.read_text(encoding="utf-8"))
template_defaults = template.get("defaults") or {}
template_engineers = [
    spec for spec in template.get("engineers", [])
    if str(spec.get("id", "")).strip()
]

def unique_extend(values: object, items: list[str]) -> list[str]:
    out = []
    if isinstance(values, list):
        for value in values:
            item = str(value)
            if item not in out:
                out.append(item)
    for item in items:
        if item not in out:
            out.append(item)
    return out

def q_array(values: list[str]) -> str:
    return "[" + ", ".join(f'"{v}"' for v in values) + "]"

template_ids = [str(spec["id"]) for spec in template_engineers]
if template_changed:
    engineers = template_ids
    monitor_engineers = [
        str(spec["id"])
        for spec in template_engineers
        if bool(spec.get("monitor", True))
    ]
    monitor_max_panes = int(template_defaults.get("monitor_max_panes", 0) or 0)
else:
    engineers = unique_extend(data.get("engineers", []), template_ids)
    monitor_engineers = unique_extend(data.get("monitor_engineers", []), template_ids)
    monitor_max_panes = max(
        int(template_defaults.get("monitor_max_panes", 0) or 0),
        int(data.get("monitor_max_panes", 0) or 0),
    )

def set_or_insert(src: str, key: str, rendered: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    line = f"{key} = {rendered}"
    if pattern.search(src):
        return pattern.sub(line, src, count=1)
    marker = re.search(r"^\[seat_overrides\.", src, re.MULTILINE)
    if marker:
        return src[: marker.start()] + line + "\n" + src[marker.start():]
    return src.rstrip() + "\n" + line + "\n"

def remove_stale_seat_overrides(src: str, active_seats: set[str]) -> str:
    pattern = re.compile(r"^\[seat_overrides\.([^\]]+)\]\s*$", re.MULTILINE)
    out = []
    cursor = 0
    for match in pattern.finditer(src):
        seat = match.group(1)
        after = src[match.end():]
        next_header = re.search(r"^\[", after, re.MULTILINE)
        block_end = match.end() + (next_header.start() if next_header else len(after))
        if seat not in active_seats:
            out.append(src[cursor:match.start()])
            cursor = block_end
    out.append(src[cursor:])
    return "".join(out)

if template_changed:
    text = remove_stale_seat_overrides(text, set(template_ids))

text = set_or_insert(text, "engineers", q_array(engineers))
text = set_or_insert(text, "monitor_engineers", q_array(monitor_engineers))
text = set_or_insert(text, "monitor_max_panes", str(monitor_max_panes))
text = set_or_insert(text, "template_name", f'"{template_name}"')

def upsert_table_key(src: str, table: str, key: str, value: str, *, preserve_existing: bool = True) -> str:
    header = f"[{table}]"
    header_match = re.search(rf"^\[{re.escape(table)}\]\s*$", src, re.MULTILINE)
    line = f"{key} = {value}"
    if not header_match:
        return src.rstrip() + f"\n\n{header}\n{line}\n"
    block_start = header_match.end()
    after = src[block_start:]
    next_header = re.search(r"^\[", after, re.MULTILINE)
    block_end = block_start + (next_header.start() if next_header else len(after))
    block = src[block_start:block_end]
    key_match = re.search(rf"^{re.escape(key)}\s*=.*$", block, re.MULTILINE)
    if key_match:
        if preserve_existing:
            return src
        block = block[: key_match.start()] + line + block[key_match.end():]
    else:
        block = block.rstrip("\n") + "\n" + line + "\n"
    return src[:block_start] + block + src[block_end:]

def render_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return q_array([str(item) for item in value])
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

for spec in template_engineers:
    seat = str(spec["id"])
    for key in ("tool", "auth_mode", "provider", "model", "base_url"):
        value = spec.get(key)
        if value in (None, ""):
            continue
        text = upsert_table_key(
            text,
            f"seat_overrides.{seat}",
            key,
            render_value(value),
            preserve_existing=True,
        )
path.write_text(text, encoding="utf-8")
PY
  note "[install] project.toml template migration complete (backup: $backup_path)"

  if [[ "$template_name_changed" == "1" ]]; then
    if [[ -z "$AGENT_ADMIN_SCRIPT" || ! -f "$AGENT_ADMIN_SCRIPT" ]]; then
      warn "workspace re-render skipped for $PROJECT because AGENT_ADMIN_SCRIPT is unavailable"
      return 0
    fi
    "$PYTHON_BIN" "$AGENT_ADMIN_SCRIPT" engineer regenerate-workspace --project "$PROJECT" --all-seats --yes \
      || die 31 PROJECT_WORKSPACE_REGEN_FAILED "unable to regenerate workspaces for $PROJECT after template change"
    note "[install] workspace regeneration completed for template update on $PROJECT"
  fi
}

ensure_patrol_engineer_record() {
  local patrol_session="$HOME/.agents/sessions/$PROJECT/patrol/session.toml"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] %q %q engineer create patrol %q --no-monitor\n' \
      "$PYTHON_BIN" "$AGENT_ADMIN_SCRIPT" "$PROJECT"
    return 0
  fi
  if [[ -f "$patrol_session" ]]; then
    note "[install] patrol engineer session already registered"
    return 0
  fi
  if [[ ! -f "$AGENT_ADMIN_SCRIPT" ]]; then
    warn "patrol engineer create skipped; missing agent_admin helper: $AGENT_ADMIN_SCRIPT"
    return 0
  fi
  "$PYTHON_BIN" "$AGENT_ADMIN_SCRIPT" engineer create patrol "$PROJECT" --no-monitor \
    || die 31 PATROL_ENGINEER_CREATE_FAILED "unable to create patrol engineer session for $PROJECT"
}

template_has_seat() {
  local target="$1" seat
  [[ "$PRIMARY_SEAT_ID" == "$target" ]] && return 0
  for seat in "${PENDING_SEATS[@]}"; do
    [[ "$seat" == "$target" ]] && return 0
  done
  return 1
}

install_patrol_bootstrap() {
  if ! template_has_seat "patrol"; then
    note "Step 7.6: patrol bootstrap skipped (template has no patrol seat)"
    return 0
  fi
  note "Step 7.6: install patrol hook + patrol cron"
  local patrol_workspace="$HOME/.agents/workspaces/$PROJECT/patrol"
  ensure_patrol_engineer_record
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] mkdir -p %q\n' "$patrol_workspace"
    printf '[dry-run] %q %q --workspace %q\n' "$PYTHON_BIN" "$PATROL_HOOK_INSTALLER" "$patrol_workspace"
  elif [[ ! -f "$PATROL_HOOK_INSTALLER" ]]; then
    warn "patrol hook install skipped; missing helper: $PATROL_HOOK_INSTALLER"
  else
    mkdir -p "$patrol_workspace"
    "$PYTHON_BIN" "$PATROL_HOOK_INSTALLER" --workspace "$patrol_workspace"
  fi
  prompt_patrol_cron_optin
}

bootstrap_project_profile() {
  note "Step 5.5: bootstrap project engineer profiles (no tmux start)"
  [[ -f "$WAIT_FOR_SEAT_SCRIPT" || "$DRY_RUN" == "1" ]] || die 31 WAIT_SCRIPT_MISSING "missing wait-for-seat script: $WAIT_FOR_SEAT_SCRIPT"
  [[ -f "$AGENT_ADMIN_SCRIPT" || "$DRY_RUN" == "1" ]] || die 31 AGENT_ADMIN_MISSING "missing agent_admin script: $AGENT_ADMIN_SCRIPT"
  local profile_check_path="$HOME/.agents/profiles/${PROJECT}-profile-dynamic.toml"
  # Canonical templates live in templates/*.toml and must not be overwritten
  # by install-time generated template files.
  write_project_local_toml

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] (cd %q && %q %q project bootstrap --template %q --local %q)\n' \
      "$AGENTS_TEMPLATES_ROOT" "$PYTHON_BIN" "$AGENT_ADMIN_SCRIPT" "$CLAWSEAT_TEMPLATE_NAME" "$PROJECT_LOCAL_TOML"
    ensure_deepseek_secret_template
    seed_bootstrap_secrets
    return 0
  fi

  if [[ -f "$PROJECT_RECORD_PATH" ]]; then
    printf 'Project %s already exists at %s; skipping bootstrap.\n' "$PROJECT" "$PROJECT_RECORD_PATH"
    return 0
  fi

  mkdir -p "$AGENTS_TEMPLATES_ROOT" || die 31 TEMPLATE_ROOT_CREATE_FAILED "unable to create $AGENTS_TEMPLATES_ROOT"
  if [[ "${REINSTALL_TEMPLATE_CHANGED:-0}" == "1" && -f "$profile_check_path" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
      printf '[dry-run] reinstall template changed: remove stale profile %q before bootstrap\n' "$profile_check_path"
    else
      rm -f "$profile_check_path" || die 31 PROFILE_REMOVE_FAILED "unable to remove stale reinstall profile: $profile_check_path"
    fi
  fi
  (
    cd "$AGENTS_TEMPLATES_ROOT" &&
    "$PYTHON_BIN" "$AGENT_ADMIN_SCRIPT" project bootstrap --template "$CLAWSEAT_TEMPLATE_NAME" --local "$PROJECT_LOCAL_TOML"
  ) || die 31 PROJECT_BOOTSTRAP_FAILED "unable to bootstrap project profile via agent_admin: $PROJECT"
  ensure_deepseek_secret_template
  seed_bootstrap_secrets

  if [[ ! -f "$profile_check_path" ]]; then
    die 31 PROFILE_RENDER_MISSING "agent_admin project bootstrap finished but profile not rendered: $profile_check_path. This indicates a regression in agent_admin_crud_bootstrap.py; profile-dynamic.toml is required by dispatch_task.py / state.seed."
  fi
}

_update_projects_json() {
  local action="${1:-install}" project="${2:-$PROJECT}"
  local primary_tool="claude" primary_session_name
  case "$action" in
    install|reinstall|add|update) ;;
    uninstall|remove)
      if [[ "$DRY_RUN" == "1" ]]; then
        printf '[dry-run] %q %q unregister %q\n' "$PYTHON_BIN" "$PROJECTS_REGISTRY_SCRIPT" "$project"
        return 0
      fi
      [[ -f "$PROJECTS_REGISTRY_SCRIPT" ]] || die 31 PROJECTS_REGISTRY_MISSING "missing projects registry helper: $PROJECTS_REGISTRY_SCRIPT"
      "$PYTHON_BIN" "$PROJECTS_REGISTRY_SCRIPT" unregister "$project" || true
      return 0
      ;;
    *) die 31 PROJECTS_JSON_ACTION_UNKNOWN "unknown projects.json action: $action" ;;
  esac

  [[ "$PRIMARY_SEAT_ID" == "memory" ]] && primary_tool="$(primary_effective_tool)"
  primary_session_name="$(primary_tmux_name)"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] %q %q register %q --primary-seat %q --primary-seat-tool %q --tmux-name %q --template-name %q --repo-path %q\n' \
      "$PYTHON_BIN" "$PROJECTS_REGISTRY_SCRIPT" "$PROJECT" "$PRIMARY_SEAT_ID" "$primary_tool" "$primary_session_name" "$CLAWSEAT_TEMPLATE_NAME" "$PROJECT_REPO_ROOT"
    return 0
  fi
  if [[ ! -f "$PROJECTS_REGISTRY_SCRIPT" ]]; then
    warn "projects.json register skipped; missing $PROJECTS_REGISTRY_SCRIPT"
    return 0
  fi
  "$PYTHON_BIN" "$PROJECTS_REGISTRY_SCRIPT" register "$PROJECT" \
    --primary-seat "$PRIMARY_SEAT_ID" \
    --primary-seat-tool "$primary_tool" \
    --tmux-name "$primary_session_name" \
    --template-name "$CLAWSEAT_TEMPLATE_NAME" \
    --repo-path "$PROJECT_REPO_ROOT" >/dev/null \
    || warn "projects.json register failed (non-fatal); see ~/.clawseat/projects.json"
}

register_project_registry() {
  local action="install"
  [[ "$FORCE_REINSTALL" == "1" ]] && action="reinstall"
  _update_projects_json "$action" "$PROJECT"
}

uninstall_project_registry_entry() {
  local project="$1"
  _update_projects_json uninstall "$project"
}

touch_project_registry() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] %q %q touch %q\n' "$PYTHON_BIN" "$PROJECTS_REGISTRY_SCRIPT" "$PROJECT"
    return 0
  fi
  [[ -f "$PROJECTS_REGISTRY_SCRIPT" ]] || return 0
  "$PYTHON_BIN" "$PROJECTS_REGISTRY_SCRIPT" touch "$PROJECT" >/dev/null 2>&1 || true
}

install_clawseat_cli_symlink() {
  local link="/usr/local/bin/clawseat"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] ln -sfn %q %q\n' "$CLAWSEAT_CLI_SCRIPT" "$link"
    return 0
  fi
  [[ -f "$CLAWSEAT_CLI_SCRIPT" ]] || { warn "clawseat CLI skipped; missing $CLAWSEAT_CLI_SCRIPT"; return 0; }
  if [[ -w "$(dirname "$link")" || ( ! -e "$link" && -w "$(dirname "$link")" ) ]]; then
    ln -sfn "$CLAWSEAT_CLI_SCRIPT" "$link" || warn "unable to install $link"
  else
    warn "clawseat CLI symlink skipped; $(dirname "$link") not writable"
  fi
}

render_brief() {
  note "Step 4: render memory bootstrap brief"
  [[ -f "$MEMORY_BRIEF_TEMPLATE" || "$DRY_RUN" == "1" ]] || die 30 TEMPLATE_MISSING "missing template: $MEMORY_BRIEF_TEMPLATE"
  local pending_seats_human primary_session_name
  printf -v pending_seats_human '%s, ' "${PENDING_SEATS[@]}"
  pending_seats_human="${pending_seats_human%, }"
  primary_session_name="$(primary_tmux_name)"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] render %s -> %s\n' "$MEMORY_BRIEF_TEMPLATE" "$BRIEF_PATH"
  else
    "$PYTHON_BIN" - "$MEMORY_BRIEF_TEMPLATE" "$BRIEF_PATH" "$PROJECT" "$REPO_ROOT" "$REAL_HOME" "$CLAWSEAT_TEMPLATE_NAME" "$PRIMARY_SEAT_ID" "$pending_seats_human" "$primary_session_name" <<'PY'
from pathlib import Path
from string import Template
import sys
tmpl = Template(Path(sys.argv[1]).read_text(encoding="utf-8")).safe_substitute(
    PROJECT_NAME=sys.argv[3],
    CLAWSEAT_ROOT=sys.argv[4],
    AGENT_HOME=sys.argv[5],
    PRIMARY_SEAT_ID=sys.argv[7],
    PENDING_SEATS_HUMAN=sys.argv[8],
    PRIMARY_SESSION_NAME=sys.argv[9],
)
tmpl = tmpl.replace("{CLAWSEAT_TEMPLATE_NAME}", sys.argv[6] if len(sys.argv) > 6 else "clawseat-engineering")
out = Path(sys.argv[2]); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(tmpl, encoding="utf-8")
PY
    chmod 600 "$BRIEF_PATH" || die 30 BRIEF_CHMOD_FAILED "unable to chmod $BRIEF_PATH"
  fi
}

write_operator_guide() {
  local primary_session_name
  primary_session_name="$(primary_tmux_name)"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] write %s\n' "$GUIDE_FILE"
    return 0
  fi

  # The rendered guide uses `${primary_session_name}` so v2 memory-primary
  # projects and imported legacy templates both stay correct.
  mkdir -p "$(dirname "$GUIDE_FILE")" || die 30 GUIDE_DIR_FAILED "unable to create $(dirname "$GUIDE_FILE")"
  cat >"$GUIDE_FILE" <<EOF
# Operator — ClawSeat $PROJECT 启动指引

install.sh 已完成。现在按 6 步触发 Phase-A。v2 项目使用项目 workers 窗口 + shared memories 窗口；legacy template 可能仍使用单窗口布局。

1. 切到 primary seat：\`${primary_session_name}\`。v2 项目通常在 \`clawseat-memories\` 窗口的项目 tab；legacy template 可能在项目窗口中。

2. **先确认 ${primary_session_name} pane 已就绪** — install.sh 不再自动发送 Phase-A kickoff；kickoff 已写入文件，等待 operator 主动触发：

   \`\`\`bash
   tmux capture-pane -t '${primary_session_name}' -p | tail -15
   \`\`\`

   如果看到 Bypass Permissions / Trust folder / Login / Accessing workspace / Quick safety check 等确认屏，先按屏幕提示处理完，再继续。

3. Phase-A kickoff prompt 文件：

   \`\`\`bash
   cat ${KICKOFF_FILE}
   \`\`\`

4. 选择一种触发方式（A/B/C 三选一）：

   **A) 让当前 install-memory / 安装 agent 通过 transport 发送 kickoff：**

   \`\`\`bash
   bash ${SEND_AND_VERIFY_SCRIPT} --project ${PROJECT} ${primary_session_name} "\$(cat "${KICKOFF_FILE}")"
   \`\`\`

   **B) 手动粘贴：**

   \`\`\`bash
   cat ${KICKOFF_FILE}
   \`\`\`

   打开 ${primary_session_name} pane，把输出复制到 primary seat prompt，按 Enter。

   kickoff 内容要求：
   - Phase-A 不让 memory 做同步调研。
   - B2.5 / B5 都按 brief 由 ${PRIMARY_SEAT_ID} seat 自己 Read openclaw / binding 文件。
   - memory 在 Phase-A 唯一位置是 B7 后接收 phase-a-decisions learnings。
   - 然后按 B3 / B3.5 / B5 / B6 / B7 顺序推进；用 agent_admin.py session start-engineer 逐个拉起 seat（不要 fan-out，一个一个来）。

   **C) 让 install-memory 接手：**

   在 install-memory chat 里说：\`dispatch ${PROJECT} kickoff\`。

5. **验证 Phase-A 已启动** — 触发后立刻 re-capture 确认：

   \`\`\`bash
   tmux capture-pane -t '${primary_session_name}' -p | tail -10
   \`\`\`

   预期看到 \`B0\` / \`已读取 brief\` / \`env_scan\` 等字样。

6. 每走完一步向 ${PRIMARY_SEAT_ID} seat 说"继续"或给修正（provider / chat_id 等）

## 项目注册表

本项目已注册到 \`~/.clawseat/projects.json\`，memories 窗口优先按该注册表展示项目。
如需从注册表移除本项目（不删除 tmux/session 文件）：

\`\`\`bash
python3 ${PROJECTS_REGISTRY_SCRIPT} unregister ${PROJECT}
\`\`\`

## 如果 ${PRIMARY_SEAT_ID} seat 报 BRIEF_DRIFT_DETECTED

${PRIMARY_SEAT_ID} seat 在每个 B 步开始前会先跑 brief drift check hook。这只能检测 brief 是否在你启动后被更新，不能让运行中的 agent 热更新 system prompt。

推荐处理：

1. \`tmux kill-session -t ${primary_session_name}\`
2. 重新启动 primary seat（建议重跑 \`scripts/install.sh --project ${PROJECT} --reinstall\`，或按同样的 \`agent-launcher.sh\` 参数重起）
3. 让 ${PRIMARY_SEAT_ID} seat 重新读取 \`\$CLAWSEAT_MEMORY_BRIEF\`

如果你暂时不 restart，也可以继续按旧 brief 跑，但它不会自动感知后续改动。
EOF
  chmod 600 "$GUIDE_FILE" || die 30 GUIDE_CHMOD_FAILED "unable to chmod $GUIDE_FILE"
}

phase_a_kickoff_prompt() {
  printf '读 %s 开始 Phase-A。按 brief 顺序执行 B0-B7，每步向我汇报或 CLI prompt 我确认。不要 fan-out specialist seat；spawn engineer seat 要 one-at-a-time。\n' "$BRIEF_PATH"
}

persist_phase_a_kickoff_prompt() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] write %s\n' "$KICKOFF_FILE"
    return 0
  fi

  mkdir -p "$(dirname "$KICKOFF_FILE")" || die 30 KICKOFF_DIR_FAILED "unable to create $(dirname "$KICKOFF_FILE")"
  (umask 077; phase_a_kickoff_prompt >"$KICKOFF_FILE") \
    || die 30 KICKOFF_WRITE_FAILED "unable to write $KICKOFF_FILE"
  chmod 600 "$KICKOFF_FILE" || die 30 KICKOFF_CHMOD_FAILED "unable to chmod $KICKOFF_FILE"
}

print_operator_banner() {
  local primary_session_name
  primary_session_name="$(primary_tmux_name)"
  printf '\n'
  printf -- '────────────────────────────────────────────────────────────────\n'
  printf '  ClawSeat install complete / 安装已完成\n'
  printf -- '────────────────────────────────────────────────────────────────\n'
  printf '\n'
  printf '  AGENT (if you are an AI agent running install.sh):\n'
  printf '    You MUST relay this banner to the operator before ending your\n'
  printf '    session. install.sh exiting is NOT the final completion signal —\n'
  printf '    Phase-A has NOT been auto-sent. The operator must choose A/B/C\n'
  printf '    below to trigger kickoff, then verify Phase-A started.\n'
  printf '    See docs/INSTALL.md §0 step 5.\n'
  printf '    向 operator 复述本 banner 是必做步骤，禁止跳过。\n'
  printf '\n'
  printf '  OPERATOR — NEXT STEPS / 操作员下一步:\n'
  printf '    ✔ Install complete. %s pane is ready or waiting for login/trust confirmation.\n' "$primary_session_name"
  printf '    Phase-A kickoff prompt was saved here:\n'
  printf '       %s\n' "$KICKOFF_FILE"
  printf '\n'
  printf '    Choose one to start Phase-A / 三选一启动 Phase-A:\n'
  printf '\n'
  printf '    A) Existing install-memory / current install agent dispatches kickoff:\n'
  printf '       bash %q --project %q %q "$(cat %q)"\n' \
    "$SEND_AND_VERIFY_SCRIPT" "$PROJECT" "$primary_session_name" "$KICKOFF_FILE"
  printf '\n'
  printf '    B) Manual paste / 手动粘贴:\n'
  printf '       cat %q\n' "$KICKOFF_FILE"
  printf '       Then paste the output into the %s primary seat prompt and press Enter.\n' "$primary_session_name"
  printf '\n'
  printf '    C) Ask install-memory in chat / 在 install-memory chat 里说:\n'
  printf '       dispatch %s kickoff\n' "$PROJECT"
  printf '\n'
  printf '    After A/B/C, verify Phase-A is running / 触发后确认:\n'
  printf '       tmux capture-pane -t %q -p | tail -10\n' "$primary_session_name"
  printf '       Expected: B0 / "已读取 brief" / env_scan activity.\n'
  printf '\n'
  printf '    Operator guide / 操作员指引:\n'
  printf '       cat %s\n' "$GUIDE_FILE"
  printf '    Registry cleanup / 注册表移除:\n'
  printf '       python3 %q unregister %q\n' "$PROJECTS_REGISTRY_SCRIPT" "$PROJECT"
  printf '\n'
  printf -- '────────────────────────────────────────────────────────────────\n'
}
