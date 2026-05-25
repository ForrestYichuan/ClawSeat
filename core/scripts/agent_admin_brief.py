#!/usr/bin/env python3
"""ClawSeat v3 brief subcommand.

Memory writes brief markdown + appends task_created event to per-team queue.
Planner reads queue via 7-step loop (see core/lib/queue_io.py).

Subcommands:
  queue    Write brief file + append task_created event
  list     Show pending tasks for a team
  claim    Planner claims a pending task
  reset    Mark a task reset so it can be recovered
  requeue  Recover a blocked/reset task with its existing brief
  show     Show current state of a task_id

Phase 1 minimal scope: standalone CLI. Phase 2 integrates into agent_admin.py
PARSER_HOOKS dispatch.

See spec §4.2 (brief schema) + §4.3 (queue events).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_LIB = _REPO_ROOT / "core" / "lib"
_CORE_SCRIPTS = _REPO_ROOT / "core" / "scripts"
for _p in (str(_CORE_LIB), str(_CORE_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from queue_io import (  # noqa: E402
    QueueError,
    append_event,
    queue_is_drained,
    queue_state_label,
    query_pending,
    read_current_state,
)
from _toml_compat import load_safe as _toml_load_safe  # noqa: E402
from profile_loader_v3 import ProfileV3Error, load_profile_v3  # noqa: E402
from real_home import real_user_home  # noqa: E402
import agent_admin_config as _cfg  # noqa: E402

try:
    import yaml  # type: ignore
except ImportError as _exc:  # pragma: no cover
    raise SystemExit("PyYAML required for agent_admin_brief")


class _QuotedStrDumper(yaml.SafeDumper):
    """SafeDumper that single-quotes all str scalars.

    Why: default safe_dump emits plain ISO datetime strings, which round-trip
    through safe_load become datetime objects. jsonschema then rejects them
    because schema declares string. Quoting forces unambiguous str on load.
    Fix #B (post-review retest).
    """


def _quoted_str_representer(dumper, data):  # type: ignore[no-untyped-def]
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")


_QuotedStrDumper.add_representer(str, _quoted_str_representer)

# Post-review fix #4: input validation to prevent path traversal.
# Patterns must mirror the schemas: project/team match project_toml_v3.schema
# (^[a-z0-9][a-z0-9-]*$) and task_id matches brief.schema (^[A-Za-z0-9][A-Za-z0-9_.-]*$).
_PROJECT_TEAM_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class InputValidationError(RuntimeError):
    pass


class WakeHookError(RuntimeError):
    def __init__(self, reason: str, *, target: str = "") -> None:
        super().__init__(reason)
        self.target = target


# ---------------------------------------------------------------------------
# Warden bridge — operator-overlay test channel
#
# Warden is the human operator (not a second memory seat).  In normal
# operation memory communicates through the standard chat surface; the inbox
# file below is a fallback durable path for smoke-test scenarios where direct
# chat is unavailable (e.g. tmux session not yet started, seat offline).
# It is NOT a permanent seat-to-seat protocol; do not reference it in hot
# prompts or generated workspace prose.
# ---------------------------------------------------------------------------

WARDEN_INBOX_FILENAME = "warden-inbox.md"


def warden_inbox_path(project: str) -> Path:
    """Return the operator-overlay inbox path for the project."""
    return real_user_home() / ".agents" / "tasks" / project / WARDEN_INBOX_FILENAME


def write_warden_blocked(project: str, task_id: str, message: str) -> None:
    """Append a BLOCKED report to the operator-overlay inbox.

    Used in smoke-test / offline scenarios when memory cannot proceed and
    normal chat is unavailable.  Each call appends a timestamped entry.
    """
    path = warden_inbox_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = f"\n## BLOCKED {ts} task={task_id}\n\n{message}\n"
    with path.open("a") as fh:
        fh.write(entry)


def _validate_identifier(value: str, kind: str) -> None:
    pattern = _TASK_ID_PATTERN if kind == "task_id" else _PROJECT_TEAM_PATTERN
    if not pattern.match(value or ""):
        raise InputValidationError(
            f"invalid {kind}: {value!r} (must match {pattern.pattern})"
        )
    if ".." in value or "/" in value or "\\" in value:
        raise InputValidationError(
            f"invalid {kind}: {value!r} contains path-traversal characters"
        )


def _validate_cli_inputs(project: str, team: str, task_id: str | None = None) -> None:
    _validate_identifier(project, "project")
    _validate_identifier(team, "team")
    if task_id is not None:
        _validate_identifier(task_id, "task_id")


def _validate_external_brief_content(
    brief_text: str, source_path: str, project: str, team: str, task_id: str
) -> None:
    """Validate caller-supplied brief content (--brief-content-file).

    Post-retest #2: parse frontmatter, verify schema + CLI match. Raise
    InputValidationError on any failure.
    """
    if not brief_text.startswith("---\n"):
        raise InputValidationError(
            f"{source_path}: brief content must start with '---' frontmatter"
        )
    end = brief_text.find("\n---\n", 4)
    if end == -1:
        end = brief_text.find("\n---", 4)
    if end == -1:
        raise InputValidationError(f"{source_path}: unterminated frontmatter")

    try:
        data = yaml.safe_load(brief_text[4:end])
    except Exception as exc:  # noqa: BLE001
        raise InputValidationError(f"{source_path}: frontmatter parse error: {exc}")
    if not isinstance(data, dict):
        raise InputValidationError(f"{source_path}: frontmatter must be a mapping")

    # CLI match
    for field_name, expected in (("task_id", task_id), ("project", project), ("team", team)):
        actual = data.get(field_name)
        if actual is None:
            raise InputValidationError(
                f"{source_path}: brief missing required field {field_name!r}"
            )
        if str(actual) != str(expected):
            raise InputValidationError(
                f"{source_path}: brief.{field_name}={actual!r} mismatches CLI {field_name}={expected!r}"
            )

    # Schema sanity (cheap fallback even without jsonschema)
    seats = data.get("seats_required")
    if not isinstance(seats, list) or not seats:
        raise InputValidationError(
            f"{source_path}: brief.seats_required must have minItems 1"
        )
    ac = data.get("acceptance_criteria") or {}
    if not isinstance(ac, dict):
        raise InputValidationError(
            f"{source_path}: brief.acceptance_criteria must be a mapping"
        )
    has_any_route_item = False
    for route in ("mechanical", "reviewer", "operator"):
        items = ac.get(route) or []
        if not isinstance(items, list):
            raise InputValidationError(
                f"{source_path}: brief.acceptance_criteria.{route} must be a list"
            )
        has_any_route_item = has_any_route_item or bool(items)
    if not has_any_route_item:
        raise InputValidationError(
            f"{source_path}: brief.acceptance_criteria must include at least one route item"
        )


def _brief_frontmatter(brief_text: str, source_path: str) -> dict:
    if not brief_text.startswith("---\n"):
        raise InputValidationError(
            f"{source_path}: brief content must start with '---' frontmatter"
        )
    end = brief_text.find("\n---\n", 4)
    if end == -1:
        end = brief_text.find("\n---", 4)
    if end == -1:
        raise InputValidationError(f"{source_path}: unterminated frontmatter")
    try:
        data = yaml.safe_load(brief_text[4:end])
    except Exception as exc:  # noqa: BLE001
        raise InputValidationError(f"{source_path}: frontmatter parse error: {exc}")
    if not isinstance(data, dict):
        raise InputValidationError(f"{source_path}: frontmatter must be a mapping")
    return data


def _criterion_text(item: object) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        fields = [
            item.get("command"),
            item.get("description"),
            item.get("summary"),
            item.get("question"),
        ]
        return " ".join(str(v) for v in fields if v is not None)
    return str(item)


def _brief_acceptance_block_reason(brief_text: str, source_path: str = "<brief>") -> str | None:
    """Return a block reason when a brief is not ready for planner wake/claim."""
    try:
        data = _brief_frontmatter(brief_text, source_path)
    except InputValidationError as exc:
        return str(exc)
    ac = data.get("acceptance_criteria") or {}
    if not isinstance(ac, dict):
        return "acceptance_criteria must be a mapping"
    has_any_route_item = False
    for route in ("mechanical", "reviewer", "operator"):
        items = ac.get(route) or []
        if not isinstance(items, list):
            return f"acceptance_criteria.{route} must be a list"
        has_any_route_item = has_any_route_item or bool(items)
    if not has_any_route_item:
        return "acceptance_criteria has no route items"

    placeholder_patterns = (
        "todo:",
        "todo",
        "replace with",
        "placeholder",
        "待 memory 补全",
        "待补全",
        "<待",
    )
    for route in ("mechanical", "reviewer", "operator"):
        items = ac.get(route) or []
        for item in items:
            text = _criterion_text(item).strip().lower()
            if any(pattern in text for pattern in placeholder_patterns):
                return f"acceptance_criteria.{route} contains placeholder text"
    return None


def _agents_root() -> Path:
    return real_user_home() / ".agents"


def _project_team_root(project: str, team: str) -> Path:
    return _agents_root() / "tasks" / project / team


def _queue_path(project: str, team: str) -> Path:
    return _project_team_root(project, team) / "tasks.queue.jsonl"


def _profile_path(project: str) -> Path:
    return _agents_root() / "profiles" / f"{project}-profile-dynamic.toml"


def _send_script_path() -> Path:
    override = os.environ.get("CLAWSEAT_BRIEF_WAKE_SEND_SCRIPT", "").strip()
    if override:
        return Path(override)
    return _REPO_ROOT / "core" / "shell-scripts" / "send-and-verify.sh"


# ---------------------------------------------------------------------------
# Queue-drained relay helpers (notify_policy=queue_drained_only)
# ---------------------------------------------------------------------------

_COMPLETE_HANDOFF_SCRIPT = (
    _REPO_ROOT / "core" / "skills" / "gstack-harness" / "scripts" / "complete_handoff.py"
)

# Ordered list of known-good Python paths probed when sys.executable lacks toml deps.
_RELAY_PYTHON_FALLBACK_CANDIDATES: list[str] = [
    "/opt/homebrew/opt/python@3.12/bin/python3.12",
    "/opt/homebrew/opt/python@3.11/bin/python3.11",
    "/opt/homebrew/bin/python3",
]


def _can_import_toml_exe(exe: str) -> bool:
    """Return True if *exe* can import tomllib (stdlib 3.11+) or tomli (backport)."""
    for mod in ("tomllib", "tomli"):
        try:
            r = subprocess.run(
                [exe, "-c", f"import {mod}"],
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    return False


def _find_relay_python() -> str:
    """Return a Python interpreter with tomllib/tomli available.

    Checks sys.executable first; if it lacks the required TOML module (happens
    with system Python 3.9 on macOS that predates tomllib), probes known
    Homebrew paths.  Falls back to sys.executable when nothing better is found
    so the caller still gets *some* interpreter.
    """
    if _can_import_toml_exe(sys.executable):
        return sys.executable
    for candidate in _RELAY_PYTHON_FALLBACK_CANDIDATES:
        if candidate != sys.executable and _can_import_toml_exe(candidate):
            return candidate
    return sys.executable


def _team_notify_policy(project: str, team: str) -> str:
    """Read notify_policy for a team from the active profile. Returns '' on failure."""
    profile_file = _profile_path(project)
    if not profile_file.exists():
        return ""
    try:
        with profile_file.open("rb") as fh:
            data = _toml_load_safe(fh)
    except Exception:  # noqa: BLE001
        return ""
    teams = data.get("teams") or {}
    team_cfg = teams.get(team) if isinstance(teams, dict) else None
    if not isinstance(team_cfg, dict):
        return ""
    return str(team_cfg.get("notify_policy") or "").strip()


def _team_queue_is_drained(queue: Path, current_task_id: str) -> bool:
    """True if every task other than current_task_id is task_done."""
    try:
        state = read_current_state(queue)
    except Exception:  # noqa: BLE001
        return False
    return queue_is_drained(state, ignore_task_id=current_task_id)


def _relay_receipt_exists(project: str, task_id: str, planner_seat: str) -> bool:
    """Check if a prior durable relay receipt exists (idempotency guard)."""
    receipt = (
        _agents_root() / "tasks" / project / "patrol" / "handoffs"
        / f"{task_id}__{planner_seat}__memory.json"
    )
    return receipt.exists()


def _do_relay_complete_handoff(
    project: str, team: str, task_id: str, planner_seat: str
) -> None:
    """Invoke complete_handoff.py to relay queue-drained notification to memory.

    This function is module-level so tests can monkeypatch it without invoking
    real tmux, real sessions, or live profile resolution.
    """
    if not _COMPLETE_HANDOFF_SCRIPT.exists():
        raise WakeHookError(
            f"complete_handoff not found: {_COMPLETE_HANDOFF_SCRIPT}",
            target="memory",
        )
    profile = str(_profile_path(project))
    summary = (
        f"Queue drained for team {team!r} after task_done {task_id!r}. "
        "Planner completed all pending work in this team queue."
    )
    result = subprocess.run(
        [
            _find_relay_python(), str(_COMPLETE_HANDOFF_SCRIPT),
            "--profile", profile,
            "--source", planner_seat,
            "--target", "memory",
            "--task-id", task_id,
            "--status", "completed",
            "--verdict", "APPROVED",
            "--title", f"Queue drained: {project}/{team}",
            "--summary", summary,
            "--user-summary", summary,  # required by lineage schema; avoids deprecation warning
            "--no-notify",              # relay writes receipt only; caller sends wake separately
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = _one_line_detail(result.stderr or result.stdout or f"exit {result.returncode}")
        raise WakeHookError(f"complete_handoff relay failed: {detail}", target="memory")


def _maybe_relay_queue_drained(
    project: str, team: str, task_id: str, actor: str
) -> None:
    """After task_done, relay to memory if queue is drained and notify_policy allows.

    Skips silently when:
    - notify_policy is not queue_drained_only (includes never_notify_memory);
    - another task in the queue is not yet terminal;
    - planner seat cannot be resolved from profile;
    - a relay receipt already exists (idempotent rerun).

    Prints RELAY_OK on success, RELAY_SKIP on skip, RELAY_FAILED on error.
    """
    policy = _team_notify_policy(project, team)
    if policy != "queue_drained_only":
        return

    queue = _queue_path(project, team)
    if not _team_queue_is_drained(queue, task_id):
        return

    try:
        planner_seat = _planner_seat_for_team(project, team)
    except WakeHookError as exc:
        print(f"RELAY_SKIP cannot resolve planner: {exc}", file=sys.stderr)
        return

    if _relay_receipt_exists(project, task_id, planner_seat):
        print(f"RELAY_SKIP receipt already exists for {task_id}")
        return

    try:
        _do_relay_complete_handoff(project, team, task_id, planner_seat)
        print(f"RELAY_OK queue-drained relay to memory complete (task_done {task_id!r})")
    except WakeHookError as exc:
        print(f"RELAY_FAILED {exc}", file=sys.stderr)


def _one_line_detail(text: str, *, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) > limit:
        return compact[: limit - 3] + "..."
    return compact or "unknown"


def _planner_seat_for_team(project: str, team: str) -> str:
    try:
        profile = load_profile_v3(_profile_path(project))
        team_name = team if profile.is_multi() else "default"
        seats = profile.seats_of(team_name)
    except ProfileV3Error as exc:
        raise WakeHookError(f"profile resolution failed: {exc}") from exc

    _PLANNER_ROLES = frozenset({"planner", "planner-dispatcher"})
    planners = [seat for seat in seats if profile.seat_roles.get(seat) in _PLANNER_ROLES]
    if not planners:
        raise WakeHookError(f"team {team!r} has no planner seat")
    if len(planners) > 1:
        raise WakeHookError(f"team {team!r} has multiple planner seats: {planners}")
    return planners[0]


def _wake_team_planner(project: str, team: str, task_id: str) -> str:
    target = _planner_seat_for_team(project, team)
    send_script = _send_script_path()
    if not send_script.exists():
        raise WakeHookError(f"send script not found: {send_script}", target=target)

    message = (
        f"[QUEUE-WAKE] {project}/{team} {task_id}; "
        "claim the brief, write/update workflow, then execute it to closeout or report blocker."
    )
    result = subprocess.run(
        ["bash", str(send_script), "--project", project, target, message],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = _one_line_detail(result.stderr or result.stdout or f"exit {result.returncode}")
        raise WakeHookError(detail, target=target)
    return target


def _list_teams(project: str) -> list[str]:
    """Return all team subdirs under tasks/<project>/ that have a queue file."""
    proj_root = _agents_root() / "tasks" / project
    if not proj_root.exists():
        return []
    teams = []
    for child in proj_root.iterdir():
        if not child.is_dir():
            continue
        if (child / "tasks.queue.jsonl").exists() or (child / "brief").exists():
            teams.append(child.name)
    return sorted(teams)


def _resolve_cross_team_upstream(
    project: str, current_team: str, task_id: str
) -> tuple[str, str] | None:
    """Post-retest #5: locate a task_id across ALL teams in the project.

    Returns (team, status) if found, None otherwise. Used to evaluate
    cross-team depends_on without forcing planner to know which team owns
    each upstream task.
    """
    teams = _list_teams(project)
    for team in teams:
        if team == current_team:
            continue
        q = _queue_path(project, team)
        if not q.exists():
            continue
        state = read_current_state(q)
        if task_id in state:
            return team, state[task_id].status
    return None


def _unmet_dependencies(
    project: str,
    team: str,
    state: dict,
    depends_on: list[str],
) -> list[str]:
    unmet: list[str] = []
    for upstream_id in depends_on:
        up = state.get(upstream_id)
        if up is not None:
            if up.status != "task_done":
                unmet.append(upstream_id)
            continue
        cross = _resolve_cross_team_upstream(project, team, upstream_id)
        if cross is None or cross[1] != "task_done":
            unmet.append(upstream_id)
    return unmet


def _brief_path(project: str, team: str, task_id: str) -> Path:
    return _project_team_root(project, team) / "brief" / f"{task_id}.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def cmd_queue(args: argparse.Namespace) -> int:
    """Append task_created event + write brief markdown skeleton.

    Post-review fixes:
    - #4 input validation (project/team/task_id pattern + path-traversal block)
    - #5 atomic non-destructive write (temp file → rename only after append)
    - #7 schema-valid skeleton (seats_required + mechanical have ≥1 item)
    """
    try:
        _validate_cli_inputs(args.project, args.team, args.task_id)
    except InputValidationError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2

    project = args.project
    team = args.team
    task_id = args.task_id

    queue = _queue_path(project, team)
    try:
        current_state = read_current_state(queue) if queue.exists() else {}
    except Exception as exc:  # noqa: BLE001
        print(f"queue state read failed: {exc}", file=sys.stderr)
        return 1
    open_blockers = _blocking_open_tasks(current_state, args.depends_on or [], task_id)
    if open_blockers and not getattr(args, "allow_open", False):
        blocker = max(open_blockers, key=lambda ts: ts.last_seq)
        print(
            "queue has open task; refusing parallel team dispatch "
            f"(task_id={blocker.task_id}, status={blocker.status}). "
            "Finish/drain it first or pass --allow-open for an explicit exception.",
            file=sys.stderr,
        )
        return 4

    latest = _latest_task_in_queue(current_state)
    if (
        latest
        and latest.status == "task_done"
        and latest.verdict == "PASS"
        and latest.task_id not in (args.depends_on or [])
        and not getattr(args, "allow_unintegrated", False)
    ):
        integration = _latest_delivery_integration(project, latest.task_id)
        if integration.get("status") == "needs_integration":
            print(
                "latest PASS delivery still needs review/latest integration; "
                f"refusing new dispatch (latest_task_id={latest.task_id}, "
                f"reason={integration.get('reason') or 'unknown'}). "
                "Integrate it first, add an explicit --depends-on edge, or pass "
                "--allow-unintegrated for an intentional exception.",
                file=sys.stderr,
            )
            return 4

    brief = _brief_path(project, team, task_id)
    brief.parent.mkdir(parents=True, exist_ok=True)

    # Pre-check overwrite policy BEFORE writing anything (Fix #5)
    # CF007: --brief-content-file implicitly allows overwrite (user is
    # explicitly providing the content, so skeleton-overwrite protection
    # does not apply). --force alone still generates a skeleton.
    brief_pre_exists = brief.exists()
    if brief_pre_exists and not args.force and not args.brief_content_file:
        print(f"refusing to overwrite existing brief: {brief}", file=sys.stderr)
        return 2

    if args.brief_content_file:
        brief_text = Path(args.brief_content_file).read_text(encoding="utf-8")
        # Post-retest #2: when caller supplies content, still validate it
        # both against brief schema (minItems etc.) AND against CLI args
        # (no mismatched task_id/project/team).
        try:
            _validate_external_brief_content(
                brief_text, args.brief_content_file, project, team, task_id
            )
        except InputValidationError as exc:
            print(f"brief content validation failed: {exc}", file=sys.stderr)
            return 2
    else:
        # Fix #B (post-review retest): build dict + yaml.safe_dump to handle
        # quotes / special chars / non-string scalars correctly. Also forces
        # created to be a string (default yaml.safe_dump would emit ISO without
        # quotes which PyYAML on load parses as datetime).
        frontmatter = {
            "task_id": str(task_id),
            "project": str(project),
            "team": str(team),
            "created": _utc_now(),
            "created_by": "memory",
            "objective": str(args.objective),
            "depends_on": list(args.depends_on or []),
            "acceptance_criteria": {
                "mechanical": [
                    "TODO: replace with a real mechanical command before dispatch"
                ],
                "reviewer": [],
                "operator": [],
            },
            "seats_required": list(args.seats_required or ["builder"]),
            "fuzz_required": False,
            "priority": "P2",
            "notify_on_completion": ["memory"],
        }
        # default_style='"' would force JSON-quoting every string. We instead
        # use default mode but specify default_flow_style=False + allow_unicode
        # so strings with quotes/colons get properly escaped, and `created`
        # stays a quoted string (because we passed it as a Python str).
        # Explicit-quoting `created` removes ambiguity on round-trip parse.
        yaml_body = yaml.dump(
            frontmatter,
            Dumper=_QuotedStrDumper,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=4096,
        )
        brief_text = (
            "---\n"
            + yaml_body
            + "---\n\n"
            "# Brief 正文\n\n"
            "## 目标\n\n"
            + str(args.objective).strip()
            + "\n\n"
            "## 验收说明\n\n"
            "<待 memory 补全 mechanical/reviewer/operator 路由项>\n\n"
            "<!-- Acceptance authoring rules (CF043/CF044):\n"
            "  - mechanical: must be machine-deterministic shell commands (binary exit code)\n"
            "    - GOOD: 'bash -lc \"cd /path && python3 -m pytest tests -q -k filter\"'\n"
            "    - AVOID: human-language qualifiers inside mechanical descriptions\n"
            "      (executor cannot honor natural-language conditions, only exit codes)\n"
            "    - For non-blocking baseline (full-suite probe): add diagnostic:true to the\n"
            "      criterion dict, or pass --baseline-criteria N to acceptance run\n"
            "  - reviewer: planner self-review or dedicated reviewer criteria\n"
            "  - operator: questions answered manually by the human operator\n"
            "  See docs/guides/acceptance-criteria.md for details.\n"
            "-->\n"
        )

    acceptance_block = _brief_acceptance_block_reason(brief_text, str(brief))

    # Fix #5: write to temp file, atomic rename ONLY after append succeeds.
    # If append fails, we unlink the temp file and leave any pre-existing brief alone.
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{task_id}.", suffix=".tmp", dir=str(brief.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(brief_text)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    event = {
        "event_type": "task_created",
        "actor": "memory",
        "task_id": task_id,
        "brief_path": str(brief.relative_to(_agents_root())),
        "parent_task_id": args.parent_task_id,
        "depends_on": args.depends_on or [],
    }
    try:
        result = append_event(queue, event)
    except QueueError as exc:
        # Fix #5: never unlink pre-existing brief on append failure.
        tmp_path.unlink(missing_ok=True)
        print(f"queue append failed: {exc}", file=sys.stderr)
        return 1

    # Append succeeded; atomic rename temp → final.
    os.replace(tmp_path, brief)

    print(f"queued task {task_id}")
    print(f"  brief: {brief}")
    print(f"  queue: {queue}")
    print(f"  seq:   {result['seq']}")
    if acceptance_block:
        wait_event = {
            "event_type": "task_waiting_for",
            "actor": "memory",
            "task_id": task_id,
            "waiting_for": "acceptance_criteria",
        }
        try:
            append_event(queue, wait_event)
        except QueueError as exc:
            print(f"waiting_for append failed: {exc}", file=sys.stderr)
            return 1
        if args.no_wake:
            print("WAKE_SKIPPED (--no-wake)")
            return 0
        print(f"WAKE_DEFERRED reason=acceptance_criteria detail={acceptance_block}")
        return 0
    if args.no_wake:
        print("WAKE_SKIPPED (--no-wake)")
        return 0
    try:
        target = _wake_team_planner(project, team, task_id)
    except WakeHookError as exc:
        target = exc.target or "<unresolved>"
        print(f"HOOK_WAKE_FAILED target={target} reason={exc}", file=sys.stderr)
        return 3
    print(f"WAKE_OK target={target}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Show pending tasks for a team. Default: pending only; --all shows all."""
    try:
        _validate_cli_inputs(args.project, args.team)
    except InputValidationError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2
    queue = _queue_path(args.project, args.team)
    if not queue.exists():
        print(f"queue not initialized: {queue}", file=sys.stderr)
        return 0

    state = read_current_state(queue)
    if args.all:
        rows = list(state.values())
    else:
        rows = [ts for ts in state.values() if ts.status == "task_created"]
    rows.sort(key=lambda ts: ts.last_seq)

    if not rows:
        print(f"no {'tasks' if args.all else 'pending tasks'} in {args.project}/{args.team}")
        return 0

    for ts in rows:
        depends = ",".join(ts.depends_on) if ts.depends_on else "-"
        print(f"{ts.task_id}\t{ts.status}\t{ts.actor}\t{ts.last_event_ts}\tdepends_on={depends}")
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    """Planner claims a pending task. Appends task_claimed event.

    Checks depends_on first; if unmet, writes task_waiting_for instead.
    (Fix #6: task_created -> task_waiting_for transition allowed by queue_io
    VALID_TRANSITIONS as of v3 Phase 1 post-review.)
    """
    try:
        _validate_cli_inputs(args.project, args.team, args.task_id)
    except InputValidationError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2
    queue = _queue_path(args.project, args.team)
    state = read_current_state(queue)
    ts = state.get(args.task_id)
    if ts is None:
        print(f"task_id {args.task_id!r} not in queue", file=sys.stderr)
        return 2
    # Fix #A (post-review retest): allow retry from task_waiting_for state.
    # State machine permits task_waiting_for → task_claimed when deps now met,
    # or task_waiting_for → task_waiting_for if still blocked.
    if ts.status not in ("task_created", "task_waiting_for", "task_reset"):
        print(
            f"task_id {args.task_id!r} is in state {ts.status!r}, not claimable",
            file=sys.stderr,
        )
        return 2

    brief_path = Path(ts.brief_path)
    if not brief_path.is_absolute():
        brief_path = _agents_root() / brief_path
    if brief_path.exists():
        acceptance_block = _brief_acceptance_block_reason(
            brief_path.read_text(encoding="utf-8"),
            str(brief_path),
        )
    else:
        acceptance_block = f"brief missing: {brief_path}"
    if acceptance_block:
        event = {
            "event_type": "task_waiting_for",
            "actor": args.actor,
            "task_id": args.task_id,
            "waiting_for": "acceptance_criteria",
        }
        try:
            append_event(queue, event)
        except QueueError as exc:
            print(f"waiting_for append failed: {exc}", file=sys.stderr)
            return 1
        print(f"task {args.task_id} blocked on acceptance_criteria: {acceptance_block}")
        return 3

    # Post-retest #5: check depends_on across ALL teams in the project.
    # Local queue checked first; if absent, fall through to cross-team scan.
    unmet = _unmet_dependencies(args.project, args.team, state, ts.depends_on)

    if unmet:
        for up_id in unmet:
            event = {
                "event_type": "task_waiting_for",
                "actor": args.actor,
                "task_id": args.task_id,
                "waiting_for": up_id,
            }
            try:
                append_event(queue, event)
            except QueueError as exc:
                print(f"waiting_for append failed: {exc}", file=sys.stderr)
                return 1
        print(f"task {args.task_id} blocked on upstream: {unmet}")
        return 3

    event = {
        "event_type": "task_claimed",
        "actor": args.actor,
        "task_id": args.task_id,
    }
    try:
        result = append_event(queue, event)
    except QueueError as exc:
        print(f"claim append failed: {exc}", file=sys.stderr)
        return 1
    print(f"claimed {args.task_id} (seq {result['seq']})")
    print(f"  brief: {ts.brief_path}")
    return 0


def _task_brief_path(ts) -> Path | None:
    if not ts.brief_path:
        return None
    brief_path = Path(ts.brief_path)
    if not brief_path.is_absolute():
        brief_path = _agents_root() / brief_path
    return brief_path


def _task_acceptance_block_reason(ts) -> str | None:
    brief_path = _task_brief_path(ts)
    if brief_path is None:
        return "task has no brief_path"
    if not brief_path.exists():
        return f"brief missing: {brief_path}"
    return _brief_acceptance_block_reason(
        brief_path.read_text(encoding="utf-8"),
        str(brief_path),
    )


def cmd_reset(args: argparse.Namespace) -> int:
    """Append task_reset for a recoverable brief task."""
    try:
        _validate_cli_inputs(args.project, args.team, args.task_id)
    except InputValidationError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2

    queue = _queue_path(args.project, args.team)
    state = read_current_state(queue)
    ts = state.get(args.task_id)
    if ts is None:
        print(f"task_id {args.task_id!r} not in queue", file=sys.stderr)
        return 2
    if ts.status == "task_reset":
        print(f"task {args.task_id} already reset")
        return 0

    event = {
        "event_type": "task_reset",
        "actor": args.actor,
        "task_id": args.task_id,
        "reset_reason": args.reason,
    }
    try:
        result = append_event(queue, event)
    except QueueError as exc:
        print(
            f"reset append failed: {exc}; "
            "only created/claimed/in_progress/waiting tasks can be reset",
            file=sys.stderr,
        )
        return 1
    print(f"reset {args.task_id} (seq {result['seq']})")
    print(
        "next: agent_admin.py brief requeue "
        f"--project {args.project} --team {args.team} --task-id {args.task_id}"
    )
    return 0


def cmd_requeue(args: argparse.Namespace) -> int:
    """Recover a blocked/reset task by appending a fresh task_created event.

    The command does not rewrite the brief. It validates that the existing
    brief is claimable, then uses the queue state machine to recover the same
    task id from task_waiting_for/task_reset/task_failed/task_bounced.
    """
    try:
        _validate_cli_inputs(args.project, args.team, args.task_id)
    except InputValidationError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2

    queue = _queue_path(args.project, args.team)
    state = read_current_state(queue)
    ts = state.get(args.task_id)
    if ts is None:
        print(f"task_id {args.task_id!r} not in queue", file=sys.stderr)
        return 2

    if ts.status == "task_created":
        print(f"task {args.task_id} already claimable")
        if args.no_wake:
            print("WAKE_SKIPPED (--no-wake)")
            return 0
        try:
            target = _wake_team_planner(args.project, args.team, args.task_id)
        except WakeHookError as exc:
            target = exc.target or "<unresolved>"
            print(f"HOOK_WAKE_FAILED target={target} reason={exc}", file=sys.stderr)
            return 3
        print(f"WAKE_OK target={target}")
        return 0

    recoverable = {"task_waiting_for", "task_reset", "task_failed", "task_bounced"}
    if ts.status not in recoverable:
        print(
            f"task_id {args.task_id!r} is in state {ts.status!r}, not requeueable",
            file=sys.stderr,
        )
        return 2

    acceptance_block = _task_acceptance_block_reason(ts)
    if acceptance_block:
        print(f"REQUEUE_BLOCKED reason=acceptance_criteria detail={acceptance_block}")
        return 3

    if ts.status == "task_waiting_for":
        reset_event = {
            "event_type": "task_reset",
            "actor": args.actor,
            "task_id": args.task_id,
            "reset_reason": args.reason,
        }
        try:
            append_event(queue, reset_event)
        except QueueError as exc:
            print(f"reset append failed: {exc}", file=sys.stderr)
            return 1
        state = read_current_state(queue)
        ts = state[args.task_id]

    create_event = {
        "event_type": "task_created",
        "actor": args.actor,
        "task_id": args.task_id,
        "brief_path": ts.brief_path,
        "parent_task_id": ts.parent_task_id,
        "depends_on": ts.depends_on,
    }
    try:
        result = append_event(queue, create_event)
    except QueueError as exc:
        print(f"requeue append failed: {exc}", file=sys.stderr)
        return 1

    print(f"requeued {args.task_id} (seq {result['seq']})")
    if args.no_wake:
        print("WAKE_SKIPPED (--no-wake)")
        return 0
    try:
        target = _wake_team_planner(args.project, args.team, args.task_id)
    except WakeHookError as exc:
        target = exc.target or "<unresolved>"
        print(f"HOOK_WAKE_FAILED target={target} reason={exc}", file=sys.stderr)
        return 3
    print(f"WAKE_OK target={target}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    """Mark a claimed brief task as in progress."""
    try:
        _validate_cli_inputs(args.project, args.team, args.task_id)
    except InputValidationError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2

    queue = _queue_path(args.project, args.team)
    state = read_current_state(queue)
    ts = state.get(args.task_id)
    if ts is None:
        print(f"task_id {args.task_id!r} not in queue", file=sys.stderr)
        return 2

    if ts.status == "task_in_progress":
        print(f"task {args.task_id} already in_progress")
        return 0
    if ts.status == "task_done":
        print(f"task {args.task_id} already done")
        return 0
    if ts.status != "task_claimed":
        print(
            f"task_id {args.task_id!r} is in state {ts.status!r}, not startable",
            file=sys.stderr,
        )
        return 2

    event = {
        "event_type": "task_in_progress",
        "actor": args.actor,
        "task_id": args.task_id,
    }
    try:
        result = append_event(queue, event)
    except QueueError as exc:
        print(f"start append failed: {exc}", file=sys.stderr)
        return 1
    print(f"started {args.task_id} (seq {result['seq']})")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        _validate_cli_inputs(args.project, args.team, args.task_id)
    except InputValidationError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2
    queue = _queue_path(args.project, args.team)
    state = read_current_state(queue)
    ts = state.get(args.task_id)
    if ts is None:
        print(f"task_id {args.task_id!r} not in queue", file=sys.stderr)
        return 2
    print(json.dumps(
        {
            "task_id": ts.task_id,
            "status": ts.status,
            "last_seq": ts.last_seq,
            "last_event_ts": ts.last_event_ts,
            "actor": ts.actor,
            "brief_path": ts.brief_path,
            "depends_on": ts.depends_on,
            "waiting_for": ts.waiting_for,
            "verdict": ts.verdict,
            "fail_reason": ts.fail_reason,
            "bounce_reason": ts.bounce_reason,
            "reset_count": ts.reset_count,
        },
        indent=2,
    ))
    return 0


# ---------------------------------------------------------------------------
# Planner status snapshot helpers
# ---------------------------------------------------------------------------

def _queue_state_label(tasks: dict) -> str:
    """Derive a single human-readable queue state label from all tasks."""
    return queue_state_label(tasks)


_QUEUE_OPEN_STATUSES = frozenset(
    {"task_created", "task_waiting_for", "task_claimed", "task_in_progress"}
)
_ATTENTION_STATUS_ORDER = {
    "task_in_progress": 0,
    "task_claimed": 1,
    "task_created": 2,
    "task_waiting_for": 3,
    "task_failed": 4,
    "task_bounced": 5,
    "task_reset": 6,
}


def _blocking_open_tasks(
    tasks: dict,
    depends_on: list[str],
    current_task_id: str = "",
) -> list:
    """Open same-team tasks that would create parallel planner pressure."""
    deps = set(depends_on or [])
    return [
        ts
        for ts in tasks.values()
        if (
            ts.status in _QUEUE_OPEN_STATUSES
            and ts.task_id not in deps
            and ts.task_id != current_task_id
        )
    ]


def _task_attention_reason(task) -> str:
    if task.status == "task_waiting_for":
        return f"waiting_for={task.waiting_for or 'unknown'}"
    if task.status == "task_failed":
        return task.fail_reason or task.verdict or "failed"
    if task.status == "task_bounced":
        return task.bounce_reason or "bounced"
    if task.status == "task_reset":
        return task.reset_reason or f"reset_count={task.reset_count}"
    if task.status == "task_created":
        return "unclaimed"
    if task.status == "task_claimed":
        return f"claimed_by={task.actor}"
    if task.status == "task_in_progress":
        return f"in_progress_by={task.actor}"
    return ""


def _task_next_step(project: str, team: str, task) -> str | None:
    if task is None:
        return None
    requeue = (
        "agent_admin.py brief requeue "
        f"--project {project} --team {team} --task-id {task.task_id}"
    )
    if task.status == "task_reset":
        return requeue
    if task.status == "task_waiting_for":
        if task.waiting_for == "acceptance_criteria":
            return f"fix brief acceptance_criteria, then {requeue}"
        return f"wait for {task.waiting_for or 'upstream'}, then {requeue}"
    return None


def _attention_task(tasks: dict):
    """Return the most useful non-done task for planner-status output."""
    candidates = [ts for ts in tasks.values() if ts.status != "task_done"]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda ts: (
            _ATTENTION_STATUS_ORDER.get(ts.status, 99),
            -ts.last_seq,
        ),
    )


def _latest_task_in_queue(tasks: dict):
    """Return the TaskState with the highest last_seq, or None."""
    if not tasks:
        return None
    return max(tasks.values(), key=lambda ts: ts.last_seq)


def _project_local_toml(project: str) -> dict:
    """Read project-local.toml for a project; returns {} on failure."""
    path = real_user_home() / ".agents" / "tasks" / project / "project-local.toml"
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return _toml_load_safe(fh)
    except Exception:  # noqa: BLE001
        return {}


def review_latest_worktree_path(project: str, override: str = "") -> Path:
    """Return the canonical per-project review/latest worktree path.

    SSOT order:
    1. Explicit override (project-local.toml key review_latest_worktree)
    2. Computed: ~/.agents/worktrees/<project>/review-latest

    Each project gets its own scoped path; never a shared global path.
    """
    if override:
        return Path(override).expanduser()
    return real_user_home() / ".agents" / "worktrees" / project / "review-latest"


def launcher_review_worktree_path(project: str, override: str = "") -> Path:
    """Return the per-project desktop-launcher worktree path (memory-owned, detached).

    SSOT order:
    1. Explicit override (project-local.toml key launcher_review_worktree)
    2. Computed: ~/.<project>-launcher-review  (mirrors existing naming convention)

    Memory maintains this worktree; it is kept detached so it does not consume the
    review/latest branch ref and does not conflict with the planner-owned worktree.
    """
    if override:
        return Path(override).expanduser()
    return real_user_home() / f".{project}-launcher-review"


def _git_worktree_status(wt_path: Path) -> dict:
    """Return a status dict for a git worktree path without mutating anything."""
    result: dict = {
        "path": str(wt_path),
        "exists": wt_path.exists(),
        "status": "missing",
        "branch": None,
        "commit": None,
        "dirty": False,
        "dirty_count": 0,
    }
    if not wt_path.exists():
        return result
    try:
        r_branch = subprocess.run(
            ["git", "-C", str(wt_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        r_hash = subprocess.run(
            ["git", "-C", str(wt_path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if r_branch.returncode != 0:
            result["status"] = "not-a-git-repo"
            return result
        branch = r_branch.stdout.strip()
        commit = r_hash.stdout.strip() if r_hash.returncode == 0 else None
        result["branch"] = branch
        result["commit"] = commit
        r_dirty = subprocess.run(
            ["git", "-C", str(wt_path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        if r_dirty.returncode == 0:
            dirty_lines = [line for line in r_dirty.stdout.splitlines() if line.strip()]
            result["dirty"] = bool(dirty_lines)
            result["dirty_count"] = len(dirty_lines)
        if branch == "review/latest":
            result["status"] = "ok"
        elif branch == "HEAD":  # detached worktree
            r_ref = subprocess.run(
                ["git", "-C", str(wt_path), "rev-parse", "--short", "review/latest"],
                capture_output=True, text=True, timeout=5,
            )
            if r_ref.returncode == 0 and r_ref.stdout.strip() == commit:
                result["status"] = "ok-detached"
            else:
                result["status"] = "stale-detached"
        else:
            result["status"] = "wrong-branch"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        result["status"] = "error"
    return result


def _check_workspace_dirty(project: str) -> bool:
    """Return True if the review/latest worktree has uncommitted changes."""
    try:
        local = _project_local_toml(project)
        wt_override = str(local.get("review_latest_worktree") or "")
        wt_path = review_latest_worktree_path(project, wt_override)
        if not wt_path.exists():
            return False
        r = subprocess.run(
            ["git", "-C", str(wt_path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(r.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _check_review_latest_clean(project: str) -> bool:
    """Return True if HEAD is an ancestor of review/latest (planner work merged)."""
    try:
        local = _project_local_toml(project)
        wt_override = str(local.get("review_latest_worktree") or "")
        wt_path = review_latest_worktree_path(project, wt_override)
        if not wt_path.exists():
            return False
        r = subprocess.run(
            ["git", "-C", str(wt_path), "merge-base", "--is-ancestor", "HEAD", "review/latest"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _check_review_latest_commit(project: str) -> str | None:
    """Return the short commit hash of review/latest, or None."""
    try:
        local = _project_local_toml(project)
        wt_override = str(local.get("review_latest_worktree") or "")
        wt_path = review_latest_worktree_path(project, wt_override)
        if not wt_path.exists():
            return None
        r = subprocess.run(
            ["git", "-C", str(wt_path), "rev-parse", "--short", "review/latest"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _commit_in_review_latest(project: str, commit: str) -> bool | None:
    """Return whether commit is reachable from review/latest, or None if unknown."""
    commit = (commit or "").strip()
    if not commit:
        return None
    try:
        local = _project_local_toml(project)
        wt_override = str(local.get("review_latest_worktree") or "")
        wt_path = review_latest_worktree_path(project, wt_override)
        if not wt_path.exists():
            return None
        r = subprocess.run(
            ["git", "-C", str(wt_path), "merge-base", "--is-ancestor", commit, "review/latest"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return True
        if r.returncode == 1:
            return False
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _reported_commit_from_receipt(receipt: dict) -> str | None:
    for key in ("builder_commit", "commit", "branch_tip", "reported_commit"):
        value = receipt.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _latest_delivery_integration(project: str, task_id: str | None) -> dict:
    """Inspect durable handoff receipts for the latest PASS task.

    review/latest worktree cleanliness alone only says the validation branch is
    tidy; it does not prove the latest planner delivery was integrated. Receipts
    are the durable source for PASS_NEEDS_INTEGRATION and lineage status.
    """
    result = {
        "status": "clean",
        "reason": "",
        "receipt_path": None,
        "reported_commit": None,
    }
    if not task_id:
        return result

    handoffs = real_user_home() / ".agents" / "tasks" / project / "patrol" / "handoffs"
    if not handoffs.exists():
        return result

    candidates = sorted(
        handoffs.glob(f"{task_id}__*.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    needs_candidate: dict | None = None
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        reported_commit = _reported_commit_from_receipt(data)
        integration = str(data.get("integration") or "").strip()
        lineage_status = str(data.get("lineage_status") or "").strip()
        head_contains = data.get("head_contains_commit")
        receipt_path = str(path)

        if integration == "PASS_NEEDS_INTEGRATION":
            commit_check = _commit_in_review_latest(project, reported_commit or "")
            if reported_commit and commit_check is True:
                return {
                    "status": "clean",
                    "reason": f"receipt reported commit {reported_commit} now reaches review/latest",
                    "receipt_path": receipt_path,
                    "reported_commit": reported_commit,
                }
            if needs_candidate is None:
                needs_candidate = {
                    "status": "needs_integration",
                    "reason": "PASS_NEEDS_INTEGRATION receipt",
                    "receipt_path": receipt_path,
                    "reported_commit": reported_commit,
                }
            continue

        if lineage_status == "divergent" or head_contains is False:
            commit_check = _commit_in_review_latest(project, reported_commit or "")
            if reported_commit and commit_check is True:
                return {
                    "status": "clean",
                    "reason": f"receipt reported commit {reported_commit} now reaches review/latest",
                    "receipt_path": receipt_path,
                    "reported_commit": reported_commit,
                }
            if needs_candidate is None:
                needs_candidate = {
                    "status": "needs_integration",
                    "reason": f"lineage_status={lineage_status or 'unknown'}",
                    "receipt_path": receipt_path,
                    "reported_commit": reported_commit,
                }
            continue

        if reported_commit:
            commit_check = _commit_in_review_latest(project, reported_commit)
            if commit_check is False:
                return {
                    "status": "needs_integration",
                    "reason": f"reported commit {reported_commit} not on review/latest",
                    "receipt_path": receipt_path,
                    "reported_commit": reported_commit,
                }
            if commit_check is True:
                return {
                    "status": "clean",
                    "reason": f"reported commit {reported_commit} reaches review/latest",
                    "receipt_path": receipt_path,
                    "reported_commit": reported_commit,
                }
    if needs_candidate is not None:
        return needs_candidate
    return result


def _compute_dispatch_readiness(
    queue_depth: int,
    latest_status: str | None,
    latest_verdict: str | None,
    review_latest_clean: bool,
    workspace_dirty: bool,
    session_status: str,
    profile_drift: bool,
    integration_status: str = "clean",
    integration_reason: str = "",
) -> tuple[str, str]:
    """Compute dispatch_readiness and reason string per CF006 spec."""
    if session_status != "alive" or profile_drift:
        return "unknown", f"session={session_status}, profile_drift={profile_drift}"

    busy = queue_depth > 0 and latest_status not in ("task_done", "task_failed", None)
    if busy:
        return "busy", f"queue_depth={queue_depth}, latest_status={latest_status}"

    hot = queue_depth > 0 or latest_status not in ("task_done", "task_failed", None)
    if hot:
        return "hot", f"queue_depth={queue_depth}, latest_status={latest_status}"

    # queue is drained from here (queue_depth == 0 and latest done/failed)
    clean = (
        latest_verdict == "PASS"
        and review_latest_clean
        and not workspace_dirty
        and integration_status != "needs_integration"
    )
    if clean:
        parts = ["drained", "latest task PASS"]
        if review_latest_clean:
            parts.append("commit on review/latest")
        if not workspace_dirty:
            parts.append("workspace clean")
        return "clean", ", ".join(parts)

    if latest_status == "task_done" and integration_status == "needs_integration":
        suffix = f": {integration_reason}" if integration_reason else ""
        return "idle_unmerged", f"drained, latest task PASS, but delivery needs review/latest integration{suffix}"

    if latest_status == "task_done" and not review_latest_clean:
        return "idle_unmerged", "drained, latest task PASS, but not merged to review/latest"

    # fallback: drained but not clean (dirty workspace, verdict not PASS, etc.)
    parts = ["drained"]
    if latest_verdict:
        parts.append(f"verdict={latest_verdict}")
    if not review_latest_clean:
        parts.append("not on review/latest")
    if workspace_dirty:
        parts.append("workspace dirty")
    return "idle_unmerged", ", ".join(parts)


def check_review_latest_worktree(project: str) -> dict:
    """Return diagnostic snapshot of a project's review/latest worktree state.

    Checks:
    - review_worktree: memory owns; holds review/latest branch ref
    - launcher_worktree: memory owns; detached HEAD for desktop launcher

    Role contract (enforced here as labels, not code gates):
    - Planner: delivers branch/commit/test evidence; does not merge review/latest
    - Memory: integrates accepted deliveries into review_worktree;
              merges review/latest → main after operator confirmation;
              keeps launcher_worktree synced to review/latest
    - Builder: never merges review/latest or main
    """
    local = _project_local_toml(project)
    wt_override = str(local.get("review_latest_worktree") or "")
    launcher_override = str(local.get("launcher_review_worktree") or "")
    wt_path = review_latest_worktree_path(project, wt_override)
    launcher_path = launcher_review_worktree_path(project, launcher_override)
    return {
        "project": project,
        "review_worktree": _git_worktree_status(wt_path),
        "launcher_worktree": _git_worktree_status(launcher_path),
        "review_worktree_configured": bool(wt_override),
        "launcher_worktree_configured": bool(launcher_override),
    }


def _tmux_session_status(session_name: str) -> str:
    """Return alive/dead/unknown for a tmux session name."""
    if not session_name:
        return "unknown"
    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", f"={session_name}"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"
    return "alive" if result.returncode == 0 else "dead"


def _read_session_runtime(project: str, planner_seat: str) -> dict | None:
    """Read runtime metadata and tmux liveness from session.toml, or None."""
    session_file = (
        real_user_home() / ".agents" / "sessions" / project / planner_seat / "session.toml"
    )
    if not session_file.exists():
        return None
    try:
        with session_file.open("rb") as fh:
            data = _toml_load_safe(fh)
        if not isinstance(data, dict) or not data.get("tool"):
            return None
        session_name = str(data.get("session") or "")
        return {
            "tool": str(data.get("tool") or ""),
            "auth_mode": str(data.get("auth_mode") or ""),
            "provider": str(data.get("provider") or ""),
            "session_name": session_name,
            "session_status": _tmux_session_status(session_name),
        }
    except Exception:  # noqa: BLE001
        return None


def _project_toml_path(project: str) -> Path:
    return real_user_home() / ".agents" / "projects" / project / "project.toml"


def _load_project_toml(project: str) -> dict:
    path = _project_toml_path(project)
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            data = _toml_load_safe(fh)
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _status_profile_data(project: str) -> dict:
    """Return planner-status data with project.toml roster/overrides as SSOT."""
    profile_data: dict = {}
    profile_file = _profile_path(project)
    if profile_file.exists():
        try:
            with profile_file.open("rb") as fh:
                loaded = _toml_load_safe(fh)
            if isinstance(loaded, dict):
                profile_data = loaded
        except Exception:  # noqa: BLE001
            profile_data = {}

    project_data = _load_project_toml(project)
    active_seats = [
        str(item).strip()
        for item in (project_data.get("engineers") or [])
        if str(item).strip()
    ]
    if not active_seats:
        return profile_data
    active = set(active_seats)

    merged = dict(profile_data)
    merged["seats"] = active_seats

    profile_overrides = profile_data.get("seat_overrides") or {}
    if not isinstance(profile_overrides, dict):
        profile_overrides = {}
    project_overrides = project_data.get("seat_overrides") or {}
    if not isinstance(project_overrides, dict):
        project_overrides = {}
    overrides: dict[str, dict] = {}
    for seat in active_seats:
        base = profile_overrides.get(seat) if isinstance(profile_overrides.get(seat), dict) else {}
        project_override = (
            project_overrides.get(seat) if isinstance(project_overrides.get(seat), dict) else {}
        )
        overrides[seat] = {**base, **project_override}
    merged["seat_overrides"] = overrides

    profile_roles = profile_data.get("seat_roles") or {}
    if not isinstance(profile_roles, dict):
        profile_roles = {}
    roles: dict[str, str] = {}
    for seat in active_seats:
        override = overrides.get(seat) or {}
        role = str(override.get("role") or profile_roles.get(seat) or "").strip()
        if seat == "memory":
            role = "project-memory"
        elif not role and str(override.get("team") or "").strip():
            role = "planner"
        roles[seat] = role
    merged["seat_roles"] = roles

    profile_teams = profile_data.get("teams") or {}
    if not isinstance(profile_teams, dict):
        profile_teams = {}
    teams: dict[str, dict] = {}
    for team_name, team_cfg in profile_teams.items():
        if not isinstance(team_cfg, dict):
            continue
        seats = [str(item) for item in (team_cfg.get("seats") or []) if str(item) in active]
        if seats:
            teams[str(team_name)] = {**team_cfg, "seats": seats}

    for seat in active_seats:
        override = overrides.get(seat) or {}
        team = str(override.get("team") or "").strip()
        if not team:
            continue
        cfg = dict(teams.get(team) or {})
        seats = [str(item) for item in (cfg.get("seats") or [])]
        if seat not in seats:
            seats.append(seat)
        cfg.setdefault("team_type", "subteam")
        cfg.setdefault("notify_policy", "queue_drained_only")
        cfg.setdefault("planner_self_contained", bool(override.get("planner_self_contained")))
        cfg["seats"] = seats
        teams[team] = cfg
    merged["teams"] = teams
    return merged


def _team_planner_meta(profile_data: dict, team_name: str) -> dict | None:
    """Return planner seat + runtime metadata for a team, or None if no planner."""
    teams = profile_data.get("teams") or {}
    team_cfg = teams.get(team_name) if isinstance(teams, dict) else None
    if not isinstance(team_cfg, dict):
        return None
    team_seats = [str(s) for s in (team_cfg.get("seats") or [])]
    seat_roles = profile_data.get("seat_roles") or {}
    overrides = profile_data.get("seat_overrides") or {}
    planner_roles = {"planner", "planner-dispatcher"}
    planners = [s for s in team_seats if str((seat_roles or {}).get(s, "")) in planner_roles]
    if not planners:
        return None
    planner = planners[0]
    override = overrides.get(planner) or {} if isinstance(overrides, dict) else {}
    return {
        "planner_seat": planner,
        "display_name": str(override.get("display_name") or ""),
        "display_runtime": bool(override.get("display_runtime", False)),
        "tool": str(override.get("tool") or ""),
        "model": str(override.get("model") or "unknown"),
        "provider": str(override.get("provider") or ""),
        "auth_mode": str(override.get("auth_mode") or ""),
        "notify_policy": str(team_cfg.get("notify_policy") or ""),
        "team_type": str(team_cfg.get("team_type") or "subteam"),
    }


def planner_status_snapshot(project: str) -> list[dict]:
    """Return a status snapshot for every planner team in the project.

    Reads profile + queue state only; no tmux or session inspection.
    Returns a list of dicts, one per team that has a planner seat.
    """
    profile_data = _status_profile_data(project)
    if not profile_data:
        return []
    teams = profile_data.get("teams") or {}
    if not isinstance(teams, dict):
        return []
    rows: list[dict] = []
    for team_name, team_cfg in teams.items():
        if not isinstance(team_cfg, dict):
            continue
        meta = _team_planner_meta(profile_data, str(team_name))
        if meta is None:
            continue
        queue = _queue_path(project, str(team_name))
        try:
            tasks = read_current_state(queue) if queue.exists() else {}
        except Exception:  # noqa: BLE001
            tasks = {}
        latest = _latest_task_in_queue(tasks)
        attention = _attention_task(tasks)
        session_rt = _read_session_runtime(project, meta["planner_seat"])
        profile_drift = False
        if session_rt and meta["tool"]:
            profile_drift = (
                (session_rt["tool"] and session_rt["tool"] != meta["tool"])
                or (session_rt["auth_mode"] and session_rt["auth_mode"] != meta["auth_mode"])
                or (session_rt["provider"] and session_rt["provider"] != meta["provider"])
            )
        # Compute open task count for queue_depth
        open_statuses = {"task_created", "task_waiting_for", "task_claimed", "task_in_progress"}
        queue_depth = sum(1 for ts in tasks.values() if ts.status in open_statuses)
        session_status = session_rt["session_status"] if session_rt else "unknown"
        workspace_dirty = _check_workspace_dirty(project)
        review_latest_clean = _check_review_latest_clean(project)
        review_latest_commit = _check_review_latest_commit(project)
        integration = _latest_delivery_integration(
            project,
            latest.task_id if latest and latest.status == "task_done" and latest.verdict == "PASS" else None,
        )
        readiness, readiness_reason = _compute_dispatch_readiness(
            queue_depth=queue_depth,
            latest_status=latest.status if latest else None,
            latest_verdict=latest.verdict if latest else None,
            review_latest_clean=review_latest_clean,
            workspace_dirty=workspace_dirty,
            session_status=session_status,
            profile_drift=profile_drift,
            integration_status=str(integration.get("status") or "clean"),
            integration_reason=str(integration.get("reason") or ""),
        )
        rows.append({
            "team": str(team_name),
            "planner_seat": meta["planner_seat"],
            "display_name": meta["display_name"],
            "display_runtime": meta["display_runtime"],
            "tool": meta["tool"],
            "model": meta["model"] or "unknown",
            "provider": meta["provider"],
            "auth_mode": meta["auth_mode"],
            "notify_policy": meta["notify_policy"],
            "team_type": meta["team_type"],
            "queue_state": _queue_state_label(tasks),
            "task_count": len(tasks),
            "queue_depth": queue_depth,
            "latest_task_id": latest.task_id if latest else None,
            "latest_task_status": latest.status if latest else None,
            "latest_task_verdict": latest.verdict if latest else None,
            "latest_task_ts": latest.last_event_ts if latest else None,
            "attention_task_id": attention.task_id if attention else None,
            "attention_task_status": attention.status if attention else None,
            "attention_reason": _task_attention_reason(attention) if attention else None,
            "attention_next_step": _task_next_step(project, str(team_name), attention),
            "profile_drift": profile_drift,
            "session_tool": session_rt["tool"] if session_rt else None,
            "session_auth_mode": session_rt["auth_mode"] if session_rt else None,
            "session_provider": session_rt["provider"] if session_rt else None,
            "session_name": session_rt["session_name"] if session_rt else None,
            "session_status": session_status,
            "workspace_dirty": workspace_dirty,
            "review_latest_clean": review_latest_clean,
            "review_latest_commit": review_latest_commit,
            "integration_status": integration.get("status"),
            "integration_reason": integration.get("reason"),
            "integration_receipt_path": integration.get("receipt_path"),
            "integration_reported_commit": integration.get("reported_commit"),
            "dispatch_readiness": readiness,
            "dispatch_readiness_reason": readiness_reason,
        })
    return rows


def cmd_planner_status(args: argparse.Namespace) -> int:
    """Print a planner/queue status snapshot for all teams in a project."""
    try:
        _validate_identifier(args.project, "project")
    except InputValidationError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2
    missing_deps = _cfg.check_script_deps()
    if missing_deps:
        dep_str = ", ".join(missing_deps)
        print(
            f"[PREFLIGHT_WARN] missing script deps: {dep_str}"
            f" — install with: pip3 install {' '.join(missing_deps)}",
            file=sys.stderr,
        )
    rows = planner_status_snapshot(args.project)
    if not rows:
        print(f"no planner teams found for project {args.project!r}")
        return 0
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        tool_label = row["tool"] or "unknown"
        if row["model"] and row["model"] != "unknown":
            tool_label = f"{tool_label}/{row['model']}"
        runtime_parts = [p for p in (row.get("provider", ""), row.get("auth_mode", "")) if p]
        if runtime_parts:
            tool_label = f"{tool_label}({','.join(runtime_parts)})"
        latest_label = (
            f"{row['latest_task_id']} [{row['latest_task_status']}]"
            if row["latest_task_id"]
            else "no tasks"
        )
        drift_suffix = ""
        if row.get("profile_drift"):
            session_tool = row.get("session_tool") or "?"
            session_auth = row.get("session_auth_mode") or "?"
            session_prov = row.get("session_provider") or "?"
            drift_suffix = f"  [DRIFT: session={session_tool}({session_prov},{session_auth})]"
        live_suffix = ""
        if row.get("session_status") == "dead":
            live_suffix = f"  [DEAD: session={row.get('session_name') or '?'}]"
        elif row.get("session_status") == "alive":
            live_suffix = "  [LIVE]"
        attention_suffix = ""
        if row.get("attention_task_id"):
            attention_suffix = (
                f"  attention={row['attention_task_id']}"
                f"[{row['attention_task_status']}]"
            )
            if row.get("attention_reason"):
                attention_suffix += f": {row['attention_reason']}"
            if row.get("attention_next_step"):
                attention_suffix += f"; next={row['attention_next_step']}"
        dn = row.get("display_name") or ""
        seat_id = row["planner_seat"]
        planner_label = f"{dn} ({seat_id})" if dn and dn != seat_id else seat_id
        print(
            f"{row['team']:30s}  planner={planner_label}"
            f"  tool={tool_label}"
            f"  queue={row['queue_state']}({row['task_count']})"
            f"  latest={latest_label}"
            f"{attention_suffix}"
            f"{drift_suffix}"
            f"{live_suffix}"
        )
        readiness = row.get("dispatch_readiness", "unknown")
        readiness_reason = row.get("dispatch_readiness_reason", "")
        dirty_flag = "dirty" if row.get("workspace_dirty") else "clean"
        merged_flag = "merged" if row.get("review_latest_clean") else "unmerged"
        integration_suffix = ""
        if row.get("integration_status") == "needs_integration":
            integration_suffix = "  integration=needs_integration"
        print(
            f"{'':30s}  dispatch_readiness={readiness}"
            f"  workspace={dirty_flag}"
            f"  review/latest={merged_flag}"
            f"{integration_suffix}"
            f"  [{readiness_reason}]"
        )
    # Review worktree status — project-level, shown once after all team rows
    if not getattr(args, "json", False):
        try:
            wt = check_review_latest_worktree(args.project)
            rv = wt["review_worktree"]
            lv = wt["launcher_worktree"]
            rv_status = f"{rv['status']}+dirty" if rv.get("dirty") else rv["status"]
            lv_status = f"{lv['status']}+dirty" if lv.get("dirty") else lv["status"]
            rv_label = f"{rv_status}@{rv['commit']}" if rv.get("commit") else rv_status
            lv_label = f"{lv_status}@{lv['commit']}" if lv.get("commit") else lv_status
            print(
                f"review/latest  worktree={rv_label}({rv['path']})"
                f"  launcher={lv_label}({lv['path']})"
            )
        except Exception:  # noqa: BLE001
            pass
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    """Mark a brief task as done by appending the official queue events.

    The command is intentionally tolerant of older rehearsal/task flows that
    reached a durable handoff PASS before the queue was advanced. It preserves
    queue_io's state machine by appending any missing intermediate states
    instead of bypassing validation.
    """
    try:
        _validate_cli_inputs(args.project, args.team, args.task_id)
    except InputValidationError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2

    queue = _queue_path(args.project, args.team)
    state = read_current_state(queue)
    ts = state.get(args.task_id)
    if ts is None:
        print(f"task_id {args.task_id!r} not in queue", file=sys.stderr)
        return 2

    brief_path = Path(ts.brief_path)
    if not brief_path.is_absolute():
        brief_path = _agents_root() / brief_path
    if brief_path.exists():
        acceptance_block = _brief_acceptance_block_reason(
            brief_path.read_text(encoding="utf-8"),
            str(brief_path),
        )
    else:
        acceptance_block = f"brief missing: {brief_path}"
    if acceptance_block:
        print(
            f"task {args.task_id} blocked on acceptance_criteria: {acceptance_block}",
            file=sys.stderr,
        )
        return 3

    actor = args.actor
    if ts.status == "task_done":
        if ts.verdict == "PASS":
            print(f"task {args.task_id} already done")
            try:
                _maybe_relay_queue_drained(args.project, args.team, args.task_id, args.actor)
            except Exception as exc:  # noqa: BLE001
                print(f"RELAY_ERROR unexpected: {exc}", file=sys.stderr)
            return 0
        print(
            f"task_id {args.task_id!r} is done with verdict {ts.verdict!r}, not PASS",
            file=sys.stderr,
        )
        return 2

    unmet = _unmet_dependencies(args.project, args.team, state, ts.depends_on)
    if unmet:
        print(
            f"task_id {args.task_id!r} still blocked on upstream: {unmet}",
            file=sys.stderr,
        )
        return 3

    plan: list[str]
    if ts.status in ("task_created", "task_waiting_for", "task_reset"):
        plan = ["task_claimed", "task_in_progress", "task_done"]
    elif ts.status == "task_claimed":
        plan = ["task_in_progress", "task_done"]
    elif ts.status == "task_in_progress":
        plan = ["task_done"]
    else:
        print(
            f"task_id {args.task_id!r} is in state {ts.status!r}, not completable",
            file=sys.stderr,
        )
        return 2

    last_result: dict | None = None
    for event_type in plan:
        event = {
            "event_type": event_type,
            "actor": actor,
            "task_id": args.task_id,
        }
        if event_type == "task_done":
            event["verdict"] = "PASS"
        try:
            last_result = append_event(queue, event)
        except QueueError as exc:
            print(f"done append failed: {exc}", file=sys.stderr)
            return 1

    seq = last_result["seq"] if last_result else "?"
    print(f"done {args.task_id} (seq {seq}, verdict PASS)")
    try:
        _maybe_relay_queue_drained(args.project, args.team, args.task_id, actor)
    except Exception as exc:  # noqa: BLE001
        print(f"RELAY_ERROR unexpected: {exc}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent_admin_brief",
        description="ClawSeat v3 brief / queue subcommand (Phase 1 minimal).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("queue", help="Write brief + append task_created event")
    q.add_argument("--project", required=True)
    q.add_argument("--team", required=True)
    q.add_argument("--task-id", required=True, dest="task_id")
    q.add_argument("--objective", required=True)
    q.add_argument("--depends-on", nargs="*", default=[], dest="depends_on")
    q.add_argument(
        "--seats-required",
        nargs="*",
        default=None,
        dest="seats_required",
        help="Seats required (default: ['builder']). Schema requires non-empty.",
    )
    q.add_argument("--parent-task-id", default=None, dest="parent_task_id")
    q.add_argument("--brief-content-file", default=None, dest="brief_content_file",
                   help="Path to pre-written brief markdown. Implicitly allows overwrite "
                        "when the brief file already exists (no --force needed). "
                        "Without this flag, --force generates a skeleton.")
    q.add_argument("--force", action="store_true",
                   help="Overwrite existing brief with skeleton. "
                        "When combined with --brief-content-file, uses the provided "
                        "content instead of generating a skeleton.")
    q.add_argument("--no-wake", action="store_true",
                   help="Append the queue event without waking the team planner.")
    q.add_argument("--allow-open", action="store_true",
                   help="Explicitly allow queueing while this team has another open task.")
    q.add_argument("--allow-unintegrated", action="store_true",
                   help="Explicitly allow queueing after a PASS delivery still needs review/latest integration.")
    q.set_defaults(func=cmd_queue)

    l = sub.add_parser("list", help="List tasks for a team (default: pending only)")
    l.add_argument("--project", required=True)
    l.add_argument("--team", required=True)
    l.add_argument("--all", action="store_true")
    l.set_defaults(func=cmd_list)

    c = sub.add_parser("claim", help="Planner claims a pending task")
    c.add_argument("--project", required=True)
    c.add_argument("--team", required=True)
    c.add_argument("--task-id", required=True, dest="task_id")
    c.add_argument("--actor", required=True,
                   help="Format: <role>@<tool>, e.g. planner@claude")
    c.set_defaults(func=cmd_claim)

    r = sub.add_parser("reset", help="Append task_reset for a recoverable task")
    r.add_argument("--project", required=True)
    r.add_argument("--team", required=True)
    r.add_argument("--task-id", required=True, dest="task_id")
    r.add_argument("--actor", default="memory",
                   help="Actor for task_reset (default: memory)")
    r.add_argument("--reason", required=True,
                   help="Short reset reason for the queue event.")
    r.set_defaults(func=cmd_reset)

    rq = sub.add_parser("requeue", help="Recover a blocked/reset task and wake planner")
    rq.add_argument("--project", required=True)
    rq.add_argument("--team", required=True)
    rq.add_argument("--task-id", required=True, dest="task_id")
    rq.add_argument("--actor", default="memory",
                    help="Actor for recovery events (default: memory)")
    rq.add_argument("--reason", default="requeue after brief repair",
                    help="Reset reason if the task is currently waiting_for.")
    rq.add_argument("--no-wake", action="store_true",
                    help="Recover the queue event without waking the team planner.")
    rq.set_defaults(func=cmd_requeue)

    st = sub.add_parser("start", help="Mark a claimed task_in_progress")
    st.add_argument("--project", required=True)
    st.add_argument("--team", required=True)
    st.add_argument("--task-id", required=True, dest="task_id")
    st.add_argument("--actor", required=True,
                    help="Format: <role>@<tool>, e.g. planner@claude")
    st.set_defaults(func=cmd_start)

    s = sub.add_parser("show", help="Show current state of a task_id")
    s.add_argument("--project", required=True)
    s.add_argument("--team", required=True)
    s.add_argument("--task-id", required=True, dest="task_id")
    s.set_defaults(func=cmd_show)

    d = sub.add_parser("done", help="Append task_done PASS for a brief task")
    d.add_argument("--project", required=True)
    d.add_argument("--team", required=True)
    d.add_argument("--task-id", required=True, dest="task_id")
    d.add_argument("--actor", required=True,
                   help="Format: <role>@<tool>, e.g. planner@claude")
    d.add_argument("--verdict", default="PASS", choices=["PASS"],
                   help="Only PASS is accepted for task_done.")
    d.set_defaults(func=cmd_done)

    ps = sub.add_parser(
        "planner-status",
        help="Print a status snapshot for all planner teams in a project",
    )
    ps.add_argument("--project", required=True)
    ps.add_argument("--json", action="store_true", help="Emit JSON instead of plain text")
    ps.set_defaults(func=cmd_planner_status)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
