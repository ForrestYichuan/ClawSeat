"""ClawSeat v3 profile loader extension.

Reads [mode] and [teams] metadata from project.toml on top of the existing
flat seats/seat_roles/seat_overrides layout. Single-team projects are
transparent (mode absent or team_structure='single').

This is a *thin* metadata reader. It deliberately does NOT replace the
canonical loader at core/skills/gstack-harness/scripts/_common/profile.py —
it sits alongside as an additive reader that the multi-team helpers
(queue_io, agent_admin brief, install.sh multi render) consult.

See spec §4.1, §15 (install-spec-2026-05-13-clawseat-v3-multi-team-protocol.md).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import sys as _sys, pathlib as _pl
_plv3_scripts = str(_pl.Path(__file__).resolve().parent.parent / "scripts")
if _plv3_scripts not in _sys.path: _sys.path.insert(0, _plv3_scripts)
from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load


VALID_MODES = frozenset({"single", "multi"})


class ProfileV3Error(RuntimeError):
    """Raised on v3 schema or consistency violations."""


@dataclass
class TeamSpec:
    name: str
    seats: list[str]


@dataclass
class ProfileV3:
    """v3 metadata layer. Does not duplicate the legacy HarnessProfile."""

    profile_path: Path
    project_name: str
    team_structure: str  # 'single' or 'multi'
    project_memory: str = "memory"
    teams: dict[str, TeamSpec] = field(default_factory=dict)
    seats: list[str] = field(default_factory=list)
    seat_roles: dict[str, str] = field(default_factory=dict)
    _seat_to_team: dict[str, str] = field(default_factory=dict)

    def team_of(self, seat_id: str) -> str:
        """Reverse-lookup: which team owns this seat.

        For single mode, returns 'default'. For multi mode, raises if seat is
        not in any team's seats list.
        """
        if self.team_structure == "single":
            return "default"
        team = self._seat_to_team.get(seat_id)
        if team is None:
            raise ProfileV3Error(
                f"seat {seat_id!r} is not assigned to any team in project "
                f"{self.project_name!r}"
            )
        return team

    def seats_of(self, team_name: str) -> list[str]:
        if self.team_structure == "single":
            if team_name != "default":
                raise ProfileV3Error(
                    f"single-mode project has no team {team_name!r}; only 'default'"
                )
            return list(self.seats)
        team = self.teams.get(team_name)
        if team is None:
            raise ProfileV3Error(
                f"team {team_name!r} not declared in project {self.project_name!r}"
            )
        return list(team.seats)

    def is_multi(self) -> bool:
        return self.team_structure == "multi"


def load_profile_v3(profile_path: Path | str) -> ProfileV3:
    """Parse project.toml v3 metadata. Returns ProfileV3 dataclass.

    Validates:
    - project_name present
    - seats list non-empty
    - seat_roles covers every seat
    - mode.team_structure ∈ {single, multi}
    - multi mode requires [teams]
    - every seat in [teams].*.seats is in top-level seats
    - every non-memory worker seat appears in exactly one team (no overlap)
    """
    path = Path(profile_path)
    if not path.exists():
        raise ProfileV3Error(f"profile not found: {path}")

    with path.open("rb") as fh:
        data = _toml_load(fh)

    project_name = str(data.get("project_name") or "").strip()
    if not project_name:
        raise ProfileV3Error(f"{path}: missing project_name")

    seats = [str(s) for s in data.get("seats") or []]
    if not seats:
        raise ProfileV3Error(f"{path}: 'seats' must be a non-empty list")

    seat_roles_raw = data.get("seat_roles") or {}
    if not isinstance(seat_roles_raw, dict):
        raise ProfileV3Error(f"{path}: [seat_roles] must be a table")
    seat_roles = {str(k): str(v) for k, v in seat_roles_raw.items()}

    missing_roles = [s for s in seats if s not in seat_roles]
    if missing_roles:
        raise ProfileV3Error(
            f"{path}: seat_roles missing entries for {missing_roles}"
        )

    mode_block = data.get("mode") or {}
    team_structure = str(mode_block.get("team_structure", "single")).strip()
    if team_structure not in VALID_MODES:
        raise ProfileV3Error(
            f"{path}: mode.team_structure must be one of {sorted(VALID_MODES)}, "
            f"got {team_structure!r}"
        )
    project_memory = str(mode_block.get("project_memory", "memory")).strip() or "memory"
    if project_memory not in seats:
        raise ProfileV3Error(
            f"{path}: mode.project_memory={project_memory!r} is not in top-level seats"
        )

    teams: dict[str, TeamSpec] = {}
    seat_to_team: dict[str, str] = {}

    if team_structure == "multi":
        teams_block = data.get("teams")
        if not isinstance(teams_block, dict) or not teams_block:
            raise ProfileV3Error(
                f"{path}: multi mode requires non-empty [teams] table"
            )
        for team_name, team_cfg in teams_block.items():
            if not isinstance(team_cfg, dict):
                raise ProfileV3Error(
                    f"{path}: [teams.{team_name}] must be a table"
                )
            team_seats = [str(s) for s in team_cfg.get("seats") or []]
            if not team_seats:
                raise ProfileV3Error(
                    f"{path}: [teams.{team_name}].seats is empty"
                )
            for seat in team_seats:
                if seat not in seats:
                    raise ProfileV3Error(
                        f"{path}: team {team_name!r} references seat {seat!r} "
                        f"that is not in top-level 'seats'"
                    )
                if seat in seat_to_team:
                    raise ProfileV3Error(
                        f"{path}: seat {seat!r} appears in multiple teams "
                        f"({seat_to_team[seat]!r} and {team_name!r})"
                    )
                seat_to_team[seat] = str(team_name)
            teams[str(team_name)] = TeamSpec(name=str(team_name), seats=team_seats)

        if project_memory in seat_to_team:
            raise ProfileV3Error(
                f"{path}: project memory seat {project_memory!r} must not be assigned to a subteam"
            )
        orphan = [s for s in seats if s not in seat_to_team and s != project_memory]
        if orphan:
            raise ProfileV3Error(
                f"{path}: multi mode but seats not assigned to any team: {orphan}"
            )

    return ProfileV3(
        profile_path=path,
        project_name=project_name,
        team_structure=team_structure,
        project_memory=project_memory,
        teams=teams,
        seats=seats,
        seat_roles=seat_roles,
        _seat_to_team=seat_to_team,
    )


def detect_mode(profile_path: Path | str) -> str:
    """Cheap mode detection — used by callers that just need to branch single/multi."""
    return load_profile_v3(profile_path).team_structure
