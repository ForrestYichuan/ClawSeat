"""CX: complete_handoff closure schema + base validation tests."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "skills" / "gstack-harness" / "scripts"
_DISPATCH = _SCRIPTS / "dispatch_task.py"
_COMPLETE = _SCRIPTS / "complete_handoff.py"

sys.path.insert(0, str(_REPO / "core" / "lib"))
sys.path.insert(0, str(_SCRIPTS))

from complete_handoff import complete_v3_brief_queue_if_possible  # noqa: E402
from queue_io import append_event, read_current_state  # noqa: E402


def _run(*cmd: str, cwd: Path | str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *cmd],
        capture_output=True,
        text=True,
        cwd=str(cwd or _SCRIPTS),
        check=False,
    )


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "ci"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "ci@example.com"], check=True)

    (repo / "README.md").write_text("main\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "main", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-B", "main"], check=True)

    main_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/clawseat/main", main_sha], check=True)

    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feat/CX-test"], check=True)
    (repo / "FEATURE.md").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "FEATURE.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "feature branch", "-q"], check=True)
    return repo


def _write_profile(tmp_path: Path, repo_root: Path) -> tuple[Path, Path, Path]:
    tasks = tmp_path / "tasks"
    handoffs = tmp_path / "handoffs"
    workspaces = tmp_path / "workspaces"
    tasks.mkdir()
    (tasks / "planner").mkdir()
    (tasks / "builder").mkdir()
    handoffs.mkdir()
    workspaces.mkdir()
    profile = tmp_path / "profile.toml"
    profile.write_text(
        f"""\
version = 1
profile_name = \"test-profile\"
project_name = \"test\"
template_name = \"gstack-harness\"
repo_root = \"{repo_root}\"
tasks_root = \"{tasks}\"
workspace_root = \"{workspaces}\"
handoff_dir = \"{handoffs}\"
project_doc = \"{tasks}/PROJECT.md\"
tasks_doc = \"{tasks}/TASKS.md\"
status_doc = \"{tasks}/STATUS.md\"
send_script = \"/bin/echo\"
status_script = \"/bin/echo\"
patrol_script = \"/bin/echo\"
agent_admin = \"/bin/echo\"
heartbeat_receipt = \"{workspaces}/koder/HEARTBEAT_RECEIPT.toml\"
heartbeat_transport = \"tmux\"
default_notify_target = \"planner\"
heartbeat_owner = \"koder\"
heartbeat_seats = []
active_loop_owner = \"planner\"
seats = [\"planner\", \"builder\"]

[seat_roles]
planner = \"planner-dispatcher\"
builder = \"builder\"

[dynamic_roster]
materialized_seats = [\"planner\", \"builder\"]
""",
        encoding="utf-8",
    )
    (tasks / "TASKS.md").write_text("", encoding="utf-8")
    return profile, handoffs, tasks


def _dispatch(
    profile: Path,
    task_id: str,
    *,
    no_notify: bool = True,
    expected_branch: str = "feat/CX-test",
    expected_worktree: str = "/tmp/CX-test-wt",
) -> subprocess.CompletedProcess[str]:
    args = [
        str(_DISPATCH),
        "--profile", str(profile),
        "--source", "planner",
        "--target", "builder",
        "--task-id", task_id,
        "--title", f"test {task_id}",
        "--objective", "run",
        "--test-policy", "UPDATE",
        "--reply-to", "planner",
    ]
    if expected_branch:
        args.extend(["--expected-branch", expected_branch])
    if expected_worktree:
        args.extend(["--expected-worktree", expected_worktree])
    if no_notify:
        args.append("--no-notify")
    return _run(*args)


def _dispatch_with_handoff_fields(
    profile: Path,
    task_id: str,
    *,
    core_ux: bool = False,
    no_notify: bool = True,
) -> subprocess.CompletedProcess[str]:
    args = [
        str(_DISPATCH),
        "--profile", str(profile),
        "--source", "planner",
        "--target", "builder",
        "--task-id", task_id,
        "--title", f"test {task_id}",
        "--objective", "run",
        "--test-policy", "UPDATE",
        "--reply-to", "planner",
        "--expected-branch",
        "feat/CX-test",
        "--expected-worktree",
        "/tmp/CX-test-wt",
    ]
    if core_ux:
        args.append("--core-ux")
    if no_notify:
        args.append("--no-notify")
    return _run(*args)


def _complete(
    profile: Path,
    task_id: str,
    *,
    target: str = "planner",
    branch: str | None = None,
    pr_number: str | None = None,
    ci_conclusion: str | None = None,
    core_ux_gate: str | None = None,
    ack_only: bool = False,
    no_notify: bool = True,
) -> subprocess.CompletedProcess[str]:
    (Path(profile).parent / "tasks" / "builder" / "DELIVERY.md").write_text(
        f"task_id: {task_id}\nowner: builder\n\nDone.\n",
        encoding="utf-8",
    )
    args = [
        str(_COMPLETE),
        "--profile", str(profile),
        "--source", "builder",
        "--target", target,
        "--task-id", task_id,
        "--title", f"done {task_id}",
        "--summary", "completed",
        "--status", "completed",
    ]
    if branch:
        args.extend(["--branch", branch])
    if pr_number:
        args.extend(["--pr-number", pr_number])
    if ci_conclusion:
        args.extend(["--ci-conclusion", ci_conclusion])
    if core_ux_gate is not None:
        args.extend(["--core-ux-gate", core_ux_gate])
    if ack_only:
        args.append("--ack-only")
    if no_notify:
        args.append("--no-notify")
    return _run(*args)


def _get_receipt(handoffs: Path, task_id: str) -> dict:
    path = handoffs / f"{task_id}__builder__planner.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_completion_accepts_required_closure_fields_when_available(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    profile, handoffs, _ = _write_profile(tmp_path, repo)

    assert _dispatch(profile, "C1").returncode == 0
    result = _complete(profile, "C1", branch="feat/CX-test", pr_number="101", ci_conclusion="success")
    assert result.returncode == 0, result.stderr

    receipt = _get_receipt(handoffs, "C1")
    expected_base = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "feat/CX-test", "clawseat/main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    expected_tip = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "feat/CX-test"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert receipt["branch_base"] == expected_base
    assert receipt["branch_tip"] == expected_tip
    assert receipt["pr_number"] == "101"
    assert receipt["ci_conclusion"] == "success"


def test_completion_syncs_matching_v3_brief_queue(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    queue = tasks_root / "team-a" / "tasks.queue.jsonl"
    profile_path = tmp_path / "profile.toml"
    profile_path.write_text(
        f"""profile_name = "p-profile"
project_name = "p"
tasks_root = "{tasks_root}"

[teams]
team-a = {{ seats = ["team-a-planner"] }}

[seat_overrides.team-a-planner]
tool = "claude"
""",
        encoding="utf-8",
    )
    append_event(
        queue,
        {
            "event_type": "task_created",
            "actor": "memory",
            "task_id": "T1",
            "brief_path": "tasks/p/team-a/brief/T1.md",
            "parent_task_id": None,
            "depends_on": [],
        },
    )
    append_event(
        queue,
        {"event_type": "task_claimed", "actor": "planner@claude", "task_id": "T1"},
    )
    profile = SimpleNamespace(
        profile_path=profile_path,
        tasks_root=tasks_root,
        seat_overrides={"team-a-planner": {"tool": "claude"}},
    )

    synced = complete_v3_brief_queue_if_possible(
        profile,
        seat="team-a-planner",
        task_id="T1",
        status="completed",
        verdict="PASS",
        summary="done",
    )

    assert synced == queue
    state = read_current_state(queue)
    assert state["T1"].status == "task_done"
    assert state["T1"].verdict == "PASS"


def test_completion_syncs_investigation_verdict_to_v3_queue(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    queue = tasks_root / "team-a" / "tasks.queue.jsonl"
    profile_path = tmp_path / "profile.toml"
    profile_path.write_text(
        f"""profile_name = "p-profile"
project_name = "p"
tasks_root = "{tasks_root}"

[teams]
team-a = {{ seats = ["team-a-planner"] }}

[seat_overrides.team-a-planner]
tool = "claude"
""",
        encoding="utf-8",
    )
    append_event(
        queue,
        {
            "event_type": "task_created",
            "actor": "memory",
            "task_id": "T2",
            "brief_path": "tasks/p/team-a/brief/T2.md",
            "parent_task_id": None,
            "depends_on": [],
        },
    )
    append_event(
        queue,
        {"event_type": "task_claimed", "actor": "planner@claude", "task_id": "T2"},
    )
    profile = SimpleNamespace(
        profile_path=profile_path,
        tasks_root=tasks_root,
        seat_overrides={"team-a-planner": {"tool": "claude"}},
    )

    synced = complete_v3_brief_queue_if_possible(
        profile,
        seat="team-a-planner",
        task_id="T2",
        status="completed",
        verdict="INVESTIGATED",
        summary="investigated",
    )

    assert synced == queue
    assert read_current_state(queue)["T2"].status == "task_done"


def test_completion_syncs_reset_v3_brief_queue(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    queue = tasks_root / "team-a" / "tasks.queue.jsonl"
    profile_path = tmp_path / "profile.toml"
    profile_path.write_text(
        f"""profile_name = "p-profile"
project_name = "p"
tasks_root = "{tasks_root}"

[teams]
team-a = {{ seats = ["team-a-planner"] }}

[seat_overrides.team-a-planner]
tool = "claude"
""",
        encoding="utf-8",
    )
    append_event(
        queue,
        {
            "event_type": "task_created",
            "actor": "memory",
            "task_id": "T3",
            "brief_path": "tasks/p/team-a/brief/T3.md",
            "parent_task_id": None,
            "depends_on": [],
        },
    )
    append_event(
        queue,
        {
            "event_type": "task_reset",
            "actor": "memory",
            "task_id": "T3",
            "reset_reason": "acceptance criteria repaired",
        },
    )
    profile = SimpleNamespace(
        profile_path=profile_path,
        tasks_root=tasks_root,
        seat_overrides={"team-a-planner": {"tool": "claude"}},
    )

    synced = complete_v3_brief_queue_if_possible(
        profile,
        seat="team-a-planner",
        task_id="T3",
        status="completed",
        verdict="PASS",
        summary="done after reset",
    )

    assert synced == queue
    state = read_current_state(queue)
    assert state["T3"].status == "task_done"
    assert state["T3"].verdict == "PASS"


def test_completion_without_branch_fields_fails_when_expected_base_present(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    profile, handoffs, _ = _write_profile(tmp_path, repo)
    assert _dispatch(profile, "C2").returncode == 0

    result = _complete(profile, "C2")
    assert result.returncode != 0
    assert "closure receipt missing required fields" in result.stderr
    assert "branch_base" in result.stderr
    assert not (handoffs / "C2__builder__planner.json").exists()


def test_completion_requires_core_ux_gate_for_core_ux_steps(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    profile, handoffs, _ = _write_profile(tmp_path, repo)
    assert _dispatch_with_handoff_fields(profile, "C9", core_ux=True).returncode == 0

    result = _complete(profile, "C9", branch="feat/CX-test", pr_number="109", ci_conclusion="success")
    assert result.returncode != 0
    assert "core_ux_gate is required for core_ux steps" in result.stderr
    assert not (handoffs / "C9__builder__planner.json").exists()

    result = _complete(
        profile,
        "C9",
        branch="feat/CX-test",
        pr_number="109",
        ci_conclusion="success",
        # explicit positive case
        core_ux_gate="met",
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads((handoffs / "C9__builder__planner.json").read_text(encoding="utf-8"))
    assert receipt["core_ux_gate"] == "met"


def test_completion_does_not_require_core_ux_gate_for_non_core_ux(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    profile, handoffs, _ = _write_profile(tmp_path, repo)
    assert _dispatch_with_handoff_fields(profile, "C10", core_ux=False).returncode == 0

    result = _complete(profile, "C10", branch="feat/CX-test", pr_number="110", ci_conclusion="success")
    assert result.returncode == 0, result.stderr
    receipt = json.loads((handoffs / "C10__builder__planner.json").read_text(encoding="utf-8"))
    assert "core_ux_gate" not in receipt


def test_completion_missing_pr_number_fails(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    profile, _, _ = _write_profile(tmp_path, repo)
    assert _dispatch(profile, "C3").returncode == 0

    result = _complete(profile, "C3", branch="feat/CX-test", ci_conclusion="success")
    assert result.returncode != 0
    assert "pr_number" in result.stderr


def test_completion_missing_ci_conclusion_fails(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    profile, _, _ = _write_profile(tmp_path, repo)
    assert _dispatch(profile, "C4").returncode == 0

    result = _complete(profile, "C4", branch="feat/CX-test", pr_number="101")
    assert result.returncode != 0
    assert "ci_conclusion" in result.stderr


def test_completion_soft_fails_stale_branch_base_against_expected(tmp_path: Path) -> None:
    """v3 spec §10 item 6: branch_base mismatch is now a soft-fail (warning +
    lineage_status=divergent), not SystemExit. Earlier hard-fail blocked
    AL-503 planner→memory fan-in. Memory PASS_NEEDS_INTEGRATION handler
    recovers downstream (spec §C / DO spec)."""
    import json as _json

    repo = _init_git_repo(tmp_path)
    profile, _, _ = _write_profile(tmp_path, repo)

    assert _dispatch(profile, "C5").returncode == 0

    # Advance local main and advance the main remote ref without rebuilding the feature branch.
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    (repo / "README.md").write_text("hotfix\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "hotfix", "-q"], check=True)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/clawseat/main", head], check=True)

    result = _complete(profile, "C5", branch="main", pr_number="101", ci_conclusion="success")
    # Receipt now emitted (returncode 0) with warning on stderr.
    assert result.returncode == 0, f"expected soft-fail success, got: {result.stderr}"
    assert "branch_base mismatch" in result.stderr
    # PASS_NEEDS_INTEGRATION signal must appear so memory's handler routes recovery.
    assert "PASS_NEEDS_INTEGRATION" in result.stderr, (
        "soft-fail must surface PASS_NEEDS_INTEGRATION hint so memory "
        "handler can route recovery (spec §C / DO spec)"
    )
    # Verify receipt records the divergent lineage so downstream (memory) can
    # route via PASS_NEEDS_INTEGRATION.
    receipt_path = tmp_path / "handoffs" / "C5__builder__planner.json"
    assert receipt_path.exists()
    receipt = _json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt.get("lineage_status") == "divergent"
    assert receipt.get("head_contains_commit") is False


def test_completion_legacy_without_expected_base_skips_validation(tmp_path: Path) -> None:
    # Missing repo means dispatch omits expected_base_sha.
    repo = tmp_path / "missing-repo"
    profile, _, _ = _write_profile(tmp_path, repo)
    # Do not create repo at all.
    assert _dispatch(profile, "C6", no_notify=True).returncode == 0

    result = _complete(profile, "C6")
    assert result.returncode == 0
    receipt = json.loads(
        (tmp_path / "handoffs" / "C6__builder__planner.json").read_text(encoding="utf-8")
    )
    assert receipt["kind"] == "completion"


def test_ack_only_skips_closure_validation_when_expected_base_present(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    profile, handoffs, _ = _write_profile(tmp_path, repo)
    assert _dispatch(profile, "C7").returncode == 0

    result = _complete(profile, "C7", ack_only=True)
    assert result.returncode == 0, result.stderr
    assert (handoffs / "C7__builder__planner.json").exists()
    receipt = json.loads((handoffs / "C7__builder__planner.json").read_text(encoding="utf-8"))
    assert receipt["kind"] == "completion"
    assert "consumed_at" in receipt
    assert receipt["consumed_ack"].startswith("Consumed: C7 from builder at ")


def test_complete_handoff_help_includes_branch_and_closure_flags() -> None:
    result = _run(str(_COMPLETE), "--help")
    assert result.returncode == 0
    for flag in ("--branch", "--pr-number", "--ci-conclusion"):
        assert flag in result.stdout
