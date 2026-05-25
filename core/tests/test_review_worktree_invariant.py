"""Tests for project-local review/latest worktree invariant (MP024).

Each ClawSeat project owns exactly one scoped review/latest worktree path.
The path must contain the project name and must not be a shared global path.

Role contract (tested as labels, not code gates):
- Planner: delivers branch/commit/test evidence and does not merge review/latest
- Memory: integrates accepted deliveries into review_worktree;
          merges review/latest → main after operator confirmation;
          keeps launcher_worktree synced; maintains desktop launch script
- Builder: does NOT merge review/latest or main

Diagnostic: check_review_latest_worktree() reports path/status/hash or flags
missing/stale without creating or mutating real worktrees.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# conftest.py adds core/scripts to path
import agent_admin_brief as brief_mod
from agent_admin_brief import (
    check_review_latest_worktree,
    launcher_review_worktree_path,
    review_latest_worktree_path,
)


# ---------------------------------------------------------------------------
# review_latest_worktree_path — per-project scoping
# ---------------------------------------------------------------------------


class TestReviewWorktreePath:
    def test_default_path_contains_project_name(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            path = review_latest_worktree_path("cartooner-front")
        assert "cartooner-front" in str(path)

    def test_different_projects_get_different_paths(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            p1 = review_latest_worktree_path("project-alpha")
            p2 = review_latest_worktree_path("project-beta")
        assert p1 != p2

    def test_default_path_ends_with_review_latest(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            path = review_latest_worktree_path("my-project")
        assert path.name == "review-latest"

    def test_override_from_project_local_toml(self, tmp_path):
        custom = str(tmp_path / "custom-wt")
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            path = review_latest_worktree_path("p", override=custom)
        assert str(path) == custom

    def test_not_a_shared_global_path(self, tmp_path):
        """Path must be under ~/.agents/worktrees/<project>/, not a global tmp."""
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            path = review_latest_worktree_path("my-proj")
        assert ".agents" in str(path)
        assert "worktrees" in str(path)
        assert "my-proj" in str(path)
        # Must NOT be a /tmp path or global path
        assert not str(path).startswith("/private/tmp")
        assert not str(path).startswith("/tmp")


# ---------------------------------------------------------------------------
# launcher_review_worktree_path — per-project scoping
# ---------------------------------------------------------------------------


class TestLauncherReviewWorktreePath:
    def test_default_contains_project_name(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            path = launcher_review_worktree_path("cartooner-front")
        assert "cartooner-front" in str(path)

    def test_different_projects_get_different_launcher_paths(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            p1 = launcher_review_worktree_path("alpha")
            p2 = launcher_review_worktree_path("beta")
        assert p1 != p2

    def test_launcher_path_differs_from_review_worktree_path(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            review = review_latest_worktree_path("my-proj")
            launcher = launcher_review_worktree_path("my-proj")
        assert review != launcher

    def test_override_from_project_local_toml(self, tmp_path):
        custom = str(tmp_path / "custom-launcher-wt")
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            path = launcher_review_worktree_path("p", override=custom)
        assert str(path) == custom


# ---------------------------------------------------------------------------
# _git_worktree_status — status detection without mutating worktrees
# ---------------------------------------------------------------------------


class TestGitWorktreeStatus:
    def test_missing_path_returns_missing_status(self, tmp_path):
        nonexistent = tmp_path / "no-such-wt"
        result = brief_mod._git_worktree_status(nonexistent)
        assert result["status"] == "missing"
        assert result["exists"] is False
        assert result["branch"] is None
        assert result["commit"] is None

    def test_ok_when_branch_is_review_latest(self, tmp_path):
        fake_wt = tmp_path / "wt"
        fake_wt.mkdir()

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            if "--abbrev-ref" in cmd:
                r.returncode = 0
                r.stdout = "review/latest"
            elif "--short" in cmd and "HEAD" in cmd:
                r.returncode = 0
                r.stdout = "abc1234"
            elif "status" in cmd:
                r.returncode = 0
                r.stdout = ""
            else:
                r.returncode = 1
                r.stdout = ""
            return r

        with patch.object(subprocess, "run", side_effect=fake_run):
            result = brief_mod._git_worktree_status(fake_wt)

        assert result["status"] == "ok"
        assert result["branch"] == "review/latest"
        assert result["commit"] == "abc1234"
        assert result["dirty"] is False
        assert result["dirty_count"] == 0

    def test_wrong_branch_flagged(self, tmp_path):
        fake_wt = tmp_path / "wt"
        fake_wt.mkdir()

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            if "--abbrev-ref" in cmd:
                r.returncode = 0
                r.stdout = "main"
            elif "status" in cmd:
                r.returncode = 0
                r.stdout = ""
            else:
                r.returncode = 0
                r.stdout = "000dead"
            return r

        with patch.object(subprocess, "run", side_effect=fake_run):
            result = brief_mod._git_worktree_status(fake_wt)

        assert result["status"] == "wrong-branch"

    def test_detached_and_synced_returns_ok_detached(self, tmp_path):
        fake_wt = tmp_path / "wt"
        fake_wt.mkdir()
        commit = "aabbcc1"

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            if "--abbrev-ref" in cmd:
                r.returncode = 0
                r.stdout = "HEAD"  # detached
            elif "status" in cmd:
                r.returncode = 0
                r.stdout = ""
            elif "--short" in cmd:
                r.returncode = 0
                r.stdout = commit  # both HEAD and review/latest resolve to same hash
            else:
                r.returncode = 0
                r.stdout = commit
            return r

        with patch.object(subprocess, "run", side_effect=fake_run):
            result = brief_mod._git_worktree_status(fake_wt)

        assert result["status"] == "ok-detached"
        assert result["dirty"] is False

    def test_detached_and_stale_returns_stale_detached(self, tmp_path):
        fake_wt = tmp_path / "wt"
        fake_wt.mkdir()

        call_count = [0]

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            call_count[0] += 1
            if "--abbrev-ref" in cmd:
                r.returncode = 0
                r.stdout = "HEAD"
            elif "status" in cmd:
                r.returncode = 0
                r.stdout = ""
            elif "--short" in cmd and call_count[0] <= 2:
                r.returncode = 0
                r.stdout = "old1111"  # HEAD is old
            else:
                r.returncode = 0
                r.stdout = "new2222"  # review/latest is newer
            return r

        with patch.object(subprocess, "run", side_effect=fake_run):
            result = brief_mod._git_worktree_status(fake_wt)

        assert result["status"] == "stale-detached"

    def test_dirty_worktree_is_reported_without_changing_branch_status(self, tmp_path):
        fake_wt = tmp_path / "wt"
        fake_wt.mkdir()

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            if "--abbrev-ref" in cmd:
                r.returncode = 0
                r.stdout = "HEAD"
            elif "status" in cmd:
                r.returncode = 0
                r.stdout = " M tracked.py\n?? scratch.log\n"
            elif "--short" in cmd:
                r.returncode = 0
                r.stdout = "abc1234"
            else:
                r.returncode = 0
                r.stdout = "abc1234"
            return r

        with patch.object(subprocess, "run", side_effect=fake_run):
            result = brief_mod._git_worktree_status(fake_wt)

        assert result["status"] == "ok-detached"
        assert result["dirty"] is True
        assert result["dirty_count"] == 2

    def test_git_timeout_returns_error(self, tmp_path):
        fake_wt = tmp_path / "wt"
        fake_wt.mkdir()

        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=5)):
            result = brief_mod._git_worktree_status(fake_wt)

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# check_review_latest_worktree — project-scoped diagnostic
# ---------------------------------------------------------------------------


class TestCheckReviewLatestWorktree:
    def test_returns_project_key(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            result = check_review_latest_worktree("my-proj")
        assert result["project"] == "my-proj"

    def test_review_worktree_path_is_project_scoped(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            result = check_review_latest_worktree("my-proj")
        assert "my-proj" in result["review_worktree"]["path"]

    def test_launcher_worktree_path_is_project_scoped(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            result = check_review_latest_worktree("my-proj")
        assert "my-proj" in result["launcher_worktree"]["path"]

    def test_review_and_launcher_paths_differ(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            result = check_review_latest_worktree("my-proj")
        assert result["review_worktree"]["path"] != result["launcher_worktree"]["path"]

    def test_project_local_override_respected(self, tmp_path):
        custom_wt = str(tmp_path / "custom-review")
        toml_content = f'review_latest_worktree = "{custom_wt}"\n'
        toml_path = tmp_path / ".agents" / "tasks" / "p" / "project-local.toml"
        toml_path.parent.mkdir(parents=True, exist_ok=True)
        toml_path.write_text(toml_content)

        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            result = check_review_latest_worktree("p")

        assert result["review_worktree"]["path"] == custom_wt
        assert result["review_worktree_configured"] is True

    def test_missing_worktree_status_is_missing(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            result = check_review_latest_worktree("p")
        # Neither worktree exists in tmp_path
        assert result["review_worktree"]["status"] == "missing"
        assert result["launcher_worktree"]["status"] == "missing"

    def test_two_projects_get_different_paths(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            r1 = check_review_latest_worktree("project-a")
            r2 = check_review_latest_worktree("project-b")
        assert r1["review_worktree"]["path"] != r2["review_worktree"]["path"]
        assert r1["launcher_worktree"]["path"] != r2["launcher_worktree"]["path"]
