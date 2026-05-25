from __future__ import annotations

from dataclasses import is_dataclass, replace
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_admin_config import validate_runtime_combo
from agent_admin_crud_base import (
    CrudHooks,
    _update_profile_seat,
    archive_session_artifacts,
    require_caller_authority,
)
from seat_roles import normalize_seat_role


_OPERATOR_CUSTOM_START = "<!-- OPERATOR-CUSTOM-START -->"
_OPERATOR_CUSTOM_END = "<!-- OPERATOR-CUSTOM-END -->"


def _extract_operator_custom_block(text: str) -> tuple[str, int] | None:
    start = text.find(_OPERATOR_CUSTOM_START)
    if start < 0:
        return None
    end = text.find(_OPERATOR_CUSTOM_END, start + len(_OPERATOR_CUSTOM_START))
    if end < 0:
        return None
    end += len(_OPERATOR_CUSTOM_END)
    return text[start:end], text[:start].count("\n")


def _strip_operator_custom_block(text: str) -> str:
    block = _extract_operator_custom_block(text)
    if block is None:
        return text
    custom_text, _line_no = block
    return text.replace(custom_text, "", 1)


def _inject_operator_custom_block(rendered: str, block: str, line_no: int) -> str:
    cleaned = _strip_operator_custom_block(rendered)
    lines = cleaned.splitlines(keepends=True)
    index = min(max(line_no, 0), len(lines))
    custom_block = block
    if custom_block and not custom_block.endswith("\n"):
        custom_block += "\n"
    if index > 0 and lines and not lines[index - 1].endswith("\n"):
        custom_block = "\n" + custom_block
    lines.insert(index, custom_block)
    return "".join(lines)


def preserve_operator_custom_block(existing: str, rendered: str) -> tuple[str, bool]:
    block = _extract_operator_custom_block(existing)
    if block is None:
        return rendered, False
    custom_text, line_no = block
    return _inject_operator_custom_block(rendered, custom_text, line_no), True


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _dynamic_profile_context(project: Any) -> tuple[list[str], dict[str, dict[str, object]], str] | None:
    try:
        from agent_admin_workspace import _load_dynamic_profile_data  # noqa: PLC0415

        loaded = _load_dynamic_profile_data(str(project.name))
    except Exception as exc:
        print(f"warn: dynamic profile ignored for workspace refresh: {exc}", file=sys.stderr)
        return None
    if loaded is None:
        return None
    _path, data = loaded

    project_seats = [str(seat) for seat in getattr(project, "engineers", []) or []]
    seats = list(project_seats)
    teams = data.get("teams") or {}
    if not seats and isinstance(teams, dict):
        for team_cfg in teams.values():
            if isinstance(team_cfg, dict):
                seats.extend(str(seat) for seat in team_cfg.get("seats") or [])
    mode = data.get("mode") or {}
    if isinstance(mode, dict):
        memory_seat = str(mode.get("project_memory") or "").strip()
        if memory_seat:
            seats.append(memory_seat)

    overrides_raw = data.get("seat_overrides") or {}
    overrides: dict[str, dict[str, object]] = {}
    if isinstance(overrides_raw, dict):
        for seat_id, values in overrides_raw.items():
            if isinstance(values, dict):
                overrides[str(seat_id)] = dict(values)

    template_name = str(data.get("template_name") or "").strip()
    return _dedupe(seats), overrides, template_name


def _project_with_dynamic_profile(project: Any) -> Any:
    context = _dynamic_profile_context(project)
    if context is None:
        return project
    seats, profile_overrides, profile_template_name = context
    if not seats:
        return project

    merged_overrides = {
        **dict(getattr(project, "seat_overrides", None) or {}),
        **profile_overrides,
    }
    updates = {
        "engineers": seats,
        "monitor_engineers": [
            seat
            for seat in getattr(project, "monitor_engineers", []) or []
            if seat in set(seats)
        ],
        "seat_overrides": merged_overrides,
    }
    if profile_template_name:
        updates["template_name"] = profile_template_name

    if is_dataclass(project):
        return replace(project, **updates)
    clone = SimpleNamespace(**vars(project))
    for key, value in updates.items():
        setattr(clone, key, value)
    return clone


class EngineerCrud:
    def __init__(self, hooks: CrudHooks) -> None:
        self.hooks = hooks

    def _require_escalation_authority(self, action: str) -> None:
        require_caller_authority("escalation", action, self.hooks.error_cls)

    def _archive_session_artifacts(self, session: Any) -> None:
        archive_session_artifacts(self.hooks, session)

    def _engineer_template_defaults(self, project: Any, engineer_id: str) -> dict[str, str]:
        template_names = [str(getattr(project, "template_name", "") or ""), "gstack-harness"]
        seen: set[str] = set()
        for template_name in template_names:
            if not template_name or template_name in seen:
                continue
            seen.add(template_name)
            try:
                template = self.hooks.load_template(template_name)
            except Exception:
                continue
            for spec in template.get("engineers", []):
                spec_id = normalize_seat_role(self.hooks.normalize_name(str(spec.get("id", ""))))
                if spec_id == engineer_id:
                    return {
                        "tool": str(spec.get("tool", "") or ""),
                        "mode": str(spec.get("auth_mode", "") or ""),
                        "provider": str(spec.get("provider", "") or ""),
                    }
        return {}

    def engineer_create(self, args: Any) -> int:
        self._require_escalation_authority("engineer create")
        projects = self.hooks.load_projects()
        project = projects[args.project]
        engineer_id = normalize_seat_role(self.hooks.normalize_name(args.engineer))
        if engineer_id == "qa" or engineer_id.startswith("qa-"):
            raise self.hooks.error_cls("qa seat ids were removed 2026-04-29; use patrol")
        defaults = self._engineer_template_defaults(project, engineer_id)
        tool = getattr(args, "tool", None) or defaults.get("tool") or "claude"
        mode = getattr(args, "mode", None) or defaults.get("mode") or "oauth"
        provider = getattr(args, "provider", None) or defaults.get("provider") or "anthropic"
        # Validate the tool/auth_mode/provider triple BEFORE we touch any
        # filesystem state. Historically typos like `anthropix` (vs
        # `anthropic`) silently created engineer profiles + runtime sandbox
        # directories under the wrong identity path, then the seat would
        # start but never get its secret because the secret-file lookup
        # used the typoed provider. The operator's only symptom was a blank
        # pane. Catching this at the argparse boundary gives a clear error.
        validate_runtime_combo(
            tool,
            mode,
            provider,
            error_cls=self.hooks.error_cls,
            context=f"engineer create {args.engineer}",
        )
        if self.hooks.session_path(project.name, engineer_id).exists():
            raise self.hooks.error_cls(f"{engineer_id} already has a session in {project.name}")
        if self.hooks.engineer_path(engineer_id).exists():
            profile = self.hooks.load_engineer(engineer_id)
        else:
            profile = self.hooks.create_engineer_profile(
                engineer_id=engineer_id,
                tool=tool,
                auth_mode=mode,
                provider=provider,
            )
            self.hooks.write_engineer(profile)
        session = self.hooks.create_session_record(
            engineer_id=engineer_id,
            project=project,
            tool=tool,
            auth_mode=mode,
            provider=provider,
            monitor=not args.no_monitor,
        )
        self.hooks.write_session(session)
        self.hooks.apply_template(session, project)
        self.hooks.ensure_dir(Path(session.runtime_dir))
        if session.secret_file:
            self.hooks.write_env_file(Path(session.secret_file), {}, self.hooks.ensure_dir, self.hooks.write_text)
        profile_path = getattr(args, "profile", None)
        if profile_path:
            session_toml = self.hooks.session_path(project.name, engineer_id)
            if not session_toml.exists():
                print(
                    f"warn: session.toml not found at {session_toml}; skipping profile update",
                    file=sys.stderr,
                )
            else:
                try:
                    session_data = self.hooks.load_toml(session_toml)
                    role_val = normalize_seat_role(
                        (getattr(args, "role", None) or "").strip() or engineer_id.split("-")[0]
                    )
                    _update_profile_seat(
                        Path(profile_path),
                        engineer_id,
                        role_val,
                        session_data.get("tool", tool),
                        session_data.get("auth_mode", mode),
                        session_data.get("provider", provider),
                        session_data.get("model"),
                    )
                except Exception as exc:
                    print(f"warn: profile update failed: {exc}", file=sys.stderr)

        print(session.engineer_id)
        return 0

    def engineer_delete(self, args: Any) -> int:
        self._require_escalation_authority("engineer delete")
        project_name = getattr(args, "project", None)
        if project_name:
            session = self.hooks.resolve_engineer_session(args.engineer, project_name=project_name)
            self.hooks.session_service.stop_engineer(session)
            self._archive_session_artifacts(session)
            remaining_sessions = [
                item for item in self.hooks.load_sessions().values() if item.engineer_id == session.engineer_id
            ]
            if not remaining_sessions:
                self.hooks.archive_if_exists(self.hooks.engineer_path(session.engineer_id).parent, "engineers")
            return 0

        engineer = self.hooks.resolve_engineer(args.engineer)
        all_sessions = [
            item for item in self.hooks.load_sessions().values() if item.engineer_id == engineer.engineer_id
        ]
        for session in all_sessions:
            self.hooks.session_service.stop_engineer(session)
            self._archive_session_artifacts(session)
        self.hooks.archive_if_exists(self.hooks.engineer_path(engineer.engineer_id).parent, "engineers")
        return 0

    def engineer_rename(self, args: Any) -> int:
        self._require_escalation_authority("engineer rename")
        old = self.hooks.resolve_engineer(args.old)
        new_id = self.hooks.normalize_name(args.new)
        if self.hooks.engineer_path(new_id).exists():
            raise self.hooks.error_cls(f"{new_id} already exists")

        new_engineer = self.hooks.engineer_cls(
            engineer_id=new_id,
            display_name=new_id,
            aliases=[*old.aliases, old.engineer_id],
            role=old.role,
            role_details=list(old.role_details),
            skills=list(old.skills),
            human_facing=old.human_facing,
            active_loop_owner=old.active_loop_owner,
            dispatch_authority=old.dispatch_authority,
            patrol_authority=old.patrol_authority,
            unblock_authority=old.unblock_authority,
            escalation_authority=old.escalation_authority,
            remind_active_loop_owner=old.remind_active_loop_owner,
            review_authority=old.review_authority,
            design_authority=old.design_authority,
            default_tool=old.default_tool,
            default_auth_mode=old.default_auth_mode,
            default_provider=old.default_provider,
        )
        self.hooks.write_engineer(new_engineer)

        all_sessions = [
            item for item in self.hooks.load_sessions().values() if item.engineer_id == old.engineer_id
        ]
        for old_session in all_sessions:
            new_identity = self.hooks.identity_name(
                old_session.tool,
                old_session.auth_mode,
                old_session.provider,
                new_id,
                old_session.project,
            )
            new_session = self.hooks.session_record_cls(
                engineer_id=new_id,
                project=old_session.project,
                tool=old_session.tool,
                auth_mode=old_session.auth_mode,
                provider=old_session.provider,
                identity=new_identity,
                workspace=str(self.hooks.workspaces_root / old_session.project / new_id),
                runtime_dir=str(
                    self.hooks.runtime_dir_for_identity(
                        old_session.tool,
                        old_session.auth_mode,
                        new_identity,
                    )
                ),
                session=self.hooks.session_name_for(old_session.project, new_id, old_session.tool),
                bin_path=old_session.bin_path,
                monitor=old_session.monitor,
                legacy_sessions=list(old_session.legacy_sessions),
                launch_args=list(old_session.launch_args),
                secret_file="",
                wrapper=old_session.wrapper,
            )
            if old_session.secret_file:
                new_session.secret_file = str(
                    self.hooks.secret_file_for(old_session.tool, old_session.provider, new_id)
                )

            if self.hooks.tmux_has_session(old_session.session):
                subprocess.run(["tmux", "rename-session", "-t", old_session.session, new_session.session], check=True)

            if Path(old_session.workspace).exists():
                self.hooks.ensure_dir(Path(new_session.workspace).parent)
                shutil.move(old_session.workspace, new_session.workspace)
            if Path(old_session.runtime_dir).exists():
                self.hooks.ensure_dir(Path(new_session.runtime_dir).parent)
                shutil.move(old_session.runtime_dir, new_session.runtime_dir)
            if old_session.secret_file and Path(old_session.secret_file).exists():
                self.hooks.ensure_dir(Path(new_session.secret_file).parent)
                shutil.move(old_session.secret_file, new_session.secret_file)
                self.hooks.ensure_secret_permissions(Path(new_session.secret_file))

            self.hooks.write_session(new_session)
            self.hooks.archive_if_exists(self.hooks.session_path(old_session.project, old.engineer_id).parent, "sessions")
            project = self.hooks.load_project(old_session.project)
            project.engineers = [new_id if item == old.engineer_id else item for item in project.engineers]
            project.monitor_engineers = [
                new_id if item == old.engineer_id else item for item in project.monitor_engineers
            ]
            self.hooks.write_project(project)

        shutil.rmtree(self.hooks.engineer_path(old.engineer_id).parent)
        return 0

    def engineer_rebind(self, args: Any) -> int:
        self._require_escalation_authority("engineer rebind")
        session = self.hooks.resolve_engineer_session(args.engineer, project_name=getattr(args, "project", None))
        requested_tool = getattr(args, "tool", None)
        if requested_tool is not None and requested_tool != session.tool:
            print(
                f"error: rebind cannot change tool (current={session.tool}, requested={requested_tool}). "
                f"Use 'engineer delete {args.engineer}' then 'engineer create' with the new tool.",
                file=sys.stderr,
            )
            return 2
        project = self.hooks.load_project(session.project)
        provider = args.provider
        mode = args.mode
        new_identity = self.hooks.identity_name(
            session.tool,
            mode,
            provider,
            session.engineer_id,
            session.project,
        )
        new_runtime = self.hooks.runtime_dir_for_identity(session.tool, mode, new_identity)
        if session.auth_mode == mode and session.provider == provider:
            return 0

        old_runtime = Path(session.runtime_dir)
        if old_runtime.exists():
            self.hooks.archive_if_exists(old_runtime, "runtimes")
        new_secret_file = ""
        if mode == "api":
            new_secret_file = str(self.hooks.secret_file_for(session.tool, provider, session.engineer_id))
            if not Path(new_secret_file).exists():
                self.hooks.write_env_file(Path(new_secret_file), {}, self.hooks.ensure_dir, self.hooks.write_text)
        session.auth_mode = mode
        session.provider = provider
        session.identity = new_identity
        session.runtime_dir = str(new_runtime)
        session.secret_file = new_secret_file
        self.hooks.write_session(session)
        self.hooks.apply_template(session, project)

        profile_path = getattr(args, "profile", None)
        if profile_path:
            session_toml = self.hooks.session_path(session.project, session.engineer_id)
            if not session_toml.exists():
                print(
                    f"warn: session.toml not found at {session_toml}; skipping profile update",
                    file=sys.stderr,
                )
            else:
                try:
                    session_data = self.hooks.load_toml(session_toml)
                    role_val = session.engineer_id.split("-")[0]
                    _update_profile_seat(
                        Path(profile_path),
                        session.engineer_id,
                        role_val,
                        session_data.get("tool", session.tool),
                        session_data.get("auth_mode", mode),
                        session_data.get("provider", provider),
                        session_data.get("model"),
                        rebind=True,
                    )
                except Exception as exc:
                    print(f"warn: profile update failed: {exc}", file=sys.stderr)

        return 0

    def engineer_refresh_workspace(self, args: Any) -> int:
        session = self.hooks.resolve_engineer_session(
            args.engineer,
            project_name=getattr(args, "project", None),
        )
        project = self.hooks.load_project(session.project)
        self.hooks.apply_template(session, project)
        print(f"refreshed\t{session.engineer_id}\t{session.session}\t{session.workspace}")
        return 0

    @staticmethod
    def _workspace_doc_hash(text: str) -> str:
        lines = text.splitlines()
        if lines and lines[0].startswith("<!-- rendered_from_clawseat_sha="):
            lines = lines[1:]
        return sha256(("\n".join(lines).strip() + "\n").encode("utf-8")).hexdigest()

    @staticmethod
    def _confirm_overwrite(path: Path) -> bool:
        response = input(f"{path} has local changes. Overwrite? (Y/n) ").strip().lower()
        return response in {"", "y", "yes"}

    def _regenerate_one_workspace(self, session: Any, project: Any, *, assume_yes: bool = False) -> None:
        workspace = Path(session.workspace)
        rendered = self.hooks.render_template_text(session.tool, session, project)
        workspace_docs = {
            relpath: content
            for relpath, content in rendered.items()
            if relpath in {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}
        }
        final_workspace_docs: dict[str, str] = {}
        preserved_docs: list[Path] = []
        fully_rendered_docs: list[Path] = []
        changed_docs: list[Path] = []
        for relpath, content in workspace_docs.items():
            path = workspace / relpath
            if not path.exists():
                final_workspace_docs[relpath] = content
                continue
            existing = path.read_text(encoding="utf-8")
            final_content, preserved = preserve_operator_custom_block(existing, content)
            final_workspace_docs[relpath] = final_content
            if preserved:
                preserved_docs.append(path)
            else:
                fully_rendered_docs.append(path)
            if self._workspace_doc_hash(existing) != self._workspace_doc_hash(final_content):
                changed_docs.append(path)
        if changed_docs and not assume_yes:
            for path in changed_docs:
                if not self._confirm_overwrite(path):
                    raise self.hooks.error_cls(f"workspace regenerate aborted by operator: {path}")

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup_dir = workspace / f".backup-{timestamp}"
        backed_up = False
        for relpath in workspace_docs:
            path = workspace / relpath
            if not path.exists():
                continue
            target = backup_dir / relpath
            self.hooks.ensure_dir(target.parent)
            shutil.copy2(path, target)
            shutil.copy2(path, path.with_name(f"{path.name}.bak.{timestamp}"))
            backed_up = True
        self.hooks.apply_template(session, project)
        for relpath, content in final_workspace_docs.items():
            path = workspace / relpath
            if path.exists():
                path.write_text(content, encoding="utf-8")
        backup_note = f"\tbackup={backup_dir}" if backed_up else ""
        print(f"regenerated\t{session.engineer_id}\t{session.session}\t{session.workspace}{backup_note}")
        for path in preserved_docs:
            print(f"preserved-operator-custom\t{path}")
        for path in fully_rendered_docs:
            print(f"hint\t{path}\tno OPERATOR-CUSTOM markers found, fully re-rendered")

    def engineer_regenerate_workspace(self, args: Any) -> int:
        self._require_escalation_authority("engineer regenerate-workspace")
        project = _project_with_dynamic_profile(self.hooks.load_project(args.project))
        assume_yes = bool(getattr(args, "yes", False))
        if getattr(args, "all_seats", False):
            if getattr(args, "engineer", None):
                raise self.hooks.error_cls("regenerate-workspace accepts either <seat> or --all-seats, not both")
            sessions = []
            skipped: list[str] = []
            for engineer_id in project.engineers:
                try:
                    sessions.append(self.hooks.resolve_engineer_session(engineer_id, project_name=project.name))
                except self.hooks.error_cls as exc:
                    skipped.append(f"{engineer_id}: {exc}")
                    print(f"skipped\t{engineer_id}\t{exc}", file=sys.stderr)
            if not sessions:
                detail = "; ".join(skipped) if skipped else "project has no seats"
                raise self.hooks.error_cls(f"no regeneratable workspaces in project {project.name}: {detail}")
        else:
            if not getattr(args, "engineer", None):
                raise self.hooks.error_cls("regenerate-workspace requires <seat> or --all-seats")
            sessions = [
                self.hooks.resolve_engineer_session(
                    args.engineer,
                    project_name=project.name,
                )
            ]
        for session in sessions:
            self._regenerate_one_workspace(session, project, assume_yes=assume_yes)
        return 0

    def engineer_secret_set(self, args: Any) -> int:
        self._require_escalation_authority("engineer secret-set")
        session = self.hooks.resolve_engineer_session(args.engineer, project_name=getattr(args, "project", None))
        if session.auth_mode != "api" or not session.secret_file:
            raise self.hooks.error_cls(f"{session.engineer_id} does not use API secrets")
        values = self.hooks.parse_env_file(Path(session.secret_file))
        values[args.key] = args.value
        self.hooks.write_env_file(Path(session.secret_file), values, self.hooks.ensure_dir, self.hooks.write_text)
        return 0
