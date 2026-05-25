---
name: gstack-harness
description: "Multi-seat harness: bootstrap roster, start seats, dispatch tasks, complete handoffs with durable ACKs, provision frontstage heartbeat, render CLI operator console for a project profile."
---

# Gstack Harness

`gstack-harness` is the runtime orchestration core for a multi-seat engineering
team.

It does not replace existing `gstack` specialist skills. It wraps them in a
stable harness runtime:

- seat model
- dispatch/completion/ACK protocol
- CLI-first transport plus optional async adapters
- heartbeat / patrol / unblock
- CLI control console
- project profiles

Project profiles may also define project-local seat runtime overrides, so the
same stable seat ids can map to different tool/auth/provider choices per
project without changing the role model.
Writing boundaries: see [`core/references/seat-ownership.md`](../../references/seat-ownership.md).

## Use this skill when

- a project has a frontstage seat plus planner / specialist seats
- task handoffs are document-first and transport-aware
- you need a reliable `assigned -> notified -> consumed` protocol that still
  works in CLI-only mode
- you need one CLI console to inspect chain health
- you need to bootstrap or start seats from a project profile

## Load by task

Do not load every reference by default. Start from the project profile under
`assets/profiles/`, then load only the references needed for the current task:

- seat launch / runtime choice / window layout
  - [Seat model](references/seat-model.md)
  - [Heartbeat policy](references/heartbeat-policy.md) when the seat is the
    frontstage / heartbeat owner
- dispatch / completion / verify handoff
  - [Chain protocol](references/chain-protocol.md)
  - [Dispatch playbook](references/dispatch-playbook.md)
  - [Feishu delegation report](references/feishu-delegation-report.md) only when a
    Feishu-side async sink or koder overlay is active; otherwise CLI-only flow
    stays on handoff JSON + state.db events
- parallel execution of independent sub-tasks
  - fan out independent sub-tasks through the seat's agent-dispatch primitive, then serialize only the final cross-check / delivery step
- console / patrol / reminder review
  - [Console model](references/console-model.md)
  - [Heartbeat policy](references/heartbeat-policy.md)
- tmux transport / diagnostics / self-diagnosis
  - loaded automatically via `tmux-basics` skill (all seats)
- bootstrap / project setup
  - [Seat model](references/seat-model.md)
  - [Console model](references/console-model.md)

Consumer-specific project profiles do not belong in this core skill.

Keep those under:

- `adapters/<project>/`
- `examples/<project>/profiles/`

## Script entrypoints

- `scripts/bootstrap_harness.py`
  - bootstrap a project from a profile into the existing `agent_admin` runtime
  - with `--start`, only starts the frontstage / heartbeat owner first, then
    opens the project window; it does not eagerly launch every seat
  - bootstrap must still pre-initialize every declared seat's managed scaffold:
    session record, isolated runtime dir, workspace guide, `WORKSPACE_CONTRACT.toml`,
    `repos/` symlink, and an idle `TODO.md` inbox entry
- `scripts/start_seat.py`
  - start a seat and auto-provision heartbeat when the seat is the frontstage
  - for non-frontstage seats, first prints a launch summary and requires an
    explicit confirmation rerun before the seat is actually started
  - use this after any runtime reconfiguration so the seat comes back up on the
    selected harness/runtime
  - do not use this as the first recovery move for a stopped Claude seat that
    still has its original workspace, runtime home, and prior Claude session
    history; recover that seat first, then fall back to a fresh start only if
    runtime recovery fails
- `scripts/dispatch_task.py`
  - default dispatch path for frontstage -> planner and planner -> specialist
    handoffs
  - writes `TODO`, updates project task/state docs, notifies the target seat,
    records the durable handoff JSON + state.db event, and optionally fans out
    an async broadcast through the configured transport
  - Feishu group broadcast is only one transport; the legacy auto-broadcast path
    remains opt-in via `CLAWSEAT_ENABLE_LEGACY_FEISHU_BROADCAST=1`
- `scripts/notify_seat.py`
  - send a protocol-compliant seat-to-seat notice, reminder, or unblock
    message using the standard transport instead of raw tmux
- `scripts/complete_handoff.py`
  - write `DELIVERY`, notify the target seat, and optionally write a durable
    `Consumed:` ACK when the receiver has consumed the handoff
  - writes the matching durable closeout trail to handoff JSON + state.db, then
    optionally mirrors the closeout on any configured async transport
  - Feishu closeout/broadcast is optional transport only; legacy group
    auto-broadcast still requires `CLAWSEAT_ENABLE_LEGACY_FEISHU_BROADCAST=1`
- `scripts/send_delegation_report.py`
  - emit a delegation report on the Feishu-side async path
  - today's built-in serializer is `OC_DELEGATION_REPORT_V1` over
    `lark-cli --as user`; in CLI-only mode the same delegation state lives in
    handoff JSON + state.db events instead
  - use this only when a koder overlay or other Feishu-side receiver is active;
    it is not the primary control path
- `scripts/verify_handoff.py`
  - verify `assigned`, `notified`, and `consumed` for one handoff
- `scripts/render_console.py`
  - render a CLI summary of seat state, handoff health, heartbeat, and reminder
    candidates
- `scripts/patrol_loop.py`
  - run the profile’s patrol/unblock loop
- `scripts/provision_heartbeat.py`
  - provision heartbeat only for seats allowed by the profile
- `scripts/ack_contract.py`
  - write a durable receipt that the seat has re-read its generated workspace
    guide and `WORKSPACE_CONTRACT.toml`
  - can be called manually by the operator, by the seat itself, or from a
    future post-start / post-reread hook
- `scripts/selftest.py`
  - run an ephemeral end-to-end smoke test for dispatch, completion, ACK,
    canonical review verdict enforcement, console rendering, and heartbeat-seat
    gating

## Design rules

- Documents remain the source of truth.
- CLI direct interaction is the primary control path for operator/frontstage
  coordination.
- handoff JSON + state.db events carry the durable delegation facts.
- tmux and Feishu are transports/reminders, not the facts database.
- `OC_DELEGATION_REPORT_V1` is one Feishu-side serialization format, not the
  canonical or only control packet.
- seat-to-seat transport must default to the framework transport helper at
  `<repo-root>/core/shell-scripts/send-and-verify.sh`; raw `tmux send-keys` is only a
  fallback path
- if transport falls back to raw tmux, it must still honor the send contract: text, wait 1 second, `Enter`, then verify the message did not stay queued in the input buffer
- frontstage and planner seats should prefer `scripts/notify_seat.py` for ad hoc reminders/unblocks rather than composing transport by hand
- if an async broadcast path is configured, treat it as an optional mirror of
  the same handoff state; planner stop-hook summaries, legacy Feishu group
  broadcasts, and koder-facing envelopes all sit on top of the same receipt
  trail
- `gstack` specialist skills stay in place; this skill only orchestrates them.
- Sub-agent fan-out is the default for tasks with independent sub-goals. If a
  dispatched task has two or more sub-parts that touch disjoint files, run
  disjoint tests, or investigate disjoint code paths, the receiving seat must
  fan them out via its agent-dispatch primitive (Claude Code `Agent` tool,
  Codex subagent, Gemini subagent) and only serialize the final cross-check /
  delivery step. Fan-out rules, pattern, and anti-patterns are summarized in
  the dispatch playbook and seat dispatch docs.
- Treat dynamic-roster fields as separate concerns:
  - `seats` = canonical roster
  - `materialized_seats` = seats that get precreated workspace/task scaffolding
  - `runtime_seats` = seats that should receive tmux session/runtime records
  - `default_start_seats` = first-launch / operator-start hint
  - `bootstrap_seats` = backward-compatible bootstrap/frontstage intent only
- Starter profiles should normally set `materialized_seats = seats`.
- Set `runtime_seats = materialized_seats` for local tmux frontstages, but exclude the heartbeat owner when `heartbeat_transport = "openclaw"`.
- The frontstage-supervisor seat owns seat startup and operator window layout.
  It is responsible for:
  - deciding when a seat should be launched
  - confirming harness/runtime choice with the user before starting non-frontstage seats
  - batching related seat starts first when multiple seats must be relaunched together, then doing one window refresh/open pass after the launches succeed
  - remembering that `--no-open-window` / `--defer-window-refresh` starts seats headlessly; until the final `window open-monitor` or `window open-engineer`, the seat is running but not yet visible in the project tabs
  - opening or refreshing the project window so tabs reflect the project's
    canonical seat order
- Only frontstage-supervisor seats run recurring heartbeat.
- `consumed` must be backed by a durable `Consumed:` ACK, not just pane text.
- Review handoffs must carry a canonical `Verdict:` field.
- Frontstage must not start a non-frontstage seat silently. Before launch, it
  must show the user:
  - the harness/profile being used
  - the target seat and role
  - the selected tool, auth mode, and provider/model family
  Then, after user approval, it may re-run the start command with explicit
  confirmation.
- when a stage closeout lands back at frontstage, the active frontstage should
  reconcile the linked delivery trail, update the project docs, and then
  summarize the wrap-up for the user
  - in v0.7 CLI-first mode this is usually ancestor/operator-facing CLI work
  - if koder overlay is active, it mirrors the same delivery trail for Feishu
    presentation; it does not become a separate source of truth

## Claude recovery rule

For Claude seats, recovery and fresh start are not the same operation.

- if a seat previously worked and its original workspace, runtime home, and
  Claude session history still exist, prefer runtime recovery first
- runtime recovery means:
  - reuse the same workspace directory
  - reuse the same Claude runtime home / XDG directories
  - resume the prior Claude session when possible
- only fall back to `scripts/start_seat.py` / `agent-admin session start-engineer`
  after recovery fails or when the user explicitly wants a fresh session
- if you do fall back to a fresh session, say so clearly; do not describe it as
  "restoring" the old live seat

## Runtime selection heuristic

All runtime selection policy is defined in [Seat model](references/seat-model.md).
Do not duplicate runtime matrix or provider details here.

## First-launch rule

Fresh Claude seats are not yet fully hands-off.

- On first launch, the user may need to manually complete Claude onboarding in
  the TUI:
  - OAuth login/code flow
  - workspace trust confirmation
  - bypass-permissions confirmation
- After the user finishes those prompts, the operator should take over again
  and continue heartbeat provisioning / dispatch / patrol.
- Treat this as a normal first-run activation cost, not as a project-runtime
  failure.

## Project wrappers

Project-specific wrapper skills may sit on top of this core.

For example:

- a consumer project can keep its own project-facing frontstage wrapper
- `gstack-harness` remains the reusable runtime core

Project wrappers are project-local. Do not let a project-specific wrapper leak
into another project's live workspace or seat launch decisions.

Project profiles are also the right place to declare project-specific seat
runtime choices. Avoid patching those choices by hand after bootstrap when the
project profile can carry them directly.

## Adapter stub (R-01)

The OpenClaw native adapter is **not yet implemented**.

`core/adapter/adapter_shim.py` always returns a `TmuxCliAdapter` regardless of
the runtime environment. There is no code path that instantiates a real
OpenClaw adapter object — the shim exists only as a structural placeholder.

Practical implication: all seat-to-seat transport calls ultimately go through
the tmux/file-artifact path unless an explicit async bridge is configured. When
the Feishu-side koder overlay is active, the caller may explicitly bypass tmux
and use the Feishu user-message path (`complete_handoff.py` /
`send_delegation_report.py` → Feishu serializer). That bypass is optional and
exists only when the overlay/bridge is configured. In CLI-only mode there is no
Feishu bypass; use direct CLI interaction, handoff JSON, state.db events, or
`agent_admin` CLI instead. Do not assume the adapter layer will detect and
switch modes automatically.

## Sandbox HOME resolution

Seats run inside an isolated HOME at `~/.agents/runtime/identities/<tool>/<auth>/<identity>/home/`.
`Path.home()` inside a seat returns this sandbox path — **not** the operator's real HOME.

All `agent_admin` scripts and `_common.py` use `_resolve_effective_home()` to bypass sandbox isolation:

1. `CLAWSEAT_REAL_HOME` env override (explicit, highest priority)
2. `AGENT_HOME` env differing from `Path.home()` (injected by `start_seat.py`)
3. `pwd.getpwuid(os.getuid()).pw_dir` — the OS's authoritative answer
4. `Path.home()` as last-resort fallback

**For tests**: set `CLAWSEAT_SANDBOX_HOME_STRICT=1` to force `Path.home()` (sandbox behavior).
**For operators**: `--real-home <path>` on any `agent-admin` subcommand sets `CLAWSEAT_REAL_HOME` for that invocation.
**DO NOT** use `Path.home()` directly in scripts that run inside seats — always go through `_resolve_effective_home()`.

## Seat configuration rule

`koder` is responsible for configuring seats, not just launching them.

- Follow the configuration workflow in [Seat model](references/seat-model.md).
- In short: confirm the runtime choice with the user, apply it through the
  runtime tooling, restart via `scripts/start_seat.py`, then ensure the seat
  re-reads its generated workspace guide and `WORKSPACE_CONTRACT.toml`.
- When possible, stamp the reread with `scripts/ack_contract.py`. This is the
  hook-friendly path for durable contract acknowledgement.
