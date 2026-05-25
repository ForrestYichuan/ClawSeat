"""Round 4 strict-review regression tests for Phase 3 false-passes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))


@pytest.fixture
def env_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    return tmp_path


def _write_brief(tmp_path, frontmatter: str, project: str = "p", team: str = "t") -> Path:
    """Extract task_id from frontmatter so test call sites stay terse."""
    import re
    match = re.search(r"^task_id:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
    assert match, "frontmatter must contain task_id"
    task_id = match.group(1).strip().strip("'\"")
    d = tmp_path / ".agents" / "tasks" / project / team / "brief"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{task_id}.md"
    p.write_text(f"---\n{frontmatter}\n---\n", encoding="utf-8")
    return p


# ---------- A: route override actually batches into target route ----------


def test_round4A_mechanical_route_operator_lands_in_operator(env_home, tmp_path):
    from acceptance_executor import aggregate_verdict, run_acceptance

    _write_brief(env_home, """
task_id: T1
project: p
team: t
objective: route override
seats_required: [builder]
acceptance_criteria:
  mechanical:
    - "true"
    - {criterion: "review art quality", route: operator}
""")
    results = run_acceptance(project="p", team="t", task_id="T1")
    operator_criteria = [i.criterion for i in results["operator"].items]
    assert "review art quality" in operator_criteria, \
        f"operator-routed item missing from operator route; got {operator_criteria}"
    # Operator items pending → aggregate must be PENDING
    assert aggregate_verdict(results) == "PENDING"


def test_round4A_mechanical_route_reviewer_lands_in_reviewer(env_home, tmp_path):
    from acceptance_executor import run_acceptance

    captured = []

    def fake_dispatch(packet):
        captured.append(packet)
        return "dispatched: fake-seq"

    _write_brief(env_home, """
task_id: T2
project: p
team: t
objective: reviewer override
seats_required: [builder, reviewer]
acceptance_criteria:
  mechanical:
    - "true"
    - {criterion: "balance math audit", route: reviewer}
""")
    results = run_acceptance(project="p", team="t", task_id="T2", dispatch_fn=fake_dispatch)
    rev_criteria = [i.criterion for i in results["reviewer"].items]
    assert "balance math audit" in rev_criteria, \
        f"reviewer-routed item missing from reviewer route; got {rev_criteria}"
    # Dispatch should have been called with the rerouted item
    assert any("balance math audit" in p["items"] for p in captured), \
        f"dispatch_fn not invoked with routed item; called with {captured}"


# ---------- B: contract body in snapshot + diff ----------


def _make_runtime_dsl(path: Path, body: str = "") -> Path:
    path.write_text(
        "---\n"
        "contract_name: EffectExpression\n"
        "version: 1.0.0\n"
        "owner_team: core\n"
        "status: draft\n"
        "contract_type: dsl\n"
        "prototype_log:\n"
        "  - ts: '2026-05-14T00:00:00+00:00'\n"
        "    consumer_team: content\n"
        "    primitives_tested: [deal_damage]\n"
        "    verdict: sufficient\n"
        "sample_data:\n"
        "  - {primitive: deal_damage, value: 6}\n"
        "---\n"
        + body,
        encoding="utf-8",
    )
    return path


def test_round4B_publish_preserves_body(env_home, tmp_path):
    from contract_publish import publish_snapshot

    runtime = _make_runtime_dsl(
        tmp_path / "rt.yaml",
        body="message: this is the executable schema body\n",
    )
    snap = publish_snapshot("EffectExpression", "1.0.0", "p", runtime, consumers=["content"])
    snap_text = snap.read_text(encoding="utf-8")
    assert "this is the executable schema body" in snap_text, \
        "snapshot must include runtime body, not just frontmatter"


def test_round4B_drift_detects_body_change(env_home, tmp_path):
    from contract_drift_check import check_drift
    from contract_publish import publish_snapshot

    runtime = _make_runtime_dsl(
        tmp_path / "rt.yaml",
        body="message: original body\n",
    )
    snap = publish_snapshot("EffectExpression", "1.0.0", "p", runtime, consumers=["content"])

    # Mutate runtime body only
    text = runtime.read_text()
    runtime.write_text(text.replace("original body", "modified body"), encoding="utf-8")

    report = check_drift("EffectExpression", "1.0.0", "p",
                         runtime_path=runtime, snapshot_path=snap)
    assert not report.in_sync, "body mutation must surface as drift"
    assert any("_runtime_body" in d or "body" in d for d in report.drifts), \
        f"drift should mention body change; got {report.drifts}"


# ---------- C: fuzz requires explicit target_command ----------


def test_round4C_fuzz_missing_target_command_fails(env_home, tmp_path):
    from acceptance_executor import aggregate_verdict, run_acceptance

    _write_brief(env_home, """
task_id: TFUZ
project: p
team: t
objective: fuzz without target
seats_required: [builder]
acceptance_criteria:
  mechanical: ["true"]
fuzz_required: true
fuzz_spec:
  - name: empty_target
    generator: random_value
    type: int
    bounds: [0, 10]
    iterations: 5
    seed: 1
""")
    results = run_acceptance(project="p", team="t", task_id="TFUZ")
    assert results["mechanical"].verdict == "FAIL", \
        "fuzz with no target_command must FAIL mechanical"
    # Failure item should mention missing target
    assert any("target_command" in i.criterion for i in results["mechanical"].items if i.result == "fail"), \
        "mechanical FAIL must surface missing target_command reason"


def test_round4C_fuzz_with_real_target_passes(env_home, tmp_path):
    from acceptance_executor import run_acceptance

    _write_brief(env_home, """
task_id: TFUZ2
project: p
team: t
objective: fuzz with target
seats_required: [builder]
acceptance_criteria:
  mechanical: ["true"]
fuzz_required: true
fuzz_spec:
  - name: with_target
    generator: random_value
    type: int
    bounds: [0, 10]
    iterations: 3
    seed: 1
    target_command: "true"
""")
    results = run_acceptance(project="p", team="t", task_id="TFUZ2")
    assert results["mechanical"].verdict == "PASS"


# ---------- D: unquoted datetimes don't false-fail schema ----------


def test_round4D_drift_check_handles_unquoted_datetime(env_home, tmp_path):
    from contract_drift_check import check_drift

    # Unquoted published_ts — PyYAML auto-parses as datetime; must be stringified
    runtime = tmp_path / "Foo__v1.0.0.yaml"
    runtime.write_text(
        "---\n"
        "contract_name: Foo\n"
        "version: 1.0.0\n"
        "owner_team: core\n"
        "status: published\n"
        "published_ts: 2026-05-18T10:00:00+08:00\n"
        "consumers: [content]\n"
        "---\n",
        encoding="utf-8",
    )
    snap_dir = tmp_path / ".agents" / "tasks" / "p" / "contracts" / "Foo__v1.0.0"
    snap_dir.mkdir(parents=True)
    snap = snap_dir / "published.yaml"
    snap.write_text(runtime.read_text(), encoding="utf-8")

    # Should NOT raise schema violation; should return in_sync
    report = check_drift("Foo", "1.0.0", "p",
                         runtime_path=runtime, snapshot_path=snap)
    assert report.in_sync, f"unexpected drifts: {report.drifts}"


# ---------- Round 5: proto contract symmetric handling ----------


def _write_proto_runtime(path: Path, body: str = "syntax = \"proto3\";\nmessage Foo { string a = 1; }\n") -> Path:
    path.write_text(
        "// ---\n"
        "// contract_name: Foo\n"
        "// version: 1.0.0\n"
        "// owner_team: core\n"
        "// status: draft\n"
        "// ---\n"
        + body,
        encoding="utf-8",
    )
    return path


def test_round5_publish_accepts_proto(env_home, tmp_path):
    """publish_snapshot must handle .proto runtime files (// frontmatter)."""
    from contract_publish import publish_snapshot

    runtime = _write_proto_runtime(tmp_path / "Foo__v1.0.0.proto")
    snap = publish_snapshot("Foo", "1.0.0", "p", runtime, consumers=["content"])
    assert snap.exists()
    snap_text = snap.read_text(encoding="utf-8")
    # Frontmatter normalized into YAML; body preserved
    assert "contract_name:" in snap_text
    assert "message Foo" in snap_text, "proto body must be preserved in snapshot"


def test_round5_drift_detects_proto_body_change(env_home, tmp_path):
    """check_drift on .proto runtime must detect body changes."""
    from contract_drift_check import check_drift
    from contract_publish import publish_snapshot

    runtime = _write_proto_runtime(
        tmp_path / "Foo__v1.0.0.proto",
        body="syntax = \"proto3\";\nmessage Foo { string a = 1; }\n",
    )
    snap = publish_snapshot("Foo", "1.0.0", "p", runtime, consumers=["content"])

    # Confirm in_sync first
    report_pre = check_drift("Foo", "1.0.0", "p",
                              runtime_path=runtime, snapshot_path=snap)
    assert report_pre.in_sync, f"proto round-trip should be in_sync: {report_pre.drifts}"

    # Mutate proto body — only the type changes
    runtime.write_text(
        runtime.read_text().replace("string a = 1", "int32 a = 1"),
        encoding="utf-8",
    )
    report_post = check_drift("Foo", "1.0.0", "p",
                               runtime_path=runtime, snapshot_path=snap)
    assert not report_post.in_sync, \
        f"proto body change should drift; got drifts={report_post.drifts}"


def test_round4D_brief_unquoted_datetime_validates(env_home, tmp_path):
    """Brief with unquoted ISO datetime in created field must validate."""
    from acceptance_executor import run_acceptance

    # NOTE: yaml.safe_dump from our skeleton writer always quotes strings, so
    # the inbound brief from production never has unquoted datetimes. This
    # test exercises the validator with an externally-authored brief where
    # an operator wrote unquoted timestamps directly.
    _write_brief(env_home, """
task_id: TDT
project: p
team: t
created: 2026-05-18T10:00:00+08:00
created_by: memory
objective: dt test
seats_required: [builder]
acceptance_criteria:
  mechanical: ["true"]
""")
    # Should not raise schema violation despite unquoted created field
    results = run_acceptance(project="p", team="t", task_id="TDT")
    assert results["mechanical"].verdict == "PASS"
