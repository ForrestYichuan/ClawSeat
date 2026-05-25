#!/usr/bin/env python3
"""Planner auto-compaction helper.

The planner Stop hook calls this script after a model turn.  It keeps the
policy small and runtime-owned:

* compact only at a durable safe point (team queue drained after task_done);
* write a recovery snapshot first;
* send /compact once per queue seq.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
CORE_LIB = REPO_ROOT / "core" / "lib"
if str(CORE_LIB) not in sys.path:
    sys.path.insert(0, str(CORE_LIB))

from profile_loader_v3 import ProfileV3Error, load_profile_v3  # noqa: E402
from queue_io import queue_state_label, read_current_state  # noqa: E402
from real_home import real_user_home  # noqa: E402


MARKER = "[memory: compact-me]"
@dataclass(frozen=True)
class WorkspaceHint:
    project: str = ""
    seat: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _agents_root() -> Path:
    return real_user_home() / ".agents"


def _profile_path(project: str) -> Path:
    return _agents_root() / "profiles" / f"{project}-profile-dynamic.toml"


def _queue_path(project: str, team: str) -> Path:
    return _agents_root() / "tasks" / project / team / "tasks.queue.jsonl"


def _runtime_root(project: str, team: str) -> Path:
    return _agents_root() / "tasks" / project / team / "runtime" / "planner-compact"


def _infer_workspace_hint(workspace: str) -> WorkspaceHint:
    if not workspace:
        return WorkspaceHint()
    parts = Path(workspace).expanduser().parts
    for idx, part in enumerate(parts):
        if part == "workspaces" and idx + 2 < len(parts):
            return WorkspaceHint(project=parts[idx + 1], seat=parts[idx + 2])
    return WorkspaceHint()


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@contextmanager
def _state_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "state.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _first_existing(paths: list[Path]) -> str:
    for path in paths:
        if path.exists():
            return str(path)
    return str(paths[0]) if paths else ""


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _review_latest_hash(project: str) -> str:
    review_latest = _agents_root() / "worktrees" / project / "review-latest"
    if not review_latest.exists():
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", str(review_latest), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _receipt_paths(project: str, task_id: str) -> list[str]:
    if not task_id:
        return []
    handoff_dir = _agents_root() / "tasks" / project / "patrol" / "handoffs"
    if not handoff_dir.exists():
        return []
    matches = sorted(handoff_dir.glob(f"{task_id}*.json"))
    return [str(path) for path in matches[:12]]


def _resolve_planner(project: str, seat_hint: str, team_hint: str) -> tuple[str, str]:
    profile = load_profile_v3(_profile_path(project))
    seat = seat_hint.strip()
    if not seat or seat == "planner":
        planner_roles = {"planner", "planner-dispatcher"}
        planners = [s for s in profile.seats if profile.seat_roles.get(s) in planner_roles]
        if len(planners) != 1:
            raise ProfileV3Error(f"cannot infer planner seat; candidates={planners}")
        seat = planners[0]

    team = team_hint.strip()
    if not team:
        team = profile.team_of(seat)
    return seat, team


def _latest_state(state: dict[str, object]):
    if not state:
        return None
    return max(state.values(), key=lambda ts: ts.last_seq)


def _write_snapshot(
    *,
    project: str,
    team: str,
    planner_seat: str,
    reason: str,
    queue_path: Path,
    queue_status: str,
    latest,
    open_tasks: list[object],
    marker_present: bool,
) -> Path:
    root = _runtime_root(project, team)
    root.mkdir(parents=True, exist_ok=True)
    task_id = latest.task_id if latest else ""
    workflow_path = _agents_root() / "tasks" / project / team / "workflow" / f"{task_id}.md"
    acceptance_dir = _agents_root() / "tasks" / project / team / "acceptance"
    delivery_candidates = [
        _agents_root() / "tasks" / project / planner_seat / "DELIVERY.md",
        _agents_root() / "tasks" / project / team / "DELIVERY.md",
    ]
    brief_path = Path(latest.brief_path) if latest and latest.brief_path else Path("")
    if brief_path and not brief_path.is_absolute():
        brief_path = _agents_root() / brief_path

    receipts = _receipt_paths(project, task_id)
    lines = [
        "# Planner Compact Snapshot",
        "",
        f"- generated_at: {_utc_now()}",
        f"- project: {project}",
        f"- team: {team}",
        f"- planner_seat: {planner_seat}",
        f"- reason: {reason}",
        f"- marker_present: {str(marker_present).lower()}",
        f"- queue_path: {queue_path}",
        f"- queue_status: {queue_status}",
    ]
    if latest:
        lines.extend(
            [
                f"- latest_task_id: {latest.task_id}",
                f"- latest_status: {latest.status}",
                f"- latest_seq: {latest.last_seq}",
                f"- latest_event_ts: {latest.last_event_ts}",
                f"- workflow: {workflow_path}",
                f"- brief: {brief_path if str(brief_path) != '.' else ''}",
                f"- delivery: {_first_existing(delivery_candidates)}",
                f"- acceptance_dir: {acceptance_dir}",
                f"- review_latest_hash: {_review_latest_hash(project)}",
            ]
        )
    if open_tasks:
        lines.append("")
        lines.append("## Open Tasks")
        for item in open_tasks:
            lines.append(f"- {item.task_id}: {item.status} (seq={item.last_seq})")
    if receipts:
        lines.append("")
        lines.append("## Handoff Receipts")
        for receipt in receipts:
            lines.append(f"- {receipt}")
    lines.append("")
    snapshot = root / "latest.md"
    snapshot.write_text("\n".join(lines), encoding="utf-8")
    return snapshot


def _send_compact(project: str, planner_seat: str, send_script: Path, *, dry_run: bool) -> None:
    if dry_run:
        return
    result = subprocess.run(
        ["bash", str(send_script), "--project", project, planner_seat, "/compact"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout or f"exit {result.returncode}").split())
        raise RuntimeError(f"send /compact failed: {detail}")


def run(args: argparse.Namespace) -> int:
    workspace_hint = _infer_workspace_hint(args.workspace)
    project = args.project or workspace_hint.project
    if not project:
        print("COMPACT_SKIP reason=project_unresolved")
        return 0

    try:
        planner_seat, team = _resolve_planner(project, args.seat or workspace_hint.seat, args.team)
    except Exception as exc:  # noqa: BLE001
        print(f"COMPACT_SKIP reason=profile_unresolved detail={exc}")
        return 0

    queue = _queue_path(project, team)
    if not queue.exists():
        print(f"COMPACT_SKIP reason=queue_missing project={project} team={team}")
        return 0

    state = read_current_state(queue)
    latest = _latest_state(state)
    open_tasks = sorted(
        [ts for ts in state.values() if ts.status != "task_done"],
        key=lambda ts: ts.last_seq,
    )
    queue_status = queue_state_label(state)
    marker_present = MARKER in args.text

    if open_tasks:
        if not args.dry_run:
            _write_snapshot(
                project=project,
                team=team,
                planner_seat=planner_seat,
                reason="marker_active_queue" if marker_present else "active_queue",
                queue_path=queue,
                queue_status=queue_status,
                latest=latest,
                open_tasks=open_tasks,
                marker_present=marker_present,
            )
        print(
            f"COMPACT_SKIP reason=queue_not_drained project={project} team={team} "
            f"open={len(open_tasks)} queue_status={queue_status}"
        )
        return 0

    if latest is None:
        print(f"COMPACT_SKIP reason=empty_queue project={project} team={team}")
        return 0

    if latest.status != "task_done" and not marker_present:
        print(f"COMPACT_SKIP reason=latest_not_done project={project} team={team} status={latest.status}")
        return 0

    root = _runtime_root(project, team)
    with _state_lock(root):
        state_path = root / "state.json"
        compact_state = _load_json(state_path)
        last_seq = int(compact_state.get("last_compacted_queue_seq") or 0)
        marker_hash = _short_hash(args.text) if marker_present else ""
        if latest.last_seq <= last_seq:
            print(
                "COMPACT_SKIP "
                f"reason=already_compacted project={project} team={team} seq={latest.last_seq}"
            )
            return 0

        reason = "marker" if marker_present else "queue_drained"
        snapshot = root / "latest.md"
        if not args.dry_run:
            snapshot = _write_snapshot(
                project=project,
                team=team,
                planner_seat=planner_seat,
                reason=reason,
                queue_path=queue,
                queue_status=queue_status,
                latest=latest,
                open_tasks=open_tasks,
                marker_present=marker_present,
            )

        send_script_raw = args.send_script or os.environ.get("CLAWSEAT_PLANNER_COMPACT_SEND_SCRIPT", "")
        send_script = Path(send_script_raw) if send_script_raw else REPO_ROOT / "core" / "shell-scripts" / "send-and-verify.sh"
        if not send_script.exists():
            print(f"COMPACT_SKIP reason=send_script_missing path={send_script}")
            return 0

        try:
            _send_compact(project, planner_seat, send_script, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"COMPACT_FAILED project={project} team={team} task_id={latest.task_id} detail={exc}")
            return 1

        compact_state.update(
            {
                "last_compacted_at": _utc_now(),
                "last_compacted_task_id": latest.task_id,
                "last_compacted_queue_seq": latest.last_seq,
                "last_marker_hash": marker_hash,
                "snapshot": str(snapshot),
            }
        )
        if not args.dry_run:
            _write_json(state_path, compact_state)
        prefix = "COMPACT_DRY_RUN " if args.dry_run else "COMPACT_SENT "
        print(
            prefix
            + f"project={project} team={team} seat={planner_seat} "
            + f"task_id={latest.task_id} seq={latest.last_seq} snapshot={snapshot}"
        )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("PLANNER_AUTO_COMPACT_PROJECT", ""))
    parser.add_argument("--team", default=os.environ.get("PLANNER_AUTO_COMPACT_TEAM", ""))
    parser.add_argument("--seat", default=os.environ.get("PLANNER_AUTO_COMPACT_SEAT", ""))
    parser.add_argument("--workspace", default=os.environ.get("PLANNER_AUTO_COMPACT_WORKSPACE", ""))
    parser.add_argument("--text", default=os.environ.get("PLANNER_AUTO_COMPACT_TEXT", ""))
    parser.add_argument("--send-script", default=os.environ.get("CLAWSEAT_PLANNER_COMPACT_SEND_SCRIPT", ""))
    parser.add_argument("--dry-run", action="store_true", default=os.environ.get("PLANNER_AUTO_COMPACT_DRY_RUN") == "1")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
