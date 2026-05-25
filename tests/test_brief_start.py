from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))
sys.path.insert(0, str(REPO_ROOT / "core" / "scripts"))

from agent_admin_brief import build_parser, cmd_claim, cmd_queue, cmd_start  # noqa: E402
from queue_io import read_current_state  # noqa: E402


def _parser():
    return build_parser()


def _queue_ready_task(tmp_path: Path, monkeypatch, *, project: str = "p", team: str = "t", task_id: str = "T1"):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = _parser()
    brief = tmp_path / "ready.md"
    brief.write_text(
        f"""---
task_id: {task_id}
project: {project}
team: {team}
objective: "ready task"
seats_required: [planner]
acceptance_criteria:
  mechanical:
    - "true"
---

# Ready
""",
        encoding="utf-8",
    )
    assert cmd_queue(
        parser.parse_args([
            "queue",
            "--project", project,
            "--team", team,
            "--task-id", task_id,
            "--objective", "ready task",
            "--brief-content-file", str(brief),
            "--no-wake",
        ])
    ) == 0
    return parser


def _queue_path(tmp_path: Path, project: str = "p", team: str = "t") -> Path:
    return tmp_path / ".agents" / "tasks" / project / team / "tasks.queue.jsonl"


def test_start_moves_claimed_task_to_in_progress(tmp_path, monkeypatch, capsys):
    parser = _queue_ready_task(tmp_path, monkeypatch)
    assert cmd_claim(
        parser.parse_args([
            "claim",
            "--project", "p",
            "--team", "t",
            "--task-id", "T1",
            "--actor", "planner@claude",
        ])
    ) == 0

    rc = cmd_start(
        parser.parse_args([
            "start",
            "--project", "p",
            "--team", "t",
            "--task-id", "T1",
            "--actor", "planner@claude",
        ])
    )
    out = capsys.readouterr()

    assert rc == 0
    assert "started T1" in out.out
    assert read_current_state(_queue_path(tmp_path))["T1"].status == "task_in_progress"


def test_start_is_idempotent_for_already_in_progress(tmp_path, monkeypatch, capsys):
    parser = _queue_ready_task(tmp_path, monkeypatch)
    cmd_claim(
        parser.parse_args([
            "claim", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ])
    )
    cmd_start(
        parser.parse_args([
            "start", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ])
    )

    rc = cmd_start(
        parser.parse_args([
            "start", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ])
    )
    out = capsys.readouterr()

    assert rc == 0
    assert "already in_progress" in out.out
    events = [
        line
        for line in _queue_path(tmp_path).read_text(encoding="utf-8").splitlines()
        if '"event_type": "task_in_progress"' in line
    ]
    assert len(events) == 1


def test_start_rejects_unclaimed_task(tmp_path, monkeypatch, capsys):
    parser = _queue_ready_task(tmp_path, monkeypatch)

    rc = cmd_start(
        parser.parse_args([
            "start",
            "--project", "p",
            "--team", "t",
            "--task-id", "T1",
            "--actor", "planner@claude",
        ])
    )
    out = capsys.readouterr()

    assert rc == 2
    assert "not startable" in out.err
    assert read_current_state(_queue_path(tmp_path))["T1"].status == "task_created"
