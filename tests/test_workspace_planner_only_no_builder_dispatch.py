from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import agent_admin_workspace as workspace  # noqa: E402


def _write_planner_only_profile(home: Path, *, use_self_contained_flag: bool = False) -> Path:
    """Write a profile for a planner-only subteam (no builders)."""
    profile = home / ".agents" / "profiles" / "planner-only-profile-dynamic.toml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    if use_self_contained_flag:
        team_line = (
            'solo-team = { seats = ["solo-planner"], team_type = "subteam", '
            'planner_mode = "delivery", notify_policy = "queue_drained_only", '
            'planner_self_contained = true }'
        )
    else:
        team_line = (
            'solo-team = { seats = ["solo-planner"], team_type = "subteam", '
            'planner_mode = "delivery", notify_policy = "queue_drained_only", '
            'scaling_policy = { max_builders = 0, overflow_action = "propose_new_subteam", reviewer_fallback = "planner" } }'
        )
    profile.write_text(
        f"""
project_name = "planner-only"
repo_root = "/repo/planner-only"
seats = [
  "memory",
  "solo-planner",
]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
{team_line}

[seat_roles]
memory = "project-memory"
solo-planner = "planner"

[seat_overrides.memory]
tool = "codex"
auth_mode = "oauth"
provider = "openai"

[seat_overrides.solo-planner]
tool = "claude"
auth_mode = "oauth_token"
provider = "anthropic"
purpose = "Self-contained engineering planner for solo-team"
capabilities = ["root-cause research", "solution design", "implementation", "tests", "self-review"]
""".lstrip(),
        encoding="utf-8",
    )
    return profile


def _write_builder_mode_profile(home: Path) -> Path:
    """Write a profile for a builder-mode team (has builders)."""
    profile = home / ".agents" / "profiles" / "builder-mode-profile-dynamic.toml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        """
project_name = "builder-mode"
repo_root = "/repo/builder-mode"
seats = [
  "memory",
  "team-planner",
  "team-builder",
]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
dev-team = { seats = ["team-planner", "team-builder"], team_type = "subteam", planner_mode = "delivery", notify_policy = "queue_drained_only", scaling_policy = { max_builders = 1, reviewer_fallback = "planner" } }

[seat_roles]
memory = "project-memory"
team-planner = "planner"
team-builder = "builder"

[seat_overrides.memory]
tool = "codex"
auth_mode = "oauth"
provider = "openai"

[seat_overrides.team-planner]
tool = "claude"
auth_mode = "oauth_token"
provider = "anthropic"

[seat_overrides.team-builder]
tool = "codex"
auth_mode = "oauth"
provider = "openai"
""".lstrip(),
        encoding="utf-8",
    )
    return profile


class TestPlannerOnlyMaxBuilders0:
    """max_builders=0 in scaling_policy triggers planner-only mode."""

    def test_no_builder_dispatch_prose(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        _write_planner_only_profile(home)
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
        monkeypatch.setenv("HOME", str(home))

        session = SimpleNamespace(engineer_id="solo-planner")
        project = SimpleNamespace(name="planner-only", engineers=[], repo_root="/repo/planner-only")
        engineer = SimpleNamespace(role="planner")

        text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

        assert "dispatch implementation to the exact owning builder seat" not in text
        assert "builder must not weaken planner acceptance tests" not in text

    def test_no_bounce_to_memory_prose(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        _write_planner_only_profile(home)
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
        monkeypatch.setenv("HOME", str(home))

        session = SimpleNamespace(engineer_id="solo-planner")
        project = SimpleNamespace(name="planner-only", engineers=[], repo_root="/repo/planner-only")
        engineer = SimpleNamespace(role="planner")

        text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

        assert "No builder is declared for this team; bounce implementation work to memory" not in text

    def test_no_builder_assignment_rules_section(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        _write_planner_only_profile(home)
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
        monkeypatch.setenv("HOME", str(home))

        session = SimpleNamespace(engineer_id="solo-planner")
        project = SimpleNamespace(name="planner-only", engineers=[], repo_root="/repo/planner-only")
        engineer = SimpleNamespace(role="planner")

        text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

        assert "## Builder Assignment Rules" not in text
        assert "## Dev Planner Dispatch Rules" not in text

    def test_no_false_reviewer_fallback_prose(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        _write_planner_only_profile(home)
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
        monkeypatch.setenv("HOME", str(home))

        session = SimpleNamespace(engineer_id="solo-planner")
        project = SimpleNamespace(name="planner-only", engineers=[], repo_root="/repo/planner-only")
        engineer = SimpleNamespace(role="planner")

        text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

        assert "planner reviews only because this team has one builder" not in text

    def test_planner_only_mode_section_present(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        _write_planner_only_profile(home)
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
        monkeypatch.setenv("HOME", str(home))

        session = SimpleNamespace(engineer_id="solo-planner")
        project = SimpleNamespace(name="planner-only", engineers=[], repo_root="/repo/planner-only")
        engineer = SimpleNamespace(role="planner")

        text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

        assert "## Planner-Only Mode" in text
        assert "Self-contained" in text
        assert "Do not dispatch builder work" in text
        assert "Escalate to memory only for roster changes" in text

    def test_queue_drained_relay_rule_still_present(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        _write_planner_only_profile(home)
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
        monkeypatch.setenv("HOME", str(home))

        session = SimpleNamespace(engineer_id="solo-planner")
        project = SimpleNamespace(name="planner-only", engineers=[], repo_root="/repo/planner-only")
        engineer = SimpleNamespace(role="planner")

        text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

        assert "Notify memory only when this team queue is drained" in text


class TestPlannerOnlySelfContainedFlag:
    """planner_self_contained=true also triggers planner-only mode."""

    def test_self_contained_flag_suppresses_builder_dispatch(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        _write_planner_only_profile(home, use_self_contained_flag=True)
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
        monkeypatch.setenv("HOME", str(home))

        session = SimpleNamespace(engineer_id="solo-planner")
        project = SimpleNamespace(name="planner-only", engineers=[], repo_root="/repo/planner-only")
        engineer = SimpleNamespace(role="planner")

        text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

        assert "dispatch implementation to the exact owning builder seat" not in text
        assert "No builder is declared for this team; bounce implementation work to memory" not in text
        assert "## Planner-Only Mode" in text


class TestBuilderModeUnchanged:
    """Builder-mode teams still get builder dispatch and assignment rules."""

    def test_builder_mode_still_gets_dev_planner_dispatch_rules(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        _write_builder_mode_profile(home)
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
        monkeypatch.setenv("HOME", str(home))

        session = SimpleNamespace(engineer_id="team-planner")
        project = SimpleNamespace(name="builder-mode", engineers=[], repo_root="/repo/builder-mode")
        engineer = SimpleNamespace(role="planner")

        text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

        assert "## Dev Planner Dispatch Rules" in text
        assert "dispatch implementation to the exact owning builder seat" in text

    def test_builder_mode_still_gets_builder_assignment_rules(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        _write_builder_mode_profile(home)
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
        monkeypatch.setenv("HOME", str(home))

        session = SimpleNamespace(engineer_id="team-planner")
        project = SimpleNamespace(name="builder-mode", engineers=[], repo_root="/repo/builder-mode")
        engineer = SimpleNamespace(role="planner")

        text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

        assert "## Builder Assignment Rules" in text
        assert "Available builders in this team" in text
        assert "team-builder" in text

    def test_builder_mode_no_planner_only_mode_section(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        _write_builder_mode_profile(home)
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
        monkeypatch.setenv("HOME", str(home))

        session = SimpleNamespace(engineer_id="team-planner")
        project = SimpleNamespace(name="builder-mode", engineers=[], repo_root="/repo/builder-mode")
        engineer = SimpleNamespace(role="planner")

        text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

        assert "## Planner-Only Mode" not in text
        assert "Do not dispatch builder work" not in text
