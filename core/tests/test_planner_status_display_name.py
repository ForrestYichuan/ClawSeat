"""Tests for seat display_name in planner-status output (seat-display-name-impl).

When seat_overrides includes display_name, planner_status_snapshot returns it
and cmd_planner_status formats the planner label as "display_name (seat_id)".

provider/auth info now always appears in the tool label for runtime diagnosis;
display_runtime remains exposed in the JSON snapshot for compatibility.
"""

from __future__ import annotations

import argparse
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest.py adds core/scripts to path
import agent_admin_brief as brief_mod
from agent_admin_brief import planner_status_snapshot, cmd_planner_status


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_PROFILE_NO_DN = """\
profile_name = "p-profile-dynamic"
project_name = "p"
seats = ["memory", "{planner}"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
{team} = {{ seats = ["{planner}"], notify_policy = "queue_drained_only" }}

[seat_roles]
memory = "project-memory"
{planner} = "planner"

[seat_overrides.{planner}]
tool = "claude"
auth_mode = "oauth_token"
provider = "anthropic"
"""

_PROFILE_WITH_DN = """\
profile_name = "p-profile-dynamic"
project_name = "p"
seats = ["memory", "{planner}"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
{team} = {{ seats = ["{planner}"], notify_policy = "queue_drained_only" }}

[seat_roles]
memory = "project-memory"
{planner} = "planner"

[seat_overrides.{planner}]
display_name = "{display_name}"
display_runtime = {display_runtime}
tool = "claude"
auth_mode = "oauth_token"
provider = "anthropic"
"""


def _write_profile_no_dn(home: Path, *, project: str = "p", team: str = "t",
                          planner: str = "ui-planner") -> None:
    path = home / ".agents" / "profiles" / f"{project}-profile-dynamic.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PROFILE_NO_DN.format(project=project, team=team, planner=planner))


def _write_profile_with_dn(home: Path, *, project: str = "p", team: str = "t",
                            planner: str = "ui-planner", display_name: str,
                            display_runtime: bool = False) -> None:
    path = home / ".agents" / "profiles" / f"{project}-profile-dynamic.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    dr_str = "true" if display_runtime else "false"
    path.write_text(_PROFILE_WITH_DN.format(
        project=project, team=team, planner=planner,
        display_name=display_name, display_runtime=dr_str,
    ))


# ---------------------------------------------------------------------------
# planner_status_snapshot: display_name propagation
# ---------------------------------------------------------------------------


class TestPlannerStatusDisplayName:
    def test_display_name_empty_when_not_set(self, tmp_path):
        """Without display_name in overrides, snapshot returns empty string."""
        _write_profile_no_dn(tmp_path, planner="ui-planner", team="t")
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            rows = planner_status_snapshot("p")
        assert len(rows) == 1
        assert rows[0]["display_name"] == ""

    def test_display_name_returned_when_set(self, tmp_path):
        """display_name in seat_overrides is returned in snapshot row."""
        _write_profile_with_dn(tmp_path, planner="ui-planner", team="t",
                                display_name="UI-Claude")
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            rows = planner_status_snapshot("p")
        assert len(rows) == 1
        assert rows[0]["display_name"] == "UI-Claude"
        assert rows[0]["planner_seat"] == "ui-planner"

    def test_display_runtime_false_by_default(self, tmp_path):
        """display_runtime defaults to False when not set in overrides."""
        _write_profile_no_dn(tmp_path, planner="ui-planner", team="t")
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            rows = planner_status_snapshot("p")
        assert rows[0]["display_runtime"] is False

    def test_display_runtime_true_when_set(self, tmp_path):
        """display_runtime=true is reflected in snapshot row."""
        _write_profile_with_dn(tmp_path, planner="ui-planner", team="t",
                                display_name="UI-Claude", display_runtime=True)
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            rows = planner_status_snapshot("p")
        assert rows[0]["display_runtime"] is True


# ---------------------------------------------------------------------------
# cmd_planner_status: text output with display_name
# ---------------------------------------------------------------------------


def _run_cmd(tmp_path: Path) -> str:
    args = argparse.Namespace(project="p", json=False)
    captured = StringIO()
    with patch.object(brief_mod, "real_user_home", return_value=tmp_path), \
         patch("sys.stdout", captured), \
         patch.object(brief_mod, "check_review_latest_worktree", side_effect=Exception("skip")):
        cmd_planner_status(args)
    return captured.getvalue()


class TestCmdPlannerStatusDisplayName:
    def test_shows_seat_id_only_when_no_display_name(self, tmp_path):
        """Without display_name, output shows bare seat_id."""
        _write_profile_no_dn(tmp_path, planner="ui-planner", team="t")
        out = _run_cmd(tmp_path)
        assert "planner=ui-planner" in out
        # no parenthesized seat_id with display_name prefix
        assert "(" not in out.split("planner=")[1].split("  ")[0]

    def test_shows_display_name_with_seat_id_when_set(self, tmp_path):
        """With display_name set, output shows 'display_name (seat_id)'."""
        _write_profile_with_dn(tmp_path, planner="ui-planner", team="t",
                                display_name="UI-Claude")
        out = _run_cmd(tmp_path)
        assert "planner=UI-Claude (ui-planner)" in out

    def test_runtime_shown_when_display_runtime_false(self, tmp_path):
        """display_runtime=false: provider/auth still shown in tool label."""
        _write_profile_with_dn(tmp_path, planner="ui-planner", team="t",
                                display_name="UI-Claude", display_runtime=False)
        out = _run_cmd(tmp_path)
        tool_part = [p for p in out.split("  ") if p.startswith("tool=")]
        assert tool_part, f"tool= not found in: {out!r}"
        assert "anthropic" in tool_part[0]
        assert "oauth_token" in tool_part[0]

    def test_runtime_shown_when_display_runtime_true(self, tmp_path):
        """display_runtime=true: provider/auth appear in tool label."""
        _write_profile_with_dn(tmp_path, planner="ui-planner", team="t",
                                display_name="UI-Claude", display_runtime=True)
        out = _run_cmd(tmp_path)
        tool_part = [p for p in out.split("  ") if p.startswith("tool=")]
        assert tool_part, f"tool= not found in: {out!r}"
        assert "anthropic" in tool_part[0]
        assert "oauth_token" in tool_part[0]

    def test_runtime_shown_without_display_name_and_display_runtime_false(self, tmp_path):
        """No display_name + no display_runtime: runtime fragment still present."""
        _write_profile_no_dn(tmp_path, planner="ui-planner", team="t")
        out = _run_cmd(tmp_path)
        tool_part = [p for p in out.split("  ") if p.startswith("tool=")]
        assert tool_part
        assert "anthropic" in tool_part[0]
        assert "oauth_token" in tool_part[0]
