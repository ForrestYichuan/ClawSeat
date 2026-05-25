"""Seat resolver: transport hook for legacy harness dispatch/notify flows.

Detection order (short-circuit priority):
1. explicit OpenClaw frontstage — target == frontstage target and transport=openclaw
2. tmux project seats          — target in the profile's tmux-backed runtime seat set
3. named OpenClaw workspace    — ~/.openclaw/workspace-<target>/WORKSPACE_CONTRACT.toml exists + feishu_group_id
4. file-only                   — all other cases (no known transport, write to handoff dir)

Compatibility boundary:
- `profile_heartbeat_owner` / `profile_heartbeat_transport`
- `profile_runtime_seats` (and upstream `materialized_seats` fallbacks)

Those names survive only as legacy harness/local-override shims. Layered v2
profiles do not carry them as canonical model fields; callers should reach this
module through the transport router / migration helpers instead of treating the
legacy names as the primary seat model.

Side effects: NONE from resolve_seat() itself. File writes (handoff JSON) are
the caller's responsibility.

Supports --strict mode: raise SeatResolutionError instead of returning
kind="error" when target cannot be resolved.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Paths (duplicated from _common to keep resolver standalone) ───────────────
#
# Resolver helpers (notify_seat / dispatch_task / complete_handoff) call into
# this module from inside tmux seats whose HOME points at the sandbox
#   ~/.agents/runtime/identities/<tool>/<auth>/<id>/home/
# Reading $HOME directly there sends _agents_root() and _openclaw_home_resolved()
# at sandbox-local paths that don't contain session.toml or workspace contracts.
# Delegate to the canonical helper instead — start_seat.py exports AGENT_HOME
# pointing at the real HOME, and real_user_home() also falls through to
# pwd.getpwuid as a last resort.
_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
from real_home import real_user_home  # noqa: E402

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from _toml_compat import loads_safe as _toml_loads_safe  # noqa: E402


def _home() -> Path:
    return real_user_home()


def _agents_root() -> Path:
    return _home() / ".agents"


def _openclaw_home_resolved() -> Path:
    return Path(os.environ.get("OPENCLAW_HOME", str(_home() / ".openclaw"))).expanduser()


def _load_toml(path: Path) -> Optional[dict]:
    try:
        return _toml_loads_safe(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_session_name_from_session_toml(project_name: str, seat: str) -> Optional[str]:
    """Resolve tmux session name from session.toml, or return None."""
    session_toml = _agents_root() / "sessions" / project_name / seat / "session.toml"
    data = _load_toml(session_toml)
    if data:
        return str(data.get("session", "")).strip() or None
    return None


def _workspace_agent_name(contract_path: Path) -> str:
    name = contract_path.parent.name
    if name.startswith("workspace-"):
        return name[len("workspace-") :]
    return name


def _find_frontstage_openclaw_contract(
    *,
    oc_home: Path,
    heartbeat_owner: str,
    profile_project_name: str,
) -> Path | None:
    for contract_path in sorted(oc_home.glob("workspace-*/WORKSPACE_CONTRACT.toml")):
        contract_data = _load_toml(contract_path)
        if not contract_data:
            continue
        contract_seat = str(contract_data.get("seat_id", "")).strip()
        contract_project = str(contract_data.get("project", "")).strip()
        if contract_seat != heartbeat_owner:
            continue
        if contract_project and contract_project != profile_project_name:
            continue
        return contract_path
    return None


@dataclass(frozen=True)
class _ResolverTransportHints:
    declared_project_seats: tuple[str, ...]
    tmux_project_seats: tuple[str, ...]
    frontstage_target: str
    frontstage_transport: str


def _normalized_seat_list(values: Optional[list[str]]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        seat = str(value).strip()
        if not seat or seat in seen:
            continue
        seen.add(seat)
        ordered.append(seat)
    return tuple(ordered)


def _resolver_transport_hints(
    *,
    profile_seats: list[str],
    profile_runtime_seats: Optional[list[str]],
    profile_heartbeat_owner: Optional[str],
    profile_heartbeat_transport: str,
) -> _ResolverTransportHints:
    """Map legacy harness compat fields onto the transport concepts we route on."""
    declared_project_seats = _normalized_seat_list(profile_seats)
    tmux_project_seats = _normalized_seat_list(profile_runtime_seats) or declared_project_seats
    frontstage_target = (profile_heartbeat_owner or "").strip()
    frontstage_transport = (profile_heartbeat_transport or "tmux").strip().lower() or "tmux"
    return _ResolverTransportHints(
        declared_project_seats=declared_project_seats,
        tmux_project_seats=tmux_project_seats,
        frontstage_target=frontstage_target,
        frontstage_transport=frontstage_transport,
    )


def _profile_tmux_runtime_seats(profile: "HarnessProfileLike", declared_project_seats: list[str]) -> list[str]:
    resolver = getattr(profile, "tmux_runtime_seats", None)
    if callable(resolver):
        return list(resolver())
    return list(getattr(profile, "runtime_seats", []) or declared_project_seats)


def _profile_frontstage_target(profile: "HarnessProfileLike") -> str:
    resolver = getattr(profile, "frontstage_target_seat", None)
    if callable(resolver):
        return str(resolver()).strip()
    return str(getattr(profile, "heartbeat_owner", "")).strip()


def _profile_frontstage_transport(profile: "HarnessProfileLike") -> str:
    resolver = getattr(profile, "frontstage_transport_kind", None)
    if callable(resolver):
        value = resolver()
    else:
        value = getattr(profile, "heartbeat_transport", "tmux")
    return str(value).strip().lower() or "tmux"


# ── SeatResolution dataclass ───────────────────────────────────────────────────


class SeatResolutionError(Exception):
    """Raised when --strict mode is active and resolution fails."""


@dataclass
class SeatResolution:
    """Result of resolving a target seat to a transport.

    Attributes
    ----------
    kind : str
        One of "tmux", "openclaw", "file-only", "error".
    transport : str
        One of "tmux-send-keys", "feishu-oc-v1", "patrol-handoff-dir", "unresolved".
    target : str
        The original target name that was resolved.
    session_name : str, optional
        Present when kind == "tmux".
    group_id : str, optional
        Present when kind == "openclaw".
    agent_name : str, optional
        Present when kind == "openclaw".
    handoff_path : Path, optional
        Present when kind == "file-only".
    error : str, optional
        Present when kind == "error" (non-strict mode).

    Notes
    -----
    __post_init__ enforces that required fields are present for each kind.
    Use --strict to raise SeatResolutionError instead of returning kind="error".
    """

    kind: str
    transport: str
    target: str
    session_name: Optional[str] = None
    group_id: Optional[str] = None
    agent_name: Optional[str] = None
    handoff_path: Optional[Path] = None
    error: Optional[str] = None

    # Internal: strict flag carried through for error messages
    _strict: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if self.kind == "tmux":
            # session_name may be None if session.toml hasn't been created yet.
            # Downstream callers (notify_seat.py) handle this gracefully.
            pass
        elif self.kind == "openclaw":
            if not self.group_id:
                raise ValueError(
                    f"SeatResolution(kind='openclaw'): group_id is required; got {self.group_id!r}"
                )
            if not self.agent_name:
                raise ValueError(
                    f"SeatResolution(kind='openclaw'): agent_name is required; got {self.agent_name!r}"
                )
        elif self.kind == "file-only":
            # handoff_path is recommended but not enforced — caller may not need it
            pass
        elif self.kind == "error":
            if not self.error:
                raise ValueError(
                    f"SeatResolution(kind='error'): error message is required; got {self.error!r}"
                )
        else:
            raise ValueError(f"Unknown SeatResolution kind: {self.kind!r}")

    @property
    def is_tmux(self) -> bool:
        return self.kind == "tmux"

    @property
    def is_openclaw(self) -> bool:
        return self.kind == "openclaw"

    @property
    def is_file_only(self) -> bool:
        return self.kind == "file-only"

    def into_error(self, strict: bool = False) -> "SeatResolution":
        """Return self if kind != 'error', otherwise raise or return error resolution."""
        if self.kind == "error" and strict:
            raise SeatResolutionError(self.error or "resolution failed")
        return self

    def dispatch_error_message(self) -> str:
        """Human-readable error message for dispatch_task.py when kind == 'openclaw'."""
        return (
            f"target {self.target!r} resolves to kind=openclaw (agent={self.agent_name!r}); "
            f"dispatch_task cannot deliver tasks directly to OpenClaw agents. "
            f"use complete_handoff.py --frontstage-disposition AUTO_ADVANCE instead "
            f"(planner aggregates and forwards via OC_DELEGATION_REPORT_V1)."
        )


# ── Core resolver ──────────────────────────────────────────────────────────────


def resolve_seat(
    target: str,
    profile_seats: list[str],
    profile_project_name: str,
    profile_handoff_dir: Path,
    profile_session_name_resolver: Optional[callable] = None,
    strict: bool = False,
    _openclaw_home: Optional[Path] = None,
    profile_runtime_seats: Optional[list[str]] = None,
    profile_heartbeat_owner: Optional[str] = None,
    profile_heartbeat_transport: str = "tmux",
) -> SeatResolution:
    """Resolve a target seat name to a SeatResolution.

    Parameters
    ----------
    target :
        Seat or agent name to resolve.
    profile_seats :
        Declared seat names from the profile or profile-like object.
    profile_project_name :
        Project name for session.toml resolution.
    profile_handoff_dir :
        Directory for file-only handoff JSON files.
    profile_session_name_resolver :
        Optional callable(profile_project_name, target) → str | None.
        If not provided, uses the built-in session.toml resolver.
    profile_runtime_seats :
        Legacy harness/local-override shim for the tmux-backed seat set. If
        omitted, the resolver falls back to `profile_seats`.
    profile_heartbeat_owner / profile_heartbeat_transport :
        Legacy frontstage shims used only for the explicit OpenClaw frontstage
        shortcut. Layered v2 profiles should not surface these fields directly.
    strict :
        If True, raise SeatResolutionError on resolution failure instead of
        returning kind="error".

    Returns
    -------
    SeatResolution
        Fully populated resolution. Use .into_error(strict) to convert
        kind="error" to an exception in strict mode.
    """
    hints = _resolver_transport_hints(
        profile_seats=profile_seats,
        profile_runtime_seats=profile_runtime_seats,
        profile_heartbeat_owner=profile_heartbeat_owner,
        profile_heartbeat_transport=profile_heartbeat_transport,
    )
    oc_home = _openclaw_home if _openclaw_home is not None else _openclaw_home_resolved()

    def _openclaw_resolution(contract_path: Path) -> SeatResolution:
        contract_data = _load_toml(contract_path)
        group_id: Optional[str] = None
        if contract_data:
            raw_gid = str(contract_data.get("feishu_group_id", "")).strip()
            group_id = raw_gid if raw_gid else None
        if not group_id:
            handoff_path = profile_handoff_dir / f"{target}.json"
            return SeatResolution(
                kind="file-only",
                transport="patrol-handoff-dir",
                target=target,
                handoff_path=handoff_path,
                error=f"workspace contract exists at {contract_path} but feishu_group_id is missing",
            )
        return SeatResolution(
            kind="openclaw",
            transport="feishu-oc-v1",
            target=target,
            group_id=group_id,
            agent_name=_workspace_agent_name(contract_path),
        )

    # ── 1. explicit OpenClaw frontstage ─────────────────────────────────────
    if target == hints.frontstage_target and hints.frontstage_transport == "openclaw":
        contract_path = _find_frontstage_openclaw_contract(
            oc_home=oc_home,
            heartbeat_owner=hints.frontstage_target,
            profile_project_name=profile_project_name,
        )
        if contract_path is not None:
            return _openclaw_resolution(contract_path)
        handoff_path = profile_handoff_dir / f"{target}.json"
        error_msg = (
            f"frontstage target {target!r} is configured for OpenClaw transport "
            f"but no matching OpenClaw workspace contract was found under {oc_home}"
        )
        if strict:
            raise SeatResolutionError(error_msg)
        return SeatResolution(
            kind="file-only",
            transport="patrol-handoff-dir",
            target=target,
            handoff_path=handoff_path,
            error=error_msg,
        )

    # ── 2. tmux: target is a declared runtime seat in the profile ───────────
    if target in hints.tmux_project_seats:
        resolver = profile_session_name_resolver or _resolve_session_name_from_session_toml
        session_name = resolver(profile_project_name, target)
        return SeatResolution(
            kind="tmux",
            transport="tmux-send-keys",
            target=target,
            session_name=session_name,
        )

    # ── 2.5. suffix alias fallback ───────────────────────────────────────────
    # Bare seat name (e.g. "builder") may not be in tmux_project_seats when
    # dynamic_roster generates runtime_seats with numbered suffixes such as
    # "builder-1".  Try -1 through -9 in order and resolve to the first match.
    # Emits a diagnostic note to stderr so callers can see the alias mapping.
    _alias_target: str | None = None
    for _n in range(1, 10):
        _candidate = f"{target}-{_n}"
        if _candidate in hints.tmux_project_seats:
            _alias_target = _candidate
            break
    if _alias_target is not None:
        print(
            f"note: seat {target!r} resolved via alias {_alias_target!r}",
            file=sys.stderr,
        )
        resolver = profile_session_name_resolver or _resolve_session_name_from_session_toml
        session_name = resolver(profile_project_name, _alias_target)
        return SeatResolution(
            kind="tmux",
            transport="tmux-send-keys",
            target=target,
            session_name=session_name,
        )

    # ── 3. openclaw: workspace contract exists with feishu_group_id ───────────
    workspace_contract = oc_home / f"workspace-{target}" / "WORKSPACE_CONTRACT.toml"
    if workspace_contract.exists():
        return _openclaw_resolution(workspace_contract)

    # ── 4. file-only fallback ─────────────────────────────────────────────────
    # Ambiguous: not a known tmux seat and no OpenClaw workspace.
    # In non-strict mode, this is a valid file-only resolution.
    # In strict mode, raise because the target cannot be reliably dispatched.
    handoff_path = profile_handoff_dir / f"{target}.json"
    error_msg = (
        f"target {target!r} is not a declared tmux seat "
        f"(runtime seats: {list(hints.tmux_project_seats)}) and has no OpenClaw workspace contract "
        f"(checked {workspace_contract}); cannot dispatch."
    )
    if strict:
        raise SeatResolutionError(error_msg)
    return SeatResolution(
        kind="file-only",
        transport="patrol-handoff-dir",
        target=target,
        handoff_path=handoff_path,
    )


# ── Convenience overloads ─────────────────────────────────────────────────────


def resolve_seat_from_profile(
    target: str,
    profile: "HarnessProfileLike",
    strict: bool = False,
) -> SeatResolution:
    """Resolve a target using a legacy-harness-compatible profile-like object.

    profile must expose declared project seats/project_name/handoff_dir.
    Optional helper methods (`tmux_runtime_seats`, `frontstage_target_seat`,
    `frontstage_transport_kind`) override the legacy `runtime_seats` /
    `heartbeat_*` attrs when present.
    """
    seats: list[str] = getattr(profile, "seats", [])
    runtime_seats = _profile_tmux_runtime_seats(profile, seats)
    project_name: str = getattr(profile, "project_name", "")
    handoff_dir: Path = getattr(profile, "handoff_dir", Path("~/.agents/tasks/hardening-b/patrol/handoffs"))
    frontstage_target = _profile_frontstage_target(profile)
    frontstage_transport = _profile_frontstage_transport(profile)

    def session_resolver(proj: str, seat: str) -> Optional[str]:
        return _resolve_session_name_from_session_toml(proj, seat)

    return resolve_seat(
        target=target,
        profile_seats=seats,
        profile_project_name=project_name,
        profile_handoff_dir=Path(handoff_dir),
        profile_session_name_resolver=session_resolver,
        strict=strict,
        profile_runtime_seats=runtime_seats,
        profile_heartbeat_owner=frontstage_target,
        profile_heartbeat_transport=frontstage_transport,
    )


# Protocol for profile-like objects (no hard import to avoid circular deps)
class HarnessProfileLike:
    """Minimal protocol that resolve_seat_from_profile needs from a profile object."""

    seats: list[str]
    runtime_seats: list[str]
    project_name: str
    handoff_dir: Path
    heartbeat_owner: str = ""
    heartbeat_transport: str = "tmux"
    profile_path: Path = Path()

    def tmux_runtime_seats(self) -> list[str]:
        return list(getattr(self, "runtime_seats", []) or getattr(self, "seats", []))

    def frontstage_target_seat(self) -> str:
        return str(getattr(self, "heartbeat_owner", "")).strip()

    def frontstage_transport_kind(self) -> str:
        return str(getattr(self, "heartbeat_transport", "tmux")).strip().lower() or "tmux"

    @classmethod
    def from_toml_path(cls, path: Path) -> "HarnessProfileLike":
        data = _load_toml(path)
        if data is None:
            raise ValueError(f"Could not load profile from {path}")
        obj = cls()
        obj.profile_path = path
        obj.seats = [str(s) for s in data.get("seats", [])]
        dynamic = data.get("dynamic_roster", {})
        runtime_raw = dynamic.get("runtime_seats", obj.seats) if isinstance(dynamic, dict) else obj.seats
        obj.runtime_seats = [str(s) for s in runtime_raw]
        obj.project_name = str(data.get("project_name", ""))
        # Compatibility only: layered v2 profiles removed heartbeat_* and
        # runtime/materialized seat transport hints from the canonical schema.
        # If the keys are absent, resolver falls back to declared seats and the
        # explicit frontstage shortcut is disabled.
        obj.heartbeat_owner = str(data.get("heartbeat_owner", ""))
        obj.heartbeat_transport = str(data.get("heartbeat_transport", "tmux")).strip().lower() or "tmux"
        hdir = str(data.get("handoff_dir", ""))
        obj.handoff_dir = Path(hdir).expanduser() if hdir else path.parent
        return obj
