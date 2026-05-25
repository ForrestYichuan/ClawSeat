"""ClawSeat v3 config proposal render validator.

Per spec §16.7.2 — install.sh runs this before rendering project.toml.
Any violation → install.sh exits non-zero with stderr violations list.

Validates:
- tool/auth_mode/provider enum
- (tool, role) Gemini blacklist per §6.4
- proposal_status == approved + operator_approved_ts non-null
- role values exist in skill catalog (best-effort; warns if catalog missing)

See spec §16.7 (install-spec-2026-05-13-clawseat-v3-multi-team-protocol.md).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib  # noqa: F401 — kept for symmetry
else:  # pragma: no cover
    pass

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover — PyYAML is a hard dep in ClawSeat runtime
    yaml = None  # type: ignore


VALID_TOOL = frozenset({"claude", "codex", "gemini"})
VALID_AUTH_MODE = frozenset({"oauth", "oauth_token", "api"})
VALID_PROVIDER = frozenset({"anthropic", "openai", "google", "minimax"})
VALID_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VALID_TEAM_TYPE = frozenset({"subteam", "quality-docs"})
VALID_REVIEW_MODEL = frozenset({"dedicated_reviewer", "planner_owned"})
VALID_PLANNER_MODE = frozenset({"delivery", "quality_campaign"})
VALID_NOTIFY_POLICY = frozenset({"queue_drained_only", "never_notify_memory"})

# Known role catalog (post-review fix #2; spec §16.7.2 role catalog validation).
# Source of truth: planner/builder/reviewer/patrol are the original 4 specialist
# roles; v3 adds designer-image, designer-creative, content-narrative for the
# multi-team workflow. Memory is excluded — it's never a worker.
KNOWN_ROLES = frozenset(
    {
        "planner",
        "builder",
        "reviewer",
        "patrol",
        "designer",
        "designer-image",
        "designer-creative",
        "content-narrative",
    }
)

# §6.4 — (tool, role) blacklist; violation rejects render
GEMINI_BLACKLIST_ROLES = frozenset(
    {
        "memory",
        "reviewer",
        # backend / engine / business-logic builder is a category, not a literal role.
        # Phase 1 enforces via explicit role names; capability-based check is Phase 4.
    }
)
CODEX_BLACKLIST_ROLES = frozenset(
    {
        "memory",  # main memory long-context requirement
    }
)
MINIMAX_BLACKLIST_ROLES = frozenset(
    {
        "builder",
        "reviewer",
        "memory",
        # patrol/test-runner is allowed (specific purpose)
    }
)


class ProposalValidationError(RuntimeError):
    """Raised when one or more proposals fail validation."""

    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__(
            f"{len(violations)} proposal validation violation(s):\n  - "
            + "\n  - ".join(violations)
        )


@dataclass
class ValidationReport:
    proposal_file: Path
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML required to validate proposals")
    text = path.read_text(encoding="utf-8")
    # Strip leading frontmatter if present (---...---), then parse rest as YAML
    if text.startswith("---\n"):
        # Find the closing ---
        end = text.find("\n---\n", 4)
        if end == -1:
            end = text.find("\n---", 4)
        if end != -1:
            text = text[4:end]
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise RuntimeError(f"{path}: top-level YAML must be a mapping")
    return data


def _check_seat(
    seat: dict[str, Any],
    proposal_file: Path,
    seat_idx: int,
) -> tuple[list[str], list[str]]:
    violations: list[str] = []
    warnings: list[str] = []
    seat_ctx = f"{proposal_file.name} seat[{seat_idx}]"

    role = str(seat.get("role") or "").strip()
    tool = str(seat.get("tool") or "").strip()
    auth_mode = str(seat.get("auth_mode") or "").strip()
    provider = str(seat.get("provider") or "").strip()
    instance = str(seat.get("instance") or "").strip()

    if not role:
        violations.append(f"{seat_ctx}: missing required 'role'")
    elif role not in KNOWN_ROLES:
        # post-review fix #2: role catalog enforcement
        violations.append(
            f"{seat_ctx}: role={role!r} not in known catalog "
            f"{sorted(KNOWN_ROLES)}. Add to KNOWN_ROLES if intentional new role."
        )
    if tool not in VALID_TOOL:
        violations.append(
            f"{seat_ctx}: tool={tool!r} not in {sorted(VALID_TOOL)}"
        )
    if auth_mode not in VALID_AUTH_MODE:
        violations.append(
            f"{seat_ctx}: auth_mode={auth_mode!r} not in {sorted(VALID_AUTH_MODE)}"
        )
    if provider not in VALID_PROVIDER:
        violations.append(
            f"{seat_ctx}: provider={provider!r} not in {sorted(VALID_PROVIDER)}"
        )
    if instance and not VALID_IDENTIFIER_RE.match(instance):
        violations.append(
            f"{seat_ctx}: instance={instance!r} must match "
            "^[a-z0-9][a-z0-9-]*$"
        )

    # §6.4 blacklist
    if tool == "gemini" and role in GEMINI_BLACKLIST_ROLES:
        violations.append(
            f"{seat_ctx}: §6.4 Gemini blacklist — role={role!r} not allowed for gemini"
        )
    if tool == "codex" and role in CODEX_BLACKLIST_ROLES:
        violations.append(
            f"{seat_ctx}: §6.4 Codex blacklist — role={role!r} not allowed for codex"
        )
    if (
        tool == "claude"
        and provider == "minimax"
        and role in MINIMAX_BLACKLIST_ROLES
    ):
        violations.append(
            f"{seat_ctx}: §6.4 Minimax-mode blacklist — role={role!r} not allowed"
        )

    if "rationale" not in seat or not str(seat.get("rationale") or "").strip():
        warnings.append(
            f"{seat_ctx}: §16.4 requires 'rationale' field explaining tool choice"
        )

    return violations, warnings


def _seat_identity(seat: dict[str, Any]) -> str:
    role = str(seat.get("role") or "").strip()
    instance = str(seat.get("instance") or "").strip()
    return f"{role}-{instance}" if instance else role


def normalize_review_model_fields(data: dict[str, Any]) -> tuple[str, bool | None, bool]:
    """Return (review_model, dedicated_reviewer, used_legacy_mapping).

    Early multi-team drafts used a nested mapping:

      review_model:
        dedicated_reviewer: false
        planner_reviews: true

    The canonical schema now stores `review_model` as an enum string plus a
    top-level `dedicated_reviewer` boolean. Accept the old shape during
    validation/render and normalize it so reinstalling older approved
    proposals does not dead-end.
    """
    raw_review_model = data.get("review_model")
    top_level_dedicated = data.get("dedicated_reviewer") if "dedicated_reviewer" in data else None
    dedicated_reviewer = top_level_dedicated if isinstance(top_level_dedicated, bool) else None

    if isinstance(raw_review_model, dict):
        nested_dedicated = raw_review_model.get("dedicated_reviewer")
        planner_reviews = raw_review_model.get("planner_reviews")
        explicit_model = str(
            raw_review_model.get("model")
            or raw_review_model.get("mode")
            or raw_review_model.get("type")
            or ""
        ).strip()

        if explicit_model in VALID_REVIEW_MODEL:
            review_model = explicit_model
        elif nested_dedicated is False or planner_reviews is True:
            review_model = "planner_owned"
        elif nested_dedicated is True or planner_reviews is False:
            review_model = "dedicated_reviewer"
        else:
            review_model = ""

        if dedicated_reviewer is None and isinstance(nested_dedicated, bool):
            dedicated_reviewer = nested_dedicated
        if dedicated_reviewer is None and review_model == "planner_owned":
            dedicated_reviewer = False
        elif dedicated_reviewer is None and review_model == "dedicated_reviewer":
            dedicated_reviewer = True
        return review_model, dedicated_reviewer, True

    return str(raw_review_model or "").strip(), dedicated_reviewer, False


def _check_team_metadata(data: dict[str, Any], proposal_file: Path) -> list[str]:
    violations: list[str] = []
    team = str(data.get("team") or "").strip()
    if team and not VALID_IDENTIFIER_RE.match(team):
        violations.append(
            f"{proposal_file.name}: team={team!r} must match "
            "^[a-z0-9][a-z0-9-]*$"
        )

    if "autonomous" in data and not isinstance(data.get("autonomous"), bool):
        violations.append(f"{proposal_file.name}: autonomous must be a boolean")
    review_model, dedicated_reviewer, legacy_review_mapping = normalize_review_model_fields(data)
    raw_review_model = data.get("review_model")
    if legacy_review_mapping:
        nested_dedicated = raw_review_model.get("dedicated_reviewer") if isinstance(raw_review_model, dict) else None
        if "dedicated_reviewer" in raw_review_model and not isinstance(nested_dedicated, bool):
            violations.append(f"{proposal_file.name}: review_model.dedicated_reviewer must be a boolean")
        if not review_model:
            violations.append(
                f"{proposal_file.name}: legacy review_model mapping must imply "
                "review_model='planner_owned' or 'dedicated_reviewer'"
            )
    if review_model and review_model not in VALID_REVIEW_MODEL:
        violations.append(
            f"{proposal_file.name}: review_model={review_model!r} not in "
            f"{sorted(VALID_REVIEW_MODEL)}"
        )
    if "dedicated_reviewer" in data and not isinstance(data.get("dedicated_reviewer"), bool):
        violations.append(f"{proposal_file.name}: dedicated_reviewer must be a boolean")
    if dedicated_reviewer is False and review_model != "planner_owned":
        violations.append(
            f"{proposal_file.name}: dedicated_reviewer=false requires "
            "review_model='planner_owned'"
        )
    if review_model == "planner_owned" and dedicated_reviewer is not False:
        violations.append(
            f"{proposal_file.name}: review_model='planner_owned' requires "
            "dedicated_reviewer=false"
        )
    for key in ("loop", "stop_rule"):
        if key not in data:
            continue
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            violations.append(f"{proposal_file.name}: {key} must be a non-empty string")

    team_type = str(data.get("team_type") or "").strip()
    if team_type and team_type not in VALID_TEAM_TYPE:
        violations.append(
            f"{proposal_file.name}: team_type={team_type!r} not in "
            f"{sorted(VALID_TEAM_TYPE)}"
        )
    effective_team_type = (
        team_type
        or (
            "quality-docs"
            if team == "quality-docs" or bool(data.get("autonomous"))
            else "subteam"
        )
    )
    planner_mode = str(data.get("planner_mode") or "").strip()
    if planner_mode and planner_mode not in VALID_PLANNER_MODE:
        violations.append(
            f"{proposal_file.name}: planner_mode={planner_mode!r} not in "
            f"{sorted(VALID_PLANNER_MODE)}"
        )
    notify_policy = str(data.get("notify_policy") or "").strip()
    if notify_policy and notify_policy not in VALID_NOTIFY_POLICY:
        violations.append(
            f"{proposal_file.name}: notify_policy={notify_policy!r} not in "
            f"{sorted(VALID_NOTIFY_POLICY)}"
        )
    if "quality_gate_doc" in data:
        quality_gate_doc = data.get("quality_gate_doc")
        if not isinstance(quality_gate_doc, str) or not quality_gate_doc.strip():
            violations.append(f"{proposal_file.name}: quality_gate_doc must be a non-empty string")
    if effective_team_type == "quality-docs":
        if planner_mode and planner_mode != "quality_campaign":
            violations.append(
                f"{proposal_file.name}: quality-docs requires "
                "planner_mode='quality_campaign'"
            )
        if notify_policy and notify_policy != "never_notify_memory":
            violations.append(
                f"{proposal_file.name}: quality-docs requires "
                "notify_policy='never_notify_memory'"
            )
    elif effective_team_type == "subteam":
        if planner_mode and planner_mode != "delivery":
            violations.append(
                f"{proposal_file.name}: subteam requires planner_mode='delivery'"
            )
        if notify_policy and notify_policy != "queue_drained_only":
            violations.append(
                f"{proposal_file.name}: subteam requires "
                "notify_policy='queue_drained_only'"
            )

    if "ownership_paths" in data:
        paths = data.get("ownership_paths")
        if not isinstance(paths, list) or not paths:
            violations.append(f"{proposal_file.name}: ownership_paths must be a non-empty list")
        elif any(not isinstance(item, str) or not item.strip() for item in paths):
            violations.append(f"{proposal_file.name}: ownership_paths items must be non-empty strings")

    if "scaling_policy" in data:
        policy = data.get("scaling_policy")
        if not isinstance(policy, dict):
            violations.append(f"{proposal_file.name}: scaling_policy must be a mapping")
        else:
            max_builders = policy.get("max_builders")
            if max_builders is not None and max_builders != 3:
                violations.append(f"{proposal_file.name}: scaling_policy.max_builders must be 3")
            reviewer_gte = policy.get("reviewer_required_when_builders_gte")
            if reviewer_gte is not None and reviewer_gte != 2:
                violations.append(
                    f"{proposal_file.name}: "
                    "scaling_policy.reviewer_required_when_builders_gte must be 2"
                )
            overflow = str(policy.get("overflow_action") or "").strip()
            if overflow and overflow != "propose_new_subteam":
                violations.append(
                    f"{proposal_file.name}: scaling_policy.overflow_action "
                    "must be 'propose_new_subteam'"
                )
            fallback = str(policy.get("reviewer_fallback") or "").strip()
            if fallback and fallback != "planner":
                violations.append(
                    f"{proposal_file.name}: scaling_policy.reviewer_fallback "
                    "must be 'planner'"
                )
    return violations


def _check_subteam_policy(
    data: dict[str, Any],
    proposal_file: Path,
    seats: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    violations: list[str] = []
    warnings: list[str] = []
    team_type = str(data.get("team_type") or "").strip()
    if team_type != "subteam":
        return violations, warnings

    roles = [str(seat.get("role") or "").strip() for seat in seats]
    planner_count = roles.count("planner")
    builder_count = roles.count("builder")
    reviewer_count = roles.count("reviewer")
    review_model, dedicated_reviewer, _ = normalize_review_model_fields(data)
    planner_owned_review = (
        review_model == "planner_owned"
        or dedicated_reviewer is False
    )

    if planner_count != 1:
        violations.append(
            f"{proposal_file.name}: subteam must declare exactly one planner "
            f"(found {planner_count})"
        )
    if builder_count < 1:
        violations.append(f"{proposal_file.name}: subteam must declare at least one builder")
    if builder_count > 3:
        violations.append(
            f"{proposal_file.name}: subteam declares {builder_count} builders; "
            "max is 3, propose a new subteam instead"
        )
    if builder_count >= 2 and reviewer_count < 1:
        violations.append(
            f"{proposal_file.name}: subteam with {builder_count} builders "
            "must declare a reviewer"
        )
    if planner_owned_review and builder_count != 1:
        violations.append(
            f"{proposal_file.name}: review_model='planner_owned' is only valid "
            "for lightweight subteams with exactly one builder"
        )
    if planner_owned_review and reviewer_count > 0:
        violations.append(
            f"{proposal_file.name}: review_model='planner_owned' must not declare "
            "a dedicated reviewer seat"
        )
    if reviewer_count > 1:
        violations.append(
            f"{proposal_file.name}: subteam should declare at most one reviewer "
            f"(found {reviewer_count})"
        )
    if "ownership_paths" not in data:
        warnings.append(
            f"{proposal_file.name}: subteam should declare ownership_paths "
            "so planner can route by module boundary"
        )
    return violations, warnings


def validate_proposal_file(path: Path | str) -> ValidationReport:
    """Validate one approved config yaml. Returns ValidationReport (caller decides)."""
    p = Path(path)
    report = ValidationReport(proposal_file=p)

    if not p.exists():
        report.violations.append(f"{p.name}: file not found")
        return report

    try:
        data = _load_yaml(p)
    except Exception as exc:  # noqa: BLE001 - yaml/io errors surfaced as violations
        report.violations.append(f"{p.name}: parse error: {exc}")
        return report

    status = str(data.get("proposal_status") or "").strip()
    if status != "approved":
        report.violations.append(
            f"{p.name}: proposal_status={status!r} (must be 'approved' to render)"
        )

    if not data.get("operator_approved_ts"):
        report.violations.append(
            f"{p.name}: operator_approved_ts is empty/null"
        )

    if "estimated_monthly_cost_usd" not in data:
        report.warnings.append(
            f"{p.name}: §16.4 requires estimated_monthly_cost_usd"
        )
    report.violations.extend(_check_team_metadata(data, p))

    seats = data.get("seats") or []
    if not isinstance(seats, list) or not seats:
        report.violations.append(f"{p.name}: 'seats' must be non-empty list")
    else:
        seen_identities: dict[str, int] = {}
        seat_dicts: list[dict[str, Any]] = []
        for idx, seat in enumerate(seats):
            if not isinstance(seat, dict):
                report.violations.append(
                    f"{p.name} seat[{idx}]: must be a mapping"
                )
                continue
            seat_dicts.append(seat)
            v, w = _check_seat(seat, p, idx)
            report.violations.extend(v)
            report.warnings.extend(w)
            identity = _seat_identity(seat)
            if identity in seen_identities:
                report.violations.append(
                    f"{p.name} seat[{idx}]: duplicate role/instance identity "
                    f"{identity!r}; first seen at seat[{seen_identities[identity]}]"
                )
            else:
                seen_identities[identity] = idx
        v, w = _check_subteam_policy(data, p, seat_dicts)
        report.violations.extend(v)
        report.warnings.extend(w)

    return report


def validate_proposal_dir(proposals_dir: Path | str) -> list[ValidationReport]:
    """Validate every *__approved.yaml in a directory. Returns list of reports."""
    d = Path(proposals_dir)
    if not d.exists():
        return []
    reports: list[ValidationReport] = []
    for yaml_file in sorted(d.glob("*__approved.yaml")):
        reports.append(validate_proposal_file(yaml_file))
    return reports


def assert_all_valid(proposals_dir: Path | str) -> None:
    """Raise ProposalValidationError if any approved config has violations.

    install.sh calls this before rendering project.toml. Warnings do not block.
    """
    reports = validate_proposal_dir(proposals_dir)
    all_violations: list[str] = []
    for r in reports:
        all_violations.extend(r.violations)
    if all_violations:
        raise ProposalValidationError(all_violations)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: python3 proposal_validator.py <proposals_dir>."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate ClawSeat v3 approved config proposals before render."
    )
    parser.add_argument("proposals_dir", help="Directory containing *__approved.yaml")
    parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="Treat warnings as violations (strict mode).",
    )
    args = parser.parse_args(argv)

    reports = validate_proposal_dir(args.proposals_dir)
    if not reports:
        print(f"No *__approved.yaml found in {args.proposals_dir}", file=sys.stderr)
        return 1

    any_violation = False
    for r in reports:
        if r.violations:
            any_violation = True
            print(f"FAIL {r.proposal_file.name}", file=sys.stderr)
            for v in r.violations:
                print(f"  ✗ {v}", file=sys.stderr)
        else:
            print(f"PASS {r.proposal_file.name}")
        for w in r.warnings:
            if args.warnings_as_errors:
                any_violation = True
                print(f"  ✗ (strict) {w}", file=sys.stderr)
            else:
                print(f"  ⚠ {w}", file=sys.stderr)

    return 1 if any_violation else 0


if __name__ == "__main__":
    raise SystemExit(main())
