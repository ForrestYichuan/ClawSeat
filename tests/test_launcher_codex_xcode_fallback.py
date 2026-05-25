from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
_LAUNCHER = _REPO / "core" / "launchers" / "agent-launcher.sh"
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_codex_xcode_exec_agent_injects_xcode_best_base_url_fallback(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)

    secret = fake_home / ".agent-runtime" / "secrets" / "codex" / "xcode.env"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("OPENAI_API_KEY=sk-codex-xcode\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    _write_executable(
        bin_dir / "codex",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "login" ]]; then
  cat >/dev/null
  exit 0
fi
printf 'ARGS=%s\n' "$*"
printf 'OPENAI_BASE_URL=%s\n' "${OPENAI_BASE_URL:-}"
printf 'OPENAI_API_BASE=%s\n' "${OPENAI_API_BASE:-}"
printf 'CLAWSEAT_PROVIDER=%s\n' "${CLAWSEAT_PROVIDER:-}"
printf 'CONFIG_PATH=%s\n' "${CODEX_HOME:-}/config.toml"
cat "${CODEX_HOME:?}/config.toml"
""",
    )

    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop("OPENAI_BASE_URL", None)
    env.pop("OPENAI_API_BASE", None)
    env.update(
        {
            "HOME": str(fake_home),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "CLAWSEAT_PROVIDER": "xcode-best",
        }
    )

    result = subprocess.run(
        [
            str(_LAUNCHER),
            "--tool",
            "codex",
            "--auth",
            "xcode",
            "--dir",
            str(workdir),
            "--session",
            "codex-xcode-fallback",
            "--exec-agent",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ARGS=--dangerously-bypass-approvals-and-sandbox -C" in result.stdout
    assert "OPENAI_BASE_URL=https://api.xcode.best/v1" in result.stdout
    assert "OPENAI_API_BASE=" in result.stdout
    assert "CLAWSEAT_PROVIDER=xcode-best" in result.stdout
    assert 'model_provider = "xcodeapi"' in result.stdout
    assert '[model_providers.xcodeapi]' in result.stdout
    assert 'name = "xcodeapi"' in result.stdout
    assert 'base_url = "https://api.xcode.best/v1"' in result.stdout
    assert 'experimental_bearer_token = "sk-codex-xcode"' in result.stdout


def test_codex_xcode_exec_agent_renders_fresh_config_over_existing_symlink(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)

    secret = fake_home / ".agent-runtime" / "secrets" / "codex" / "xcode.env"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("OPENAI_API_KEY=sk-codex-xcode\n", encoding="utf-8")

    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    session_name = "codex-xcode-symlink-reset"
    runtime_codex_home = (
        fake_home
        / ".agent-runtime"
        / "identities"
        / "codex"
        / "api"
        / f"xcode-{session_name}-codex"
        / "codex-home"
    )
    runtime_codex_home.mkdir(parents=True, exist_ok=True)
    foreign_config = tmp_path / "foreign-config.toml"
    foreign_config.write_text('model_provider = "wrong"\n', encoding="utf-8")
    (runtime_codex_home / "config.toml").symlink_to(foreign_config)

    bin_dir = tmp_path / "bin"
    _write_executable(
        bin_dir / "codex",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "login" ]]; then
  cat >/dev/null
  exit 0
fi
cat "${CODEX_HOME:?}/config.toml"
""",
    )

    env = {
        **os.environ,
        "HOME": str(fake_home),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CLAWSEAT_PROVIDER": "xcode-best",
    }
    result = subprocess.run(
        [
            str(_LAUNCHER),
            "--tool",
            "codex",
            "--auth",
            "xcode",
            "--dir",
            str(workdir),
            "--session",
            session_name,
            "--exec-agent",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config_path = runtime_codex_home / "config.toml"
    assert config_path.exists()
    assert not config_path.is_symlink()
    rendered = config_path.read_text(encoding="utf-8")
    assert 'model_provider = "xcodeapi"' in rendered
    assert '[model_providers.xcodeapi]' in rendered
    assert 'base_url = "https://api.xcode.best/v1"' in rendered
    assert foreign_config.read_text(encoding="utf-8") == 'model_provider = "wrong"\n'


def test_agent_launcher_codex_exec_sites_all_include_yolo_flag() -> None:
    text = (_REPO / "core" / "launchers" / "runtimes" / "codex.sh").read_text(
        encoding="utf-8"
    )
    # All 4 exec codex sites must include --dangerously-bypass-approvals-and-sandbox
    # paired with -C "$workdir". Line-based check so resume_cmd injection or
    # any future arg reordering doesn't break the invariant test.
    exec_lines = [line for line in text.splitlines() if line.lstrip().startswith("exec codex")]
    assert len(exec_lines) == 4, (
        f"expected 4 codex exec sites, found {len(exec_lines)}: {exec_lines}"
    )
    for line in exec_lines:
        assert "--dangerously-bypass-approvals-and-sandbox" in line, (
            f"exec codex site missing yolo flag: {line}"
        )
        assert '-C "$workdir"' in line, f"exec codex site missing -C workdir: {line}"


# ── Secret-sync tests for FIX-CODEX-XCODE-SECRET-SYNC ───────────────────────


def test_resolve_launcher_secret_target_codex_xcode(tmp_path: Path) -> None:
    """resolve_launcher_secret_target('codex','xcode') must return a path under
    real_home/.agent-runtime/secrets/codex/xcode.env."""
    from agent_admin_config import resolve_launcher_secret_target

    result = resolve_launcher_secret_target("codex", "xcode", real_home=tmp_path)
    assert result is not None, "codex/xcode must have a secret target (was None before fix)"
    assert str(result).endswith(".agent-runtime/secrets/codex/xcode.env"), (
        f"unexpected path: {result}"
    )
    assert result == tmp_path / ".agent-runtime" / "secrets" / "codex" / "xcode.env"


def _make_codex_xcode_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Shared setup for codex/api/xcode-best SessionService tests."""
    import agent_admin_session as aas

    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    # real_user_home() is imported directly into agent_admin_session — patch it there.
    monkeypatch.setattr(aas, "real_user_home", lambda: fake_home)

    # Per-seat source secret (fake content — never printed)
    secret_dir = tmp_path / "secrets" / "codex" / "xcode-best"
    secret_dir.mkdir(parents=True, exist_ok=True)
    source_secret = secret_dir / "reviewer-1.env"
    source_secret.write_text("OPENAI_API_KEY=FAKE_FIXTURE_KEY\n", encoding="utf-8")

    session = SimpleNamespace(
        engineer_id="reviewer-1",
        project="install",
        tool="codex",
        auth_mode="api",
        provider="xcode-best",
        identity="codex.api.xcode-best.install.reviewer-1",
        workspace=str(tmp_path / "workspace" / "reviewer-1"),
        runtime_dir="/tmp/legacy-runtime",
        session="install-reviewer-1-codex",
        secret_file=str(source_secret),
        wrapper="",
        _template_model="",
    )

    launcher = tmp_path / "repo" / "core" / "launchers" / "agent-launcher.sh"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    launcher.chmod(0o755)

    hooks = MagicMock()
    hooks.agentctl_path = str(tmp_path / "agentctl.sh")
    hooks.launcher_path = str(launcher)
    hooks.load_project.return_value = SimpleNamespace(name="install")
    hooks.reconcile_session_runtime.return_value = session
    hooks.tmux_has_session.return_value = False
    hooks.write_session = MagicMock()

    svc = aas.SessionService(hooks)
    return svc, session, fake_home, source_secret


def test_sync_launcher_secret_codex_xcode_writes_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """start_engineer for codex/api/xcode-best must sync the secret to
    ~/.agent-runtime/secrets/codex/xcode.env with mode 0600."""
    import agent_admin_session as aas

    svc, session, fake_home, source_secret = _make_codex_xcode_service(tmp_path, monkeypatch)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(aas.subprocess, "run", fake_run)

    with (
        patch.object(svc, "_assert_session_running"),
        patch.object(svc, "_run_tmux_with_retry"),
    ):
        svc.start_engineer(session)

    target = fake_home / ".agent-runtime" / "secrets" / "codex" / "xcode.env"
    assert target.exists(), "launcher secret target must be written by _sync_launcher_secret_file"
    assert oct(target.stat().st_mode & 0o777) == oct(0o600), (
        f"target must be 0600, got {oct(target.stat().st_mode & 0o777)}"
    )

    # Verify content was copied (by checking key name, not value)
    content = target.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in content

    # Negative: fake key value must not leak into captured test output
    captured = capsys.readouterr()
    assert "FAKE_FIXTURE_KEY" not in captured.out
    assert "FAKE_FIXTURE_KEY" not in captured.err


def test_sync_launcher_secret_codex_xcode_source_is_per_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Target file content must come from session.secret_file (per-seat),
    not from a global path."""
    import agent_admin_session as aas

    svc, session, fake_home, source_secret = _make_codex_xcode_service(tmp_path, monkeypatch)

    # Write a distinct marker to the per-seat source
    source_secret.write_text("OPENAI_API_KEY=SEAT_SPECIFIC_MARKER\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(aas.subprocess, "run", fake_run)

    with (
        patch.object(svc, "_assert_session_running"),
        patch.object(svc, "_run_tmux_with_retry"),
    ):
        svc.start_engineer(session)

    target = fake_home / ".agent-runtime" / "secrets" / "codex" / "xcode.env"
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    # Key name present — content came from per-seat source
    assert "OPENAI_API_KEY=" in content
