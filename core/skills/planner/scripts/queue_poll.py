#!/usr/bin/env python3
"""Planner queue poll script (Phase 3).

Invoked by:
- SessionStart hook (one-shot at planner startup)
- launchd / cron (every 60s during planner session)
- manual debug

Behavior:
1. Discover team queues this planner is responsible for (from project.toml
   teams metadata via profile_loader_v3).
2. For each team queue, find oldest `task_created` task.
3. If found and depends_on satisfied, attempt claim. Print task_id +
   brief path to stdout so the wrapping daemon/notification can route it.
4. If depends_on unmet, helper already wrote task_waiting_for; move on.

No blocking, no sleep — caller controls polling cadence.

Exit codes:
  0: ran successfully (may or may not have claimed anything)
  1: configuration error (missing profile)
  2: I/O error (queue corrupt, etc.)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
_CORE_LIB = REPO_ROOT / "core" / "lib"
if str(_CORE_LIB) not in sys.path:
    sys.path.insert(0, str(_CORE_LIB))

from profile_loader_v3 import ProfileV3Error, load_profile_v3  # noqa: E402
from queue_io import (  # noqa: E402
    QueueError,
    append_event,
    read_current_state,
)
from acceptance_criteria import (  # noqa: E402
    brief_acceptance_ready,
    load_brief_frontmatter,
)
from real_home import real_user_home  # noqa: E402


def _agents_root() -> Path:
    return real_user_home() / ".agents"


def _queue_path(project: str, team: str) -> Path:
    return _agents_root() / "tasks" / project / team / "tasks.queue.jsonl"


def _project_team_root(project: str, team: str) -> Path:
    return _agents_root() / "tasks" / project / team


def _resolve_task_brief_path(project: str, team: str, brief_path: str | None) -> Path | None:
    if not brief_path:
        return None
    path = Path(brief_path)
    if path.is_absolute():
        return path
    agents_relative = _agents_root() / path
    team_relative = _project_team_root(project, team) / path
    if agents_relative.exists() or not team_relative.exists():
        return agents_relative
    return team_relative


def _brief_acceptance_ready_for_task(project: str, team: str, brief_path: str | None) -> tuple[bool, str]:
    resolved = _resolve_task_brief_path(project, team, brief_path)
    if resolved is None:
        return False, "brief_path missing"
    if not resolved.exists():
        return False, f"brief not found: {resolved}"
    try:
        return brief_acceptance_ready(load_brief_frontmatter(resolved))
    except Exception as exc:  # noqa: BLE001
        return False, f"brief acceptance parse failed: {exc}"


def _list_project_teams(project: str) -> list[str]:
    proj_root = _agents_root() / "tasks" / project
    if not proj_root.exists():
        return []
    return sorted(
        c.name for c in proj_root.iterdir()
        if c.is_dir() and (c / "tasks.queue.jsonl").exists()
    )


def _cross_team_upstream_status(project: str, current_team: str, task_id: str) -> str | None:
    """Post-retest #5: scan all sibling team queues for a task_id."""
    for team in _list_project_teams(project):
        if team == current_team:
            continue
        state = read_current_state(_queue_path(project, team))
        if task_id in state:
            return state[task_id].status
    return None


def _is_upstream_done(state: dict, project: str, current_team: str, upstream_id: str) -> bool:
    up = state.get(upstream_id)
    if up is not None:
        return up.status == "task_done"
    cross_status = _cross_team_upstream_status(project, current_team, upstream_id)
    return cross_status == "task_done"


def poll_team(project: str, team: str, actor: str) -> dict | None:
    """Poll one team queue. Returns a dict describing the claim outcome, or
    None when nothing pending. Caller can serialize this back to logs / notify
    pipeline. Never raises on absent queue (warm-start case).
    """
    queue = _queue_path(project, team)
    if not queue.exists():
        return None
    state = read_current_state(queue)
    # FIFO by last_seq among task_created
    pending = sorted(
        [ts for ts in state.values() if ts.status == "task_created"],
        key=lambda t: t.last_seq,
    )
    if not pending:
        # Also retry any task_waiting_for items whose upstream is now done
        waiting = sorted(
            [ts for ts in state.values() if ts.status == "task_waiting_for"],
            key=lambda t: t.last_seq,
        )
        for ts in waiting:
            unmet = [
                u for u in ts.depends_on
                if not _is_upstream_done(state, project, team, u)
            ]
            acceptance_ready, acceptance_reason = _brief_acceptance_ready_for_task(
                project, team, ts.brief_path
            )
            if not acceptance_ready:
                try:
                    append_event(queue, {
                        "event_type": "task_waiting_for",
                        "actor": actor,
                        "task_id": ts.task_id,
                        "waiting_for": "acceptance_criteria",
                    })
                except QueueError:
                    pass
                return {
                    "team": team,
                    "task_id": ts.task_id,
                    "brief_path": ts.brief_path,
                    "verdict": "waiting_for",
                    "waiting_for": "acceptance_criteria",
                    "reason": acceptance_reason,
                }
            if not unmet:
                # Upstream done — retry claim
                try:
                    append_event(queue, {
                        "event_type": "task_claimed",
                        "actor": actor,
                        "task_id": ts.task_id,
                    })
                except QueueError:
                    continue
                return {
                    "team": team,
                    "task_id": ts.task_id,
                    "brief_path": ts.brief_path,
                    "verdict": "claimed_after_waiting",
                }
        return None

    # Try to claim the oldest pending. depends_on check inline (cross-team aware).
    candidate = pending[0]
    acceptance_ready, acceptance_reason = _brief_acceptance_ready_for_task(
        project, team, candidate.brief_path
    )
    if not acceptance_ready:
        try:
            append_event(queue, {
                "event_type": "task_waiting_for",
                "actor": actor,
                "task_id": candidate.task_id,
                "waiting_for": "acceptance_criteria",
            })
        except QueueError:
            pass
        return {
            "team": team,
            "task_id": candidate.task_id,
            "brief_path": candidate.brief_path,
            "verdict": "waiting_for",
            "waiting_for": "acceptance_criteria",
            "reason": acceptance_reason,
        }
    unmet = [
        u for u in candidate.depends_on
        if not _is_upstream_done(state, project, team, u)
    ]
    if unmet:
        try:
            append_event(queue, {
                "event_type": "task_waiting_for",
                "actor": actor,
                "task_id": candidate.task_id,
                "waiting_for": unmet[0],
            })
        except QueueError:
            pass
        return {
            "team": team,
            "task_id": candidate.task_id,
            "brief_path": candidate.brief_path,
            "verdict": "waiting_for",
            "waiting_for": unmet[0],
        }

    try:
        append_event(queue, {
            "event_type": "task_claimed",
            "actor": actor,
            "task_id": candidate.task_id,
        })
    except QueueError as exc:
        # Race: another planner claimed it. Not an error; just no-op this tick.
        return {
            "team": team,
            "task_id": candidate.task_id,
            "verdict": "race_lost",
            "reason": str(exc),
        }
    return {
        "team": team,
        "task_id": candidate.task_id,
        "brief_path": candidate.brief_path,
        "verdict": "claimed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Planner queue poll: claim oldest pending task or retry waiting_for."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--team",
        default=None,
        help="Single team to poll. If absent, polls all teams from project.toml.",
    )
    parser.add_argument(
        "--actor",
        required=True,
        help="<role>@<tool> e.g. planner@claude",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Optional profile path; needed when --team absent (default ~/.agents/profiles/<p>-profile-dynamic.toml)",
    )
    args = parser.parse_args(argv)

    teams_to_poll: list[str]
    if args.team:
        teams_to_poll = [args.team]
    else:
        profile_path = args.profile or str(
            _agents_root() / "profiles" / f"{args.project}-profile-dynamic.toml"
        )
        try:
            profile = load_profile_v3(profile_path)
        except ProfileV3Error as exc:
            print(f"profile load failed: {exc}", file=sys.stderr)
            return 1
        if profile.is_multi():
            teams_to_poll = list(profile.teams.keys())
        else:
            teams_to_poll = ["default"]

    results = []
    for team in teams_to_poll:
        r = poll_team(args.project, team, args.actor)
        if r is not None:
            results.append(r)
            print(f"{r['team']}\t{r.get('verdict','?')}\t{r.get('task_id','-')}\t"
                  f"{r.get('brief_path') or r.get('reason','')}")

    if not results:
        # Silent success — useful for cron / launchd
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
