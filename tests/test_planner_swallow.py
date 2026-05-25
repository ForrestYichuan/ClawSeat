from pathlib import Path


def test_planner_skill_excludes_project_specific_core_ux_gate() -> None:
    planner = Path("core/skills/planner/SKILL.md").read_text(encoding="utf-8")

    assert "### Core UX gate" not in planner
    assert "SWALLOW PASS DENIED" not in planner
    assert "core_ux_swallow_blocked" not in planner


def test_core_ux_gate_note_remains_outside_planner_hot_path() -> None:
    builder = Path("core/skills/builder/SKILL.md").read_text(encoding="utf-8")

    assert "Core UX gate note" in builder
    assert "core_ux_gate" in builder
