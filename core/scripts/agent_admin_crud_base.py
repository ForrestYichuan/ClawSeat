from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping

from _toml_compat import loads_safe as _toml_loads_safe

from agent_admin_config import HOME, REPO_ROOT, SEND_AND_VERIFY_SH, validate_runtime_combo


# ── Profile TOML helpers (text-based, preserves comments and order) ────────────


def _toml_inline_list_add(text: str, key: str, value: str, *, section: str | None = None) -> str:
    """Add *value* to an inline TOML list; no-op if already present or key not found."""
    if section is not None:
        sec_m = re.search(rf'^\[{re.escape(section)}\]', text, re.MULTILINE)
        if not sec_m:
            return text
        after_sec = text[sec_m.end():]
        nxt = re.search(r'^\[', after_sec, re.MULTILINE)
        search_text = after_sec[: nxt.start()] if nxt else after_sec
        base_offset = sec_m.end()
    else:
        search_text = text
        base_offset = 0

    pat = re.compile(rf'^{re.escape(key)}\s*=\s*\[([^\]]*)\]', re.MULTILINE)
    km = pat.search(search_text)
    if not km:
        return text

    inner = km.group(1)
    existing = [v.strip().strip("\"'") for v in inner.split(",") if v.strip().strip("\"'")]
    if value in existing:
        return text

    new_inner = (inner + f', "{value}"') if inner.strip() else f'"{value}"'
    new_entry = f"{key} = [{new_inner}]"
    abs_start = base_offset + km.start()
    abs_end = base_offset + km.end()
    return text[:abs_start] + new_entry + text[abs_end:]


def _toml_seat_role_set(text: str, seat_id: str, role: str) -> str:
    """Set seat_roles.<seat_id> = role in the [seat_roles] section."""
    sec_m = re.search(r'^\[seat_roles\]', text, re.MULTILINE)
    if not sec_m:
        return text.rstrip("\n") + f'\n\n[seat_roles]\n{seat_id} = "{role}"\n'

    after = text[sec_m.end():]
    nxt = re.search(r'^\[', after, re.MULTILINE)
    sec_len = nxt.start() if nxt else len(after)
    sec_content = after[:sec_len]

    km = re.search(rf'^{re.escape(seat_id)}\s*=.*$', sec_content, re.MULTILINE)
    if km:
        new_sec = sec_content[: km.start()] + f'{seat_id} = "{role}"' + sec_content[km.end():]
    else:
        new_sec = sec_content.rstrip("\n") + f'\n{seat_id} = "{role}"\n'

    return text[: sec_m.end()] + new_sec + text[sec_m.end() + sec_len:]


def _toml_seat_overrides_set(
    text: str,
    seat_id: str,
    tool: str,
    auth_mode: str,
    provider: str,
    model: str | None,
    *,
    update: bool = False,
) -> str:
    """Add (or update when update=True) a [seat_overrides.<seat_id>] block."""
    block_key = f"seat_overrides.{seat_id}"
    block_m = re.search(rf'^\[{re.escape(block_key)}\]', text, re.MULTILINE)

    lines = [f"[{block_key}]", f'tool = "{tool}"', f'auth_mode = "{auth_mode}"', f'provider = "{provider}"']
    if model:
        lines.append(f'model = "{model}"')
    new_block = "\n".join(lines) + "\n"

    if block_m:
        if not update:
            return text  # already exists — idempotent skip for create
        after = text[block_m.end():]
        nxt = re.search(r'^\[', after, re.MULTILINE)
        block_end = block_m.end() + (nxt.start() if nxt else len(after))
        before = text[: block_m.start()].rstrip("\n") + "\n\n"
        rest = text[block_end:].lstrip("\n")
        return before + new_block + ("\n" + rest if rest else "")

    return text.rstrip("\n") + "\n\n" + new_block


def _update_profile_seat(
    profile_path: Path,
    seat_id: str,
    role: str,
    tool: str,
    auth_mode: str,
    provider: str,
    model: str | None = None,
    *,
    rebind: bool = False,
) -> None:
    """Update a harness profile TOML with seat metadata.

    For create (rebind=False): idempotently appends seat to seats,
    materialized_seats, runtime_seats, seat_roles, and seat_overrides.
    For rebind (rebind=True): only updates seat_overrides (always overwrites).
    """
    if not re.match(r'^[a-zA-Z0-9_-]+$', seat_id):
        raise ValueError(f"Invalid seat_id {seat_id!r}: must match [a-zA-Z0-9_-]+")

    text = profile_path.read_text(encoding="utf-8")

    if not rebind:
        text = _toml_inline_list_add(text, "seats", seat_id)
        text = _toml_inline_list_add(text, "materialized_seats", seat_id, section="dynamic_roster")
        text = _toml_inline_list_add(text, "runtime_seats", seat_id, section="dynamic_roster")
        text = _toml_seat_role_set(text, seat_id, role)
        text = _toml_seat_overrides_set(text, seat_id, tool, auth_mode, provider, model)
    else:
        text = _toml_seat_overrides_set(text, seat_id, tool, auth_mode, provider, model, update=True)

    profile_path.write_text(text, encoding="utf-8")


_PROJECT_TOOL_SEED_MAP: dict[str, tuple[tuple[str, bool], ...]] = {
    "lark-cli": ((".lark-cli", True),),
    "gemini": (
        (".config/gemini", True),
        (".gemini", True),
    ),
    "codex": (
        (".config/codex", True),
        (".codex", True),
    ),
    "iterm2": (
        ("Library/Application Support/iTerm2", True),
        ("Library/Preferences/com.googlecode.iterm2.plist", False),
    ),
}

_PROJECT_TOOL_SEED_ALIASES = {
    "lark": "lark-cli",
    "lark-cli": "lark-cli",
    "lark_cli": "lark-cli",
    "gemini": "gemini",
    "codex": "codex",
    "iterm": "iterm2",
    "iterm2": "iterm2",
}

_DEFAULT_PROJECT_TOOL_SEEDS = ("lark-cli", "gemini", "codex", "iterm2")


def _normalize_project_tool_seed_names(raw: str | None) -> list[str]:
    if raw is None:
        return list(_DEFAULT_PROJECT_TOOL_SEEDS)
    tokens = [item.strip().lower() for item in re.split(r"[,\s]+", raw) if item.strip()]
    if not tokens:
        return list(_DEFAULT_PROJECT_TOOL_SEEDS)
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        canonical = _PROJECT_TOOL_SEED_ALIASES.get(token)
        if not canonical:
            raise ValueError(
                f"unknown tool {token!r}; expected lark-cli, gemini, codex, or iterm2"
            )
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def _project_tool_seed_entries(tool: str) -> tuple[tuple[str, bool], ...]:
    try:
        return _PROJECT_TOOL_SEED_MAP[tool]
    except KeyError as exc:
        raise ValueError(f"unknown project tool seed {tool!r}") from exc


def _remove_existing_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _copy_project_tool_seed(source: Path, target: Path) -> None:
    if source.is_dir():
        if target.exists() and not target.is_dir():
            _remove_existing_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
        return
    if target.exists():
        _remove_existing_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _project_tool_root_path(project: str) -> Path:
    from project_tool_root import project_tool_root

    return project_tool_root(project)


def _q(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _q_array(values: list[str]) -> str:
    return "[" + ", ".join(_q(value) for value in values) + "]"


def _render_dynamic_profile(
    template_text: str,
    *,
    project: str,
    repo_root: str,
    profile_path: Path,
    seats: list[str],
    seat_roles: dict[str, str],
) -> str:
    tasks_root = HOME / ".agents" / "tasks" / project
    workspace_root = HOME / ".agents" / "workspaces" / project
    handoff_dir = tasks_root / "patrol" / "handoffs"
    default_target = "planner" if "planner" in seats else (seats[0] if seats else "")
    seat_roles_block = "\n".join(f"{seat} = {_q(seat_roles.get(seat, seat))}" for seat in seats)
    seat_overrides_block = "\n\n".join(f"[seat_overrides.{seat}]" for seat in seats)
    replacements = {
        "{{project}}": project,
        "{{profile_path}}": str(profile_path),
        "{{repo_root}}": repo_root,
        "{{tasks_root}}": str(tasks_root),
        "{{project_doc}}": str(tasks_root / "PROJECT.md"),
        "{{tasks_doc}}": str(tasks_root / "TASKS.md"),
        "{{status_doc}}": str(tasks_root / "STATUS.md"),
        "{{send_script}}": str(SEND_AND_VERIFY_SH),
        "{{agent_admin}}": str(REPO_ROOT / "core" / "scripts" / "agent_admin.py"),
        "{{workspace_root}}": str(workspace_root),
        "{{handoff_dir}}": str(handoff_dir),
        "{{heartbeat_receipt}}": str(HOME / ".openclaw" / "koder" / f"{project}-HEARTBEAT_RECEIPT.toml"),
        "{{session_root}}": str(HOME / ".agents" / "sessions"),
        "{{default_notify_target}}": default_target,
        "{{seats}}": _q_array(seats),
        "{{seat_roles_block}}": seat_roles_block,
        "{{seat_overrides_block}}": seat_overrides_block,
    }
    rendered = template_text
    for needle, value in replacements.items():
        rendered = rendered.replace(needle, value)
    return rendered.rstrip() + "\n"


@dataclass
class CrudHooks:
    error_cls: type[Exception]
    project_cls: type
    engineer_cls: type
    session_record_cls: type
    sessions_root: Path
    workspaces_root: Path
    current_project_path: Path
    normalize_name: Callable[[str], str]
    project_path: Callable[[str], Path]
    engineer_path: Callable[[str], Path]
    session_path: Callable[[str, str], Path]
    load_project: Callable[[str], Any]
    load_projects: Callable[[], dict[str, Any]]
    load_project_or_current: Callable[[str | None], Any]
    load_engineer: Callable[[str], Any]
    load_sessions: Callable[[], dict[tuple[str, str], Any]]
    load_template: Callable[[str], dict]
    load_toml: Callable[[Path], dict]
    merge_template_local: Callable[[dict, dict], dict]
    write_project: Callable[[Any], None]
    write_engineer: Callable[[Any], None]
    write_session: Callable[[Any], None]
    set_current_project: Callable[[str], None]
    get_current_project_name: Callable[..., str | None]
    show_project: Callable[[Any], int]
    resolve_engineer: Callable[[str], Any]
    resolve_engineer_session: Callable[..., Any]
    create_engineer_profile: Callable[..., Any]
    merge_engineer_profile_with_template: Callable[[Any, dict], Any]
    create_session_record: Callable[..., Any]
    apply_template: Callable[[Any, Any], None]
    render_template_text: Callable[..., dict[str, str]]
    ensure_empty_env_file: Callable[..., None]
    ensure_dir: Callable[[Path], None]
    write_text: Callable[..., None]
    write_env_file: Callable[..., None]
    parse_env_file: Callable[[Path], dict[str, str]]
    archive_if_exists: Callable[[Path, str], None]
    identity_name: Callable[..., str]
    runtime_dir_for_identity: Callable[..., Path]
    secret_file_for: Callable[..., Path]
    session_name_for: Callable[..., str]
    ensure_secret_permissions: Callable[[Path], None]
    session_service: Any
    tmux_has_session: Callable[[str], bool]


_CALLER_ENGINEER_PROFILE_ENV = "CLAWSEAT_ENGINEER_PROFILE"
_CALLER_ENGINEER_ID_ENVS = ("CLAWSEAT_ENGINEER_ID", "CLAWSEAT_SEAT")


def _caller_env(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def caller_engineer_id(env: Mapping[str, str] | None = None) -> str:
    env_map = _caller_env(env)
    for key in _CALLER_ENGINEER_ID_ENVS:
        value = str(env_map.get(key, "")).strip()
        if value:
            return value
    return ""


def caller_engineer_profile_path(env: Mapping[str, str] | None = None) -> Path | None:
    raw = str(_caller_env(env).get(_CALLER_ENGINEER_PROFILE_ENV, "")).strip()
    return Path(raw).expanduser() if raw else None


def caller_engineer_profile(env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    path = caller_engineer_profile_path(env)
    if path is None or not path.is_file():
        return None
    try:
        return _toml_loads_safe(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def require_caller_authority(
    authority: str,
    action: str,
    error_cls: type[Exception],
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    if authority not in {"dispatch", "escalation"}:
        raise ValueError(f"unknown authority level: {authority!r}")

    profile = caller_engineer_profile(env)
    caller_id = caller_engineer_id(env) or "<unknown>"
    if profile is None:
        raise error_cls(
            f"{action} requires CLAWSEAT_ENGINEER_PROFILE with {authority}_authority "
            f"(caller={caller_id})"
        )

    profile_caller_id = str(profile.get("id", profile.get("engineer_id", caller_id)) or caller_id).strip()
    allowed = bool(profile.get("escalation_authority", False))
    if authority == "dispatch":
        allowed = bool(profile.get("dispatch_authority", False)) or allowed
    if not allowed:
        raise error_cls(
            f"{action} requires {authority}_authority (caller={profile_caller_id or caller_id})"
        )


def archive_session_artifacts(hooks: CrudHooks, session: Any) -> None:
    """Archive workspace/runtime/secret/session-dir and remove from project rosters."""
    hooks.archive_if_exists(Path(session.workspace), "workspaces")
    hooks.archive_if_exists(Path(session.runtime_dir), "runtimes")
    if session.secret_file:
        hooks.archive_if_exists(Path(session.secret_file), "secrets")
    hooks.archive_if_exists(hooks.session_path(session.project, session.engineer_id).parent, "sessions")
    project = hooks.load_project(session.project)
    project.engineers = [item for item in project.engineers if item != session.engineer_id]
    project.monitor_engineers = [item for item in project.monitor_engineers if item != session.engineer_id]
    hooks.write_project(project)
