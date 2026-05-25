from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BRIEF = REPO / "core" / "templates" / "memory-bootstrap.template.md"


def test_memory_brief_lists_three_templates() -> None:
    text = BRIEF.read_text(encoding="utf-8")
    assert "clawseat-creative: 5-seat" in text
    assert "clawseat-engineering: 6-seat" in text
    assert "clawseat-solo: v3 MULTI_TEAM_MINIMAL" in text
    assert "完全自定义" in text


def test_memory_brief_installs_planner_hook_into_current_seat_workspace() -> None:
    text = BRIEF.read_text(encoding="utf-8")
    assert "--workspace ${AGENT_HOME}/.agents/workspaces/${PROJECT_NAME}/${seat}" in text
    assert "--workspace ${AGENT_HOME}/.agents/workspaces/${PROJECT_NAME}/planner" not in text
