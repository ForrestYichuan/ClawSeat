#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from _common import (
    OPENCLAW_HOME,
    REPO_ROOT,
    capture_session_pane,
    detect_claude_onboarding_step,
    load_profile,
    load_toml,
    materialize_profile_runtime,
    require_success,
    run_command,
    seed_empty_secret_from_peer,
    session_name_for,
    session_path_for,
    utc_now_iso,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start a harness seat for a project profile.")
    parser.add_argument("--profile", required=True, help="Path to the project profile TOML.")
    parser.add_argument("--seat", required=True, help="Seat id to start.")
    parser.add_argument("--reset", action="store_true", help="Reset the seat session before starting.")
    parser.add_argument(
        "--confirm-start",
        action="store_true",
        help="Required for non-frontstage seats after the launch summary has been reviewed with the user.",
    )
    parser.add_argument("--tool", help="Override tool (claude, codex, gemini). Updates session before start.")
    parser.add_argument("--auth-mode", help="Override auth mode (oauth, api). Updates session before start.")
    parser.add_argument("--provider", help="Override provider. Updates session before start.")
    parser.add_argument(
        "--skip-bridge-preflight",
        action="store_true",
        help=(
            "Do not run the Feishu bridge preflight (group/auth/envelope). "
            "Default: run for bridge-aware seats. Use this only when the "
            "project is not yet bound and you accept tmux-only operation."
        ),
    )
    return parser.parse_args()


def _load_bridge_preflight():
    """Lazy-load core/lib/bridge_preflight.py — keeps test environments
    that don't touch the bridge free of subprocess side effects."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bridge_preflight", str(REPO_ROOT / "core" / "lib" / "bridge_preflight.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_bridge_preflight_or_exit(profile, seat: str, *, skip: bool) -> None:
    """For bridge-aware seats, run the preflight and abort if anything is
    red. No-op for non-bridge seats or when --skip-bridge-preflight is set."""
    if skip:
        return
    role = (profile.seat_roles or {}).get(seat, "")
    bp = _load_bridge_preflight()
    if not bp.seat_participates_in_bridge(
        seat=seat,
        role=role,
        heartbeat_owner=profile.heartbeat_owner,
        active_loop_owner=profile.active_loop_owner,
        heartbeat_transport=profile.heartbeat_transport,
    ):
        return
    result = bp.run_bridge_preflight(project=profile.project_name, seat=seat)
    print(result.render())
    if not result.ok:
        print(
            "\nseat launch aborted by bridge preflight. Fix the failing "
            "check(s) above, or re-run with --skip-bridge-preflight to "
            "start the seat without a functional Feishu bridge.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def write_frontstage_receipt(profile, seat: str) -> str:
    """
    Write a durable frontstage binding receipt proving the heartbeat owner has
    entered frontstage with the correct identity and project binding.
    """
    session_path = session_path_for(profile, seat)
    session_data = load_toml(session_path)
    if session_data is None:
        raise RuntimeError(
            f"session.toml not found for seat '{seat}' in project '{profile.project_name}': "
            f"expected at {session_path}. "
            "Run bootstrap_harness.py or agent_admin session switch-harness to create it."
        )
    role = profile.seat_roles.get(seat, "specialist")
    workspace = profile.workspace_for(seat)
    receipt_path = workspace / "FRONTSTAGE_RECEIPT.toml"
    lines = [
        "version = 1",
        f'seat_id = "{seat}"',
        f'role = "{role}"',
        f'project = "{profile.project_name}"',
        f'entered_at = "{utc_now_iso()}"',
        f"tool = \"{session_data.get('tool', '-')}\"",
        f"auth_mode = \"{session_data.get('auth_mode', '-')}\"",
        f"provider = \"{session_data.get('provider', '-')}\"",
        f"identity = \"{session_data.get('identity', '-')}\"",
        f"workspace = \"{workspace}\"",
        f"contract_path = \"{workspace / 'WORKSPACE_CONTRACT.toml'}\"",
    ]
    receipt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(receipt_path)


SEND_SCRIPT_PATH = REPO_ROOT / "core" / "shell-scripts" / "send-and-verify.sh"


def find_openclaw_frontstage_contract(profile, seat: str, *, cwd: Path | None = None) -> Path | None:
    if seat != profile.heartbeat_owner:
        return None
    openclaw_root = OPENCLAW_HOME.expanduser().resolve()
    search_root = (cwd or Path.cwd()).expanduser().resolve()
    candidates: list[Path] = []
    seen: set[Path] = set()
    for base in [search_root, *search_root.parents]:
        contract = (base / "WORKSPACE_CONTRACT.toml").resolve()
        if contract in seen or not contract.exists():
            continue
        seen.add(contract)
        candidates.append(contract)
    for contract in candidates:
        try:
            contract.relative_to(openclaw_root)
        except ValueError:
            continue
        contract_data = load_toml(contract) or {}
        contract_seat = str(contract_data.get("seat_id", "")).strip()
        contract_project = str(contract_data.get("project", "")).strip()
        if contract_seat == seat and contract_project == profile.project_name:
            return contract
    return None


def _send_frontstage_via_send_and_verify(project: str, seat: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SEND_SCRIPT_PATH), "--project", project, seat, "enter_frontstage"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def send_frontstage_trigger(profile, seat: str) -> None:
    """
    Send a frontstage entry trigger to the seat via tmux.
    For the heartbeat owner, this triggers automatic entry into the frontstage shell.
    """
    session_name = session_name_for(profile, seat)
    if not session_name:
        return
    result = _send_frontstage_via_send_and_verify(profile.project_name, seat)
    if result.returncode == 0:
        print(
            f"frontstage_trigger_ok: {profile.project_name}/{seat} via send-and-verify "
            f"(session={session_name})"
        )
        return
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    fix_hint = (
        "single_fix: run "
        f"`python3 {REPO_ROOT}/core/scripts/agent_admin.py window open-engineer {seat} --project {profile.project_name}` "
        "to reopen the frontstage window, then `tmux list-sessions` to confirm runtime state."
    )
    detail = output or "no output"
    if "RETRY_FAILED" in detail:
        detail += "; send-and-verify detected unsubmitted input"
    if "TMUX_MISSING" in detail:
        detail += "; recover path: ensure CLAWSEAT_ROOT and tmux are valid, then rerun preflight"
    raise RuntimeError(
        f"frontstage trigger failed for seat '{seat}' in project '{profile.project_name}': "
        f"tmux/send failed; return_code={result.returncode}; output={detail}; {fix_hint}"
    )


def apply_config_overrides(profile, seat: str, *, tool: str | None, auth_mode: str | None, provider: str | None) -> bool:
    """Apply tool/auth_mode/provider overrides via agent_admin switch-harness.

    Returns True if the session was updated, False if no changes were needed.
    """
    if not tool and not auth_mode and not provider:
        return False
    session_path = session_path_for(profile, seat)
    session_data = load_toml(session_path)
    if session_data is None:
        raise RuntimeError(
            f"session.toml not found for seat '{seat}' in project '{profile.project_name}': "
            f"expected at {session_path}. "
            "Run bootstrap_harness.py or agent_admin session switch-harness to create it."
        )
    current_tool = session_data.get("tool", "")
    current_auth = session_data.get("auth_mode", "")
    current_provider = session_data.get("provider", "")
    new_tool = tool or current_tool
    new_auth = auth_mode or current_auth
    new_provider = provider or current_provider
    if new_tool == current_tool and new_auth == current_auth and new_provider == current_provider:
        return False
    cmd = [
        sys.executable,
        str(profile.agent_admin),
        "session",
        "switch-harness",
        "--engineer",
        seat,
        "--project",
        profile.project_name,
        "--tool",
        new_tool,
        "--mode",
        new_auth,
        "--provider",
        new_provider,
    ]
    result = run_command(cmd, cwd=profile.repo_root)
    require_success(result, f"switch config for {seat}")
    if result.stdout.strip():
        print(result.stdout.strip())
    return True


def render_launch_summary(profile, seat: str) -> str:
    session_path = session_path_for(profile, seat)
    session_data = load_toml(session_path)
    if session_data is None:
        return (
            f"launch_summary_unavailable: session.toml not found for seat '{seat}' "
            f"in project '{profile.project_name}' at {session_path}. "
            "Run bootstrap_harness.py first."
        )
    role = profile.seat_roles.get(seat, "specialist")
    lines = [
        "launch_summary:",
        f"  profile: {profile.profile_name}",
        f"  harness_template: {profile.template_name}",
        f"  project: {profile.project_name}",
        f"  seat: {seat}",
        f"  role: {role}",
        f"  tool: {session_data.get('tool', '-')}",
        f"  auth_mode: {session_data.get('auth_mode', '-')}",
        f"  provider: {session_data.get('provider', '-')}",
        f"  session: {session_data.get('session', '-')}",
        f"  workspace: {session_data.get('workspace', '-')}",
        "config_override_hint: to change tool/auth/provider, re-run with --tool/--auth-mode/--provider flags.",
    ]
    return "\n".join(lines)


def print_effective_launch(profile, seat: str) -> None:
    result = run_command(
        [
            sys.executable,
            str(profile.agent_admin),
            "session",
            "effective-launch",
            seat,
            "--project",
            profile.project_name,
        ],
        cwd=profile.repo_root,
    )
    require_success(result, f"effective launch for {seat}")
    output = result.stdout.strip()
    if output:
        print("effective_launch:")
        print(output)


def main() -> int:
    args = parse_args()
    profile = load_profile(args.profile)
    materialize_profile_runtime(profile)
    runtime_seats = list(profile.runtime_seats or profile.materialized_seats or profile.seats)
    if args.seat == profile.heartbeat_owner and profile.heartbeat_transport == "openclaw":
        openclaw_frontstage_contract = find_openclaw_frontstage_contract(profile, args.seat)
        detail = (
            f" via {openclaw_frontstage_contract}"
            if openclaw_frontstage_contract is not None
            else " via profile heartbeat_transport=openclaw"
        )
        print(
            "openclaw_frontstage_start_blocked: "
            f"seat '{args.seat}' is bound to the OpenClaw frontstage, not a tmux runtime{detail}. "
            f"Do not run start_seat.py --seat {args.seat}; the current OpenClaw agent already owns frontstage."
        )
        if profile.default_start_seats:
            backend_defaults = [
                seat_id
                for seat_id in profile.default_start_seats
                if seat_id in runtime_seats and seat_id != profile.heartbeat_owner
            ]
            if backend_defaults:
                print(
                    "next_step: "
                    f"start a backend seat instead, for example {backend_defaults[0]!r}."
                )
        return 1
    if args.seat not in runtime_seats:
        print(
            "seat_not_runtime_startable: "
            f"seat '{args.seat}' is not declared in profile.runtime_seats={runtime_seats}. "
            "Only runtime seats can be started via tmux."
        )
        return 1
    openclaw_frontstage_contract = find_openclaw_frontstage_contract(profile, args.seat)
    if openclaw_frontstage_contract is not None:
        print(
            "openclaw_frontstage_self_start_blocked: "
            f"the current OpenClaw agent already owns frontstage seat '{args.seat}' "
            f"via {openclaw_frontstage_contract}. "
            f"Do not run start_seat.py --seat {args.seat} from the active OpenClaw frontstage workspace."
        )
        if profile.default_start_seats:
            backend_defaults = [
                seat_id
                for seat_id in profile.default_start_seats
                if seat_id in runtime_seats and seat_id != profile.heartbeat_owner
            ]
            if backend_defaults:
                print(
                    "next_step: "
                    f"start a backend seat instead, for example {backend_defaults[0]!r}."
                )
        return 1
    # Per-seat skill validation
    try:
        import importlib.util as _ilu
        _sr_spec = _ilu.spec_from_file_location("skill_registry", str(REPO_ROOT / "core" / "skill_registry.py"))
        if _sr_spec and _sr_spec.loader:
            _sr = _ilu.module_from_spec(_sr_spec)
            # Python 3.12+ dataclass(slots=True) needs the module in sys.modules
            import sys as _sys
            _sys.modules.setdefault("skill_registry", _sr)
            _sr_spec.loader.exec_module(_sr)
            seat_role = (profile.seat_roles or {}).get(args.seat, "")
            if seat_role:
                _sr_result = _sr.validate_all(role=seat_role)
                for _si in _sr_result.required_missing:
                    print(f"skill_blocked: {_si.name} ({_si.source}) required for {seat_role} — not found at {_si.expanded_path}")
                    if _si.fix_hint:
                        print(f"  -> {_si.fix_hint}")
                if _sr_result.required_missing:
                    print(f"\nSeat {args.seat} cannot start: {len(_sr_result.required_missing)} required skill(s) missing.")
                    return 1
                for _si in _sr_result.optional_missing:
                    print(f"skill_warning: {_si.name} ({_si.source}) — {_si.expanded_path}")
    except Exception as _exc:
        print(f"skill_check_skipped: {_exc}")

    # C3 bridge preflight — for bridge-aware seats (planner / koder-openclaw),
    # fail fast here if the project has no binding, lark-cli auth is stale,
    # or the delegation envelope can't render. Avoids discovering drift
    # mid-task. No-op for non-bridge seats and when the user explicitly
    # opts out with --skip-bridge-preflight.
    run_bridge_preflight_or_exit(profile, args.seat, skip=args.skip_bridge_preflight)

    has_overrides = args.tool or args.auth_mode or args.provider
    if has_overrides:
        # Local pre-validation: fail before the subprocess round-trip to
        # agent-admin switch-harness. When all three override args are
        # supplied together, we can reject a bad triple (e.g. tool=claude,
        # auth_mode=oauth, provider=anthropix) right here with the full
        # list of valid providers — no half-mutated session.toml state,
        # no burned identity directory.
        if args.tool and args.auth_mode and args.provider:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                "agent_admin_config",
                str(REPO_ROOT / "core" / "scripts" / "agent_admin_config.py"),
            )
            if _spec and _spec.loader:
                _mod = _ilu.module_from_spec(_spec)
                sys.modules.setdefault("agent_admin_config", _mod)
                _spec.loader.exec_module(_mod)
                _mod.validate_runtime_combo(
                    args.tool,
                    args.auth_mode,
                    args.provider,
                    error_cls=RuntimeError,
                    context=f"start_seat --seat {args.seat}",
                )
        switched = apply_config_overrides(
            profile, args.seat,
            tool=args.tool, auth_mode=args.auth_mode, provider=args.provider,
        )
        if switched:
            print(f"config_updated: {args.seat} session updated before start")
            # After a harness switch, always reset the tmux session so the old
            # session (running with stale auth/tool config) is not reused.
            args.reset = True
    if args.seat not in profile.heartbeat_seats and not args.confirm_start:
        print(render_launch_summary(profile, args.seat))
        print(
            "launch_confirmation_required: review the selected harness, seat, tool, auth mode, and provider with the user first; then re-run with --confirm-start."
        )
        return 2
    seeded_from = seed_empty_secret_from_peer(profile, args.seat)
    # OAuth is intentionally NOT seeded here — each CLI (claude/codex/gemini)
    # manages its own login in the TUI pane. Our job is only to spin up the
    # tmux+CLI+iTerm plumbing so the user can see and complete the login.
    # Ensure model/effort are written to settings.local.json before starting.
    # This covers seats started after bootstrap (via planner dispatch).
    from _common import _patch_claude_settings_from_profile
    _patch_claude_settings_from_profile(profile, [args.seat])
    cmd = [
        sys.executable,
        str(profile.agent_admin),
        "session",
        "start-engineer",
        args.seat,
        "--project",
        profile.project_name,
    ]
    if args.reset:
        cmd.append("--reset")
    result = run_command(cmd, cwd=profile.repo_root)
    require_success(result, "start_seat")
    session_data = load_toml(session_path_for(profile, args.seat)) or {}
    if str(session_data.get("tool", "")).strip() == "codex":
        print_effective_launch(profile, args.seat)
    window_cmd = [
        sys.executable,
        str(profile.agent_admin),
        "window",
        "open-engineer",
        args.seat,
        "--project",
        profile.project_name,
    ]
    session_name = session_data.get("session", f"{profile.project_name}-{args.seat}")
    # Retry window open once — iTerm AppleScript can be flaky.
    open_result = run_command(window_cmd, cwd=profile.repo_root)
    if open_result.returncode != 0:
        print(
            f"window_open_retry: first attempt failed (rc={open_result.returncode}), retrying…",
            file=sys.stderr,
        )
        import time as _t2
        _t2.sleep(1)
        open_result = run_command(window_cmd, cwd=profile.repo_root)
    if open_result.returncode != 0:
        # Window open is non-fatal — the tmux session is already running.
        print(
            f"window_open_skipped: iTerm window for {args.seat} could not be opened "
            f"(rc={open_result.returncode}). The tmux session is running — connect with:\n"
            f"  tmux attach -t {session_name}",
            file=sys.stderr,
        )
    if seeded_from is not None:
        print(f"seeded secret for {args.seat} from {seeded_from}")
    if result.stdout.strip():
        print(result.stdout.strip())
    # TUI visibility check — ensure user can actually see the seat
    tui_check = run_command(
        [
            sys.executable, "-c",
            "import sys; sys.path.insert(0, %r); "
            "from agent_admin_window import verify_tui_visible; "
            "import json; print(json.dumps(verify_tui_visible(%r, retries=3, delay=2.0)))"
            % (str(profile.repo_root / "core" / "scripts"), session_name),
        ],
        cwd=profile.repo_root,
    )
    if tui_check.returncode == 0 and tui_check.stdout.strip():
        import json as _json
        try:
            tui_state = _json.loads(tui_check.stdout.strip())
            if not tui_state.get("session_exists"):
                print(
                    f"session_lost: tmux session '{session_name}' disappeared after startup. "
                    "The seat may have exited immediately.",
                    file=sys.stderr,
                )
            elif not tui_state.get("visible"):
                print(
                    f"tui_not_visible: session '{session_name}' is running but not attached "
                    f"(clients={tui_state.get('clients', 0)}). "
                    f"Connect manually: tmux attach -t {session_name}",
                    file=sys.stderr,
                )
        except (ValueError, KeyError):  # silent-ok: TUI state parse is best-effort; don't block seat startup on malformed JSON
            pass

    # Retry pane capture with delay — the TUI may not have rendered yet
    import time as _time
    pane_text = capture_session_pane(profile, args.seat)
    onboarding_step = detect_claude_onboarding_step(pane_text)
    if onboarding_step is None and not pane_text.strip():
        # Pane empty — TUI still loading, retry after delay
        for _retry in range(3):
            _time.sleep(2)
            pane_text = capture_session_pane(profile, args.seat)
            onboarding_step = detect_claude_onboarding_step(pane_text)
            if onboarding_step is not None or pane_text.strip():
                break
        if not pane_text.strip():
            print(
                "onboarding_check_inconclusive: "
                f"pane for {args.seat} is still empty after 6s. "
                "The seat may be loading or stuck. Check the tmux pane manually: "
                f"tmux attach -t $(agentctl.sh session-name {args.seat} --project {profile.project_name})"
            )
    if onboarding_step == "claude_bypass_permissions":
        # Claude Code 2.1.145+ shows an interactive "Yes, I accept" dialog for
        # --dangerously-skip-permissions on first run in a new identity HOME.
        # Auto-accept: ClawSeat seats are intentionally launched with this flag;
        # requiring manual acceptance on every new identity blocks automation.
        import subprocess as _sp
        _bp_session = session_name
        _sp.run(
            ["tmux", "send-keys", "-t", _bp_session, "2", "Enter"],
            capture_output=True,
        )
        print(
            f"bypass_permissions_auto_accepted: "
            f"sent '2\\nEnter' to {_bp_session} (--dangerously-skip-permissions is intentional)"
        )
        _time.sleep(3)
        pane_text = capture_session_pane(profile, args.seat)
        onboarding_step = detect_claude_onboarding_step(pane_text)

    if onboarding_step is not None:
        hint = ""
        # oauth_login / oauth_code / oauth_error steps (across claude/codex/
        # gemini — see CLAUDE_ONBOARDING_MARKERS) may time out; pressing
        # Enter in the TUI usually re-triggers the flow.
        if any(tok in onboarding_step for tok in ("oauth_login", "oauth_code", "oauth_error")):
            hint = (
                " If OAuth times out (e.g. 'timeout of 15000ms exceeded'), "
                "press Enter in the tmux window to retry."
            )
        # Identify which CLI via the marker-step prefix (claude_/codex_/gemini_).
        cli = onboarding_step.split("_", 1)[0] if "_" in onboarding_step else "tui"
        print(
            "manual_onboarding_required: "
            f"{args.seat} is waiting on {cli} first-run step '{onboarding_step}'.{hint} "
            "Ask the user to complete the prompt in the TUI window, then notify the operator to take over."
        )
    else:
        # No onboarding step detected — the heartbeat owner is ready to enter frontstage
        if args.seat == profile.heartbeat_owner:
            # Write durable frontstage receipt
            receipt_path = write_frontstage_receipt(profile, args.seat)
            print(f"frontstage_receipt_written: {receipt_path}")
            # Send frontstage entry trigger to the heartbeat owner
            send_frontstage_trigger(profile, args.seat)
            print(
                "frontstage_auto_entry: "
                f"{args.seat} has been triggered to enter frontstage shell automatically. "
                "The seat will read WORKSPACE_CONTRACT.toml and enter the frontstage loop."
            )
        else:
            print(
                "contract_reread_required: "
                f"after {args.seat} is up, have that seat re-read its generated workspace guide "
                "and WORKSPACE_CONTRACT.toml before treating it as fully ready. "
                "If you need durable proof, run scripts/ack_contract.py for that seat afterwards."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
