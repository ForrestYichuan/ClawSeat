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

if ! declare -F load_custom_env >/dev/null; then
  # shellcheck source=../helpers/env.sh
  source "$LAUNCHER_DIR/helpers/env.sh"
fi
if ! declare -F resolve_claude_secret_file >/dev/null; then
  # shellcheck source=../helpers/auth.sh
  source "$LAUNCHER_DIR/helpers/auth.sh"
fi
if ! declare -F seed_user_tool_dirs >/dev/null; then
  # shellcheck source=../helpers/sandbox.sh
  source "$LAUNCHER_DIR/helpers/sandbox.sh"
fi

run_claude_runtime() {
  local auth_mode="$1"
  local workdir="$2"
  local session_name="$3"
  local mode_label="Claude Code"
  local -a resume_args=()
  local resume_label=""
  local resume_session_id=""

  if [[ "${CLAWSEAT_NO_AUTO_RESUME:-0}" != "1" ]]; then
    resume_session_id="$(launcher_read_active_session_id "${CLAWSEAT_SEAT:-}" 2>/dev/null || true)"
    if [[ -n "$resume_session_id" ]]; then
      resume_args=(--resume "$resume_session_id")
      resume_label="$resume_session_id"
    fi
  fi

  if [[ "$auth_mode" == "oauth" ]]; then
    # "oauth" in ClawSeat is not "launcher does OAuth" — it's "reuse the
    # host's existing Claude Code login state" (OAuth handled by Claude
    # Code itself, not us). To make that work we must:
    #   1. route HOME + every XDG base dir back to REAL_HOME so Claude
    #      finds its own ~/.claude, ~/.claude.json, keychain session,
    #      and XDG cache/state (credentials, project history)
    #   2. drop all agent-launcher-provided token env vars so Claude
    #      doesn't prefer a stale token/API-mode path over its own
    #      native login flow
    #   3. PRESERVE host-supplied PROXY / TLS / timeout / Claude-Desktop
    #      managed OAuth markers (see capture_oauth_host_env in env.sh).
    #      Without preservation, OAuth seats in region-restricted networks
    #      (China etc.) lose HTTPS_PROXY and hit transient 403 "Request
    #      not allowed" while host Claude Desktop works fine. See the
    #      2026-05-03 install-memory 403 investigation in MEMORY.md.
    # Older ClawSeat builds only restored HOME and missed XDG + CLAUDE_CODE_OAUTH_TOKEN,
    # leaving Claude looking at sandbox XDG dirs and picking up stale token env —
    # the root cause of the "re-auth every start" bug.
    local _oauth_host_env_snapshot
    _oauth_host_env_snapshot="$(capture_oauth_host_env)"
    unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL ANTHROPIC_MODEL
    unset CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CODE_SUBSCRIBER_SUBSCRIPTION_ID
    export HOME="$REAL_HOME"
    export XDG_CONFIG_HOME="$REAL_HOME/.config"
    export XDG_DATA_HOME="$REAL_HOME/.local/share"
    export XDG_CACHE_HOME="$REAL_HOME/.cache"
    export XDG_STATE_HOME="$REAL_HOME/.local/state"
    # Re-apply preserved host env after the wipe.
    eval "$_oauth_host_env_snapshot"
    seed_user_tool_dirs "$HOME" "${CLAWSEAT_PROJECT:-}"
    prepare_claude_host_oauth_state "$HOME" "$workdir"
    cd "$workdir"
    echo "────────────────────────────────────────"
    echo " Claude Code · Host OAuth (reuse)"
    echo " Session:    $session_name"
    echo " Directory:  $workdir"
    echo " HOME:       $HOME"
    echo " XDG_*:      \$REAL_HOME/{config,cache,state,local/share}"
    if [[ -n "${HTTPS_PROXY:-}${HTTP_PROXY:-}" ]]; then
      echo " PROXY:      ${HTTPS_PROXY:-${HTTP_PROXY:-}}"
    fi
    if [[ "${CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:-}" == "1" ]]; then
      echo " Host-managed OAuth: yes (Claude Desktop wrapper)"
    fi
    # Crash-recovery fallback: if Stop hook didn't fire (seat killed before
    # clean exit) but cwd has prior Claude history, --continue picks up the
    # most recent conversation. Only used when no precise --resume <uuid>
    # is available from the hook-written .session file.
    if [[ -z "$resume_label" && "${CLAWSEAT_NO_AUTO_RESUME:-0}" != "1" ]] \
        && _has_claude_cwd_history "$HOME" "$workdir"; then
      resume_args=(--continue)
      resume_label="--continue (latest in cwd)"
    fi
    echo "────────────────────────────────────────"
    [[ -n "$resume_label" ]] && launcher_resume_banner "$resume_label" >&2
    # Parent Codex/Claude sessions can export CLAUDECODE. Claude Code treats
    # that as an active nested session and exits immediately, which makes the
    # tmux seat disappear before restart-seat can observe it.
    unset CLAUDECODE
    # CARTOONER_CLAUDE_CODE_EXECUTABLE: when set (cartooner-spawned seats),
    # bypass PATH resolution and exec the absolute binary directly. Avoids
    # the pnpm sh wrapper layer that otherwise sits between tmux pane and
    # native claude (process tree integrity for ClawSeat's pane_pid checks).
    # Falls back to PATH `claude` for non-cartooner invocations.
    exec "${CARTOONER_CLAUDE_CODE_EXECUTABLE:-claude}" --dangerously-skip-permissions ${resume_args[@]+"${resume_args[@]}"}
  fi

  local secret_file="" runtime_dir
  if [[ "$auth_mode" != "custom" ]]; then
    secret_file="$(resolve_claude_secret_file "$auth_mode")"
    if [[ ! -f "$secret_file" ]]; then
      show_claude_auth_setup_hint "$auth_mode"
      echo "error: missing Claude secret file: $secret_file" >&2
      exit 1
    fi
  fi

  case "$auth_mode" in
    oauth_token)
      runtime_dir="$REAL_HOME/.agent-runtime/identities/claude/oauth_token/${auth_mode}-${session_name}"
      ;;
    *)
      runtime_dir="$REAL_HOME/.agent-runtime/identities/claude/api/${auth_mode}-${session_name}"
      ;;
  esac
  mkdir -p \
    "$runtime_dir/home" \
    "$runtime_dir/xdg/config" \
    "$runtime_dir/xdg/data" \
    "$runtime_dir/xdg/cache" \
    "$runtime_dir/xdg/state"

  # Same host-env preservation pattern as the oauth (host reuse) branch
  # for PROXY/TLS/timeout — non-OAuth seats also need them to reach
  # custom endpoints from region-restricted networks.
  #
  # CRITICAL: pass `0` to skip host-managed CLAUDE_CODE_* preservation.
  # When CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST=1 (Claude Desktop wrapper),
  # the host injects CLAUDE_CODE_OAUTH_TOKEN into every child process —
  # including non-OAuth seats. Claude Code CLI prefers that token over
  # ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY, so leaking it into a
  # custom / minimax / xcode / deepseek seat sends an Anthropic OAuth
  # token to a non-Anthropic endpoint and gets a spurious 401
  # ("Authentication Fails, Your api key: ****QAA is invalid").
  # The oauth_token branch below re-exports CLAUDE_CODE_OAUTH_TOKEN from
  # the secret file, not from host env, so dropping host vars here is
  # always safe.
  local _claude_host_env_snapshot
  _claude_host_env_snapshot="$(capture_oauth_host_env 0)"
  unset CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CODE_SUBSCRIBER_SUBSCRIPTION_ID
  unset CLAUDE_CODE_RATE_LIMIT_TIER CLAUDE_CODE_SUBSCRIPTION_TYPE
  unset CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST
  unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL ANTHROPIC_MODEL
  eval "$_claude_host_env_snapshot"

  if [[ "$auth_mode" == "custom" ]]; then
    load_custom_env "$CUSTOM_ENV_FILE"
    # Claude Code reports a conflict if both ANTHROPIC_AUTH_TOKEN and
    # ANTHROPIC_API_KEY are set. For non-anthropic.com endpoints
    # (minimax / xcode-best / any custom proxy) the correct variable is
    # AUTH_TOKEN. anthropic-console seats use --auth anthropic-console,
    # which lands in the explicit branch below and sets API_KEY only.
    export ANTHROPIC_AUTH_TOKEN="${LAUNCHER_CUSTOM_API_KEY:-}"
    export ANTHROPIC_BASE_URL="${LAUNCHER_CUSTOM_BASE_URL:-}"
    export ANTHROPIC_MODEL="${LAUNCHER_CUSTOM_MODEL:-}"
    export API_TIMEOUT_MS=3000000
    export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
    mode_label="Custom API"
  else
    set -a
    source "$secret_file"
    set +a
    case "$auth_mode" in
      oauth_token)
        if ! env_file_has_key "$secret_file" "CLAUDE_CODE_OAUTH_TOKEN"; then
          show_claude_auth_setup_hint "$auth_mode"
          echo "error: CLAUDE_CODE_OAUTH_TOKEN missing in $secret_file" >&2
          exit 1
        fi
        export CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}"
        mode_label="OAuth token"
        ;;
      anthropic-console)
        if ! env_file_has_key "$secret_file" "ANTHROPIC_API_KEY"; then
          show_claude_auth_setup_hint "$auth_mode"
          echo "error: ANTHROPIC_API_KEY missing in $secret_file" >&2
          exit 1
        fi
        export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
        mode_label="Anthropic Console API"
        ;;
      minimax)
        if [[ -n "${CUSTOM_ENV_FILE:-}" ]] && [[ -f "$CUSTOM_ENV_FILE" ]]; then
          load_custom_env "$CUSTOM_ENV_FILE"
          export ANTHROPIC_AUTH_TOKEN="${LAUNCHER_CUSTOM_API_KEY:-}"
          export ANTHROPIC_BASE_URL="${LAUNCHER_CUSTOM_BASE_URL:-}"
          export ANTHROPIC_MODEL="${LAUNCHER_CUSTOM_MODEL:-}"
        fi
        export API_TIMEOUT_MS=3000000
        export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
        mode_label="MiniMax API"
        ;;
      deepseek)
        if [[ -n "${CUSTOM_ENV_FILE:-}" ]] && [[ -f "$CUSTOM_ENV_FILE" ]]; then
          load_custom_env "$CUSTOM_ENV_FILE"
          export ANTHROPIC_AUTH_TOKEN="${LAUNCHER_CUSTOM_API_KEY:-}"
          export ANTHROPIC_BASE_URL="${LAUNCHER_CUSTOM_BASE_URL:-}"
          export ANTHROPIC_MODEL="${LAUNCHER_CUSTOM_MODEL:-}"
        fi
        export API_TIMEOUT_MS=3000000
        export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
        mode_label="DeepSeek API"
        ;;
      xcode)
        if [[ -n "${CUSTOM_ENV_FILE:-}" ]] && [[ -f "$CUSTOM_ENV_FILE" ]]; then
          load_custom_env "$CUSTOM_ENV_FILE"
          export ANTHROPIC_AUTH_TOKEN="${LAUNCHER_CUSTOM_API_KEY:-}"
          export ANTHROPIC_BASE_URL="${LAUNCHER_CUSTOM_BASE_URL:-}"
          export ANTHROPIC_MODEL="${LAUNCHER_CUSTOM_MODEL:-}"
        fi
        export API_TIMEOUT_MS=3000000
        export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
        mode_label="Xcode API"
        ;;
    esac
  fi

  export AGENT_HOME="$REAL_HOME"
  export AGENTS_ROOT="$REAL_HOME/.agents"
  export HOME="$runtime_dir/home"
  export XDG_CONFIG_HOME="$runtime_dir/xdg/config"
  export XDG_DATA_HOME="$runtime_dir/xdg/data"
  export XDG_CACHE_HOME="$runtime_dir/xdg/cache"
  export XDG_STATE_HOME="$runtime_dir/xdg/state"

  seed_user_tool_dirs "$HOME" "${CLAWSEAT_PROJECT:-}"
  # Skip Claude onboarding in the isolated HOME. Without this the seat
  # blocks on the welcome + auth pages even when API keys are live in env.
  prepare_claude_home "$HOME" "$session_name" "$workdir"

  cd "$workdir"
  echo "────────────────────────────────────────"
  echo " Claude Code · $mode_label"
  echo " Session:    $session_name"
  echo " Directory:  $workdir"
  echo " Model:      ${ANTHROPIC_MODEL:-<unset>}"
  echo " Endpoint:   ${ANTHROPIC_BASE_URL:-default}"
  echo " HOME:       $HOME"
  echo " AGENT_HOME: $AGENT_HOME"
  # Crash-recovery fallback — see oauth branch comment above.
  if [[ -z "$resume_label" && "${CLAWSEAT_NO_AUTO_RESUME:-0}" != "1" ]] \
      && _has_claude_cwd_history "$HOME" "$workdir"; then
    resume_args=(--continue)
    resume_label="--continue (latest in cwd)"
  fi
  echo "────────────────────────────────────────"
  [[ -n "$resume_label" ]] && launcher_resume_banner "$resume_label" >&2
  unset CLAUDECODE
  # See oauth branch above for CARTOONER_CLAUDE_CODE_EXECUTABLE rationale.
  exec "${CARTOONER_CLAUDE_CODE_EXECUTABLE:-claude}" --dangerously-skip-permissions ${resume_args[@]+"${resume_args[@]}"}
}
