---
name: clawseat-roster-admin
description: >
  Controlled roster-change gate for ClawSeat project-memory. Use when an
  operator asks memory to add one builder/reviewer/patrol seat to an existing
  team, or to add one new subteam to an existing v3 multi-team project. Covers
  proposal, operator approval, allowed execution primitives, workspace refresh,
  launch/rehearsal, and evidence recording. Do NOT use for unsolicited scaling,
  direct TOML edits, deleting seats, changing secrets, or bypassing operator
  approval.
---

# ClawSeat Roster Admin

Use this skill only from the project-level memory seat after the operator asks
for a roster change or approves a memory proposal.

## Authority Model

Memory may:

1. Scan the repo and current project roster.
2. Recommend adding a single seat or a subteam.
3. Write a reviewable proposal.
4. Ask for explicit operator approval.
5. After approval, invoke an approved admin action.
6. Refresh workspaces, verify visibility, and run post-spawn rehearsal.

Memory must not:

- Edit `project.toml`, dynamic profiles, `[teams]`, `[seat_roles]`, or
  `[seat_overrides]` by hand.
- Start a new seat before the operator approves the exact roster change.
- Add a fourth builder to a team.
- Add memory seats inside subteams.
- Add `patrol` seats outside the `quality-docs` team.
- Treat `agent_admin.py engineer create` as sufficient for v3 multi-team
  rosters; it does not update `[teams]` ownership metadata.

## Choose The Change Type

Use **single-seat hot-plug** when:

- The existing team already owns the path/module.
- The requested role is `builder`, `reviewer`, or `patrol`.
- Builder count remains <= 3.
- Adding a second or third builder has a reviewer in the same team, or the
  approved change first adds the reviewer.
- Patrol is for `quality-docs`.

Use **new subteam** when:

- The work crosses a new module/layer boundary.
- A team would need a fourth builder.
- Ownership paths do not naturally belong to an existing team.
- A new planner is needed to own workflow design for that module/layer.

## Proposal Files

For single-seat changes, write:

```text
~/.agents/tasks/<project>/_roster-changes/<YYYYMMDD-HHMMSS>-add-seat-<team>-<role>.md
```

Include:

- project, team, role, instance, generated seat id
- tool/auth/provider/model
- reason and ownership/risk
- exact effects on profile/project metadata
- `TEAM_OWNERSHIP.md` update plan for stable team/builder split
- whether to launch tmux after creation
- verification plan

For subteams, use `multi-team-intake` proposal YAML:

```text
~/.agents/tasks/<project>/_config-proposals/<team>__proposed.yaml
```

Only rename or rewrite to `<team>__approved.yaml` after operator approval.

## Approved Execution Primitives

### Add A Subteam

After approval:

```bash
bash "$CLAWSEAT_ROOT/scripts/install_multi.sh" --project <project> --upgrade-team <team> --dry-run
bash "$CLAWSEAT_ROOT/scripts/install_multi.sh" --project <project> --upgrade-team <team>
python3 "$CLAWSEAT_ROOT/core/scripts/agent_admin.py" engineer regenerate-workspace --all-seats --project <project> --yes
```

Only launch newly added seats when the operator approved launch:

```bash
bash "$CLAWSEAT_ROOT/scripts/restart-seat.sh" <project> <seat> --no-window
```

### Add One Seat To An Existing Team

Preferred primitive, when present:

```bash
python3 "$CLAWSEAT_ROOT/core/scripts/roster_admin.py" add-seat \
  --project <project> \
  --team <team> \
  --role <builder|reviewer|patrol> \
  --instance <instance> \
  --tool <claude|codex|gemini> \
  --auth-mode <oauth|oauth_token|api> \
  --provider <provider> \
  --model <model-or-empty> \
  --refresh \
  --start
```

If `core/scripts/roster_admin.py` is not present, stop and ask the operator to
use the Cartooner AgentLauncher project config modal's "新增 Seat" action. Do not
fall back to manual TOML edits.

## Verification

After any roster change:

```bash
python3 "$CLAWSEAT_ROOT/core/scripts/agent_admin.py" project seat list --project <project>
python3 "$CLAWSEAT_ROOT/core/scripts/agent_admin.py" engineer regenerate-workspace <seat> --project <project> --yes
```

Update the current-project ownership doc:

```text
~/.agents/tasks/<project>/TEAM_OWNERSHIP.md
```

Record only stable team responsibilities, seat ids, builder purpose/capabilities,
ownership paths, and boundaries. Do not record secrets, auth/model runtime,
tmux sessions, or per-task assignments.

Then run the memory post-spawn chain rehearsal for each launched seat. If any
seat fails self-report, workspace render, handoff, or notification verification,
mark the roster change `blocked`, do not dispatch real work to that seat, and
ask for operator guidance.

## Final Report

Report:

- changed project/team/seat ids
- command(s) run
- `TEAM_OWNERSHIP.md` update summary
- workspace refresh result
- launch result or "not launched"
- rehearsal result
- remaining risks

Write the decision/evidence to memory via the normal memory reporting channel.
