from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


@pytest.fixture(autouse=True)
def _caller_escalation_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = tmp_path / "caller.toml"
    profile.write_text(
        "\n".join(
            [
                "version = 1",
                'id = "planner"',
                'display_name = "planner"',
                'role = "planner"',
                "dispatch_authority = false",
                "escalation_authority = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAWSEAT_ENGINEER_PROFILE", str(profile))
    monkeypatch.setenv("CLAWSEAT_ENGINEER_ID", "planner")
    monkeypatch.setenv("CLAWSEAT_SEAT", "planner")
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))


def test_regenerate_workspace_command_exists() -> None:
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS / "agent_admin.py"), "engineer", "regenerate-workspace", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--all-seats" in result.stdout
    assert "--project" in result.stdout


def _handlers(tmp_path: Path):
    from agent_admin_crud import CrudHandlers

    hooks = MagicMock()
    hooks.error_cls = RuntimeError
    hooks.ensure_dir.side_effect = lambda path: Path(path).mkdir(parents=True, exist_ok=True)
    hooks.load_project.return_value = SimpleNamespace(name="install", engineers=["builder"])
    session = SimpleNamespace(
        engineer_id="builder",
        project="install",
        session="install-builder-codex",
        tool="codex",
        workspace=str(tmp_path / "workspace" / "builder"),
    )
    hooks.resolve_engineer_session.return_value = session
    hooks.render_template_text.return_value = {
        "AGENTS.md": "<!-- rendered_from_clawseat_sha=new rendered_at=now renderer_version=v1 -->\n# builder\nnew\n",
    }
    hooks.apply_template.side_effect = lambda _session, _project: (
        Path(session.workspace) / "AGENTS.md"
    ).write_text(hooks.render_template_text.return_value["AGENTS.md"], encoding="utf-8")
    return CrudHandlers(hooks), hooks, session


def test_regenerate_workspace_does_not_touch_session_toml(tmp_path: Path) -> None:
    handlers, hooks, session = _handlers(tmp_path)
    workspace = Path(session.workspace)
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("# builder\nnew\n", encoding="utf-8")
    session_toml = tmp_path / "sessions" / "install" / "builder" / "session.toml"
    session_toml.parent.mkdir(parents=True)
    session_toml.write_text("session = 'install-builder-codex'\n", encoding="utf-8")

    rc = handlers.engineer_regenerate_workspace(
        SimpleNamespace(project="install", engineer="builder", all_seats=False, yes=True)
    )

    assert rc == 0
    assert session_toml.read_text(encoding="utf-8") == "session = 'install-builder-codex'\n"
    hooks.apply_template.assert_called_once()


def test_regenerate_workspace_creates_backup_before_overwrite(tmp_path: Path) -> None:
    handlers, _hooks, session = _handlers(tmp_path)
    workspace = Path(session.workspace)
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("operator local edit\n", encoding="utf-8")

    handlers.engineer_regenerate_workspace(
        SimpleNamespace(project="install", engineer="builder", all_seats=False, yes=True)
    )

    backups = list(workspace.glob(".backup-*/AGENTS.md"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "operator local edit\n"


def test_regenerate_all_skips_uninitialized_project_seats(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    handlers, hooks, _session = _handlers(tmp_path)
    memory_session = SimpleNamespace(
        engineer_id="memory",
        project="install",
        session="install-memory-codex",
        tool="codex",
        workspace=str(tmp_path / "workspace" / "memory"),
    )
    hooks.load_project.return_value = SimpleNamespace(
        name="install",
        engineers=["memory", "cartooner-front-planner"],
    )

    def resolve_engineer_session(engineer_id: str, *, project_name: str | None = None):
        assert project_name == "install"
        if engineer_id == "memory":
            return memory_session
        raise RuntimeError(f"Unknown engineer: {engineer_id}")

    def apply_template(session_obj, _project) -> None:
        workspace = Path(session_obj.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "AGENTS.md").write_text(
            hooks.render_template_text.return_value["AGENTS.md"],
            encoding="utf-8",
        )

    hooks.resolve_engineer_session.side_effect = resolve_engineer_session
    hooks.apply_template.side_effect = apply_template

    rc = handlers.engineer_regenerate_workspace(
        SimpleNamespace(project="install", engineer=None, all_seats=True, yes=True)
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert "skipped\tcartooner-front-planner\tUnknown engineer: cartooner-front-planner" in captured.err
    assert (Path(memory_session.workspace) / "AGENTS.md").exists()
    assert hooks.apply_template.call_count == 1


def test_regenerate_all_uses_project_roster_with_dynamic_profile_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    profile_path = tmp_path / ".agents" / "profiles" / "install-profile-dynamic.toml"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        """
template_name = "clawseat-minimal"
seats = ["memory", "cartooner-product2-planner", "cartooner-product-solo-planner"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
cartooner-product2 = { seats = ["cartooner-product2-planner"] }
cartooner-product-solo = { seats = ["cartooner-product-solo-planner"] }

[seat_roles]
memory = "project-memory"
cartooner-product2-planner = "planner"
cartooner-product-solo-planner = "planner"

[seat_overrides.cartooner-product2-planner]
role = "planner-dispatcher"
team = "cartooner-product2"
dispatch_authority = true

[seat_overrides.cartooner-product-solo-planner]
role = "planner-dispatcher"
team = "cartooner-product-solo"
dispatch_authority = true
""",
        encoding="utf-8",
    )

    handlers, hooks, _session = _handlers(tmp_path)
    hooks.load_project.return_value = SimpleNamespace(
        name="install",
        engineers=["memory", "cartooner-product2-planner"],
        monitor_engineers=["memory"],
        template_name="old-template",
        seat_overrides={"memory": {"role": "project-memory"}},
    )
    sessions = {
        "memory": SimpleNamespace(
            engineer_id="memory",
            project="install",
            session="install-memory-codex",
            tool="codex",
            workspace=str(tmp_path / "workspace" / "memory"),
        ),
        "cartooner-product-solo-planner": SimpleNamespace(
            engineer_id="cartooner-product-solo-planner",
            project="install",
            session="install-cartooner-product-solo-planner-claude",
            tool="claude",
            workspace=str(tmp_path / "workspace" / "cartooner-product-solo-planner"),
        ),
        "cartooner-product2-planner": SimpleNamespace(
            engineer_id="cartooner-product2-planner",
            project="install",
            session="install-cartooner-product2-planner-claude",
            tool="claude",
            workspace=str(tmp_path / "workspace" / "cartooner-product2-planner"),
        ),
    }
    hooks.resolve_engineer_session.side_effect = lambda engineer_id, *, project_name=None: sessions[engineer_id]
    render_projects: list[SimpleNamespace] = []

    def apply_template(session_obj, project_obj) -> None:
        render_projects.append(project_obj)
        workspace = Path(session_obj.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "AGENTS.md").write_text(
            hooks.render_template_text.return_value["AGENTS.md"],
            encoding="utf-8",
        )

    hooks.apply_template.side_effect = apply_template

    rc = handlers.engineer_regenerate_workspace(
        SimpleNamespace(project="install", engineer=None, all_seats=True, yes=True)
    )

    assert rc == 0
    assert hooks.resolve_engineer_session.call_count == 2
    assert (Path(sessions["memory"].workspace) / "AGENTS.md").exists()
    assert (Path(sessions["cartooner-product2-planner"].workspace) / "AGENTS.md").exists()
    assert not (Path(sessions["cartooner-product-solo-planner"].workspace) / "AGENTS.md").exists()
    assert all(project.engineers == ["memory", "cartooner-product2-planner"] for project in render_projects)
    assert all(project.template_name == "clawseat-minimal" for project in render_projects)
    assert all(
        project.seat_overrides["cartooner-product2-planner"]["team"] == "cartooner-product2"
        for project in render_projects
    )
