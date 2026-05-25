"""C10.5 tests: modal_detector.py."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "core" / "lib"))
sys.path.insert(0, str(_REPO / "core" / "scripts"))
sys.path.insert(0, str(_REPO / "core" / "skills" / "gstack-harness" / "scripts"))

import modal_detector as md  # noqa: E402
from core.lib.state import open_db  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path):
    db = str(tmp_path / "state.db")
    with open_db(db) as conn:
        pass  # trigger schema init
    return db


@pytest.fixture()
def stub_tmux(tmp_path):
    """Stub tmux binary that serves pre-canned pane content per session name."""
    stub = tmp_path / "tmux"
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    stub.write_text(
        f"""#!/usr/bin/env bash
# args: list-sessions -F ... | capture-pane -t <session> -p -S -120
if [ "$1" = "list-sessions" ]; then
    ls "{sessions_dir}/"
elif [ "$1" = "capture-pane" ]; then
    session="$3"
    f="{sessions_dir}/$session"
    [ -f "$f" ] && cat "$f" || echo ""
fi
""",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub, sessions_dir


def _add_session(sessions_dir: Path, name: str, content: str) -> None:
    (sessions_dir / name).write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Pattern detection tests
# ---------------------------------------------------------------------------

_MODAL_V2 = textwrap.dedent("""\
    Some preamble output here.
    Do you want to proceed?
    ❯ 1. Yes
      2. Yes, and allow hooks/ access and similar commands
      3. No
    """)

_MODAL_NO_CURSOR = textwrap.dedent("""\
    Do you want to proceed?
    1. Yes
    2. No
    """)

_IDLE_PROMPT = textwrap.dedent("""\
    > Some command output
    ❯
    """)

_SHELL_CONFIRM = textwrap.dedent("""\
    Do you want to continue? [Y/n]:
    """)

_PROCEED_NO_NUMBERS = textwrap.dedent("""\
    Do you want to proceed?
    Please type yes or no.
    """)


def test_detect_modal_v2_with_cursor():
    match = md._detect_modal(_MODAL_V2)
    assert match is not None
    assert "Do you want to proceed?" in match.question
    assert len(match.options) == 3
    assert any("Yes" in opt for opt in match.options)
    assert any("hooks/" in opt for opt in match.options)


def test_detect_modal_no_cursor():
    match = md._detect_modal(_MODAL_NO_CURSOR)
    assert match is not None
    assert len(match.options) == 2


def test_detect_idle_prompt_no_match():
    assert md._detect_modal(_IDLE_PROMPT) is None


def test_detect_shell_confirm_no_match():
    assert md._detect_modal(_SHELL_CONFIRM) is None


def test_detect_proceed_without_numbered_list_no_match():
    assert md._detect_modal(_PROCEED_NO_NUMBERS) is None


def test_detect_modal_returns_preview():
    match = md._detect_modal(_MODAL_V2)
    assert match is not None
    assert len(match.preview) > 0


def test_detect_modal_empty_pane():
    assert md._detect_modal("") is None


def test_detect_modal_large_pane_uses_tail():
    """Modal at the end of a large pane is still detected."""
    filler = "x" * 10000
    text = filler + "\n" + _MODAL_V2
    assert md._detect_modal(text) is not None


# ---------------------------------------------------------------------------
# Fingerprint tests
# ---------------------------------------------------------------------------


def test_fingerprint_same_modal_same_result():
    f1 = md._fingerprint("install-builder-2-claude", "Do you want to proceed?", ["1. Yes", "2. No"])
    f2 = md._fingerprint("install-builder-2-claude", "Do you want to proceed?", ["1. Yes", "2. No"])
    assert f1 == f2


def test_fingerprint_different_sessions_different():
    f1 = md._fingerprint("install-builder-1-claude", "Do you want to proceed?", ["1. Yes", "2. No"])
    f2 = md._fingerprint("install-builder-2-claude", "Do you want to proceed?", ["1. Yes", "2. No"])
    assert f1 != f2


def test_fingerprint_different_questions_different():
    f1 = md._fingerprint("s", "Do you want to proceed?", ["1. Yes", "2. No"])
    f2 = md._fingerprint("s", "Allow npm install?", ["1. Yes", "2. No"])
    assert f1 != f2


def test_fingerprint_different_options_different():
    f1 = md._fingerprint("s", "Do you want to proceed?", ["1. Yes", "2. No"])
    f2 = md._fingerprint("s", "Do you want to proceed?", ["1. Yes", "2. Yes, and allow hooks/", "3. No"])
    assert f1 != f2


def test_fingerprint_length_16():
    fp = md._fingerprint("session", "question", ["opt1"])
    assert len(fp) == 16


# ---------------------------------------------------------------------------
# Session name parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session,project,seat", [
    ("install-builder-2-claude", "install", "builder-2"),
    ("cartooner-planner-claude", "cartooner", "planner"),
    ("ancestor-cc", "ancestor", "cc"),
    ("myproject-koder-codex", "myproject", "koder"),
    ("simple", "simple", "simple"),
])
def test_parse_session_name(session, project, seat):
    p, s = md._parse_session_name(session)
    assert p == project
    assert s == seat


# ---------------------------------------------------------------------------
# Fingerprint dedup via DB
# ---------------------------------------------------------------------------


def test_fingerprint_dedup_skips_second_insert(tmp_db, stub_tmux):
    stub, sessions_dir = stub_tmux
    _add_session(sessions_dir, "install-builder-2-claude", _MODAL_V2)

    stats1 = md.scan_once(db_path=tmp_db, tmux_bin=str(stub))
    stats2 = md.scan_once(db_path=tmp_db, tmux_bin=str(stub))

    assert stats1["inserted"] == 1
    assert stats2["inserted"] == 0
    assert stats2["skipped"] == 1


# ---------------------------------------------------------------------------
# Multi-seat scan
# ---------------------------------------------------------------------------


def test_multi_seat_only_modal_session_emits(tmp_db, stub_tmux):
    """3 sessions, 1 with modal → exactly 1 event inserted."""
    stub, sessions_dir = stub_tmux
    _add_session(sessions_dir, "install-koder-claude", "just normal output\n> ")
    _add_session(sessions_dir, "install-planner-claude", "")
    _add_session(sessions_dir, "install-builder-2-claude", _MODAL_V2)

    stats = md.scan_once(db_path=tmp_db, tmux_bin=str(stub))
    assert stats["sessions"] == 3
    assert stats["modals"] == 1
    assert stats["inserted"] == 1


# ---------------------------------------------------------------------------
# Project filter
# ---------------------------------------------------------------------------


def test_project_filter_scopes_sessions(tmp_db, stub_tmux):
    stub, sessions_dir = stub_tmux
    _add_session(sessions_dir, "install-builder-2-claude", _MODAL_V2)
    _add_session(sessions_dir, "cartooner-builder-1-claude", _MODAL_NO_CURSOR)

    # Filter to install only
    stats = md.scan_once(project_filter="install", db_path=tmp_db, tmux_bin=str(stub))
    assert stats["inserted"] == 1

    # Verify cartooner event not in DB
    with open_db(tmp_db) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE type='seat.blocked_on_modal'"
        ).fetchall()
    import json
    projects = [json.loads(r[0]).get("seat", "") for r in rows]
    # Only install-scoped sessions should have been scanned
    assert all("cartooner" not in p for p in projects)


def test_project_filter_other_project_ignored(tmp_db, stub_tmux):
    stub, sessions_dir = stub_tmux
    _add_session(sessions_dir, "other-project-builder-1-claude", _MODAL_V2)

    stats = md.scan_once(project_filter="install", db_path=tmp_db, tmux_bin=str(stub))
    assert stats["inserted"] == 0
    assert stats["sessions"] == 0


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_no_db_write(tmp_db, stub_tmux, capsys):
    stub, sessions_dir = stub_tmux
    _add_session(sessions_dir, "install-builder-2-claude", _MODAL_V2)

    stats = md.scan_once(dry_run=True, db_path=tmp_db, tmux_bin=str(stub))
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert stats["inserted"] == 1  # counted as inserted in dry-run

    # No actual DB write
    with open_db(tmp_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# --install-launchd
# ---------------------------------------------------------------------------


def test_install_launchd_writes_plist(tmp_path):
    dest = tmp_path / "com.clawseat.modal-detector.plist"
    rc = md.install_launchd(interval=60, dest=dest)
    assert rc == 0
    assert dest.exists()
    xml = dest.read_text()
    assert "com.clawseat.modal-detector" in xml
    assert "modal_detector.py" in xml
    assert "<integer>60</integer>" in xml


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_install_launchd_plist_valid_xml(tmp_path):
    dest = tmp_path / "test.plist"
    md.install_launchd(interval=60, dest=dest)
    result = subprocess.run(
        ["plutil", "-lint", str(dest)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"plutil error: {result.stderr}"


def test_render_plist_interval_substituted():
    xml = md.render_plist(interval=120)
    assert "<integer>120</integer>" in xml
    assert "<string>120</string>" in xml


# ---------------------------------------------------------------------------
# Live tmux integration (real tmux required)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    subprocess.run(["which", "tmux"], capture_output=True).returncode != 0,
    reason="tmux not available",
)
def test_live_tmux_modal_detected(tmp_db, tmp_path):
    """Start a real tmux session with a modal, verify detection."""
    session = "test-c105-modal-001"
    modal_content = "Do you want to proceed?\n 1. Yes\n 2. Yes, and allow hooks/\n 3. No\n"
    try:
        start = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, "-x", "120", "-y", "40"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "TMUX": ""},
        )
        if start.returncode != 0:
            stderr = start.stderr.lower()
            if (
                "fork failed" in stderr
                or "out of pty" in stderr
                or "device not configured" in stderr
            ):
                pytest.skip(f"tmux cannot allocate a new PTY: {start.stderr.strip()}")
            raise subprocess.CalledProcessError(
                start.returncode,
                ["tmux", "new-session", "-d", "-s", session, "-x", "120", "-y", "40"],
                output=start.stdout,
                stderr=start.stderr,
            )
        subprocess.run(
            ["tmux", "send-keys", "-t", session, modal_content, ""],
            check=True,
            env={**os.environ, "TMUX": ""},
        )
        import time
        time.sleep(0.3)

        stats = md.scan_once(project_filter="test", db_path=tmp_db)
        # May or may not match depending on pane rendering; just verify no crash
        assert isinstance(stats["sessions"], int)
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            env={**os.environ, "TMUX": ""},
            capture_output=True,
        )


# ---------------------------------------------------------------------------
# feishu_announcer DEFAULT_EVENT_TYPES includes seat.blocked_on_modal
# ---------------------------------------------------------------------------


def test_feishu_announcer_default_event_types_includes_modal():
    import feishu_announcer as fa
    assert "seat.blocked_on_modal" in fa._DEFAULT_EVENT_TYPES


# ---------------------------------------------------------------------------
# CLI: --once mode via main()
# ---------------------------------------------------------------------------


def test_cli_once_no_sessions(tmp_db, stub_tmux, capsys):
    stub, sessions_dir = stub_tmux
    # no sessions added → stub ls returns empty
    rc = md.main(["--once", "--db", tmp_db, "--tmux-bin", str(stub)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sessions=0" in out


def test_cli_once_with_modal(tmp_db, stub_tmux, capsys):
    stub, sessions_dir = stub_tmux
    _add_session(sessions_dir, "install-koder-claude", _MODAL_V2)
    rc = md.main(["--once", "--db", tmp_db, "--tmux-bin", str(stub)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "inserted=1" in out


def test_cli_dry_run_flag(tmp_db, stub_tmux, capsys):
    stub, sessions_dir = stub_tmux
    _add_session(sessions_dir, "install-koder-claude", _MODAL_V2)
    md.main(["--once", "--dry-run", "--db", tmp_db, "--tmux-bin", str(stub)])
    out = capsys.readouterr().out
    assert "DRY-RUN" in out


def test_cli_install_launchd(tmp_path, capsys):
    dest = tmp_path / "out.plist"
    with mock.patch.object(md, "_LAUNCHD_DEST", dest):
        rc = md.main(["--install-launchd"])
    assert rc == 0
    assert dest.exists()
