#!/opt/homebrew/bin/python3.12
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROFILE_PATH = Path("/Users/ccopc/.agents/profiles/clawseat-profile-dynamic.toml")
TASKS_ROOT = Path("/Users/ccopc/.agents/tasks/clawseat")
TASKS_MD = TASKS_ROOT / "TASKS.md"
STATUS_MD = TASKS_ROOT / "STATUS.md"
HANDOFF_DIR = TASKS_ROOT / "patrol" / "handoffs"
SESSION_ROOT = Path("/Users/ccopc/.agents/sessions/clawseat")

SEAT_ORDER = ["memory", "planner", "builder", "reviewer", "patrol"]
STALE_AFTER_SECONDS = 24 * 60 * 60
SECRETISH = re.compile(
    r"(token|secret|api[_-]?key|app[_-]?secret|private[_-]?key|password|credential)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Handoff:
    task_id: str
    source: str
    target: str
    kind: str
    notified_at: str | None
    consumed_at: str | None
    verdict: str | None
    status: str | None
    path: str

    @property
    def timestamp(self) -> str | None:
        return self.consumed_at or self.notified_at


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_seconds(value: str | None) -> float | None:
    parsed = parse_ts(value)
    if not parsed:
        return None
    return max(0.0, (utc_now() - parsed).total_seconds())


def safe_read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    if SECRETISH.search(path.name):
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def load_toml(path: Path) -> dict[str, Any]:
    if not path.exists() or SECRETISH.search(path.name):
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists() or SECRETISH.search(path.name):
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def profile_data() -> dict[str, Any]:
    return load_toml(PROFILE_PATH)


def session_data(seat: str) -> dict[str, str]:
    data = load_toml(SESSION_ROOT / seat / "session.toml")
    identity = str(data.get("identity", ""))
    tool = str(data.get("tool", "")) or (identity.split(".")[0] if identity else "")
    provider = str(data.get("provider", "")) or (identity.split(".")[2] if len(identity.split(".")) > 2 else "")
    session = str(data.get("session", "")) or f"clawseat-{seat}-{tool or 'unknown'}"
    return {"tool": tool or "unknown", "provider": provider or "unknown", "session": session}


def task_registry() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in safe_read_text(TASKS_MD).splitlines():
        line = raw.strip()
        if not line.startswith("|") or "---" in line or line.startswith("| ID "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        rows.append({"task_id": cells[0], "title": cells[1], "owner": cells[2], "status": cells[3]})
    return rows


def parse_todo(seat: str) -> dict[str, Any] | None:
    path = TASKS_ROOT / seat / "TODO.md"
    text = safe_read_text(path)
    if not text:
        return None
    pattern = re.compile(r"^## \[(?P<status>[^\]]+)\] (?P<title>.+)$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    for index, match in enumerate(matches):
        status = match.group("status").strip().lower()
        if status in {"completed", "done", "superseded"}:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end]
        fields = extract_colon_fields(block)
        task_id = fields.get("task_id") or match.group("title").strip()
        return {
            "task_id": task_id,
            "title": fields.get("title") or match.group("title").strip(),
            "status": normalize_task_status(status),
            "source": fields.get("source", ""),
            "reply_to": fields.get("reply_to", ""),
            "dispatched_at": fields.get("dispatched_at", ""),
        }
    return None


def normalize_task_status(status: str) -> str:
    value = status.lower().strip()
    if value in {"pending", "queued", "assigned"}:
        return "queued"
    if value in {"in_progress", "in-progress", "active", "working"}:
        return "active"
    if value in {"blocked", "failed"}:
        return "blocked"
    if value in {"completed", "done"}:
        return "completed"
    return value or "unknown"


def extract_colon_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw in text.splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip().lower()
        if re.fullmatch(r"[a-zA-Z0-9_ -]+", key):
            fields[key.replace(" ", "_")] = value.strip()
    return fields


def parse_delivery(seat: str) -> dict[str, str] | None:
    path = TASKS_ROOT / seat / "DELIVERY.md"
    fields = extract_colon_fields(safe_read_text(path))
    if not fields:
        return None
    verdict = fields.get("verdict", "")
    if not verdict:
        match = re.search(r"^Verdict:\s*(.+)$", safe_read_text(path), flags=re.MULTILINE)
        verdict = match.group(1).strip() if match else ""
    task_id = fields.get("task_id", "")
    if not task_id:
        return None
    return {
        "task_id": task_id,
        "status": fields.get("status", "unknown"),
        "verdict": verdict or "unknown",
        "date": fields.get("date", ""),
    }


def consumed_timestamp(path: Path, payload: dict[str, Any]) -> str | None:
    if payload.get("consumed_at"):
        return str(payload["consumed_at"])
    consumed_path = Path(str(path) + ".consumed")
    if consumed_path.exists():
        consumed = load_json(consumed_path)
        return str(consumed.get("consumed_at") or consumed.get("ack_at") or consumed.get("delivered_at") or "") or None
    return None


def handoff_receipts() -> list[Handoff]:
    items: list[Handoff] = []
    if not HANDOFF_DIR.exists():
        return items
    for path in sorted(HANDOFF_DIR.glob("*.json")):
        payload = load_json(path)
        if not payload:
            continue
        items.append(
            Handoff(
                task_id=str(payload.get("task_id", "")),
                source=str(payload.get("source", "")),
                target=str(payload.get("target", "")),
                kind=str(payload.get("kind", "dispatch")),
                notified_at=str(payload.get("notified_at") or payload.get("delivered_at") or payload.get("assigned_at") or "") or None,
                consumed_at=consumed_timestamp(path, payload),
                verdict=str(payload.get("verdict") or "") or None,
                status=str(payload.get("status") or "") or None,
                path=str(path),
            )
        )
    return sorted(items, key=lambda item: parse_ts(item.timestamp) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def tmux_sessions() -> set[str]:
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def recent_timeline(limit: int = 40) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for raw in safe_read_text(STATUS_MD).splitlines():
        line = raw.strip()
        match = re.match(r"^- (?P<ts>\d{4}-\d{2}-\d{2}T\S+): (?P<event>.+)$", line)
        if not match:
            continue
        event = match.group("event").strip()
        seat = infer_seat_from_event(event)
        events.append({"timestamp": match.group("ts"), "event": event, "seat": seat})
    events.sort(key=lambda item: parse_ts(item["timestamp"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return events[:limit]


def infer_seat_from_event(event: str) -> str:
    for seat in SEAT_ORDER:
        if re.search(rf"\b{re.escape(seat)}\b", event):
            return seat
    return ""


def handoff_for_seat(seat: str, handoffs: list[Handoff]) -> list[Handoff]:
    return [item for item in handoffs if item.source == seat or item.target == seat][:8]


def current_handoff_for_seat(seat: str, handoffs: list[Handoff]) -> Handoff | None:
    for item in handoffs:
        if item.target == seat and item.kind == "dispatch" and not item.consumed_at:
            return item
    for item in handoffs:
        if item.source == seat and item.kind == "completion":
            return item
    return None


def infer_state(
    *,
    seat: str,
    liveness: str,
    current_task: dict[str, Any] | None,
    latest_delivery: dict[str, str] | None,
    latest_handoff: Handoff | None,
) -> str:
    if current_task and current_task.get("status") == "blocked":
        return "blocked"
    delivery_applies = bool(
        latest_delivery
        and (not current_task or latest_delivery.get("task_id") == current_task.get("task_id"))
    )
    if delivery_applies and str(latest_delivery.get("verdict", "")).upper() == "BLOCKED":
        return "blocked"
    if (
        latest_handoff
        and latest_handoff.kind == "dispatch"
        and latest_handoff.target == seat
        and latest_handoff.notified_at
        and not latest_handoff.consumed_at
    ):
        return "active" if liveness == "running" else "queued"
    if current_task:
        return "waiting"
    if latest_handoff and latest_handoff.kind == "completion" and latest_handoff.verdict:
        return "delivered"
    if latest_handoff and latest_handoff.kind == "dispatch" and latest_handoff.source == seat and not latest_handoff.consumed_at:
        return "waiting"
    if liveness in {"missing", "unknown"}:
        return "unknown"
    return "idle"


def is_stale(current_task: dict[str, Any] | None, latest_handoff: Handoff | None) -> bool:
    if not current_task:
        return False
    newest = latest_handoff.timestamp if latest_handoff else current_task.get("dispatched_at")
    age = age_seconds(newest)
    return bool(age is not None and age > STALE_AFTER_SECONDS)


def render_handoff(item: Handoff) -> dict[str, Any]:
    return {
        "task_id": item.task_id,
        "source": item.source,
        "target": item.target,
        "kind": item.kind,
        "notified": bool(item.notified_at),
        "consumed": bool(item.consumed_at),
        "verdict": item.verdict or "",
        "timestamp": item.timestamp or "",
    }


def seat_bundle(profile: dict[str, Any], handoffs: list[Handoff], running_sessions: set[str]) -> list[dict[str, Any]]:
    seats = list(profile.get("seats") or SEAT_ORDER)
    roles = dict(profile.get("seat_roles") or {})
    output: list[dict[str, Any]] = []
    for seat in seats:
        session = session_data(seat)
        session_name = session["session"]
        liveness = "running" if session_name in running_sessions else "missing"
        current_task = parse_todo(seat)
        latest_delivery = parse_delivery(seat)
        seat_handoffs = handoff_for_seat(seat, handoffs)
        latest_handoff = current_handoff_for_seat(seat, handoffs) or (seat_handoffs[0] if seat_handoffs else None)
        state = infer_state(
            seat=seat,
            liveness=liveness,
            current_task=current_task,
            latest_delivery=latest_delivery,
            latest_handoff=latest_handoff,
        )
        stale = is_stale(current_task, latest_handoff)
        if stale and state not in {"blocked", "active"}:
            state = "stale"
        output.append(
            {
                "id": seat,
                "role": roles.get(seat, ""),
                "session": session_name,
                "tool": session["tool"],
                "provider": session["provider"],
                "liveness": liveness,
                "current_task": current_task,
                "recent_handoffs": [render_handoff(item) for item in seat_handoffs],
                "latest_delivery": latest_delivery,
                "state": state,
                "stale": stale,
            }
        )
    return output


def build_payload() -> dict[str, Any]:
    profile = profile_data()
    handoffs = handoff_receipts()
    seats = seat_bundle(profile, handoffs, tmux_sessions())
    task_rows = task_registry()
    active_tasks = sum(1 for seat in seats if seat.get("current_task"))
    blocked_tasks = sum(1 for seat in seats if seat.get("state") == "blocked")
    running = sum(1 for seat in seats if seat.get("liveness") == "running")
    return {
        "generated_at": iso_now(),
        "profile": str(profile.get("profile_name") or profile.get("project_name") or "clawseat"),
        "seats": seats,
        "task_registry": task_rows,
        "recent_timeline": recent_timeline(),
        "summary": {
            "total_seats": len(seats),
            "running": running,
            "active_tasks": active_tasks,
            "blocked_tasks": blocked_tasks,
            "stale_seats": sum(1 for seat in seats if seat.get("stale")),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit normalized ClawSeat seat activity JSON.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON for humans.")
    args = parser.parse_args()
    print(json.dumps(build_payload(), ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
