from __future__ import annotations

from agent_admin_session_base import (
    os,
    shlex,
    shutil,
    tempfile,
    Path,
    Any,
    parse_env_file,
    real_user_home,
    seed_user_tool_dirs,
    _real_home_for_tool_seeding,
    SessionStartError,
)


class SessionLaunchEnv:
    def _real_home_for_tool_seeding(self) -> Path:
        compat_globals = getattr(self, "_compat_module_globals", None)
        if isinstance(compat_globals, dict):
            real_home_fn = compat_globals.get("real_user_home")
            if callable(real_home_fn):
                return real_home_fn()
        return _real_home_for_tool_seeding()

    def _parse_env_file(self, path: str) -> dict[str, str]:
        return parse_env_file(path)

    def _provider_record(self, session: Any) -> Any | None:
        try:
            from providers import ProviderError, get_provider
        except Exception:
            return None
        try:
            provider = get_provider(session.provider, home=self._real_home_for_tool_seeding())
        except ProviderError as exc:
            raise SessionStartError(str(exc)) from exc
        return provider

    def _provider_secret_file(self, session: Any) -> Path | None:
        provider = self._provider_record(session)
        if provider is not None:
            return Path(provider.secret_file)
        if session.secret_file:
            return Path(session.secret_file)
        return None

    def _provider_secret_env(self, session: Any) -> dict[str, str]:
        provider = self._provider_record(session)
        if provider is not None:
            try:
                from providers import load_provider_secret_vars
            except Exception:
                return self._parse_env_file(str(Path(provider.secret_file)))
            return load_provider_secret_vars(provider)
        secret_path = Path(session.secret_file) if session.secret_file else None
        if secret_path is None:
            return {}
        return self._parse_env_file(str(secret_path))

    def _launcher_auth_for(self, session: Any) -> str:
        from agent_admin_config import resolve_launcher_auth
        return resolve_launcher_auth(
            session.tool, session.auth_mode, session.provider, error_cls=SessionStartError
        )

    def _launcher_secret_target(self, session: Any, launcher_auth: str) -> Path | None:
        from agent_admin_config import resolve_launcher_secret_target
        return resolve_launcher_secret_target(session.tool, launcher_auth, real_home=self._real_home_for_tool_seeding())

    def _sync_launcher_secret_file(self, session: Any, launcher_auth: str) -> None:
        source = self._provider_secret_file(session)
        if source is None:
            return
        target = self._launcher_secret_target(session, launcher_auth)
        if target is None or not source.exists() or not source.read_text(encoding="utf-8").strip():
            return
        try:
            if source.resolve() == target.resolve():
                target.chmod(0o600)
                return
        except OSError:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        target.chmod(0o600)

    def _custom_env_payload(self, session: Any) -> dict[str, str]:
        from agent_admin_config import DEFAULT_CCR_BASE_URL, provider_default_base_url, provider_default_model

        if session.tool == "claude" and session.auth_mode == "ccr":
            return {
                "LAUNCHER_CUSTOM_API_KEY": "ccr-local-dummy",
                "LAUNCHER_CUSTOM_BASE_URL": os.environ.get("CLAWSEAT_CCR_BASE_URL", DEFAULT_CCR_BASE_URL),
            }

        provider = self._provider_record(session)
        secret_env = self._provider_secret_env(session)
        family = str(getattr(provider, "family", "") or "").strip()
        base_url = str(getattr(provider, "base_url", "") or "").strip()
        model = str(getattr(provider, "model", "") or "").strip()
        if not family:
            family = "anthropic" if session.tool == "claude" else ("openai" if session.tool == "codex" else "gemini")
            if not base_url:
                base_url = provider_default_base_url(session.tool, session.provider) or ""
            if not model:
                model = provider_default_model(session.tool, session.provider) or ""
        try:
            from providers import build_env_overlay
        except Exception as exc:
            raise SessionStartError(f"provider env overlay unavailable: {exc}") from exc
        overlay = build_env_overlay(family, secret_env, base_url, model)
        api_key = (
            overlay.get("ANTHROPIC_AUTH_TOKEN")
            or overlay.get("ANTHROPIC_API_KEY")
            or overlay.get("OPENAI_API_KEY")
            or overlay.get("GEMINI_API_KEY")
            or overlay.get("GOOGLE_API_KEY")
            or overlay.get("MINIMAX_API_KEY")
        )
        if session.tool == "claude":
            if not api_key:
                raise SessionStartError(
                    f"custom launcher env for {session.engineer_id} is missing a Claude-compatible API key"
                )
            payload = {
                "LAUNCHER_CUSTOM_API_KEY": api_key,
            }
            base_url = (
                overlay.get("ANTHROPIC_BASE_URL")
                or overlay.get("OPENAI_BASE_URL")
                or overlay.get("OPENAI_API_BASE")
                or overlay.get("GOOGLE_GEMINI_BASE_URL")
                or overlay.get("MINIMAX_API_HOST")
                or ""
            )
            if base_url:
                payload["LAUNCHER_CUSTOM_BASE_URL"] = base_url
            model = (
                overlay.get("ANTHROPIC_MODEL")
                or overlay.get("OPENAI_MODEL")
                or overlay.get("GEMINI_MODEL")
                or ""
            )
            if model:
                payload["LAUNCHER_CUSTOM_MODEL"] = model
            return payload

        if session.tool == "codex":
            api_key = api_key or ""
            if not api_key:
                raise SessionStartError(
                    f"custom launcher env for {session.engineer_id} is missing OPENAI_API_KEY"
                )
            base_url = (
                overlay.get("OPENAI_BASE_URL")
                or overlay.get("OPENAI_API_BASE")
                or ""
            )
            payload = {
                "LAUNCHER_CUSTOM_API_KEY": api_key,
                "LAUNCHER_CUSTOM_BASE_URL": base_url or "",
            }
            model = overlay.get("OPENAI_MODEL", "") or getattr(session, "_template_model", "")
            if model:
                payload["LAUNCHER_CUSTOM_MODEL"] = model
            return payload

        if session.tool == "gemini":
            api_key = api_key or ""
            if not api_key:
                raise SessionStartError(
                    f"custom launcher env for {session.engineer_id} is missing GEMINI_API_KEY / GOOGLE_API_KEY"
                )
            payload = {
                "LAUNCHER_CUSTOM_API_KEY": api_key,
                "LAUNCHER_CUSTOM_BASE_URL": overlay.get(
                    "GOOGLE_GEMINI_BASE_URL",
                    overlay.get("GEMINI_BASE_URL", ""),
                ),
            }
            model = overlay.get("GEMINI_MODEL", "") or getattr(session, "_template_model", "")
            if model:
                payload["LAUNCHER_CUSTOM_MODEL"] = model
            return payload

        raise SessionStartError(f"custom launcher env not implemented for tool={session.tool}")

    def _write_launcher_custom_env_file(self, session: Any) -> str:
        safe_session = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in session.session)
        payload = self._custom_env_payload(session)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            prefix=f"agent-admin-custom-{safe_session}.",
            dir="/tmp",
            delete=False,
            encoding="utf-8",
        )
        try:
            for key, value in payload.items():
                handle.write(f"export {key}={shlex.quote(value)}\n")
        finally:
            handle.close()
        os.chmod(handle.name, 0o600)
        return handle.name

    def _launcher_runtime_dir(self, session: Any, launcher_auth: str) -> Path | None:
        operator_home = self._real_home_for_tool_seeding()
        if session.tool == "claude":
            if launcher_auth == "oauth":
                return None
            if launcher_auth == "oauth_token":
                return operator_home / ".agent-runtime" / "identities" / "claude" / "oauth_token" / f"{launcher_auth}-{session.session}"
            return operator_home / ".agent-runtime" / "identities" / "claude" / "api" / f"{launcher_auth}-{session.session}"
        if session.tool == "codex":
            if launcher_auth == "chatgpt":
                return None
            return operator_home / ".agent-runtime" / "identities" / "codex" / "api" / f"{launcher_auth}-{session.session}"
        if session.tool == "gemini":
            if launcher_auth == "oauth":
                return None
            return operator_home / ".agent-runtime" / "identities" / "gemini" / "api" / f"{launcher_auth}-{session.session}"
        return None

    def _memory_brief_path(self, project: str) -> Path:
        real_home = self._real_home_for_tool_seeding()
        return real_home / ".agents" / "tasks" / project / "patrol" / "handoffs" / "memory-bootstrap.md"

    def reseed_sandbox_user_tool_dirs(self, session: Any) -> list[str]:
        launcher_auth = self._launcher_auth_for(session)
        runtime_dir = self._launcher_runtime_dir(session, launcher_auth)
        if runtime_dir is None:
            return []
        return seed_user_tool_dirs(Path(runtime_dir) / "home", project_name=session.project)
