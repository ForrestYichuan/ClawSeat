from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "scripts" / "install.sh"


def test_install_help_lists_builtin_templates_and_provider_deprecation() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL), "--help"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHON_BIN": sys.executable},
        check=False,
    )
    assert result.returncode == 0
    assert "clawseat-engineering|clawseat-creative|clawseat-minimal|clawseat-solo" in result.stdout
    assert "--all-api-provider" in result.stdout
    assert "--provider now controls the memory seat only" in result.stdout


def test_templates_directory_has_builtin_rosters() -> None:
    roster_names = sorted(path.name for path in (REPO / "templates").glob("clawseat-*.toml"))
    assert roster_names == ["clawseat-creative.toml", "clawseat-engineering.toml", "clawseat-solo.toml"]


def test_engineering_option_in_menu() -> None:
    """Selecting option 1 (default) in prompt_kind_first_flow maps to clawseat-engineering."""
    script = f"source {REPO / 'scripts/install/lib/project.sh'}; prompt_template_for_choice 1"
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clawseat-engineering"


def test_creative_option_in_menu() -> None:
    """Selecting option 2 in prompt_kind_first_flow maps to clawseat-creative."""
    script = f"source {REPO / 'scripts/install/lib/project.sh'}; prompt_template_for_choice 2"
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clawseat-creative"


def test_minimal_option_in_menu() -> None:
    """Selecting option 3 in prompt_kind_first_flow maps to clawseat-minimal."""
    script = f"source {REPO / 'scripts/install/lib/project.sh'}; prompt_template_for_choice 3"
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clawseat-minimal"
