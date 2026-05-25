from __future__ import annotations

from typing import Final


DEFAULT_ROLE_SKILL: Final[str] = "clawseat"
SHARED_SKILLS: Final[tuple[str, ...]] = (
    "clawseat",
    "gstack-harness",
    "tmux-basics",
)
ROLE_SKILL_ALIASES: Final[dict[str, str]] = {
    "planner-dispatcher": "planner",
    "project-memory": "memory-oracle",
    "memory-oracle": "memory-oracle",
    "solo-tui": "solo-tui",
    "user-proxy": "solo-tui",
    "spec-owner": "solo-tui",
    "product-tester": "solo-tui",
    "root-cause-scout": "solo-tui",
    "warden": "solo-tui",
    "qa": "patrol",
    "code-reviewer": "reviewer",
    "frontstage-supervisor": "clawseat-koder",
}
SEAT_SKILL_MAP: Final[dict[str, str]] = {
    # engineering / solo template seats (gstack-bound)
    "ancestor": "clawseat-ancestor",
    "planner": "planner",
    "memory": "memory-oracle",
    "solo": "solo-tui",
    "warden": "solo-tui",
    "builder": "builder",
    "reviewer": "reviewer",
    "patrol": "patrol",
    "designer": "designer",
    # clawseat-creative template seats (cartooner-harness-bound)
    # Full hyphenated id checked first by seat_skill_key, so these win
    # over the prefix-fallback (e.g. "builder-image" -> "builder").
    "writer": "cartooner-harness",
    "builder-image": "cartooner-harness",
    "builder-av": "cartooner-harness",
}


def seat_skill_key(seat_id: str) -> str:
    normalized = str(seat_id).strip().lower()
    if not normalized:
        return ""
    if normalized in SEAT_SKILL_MAP:
        return normalized
    if normalized in ROLE_SKILL_ALIASES:
        return normalized
    for creative_prefix in ("builder-image-", "builder-av-"):
        if normalized.startswith(creative_prefix):
            return creative_prefix.rstrip("-")
    first = normalized.split("-", 1)[0]
    if first in SEAT_SKILL_MAP or first in ROLE_SKILL_ALIASES:
        return first
    for token in normalized.split("-"):
        if token in SEAT_SKILL_MAP or token in ROLE_SKILL_ALIASES:
            return token
    return first


def role_skill_for_seat(seat_id: str) -> str:
    key = seat_skill_key(seat_id)
    if key in ROLE_SKILL_ALIASES:
        return ROLE_SKILL_ALIASES[key]
    return SEAT_SKILL_MAP.get(key, DEFAULT_ROLE_SKILL)


def role_skill_for_hint(role_hint: str | None) -> str | None:
    if not role_hint:
        return None
    key = seat_skill_key(role_hint)
    if key in ROLE_SKILL_ALIASES:
        return ROLE_SKILL_ALIASES[key]
    if key in SEAT_SKILL_MAP:
        return SEAT_SKILL_MAP[key]
    return None


def skill_names_for_seat(seat_id: str, role_hint: str | None = None) -> list[str]:
    role_skill = role_skill_for_hint(role_hint) or role_skill_for_seat(seat_id)
    ordered = [role_skill, *SHARED_SKILLS]
    seen: set[str] = set()
    result: list[str] = []
    for item in ordered:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
