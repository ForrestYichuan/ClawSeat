#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_LIB = REPO_ROOT / "core" / "lib"
if str(CORE_LIB) not in sys.path:
    sys.path.insert(0, str(CORE_LIB))

try:
    from real_home import real_user_home
except ImportError:  # pragma: no cover
    real_user_home = Path.home  # type: ignore[assignment]


def query_seat_liveness(project: str, max_age_seconds: int = 300) -> list[dict[str, str]]:
    """Return fresh, alive seats for *project* from state.db.

    Preferred schema is ``seat_liveness`` with ``status='alive'`` and
    ``last_heartbeat_ts``. Current installs also expose the same facts in the
    ``seats`` table with ``status='live'``; that schema is read as a
    compatibility fallback and normalized to ``status='alive'``.
    """
    for db_path in _state_db_candidates(project):
        rows = _read_alive_rows(db_path, project, max_age_seconds)
        if rows:
            return rows
    return []


def restart_seat(project: str, role: str, timeout_seconds: int = 60) -> bool:
    """Start *role* for *project* and wait for a fresh heartbeat."""
    command = [
        sys.executable,
        str(REPO_ROOT / "core" / "scripts" / "agent_admin.py"),
        "window",
        "open-engineer",
        role,
        "--project",
        project,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        for seat in query_seat_liveness(project):
            if seat.get("role") == role:
                return True
        time.sleep(2)
    return False


def _state_db_candidates(project: str) -> list[Path]:
    explicit = os.environ.get("CLAWSEAT_STATE_DB", "").strip()
    if explicit:
        return [Path(explicit).expanduser()]

    home = real_user_home()
    candidates: list[Path] = []
    workspaces = home / ".agents" / "workspaces" / project
    for seat_id in ("memory", "ancestor", "planner"):
        candidates.append(workspaces / seat_id / "state.db")
    if workspaces.exists():
        candidates.extend(sorted(workspaces.glob("*/state.db")))
    candidates.append(home / ".agents" / "state.db")
    return _unique_paths(candidates)


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        unique.append(Path(key))
    return unique


def _read_alive_rows(db_path: Path, project: str, max_age_seconds: int) -> list[dict[str, str]]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        rows = _query_seat_liveness_table(conn, project, max_age_seconds)
        if rows:
            return rows
        return _query_seats_table(conn, project, max_age_seconds)
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _query_seat_liveness_table(
    conn: sqlite3.Connection,
    project: str,
    max_age_seconds: int,
) -> list[dict[str, str]]:
    if not _has_table(conn, "seat_liveness"):
        return []
    columns = _columns(conn, "seat_liveness")
    heartbeat_col = "last_heartbeat_ts" if "last_heartbeat_ts" in columns else "last_heartbeat"
    if heartbeat_col not in columns or "role" not in columns or "status" not in columns:
        return []

    select = ["role", "status", heartbeat_col]
    order_session_col = ""
    if "session_name" in columns:
        select.append("session_name")
        order_session_col = "session_name"
    elif "seat_id" in columns:
        order_session_col = "seat_id"
    else:
        return []
    if "seat_id" in columns:
        select.append("seat_id")

    query = f"SELECT {', '.join(select)} FROM seat_liveness WHERE status = 'alive'"
    params: list[Any] = []
    if "project" in columns:
        query += " AND project = ?"
        params.append(project)
    query += f" ORDER BY role, {order_session_col}"
    rows = conn.execute(query, params).fetchall()
    return [
        _normalize_row(row, heartbeat_col=heartbeat_col)
        for row in rows
        if _is_fresh(row[heartbeat_col], max_age_seconds)
    ]


def _query_seats_table(
    conn: sqlite3.Connection,
    project: str,
    max_age_seconds: int,
) -> list[dict[str, str]]:
    if not _has_table(conn, "seats"):
        return []
    rows = conn.execute(
        """
        SELECT seat_id, role, session_name, status, last_heartbeat
        FROM seats
        WHERE project = ?
          AND status IN ('alive', 'live')
        ORDER BY role, session_name
        """,
        (project,),
    ).fetchall()
    return [
        _normalize_row(row, heartbeat_col="last_heartbeat")
        for row in rows
        if _is_fresh(row["last_heartbeat"], max_age_seconds)
    ]


def _normalize_row(
    row: sqlite3.Row,
    *,
    heartbeat_col: str,
) -> dict[str, str]:
    heartbeat = str(row[heartbeat_col] or "")
    session_name = row["session_name"] if "session_name" in row.keys() else row["seat_id"]
    normalized = {
        "role": str(row["role"]),
        "session_name": str(session_name),
        "status": "alive",
        "last_heartbeat_ts": heartbeat,
    }
    if "seat_id" in row.keys():
        normalized["seat_id"] = str(row["seat_id"])
    return normalized


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _is_fresh(value: Any, max_age_seconds: int) -> bool:
    heartbeat = _parse_timestamp(value)
    if heartbeat is None:
        return False
    return (datetime.now(timezone.utc) - heartbeat).total_seconds() <= max_age_seconds


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    import json

    project_arg = sys.argv[1] if len(sys.argv) > 1 else "install"
    print(json.dumps(query_seat_liveness(project_arg), ensure_ascii=False, indent=2))
