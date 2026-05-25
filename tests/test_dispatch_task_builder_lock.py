from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_complete_handoff import _dispatch, _write_profile


ROOT = Path(__file__).resolve().parents[1]
DISPATCH_SCRIPT = ROOT / "core" / "skills" / "gstack-harness" / "scripts" / "dispatch_task.py"


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-q", "-m", "init"], check=True)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/clawseat/main", head], check=True)
    return repo


def _dispatch_with_force(
    profile: Path,
    task_id: str,
    *,
    target: str = "builder",
    force_parallel_builder: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(DISPATCH_SCRIPT),
        "--profile",
        str(profile),
        "--source",
        "planner",
        "--target",
        target,
        "--task-id",
        task_id,
        "--title",
        task_id,
        "--objective",
        "objective",
        "--test-policy",
        "N/A",
        "--reply-to",
        "planner",
        "--no-notify",
    ]
    if force_parallel_builder:
        cmd.append("--force-parallel-builder")
    return subprocess.run(cmd, capture_output=True, text=True)


def _write_multi_builder_profile(tmp_path: Path, repo_root: Path) -> tuple[Path, Path, Path]:
    tasks = tmp_path / "tasks"
    handoffs = tmp_path / "handoffs"
    workspaces = tmp_path / "workspaces"
    for seat in ("planner", "builder-a", "builder-b"):
        (tasks / seat).mkdir(parents=True)
    handoffs.mkdir()
    workspaces.mkdir()
    profile = tmp_path / "profile-multi-builder.toml"
    profile.write_text(
        f"""\
version = 1
profile_name = "test-profile"
project_name = "test"
template_name = "gstack-harness"
repo_root = "{repo_root}"
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
default_notify_target = "planner"
heartbeat_owner = "koder"
heartbeat_seats = []
active_loop_owner = "planner"
seats = ["planner", "builder-a", "builder-b"]

[seat_roles]
planner = "planner-dispatcher"
builder-a = "builder"
builder-b = "builder"

[dynamic_roster]
materialized_seats = ["planner", "builder-a", "builder-b"]
""",
        encoding="utf-8",
    )
    (tasks / "TASKS.md").write_text("", encoding="utf-8")
    return profile, handoffs, tasks


def test_builder_dispatch_lock_blocks_second_dispatch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    profile, handoffs, _tasks = _write_profile(tmp_path, repo)

    first = _dispatch(profile, "task-1")
    assert first.returncode == 0

    blocked = _dispatch_with_force(profile, "task-2")
    assert blocked.returncode != 0
    assert "BLOCKED: builder seat builder dispatch outstanding (task-1)" in blocked.stderr
    assert "stacking another task on the same seat" in blocked.stderr
    assert not (handoffs / "task-2__planner__builder.json").exists()


def test_force_parallel_builder_bypasses_lock(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    profile, handoffs, _tasks = _write_profile(tmp_path, repo)

    first = _dispatch(profile, "task-1")
    assert first.returncode == 0

    forced = _dispatch_with_force(profile, "task-2", force_parallel_builder=True)
    assert forced.returncode == 0
    assert "WARNING: bypassing same-seat builder dispatch lock" in forced.stderr
    assert (handoffs / "task-2__planner__builder.json").exists()


def test_builder_dispatch_lock_is_per_exact_builder_seat(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    profile, handoffs, _tasks = _write_multi_builder_profile(tmp_path, repo)

    first = _dispatch_with_force(profile, "task-a", target="builder-a")
    assert first.returncode == 0, first.stderr

    second = _dispatch_with_force(profile, "task-b", target="builder-b")
    assert second.returncode == 0, second.stderr
    assert (handoffs / "task-a__planner__builder-a.json").exists()
    assert (handoffs / "task-b__planner__builder-b.json").exists()

    stacked = _dispatch_with_force(profile, "task-a2", target="builder-a")
    assert stacked.returncode != 0
    assert "BLOCKED: builder seat builder-a dispatch outstanding (task-a)" in stacked.stderr
    assert not (handoffs / "task-a2__planner__builder-a.json").exists()
