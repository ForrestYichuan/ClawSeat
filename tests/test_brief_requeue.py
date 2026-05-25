from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))
sys.path.insert(0, str(REPO_ROOT / "core" / "scripts"))

from agent_admin_brief import build_parser, cmd_claim, cmd_queue, cmd_requeue, cmd_reset  # noqa: E402
from queue_io import append_event, read_current_state  # noqa: E402


def _brief(path: Path, *, project: str = "p", team: str = "t", task_id: str = "T1",
           mechanical: str = "python3 -c 'print(1)'") -> Path:
    path.write_text(
        f"""---
task_id: {task_id}
project: {project}
team: {team}
created: '2026-05-21T00:00:00+00:00'
created_by: memory
objective: recover task
depends_on: []
acceptance_criteria:
  mechanical:
    - "{mechanical}"
  reviewer: []
  operator: []
seats_required:
  - planner
fuzz_required: false
priority: P2
notify_on_completion:
  - memory
---

# Brief
""",
        encoding="utf-8",
    )
    return path


def test_requeue_recovers_waiting_for_after_brief_fixed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()
    brief = _brief(tmp_path / "brief.md")

    assert cmd_queue(parser.parse_args([
        "queue", "--project", "p", "--team", "t", "--task-id", "T1",
        "--objective", "recover task", "--brief-content-file", str(brief), "--no-wake",
    ])) == 0
    queue = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    append_event(queue, {
        "event_type": "task_waiting_for",
        "actor": "planner@claude",
        "task_id": "T1",
        "waiting_for": "acceptance_criteria",
    })

    rc = cmd_requeue(parser.parse_args([
        "requeue", "--project", "p", "--team", "t", "--task-id", "T1", "--no-wake",
    ]))
    out = capsys.readouterr()

    assert rc == 0
    assert "requeued T1" in out.out
    assert "WAKE_SKIPPED" in out.out
    state = read_current_state(queue)["T1"]
    assert state.status == "task_created"
    assert state.reset_count == 1


def test_requeue_refuses_placeholder_brief(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()
    brief = _brief(
        tmp_path / "brief.md",
        mechanical="TODO: replace with a real mechanical command before dispatch",
    )

    assert cmd_queue(parser.parse_args([
        "queue", "--project", "p", "--team", "t", "--task-id", "T1",
        "--objective", "recover task", "--brief-content-file", str(brief), "--no-wake",
    ])) == 0
    queue = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"

    rc = cmd_requeue(parser.parse_args([
        "requeue", "--project", "p", "--team", "t", "--task-id", "T1", "--no-wake",
    ]))
    out = capsys.readouterr()

    assert rc == 3
    assert "REQUEUE_BLOCKED reason=acceptance_criteria" in out.out
    assert read_current_state(queue)["T1"].status == "task_waiting_for"


def test_reset_then_task_specific_claim_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()
    brief = _brief(tmp_path / "brief.md")

    assert cmd_queue(parser.parse_args([
        "queue", "--project", "p", "--team", "t", "--task-id", "T1",
        "--objective", "recover task", "--brief-content-file", str(brief), "--no-wake",
    ])) == 0
    assert cmd_reset(parser.parse_args([
        "reset", "--project", "p", "--team", "t", "--task-id", "T1",
        "--reason", "operator repaired brief",
    ])) == 0
    assert cmd_claim(parser.parse_args([
        "claim", "--project", "p", "--team", "t", "--task-id", "T1",
        "--actor", "planner@claude",
    ])) == 0

    queue = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    assert read_current_state(queue)["T1"].status == "task_claimed"
