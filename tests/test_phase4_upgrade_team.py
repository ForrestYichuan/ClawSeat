"""Phase 4 --upgrade-team incremental flag."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_approved(proposals_dir: Path, team: str, project: str) -> None:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "project": project, "team": team,
        "proposal_status": "approved",
        "operator_approved_ts": "2026-05-14T00:00:00+00:00",
        "seats": [
            {"role": "builder", "tool": "claude", "provider": "anthropic",
             "auth_mode": "oauth_token", "rationale": "ok"},
        ],
        "estimated_monthly_cost_usd": {"low": 1, "high": 2},
    }
    (proposals_dir / f"{team}__approved.yaml").write_text(
        "---\n" + yaml.safe_dump(data, default_flow_style=False, sort_keys=False) + "---\n",
        encoding="utf-8",
    )


def _run_install_multi(env_home: Path, args: list[str]) -> subprocess.CompletedProcess:
    script = REPO_ROOT / "scripts" / "install_multi.sh"
    return subprocess.run(
        ["bash", str(script)] + args,
        env={**os.environ, "CLAWSEAT_REAL_HOME": str(env_home)},
        capture_output=True, text=True,
    )


def test_upgrade_team_adds_seat_to_existing_render(tmp_path):
    project = "p-upg"
    proposals = tmp_path / ".agents" / "tasks" / project / "_config-proposals"
    _write_approved(proposals, "core", project)
    _write_approved(proposals, "content", project)

    # First render: only core
    r1 = _run_install_multi(tmp_path, ["--project", project, "--teams", "core"])
    assert r1.returncode == 0, r1.stderr
    # Sanity: core team workspace was created
    assert (tmp_path / ".agents" / "tasks" / project / "core").is_dir()
    # content NOT yet created (filter excluded it)
    assert not (tmp_path / ".agents" / "tasks" / project / "content").is_dir()

    # Now upgrade-team: add content while keeping core
    r2 = _run_install_multi(tmp_path, ["--project", project, "--upgrade-team", "content"])
    assert r2.returncode == 0, f"upgrade failed: {r2.stderr}\nSTDOUT:\n{r2.stdout}"
    assert "teams=core,content" in r2.stdout or "teams=content,core" in r2.stdout
    # Both team workspaces should now exist
    assert (tmp_path / ".agents" / "tasks" / project / "core").is_dir()
    assert (tmp_path / ".agents" / "tasks" / project / "content").is_dir()


def test_upgrade_team_rejects_missing_approved_yaml(tmp_path):
    project = "p-bad"
    proposals = tmp_path / ".agents" / "tasks" / project / "_config-proposals"
    _write_approved(proposals, "core", project)

    r = _run_install_multi(tmp_path, ["--project", project, "--upgrade-team", "ghost"])
    assert r.returncode != 0
    assert "missing" in r.stderr.lower() or "ghost__approved.yaml" in r.stderr


def test_upgrade_team_mutually_exclusive_with_teams(tmp_path):
    project = "p-ex"
    proposals = tmp_path / ".agents" / "tasks" / project / "_config-proposals"
    _write_approved(proposals, "core", project)

    r = _run_install_multi(
        tmp_path,
        ["--project", project, "--upgrade-team", "core", "--teams", "core"],
    )
    assert r.returncode != 0
    assert "mutually exclusive" in r.stderr
