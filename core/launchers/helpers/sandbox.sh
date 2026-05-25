#!/usr/bin/env bash
# Sourced by agent-launcher.sh. Keep path resolution BASH_SOURCE-based because
# sourced files observe a different $0 than the top-level launcher.

if [[ -z "${LAUNCHER_DIR:-}" ]]; then
  _launcher_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  case "$_launcher_lib_dir" in
    */helpers|*/runtimes) LAUNCHER_DIR="$(cd "$_launcher_lib_dir/.." && pwd)" ;;
    *) LAUNCHER_DIR="$_launcher_lib_dir" ;;
  esac
  export LAUNCHER_DIR
fi
if [[ -z "${LAUNCHER_REPO_ROOT:-}" ]]; then
  LAUNCHER_REPO_ROOT="$(cd "$LAUNCHER_DIR/../.." && pwd)"
fi
REAL_HOME="${REAL_HOME:-${HOME:-}}"
LAUNCHER_PYTHON_BIN="${LAUNCHER_PYTHON_BIN:-${PYTHON_BIN:-python3}}"

seed_user_tool_dirs() {
  # Seed user-level tool directories/files from the real HOME into the
  # per-seat sandbox HOME. This keeps user-auth state and tool sockets
  # visible to isolated runtimes without copying secrets out of band.
  #
  # If a sandbox already has an independent copy, move it aside under
  # .sandbox-pre-seed-backup/ and replace it with a symlink so we can
  # retroactively heal old runtimes.
  local runtime_home="$1"
  local project_name="${2:-${CLAWSEAT_PROJECT:-}}"
  if [[ "$runtime_home" == "$REAL_HOME" ]]; then
    return 0
  fi
  local source_home="$REAL_HOME"
  if [[ "${CLAWSEAT_TOOLS_ISOLATION:-shared-real-home}" == "per-project" && -n "$project_name" ]]; then
    source_home="${CLAWSEAT_PROJECT_TOOL_ROOT:-${AGENT_HOME:-$REAL_HOME}/.agent-runtime/projects/$project_name}"
    [[ -d "$source_home" ]] || return 0
  fi
  local seeds=(
    ".lark-cli"
    "Library/Application Support/iTerm2"
    "Library/Preferences/com.googlecode.iterm2.plist"
    ".config/gemini"
    ".gemini"
    ".config/codex"
    ".codex"
    ".cartooner"
  )

  local subpath src tgt backup_base backup_path current_target
  backup_base="$runtime_home/.sandbox-pre-seed-backup"
  for subpath in "${seeds[@]}"; do
    src="$source_home/$subpath"
    tgt="$runtime_home/$subpath"
    [[ -e "$src" ]] || continue

    if [[ -L "$tgt" ]]; then
      current_target="$(readlink "$tgt" 2>/dev/null || true)"
      if [[ "$current_target" == "$src" ]]; then
        continue
      fi
      rm -f "$tgt"
    elif [[ -e "$tgt" ]]; then
      backup_path="$backup_base/$subpath.$(date +%s)"
      mkdir -p "$(dirname "$backup_path")"
      mv "$tgt" "$backup_path"
    fi

    if [[ ! -e "$tgt" ]]; then
      mkdir -p "$(dirname "$tgt")"
      ln -s "$src" "$tgt"
    fi
  done

  local tool_dir
  for tool_dir in \
    "$REAL_HOME/AI/工具入口" \
    "$REAL_HOME/AI/toolchains/npm-global/bin" \
    "/opt/homebrew/bin"; do
    [[ -d "$tool_dir" ]] || continue
    case ":${PATH:-}:" in
      *":$tool_dir:"*) ;;
      *) export PATH="$tool_dir${PATH:+:$PATH}" ;;
    esac
  done

  # Seed the lark-cli HOME-override wrapper so sandbox seats can invoke
  # `lark-cli ...` transparently and still see the operator's real
  # Keychain-backed auth state. The wrapper itself lives in
  # CLAWSEAT_ROOT/core/shell-scripts/lark-cli; we symlink it into
  # $runtime_home/bin/lark-cli and prepend that bin dir to PATH so
  # seats pick it up before any real lark-cli in the system PATH.
  #
  # `${CLAWSEAT_ROOT:-}` fallback keeps `set -u` callers (e.g. the
  # launcher-lib helper sourced by test_launcher_project_tool_seed.py
  # under `set -euo pipefail`) from tripping when the env var hasn't
  # been wired. The wrapper-seed step no-ops cleanly in that case.
  local wrapper_src="${CLAWSEAT_ROOT:-}/core/shell-scripts/lark-cli"
  if [[ -n "${CLAWSEAT_ROOT:-}" && -x "$wrapper_src" ]]; then
    local wrapper_tgt="$runtime_home/bin/lark-cli"
    mkdir -p "$(dirname "$wrapper_tgt")"
    if [[ -L "$wrapper_tgt" ]]; then
      current_target="$(readlink "$wrapper_tgt" 2>/dev/null || true)"
      if [[ "$current_target" != "$wrapper_src" ]]; then
        rm -f "$wrapper_tgt"
        ln -s "$wrapper_src" "$wrapper_tgt"
      fi
    elif [[ -e "$wrapper_tgt" ]]; then
      backup_path="$backup_base/bin/lark-cli.$(date +%s)"
      mkdir -p "$(dirname "$backup_path")"
      mv "$wrapper_tgt" "$backup_path"
      ln -s "$wrapper_src" "$wrapper_tgt"
    else
      ln -s "$wrapper_src" "$wrapper_tgt"
    fi

    # Prepend $runtime_home/bin to PATH so the wrapper shadows the real
    # lark-cli in system PATH. Idempotent; repeated seat restarts don't
    # double-prepend.
    case ":${PATH:-}:" in
      *":$runtime_home/bin:"*) ;;
      *) export PATH="$runtime_home/bin${PATH:+:$PATH}" ;;
    esac
  fi
}

prepare_codex_home() {
  local codex_home="$1"
  local source_home="${2:-$HOME}"
  mkdir -p "$codex_home"

  local shared_items=(
    "config.toml"
    "skills"
    "plugins"
    "rules"
    "vendor_imports"
  )

  local item
  for item in "${shared_items[@]}"; do
    if [[ -e "$source_home/.codex/$item" && ! -e "$codex_home/$item" ]]; then
      ln -s "$source_home/.codex/$item" "$codex_home/$item"
    fi
  done
}

prepare_gemini_home() {
  local runtime_home="$1"
  local workdir="${2:-${WORKDIR:-}}"
  local project_name="${3:-${CLAWSEAT_PROJECT:-}}"
  local source_home="$REAL_HOME"
  if [[ "${CLAWSEAT_TOOLS_ISOLATION:-shared-real-home}" == "per-project" && -n "$project_name" ]]; then
    source_home="${CLAWSEAT_PROJECT_TOOL_ROOT:-${AGENT_HOME:-$REAL_HOME}/.agent-runtime/projects/$project_name}"
    [[ -d "$source_home" ]] || source_home="$REAL_HOME"
  fi
  local gemini_home="$runtime_home/.gemini"
  local source_gemini_home="$source_home/.gemini"
  local current_target=""
  mkdir -p "$gemini_home"

  if [[ -L "$gemini_home" ]]; then
    current_target="$(readlink "$gemini_home" 2>/dev/null || true)"
    if [[ "$current_target" == "$source_gemini_home" ]]; then
      rm -f "$gemini_home"
      mkdir -p "$gemini_home"
    fi
  fi

  if [[ -d "$source_gemini_home" ]]; then
    local item item_name
    shopt -s nullglob
    for item in "$source_gemini_home"/*; do
      item_name="${item##*/}"
      [[ "$item_name" == "trustedFolders.json" ]] && continue
      if [[ ! -e "$gemini_home/$item_name" ]]; then
        ln -s "$item" "$gemini_home/$item_name"
      fi
    done
    shopt -u nullglob
  fi

  if [[ -n "$workdir" ]]; then
    if [[ -L "$gemini_home/trustedFolders.json" ]]; then
      rm -f "$gemini_home/trustedFolders.json"
    fi
    python3 - "$source_gemini_home/trustedFolders.json" "$gemini_home/trustedFolders.json" "$workdir" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
workdir = sys.argv[3]
data: dict[str, str] = {}
for candidate in (src, dst):
    if not candidate.exists():
        continue
    try:
        loaded = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception:
        loaded = {}
    if isinstance(loaded, dict):
        data.update({str(key): str(value) for key, value in loaded.items()})
data[workdir] = "TRUST_FOLDER"
dst.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
  fi
}

prepare_claude_home() {
  # Seed Claude Code's isolated HOME so onboarding doesn't fire for every
  # seat launch. Keep Claude on an explicit white-list model: share a
  # small set of user-level compatibility paths, but materialize the
  # sandbox's settings/skills from the seat-specific .claude-template.
  local runtime_home="$1"
  local session_name="${2:-}"
  local trust_workdir="${3:-}"
  local runtime_claude="$runtime_home/.claude"
  local source_claude="$REAL_HOME/.claude"
  local source_claude_json="$REAL_HOME/.claude.json"
  local runtime_claude_json="$runtime_home/.claude.json"
  local existing_runtime_claude_json=""
  mkdir -p "$runtime_claude"

  # Always materialize a runtime-local .claude.json for isolated API seats.
  # The real host file may carry an unfinished onboarding state that forces
  # Claude Code back into the login picker even when API auth env is already
  # present. Keep useful host/runtime fields, but force onboarding complete.
  # Also pre-trust the seat's workdir so the workspace trust dialog
  # ("Yes, I trust this folder") doesn't block automated tmux launches —
  # `--dangerously-skip-permissions` covers runtime perms but NOT this dialog.
  if [[ -f "$runtime_claude_json" && ! -L "$runtime_claude_json" ]]; then
    existing_runtime_claude_json="$runtime_claude_json"
  fi
  if [[ -L "$runtime_claude_json" ]]; then
    rm -f "$runtime_claude_json"
  fi
  python3 - "$source_claude_json" "$existing_runtime_claude_json" "$runtime_claude_json" "$trust_workdir" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
existing_runtime_path = Path(sys.argv[2]) if sys.argv[2] else None
target_path = Path(sys.argv[3])
trust_workdir = sys.argv[4] if len(sys.argv) > 4 else ""
data: dict[str, object] = {}

for candidate in (source_path, existing_runtime_path):
    if candidate is None or not candidate.exists():
        continue
    try:
        loaded = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception:
        loaded = {}
    if isinstance(loaded, dict):
        data.update(loaded)

data["hasCompletedOnboarding"] = True
data["hasSeenWelcome"] = True
version = data.get("lastOnboardingVersion")
if not isinstance(version, str) or not version.strip():
    data["lastOnboardingVersion"] = "99.99.99"

# Pre-trust the seat's workspace so Claude doesn't block on the
# "Yes, I trust this folder" dialog — that dialog is independent of
# --dangerously-skip-permissions and only auto-skips in non-interactive
# (-p / piped stdout) modes, neither of which applies inside tmux.
if trust_workdir:
    projects = data.get("projects")
    if not isinstance(projects, dict):
        projects = {}
    entry = projects.get(trust_workdir)
    if not isinstance(entry, dict):
        entry = {}
    entry["hasTrustDialogAccepted"] = True
    projects[trust_workdir] = entry
    data["projects"] = projects

target_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

  # Preserve compatibility caches and definition directories that remain
  # intentionally shared across sandboxes.
  local compat_items=(
    "statsig"
  )
  local item
  for item in "${compat_items[@]}"; do
    if [[ -e "$source_claude/$item" && ! -e "$runtime_claude/$item" ]]; then
      ln -s "$source_claude/$item" "$runtime_claude/$item"
    fi
  done

  local shared_items=(
    "commands"
    "agents"
  )
  for item in "${shared_items[@]}"; do
    if [[ -e "$source_claude/$item" && ! -e "$runtime_claude/$item" ]]; then
      ln -s "$source_claude/$item" "$runtime_claude/$item"
    fi
  done

  local seat_id="${CLAWSEAT_SEAT:-${CLAWSEAT_ENGINEER_ID:-}}"
  # Fallback: infer seat_id from session_name when env not passed.
  # session_name formats: "<project>-<seat>-<tool>", primary-seat overrides
  # such as "<project>-memory", or "machine-memory-<tool>"
  if [[ -z "$seat_id" && -n "$session_name" ]]; then
    local _project="${CLAWSEAT_PROJECT:-}"
    local _candidate="$session_name"
    [[ -n "$_project" ]] && _candidate="${_candidate#${_project}-}"
    _candidate="${_candidate#machine-}"
    _candidate="${_candidate%-claude}"
    _candidate="${_candidate%-codex}"
    _candidate="${_candidate%-gemini}"
    if [[ -d "${AGENTS_ROOT:-$REAL_HOME/.agents}/engineers/$_candidate" ]]; then
      seat_id="$_candidate"
    fi
  fi
  local runtime_settings="$runtime_claude/settings.json"
  local runtime_skills="$runtime_claude/skills"
  if [[ -n "$seat_id" ]]; then
    "$LAUNCHER_PYTHON_BIN" - "$LAUNCHER_REPO_ROOT" "${AGENTS_ROOT:-$REAL_HOME/.agents}" "$seat_id" "$runtime_claude" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
agents_root = Path(sys.argv[2])
seat_id = sys.argv[3]
runtime_claude_root = Path(sys.argv[4])
sys.path.insert(0, str(repo_root / "core" / "scripts"))

from seat_claude_template import copy_seat_claude_template_to_runtime

copy_seat_claude_template_to_runtime(
    agents_root / "engineers",
    seat_id,
    runtime_claude_root,
    clawseat_root=repo_root,
)
PY
    return 0
  fi

  if [[ -L "$runtime_settings" ]]; then
    rm -f "$runtime_settings"
  fi
  if [[ -L "$runtime_skills" ]]; then
    rm -f "$runtime_skills"
  fi
  mkdir -p "$runtime_skills"
  "$LAUNCHER_PYTHON_BIN" - "$runtime_settings" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
data: dict[str, object] = {}
if target_path.exists():
    try:
        loaded = json.loads(target_path.read_text(encoding="utf-8"))
    except Exception:
        loaded = {}
    if isinstance(loaded, dict):
        data.update(loaded)
if not isinstance(data.get("hooks"), dict):
    data["hooks"] = {}
if not isinstance(data.get("permissions"), dict):
    data["permissions"] = {}
target_path.parent.mkdir(parents=True, exist_ok=True)
target_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}
