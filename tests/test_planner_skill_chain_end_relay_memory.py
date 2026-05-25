from __future__ import annotations

from pathlib import Path


def test_planner_skill_requires_chain_end_relay_to_memory() -> None:
    text = Path("core/skills/planner/SKILL.md").read_text(encoding="utf-8")

    assert "Chain End Relay to Memory" in text
    assert "双入口都适用" in text
    assert "planner-entry route" in text
    assert "complete_handoff.py --source <exact planner seat> --target memory --task-id <id> --status completed --verdict <V> --notify" in text
    stale_generic = "complete_handoff.py --source " + "planner --target memory"
    assert stale_generic not in text
    assert "send-and-verify.sh --project <p> memory" not in text
    assert "wake-up only" in text
    assert "experience retention" in text
    assert "queue_drained_only" in text
    assert "no per-task memory relay" in text
    assert "never_notify_memory" in text
