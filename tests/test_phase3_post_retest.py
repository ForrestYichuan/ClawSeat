"""Phase 3 post-retest regression: 8 false-pass findings from strict review.

Each test reproduces the original bug then asserts the fix behavior.
Reviewer: codex strict retest after Phase 3 declared complete.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))
sys.path.insert(0, str(REPO_ROOT / "core" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "core" / "skills" / "planner" / "scripts"))


def _write_ready_brief_content(path: Path, *, project: str, team: str, task_id: str) -> None:
    path.write_text(
        f"""---
task_id: {task_id}
project: {project}
team: {team}
objective: ready acceptance
seats_required: [builder]
acceptance_criteria:
  mechanical:
    - "true"
---

# body
""",
        encoding="utf-8",
    )


def _write_ready_queue_brief(home: Path, *, project: str, team: str, task_id: str) -> None:
    brief = home / ".agents" / "tasks" / project / team / "brief" / f"{task_id}.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    _write_ready_brief_content(brief, project=project, team=team, task_id=task_id)


# ---------- #1: render output compatible with real load_profile ----------


def test_retest1_render_output_loads_via_real_harness(tmp_path, monkeypatch):
    from render_project_toml_v3 import render_project_toml_v3

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    proposals = tmp_path / "_config-proposals"
    proposals.mkdir()
    (proposals / "core__approved.yaml").write_text(
        """---
project: p
team: core
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
seats:
  - role: planner
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    model: claude-opus-4-7
    rationale: ok
estimated_monthly_cost_usd: { low: 1, high: 2 }
---
""",
        encoding="utf-8",
    )
    toml_text = render_project_toml_v3(project="p", proposals_dir=proposals)
    toml_path = tmp_path / "p-profile-dynamic.toml"
    toml_path.write_text(toml_text, encoding="utf-8")

    # Import the REAL runtime loader and verify it accepts the rendered file.
    sys.path.insert(0, str(REPO_ROOT / "core" / "skills" / "gstack-harness" / "scripts"))
    # _common is a package — make sure it imports cleanly
    from _common.profile import load_profile

    try:
        profile = load_profile(toml_path)
    except KeyError as exc:
        pytest.fail(f"runtime loader KeyError on rendered profile: {exc}")
    assert profile.project_name == "p"
    assert profile.profile_name == "p-profile-dynamic"
    assert profile.template_name == "clawseat-engineering"
    assert "memory" in profile.seats


# ---------- #2: brief schema enforced ----------


def _make_brief(tmp_path: Path, frontmatter: str, task_id: str = "T1",
                project: str = "p", team: str = "t") -> Path:
    d = tmp_path / ".agents" / "tasks" / project / team / "brief"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{task_id}.md"
    p.write_text(f"---\n{frontmatter}\n---\n", encoding="utf-8")
    return p


def test_retest2_empty_acceptance_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    from acceptance_executor import AcceptanceError, run_acceptance

    _make_brief(tmp_path, """
task_id: T1
project: p
team: t
objective: empty acceptance
seats_required: [builder]
acceptance_criteria:
  mechanical: []
""")
    with pytest.raises(AcceptanceError, match="acceptance_criteria"):
        run_acceptance(project="p", team="t", task_id="T1")


def test_retest2_brief_content_file_validated(tmp_path, monkeypatch):
    from agent_admin_brief import build_parser, cmd_queue

    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\ntask_id: WRONG\nproject: notp\nteam: nott\n"
        "seats_required: []\nacceptance_criteria:\n  mechanical: []\n---\n",
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args([
        "queue", "--project", "p", "--team", "t", "--task-id", "T1",
        "--objective", "x", "--brief-content-file", str(bad),
    ])
    rc = cmd_queue(args)
    assert rc == 2, "invalid brief content must be rejected"


# ---------- #3: fuzz_required wired into mechanical verdict ----------


def test_retest3_fuzz_required_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    from acceptance_executor import run_acceptance

    _make_brief(tmp_path, """
task_id: T1
project: p
team: t
objective: fuzz wired
seats_required: [builder]
acceptance_criteria:
  mechanical:
    - "true"
fuzz_required: true
fuzz_spec:
  - name: deterministic
    generator: random_value
    type: int
    bounds: [0, 10]
    iterations: 5
    seed: 1
""")
    results = run_acceptance(project="p", team="t", task_id="T1")
    # mechanical now has 1 original + 1 synthetic fuzz item
    item_strs = [i.criterion for i in results["mechanical"].items]
    assert any("fuzz" in s for s in item_strs), \
        f"fuzz item must be appended; got {item_strs}"


def test_retest3_fuzz_required_with_missing_spec_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    from acceptance_executor import run_acceptance

    _make_brief(tmp_path, """
task_id: T1
project: p
team: t
objective: fuzz missing spec
seats_required: [builder]
acceptance_criteria:
  mechanical: ["true"]
fuzz_required: true
""")
    results = run_acceptance(project="p", team="t", task_id="T1")
    assert results["mechanical"].verdict == "FAIL", \
        "fuzz_required without fuzz_spec must FAIL mechanical"


# ---------- #4: contract drift validates schema ----------


def test_retest4_dsl_missing_prototype_log_fails_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    from contract_drift_check import DriftCheckError, check_drift

    runtime = tmp_path / "EffectExpression__v1.0.0.yaml"
    runtime.write_text(
        """---
contract_name: EffectExpression
version: 1.0.0
owner_team: core
status: draft
contract_type: dsl
---
""",
        encoding="utf-8",
    )
    # Snapshot identical (would be in_sync without schema validation)
    snap_dir = tmp_path / ".agents" / "tasks" / "p" / "contracts" / "EffectExpression__v1.0.0"
    snap_dir.mkdir(parents=True)
    snap = snap_dir / "published.yaml"
    snap.write_text(runtime.read_text(), encoding="utf-8")

    with pytest.raises(DriftCheckError, match="prototype_log|sample_data|schema"):
        check_drift("EffectExpression", "1.0.0", "p",
                    runtime_path=runtime, snapshot_path=snap)


def test_retest4_proto_runtime_resolved(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    from contract_drift_check import check_drift

    runtime = tmp_path / "Foo__v1.0.0.proto"
    runtime.write_text(
        "// ---\n"
        "// contract_name: Foo\n"
        "// version: 1.0.0\n"
        "// owner_team: core\n"
        "// status: draft\n"
        "// ---\n"
        "syntax = \"proto3\";\n"
        "message Foo {}\n",
        encoding="utf-8",
    )
    snap_dir = tmp_path / ".agents" / "tasks" / "p" / "contracts" / "Foo__v1.0.0"
    snap_dir.mkdir(parents=True)
    snap = snap_dir / "published.yaml"
    snap.write_text(
        "---\n"
        "contract_name: Foo\n"
        "version: 1.0.0\n"
        "owner_team: core\n"
        "status: draft\n"
        "---\n"
        "syntax = \"proto3\";\n"
        "message Foo {}\n",
        encoding="utf-8",
    )
    report = check_drift("Foo", "1.0.0", "p",
                         runtime_path=runtime, snapshot_path=snap)
    assert report.in_sync, f"proto round-trip drift: {report.drifts}"


# ---------- #5: cross-team depends_on ----------


def test_retest5_cross_team_depends_unblocks_when_done(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    from agent_admin_brief import build_parser, cmd_claim, cmd_queue
    from queue_io import append_event

    parser = build_parser()
    # Upstream in core team
    core_brief = tmp_path / "core-up.md"
    down_brief = tmp_path / "con-down.md"
    _write_ready_brief_content(core_brief, project="p", team="core", task_id="CORE-UP")
    _write_ready_brief_content(down_brief, project="p", team="content", task_id="CON-DOWN")
    cmd_queue(parser.parse_args(["queue", "--project", "p", "--team", "core",
                                  "--task-id", "CORE-UP", "--objective", "u",
                                  "--brief-content-file", str(core_brief),
                                  "--no-wake"]))
    # Drive CORE-UP to done
    core_queue = tmp_path / ".agents" / "tasks" / "p" / "core" / "tasks.queue.jsonl"
    append_event(core_queue, {"event_type": "task_claimed", "actor": "planner@claude", "task_id": "CORE-UP"})
    append_event(core_queue, {"event_type": "task_in_progress", "actor": "planner@claude", "task_id": "CORE-UP"})
    append_event(core_queue, {"event_type": "task_done", "actor": "memory", "task_id": "CORE-UP", "verdict": "PASS"})

    # Downstream in content team with depends_on CORE-UP
    cmd_queue(parser.parse_args(["queue", "--project", "p", "--team", "content",
                                  "--task-id", "CON-DOWN", "--objective", "d",
                                  "--depends-on", "CORE-UP",
                                  "--brief-content-file", str(down_brief),
                                  "--no-wake"]))

    # Claim should succeed (cross-team resolution)
    rc = cmd_claim(parser.parse_args(["claim", "--project", "p", "--team", "content",
                                       "--task-id", "CON-DOWN", "--actor", "planner@claude"]))
    assert rc == 0, "claim must succeed when cross-team upstream is task_done"


def test_retest5_cross_team_blocks_when_upstream_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    from agent_admin_brief import build_parser, cmd_claim, cmd_queue

    parser = build_parser()
    core_brief = tmp_path / "core-up.md"
    down_brief = tmp_path / "con-down.md"
    _write_ready_brief_content(core_brief, project="p", team="core", task_id="CORE-UP")
    _write_ready_brief_content(down_brief, project="p", team="content", task_id="CON-DOWN")
    cmd_queue(parser.parse_args(["queue", "--project", "p", "--team", "core",
                                  "--task-id", "CORE-UP", "--objective", "u",
                                  "--brief-content-file", str(core_brief),
                                  "--no-wake"]))
    # Don't advance CORE-UP — still task_created
    cmd_queue(parser.parse_args(["queue", "--project", "p", "--team", "content",
                                  "--task-id", "CON-DOWN", "--objective", "d",
                                  "--depends-on", "CORE-UP",
                                  "--brief-content-file", str(down_brief),
                                  "--no-wake"]))
    rc = cmd_claim(parser.parse_args(["claim", "--project", "p", "--team", "content",
                                       "--task-id", "CON-DOWN", "--actor", "planner@claude"]))
    assert rc == 3, "claim must report waiting_for when cross-team upstream pending"


# ---------- #6: per-criterion route in mechanical ----------


def test_retest6_route_operator_actually_routes_not_skipped(tmp_path, monkeypatch):
    """Round 4 #A supersedes Round 3 #6: items with route override are not just
    skipped from mechanical — they MUST be batched into the target route.
    """
    monkeypatch.setenv("CLAWSEAT_REAL_HOME", str(tmp_path))
    from acceptance_executor import run_acceptance

    _make_brief(tmp_path, """
task_id: T1
project: p
team: t
objective: per-criterion route
seats_required: [builder]
acceptance_criteria:
  mechanical:
    - "true"
    - {criterion: "review art quality", route: operator}
""")
    results = run_acceptance(project="p", team="t", task_id="T1")
    # Item must appear in operator route, NOT in mechanical
    operator_items = [i.criterion for i in results["operator"].items]
    assert "review art quality" in operator_items, \
        f"operator-routed item must land in operator route; got {operator_items}"
    mechanical_items = [i.criterion for i in results["mechanical"].items]
    assert "review art quality" not in mechanical_items, \
        "operator-routed item must NOT remain in mechanical"


# ---------- #7: hook shape canonical ----------


def test_retest7_sessionstart_hook_uses_canonical_shape(tmp_path):
    from install_queue_poll import install_sessionstart_hook

    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings_path = install_sessionstart_hook(workspace, "p", "t", "claude")
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    ss = settings["hooks"]["SessionStart"]
    assert len(ss) >= 1
    entry = ss[0]
    # Canonical shape: matcher + nested hooks list
    assert "matcher" in entry, "missing 'matcher' field"
    assert "hooks" in entry, "missing nested 'hooks' list"
    assert isinstance(entry["hooks"], list)
    assert any(h.get("type") == "command" for h in entry["hooks"])


def test_queue_poll_uses_real_user_home_under_sandbox_home(tmp_path, monkeypatch):
    from queue_io import append_event, read_current_state
    from queue_poll import poll_team

    real_home = tmp_path / "real-home"
    sandbox_home = tmp_path / ".agent-runtime" / "identities" / "claude" / "home"
    queue = real_home / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl"
    _write_ready_queue_brief(real_home, project="p", team="t", task_id="Tpoll")
    append_event(
        queue,
        {
            "event_type": "task_created",
            "actor": "memory",
            "task_id": "Tpoll",
            "brief_path": "tasks/p/t/brief/Tpoll.md",
        },
    )
    monkeypatch.delenv("CLAWSEAT_REAL_HOME", raising=False)
    monkeypatch.setenv("HOME", str(sandbox_home))
    monkeypatch.setenv("AGENT_HOME", str(real_home))

    result = poll_team("p", "t", "planner@claude")

    assert result is not None
    assert result["verdict"] == "claimed"
    assert read_current_state(queue)["Tpoll"].status == "task_claimed"
    assert not (sandbox_home / ".agents" / "tasks" / "p" / "t" / "tasks.queue.jsonl").exists()


# ---------- #8: combinatorial coverage ----------


def test_retest8_combinatorial_covers_full_cartesian(tmp_path):
    from fuzz_harness import run_fuzz

    spec = {
        "name": "covtest",
        "generator": "combinatorial",
        "dimensions": {"a": [1, 2], "b": ["x", "y"]},
    }
    payloads = []

    def collect(p):
        payloads.append(json.dumps(p, sort_keys=True))

    result = run_fuzz(spec, target_fn=collect, iterations=4, seed=1)
    unique = set(payloads)
    assert len(unique) == 4, (
        f"Cartesian coverage failed — expected 4 unique payloads (2x2), "
        f"got {len(unique)}: {sorted(unique)}"
    )
    assert result.unique_payloads == 4


def test_retest8_combinatorial_sample_mode_explicit(tmp_path):
    from fuzz_harness import run_fuzz

    spec = {
        "name": "sample_explicit",
        "generator": "combinatorial",
        "dimensions": {"a": [1, 2], "b": ["x", "y"]},
        "combinatorial_mode": "sample",
    }
    payloads = []
    run_fuzz(spec, target_fn=lambda p: payloads.append(json.dumps(p, sort_keys=True)),
             iterations=4, seed=1)
    # In sample mode, no coverage guarantee — may have duplicates
    # Just verify it didn't crash and produced 4 items
    assert len(payloads) == 4


def test_retest8_iterations_lower_than_product_uses_sampling(tmp_path):
    from fuzz_harness import run_fuzz

    spec = {
        "name": "underbudget",
        "generator": "combinatorial",
        "dimensions": {"a": [1, 2, 3], "b": [10, 20, 30]},  # 9 combos
    }
    payloads = []
    run_fuzz(spec, target_fn=lambda p: payloads.append(p),
             iterations=3, seed=1)
    # When iterations < product size, falls back to random sampling — no coverage guarantee
    assert len(payloads) == 3
