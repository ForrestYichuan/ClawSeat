"""Tests for the simplified send-and-verify.sh (fire-and-forget + 3 Enter)."""
from __future__ import annotations

import io
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "core/shell-scripts/send-and-verify.sh"
pytestmark = pytest.mark.skipif(not shutil.which("tmux"), reason="tmux not available")

_UTILS_DIR = str(Path(__file__).resolve().parents[1] / "core/skills/gstack-harness/scripts")


def _mock_agentctl(tmp: Path) -> str:
    """Write a mock agentctl that returns its first non-flag argument as the session name."""
    p = tmp / "mock_agentctl.sh"
    p.write_text(
        "#!/usr/bin/env bash\n"
        "# mock: shift past 'session-name', skip --project <val>, echo remaining arg\n"
        "shift  # skip 'session-name'\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in --project) shift 2 ;; *) echo \"$1\"; exit 0 ;; esac\n"
        "done\n"
        "exit 0\n"
    )
    p.chmod(0o755)
    return str(p)


def _mk_session(name: str) -> None:
    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    stderr = result.stderr.lower()
    if (
        "fork failed" in stderr
        or "out of pty" in stderr
        or "device not configured" in stderr
    ):
        pytest.skip(f"tmux cannot allocate a new PTY: {result.stderr.strip()}")
    raise subprocess.CalledProcessError(
        result.returncode,
        ["tmux", "new-session", "-d", "-s", name],
        output=result.stdout,
        stderr=result.stderr,
    )


def _kill_session(name: str) -> None:
    subprocess.run(["tmux", "kill-session", "-t", name], check=False)


def _run(args: list[str], extra_env: dict | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run([str(SCRIPT)] + args, capture_output=True, text=True, env=env)


def test_bash_syntax_check():
    """Script must pass bash -n syntax check."""
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_sent_log_format_on_success(tmp_path, isolated_tasks_dir):
    """Successful send outputs 'SENT: <session>' and exits 0."""
    sess = "test-sent-log-success"
    mock_ctl = _mock_agentctl(tmp_path)
    _mk_session(sess)
    try:
        result = _run([sess, "hello world"], {"AGENTCTL_BIN": mock_ctl})
        assert result.returncode == 0, result.stderr
        assert f"SENT: {sess}" in result.stdout
    finally:
        _kill_session(sess)


def test_session_dead_returns_exit_1(tmp_path, isolated_tasks_dir):
    """Non-existent tmux session → SESSION_DEAD + rc=1."""
    sess = "nonexistent-session-xyz-dead-12345"
    mock_ctl = _mock_agentctl(tmp_path)
    subprocess.run(["tmux", "kill-session", "-t", sess], check=False)
    result = _run([sess, "hi"], {"AGENTCTL_BIN": mock_ctl})
    assert result.returncode == 1
    assert "SESSION_DEAD" in result.stdout


def test_session_not_found_returns_exit_1():
    """Missing session argument → usage error, rc=1."""
    result = _run([])
    assert result.returncode == 1


def test_emoji_message(tmp_path, isolated_tasks_dir):
    """Emoji message sends successfully."""
    sess = "test-emoji-msg"
    mock_ctl = _mock_agentctl(tmp_path)
    _mk_session(sess)
    try:
        result = _run([sess, "🚀 deploy ok"], {"AGENTCTL_BIN": mock_ctl})
        assert result.returncode == 0, result.stderr
        assert f"SENT: {sess}" in result.stdout
    finally:
        _kill_session(sess)


def test_chinese_message(tmp_path, isolated_tasks_dir):
    """Chinese characters send successfully."""
    sess = "test-chinese-msg"
    mock_ctl = _mock_agentctl(tmp_path)
    _mk_session(sess)
    try:
        result = _run([sess, "中文消息测试"], {"AGENTCTL_BIN": mock_ctl})
        assert result.returncode == 0, result.stderr
        assert f"SENT: {sess}" in result.stdout
    finally:
        _kill_session(sess)


def test_newline_message(tmp_path, isolated_tasks_dir):
    """Multi-line message sends successfully."""
    sess = "test-newline-msg"
    mock_ctl = _mock_agentctl(tmp_path)
    _mk_session(sess)
    try:
        result = _run([sess, "line1\nline2"], {"AGENTCTL_BIN": mock_ctl})
        assert result.returncode == 0, result.stderr
        assert f"SENT: {sess}" in result.stdout
    finally:
        _kill_session(sess)


def test_long_message_1kb(tmp_path, isolated_tasks_dir):
    """1024-byte message sends successfully."""
    sess = "test-long-msg"
    mock_ctl = _mock_agentctl(tmp_path)
    _mk_session(sess)
    try:
        result = _run([sess, "x" * 1024], {"AGENTCTL_BIN": mock_ctl})
        assert result.returncode == 0, result.stderr
        assert f"SENT: {sess}" in result.stdout
    finally:
        _kill_session(sess)


def _send_worker(args: tuple[str, str, str]) -> tuple[int, str]:
    sess, msg, mock_ctl = args
    env = os.environ.copy()
    env["AGENTCTL_BIN"] = mock_ctl
    r = subprocess.run(
        [str(SCRIPT), sess, msg], capture_output=True, text=True, env=env
    )
    return r.returncode, r.stdout


def test_concurrent_sends_different_sessions(tmp_path, isolated_tasks_dir):
    """Two concurrent sends to different sessions both succeed."""
    s1, s2 = "test-concurrent-a", "test-concurrent-b"
    mock_ctl = _mock_agentctl(tmp_path)
    _mk_session(s1)
    _mk_session(s2)
    try:
        with multiprocessing.Pool(2) as pool:
            results = pool.map(_send_worker, [(s1, "msg-a", mock_ctl), (s2, "msg-b", mock_ctl)])
        for rc, out in results:
            assert rc == 0
            assert "SENT:" in out
    finally:
        _kill_session(s1)
        _kill_session(s2)


def test_project_flag_routing(tmp_path):
    """--project flag routes through mock agentctl and resolves correctly."""
    sess = "test-proj-routing"
    mock_ctl = _mock_agentctl(tmp_path)
    _mk_session(sess)
    try:
        result = _run(["--project", "myproj", sess, "hello"], {"AGENTCTL_BIN": mock_ctl})
        assert result.returncode == 0, result.stderr
        assert f"SENT: {sess}" in result.stdout
    finally:
        _kill_session(sess)


def test_require_success_allow_skip_exit_2_warns_not_raises():
    """require_success_allow_skip with rc=2 prints warn to stderr and does NOT raise."""
    if _UTILS_DIR not in sys.path:
        sys.path.insert(0, _UTILS_DIR)
    from _utils import require_success_allow_skip

    fake = MagicMock()
    fake.returncode = 2
    fake.stderr = "skipped by peer"
    fake.stdout = ""
    buf = io.StringIO()
    with mock.patch("sys.stderr", buf):
        require_success_allow_skip(fake, "send-op")
    assert "warn:" in buf.getvalue()
    assert "skipped" in buf.getvalue()


def test_require_success_exit_2_now_raises():
    """require_success with rc=2 now raises RuntimeError (strict semantics)."""
    if _UTILS_DIR not in sys.path:
        sys.path.insert(0, _UTILS_DIR)
    from _utils import require_success

    fake = MagicMock()
    fake.returncode = 2
    fake.stderr = ""
    fake.stdout = ""
    with pytest.raises(RuntimeError) as exc_info:
        require_success(fake, "send-op")
    assert "exit 2" in str(exc_info.value)


def test_require_success_exit_1_still_raises():
    """require_success with rc=1 raises RuntimeError."""
    if _UTILS_DIR not in sys.path:
        sys.path.insert(0, _UTILS_DIR)
    from _utils import require_success

    fake = MagicMock()
    fake.returncode = 1
    fake.stderr = "something broke"
    fake.stdout = ""
    with pytest.raises(RuntimeError):
        require_success(fake, "send-op")


def test_require_success_allow_skip_exit_1_still_raises():
    """require_success_allow_skip with rc=1 also raises RuntimeError."""
    if _UTILS_DIR not in sys.path:
        sys.path.insert(0, _UTILS_DIR)
    from _utils import require_success_allow_skip

    fake = MagicMock()
    fake.returncode = 1
    fake.stderr = "something broke"
    fake.stdout = ""
    with pytest.raises(RuntimeError):
        require_success_allow_skip(fake, "send-op")
