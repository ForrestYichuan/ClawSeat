from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


REPO = Path(__file__).resolve().parents[1]
ARCH = REPO / "docs" / "ARCHITECTURE.md"


def _template_seats(template: Path) -> list[str]:
    data = tomllib.loads(template.read_text(encoding="utf-8"))
    return [str(item["id"]) for item in data["engineers"]]


def test_architecture_project_template_roster_matches_templates() -> None:
    arch = ARCH.read_text(encoding="utf-8")

    for template in sorted((REPO / "templates").glob("*.toml")):
        name = template.stem
        seats = _template_seats(template)
        matching_lines = [line for line in arch.splitlines() if f"`{name}`" in line]
        assert matching_lines, f"ARCHITECTURE.md must document {name}"
        joined = "\n".join(matching_lines)
        if name == "clawseat-solo":
            assert "MULTI_TEAM_MINIMAL" in joined
            assert "planner+builder" in joined
            continue
        assert str(len(seats)) in joined, f"{name} count must be {len(seats)}"
        for seat in seats:
            assert seat in joined, f"{name} missing seat {seat}"


def test_architecture_template_roster_contains_three_templates() -> None:
    arch = ARCH.read_text(encoding="utf-8")
    template_section = arch.split("### Project Templates", 1)[1].split("### Solo Alias", 1)[0]
    assert "`clawseat-engineering`" in template_section
    assert "`clawseat-creative`" in template_section
    assert "`clawseat-solo`" in template_section
    assert "`cartooner-creative`" not in template_section
    assert "`team-creation`" not in template_section


def test_architecture_engineering_row_is_five_seats_with_reviewer_authority() -> None:
    arch = ARCH.read_text(encoding="utf-8")
    template_section = arch.split("### Project Templates", 1)[1].split("### Solo Alias", 1)[0]
    row = next(line for line in template_section.splitlines() if line.startswith("| `clawseat-engineering`"))
    assert "| 5 |" in row
    assert "reviewer" in row
    assert "qa" in row.lower()
