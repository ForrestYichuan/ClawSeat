"""A1: Migrate 6 Claude seats from auth_mode=oauth to oauth_token / api.

Usage:
  migrate-seat-auth plan               # print current mapping + proposed
  migrate-seat-auth apply --dry-run    # show agent-admin commands, no changes
  migrate-seat-auth apply              # actually run them
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from _toml_compat import load_safe as _toml_load_safe

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.lib.real_home import real_user_home

HOME = real_user_home()
AGENTS_ROOT = HOME / ".agents"

# Operator decision: hardcoded target mapping.
# install/builder-2 and audit/builder-1 use api+anthropic-console for diversity
# and isolation (see brief SEAT-AUTH-A1-MIGRATION).
TARGET_MAPPING: dict[tuple[str, str], tuple[str, str]] = {
    ("install", "koder"):      ("oauth_token", "anthropic"),
    ("install", "planner"):    ("oauth_token", "anthropic"),
    ("install", "builder-1"):  ("oauth_token", "anthropic"),
    ("install", "builder-2"):  ("api",         "anthropic-console"),
    ("myproject", "planner"):  ("oauth_token", "anthropic"),
    ("audit", "builder-1"):    ("api",         "anthropic-console"),
}

_OAUTH_TOKEN_SECRET = AGENTS_ROOT / ".env.global"
_ANTHROPIC_CONSOLE_SECRET = AGENTS_ROOT / "secrets" / "claude" / "anthropic-console.env"


def _session_toml_path(project: str, seat: str) -> Path:
    return AGENTS_ROOT / "sessions" / project / seat / "session.toml"


def _read_session_toml(project: str, seat: str) -> dict:
    path = _session_toml_path(project, seat)
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return _toml_load_safe(f)


def _check_env_file_has_key(path: Path, key: str) -> bool:
    if not path.exists():
        return False
    text = path.read_text(errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(key + "=") or line.startswith(f"export {key}="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if val:
                return True
    return False


def _preflight() -> list[str]:
    """Return list of preflight failure messages (empty = all OK)."""
    errors: list[str] = []

    # Check CLAUDE_CODE_OAUTH_TOKEN for oauth_token seats.
    needs_oauth_token = any(
        mode == "oauth_token" for (_, _), (mode, _) in TARGET_MAPPING.items()
    )
    if needs_oauth_token:
        found = _check_env_file_has_key(_OAUTH_TOKEN_SECRET, "CLAUDE_CODE_OAUTH_TOKEN")
        if not found:
            # Also check per-seat koder.env.
            alt = AGENTS_ROOT / "secrets" / "claude" / "koder.env"
            found = _check_env_file_has_key(alt, "CLAUDE_CODE_OAUTH_TOKEN")
        if not found:
            errors.append(
                "CLAUDE_CODE_OAUTH_TOKEN not found in:\n"
                f"  {_OAUTH_TOKEN_SECRET}\n"
                f"  {AGENTS_ROOT / 'secrets' / 'claude' / 'koder.env'}\n\n"
                "Operator action required:\n"
                "  1. claude setup-token  →  export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...\n"
                "     echo 'export CLAUDE_CODE_OAUTH_TOKEN=...' >> ~/.agents/.env.global\n"
            )

    # Check ANTHROPIC_API_KEY for anthropic-console seats.
    needs_console = any(
        provider == "anthropic-console" for (_, _), (_, provider) in TARGET_MAPPING.items()
    )
    if needs_console:
        found = _check_env_file_has_key(_ANTHROPIC_CONSOLE_SECRET, "ANTHROPIC_API_KEY")
        if not found:
            errors.append(
                f"ANTHROPIC_API_KEY not found in {_ANTHROPIC_CONSOLE_SECRET}\n\n"
                "Operator action required:\n"
                "  2. Create Anthropic Console API key (scoped-role=Claude Code)\n"
                f"     mkdir -p {_ANTHROPIC_CONSOLE_SECRET.parent}\n"
                f"     echo 'ANTHROPIC_API_KEY=sk-ant-api03-...' > {_ANTHROPIC_CONSOLE_SECRET}\n"
                f"     chmod 600 {_ANTHROPIC_CONSOLE_SECRET}\n"
            )

    return errors


def _build_agent_admin_cmd(project: str, seat: str, mode: str, provider: str) -> list[str]:
    return [
        "agent-admin", "session", "switch-harness",
        "--engineer", seat,
        "--project", project,
        "--tool", "claude",
        "--mode", mode,
        "--provider", provider,
    ]


def cmd_plan(_args: object) -> int:
    print(f"{'Project':<12} {'Seat':<12} {'Current auth_mode':<20} {'Current provider':<20} {'→ auth_mode':<15} {'→ provider'}")
    print("-" * 100)
    for (project, seat), (target_mode, target_provider) in TARGET_MAPPING.items():
        data = _read_session_toml(project, seat)
        cur_mode = data.get("auth_mode", "(not found)")
        cur_provider = data.get("provider", "(not found)")
        print(f"{project:<12} {seat:<12} {cur_mode:<20} {cur_provider:<20} {target_mode:<15} {target_provider}")
    return 0


def cmd_apply(args: object) -> int:
    dry_run: bool = getattr(args, "dry_run", False)

    errors = _preflight()
    if errors:
        for msg in errors:
            print(f"ERROR: {msg}", file=sys.stderr)
        print("\nRe-run after resolving the above.\n  migrate-seat-auth apply", file=sys.stderr)
        return 2

    changed = 0
    skipped = 0

    for (project, seat), (target_mode, target_provider) in TARGET_MAPPING.items():
        data = _read_session_toml(project, seat)
        if not data:
            print(f"  skip  {project}/{seat}: session.toml not found")
            skipped += 1
            continue

        cur_mode = data.get("auth_mode", "")
        cur_provider = data.get("provider", "")

        if cur_mode == target_mode and cur_provider == target_provider:
            print(f"  ok    {project}/{seat}: already {target_mode}/{target_provider}")
            skipped += 1
            continue

        cmd = _build_agent_admin_cmd(project, seat, target_mode, target_provider)
        print(f"  {'(dry) ' if dry_run else ''}run   {project}/{seat}: {cur_mode}/{cur_provider} → {target_mode}/{target_provider}")
        print(f"        {' '.join(cmd)}")

        if not dry_run:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  FAIL  {project}/{seat}: exit {result.returncode}", file=sys.stderr)
                if result.stderr:
                    print(f"        {result.stderr.strip()}", file=sys.stderr)
                return 1
            # Verify the change took effect.
            new_data = _read_session_toml(project, seat)
            if new_data.get("auth_mode") != target_mode or new_data.get("provider") != target_provider:
                print(
                    f"  FAIL  {project}/{seat}: post-check mismatch — "
                    f"got {new_data.get('auth_mode')}/{new_data.get('provider')}",
                    file=sys.stderr,
                )
                return 1
        changed += 1

    if dry_run:
        print(f"\nDry run: {changed} command(s) shown, {skipped} already OK / skipped. No changes made.")
        return 0

    if changed == 0:
        print("Already migrated: all seats at target auth_mode/provider.")
        return 0

    print(f"\nMigration applied: {changed} seat(s) updated, {skipped} skipped.")
    print("\nTo activate, restart affected seats:")
    for (project, seat), (mode, _) in TARGET_MAPPING.items():
        print(f"  tmux kill-session -t {project}-{seat}-claude")
    print("\nVerification after restart:")
    print("  ps -p <pid> -E -o command= | tr ' ' '\\n' | grep -E 'ANTHROPIC_|CLAUDE_CODE_OAUTH'")
    return 0


def _build_parser():
    import argparse
    parser = argparse.ArgumentParser(
        prog="migrate-seat-auth",
        description="Migrate ClawSeat Claude seats to new auth_mode/provider.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="Print current and proposed mapping table.")
    apply_p = sub.add_parser("apply", help="Apply the migration (or dry-run).")
    apply_p.add_argument(
        "--dry-run", action="store_true",
        help="Print agent-admin commands without executing them.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "plan":
        return cmd_plan(args)
    if args.command == "apply":
        return cmd_apply(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
