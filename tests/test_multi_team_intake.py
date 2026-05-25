from __future__ import annotations

import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "lib"))
sys.path.insert(0, str(REPO_ROOT / "core" / "scripts"))


def _approved_quality_docs_yaml(project: str = "p") -> str:
    return f"""---
project: {project}
team: quality-docs
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
team_type: quality-docs
planner_mode: quality_campaign
notify_policy: never_notify_memory
quality_gate_doc: quality-docs/QUALITY.md
autonomous: true
loop: continuous
stop_rule: campaign_clean_streak_3
seats:
  - role: planner
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: writes QA campaigns and docs
  - role: patrol
    instance: fast
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: runs deterministic smoke and targeted tests
  - role: patrol
    instance: human
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: simulates human product workflows
  - role: patrol
    instance: chaos
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: tests failure injection and recovery
estimated_monthly_cost_usd: {{ low: 0, high: 20 }}
---
"""


def _approved_subteam_yaml(project: str = "p") -> str:
    return f"""---
project: {project}
team: cartooner-front
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
team_type: subteam
planner_mode: delivery
notify_policy: queue_drained_only
ownership_paths:
  - apps/web/src/components/**
  - apps/web/src/store/**
scaling_policy:
  max_builders: 3
  reviewer_required_when_builders_gte: 2
  overflow_action: propose_new_subteam
  reviewer_fallback: planner
seats:
  - role: planner
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    rationale: plans front work
  - role: builder
    instance: core
    tool: codex
    provider: openai
    auth_mode: oauth
    purpose: front product surface generalist
    capabilities: [react, zustand, vibe-canvas]
    rationale: implements front work
estimated_monthly_cost_usd: {{ low: 0, high: 30 }}
---
"""


def _approved_lightweight_subteam_yaml(project: str = "p") -> str:
    return f"""---
project: {project}
team: creative-runtime
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
team_type: subteam
planner_mode: delivery
notify_policy: queue_drained_only
ownership_paths:
  - packages/runtime/**
review_model: planner_owned
dedicated_reviewer: false
scaling_policy:
  max_builders: 3
  reviewer_required_when_builders_gte: 2
  overflow_action: propose_new_subteam
  reviewer_fallback: planner
seats:
  - role: planner
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    rationale: plans and owns review for the lightweight runtime team
  - role: builder
    instance: runtime
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: implements runtime work
estimated_monthly_cost_usd: {{ low: 0, high: 30 }}
---
"""


def _approved_legacy_nested_review_subteam_yaml(project: str = "p") -> str:
    return _approved_lightweight_subteam_yaml(project).replace(
        "review_model: planner_owned\ndedicated_reviewer: false\n",
        """review_model:
  dedicated_reviewer: false
  planner_reviews: true
  escalation_review_path: quality-docs
""",
    )


def test_render_supports_named_same_role_instances(tmp_path: Path) -> None:
    from render_project_toml_v3 import render_project_toml_v3

    proposals = tmp_path / "_config-proposals"
    proposals.mkdir()
    (proposals / "quality-docs__approved.yaml").write_text(
        _approved_quality_docs_yaml(),
        encoding="utf-8",
    )

    data = tomllib.loads(render_project_toml_v3(project="p", proposals_dir=proposals))

    assert data["mode"]["project_memory"] == "memory"
    assert data["seat_roles"]["memory"] == "project-memory"
    assert data["seat_overrides"]["memory"]["tool"] == "codex"
    assert data["teams"]["quality-docs"]["autonomous"] is True
    assert data["teams"]["quality-docs"]["team_type"] == "quality-docs"
    assert data["teams"]["quality-docs"]["planner_mode"] == "quality_campaign"
    assert data["teams"]["quality-docs"]["notify_policy"] == "never_notify_memory"
    assert data["teams"]["quality-docs"]["quality_gate_doc"] == "quality-docs/QUALITY.md"
    assert data["teams"]["quality-docs"]["loop"] == "continuous"
    assert data["teams"]["quality-docs"]["stop_rule"] == "campaign_clean_streak_3"
    assert data["teams"]["quality-docs"]["seats"] == [
        "quality-docs-planner",
        "quality-docs-patrol-fast",
        "quality-docs-patrol-human",
        "quality-docs-patrol-chaos",
    ]
    assert data["seat_roles"]["quality-docs-patrol-fast"] == "patrol"
    assert data["seat_roles"]["quality-docs-patrol-human"] == "patrol"
    assert data["seat_overrides"]["quality-docs-patrol-chaos"]["instance"] == "chaos"


def test_render_preserves_subteam_scaling_and_ownership(tmp_path: Path) -> None:
    from render_project_toml_v3 import render_project_toml_v3

    proposals = tmp_path / "_config-proposals"
    proposals.mkdir()
    (proposals / "cartooner-front__approved.yaml").write_text(
        _approved_subteam_yaml(),
        encoding="utf-8",
    )

    data = tomllib.loads(render_project_toml_v3(project="p", proposals_dir=proposals))

    team = data["teams"]["cartooner-front"]
    assert team["team_type"] == "subteam"
    assert team["planner_mode"] == "delivery"
    assert team["notify_policy"] == "queue_drained_only"
    assert team["ownership_paths"] == [
        "apps/web/src/components/**",
        "apps/web/src/store/**",
    ]
    assert team["scaling_policy"] == {
        "max_builders": 3,
        "reviewer_required_when_builders_gte": 2,
        "overflow_action": "propose_new_subteam",
        "reviewer_fallback": "planner",
    }
    assert data["seat_overrides"]["cartooner-front-builder-core"]["instance"] == "core"
    assert data["seat_overrides"]["cartooner-front-builder-core"]["capabilities"] == [
        "react",
        "zustand",
        "vibe-canvas",
    ]


def test_render_supports_lightweight_planner_owned_review_subteam(tmp_path: Path) -> None:
    from proposal_validator import validate_proposal_file
    from render_project_toml_v3 import render_project_toml_v3

    proposals = tmp_path / "_config-proposals"
    proposals.mkdir()
    proposal = proposals / "creative-runtime__approved.yaml"
    proposal.write_text(_approved_lightweight_subteam_yaml(), encoding="utf-8")

    report = validate_proposal_file(proposal)
    assert report.ok, report.violations

    data = tomllib.loads(render_project_toml_v3(project="p", proposals_dir=proposals))
    team = data["teams"]["creative-runtime"]
    assert team["review_model"] == "planner_owned"
    assert team["dedicated_reviewer"] is False


def test_render_normalizes_legacy_nested_review_model_mapping(tmp_path: Path) -> None:
    from proposal_validator import validate_proposal_file
    from render_project_toml_v3 import render_project_toml_v3

    proposals = tmp_path / "_config-proposals"
    proposals.mkdir()
    proposal = proposals / "creative-runtime__approved.yaml"
    proposal.write_text(
        _approved_legacy_nested_review_subteam_yaml(),
        encoding="utf-8",
    )

    report = validate_proposal_file(proposal)
    assert report.ok

    data = tomllib.loads(render_project_toml_v3(project="p", proposals_dir=proposals))
    team = data["teams"]["creative-runtime"]
    assert team["review_model"] == "planner_owned"
    assert team["dedicated_reviewer"] is False
    assert "planner_reviews" not in team
    assert team["seats"] == [
        "creative-runtime-planner",
        "creative-runtime-builder-runtime",
    ]


def test_render_team_ownership_markdown_captures_quality_docs_and_lightweight_review(
    tmp_path: Path,
) -> None:
    from render_project_toml_v3 import (
        render_project_toml_v3,
        render_team_ownership_markdown,
    )

    proposals = tmp_path / "_config-proposals"
    proposals.mkdir()
    (proposals / "quality-docs__approved.yaml").write_text(
        _approved_quality_docs_yaml(),
        encoding="utf-8",
    )
    (proposals / "creative-runtime__approved.yaml").write_text(
        _approved_lightweight_subteam_yaml(),
        encoding="utf-8",
    )

    profile_text = render_project_toml_v3(project="p", proposals_dir=proposals)
    doc = render_team_ownership_markdown("p", tomllib.loads(profile_text))

    assert "# p Team Ownership" in doc
    assert "project.toml` / approved YAML remain runtime authority" in doc
    assert "status-gate-first and capability-second" in doc
    assert "team ownership is only a same-tier tie-breaker" in doc
    assert "## quality-docs" in doc
    assert "autonomous continuous QA" in doc
    assert "quality-docs-patrol-chaos" in doc
    assert "Does not edit product code" in doc
    assert "## creative-runtime" in doc
    assert "Review model: `planner_owned`" in doc
    assert "Planner mode: `delivery`" in doc
    assert "Notify policy: `queue_drained_only`" in doc
    assert "Quality gate doc: `quality-docs/QUALITY.md`" in doc
    assert "Planner owns review because this lightweight subteam has no dedicated reviewer" in doc
    assert "Declared paths guide routing but do not override planner-status" in doc


def test_validator_rejects_duplicate_same_role_without_instance(tmp_path: Path) -> None:
    from proposal_validator import validate_proposal_file

    proposal = tmp_path / "quality-docs__approved.yaml"
    proposal.write_text(
        """---
project: p
team: quality-docs
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
seats:
  - role: patrol
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: one
  - role: patrol
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: two
estimated_monthly_cost_usd: { low: 0, high: 20 }
---
""",
        encoding="utf-8",
    )

    report = validate_proposal_file(proposal)
    assert not report.ok
    assert any("duplicate role/instance identity" in v for v in report.violations)


def test_validator_rejects_bad_instance_name(tmp_path: Path) -> None:
    from proposal_validator import validate_proposal_file

    proposal = tmp_path / "quality-docs__approved.yaml"
    proposal.write_text(
        """---
project: p
team: quality-docs
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
seats:
  - role: patrol
    instance: "Fast Lane"
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: invalid
estimated_monthly_cost_usd: { low: 0, high: 20 }
---
""",
        encoding="utf-8",
    )

    report = validate_proposal_file(proposal)
    assert not report.ok
    assert any("instance='Fast Lane'" in v for v in report.violations)


def test_validator_requires_reviewer_for_multi_builder_subteam(tmp_path: Path) -> None:
    from proposal_validator import validate_proposal_file

    proposal = tmp_path / "front__approved.yaml"
    proposal.write_text(
        """---
project: p
team: front
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
team_type: subteam
ownership_paths: [apps/web/src/**]
scaling_policy:
  max_builders: 3
  reviewer_required_when_builders_gte: 2
  overflow_action: propose_new_subteam
  reviewer_fallback: planner
seats:
  - role: planner
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    rationale: plans
  - role: builder
    instance: app-shell
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: one
  - role: builder
    instance: canvas
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: two
estimated_monthly_cost_usd: { low: 0, high: 30 }
---
""",
        encoding="utf-8",
    )

    report = validate_proposal_file(proposal)
    assert not report.ok
    assert any("must declare a reviewer" in v for v in report.violations)


def test_validator_rejects_planner_owned_review_with_multiple_builders(tmp_path: Path) -> None:
    from proposal_validator import validate_proposal_file

    proposal = tmp_path / "front__approved.yaml"
    proposal.write_text(
        """---
project: p
team: front
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
team_type: subteam
ownership_paths: [apps/web/src/**]
review_model: planner_owned
dedicated_reviewer: false
scaling_policy:
  max_builders: 3
  reviewer_required_when_builders_gte: 2
  overflow_action: propose_new_subteam
  reviewer_fallback: planner
seats:
  - role: planner
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    rationale: plans
  - role: builder
    instance: one
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: one
  - role: builder
    instance: two
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: two
  - role: reviewer
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: reviews
estimated_monthly_cost_usd: { low: 0, high: 30 }
---
""",
        encoding="utf-8",
    )

    report = validate_proposal_file(proposal)
    assert not report.ok
    assert any("planner_owned" in v and "exactly one builder" in v for v in report.violations)


def test_validator_requires_explicit_planner_owned_pair_for_lightweight_review(tmp_path: Path) -> None:
    from proposal_validator import validate_proposal_file

    missing_model = tmp_path / "missing-model__approved.yaml"
    missing_model.write_text(
        _approved_lightweight_subteam_yaml().replace("review_model: planner_owned\n", ""),
        encoding="utf-8",
    )
    missing_flag = tmp_path / "missing-flag__approved.yaml"
    missing_flag.write_text(
        _approved_lightweight_subteam_yaml().replace("dedicated_reviewer: false\n", ""),
        encoding="utf-8",
    )

    report_model = validate_proposal_file(missing_model)
    report_flag = validate_proposal_file(missing_flag)

    assert not report_model.ok
    assert any("dedicated_reviewer=false requires review_model='planner_owned'" in v for v in report_model.violations)
    assert not report_flag.ok
    assert any("review_model='planner_owned' requires dedicated_reviewer=false" in v for v in report_flag.violations)


def test_validator_rejects_four_builder_subteam(tmp_path: Path) -> None:
    from proposal_validator import validate_proposal_file

    proposal = tmp_path / "front__approved.yaml"
    proposal.write_text(
        """---
project: p
team: front
proposal_status: approved
operator_approved_ts: 2026-05-14T00:00:00+00:00
team_type: subteam
ownership_paths: [apps/web/src/**]
scaling_policy:
  max_builders: 3
  reviewer_required_when_builders_gte: 2
  overflow_action: propose_new_subteam
  reviewer_fallback: planner
seats:
  - role: planner
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    rationale: plans
  - role: builder
    instance: one
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: one
  - role: builder
    instance: two
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: two
  - role: builder
    instance: three
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: three
  - role: builder
    instance: four
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: four
  - role: reviewer
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: reviews
estimated_monthly_cost_usd: { low: 0, high: 30 }
---
""",
        encoding="utf-8",
    )

    report = validate_proposal_file(proposal)
    assert not report.ok
    assert any("max is 3" in v for v in report.violations)


def test_validator_rejects_wrong_planner_mode_policy_combo(tmp_path: Path) -> None:
    from proposal_validator import validate_proposal_file

    subteam = tmp_path / "front__approved.yaml"
    subteam.write_text(
        _approved_subteam_yaml().replace("planner_mode: delivery", "planner_mode: quality_campaign"),
        encoding="utf-8",
    )
    quality = tmp_path / "quality-docs__approved.yaml"
    quality.write_text(
        _approved_quality_docs_yaml().replace("notify_policy: never_notify_memory", "notify_policy: queue_drained_only"),
        encoding="utf-8",
    )

    subteam_report = validate_proposal_file(subteam)
    quality_report = validate_proposal_file(quality)

    assert not subteam_report.ok
    assert any("subteam requires planner_mode='delivery'" in v for v in subteam_report.violations)
    assert not quality_report.ok
    assert any("quality-docs requires notify_policy='never_notify_memory'" in v for v in quality_report.violations)


def test_multi_team_intake_skill_documents_generic_quality_group() -> None:
    skill = REPO_ROOT / "core" / "skills" / "multi-team-intake" / "SKILL.md"
    reference = (
        REPO_ROOT
        / "core"
        / "skills"
        / "multi-team-intake"
        / "references"
        / "generic-project-group.md"
    )

    text = skill.read_text(encoding="utf-8")
    ref_text = reference.read_text(encoding="utf-8")

    assert "module/layer-based team topology" in text
    assert "reviewer_required_when_builders_gte" in text
    assert "propose_new_subteam" in text
    assert "capabilities" in text
    assert "owner_seat" in text
    assert "planner_mode: delivery" in text
    assert "notify_policy: queue_drained_only" in text
    assert "planner_mode: quality_campaign" in text
    assert "notify_policy: never_notify_memory" in text
    assert "QUALITY.md" in text
    assert "TEAM_OWNERSHIP.md" in text
    assert "not a second config source" in text
    assert "quality-docs" in text
    assert "campaign_clean_streak_3" in text
    assert "patrol-fast" in text and "patrol-human" in text and "patrol-chaos" in text
    assert "cartooner-front" in ref_text
    assert "cartooner-runtime-platform" in ref_text
    assert "cartooner-skills" in ref_text
    assert "product-surface" in ref_text
    assert "runtime-platform" in ref_text
    assert "orchestration-ops" in ref_text
    assert "planner does not maintain a second long-lived builder" in ref_text


def test_roster_admin_updates_team_ownership_doc() -> None:
    text = (
        REPO_ROOT
        / "core"
        / "skills"
        / "clawseat-roster-admin"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "TEAM_OWNERSHIP.md" in text
    assert "stable team responsibilities" in text
    assert "per-task assignments" in text
