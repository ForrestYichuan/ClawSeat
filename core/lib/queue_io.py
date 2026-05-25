"""ClawSeat v3 tasks.queue.jsonl event-stream helper.

Single-writer-at-a-time guaranteed by fcntl.LOCK_EX file lock. Events are
immutable; state changes append new events. Readers collapse-by-task_id to
derive current state.

See spec §4.3 (install-spec-2026-05-13-clawseat-v3-multi-team-protocol.md).
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Mapping


VALID_EVENT_TYPES = frozenset(
    {
        "task_created",
        "task_claimed",
        "task_in_progress",
        "task_waiting_for",
        "task_done",
        "task_failed",
        "task_bounced",
        "task_reset",
    }
)

QUEUE_ACTIVE_STATUSES = frozenset({"task_in_progress"})
QUEUE_CLAIMED_STATUSES = frozenset({"task_claimed"})
QUEUE_WAITING_STATUSES = frozenset({"task_created", "task_waiting_for"})
QUEUE_BLOCKED_STATUSES = frozenset({"task_failed", "task_bounced", "task_reset"})
QUEUE_DONE_STATUSES = frozenset({"task_done"})

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    # source state -> allowed next event_type
    "<none>": frozenset({"task_created"}),
    # task_created -> task_waiting_for allowed: planner inspects depends_on at claim
    # time; if upstream not done, helper records waiting_for without intermediate claim.
    "task_created": frozenset(
        {"task_claimed", "task_waiting_for", "task_reset", "task_failed"}
    ),
    "task_claimed": frozenset({"task_in_progress", "task_waiting_for", "task_reset"}),
    "task_in_progress": frozenset(
        {"task_done", "task_failed", "task_bounced", "task_reset"}
    ),
    # task_waiting_for retries: planner re-claims when upstream completes.
    "task_waiting_for": frozenset({"task_claimed", "task_waiting_for", "task_reset"}),
    "task_done": frozenset(set()),
    "task_failed": frozenset({"task_created"}),  # retry as new task_id only — but state-machine permits same id reset
    "task_bounced": frozenset({"task_created"}),
    "task_reset": frozenset({"task_claimed", "task_created"}),
}

_ACTOR_PATTERN = re.compile(
    r"^(memory|patrol|operator|[a-z0-9-]+@(claude|codex|gemini))$"
)
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class QueueError(RuntimeError):
    """Raised on schema, state-machine, or IO violations."""


@dataclass
class TaskState:
    """Collapsed state of one task_id derived from the event stream."""

    task_id: str
    status: str  # last event_type seen
    last_seq: int
    last_event_ts: str
    actor: str
    brief_path: str | None = None
    parent_task_id: str | None = None
    depends_on: list[str] = field(default_factory=list)
    waiting_for: str | None = None
    verdict: str | None = None
    fail_reason: str | None = None
    bounce_reason: str | None = None
    reset_reason: str | None = None
    reset_count: int = 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_event_shape(event: dict) -> None:
    required = {"event_type", "event_ts", "seq", "actor", "task_id"}
    missing = required - event.keys()
    if missing:
        raise QueueError(f"event missing required keys: {sorted(missing)}")
    if event["event_type"] not in VALID_EVENT_TYPES:
        raise QueueError(f"unknown event_type: {event['event_type']!r}")
    if not isinstance(event["seq"], int) or event["seq"] < 1:
        raise QueueError(f"seq must be int >= 1, got {event['seq']!r}")
    if not _ACTOR_PATTERN.match(str(event["actor"])):
        raise QueueError(f"actor format invalid: {event['actor']!r}")
    if not _TASK_ID_PATTERN.match(str(event["task_id"])):
        raise QueueError(f"task_id format invalid: {event['task_id']!r}")

    et = event["event_type"]
    if et == "task_created" and "brief_path" not in event:
        raise QueueError("task_created requires brief_path")
    if et == "task_waiting_for" and "waiting_for" not in event:
        raise QueueError("task_waiting_for requires waiting_for")
    if et == "task_failed" and "verdict" not in event:
        raise QueueError("task_failed requires verdict")
    if et == "task_done" and event.get("verdict") != "PASS":
        raise QueueError("task_done requires verdict='PASS'")
    if et == "task_bounced" and "bounce_reason" not in event:
        raise QueueError("task_bounced requires bounce_reason")
    if et == "task_reset" and "reset_reason" not in event:
        raise QueueError("task_reset requires reset_reason")


@contextmanager
def _flock_exclusive(path: Path, *, retries: int = 50, retry_delay: float = 0.02) -> Iterator[int]:
    """Acquire POSIX exclusive lock on path, creating it if needed.

    Uses an explicit fd (not the data file) to avoid holding the data file
    open across the write — that simplifies fsync semantics and rotation.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        for _ in range(retries):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
                time.sleep(retry_delay)
        else:
            raise QueueError(
                f"could not acquire lock on {lock_path} after {retries * retry_delay:.2f}s"
            )
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_last_seq(path: Path) -> int:
    """Return the highest seq written so far, or 0 if file empty/missing.

    Reads forward (not reverse) because seq is monotonic — we know last line
    has the highest seq. Optimization for huge files left as future work.
    """
    if not path.exists():
        return 0
    last_seq = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                seq = int(event.get("seq", 0))
                if seq > last_seq:
                    last_seq = seq
            except (json.JSONDecodeError, ValueError, TypeError):
                # Skip malformed line — readers tolerate but writers must not produce
                continue
    return last_seq


def _read_task_status(path: Path, task_id: str) -> str:
    """Return current event_type for task_id, or '<none>' if absent.

    Used inside the write critical section to validate state-machine.
    """
    if not path.exists():
        return "<none>"
    status = "<none>"
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = _normalize_event_for_read(json.loads(line))
                if event and event.get("task_id") == task_id:
                    status = event["event_type"]
            except (json.JSONDecodeError, KeyError):
                continue
    return status


def append_event(
    queue_path: Path | str,
    event: dict,
    *,
    enforce_state_machine: bool = True,
) -> dict:
    """Append a single event to the queue file. Returns the event with seq filled in.

    Caller passes event WITHOUT seq (helper assigns under lock) and WITHOUT
    event_ts unless they want to override. Inside the flock, helper:

    1. Validates shape
    2. Reads last seq, assigns seq = last + 1
    3. Validates state-machine transition (unless disabled)
    4. Appends as single JSON line (no trailing newline trickery)
    5. fsync to ensure durability

    Raises QueueError on any violation; file is left untouched.
    """
    path = Path(queue_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    event = dict(event)  # don't mutate caller's dict
    event.setdefault("event_ts", _utc_now_iso())

    with _flock_exclusive(path):
        last_seq = _read_last_seq(path)
        event["seq"] = last_seq + 1

        _validate_event_shape(event)

        if enforce_state_machine:
            current_status = _read_task_status(path, event["task_id"])
            allowed = VALID_TRANSITIONS.get(current_status, frozenset())
            if event["event_type"] not in allowed:
                raise QueueError(
                    f"state-machine violation: task_id={event['task_id']} "
                    f"current={current_status} attempted={event['event_type']}; "
                    f"allowed={sorted(allowed)}"
                )

        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    return event


def _normalize_event_for_read(event: dict, *, fallback_seq: int | None = None) -> dict | None:
    """Return a canonical read model for valid current and legacy queue events.

    Writers still emit only the canonical schema. Readers tolerate older/manual
    rows that used ``type``/``seat``/``ts`` so planner-status does not report a
    false open queue after a task already closed.
    """
    if not isinstance(event, dict):
        return None
    normalized = dict(event)
    if "event_type" not in normalized:
        if "type" in normalized:
            normalized["event_type"] = normalized["type"]
        elif "event" in normalized:
            normalized["event_type"] = normalized["event"]
    if normalized.get("event_type") == "task_blocked":
        # Legacy/manual closeout rows used task_blocked. The canonical queue
        # model has no task_blocked event; task_bounced is the closest blocked
        # terminal state and keeps the task out of the claimable queue.
        normalized["event_type"] = "task_bounced"
        if "bounce_reason" not in normalized and "reason" in normalized:
            normalized["bounce_reason"] = normalized["reason"]
    if "actor" not in normalized and "seat" in normalized:
        normalized["actor"] = normalized["seat"]
    if "event_ts" not in normalized and "ts" in normalized:
        normalized["event_ts"] = str(normalized["ts"])
    if "seq" not in normalized and fallback_seq is not None:
        normalized["seq"] = fallback_seq
    if not {"seq", "event_type", "task_id"} <= normalized.keys():
        return None
    try:
        normalized["seq"] = int(normalized["seq"])
    except (TypeError, ValueError):
        return None
    if normalized["event_type"] not in VALID_EVENT_TYPES:
        return None
    normalized.setdefault("actor", "operator")
    normalized.setdefault("event_ts", "")
    return normalized


def read_events(queue_path: Path | str) -> list[dict]:
    """Return all readable events in append order. Malformed lines silently skipped.

    Canonical writers append monotonically increasing ``seq`` values, but older
    manual closeout rows may omit seq or even reuse/decrease it. For current
    state, append order is the durable event order; seq remains a display/debug
    field after read normalization.
    """
    path = Path(queue_path)
    if not path.exists():
        return []
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = _normalize_event_for_read(json.loads(line), fallback_seq=line_no)
            except json.JSONDecodeError:
                continue
            if event:
                events.append(event)
    return events


def read_current_state(queue_path: Path | str) -> dict[str, TaskState]:
    """Collapse the event stream to per-task current state.

    For each task_id, takes the most recent event (by seq) and derives
    cumulative fields (depends_on, brief_path, reset_count, etc.).
    """
    events = read_events(queue_path)
    state: dict[str, TaskState] = {}
    for event in events:
        task_id = event["task_id"]
        et = event["event_type"]
        ts = TaskState(
            task_id=task_id,
            status=et,
            last_seq=int(event["seq"]),
            last_event_ts=str(event["event_ts"]),
            actor=str(event["actor"]),
        )
        prev = state.get(task_id)
        if prev is not None:
            # Carry forward immutable fields from earlier events
            ts.brief_path = prev.brief_path
            ts.parent_task_id = prev.parent_task_id
            ts.depends_on = list(prev.depends_on)
            ts.reset_count = prev.reset_count

        if et == "task_created":
            ts.brief_path = event.get("brief_path")
            ts.parent_task_id = event.get("parent_task_id")
            ts.depends_on = list(event.get("depends_on") or [])
        elif et == "task_waiting_for":
            ts.waiting_for = event.get("waiting_for")
        elif et == "task_done":
            ts.verdict = event.get("verdict", "PASS")
        elif et == "task_failed":
            ts.verdict = event.get("verdict", "FAIL")
            ts.fail_reason = event.get("fail_reason") or event.get("reason")
        elif et == "task_bounced":
            ts.bounce_reason = event.get("bounce_reason") or event.get("reason")
        elif et == "task_reset":
            ts.reset_count += 1
            ts.reset_reason = event.get("reset_reason")

        state[task_id] = ts
    return state


def query_pending(queue_path: Path | str) -> list[TaskState]:
    """Tasks currently in task_created state (claimable)."""
    return [ts for ts in read_current_state(queue_path).values() if ts.status == "task_created"]


def queue_state_label(tasks: Mapping[str, TaskState]) -> str:
    """Return the operator-facing state of a collapsed team queue.

    ``drained`` is intentionally narrow: every current task must be
    ``task_done``. Failed, bounced, and reset tasks still need attention, so
    they are reported as ``blocked`` instead of looking complete.
    """
    if not tasks:
        return "empty"
    statuses = {ts.status for ts in tasks.values()}
    if statuses & QUEUE_ACTIVE_STATUSES:
        return "active"
    if statuses & QUEUE_CLAIMED_STATUSES:
        return "claimed"
    if statuses & QUEUE_WAITING_STATUSES:
        return "waiting"
    if statuses & QUEUE_BLOCKED_STATUSES:
        return "blocked"
    if statuses <= QUEUE_DONE_STATUSES:
        return "drained"
    return "mixed"


def queue_is_drained(tasks: Mapping[str, TaskState], *, ignore_task_id: str = "") -> bool:
    """True when every non-ignored current task is ``task_done``."""
    for task_id, task in tasks.items():
        if ignore_task_id and task_id == ignore_task_id:
            continue
        if task.status != "task_done":
            return False
    return True


def query_claimed_by(queue_path: Path | str, actor: str) -> list[TaskState]:
    """Tasks an actor currently has claimed or in_progress."""
    return [
        ts
        for ts in read_current_state(queue_path).values()
        if ts.status in ("task_claimed", "task_in_progress") and ts.actor == actor
    ]


def query_waiting_for(queue_path: Path | str, upstream_id: str) -> list[TaskState]:
    """Tasks blocked on a specific upstream task_id."""
    return [
        ts
        for ts in read_current_state(queue_path).values()
        if ts.status == "task_waiting_for" and ts.waiting_for == upstream_id
    ]
