from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent_admin_config import AUTH_MODES_REQUIRING_SECRET_FILE, validate_runtime_combo


@dataclass
class SwitchHooks:
    error_cls: type[Exception]
    legacy_secrets_root: Path
    tool_binaries: dict[str, str]
    default_tool_args: dict[str, list[str]]
    identity_name: Callable[..., str]
    runtime_dir_for_identity: Callable[..., Path]
    secret_file_for: Callable[..., Path]
    session_name_for: Callable[..., str]
    ensure_dir: Callable[[Path], None]
    ensure_secret_permissions: Callable[[Path], None]
    write_env_file: Callable[..., None]
    parse_env_file: Callable[[Path], dict[str, str]]
    load_project: Callable[[str], Any]
    load_project_or_current: Callable[[str | None], Any]
    load_session: Callable[[str, str], Any]
    write_session: Callable[[Any], None]
    apply_template: Callable[[Any, Any], None]
    session_stop_engineer: Callable[[Any], None]
    session_record_cls: type
    normalize_name: Callable[[str], str]
    engineer_path: Callable[[str], Path] | None = None
    load_engineer: Callable[[str], Any] | None = None
    write_engineer: Callable[[Any], None] | None = None
    create_engineer_profile: Callable[..., Any] | None = None
    create_session_record: Callable[..., Any] | None = None


class SwitchHandlers:
    def __init__(self, hooks: SwitchHooks) -> None:
        self.hooks = hooks

    def _provider_secret_file(self, session: Any) -> Path | None:
        try:
            from providers import ProviderError, get_provider
        except Exception:
            return None
        try:
            provider = get_provider(session.provider, home=self.operator_home())
        except ProviderError as exc:
            raise self.hooks.error_cls(str(exc)) from exc
        if provider is None:
            return None
        return Path(provider.secret_file)

    def default_launch_args_for_tool(self, tool: str) -> list[str]:
        return list(self.hooks.default_tool_args.get(tool, []))

    def operator_home(self) -> Path:
        return self.hooks.legacy_secrets_root.parent.parent

    def shared_secret_candidates(self, session: Any) -> list[Path]:
        operator_home = self.operator_home()
        candidates: list[Path] = []
        if session.tool == "claude" and session.auth_mode == "oauth_token":
            candidates.append(operator_home / ".agents" / ".env.global")
        if session.tool == "claude" and session.provider == "anthropic-console":
            candidates.append(
                operator_home / ".agents" / "secrets" / "claude" / "anthropic-console.env"
            )
        if session.tool == "claude" and session.provider == "minimax":
            candidates.append(self.hooks.legacy_secrets_root / "claude" / "minimax.env")
        if session.tool == "claude" and session.provider == "deepseek":
            candidates.append(self.hooks.legacy_secrets_root / "claude" / "deepseek.env")
            candidates.append(
                operator_home
                / ".local"
                / "share"
                / "agent-launcher"
                / "secrets"
                / "claude"
                / "deepseek.env"
            )
        if session.tool == "claude" and session.provider == "ark":
            candidates.append(self.hooks.legacy_secrets_root / "claude" / "ark.env")
        if session.tool == "claude" and session.provider == "xcode-best":
            candidates.append(self.hooks.legacy_secrets_root / "claude" / "xcode.env")
        if session.tool == "codex" and session.provider == "xcode-best":
            candidates.append(self.hooks.legacy_secrets_root / "codex" / "xcode.env")
        if session.tool == "gemini" and session.provider == "google-api-key":
            candidates.append(self.hooks.legacy_secrets_root / "gemini" / "primary.env")
        return candidates

    def ensure_secret_ready(self, session: Any) -> None:
        if session.auth_mode not in AUTH_MODES_REQUIRING_SECRET_FILE:
            return
        provider_secret_path = self._provider_secret_file(session)
        secret_path = provider_secret_path or (Path(session.secret_file) if session.secret_file else None)
        if secret_path is None:
            return
        if not secret_path.exists() or not secret_path.read_text().strip():
            self.hooks.ensure_dir(secret_path.parent)
            source_candidates: list[Path] = []
            if session.secret_file:
                legacy_secret = Path(session.secret_file)
                if legacy_secret != secret_path and legacy_secret.exists() and legacy_secret.read_text().strip():
                    source_candidates.append(legacy_secret)
            for peer in sorted(secret_path.parent.glob("*.env")):
                if peer == secret_path or not peer.read_text().strip():
                    continue
                source_candidates.append(peer)
            for shared_secret in self.shared_secret_candidates(session):
                if not shared_secret.exists() or not shared_secret.read_text().strip():
                    continue
                source_candidates.append(shared_secret)
            for source in source_candidates:
                if source == secret_path or not source.exists() or not source.read_text().strip():
                    continue
                shutil.copy2(source, secret_path)
                self.hooks.ensure_secret_permissions(secret_path)
                break
        if not secret_path.exists():
            raise self.hooks.error_cls(
                f"Abort: missing secret file for {session.engineer_id}: {secret_path}. "
                f"Provision the secret before switching to {session.tool}/{session.provider} "
                f"{session.auth_mode} auth."
            )
        if not secret_path.read_text().strip():
            raise self.hooks.error_cls(
                f"Abort: secret file is empty for {session.engineer_id}: {secret_path}. "
                f"Provision the secret before switching to {session.tool}/{session.provider} "
                f"{session.auth_mode} auth."
            )

    def expected_identity_for_session(self, session: Any) -> str:
        return self.hooks.identity_name(
            session.tool,
            session.auth_mode,
            session.provider,
            session.engineer_id,
            session.project,
        )

    def reconcile_session_runtime(self, session: Any) -> Any:
        expected_identity = self.expected_identity_for_session(session)
        expected_runtime = self.hooks.runtime_dir_for_identity(
            session.tool,
            session.auth_mode,
            expected_identity,
        )
        expected_bin_path = self.hooks.tool_binaries[session.tool]
        expected_launch_args = self.default_launch_args_for_tool(session.tool)
        if (
            session.identity == expected_identity
            and session.runtime_dir == str(expected_runtime)
            and session.bin_path == expected_bin_path
            and session.launch_args == expected_launch_args
        ):
            return session
        session.identity = expected_identity
        session.runtime_dir = str(expected_runtime)
        session.bin_path = expected_bin_path
        session.launch_args = expected_launch_args
        self.hooks.write_session(session)
        self.hooks.apply_template(session, self.hooks.load_project(session.project))
        return session

    def _registry_secret_file(self, provider_name: str) -> str:
        """Return provider registry secret_file for provider_name, or empty string.

        Prefers the registry's flat per-provider path over the generated
        per-engineer nested path from secret_file_for().  Silently falls back
        when the registry or provider is unavailable.
        """
        try:
            from providers import get_provider as _gp
            p = _gp(provider_name, home=self.operator_home())
            if p is not None and getattr(p, "secret_file", ""):
                return str(p.secret_file)
        except Exception:  # noqa: BLE001
            pass
        return ""

    def build_switched_session(
        self,
        old_session: Any,
        project: Any,
        tool: str,
        auth_mode: str,
        provider: str,
        model: str = "",
    ) -> Any:
        engineer_id = old_session.engineer_id
        identity = self.hooks.identity_name(tool, auth_mode, provider, engineer_id, project.name)
        if auth_mode in AUTH_MODES_REQUIRING_SECRET_FILE:
            # Prefer the provider registry's flat per-provider secret path so that
            # seats sharing a provider (e.g. baidu-glm) use the one file that
            # the operator actually populated.  Fall back to the generated
            # per-engineer nested path only when the registry has no entry.
            secret_file = (
                self._registry_secret_file(provider)
                or str(self.hooks.secret_file_for(tool, provider, engineer_id))
            )
        else:
            secret_file = ""
        session = self.hooks.session_record_cls(
            engineer_id=engineer_id,
            project=project.name,
            tool=tool,
            auth_mode=auth_mode,
            provider=provider,
            identity=identity,
            workspace=old_session.workspace,
            runtime_dir=str(self.hooks.runtime_dir_for_identity(tool, auth_mode, identity)),
            session=self.hooks.session_name_for(project.name, engineer_id, tool),
            bin_path=self.hooks.tool_binaries[tool],
            monitor=old_session.monitor,
            legacy_sessions=list(old_session.legacy_sessions),
            launch_args=self.default_launch_args_for_tool(tool),
            secret_file=secret_file,
            wrapper=old_session.wrapper,
        )
        session._template_model = model.strip()
        return session

    def create_declared_session_for_switch(self, project: Any, args: Any, model: str = "") -> Any:
        engineer_id = self.hooks.normalize_name(args.engineer)
        project_engineers = list(getattr(project, "engineers", []) or [])
        if project_engineers and engineer_id not in project_engineers:
            raise self.hooks.error_cls(
                f"{engineer_id} has no session in project {project.name}; add the seat before switching harness"
            )
        required_hooks = {
            "engineer_path": getattr(self.hooks, "engineer_path", None),
            "load_engineer": getattr(self.hooks, "load_engineer", None),
            "write_engineer": getattr(self.hooks, "write_engineer", None),
            "create_engineer_profile": getattr(self.hooks, "create_engineer_profile", None),
            "create_session_record": getattr(self.hooks, "create_session_record", None),
        }
        missing = [name for name, hook in required_hooks.items() if hook is None]
        if missing:
            raise self.hooks.error_cls(
                f"{engineer_id} has no session in project {project.name}; cannot auto-create session "
                f"because switch-harness is missing hooks: {', '.join(missing)}"
            )

        assert self.hooks.engineer_path is not None
        assert self.hooks.load_engineer is not None
        assert self.hooks.write_engineer is not None
        assert self.hooks.create_engineer_profile is not None
        assert self.hooks.create_session_record is not None

        if self.hooks.engineer_path(engineer_id).exists():
            self.hooks.load_engineer(engineer_id)
        else:
            profile = self.hooks.create_engineer_profile(
                engineer_id=engineer_id,
                tool=args.tool,
                auth_mode=args.mode,
                provider=args.provider,
            )
            self.hooks.write_engineer(profile)
        monitor_engineers = list(getattr(project, "monitor_engineers", []) or [])
        monitor = True if not monitor_engineers else engineer_id in monitor_engineers
        session = self.hooks.create_session_record(
            engineer_id=engineer_id,
            project=project,
            tool=args.tool,
            auth_mode=args.mode,
            provider=args.provider,
            monitor=monitor,
        )
        session._template_model = model.strip()
        self.ensure_secret_ready(session)
        self.hooks.write_session(session)
        self.hooks.apply_template(session, project)
        self.hooks.ensure_dir(Path(session.runtime_dir))
        print(f"created session for declared seat {engineer_id} in {project.name}: {session.session}")
        return session

    def session_switch_harness(self, args: Any) -> int:
        if args.provider == "ark" and args.tool != "claude":
            raise self.hooks.error_cls(
                f"session switch-harness {args.engineer}: ark provider is claude-only; "
                f"rerun with --tool claude --provider ark (got tool={args.tool!r})."
            )
        requested_model = str(getattr(args, "model", "")).strip()
        if requested_model and args.tool != "claude":
            raise self.hooks.error_cls(
                f"session switch-harness {args.engineer}: --model is only supported for tool=claude "
                f"(got tool={args.tool!r})."
            )
        # Validate the tool/auth_mode/provider triple BEFORE any mutation.
        # Same rationale as CRUD engineer_create: unknown provider strings
        # used to silently succeed through build_switched_session, burning
        # an identity directory under the typoed name.
        validate_runtime_combo(
            args.tool,
            args.mode,
            args.provider,
            error_cls=self.hooks.error_cls,
            context=f"session switch-harness {args.engineer}",
        )
        project = self.hooks.load_project_or_current(args.project)
        try:
            old_session = self.hooks.load_session(project.name, self.hooks.normalize_name(args.engineer))
        except FileNotFoundError:
            self.create_declared_session_for_switch(project, args, requested_model)
            try:
                from seat_harness_memory import save_last_harness
                save_last_harness(
                    self.hooks.normalize_name(args.engineer),
                    args.tool,
                    args.mode,
                    args.provider,
                    model=requested_model,
                )
            except Exception as exc:  # pragma: no cover - sidecar cache only.
                print(f"warn: save_last_harness for {args.engineer}: {exc}", file=sys.stderr)
            return 0
        new_session = self.build_switched_session(
            old_session,
            project,
            args.tool,
            args.mode,
            args.provider,
            model=requested_model,
        )
        if (
            old_session.tool == new_session.tool
            and old_session.auth_mode == new_session.auth_mode
            and old_session.provider == new_session.provider
            and not requested_model
        ):
            print(f"no change for {old_session.engineer_id} in {project.name}")
            return 0
        self.ensure_secret_ready(new_session)
        self.hooks.session_stop_engineer(old_session)
        self.hooks.write_session(new_session)
        self.hooks.apply_template(new_session, project)
        self.hooks.ensure_dir(Path(new_session.runtime_dir))
        print(f"switched {new_session.engineer_id} in {project.name}: {old_session.session} -> {new_session.session}")
        print(f"run: agent-admin session start-engineer {new_session.engineer_id} --project {project.name}")
        try:
            from seat_harness_memory import save_last_harness
            save_last_harness(
                new_session.engineer_id,
                new_session.tool,
                new_session.auth_mode,
                new_session.provider,
                model=getattr(new_session, "_template_model", "") or "",
            )
        except Exception as exc:  # silent-ok: harness memory write is best-effort; must not fail switch
            print(f"warn: save_last_harness for {new_session.engineer_id}: {exc}", file=sys.stderr)
        return 0

    def session_switch_auth(self, args: Any) -> int:
        project = self.hooks.load_project_or_current(args.project)
        old_session = self.hooks.load_session(project.name, self.hooks.normalize_name(args.engineer))
        # Validate the (unchanged tool, new auth_mode, new provider) triple
        # before we mutate anything. For switch-auth the tool is locked to
        # the old session's tool — we only validate the requested auth
        # side of the combo.
        validate_runtime_combo(
            old_session.tool,
            args.mode,
            args.provider,
            error_cls=self.hooks.error_cls,
            context=f"session switch-auth {args.engineer}",
        )
        new_session = self.build_switched_session(
            old_session,
            project,
            old_session.tool,
            args.mode,
            args.provider,
        )
        if new_session.tool != old_session.tool:
            raise self.hooks.error_cls(
                f"Tool change requested for {old_session.engineer_id}; use session switch-harness instead"
            )
        if old_session.auth_mode == new_session.auth_mode and old_session.provider == new_session.provider:
            print(f"no auth change for {old_session.engineer_id} in {project.name}")
            return 0
        self.ensure_secret_ready(new_session)
        self.hooks.session_stop_engineer(old_session)
        self.hooks.write_session(new_session)
        self.hooks.ensure_dir(Path(new_session.runtime_dir))
        print(
            f"auth switched for {new_session.engineer_id} in {project.name}: "
            f"{old_session.identity} -> {new_session.identity}"
        )
        print(f"run: agent-admin session start-engineer {new_session.engineer_id} --project {project.name}")
        return 0
