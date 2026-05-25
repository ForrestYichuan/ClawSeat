from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "core" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _toml_compat import load_safe as toml_load  # noqa: E402
from agent_admin_workspace import (  # noqa: E402
    render_communication_protocol_lines,
    render_protocol_reminder_lines,
    render_role_scope_summary,
    render_seat_boundary_lines,
)
from seat_skill_mapping import (  # noqa: E402
    role_skill_for_hint,
    role_skill_for_seat,
    skill_names_for_seat,
)


def test_solo_tui_template_is_valid_and_points_at_skill():
    template_path = REPO / "core" / "templates" / "solo-tui" / "template.toml"
    with template_path.open("rb") as fh:
        data = toml_load(fh)

    assert data["template_name"] == "solo-tui"
    engineers = data["engineers"]
    assert len(engineers) == 1
    solo = engineers[0]
    assert solo["id"] == "solo"
    assert solo["role"] == "solo-tui"
    assert solo["human_facing"] is True
    assert "{CLAWSEAT_ROOT}/core/skills/solo-tui/SKILL.md" in solo["skills"]
    assert (REPO / "core" / "skills" / "solo-tui" / "SKILL.md").is_file()


def test_solo_tui_role_skill_mapping_for_claude_code_bundle():
    assert role_skill_for_hint("solo-tui") == "solo-tui"
    assert role_skill_for_hint("user-proxy") == "solo-tui"
    assert role_skill_for_hint("spec-owner") == "solo-tui"
    assert role_skill_for_seat("solo") == "solo-tui"
    assert role_skill_for_seat("warden") == "solo-tui"

    skills = skill_names_for_seat("solo", role_hint="solo-tui")
    assert skills[0] == "solo-tui"
    assert "tmux-basics" in skills


def test_solo_tui_workspace_rules_are_lightweight():
    engineer = SimpleNamespace(
        role="solo-tui",
        skills=[],
        engineer_id="solo",
    )
    session = SimpleNamespace(
        engineer_id="solo",
        project="demo",
        project_record=None,
        project_engineers={},
        engineer_order=[],
    )

    reminder = "\n".join(render_protocol_reminder_lines(engineer, "solo-tui"))
    assert "No background patrol" in reminder
    assert "professional architecture/code advisor" in reminder
    assert "goal + context + boundary + anti-goal + acceptance + delivery" in reminder
    assert "queue/state tracking" in reminder
    assert "Inspiration Browser" in reminder
    assert "Patrol review" in reminder
    assert "review/latest" in reminder
    assert "complete_handoff.py" not in reminder
    assert "agent_admin.py brief planner-status" not in reminder

    boundary = "\n".join(render_seat_boundary_lines(session, engineer))
    assert "professional intent analysis" in boundary
    assert "AI-friendly specs" in boundary
    assert "do not monitor or poll by default" in boundary
    assert "project memory, planner, queue owner" in boundary
    assert "Inspiration Browser" in boundary
    assert "delivered-but-unintegrated" in boundary
    assert "review/latest" in boundary

    protocol = "\n".join(
        render_communication_protocol_lines(engineer, "demo", seat_id="solo")
    )
    assert "default to natural language" in protocol
    assert "concise professional request" in protocol
    assert "temporary reply path" in protocol
    assert "standing backchannel" in protocol

    assert render_role_scope_summary(engineer) == (
        "professional intent analysis, root-cause evidence, AI-friendly specs, "
        "product validation, and lightweight direct fixes"
    )
