#!/usr/bin/env python3
"""Universal pane-content watchdog for marker-driven actions and capacity retry."""
from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CORE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CORE_ROOT.parent
CORE_LIB = CORE_ROOT / "lib"
if str(CORE_LIB) not in sys.path:
    sys.path.insert(0, str(CORE_LIB))

from profile_loader_v3 import ProfileV3Error, load_profile_v3  # noqa: E402


CLEAR_MARKER_RE = re.compile(r"\[(CLEAR)-REQUESTED\]")
COMPACT_MARKER_RE = re.compile(r"\[(COMPACT)-REQUESTED\]")
CAPACITY_MARKER_RE = re.compile(r"Selected model is at capacity|AT CAPACITY", re.IGNORECASE)
THINKING_RE = re.compile(r"(thinking|wibbling|working|honking)", re.IGNORECASE)
_SESSION_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")

MarkerAction = Callable[[str, str], None]
CapacityExceeded = Callable[[str, str | None, str, int], None]


@dataclass(frozen=True)
class Handler:
    name: str
    marker_re: re.Pattern[str]
    action: MarkerAction
    cooldown_seconds: int
    max_retries: int | None
    on_max_exceeded: CapacityExceeded | None
    skip_if_thinking: bool = True


def _home() -> Path:
    return Path(os.environ.get("CLAWSEAT_REAL_HOME") or os.environ.get("HOME") or str(Path.home())).expanduser()


def _runtime_root() -> Path:
    return Path(os.environ.get("CLAWSEAT_RUNTIME_ROOT") or (_home() / ".agent-runtime")).expanduser()


def _agents_root() -> Path:
    return _home() / ".agents"


def _profile_path(project: str) -> Path:
    return _agents_root() / "profiles" / f"{project}-profile-dynamic.toml"


def _project_names() -> list[str]:
    root = _home() / ".agents" / "projects"
    if not root.exists():
        return []
    return sorted((path.name for path in root.iterdir() if path.is_dir()), key=len, reverse=True)


def _session_project(session: str, projects: list[str]) -> str | None:
    for project in projects:
        if session == project or session.startswith(f"{project}-"):
            return project
    if "-" in session:
        return session.split("-", 1)[0]
    return None


def _session_matches_seat(session: str, project: str, seat: str) -> bool:
    prefix = f"{project}-{seat}"
    return session == prefix or session.startswith(f"{prefix}-")


def _planner_seats(project: str) -> list[str]:
    try:
        profile = load_profile_v3(_profile_path(project))
    except ProfileV3Error:
        return []
    planner_roles = {"planner", "planner-dispatcher"}
    return [seat for seat in profile.seats if profile.seat_roles.get(seat) in planner_roles]


def _list_live_sessions(tmux_bin: str = "tmux") -> list[str]:
    try:
        out = subprocess.check_output(
            [tmux_bin, "list-sessions", "-F", "#{session_name}"],
            text=True,
            env={**os.environ, "TMUX": ""},
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _capture_pane(session: str, *, tmux_bin: str = "tmux", lines: int = 120) -> str:
    try:
        return subprocess.check_output(
            [tmux_bin, "capture-pane", "-t", session, "-p", "-S", f"-{lines}"],
            text=True,
            env={**os.environ, "TMUX": ""},
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _send_command(session: str, command: str, *, tmux_bin: str = "tmux") -> None:
    subprocess.run(
        [tmux_bin, "send-keys", "-t", session, command, "Enter"],
        check=True,
        env={**os.environ, "TMUX": ""},
    )


def _notify_capacity_exceeded(session: str, project: str | None, marker_line: str, retries: int) -> None:
    print(
        f"watchdog capacity exceeded: seat={session}, project={project}, marker_line={marker_line}, retries={retries}",
        file=sys.stderr,
    )


def _marker_line(pane_text: str, match: re.Match[str]) -> str:
    start = pane_text.rfind("\n", 0, match.start()) + 1
    end = pane_text.find("\n", match.end())
    if end < 0:
        end = len(pane_text)
    return pane_text[start:end].strip()


def _marker_hash(session: str, action: str, marker_line: str) -> str:
    seed = f"{session}:{action}:{marker_line}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _safe_session(session: str) -> str:
    return _SESSION_SAFE_RE.sub("_", session)


def _seen_path(session: str, runtime_root: Path | None = None) -> Path:
    return (runtime_root or _runtime_root()) / "watchdog" / f"{_safe_session(session)}.seen"


def _capacity_state_path(session: str, runtime_root: Path | None = None) -> Path:
    return (runtime_root or _runtime_root()) / "watchdog" / f"{_safe_session(session)}.capacity.json"


def _mark_seen_if_new(session: str, marker_hash: str, runtime_root: Path | None = None) -> bool:
    path = _seen_path(session, runtime_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        seen = {line.strip() for line in handle if line.strip()}
        if marker_hash in seen:
            return False
        handle.write(marker_hash + "\n")
        handle.flush()
        return True


def _utc_now(now: float | None = None) -> str:
    dt = datetime.datetime.fromtimestamp(now if now is not None else time.time(), tz=datetime.timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _parse_utc_time(value: object) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value
    if value.endswith("Z"):
        normalized = value[:-1] + "+00:00"
    try:
        return datetime.datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _load_capacity_state(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    required = {
        "first_seen_at",
        "retries",
        "last_retry_at",
        "next_retry_at",
        "marker_line",
        "escalated",
    }
    if not required.issubset(data):
        return None
    return data


def _write_capacity_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _delete_capacity_state(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _new_capacity_state(marker_line: str, now: float, cooldown_seconds: int) -> dict[str, object]:
    timestamp = _utc_now(now)
    return {
        "first_seen_at": timestamp,
        "retries": 0,
        "last_retry_at": timestamp,
        "next_retry_at": _utc_now(now + cooldown_seconds),
        "marker_line": marker_line,
        "escalated": False,
    }


def _build_handlers(tmux_bin: str) -> list[Handler]:
    return [
        Handler(
            name="capacity",
            marker_re=CAPACITY_MARKER_RE,
            action=lambda session, _marker_line: _send_command(session, "继续", tmux_bin=tmux_bin),
            cooldown_seconds=10,
            max_retries=3,
            on_max_exceeded=lambda session, marker_project, marker_line, retries: _notify_capacity_exceeded(
                session,
                marker_project,
                marker_line,
                retries,
            ),
            skip_if_thinking=True,
        ),
        Handler(
            name="clear",
            marker_re=CLEAR_MARKER_RE,
            action=lambda session, _marker_line: _send_command(session, "/clear", tmux_bin=tmux_bin),
            cooldown_seconds=0,
            max_retries=None,
            on_max_exceeded=None,
            skip_if_thinking=True,
        ),
        Handler(
            name="compact",
            marker_re=COMPACT_MARKER_RE,
            action=lambda session, _marker_line: _send_command(session, "/compact", tmux_bin=tmux_bin),
            cooldown_seconds=0,
            max_retries=None,
            on_max_exceeded=None,
            skip_if_thinking=True,
        ),
    ]


def _scan_capacity_handler(
    session: str,
    marker_line: str,
    *,
    handler: Handler,
    project: str | None,
    runtime_root: Path | None,
    now: float,
    dry_run: bool,
) -> tuple[str, str | None]:
    state_path = _capacity_state_path(session, runtime_root=runtime_root)
    state = _load_capacity_state(state_path)

    if not isinstance(state, dict) or state.get("marker_line") != marker_line:
        state = _new_capacity_state(marker_line, now, handler.cooldown_seconds)
        if not dry_run:
            _write_capacity_state(state_path, state)
        return "seen", None

    if not isinstance(state.get("escalated"), bool):
        state["escalated"] = False

    if state.get("escalated"):
        return "seen", None

    next_retry_at = _parse_utc_time(state.get("next_retry_at"))
    if next_retry_at is None:
        return "seen", None
    if now < next_retry_at:
        return "seen", None

    retries = int(state.get("retries", 0) or 0)
    max_retries = handler.max_retries or 0
    if retries >= max_retries:
        if handler.on_max_exceeded:
            handler.on_max_exceeded(session, project, marker_line, retries)
        state["escalated"] = True
        if not dry_run:
            _write_capacity_state(state_path, state)
        return "seen", None

    if dry_run:
        return "seen", None

    handler.action(session, marker_line)
    state["retries"] = retries + 1
    state["last_retry_at"] = _utc_now(now)
    state["next_retry_at"] = _utc_now(now + handler.cooldown_seconds)
    print(f"sent 继续 to {session}")
    _write_capacity_state(state_path, state)
    return "sent", "继续"


def _scan_session(
    session: str,
    *,
    project: str | None,
    tmux_bin: str = "tmux",
    lines: int = 120,
    runtime_root: Path | None = None,
    dry_run: bool = False,
) -> tuple[str, str | None]:
    pane_text = _capture_pane(session, tmux_bin=tmux_bin, lines=lines)
    if not pane_text:
        return "empty", None
    if THINKING_RE.search(pane_text):
        return "thinking", None

    now = time.time()
    status = "none"
    commands: list[str] = []
    for handler in _build_handlers(tmux_bin=tmux_bin):
        match = handler.marker_re.search(pane_text)
        if not match:
            if handler.name == "capacity":
                _delete_capacity_state(_capacity_state_path(session, runtime_root=runtime_root))
            continue

        marker = _marker_line(pane_text, match)
        if handler.name == "capacity":
            action_status, action = _scan_capacity_handler(
                session,
                marker,
                handler=handler,
                project=project,
                runtime_root=runtime_root,
                now=now,
                dry_run=dry_run,
            )
        else:
            action = f"/{handler.name}"
            marker_hash = _marker_hash(session, handler.name, marker)
            if dry_run:
                print(f"[dry-run] {session}: {action}")
                action_status = "sent"
            else:
                if not _mark_seen_if_new(session, marker_hash, runtime_root):
                    action_status = "seen"
                else:
                    handler.action(session, marker)
                    print(f"sent {action} to {session}")
                    action_status = "sent"

        if action_status == "sent":
            status = "sent"
            if action:
                commands.append(action)
        elif action_status == "seen" and status != "sent":
            status = "seen"

    return status, ", ".join(commands) if commands else None


def _planner_auto_compact_script() -> Path:
    return REPO_ROOT / "core" / "skills" / "planner" / "scripts" / "planner_auto_compact.py"


def _run_planner_auto_compact(
    *,
    project: str,
    seat: str,
    dry_run: bool,
) -> tuple[str, str]:
    helper = _planner_auto_compact_script()
    if not helper.exists():
        return "skip", f"helper_missing:{helper}"
    env = dict(os.environ)
    env.setdefault("CLAWSEAT_REAL_HOME", str(_home()))
    env["PLANNER_AUTO_COMPACT_PROJECT"] = project
    env["PLANNER_AUTO_COMPACT_SEAT"] = seat
    env["PLANNER_AUTO_COMPACT_WORKSPACE"] = str(_agents_root() / "workspaces" / project / seat)
    if dry_run:
        env["PLANNER_AUTO_COMPACT_DRY_RUN"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, str(helper)],
            capture_output=True,
            text=True,
            env=env,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "failed", str(exc)

    output = " ".join((result.stdout or result.stderr or "").split())
    if result.returncode != 0:
        return "failed", output or f"exit {result.returncode}"
    if output.startswith("COMPACT_SENT") or output.startswith("COMPACT_DRY_RUN"):
        return "sent", output
    return "skip", output


def _scan_planner_auto_compact(
    *,
    project: str | None,
    sessions: list[str],
    projects: list[str],
    tmux_bin: str,
    lines: int,
    dry_run: bool,
) -> dict[str, int]:
    stats = {"planner_compact_sent": 0, "planner_compact_skip": 0, "planner_compact_failed": 0}
    target_projects = [project] if project else projects
    for project_name in target_projects:
        if not project_name:
            continue
        for seat in _planner_seats(project_name):
            session = next((s for s in sessions if _session_matches_seat(s, project_name, seat)), "")
            if not session:
                stats["planner_compact_skip"] += 1
                continue
            pane_text = _capture_pane(session, tmux_bin=tmux_bin, lines=lines)
            if THINKING_RE.search(pane_text):
                stats["planner_compact_skip"] += 1
                continue
            status, detail = _run_planner_auto_compact(
                project=project_name,
                seat=seat,
                dry_run=dry_run,
            )
            if status == "sent":
                stats["planner_compact_sent"] += 1
            elif status == "failed":
                stats["planner_compact_failed"] += 1
                print(f"planner_auto_compact failed: project={project_name} seat={seat} detail={detail}", file=sys.stderr)
            else:
                stats["planner_compact_skip"] += 1
    return stats


def scan_once(
    *,
    project: str | None = None,
    tmux_bin: str = "tmux",
    lines: int = 120,
    runtime_root: Path | None = None,
    dry_run: bool = False,
    planner_auto_compact: bool = True,
) -> dict[str, int]:
    stats = {
        "sessions": 0,
        "sent": 0,
        "seen": 0,
        "thinking": 0,
        "none": 0,
        "empty": 0,
        "planner_compact_sent": 0,
        "planner_compact_skip": 0,
        "planner_compact_failed": 0,
    }
    projects = _project_names()
    if project and project not in projects:
        projects = [project, *projects]
    sessions = _list_live_sessions(tmux_bin)
    for session in sessions:
        if project:
            if _session_project(session, projects) != project:
                continue
        elif projects and _session_project(session, projects) not in projects:
            continue

        stats["sessions"] += 1
        status, _command = _scan_session(
            session,
            project=_session_project(session, projects),
            tmux_bin=tmux_bin,
            lines=lines,
            runtime_root=runtime_root,
            dry_run=dry_run,
        )
        stats[status] = stats.get(status, 0) + 1
    if planner_auto_compact and os.environ.get("CLAWSEAT_PLANNER_AUTO_COMPACT_ENABLED", "1") == "1":
        compact_stats = _scan_planner_auto_compact(
            project=project,
            sessions=sessions,
            projects=projects,
            tmux_bin=tmux_bin,
            lines=lines,
            dry_run=dry_run,
        )
        for key, value in compact_stats.items():
            stats[key] = stats.get(key, 0) + value
    return stats


def run_loop(
    *,
    interval: int,
    project: str | None = None,
    tmux_bin: str = "tmux",
    lines: int = 120,
    runtime_root: Path | None = None,
    dry_run: bool = False,
    planner_auto_compact: bool = True,
) -> int:
    while True:
        stats = scan_once(
            project=project,
            tmux_bin=tmux_bin,
            lines=lines,
            runtime_root=runtime_root,
            dry_run=dry_run,
            planner_auto_compact=planner_auto_compact,
        )
        print(" ".join(f"{key}={value}" for key, value in stats.items()))
        time.sleep(interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Scan once and exit.")
    parser.add_argument("--interval", type=int, default=None, help="Run as a daemon, scanning every N seconds.")
    parser.add_argument("--project", help="Limit scan to one project.")
    parser.add_argument("--lines", type=int, default=120, help="Pane tail lines to inspect.")
    parser.add_argument("--tmux-bin", default="tmux", help="tmux executable path.")
    parser.add_argument("--runtime-root", help="Override ~/.agent-runtime for tests.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without tmux send-keys.")
    parser.add_argument(
        "--no-planner-auto-compact",
        action="store_true",
        help="Disable queue-drained planner auto-compact scan.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime_root = Path(args.runtime_root).expanduser() if args.runtime_root else None
    if args.interval is not None and not args.once:
        return run_loop(
            interval=args.interval,
            project=args.project,
            tmux_bin=args.tmux_bin,
            lines=args.lines,
            runtime_root=runtime_root,
            dry_run=args.dry_run,
            planner_auto_compact=not args.no_planner_auto_compact,
        )
    stats = scan_once(
        project=args.project,
        tmux_bin=args.tmux_bin,
        lines=args.lines,
        runtime_root=runtime_root,
        dry_run=args.dry_run,
        planner_auto_compact=not args.no_planner_auto_compact,
    )
    print(" ".join(f"{key}={value}" for key, value in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
