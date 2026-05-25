"""Persist per-seat harness choices to ~/.agents/engineers/<seat>/last-harness.toml.

The file is a "last choice snapshot" independent of engineer.toml — it records
the most recently confirmed (tool, auth_mode, provider, model) so that future
installs can offer to reuse the same harness instead of prompting from scratch.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_LIB = str(_REPO_ROOT / "core" / "lib")
if _CORE_LIB not in sys.path:
    sys.path.insert(0, _CORE_LIB)
_CORE_SCRIPTS = str(_REPO_ROOT / "core" / "scripts")
if _CORE_SCRIPTS not in sys.path:
    sys.path.insert(0, _CORE_SCRIPTS)
from real_home import real_user_home  # noqa: E402
from _toml_compat import loads_safe as _toml_loads_safe  # noqa: E402

_FILENAME = "last-harness.toml"


def _engineers_root(home: Path | None = None) -> Path:
    return (home or real_user_home()) / ".agents" / "engineers"


def _harness_path(seat_id: str, home: Path | None = None) -> Path:
    return _engineers_root(home) / seat_id / _FILENAME


def load_last_harness(seat_id: str, home: Path | None = None) -> dict[str, str] | None:
    """Return the last saved harness dict for *seat_id*, or None if not found."""
    path = _harness_path(seat_id, home)
    if not path.exists():
        return None
    try:
        data = _toml_loads_safe(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    required = {"tool", "auth_mode", "provider"}
    if not required.issubset(data.keys()):
        return None
    result = {k: str(v) for k, v in data.items() if isinstance(v, str) and v}
    if not required.issubset(result.keys()):
        return None
    return result


def save_last_harness(
    seat_id: str,
    tool: str,
    auth_mode: str,
    provider: str,
    model: str = "",
    home: Path | None = None,
) -> None:
    """Write the harness choice for *seat_id* to last-harness.toml."""
    path = _harness_path(seat_id, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'tool = "{tool}"',
        f'auth_mode = "{auth_mode}"',
        f'provider = "{provider}"',
    ]
    if model:
        lines.append(f'model = "{model}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def reset_all_harness_memory(home: Path | None = None) -> list[str]:
    """Delete all last-harness.toml files under ~/.agents/engineers/.

    Returns the list of seat_ids whose files were removed.
    """
    root = _engineers_root(home)
    removed: list[str] = []
    if not root.exists():
        return removed
    for seat_dir in root.iterdir():
        if not seat_dir.is_dir():
            continue
        harness_file = seat_dir / _FILENAME
        if harness_file.exists():
            harness_file.unlink()
            removed.append(seat_dir.name)
    return removed
