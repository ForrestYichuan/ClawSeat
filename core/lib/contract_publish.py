"""ClawSeat v3 contract publish/snapshot helper (Phase 3).

Snapshots runtime contract → tasks/<project>/contracts/<name>__v<ver>/published.yaml
and initializes consumer-pacts/ + review.md. Per spec §4.6.1.

Idempotent: running twice on same runtime content produces identical snapshot
(modulo published_ts in frontmatter, which is preserved if already set).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


class ContractPublishError(RuntimeError):
    pass


def _agents_root() -> Path:
    return Path(
        os.environ.get(
            "CLAWSEAT_REAL_HOME",
            os.environ.get("HOME", str(Path.home())),
        )
    ).expanduser() / ".agents"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_comment_frontmatter(text: str, path: Path) -> tuple[dict, str]:
    """Read //-prefixed YAML frontmatter and return (frontmatter, body)."""
    if yaml is None:
        raise ContractPublishError("PyYAML required")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "// ---":
        raise ContractPublishError(f"{path}: proto contract must start with '// ---'")

    front_lines: list[str] = []
    body_start = None
    for idx, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        if stripped == "// ---":
            body_start = idx + 1
            break
        if not line.startswith("//"):
            raise ContractPublishError(f"{path}: unterminated proto frontmatter")
        front_lines.append(line[2:].lstrip())

    if body_start is None:
        raise ContractPublishError(f"{path}: unterminated proto frontmatter")
    front = yaml.safe_load("".join(front_lines)) or {}
    if not isinstance(front, dict):
        raise ContractPublishError(f"{path}: frontmatter must be a mapping")
    return front, "".join(lines[body_start:])


def _read_runtime_contract(path: Path) -> tuple[dict, str, str]:
    """Return (frontmatter_dict, full_text, body_after_frontmatter).

    Round 4 #B: body (executable schema after closing ---) is now extracted
    and threaded into the snapshot so drift_check can detect body changes.
    Round 5: support comment-prefixed proto frontmatter too.
    """
    if yaml is None:
        raise ContractPublishError("PyYAML required")
    full_text = path.read_text(encoding="utf-8")
    body = ""
    if path.suffix == ".proto" or full_text.startswith("// ---\n"):
        front, body = _read_comment_frontmatter(full_text, path)
        return front, full_text, body
    if full_text.startswith("---\n"):
        end = full_text.find("\n---\n", 4)
        if end == -1:
            end = full_text.find("\n---", 4)
        if end == -1:
            raise ContractPublishError(f"{path}: unterminated frontmatter")
        front = yaml.safe_load(full_text[4:end]) or {}
        # body starts after the closing --- marker
        body_start = full_text.find("\n", end + 1) + 1
        body = full_text[body_start:]
    else:
        front = yaml.safe_load(full_text) or {}
    if not isinstance(front, dict):
        raise ContractPublishError(f"{path}: frontmatter must be a mapping")
    return front, full_text, body


def publish_snapshot(
    contract_name: str,
    version: str,
    project: str,
    runtime_path: Path,
    consumers: list[str] | None = None,
) -> Path:
    """Copy runtime contract to published snapshot. Returns snapshot path.

    Side effects:
    - creates tasks/<p>/contracts/<name>__v<ver>/ if absent
    - writes published.yaml (snapshot of runtime)
    - creates consumer-pacts/ + drift-checks/ subdirs (empty)
    - if review.md absent, writes placeholder
    """
    if not runtime_path.exists():
        raise ContractPublishError(f"runtime contract not found: {runtime_path}")

    front, _, body = _read_runtime_contract(runtime_path)
    # Validate basic fields
    for required in ("contract_name", "version"):
        if required not in front:
            raise ContractPublishError(
                f"{runtime_path}: missing required frontmatter field {required!r}"
            )
    if str(front["contract_name"]) != contract_name:
        raise ContractPublishError(
            f"runtime contract_name={front['contract_name']!r} mismatches CLI {contract_name!r}"
        )
    if str(front["version"]) != version:
        raise ContractPublishError(
            f"runtime version={front['version']!r} mismatches CLI {version!r}"
        )

    snapshot_dir = (
        _agents_root() / "tasks" / project / "contracts"
        / f"{contract_name}__v{version}"
    )
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "consumer-pacts").mkdir(exist_ok=True)
    (snapshot_dir / "drift-checks").mkdir(exist_ok=True)

    # If status not yet 'published', stamp it now; preserve existing published_ts
    if front.get("status") != "published":
        front["status"] = "published"
    if "published_ts" not in front:
        front["published_ts"] = _utc_now()
    if consumers is not None:
        front["consumers"] = consumers
    front.setdefault("owner_team", project)

    snapshot_path = snapshot_dir / "published.yaml"
    # Round 4 #B: snapshot must preserve the runtime body (executable schema).
    snapshot_text = (
        "---\n"
        + yaml.safe_dump(front, default_flow_style=False, sort_keys=False, allow_unicode=True)
        + "---\n"
        + body
    )
    snapshot_path.write_text(snapshot_text, encoding="utf-8")

    review_path = snapshot_dir / "review.md"
    if not review_path.exists():
        review_path.write_text(
            f"# Contract review: {contract_name} v{version}\n\n"
            f"Reviewer: <pending>\n"
            f"Verdict: <pending>\n\n"
            f"Notes:\n- snapshot taken from {runtime_path}\n",
            encoding="utf-8",
        )

    return snapshot_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ClawSeat v3 contract publish/snapshot helper."
    )
    parser.add_argument("--contract", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--runtime-path", required=True, dest="runtime_path")
    parser.add_argument("--consumers", nargs="*", default=None,
                        help="Override/initialize consumer team list in snapshot.")
    args = parser.parse_args(argv)

    try:
        snapshot = publish_snapshot(
            contract_name=args.contract,
            version=args.version,
            project=args.project,
            runtime_path=Path(args.runtime_path),
            consumers=args.consumers,
        )
    except ContractPublishError as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 1
    print(f"published: {snapshot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
