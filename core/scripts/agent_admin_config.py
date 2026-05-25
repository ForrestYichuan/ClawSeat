"""Canonical session/runtime auth configuration for ClawSeat.

This module is the SSOT for:

1. Session-level `(tool, auth_mode, provider)` state — used by engineer
   profiles, session records, templates, migration, and validation.
   These `auth_mode` values are canonical state values, not the lower-level
   execution labels accepted by `core/launchers/agent-launcher.sh`.

2. Launcher auth translation — `LAUNCHER_AUTH_MAP` and `resolve_launcher_auth()`
   map each `(tool, auth_mode, provider)` triple to the launcher `--auth` label.
   Examples:
   - `codex/oauth/openai` → `--auth chatgpt`
   - `gemini/api/google-api-key` → `--auth primary`
   - `claude/api/ark` → `--auth custom` (live path)
   - `claude/ccr/ccr-local` → `--auth custom` (live path)

3. Launcher secret-file targets — `LAUNCHER_SECRET_TARGETS` and
   `resolve_launcher_secret_target()` map `(tool, launcher_auth)` to the
   operator-home-relative secret file path that must be seeded before launch.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


_TOOL_BIN_SOURCES: dict[str, str] = {}


# ── Sandbox HOME resolution ───────────────────────────────────────────────────
#
# Claude Code seats run inside a sandbox HOME at
#   ~/.agents/runtime/identities/<tool>/<auth>/<identity>/home/
# so Path.home() inside a seat returns THAT, not the operator's real HOME.
# All agent_admin path resolution must use the effective home to avoid
# pointing at sandbox-local artifacts that don't exist there.
#
# Canonical implementation lives in core/lib/real_home.py; this module
# re-exports the helpers (with their historical underscore-prefix names)
# so existing agent_admin_* callers keep working.

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_LIB = str(_REPO_ROOT / "core" / "lib")
if _CORE_LIB not in sys.path:
    sys.path.insert(0, _CORE_LIB)
from real_home import (  # noqa: E402
    is_sandbox_home as _is_sandbox_home,
    real_user_home as _real_user_home,
)


def _resolve_effective_home() -> Path:
    """Return the effective HOME for agent_admin path resolution.

    Respects CLAWSEAT_SANDBOX_HOME_STRICT=1 (force sandbox, for tests)
    and delegates to _real_user_home() otherwise.
    """
    return _real_user_home()


def _resolve_tool_bin(name: str) -> str:
    """Resolve a backend CLI binary path.

    Probe order: PATH (via shutil.which) → ClawSeat local tool entries →
    /opt/homebrew/bin → bare name.
    The "bare" fallback means `shell exec` will rely on the caller's PATH
    at runtime; if the CLI is not installed, execution fails with a
    cryptic "command not found" instead of a clear preflight error.
    `unresolved_tool_bins()` exposes that state so startup code
    (preflight, agent_admin main) can surface a visible warning.
    """
    resolved = shutil.which(name)
    if resolved:
        _TOOL_BIN_SOURCES[name] = "path"
        return resolved
    home = _resolve_effective_home()
    for candidate in (
        home / "AI" / "工具入口" / name,
        home / "AI" / "toolchains" / "npm-global" / "bin" / name,
    ):
        if candidate.exists():
            _TOOL_BIN_SOURCES[name] = "clawseat"
            return str(candidate)
    homebrew = f"/opt/homebrew/bin/{name}"
    if os.path.exists(homebrew):
        _TOOL_BIN_SOURCES[name] = "homebrew"
        return homebrew
    _TOOL_BIN_SOURCES[name] = "bare"
    return name


def tool_bin_source(name: str) -> str:
    """Return how TOOL_BINARIES[name] was resolved: path | homebrew | bare.
    Empty string if the name was never probed."""
    return _TOOL_BIN_SOURCES.get(name, "")


def unresolved_tool_bins() -> list[str]:
    """List of tools whose binary fell back to the bare name (i.e. could
    not be located on disk at import time). These will fail at exec time
    unless the user's PATH picks them up."""
    return sorted(name for name, src in _TOOL_BIN_SOURCES.items() if src == "bare")


def check_script_deps() -> list[str]:
    """Return list of missing Python pip-package names required by ClawSeat scripts.

    These packages must be importable for agent_admin_brief, complete_handoff,
    and related scripts to function. Empty return means all deps are present.
    Call before claiming seat readiness; if non-empty, the seat should report
    BLOCKED rather than summarising queue state from partial reads.
    """
    missing: list[str] = []
    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append("PyYAML")
    # tomllib is stdlib in Python 3.11+; tomli is the backport for 3.9/3.10
    try:
        import tomllib  # noqa: F401
    except ImportError:
        try:
            import tomli  # noqa: F401
        except ImportError:
            missing.append("tomli")
    return missing


# Ordered probe list for find_clawseat_python() — Homebrew-managed interpreters
# that reliably ship with the required packages on macOS.
_CLAWSEAT_PYTHON_FALLBACK_CANDIDATES: list[str] = [
    "/opt/homebrew/opt/python@3.12/bin/python3.12",
    "/opt/homebrew/opt/python@3.11/bin/python3.11",
    "/opt/homebrew/bin/python3",
]


def _can_import_pytest(exe: str) -> bool:
    """Return True if *exe* can import pytest."""
    try:
        r = subprocess.run(
            [exe, "-c", "import pytest"],
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _exe_is_clawseat_ready(exe: str) -> bool:
    """Return True if *exe* has tomllib/tomli AND pytest installed."""
    # check_script_deps tests the current process; here we probe a specific exe
    for mod_group in [["tomllib", "tomli"], ["pytest"]]:
        found = False
        for mod in mod_group:
            try:
                r = subprocess.run(
                    [exe, "-c", f"import {mod}"],
                    capture_output=True,
                    timeout=5,
                )
                if r.returncode == 0:
                    found = True
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
        if not found:
            return False
    return True


def find_clawseat_python() -> str:
    """Return a Python interpreter ready to run ClawSeat acceptance tests.

    Requires: tomllib/tomli (TOML parsing) + pytest (test runner).
    Checks sys.executable first, then probes _CLAWSEAT_PYTHON_FALLBACK_CANDIDATES.
    Falls back to sys.executable when no better interpreter is found so callers
    always receive a usable path (they can separately report missing deps).

    Policy: mechanical acceptance commands must use this path rather than bare
    ``python3`` / ``/usr/bin/python3`` so they are deterministic across Claude/
    Codex/Gemini isolated shells that may not have pytest installed.
    """
    if _exe_is_clawseat_ready(sys.executable):
        return sys.executable
    for candidate in _CLAWSEAT_PYTHON_FALLBACK_CANDIDATES:
        if candidate != sys.executable and _exe_is_clawseat_ready(candidate):
            return candidate
    return sys.executable


def _default_path() -> str:
    override = os.environ.get("CLAWSEAT_DEFAULT_PATH")
    if override:
        return override
    base = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    if sys.platform == "darwin":
        home = _resolve_effective_home()
        ai_root = home / "AI"
        tool_entry = ai_root / "工具入口"
        npm_global = ai_root / "toolchains" / "npm-global" / "bin"
        return f"{tool_entry}:{npm_global}:/opt/homebrew/bin:{base}"
    return base


REPO_ROOT = Path(__file__).resolve().parents[2]
HOME = _resolve_effective_home()
AGENTS_ROOT = HOME / ".agents"
PROJECTS_ROOT = AGENTS_ROOT / "projects"
ENGINEERS_ROOT = AGENTS_ROOT / "engineers"
SESSIONS_ROOT = AGENTS_ROOT / "sessions"
WORKSPACES_ROOT = AGENTS_ROOT / "workspaces"
RUNTIME_ROOT = AGENTS_ROOT / "runtime" / "identities"
SECRETS_ROOT = AGENTS_ROOT / "secrets"
LEGACY_ROOT = AGENTS_ROOT / "legacy"
STATE_ROOT = AGENTS_ROOT / "state"
CURRENT_PROJECT_PATH = STATE_ROOT / "current_project"
TEMPLATES_ROOT = REPO_ROOT / "core" / "templates"
DEFAULT_PATH = _default_path()
AGENTCTL_SH = REPO_ROOT / "core" / "shell-scripts" / "agentctl.sh"
SEND_AND_VERIFY_SH = REPO_ROOT / "core" / "shell-scripts" / "send-and-verify.sh"
HARNESS_PROFILE_ROOT = REPO_ROOT / "core" / "skills" / "gstack-harness" / "assets" / "profiles"

LEGACY_IDENTITIES_ROOT = HOME / ".agent-runtime" / "identities"
LEGACY_SECRETS_ROOT = HOME / ".agent-runtime" / "secrets"
LEGACY_GEMINI_SANDBOXES = [
    REPO_ROOT / ".gemini-E-sandbox",
    REPO_ROOT / ".gemini-F-sandbox",
    REPO_ROOT / ".gemini-image-sandbox",
]

LEGACY_CONFIG_ROOT = REPO_ROOT / ".agent" / "config"
LEGACY_ASSIGNMENTS_PATH = LEGACY_CONFIG_ROOT / "engineer-assignments.toml"
LEGACY_IDENTITIES_PATH = LEGACY_CONFIG_ROOT / "auth-identities.toml"

# Legacy compatibility defaults for the historical "coding" roster.
# These are not the canonical role-first runtime model for new ClawSeat projects;
# new projects should come from templates/profiles such as gstack-harness and use
# `memory / planner / builder / patrol / designer (+reviewer if engineering template)`.
# Note: koder is an OpenClaw agent (overlay), not a ClawSeat tmux seat; see
# docs/rfc/V2-VOCAB-DRIFT-AUDIT.md.
PROJECT_DEFAULTS = {
    "coding": {
        "repo_root": str(REPO_ROOT),
        "monitor_session": "project-coding-monitor",
        "engineers": [
            "engineer-a",
            "engineer-b",
            "engineer-c",
            "engineer-d",
            "engineer-e",
            "engineer-f",
            "engineer-g",
            "engineer-h",
            "engineer-pm",
        ],
        "monitor_engineers": [
            "engineer-a",
            "engineer-b",
            "engineer-c",
            "engineer-d",
            "engineer-g",
            "engineer-h",
            "engineer-e",
            "engineer-f",
        ],
    }
}

# Historical engineer definitions kept only for migration, recovery, and old
# project compatibility. Do not copy these ids into new profiles unless you are
# explicitly operating a legacy engineer-* project.
LEGACY_ENGINEERS = {
    "engineer-a": {
        "project": "coding",
        "tool": "codex",
        "auth_mode": "oauth",
        "provider": "openai",
        "legacy_workspace": "",
        "legacy_session": "codex-A",
        "launch_args": ["--full-auto"],
        "monitor": True,
        "seed_runtime": str(LEGACY_IDENTITIES_ROOT / "codex" / "oauth" / "main"),
        "seed_secret": "",
    },
    "engineer-b": {
        "project": "coding",
        "tool": "claude",
        "auth_mode": "oauth",
        "provider": "anthropic",
        "legacy_workspace": str(HOME / ".b-workspace"),
        "legacy_session": "claude-B",
        "launch_args": [],
        "monitor": True,
        "seed_runtime": str(LEGACY_IDENTITIES_ROOT / "claude" / "oauth" / "main"),
        "seed_secret": "",
    },
    "engineer-c": {
        "project": "coding",
        "tool": "claude",
        "auth_mode": "api",
        "provider": "xcode-best",
        "legacy_workspace": str(REPO_ROOT / ".c-workspace"),
        "legacy_session": "claude-C",
        "launch_args": [],
        "monitor": True,
        "seed_runtime": str(LEGACY_IDENTITIES_ROOT / "claude" / "api" / "xcode"),
        "seed_secret": str(LEGACY_SECRETS_ROOT / "claude" / "xcode.env"),
    },
    "engineer-d": {
        "project": "coding",
        "tool": "claude",
        "auth_mode": "api",
        "provider": "xcode-best",
        "legacy_workspace": str(REPO_ROOT / ".d-workspace"),
        "legacy_session": "claude-D",
        "launch_args": [],
        "monitor": True,
        "seed_runtime": str(LEGACY_IDENTITIES_ROOT / "claude" / "api" / "xcode"),
        "seed_secret": str(LEGACY_SECRETS_ROOT / "claude" / "xcode.env"),
    },
    "engineer-e": {
        "project": "coding",
        "tool": "gemini",
        "auth_mode": "api",
        "provider": "google-api-key",
        "legacy_workspace": "",
        "legacy_session": "gemini-E",
        "launch_args": [],
        "monitor": True,
        "seed_runtime": str(LEGACY_IDENTITIES_ROOT / "gemini" / "api" / "primary"),
        "seed_secret": str(LEGACY_SECRETS_ROOT / "gemini" / "primary.env"),
    },
    "engineer-f": {
        "project": "coding",
        "tool": "gemini",
        "auth_mode": "oauth",
        "provider": "google",
        "legacy_workspace": "",
        "legacy_session": "gemini-F",
        "launch_args": [],
        "monitor": True,
        "seed_runtime": str(LEGACY_IDENTITIES_ROOT / "gemini" / "oauth" / "main"),
        "seed_secret": "",
    },
    "engineer-g": {
        "project": "coding",
        "tool": "claude",
        "auth_mode": "api",
        "provider": "minimax",
        "legacy_workspace": str(REPO_ROOT / ".g-workspace"),
        "legacy_session": "claude-G",
        "launch_args": [],
        "monitor": True,
        "seed_runtime": str(LEGACY_IDENTITIES_ROOT / "claude" / "api" / "minimax"),
        "seed_secret": str(LEGACY_SECRETS_ROOT / "claude" / "minimax.env"),
    },
    "engineer-h": {
        "project": "coding",
        "tool": "claude",
        "auth_mode": "api",
        "provider": "minimax",
        "legacy_workspace": str(REPO_ROOT / ".h-workspace"),
        "legacy_session": "claude-H",
        "launch_args": [],
        "monitor": True,
        "seed_runtime": str(LEGACY_IDENTITIES_ROOT / "claude" / "api" / "minimax"),
        "seed_secret": str(LEGACY_SECRETS_ROOT / "claude" / "minimax.env"),
    },
    "engineer-pm": {
        "project": "coding",
        "tool": "codex",
        "auth_mode": "oauth",
        "provider": "openai",
        "legacy_workspace": str(REPO_ROOT / ".pm-workspace"),
        "legacy_session": "codex-PM",
        "launch_args": [],
        "monitor": False,
        "seed_runtime": str(LEGACY_IDENTITIES_ROOT / "codex" / "oauth" / "main"),
        "seed_secret": "",
    },
}

TOOL_BINARIES = {
    "codex": _resolve_tool_bin("codex"),
    "claude": _resolve_tool_bin("claude"),
    "gemini": _resolve_tool_bin("gemini"),
}

DEFAULT_TOOL_ARGS = {
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
    "claude": ["--dangerously-skip-permissions"],
    "gemini": ["--approval-mode=yolo"],
}

DEFAULT_CCR_BASE_URL = "http://127.0.0.1:3456"

DEFAULT_TOOL_BASE_URLS = {
    "claude": "https://api.anthropic.com",
    "codex": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com",
}

PROVIDER_DEFAULTS = {
    "claude": {
        "anthropic-console": {
            "base_url": DEFAULT_TOOL_BASE_URLS["claude"],
            "url_markers": ("api.anthropic.com",),
        },
        "minimax": {
            "base_url": "https://api.minimaxi.com/anthropic",
            "default_model": "MiniMax-M2.7-highspeed",
            "url_markers": ("minimaxi.com",),
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com/anthropic",
            "default_model": "deepseek-v4-pro[1M]",
            "url_markers": ("deepseek.com",),
        },
        "ark": {
            "base_url": "https://ark.cn-beijing.volces.com/api/coding",
            "default_model": "ark-code-latest",
            "url_markers": ("volces.com",),
        },
        "xcode-best": {
            "base_url": "https://xcode.best",
            "default_model": "gpt-5.5",
            "url_markers": ("xcode.best",),
        },
        "ccr-local": {
            "base_url": DEFAULT_CCR_BASE_URL,
            "url_markers": ("127.0.0.1:3456", "localhost:3456"),
        },
    },
    "codex": {
        "openai": {
            "base_url": DEFAULT_TOOL_BASE_URLS["codex"],
            "url_markers": ("api.openai.com",),
        },
        "xcode-best": {
            "base_url": "https://api.xcode.best/v1",
            "default_model": "gpt-5.5",
            "url_markers": ("xcode.best",),
        },
    },
    "gemini": {
        "google-api-key": {
            "base_url": DEFAULT_TOOL_BASE_URLS["gemini"],
            "url_markers": ("generativelanguage.googleapis.com",),
        },
    },
}


def tool_default_base_url(tool: str) -> str | None:
    value = DEFAULT_TOOL_BASE_URLS.get(tool)
    if isinstance(value, str) and value:
        return value
    return None


def _provider_defaults(tool: str, provider: str) -> dict[str, object]:
    raw = PROVIDER_DEFAULTS.get(tool, {}).get(provider, {})
    return dict(raw) if isinstance(raw, dict) else {}


def provider_default_base_url(tool: str, provider: str) -> str | None:
    value = _provider_defaults(tool, provider).get("base_url")
    if isinstance(value, str) and value:
        return value
    return None


def provider_default_model(tool: str, provider: str) -> str | None:
    value = _provider_defaults(tool, provider).get("default_model")
    if isinstance(value, str) and value:
        return value
    return None


def _provider_url_markers(tool: str, provider: str) -> tuple[str, ...]:
    value = _provider_defaults(tool, provider).get("url_markers", ())
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def provider_url_matches(tool: str, provider: str, base_url: str) -> bool:
    candidate = str(base_url or "").strip().lower()
    if not candidate:
        return False
    return any(marker.lower() in candidate for marker in _provider_url_markers(tool, provider))


XCODE_PROVIDER_ENDPOINT_RULES = {
    "claude": {
        "xcode-best": {
            "env_var": "ANTHROPIC_BASE_URL",
            "base_url": PROVIDER_DEFAULTS["claude"]["xcode-best"]["base_url"],
        }
    },
    "codex": {
        "xcode-best": {
            "base_url": PROVIDER_DEFAULTS["codex"]["xcode-best"]["base_url"],
        }
    },
}

CLAUDE_API_PROVIDER_CONFIGS = {
    "minimax": {
        "model": PROVIDER_DEFAULTS["claude"]["minimax"]["default_model"],
        "base_url": PROVIDER_DEFAULTS["claude"]["minimax"]["base_url"],
        # MiniMax Anthropic-compatible endpoint requires ANTHROPIC_AUTH_TOKEN
        # instead of ANTHROPIC_API_KEY, plus extended timeout and traffic control.
        "auth_token_var": "ANTHROPIC_AUTH_TOKEN",
        "extra_env": {
            "API_TIMEOUT_MS": "3000000",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
    },
    "deepseek": {
        "model": PROVIDER_DEFAULTS["claude"]["deepseek"]["default_model"],
        "base_url": PROVIDER_DEFAULTS["claude"]["deepseek"]["base_url"],
        "auth_token_var": "DEEPSEEK_API_KEY",
        "extra_env": {
            "API_TIMEOUT_MS": "3000000",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
    },
    "ark": {
        "model": PROVIDER_DEFAULTS["claude"]["ark"]["default_model"],
        "base_url": PROVIDER_DEFAULTS["claude"]["ark"]["base_url"],
        # ARK exposes an Anthropic-compatible Claude Code endpoint but
        # operators already store the upstream secret as ARK_API_KEY in
        # ~/.agent-runtime/secrets/claude/ark.env. Session startup aliases
        # that name into launcher custom env at runtime.
        "auth_token_var": "ARK_API_KEY",
        "extra_env": {
            "API_TIMEOUT_MS": "3000000",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
    },
    "xcode-best": {
        "model": PROVIDER_DEFAULTS["claude"]["xcode-best"]["default_model"],
        "base_url": PROVIDER_DEFAULTS["claude"]["xcode-best"]["base_url"],
        "auth_token_var": "XCODE_BEST_API_KEY",
        "extra_env": {
            "API_TIMEOUT_MS": "3000000",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
    },
}

CODEX_API_PROVIDER_CONFIGS = {
    "xcode-best": {
        "model_provider": "api111",
        "model": PROVIDER_DEFAULTS["codex"]["xcode-best"]["default_model"],
        "model_reasoning_effort": "high",
        "disable_response_storage": True,
        "preferred_auth_method": "apikey",
        "personality": "pragmatic",
        "base_url": PROVIDER_DEFAULTS["codex"]["xcode-best"]["base_url"],
        "wire_api": "responses",
    },
}

# Gemini has no non-Google API providers today; the matrix exposes
# `api/google-api-key` which is handled via plain env-var injection (no
# config.toml rendering). This dict is kept empty to preserve structural
# symmetry with CLAUDE_/CODEX_API_PROVIDER_CONFIGS and to provide a
# documented place for future providers (e.g. google-vertex). Adding an
# entry here must be paired with an update to SUPPORTED_RUNTIME_MATRIX
# and any renderer that consumes it.
GEMINI_API_PROVIDER_CONFIGS: dict[str, dict[str, object]] = {}


# ── Codex provider schema ───────────────────────────────────────────
#
# `write_codex_api_config` used to render a raw dict into TOML. A typo in
# the dict (e.g. `disable_response_storge`) was silently dropped and the
# codex CLI started without it, which produced confusing runtime
# failures. The dataclass below pins the accepted field set; any unknown
# key raises at load time via `parse_codex_provider_config`. Renderers
# must read from the dataclass, never from a raw dict.

@dataclass(frozen=True)
class CodexProviderConfig:
    model_provider: str
    model: str
    base_url: str
    wire_api: str
    model_reasoning_effort: str | None = None
    disable_response_storage: bool | None = None
    preferred_auth_method: str | None = None
    personality: str | None = None
    name: str | None = None
    env_key: str | None = None
    requires_openai_auth: bool | None = None
    request_max_retries: int | None = None
    stream_max_retries: int | None = None
    stream_idle_timeout_ms: int | None = None
    profile_name: str | None = None


def parse_codex_provider_config(data: dict[str, Any]) -> CodexProviderConfig:
    """Convert a raw dict entry from CODEX_API_PROVIDER_CONFIGS to the
    strongly-typed dataclass. Unknown keys raise ValueError instead of
    being silently dropped — the original reason this function exists
    (audit M1 — see docs/audit-log)."""
    allowed = {field.name for field in fields(CodexProviderConfig)}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(
            f"unknown codex provider config key(s): {sorted(unknown)}; "
            f"allowed: {sorted(allowed)}"
        )
    return CodexProviderConfig(**data)


# Canonical session-level matrix. These `auth_mode` values are persisted in
# session/profile/template state and validated by agent_admin_* entry points.
# Do not replace them with launcher execution labels such as `chatgpt`,
# `primary`, `xcode`, or `custom`.
#
# Translation to launcher `--auth` happens at the execution boundary in
# `SessionService._launcher_auth_for()`. That live bridge includes
# `claude/api/ark -> custom` and `claude/ccr/ccr-local -> custom`, even though
# raw launcher case labels do not mention `ark` or `ccr`.
SUPPORTED_RUNTIME_MATRIX = {
    "claude": {
        # Legacy Keychain OAuth; maps 1:1 to launcher `--auth oauth`.
        "oauth": ("anthropic",),
        # Canonical Claude API providers. Current agent_admin startup routes
        # these through launcher `--auth custom`; legacy shell callers may
        # still use direct `anthropic-console|minimax|xcode` launcher labels.
        "api": ("xcode-best", "minimax", "deepseek", "ark", "anthropic-console"),
        # C5: long-lived token from `claude setup-token` (valid ~1 year).
        # Skips macOS Keychain entirely — avoids the per-seat popup storm
        # caused by each seat having a different HOME (upstream
        # anthropics/claude-code#43000).
        "oauth_token": ("anthropic",),
        # C5: Claude Code Router (CCR) — local proxy that multiplexes
        # Anthropic-compatible providers (Kimi / MiniMax / GLM / DeepSeek
        # / OpenRouter) behind one ANTHROPIC_BASE_URL. Keeps backend
        # seats off the user's OAuth quota and switchable per-request.
        "ccr": ("ccr-local",),
    },
    "codex": {
        "oauth": ("openai",),
        "api": ("xcode-best",),
    },
    "gemini": {
        "oauth": ("google",),
        "api": ("google-api-key",),
    },
}


# C5 auth mode families. Used by validators and by build_runtime to decide
# which env-injection branch runs. Keeping this explicit makes it easy to
# answer "does this auth mode need a secret file?" without reading the
# matrix carefully.
AUTH_MODES_REQUIRING_SECRET_FILE = frozenset({"api", "oauth_token"})
AUTH_MODES_WITHOUT_SECRET_FILE = frozenset({"oauth", "ccr"})
ALL_AUTH_MODES = AUTH_MODES_REQUIRING_SECRET_FILE | AUTH_MODES_WITHOUT_SECRET_FILE


def supported_providers(tool: str, auth_mode: str) -> tuple[str, ...]:
    return SUPPORTED_RUNTIME_MATRIX.get(tool, {}).get(auth_mode, ())


def _is_claude_api_registry_provider(provider: str) -> bool:
    """Check whether a provider name is registered in the local provider registry
    with tool=claude.  Used as a fallback by is_supported_runtime_combo so that
    operators can use saved API providers (e.g. baidu-glm, newapi-local-claude)
    without having to hardcode them in the static SUPPORTED_RUNTIME_MATRIX.

    Fails silently (returns False) if the registry is unavailable, so the
    static matrix path is always the primary gate.
    """
    try:
        from providers import get_provider  # noqa: PLC0415
        p = get_provider(provider)
        return p is not None and str(getattr(p, "tool", "") or "") == "claude"
    except Exception:  # noqa: BLE001
        return False


def is_supported_runtime_combo(tool: str, auth_mode: str, provider: str) -> bool:
    if provider in supported_providers(tool, auth_mode):
        return True
    # For claude/api, also accept providers registered in the local provider
    # registry with tool=claude.  This allows operators to register custom
    # API providers (baidu-glm, newapi-local-claude, etc.) without requiring
    # a framework update for every new Claude-compatible endpoint.
    if tool == "claude" and auth_mode == "api":
        return _is_claude_api_registry_provider(provider)
    return False


def supported_runtime_summary_lines() -> list[str]:
    lines: list[str] = []
    for tool in ("claude", "codex", "gemini"):
        tool_map = SUPPORTED_RUNTIME_MATRIX.get(tool, {})
        for auth_mode in ("oauth", "api", "oauth_token", "ccr"):
            providers = tool_map.get(auth_mode)
            if not providers:
                continue
            provider_text = ", ".join(providers)
            lines.append(f"- `{tool}` + `{auth_mode}`: {provider_text}")
    return lines


def validate_runtime_combo(
    tool: str,
    auth_mode: str,
    provider: str,
    *,
    error_cls: type[Exception] = ValueError,
    context: str | None = None,
) -> None:
    if is_supported_runtime_combo(tool, auth_mode, provider):
        return
    provider_text = ", ".join(supported_providers(tool, auth_mode)) or "none"
    prefix = f"{context}: " if context else ""
    # If the provider exists under a different mode for the same tool, add a hint.
    tool_map = SUPPORTED_RUNTIME_MATRIX.get(tool, {})
    alt_modes = [m for m, providers in tool_map.items() if m != auth_mode and provider in providers]
    hint = f" (`{provider}` is valid for {tool}/{', '.join(alt_modes)} — rerun with --mode {alt_modes[0]})" if alt_modes else ""
    raise error_cls(
        f"{prefix}unsupported runtime combination `{tool}/{auth_mode}/{provider}`. "
        f"Supported providers for `{tool}/{auth_mode}`: {provider_text}.{hint}"
    )


# ── Launcher auth translation (SSOT) ─────────────────────────────────────────
#
# Maps canonical (tool, auth_mode, provider) → launcher --auth label.
# Provider-specific entries take precedence; (tool, auth_mode, None) is the
# catch-all for a given mode. Resolution order in resolve_launcher_auth():
#   1. exact (tool, auth_mode, provider) key
#   2. (tool, auth_mode, None) fallback
#   3. raise error_cls
LAUNCHER_AUTH_MAP: dict[tuple[str, str, str | None], str] = {
    ("claude", "oauth",       None):            "oauth",
    ("claude", "oauth_token", None):            "oauth_token",
    ("claude", "ccr",         None):            "custom",
    ("claude", "api",         "minimax"):       "minimax",
    ("claude", "api",         None):            "custom",
    ("codex",  "oauth",       None):            "chatgpt",
    ("codex",  "api",         "xcode-best"):    "xcode",
    ("codex",  "api",         None):            "custom",
    ("gemini", "oauth",       None):            "oauth",
    ("gemini", "api",         "google-api-key"): "primary",
    ("gemini", "api",         None):            "custom",
}

# Maps (tool, launcher_auth) → path relative to real operator home that must
# be seeded with credentials before the launcher starts.
LAUNCHER_SECRET_TARGETS: dict[tuple[str, str], str] = {
    ("claude", "oauth_token"): ".agents/.env.global",
    ("claude", "minimax"):     ".agent-runtime/secrets/claude/minimax.env",
    ("gemini", "primary"):     ".agent-runtime/secrets/gemini/primary.env",
    ("codex",  "xcode"):       ".agent-runtime/secrets/codex/xcode.env",
}


def resolve_launcher_auth(
    tool: str,
    auth_mode: str,
    provider: str,
    *,
    error_cls: type[Exception] = ValueError,
) -> str:
    label = LAUNCHER_AUTH_MAP.get((tool, auth_mode, provider)) or LAUNCHER_AUTH_MAP.get(
        (tool, auth_mode, None)
    )
    if label is not None:
        return label
    raise error_cls(
        f"unsupported launcher auth mapping for tool={tool!r} auth_mode={auth_mode!r} "
        f"provider={provider!r}"
    )


def resolve_launcher_secret_target(
    tool: str,
    launcher_auth: str,
    *,
    real_home: "Path | None" = None,
) -> "Path | None":
    rel = LAUNCHER_SECRET_TARGETS.get((tool, launcher_auth))
    if rel is None:
        return None
    home = real_home if real_home is not None else _real_user_home()
    return home / rel
