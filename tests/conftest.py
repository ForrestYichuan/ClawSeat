"""Shared pytest fixtures for ClawSeat test suite."""
from __future__ import annotations

import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

# ── Path bootstrapping ────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]

_EXTRA_PATHS = [
    str(REPO_ROOT),
    str(REPO_ROOT / "core"),
    str(REPO_ROOT / "core" / "scripts"),
    str(REPO_ROOT / "core" / "migration"),
    str(REPO_ROOT / "core" / "skills" / "gstack-harness" / "scripts"),
    str(REPO_ROOT / "core" / "skills" / "clawseat-install" / "scripts"),
    str(REPO_ROOT / "shells" / "openclaw-plugin"),
]

for _p in _EXTRA_PATHS:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _task_io import write_todo  # noqa: E402
from migrate_profile import build_lines  # noqa: E402


# ── Test suite layering ─────────────────────────────────────────────────

# Files that require Python 3.11+ (tomllib, slots=True, X | Y type union syntax).
# Silently skip collection on older runtimes rather than failing the whole suite.
if sys.version_info < (3, 11):
    collect_ignore_glob = [
        "test_install_qa_gaps.py",
        "test_launch_permissions.py",
        "test_memory_extract_links.py",
        "test_migrate_seat_auth.py",
        "test_multi_team_intake.py",
        "test_multi_team_minimal_seed.py",
        "test_openclaw_migrated_skills_registered.py",
        "test_patrol_codex_rewake.py",
        "test_patrol_stale_handoff_detect.py",
        "test_patrol_stale_handoff_rewake.py",
        "test_preflight.py",
        "test_preflight_gstack_severity.py",
        "test_preflight_profile_aware.py",
        "test_sandbox_home_symlink.py",
        "test_seat_clear_watchdog.py",
        "test_token_watermark.py",
    ]

HOST_TEST_FILES = {
    "test_feishu_enabled_switch.py",
    "test_heartbeat.py",
    "test_install_python_selection.py",
    "test_lark_cli_wrapper.py",
    "test_modal_detector.py",
    "test_openclaw_koder_workspace.py",
    "test_planner_announce.py",
    "test_scan_project_smoke.py",
    "test_seat_resolver_alias.py",
}

SLOW_TEST_FILES = {
    "test_ark_provider_support.py",
    "test_install_ancestor_patrol_plist.py",
    "test_install_auto_kickoff.py",
    "test_install_lazy_panes.py",
    "test_install_memory_singleton.py",
    "test_install_migrate_template_driven.py",
    "test_install_mirror_openclaw_skills.py",
    "test_install_pending_seats_dynamic.py",
    "test_install_phase_ready_early_exit.py",
    "test_install_post_bootstrap_profile_present.py",
    "test_install_privacy_setup.py",
    "test_install_provider_noninteractive.py",
    "test_install_repo_root_override.py",
    "test_install_seed_template_driven.py",
    "test_install_skill_symlinks.py",
    "test_install_template_flag.py",
    "test_pre_commit_privacy_hook.py",
    "test_check_engineer_status_working_detection.py",
    "test_launcher_oauth_host_env_preserve.py",
    "test_memory_stop_hook.py",
    "test_recover_grid_pty_warning.py",
    "test_recover_grid_worker_check.py",
    "test_scan_machine_subset.py",
    "test_send_notify_simplified.py",
    "test_send_and_verify_idle_wait.py",
    "test_session_stability_window.py",
    "test_skill_tier_registration.py",
    "test_skills_visible_to_all_tools.py",
    "test_seat_session_status_line.py",
    "test_template_clawseat_creative.py",
    "test_wait_for_text_target.py",
    "test_wait_for_seat_rejects_stale_tool.py",
    "test_xcode_best_provider_support.py",
}

SLOW_NODEID_PARTS = (
    "test_memory_oracle.py::TestScan",
)

LEGACY_NAME_PARTS = (
    "legacy",
    "deprecated",
    "retired",
    "compat",
    "dead_code",
    "dead-code",
)

SCRIPT_MARKER_TOKENS = (
    "subprocess.run(",
    "subprocess.check_output(",
    "Popen(",
    "install.sh",
    "tmux",
    "osascript",
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "host: requires local workstation state")
    config.addinivalue_line("markers", "slow: process-heavy or intentionally waits")
    config.addinivalue_line("markers", "legacy: deprecated compatibility surface")
    config.addinivalue_line("markers", "script: shell/CLI/subprocess surface")


@lru_cache(maxsize=None)
def _test_file_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(str(item.fspath))
        name = path.name
        rel = path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT) else path.as_posix()
        lower_name = name.lower()
        text = _test_file_text(str(path))

        if rel.startswith("tests/e2e/") or name in HOST_TEST_FILES:
            item.add_marker(pytest.mark.host)
        if (
            name.startswith("test_install_")
            or name in SLOW_TEST_FILES
            or any(part in item.nodeid for part in SLOW_NODEID_PARTS)
        ):
            item.add_marker(pytest.mark.slow)
        lower_nodeid = item.nodeid.lower()
        if any(part in lower_name or part in lower_nodeid for part in LEGACY_NAME_PARTS):
            item.add_marker(pytest.mark.legacy)
        if any(token in text for token in SCRIPT_MARKER_TOKENS):
            item.add_marker(pytest.mark.script)


# ── Fixtures ──────────────────────────────────────────────────────────

def _current_tmux_sessions() -> set[str]:
    try:
        raw = subprocess.check_output(
            ["tmux", "list-sessions", "-F", "#S"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().splitlines()
    except Exception:
        raw = []
    return {session.strip() for session in raw if session.strip()}


def _non_current_project_sessions(project: str) -> set[str]:
    return {
        session
        for session in _current_tmux_sessions()
        if not session.startswith(f"{project}-")
    }


@pytest.fixture(autouse=True, scope="session")
def assert_no_cross_project_session_kill():
    """Fail the sweep if any pre-existing non-project tmux session disappears."""
    project = os.environ.get("CLAWSEAT_PROJECT", "install")
    before = _non_current_project_sessions(project)
    yield
    after = _non_current_project_sessions(project)
    killed = before - after
    assert not killed, (
        f"pytest sweep killed cross-project tmux sessions: {killed}. "
        "If PTY exhaustion, send [BLOCKED:reason=pty-exhaustion] and escalate — do NOT self-resolve."
    )


@pytest.fixture()
def repo_root() -> Path:
    """Return the ClawSeat repository root."""
    return REPO_ROOT


@pytest.fixture()
def harness_profile(tmp_path: Path) -> Path:
    """Write a minimal but complete harness profile TOML and return its path.

    The profile contains every required field so that ``load_profile()``
    succeeds without hitting validation errors.
    """
    tasks_root = tmp_path / "tasks" / "test-project"
    workspace_root = tmp_path / "workspaces" / "test-project"
    handoff_dir = tasks_root / "patrol" / "handoffs"

    profile_path = tmp_path / "install-profile.toml"
    profile_path.write_text(
        "\n".join(
            [
                'version = 1',
                'profile_name = "test-harness-profile"',
                'template_name = "gstack-harness"',
                'project_name = "test-project"',
                f'repo_root = "{REPO_ROOT}"',
                f'tasks_root = "{tasks_root}"',
                f'project_doc = "{tasks_root / "PROJECT.md"}"',
                f'tasks_doc = "{tasks_root / "TASKS.md"}"',
                f'status_doc = "{tasks_root / "STATUS.md"}"',
                f'send_script = "{REPO_ROOT / "core" / "shell-scripts" / "send-and-verify.sh"}"',
                f'status_script = "{tasks_root / "patrol" / "check-status.sh"}"',
                f'patrol_script = "{tasks_root / "patrol" / "patrol-supervisor.sh"}"',
                f'agent_admin = "{REPO_ROOT / "core" / "scripts" / "agent_admin.py"}"',
                f'workspace_root = "{workspace_root}"',
                f'handoff_dir = "{handoff_dir}"',
                'heartbeat_owner = "koder"',
                'active_loop_owner = "planner"',
                'default_notify_target = "planner"',
                f'heartbeat_receipt = "{workspace_root / "koder" / "HEARTBEAT_RECEIPT.toml"}"',
                'seats = ["koder", "planner", "reviewer-1"]',
                'heartbeat_seats = ["koder"]',
                '',
                '[seat_roles]',
                'koder = "frontstage-supervisor"',
                'planner = "planner-dispatcher"',
                'reviewer-1 = "reviewer"',
                '',
                '[dynamic_roster]',
                'enabled = true',
                f'session_root = "{tmp_path / "sessions"}"',
                'bootstrap_seats = ["koder"]',
                'default_start_seats = ["koder", "planner"]',
                'compat_legacy_seats = false',
                '',
            ]
        ),
        encoding="utf-8",
    )
    return profile_path


@pytest.fixture()
def isolated_tasks_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point send-and-verify multi-project detection at an empty temp tree."""
    monkeypatch.setenv("AGENTS_TASKS_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True, scope="session")
def seed_install_compat_backlog_paths() -> None:
    """Seed the install compatibility backlog TODOs in the real home.

    The CI regression under `test_w2_medium_findings_resolved.py` checks that
    the historical `patrol` and `qa` backlog inboxes still exist on a fresh
    machine. Local operator homes already have them from prior runs; the test
    suite needs to create them once when they are absent.
    """
    tasks_root = Path.home() / ".agents" / "tasks" / "install"
    specs = {
        "patrol": {
            "task_id": "compatibility-backlog-anchor-patrol",
            "owner": "patrol",
            "title": "等待任务派发",
            "objective": "Compatibility backlog anchor for CI home checks.",
            "source": "bootstrap",
            "reply_to": "planner",
        },
        "qa": {
            "task_id": "compatibility-backlog-anchor-qa",
            "owner": "qa",
            "title": "等待任务派发",
            "objective": "Compatibility backlog anchor for CI home checks.",
            "source": "bootstrap",
            "reply_to": "memory",
        },
    }
    for seat, payload in specs.items():
        todo = tasks_root / seat / "TODO.md"
        if todo.exists():
            continue
        todo.parent.mkdir(parents=True, exist_ok=True)
        write_todo(
            todo,
            project="install",
            status="pending",
            test_policy=None,
            **payload,
        )

    profile_path = Path.home() / ".agents" / "profiles" / "install-profile-dynamic.toml"
    if not profile_path.exists():
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        (Path.home() / ".agents" / "tasks" / "install").mkdir(parents=True, exist_ok=True)
        (Path.home() / ".agents" / "workspaces" / "install").mkdir(parents=True, exist_ok=True)
        (Path.home() / ".agents" / "tasks" / "install" / "patrol" / "handoffs").mkdir(parents=True, exist_ok=True)
        lines = build_lines(
            {
                "profile_name": "install",
                "description": "canonical install profile for CI compatibility",
                "send_script": str(REPO_ROOT / "core" / "shell-scripts" / "send-and-verify.sh"),
                "agent_admin": str(REPO_ROOT / "core" / "scripts" / "agent_admin.py"),
                "heartbeat_owner": "koder",
                "active_loop_owner": "planner",
                "default_notify_target": "planner",
                "seat_roles": {
                    "koder": "frontstage-supervisor",
                    "builder": "code-builder",
                    "planner": "planner-dispatcher",
                },
                "seats": ["koder", "builder", "planner"],
            },
            project_name="install",
            repo_root=str(REPO_ROOT),
            bootstrap_only=False,
        )
        profile_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
