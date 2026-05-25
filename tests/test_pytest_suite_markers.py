from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


REPO = Path(__file__).resolve().parents[1]


def test_pytest_markers_registered() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    markers = "\n".join(pyproject["tool"]["pytest"]["ini_options"]["markers"])

    for marker in ("host", "slow", "legacy", "script"):
        assert f"{marker}:" in markers


def test_fast_runner_uses_layered_marker_expression() -> None:
    script = REPO / "scripts" / "test-fast.sh"
    text = script.read_text(encoding="utf-8")

    assert 'CLAWSEAT_FAST_MARK_EXPR:-not host and not slow' in text
    assert "--durations=" in text


def test_known_host_and_slow_files_are_classified() -> None:
    conftest = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")

    assert '"test_scan_project_smoke.py"' in conftest
    assert '"test_session_stability_window.py"' in conftest
    assert '"test_scan_machine_subset.py"' in conftest
    assert '"test_memory_oracle.py::TestScan"' in conftest
    assert "name.startswith(\"test_install_\")" in conftest


def test_legacy_marker_uses_nodeid_terms() -> None:
    conftest = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")

    assert "lower_nodeid = item.nodeid.lower()" in conftest
    assert "lower_name or part in lower_nodeid" in conftest
