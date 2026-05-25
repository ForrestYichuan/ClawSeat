from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "core" / "scripts" / "seat-diagnostic.sh"

# The diagnostic script calls `python3` internally; on this host that may
# resolve to /usr/bin/python3 3.9 which lacks tomllib.  Prefer a known-good
# Python interpreter (3.11+ with tomllib stdlib) when running these tests.
_HOMEBREW_PYTHON_CANDIDATES = [
    "/opt/homebrew/opt/python@3.12/bin/python3.12",
    "/opt/homebrew/opt/python@3.11/bin/python3.11",
    "/opt/homebrew/bin/python3",
]


def _find_good_python() -> str | None:
    """Return path to Python >= 3.11 with tomllib, or None."""
    for candidate in _HOMEBREW_PYTHON_CANDIDATES:
        if shutil.which(candidate) or Path(candidate).exists():
            r = subprocess.run(
                [candidate, "-c", "import tomllib"],
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return candidate
    # Current interpreter may already have tomllib (running under 3.11+)
    if sys.version_info >= (3, 11):
        return sys.executable
    return None


def _write_executable(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_session(home: Path, *, project: str = "demo", seat: str = "builder") -> None:
    session_dir = home / ".agents" / "sessions" / project / seat
    session_dir.mkdir(parents=True)
    runtime = home / ".agents" / "runtime" / "identities" / "codex" / "api" / "codex.api.xcode.demo.builder"
    (runtime / "codex-home" / "log").mkdir(parents=True)
    (runtime / "codex-home" / "log" / "codex-tui.log").write_text("real error line\n", encoding="utf-8")
    secret = home / ".agents" / "secrets" / "codex" / "xcode-best" / f"{seat}.env"
    secret.parent.mkdir(parents=True)
    secret.write_text(
        "OPENAI_API_KEY=test-key\nOPENAI_BASE_URL=https://unit.test/v1\n",
        encoding="utf-8",
    )
    (session_dir / "session.toml").write_text(
        f"""\
version = 1
project = "{project}"
engineer_id = "{seat}"
tool = "codex"
auth_mode = "api"
provider = "xcode-best"
identity = "codex.api.xcode.demo.builder"
workspace = "{home / ".agents" / "workspaces" / project / seat}"
runtime_dir = "{runtime}"
session = "{project}-{seat}-codex"
bin_path = "/usr/bin/codex"
secret_file = "{secret}"
""",
        encoding="utf-8",
    )


def _write_project(home: Path, *, project: str = "demo", seat: str = "builder") -> None:
    project_dir = home / ".agents" / "projects" / project
    project_dir.mkdir(parents=True)
    (project_dir / "project.toml").write_text(
        f"""\
version = 1
name = "{project}"
engineers = ["memory", "{seat}"]

[seat_overrides.{seat}]
tool = "codex"
auth_mode = "api"
provider = "xcode-best"
""",
        encoding="utf-8",
    )


def _write_stubs(
    bin_dir: Path,
    *,
    curl_code: str = "200",
    session_name: str = "demo-builder-codex",
    good_python: str | None = None,
) -> tuple[Path, Path]:
    bin_dir.mkdir(parents=True, exist_ok=True)
    agentctl_log = bin_dir / "agentctl.log"
    curl_log = bin_dir / "curl.log"
    _write_executable(
        bin_dir / "agentctl",
        f"""\
#!/bin/sh
echo "$@" >> {agentctl_log}
echo {session_name}
""",
    )
    _write_executable(
        bin_dir / "tmux",
        """\
#!/bin/sh
case "$1" in
  has-session) exit 0 ;;
  list-clients) echo "client-1"; exit 0 ;;
  capture-pane) echo "pane tail"; exit 0 ;;
  *) exit 1 ;;
esac
""",
    )
    _write_executable(
        bin_dir / "curl",
        f"""\
#!/bin/sh
echo "$@" >> {curl_log}
printf '{curl_code}'
""",
    )
    # Provide a python3 wrapper using a known-good interpreter (>= 3.11) so the
    # diagnostic's inline Python block can import tomllib without requiring tomli.
    if good_python and good_python != sys.executable:
        _write_executable(
            bin_dir / "python3",
            f"#!/bin/sh\nexec {good_python} \"$@\"\n",
        )
    return agentctl_log, curl_log


def _run(home: Path, bin_dir: Path, *, project: str = "demo", seat: str = "builder") -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENT_HOME"] = str(home)
    env["HOME"] = str(home)
    env["AGENTS_ROOT"] = str(home / ".agents")
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", str(_SCRIPT), project, seat],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_diagnostic_resolves_session_name(tmp_path: Path) -> None:
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    _write_project(home)
    good_python = _find_good_python()
    agentctl_log, _curl_log = _write_stubs(bin_dir, good_python=good_python)

    result = _run(home, bin_dir)

    assert result.returncode == 0, result.stderr
    assert "session = demo-builder-codex" in result.stdout
    assert "session-name builder --project demo" in agentctl_log.read_text(encoding="utf-8")


def test_diagnostic_includes_all_four_blocks(tmp_path: Path) -> None:
    good_python = _find_good_python()
    if good_python is None:
        import pytest
        pytest.skip("No Python >= 3.11 found; diagnostic metadata block requires tomllib")
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    _write_project(home)
    _write_session(home)
    _write_stubs(bin_dir, good_python=good_python)

    result = _run(home, bin_dir)

    assert result.returncode == 0, result.stderr
    assert "=== TMUX ===" in result.stdout
    assert "=== LOG (tail 30) ===" in result.stdout
    assert "=== ENDPOINT ===" in result.stdout
    assert "=== SECRETS ===" in result.stdout
    assert "log: real error line" in result.stdout


def test_diagnostic_handles_missing_log_file_gracefully(tmp_path: Path) -> None:
    good_python = _find_good_python()
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    _write_project(home)
    _write_session(home)
    runtime_log = next(home.glob(".agents/runtime/identities/codex/api/*/codex-home/log/codex-tui.log"))
    runtime_log.unlink()
    _write_stubs(bin_dir, good_python=good_python)

    result = _run(home, bin_dir)

    assert result.returncode == 0, result.stderr
    assert "<no log file at" in result.stdout
    assert "=== ENDPOINT ===" in result.stdout


def test_diagnostic_reports_auth_token_for_minimax_family_provider(tmp_path: Path) -> None:
    """MP028 regression: providers with family=minimax (e.g. baidu-glm) must show
    required_keys = ANTHROPIC_AUTH_TOKEN, not ANTHROPIC_API_KEY.

    Root cause (MP027/MP028): seat-diagnostic.sh tried to derive the repo root
    using __file__ inside a heredoc Python block — __file__ is undefined there,
    so PROVIDER_FAMILY was always empty.  MP028 fixes this by passing $REPO_ROOT
    as argv[4] from the shell side.
    """
    import pytest
    good_python = _find_good_python()
    if good_python is None:
        pytest.skip("No Python >= 3.11 found; diagnostic metadata block requires tomllib")

    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"

    # Write a session for a baidu-glm seat (minimax family, has ANTHROPIC_AUTH_TOKEN)
    seat = "memory"
    project = "demo"
    session_dir = home / ".agents" / "sessions" / project / seat
    session_dir.mkdir(parents=True)
    secret = home / ".agents" / "secrets" / "claude" / "baidu-glm.env"
    secret.parent.mkdir(parents=True)
    secret.write_text("ANTHROPIC_AUTH_TOKEN=test-token-placeholder\n", encoding="utf-8")

    (session_dir / "session.toml").write_text(
        f"""\
version = 1
project = "{project}"
engineer_id = "{seat}"
tool = "claude"
auth_mode = "api"
provider = "baidu-glm"
identity = "claude.api.baidu-glm.{project}.{seat}"
workspace = "{home / ".agents" / "workspaces" / project / seat}"
runtime_dir = "{home / ".agents" / "runtime" / "ids" / seat}"
session = "{project}-{seat}-claude"
bin_path = "/usr/bin/claude"
secret_file = "{secret}"
""",
        encoding="utf-8",
    )

    # Minimal project.toml (no overrides needed)
    project_dir = home / ".agents" / "projects" / project
    project_dir.mkdir(parents=True)
    (project_dir / "project.toml").write_text(
        f'version = 1\nname = "{project}"\nengineers = ["{seat}"]\n',
        encoding="utf-8",
    )

    _write_stubs(
        bin_dir,
        curl_code="000",
        session_name=f"{project}-{seat}-claude",
        good_python=good_python,
    )

    result = _run(home, bin_dir, project=project, seat=seat)

    assert result.returncode == 0, f"stderr: {result.stderr}"
    # With family correctly resolved via $REPO_ROOT, required_keys must be AUTH_TOKEN
    assert "required_keys = ANTHROPIC_AUTH_TOKEN" in result.stdout, (
        f"Expected 'required_keys = ANTHROPIC_AUTH_TOKEN' in diagnostic output.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "secret_status = ok" in result.stdout, (
        f"Expected 'secret_status = ok' — ANTHROPIC_AUTH_TOKEN should satisfy requirement.\n"
        f"stdout:\n{result.stdout}"
    )
    # Confirm family was resolved (not empty), so PROVIDER_FAMILY drove the result
    assert "ANTHROPIC_API_KEY" not in result.stdout.split("required_keys =")[1].split("\n")[0], (
        "required_keys must be ANTHROPIC_AUTH_TOKEN, not ANTHROPIC_API_KEY"
    )


def test_diagnostic_curls_provider_endpoint(tmp_path: Path) -> None:
    good_python = _find_good_python()
    if good_python is None:
        import pytest
        pytest.skip("No Python >= 3.11 found; diagnostic metadata block requires tomllib")
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    _write_project(home)
    _write_session(home)
    _agentctl_log, curl_log = _write_stubs(bin_dir, curl_code="204", good_python=good_python)

    result = _run(home, bin_dir)

    assert result.returncode == 0, result.stderr
    assert "http_code = 204" in result.stdout
    curl_text = curl_log.read_text(encoding="utf-8")
    assert "https://unit.test/v1/models" in curl_text
    assert "Authorization: Bearer test-key" in curl_text


def test_diagnostic_session_alive_with_wrong_tmux_socket_inherited(tmp_path: Path) -> None:
    """MP036 regression: session_alive must be correct even when the calling process
    has TMUX set to a non-existent socket (e.g. when run from inside a planner pane).

    Root cause: seat-diagnostic.sh called bare `tmux has-session` which inherits the
    TMUX env var from the caller.  When the caller is a Claude Code planner running in
    a different tmux socket than the "default" server used by agent-launcher.sh, the
    check fails with "error connecting to <planner-socket>" even though the session
    lives on the default server.

    Fix: `unset TMUX TMUX_PANE` at the top of print_tmux_block() mirrors the
    `env -u TMUX` pattern used by send-and-verify.sh.
    """
    good_python = _find_good_python()
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    _write_project(home)
    _write_session(home)
    _write_stubs(bin_dir, good_python=good_python)

    # Inject a fake TMUX socket path that does NOT exist — simulates the planner's env.
    fake_tmux_socket = str(tmp_path / "fake-planner-tmux-socket")
    env = os.environ.copy()
    env["AGENT_HOME"] = str(home)
    env["HOME"] = str(home)
    env["AGENTS_ROOT"] = str(home / ".agents")
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["TMUX"] = fake_tmux_socket  # <-- inject wrong socket

    result = subprocess.run(
        ["bash", str(_SCRIPT), "demo", "builder"],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"diagnostic must not crash: {result.stderr}"
    # With the fix (unset TMUX TMUX_PANE), the tmux stub in bin_dir is found
    # and session_alive = yes is reported correctly despite the wrong inherited TMUX.
    # Without the fix, tmux would try to connect to fake_tmux_socket and fail.
    assert "session_alive = yes" in result.stdout, (
        f"session_alive must be 'yes' even with wrong TMUX socket inherited.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
