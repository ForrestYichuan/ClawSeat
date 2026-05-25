"""Phase 3 tests: contract drift / publish / queue_poll / fuzz harness / seat templates."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))
sys.path.insert(0, str(REPO_ROOT / "core" / "skills" / "planner" / "scripts"))

from contract_drift_check import DriftCheckError, check_drift  # noqa: E402
from contract_publish import ContractPublishError, publish_snapshot  # noqa: E402
from fuzz_harness import FuzzError, run_fuzz  # noqa: E402


@pytest.fixture
def env_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    return tmp_path


def _write_ready_brief(home: Path, project: str, team: str, task_id: str) -> None:
    brief = home / ".agents" / "tasks" / project / team / "brief" / f"{task_id}.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text(
        f"""---
task_id: {task_id}
project: {project}
team: {team}
objective: "queue poll ready brief"
seats_required: [builder]
acceptance_criteria:
  mechanical:
    - "true"
---

# Brief
""",
        encoding="utf-8",
    )


# ---------- Contract publish + drift ----------


def _runtime_dsl(path: Path) -> Path:
    path.write_text(
        """---
contract_name: EffectExpression
version: 1.0.0
owner_team: core
status: draft
contract_type: dsl
prototype_log:
  - {ts: '2026-05-14T00:00:00+00:00', consumer_team: content, primitives_tested: [deal_damage], verdict: sufficient}
sample_data:
  - {primitive: deal_damage, value: 6}
---
""",
        encoding="utf-8",
    )
    return path


def test_publish_writes_snapshot(env_home, tmp_path):
    runtime = _runtime_dsl(tmp_path / "rt.yaml")
    snap = publish_snapshot("EffectExpression", "1.0.0", "p", runtime, consumers=["content"])
    assert snap.exists()
    data = yaml.safe_load(snap.read_text(encoding="utf-8").split("---\n", 2)[1])
    assert data["status"] == "published"
    assert data["consumers"] == ["content"]
    assert (snap.parent / "consumer-pacts").is_dir()
    assert (snap.parent / "drift-checks").is_dir()
    assert (snap.parent / "review.md").exists()


def test_publish_rejects_mismatched_name(env_home, tmp_path):
    runtime = _runtime_dsl(tmp_path / "rt.yaml")
    with pytest.raises(ContractPublishError, match="contract_name"):
        publish_snapshot("WrongName", "1.0.0", "p", runtime)


def test_drift_in_sync_after_publish(env_home, tmp_path):
    runtime = _runtime_dsl(tmp_path / "rt.yaml")
    snap = publish_snapshot("EffectExpression", "1.0.0", "p", runtime, consumers=["content"])
    report = check_drift(
        "EffectExpression", "1.0.0", "p",
        runtime_path=runtime, snapshot_path=snap,
    )
    assert report.in_sync, f"unexpected drifts: {report.drifts}"


def test_drift_detected_on_substance_change(env_home, tmp_path):
    runtime = _runtime_dsl(tmp_path / "rt.yaml")
    # Post-retest #4: published snapshot needs `consumers` per schema if=status=published.
    snap = publish_snapshot("EffectExpression", "1.0.0", "p", runtime, consumers=["content"])
    # Mutate the actual schema substance
    txt = runtime.read_text()
    runtime.write_text(txt.replace("value: 6", "value: 99"))
    report = check_drift(
        "EffectExpression", "1.0.0", "p",
        runtime_path=runtime, snapshot_path=snap,
    )
    assert not report.in_sync
    assert any("value changed" in d for d in report.drifts), report.drifts


def test_drift_check_missing_snapshot_raises(env_home, tmp_path):
    runtime = _runtime_dsl(tmp_path / "rt.yaml")
    with pytest.raises(DriftCheckError, match="snapshot not found"):
        check_drift(
            "EffectExpression", "1.0.0", "p",
            runtime_path=runtime,
            snapshot_path=tmp_path / "does-not-exist.yaml",
        )


# ---------- Seat templates ----------


def test_seat_templates_exist_and_are_valid_yaml():
    template_dir = REPO_ROOT / "core" / "seat-templates"
    assert template_dir.is_dir()
    files = list(template_dir.glob("*.yaml"))
    assert len(files) >= 6, f"expected ≥6 seat templates, found {len(files)}"
    required_fields = {"template_name", "role", "tool", "provider", "auth_mode"}
    for f in files:
        text = f.read_text(encoding="utf-8")
        front = text.split("---\n", 2)[1] if text.startswith("---\n") else text
        data = yaml.safe_load(front)
        assert isinstance(data, dict), f"{f.name}: frontmatter not a mapping"
        missing = required_fields - data.keys()
        assert not missing, f"{f.name} missing {missing}"
        assert data["tool"] in ("claude", "codex", "gemini")
        assert data["auth_mode"] in ("oauth", "oauth_token", "api")


def test_seat_template_covers_all_v3_roles():
    template_dir = REPO_ROOT / "core" / "seat-templates"
    roles_present = set()
    for f in template_dir.glob("*.yaml"):
        data = yaml.safe_load(f.read_text(encoding="utf-8").split("---\n", 2)[1])
        roles_present.add(data["role"])
    # 6 minimum roles for v3 multi-team
    expected = {"planner", "builder", "reviewer", "patrol", "designer-creative", "designer-image"}
    missing = expected - roles_present
    assert not missing, f"templates missing roles: {missing}"


# ---------- Queue poll ----------


def test_queue_poll_claims_oldest_pending(env_home, tmp_path):
    from queue_io import append_event
    from queue_poll import poll_team

    queue = env_home / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    queue.parent.mkdir(parents=True)
    _write_ready_brief(env_home, "p", "t", "T1")
    _write_ready_brief(env_home, "p", "t", "T2")
    append_event(queue, {"event_type": "task_created", "actor": "memory",
                         "task_id": "T1", "brief_path": "brief/T1.md"})
    append_event(queue, {"event_type": "task_created", "actor": "memory",
                         "task_id": "T2", "brief_path": "brief/T2.md"})

    result = poll_team("p", "t", "planner@claude")
    assert result is not None
    assert result["task_id"] == "T1"  # oldest seq first
    assert result["verdict"] == "claimed"


def test_queue_poll_blocks_on_unmet_dependency(env_home, tmp_path):
    from queue_io import append_event
    from queue_poll import poll_team

    queue = env_home / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    queue.parent.mkdir(parents=True)
    _write_ready_brief(env_home, "p", "t", "UP")
    _write_ready_brief(env_home, "p", "t", "DOWN")
    append_event(queue, {"event_type": "task_created", "actor": "memory",
                         "task_id": "UP", "brief_path": "brief/UP.md"})
    append_event(queue, {"event_type": "task_created", "actor": "memory",
                         "task_id": "DOWN", "brief_path": "brief/DOWN.md",
                         "depends_on": ["UP"]})

    # First poll claims UP
    r1 = poll_team("p", "t", "planner@claude")
    assert r1["task_id"] == "UP"

    # Second poll: DOWN should hit waiting_for since UP not yet done
    r2 = poll_team("p", "t", "planner@claude")
    assert r2["task_id"] == "DOWN"
    assert r2["verdict"] == "waiting_for"


def test_queue_poll_retries_waiting_after_upstream_done(env_home, tmp_path):
    from queue_io import append_event
    from queue_poll import poll_team

    queue = env_home / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    queue.parent.mkdir(parents=True)
    _write_ready_brief(env_home, "p", "t", "UP")
    _write_ready_brief(env_home, "p", "t", "DOWN")
    # Pre-arrange: UP done, DOWN waiting_for
    append_event(queue, {"event_type": "task_created", "actor": "memory",
                         "task_id": "UP", "brief_path": "brief/UP.md"})
    append_event(queue, {"event_type": "task_claimed", "actor": "planner@claude", "task_id": "UP"})
    append_event(queue, {"event_type": "task_in_progress", "actor": "planner@claude", "task_id": "UP"})
    append_event(queue, {"event_type": "task_done", "actor": "memory", "task_id": "UP", "verdict": "PASS"})
    append_event(queue, {"event_type": "task_created", "actor": "memory",
                         "task_id": "DOWN", "brief_path": "brief/DOWN.md",
                         "depends_on": ["UP"]})
    append_event(queue, {"event_type": "task_waiting_for", "actor": "planner@claude",
                         "task_id": "DOWN", "waiting_for": "UP"})

    r = poll_team("p", "t", "planner@claude")
    assert r is not None
    assert r["task_id"] == "DOWN"
    assert r["verdict"] == "claimed_after_waiting"


# ---------- Fuzz harness ----------


def test_fuzz_expression_generator_deterministic_with_seed():
    spec = {"name": "test", "generator": "expression",
            "primitives": ["deal_damage", "apply_poison"],
            "max_depth": 2, "leaves": [1, 2, 3]}
    cases1, cases2 = [], []

    def collect1(p):
        cases1.append(p)

    def collect2(p):
        cases2.append(p)

    run_fuzz(spec, target_fn=collect1, iterations=10, seed=42)
    run_fuzz(spec, target_fn=collect2, iterations=10, seed=42)
    assert cases1 == cases2, "same seed must yield identical fuzz sequence"


def test_fuzz_detects_target_failures():
    spec = {"name": "fail", "generator": "random_value",
            "type": "int", "bounds": [0, 100]}

    def reject_even(p):
        if p % 2 == 0:
            raise RuntimeError(f"even value rejected: {p}")

    result = run_fuzz(spec, target_fn=reject_even, iterations=50, seed=7)
    assert result.cases_run == 50
    assert len(result.failures) > 0
    assert all("rejected" in f["error"] for f in result.failures)


def test_fuzz_combinatorial_picks_within_dimensions():
    spec = {"name": "combo", "generator": "combinatorial",
            "dimensions": {"card": ["strike", "defend"], "relic": ["bag", "sword"]}}
    payloads = []

    def collect(p):
        payloads.append(p)

    run_fuzz(spec, target_fn=collect, iterations=20, seed=1)
    for p in payloads:
        assert p["card"] in ("strike", "defend")
        assert p["relic"] in ("bag", "sword")


def test_fuzz_writes_log_when_out_dir_given(env_home, tmp_path):
    spec = {"name": "log_test", "generator": "random_value", "type": "int"}
    out_dir = tmp_path / "fuzz_logs"
    run_fuzz(spec, target_fn=lambda p: None, iterations=5, seed=1, out_dir=out_dir)
    logs = list(out_dir.glob("fuzz__log_test__*.json"))
    assert len(logs) == 1


def test_fuzz_rejects_unknown_generator():
    with pytest.raises(FuzzError, match="unknown generator"):
        run_fuzz({"generator": "wat"}, target_fn=lambda p: None, iterations=1)
