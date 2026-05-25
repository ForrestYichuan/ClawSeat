#!/usr/bin/env python3
"""Render v3 project.toml from approved config proposals.

Phase 1 minimal multi-mode render path. Called by install.sh --mode multi.

Pipeline:
1. Read tasks/<project>/_config-proposals/*__approved.yaml
2. Validate via proposal_validator
3. Emit v3 project.toml to --output path

The resulting project.toml:
- Stays loader-compatible (top-level `seats`, `[seat_roles]`, flat `[seat_overrides.*]`)
- Adds `[mode]` + `[teams]` metadata for v3 loader (profile_loader_v3.py)

See spec §4.1, §9.2, §16.7 (install-spec-2026-05-13-clawseat-v3-multi-team-protocol.md).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_LIB = _REPO_ROOT / "core" / "lib"
if str(_CORE_LIB) not in sys.path:
    sys.path.insert(0, str(_CORE_LIB))

try:
    import yaml  # type: ignore
except ImportError:
    print("PyYAML required", file=sys.stderr)
    raise SystemExit(1)

from proposal_validator import (  # noqa: E402
    ProposalValidationError,
    assert_all_valid,
    normalize_review_model_fields,
    validate_proposal_dir,
)

PROJECT_MEMORY_SEAT = "memory"
DEFAULT_QUALITY_GATE_DOC = "quality-docs/QUALITY.md"


def _load_yaml_proposal(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end == -1:
            end = text.find("\n---", 4)
        if end != -1:
            text = text[4:end]
    return yaml.safe_load(text)


def _toml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_array(items: list[str]) -> str:
    quoted = [_toml_quote(s) for s in items]
    return "[\n  " + ",\n  ".join(quoted) + ",\n]"


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        parts = [
            f"{str(key)} = {_toml_value(val)}"
            for key, val in value.items()
        ]
        return "{ " + ", ".join(parts) + " }"
    return _toml_quote(str(value))


def _md_value_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _role_label(role: str, override: dict[str, object]) -> str:
    instance = str(override.get("instance") or "").strip()
    if instance:
        return f"{role}-{instance}"
    return role


def render_team_ownership_markdown(project: str, profile_data: dict[str, object]) -> str:
    """Render the current-project TEAM_OWNERSHIP.md summary from v3 profile data.

    This is intentionally descriptive. Runtime decisions still come from the
    approved YAML/profile TOML; the doc gives memory and planners a compact SSOT
    to reread after restart or compaction.
    """
    mode = profile_data.get("mode") if isinstance(profile_data.get("mode"), dict) else {}
    teams = profile_data.get("teams") if isinstance(profile_data.get("teams"), dict) else {}
    seat_roles = profile_data.get("seat_roles") if isinstance(profile_data.get("seat_roles"), dict) else {}
    overrides = profile_data.get("seat_overrides") if isinstance(profile_data.get("seat_overrides"), dict) else {}
    project_memory = str(mode.get("project_memory") or PROJECT_MEMORY_SEAT).strip() or PROJECT_MEMORY_SEAT

    lines: list[str] = [
        f"# {project} Team Ownership",
        "",
        "Generated from the approved v3 team config and dynamic profile.",
        "This file is descriptive only; `project.toml` / approved YAML remain runtime authority.",
        "Dispatch is status-gate-first and capability-second; team ownership is only a same-tier tie-breaker, not a hard lock.",
        "",
        "## project-memory",
        "Mission: project-level memory, intake, stable team context, queue metadata, and cross-team coordination.",
        "Ownership paths:",
        "- project-wide context and routing metadata",
        "Seats:",
        f"- project-memory: `{project_memory}`",
        "Boundaries:",
        "- Does not perform subteam implementation, direct specialist dispatch, or per-task builder assignment.",
        "- Maintains this document after approved roster or ownership changes.",
        "",
    ]

    if not teams:
        lines.extend(
            [
                "## Teams",
                "No v3 teams are declared in the profile.",
                "",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    for team_name, raw_team in teams.items():
        if not isinstance(raw_team, dict):
            continue
        inferred_team_type = (
            "quality-docs"
            if str(team_name) == "quality-docs" or bool(raw_team.get("autonomous"))
            else "subteam"
        )
        team_type = str(raw_team.get("team_type") or inferred_team_type).strip() or inferred_team_type
        seats = _md_value_list(raw_team.get("seats"))
        ownership_paths = _md_value_list(raw_team.get("ownership_paths"))
        review_model = str(raw_team.get("review_model") or "").strip()
        planner_mode = str(raw_team.get("planner_mode") or "").strip()
        notify_policy = str(raw_team.get("notify_policy") or "").strip()
        quality_gate_doc = str(raw_team.get("quality_gate_doc") or "").strip()
        autonomous = bool(raw_team.get("autonomous")) if "autonomous" in raw_team else False
        lines.extend([f"## {team_name}"])
        if team_type == "quality-docs":
            lines.append(
                "Mission: autonomous continuous QA, human-path simulation, chaos/risk testing, evidence, and QA docs."
            )
        else:
            lines.append("Mission: provide domain context for planning and delivery within the declared module/layer boundary.")
        lines.append(f"Team type: `{team_type}`")
        if autonomous:
            lines.append("Autonomy: `true`; planner owns campaign design and patrol scheduling.")
        if review_model:
            lines.append(f"Review model: `{review_model}`")
        if planner_mode:
            lines.append(f"Planner mode: `{planner_mode}`")
        if notify_policy:
            lines.append(f"Notify policy: `{notify_policy}`")
        if quality_gate_doc:
            lines.append(f"Quality gate doc: `{quality_gate_doc}`")
        lines.append("Ownership paths:")
        if ownership_paths:
            lines.extend(f"- `{path}`" for path in ownership_paths)
        else:
            lines.append("- Not declared; planner must ask memory to clarify before broad dispatch.")
        lines.append("Seats:")
        for seat_id in seats:
            role = str(seat_roles.get(seat_id) or "unknown").strip() or "unknown"
            override = overrides.get(seat_id) if isinstance(overrides.get(seat_id), dict) else {}
            label = _role_label(role, override)
            detail_parts: list[str] = []
            purpose = str(override.get("purpose") or "").strip()
            if purpose:
                detail_parts.append(purpose)
            capabilities = _md_value_list(override.get("capabilities"))
            if capabilities:
                detail_parts.append("capabilities: " + ", ".join(f"`{item}`" for item in capabilities))
            detail = "; " + "; ".join(detail_parts) if detail_parts else ""
            lines.append(f"- {label}: `{seat_id}`{detail}")
        lines.append("Boundaries:")
        if team_type == "quality-docs":
            lines.extend(
                [
                    "- Does not edit product code or own implementation fixes.",
                    "- Findings are recorded in QUALITY.md/findings and pulled by memory during acceptance.",
                    "- Patrols run assigned missions and report to `quality-docs-planner`.",
                ]
            )
        else:
            lines.extend(
                [
                    "- Declared paths guide routing but do not override planner-status, model capability, or a warden/operator brief.",
                    "- Per-task builder assignment belongs in that task's `workflow.md`, not in this document.",
                ]
            )
            if review_model == "planner_owned":
                lines.append("- Planner owns review because this lightweight subteam has no dedicated reviewer.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _seat_id_for(team_name: str, seat: dict) -> str:
    role = str(seat["role"]).strip()
    instance = str(seat.get("instance") or "").strip()
    return f"{team_name}-{role}-{instance}" if instance else f"{team_name}-{role}"


def _team_metadata(data: dict, team_name: str) -> dict[str, object]:
    explicit_team_type = str(data.get("team_type") or "").strip()
    inferred_team_type = (
        "quality-docs"
        if team_name == "quality-docs" or bool(data.get("autonomous"))
        else "subteam"
    )
    team_type = explicit_team_type or inferred_team_type
    metadata: dict[str, object] = {"team_type": team_type}
    if team_type == "quality-docs":
        metadata["planner_mode"] = str(data.get("planner_mode") or "quality_campaign").strip()
        metadata["notify_policy"] = str(data.get("notify_policy") or "never_notify_memory").strip()
        metadata["quality_gate_doc"] = str(data.get("quality_gate_doc") or DEFAULT_QUALITY_GATE_DOC).strip()
    else:
        metadata["planner_mode"] = str(data.get("planner_mode") or "delivery").strip()
        metadata["notify_policy"] = str(data.get("notify_policy") or "queue_drained_only").strip()
    review_model, dedicated_reviewer, _ = normalize_review_model_fields(data)
    if review_model:
        metadata["review_model"] = review_model
    if dedicated_reviewer is not None:
        metadata["dedicated_reviewer"] = dedicated_reviewer
    for key in (
        "ownership_paths",
        "scaling_policy",
        "autonomous",
        "loop",
        "stop_rule",
        "quality_gate_doc",
    ):
        if key not in data:
            continue
        if key == "quality_gate_doc" and team_type != "quality-docs":
            continue
        value = data.get(key)
        if key == "autonomous":
            metadata[key] = bool(value)
        elif key == "dedicated_reviewer":
            metadata[key] = bool(value)
        elif key == "ownership_paths" and isinstance(value, list):
            metadata[key] = [str(item).strip() for item in value if str(item).strip()]
        elif key == "scaling_policy" and isinstance(value, dict):
            metadata[key] = dict(value)
        elif str(value or "").strip():
            metadata[key] = str(value).strip()
    return metadata


def render_project_record_toml_v3(profile_text: str) -> str:
    """Render ~/.agents/projects/<project>/project.toml from a v3 profile.

    The dynamic profile is the rich v3 runtime record. The project registry is
    still consumed by older ClawSeat and Cartooner paths, so keep it in the v1
    shape while using the v3 seat roster and overrides. This prevents stale
    flat-template seats from resurfacing as "ungrouped" UI seats after
    reinstall.
    """
    data = _toml_loads(profile_text)
    project = str(data.get("project_name") or "").strip()
    repo_root = str(data.get("repo_root") or "").strip()
    template_name = str(data.get("template_name") or "clawseat-minimal").strip()
    seats = [str(item) for item in data.get("seats", []) if str(item).strip()]
    seat_overrides = data.get("seat_overrides") if isinstance(data.get("seat_overrides"), dict) else {}
    monitor_count = max(1, min(len(seats), 12))

    lines: list[str] = [
        "version = 1",
        f"name = {_toml_quote(project)}",
        f"repo_root = {_toml_quote(repo_root)}",
        f"monitor_session = {_toml_quote(f'project-{project}-monitor')}",
        f"template_name = {_toml_quote(template_name)}",
        'window_mode = "split-2"',
        f"monitor_max_panes = {monitor_count}",
        "open_detail_windows = false",
        f"engineers = {_toml_array(seats)}",
        f"monitor_engineers = {_toml_array(seats)}",
    ]
    for seat_id, override in sorted(seat_overrides.items()):
        if not isinstance(override, dict):
            continue
        lines.extend(["", f"[seat_overrides.{seat_id}]"])
        for key, value in override.items():
            lines.append(f"{key} = {_toml_value(value)}")
    lines.append("")
    return "\n".join(lines)


def render_project_toml_v3(
    project: str,
    proposals_dir: Path,
    repo_root: str | None = None,
    teams_filter: list[str] | None = None,
    template_name: str = "clawseat-engineering",
) -> str:
    """Generate the project.toml text from approved config proposals.

    If teams_filter is given, only those teams are rendered. Unknown team
    names (not present in proposals_dir as <team>__approved.yaml) hard-fail.
    None / empty list ⇒ render all approved teams.

    Caller is expected to have run `assert_all_valid(proposals_dir)` first;
    we re-run it here defensively because rendering a broken toml is worse
    than failing loudly.
    """
    assert_all_valid(proposals_dir)

    all_files = sorted(Path(proposals_dir).glob("*__approved.yaml"))
    if not all_files:
        raise RuntimeError(f"no approved configs in {proposals_dir}")

    available_teams = {f.name.removesuffix("__approved.yaml"): f for f in all_files}

    if teams_filter:
        requested = [t.strip() for t in teams_filter if t.strip()]
        unknown = [t for t in requested if t not in available_teams]
        if unknown:
            raise RuntimeError(
                f"unknown team(s) requested via --teams: {unknown}; "
                f"approved teams available: {sorted(available_teams)}"
            )
        team_files = [available_teams[t] for t in requested]
    else:
        team_files = all_files

    teams: dict[str, dict[str, object]] = {}
    all_seats: list[str] = [PROJECT_MEMORY_SEAT]
    seat_roles: dict[str, str] = {PROJECT_MEMORY_SEAT: "project-memory"}
    seat_overrides: dict[str, dict[str, object]] = {
        PROJECT_MEMORY_SEAT: {
            "tool": "codex",
            "provider": "openai",
            "auth_mode": "oauth",
        }
    }

    for f in team_files:
        data = _load_yaml_proposal(f)
        team_name = str(data.get("team") or "").strip()
        if not team_name:
            raise RuntimeError(f"{f.name}: missing 'team' field")

        # post-review fix #3: cross-field validation
        yaml_project = str(data.get("project") or "").strip()
        if yaml_project and yaml_project != project:
            raise RuntimeError(
                f"{f.name}: project mismatch — yaml says {yaml_project!r}, "
                f"CLI --project says {project!r}; refusing to render"
            )
        filename_team = f.name.removesuffix("__approved.yaml")
        if team_name != filename_team:
            raise RuntimeError(
                f"{f.name}: team field {team_name!r} does not match filename "
                f"team prefix {filename_team!r}; refusing to render"
            )
        team_seat_ids: list[str] = []
        for seat in data.get("seats") or []:
            role = str(seat["role"]).strip()
            instance = str(seat.get("instance") or "").strip()
            seat_id = _seat_id_for(team_name, seat)
            team_seat_ids.append(seat_id)
            all_seats.append(seat_id)
            seat_roles[seat_id] = role
            override: dict[str, object] = {
                "tool": str(seat["tool"]),
                "provider": str(seat["provider"]),
                "auth_mode": str(seat["auth_mode"]),
            }
            if instance:
                override["instance"] = instance
            # Preserve concrete model id from approved config (post-review fix #1).
            # Approved yaml may omit model only for tools where provider implies it.
            if seat.get("model"):
                override["model"] = str(seat["model"])
            if seat.get("purpose"):
                override["purpose"] = str(seat["purpose"])
            caps = seat.get("capabilities")
            if caps:
                override["capabilities"] = list(caps)
            seat_overrides[seat_id] = override
        teams[team_name] = {
            "seats": team_seat_ids,
            "metadata": _team_metadata(data, team_name),
        }

    # Sanity: no duplicate seat ids across teams
    if len(set(all_seats)) != len(all_seats):
        dupes = [s for s in all_seats if all_seats.count(s) > 1]
        raise RuntimeError(f"duplicate seat ids across teams: {sorted(set(dupes))}")

    # Determine paths needed by the runtime harness profile loader (post-retest #1).
    # See core/skills/gstack-harness/scripts/_common/profile.py:240+ for required keys.
    home_env = os.environ.get("CLAWSEAT_REAL_HOME") or os.environ.get("HOME") or str(Path.home())
    clawseat_root = os.environ.get("CLAWSEAT_ROOT") or str(Path(__file__).resolve().parents[2])
    agents_root = f"{home_env}/.agents"
    tasks_root = f"{agents_root}/tasks/{project}"
    workspace_root = f"{agents_root}/workspaces/{project}"
    handoff_dir = f"{tasks_root}/patrol/handoffs"

    lines: list[str] = []
    lines.append(f"# Generated by render_project_toml_v3.py for project {project!r}")
    lines.append(f"# Rendered from {proposals_dir}")
    lines.append("")
    # All top-level scalar/array keys MUST come before any [table] section.
    lines.append(f"profile_name = {_toml_quote(f'{project}-profile-dynamic')}")
    lines.append(f"template_name = {_toml_quote(template_name)}")
    lines.append(f"project_name = {_toml_quote(project)}")
    if repo_root:
        lines.append(f"repo_root = {_toml_quote(repo_root)}")
    else:
        lines.append(f"repo_root = {_toml_quote(clawseat_root)}")
    lines.append(f"tasks_root = {_toml_quote(tasks_root)}")
    lines.append(f"project_doc = {_toml_quote(f'{tasks_root}/project.md')}")
    lines.append(f"tasks_doc = {_toml_quote(f'{tasks_root}/TASKS.md')}")
    lines.append(f"status_doc = {_toml_quote(f'{tasks_root}/STATUS.md')}")
    lines.append(f"send_script = {_toml_quote(f'{clawseat_root}/core/shell-scripts/send-and-verify.sh')}")
    lines.append(f"agent_admin = {_toml_quote(f'{clawseat_root}/core/scripts/agent_admin.py')}")
    lines.append(f"workspace_root = {_toml_quote(workspace_root)}")
    lines.append(f"handoff_dir = {_toml_quote(handoff_dir)}")
    lines.append("")
    lines.append("# Loader-compatible flat seats (read by existing profile.py)")
    lines.append(f"seats = {_toml_array(all_seats)}")
    lines.append("")
    lines.append("# v3 mode + teams metadata (read by core/lib/profile_loader_v3.py)")
    lines.append("[mode]")
    lines.append('team_structure = "multi"')
    lines.append(f"project_memory = {_toml_quote(PROJECT_MEMORY_SEAT)}")
    lines.append("")
    lines.append("[teams]")
    for team_name, team_data in teams.items():
        # Inline table with seats array
        seat_list = ", ".join(_toml_quote(s) for s in team_data["seats"])
        inline_parts = [f"seats = [{seat_list}]"]
        metadata = team_data.get("metadata") or {}
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                inline_parts.append(f"{key} = {_toml_value(value)}")
        lines.append(f"{team_name} = {{ {', '.join(inline_parts)} }}")
    lines.append("")
    lines.append("[seat_roles]")
    for seat_id, role in seat_roles.items():
        lines.append(f"{seat_id} = {_toml_quote(role)}")
    lines.append("")
    for seat_id, override in seat_overrides.items():
        lines.append(f"[seat_overrides.{seat_id}]")
        for key, val in override.items():
            if isinstance(val, list):
                lines.append(f"{key} = {_toml_array([str(x) for x in val])}")
            else:
                lines.append(f"{key} = {_toml_quote(str(val))}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render v3 project.toml from approved config proposals."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--proposals-dir", required=True,
                        help="Path to tasks/<project>/_config-proposals/")
    parser.add_argument("--output", default="-",
                        help="Output path (default '-' for stdout)")
    parser.add_argument("--repo-root", default=None,
                        help="Optional repo_root absolute path to embed in project.toml")
    parser.add_argument("--teams", default=None,
                        help="Comma-separated team filter (default: all approved). "
                             "Unknown teams hard-fail.")
    parser.add_argument(
        "--ownership-output",
        default=None,
        help="Optional path for generated TEAM_OWNERSHIP.md sidecar.",
    )
    parser.add_argument(
        "--project-record-output",
        default=None,
        help="Optional path for ~/.agents/projects/<project>/project.toml registry output.",
    )
    parser.add_argument(
        "--template-name",
        default="clawseat-engineering",
        help="Template name to embed in project.toml (default: clawseat-engineering).",
    )
    args = parser.parse_args(argv)

    teams_filter = None
    if args.teams:
        teams_filter = [t.strip() for t in args.teams.split(",") if t.strip()]

    try:
        toml_text = render_project_toml_v3(
            project=args.project,
            proposals_dir=Path(args.proposals_dir),
            repo_root=args.repo_root,
            teams_filter=teams_filter,
            template_name=args.template_name,
        )
    except ProposalValidationError as exc:
        for v in exc.violations:
            print(f"  ✗ {v}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"render failed: {exc}", file=sys.stderr)
        return 1

    if args.output == "-":
        sys.stdout.write(toml_text)
    else:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(toml_text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    if args.project_record_output:
        project_record_path = Path(args.project_record_output)
        project_record_path.parent.mkdir(parents=True, exist_ok=True)
        project_record_path.write_text(render_project_record_toml_v3(toml_text), encoding="utf-8")
        print(f"wrote {project_record_path}", file=sys.stderr)
    if args.ownership_output:
        profile_data = _toml_loads(toml_text)
        ownership_text = render_team_ownership_markdown(args.project, profile_data)
        ownership_path = Path(args.ownership_output)
        ownership_path.parent.mkdir(parents=True, exist_ok=True)
        ownership_path.write_text(ownership_text, encoding="utf-8")
        print(f"wrote {ownership_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
