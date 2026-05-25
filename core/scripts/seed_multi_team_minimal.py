#!/usr/bin/env python3
"""Seed approved v3 proposals for the MULTI_TEAM_MINIMAL template.

This is the compatibility bridge for the old ``clawseat-solo`` entry point:
solo is no longer a standalone single-mode runtime. It is the minimal dev
subteam archetype inside a v3 multi-team project group.

Four hot-pluggable subgroup profiles are supported via --profile:
  dev-minimal  (default): planner + builder, planner self-reviews
  dev-standard          : planner + 2 builders + reviewer, reviewer gate required
  test                  : planner + patrol, QA-only, no product code edits by default
  planner-only          : memory + planner(s) only, no builder; planner self-contains
                          diagnosis, planning, implementation, testing, and closeout

All profiles inherit: local review/latest validation, push/PR/CI opt-in only,
OpenClaw/Koder/Feishu/Lark as optional adapters, hot-plug without history loss.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

PROFILES_NOTE = (
    "Subgroup profiles: dev-minimal (planner + builder, default), "
    "dev-standard (planner + 2 builders + reviewer), "
    "test (planner + patrol), "
    "planner-only (planner self-contains all engineering work; no builder). "
    "All inherit: local review/latest validation, push/PR/CI opt-in only, "
    "OpenClaw/Koder/Feishu/Lark as optional adapters, hot-plug without history loss."
)

VALID_PROFILES = ("dev-minimal", "dev-standard", "test", "planner-only")


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _yaml_list(items: list[str], *, indent: str = "  ") -> str:
    return "\n".join(f"{indent}- {_quote(item)}" for item in items)


def _header(project: str, team: str, ts: str) -> list[str]:
    return [
        "---",
        f"project: {project}",
        f"team: {team}",
        "proposal_status: approved",
        f"operator_approved_ts: {ts}",
    ]


def _subteam_yaml(project: str, team: str, ts: str, ownership_paths: list[str]) -> str:
    """Dev-minimal: planner + builder, planner_owned review. Default profile."""
    lines = _header(project, team, ts)
    lines.extend(
        [
            "# dev-minimal: planner + builder (default subgroup profile)",
            "subgroup_profile: dev-minimal",
            "team_type: subteam",
            "planner_mode: delivery",
            "notify_policy: queue_drained_only",
            "review_model: planner_owned",
            "dedicated_reviewer: false",
            "ownership_paths:",
            _yaml_list(ownership_paths),
            "scaling_policy:",
            "  max_builders: 3",
            "  reviewer_required_when_builders_gte: 2",
            "  overflow_action: propose_new_subteam",
            "  reviewer_fallback: planner",
            "seats:",
            "  - role: planner",
            "    tool: claude",
            "    provider: anthropic",
            "    auth_mode: oauth_token",
            f"    rationale: {_quote('claims this team queue, writes workflow.md, dispatches the minimal solo unit')}",
            "  - role: builder",
            "    instance: core",
            "    tool: codex",
            "    provider: openai",
            "    auth_mode: oauth",
            f"    purpose: {_quote('minimal implementation builder for this module/layer')}",
            "    capabilities: [implementation, tests, docs]",
            f"    rationale: {_quote('implements planner-assigned changes and returns DELIVERY.md evidence')}",
            "estimated_monthly_cost_usd: { low: 0, high: 30 }",
            "---",
            "",
        ]
    )
    return "\n".join(lines)


def _dev_minimal_yaml(project: str, team: str, ts: str, ownership_paths: list[str]) -> str:
    """Explicit dev-minimal profile: same as _subteam_yaml (for clarity in dispatch)."""
    return _subteam_yaml(project, team, ts, ownership_paths)


def _dev_standard_yaml(project: str, team: str, ts: str, ownership_paths: list[str]) -> str:
    """Dev-standard: planner + 2 builders + reviewer. Reviewer gate required before closeout."""
    lines = _header(project, team, ts)
    lines.extend(
        [
            "# dev-standard: planner + 2 builders + reviewer",
            "subgroup_profile: dev-standard",
            "team_type: subteam",
            "planner_mode: delivery",
            "notify_policy: queue_drained_only",
            "ownership_paths:",
            _yaml_list(ownership_paths),
            "scaling_policy:",
            "  max_builders: 3",
            "  reviewer_required_when_builders_gte: 2",
            "  overflow_action: propose_new_subteam",
            "  reviewer_fallback: planner",
            "seats:",
            "  - role: planner",
            "    tool: claude",
            "    provider: anthropic",
            "    auth_mode: oauth_token",
            f"    rationale: {_quote('owns decomposition, assigns disjoint scopes to builders, does fan-in, final closeout')}",
            "  - role: builder",
            "    instance: primary",
            "    tool: codex",
            "    provider: openai",
            "    auth_mode: oauth",
            f"    purpose: {_quote('primary builder — owns first disjoint write scope')}",
            "    capabilities: [implementation, tests, docs]",
            f"    rationale: {_quote('implements planner-assigned scope and returns DELIVERY.md evidence')}",
            "  - role: builder",
            "    instance: secondary",
            "    tool: codex",
            "    provider: openai",
            "    auth_mode: oauth",
            f"    purpose: {_quote('secondary builder — owns second disjoint write scope')}",
            "    capabilities: [implementation, tests, docs]",
            f"    rationale: {_quote('implements parallel planner-assigned scope and returns DELIVERY.md evidence')}",
            "  - role: reviewer",
            "    tool: codex",
            "    provider: openai",
            "    auth_mode: oauth",
            f"    rationale: {_quote('independent reviewer gate required before planner final closeout')}",
            "estimated_monthly_cost_usd: { low: 0, high: 60 }",
            "---",
            "",
        ]
    )
    return "\n".join(lines)


def _test_group_yaml(project: str, team: str, ts: str) -> str:
    """Test group: planner + patrol. QA/evidence only. No product code edits by default."""
    lines = _header(project, team, ts)
    lines.extend(
        [
            "# test group: planner + patrol (QA-only, no product code edits by default)",
            "subgroup_profile: test",
            "team_type: quality-docs",
            "planner_mode: quality_campaign",
            "notify_policy: never_notify_memory",
            "autonomous: false",
            "quality_gate_doc: quality-docs/QUALITY.md",
            "seats:",
            "  - role: planner",
            "    tool: claude",
            "    provider: anthropic",
            "    auth_mode: oauth_token",
            f"    rationale: {_quote('designs test campaigns, assigns patrol, fans in evidence, manages QUALITY.md')}",
            "  - role: patrol",
            "    instance: human",
            "    tool: claude",
            "    provider: anthropic",
            "    auth_mode: oauth_token",
            f"    rationale: {_quote('executes QA/smoke/reproduction checks; does not edit product code by default')}",
            "estimated_monthly_cost_usd: { low: 0, high: 15 }",
            "---",
            "",
        ]
    )
    return "\n".join(lines)


def _planner_only_yaml(
    project: str,
    team: str,
    ts: str,
    ownership_paths: list[str],
    planner_count: int = 1,
) -> str:
    """Planner-only: one or more planners, no builder, planner self-reviews.

    Each planner seat is self-contained: it diagnoses root causes, writes
    workflows, implements changes, runs tests, self-reviews, and closes out.
    Provider/tool/auth default to claude/anthropic/oauth_token; operator edits
    the generated YAML before running install_multi.sh to change provider.
    Builder is absent by default. Use --planner-count N to generate N planner seats.
    """
    if planner_count < 1:
        planner_count = 1
    _INSTANCES = ["primary", "secondary", "tertiary", "quaternary", "quinary"]
    lines = _header(project, team, ts)
    lines.extend(
        [
            f"# planner-only: {planner_count} planner seat(s), no builder, planner self-reviews",
            "subgroup_profile: planner-only",
            "team_type: subteam",
            "planner_mode: delivery",
            "notify_policy: queue_drained_only",
            "review_model: planner_owned",
            "dedicated_reviewer: false",
            "dedicated_builder: false",
            "planner_test_lock_required: true",
            "ownership_paths:",
            _yaml_list(ownership_paths),
            "scaling_policy:",
            "  max_builders: 0",
            "  reviewer_required_when_builders_gte: 999",
            "  overflow_action: propose_new_subteam",
            "  reviewer_fallback: planner",
            "seats:",
        ]
    )
    for idx in range(planner_count):
        if planner_count == 1:
            instance_label = ""
            purpose = "self-contained engineering planner: diagnoses, implements, tests, self-reviews, and closes out without a dedicated builder"
        else:
            instance = _INSTANCES[idx] if idx < len(_INSTANCES) else str(idx + 1)
            instance_label = f"\n    instance: {instance}"
            purpose = f"{instance} engineering planner: self-contained diagnosis, implementation, testing, self-review, and closeout"
        lines.extend(
            [
                "  - role: planner" + instance_label,
                "    tool: claude",
                "    provider: anthropic",
                "    auth_mode: oauth_token",
                f"    purpose: {_quote(purpose)}",
                "    capabilities: [implementation, tests, docs, self-review]",
                f"    rationale: {_quote('high-reasoning planner owns the full engineering cycle; builder is optional and absent in this profile')}",
            ]
        )
    lines.extend(
        [
            f"estimated_monthly_cost_usd: {{ low: 0, high: {20 * planner_count} }}",
            "---",
            "",
        ]
    )
    return "\n".join(lines)


def _quality_docs_yaml(project: str, ts: str) -> str:
    lines = _header(project, "quality-docs", ts)
    lines.extend(
        [
            "team_type: quality-docs",
            "planner_mode: quality_campaign",
            "notify_policy: never_notify_memory",
            "quality_gate_doc: quality-docs/QUALITY.md",
            "autonomous: true",
            "loop: continuous",
            "stop_rule: campaign_clean_streak_3",
            "seats:",
            "  - role: planner",
            "    tool: claude",
            "    provider: minimax",
            "    auth_mode: api",
            f"    rationale: {_quote('designs campaigns, assigns patrols, maintains QUALITY.md, and researches findings')}",
            "  - role: patrol",
            "    instance: human",
            "    tool: claude",
            "    provider: minimax",
            "    auth_mode: api",
            f"    rationale: {_quote('minimal human-path patrol; simulates product workflows and records evidence')}",
            "estimated_monthly_cost_usd: { low: 0, high: 10 }",
            "---",
            "",
        ]
    )
    return "\n".join(lines)


_CARTOONER_PATHS = {
    "cartooner-front": [
        "apps/web/src/**",
        "apps/web/electron/**",
    ],
    "cartooner-runtime-platform": [
        "apps/web/electron/**",
        "apps/web/src/services/**",
        "core/launchers/**",
        "scripts/**",
    ],
    "cartooner-skills": [
        "core/skills/cartooner-harness/**",
        "~/.agents/skills/cartooner*/**",
    ],
}


def _default_teams(project: str, repo_root: str, archetype: str) -> list[str]:
    resolved = archetype
    if resolved == "auto":
        haystack = f"{project} {repo_root}".lower()
        resolved = "cartooner" if "cartooner" in haystack else "generic"
    if resolved == "cartooner":
        return ["cartooner-front", "quality-docs"]
    return ["core", "quality-docs"]


def _paths_for(team: str, archetype: str) -> list[str]:
    if archetype in {"auto", "cartooner"} and team in _CARTOONER_PATHS:
        return list(_CARTOONER_PATHS[team])
    if team in {"core", "product-surface"}:
        return ["**/*"]
    return [f"{team}/**", "src/**", "tests/**"]


def seed(
    *,
    project: str,
    output_dir: Path,
    repo_root: str,
    teams: list[str],
    archetype: str,
    force: bool,
    profile: str = "dev-minimal",
    planner_count: int = 1,
) -> list[Path]:
    if profile not in VALID_PROFILES:
        raise ValueError(f"--profile must be one of {VALID_PROFILES}; got {profile!r}")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    selected = list(teams or _default_teams(project, repo_root, archetype))
    # For non-test profiles, always ensure quality-docs is included.
    # For the test profile, the selected teams ARE the QA teams — no auto-append.
    # For planner-only, also skip quality-docs auto-append (planning-focused teams
    # don't automatically pair with a quality patrol).
    if profile not in ("test", "planner-only") and "quality-docs" not in selected:
        selected.append("quality-docs")
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for team in selected:
        path = output_dir / f"{team}__approved.yaml"
        if path.exists() and not force:
            continue
        if team == "quality-docs" and profile not in ("test", "planner-only"):
            text = _quality_docs_yaml(project, ts)
        elif profile == "dev-standard":
            text = _dev_standard_yaml(project, team, ts, _paths_for(team, archetype))
        elif profile == "test":
            text = _test_group_yaml(project, team, ts)
        elif profile == "planner-only":
            text = _planner_only_yaml(project, team, ts, _paths_for(team, archetype), planner_count=planner_count)
        else:
            # dev-minimal (default)
            text = _dev_minimal_yaml(project, team, ts, _paths_for(team, archetype))
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed MULTI_TEAM_MINIMAL approved proposal YAML files.\n\n"
            + PROFILES_NOTE
        )
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--repo-root", default="")
    parser.add_argument("--teams", default="")
    parser.add_argument("--archetype", choices=["auto", "generic", "cartooner"], default="auto")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--profile",
        choices=list(VALID_PROFILES),
        default="dev-minimal",
        help=(
            "Subgroup profile to generate. "
            "dev-minimal (default): planner + builder, planner self-reviews. "
            "dev-standard: planner + 2 builders + reviewer, reviewer gate required. "
            "test: planner + patrol, QA-only, no product code edits by default. "
            "planner-only: planner self-contains all engineering (no builder); "
            "provider/tool/auth configurable through project inputs."
        ),
    )
    parser.add_argument(
        "--planner-count",
        type=int,
        default=1,
        dest="planner_count",
        help=(
            "Number of planner seats to generate for the planner-only profile. "
            "Default: 1. Ignored for other profiles. "
            "Each additional planner gets a distinct instance name (primary, secondary, etc.). "
            "Provider/tool/auth for each seat can be edited in the generated YAML."
        ),
    )
    args = parser.parse_args(argv)

    teams = [item.strip() for item in args.teams.split(",") if item.strip()]
    written = seed(
        project=args.project,
        output_dir=Path(args.output_dir),
        repo_root=args.repo_root,
        teams=teams,
        planner_count=args.planner_count,
        archetype=args.archetype,
        force=args.force,
        profile=args.profile,
    )
    for path in written:
        print(f"seeded {path}")
    if not written:
        print("seeded 0 files; existing approved proposals preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
