"""Tests for core/lib/acceptance_executor.py — v3 Phase 2."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))

from acceptance_executor import (  # noqa: E402
    AcceptanceError,
    aggregate_verdict,
    route_operator,
    route_reviewer,
    run_acceptance,
    run_mechanical,
)


def _write_brief(tmp_path: Path, project: str, team: str, task_id: str, brief_yaml: str) -> Path:
    brief_dir = tmp_path / ".agents" / "tasks" / project / team / "brief"
    brief_dir.mkdir(parents=True, exist_ok=True)
    brief = brief_dir / f"{task_id}.md"
    brief.write_text(f"---\n{brief_yaml}\n---\n\n# body\n", encoding="utf-8")
    return brief


@pytest.fixture
def env_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    return tmp_path


def test_mechanical_pass(env_home, tmp_path):
    _write_brief(env_home, "p", "t", "T1", """
task_id: T1
project: p
team: t
objective: "smoke"
seats_required: [builder]
acceptance_criteria:
  mechanical:
    - "true"
    - "echo hello"
""")
    results = run_acceptance(project="p", team="t", task_id="T1")
    assert results["mechanical"].verdict == "PASS"
    assert len(results["mechanical"].items) == 2
    assert all(i.result == "pass" for i in results["mechanical"].items)
    assert aggregate_verdict(results) == "PASS"


def test_mechanical_fail_propagates(env_home, tmp_path):
    _write_brief(env_home, "p", "t", "T2", """
task_id: T2
project: p
team: t
objective: "fail test"
seats_required: [builder]
acceptance_criteria:
  mechanical:
    - "true"
    - "false"
""")
    results = run_acceptance(project="p", team="t", task_id="T2")
    assert results["mechanical"].verdict == "FAIL"
    assert aggregate_verdict(results) == "FAIL"


def test_mechanical_captures_stdout_stderr(env_home, tmp_path):
    _write_brief(env_home, "p", "t", "T3", """
task_id: T3
project: p
team: t
objective: "capture"
seats_required: [builder]
acceptance_criteria:
  mechanical:
    - "echo good-stdout && echo bad-stderr >&2"
""")
    results = run_acceptance(project="p", team="t", task_id="T3")
    item = results["mechanical"].items[0]
    assert item.result == "pass"
    assert Path(item.stdout_path).read_text().strip() == "good-stdout"
    assert Path(item.stderr_path).read_text().strip() == "bad-stderr"


def test_reviewer_route_writes_dispatch_packet(env_home, tmp_path):
    _write_brief(env_home, "p", "t", "T4", """
task_id: T4
project: p
team: t
objective: "review"
seats_required: [builder, reviewer]
acceptance_criteria:
  mechanical: ["true"]
  reviewer:
    - "check code style"
    - "verify balance math"
""")
    results = run_acceptance(project="p", team="t", task_id="T4")
    assert results["reviewer"].verdict == "PENDING"
    packet_path = env_home / ".agents" / "tasks" / "p" / "t" / "acceptance" / "T4__reviewer.dispatch.json"
    packet = json.loads(packet_path.read_text())
    assert packet["reviewer_seat"] == "t-reviewer"
    assert len(packet["items"]) == 2
    assert "check code style" in packet["items"]


def test_operator_route_writes_pending_file(env_home, tmp_path):
    _write_brief(env_home, "p", "t", "T5", """
task_id: T5
project: p
team: t
objective: "image audit"
seats_required: [designer-image]
acceptance_criteria:
  mechanical: ["true"]
  operator:
    - "operator confirms card art style matches STS aesthetic"
    - "operator confirms boss intent telegraphing is clear"
""")
    results = run_acceptance(project="p", team="t", task_id="T5")
    assert results["operator"].verdict == "PENDING"
    pending = json.loads(
        (env_home / ".agents" / "tasks" / "p" / "t" / "acceptance" / "T5__operator.pending.json").read_text()
    )
    assert len(pending["questions"]) == 2
    assert pending["questions"][0]["answer"] is None


def test_aggregate_pending_when_mechanical_pass_but_others_pending(env_home, tmp_path):
    _write_brief(env_home, "p", "t", "T6", """
task_id: T6
project: p
team: t
objective: "mixed"
seats_required: [builder, reviewer]
acceptance_criteria:
  mechanical: ["true"]
  reviewer: ["check x"]
  operator: ["check y"]
""")
    results = run_acceptance(project="p", team="t", task_id="T6")
    assert results["mechanical"].verdict == "PASS"
    assert aggregate_verdict(results) == "PENDING"


def test_empty_acceptance_now_fails_schema(env_home, tmp_path):
    """Empty acceptance still cannot vacuously PASS."""
    _write_brief(env_home, "p", "t", "T7", """
task_id: T7
project: p
team: t
objective: "docs only"
seats_required: [builder]
acceptance_criteria:
  mechanical: []
""")
    with pytest.raises(AcceptanceError, match="acceptance_criteria"):
        run_acceptance(project="p", team="t", task_id="T7")


def test_reviewer_only_acceptance_is_allowed_and_pending(env_home, tmp_path):
    _write_brief(env_home, "p", "t", "T7R", """
task_id: T7R
project: p
team: t
objective: "read-only review"
seats_required: [builder]
acceptance_criteria:
  mechanical: []
  reviewer:
    - "Delivery records inspected files and recommendation."
""")
    results = run_acceptance(project="p", team="t", task_id="T7R")
    assert results["mechanical"].verdict == "PASS"
    assert results["mechanical"].items == []
    assert results["reviewer"].verdict == "PENDING"
    assert aggregate_verdict(results) == "PENDING"


def test_missing_brief_raises(env_home, tmp_path):
    with pytest.raises(AcceptanceError, match="brief not found"):
        run_acceptance(project="p", team="t", task_id="DOES-NOT-EXIST")


def test_phase2A_rejects_brief_cli_mismatch(env_home, tmp_path):
    """Phase 2 fix #A: brief frontmatter must match CLI args."""
    _write_brief(env_home, "p", "t", "TMIS", """
task_id: OTHER
project: other-project
team: otherteam
objective: "mismatch test"
seats_required: [builder]
acceptance_criteria:
  mechanical: ["true"]
""")
    with pytest.raises(AcceptanceError, match="brief vs CLI mismatch"):
        run_acceptance(project="p", team="t", task_id="TMIS")


def test_phase2B_writes_consolidated_mechanical_log(env_home, tmp_path):
    """Phase 2 fix #B: __mechanical.log consolidated file written per spec §4.7.1."""
    _write_brief(env_home, "p", "t", "TLOG", """
task_id: TLOG
project: p
team: t
objective: "log test"
seats_required: [builder]
acceptance_criteria:
  mechanical:
    - "echo line1"
    - "false"
""")
    run_acceptance(project="p", team="t", task_id="TLOG")
    log_path = env_home / ".agents" / "tasks" / "p" / "t" / "acceptance" / "TLOG__mechanical.log"
    assert log_path.exists(), "consolidated mechanical.log must be written"
    content = log_path.read_text(encoding="utf-8")
    assert "Verdict: FAIL" in content
    assert "echo line1" in content
    assert "line1" in content  # captured stdout appears in consolidated log
    assert "Criterion #0" in content
    assert "Criterion #1" in content


def test_phase2C_reviewer_dispatch_called_when_items_present(env_home, tmp_path):
    """Phase 2 fix #C: reviewer route invokes dispatch_fn with packet."""
    _write_brief(env_home, "p", "t", "TREV", """
task_id: TREV
project: p
team: t
objective: "review dispatch"
seats_required: [reviewer]
acceptance_criteria:
  mechanical: ["true"]
  reviewer:
    - "audit balance"
""")
    captured = []

    def fake_dispatch(packet):
        captured.append(packet)
        return f"dispatched: fake-seq"

    results = run_acceptance(project="p", team="t", task_id="TREV", dispatch_fn=fake_dispatch)
    assert results["reviewer"].verdict == "PENDING"
    assert len(captured) == 1, "reviewer dispatch_fn must be invoked"
    assert captured[0]["task_id"] == "TREV"
    assert "audit balance" in captured[0]["items"]
    # Item dispatch_receipt records the call result
    assert "dispatched" in results["reviewer"].items[0].dispatch_receipt


def test_phase2C_reviewer_dispatch_skips_gracefully_no_profile(env_home, tmp_path):
    """When profile missing, executor must NOT hang; records skipped reason."""
    _write_brief(env_home, "p", "t", "TREVN", """
task_id: TREVN
project: p
team: t
objective: "no profile"
seats_required: [reviewer]
acceptance_criteria:
  mechanical: ["true"]
  reviewer: ["check"]
""")
    # Use default dispatch_fn (real one) — profile won't exist under tmp_path
    results = run_acceptance(project="p", team="t", task_id="TREVN")
    assert results["reviewer"].verdict == "PENDING"
    receipt = results["reviewer"].items[0].dispatch_receipt or ""
    assert "skipped" in receipt or "profile not found" in receipt, (
        f"expected graceful skip when profile missing, got: {receipt!r}"
    )


def test_receipts_persisted(env_home, tmp_path):
    _write_brief(env_home, "p", "t", "T8", """
task_id: T8
project: p
team: t
objective: "receipts"
seats_required: [builder]
acceptance_criteria:
  mechanical: ["true"]
""")
    run_acceptance(project="p", team="t", task_id="T8")
    acceptance_dir = env_home / ".agents" / "tasks" / "p" / "t" / "acceptance"
    assert (acceptance_dir / "T8__mechanical.json").exists()
    assert (acceptance_dir / "T8__reviewer.json").exists()
    assert (acceptance_dir / "T8__operator.json").exists()
    mech_receipt = json.loads((acceptance_dir / "T8__mechanical.json").read_text())
    assert mech_receipt["verdict"] == "PASS"
    assert mech_receipt["summary"]["pass"] == 1
