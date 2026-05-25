"""Smoke tests for scripts/clawseat-update-projects.sh.

Drives the script with a synthetic CLAWSEAT_REAL_HOME pointing at a tmp_path
containing fake project/workspace dirs. Tests only the dry-run path — --apply
would invoke real agent_admin and is out of scope for unit-level tests.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "clawseat-update-projects.sh"


def _run(env_home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={**os.environ, "CLAWSEAT_REAL_HOME": str(env_home)},
        capture_output=True, text=True,
    )


def _seed_project(env_home: Path, project: str, rendered_sha: str | None) -> None:
    """Create a minimal project + workspace skeleton with optional rendered SHA."""
    (env_home / ".agents" / "projects" / project).mkdir(parents=True, exist_ok=True)
    ws = env_home / ".agents" / "workspaces" / project / "memory"
    ws.mkdir(parents=True, exist_ok=True)
    if rendered_sha is not None:
        (ws / "CLAUDE.md").write_text(
            f"<!-- rendered_from_clawseat_sha={rendered_sha} -->\n# fake workspace\n",
            encoding="utf-8",
        )
    else:
        (ws / "CLAUDE.md").write_text("# fake workspace without marker\n", encoding="utf-8")


def _write_project_toml(env_home: Path, project: str, engineers: list[str]) -> None:
    project_dir = env_home / ".agents" / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    seats = ", ".join(f'"{seat}"' for seat in engineers)
    (project_dir / "project.toml").write_text(
        f'version = 1\nname = "{project}"\nengineers = [{seats}]\n',
        encoding="utf-8",
    )


def test_help_works():
    proc = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "Bulk-regenerate ClawSeat project workspaces" in proc.stdout


def test_dry_run_reports_stale_project(tmp_path):
    """Project with old SHA shows as 'stale' in dry-run output."""
    _seed_project(tmp_path, "fake-stale", rendered_sha="abc123def456" + "0" * 28)
    r = _run(tmp_path, "--project", "fake-stale")
    assert r.returncode == 0, r.stderr
    assert "stale" in r.stdout
    assert "fake-stale" in r.stdout


def test_dry_run_reports_fresh_project(tmp_path):
    """Project with SHA matching current ClawSeat HEAD shows as 'fresh'."""
    head_sha = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _seed_project(tmp_path, "fake-fresh", rendered_sha=head_sha)
    r = _run(tmp_path, "--project", "fake-fresh")
    assert r.returncode == 0, r.stderr
    assert "fresh" in r.stdout
    assert "fake-fresh" in r.stdout


def test_dry_run_ignores_workspace_backup_markers(tmp_path):
    """Workspace .backup-* folders should not make a freshly rendered project stale."""
    head_sha = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _seed_project(tmp_path, "fake-with-backup", rendered_sha=head_sha)
    backup = tmp_path / ".agents" / "workspaces" / "fake-with-backup" / ".backup-20260513T204203Z"
    backup.mkdir(parents=True, exist_ok=True)
    (backup / "CLAUDE.md").write_text(
        "<!-- rendered_from_clawseat_sha=abc123def4560000000000000000000000000000 -->\n",
        encoding="utf-8",
    )

    r = _run(tmp_path, "--project", "fake-with-backup")

    assert r.returncode == 0, r.stderr
    assert "fresh" in r.stdout
    assert "fake-with-backup" in r.stdout


def test_dry_run_ignores_retired_workspace_markers_when_project_toml_lists_active_seats(tmp_path):
    """Stale retired seat dirs must not keep a project stale after active seats refresh."""
    head_sha = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    project = "fake-with-retired"
    _write_project_toml(tmp_path, project, ["memory"])
    active = tmp_path / ".agents" / "workspaces" / project / "memory"
    active.mkdir(parents=True, exist_ok=True)
    (active / "CLAUDE.md").write_text(
        f"<!-- rendered_from_clawseat_sha={head_sha} -->\n# active\n",
        encoding="utf-8",
    )
    retired = tmp_path / ".agents" / "workspaces" / project / "retired-planner"
    retired.mkdir(parents=True, exist_ok=True)
    (retired / "AGENTS.md").write_text(
        "<!-- rendered_from_clawseat_sha=abc123def4560000000000000000000000000000 -->\n# retired\n",
        encoding="utf-8",
    )

    r = _run(tmp_path, "--project", project)

    assert r.returncode == 0, r.stderr
    assert "fresh" in r.stdout
    assert project in r.stdout


def test_dry_run_reports_unknown_when_marker_missing(tmp_path):
    """Workspace without rendered_from_clawseat_sha marker reports as 'unknown'."""
    _seed_project(tmp_path, "fake-nomarker", rendered_sha=None)
    r = _run(tmp_path, "--project", "fake-nomarker")
    assert r.returncode == 0, r.stderr
    assert "unknown" in r.stdout


def test_frozen_project_is_skipped(tmp_path):
    """Default frozen list (install / dp-e2e-* / test-fix / etc.) skips."""
    old = "abc123def456" + "0" * 28
    _seed_project(tmp_path, "install", rendered_sha=old)
    _seed_project(tmp_path, "dp-e2e-2026", rendered_sha=old)
    _seed_project(tmp_path, "test-fix", rendered_sha=old)
    _seed_project(tmp_path, "testbed-foo", rendered_sha=old)
    _seed_project(tmp_path, "demo-m-verify", rendered_sha=old)
    r = _run(tmp_path)
    assert r.returncode == 0
    for frozen in ("install", "dp-e2e-2026", "test-fix", "testbed-foo", "demo-m-verify"):
        assert frozen in r.stdout
        # Each frozen project must appear with "frozen" annotation
        for line in r.stdout.splitlines():
            if frozen in line:
                assert "frozen" in line, f"frozen project {frozen!r} not labeled: {line}"
                break


def test_include_frozen_overrides_skip(tmp_path):
    """--include-frozen treats frozen projects as regular candidates."""
    _seed_project(tmp_path, "install", rendered_sha="abc123def456" + "0" * 28)
    r = _run(tmp_path, "--include-frozen")
    assert r.returncode == 0
    # Should now show install as stale (not skipped as frozen)
    install_line = next((l for l in r.stdout.splitlines() if "install" in l), "")
    assert "stale" in install_line, f"unexpected: {install_line!r}"


def test_also_frozen_extends_skip_list(tmp_path):
    """--also-frozen <prefix-csv> adds extra frozen prefixes."""
    _seed_project(tmp_path, "cartooner-front", rendered_sha="abc123def456" + "0" * 28)
    r = _run(tmp_path, "--also-frozen", "cartooner-")
    assert r.returncode == 0
    cart_line = next((l for l in r.stdout.splitlines() if "cartooner-front" in l), "")
    assert "frozen" in cart_line


def test_summary_line_present(tmp_path):
    _seed_project(tmp_path, "fake-one", rendered_sha="abc123def456" + "0" * 28)
    r = _run(tmp_path)
    assert r.returncode == 0
    assert "Summary:" in r.stdout
    assert "fresh=" in r.stdout
    assert "stale=" in r.stdout
    assert "skipped=" in r.stdout


def test_no_projects_dir_fails_gracefully(tmp_path):
    r = _run(tmp_path)
    # No .agents/projects under tmp_path → exits 1
    assert r.returncode == 1
    assert "no projects dir" in r.stderr
