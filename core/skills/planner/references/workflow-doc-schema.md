# Workflow Doc Schema

`workflow.md` is the durable execution plan for workflow-driven architecture.
It lives under `~/.agents/tasks/<project>/<task_id>/workflow.md`.

This schema is intentionally Markdown plus YAML-style fields so humans can edit
it and `agent_admin task` can scan it without a heavy parser.

## Top-Level Fields

```yaml
project: install
created: 2026-04-28T10:00:00Z
author: planner
seats_available: [memory, planner, builder, reviewer, patrol, designer]
seat_fallback:
  reviewer: planner
acceptance_criteria:
  - python3 -m pytest -q
```

Field definitions:

- `project`: project id. Must match `~/.agents/tasks/<project>`.
- `created`: ISO8601 timestamp for initial workflow creation.
- `author`: seat or tool that created the workflow.
- `seats_available`: roles that planner may assign in this project.
- `seat_fallback`: mapping from missing role to fallback role.
- `acceptance_criteria`: list of commands, checks, or human criteria required
  before planner can close the task.

## Step Fields

```yaml
name: foundation-docs
owner_role: builder
owner_seat: cartooner-front-builder-core
status: pending
prereq: []
mode: single
subagent_count: 0
per_subagent_inner_parallel: 0
context_per_subagent: ""
skill_commands: []
product_acceptance_required: false
artifacts: []
notify_on_done: [planner]
notify_on_issues: [planner]
notify_on_blocked: [planner, memory]
max_iterations: 3
escalate_on_max: planner
clear_after_step: true
```

Field definitions:

- `name`: stable step name. It must match the heading name.
- `owner_role`: role that owns the step.
- `owner_seat`: optional exact seat id. Required when a subteam has multiple
  seats with the same role, especially multiple builders. Use this to preserve
  module ownership; `owner_role` remains the capability fallback.
- `status`: one of `pending`, `in_progress`, `done`, or `blocked`.
- `prereq`: list of step names that must be `done` before this step is ready.
- `mode`: one of `single`, `parallel_subagents`, or `nested`.
- `subagent_count`: integer, `dynamic`, or `based_on_<artifact>`.
- `per_subagent_inner_parallel`: integer. `0` means no nested fan-out.
- `context_per_subagent`: description given to each subagent.
- `product_acceptance_required`: `true` when this step affects user-facing
  behavior and requires product acceptance criteria checks. Default is `false`.
- `skill_commands`: list of skill invocation strings.
- `artifacts`: list of files or directories the step must produce or inspect.
- `notify_on_done`: roles to notify after successful completion.
- `notify_on_issues`: roles to notify for non-blocking issues.
- `notify_on_blocked`: roles to notify when the step becomes `blocked`.
- `max_iterations`: integer retry/repair limit. Default is `3`.
- `escalate_on_max`: role to notify when `max_iterations` is exceeded.
- `clear_after_step`: boolean. Specialist default is `true`; planner is forced
  false because planner must retain routing state across the chain.

## Mode: single

`single` means one owner agent executes the step sequentially.

Complete workflow example, step 1:

```yaml
## Step 1: write-foundation-docs
owner_role: builder
owner_seat: product-surface-builder-core
status: pending
prereq: []
mode: single
subagent_count: 0
per_subagent_inner_parallel: 0
context_per_subagent: ""
product_acceptance_required: false
skill_commands: []
artifacts:
  - core/references/seat-capabilities.md
notify_on_done: [planner]
notify_on_issues: [planner]
notify_on_blocked: [planner]
max_iterations: 3
escalate_on_max: planner
clear_after_step: true
```

## Mode: parallel_subagents

`parallel_subagents` means the main owner uses the Agent tool to run N
subagents concurrently.

Complete workflow example, step 2:

```yaml
## Step 2: audit-doc-families
owner_role: reviewer
owner_seat: product-surface-reviewer
status: pending
prereq: [write-foundation-docs]
mode: parallel_subagents
subagent_count: 3
per_subagent_inner_parallel: 0
context_per_subagent: "Audit one reference doc for required sections."
product_acceptance_required: true
skill_commands:
  - "Skill: review"
artifacts:
  - core/references/
notify_on_done: [planner]
notify_on_issues: [planner]
notify_on_blocked: [planner, memory]
max_iterations: 3
escalate_on_max: planner
clear_after_step: true
```

## Mode: nested

`nested` means outer parallelism plus inner per-subagent parallelism.

Use it for asset grids and combinatorial creative work.

Nested ecommerce video example:

```yaml
## Step 4: generate-assets
owner_role: designer
status: pending
prereq: [extract-scenes]
mode: nested
subagent_count: based_on_extraction   # Step 3 extracted character+scene count
per_subagent_inner_parallel: 4        # each subagent fans out a 4-image grid
product_acceptance_required: false
context_per_subagent: |
  Generate 4-variant image grid for character/scene from Step 3 extraction.
skill_commands:
  - "Skill: design-shotgun"
artifacts:
  - artifacts/generated-assets/
notify_on_done: [planner]
notify_on_issues: [planner, designer]
notify_on_blocked: [planner, memory]
max_iterations: 3
escalate_on_max: planner
clear_after_step: true
```

## Fan-Out / Fan-In Flow

1. Planner marks prerequisites `done`.
2. Planner dispatches the owner step.
3. Owner reads `mode`.
4. For `single`, owner executes locally.
5. For `parallel_subagents`, owner starts N independent subagents.
6. For `nested`, owner starts outer subagents by entity.
7. Each outer subagent may start inner parallel work up to
   `per_subagent_inner_parallel`.
8. Each subagent writes its artifact and summary.
9. Owner waits for all subagents to complete.
10. Owner detects conflicts or missing artifacts.
11. Owner retries within `max_iterations` when the issue is local.
12. Owner marks the step `blocked` when retries exceed authority.
13. Owner fans in summaries into `DELIVERY.md`.
14. Owner notifies `notify_on_done` on success.
15. Owner notifies `notify_on_issues` for non-blocking concerns.
16. Owner notifies `notify_on_blocked` when blocked.
17. Planner consumes delivery.
18. Planner updates dependent step readiness.
19. Planner dispatches newly unblocked steps.
20. Memory/user receives escalation only when planner lacks authority.

## Planner Clear Rule

Planner `clear_after_step` is forced false even when the field is omitted or set
incorrectly. Planner must carry owner map, consumed deliveries, open blockers,
and next-hop state until the chain is complete.
