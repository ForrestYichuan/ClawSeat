from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import agent_admin  # noqa: E402


def _render(role: str, *, engineer_id: str = "builder", tool: str = "codex") -> str:
    session = SimpleNamespace(
        engineer_id=engineer_id,
        tool=tool,
        workspace=f"/tmp/agents/workspaces/install/{engineer_id}",
        auth_mode="oauth",
        provider="",
    )
    project = SimpleNamespace(
        name="install",
        repo_root=str(_REPO),
        engineers=[engineer_id],
        template_name="",
        seat_overrides={},
    )
    engineer = SimpleNamespace(
        engineer_id=engineer_id,
        role=role,
        role_details=[],
        aliases=[],
        skills=[],
        default_tool=tool,
        default_auth_mode="oauth",
        default_provider="",
    )
    rendered = agent_admin.TEMPLATE_HANDLERS.render_template_text(
        tool,
        session,
        project,
        engineer_override=engineer,
        project_engineers={engineer_id: engineer},
        engineer_order=[engineer_id],
    )
    return rendered["AGENTS.md"]


def test_workspace_protocol_reminder_is_before_read_first_for_specialist() -> None:
    text = _render("builder")

    assert "## ⚠ Protocol Reminder" in text
    assert text.index("## ⚠ Protocol Reminder") < text.index("**Read first:**")
    assert "complete_handoff.py" in text
    assert "NOT optional" in text
    assert "Fan-out trigger" in text


def test_memory_protocol_reminder_prioritizes_v3_brief_queue() -> None:
    text = _render("memory-oracle", engineer_id="memory", tool="codex")

    assert "## ⚠ Protocol Reminder" in text
    assert "`agent_admin.py brief planner-status --project <project>`" in text
    assert "Brief Fidelity" in text
    assert "do not weaken product intent" in text
    assert "memory→planner uses `agent_admin.py brief queue`" in text
    assert "runtime blocks v3 memory→planner split-brain dispatch" in text
    assert "agent_admin task create` -> workflow.md -> `dispatch_task.py`" not in text
