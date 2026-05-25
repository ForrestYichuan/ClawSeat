from __future__ import annotations
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from seat_skill_mapping import SEAT_SKILL_MAP, role_skill_for_hint, role_skill_for_seat, skill_names_for_seat

def test_seat_skill_map_canonical_roles() -> None:
    assert SEAT_SKILL_MAP['builder'] == 'builder'
    assert SEAT_SKILL_MAP['reviewer'] == 'reviewer'
    assert SEAT_SKILL_MAP['patrol'] == 'patrol'
    assert 'qa' not in SEAT_SKILL_MAP
    assert SEAT_SKILL_MAP['designer'] == 'designer'
    assert SEAT_SKILL_MAP['ancestor'] == 'clawseat-ancestor'
    assert SEAT_SKILL_MAP['planner'] == 'planner'
    assert SEAT_SKILL_MAP['memory'] == 'memory-oracle'

def test_role_skill_for_seat_with_suffix() -> None:
    assert role_skill_for_seat('builder-1') == 'builder'
    assert role_skill_for_seat('reviewer-abc') == 'reviewer'
    assert role_skill_for_seat('patrol-42') == 'patrol'
    assert role_skill_for_seat('designer-main') == 'designer'
    assert role_skill_for_seat('unknown-role') == 'clawseat'

def test_role_skill_for_multi_team_dynamic_seats() -> None:
    assert role_skill_for_seat("front-product-planner") == "planner"
    assert role_skill_for_seat("front-product-builder-core") == "builder"
    assert role_skill_for_seat("front-product-reviewer") == "reviewer"
    assert role_skill_for_seat("quality-docs-patrol-fast") == "patrol"

def test_role_hint_aliases_drive_runtime_skill_bundle() -> None:
    assert role_skill_for_seat("planner-dispatcher") == "planner"
    assert role_skill_for_seat("project-memory") == "memory-oracle"
    assert role_skill_for_seat("qa") == "patrol"
    assert skill_names_for_seat("builder-tools-planner", role_hint="planner-dispatcher")[0] == "planner"

def test_unknown_role_hint_does_not_mask_seat_id_mapping() -> None:
    assert role_skill_for_hint("specialist") is None
    assert skill_names_for_seat("builder-2", role_hint="specialist")[0] == "builder"
