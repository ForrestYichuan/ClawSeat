from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

for path in (Path(__file__).resolve().parents[1] / "core" / "lib", Path(__file__).resolve().parents[1] / "core" / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from state import open_db


REPO = Path(__file__).resolve().parents[1]


def _write_tmux_stub(bin_dir: Path, log_path: Path) -> Path:
    tmux = bin_dir / "tmux"
    tmux.parent.mkdir(parents=True, exist_ok=True)
    tmux.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "%s\\n" "$*" >> "${TMUX_LOG:?}"',
                'case "$1" in',
                "  has-session) exit 0 ;;",
                "  kill-session) exit 0 ;;",
                "  *) exit 0 ;;",
                "esac",
                "",
            ]
        ),
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    return tmux


def _init_state_db(home: Path) -> Path:
    db_path = home / ".agents" / "state.db"
    conn = open_db(db_path=db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
          name TEXT PRIMARY KEY,
          feishu_group_id TEXT NOT NULL DEFAULT '',
          feishu_bot_account TEXT NOT NULL DEFAULT '',
          repo_root TEXT NOT NULL DEFAULT '',
          heartbeat_owner TEXT NOT NULL DEFAULT '',
          active_loop_owner TEXT NOT NULL DEFAULT '',
          bound_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seats (
          project TEXT NOT NULL,
          seat_id TEXT NOT NULL,
          role TEXT NOT NULL,
          tool TEXT NOT NULL,
          auth_mode TEXT NOT NULL,
          provider TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'unknown',
          last_heartbeat TEXT,
          session_name TEXT,
          workspace TEXT,
          PRIMARY KEY (project, seat_id)
        )
        """
    )
    conn.commit()
    return db_path


def _insert_project_and_seat(
    db_path: Path,
    *,
    project: str,
    seat_id: str,
    session_name: str,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO projects (name) VALUES (?)",
        (project,),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO seats (
          project, seat_id, role, tool, auth_mode, provider, status, last_heartbeat, session_name, workspace
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project,
            seat_id,
            "builder",
            "claude",
            "api",
            "minimax",
            "live",
            "2026-05-13T00:00:00Z",
            session_name,
            f"/workspace/{project}/{seat_id}",
        ),
    )
    conn.commit()
    conn.close()


def test_delete_seat_script_removes_db_row_and_session_dir(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    db_path = _init_state_db(home)
    project = "project-a"
    seat_id = "seat-a"
    session_name = f"{project}-{seat_id}-claude"
    _insert_project_and_seat(db_path, project=project, seat_id=seat_id, session_name=session_name)

    seat_dir = home / ".agents" / "sessions" / project / seat_id
    seat_dir.mkdir(parents=True, exist_ok=True)
    (seat_dir / "session.toml").write_text(
        f'provider = "minimax"\nsession = "{session_name}"\n',
        encoding="utf-8",
    )
    tmux_log = tmp_path / "tmux.log"
    tmux_bin = _write_tmux_stub(tmp_path / "bin", tmux_log)

    result = subprocess.run(
        ["bash", str(REPO / "scripts" / "delete-seat.sh"), project, seat_id],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
            "TMUX_BIN": str(tmux_bin),
            "TMUX_LOG": str(tmux_log),
        },
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["seat_id"] == seat_id
    assert payload["killed_tmux"] is True
    assert payload["removed_rows"] == 1
    assert str(seat_dir) in payload["removed_files"]
    assert not seat_dir.exists()

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT 1 FROM seats WHERE project = ? AND seat_id = ?",
        (project, seat_id),
    ).fetchone()
    conn.close()
    assert row is None
    assert "has-session -t =project-a-seat-a-claude" in tmux_log.read_text(encoding="utf-8")
    assert "kill-session -t =project-a-seat-a-claude" in tmux_log.read_text(encoding="utf-8")


def test_delete_project_script_removes_project_tree_and_all_seats(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    db_path = _init_state_db(home)
    project = "project-b"
    seats = [("seat-a", f"{project}-seat-a-claude"), ("seat-b", f"{project}-seat-b-claude")]
    for seat_id, session_name in seats:
        _insert_project_and_seat(db_path, project=project, seat_id=seat_id, session_name=session_name)
        seat_dir = home / ".agents" / "sessions" / project / seat_id
        seat_dir.mkdir(parents=True, exist_ok=True)
        (seat_dir / "session.toml").write_text(
            f'provider = "minimax"\nsession = "{session_name}"\n',
            encoding="utf-8",
        )
    project_sessions_dir = home / ".agents" / "sessions" / project
    project_dir = home / ".agents" / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)

    tmux_log = tmp_path / "tmux.log"
    tmux_bin = _write_tmux_stub(tmp_path / "bin", tmux_log)

    result = subprocess.run(
        ["bash", str(REPO / "scripts" / "delete-project.sh"), project],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
            "TMUX_BIN": str(tmux_bin),
            "TMUX_LOG": str(tmux_log),
        },
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["project"] == project
    assert sorted(item["seat_id"] for item in payload["deleted_seats"]) == ["seat-a", "seat-b"]
    assert str(project_sessions_dir) in payload["removed_files"]
    assert str(project_dir) in payload["removed_files"]
    assert not project_sessions_dir.exists()
    assert not project_dir.exists()

    conn = sqlite3.connect(str(db_path))
    project_row = conn.execute("SELECT 1 FROM projects WHERE name = ?", (project,)).fetchone()
    seat_rows = conn.execute("SELECT 1 FROM seats WHERE project = ?", (project,)).fetchone()
    conn.close()
    assert project_row is None
    assert seat_rows is None

    tmux_log_text = tmux_log.read_text(encoding="utf-8")
    assert tmux_log_text.count("kill-session") == 2
