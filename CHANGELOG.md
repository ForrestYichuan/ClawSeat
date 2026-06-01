# Changelog

All notable changes to this project are documented in this file.

## [0.2.1] - 2026-05-03

### Fixed

- BF-mor-2 / F17: replaced stale `agent_admin window list-panes` references
  with canonical `tmux list-panes` / `tmux list-clients` wording in the
  bootstrap doc and recovery script.
- BF-mor-3 / F21: `window open-grid` now prints
  `window open-grid: rebuilt project=<p> seats=<N>` by default and accepts
  `--quiet` to suppress the summary line.
- BF-mor-4 / F22: `projects_registry validate` now prints
  `projects_registry validate <p>: OK|FAIL — <reason>` by default and accepts
  `--quiet` to suppress the summary line.

## [Unreleased]

### Added

- `core/scripts/seat_status.py`, `core/scripts/seat_dashboard.html`, and
  `core/scripts/seat_status.sh`: local ClawSeat seat activity control surface
  that summarizes durable TODO/handoff/DELIVERY/STATUS state plus tmux session
  liveness for the five-seat roster without reading secret files or tmux pane
  content.

### Changed

- A1: Operational migration of 6 Claude seats from `auth_mode=oauth` to
  `oauth_token` or `api/anthropic-console`, eliminating the per-seat
  Keychain popup risk surface.
  New provider `"anthropic-console"` added to
  `SUPPORTED_RUNTIME_MATRIX["claude"]["api"]`; `build_runtime` injects
  `ANTHROPIC_API_KEY` and clears `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL`
  / `CLAUDE_CODE_OAUTH_TOKEN` defensively.
  `core/scripts/migrate_seat_auth.py` — one-shot operator script with
  `plan` / `apply --dry-run` / `apply` modes, preflight checks, and
  idempotent post-verify.
  `docs/auth-modes.md` — new reference page for all 4 auth modes + decision
  guide + migration walkthrough.
  `docs/ARCHITECTURE.md §3j`.

- C16: `HEARTBEAT_RECEIPT.toml` schema bumped v1 → v2. New fields:
  `token_usage_pct` (0..1), `token_usage_source`, `token_usage_measured_at`.
  Pre-v2 receipts missing these fields are treated as "unknown" — no alert
  fired, fully backwards compatible.
  `patrol_supervisor.py` emits `seat.context_near_limit` event when
  `token_usage_pct >= 0.80`. feishu_announcer routes it to Feishu.
  Heuristic: session.jsonl size ÷ (model_max_tokens × 8 bytes). ~30% error bar.

- C15: `dispatch_task.py` and `complete_handoff.py` now notify by default.
  Use `--no-notify` to opt out. `--skip-notify` is a deprecated alias that
  still works but prints a warning to stderr.
  `add_notify_args` / `resolve_notify` shared helpers in `_common.py` keep
  static and dynamic script variants in sync. See ARCHITECTURE.md §3h.

## [0.2.0] - 2026-04-21

Ship of the ClawSeat audit-campaign R1 + R2 batch. R1 addressed the audit's
ship-blockers (HOME leaks, `/cs` profile collision, doc↔code contradictions,
python3.11 across the install flow); R2 swept the remaining urgent polish
flagged by ancestor patrol cycles 1 and 2.

### Fixed

- `/cs` bootstrap no longer collides with the local install profile — flipped
  `install-with-memory.toml` to tmux transport and added koder to
  `runtime_seats`.
- 14 HIGH HOME-leak sites migrated from `Path.home()` / `os.path.expanduser` to
  the `real_user_home()` helper so seat sandboxes no longer divert the heartbeat
  owner's on-disk state.
- Install flow command examples pinned to `python3.11` so tomllib-using scripts
  do not crash under macOS's default `python3` (3.9).
- `tests/test_gstack_skills_root_override.py` — four assertions were asserting
  `Path.home()` where the implementation correctly uses
  `_real_user_home_ssot()`. Tests now lock the documented contract and no
  longer fail in sandbox environments.
- Docs python3 examples that were missed in R1:
  `docs/INSTALL.md`, `docs/INSTALL_GUIDE.md`, `core/skills/clawseat-install/SKILL.md`,
  `core/skills/cs/SKILL.md`.
- `install-flow.md:544` and `ancestor-runbook.md:574` — `bind_project_to_group`
  snippet no longer imports from a nonexistent
  `core.skills.clawseat_install.scripts.bind_project` module. Replaced with
  the working `shells/openclaw-plugin/_bridge_binding` pattern and the required
  `account_id` / `session_key` arguments.

### Added

- `core/templates/gstack-harness/template.toml`: `[[engineers]]` stanza for
  `builder-2`, matching the install profile's runtime materialization.
- `core/skills/gstack-harness/scripts/selftest.py`: engineer list now reads
  dynamically from the template TOML instead of a hardcoded seat tuple, so
  new seats cannot silently skip selftest coverage.
- `docs/ARCHITECTURE.md`: new "Heartbeat transport vs runtime seats" section
  explaining the tmux (local) vs openclaw (overlay) distinction and
  `runtime_seats`.
- `docs/INSTALL.md`: matching mode comparison table for
  `install-with-memory.toml` vs `install-openclaw.toml`.

## [0.1.0]

Baseline at audit-campaign start (pre-R1). Initial public cut of the ClawSeat
multi-seat control-plane framework.
