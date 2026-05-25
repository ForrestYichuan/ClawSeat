"""Shared helpers for ClawSeat brief acceptance criteria.

The queue layer needs a cheap readiness gate before waking or claiming a brief,
and the acceptance executor needs to avoid treating reviewer/operator prose as a
shell command. This module keeps those two checks aligned.
"""

from __future__ import annotations

import re
import shlex
import shutil
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


_PLACEHOLDER_RE = re.compile(
    r"^\s*(TODO\b|TBD\b|FIXME\b|<待|待\s|replace\s+with\b)",
    re.IGNORECASE,
)

_SHELL_BUILTINS = {
    "!",
    ".",
    ":",
    "[",
    "[[",
    "cd",
    "echo",
    "eval",
    "exec",
    "exit",
    "export",
    "false",
    "printf",
    "pwd",
    "read",
    "set",
    "source",
    "test",
    "time",
    "true",
    "unset",
}


def load_brief_frontmatter(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML required")
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        end = text.find("\n---", 4)
    if end == -1:
        raise ValueError(f"{path}: unterminated frontmatter")
    data = yaml.safe_load(text[4:end])
    if not isinstance(data, dict):
        raise ValueError(f"{path}: frontmatter is not a mapping")
    return data


def load_brief_frontmatter_text(brief_text: str, source_name: str = "<brief>") -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML required")
    if not brief_text.startswith("---\n"):
        raise ValueError(f"{source_name}: missing YAML frontmatter")
    end = brief_text.find("\n---\n", 4)
    if end == -1:
        end = brief_text.find("\n---", 4)
    if end == -1:
        raise ValueError(f"{source_name}: unterminated frontmatter")
    data = yaml.safe_load(brief_text[4:end])
    if not isinstance(data, dict):
        raise ValueError(f"{source_name}: frontmatter is not a mapping")
    return data


def is_placeholder_text(value: Any) -> bool:
    return bool(_PLACEHOLDER_RE.search(str(value or "").strip()))


def criterion_command_and_text(criterion: Any) -> tuple[str, str]:
    """Normalize one acceptance criterion to display text and shell command.

    A mapping with an explicit `command` is the only structured form that
    contributes a shell command. A mapping with only `criterion` is narrative
    text and should not be executed as shell.
    """
    if isinstance(criterion, str):
        value = criterion.strip()
        return value, value
    if isinstance(criterion, dict):
        command = str(criterion.get("command") or "").strip()
        text = str(
            criterion.get("description")
            or criterion.get("criterion")
            or command
        ).strip()
        return text, command
    raise ValueError(f"unrecognized criterion shape: {criterion!r}")


def is_shell_runnable_command(command: str) -> bool:
    command = str(command or "").strip()
    if not command or is_placeholder_text(command):
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return False

    idx = 0
    while idx < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return False

    executable = tokens[idx]
    if executable in _SHELL_BUILTINS:
        return True
    if executable.startswith(("./", "../", "/")):
        return True
    return shutil.which(executable) is not None


def criterion_is_shell_runnable(criterion: Any) -> bool:
    _, command = criterion_command_and_text(criterion)
    return is_shell_runnable_command(command)


def acceptance_has_any_route_item(acceptance: dict[str, Any]) -> bool:
    for route in ("mechanical", "reviewer", "operator"):
        items = acceptance.get(route) or []
        if isinstance(items, list) and items:
            return True
    return False


def brief_acceptance_ready(brief: dict[str, Any]) -> tuple[bool, str]:
    acceptance = brief.get("acceptance_criteria")
    if not isinstance(acceptance, dict):
        return False, "brief.acceptance_criteria missing"

    if not acceptance_has_any_route_item(acceptance):
        return False, "brief.acceptance_criteria has no route items"

    for route in ("mechanical", "reviewer", "operator"):
        items = acceptance.get(route) or []
        if not isinstance(items, list):
            return False, f"brief.acceptance_criteria.{route} must be a list"
        for item in items:
            try:
                text, command = criterion_command_and_text(item)
            except ValueError as exc:
                return False, str(exc)
            if is_placeholder_text(text) or is_placeholder_text(command):
                return False, f"brief.acceptance_criteria.{route} contains placeholder text"

    mechanical = acceptance.get("mechanical") or []
    if mechanical and not any(criterion_is_shell_runnable(item) for item in mechanical):
        return False, "brief.acceptance_criteria.mechanical has no shell-runnable command"

    return True, "ok"
