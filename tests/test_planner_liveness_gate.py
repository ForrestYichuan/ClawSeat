from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from assign_owner import EscalationRequired, assign_owner  # noqa: E402
import assign_owner as assign_owner_module  # noqa: E402
from liveness_gate import query_seat_liveness  # noqa: E402


def test_liveness_gate_returns_alive_seats_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "state.db"
    monkeypatch.setenv("CLAWSEAT_STATE_DB", str(db_path))
    now = datetime.now(timezone.utc)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE seat_liveness (
              project TEXT,
              role TEXT,
              session_name TEXT,
              status TEXT,
              last_heartbeat_ts TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO seat_liveness VALUES (?, ?, ?, ?, ?)",
            [
                ("install", "builder", "install-builder-codex", "alive", now.isoformat()),
                (
                    "install",
                    "reviewer",
                    "install-reviewer-claude",
                    "alive",
                    (now - timedelta(seconds=600)).isoformat(),
                ),
                ("install", "patrol", "install-patrol-claude", "dead", now.isoformat()),
                ("other", "builder", "other-builder-codex", "alive", now.isoformat()),
            ],
        )

    assert query_seat_liveness("install", max_age_seconds=300) == [
        {
            "role": "builder",
            "session_name": "install-builder-codex",
            "status": "alive",
            "last_heartbeat_ts": now.isoformat(),
        }
    ]


def test_liveness_gate_seats_table_includes_seat_id_for_exact_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "state.db"
    monkeypatch.setenv("CLAWSEAT_STATE_DB", str(db_path))
    now = datetime.now(timezone.utc)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE seats (
              project TEXT,
              seat_id TEXT,
              role TEXT,
              tool TEXT,
              auth_mode TEXT,
              provider TEXT,
              status TEXT,
              last_heartbeat TEXT,
              session_name TEXT,
              workspace TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO seats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "install",
                "builder-1",
                "builder",
                "codex",
                "oauth",
                "openai",
                "live",
                now.isoformat(),
                "install-builder-1-codex",
                "/tmp/ws",
            ),
        )

    assert query_seat_liveness("install", max_age_seconds=300) == [
        {
            "seat_id": "builder-1",
            "role": "builder",
            "session_name": "install-builder-1-codex",
            "status": "alive",
            "last_heartbeat_ts": now.isoformat(),
        }
    ]


def test_assign_owner_swallow_when_restart_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assign_owner_module, "restart_seat", lambda project, role: False)

    direct = assign_owner(
        "builder",
        [{"role": "builder", "session_name": "install-builder-codex", "status": "alive"}],
        "install",
    )
    assert direct == "install-builder-codex"

    assert assign_owner("reviewer", [], "install") == "planner [SWALLOW=reviewer]"

    with pytest.raises(EscalationRequired):
        assign_owner("memory", [], "install")
