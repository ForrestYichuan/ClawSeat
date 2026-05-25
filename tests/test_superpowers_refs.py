from __future__ import annotations

from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_BORROWED = _REPO / "core" / "references" / "superpowers-borrowed"

_SEATS = (
    "memory-oracle",
    "planner",
    "builder",
    "reviewer",
    "patrol",
    "designer",
)


def test_superpowers_borrowed_tree_is_not_integrated() -> None:
    assert not _BORROWED.exists()


def test_seat_skills_do_not_reference_superpowers() -> None:
    for seat in _SEATS:
        path = _REPO / "core" / "skills" / seat / "SKILL.md"
        text = path.read_text(encoding="utf-8").lower()
        assert "superpowers" not in text, seat
        assert "superpowers-borrowed" not in text, seat
        assert "borrowed practices" not in text, seat


def test_skill_catalog_does_not_expose_superpowers() -> None:
    text = (_REPO / "core" / "references" / "skill-catalog.md").read_text(encoding="utf-8").lower()
    assert "superpowers" not in text
    assert "superpowers-borrowed" not in text


def test_v3_seat_templates_do_not_reference_superpowers() -> None:
    for path in (_REPO / "core" / "seat-templates").glob("*.yaml"):
        text = path.read_text(encoding="utf-8").lower()
        assert "superpowers" not in text, path.name
