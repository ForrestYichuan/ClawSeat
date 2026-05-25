#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load


SCHEMA_VERSION = 2
VALID_STATUSES = {"active", "archived", "broken"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def registry_root() -> Path:
    override = os.environ.get("CLAWSEAT_REGISTRY_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".clawseat"


def registry_path() -> Path:
    return registry_root() / "projects.json"


def backup_path() -> Path:
    return registry_path().with_suffix(".json.bak")


@dataclass
class ProjectEntry:
    name: str
    primary_seat: str
    tmux_name: str
    registered_at: str
    primary_seat_tool: str = ""
    template_name: str = ""
    last_access: str = ""
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
    repo_path: str = ""
    seats: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProjectEntry":
        registered_at = str(raw.get("registered_at") or _now())
        status = str(raw.get("status") or "active")
        if status not in VALID_STATUSES:
            status = "broken"
        metadata = raw.get("metadata")
        primary_seat = str(raw.get("primary_seat") or raw.get("primary") or "").strip()
        tmux_name = str(raw.get("tmux_name") or "").strip()
        raw_seats = raw.get("seats")
        seats = (
            {str(key): str(value) for key, value in raw_seats.items() if str(key).strip() and str(value).strip()}
            if isinstance(raw_seats, dict)
            else {}
        )
        if primary_seat and tmux_name and primary_seat not in seats:
            seats[primary_seat] = tmux_name
        return cls(
            name=str(raw.get("name", "")).strip(),
            primary_seat=primary_seat,
            primary_seat_tool=str(raw.get("primary_seat_tool") or "").strip(),
            tmux_name=tmux_name,
            template_name=str(raw.get("template_name") or "").strip(),
            registered_at=registered_at,
            last_access=str(raw.get("last_access") or registered_at),
            status=status,
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            repo_path=str(raw.get("repo_path") or "").strip(),
            seats=seats,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data["last_access"]:
            data["last_access"] = data["registered_at"]
        if data["status"] not in VALID_STATUSES:
            data["status"] = "broken"
        return data


def _empty_registry() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "projects": []}


def _decode_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalise_registry(raw: dict[str, Any]) -> dict[str, Any]:
    projects: list[dict[str, Any]] = []
    for item in raw.get("projects", []):
        if not isinstance(item, dict):
            continue
        entry = ProjectEntry.from_dict(item)
        if not entry.name:
            continue
        if not entry.primary_seat:
            entry.primary_seat = "memory"
        if not entry.tmux_name:
            entry.tmux_name = f"{entry.name}-{entry.primary_seat}"
        if entry.primary_seat and entry.tmux_name and entry.primary_seat not in entry.seats:
            entry.seats[entry.primary_seat] = entry.tmux_name
        projects.append(entry.to_dict())
    projects.sort(key=lambda item: item["name"])
    return {"version": SCHEMA_VERSION, "projects": projects}


def load_registry(*, recover: bool = True) -> dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return _empty_registry()
    try:
        return _normalise_registry(_decode_json(path))
    except Exception:
        bak = backup_path()
        if recover and bak.exists():
            recovered = _normalise_registry(_decode_json(bak))
            atomic_write(recovered)
            return recovered
        return _empty_registry()


def atomic_write(data: dict[str, Any]) -> None:
    root = registry_root()
    root.mkdir(parents=True, exist_ok=True)
    normalised = _normalise_registry(data)
    path = registry_path()
    fd, tmp_name = tempfile.mkstemp(prefix="projects.", suffix=".json", dir=str(root))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(normalised, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        tmp.chmod(0o600)
        os.replace(tmp, path)
        shutil.copy2(path, backup_path())
        backup_path().chmod(0o600)
    finally:
        if tmp.exists():
            tmp.unlink()


def _project_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("name")): item for item in data.get("projects", []) if item.get("name")}


def enumerate_projects(*, include_archived: bool = True) -> list[ProjectEntry]:
    entries = [ProjectEntry.from_dict(item) for item in load_registry().get("projects", [])]
    if include_archived:
        return entries
    return [entry for entry in entries if entry.status == "active"]


def get_project(name: str) -> ProjectEntry | None:
    for entry in enumerate_projects():
        if entry.name == name:
            return entry
    return None


def register_project(
    name: str,
    primary_seat: str,
    *,
    tmux_name: str = "",
    primary_seat_tool: str = "",
    template_name: str = "",
    repo_path: str = "",
    status: str = "active",
    metadata: dict[str, Any] | None = None,
    seats: dict[str, str] | None = None,
) -> ProjectEntry:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; expected one of {sorted(VALID_STATUSES)}")
    data = load_registry()
    index = _project_index(data)
    now = _now()
    existing = ProjectEntry.from_dict(index[name]) if name in index else None
    entry = ProjectEntry(
        name=name,
        primary_seat=primary_seat,
        primary_seat_tool=primary_seat_tool or (existing.primary_seat_tool if existing else ""),
        tmux_name=tmux_name or f"{name}-{primary_seat}",
        template_name=template_name or (existing.template_name if existing else ""),
        registered_at=existing.registered_at if existing else now,
        last_access=now,
        status=status,
        metadata={**(existing.metadata if existing else {}), **(metadata or {})},
        repo_path=repo_path or (existing.repo_path if existing else ""),
        seats={**(existing.seats if existing else {}), **(seats or {})},
    )
    index[name] = entry.to_dict()
    data["projects"] = list(index.values())
    atomic_write(data)
    return entry


def unregister_project(name: str) -> bool:
    data = load_registry()
    before = len(data.get("projects", []))
    data["projects"] = [item for item in data.get("projects", []) if item.get("name") != name]
    atomic_write(data)
    return len(data["projects"]) != before


def update_project(
    name: str,
    *,
    status: str | None = None,
    metadata: dict[str, Any] | None = None,
    repo_path: str | None = None,
    template_name: str | None = None,
    primary_seat_tool: str | None = None,
    seats: dict[str, str] | None = None,
) -> ProjectEntry:
    data = load_registry()
    index = _project_index(data)
    if name not in index:
        raise KeyError(name)
    entry = ProjectEntry.from_dict(index[name])
    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}; expected one of {sorted(VALID_STATUSES)}")
        entry.status = status
    if metadata:
        entry.metadata.update(metadata)
    if repo_path is not None:
        entry.repo_path = repo_path
    if template_name is not None:
        entry.template_name = template_name
    if primary_seat_tool is not None:
        entry.primary_seat_tool = primary_seat_tool
    if seats is not None:
        entry.seats = dict(seats)
    index[name] = entry.to_dict()
    data["projects"] = list(index.values())
    atomic_write(data)
    return entry


def touch_project(name: str) -> ProjectEntry | None:
    entry = get_project(name)
    if entry is None:
        return None
    data = load_registry()
    index = _project_index(data)
    entry.last_access = _now()
    index[name] = entry.to_dict()
    data["projects"] = list(index.values())
    atomic_write(data)
    return entry


def _agents_root() -> Path:
    override = os.environ.get("CLAWSEAT_AGENTS_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agents"


def _project_toml_path(project: str, agents_home: Path | None = None) -> Path:
    root = agents_home or _agents_root()
    return root / "projects" / project / "project.toml"


def _primary_from_toml(data: dict[str, Any]) -> str:
    engineers = data.get("engineers")
    if isinstance(engineers, list) and engineers:
        return str(engineers[0])
    embedded = data.get("engineer") or data.get("engineers_table") or []
    if isinstance(embedded, list) and embedded:
        first = embedded[0]
        if isinstance(first, dict):
            return str(first.get("id") or "")
    return ""


def _primary_tool_from_toml(data: dict[str, Any], primary: str) -> str:
    overrides = data.get("seat_overrides")
    if isinstance(overrides, dict):
        primary_override = overrides.get(primary)
        if isinstance(primary_override, dict) and primary_override.get("tool"):
            return str(primary_override["tool"])
    for item in data.get("engineer", []) if isinstance(data.get("engineer"), list) else []:
        if isinstance(item, dict) and str(item.get("id")) == primary:
            return str(item.get("tool") or item.get("default_tool") or "")
    return ""


def validate_registry_vs_project_toml(
    project: str,
    *,
    agents_home: Path | None = None,
) -> list[str]:
    warnings: list[str] = []
    entry = get_project(project)
    if entry is None:
        return [f"registry missing project {project}"]
    path = _project_toml_path(project, agents_home)
    if not path.exists():
        return [f"project.toml missing for {project}: {path}"]
    with path.open("rb") as handle:
        project_data = _toml_load(handle)
    primary = _primary_from_toml(project_data)
    if primary and entry.primary_seat != primary:
        warnings.append(
            f"primary_seat mismatch: registry={entry.primary_seat} project.toml={primary}"
        )
    primary_tool = _primary_tool_from_toml(project_data, primary)
    if primary_tool and entry.primary_seat_tool and entry.primary_seat_tool != primary_tool:
        warnings.append(
            f"primary_seat_tool mismatch: registry={entry.primary_seat_tool} project.toml={primary_tool}"
        )
    template_name = str(project_data.get("template_name") or "")
    if template_name and entry.template_name and entry.template_name != template_name:
        warnings.append(
            f"template_name mismatch: registry={entry.template_name} project.toml={template_name}"
        )
    return warnings


def _metadata_items(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"metadata must be key=value, got {item!r}")
        key, value = item.split("=", 1)
        result[key] = value
    return result


def _seat_items(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"seat mapping must be seat=session, got {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(f"seat mapping must be seat=session, got {item!r}")
        result[key] = value
    return result


def _cmd_list(args: argparse.Namespace) -> int:
    entries = enumerate_projects(include_archived=not args.active_only)
    if args.json:
        print(json.dumps([entry.to_dict() for entry in entries], ensure_ascii=False, indent=2))
    else:
        for entry in entries:
            print(f"{entry.name}\t{entry.status}\t{entry.primary_seat}\t{entry.last_access}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    entry = get_project(args.project)
    if entry is None:
        print(f"project not found: {args.project}", file=sys.stderr)
        return 1
    print(json.dumps(entry.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cmd_register(args: argparse.Namespace) -> int:
    entry = register_project(
        args.project,
        args.primary_seat,
        tmux_name=args.tmux_name or "",
        primary_seat_tool=args.primary_seat_tool or "",
        template_name=args.template_name or "",
        repo_path=args.repo_path or "",
        status=args.status,
        metadata=_metadata_items(args.metadata or []),
        seats=_seat_items(args.seat or []),
    )
    print(json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def _cmd_unregister(args: argparse.Namespace) -> int:
    removed = unregister_project(args.project)
    print(f"unregistered {args.project}" if removed else f"not registered {args.project}")
    return 0 if removed else 1


def _cmd_update(args: argparse.Namespace) -> int:
    try:
        entry = update_project(
            args.project,
            status=args.status,
            metadata=_metadata_items(args.metadata or []),
            repo_path=args.repo_path,
            template_name=args.template_name,
            primary_seat_tool=args.primary_seat_tool,
            seats=_seat_items(args.seat) if args.seat is not None else None,
        )
    except KeyError:
        print(f"project not found: {args.project}", file=sys.stderr)
        return 1
    print(json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def _cmd_touch(args: argparse.Namespace) -> int:
    entry = touch_project(args.project)
    if entry is None:
        print(f"project not found: {args.project}", file=sys.stderr)
        return 1
    print(json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    warnings = validate_registry_vs_project_toml(args.project)
    quiet = bool(getattr(args, "quiet", False))
    ok = not warnings
    if not quiet:
        if ok:
            print(f"projects_registry validate {args.project}: OK")
        else:
            reason = "; ".join(warnings)
            print(f"projects_registry validate {args.project}: FAIL — {reason}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="projects_registry.py")
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.add_argument("--active-only", action="store_true")
    list_cmd.set_defaults(func=_cmd_list)

    show_cmd = sub.add_parser("show")
    show_cmd.add_argument("project")
    show_cmd.set_defaults(func=_cmd_show)

    register_cmd = sub.add_parser("register")
    register_cmd.add_argument("project")
    register_cmd.add_argument("--primary-seat", default="memory")
    register_cmd.add_argument("--primary-seat-tool", default="")
    register_cmd.add_argument("--tmux-name", default="")
    register_cmd.add_argument("--template-name", default="")
    register_cmd.add_argument("--repo-path", default="")
    register_cmd.add_argument("--status", choices=sorted(VALID_STATUSES), default="active")
    register_cmd.add_argument("--metadata", action="append", default=[])
    register_cmd.add_argument("--seat", action="append", default=[], help="Seat mapping as seat=session; repeatable.")
    register_cmd.set_defaults(func=_cmd_register)

    unregister_cmd = sub.add_parser("unregister")
    unregister_cmd.add_argument("project")
    unregister_cmd.set_defaults(func=_cmd_unregister)

    update_cmd = sub.add_parser("update")
    update_cmd.add_argument("project")
    update_cmd.add_argument("--status", choices=sorted(VALID_STATUSES))
    update_cmd.add_argument("--metadata", action="append", default=[])
    update_cmd.add_argument("--repo-path")
    update_cmd.add_argument("--template-name")
    update_cmd.add_argument("--primary-seat-tool")
    update_cmd.add_argument("--seat", action="append", help="Replace seat mappings with seat=session entries; repeatable.")
    update_cmd.set_defaults(func=_cmd_update)

    touch_cmd = sub.add_parser("touch")
    touch_cmd.add_argument("project")
    touch_cmd.set_defaults(func=_cmd_touch)

    validate_cmd = sub.add_parser("validate")
    validate_cmd.add_argument("project")
    validate_cmd.add_argument("--quiet", action="store_true")
    validate_cmd.set_defaults(func=_cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
