#!/usr/bin/env bash
# shellcheck shell=bash
# Loaded by scripts/install.sh. Resolve this file with BASH_SOURCE so
# callers may source install.sh from any current working directory.
_CLAWSEAT_INSTALL_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

symlink_skills() {
  local skills_home="$1"; shift
  local skill target link
  mkdir -p "$skills_home" || die 31 SKILL_SYMLINK_DIR_FAILED "unable to create $skills_home"
  for skill in "$@"; do
    target="$REPO_ROOT/core/skills/$skill"
    link="$skills_home/$skill"
    if [[ ! -d "$target" ]]; then
      warn "skill symlink skipped; missing skill directory: $target"
      continue
    fi
    if [[ -L "$link" ]]; then
      rm -f "$link"
    elif [[ -e "$link" ]]; then
      local backup="${link}.bak.$(date -u +%Y%m%dT%H%M%SZ)" idx=0
      while [[ -e "$backup" ]]; do
        idx=$((idx + 1))
        backup="${link}.bak.$(date -u +%Y%m%dT%H%M%SZ).$idx"
      done
      mv "$link" "$backup" || die 31 SKILL_SYMLINK_FAILED "unable to preserve unmanaged skill path $link"
      warn "preserved unmanaged skill path: $link -> $backup"
    fi
    ln -s "$target" "$link" || die 31 SKILL_SYMLINK_FAILED "unable to link $link -> $target"
  done
}

mirror_agents_skills_to_home() {
  local agents_skills_home="$1" skills_home="$2"; shift 2
  local source link skill
  local -a whitelist=("$@")
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] mkdir -p %q\n' "$skills_home"
    if [[ ${#whitelist[@]} -gt 0 ]]; then
      for skill in "${whitelist[@]}"; do
        printf '[dry-run] mirror %q into %q\n' "$agents_skills_home/$skill" "$skills_home/$skill"
      done
    else
      printf '[dry-run] mirror %q/* into %q\n' "$agents_skills_home" "$skills_home"
    fi
    return 0
  fi

  mkdir -p "$skills_home" || die 31 SKILL_SYMLINK_DIR_FAILED "unable to create $skills_home"
  [[ -d "$agents_skills_home" ]] || return 0

  shopt -s nullglob
  for source in "$agents_skills_home"/*; do
    skill="$(basename "$source")"
    if [[ ${#whitelist[@]} -gt 0 ]]; then
      local allowed=0 item
      for item in "${whitelist[@]}"; do
        [[ "$skill" == "$item" ]] && { allowed=1; break; }
      done
      [[ "$allowed" == "1" ]] || continue
    fi
    link="$skills_home/$skill"
    if [[ -e "$link" && ! -L "$link" ]]; then
      warn "skill mirror skipped; unmanaged path exists: $link"
      continue
    fi
    rm -f "$link"
    ln -s "$source" "$link" || die 31 SKILL_SYMLINK_FAILED "unable to link $link -> $source"
  done
  shopt -u nullglob
}

remove_skill_symlinks() {
  local skills_home="$1"; shift
  local skill link target
  for skill in "$@"; do
    link="$skills_home/$skill"
    if [[ -L "$link" ]]; then
      target="$(readlink "$link" || true)"
      if [[ "$target" != "$REPO_ROOT/core/skills/"* ]]; then
        warn "skill symlink cleanup skipped; unmanaged link: $link -> $target"
        continue
      fi
      rm -f "$link" || die 31 SKILL_SYMLINK_FAILED "unable to remove skipped skill link $link"
    fi
  done
}

install_skill_tier_for_home() {
  local tool="$1" skills_home="$2"; shift 2
  local -a whitelist=("$@")
  # AC: skip Gemini — Gemini CLI has a built-in ~/.agents/skills/ alias.
  # Mirroring those same skills into ~/.gemini/skills/ makes Gemini double-scan
  # the same SKILL.md files and emit conflict warnings on startup.
  if [[ "$tool" == "gemini" ]]; then
    return 0
  fi

  local agents_skills_home="$HOME/.agents/skills"
  if [[ "$skills_home" != "$agents_skills_home" ]]; then
    if [[ ${#whitelist[@]} -gt 0 ]]; then
      mirror_agents_skills_to_home "$agents_skills_home" "$skills_home" "${whitelist[@]}"
    else
      mirror_agents_skills_to_home "$agents_skills_home" "$skills_home"
    fi
    return 0
  fi

  local -a core_skills=(clawseat-memory clawseat-decision-escalation)
  local -a extended_skills=(clawseat-koder clawseat-privacy clawseat-memory-reporting clawseat-intake)
  local -a selected_skills=("${core_skills[@]}")

  if [[ "$tool" == "claude" || "$LOAD_ALL_SKILLS" == "1" ]]; then
    selected_skills+=("${extended_skills[@]}")
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] mkdir -p %q\n' "$skills_home"
    local skill
    for skill in "${selected_skills[@]}"; do
      printf '[dry-run] ln -sfn %q %q\n' "$REPO_ROOT/core/skills/$skill" "$skills_home/$skill"
    done
    if [[ "$tool" != "claude" && "$LOAD_ALL_SKILLS" != "1" ]]; then
      for skill in "${extended_skills[@]}"; do
        printf '[dry-run] rm -f %q\n' "$skills_home/$skill"
      done
    fi
    return 0
  fi

  symlink_skills "$skills_home" "${selected_skills[@]}"
  if [[ "$tool" != "claude" && "$LOAD_ALL_SKILLS" != "1" ]]; then
    remove_skill_symlinks "$skills_home" "${extended_skills[@]}"
  fi
}

cleanup_legacy_gemini_skill_symlinks() {
  local gemini_skills="$HOME/.gemini/skills"
  [[ -d "$gemini_skills" ]] || return 0

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] cleanup legacy gemini skill symlinks in %q\n' "$gemini_skills"
    return 0
  fi

  local entry target
  for entry in "$gemini_skills"/*; do
    [[ -L "$entry" ]] || continue
    target="$(readlink "$entry" || true)"
    if [[ "$target" == */.agents/skills/* ]]; then
      rm -f "$entry" || die 31 SKILL_SYMLINK_FAILED "unable to remove legacy gemini skill link $entry"
      note "removed legacy gemini skill symlink: $(basename "$entry")"
    fi
  done
}

install_skills_by_tier() {
  note "Step 5.8: install ClawSeat skill symlinks"
  install_skill_tier_for_home claude "$HOME/.agents/skills"
  install_skill_tier_for_home claude "$HOME/.claude/skills"
  install_skill_tier_for_home gemini "$HOME/.gemini/skills"
  cleanup_legacy_gemini_skill_symlinks
  install_skill_tier_for_home codex "$HOME/.codex/skills"
  if [[ -d "$HOME/.openclaw" ]]; then
    install_skill_tier_for_home openclaw "$HOME/.openclaw/skills" \
      "clawseat-intake" "clawseat-koder"
  fi
}

install_privacy_pre_commit_hook() {
  note "Step 5.9: install privacy pre-commit hook"
  local hook_path="" hook_dir="" local_hook="" candidate="" idx=0 privacy_script="$REPO_ROOT/core/scripts/privacy-check.sh"

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] install privacy pre-commit hook for repo %q\n' "$PROJECT_REPO_ROOT"
    return 0
  fi
  if [[ ! -f "$privacy_script" ]]; then
    warn "privacy pre-commit hook skipped; missing privacy-check helper: $privacy_script"
    return 0
  fi
  if ! git -C "$PROJECT_REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    warn "privacy pre-commit hook skipped; not a git worktree: $PROJECT_REPO_ROOT"
    return 0
  fi
  hook_path="$(git -C "$PROJECT_REPO_ROOT" rev-parse --git-path hooks/pre-commit)"
  [[ "$hook_path" == /* ]] || hook_path="$PROJECT_REPO_ROOT/$hook_path"
  hook_dir="$(dirname "$hook_path")"
  mkdir -p "$hook_dir" || die 31 PRIVACY_HOOK_DIR_FAILED "unable to create $hook_dir"

  if [[ -f "$hook_path" ]] && grep -q 'CLAWSEAT_PRIVACY_CHECK_BEGIN' "$hook_path" 2>/dev/null; then
    chmod +x "$hook_path" || true
    return 0
  fi

  if [[ -e "$hook_path" || -L "$hook_path" ]]; then
    candidate="${hook_path}.clawseat-local"
    while [[ -e "$candidate" || -L "$candidate" ]]; do
      idx=$((idx + 1))
      candidate="${hook_path}.clawseat-local.$idx"
    done
    mv "$hook_path" "$candidate" || die 31 PRIVACY_HOOK_PRESERVE_FAILED "unable to preserve existing hook: $hook_path"
    chmod +x "$candidate" || true
    local_hook="$candidate"
  fi

  {
    printf '#!/usr/bin/env bash\n'
    printf 'set -euo pipefail\n'
    printf '# CLAWSEAT_PRIVACY_CHECK_BEGIN\n'
    printf 'bash %q\n' "$privacy_script"
    printf '# CLAWSEAT_PRIVACY_CHECK_END\n'
    if [[ -n "$local_hook" ]]; then
      printf 'if [[ -x %q ]]; then\n' "$local_hook"
      printf '  %q "$@"\n' "$local_hook"
      printf 'fi\n'
    fi
  } >"$hook_path" || die 31 PRIVACY_HOOK_WRITE_FAILED "unable to write $hook_path"
  chmod +x "$hook_path" || die 31 PRIVACY_HOOK_CHMOD_FAILED "unable to chmod $hook_path"
}

prompt_patrol_cron_optin() {
  local answer="${CLAWSEAT_PATROL_CRON_OPT_IN:-}"
  if [[ -z "$answer" ]]; then
    if [[ -t 0 && -t 1 ]]; then
      printf '[install] Patrol Cron 是否启用每日扫描？(y/N) '
      read -r answer
    else
      answer="n"
    fi
  fi

  if [[ "$answer" =~ ^[Yy]$ ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
      printf '[dry-run] %q %q install\n' "$PYTHON_BIN" "$PATROL_CRON_INSTALLER"
    elif [[ ! -f "$PATROL_CRON_INSTALLER" ]]; then
      warn "patrol cron skipped; missing helper: $PATROL_CRON_INSTALLER"
      return 0
    else
      "$PYTHON_BIN" "$PATROL_CRON_INSTALLER" install
    fi
    note "[install] Patrol Cron installed"
  else
    note "[install] Patrol Cron skipped"
  fi
}
