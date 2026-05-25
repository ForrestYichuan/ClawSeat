import os
import subprocess
import sys
import textwrap
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "restart-seat.sh"


def _write_fake_tmux(fakebin: Path, tmux_state: Path, tmux_log: Path) -> Path:
    fakebin.mkdir()
    tmux = fakebin / "tmux"
    tmux.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            log={tmux_log}
            state={tmux_state}
            cmd="${{1:-}}"
            shift || true
            printf 'TMUX %q' "$cmd" >> "$log"
            for arg in "$@"; do printf ' %q' "$arg" >> "$log"; done
            printf ' TMUX_ENV=%q TMUX_PANE_ENV=%q\\n' "${{TMUX:-}}" "${{TMUX_PANE:-}}" >> "$log"
            session=""
            while [[ $# -gt 0 ]]; do
              if [[ "$1" == "-t" || "$1" == "-s" ]]; then
                session="${{2#=}}"
                shift 2
              else
                shift
              fi
            done
            case "$cmd" in
              has-session)
                [[ -n "$session" && -f "$state/$session" ]] && exit 0
                exit 1
                ;;
              new-session)
                mkdir -p "$state"
                [[ -n "$session" ]] && touch "$state/$session"
                exit 0
                ;;
              kill-session)
                [[ -n "$session" ]] && rm -f "$state/$session"
                exit 0
                ;;
              set-option)
                exit 0
                ;;
              *)
                exit 0
                ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    return tmux


def test_restart_seat_translates_canonical_api_auth_to_launcher_labels() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "_launcher_auth_for" in text
    assert "_write_launcher_custom_env_file" in text
    assert '--auth "$LAUNCHER_AUTH"' in text
    assert "--custom-env-file" in text
    assert "CUSTOM_ENV_FILE_DELEGATED_TO_TMUX" in text


def test_restart_seat_launches_custom_provider_through_agent_admin_plan(tmp_path: Path) -> None:
    """Exercise the live restart script path without touching real tmux/HOME."""
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    fakebin = tmp_path / "fakebin"
    tmux_state = tmp_path / "tmux-state"
    tmux_log = tmp_path / "tmux.log"
    provider = "smoke-custom-provider"
    project = "smoke-provider-restart"
    seat = "memory"
    session = "smoke-provider-restart-memory-claude"
    secret_value = "smoke-secret-value-not-for-output"
    secret_file = home / ".agents" / "secrets" / "claude" / f"{provider}.env"

    workspace.mkdir(parents=True)
    secret_file.parent.mkdir(parents=True)
    secret_file.write_text(f"ANTHROPIC_API_KEY={secret_value}\n", encoding="utf-8")
    secret_file.chmod(0o600)
    (home / ".agents" / "engineers" / seat).mkdir(parents=True)
    (home / ".agents" / "sessions" / project / seat).mkdir(parents=True)
    (home / ".agents" / "projects" / project).mkdir(parents=True)
    (home / ".agents" / "providers.toml").write_text(
        textwrap.dedent(
            f"""\
            version = 1

            [providers."{provider}"]
            tool = "claude"
            kind = "api_key"
            family = "anthropic"
            base_url = "https://example.invalid/anthropic"
            model = "claude-smoke"
            secret_file = "{secret_file}"
            created_at = "2026-05-14T00:00:00Z"
            updated_at = "2026-05-14T00:00:00Z"
            """
        ),
        encoding="utf-8",
    )
    (home / ".agents" / "engineers" / seat / "engineer.toml").write_text(
        textwrap.dedent(
            f"""\
            id = "{seat}"
            display_name = "Memory"
            aliases = []
            default_tool = "claude"
            default_auth_mode = "api"
            default_provider = "{provider}"
            """
        ),
        encoding="utf-8",
    )
    (home / ".agents" / "projects" / project / "project.toml").write_text(
        textwrap.dedent(
            f"""\
            name = "{project}"
            repo_root = "{workspace}"
            monitor_session = "{project}-monitor"
            engineers = ["{seat}"]
            monitor_engineers = []
            """
        ),
        encoding="utf-8",
    )
    (home / ".agents" / "sessions" / project / seat / "session.toml").write_text(
        textwrap.dedent(
            f"""\
            version = 1
            project = "{project}"
            engineer_id = "{seat}"
            tool = "claude"
            auth_mode = "api"
            provider = "{provider}"
            identity = "{provider}-{session}"
            workspace = "{workspace}"
            runtime_dir = ""
            session = "{session}"
            bin_path = "/usr/bin/true"
            monitor = true
            legacy_sessions = []
            launch_args = []
            """
        ),
        encoding="utf-8",
    )

    tmux = _write_fake_tmux(fakebin, tmux_state, tmux_log)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
            "TMUX_BIN": str(tmux),
            "TMUX": "/tmp/polluted-cartooner-socket,123,0",
            "TMUX_PANE": "%polluted",
            "PATH": f"{fakebin}{os.pathsep}{env.get('PATH', '')}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT), project, seat, "--no-window"],
        cwd=str(REPO),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "launcher_auth: custom" in result.stdout
    assert "result:    tmux session alive" in result.stdout
    assert secret_value not in result.stdout
    assert secret_value not in result.stderr

    log = tmux_log.read_text(encoding="utf-8")
    assert "--auth\\ custom" in log
    assert "--custom-env-file" in log
    assert secret_value not in log


def test_restart_seat_forces_legacy_claude_oauth_anthropic_to_oauth_token(tmp_path: Path) -> None:
    """Legacy Claude official OAuth sessions should restart with long-lived token auth."""
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    fakebin = tmp_path / "fakebin"
    tmux_state = tmp_path / "tmux-state"
    tmux_log = tmp_path / "tmux.log"
    project = "legacy-oauth-restart"
    seat = "memory"
    session = "legacy-oauth-restart-memory-claude"
    secret_file = home / ".agents" / "secrets" / "claude" / "anthropic.env"

    workspace.mkdir(parents=True)
    secret_file.parent.mkdir(parents=True)
    secret_file.write_text("CLAUDE_CODE_OAUTH_TOKEN=legacy-oauth-token\n", encoding="utf-8")
    secret_file.chmod(0o600)
    (home / ".agents" / "engineers" / seat).mkdir(parents=True)
    (home / ".agents" / "sessions" / project / seat).mkdir(parents=True)
    (home / ".agents" / "projects" / project).mkdir(parents=True)
    (home / ".agents" / "providers.toml").write_text(
        textwrap.dedent(
            f"""\
            version = 1

            [providers.anthropic]
            tool = "claude"
            kind = "oauth_token"
            family = "anthropic"
            secret_file = "{secret_file}"
            created_at = "2026-05-14T00:00:00Z"
            updated_at = "2026-05-14T00:00:00Z"
            """
        ),
        encoding="utf-8",
    )
    (home / ".agents" / "engineers" / seat / "engineer.toml").write_text(
        textwrap.dedent(
            f"""\
            id = "{seat}"
            display_name = "Memory"
            aliases = []
            default_tool = "claude"
            default_auth_mode = "oauth"
            default_provider = "anthropic"
            """
        ),
        encoding="utf-8",
    )
    (home / ".agents" / "projects" / project / "project.toml").write_text(
        textwrap.dedent(
            f"""\
            name = "{project}"
            repo_root = "{workspace}"
            monitor_session = "{project}-monitor"
            engineers = ["{seat}"]
            monitor_engineers = []
            """
        ),
        encoding="utf-8",
    )
    (home / ".agents" / "sessions" / project / seat / "session.toml").write_text(
        textwrap.dedent(
            f"""\
            version = 1
            project = "{project}"
            engineer_id = "{seat}"
            tool = "claude"
            auth_mode = "oauth"
            provider = "anthropic"
            identity = "claude.oauth.anthropic.{project}.{seat}"
            workspace = "{workspace}"
            runtime_dir = ""
            session = "{session}"
            bin_path = "/usr/bin/true"
            monitor = true
            legacy_sessions = []
            launch_args = []
            """
        ),
        encoding="utf-8",
    )

    tmux = _write_fake_tmux(fakebin, tmux_state, tmux_log)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
            "TMUX_BIN": str(tmux),
            "PATH": f"{fakebin}{os.pathsep}{env.get('PATH', '')}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT), project, seat, "--no-window"],
        cwd=str(REPO),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "launcher_auth: oauth_token" in result.stdout
    assert "result:    tmux session alive" in result.stdout

    log = tmux_log.read_text(encoding="utf-8")
    assert "--auth\\ oauth_token" in log
    assert "legacy-oauth-token" not in result.stdout
    assert "legacy-oauth-token" not in result.stderr
    assert "legacy-oauth-token" not in log
    assert (home / ".agents" / ".env.global").read_text(encoding="utf-8") == "CLAUDE_CODE_OAUTH_TOKEN=legacy-oauth-token\n"


def test_restart_seat_materializes_declared_project_seat(tmp_path: Path) -> None:
    """A v3 seat may be declared in project.toml before session.toml exists."""
    home = tmp_path / "home"
    workspace = tmp_path / "repo"
    fakebin = tmp_path / "fakebin"
    tmux_state = tmp_path / "tmux-state"
    tmux_log = tmp_path / "tmux.log"
    project = "declared-restart"
    seat = "creative-runtime-builder"
    session = f"{project}-{seat}-codex"

    workspace.mkdir(parents=True)
    (home / ".agents" / "projects" / project).mkdir(parents=True)
    (home / ".agents" / "projects" / project / "project.toml").write_text(
        textwrap.dedent(
            f"""\
            name = "{project}"
            repo_root = "{workspace}"
            monitor_session = "{project}-monitor"
            engineers = ["{seat}"]
            monitor_engineers = ["{seat}"]

            [seat_overrides.{seat}]
            tool = "codex"
            auth_mode = "oauth"
            provider = "openai"
            """
        ),
        encoding="utf-8",
    )

    tmux = _write_fake_tmux(fakebin, tmux_state, tmux_log)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
            "TMUX_BIN": str(tmux),
            "PATH": f"{fakebin}{os.pathsep}{env.get('PATH', '')}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT), project, seat, "--no-window"],
        cwd=str(REPO),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert f"session:       {session}" in result.stdout
    assert "result:    tmux session alive" in result.stdout
    assert "polluted-cartooner-socket" not in tmux_log.read_text(encoding="utf-8")
    assert "%polluted" not in tmux_log.read_text(encoding="utf-8")
    assert (home / ".agents" / "engineers" / seat / "engineer.toml").exists()
    assert (home / ".agents" / "sessions" / project / seat / "session.toml").exists()
