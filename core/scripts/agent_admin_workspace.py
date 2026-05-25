from __future__ import annotations

import hashlib
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load

# agent_admin_config lives in the same scripts directory.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from agent_admin_config import _resolve_effective_home as _ws_effective_home  # noqa: E402


REPO_ROOT = Path(
    os.environ.get("CLAWSEAT_ROOT", str(Path(__file__).resolve().parents[2]))
)
HARNESS_PROFILE_ROOT = REPO_ROOT / "core" / "skills" / "gstack-harness" / "assets" / "profiles"
SEND_AND_VERIFY_SH = REPO_ROOT / "core" / "shell-scripts" / "send-and-verify.sh"
HARNESS_SCRIPTS_ROOT = REPO_ROOT / "core" / "skills" / "gstack-harness" / "scripts"
TOOLS_SHARED_ROOT = REPO_ROOT / "core" / "templates" / "shared" / "TOOLS"

_SPECIALIST_ROLES = frozenset({"builder", "reviewer", "patrol", "designer"})
DEFAULT_QUALITY_GATE_DOC = "quality-docs/QUALITY.md"

# Ensure `core/` is importable so bare `from resolve import ...` resolves
# regardless of how this module is invoked (direct script vs import).
_CORE_PATH = str(REPO_ROOT / "core")
if _CORE_PATH not in sys.path:
    sys.path.insert(0, _CORE_PATH)
from lib.utils import q, q_array  # noqa: E402


def render_role_line(engineer: Any, bullet: bool = False) -> str:
    if not getattr(engineer, "role", ""):
        return ""
    prefix = "- " if bullet else ""
    return f"{prefix}Role: `{engineer.role}`"


def render_role_details_lines(engineer: Any) -> list[str]:
    details = list(getattr(engineer, "role_details", []) or [])
    if not details:
        return []
    if engineer.role in _SPECIALIST_ROLES:
        summary = "; ".join(details)
        if len(summary) > 200:
            summary = summary[:197] + "..."
        return ["## Role Focus", summary]
    lines = ["## Role Focus", ""]
    lines.extend(f"- {detail}" for detail in details)
    return lines


def render_aliases_lines(engineer: Any) -> list[str]:
    aliases = list(getattr(engineer, "aliases", []) or [])
    if not aliases:
        return []
    alias_text = ", ".join(f"`{alias}`" for alias in aliases)
    return [
        "## Aliases",
        "",
        f"- {alias_text}",
    ]


def render_authority_lines(engineer: Any) -> list[str]:
    if engineer.role in _SPECIALIST_ROLES:
        return []
    capabilities: list[str] = []
    if getattr(engineer, "human_facing", False):
        capabilities.append("human-facing intake and professional user communication")
    if getattr(engineer, "active_loop_owner", False):
        capabilities.append("active loop ownership")
    if getattr(engineer, "dispatch_authority", False):
        capabilities.append("downstream dispatch authority")
    if getattr(engineer, "patrol_authority", False):
        capabilities.append("patrol / supervision authority")
    if getattr(engineer, "unblock_authority", False):
        capabilities.append("chain unblock authority (confirmations, approvals, reminders)")
    if getattr(engineer, "escalation_authority", False):
        capabilities.append("escalation authority")
    if getattr(engineer, "remind_active_loop_owner", False):
        capabilities.append("may remind the active loop owner when patrol finds drift")
    if getattr(engineer, "review_authority", False):
        capabilities.append("review verdict authority")
    if getattr(engineer, "design_authority", False):
        capabilities.append("design review / prototype authority")
    if not capabilities:
        return []
    lines = ["## Seat Capabilities", ""]
    lines.extend(f"- {capability}" for capability in capabilities)
    return lines


def render_protocol_reminder_lines(
    engineer: Any,
    role: str,
    *,
    template_name: str = "",
) -> list[str]:
    lines = ["## ⚠ Protocol Reminder (每轮先读)", ""]
    normalized = (role or getattr(engineer, "role", "") or "").strip()
    seat_id = (getattr(engineer, "engineer_id", "") or "").strip().lower()
    if template_name in _CARTOONER_TEMPLATES:
        return _render_protocol_reminder_cartooner(seat_id)
    if normalized in {"project-memory", "memory-oracle"}:
        lines.extend([
            "1. **Status Snapshot**: user wake / pre-dispatch -> `agent_admin.py brief planner-status --project <project>`; manual queue scans only if it fails or debugging needs detail",
            "2. **Brief Fidelity**: preserve operator/warden Goal/Context/Boundary/Anti-goal/Acceptance; do not weaken product intent",
            "3. **v3 Dispatch**: memory→planner uses `agent_admin.py brief queue`; downstream/legacy handoff uses `dispatch_task.py`",
            "4. **Verify Queue**: after queueing, read `planner-status`; only debug with raw queue files if the snapshot is unclear",
            "5. **Chain end**: accept queue-drained planner relay -> read DELIVERY / acceptance / review/latest -> write KB summary",
            "6. **Don't**: direct downstream dispatch, code/config/seat lifecycle, or network/outbound without privacy guard; runtime blocks v3 memory→planner split-brain dispatch",
        ])
    elif normalized in {"planner", "planner-dispatcher"}:
        lines.extend([
            "1. **Intent Fidelity**: preserve brief outcome / constraints / anti-goal / acceptance; bounce instead of implementing a weaker reading",
            "2. **/clear before dispatch**: G1 closure / G2 context-relatedness / G3 idle; 三 gate 全过即发，先 /clear 再 dispatch；见 `core/skills/planner/SKILL.md:57`。",
            "3. **Dispatch specialist**: dispatch_task.py -> handoff.json + send-and-verify wake target",
            "4. **Strict fan-in**: before relay memory, verify every specialist .consumed receipt; missing -> verdict=BLOCKED",
            "5. **Post-DELIVERY closeout**: read DELIVERY -> verdict -> planner/DELIVERY.md; in multi-team delivery mode notify memory only when queue is drained",
            "6. **Compact not Clear**: relay `[memory: compact-me]` to memory; never emit `[CLEAR-REQUESTED]`",
        ])
    elif normalized in {"solo-tui", "user-proxy", "warden"}:
        lines.extend([
            "1. **No background patrol**: do not monitor, poll, or inspect internals unless the user explicitly asks.",
            "2. **Spec authoring**: act as a professional architecture/code advisor; preserve user intent as goal + context + boundary + anti-goal + acceptance + delivery.",
            "3. **Problem reports**: investigate root cause before forwarding a vague issue to another agent.",
            "4. **Product testing**: use product-test personas only when validation needs them; inspect logs/events/artifacts when evidence is needed.",
            "5. **Patrol review**: when explicitly/scheduled to patrol, review delivered-but-unintegrated work; merge PASS only to project review/latest and notify user.",
            "6. **Web opening**: open user-facing webpages in Cartooner's Inspiration Browser by default; external browser only by request/auth/debug need.",
            "7. **Memory relay**: hand product/code briefs to memory for queue/state tracking; do not become the state machine.",
            "8. **Direct fixes**: fix framework/template/automation defects in your scope; do not become memory or planner by default.",
        ])
    elif normalized in {"builder", "reviewer"}:
        lines.extend([
            "1. **Closeout MANDATORY two-step**: complete_handoff.py (.consumed receipt) + send-and-verify.sh (wakeup) — NOT optional",
            "2. **Fan-out trigger**: 2+ disjoint sub-goals (files / tests / research lanes) -> MUST fan-out",
            "3. **/clear-before-dispatch**: 派工前若 worker 上一波闭环且 idle,planner 应已发 /clear;若没收到 /clear 但条件齐,直接报 finding.",
            "4. **DELIVERY.md**: include task_id / source / reply_to / files list / Tests / Verdict",
            "5. **Failure escalate**: complete_handoff --status blocked --target planner; do NOT silent retry",
            "6. **Don't**: dispatch other specialists; touch seat lifecycle / config / secrets",
        ])
    else:
        lines.extend([
            "1. **Closeout MANDATORY two-step**: complete_handoff.py (.consumed receipt) + send-and-verify.sh (wakeup) — NOT optional",
            "2. **Fan-out trigger**: 2+ disjoint sub-goals (files / tests / research lanes) -> MUST fan-out",
            "3. **DELIVERY.md**: include task_id / source / reply_to / files list / Tests / Verdict",
            "4. **Failure escalate**: complete_handoff --status blocked --target planner; do NOT silent retry",
            "5. **Don't**: dispatch other specialists; touch seat lifecycle / config / secrets",
        ])
    lines.append("")
    return lines


def _resolve_tasks_root(project: Any) -> str:
    """Resolve the actual tasks root for a project.

    Uses the standard ~/.agents/tasks/{project} path when ~/.agents exists
    (ClawSeat convention). Falls back to repo_root/.tasks for projects
    that keep tasks inside their repo.
    """
    agents_root = Path(os.environ.get("AGENTS_ROOT", str(_ws_effective_home() / ".agents")))
    agents_tasks = agents_root / "tasks" / project.name
    # Use the standard agents path if the agents root exists (even if the
    # project tasks dir hasn't been created yet — it will be during bootstrap).
    if agents_root.exists():
        return str(agents_tasks)
    return f"{project.repo_root}/.tasks"


def render_read_first_lines(session: Any, project: Any, engineer: Any) -> list[str]:
    tasks_root = _resolve_tasks_root(project)
    repo_root = project.repo_root
    todo_path = f"{tasks_root}/{session.engineer_id}/TODO.md"
    project_doc = f"{tasks_root}/PROJECT.md"
    tasks_doc = f"{tasks_root}/TASKS.md"
    status_doc = f"{tasks_root}/STATUS.md"
    if engineer.role in _SPECIALIST_ROLES:
        return [f"**Read first:** `{todo_path}`"]
    lines = [
        "## Read First",
        "",
        f"1. `{todo_path}`",
        f"2. `{project_doc}`",
        f"3. `{tasks_doc}`",
    ]
    next_index = 4
    include_team_ownership = (
        engineer.role in {"memory", "project-memory", "memory-oracle", "planner", "planner-dispatcher"}
        and _is_multi_team_project(project.name)
    )
    if include_team_ownership:
        lines.append(f"{next_index}. `{tasks_root}/TEAM_OWNERSHIP.md`")
        next_index += 1
    quality_gate_path = ""
    if engineer.role in {"memory", "project-memory", "memory-oracle"} and _is_multi_team_project(project.name):
        quality_gate_path = _quality_gate_path_for_project(project.name, tasks_root)
    elif engineer.role in {"planner", "planner-dispatcher"}:
        quality_gate_path = _quality_gate_path_for_seat(
            project.name,
            str(getattr(session, "engineer_id", "") or ""),
            tasks_root,
        )
    if quality_gate_path:
        lines.append(f"{next_index}. `{quality_gate_path}`")
        next_index += 1
    if engineer.role in {"memory", "project-memory", "memory-oracle", "planner", "planner-dispatcher"}:
        lines.append(f"{next_index}. `{status_doc}`")
        next_index += 1
    if engineer.role in {"frontstage-supervisor"}:
        lines.append(f"{next_index}. `{status_doc}`")
        next_index += 1
    if engineer.role == "planner-dispatcher":
        planner_brief = Path(tasks_root) / "planner/PLANNER_BRIEF.md"
        if planner_brief.exists():
            lines.append(f"{next_index}. `{planner_brief}`")
            next_index += 1
        warden_brief = Path(tasks_root) / "warden/WARDEN_BRIEF.md"
        if warden_brief.exists():
            lines.append(f"{next_index}. `{warden_brief}`")
            next_index += 1
    role_contract = None
    if engineer.role == "frontstage-supervisor":
        candidate = Path(repo_root) / "KODER.md"
        if candidate.exists():
            role_contract = str(candidate)
    if role_contract:
        lines.append(f"{next_index}. `{role_contract}`")
        next_index += 1
    roster_contract = None
    if engineer.role == "frontstage-supervisor":
        candidate = Path(repo_root) / ".tasks/FE-003-SPECIALIST-ROSTER.md"
        if candidate.exists():
            roster_contract = str(candidate)
    if roster_contract:
        lines.append(f"{next_index}. `{roster_contract}`")
        next_index += 1
    lines.append(f"{next_index}. task-specific docs referenced by the current TODO")
    return lines


def render_harness_runtime_lines(engineer: Any) -> list[str]:
    if engineer.role in _SPECIALIST_ROLES or engineer.role == "planner-dispatcher":
        return []
    skills = list(getattr(engineer, "skills", []) or [])
    if not any("gstack-harness/SKILL.md" in skill for skill in skills):
        return []
    lines = [
        "`gstack-harness` provides the shared runtime for:",
        "",
        "- seat/runtime schema",
        "- dispatch/completion/ACK protocol",
        "- heartbeat / patrol / unblock loop",
        "- CLI control console",
    ]
    return lines


def render_role_scope_summary(engineer: Any) -> str:
    role = engineer.role
    if role == "frontstage-supervisor":
        return "intake framing, seat launch, patrol, unblock, and escalation"
    if role == "solo-tui":
        return "professional intent analysis, root-cause evidence, AI-friendly specs, product validation, and lightweight direct fixes"
    if role == "planner-dispatcher":
        return "task initialization, research coordination, execution planning, next-hop routing, and durable consumption of completions"
    if role == "builder":
        return "implementation and code changes"
    if role == "reviewer":
        return "code review and canonical verdicts"
    if role == "patrol":
        return "patrol verification, repro, and regression checks"
    if role == "designer":
        return "design review, visual direction, and prototype guidance"
    return "assigned seat responsibilities"


def role_matches(role: str, expected: str) -> bool:
    normalized = role.strip()
    if expected == "planner":
        return normalized in {"planner", "planner-dispatcher"}
    return normalized == expected


def preferred_seat_for_role(
    project: Any | None,
    expected_role: str,
    *,
    project_engineers: dict[str, Any] | None = None,
    engineer_order: list[str] | None = None,
    exclude: set[str] | None = None,
) -> str | None:
    if project is None:
        return None
    engineers = project_engineers or {}
    ordered_engineer_ids = list(engineer_order or project.engineers or engineers.keys())
    blocked = exclude or set()
    candidates = [
        engineer_id
        for engineer_id in ordered_engineer_ids
        if engineer_id not in blocked
        and role_matches(getattr(engineers.get(engineer_id), "role", ""), expected_role)
    ]
    preferred = {
        "planner": "planner",
        "builder": "builder-1",
        "reviewer": "reviewer-1",
        "patrol": "patrol-1",
        "designer": "designer-1",
    }.get(expected_role)
    if preferred in candidates:
        return preferred
    if candidates:
        return candidates[0]
    return None


def _load_dynamic_profile_data(project_name: str) -> tuple[Path, dict[str, Any]] | None:
    try:
        from resolve import dynamic_profile_path as _dpp  # noqa: PLC0415
    except Exception:
        return None
    profile_path = _dpp(project_name)
    if not profile_path.is_file():
        return None
    try:
        with profile_path.open("rb") as fh:
            data = _toml_load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data = _merge_project_toml_ssot(project_name, data)
    return profile_path, data


def _merge_project_toml_ssot(project_name: str, profile_data: dict[str, Any]) -> dict[str, Any]:
    """Overlay project.toml roster/runtime choices onto dynamic profile metadata."""
    project_toml = _ws_effective_home() / ".agents" / "projects" / project_name / "project.toml"
    if not project_toml.exists():
        return profile_data
    try:
        with project_toml.open("rb") as fh:
            project_data = _toml_load(fh)
    except Exception:
        return profile_data
    if not isinstance(project_data, dict):
        return profile_data

    active_seats = [
        str(item).strip()
        for item in (project_data.get("engineers") or [])
        if str(item).strip()
    ]
    if not active_seats:
        return profile_data
    active = set(active_seats)

    merged: dict[str, Any] = dict(profile_data)
    merged["seats"] = active_seats

    profile_overrides = profile_data.get("seat_overrides") or {}
    if not isinstance(profile_overrides, dict):
        profile_overrides = {}
    project_overrides = project_data.get("seat_overrides") or {}
    if not isinstance(project_overrides, dict):
        project_overrides = {}
    overrides: dict[str, dict[str, Any]] = {}
    for seat in active_seats:
        base = profile_overrides.get(seat) if isinstance(profile_overrides.get(seat), dict) else {}
        project_override = (
            project_overrides.get(seat) if isinstance(project_overrides.get(seat), dict) else {}
        )
        overrides[seat] = {**base, **project_override}
    merged["seat_overrides"] = overrides

    profile_roles = profile_data.get("seat_roles") or {}
    if not isinstance(profile_roles, dict):
        profile_roles = {}
    roles: dict[str, str] = {}
    for seat in active_seats:
        override = overrides.get(seat) or {}
        role = str(override.get("role") or profile_roles.get(seat) or "").strip()
        if seat == "memory":
            role = "project-memory"
        elif not role and str(override.get("team") or "").strip():
            role = "planner"
        roles[seat] = role
    merged["seat_roles"] = roles

    profile_teams = profile_data.get("teams") or {}
    if not isinstance(profile_teams, dict):
        profile_teams = {}
    teams: dict[str, dict[str, Any]] = {}
    for team_name, team_cfg in profile_teams.items():
        if not isinstance(team_cfg, dict):
            continue
        seats = [str(item) for item in (team_cfg.get("seats") or []) if str(item) in active]
        if seats:
            teams[str(team_name)] = {**team_cfg, "seats": seats}
    for seat in active_seats:
        override = overrides.get(seat) or {}
        team = str(override.get("team") or "").strip()
        if not team:
            continue
        cfg = dict(teams.get(team) or {})
        seats = [str(item) for item in (cfg.get("seats") or [])]
        if seat not in seats:
            seats.append(seat)
        cfg.setdefault("team_type", "subteam")
        cfg.setdefault("notify_policy", "queue_drained_only")
        cfg["seats"] = seats
        teams[team] = cfg
    merged["teams"] = teams
    return merged


def _role_for_profile_seat(profile_data: dict[str, Any], seat_id: str) -> str:
    roles = profile_data.get("seat_roles") or {}
    if isinstance(roles, dict):
        role = str(roles.get(seat_id) or "").strip()
        if role:
            return role
    return ""


def _team_for_profile_seat(profile_data: dict[str, Any], seat_id: str) -> tuple[str, dict[str, Any]] | None:
    mode = profile_data.get("mode") or {}
    if not isinstance(mode, dict) or str(mode.get("team_structure") or "single") != "multi":
        return None
    project_memory = str(mode.get("project_memory") or "memory").strip() or "memory"
    if seat_id == project_memory:
        return None
    teams = profile_data.get("teams") or {}
    if not isinstance(teams, dict):
        return None
    for team_name, team_cfg in teams.items():
        if not isinstance(team_cfg, dict):
            continue
        seats = [str(item) for item in team_cfg.get("seats") or []]
        if seat_id in seats:
            return str(team_name), team_cfg
    return None


def _is_project_memory_seat(profile_data: dict[str, Any], seat_id: str) -> bool:
    mode = profile_data.get("mode") or {}
    if not isinstance(mode, dict) or str(mode.get("team_structure") or "single") != "multi":
        return False
    project_memory = str(mode.get("project_memory") or "memory").strip() or "memory"
    return seat_id == project_memory


def _is_multi_team_project(project_name: str) -> bool:
    loaded = _load_dynamic_profile_data(project_name)
    if loaded is None:
        return False
    _profile_path, profile_data = loaded
    mode = profile_data.get("mode") or {}
    return isinstance(mode, dict) and str(mode.get("team_structure") or "single") == "multi"


def _team_type_for(team_name: str, team_cfg: dict[str, Any]) -> str:
    explicit = str(team_cfg.get("team_type") or "").strip()
    if explicit:
        return explicit
    if team_name == "quality-docs" or bool(team_cfg.get("autonomous")):
        return "quality-docs"
    return "subteam"


def _planner_mode_for(team_name: str, team_cfg: dict[str, Any]) -> str:
    explicit = str(team_cfg.get("planner_mode") or "").strip()
    if explicit:
        return explicit
    return "quality_campaign" if _team_type_for(team_name, team_cfg) == "quality-docs" else "delivery"


def _notify_policy_for(team_name: str, team_cfg: dict[str, Any]) -> str:
    explicit = str(team_cfg.get("notify_policy") or "").strip()
    if explicit:
        return explicit
    return "never_notify_memory" if _team_type_for(team_name, team_cfg) == "quality-docs" else "queue_drained_only"


def _quality_gate_doc_for(team_name: str, team_cfg: dict[str, Any]) -> str:
    if _team_type_for(team_name, team_cfg) != "quality-docs":
        return ""
    return str(team_cfg.get("quality_gate_doc") or DEFAULT_QUALITY_GATE_DOC).strip()


def _resolve_project_doc_path(tasks_root: str, doc_path: str) -> str:
    if not doc_path:
        return ""
    if doc_path.startswith("/"):
        return doc_path
    return f"{tasks_root}/{doc_path.lstrip('/')}"


def _quality_gate_path_for_project(project_name: str, tasks_root: str) -> str:
    loaded = _load_dynamic_profile_data(project_name)
    if loaded is None:
        return ""
    _profile_path, profile_data = loaded
    teams = profile_data.get("teams") or {}
    if not isinstance(teams, dict):
        return ""
    for team_name, team_cfg in teams.items():
        if not isinstance(team_cfg, dict):
            continue
        doc_path = _quality_gate_doc_for(str(team_name), team_cfg)
        if doc_path:
            return _resolve_project_doc_path(tasks_root, doc_path)
    return ""


def _quality_gate_path_for_seat(project_name: str, seat_id: str, tasks_root: str) -> str:
    loaded = _load_dynamic_profile_data(project_name)
    if loaded is None:
        return ""
    _profile_path, profile_data = loaded
    team_info = _team_for_profile_seat(profile_data, seat_id)
    if team_info is None:
        return ""
    team_name, team_cfg = team_info
    doc_path = _quality_gate_doc_for(team_name, team_cfg)
    return _resolve_project_doc_path(tasks_root, doc_path) if doc_path else ""


def _render_runtime_fragment(override: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("tool", "auth_mode", "provider", "model"):
        value = str(override.get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    return ", ".join(parts) if parts else "runtime not declared"


def render_multi_team_project_ownership_lines(session: Any, project: Any) -> list[str]:
    loaded = _load_dynamic_profile_data(project.name)
    if loaded is None:
        return []
    profile_path, profile_data = loaded
    seat_id = str(getattr(session, "engineer_id", "") or "").strip()
    if not _is_project_memory_seat(profile_data, seat_id):
        return []

    teams = profile_data.get("teams") or {}
    if not isinstance(teams, dict):
        return []
    roles = profile_data.get("seat_roles") or {}
    if not isinstance(roles, dict):
        roles = {}
    overrides = profile_data.get("seat_overrides") or {}
    if not isinstance(overrides, dict):
        overrides = {}

    tasks_root = _resolve_tasks_root(project)
    quality_gate_path = _quality_gate_path_for_project(project.name, tasks_root)
    lines = [
        "## Project Team Ownership",
        "",
        f"- Profile: `{profile_path}`",
        f"- Project mode: `multi`",
        f"- Project memory: `{seat_id}`",
        f"- Ownership summary doc: `{tasks_root}/TEAM_OWNERSHIP.md`",
        "- Memory owns stable roster/ownership facts; team planners own per-task workflows.",
    ]
    if quality_gate_path:
        lines.append(f"- Memory acceptance preflight: read quality gate `{quality_gate_path}` before commit/push.")
    lines.extend(["", "## Teams", ""])
    for team_name, team_cfg in teams.items():
        if not isinstance(team_cfg, dict):
            continue
        team_name_str = str(team_name)
        team_type = _team_type_for(team_name_str, team_cfg)
        planner_mode = _planner_mode_for(team_name_str, team_cfg)
        notify_policy = _notify_policy_for(team_name_str, team_cfg)
        quality_gate_doc = _quality_gate_doc_for(team_name_str, team_cfg)
        team_seats = [str(item) for item in team_cfg.get("seats") or []]
        ownership_paths = [
            str(item).strip()
            for item in team_cfg.get("ownership_paths") or []
            if str(item).strip()
        ]
        seat_fragments: list[str] = []
        for managed_seat in team_seats:
            role = str(roles.get(managed_seat) or "unknown").strip() or "unknown"
            override = overrides.get(managed_seat) if isinstance(overrides.get(managed_seat), dict) else {}
            instance = str(override.get("instance") or "").strip()
            label = f"{role}-{instance}" if instance else role
            purpose = str(override.get("purpose") or "").strip()
            purpose_suffix = f" ({purpose})" if purpose else ""
            seat_fragments.append(f"{label}: `{managed_seat}`{purpose_suffix}")
        team_bits = [
            f"type `{team_type}`",
            f"planner_mode `{planner_mode}`",
            f"notify_policy `{notify_policy}`",
        ]
        if team_cfg.get("autonomous"):
            team_bits.append("autonomous")
        review_model = str(team_cfg.get("review_model") or "").strip()
        if review_model:
            team_bits.append(f"review `{review_model}`")
        if ownership_paths:
            team_bits.append("paths " + ", ".join(f"`{path}`" for path in ownership_paths))
        else:
            team_bits.append("paths not declared")
        lines.append(f"- `{team_name}`: " + "; ".join(team_bits))
        if quality_gate_doc:
            lines.append(f"  Quality gate: `{quality_gate_doc}`")
        if seat_fragments:
            lines.append("  Seats: " + "; ".join(seat_fragments))
        if team_type == "quality-docs":
            lines.append(
                "  Boundary: continuous QA/docs only; no product code edits; findings are recorded for memory's pull-based gate."
            )
    return lines


def render_multi_team_scope_lines(session: Any, project: Any) -> list[str]:
    loaded = _load_dynamic_profile_data(project.name)
    if loaded is None:
        return []
    profile_path, profile_data = loaded
    seat_id = str(getattr(session, "engineer_id", "") or "").strip()
    team_info = _team_for_profile_seat(profile_data, seat_id)
    if team_info is None:
        return []
    team_name, team_cfg = team_info
    team_type = _team_type_for(team_name, team_cfg)
    planner_mode = _planner_mode_for(team_name, team_cfg)
    notify_policy = _notify_policy_for(team_name, team_cfg)
    quality_gate_doc = _quality_gate_doc_for(team_name, team_cfg)
    tasks_root = _resolve_tasks_root(project)
    team_seats = [str(item) for item in team_cfg.get("seats") or []]
    overrides = profile_data.get("seat_overrides") or {}
    if not isinstance(overrides, dict):
        overrides = {}
    current_role = _role_for_profile_seat(profile_data, seat_id)
    ownership_paths = [
        str(item).strip()
        for item in team_cfg.get("ownership_paths") or []
        if str(item).strip()
    ]
    scaling_policy = team_cfg.get("scaling_policy") or {}
    if not isinstance(scaling_policy, dict):
        scaling_policy = {}

    seat_override = overrides.get(seat_id) if isinstance(overrides.get(seat_id), dict) else {}
    seat_display_name = str(seat_override.get("display_name") or "").strip()
    seat_label = f"`{seat_display_name}` (`{seat_id}`)" if seat_display_name and seat_display_name != seat_id else f"`{seat_id}`"

    lines = [
        "## Team Scope",
        "",
        f"- Profile: `{profile_path}`",
        f"- Project mode: `multi`",
        f"- Your team: `{team_name}`",
        f"- Your seat: {seat_label} (`{current_role or 'role not declared'}`)",
        f"- Team type: `{team_type}`",
        f"- Planner mode: `{planner_mode}`",
        f"- Notify policy: `{notify_policy}`",
        f"- Team queue: `~/.agents/tasks/{project.name}/{team_name}/tasks.queue.jsonl`",
    ]
    if quality_gate_doc:
        lines.append(f"- Quality gate doc: `{_resolve_project_doc_path(tasks_root, quality_gate_doc)}`")
    if ownership_paths:
        lines.append(
            "- Team ownership paths: "
            + ", ".join(f"`{path}`" for path in ownership_paths)
        )
    else:
        lines.append("- Team ownership paths: not declared; ask memory to clarify before broad dispatch")
    if scaling_policy:
        policy_items = ", ".join(
            f"{key}={value}" for key, value in sorted(scaling_policy.items())
        )
        lines.append(f"- Scaling policy: `{policy_items}`")

    lines.extend(["", "## Managed Team Seats", ""])
    builders: list[str] = []
    reviewer_seats: list[str] = []
    for managed_seat in team_seats:
        role = _role_for_profile_seat(profile_data, managed_seat) or "unknown"
        override = overrides.get(managed_seat) if isinstance(overrides.get(managed_seat), dict) else {}
        if role == "builder":
            builders.append(managed_seat)
        elif role == "reviewer":
            reviewer_seats.append(managed_seat)
        details: list[str] = [f"role `{role}`", _render_runtime_fragment(override)]
        instance = str(override.get("instance") or "").strip()
        if instance:
            details.append(f"instance `{instance}`")
        purpose = str(override.get("purpose") or "").strip()
        if purpose:
            details.append(f"purpose: {purpose}")
        capabilities = [
            str(item).strip()
            for item in override.get("capabilities") or []
            if str(item).strip()
        ]
        if capabilities:
            details.append("capabilities: " + ", ".join(f"`{item}`" for item in capabilities))
        managed_dn = str(override.get("display_name") or "").strip()
        managed_label = (
            f"`{managed_dn}` (`{managed_seat}`)"
            if managed_dn and managed_dn != managed_seat
            else f"`{managed_seat}`"
        )
        marker = " (you)" if managed_seat == seat_id else ""
        lines.append(f"- {managed_label}{marker}: " + "; ".join(details))

    if current_role in {"planner", "planner-dispatcher"} and planner_mode == "quality_campaign":
        patrols = [
            managed_seat
            for managed_seat in team_seats
            if _role_for_profile_seat(profile_data, managed_seat) == "patrol"
        ]
        lines.extend(["", "## Quality Campaign Rules", ""])
        lines.extend(
            [
                "- Do not notify memory directly; update `QUALITY.md`, findings, campaigns, missions, and evidence instead.",
                "- Design high-frequency patrol missions from dev queues, workflow docs, deliveries, git diff, flaky history, and open risks.",
                "- After a clean mission, raise difficulty; after three clean rounds for one patrol/campaign, switch that patrol to a new attack surface.",
                "- Research likely root cause for every finding, but do not edit product implementation or directly command dev teams.",
                "- Memory pulls this quality gate before final acceptance, commit, or push.",
            ]
        )
        if patrols:
            lines.append("- Patrol seats: " + ", ".join(f"`{seat}`" for seat in patrols))
        else:
            lines.append("- No patrol seats are declared; ask memory for roster repair.")
    elif current_role in {"planner", "planner-dispatcher"}:
        max_builders = int(scaling_policy.get("max_builders", -1))
        planner_self_contained = bool(team_cfg.get("planner_self_contained", False))
        is_planner_only = planner_self_contained or max_builders == 0
        if is_planner_only:
            lines.extend(["", "## Planner-Only Mode", ""])
            lines.extend(
                [
                    "- Self-contained: research, implement, tests, self-review, `task_done`, and queue-drained relay all owned by this planner.",
                    "- Do not dispatch builder work; this team has no builder seat.",
                    "- Escalate to memory only for roster changes, permission decisions, or operator authority blockers.",
                    "- After task PASS, append `task_done`, claim/continue the next queued task, and do not notify memory per task.",
                    "- Notify memory only when this team queue is drained or an exception needs memory/operator authority.",
                ]
            )
        else:
            lines.extend(["", "## Dev Planner Dispatch Rules", ""])
            lines.extend(
                [
                    "- Research the task, define verification/checklist first, then dispatch implementation to the exact owning builder seat.",
                    "- Prefer writing or naming acceptance tests before builder implementation; builder must not weaken planner acceptance tests.",
                    "- When builder delivery fails, send concrete rework to that builder until acceptance passes or the rework threshold is hit.",
                    "- After task PASS, append `task_done`, claim/continue the next queued task, and do not notify memory per task.",
                    "- Notify memory only when this team queue is drained or an exception needs memory/operator authority.",
                ]
            )
            lines.extend(["", "## Builder Assignment Rules", ""])
            if builders:
                lines.append(
                    "- Available builders in this team: "
                    + ", ".join(f"`{builder}`" for builder in builders)
                )
            else:
                lines.append("- No builder is declared for this team; bounce implementation work to memory.")
            if reviewer_seats:
                lines.append("- Reviewer gate: " + ", ".join(f"`{seat}`" for seat in reviewer_seats))
            elif len(builders) > 1:
                lines.append("- Reviewer gate missing for multiple builders; block and ask memory for roster repair.")
            else:
                lines.append("- Reviewer fallback: planner reviews only because this team has one builder.")
            lines.extend(
                [
                    "- With multiple builders, never dispatch to bare role `builder`; choose an exact `owner_seat`.",
                    "- Assign by declared `capabilities`, `purpose`, and `ownership_paths` first; then by disjoint files/tests.",
                    "- Keep the same file or tightly coupled module on one builder unless the workflow declares a merge owner.",
                    "- Run parallel builder waves only when write scopes are disjoint and fan-in is explicit before review.",
                    "- If a fourth builder would be useful, stop and ask memory to propose a new subteam.",
                ]
            )
    return lines


def render_project_seat_map_lines(
    session: Any,
    project: Any,
    engineer: Any,
    *,
    project_engineers: dict[str, Any] | None = None,
    engineer_order: list[str] | None = None,
) -> list[str]:
    memory_lines = render_multi_team_project_ownership_lines(session, project)
    if memory_lines:
        return memory_lines
    lines = render_multi_team_scope_lines(session, project)
    if lines and engineer.role in {"planner", "planner-dispatcher"}:
        return lines
    if engineer.role not in {"frontstage-supervisor", "planner-dispatcher"}:
        return lines
    engineers = project_engineers or {}
    ordered_engineer_ids = list(engineer_order or project.engineers or engineers.keys())
    seat_lines: list[str] = []
    for engineer_id in ordered_engineer_ids:
        mapped_engineer = engineers.get(engineer_id)
        if not mapped_engineer:
            continue
        runtime = (
            f"{mapped_engineer.default_tool} / "
            f"{mapped_engineer.default_auth_mode} / "
            f"{mapped_engineer.default_provider}"
        )
        scope = render_role_scope_summary(mapped_engineer)
        seat_lines.append(f"- `{engineer_id}` -> `{mapped_engineer.role}`: {scope} (`{runtime}`)")
    if not seat_lines:
        return lines
    if lines:
        lines.append("")
    lines.extend([
        "## Project Seat Map",
        "",
        f"- Current project role order: `{' -> '.join(ordered_engineer_ids)}`",
    ])
    lines.extend(seat_lines)
    return lines


def render_seat_boundary_lines(session: Any, engineer: Any) -> list[str]:
    seat_name = session.engineer_id
    project_name = str(getattr(session, "project", "") or "").strip()
    multi_team_context: tuple[str, dict[str, Any]] | None = None
    if project_name:
        loaded_profile = _load_dynamic_profile_data(project_name)
        if loaded_profile is not None:
            _profile_path, profile_data = loaded_profile
            multi_team_context = _team_for_profile_seat(profile_data, seat_name)
    planner_seat = (
        preferred_seat_for_role(
            getattr(session, "project_record", None),
            "planner",
            project_engineers=getattr(session, "project_engineers", None),
            engineer_order=getattr(session, "engineer_order", None),
        )
        or "planner"
    )
    lines = ["## Seat Boundary", ""]
    if engineer.role == "solo-tui":
        lines.extend(
            [
                f"- `{seat_name}` owns professional intent analysis, root-cause evidence packets, AI-friendly specs, product validation, and lightweight direct fixes.",
                "- do not monitor or poll by default; inspect panes, queues, logs, events, or artifacts only when the user asks",
                "- do not become project memory, planner, queue owner, or canonical dispatch authority unless the user explicitly changes the role",
                "- when relaying work, keep the request short and include goal, context, boundary, anti-goal when needed, acceptance, and delivery",
                "- for product/code work, hand the brief to memory for queue/state tracking instead of taking over the task chain",
                "- during explicit or scheduled patrol, review delivered-but-unintegrated work; merge passing work only to this project's review/latest and notify the user",
                "- when opening a webpage for the user, default to Cartooner's built-in Inspiration Browser; use external browsers only for explicit request, auth boundary, or debugging need",
                "- when a reply is needed, include the exact temporary reply path in the message: chat, file, inbox, script, and target session if applicable",
                "- use product-test personas only when validation needs them; use internal evidence to diagnose or verify",
                "- directly fix framework, template, or automation defects in this seat's owned scope; avoid forwarding vague issues",
            ]
        )
    elif engineer.role == "frontstage-supervisor":
        lines.extend(
            [
                f"- `{seat_name}` owns intake framing, seat launch orchestration, patrol, unblock, and escalations.",
                f"- do intake framing and scope clarification before handing active work to `{planner_seat}`",
                f"- do not own execution planning or next-hop routing; that belongs to `{planner_seat}`",
                f"- use document-first dispatch helpers when handing work to `{planner_seat}`; do not hand-write task chain state unless the helper path is unavailable",
                "- before launching any non-frontstage seat, summarize harness/profile, seat/role, tool/runtime, and auth/provider to the user and wait for confirmation",
                "- once planner is live in an OpenClaw/Feishu setup, proactively ask the user for the target Feishu group ID; do not wait for the user to request group wiring",
                "- after the group ID arrives, require an explicit project-binding confirmation: bind the current project, switch to another existing project, or create a new project; do not treat a new group as an automatic new project",
                "- in that bridge flow, keep `main` mention-gated and keep the project-facing `koder` account non-mention-gated by default; optional system seats such as `warden` only become non-mention-gated when they are explicitly deployed",
                "- planner should treat the bound group as the user-visible bridge for `OC_DELEGATION_REPORT_V1` closeouts; keep legacy auto-broadcast disabled by default; opt-in requires CLAWSEAT_ENABLE_LEGACY_FEISHU_BROADCAST=1",
                f"- once group ID and project binding are both confirmed, immediately hand the Feishu smoke test to `{planner_seat}`, tell the user “收到测试消息即可回复希望完成什么任务”, and bring up `reviewer-1` in parallel when that seat exists",
                "- if the current chain is verification-heavy, bring up `patrol-1` in parallel with or immediately after `reviewer-1`; do not treat patrol as a first-launch seat",
                f"- remind `{planner_seat}` when drift appears; do not silently reroute specialists yourself",
                "- do not absorb builder, reviewer, patrol, or designer specialist work",
            ]
        )
    elif engineer.role == "planner-dispatcher":
        if multi_team_context is not None:
            team_name, team_cfg = multi_team_context
            planner_mode = _planner_mode_for(team_name, team_cfg)
            notify_policy = _notify_policy_for(team_name, team_cfg)
            lines.extend(
                [
                    f"- `{seat_name}` owns `{team_name}` execution decisions, next-hop routing, durable consumption, and planner-side acceptance.",
                    f"- team notify policy is `{notify_policy}`; do not use legacy single-team closeout semantics in multi-team mode.",
                    "- use document-first dispatch helpers; treat raw `tmux send-keys` as a protocol violation",
                ]
            )
            if planner_mode == "quality_campaign":
                lines.extend(
                    [
                        f"- expect patrol seats to return findings and evidence to `{seat_name}`, never directly to memory",
                        "- never notify memory directly; update `QUALITY.md`, findings, campaigns, missions, and evidence instead",
                        "- escalate to project memory only for roster, ownership, queue-dependency, or operator-authority decisions",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"- expect builders/reviewers to return completion to `{seat_name}`, not directly to memory",
                        "- after task PASS, append `task_done`, claim or continue the next queued task, and avoid per-task memory pings",
                        "- notify project memory only when this team queue is drained or an exception needs memory/operator authority",
                    ]
                )
            return lines
        lines.extend(
            [
                f"- `{seat_name}` owns execution decisions, next-hop routing, and durable consumption of specialist completions.",
                f"- expect specialists to return completion to `{seat_name}`, not directly to koder",
                "- when planner has just been initialized for an OpenClaw/Feishu workflow, return a short ready signal so frontstage can finish group binding and the bridge smoke test",
                "- use `gstack-harness/scripts/dispatch_task.py` as the default path for planner -> specialist dispatch; do not hand-write TODO/TASKS/STATUS unless the helper is unavailable",
                "- use document-first dispatch helpers; treat raw `tmux send-keys` as a protocol violation",
                "- escalate back to koder only when direction, seat boundaries, or model/auth choices need frontstage help",
            ]
        )
    else:
        lines = [f"Specialist seat. Execute `TODO.md` and return to `{planner_seat}`."]
    return lines


_CARTOONER_TEMPLATES = frozenset({"clawseat-creative"})


def render_communication_protocol_lines(
    engineer: Any,
    project_name: str,
    *,
    template_name: str = "",
    seat_id: str = "",
) -> list[str]:
    send_script = str(SEND_AND_VERIFY_SH)
    # Creative templates branch FIRST — even for "specialist" seats like
    # patrol (which exists in both engineering and creative templates and
    # would otherwise short-circuit to the engineering specialist stub).
    if template_name in _CARTOONER_TEMPLATES:
        return _render_communication_protocol_cartooner(
            engineer, project_name, send_script
        )

    if engineer.role == "solo-tui":
        lines = [
            "## Communication Protocol",
            "",
            "- default to natural language; do not wrap product or SDK testing in internal protocol",
            "- if another TUI, SDK, memory seat, or product chat must reply, state the temporary reply path in the same message",
            "- acceptable reply paths include this chat, an absolute file path, an inbox path, or a provided send script plus exact target session",
            "- use scripts such as peer-send only when the task needs TUI transport; otherwise send the concise professional request directly",
            "- do not assume a standing backchannel; repeat the reply method whenever a reply matters",
            "- treat logs, events, queues, and delivery files as diagnostic evidence, not as the default interaction surface",
        ]
        return lines

    if engineer.role in _SPECIALIST_ROLES:
        return ["Read `TOOLS/protocol.md` for full communication protocol."]
    notify_script = str(HARNESS_SCRIPTS_ROOT / "notify_seat.py")
    planner_seat = (
        preferred_seat_for_role(
            getattr(engineer, "_project_record", None),
            "planner",
            project_engineers=getattr(engineer, "_project_engineers", None),
            engineer_order=getattr(engineer, "_engineer_order", None),
        )
        or "planner"
    )
    lines = [
        "## Communication Protocol",
        "",
        "- treat `TODO.md`, `DELIVERY.md`, and handoff receipts as the source of truth; tmux/chat only wakes the next seat up",
        "- read `source` and `reply_to` in `TODO.md` to know who dispatched the task and who should receive the completion",
        f"- for any seat-to-seat notification, use `{send_script}` as the default transport",
        f"- in multi-project mode, if you call `send-and-verify.sh` directly, pass `--project {project_name}` or use the canonical session name for this project",
        f"- prefer `{notify_script}` for ad hoc reminders or unblock notices instead of composing transport by hand",
        "- treat raw `tmux send-keys` as a protocol violation unless the transport script is unavailable",
        "- if a fallback is unavoidable, replicate the transport contract: send text, wait 1 second, send `Enter`, then verify the message did not remain stranded in the input buffer",
        "",
        "## Canonical Dispatch & Receipt (LL3 + OO)",
        "- canonical dispatch: `dispatch_task.py --profile <profile> --target <seat> --task-id <id> --title <t> --objective <o> --test-policy <p>`",
        "- canonical receipt (two required steps): `complete_handoff.py` writes the `.consumed` durable receipt, then `send-and-verify.sh` sends the wake-up",
        "- send-and-verify does not substitute for complete_handoff.py; the former is wake-up only, the latter is required for chain audit",
        "",
        "## Fan-out Default (LL6)",
        "- tasks with 2+ independent sub-goals (disjoint files / disjoint tests / disjoint research lanes / multi-part) must fan-out via parallel sub-agents",
        "- fan out independent sub-goals via the seat dispatch primitive; only serialize the final cross-check / delivery step",
    ]
    if engineer.role == "frontstage-supervisor":
        lines.extend(
            [
                f"- when patrol finds waiting approvals or drift, unblock or remind `{planner_seat}`; do not replace `{planner_seat}` as planner",
                f"- when handing work to `{planner_seat}`, default to `gstack-harness/scripts/dispatch_task.py` so the dispatch leaves `source`, `reply_to`, and a receipt",
                "- after starting a seat, refresh the project window so tabs stay in canonical role order",
                "- after planner startup in an OpenClaw/Feishu workflow, ask the user for the group ID, verify it from `~/.openclaw/agents/*/sessions/sessions.json` if possible, then require explicit project binding before expecting unattended group traffic",
                "- in that flow, require `main` to stay on `requireMention=true`; keep the project-facing `koder` account non-mention-gated by default, and only add optional system seats such as `warden` when they are explicitly deployed for that group",
                "- once the group ID and project binding are known, treat the Feishu group as the user-visible bridge for explicit smoke tests and OC_DELEGATION_REPORT_V1 closeouts; do not rely on the legacy auto-broadcast path as the control packet",
                f"- after the group bridge is ready, dispatch the first smoke test to `{planner_seat}`, tell the user “收到测试消息即可回复希望完成什么任务”, and start `reviewer-1` in parallel when present",
                "- when the planner bridge uses `lark-cli --as user`, do not trust sender identity in the group; only treat `OC_DELEGATION_REPORT_V1` as a machine-readable delegation receipt",
                f"- when `{planner_seat}` returns a planning memo or execution plan with `FrontstageDisposition: AUTO_ADVANCE`, convert it into downstream dispatch promptly instead of leaving it parked at frontstage",
                f"- when `{planner_seat}` returns a closeout receipt, summarize it for the user in plain language and auto-advance by default; only ask the user to decide when the receipt explicitly says `FrontstageDisposition: USER_DECISION_NEEDED`",
                "- when that closeout becomes visible in the group, read the linked delivery trail, reconcile the wrap-up, and update the project docs before giving the user the summary",
                "- planner -> frontstage closeout should also refresh `koder/TODO.md` so frontstage keeps a durable current-task anchor across compaction or restarts",
            ]
        )
    elif engineer.role == "planner-dispatcher":
        multi_team_context: tuple[str, dict[str, Any]] | None = None
        if seat_id:
            loaded_profile = _load_dynamic_profile_data(project_name)
            if loaded_profile is not None:
                _profile_path, profile_data = loaded_profile
                multi_team_context = _team_for_profile_seat(profile_data, seat_id)
        if multi_team_context is not None:
            team_name, team_cfg = multi_team_context
            planner_mode = _planner_mode_for(team_name, team_cfg)
            notify_policy = _notify_policy_for(team_name, team_cfg)
            lines.extend(
                [
                    "- dispatch via `dispatch_task.py` (not raw tmux); always pass `--test-policy` and `--intent` to activate the gstack skill",
                    "- stamp durable `Consumed:` ACK before routing the next hop; ACK alone does NOT finish the chain",
                    "- use canonical verdicts: `APPROVED` / `APPROVED_WITH_NITS` / `CHANGES_REQUESTED` / `BLOCKED` / `DECISION_NEEDED`",
                    f"- this team is `{team_name}` with notify policy `{notify_policy}`; do not use legacy single-team closeout commands",
                ]
            )
            if planner_mode == "quality_campaign":
                lines.append(
                    "- never notify memory directly; update `QUALITY.md`, findings, campaigns, missions, and evidence for memory to pull during acceptance"
                )
            else:
                lines.append(
                    "- when the team queue is drained, notify project memory with a concise queue-drained closeout and evidence links; do not send per-task memory pings"
                )
            return lines
        lines.extend(
            [
                "- dispatch via `dispatch_task.py` (not raw tmux); always pass `--test-policy` and `--intent` to activate the gstack skill",
                "- stamp durable `Consumed:` ACK before routing the next hop; ACK alone does NOT finish the chain",
                "- use canonical verdicts: `APPROVED` / `APPROVED_WITH_NITS` / `CHANGES_REQUESTED` / `BLOCKED` / `DECISION_NEEDED`",
                "- closeout to koder via `complete_handoff.py --frontstage-disposition AUTO_ADVANCE --user-summary ...`",
                "- Feishu: emit `OC_DELEGATION_REPORT_V1` via `send_delegation_report.py`; legacy broadcast opt-in only. See `TOOLS/feishu.md`.",
            ]
        )
    else:
        lines.extend(
            [
                f"- when you complete assigned work, update `DELIVERY.md`, call `complete_handoff.py` to write the durable receipt, then wake `{planner_seat}` with `send-and-verify.sh --project {project_name} {planner_seat} ...`; send-and-verify is wake-up only and cannot substitute",
                "- if you are reviewing work, include a canonical `Verdict:` field in `DELIVERY.md`",
            ]
        )
    return lines


def _render_protocol_reminder_cartooner(seat_id: str) -> list[str]:
    """Per-轮 reminder for cartooner-creative seats.

    Mirrors render_protocol_reminder_lines's per-role tailoring but with
    cartooner-harness primitives (dispatch_brief / spawn_lane / report_to_memory)
    instead of gstack's complete_handoff / dispatch_task / DELIVERY.md.
    """
    lines = ["## ⚠ Protocol Reminder (每轮先读)", ""]
    seat = (seat_id or "").strip().lower()

    if seat == "memory":
        lines.extend([
            "1. **Dispatch**: choose `dispatch_brief.py` (1 deliverable) vs `spawn_lane.py` (N candidates)",
            "2. **Closure**: read `PROJECT_INDEX.briefs[<id>].state` or `lanes[<id>].state` before next step",
            "3. **Pick**: aesthetic decisions ALWAYS escalate via `pick_winner.py --strategy manual` + AskUserQuestion",
            "4. **Vision Steward**: never produce creative content; never view asset content (no-image-policy)",
            "5. **User-direct**: receiving `report_to_memory --event user_direct_request` auto-flips to manual",
            "6. **Don't**: raw `tmux send-keys`; lateral writer→builder dispatch (always memory-routed)",
        ])
    elif seat == "writer":
        lines.extend([
            "1. **Receive**: read `briefs/<id>.toml` (frontmatter + body) or `lanes/<id>.toml`",
            "2. **Close brief**: `deliver_brief.py --actor writer --output-path <path>` (UTF-8, ≤ 5MB)",
            "3. **Close lane**: `deposit_asset.py --asset-type text --actor writer` × N (final adds `--all-candidates-deposited`)",
            "4. **Boundary**: only narrative_outline.md / lyrics / copy / 文案; no shot_list, no model prompts, no asset viewing",
            "5. **User-direct**: `report_to_memory --event user_direct_request` BEFORE acting",
            "6. **Don't**: dispatch other seats directly (memory routes everything)",
        ])
    elif seat in ("builder-image", "builder-av"):
        modal = "image (nb / gpt-image-2)" if seat == "builder-image" else "video / audio (Seedance / MiniMax)"
        lines.extend([
            "1. **Receive**: brief (single deliverable) or lane (N candidates)",
            f"2. **Generate**: produce {modal} via cartooner-* skills",
            "3. **Close**: `deposit_asset.py` per asset (model_metadata + file_metadata only — never self-eval)",
            "4. **Vision input**: ONLY via `spawn_subagent.py` (root_cause needs user_feedback; reference_learning needs URL)",
            "5. **User-direct**: `report_to_memory --event user_direct_request` BEFORE acting",
            "6. **Don't**: view assets in main thread; dispatch other seats; deposit out-of-modal asset_type",
        ])
    elif seat == "patrol":
        lines.extend([
            "1. **Read-only**: never dispatch, never deposit, never `pick_winner`",
            "2. **Audits**: `patrol_pipeline_sla.py --check {sla|integrity|authorization|all}`; exit 2 = anomalies",
            "3. **Findings**: emit `escalate_to_producer.py --trigger sla_breach` only when auto mode + threshold breached",
            "4. **User-direct**: query OK; mutate returns clear error per user-direct-contract.md",
            "5. **Don't**: any state mutation; any creative output; any vision input",
        ])
    else:
        lines.extend([
            "1. **Receive work** via lane / brief; do NOT pull from a non-cartooner queue",
            "2. **Close** via `deposit_asset.py` or `deliver_brief.py`; never silent",
            "3. **User-direct** must `report_to_memory --event user_direct_request` BEFORE acting",
            "4. **Don't**: raw `tmux send-keys`; lateral seat dispatch (memory-routed only)",
        ])
    lines.append("")
    return lines


def _render_communication_protocol_cartooner(
    engineer: Any,
    project_name: str,
    send_script: str,
) -> list[str]:
    """Communication protocol lines for clawseat-creative seats.

    Mirrors gstack's render structure but with cartooner-harness vocab:
    lane / brief / deposit / pick / iterate, hub-and-spoke through memory,
    state lives in ~/.cartooner/projects/<id>/, never raw tmux send-keys.
    """
    role = (engineer.role or "").strip().lower()
    seat_id = (getattr(engineer, "engineer_id", "") or "").strip().lower()
    is_memory = seat_id == "memory" or role.startswith("project-memory")
    is_writer = seat_id == "writer" or role == "screenwriter"
    is_builder_image = seat_id == "builder-image"
    is_builder_av = seat_id == "builder-av"
    is_patrol = seat_id == "patrol" or role in ("patrol", "qa")

    lines = [
        "## Communication Protocol (cartooner-creative)",
        "",
        "- spec: `core/skills/cartooner-harness/references/communication-protocol.md`",
        f"- transport: `{send_script}` (ClawSeat-level, shared with engineering)",
        "- treat raw `tmux send-keys` as a protocol violation — `dispatch_brief.py` "
        "and `spawn_lane.py` invoke send-and-verify internally",
        "- source of truth: `~/.cartooner/projects/<id>/PROJECT_INDEX.json` "
        "+ `lanes/` + `briefs/` + `tournaments/` + `iterations/` + `escalations/`",
        "- `~/.cartooner/_handoff/` is REMOVED; ignore any legacy files there",
        f"- multi-project mode: send-and-verify calls always need `--project {project_name}`",
    ]

    if is_memory:
        lines += [
            "",
            "## Dispatch (memory → executor seat) — choice rule",
            "- ONE authoritative deliverable expected → `dispatch_brief.py "
            "--target <writer|builder-image|builder-av> --intent <intent>`",
            "- N parallel candidates expected → `spawn_lane.py --seat <writer|"
            "builder-image|builder-av> --count N --shot-id <id>`",
            "- writer accepts both: brief for canonical narrative_outline.md / "
            "shot_list copy revisions; lane for multi-candidate hooks / lyric drafts",
            "",
            "## Closure (memory reads back)",
            "- briefs: `PROJECT_INDEX.briefs[<id>].state == \"delivered\"` "
            "+ `result.output_path`; receiver invoked `deliver_brief.py`",
            "- lanes: `PROJECT_INDEX.lanes[<id>].state == \"deposited\"` then "
            "`pick_winner.py --strategy manual` blocking on `AskUserQuestion`",
            "",
            "## Vision Steward discipline",
            "- you NEVER produce creative content; dispatch writer / builder-* "
            "via the primitives above",
            "- aesthetic decisions ALWAYS escalate to user (the Producer); "
            "default `pick_strategy = escalate-always`",
            "- auto-pick only allowed under `pick_strategy = model-metadata-rank` "
            "AND the model API provides a numeric `aesthetic_score`",
            "- never view asset content (no-image-policy hard rule)",
        ]
    elif is_writer:
        lines += [
            "",
            "## Receiving work",
            "- brief: `~/.cartooner/projects/<id>/briefs/<id>.toml` with "
            "frontmatter (target=writer) + markdown body",
            "- lane: `~/.cartooner/projects/<id>/lanes/<id>.toml` with "
            "seat=writer, count=N text candidates expected",
            "",
            "## Closing work",
            "- single deliverable: `deliver_brief.py --brief-id <id> --actor writer "
            "--output-path <path>` (UTF-8, ≤ 5MB)",
            "- N candidates: `deposit_asset.py --asset-type text --actor writer` "
            "× N (last call adds `--all-candidates-deposited`)",
            "",
            "## Forbidden",
            "- no direct dispatch to builder-* (memory routes everything; "
            "violation breaks Vision Steward SSOT)",
            "- no shot decisions / model prompts / camera vocabulary "
            "(builder-av's domain)",
            "- no asset viewing (no-image-policy)",
        ]
    elif is_builder_image or is_builder_av:
        modal = "image" if is_builder_image else "video / audio"
        skills = ("nb / gpt-image-2 / storyboard / design"
                  if is_builder_image else
                  "Seedance / shot_list authoring / MiniMax music / TTS")
        lines += [
            "",
            "## Receiving work",
            "- brief: single deliverable (revise shot_list / character_dna / reference_learning report)",
            "- lane: N parallel candidate generations via skill stack ({})".format(skills),
            "",
            "## Closing work",
            "- brief deliverable: `deliver_brief.py --brief-id <id> --actor "
            f"{seat_id} --output-path <path>` or `--fail --reason ...`",
            "- lane candidates: `deposit_asset.py` per asset (model_metadata + "
            "file_metadata only; never self-eval), final call adds "
            "`--all-candidates-deposited`",
            "",
            "## Vision input boundary (no-image-policy)",
            "- main thread: NEVER view candidate / reference content",
            "- only sanctioned vision path: `spawn_subagent.py` (root_cause / "
            "reference_learning), text-only report ≤ 1MB UTF-8 returned to main thread",
            "",
            "## Forbidden",
            "- no direct dispatch to other executor seats (memory routes everything)",
            "- no narrative authoring (writer's domain)",
            f"- no {('audio / video' if is_builder_image else 'image')} deposits "
            f"(asset_type strictly = {modal})",
        ]
    elif is_patrol:
        lines += [
            "",
            "## Read-only Asset Guardian",
            "- `patrol_pipeline_sla.py --check {sla|integrity|authorization|all}`",
            "- exit 2 on anomalies; emit findings to memory via "
            "`escalate_to_producer.py --trigger sla_breach` (only when "
            "automation_mode=auto and threshold breached)",
            "",
            "## Forbidden",
            "- never dispatch (no `dispatch_brief.py` / `spawn_lane.py` calls)",
            "- never deposit any asset",
            "- never `pick_winner.py` (no decision authority)",
            "- never user-direct *mutate* (user-direct query OK; user-direct "
            "mutate returns a clear error per user-direct-contract.md)",
        ]

    lines += [
        "",
        "## User-direct override (Producer-centric)",
        "- any seat receiving user-direct calls `report_to_memory.py "
        "--event user_direct_request` fail-closed BEFORE acting",
        "- auto mode auto-flips to manual on `user_direct_received`",
        "- self-dispatch after user-direct: pass "
        "`--triggered-by user_direct --actor <self>` to `dispatch_brief.py` / "
        "`spawn_lane.py`; audit shows the user-direct provenance throughout",
    ]
    return lines


def render_dispatch_playbook_lines(session: Any, project: Any, engineer: Any) -> list[str]:
    profile_path = HARNESS_PROFILE_ROOT / f"{project.name}.toml"
    from resolve import dynamic_profile_path as _dpp
    dynamic_profile_path = _dpp(project.name)
    if profile_path.exists():
        profile_ref = str(profile_path)
    elif dynamic_profile_path.exists():
        profile_ref = str(dynamic_profile_path)
    else:
        profile_ref = "<profile-path>"
    root = str(HARNESS_SCRIPTS_ROOT)
    lines: list[str] = []
    planner_seat = (
        preferred_seat_for_role(
            project,
            "planner",
            project_engineers=getattr(session, "project_engineers", None),
            engineer_order=getattr(session, "engineer_order", None),
        )
        or "planner"
    )

    if engineer.role == "frontstage-supervisor":
        lines = [
            "## Dispatch Playbook",
            "",
            "Use these canonical commands instead of hand-writing task-chain state:",
            "",
            f"Dispatch work to `{planner_seat}`:",
            "```bash",
            f"python3 {root}/dispatch_task.py \\",
            f"  --profile {profile_ref} \\",
            "  --source koder \\",
            f"  --target {planner_seat} \\",
            "  --task-id <TASK_ID> \\",
            "  --title '<TITLE>' \\",
            "  --objective '<OBJECTIVE>' \\",
            "  --test-policy UPDATE \\",
            "  --reply-to koder",
            "```",
            "",
            f"Send a one-off unblock/reminder to `{planner_seat}`:",
            "```bash",
            f"python3 {root}/notify_seat.py \\",
            f"  --profile {profile_ref} \\",
            "  --source koder \\",
            f"  --target {planner_seat} \\",
            "  --task-id <TASK_ID> \\",
            "  --kind unblock \\",
            "  --reply-to koder \\",
            "  --message '<MESSAGE>'",
            "```",
        ]
    elif engineer.role == "planner-dispatcher":
        _materialize_planner_tools(session, project, engineer, profile_ref, root, planner_seat)
        lines = [
            "## Dispatch Playbook",
            "",
            "**Always pass `--intent`** — see `TOOLS/intent.md` for target→intent map.",
            "See `TOOLS/handoff.md` for ACK, closeout, and seat-needed commands.",
            "See `TOOLS/seat-lifecycle.md` for seat lifecycle rules.",
            "",
            "```bash",
            f"python3 {root}/dispatch_task.py \\",
            f"  --profile {profile_ref} \\",
            f"  --source {planner_seat} \\",
            "  --target <TARGET_SEAT> \\",
            "  --task-id <TASK_ID> \\",
            "  --title '<TITLE>' \\",
            "  --objective '<OBJECTIVE>' \\",
            "  --test-policy UPDATE \\",
            "  --intent <INTENT_KEY> \\",
            f"  --reply-to {planner_seat}",
            "```",
        ]
    elif engineer.role in _SPECIALIST_ROLES:
        _materialize_specialist_protocol(session)
        verdict_flag = "  --verdict APPROVED \\\n" if engineer.role == "reviewer" else ""
        cmd = (
            f"python3 {root}/complete_handoff.py "
            f"--profile {profile_ref} "
            f"--source {session.engineer_id} "
            f"--target {planner_seat} "
            f"--task-id <ID> --title '<T>' --summary '<S>'"
            + (f" --verdict APPROVED" if engineer.role == "reviewer" else "")
        )
        lines = ["## Dispatch", "```bash", cmd, "```"]
    return lines


def _materialize_planner_tools(
    session: Any,
    project: Any,
    engineer: Any,
    profile_ref: str,
    root: str,
    planner_seat: str,
) -> None:
    workspace = Path(getattr(session, "workspace", "") or "")
    if not workspace.is_dir():
        return
    tools_dir = workspace / "TOOLS"
    try:
        tools_dir.mkdir(exist_ok=True)
    except OSError:
        return
    files = {
        "intent.md": render_tools_intent(session, project, engineer),
        "handoff.md": render_tools_handoff(session, project, engineer, profile_ref, root, planner_seat),
        "feishu.md": render_tools_feishu(session, project, engineer),
        "seat-lifecycle.md": render_tools_seat_lifecycle(session, project, engineer, profile_ref, root, planner_seat),
        "memory.md": render_tools_memory_learning(),
    }
    for name, content in files.items():
        try:
            (tools_dir / name).write_text(content, encoding="utf-8")
        except OSError:
            pass


def _materialize_specialist_protocol(session: Any) -> None:
    workspace = Path(getattr(session, "workspace", "") or "")
    if not workspace.is_dir():
        return
    tools_dir = workspace / "TOOLS"
    try:
        tools_dir.mkdir(exist_ok=True)
    except OSError:
        return
    target = tools_dir / "protocol.md"
    source = TOOLS_SHARED_ROOT / "protocol.md"
    if target.is_symlink() or target.exists():
        return
    try:
        target.symlink_to(source)
    except OSError:
        try:
            if source.is_file():
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    # Also materialize memory.md for specialists (memory learning channel docs)
    _materialize_shared_tools(workspace)


def _materialize_shared_tools(workspace: Path) -> None:
    """Copy shared TOOLS/ files (workspace_tools in template.toml) into the workspace."""
    tools_dir = workspace / "TOOLS"
    try:
        tools_dir.mkdir(exist_ok=True)
    except OSError:
        return
    memory_target = tools_dir / "memory.md"
    if not memory_target.exists():
        try:
            memory_target.write_text(render_tools_memory_learning(), encoding="utf-8")
        except OSError:
            pass


def render_tools_memory_learning() -> str:
    """Read the shared TOOLS/memory.md learning-channel template."""
    shared = TOOLS_SHARED_ROOT / "memory.md"
    if shared.is_file():
        return shared.read_text(encoding="utf-8")
    return "# Memory Learning Channel\n\nSee `core/templates/shared/TOOLS/memory.md`.\n"


def render_tools_intent(session: Any, project: Any, engineer: Any) -> str:
    shared = TOOLS_SHARED_ROOT / "intent.md"
    if shared.is_file():
        return shared.read_text(encoding="utf-8")
    return "# Dispatch Intent Map\n\nSee `dispatch_task.py --help` for available `--intent` keys.\n"


def render_tools_handoff(
    session: Any, project: Any, engineer: Any, profile_ref: str, root: str, planner_seat: str
) -> str:
    shared = TOOLS_SHARED_ROOT / "handoff.md"
    template = shared.read_text(encoding="utf-8") if shared.is_file() else ""
    template = template.replace("<HARNESS_SCRIPTS>", root).replace("<PROFILE>", profile_ref)
    return template


def render_tools_feishu(session: Any, project: Any, engineer: Any) -> str:
    shared = TOOLS_SHARED_ROOT / "feishu.md"
    if shared.is_file():
        root = str(HARNESS_SCRIPTS_ROOT)
        return shared.read_text(encoding="utf-8").replace("<HARNESS_SCRIPTS>", root)
    return "# Feishu Protocol\n\nSee `send_delegation_report.py --help`.\n"


def render_tools_seat_lifecycle(
    session: Any, project: Any, engineer: Any, profile_ref: str, root: str, planner_seat: str
) -> str:
    shared = TOOLS_SHARED_ROOT / "seat-lifecycle.md"
    template = shared.read_text(encoding="utf-8") if shared.is_file() else ""
    template = template.replace("<HARNESS_SCRIPTS>", root).replace("<PROFILE>", profile_ref)
    from resolve import dynamic_profile_path as _dpp  # noqa: PLC0415
    agent_admin = str(
        Path(
            os.environ.get("CLAWSEAT_ROOT", str(Path(__file__).resolve().parents[2]))
        ) / "core" / "scripts" / "agent_admin.py"
    )
    template = template.replace("<AGENT_ADMIN>", agent_admin)
    return template


def workspace_contract_payload(
    session: Any,
    project: Any,
    engineer: Any,
    *,
    project_engineers: dict[str, Any] | None = None,
    engineer_order: list[str] | None = None,
) -> dict[str, object]:
    merged_engineer = (project_engineers or {}).get(session.engineer_id)
    role_details = list(getattr(engineer, "role_details", []) or [])
    if not role_details and merged_engineer is not None:
        role_details = list(getattr(merged_engineer, "role_details", []) or [])
    read_first_items = [
        line.split("`")[1]
        for line in render_read_first_lines(session, project, engineer)
        if line and line[0].isdigit() and "`" in line
    ]
    resolved_tasks_root = _resolve_tasks_root(project)
    source_paths: list[str] = [
        f"{resolved_tasks_root}/{session.engineer_id}/TODO.md",
        f"{resolved_tasks_root}/PROJECT.md",
        f"{resolved_tasks_root}/TASKS.md",
    ]
    if engineer.role in {"frontstage-supervisor", "planner-dispatcher"}:
        source_paths.append(f"{resolved_tasks_root}/STATUS.md")
    include_team_ownership = (
        engineer.role in {"memory", "project-memory", "memory-oracle", "planner", "planner-dispatcher"}
        and _is_multi_team_project(project.name)
    )
    if include_team_ownership:
        source_paths.append(f"{resolved_tasks_root}/TEAM_OWNERSHIP.md")
    if engineer.role in {"memory", "project-memory", "memory-oracle", "planner", "planner-dispatcher"}:
        if f"{resolved_tasks_root}/STATUS.md" not in source_paths:
            source_paths.append(f"{resolved_tasks_root}/STATUS.md")
    if engineer.role == "frontstage-supervisor":
        candidate = Path(project.repo_root) / "KODER.md"
        if candidate.exists():
            source_paths.append(str(candidate))
        roster = Path(resolved_tasks_root) / "FE-003-SPECIALIST-ROSTER.md"
        if roster.exists():
            source_paths.append(str(roster))
    for path in read_first_items:
        if path not in source_paths:
            source_paths.append(path)
    project_seat_map: list[str] = []
    for line in render_project_seat_map_lines(
        session,
        project,
        engineer,
        project_engineers=project_engineers,
        engineer_order=engineer_order,
    ):
        if line.startswith("- "):
            project_seat_map.append(line[2:])
            continue
        stripped = line.strip()
        if stripped.startswith(("Seats:", "Boundary:")):
            project_seat_map.append(stripped)
    review_latest_integration = [
        "Each ClawSeat project owns one project-local validation worktree for review/latest; never share it across projects.",
        "Builders never merge review/latest or main.",
        "Planner delivers branch/commit evidence, tests, and blockers; it does not merge review/latest.",
        "Memory, or a user-authorized warden during patrol, integrates accepted planner deliveries into that project's own review/latest worktree.",
        "Memory may merge from that project review/latest worktree to main only after explicit user confirmation.",
        "Memory closeout records user confirmation, review/latest hash, and main merge hash or blocker.",
        "Memory owns desktop launch scripts so user review opens this project's review/latest worktree, not main, a shared global worktree, or a stale tmp worktree.",
        "On conflict: stop and report; no force-push and no main changes.",
    ]
    return {
        "engineer_id": session.engineer_id,
        "project": project.name,
        "tool": session.tool,
        "workspace": session.workspace,
        "role": engineer.role,
        "role_details": role_details,
        "aliases": list(getattr(engineer, "aliases", []) or []),
        "capabilities": [line[2:] for line in render_authority_lines(engineer) if line.startswith("- ")],
        "read_first": read_first_items,
        "project_seat_map": project_seat_map,
        "seat_boundary": [line[2:] for line in render_seat_boundary_lines(session, engineer) if line.startswith("- ")],
        "communication_protocol": [
            line[2:]
            for line in render_communication_protocol_lines(
                engineer,
                project.name,
                template_name=str(getattr(project, "template_name", "") or ""),
                seat_id=str(getattr(session, "engineer_id", "") or ""),
            )
            if line.startswith("- ")
        ],
        "review_latest_integration": review_latest_integration,
        "source_paths": source_paths,
    }


def workspace_contract_fingerprint(payload: dict[str, object]) -> str:
    canonical = repr(sorted(payload.items())).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def render_workspace_contract_text(
    session: Any,
    project: Any,
    engineer: Any,
    *,
    project_engineers: dict[str, Any] | None = None,
    engineer_order: list[str] | None = None,
) -> str:
    payload = workspace_contract_payload(
        session,
        project,
        engineer,
        project_engineers=project_engineers,
        engineer_order=engineer_order,
    )
    fingerprint = workspace_contract_fingerprint(payload)
    # Resolve profile path for this project
    from resolve import dynamic_profile_path as _dpp
    _profile_path = str(_dpp(project.name))

    lines = [
        'version = 1',
        f"engineer_id = {q(session.engineer_id)}",
        f"project = {q(project.name)}",
        f"profile = {q(_profile_path)}",
        f"tool = {q(session.tool)}",
        f"workspace = {q(session.workspace)}",
        f"role = {q(engineer.role)}",
        f"fingerprint = {q(fingerprint)}",
        f"aliases = {q_array([str(item) for item in payload['aliases']])}",
        f"role_details = {q_array([str(item) for item in payload['role_details']])}",
        f"capabilities = {q_array([str(item) for item in payload['capabilities']])}",
        f"read_first = {q_array([str(item) for item in payload['read_first']])}",
        f"project_seat_map = {q_array([str(item) for item in payload['project_seat_map']])}",
        f"seat_boundary = {q_array([str(item) for item in payload['seat_boundary']])}",
        f"communication_protocol = {q_array([str(item) for item in payload['communication_protocol']])}",
        f"review_latest_integration = {q_array([str(item) for item in payload['review_latest_integration']])}",
        f"source_paths = {q_array([str(item) for item in payload['source_paths']])}",
        "",
    ]
    return "\n".join(lines)


def _expand_skill_path(raw: str) -> str:
    """Expand portable placeholders in a skill path.

    - ``{CLAWSEAT_ROOT}`` is replaced with the resolved ``REPO_ROOT``.
    - ``~`` is expanded via :func:`os.path.expanduser`.
    """
    expanded = raw.replace("{CLAWSEAT_ROOT}", str(REPO_ROOT))
    expanded = os.path.expanduser(expanded)
    return expanded


def render_loaded_skills_lines(engineer: Any, engineer_id: str) -> list[str]:
    skills = list(getattr(engineer, "skills", []) or [])
    if not skills:
        return []
    if engineer.role in _SPECIALIST_ROLES or engineer.role == "planner-dispatcher":
        expanded_paths = [f"`{_expand_skill_path(s)}`" for s in skills]
        return ["**Skills:** " + ", ".join(expanded_paths)]
    header = "## Loaded Skills"
    lines = [header, "", f"Use these as the default skill set for `{engineer_id}`:", ""]
    for raw_skill in skills:
        expanded = _expand_skill_path(raw_skill)
        if Path(expanded).exists():
            lines.append(f"- `{expanded}`")
        else:
            lines.append(f"- `{expanded}` (WARNING: path not found on this machine)")
    return lines


def render_optional_skill_when_to_use(description: str) -> str:
    first_line = description.strip().splitlines()[0] if description.strip() else ""
    return first_line.strip()


def render_optional_skills_catalog(optional_skills: list[dict[str, object]]) -> str:
    lines = [
        "# Optional Skill Catalog",
        "",
        "These skills are available to this project but are not preloaded for every seat.",
        "Activate only when your TODO.md explicitly references them.",
        "",
    ]
    for skill in optional_skills:
        name = str(skill.get("name", "")).strip()
        path = str(skill.get("path", "")).strip()
        description = str(skill.get("description", "")).strip()
        when_to_use = render_optional_skill_when_to_use(description)
        seat_affinity = [str(item).strip() for item in skill.get("seat_affinity", []) if str(item).strip()]
        lines.append(f"## `{name}`")
        lines.append("")
        if path:
            lines.append(f"- Path: `{path}`")
        if seat_affinity:
            lines.append(f"- Seat affinity: {', '.join(f'`{item}`' for item in seat_affinity)}")
        if when_to_use:
            lines.append(f"- Use when: {when_to_use}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# C14 — Profile regeneration preserve logic
# ---------------------------------------------------------------------------

PRESERVE_FIELDS: tuple[str, ...] = (
    "heartbeat_transport",
    "heartbeat_owner",
    "seats",
    "heartbeat_seats",
    "default_start_seats",
    "materialized_seats",
    "runtime_seats",
    "bootstrap_seats",
    "active_loop_owner",
    "default_notify_target",
    "feishu_group_id",
    "seat_roles",
    "seat_overrides",
    "dynamic_roster",
    "patrol",
    "observability",
)


def _toml_val(v: Any) -> str:
    """Serialize a single TOML value (scalar or list of scalars)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, list):
        items = ", ".join(_toml_val(item) for item in v)
        return f"[{items}]"
    raise ValueError(f"unsupported TOML value type {type(v).__name__} for value {v!r}")


def _serialize_profile_toml(data: dict[str, Any]) -> str:
    """Serialize a profile dict to TOML text.

    Handles: top-level scalars/lists, nested tables ([section]),
    and doubly-nested tables ([section.subsection]).
    """
    lines: list[str] = []

    # Top-level scalars and lists first (in insertion order)
    for key, val in data.items():
        if not isinstance(val, dict):
            lines.append(f"{key} = {_toml_val(val)}")

    # Top-level tables
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        lines.append("")
        lines.append(f"[{key}]")
        # Scalars / lists within this table
        for subkey, subval in val.items():
            if not isinstance(subval, dict):
                lines.append(f"{subkey} = {_toml_val(subval)}")
        # Sub-tables (e.g. seat_overrides.planner)
        for subkey, subval in val.items():
            if isinstance(subval, dict):
                lines.append("")
                lines.append(f"[{key}.{subkey}]")
                for k2, v2 in subval.items():
                    lines.append(f"{k2} = {_toml_val(v2)}")

    return "\n".join(lines) + "\n"


def render_profile_preserving_operator_edits(
    target_path: Path,
    fresh_payload: dict[str, Any],
    *,
    preserve_fields: tuple[str, ...] = PRESERVE_FIELDS,
) -> dict[str, Any]:
    """Merge fresh_payload with operator-set values from target_path.

    If target_path exists, read it and for every key in preserve_fields
    that's present in the existing file, use the existing value.
    Fields not in preserve_fields get fresh_payload's value.
    Extra fields in the existing file (unknown to the template) are also
    carried forward so future schema extensions don't silently disappear.

    Emits one stderr warning line per preserved field where the fresh
    payload differs from the existing value.
    """
    merged: dict[str, Any] = dict(fresh_payload)

    if not target_path.exists():
        return merged

    try:
        existing_text = target_path.read_text(encoding="utf-8")
        existing = _toml_loads(existing_text)
    except Exception as exc:
        print(
            f"WARNING [C14]: could not parse existing profile {target_path}: {exc}; "
            "using fresh payload without preservation.",
            file=sys.stderr,
        )
        return merged

    # Preserve fields from allowlist (with warning on divergence)
    for field in preserve_fields:
        if field not in existing:
            continue
        existing_val = existing[field]
        fresh_val = fresh_payload.get(field)
        if fresh_val is not None and fresh_val != existing_val:
            print(
                f"WARNING [C14]: preserving operator-set '{field}' = {existing_val!r} "
                f"(fresh payload had {fresh_val!r})",
                file=sys.stderr,
            )
        merged[field] = existing_val

    # Carry forward extra/unknown fields not in the fresh payload at all
    for field, val in existing.items():
        if field not in merged:
            merged[field] = val

    return merged
