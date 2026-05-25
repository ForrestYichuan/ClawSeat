"""state.py — C8 SQLite ledger for seats / projects / tasks / events.

Single authoritative state store for a ClawSeat installation. Backed by a
plain SQLite file at ``~/.agents/state.db``. No ORM, no external deps —
stdlib ``sqlite3`` only.

Usage::

    from state import open_db, upsert_project, list_seats, pick_least_busy_seat

    conn = open_db()                        # opens + auto-migrates schema
    upsert_seat(conn, seat)
    live_builders = list_seats(conn, "install", role="builder", status="live")
    pick = pick_least_busy_seat(conn, "install", "builder")

See ``core/scripts/state_admin.py`` for the operator-facing CLI.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys as _sys, pathlib as _pl
_fc_scripts = str(_pl.Path(__file__).resolve().parent.parent / "scripts")
if _fc_scripts not in _sys.path: _sys.path.insert(0, _fc_scripts)
from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load

try:
    from real_home import real_user_home
except ImportError:  # pragma: no cover — when imported via core.lib package path
    import sys as _sys
    _lib_dir = Path(__file__).resolve().parent
    if str(_lib_dir) not in _sys.path:
        _sys.path.insert(0, str(_lib_dir))
    from real_home import real_user_home  # type: ignore[no-redef]

__all__ = [
    "Project", "Seat", "Task", "Event",
    "open_db",
    "get_project", "list_projects",
    "get_seat", "list_seats",
    "get_task", "open_tasks_for_seat", "pick_least_busy_seat",
    "upsert_project", "upsert_seat",
    "record_task_dispatched", "mark_task_completed",
    "record_event", "record_event_if_new",
    "list_unsent_feishu_events", "mark_feishu_sent",
    "seed_from_filesystem",
]

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses — one per table row
# ---------------------------------------------------------------------------

@dataclass
class Project:
    name: str
    feishu_group_id: str = ""
    feishu_bot_account: str = ""
    repo_root: str = ""
    heartbeat_owner: str = ""
    active_loop_owner: str = ""
    bound_at: str = ""


@dataclass
class Seat:
    project: str
    seat_id: str
    role: str
    tool: str
    auth_mode: str
    provider: str = ""
    status: str = "unknown"
    last_heartbeat: str | None = None
    session_name: str | None = None
    workspace: str | None = None


@dataclass
class Task:
    id: str
    project: str
    source: str
    target: str
    status: str
    opened_at: str
    role_hint: str | None = None
    title: str | None = None
    correlation_id: str | None = None
    closed_at: str | None = None
    disposition: str | None = None


@dataclass
class Event:
    ts: str
    type: str
    payload_json: str
    project: str | None = None
    id: int = 0  # set by DB on insert


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS projects (
  name              TEXT PRIMARY KEY,
  feishu_group_id   TEXT NOT NULL DEFAULT '',
  feishu_bot_account TEXT NOT NULL DEFAULT '',
  repo_root         TEXT NOT NULL DEFAULT '',
  heartbeat_owner   TEXT NOT NULL DEFAULT '',
  active_loop_owner TEXT NOT NULL DEFAULT '',
  bound_at          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS seats (
  project        TEXT NOT NULL,
  seat_id        TEXT NOT NULL,
  role           TEXT NOT NULL,
  tool           TEXT NOT NULL,
  auth_mode      TEXT NOT NULL,
  provider       TEXT NOT NULL DEFAULT '',
  status         TEXT NOT NULL DEFAULT 'unknown',
  last_heartbeat TEXT,
  session_name   TEXT,
  workspace      TEXT,
  PRIMARY KEY (project, seat_id)
);

CREATE TABLE IF NOT EXISTS tasks (
  id             TEXT PRIMARY KEY,
  project        TEXT NOT NULL,
  source         TEXT NOT NULL,
  target         TEXT NOT NULL,
  role_hint      TEXT,
  status         TEXT NOT NULL,
  title          TEXT,
  correlation_id TEXT,
  opened_at      TEXT NOT NULL,
  closed_at      TEXT,
  disposition    TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL,
  type         TEXT NOT NULL,
  project      TEXT,
  payload_json TEXT NOT NULL,
  fingerprint  TEXT
);

CREATE INDEX IF NOT EXISTS idx_seats_status ON seats(project, role, status);
CREATE INDEX IF NOT EXISTS idx_tasks_open   ON tasks(project, target, status);
CREATE INDEX IF NOT EXISTS idx_events_ts    ON events(ts);
"""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def open_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (and auto-schema) the state DB.

    Default path: ``~/.agents/state.db`` resolved via real_user_home().
    Override via ``CLAWSEAT_STATE_DB`` env var (used by tests).
    Re-opening an already-initialised DB is a no-op (all DDL uses
    ``CREATE TABLE IF NOT EXISTS``).
    """
    import os as _os
    if db_path is None:
        env_override = _os.environ.get("CLAWSEAT_STATE_DB", "").strip()
        db_path = Path(env_override) if env_override else real_user_home() / ".agents" / "state.db"
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    # Migration: older installs predate the fingerprint column.
    try:
        conn.execute("ALTER TABLE events ADD COLUMN fingerprint TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_fingerprint ON events(fingerprint)"
    )
    # Migration: C11 adds feishu_sent column for Feishu announcer tracking.
    try:
        conn.execute("ALTER TABLE events ADD COLUMN feishu_sent TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_feishu_pending "
        "ON events(type, feishu_sent) WHERE feishu_sent IS NULL"
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_project(conn: sqlite3.Connection, name: str) -> Project | None:
    row = conn.execute(
        "SELECT * FROM projects WHERE name = ?", (name,)
    ).fetchone()
    return _row_to_project(row) if row else None


def list_projects(conn: sqlite3.Connection) -> list[Project]:
    rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
    return [_row_to_project(r) for r in rows]


def get_seat(conn: sqlite3.Connection, project: str, seat_id: str) -> Seat | None:
    row = conn.execute(
        "SELECT * FROM seats WHERE project = ? AND seat_id = ?",
        (project, seat_id),
    ).fetchone()
    return _row_to_seat(row) if row else None


def list_seats(
    conn: sqlite3.Connection,
    project: str,
    *,
    role: str | None = None,
    status: str | None = None,
) -> list[Seat]:
    q = "SELECT * FROM seats WHERE project = ?"
    params: list[Any] = [project]
    if role is not None:
        q += " AND role = ?"
        params.append(role)
    if status is not None:
        q += " AND status = ?"
        params.append(status)
    q += " ORDER BY seat_id"
    rows = conn.execute(q, params).fetchall()
    return [_row_to_seat(r) for r in rows]


def get_task(conn: sqlite3.Connection, task_id: str) -> Task | None:
    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    return _row_to_task(row) if row else None


def open_tasks_for_seat(
    conn: sqlite3.Connection, project: str, seat_id: str
) -> list[Task]:
    rows = conn.execute(
        "SELECT * FROM tasks WHERE project = ? AND target = ? "
        "AND status IN ('dispatched', 'in_progress') ORDER BY opened_at",
        (project, seat_id),
    ).fetchall()
    return [_row_to_task(r) for r in rows]


def pick_least_busy_seat(
    conn: sqlite3.Connection, project: str, role: str
) -> Seat | None:
    """Return the live seat with fewest in-flight tasks for (project, role).

    In-flight = status IN ('dispatched', 'in_progress').
    Ties broken alphabetically by seat_id (deterministic).
    Returns None if no live seat exists for the given role.
    """
    row = conn.execute(
        """
        SELECT s.*,
               COUNT(t.id) AS inflight
        FROM   seats s
        LEFT JOIN tasks t
               ON  t.project = s.project
               AND t.target  = s.seat_id
               AND t.status IN ('dispatched', 'in_progress')
        WHERE  s.project = ?
        AND    s.role    = ?
        AND    s.status  = 'live'
        GROUP  BY s.seat_id
        ORDER  BY inflight ASC, s.seat_id ASC
        LIMIT  1
        """,
        (project, role),
    ).fetchone()
    if row is None:
        return None
    return _row_to_seat(row)


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def upsert_project(conn: sqlite3.Connection, project: Project) -> None:
    conn.execute(
        """
        INSERT INTO projects
          (name, feishu_group_id, feishu_bot_account, repo_root,
           heartbeat_owner, active_loop_owner, bound_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
          feishu_group_id   = excluded.feishu_group_id,
          feishu_bot_account= excluded.feishu_bot_account,
          repo_root         = excluded.repo_root,
          heartbeat_owner   = excluded.heartbeat_owner,
          active_loop_owner = excluded.active_loop_owner,
          bound_at          = excluded.bound_at
        """,
        (
            project.name, project.feishu_group_id, project.feishu_bot_account,
            project.repo_root, project.heartbeat_owner, project.active_loop_owner,
            project.bound_at,
        ),
    )
    conn.commit()


def upsert_seat(
    conn: sqlite3.Connection,
    seat: Seat,
    *,
    allow_stopped_revival: bool = False,
) -> None:
    existing = conn.execute(
        "SELECT status FROM seats WHERE project = ? AND seat_id = ?",
        (seat.project, seat.seat_id),
    ).fetchone()
    if (
        existing is not None
        and str(existing["status"]) == "stopped"
        and seat.status == "live"
        and not allow_stopped_revival
    ):
        raise ValueError(
            f"refusing to revive stopped seat {seat.project}/{seat.seat_id} "
            "without allow_stopped_revival=True"
        )
    conn.execute(
        """
        INSERT INTO seats
          (project, seat_id, role, tool, auth_mode, provider,
           status, last_heartbeat, session_name, workspace)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project, seat_id) DO UPDATE SET
          role           = excluded.role,
          tool           = excluded.tool,
          auth_mode      = excluded.auth_mode,
          provider       = excluded.provider,
          status         = excluded.status,
          last_heartbeat = excluded.last_heartbeat,
          session_name   = excluded.session_name,
          workspace      = excluded.workspace
        """,
        (
            seat.project, seat.seat_id, seat.role, seat.tool, seat.auth_mode,
            seat.provider, seat.status, seat.last_heartbeat,
            seat.session_name, seat.workspace,
        ),
    )
    conn.commit()


def record_task_dispatched(conn: sqlite3.Connection, task: Task) -> None:
    """Insert a new task. Silently ignores if task_id already exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO tasks
          (id, project, source, target, role_hint, status, title,
           correlation_id, opened_at, closed_at, disposition)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task.id, task.project, task.source, task.target, task.role_hint,
            task.status, task.title, task.correlation_id, task.opened_at,
            task.closed_at, task.disposition,
        ),
    )
    conn.commit()


def mark_task_completed(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    disposition: str = "",
    closed_at: str | None = None,
) -> None:
    if closed_at is None:
        closed_at = _utcnow()
    conn.execute(
        """
        UPDATE tasks
        SET    status      = 'completed',
               closed_at   = ?,
               disposition = ?
        WHERE  id = ?
        """,
        (closed_at, disposition, task_id),
    )
    conn.commit()


def record_event(
    conn: sqlite3.Connection,
    type: str,
    project: str | None,
    **payload: Any,
) -> None:
    conn.execute(
        "INSERT INTO events (ts, type, project, payload_json) VALUES (?, ?, ?, ?)",
        (_utcnow(), type, project, json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()


def record_event_if_new(
    conn: sqlite3.Connection,
    type: str,
    project: str | None,
    fingerprint: str,
    **payload: Any,
) -> bool:
    """Insert an event only if ``fingerprint`` is not already in the table.

    Returns True on insert, False if an identical fingerprint was already
    recorded. Used by the C10 events watcher to re-derive events from
    handoff JSONs idempotently.
    """
    if not fingerprint:
        raise ValueError("fingerprint must be a non-empty string")
    existing = conn.execute(
        "SELECT 1 FROM events WHERE fingerprint = ? LIMIT 1", (fingerprint,)
    ).fetchone()
    if existing is not None:
        return False
    conn.execute(
        "INSERT INTO events (ts, type, project, payload_json, fingerprint) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            _utcnow(), type, project,
            json.dumps(payload, ensure_ascii=False),
            fingerprint,
        ),
    )
    conn.commit()
    return True


def list_unsent_feishu_events(
    conn: sqlite3.Connection,
    *,
    event_types: tuple[str, ...] = ("task.completed", "chain.closeout"),
    limit: int = 100,
    project: str | None = None,
) -> list[Event]:
    """Return events whose type is in event_types and feishu_sent IS NULL.

    Ordered by ts ascending so the oldest event is sent first.
    Optionally scoped to a single project via ``project``.
    """
    placeholders = ",".join("?" for _ in event_types)
    params: list[Any] = list(event_types)
    q = (
        f"SELECT * FROM events WHERE type IN ({placeholders}) "
        "AND feishu_sent IS NULL"
    )
    if project is not None:
        q += " AND project = ?"
        params.append(project)
    q += " ORDER BY ts ASC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    return [_row_to_event(r) for r in rows]


def mark_feishu_sent(conn: sqlite3.Connection, event_id: int, ts: str) -> None:
    """Record that the Feishu envelope for event_id was sent at ts."""
    conn.execute(
        "UPDATE events SET feishu_sent = ? WHERE id = ?",
        (ts, event_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Filesystem seeding
# ---------------------------------------------------------------------------

def seed_from_filesystem(
    home: Path | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Read existing TOML/JSON artefacts and populate the DB.

    Returns a counts dict: ``{'projects': n, 'seats': m, 'tasks': k}``.

    Idempotent: re-running updates rows in place and never clobbers
    disposition/closed_at for tasks already marked complete.
    """
    if conn is None:
        conn = open_db(db_path)
    if home is None:
        home = real_user_home()

    agents_root = home / ".agents"
    counts: dict[str, int] = {"projects": 0, "seats": 0, "tasks": 0}

    # ── 1. Projects from PROJECT_BINDING.toml ─────────────────────────────
    seat_roles_cache: dict[str, dict[str, str]] = {}  # project → {seat_id: role}

    for binding_path in sorted((agents_root / "tasks").glob("*/PROJECT_BINDING.toml")):
        project_name = binding_path.parent.name
        try:
            data = _toml_loads(binding_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"state.seed: skipping malformed binding {binding_path}: {exc}")
            continue

        # Try profile TOML for heartbeat_owner, active_loop_owner, seat_roles
        heartbeat_owner = ""
        active_loop_owner = ""
        profile_path = agents_root / "profiles" / f"{project_name}-profile-dynamic.toml"
        if profile_path.exists():
            try:
                profile = _toml_loads(profile_path.read_text(encoding="utf-8"))
                heartbeat_owner = str(profile.get("heartbeat_owner", ""))
                active_loop_owner = str(profile.get("active_loop_owner", heartbeat_owner))
                raw_roles = profile.get("seat_roles", {})
                if isinstance(raw_roles, dict):
                    seat_roles_cache[project_name] = {
                        str(k): str(v) for k, v in raw_roles.items()
                    }
            except Exception:  # noqa: BLE001
                pass

        upsert_project(conn, Project(
            name=project_name,
            feishu_group_id=str(data.get("feishu_group_id", "")),
            feishu_bot_account=str(data.get("feishu_bot_account", "")),
            bound_at=str(data.get("bound_at", "")),
            heartbeat_owner=heartbeat_owner,
            active_loop_owner=active_loop_owner,
        ))
        counts["projects"] += 1

    # ── 2. Live tmux sessions → set of active session names ───────────────
    live_sessions: set[str] = _get_live_tmux_sessions()
    now_iso = _utcnow()

    # ── 3. Seats from session.toml ─────────────────────────────────────────
    for session_path in sorted((agents_root / "sessions").glob("*/*/*")):
        if session_path.name != "session.toml":
            continue
        try:
            data = _toml_loads(session_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"state.seed: skipping malformed session {session_path}: {exc}")
            continue

        project = str(data.get("project", ""))
        seat_id = str(data.get("engineer_id", ""))
        if not project or not seat_id:
            continue

        session_name = str(data.get("session", ""))
        is_live = bool(session_name and session_name in live_sessions)
        status = "live" if is_live else "stopped"
        last_heartbeat = now_iso if is_live else None

        # Role: from cached profile seat_roles, else infer from seat_id
        roles = seat_roles_cache.get(project, {})
        role = roles.get(seat_id) or _infer_role(seat_id)

        upsert_kwargs = {"allow_stopped_revival": True} if status == "live" else {}
        upsert_seat(
            conn,
            Seat(
                project=project,
                seat_id=seat_id,
                role=role,
                tool=str(data.get("tool", "")),
                auth_mode=str(data.get("auth_mode", "")),
                provider=str(data.get("provider", "")),
                status=status,
                last_heartbeat=last_heartbeat,
                session_name=session_name or None,
                workspace=str(data.get("workspace", "")) or None,
            ),
            **upsert_kwargs,
        )
        counts["seats"] += 1

    # ── 4. Tasks from patrol/handoffs/*.json ──────────────────────────────
    for handoff_path in sorted(
        (agents_root / "tasks").glob("*/patrol/handoffs/*.json")
    ):
        project = handoff_path.parts[
            handoff_path.parts.index("tasks") + 1
        ] if "tasks" in handoff_path.parts else ""
        if not project:
            continue
        try:
            data = json.loads(handoff_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"state.seed: skipping malformed handoff {handoff_path}: {exc}")
            continue

        task_id = str(data.get("task_id", "")) or handoff_path.stem.split("__")[0]
        if not task_id:
            continue
        if get_task(conn, task_id) is not None:
            # Already seeded; do not clobber completed disposition
            continue

        kind = str(data.get("kind", ""))
        status = "completed" if kind == "completion" else "dispatched"
        delivered_at = str(data.get("delivered_at", ""))
        closed_at = delivered_at if status == "completed" else None
        opened_at = delivered_at or now_iso

        record_task_dispatched(conn, Task(
            id=task_id,
            project=project,
            source=str(data.get("source", "")),
            target=str(data.get("target", "")),
            status=status,
            opened_at=opened_at,
            correlation_id=str(data.get("correlation_id", "")) or None,
            closed_at=closed_at,
            disposition=str(data.get("frontstage_disposition", "")) or None,
        ))
        counts["tasks"] += 1

    return counts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_live_tmux_sessions() -> set[str]:
    """Return set of running tmux session names, or empty set if tmux unavailable."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return {line.strip() for line in result.stdout.splitlines() if line.strip()}
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return set()


_ROLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^builder"), "builder"),
    (re.compile(r"^reviewer"), "reviewer"),
    (re.compile(r"^patrol"), "patrol"),
    (re.compile(r"^planner"), "planner"),
    (re.compile(r"^designer"), "designer"),
    (re.compile(r"^memory$"), "memory-oracle"),
    (re.compile(r"^koder$"), "frontstage-supervisor"),
    (re.compile(r"^engineer"), "builder"),
]


def _infer_role(seat_id: str) -> str:
    for pattern, role in _ROLE_PATTERNS:
        if pattern.match(seat_id):
            return role
    return "specialist"


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        name=row["name"],
        feishu_group_id=row["feishu_group_id"],
        feishu_bot_account=row["feishu_bot_account"],
        repo_root=row["repo_root"],
        heartbeat_owner=row["heartbeat_owner"],
        active_loop_owner=row["active_loop_owner"],
        bound_at=row["bound_at"],
    )


def _row_to_seat(row: sqlite3.Row) -> Seat:
    return Seat(
        project=row["project"],
        seat_id=row["seat_id"],
        role=row["role"],
        tool=row["tool"],
        auth_mode=row["auth_mode"],
        provider=row["provider"],
        status=row["status"],
        last_heartbeat=row["last_heartbeat"],
        session_name=row["session_name"],
        workspace=row["workspace"],
    )


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        project=row["project"],
        source=row["source"],
        target=row["target"],
        role_hint=row["role_hint"],
        status=row["status"],
        title=row["title"],
        correlation_id=row["correlation_id"],
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        disposition=row["disposition"],
    )


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        ts=row["ts"],
        type=row["type"],
        project=row["project"],
        payload_json=row["payload_json"],
    )
