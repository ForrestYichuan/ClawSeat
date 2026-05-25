from __future__ import annotations

from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_DOC = _REPO / "core" / "skills" / "planner" / "references" / "workflow-doc-schema.md"


def _text() -> str:
    return _DOC.read_text(encoding="utf-8")


def test_workflow_schema_has_required_fields() -> None:
    text = _text()

    for field in (
        "project",
        "created",
        "author",
        "seats_available",
        "seat_fallback",
        "acceptance_criteria",
        "name",
        "owner_role",
        "status",
        "prereq",
        "mode",
        "subagent_count",
        "per_subagent_inner_parallel",
        "context_per_subagent",
        "product_acceptance_required",
        "skill_commands",
        "artifacts",
        "notify_on_done",
        "notify_on_issues",
        "notify_on_blocked",
        "max_iterations",
        "escalate_on_max",
        "clear_after_step",
    ):
        assert field in text


def test_workflow_schema_nested_mode() -> None:
    text = _text()

    assert "`single` means one owner agent executes the step sequentially." in text
    assert "`parallel_subagents` means the main owner uses the Agent tool" in text
    assert "`nested` means outer parallelism plus inner per-subagent parallelism." in text
    assert "## Step 4: generate-assets" in text
    assert "subagent_count: based_on_extraction" in text
    assert "per_subagent_inner_parallel: 4" in text
    assert "Fan-Out / Fan-In Flow" in text


def test_workflow_clear_after_step_planner_forced_false() -> None:
    text = _text()

    assert "Specialist default is `true`; planner is forced" in text
    assert "Planner `clear_after_step` is forced false" in text
