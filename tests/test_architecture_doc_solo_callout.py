from pathlib import Path


def test_architecture_has_solo_callout() -> None:
    """ARCHITECTURE.md documents clawseat-solo as a v3 minimal alias."""
    content = Path("docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    heading = "### Solo Alias (Minimal v3 Project Group)"
    assert heading in content
    idx = content.index(heading)
    section = content[idx:idx + 800]
    assert "MULTI_TEAM_MINIMAL" in section
    assert "planner+builder" in section
    for seat in ["project-memory", "builder", "planner", "quality-docs"]:
        assert seat in section, f"Solo callout missing seat: {seat}"
