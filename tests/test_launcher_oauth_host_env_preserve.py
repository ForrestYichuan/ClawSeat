"""Regression: OAuth claude seats preserve host PROXY/TLS/Claude-Desktop env.

Background — see docs/auth-modes.md "Host environment preservation" and the
2026-05-04 install-memory 403 investigation. The launcher historically
wiped CLAUDE_CODE_* and ANTHROPIC_* before exec, which also wiped
HTTPS_PROXY (needed in region-restricted networks) and Claude Desktop's
host-managed OAuth markers. This test runs the helper directly via bash
to verify the snapshot/restore contract on:

  * always-preserved vars (PROXY/TLS/timeout)
  * host-marker-gated CLAUDE_CODE_* vars
"""
from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_HELPER = REPO_ROOT / "core" / "launchers" / "helpers" / "env.sh"
LAUNCHER = REPO_ROOT / "core" / "launchers" / "agent-launcher.sh"


_PRESERVE_VARS_ALL = (
    "HTTPS_PROXY HTTP_PROXY ALL_PROXY NO_PROXY "
    "https_proxy http_proxy all_proxy no_proxy "
    "NODE_USE_SYSTEM_CA NODE_EXTRA_CA_CERTS API_TIMEOUT_MS "
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST CLAUDE_CODE_OAUTH_TOKEN "
    "CLAUDE_CODE_SUBSCRIBER_SUBSCRIPTION_ID CLAUDE_CODE_RATE_LIMIT_TIER "
    "CLAUDE_CODE_SUBSCRIPTION_TYPE CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH "
    "ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL ANTHROPIC_MODEL"
)


def _run(env_dict: dict[str, str], commands: str) -> str:
    """Source env.sh, set env_dict, then run commands. Return stdout.

    Pre-unset the entire whitelist before applying env_dict so the test
    isn't polluted by env vars inherited from the host shell (e.g. Claude
    Desktop sets CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST=1 in every child
    process; unless we wipe it first the marker is always present).
    """
    setup = "\n".join(f"export {k}={v!r}" for k, v in env_dict.items())
    script = f"""
set -euo pipefail
unset {_PRESERVE_VARS_ALL}
source {ENV_HELPER!s}
{setup}
{commands}
"""
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    assert result.returncode == 0, f"bash failed: stderr={result.stderr}\nstdout={result.stdout}"
    return result.stdout


def test_proxy_vars_always_preserved() -> None:
    """HTTPS_PROXY etc. always preserved (no host marker required)."""
    out = _run(
        {"HTTPS_PROXY": "http://127.0.0.1:10808", "HTTP_PROXY": "http://127.0.0.1:10808"},
        # Snapshot, then wipe, then eval — verify restore.
        """
snap=$(capture_oauth_host_env)
unset HTTPS_PROXY HTTP_PROXY
eval "$snap"
echo "HTTPS_PROXY=${HTTPS_PROXY:-NONE}"
echo "HTTP_PROXY=${HTTP_PROXY:-NONE}"
""",
    )
    assert "HTTPS_PROXY=http://127.0.0.1:10808" in out
    assert "HTTP_PROXY=http://127.0.0.1:10808" in out


def test_no_proxy_var_preserved_with_special_chars() -> None:
    out = _run(
        {"NO_PROXY": "localhost,127.0.0.1,::1,.local"},
        """
snap=$(capture_oauth_host_env)
unset NO_PROXY
eval "$snap"
echo "NO_PROXY=${NO_PROXY:-NONE}"
""",
    )
    assert "NO_PROXY=localhost,127.0.0.1,::1,.local" in out


def test_lowercase_proxy_vars_preserved() -> None:
    """curl/git etc. honor lowercase variants too — preserve both."""
    out = _run(
        {"https_proxy": "http://127.0.0.1:10808", "no_proxy": "127.0.0.1"},
        """
snap=$(capture_oauth_host_env)
unset https_proxy no_proxy
eval "$snap"
echo "https_proxy=${https_proxy:-NONE}"
echo "no_proxy=${no_proxy:-NONE}"
""",
    )
    assert "https_proxy=http://127.0.0.1:10808" in out
    assert "no_proxy=127.0.0.1" in out


def test_macos_system_proxy_detected_when_proxy_env_missing(tmp_path: Path) -> None:
    """Launcher should convert macOS system proxy settings into tmux env."""
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    (fakebin / "uname").write_text("#!/usr/bin/env bash\nprintf 'Darwin\\n'\n", encoding="utf-8")
    (fakebin / "scutil").write_text(
        """#!/usr/bin/env bash
if [[ "$1" != "--proxy" ]]; then
  exit 2
fi
cat <<'EOF'
<dictionary> {
  HTTPEnable : 1
  HTTPProxy : 127.0.0.1
  HTTPPort : 10808
  HTTPSEnable : 1
  HTTPSProxy : 127.0.0.1
  HTTPSPort : 10808
  SOCKSEnable : 1
  SOCKSProxy : 127.0.0.1
  SOCKSPort : 10808
}
EOF
""",
        encoding="utf-8",
    )
    (fakebin / "uname").chmod(0o755)
    (fakebin / "scutil").chmod(0o755)
    script = f"""
set -euo pipefail
unset {_PRESERVE_VARS_ALL}
source {ENV_HELPER!s}
ensure_launcher_proxy_env
echo "HTTPS_PROXY=${{HTTPS_PROXY:-NONE}}"
echo "HTTP_PROXY=${{HTTP_PROXY:-NONE}}"
echo "ALL_PROXY=${{ALL_PROXY:-NONE}}"
echo "NO_PROXY=${{NO_PROXY:-NONE}}"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{fakebin}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "HTTPS_PROXY=http://127.0.0.1:10808" in result.stdout
    assert "HTTP_PROXY=http://127.0.0.1:10808" in result.stdout
    assert "ALL_PROXY=socks5://127.0.0.1:10808" in result.stdout
    assert "NO_PROXY=localhost,127.0.0.1,::1,.local" in result.stdout


def test_existing_proxy_env_not_overwritten_by_system_proxy(tmp_path: Path) -> None:
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    (fakebin / "uname").write_text("#!/usr/bin/env bash\nprintf 'Darwin\\n'\n", encoding="utf-8")
    (fakebin / "scutil").write_text(
        """#!/usr/bin/env bash
cat <<'EOF'
<dictionary> {
  HTTPEnable : 1
  HTTPProxy : 127.0.0.1
  HTTPPort : 10808
}
EOF
""",
        encoding="utf-8",
    )
    (fakebin / "uname").chmod(0o755)
    (fakebin / "scutil").chmod(0o755)
    script = f"""
set -euo pipefail
unset {_PRESERVE_VARS_ALL}
export HTTPS_PROXY=http://existing.proxy:9999
source {ENV_HELPER!s}
ensure_launcher_proxy_env
echo "HTTPS_PROXY=${{HTTPS_PROXY:-NONE}}"
echo "HTTP_PROXY=${{HTTP_PROXY:-NONE}}"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{fakebin}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "HTTPS_PROXY=http://existing.proxy:9999" in result.stdout
    assert "HTTP_PROXY=NONE" in result.stdout


def test_tls_vars_preserved() -> None:
    out = _run(
        {"NODE_USE_SYSTEM_CA": "1", "NODE_EXTRA_CA_CERTS": "/etc/ssl/cert.pem"},
        """
snap=$(capture_oauth_host_env)
unset NODE_USE_SYSTEM_CA NODE_EXTRA_CA_CERTS
eval "$snap"
echo "NODE_USE_SYSTEM_CA=${NODE_USE_SYSTEM_CA:-NONE}"
echo "NODE_EXTRA_CA_CERTS=${NODE_EXTRA_CA_CERTS:-NONE}"
""",
    )
    assert "NODE_USE_SYSTEM_CA=1" in out
    assert "NODE_EXTRA_CA_CERTS=/etc/ssl/cert.pem" in out


def test_api_timeout_preserved() -> None:
    out = _run(
        {"API_TIMEOUT_MS": "900000"},
        """
snap=$(capture_oauth_host_env)
unset API_TIMEOUT_MS
eval "$snap"
echo "API_TIMEOUT_MS=${API_TIMEOUT_MS:-NONE}"
""",
    )
    assert "API_TIMEOUT_MS=900000" in out


def test_host_managed_oauth_token_preserved_when_marker_set() -> None:
    """Claude Desktop wrapper marker is what unlocks CLAUDE_CODE_* preservation."""
    out = _run(
        {
            "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST": "1",
            "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-FAKE_TOKEN_FOR_TEST",
            "CLAUDE_CODE_RATE_LIMIT_TIER": "default_claude_max_20x",
            "CLAUDE_CODE_SUBSCRIPTION_TYPE": "max",
        },
        """
snap=$(capture_oauth_host_env)
unset CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST CLAUDE_CODE_OAUTH_TOKEN
unset CLAUDE_CODE_RATE_LIMIT_TIER CLAUDE_CODE_SUBSCRIPTION_TYPE
eval "$snap"
echo "MARKER=${CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:-NONE}"
echo "TOKEN=${CLAUDE_CODE_OAUTH_TOKEN:-NONE}"
echo "TIER=${CLAUDE_CODE_RATE_LIMIT_TIER:-NONE}"
echo "TYPE=${CLAUDE_CODE_SUBSCRIPTION_TYPE:-NONE}"
""",
    )
    assert "MARKER=1" in out
    assert "TOKEN=sk-ant-oat01-FAKE_TOKEN_FOR_TEST" in out
    assert "TIER=default_claude_max_20x" in out
    assert "TYPE=max" in out


def test_host_managed_oauth_token_dropped_without_marker() -> None:
    """Token in env without host marker = stale; must be dropped."""
    out = _run(
        {
            # NO CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST.
            "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-STALE_TOKEN",
        },
        """
snap=$(capture_oauth_host_env)
unset CLAUDE_CODE_OAUTH_TOKEN
eval "$snap"
echo "TOKEN=${CLAUDE_CODE_OAUTH_TOKEN:-DROPPED}"
""",
    )
    # Without the host marker, capture must NOT include the token.
    assert "TOKEN=DROPPED" in out


def test_empty_vars_not_re_exported() -> None:
    """Don't resurrect blank/unset inheritance."""
    out = _run(
        {"HTTPS_PROXY": ""},
        """
snap=$(capture_oauth_host_env)
unset HTTPS_PROXY
eval "$snap"
echo "VAL=${HTTPS_PROXY:-UNSET}"
""",
    )
    assert "VAL=UNSET" in out


def test_anthropic_vars_never_in_snapshot() -> None:
    """ANTHROPIC_* must not be resurrected — would re-pollute OAuth path."""
    out = _run(
        {
            "ANTHROPIC_API_KEY": "sk-ant-api03-LEAK",
            "ANTHROPIC_BASE_URL": "https://leak.example.com",
        },
        """
snap=$(capture_oauth_host_env)
echo "SNAP_LENGTH=${#snap}"
echo "$snap" | grep -q ANTHROPIC && echo "LEAK_DETECTED" || echo "CLEAN"
""",
    )
    assert "CLEAN" in out
    assert "LEAK_DETECTED" not in out


def test_special_chars_in_proxy_url_quoted_safely() -> None:
    """User:password URLs with @ : / etc. must round-trip via printf %q."""
    proxy = "http://user:p@ssw 0rd@127.0.0.1:8080"
    out = _run(
        {"HTTPS_PROXY": proxy},
        """
snap=$(capture_oauth_host_env)
unset HTTPS_PROXY
eval "$snap"
echo "ROUND_TRIP=$HTTPS_PROXY"
""",
    )
    assert f"ROUND_TRIP={proxy}" in out


def test_claude_oauth_runtime_unsets_nested_claudecode_marker(tmp_path: Path) -> None:
    """Launching a seat from an existing Claude/Codex shell must not look nested."""
    home = tmp_path / "home"
    workdir = tmp_path / "workspace"
    fakebin = tmp_path / "fakebin"
    marker = tmp_path / "claude-env.log"
    home.mkdir()
    workdir.mkdir()
    fakebin.mkdir()
    fake_claude = fakebin / "claude"
    fake_claude.write_text(
        f"#!/usr/bin/env bash\nprintf 'CLAUDECODE=%s\\n' \"${{CLAUDECODE:-UNSET}}\" > {marker!s}\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)

    env = {
        "PATH": f"{fakebin}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(home),
        "CLAUDECODE": "1",
        "CLAWSEAT_NO_AUTO_RESUME": "1",
    }
    result = subprocess.run(
        [
            "bash",
            str(LAUNCHER),
            "--tool",
            "claude",
            "--session",
            "nested-marker-smoke",
            "--auth",
            "oauth",
            "--dir",
            str(workdir),
            "--exec-agent",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert marker.read_text(encoding="utf-8").strip() == "CLAUDECODE=UNSET"
