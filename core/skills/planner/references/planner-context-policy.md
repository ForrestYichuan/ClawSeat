# Planner Context Policy

Planner is the workflow orchestrator and keeps cross-task decision context.

- `[CLEAR-REQUESTED] FORBIDDEN` for planner.
- Planner uses `[memory: compact-me]` in the memory relay text when planner
  context should be compacted.
- Planner does not emit direct stop-hook lifecycle markers.
- Ask memory to compact at a cross-phase boundary or when context usage is
  heavy.
- Compact summaries must preserve active task ids, dispatch decisions,
  blockers, owner assignments, and pending reviews.
