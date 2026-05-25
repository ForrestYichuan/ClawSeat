"""Pytest bootstrap for core/tests — adds ClawSeat scripts/lib to sys.path."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

for _p in [
    str(_REPO_ROOT / "core" / "scripts"),
    str(_REPO_ROOT / "core" / "lib"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
