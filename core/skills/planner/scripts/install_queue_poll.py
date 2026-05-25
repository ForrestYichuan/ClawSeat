#!/usr/bin/env python3
"""Install planner queue-poll: SessionStart hook + launchd plist (Phase 3).

For Claude Code planner workspaces:
- Writes `.claude/settings.local.json` SessionStart hook entry that invokes
  queue_poll.py once at session start.
- Optionally renders the launchd plist from
  `core/templates/planner-queue-poll.plist.in` and writes to
  `~/Library/LaunchAgents/`. User must `launchctl load` manually (operator
  consent gate).

Idempotent: re-running merges existing hook entries.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
_CORE_LIB = REPO_ROOT / "core" / "lib"
if str(_CORE_LIB) not in sys.path:
    sys.path.insert(0, str(_CORE_LIB))
from real_home import real_user_home  # noqa: E402


def _agents_root() -> Path:
    return real_user_home() / ".agents"


def install_sessionstart_hook(workspace: Path, project: str, team: str, tool: str) -> Path:
    """Write SessionStart hook into <workspace>/.claude/settings.local.json.

    Post-retest #7 fix: use canonical Claude Code hook shape (matcher + nested
    hooks list), matching install_planner_hook.py pattern. The earlier flat
    shape ({type, command}) does not fire under Claude Code's hook runtime.
    """
    settings_dir = workspace / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_path = settings_dir / "settings.local.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    sessionstart_entries = hooks.setdefault("SessionStart", [])
    if not isinstance(sessionstart_entries, list):
        sessionstart_entries = []
        hooks["SessionStart"] = sessionstart_entries

    command = (
        f"python3 {REPO_ROOT}/core/skills/planner/scripts/queue_poll.py "
        f"--project {project} --team {team} --actor planner@{tool}"
    )

    # Canonical shape: each top-level entry has {matcher, hooks: [{type, command, timeout}]}
    already_present = False
    for entry in sessionstart_entries:
        if not isinstance(entry, dict):
            continue
        for hook_def in entry.get("hooks") or []:
            if isinstance(hook_def, dict) and hook_def.get("command") == command:
                already_present = True
                break
        if already_present:
            break

    if not already_present:
        sessionstart_entries.append({
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": 30,
                }
            ],
        })

    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return settings_path


def render_launchd_plist(project: str, team: str, tool: str, log_dir: Path) -> str:
    """Return rendered plist text. Caller writes to disk + loads."""
    template_path = REPO_ROOT / "core" / "templates" / "planner-queue-poll.plist.in"
    text = template_path.read_text(encoding="utf-8")
    return (
        text.replace("{PROJECT}", project)
            .replace("{TEAM}", team)
            .replace("{TOOL}", tool)
            .replace("{REPO_ROOT}", str(REPO_ROOT))
            .replace("{LOG_DIR}", str(log_dir))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install planner queue-poll hook + (optional) launchd plist."
    )
    parser.add_argument("--workspace", required=True, help="Planner workspace dir")
    parser.add_argument("--project", required=True)
    parser.add_argument("--team", required=True)
    parser.add_argument("--tool", required=True, choices=["claude", "codex", "gemini"])
    parser.add_argument("--write-plist", action="store_true",
                        help="Also render and write launchd plist (user still must launchctl load)")
    parser.add_argument("--plist-out", default=None, help="Override plist output path")
    args = parser.parse_args(argv)

    settings_path = install_sessionstart_hook(
        Path(args.workspace), args.project, args.team, args.tool
    )
    print(f"hook installed: {settings_path}")

    if args.write_plist:
        log_dir = _agents_root() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        plist_text = render_launchd_plist(args.project, args.team, args.tool, log_dir)
        plist_out = (
            Path(args.plist_out) if args.plist_out
            else Path.home() / "Library" / "LaunchAgents"
                 / f"com.clawseat.queue-poll.{args.project}.{args.team}.plist"
        )
        plist_out.parent.mkdir(parents=True, exist_ok=True)
        plist_out.write_text(plist_text, encoding="utf-8")
        print(f"plist written: {plist_out}")
        print(f"To activate: launchctl load {plist_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
