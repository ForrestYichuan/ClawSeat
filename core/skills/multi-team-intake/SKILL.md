---
name: multi-team-intake
description: >
  Memory-driven project-group designer for ClawSeat v3 multi-team projects.
  Use when the first project memory needs to analyze a repo, recommend a
  module/layer-based team topology, write reviewable config proposals, or
  design an autonomous quality-docs testing group. For applying approved
  roster changes after initial design, pair with clawseat-roster-admin.
---

# Multi-Team Intake

Use this skill from the first project-level memory seat when a project is being
turned into a ClawSeat v3 project group. The output is a proposal pack, not a
runtime roster: write `*__proposed.yaml` first, ask the operator to approve, and
only then rename or rewrite to `*__approved.yaml`.

## Core Workflow

1. Scan the target repo's current docs, manifests, tests, and major directories.
2. Identify natural module/layer boundaries. Prefer real ownership seams over a
   fixed team count.
3. Recommend a phase-1 topology with small teams and clear path ownership.
4. Include an autonomous `quality-docs` team for products that need continuous
   validation, long-running GUI flows, SDK/provider integration, or release gates.
5. Explain each team: why it exists, which paths it owns, which risks it tests,
   and which acceptance routes it needs.
6. Draft the current-project `TEAM_OWNERSHIP.md` summary for the proposed
   topology. Keep it descriptive only; do not duplicate runtime config.
7. Write proposed YAML files under:

   ```text
   ~/.agents/tasks/<project>/_config-proposals/
   ```

8. Ask for operator approval before producing `__approved.yaml`.
9. After approval, update:

   ```text
   ~/.agents/tasks/<project>/TEAM_OWNERSHIP.md
   ```

   This document is project-local and owned by memory. It records stable team
   and builder capability split; it is not a second config source.
10. Tell the operator the dry-run command:

   ```bash
   scripts/install.sh --mode multi --project <project> --teams <csv> --dry-run
   ```

Memory designs the project group and briefs. Team planners design workflows.
Memory does not write `workflow.md` and does not dispatch directly to builders.
There is one project-level memory. Subteams never include memory seats.
The old `clawseat-solo` idea is now this minimal subteam archetype: a
`planner + builder` unit managed by the project-level memory, not a separate
single-mode project.

After the operator approves adding a seat or subteam to an already-installed
project, switch to `clawseat-roster-admin`; do not edit project/profile TOML
directly from this skill.

## Minimal Project Group Rules

Every multi-team project starts from:

```text
project-memory
one or more subteams
quality-docs
```

Subteams are execution units:

```yaml
team_type: subteam
planner_mode: delivery
notify_policy: queue_drained_only
review_model: dedicated_reviewer
dedicated_reviewer: true
scaling_policy:
  max_builders: 3
  reviewer_required_when_builders_gte: 2
  overflow_action: propose_new_subteam
  reviewer_fallback: planner
```

Rules:

1. A subteam has exactly one planner.
2. A subteam has 1-3 builders.
3. With 1 builder, reviewer is optional. If omitted, set
   `review_model: planner_owned` and `dedicated_reviewer: false`; planner
   performs spec/delivery review fallback.
4. With 2-3 builders, reviewer is mandatory.
5. With 2-3 builders, every builder seat must declare `instance`, `purpose`,
   and `capabilities` so the subteam planner can choose exact `owner_seat`.
6. A requested 4th builder is forbidden; memory must propose a new subteam
   instead.
7. Each subteam should declare `ownership_paths` so planner can route by module
   boundary.
8. Dev planners do not notify memory per task. They research, write or name tests
   or acceptance checks when practical, dispatch exact builders, review delivery,
   loop rework, append `task_done`, and only wake memory when the team queue is
   drained or an exception needs memory/user authority.

## Generic Topology Heuristic

Start from these candidate domains, then merge or rename them to match the repo:

| Candidate team | Use when the repo has... |
|---|---|
| `product-surface` | UI, user workflows, front-end state, UX, visible product behavior |
| `runtime-platform` | Electron/CLI/server runtime, SDK adapters, providers, IPC, auth/env, local paths |
| `domain-capability` | Domain skills, content pipelines, asset workflows, business logic, model-specific capabilities |
| `orchestration-ops` | ClawSeat/project-group bridge, launch/restart/recovery, tmux/session automation, install/upgrade |
| `quality-docs` | Continuous testing, human-path simulation, QA docs, release gates; add chaos/risk patrols only when needed |

Do not force all five. A small library might only need `runtime-platform` and
`quality-docs`; a large desktop product may need all five.

## Quality-Docs Rule

`quality-docs` is autonomous. Its planner decides when and how to test; memory
does not decide whether testing is needed. The team runs continuously and never
notifies memory directly; memory pulls the gate doc when awakened:

```text
quality-docs-planner
quality-docs-patrol-human
```

`quality-docs-patrol-fast` and `quality-docs-patrol-chaos` are scale-up seats,
not part of MULTI_TEAM_MINIMAL.

Use `instance` for same-role seats:

```yaml
seats:
  - role: planner
    tool: claude
    provider: minimax
    auth_mode: api
  - role: patrol
    instance: fast
    tool: claude
    provider: minimax
    auth_mode: api
  - role: patrol
    instance: human
    tool: claude
    provider: minimax
    auth_mode: api
  - role: patrol
    instance: chaos
    tool: claude
    provider: minimax
    auth_mode: api
```

Planner loop:

1. Scan development queues, workflow docs, deliveries, git diff, open findings,
   flaky history, and risk registers.
2. Create `TestCampaign`s and assign `TestMission`s.
3. Each patrol runs exactly the mission assigned to it, writes evidence, then
   waits for the next mission.
4. If a mission finds nothing, raise the next mission's difficulty.
5. A patrol leaves one campaign only after three consecutive clean rounds for
   that campaign; then planner switches it to a new attack surface.
6. Findings are recorded in `quality-docs/QUALITY.md` and
   `quality-docs/findings/<id>.md` with likely owner/root cause. Patrols do not
   edit product code or directly command development teams.

## Proposal Fields

Each normal subteam proposal should include:

```yaml
project: <project>
team: <team>
proposal_status: proposed
team_type: subteam
planner_mode: delivery
notify_policy: queue_drained_only
ownership_paths:
  - src/**
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
    rationale: "claims this team queue and writes workflow.md"
estimated_monthly_cost_usd:
  low: 0
  high: 20
```

Only autonomous teams such as `quality-docs` add:

```yaml
team_type: quality-docs
planner_mode: quality_campaign
notify_policy: never_notify_memory
quality_gate_doc: quality-docs/QUALITY.md
autonomous: true
loop: continuous
stop_rule: campaign_clean_streak_3
```

Use `instance` when a team needs more than one seat with the same role. The v3
renderer materializes seat ids as `<team>-<role>-<instance>`.

## TEAM_OWNERSHIP.md

Memory maintains `~/.agents/tasks/<project>/TEAM_OWNERSHIP.md` for the current
project only. Keep it short:

```md
## <team>
Mission: ...
Ownership paths:
- src/...
Seats:
- planner: <team>-planner
- reviewer: <team>-reviewer
- builder-<instance>: purpose; capabilities
Boundaries:
- Not responsible for ...
```

Do not add a planner-owned long-lived builder assignment document. Planner's
per-task split belongs in `tasks/<project>/<team>/workflow/<task_id>.md`; stable
ownership changes are relayed back to memory for this document.

## References

Read `references/generic-project-group.md` when generating a full generic
proposal pack or when the operator asks for the default project-group template.
