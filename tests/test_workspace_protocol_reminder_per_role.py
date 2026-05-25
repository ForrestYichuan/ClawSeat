from __future__ import annotations

import importlib.util
from pathlib import Path


_HELPER_PATH = Path(__file__).with_name("test_workspace_protocol_reminder_block.py")
_spec = importlib.util.spec_from_file_location("_protocol_reminder_block_helper", _HELPER_PATH)
assert _spec is not None
assert _spec.loader is not None
_helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helper)
_render = _helper._render


def test_workspace_protocol_reminder_content_is_role_specific() -> None:
    memory = _render("project-memory", engineer_id="memory")
    planner = _render("planner-dispatcher", engineer_id="planner")
    builder = _render("builder", engineer_id="builder")
    patrol = _render("patrol", engineer_id="patrol")

    assert "Verify Queue" in memory
    assert "Brief Fidelity" in memory
    assert "Chain end" in memory
    assert "do not weaken product intent" in memory
    assert "agent_admin.py brief planner-status --project <project>" in memory
    assert "manual queue scans only if it fails or debugging needs detail" in memory
    assert "readiness-sync" not in memory
    assert "NOT optional" not in memory

    assert "Strict fan-in" in planner
    assert "Intent Fidelity" in planner
    assert "relay memory" in planner
    assert "[memory: compact-me]" in planner
    assert "never emit `[CLEAR-REQUESTED]`" in planner

    for specialist in (builder, patrol):
        assert "Closeout MANDATORY two-step" in specialist
        assert "Fan-out trigger" in specialist
        assert "complete_handoff.py" in specialist
