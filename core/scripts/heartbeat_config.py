#!/usr/bin/env python3
"""heartbeat_config — manage per-project heartbeat cron config (C12).

Usage::

    heartbeat_config.py set --project X [--cadence 10min] [--template "..."] [--enabled true|false]
    heartbeat_config.py show --project X
    heartbeat_config.py list
    heartbeat_config.py render-plist --project X [--output /path/to/plist]
    heartbeat_config.py validate --project X
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from real_home import real_user_home  # noqa: E402

from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load

HEARTBEAT_SCHEMA_VERSION = 1
DEFAULT_CADENCE = "10min"
DEFAULT_TEMPLATE = "[HEARTBEAT_TICK project={project} ts={ts}] koder: run patrol, report drift, update STATUS.md if changed."

_CADENCE_RE = re.compile(
    r"^(?:(\d+)min|(\d+)m|(\d+)h|(\d+))$", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Config path
# ---------------------------------------------------------------------------


def heartbeat_dir(home: Path | None = None) -> Path:
    return (home or real_user_home()) / ".agents" / "heartbeat"


def config_path(project: str, home: Path | None = None) -> Path:
    return heartbeat_dir(home) / f"{project}.toml"


# ---------------------------------------------------------------------------
# Cadence parsing
# ---------------------------------------------------------------------------


def parse_cadence_seconds(cadence: str) -> int:
    """Convert cadence string to integer seconds.

    Accepts: "5min", "30m", "1h", raw integer (seconds string).
    Raises ValueError on unrecognised format.
    """
    s = (cadence or "").strip()
    m = _CADENCE_RE.match(s)
    if not m:
        raise ValueError(
            f"invalid cadence {cadence!r}: use '<N>min', '<N>m', '<N>h', or an integer seconds value"
        )
    if m.group(1) is not None:  # Nmin
        return int(m.group(1)) * 60
    if m.group(2) is not None:  # Nm
        return int(m.group(2)) * 60
    if m.group(3) is not None:  # Nh
        return int(m.group(3)) * 3600
    # raw integer seconds
    return int(m.group(4))


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _escape_toml(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_config(cfg: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"version = {cfg['version']}",
        f'project = "{_escape_toml(cfg["project"])}"',
        f"enabled = {'true' if cfg['enabled'] else 'false'}",
        f'cadence = "{_escape_toml(cfg["cadence"])}"',
        f'feishu_group_id = "{_escape_toml(cfg["feishu_group_id"])}"',
        f'message_template = "{_escape_toml(cfg["message_template"])}"',
        f'created_at = "{_escape_toml(cfg["created_at"])}"',
        f'updated_at = "{_escape_toml(cfg["updated_at"])}"',
    ]
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


def load_config(project: str, *, home: Path | None = None) -> dict[str, Any] | None:
    path = config_path(project, home=home)
    if not path.exists():
        return None
    raw = _toml_loads(path.read_text(encoding="utf-8"))
    return dict(raw)


def _resolve_feishu_group_id(project: str, home: Path | None = None) -> str:
    """Pull feishu_group_id from PROJECT_BINDING.toml; return '' if missing."""
    try:
        from core.lib.project_binding import load_binding
        binding = load_binding(project, home=home)
        if binding and binding.feishu_group_id:
            return binding.feishu_group_id
    except Exception:
        return ""
    return ""


def _warn_if_external(project: str, home: Path | None = None) -> None:
    """Warn when the bound group is cross-tenant (external=true)."""
    try:
        from core.lib.project_binding import load_binding
        binding = load_binding(project, home=home)
        if binding and binding.feishu_external:
            print(
                f"WARNING: project '{project}' is bound to an external (cross-tenant) "
                "Feishu group. Heartbeat ticks will cross tenant boundaries.",
                file=sys.stderr,
            )
    except Exception:
        return


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_set(args: argparse.Namespace, home: Path | None = None) -> int:
    project = args.project.strip()
    path = config_path(project, home=home)
    now = _utcnow()

    existing = load_config(project, home=home)
    created_at = existing["created_at"] if existing else now

    cadence = getattr(args, "cadence", None) or (existing or {}).get("cadence", DEFAULT_CADENCE)
    try:
        parse_cadence_seconds(cadence)
    except ValueError as exc:
        print(f"heartbeat_config set: {exc}", file=sys.stderr)
        return 1

    template = getattr(args, "template", None) or (existing or {}).get("message_template", DEFAULT_TEMPLATE)

    enabled_raw = getattr(args, "enabled", None)
    if enabled_raw is not None:
        enabled = enabled_raw.lower() in ("true", "1", "yes")
    else:
        enabled = (existing or {}).get("enabled", True)

    group_id = getattr(args, "feishu_group_id", None) or ""
    if not group_id:
        group_id = (existing or {}).get("feishu_group_id", "") or _resolve_feishu_group_id(project, home=home)

    cfg: dict[str, Any] = {
        "version": HEARTBEAT_SCHEMA_VERSION,
        "project": project,
        "enabled": enabled,
        "cadence": cadence,
        "feishu_group_id": group_id,
        "message_template": template,
        "created_at": created_at,
        "updated_at": now,
    }
    _write_config(cfg, path)
    _warn_if_external(project, home=home)
    print(f"heartbeat config written: {path}")
    return 0


def cmd_show(args: argparse.Namespace, home: Path | None = None) -> int:
    cfg = load_config(args.project, home=home)
    if cfg is None:
        print(f"heartbeat_config show: no config for project '{args.project}'", file=sys.stderr)
        return 1
    for key, val in cfg.items():
        print(f"{key} = {val!r}")
    return 0


def cmd_list(args: argparse.Namespace, home: Path | None = None) -> int:
    hdir = heartbeat_dir(home)
    if not hdir.exists():
        print("(no heartbeat configs)")
        return 0
    configs = sorted(hdir.glob("*.toml"))
    if not configs:
        print("(no heartbeat configs)")
        return 0
    for p in configs:
        project = p.stem
        try:
            cfg = load_config(project, home=home)
            enabled = cfg.get("enabled", True) if cfg else "?"
            cadence = cfg.get("cadence", "?") if cfg else "?"
            print(f"{project:20s}  enabled={enabled}  cadence={cadence}")
        except Exception as exc:
            print(f"{project:20s}  (parse error: {exc})")
    return 0


def render_plist(project: str, cfg: dict[str, Any], clawseat_root: str | None = None) -> str:
    cadence_secs = parse_cadence_seconds(str(cfg.get("cadence", DEFAULT_CADENCE)))
    home = str(real_user_home())
    root = clawseat_root or str(_REPO_ROOT)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.clawseat.heartbeat.{project}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{root}/core/scripts/heartbeat_beacon.sh</string>
    <string>{project}</string>
  </array>
  <key>StartInterval</key><integer>{cadence_secs}</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{home}/.agents/heartbeat/{project}.log</string>
  <key>StandardErrorPath</key><string>{home}/.agents/heartbeat/{project}.err</string>
</dict>
</plist>
"""


def cmd_render_plist(args: argparse.Namespace, home: Path | None = None) -> int:
    cfg = load_config(args.project, home=home)
    if cfg is None:
        print(f"heartbeat_config render-plist: no config for project '{args.project}'", file=sys.stderr)
        return 1
    try:
        xml = render_plist(args.project, cfg)
    except ValueError as exc:
        print(f"heartbeat_config render-plist: {exc}", file=sys.stderr)
        return 1
    output = getattr(args, "output", None)
    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(xml, encoding="utf-8")
        print(f"plist written: {out_path}")
    else:
        sys.stdout.write(xml)
    return 0


def cmd_validate(args: argparse.Namespace, home: Path | None = None) -> int:
    cfg = load_config(args.project, home=home)
    if cfg is None:
        print(f"heartbeat_config validate: no config for project '{args.project}'", file=sys.stderr)
        return 1
    errors: list[str] = []
    if not cfg.get("feishu_group_id", ""):
        errors.append("feishu_group_id is missing")
    try:
        parse_cadence_seconds(str(cfg.get("cadence", "")))
    except ValueError as exc:
        errors.append(str(exc))
    if not cfg.get("message_template", ""):
        errors.append("message_template is empty")
    if errors:
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        return 1
    _warn_if_external(args.project, home=home)
    print(f"heartbeat config for '{args.project}' is valid")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="heartbeat_config",
        description="Manage per-project heartbeat cron config",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # set
    s = sub.add_parser("set", help="Create or update heartbeat config")
    s.add_argument("--project", required=True)
    s.add_argument("--cadence", default=None, help="e.g. 10min, 30m, 1h, 600")
    s.add_argument("--template", default=None, dest="template")
    s.add_argument("--enabled", default=None, choices=["true", "false", "True", "False"])
    s.add_argument("--feishu-group-id", default=None, dest="feishu_group_id")

    # show
    sh = sub.add_parser("show", help="Print config for a project")
    sh.add_argument("--project", required=True)

    # list
    sub.add_parser("list", help="List all heartbeat configs")

    # render-plist
    rp = sub.add_parser("render-plist", help="Render launchd plist XML")
    rp.add_argument("--project", required=True)
    rp.add_argument("--output", default=None, help="Write to file instead of stdout")

    # validate
    v = sub.add_parser("validate", help="Validate config fields")
    v.add_argument("--project", required=True)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "set": cmd_set,
        "show": cmd_show,
        "list": cmd_list,
        "render-plist": cmd_render_plist,
        "validate": cmd_validate,
    }
    fn = dispatch.get(args.command)
    if fn is None:
        parser.print_help()
        return 1
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
