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

launcher_config_value() {
  local query="$1"
  local tool="${2:-}"
  local provider="${3:-}"
  "$LAUNCHER_PYTHON_BIN" - "$LAUNCHER_REPO_ROOT" "$query" "$tool" "$provider" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
query = sys.argv[2]
tool = sys.argv[3]
provider = sys.argv[4]
sys.path.insert(0, str(repo_root / "core" / "scripts"))

from agent_admin_config import provider_default_base_url, tool_default_base_url

value = ""
if query == "tool-default-base-url":
    value = tool_default_base_url(tool) or ""
elif query == "provider-default-base-url":
    value = provider_default_base_url(tool, provider) or ""
print(value)
PY
}

launcher_tool_default_base_url() {
  launcher_config_value "tool-default-base-url" "$1"
}

launcher_provider_default_base_url() {
  launcher_config_value "provider-default-base-url" "$1" "$2"
}

write_custom_env_file() {
  local api_key="$1"
  local base_url="$2"
  local model="$3"
  local env_file
  env_file="$(mktemp /tmp/agent-launcher-custom.XXXXXX)"
  chmod 600 "$env_file"
  {
    printf 'export LAUNCHER_CUSTOM_API_KEY=%q\n' "$api_key"
    if [[ -n "$base_url" ]]; then
      printf 'export LAUNCHER_CUSTOM_BASE_URL=%q\n' "$base_url"
    fi
    if [[ -n "$model" ]]; then
      printf 'export LAUNCHER_CUSTOM_MODEL=%q\n' "$model"
    fi
  } >"$env_file"
  printf '%s\n' "$env_file"
}

env_file_has_key() {
  local path="$1"
  local key="$2"
  python3 - "$path" "$key" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
if not path.exists():
    raise SystemExit(1)

try:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
except Exception:
    raise SystemExit(1)

for line in lines:
    s = line.strip()
    if not s or s.startswith("#"):
        continue
    if s.startswith("export "):
        s = s[len("export "):].strip()
    if not s.startswith(key + "="):
        continue
    value = s.split("=", 1)[1].strip().strip('"').strip("'")
    if value:
        raise SystemExit(0)

raise SystemExit(1)
PY
}

ensure_custom_env_file_for_auth() {
  if [[ "$AUTH_MODE" != "custom" ]]; then
    return 0
  fi
  if [[ -n "$CUSTOM_ENV_FILE" ]]; then
    if [[ ! -f "$CUSTOM_ENV_FILE" ]]; then
      echo "error: missing custom env file: $CUSTOM_ENV_FILE" >&2
      exit 2
    fi
    return 0
  fi
  if [[ -z "${LAUNCHER_CUSTOM_API_KEY:-}" ]]; then
    echo "error: --auth custom requires --custom-env-file or LAUNCHER_CUSTOM_API_KEY in env" >&2
    exit 2
  fi
  CUSTOM_ENV_FILE="$(write_custom_env_file \
    "${LAUNCHER_CUSTOM_API_KEY:-}" \
    "${LAUNCHER_CUSTOM_BASE_URL:-}" \
    "${LAUNCHER_CUSTOM_MODEL:-}")"
  GENERATED_CUSTOM_ENV_FILE="1"
}

cleanup_generated_custom_env_file() {
  if [[ "$GENERATED_CUSTOM_ENV_FILE" == "1" && -n "$CUSTOM_ENV_FILE" && -f "$CUSTOM_ENV_FILE" ]]; then
    rm -f "$CUSTOM_ENV_FILE"
  fi
}

load_custom_env() {
  local env_file="$1"
  if [[ -z "$env_file" ]]; then
    return 0
  fi
  if [[ ! -f "$env_file" ]]; then
    echo "error: missing custom env file: $env_file" >&2
    exit 1
  fi

  set -a
  source "$env_file"
  set +a
  case "$env_file" in
    /tmp/agent-launcher-custom.*|/var/folders/*/T/agent-launcher-custom.*)
      rm -f "$env_file"
      ;;
  esac
}

# ============================================================================
# OAuth seat host-env preservation
#
# ClawSeat OAuth seats (--auth oauth | oauth_token) inherit the tmux session's
# environment. Historically the launcher did `unset ANTHROPIC_* CLAUDE_CODE_*`
# to drop stale provider state, but it also wiped legitimate host-supplied
# state — most importantly:
#
#   * HTTPS_PROXY / HTTP_PROXY / ALL_PROXY / NO_PROXY
#     Required in region-restricted networks (China etc.) to reach
#     api.anthropic.com. When dropped, OAuth seats hit transient
#     "Request not allowed" 403s while the host's Claude Desktop
#     (which keeps PROXY) works fine. See GitHub issue #30318 and the
#     2026-05-03 "install-memory 403" investigation.
#   * NODE_USE_SYSTEM_CA / NODE_EXTRA_CA_CERTS
#     Needed when corporate / system CA bundles are not in Node's default.
#   * API_TIMEOUT_MS
#     Long requests (compaction, large agent tasks) need 600s+; without it
#     defaults bite first.
#   * CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST + sibling CLAUDE_CODE_* markers
#     When set by the host (Claude Desktop wrapper), the host has already
#     provided a known-good long-lived OAuth token via env. Preserve them
#     so the OAuth seat can ride the host's authenticated session instead
#     of churning through keychain refresh.
#
# This helper captures the relevant vars before we wipe provider state,
# returning a small text blob the caller can `eval` after the wipe to
# restore them. Only non-empty values are restored — never resurrects
# unset / blank inheritance.
#
# Usage:
#   local oauth_env_snapshot
#   oauth_env_snapshot="$(capture_oauth_host_env)"
#   unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ...
#   eval "$oauth_env_snapshot"
# ============================================================================
_OAUTH_PRESERVE_PROXY_VARS=(HTTPS_PROXY HTTP_PROXY ALL_PROXY NO_PROXY \
                            https_proxy http_proxy all_proxy no_proxy)
_OAUTH_PRESERVE_TLS_VARS=(NODE_USE_SYSTEM_CA NODE_EXTRA_CA_CERTS)
_OAUTH_PRESERVE_TIMEOUT_VARS=(API_TIMEOUT_MS)
# Host-managed CLAUDE_CODE_* state. CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST=1
# is the Claude Desktop wrapper's marker; when present we trust that the
# host provided a coherent OAuth env (token + tier + subscription markers)
# and preserve all of them as a unit. CLAUDE_CODE_OAUTH_TOKEN by itself
# without the marker is treated as stale and dropped (preserves the
# original re-auth-on-stale-env safety property).
_OAUTH_PRESERVE_HOST_MANAGED_VARS=(CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST \
                                   CLAUDE_CODE_OAUTH_TOKEN \
                                   CLAUDE_CODE_SUBSCRIBER_SUBSCRIPTION_ID \
                                   CLAUDE_CODE_RATE_LIMIT_TIER \
                                   CLAUDE_CODE_SUBSCRIPTION_TYPE \
                                   CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH)

launcher_proxy_env_is_set() {
  [[ -n "${HTTPS_PROXY:-}${HTTP_PROXY:-}${ALL_PROXY:-}${https_proxy:-}${http_proxy:-}${all_proxy:-}" ]]
}

launcher_detect_macos_system_proxy_exports() {
  [[ "$(uname -s 2>/dev/null || true)" == "Darwin" ]] || return 0
  command -v scutil >/dev/null 2>&1 || return 0

  local line value
  local http_enable="" http_host="" http_port=""
  local https_enable="" https_host="" https_port=""
  local socks_enable="" socks_host="" socks_port=""
  while IFS= read -r line; do
    value="${line##* : }"
    case "$line" in
      *"HTTPEnable : "*) http_enable="$value" ;;
      *"HTTPProxy : "*) http_host="$value" ;;
      *"HTTPPort : "*) http_port="$value" ;;
      *"HTTPSEnable : "*) https_enable="$value" ;;
      *"HTTPSProxy : "*) https_host="$value" ;;
      *"HTTPSPort : "*) https_port="$value" ;;
      *"SOCKSEnable : "*) socks_enable="$value" ;;
      *"SOCKSProxy : "*) socks_host="$value" ;;
      *"SOCKSPort : "*) socks_port="$value" ;;
    esac
  done < <(scutil --proxy 2>/dev/null || true)

  local out="" proxy_value=""
  if [[ "$http_enable" == "1" && -n "$http_host" && -n "$http_port" ]]; then
    proxy_value="http://${http_host}:${http_port}"
    out+="export http_proxy=$(printf '%q' "$proxy_value"); "
    out+="export HTTP_PROXY=$(printf '%q' "$proxy_value"); "
  fi
  if [[ "$https_enable" == "1" && -n "$https_host" && -n "$https_port" ]]; then
    proxy_value="http://${https_host}:${https_port}"
    out+="export https_proxy=$(printf '%q' "$proxy_value"); "
    out+="export HTTPS_PROXY=$(printf '%q' "$proxy_value"); "
  fi
  if [[ "$socks_enable" == "1" && -n "$socks_host" && -n "$socks_port" ]]; then
    proxy_value="socks5://${socks_host}:${socks_port}"
    out+="export all_proxy=$(printf '%q' "$proxy_value"); "
    out+="export ALL_PROXY=$(printf '%q' "$proxy_value"); "
  fi
  if [[ -n "$out" && -z "${NO_PROXY:-}${no_proxy:-}" ]]; then
    proxy_value="localhost,127.0.0.1,::1,.local"
    out+="export no_proxy=$(printf '%q' "$proxy_value"); "
    out+="export NO_PROXY=$(printf '%q' "$proxy_value"); "
  fi
  printf '%s\n' "$out"
}

ensure_launcher_proxy_env() {
  launcher_proxy_env_is_set && return 0
  local proxy_env_snapshot=""
  proxy_env_snapshot="$(launcher_detect_macos_system_proxy_exports)"
  [[ -n "$proxy_env_snapshot" ]] || return 0
  eval "$proxy_env_snapshot"
}

# Args:
#   $1 — include_host_managed_oauth: "1" (default) to also preserve
#        CLAUDE_CODE_OAUTH_TOKEN + sibling host-managed markers when
#        CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST=1 is set. Pass "0" from
#        non-OAuth call sites (api / custom / minimax / anthropic-console
#        seats) — for those, CLAUDE_CODE_OAUTH_TOKEN would override the
#        per-seat ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY and produce a
#        spurious 401 against the non-Anthropic endpoint.
capture_oauth_host_env() {
  local include_host_managed_oauth="${1:-1}"
  local out="" name val
  # Always preserve PROXY / TLS / TIMEOUT vars when set on the host.
  for name in "${_OAUTH_PRESERVE_PROXY_VARS[@]}" \
              "${_OAUTH_PRESERVE_TLS_VARS[@]}" \
              "${_OAUTH_PRESERVE_TIMEOUT_VARS[@]}"; do
    val="${!name:-}"
    if [[ -n "$val" ]]; then
      out+="export $name=$(printf '%q' "$val"); "
    fi
  done
  # Preserve host-managed CLAUDE_CODE_* only if the host marker is present
  # AND the caller asked for it. Without the marker, dropping these vars
  # protects against stale token inheritance from older shell sessions.
  if [[ "$include_host_managed_oauth" == "1" ]] \
     && [[ "${CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:-}" == "1" ]]; then
    for name in "${_OAUTH_PRESERVE_HOST_MANAGED_VARS[@]}"; do
      val="${!name:-}"
      if [[ -n "$val" ]]; then
        out+="export $name=$(printf '%q' "$val"); "
      fi
    done
  fi
  printf '%s\n' "$out"
}
