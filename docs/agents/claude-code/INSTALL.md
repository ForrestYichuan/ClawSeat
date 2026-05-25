# Claude Code Install Driver

This page adapts the generic [Install Agent Prompt](../../INSTALL_AGENT_PROMPT.md)
to Claude Code tool behavior.

## Voice & Tone

Use the same voice contract as the generic prompt: concise, explicit, and
recommendation-driven. Keep `/en`, `/zh`, empty Enter defaults, `详`, and
Recommended★ prompts available throughout the session.

## Confirmation Pattern

Use Claude Code tools in this order:

1. **Read** `docs/INSTALL.md`, `docs/INSTALL_AGENT_PROMPT.md`, and the relevant
   install scripts before changing behavior.
2. **Bash** Step 0 detection with `bash scripts/install.sh --detect-only`.
3. **AskUserQuestion** for each confirmation. Use the rich UI instead of plain
   markdown when asking language, template, project name, summary, run, or
   failure-choice questions.
4. **Bash run_in_background** for the long `install.sh` run so narration and
   monitoring can continue.
5. **Monitor** the background command and summarize each state transition.
6. **TaskCreate** the 11-step progress checklist before the run starts.

Confirmation lines keep this shape:

```text
Recommended★: <choice>
Reason: <one sentence>
Confirm: [Enter=default / change / 详 / cancel]
```

AskUserQuestion reference JSON:

```json
{
  "question": "Choose the ClawSeat template.",
  "header": "Template",
  "options": [
    {"label": "Creative (Recommended)", "description": "Best first install default."},
    {"label": "Engineering", "description": "Adds reviewer for code review lanes."},
    {"label": "Solo", "description": "Legacy alias for v3 MULTI_TEAM_MINIMAL."}
  ]
}
```

## Failure Pattern

When a Bash or Monitor step fails, classify the failure, then offer two or
three concrete fixes. Never kill unrelated tmux or iTerm sessions. If PTY
resources are exhausted, stop and escalate using the project protocol.

## Startup Trust And Permission Prompts

Claude Code v2.1+ may show normal startup trust or permission prompts while a
new seat is being attached. Confirm them directly; do not report them as
`install.sh` bugs.

Common normal prompts:

1. `Yes, I trust this folder` workspace trust prompt -> choose `1` or press Enter.
2. `Bypass Permissions` permission-level prompt with default/strict/bypass
   options -> choose bypass, usually `1` or Enter.
3. `Allow this skill to read...` first-use skill permission prompt -> choose
   Yes or `1`.

Rule: if it appears during startup and includes `trust`, `permission`,
`bypass`, or `allow`, treat it as normal Claude Code authorization and confirm
it. Real failures are process crashes, Python tracebacks, API 401/secret
missing errors, absent tmux sessions, or windows that never open.

## detect_all JSON Reference

Read Step 0 output as JSON and keep it available for later decisions:
OAuth state, PTY state, branch state, existing projects, and timestamp.
