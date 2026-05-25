from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from agent_admin_switch import SwitchHandlers, SwitchHooks  # noqa: E402


def test_switch_harness_creates_missing_session_for_declared_seat(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    project = SimpleNamespace(
        name="cartooner-front",
        engineers=["creative-runtime-builder"],
        monitor_engineers=["creative-runtime-builder"],
    )
    created_profiles: list[SimpleNamespace] = []
    written_sessions: list[SimpleNamespace] = []
    applied: list[tuple[str, str]] = []
    stopped: list[SimpleNamespace] = []

    def create_session_record(
        *,
        engineer_id: str,
        project: SimpleNamespace,
        tool: str,
        auth_mode: str,
        provider: str,
        monitor: bool = True,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            engineer_id=engineer_id,
            project=project.name,
            tool=tool,
            auth_mode=auth_mode,
            provider=provider,
            identity=f"{tool}.{auth_mode}.{provider}.{project.name}.{engineer_id}",
            workspace=str(tmp_path / "workspaces" / project.name / engineer_id),
            runtime_dir=str(tmp_path / "runtime" / engineer_id),
            session=f"{project.name}-{engineer_id}-{tool}",
            bin_path=tool,
            monitor=monitor,
            legacy_sessions=[],
            launch_args=[],
            secret_file="",
            wrapper="",
        )

    handlers = SwitchHandlers(
        SwitchHooks(
            error_cls=RuntimeError,
            legacy_secrets_root=tmp_path / ".agents" / "secrets",
            tool_binaries={"codex": "codex", "claude": "claude", "gemini": "gemini"},
            default_tool_args={"codex": [], "claude": [], "gemini": []},
            identity_name=lambda tool, mode, provider, engineer_id, project: (
                f"{tool}.{mode}.{provider}.{project}.{engineer_id}"
            ),
            runtime_dir_for_identity=lambda tool, mode, identity: tmp_path / "runtime" / identity,
            secret_file_for=lambda tool, provider, engineer_id: tmp_path / "secrets" / tool / provider / f"{engineer_id}.env",
            session_name_for=lambda project, engineer_id, tool: f"{project}-{engineer_id}-{tool}",
            ensure_dir=lambda path: path.mkdir(parents=True, exist_ok=True),
            ensure_secret_permissions=lambda path: None,
            write_env_file=lambda *args, **kwargs: None,
            parse_env_file=lambda path: {},
            load_project=lambda name: project,
            load_project_or_current=lambda name: project,
            load_session=lambda _project, _engineer: (_ for _ in ()).throw(FileNotFoundError("session.toml")),
            write_session=lambda session: written_sessions.append(session),
            apply_template=lambda session, project: applied.append((session.engineer_id, project.name)),
            session_stop_engineer=lambda session: stopped.append(session),
            session_record_cls=SimpleNamespace,
            normalize_name=lambda name: name,
            engineer_path=lambda engineer_id: tmp_path / "engineers" / engineer_id / "engineer.toml",
            load_engineer=lambda engineer_id: SimpleNamespace(engineer_id=engineer_id),
            write_engineer=lambda engineer: created_profiles.append(engineer),
            create_engineer_profile=lambda **kwargs: SimpleNamespace(engineer_id=kwargs["engineer_id"]),
            create_session_record=create_session_record,
        )
    )

    result = handlers.session_switch_harness(
        SimpleNamespace(
            project="cartooner-front",
            engineer="creative-runtime-builder",
            tool="codex",
            mode="oauth",
            provider="openai",
            model="",
        )
    )

    captured = capsys.readouterr()
    assert result == 0
    assert created_profiles[0].engineer_id == "creative-runtime-builder"
    assert written_sessions[0].engineer_id == "creative-runtime-builder"
    assert written_sessions[0].tool == "codex"
    assert applied == [("creative-runtime-builder", "cartooner-front")]
    assert stopped == []
    assert "created session for declared seat creative-runtime-builder" in captured.out
