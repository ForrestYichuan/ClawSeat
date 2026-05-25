"""Pin tests for FIX-SEND-AND-VERIFY-DELEGATE-TO-TMUX-SEND.

Root cause: agent-launcher wraps `tmux` with a guard that blocks `send-keys`
unless AGENT_LAUNCHER_TMUX_SEND_ACTIVE=1 or the caller goes through the
compliant `tmux-send` binary.  The prior send-and-verify.sh always used raw
send-keys → guard intercepted → exit 2 → notified_at:null.

Tests verify:
  1. When tmux-send is on PATH, send-and-verify delegates to it (not raw send-keys).
  2. When tmux-send is absent, fallback exports AGENT_LAUNCHER_TMUX_SEND_ACTIVE=1.
  3. dispatch_task.py emits NOTIFY FAILED banner and exits non-zero when notify fails.
  4. --allow-notify-failure tolerates the failure (exit 0).
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SEND_AND_VERIFY = _REPO / "core" / "shell-scripts" / "send-and-verify.sh"
_DISPATCH = _REPO / "core" / "skills" / "gstack-harness" / "scripts" / "dispatch_task.py"
_SCRIPTS = _REPO / "core" / "skills" / "gstack-harness" / "scripts"


def _write_exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return path


def _base_env(tmp_path: Path) -> dict:
    """Env for send-and-verify.sh: stubs take priority, system tools still available."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(tmp_path / "home"),
        # Prepend stub bin dir so our shims win, but keep system PATH so
        # bash/sleep/printf/env/etc. are still resolvable.
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
        "CLAWSEAT_REAL_HOME": str(tmp_path / "home"),
        "CLAWSEAT_SEND_ALLOW_NO_PROJECT": "1",   # skip multi-project guard
    }


# ── Test 1: delegates to tmux-send when available ────────────────────────────


def test_delegates_to_tmux_send_when_available(tmp_path: Path) -> None:
    """When tmux-send is on PATH, send-and-verify must route through it, not raw send-keys."""
    bin_dir = tmp_path / "bin"
    log = tmp_path / "tmux-send.log"

    # Stub tmux-send: records its args and succeeds.
    _write_exe(
        bin_dir / "tmux-send",
        f"#!/bin/bash\nprintf '%s\\n' \"tmux-send $*\" >> '{log}'\nexit 0\n",
    )
    # Stub tmux: only records send-keys calls (has-session is transparent).
    raw_log = tmp_path / "tmux-raw.log"
    _write_exe(
        bin_dir / "tmux",
        textwrap.dedent(f"""\
            #!/bin/bash
            case "$1" in
              has-session) exit 0 ;;
              send-keys) printf 'RAW send-keys\\n' >> '{raw_log}'; exit 0 ;;
              *) exit 0 ;;
            esac
        """),
    )
    # Stub agentctl: echo SESSION as-is.
    _write_exe(bin_dir / "agentctl.sh", "#!/bin/bash\necho 'stub-session'\n")

    env = _base_env(tmp_path)
    env["AGENTCTL_BIN"] = str(bin_dir / "agentctl.sh")
    env["TMUX_BIN"] = str(bin_dir / "tmux")

    result = subprocess.run(
        ["bash", str(_SEND_AND_VERIFY), "stub-session", "hello world"],
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 0, f"expected success; stderr:\n{result.stderr}"
    assert log.exists(), "tmux-send was not called"
    assert "tmux-send stub-session" in log.read_text(encoding="utf-8")
    # Raw tmux send-keys must NOT have been invoked.
    assert not raw_log.exists() or "RAW send-keys" not in raw_log.read_text(encoding="utf-8"), (
        "raw tmux send-keys was called even though tmux-send was available"
    )


# ── Test 2: fallback exports ACTIVE=1 when tmux-send unavailable ─────────────


def test_fallback_sets_active_env_when_tmux_send_unavailable(tmp_path: Path) -> None:
    """When tmux-send is absent, the fallback raw path must export AGENT_LAUNCHER_TMUX_SEND_ACTIVE=1."""
    bin_dir = tmp_path / "bin"
    active_log = tmp_path / "active.log"

    # Stub tmux: records AGENT_LAUNCHER_TMUX_SEND_ACTIVE in env.
    _write_exe(
        bin_dir / "tmux",
        textwrap.dedent(f"""\
            #!/bin/bash
            case "$1" in
              has-session) exit 0 ;;
              send-keys) printf 'ACTIVE=%s\\n' "${{AGENT_LAUNCHER_TMUX_SEND_ACTIVE:-}}" >> '{active_log}'; exit 0 ;;
              *) exit 0 ;;
            esac
        """),
    )
    # No tmux-send in bin_dir.
    _write_exe(bin_dir / "agentctl.sh", "#!/bin/bash\necho 'stub-session'\n")

    env = _base_env(tmp_path)
    env["AGENTCTL_BIN"] = str(bin_dir / "agentctl.sh")
    env["TMUX_BIN"] = str(bin_dir / "tmux")
    # Ensure tmux-send is definitely not resolvable via the fallback dir.
    env["AGENT_LAUNCHER_BIN"] = str(tmp_path / "no-such-dir")

    result = subprocess.run(
        ["bash", str(_SEND_AND_VERIFY), "stub-session", "hello"],
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    content = active_log.read_text(encoding="utf-8") if active_log.exists() else ""
    assert "ACTIVE=1" in content, (
        f"AGENT_LAUNCHER_TMUX_SEND_ACTIVE=1 was not exported in raw fallback path; log:\n{content}"
    )


def test_fallback_sends_three_enter_flushes_when_tmux_send_unavailable(tmp_path: Path) -> None:
    """Raw fallback must send text once, then Enter three times for slow Codex/Gemini TUIs."""
    bin_dir = tmp_path / "bin"
    send_log = tmp_path / "send.log"

    _write_exe(
        bin_dir / "tmux",
        textwrap.dedent(f"""\
            #!/bin/bash
            case "$1" in
              has-session) exit 0 ;;
              send-keys)
                if [ "$2" = "-l" ]; then
                  printf 'TEXT:%s\\n' "${{@: -1}}" >> '{send_log}'
                else
                  printf 'KEY:%s\\n' "${{@: -1}}" >> '{send_log}'
                fi
                exit 0
                ;;
              *) exit 0 ;;
            esac
        """),
    )
    _write_exe(bin_dir / "agentctl.sh", "#!/bin/bash\necho 'stub-session'\n")

    env = _base_env(tmp_path)
    env["AGENTCTL_BIN"] = str(bin_dir / "agentctl.sh")
    env["TMUX_BIN"] = str(bin_dir / "tmux")
    env["AGENT_LAUNCHER_BIN"] = str(tmp_path / "no-such-dir")
    env["AGENT_LAUNCHER_TMUX_SEND_ENTER_DELAY"] = "0"

    result = subprocess.run(
        ["bash", str(_SEND_AND_VERIFY), "stub-session", "hello"],
        capture_output=True, text=True, env=env, timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert send_log.read_text(encoding="utf-8").splitlines() == [
        "TEXT:hello",
        "KEY:Enter",
        "KEY:Enter",
        "KEY:Enter",
    ]


# ── Test 3: dispatch_task loud-fail on notify failure ────────────────────────


def _make_profile(tmp_path: Path) -> tuple[Path, Path]:
    tasks = tmp_path / "tasks"
    handoffs = tmp_path / "handoffs"
    ws = tmp_path / "workspaces"
    for d in (tasks / "builder", handoffs, ws):
        d.mkdir(parents=True, exist_ok=True)

    send_stub = tmp_path / "bin" / "send-and-verify.sh"
    _write_exe(send_stub, "#!/bin/bash\necho 'SEND_FAIL' >&2\nexit 2\n")

    profile = tmp_path / "profile.toml"
    profile.write_text(
        textwrap.dedent(f"""\
            version = 1
            profile_name = "test"
            template_name = "gstack-harness"
            project_name = "testproject"
            repo_root = "{tmp_path}"
            tasks_root = "{tasks}"
            workspace_root = "{ws}"
            handoff_dir = "{handoffs}"
            project_doc = "{tasks}/PROJECT.md"
            tasks_doc = "{tasks}/TASKS.md"
            status_doc = "{tasks}/STATUS.md"
            send_script = "{send_stub}"
            status_script = "/bin/echo"
            patrol_script = "/bin/echo"
            agent_admin = "/bin/echo"
            heartbeat_receipt = "{ws}/koder/HEARTBEAT_RECEIPT.toml"
            seats = ["builder", "planner"]
            heartbeat_seats = []
            active_loop_owner = "planner"
            default_notify_target = "planner"
            heartbeat_owner = "koder"
            heartbeat_transport = "openclaw"

            [seat_roles]
            builder = "builder"
            planner = "planner-dispatcher"

            [dynamic_roster]
            materialized_seats = ["builder", "planner"]
        """),
        encoding="utf-8",
    )
    (tasks / "builder").mkdir(parents=True, exist_ok=True)
    return profile, tasks


def _dispatch_cmd(
    profile: Path, task_id: str = "T-FAIL-001", extra: list[str] | None = None
) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable, str(_DISPATCH),
        "--profile", str(profile),
        "--source", "planner",
        "--target", "builder",
        "--task-id", task_id,
        "--title", "test notify fail",
        "--objective", "test objective",
        "--test-policy", "UPDATE",
        "--reply-to", "planner",
    ] + (extra or [])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                          env={**os.environ, "CLAWSEAT_STATE_DB": ""})


def test_notify_failure_loud_exits_nonzero(tmp_path: Path) -> None:
    """When send-and-verify.sh fails (exit 2), dispatch_task must exit ≠ 0 with NOTIFY FAILED banner."""
    profile, _ = _make_profile(tmp_path)
    result = _dispatch_cmd(profile)
    assert result.returncode != 0, (
        f"expected non-zero exit on notify failure; got 0\nstdout:\n{result.stdout}"
    )
    assert "NOTIFY FAILED" in result.stderr, (
        f"expected 'NOTIFY FAILED' banner in stderr;\nstderr:\n{result.stderr}"
    )


# ── Test 4: --allow-notify-failure tolerates failure ─────────────────────────


def test_allow_notify_failure_flag_tolerates(tmp_path: Path) -> None:
    """--allow-notify-failure: dispatch_task exits 0 even when notify fails."""
    profile, _ = _make_profile(tmp_path)
    result = _dispatch_cmd(profile, task_id="T-ALLOW-001", extra=["--allow-notify-failure"])
    assert result.returncode == 0, (
        f"--allow-notify-failure should produce exit 0;\nstderr:\n{result.stderr}"
    )
    # Banner is still emitted (informational) but exit is 0.
    assert "NOTIFY FAILED" in result.stderr
    assert "allow-notify-failure" in result.stderr.lower() or "continuing" in result.stderr.lower()
