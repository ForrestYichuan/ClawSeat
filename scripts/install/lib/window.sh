#!/usr/bin/env bash
# shellcheck shell=bash
# Loaded by scripts/install.sh. Resolve this file with BASH_SOURCE so
# callers may source install.sh from any current working directory.
_CLAWSEAT_INSTALL_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

memory_patrol_cadence_seconds() {
  local cadence_minutes="${CLAWSEAT_MEMORY_PATROL_CADENCE_MINUTES:-${CLAWSEAT_ANCESTOR_PATROL_CADENCE_MINUTES:-30}}"
  if [[ ! "$cadence_minutes" =~ ^[0-9]+$ ]] || (( cadence_minutes <= 0 )); then
    cadence_minutes=30
  fi
  printf '%s\n' "$((cadence_minutes * 60))"
}

uninstall_primary_patrol_plist_if_present() {
  # Teardown idempotency: bootout is attempted *unconditionally* by label so
  # a ghost-loaded LaunchAgent (plist file manually deleted but the job is
  # still loaded in launchd) is still unloaded. `launchctl bootout ... || true`
  # is a no-op when the label is not loaded, so this is safe. File removal
  # (note + rm) is still gated on plist existence.
  local have_file=0
  [[ -f "$MEMORY_PATROL_PLIST_PATH" ]] && have_file=1

  if [[ "$have_file" == "1" ]]; then
    note "  cleanup: found stale $MEMORY_PATROL_PLIST_PATH — removing (auto-patrol disabled; upgrade path)"
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] launchctl bootout gui/%s/%s 2>/dev/null || true\n' "$(id -u)" "$MEMORY_PATROL_PLIST_LABEL"
    [[ "$have_file" == "1" ]] && printf '[dry-run] rm -f %q\n' "$MEMORY_PATROL_PLIST_PATH"
    return 0
  fi
  if [[ "$(uname -s)" == "Darwin" ]]; then
    launchctl bootout "gui/$(id -u)/$MEMORY_PATROL_PLIST_LABEL" 2>/dev/null || true
  fi
  [[ "$have_file" == "1" ]] || return 0
  rm -f "$MEMORY_PATROL_PLIST_PATH"
}

install_primary_patrol_plist() {
  if [[ "$ENABLE_AUTO_PATROL" != "1" ]]; then
    note "Step 6: auto-patrol disabled (default; pass --enable-auto-patrol to install a periodic plist that sends a natural-language patrol request)"
    # Upgrade path: if a previous install had the plist enabled, tear it down
    # so the project actually becomes manual-by-default.
    uninstall_primary_patrol_plist_if_present
    return 0
  fi
  note "Step 6: install QA patrol LaunchAgent (--enable-auto-patrol)"
  [[ -f "$MEMORY_PATROL_TEMPLATE" || "$DRY_RUN" == "1" ]] || die 31 MEMORY_PATROL_TEMPLATE_MISSING "missing patrol plist template: $MEMORY_PATROL_TEMPLATE"

  local cadence_seconds="" launchd_domain=""
  cadence_seconds="$(memory_patrol_cadence_seconds)"
  launchd_domain="gui/$(id -u)"

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] mkdir -p %q %q\n' "$(dirname "$MEMORY_PATROL_PLIST_PATH")" "$MEMORY_PATROL_LOG_DIR"
    printf '[dry-run] render %s -> %s\n' "$MEMORY_PATROL_TEMPLATE" "$MEMORY_PATROL_PLIST_PATH"
    printf '[dry-run] launchctl bootout %s/%s 2>/dev/null || true\n' "$launchd_domain" "$MEMORY_PATROL_PLIST_LABEL"
    printf '[dry-run] launchctl bootstrap %s %q\n' "$launchd_domain" "$MEMORY_PATROL_PLIST_PATH"
    return 0
  fi

  mkdir -p "$(dirname "$MEMORY_PATROL_PLIST_PATH")" "$MEMORY_PATROL_LOG_DIR" \
    || die 31 MEMORY_PATROL_DIR_FAILED "unable to create patrol plist/log directories"

  sed \
    -e "s|{PROJECT}|${PROJECT}|g" \
    -e "s|{CADENCE_SECONDS}|${cadence_seconds}|g" \
    -e "s|{CLAWSEAT_ROOT}|${CLAWSEAT_ROOT}|g" \
    -e "s|{LOG_DIR}|${MEMORY_PATROL_LOG_DIR}|g" \
    "$MEMORY_PATROL_TEMPLATE" > "$MEMORY_PATROL_PLIST_PATH" \
    || die 31 MEMORY_PATROL_RENDER_FAILED "unable to render $MEMORY_PATROL_PLIST_PATH"
  chmod 644 "$MEMORY_PATROL_PLIST_PATH" \
    || die 31 MEMORY_PATROL_CHMOD_FAILED "unable to chmod $MEMORY_PATROL_PLIST_PATH"

  if command -v plutil >/dev/null 2>&1; then
    plutil -lint "$MEMORY_PATROL_PLIST_PATH" >/dev/null 2>&1 \
      || die 31 MEMORY_PATROL_INVALID "rendered patrol plist is not valid XML: $MEMORY_PATROL_PLIST_PATH"
  fi

  if [[ "$(uname -s)" != "Darwin" ]]; then
    warn "Skipping launchctl bootstrap for QA patrol on non-macOS host."
    return 0
  fi
  if ! command -v launchctl >/dev/null 2>&1; then
    if is_sandbox_install; then
      warn "Skipping launchctl bootstrap for QA patrol in sandbox/headless install: launchctl missing."
      return 0
    fi
    die 31 MEMORY_PATROL_LAUNCHCTL_MISSING "launchctl is required to bootstrap $MEMORY_PATROL_PLIST_PATH"
  fi

  launchctl bootout "${launchd_domain}/${MEMORY_PATROL_PLIST_LABEL}" 2>/dev/null || true
  if ! launchctl bootstrap "$launchd_domain" "$MEMORY_PATROL_PLIST_PATH" 2>/dev/null; then
    if is_sandbox_install; then
      warn "Skipping launchctl bootstrap for QA patrol in sandbox/headless install."
      return 0
    fi
    die 31 MEMORY_PATROL_BOOTSTRAP_FAILED "failed to bootstrap $MEMORY_PATROL_PLIST_PATH"
  fi
}

install_seat_clear_watchdog() {
  note "Step 6.5: install universal clear/compact watchdog"
  if [[ ! -f "$SEAT_CLEAR_WATCHDOG_INSTALLER" && "$DRY_RUN" != "1" ]]; then
    warn "seat clear/compact watchdog skipped; missing installer: $SEAT_CLEAR_WATCHDOG_INSTALLER"
    return 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] %q %q --clawseat-root %q --home %q --python-bin %q\n' \
      "$PYTHON_BIN" "$SEAT_CLEAR_WATCHDOG_INSTALLER" "$CLAWSEAT_ROOT" "$HOME" "$PYTHON_BIN"
    return 0
  fi
  "$PYTHON_BIN" "$SEAT_CLEAR_WATCHDOG_INSTALLER" \
    --clawseat-root "$CLAWSEAT_ROOT" \
    --home "$HOME" \
    --python-bin "$PYTHON_BIN" \
    || warn "seat clear/compact watchdog install failed; continue without scheduled watchdog"
}

configure_tmux_session_display() {
  local session="$1"
  # tmux accepts `=name` for exact matching in has-session, but set-option
  # rejects that target form on this host. Use the plain session name here.
  run tmux set-option -t "$session" detach-on-destroy off
  run tmux set-option -t "$session" status on
  run tmux set-option -t "$session" status-left "[#{session_name}] "
  run tmux set-option -t "$session" status-right "#{?client_attached,ATTACHED,WAITING} | %H:%M"
  run tmux set-option -t "$session" status-style "fg=white,bg=blue,bold"
}

ensure_tmux_session_alive() {
  local session="$1"
  if tmux has-session -t "=$session" 2>/dev/null; then
    return 0
  fi
  die 31 TMUX_SESSION_DIED_AFTER_LAUNCH "tmux session vanished before display configuration: $session"
}

post_spawn_trust_folder_auto_enter() {
  local session_name="$1" tool="$2" delay="" content=""
  [[ "$tool" == "claude" ]] || return 0
  delay="${CLAWSEAT_TRUST_PROMPT_SLEEP_SECONDS:-3}"
  sleep "$delay"
  content="$(tmux capture-pane -t "=$session_name" -p -S -50 2>/dev/null || true)"
  if printf '%s\n' "$content" | grep -qE "Trust folder|Quick safety check|trust the files"; then
    tmux send-keys -t "=$session_name" "" Enter
    note "Auto-confirmed Trust folder prompt for ${session_name}"
  fi
}

launch_seat() {
  local session="$1" cwd="${2:-$REPO_ROOT}" brief_path="${3:-}" seat_id="${4:-}" auth_mode="" custom_env_file="" launcher_tool="" actual_session=""
  local launcher_model=""
  launcher_tool="$(launcher_tool_for_seat "$seat_id")"
  actual_session="$(seat_tmux_name "$session" "$launcher_tool")"
  auth_mode="$(launcher_auth_for_seat "$seat_id")"
  launcher_model="$(memory_effective_model)"
  custom_env_file="$(launcher_custom_env_file_for_session "$actual_session")"

  if [[ "$DRY_RUN" == "1" ]]; then
    run tmux kill-session -t "=$actual_session"
  else
    tmux kill-session -t "=$actual_session" 2>/dev/null || true
    mkdir -p "$cwd" || die 31 TMUX_CWD_CREATE_FAILED "unable to create launcher cwd: $cwd"
  fi

  local -a cmd=(env "CLAWSEAT_ROOT=$CLAWSEAT_ROOT")
  cmd+=("CLAWSEAT_PROJECT=$PROJECT")
  cmd+=("REAL_HOME=${REAL_HOME:-$HOME}")
  cmd+=("CLAWSEAT_MEMORY_BRIEF=$brief_path")
  cmd+=("CLAWSEAT_ANCESTOR_BRIEF=$brief_path")
  [[ -n "$seat_id" ]] && cmd+=("CLAWSEAT_SEAT=$seat_id")
  if [[ "$seat_id" == "$PRIMARY_SEAT_ID" && "$launcher_tool" != "claude" && -n "$launcher_model" ]]; then
    cmd+=("LAUNCHER_CUSTOM_MODEL=$launcher_model")
  fi
  [[ -n "${CLAWSEAT_NO_AUTO_RESUME:-}" ]] && cmd+=("CLAWSEAT_NO_AUTO_RESUME=$CLAWSEAT_NO_AUTO_RESUME")
  cmd+=(bash "$LAUNCHER_SCRIPT" --headless --tool "$launcher_tool" --auth "$auth_mode" --dir "$cwd" --session "$actual_session")
  [[ -n "$custom_env_file" ]] && cmd+=(--custom-env-file "$custom_env_file")
  [[ "$DRY_RUN" == "1" ]] && cmd+=(--dry-run)

  if [[ "$DRY_RUN" == "1" ]]; then
    run "${cmd[@]}"
    configure_tmux_session_display "$actual_session"
    return 0
  fi

  if ! "${cmd[@]}"; then
    [[ -n "$custom_env_file" && -f "$custom_env_file" ]] && rm -f "$custom_env_file"
    die 31 TMUX_SESSION_CREATE_FAILED "unable to launch tmux session via agent-launcher: $actual_session"
  fi
  ensure_tmux_session_alive "$actual_session"
  configure_tmux_session_display "$actual_session"
  post_spawn_trust_folder_auto_enter "$actual_session" "$launcher_tool"
}

check_iterm_window_exists() {
  local title="$1"
  if ! command -v osascript >/dev/null 2>&1; then
    printf '0\n'
    return 0
  fi
  osascript - "$title" <<'APPLESCRIPT' 2>/dev/null || printf '0\n'
on run argv
  set wanted to item 1 of argv
  tell application "iTerm"
    repeat with w in windows
      try
        if (name of w as string) contains wanted then
          return "1"
        end if
      end try
    end repeat
  end tell
  return "0"
end run
APPLESCRIPT
}

is_sandbox_install() {
  if PYTHONPATH="$REPO_ROOT/core/lib${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" - "$CALLER_HOME" <<'PY' >/dev/null 2>&1
import sys
from pathlib import Path

from real_home import is_sandbox_home, real_user_home

caller_home = Path(sys.argv[1]).expanduser()
real_home = real_user_home()
raise SystemExit(0 if is_sandbox_home(caller_home) or caller_home != real_home else 1)
PY
  then
    return 0
  fi

  case "$CALLER_HOME" in
    *"/.agents/runtime/identities/"*|*"/.agent-runtime/identities/"*)
      return 0
      ;;
  esac
  return 1
}

open_iterm_window() {
  local payload="$1" target_var="$2" err_file out status timeout_bin
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] %q %q <<JSON\n%s\nJSON\n' "$PYTHON_BIN" "$ITERM_DRIVER" "$payload"
    printf -v "$target_var" '%s' "dry-run-$target_var"; return
  fi
  if [[ "$(uname -s)" != "Darwin" ]]; then
    if is_sandbox_install; then
      warn "Skipping iTerm window open in sandbox/headless install: native iTerm panes require macOS."
      printf -v "$target_var" '%s' ""
      return 0
    fi
    die 40 ITERM_MACOS_ONLY "native iTerm panes require macOS."
  fi
  if ! "$PYTHON_BIN" -c 'import iterm2' >/dev/null 2>&1; then
    if is_sandbox_install; then
      warn "Skipping iTerm window open in sandbox/headless install: missing iterm2 module."
      printf -v "$target_var" '%s' ""
      return 0
    fi
    die 40 ITERM2_PYTHON_MISSING "missing iterm2 module; install with: pip3 install --user --break-system-packages iterm2"
  fi
  err_file="$(mktemp)"
  if command -v timeout >/dev/null 2>&1; then
    timeout_bin="timeout"
  elif command -v gtimeout >/dev/null 2>&1; then
    timeout_bin="gtimeout"
  else
    timeout_bin=""
    warn "timeout/gtimeout not found; running iTerm pane driver without wall-clock timeout."
  fi
  if [[ -n "$timeout_bin" ]]; then
    out="$(
      printf '%s' "$payload" | "$timeout_bin" "${ITERM_DRIVER_TIMEOUT_SECONDS}s" "$PYTHON_BIN" "$ITERM_DRIVER" 2>"$err_file"
    )"
  else
    out="$(
      printf '%s' "$payload" | "$PYTHON_BIN" "$ITERM_DRIVER" 2>"$err_file"
    )"
  fi || {
    status=$?
    cat "$err_file" >&2
    rm -f "$err_file"
    if [[ "$status" == "124" ]]; then
      if is_sandbox_install; then
        warn "Skipping iTerm window open in sandbox/headless install: iTerm pane driver timed out after ${ITERM_DRIVER_TIMEOUT_SECONDS}s."
        printf -v "$target_var" '%s' ""
        return 0
      fi
      die 40 ITERM_DRIVER_FAILED "iTerm pane driver timed out after ${ITERM_DRIVER_TIMEOUT_SECONDS}s."
    fi
    if is_sandbox_install; then
      warn "Skipping iTerm window open in sandbox/headless install: iTerm pane driver execution failed."
      printf -v "$target_var" '%s' ""
      return 0
    fi
    die 40 ITERM_DRIVER_FAILED "iTerm pane driver execution failed."
  }
  [[ ! -s "$err_file" ]] || cat "$err_file" >&2; rm -f "$err_file"
  status="$(printf '%s' "$out" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' 2>/dev/null || true)"
  [[ "$status" == "ok" ]] || die 40 ITERM_LAYOUT_FAILED "$(printf '%s' "$out" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("reason","driver returned non-ok status"))' 2>/dev/null || echo "driver returned non-ok status")"
  printf -v "$target_var" '%s' "$(printf '%s' "$out" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("window_id",""))')"
}

focus_iterm_window() {
  [[ "$DRY_RUN" == "1" ]] && { printf '[dry-run] focus iTerm window %s pane %s\n' "$1" "$2"; return; }
  "$PYTHON_BIN" - "$1" "$2" <<'PY' || die 41 ITERM_FOCUS_FAILED "unable to focus iTerm window/pane."
import sys, iterm2
target_window, target_label = sys.argv[1], sys.argv[2]
async def main(connection):
    app = await iterm2.async_get_app(connection); await app.async_activate()
    for window in app.windows:
        if window.window_id != target_window: continue
        await window.async_activate()
        for tab in window.tabs:
            for session in getattr(tab, "sessions", []):
                if getattr(session, "name", "") == target_label:
                    await session.async_activate(); return
        return
    raise SystemExit(1)
iterm2.run_until_complete(main)
PY
}

grid_payload() {
  # v1 compat: single-window project grid (primary seat + N workers)
  # v2 callers should use workers_payload() + memories_payload() instead.
  "$PYTHON_BIN" - "$PROJECT" "$WAIT_FOR_SEAT_SCRIPT" "$PRIMARY_SEAT_ID" "$(primary_tmux_name)" "${PENDING_SEATS[@]}" <<'PY'
import json
import shlex
import sys

project, wait_script, primary_seat, primary_session = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
seats = sys.argv[5:]
panes = [
    {"label": primary_seat, "command": f"tmux attach -t '={primary_session}'"},
]
for seat in seats:
    panes.append(
        {
            "label": seat,
            "command": "bash "
            + shlex.quote(wait_script)
            + " "
            + shlex.quote(project)
            + " "
            + shlex.quote(seat),
        }
    )
print(json.dumps({"title": f"clawseat-{project}", "panes": panes}, ensure_ascii=False))
PY
}

# v2 workers_payload: template-defined main worker left 50% + N-1 workers right side.
# Recipe per template window_layout.workers_grid.right_fill_order:
#
# right_fill_order=col-major (default): single right column, top-to-bottom fill
#   N_workers=1 (main only): []
#   N_workers=2 (main+1):    [[0, True]]
#   N_workers=3 (main+2):    [[0, True], [1, False]]
#   N_workers=4 (main+3):    [[0, True], [1, False], [2, False]]
#
# right_fill_order=grid-2-rows (legacy RFC-001 §3.1): max 2 rows, expand cols
#   N_workers=4 (main+3):    [[0, True], [1, True], [1, False]]
#   N_workers=5 (main+4):    [[0, True], [1, True], [1, False], [2, False]]
#
# right_fill_order=balanced-2x2: equal 2x2 grid (all 4 panes same size; ignores
# left_main 50%-width). Required for clawseat-creative 4-worker layouts where
# col-major collapses right column to <24 rows (TUI breakage threshold).
#   N_workers=4 (main+3):    [[0, True], [0, False], [1, False]]
#   Other N_workers:         error (balanced-2x2 only valid for exactly 4 workers)

workers_payload() {
  "$PYTHON_BIN" - "$PROJECT" "$WAIT_FOR_SEAT_SCRIPT" "$REPO_ROOT/templates/${CLAWSEAT_TEMPLATE_NAME}.toml" "${PENDING_SEATS[@]}" <<'PY'
import json
import shlex
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

project, wait_script, template_path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
seats = sys.argv[4:]  # PENDING_SEATS minus PRIMARY_SEAT_ID
if not seats:
    raise SystemExit("workers_payload requires at least one worker seat")

layout = {}
if template_path.is_file():
    with template_path.open("rb") as fh:
        data = tomllib.load(fh)
    layout = data.get("window_layout", {}).get("workers_grid", {})

main_seat = str(layout.get("left_main_seat") or ("planner" if "planner" in seats else seats[0]))
if main_seat not in seats:
    raise SystemExit(f"workers_payload left_main_seat {main_seat!r} is not in seats list")

configured_right = [str(seat) for seat in layout.get("right_seats", []) if str(seat) in seats and str(seat) != main_seat]
remaining = [seat for seat in seats if seat != main_seat and seat not in configured_right]
right_seats = configured_right + remaining
fill_order = str(layout.get("right_fill_order", "col-major"))

# Build right-side recipe from template fill order.
def right_recipe(n_right: int, fill_order: str) -> list[list]:
    """Returns split steps relative to pane indices in the COMBINED layout
    (main worker is pane 0; right starts as pane 1 after first vertical split)."""
    if fill_order not in {"col-major", "grid-2-rows", "balanced-2x2"}:
        raise SystemExit(f"unknown right_fill_order: {fill_order!r}")
    if n_right == 0: return []
    if n_right == 1: return []  # right side is single pane (after the main-vs-right split)

    if fill_order == "col-major":
        return [[idx, False] for idx in range(1, n_right)]

    if fill_order == "balanced-2x2":
        # Equal 2x2 grid for exactly 4 workers (main + 3 right). After the
        # outer [[0, True]] main-vs-right split: split pane 0 (main) horizontally
        # → BL pane; split pane 1 (right) horizontally → BR pane. Result:
        # pane 0=TL (main), pane 1=TR, pane 2=BL, pane 3=BR.
        if n_right != 3:
            raise SystemExit(
                f"balanced-2x2 requires exactly 3 right_seats (4 workers total); "
                f"got {n_right}. Use col-major or grid-2-rows for "
                f"{n_right + 1}-worker layouts."
            )
        return [[0, False], [1, False]]

    # grid-2-rows: legacy max-2-rows formula. Build top row of right, then
    # horizontal splits per col.
    cols = (n_right + 1) // 2
    splits: list[list[int]] = []
    # Build top row of right area: split pane 1 vertically (cols-1) times
    for col in range(1, cols):
        # parent index in combined layout: 0=main, 1=right_col0, 2=right_col1, ...
        splits.append([col, True])
    # Horizontal splits: each top right pane splits into bottom (col-major)
    cols_with_bottom = n_right - cols  # number of cols that need a bottom pane
    for col in range(cols_with_bottom):
        splits.append([col + 1, False])  # +1 because pane 0 is main
    return splits

recipe = ([[0, True]] + right_recipe(len(right_seats), fill_order)) if right_seats else []

# Pane order for payload (matches recipe creation order):
#   pane[0] = main worker (left)
#   pane[1] = right col_0 top (first right worker)
#   pane[2..cols] = top of subsequent right cols
#   pane[cols+1..] = bottom row of right cols (col-major)
#
# NB: workers are spawned by memory during Phase-A, not launched directly by
# install.sh. Their tmux sessions may not exist yet, so every worker pane uses
# wait-for-seat.sh instead of direct `tmux attach`.
panes = [
    {
        "label": main_seat,
        "command": "bash "
        + shlex.quote(wait_script)
        + " "
        + shlex.quote(project)
        + " "
        + shlex.quote(main_seat),
    },
]
def right_order(n_right: int, fill_order: str) -> list[int]:
    if fill_order not in {"col-major", "grid-2-rows", "balanced-2x2"}:
        raise SystemExit(f"unknown right_fill_order: {fill_order!r}")
    if fill_order in ("col-major", "balanced-2x2"):
        return list(range(n_right))

    cols = max(1, (n_right + 1) // 2 if n_right >= 3 else 1)
    # For n_right=1: just first right_seat
    # For n_right=2: top + bottom (1 col)
    # For n_right>=3: row-major in driver order = top row left-to-right + bottom row left-to-right
    if n_right == 1:
        return [0]
    if n_right == 2:
        return [0, 1]  # top, bottom

    # User intent (grid-2-rows): user_idx 0=col0_top, 1=col0_bot,
    # 2=col1_top, 3=col1_bot, ...
    # Driver order: top row first (col0_top, col1_top, col2_top, ...), then bottom row.
    ordering = []
    for col in range(cols):
        user_idx = col * 2
        if user_idx < n_right:
            ordering.append(user_idx)
    for col in range(cols):
        user_idx = col * 2 + 1
        if user_idx < n_right:
            ordering.append(user_idx)
    return ordering

# Compute right-side fill order matching recipe pane creation.
n_right = len(right_seats)
if n_right > 0:
    ordering = right_order(n_right, fill_order)
    for driver_idx, user_idx in enumerate(ordering):
        seat = right_seats[user_idx]
        panes.append(
            {
                "label": seat,
                "command": "bash "
                + shlex.quote(wait_script)
                + " "
                + shlex.quote(project)
                + " "
                + shlex.quote(seat),
            }
        )

print(json.dumps({
    "title": f"clawseat-{project}-workers",
    "panes": panes,
    "recipe": recipe,
}, ensure_ascii=False))
PY
}

# v2 memories_payload: prefer ~/.clawseat/projects.json, fallback to live tmux memory sessions.

memories_payload() {
  PYTHONPATH="$REPO_ROOT/core/scripts${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" - <<'PY'
import json
import os
import subprocess

from projects_registry import enumerate_projects


def registry_tabs():
    tabs = []
    for entry in enumerate_projects():
        name = entry.name
        tmux_name = entry.tmux_name
        if not name or not tmux_name:
            continue
        tabs.append({
            "name": name,
            "command": f"tmux attach -t '={tmux_name}'",
        })
    return tabs


def tmux_fallback_tabs():
    result = subprocess.run(
        ["tmux", "ls", "-F", "#{session_name}"],
        capture_output=True, text=True, env={**os.environ, "TMUX": ""}, check=False,
    )
    all_sessions = result.stdout.strip().split("\n") if result.returncode == 0 else []
    legacy_global_memory = "-".join(("machine", "memory", "claude"))
    memory_sessions = sorted([
        s for s in all_sessions
        if s.endswith("-memory") and s != legacy_global_memory
    ])
    return [
        {
            "name": sess[:-len("-memory")],
            "command": f"tmux attach -t '={sess}'",
        }
        for sess in memory_sessions
    ]


tabs = registry_tabs() or tmux_fallback_tabs()
if not tabs:
    print(json.dumps({"status": "skip", "reason": "no registered or live project memory sessions found"}))
    raise SystemExit(0)

print(json.dumps({
    "mode": "tabs",
    "title": "clawseat-memories",
    "tabs": tabs,
    "ensure": True,
}, ensure_ascii=False))
PY
}
