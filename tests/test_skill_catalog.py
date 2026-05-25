from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_CATALOG = _REPO / "core" / "references" / "skill-catalog.md"
_SCRIPT = _REPO / "core" / "scripts" / "rebuild_skill_catalog.py"


def test_skill_catalog_has_three_sources() -> None:
    text = _CATALOG.read_text(encoding="utf-8")

    assert "~/.agents/skills/" in text
    assert "~/.claude/skills/" in text
    assert "~/.claude/plugins/marketplaces/" in text
    assert "core/references/superpowers-borrowed/" not in text
    assert text.count("\n| ") >= 50


def test_skill_catalog_lazy_cache_works(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    claude = tmp_path / "claude"
    marketplace = tmp_path / "marketplace"
    cache = tmp_path / "cache" / "skill-catalog.json"

    (agents / "alpha").mkdir(parents=True)
    (agents / "alpha" / "SKILL.md").write_text(
        '---\nname: alpha\ndescription: "Alpha skill."\n---\n',
        encoding="utf-8",
    )
    (claude / "beta").mkdir(parents=True)
    (claude / "beta" / "SKILL.md").write_text("# Beta\n\nBeta skill.\n", encoding="utf-8")
    (marketplace / "gamma").mkdir(parents=True)
    (marketplace / "gamma" / "README.md").write_text("# Gamma\n\nGamma plugin.\n", encoding="utf-8")

    env = {
        **os.environ,
        "CLAWSEAT_SKILL_CATALOG_CACHE": str(cache),
        "CLAWSEAT_SKILL_CATALOG_AGENTS_ROOT": str(agents),
        "CLAWSEAT_SKILL_CATALOG_CLAUDE_ROOT": str(claude),
        "CLAWSEAT_SKILL_CATALOG_MARKETPLACES_ROOT": str(marketplace),
    }
    first = subprocess.run(
        [sys.executable, str(_SCRIPT), "--force"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    assert {item["source"] for item in first_payload["skills"]} == {
        "clawseat",
        "gstack",
        "marketplace",
    }

    (agents / "new-skill").mkdir()
    (agents / "new-skill" / "SKILL.md").write_text("# New Skill\n", encoding="utf-8")
    second = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout) == first_payload


def test_skill_catalog_no_duplicates_md_and_json() -> None:
    """Both skill-catalog.md and JSON cache have no duplicate skill entries."""
    cache = Path("~/.agents/cache/skill-catalog.json").expanduser()
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        keys = [(skill["name"], skill["source"]) for skill in data.get("skills", [])]
        assert len(keys) == len(set(keys)), "JSON cache has duplicate skill entries"

    md = _CATALOG.read_text(encoding="utf-8")
    rows = [
        line
        for line in md.splitlines()
        if line.startswith("| ") and not line.startswith("| ---") and not line.startswith("| Skill |")
    ]
    keys: list[tuple[str, str]] = []
    for row in rows:
        parts = [part.strip() for part in row.strip("|").split("|")]
        if len(parts) >= 2:
            keys.append((parts[0], parts[1]))
    assert len(keys) == len(set(keys)), "skill-catalog.md has duplicate skill entries"
