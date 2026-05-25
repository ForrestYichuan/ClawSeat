# Planner self-closeout protocol

This protocol defines the atomic planner relay that closes a builder task and
immediately hands the chain back to memory.

## Trigger

Run `complete_handoff.py` with:

```bash
complete_handoff.py --source <exact planner seat> --target memory --task-id <id> --status completed --verdict <V> --notify
```

The durable receipt is still the primary relay path. `send-and-verify.sh` is
wake-up only and may follow when a separate nudge is needed.

## Atomic order

1. Rename any incoming `/<task_id>__*__planner.json` receipt to `.json.consumed`.
2. Write `planner/DELIVERY.md`.
3. Persist the planner→memory receipt.
4. Notify memory.

If no incoming builder→planner receipt exists, log that the rename was skipped
and continue.

## Delivery metadata

When planner relays to memory, `planner/DELIVERY.md` should carry the branch,
commit, sweep count, and the one-line summary extracted from builder DELIVERY.

## Review/latest validation

Each ClawSeat project owns one project-local validation worktree for
`review/latest`; never share it across projects. Builders never merge
`review/latest` or `main`, and planners never merge directly to `main`.
Planner closeout reports branch/commit evidence, tests, and blocker/conflict
files; it does not merge `review/latest`.

Memory integrates accepted planner deliveries into that project's own
`review/latest` worktree. On conflict: stop and report; do not force-push and
do not modify `main`.

Memory is the final main-integration boundary: only after explicit user
confirmation may memory merge from that project `review/latest` worktree to
`main`. Memory closeout records user confirmation, `review/latest` hash, and
main merge hash or blocker. Memory also owns desktop launch scripts so user
review opens this project's `review/latest` worktree, not `main`, a shared
global worktree, or a stale tmp worktree.

## Escape hatch

`--enforce-planner-self-closeout=false` bypasses the rename and the
planner/DELIVERY write. Use it only when you explicitly accept drift between
`.consumed` receipts and `planner/DELIVERY.md`.
