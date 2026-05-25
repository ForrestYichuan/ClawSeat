"""agent_admin_layered.py — P1 layered-model subcommands.

Implements four new agent-admin subcommands added by Phase 1:

* ``agent-admin project koder-bind --project X --tenant Y``
* ``agent-admin machine memory show``
* ``agent-admin project seat list --project X``
* ``agent-admin project validate --project X``

All four consume the v0.4 layered model: the machine layer
(``~/.clawseat/machine.toml`` — builder-1's ``machine_config.py``), the
project profile v2 (builder-1's ``profile_validator.py``), and the
existing per-project binding (``project_binding.py``).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load

# Path bootstrapping so imports work from both script + package contexts.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "core" / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from real_home import real_user_home  # noqa: E402
from tmux import tmux_session_alive as _tmux_session_alive_shared  # noqa: E402
from project_binding import (  # noqa: E402
    ProjectBinding,
    ProjectBindingError,
    binding_path,
    load_binding,
    write_binding,
)

from machine_config import load_machine, validate_tenant  # noqa: E402

_MACHINE_AVAILABLE = True

try:
    from profile_validator import validate_profile_v2  # type: ignore[import-not-found]
    _VALIDATOR_AVAILABLE = True
except ImportError:  # pragma: no cover — fallback when builder-1 hasn't landed
    _VALIDATOR_AVAILABLE = False

    def validate_profile_v2(*_a, **_kw):  # type: ignore[no-redef]
        raise ImportError("core.lib.profile_validator unavailable")

try:
    from profile_loader_v3 import ProfileV3Error, load_profile_v3  # type: ignore[import-not-found]
    _V3_LOADER_AVAILABLE = True
except ImportError:  # pragma: no cover
    _V3_LOADER_AVAILABLE = False

    class ProfileV3Error(RuntimeError):  # type: ignore[no-redef]
        pass

    def load_profile_v3(*_a, **_kw):  # type: ignore[no-redef]
        raise ImportError("core.lib.profile_loader_v3 unavailable")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TENANT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def project_profile_path(project: str, *, home: Path | None = None) -> Path:
    base = home or real_user_home()
    return base / ".agents" / "profiles" / f"{project}-profile-dynamic.toml"


def _iso_now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_text(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _update_workspace_contract_project(workspace: Path, project: str) -> None:
    """Rewrite the ``project = "..."`` line in WORKSPACE_CONTRACT.toml.

    If the key is absent we append it. If the file is absent we create it.
    Atomic via tmp + replace. Other keys preserved verbatim.
    """
    contract = workspace / "WORKSPACE_CONTRACT.toml"
    workspace.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if contract.is_file():
        lines = contract.read_text(encoding="utf-8").splitlines()
    project_literal = f'project = "{project}"'
    for i, line in enumerate(lines):
        if re.match(r"^\s*project\s*=", line):
            lines[i] = project_literal
            break
    else:
        lines.append(project_literal)
    body = "\n".join(lines) + ("\n" if lines else "")
    _atomic_write_text(contract, body)


# ---------------------------------------------------------------------------
# project koder-bind
# ---------------------------------------------------------------------------

class KoderBindError(RuntimeError):
    pass


def _koder_bind_recovery_hint(tenant: str, cfg: Any, err: str) -> str:
    """Generate actionable recovery text when koder-bind fails.

    Called only when validate_tenant returned (False, err). The hint is
    appended to KoderBindError message so the operator / agent sees
    exactly what to do next instead of a bare "does not exist" line.
    """
    # Only help on the missing-workspace case — other validation
    # failures (bad name, unknown tenant) already have clear messages.
    if "does not exist" not in err:
        return ""

    # Locate expected workspace path for this tenant (if registered at all).
    expected: Path | None = None
    if tenant in getattr(cfg, "tenants", {}):
        tenant_obj = cfg.tenants[tenant]
        try:
            expected = Path(os.path.expanduser(str(tenant_obj.workspace)))
        except Exception:
            expected = None

    # Error path reachable here: validate_tenant(..., "does not exist"), i.e.
    # tenant.workspace does NOT exist on disk. That means in-workspace
    # .backup-<ts>/ directories cannot be scanned (the workspace itself is
    # gone). Only sibling backups — operator-initiated `mv workspace
    # workspace.pre-*-backup.<ts>` that preserves the whole old workspace
    # next to the expected path — are actionable here. Managed in-workspace
    # backups are a different error mode (corrupted/partial workspace) and
    # belong to a separate validator failure; do not conflate.
    sibling_backups: list[Path] = []
    if expected is not None:
        parent = expected.parent
        stem = expected.name
        if parent.is_dir():
            try:
                siblings = [
                    p
                    for p in parent.iterdir()
                    if p.is_dir()
                    and p.name != stem
                    and p.name.startswith(stem)
                    and "backup" in p.name.lower()
                ]
                sibling_backups = sorted(
                    siblings,
                    key=lambda p: _safe_mtime(p),
                    reverse=True,
                )
            except OSError:
                sibling_backups = []

    lines = [
        "",
        "",
        "Recovery options / 恢复选项:",
    ]
    step = 1
    if sibling_backups:
        lines.append(
            f"  ({step}) Restore whole-workspace backup (operator-initiated) / "
            f"从整个 workspace 备份恢复 — {len(sibling_backups)} candidate(s), newest first by mtime:"
        )
        for b in sibling_backups[:3]:
            lines.append(f"        mv {b} {expected}")
        step += 1
    else:
        lines.append(
            f"  ({step}) No sibling backup found next to {expected}. Create or "
            f"restore the workspace manually / 手工创建或恢复 workspace."
        )
        step += 1
    lines.append(
        f"  ({step}) Re-initialize the tenant workspace via OpenClaw side / 通过 "
        "OpenClaw 重新初始化该 tenant workspace, then retry koder-bind."
    )
    step += 1
    lines.append(
        f"  ({step}) Skip the koder overlay for this install / 本次安装跳过 koder "
        "overlay (omit the koder-bind step; projects can run without koder "
        "until you are ready)."
    )
    return "\n".join(lines)


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


_FEISHU_GROUP_ID_RE = re.compile(r"^oc_.{16,}$")


def _validate_feishu_group_id(group_id: str) -> None:
    """Raise KoderBindError if group_id doesn't match oc_<16+ chars>."""
    if not _FEISHU_GROUP_ID_RE.match(group_id):
        raise KoderBindError(
            f"invalid feishu_group_id {group_id!r}: must start with 'oc_' "
            "and be at least 19 chars total (oc_ + 16 chars). "
            "Find it in Feishu developer console → group chat info."
        )


def do_koder_bind(
    project: str,
    tenant: str,
    *,
    group_id: str | None = None,
    machine_cfg=None,
    binding_home: Path | None = None,
) -> dict[str, Any]:
    """Bind tenant's OpenClaw workspace to project + record it in PROJECT_BINDING.

    Atomic-ish: updates both files; if the binding write fails, the
    workspace contract update is rolled back via backup/restore.

    Parameters
    ----------
    group_id :
        Feishu open-chat group ID (format: oc_ + ≥16 chars).
        If None, writes "oc_pending" placeholder — update later with
        `agent-admin project bind --feishu-group`.
    """
    if group_id is not None:
        _validate_feishu_group_id(group_id)
    if not _MACHINE_AVAILABLE and machine_cfg is None:
        raise KoderBindError(
            "core.lib.machine_config not importable; koder-bind requires the "
            "machine layer (builder-1's Phase 1 commits)."
        )
    if not _TENANT_NAME_RE.match(tenant):
        raise KoderBindError(
            f"invalid tenant name {tenant!r}; must match [a-z][a-z0-9_-]*"
        )
    cfg = machine_cfg if machine_cfg is not None else load_machine()
    ok, err = validate_tenant(cfg, tenant)
    if not ok:
        hint = _koder_bind_recovery_hint(tenant, cfg, err)
        raise KoderBindError(
            f"tenant {tenant!r} not registered in machine.toml: {err}. "
            f"Known tenants: {sorted(cfg.tenants.keys())}.{hint}"
        )

    tenant_obj = cfg.tenants[tenant]
    workspace = Path(os.path.expanduser(str(tenant_obj.workspace))).expanduser()

    # Back up workspace contract in case the binding write fails.
    contract = workspace / "WORKSPACE_CONTRACT.toml"
    backup: bytes | None = None
    if contract.is_file():
        backup = contract.read_bytes()
    _update_workspace_contract_project(workspace, project)

    try:
        existing = load_binding(project, home=binding_home)
        extras: dict[str, Any] = dict(existing.extras) if existing is not None else {}
        previous_tenant = extras.get("openclaw_frontstage_tenant")
        extras["openclaw_frontstage_tenant"] = tenant
        extras["bound_via"] = "agent-admin project koder-bind"
        extras["last_bind_ts"] = _iso_now_utc()

        if existing is not None:
            binding = ProjectBinding(
                project=existing.project,
                feishu_group_id=group_id or existing.feishu_group_id,
                feishu_group_name=existing.feishu_group_name,
                feishu_external=existing.feishu_external,
                feishu_sender_app_id=existing.feishu_sender_app_id,
                feishu_sender_mode=existing.feishu_sender_mode,
                openclaw_koder_agent=existing.openclaw_koder_agent,
                tools_isolation=existing.tools_isolation,
                gemini_account_email=existing.gemini_account_email,
                codex_account_email=existing.codex_account_email,
                require_mention=existing.require_mention,
                bound_by=existing.bound_by,
                extras=extras,
            )
        else:
            # New binding — use provided group_id or placeholder.
            # Placeholder can be replaced later with `agent-admin project bind`.
            binding = ProjectBinding(
                project=project,
                feishu_group_id=group_id or "oc_pending",
                openclaw_koder_agent="koder",
                bound_by="agent-admin project koder-bind",
                extras=extras,
            )
        write_binding(binding, home=binding_home)
    except Exception:
        # Roll back workspace contract if the binding write blows up.
        if backup is None:
            try:
                contract.unlink()
            except FileNotFoundError:
                pass
        else:
            contract.write_bytes(backup)
        raise

    return {
        "project": project,
        "tenant": tenant,
        "workspace": str(workspace),
        "previous_tenant": previous_tenant,
    }


def cmd_project_koder_bind(args: argparse.Namespace) -> int:
    try:
        result = do_koder_bind(
            args.project, args.tenant,
            group_id=getattr(args, "feishu_group_id", None),
        )
    except (KoderBindError, ProjectBindingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    prev = result.get("previous_tenant")
    action = (
        f"rebound (was {prev!r})" if prev and prev != args.tenant
        else "bound" if prev is None
        else "already-bound (no-op)"
    )
    print(
        f"koder {action}: project={result['project']} tenant={result['tenant']} "
        f"workspace={result['workspace']}"
    )
    return 0


# ---------------------------------------------------------------------------
# machine memory show
# ---------------------------------------------------------------------------

def _tmux_session_alive(name: str) -> bool:
    return _tmux_session_alive_shared(name, timeout=3.0)


def describe_memory(cfg=None) -> list[str]:
    """Return a human-readable list of lines describing memory config + runtime."""
    cfg = cfg if cfg is not None else load_machine()
    mem = cfg.memory
    lines = [
        "machine memory service",
        f"  role         = {mem.role}",
        f"  tool         = {mem.tool}",
        f"  auth_mode    = {mem.auth_mode}",
        f"  provider     = {mem.provider}",
        f"  model        = {mem.model or '(provider default)'}",
        f"  runtime_dir  = {mem.runtime_dir}",
        f"  storage_root = {mem.storage_root}",
        f"  monitor      = {mem.monitor}",
        f"  launch_args  = {list(mem.launch_args)}",
    ]
    # Runtime probe — best-effort; tmux may be absent in CI/sandboxes.
    session = "install-memory-claude"
    alive = _tmux_session_alive(session)
    lines.append(f"runtime: tmux session {session!r} alive={alive}")
    return lines


def cmd_machine_memory_show(args: argparse.Namespace) -> int:
    if not _MACHINE_AVAILABLE:
        print(
            "error: core.lib.machine_config not importable; machine memory "
            "show requires builder-1's P1 commits.",
            file=sys.stderr,
        )
        return 2
    try:
        lines = describe_memory()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


# ---------------------------------------------------------------------------
# project seat list
# ---------------------------------------------------------------------------

def expand_parallel_seats(seats: list[str], overrides: dict[str, Any]) -> list[str]:
    """Expand parallel_instances per schema §8 naming convention.

    ``{role}`` when N==1, ``{role}_{n}`` (1-indexed) when N>1.
    """
    out: list[str] = []
    for seat in seats:
        n = 1
        ovr = overrides.get(seat) or {}
        if isinstance(ovr, dict):
            try:
                n = int(ovr.get("parallel_instances", 1))
            except (TypeError, ValueError):
                n = 1
        if n <= 1:
            out.append(seat)
        else:
            for i in range(1, n + 1):
                out.append(f"{seat}_{i}")
    return out


def list_seats_for_project(project: str, *, home: Path | None = None) -> list[str]:
    path = project_profile_path(project, home=home)
    if not path.is_file():
        raise FileNotFoundError(f"profile not found: {path}")
    raw = _toml_loads(path.read_text(encoding="utf-8"))
    seats = list(raw.get("seats", []))
    overrides = raw.get("seat_overrides", {}) or {}
    return expand_parallel_seats(seats, overrides)


def cmd_project_seat_list(args: argparse.Namespace) -> int:
    try:
        seats = list_seats_for_project(args.project)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for seat in seats:
        print(seat)
    return 0


# ---------------------------------------------------------------------------
# project validate
# ---------------------------------------------------------------------------

def cmd_project_validate(args: argparse.Namespace) -> int:
    path = project_profile_path(args.project)
    try:
        with path.open("rb") as handle:
            raw_profile = _toml_load(handle)
    except FileNotFoundError:
        print(f"error: profile not found: {path}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to read profile {path}: {exc}", file=sys.stderr)
        return 1

    mode = raw_profile.get("mode") if isinstance(raw_profile, dict) else {}
    if isinstance(mode, dict) and str(mode.get("team_structure") or "").strip() == "multi":
        if not _V3_LOADER_AVAILABLE:
            print(
                "error: core.lib.profile_loader_v3 not importable; "
                "v3 multi-team project validate requires the v3 loader.",
                file=sys.stderr,
            )
            return 2
        try:
            profile = load_profile_v3(path)
        except ProfileV3Error as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if profile.project_name != args.project:
            print(
                f"error: profile project_name={profile.project_name!r} "
                f"does not match requested project {args.project!r}",
                file=sys.stderr,
            )
            return 1
        teams = ", ".join(sorted(profile.teams)) if profile.teams else "(none)"
        print(f"ok: {path} (v3 multi; seats={len(profile.seats)}; teams={teams})")
        return 0

    if not _VALIDATOR_AVAILABLE:
        print(
            "error: core.lib.profile_validator not importable; "
            "project validate requires builder-1's P1 commits.",
            file=sys.stderr,
        )
        return 2
    result = validate_profile_v2(path)
    for w in result.warnings:
        print(f"warning: {w}")
    for e in result.errors:
        print(f"error: {e}", file=sys.stderr)
    if result.ok:
        print(f"ok: {path}")
        return 0
    return 1
