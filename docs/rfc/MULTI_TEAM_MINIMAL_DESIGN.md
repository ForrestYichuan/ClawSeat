# MULTI_TEAM_MINIMAL Design Notes

Status: draft-agreed context snapshot
Last updated: 2026-05-14

This note records the decisions agreed with the operator before implementing the
minimal multi-team template and the future memory-driven team-design skill.

## Goal

`MULTI_TEAM_MINIMAL` is the replacement semantics for the old `clawseat-solo`
idea. A "solo" unit is now a team-local `planner + builder` development
subteam, not a whole project with its own memory. The template is a minimal
multi-team proposal pack and intake workflow for v3:

1. A first project-level memory seat analyzes the target repo.
2. Memory recommends a team topology and explains why.
3. Memory writes reviewable config proposals under
   `~/.agents/tasks/<project>/_config-proposals/`.
4. The operator approves proposals.
5. `scripts/install.sh --mode multi --project <p> --teams <csv>` renders the
   v3 profile and team workspaces. For compatibility,
   `scripts/install.sh --template clawseat-solo --project <p>` seeds this same
   minimal proposal pack and delegates to `install_multi.sh`.

The first project-level memory seat is a project-group architect. It is not a
team-local memory. In the rendered v3 profile, it remains the top-level
`mode.project_memory = "memory"` seat so worker teams can still close receipts
and wake memory through the normal transport path.

## Non-Goals

- Do not depend on `core/seat-templates/` for the first minimal version. The v3
  seat template catalog is still under development.
- Do not preserve a separate single-mode solo runtime. `clawseat-solo` remains
  only as a legacy CLI alias for `MULTI_TEAM_MINIMAL`.
- Do not require iTerm. Cartooner-integrated multi-team testing uses tmux and
  embedded terminals / `--no-window`.
- Do not require reviewer for every subteam at first launch. A one-builder
  subteam may use planner review fallback. Once a subteam has two or three
  builders, reviewer is mandatory.
- Do not assume the old Cartooner Python/OpenClaw backend architecture.

## Cartooner Facts Used For The Design

Current Cartooner mainline is SDK-only:

- Desktop host: Electron.
- UI: React / Vite / TypeScript.
- Agent channel: Claude Code SDK.
- Main product surfaces: chat sidebar, Vibe Canvas, Tactical HUD, asset sidebars,
  inspiration browser, project management.
- Workflow visualization: task / phase / decision / deliverable nodes.
- Event model: SDK raw events, `cartooner-command`, provider calls, asset
  events, and user decisions are normalized into Core Events.
- Current docs explicitly warn not to add new external AI orchestration backend,
  Python runtime service, Socket.IO server, old file browser, old workbench, or
  old project DB.

Therefore the minimal Cartooner multi-team split should follow product and
runtime boundaries, not legacy backend/frontend boundaries.

## Generic Minimal Topology

The generic minimal topology is not a fixed team count. Memory derives team
boundaries from the repo's modules/layers and starts from these candidate
domains:

```text
project-level memory
  Owns user intent, repo analysis, team topology proposal, acceptance strategy,
  and final memory retention.

team: product-surface
  Owns user-visible UI/product workflows, interaction state, and UX regressions.

team: runtime-platform
  Owns app/runtime host, SDK/provider adapters, auth/env, local paths, IPC,
  terminal/session bridges, and integration acceptance.

team: domain-capability
  Owns project-specific skills, business logic, content/asset pipelines, or
  model-specific capabilities.

team: orchestration-ops
  Owns ClawSeat/project-group lifecycle, install/restart/recovery, queue
  protocol, and automation glue.

team: quality-docs
  Owns autonomous continuous testing, human-path simulation, chaos/risk testing,
  QA docs, findings, and verification loops.
```

Most development teams start with only:

```text
planner + builder
```

Normal subteams never include memory and must obey:

```yaml
team_type: subteam
scaling_policy:
  max_builders: 3
  reviewer_required_when_builders_gte: 2
  overflow_action: propose_new_subteam
  reviewer_fallback: planner
```

The exact gate:

```text
1 builder: reviewer optional; planner performs spec/delivery review fallback.
2-3 builders: reviewer mandatory.
4th builder request: forbidden; memory proposes a new subteam.
```

The `quality-docs` team starts with:

```text
planner + patrol-human
```

`patrol-fast` and `patrol-chaos` are expansion seats for sustained QA
campaigns; they are not part of the minimal launch.

If a candidate domain has no matching code or ownership surface, memory merges
it into the nearest real team. If one domain has distinct modules with different
tests and failure modes, memory may split it.

## Cartooner Topology Example

For Cartooner, the derived topology maps to the real architecture:

```text
cartooner-front
  React UI/UX, Chat Sidebar, Vibe Canvas nodes/edges, Tactical HUD, Core Event
  display, interaction state, and visible product workflows.

cartooner-runtime-platform
  Electron host, IPC/preload, Claude Code SDK query path, streaming events,
  cwd isolation, provider env overlay, MiniMax/Anthropic compatibility,
  local workspace data, and ClawSeat bridge hooks.

cartooner-skills
  ~/.agents/skills/cartooner-* router/resource-ops/image/video/audio/prompt,
  pipeline runtime, project/asset persistence, style bible, and media/provider
  skill guardrails.

quality-docs
  Autonomous QA planner plus one MiniMax human-path patrol for realistic
  product workflow testing.
```

The default first launch should be:

```text
cartooner-memory
cartooner-front: planner + builder-core
quality-docs: planner + patrol-human
```

Memory may later recommend `cartooner-runtime-platform` or
`cartooner-skills`, or expand a subteam to 2-3 builders with a mandatory
reviewer.

## Minimal Proposal Shape

The proposal pack should generate one `*__approved.yaml` per team after user
approval. A minimal team proposal looks like:

```yaml
---
project: <project>
team: product-ui
proposal_status: approved
operator_approved_ts: <iso8601>
team_type: subteam
ownership_paths:
  - apps/web/src/**
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
    rationale: "claims this team's queue, writes workflow.md, dispatches team work"
  - role: builder
    tool: codex
    provider: openai
    auth_mode: oauth
    rationale: "implements planner-assigned changes and writes DELIVERY.md"
estimated_monthly_cost_usd:
  low: 0
  high: 20
---
```

The exact tool/provider defaults remain policy, not hard-coded truth. The skill
should explain the choice and let the operator approve or change it.

When a team needs multiple seats with the same role, proposals use `instance`:

```yaml
team: quality-docs
team_type: quality-docs
autonomous: true
loop: continuous
stop_rule: campaign_clean_streak_3
seats:
  - role: planner
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: "designs QA campaigns and keeps QUALITY.md current"
  - role: patrol
    instance: human
    tool: claude
    provider: minimax
    auth_mode: api
    rationale: "simulates real user workflows"
```

The minimal renderer materializes this as `quality-docs-patrol-human`.
`quality-docs-planner` may later add `patrol-fast` and `patrol-chaos` when a
campaign needs deterministic high-frequency checks or fault-injection work.

## Memory-Driven Team Designer Skill

The final implementation should become a skill, tentatively:

```text
core/skills/multi-team-intake/SKILL.md
```

Skill responsibilities:

1. Scan the target repo's current docs, package manifests, tests, and major
   directories.
2. Identify project archetype and module boundaries.
3. Recommend a team topology with phase-1 minimal teams and optional phase-2
   teams.
4. Explain why each team exists, which paths it owns, and which acceptance
   routes it needs.
5. Write proposed YAML files first, not approved YAML.
6. Ask the operator to approve or edit.
7. Convert proposals to `__approved.yaml` only after explicit approval.
8. Enforce the subteam scaling gate: 1 builder can rely on planner review
   fallback, 2-3 builders require reviewer, and a 4th builder means a new
   subteam proposal.
9. Tell the operator the exact `install.sh --mode multi` dry-run command.

Important boundary: memory designs the project groups and writes briefs /
acceptance criteria. Planner claims team queues and authors workflow. Memory
does not write `workflow.md` and does not dispatch directly to builders.

The skill now lives at:

```text
core/skills/multi-team-intake/SKILL.md
```

Its generic proposal-pack reference lives at:

```text
core/skills/multi-team-intake/references/generic-project-group.md
```

## Autonomous Quality-Docs Loop

`quality-docs` is intentionally different from normal memory-queued teams:

```text
quality-docs-planner decides what to test and when
memory receives status and long-term findings but does not decide whether to test
patrols execute assigned TestMissions and record evidence
```

The planner scans development queues, workflow docs, deliveries, git diff, open
findings, flaky history, and risk registers. It creates TestCampaigns and assigns
missions to patrols. A patrol that finds nothing gets a harder next mission. A
patrol leaves one campaign only after three consecutive clean rounds for that
campaign; then the planner switches it to another attack surface. Findings are
assigned back to the owning development team for fixes, and every closed finding
becomes a regression scenario in the test matrix.

## Memory To Planner Execution Trigger

For v3 multi-team, the agreed default is pull-first:

1. Memory writes a brief:

   ```bash
   python3 core/scripts/agent_admin.py brief queue \
     --project <p> --team <t> \
     --task-id <task_id> \
     --objective "<one-line objective>" \
     --seats-required builder
   ```

2. This writes:

   ```text
   ~/.agents/tasks/<project>/<team>/brief/<task_id>.md
   ~/.agents/tasks/<project>/<team>/tasks.queue.jsonl
   ```

3. Planner claims:

   ```bash
   python3 core/scripts/agent_admin.py brief claim \
     --project <p> --team <t> \
     --task-id <task_id> \
     --actor planner@claude
   ```

4. Planner writes:

   ```text
   ~/.agents/tasks/<project>/<team>/workflow/<task_id>.md
   ```

For the minimal template phase, do not default to a background 60-second claim
loop. A launchd poller that claims without waking the planner TUI can create a
confusing "claimed but not executing" state. Start with SessionStart/manual
claim, or add a wrapper that claims and wakes the correct planner pane.

## Cross-Team Dependency Rule

Use `depends_on` across teams only through durable queue state. A downstream
team task must remain `task_waiting_for` until the upstream task is `task_done`
in any sibling team queue.

Every task identity in discussion or compact summaries must include:

```text
project + team + task_id
```

Never refer to a bare task id in multi-team mode.

## Multi-Builder Dispatch Rule

When a subteam has more than one builder, planner must route by exact seat, not
only by role:

```yaml
owner_role: builder
owner_seat: cartooner-front-builder-canvas-graph
```

`owner_role` remains the fallback capability label. `owner_seat` is the module
ownership decision. Planner derives it from `[teams].<team>.ownership_paths`,
`[teams].<team>.scaling_policy`, and `seat_overrides.<seat>.purpose` /
`capabilities`.

Using `--target-role builder` is acceptable only for a one-builder subteam or
for explicitly fungible work. Module-owned work must use `dispatch_task.py
--target <exact-seat>`.

## Planner Compact Trigger

Planner must not emit `[CLEAR-REQUESTED]`. Planner keeps cross-step routing
state and must preserve that context through compact.

Planner asks memory to compact by appending this marker to its relay:

```text
[memory: compact-me]
```

Memory then:

1. Checks that planner durable state is written.
2. Preserves a compact summary with:
   - project
   - team
   - task_id
   - workflow path
   - queue state
   - owner map
   - blockers
   - handoff receipt paths
   - pending acceptance routes
3. Applies an idle gate: planner is not thinking/working and no active write is
   in progress.
4. Sends `/compact` through the standard transport:

   ```bash
   core/shell-scripts/send-and-verify.sh --project <project> <planner-seat> "/compact"
   ```

The compact summary must include `team`; otherwise planner recovery can mix
similarly named tasks across teams.

## Protocol Rigor Review

The agreed MULTI_TEAM v0.1 protocol is strict enough to dogfood because it has:

- one project memory instead of duplicated team memories
- top-level `project_memory` encoded in the profile, outside all subteams
- per-team queues and `project + team + task_id` identity
- memory-owned spec and final acceptance
- planner-owned workflow and dispatch
- builder scaling gates with reviewer required for 2-3 builders
- exact `owner_seat` routing for multi-builder subteams
- autonomous `quality-docs` with evidence, findings, and regression docs
- durable cross-team dependencies through queue state
- team-aware compact summaries

Known hardening item before enabling a background claim loop by default:

- add a claim-and-wakeup wrapper so a queued task is never marked claimed while
  the planner TUI remains idle.

## Open Design Questions

1. Should minimal builder default to `codex/oauth/openai` or to
   `claude/oauth_token/anthropic` for consistency with planner?
2. Should a safe claim-and-wakeup wrapper be added before enabling the
   60-second background planner poller by default?
