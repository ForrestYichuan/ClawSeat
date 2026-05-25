# Generic Project Group Proposal Pack

This is the default shape for a ClawSeat v3 multi-team project group. Memory
must adapt names, ownership paths, and builder instances after scanning the
actual repo.

## Recommendation Card

```text
topology_strategy: project-memory-plus-scaled-subteams
project_archetype: derived-from-repo
project_memory: one global memory, never copied into subteams
rendered_profile: top-level mode.project_memory = "memory"; teams contain workers only
required_always:
  - quality-docs
candidate_subteams:
  - product-surface
  - runtime-platform
  - domain-capability
  - orchestration-ops
```

Merge a candidate when the repo has no matching surface. Split a candidate when
one layer contains clearly independent modules with different owners, tests, and
failure modes.

## Subteam Scaling Policy

`clawseat-solo` is no longer a separate single-mode runtime. Its useful part is
the minimal dev subteam archetype below; multi-team can run many of these units
under one project memory.

Every normal subteam follows the same gate:

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

- `planner + builder-core` is the minimal subteam.
- With one builder, reviewer is optional. If omitted, set
  `review_model: planner_owned` and `dedicated_reviewer: false`; planner
  performs spec review and delivery review fallback.
- With two or three builders, reviewer is mandatory.
- With two or three builders, each builder must have a distinct `instance`,
  `purpose`, and `capabilities`; these fields are the planner's routing hints
  for exact `owner_seat` selection.
- A fourth builder is forbidden; memory must propose a new subteam instead.
- Dev planner researches, defines verification first when practical, dispatches
  exact builder seats, reviews delivery, loops rework, writes `task_done`, and
  notifies memory only when its team queue is drained or blocked by authority.
- Subteams must not declare a memory seat.
- Applying an approved roster change is handled by `clawseat-roster-admin`, not
  by direct TOML edits.

## Current-Project Ownership Doc

Memory maintains one project-local doc:

```text
~/.agents/tasks/<project>/TEAM_OWNERSHIP.md
```

It is a readable ownership summary, not runtime config. Include each team's
mission, ownership paths, seat ids, stable builder purpose/capabilities, and
non-owned boundaries. Planner reads this doc but writes per-task assignments in
`workflow/<task_id>.md`; planner does not maintain a second long-lived builder
split file.

## Generic YAML Examples

### product-surface__proposed.yaml

```yaml
project: <project>
team: product-surface
proposal_status: proposed
team_type: subteam
planner_mode: delivery
notify_policy: queue_drained_only
ownership_paths:
  - apps/web/src/components/**
  - apps/web/src/store/**
  - apps/web/src/domain/**
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
    rationale: "plans user-visible product/UI work and owns this team's workflow."
  - role: builder
    instance: core
    tool: codex
    provider: openai
    auth_mode: oauth
    purpose: "product surface generalist"
    capabilities: ["ui", "state", "ux", "frontend-tests"]
    rationale: "implements UI/product changes and targeted tests."
estimated_monthly_cost_usd: { low: 0, high: 30 }
```

When adding a second builder, add a reviewer in the same proposal:

```yaml
seats:
  - role: planner
    tool: claude
    provider: anthropic
    auth_mode: oauth_token
    rationale: "plans product-surface work."
  - role: builder
    instance: app-shell
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: "implements app shell and global UI state."
  - role: builder
    instance: canvas-graph
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: "implements workflow graph, nodes, edges, and canvas state."
  - role: reviewer
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: "reviews specs, parallel builder deliveries, and regression evidence."
```

### runtime-platform__proposed.yaml

```yaml
project: <project>
team: runtime-platform
proposal_status: proposed
team_type: subteam
planner_mode: delivery
notify_policy: queue_drained_only
ownership_paths:
  - apps/web/electron/src/main/**
  - apps/web/electron/src/preload/**
  - apps/web/src/services/control-plane/**
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
    rationale: "plans runtime, SDK/provider, env, auth, IPC, and local-path work."
  - role: builder
    instance: core
    tool: codex
    provider: openai
    auth_mode: oauth
    purpose: "runtime platform generalist"
    capabilities: ["electron", "ipc", "sdk", "providers", "workspace-data"]
    rationale: "implements runtime/integration changes and regression tests."
estimated_monthly_cost_usd: { low: 0, high: 30 }
```

### domain-capability__proposed.yaml

```yaml
project: <project>
team: domain-capability
proposal_status: proposed
team_type: subteam
planner_mode: delivery
notify_policy: queue_drained_only
ownership_paths:
  - core/skills/**
  - skills/**
  - src/domain/**
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
    rationale: "plans domain-specific skills, pipelines, content, or business logic."
  - role: builder
    instance: core
    tool: codex
    provider: openai
    auth_mode: oauth
    purpose: "domain capability generalist"
    capabilities: ["skills", "pipelines", "business-logic", "fixtures"]
    rationale: "implements domain capability changes and fixtures."
estimated_monthly_cost_usd: { low: 0, high: 30 }
```

### orchestration-ops__proposed.yaml

```yaml
project: <project>
team: orchestration-ops
proposal_status: proposed
team_type: subteam
planner_mode: delivery
notify_policy: queue_drained_only
ownership_paths:
  - scripts/**
  - core/scripts/**
  - core/shell-scripts/**
  - docs/**
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
    rationale: "plans project-group lifecycle, install, restart, recovery, and queue protocol work."
  - role: builder
    instance: core
    tool: codex
    provider: openai
    auth_mode: oauth
    purpose: "orchestration and automation generalist"
    capabilities: ["install", "restart", "queue-protocol", "tmux", "recovery"]
    rationale: "implements automation/ops scripts and protocol tests."
estimated_monthly_cost_usd: { low: 0, high: 30 }
```

### quality-docs__proposed.yaml

```yaml
project: <project>
team: quality-docs
proposal_status: proposed
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
    rationale: "designs campaigns, assigns patrols, maintains QA docs, and routes findings."
  - role: patrol
    instance: fast
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: "runs high-frequency deterministic checks: typecheck, unit, targeted tests, queue smoke."
  - role: patrol
    instance: human
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: "simulates real user workflows and captures screenshots/logs."
  - role: patrol
    instance: chaos
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: "tests failure injection, recovery, missing credentials, port/session conflicts, and long-running state."
estimated_monthly_cost_usd: { low: 0, high: 20 }
```

## Cartooner Minimal Pack

Default first launch:

```text
cartooner-memory
cartooner-front: planner + builder-core
quality-docs: planner + patrol-human
```

Recommended subteams after memory confirms real need:

```text
cartooner-front
  ownership: apps/web/src UI, Vibe Canvas, Core Events, interaction state
  max builders: app-shell, canvas-graph, interaction-events

cartooner-runtime-platform
  ownership: Electron main/preload, SDK runtime, providers, workspace data,
  ClawSeat bridge
  max builders: electron-ipc, sdk-runtime, provider-workspace

cartooner-skills
  ownership: ~/.agents/skills/cartooner*, asset persistence, media skills,
  pipeline/runtime/style skills
  max builders: skill-router, media-skills, memory-workflow-skills
```

## Quality-Docs Artifacts

`quality-docs-planner` maintains:

```text
~/.agents/tasks/<project>/quality-docs/QUALITY.md
~/.agents/tasks/<project>/quality-docs/campaigns/
~/.agents/tasks/<project>/quality-docs/missions/
~/.agents/tasks/<project>/quality-docs/runs/
~/.agents/tasks/<project>/quality-docs/findings/
~/.agents/tasks/<project>/quality-docs/evidence/
```

`QUALITY.md` is intentionally compact: `Gate: PASS|WATCH|BLOCK_COMMIT`, active
findings, recent checks, next campaign, and links to detailed finding files.
Every closed finding becomes a regression scenario in the relevant campaign or
matrix doc when useful; avoid duplicating large logs in the top-level gate.
