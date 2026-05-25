from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ParserHooks:
    migrate_legacy: Callable[[Any], int]
    cmd_list_projects: Callable[[Any], int]
    cmd_list_engineers: Callable[[Any], int]
    cmd_list_identities: Callable[[Any], int]
    cmd_provider_list: Callable[[Any], int]
    cmd_provider_get: Callable[[Any], int]
    cmd_provider_add: Callable[[Any], int]
    cmd_provider_update: Callable[[Any], int]
    cmd_provider_remove: Callable[[Any], int]
    cmd_provider_rename: Callable[[Any], int]
    cmd_show_project: Callable[[Any], int]
    cmd_show_engineer: Callable[[Any], int]
    cmd_show: Callable[[Any], int]
    cmd_resolve: Callable[[Any], int]
    cmd_show_identity: Callable[[Any], int]
    cmd_run_engineer: Callable[[Any], int]
    cmd_start: Callable[[Any], int]
    cmd_start_identity: Callable[[Any], int]
    cmd_session_name: Callable[[Any], int]
    cmd_project_open: Callable[[Any], int]
    cmd_seat_resume: Callable[[Any], int]
    cmd_seat_liveness: Callable[[Any], int]
    cmd_project_current: Callable[[Any], int]
    cmd_project_use: Callable[[Any], int]
    cmd_project_create: Callable[[Any], int]
    cmd_project_bootstrap: Callable[[Any], int]
    cmd_project_delete: Callable[[Any], int]
    cmd_project_layout_set: Callable[[Any], int]
    cmd_project_bind: Callable[[Any], int]
    cmd_project_resume: Callable[[Any], int]
    cmd_project_binding_show: Callable[[Any], int]
    cmd_project_binding_list: Callable[[Any], int]
    cmd_project_unbind: Callable[[Any], int]
    cmd_project_init_tools: Callable[[Any], int]
    cmd_project_switch_identity: Callable[[Any], int]
    cmd_session_start_engineer: Callable[[Any], int]
    cmd_session_reseed_sandbox: Callable[[Any], int]
    cmd_session_batch_start_engineer: Callable[[Any], int]
    cmd_session_provision_heartbeat: Callable[[Any], int]
    cmd_session_stop_engineer: Callable[[Any], int]
    cmd_session_rename: Callable[[Any], int]
    cmd_session_start_project: Callable[[Any], int]
    cmd_session_status: Callable[[Any], int]
    cmd_session_reconcile: Callable[[Any], int]
    cmd_session_list_live: Callable[[Any], int]
    cmd_session_effective_launch: Callable[[Any], int]
    cmd_session_switch_harness: Callable[[Any], int]
    cmd_session_switch_auth: Callable[[Any], int]
    cmd_tmux_clean_stale_clients: Callable[[Any], int]
    cmd_window_open_monitor: Callable[[Any], int]
    cmd_window_open_dashboard: Callable[[Any], int]
    cmd_window_open_grid: Callable[[Any], int]
    cmd_window_open_engineer: Callable[[Any], int]
    cmd_window_reseed_pane: Callable[[Any], int]
    cmd_window_config_monitor: Callable[[Any], int]
    cmd_engineer_create: Callable[[Any], int]
    cmd_engineer_delete: Callable[[Any], int]
    cmd_engineer_rename: Callable[[Any], int]
    cmd_engineer_rebind: Callable[[Any], int]
    cmd_engineer_refresh_workspace: Callable[[Any], int]
    cmd_engineer_regenerate_workspace: Callable[[Any], int]
    cmd_engineer_secret_set: Callable[[Any], int]
    cmd_task_create: Callable[[Any], int]
    cmd_task_auto_supersede: Callable[[Any], int]
    cmd_task_list_pending: Callable[[Any], int]
    cmd_task_update_status: Callable[[Any], int]
    cmd_tui: Callable[[Any], int]
    # P1 layered-model (see docs/schemas/v0.4-layered-model.md):
    cmd_project_koder_bind: Callable[[Any], int]
    cmd_machine_memory_show: Callable[[Any], int]
    cmd_project_seat_list: Callable[[Any], int]
    cmd_project_validate: Callable[[Any], int]
    # v3 brief / queue commands (spec §4.2 §4.3)
    cmd_brief_queue: Callable[[Any], int]
    cmd_brief_list: Callable[[Any], int]
    cmd_brief_claim: Callable[[Any], int]
    cmd_brief_reset: Callable[[Any], int]
    cmd_brief_requeue: Callable[[Any], int]
    cmd_brief_start: Callable[[Any], int]
    cmd_brief_show: Callable[[Any], int]
    cmd_brief_done: Callable[[Any], int]
    cmd_brief_planner_status: Callable[[Any], int]
    # v3 acceptance executor (Phase 2, spec §4.7)
    cmd_acceptance_run: Callable[[Any], int]


def build_parser(hooks: ParserHooks) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    template_help = "Template name/path. Built-ins: clawseat-engineering, clawseat-creative, clawseat-solo."

    migrate = sub.add_parser("migrate-legacy", help="Migrate legacy engineer/profile state.")
    migrate.add_argument("--force", action="store_true")
    migrate.set_defaults(func=hooks.migrate_legacy)

    list_projects = sub.add_parser("list-projects", help="List configured projects.")
    list_projects.set_defaults(func=hooks.cmd_list_projects)

    list_engineers = sub.add_parser("list-engineers", help="List configured engineers/seats.")
    list_engineers.set_defaults(func=hooks.cmd_list_engineers)

    list_identities = sub.add_parser("list-identities", help="List configured tool identities.")
    list_identities.set_defaults(func=hooks.cmd_list_identities)

    provider = sub.add_parser("provider", help="Provider SSOT registry and secret-file operations.")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True)

    provider_list = provider_sub.add_parser("list", help="List configured providers.")
    provider_list.add_argument("--tool", choices=["claude", "codex", "gemini"])
    provider_list.add_argument("--json", action="store_true")
    provider_list.set_defaults(func=hooks.cmd_provider_list)

    provider_get = provider_sub.add_parser("get", help="Show one provider record.")
    provider_get.add_argument("--name", required=True)
    provider_get.add_argument("--json", action="store_true")
    provider_get.set_defaults(func=hooks.cmd_provider_get)

    provider_add = provider_sub.add_parser("add", help="Add one provider record.")
    provider_add.add_argument("--name", required=True)
    provider_add.add_argument("--tool", required=True, choices=["claude", "codex", "gemini"])
    provider_add.add_argument("--kind", required=True, choices=["api_key", "oauth_token"])
    provider_add.add_argument(
        "--family",
        required=True,
        choices=["anthropic", "minimax", "deepseek", "xcode-best", "openai", "openai-compat", "gemini"],
    )
    provider_add.add_argument("--base-url", default="")
    provider_add.add_argument("--model", default="")
    provider_add.add_argument("--secret-stdin", action="store_true", required=True)
    provider_add.add_argument("--json", action="store_true")
    provider_add.set_defaults(func=hooks.cmd_provider_add)

    provider_update = provider_sub.add_parser("update", help="Update provider metadata or secret.")
    provider_update.add_argument("--name", required=True)
    provider_update.add_argument("--base-url")
    provider_update.add_argument("--model")
    provider_update.add_argument("--secret-stdin", action="store_true")
    provider_update.add_argument("--json", action="store_true")
    provider_update.set_defaults(func=hooks.cmd_provider_update)

    provider_remove = provider_sub.add_parser("remove", help="Remove one provider record.")
    provider_remove.add_argument("--name", required=True)
    provider_remove.add_argument("--force", action="store_true")
    provider_remove.add_argument("--json", action="store_true")
    provider_remove.set_defaults(func=hooks.cmd_provider_remove)

    provider_rename = provider_sub.add_parser("rename", help="Rename one provider record.")
    provider_rename.add_argument("--from", dest="from_name", required=True)
    provider_rename.add_argument("--to", dest="to_name", required=True)
    provider_rename.add_argument("--json", action="store_true")
    provider_rename.set_defaults(func=hooks.cmd_provider_rename)

    show_project = sub.add_parser("show-project", help="Show one project record.")
    show_project.add_argument("project")
    show_project.set_defaults(func=hooks.cmd_show_project)

    show_engineer = sub.add_parser("show-engineer", help="Show one engineer/seat record.")
    show_engineer.add_argument("engineer")
    show_engineer.add_argument("--project")
    show_engineer.set_defaults(func=hooks.cmd_show_engineer)

    show = sub.add_parser("show", help="Show one engineer/seat record.")
    show.add_argument("engineer")
    show.add_argument("--project")
    show.set_defaults(func=hooks.cmd_show)

    resolve = sub.add_parser("resolve", help="Resolve an engineer launch identity for a tool.")
    resolve.add_argument("engineer")
    resolve.add_argument("tool", choices=["codex", "claude", "gemini"])
    resolve.add_argument("--project")
    resolve.set_defaults(func=hooks.cmd_resolve)

    show_identity = sub.add_parser("show-identity", help="Show one tool identity.")
    show_identity.add_argument("identity")
    show_identity.set_defaults(func=hooks.cmd_show_identity)

    run_engineer = sub.add_parser("run-engineer", help="Run a command as an engineer identity.")
    run_engineer.add_argument("engineer")
    run_engineer.add_argument("--project")
    run_engineer.add_argument("cmd", nargs=argparse.REMAINDER)
    run_engineer.set_defaults(func=hooks.cmd_run_engineer)

    start = sub.add_parser("start", help="Start a tool command for an engineer identity.")
    start.add_argument("engineer")
    start.add_argument("tool", choices=["codex", "claude", "gemini"])
    start.add_argument("--project")
    start.add_argument("cmd", nargs=argparse.REMAINDER)
    start.set_defaults(func=hooks.cmd_start)

    start_identity = sub.add_parser("start-identity", help="Start a command for a raw identity.")
    start_identity.add_argument("identity")
    start_identity.add_argument("cmd", nargs=argparse.REMAINDER)
    start_identity.set_defaults(func=hooks.cmd_start_identity)

    session_name = sub.add_parser("session-name", help="Print the canonical tmux session name.")
    session_name.add_argument("target")
    session_name.add_argument("--project")
    session_name.set_defaults(func=hooks.cmd_session_name)

    project_open = sub.add_parser("project-open", help="Open a project workspace/window.")
    project_open.add_argument("project")
    project_open.set_defaults(func=hooks.cmd_project_open)

    project = sub.add_parser("project", help="Project registry, bootstrap, binding, and validation operations.")
    project_sub = project.add_subparsers(dest="project_command", required=True)

    project_list_nested = project_sub.add_parser("list")
    project_list_nested.set_defaults(func=hooks.cmd_list_projects)

    project_current_nested = project_sub.add_parser("current")
    project_current_nested.set_defaults(func=hooks.cmd_project_current)

    project_use_nested = project_sub.add_parser("use")
    project_use_nested.add_argument("project")
    project_use_nested.set_defaults(func=hooks.cmd_project_use)

    project_show_nested = project_sub.add_parser("show")
    project_show_nested.add_argument("project", nargs="?")
    project_show_nested.set_defaults(func=hooks.cmd_show_project)

    project_open_nested = project_sub.add_parser("open")
    project_open_nested.add_argument("project")
    project_open_nested.set_defaults(func=hooks.cmd_project_open)

    project_resume_nested = project_sub.add_parser("resume", help="Resume all seats in a project.")
    project_resume_nested.add_argument("project")
    project_resume_nested.add_argument(
        "--fresh",
        action="store_true",
        help="Skip auto-resume and start each seat fresh.",
    )
    project_resume_nested.set_defaults(func=hooks.cmd_project_resume)

    project_create_nested = project_sub.add_parser("create")
    project_create_nested.add_argument("project")
    project_create_nested.add_argument("repo_root")
    project_create_nested.add_argument(
        "--template",
        default="clawseat-engineering",
        metavar="TEMPLATE",
        help=f"Project roster template to use (default: clawseat-engineering). {template_help}",
    )
    project_create_nested.add_argument("--window-mode", choices=["tabs-1up", "tabs-2up", "split-2"], default=None)
    project_create_nested.add_argument("--open-detail-windows", action="store_true")
    project_create_nested.set_defaults(func=hooks.cmd_project_create)

    project_bootstrap_nested = project_sub.add_parser("bootstrap")
    project_bootstrap_nested.add_argument(
        "--template",
        required=True,
        metavar="TEMPLATE",
        help=template_help,
    )
    project_bootstrap_nested.add_argument(
        "--local",
        required=True,
        help="Path to a local TOML override file containing project_name/repo_root overrides",
    )
    project_bootstrap_nested.add_argument("--start", action="store_true")
    project_bootstrap_nested.set_defaults(func=hooks.cmd_project_bootstrap)

    project_delete_nested = project_sub.add_parser("delete")
    project_delete_nested.add_argument("project")
    project_delete_nested.set_defaults(func=hooks.cmd_project_delete)

    project_layout_nested = project_sub.add_parser("layout")
    project_layout_nested.add_argument("project", nargs="?")
    project_layout_nested.add_argument("--window-mode", choices=["tabs-1up", "tabs-2up", "project-monitor"])
    project_layout_nested.add_argument("--monitor-max-panes", type=int)
    project_layout_nested.add_argument("--monitor-engineers")
    project_layout_nested.add_argument("--open-detail-windows", choices=["true", "false"])
    project_layout_nested.set_defaults(func=hooks.cmd_project_layout_set)

    # C2: per-project binding SSOT — writes ~/.agents/tasks/<project>/PROJECT_BINDING.toml
    project_bind_nested = project_sub.add_parser(
        "bind",
        help="Write per-project binding (Feishu group / sender app / koder agent). See C2 guardrail.",
    )
    project_bind_nested.add_argument("--project", required=True)
    project_bind_nested.add_argument(
        "--feishu-group",
        "--group",
        dest="feishu_group",
        required=True,
        help="Feishu group id (must start with 'oc_').",
    )
    project_bind_nested.add_argument(
        "--feishu-sender-app-id",
        dest="feishu_sender_app_id",
        default="",
        help="lark-cli sender app id (cli_...).",
    )
    project_bind_nested.add_argument(
        "--feishu-sender-mode",
        dest="feishu_sender_mode",
        choices=["user", "bot", "auto"],
        default="auto",
        help="lark-cli sender mode (default: auto).",
    )
    project_bind_nested.add_argument(
        "--openclaw-koder-agent",
        dest="openclaw_koder_agent",
        default="",
        help="OpenClaw agent that receives the koder overlay.",
    )
    project_bind_nested.add_argument(
        "--feishu-bot-account",
        "--account",
        dest="feishu_bot_account",
        default=None,
        help="Deprecated alias: routes to sender app id when value starts with cli_, else to openclaw-koder-agent.",
    )
    project_bind_nested.add_argument(
        "--require-mention",
        dest="require_mention",
        action="store_true",
        help="Gate bot responses on @mention for this group.",
    )
    project_bind_nested.add_argument(
        "--bound-by",
        default="",
        help="Optional label of who/what wrote the binding.",
    )
    project_bind_nested.set_defaults(func=hooks.cmd_project_bind)

    project_binding_show_nested = project_sub.add_parser(
        "binding-show",
        help="Print the binding file path and its contents for <project>.",
    )
    project_binding_show_nested.add_argument("project")
    project_binding_show_nested.set_defaults(func=hooks.cmd_project_binding_show)

    project_binding_list_nested = project_sub.add_parser(
        "binding-list",
        help="List every project that has a PROJECT_BINDING.toml.",
    )
    project_binding_list_nested.set_defaults(func=hooks.cmd_project_binding_list)

    project_unbind_nested = project_sub.add_parser(
        "unbind",
        help="Delete the PROJECT_BINDING.toml for <project>.",
    )
    project_unbind_nested.add_argument("project")
    project_unbind_nested.set_defaults(func=hooks.cmd_project_unbind)

    project_init_tools_nested = project_sub.add_parser(
        "init-tools",
        help="Initialize per-project tool state under ~/.agent-runtime/projects/<project>.",
    )
    project_init_tools_nested.add_argument("project")
    project_init_tools_nested.add_argument(
        "--from",
        dest="from_source",
        choices=["real-home", "empty"],
        default="real-home",
        help="Copy from real HOME or create empty tool dirs.",
    )
    project_init_tools_nested.add_argument(
        "--source-project",
        default="",
        help="Optional project name to copy tool state from instead of real HOME.",
    )
    project_init_tools_nested.add_argument(
        "--tools",
        default="",
        help="Comma-separated tool seed list: lark-cli, gemini, codex, iterm2.",
    )
    project_init_tools_nested.add_argument("--dry-run", action="store_true")
    project_init_tools_nested.set_defaults(func=hooks.cmd_project_init_tools)

    project_switch_identity_nested = project_sub.add_parser(
        "switch-identity",
        help="Switch a project's Feishu/Gemini/Codex identity and reseed its seats.",
    )
    project_switch_identity_nested.add_argument("project")
    project_switch_identity_nested.add_argument(
        "--tool",
        required=True,
        choices=["feishu", "gemini", "codex"],
        help="Which project identity to switch.",
    )
    project_switch_identity_nested.add_argument(
        "--identity",
        required=True,
        help="New identity value (Feishu app id, Gemini email, or Codex email).",
    )
    project_switch_identity_nested.add_argument("--dry-run", action="store_true")
    project_switch_identity_nested.set_defaults(func=hooks.cmd_project_switch_identity)

    # P1 layered-model: project koder-bind / seat list / validate (§3-§5).
    project_koder_bind_nested = project_sub.add_parser(
        "koder-bind",
        help="Bind an OpenClaw tenant as this project's koder frontstage "
             "(v0.4 layered model).",
    )
    project_koder_bind_nested.add_argument("--project", required=True)
    project_koder_bind_nested.add_argument(
        "--tenant", required=True,
        help="Tenant name as registered in machine.toml [openclaw_tenants.X].",
    )
    project_koder_bind_nested.add_argument(
        "--feishu-group-id",
        default=None,
        dest="feishu_group_id",
        help=(
            "Feishu open-chat group ID (format: oc_ + ≥16 chars). "
            "If omitted, 'oc_pending' placeholder is written and can be "
            "updated later with `project bind --feishu-group`."
        ),
    )
    project_koder_bind_nested.set_defaults(func=hooks.cmd_project_koder_bind)

    project_seat_nested = project_sub.add_parser(
        "seat",
        help="Per-project seat operations (list, ...).",
    )
    project_seat_sub = project_seat_nested.add_subparsers(
        dest="project_seat_command", required=True,
    )
    project_seat_list_nested = project_seat_sub.add_parser(
        "list",
        help="List expanded seats for <project> (respects parallel_instances).",
    )
    project_seat_list_nested.add_argument("--project", required=True)
    project_seat_list_nested.set_defaults(func=hooks.cmd_project_seat_list)

    project_validate_nested = project_sub.add_parser(
        "validate",
        help="Validate <project>'s profile against the v0.4 layered schema.",
    )
    project_validate_nested.add_argument("--project", required=True)
    project_validate_nested.set_defaults(func=hooks.cmd_project_validate)

    seat = sub.add_parser("seat", help="Per-seat resume operations.")
    seat_sub = seat.add_subparsers(dest="seat_command", required=True)
    seat_resume = seat_sub.add_parser(
        "resume",
        help="Resume a single seat from its active session marker.",
    )
    seat_resume.add_argument("seat")
    seat_resume.add_argument("--project")
    seat_resume.add_argument(
        "--fresh",
        action="store_true",
        help="Skip auto-resume and start the seat fresh.",
    )
    seat_resume.set_defaults(func=hooks.cmd_seat_resume)

    seat_liveness = seat_sub.add_parser(
        "liveness",
        help="Show live seats for a project from heartbeat/state records.",
    )
    seat_liveness.add_argument("--project", required=True)
    seat_liveness.add_argument("--seat", default=None, help="Optional seat/role filter.")
    seat_liveness.add_argument(
        "--max-age-seconds",
        type=int,
        default=300,
        help="Heartbeat freshness window in seconds (default: 300).",
    )
    seat_liveness.add_argument("--json", action="store_true", dest="as_json")
    seat_liveness.set_defaults(func=hooks.cmd_seat_liveness)

    # P1 layered-model: machine ... (§3).
    machine = sub.add_parser("machine", help="Machine-layer operations.")
    machine_sub = machine.add_subparsers(dest="machine_command", required=True)
    machine_memory = machine_sub.add_parser(
        "memory", help="Memory singleton service operations.",
    )
    machine_memory_sub = machine_memory.add_subparsers(
        dest="machine_memory_command", required=True,
    )
    machine_memory_show = machine_memory_sub.add_parser(
        "show", help="Print memory service config + runtime status.",
    )
    machine_memory_show.set_defaults(func=hooks.cmd_machine_memory_show)

    session = sub.add_parser("session", help="Project-scoped tmux session lifecycle operations.")
    session_sub = session.add_subparsers(dest="session_command", required=True)

    session_start_eng = session_sub.add_parser("start-engineer")
    session_start_eng.add_argument("engineer")
    session_start_eng.add_argument("--project")
    session_start_eng.add_argument("--reset", action="store_true")
    session_start_eng.add_argument(
        "--accept-override",
        action="store_true",
        help="bypass project.toml seat_overrides SSOT mismatch guard",
    )
    session_start_eng.set_defaults(func=hooks.cmd_session_start_engineer)

    session_reseed_sandbox = session_sub.add_parser(
        "reseed-sandbox",
        help="Rebuild sandbox HOME symlinks for a project or specific seats.",
    )
    session_reseed_sandbox.add_argument("--project")
    session_reseed_sandbox.add_argument("--all", action="store_true")
    session_reseed_sandbox.add_argument("engineers", nargs="*")
    session_reseed_sandbox.set_defaults(func=hooks.cmd_session_reseed_sandbox)

    # batch-start-engineer: atomic multi-seat startup.
    # Phase 1: parallel tmux start for every engineer (no iTerm side effects).
    # Python threads join before Phase 2, replacing the `wait` that shell
    # operators used to have to remember.
    # Phase 2: single `window open-monitor` call creates one iTerm window with
    # one tab per seat, in one AppleScript invocation. No race is possible
    # because there is no concurrency during Phase 2.
    # --no-iterm skips Phase 2 (tmux-only mode) — useful for CI or when the
    # operator plans to open the window later with `window open-monitor`.
    session_batch_start = session_sub.add_parser(
        "batch-start-engineer",
        help="start N seats in parallel (tmux) then open one iTerm window with all tabs (single AppleScript)",
    )
    session_batch_start.add_argument(
        "engineers",
        nargs="+",
        help="engineer ids to start (e.g. planner builder-1 reviewer-1 designer-1)",
    )
    session_batch_start.add_argument("--project")
    session_batch_start.add_argument("--reset", action="store_true")
    session_batch_start.add_argument(
        "--accept-override",
        action="store_true",
        help="bypass project.toml seat_overrides SSOT mismatch guard",
    )
    session_batch_start.add_argument(
        "--no-iterm",
        action="store_true",
        help="skip Phase 2; only start tmux sessions",
    )
    session_batch_start.set_defaults(func=hooks.cmd_session_batch_start_engineer)

    session_provision_heartbeat = session_sub.add_parser("provision-heartbeat")
    session_provision_heartbeat.add_argument("engineer")
    session_provision_heartbeat.add_argument("--project")
    session_provision_heartbeat.add_argument("--force", action="store_true")
    session_provision_heartbeat.add_argument("--dry-run", action="store_true")
    session_provision_heartbeat.set_defaults(func=hooks.cmd_session_provision_heartbeat)

    session_stop_eng = session_sub.add_parser("stop-engineer")
    session_stop_eng.add_argument("engineer")
    session_stop_eng.add_argument("--project")
    session_stop_eng.add_argument("--keep-iterm-tab", action="store_true", help="Skip closing the iTerm pane before killing the tmux session. (Flag name kept for backward compatibility; closes the matching pane only, not the entire tab — see RCA 2026-04-25.)")
    session_stop_eng.set_defaults(func=hooks.cmd_session_stop_engineer)

    session_rename = session_sub.add_parser(
        "rename",
        help="Rename one project-scoped seat session and restart it under the new seat id.",
    )
    session_rename.add_argument("--project", required=True)
    session_rename.add_argument("--from", dest="from_seat", required=True)
    session_rename.add_argument("--to", dest="to_seat", required=True)
    session_rename.set_defaults(func=hooks.cmd_session_rename)

    session_start_project = session_sub.add_parser("start-project")
    session_start_project.add_argument("project", nargs="?")
    session_start_project.add_argument("--reset", action="store_true")
    session_start_project.add_argument("--no-monitor", action="store_true")
    session_start_project.set_defaults(func=hooks.cmd_session_start_project)

    session_status_parser = session_sub.add_parser("status")
    session_status_parser.add_argument("engineer", nargs="?")
    session_status_parser.add_argument("--project")
    session_status_parser.set_defaults(func=hooks.cmd_session_status)

    session_reconcile_parser = session_sub.add_parser("reconcile")
    session_reconcile_parser.add_argument("--project")
    session_reconcile_parser.set_defaults(func=hooks.cmd_session_reconcile)

    session_list_live_parser = session_sub.add_parser("list-live")
    session_list_live_parser.add_argument("--project")
    session_list_live_parser.add_argument("--role")
    session_list_live_parser.set_defaults(func=hooks.cmd_session_list_live)

    session_effective_launch = session_sub.add_parser("effective-launch")
    session_effective_launch.add_argument("engineer")
    session_effective_launch.add_argument("--project")
    session_effective_launch.add_argument("cmd", nargs=argparse.REMAINDER)
    session_effective_launch.set_defaults(func=hooks.cmd_session_effective_launch)

    switch_harness = session_sub.add_parser("switch-harness")
    switch_harness.add_argument("--project", required=True)
    switch_harness.add_argument("--engineer", required=True)
    switch_harness.add_argument("--tool", required=True, choices=["codex", "claude", "gemini"])
    switch_harness.add_argument("--mode", required=True, choices=["oauth", "oauth_token", "api"])
    switch_harness.add_argument("--provider", required=True)
    switch_harness.add_argument(
        "--model",
        default="",
        help="Optional model override. Currently supported only for tool=claude.",
    )
    switch_harness.set_defaults(func=hooks.cmd_session_switch_harness)

    switch_auth = session_sub.add_parser("switch-auth")
    switch_auth.add_argument("--project", required=True)
    switch_auth.add_argument("--engineer", required=True)
    switch_auth.add_argument("--mode", required=True, choices=["oauth", "oauth_token", "api"])
    switch_auth.add_argument("--provider", required=True)
    switch_auth.set_defaults(func=hooks.cmd_session_switch_auth)

    tmux = sub.add_parser("tmux", help="tmux client maintenance operations.")
    tmux_sub = tmux.add_subparsers(dest="tmux_command", required=True)

    tmux_clean_stale_clients = tmux_sub.add_parser(
        "clean-stale-clients",
        help="Reap stale tmux attach clients for a project.",
    )
    tmux_clean_stale_clients.add_argument("--project")
    tmux_clean_stale_clients.add_argument("--dry-run", action="store_true")
    tmux_clean_stale_clients.set_defaults(func=hooks.cmd_tmux_clean_stale_clients)

    window = sub.add_parser("window", help="iTerm/tmux project window operations.")
    window_sub = window.add_subparsers(dest="window_command", required=True)

    open_monitor = window_sub.add_parser("open-monitor")
    open_monitor.add_argument("project", nargs="?")
    open_monitor.set_defaults(func=hooks.cmd_window_open_monitor)

    open_dashboard = window_sub.add_parser("open-dashboard")
    open_dashboard.set_defaults(func=hooks.cmd_window_open_dashboard)

    open_grid = window_sub.add_parser(
        "open-grid",
        help="Reopen the project iTerm grid. --open-memory/--refresh-memories control explicit memory refresh.",
    )
    open_grid.add_argument("project")
    open_grid.add_argument("--recover", action="store_true")
    open_grid.add_argument("--rebuild", action="store_true", help="Close any existing project window and open a fresh grid.")
    open_grid.add_argument(
        "--open-memory",
        action="store_true",
        help="Compatibility alias for explicit memory refresh during grid open.",
    )
    open_grid.add_argument(
        "--refresh-memories",
        action="store_true",
        help="Explicitly refresh the shared memories window during this run.",
    )
    open_grid.add_argument("--quiet", action="store_true", help="Suppress the summary line.")
    open_grid.set_defaults(func=hooks.cmd_window_open_grid)

    open_engineer = window_sub.add_parser("open-engineer")
    open_engineer.add_argument("engineer")
    open_engineer.add_argument("--project")
    open_engineer.set_defaults(func=hooks.cmd_window_open_engineer)

    reseed_pane = window_sub.add_parser("reseed-pane")
    reseed_pane.add_argument("seat")
    reseed_pane.add_argument("--project", required=True)
    reseed_pane.set_defaults(func=hooks.cmd_window_reseed_pane)

    config_monitor = window_sub.add_parser("config-monitor")
    config_monitor.add_argument("project", nargs="?")
    config_monitor.add_argument("engineers")
    config_monitor.set_defaults(func=hooks.cmd_window_config_monitor)

    engineer = sub.add_parser("engineer", help="Engineer/seat CRUD and workspace operations.")
    engineer_sub = engineer.add_subparsers(dest="engineer_command", required=True)

    engineer_list_nested = engineer_sub.add_parser("list")
    engineer_list_nested.set_defaults(func=hooks.cmd_list_engineers)

    engineer_show_nested = engineer_sub.add_parser("show")
    engineer_show_nested.add_argument("engineer")
    engineer_show_nested.add_argument("--project")
    engineer_show_nested.set_defaults(func=hooks.cmd_show_engineer)

    create = engineer_sub.add_parser("create")
    create.add_argument("engineer")
    create.add_argument("project")
    create.add_argument("tool", nargs="?", choices=["codex", "claude", "gemini"])
    create.add_argument("mode", nargs="?", choices=["oauth", "api"])
    create.add_argument("provider", nargs="?")
    create.add_argument("--no-monitor", action="store_true")
    create.set_defaults(func=hooks.cmd_engineer_create)

    delete = engineer_sub.add_parser("delete")
    delete.add_argument("engineer")
    delete.add_argument("--project")
    delete.set_defaults(func=hooks.cmd_engineer_delete)

    rename = engineer_sub.add_parser("rename")
    rename.add_argument("old")
    rename.add_argument("new")
    rename.set_defaults(func=hooks.cmd_engineer_rename)

    rebind = engineer_sub.add_parser(
        "rebind",
        help="Change auth_mode + provider only (cannot change tool).",
        description=(
            "rebind changes auth_mode + provider only; it CANNOT change the tool "
            "(e.g. claude → codex). To swap tools, use 'engineer delete' followed "
            "by 'engineer create'."
        ),
    )
    rebind.add_argument("engineer")
    rebind.add_argument("--project")
    rebind.add_argument("mode", choices=["oauth", "api"])
    rebind.add_argument("provider")
    rebind.add_argument(
        "--tool",
        choices=["claude", "codex"],
        default=None,
        help=(
            "Safety check only; must match current tool. "
            "Use delete+create to actually swap tools."
        ),
    )
    rebind.set_defaults(func=hooks.cmd_engineer_rebind)

    refresh_workspace = engineer_sub.add_parser("refresh-workspace")
    refresh_workspace.add_argument("engineer")
    refresh_workspace.add_argument("--project")
    refresh_workspace.set_defaults(func=hooks.cmd_engineer_refresh_workspace)

    regenerate_workspace = engineer_sub.add_parser("regenerate-workspace")
    regenerate_workspace.add_argument("engineer", nargs="?")
    regenerate_workspace.add_argument("--all-seats", action="store_true")
    regenerate_workspace.add_argument("--project", required=True)
    regenerate_workspace.add_argument(
        "--yes",
        action="store_true",
        help="Assume yes for overwrite prompts after the operator has already approved a bulk re-render.",
    )
    regenerate_workspace.set_defaults(func=hooks.cmd_engineer_regenerate_workspace)

    secret = engineer_sub.add_parser("secret-set")
    secret.add_argument("engineer")
    secret.add_argument("--project")
    secret.add_argument("key")
    secret.add_argument("value")
    secret.set_defaults(func=hooks.cmd_engineer_secret_set)

    task = sub.add_parser("task", help="Project task TODO/workflow status operations.")
    task_sub = task.add_subparsers(dest="task_command", required=True)

    task_create = task_sub.add_parser("create")
    task_create.add_argument("task_id")
    task_create.add_argument("--project", required=True)
    task_create.add_argument("--workflow-template", default="")
    task_create.set_defaults(func=hooks.cmd_task_create)

    task_auto_supersede = task_sub.add_parser("auto-supersede")
    task_auto_supersede.add_argument("--project", required=True)
    task_auto_supersede.add_argument("--age-days", type=int, default=3)
    task_auto_supersede.set_defaults(func=hooks.cmd_task_auto_supersede)

    task_list_pending = task_sub.add_parser("list-pending")
    task_list_pending.add_argument("--project", required=True)
    task_list_pending.add_argument("--owner-role", required=True)
    task_list_pending.set_defaults(func=hooks.cmd_task_list_pending)

    task_update_status = task_sub.add_parser("update-status")
    task_update_status.add_argument("task_id")
    task_update_status.add_argument("step_name")
    task_update_status.add_argument("status", choices=["pending", "in_progress", "done", "blocked"])
    task_update_status.add_argument("--project", required=True)
    task_update_status.set_defaults(func=hooks.cmd_task_update_status)

    # v3 brief subcommand — memory writes brief + queue events, planner pulls.
    # Spec §4.2 (brief schema) + §4.3 (queue events).
    brief = sub.add_parser(
        "brief",
        help="v3 multi-team brief/queue ops (memory writes brief, planner claims).",
    )
    brief_sub = brief.add_subparsers(dest="brief_command", required=True)

    brief_queue = brief_sub.add_parser(
        "queue",
        help="Write brief markdown + append task_created event to per-team queue.",
    )
    brief_queue.add_argument("--project", required=True)
    brief_queue.add_argument("--team", required=True)
    brief_queue.add_argument("--task-id", required=True, dest="task_id")
    brief_queue.add_argument("--objective", required=True)
    brief_queue.add_argument("--depends-on", nargs="*", default=[], dest="depends_on")
    brief_queue.add_argument(
        "--seats-required",
        nargs="*",
        default=None,
        dest="seats_required",
        help="Seats required (default: ['builder']). Schema requires non-empty.",
    )
    brief_queue.add_argument("--parent-task-id", default=None, dest="parent_task_id")
    brief_queue.add_argument(
        "--brief-content-file",
        default=None,
        dest="brief_content_file",
        help="Optional path to pre-written brief markdown (overrides skeleton).",
    )
    brief_queue.add_argument("--force", action="store_true", help="Overwrite existing brief.")
    brief_queue.add_argument(
        "--no-wake",
        action="store_true",
        help="Append the queue event without waking the team planner.",
    )
    brief_queue.add_argument(
        "--allow-open",
        action="store_true",
        help="Explicitly allow queueing while this team has another open task.",
    )
    brief_queue.set_defaults(func=hooks.cmd_brief_queue)

    brief_list = brief_sub.add_parser(
        "list",
        help="List tasks for a team (default: pending only; --all shows all).",
    )
    brief_list.add_argument("--project", required=True)
    brief_list.add_argument("--team", required=True)
    brief_list.add_argument("--all", action="store_true")
    brief_list.set_defaults(func=hooks.cmd_brief_list)

    brief_claim = brief_sub.add_parser(
        "claim",
        help="Planner claims a pending task (validates depends_on).",
    )
    brief_claim.add_argument("--project", required=True)
    brief_claim.add_argument("--team", required=True)
    brief_claim.add_argument("--task-id", required=True, dest="task_id")
    brief_claim.add_argument(
        "--actor",
        required=True,
        help="Format: <role>@<tool>, e.g. planner@claude",
    )
    brief_claim.set_defaults(func=hooks.cmd_brief_claim)

    brief_reset = brief_sub.add_parser(
        "reset",
        help="Append task_reset for a recoverable brief task.",
    )
    brief_reset.add_argument("--project", required=True)
    brief_reset.add_argument("--team", required=True)
    brief_reset.add_argument("--task-id", required=True, dest="task_id")
    brief_reset.add_argument(
        "--actor",
        default="memory",
        help="Actor for task_reset (default: memory).",
    )
    brief_reset.add_argument(
        "--reason",
        required=True,
        help="Short reset reason for the queue event.",
    )
    brief_reset.set_defaults(func=hooks.cmd_brief_reset)

    brief_requeue = brief_sub.add_parser(
        "requeue",
        help="Recover a blocked/reset task with its existing brief, then wake planner.",
    )
    brief_requeue.add_argument("--project", required=True)
    brief_requeue.add_argument("--team", required=True)
    brief_requeue.add_argument("--task-id", required=True, dest="task_id")
    brief_requeue.add_argument(
        "--actor",
        default="memory",
        help="Actor for recovery events (default: memory).",
    )
    brief_requeue.add_argument(
        "--reason",
        default="requeue after brief repair",
        help="Reset reason if the task is currently waiting_for.",
    )
    brief_requeue.add_argument(
        "--no-wake",
        action="store_true",
        help="Recover the queue event without waking the team planner.",
    )
    brief_requeue.set_defaults(func=hooks.cmd_brief_requeue)

    brief_start = brief_sub.add_parser(
        "start",
        help="Mark a claimed brief task as task_in_progress.",
    )
    brief_start.add_argument("--project", required=True)
    brief_start.add_argument("--team", required=True)
    brief_start.add_argument("--task-id", required=True, dest="task_id")
    brief_start.add_argument(
        "--actor",
        required=True,
        help="Format: <role>@<tool>, e.g. planner@claude",
    )
    brief_start.set_defaults(func=hooks.cmd_brief_start)

    brief_show = brief_sub.add_parser(
        "show",
        help="Show current state (collapsed) of a task_id in the queue.",
    )
    brief_show.add_argument("--project", required=True)
    brief_show.add_argument("--team", required=True)
    brief_show.add_argument("--task-id", required=True, dest="task_id")
    brief_show.set_defaults(func=hooks.cmd_brief_show)

    brief_done = brief_sub.add_parser(
        "done",
        help="Mark a brief task_done PASS after planner final closeout.",
    )
    brief_done.add_argument("--project", required=True)
    brief_done.add_argument("--team", required=True)
    brief_done.add_argument("--task-id", required=True, dest="task_id")
    brief_done.add_argument(
        "--actor",
        required=True,
        help="Format: <role>@<tool>, e.g. planner@claude",
    )
    brief_done.add_argument(
        "--verdict",
        default="PASS",
        choices=["PASS"],
        help="Only PASS is accepted for task_done.",
    )
    brief_done.set_defaults(func=hooks.cmd_brief_done)

    brief_planner_status = brief_sub.add_parser(
        "planner-status",
        help="Print a status snapshot for all planner teams in a project.",
    )
    brief_planner_status.add_argument("--project", required=True)
    brief_planner_status.add_argument(
        "--json", action="store_true", help="Emit JSON instead of plain text"
    )
    brief_planner_status.set_defaults(func=hooks.cmd_brief_planner_status)

    # v3 acceptance executor (Phase 2, spec §4.7)
    acceptance = sub.add_parser(
        "acceptance",
        help="v3 acceptance executor (mechanical / reviewer / operator routes).",
    )
    acceptance_sub = acceptance.add_subparsers(dest="acceptance_command", required=True)

    acceptance_run = acceptance_sub.add_parser(
        "run",
        help="Run brief.acceptance_criteria for a task; physically execute mechanical commands, route reviewer/operator items.",
    )
    acceptance_run.add_argument("--project", required=True)
    acceptance_run.add_argument("--team", required=True)
    acceptance_run.add_argument("--task-id", required=True, dest="task_id")
    acceptance_run.add_argument("--brief-path", default=None, dest="brief_path",
                                help="Explicit brief path (default: tasks/<p>/<t>/brief/<task_id>.md)")
    acceptance_run.add_argument("--reviewer-seat", default=None, dest="reviewer_seat")
    acceptance_run.add_argument("--cwd", default=None, help="Working dir for mechanical commands")
    acceptance_run.add_argument("--profile", default=None, dest="profile",
                                help="Profile path for reviewer dispatch (default: ~/.agents/profiles/<project>-profile-dynamic.toml)")
    acceptance_run.add_argument(
        "--actor",
        default="planner@claude",
        help="Queue actor for automatic task_done on aggregate PASS.",
    )
    acceptance_run.add_argument(
        "--skip-queue-done",
        action="store_true",
        help="Do not append task_done when aggregate acceptance verdict is PASS.",
    )
    acceptance_run.add_argument(
        "--baseline-criteria",
        default=None,
        dest="baseline_criteria",
        help=(
            "Comma-separated 0-based indices of mechanical criteria to treat as "
            "diagnostic/non-blocking baseline evidence. Non-zero exit is recorded as "
            "'diagnostic' and does not contribute to aggregate FAIL."
        ),
    )
    acceptance_run.set_defaults(func=hooks.cmd_acceptance_run)

    identity = sub.add_parser("identity", help="Tool identity list/show operations.")
    identity_sub = identity.add_subparsers(dest="identity_command", required=True)

    identity_list_nested = identity_sub.add_parser("list")
    identity_list_nested.set_defaults(func=hooks.cmd_list_identities)

    identity_show_nested = identity_sub.add_parser("show")
    identity_show_nested.add_argument("identity")
    identity_show_nested.set_defaults(func=hooks.cmd_show_identity)

    tui = sub.add_parser("tui", help="Launch the legacy interactive admin UI.")
    tui.set_defaults(func=hooks.cmd_tui)

    return parser
