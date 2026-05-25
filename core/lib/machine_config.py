"""P1: machine.toml schema — machine-wide singletons + OpenClaw tenant registry.

Schema: docs/schemas/v0.4-layered-model.md §3.
File:   ~/.clawseat/machine.toml  (respects CLAWSEAT_REAL_HOME for tests).
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "core" / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sys as _sys, pathlib as _pl
_mc_scripts = str(_pl.Path(__file__).resolve().parent.parent / "scripts")
if _mc_scripts not in _sys.path: _sys.path.insert(0, _mc_scripts)
from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load

from openclaw_home import discover_openclaw_home
from real_home import real_user_home


MACHINE_SCHEMA_VERSION = 1
_TENANT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

_DEFAULT_MEMORY_TOOL = "claude"
_DEFAULT_MEMORY_AUTH_MODE = "api"
_DEFAULT_MEMORY_PROVIDER = "minimax"
_DEFAULT_MEMORY_MODEL = "MiniMax-M2.7-highspeed"


class MachineConfigError(ValueError):
    """Raised for invalid machine.toml content."""


@dataclass
class MemoryService:
    role: str = "memory-oracle"
    tool: str = _DEFAULT_MEMORY_TOOL
    auth_mode: str = _DEFAULT_MEMORY_AUTH_MODE
    provider: str = _DEFAULT_MEMORY_PROVIDER
    model: str = _DEFAULT_MEMORY_MODEL
    runtime_dir: Path = field(default_factory=lambda: Path("~/.agents/runtime/memory").expanduser())
    storage_root: Path = field(default_factory=lambda: Path("~/.agents/memory").expanduser())
    launch_args: list[str] = field(default_factory=list)
    monitor: bool = True


@dataclass
class OpenClawTenant:
    name: str
    workspace: Path
    description: str = ""


@dataclass
class FeishuRouting:
    chat_id: str
    bound_projects: list[str] = field(default_factory=list)
    default_project: str = ""


@dataclass
class MachineConfig:
    version: int
    memory: MemoryService
    tenants: dict[str, OpenClawTenant]
    source_path: Path
    feishu_routing: dict[str, FeishuRouting] = field(default_factory=dict)


# ── Path helpers ─────────────────────────────────────────────────────


def default_path() -> Path:
    """~/.clawseat/machine.toml (respects CLAWSEAT_REAL_HOME for tests)."""
    return real_user_home() / ".clawseat" / "machine.toml"


def _openclaw_workspace_root() -> Path:
    # Test/debug isolation: machine_config already respects CLAWSEAT_REAL_HOME
    # for machine.toml. Keep OpenClaw auto-discovery anchored there too so
    # local host CLI state does not leak into tmp-home test fixtures.
    return discover_openclaw_home(
        home=real_user_home(),
        allow_cli=not bool(os.environ.get("CLAWSEAT_REAL_HOME")),
    )


# ── TOML serialization ────────────────────────────────────────────────


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _serialize_machine(cfg: MachineConfig) -> str:
    m = cfg.memory
    lines: list[str] = [
        f"version = {cfg.version}",
        "",
        "[services.memory]",
        f'role = "{_escape(m.role)}"',
        f'tool = "{_escape(m.tool)}"',
        f'auth_mode = "{_escape(m.auth_mode)}"',
        f'provider = "{_escape(m.provider)}"',
    ]
    if m.model:
        lines.append(f'model = "{_escape(m.model)}"')
    lines.append(f'runtime_dir = "{_escape(str(m.runtime_dir))}"')
    lines.append(f'storage_root = "{_escape(str(m.storage_root))}"')
    args_str = "[" + ", ".join(f'"{_escape(a)}"' for a in m.launch_args) + "]"
    lines.append(f"launch_args = {args_str}")
    lines.append(f"monitor = {'true' if m.monitor else 'false'}")

    for name, tenant in sorted(cfg.tenants.items()):
        lines.append("")
        lines.append(f"[openclaw_tenants.{name}]")
        lines.append(f'workspace = "{_escape(str(tenant.workspace))}"')
        if tenant.description:
            lines.append(f'description = "{_escape(tenant.description)}"')

    for chat_id, routing in sorted(cfg.feishu_routing.items()):
        lines.append("")
        lines.append(f"[feishu_routing.{chat_id}]")
        projects = "[" + ", ".join(f'"{_escape(project)}"' for project in routing.bound_projects) + "]"
        lines.append(f"bound_projects = {projects}")
        if routing.default_project:
            lines.append(f'default_project = "{_escape(routing.default_project)}"')

    return "\n".join(lines) + "\n"


# ── Parse from raw TOML dict ──────────────────────────────────────────


def _parse_memory(raw_mem: dict[str, Any]) -> MemoryService:
    def _path(key: str, default: str) -> Path:
        return Path(str(raw_mem.get(key, default))).expanduser()

    return MemoryService(
        role=str(raw_mem.get("role", "memory-oracle")),
        tool=str(raw_mem.get("tool", _DEFAULT_MEMORY_TOOL)),
        auth_mode=str(raw_mem.get("auth_mode", _DEFAULT_MEMORY_AUTH_MODE)),
        provider=str(raw_mem.get("provider", _DEFAULT_MEMORY_PROVIDER)),
        model=str(raw_mem.get("model", _DEFAULT_MEMORY_MODEL)),
        runtime_dir=_path("runtime_dir", "~/.agents/runtime/memory"),
        storage_root=_path("storage_root", "~/.agents/memory"),
        launch_args=list(raw_mem.get("launch_args", [])),
        monitor=bool(raw_mem.get("monitor", True)),
    )


def _parse_tenants(raw_tenants: dict[str, Any]) -> dict[str, OpenClawTenant]:
    tenants: dict[str, OpenClawTenant] = {}
    for name, tdata in raw_tenants.items():
        if not isinstance(tdata, dict):
            raise MachineConfigError(f"tenant {name!r}: expected a table, got {type(tdata).__name__}")
        workspace = Path(str(tdata.get("workspace", ""))).expanduser()
        tenants[name] = OpenClawTenant(
            name=name,
            workspace=workspace,
            description=str(tdata.get("description", "")),
        )
    return tenants


def _parse_feishu_routing(raw_routes: dict[str, Any]) -> dict[str, FeishuRouting]:
    routes: dict[str, FeishuRouting] = {}
    for chat_id, rdata in raw_routes.items():
        if not isinstance(rdata, dict):
            raise MachineConfigError(f"feishu_routing {chat_id!r}: expected a table, got {type(rdata).__name__}")
        bound = [
            str(project).strip()
            for project in rdata.get("bound_projects", [])
            if str(project).strip()
        ]
        default_project = str(rdata.get("default_project") or "").strip()
        routes[str(chat_id)] = FeishuRouting(
            chat_id=str(chat_id),
            bound_projects=bound,
            default_project=default_project,
        )
    return routes


def _parse_raw(raw: dict[str, Any], source_path: Path) -> MachineConfig:
    services = raw.get("services", {})
    if not isinstance(services, dict) or "memory" not in services:
        raise MachineConfigError("machine.toml must have [services.memory]")
    memory = _parse_memory(services["memory"])
    raw_tenants = raw.get("openclaw_tenants", {})
    tenants = _parse_tenants(raw_tenants if isinstance(raw_tenants, dict) else {})
    raw_routes = raw.get("feishu_routing", {})
    feishu_routing = _parse_feishu_routing(raw_routes if isinstance(raw_routes, dict) else {})
    version = int(raw.get("version", MACHINE_SCHEMA_VERSION))
    return MachineConfig(
        version=version,
        memory=memory,
        tenants=tenants,
        feishu_routing=feishu_routing,
        source_path=source_path,
    )


# ── Auto-discovery ────────────────────────────────────────────────────


def _discover_tenants() -> dict[str, OpenClawTenant]:
    """Scan OpenClaw workspaces and build an OpenClawTenant for each."""
    oc_root = _openclaw_workspace_root()
    tenants: dict[str, OpenClawTenant] = {}
    if not oc_root.exists():
        return tenants
    for child in sorted(oc_root.iterdir()):
        if not child.is_dir():
            continue
        contract = child / "WORKSPACE_CONTRACT.toml"
        if child.name.startswith("workspace-"):
            name = child.name[len("workspace-"):]
        elif contract.exists():
            name = child.name
        else:
            continue
        if not _TENANT_NAME_RE.match(name):
            continue
        tenants[name] = OpenClawTenant(name=name, workspace=child, description="")
    return tenants


def _default_machine(path: Path) -> MachineConfig:
    """Create a default MachineConfig with auto-discovered tenants."""
    tenants = _discover_tenants()
    return MachineConfig(
        version=MACHINE_SCHEMA_VERSION,
        memory=MemoryService(),
        tenants=tenants,
        feishu_routing={},
        source_path=path,
    )


# ── Public API ────────────────────────────────────────────────────────


def load_machine(path: Path | None = None) -> MachineConfig:
    """Load + parse machine.toml. Creates a default if file missing."""
    resolved = path or default_path()
    if not resolved.exists():
        cfg = _default_machine(resolved)
        write_machine(cfg, resolved)
        return cfg
    try:
        raw = _toml_loads(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MachineConfigError(f"cannot parse {resolved}: {exc}") from exc
    return _parse_raw(raw, resolved)


def write_machine(cfg: MachineConfig, path: Path | None = None) -> Path:
    """Validate + atomically write machine.toml. Returns written path."""
    resolved = path or cfg.source_path or default_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    content = _serialize_machine(cfg)
    tmp = resolved.with_suffix(".toml.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, resolved)
    try:
        os.chmod(resolved, 0o644)
    except OSError:  # silent-ok: best-effort chmod
        pass
    return resolved


def list_openclaw_tenants(cfg: MachineConfig | None = None) -> list[OpenClawTenant]:
    """Return all tenants in config order (sorted by name). Convenience for TUI."""
    if cfg is None:
        cfg = load_machine()
    return sorted(cfg.tenants.values(), key=lambda t: t.name)


def validate_tenant(cfg: MachineConfig, name: str) -> tuple[bool, str]:
    """Return (ok, error_or_empty). Checks name exists, workspace exists,
    workspace contains WORKSPACE_CONTRACT.toml (§3 validator rule 3)."""
    if name not in cfg.tenants:
        known = sorted(cfg.tenants.keys())
        return (False, f"tenant {name!r} not found; known tenants: {known}")
    tenant = cfg.tenants[name]
    if not tenant.workspace.exists():
        return (False, f"tenant {name!r} workspace {tenant.workspace} does not exist")
    contract = tenant.workspace / "WORKSPACE_CONTRACT.toml"
    if not contract.exists():
        return (False, f"tenant {name!r} workspace missing WORKSPACE_CONTRACT.toml at {contract}")
    return (True, "")
