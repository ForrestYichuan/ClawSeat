"""Tests for planner_status_snapshot and cmd_planner_status (MP009).

All tests are unit tests: reads profile + queue state only, no tmux.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))
sys.path.insert(0, str(REPO_ROOT / "core" / "scripts"))

import agent_admin_brief as brief_mod  # noqa: E402
from agent_admin_brief import (  # noqa: E402
    build_parser,
    cmd_planner_status,
    planner_status_snapshot,
    _queue_state_label,
)
from queue_io import append_event  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_multi_profile(home: Path, *, project: str = "p") -> Path:
    profiles = home / ".agents" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    profile = profiles / f"{project}-profile-dynamic.toml"
    profile.write_text(
        f"""profile_name = "{project}-profile-dynamic"
project_name = "{project}"
seats = ["memory", "team-a-planner", "team-a-builder", "team-b-planner", "team-c-dispatcher", "qd-planner"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
team-a = {{ seats = ["team-a-planner", "team-a-builder"], notify_policy = "queue_drained_only" }}
team-b = {{ seats = ["team-b-planner"], notify_policy = "queue_drained_only" }}
team-c = {{ seats = ["team-c-dispatcher"], notify_policy = "queue_drained_only" }}
quality-docs = {{ seats = ["qd-planner"], notify_policy = "never_notify_memory", team_type = "quality-docs" }}
no-planner-team = {{ seats = ["team-a-builder"] }}

[seat_roles]
memory = "project-memory"
team-a-planner = "planner"
team-a-builder = "builder"
team-b-planner = "planner"
team-c-dispatcher = "planner-dispatcher"
qd-planner = "planner"

[seat_overrides.team-a-planner]
tool = "claude"
auth_mode = "oauth_token"
provider = "anthropic"

[seat_overrides.team-b-planner]
tool = "codex"
model = "o3"
auth_mode = "oauth"
provider = "openai"

[seat_overrides.team-c-dispatcher]
tool = "claude"
model = "sonnet"
auth_mode = "oauth"
provider = "anthropic"

[seat_overrides.qd-planner]
tool = "claude"
auth_mode = "api"
provider = "minimax"
""",
        encoding="utf-8",
    )
    return profile


def _write_project_toml_with_active_roster(home: Path, *, project: str = "p") -> Path:
    project_dir = home / ".agents" / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / "project.toml"
    path.write_text(
        f"""version = 1
name = "{project}"
repo_root = "/tmp/repo"
engineers = ["memory", "team-a-planner", "team-c-dispatcher"]

[seat_overrides.memory]
tool = "claude"
auth_mode = "api"
provider = "minimax"

[seat_overrides.team-a-planner]
tool = "claude"
auth_mode = "api"
provider = "baidu-glm"
role = "planner-dispatcher"
team = "team-a"

[seat_overrides.team-c-dispatcher]
tool = "codex"
auth_mode = "oauth"
provider = "openai"
role = "planner-dispatcher"
team = "team-c"
""",
        encoding="utf-8",
    )
    return path


def _queue_path(home: Path, project: str, team: str) -> Path:
    return home / ".agents" / "tasks" / project / team / "tasks.queue.jsonl"


def _seed_queue(home: Path, project: str, team: str, events: list[dict]) -> Path:
    q = _queue_path(home, project, team)
    q.parent.mkdir(parents=True, exist_ok=True)
    for ev in events:
        append_event(q, ev)
    return q


def _write_session_runtime(
    home: Path,
    *,
    project: str,
    seat: str,
    session: str,
    tool: str = "claude",
    auth_mode: str = "oauth_token",
    provider: str = "anthropic",
) -> Path:
    session_dir = home / ".agents" / "sessions" / project / seat
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "session.toml"
    path.write_text(
        f"""version = 1
project = "{project}"
engineer_id = "{seat}"
tool = "{tool}"
auth_mode = "{auth_mode}"
provider = "{provider}"
session = "{session}"
""",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

class TestQueueStateLabel:
    def test_empty(self):
        assert _queue_state_label({}) == "empty"

    def test_drained(self):
        from queue_io import TaskState
        tasks = {
            "T1": TaskState(task_id="T1", status="task_done", last_seq=3,
                            last_event_ts="2026-01-01T00:00:00+00:00", actor="planner@claude"),
        }
        assert _queue_state_label(tasks) == "drained"

    def test_active_in_progress(self):
        from queue_io import TaskState
        tasks = {
            "T1": TaskState(task_id="T1", status="task_in_progress", last_seq=2,
                            last_event_ts="2026-01-01T00:00:00+00:00", actor="planner@claude"),
        }
        assert _queue_state_label(tasks) == "active"

    def test_claimed(self):
        from queue_io import TaskState
        tasks = {
            "T1": TaskState(task_id="T1", status="task_claimed", last_seq=2,
                            last_event_ts="2026-01-01T00:00:00+00:00", actor="planner@claude"),
        }
        assert _queue_state_label(tasks) == "claimed"

    def test_waiting_task_created(self):
        from queue_io import TaskState
        tasks = {
            "T1": TaskState(task_id="T1", status="task_created", last_seq=1,
                            last_event_ts="2026-01-01T00:00:00+00:00", actor="memory"),
        }
        assert _queue_state_label(tasks) == "waiting"

    def test_waiting_for_upstream(self):
        from queue_io import TaskState
        tasks = {
            "T1": TaskState(task_id="T1", status="task_waiting_for", last_seq=2,
                            last_event_ts="2026-01-01T00:00:00+00:00", actor="planner@claude"),
        }
        assert _queue_state_label(tasks) == "waiting"

    def test_active_wins_over_claimed(self):
        from queue_io import TaskState
        tasks = {
            "T1": TaskState(task_id="T1", status="task_claimed", last_seq=2,
                            last_event_ts="2026-01-01T00:00:00+00:00", actor="planner@claude"),
            "T2": TaskState(task_id="T2", status="task_in_progress", last_seq=3,
                            last_event_ts="2026-01-01T00:01:00+00:00", actor="planner@claude"),
        }
        assert _queue_state_label(tasks) == "active"

    def test_failed_task_reports_blocked(self):
        from queue_io import TaskState
        tasks = {
            "T1": TaskState(task_id="T1", status="task_failed", last_seq=4,
                            last_event_ts="2026-01-01T00:00:00+00:00", actor="planner@claude"),
        }
        assert _queue_state_label(tasks) == "blocked"

    def test_bounced_task_reports_blocked(self):
        from queue_io import TaskState
        tasks = {
            "T1": TaskState(task_id="T1", status="task_bounced", last_seq=4,
                            last_event_ts="2026-01-01T00:00:00+00:00", actor="planner@claude"),
        }
        assert _queue_state_label(tasks) == "blocked"

    def test_reset_task_reports_blocked(self):
        from queue_io import TaskState
        tasks = {
            "T1": TaskState(task_id="T1", status="task_reset", last_seq=4,
                            last_event_ts="2026-01-01T00:00:00+00:00", actor="operator"),
        }
        assert _queue_state_label(tasks) == "blocked"


# ---------------------------------------------------------------------------
# planner_status_snapshot integration tests
# ---------------------------------------------------------------------------

class TestPlannerStatusSnapshot:
    def test_returns_all_planner_teams(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        rows = planner_status_snapshot("p")
        teams = {r["team"] for r in rows}
        assert "team-a" in teams
        assert "team-b" in teams
        assert "team-c" in teams
        assert "quality-docs" in teams
        # no-planner-team has no planner seat — excluded
        assert "no-planner-team" not in teams

    def test_planner_seat_and_tool_resolved(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        rows = planner_status_snapshot("p")
        team_a = next(r for r in rows if r["team"] == "team-a")
        assert team_a["planner_seat"] == "team-a-planner"
        assert team_a["tool"] == "claude"

    def test_tool_and_model_for_codex_seat(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        rows = planner_status_snapshot("p")
        team_b = next(r for r in rows if r["team"] == "team-b")
        assert team_b["planner_seat"] == "team-b-planner"
        assert team_b["tool"] == "codex"
        assert team_b["model"] == "o3"

    def test_planner_dispatcher_team_is_included(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        rows = planner_status_snapshot("p")
        team_c = next(r for r in rows if r["team"] == "team-c")
        assert team_c["planner_seat"] == "team-c-dispatcher"
        assert team_c["tool"] == "claude"
        assert team_c["model"] == "sonnet"

    def test_empty_queue_reports_empty_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        rows = planner_status_snapshot("p")
        team_a = next(r for r in rows if r["team"] == "team-a")
        assert team_a["queue_state"] == "empty"
        assert team_a["task_count"] == 0
        assert team_a["latest_task_id"] is None

    def test_drained_queue_reports_drained(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        _seed_queue(tmp_path, "p", "team-a", [
            {"event_type": "task_created", "actor": "memory", "task_id": "T1",
             "brief_path": "b/T1.md", "parent_task_id": None, "depends_on": []},
            {"event_type": "task_claimed", "actor": "planner@claude", "task_id": "T1"},
            {"event_type": "task_in_progress", "actor": "planner@claude", "task_id": "T1"},
            {"event_type": "task_done", "actor": "planner@claude", "task_id": "T1", "verdict": "PASS"},
        ])
        rows = planner_status_snapshot("p")
        team_a = next(r for r in rows if r["team"] == "team-a")
        assert team_a["queue_state"] == "drained"

    def test_session_liveness_reports_dead_planner(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        _write_session_runtime(
            tmp_path,
            project="p",
            seat="team-a-planner",
            session="p-team-a-planner-claude",
        )
        _seed_queue(tmp_path, "p", "team-a", [
            {"event_type": "task_created", "actor": "memory", "task_id": "T1",
             "brief_path": "b/T1.md", "parent_task_id": None, "depends_on": []},
            {"event_type": "task_claimed", "actor": "planner@claude", "task_id": "T1"},
            {"event_type": "task_in_progress", "actor": "planner@claude", "task_id": "T1"},
            {"event_type": "task_done", "actor": "planner@claude", "task_id": "T1", "verdict": "PASS"},
        ])

        class Result:
            returncode = 1
            stdout = ""
            stderr = ""

        monkeypatch.setattr(brief_mod.subprocess, "run", lambda *a, **kw: Result())

        rows = planner_status_snapshot("p")
        team_a = next(r for r in rows if r["team"] == "team-a")
        assert team_a["session_name"] == "p-team-a-planner-claude"
        assert team_a["session_status"] == "dead"
        assert team_a["task_count"] == 1
        assert team_a["latest_task_id"] == "T1"
        assert team_a["latest_task_status"] == "task_done"

    def test_claimed_task_reports_claimed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        _seed_queue(tmp_path, "p", "team-a", [
            {"event_type": "task_created", "actor": "memory", "task_id": "T1",
             "brief_path": "b/T1.md", "parent_task_id": None, "depends_on": []},
            {"event_type": "task_claimed", "actor": "planner@claude", "task_id": "T1"},
        ])
        rows = planner_status_snapshot("p")
        team_a = next(r for r in rows if r["team"] == "team-a")
        assert team_a["queue_state"] == "claimed"
        assert team_a["latest_task_status"] == "task_claimed"
        assert team_a["attention_task_id"] == "T1"
        assert team_a["attention_task_status"] == "task_claimed"
        assert team_a["attention_reason"] == "claimed_by=planner@claude"

    def test_waiting_task_reports_waiting(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        _seed_queue(tmp_path, "p", "team-b", [
            {"event_type": "task_created", "actor": "memory", "task_id": "T2",
             "brief_path": "b/T2.md", "parent_task_id": None, "depends_on": []},
        ])
        rows = planner_status_snapshot("p")
        team_b = next(r for r in rows if r["team"] == "team-b")
        assert team_b["queue_state"] == "waiting"
        assert team_b["latest_task_status"] == "task_created"

    def test_failed_task_reports_blocked_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        _seed_queue(tmp_path, "p", "team-a", [
            {"event_type": "task_created", "actor": "memory", "task_id": "T1",
             "brief_path": "b/T1.md", "parent_task_id": None, "depends_on": []},
            {"event_type": "task_claimed", "actor": "planner@claude", "task_id": "T1"},
            {"event_type": "task_in_progress", "actor": "planner@claude", "task_id": "T1"},
            {"event_type": "task_failed", "actor": "planner@claude", "task_id": "T1",
             "verdict": "FAIL", "fail_reason": "mechanical failed"},
        ])
        rows = planner_status_snapshot("p")
        team_a = next(r for r in rows if r["team"] == "team-a")
        assert team_a["queue_state"] == "blocked"
        assert team_a["latest_task_status"] == "task_failed"
        assert team_a["attention_task_id"] == "T1"
        assert team_a["attention_reason"] == "mechanical failed"

    def test_reset_task_reports_attention_reason(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        _seed_queue(tmp_path, "p", "team-a", [
            {"event_type": "task_created", "actor": "memory", "task_id": "T1",
             "brief_path": "b/T1.md", "parent_task_id": None, "depends_on": []},
            {"event_type": "task_reset", "actor": "operator", "task_id": "T1",
             "reset_reason": "acceptance criteria repaired"},
        ])
        rows = planner_status_snapshot("p")
        team_a = next(r for r in rows if r["team"] == "team-a")
        assert team_a["queue_state"] == "blocked"
        assert team_a["attention_task_id"] == "T1"
        assert team_a["attention_task_status"] == "task_reset"
        assert team_a["attention_reason"] == "acceptance criteria repaired"
        assert team_a["attention_next_step"] == (
            "agent_admin.py brief requeue --project p --team team-a --task-id T1"
        )

    def test_notify_policy_reported(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        rows = planner_status_snapshot("p")
        qd = next(r for r in rows if r["team"] == "quality-docs")
        assert qd["notify_policy"] == "never_notify_memory"
        assert qd["team_type"] == "quality-docs"

    def test_missing_profile_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        rows = planner_status_snapshot("no-such-project")
        assert rows == []


# ---------------------------------------------------------------------------
# cmd_planner_status CLI tests
# ---------------------------------------------------------------------------

class TestCmdPlannerStatus:
    def test_plain_text_output_contains_team_and_state(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        _seed_queue(tmp_path, "p", "team-a", [
            {"event_type": "task_created", "actor": "memory", "task_id": "T1",
             "brief_path": "b/T1.md", "parent_task_id": None, "depends_on": []},
        ])
        parser = build_parser()
        rc = cmd_planner_status(
            parser.parse_args(["planner-status", "--project", "p"])
        )
        out = capsys.readouterr()
        assert rc == 0
        assert "team-a" in out.out
        assert "team-a-planner" in out.out
        assert "waiting" in out.out
        assert "attention=T1[task_created]: unclaimed" in out.out

    def test_json_output_is_parseable_list(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        parser = build_parser()
        rc = cmd_planner_status(
            parser.parse_args(["planner-status", "--project", "p", "--json"])
        )
        out = capsys.readouterr()
        assert rc == 0
        data = json.loads(out.out)
        assert isinstance(data, list)
        assert any(r["team"] == "team-a" for r in data)
        assert all("queue_state" in r for r in data)

    def test_json_output_includes_required_fields(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        parser = build_parser()
        cmd_planner_status(
            parser.parse_args(["planner-status", "--project", "p", "--json"])
        )
        out = capsys.readouterr()
        data = json.loads(out.out)
        required_fields = {
            "team", "planner_seat", "tool", "model", "provider", "auth_mode",
            "notify_policy", "team_type", "queue_state", "task_count",
            "latest_task_id", "latest_task_status", "latest_task_ts",
        }
        for row in data:
            assert required_fields <= row.keys(), f"missing fields in {row}"

    def test_no_project_returns_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        parser = build_parser()
        rc = cmd_planner_status(
            parser.parse_args(["planner-status", "--project", "ghost"])
        )
        out = capsys.readouterr()
        assert rc == 0
        assert "no planner teams" in out.out

    def test_uses_profile_and_queue_not_pane_text(self, tmp_path, monkeypatch):
        """Snapshot must work even when TMUX is unset — no pane reads allowed."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        monkeypatch.delenv("TMUX", raising=False)
        _write_multi_profile(tmp_path, project="p")
        # Should not raise, not attempt tmux, return rows based on profile+queue
        rows = planner_status_snapshot("p")
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# MP013: provider / auth_mode / model snapshot tests
# ---------------------------------------------------------------------------

class TestProviderAuthSnapshot:
    """planner_status_snapshot exposes provider/auth/model as informational metadata."""

    def test_snapshot_includes_provider_field(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        rows = planner_status_snapshot("p")
        team_a = next(r for r in rows if r["team"] == "team-a")
        assert "provider" in team_a
        assert team_a["provider"] == "anthropic"

    def test_snapshot_includes_auth_mode_field(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        rows = planner_status_snapshot("p")
        team_a = next(r for r in rows if r["team"] == "team-a")
        assert "auth_mode" in team_a
        assert team_a["auth_mode"] == "oauth_token"

    def test_snapshot_provider_and_auth_for_codex_seat(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        rows = planner_status_snapshot("p")
        team_b = next(r for r in rows if r["team"] == "team-b")
        assert team_b["provider"] == "openai"
        assert team_b["auth_mode"] == "oauth"

    def test_model_field_present_and_explicit_when_unconfigured(self, tmp_path, monkeypatch):
        """model field must exist in every row; 'unknown' placeholder when unconfigured."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        rows = planner_status_snapshot("p")
        for row in rows:
            assert "model" in row, f"missing model in {row['team']}"
            assert row["model"], f"model must not be empty string in {row['team']}"
        team_a = next(r for r in rows if r["team"] == "team-a")
        assert team_a["model"] == "unknown"  # team-a has no model configured

    def test_model_non_empty_when_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        rows = planner_status_snapshot("p")
        team_b = next(r for r in rows if r["team"] == "team-b")
        assert team_b["model"] == "o3"

    def test_plain_text_includes_provider_in_tool_label(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        parser = build_parser()
        cmd_planner_status(parser.parse_args(["planner-status", "--project", "p"]))
        out = capsys.readouterr()
        # Provider appears in tool label parenthetical
        assert "anthropic" in out.out, f"provider 'anthropic' not in text output: {out.out!r}"
        assert "oauth_token" in out.out, f"auth_mode 'oauth_token' not in text output: {out.out!r}"

    def test_plain_text_includes_model_in_tool_label(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        parser = build_parser()
        cmd_planner_status(parser.parse_args(["planner-status", "--project", "p"]))
        out = capsys.readouterr()
        # team-b has model=o3
        assert "o3" in out.out, f"model 'o3' not in text output: {out.out!r}"

    def test_provider_auth_are_informational_not_readiness_blockers(self, tmp_path, monkeypatch, capsys):
        """Missing provider/auth must not cause an error — just empty strings."""
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        profiles = tmp_path / ".agents" / "profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / "min-profile-dynamic.toml").write_text(
            """profile_name = "min-profile-dynamic"
project_name = "min"
seats = ["memory", "min-planner"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
min-team = { seats = ["min-planner"], notify_policy = "queue_drained_only" }

[seat_roles]
memory = "project-memory"
min-planner = "planner"

[seat_overrides.min-planner]
tool = "claude"
""",
            encoding="utf-8",
        )
        rows = planner_status_snapshot("min")
        assert len(rows) == 1
        row = rows[0]
        # provider/auth absent → empty string (no guess)
        assert row["provider"] == ""
        assert row["auth_mode"] == ""
        # model absent → explicit "unknown" placeholder, never empty string
        assert row["model"] == "unknown"
        assert row["tool"] == "claude"

    def test_project_toml_roster_and_overrides_are_status_ssot(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        _write_project_toml_with_active_roster(tmp_path, project="p")

        rows = planner_status_snapshot("p")
        by_team = {row["team"]: row for row in rows}

        assert set(by_team) == {"team-a", "team-c"}
        assert by_team["team-a"]["planner_seat"] == "team-a-planner"
        assert by_team["team-a"]["provider"] == "baidu-glm"
        assert by_team["team-a"]["auth_mode"] == "api"
        assert by_team["team-c"]["tool"] == "codex"
        assert by_team["team-c"]["provider"] == "openai"

    def test_json_output_includes_provider_auth_model(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
        _write_multi_profile(tmp_path, project="p")
        parser = build_parser()
        cmd_planner_status(parser.parse_args(["planner-status", "--project", "p", "--json"]))
        out = capsys.readouterr()
        data = json.loads(out.out)
        for row in data:
            assert "provider" in row, f"provider missing from JSON row {row['team']}"
            assert "auth_mode" in row, f"auth_mode missing from JSON row {row['team']}"
            assert "model" in row, f"model missing from JSON row {row['team']}"
