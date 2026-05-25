from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import agent_admin_workspace as workspace  # noqa: E402


def _write_multi_profile(home: Path) -> Path:
    profile = home / ".agents" / "profiles" / "cartooner-profile-dynamic.toml"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        """
project_name = "cartooner"
repo_root = "/repo/cartooner"
seats = [
  "memory",
  "cartooner-front-planner",
  "cartooner-front-builder-ui",
  "cartooner-front-builder-state",
  "cartooner-front-reviewer",
  "quality-docs-planner",
  "quality-docs-patrol-fast",
  "quality-docs-patrol-human",
  "quality-docs-patrol-chaos",
]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
cartooner-front = { seats = ["cartooner-front-planner", "cartooner-front-builder-ui", "cartooner-front-builder-state", "cartooner-front-reviewer"], team_type = "subteam", planner_mode = "delivery", notify_policy = "queue_drained_only", ownership_paths = ["apps/web/src/components/**", "apps/web/src/store/**"], scaling_policy = { max_builders = 3, reviewer_required_when_builders_gte = 2, overflow_action = "propose_new_subteam", reviewer_fallback = "planner" } }
quality-docs = { seats = ["quality-docs-planner", "quality-docs-patrol-fast", "quality-docs-patrol-human", "quality-docs-patrol-chaos"], team_type = "quality-docs", planner_mode = "quality_campaign", notify_policy = "never_notify_memory", quality_gate_doc = "quality-docs/QUALITY.md", autonomous = true, loop = "continuous", stop_rule = "campaign_clean_streak_3" }

[seat_roles]
memory = "project-memory"
cartooner-front-planner = "planner"
cartooner-front-builder-ui = "builder"
cartooner-front-builder-state = "builder"
cartooner-front-reviewer = "reviewer"
quality-docs-planner = "planner"
quality-docs-patrol-fast = "patrol"
quality-docs-patrol-human = "patrol"
quality-docs-patrol-chaos = "patrol"

[seat_overrides.memory]
tool = "codex"
auth_mode = "oauth"
provider = "openai"

[seat_overrides.cartooner-front-planner]
tool = "claude"
auth_mode = "oauth_token"
provider = "anthropic"

[seat_overrides.cartooner-front-builder-ui]
tool = "codex"
auth_mode = "oauth"
provider = "openai"
instance = "ui"
purpose = "React surfaces and component integration"
capabilities = ["react", "tailwind", "electron-ui"]

[seat_overrides.cartooner-front-builder-state]
tool = "codex"
auth_mode = "oauth"
provider = "openai"
instance = "state"
purpose = "Zustand stores and IPC data flow"
capabilities = ["zustand", "ipc", "state"]

[seat_overrides.cartooner-front-reviewer]
tool = "codex"
auth_mode = "oauth"
provider = "openai"

[seat_overrides.quality-docs-planner]
tool = "claude"
auth_mode = "api"
provider = "minimax"

[seat_overrides.quality-docs-patrol-fast]
tool = "claude"
auth_mode = "api"
provider = "minimax"
instance = "fast"
purpose = "deterministic smoke and targeted regression checks"

[seat_overrides.quality-docs-patrol-human]
tool = "claude"
auth_mode = "api"
provider = "minimax"
instance = "human"
purpose = "human workflow simulation"

[seat_overrides.quality-docs-patrol-chaos]
tool = "claude"
auth_mode = "api"
provider = "minimax"
instance = "chaos"
purpose = "failure and edge-case exploration"
""".lstrip(),
        encoding="utf-8",
    )
    return profile


def test_multi_team_planner_workspace_shows_team_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    profile = _write_multi_profile(home)
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    session = SimpleNamespace(engineer_id="cartooner-front-planner")
    project = SimpleNamespace(name="cartooner", engineers=[], repo_root="/repo/cartooner")
    engineer = SimpleNamespace(role="planner")

    lines = workspace.render_project_seat_map_lines(session, project, engineer)
    text = "\n".join(lines)

    assert f"- Profile: `{profile}`" in text
    assert "- Your team: `cartooner-front`" in text
    assert "- Your seat: `cartooner-front-planner` (`planner`)" in text
    assert "- Planner mode: `delivery`" in text
    assert "- Notify policy: `queue_drained_only`" in text
    assert "`apps/web/src/components/**`" in text
    assert "`apps/web/src/store/**`" in text
    assert "`cartooner-front-builder-ui`" in text
    assert "capabilities: `react`, `tailwind`, `electron-ui`" in text
    assert "`cartooner-front-builder-state`" in text
    assert "## Builder Assignment Rules" in text
    assert "## Dev Planner Dispatch Rules" in text
    assert "do not notify memory per task" in text
    assert "never dispatch to bare role `builder`" in text
    assert "exact `owner_seat`" in text
    assert "Reviewer gate: `cartooner-front-reviewer`" in text


def test_multi_team_workspace_uses_project_toml_roster_and_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    _write_multi_profile(home)
    project_dir = home / ".agents" / "projects" / "cartooner"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.toml").write_text(
        """
version = 1
name = "cartooner"
repo_root = "/repo/cartooner"
engineers = ["memory", "cartooner-front-planner"]

[seat_overrides.memory]
tool = "claude"
auth_mode = "api"
provider = "minimax"

[seat_overrides.cartooner-front-planner]
tool = "codex"
auth_mode = "oauth"
provider = "openai"
role = "planner-dispatcher"
team = "cartooner-front"
purpose = "Project-local active planner"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    session = SimpleNamespace(engineer_id="cartooner-front-planner")
    project = SimpleNamespace(name="cartooner", engineers=[], repo_root="/repo/cartooner")
    engineer = SimpleNamespace(role="planner")

    text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

    assert "tool=codex, auth_mode=oauth, provider=openai" in text
    assert "Project-local active planner" in text
    assert "quality-docs" not in text
    assert "provider=anthropic" not in text


def test_multi_team_planner_protocol_uses_memory_not_legacy_koder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    _write_multi_profile(home)
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    session = SimpleNamespace(engineer_id="cartooner-front-planner", project="cartooner")
    engineer = SimpleNamespace(role="planner-dispatcher")

    boundary = "\n".join(workspace.render_seat_boundary_lines(session, engineer))
    protocol = "\n".join(
        workspace.render_communication_protocol_lines(
            engineer,
            "cartooner",
            seat_id="cartooner-front-planner",
        )
    )
    text = f"{boundary}\n{protocol}"

    assert "koder" not in text
    assert "frontstage" not in text.lower()
    assert "notify project memory only when this team queue is drained" in text
    assert "do not send per-task memory pings" in text


def test_multi_team_scope_is_rendered_even_when_role_stub_is_dynamic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    _write_multi_profile(home)
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    session = SimpleNamespace(engineer_id="cartooner-front-planner")
    project = SimpleNamespace(name="cartooner", engineers=[], repo_root="/repo/cartooner")
    engineer = SimpleNamespace(role="cartooner-front-planner")

    text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))

    assert "- Your team: `cartooner-front`" in text
    assert "## Builder Assignment Rules" in text


def test_multi_team_planner_scope_does_not_append_legacy_project_map(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    _write_multi_profile(home)
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    session = SimpleNamespace(engineer_id="cartooner-front-planner")
    project = SimpleNamespace(
        name="cartooner",
        engineers=["cartooner-front-planner"],
        repo_root="/repo/cartooner",
    )
    engineer = SimpleNamespace(role="planner-dispatcher")
    stale_engineers = {
        "cartooner-front-planner": SimpleNamespace(
            role="planner-dispatcher",
            default_tool="claude",
            default_auth_mode="api",
            default_provider="stale-provider",
        )
    }

    text = "\n".join(
        workspace.render_project_seat_map_lines(
            session,
            project,
            engineer,
            project_engineers=stale_engineers,
            engineer_order=["cartooner-front-planner"],
        )
    )

    assert "- Your team: `cartooner-front`" in text
    assert "Current project role order" not in text
    assert "stale-provider" not in text


def test_multi_team_memory_workspace_shows_project_ownership(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    profile = _write_multi_profile(home)
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    session = SimpleNamespace(
        engineer_id="memory",
        tool="codex",
        workspace=str(home / ".agents" / "workspaces" / "cartooner" / "memory"),
    )
    project = SimpleNamespace(name="cartooner", engineers=[], repo_root="/repo/cartooner")
    engineer = SimpleNamespace(role="project-memory", role_details=[], aliases=[])

    lines = workspace.render_project_seat_map_lines(session, project, engineer)
    text = "\n".join(lines)
    payload = workspace.workspace_contract_payload(session, project, engineer)

    assert f"- Profile: `{profile}`" in text
    assert "## Project Team Ownership" in text
    assert "- Project memory: `memory`" in text
    assert "TEAM_OWNERSHIP.md" in text
    assert "QUALITY.md" in text
    assert "Memory acceptance preflight" in text
    assert "`cartooner-front`" in text
    assert "`quality-docs`" in text
    assert "continuous QA/docs only" in text
    assert "never_notify_memory" in text
    assert "quality-docs-patrol-chaos" in text
    assert payload["project_seat_map"]
    assert any("quality-docs" in item for item in payload["project_seat_map"])
    assert any("quality-docs-patrol-chaos" in item for item in payload["project_seat_map"])
    assert any("TEAM_OWNERSHIP.md" in item for item in payload["read_first"])
    assert any("TEAM_OWNERSHIP.md" in item for item in payload["source_paths"])
    assert any("quality-docs/QUALITY.md" in item for item in payload["read_first"])
    assert any("quality-docs/QUALITY.md" in item for item in payload["source_paths"])


def test_team_ownership_read_first_is_multi_team_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    (home / ".agents" / "profiles").mkdir(parents=True)
    (home / ".agents" / "profiles" / "single-profile-dynamic.toml").write_text(
        """
project_name = "single"
seats = ["memory"]

[seat_roles]
memory = "project-memory"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    session = SimpleNamespace(engineer_id="memory")
    project = SimpleNamespace(name="single", engineers=[], repo_root="/repo/single")
    engineer = SimpleNamespace(role="project-memory")

    text = "\n".join(workspace.render_read_first_lines(session, project, engineer))

    assert "STATUS.md" in text
    assert "TEAM_OWNERSHIP.md" not in text


def test_workspace_contract_carries_review_latest_integration_boundary() -> None:
    session = SimpleNamespace(
        engineer_id="cartooner-front-planner",
        tool="codex",
        workspace="/tmp/agents/workspaces/cartooner/cartooner-front-planner",
    )
    project = SimpleNamespace(
        name="cartooner",
        engineers=["cartooner-front-planner"],
        repo_root="/repo/cartooner",
        template_name="",
        seat_overrides={},
    )
    engineer = SimpleNamespace(role="planner", role_details=[], aliases=[])

    payload = workspace.workspace_contract_payload(session, project, engineer)
    contract = workspace.render_workspace_contract_text(session, project, engineer)
    rules = payload["review_latest_integration"]

    assert any("project-local validation worktree" in rule for rule in rules)
    assert any("never share it across projects" in rule for rule in rules)
    assert any("Builders never merge review/latest or main" in rule for rule in rules)
    assert any("Planner delivers branch/commit evidence" in rule for rule in rules)
    assert any("user-authorized warden during patrol" in rule for rule in rules)
    assert any("integrates accepted planner deliveries" in rule for rule in rules)
    assert any("explicit user confirmation" in rule for rule in rules)
    assert any("desktop launch scripts" in rule for rule in rules)
    assert any("stale tmp worktree" in rule for rule in rules)
    assert "review_latest_integration = [" in contract
    assert "project-local validation worktree" in contract
    assert "does not merge review/latest" in contract
    assert "user-authorized warden during patrol" in contract
    assert "integrates accepted planner deliveries" in contract
    assert "shared global worktree" in contract


def test_quality_docs_planner_workspace_uses_campaign_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    _write_multi_profile(home)
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    session = SimpleNamespace(engineer_id="quality-docs-planner")
    project = SimpleNamespace(name="cartooner", engineers=[], repo_root="/repo/cartooner")
    engineer = SimpleNamespace(role="planner")

    text = "\n".join(workspace.render_project_seat_map_lines(session, project, engineer))
    read_first = "\n".join(workspace.render_read_first_lines(session, project, engineer))

    assert "- Your team: `quality-docs`" in text
    assert "- Planner mode: `quality_campaign`" in text
    assert "- Notify policy: `never_notify_memory`" in text
    assert "## Quality Campaign Rules" in text
    assert "Do not notify memory directly" in text
    assert "`quality-docs-patrol-fast`" in text
    assert "quality-docs/QUALITY.md" in read_first


def test_quality_docs_planner_protocol_never_uses_legacy_koder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    _write_multi_profile(home)
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    session = SimpleNamespace(engineer_id="quality-docs-planner", project="cartooner")
    engineer = SimpleNamespace(role="planner-dispatcher")

    boundary = "\n".join(workspace.render_seat_boundary_lines(session, engineer))
    protocol = "\n".join(
        workspace.render_communication_protocol_lines(
            engineer,
            "cartooner",
            seat_id="quality-docs-planner",
        )
    )
    text = f"{boundary}\n{protocol}"

    assert "koder" not in text
    assert "frontstage" not in text.lower()
    assert "never notify memory directly" in text
    assert "QUALITY.md" in text
