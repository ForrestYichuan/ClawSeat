"""ClawSeat v3 acceptance executor (Phase 2).

Runs `brief.acceptance_criteria.{mechanical,reviewer,operator}` per spec §4.7.
Memory writes the acceptance criteria; planner invokes this executor after
all workflow steps finish.

Three routes:
- mechanical: physically run shell commands; capture stdout/stderr/exit
- reviewer:   dispatch to reviewer seat via dispatch_task.py (existing infra)
- operator:   write batched questions to operator-pending file; operator answers manually

See spec §4.7.4 (acceptance routing) + brief.schema.json.
"""

from __future__ import annotations

import json
import os
import stat as _stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = REPO_ROOT / "core" / "schemas" / "brief.schema.json"
# Ensure sibling lib modules importable when called as standalone
if str(REPO_ROOT / "core" / "lib") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))
from acceptance_criteria import (  # noqa: E402
    criterion_command_and_text as _shared_criterion_command_and_text,
    criterion_is_shell_runnable,
)
from real_home import real_user_home  # noqa: E402

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover — PyYAML is a hard dep
    yaml = None  # type: ignore


class AcceptanceError(RuntimeError):
    """Schema or invocation error. Distinct from acceptance FAIL (which is data)."""


_RESULT_PASS = "pass"
_RESULT_FAIL = "fail"
_RESULT_PENDING = "pending"
_RESULT_SKIPPED = "skipped"
_RESULT_DIAGNOSTIC = "diagnostic"  # non-blocking baseline evidence; never contributes to aggregate FAIL


@dataclass
class ItemResult:
    criterion: str
    result: str  # 'pass' | 'fail' | 'pending' | 'skipped' | 'diagnostic'
    command: str | None = None
    exit_code: int | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    runtime_ms: int | None = None
    dispatch_receipt: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class RouteResult:
    route: str
    verdict: str  # 'PASS' | 'FAIL' | 'PENDING'
    items: list[ItemResult] = field(default_factory=list)


def _agents_root() -> Path:
    return real_user_home() / ".agents"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stringify_datetimes(value):
    """Round 4 #D: recursively convert datetime/date to ISO strings.

    PyYAML safe_load auto-parses unquoted ISO timestamps to datetime objects;
    schemas declaring date-time format with type:string then reject them.
    Normalize before schema validation.
    """
    from datetime import date, datetime
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _stringify_datetimes(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify_datetimes(v) for v in value]
    return value


def _validate_brief_schema(brief: dict, brief_path: Path) -> None:
    """Post-retest #2 + Round 4 #D: validate brief against brief.schema.json.

    Schema enforces minItems:1 for seats_required and requires at least one
    acceptance route item. Pure review/operator briefs may omit mechanical;
    empty acceptance can no longer vacuously PASS.

    Uses jsonschema when installed; falls back to lightweight required-field +
    minItems check sufficient for known v3 invariants.

    Round 4 #D: brief datetimes (created/deadline) are converted to ISO strings
    before validation to avoid PyYAML's auto-datetime coercion confusing the
    schema's string format declaration.
    """
    brief = _stringify_datetimes(brief)
    try:
        import jsonschema  # type: ignore
    except ImportError:
        # Lightweight fallback: enforce the two minItems we care about.
        seats = brief.get("seats_required")
        if not isinstance(seats, list) or not seats:
            raise AcceptanceError(
                f"{brief_path}: brief.seats_required must have minItems 1"
            )
        ac = brief.get("acceptance_criteria") or {}
        if not isinstance(ac, dict):
            raise AcceptanceError(
                f"{brief_path}: brief.acceptance_criteria must be a mapping"
            )
        has_any_route_item = False
        for route in ("mechanical", "reviewer", "operator"):
            items = ac.get(route) or []
            if not isinstance(items, list):
                raise AcceptanceError(
                    f"{brief_path}: brief.acceptance_criteria.{route} must be a list"
                )
            has_any_route_item = has_any_route_item or bool(items)
        if not has_any_route_item:
            raise AcceptanceError(
                f"{brief_path}: brief.acceptance_criteria must include at least one route item"
            )
        return

    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(brief, schema)
    except jsonschema.ValidationError as exc:
        raise AcceptanceError(
            f"{brief_path}: brief schema violation — {exc.message} (at {'.'.join(str(p) for p in exc.absolute_path)})"
        )


def _load_brief(brief_path: Path) -> dict:
    if yaml is None:
        raise AcceptanceError("PyYAML required")
    text = brief_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AcceptanceError(f"{brief_path}: missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        end = text.find("\n---", 4)
    if end == -1:
        raise AcceptanceError(f"{brief_path}: unterminated frontmatter")
    data = yaml.safe_load(text[4:end])
    if not isinstance(data, dict):
        raise AcceptanceError(f"{brief_path}: frontmatter is not a mapping")
    return data


def _criterion_command_and_text(criterion: Any) -> tuple[str, str]:
    """Normalize a criterion entry to (display_text, shell_command)."""
    try:
        return _shared_criterion_command_and_text(criterion)
    except ValueError as exc:
        raise AcceptanceError(str(exc)) from exc


def _criterion_route(criterion: Any) -> str:
    """Post-retest #6: per-criterion route override.

    Default for items inside mechanical[] is 'mechanical'. If a dict criterion
    declares route: reviewer/operator, it diverts there even when nested in
    the mechanical section.
    """
    if isinstance(criterion, dict):
        route = str(criterion.get("route") or "").strip()
        if route in ("mechanical", "reviewer", "operator"):
            return route
    return "mechanical"


def _split_by_route(brief: dict) -> tuple[list, list, list]:
    """Round 4 #A: split criteria by per-item route override.

    For each section in brief.acceptance_criteria (mechanical/reviewer/operator),
    inspect each item's `route` field. Items with explicit route override are
    moved to the target section. Returns (mech_items, reviewer_items, operator_items).
    """
    acceptance = brief.get("acceptance_criteria") or {}
    base_mech = acceptance.get("mechanical") or []
    base_rev = list(acceptance.get("reviewer") or [])
    base_op = list(acceptance.get("operator") or [])

    final_mech: list = []
    for item in base_mech:
        route = _criterion_route(item)
        if route == "reviewer":
            base_rev.append(item)
        elif route == "operator":
            base_op.append(item)
        else:
            final_mech.append(item)
    return final_mech, base_rev, base_op


def _normalize_python_invocations(cmd: str) -> str:
    """Rewrite python/python3/pytest command names to full sys.executable paths.

    Acceptance commands often use ``bash -lc`` (login shell) which re-reads
    .bash_profile / .zprofile and may reset PATH to system defaults, causing
    the ambient ``python3`` (e.g. /Library/Developer/CommandLineTools/usr/bin/python3)
    to be used instead of the project interpreter that has tomllib/pytest.

    Replacing the command name with the full path to sys.executable bypasses
    PATH lookup entirely, so the correct interpreter is always used.

    Only replaces standalone command names (not paths like /usr/bin/python3).
    Handles ``python3 -m pytest`` as a unit to avoid double-replacement.
    """
    import re  # noqa: PLC0415
    exe = sys.executable
    # Step 1: Replace "python3 -m pytest" and "python -m pytest" as units (most specific)
    for py_cmd in ("python3", "python"):
        cmd = re.sub(
            rf"(?<![/\w]){py_cmd}\s+-m\s+pytest(?!\w)",
            f"{exe} -m pytest",
            cmd,
        )
    # Step 2: Replace standalone "pytest" — skip when already following "-m "
    cmd = re.sub(r"(?<!-m )(?<![/\w])pytest(?!\w)", f"{exe} -m pytest", cmd)
    # Step 3: Replace remaining standalone "python3" and "python"
    cmd = re.sub(r"(?<![/\w])python3(?!\w)", exe, cmd)
    cmd = re.sub(r"(?<![/\w])python(?!\w)", exe, cmd)
    return cmd


def _make_python_shims(shim_dir: Path) -> None:
    """Write python/python3/pytest shims that delegate to the current sys.executable.

    Prepending shim_dir to PATH ensures acceptance mechanical commands that
    invoke `python`, `python3`, or `pytest` use the same interpreter and
    installed packages as the executor — not an ambient system Python that
    may lack tomllib/tomli or the project's pytest version.
    """
    exe = sys.executable
    entries = [
        ("python",  f"{exe}"),
        ("python3", f"{exe}"),
        ("pytest",  f"{exe} -m pytest"),
    ]
    for name, argv0 in entries:
        shim = shim_dir / name
        shim.write_text(f'#!/bin/sh\nexec {argv0} "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)


def _resolve_reviewer_seat_from_profile(
    team: str,
    project: str,
    agents_root: Path | None = None,
    profile_path: Path | None = None,
) -> str:
    """Return the reviewer seat for a team by consulting the project profile.

    Resolution order:
    1. If the team declares a dedicated reviewer seat in the profile → use it.
    2. If no reviewer seat (dev-minimal/planner-owned) → fall back to the
       team's planner seat (planner self-review).
    3. If the profile is unavailable or the team is not found → fall back to
       the legacy {team}-reviewer string.

    This prevents dispatching to non-existent {team}-reviewer seats for
    planner-owned subgroups (CF041/CF042 acceptance blocker).
    """
    if agents_root is None:
        agents_root = _agents_root()
    if profile_path is None:
        profile_path = agents_root / "profiles" / f"{project}-profile-dynamic.toml"
    try:
        from profile_loader_v3 import load_profile_v3  # noqa: PLC0415
        profile = load_profile_v3(profile_path)
        team_name = team if profile.is_multi() else "default"
        seats = profile.seats_of(team_name)
        reviewers = [s for s in seats if profile.seat_roles.get(s) == "reviewer"]
        if reviewers:
            return reviewers[0]
        planners = [s for s in seats if profile.seat_roles.get(s) == "planner"]
        if planners:
            return planners[0]
    except Exception:  # noqa: BLE001 — profile unavailable or team missing
        pass
    return f"{team}-reviewer"


def _criterion_is_diagnostic(criterion: Any, idx: int, baseline_indices: set[int]) -> bool:
    """Return True when a criterion should be treated as non-blocking diagnostic evidence."""
    if idx in baseline_indices:
        return True
    if isinstance(criterion, dict) and criterion.get("diagnostic"):
        return True
    return False


def run_mechanical(
    brief: dict,
    acceptance_dir: Path,
    task_id: str,
    cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    pre_split_mech: list | None = None,
    baseline_indices: set[int] | None = None,
) -> RouteResult:
    """Run each mechanical criterion. Returns aggregate verdict.

    Captures stdout + stderr to per-criterion files under acceptance_dir.

    Round 4 #A: caller normally passes `pre_split_mech` (already filtered to
    items truly destined for mechanical execution); items with route override
    have been re-routed by the caller. Backwards-compatible: when None, the
    function recomputes the split internally.
    """
    if pre_split_mech is None:
        pre_split_mech, _, _ = _split_by_route(brief)
    mech = pre_split_mech
    result = RouteResult(route="mechanical", verdict="PASS")

    # Pure review/operator briefs legitimately have no mechanical route. The
    # schema already blocks truly empty acceptance; an empty mechanical section
    # here means "no shell work", not failure.
    if not mech:
        return result

    acceptance_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)

    _baseline = baseline_indices or set()
    any_fail = False
    any_executed = False
    with tempfile.TemporaryDirectory(prefix="clawseat-acceptance-shims-") as _shim_dir:
        _shim_path = Path(_shim_dir)
        _make_python_shims(_shim_path)
        env["PATH"] = f"{_shim_path}{os.pathsep}{env.get('PATH', os.defpath)}"
        for idx, item in enumerate(mech):
            text, cmd = _criterion_command_and_text(item)
            is_diagnostic = _criterion_is_diagnostic(item, idx, _baseline)
            if not cmd or not criterion_is_shell_runnable(item):
                result.items.append(
                    ItemResult(
                        criterion=text,
                        result=_RESULT_SKIPPED,
                        command="not_shell_runnable",
                    )
                )
                continue
            stdout_p = acceptance_dir / f"{task_id}__mech__{idx:02d}.stdout"
            stderr_p = acceptance_dir / f"{task_id}__mech__{idx:02d}.stderr"
            start = time.monotonic()
            try:
                any_executed = True
                proc = subprocess.run(
                    _normalize_python_invocations(cmd),
                    shell=True,
                    cwd=str(cwd) if cwd else None,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                runtime_ms = int((time.monotonic() - start) * 1000)
                stdout_p.write_text(proc.stdout, encoding="utf-8")
                stderr_p.write_text(proc.stderr, encoding="utf-8")
                if proc.returncode == 0:
                    verdict = _RESULT_PASS
                elif is_diagnostic:
                    verdict = _RESULT_DIAGNOSTIC
                else:
                    verdict = _RESULT_FAIL
                    any_fail = True
                result.items.append(
                    ItemResult(
                        criterion=text,
                        result=verdict,
                        command=cmd,
                        exit_code=proc.returncode,
                        stdout_path=str(stdout_p),
                        stderr_path=str(stderr_p),
                        runtime_ms=runtime_ms,
                    )
                )
            except subprocess.TimeoutExpired as exc:
                runtime_ms = int((time.monotonic() - start) * 1000)
                stdout_p.write_text(exc.stdout or "", encoding="utf-8")
                stderr_p.write_text(
                    (exc.stderr or "") + f"\n[acceptance_executor] timeout after {runtime_ms}ms",
                    encoding="utf-8",
                )
                any_fail = True
                result.items.append(
                    ItemResult(
                        criterion=text,
                        result="fail",
                        command=cmd,
                        exit_code=-1,
                        stdout_path=str(stdout_p),
                        stderr_path=str(stderr_p),
                        runtime_ms=runtime_ms,
                    )
                )

    result.verdict = "FAIL" if any_fail or not any_executed else "PASS"
    return result


def route_reviewer(
    brief: dict,
    project: str,
    team: str,
    task_id: str,
    acceptance_dir: Path,
    reviewer_seat: str | None = None,
    dispatch_fn=None,
    pre_split_items: list | None = None,
    profile_path: Path | None = None,
) -> RouteResult:
    """Route reviewer items via dispatch_task.py. PENDING until reviewer relays.

    dispatch_fn is injected for testing; in production it shells out to
    `dispatch_task.py`. We don't fail if reviewer is offline — we record
    PENDING and let planner watch DELIVERY.md as in existing 7-step loop.

    Round 4 #A: caller supplies `pre_split_items` (reviewer items plus any
    mechanical items with route=reviewer override). Backwards-compatible:
    None falls back to bare brief.reviewer list.
    """
    items = (
        pre_split_items
        if pre_split_items is not None
        else (brief.get("acceptance_criteria") or {}).get("reviewer") or []
    )
    result = RouteResult(route="reviewer", verdict="PASS")  # vacuous PASS if empty

    if not items:
        return result

    result.verdict = "PENDING"
    acceptance_dir.mkdir(parents=True, exist_ok=True)

    # Resolve reviewer seat: prefer explicit arg, then profile-based routing
    # (dedicated reviewer or planner fallback for dev-minimal teams).
    if not reviewer_seat:
        reviewer_seat = _resolve_reviewer_seat_from_profile(
            team, project, profile_path=profile_path
        )

    # Materialize a single dispatch packet listing all reviewer items.
    packet_path = acceptance_dir / f"{task_id}__reviewer.dispatch.json"
    packet = {
        "task_id": task_id,
        "project": project,
        "team": team,
        "reviewer_seat": reviewer_seat,
        "items": [_criterion_command_and_text(c)[0] for c in items],
        "ts": _utc_now(),
    }
    packet_path.write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")

    if dispatch_fn is not None:
        try:
            dispatch_receipt = dispatch_fn(packet)
        except Exception as exc:  # noqa: BLE001
            dispatch_receipt = f"dispatch error: {exc}"
    else:
        dispatch_receipt = str(packet_path)  # placeholder; planner uses existing dispatch infra

    for c in items:
        text, _ = _criterion_command_and_text(c)
        result.items.append(
            ItemResult(
                criterion=text,
                result="pending",
                dispatch_receipt=dispatch_receipt,
            )
        )
    return result


def route_operator(
    brief: dict,
    project: str,
    team: str,
    task_id: str,
    acceptance_dir: Path,
    pre_split_items: list | None = None,
) -> RouteResult:
    """Batch operator items into a pending-answer file.

    Round 4 #A: pre_split_items includes operator-routed items from any
    section (including mechanical with route=operator override).
    """
    items = (
        pre_split_items
        if pre_split_items is not None
        else (brief.get("acceptance_criteria") or {}).get("operator") or []
    )
    result = RouteResult(route="operator", verdict="PASS")  # vacuous PASS

    if not items:
        return result

    result.verdict = "PENDING"
    acceptance_dir.mkdir(parents=True, exist_ok=True)
    pending_path = acceptance_dir / f"{task_id}__operator.pending.json"

    questions = []
    for idx, c in enumerate(items):
        text, _ = _criterion_command_and_text(c)
        questions.append({"id": f"q{idx:02d}", "criterion": text, "answer": None, "answered_ts": None})

    pending_path.write_text(
        json.dumps({"task_id": task_id, "project": project, "team": team, "ts": _utc_now(), "questions": questions}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    for q in questions:
        result.items.append(ItemResult(criterion=q["criterion"], result="pending"))

    return result


def _validate_brief_matches_cli(brief: dict, project: str, team: str, task_id: str) -> None:
    """Post-review Phase 2 fix #A: refuse to run when brief frontmatter
    disagrees with CLI args. Prevents wrong task/project being marked accepted."""
    mismatches = []
    for field, expected in (("task_id", task_id), ("project", project), ("team", team)):
        actual = brief.get(field)
        if actual is None:
            mismatches.append(f"brief missing {field}")
        elif str(actual) != str(expected):
            mismatches.append(f"brief.{field}={actual!r} vs CLI {field}={expected!r}")
    if mismatches:
        raise AcceptanceError("brief vs CLI mismatch: " + "; ".join(mismatches))


def run_acceptance(
    project: str,
    team: str,
    task_id: str,
    brief_path: Path | None = None,
    reviewer_seat: str | None = None,
    dispatch_fn=None,
    cwd: Path | None = None,
    profile_path: Path | None = None,
    baseline_indices: set[int] | None = None,
) -> dict[str, RouteResult]:
    """Top-level entry: run all three routes, write receipts, return dict.

    Caller (typically planner via agent_admin acceptance run) uses the
    aggregate verdict to decide next step.
    """
    agents_root = _agents_root()
    if brief_path is None:
        brief_path = agents_root / "tasks" / project / team / "brief" / f"{task_id}.md"
    if not brief_path.exists():
        raise AcceptanceError(f"brief not found: {brief_path}")
    brief = _load_brief(brief_path)

    # Phase 2 fix #A: brief frontmatter must agree with CLI args
    _validate_brief_matches_cli(brief, project, team, task_id)

    # Post-retest #2: brief must conform to brief.schema.json
    _validate_brief_schema(brief, brief_path)

    acceptance_dir = agents_root / "tasks" / project / team / "acceptance"

    # Default reviewer dispatch: invoke real dispatch_task.py subprocess (fix #C).
    # Inject None or a fake to opt out (used in tests).
    if dispatch_fn is None:
        dispatch_fn = lambda packet: _default_reviewer_dispatch(  # noqa: E731
            packet, profile_path=profile_path, agents_root=agents_root, project=project
        )

    # Round 4 #A: split criteria once by route, pass to each route helper
    final_mech, final_rev, final_op = _split_by_route(brief)
    results = {
        "mechanical": run_mechanical(
            brief, acceptance_dir, task_id, cwd=cwd, pre_split_mech=final_mech,
            baseline_indices=baseline_indices,
        ),
        "reviewer": route_reviewer(
            brief, project, team, task_id, acceptance_dir,
            reviewer_seat=reviewer_seat, dispatch_fn=dispatch_fn,
            pre_split_items=final_rev, profile_path=profile_path,
        ),
        "operator": route_operator(
            brief, project, team, task_id, acceptance_dir,
            pre_split_items=final_op,
        ),
    }

    # Post-retest #3: wire fuzz_required into acceptance. When the brief
    # declares fuzz_required=true, an absent/failed fuzz run downgrades the
    # mechanical verdict to FAIL (spec §4.7.3).
    fuzz_outcome = _maybe_run_fuzz(brief, acceptance_dir, task_id)
    if fuzz_outcome is not None:
        # Append a synthetic mechanical item that captures fuzz result
        mech = results["mechanical"]
        mech.items.append(fuzz_outcome)
        if fuzz_outcome.result != "pass":
            mech.verdict = "FAIL"

    # Phase 2 fix #B: consolidated __mechanical.log per spec §4.7.1
    _write_mechanical_consolidated_log(
        acceptance_dir, task_id, results["mechanical"]
    )

    # Persist mechanical receipt
    mech_receipt = acceptance_dir / f"{task_id}__mechanical.json"
    _write_receipt(mech_receipt, project, team, task_id, results["mechanical"])

    # Persist reviewer/operator state too (verdict=PENDING until they answer)
    rev_receipt = acceptance_dir / f"{task_id}__reviewer.json"
    _write_receipt(rev_receipt, project, team, task_id, results["reviewer"])

    op_receipt = acceptance_dir / f"{task_id}__operator.json"
    _write_receipt(op_receipt, project, team, task_id, results["operator"])

    # cf013: auto-reconcile reviewer section from any routed __accept_review handoff.
    # Checks patrol/handoffs for {task_id}__accept_review__*__memory.json; if found with
    # APPROVED/APPROVED_WITH_NITS → updates reviewer receipt to PASS; FAIL/CHANGES_REQUESTED
    # → FAIL. Historical handoff files are never touched. Works on both first-run dispatch
    # and re-runs against an already-dispatched task.
    handoffs_dir = _agents_root() / "tasks" / project / "patrol" / "handoffs"
    reconciled_reviewer = reconcile_reviewer_acceptance(task_id, acceptance_dir, handoffs_dir=handoffs_dir)
    if reconciled_reviewer is not None:
        results["reviewer"] = reconciled_reviewer

    return results


def _maybe_run_fuzz(
    brief: dict, acceptance_dir: Path, task_id: str
) -> ItemResult | None:
    """Post-retest #3: invoke fuzz_harness when brief.fuzz_required=true.

    Returns an ItemResult capturing the fuzz outcome (to append to mechanical),
    or None if fuzz_required is false / absent.

    FAIL conditions:
    - fuzz_required=true but fuzz_spec missing/empty → FAIL
    - fuzz harness reports any failure → FAIL
    - fuzz harness raises → FAIL
    """
    if not brief.get("fuzz_required"):
        return None

    spec = brief.get("fuzz_spec")
    descriptor = "fuzz (required by brief.fuzz_required)"
    if not spec:
        return ItemResult(
            criterion=descriptor,
            result="fail",
            command="fuzz_harness",
            exit_code=-1,
            runtime_ms=0,
        )

    # spec may be either a single spec dict OR list of spec dicts; normalize
    if isinstance(spec, dict):
        specs = [spec]
    elif isinstance(spec, list):
        specs = list(spec)
    else:
        return ItemResult(
            criterion=descriptor + f" (invalid spec type: {type(spec).__name__})",
            result="fail",
            command="fuzz_harness",
        )

    # Import lazily to avoid circular if fuzz_harness imports from executor.
    from fuzz_harness import FuzzError, run_fuzz

    acceptance_dir.mkdir(parents=True, exist_ok=True)
    any_failure = False
    aggregate_runtime = 0
    aggregate_cases = 0
    aggregate_failures = 0

    for s in specs:
        s_dict = dict(s) if isinstance(s, dict) else {}
        target_command = s_dict.pop("target_command", None)
        iterations = int(s_dict.pop("iterations", 100))
        seed = s_dict.pop("seed", None)

        # Round 4 #C: fuzz_required must be exercised against a real target.
        # No more permissive JSON-noop fallback — missing target_command is FAIL.
        if not target_command:
            return ItemResult(
                criterion=descriptor + " (spec missing target_command — won't run)",
                result="fail",
                command="fuzz_harness",
            )

        try:
            r = run_fuzz(
                s_dict,
                target_command=target_command,
                iterations=iterations,
                seed=seed,
                out_dir=acceptance_dir / "fuzz",
            )
            aggregate_cases += r.cases_run
            aggregate_runtime += r.elapsed_ms
            if r.failures:
                any_failure = True
                aggregate_failures += len(r.failures)
        except FuzzError as exc:
            return ItemResult(
                criterion=descriptor + f" (spec error: {exc})",
                result="fail",
                command="fuzz_harness",
            )

    return ItemResult(
        criterion=(
            f"{descriptor}: {aggregate_cases} cases, {aggregate_failures} failures"
        ),
        result="fail" if any_failure else "pass",
        command="fuzz_harness",
        runtime_ms=aggregate_runtime,
    )


def _default_reviewer_dispatch(
    packet: dict,
    *,
    profile_path: Path | None,
    agents_root: Path,
    project: str,
) -> str:
    """Phase 2 fix #C: actually invoke dispatch_task.py subprocess.

    Returns a string describing the outcome (success → 'dispatched seq=…';
    profile missing → 'skipped: profile not found'; subprocess fail → error
    string). Acceptance receipt records this in dispatch_receipt field.
    """
    if profile_path is None:
        profile_path = agents_root / "profiles" / f"{project}-profile-dynamic.toml"
    if not Path(profile_path).exists():
        return f"skipped: profile not found at {profile_path}"

    repo_root = Path(__file__).resolve().parents[2]
    dispatch_script = repo_root / "core" / "skills" / "gstack-harness" / "scripts" / "dispatch_task.py"
    if not dispatch_script.exists():
        return f"skipped: dispatch_task.py not found at {dispatch_script}"

    items = packet.get("items") or []
    if not items:
        return "skipped: no reviewer items"

    objective = "Acceptance review batch: " + "; ".join(items[:10])
    if len(items) > 10:
        objective += f" (+ {len(items) - 10} more)"

    task_id = str(packet["task_id"])
    reviewer_seat = str(packet["reviewer_seat"])
    review_task_id = f"{task_id}__accept_review"

    cmd = [
        sys.executable,
        str(dispatch_script),
        "--profile", str(profile_path),
        "--source", "memory",        # executor invokes on behalf of memory's acceptance
        "--target", reviewer_seat,
        "--task-id", review_task_id,
        "--title", f"acceptance review for {task_id}",
        "--objective", objective,
        "--test-policy", "N/A",
        "--reply-to", "memory",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "error: dispatch_task.py timeout 60s"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()[-1] if (proc.stderr or proc.stdout) else f"exit {proc.returncode}"
        return f"error: dispatch_task.py exit {proc.returncode}: {err[:200]}"
    return f"dispatched: task_id={review_task_id} target={reviewer_seat}"


def _write_mechanical_consolidated_log(
    acceptance_dir: Path, task_id: str, result: RouteResult
) -> None:
    """Phase 2 fix #B: write consolidated __mechanical.log per spec §4.7.1."""
    acceptance_dir.mkdir(parents=True, exist_ok=True)
    log_path = acceptance_dir / f"{task_id}__mechanical.log"
    lines: list[str] = [
        f"# Mechanical acceptance log for task_id={task_id}",
        f"# Verdict: {result.verdict}",
        f"# Generated: {_utc_now()}",
        "",
    ]
    for idx, item in enumerate(result.items):
        lines.append("=" * 72)
        lines.append(f"Criterion #{idx}: {item.criterion}")
        lines.append(f"Result: {item.result}")
        if item.command:
            lines.append(f"Command: {item.command}")
        if item.exit_code is not None:
            lines.append(f"Exit code: {item.exit_code}")
        if item.runtime_ms is not None:
            lines.append(f"Runtime: {item.runtime_ms} ms")
        if item.stdout_path:
            stdout_text = ""
            try:
                stdout_text = Path(item.stdout_path).read_text(encoding="utf-8")
            except OSError:
                pass
            lines.append("--- stdout ---")
            lines.append(stdout_text.rstrip())
        if item.stderr_path:
            stderr_text = ""
            try:
                stderr_text = Path(item.stderr_path).read_text(encoding="utf-8")
            except OSError:
                pass
            lines.append("--- stderr ---")
            lines.append(stderr_text.rstrip())
        lines.append("")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_receipt(path: Path, project: str, team: str, task_id: str, result: RouteResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "total": len(result.items),
        "pass": sum(1 for i in result.items if i.result == "pass"),
        "fail": sum(1 for i in result.items if i.result == "fail"),
        "pending": sum(1 for i in result.items if i.result == "pending"),
    }
    payload = {
        "task_id": task_id,
        "project": project,
        "team": team,
        "ts": _utc_now(),
        "route": result.route,
        "verdict": result.verdict,
        "items": [i.to_dict() for i in result.items],
        "summary": summary,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def aggregate_verdict(results: dict[str, RouteResult]) -> str:
    """PASS only when all routes are PASS (or empty). FAIL on any route FAIL. PENDING otherwise.

    Priority: FAIL > PENDING > PASS. Mechanical FAIL blocks immediately. Reviewer or
    operator FAIL also blocks (e.g. CHANGES_REQUESTED reconciled into FAIL). PENDING
    when mechanical PASS but reviewer/operator work outstanding.
    """
    mech = results.get("mechanical")
    if mech is None or mech.verdict == "FAIL":
        return "FAIL"
    if any(r.verdict == "FAIL" for r in results.values()):
        return "FAIL"
    if any(r.verdict == "PENDING" for r in results.values()):
        return "PENDING"
    return "PASS"


# Reviewer verdicts that map to acceptance PASS
_REVIEWER_APPROVED_VERDICTS = frozenset({"APPROVED", "APPROVED_WITH_NITS"})
# Reviewer verdicts that map to acceptance FAIL (block parent aggregate)
_REVIEWER_BLOCKED_VERDICTS = frozenset({"FAIL", "CHANGES_REQUESTED", "REJECTED"})


def load_route_result_from_receipt(receipt_path: Path) -> RouteResult | None:
    """Load a RouteResult from a persisted JSON receipt. Returns None if missing/unreadable."""
    try:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    route = str(data.get("route", "reviewer"))
    verdict = str(data.get("verdict", "PENDING"))
    items = []
    for item_dict in data.get("items") or []:
        items.append(
            ItemResult(
                criterion=str(item_dict.get("criterion", "")),
                result=str(item_dict.get("result", "pending")),
                command=item_dict.get("command"),
                exit_code=item_dict.get("exit_code"),
                stdout_path=item_dict.get("stdout_path"),
                stderr_path=item_dict.get("stderr_path"),
                runtime_ms=item_dict.get("runtime_ms"),
                dispatch_receipt=item_dict.get("dispatch_receipt"),
            )
        )
    rr = RouteResult(route=route, verdict=verdict)
    rr.items = items
    return rr


def _find_accept_review_handoff(task_id: str, handoffs_dir: Path) -> dict | None:
    """Scan handoffs_dir for a reviewer completion receipt for {task_id}__accept_review.

    Returns the parsed JSON dict of the first matching completion handoff, or None.
    Matching pattern: {task_id}__accept_review__*__memory.json  (reviewer → memory).
    Receipt must have kind=completion and verdict present.
    """
    if not handoffs_dir.is_dir():
        return None
    prefix = f"{task_id}__accept_review__"
    suffix = "__memory.json"
    for entry in sorted(handoffs_dir.iterdir()):
        name = entry.name
        if not name.startswith(prefix) or not name.endswith(suffix):
            continue
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("kind") != "completion":
            continue
        verdict = data.get("verdict")
        if verdict and (
            verdict in _REVIEWER_APPROVED_VERDICTS or verdict in _REVIEWER_BLOCKED_VERDICTS
        ):
            return data
    return None


def reconcile_reviewer_acceptance(
    task_id: str,
    acceptance_dir: Path,
    handoffs_dir: Path | None = None,
    project: str | None = None,
) -> RouteResult | None:
    """Reconcile the reviewer section of the acceptance aggregate from a routed receipt.

    When the reviewer submits an __accept_review completion with APPROVED/APPROVED_WITH_NITS,
    the parent aggregate reviewer section should transition from PENDING to PASS.
    FAIL/CHANGES_REQUESTED/REJECTED keeps it blocked (verdict = FAIL).

    The reviewer receipt file is updated in-place; historical item data is preserved
    and reconciliation metadata is appended. Historical accept_review handoff files
    are never deleted.

    Returns the updated RouteResult if reconciliation happened, or None if:
    - no accept_review handoff found
    - reviewer section is already PASS or FAIL (nothing to reconcile)
    - reviewer receipt file missing
    """
    receipt_path = acceptance_dir / f"{task_id}__reviewer.json"
    route_result = load_route_result_from_receipt(receipt_path)
    if route_result is None:
        return None
    if route_result.verdict != "PENDING":
        return route_result

    # Determine handoffs directory
    if handoffs_dir is None and project is not None:
        handoffs_dir = _agents_root() / "tasks" / project / "patrol" / "handoffs"

    if handoffs_dir is None:
        return None

    handoff = _find_accept_review_handoff(task_id, handoffs_dir)
    if handoff is None:
        return None

    reviewer_verdict = str(handoff.get("verdict", ""))
    if reviewer_verdict in _REVIEWER_APPROVED_VERDICTS:
        new_verdict = "PASS"
    elif reviewer_verdict in _REVIEWER_BLOCKED_VERDICTS:
        new_verdict = "FAIL"
    else:
        return None

    # Update items: mark pending items as reconciled
    for item in route_result.items:
        if item.result == "pending":
            item.result = "pass" if new_verdict == "PASS" else "fail"

    route_result.verdict = new_verdict

    # Persist updated receipt, carrying reconciliation audit trail
    try:
        existing = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}

    existing["verdict"] = new_verdict
    existing["reconciled_at"] = _utc_now()
    existing["reconciled_verdict"] = reviewer_verdict
    existing["accept_review_handoff"] = str(handoff.get("task_id", f"{task_id}__accept_review"))
    existing["items"] = [i.to_dict() for i in route_result.items]
    existing["summary"] = {
        "total": len(route_result.items),
        "pass": sum(1 for i in route_result.items if i.result == "pass"),
        "fail": sum(1 for i in route_result.items if i.result == "fail"),
        "pending": sum(1 for i in route_result.items if i.result == "pending"),
    }

    receipt_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return route_result


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="ClawSeat v3 acceptance executor (mechanical/reviewer/operator routes)."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--team", required=True)
    parser.add_argument("--task-id", required=True, dest="task_id")
    parser.add_argument("--brief-path", default=None, dest="brief_path",
                        help="Explicit brief path (default: tasks/<p>/<t>/brief/<task_id>.md)")
    parser.add_argument("--reviewer-seat", default=None, dest="reviewer_seat")
    parser.add_argument("--cwd", default=None, help="Working dir for mechanical commands")
    parser.add_argument("--profile", default=None, dest="profile",
                        help="Optional profile path for reviewer dispatch (default: ~/.agents/profiles/<project>-profile-dynamic.toml)")
    args = parser.parse_args(argv)

    try:
        results = run_acceptance(
            project=args.project,
            team=args.team,
            task_id=args.task_id,
            brief_path=Path(args.brief_path) if args.brief_path else None,
            reviewer_seat=args.reviewer_seat,
            cwd=Path(args.cwd) if args.cwd else None,
            profile_path=Path(args.profile) if args.profile else None,
        )
    except AcceptanceError as exc:
        print(f"acceptance schema error: {exc}", file=sys.stderr)
        return 2

    verdict = aggregate_verdict(results)
    for route, r in results.items():
        passed = sum(1 for i in r.items if i.result == "pass")
        failed = sum(1 for i in r.items if i.result == "fail")
        pending = sum(1 for i in r.items if i.result == "pending")
        print(f"{route}: {r.verdict} (pass={passed} fail={failed} pending={pending})")
    print(f"aggregate: {verdict}")

    # Exit code mapping per planner-brief-parsing-contract.md §4
    if verdict == "FAIL":
        return 1
    return 0  # PASS or PENDING


if __name__ == "__main__":
    raise SystemExit(main())
