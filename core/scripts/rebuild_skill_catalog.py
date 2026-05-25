#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


CACHE_TTL_SECONDS = 30 * 60
VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[2]


def _home() -> Path:
    return Path(os.environ.get("CLAWSEAT_SKILL_CATALOG_HOME", str(Path.home()))).expanduser()


def cache_path() -> Path:
    override = os.environ.get("CLAWSEAT_SKILL_CATALOG_CACHE")
    if override:
        return Path(override).expanduser()
    return _home() / ".agents" / "cache" / "skill-catalog.json"


def source_roots() -> dict[str, Path]:
    home = _home()
    return {
        "clawseat": Path(
            os.environ.get("CLAWSEAT_SKILL_CATALOG_AGENTS_ROOT", str(home / ".agents" / "skills"))
        ).expanduser(),
        "gstack": Path(
            os.environ.get("CLAWSEAT_SKILL_CATALOG_CLAUDE_ROOT", str(home / ".claude" / "skills"))
        ).expanduser(),
        "marketplace": Path(
            os.environ.get(
                "CLAWSEAT_SKILL_CATALOG_MARKETPLACES_ROOT",
                str(home / ".claude" / "plugins" / "marketplaces"),
            )
        ).expanduser(),
    }


def _is_fresh(path: Path, *, now: float | None = None) -> bool:
    if not path.exists():
        return False
    current = now if now is not None else datetime.now(timezone.utc).timestamp()
    return current - path.stat().st_mtime < CACHE_TTL_SECONDS


def _read_cache(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def catalog_markdown_path() -> Path:
    return REPO_ROOT / "core" / "references" / "skill-catalog.md"


def _write_markdown_catalog(path: Path, payload: dict) -> None:
    skills = payload.get("skills", [])
    lines = [
        "# Skill Catalog",
        "",
        "Generated foundation catalog for planner routing and skill discovery. Run "
        "`python3 core/scripts/rebuild_skill_catalog.py --force --update-md` to refresh this snapshot and the lazy JSON cache at `~/.agents/cache/skill-catalog.json`.",
        "",
        "## Source Notes",
        "",
        "- `~/.agents/skills/` - ClawSeat project and machine workflow skills.",
        "- `~/.claude/skills/` - gstack and local Claude skills.",
        "- `~/.claude/plugins/marketplaces/` - Anthropic/Claude marketplace plugin docs.",
        "",
        f"Total unique entries in this catalog: {len(skills)}.",
        "",
        "| Skill | Source | Purpose | When to use | Command form |",
        "| --- | --- | --- | --- | --- |",
    ]
    for skill in skills:
        source = str(skill.get("source", ""))
        name = str(skill.get("name", ""))
        description = str(skill.get("description", ""))
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(name),
                    _md_cell(_source_label(source)),
                    _md_cell(description),
                    _md_cell(_when_to_use(source)),
                    _md_cell(f"Skill: {name}"),
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _md_cell(value: str) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    compact = compact.replace("|", "\\|")
    if len(compact) > 120:
        compact = compact[:117].rstrip() + "..."
    return compact


def _source_label(source: str) -> str:
    return {
        "clawseat": "~/.agents/skills/",
        "gstack": "~/.claude/skills/",
        "marketplace": "~/.claude/plugins/marketplaces/",
    }.get(source, source)


def _when_to_use(source: str) -> str:
    if source == "clawseat":
        return "ClawSeat seat workflow needs this role capability"
    return "Use when the matching workflow is requested"


def _candidate_docs(root: Path, *, source: str) -> Iterable[Path]:
    if not root.exists():
        return []
    docs: dict[str, Path] = {}
    for child in sorted(root.iterdir()):
        if child.is_dir() or child.is_symlink():
            names = ("SKILL.md", "README.md") if source == "marketplace" else ("SKILL.md",)
            for name in names:
                doc = child / name
                if doc.exists():
                    docs[str(doc.resolve())] = doc
    for doc in root.rglob("SKILL.md"):
        if doc.exists():
            docs[str(doc.resolve())] = doc
    if source == "marketplace":
        for doc in root.rglob("README.md"):
            if doc.exists():
                docs[str(doc.resolve())] = doc
    return sorted(docs.values(), key=lambda path: str(path))


def _frontmatter_name(text: str) -> str:
    match = re.search(r"(?m)^name:\s*[\"']?([^\"'\n]+)[\"']?\s*$", text)
    return match.group(1).strip() if match else ""


def _frontmatter_description(text: str) -> str:
    inline = re.search(r"(?m)^description:\s*[\"']?(.+?)[\"']?\s*$", text)
    if inline and inline.group(1).strip() not in {"|", ">"}:
        return inline.group(1).strip().strip('"')

    block = re.search(r"(?ms)^description:\s*[|>]\s*\n(?P<body>(?:\s+.+\n?)+)", text)
    if block:
        lines = [line.strip() for line in block.group("body").splitlines()]
        lines = [line for line in lines if line]
        if lines:
            return " ".join(lines[:3])
    return ""


def _fallback_description(text: str, path: Path) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {"---"}:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if not stripped.startswith(("<!--", "`")):
            return stripped
    return path.stem.replace("-", " ")


def _description(text: str, path: Path) -> str:
    return _frontmatter_description(text) or _fallback_description(text, path)


def _skill_name(path: Path, text: str, *, source: str) -> str:
    name = _frontmatter_name(text)
    if name:
        return name
    if path.name == "README.md":
        return path.parent.name
    return path.parent.name


def _scan_source(source: str, root: Path) -> list[dict[str, str]]:
    skills: list[dict[str, str]] = []
    for doc in _candidate_docs(root, source=source):
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        skills.append(
            {
                "name": _skill_name(doc, text, source=source),
                "source": source,
                "path": str(doc),
                "description": _description(text, doc),
            }
        )
    return skills


def _dedupe_skills(skills: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    deduped: dict[tuple[str, str], dict[str, str]] = {}
    for skill in skills:
        key = (skill["name"], skill["source"])
        existing = deduped.get(key)
        if existing is None or _dedupe_rank(skill) < _dedupe_rank(existing):
            deduped[key] = skill
    return sorted(deduped.values(), key=lambda item: (item["source"], item["name"], item["path"]))


def _dedupe_rank(skill: dict[str, str]) -> tuple[int, str]:
    path = Path(skill["path"])
    preferred_doc = 0 if path.name == "SKILL.md" else 1
    return (preferred_doc, str(path.resolve()))


def rebuild_catalog() -> dict:
    skills: list[dict[str, str]] = []
    for source, root in source_roots().items():
        skills.extend(_scan_source(source, root))
    skills = _dedupe_skills(skills)
    return {
        "version": VERSION,
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "skills": skills,
    }


def load_or_rebuild(*, force: bool = False) -> dict:
    path = cache_path()
    if not force and _is_fresh(path):
        return _read_cache(path)
    payload = rebuild_catalog()
    _write_cache(path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the ClawSeat skill catalog cache.")
    parser.add_argument("--force", action="store_true", help="ignore the 30 minute lazy cache TTL")
    parser.add_argument(
        "--update-md",
        action="store_true",
        help="also rewrite core/references/skill-catalog.md from rebuilt JSON cache",
    )
    args = parser.parse_args()
    payload = load_or_rebuild(force=args.force or args.update_md)
    if args.update_md:
        _write_markdown_catalog(catalog_markdown_path(), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
