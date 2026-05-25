from __future__ import annotations

from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_PLANNER_POLICY = _REPO / "core" / "skills" / "planner" / "references" / "planner-context-policy.md"


def test_planner_context_policy_forbids_clear_marker() -> None:
    text = _PLANNER_POLICY.read_text(encoding="utf-8")

    assert "[CLEAR-REQUESTED] FORBIDDEN" in text
    assert "[memory: compact-me]" in text
    assert "does not emit direct stop-hook lifecycle markers" in text
