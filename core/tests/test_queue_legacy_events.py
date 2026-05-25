"""Tests for tolerant queue reads of legacy/manual closeout rows."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import agent_admin_brief as brief_mod
from agent_admin_brief import cmd_queue, planner_status_snapshot
from queue_io import queue_state_label, read_current_state


_PROFILE = """\
profile_name = "p-profile-dynamic"
project_name = "p"
seats = ["memory", "ui-planner"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
t = { seats = ["ui-planner"], notify_policy = "queue_drained_only" }

[seat_roles]
memory = "project-memory"
ui-planner = "planner"

[seat_overrides.ui-planner]
tool = "claude"
auth_mode = "oauth_token"
provider = "anthropic"
"""


def _append(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def _write_profile(home: Path) -> None:
    path = home / ".agents" / "profiles" / "p-profile-dynamic.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PROFILE)


def test_legacy_event_field_without_seq_can_close_task(tmp_path):
    queue = tmp_path / "tasks.queue.jsonl"
    _append(queue, {
        "seq": 1,
        "event_type": "task_created",
        "event_ts": "2026-05-21T00:00:00+00:00",
        "actor": "memory",
        "task_id": "t1",
        "brief_path": "tasks/p/t/brief/t1.md",
    })
    _append(queue, {
        "event": "task_done",
        "ts": "2026-05-21T00:01:00+00:00",
        "seat": "ui-planner",
        "task_id": "t1",
        "verdict": "PASS",
    })

    state = read_current_state(queue)

    assert state["t1"].status == "task_done"
    assert state["t1"].verdict == "PASS"
    assert state["t1"].actor == "ui-planner"
    assert queue_state_label(state) == "drained"


def test_legacy_task_blocked_maps_to_bounced_terminal_state(tmp_path):
    queue = tmp_path / "tasks.queue.jsonl"
    _append(queue, {
        "seq": 1,
        "event_type": "task_created",
        "event_ts": "2026-05-21T00:00:00+00:00",
        "actor": "memory",
        "task_id": "t1",
        "brief_path": "tasks/p/t/brief/t1.md",
    })
    _append(queue, {
        "seq": 2,
        "type": "task_blocked",
        "ts": "2026-05-21T00:01:00+00:00",
        "task_id": "t1",
        "reason": "operator_taking_over",
    })

    state = read_current_state(queue)

    assert state["t1"].status == "task_bounced"
    assert state["t1"].bounce_reason == "operator_taking_over"
    assert queue_state_label(state) == "blocked"


def test_planner_status_ignores_legacy_closed_task_for_queue_depth(tmp_path):
    _write_profile(tmp_path)
    queue = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    _append(queue, {
        "seq": 1,
        "event_type": "task_created",
        "event_ts": "2026-05-21T00:00:00+00:00",
        "actor": "memory",
        "task_id": "t1",
        "brief_path": "tasks/p/t/brief/t1.md",
    })
    _append(queue, {
        "event": "task_done",
        "ts": "2026-05-21T00:01:00+00:00",
        "seat": "ui-planner",
        "task_id": "t1",
        "verdict": "PASS",
    })

    with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
        rows = planner_status_snapshot("p")

    assert rows[0]["queue_state"] == "drained"
    assert rows[0]["queue_depth"] == 0
    assert rows[0]["attention_task_id"] is None


def test_planner_status_marks_pass_needs_integration_receipt_unmerged(tmp_path):
    _write_profile(tmp_path)
    queue = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    _append(queue, {
        "seq": 1,
        "event_type": "task_created",
        "event_ts": "2026-05-21T00:00:00+00:00",
        "actor": "memory",
        "task_id": "t1",
        "brief_path": "tasks/p/t/brief/t1.md",
    })
    _append(queue, {
        "seq": 2,
        "event_type": "task_done",
        "event_ts": "2026-05-21T00:01:00+00:00",
        "actor": "ui-planner",
        "task_id": "t1",
        "verdict": "PASS",
    })
    handoff = tmp_path / ".agents" / "tasks" / "p" / "patrol" / "handoffs" / "t1__ui-planner__memory.json"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(json.dumps({
        "task_id": "t1",
        "source": "ui-planner",
        "target": "memory",
        "verdict": "PASS",
        "integration": "PASS_NEEDS_INTEGRATION",
    }), encoding="utf-8")

    with patch.object(brief_mod, "real_user_home", return_value=tmp_path), \
         patch.object(brief_mod, "_read_session_runtime", return_value={"session_status": "alive", "tool": "", "auth_mode": "", "provider": "", "session_name": "s"}), \
         patch.object(brief_mod, "_check_workspace_dirty", return_value=False), \
         patch.object(brief_mod, "_check_review_latest_clean", return_value=True), \
         patch.object(brief_mod, "_check_review_latest_commit", return_value="abc123"):
        rows = planner_status_snapshot("p")

    row = rows[0]
    assert row["queue_state"] == "drained"
    assert row["integration_status"] == "needs_integration"
    assert row["dispatch_readiness"] == "idle_unmerged"
    assert "delivery needs review/latest integration" in row["dispatch_readiness_reason"]


def test_planner_status_does_not_block_when_reported_commit_now_reaches_review_latest(tmp_path):
    _write_profile(tmp_path)
    queue = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    _append(queue, {
        "seq": 1,
        "event_type": "task_created",
        "event_ts": "2026-05-21T00:00:00+00:00",
        "actor": "memory",
        "task_id": "t1",
        "brief_path": "tasks/p/t/brief/t1.md",
    })
    _append(queue, {
        "seq": 2,
        "event_type": "task_done",
        "event_ts": "2026-05-21T00:01:00+00:00",
        "actor": "ui-planner",
        "task_id": "t1",
        "verdict": "PASS",
    })
    handoff = tmp_path / ".agents" / "tasks" / "p" / "patrol" / "handoffs" / "t1__ui-planner__memory.json"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(json.dumps({
        "task_id": "t1",
        "source": "ui-planner",
        "target": "memory",
        "verdict": "PASS",
        "integration": "PASS_NEEDS_INTEGRATION",
        "builder_commit": "abc123",
    }), encoding="utf-8")

    with patch.object(brief_mod, "real_user_home", return_value=tmp_path), \
         patch.object(brief_mod, "_read_session_runtime", return_value={"session_status": "alive", "tool": "", "auth_mode": "", "provider": "", "session_name": "s"}), \
         patch.object(brief_mod, "_check_workspace_dirty", return_value=False), \
         patch.object(brief_mod, "_check_review_latest_clean", return_value=True), \
         patch.object(brief_mod, "_check_review_latest_commit", return_value="abc123"), \
         patch.object(brief_mod, "_commit_in_review_latest", return_value=True):
        rows = planner_status_snapshot("p")

    row = rows[0]
    assert row["integration_status"] == "clean"
    assert row["dispatch_readiness"] == "clean"


def test_queue_refuses_dispatch_after_unintegrated_pass_delivery(tmp_path, capsys):
    queue = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    _append(queue, {
        "seq": 1,
        "event_type": "task_created",
        "event_ts": "2026-05-21T00:00:00+00:00",
        "actor": "memory",
        "task_id": "t1",
        "brief_path": "tasks/p/t/brief/t1.md",
    })
    _append(queue, {
        "seq": 2,
        "event_type": "task_done",
        "event_ts": "2026-05-21T00:01:00+00:00",
        "actor": "ui-planner",
        "task_id": "t1",
        "verdict": "PASS",
    })
    handoff = tmp_path / ".agents" / "tasks" / "p" / "patrol" / "handoffs" / "t1__ui-planner__memory.json"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(json.dumps({
        "task_id": "t1",
        "source": "ui-planner",
        "target": "memory",
        "verdict": "PASS",
        "integration": "PASS_NEEDS_INTEGRATION",
    }), encoding="utf-8")

    args = Namespace(
        project="p",
        team="t",
        task_id="t2",
        objective="next task",
        depends_on=[],
        seats_required=None,
        parent_task_id=None,
        brief_content_file=None,
        force=False,
        no_wake=True,
        allow_open=False,
        allow_unintegrated=False,
    )
    with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
        rc = cmd_queue(args)

    assert rc == 4
    assert "needs review/latest integration" in capsys.readouterr().err
    state = read_current_state(queue)
    assert "t2" not in state
