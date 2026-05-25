#!/usr/bin/env python3
"""Audit tests that mention legacy, compatibility, or retired surfaces.

This is intentionally a static scanner. It does not import test modules, so it
is safe to run while investigating suite shape or pytest collection issues.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = REPO_ROOT / "tests"

LEGACY_RE = re.compile(
    r"(legacy|deprecated|retired|dead[-_]code|compat(?:ibility)?|"
    r"superseded|backwards?[ -]compat)",
    re.IGNORECASE,
)

REMOVAL_RE = re.compile(
    r"(does_not_exist|no_longer|no longer|not in text|not exists|"
    r"removed|retired|absent|rejects_retired|no_legacy)",
    re.IGNORECASE,
)

COMPAT_RE = re.compile(
    r"(backwards?[ -]compat|compat|legacy_.*maps|maps_to|migrat|alias|"
    r"fallback|still works|still accepted|preserves_legacy)",
    re.IGNORECASE,
)

COMMENT_OR_DOC_RE = re.compile(r"^\s*(#|\"\"\"|'''|\*)")


@dataclass(frozen=True)
class AuditEntry:
    path: str
    category: str
    hit_count: int
    sample_lines: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "category": self.category,
            "hit_count": self.hit_count,
            "sample_lines": list(self.sample_lines),
        }


def iter_test_files(tests_root: Path = TESTS_ROOT) -> Iterable[Path]:
    yield from sorted(tests_root.rglob("test_*.py"))


def find_hits(path: Path) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if LEGACY_RE.search(line):
            hits.append((number, line.strip()))
    return hits


def classify(path: Path, hits: list[tuple[int, str]]) -> str:
    rel = path.relative_to(REPO_ROOT).as_posix()
    haystack = "\n".join([rel, *(line for _, line in hits)])

    if REMOVAL_RE.search(haystack):
        return "removal_guard"
    if COMPAT_RE.search(haystack):
        return "migration_or_compat"
    if hits and all(COMMENT_OR_DOC_RE.search(line) for _, line in hits):
        return "stale_comment"
    return "review"


def build_entries() -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    for path in iter_test_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        hits = find_hits(path)
        if not hits and LEGACY_RE.search(rel):
            hits = [(0, f"<path>: {rel}")]
        if not hits:
            continue
        samples = tuple(f"{number}: {line}" for number, line in hits[:3])
        entries.append(
            AuditEntry(
                path=rel,
                category=classify(path, hits),
                hit_count=len(hits),
                sample_lines=samples,
            )
        )
    return entries


def summarize(entries: list[AuditEntry]) -> dict[str, object]:
    categories: dict[str, int] = {}
    for entry in entries:
        categories[entry.category] = categories.get(entry.category, 0) + 1
    return {
        "files_scanned": sum(1 for _ in iter_test_files()),
        "legacy_hit_files": len(entries),
        "categories": dict(sorted(categories.items())),
        "entries": [entry.as_dict() for entry in entries],
    }


def render_markdown(summary: dict[str, object]) -> str:
    entries = [AuditEntry(**entry) for entry in summary["entries"]]  # type: ignore[arg-type]
    by_category: dict[str, list[AuditEntry]] = {}
    for entry in entries:
        by_category.setdefault(entry.category, []).append(entry)

    lines = [
        "# Test Suite Legacy Audit",
        "",
        "Static scan of `tests/test_*.py` for legacy, deprecated, retired, dead-code, compatibility, and superseded language.",
        "",
        "## Summary",
        "",
        f"- Files scanned: {summary['files_scanned']}",
        f"- Files with legacy/compat text: {summary['legacy_hit_files']}",
    ]

    categories = summary["categories"]
    if isinstance(categories, dict):
        for category, count in categories.items():
            lines.append(f"- {category}: {count}")

    for category in ("migration_or_compat", "removal_guard", "stale_comment", "review"):
        grouped = by_category.get(category, [])
        if not grouped:
            continue
        lines.extend(["", f"## {category}", ""])
        for entry in grouped:
            lines.append(f"- `{entry.path}` ({entry.hit_count} hits)")
            for sample in entry.sample_lines:
                lines.append(f"  - {sample}")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    summary = summarize(build_entries())
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
