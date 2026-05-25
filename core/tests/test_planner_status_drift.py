"""Tests for planner-status profile/session drift detection (MP022).

When the dynamic profile says tool=gemini but session.toml says tool=claude,
planner_status_snapshot marks profile_drift=True and cmd_planner_status
appends [DRIFT: session=...] to the output line.

Non-drift case: no DRIFT marker.
Missing session file: no drift (absent != wrong).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest.py adds core/scripts to path
import agent_admin_brief as brief_mod
from agent_admin_brief import planner_status_snapshot, cmd_planner_status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROFILE_TMPL = """\
profile_name = "{project}-profile-dynamic"
project_name = "{project}"
seats = ["memory", "{planner}", "t-builder"]

[mode]
team_structure = "multi"
project_memory = "memory"

[teams]
{team} = {{ seats = ["{planner}", "t-builder"], notify_policy = "queue_drained_only" }}

[seat_roles]
memory = "project-memory"
{planner} = "planner"
t-builder = "builder"

[seat_overrides.{planner}]
tool = "{profile_tool}"
auth_mode = "{profile_auth}"
provider = "{profile_provider}"
"""

_SESSION_TMPL = """\
version = 1
project = "{project}"
engineer_id = "{planner}"
tool = "{session_tool}"
auth_mode = "{session_auth}"
provider = "{session_provider}"
session = "{project}-{planner}-{session_tool}"
"""


def _write_profile(home: Path, *, project: str, team: str, planner: str,
                   profile_tool: str, profile_auth: str, profile_provider: str) -> None:
    path = home / ".agents" / "profiles" / f"{project}-profile-dynamic.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PROFILE_TMPL.format(
        project=project, team=team, planner=planner,
        profile_tool=profile_tool, profile_auth=profile_auth, profile_provider=profile_provider,
    ))


def _write_session(home: Path, *, project: str, planner: str,
                   session_tool: str, session_auth: str, session_provider: str) -> None:
    path = home / ".agents" / "sessions" / project / planner / "session.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SESSION_TMPL.format(
        project=project, planner=planner,
        session_tool=session_tool, session_auth=session_auth, session_provider=session_provider,
    ))


# ---------------------------------------------------------------------------
# _read_session_runtime
# ---------------------------------------------------------------------------


class TestReadSessionRuntime:
    def test_returns_none_when_file_missing(self, tmp_path):
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            result = brief_mod._read_session_runtime("proj", "planner")
        assert result is None

    def test_reads_tool_auth_provider(self, tmp_path):
        _write_session(tmp_path, project="proj", planner="ui-planner",
                       session_tool="claude", session_auth="oauth_token", session_provider="anthropic")
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            result = brief_mod._read_session_runtime("proj", "ui-planner")
        assert result is not None
        assert result["tool"] == "claude"
        assert result["auth_mode"] == "oauth_token"
        assert result["provider"] == "anthropic"

    def test_returns_none_on_empty_file(self, tmp_path):
        path = tmp_path / ".agents" / "sessions" / "proj" / "ui-planner" / "session.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            result = brief_mod._read_session_runtime("proj", "ui-planner")
        assert result is None


# ---------------------------------------------------------------------------
# planner_status_snapshot drift detection
# ---------------------------------------------------------------------------


class TestPlannerStatusDrift:
    def test_profile_drift_true_when_tool_differs(self, tmp_path):
        """Profile says gemini; session says claude → drift=True."""
        _write_profile(tmp_path, project="p", team="t", planner="ui-planner",
                       profile_tool="gemini", profile_auth="oauth", profile_provider="google")
        _write_session(tmp_path, project="p", planner="ui-planner",
                       session_tool="claude", session_auth="oauth_token", session_provider="anthropic")

        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            rows = planner_status_snapshot("p")

        assert len(rows) == 1
        assert rows[0]["profile_drift"] is True
        assert rows[0]["session_tool"] == "claude"

    def test_profile_drift_false_when_tools_match(self, tmp_path):
        """Profile and session agree → drift=False."""
        _write_profile(tmp_path, project="p", team="t", planner="ui-planner",
                       profile_tool="claude", profile_auth="oauth_token", profile_provider="anthropic")
        _write_session(tmp_path, project="p", planner="ui-planner",
                       session_tool="claude", session_auth="oauth_token", session_provider="anthropic")

        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            rows = planner_status_snapshot("p")

        assert rows[0]["profile_drift"] is False

    def test_profile_drift_false_when_no_session_file(self, tmp_path):
        """Missing session.toml is not drift (seat may not be started yet)."""
        _write_profile(tmp_path, project="p", team="t", planner="ui-planner",
                       profile_tool="gemini", profile_auth="oauth", profile_provider="google")
        # No session file written

        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            rows = planner_status_snapshot("p")

        assert rows[0]["profile_drift"] is False
        assert rows[0]["session_tool"] is None

    def test_drift_row_contains_session_fields(self, tmp_path):
        """Drifted row must expose session_tool, session_auth_mode, session_provider."""
        _write_profile(tmp_path, project="p", team="t", planner="ui-planner",
                       profile_tool="gemini", profile_auth="oauth", profile_provider="google")
        _write_session(tmp_path, project="p", planner="ui-planner",
                       session_tool="claude", session_auth="api", session_provider="baidu-glm")

        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            rows = planner_status_snapshot("p")

        row = rows[0]
        assert row["session_tool"] == "claude"
        assert row["session_auth_mode"] == "api"
        assert row["session_provider"] == "baidu-glm"

    def test_drift_on_provider_only_difference(self, tmp_path):
        """Drift fires when only provider differs (tool+auth match)."""
        _write_profile(tmp_path, project="p", team="t", planner="ui-planner",
                       profile_tool="claude", profile_auth="api", profile_provider="minimax")
        _write_session(tmp_path, project="p", planner="ui-planner",
                       session_tool="claude", session_auth="api", session_provider="baidu-glm")

        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            rows = planner_status_snapshot("p")

        assert rows[0]["profile_drift"] is True


# ---------------------------------------------------------------------------
# cmd_planner_status text output: DRIFT marker
# ---------------------------------------------------------------------------


class TestCmdPlannerStatusDriftOutput:
    def test_drift_marker_in_text_output(self, tmp_path, capsys):
        _write_profile(tmp_path, project="p", team="t", planner="ui-planner",
                       profile_tool="gemini", profile_auth="oauth", profile_provider="google")
        _write_session(tmp_path, project="p", planner="ui-planner",
                       session_tool="claude", session_auth="oauth_token", session_provider="anthropic")

        args = argparse.Namespace(project="p", json=False)
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            cmd_planner_status(args)

        out = capsys.readouterr().out
        assert "[DRIFT:" in out, f"DRIFT marker missing from output:\n{out}"
        assert "claude" in out

    def test_no_drift_marker_when_matching(self, tmp_path, capsys):
        _write_profile(tmp_path, project="p", team="t", planner="ui-planner",
                       profile_tool="claude", profile_auth="oauth_token", profile_provider="anthropic")
        _write_session(tmp_path, project="p", planner="ui-planner",
                       session_tool="claude", session_auth="oauth_token", session_provider="anthropic")

        args = argparse.Namespace(project="p", json=False)
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            cmd_planner_status(args)

        out = capsys.readouterr().out
        assert "[DRIFT:" not in out

    def test_no_drift_marker_when_no_session(self, tmp_path, capsys):
        _write_profile(tmp_path, project="p", team="t", planner="ui-planner",
                       profile_tool="gemini", profile_auth="oauth", profile_provider="google")

        args = argparse.Namespace(project="p", json=False)
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            cmd_planner_status(args)

        out = capsys.readouterr().out
        assert "[DRIFT:" not in out

    def test_json_output_includes_profile_drift_field(self, tmp_path, capsys):
        import json
        _write_profile(tmp_path, project="p", team="t", planner="ui-planner",
                       profile_tool="gemini", profile_auth="oauth", profile_provider="google")
        _write_session(tmp_path, project="p", planner="ui-planner",
                       session_tool="claude", session_auth="oauth_token", session_provider="anthropic")

        args = argparse.Namespace(project="p", json=True)
        with patch.object(brief_mod, "real_user_home", return_value=tmp_path):
            cmd_planner_status(args)

        rows = json.loads(capsys.readouterr().out)
        assert rows[0]["profile_drift"] is True
        assert rows[0]["session_tool"] == "claude"
