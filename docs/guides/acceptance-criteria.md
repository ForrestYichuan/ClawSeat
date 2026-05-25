# ClawSeat Acceptance Criteria Authoring Guide

Acceptance criteria govern whether a task can close. The executor (CF042+)
runs mechanical commands, routes reviewer items to the declared reviewer seat,
and batches operator questions for the human operator.

## Three Routes

### mechanical — machine-deterministic shell commands

Use mechanical criteria only when there is a command the acceptance executor can
run and get a binary pass/fail from the exit code alone. Pure review/operator
tasks do not need a fake mechanical command.

```yaml
acceptance_criteria:
  mechanical:
    # String form — simplest
    - "bash -lc 'cd /path && python3 -m pytest tests -q -k \"filter\"'"

    # Dict form — adds description and optional attributes
    - command: "bash -lc 'cd /path && python3 -m pytest tests/focused -q'"
      description: "Focused behavior tests pass."

    # Dict form with diagnostic baseline (see below)
    - command: "bash -lc 'cd /path && python3 -m pytest tests -q'"
      description: "Full-suite baseline probe."
      diagnostic: true
```

**Rule: hard mechanical commands must be machine-deterministic.**
Do not write human OR qualifiers inside mechanical descriptions. The acceptance
executor cannot honor natural-language conditions — only binary exit codes.

```
# WRONG — human qualifier is not enforceable; executor only sees exit code
- "pytest tests -q"   # description with "passes OR planner documents pre-existing failures"

# RIGHT — separate the concerns cleanly
- command: "pytest tests -q -k 'focused_filter'"
  description: "Focused implementation tests pass."
- command: "pytest tests -q"
  description: "Full-suite baseline probe — unrelated pre-existing failures non-blocking."
  diagnostic: true
```

### reviewer — planner or reviewer seat criteria

Reviewer items are dispatched to the team's reviewer seat (or planner self-review
for dev-minimal teams). They remain `PENDING` until the reviewer relays.

```yaml
  reviewer:
    - "Planner confirms the implementation targets framework scope only."
    - "Planner confirms relevant failures still block aggregate acceptance."
```

### operator — human-answered questions

Operator items are batched into a pending-answer file. The human operator
answers them manually before the task can close as PASS.

```yaml
  operator:
    - "Closeout states which paths changed."
    - "Closeout states whether reinstall is recommended."
```

## Diagnostic Baseline (CF043)

A **diagnostic** criterion runs its command and captures output, but a non-zero
exit code is recorded as `diagnostic` (not `fail`) and does NOT cause aggregate
FAIL. Use this for full-suite probes where pre-existing unrelated failures exist.

**Relevant failures must remain non-diagnostic.** Diagnostic is opt-in only.

### Option A: `diagnostic: true` in the brief

```yaml
acceptance_criteria:
  mechanical:
    - command: "bash -lc 'cd /path && python3 -m pytest tests -q -k relevant_filter'"
      description: "Focused implementation tests — relevant failures block."
    - command: "bash -lc 'cd /path && python3 -m pytest tests -q'"
      description: "Full-suite baseline probe — unrelated pre-existing failures non-blocking."
      diagnostic: true
```

### Option B: `--baseline-criteria N` at acceptance-run time

When the brief is already written (memory-owned) and cannot be edited, the
planner can pass criterion indices at run time:

```sh
agent_admin acceptance run \
  --project my-project --team my-team \
  --task-id MY-TASK \
  --actor planner@claude \
  --baseline-criteria 1          # index 1 = full-suite criterion, non-blocking
```

Multiple indices: `--baseline-criteria 1,3`.

### What counts as diagnostic output

In the acceptance log and receipt:
- `result: "diagnostic"` — criterion ran, non-zero exit, non-blocking
- `result: "pass"` — command exited 0 (always a gate regardless of diagnostic flag)
- `result: "fail"` — non-zero exit on a non-diagnostic criterion; blocks aggregate

Aggregate verdict logic:
- `FAIL` if any non-diagnostic mechanical criterion fails
- `PENDING` if mechanical PASS but reviewer/operator items outstanding
- `PASS` only when all three routes are satisfied

## Template Summary

```yaml
acceptance_criteria:
  mechanical:
    # Always: focused, machine-deterministic, binary pass/fail
    - command: "bash -lc 'cd /path && python3 -m pytest tests -q -k \"keyword\"'"
      description: "Focused behavior tests pass — relevant failures block."

    # When needed: baseline probe for full-suite evidence
    - command: "bash -lc 'cd /path && python3 -m pytest tests -q'"
      description: "Full-suite baseline probe."
      diagnostic: true    # non-zero exit → diagnostic, not fail

    # Scope guard (always non-diagnostic)
    - command: "bash -lc 'cd /path && ! git diff --name-only ... | grep product-src'"
      description: "No product source files changed."

  reviewer:
    - "Planner self-review confirms ..."

  operator:
    - "Closeout states ..."
```

## Scope Guard Best Practices (CF049)

Scope guards verify that no product UI/runtime files were changed by a
ClawSeat maintenance task. They must be **machine-deterministic and portable**
— not every host uses the same `grep` implementation.

### Use Python-based scope guards (portable)

```yaml
- command: >-
    bash -lc 'cd /path/to/cartooner && git diff --name-only review/latest...HEAD |
    python3 -c "import sys; bad=[p.strip() for p in sys.stdin
    if p.strip().startswith((\"apps/web/src/\",\"apps/web/electron/src/\"))
    and not p.strip().startswith(\"apps/web/electron/vendor/clawseat/\")];
    print(\"\\n\".join(bad)); sys.exit(1 if bad else 0)"'
  description: "No Cartooner product UI/runtime source files changed."
```

### Avoid grep with regex lookbehind (not portable)

```
# WRONG — grep lookbehind is not portable across all grep implementations
! git diff --name-only ... | grep -E "^apps/web/src/|^apps/web/electron/src/(?!.*vendor/clawseat)"
```

The lookbehind `(?!.*vendor/clawseat)` causes some `grep` versions to emit
"repetition-operator operand invalid" or "invalid syntax" errors. Use the
Python-based pattern instead, which runs with `sys.executable` and is fully
portable.

### Rule: scope guards are always hard-gating

Do **not** apply `diagnostic: true` to scope guards. A changed product file
is a real error, not unrelated baseline evidence.

## Historical Queue Rows (CV002 Pattern)

A `task_claimed` row can remain visibly open in `brief list --all` after
the work is complete if:

- The planner opened a repair child task (`cv002r`) rather than calling
  `agent_admin brief done` or emitting `task_bounced`/`task_failed` on the
  parent (`cv002`).
- The queue state machine correctly records this as `task_claimed` because no
  terminal event (`task_done`, `task_failed`, `task_bounced`) was ever appended
  to the parent row.

**This is expected behavior** (data artifact), not a tooling bug.

- `brief list` (no flag) shows only `task_created` — hides historical rows.
- `brief list --all` shows all non-terminal states including `task_claimed`.
- The repair child's `task_done PASS` does **not** automatically close the
  parent; the parent and child are independent queue entries.
- To close the parent explicitly, use `agent_admin brief done` with memory or
  operator authority; do not edit the queue ledger directly.
