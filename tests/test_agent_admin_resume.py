from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import agent_admin_commands as resume_mod
from agent_admin_commands import CommandHandlers, CommandHooks  # noqa: E402
from agent_admin_parser import ParserHooks, build_parser  # noqa: E402


@pytest.fixture(autouse=True)
def _dispatch_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = tmp_path / "caller.toml"
    profile.write_text(
        "\n".join(
            [
                "version = 1",
                'id = "planner"',
                'display_name = "planner"',
                'role = "planner"',
                "dispatch_authority = true",
                "escalation_authority = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAWSEAT_ENGINEER_PROFILE", str(profile))
    monkeypatch.setenv("CLAWSEAT_ENGINEER_ID", "planner")
    monkeypatch.setenv("CLAWSEAT_SEAT", "planner")


class _FakeSessionService:
    def __init__(self) -> None:
        self.started: list[tuple[str, bool]] = []

    def start_engineer(self, session, reset: bool = False) -> None:
        self.started.append((session.engineer_id, reset))

    def start_project(self, project, ensure_monitor: bool = True, reset: bool = False):  # noqa: ARG002
        return None


def _make_session(tmp_path: Path, seat: str, tool: str, project_name: str = "install") -> SimpleNamespace:
    provider = {"claude": "anthropic", "codex": "openai", "gemini": "google"}.get(tool, "unknown")
    auth_mode = {"claude": "oauth", "codex": "chatgpt", "gemini": "oauth"}.get(tool, "oauth")
    return SimpleNamespace(
        engineer_id=seat,
        project=project_name,
        tool=tool,
        auth_mode=auth_mode,
        provider=provider,
        session=f"{project_name}-{seat}-{tool}",
        workspace=str(tmp_path / "workspace" / seat),
        runtime_dir="",
        identity=f"{tool}.{auth_mode}.{provider}.{project_name}.{seat}",
        wrapper="",
        secret_file="",
        bin_path="",
        monitor=False,
        legacy_sessions=[],
    )


def _make_handlers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tool_by_seat: dict[str, str],
    tmux_alive_by_session: dict[str, bool] | None = None,
    status_by_seat: dict[str, str] | None = None,
    project_name: str = "install",
    engineers: list[str] | None = None,
) -> tuple[CommandHandlers, _FakeSessionService, list[list[str]]]:
    service = _FakeSessionService()
    tmux_alive_by_session = tmux_alive_by_session or {}
    status_by_seat = status_by_seat or {}
    sent_commands: list[list[str]] = []

    def resolve(seat: str, project_name: str | None = None):  # noqa: ARG001
        tool = tool_by_seat.get(seat, "codex")
        return _make_session(tmp_path, seat, tool, project_name or "install")

    def load_project_or_current(requested: str | None):
        return SimpleNamespace(
            name=requested or project_name,
            engineers=list(engineers or tool_by_seat.keys()),
            monitor_engineers=[],
            seat_overrides={},
            window_mode="tabs-1up",
        )

    def load_project_sessions(requested: str):  # noqa: ARG001
        return {
            seat: _make_session(tmp_path, seat, tool, project_name)
            for seat, tool in tool_by_seat.items()
        }

    def fake_tmux_has_session(session_name: str) -> bool:
        return tmux_alive_by_session.get(session_name, False)

    hooks = CommandHooks(
        error_cls=RuntimeError,
        load_project_or_current=load_project_or_current,
        resolve_engineer_session=resolve,
        provision_session_heartbeat=lambda session: (True, f"heartbeat:{session.engineer_id}"),
        load_project_sessions=load_project_sessions,
        tmux_has_session=fake_tmux_has_session,
        load_projects=lambda: {},
        get_current_project_name=lambda _projects: None,
        session_service=service,
        open_monitor_window=lambda *_args, **_kwargs: None,
        open_dashboard_window=lambda _projects: None,
        open_project_tabs_window=lambda *_args, **_kwargs: None,
        open_engineer_window=lambda *_args, **_kwargs: None,
        load_engineers=lambda: {},
    )

    handler = CommandHandlers(hooks)

    real_home = tmp_path / "real-home"
    real_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(resume_mod, "real_user_home", lambda: real_home, raising=False)
    projects_registry_module = types.ModuleType("projects_registry")
    projects_registry_module.touch_project = lambda _project: None  # type: ignore[attr-defined]
    monkeypatch.setattr(resume_mod, "projects_registry", projects_registry_module, raising=False)

    status_script = _REPO / "core" / "shell-scripts" / "check-engineer-status.sh"

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        if cmd[:2] == ["bash", str(status_script)]:
            seat = cmd[-1]
            status = status_by_seat.get(seat, "IDLE (shell, no task)")
            return subprocess.CompletedProcess(cmd, 0, f"{seat}: {status}\n", "")
        if cmd[:2] == ["tmux", "send-keys"]:
            sent_commands.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(resume_mod.subprocess, "run", fake_run)
    handler._test_monkeypatch = monkeypatch  # type: ignore[attr-defined]
    return handler, service, sent_commands


def test_resume_parser_accepts_fresh_flags() -> None:
    noop = lambda *args, **kwargs: 0  # noqa: ARG005
    hooks = ParserHooks(**{field_name: noop for field_name in ParserHooks.__dataclass_fields__})
    parser = build_parser(hooks)

    seat_args = parser.parse_args(["seat", "resume", "builder", "--fresh"])
    liveness_args = parser.parse_args(["seat", "liveness", "--project", "install", "--seat", "builder", "--json"])
    project_args = parser.parse_args(["project", "resume", "install", "--fresh"])

    assert seat_args.seat == "builder"
    assert seat_args.fresh is True
    assert liveness_args.project == "install"
    assert liveness_args.seat == "builder"
    assert liveness_args.as_json is True
    assert project_args.project == "install"
    assert project_args.fresh is True


def test_seat_resume_sends_resume_command_for_idle_shell(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers, service, sent_commands = _make_handlers(
        tmp_path,
        monkeypatch,
        tool_by_seat={"builder": "codex"},
        tmux_alive_by_session={"install-builder-codex": True},
        status_by_seat={"builder": "IDLE (shell, no task)"},
    )
    active_file = tmp_path / "real-home" / ".agent-runtime" / "active" / "builder.session"
    active_file.parent.mkdir(parents=True, exist_ok=True)
    active_file.write_text("019d3f3f-1234-5678-90ab-cdef12345678\n", encoding="utf-8")

    rc = handlers.seat_resume(SimpleNamespace(seat="builder", project="install", fresh=False))

    out = capsys.readouterr().out
    assert rc == 0
    assert service.started == []
    assert sent_commands
    assert "codex --resume 019d3f3f-1234-5678-90ab-cdef12345678" in " ".join(sent_commands[0])
    assert "Resuming session 019d3f3f-1234-5678-90ab-cdef12345678" in out
    assert "install-builder-codex" in out


def test_seat_resume_spawns_fresh_when_tmux_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers, service, sent_commands = _make_handlers(
        tmp_path,
        monkeypatch,
        tool_by_seat={"builder": "codex"},
    )

    rc = handlers.seat_resume(SimpleNamespace(seat="builder", project="install", fresh=True))

    out = capsys.readouterr().out
    assert rc == 0
    assert service.started == [("builder", True)]
    assert sent_commands == []
    assert "install-builder-codex" in out


def test_seat_resume_rejects_live_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    handlers, service, sent_commands = _make_handlers(
        tmp_path,
        monkeypatch,
        tool_by_seat={"builder": "codex"},
        tmux_alive_by_session={"install-builder-codex": True},
        status_by_seat={"builder": "WORKING (codex tool)"},
    )

    with pytest.raises(RuntimeError, match="tmux still active"):
        handlers.seat_resume(SimpleNamespace(seat="builder", project="install", fresh=False))

    assert service.started == []
    assert sent_commands == []


def test_project_resume_continues_after_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    handlers, service, sent_commands = _make_handlers(
        tmp_path,
        monkeypatch,
        tool_by_seat={"builder": "codex", "reviewer": "gemini"},
        tmux_alive_by_session={"install-reviewer-gemini": True},
        status_by_seat={"builder": "IDLE (shell, no task)", "reviewer": "WORKING (gemini active)"},
        engineers=["builder", "reviewer"],
    )
    active_file = tmp_path / "real-home" / ".agent-runtime" / "active" / "builder.session"
    active_file.parent.mkdir(parents=True, exist_ok=True)
    active_file.write_text("019d3f3f-1234-5678-90ab-cdef12345678\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="project resume failed"):
        handlers.project_resume(SimpleNamespace(project="install", fresh=False))

    assert service.started == [("builder", False)]
    assert sent_commands == []


def test_resume_protocol_surface_mentions_key_paths() -> None:
    common = (_REPO / "core" / "launchers" / "agent-launcher-common.sh").read_text(encoding="utf-8")
    claude = (_REPO / "core" / "launchers" / "runtimes" / "claude.sh").read_text(encoding="utf-8")
    codex = (_REPO / "core" / "launchers" / "runtimes" / "codex.sh").read_text(encoding="utf-8")
    gemini = (_REPO / "core" / "launchers" / "runtimes" / "gemini.sh").read_text(encoding="utf-8")
    window = (_REPO / "scripts" / "install" / "lib" / "window.sh").read_text(encoding="utf-8")
    hook = (_REPO / "scripts" / "hooks" / "memory-stop-hook.sh").read_text(encoding="utf-8")
    doc = (_REPO / "docs" / "resume-protocol.md").read_text(encoding="utf-8")

    assert "launcher_active_session_dir" in common
    assert "launcher_write_active_session_id" in common
    assert "launcher_resume_banner" in common
    assert "--resume" in claude
    assert "--last" in codex
    assert "--resume latest" in gemini
    assert "REAL_HOME" in window
    assert "CLAWSEAT_NO_AUTO_RESUME" in window
    assert "SESSION_ID" in hook
    assert "write_active_session_marker" in hook
    assert "~/.agent-runtime/active/<seat>.session" in doc
    assert "CLAWSEAT_NO_AUTO_RESUME=1" in doc
