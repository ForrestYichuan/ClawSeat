from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "core" / "skills" / "planner" / "scripts" / "planner_auto_compact.py"


def _write_profile(home: Path) -> None:
    profile = home / ".agents" / "profiles" / "p-profile-dynamic.toml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        """
project_name = "p"
seats = ["memory", "team-planner"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
t = { seats = ["team-planner"] }

[seat_roles]
memory = "project-memory"
team-planner = "planner"
""".lstrip(),
        encoding="utf-8",
    )


def _write_queue(home: Path, *, final_status: str = "task_done") -> Path:
    queue = home / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "event_type": "task_created",
            "event_ts": "2026-05-20T00:00:00+00:00",
            "seq": 1,
            "actor": "memory",
            "task_id": "task-1",
            "brief_path": "tasks/p/t/brief/task-1.md",
        },
        {
            "event_type": "task_claimed",
            "event_ts": "2026-05-20T00:01:00+00:00",
            "seq": 2,
            "actor": "team-planner@claude",
            "task_id": "task-1",
        },
        {
            "event_type": "task_in_progress",
            "event_ts": "2026-05-20T00:02:00+00:00",
            "seq": 3,
            "actor": "team-planner@claude",
            "task_id": "task-1",
        },
    ]
    if final_status == "task_done":
        events.append(
            {
                "event_type": "task_done",
                "event_ts": "2026-05-20T00:03:00+00:00",
                "seq": 4,
                "actor": "team-planner@claude",
                "task_id": "task-1",
                "verdict": "PASS",
            }
        )
    elif final_status == "task_failed":
        events.append(
            {
                "event_type": "task_failed",
                "event_ts": "2026-05-20T00:03:00+00:00",
                "seq": 4,
                "actor": "team-planner@claude",
                "task_id": "task-1",
                "verdict": "FAIL",
                "fail_reason": "mechanical failed",
            }
        )
    queue.write_text("\n".join(json.dumps(e, sort_keys=True) for e in events) + "\n", encoding="utf-8")
    return queue


def _write_send_script(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "send.log"
    script = tmp_path / "send.sh"
    script.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$SEND_LOG"
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, log


def _run(tmp_path: Path, *, final_status: str = "task_done") -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    _write_profile(home)
    _write_queue(home, final_status=final_status)
    send_script, send_log = _write_send_script(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "CLAWSEAT_REAL_HOME": str(home),
            "PLANNER_AUTO_COMPACT_PROJECT": "p",
            "PLANNER_AUTO_COMPACT_WORKSPACE": str(home / ".agents" / "workspaces" / "p" / "team-planner"),
            "CLAWSEAT_PLANNER_COMPACT_SEND_SCRIPT": str(send_script),
            "SEND_LOG": str(send_log),
        }
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_auto_compact_sends_once_when_queue_drained_after_task_done(tmp_path: Path) -> None:
    proc = _run(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "COMPACT_SENT project=p team=t seat=team-planner task_id=task-1 seq=4" in proc.stdout
    home = tmp_path / "home"
    state = json.loads(
        (home / ".agents" / "tasks" / "p" / "t" / "runtime" / "planner-compact" / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["last_compacted_task_id"] == "task-1"
    assert state["last_compacted_queue_seq"] == 4
    snapshot = home / ".agents" / "tasks" / "p" / "t" / "runtime" / "planner-compact" / "latest.md"
    assert "- project: p" in snapshot.read_text(encoding="utf-8")
    assert "- team: t" in snapshot.read_text(encoding="utf-8")
    assert "--project p team-planner /compact" in (tmp_path / "send.log").read_text(encoding="utf-8")


def test_auto_compact_is_idempotent_for_same_queue_seq(tmp_path: Path) -> None:
    first = _run(tmp_path)
    second = _run(tmp_path)

    assert first.returncode == 0
    assert second.returncode == 0
    assert "COMPACT_SKIP reason=already_compacted project=p team=t seq=4" in second.stdout
    assert (tmp_path / "send.log").read_text(encoding="utf-8").count("/compact") == 1


def test_auto_compact_skips_active_queue_but_writes_snapshot(tmp_path: Path) -> None:
    proc = _run(tmp_path, final_status="task_in_progress")

    assert proc.returncode == 0, proc.stderr
    assert "COMPACT_SKIP reason=queue_not_drained project=p team=t open=1 queue_status=active" in proc.stdout
    assert not (tmp_path / "send.log").exists()
    snapshot = (
        tmp_path
        / "home"
        / ".agents"
        / "tasks"
        / "p"
        / "t"
        / "runtime"
        / "planner-compact"
        / "latest.md"
    )
    assert "- queue_status: active" in snapshot.read_text(encoding="utf-8")


def test_auto_compact_skips_failed_queue_but_writes_blocked_snapshot(tmp_path: Path) -> None:
    proc = _run(tmp_path, final_status="task_failed")

    assert proc.returncode == 0, proc.stderr
    assert "COMPACT_SKIP reason=queue_not_drained project=p team=t open=1 queue_status=blocked" in proc.stdout
    assert not (tmp_path / "send.log").exists()
    snapshot = (
        tmp_path
        / "home"
        / ".agents"
        / "tasks"
        / "p"
        / "t"
        / "runtime"
        / "planner-compact"
        / "latest.md"
    )
    text = snapshot.read_text(encoding="utf-8")
    assert "- queue_status: blocked" in text
    assert "- task-1: task_failed (seq=4)" in text
