"""Regression tests for brief queue acceptance readiness and narrative criteria."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))
sys.path.insert(0, str(REPO_ROOT / "core" / "scripts"))

from acceptance_executor import aggregate_verdict, run_acceptance  # noqa: E402
from agent_admin_brief import build_parser, cmd_claim, cmd_queue  # noqa: E402
from queue_io import read_current_state  # noqa: E402


def _write_brief(path: Path, *, project: str, team: str, task_id: str, mechanical: str = "true") -> None:
    path.write_text(
        f"""---
task_id: {task_id}
project: {project}
team: {team}
objective: "stable acceptance criteria"
seats_required: [builder]
acceptance_criteria:
  mechanical:
    - "{mechanical}"
---

# Brief
""",
        encoding="utf-8",
    )


def _write_reviewer_only_brief(path: Path, *, project: str, team: str, task_id: str) -> None:
    path.write_text(
        f"""---
task_id: {task_id}
project: {project}
team: {team}
objective: "read-only review"
seats_required: [builder]
acceptance_criteria:
  mechanical: []
  reviewer:
    - "Delivery records inspected files and recommendation."
---

# Brief
""",
        encoding="utf-8",
    )


def _write_profile(home: Path, *, project: str, team: str, planner: str = "t-planner") -> None:
    profiles = home / ".agents" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / f"{project}-profile-dynamic.toml").write_text(
        f'''profile_name = "{project}-profile-dynamic"
project_name = "{project}"
seats = ["memory", "{planner}", "t-builder"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
{team} = {{ seats = ["{planner}", "t-builder"] }}

[seat_roles]
memory = "project-memory"
{planner} = "planner"
t-builder = "builder"
''',
        encoding="utf-8",
    )


def _write_fake_wake_script(tmp_path: Path) -> tuple[Path, Path]:
    log_path = tmp_path / "wake.log"
    script = tmp_path / "fake-send-and-verify.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$WAKE_LOG\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, log_path


def test_generated_todo_brief_defers_wake_and_blocks_claim(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    send_script, wake_log = _write_fake_wake_script(tmp_path)
    monkeypatch.setenv("CLAWSEAT_BRIEF_WAKE_SEND_SCRIPT", str(send_script))
    monkeypatch.setenv("WAKE_LOG", str(wake_log))
    parser = build_parser()

    rc = cmd_queue(parser.parse_args([
        "queue", "--project", "p", "--team", "t", "--task-id", "Ttodo",
        "--objective", "Queue a skeleton brief before criteria are ready",
    ]))
    out = capsys.readouterr()

    assert rc == 0
    assert "WAKE_DEFERRED reason=" in out.out
    assert not wake_log.exists()

    claim_rc = cmd_claim(parser.parse_args([
        "claim", "--project", "p", "--team", "t", "--task-id", "Ttodo",
        "--actor", "planner@claude",
    ]))
    claim_out = capsys.readouterr()

    assert claim_rc == 3
    assert "blocked on acceptance_criteria" in claim_out.out
    queue = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    state = read_current_state(queue)["Ttodo"]
    assert state.status == "task_waiting_for"
    assert state.waiting_for == "acceptance_criteria"


def test_placeholder_brief_content_defers_wake_and_blocks_claim(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    send_script, wake_log = _write_fake_wake_script(tmp_path)
    monkeypatch.setenv("CLAWSEAT_BRIEF_WAKE_SEND_SCRIPT", str(send_script))
    monkeypatch.setenv("WAKE_LOG", str(wake_log))
    project = "p"
    team = "t"
    task_id = "Tplaceholder"
    _write_profile(tmp_path, project=project, team=team)
    brief = tmp_path / "placeholder.md"
    _write_brief(
        brief,
        project=project,
        team=team,
        task_id=task_id,
        mechanical="TODO: replace with a real mechanical command before dispatch",
    )
    parser = build_parser()

    rc = cmd_queue(parser.parse_args([
        "queue", "--project", project, "--team", team, "--task-id", task_id,
        "--objective", "Queue a placeholder criteria brief",
        "--brief-content-file", str(brief),
    ]))
    out = capsys.readouterr()

    assert rc == 0
    assert "WAKE_DEFERRED reason=acceptance_criteria" in out.out
    assert not wake_log.exists()

    claim_rc = cmd_claim(parser.parse_args([
        "claim", "--project", project, "--team", team, "--task-id", task_id,
        "--actor", "planner@claude",
    ]))
    claim_out = capsys.readouterr()

    assert claim_rc == 3
    assert "blocked on acceptance_criteria" in claim_out.out


def test_no_wake_remains_explicit_even_when_criteria_incomplete(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()

    rc = cmd_queue(parser.parse_args([
        "queue", "--project", "p", "--team", "t", "--task-id", "Tnowake",
        "--objective", "Queue with no wake explicit", "--no-wake",
    ]))
    out = capsys.readouterr()

    assert rc == 0
    assert "WAKE_SKIPPED (--no-wake)" in out.out
    assert "WAKE_DEFERRED" not in out.out


def test_queue_rejects_second_unrelated_open_task_by_default(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()

    assert cmd_queue(parser.parse_args([
        "queue", "--project", "p", "--team", "t", "--task-id", "Topen",
        "--objective", "First open task", "--no-wake",
    ])) == 0

    brief = tmp_path / "ready.md"
    _write_brief(brief, project="p", team="t", task_id="Tnext")
    rc = cmd_queue(parser.parse_args([
        "queue", "--project", "p", "--team", "t", "--task-id", "Tnext",
        "--objective", "Second unrelated task",
        "--brief-content-file", str(brief),
        "--no-wake",
    ]))
    out = capsys.readouterr()

    assert rc == 4
    assert "queue has open task" in out.err
    queue = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    state = read_current_state(queue)
    assert set(state) == {"Topen"}


def test_queue_allows_explicit_open_task_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()

    assert cmd_queue(parser.parse_args([
        "queue", "--project", "p", "--team", "t", "--task-id", "Topen",
        "--objective", "First open task", "--no-wake",
    ])) == 0

    brief = tmp_path / "ready.md"
    _write_brief(brief, project="p", team="t", task_id="Tnext")
    rc = cmd_queue(parser.parse_args([
        "queue", "--project", "p", "--team", "t", "--task-id", "Tnext",
        "--objective", "Second task with explicit override",
        "--brief-content-file", str(brief),
        "--allow-open",
        "--no-wake",
    ]))

    assert rc == 0
    queue = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    state = read_current_state(queue)
    assert {"Topen", "Tnext"} <= set(state)


def test_complete_criteria_still_wakes_team_planner(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    project = "p"
    team = "t"
    task_id = "Tready"
    _write_profile(tmp_path, project=project, team=team)
    send_script, wake_log = _write_fake_wake_script(tmp_path)
    monkeypatch.setenv("CLAWSEAT_BRIEF_WAKE_SEND_SCRIPT", str(send_script))
    monkeypatch.setenv("WAKE_LOG", str(wake_log))
    brief = tmp_path / "ready.md"
    _write_brief(brief, project=project, team=team, task_id=task_id)
    parser = build_parser()

    rc = cmd_queue(parser.parse_args([
        "queue", "--project", project, "--team", team, "--task-id", task_id,
        "--objective", "Queue a complete criteria brief",
        "--brief-content-file", str(brief),
    ]))
    out = capsys.readouterr()

    assert rc == 0
    assert "WAKE_OK target=t-planner" in out.out
    assert "--project p t-planner" in wake_log.read_text(encoding="utf-8")


def test_reviewer_only_criteria_wakes_and_claims_without_fake_mechanical(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    project = "p"
    team = "t"
    task_id = "Treviewonly"
    _write_profile(tmp_path, project=project, team=team)
    send_script, wake_log = _write_fake_wake_script(tmp_path)
    monkeypatch.setenv("CLAWSEAT_BRIEF_WAKE_SEND_SCRIPT", str(send_script))
    monkeypatch.setenv("WAKE_LOG", str(wake_log))
    brief = tmp_path / "review-only.md"
    _write_reviewer_only_brief(brief, project=project, team=team, task_id=task_id)
    parser = build_parser()

    rc = cmd_queue(parser.parse_args([
        "queue", "--project", project, "--team", team, "--task-id", task_id,
        "--objective", "Queue a reviewer-only criteria brief",
        "--brief-content-file", str(brief),
    ]))
    out = capsys.readouterr()

    assert rc == 0
    assert "WAKE_OK target=t-planner" in out.out
    assert "--project p t-planner" in wake_log.read_text(encoding="utf-8")

    claim_rc = cmd_claim(parser.parse_args([
        "claim", "--project", project, "--team", team, "--task-id", task_id,
        "--actor", "planner@claude",
    ]))

    assert claim_rc == 0


def test_narrative_mechanical_item_does_not_execute_as_shell_127(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    project = "p"
    team = "t"
    task_id = "Tnarrative"
    brief = tmp_path / ".agents" / "tasks" / project / team / "brief" / f"{task_id}.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    _write_brief(
        brief,
        project=project,
        team=team,
        task_id=task_id,
        mechanical="Reviewer verifies the diff is privacy safe",
    )

    results = run_acceptance(project=project, team=team, task_id=task_id)

    item = results["mechanical"].items[0]
    assert item.result == "skipped"
    assert item.exit_code is None
    assert aggregate_verdict(results) == "FAIL"
    receipt = json.loads(
        (tmp_path / ".agents" / "tasks" / project / team / "acceptance" / f"{task_id}__mechanical.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["items"][0]["result"] == "skipped"
    assert receipt["items"][0].get("exit_code") != 127
