from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]


def test_solo_dry_run_uses_multi_team_minimal(tmp_path: Path) -> None:
    """clawseat-solo is a legacy alias for the v3 multi-team minimal render."""
    home = tmp_path / "home"
    repo_root = tmp_path / "generic-app"
    home.mkdir(parents=True, exist_ok=True)
    repo_root.mkdir()
    result = subprocess.run(
        [
            "bash",
            "scripts/install.sh",
            "--dry-run",
            "--project",
            "test-solo",
            "--template",
            "clawseat-solo",
            "--repo-root",
            str(repo_root),
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
        },
        check=False,
    )
    assert result.returncode == 0, f"dry-run failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    output = result.stdout + result.stderr
    assert "legacy alias for v3 MULTI_TEAM_MINIMAL" in output
    assert 'team_structure = "multi"' in output
    assert "core-planner" in output
    assert "core-builder-core" in output
    assert "quality-docs-planner" in output
    assert "quality-docs-patrol-human" in output
    assert "quality-docs-patrol-fast" not in output
    assert "quality-docs-patrol-chaos" not in output


def test_minimal_dry_run_uses_multi_team_minimal(tmp_path: Path) -> None:
    """clawseat-minimal is the canonical v3 multi-team minimal install template."""
    home = tmp_path / "home"
    repo_root = tmp_path / "generic-app"
    home.mkdir(parents=True, exist_ok=True)
    repo_root.mkdir()
    result = subprocess.run(
        [
            "bash",
            "scripts/install.sh",
            "--dry-run",
            "--project",
            "test-minimal",
            "--template",
            "clawseat-minimal",
            "--repo-root",
            str(repo_root),
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "HOME": str(home),
            "CLAWSEAT_REAL_HOME": str(home),
            "PYTHON_BIN": sys.executable,
        },
        check=False,
    )
    assert result.returncode == 0, f"dry-run failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    output = result.stdout + result.stderr
    assert "clawseat-minimal uses v3 MULTI_TEAM_MINIMAL" in output
    assert 'template_name = "clawseat-minimal"' in output
    assert 'team_structure = "multi"' in output
    assert "core-planner" in output
    assert "core-builder-core" in output
    assert "quality-docs-planner" in output
    assert "quality-docs-patrol-human" in output
    assert "quality-docs-patrol-fast" not in output
    assert "quality-docs-patrol-chaos" not in output
