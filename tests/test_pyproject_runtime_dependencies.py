from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


REPO = Path(__file__).resolve().parents[1]


def test_yaml_runtime_dependency_is_declared() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"].get("dependencies", [])

    assert any(dep.lower().startswith("pyyaml") for dep in dependencies)
