from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import sys as _sys, pathlib as _pl
_fc_scripts = str(_pl.Path(__file__).resolve().parent.parent / "scripts")
if _fc_scripts not in _sys.path: _sys.path.insert(0, _fc_scripts)
from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load

from env_utils import parse_env_file, parse_env_text
from real_home import real_user_home
from utils import now_iso, q


PROVIDERS_FILENAME = "providers.toml"
_PROVIDERS_VERSION = 1
_PROVIDER_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TOOLS = {"claude", "codex", "gemini"}
_KINDS = {"api_key", "oauth_token"}
_FAMILIES = {"anthropic", "minimax", "deepseek", "xcode-best", "openai", "openai-compat", "gemini"}

_FAMILY_DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "minimax": "https://api.minimaxi.com/anthropic",
    "deepseek": "https://api.deepseek.com/anthropic",
    "xcode-best": "https://xcode.best",
    "openai": "https://api.openai.com/v1",
    "openai-compat": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com",
}
_FAMILY_DEFAULT_MODELS = {
    "minimax": "MiniMax-M2.7-highspeed",
    "deepseek": "deepseek-v4-pro[1M]",
    "xcode-best": "gpt-5.5",
}

_SECRET_KEY_NAMES = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ARK_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MINIMAX_API_KEY",
    "OPENAI_API_KEY",
    "XCODE_API_KEY",
    "XCODE_BEST_API_KEY",
    "API_KEY",
}

_PLACEHOLDER_SECRET_VALUES = {
    "<set-by-operator>",
    "set-by-operator",
    "<set by operator>",
    "set by operator",
}


class ProviderError(RuntimeError):
    pass


class ProviderValidationError(ProviderError):
    pass


class ProviderConflictError(ProviderError):
    pass


class ProviderNotFoundError(ProviderError):
    pass


class ProviderSecretMissingError(ProviderError):
    pass


class ProviderReferenceError(ProviderError):
    def __init__(self, name: str, refs: tuple["SessionReference", ...]) -> None:
        self.name = name
        self.refs = refs
        refs_text = ", ".join(f"{ref.project}/{ref.seat_id}" for ref in refs) or "<none>"
        super().__init__(f"provider {name!r} is still referenced by session.toml: {refs_text}")


@dataclass(frozen=True)
class SessionReference:
    project: str
    seat_id: str
    path: str
    provider: str
    secret_file: str = ""


@dataclass
class Provider:
    name: str
    tool: str
    kind: str
    family: str
    secret_file: str
    base_url: str = ""
    model: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def has_secret(self) -> bool:
        path = Path(self.secret_file).expanduser()
        try:
            return path.is_file() and bool(path.read_text(encoding="utf-8", errors="ignore").strip())
        except OSError:
            return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tool": self.tool,
            "kind": self.kind,
            "family": self.family,
            "base_url": self.base_url,
            "model": self.model,
            "has_secret": self.has_secret,
            "secret_file": self.secret_file,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def as_human_line(self) -> str:
        return "\t".join(
            [
                self.name,
                self.tool,
                self.kind,
                self.family,
                self.base_url or "-",
                self.model or "-",
                "yes" if self.has_secret else "no",
                self.secret_file,
                self.created_at or "-",
                self.updated_at or "-",
            ]
        )


@dataclass
class ProvidersStore:
    version: int = _PROVIDERS_VERSION
    providers: dict[str, Provider] = field(default_factory=dict)

    def sorted_providers(self, tool: str | None = None) -> list[Provider]:
        providers = list(self.providers.values())
        if tool is not None:
            providers = [provider for provider in providers if provider.tool == tool]
        return sorted(providers, key=lambda provider: provider.name)

    def as_toml(self) -> str:
        lines = [f"version = {self.version}", ""]
        for index, provider in enumerate(self.sorted_providers()):
            if index:
                lines.append("")
            lines.extend(_render_provider(provider))
        if len(lines) == 2:
            return "version = 1\n"
        return "\n".join(lines).rstrip() + "\n"


def providers_path(*, home: Path | None = None) -> Path:
    base = home if home is not None else real_user_home()
    return Path(base) / ".agents" / PROVIDERS_FILENAME


def secrets_root(*, tool: str, home: Path | None = None) -> Path:
    base = home if home is not None else real_user_home()
    return Path(base) / ".agents" / "secrets" / tool


def provider_secret_file_path(name: str, tool: str, *, home: Path | None = None) -> Path:
    return secrets_root(tool=tool, home=home) / f"{name}.env"


def _atomic_write_text(path: Path, content: str, *, mode: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        prefix=f".{path.name}.",
        dir=str(path.parent),
        delete=False,
        encoding="utf-8",
    )
    try:
        handle.write(content)
        handle.flush()
        os.fchmod(handle.fileno(), mode)
    finally:
        handle.close()
    os.replace(handle.name, path)
    path.chmod(mode)


def _ensure_dir_mode(path: Path, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(mode)
    except OSError:
        pass


def _strip_secret_text(secret: str) -> str:
    return secret.rstrip("\r\n")


def _is_placeholder_secret_value(value: str) -> bool:
    return str(value or "").strip().strip("\"'").lower() in _PLACEHOLDER_SECRET_VALUES


def _env_has_real_secret(parsed: Mapping[str, str]) -> bool:
    for key, value in parsed.items():
        if (
            str(key).strip() in _SECRET_KEY_NAMES
            and str(value).strip()
            and not _is_placeholder_secret_value(str(value))
        ):
            return True
    return False


def _secret_env_key(provider: Provider) -> str:
    if provider.family == "anthropic":
        return "CLAUDE_CODE_OAUTH_TOKEN" if provider.kind == "oauth_token" else "ANTHROPIC_API_KEY"
    if provider.family == "minimax":
        return "ANTHROPIC_AUTH_TOKEN"
    if provider.family == "deepseek":
        return "DEEPSEEK_API_KEY"
    if provider.family == "xcode-best":
        return "XCODE_BEST_API_KEY"
    if provider.family in {"openai", "openai-compat"}:
        return "OPENAI_API_KEY"
    if provider.family == "gemini":
        return "GEMINI_API_KEY"
    return "API_KEY"


def _looks_like_env_secret_text(secret_text: str) -> bool:
    parsed = parse_env_text(secret_text)
    return bool(parsed) and all(re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) for key in parsed)


def _secret_file_text(provider: Provider, secret: str) -> str:
    secret_text = _strip_secret_text(secret)
    if not secret_text.strip() or _is_placeholder_secret_value(secret_text):
        raise ProviderSecretMissingError(f"provider {provider.name!r} requires a secret on stdin")
    if _looks_like_env_secret_text(secret_text):
        parsed = parse_env_text(secret_text)
        if not _env_has_real_secret(parsed):
            raise ProviderSecretMissingError(
                f"provider {provider.name!r} requires a non-placeholder secret value on stdin"
            )
        return secret_text.rstrip("\n") + "\n"
    return f"{_secret_env_key(provider)}={shlex.quote(secret_text)}\n"


def _validate_name(name: str) -> str:
    normalized = str(name or "").strip()
    if not normalized:
        raise ProviderValidationError("provider name cannot be empty")
    if not _PROVIDER_NAME_RE.fullmatch(normalized):
        raise ProviderValidationError(
            f"invalid provider name {name!r}: must match kebab-case [a-z0-9-]+"
        )
    return normalized


def _validate_common_fields(name: str, tool: str, kind: str, family: str) -> None:
    if tool not in _TOOLS:
        raise ProviderValidationError(f"invalid provider tool {tool!r}: must be one of {sorted(_TOOLS)}")
    if kind not in _KINDS:
        raise ProviderValidationError(f"invalid provider kind {kind!r}: must be one of {sorted(_KINDS)}")
    if family not in _FAMILIES:
        raise ProviderValidationError(
            f"invalid provider family {family!r}: must be one of {sorted(_FAMILIES)}"
        )
    if kind == "oauth_token" and tool != "claude":
        raise ProviderValidationError("oauth_token providers are only allowed for tool='claude'")
    _validate_name(name)


def _expected_secret_file(provider: Provider, *, home: Path | None = None) -> Path:
    return provider_secret_file_path(provider.name, provider.tool, home=home)


def _provider_defaults(tool: str, provider_name: str) -> tuple[str, str]:
    try:
        from agent_admin_config import provider_default_base_url, provider_default_model
    except Exception:
        return "", ""
    return (
        str(provider_default_base_url(tool, provider_name) or "").strip(),
        str(provider_default_model(tool, provider_name) or "").strip(),
    )


def _looks_like_xcode_provider(provider_name: str, base_url: str) -> bool:
    candidate = f"{provider_name} {base_url}".lower()
    return "xcode-best" in candidate or "xcode.best" in candidate


def _looks_like_deepseek_provider(provider_name: str, base_url: str) -> bool:
    candidate = f"{provider_name} {base_url}".lower()
    return "deepseek" in candidate


def _provider_from_raw(name: str, raw: Mapping[str, Any], *, home: Path | None = None) -> Provider:
    provider_name = _validate_name(name)
    allowed = {
        "tool",
        "kind",
        "family",
        "base_url",
        "model",
        "secret_file",
        "created_at",
        "updated_at",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ProviderValidationError(
            f"unknown provider field(s) for {provider_name!r}: {unknown}"
        )
    tool = str(raw.get("tool", "")).strip()
    kind = str(raw.get("kind", "")).strip()
    family = str(raw.get("family", "")).strip()
    base_url = str(raw.get("base_url", "")).strip()
    model = str(raw.get("model", "")).strip()
    secret_file = str(raw.get("secret_file", "")).strip()
    created_at = str(raw.get("created_at", "")).strip()
    updated_at = str(raw.get("updated_at", "")).strip()
    if (
        family in {"minimax", "anthropic", "openai", "openai-compat"}
        and _looks_like_deepseek_provider(provider_name, base_url)
    ):
        family = "deepseek"
        if not base_url:
            base_url = _FAMILY_DEFAULT_BASE_URLS["deepseek"]
        if not model:
            model = _FAMILY_DEFAULT_MODELS["deepseek"]
    if family in {"minimax", "openai", "openai-compat"} and _looks_like_xcode_provider(provider_name, base_url):
        family = "xcode-best"
        if not base_url:
            base_url = _FAMILY_DEFAULT_BASE_URLS["xcode-best"]
        if not model:
            model = _FAMILY_DEFAULT_MODELS["xcode-best"]
    _validate_common_fields(provider_name, tool, kind, family)
    provider = Provider(
        name=provider_name,
        tool=tool,
        kind=kind,
        family=family,
        base_url=base_url,
        model=model,
        secret_file=secret_file,
        created_at=created_at,
        updated_at=updated_at,
    )
    expected_secret = _expected_secret_file(provider, home=home)
    if Path(provider.secret_file).expanduser() != expected_secret:
        raise ProviderValidationError(
            f"provider {provider_name!r} has invalid secret_file {provider.secret_file!r}; "
            f"expected {expected_secret}"
        )
    if not provider.created_at or not provider.updated_at:
        raise ProviderValidationError(
            f"provider {provider_name!r} must define created_at and updated_at"
        )
    return provider


def _render_provider(provider: Provider) -> list[str]:
    lines = [f"[providers.{q(provider.name)}]"]
    lines.append(f"tool = {q(provider.tool)}")
    lines.append(f"kind = {q(provider.kind)}")
    lines.append(f"family = {q(provider.family)}")
    if provider.base_url:
        lines.append(f"base_url = {q(provider.base_url)}")
    if provider.model:
        lines.append(f"model = {q(provider.model)}")
    lines.append(f"secret_file = {q(provider.secret_file)}")
    lines.append(f"created_at = {q(provider.created_at)}")
    lines.append(f"updated_at = {q(provider.updated_at)}")
    return lines


def read_providers(path: Path | None = None, *, home: Path | None = None) -> ProvidersStore:
    providers_file = Path(path) if path is not None else providers_path(home=home)
    if not providers_file.exists():
        return ProvidersStore()
    with providers_file.open("rb") as handle:
        data = _toml_load(handle)
    if not isinstance(data, dict):
        raise ProviderValidationError(f"invalid providers.toml at {providers_file}: top-level TOML is not a table")
    version = int(data.get("version", 0) or 0)
    if version != _PROVIDERS_VERSION:
        raise ProviderValidationError(
            f"unsupported providers.toml version {version!r}; expected {_PROVIDERS_VERSION}"
        )
    raw_providers = data.get("providers", {})
    if raw_providers is None:
        raw_providers = {}
    if not isinstance(raw_providers, dict):
        raise ProviderValidationError("providers.toml [providers] section must be a table")
    store = ProvidersStore(version=version)
    for name in sorted(raw_providers):
        entry = raw_providers[name]
        if not isinstance(entry, dict):
            raise ProviderValidationError(f"provider entry {name!r} must be a table")
        store.providers[str(name)] = _provider_from_raw(str(name), entry, home=home)
    return store


def write_providers(store: ProvidersStore, path: Path | None = None) -> Path:
    providers_file = Path(path) if path is not None else providers_path()
    content = store.as_toml()
    _atomic_write_text(providers_file, content, mode=0o644)
    return providers_file


def get_provider(
    name: str,
    *,
    store: ProvidersStore | None = None,
    home: Path | None = None,
) -> Provider | None:
    provider_name = _validate_name(name)
    current = store if store is not None else read_providers(home=home)
    return current.providers.get(provider_name)


def list_providers(
    tool: str | None = None,
    *,
    store: ProvidersStore | None = None,
    home: Path | None = None,
) -> list[Provider]:
    current = store if store is not None else read_providers(home=home)
    return current.sorted_providers(tool)


def _session_provider_refs(name: str, *, home: Path | None = None) -> tuple[SessionReference, ...]:
    provider_name = _validate_name(name)
    base = home if home is not None else real_user_home()
    sessions_root = Path(base) / ".agents" / "sessions"
    if not sessions_root.exists():
        return ()
    refs: list[SessionReference] = []
    for session_toml in sorted(sessions_root.glob("*/*/session.toml")):
        try:
            data = _toml_loads(session_toml.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(data.get("provider", "")).strip() != provider_name:
            continue
        refs.append(
            SessionReference(
                project=session_toml.parent.parent.name,
                seat_id=session_toml.parent.name,
                path=str(session_toml),
                provider=provider_name,
                secret_file=str(data.get("secret_file", "")).strip(),
            )
        )
    return tuple(refs)


def _rewrite_session_provider_ref(
    session_path: Path,
    *,
    old_provider: str,
    new_provider: str,
    old_secret_file: str | None = None,
    new_secret_file: str | None = None,
) -> bool:
    try:
        text = session_path.read_text(encoding="utf-8")
    except OSError:
        return False
    updated = text
    updated = _replace_toml_scalar(updated, "provider", new_provider, expected=old_provider)
    if old_secret_file is not None and new_secret_file is not None:
        updated = _replace_toml_scalar(
            updated,
            "secret_file",
            new_secret_file,
            expected=old_secret_file,
        )
    if updated == text:
        return False
    _atomic_write_text(session_path, updated, mode=0o644)
    return True


def _replace_toml_scalar(text: str, key: str, value: str, *, expected: str | None = None) -> str:
    pattern = re.compile(rf"^({re.escape(key)}\s*=\s*)(.+)$", re.MULTILINE)

    def _repl(match: re.Match[str]) -> str:
        current = match.group(2).strip()
        if expected is not None:
            try:
                parsed = _toml_loads(f"key = {current}\n")
                current_value = str(parsed["key"])
            except Exception:
                current_value = current.strip("\"'")
            if current_value != expected:
                return match.group(0)
        return f"{match.group(1)}{q(value)}"

    return pattern.sub(_repl, text, count=1)


@dataclass(frozen=True)
class ProviderMutationResult:
    provider: Provider
    session_refs: tuple[SessionReference, ...] = ()


def add_provider(
    provider: Provider,
    secret: str,
    *,
    store: ProvidersStore | None = None,
) -> ProviderMutationResult:
    current = store if store is not None else read_providers()
    provider_name = _validate_name(provider.name)
    provider = Provider(
        name=provider_name,
        tool=str(provider.tool).strip(),
        kind=str(provider.kind).strip(),
        family=str(provider.family).strip(),
        secret_file="",
        base_url=str(provider.base_url).strip(),
        model=str(provider.model).strip(),
        created_at="",
        updated_at="",
    )
    _validate_common_fields(provider.name, provider.tool, provider.kind, provider.family)
    if provider.name in current.providers:
        raise ProviderConflictError(f"provider {provider.name!r} already exists")
    default_base_url, default_model = _provider_defaults(provider.tool, provider.name)
    if not provider.base_url:
        provider.base_url = default_base_url
    if not provider.model:
        provider.model = default_model
    secret_text = _secret_file_text(provider, secret)
    secret_file = _expected_secret_file(provider)
    _ensure_dir_mode(secret_file.parent, 0o700)
    _atomic_write_text(secret_file, secret_text, mode=0o600)
    timestamp = now_iso()
    provider.secret_file = str(secret_file)
    provider.created_at = timestamp
    provider.updated_at = timestamp
    current.providers[provider.name] = provider
    write_providers(current)
    return ProviderMutationResult(provider=provider)


def update_provider(
    name: str,
    patch: Mapping[str, Any],
    secret: str | None = None,
    *,
    store: ProvidersStore | None = None,
) -> ProviderMutationResult:
    current = store if store is not None else read_providers()
    provider_name = _validate_name(name)
    if provider_name not in current.providers:
        raise ProviderNotFoundError(f"provider {provider_name!r} not found")
    existing = current.providers[provider_name]
    tool = str(patch.get("tool", existing.tool)).strip()
    kind = str(patch.get("kind", existing.kind)).strip()
    family = str(patch.get("family", existing.family)).strip()
    base_url = str(patch.get("base_url", existing.base_url)).strip()
    model = str(patch.get("model", existing.model)).strip()
    _validate_common_fields(provider_name, tool, kind, family)
    default_base_url, default_model = _provider_defaults(tool, provider_name)
    if not base_url:
        base_url = default_base_url
    if not model:
        model = default_model
    expected_secret_file = provider_secret_file_path(provider_name, tool)
    updated = Provider(
        name=provider_name,
        tool=tool,
        kind=kind,
        family=family,
        base_url=base_url,
        model=model,
        secret_file=str(expected_secret_file),
        created_at=existing.created_at,
        updated_at=now_iso(),
    )
    expected_secret = _expected_secret_file(updated)
    current_secret = Path(existing.secret_file).expanduser()
    if secret is not None:
        secret_text = _secret_file_text(updated, secret)
        _ensure_dir_mode(expected_secret.parent, 0o700)
        _atomic_write_text(expected_secret, secret_text, mode=0o600)
    elif current_secret.exists() and current_secret != expected_secret:
        _ensure_dir_mode(expected_secret.parent, 0o700)
        shutil.move(str(current_secret), str(expected_secret))
        expected_secret.chmod(0o600)
    updated.secret_file = str(expected_secret)
    current.providers[provider_name] = updated
    write_providers(current)
    return ProviderMutationResult(provider=updated)


def _migrated_path(path: Path, timestamp: str) -> Path:
    return path.with_name(f"{path.name}.migrated-{timestamp}")


def _unique_migrated_path(path: Path, timestamp: str) -> Path:
    candidate = _migrated_path(path, timestamp)
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = _migrated_path(path, f"{timestamp}-{counter}")
    return candidate


def _legacy_provider_spec(
    tool: str,
    provider_name: str,
    secret_vars: Mapping[str, str],
) -> tuple[str, str, str, str]:
    normalized = {str(key): str(value).strip() for key, value in secret_vars.items() if str(value).strip()}
    base_url, default_model = _provider_defaults(tool, provider_name)

    if "CLAUDE_CODE_OAUTH_TOKEN" in normalized:
        return ("anthropic", "oauth_token", base_url, default_model)

    if "ANTHROPIC_API_KEY" in normalized:
        base_url = (
            normalized.get("ANTHROPIC_BASE_URL", "")
            or normalized.get("OPENAI_BASE_URL", "")
            or normalized.get("OPENAI_API_BASE", "")
            or base_url
        )
        model = normalized.get("ANTHROPIC_MODEL", "") or normalized.get("OPENAI_MODEL", "") or default_model
        return ("anthropic", "api_key", base_url, model)

    if "ARK_API_KEY" in normalized:
        base_url = normalized.get("ARK_BASE_URL", "") or normalized.get("ANTHROPIC_BASE_URL", "") or base_url
        model = normalized.get("ARK_MODEL", "") or normalized.get("ANTHROPIC_MODEL", "") or default_model
        return ("anthropic", "api_key", base_url, model)

    if "DEEPSEEK_API_KEY" in normalized:
        base_url = normalized.get("DEEPSEEK_BASE_URL", "") or normalized.get("ANTHROPIC_BASE_URL", "") or base_url
        model = normalized.get("DEEPSEEK_MODEL", "") or normalized.get("ANTHROPIC_MODEL", "") or default_model
        return ("deepseek", "api_key", base_url, model)

    if "ANTHROPIC_AUTH_TOKEN" in normalized:
        base_url = normalized.get("ANTHROPIC_BASE_URL", "") or normalized.get("MINIMAX_API_HOST", "") or base_url
        model = normalized.get("ANTHROPIC_MODEL", "") or default_model
        if _looks_like_deepseek_provider(provider_name, base_url):
            return (
                "deepseek",
                "api_key",
                base_url or _FAMILY_DEFAULT_BASE_URLS["deepseek"],
                model or _FAMILY_DEFAULT_MODELS["deepseek"],
            )
        if _looks_like_xcode_provider(provider_name, base_url):
            return (
                "xcode-best",
                "api_key",
                base_url or _FAMILY_DEFAULT_BASE_URLS["xcode-best"],
                model or _FAMILY_DEFAULT_MODELS["xcode-best"],
            )
        return ("minimax", "api_key", base_url, model)

    if "OPENAI_API_KEY" in normalized:
        base_url = normalized.get("OPENAI_BASE_URL", "") or normalized.get("OPENAI_API_BASE", "") or base_url
        model = normalized.get("OPENAI_MODEL", "") or default_model
        if _looks_like_xcode_provider(provider_name, base_url):
            return (
                "xcode-best",
                "api_key",
                base_url or _FAMILY_DEFAULT_BASE_URLS["xcode-best"],
                model or _FAMILY_DEFAULT_MODELS["xcode-best"],
            )
        family = "openai-compat" if base_url and "api.openai.com" not in base_url.lower() else "openai"
        return (family, "api_key", base_url, model)

    if "GEMINI_API_KEY" in normalized or "GOOGLE_API_KEY" in normalized:
        base_url = normalized.get("GOOGLE_GEMINI_BASE_URL", "") or normalized.get("GEMINI_BASE_URL", "") or base_url
        model = normalized.get("GEMINI_MODEL", "") or default_model
        return ("gemini", "api_key", base_url, model)

    raise ProviderValidationError(
        f"unable to infer provider family for {tool}/{provider_name}: missing supported secret keys"
    )


def migrate_legacy_provider_secrets(
    *,
    home: Path | None = None,
    store: ProvidersStore | None = None,
) -> list[ProviderMutationResult]:
    base = home if home is not None else real_user_home()
    secrets_root_dir = Path(base) / ".agents" / "secrets"
    if not secrets_root_dir.exists():
        return []
    current = store if store is not None else read_providers()
    results: list[ProviderMutationResult] = []
    timestamp = now_iso()

    for tool_dir in sorted(secrets_root_dir.iterdir(), key=lambda path: path.name):
        if not tool_dir.is_dir() or tool_dir.name not in _TOOLS:
            continue
        for provider_dir in sorted(tool_dir.iterdir(), key=lambda path: path.name):
            if not provider_dir.is_dir():
                continue
            legacy_files = sorted(
                (
                    path
                    for path in provider_dir.glob("*.env")
                    if path.is_file() and ".migrated-" not in path.name
                ),
                key=lambda path: (path.stat().st_mtime_ns, path.name),
            )
            if not legacy_files:
                continue
            chosen = legacy_files[-1]
            chosen_secret = chosen.read_text(encoding="utf-8", errors="ignore")
            try:
                family, kind, base_url, model = _legacy_provider_spec(tool_dir.name, provider_dir.name, parse_env_file(chosen))
            except ProviderValidationError as exc:
                print(f"warn: legacy provider migration skipped for {tool_dir.name}/{provider_dir.name}: {exc}", file=sys.stderr)
                continue

            provider = Provider(
                name=provider_dir.name,
                tool=tool_dir.name,
                kind=kind,
                family=family,
                secret_file=str(provider_secret_file_path(provider_dir.name, tool_dir.name, home=base)),
                base_url=base_url,
                model=model,
            )

            expected_secret = Path(provider.secret_file)
            if expected_secret.exists():
                existing_text = expected_secret.read_text(encoding="utf-8", errors="ignore")
                if existing_text != chosen_secret:
                    backup = _unique_migrated_path(expected_secret, timestamp)
                    _atomic_write_text(backup, existing_text, mode=0o600)
                    print(
                        f"warn: legacy provider secret conflict for {tool_dir.name}/{provider_dir.name}: "
                        f"backed up existing {expected_secret} to {backup}",
                        file=sys.stderr,
                    )
            if provider.name in current.providers:
                result = update_provider(
                    provider.name,
                    {
                        "tool": provider.tool,
                        "kind": provider.kind,
                        "family": provider.family,
                        "base_url": provider.base_url,
                        "model": provider.model,
                    },
                    secret=chosen_secret,
                    store=current,
                )
            else:
                result = add_provider(provider, chosen_secret, store=current)
            results.append(result)

            for legacy_file in legacy_files:
                if not legacy_file.exists():
                    continue
                migrated_path = _unique_migrated_path(legacy_file, timestamp)
                legacy_file.rename(migrated_path)
    return results


def remove_provider(
    name: str,
    force: bool = False,
    *,
    store: ProvidersStore | None = None,
) -> ProviderMutationResult:
    current = store if store is not None else read_providers()
    provider_name = _validate_name(name)
    provider = current.providers.get(provider_name)
    if provider is None:
        raise ProviderNotFoundError(f"provider {provider_name!r} not found")
    refs = _session_provider_refs(provider_name)
    if refs and not force:
        raise ProviderReferenceError(provider_name, refs)
    current.providers.pop(provider_name, None)
    write_providers(current)
    secret_path = Path(provider.secret_file)
    if secret_path.exists():
        try:
            secret_path.unlink()
        except OSError:
            pass
    return ProviderMutationResult(provider=provider, session_refs=refs)


def rename_provider(
    old: str,
    new: str,
    *,
    store: ProvidersStore | None = None,
) -> ProviderMutationResult:
    current = store if store is not None else read_providers()
    old_name = _validate_name(old)
    new_name = _validate_name(new)
    if old_name not in current.providers:
        raise ProviderNotFoundError(f"provider {old_name!r} not found")
    if new_name in current.providers:
        raise ProviderConflictError(f"provider {new_name!r} already exists")
    existing = current.providers.pop(old_name)
    old_secret = Path(existing.secret_file)
    renamed = Provider(
        name=new_name,
        tool=existing.tool,
        kind=existing.kind,
        family=existing.family,
        base_url=existing.base_url,
        model=existing.model,
        secret_file=str(provider_secret_file_path(new_name, existing.tool)),
        created_at=existing.created_at,
        updated_at=now_iso(),
    )
    new_secret = Path(renamed.secret_file)
    if old_secret.exists() and old_secret != new_secret:
        if new_secret.exists():
            raise ProviderConflictError(
                f"cannot rename provider {old_name!r} to {new_name!r}: secret target {new_secret} already exists"
            )
        _ensure_dir_mode(new_secret.parent, 0o700)
        os.replace(old_secret, new_secret)
        new_secret.chmod(0o600)
    elif not new_secret.exists() and old_secret.exists():
        # old_secret and new_secret are the same path; ensure permissions are correct.
        try:
            new_secret.chmod(0o600)
        except OSError:
            pass
    current.providers[new_name] = renamed
    write_providers(current)
    refs = _session_provider_refs(old_name)
    updated_refs: list[SessionReference] = []
    for ref in refs:
        session_path = Path(ref.path)
        if _rewrite_session_provider_ref(
            session_path,
            old_provider=old_name,
            new_provider=new_name,
            old_secret_file=ref.secret_file or None,
            new_secret_file=renamed.secret_file,
        ):
            updated_refs.append(
                SessionReference(
                    project=ref.project,
                    seat_id=ref.seat_id,
                    path=ref.path,
                    provider=new_name,
                    secret_file=renamed.secret_file if ref.secret_file == existing.secret_file else ref.secret_file,
                )
            )
    return ProviderMutationResult(provider=renamed, session_refs=tuple(updated_refs or refs))


def _coalesce(secret_vars: Mapping[str, str], keys: Iterable[str]) -> str:
    for key in keys:
        value = str(secret_vars.get(key, "")).strip()
        if value and not _is_placeholder_secret_value(value):
            return value
    return ""


def build_env_overlay(
    family: str,
    secret_vars: Mapping[str, str],
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    family = str(family or "").strip()
    if family not in _FAMILIES:
        raise ProviderValidationError(f"unsupported provider family {family!r}")
    base_url_value = str(base_url or "").strip() or _FAMILY_DEFAULT_BASE_URLS.get(family, "")
    model_value = str(model or "").strip() or _FAMILY_DEFAULT_MODELS.get(family, "")
    overlay: dict[str, str] = {}

    if family == "anthropic":
        token = _coalesce(
            secret_vars,
            (
                "ANTHROPIC_API_KEY",
                "CLAUDE_CODE_OAUTH_TOKEN",
                "ANTHROPIC_AUTH_TOKEN",
                "ARK_API_KEY",
                "OPENAI_API_KEY",
            ),
        )
        if not token:
            raise ProviderSecretMissingError(
                "anthropic family secret is missing ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN / "
                "ANTHROPIC_AUTH_TOKEN / ARK_API_KEY / OPENAI_API_KEY"
            )
        base_url_value = _coalesce(
            secret_vars,
            ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE", "ARK_BASE_URL"),
        ) or base_url_value
        model_value = _coalesce(secret_vars, ("ANTHROPIC_MODEL", "OPENAI_MODEL", "ARK_MODEL")) or model_value
        if "CLAUDE_CODE_OAUTH_TOKEN" in secret_vars and str(secret_vars.get("CLAUDE_CODE_OAUTH_TOKEN", "")).strip():
            overlay["CLAUDE_CODE_OAUTH_TOKEN"] = str(secret_vars["CLAUDE_CODE_OAUTH_TOKEN"]).strip()
        if "ANTHROPIC_API_KEY" in secret_vars and str(secret_vars.get("ANTHROPIC_API_KEY", "")).strip():
            overlay["ANTHROPIC_API_KEY"] = str(secret_vars["ANTHROPIC_API_KEY"]).strip()
        if "ANTHROPIC_AUTH_TOKEN" in secret_vars and str(secret_vars.get("ANTHROPIC_AUTH_TOKEN", "")).strip():
            overlay["ANTHROPIC_AUTH_TOKEN"] = str(secret_vars["ANTHROPIC_AUTH_TOKEN"]).strip()
        if "ARK_API_KEY" in secret_vars and str(secret_vars.get("ARK_API_KEY", "")).strip():
            overlay["ARK_API_KEY"] = str(secret_vars["ARK_API_KEY"]).strip()
        if "OPENAI_API_KEY" in secret_vars and str(secret_vars.get("OPENAI_API_KEY", "")).strip():
            overlay["OPENAI_API_KEY"] = str(secret_vars["OPENAI_API_KEY"]).strip()
        if "ANTHROPIC_AUTH_TOKEN" not in overlay and "ARK_API_KEY" in overlay:
            overlay["ANTHROPIC_AUTH_TOKEN"] = overlay["ARK_API_KEY"]
        if "ANTHROPIC_API_KEY" not in overlay and "OPENAI_API_KEY" in overlay:
            overlay["ANTHROPIC_API_KEY"] = overlay["OPENAI_API_KEY"]
        if base_url_value:
            overlay["ANTHROPIC_BASE_URL"] = base_url_value
        if model_value:
            overlay["ANTHROPIC_MODEL"] = model_value
        return overlay

    if family == "minimax":
        token = _coalesce(secret_vars, ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "MINIMAX_API_KEY"))
        if not token:
            raise ProviderSecretMissingError(
                "minimax family secret is missing ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY / MINIMAX_API_KEY"
            )
        overlay["ANTHROPIC_AUTH_TOKEN"] = token
        overlay["ANTHROPIC_API_KEY"] = token
        overlay["MINIMAX_API_KEY"] = token
        api_host = _coalesce(secret_vars, ("MINIMAX_API_HOST", "ANTHROPIC_BASE_URL")) or base_url_value
        if api_host:
            overlay["MINIMAX_API_HOST"] = api_host
            overlay["ANTHROPIC_BASE_URL"] = api_host
        if model_value:
            overlay["ANTHROPIC_MODEL"] = model_value
        return overlay

    if family == "deepseek":
        token = _coalesce(secret_vars, ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "API_KEY"))
        if not token:
            raise ProviderSecretMissingError(
                "deepseek family secret is missing DEEPSEEK_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY / API_KEY"
            )
        base_url_value = _coalesce(secret_vars, ("DEEPSEEK_BASE_URL", "ANTHROPIC_BASE_URL")) or base_url_value
        model_value = _coalesce(secret_vars, ("DEEPSEEK_MODEL", "ANTHROPIC_MODEL")) or model_value
        overlay["DEEPSEEK_API_KEY"] = token
        overlay["ANTHROPIC_AUTH_TOKEN"] = token
        if base_url_value:
            overlay["DEEPSEEK_BASE_URL"] = base_url_value
            overlay["ANTHROPIC_BASE_URL"] = base_url_value
        if model_value:
            overlay["DEEPSEEK_MODEL"] = model_value
            overlay["ANTHROPIC_MODEL"] = model_value
        return overlay

    if family == "xcode-best":
        key = _coalesce(
            secret_vars,
            (
                "XCODE_BEST_API_KEY",
                "XCODE_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY",
                "API_KEY",
            ),
        )
        if not key:
            raise ProviderSecretMissingError(
                "xcode-best family secret is missing XCODE_BEST_API_KEY / XCODE_API_KEY / "
                "ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY / OPENAI_API_KEY / API_KEY"
            )
        base_url_value = _coalesce(
            secret_vars,
            ("ANTHROPIC_BASE_URL", "XCODE_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE"),
        ) or base_url_value
        model_value = _coalesce(secret_vars, ("ANTHROPIC_MODEL", "XCODE_MODEL", "OPENAI_MODEL")) or model_value
        overlay["ANTHROPIC_AUTH_TOKEN"] = key
        overlay["ANTHROPIC_API_KEY"] = key
        overlay["XCODE_BEST_API_KEY"] = key
        overlay["OPENAI_API_KEY"] = key
        if base_url_value:
            overlay["ANTHROPIC_BASE_URL"] = base_url_value
            overlay["XCODE_BASE_URL"] = base_url_value
            overlay["OPENAI_BASE_URL"] = base_url_value
            overlay["OPENAI_API_BASE"] = base_url_value
        if model_value:
            overlay["ANTHROPIC_MODEL"] = model_value
            overlay["XCODE_MODEL"] = model_value
            overlay["OPENAI_MODEL"] = model_value
        return overlay

    if family == "openai":
        key = _coalesce(secret_vars, ("OPENAI_API_KEY",))
        if not key:
            raise ProviderSecretMissingError("openai family secret is missing OPENAI_API_KEY")
        base_url_value = _coalesce(secret_vars, ("OPENAI_BASE_URL", "OPENAI_API_BASE")) or base_url_value
        model_value = _coalesce(secret_vars, ("OPENAI_MODEL",)) or model_value
        overlay["OPENAI_API_KEY"] = key
        if base_url_value:
            overlay["OPENAI_BASE_URL"] = base_url_value
        if model_value:
            overlay["OPENAI_MODEL"] = model_value
        return overlay

    if family == "openai-compat":
        key = _coalesce(secret_vars, ("OPENAI_API_KEY",))
        if not key:
            raise ProviderSecretMissingError("openai-compat family secret is missing OPENAI_API_KEY")
        base_url_value = _coalesce(secret_vars, ("OPENAI_BASE_URL", "OPENAI_API_BASE")) or base_url_value
        model_value = _coalesce(secret_vars, ("OPENAI_MODEL",)) or model_value
        overlay["OPENAI_API_KEY"] = key
        if base_url_value:
            overlay["OPENAI_BASE_URL"] = base_url_value
            overlay["OPENAI_API_BASE"] = base_url_value
        if model_value:
            overlay["OPENAI_MODEL"] = model_value
        return overlay

    if family == "gemini":
        key = _coalesce(secret_vars, ("GEMINI_API_KEY", "GOOGLE_API_KEY"))
        if not key:
            raise ProviderSecretMissingError("gemini family secret is missing GEMINI_API_KEY / GOOGLE_API_KEY")
        base_url_value = _coalesce(secret_vars, ("GOOGLE_GEMINI_BASE_URL", "GEMINI_BASE_URL")) or base_url_value
        model_value = _coalesce(secret_vars, ("GEMINI_MODEL",)) or model_value
        overlay["GEMINI_API_KEY"] = key
        overlay["GOOGLE_API_KEY"] = key
        if base_url_value:
            overlay["GOOGLE_GEMINI_BASE_URL"] = base_url_value
            overlay["GEMINI_BASE_URL"] = base_url_value
        if model_value:
            overlay["GEMINI_MODEL"] = model_value
        return overlay

    raise ProviderValidationError(f"unsupported provider family {family!r}")


def load_provider_secret_vars(provider: Provider) -> dict[str, str]:
    vars = parse_env_file(provider.secret_file)
    if vars and all(re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) for key in vars):
        return vars
    try:
        raw = Path(provider.secret_file).expanduser().read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    secret_text = _strip_secret_text(raw)
    if not secret_text.strip():
        return {}
    return {_secret_env_key(provider): secret_text}
