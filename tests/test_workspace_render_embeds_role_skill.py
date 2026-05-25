"""Root-cause fix verification: bootstrap-rendered workspace files must keep
the canonical role SKILL reachable without pasting its long-form body hot.

Before 2026-04-24, `agent_admin_template.py` rendered AGENTS.md / CLAUDE.md
/ GEMINI.md purely from engineer.toml fields (mostly empty: `skills=[]`,
a single-line role_details). The authoritative role contract lived in
`core/skills/<role>/SKILL.md` but was not consumed by the render pipeline.

MP008 keeps that role mapping but slims the hot prompt: generated workspaces
now append a compact role skill hot contract that references the full
`core/skills/<role>/SKILL.md` instead of embedding every detailed section.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]
_CORE_SCRIPTS = _REPO / "core" / "scripts"
if str(_CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CORE_SCRIPTS))

import agent_admin_template  # noqa: E402


# ── helper-level tests ────────────────────────────────────────────────


def test_load_role_skill_content_strips_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "core" / "skills" / "builder"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: builder
            description: test role
            ---

            # Builder

            身份约束
            1. test constraint
            """
        ),
        encoding="utf-8",
    )
    info = agent_admin_template._load_role_skill_content(tmp_path, "builder")
    assert info is not None
    role, body = info
    assert role == "builder"
    # frontmatter stripped
    assert body.startswith("# Builder"), body[:60]
    assert "description: test role" not in body
    assert "身份约束" in body


def test_load_role_skill_content_handles_no_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "core" / "skills" / "patrol"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Patrol\n\nbody only.\n", encoding="utf-8")
    info = agent_admin_template._load_role_skill_content(tmp_path, "patrol")
    assert info is not None
    role, body = info
    assert role == "patrol"
    assert body.startswith("# Patrol")


def test_load_role_skill_content_returns_none_for_unknown_seat(tmp_path: Path) -> None:
    # No core/skills/* directory at all
    assert agent_admin_template._load_role_skill_content(tmp_path, "not-a-seat") is None


def test_load_role_skill_content_follows_memory_mapping(tmp_path: Path) -> None:
    """`memory` seat -> `memory-oracle` skill per seat_skill_mapping."""
    skill_dir = tmp_path / "core" / "skills" / "memory-oracle"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# memory-oracle\n", encoding="utf-8")
    info = agent_admin_template._load_role_skill_content(tmp_path, "memory")
    assert info is not None
    role, _ = info
    assert role == "memory-oracle"


def test_load_role_skill_content_handles_suffixed_seat_id(tmp_path: Path) -> None:
    """`builder-1` / `reviewer-2` should resolve to their base role skill."""
    skill_dir = tmp_path / "core" / "skills" / "builder"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# builder variant\n", encoding="utf-8")
    info = agent_admin_template._load_role_skill_content(tmp_path, "builder-1")
    assert info is not None
    assert info[0] == "builder"


def test_load_role_skill_content_role_hint_overrides_seat_id_mapping(tmp_path: Path) -> None:
    """role_hint wins over seat-id mapping (template lane: builder seat → builder-image skill)."""
    # Set up both the generic and the lane-specific skill
    (tmp_path / "core" / "skills" / "builder").mkdir(parents=True)
    (tmp_path / "core" / "skills" / "builder" / "SKILL.md").write_text("# Builder (engineering)\n", encoding="utf-8")
    (tmp_path / "core" / "skills" / "builder-image").mkdir(parents=True)
    (tmp_path / "core" / "skills" / "builder-image" / "SKILL.md").write_text(
        "---\nname: builder-image\n---\n# Builder Image\n\nimage lane skill body\n",
        encoding="utf-8",
    )
    info = agent_admin_template._load_role_skill_content(tmp_path, "builder", role_hint="builder-image")
    assert info is not None
    role, body = info
    assert role == "builder-image", f"expected builder-image, got {role!r}"
    assert "image lane skill body" in body
    assert "engineering" not in body


def test_load_role_skill_content_role_hint_falls_back_when_skill_missing(tmp_path: Path) -> None:
    """When role_hint SKILL.md doesn't exist, falls back to seat-id mapping."""
    (tmp_path / "core" / "skills" / "builder").mkdir(parents=True)
    (tmp_path / "core" / "skills" / "builder" / "SKILL.md").write_text("# Builder fallback\n", encoding="utf-8")
    # no builder-image dir
    info = agent_admin_template._load_role_skill_content(tmp_path, "builder", role_hint="builder-image")
    assert info is not None
    role, body = info
    assert role == "builder"
    assert "Builder fallback" in body


def test_load_role_skill_content_role_hint_alias_wins_for_dynamic_team_prefix(tmp_path: Path) -> None:
    """A team slug can contain role words; the engineer role_hint remains authoritative."""
    (tmp_path / "core" / "skills" / "planner").mkdir(parents=True)
    (tmp_path / "core" / "skills" / "planner" / "SKILL.md").write_text("# Planner alias\n", encoding="utf-8")
    (tmp_path / "core" / "skills" / "builder").mkdir(parents=True)
    (tmp_path / "core" / "skills" / "builder" / "SKILL.md").write_text("# Builder wrong prefix\n", encoding="utf-8")

    info = agent_admin_template._load_role_skill_content(
        tmp_path,
        "builder-tools-planner",
        role_hint="planner-dispatcher",
    )
    assert info is not None
    role, body = info
    assert role == "planner"
    assert "Planner alias" in body


def test_load_role_skill_content_unknown_role_hint_falls_back_to_seat_id(tmp_path: Path) -> None:
    (tmp_path / "core" / "skills" / "clawseat").mkdir(parents=True)
    (tmp_path / "core" / "skills" / "clawseat" / "SKILL.md").write_text("# Generic\n", encoding="utf-8")
    (tmp_path / "core" / "skills" / "builder").mkdir(parents=True)
    (tmp_path / "core" / "skills" / "builder" / "SKILL.md").write_text("# Builder seat\n", encoding="utf-8")

    info = agent_admin_template._load_role_skill_content(tmp_path, "builder-2", role_hint="specialist")
    assert info is not None
    role, body = info
    assert role == "builder"
    assert "Builder seat" in body


def test_role_skill_section_lines_empty_when_missing(tmp_path: Path) -> None:
    assert agent_admin_template._role_skill_section_lines(tmp_path, "unknown-seat") == []


def test_role_skill_section_lines_contains_hot_contract_and_reference(tmp_path: Path) -> None:
    skill_dir = tmp_path / "core" / "skills" / "reviewer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: reviewer\n---\n# Reviewer\n\nVerdict rules here.\n",
        encoding="utf-8",
    )
    lines = agent_admin_template._role_skill_section_lines(tmp_path, "reviewer")
    assert "## Role SKILL hot contract (canonical)" in lines
    joined = "\n".join(lines)
    assert "core/skills/reviewer/SKILL.md" in joined
    assert "full Role SKILL" in joined
    assert "Verdict rules here." not in joined
    assert "complete_handoff.py --source <exact current seat>" in joined
    assert "source=<exact planner seat>" in joined
    assert "review/latest" in joined
    hot_rules = [line for line in lines if line.startswith("- ")]
    assert len(hot_rules) == 8


# ── integration with the real repo's core/skills ──────────────────────


@pytest.mark.parametrize(
    ("seat_id", "expected_role"),
    [
        ("planner", "planner"),
        ("builder", "builder"),
        ("reviewer", "reviewer"),
        ("patrol", "patrol"),
        ("designer", "designer"),
        ("memory", "memory-oracle"),
        ("ancestor", "clawseat-ancestor"),
    ],
)
def test_real_repo_role_skills_load_for_canonical_seats(seat_id: str, expected_role: str) -> None:
    """All canonical ClawSeat seats must have a loadable role SKILL.md."""
    info = agent_admin_template._load_role_skill_content(_REPO, seat_id)
    assert info is not None, (
        f"seat {seat_id!r} should map to a role skill; missing core/skills/{expected_role}/SKILL.md?"
    )
    role, body = info
    assert role == expected_role
    assert len(body) > 100, f"{expected_role} SKILL.md should not be an empty stub"
    # A rendered role skill must not leak its frontmatter
    assert not body.startswith("---\n"), f"{expected_role} SKILL.md frontmatter not stripped"


def test_real_repo_role_skill_section_for_patrol_includes_contract_marker() -> None:
    """Patrol gets the same compact hot contract while keeping its SKILL referenced."""
    lines = agent_admin_template._role_skill_section_lines(_REPO, "patrol")
    joined = "\n".join(lines)
    assert "## Role SKILL hot contract (canonical)" in joined
    assert "core/skills/patrol/SKILL.md" in joined
    assert "Source of truth: queue files" in joined
    assert "tmux/watchdog captures are observations" in joined
    # Detailed patrol-only prose stays in the cold SKILL reference.
    assert "不写新 tests" not in joined


def test_real_repo_role_skill_section_for_planner_includes_contract_marker() -> None:
    lines = agent_admin_template._role_skill_section_lines(_REPO, "planner")
    joined = "\n".join(lines)
    assert "## Role SKILL hot contract (canonical)" in joined
    assert "core/skills/planner/SKILL.md" in joined
    assert "dispatch_task.py" in joined
    assert "complete_handoff.py --source <exact current seat>" in joined
    assert "source=<exact planner seat>" in joined
    assert "review/latest" in joined
    assert "planner delivers branch/commit evidence" in joined
    assert "memory integrates accepted work" in joined
    assert "builders never merge it or `main`" in joined
    assert "explicit user confirmation" in joined
    assert "failed, bounced, or reset queues are blocked" in joined
    hot_rules = [line for line in lines if line.startswith("- ")]
    assert len(hot_rules) == 8
    assert "## Workflow Authoring" not in joined
    stale_generic = "complete_handoff.py --source " + "planner"
    assert stale_generic not in joined
    # Compact hot contract should remain meaningful but not paste full SKILL.md.
    embedded_size = len(joined)
    assert 500 < embedded_size < 2500, f"planner workspace hot contract size out of budget: {embedded_size}"
