"""Regression tests for Phase 1 post-review 7 findings.

Each test reproduces the bug as it existed pre-fix and asserts the fix
behavior. Reviewer: codex (phase1-codex-acceptance-check.md follow-up).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))
sys.path.insert(0, str(REPO_ROOT / "core" / "scripts"))

from queue_io import VALID_TRANSITIONS, QueueError, append_event, read_current_state  # noqa: E402


def _write_ready_brief_content(
    path: Path,
    *,
    project: str = "p",
    team: str = "t",
    task_id: str = "T1",
) -> None:
    path.write_text(
        f"""---
task_id: {task_id}
project: {project}
team: {team}
created: '2026-05-15T00:00:00+00:00'
created_by: memory
objective: ready acceptance
acceptance_criteria:
  mechanical:
    - "true"
  reviewer: []
  operator: []
seats_required: [builder]
fuzz_required: false
priority: P2
notify_on_completion: [memory]
---

# body
""",
        encoding="utf-8",
    )


# ---------- Finding #1: render preserves model field ----------


def test_finding1_render_preserves_model(tmp_path):
    from render_project_toml_v3 import render_project_toml_v3

    proposals = tmp_path / "_config-proposals"
    proposals.mkdir()
    (proposals / "core__approved.yaml").write_text(
        """---
project: p
team: core
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
seats:
  - role: planner
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    model: claude-opus-4-7
    rationale: ok
estimated_monthly_cost_usd: { low: 80, high: 120 }
---
""",
        encoding="utf-8",
    )
    toml_text = render_project_toml_v3(project="p", proposals_dir=proposals)
    assert "model = \"claude-opus-4-7\"" in toml_text, "model lost in render"


# ---------- Finding #2: validator enforces role catalog ----------


def test_finding2_validator_rejects_unknown_role(tmp_path):
    from proposal_validator import validate_proposal_file

    bad = tmp_path / "core__approved.yaml"
    bad.write_text(
        """---
project: p
team: core
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
seats:
  - role: definitely-not-a-real-role
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    rationale: should not pass
estimated_monthly_cost_usd: { low: 1, high: 2 }
---
""",
        encoding="utf-8",
    )
    report = validate_proposal_file(bad)
    assert not report.ok
    assert any("known catalog" in v for v in report.violations), report.violations


def test_finding2_known_roles_still_pass(tmp_path):
    from proposal_validator import validate_proposal_file

    good = tmp_path / "core__approved.yaml"
    good.write_text(
        """---
project: p
team: core
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
seats:
  - role: planner
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    rationale: ok
  - role: designer-image
    tool: codex
    provider: openai
    auth_mode: api
    rationale: ok
estimated_monthly_cost_usd: { low: 1, high: 2 }
---
""",
        encoding="utf-8",
    )
    report = validate_proposal_file(good)
    assert report.ok, report.violations


# ---------- Finding #3: cross-field project/team validation ----------


def test_finding3_render_rejects_project_mismatch(tmp_path):
    from render_project_toml_v3 import render_project_toml_v3

    proposals = tmp_path / "_config-proposals"
    proposals.mkdir()
    (proposals / "core__approved.yaml").write_text(
        """---
project: other-project
team: core
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
seats:
  - role: planner
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    rationale: ok
estimated_monthly_cost_usd: { low: 1, high: 2 }
---
""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="project mismatch"):
        render_project_toml_v3(project="actual-project", proposals_dir=proposals)


def test_finding3_render_rejects_team_filename_mismatch(tmp_path):
    from render_project_toml_v3 import render_project_toml_v3

    proposals = tmp_path / "_config-proposals"
    proposals.mkdir()
    # File named core__approved.yaml but yaml says team=shell
    (proposals / "core__approved.yaml").write_text(
        """---
project: p
team: shell
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
seats:
  - role: planner
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    rationale: ok
estimated_monthly_cost_usd: { low: 1, high: 2 }
---
""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="does not match filename"):
        render_project_toml_v3(project="p", proposals_dir=proposals)


# ---------- Finding #4: path traversal blocked in agent_admin_brief ----------


def test_finding4_rejects_path_traversal_team(tmp_path, monkeypatch):
    from agent_admin_brief import build_parser, cmd_queue, InputValidationError

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()
    args = parser.parse_args(
        [
            "queue",
            "--project", "p",
            "--team", "../escape",
            "--task-id", "T1",
            "--objective", "x",
        ]
    )
    rc = cmd_queue(args)
    assert rc == 2, "path traversal must be rejected"
    # Verify nothing was written outside the validated path
    assert not (tmp_path.parent / "escape").exists()


def test_finding4_rejects_path_traversal_task_id(tmp_path, monkeypatch):
    from agent_admin_brief import build_parser, cmd_queue

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()
    args = parser.parse_args(
        [
            "queue",
            "--project", "p",
            "--team", "t",
            "--task-id", "../sneak",
            "--objective", "x",
        ]
    )
    rc = cmd_queue(args)
    assert rc == 2


# ---------- Finding #5: atomic non-destructive brief write ----------


def test_finding5_failed_append_does_not_unlink_existing_brief(tmp_path, monkeypatch):
    from agent_admin_brief import build_parser, cmd_queue

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()
    args1 = parser.parse_args(
        [
            "queue",
            "--project", "p",
            "--team", "t",
            "--task-id", "T1",
            "--objective", "first",
        ]
    )
    assert cmd_queue(args1) == 0
    brief = tmp_path / ".agents" / "tasks" / "p" / "t" / "brief" / "T1.md"
    original_text = brief.read_text(encoding="utf-8")

    # Second queue call with --force will overwrite; the state machine rejects
    # the duplicate task_created event, simulating an append failure.
    args2 = parser.parse_args(
        [
            "queue",
            "--project", "p",
            "--team", "t",
            "--task-id", "T1",
            "--objective", "second",
            "--force",
        ]
    )
    rc = cmd_queue(args2)
    assert rc == 1, "duplicate task_created must fail"
    # Pre-existing brief MUST remain intact (Fix #5)
    assert brief.exists(), "brief must not be deleted on append failure"
    assert brief.read_text(encoding="utf-8") == original_text, (
        "brief contents must be unchanged when append fails"
    )


# ---------- Finding #6: task_created → task_waiting_for is allowed ----------


def test_finding6_state_machine_allows_created_to_waiting_for():
    assert "task_waiting_for" in VALID_TRANSITIONS["task_created"], (
        "state machine must allow task_created → task_waiting_for per spec §4.3"
    )


def test_finding6_cli_writes_waiting_for_when_depends_unmet(tmp_path, monkeypatch):
    from agent_admin_brief import build_parser, cmd_queue, cmd_claim

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()

    # Queue UPSTREAM (T-UP) and DEPENDENT (T-DOWN with depends_on=[T-UP])
    assert (
        cmd_queue(
            parser.parse_args(
                ["queue", "--project", "p", "--team", "t", "--task-id", "T-UP",
                 "--objective", "upstream"]
            )
        )
        == 0
    )
    assert (
        cmd_queue(
            parser.parse_args(
                ["queue", "--project", "p", "--team", "t", "--task-id", "T-DOWN",
                 "--objective", "dependent", "--depends-on", "T-UP"]
            )
        )
        == 0
    )

    # Claim T-DOWN: upstream is task_created (not done) → expect waiting_for
    rc = cmd_claim(
        parser.parse_args(
            ["claim", "--project", "p", "--team", "t", "--task-id", "T-DOWN",
             "--actor", "planner@claude"]
        )
    )
    assert rc == 3, "claim should return 3 when depends_on unmet"

    # Verify event was actually written (no QueueError)
    queue_path = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    text = queue_path.read_text(encoding="utf-8")
    assert "task_waiting_for" in text, "task_waiting_for event must be persisted"


# ---------- Finding #7: skeleton passes brief.schema.json ----------


def test_finding7_skeleton_has_nonempty_required_fields(tmp_path, monkeypatch):
    """Generated skeleton must satisfy brief.schema.json minItems: 1 constraints."""
    import yaml

    from agent_admin_brief import build_parser, cmd_queue

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()
    args = parser.parse_args(
        [
            "queue", "--project", "p", "--team", "t", "--task-id", "T1",
            "--objective", "skel",
        ]
    )
    assert cmd_queue(args) == 0
    brief = tmp_path / ".agents" / "tasks" / "p" / "t" / "brief" / "T1.md"
    text = brief.read_text(encoding="utf-8")
    # Extract frontmatter and parse via safe_load (round-trip robust)
    front = text.split("---\n", 2)[1]
    data = yaml.safe_load(front)
    assert isinstance(data.get("seats_required"), list)
    assert len(data["seats_required"]) >= 1, "seats_required must have minItems 1"
    assert isinstance(data["acceptance_criteria"]["mechanical"], list)
    assert len(data["acceptance_criteria"]["mechanical"]) >= 1


def test_brief_queue_uses_real_user_home_under_sandbox_home(tmp_path, monkeypatch):
    """Sandbox HOME must not strand v3 brief queues under identity HOME."""
    from agent_admin_brief import build_parser, cmd_claim, cmd_queue

    real_home = tmp_path / "real-home"
    sandbox_home = tmp_path / ".agent-runtime" / "identities" / "claude" / "home"
    real_home.mkdir(parents=True)
    sandbox_home.mkdir(parents=True)
    monkeypatch.delenv("CLAWSEAT_REAL_HOME", raising=False)
    monkeypatch.setenv("HOME", str(sandbox_home))
    monkeypatch.setenv("AGENT_HOME", str(real_home))

    parser = build_parser()
    brief = tmp_path / "ready.md"
    _write_ready_brief_content(brief, task_id="T1")
    assert (
        cmd_queue(
            parser.parse_args(
                ["queue", "--project", "p", "--team", "t", "--task-id", "T1",
                 "--objective", "real-home", "--brief-content-file", str(brief),
                 "--no-wake"]
            )
        )
        == 0
    )
    rc = cmd_claim(
        parser.parse_args(
            ["claim", "--project", "p", "--team", "t", "--task-id", "T1",
             "--actor", "planner@claude"]
        )
    )

    assert rc == 0
    assert (real_home / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl").exists()
    assert not (sandbox_home / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl").exists()


def test_brief_done_advances_created_task_to_pass(tmp_path, monkeypatch):
    from agent_admin_brief import build_parser, cmd_done, cmd_queue
    from queue_io import read_current_state, read_events

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()
    brief = tmp_path / "ready.md"
    _write_ready_brief_content(brief, task_id="Tdone")
    assert (
        cmd_queue(
            parser.parse_args(
                ["queue", "--project", "p", "--team", "t", "--task-id", "Tdone",
                 "--objective", "done", "--brief-content-file", str(brief), "--no-wake"]
            )
        )
        == 0
    )

    rc = cmd_done(
        parser.parse_args(
            ["done", "--project", "p", "--team", "t", "--task-id", "Tdone",
             "--actor", "planner@claude"]
        )
    )

    queue_path = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    assert rc == 0
    assert [event["event_type"] for event in read_events(queue_path)] == [
        "task_created",
        "task_claimed",
        "task_in_progress",
        "task_done",
    ]
    state = read_current_state(queue_path)["Tdone"]
    assert state.status == "task_done"
    assert state.verdict == "PASS"


def test_brief_done_refuses_unmet_dependencies(tmp_path, monkeypatch):
    from agent_admin_brief import build_parser, cmd_claim, cmd_done, cmd_queue

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()
    assert (
        cmd_queue(
            parser.parse_args(
                [
                    "queue",
                    "--project", "p",
                    "--team", "t",
                    "--task-id", "DOWN",
                    "--objective", "blocked",
                    "--depends-on", "UP",
                ]
            )
        )
        == 0
    )
    assert (
        cmd_claim(
            parser.parse_args(
                ["claim", "--project", "p", "--team", "t", "--task-id", "DOWN",
                 "--actor", "planner@claude"]
            )
        )
        == 3
    )

    rc = cmd_done(
        parser.parse_args(
            ["done", "--project", "p", "--team", "t", "--task-id", "DOWN",
             "--actor", "planner@claude"]
        )
    )

    queue_path = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    assert rc == 3
    assert read_current_state(queue_path)["DOWN"].status == "task_waiting_for"


def test_agent_admin_acceptance_run_marks_queue_done_on_pass(tmp_path):
    brief_content = tmp_path / "brief.md"
    brief_content.write_text(
        """---
task_id: Taccept
project: p
team: t
created: '2026-05-15T00:00:00+00:00'
created_by: memory
objective: pass acceptance
acceptance_criteria:
  mechanical:
    - "true"
  reviewer: []
  operator: []
seats_required: [builder]
fuzz_required: false
priority: P2
notify_on_completion: [memory]
---

# body
""",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "CLAWSEAT_SUPPRESS_TOOL_BIN_WARNING": "1",
        "CLAWSEAT_REAL_HOME": str(tmp_path),
    }
    agent_admin = REPO_ROOT / "core" / "scripts" / "agent_admin.py"

    queued = subprocess.run(
        [
            sys.executable,
            str(agent_admin),
            "brief",
            "queue",
            "--project", "p",
            "--team", "t",
            "--task-id", "Taccept",
            "--objective", "pass acceptance",
            "--brief-content-file", str(brief_content),
            "--no-wake",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert queued.returncode == 0, queued.stderr

    accepted = subprocess.run(
        [
            sys.executable,
            str(agent_admin),
            "acceptance",
            "run",
            "--project", "p",
            "--team", "t",
            "--task-id", "Taccept",
            "--actor", "planner@claude",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert "aggregate: PASS" in accepted.stdout

    queue_path = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    state = read_current_state(queue_path)["Taccept"]
    assert state.status == "task_done"
    assert state.verdict == "PASS"


def test_agent_admin_seat_liveness_filters_by_seat_id(tmp_path):
    import sqlite3
    from datetime import datetime, timezone

    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE seats (
              project TEXT,
              seat_id TEXT,
              role TEXT,
              tool TEXT,
              auth_mode TEXT,
              provider TEXT,
              status TEXT,
              last_heartbeat TEXT,
              session_name TEXT,
              workspace TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO seats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "p",
                "builder-1",
                "builder",
                "codex",
                "oauth",
                "openai",
                "live",
                datetime.now(timezone.utc).isoformat(),
                "p-builder-1-codex",
                "/tmp/ws",
            ),
        )

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "core" / "scripts" / "agent_admin.py"),
            "seat",
            "liveness",
            "--project", "p",
            "--seat", "builder-1",
            "--json",
        ],
        env={**os.environ, "CLAWSEAT_STATE_DB": str(db_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    rows = json.loads(proc.stdout)
    assert rows and rows[0]["seat_id"] == "builder-1"


def test_finding_A_waiting_for_retry_succeeds_after_upstream_done(tmp_path, monkeypatch):
    """Post-retest Fix #A: cmd_claim must accept task_waiting_for retry."""
    from agent_admin_brief import build_parser, cmd_claim, cmd_queue
    from queue_io import append_event

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()

    # Queue UP and DOWN(depends_on=[UP])
    up_brief = tmp_path / "up.md"
    down_brief = tmp_path / "down.md"
    _write_ready_brief_content(up_brief, task_id="UP")
    _write_ready_brief_content(down_brief, task_id="DOWN")
    cmd_queue(parser.parse_args(["queue", "--project", "p", "--team", "t",
                                  "--task-id", "UP", "--objective", "u",
                                  "--brief-content-file", str(up_brief),
                                  "--no-wake"]))
    cmd_queue(parser.parse_args(["queue", "--project", "p", "--team", "t",
                                  "--task-id", "DOWN", "--objective", "d",
                                  "--depends-on", "UP",
                                  "--brief-content-file", str(down_brief),
                                  "--no-wake"]))

    # First claim DOWN: upstream not done → waiting_for, exit 3
    rc1 = cmd_claim(parser.parse_args(["claim", "--project", "p", "--team", "t",
                                        "--task-id", "DOWN", "--actor", "planner@claude"]))
    assert rc1 == 3

    # Manually drive UP to done (queue_io transitions)
    queue_path = tmp_path / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    append_event(queue_path, {"event_type": "task_claimed", "actor": "planner@claude", "task_id": "UP"})
    append_event(queue_path, {"event_type": "task_in_progress", "actor": "planner@claude", "task_id": "UP"})
    append_event(queue_path, {"event_type": "task_done", "actor": "memory", "task_id": "UP", "verdict": "PASS"})

    # Retry claim DOWN — now upstream done; must succeed and append task_claimed
    rc2 = cmd_claim(parser.parse_args(["claim", "--project", "p", "--team", "t",
                                        "--task-id", "DOWN", "--actor", "planner@claude"]))
    assert rc2 == 0, "second claim after upstream done must succeed"
    text = queue_path.read_text(encoding="utf-8")
    # Last DOWN event should be task_claimed
    down_events = [json.loads(line) for line in text.splitlines() if json.loads(line).get("task_id") == "DOWN"]
    assert down_events[-1]["event_type"] == "task_claimed", (
        f"DOWN should be claimed after retry, got {down_events[-1]['event_type']}"
    )


def test_finding_B_skeleton_yaml_round_trips_with_quoted_objective(tmp_path, monkeypatch):
    """Post-retest Fix #B: skeleton must produce schema-valid YAML even with quotes."""
    import yaml

    from agent_admin_brief import build_parser, cmd_queue

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()
    objective_with_quote = "objective with 'single' and \"double\" quotes"
    rc = cmd_queue(parser.parse_args([
        "queue", "--project", "p", "--team", "t", "--task-id", "T-Q",
        "--objective", objective_with_quote,
    ]))
    assert rc == 0

    brief = tmp_path / ".agents" / "tasks" / "p" / "t" / "brief" / "T-Q.md"
    text = brief.read_text(encoding="utf-8")
    # Extract frontmatter
    assert text.startswith("---\n")
    end = text.find("\n---\n", 4)
    front_text = text[4:end]
    data = yaml.safe_load(front_text)
    # Schema requires these to be strings; round-trip must not coerce to datetime
    assert isinstance(data["created"], str), f"created must round-trip as str, got {type(data['created'])}"
    assert isinstance(data["objective"], str)
    assert data["objective"] == objective_with_quote
    # Also validate against brief schema if jsonschema available
    try:
        import jsonschema  # type: ignore
        schema_path = REPO_ROOT / "core" / "schemas" / "brief.schema.json"
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(data, schema)
    except ImportError:
        pytest.skip("jsonschema not installed; round-trip type check sufficient")


def test_finding7_skeleton_accepts_seats_required_override(tmp_path, monkeypatch):
    import yaml

    from agent_admin_brief import build_parser, cmd_queue

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    parser = build_parser()
    args = parser.parse_args(
        [
            "queue", "--project", "p", "--team", "t", "--task-id", "T1",
            "--objective", "skel",
            "--seats-required", "builder", "reviewer",
        ]
    )
    assert cmd_queue(args) == 0
    brief = tmp_path / ".agents" / "tasks" / "p" / "t" / "brief" / "T1.md"
    front = brief.read_text(encoding="utf-8").split("---\n", 2)[1]
    data = yaml.safe_load(front)
    assert data["seats_required"] == ["builder", "reviewer"]
