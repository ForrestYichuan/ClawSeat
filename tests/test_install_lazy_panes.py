from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HELPERS_PATH = Path(__file__).with_name("test_install_isolation.py")
_HELPERS_SPEC = importlib.util.spec_from_file_location("test_install_isolation_helpers", _HELPERS_PATH)
assert _HELPERS_SPEC is not None and _HELPERS_SPEC.loader is not None
_HELPERS = importlib.util.module_from_spec(_HELPERS_SPEC)
_HELPERS_SPEC.loader.exec_module(_HELPERS)

_INSTALL = _HELPERS._INSTALL
_WAIT_FOR_SEAT = _HELPERS._WAIT_FOR_SEAT
_fake_install_root = _HELPERS._fake_install_root
_read_jsonl = _HELPERS._read_jsonl
_write_executable = _HELPERS._write_executable


def _write_engineer_profile(tmp_path: Path, seat: str, default_tool: str) -> Path:
    home = tmp_path / "home"
    engineer_dir = home / ".agents" / "engineers" / seat
    engineer_dir.mkdir(parents=True, exist_ok=True)
    (engineer_dir / "engineer.toml").write_text(
        f'id = "{seat}"\ndefault_tool = "{default_tool}"\n',
        encoding="utf-8",
    )
    return home


def test_install_dry_run_only_launches_memory_and_uses_lazy_wait_panes(tmp_path: Path) -> None:
    root, home, _, _, py_stubs = _fake_install_root(tmp_path)
    result = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "install.sh"),
            "--dry-run",
            "--project",
            "spawn49",
            "--template",
            "clawseat-engineering",
            "--provider",
            "minimax",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHONPATH": f"{py_stubs}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "PYTHON_BIN": sys.executable,
        },
        check=False,
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr

    assert output.count("agent-launcher.sh") == 1
    assert "spawn49-memory-claude" in output
    assert "machine-memory-claude" not in output
    assert "project bootstrap --template clawseat-engineering --local" in output
    for seat in ("planner", "builder", "reviewer", "patrol"):
        assert f"bash {root}/scripts/wait-for-seat.sh spawn49 {seat}" in output


def test_install_bootstrap_writes_runtime_template_and_lazy_grid(tmp_path: Path) -> None:
    root, home, launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)
    agent_admin_log = tmp_path / "agent_admin.jsonl"
    iterm_payload_log = tmp_path / "iterm_payload.jsonl"

    result = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "install.sh"),
            "--project",
            "spawn49",
            "--template",
            "clawseat-engineering",
            "--provider",
            "minimax",
        ],
        input="\n",
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PATH": f"{root.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": f"{py_stubs}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "PYTHON_BIN": sys.executable,
            "LOG_FILE": str(launcher_log),
            "TMUX_LOG_FILE": str(tmux_log),
            "AGENT_ADMIN_LOG": str(agent_admin_log),
            "ITERM_PAYLOAD_LOG": str(iterm_payload_log),
        },
        check=False,
    )
    assert result.returncode == 0, result.stderr

    bootstrap_calls = _read_jsonl(agent_admin_log)
    assert bootstrap_calls[0] == {
        "argv": [
            "project",
            "bootstrap",
            "--template",
            "clawseat-engineering",
            "--local",
            str(home / ".agents" / "tasks" / "spawn49" / "project-local.toml"),
        ],
        "cwd": str(home / ".agents" / "templates"),
    }
    assert [
        "engineer",
        "create",
        "patrol",
        "spawn49",
        "--no-monitor",
    ] in [call["argv"] for call in bootstrap_calls]

    local_text = (
        home / ".agents" / "tasks" / "spawn49" / "project-local.toml"
    ).read_text(encoding="utf-8")
    assert 'seat_order = ["memory", "planner", "builder", "reviewer", "patrol"]' in local_text
    assert 'session_name = "spawn49-memory-claude"' in local_text
    assert local_text.count("[[overrides]]") == 5
    assert 'provider = "deepseek"' in local_text
    assert 'provider = "minimax"' in local_text
    assert "materialized_seats" not in local_text
    assert "runtime_seats" not in local_text

    payloads = _read_jsonl(iterm_payload_log)
    grid_payload = payloads[0]
    assert grid_payload["title"] == "clawseat-spawn49-workers"
    commands = {pane["label"]: pane["command"] for pane in grid_payload["panes"]}
    for seat in ("planner", "builder", "reviewer", "patrol"):
        assert commands[seat] == f"bash {root}/scripts/wait-for-seat.sh spawn49 {seat}"

    planner_secret = home / ".agents" / "secrets" / "claude" / "deepseek" / "planner.env"
    assert planner_secret.is_file()
    assert "deepseek-v4-pro" in planner_secret.read_text(encoding="utf-8")
    assert not (home / ".agents" / "secrets" / "claude" / "minimax" / "memory.env").exists()

    guide_path = home / ".agents" / "tasks" / "spawn49" / "OPERATOR-START-HERE.md"
    assert guide_path.is_file()
    guide_text = guide_path.read_text(encoding="utf-8")
    assert "Phase-A 不让 memory 做同步调研" in guide_text
    assert "B2.5 / B5 都按 brief 由 memory seat 自己 Read openclaw / binding 文件" in guide_text
    assert "B7 后接收 phase-a-decisions learnings" in guide_text
    assert "agent_admin.py session start-engineer" in guide_text
    assert "第一步：让 memory 做 openclaw 生态调研（brief B2.6）" not in guide_text
    assert "ClawSeat install complete" in result.stdout


def test_install_no_window_skips_native_iterm_payloads_but_launches_memory(tmp_path: Path) -> None:
    root, home, launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)
    agent_admin_log = tmp_path / "agent_admin.jsonl"
    iterm_payload_log = tmp_path / "iterm_payload.jsonl"

    result = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "install.sh"),
            "--project",
            "spawn49",
            "--template",
            "clawseat-engineering",
            "--provider",
            "minimax",
            "--no-window",
        ],
        input="\n",
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PATH": f"{root.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": f"{py_stubs}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "PYTHON_BIN": sys.executable,
            "LOG_FILE": str(launcher_log),
            "TMUX_LOG_FILE": str(tmux_log),
            "AGENT_ADMIN_LOG": str(agent_admin_log),
            "ITERM_PAYLOAD_LOG": str(iterm_payload_log),
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "skip native iTerm windows (--no-window)" in result.stdout
    assert not iterm_payload_log.exists()

    records = _read_jsonl(launcher_log)
    assert [record["session"] for record in records] == ["spawn49-memory-claude"]

    bootstrap_calls = _read_jsonl(agent_admin_log)
    assert bootstrap_calls[0]["argv"][:4] == [
        "project",
        "bootstrap",
        "--template",
        "clawseat-engineering",
    ]


def test_install_explicit_custom_api_flags_work_without_detect_or_tty(tmp_path: Path) -> None:
    root, home, launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)
    agent_admin_log = tmp_path / "agent_admin.jsonl"
    iterm_payload_log = tmp_path / "iterm_payload.jsonl"
    _write_executable(
        root / "core" / "skills" / "memory-oracle" / "scripts" / "scan_environment.py",
        """#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
machine = Path(args.output) / "machine"
machine.mkdir(parents=True, exist_ok=True)
(machine / "credentials.json").write_text("{", encoding="utf-8")
for name in ("network", "openclaw", "github", "current_context"):
    (machine / f"{name}.json").write_text("{}", encoding="utf-8")
""",
    )

    result = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "install.sh"),
            "--project",
            "custom49",
            "--template",
            "clawseat-engineering",
            "--base-url",
            "https://custom.api.invalid/v1",
            "--api-key",
            "sk-custom49",
            "--model",
            "claude-custom-49",
        ],
        input="\n",
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PATH": f"{root.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": f"{py_stubs}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "PYTHON_BIN": sys.executable,
            "LOG_FILE": str(launcher_log),
            "TMUX_LOG_FILE": str(tmux_log),
            "AGENT_ADMIN_LOG": str(agent_admin_log),
            "ITERM_PAYLOAD_LOG": str(iterm_payload_log),
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Using: explicit custom API" in result.stdout

    provider_env = (
        home / ".agents" / "tasks" / "custom49" / "memory-provider.env"
    ).read_text(encoding="utf-8")
    assert "https://custom.api.invalid/v1" in provider_env
    assert "sk-custom49" in provider_env
    assert "claude-custom-49" in provider_env

    records = _read_jsonl(launcher_log)
    assert [record["session"] for record in records] == ["custom49-memory-claude"]
    for record in records:
        assert record["custom_api_key_present"] is True
        assert record["custom_base_url"] == "https://custom.api.invalid/v1"
        assert record["custom_model"] == "claude-custom-49"


def test_install_rejects_unpaired_base_url_flag(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = subprocess.run(
        ["bash", str(_INSTALL), "--base-url", "https://custom.api.invalid/v1"],
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
        },
        check=False,
    )

    assert result.returncode == 2
    assert "ERR_CODE: INVALID_FLAGS" in result.stderr
    assert "--base-url 必须和 --api-key 成对" in result.stderr


def test_install_rejects_provider_conflict_with_explicit_custom_flags(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = subprocess.run(
        [
            "bash",
            str(_INSTALL),
            "--provider",
            "minimax",
            "--base-url",
            "https://custom.api.invalid/v1",
            "--api-key",
            "sk-custom49",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
        },
        check=False,
    )

    assert result.returncode == 2
    assert "ERR_CODE: INVALID_FLAGS" in result.stderr
    assert "--base-url/--api-key 只能配 --provider custom_api 或不传 --provider" in result.stderr


def test_install_provider_minimax_with_api_key_auto_fills_base_url_and_model(tmp_path: Path) -> None:
    root, home, launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)
    agent_admin_log = tmp_path / "agent_admin.jsonl"
    iterm_payload_log = tmp_path / "iterm_payload.jsonl"
    _write_executable(
        root / "core" / "skills" / "memory-oracle" / "scripts" / "scan_environment.py",
        """#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
machine = Path(args.output) / "machine"
machine.mkdir(parents=True, exist_ok=True)
(machine / "credentials.json").write_text("{", encoding="utf-8")
for name in ("network", "openclaw", "github", "current_context"):
    (machine / f"{name}.json").write_text("{}", encoding="utf-8")
""",
    )

    result = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "install.sh"),
            "--project",
            "mini49",
            "--template",
            "clawseat-engineering",
            "--provider",
            "minimax",
            "--api-key",
            "sk-mini49",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PATH": f"{root.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": f"{py_stubs}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "PYTHON_BIN": sys.executable,
            "LOG_FILE": str(launcher_log),
            "TMUX_LOG_FILE": str(tmux_log),
            "AGENT_ADMIN_LOG": str(agent_admin_log),
            "ITERM_PAYLOAD_LOG": str(iterm_payload_log),
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Using forced provider: minimax" in result.stdout

    provider_env = (
        home / ".agents" / "tasks" / "mini49" / "memory-provider.env"
    ).read_text(encoding="utf-8")
    assert "ANTHROPIC_AUTH_TOKEN=sk-mini49" in provider_env
    assert "ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic" in provider_env
    assert "ANTHROPIC_MODEL=MiniMax-M2.7-highspeed" in provider_env

    records = _read_jsonl(launcher_log)
    assert [record["session"] for record in records] == ["mini49-memory-claude"]
    for record in records:
        assert record["custom_api_key_present"] is True
        assert record["custom_base_url"] == "https://api.minimaxi.com/anthropic"
        assert record["custom_model"] == "MiniMax-M2.7-highspeed"


def test_install_provider_anthropic_console_with_api_key_skips_detection(tmp_path: Path) -> None:
    root, home, launcher_log, tmux_log, py_stubs = _fake_install_root(tmp_path)
    agent_admin_log = tmp_path / "agent_admin.jsonl"
    iterm_payload_log = tmp_path / "iterm_payload.jsonl"
    _write_executable(
        root / "core" / "skills" / "memory-oracle" / "scripts" / "scan_environment.py",
        """#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
machine = Path(args.output) / "machine"
machine.mkdir(parents=True, exist_ok=True)
(machine / "credentials.json").write_text("{", encoding="utf-8")
for name in ("network", "openclaw", "github", "current_context"):
    (machine / f"{name}.json").write_text("{}", encoding="utf-8")
""",
    )

    result = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "install.sh"),
            "--project",
            "console49",
            "--template",
            "clawseat-engineering",
            "--provider",
            "anthropic_console",
            "--api-key",
            "sk-ant-49",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PATH": f"{root.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": f"{py_stubs}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "PYTHON_BIN": sys.executable,
            "LOG_FILE": str(launcher_log),
            "TMUX_LOG_FILE": str(tmux_log),
            "AGENT_ADMIN_LOG": str(agent_admin_log),
            "ITERM_PAYLOAD_LOG": str(iterm_payload_log),
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Using forced provider: anthropic_console" in result.stdout

    provider_env = (
        home / ".agents" / "tasks" / "console49" / "memory-provider.env"
    ).read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-ant-49" in provider_env
    assert "export ANTHROPIC_AUTH_TOKEN" not in provider_env

    records = _read_jsonl(launcher_log)
    assert [record["session"] for record in records] == ["console49-memory-claude"]
    for record in records:
        assert record["custom_api_key_present"] is True
        assert record["custom_base_url"] == "https://api.anthropic.com"


@pytest.mark.parametrize(
    "matched_session",
    [
        "spawn49-planner-claude",
        "spawn49-planner-codex",
        "spawn49-planner-gemini",
    ],
)
def test_wait_for_seat_attaches_when_matching_session_appears(tmp_path: Path, matched_session: str) -> None:
    bin_dir = tmp_path / "bin"
    count_file = tmp_path / "count.txt"
    sleep_count_file = tmp_path / "sleep-count.txt"
    attach_log = tmp_path / "attach.log"
    agentctl = tmp_path / "agentctl.sh"
    _write_executable(
        bin_dir / "tmux",
        """#!/usr/bin/env bash
set -euo pipefail
count_file="${TMUX_COUNT_FILE:?}"
attach_log="${TMUX_ATTACH_LOG:?}"
case "$1" in
  has-session)
    count=0
    if [[ -f "$count_file" ]]; then
      count="$(cat "$count_file")"
    fi
    count=$((count + 1))
    printf '%s' "$count" > "$count_file"
    if [[ "$count" -ge 2 && "$3" == "=${TMUX_MATCH_SESSION:?}" ]]; then
      exit 0
    fi
    exit 1
    ;;
  attach)
    printf '%s\\n' "$*" >> "$attach_log"
    ;;
esac
""",
    )
    _write_executable(
        bin_dir / "sleep",
        """#!/usr/bin/env bash
set -euo pipefail
count_file="${SLEEP_COUNT_FILE:?}"
count=0
if [[ -f "$count_file" ]]; then
  count="$(cat "$count_file")"
fi
count=$((count + 1))
printf '%s' "$count" > "$count_file"
if [[ "$count" -ge 2 ]]; then
  kill -TERM "$PPID"
fi
exit 0
""",
    )
    _write_executable(
        agentctl,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "session-name" ]]; then
  printf '%s\\n' "${TMUX_MATCH_SESSION:?}"
fi
""",
    )

    result = subprocess.run(
        ["bash", str(_WAIT_FOR_SEAT), "spawn49", "planner"],
        capture_output=True,
        text=True,
        timeout=5,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "AGENTCTL_BIN": str(agentctl),
            "WAIT_FOR_SEAT_POLL_SECONDS": "0.01",
            "TMUX_COUNT_FILE": str(count_file),
            "TMUX_ATTACH_LOG": str(attach_log),
            "TMUX_MATCH_SESSION": matched_session,
            "SLEEP_COUNT_FILE": str(sleep_count_file),
        },
        check=False,
    )

    assert result.returncode != 0
    assert "WARN:" not in result.stderr
    assert f"DETACHED from {matched_session}" in result.stdout
    attach_lines = attach_log.read_text(encoding="utf-8").splitlines()
    assert attach_lines
    assert all(line == f"attach -t ={matched_session}" for line in attach_lines)


def test_wait_for_seat_falls_back_to_fixed_tool_suffix_after_primary_budget(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    home = _write_engineer_profile(tmp_path, "planner", "gemini")
    sleep_count_file = tmp_path / "sleep-count.txt"
    attach_log = tmp_path / "attach.log"
    agentctl = tmp_path / "agentctl.sh"
    _write_executable(
        bin_dir / "tmux",
        """#!/usr/bin/env bash
set -euo pipefail
attach_log="${TMUX_ATTACH_LOG:?}"
case "$1" in
  has-session)
    if [[ "$3" == "=spawn49-planner-gemini" ]]; then
      exit 0
    fi
    exit 1
    ;;
  attach)
    printf '%s\\n' "$*" >> "$attach_log"
    ;;
esac
""",
    )
    _write_executable(
        agentctl,
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
    )
    _write_executable(
        bin_dir / "sleep",
        """#!/usr/bin/env bash
set -euo pipefail
count_file="${SLEEP_COUNT_FILE:?}"
count=0
if [[ -f "$count_file" ]]; then
  count="$(cat "$count_file")"
fi
count=$((count + 1))
printf '%s' "$count" > "$count_file"
if [[ "$count" -ge 2 ]]; then
  kill -TERM "$PPID"
fi
exit 0
""",
    )

    result = subprocess.run(
        ["bash", str(_WAIT_FOR_SEAT), "spawn49", "planner"],
        capture_output=True,
        text=True,
        timeout=5,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "AGENTS_ROOT": str(home / ".agents"),
            "AGENTCTL_BIN": str(agentctl),
            "WAIT_FOR_SEAT_POLL_SECONDS": "0.01",
            "WAIT_FOR_SEAT_PRIMARY_FAILURE_BUDGET": "1",
            "TMUX_ATTACH_LOG": str(attach_log),
            "SLEEP_COUNT_FILE": str(sleep_count_file),
        },
        check=False,
    )

    assert result.returncode != 0
    assert "WARN: agentctl resolution failed after 1 attempts; falling back to 'spawn49-planner-gemini'" in result.stderr
    assert "DETACHED from spawn49-planner-gemini" in result.stdout
    attach_lines = attach_log.read_text(encoding="utf-8").splitlines()
    assert attach_lines
    assert all(line == "attach -t =spawn49-planner-gemini" for line in attach_lines)


def test_wait_for_seat_does_not_fallback_to_base_session_without_canonical_resolution(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    home = _write_engineer_profile(tmp_path, "planner", "gemini")
    sleep_count_file = tmp_path / "sleep-count.txt"
    attach_log = tmp_path / "attach.log"
    agentctl = tmp_path / "agentctl.sh"
    _write_executable(
        bin_dir / "tmux",
        """#!/usr/bin/env bash
set -euo pipefail
attach_log="${TMUX_ATTACH_LOG:?}"
case "$1" in
  has-session)
    if [[ "$3" == "=spawn49-planner" ]]; then
      exit 0
    fi
    exit 1
    ;;
  attach)
    printf '%s\\n' "$*" >> "$attach_log"
    ;;
esac
""",
    )
    _write_executable(
        agentctl,
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
    )
    _write_executable(
        bin_dir / "sleep",
        """#!/usr/bin/env bash
set -euo pipefail
count_file="${SLEEP_COUNT_FILE:?}"
count=0
if [[ -f "$count_file" ]]; then
  count="$(cat "$count_file")"
fi
count=$((count + 1))
printf '%s' "$count" > "$count_file"
if [[ "$count" -ge 2 ]]; then
  kill -TERM "$PPID"
fi
exit 0
""",
    )

    result = subprocess.run(
        ["bash", str(_WAIT_FOR_SEAT), "spawn49", "planner"],
        capture_output=True,
        text=True,
        timeout=5,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "AGENTCTL_BIN": str(agentctl),
            "WAIT_FOR_SEAT_POLL_SECONDS": "0.01",
            "WAIT_FOR_SEAT_PRIMARY_FAILURE_BUDGET": "1",
            "TMUX_ATTACH_LOG": str(attach_log),
            "SLEEP_COUNT_FILE": str(sleep_count_file),
        },
        check=False,
    )

    assert result.returncode != 0
    assert "pane is waiting for spawn49-planner" in result.stdout
    assert not attach_log.exists()


def test_wait_for_seat_warns_periodically_when_primary_and_suffix_fallbacks_fail(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    home = _write_engineer_profile(tmp_path, "planner", "gemini")
    sleep_count_file = tmp_path / "sleep-count.txt"
    attach_log = tmp_path / "attach.log"
    agentctl = tmp_path / "agentctl.sh"
    _write_executable(
        bin_dir / "tmux",
        """#!/usr/bin/env bash
set -euo pipefail
attach_log="${TMUX_ATTACH_LOG:?}"
case "$1" in
  has-session)
    exit 1
    ;;
  attach)
    printf '%s\\n' "$*" >> "$attach_log"
    ;;
esac
""",
    )
    _write_executable(
        agentctl,
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
    )
    _write_executable(
        bin_dir / "sleep",
        """#!/usr/bin/env bash
set -euo pipefail
count_file="${SLEEP_COUNT_FILE:?}"
count=0
if [[ -f "$count_file" ]]; then
  count="$(cat "$count_file")"
fi
count=$((count + 1))
printf '%s' "$count" > "$count_file"
if [[ "$count" -ge 4 ]]; then
  kill -TERM "$PPID"
fi
exit 0
""",
    )

    result = subprocess.run(
        ["bash", str(_WAIT_FOR_SEAT), "spawn49", "planner"],
        capture_output=True,
        text=True,
        timeout=5,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "AGENTCTL_BIN": str(agentctl),
            "WAIT_FOR_SEAT_POLL_SECONDS": "0.01",
            "WAIT_FOR_SEAT_PRIMARY_FAILURE_BUDGET": "2",
            "WAIT_FOR_SEAT_DEGRADED_WARN_EVERY_POLLS": "2",
            "TMUX_ATTACH_LOG": str(attach_log),
            "SLEEP_COUNT_FILE": str(sleep_count_file),
        },
        check=False,
    )

    assert result.returncode != 0
    assert "pane is waiting for spawn49-planner" in result.stdout
    assert not attach_log.exists()
    assert result.stderr.count(
        "WARN: agentctl resolution still degraded for spawn49-planner"
    ) >= 2
