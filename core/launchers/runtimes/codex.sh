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

run_codex_runtime() {
  local auth_mode="$1"
  local workdir="$2"
  local session_name="$3"
  # resume_cmd is a PREFIX (subcommand + positional), not a suffix flag.
  # Newer codex CLI only accepts resume as a subcommand: `codex resume <uuid>`
  # or `codex resume --last`. The old `codex --resume <uuid>` form was
  # silently rejected as an unrecognized top-level flag, so this path was
  # effectively dead before. Fallback to --last when the hook-written
  # .session file is absent (seat killed before clean exit) is added
  # per-branch below, after CODEX_HOME is finalized.
  local -a resume_cmd=()
  local resume_label=""
  local resume_session_id=""

  if [[ "${CLAWSEAT_NO_AUTO_RESUME:-0}" != "1" ]]; then
    resume_session_id="$(launcher_read_active_session_id "${CLAWSEAT_SEAT:-}" 2>/dev/null || true)"
    if [[ -n "$resume_session_id" ]]; then
      resume_cmd=(resume "$resume_session_id")
      resume_label="$resume_session_id"
    fi
  fi

  if [[ "$auth_mode" == "chatgpt" ]]; then
    export HOME="$REAL_HOME"
    export CODEX_HOME="$REAL_HOME/.codex"
    seed_user_tool_dirs "$HOME" "${CLAWSEAT_PROJECT:-}"
    cd "$workdir"
    echo "────────────────────────────────────────"
    echo " Codex · ChatGPT login"
    echo " Session:    $session_name"
    echo " Directory:  $workdir"
    echo " CODEX_HOME: $CODEX_HOME"
    echo "────────────────────────────────────────"
    # Crash-recovery: hook didn't fire but cwd has codex history → --last.
    # `codex resume --last` filters by cwd internally (unless --all).
    if [[ -z "$resume_label" && "${CLAWSEAT_NO_AUTO_RESUME:-0}" != "1" ]] \
        && _has_codex_history "$CODEX_HOME"; then
      resume_cmd=(resume --last)
      resume_label="--last (latest in cwd)"
    fi
    [[ -n "$resume_label" ]] && launcher_resume_banner "$resume_label" >&2
    exec codex ${resume_cmd[@]+"${resume_cmd[@]}"} --dangerously-bypass-approvals-and-sandbox -C "$workdir"
  fi

  local secret_file="" runtime_dir
  if [[ "$auth_mode" != "custom" ]]; then
    secret_file="$REAL_HOME/.agent-runtime/secrets/codex/xcode.env"
    if [[ ! -f "$secret_file" ]]; then
      echo "error: missing Codex secret file: $secret_file" >&2
      exit 1
    fi
  fi

  runtime_dir="$REAL_HOME/.agent-runtime/identities/codex/api/${auth_mode}-$session_name"
  mkdir -p "$runtime_dir/home" "$runtime_dir/codex-home"

  export HOME="$runtime_dir/home"
  export CODEX_HOME="$runtime_dir/codex-home"
  seed_user_tool_dirs "$HOME" "${CLAWSEAT_PROJECT:-}"
  prepare_codex_home "$CODEX_HOME" "$HOME"

  if [[ "$auth_mode" == "custom" ]]; then
    load_custom_env "$CUSTOM_ENV_FILE"
    rm -f "$CODEX_HOME/config.toml"
    python3 - "$CODEX_HOME/config.toml" "${LAUNCHER_CUSTOM_MODEL:-gpt-5.5}" "${LAUNCHER_CUSTOM_BASE_URL:-$(launcher_tool_default_base_url codex)}" "${LAUNCHER_CUSTOM_API_KEY:-}" <<'PY'
import json
import sys

config_path, model, base_url, api_key = sys.argv[1:5]
with open(config_path, "w", encoding="utf-8") as handle:
    handle.write(f"model = {json.dumps(model)}\n")
    handle.write("[model_providers.customapi]\n")
    handle.write('name = "customapi"\n')
    handle.write(f"base_url = {json.dumps(base_url)}\n")
    handle.write('wire_api = "responses"\n')
    handle.write(f"experimental_bearer_token = {json.dumps(api_key)}\n")
PY
  else
    set -a
    source "$secret_file"
    set +a
    rm -f "$CODEX_HOME/config.toml"
    if [[ -z "${OPENAI_BASE_URL:-}" && -z "${OPENAI_API_BASE:-}" ]]; then
      case "${CLAWSEAT_PROVIDER:-}" in
        xcode-best)
          export OPENAI_BASE_URL="$(launcher_provider_default_base_url codex xcode-best)"
          ;;
      esac
    fi
    python3 - "$CODEX_HOME/config.toml" "${OPENAI_MODEL:-${LAUNCHER_CUSTOM_MODEL:-gpt-5.5}}" "${OPENAI_BASE_URL:-${OPENAI_API_BASE:-$(launcher_provider_default_base_url codex xcode-best)}}" "${OPENAI_API_KEY:-}" <<'PY'
import json
import sys

config_path, model, base_url, api_key = sys.argv[1:5]
with open(config_path, "w", encoding="utf-8") as handle:
    handle.write('model_provider = "xcodeapi"\n')
    handle.write(f"model = {json.dumps(model)}\n")
    handle.write("[model_providers.xcodeapi]\n")
    handle.write('name = "xcodeapi"\n')
    handle.write(f"base_url = {json.dumps(base_url)}\n")
    handle.write('wire_api = "responses"\n')
    handle.write(f"experimental_bearer_token = {json.dumps(api_key)}\n")
PY
    if [[ ! -f "$CODEX_HOME/auth.json" ]]; then
      printf '%s' "${OPENAI_API_KEY:-}" | HOME="$HOME" CODEX_HOME="$CODEX_HOME" codex login --with-api-key >/dev/null
    fi
  fi

  cd "$workdir"
  echo "────────────────────────────────────────"
  echo " Codex · $(uppercase_ascii "$auth_mode") API"
  echo " Session:    $session_name"
  echo " Directory:  $workdir"
  echo " HOME:       $HOME"
  echo " CODEX_HOME: $CODEX_HOME"
  echo "────────────────────────────────────────"
  # Crash-recovery fallback (see chatgpt branch above for rationale).
  if [[ -z "$resume_label" && "${CLAWSEAT_NO_AUTO_RESUME:-0}" != "1" ]] \
      && _has_codex_history "$CODEX_HOME"; then
    resume_cmd=(resume --last)
    resume_label="--last (latest in cwd)"
  fi
  [[ -n "$resume_label" ]] && launcher_resume_banner "$resume_label" >&2
  if [[ "$auth_mode" == "custom" ]]; then
    if [[ -n "${LAUNCHER_CUSTOM_MODEL:-}" ]]; then
      exec codex ${resume_cmd[@]+"${resume_cmd[@]}"} --dangerously-bypass-approvals-and-sandbox -C "$workdir" -c model_provider=customapi -m "${LAUNCHER_CUSTOM_MODEL}"
    fi
    exec codex ${resume_cmd[@]+"${resume_cmd[@]}"} --dangerously-bypass-approvals-and-sandbox -C "$workdir" -c model_provider=customapi
  fi
  exec codex ${resume_cmd[@]+"${resume_cmd[@]}"} --dangerously-bypass-approvals-and-sandbox -C "$workdir"
}
