#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

_CORE_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_CORE_LIB) not in sys.path:
    sys.path.insert(0, str(_CORE_LIB))

from _toml_compat import load_safe as _toml_load_safe  # noqa: E402

from real_home import real_user_home  # noqa: E402


def _home() -> Path:
    return real_user_home()


def _state_db_path(home: Path) -> Path:
    return home / ".agents" / "state.db"


def _connect_db_if_exists(home: Path) -> sqlite3.Connection | None:
    db_path = _state_db_path(home)
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _session_dir(home: Path, project: str, seat_id: str) -> Path:
    return home / ".agents" / "sessions" / project / seat_id


def _project_sessions_dir(home: Path, project: str) -> Path:
    return home / ".agents" / "sessions" / project


def _project_dir(home: Path, project: str) -> Path:
    return home / ".agents" / "projects" / project


def _tmux_has_session(tmux_bin: str, session_name: str) -> bool:
    if not tmux_bin:
        return False
    try:
        result = subprocess.run(
            [tmux_bin, "has-session", "-t", f"={session_name}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _tmux_kill_session(tmux_bin: str, session_name: str) -> bool:
    if not tmux_bin:
        return False
    try:
        result = subprocess.run(
            [tmux_bin, "kill-session", "-t", f"={session_name}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"tmux kill-session failed for {session_name}")
    return True


def _seat_session_name(conn: sqlite3.Connection | None, home: Path, project: str, seat_id: str) -> str:
    if conn is not None:
        row = conn.execute(
            "SELECT session_name FROM seats WHERE project = ? AND seat_id = ?",
            (project, seat_id),
        ).fetchone()
        if row and str(row["session_name"] or "").strip():
            return str(row["session_name"]).strip()
    session_toml = _session_dir(home, project, seat_id) / "session.toml"
    if session_toml.is_file():
        try:
            with session_toml.open("rb") as fh:
                data = _toml_load_safe(fh)
            session_name = str(data.get("session", "")).strip()
            if session_name:
                return session_name
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _seat_exists(conn: sqlite3.Connection | None, home: Path, project: str, seat_id: str) -> bool:
    if conn is not None:
        row = conn.execute(
            "SELECT 1 FROM seats WHERE project = ? AND seat_id = ?",
            (project, seat_id),
        ).fetchone()
        if row is not None:
            return True
    if _session_dir(home, project, seat_id).exists():
        return True
    session_name = _seat_session_name(conn, home, project, seat_id)
    if session_name and _tmux_has_session(os.environ.get("TMUX_BIN", "tmux"), session_name):
        return True
    return False


def _project_exists(conn: sqlite3.Connection | None, home: Path, project: str) -> bool:
    if conn is not None:
        row = conn.execute("SELECT 1 FROM projects WHERE name = ?", (project,)).fetchone()
        if row is not None:
            return True
        row = conn.execute("SELECT 1 FROM seats WHERE project = ? LIMIT 1", (project,)).fetchone()
        if row is not None:
            return True
    if _project_sessions_dir(home, project).exists():
        return True
    if _project_dir(home, project).exists():
        return True
    return False


def _delete_path(path: Path, removed: list[str]) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    removed.append(str(path))


def delete_seat(
    project: str,
    seat_id: str,
    *,
    force: bool = False,
    tmux_bin: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    home = home if home is not None else _home()
    tmux_bin = (tmux_bin or os.environ.get("TMUX_BIN", "tmux")).strip()
    conn = _connect_db_if_exists(home)
    session_name = _seat_session_name(conn, home, project, seat_id)
    exists = _seat_exists(conn, home, project, seat_id)
    if not exists and not force:
        raise FileNotFoundError(f"seat not found: {project}/{seat_id}")

    killed_tmux = False
    if session_name and _tmux_has_session(tmux_bin, session_name):
        killed_tmux = _tmux_kill_session(tmux_bin, session_name)

    removed_rows = 0
    removed_files: list[str] = []
    seat_dir = _session_dir(home, project, seat_id)
    if conn is not None:
        cur = conn.execute(
            "DELETE FROM seats WHERE project = ? AND seat_id = ?",
            (project, seat_id),
        )
        removed_rows = int(cur.rowcount or 0)
        conn.commit()
    _delete_path(seat_dir, removed_files)
    if conn is not None:
        conn.close()
    return {
        "ok": True,
        "seat_id": seat_id,
        "project": project,
        "killed_tmux": killed_tmux,
        "removed_rows": removed_rows,
        "removed_files": removed_files,
    }


def delete_project(
    project: str,
    *,
    force: bool = False,
    tmux_bin: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    home = home if home is not None else _home()
    tmux_bin = (tmux_bin or os.environ.get("TMUX_BIN", "tmux")).strip()
    conn = _connect_db_if_exists(home)
    exists = _project_exists(conn, home, project)
    if not exists and not force:
        raise FileNotFoundError(f"project not found: {project}")

    seat_ids: set[str] = set()
    if conn is not None:
        rows = conn.execute(
            "SELECT seat_id FROM seats WHERE project = ? ORDER BY seat_id",
            (project,),
        ).fetchall()
        seat_ids.update(str(row["seat_id"]) for row in rows)
    sessions_dir = _project_sessions_dir(home, project)
    if sessions_dir.is_dir():
        seat_ids.update(
            path.name
            for path in sessions_dir.iterdir()
            if path.is_dir()
        )

    deleted_seats: list[dict[str, Any]] = []
    for seat_id in sorted(seat_ids):
        try:
            deleted_seats.append(
                delete_seat(
                    project,
                    seat_id,
                    force=True,
                    tmux_bin=tmux_bin,
                    home=home,
                )
            )
        except FileNotFoundError:
            if force:
                deleted_seats.append(
                    {
                        "seat_id": seat_id,
                        "project": project,
                        "killed_tmux": False,
                        "removed_rows": 0,
                        "removed_files": [],
                    }
                )
                continue
            raise

    removed_files: list[str] = []
    if conn is not None:
        conn.execute("DELETE FROM projects WHERE name = ?", (project,))
        conn.commit()
        conn.close()
    _delete_path(sessions_dir, removed_files)
    _delete_path(_project_dir(home, project), removed_files)

    combined_removed_files = sorted({*removed_files, *(item for seat in deleted_seats for item in seat["removed_files"])})
    return {
        "project": project,
        "ok": True,
        "deleted_seats": deleted_seats,
        "removed_files": combined_removed_files,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hard_delete")
    sub = parser.add_subparsers(dest="command", required=True)

    seat = sub.add_parser("seat")
    seat.add_argument("project")
    seat.add_argument("seat_id")
    seat.add_argument("--force", action="store_true")

    project = sub.add_parser("project")
    project.add_argument("project")
    project.add_argument("--force", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "seat":
            payload = delete_seat(args.project, args.seat_id, force=bool(args.force))
        else:
            payload = delete_project(args.project, force=bool(args.force))
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - wrapper should stay small and explicit
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
