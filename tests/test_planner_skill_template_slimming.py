from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PLANNER_SKILL = (REPO / "core" / "skills" / "planner" / "SKILL.md").read_text(encoding="utf-8")
PLANNER_SELF_CLOSEOUT = (REPO / "core" / "references" / "planner-self-closeout-protocol.md").read_text(encoding="utf-8")


def test_planner_skill_has_single_operator_language_matching_section() -> None:
    assert PLANNER_SKILL.count("## Operator Language Matching") == 1


def test_planner_skill_excludes_project_local_hot_path_blocks() -> None:
    for token in (
        "### SUPERSEDED claims",
        "### Core UX gate",
        "core_ux_swallow_blocked",
        "SWALLOW PASS DENIED",
    ):
        assert token not in PLANNER_SKILL


def test_planner_skill_links_canonical_protocol_references() -> None:
    assert "communication-protocol.md" in PLANNER_SKILL
    assert "collaboration-rules.md" in PLANNER_SKILL


def test_planner_closeout_docs_use_exact_planner_source_placeholder() -> None:
    combined = PLANNER_SKILL + "\n" + PLANNER_SELF_CLOSEOUT

    assert "complete_handoff.py --source <exact planner seat>" in combined
    stale_generic = "complete_handoff.py --source " + "planner --target memory"
    assert stale_generic not in combined
    assert "source=<exact planner seat>" not in combined


def test_planner_skill_keeps_review_latest_integration_hot() -> None:
    assert "Review/latest Integration" in PLANNER_SKILL
    assert "project-local validation worktree" in PLANNER_SKILL
    assert "never share it across projects" in PLANNER_SKILL
    assert "Builders never merge `review/latest` or `main`" in PLANNER_SKILL
    assert "planners also never merge directly to `main`" in PLANNER_SKILL
    assert "Planner closeout reports branch/commit evidence" in PLANNER_SKILL
    assert "user-authorized warden during patrol" in PLANNER_SKILL
    assert "integrates accepted planner deliveries" in PLANNER_SKILL
    assert "explicit user confirmation" in PLANNER_SKILL
    assert "Memory closeout records user confirmation" in PLANNER_SKILL
    assert "desktop launch scripts" in PLANNER_SKILL
    assert "stale tmp worktree" in PLANNER_SKILL
