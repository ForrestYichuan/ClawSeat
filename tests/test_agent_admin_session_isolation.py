from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import agent_admin_session as aas


def _make_launcher_path(tmp_path: Path) -> Path:
    launcher = tmp_path / "repo" / "core" / "launchers" / "agent-launcher.sh"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def _write_secret(tmp_path: Path, relpath: str, content: str) -> str:
    path = tmp_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def _make_session(
    tmp_path: Path,
    *,
    engineer_id: str,
    tool: str,
    auth_mode: str,
    provider: str,
    runtime_dir: str = "/tmp/legacy-runtime",
    secret_content: str = "",
    template_model: str = "",
) -> SimpleNamespace:
    session_name = f"install-{engineer_id}-{tool}"
    secret_relpath = f"secrets/{tool}/{provider}/{engineer_id}.env"
    secret_file = _write_secret(tmp_path, secret_relpath, secret_content)
    return SimpleNamespace(
        engineer_id=engineer_id,
        project="install",
        tool=tool,
        auth_mode=auth_mode,
        provider=provider,
        identity=f"{tool}.{auth_mode}.{provider}.install.{engineer_id}",
        workspace=str(tmp_path / "workspace" / engineer_id),
        runtime_dir=runtime_dir,
        session=session_name,
        secret_file=secret_file,
        wrapper="",
        _template_model=template_model,
    )


def _make_service(tmp_path: Path, session: SimpleNamespace) -> tuple[aas.SessionService, MagicMock]:
    hooks = MagicMock()
    hooks.agentctl_path = str(tmp_path / "repo" / "core" / "shell-scripts" / "agentctl.sh")
    hooks.launcher_path = str(_make_launcher_path(tmp_path))
    hooks.load_project.return_value = SimpleNamespace(name=session.project)
    hooks.reconcile_session_runtime.return_value = session
    hooks.tmux_has_session.return_value = False
    hooks.write_session = MagicMock()
    svc = aas.SessionService(hooks)
    return svc, hooks


def test_start_engineer_source_no_longer_contains_tmux_new_session():
    source = inspect.getsource(aas.SessionService.start_engineer)
    assert "new-session" not in source


@pytest.mark.parametrize(
    ("tool", "auth_mode", "provider", "expected"),
    [
        ("claude", "oauth", "anthropic", "oauth"),
        ("claude", "oauth_token", "anthropic", "oauth_token"),
        ("claude", "ccr", "ccr-local", "custom"),
        ("claude", "api", "anthropic-console", "custom"),
        ("claude", "api", "minimax", "minimax"),
        ("claude", "api", "xcode-best", "custom"),
        ("codex", "oauth", "openai", "chatgpt"),
        ("codex", "api", "xcode-best", "xcode"),
        ("gemini", "oauth", "google", "oauth"),
        ("gemini", "api", "google-api-key", "primary"),
    ],
)
def test_launcher_auth_mapping_matrix(
    tmp_path: Path,
    tool: str,
    auth_mode: str,
    provider: str,
    expected: str,
):
    session = _make_session(
        tmp_path,
        engineer_id="seat-1",
        tool=tool,
        auth_mode=auth_mode,
        provider=provider,
    )
    svc, _ = _make_service(tmp_path, session)
    assert svc._launcher_auth_for(session) == expected


@pytest.mark.parametrize(
    (
        "tool",
        "auth_mode",
        "provider",
        "engineer_id",
        "secret_content",
        "expected_auth",
        "expected_runtime",
        "expect_custom_env",
    ),
    [
        (
            "claude",
            "api",
            "minimax",
            "patrol-1",
            "ANTHROPIC_AUTH_TOKEN=minimax-token\n",
            "minimax",
            ".agent-runtime/identities/claude/api/minimax-install-patrol-1-claude",
            False,
        ),
        (
            "codex",
            "api",
            "xcode-best",
            "reviewer-1",
            "OPENAI_API_KEY=codex-xcode-token\n",
            "xcode",
            ".agent-runtime/identities/codex/api/xcode-install-reviewer-1-codex",
            False,
        ),
        (
            "gemini",
            "api",
            "google-api-key",
            "designer-1",
            "GEMINI_API_KEY=gem-key\n",
            "primary",
            ".agent-runtime/identities/gemini/api/primary-install-designer-1-gemini",
            False,
        ),
    ],
)
def test_start_engineer_invokes_launcher_and_updates_runtime_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    auth_mode: str,
    provider: str,
    engineer_id: str,
    secret_content: str,
    expected_auth: str,
    expected_runtime: str,
    expect_custom_env: bool,
):
    fake_home = tmp_path / "sandbox-home"
    real_home = tmp_path / "real-home"
    fake_home.mkdir(parents=True, exist_ok=True)
    real_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(aas.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(aas, "real_user_home", lambda: real_home)

    session = _make_session(
        tmp_path,
        engineer_id=engineer_id,
        tool=tool,
        auth_mode=auth_mode,
        provider=provider,
        secret_content=secret_content,
    )
    svc, hooks = _make_service(tmp_path, session)

    launcher_calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(cmd, **kwargs):
        launcher_calls.append((list(cmd), dict(kwargs.get("env", {}))))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(aas.subprocess, "run", fake_run)

    with (
        patch.object(svc, "_assert_session_running"),
        patch.object(svc, "_run_tmux_with_retry") as mock_tmux,
    ):
        svc.start_engineer(session)

    assert launcher_calls, "launcher was not invoked"
    cmd, env = launcher_calls[0]
    assert cmd[:2] == ["bash", hooks.launcher_path]
    assert cmd[2:10] == [
        "--headless",
        "--tool",
        tool,
        "--auth",
        expected_auth,
        "--dir",
        session.workspace,
        "--session",
    ]
    assert cmd[10] == session.session
    assert ("--custom-env-file" in cmd) is expect_custom_env
    assert env["CLAWSEAT_ROOT"] == str(Path(hooks.launcher_path).resolve().parents[2])
    assert env["CLAWSEAT_PROVIDER"] == provider
    assert session.runtime_dir == str(real_home / expected_runtime)
    hooks.write_session.assert_called_once_with(session)
    hooks.apply_template.assert_called_with(session, hooks.load_project.return_value)
    # Filter out list-sessions (stale-tool cleanup probe) — it runs before
    # display setup but has no side effects when no stale sessions exist.
    title_cmds = [call.args[0] for call in mock_tmux.call_args_list
                  if call.args[0][0] != "list-sessions"]
    assert title_cmds[:2] == [
        ["set", "-g", "set-titles", "on"],
        ["set", "-g", "set-titles-string", "#{session_name}"],
    ]
    assert any(cmd[:4] == ["set-option", "-t", f"={session.session}", "detach-on-destroy"] for cmd in title_cmds)


def test_start_engineer_passes_custom_env_file_and_launcher_removes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(aas.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(aas, "real_user_home", lambda: fake_home)

    session = _make_session(
        tmp_path,
        engineer_id="builder-1",
        tool="claude",
        auth_mode="api",
        provider="xcode-best",
        secret_content="ANTHROPIC_AUTH_TOKEN=xcode-token\n",
    )
    svc, _ = _make_service(tmp_path, session)
    captured: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        idx = cmd.index("--custom-env-file")
        env_file = Path(cmd[idx + 1])
        captured["path"] = str(env_file)
        captured["content"] = env_file.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(aas.subprocess, "run", fake_run)

    with (
        patch.object(svc, "_assert_session_running"),
        patch.object(svc, "_run_tmux_with_retry"),
    ):
        svc.start_engineer(session)

    assert "export LAUNCHER_CUSTOM_API_KEY=xcode-token" in captured["content"]
    assert "export LAUNCHER_CUSTOM_BASE_URL=https://xcode.best" in captured["content"]
    assert not Path(captured["path"]).exists()


def test_start_engineer_codex_xcode_best_uses_xcode_auth_without_custom_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(aas.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(aas, "real_user_home", lambda: fake_home)

    session = _make_session(
        tmp_path,
        engineer_id="reviewer-1",
        tool="codex",
        auth_mode="api",
        provider="xcode-best",
        secret_content="OPENAI_API_KEY=codex-xcode-token\n",
        template_model="gpt-5.4",
    )
    svc, _ = _make_service(tmp_path, session)
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(kwargs.get("env", {}))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(aas.subprocess, "run", fake_run)

    with (
        patch.object(svc, "_assert_session_running"),
        patch.object(svc, "_run_tmux_with_retry"),
    ):
        svc.start_engineer(session)

    assert "--custom-env-file" not in captured["cmd"]
    assert captured["cmd"][6] == "xcode"
    assert captured["env"]["CLAWSEAT_PROVIDER"] == "xcode-best"


def test_start_engineer_reset_kills_session_before_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(aas.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(aas, "real_user_home", lambda: fake_home)

    session = _make_session(
        tmp_path,
        engineer_id="reviewer-1",
        tool="codex",
        auth_mode="api",
        provider="xcode-best",
        secret_content="OPENAI_API_KEY=codex-xcode-token\n",
    )
    svc, hooks = _make_service(tmp_path, session)
    hooks.tmux_has_session.side_effect = [True, False, False]
    events: list[str] = []

    def fake_run(cmd, **kwargs):
        events.append("launcher")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(aas.subprocess, "run", fake_run)

    def fake_tmux(args, **kwargs):
        events.append(f"tmux:{' '.join(args)}")
        return subprocess.CompletedProcess(["tmux", *args], 0, "", "")

    with (
        patch.object(svc, "_assert_session_running"),
        patch.object(
            svc,
            "clean_stale_attach_clients",
            side_effect=lambda session_name, **kwargs: events.append(f"cleanup:{session_name}") or [],
        ),
        patch.object(svc, "_run_tmux_with_retry", side_effect=fake_tmux),
    ):
        svc.start_engineer(session, reset=True)

    assert events[0] == f"tmux:kill-session -t {session.session}"
    assert events[1] == f"cleanup:{session.session}"
    # stale-tool cleanup probe (list-sessions returns empty → no kill)
    assert events[2] == "tmux:list-sessions -F #{session_name}"
    assert events[3] == "launcher"


def test_stop_engineer_still_kills_tmux_session(tmp_path: Path):
    session = SimpleNamespace(session="install-builder-1-claude")
    svc = aas.SessionService(MagicMock())

    with patch.object(svc, "_run_tmux_with_retry") as mock_tmux:
        svc.stop_engineer(session)

    mock_tmux.assert_called_once_with(
        ["kill-session", "-t", session.session],
        reason=f"stop engineer {session.session}",
        check=False,
    )
