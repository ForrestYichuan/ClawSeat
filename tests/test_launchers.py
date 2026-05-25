"""Tests for core/launchers/ — the merged desktop launcher.

Covers portability (no hardcoded user paths), thin-wrapper correctness,
dry-run output shape, and env-var overrides for fuzzy roots / favorites.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_LAUNCHERS = _REPO / "core" / "launchers"
_DETERMINISTIC_LAUNCHER_SOURCES = (
    "agent-launcher.sh",
    "agent-launcher-common.sh",
    "helpers/auth.sh",
    "helpers/env.sh",
    "helpers/sandbox.sh",
    "runtimes/claude.sh",
    "runtimes/codex.sh",
    "runtimes/gemini.sh",
)
_FORBIDDEN_INTERACTIVE_PATTERNS = (
    "osascript",
    "AppleScript",
    "display dialog",
    "choose from list",
    "choose folder",
    "curses",
    "launcher_choose_",
    "launcher_prompt_",
    "--prompt-auth",
)


# ─────────────────────────────────────────────────────────────────────
# Portability — no hard-coded /Users/ywf paths outside legacy fallbacks
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "agent-launcher.sh",
    "agent-launcher-common.sh",
    "agent-launcher-discover.py",
    "claude.sh",
    "codex.sh",
    "gemini.sh",
    "helpers/auth.sh",
    "helpers/env.sh",
    "helpers/sandbox.sh",
    "runtimes/claude.sh",
    "runtimes/codex.sh",
    "runtimes/gemini.sh",
])
def test_no_hardcoded_user_paths(name):
    """Source must not reference /Users/ywf anywhere — clawseat is multi-user."""
    path = _LAUNCHERS / name
    text = path.read_text()
    assert "/Users/ywf" not in text, (
        f"{name}: hard-coded /Users/ywf path found — not portable"
    )


def test_desktop_references_are_legacy_or_workspace_bookmark_only():
    """Every Desktop/ appearance must fall into one of 3 legitimate buckets:

      1. Header/comment: doc notes about where we migrated from.
      2. Legacy-fallback branch: reads $HOME/Desktop/.agent-launcher-*.json
         so users migrating from the desktop era don't lose state.
      3. Workspace bookmark: user-facing "Desktop work" menu entry that
         points at $HOME/Desktop/work as a chooser default.

    Regression guard: no UNCONDITIONAL primary write to ~/Desktop/.
    """
    for name in ("agent-launcher.sh", "agent-launcher-common.sh"):
        path = _LAUNCHERS / name
        for i, line in enumerate(path.read_text().splitlines(), start=1):
            if "Desktop/" not in line:
                continue
            stripped = line.lstrip()
            is_comment = stripped.startswith("#")
            is_legacy_probe = "legacy" in line.lower() or (
                # `-f $VAR/Desktop/.something` is a test, part of a fallback chain
                re.search(r"-f\s+['\"]?\$\w+/Desktop/\.", line) is not None
            )
            is_legacy_assign = (
                # Assignments to preset/state store ONLY after an `elif` test
                ("CUSTOM_PRESET_STORE" in line or "launcher-state" in line)
                and "Desktop/.agent-launcher-" in line
            )
            is_workspace_bookmark = "Desktop work" in line or "Desktop/work" in line
            legal = is_comment or is_legacy_probe or is_legacy_assign or is_workspace_bookmark
            assert legal, (
                f"{name}:{i} has a non-legacy Desktop/ reference: {line!r}"
            )


# ─────────────────────────────────────────────────────────────────────
# Thin wrappers — each must delegate to agent-launcher.sh with correct --tool
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("wrapper,tool", [
    ("claude.sh", "claude"),
    ("codex.sh", "codex"),
    ("gemini.sh", "gemini"),
])
def test_wrapper_delegates_to_main(wrapper, tool):
    path = _LAUNCHERS / wrapper
    text = path.read_text()
    assert "agent-launcher.sh" in text, f"{wrapper}: must delegate to agent-launcher.sh"
    assert f'--tool {tool}' in text, f"{wrapper}: must pass --tool {tool}"
    assert 'exec' in text, f"{wrapper}: must use exec for proper process replacement"


# ─────────────────────────────────────────────────────────────────────
# Dry-run shape — resolved config is printed, no side effects
# ─────────────────────────────────────────────────────────────────────

def _run(
    args: list[str],
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=cwd,
        timeout=10,
    )


@pytest.mark.parametrize("tool,auth", [
    ("claude", "oauth_token"),
    ("codex", "chatgpt"),
    ("gemini", "oauth"),
])
def test_dry_run_prints_expected_fields(tool, auth):
    expected_session = f"test-session-{tool}"
    result = _run([
        str(_LAUNCHERS / "agent-launcher.sh"),
        "--tool", tool,
        "--auth", auth,
        "--session", "test-session",
        "--dir", str(Path.home()),
        "--dry-run",
    ])
    assert result.returncode == 0, f"dry-run failed: {result.stderr}"
    out = result.stdout
    assert f"tool:     {tool}" in out
    assert f"auth:     {auth}" in out
    assert f"session:  {expected_session}" in out
    assert "dir:" in out


@pytest.mark.parametrize("tool,auth", [
    ("claude", "oauth"),
    ("claude", "oauth_token"),
    ("claude", "anthropic-console"),
    ("claude", "minimax"),
    ("claude", "deepseek"),
    ("claude", "xcode"),
    ("claude", "custom"),
    ("codex", "chatgpt"),
    ("codex", "xcode"),
    ("codex", "custom"),
    ("gemini", "oauth"),
    ("gemini", "primary"),
    ("gemini", "custom"),
])
def test_launcher_split_auth_modes_still_validate(tool, auth, tmp_path: Path):
    """Every supported auth mode must still reach dry-run after the split."""
    env = {"LAUNCHER_CUSTOM_API_KEY": "test-key"} if auth == "custom" else None

    result = _run([
        str(_LAUNCHERS / "agent-launcher.sh"),
        "--tool", tool,
        "--auth", auth,
        "--session", "auth-smoke",
        "--dir", str(tmp_path),
        "--dry-run",
    ], env=env)

    assert result.returncode == 0, result.stderr
    assert f"tool:     {tool}" in result.stdout
    assert f"auth:     {auth}" in result.stdout


def test_dry_run_does_not_double_suffix_explicit_session():
    result = _run([
        str(_LAUNCHERS / "agent-launcher.sh"),
        "--tool", "claude",
        "--auth", "oauth_token",
        "--session", "project-memory-claude",
        "--dir", str(Path.home()),
        "--dry-run",
    ])

    assert result.returncode == 0, result.stderr
    assert "session:  project-memory-claude" in result.stdout
    assert "project-memory-claude-claude" not in result.stdout


def test_load_custom_env_preserves_persistent_secret_file(tmp_path: Path):
    secret = tmp_path / "planner.env"
    secret.write_text("export LAUNCHER_CUSTOM_API_KEY='fixture-key'\n", encoding="utf-8")

    result = _run([
        "bash",
        "-lc",
        (
            "CLAWSEAT_AGENT_LAUNCHER_LIBRARY_ONLY=1 "
            f"source {str(_LAUNCHERS / 'agent-launcher.sh')!r}; "
            f"load_custom_env {str(secret)!r}; "
            f"test -f {str(secret)!r}"
        ),
    ])

    assert result.returncode == 0, result.stderr
    assert secret.exists()


def test_load_custom_env_removes_launcher_temp_file(tmp_path: Path):
    result = _run([
        "bash",
        "-lc",
        (
            "CLAWSEAT_AGENT_LAUNCHER_LIBRARY_ONLY=1 "
            f"source {str(_LAUNCHERS / 'agent-launcher.sh')!r}; "
            "tmp=$(write_custom_env_file fixture-key '' ''); "
            "load_custom_env \"$tmp\"; "
            "test ! -e \"$tmp\""
        ),
    ])

    assert result.returncode == 0, result.stderr


def test_dry_run_via_wrapper():
    """The thin wrappers should produce identical dry-run output."""
    direct = _run([
        str(_LAUNCHERS / "agent-launcher.sh"),
        "--tool", "claude", "--auth", "oauth_token",
        "--session", "dup", "--dir", str(Path.home()), "--dry-run",
    ]).stdout
    via_wrapper = _run([
        str(_LAUNCHERS / "claude.sh"),
        "--auth", "oauth_token",
        "--session", "dup", "--dir", str(Path.home()), "--dry-run",
    ]).stdout
    assert direct == via_wrapper, (
        f"wrapper output diverges from direct:\ndirect:\n{direct}\nwrapper:\n{via_wrapper}"
    )


def test_help_exits_zero():
    result = _run([str(_LAUNCHERS / "agent-launcher.sh"), "--help"])
    assert result.returncode == 0
    assert "--tool" in result.stdout
    assert "--headless" in result.stdout
    assert "--prompt-auth" not in result.stdout


@pytest.mark.parametrize("name", _DETERMINISTIC_LAUNCHER_SOURCES)
def test_deterministic_launcher_sources_have_no_interactive_primitives(name):
    text = (_LAUNCHERS / name).read_text(encoding="utf-8")
    for pattern in _FORBIDDEN_INTERACTIVE_PATTERNS:
        assert pattern not in text, f"{name}: found retired interactive primitive {pattern!r}"


def test_missing_auth_exits_two_with_explicit_error():
    result = _run([
        str(_LAUNCHERS / "agent-launcher.sh"),
        "--tool", "claude",
        "--dry-run",
    ])
    assert result.returncode == 2
    assert "error: --auth is required" in result.stderr


def test_dry_run_defaults_dir_and_session_from_cwd(tmp_path: Path):
    workspace = tmp_path / "Agent Launcher Workspace"
    workspace.mkdir()

    result = _run([
        str(_LAUNCHERS / "agent-launcher.sh"),
        "--tool", "claude",
        "--auth", "oauth_token",
        "--dry-run",
    ], cwd=str(workspace))

    assert result.returncode == 0, result.stderr
    assert f"dir:      {workspace.resolve()}" in result.stdout
    assert "session:  claude-oauth_token-agent-launcher-workspace" in result.stdout


# ─────────────────────────────────────────────────────────────────────
# Launcher directory self-relative resolution
# ─────────────────────────────────────────────────────────────────────

def test_launcher_dir_is_self_relative():
    """agent-launcher.sh must compute LAUNCHER_DIR from BASH_SOURCE, not HOME."""
    text = (_LAUNCHERS / "agent-launcher.sh").read_text()
    # The canonical incantation for self-relative dir in bash:
    assert 'LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in text, (
        "agent-launcher.sh must use BASH_SOURCE-based LAUNCHER_DIR"
    )


def test_helper_paths_are_self_relative():
    """HELPER and DISCOVER_HELPER must live next to the main launcher."""
    text = (_LAUNCHERS / "agent-launcher.sh").read_text()
    assert 'HELPER="$LAUNCHER_DIR/agent-launcher-common.sh"' in text
    assert 'DISCOVER_HELPER="$LAUNCHER_DIR/agent-launcher-discover.py"' in text


def test_launcher_bash_source_resolution_from_arbitrary_cwd(tmp_path: Path):
    result = _run([
        "bash",
        "-lc",
        (
            f"cd {tmp_path} && "
            "CLAWSEAT_AGENT_LAUNCHER_LIBRARY_ONLY=1 "
            f"source {str(_LAUNCHERS / 'agent-launcher.sh')!r} && "
            'printf "%s\\n" "$LAUNCHER_DIR" && '
            "declare -F run_claude_runtime >/dev/null && "
            "declare -F seed_user_tool_dirs >/dev/null"
        ),
    ])

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(_LAUNCHERS)


def test_launcher_helper_bash_source_resolution_from_arbitrary_cwd(tmp_path: Path):
    result = _run([
        "bash",
        "-lc",
        (
            f"cd {tmp_path} && "
            f"source {str(_LAUNCHERS / 'helpers' / 'env.sh')!r} && "
            'printf "%s\\n" "$LAUNCHER_DIR" && '
            "declare -F launcher_config_value >/dev/null"
        ),
    ])

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(_LAUNCHERS)


# ─────────────────────────────────────────────────────────────────────
# Headless mode contract — no iTerm/Terminal launch attempt
# ─────────────────────────────────────────────────────────────────────

def test_headless_flag_exists():
    """`--headless` must be documented so install_entrypoint.py can trust it."""
    help_out = _run([str(_LAUNCHERS / "agent-launcher.sh"), "--help"]).stdout
    assert "--headless" in help_out


def test_dry_run_shows_headless_state():
    """Dry-run output must include the headless flag value — the install flow
    parses this field to confirm tmux-only mode before opening its own window."""
    result = _run([
        str(_LAUNCHERS / "agent-launcher.sh"),
        "--tool", "claude", "--auth", "oauth_token",
        "--session", "h", "--dir", str(Path.home()),
        "--headless", "--dry-run",
    ])
    assert result.returncode == 0
    assert "headless: 1" in result.stdout


# ─────────────────────────────────────────────────────────────────────
# README present and accurate
# ─────────────────────────────────────────────────────────────────────

def test_readme_present():
    assert (_LAUNCHERS / "README.md").is_file()


def test_readme_documents_env_vars():
    text = (_LAUNCHERS / "README.md").read_text()
    for env_var in (
        "CLAWSEAT_LAUNCHER_ROOTS",
        "CLAWSEAT_LAUNCHER_FAVORITES",
        "AGENT_LAUNCHER_CUSTOM_PRESET_STORE",
    ):
        assert env_var in text, f"README missing docs for {env_var}"
