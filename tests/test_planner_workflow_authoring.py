from __future__ import annotations

from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SKILL = _REPO / "core" / "skills" / "planner" / "SKILL.md"


def test_planner_skill_contains_workflow_authoring_and_liveness() -> None:
    """planner SKILL has workflow authoring, catalog, liveness, dispatch, and swallow refs."""
    content = _SKILL.read_text(encoding="utf-8")

    for keyword in ["skill-catalog", "liveness", "SWALLOW", "assign_owner", "fan-out"]:
        assert keyword.lower() in content.lower(), f"Missing: {keyword}"


def test_planner_skill_compact_only_no_clear() -> None:
    """planner SKILL forbids /clear and only allows /compact."""
    lower = _SKILL.read_text(encoding="utf-8").lower()

    assert "compact" in lower
    assert "forbidden" in lower or "禁止" in lower


def test_planner_skill_pins_multi_team_builder_assignment() -> None:
    content = _SKILL.read_text(encoding="utf-8")

    assert "Multi-Builder Assignment" in content
    assert "owner_seat" in content
    assert "never dispatch a step to bare role `builder`" in content
    assert "WORKSPACE_CONTRACT.toml" in content
    assert "propose a new subteam" in content
    assert "The dispatch lock is per concrete builder seat" in content
    assert "Different builder seats in the same team may run in parallel" in content
    assert "Only one outstanding planner -> builder dispatch" not in content
    assert "TEAM_OWNERSHIP.md" in content
    assert "Do not maintain a separate long-lived builder assignment document" in content
    assert "Per-task" in content and "this task's `workflow.md`" in content
