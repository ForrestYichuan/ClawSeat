# ClawSeat v0.7 Install Playbook

> 🇨🇳 [中文版本 / Chinese version](INSTALL.zh-CN.md)

## TL;DR

New project? Run:

```bash
bash ~/ClawSeat/scripts/install.sh --project <name>
```

That is the one canonical command. Everything else is internal plumbing.

> Note: In sandbox environments (for example, agent-launcher), `~` may
> resolve to a non-standard path. Use an absolute path such as
> `/Users/ywf/coding/ClawSeat/` or your actual install location when commands
> involving `~/ClawSeat/` behave unexpectedly.

> Target executor: Claude Code (agent, not human).
> This file is the install SSOT. `scripts/install.sh` owns host bootstrap and
> runtime startup; once memory is prompt-ready, memory owns Phase-A.
> Post-install extensions (koder overlay, new projects) are covered in §4–§5.

## Overview

| Step | Executor | What happens |
|------|---------|--------------|
| 0 Agent kickoff | operator | paste the kickoff prompt (§0) into a fresh Claude Code / Codex / Gemini session |
| 1 Prerequisites | operator | `git clone` + `cd ~/ClawSeat` |
| 2 `install.sh` | script (auto) | host deps, env scan, provider pick, workers window, memories window, bypass flush |
| 3 Phase-A | memory CLI | B0–B7 interactive bootstrap |
| 4 (optional) Koder overlay | memory or operator | `scripts/apply-koder-overlay.sh` — pick an OpenClaw agent to become the Feishu reverse-channel koder |
| 5 (optional) Additional projects | operator | `bash scripts/install.sh --project <name>` |

> **Why `install.sh` instead of `agent_admin` or `agent-launcher.sh` directly?**
> `install.sh` is the L1 user-facing entry for fresh-machine bootstrap
> (host deps + scan + provider pick + project memory + workers window + brief render).
> For per-seat operations on an existing project, use `agent_admin session
> start-engineer`. For executing a single seat process with sandbox HOME,
> the system internally calls `agent-launcher.sh` (you do not). See
> [docs/ARCHITECTURE.md §3z](ARCHITECTURE.md#seat-lifecycle-entry-points-v07-pyramid)
> for the full layering.

## AI-Native Install Decision Tree

This section is the agent-facing install dialogue contract. Run Step 0
detection once, then make only five planned decisions: language, template,
project name, summary, and run. At any prompt, `/en` and `/zh` switch language,
empty Enter accepts the default, and `详` gives a short explanation of roughly
150 words without external links.

### Step 0 — Language, Detection, And Start Gate

**WHAT**: silently run `bash scripts/install.sh --detect-only --force-repo-root <CLAWSEAT_ROOT>` and summarize the `detect_all` JSON before asking any setup question.

```json
{
  "oauth": {"claude": "oauth|api_key|missing", "codex": "oauth|api_key|missing", "gemini": "oauth|api_key|missing"},
  "pty": {"used": 0, "total": 256, "warn": false},
  "branch": {"branch": "main", "warn": false},
  "existing_projects": [],
  "timestamp": "2026-04-29T00:00:00Z"
}
```

**WHY default**: Recommended★: continue in the operator's current language. Reason: it avoids one extra question while still allowing `/en` or `/zh` at any time.

**CONFIRM**: `可以开始吗? [回车=继续 / 详 / 取消]`

**ON-FAIL**: show the failing detector, then offer 2-3 fixes such as `--force-repo-root <path>`, re-login to the missing tool, or stop and escalate for PTY exhaustion. Do not kill sessions.

### Step 1 — Template Decision

**WHAT**: choose `clawseat-engineering`, `clawseat-creative`, or `clawseat-solo`. Use
`detect_template_from_name <project>` once the project intent is known. If the intent
is unclear, default to `clawseat-engineering`.

**WHY**: Default to `clawseat-engineering` because it covers the majority of code-shipping work (memory + planner + builder + reviewer + patrol). For creative work (image / video / audio / storyboard) bound to cartooner skills, choose `clawseat-creative`.

**CONFIRM**: `[回车=默认 / 1 engineering / 2 creative / 3 solo / 详 / 取消]`

**ON-FAIL**: if the operator is unsure, give two examples per template and keep the default; if the template file is missing, offer `git status`, `git pull`, or `--force-repo-root <path>`.

### Step 2 — Project Name Decision

**WHAT**: propose a lowercase `^[a-z0-9-]+$` project name. Treat `existing_projects` from `detect_all` as an **AVOID list**, never as a source of recommended names. Recommendation priority is: operator goal first, repo directory name second, generated unique name with timestamp third. Always check collisions against `existing_projects` before confirming.

**WHY default**: Recommended★: use the operator goal when provided, otherwise the repo directory name if it is not in `existing_projects`, otherwise a generated unique name like `<repo>-20260429-1251`. Reason: it prevents reusing `install` or another existing project by accident.

**CONFIRM**: `[回车=默认 / 输入新项目名 / 详 / 取消]`

**ON-FAIL**: if the name is invalid or already exists, offer a normalized slug, `--reinstall <project>` only when the operator explicitly wants to replace that project, or a generated unique suffix with timestamp.

### Step 3 — Summary, Run, And Progress

**WHAT**: show one summary with language, template, project name, repo root, branch warning, OAuth warnings, and the exact command. Then ask for the run confirmation.

**WHY default**: Recommended★: run the generated command. Reason: all detection has already completed and the operator has seen the mutable choices.

**CONFIRM**: `[回车=运行 / 修改摘要 / 详 / 取消]`

**ON-FAIL**: classify the failure and present specific fix options. Use `⚠️` for recoverable prompts, `❌` for failed commands, and `⏭️` for skipped optional steps; do not dump raw stderr alone.

Narrate the run as exactly 11 progress steps:

1. 🟢 Parse flags and resolve ClawSeat root.
2. 🟢 Run preflight and environment scan.
3. 🟢 Select template.
4. 🟢 Confirm project name and paths.
5. 🟢 Select provider or OAuth mode.
6. 🟢 Render memory bootstrap brief.
7. 🟢 Launch primary memory seat.
8. 🟢 Write project registry and local config.
9. 🟢 Open workers or solo window layout.
10. 🟢 Write operator guide and kickoff.
11. 🟢 Verify Phase-A handoff; mark optional skips with ⏭️, warnings with ⚠️, failures with ❌.

## 0. Agent kickoff prompt

Paste the prompt below into a fresh Claude Code / Codex / Gemini session to
kick off a ClawSeat install. The prompt is intentionally minimal — it tells
the agent *where* to find the playbook and *which preferences to surface*,
not how to execute each step. Every step belongs to this document
(`docs/INSTALL.md`), not the prompt.

~~~text
ClawSeat install — Agent kickoff prompt

You are invoked to install ClawSeat on this machine.

1. If `~/ClawSeat` does not exist, clone it first:
   `git clone https://github.com/KaneOrca/ClawSeat ~/ClawSeat`.
   Then read `~/ClawSeat/docs/INSTALL.md` (your <CLAWSEAT_ROOT>/docs/INSTALL.md)
   and execute it end-to-end.

   Sandbox note: if `~` resolves inside an agent runtime, use the absolute
   path `/Users/ywf/coding/ClawSeat/` or your actual ClawSeat checkout path.

2. Install preferences (operator fills these in before running):
   - Project name:           <PROJECT_NAME>       (e.g. "install" for first-time setup)
   - Repo root for seat cwd: <REPO_ROOT>          (default: CLAWSEAT_ROOT)
   - Seat harness:           <HARNESS_PREF>       (e.g. "default", "all seats claude+api+minimax")
   - Feishu mode:            <FEISHU_MODE>        ("enabled" | "disabled via CLAWSEAT_FEISHU_ENABLED=0")
   - Koder overlay:          <KODER_OVERLAY>      ("skip" | "apply tenant=<name>")

3. Consent & capability disclosure. The install playbook reads sensitive local
   state: shell environment, credential files (~/.claude/*, ~/.codex/*, macOS
   keychain entries, lark-cli session), provider API keys, and writes host
   artifacts under ~/.agents and ~/.openclaw. You are explicitly authorized to
   use platform capabilities — Bash, file Read/Write, `request_access`,
   `WebFetch`, MCP servers — as the playbook requires. Before first invoking
   each *new category* of access (first shell command, first credential read,
   first network fetch, first MCP/app grant), tell the operator in one short
   line what you are about to access and why. If the operator declines, stop
   and ask for an alternative path — do not skip silently.

4. Stop and ask the operator whenever a step is unclear, a prompt looks
   unfamiliar, or a command fails. Do NOT silently work around undocumented
   edge cases — every friction point is a signal the playbook must improve.

5. After install.sh exits, DO NOT end your session. Hand off Phase-A manually:

   (a) RELAY THE BANNER. install.sh ends with a "ClawSeat install complete"
       banner. Copy it verbatim to the operator. install.sh's zero exit code is
       NOT the final completion signal — the banner is. Never skip this.

   (b) READ THE OPERATOR GUIDE the banner points to:
           cat ~/.agents/tasks/<PROJECT_NAME>/OPERATOR-START-HERE.md
       If the operator reads Chinese, relay its Chinese instructions directly.

   (c) CAPTURE the memory pane and classify its state:
           tmux capture-pane -t '<PROJECT_NAME>-memory' -p | tail -15

       Three possible states — classify before doing anything:

       State A — PHASE-A ALREADY RUNNING or KICKOFF IN-FLIGHT
       (the memory seat already received the kickoff):

         A1. Phase-A has produced visible reply content:
               - "B0" / "已读取 brief" / env_scan output
             (Memory consumed the brief and started Phase-A steps.)

         A2. Claude Code is actively processing the just-delivered kickoff
             (matches the runtime detector
             the active-response detector in `scripts/install.sh`
             — this is the detector's exact set):
               - "Thinking..." / "Shell awaiting input"
               - spinner glyphs:  ✶  ✻  ✢  ✳  ✽  ⏺
               - "Read N file" / "Read N files"
             (Kickoff delivered; response in progress. Don't double-send.)

         If ANY of A1 or A2 is visible, DO NOT paste again — pasting
         duplicates the kickoff and creates double-input. SKIP step (d),
         go to (e).

       State B — BLOCKED ON A CONFIRMATION SCREEN (pane NOT ready; auto-send
       was intentionally skipped by the operator-wait detector):
         Pane shows any of —
         - "WARNING: Claude Code running in Bypass Permissions mode"
             → operator presses Enter to select "2. Yes, I accept"
         - "Do you trust the files in this folder" / "Trust folder"
             → operator selects Yes
         - "Browser didn't open? Use the url below to sign in" / "Paste code here"
             → operator completes OAuth in browser
         - "OAuth error:" / "Login successful. Press Enter to continue"
             → operator presses Enter
         - "Accessing workspace:" / "Quick safety check:"
             → operator presses Enter / confirms per the Claude Code prompt
         After clearing, re-capture and re-classify.

       State C — IDLE INPUT PROMPT (`> ` or `❯ ` with no recent activity):
         Pane is ready but kickoff was not delivered (auto-send failure).
         Proceed to step (d) to paste manually.

   (d) PASTE MANUALLY (only if step (c) classified as State C):
       Operator must paste the Phase-A kickoff prompt (shown at the bottom of
       the banner) into the memory pane. Walk them through copy-paste — don't
       assume. After paste + Enter, re-capture the pane to confirm Phase-A has
       started (look for "B0" / "已读取 brief" in the output).

   (e) VERIFY Phase-A is observably running (works for all three states):
       tmux capture-pane -t '<PROJECT_NAME>-memory' -p | tail -10 should show
       memory actively processing B0 (env scan / overrides read / provider
       table). Only then proceed to optional steps (koder overlay, additional
       projects) or the final report.

6. On completion (Phase-A observably running), report to the operator:
   - install.sh exit status
   - tmux session count (expect memory + configured worker seats + monitor)
   - memory seat cwd (should equal <REPO_ROOT>)
   - memory pane confirmed ready + Phase-A kickoff confirmed received
   - koder overlay outcome (if requested)
   - every friction point encountered, with exact command + stderr + the
     ambiguous doc line
~~~

### Why consent-first

ClawSeat reads credentials, API keys, and the shell environment to pick the
right provider and render the bootstrap brief. It also writes persistent host
state under `~/.agents/` (project profiles, sandbox HOMEs, identity tokens),
touches `~/.openclaw/` (for koder overlay), and optionally installs a macOS
LaunchAgent for memory patrol. An operator installing ClawSeat should see
*what is scanned* and *what is persisted* before it happens. Consent-first is
the contract, not an afterthought — every new category of access is declared
up front, and silent access to credentials or the network is a playbook bug.

### Why Phase-A handoff matters (step 5)

`install.sh` prepares the Phase-A kickoff prompt, but the invoking agent still
has to confirm the memory pane is ready and the kickoff was delivered. Claude
Code v2.1.118+ may surface a Bypass Permissions confirmation screen, Trust
Folder prompt, or OAuth flow on first boot. Treating `install.sh`'s exit as
"done" without confirming Phase-A started drops the memory seat on the floor
and Phase-A never begins — the #1 reported install failure mode. Step 5 is
non-negotiable.

### Why minimal

Every step-by-step detail baked into the prompt is a step the agent stops
reading in the playbook. A terse kickoff prompt forces the agent to treat
`docs/INSTALL.md` as the source of truth — which is also the only way surfaces
like "undocumented friction" and "stale doc line X" can be detected and fixed.
If the playbook is missing a step that seems essential, that is a doc issue,
not a prompt issue.

## Broadcast model (seat-by-seat)

Hook / CLI-first, Feishu is **write-only async notification**. ClawSeat does not subscribe to Feishu.

| Seat | Hook policy | Output channel |
|------|-------------|----------------|
| planner | Stop-hook every turn → `lark-cli msg send` | structured summary to Feishu group (≤500 chars; never raw transcript) |
| memory | skill-driven + Stop-hook | optional summary broadcast; durable memory delivery via `memory_deliver.py` when `[DELIVER:seat=<X>]` marker present |
| other seats (builder / writer / visual / reviewer / patrol) | none | CLI only, visible in their pane |

The `koder` overlay (§4) is the inbound channel: operator messages on Feishu → OpenClaw-side koder → `tmux send-keys` into a ClawSeat seat.

---

## 1. Prerequisites

Window mode contract:

- Standalone ClawSeat uses native iTerm workers/memories windows by default, so macOS + iTerm2 + the `iterm2` Python module are required for that visual mode.
- Cartooner-integrated or embedded-terminal ClawSeat should pass `--no-window`; the install still launches canonical tmux seats, but skips native iTerm windows and their bootstrap hard checks.
- `--mode multi` is the v3 multi-team/profile-render path. It renders approved team proposals and workspace skeletons, and does not open iTerm windows.

```bash
git clone <repo-url> "$HOME/ClawSeat"
cd "$HOME/ClawSeat"
export CLAWSEAT_ROOT="$PWD"
export PROJECT_NAME=install
```

Verify:

```bash
test -e "$CLAWSEAT_ROOT/.git" && test -f "$CLAWSEAT_ROOT/scripts/install.sh"
```

Failure:

```text
INSTALL_BROKEN: repository missing or scripts/install.sh not found
```

---

## 2. Run `install.sh` (automatic bootstrap)

```bash
cd "$CLAWSEAT_ROOT"
bash scripts/install.sh
```

> **交互模式（kind-first）**：如果未传 `--template` 和 `--project`，install.sh 会先问项目类型（创作 / 工程 / solo），再根据类型显示对应 placeholder 询问项目名。CI / sandbox 环境（非 TTY）不会进入交互提示；请显式传 `--project <name> --template <kind>`。

Dry-run preflight:

```bash
bash scripts/install.sh --dry-run
```

Non-interactive provider shortcuts:

```bash
bash scripts/install.sh --provider minimax
bash scripts/install.sh --provider minimax --api-key sk-cp-...
bash scripts/install.sh --provider anthropic_console --api-key sk-ant-...
bash scripts/install.sh --base-url https://api.example.invalid --api-key sk-test --model claude-sonnet
```

Additional flags:

```bash
# Install for a different project's repo
bash scripts/install.sh --project myproject --repo-root /path/to/myproject

# Override the ClawSeat install code root on multi-worktree machines
bash scripts/install.sh --project myproject --force-repo-root /Users/you/ClawSeat

# Choose a non-Claude primary memory tool
bash scripts/install.sh --memory-tool codex --memory-model gpt-5.2
bash scripts/install.sh --memory-tool gemini

# Force every API-auth worker seat onto the same provider family
bash scripts/install.sh --all-api-provider minimax

# Install the optional patrol cron entries
bash scripts/install.sh --enable-auto-patrol

# Mirror every bundled ClawSeat skill into all supported tool homes
bash scripts/install.sh --load-all-skills

# Disable Feishu notifications (no lark-cli required)
CLAWSEAT_FEISHU_ENABLED=0 bash scripts/install.sh --project myproj

# Cartooner / embedded-terminal mode: tmux seats, no native iTerm windows
bash scripts/install.sh --project myproj --template clawseat-creative --provider minimax --no-window

# v3 multi-team dry-run after memory has written approved team proposals
bash scripts/install.sh --mode multi --project myproj --teams core,content --dry-run

# Minimal v3 project group: legacy solo alias seeds MULTI_TEAM_MINIMAL proposals
bash scripts/install.sh --project myproj --template clawseat-solo --dry-run

# Forget remembered per-seat harness choices from a previous run
bash scripts/install.sh --reset-harness-memory
```

### CLI Reference

| Flag | Meaning |
|------|---------|
| `--project <name>` | Install or reinstall a named ClawSeat project. Defaults to `install`. |
| `--repo-root <path>` | Set the target project repository used as seat cwd. |
| `--force-repo-root <path>` | Override the ClawSeat install code root when auto-detecting multiple worktrees is wrong. |
| `--template <clawseat-engineering\|clawseat-creative\|clawseat-solo>` | Select the install template. `clawseat-solo` is now a legacy alias for v3 `MULTI_TEAM_MINIMAL`, not a separate single-mode roster. |
| `--memory-tool <claude\|codex\|gemini>` | Override the primary memory seat tool. Non-Claude tools skip Claude provider selection. |
| `--memory-model <model>` | Set the memory model when the selected memory tool supports an explicit model. |
| `--provider <mode\|n>` | Select the memory-seat provider by detected candidate number or mode. |
| `--all-api-provider <provider>` | Override every API-auth worker seat provider. Supported modes match the parser: `minimax`, `deepseek`, `ark`, `xcode-best`, `anthropic_console`, `custom_api`. |
| `--base-url <url> --api-key <key> [--model <name>]` | Force a custom Claude-compatible API provider for the memory seat. |
| `--api-key <key> [--model <name>]` | Used with `--provider minimax\|deepseek\|ark\|xcode-best\|anthropic_console` to force that provider explicitly. |
| `--reinstall` / `--force` | Rebuild an existing project instead of exiting at `phase=ready`. A following bare word is treated as the project name for compatibility. |
| `--uninstall <project>` | Remove the project from the projects registry. |
| `--enable-auto-patrol` | Install optional daily/weekly patrol cron entries. Without it, stale patrol LaunchAgents are removed during preflight. |
| `--load-all-skills` | Install all bundled ClawSeat skills for non-Claude tools too. Claude always receives the full set. |
| `--dry-run` | Print planned actions without mutating host state where supported. |
| `--detect-only` | Print one `detect_all` JSON environment summary and exit before install side effects. |
| `--no-window` | Skip native iTerm workers/memories windows and bootstrap iTerm hard checks. Tmux seats still launch and can be attached from embedded terminals. |
| `--reset-harness-memory` | Delete remembered per-seat harness choices and exit. |
| `--help` / `-h` | Print the parser-owned usage line. |

Available templates:

- `clawseat-engineering`: 5-seat engineering template (memory + planner + builder + reviewer + patrol), where reviewer now merges QA + visual review. Bound to gstack skills.
- `clawseat-creative`: 5-seat cartooner-bound creative team (memory + writer + builder-image + builder-av + patrol). Vision Steward + Story Specialist + Image Specialist + AV Cinematographer (Gemini for YouTube reference learning) + Asset Guardian; all seats use cartooner skills via cartooner-harness protocol layer.
- `clawseat-solo`: legacy alias for v3 `MULTI_TEAM_MINIMAL`; seeds one or more `planner+builder` subteams plus `quality-docs` under one project memory.

### Provider Selection — CLI Flag Mapping

When running in non-interactive environments (agent-launcher, CI, scripts),
use explicit flags to skip the interactive provider menu. Numeric menu
choices are detected dynamically from local credentials; only the mode names
below are stable CLI contracts:

| Stable mode | Description | CLI flag |
|---|---|---|
| `anthropic_console` | Claude memory with Anthropic Console API key | `--provider anthropic_console --api-key <key>` |
| `oauth_token` | Claude memory with Claude Code OAuth token | `--provider oauth_token` |
| `oauth` | Claude memory with host Claude OAuth | `--provider oauth` |
| `minimax` | MiniMax API | `--provider minimax --api-key <key>` |
| `deepseek` | DeepSeek API | `--provider deepseek --api-key <key>` |
| `ark` | Ark API | `--provider ark --api-key <key>` |
| `xcode-best` | Xcode-best Claude-compatible API | `--provider xcode-best --api-key <key> [--model <name>]` |
| `custom_api` | Custom Claude-compatible endpoint | `--provider custom_api --base-url <url> --api-key <key> [--model <name>]` |
| `gemini` memory tool | Gemini OAuth primary memory | `--memory-tool gemini` |
| `codex` memory tool | Codex OAuth primary memory | `--memory-tool codex [--memory-model <model>]` |

The parser-owned provider behavior lives in
`scripts/install/lib/provider.sh::select_provider()`; prefer those mode names
over older aliases such as `anthropic`, `claude_code`, `gemini_oauth`,
`codex_oauth`, or `custom`.

Security note: `--api-key` is visible in `ps` output and shell history. Prefer:

```bash
export ANTHROPIC_BASE_URL=https://api.example.invalid
export ANTHROPIC_API_KEY=sk-test
bash scripts/install.sh --provider custom_api
```

Use `--base-url + --api-key` only for CI / agent automation / no-env / no-tty cases.

### Debugging Pane Content (tmux)

When a pane was just launched, `tmux capture-pane -t <session> -p` may return
empty output because the visible buffer has not filled yet. Capture recent
scroll history instead:

```bash
# Recommended: capture last 50 lines including scroll history
tmux capture-pane -t <session>:<window>.<pane> -p -S -50

# Capture the entire scrollback buffer
tmux capture-pane -t <session> -p -S - -E -
```

This is especially important when checking whether Claude Code shows a
"Trust folder" or "Quick safety check" prompt after first launch.

What `install.sh` does in order:

1. Parse flags, resolve the real user HOME, optionally pick the freshest
   ClawSeat worktree, and load the install library modules from
   `scripts/install/lib/`.
2. Resolve Python ≥3.11 before importing anything that needs `tomllib`.
3. Resolve the project template and roster. `clawseat-engineering` renders
   `memory, planner, builder, reviewer, patrol`; `clawseat-creative` renders
   `memory, writer, builder-image, builder-av, patrol`; `clawseat-solo`
   renders only `memory, builder, planner`.
4. Run legacy path migration and seat liveness reconciliation.
5. Verify host dependencies and run
   `core/skills/memory-oracle/scripts/scan_environment.py --output ~/.agents/memory/`
   → produces `machine/{credentials,network,openclaw,github,current_context}.json`.
6. Select the primary memory provider:
   `--memory-tool codex|gemini` skips Claude provider selection; Claude memory
   builds candidates from `credentials.json` in this order: `MINIMAX_API_KEY`;
   `ANTHROPIC_AUTH_TOKEN` classified by base URL as minimax, deepseek,
   xcode-best, or custom; `ANTHROPIC_API_KEY`; `CLAUDE_CODE_OAUTH_TOKEN`;
   `DASHSCOPE_API_KEY`; `ARK_API_KEY`; host OAuth. Explicit `--base-url
   --api-key`, `--provider <mode>`, and `--provider <n>` override the detected
   default. If no candidate is usable in a non-tty run, install exits with
   `NON_TTY_NO_PROVIDER`.
7. Write the selected memory provider env under
   `~/.agents/tasks/<project>/memory-provider.env`.
8. Render the bootstrap brief under
   `~/.agents/tasks/<project>/patrol/handoffs/memory-bootstrap.md` with
   template, primary-seat, and pending-seat substitutions.
9. Launch only `<project>-memory` via `core/launchers/agent-launcher.sh`
   with sandbox HOME isolation.
10. Bootstrap or migrate the project profile with `agent_admin project bootstrap`,
    seed secrets, install skill mirrors, privacy hooks, and project registry
    entries.
11. Launch the per-project workers window via `core/scripts/iterm_panes_driver.py`
    (iTerm2 native panes — **not** nested tmux): the planner pane is primary on
    the left; the other workers attach once memory spawns them.
12. Ensure the shared memories window exists with one tab per project memory
    session.
13. Write the Phase-A kickoff prompt under `patrol/handoffs/` and print the
    operator banner with confirm-then-dispatch choices.

Verify:

```bash
test -d ~/.agents/tasks/install/patrol/handoffs
for f in credentials network openclaw github current_context; do
  test -f ~/.agents/memory/machine/$f.json || echo "MISSING $f"
done
for s in install-memory; do
  tmux has-session -t "$s" || echo "MISSING $s"
done
for s in install-planner install-builder install-reviewer install-patrol install-writer install-visual; do
  tmux has-session -t "$s" 2>/dev/null && echo "UNEXPECTED $s"
done
```

Failure codes:

```text
MISSING_PYTHON311: no supported Python >= 3.11 found
INVALID_PYTHON_BIN: PYTHON_BIN was set but is missing or too old
PREFLIGHT_FAILED: bootstrap preflight returned HARD_BLOCKED or failed
ENV_SCAN_INCOMPLETE: expected ~/.agents/memory/machine/*.json missing
NON_TTY_NO_TEMPLATE: project/template prompt needs a tty; pass --project and --template
NON_TTY_NO_PROVIDER: provider selection needs a tty; pass --provider or API flags
INTERACTIVE_REQUIRED: legacy provider prompt tty requirement
PROVIDER_NOT_FOUND: requested provider mode/candidate was not detected
ITERM_DRIVER_FAILED: iTerm pane driver timed out or exited non-zero
ITERM_LAYOUT_FAILED: iTerm pane driver returned a non-ok layout payload
```

`install.sh` ends by printing:

```text
ClawSeat install: memory is prompt-ready.
Paste the prompt shown above into the memory pane.
Workers window: clawseat-install-workers
Memories window: clawseat-memories
```

---

## 3. Operator pastes prompt; memory runs Phase-A

Paste (exact text from `install.sh` output):

```text
读取 bootstrap brief，开始 Phase-A。每步向我确认或报告。
```

Memory executes Phase-A in order:

| Token | Action | Success criterion |
|-------|--------|-------------------|
| B0-env-scan-analysis | Read `~/.agents/memory/machine/*.json`. Summarize which harnesses (claude-code / codex / gemini / minimax / dashscope) are usable and recommend the cheapest viable provider mix. Explain the rationale. | User confirms or supplies custom plan; memory writes a provider decision under `~/.agents/tasks/install/` |
| B1-read-brief | Parse the rendered bootstrap brief. | Brief understood with no missing variables. |
| B2-verify-memory | `tmux has-session -t <project>-memory`; relaunch once if dead. | Memory seat alive. |
| B2.5-bootstrap-tenants | `python3 core/scripts/bootstrap_machine_tenants.py ~/.agents/memory/` — populates `~/.clawseat/machine.toml [openclaw_tenants.*]` from `machine/openclaw.json.agents`. | `list_openclaw_tenants()` returns non-empty (if OpenClaw installed). |
| B3-verify-openclaw-binding | Read `~/.openclaw/workspace.toml` if present. | Project field matches or step is skipped with warning. |
| B3.5-launch-engineers | **Interactive, one-by-one**. For each worker from the selected v2 template (`clawseat-engineering` uses `planner`, `builder`, `reviewer`, `patrol`; `clawseat-creative` uses `writer`, `builder-image`, `builder-av`, `patrol`) ask operator for provider, optionally `session switch-harness`, then `session start-engineer`. `clawseat-solo` does not use this path anymore; it delegates to v3 multi-team minimal. | Each `install-<seat>` is alive and attached, or v3 profile skeleton rendered for the solo alias. |
| B5-verify-feishu-binding | Read project binding metadata from `~/.agents/projects/install/project.toml` / `project-local.toml`. | `feishu_group_id` present *or* operator explicitly skips (CLI-only mode). |
| B6-smoke | If `feishu_group_id` set, memory triggers planner to do one broadcast turn → `lark-cli` broadcasts a structured summary to the group. If skipped, memory runs CLI-only smoke (writes a test file, verifies via grep). | Smoke result recorded in `STATUS.md`. |
| B7-write-status-ready | Write `~/.agents/tasks/install/STATUS.md`. | `phase=ready`, `providers=<memory + workers>`. |

If the provider menu appears in smoke / CI / sandbox runs, use `--provider 1` or `CLAWSEAT_INSTALL_PROVIDER=1` to select the first detected candidate without a tty. `--provider minimax|ark|anthropic_console|custom_api` still forces a specific mode when you already know the answer.

Rules for memory:

- Do not rewrite the machine scan artifacts or the tmux/iTerm layout that Step 2 created.
- B3.5 is strictly serial — no fan-out.
- On any blocking B-step: print `PHASE_A_FAILED: <token>`, write `phase=blocked` to `STATUS.md`, stop.
- operator ↔ memory is CLI direct; never route through Feishu.

Verify:

```bash
grep -q '^phase=ready$' ~/.agents/tasks/install/STATUS.md
grep -q '^providers=' ~/.agents/tasks/install/STATUS.md
for s in install-memory install-planner install-builder install-reviewer install-patrol install-writer install-visual; do
  tmux has-session -t "$s" || echo "MISSING $s"
done
```

If `~/.agents/tasks/<project>/STATUS.md` already contains `phase=ready`, `scripts/install.sh` exits 0 instead of rebuilding. Pass `--reinstall` (alias `--force`) only when you intentionally want a rebuild.

Sandbox/headless installs:

- `scripts/install.sh` still writes project state under `real_user_home()`, not the caller's sandbox `HOME`.
- If the caller started install from a seat sandbox or another headless runtime, the Step 7/8 iTerm open is now best-effort: macOS / `iterm2` import / driver bootstrap failures emit `WARN:` and continue.
- `ITERM_LAYOUT_FAILED` remains a hard failure. If the driver already started returning a non-`ok` layout payload, that is treated as a real GUI problem, not a sandbox skip.
- Recovering or reopening the window after a sandbox install still goes through the canonical path: `agent_admin window open-grid <project> [--recover]`.

Project tool isolation:

- `agent_admin project init-tools <project> --from real-home|empty [--source-project <project>] [--tools ...]` creates or refreshes `~/.agent-runtime/projects/<project>/...`.
- `agent_admin project switch-identity <project> --tool feishu|gemini|codex --identity ...` only updates project-local identity metadata and reseeds existing seat sandboxes.
- `switch-identity` does not call native login CLIs and does not migrate credential payloads such as `~/.agent-runtime/projects/<project>/.gemini/oauth_creds.json` or `.codex/auth.json`.
- Recommended workflow:
  1. `agent_admin project init-tools <project> --from real-home` or `--source-project <other-project>`
  2. Manually place or verify the desired credential inside `~/.agent-runtime/projects/<project>/...`
  3. Run `agent_admin project switch-identity ...`

Failure:

```text
B2-memory-dead: memory seat still dead after one retry
B2.5-bootstrap-failed: machine.toml tenant population failed
B3-binding-mismatch: OpenClaw binding points at wrong project
B3.5_TIMEOUT: target seat did not come up in 15s
B5-feishu-binding-missing: no feishu_group_id and operator did not skip
B6-smoke-failed: smoke dispatch or CLI smoke failed
B7-status-write-failed: STATUS.md could not be written
```

---

## 4. (Optional) Apply koder overlay — Feishu reverse channel

Koder is an **OpenClaw-side agent** that subscribes to Feishu messages and forwards them via `tmux send-keys` into a ClawSeat seat. ClawSeat does not ship koder as a seat — it ships a **destructive overlay** that converts an existing OpenClaw agent into koder.

When you want remote access (phone → Feishu → koder → ClawSeat seat):

```bash
bash scripts/apply-koder-overlay.sh "$PROJECT_NAME"
```

Flow:

1. Script lists all registered OpenClaw tenants (from `~/.clawseat/machine.toml`).
2. Operator picks one by number.
3. Script prints a destructive-confirmation: the chosen agent's `IDENTITY.md`, `SOUL.md`, `TOOLS.md + TOOLS/*`, `MEMORY.md`, `AGENTS.md`, and workspace guide will be **overwritten** with koder templates (backups auto-taken via `--on-conflict backup`).
4. On confirmation: runs `init_koder.py` → `agent_admin project koder-bind` → `configure_koder_feishu.py`.

Verify:

```bash
python3 -c "from core.lib.machine_config import load_machine; m=load_machine(); print(m.tenants.get('<chosen-agent>'))"
```

Failure:

```text
ERR_NO_OPENCLAW_AGENTS: ~/.clawseat/machine.toml has no tenants (run B2.5 first)
ERR_BAD_PICK: selection out of range
ERR_INIT_KODER_FAILED: init_koder.py non-zero
```

Reversing the overlay: restore from backups in `<workspace>/.backup-koder-overlay-<ts>/`.

---

## 5. (Optional) Launch additional projects

ClawSeat supports multiple concurrent projects (sessions prefixed `<project>-<seat>`).

### Create a new project (default clawseat-engineering template)

```bash
bash scripts/install.sh --project <new-name>
bash scripts/install.sh --project <new-name> --provider minimax
bash scripts/install.sh --project <new-name> --repo-root /path/to/repo
```

`install.sh --project` already wires `agent_admin project bootstrap` under the
hood, so the same lazy-spawn install flow works for additional projects too.

### Create a clawseat-creative project (cartooner-bound creative team)

For image / video / audio / storyboard work bound to cartooner skills — uses a 5-seat roster
(memory / writer / builder-image / builder-av / patrol):

| Seat | Tool | Cartooner skills |
|------|------|------------------|
| memory (Vision Steward) | claude/minimax-api | cartooner (router), cartooner-resource-ops |
| writer (Story Specialist) | claude/oauth | cartooner-script-development, viral-copywriter |
| builder-image (Image Specialist) | codex/oauth | cartooner-image, cartooner-storyboard, cartooner-design, nano-banana, gpt-image-2 |
| builder-av (AV Cinematographer) | gemini/oauth | cartooner-video, cartooner-audio, cartooner-prompt, cartooner-seedance-cookbook (Gemini for YouTube reference learning) |
| patrol (Asset Guardian) | claude/minimax-api | cartooner-resource-ops (asset integrity, pipeline SLA) |

```bash
# Via install.sh (bootstraps and starts memory):
bash scripts/install.sh --project myproject \
  --repo-root /path/to/myproject \
  --template clawseat-creative

# Or directly via agent_admin:
# 1. Create local config file
cat > /tmp/myproject-local.toml << 'EOF'
project_name = "myproject"
repo_root = "/path/to/myproject"
EOF

# 2. Bootstrap with clawseat-creative template
agent_admin project bootstrap \
  --template clawseat-creative \
  --local /tmp/myproject-local.toml

# 3. Use the new project
agent_admin project use myproject
```

For an engineering team project, use the 5-seat engineering template:

| Seat | Tool | Role |
|------|------|------|
| memory | claude/oauth | lifecycle ops and dispatch |
| planner | claude/api | planning and chain-level coordination |
| builder | codex/oauth | implementation |
| reviewer | claude/oauth | QA + visual consistency checks; independent code review gate |
| patrol | claude/api | verification and evidence collection |

```bash
agent_admin project bootstrap \
  --template clawseat-engineering \
  --local /tmp/myproject-local.toml
```

### Switch context

```bash
agent_admin project use <new-name>
```

### Retire the install project and move to `foo`

```bash
INSTALL=install
FOO=foo
agent_admin project use "$FOO"
for seat in $(agent_admin session list --project "$INSTALL" 2>/dev/null | awk '/^running/{print $2}'); do
  agent_admin session stop-engineer "$seat" --project "$INSTALL" 2>/dev/null || true
done
tmux kill-session -t "project-${INSTALL}-monitor" 2>/dev/null || true
agent_admin session start-project "$FOO" --reset
# agent_admin project delete "$INSTALL"   # only if you want to wipe state
```

`project use` switches context without killing sessions; the seat-stop loop + monitor kill is what "retires" the install grid.

---

## Multi-worktree machines

If you maintain multiple ClawSeat worktrees (for example `/Users/you/ClawSeat`
and `/Users/you/coding/ClawSeat`), `install.sh` automatically detects the
freshest install code root. A worktree on `main` is preferred, and detached or
stale worktrees are skipped with a warning so ClawSeat skill symlinks do not
point at old SKILL content.

To override autodetection:

```bash
bash scripts/install.sh --project myproject --force-repo-root /Users/you/coding/ClawSeat
```

`--repo-root` still means the target project repository. Use
`--force-repo-root` only when you need to override the ClawSeat install code
root.

During install, `~/.agents/skills/` is the skill symlink source of truth.
`install.sh` mirrors that directory into `~/.claude/skills/`,
`~/.gemini/skills/`, and `~/.codex/skills/` so every supported tool can
discover the same ClawSeat-visible skill set.

---

## Troubleshooting

### `dispatch_task.py`: profile not found

Error:

```text
FileNotFoundError: ~/.agents/profiles/<project>-profile-dynamic.toml
```

Cause: the project bootstrap did not render the dynamic harness profile, so
`dispatch_task.py` cannot load the project routing and handoff paths.

Recovery:

1. Prefer a reinstall of the affected project so bootstrap re-renders all
   project state:

   ```bash
   bash ~/ClawSeat/scripts/install.sh --project <project> --reinstall
   ```

2. For diagnosis only, compare against
   `core/templates/profile-dynamic.template.toml`; do not hand-write a
   long-lived profile unless the operator explicitly chooses a manual override.

3. After the profile exists, rerun the original `dispatch_task.py --profile
   ~/.agents/profiles/<project>-profile-dynamic.toml ...` command.

---

## Failure modes (consolidated)

Common install-script failures:

| Code | Symptom | Recovery |
|------|---------|----------|
| `MISSING_PYTHON311` / `INVALID_PYTHON_BIN` | Python is absent, too old, or `PYTHON_BIN` points at an unsupported executable. | Install/use Python 3.11+ and rerun Step 2. |
| `PREFLIGHT_FAILED` | bootstrap preflight found a hard block. | Apply the printed fix command, then rerun Step 2. |
| `ENV_SCAN_INCOMPLETE` | a required `~/.agents/memory/machine/*.json` scan artifact is missing. | Rerun Step 2; inspect `scan_environment.py` output if repeatable. |
| `PROFILE_RENDER_MISSING` | `agent_admin project bootstrap` returned success but did not write `~/.agents/profiles/<project>-profile-dynamic.toml`. | Reinstall after updating ClawSeat, or inspect `agent_admin_crud_bootstrap.py` profile rendering. |
| `NON_TTY_NO_TEMPLATE` | project/template selection needs input but stdin/stdout is not a tty. | Pass `--project <name> --template <name>`. |
| `NON_TTY_NO_PROVIDER` / `INTERACTIVE_REQUIRED` | provider selection needs input but stdin/stdout is not a tty. | Pass `--provider <n|mode>` or `--base-url --api-key`. |
| `PROVIDER_NOT_FOUND` / `INVALID_PROVIDER_CHOICE` | the requested provider mode or candidate number is not usable on this host. | Re-run without the override, pick a detected candidate, or supply explicit API flags. |
| `ITERM2_PYTHON_MISSING` / `ITERM_DRIVER_FAILED` / `ITERM_LAYOUT_FAILED` | iTerm2 pane creation failed or returned a bad layout payload. | Verify iTerm2 automation and Python SDK access, then rerun Step 2. |
| `TMUX_SESSION_CREATE_FAILED` / `TMUX_SESSION_DIED_AFTER_LAUNCH` | a seat tmux session failed during launch. | Inspect tmux stderr and the seat workspace, then restart the affected seat. |

Full install-script error-code inventory from `scripts/install.sh` and
`scripts/install/lib/*.sh`:

| Source | Codes |
|--------|-------|
| `install.sh` | `COMMAND_FAILED`, `INVALID_FLAGS`, `INVALID_MEMORY_MODEL`, `INVALID_MEMORY_TOOL`, `INVALID_MODE`, `INVALID_PROJECT` |
| | `INVALID_REPO_ROOT`, `INVALID_TEMPLATE`, `MISSING_SCRIPT`, `UNKNOWN_FLAG` |
| `lib/preflight.sh` | `ENV_SCAN_INCOMPLETE`, `INVALID_PYTHON_BIN`, `MISSING_PYTHON311`, `PREFLIGHT_FAILED` |
| `lib/project.sh` | `AGENT_ADMIN_MISSING`, `BRIEF_CHMOD_FAILED`, `GUIDE_CHMOD_FAILED`, `GUIDE_DIR_FAILED`, `INVALID_PROJECT` |
| | `KICKOFF_CHMOD_FAILED`, `KICKOFF_DIR_FAILED`, `KICKOFF_WRITE_FAILED`, `PROJECTS_JSON_ACTION_UNKNOWN`, `PROJECTS_REGISTRY_MISSING` |
| | `PROFILE_RENDER_MISSING`, `PROJECT_BOOTSTRAP_FAILED`, `PROJECT_LOCAL_CHMOD_FAILED`, `PROJECT_LOCAL_DIR_FAILED` |
| | `PROJECT_PROFILE_BACKUP_FAILED`, `PROJECT_PROFILE_MIGRATE_FAILED`, `PROJECT_WORKSPACE_REGEN_FAILED`, `PATROL_ENGINEER_CREATE_FAILED`, `REINSTALL_BACKUP_FAILED` |
| | `PROFILE_REMOVE_FAILED`, `REINSTALL_PROJECT_MISSING` |
| | `NON_TTY_NO_TEMPLATE`, `TEMPLATE_CHMOD_FAILED`, `TEMPLATE_DIR_CREATE_FAILED`, `TEMPLATE_MISSING`, `TEMPLATE_ROOT_CREATE_FAILED` |
| | `WAIT_SCRIPT_MISSING` |
| `lib/provider.sh` | `INTERACTIVE_REQUIRED`, `INVALID_PROVIDER_CHOICE`, `NON_TTY_NO_PROVIDER`, `PROVIDER_ENV_CHMOD_FAILED`, `PROVIDER_ENV_DIR_FAILED` |
| | `PROVIDER_ENV_WRITE_FAILED`, `PROVIDER_INPUT_MISSING`, `PROVIDER_MODE_UNKNOWN`, `PROVIDER_NOT_FOUND` |
| `lib/secrets.sh` | `DEEPSEEK_SECRET_CHMOD_FAILED`, `DEEPSEEK_SECRET_DIR_FAILED`, `DEEPSEEK_SECRET_WRITE_FAILED` |
| | `PRIVACY_KB_CHMOD_FAILED`, `PRIVACY_KB_DIR_FAILED`, `PRIVACY_KB_WRITE_FAILED`, `PROJECT_SECRET_CHMOD_FAILED` |
| | `PROJECT_SECRET_DIR_FAILED`, `PROJECT_SECRET_WRITE_FAILED`, `PROVIDER_MODE_UNKNOWN` |
| `lib/skills.sh` | `PRIVACY_HOOK_CHMOD_FAILED`, `PRIVACY_HOOK_DIR_FAILED`, `PRIVACY_HOOK_PRESERVE_FAILED` |
| | `PRIVACY_HOOK_WRITE_FAILED`, `SKILL_SYMLINK_DIR_FAILED`, `SKILL_SYMLINK_FAILED` |
| `lib/window.sh` | `ITERM2_PYTHON_MISSING`, `ITERM_DRIVER_FAILED`, `ITERM_FOCUS_FAILED`, `ITERM_LAYOUT_FAILED` |
| | `ITERM_MACOS_ONLY`, `MEMORY_PATROL_BOOTSTRAP_FAILED`, `MEMORY_PATROL_CHMOD_FAILED`, `MEMORY_PATROL_DIR_FAILED` |
| | `MEMORY_PATROL_INVALID`, `MEMORY_PATROL_LAUNCHCTL_MISSING`, `MEMORY_PATROL_RENDER_FAILED` |
| | `MEMORY_PATROL_TEMPLATE_MISSING`, `TMUX_CWD_CREATE_FAILED`, `TMUX_SESSION_CREATE_FAILED` |
| | `TMUX_SESSION_DIED_AFTER_LAUNCH` |

Phase-A and optional overlay failures are emitted by memory or helper scripts,
not by `scripts/install.sh` itself:

| Code | Symptom | Recovery |
|------|---------|----------|
| `B2-memory-dead` | memory seat dead | memory halts, reports |
| `B2.5-bootstrap-failed` | machine.toml tenant write failed | check `~/.clawseat/` permissions |
| `B3-binding-mismatch` | openclaw binding project mismatch | fix binding, retry Phase-A |
| `B3.5_TIMEOUT` | seat did not come up | retry that seat only |
| `B5-feishu-binding-missing` | no feishu_group_id | operator may skip → CLI-only mode |
| `B6-smoke-failed` | smoke failed | keep `phase=blocked`, inspect logs |
| `B7-status-write-failed` | STATUS.md cannot be written | diagnose disk / permissions |
| `ACCEPTANCE_FAILED` | final state mismatched | inspect `STATUS.md`, tmux sessions |
| `ERR_NO_OPENCLAW_AGENTS` | koder overlay: no tenants registered | run B2.5 first or `agent_admin tenant register` manually |

---

## Resume

To rerun bootstrap safely (idempotent):

```bash
bash scripts/install.sh
```

To resume from a blocked Phase-A step:

```bash
tmux attach -t install-memory
```

Then tell memory to continue from the blocked token after fixing the underlying issue.

**Hard rule**: do not invent steps outside this file. If a required action is not
covered here, stop and surface the gap instead of improvising.
