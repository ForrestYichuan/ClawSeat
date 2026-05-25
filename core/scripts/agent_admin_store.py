from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import sys

try:
    from seat_claude_template import ensure_seat_claude_template
except ModuleNotFoundError:  # pragma: no cover
    from .seat_claude_template import ensure_seat_claude_template
from seat_roles import normalize_seat_role


@dataclass
class StoreHooks:
    error_cls: type[Exception]
    project_cls: type
    engineer_cls: type
    session_record_cls: type
    projects_root: Path
    engineers_root: Path
    sessions_root: Path
    workspaces_root: Path
    current_project_path: Path
    templates_root: Path
    repo_templates_root: Path
    tool_binaries: dict[str, str]
    default_tool_args: dict[str, list[str]]
    normalize_name: Callable[[str], str]
    ensure_dir: Callable[[Path], None]
    write_text: Callable[[Path, str, int | None], None]
    load_toml: Callable[[Path], dict]
    q: Callable[[str], str]
    q_array: Callable[[list[str]], str]
    identity_name: Callable[..., str]
    runtime_dir_for_identity: Callable[..., Path]
    secret_file_for: Callable[..., Path]
    session_name_for: Callable[..., str]


def _patrol_authority_from(data: dict[str, Any], default: bool = False) -> bool:
    return bool(data.get("patrol_authority", default))


class StoreHandlers:
    def __init__(self, hooks: StoreHooks) -> None:
        self.hooks = hooks

    def default_launch_args_for_tool(self, tool: str) -> list[str]:
        return list(self.hooks.default_tool_args.get(tool, []))

    def project_path(self, project: str) -> Path:
        return self.hooks.projects_root / project / "project.toml"

    def engineer_path(self, engineer_id: str) -> Path:
        return self.hooks.engineers_root / engineer_id / "engineer.toml"

    def session_path(self, project: str, engineer_id: str) -> Path:
        return self.hooks.sessions_root / project / engineer_id / "session.toml"

    def load_project(self, name: str) -> Any:
        data = self.hooks.load_toml(self.project_path(name))
        raw_declared_skills = data.get("declared_skills", [])
        if raw_declared_skills == []:
            declared_skills: list[str] = []
        elif isinstance(raw_declared_skills, list):
            declared_skills = [str(item) for item in raw_declared_skills]
        else:
            print(
                f"warning: declared_skills in {self.hooks.project_path(name)} is not a list; "
                "ignoring and treating as empty",
                file=sys.stderr,
            )
            declared_skills = []
        return self.hooks.project_cls(
            name=data["name"],
            repo_root=data["repo_root"],
            monitor_session=data["monitor_session"],
            engineers=list(data.get("engineers", [])),
            monitor_engineers=list(data.get("monitor_engineers", [])),
            template_name=str(data.get("template_name", "")),
            declared_skills=declared_skills,
            seat_overrides={
                str(seat_id): dict(values)
                for seat_id, values in data.get("seat_overrides", {}).items()
            },
            window_mode=data.get("window_mode", "project-monitor"),
            monitor_max_panes=int(data.get("monitor_max_panes", 4)),
            open_detail_windows=bool(data.get("open_detail_windows", False)),
        )

    def load_projects(self) -> dict[str, Any]:
        projects: dict[str, Any] = {}
        if not self.hooks.projects_root.exists():
            return projects
        for path in sorted(self.hooks.projects_root.glob("*/project.toml")):
            project = self.load_project(path.parent.name)
            projects[project.name] = project
        return projects

    def get_current_project_name(self, projects: dict[str, Any] | None = None) -> str | None:
        project_map = projects or self.load_projects()
        if self.hooks.current_project_path.exists():
            value = self.hooks.current_project_path.read_text().strip()
            if value in project_map:
                return value
        if project_map:
            return sorted(project_map)[0]
        return None

    def set_current_project(self, name: str) -> None:
        self.hooks.ensure_dir(self.hooks.current_project_path.parent)
        self.hooks.write_text(self.hooks.current_project_path, f"{name}\n", None)

    def load_project_or_current(self, name: str | None) -> Any:
        projects = self.load_projects()
        project_name = name or self.get_current_project_name(projects)
        if not project_name:
            raise self.hooks.error_cls("No project configured")
        if project_name in projects:
            return projects[project_name]
        # Fallback: project not in registry but sessions may exist on disk.
        # This happens when switch-harness is called standalone (e.g. from a
        # harness planner seat) without a prior `agent-admin project create`.
        session_dir = self.hooks.sessions_root / project_name
        if session_dir.is_dir():
            engineers: list[str] = []
            repo_root = ""
            for session_toml in sorted(session_dir.glob("*/session.toml")):
                try:
                    data = self.hooks.load_toml(session_toml)
                except Exception:
                    continue
                if data:
                    engineers.append(str(data.get("engineer_id", session_toml.parent.name)))
                    if not repo_root:
                        repo_root = str(data.get("workspace", ""))
            return self.hooks.project_cls(
                name=project_name,
                repo_root=repo_root,
                monitor_session=f"project-{project_name}-monitor",
                engineers=engineers,
                monitor_engineers=engineers[:4],
            )
        raise self.hooks.error_cls(
            f"Unknown project: {project_name}. "
            f"No registered project and no session directory found at {session_dir}. "
            "Run bootstrap_harness.py or `agent-admin project create` to register."
        )

    def load_engineer(self, engineer_id: str) -> Any:
        data = self.hooks.load_toml(self.engineer_path(engineer_id))
        return self.hooks.engineer_cls(
            engineer_id=data["id"],
            display_name=data.get("display_name", data["id"]),
            aliases=list(data.get("aliases", [])),
            role=data.get("role", ""),
            role_details=list(data.get("role_details", [])),
            skills=list(data.get("skills", [])),
            human_facing=bool(data.get("human_facing", False)),
            active_loop_owner=bool(data.get("active_loop_owner", False)),
            dispatch_authority=bool(data.get("dispatch_authority", False)),
            patrol_authority=_patrol_authority_from(data),
            unblock_authority=bool(data.get("unblock_authority", False)),
            escalation_authority=bool(data.get("escalation_authority", False)),
            remind_active_loop_owner=bool(data.get("remind_active_loop_owner", False)),
            review_authority=bool(data.get("review_authority", False)),
            design_authority=bool(data.get("design_authority", False)),
            default_tool=data.get("default_tool", data.get("tool", "")),
            default_auth_mode=data.get("default_auth_mode", data.get("auth_mode", "")),
            default_provider=data.get("default_provider", data.get("provider", "")),
        )

    def load_engineers(self) -> dict[str, Any]:
        engineers: dict[str, Any] = {}
        if not self.hooks.engineers_root.exists():
            return engineers
        for path in sorted(self.hooks.engineers_root.glob("*/engineer.toml")):
            engineer = self.load_engineer(path.parent.name)
            engineers[engineer.engineer_id] = engineer
        return engineers

    def load_session(self, project: str, engineer_id: str) -> Any:
        data = self.hooks.load_toml(self.session_path(project, engineer_id))
        return self.hooks.session_record_cls(
            engineer_id=data["engineer_id"],
            project=data["project"],
            tool=data["tool"],
            auth_mode=data["auth_mode"],
            provider=data["provider"],
            identity=data["identity"],
            workspace=data["workspace"],
            runtime_dir=data["runtime_dir"],
            session=data["session"],
            bin_path=data.get("bin_path", self.hooks.tool_binaries.get(data["tool"], "")),
            monitor=bool(data.get("monitor", True)),
            legacy_sessions=list(data.get("legacy_sessions", [])),
            launch_args=list(data.get("launch_args", [])),
            secret_file=data.get("secret_file", ""),
            wrapper=data.get("wrapper", ""),
        )

    def load_sessions(self) -> dict[tuple[str, str], Any]:
        sessions: dict[tuple[str, str], Any] = {}
        if not self.hooks.sessions_root.exists():
            return sessions
        for path in sorted(self.hooks.sessions_root.glob("*/*/session.toml")):
            session = self.load_session(path.parent.parent.name, path.parent.name)
            sessions[(session.project, session.engineer_id)] = session
        return sessions

    def load_project_sessions(self, project: str) -> dict[str, Any]:
        return {
            engineer_id: session
            for (project_name, engineer_id), session in self.load_sessions().items()
            if project_name == project
        }

    def load_template(self, name_or_path: str) -> dict:
        candidate = Path(name_or_path).expanduser()
        if candidate.exists():
            if candidate.is_dir():
                candidate = candidate / "template.toml"
        else:
            candidates = [
                self.hooks.templates_root / name_or_path / "template.toml",
                self.hooks.repo_templates_root / name_or_path,
                self.hooks.repo_templates_root / f"{name_or_path}.toml",
                self.hooks.repo_templates_root / name_or_path / "template.toml",
            ]
            candidate = next((path for path in candidates if path.exists()), candidates[0])
        if not candidate.exists():
            raise self.hooks.error_cls(f"Template not found: {name_or_path}")
        data = self.hooks.load_toml(candidate)
        if not isinstance(data.get("engineers"), list):
            raise self.hooks.error_cls(f"Template {candidate} is missing [[engineers]] entries")
        return data

    def merge_template_local(self, template: dict, local: dict) -> dict:
        project_name_raw = str(local.get("project_name", "")).strip()
        repo_root_raw = str(local.get("repo_root", "")).strip()
        if not project_name_raw:
            raise self.hooks.error_cls("local.toml is missing required field: project_name")
        if not repo_root_raw:
            raise self.hooks.error_cls("local.toml is missing required field: repo_root")

        project_name = self.hooks.normalize_name(project_name_raw)
        repo_root = str(Path(repo_root_raw).expanduser())
        defaults = dict(template.get("defaults", {}))

        template_engineers: list[dict] = []
        seen_ids: set[str] = set()
        for engineer in template.get("engineers", []):
            engineer_id = normalize_seat_role(self.hooks.normalize_name(str(engineer.get("id", ""))))
            if engineer_id in seen_ids:
                raise self.hooks.error_cls(f"Template declares duplicate engineer id: {engineer_id}")
            seen_ids.add(engineer_id)
            merged_engineer = dict(engineer)
            merged_engineer["id"] = engineer_id
            if str(merged_engineer.get("role", "")).strip():
                merged_engineer["role"] = normalize_seat_role(str(merged_engineer["role"]))
            template_engineers.append(merged_engineer)

        override_map: dict[str, dict] = {}
        for override in local.get("overrides", []):
            engineer_id = normalize_seat_role(self.hooks.normalize_name(str(override.get("id", ""))))
            if engineer_id not in seen_ids:
                raise self.hooks.error_cls(f"Override references unknown engineer id: {engineer_id}")
            if engineer_id in override_map:
                raise self.hooks.error_cls(f"Duplicate override for engineer id: {engineer_id}")
            override_map[engineer_id] = dict(override)

        final_engineers: list[dict] = []
        for engineer in template_engineers:
            engineer_id = engineer["id"]
            merged_engineer = dict(engineer)
            override = override_map.get(engineer_id)
            if override:
                merged_engineer.update(override)
                merged_engineer["id"] = engineer_id
            if merged_engineer.get("active", True) is False:
                continue
            final_engineers.append(merged_engineer)

        seat_order_raw = [
            normalize_seat_role(self.hooks.normalize_name(str(item)))
            for item in local.get("seat_order", [])
            if str(item).strip()
        ]
        materialized_seats_raw = [
            normalize_seat_role(self.hooks.normalize_name(str(item)))
            for item in local.get("materialized_seats", [])
            if str(item).strip()
        ]
        runtime_seats_raw = [
            normalize_seat_role(self.hooks.normalize_name(str(item)))
            for item in local.get("runtime_seats", [])
            if str(item).strip()
        ]
        bootstrap_seats_raw = [
            normalize_seat_role(self.hooks.normalize_name(str(item)))
            for item in local.get("bootstrap_seats", [])
            if str(item).strip()
        ]
        available_ids = {str(engineer["id"]) for engineer in final_engineers}
        if bootstrap_seats_raw:
            unknown = [seat_id for seat_id in bootstrap_seats_raw if seat_id not in available_ids]
            if unknown:
                raise self.hooks.error_cls(
                    f"bootstrap_seats references unknown/disabled engineer ids: {', '.join(unknown)}"
                )
        if materialized_seats_raw:
            unknown = [seat_id for seat_id in materialized_seats_raw if seat_id not in available_ids]
            if unknown:
                raise self.hooks.error_cls(
                    f"materialized_seats references unknown/disabled engineer ids: {', '.join(unknown)}"
                )
        if runtime_seats_raw:
            unknown = [seat_id for seat_id in runtime_seats_raw if seat_id not in available_ids]
            if unknown:
                raise self.hooks.error_cls(
                    f"runtime_seats references unknown/disabled engineer ids: {', '.join(unknown)}"
                )
            runtime_set = set(runtime_seats_raw)
            final_engineers = [
                engineer
                for engineer in final_engineers
                if str(engineer["id"]) in runtime_set
            ]
        elif materialized_seats_raw:
            materialized_set = set(materialized_seats_raw)
            final_engineers = [
                engineer
                for engineer in final_engineers
                if str(engineer["id"]) in materialized_set
            ]
        if seat_order_raw:
            unknown = [seat_id for seat_id in seat_order_raw if seat_id not in available_ids]
            if unknown:
                raise self.hooks.error_cls(
                    f"seat_order references unknown/disabled engineer ids: {', '.join(unknown)}"
                )
            order_index = {seat_id: index for index, seat_id in enumerate(seat_order_raw)}
            final_engineers.sort(
                key=lambda engineer: (
                    order_index.get(str(engineer["id"]), len(order_index)),
                    str(engineer["id"]),
                )
            )

        return {
            "project_name": project_name,
            "repo_root": repo_root,
            "window_mode": defaults.get("window_mode", "tabs-1up"),
            "monitor_max_panes": int(defaults.get("monitor_max_panes", 4)),
            "open_detail_windows": bool(defaults.get("open_detail_windows", False)),
            "engineers": final_engineers,
            "declared_skills": list(defaults.get("declared_skills", [])),
            "optional_skills": list(template.get("optional_skills", [])),
        }

    def write_project(self, project: Any) -> None:
        path = self.project_path(project.name)
        self.hooks.ensure_dir(path.parent)
        lines = [
            "version = 1",
            f"name = {self.hooks.q(project.name)}",
            f"repo_root = {self.hooks.q(project.repo_root)}",
            f"monitor_session = {self.hooks.q(project.monitor_session)}",
        ]
        if project.template_name:
            lines.append(f"template_name = {self.hooks.q(project.template_name)}")
        lines.extend(
            [
                f"window_mode = {self.hooks.q(project.window_mode)}",
                f"monitor_max_panes = {project.monitor_max_panes}",
                f"open_detail_windows = {'true' if project.open_detail_windows else 'false'}",
                f"engineers = {self.hooks.q_array(project.engineers)}",
                f"monitor_engineers = {self.hooks.q_array(project.monitor_engineers)}",
            ]
        )
        if project.declared_skills:
            lines.append(f"declared_skills = {self.hooks.q_array(project.declared_skills)}")
        for seat_id, override in sorted((project.seat_overrides or {}).items()):
            lines.extend(["", f"[seat_overrides.{seat_id}]"])
            for key, value in override.items():
                if isinstance(value, bool):
                    rendered = "true" if value else "false"
                elif isinstance(value, int):
                    rendered = str(value)
                elif isinstance(value, list):
                    rendered = self.hooks.q_array([str(item) for item in value])
                else:
                    rendered = self.hooks.q(str(value))
                lines.append(f"{key} = {rendered}")
        lines.append("")
        self.hooks.write_text(path, "\n".join(lines), None)

    def write_engineer(self, engineer: Any) -> None:
        path = self.engineer_path(engineer.engineer_id)
        self.hooks.ensure_dir(path.parent)
        lines = [
            "version = 1",
            f"id = {self.hooks.q(engineer.engineer_id)}",
            f"display_name = {self.hooks.q(engineer.display_name)}",
            f"aliases = {self.hooks.q_array(engineer.aliases)}",
            f"role = {self.hooks.q(engineer.role)}",
        ]
        if engineer.role_details:
            lines.append(f"role_details = {self.hooks.q_array(engineer.role_details)}")
        lines.extend(
            [
                f"skills = {self.hooks.q_array(engineer.skills)}",
                f"human_facing = {'true' if engineer.human_facing else 'false'}",
                f"active_loop_owner = {'true' if engineer.active_loop_owner else 'false'}",
                f"dispatch_authority = {'true' if engineer.dispatch_authority else 'false'}",
                f"patrol_authority = {'true' if engineer.patrol_authority else 'false'}",
                f"unblock_authority = {'true' if engineer.unblock_authority else 'false'}",
                f"escalation_authority = {'true' if engineer.escalation_authority else 'false'}",
                f"remind_active_loop_owner = {'true' if engineer.remind_active_loop_owner else 'false'}",
                f"review_authority = {'true' if engineer.review_authority else 'false'}",
                f"design_authority = {'true' if engineer.design_authority else 'false'}",
                f"default_tool = {self.hooks.q(engineer.default_tool)}",
                f"default_auth_mode = {self.hooks.q(engineer.default_auth_mode)}",
                f"default_provider = {self.hooks.q(engineer.default_provider)}",
            ]
        )
        lines.append("")
        self.hooks.write_text(path, "\n".join(lines), None)
        try:
            ensure_seat_claude_template(
                self.hooks.engineers_root,
                engineer.engineer_id,
                role_hint=engineer.role or None,
            )
        except Exception as exc:
            raise self.hooks.error_cls(
                f"failed to prepare Claude template for {engineer.engineer_id}: {exc}"
            ) from exc

    def write_session(self, session: Any) -> None:
        path = self.session_path(session.project, session.engineer_id)
        self.hooks.ensure_dir(path.parent)
        lines = [
            "version = 1",
            f"project = {self.hooks.q(session.project)}",
            f"engineer_id = {self.hooks.q(session.engineer_id)}",
            f"tool = {self.hooks.q(session.tool)}",
            f"auth_mode = {self.hooks.q(session.auth_mode)}",
            f"provider = {self.hooks.q(session.provider)}",
            f"identity = {self.hooks.q(session.identity)}",
            f"workspace = {self.hooks.q(session.workspace)}",
            f"runtime_dir = {self.hooks.q(session.runtime_dir)}",
            f"session = {self.hooks.q(session.session)}",
            f"bin_path = {self.hooks.q(session.bin_path)}",
            f"monitor = {'true' if session.monitor else 'false'}",
            f"legacy_sessions = {self.hooks.q_array(session.legacy_sessions)}",
            f"launch_args = {self.hooks.q_array(session.launch_args)}",
        ]
        if session.secret_file:
            lines.append(f"secret_file = {self.hooks.q(session.secret_file)}")
        if session.wrapper:
            lines.append(f"wrapper = {self.hooks.q(session.wrapper)}")
        lines.append("")
        self.hooks.write_text(path, "\n".join(lines), None)

    def create_engineer_profile(
        self,
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
    ) -> Any:
        engineer_id = normalize_seat_role(self.hooks.normalize_name(engineer_id))
        return self.hooks.engineer_cls(
            engineer_id=engineer_id,
            display_name=display_name or engineer_id,
            aliases=list(aliases or []),
            role=normalize_seat_role(role) or engineer_id,
            role_details=list(role_details or []),
            skills=list(skills or []),
            human_facing=human_facing,
            active_loop_owner=active_loop_owner,
            dispatch_authority=dispatch_authority,
            patrol_authority=patrol_authority,
            unblock_authority=unblock_authority,
            escalation_authority=escalation_authority,
            remind_active_loop_owner=remind_active_loop_owner,
            review_authority=review_authority,
            design_authority=design_authority,
            default_tool=tool,
            default_auth_mode=auth_mode,
            default_provider=provider,
        )

    def merge_engineer_profile_with_template(self, profile: Any, engineer_spec: dict) -> Any:
        role = str(engineer_spec.get("role", profile.role)).strip()
        display_name = str(engineer_spec.get("display_name", profile.display_name)).strip() or profile.display_name
        aliases = list(engineer_spec.get("aliases", profile.aliases))
        role_details = list(engineer_spec.get("role_details", profile.role_details))
        skills = list(engineer_spec.get("skills", profile.skills))
        default_tool = str(engineer_spec.get("tool", profile.default_tool)).strip() or profile.default_tool
        default_auth_mode = (
            str(engineer_spec.get("auth_mode", profile.default_auth_mode)).strip()
            or profile.default_auth_mode
        )
        default_provider = (
            str(engineer_spec.get("provider", profile.default_provider)).strip()
            or profile.default_provider
        )
        return self.hooks.engineer_cls(
            engineer_id=profile.engineer_id,
            display_name=display_name,
            aliases=aliases,
            role=role,
            role_details=role_details,
            skills=skills,
            human_facing=bool(engineer_spec.get("human_facing", profile.human_facing)),
            active_loop_owner=bool(engineer_spec.get("active_loop_owner", profile.active_loop_owner)),
            dispatch_authority=bool(engineer_spec.get("dispatch_authority", profile.dispatch_authority)),
            patrol_authority=_patrol_authority_from(engineer_spec, profile.patrol_authority),
            unblock_authority=bool(engineer_spec.get("unblock_authority", profile.unblock_authority)),
            escalation_authority=bool(engineer_spec.get("escalation_authority", profile.escalation_authority)),
            remind_active_loop_owner=bool(
                engineer_spec.get("remind_active_loop_owner", profile.remind_active_loop_owner)
            ),
            review_authority=bool(engineer_spec.get("review_authority", profile.review_authority)),
            design_authority=bool(engineer_spec.get("design_authority", profile.design_authority)),
            default_tool=default_tool,
            default_auth_mode=default_auth_mode,
            default_provider=default_provider,
        )

    def create_session_record(
        self,
        engineer_id: str,
        project: Any,
        tool: str,
        auth_mode: str,
        provider: str,
        monitor: bool = True,
        session_name: str = "",
        legacy_session: str = "",
        launch_args: list[str] | None = None,
        wrapper: str = "",
    ) -> Any:
        engineer_id = normalize_seat_role(self.hooks.normalize_name(engineer_id))
        identity = self.hooks.identity_name(tool, auth_mode, provider, engineer_id, project.name)
        workspace = self.hooks.workspaces_root / project.name / engineer_id
        runtime_dir = self.hooks.runtime_dir_for_identity(tool, auth_mode, identity)
        secret_file = ""
        if auth_mode == "api":
            secret_file = str(self.hooks.secret_file_for(tool, provider, engineer_id))
        resolved_launch_args = (
            list(launch_args)
            if launch_args is not None
            else self.default_launch_args_for_tool(tool)
        )
        canonical_session = self.hooks.session_name_for(project.name, engineer_id, tool)
        effective_session = session_name or canonical_session
        legacy_sessions = []
        if legacy_session:
            legacy_sessions.append(legacy_session)
        if effective_session != canonical_session and canonical_session not in legacy_sessions:
            legacy_sessions.append(canonical_session)
        return self.hooks.session_record_cls(
            engineer_id=engineer_id,
            project=project.name,
            tool=tool,
            auth_mode=auth_mode,
            provider=provider,
            identity=identity,
            workspace=str(workspace),
            runtime_dir=str(runtime_dir),
            session=effective_session,
            bin_path=self.hooks.tool_binaries[tool],
            monitor=monitor,
            legacy_sessions=legacy_sessions,
            launch_args=resolved_launch_args,
            secret_file=secret_file,
            wrapper=wrapper,
        )

    def project_template_context(
        self,
        project: Any,
    ) -> tuple[dict[str, Any], list[str], list[dict[str, object]]] | None:
        if not project.template_name:
            return None
        try:
            template = self.load_template(project.template_name)
        except self.hooks.error_cls:
            return None
        template_ids = {
            self.hooks.normalize_name(str(spec.get("id", "")))
            for spec in template.get("engineers", [])
            if str(spec.get("id", "")).strip()
        }
        project_ids = [str(item) for item in getattr(project, "engineers", []) or []]
        if project_ids and set(project_ids) != template_ids:
            optional_skills = list(template.get("optional_skills", []))
            project_profiles: dict[str, Any] = {}
            for engineer_id in project_ids:
                if self.engineer_path(engineer_id).exists():
                    base_profile = self.load_engineer(engineer_id)
                else:
                    override = dict((project.seat_overrides or {}).get(engineer_id, {}))
                    base_profile = self.create_engineer_profile(
                        engineer_id=engineer_id,
                        tool=str(override.get("tool", "") or "claude"),
                        auth_mode=str(override.get("auth_mode", "") or "oauth"),
                        provider=str(override.get("provider", "") or "anthropic"),
                        role=str(override.get("role", "") or engineer_id),
                        display_name=engineer_id,
                    )
                override = dict((project.seat_overrides or {}).get(engineer_id, {}))
                if override:
                    project_profiles[engineer_id] = self.merge_engineer_profile_with_template(
                        base_profile,
                        {
                            "id": engineer_id,
                            "tool": override.get("tool", base_profile.default_tool),
                            "auth_mode": override.get("auth_mode", base_profile.default_auth_mode),
                            "provider": override.get("provider", base_profile.default_provider),
                            "role": override.get("role", base_profile.role),
                            "display_name": override.get("display_name", base_profile.display_name),
                            "role_details": override.get("role_details", base_profile.role_details),
                            "skills": override.get("skills", base_profile.skills),
                            "aliases": override.get("aliases", base_profile.aliases),
                            "human_facing": override.get("human_facing", base_profile.human_facing),
                            "active_loop_owner": override.get("active_loop_owner", base_profile.active_loop_owner),
                            "dispatch_authority": override.get("dispatch_authority", base_profile.dispatch_authority),
                            "patrol_authority": override.get("patrol_authority", base_profile.patrol_authority),
                            "unblock_authority": override.get("unblock_authority", base_profile.unblock_authority),
                            "escalation_authority": override.get("escalation_authority", base_profile.escalation_authority),
                            "remind_active_loop_owner": override.get("remind_active_loop_owner", base_profile.remind_active_loop_owner),
                            "review_authority": override.get("review_authority", base_profile.review_authority),
                            "design_authority": override.get("design_authority", base_profile.design_authority),
                        },
                    )
                else:
                    project_profiles[engineer_id] = base_profile
            return project_profiles, project_ids, optional_skills
        merged = self.merge_template_local(
            template,
            {
                "project_name": project.name,
                "repo_root": project.repo_root,
                "overrides": [
                    {"id": seat_id, **dict(values)}
                    for seat_id, values in (project.seat_overrides or {}).items()
                ],
            },
        )
        template_profiles: dict[str, Any] = {}
        engineer_order: list[str] = []
        for engineer_spec in merged["engineers"]:
            engineer_id = self.hooks.normalize_name(str(engineer_spec["id"]))
            engineer_order.append(engineer_id)
            if self.engineer_path(engineer_id).exists():
                base_profile = self.load_engineer(engineer_id)
            else:
                role = str(engineer_spec.get("role", "")).strip()
                base_profile = self.create_engineer_profile(
                    engineer_id=engineer_id,
                    tool=str(engineer_spec["tool"]),
                    auth_mode=str(engineer_spec["auth_mode"]),
                    provider=str(engineer_spec["provider"]),
                    role=role,
                    display_name=str(engineer_spec.get("display_name", "")).strip() or role or engineer_id,
                    role_details=list(engineer_spec.get("role_details", [])),
                    skills=list(engineer_spec.get("skills", [])),
                    aliases=list(engineer_spec.get("aliases", [])),
                    human_facing=bool(engineer_spec.get("human_facing", False)),
                    active_loop_owner=bool(engineer_spec.get("active_loop_owner", False)),
                    dispatch_authority=bool(engineer_spec.get("dispatch_authority", False)),
                    patrol_authority=_patrol_authority_from(engineer_spec),
                    unblock_authority=bool(engineer_spec.get("unblock_authority", False)),
                    escalation_authority=bool(engineer_spec.get("escalation_authority", False)),
                    remind_active_loop_owner=bool(engineer_spec.get("remind_active_loop_owner", False)),
                    review_authority=bool(engineer_spec.get("review_authority", False)),
                    design_authority=bool(engineer_spec.get("design_authority", False)),
                )
            template_profiles[engineer_id] = self.merge_engineer_profile_with_template(
                base_profile,
                engineer_spec,
            )
        return template_profiles, engineer_order, list(merged.get("optional_skills", []))
