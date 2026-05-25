"""Tests for cf013: acceptance review aggregate reconciliation.

When a reviewer submits an __accept_review completion with APPROVED or
APPROVED_WITH_NITS, the parent task's reviewer section must transition from
PENDING to PASS (not require manual memory override).

FAIL/CHANGES_REQUESTED/REJECTED must still block the parent aggregate.
Historical acceptance and handoff receipts must remain append-only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))

from acceptance_executor import (  # noqa: E402
    RouteResult,
    aggregate_verdict,
    load_route_result_from_receipt,
    reconcile_reviewer_acceptance,
    run_acceptance,
)


@pytest.fixture
def env_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    return tmp_path


def _write_brief(env_home: Path, project: str, team: str, task_id: str) -> Path:
    brief_dir = env_home / ".agents" / "tasks" / project / team / "brief"
    brief_dir.mkdir(parents=True, exist_ok=True)
    brief = brief_dir / f"{task_id}.md"
    brief.write_text(
        f"---\n"
        f"task_id: {task_id}\n"
        f"project: {project}\n"
        f"team: {team}\n"
        f"objective: reconcile test\n"
        f"seats_required: [builder, reviewer]\n"
        f"acceptance_criteria:\n"
        f"  mechanical:\n"
        f"    - 'true'\n"
        f"  reviewer:\n"
        f"    - 'check code quality'\n"
        f"---\n\n# Brief\n",
        encoding="utf-8",
    )
    return brief


def _write_accept_review_handoff(
    handoffs_dir: Path,
    task_id: str,
    reviewer_seat: str,
    verdict: str,
    summary: str = "reviewed",
) -> Path:
    """Write a fake reviewer → memory completion handoff receipt."""
    handoffs_dir.mkdir(parents=True, exist_ok=True)
    name = f"{task_id}__accept_review__{reviewer_seat}__memory.json"
    path = handoffs_dir / name
    path.write_text(
        json.dumps({
            "kind": "completion",
            "task_id": f"{task_id}__accept_review",
            "source": reviewer_seat,
            "target": "memory",
            "status": "pass",
            "verdict": verdict,
            "summary": summary,
        }, indent=2),
        encoding="utf-8",
    )
    return path


def _acceptance_dir(env_home: Path, project: str, team: str) -> Path:
    return env_home / ".agents" / "tasks" / project / team / "acceptance"


def _handoffs_dir(env_home: Path, project: str) -> Path:
    return env_home / ".agents" / "tasks" / project / "patrol" / "handoffs"


# ---------------------------------------------------------------------------
# Test 1: APPROVED reviewer receipt → aggregate reconciled PENDING → PASS
# ---------------------------------------------------------------------------

def test_approved_reviewer_reconciles_aggregate(env_home):
    project, team, task_id = "p", "t", "TAPPRV"
    _write_brief(env_home, project, team, task_id)

    # Run acceptance: mechanical PASS, reviewer dispatched → PENDING
    results = run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")
    assert results["reviewer"].verdict == "PENDING"
    assert aggregate_verdict(results) == "PENDING"

    # Reviewer submits APPROVED completion handoff
    handoffs = _handoffs_dir(env_home, project)
    _write_accept_review_handoff(handoffs, task_id, "t-reviewer", "APPROVED")

    # Reconcile
    acc_dir = _acceptance_dir(env_home, project, team)
    updated = reconcile_reviewer_acceptance(task_id, acc_dir, handoffs_dir=handoffs)
    assert updated is not None
    assert updated.verdict == "PASS"

    # Reload from disk and verify aggregate
    reviewer_rr = load_route_result_from_receipt(acc_dir / f"{task_id}__reviewer.json")
    assert reviewer_rr is not None
    assert reviewer_rr.verdict == "PASS"

    mech_rr = load_route_result_from_receipt(acc_dir / f"{task_id}__mechanical.json")
    assert mech_rr is not None
    assert mech_rr.verdict == "PASS"

    # With reconciled reviewer, aggregate should be PASS
    reconciled_results = {
        "mechanical": mech_rr,
        "reviewer": reviewer_rr,
        "operator": RouteResult(route="operator", verdict="PASS"),
    }
    assert aggregate_verdict(reconciled_results) == "PASS"


def test_approved_with_nits_also_reconciles_to_pass(env_home):
    project, team, task_id = "p", "t", "TAPPRN"
    _write_brief(env_home, project, team, task_id)
    run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")

    handoffs = _handoffs_dir(env_home, project)
    _write_accept_review_handoff(handoffs, task_id, "t-reviewer", "APPROVED_WITH_NITS")

    acc_dir = _acceptance_dir(env_home, project, team)
    updated = reconcile_reviewer_acceptance(task_id, acc_dir, handoffs_dir=handoffs)
    assert updated is not None
    assert updated.verdict == "PASS"


# ---------------------------------------------------------------------------
# Test 2: FAIL/CHANGES_REQUESTED → parent aggregate still blocked
# ---------------------------------------------------------------------------

def test_fail_verdict_keeps_reviewer_blocked(env_home):
    project, team, task_id = "p", "t", "TFAIL"
    _write_brief(env_home, project, team, task_id)
    run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")

    handoffs = _handoffs_dir(env_home, project)
    _write_accept_review_handoff(handoffs, task_id, "t-reviewer", "FAIL")

    acc_dir = _acceptance_dir(env_home, project, team)
    updated = reconcile_reviewer_acceptance(task_id, acc_dir, handoffs_dir=handoffs)
    assert updated is not None
    assert updated.verdict == "FAIL"

    reviewer_rr = load_route_result_from_receipt(acc_dir / f"{task_id}__reviewer.json")
    assert reviewer_rr is not None
    assert reviewer_rr.verdict == "FAIL"

    mech_rr = load_route_result_from_receipt(acc_dir / f"{task_id}__mechanical.json")
    reconciled = {
        "mechanical": mech_rr,
        "reviewer": reviewer_rr,
        "operator": RouteResult(route="operator", verdict="PASS"),
    }
    assert aggregate_verdict(reconciled) == "FAIL"


def test_changes_requested_verdict_keeps_reviewer_blocked(env_home):
    project, team, task_id = "p", "t", "TCHANGES"
    _write_brief(env_home, project, team, task_id)
    run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")

    handoffs = _handoffs_dir(env_home, project)
    _write_accept_review_handoff(handoffs, task_id, "t-reviewer", "CHANGES_REQUESTED")

    acc_dir = _acceptance_dir(env_home, project, team)
    updated = reconcile_reviewer_acceptance(task_id, acc_dir, handoffs_dir=handoffs)
    assert updated is not None
    assert updated.verdict == "FAIL"


def test_rejected_verdict_keeps_reviewer_blocked(env_home):
    project, team, task_id = "p", "t", "TREJECT"
    _write_brief(env_home, project, team, task_id)
    run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")

    handoffs = _handoffs_dir(env_home, project)
    _write_accept_review_handoff(handoffs, task_id, "t-reviewer", "REJECTED")

    acc_dir = _acceptance_dir(env_home, project, team)
    updated = reconcile_reviewer_acceptance(task_id, acc_dir, handoffs_dir=handoffs)
    assert updated is not None
    assert updated.verdict == "FAIL"


# ---------------------------------------------------------------------------
# Test 3: Historical receipts preserved (append-only, no deletion)
# ---------------------------------------------------------------------------

def test_historical_handoff_files_preserved_after_reconciliation(env_home):
    project, team, task_id = "p", "t", "THIST"
    _write_brief(env_home, project, team, task_id)
    run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")

    handoffs = _handoffs_dir(env_home, project)
    handoff_path = _write_accept_review_handoff(handoffs, task_id, "t-reviewer", "APPROVED")

    acc_dir = _acceptance_dir(env_home, project, team)
    reconcile_reviewer_acceptance(task_id, acc_dir, handoffs_dir=handoffs)

    # Original handoff still exists unchanged
    assert handoff_path.exists(), "accept_review handoff must not be deleted"
    original = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert original["kind"] == "completion"
    assert original["verdict"] == "APPROVED"


def test_reviewer_receipt_carries_reconciliation_metadata(env_home):
    project, team, task_id = "p", "t", "TMETA"
    _write_brief(env_home, project, team, task_id)
    run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")

    handoffs = _handoffs_dir(env_home, project)
    _write_accept_review_handoff(handoffs, task_id, "t-reviewer", "APPROVED_WITH_NITS", "LGTM")

    acc_dir = _acceptance_dir(env_home, project, team)
    reconcile_reviewer_acceptance(task_id, acc_dir, handoffs_dir=handoffs)

    receipt = json.loads((acc_dir / f"{task_id}__reviewer.json").read_text(encoding="utf-8"))
    assert receipt["verdict"] == "PASS"
    assert "reconciled_at" in receipt
    assert receipt["reconciled_verdict"] == "APPROVED_WITH_NITS"
    assert "accept_review_handoff" in receipt


# ---------------------------------------------------------------------------
# Test 4: No-op cases (missing handoff, already resolved)
# ---------------------------------------------------------------------------

def test_no_handoff_returns_none(env_home):
    project, team, task_id = "p", "t", "TNOH"
    _write_brief(env_home, project, team, task_id)
    run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")

    acc_dir = _acceptance_dir(env_home, project, team)
    handoffs = _handoffs_dir(env_home, project)
    handoffs.mkdir(parents=True, exist_ok=True)

    result = reconcile_reviewer_acceptance(task_id, acc_dir, handoffs_dir=handoffs)
    assert result is None


def test_already_pass_not_overwritten(env_home):
    project, team, task_id = "p", "t", "TALP"
    _write_brief(env_home, project, team, task_id)
    run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")

    handoffs = _handoffs_dir(env_home, project)
    _write_accept_review_handoff(handoffs, task_id, "t-reviewer", "APPROVED")

    acc_dir = _acceptance_dir(env_home, project, team)
    # First reconcile
    r1 = reconcile_reviewer_acceptance(task_id, acc_dir, handoffs_dir=handoffs)
    assert r1 is not None and r1.verdict == "PASS"

    # Second reconcile: already PASS, returns it unchanged
    r2 = reconcile_reviewer_acceptance(task_id, acc_dir, handoffs_dir=handoffs)
    assert r2 is not None and r2.verdict == "PASS"


# ---------------------------------------------------------------------------
# Test 5: CLI-level regression — run_acceptance() auto-reconciles on re-run
# ---------------------------------------------------------------------------

def test_run_acceptance_auto_reconciles_on_second_call(env_home):
    """run_acceptance() must auto-reconcile reviewer from __accept_review handoff.

    Reproduces the reviewer finding: second call still returned PENDING before fix.
    """
    project, team, task_id = "p", "t", "TCLI1"
    _write_brief(env_home, project, team, task_id)

    # First run: reviewer dispatched, aggregate PENDING
    results1 = run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")
    assert results1["reviewer"].verdict == "PENDING"
    assert aggregate_verdict(results1) == "PENDING"

    # Reviewer submits APPROVED handoff
    handoffs = _handoffs_dir(env_home, project)
    _write_accept_review_handoff(handoffs, task_id, "t-reviewer", "APPROVED")

    # Second run: run_acceptance() must auto-reconcile reviewer → PASS
    results2 = run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")
    assert results2["reviewer"].verdict == "PASS", (
        "run_acceptance() must auto-reconcile APPROVED __accept_review handoff; "
        f"got reviewer.verdict={results2['reviewer'].verdict!r}"
    )
    assert aggregate_verdict(results2) == "PASS"


def test_run_acceptance_auto_reconciles_fail_on_second_call(env_home):
    """run_acceptance() must also reconcile CHANGES_REQUESTED → FAIL verdict."""
    project, team, task_id = "p", "t", "TCLI2"
    _write_brief(env_home, project, team, task_id)

    run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")

    handoffs = _handoffs_dir(env_home, project)
    _write_accept_review_handoff(handoffs, task_id, "t-reviewer", "CHANGES_REQUESTED")

    results2 = run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")
    assert results2["reviewer"].verdict == "FAIL"
    assert aggregate_verdict(results2) == "FAIL"


def test_run_acceptance_first_call_with_existing_handoff_reconciles(env_home):
    """If __accept_review handoff exists before first run, first call must reconcile."""
    project, team, task_id = "p", "t", "TCLI3"
    _write_brief(env_home, project, team, task_id)

    # Pre-create APPROVED handoff before any acceptance run
    handoffs = _handoffs_dir(env_home, project)
    _write_accept_review_handoff(handoffs, task_id, "t-reviewer", "APPROVED")

    results = run_acceptance(project=project, team=team, task_id=task_id, dispatch_fn=lambda p: "fake")
    assert results["reviewer"].verdict == "PASS"
    assert aggregate_verdict(results) == "PASS"
