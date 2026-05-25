# Context Management Protocol

Seats use two output markers to signal context lifecycle events to the stop hook.

## [CLEAR-REQUESTED]

Emit this marker as the **last line** of your output when ALL of the following are true:

- Step status has been set to `done`
- All artifacts have been written to disk
- `clear_after_step: true` is set in the current workflow.md step

The stop hook will trigger an external `/clear` on this marker.

## [COMPACT-REQUESTED]

Emit this marker as the **last line** of your output when:

- Context usage exceeds 80% within a step
- `iter > 1` or post-subagent fan-out has made context heavy

The stop hook will trigger `/compact` on this marker.

## Priority rule

If both markers could apply in the same turn:

1. Finish all durable writes first (artifacts, DELIVERY.md, status updates).
2. Emit exactly **one** marker as the final line — prefer `[CLEAR-REQUESTED]` when `clear_after_step: true`.

## Role exceptions

**planner must NOT emit `[CLEAR-REQUESTED]`** — planner maintains cross-step workflow state and decision context across the chain. Clearing planner context loses active task ids, dispatch decisions, blockers, and owner assignments. Planner asks memory to compact with `[memory: compact-me]` in relay text instead of emitting direct stop-hook lifecycle markers.
