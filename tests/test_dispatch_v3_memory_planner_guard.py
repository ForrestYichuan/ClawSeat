from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "core" / "skills" / "gstack-harness" / "scripts"


def _v3_profile(tmp_path: Path) -> tuple[Path, Path]:
    tasks = tmp_path / "tasks"
    planner_todo = tasks / "product-planner" / "TODO.md"
    planner_todo.parent.mkdir(parents=True)
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    ws = tmp_path / "workspaces"
    ws.mkdir()

    profile = tmp_path / "profile.toml"
    profile.write_text(
        f"""\
profile_name = "test-profile"
template_name = "clawseat-minimal"
project_name = "test-project"
repo_root = "{tmp_path}"
tasks_root = "{tasks}"
workspace_root = "{ws}"
handoff_dir = "{handoffs}"
project_doc = "{tasks}/PROJECT.md"
tasks_doc = "{tasks}/TASKS.md"
status_doc = "{tasks}/STATUS.md"
send_script = "/bin/echo"
status_script = "/bin/echo"
patrol_script = "/bin/echo"
agent_admin = "{REPO}/core/scripts/agent_admin.py"
heartbeat_receipt = "{ws}/memory/HEARTBEAT_RECEIPT.toml"
seats = ["memory", "product-planner"]
heartbeat_seats = []
active_loop_owner = "memory"
default_notify_target = "memory"
heartbeat_owner = "memory"

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
product = {{ seats = ["product-planner"], team_type = "subteam", notify_policy = "queue_drained_only" }}

[seat_roles]
memory = "project-memory"
product-planner = "planner"
""",
        encoding="utf-8",
    )
    return profile, planner_todo


def test_dispatch_task_rejects_v3_memory_to_planner_split_brain(tmp_path: Path) -> None:
    profile, planner_todo = _v3_profile(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "dispatch_task.py"),
            "--profile",
            str(profile),
            "--source",
            "memory",
            "--target",
            "product-planner",
            "--task-id",
            "vk019-vault-task-chain-dynamic-docs-20260520",
            "--title",
            "Vault task chain dynamic docs",
            "--objective",
            "Make task-chain dynamic docs browsable in Vault.",
            "--test-policy",
            "EXTEND",
            "--reply-to",
            "memory",
            "--no-notify",
        ],
        capture_output=True,
        text=True,
        cwd=str(SCRIPTS),
    )

    assert result.returncode == 2
    assert "v3 planner-target dispatch must use agent_admin.py brief queue" in result.stderr
    assert "brief queue --project test-project" in result.stderr
    assert not planner_todo.exists()


def test_dispatch_task_rejects_v3_generic_planner_source_to_planner(tmp_path: Path) -> None:
    profile, planner_todo = _v3_profile(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "dispatch_task.py"),
            "--profile",
            str(profile),
            "--source",
            "planner",
            "--target",
            "product-planner",
            "--task-id",
            "cu018-solo-tui-auto-grid-20260520",
            "--title",
            "Solo TUI auto grid",
            "--objective",
            "Open newly created solo TUI sessions in the PTY grid.",
            "--test-policy",
            "EXTEND",
            "--reply-to",
            "planner",
            "--no-notify",
        ],
        capture_output=True,
        text=True,
        cwd=str(SCRIPTS),
    )

    assert result.returncode == 2
    assert "v3 planner-target dispatch must use agent_admin.py brief queue" in result.stderr
    assert "generic planner aliases" in result.stderr
    assert "brief queue --project test-project" in result.stderr
    assert not planner_todo.exists()
