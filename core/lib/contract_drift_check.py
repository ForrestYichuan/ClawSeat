"""ClawSeat v3 contract drift check (Phase 3).

Compares runtime SSOT contract (repo source tree) against published snapshot
(tasks/<project>/contracts/<name>__v<ver>/published.yaml). Reports field /
type / metadata drift.

Patrol or memory invoke this on each PR / merge per spec §4.6.3.

Exit codes:
  0: in sync
  1: drift detected (details on stdout)
  2: missing runtime or snapshot file / schema error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT_SCHEMA_PATH = REPO_ROOT / "core" / "schemas" / "contract.schema.json"


class DriftCheckError(RuntimeError):
    pass


def _validate_contract_schema(data: dict, source: str) -> None:
    """Post-retest #4: validate contract frontmatter against contract.schema.json.

    DSL contracts missing prototype_log fail here (spec §4.6.4 P8 enforce).
    Round 4 #D: data should already have datetimes stringified via _stringify_datetimes.
    """
    # Synthetic body field is not part of the schema; strip before validation
    payload = {k: v for k, v in data.items() if k != "_runtime_body"}
    try:
        import jsonschema  # type: ignore
    except ImportError:
        # Minimal fallback for the P8 invariant
        if payload.get("contract_type") == "dsl":
            if not payload.get("prototype_log"):
                raise DriftCheckError(
                    f"{source}: DSL contract missing required prototype_log (§4.6.4 P8)"
                )
            if not payload.get("sample_data"):
                raise DriftCheckError(
                    f"{source}: DSL contract missing required sample_data (§4.6.4 P8)"
                )
        for required in ("contract_name", "version", "owner_team", "status"):
            if required not in payload:
                raise DriftCheckError(f"{source}: contract missing {required!r}")
        return

    schema = json.loads(_CONTRACT_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        raise DriftCheckError(
            f"{source}: contract schema violation — {exc.message} (at {'.'.join(str(p) for p in exc.absolute_path)})"
        )


# Fields that exist only in the publish snapshot (not runtime SSOT).
# Stripped before diff so a runtime draft doesn't read as "metadata drift".
_PUBLISH_METADATA_FIELDS = frozenset(
    {"status", "published_ts", "consumers"}
)


def _strip_publish_metadata(data: dict) -> dict:
    """Return a copy of data without publish-only metadata fields."""
    return {k: v for k, v in data.items() if k not in _PUBLISH_METADATA_FIELDS}


@dataclass
class DriftReport:
    contract_name: str
    version: str
    runtime_path: Path
    snapshot_path: Path
    drifts: list[str] = field(default_factory=list)

    @property
    def in_sync(self) -> bool:
        return not self.drifts


def _agents_root() -> Path:
    return Path(
        os.environ.get(
            "CLAWSEAT_REAL_HOME",
            os.environ.get("HOME", str(Path.home())),
        )
    ).expanduser() / ".agents"


def _load_yaml(path: Path) -> dict:
    """Load frontmatter and extract executable body.

    Round 4 #B: stores raw body text under synthetic `_runtime_body` key so
    drift_check compares schema definition body too. Round 4 #D: stringify
    datetime/date scalars to ISO strings to satisfy jsonschema string types.
    """
    if yaml is None:
        raise DriftCheckError("PyYAML required")
    full_text = path.read_text(encoding="utf-8")
    text = full_text
    body = ""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end == -1:
            end = text.find("\n---", 4)
        if end != -1:
            body_start = text.find("\n", end + 1) + 1
            body = text[body_start:].rstrip()
            text = text[4:end]
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise DriftCheckError(f"{path}: top-level must be a mapping")
    data = _stringify_datetimes(data)
    if body:
        data["_runtime_body"] = body
    return data


def _load_proto(path: Path) -> dict:
    """Load //-prefixed YAML frontmatter and preserve proto body for drift."""
    if yaml is None:
        raise DriftCheckError("PyYAML required")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "// ---":
        raise DriftCheckError(
            f"{path}: proto contract must start with '// ---' frontmatter block"
        )

    front_lines: list[str] = []
    body_start = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "// ---":
            body_start = idx + 1
            break
        if not line.startswith("//"):
            raise DriftCheckError(f"{path}: unterminated proto frontmatter block")
        front_lines.append(line[2:].lstrip())

    if body_start is None:
        raise DriftCheckError(f"{path}: unterminated proto frontmatter block")
    data = yaml.safe_load("".join(front_lines))
    if not isinstance(data, dict):
        raise DriftCheckError(f"{path}: proto frontmatter not a mapping")
    data = _stringify_datetimes(data)
    body = "".join(lines[body_start:]).rstrip()
    if body:
        data["_runtime_body"] = body
    return data


def _stringify_datetimes(value):
    """Round 4 #D: recursively convert datetime/date objects to ISO strings.

    PyYAML safe_load auto-parses ISO timestamps to datetime; jsonschema
    string-typed fields then reject them. Normalize here.
    """
    from datetime import date, datetime
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _stringify_datetimes(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify_datetimes(v) for v in value]
    return value


def _diff_dicts(
    runtime: Any,
    snapshot: Any,
    path: str = "",
    drifts: list[str] | None = None,
) -> list[str]:
    """Recursive structural diff. Adds human-readable drift lines."""
    if drifts is None:
        drifts = []
    if type(runtime).__name__ != type(snapshot).__name__:
        drifts.append(
            f"{path or '/'}: type changed {type(snapshot).__name__} → {type(runtime).__name__}"
        )
        return drifts
    if isinstance(runtime, dict):
        runtime_keys = set(runtime.keys())
        snapshot_keys = set(snapshot.keys())
        for added in sorted(runtime_keys - snapshot_keys):
            drifts.append(f"{path}/{added}: ADDED in runtime (not in snapshot)")
        for removed in sorted(snapshot_keys - runtime_keys):
            drifts.append(f"{path}/{removed}: REMOVED in runtime (was in snapshot)")
        for shared in sorted(runtime_keys & snapshot_keys):
            _diff_dicts(runtime[shared], snapshot[shared], f"{path}/{shared}", drifts)
    elif isinstance(runtime, list):
        if len(runtime) != len(snapshot):
            drifts.append(
                f"{path or '/'}: list length changed {len(snapshot)} → {len(runtime)}"
            )
        for idx, (r, s) in enumerate(zip(runtime, snapshot)):
            _diff_dicts(r, s, f"{path}[{idx}]", drifts)
    else:
        if runtime != snapshot:
            drifts.append(f"{path or '/'}: value changed {snapshot!r} → {runtime!r}")
    return drifts


def check_drift(
    contract_name: str,
    version: str,
    project: str,
    repo_root: Path | None = None,
    runtime_path: Path | None = None,
    snapshot_path: Path | None = None,
) -> DriftReport:
    """Compare runtime contract vs published snapshot.

    runtime_path / snapshot_path overrides are for testing; production callers
    pass contract_name/version/project and let helper resolve canonical paths.
    """
    agents_root = _agents_root()

    if runtime_path is None:
        if repo_root is None:
            raise DriftCheckError(
                "runtime_path or repo_root must be provided to resolve runtime contract"
            )
        # Canonical runtime location: <repo>/core/<project>/contracts/<name>__v<ver>.{yaml,proto,json}
        # Fallback for STS dogfood: <repo>/core/sts/contracts/<name>__v<ver>.{yaml,proto,json}
        # Post-retest #4: try all spec §4.6.1 allowed extensions in order.
        candidates = []
        for ext in (".yaml", ".proto", ".json"):
            for root in (
                Path(repo_root) / "core" / project / "contracts",
                Path(repo_root) / "core" / "sts" / "contracts",
                Path(repo_root) / "contracts",
            ):
                candidates.append(root / f"{contract_name}__v{version}{ext}")
        runtime_path = next((c for c in candidates if c.exists()), candidates[0])

    if snapshot_path is None:
        snapshot_path = (
            agents_root / "tasks" / project / "contracts"
            / f"{contract_name}__v{version}" / "published.yaml"
        )

    report = DriftReport(
        contract_name=contract_name,
        version=version,
        runtime_path=runtime_path,
        snapshot_path=snapshot_path,
    )

    if not runtime_path.exists():
        raise DriftCheckError(f"runtime contract not found: {runtime_path}")
    if not snapshot_path.exists():
        raise DriftCheckError(f"published snapshot not found: {snapshot_path}")

    raw_runtime = _load_runtime_any(runtime_path)
    raw_snapshot = _load_yaml(snapshot_path)

    # Post-retest #4: validate BOTH sides against contract.schema.json.
    # An invalid runtime+snapshot pair that happens to match each other is NOT
    # in-sync — it's a schema violation.
    _validate_contract_schema(raw_runtime, str(runtime_path))
    _validate_contract_schema(raw_snapshot, str(snapshot_path))

    runtime_data = _strip_publish_metadata(raw_runtime)
    snapshot_data = _strip_publish_metadata(raw_snapshot)

    report.drifts = _diff_dicts(runtime_data, snapshot_data)
    return report


def _load_runtime_any(path: Path) -> dict:
    """Load runtime contract from .yaml, .proto, or .json per §4.6.1.

    For .proto/.json files, expect a YAML/JSON frontmatter block at the top
    (---...---) containing contract_name/version/owner_team etc. The body
    after the frontmatter is the actual schema definition (not parsed here).
    """
    if path.suffix == ".yaml":
        return _load_yaml(path)
    if path.suffix == ".json":
        text = path.read_text(encoding="utf-8")
        if text.lstrip().startswith("{"):
            return json.loads(text)
        # JSON file with frontmatter
        return _load_yaml(path)
    if path.suffix == ".proto":
        return _load_proto(path)
    raise DriftCheckError(
        f"{path}: unsupported runtime contract extension (allowed: .yaml/.proto/.json)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ClawSeat v3 contract drift check (runtime vs published snapshot)."
    )
    parser.add_argument("--contract", required=True, help="Contract name (e.g. EffectExpression)")
    parser.add_argument("--version", required=True, help="Semver e.g. 1.0.0")
    parser.add_argument("--project", required=True)
    parser.add_argument("--repo-root", default=None,
                        help="Repo root to resolve runtime path (default: $CLAWSEAT_ROOT or $REPO_ROOT)")
    parser.add_argument("--runtime-path", default=None, dest="runtime_path",
                        help="Override runtime contract path (testing)")
    parser.add_argument("--snapshot-path", default=None, dest="snapshot_path",
                        help="Override snapshot path (testing)")
    args = parser.parse_args(argv)

    repo_root = args.repo_root or os.environ.get("CLAWSEAT_ROOT") or os.environ.get("REPO_ROOT")
    repo_root = Path(repo_root) if repo_root else None

    try:
        report = check_drift(
            contract_name=args.contract,
            version=args.version,
            project=args.project,
            repo_root=repo_root,
            runtime_path=Path(args.runtime_path) if args.runtime_path else None,
            snapshot_path=Path(args.snapshot_path) if args.snapshot_path else None,
        )
    except DriftCheckError as exc:
        print(f"drift check error: {exc}", file=sys.stderr)
        return 2

    if report.in_sync:
        print(
            f"in_sync: {report.contract_name} v{report.version}\n"
            f"  runtime:  {report.runtime_path}\n"
            f"  snapshot: {report.snapshot_path}"
        )
        return 0

    print(f"DRIFT DETECTED: {report.contract_name} v{report.version}", file=sys.stderr)
    print(f"  runtime:  {report.runtime_path}", file=sys.stderr)
    print(f"  snapshot: {report.snapshot_path}", file=sys.stderr)
    for d in report.drifts:
        print(f"  ✗ {d}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
