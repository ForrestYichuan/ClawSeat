#!/usr/bin/env bash
# shellcheck shell=bash
# Loaded by scripts/install.sh. Resolve this file with BASH_SOURCE so
# callers may source install.sh from any current working directory.
_CLAWSEAT_INSTALL_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_preflight_note() {
  if declare -F note >/dev/null 2>&1; then
    note "$@"
  else
    printf '==> %s\n' "$*"
  fi
}

_preflight_warn() {
  if declare -F warn >/dev/null 2>&1; then
    warn "$@"
  else
    printf 'WARN: %s\n' "$*" >&2
  fi
}

detect_sandbox_home() {
  local resolved_home="" real_home=""
  resolved_home="$(cd ~ 2>/dev/null && pwd || printf '%s\n' "$HOME")"
  real_home="${CLAWSEAT_REAL_HOME:-$resolved_home}"
  if [[ "$resolved_home" != "$HOME" || "$real_home" != "$HOME" ]]; then
    _preflight_warn "sandbox HOME: \$HOME=$HOME but ~ resolves to $resolved_home"
    _preflight_warn "Use absolute paths. Some INSTALL.md examples with ~ may fail."
    _preflight_warn "Example: use $resolved_home/coding/ClawSeat instead of ~/coding/ClawSeat"
  else
    _preflight_note "HOME: $HOME"
  fi
}

resolve_python_candidate() {
  local candidate="$1"
  if [[ "$candidate" == */* ]]; then
    [[ -x "$candidate" ]] || return 1
    printf '%s\n' "$candidate"
    return 0
  fi
  command -v "$candidate" 2>/dev/null || return 1
}

python_candidate_version() {
  local candidate="$1"
  "$candidate" -c 'import sys; print(".".join(str(part) for part in sys.version_info[:3]))' 2>/dev/null
}

python_version_supported() {
  local version="$1"
  local major="" minor="" patch=""
  IFS=. read -r major minor patch <<<"$version"
  [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ ]] || return 1
  (( major > 3 || (major == 3 && minor >= 11) ))
}

resolve_supported_python_bin() {
  local resolved="" version="" detail="" candidate=""
  local -a attempted=()
  local -a candidates=(
    "python3.13"
    "python3.12"
    "python3.11"
    "/opt/homebrew/bin/python3.13"
    "/opt/homebrew/bin/python3.12"
    "/opt/homebrew/bin/python3.11"
    "/usr/local/bin/python3.13"
    "/usr/local/bin/python3.12"
    "/usr/local/bin/python3.11"
    "python3"
    "python"
  )

  if [[ -n "$PYTHON_BIN_WAS_SET" && -n "$PYTHON_BIN_OVERRIDE" ]]; then
    resolved="$(resolve_python_candidate "$PYTHON_BIN_OVERRIDE" || true)"
    if [[ -z "$resolved" ]]; then
      die 2 INVALID_PYTHON_BIN \
        "PYTHON_BIN=$PYTHON_BIN_OVERRIDE was provided, but that executable was not found. ClawSeat install requires Python >= 3.11 before preflight can import. Try: PYTHON_BIN=/opt/homebrew/bin/python3.12 bash scripts/install.sh --provider 1"
    fi
    version="$(python_candidate_version "$resolved" || true)"
    if [[ -n "$version" ]] && python_version_supported "$version"; then
      PYTHON_BIN="$resolved"
      PYTHON_BIN_VERSION="$version"
      PYTHON_BIN_RESOLUTION="explicit"
      export PYTHON_BIN
      return 0
    fi
    detail="version probe failed"
    [[ -n "$version" ]] && detail="Python $version"
    die 2 INVALID_PYTHON_BIN \
      "PYTHON_BIN=$PYTHON_BIN_OVERRIDE resolves to $resolved ($detail), but ClawSeat install requires Python >= 3.11 before preflight can import. Try: PYTHON_BIN=/opt/homebrew/bin/python3.12 bash scripts/install.sh --provider 1"
  fi

  for candidate in "${candidates[@]}"; do
    resolved="$(resolve_python_candidate "$candidate" || true)"
    [[ -n "$resolved" ]] || continue
    version="$(python_candidate_version "$resolved" || true)"
    if [[ -n "$version" ]]; then
      attempted+=("$resolved=$version")
      if python_version_supported "$version"; then
        PYTHON_BIN="$resolved"
        PYTHON_BIN_VERSION="$version"
        PYTHON_BIN_RESOLUTION="auto"
        export PYTHON_BIN
        return 0
      fi
    fi
  done

  local attempted_summary="none"
  if [[ ${#attempted[@]} -gt 0 ]]; then
    attempted_summary="$(printf '%s' "${attempted[0]}")"
    local idx=1
    while (( idx < ${#attempted[@]} )); do
      attempted_summary+=", ${attempted[$idx]}"
      ((idx += 1))
    done
  fi
  die 2 MISSING_PYTHON311 \
    "No supported Python >= 3.11 found for ClawSeat install before preflight import. Detected: $attempted_summary. Install/use python3.11+ or run: PYTHON_BIN=/opt/homebrew/bin/python3.12 bash scripts/install.sh --provider 1"
}

_clawseat_main_ref_for_root() {
  local root="$1"
  if git -C "$root" rev-parse --verify --quiet clawseat/main >/dev/null 2>&1; then
    printf '%s\n' "clawseat/main"
    return 0
  fi
  if git -C "$root" rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
    printf '%s\n' "origin/main"
    return 0
  fi
  printf '\n'
}

_clawseat_commit_ts() {
  local root="$1"
  git -C "$root" log -1 --format=%ct HEAD 2>/dev/null || printf '0\n'
}

_clawseat_branch_has_upstream() {
  local root="$1"
  git -C "$root" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1
}

_clawseat_behind_main_count() {
  local root="$1" main_ref="$2"
  [[ -n "$main_ref" ]] || { printf '0\n'; return 0; }
  git -C "$root" rev-list --count "HEAD..$main_ref" 2>/dev/null || printf '0\n'
}

_select_fresh_clawseat_root() {
  local candidate_root="$1"
  local template_name="${2:-}"
  local worktree_list=""
  worktree_list="$(git -C "$candidate_root" worktree list --porcelain 2>/dev/null)" || {
    printf '%s\n' "$candidate_root"
    return 0
  }

  local main_ref=""
  main_ref="$(_clawseat_main_ref_for_root "$candidate_root")"

  local current_path="" current_head="" current_branch="" current_detached=0
  local best_path="" best_score=-1 best_branch="" best_head=""
  local worktree_count=0
  local -a skipped=()

  _consider_clawseat_worktree() {
    [[ -n "$current_path" ]] || return 0
    worktree_count=$((worktree_count + 1))

    local short_head="${current_head:0:12}"
    local branch_short="${current_branch#refs/heads/}"
    local behind=0 ts=0 score=0 reason=""
    behind="$(_clawseat_behind_main_count "$current_path" "$main_ref")"
    [[ "$behind" =~ ^[0-9]+$ ]] || behind=0

    if [[ "$current_detached" == "1" || -z "$current_branch" ]]; then
      reason="detached at ${short_head:-unknown} (${behind} commits behind main)"
      skipped+=("$current_path|$reason")
      return 0
    fi

    if [[ "$branch_short" != "main" && "$behind" -gt 0 ]]; then
      reason="branch $branch_short is ${behind} commits behind main"
      skipped+=("$current_path|$reason")
      return 0
    fi

    ts="$(_clawseat_commit_ts "$current_path")"
    [[ "$ts" =~ ^[0-9]+$ ]] || ts=0
    if [[ "$branch_short" == "main" ]]; then
      score=$((3000000000 + ts))
    elif _clawseat_branch_has_upstream "$current_path"; then
      score=$((2000000000 + ts))
    else
      score=$((1000000000 + ts))
    fi

    if [[ -z "$best_path" || "$score" -gt "$best_score" ]]; then
      best_path="$current_path"
      best_score="$score"
      best_branch="$branch_short"
      best_head="$short_head"
    fi
  }

  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" ]]; then
      _consider_clawseat_worktree
      current_path=""; current_head=""; current_branch=""; current_detached=0
      continue
    fi
    case "$line" in
      worktree\ *) current_path="${line#worktree }" ;;
      HEAD\ *) current_head="${line#HEAD }" ;;
      branch\ *) current_branch="${line#branch }" ;;
      detached) current_detached=1 ;;
    esac
  done <<<"$worktree_list"
  _consider_clawseat_worktree

  if [[ "$worktree_count" -le 1 || -z "$best_path" ]]; then
    printf '%s\n' "$candidate_root"
    return 0
  fi

  local selected_label="${best_branch:-DETACHED}: ${best_head:-unknown}"
  local item="" skip_path="" skip_reason=""
  if ((${#skipped[@]} > 0)); then
    for item in "${skipped[@]}"; do
      skip_path="${item%%|*}"
      skip_reason="${item#*|}"
      warn "ClawSeat REPO_ROOT=$skip_path is $skip_reason."
      warn "      Skipping in favor of $best_path ($selected_label)."
      warn "      Override: --force-repo-root <path>"
    done
  fi

  if [[ -n "$template_name" ]]; then
    local candidate_template="$candidate_root/templates/$template_name.toml"
    local selected_template="$best_path/templates/$template_name.toml"
    if [[ -f "$candidate_template" && ! -f "$selected_template" ]]; then
      warn "ClawSeat REPO_ROOT=$best_path does not contain template '$template_name'."
      warn "      Falling back to candidate worktree $candidate_root for requested template."
      printf '%s\n' "$candidate_root"
      return 0
    fi
  fi

  printf '%s\n' "$best_path"
}


ensure_host_deps() {
  detect_sandbox_home
  note "Step 1: preflight"
  if [[ "$FORCE_REINSTALL" != "1" && -f "$STATUS_FILE" ]] && grep -q '^phase=ready$' "$STATUS_FILE"; then
    # Round-8: even on the "already installed" fast-path, honor the
    # auto-patrol default. If the operator rerun without
    # --enable-auto-patrol but an existing LaunchAgent is still firing
    # (from a pre-Round-8 install), tear it down; otherwise the ghost
    # plist keeps injecting stale payloads even though install.sh
    # itself exited early and never reached Step 6.
    if [[ "$ENABLE_AUTO_PATROL" != "1" ]]; then
      uninstall_primary_patrol_plist_if_present
    fi
    printf 'Project %s already installed (phase=ready) at %s.\n' "$PROJECT" "$STATUS_FILE"
    printf 'Use --reinstall or --force to rebuild.\n'
    exit 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    local -a pf_dry_args=("$PYTHON_BIN" "$REPO_ROOT/core/preflight.py" "--project" "$PROJECT" "--phase" "bootstrap")
    [[ "${OPEN_NATIVE_WINDOWS:-1}" != "1" ]] && pf_dry_args+=("--no-window")
    printf '[dry-run] '
    printf '%q ' "${pf_dry_args[@]}"
    printf '\n'
    return 0
  fi

  local pf_out="" pf_rc=0
  local -a pf_args=("$REPO_ROOT/core/preflight.py" "--project" "$PROJECT" "--phase" "bootstrap")
  [[ "${OPEN_NATIVE_WINDOWS:-1}" != "1" ]] && pf_args+=("--no-window")
  if pf_out="$("$PYTHON_BIN" "${pf_args[@]}" 2>&1)"; then
    pf_rc=0
  else
    pf_rc=$?
  fi
  printf '%s\n' "$pf_out"
  if [[ $pf_rc -ne 0 ]]; then
    if [[ "$pf_out" == *"HARD_BLOCKED"* ]]; then
      die 10 PREFLIGHT_FAILED "preflight 检测到 HARD_BLOCKED 项。按上面 fix_command 修复后重跑 install.sh。"
    fi
    die 10 PREFLIGHT_FAILED "preflight failed. 按上面的输出修复后重跑 install.sh。"
  fi
  echo "OK: preflight"
}

ensure_python_tomllib_fallback() {
  note "Step 2.5: ensure Python tomllib fallback"
  if "$PYTHON_BIN" -c 'import tomllib' >/dev/null 2>&1; then
    return 0
  fi
  if "$PYTHON_BIN" -c 'import tomli' >/dev/null 2>&1; then
    return 0
  fi
  "$PYTHON_BIN" -m pip install --user --quiet tomli >/dev/null 2>&1 || true
}

scan_machine() {
  note "Step 2: environment scan"
  run "$PYTHON_BIN" "$SCAN_SCRIPT" --output "$MEMORY_ROOT"
  [[ "$DRY_RUN" == "1" ]] && { printf '[dry-run] verify %s\n' "$MEMORY_ROOT/machine/{credentials,network,openclaw,github,current_context}.json"; return; }
  local name
  for name in credentials network openclaw github current_context; do
    [[ -f "$MEMORY_ROOT/machine/$name.json" ]] || die 2 ENV_SCAN_INCOMPLETE "missing memory artifact: $MEMORY_ROOT/machine/$name.json"
  done
}

run_legacy_path_migration() {
  [[ -f "$MIGRATE_ANCESTOR_PATHS_SCRIPT" ]] || return 0
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] %q %q --project %q\n' "$PYTHON_BIN" "$MIGRATE_ANCESTOR_PATHS_SCRIPT" "$PROJECT"
    return 0
  fi
  "$PYTHON_BIN" "$MIGRATE_ANCESTOR_PATHS_SCRIPT" --project "$PROJECT" \
    || warn "legacy path migration failed (non-fatal); run $MIGRATE_ANCESTOR_PATHS_SCRIPT --project $PROJECT"
}

reconcile_seat_liveness_state() {
  note "Step 1.5: reconcile seat liveness state"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] %q %q --project %q\n' "$PYTHON_BIN" "$RECONCILE_SEAT_STATES_SCRIPT" "$PROJECT"
    return 0
  fi
  [[ -f "$RECONCILE_SEAT_STATES_SCRIPT" ]] || { warn "state.db reconcile skipped (missing $RECONCILE_SEAT_STATES_SCRIPT)"; return 0; }
  "$PYTHON_BIN" "$RECONCILE_SEAT_STATES_SCRIPT" --project "$PROJECT" \
    || warn "state.db reconcile skipped (non-fatal)"
}
