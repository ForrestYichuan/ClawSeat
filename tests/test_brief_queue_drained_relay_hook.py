"""Tests for the queue-drained relay hook introduced in MP007.

All tests are unit tests: no real tmux, no live memory session, no real
complete_handoff.py invocation. The test seam is monkeypatching
agent_admin_brief._do_relay_complete_handoff.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))
sys.path.insert(0, str(REPO_ROOT / "core" / "scripts"))

import agent_admin_brief as brief_mod  # noqa: E402
from agent_admin_brief import (  # noqa: E402
    build_parser,
    cmd_done,
    _team_notify_policy,
    _team_queue_is_drained,
    _maybe_relay_queue_drained,
)
from queue_io import read_current_state  # noqa: E402


# ---------------------------------------------------------------------------
# Profile / queue fixture helpers
# ---------------------------------------------------------------------------

def _write_profile(
    home: Path,
    *,
    project: str,
    team: str,
    planner: str = "t-planner",
    notify_policy: str = "queue_drained_only",
) -> None:
    profiles = home / ".agents" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / f"{project}-profile-dynamic.toml").write_text(
        f"""profile_name = "{project}-profile-dynamic"
project_name = "{project}"
seats = ["memory", "{planner}", "t-builder"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
{team} = {{ seats = ["{planner}", "t-builder"], notify_policy = "{notify_policy}" }}

[seat_roles]
memory = "project-memory"
{planner} = "planner"
t-builder = "builder"
""",
        encoding="utf-8",
    )


def _write_brief_file(
    home: Path, *, project: str, team: str, task_id: str
) -> Path:
    brief_dir = home / ".agents" / "tasks" / project / team / "brief"
    brief_dir.mkdir(parents=True, exist_ok=True)
    brief = brief_dir / f"{task_id}.md"
    brief.write_text(
        f"""---
task_id: {task_id}
project: {project}
team: {team}
objective: "test task"
seats_required: [planner]
acceptance_criteria:
  mechanical:
    - "true"
---
""",
        encoding="utf-8",
    )
    return brief


def _queue_path(home: Path, project: str, team: str) -> Path:
    return home / ".agents" / "tasks" / project / team / "tasks.queue.jsonl"


def _enqueue_task(home: Path, project: str, team: str, task_id: str) -> Path:
    """Append task_created to the queue so cmd_done can find the task."""
    from queue_io import append_event
    q = _queue_path(home, project, team)
    q.parent.mkdir(parents=True, exist_ok=True)
    append_event(q, {
        "event_type": "task_created",
        "actor": "memory",
        "task_id": task_id,
        "brief_path": f"tasks/{project}/{team}/brief/{task_id}.md",
        "parent_task_id": None,
        "depends_on": [],
    })
    return q


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

class TestTeamNotifyPolicy:
    def test_reads_queue_drained_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t", notify_policy="queue_drained_only")
        assert _team_notify_policy("p", "t") == "queue_drained_only"

    def test_reads_never_notify_memory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t", notify_policy="never_notify_memory")
        assert _team_notify_policy("p", "t") == "never_notify_memory"

    def test_returns_empty_when_profile_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        assert _team_notify_policy("no-such-project", "t") == ""

    def test_returns_empty_when_team_not_in_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t", notify_policy="queue_drained_only")
        assert _team_notify_policy("p", "other-team") == ""


class TestTeamQueueIsDrained:
    def test_single_task_done_is_drained(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T1")
        queue = _enqueue_task(tmp_path, "p", "t", "T1")
        parser = build_parser()
        monkeypatch.setattr(brief_mod, "_do_relay_complete_handoff", lambda *a, **kw: None)
        cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ]))
        assert _team_queue_is_drained(queue, "T1") is True

    def test_pending_sibling_not_drained(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T1")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T2")
        queue = _enqueue_task(tmp_path, "p", "t", "T1")
        _enqueue_task(tmp_path, "p", "t", "T2")
        parser = build_parser()
        monkeypatch.setattr(brief_mod, "_do_relay_complete_handoff", lambda *a, **kw: None)
        cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ]))
        assert _team_queue_is_drained(queue, "T1") is False

    def test_failed_sibling_not_drained(self, tmp_path, monkeypatch):
        from queue_io import append_event

        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T1")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T2")
        queue = _enqueue_task(tmp_path, "p", "t", "T1")
        _enqueue_task(tmp_path, "p", "t", "T2")
        append_event(queue, {"event_type": "task_claimed", "actor": "planner@claude", "task_id": "T2"})
        append_event(queue, {"event_type": "task_in_progress", "actor": "planner@claude", "task_id": "T2"})
        append_event(queue, {
            "event_type": "task_failed",
            "actor": "planner@claude",
            "task_id": "T2",
            "verdict": "FAIL",
            "fail_reason": "mechanical failed",
        })
        parser = build_parser()
        monkeypatch.setattr(brief_mod, "_do_relay_complete_handoff", lambda *a, **kw: None)
        cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ]))
        assert _team_queue_is_drained(queue, "T1") is False


# ---------------------------------------------------------------------------
# Integration tests for cmd_done relay hook
# ---------------------------------------------------------------------------

class TestCmdDoneRelayHook:
    def test_drained_queue_triggers_relay(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t", notify_policy="queue_drained_only")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T1")
        _enqueue_task(tmp_path, "p", "t", "T1")

        relay_calls: list[tuple] = []
        monkeypatch.setattr(
            brief_mod, "_do_relay_complete_handoff",
            lambda project, team, task_id, planner_seat: relay_calls.append((project, team, task_id, planner_seat)),
        )

        parser = build_parser()
        rc = cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ]))
        out = capsys.readouterr()

        assert rc == 0
        assert len(relay_calls) == 1
        assert relay_calls[0] == ("p", "t", "T1", "t-planner")
        assert "RELAY_OK" in out.out

    def test_reset_task_can_be_done_and_relayed(self, tmp_path, monkeypatch, capsys):
        from queue_io import append_event

        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t", notify_policy="queue_drained_only")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T1")
        queue = _enqueue_task(tmp_path, "p", "t", "T1")
        append_event(queue, {
            "event_type": "task_reset",
            "actor": "memory",
            "task_id": "T1",
            "reset_reason": "acceptance criteria repaired",
        })

        relay_calls: list[tuple] = []
        monkeypatch.setattr(
            brief_mod, "_do_relay_complete_handoff",
            lambda project, team, task_id, planner_seat: relay_calls.append((project, team, task_id, planner_seat)),
        )

        parser = build_parser()
        rc = cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ]))
        out = capsys.readouterr()
        state = read_current_state(queue)

        assert rc == 0
        assert state["T1"].status == "task_done"
        assert relay_calls == [("p", "t", "T1", "t-planner")]
        assert "RELAY_OK" in out.out

    def test_undrained_queue_suppresses_relay(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t", notify_policy="queue_drained_only")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T1")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T2")
        _enqueue_task(tmp_path, "p", "t", "T1")
        _enqueue_task(tmp_path, "p", "t", "T2")  # sibling still pending

        relay_calls: list[tuple] = []
        monkeypatch.setattr(
            brief_mod, "_do_relay_complete_handoff",
            lambda *a, **kw: relay_calls.append(a),
        )

        parser = build_parser()
        rc = cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ]))
        out = capsys.readouterr()

        assert rc == 0
        assert relay_calls == []
        assert "RELAY_OK" not in out.out

    def test_never_notify_memory_suppresses_relay(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t", notify_policy="never_notify_memory")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T1")
        _enqueue_task(tmp_path, "p", "t", "T1")

        relay_calls: list[tuple] = []
        monkeypatch.setattr(
            brief_mod, "_do_relay_complete_handoff",
            lambda *a, **kw: relay_calls.append(a),
        )

        parser = build_parser()
        rc = cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ]))
        out = capsys.readouterr()

        assert rc == 0
        assert relay_calls == []
        assert "RELAY_OK" not in out.out

    def test_quality_docs_never_notify_suppresses_relay(self, tmp_path, monkeypatch, capsys):
        """Quality-docs teams use never_notify_memory — relay must be suppressed."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        profiles = tmp_path / ".agents" / "profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / "qp-profile-dynamic.toml").write_text(
            """profile_name = "qp-profile-dynamic"
project_name = "qp"
seats = ["memory", "qd-planner"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
quality-docs = { seats = ["qd-planner"], notify_policy = "never_notify_memory", team_type = "quality-docs" }

[seat_roles]
memory = "project-memory"
qd-planner = "planner"
""",
            encoding="utf-8",
        )
        _write_brief_file(tmp_path, project="qp", team="quality-docs", task_id="QT1")
        _enqueue_task(tmp_path, "qp", "quality-docs", "QT1")

        relay_calls: list[tuple] = []
        monkeypatch.setattr(
            brief_mod, "_do_relay_complete_handoff",
            lambda *a, **kw: relay_calls.append(a),
        )

        parser = build_parser()
        rc = cmd_done(parser.parse_args([
            "done", "--project", "qp", "--team", "quality-docs",
            "--task-id", "QT1", "--actor", "planner@claude",
        ]))
        assert rc == 0
        assert relay_calls == []

    def test_rerun_after_receipt_exists_is_idempotent(self, tmp_path, monkeypatch, capsys):
        """Rerunning brief done when receipt already exists skips relay (RELAY_SKIP)."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t", notify_policy="queue_drained_only")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T1")
        _enqueue_task(tmp_path, "p", "t", "T1")

        relay_calls: list[tuple] = []
        monkeypatch.setattr(
            brief_mod, "_do_relay_complete_handoff",
            lambda *a, **kw: relay_calls.append(a),
        )

        parser = build_parser()
        # First run: completes and triggers relay
        cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ]))
        assert len(relay_calls) == 1

        # Simulate receipt existing (idempotency guard)
        receipt_dir = tmp_path / ".agents" / "tasks" / "p" / "patrol" / "handoffs"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        (receipt_dir / "T1__t-planner__memory.json").write_text("{}", encoding="utf-8")

        # Second run: already task_done PASS + receipt exists -> RELAY_SKIP
        rc2 = cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ]))
        out2 = capsys.readouterr()

        assert rc2 == 0
        assert len(relay_calls) == 1  # not called again
        assert "RELAY_SKIP" in out2.out

    def test_acceptance_pass_path_shares_relay_hook(self, tmp_path, monkeypatch, capsys):
        """acceptance run PASS calls brief_done which calls the relay hook."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t", notify_policy="queue_drained_only")
        # Write brief with passing mechanical criterion
        brief_dir = tmp_path / ".agents" / "tasks" / "p" / "t" / "brief"
        brief_dir.mkdir(parents=True, exist_ok=True)
        (brief_dir / "T1.md").write_text(
            """---
task_id: T1
project: p
team: t
objective: "acceptance path test"
seats_required: [planner]
acceptance_criteria:
  mechanical:
    - "true"
---
""",
            encoding="utf-8",
        )
        # Pre-claim T1 so brief_done can find it
        _enqueue_task(tmp_path, "p", "t", "T1")
        from queue_io import append_event
        queue = _queue_path(tmp_path, "p", "t")
        append_event(queue, {"event_type": "task_claimed", "actor": "planner@claude", "task_id": "T1"})

        relay_calls: list[tuple] = []
        monkeypatch.setattr(
            brief_mod, "_do_relay_complete_handoff",
            lambda *a, **kw: relay_calls.append(a),
        )

        import argparse
        from agent_admin import cmd_acceptance_run
        acceptance_args = argparse.Namespace(
            project="p",
            team="t",
            task_id="T1",
            actor="planner@claude",
            brief_path=None,
            skip_queue_done=False,
            baseline_criteria=0,
            reviewer_seat=None,
            cwd=None,
            profile=None,
        )
        cmd_acceptance_run(acceptance_args)
        capsys.readouterr()  # drain output

        assert len(relay_calls) == 1
        assert relay_calls[0][0] == "p"   # project
        assert relay_calls[0][1] == "t"   # team
        assert relay_calls[0][2] == "T1"  # task_id

    def test_no_real_tmux_or_session_dependency(self, tmp_path, monkeypatch, capsys):
        """Relay hook must not fail when TMUX is not set or sessions don't exist."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        monkeypatch.delenv("TMUX", raising=False)
        _write_profile(tmp_path, project="p", team="t", notify_policy="queue_drained_only")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T1")
        _enqueue_task(tmp_path, "p", "t", "T1")

        relay_calls: list[tuple] = []
        monkeypatch.setattr(
            brief_mod, "_do_relay_complete_handoff",
            lambda *a, **kw: relay_calls.append(a),
        )

        parser = build_parser()
        rc = cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1", "--actor", "planner@claude",
        ]))
        assert rc == 0
        assert len(relay_calls) == 1


# ---------------------------------------------------------------------------
# MP011 regression: relay must use exact seat id as source, not generic role
# ---------------------------------------------------------------------------

class TestExactPlannerSource:
    """Prevent recurrence of vk006/vk007c generic-source receipt problem.

    Root cause: those receipts were written by manual complete_handoff calls
    (pre-MP007) with --source planner. The automatic hook already resolved the
    exact seat; these tests pin that behaviour and extend it to planner-dispatcher.
    """

    def test_relay_source_is_exact_seat_not_generic_role(self, tmp_path, monkeypatch):
        """The planner seat id passed to _do_relay_complete_handoff must be the
        full seat id (e.g. 'my-team-planner'), not the generic role string 'planner'.
        """
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(
            tmp_path, project="p", team="t",
            planner="my-team-planner",
            notify_policy="queue_drained_only",
        )
        _write_brief_file(tmp_path, project="p", team="t", task_id="T1")
        _enqueue_task(tmp_path, "p", "t", "T1")

        relay_calls: list[tuple] = []
        monkeypatch.setattr(
            brief_mod, "_do_relay_complete_handoff",
            lambda *a, **kw: relay_calls.append(a),
        )

        parser = build_parser()
        cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1",
            "--actor", "planner@claude",
        ]))

        assert len(relay_calls) == 1
        _proj, _team, _task, source = relay_calls[0]
        # Must be the full seat id — not the generic string "planner"
        assert source == "my-team-planner", (
            f"relay used source={source!r}; expected exact seat 'my-team-planner'. "
            "This regression was introduced by VK006/VK007c where manual calls "
            "used --source planner generically."
        )
        assert source != "planner"

    def test_receipt_path_embeds_exact_seat_not_generic_role(self, tmp_path, monkeypatch):
        """_relay_receipt_exists must check for exact-seat receipt, not generic planner receipt."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(
            tmp_path, project="p", team="t",
            planner="exact-planner-seat",
            notify_policy="queue_drained_only",
        )
        _write_brief_file(tmp_path, project="p", team="t", task_id="T1")
        _enqueue_task(tmp_path, "p", "t", "T1")

        relay_calls: list[tuple] = []
        monkeypatch.setattr(
            brief_mod, "_do_relay_complete_handoff",
            lambda *a, **kw: relay_calls.append(a),
        )

        parser = build_parser()
        cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1",
            "--actor", "planner@claude",
        ]))
        assert len(relay_calls) == 1

        # Simulate an exact-seat receipt existing
        receipt_dir = tmp_path / ".agents" / "tasks" / "p" / "patrol" / "handoffs"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        (receipt_dir / "T1__exact-planner-seat__memory.json").write_text("{}", encoding="utf-8")

        # Rerun: should skip because exact-seat receipt exists
        cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T1",
            "--actor", "planner@claude",
        ]))
        # Still only one relay call — exact-seat receipt detected idempotency
        assert len(relay_calls) == 1

        # A generic 'planner' receipt alone must NOT satisfy the idempotency guard
        (receipt_dir / "T1__planner__memory.json").write_text("{}", encoding="utf-8")
        (receipt_dir / "T1__exact-planner-seat__memory.json").unlink()

        relay_calls.clear()
        _enqueue_task(tmp_path, "p", "t", "T2")
        _write_brief_file(tmp_path, project="p", team="t", task_id="T2")
        cmd_done(parser.parse_args([
            "done", "--project", "p", "--team", "t", "--task-id", "T2",
            "--actor", "planner@claude",
        ]))
        # Relay fires again — generic receipt did not satisfy exact-seat guard
        assert len(relay_calls) == 1

    def test_planner_dispatcher_role_resolves_exact_seat(self, tmp_path, monkeypatch):
        """Teams using planner-dispatcher role must also produce exact-seat receipts."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        profiles = tmp_path / ".agents" / "profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / "pd-profile-dynamic.toml").write_text(
            """profile_name = "pd-profile-dynamic"
project_name = "pd"
seats = ["memory", "pd-planner", "pd-builder"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
pd-team = { seats = ["pd-planner", "pd-builder"], notify_policy = "queue_drained_only" }

[seat_roles]
memory = "project-memory"
pd-planner = "planner-dispatcher"
pd-builder = "builder"
""",
            encoding="utf-8",
        )
        _write_brief_file(tmp_path, project="pd", team="pd-team", task_id="T1")
        _enqueue_task(tmp_path, "pd", "pd-team", "T1")

        relay_calls: list[tuple] = []
        monkeypatch.setattr(
            brief_mod, "_do_relay_complete_handoff",
            lambda *a, **kw: relay_calls.append(a),
        )

        parser = build_parser()
        cmd_done(parser.parse_args([
            "done", "--project", "pd", "--team", "pd-team",
            "--task-id", "T1", "--actor", "planner-dispatcher@claude",
        ]))

        assert len(relay_calls) == 1
        _proj, _team, _task, source = relay_calls[0]
        assert source == "pd-planner"
        assert source != "planner"
        assert source != "planner-dispatcher"


# ---------------------------------------------------------------------------
# MP017: _do_relay_complete_handoff subprocess args (relay schema repair)
# ---------------------------------------------------------------------------


class TestDoRelaySubprocessArgs:
    """Verify that _do_relay_complete_handoff passes the required schema args.

    MP017 fixes:
    - --user-summary must be present (satisfies lineage schema, avoids deprecation)
    - --no-notify must be present (relay must not try to wake a stopped session)

    MP021 note: _find_relay_python() is mocked to sys.executable here so the
    tests don't see extra subprocess.run calls from the interpreter-probing logic.
    """

    def _fake_completed_process(self):
        import subprocess
        r = subprocess.CompletedProcess(args=[], returncode=0)
        r.stdout = "completed ok"
        r.stderr = ""
        return r

    def _relay_cmd(self, tmp_path, monkeypatch) -> list[str]:
        """Run _do_relay_complete_handoff with mocked subprocess; return captured cmd."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_profile(tmp_path, project="p", team="t", planner="p-planner")

        captured_args: list[list[str]] = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_args.append(list(cmd))
            return self._fake_completed_process()

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

        # Patch _find_relay_python so no extra subprocess probes fire
        import sys as _sys
        monkeypatch.setattr(brief_mod, "_find_relay_python", lambda: _sys.executable)

        brief_mod._do_relay_complete_handoff("p", "t", "T1", "p-planner")
        assert len(captured_args) == 1, f"expected 1 subprocess call, got {len(captured_args)}"
        return captured_args[0]

    def test_no_notify_in_relay_subprocess_args(self, tmp_path, monkeypatch):
        """--no-notify must be passed so relay succeeds when memory is stopped."""
        cmd = self._relay_cmd(tmp_path, monkeypatch)
        assert "--no-notify" in cmd, f"--no-notify missing from relay args: {cmd}"

    def test_user_summary_in_relay_subprocess_args(self, tmp_path, monkeypatch):
        """--user-summary must be passed to satisfy the lineage schema."""
        cmd = self._relay_cmd(tmp_path, monkeypatch)
        assert "--user-summary" in cmd, f"--user-summary missing from relay args: {cmd}"
        idx = cmd.index("--user-summary")
        assert idx + 1 < len(cmd) and cmd[idx + 1], "user-summary value must be non-empty"

    def test_relay_does_not_pass_notify(self, tmp_path, monkeypatch):
        """Relay must not pass --notify (default-ON would cause session_dead on stopped memory)."""
        cmd = self._relay_cmd(tmp_path, monkeypatch)
        assert "--notify" not in cmd or "--no-notify" in cmd, (
            "--notify passed without --no-notify; would fail on stopped memory session"
        )
