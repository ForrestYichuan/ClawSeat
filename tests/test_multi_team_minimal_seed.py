from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_seed_multi_team_minimal_detects_cartooner(tmp_path: Path) -> None:
    proposals = tmp_path / "_config-proposals"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "core" / "scripts" / "seed_multi_team_minimal.py"),
            "--project",
            "cartooner",
            "--repo-root",
            "/repo/cartooner",
            "--output-dir",
            str(proposals),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    front = proposals / "cartooner-front__approved.yaml"
    quality = proposals / "quality-docs__approved.yaml"
    assert front.exists()
    assert quality.exists()
    assert "planner_mode: delivery" in front.read_text(encoding="utf-8")
    assert "notify_policy: queue_drained_only" in front.read_text(encoding="utf-8")
    quality_text = quality.read_text(encoding="utf-8")
    assert "planner_mode: quality_campaign" in quality_text
    assert "notify_policy: never_notify_memory" in quality_text
    assert "quality_gate_doc: quality-docs/QUALITY.md" in quality_text
    assert "instance: human" in quality_text
    assert "instance: fast" not in quality_text
    assert "instance: chaos" not in quality_text


def test_install_multi_seed_template_writes_v3_profile(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = subprocess.run(
        [
            "bash",
            str(REPO / "scripts" / "install_multi.sh"),
            "--project",
            "mini",
            "--seed-template",
            "multi-team-minimal",
            "--seed-archetype",
            "generic",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "CLAWSEAT_REAL_HOME": str(home), "PYTHON_BIN": sys.executable},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    profile = home / ".agents" / "profiles" / "mini-profile-dynamic.toml"
    data = tomllib.loads(profile.read_text(encoding="utf-8"))
    assert data["mode"]["team_structure"] == "multi"
    assert data["template_name"] == "clawseat-minimal"
    assert data["teams"]["core"]["planner_mode"] == "delivery"
    assert data["teams"]["quality-docs"]["notify_policy"] == "never_notify_memory"
    assert data["teams"]["quality-docs"]["quality_gate_doc"] == "quality-docs/QUALITY.md"
    assert (home / ".agents" / "tasks" / "mini" / "TEAM_OWNERSHIP.md").exists()
    project_record = home / ".agents" / "projects" / "mini" / "project.toml"
    project_data = tomllib.loads(project_record.read_text(encoding="utf-8"))
    assert project_data["template_name"] == "clawseat-minimal"
    assert project_data["engineers"] == data["seats"]
    assert "builder" not in project_data["engineers"]
    assert "quality-docs-patrol-human" in project_data["engineers"]
    assert "quality-docs-patrol-fast" not in project_data["engineers"]
    assert "quality-docs-patrol-chaos" not in project_data["engineers"]
    assert project_data["seat_overrides"]["core-builder-core"]["tool"] == "codex"


def test_seed_multi_team_minimal_adds_quality_docs_to_custom_teams(tmp_path: Path) -> None:
    proposals = tmp_path / "_config-proposals"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "core" / "scripts" / "seed_multi_team_minimal.py"),
            "--project",
            "custom",
            "--repo-root",
            "/repo/custom",
            "--output-dir",
            str(proposals),
            "--teams",
            "api,ui",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (proposals / "api__approved.yaml").exists()
    assert (proposals / "ui__approved.yaml").exists()
    assert (proposals / "quality-docs__approved.yaml").exists()


def test_clawseat_solo_alias_dry_run_does_not_persist_seed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo_root = tmp_path / "generic-app"
    repo_root.mkdir()
    result = subprocess.run(
        [
            "bash",
            str(REPO / "scripts" / "install.sh"),
            "--project",
            "solo-alias",
            "--template",
            "clawseat-solo",
            "--repo-root",
            str(repo_root),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "CLAWSEAT_REAL_HOME": str(home), "PYTHON_BIN": sys.executable},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "legacy alias for v3 MULTI_TEAM_MINIMAL" in result.stderr
    assert 'team_structure = "multi"' in result.stdout
    assert "core-builder-core" in result.stdout
    assert not (home / ".agents" / "tasks" / "solo-alias" / "_config-proposals").exists()
