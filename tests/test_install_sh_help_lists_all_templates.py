from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "scripts" / "install.sh"


def test_install_help_lists_all_templates() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert "Templates (--template)" in output
    assert "clawseat-engineering" in output
    assert "clawseat-creative" in output
    assert "clawseat-minimal" in output
    assert "clawseat-solo" in output
    assert "--no-window skips native iTerm" in output
    assert "5-seat" in output
    assert "MULTI_TEAM_MINIMAL" in output
    assert "cartooner-bound" in output
    assert "patrol" in output
    assert "team-creation" not in output
    assert "cartooner-creative" not in output


def test_clawseat_solo_template_dry_run_is_valid(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = subprocess.run(
        [
            "bash",
            str(INSTALL),
            "--template",
            "clawseat-solo",
            "--dry-run",
            "--project",
            "jj-solo",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "INVALID_TEMPLATE" not in result.stderr
    assert "legacy alias for v3 MULTI_TEAM_MINIMAL" in result.stderr
    assert 'team_structure = "multi"' in result.stdout
    assert "quality-docs" in result.stdout


def test_clawseat_minimal_template_dry_run_is_valid(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = subprocess.run(
        [
            "bash",
            str(INSTALL),
            "--template",
            "clawseat-minimal",
            "--dry-run",
            "--project",
            "jj-minimal",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "INVALID_TEMPLATE" not in result.stderr
    assert "clawseat-minimal uses v3 MULTI_TEAM_MINIMAL" in result.stderr
    assert 'template_name = "clawseat-minimal"' in result.stdout
    assert 'team_structure = "multi"' in result.stdout
    assert "quality-docs" in result.stdout
