"""P1: v2 profile validator + write_validated seam.

Spec: docs/schemas/v0.4-layered-model.md §7.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "core" / "lib"), str(_REPO_ROOT / "core" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sys as _sys, pathlib as _pl
_fc_scripts = str(_pl.Path(__file__).resolve().parent.parent / "scripts")
if _fc_scripts not in _sys.path: _sys.path.insert(0, _fc_scripts)
from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load

from machine_config import (  # noqa: E402
    MachineConfig,
    MachineConfigError,
    MACHINE_SCHEMA_VERSION,
    _TENANT_NAME_RE,
    load_machine,
)

PROFILE_SCHEMA_VERSION = 2

# §7: legal seat names. `ancestor` stays as a v1 primary-seat alias for
# migration compatibility; v2 templates use `memory` as the primary seat.
LEGAL_SEATS = frozenset({"ancestor", "memory", "planner", "builder", "reviewer", "patrol", "designer"})
# §7 rule 3: minimum required. A profile must declare planner plus either
# memory (v2 canonical) or ancestor (v1 compatibility).
REQUIRED_PLANNER = frozenset({"planner"})
REQUIRED_PRIMARY = frozenset({"ancestor", "memory"})
# §7 rule 10: only these may have parallel_instances > 1.
PARALLEL_ALLOWED = frozenset({"builder", "reviewer", "patrol"})
# §7 rule 8: deprecated fields rejected in v2 profiles.
DEPRECATED_FIELDS = frozenset({"heartbeat_transport", "heartbeat_owner", "heartbeat_receipt", "heartbeat_seats",
                                "feishu_group_id", "runtime_seats", "materialized_seats"})
# Seat names rejected in v2 profiles (rule 8 extended).
# Legacy patrol alias removed 2026-04-29.
_NUMBERED_SEAT_RE = re.compile(r"^(koder|builder-\d+|reviewer-\d+|patrol-\d+)$")


class ProfileValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    normalized: dict | None = None


# ── Profile rules ─────────────────────────────────────────────────────


def _check_profile(raw: dict[str, Any], *, machine_cfg: MachineConfig | None) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    # Rule 1: version == 2
    version = raw.get("version")
    if version != PROFILE_SCHEMA_VERSION:
        errors.append(
            f"profile version must be {PROFILE_SCHEMA_VERSION}, got {version!r}. "
            "Run `install.sh --reinstall <project>` to regenerate a canonical v2 profile."
        )

    # Rule 8 (deprecated fields)
    for dep in DEPRECATED_FIELDS:
        if dep in raw:
            errors.append(
                f"deprecated field {dep!r} must not appear in v2 profiles. "
                "Remove it or run `install.sh --reinstall <project>` to regenerate a canonical v2 profile."
            )

    # Rule 8 (seats): memory/koder/builder-N/reviewer-N/patrol-N seat names
    all_seat_names: list[str] = []
    for src in ("seats", "seat_roles", "materialized_seats", "runtime_seats",
                "default_start_seats", "bootstrap_seats"):
        val = raw.get(src)
        if val is None:
            continue
        if isinstance(val, list):
            all_seat_names.extend(str(s) for s in val)
        elif isinstance(val, dict):
            all_seat_names.extend(str(k) for k in val.keys())

    dynamic = raw.get("dynamic_roster", {}) or {}
    if isinstance(dynamic, dict):
        for sub in ("default_start_seats", "bootstrap_seats"):
            v = dynamic.get(sub, [])
            if isinstance(v, list):
                all_seat_names.extend(str(s) for s in v)
    legacy_parallel = raw.get("parallel_instances", {}) or {}
    if isinstance(legacy_parallel, dict):
        all_seat_names.extend(str(k) for k in legacy_parallel.keys())

    for sname in all_seat_names:
        if sname not in LEGAL_SEATS and not _NUMBERED_SEAT_RE.match(sname):
            errors.append(
                f"illegal seat name {sname!r} in profile seat metadata. "
                f"Allowed: {sorted(LEGAL_SEATS)}."
            )
        if _NUMBERED_SEAT_RE.match(sname):
            errors.append(
                f"seat name {sname!r} is not allowed in v2 profiles. "
                "Use the canonical role name (e.g. 'builder' with parallel_instances). "
                "Run `install.sh --reinstall <project>` to regenerate a canonical v2 profile."
            )

    # Rules 2, 3, 4: seats list
    seats_raw = raw.get("seats", [])
    if not isinstance(seats_raw, list):
        errors.append("'seats' must be a list")
        seats_raw = []
    seats = [str(s) for s in seats_raw]

    illegal = sorted(set(seats) - LEGAL_SEATS)
    if illegal:
        errors.append(
            f"illegal seat name(s) {illegal} in 'seats'. "
            f"Allowed: {sorted(LEGAL_SEATS)}."
        )

    seat_set = set(seats)
    if not (REQUIRED_PLANNER & seat_set):
        errors.append(
            "'seats' must include required seat: planner."
        )
    if not (REQUIRED_PRIMARY & seat_set):
        errors.append(
            "'seats' must include a primary seat: memory (v2) or ancestor (v1 compatibility)."
        )

    if len(seats) != len(set(seats)):
        dupes = sorted(s for s in seats if seats.count(s) > 1)
        errors.append(f"duplicate seat(s) in 'seats': {dupes}.")

    if "designer" not in seats:
        warnings.append(
            "seat 'designer' is not listed. If this project has no designer, "
            "that's intentional — but the v2 schema recommends declaring all 6 roles."
        )

    # Rules 9, 10: seat_overrides.X.parallel_instances
    overrides = raw.get("seat_overrides", {}) or {}
    if isinstance(overrides, dict):
        for seat_name, seat_cfg in overrides.items():
            if not isinstance(seat_cfg, dict):
                continue
            pi = seat_cfg.get("parallel_instances")
            if pi is None:
                continue
            try:
                pi_int = int(pi)
            except (TypeError, ValueError):
                errors.append(f"seat_overrides.{seat_name}.parallel_instances must be an integer")
                continue
            if not (1 <= pi_int <= 10):
                errors.append(
                    f"seat_overrides.{seat_name}.parallel_instances={pi_int} out of range [1, 10]."
                )
            if pi_int > 1 and seat_name not in PARALLEL_ALLOWED:
                errors.append(
                    f"seat_overrides.{seat_name}.parallel_instances > 1 is not permitted "
                    f"for {seat_name!r}. Only {sorted(PARALLEL_ALLOWED)} support fan-out."
                )

    # Rule 11: seat_overrides.X.tool/auth_mode/provider validation
    if isinstance(overrides, dict):
        try:
            from agent_admin_config import validate_runtime_combo
            for seat_name, seat_cfg in overrides.items():
                if not isinstance(seat_cfg, dict):
                    continue
                t = seat_cfg.get("tool", "")
                am = seat_cfg.get("auth_mode", "")
                prov = seat_cfg.get("provider", "")
                if t and am and prov:
                    try:
                        validate_runtime_combo(str(t), str(am), str(prov),
                                               context=f"seat_overrides.{seat_name}")
                    except ValueError as exc:
                        errors.append(str(exc))
        except ImportError:
            pass  # silent-ok: agent_admin_config may not be on path in all contexts

    # Rules 5, 6: machine cross-validation (if machine_cfg provided)
    if machine_cfg is not None:
        oc_agent = str(raw.get("openclaw_frontstage_agent", "")).strip()
        if not oc_agent:
            errors.append(
                "missing 'openclaw_frontstage_agent'. "
                "Run `agent-admin project koder-bind --project <name> --tenant <tenant>`."
            )
        elif oc_agent not in machine_cfg.tenants:
            known = sorted(machine_cfg.tenants.keys())
            errors.append(
                f"openclaw_frontstage_agent={oc_agent!r} is not in machine.toml tenants. "
                f"Known tenants: {known}. "
                "Run `agent-admin project koder-bind --project <name> --tenant <tenant>`."
            )

        machine_services = raw.get("machine_services", [])
        if isinstance(machine_services, list):
            # Currently only "memory" is a valid service.
            for svc in machine_services:
                if str(svc) not in ("memory",):
                    errors.append(f"machine_services entry {svc!r} is not a known service in machine.toml.")

    # Rule 12: PROJECT_BINDING cross-validation
    project_name = str(raw.get("project_name", "")).strip()
    if project_name:
        try:
            from project_binding import load_binding
            binding = load_binding(project_name)
            if binding is not None:
                oc_agent = str(raw.get("openclaw_frontstage_agent", "")).strip()
                bound_tenant = getattr(binding, "extras", {}).get("openclaw_frontstage_tenant", "")
                if not bound_tenant:
                    bound_tenant = str(binding.extras.get("openclaw_frontstage_tenant", ""))
                if oc_agent and bound_tenant and oc_agent != bound_tenant:
                    errors.append(
                        f"profile.openclaw_frontstage_agent={oc_agent!r} mismatches "
                        f"PROJECT_BINDING.toml.openclaw_frontstage_tenant={bound_tenant!r}. "
                        "Fix with: agent-admin project koder-bind "
                        f"--project {project_name} --tenant {oc_agent}"
                    )
        except Exception:  # silent-ok: binding load failure is non-fatal; missing binding is ok
            pass

    normalized = dict(raw) if not errors else None
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, normalized=normalized)


# ── Machine config rules ──────────────────────────────────────────────


def _check_machine(raw: dict[str, Any], path: Path) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    # Rule 1: version == 1
    version = raw.get("version")
    if version != MACHINE_SCHEMA_VERSION:
        errors.append(f"machine.toml version must be {MACHINE_SCHEMA_VERSION}, got {version!r}.")

    # Rule 2: exactly one [services.memory]
    services = raw.get("services", {})
    if not isinstance(services, dict) or "memory" not in services:
        errors.append("machine.toml must have exactly one [services.memory] table.")
    else:
        mem = services["memory"]
        if not isinstance(mem, dict):
            errors.append("[services.memory] must be a table.")
        else:
            for required in ("role", "tool", "auth_mode", "provider"):
                if not str(mem.get(required, "")).strip():
                    errors.append(f"[services.memory] is missing required field {required!r}.")
            auth_mode = str(mem.get("auth_mode", ""))
            if auth_mode not in ("api", "oauth_token"):
                errors.append(
                    f"[services.memory].auth_mode must be 'api' or 'oauth_token', got {auth_mode!r}."
                )

    # Rules 3, 4: tenant validation
    raw_tenants = raw.get("openclaw_tenants", {})
    if isinstance(raw_tenants, dict):
        for name, tdata in raw_tenants.items():
            if not _TENANT_NAME_RE.match(name):
                errors.append(
                    f"tenant name {name!r} must match [a-z][a-z0-9_-]*."
                )
            if not isinstance(tdata, dict):
                continue
            ws = tdata.get("workspace", "")
            if not ws:
                errors.append(f"tenant {name!r} is missing 'workspace'.")
                continue
            ws_path = Path(str(ws)).expanduser()
            if not ws_path.exists():
                warnings.append(
                    f"tenant {name!r} workspace {ws_path} does not exist on disk."
                )

    normalized = dict(raw) if not errors else None
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, normalized=normalized)


# ── Public API ────────────────────────────────────────────────────────


def validate_profile_v2(
    path: Path,
    *,
    machine_cfg: MachineConfig | None = None,
) -> ValidationResult:
    """Parse v2 profile + cross-validate against machine.toml. Never raises except on I/O."""
    try:
        raw = _toml_loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ValidationResult(ok=False, errors=[f"profile not found: {path}"])
    except Exception as exc:
        return ValidationResult(ok=False, errors=[f"cannot parse {path}: {exc}"])
    return _check_profile(raw, machine_cfg=machine_cfg)


def validate_machine_config(path: Path) -> ValidationResult:
    """Parse machine.toml and enforce §3 rules."""
    try:
        raw = _toml_loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ValidationResult(ok=False, errors=[f"machine.toml not found: {path}"])
    except Exception as exc:
        return ValidationResult(ok=False, errors=[f"cannot parse {path}: {exc}"])
    return _check_machine(raw, path)


def write_validated(
    payload: dict,
    path: Path,
    *,
    machine_cfg: MachineConfig | None = None,
) -> Path:
    """Validate profile (or machine.toml) then atomically write.

    Raises ProfileValidationError on failure.
    Atomic = tmp file + os.replace. 0o644 perms.
    """
    # Determine whether this is a machine.toml or a profile payload.
    is_machine = "services" in payload and "memory" in (payload.get("services") or {})

    if is_machine:
        result = _check_machine(payload, path)
    else:
        result = _check_profile(payload, machine_cfg=machine_cfg)

    if not result.ok:
        raise ProfileValidationError(result.errors)

    # Serialize via tomllib-compatible TOML (hand-rolled; stdlib only).
    content = _dict_to_toml(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o644)
    except OSError:  # silent-ok: best-effort chmod
        pass
    return path


# ── Minimal TOML serializer (stdlib only, covers flat + one-level tables) ──


def _toml_value(v: Any, *, indent: str = "") -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        if not v:
            return "[]"
        # Inline for simple scalars; multi-line for tables.
        if all(isinstance(i, (str, int, float, bool)) for i in v):
            return "[" + ", ".join(_toml_value(i) for i in v) + "]"
        items = ",\n".join(f"  {_toml_value(i)}" for i in v)
        return f"[\n{items},\n]"
    if isinstance(v, dict):
        return ""  # handled as [table] by caller
    return repr(v)


def _dict_to_toml(d: dict, _prefix: str = "") -> str:
    """Minimal TOML serializer: flat keys first, then [sections]."""
    lines: list[str] = []
    deferred: list[tuple[str, dict]] = []

    for k, v in d.items():
        if isinstance(v, dict):
            deferred.append((k, v))
        else:
            lines.append(f"{k} = {_toml_value(v)}")

    for k, v in deferred:
        section_key = f"{_prefix}.{k}" if _prefix else k
        lines.append("")
        lines.append(f"[{section_key}]")
        # Recurse: first write scalar children, then nested tables.
        nested_scalars = {ck: cv for ck, cv in v.items() if not isinstance(cv, dict)}
        nested_tables = {ck: cv for ck, cv in v.items() if isinstance(cv, dict)}
        for ck, cv in nested_scalars.items():
            lines.append(f"{ck} = {_toml_value(cv)}")
        for ck, cv in nested_tables.items():
            sub_key = f"{section_key}.{ck}"
            lines.append("")
            lines.append(f"[{sub_key}]")
            for sk, sv in cv.items():
                if not isinstance(sv, dict):
                    lines.append(f"{sk} = {_toml_value(sv)}")

    return "\n".join(lines) + "\n"
