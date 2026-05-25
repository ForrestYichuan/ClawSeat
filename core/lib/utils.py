"""Small shared helpers used across ClawSeat scripts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import sys as _sys, pathlib as _pl
_utils_scripts = str(_pl.Path(__file__).resolve().parent.parent / "scripts")
if _utils_scripts not in _sys.path: _sys.path.insert(0, _utils_scripts)
from _toml_compat import loads_safe as _toml_loads, load_safe as _toml_load


def now_iso() -> str:
    """Return a UTC ISO-8601 timestamp using the repository's Z suffix convention."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def q(value: object) -> str:
    """Quote a value for TOML/JSON-compatible string embedding."""
    return json.dumps(value, ensure_ascii=False)


def q_array(values: Iterable[str]) -> str:
    """Format a string iterable as a TOML array."""
    return "[" + ", ".join(q(value) for value in values) + "]"


def load_toml(path: Path | str, *, missing_ok: bool = False) -> dict[str, Any] | None:
    """Load TOML from disk."""
    toml_path = Path(path)
    if missing_ok and not toml_path.exists():
        return None
    with toml_path.open("rb") as handle:
        return _toml_load(handle)
