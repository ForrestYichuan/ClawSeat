# Workflow Collaboration Protocol

All specialist seats follow this 7-step loop when executing workflow.md steps.

## Queue Wake Hook

In v3 brief mode, `agent_admin brief queue` is the canonical dispatch entry.
After it durably appends `task_created` and writes the brief, it runs a minimal
post-append hook unless `--no-wake` is passed:

```text
task_created -> wake that team's planner
```

The hook only sends a short `[QUEUE-WAKE] <project>/<team> <task_id>` message.
It does not claim tasks, read briefs, inspect workflow steps, wake builders, or
notify memory. If the hook prints `HOOK_WAKE_FAILED`, the task is already
durable but the planner was not notified; the caller must surface the block
instead of silently stopping.

## 7-Step Loop

On receiving a `send-and-verify` notification:

1. Read `~/.agents/tasks/<project>/<task_id>/workflow.md`
2. Find the step where `owner_role=<my-role>`, `status=pending`, and all prereqs are done
3. `agent_admin task update-status <task_id> <step> in_progress --project <p>`
4. Execute the `skill_commands` listed in the step
5. Write artifacts and `DELIVERY.md`
6. `agent_admin task update-status <task_id> <step> done --project <p>`
7. Notify `notify_on_done` roles via send-and-verify

## Pull Fallback (recovery after restart/compact)

If no push notification arrives after idle time, poll for pending work:

```bash
agent_admin task list-pending --project <p> --owner-role <my-role>
```

Claim only steps assigned to your role where prereqs are satisfied.

## Failure Path

On command error or `iter > max_iterations`:

- Do NOT retry silently.
- Notify `notify_on_blocked` roles immediately.
- Record stderr, command output, and other evidence under `artifacts/`.
- Do not proceed to the next step while this step is blocked.
