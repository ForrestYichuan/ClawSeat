"""Per-project binding SSOT (C2).

`~/.agents/tasks/<project>/PROJECT_BINDING.toml` is the single source of
truth for a project's external bindings — currently its Feishu group,
sender identity, OpenClaw koder overlay target, and mention-gate
policy. The file is written by ``agent_admin project bind`` (and
anything else that understands this schema) and is read by every Feishu
closeout path via ``_feishu.resolve_feishu_group_strict(project)``.

Why this file (and not WORKSPACE_CONTRACT.toml):

1. WORKSPACE_CONTRACT.toml is **regenerated** by the framework during
   bootstrap/reconfigure. Fields not in the generator's payload get
   silently erased on regeneration. Binding data is out-of-band and
   must survive those rewrites.
2. Bindings are per-project, not per-seat. A project contract lives
   under ``<workspace>/<seat>/`` and is naturally per-seat; binding
   belongs one level up.
3. Having a clear, standalone file makes it obvious to an operator
   what is bound where, and makes drift (bindings without a project,
   stale bindings after project delete) trivially detectable.

Schema (v3):

    version = 3
    project = "install"
    feishu_group_id = "oc_b0386423ec11582696a3079ab2ab89ba"
    feishu_group_name = "ClawSeat Squad"   # enriched from lark-cli chats list
    feishu_external = false                # cross-tenant flag from lark-cli
    feishu_sender_app_id = "cli_a96abcca2e78dbc2"
    feishu_sender_mode = "user"
    openclaw_koder_agent = "yu"
    tools_isolation = "shared-real-home"
    gemini_account_email = ""
    codex_account_email = ""
    require_mention = false
    bound_at = "2026-04-21T16:45:16+00:00"
    bound_by = "koder"         # optional — seat that wrote the binding

Unknown fields are preserved on rewrite so future schema extensions
don't silently drop operator-authored metadata.
Legacy ``feishu_bot_account`` is accepted on read and mapped to the new
v3 fields when a binding is rewritten.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from real_home import real_user_home

import sys as _sys, pathlib as _pl
_pb_scripts = str(_pl.Path(__file__).resolve().parent.parent / "scripts")
if _pb_scripts not in _sys.path: _sys.path.insert(0, _pb_scripts)
from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load

BINDING_SCHEMA_VERSION = 3
BINDING_FILE_NAME = "PROJECT_BINDING.toml"

_BINDING_KNOWN_FIELDS = {
    "version",
    "project",
    "feishu_group_id",
    "feishu_group_name",
    "feishu_external",
    "feishu_sender_app_id",
    "feishu_sender_mode",
    "openclaw_koder_agent",
    "feishu_bot_account",
    "tools_isolation",
    "gemini_account_email",
    "codex_account_email",
    "require_mention",
    "bound_at",
    "bound_by",
}

# Canonical Feishu group id: `oc_` + alphanumerics/underscore/hyphen. Same
# regex as _feishu._FEISHU_GROUP_ID_RE — duplicated here to avoid a cycle
# (this module is imported by agent_admin, which lives outside the
# gstack-harness scripts namespace where _feishu sits).
_FEISHU_GROUP_ID_RE = re.compile(r"^oc_[A-Za-z0-9_-]+$")


class ProjectBindingError(ValueError):
    """Raised on malformed, missing, or mismatched project bindings."""


@dataclass
class ProjectBinding:
    project: str
    feishu_group_id: str
    feishu_group_name: str = ""   # display name from lark-cli chats list
    feishu_external: bool = False  # whether the chat spans multiple tenants
    feishu_sender_app_id: str = ""   # lark-cli app id (sender identity)
    feishu_sender_mode: str = "auto"  # user | bot | auto
    openclaw_koder_agent: str = ""    # OpenClaw agent that gets koder overlay
    feishu_bot_account: str = ""      # legacy alias; kept for compat only
    tools_isolation: str = "shared-real-home"  # shared-real-home | per-project
    gemini_account_email: str = ""
    codex_account_email: str = ""
    require_mention: bool = False
    bound_at: str = ""
    bound_by: str = ""
    version: int = BINDING_SCHEMA_VERSION
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.project = validate_project_name(self.project)
        group_id = (self.feishu_group_id or "").strip()
        if group_id:
            self.feishu_group_id = validate_feishu_group_id(group_id)
        else:
            self.feishu_group_id = ""
        self.feishu_group_name = self.feishu_group_name.strip()
        self.feishu_external = bool(self.feishu_external)
        self.feishu_sender_app_id = self.feishu_sender_app_id.strip()
        self.feishu_sender_mode = _normalize_sender_mode(self.feishu_sender_mode)
        self.openclaw_koder_agent = self.openclaw_koder_agent.strip()
        self.feishu_bot_account = self.feishu_bot_account.strip()
        self.tools_isolation = _normalize_tools_isolation(self.tools_isolation)
        self.gemini_account_email = self.gemini_account_email.strip()
        self.codex_account_email = self.codex_account_email.strip()
        if self.feishu_bot_account and not (self.feishu_sender_app_id or self.openclaw_koder_agent):
            if self.feishu_bot_account.startswith("cli_"):
                self.feishu_sender_app_id = self.feishu_bot_account
            else:
                self.openclaw_koder_agent = self.feishu_bot_account
        if not self.feishu_bot_account:
            self.feishu_bot_account = self.feishu_sender_app_id or self.openclaw_koder_agent
        self.require_mention = bool(self.require_mention)
        self.bound_at = self.bound_at.strip()
        self.bound_by = self.bound_by.strip()
        self.version = max(int(self.version or BINDING_SCHEMA_VERSION), BINDING_SCHEMA_VERSION)

    def as_toml(self) -> str:
        """Serialize to TOML. Deterministic field order for diff readability."""
        lines = [
            f"version = {self.version}",
            f'project = "{_escape(self.project)}"',
            f'feishu_group_id = "{_escape(self.feishu_group_id)}"',
            f'feishu_group_name = "{_escape(self.feishu_group_name)}"',
            f"feishu_external = {'true' if self.feishu_external else 'false'}",
            f'feishu_sender_app_id = "{_escape(self.feishu_sender_app_id)}"',
            f'feishu_sender_mode = "{_escape(self.feishu_sender_mode)}"',
            f'openclaw_koder_agent = "{_escape(self.openclaw_koder_agent)}"',
            f'tools_isolation = "{_escape(self.tools_isolation)}"',
            f'gemini_account_email = "{_escape(self.gemini_account_email)}"',
            f'codex_account_email = "{_escape(self.codex_account_email)}"',
            f"require_mention = {'true' if self.require_mention else 'false'}",
            f'bound_at = "{_escape(self.bound_at)}"',
        ]
        if self.bound_by:
            lines.append(f'bound_by = "{_escape(self.bound_by)}"')
        for key in sorted(self.extras):
            lines.append(_format_extra(key, self.extras[key]))
        return "\n".join(lines) + "\n"

    @classmethod
    def from_toml(
        cls,
        data: dict[str, Any],
        *,
        fallback_project: str | None = None,
    ) -> "ProjectBinding":
        raw = dict(data)
        legacy = str(raw.pop("feishu_bot_account", "")).strip()
        if legacy and not (str(raw.get("feishu_sender_app_id", "")).strip() or str(raw.get("openclaw_koder_agent", "")).strip()):
            if legacy.startswith("cli_"):
                raw["feishu_sender_app_id"] = legacy
            else:
                raw["openclaw_koder_agent"] = legacy

        project = str(raw.get("project", fallback_project or "")).strip()
        binding = cls(
            project=project,
            feishu_group_id=str(raw.get("feishu_group_id", "")).strip(),
            feishu_group_name=str(raw.get("feishu_group_name", "")).strip(),
            feishu_external=bool(raw.get("feishu_external", False)),
            feishu_sender_app_id=str(raw.get("feishu_sender_app_id", "")).strip(),
            feishu_sender_mode=str(raw.get("feishu_sender_mode", "auto")).strip() or "auto",
            openclaw_koder_agent=str(raw.get("openclaw_koder_agent", "")).strip(),
            feishu_bot_account=legacy,
            tools_isolation=str(raw.get("tools_isolation", "shared-real-home")).strip() or "shared-real-home",
            gemini_account_email=str(raw.get("gemini_account_email", "")).strip(),
            codex_account_email=str(raw.get("codex_account_email", "")).strip(),
            require_mention=bool(raw.get("require_mention", False)),
            bound_at=str(raw.get("bound_at", "")).strip(),
            bound_by=str(raw.get("bound_by", "")).strip(),
            version=int(raw.get("version", BINDING_SCHEMA_VERSION)),
            extras={k: v for k, v in raw.items() if k not in _BINDING_KNOWN_FIELDS},
        )
        return binding


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_extra(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key} = {value}"
    if isinstance(value, str):
        return f'{key} = "{_escape(value)}"'
    raise ProjectBindingError(
        f"cannot serialize extra key {key!r} of type {type(value).__name__} "
        "back to TOML; bindings only support scalar string/int/bool extras"
    )


def _normalize_sender_mode(mode: str) -> str:
    value = (mode or "").strip().lower()
    if not value:
        return "auto"
    if value not in {"user", "bot", "auto"}:
        raise ProjectBindingError(
            f"invalid feishu_sender_mode {mode!r}: must be one of user, bot, auto"
        )
    return value


def _normalize_tools_isolation(value: str) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        return "shared-real-home"
    if normalized not in {"shared-real-home", "per-project"}:
        raise ProjectBindingError(
            f"invalid tools_isolation {value!r}: must be one of shared-real-home, per-project"
        )
    return normalized


def validate_feishu_group_id(group_id: str) -> str:
    """Return the stripped id if valid, else raise ProjectBindingError."""
    value = (group_id or "").strip()
    if not _FEISHU_GROUP_ID_RE.match(value):
        raise ProjectBindingError(
            f"invalid Feishu group id {group_id!r}: must match 'oc_<alphanum>' "
            "(e.g. oc_b0386423ec11582696a3079ab2ab89ba)"
        )
    return value


def validate_project_name(project: str) -> str:
    value = (project or "").strip()
    if not value:
        raise ProjectBindingError("project name cannot be empty")
    # Project name lands in filesystem paths, keep it conservative.
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", value):
        raise ProjectBindingError(
            f"invalid project name {project!r}: must start with alphanumeric "
            "and contain only alphanumerics, dot, hyphen, or underscore"
        )
    return value


# ── Path helpers ──────────────────────────────────────────────────────


def bindings_root(home: Path | None = None) -> Path:
    """Parent directory that contains each project's binding file."""
    return (home or real_user_home()) / ".agents" / "tasks"


def binding_path(project: str, home: Path | None = None) -> Path:
    """Return ``~/.agents/tasks/<project>/PROJECT_BINDING.toml``."""
    return bindings_root(home) / validate_project_name(project) / BINDING_FILE_NAME


# ── Read ──────────────────────────────────────────────────────────────


def load_binding(project: str, *, home: Path | None = None) -> ProjectBinding | None:
    """Return the binding for ``project`` or ``None`` if the file is missing."""
    path = binding_path(project, home=home)
    if not path.exists():
        return None
    try:
        raw = _toml_loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # parse errors should not be silent
        raise ProjectBindingError(f"cannot parse {path}: {exc}") from exc

    declared_project = str(raw.get("project", "")).strip()
    if declared_project and declared_project != validate_project_name(project):
        raise ProjectBindingError(
            f"{path} declares project={declared_project!r} but was loaded for "
            f"project={project!r}; the file is in the wrong directory"
        )
    group_id = str(raw.get("feishu_group_id", "")).strip()
    if group_id:
        validate_feishu_group_id(group_id)
    if not declared_project:
        raw["project"] = project
    return ProjectBinding.from_toml(raw, fallback_project=project)


# ── Write ─────────────────────────────────────────────────────────────


def write_binding(
    binding: ProjectBinding,
    *,
    home: Path | None = None,
    bound_at: str | None = None,
) -> Path:
    """Persist ``binding`` to disk, creating parent dirs. Returns the path."""
    validate_project_name(binding.project)
    validate_feishu_group_id(binding.feishu_group_id)
    if not binding.bound_at:
        binding.bound_at = bound_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = binding_path(binding.project, home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic-ish write: write tmp + rename. Permissions 0o644 (non-secret).
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(binding.as_toml(), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass
    return path


def bind_project(
    *,
    project: str,
    feishu_group_id: str,
    feishu_group_name: str = "",
    feishu_external: bool = False,
    feishu_sender_app_id: str = "",
    feishu_sender_mode: str = "auto",
    openclaw_koder_agent: str = "",
    feishu_bot_account: str = "",
    tools_isolation: str = "shared-real-home",
    gemini_account_email: str = "",
    codex_account_email: str = "",
    require_mention: bool = False,
    bound_by: str = "",
    home: Path | None = None,
) -> Path:
    """Convenience constructor → write. Returns the written path."""
    binding = ProjectBinding(
        project=validate_project_name(project),
        feishu_group_id=validate_feishu_group_id(feishu_group_id),
        feishu_group_name=feishu_group_name.strip(),
        feishu_external=feishu_external,
        feishu_sender_app_id=feishu_sender_app_id.strip(),
        feishu_sender_mode=feishu_sender_mode,
        openclaw_koder_agent=openclaw_koder_agent.strip(),
        feishu_bot_account=feishu_bot_account.strip(),
        tools_isolation=tools_isolation,
        gemini_account_email=gemini_account_email.strip(),
        codex_account_email=codex_account_email.strip(),
        require_mention=require_mention,
        bound_by=bound_by.strip(),
    )
    return write_binding(binding, home=home)


def fetch_chat_metadata(group_id: str) -> tuple[str, bool]:
    """Call ``lark-cli im chats list`` and return ``(name, external)`` for group_id.

    Returns ``("", False)`` when lark-cli is unavailable, auth fails, or the
    group is not found — enrichment is best-effort and must never block bind.
    """
    lark_cli = shutil.which("lark-cli")
    if not lark_cli:
        return ("", False)
    try:
        result = subprocess.run(
            [lark_cli, "--as", "user", "im", "chats", "list",
             "--format", "json", "--page-all"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HOME": str(real_user_home())},
        )
        if result.returncode != 0:
            return ("", False)
        data = json.loads(result.stdout)
        for item in data.get("data", {}).get("items", []):
            if item.get("chat_id") == group_id:
                return (str(item.get("name", "")).strip(), bool(item.get("external", False)))
    except Exception:
        return ("", False)
    return ("", False)


def list_bindings(*, home: Path | None = None) -> list[ProjectBinding]:
    """Return every parseable binding under ``~/.agents/tasks/*/``."""
    root = bindings_root(home)
    if not root.exists():
        return []
    results: list[ProjectBinding] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            binding = load_binding(child.name, home=home)
        except ProjectBindingError:
            continue
        if binding is not None:
            results.append(binding)
    return results


# ── Reverse lookup (chat_id → project) ────────────────────────────────
# Used by koder at message-in time. For each incoming Feishu message the
# chat_id is known (from the message event); koder must figure out which
# project this session is bound to. Under the v0.4 A-track (enforced
# distinct groups per project), the chat_id → project map is injective.

def resolve_project_from_chat_id(
    chat_id: str,
    *,
    home: Path | None = None,
) -> ProjectBinding | None:
    """Return the binding whose ``feishu_group_id`` matches ``chat_id``.

    Returns None when no binding matches (koder should treat this as
    "unknown chat; prompt the operator to run `cs install` or ignore").
    Raises ProjectBindingError if two or more projects claim the same
    chat_id — v0.4 A-track forbids that. Repair by updating one of the
    offending PROJECT_BINDING.toml files.
    """
    chat_id = (chat_id or "").strip()
    if not chat_id:
        return None
    hits = [b for b in list_bindings(home=home) if b.feishu_group_id == chat_id]
    if not hits:
        return None
    if len(hits) > 1:
        names = ", ".join(sorted(h.project for h in hits))
        raise ProjectBindingError(
            f"chat_id {chat_id} is bound to multiple projects: {names}. "
            "v0.4 requires one project per Feishu group; "
            "edit PROJECT_BINDING.toml for all but one to resolve."
        )
    return hits[0]


def chat_id_index(*, home: Path | None = None) -> dict[str, str]:
    """Build `{chat_id: project}` from all bindings. Caller responsible for
    deciding whether to cache or re-scan per request.

    Raises ProjectBindingError on the same duplicate-chat_id condition as
    ``resolve_project_from_chat_id``.
    """
    index: dict[str, str] = {}
    for binding in list_bindings(home=home):
        if not binding.feishu_group_id:
            continue
        prior = index.get(binding.feishu_group_id)
        if prior is not None and prior != binding.project:
            raise ProjectBindingError(
                f"chat_id {binding.feishu_group_id} is bound to both "
                f"{prior!r} and {binding.project!r}; "
                "v0.4 requires one project per Feishu group"
            )
        index[binding.feishu_group_id] = binding.project
    return index
