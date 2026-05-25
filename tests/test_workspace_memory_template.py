from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from agent_admin_template import TemplateHandlers, TemplateHooks  # noqa: E402


def _session(tool: str = "claude") -> SimpleNamespace:
    return SimpleNamespace(
        engineer_id="memory",
        project="cartooner",
        tool=tool,
        workspace="/Users/ywf/.agents/workspaces/cartooner/memory",
        auth_mode="oauth",
        provider="",
    )


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        name="cartooner",
        repo_root=str(_REPO),
        tasks_root="/Users/ywf/.agents/tasks/cartooner",
    )


def _engineer() -> SimpleNamespace:
    return SimpleNamespace(
        engineer_id="memory",
        role="memory",
        role_details=[],
        aliases=[],
        skills=[],
    )


def _handlers() -> TemplateHandlers:
    engineer = _engineer()
    hooks = TemplateHooks(
        ensure_dir=lambda path: path.mkdir(parents=True, exist_ok=True),
        write_text=lambda path, content: path.write_text(content, encoding="utf-8"),
        load_engineer=lambda _seat: engineer,
        project_template_context=lambda _project: None,
        q=lambda value: value,
        render_authority_lines=lambda _engineer: [],
        render_protocol_reminder_lines=lambda _engineer, _role, **_: [],
        render_read_first_lines=lambda _session, _project, _engineer: [],
        render_harness_runtime_lines=lambda _engineer: [],
        render_project_seat_map_lines=lambda *args, **kwargs: [],
        render_seat_boundary_lines=lambda _session, _engineer: [],
        render_communication_protocol_lines=lambda _engineer, _project, **_: [],
        render_dispatch_playbook_lines=lambda _session, _project, _engineer: [],
        render_loaded_skills_lines=lambda _engineer, _seat: [],
        render_optional_skills_catalog=lambda _skills: "",
        workspace_contract_payload=lambda *args, **kwargs: {"project": "cartooner"},
        workspace_contract_fingerprint=lambda _payload: "fp-test",
        render_workspace_contract_text=lambda *args, **kwargs: "contract = true\n",
        render_role_line=lambda _engineer, _compact: "",
        render_role_details_lines=lambda _engineer: [],
        render_aliases_lines=lambda _engineer: [],
        render_heartbeat_text=lambda _session, _project, _engineer: None,
        render_heartbeat_manifest_text=lambda *args, **kwargs: None,
    )
    return TemplateHandlers(hooks)


def test_memory_workspace_claude_renders_l3_hub_without_worker_vocab() -> None:
    rendered = _handlers().render_template_text("claude", _session("claude"), _project())
    text = rendered["CLAUDE.md"]

    assert "L3 hub" in text
    assert "RFC-002 §2.2" in text
    assert "project.toml" in text
    assert "TEAM_OWNERSHIP.md" in text
    assert "quality-docs/QUALITY.md" in text
    assert "queue is drained" in text
    assert "Planner Selection" in text
    assert "status gate first" in text
    assert "tie-breakers inside the same status tier" in text
    assert "busy, dirty, or `idle_unmerged`" in text
    assert "watchdog/tmux captures as observations" in text
    assert "never derive dispatch policy" in text
    assert "seat_overrides" in text
    assert "specialist" not in text.lower()
    assert "return to planner" not in text.lower()


def test_memory_workspace_gemini_renders_tool_specific_paths() -> None:
    rendered = _handlers().render_template_text("gemini", _session("gemini"), _project())
    text = rendered["GEMINI.md"]

    assert "~/.gemini/skills/" in text
    assert "~/.gemini/log/gemini-tui.log" in text
    assert "/run-bash-in-repo" in text
    assert "TEAM_OWNERSHIP.md" in text
    assert "quality-docs/QUALITY.md" in text
    assert "Planner Selection" in text
    assert "status gate first" in text
    assert "tie-breakers inside the same status tier" in text
    assert "watchdog/tmux captures as observations" in text
    assert "~/.agents/skills/" not in text


def test_memory_workspace_claude_renders_claude_skill_paths() -> None:
    rendered = _handlers().render_template_text("claude", _session("claude"), _project())
    text = rendered["CLAUDE.md"]

    assert "~/.agents/skills/" in text
    assert "TEAM_OWNERSHIP.md" in text
    assert "~/.gemini/skills/" not in text


def test_non_primary_memory_instruction_files_are_compact_pointers() -> None:
    rendered = _handlers().render_template_text("claude", _session("claude"), _project())

    assert "L3 hub" in rendered["CLAUDE.md"]
    assert "canonical instruction file is `CLAUDE.md`" in rendered["AGENTS.md"]
    assert "L3 hub" not in rendered["AGENTS.md"]
    assert len(rendered["AGENTS.md"]) < 500


def test_codex_memory_workspace_gets_memory_docs_for_cross_tool_migration() -> None:
    rendered = _handlers().render_template_text("codex", _session("codex"), _project())
    text = rendered["AGENTS.md"]

    assert "CLAUDE.md" in rendered
    assert "GEMINI.md" in rendered
    assert "Project Memory Seat - Codex" in text
    assert "status gate first" in text
    assert "tie-breakers inside the same status tier" in text
    assert "watchdog/tmux captures as observations" in text
    assert "L3 hub" in text
    assert "Primary instruction file:" in text
    assert "~/.codex/" in text


def test_claude_settings_render_without_toml_modules(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "tomllib", None)
    monkeypatch.setitem(sys.modules, "tomli", None)

    text = _handlers()._render_claude_settings(_session("claude"))

    assert '"workspace_label": "memory"' in text


def test_memory_workspace_all_supported_tools_render_tool_specific_contracts() -> None:
    cases = {
        "claude": ("CLAUDE.md", "Project Memory Seat - Claude", "Claude settings", "~/.agents/skills/"),
        "codex": ("AGENTS.md", "Project Memory Seat - Codex", "Codex config", "~/.codex/"),
        "gemini": ("GEMINI.md", "Project Memory Seat - Gemini", "Gemini logs", "~/.gemini/skills/"),
    }

    for tool, (doc_name, title, path_label, tool_path) in cases.items():
        rendered = _handlers().render_template_text(tool, _session(tool), _project())
        text = rendered[doc_name]

        assert title in text
        assert path_label in text
        assert tool_path in text
        assert "agent_admin.py brief queue" in text
        assert "runtime blocks the v3 memory→planner split-brain path" in text
        assert "dispatch_task.py" in text
        assert "complete_handoff.py" in text
        assert "send-and-verify.sh" in text
