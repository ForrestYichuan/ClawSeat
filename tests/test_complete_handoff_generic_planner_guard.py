"""Guard against generic --source planner in multi-team projects (MP012).

Root cause: VK006/VK007c produced receipts as <task_id>__planner__memory.json
because manual complete_handoff calls used --source planner before the
queue-drained relay was automated. In multi-team projects this is ambiguous.

Guard behaviour (added in MP012):
- Multiple exact planner seats in profile → SystemExit with exact seat list.
- Single exact planner seat → normalise to that seat (info printed to stderr).
- Legacy seat named "planner" (zero non-generic exact seats) → unchanged;
  existing self-closeout guard handles it.
- Exact planner source → passes through without any guard intervention.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "skills" / "gstack-harness" / "scripts"
_COMPLETE_HANDOFF = _SCRIPTS / "complete_handoff.py"


# ---------------------------------------------------------------------------
# Synthetic profile helpers
# ---------------------------------------------------------------------------

def _base_profile_fields(tmp_path: Path) -> dict[str, str]:
    tasks = tmp_path / "tasks"
    handoffs = tmp_path / "handoffs"
    workspaces = tmp_path / "workspaces"
    for d in (tasks, handoffs, workspaces):
        d.mkdir(parents=True, exist_ok=True)
    (tasks / "TASKS.md").write_text("", encoding="utf-8")
    return {
        "tasks": str(tasks),
        "handoffs": str(handoffs),
        "workspaces": str(workspaces),
    }


def _write_multi_planner_profile(tmp_path: Path) -> Path:
    """Profile with multiple exact planner seats — simulates multi-team project."""
    dirs = _base_profile_fields(tmp_path)
    profile = tmp_path / "multi-planner.toml"
    profile.write_text(
        f"""\
version = 1
profile_name = "multi-planner-test"
project_name = "cartooner-front"
template_name = "gstack-harness"
repo_root = "{tmp_path}"
tasks_root = "{dirs['tasks']}"
workspace_root = "{dirs['workspaces']}"
handoff_dir = "{dirs['handoffs']}"
project_doc = "{dirs['tasks']}/PROJECT.md"
tasks_doc = "{dirs['tasks']}/TASKS.md"
status_doc = "{dirs['tasks']}/STATUS.md"
send_script = "/bin/echo"
status_script = "/bin/echo"
patrol_script = "/bin/echo"
agent_admin = "/bin/echo"
heartbeat_receipt = "{dirs['workspaces']}/koder/HEARTBEAT_RECEIPT.toml"
heartbeat_transport = "tmux"
default_notify_target = "memory"
heartbeat_owner = "koder"
heartbeat_seats = []
active_loop_owner = "cartooner-product-glm-planner"
seats = [
  "memory",
  "cartooner-product-glm-planner",
  "cartooner-ui-polish-planner",
  "cartooner-product2-planner",
]

[seat_roles]
memory = "project-memory"
cartooner-product-glm-planner = "planner"
cartooner-ui-polish-planner = "planner"
cartooner-product2-planner = "planner"

[dynamic_roster]
materialized_seats = [
  "memory",
  "cartooner-product-glm-planner",
  "cartooner-ui-polish-planner",
  "cartooner-product2-planner",
]
""",
        encoding="utf-8",
    )
    return profile


def _write_single_exact_planner_profile(tmp_path: Path) -> Path:
    """Profile with exactly one non-generic planner seat — unambiguous normalization."""
    dirs = _base_profile_fields(tmp_path)
    profile = tmp_path / "single-exact-planner.toml"
    profile.write_text(
        f"""\
version = 1
profile_name = "single-exact-planner-test"
project_name = "cartooner-solo"
template_name = "gstack-harness"
repo_root = "{tmp_path}"
tasks_root = "{dirs['tasks']}"
workspace_root = "{dirs['workspaces']}"
handoff_dir = "{dirs['handoffs']}"
project_doc = "{dirs['tasks']}/PROJECT.md"
tasks_doc = "{dirs['tasks']}/TASKS.md"
status_doc = "{dirs['tasks']}/STATUS.md"
send_script = "/bin/echo"
status_script = "/bin/echo"
patrol_script = "/bin/echo"
agent_admin = "/bin/echo"
heartbeat_receipt = "{dirs['workspaces']}/koder/HEARTBEAT_RECEIPT.toml"
heartbeat_transport = "tmux"
default_notify_target = "memory"
heartbeat_owner = "koder"
heartbeat_seats = []
active_loop_owner = "solo-planner"
seats = ["memory", "solo-planner"]

[seat_roles]
memory = "project-memory"
solo-planner = "planner"

[dynamic_roster]
materialized_seats = ["memory", "solo-planner"]
""",
        encoding="utf-8",
    )
    return profile


def _write_legacy_single_team_profile(tmp_path: Path) -> Path:
    """Legacy profile where the seat is literally named 'planner' (zero exact seats)."""
    dirs = _base_profile_fields(tmp_path)
    profile = tmp_path / "legacy-single-team.toml"
    profile.write_text(
        f"""\
version = 1
profile_name = "legacy-single-team-test"
project_name = "legacy"
template_name = "gstack-harness"
repo_root = "{tmp_path}"
tasks_root = "{dirs['tasks']}"
workspace_root = "{dirs['workspaces']}"
handoff_dir = "{dirs['handoffs']}"
project_doc = "{dirs['tasks']}/PROJECT.md"
tasks_doc = "{dirs['tasks']}/TASKS.md"
status_doc = "{dirs['tasks']}/STATUS.md"
send_script = "/bin/echo"
status_script = "/bin/echo"
patrol_script = "/bin/echo"
agent_admin = "/bin/echo"
heartbeat_receipt = "{dirs['workspaces']}/koder/HEARTBEAT_RECEIPT.toml"
heartbeat_transport = "tmux"
default_notify_target = "planner"
heartbeat_owner = "koder"
heartbeat_seats = []
active_loop_owner = "planner"
seats = ["planner", "builder"]

[seat_roles]
planner = "planner-dispatcher"
builder = "builder"

[dynamic_roster]
materialized_seats = ["planner", "builder"]
""",
        encoding="utf-8",
    )
    return profile


def _run(profile: Path, *, source: str, target: str = "memory") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, str(_COMPLETE_HANDOFF),
            "--profile", str(profile),
            "--source", source,
            "--target", target,
            "--task-id", "guard-probe-task",
            "--title", "guard probe",
            "--summary", "probe for generic planner source guard",
        ],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Tests: multi-team ambiguous → error
# ---------------------------------------------------------------------------

class TestGenericPlannerSourceGuard:
    def test_multi_team_generic_planner_is_rejected(self, tmp_path):
        """--source planner in multi-team project must be rejected with exact seat list."""
        profile = _write_multi_planner_profile(tmp_path)
        proc = _run(profile, source="planner")

        assert proc.returncode != 0, (
            f"--source planner with multiple planner seats must fail; got rc={proc.returncode}"
        )
        combined = proc.stderr + proc.stdout
        assert "ambiguous" in combined.lower() or "exact planner" in combined.lower(), (
            f"error must mention ambiguity; got: {combined!r}"
        )
        # Error must name at least one exact planner seat
        assert "cartooner-" in combined, (
            f"error must list at least one exact planner seat; got: {combined!r}"
        )

    def test_multi_team_generic_planner_dispatcher_is_rejected(self, tmp_path):
        """--source planner-dispatcher is equally ambiguous in multi-team."""
        # Profile with planner-dispatcher roles
        dirs = {}
        tasks = tmp_path / "tasks2"
        handoffs = tmp_path / "handoffs2"
        workspaces = tmp_path / "workspaces2"
        for d in (tasks, handoffs, workspaces):
            d.mkdir(parents=True, exist_ok=True)
        (tasks / "TASKS.md").write_text("", encoding="utf-8")
        profile = tmp_path / "multi-pd.toml"
        profile.write_text(
            f"""\
version = 1
profile_name = "multi-pd-test"
project_name = "cartooner-front"
template_name = "gstack-harness"
repo_root = "{tmp_path}"
tasks_root = "{tasks}"
workspace_root = "{workspaces}"
handoff_dir = "{handoffs}"
project_doc = "{tasks}/PROJECT.md"
tasks_doc = "{tasks}/TASKS.md"
status_doc = "{tasks}/STATUS.md"
send_script = "/bin/echo"
status_script = "/bin/echo"
patrol_script = "/bin/echo"
agent_admin = "/bin/echo"
heartbeat_receipt = "{workspaces}/koder/HEARTBEAT_RECEIPT.toml"
heartbeat_transport = "tmux"
default_notify_target = "memory"
heartbeat_owner = "koder"
heartbeat_seats = []
active_loop_owner = "team-a-planner"
seats = ["memory", "team-a-planner", "team-b-planner"]

[seat_roles]
memory = "project-memory"
team-a-planner = "planner-dispatcher"
team-b-planner = "planner-dispatcher"

[dynamic_roster]
materialized_seats = ["memory", "team-a-planner", "team-b-planner"]
""",
            encoding="utf-8",
        )

        proc = _run(profile, source="planner-dispatcher")
        assert proc.returncode != 0, (
            f"--source planner-dispatcher with multiple seats must fail; rc={proc.returncode}"
        )
        combined = proc.stderr + proc.stdout
        assert "team-a-planner" in combined or "team-b-planner" in combined, (
            f"error must list exact seats; got: {combined!r}"
        )

    def test_multi_team_error_lists_all_exact_seats(self, tmp_path):
        """Error message must list ALL exact planner seats, not just one."""
        profile = _write_multi_planner_profile(tmp_path)
        proc = _run(profile, source="planner")

        combined = proc.stderr + proc.stdout
        # All three planner seats should appear in the error
        for seat in (
            "cartooner-product-glm-planner",
            "cartooner-ui-polish-planner",
            "cartooner-product2-planner",
        ):
            assert seat in combined, (
                f"error must list {seat!r}; got: {combined!r}"
            )

    def test_multi_team_generic_planner_target_is_rejected(self, tmp_path):
        """--target planner in multi-team project must be rejected with exact seat list."""
        profile = _write_multi_planner_profile(tmp_path)
        proc = _run(
            profile,
            source="cartooner-product-glm-planner",
            target="planner",
        )

        assert proc.returncode != 0, (
            f"--target planner with multiple planner seats must fail; got rc={proc.returncode}"
        )
        combined = proc.stderr + proc.stdout
        assert "generic --target 'planner' is ambiguous" in combined, (
            f"error must mention generic target ambiguity; got: {combined!r}"
        )
        assert "route chain-end relay to memory" in combined, (
            f"error must point chain-end relays at memory; got: {combined!r}"
        )
        for seat in (
            "cartooner-product-glm-planner",
            "cartooner-ui-polish-planner",
            "cartooner-product2-planner",
        ):
            assert seat in combined, (
                f"error must list {seat!r}; got: {combined!r}"
            )

    # ---------------------------------------------------------------------------
    # Tests: single exact planner → normalise
    # ---------------------------------------------------------------------------

    def test_single_exact_planner_is_normalised(self, tmp_path):
        """--source planner with exactly one non-generic planner seat is normalised."""
        profile = _write_single_exact_planner_profile(tmp_path)
        proc = _run(profile, source="planner")

        # Must NOT be rejected with the ambiguous error
        combined = proc.stderr + proc.stdout
        assert "ambiguous" not in combined.lower(), (
            f"single exact planner must not be rejected as ambiguous; got: {combined!r}"
        )
        # Info message must mention normalization and exact seat
        assert "normalized" in combined.lower() or "solo-planner" in combined, (
            f"info message about normalization expected; got: {combined!r}"
        )

    def test_single_exact_planner_info_names_exact_seat(self, tmp_path):
        """Normalization info message must name the resolved exact seat."""
        profile = _write_single_exact_planner_profile(tmp_path)
        proc = _run(profile, source="planner")

        # The stderr info line should contain the exact seat id
        assert "solo-planner" in proc.stderr, (
            f"info message must name exact seat 'solo-planner'; stderr: {proc.stderr!r}"
        )

    def test_single_exact_planner_target_is_normalised(self, tmp_path):
        """--target planner with exactly one non-generic planner seat is normalised."""
        profile = _write_single_exact_planner_profile(tmp_path)
        proc = _run(profile, source="memory", target="planner")

        combined = proc.stderr + proc.stdout
        assert "ambiguous" not in combined.lower(), (
            f"single exact planner target must not be rejected; got: {combined!r}"
        )
        assert "normalized generic --target 'planner' to exact planner seat 'solo-planner'" in combined, (
            f"target normalization info expected; got: {combined!r}"
        )

    # ---------------------------------------------------------------------------
    # Tests: legacy single-team (seat named "planner") → unchanged
    # ---------------------------------------------------------------------------

    def test_legacy_single_team_generic_source_not_blocked(self, tmp_path):
        """Legacy profile where seat is literally 'planner' must not be rejected."""
        profile = _write_legacy_single_team_profile(tmp_path)
        proc = _run(profile, source="planner")

        combined = proc.stderr + proc.stdout
        # Must NOT get the ambiguous-seat error
        assert "ambiguous" not in combined.lower(), (
            f"legacy single-team must not be blocked by multi-team guard; got: {combined!r}"
        )
        # May get the existing self-closeout warning or proceed — both are acceptable
        assert proc.returncode in (0, 1), (
            f"legacy single-team: unexpected exit code {proc.returncode}"
        )

    # ---------------------------------------------------------------------------
    # Tests: exact source → passes through unchanged
    # ---------------------------------------------------------------------------

    def test_exact_planner_source_not_intercepted(self, tmp_path):
        """Exact planner seat name must not be altered by the guard."""
        profile = _write_multi_planner_profile(tmp_path)
        # Using an exact seat — guard must not fire
        proc = _run(profile, source="cartooner-product-glm-planner")

        combined = proc.stderr + proc.stdout
        assert "ambiguous" not in combined.lower(), (
            f"exact source must not trigger ambiguous guard; got: {combined!r}"
        )
        assert "normalized" not in combined.lower(), (
            f"exact source must not be 'normalized'; got: {combined!r}"
        )
