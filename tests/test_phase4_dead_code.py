"""Phase 4 §10 dead-code cleanup tests (high-risk items)."""

from __future__ import annotations

import io
import os
import sys
import types
from contextlib import redirect_stderr
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "skills" / "gstack-harness" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "core" / "scripts"))


# ---------- §10 item 6: complete_handoff branch_base soft-fail ----------


def test_branch_base_mismatch_no_longer_hard_fails():
    """Pre-fix: raised SystemExit on mismatch (AL-503 blocker). Post-fix:
    emits warning + sets lineage_status=divergent, returns silently."""
    from complete_handoff import _validate_completion_receipt

    receipt = {
        "branch_base": "AAA",       # mismatched
        "branch_tip": "BBB",
        "pr_number": 42,
        "ci_conclusion": "success",
        "lineage_status": "unknown",
    }
    source_dispatch = {"expected_base_sha": "ZZZ"}  # expected ≠ actual

    buf = io.StringIO()
    with redirect_stderr(buf):
        # Must NOT raise
        _validate_completion_receipt(receipt, source_dispatch)

    stderr_text = buf.getvalue()
    assert "branch_base mismatch" in stderr_text, \
        f"expected warning, got: {stderr_text!r}"
    # Receipt now records divergent lineage
    assert receipt["lineage_status"] == "divergent"
    assert receipt["head_contains_commit"] is False


def test_branch_base_match_silent_passthrough():
    """In-sync receipt continues to pass without noise."""
    from complete_handoff import _validate_completion_receipt

    receipt = {
        "branch_base": "AAA",
        "branch_tip": "BBB",
        "pr_number": 42,
        "ci_conclusion": "success",
        "lineage_status": "in-lineage",
    }
    source_dispatch = {"expected_base_sha": "AAA"}

    buf = io.StringIO()
    with redirect_stderr(buf):
        _validate_completion_receipt(receipt, source_dispatch)
    assert "mismatch" not in buf.getvalue()
    # Receipt untouched
    assert receipt["lineage_status"] == "in-lineage"


def test_branch_base_soft_fail_threads_into_pass_needs_integration():
    """Audit fix 2: when _validate_completion_receipt soft-fails branch_base,
    the divergent state must propagate to PASS_NEEDS_INTEGRATION notification
    AND to the notify message memory sees. Earlier bug: cached `lineage_status`
    captured at _annotate_lineage_status time bypassed the soft-fail downgrade.
    """
    # Inspect the script source directly — we can't easily simulate the full
    # main() invocation, but we can assert the downstream conditional uses
    # `final_lineage_status` (the post-soft-fail value) not `lineage_status`
    # (the pre-soft-fail cached value).
    src_path = REPO_ROOT / "core" / "skills" / "gstack-harness" / "scripts" / "complete_handoff.py"
    src = src_path.read_text(encoding="utf-8")

    # PASS_NEEDS_INTEGRATION emit gate must use final_*
    assert "if final_lineage_status == \"divergent\" and reported_commit and args.target != \"memory\":" in src, \
        "PASS_NEEDS_INTEGRATION emit must check final_lineage_status, not cached lineage_status"

    # Notify message append gate must also use final_*
    assert "if final_lineage_status == \"divergent\" and reported_commit:" in src, \
        "notify-message append must check final_lineage_status, not cached lineage_status"

    # Receipt persistence reads from the dict so the soft-fail mutation survives
    assert "final_lineage_status = str(receipt.get(\"lineage_status\") or lineage_status)" in src, \
        "receipt persistence must honor _validate_completion_receipt's mutation"


def test_phase4_dead_code_tests_are_ci_portable():
    text = Path(__file__).read_text(encoding="utf-8")
    hardcoded_checkout = "/Users/ywf/coding" + "/ClawSeat"
    assert hardcoded_checkout not in text


def test_missing_required_fields_still_hard_fail():
    """Missing branch_base/branch_tip/pr_number/ci_conclusion is a real bug.
    Soft-fail only applies to mismatched values, not missing fields."""
    from complete_handoff import _validate_completion_receipt

    receipt = {"branch_base": "AAA"}  # missing branch_tip, pr_number, ci_conclusion
    source_dispatch = {"expected_base_sha": "AAA"}

    with pytest.raises(SystemExit, match="closure receipt missing required fields"):
        _validate_completion_receipt(receipt, source_dispatch)


# ---------- §10 item 2: agent_admin task create deprecation ----------


def test_task_create_brief_driven_emits_deprecation_warning(tmp_path, monkeypatch):
    """v3 spec §10 item 2: brief-driven workflow_template warns toward
    agent_admin brief queue. Non-breaking — still creates the task dir."""
    from agent_admin_task import create_task

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)

    args = types.SimpleNamespace(
        task_id="T-DEP",
        project="p-dep",
        workflow_template="brief-driven",
    )
    buf = io.StringIO()
    with redirect_stderr(buf):
        rc = create_task(args)
    assert rc == 0  # non-breaking
    stderr_text = buf.getvalue()
    assert "deprecated" in stderr_text
    assert "brief queue" in stderr_text
    # Task dir still created
    assert (tmp_path / ".agents" / "tasks" / "p-dep" / "T-DEP" / "workflow.md").exists()


def test_task_create_non_brief_template_no_warning(tmp_path, monkeypatch):
    """Other templates (or empty) should not emit deprecation noise."""
    from agent_admin_task import create_task

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)

    args = types.SimpleNamespace(
        task_id="T-OK",
        project="p-ok",
        workflow_template="",  # default / unset
    )
    buf = io.StringIO()
    with redirect_stderr(buf):
        rc = create_task(args)
    assert rc == 0
    assert "deprecated" not in buf.getvalue()
