#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from agent_admin_heartbeat import (
    HeartbeatHandlers,
    HeartbeatHooks,
)
from agent_admin_commands import CommandHandlers, CommandHooks
from agent_admin_layered import (
    cmd_machine_memory_show,
    cmd_project_koder_bind,
    cmd_project_seat_list,
    cmd_project_validate,
)
from agent_admin_config import (
    AGENTCTL_SH,
    AGENTS_ROOT,
    CODEX_API_PROVIDER_CONFIGS,
    CURRENT_PROJECT_PATH,
    DEFAULT_PATH,
    DEFAULT_TOOL_ARGS,
    ENGINEERS_ROOT,
    HARNESS_PROFILE_ROOT,
    HOME,
    LEGACY_ASSIGNMENTS_PATH,
    LEGACY_CONFIG_ROOT,
    LEGACY_ENGINEERS,
    LEGACY_GEMINI_SANDBOXES,
    LEGACY_IDENTITIES_PATH,
    LEGACY_IDENTITIES_ROOT,
    LEGACY_ROOT,
    LEGACY_SECRETS_ROOT,
    PROJECTS_ROOT,
    PROJECT_DEFAULTS,
    REPO_ROOT,
    RUNTIME_ROOT,
    SEND_AND_VERIFY_SH,
    SECRETS_ROOT,
    SESSIONS_ROOT,
    STATE_ROOT,
    TEMPLATES_ROOT,
    TOOL_BINARIES,
    WORKSPACES_ROOT,
)
_CORE_LIB = REPO_ROOT / "core" / "lib"
if str(_CORE_LIB) not in sys.path:
    sys.path.insert(0, str(_CORE_LIB))
from utils import load_toml, q, q_array  # noqa: E402
from agent_admin_crud import CrudHandlers, CrudHooks
from agent_admin_info import InfoHandlers, InfoHooks
# agent_admin_legacy is imported lazily inside migrate_legacy / migrate_session_model
# (audit H8): a top-level import forced the runtime path to pull in migration
# code on every invocation, so any bug there leaked into normal operation.
from agent_admin_parser import ParserHooks, build_parser as build_agent_admin_parser
from agent_admin_provider import ProviderHandlers
from agent_admin_runtime import (
    common_env,
    detect_macos_system_proxies,
    ensure_empty_env_file,
    ensure_secret_permissions,
    identity_name,
    parse_env_file,
    runtime_dir_for_identity,
    secret_file_for,
    session_name_for,
    write_codex_api_config,
    write_env_file,
)
from agent_admin_resolve import ResolveHandlers, ResolveHooks
from agent_admin_session import SessionHooks, SessionService, SessionStartError
from agent_admin_store import StoreHandlers, StoreHooks
from agent_admin_switch import SwitchHandlers, SwitchHooks
from agent_admin_task import TaskCommandError
from agent_admin_task import auto_supersede as task_auto_supersede
from agent_admin_task import create_task as task_create
from agent_admin_task import list_pending as task_list_pending
from agent_admin_task import update_status as task_update_status

# v3 brief / queue subcommand wiring (spec §4.2 §4.3)
from agent_admin_brief import cmd_queue as brief_queue
from agent_admin_brief import cmd_list as brief_list
from agent_admin_brief import cmd_claim as brief_claim
from agent_admin_brief import cmd_reset as brief_reset
from agent_admin_brief import cmd_requeue as brief_requeue
from agent_admin_brief import cmd_start as brief_start
from agent_admin_brief import cmd_show as brief_show
from agent_admin_brief import cmd_done as brief_done
from agent_admin_brief import cmd_planner_status as brief_planner_status
from liveness_gate import query_seat_liveness

# v3 acceptance executor (Phase 2, spec §4.7)
_CORE_LIB_PATH = REPO_ROOT / "core" / "lib"
if str(_CORE_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_CORE_LIB_PATH))
from acceptance_executor import (  # noqa: E402
    AcceptanceError as _AcceptanceError,
    aggregate_verdict as _aggregate_verdict,
    run_acceptance as _run_acceptance,
)
from agent_admin_template import TemplateHandlers, TemplateHooks
from agent_admin_tui import TuiHooks, run_tui_app
from agent_admin_window import (
    AgentAdminWindowError,
    build_monitor_layout,
    display_name_for,
    open_dashboard_window,
    open_engineer_window,
    open_monitor_window,
    open_project_tabs_window,
    tmux_has_session,
)
from agent_admin_workspace import (
    render_aliases_lines,
    render_authority_lines,
    render_communication_protocol_lines,
    render_dispatch_playbook_lines,
    render_harness_runtime_lines,
    render_loaded_skills_lines,
    render_optional_skills_catalog,
    render_project_seat_map_lines,
    render_protocol_reminder_lines,
    render_read_first_lines,
    render_role_details_lines,
    render_role_line,
    render_seat_boundary_lines,
    workspace_contract_fingerprint,
    workspace_contract_payload,
    render_workspace_contract_text,
)

@dataclass
class Engineer:
    engineer_id: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    role: str = ""
    role_details: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    human_facing: bool = False
    active_loop_owner: bool = False
    dispatch_authority: bool = False
    patrol_authority: bool = False
    unblock_authority: bool = False
    escalation_authority: bool = False
    remind_active_loop_owner: bool = False
    review_authority: bool = False
    design_authority: bool = False
    default_tool: str = ""
    default_auth_mode: str = ""
    default_provider: str = ""
    # Template rendering context (set by render_template_text, read by workspace renderers)
    _project_record: object = None
    _project_engineers: dict[str, object] = field(default_factory=dict)
    _engineer_order: list[str] = field(default_factory=list)


@dataclass
class SessionRecord:
    engineer_id: str
    project: str
    tool: str
    auth_mode: str
    provider: str
    identity: str
    workspace: str
    runtime_dir: str
    session: str
    bin_path: str
    monitor: bool = True
    legacy_sessions: list[str] = field(default_factory=list)
    launch_args: list[str] = field(default_factory=list)
    secret_file: str = ""
    wrapper: str = ""
    # Template context (set by agent_admin_crud during apply_template)
    _template_model: str = ""
    _template_effort: str = ""


@dataclass
class Project:
    name: str
    repo_root: str
    monitor_session: str
    engineers: list[str]
    monitor_engineers: list[str]
    template_name: str = ""
    declared_skills: list[str] = field(default_factory=list)
    seat_overrides: dict[str, dict[str, object]] | None = None
    window_mode: str = "project-monitor"
    monitor_max_panes: int = 4
    open_detail_windows: bool = False


class AgentAdminError(RuntimeError):
    pass


PROVIDER_HANDLERS = ProviderHandlers(AgentAdminError)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str, mode: int | None = None) -> None:
    ensure_dir(path.parent)
    path.write_text(content)
    if mode is not None:
        path.chmod(mode)


def normalize_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    if not normalized:
        raise AgentAdminError("Name cannot be empty")
    return normalized


def ensure_root_layout() -> None:
    for path in (
        AGENTS_ROOT,
        PROJECTS_ROOT,
        ENGINEERS_ROOT,
        SESSIONS_ROOT,
        WORKSPACES_ROOT,
        RUNTIME_ROOT,
        SECRETS_ROOT,
        LEGACY_ROOT,
        STATE_ROOT,
    ):
        ensure_dir(path)


def project_path(project: str) -> Path:
    return STORE_HANDLERS.project_path(project)


def engineer_path(engineer_id: str) -> Path:
    return STORE_HANDLERS.engineer_path(engineer_id)


def session_path(project: str, engineer_id: str) -> Path:
    return STORE_HANDLERS.session_path(project, engineer_id)


def load_project(name: str) -> Project:
    return STORE_HANDLERS.load_project(name)


def load_projects() -> dict[str, Project]:
    return STORE_HANDLERS.load_projects()


def get_current_project_name(projects: dict[str, Project] | None = None) -> str | None:
    return STORE_HANDLERS.get_current_project_name(projects)


def set_current_project(name: str) -> None:
    STORE_HANDLERS.set_current_project(name)


def load_project_or_current(name: str | None) -> Project:
    return STORE_HANDLERS.load_project_or_current(name)


def load_engineer(engineer_id: str) -> Engineer:
    return STORE_HANDLERS.load_engineer(engineer_id)


def load_engineers() -> dict[str, Engineer]:
    return STORE_HANDLERS.load_engineers()


def load_session(project: str, engineer_id: str) -> SessionRecord:
    return STORE_HANDLERS.load_session(project, engineer_id)


def load_sessions() -> dict[tuple[str, str], SessionRecord]:
    return STORE_HANDLERS.load_sessions()


def load_project_sessions(project: str) -> dict[str, SessionRecord]:
    return STORE_HANDLERS.load_project_sessions(project)


def load_template(name_or_path: str) -> dict:
    return STORE_HANDLERS.load_template(name_or_path)


def merge_template_local(template: dict, local: dict) -> dict:
    return STORE_HANDLERS.merge_template_local(template, local)


def write_project(project: Project) -> None:
    STORE_HANDLERS.write_project(project)


def project_template_context(project: Project) -> tuple[dict[str, Engineer], list[str], list[dict[str, object]]] | None:
    return STORE_HANDLERS.project_template_context(project)


def write_engineer(engineer: Engineer) -> None:
    STORE_HANDLERS.write_engineer(engineer)


def write_session(session: SessionRecord) -> None:
    STORE_HANDLERS.write_session(session)


def find_active_loop_owner(
    project: Project,
    *,
    project_engineers: dict[str, Engineer] | None = None,
    engineer_order: list[str] | None = None,
) -> str | None:
    engineers = project_engineers or load_engineers()
    ordered_engineer_ids = list(engineer_order or project.engineers or engineers.keys())
    for engineer_id in ordered_engineer_ids:
        engineer = engineers.get(engineer_id)
        if engineer and engineer.active_loop_owner:
            return engineer.engineer_id
    return None


def render_heartbeat_text(session: SessionRecord, project: Project, engineer: Engineer) -> str | None:
    return HEARTBEAT_HANDLERS.render_heartbeat_text(session, project, engineer)


def render_heartbeat_manifest_text(
    session: SessionRecord,
    project: Project,
    engineer: Engineer,
    *,
    project_engineers: dict[str, Engineer] | None = None,
    engineer_order: list[str] | None = None,
) -> str | None:
    return HEARTBEAT_HANDLERS.render_heartbeat_manifest_text(
        session,
        project,
        engineer,
        project_engineers=project_engineers,
        engineer_order=engineer_order,
    )


def heartbeat_manifest_path(session: SessionRecord) -> Path:
    return HEARTBEAT_HANDLERS.manifest_path(session)


def heartbeat_manifest_fingerprint(manifest: dict) -> str:
    return HEARTBEAT_HANDLERS.manifest_fingerprint(manifest)


def provision_session_heartbeat(
    session: SessionRecord,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[bool, str]:
    return HEARTBEAT_HANDLERS.provision_session_heartbeat(
        session,
        force=force,
        dry_run=dry_run,
    )


STORE_HANDLERS = StoreHandlers(
    StoreHooks(
        error_cls=AgentAdminError,
        project_cls=Project,
        engineer_cls=Engineer,
        session_record_cls=SessionRecord,
        projects_root=PROJECTS_ROOT,
        engineers_root=ENGINEERS_ROOT,
        sessions_root=SESSIONS_ROOT,
        workspaces_root=WORKSPACES_ROOT,
        current_project_path=CURRENT_PROJECT_PATH,
        templates_root=TEMPLATES_ROOT,
        repo_templates_root=REPO_ROOT / "templates",
        tool_binaries=TOOL_BINARIES,
        default_tool_args=DEFAULT_TOOL_ARGS,
        normalize_name=normalize_name,
        ensure_dir=ensure_dir,
        write_text=write_text,
        load_toml=load_toml,
        q=q,
        q_array=q_array,
        identity_name=identity_name,
        runtime_dir_for_identity=runtime_dir_for_identity,
        secret_file_for=secret_file_for,
        session_name_for=session_name_for,
    )
)


def render_template_text(
    tool: str,
    session: SessionRecord,
    project: Project,
    engineer_override: Engineer | None = None,
    project_engineers: dict[str, Engineer] | None = None,
    engineer_order: list[str] | None = None,
) -> dict[str, str]:
    return TEMPLATE_HANDLERS.render_template_text(
        tool,
        session,
        project,
        engineer_override=engineer_override,
        project_engineers=project_engineers,
        engineer_order=engineer_order,
    )


def apply_template(
    session: SessionRecord,
    project: Project,
    engineer_override: Engineer | None = None,
    optional_skills: list[dict[str, object]] | None = None,
    project_engineers: dict[str, Engineer] | None = None,
    engineer_order: list[str] | None = None,
) -> None:
    TEMPLATE_HANDLERS.apply_template(
        session,
        project,
        engineer_override=engineer_override,
        optional_skills=optional_skills,
        project_engineers=project_engineers,
        engineer_order=engineer_order,
    )


TEMPLATE_HANDLERS = TemplateHandlers(
    TemplateHooks(
        ensure_dir=ensure_dir,
        write_text=write_text,
        load_engineer=load_engineer,
        project_template_context=project_template_context,
        q=q,
        render_authority_lines=render_authority_lines,
        render_protocol_reminder_lines=render_protocol_reminder_lines,
        render_read_first_lines=render_read_first_lines,
        render_harness_runtime_lines=render_harness_runtime_lines,
        render_project_seat_map_lines=render_project_seat_map_lines,
        render_seat_boundary_lines=render_seat_boundary_lines,
        render_communication_protocol_lines=render_communication_protocol_lines,
        render_dispatch_playbook_lines=render_dispatch_playbook_lines,
        render_loaded_skills_lines=render_loaded_skills_lines,
        render_optional_skills_catalog=render_optional_skills_catalog,
        workspace_contract_payload=workspace_contract_payload,
        workspace_contract_fingerprint=workspace_contract_fingerprint,
        render_workspace_contract_text=render_workspace_contract_text,
        render_role_line=render_role_line,
        render_role_details_lines=render_role_details_lines,
        render_aliases_lines=render_aliases_lines,
        render_heartbeat_text=render_heartbeat_text,
        render_heartbeat_manifest_text=render_heartbeat_manifest_text,
    )
)


def build_runtime(session: SessionRecord) -> tuple[str, dict[str, str]]:
    return RESOLVE_HANDLERS.build_runtime(session)


def default_launch_args(session: SessionRecord) -> list[str]:
    return RESOLVE_HANDLERS.default_launch_args(session)


def resolve_engineer(name: str, engineers: dict[str, Engineer] | None = None) -> Engineer:
    return RESOLVE_HANDLERS.resolve_engineer(name, engineers)


def resolve_engineer_session(
    engineer_name: str,
    project_name: str | None = None,
    sessions: dict[tuple[str, str], SessionRecord] | None = None,
    engineers: dict[str, Engineer] | None = None,
) -> SessionRecord:
    return RESOLVE_HANDLERS.resolve_engineer_session(
        engineer_name,
        project_name=project_name,
        sessions=sessions,
        engineers=engineers,
    )


def resolve_session(
    name: str,
    project_name: str | None = None,
    *,
    prefer_current_project: bool = True,
) -> str:
    return RESOLVE_HANDLERS.resolve_session(
        name,
        project_name=project_name,
        prefer_current_project=prefer_current_project,
    )


def build_engineer_exec(session: SessionRecord) -> list[str]:
    return SESSION_SERVICE.build_engineer_exec(session)


def session_start_engineer(session: SessionRecord, reset: bool = False) -> None:
    SESSION_SERVICE.start_engineer(session, reset=reset)


def session_stop_engineer(session: SessionRecord) -> None:
    SESSION_SERVICE.stop_engineer(session)


def session_status(session: SessionRecord) -> str:
    return SESSION_SERVICE.status(session)


def session_start_project(project: Project, ensure_monitor: bool = True, reset: bool = False) -> None:
    SESSION_SERVICE.start_project(project, ensure_monitor=ensure_monitor, reset=reset)


def project_engineer_context(project: Project) -> tuple[dict[str, Engineer], list[str]]:
    return SESSION_SERVICE.project_engineer_context(project)


def project_autostart_engineer_ids(project: Project, *, ensure_monitor: bool = False) -> list[str]:
    return SESSION_SERVICE.project_autostart_engineer_ids(project, ensure_monitor=ensure_monitor)


def seat_requires_launch_confirmation(project: Project, engineer_id: str) -> bool:
    return SESSION_SERVICE.seat_requires_launch_confirmation(project, engineer_id)


def display_label(engineer: Engineer | None, fallback: str) -> str:
    return RESOLVE_HANDLERS.display_label(engineer, fallback)


RESOLVE_HANDLERS = ResolveHandlers(
    ResolveHooks(
        error_cls=AgentAdminError,
        default_tool_args=DEFAULT_TOOL_ARGS,
        codex_api_provider_configs=CODEX_API_PROVIDER_CONFIGS,
        common_env=common_env,
        ensure_dir=ensure_dir,
        parse_env_file=parse_env_file,
        write_codex_api_config=write_codex_api_config,
        write_text=write_text,
        load_project=load_project,
        load_projects=load_projects,
        load_engineers=load_engineers,
        load_sessions=load_sessions,
        get_current_project_name=get_current_project_name,
        display_name_for=display_name_for,
    )
)


def create_engineer_profile(
    engineer_id: str,
    tool: str,
    auth_mode: str,
    provider: str,
    role: str = "",
    display_name: str = "",
    role_details: list[str] | None = None,
    skills: list[str] | None = None,
    aliases: list[str] | None = None,
    human_facing: bool = False,
    active_loop_owner: bool = False,
    dispatch_authority: bool = False,
    patrol_authority: bool = False,
    unblock_authority: bool = False,
    escalation_authority: bool = False,
    remind_active_loop_owner: bool = False,
    review_authority: bool = False,
    design_authority: bool = False,
) -> Engineer:
    return STORE_HANDLERS.create_engineer_profile(
        engineer_id=engineer_id,
        tool=tool,
        auth_mode=auth_mode,
        provider=provider,
        role=role,
        display_name=display_name,
        role_details=role_details,
        skills=skills,
        aliases=aliases,
        human_facing=human_facing,
        active_loop_owner=active_loop_owner,
        dispatch_authority=dispatch_authority,
        patrol_authority=patrol_authority,
        unblock_authority=unblock_authority,
        escalation_authority=escalation_authority,
        remind_active_loop_owner=remind_active_loop_owner,
        review_authority=review_authority,
        design_authority=design_authority,
    )


def merge_engineer_profile_with_template(profile: Engineer, engineer_spec: dict) -> Engineer:
    return STORE_HANDLERS.merge_engineer_profile_with_template(profile, engineer_spec)


def create_session_record(
    engineer_id: str,
    project: Project,
    tool: str,
    auth_mode: str,
    provider: str,
    monitor: bool = True,
    session_name: str = "",
    legacy_session: str = "",
    launch_args: list[str] | None = None,
    wrapper: str = "",
) -> SessionRecord:
    return STORE_HANDLERS.create_session_record(
        engineer_id=engineer_id,
        project=project,
        tool=tool,
        auth_mode=auth_mode,
        provider=provider,
        monitor=monitor,
        session_name=session_name,
        legacy_session=legacy_session,
        launch_args=launch_args,
        wrapper=wrapper,
    )


def _archive_timestamp() -> str:
    # Inlined from agent_admin_legacy.current_timestamp so that the hot path
    # (CRUD delete/rebind → archive_if_exists) never imports the legacy module.
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def archive_if_exists(path: Path, category: str) -> None:
    """Move *path* into the legacy archive under *category* (runtime utility).

    Previously this dispatched through ``LEGACY_HANDLERS`` which forced the
    entire ``agent_admin_legacy`` module into every import (audit H8). The
    actual behaviour is a bare ``shutil.move`` — no legacy state needed.
    """
    if not path.exists():
        return
    target = LEGACY_ROOT / category / f"{path.name}-{_archive_timestamp()}"
    ensure_dir(target.parent)
    shutil.move(str(path), str(target))


def _build_legacy_handlers():
    """Construct the on-demand LegacyHandlers. Import is deferred so the
    runtime path never pulls in migration code unless the caller actually
    needs a migration operation."""
    from agent_admin_legacy import LegacyHandlers, LegacyHooks
    return LegacyHandlers(
        LegacyHooks(
            legacy_root=LEGACY_ROOT,
            engineers_root=ENGINEERS_ROOT,
            legacy_gemini_sandboxes=LEGACY_GEMINI_SANDBOXES,
            project_defaults=PROJECT_DEFAULTS,
            legacy_engineers=LEGACY_ENGINEERS,
            error_cls=AgentAdminError,
            project_cls=Project,
            engineer_cls=Engineer,
            session_record_cls=SessionRecord,
            tool_binaries=TOOL_BINARIES,
            ensure_root_layout=ensure_root_layout,
            ensure_dir=ensure_dir,
            project_path=project_path,
            load_toml=load_toml,
            load_projects=load_projects,
            write_project=write_project,
            write_engineer=write_engineer,
            write_session=write_session,
            apply_template=apply_template,
            create_engineer_profile=create_engineer_profile,
            create_session_record=create_session_record,
            write_env_file=write_env_file,
            write_text=write_text,
            ensure_secret_permissions=ensure_secret_permissions,
        )
    )


def _legacy_state_present() -> bool:
    """Cheap check for pre-migration engineer records (a `project` field in
    engineer.toml). If none exist, migrate_session_model can short-circuit
    without importing agent_admin_legacy."""
    if not ENGINEERS_ROOT.exists():
        return False
    for path in ENGINEERS_ROOT.glob("*/engineer.toml"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Legacy files carried a top-level `project = "..."`; modern records do not.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("project ") or stripped.startswith("project="):
                return True
    return False


def migrate_session_model() -> None:
    if not _legacy_state_present():
        return
    _build_legacy_handlers().migrate_session_model()


def migrate_legacy(args: argparse.Namespace) -> int:
    return _build_legacy_handlers().migrate_legacy(args)


def engineer_summary(engineer: Engineer, sessions: dict[tuple[str, str], SessionRecord] | None = None) -> str:
    return INFO_HANDLERS.engineer_summary(engineer, sessions)


def session_summary(session: SessionRecord) -> str:
    return INFO_HANDLERS.session_summary(session)


def cmd_list_projects(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.list_projects(args)


def cmd_list_engineers(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.list_engineers(args)


def cmd_show_project(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.show_project(args)


def cmd_show_engineer(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.show_engineer(args)


def cmd_show(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.show(args)


def cmd_resolve(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.resolve(args)


def cmd_show_identity(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.show_identity(args)


def cmd_list_identities(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.list_identities(args)


def cmd_provider_list(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.list(args)


def cmd_provider_get(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.get(args)


def cmd_provider_add(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.add(args)


def cmd_provider_update(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.update(args)


def cmd_provider_remove(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.remove(args)


def cmd_provider_rename(args: argparse.Namespace) -> int:
    return PROVIDER_HANDLERS.rename(args)


def cmd_run_engineer(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.run_engineer(args)


def cmd_start(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.start(args)


def cmd_start_identity(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.start_identity(args)


def cmd_session_name(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.session_name(args)


def cmd_project_open(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_open(args)


def cmd_project_create(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_create(args)


def cmd_project_bootstrap(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_bootstrap(args)


def cmd_project_use(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_use(args)


def cmd_project_current(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_current(args)


def cmd_project_layout_set(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_layout_set(args)


def cmd_project_delete(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_delete(args)


def cmd_project_init_tools(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_init_tools(args)


def cmd_project_switch_identity(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_switch_identity(args)


# ── C2: per-project binding SSOT ──────────────────────────────────────
#
# `agent_admin project bind --project X --group oc_...` writes
# ~/.agents/tasks/X/PROJECT_BINDING.toml, the SSOT consumed by every
# Feishu closeout path via resolve_feishu_group_strict(project). The
# file is intentionally out-of-band from WORKSPACE_CONTRACT.toml so
# bootstrap/reconfigure regenerations cannot wipe the binding.


def cmd_project_bind(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_bind(args)


def cmd_project_binding_show(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_binding_show(args)


def cmd_project_binding_list(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_binding_list(args)


def cmd_project_unbind(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_unbind(args)


def cmd_session_start_engineer(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.session_start_engineer(args)


def cmd_session_reseed_sandbox(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.session_reseed_sandbox(args)


def cmd_session_batch_start_engineer(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.session_batch_start_engineer(args)


def cmd_session_provision_heartbeat(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.session_provision_heartbeat(args)


def cmd_session_stop_engineer(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.session_stop_engineer(args)


def cmd_session_rename(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.session_rename(args)


def cmd_session_start_project(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.session_start_project(args)


def cmd_session_status(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.session_status(args)


def cmd_seat_resume(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.seat_resume(args)


def cmd_seat_liveness(args: argparse.Namespace) -> int:
    rows = query_seat_liveness(args.project, max_age_seconds=args.max_age_seconds)
    if args.seat:
        rows = [
            row for row in rows
            if row.get("seat_id") == args.seat
            or row.get("role") == args.seat
            or row.get("session_name") == args.seat
        ]
    if args.as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print(f"no live seats for project {args.project}")
        return 0
    for row in rows:
        print(
            f"{row.get('role', '-')}\t{row.get('session_name', '-')}\t"
            f"{row.get('status', '-')}\t{row.get('last_heartbeat_ts', '-')}"
        )
    return 0


def cmd_project_resume(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.project_resume(args)


def cmd_session_reconcile(args: argparse.Namespace) -> int:
    from reconcile_seat_states import reconcile

    counts = reconcile(project=args.project)
    print(
        f"reconciled seats: live={counts['live']} dead={counts['dead']} skipped={counts['skipped']}"
    )
    return 0


def cmd_session_list_live(args: argparse.Namespace) -> int:
    core_path = REPO_ROOT / "core"
    if str(core_path) not in sys.path:
        sys.path.insert(0, str(core_path))
    from core.lib.state import list_seats, open_db

    with open_db() as conn:
        projects = [args.project] if args.project else [
            str(row["project"])
            for row in conn.execute("SELECT DISTINCT project FROM seats ORDER BY project").fetchall()
        ]
        for project_name in sorted(projects):
            for seat in list_seats(conn, project_name, role=args.role, status="live"):
                print(
                    "\t".join(
                        [
                            seat.project,
                            seat.seat_id,
                            seat.role,
                            seat.status,
                            seat.session_name or "",
                            seat.last_heartbeat or "",
                        ]
                    )
                )
    return 0


def cmd_session_effective_launch(args: argparse.Namespace) -> int:
    return INFO_HANDLERS.session_effective_launch(args)


def cmd_tmux_clean_stale_clients(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.tmux_clean_stale_clients(args)


def ensure_api_secret_ready(session: SessionRecord) -> None:
    SWITCH_HANDLERS.ensure_secret_ready(session)


def expected_identity_for_session(session: SessionRecord) -> str:
    return SWITCH_HANDLERS.expected_identity_for_session(session)


def reconcile_session_runtime(session: SessionRecord) -> SessionRecord:
    return SWITCH_HANDLERS.reconcile_session_runtime(session)


SWITCH_HANDLERS = SwitchHandlers(
    SwitchHooks(
        error_cls=AgentAdminError,
        legacy_secrets_root=LEGACY_SECRETS_ROOT,
        tool_binaries=TOOL_BINARIES,
        default_tool_args=DEFAULT_TOOL_ARGS,
        identity_name=identity_name,
        runtime_dir_for_identity=runtime_dir_for_identity,
        secret_file_for=secret_file_for,
        session_name_for=session_name_for,
        ensure_dir=ensure_dir,
        ensure_secret_permissions=ensure_secret_permissions,
        write_env_file=write_env_file,
        parse_env_file=parse_env_file,
        load_project=load_project,
        load_project_or_current=load_project_or_current,
        load_session=load_session,
        write_session=write_session,
        apply_template=apply_template,
        session_stop_engineer=session_stop_engineer,
        session_record_cls=SessionRecord,
        normalize_name=normalize_name,
        engineer_path=engineer_path,
        load_engineer=load_engineer,
        write_engineer=write_engineer,
        create_engineer_profile=create_engineer_profile,
        create_session_record=create_session_record,
    )
)


SESSION_SERVICE = SessionService(
    SessionHooks(
        agentctl_path=str(AGENTCTL_SH),
        launcher_path=str(REPO_ROOT / "core" / "launchers" / "agent-launcher.sh"),
        load_project=load_project,
        apply_template=apply_template,
        reconcile_session_runtime=reconcile_session_runtime,
        ensure_api_secret_ready=ensure_api_secret_ready,
        write_session=write_session,
        load_project_sessions=load_project_sessions,
        project_template_context=project_template_context,
        load_engineers=load_engineers,
        tmux_has_session=tmux_has_session,
        build_monitor_layout=build_monitor_layout,
    )
)

HEARTBEAT_HANDLERS = HeartbeatHandlers(
    HeartbeatHooks(
        error_cls=AgentAdminError,
        send_and_verify_sh=str(SEND_AND_VERIFY_SH),
        q=q,
        q_array=q_array,
        ensure_dir=ensure_dir,
        write_text=write_text,
        load_toml=load_toml,
        tmux_has_session=tmux_has_session,
        find_active_loop_owner=find_active_loop_owner,
    )
)

COMMAND_HANDLERS = CommandHandlers(
    CommandHooks(
        error_cls=AgentAdminError,
        load_project_or_current=load_project_or_current,
        resolve_engineer_session=resolve_engineer_session,
        provision_session_heartbeat=provision_session_heartbeat,
        load_project_sessions=load_project_sessions,
        tmux_has_session=tmux_has_session,
        load_projects=load_projects,
        get_current_project_name=get_current_project_name,
        session_service=SESSION_SERVICE,
        open_monitor_window=open_monitor_window,
        open_dashboard_window=open_dashboard_window,
        open_project_tabs_window=open_project_tabs_window,
        open_engineer_window=open_engineer_window,
        load_engineers=load_engineers,
        write_project=write_project,
        write_session=write_session,
        session_path=session_path,
        archive_if_exists=archive_if_exists,
        identity_name=identity_name,
        runtime_dir_for_identity=runtime_dir_for_identity,
        secret_file_for=secret_file_for,
        session_name_for=session_name_for,
        workspaces_root=WORKSPACES_ROOT,
        ensure_dir=ensure_dir,
        ensure_secret_permissions=ensure_secret_permissions,
    )
)

INFO_HANDLERS = InfoHandlers(
    InfoHooks(
        error_cls=AgentAdminError,
        load_projects=load_projects,
        load_project_or_current=load_project_or_current,
        load_project=load_project,
        load_engineers=load_engineers,
        load_sessions=load_sessions,
        project_template_context=project_template_context,
        resolve_engineer=resolve_engineer,
        resolve_engineer_session=resolve_engineer_session,
        resolve_session=resolve_session,
        display_label=display_label,
        session_status=session_status,
        build_runtime=build_runtime,
        default_launch_args=default_launch_args,
    )
)

CRUD_HANDLERS = CrudHandlers(
    CrudHooks(
        error_cls=AgentAdminError,
        project_cls=Project,
        engineer_cls=Engineer,
        session_record_cls=SessionRecord,
        sessions_root=SESSIONS_ROOT,
        workspaces_root=WORKSPACES_ROOT,
        current_project_path=CURRENT_PROJECT_PATH,
        normalize_name=normalize_name,
        project_path=project_path,
        engineer_path=engineer_path,
        session_path=session_path,
        load_project=load_project,
        load_projects=load_projects,
        load_project_or_current=load_project_or_current,
        load_engineer=load_engineer,
        load_sessions=load_sessions,
        load_template=load_template,
        load_toml=load_toml,
        merge_template_local=merge_template_local,
        write_project=write_project,
        write_engineer=write_engineer,
        write_session=write_session,
        set_current_project=set_current_project,
        get_current_project_name=get_current_project_name,
        show_project=cmd_show_project,
        resolve_engineer=resolve_engineer,
        resolve_engineer_session=resolve_engineer_session,
        create_engineer_profile=create_engineer_profile,
        merge_engineer_profile_with_template=merge_engineer_profile_with_template,
        create_session_record=create_session_record,
        apply_template=apply_template,
        render_template_text=render_template_text,
        ensure_empty_env_file=ensure_empty_env_file,
        ensure_dir=ensure_dir,
        write_text=write_text,
        write_env_file=write_env_file,
        parse_env_file=parse_env_file,
        archive_if_exists=archive_if_exists,
        identity_name=identity_name,
        runtime_dir_for_identity=runtime_dir_for_identity,
        secret_file_for=secret_file_for,
        session_name_for=session_name_for,
        ensure_secret_permissions=ensure_secret_permissions,
        session_service=SESSION_SERVICE,
        tmux_has_session=tmux_has_session,
    )
)

TUI_HOOKS = TuiHooks(
    error_cls=AgentAdminError,
    load_projects=load_projects,
    load_engineers=load_engineers,
    get_current_project_name=get_current_project_name,
    set_current_project=set_current_project,
    load_project_sessions=load_project_sessions,
    display_name_for=display_name_for,
    engineer_summary=engineer_summary,
    session_summary=session_summary,
    session_status=session_status,
    normalize_name=normalize_name,
    cmd_project_create=CRUD_HANDLERS.project_create,
    cmd_project_layout_set=CRUD_HANDLERS.project_layout_set,
    session_start_engineer=SESSION_SERVICE.start_engineer,
    cmd_window_open_dashboard=COMMAND_HANDLERS.window_open_dashboard,
    open_engineer_window=open_engineer_window,
    cmd_engineer_create=CRUD_HANDLERS.engineer_create,
    cmd_engineer_rename=CRUD_HANDLERS.engineer_rename,
    cmd_engineer_rebind=CRUD_HANDLERS.engineer_rebind,
    cmd_engineer_secret_set=CRUD_HANDLERS.engineer_secret_set,
    cmd_engineer_delete=CRUD_HANDLERS.engineer_delete,
)

def cmd_session_switch_harness(args: argparse.Namespace) -> int:
    return SWITCH_HANDLERS.session_switch_harness(args)


def cmd_session_switch_auth(args: argparse.Namespace) -> int:
    return SWITCH_HANDLERS.session_switch_auth(args)


def cmd_window_open_monitor(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.window_open_monitor(args)


def cmd_window_open_dashboard(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.window_open_dashboard(args)


def cmd_window_open_grid(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.window_open_grid(args)


def cmd_window_open_engineer(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.window_open_engineer(args)


def cmd_window_reseed_pane(args: argparse.Namespace) -> int:
    return COMMAND_HANDLERS.window_reseed_pane(args)


def cmd_engineer_create(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.engineer_create(args)


def cmd_engineer_delete(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.engineer_delete(args)


def cmd_engineer_rename(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.engineer_rename(args)


def cmd_engineer_rebind(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.engineer_rebind(args)


def cmd_engineer_refresh_workspace(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.engineer_refresh_workspace(args)


def cmd_engineer_regenerate_workspace(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.engineer_regenerate_workspace(args)


def cmd_engineer_secret_set(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.engineer_secret_set(args)


def cmd_task_create(args: argparse.Namespace) -> int:
    return task_create(args)


def cmd_task_auto_supersede(args: argparse.Namespace) -> int:
    return task_auto_supersede(args)


def cmd_task_list_pending(args: argparse.Namespace) -> int:
    return task_list_pending(args)


def cmd_task_update_status(args: argparse.Namespace) -> int:
    return task_update_status(args)


def cmd_brief_queue(args: argparse.Namespace) -> int:
    return brief_queue(args)


def cmd_brief_list(args: argparse.Namespace) -> int:
    return brief_list(args)


def cmd_brief_claim(args: argparse.Namespace) -> int:
    return brief_claim(args)


def cmd_brief_reset(args: argparse.Namespace) -> int:
    return brief_reset(args)


def cmd_brief_requeue(args: argparse.Namespace) -> int:
    return brief_requeue(args)


def cmd_brief_start(args: argparse.Namespace) -> int:
    return brief_start(args)


def cmd_brief_show(args: argparse.Namespace) -> int:
    return brief_show(args)


def cmd_brief_done(args: argparse.Namespace) -> int:
    return brief_done(args)


def cmd_brief_planner_status(args: argparse.Namespace) -> int:
    return brief_planner_status(args)


def cmd_acceptance_run(args: argparse.Namespace) -> int:
    baseline_raw = getattr(args, "baseline_criteria", None) or ""
    baseline_indices: set[int] = set()
    for token in baseline_raw.split(","):
        token = token.strip()
        if token:
            try:
                baseline_indices.add(int(token))
            except ValueError:
                print(f"warn: invalid --baseline-criteria token {token!r}; skipping", file=sys.stderr)
    try:
        results = _run_acceptance(
            project=args.project,
            team=args.team,
            task_id=args.task_id,
            brief_path=Path(args.brief_path) if args.brief_path else None,
            reviewer_seat=args.reviewer_seat,
            cwd=Path(args.cwd) if args.cwd else None,
            profile_path=Path(args.profile) if getattr(args, "profile", None) else None,
            baseline_indices=baseline_indices or None,
        )
    except _AcceptanceError as exc:
        print(f"acceptance schema error: {exc}", file=sys.stderr)
        return 2
    verdict = _aggregate_verdict(results)
    for route, r in results.items():
        passed = sum(1 for i in r.items if i.result == "pass")
        failed = sum(1 for i in r.items if i.result == "fail")
        pending = sum(1 for i in r.items if i.result == "pending")
        diagnostic = sum(1 for i in r.items if i.result == "diagnostic")
        diag_suffix = f" diagnostic={diagnostic}" if diagnostic else ""
        print(f"{route}: {r.verdict} (pass={passed} fail={failed} pending={pending}{diag_suffix})")
    print(f"aggregate: {verdict}")
    if verdict == "PASS" and not getattr(args, "skip_queue_done", False):
        done_rc = brief_done(
            argparse.Namespace(
                project=args.project,
                team=args.team,
                task_id=args.task_id,
                actor=args.actor,
                verdict="PASS",
            )
        )
        if done_rc != 0:
            return done_rc
    return 1 if verdict == "FAIL" else 0


def cmd_window_config_monitor(args: argparse.Namespace) -> int:
    project = load_project_or_current(args.project)
    engineers = [normalize_name(item) for item in args.engineers.split(",") if item.strip()]
    project.monitor_engineers = engineers
    write_project(project)
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    return run_tui_app(TUI_HOOKS)


def cmd_project_koder_bind(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_koder_bind(args)


def cmd_project_validate(args: argparse.Namespace) -> int:
    return CRUD_HANDLERS.project_validate(args)


PARSER_HOOKS = ParserHooks(
    migrate_legacy=migrate_legacy,
    cmd_list_projects=cmd_list_projects,
    cmd_list_engineers=cmd_list_engineers,
    cmd_list_identities=cmd_list_identities,
    cmd_provider_list=cmd_provider_list,
    cmd_provider_get=cmd_provider_get,
    cmd_provider_add=cmd_provider_add,
    cmd_provider_update=cmd_provider_update,
    cmd_provider_remove=cmd_provider_remove,
    cmd_provider_rename=cmd_provider_rename,
    cmd_show_project=cmd_show_project,
    cmd_show_engineer=cmd_show_engineer,
    cmd_show=cmd_show,
    cmd_resolve=cmd_resolve,
    cmd_show_identity=cmd_show_identity,
    cmd_run_engineer=cmd_run_engineer,
    cmd_start=cmd_start,
    cmd_start_identity=cmd_start_identity,
    cmd_session_name=cmd_session_name,
    cmd_project_open=cmd_project_open,
    cmd_seat_resume=cmd_seat_resume,
    cmd_seat_liveness=cmd_seat_liveness,
    cmd_project_current=cmd_project_current,
    cmd_project_use=cmd_project_use,
    cmd_project_create=cmd_project_create,
    cmd_project_bootstrap=cmd_project_bootstrap,
    cmd_project_delete=cmd_project_delete,
    cmd_project_layout_set=cmd_project_layout_set,
    cmd_project_bind=cmd_project_bind,
    cmd_project_resume=cmd_project_resume,
    cmd_project_binding_show=cmd_project_binding_show,
    cmd_project_binding_list=cmd_project_binding_list,
    cmd_project_unbind=cmd_project_unbind,
    cmd_project_init_tools=cmd_project_init_tools,
    cmd_project_switch_identity=cmd_project_switch_identity,
    cmd_session_start_engineer=cmd_session_start_engineer,
    cmd_session_reseed_sandbox=cmd_session_reseed_sandbox,
    cmd_session_batch_start_engineer=cmd_session_batch_start_engineer,
    cmd_session_provision_heartbeat=cmd_session_provision_heartbeat,
    cmd_session_stop_engineer=cmd_session_stop_engineer,
    cmd_session_rename=cmd_session_rename,
    cmd_session_start_project=cmd_session_start_project,
    cmd_session_status=cmd_session_status,
    cmd_session_reconcile=cmd_session_reconcile,
    cmd_session_list_live=cmd_session_list_live,
    cmd_session_effective_launch=cmd_session_effective_launch,
    cmd_session_switch_harness=cmd_session_switch_harness,
    cmd_session_switch_auth=cmd_session_switch_auth,
    cmd_tmux_clean_stale_clients=cmd_tmux_clean_stale_clients,
    cmd_window_open_monitor=cmd_window_open_monitor,
    cmd_window_open_dashboard=cmd_window_open_dashboard,
    cmd_window_open_grid=cmd_window_open_grid,
    cmd_window_open_engineer=cmd_window_open_engineer,
    cmd_window_reseed_pane=cmd_window_reseed_pane,
    cmd_window_config_monitor=cmd_window_config_monitor,
    cmd_engineer_create=cmd_engineer_create,
    cmd_engineer_delete=cmd_engineer_delete,
    cmd_engineer_rename=cmd_engineer_rename,
    cmd_engineer_rebind=cmd_engineer_rebind,
    cmd_engineer_refresh_workspace=cmd_engineer_refresh_workspace,
    cmd_engineer_regenerate_workspace=cmd_engineer_regenerate_workspace,
    cmd_engineer_secret_set=cmd_engineer_secret_set,
    cmd_task_create=cmd_task_create,
    cmd_task_auto_supersede=cmd_task_auto_supersede,
    cmd_task_list_pending=cmd_task_list_pending,
    cmd_task_update_status=cmd_task_update_status,
    cmd_brief_queue=cmd_brief_queue,
    cmd_brief_list=cmd_brief_list,
    cmd_brief_claim=cmd_brief_claim,
    cmd_brief_reset=cmd_brief_reset,
    cmd_brief_requeue=cmd_brief_requeue,
    cmd_brief_start=cmd_brief_start,
    cmd_brief_show=cmd_brief_show,
    cmd_brief_done=cmd_brief_done,
    cmd_brief_planner_status=cmd_brief_planner_status,
    cmd_acceptance_run=cmd_acceptance_run,
    cmd_tui=cmd_tui,
    cmd_project_koder_bind=cmd_project_koder_bind,
    cmd_machine_memory_show=cmd_machine_memory_show,
    cmd_project_seat_list=cmd_project_seat_list,
    cmd_project_validate=cmd_project_validate,
)


def build_parser() -> argparse.ArgumentParser:
    return build_agent_admin_parser(PARSER_HOOKS)


_REAL_HOME_AUTO = ""  # sentinel: --real-home with no value → auto-resolve via _real_user_home()


def _warn_unresolved_tool_bins() -> None:
    """Emit a one-line stderr warning if any backend CLI (claude/codex/
    gemini) could not be located at import time and fell back to the
    bare name. See agent_admin_config._resolve_tool_bin / audit H3."""
    from agent_admin_config import unresolved_tool_bins

    if os.environ.get("CLAWSEAT_SUPPRESS_TOOL_BIN_WARNING"):
        return
    missing = unresolved_tool_bins()
    if not missing:
        return
    print(
        "agent_admin: WARNING — backend CLI binaries not found on disk: "
        f"{', '.join(missing)} (will use bare name; execution may fail). "
        "Install via `npm i -g @anthropic-ai/claude-code @openai/codex @google/gemini-cli` "
        "or set `CLAWSEAT_SUPPRESS_TOOL_BIN_WARNING=1` to silence.",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    # Pre-parse --real-home using parse_known_args so it is accepted at ANY
    # position — before or after the subcommand (e.g. "engineer list --real-home").
    # nargs='?' means: flag alone → const (auto-resolve); flag + path → explicit override.
    from agent_admin_config import _real_user_home as _get_real_home
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument(
        "--real-home",
        dest="real_home",
        nargs="?",
        const=_REAL_HOME_AUTO,
        default=None,
        help="Set CLAWSEAT_REAL_HOME for this invocation. "
             "Alone: auto-resolve via pwd. With path: use that path explicitly.",
    )
    pre_args, remaining = pre.parse_known_args(argv)
    if pre_args.real_home is not None:
        if pre_args.real_home == _REAL_HOME_AUTO:
            os.environ["CLAWSEAT_REAL_HOME"] = str(_get_real_home())
        else:
            os.environ["CLAWSEAT_REAL_HOME"] = str(Path(pre_args.real_home).expanduser())

    ensure_root_layout()
    migrate_session_model()
    _warn_unresolved_tool_bins()
    parser = build_parser()
    args = parser.parse_args(remaining)
    try:
        return args.func(args)
    except (AgentAdminError, AgentAdminWindowError, SessionStartError, TaskCommandError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
